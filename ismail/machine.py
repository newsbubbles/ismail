"""The machine is shared: many agent sessions render, measure, separate and run Blender on one computer. This is
the governor every heavy job goes through, so no session starts one blind (2026-10-02: six sessions stacked heavy
jobs on a laptop GTX 1080 until it sat at 92 C pinned at 139 MHz and the user stopped everything).

    python -m ismail.machine                              # the board: GPU, CPU, memory, every heavy job running
    python -m ismail.machine run --gpu -- <command ...>   # run a command in the GPU slot (Blender, whisper, demucs)
    python -m ismail.machine run --cpu --mem 6 -- <cmd>   # a CPU-heavy command expected to need ~6 GB
    python -m ismail.machine run --cpu --disk 3 -- <cmd>  # ... that writes ~3 GB
    python -m ismail.machine disk                         # where the disk went: songs, _reclaim, the pagefile
    with machine.slot('cpu', 'render song bars 1-64', mem_gb=3): ...     # from Python

Rules (the slots): one GPU-heavy job machine-wide, two CPU-heavy jobs (a live engine on air holds one). No new heavy
job while the GPU is in thermal or hardware slowdown or above GPU_HOT_C: CPU and GPU share one cooler, so a hot GPU
is not a free CPU. No new CPU job while the CPU is CPU_BUSY % busy or more, whoever is using it: most of the load on
this machine is not on the board (the desktop app, servers, other tools), and the refusal names the top processes. A job whose memory estimate does not fit the free commit (minus a reserve) is refused instead of
dying with a MemoryError. Disk and commit hold for everyone: no new heavy job while a drive jobs write to (the
songs folder's, the working directory's) has less than DISK_FLOOR_GB free, or while less than COMMIT_FLOOR_GB of
commit is free (on Windows a job past its memory grows the pagefile, and the pagefile takes the disk: 2026-10-05 a
job that declared 7 GB took 10.7 GB and D: went from 9 GB to 0.2 GB in nine minutes). A running job past its
declared memory by MEM_OVER is flagged on the board and in its own output; one at MEM_SUSPEND times its declared
memory while commit runs low (under SUSPEND_COMMIT_GB) has its processes paused, never killed, until someone runs
`python -m ismail.machine resume <job>` (2026-10-06: an ffmpeg declared 3 GB and took 37 GB during a live set). A
`run` command is held to its threads by its environment and by CPU affinity (the libraries that ignore the thread
variables, CTranslate2 among them, still get only that many cores). A refused job says what is running, whose it is and when to retry; force=True (only when
the user says so) runs it anyway. Jobs of processes that died are cleared on the next look.

Waiting and priority: a job may wait for its slot (`run --wait 30m`, `slot(..., wait=1800)`) instead of being
refused. Waiters line up: the session the user gave priority to first (`python -m ismail.machine priority vox --for
3h --by "the user"`, it expires by itself), then by arrival; a job that does not wait yields to every waiter ahead
of it. Priority orders the line only: the heat limit, the busy CPU and the memory reserve hold for everyone.

Slots and threads: a slot counts jobs, not cores, so it also caps its threads (`threads=`, `run --threads`; BLAS,
OpenMP and torch). `machine run` hands its slot to its command ($ISMAIL_SLOT): a script it runs can render or measure
and those ops run in the run's slot instead of waiting forever for a second one (ledger:M81). While a set is on air the
CPU jobs beside it share on_air_threads() threads (ledger:M132).

The board lives in <songs>/_machine/ (one per machine, shared by every checkout and worktree), or $ISMAIL_MACHINE_DIR.
"""
import argparse
import contextlib
import ctypes
import json
import os
import shutil
import re
import subprocess
import sys
import threading
import time

import psutil

from .handoffs import SONGS

SLOTS = {'gpu': 1, 'cpu': 2}
GPU_HOT_C = 85
# nvidia-smi clocks_throttle_reasons bits that mean the card is slowing itself down (0x1 is idle: fine)
GPU_BAD = {0x8: 'hardware slowdown', 0x20: 'thermal slowdown (driver)', 0x40: 'thermal slowdown (hardware)',
           0x80: 'power brake'}
CPU_BUSY = 80.0                    # % of all cores, averaged over CPU_SAMPLE_S
CPU_SAMPLE_S = 2.0
RESERVE_GB = 4.0                   # commit kept free for the desktop, the sessions and the live engine
NOVICE = ('someone new to computers is never asked to close things (they may not be able to, and it alarms '
          'them): wait quietly and say "the computer is busy, one moment"')
WAIT_POLL_S = 5.0                  # a waiting job looks again this often
THREADS = 2                        # numeric threads per heavy job
DISK_FLOOR_GB = 15.0               # no new heavy job while a drive jobs write to has less free than this
DISK_WARN_GB = 30.0                # the board warns below this
COMMIT_FLOOR_GB = 6.0              # no new heavy job while less commit than this is free (the pagefile would grow)
MEM_OVER = 1.25                    # a running job past its declared memory by this much is flagged
MEM_OVER_MIN_GB = 0.5              # ... and by at least this much (a small render's estimate is not worth a flag)
MEM_SUSPEND = 3.0                  # a job at this many times its declared memory ...
SUSPEND_COMMIT_GB = 10.0           # ... while less commit than this is free is paused (ledger:M154; never killed)
SUSPEND_COMMIT_FRAC = 0.15         # ... or than this share of the commit limit, if smaller (an 8 GB laptop is always
                                   # under 10 GB free, and a job there is not in trouble for that alone)
JOB_NOTE_S = 15.0                  # a running job writes its memory to its board file this often
# What wins when a live set is on air and a render wants the machine (ledger:M127, hq:D-11). Nate, 2026-10-05, after
# two real dropouts in a set: GPU jobs and Blender renders wait while a set plays, "only for now during sets". It is a
# temporary rule: revisit when the laptop is repasted (the GPU heat) or a faster engine lands.
# Policies: 'set_first' (today: the set wins, those renders wait), 'off' (no rule), 'render_first' (the future
# direction, not built: the render runs when the governor judges it a burden and the DJ is asked to pause the set
# gracefully, announcing it). The policy is a board file (on_air.json), set with `machine on-air --policy ...`.
ON_AIR_POLICIES = ('set_first', 'off', 'render_first')
ON_AIR_DEFAULT = 'set_first'
ON_AIR_HELD = re.compile(r'blender|eevee|cycles', re.I)   # CPU jobs that are renders on the GPU in all but name
SLOT_ENV = 'ISMAIL_SLOT'           # the slot `machine run` hands its command: a slot asked for under it runs in it
THREAD_ENV = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS',
              'VECLIB_MAXIMUM_THREADS', 'NUMBA_NUM_THREADS', 'RAYON_NUM_THREADS')
# a command in a slot starts with the slot's thread cap (torch and CTranslate2 read OMP's); `run` also pins it to that
# many cores (ledger:M153: faster-whisper with threads=2 still used about 5 cores)


class MachineBusy(RuntimeError):
    pass


def board_dir():
    return os.environ.get('ISMAIL_MACHINE_DIR') or os.path.join(SONGS, '_machine')


# ------------------------------------------------------------------ readings

def _gpu_query():
    """-> dict or None (no NVIDIA GPU or no nvidia-smi)."""
    q = 'temperature.gpu,clocks.sm,clocks.max.sm,utilization.gpu,clocks_throttle_reasons.active,memory.used,memory.total'
    try:
        out = subprocess.run(['nvidia-smi', f'--query-gpu={q}', '--format=csv,noheader,nounits'], capture_output=True,
                             text=True, timeout=5).stdout.strip().splitlines()[0]
        t, clk, mx, util, reasons, mu, mt = [x.strip() for x in out.split(',')]
        return {'temp': float(t), 'clock': float(clk), 'max_clock': float(mx), 'util': float(util),
                'reasons': int(reasons, 16), 'mem_used_gb': float(mu) / 1024, 'mem_total_gb': float(mt) / 1024}
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return None


_gpu_cache = [0.0, None]


def gpu():
    if time.time() - _gpu_cache[0] > 5:
        _gpu_cache[:] = [time.time(), _gpu_query()]
    return _gpu_cache[1]


def gpu_trouble(g):
    """Why the GPU should take no new heavy job ('' when it can)."""
    if g is None:
        return ''
    why = [name for bit, name in GPU_BAD.items() if g['reasons'] & bit]
    if g['temp'] >= GPU_HOT_C:
        why.append(f"{g['temp']:.0f} C (limit {GPU_HOT_C} C)")
    return ', '.join(why)


_cpu_cache = [0.0, None]


def cpu_load():
    """-> (% of all cores busy over CPU_SAMPLE_S, [(% of all cores, process name, pid)] for the top 4), cached 10 s
    in this process and on the board for every process (each CLI call is a new process: each paid 2 s of sampling)."""
    if time.time() - _cpu_cache[0] < 10 and _cpu_cache[1] is not None:
        return _cpu_cache[1]
    shared = os.path.join(board_dir(), 'cpu.json')
    try:
        with open(shared, encoding='utf8') as f:
            d = json.load(f)
        if time.time() - d['t'] < 10:
            _cpu_cache[:] = [d['t'], (d['busy'], [tuple(x) for x in d['top']])]
            return _cpu_cache[1]
    except (OSError, ValueError, KeyError):
        pass
    procs = []
    for p in psutil.process_iter(['name']):
        try:
            p.cpu_percent(None)
            procs.append(p)
        except psutil.Error:
            pass
    total = psutil.cpu_percent(interval=CPU_SAMPLE_S)
    n = psutil.cpu_count() or 1
    top = []
    for p in procs:
        try:
            c = p.cpu_percent(None) / n
        except psutil.Error:
            continue
        if c >= 1.0 and p.pid and p.info.get('name') not in ('System Idle Process', 'idle'):
            top.append((c, p.info.get('name') or '?', p.pid))
    top.sort(reverse=True)
    _cpu_cache[:] = [time.time(), (total, top[:4])]
    try:
        os.makedirs(board_dir(), exist_ok=True)
        tmp = shared + f".{os.getpid()}.tmp"
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump({'t': _cpu_cache[0], 'busy': total, 'top': top[:4]}, f)
        os.replace(tmp, shared)
    except OSError:
        pass
    return _cpu_cache[1]


def _top_text(top):
    return ', '.join(f"{name} {c:.0f}%" for c, name, _ in top) or 'no single process stands out'


def memory():
    """-> (commit free GB, commit limit GB, RAM free GB). On Windows the commit limit is the wall a job hits."""
    vm = psutil.virtual_memory()
    if sys.platform == 'win32':
        class MS(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                        ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                        ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                        ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
        m = MS()
        m.dwLength = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return m.ullAvailPageFile / 2 ** 30, m.ullTotalPageFile / 2 ** 30, vm.available / 2 ** 30
    sw = psutil.swap_memory()
    return (vm.available + sw.free) / 2 ** 30, (vm.total + sw.total) / 2 ** 30, vm.available / 2 ** 30


def _label(path):
    d = os.path.splitdrive(os.path.abspath(path))[0]
    return d or path


def disks(*paths):
    """-> [(drive, free GB)] for the drives heavy jobs write to: the songs folder's, the working directory's, and
    any path given (a render's project). One entry per filesystem."""
    out, seen = [], set()
    for p in (SONGS, os.getcwd()) + tuple(x for x in paths if x):
        p = os.path.abspath(p)
        while not os.path.exists(p) and os.path.dirname(p) != p:
            p = os.path.dirname(p)
        try:
            dev = os.stat(p).st_dev
            free = shutil.disk_usage(p).free / 2 ** 30
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        out.append((_label(p), free))
    return out


def pagefiles():
    """-> [(drive, GB)] of the Windows pagefiles (each grows into its drive when commit runs out)."""
    out = []
    if sys.platform != 'win32':
        return out
    for part in psutil.disk_partitions(all=False):
        f = os.path.join(part.mountpoint, 'pagefile.sys')
        try:
            out.append((_label(f), os.stat(f).st_size / 2 ** 30))
        except OSError:
            pass
    return out


def _disk_why(disk_gb=0.0, disk_path=None, disk_hint=''):
    low = [(d, f) for d, f in disks(disk_path) if f - disk_gb < DISK_FLOOR_GB]
    if not low:
        return ''
    where = ', '.join(f"{d} has {f:.1f} GB free" for d, f in low)
    need = f"it writes about {disk_gb:.1f} GB and " if disk_gb else ''
    return (f"disk: {need}{where} (heavy jobs wait below {DISK_FLOOR_GB:.0f} GB free): "
            + (disk_hint + '; ' if disk_hint else '')
            + "`python -m ismail.machine disk` shows where the space went; move finished intermediates (caches, old "
              "renders, uncut takes) into the project's _reclaim/ folder (never delete: the user clears _reclaim), "
              "and tell the user")


def _memory_why(js):
    free, limit, _ = memory()
    if free >= COMMIT_FLOOR_GB:
        return ''
    over = [j for j in js if j.get('over')]
    return (f"memory: {free:.1f} GB of commit free (heavy jobs wait below {COMMIT_FLOOR_GB:.0f} GB; past it Windows "
            f"grows the pagefile into the disk)" + (": " + '; '.join(_describe(j) for j in over) if over else
                                                  ": the board shows each job's memory") +
            ": wait; a user who runs other work on this machine can be asked what can close, but " + NOVICE)


# ------------------------------------------------------------------ the job board

def _alive(job):
    try:
        p = psutil.Process(job['pid'])
        return abs(p.create_time() - job.get('pid_start', p.create_time())) < 1.0
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def jobs():
    """Heavy jobs running now (the dead ones are removed from the board)."""
    d = os.path.join(board_dir(), 'jobs')
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith('.json'):
            continue
        p = os.path.join(d, f)
        job = None
        for _ in range(5):          # Windows: a file being replaced by its job's meter cannot be opened for a moment
            try:
                with open(p, encoding='utf8') as fh:
                    job = json.load(fh)
                break
            except FileNotFoundError:
                break
            except (OSError, ValueError):
                time.sleep(0.02)
        if job is None:
            continue
        if _alive(job):
            out.append(job)
        else:
            try:
                os.remove(p)
            except OSError:
                pass
    return out


def priority():
    """-> the priority grant {'who', 'until', 'by', 'why', 'set'} if one is in force, else None."""
    try:
        with open(os.path.join(board_dir(), 'priority.json'), encoding='utf8') as f:
            p = json.load(f)
    except (OSError, ValueError):
        return None
    return p if p.get('until', 0) > time.time() else None


def set_priority(who, for_s, by, why=''):
    """Give `who` (a session's name on the board) first place in the line for for_s seconds. Only the user decides
    this: by names who asked (a session asking for itself is not enough)."""
    if not by or not str(by).strip():
        raise ValueError("by: who gave the priority (the user); a session does not give itself priority")
    p = {'who': who, 'until': time.time() + float(for_s), 'by': str(by).strip(), 'why': why, 'set': time.time()}
    with _board_lock():
        with open(os.path.join(board_dir(), 'priority.json'), 'w', encoding='utf8') as f:
            json.dump(p, f)
    return p


def _until(t):
    """When a grant ends, readable: the time today, or the day and time beyond today (a 3-day grant)."""
    lt = time.localtime(t)
    return time.strftime('%H:%M' if time.strftime('%Y%m%d', lt) == time.strftime('%Y%m%d') else '%a %d %b %H:%M', lt)


def names_seen(hours=48):
    """The board names (who) of jobs running, waiting, or finished in the last `hours`, with a count each."""
    seen = {}
    for j in list(jobs()) + waiters() + history(since=time.time() - hours * 3600):
        seen[j.get('who')] = seen.get(j.get('who'), 0) + 1
    seen.pop(None, None)
    return seen


def priority_match(who=None):
    """'' when a grant's name matches jobs seen in the last 48 h, else a warning naming the names in use (ledger:M143:
    `who` defaults to the working folder's name, so nearly every job from D:/ismail is 'ismail' and a grant to a
    session matched nothing unless that session sets ISMAIL_SESSION)."""
    pr = priority()
    who = who or (pr and pr['who'])
    if not who:
        return ''
    seen = names_seen()
    if who in seen:
        return ''
    names = ', '.join(f"{n} ({c})" for n, c in sorted(seen.items(), key=lambda x: -x[1])[:8]) or 'none'
    return (f"no job named '{who}' ran or waited in the last 48 h, so this priority matches nothing yet. Names in use: "
            f"{names}. A session shows by its own name when it sets ISMAIL_SESSION=<its name> before it runs jobs.")


def clear_priority():
    with _board_lock():
        try:
            os.remove(os.path.join(board_dir(), 'priority.json'))
        except OSError:
            pass


def waiters():
    """Jobs waiting for a slot, alive ones only, in line order."""
    d = os.path.join(board_dir(), 'waiting')
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            try:
                with open(p, encoding='utf8') as fh:
                    w = json.load(fh)
            except (OSError, ValueError):
                continue
            if _alive(w):
                w['_path'] = p
                out.append(w)
            else:
                try:
                    os.remove(p)
                except OSError:
                    pass
    pr = priority()
    return sorted(out, key=lambda w: _rank(w['who'], w['since'], pr))


def _rank(who, since, pr):
    return (0 if pr and who == pr['who'] else 1, since)


def _ahead(kind, who, since, me=None):
    """Waiters for this kind of slot that are ahead of a job (who, since) in the line."""
    pr = priority()
    mine = _rank(who, since, pr)
    return [w for w in waiters() if w.get('id') != me and (w['kind'] == kind or (kind == 'cpu' and w['kind'] == 'live'))
            and _rank(w['who'], w['since'], pr) < mine]


STALE_LOCK_S = 30.0    # a board lock older than this was left by a holder that died mid-update


def _read(path):
    try:
        with open(path, encoding='utf8') as f:
            return f.read()
    except OSError:
        return None


def _break_stale(lock):
    """Take a dead holder's lock away. Renaming it is atomic, so of several waiters that all judged it stale only
    one moves it; a waiter that moved a lock someone took meanwhile (fresh again) puts it back."""
    moved = f"{lock}.stale.{os.getpid()}.{threading.get_ident()}"
    try:
        os.rename(lock, moved)
    except OSError:                                    # gone already, or another waiter moved it
        return
    try:
        if time.time() - os.path.getmtime(moved) < STALE_LOCK_S:
            os.link(moved, lock)                       # never over a lock that exists (FileExistsError)
    except OSError:
        pass
    try:
        os.remove(moved)
    except OSError:
        pass


@contextlib.contextmanager
def _board_lock(timeout=10.0):
    """One writer at a time on the shared board, across every session's processes. A waiter retries while the lock
    changes hands under it (a holder can let go between our failed create and our look at the lock: that race once
    killed a job waiting in line), and a holder removes only its own lock."""
    os.makedirs(os.path.join(board_dir(), 'jobs'), exist_ok=True)
    lock = os.path.join(board_dir(), 'lock')
    token = f"{os.getpid()} {threading.get_ident()} {time.time()!r}"
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, token.encode())
            break
        except (FileExistsError, PermissionError):     # PermissionError: Windows, a lock being deleted
            try:
                age = time.time() - os.path.getmtime(lock)
            except OSError:                            # its holder let go just now: try again
                age = 0.0
            if age > STALE_LOCK_S:
                _break_stale(lock)
            if time.time() - t0 > timeout:
                raise MachineBusy(f"the job board {lock} stayed locked for {timeout:.0f} s; try again")
            time.sleep(0.02 + 0.03 * (threading.get_ident() % 7) / 7)
    try:
        yield
    finally:
        os.close(fd)
        if _read(lock) == token:                       # never remove a lock another holder took
            try:
                os.remove(lock)
            except OSError:
                pass


def _ago(t):
    m = (time.time() - t) / 60
    return f"{m:.0f} min" if m >= 1 else f"{m * 60:.0f} s"


def _describe(job):
    eta = ''
    if job.get('est_s'):
        left = job['started'] + job['est_s'] - time.time()
        eta = f", expected done in {left / 60:.0f} min" if left > 0 else f", {-left / 60:.0f} min past its estimate"
    what = str(job['what']).strip().splitlines() or ['']
    what = what[0] + (' ...' if len(what) > 1 else '')          # a `python -c` job shows its first line
    thr = f", {job['threads']} threads" if job['kind'] != 'live' and job.get('threads') else ''
    mem = ''
    if job.get('mem_now_gb') is not None:
        mem = f", using {job['mem_now_gb']:.1f} GB" + (f" of {job['mem_gb']:g} declared" if job.get('mem_gb') else '')
    if job.get('over'):
        mem += f", OVER: peaked at {job['over']:.1f} GB"
    if job.get('suspended'):
        mem += (f", SUSPENDED: OVER since {_ago(job['suspended']['at'])} (paused, not killed; "
                f"python -m ismail.machine resume {job['id']})")
    if job.get('cores'):
        thr += f" on cores {','.join(map(str, job['cores']))}"
    return f"{job['kind']} '{what}' ({job['who']}, pid {job['pid']}, {_ago(job['started'])}{thr}{eta}{mem})"


def duration_s(text):
    """'10m', '600s', '1.5h' -> seconds. A bare number is minutes; above 240 it is refused, because a number of
    seconds passed as minutes put a 10-minute render on the board as 585 min and a peer thought a job had hung."""
    t = str(text).strip().lower()
    unit = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}.get(t[-1:]) if t[-1:].isalpha() else None
    try:
        v = float(t[:-1] if unit else t)
    except ValueError:
        raise ValueError(f"duration {text!r}: give one like 10m, 600s, 1.5h or 3d")
    if unit is None and v > 240:
        raise ValueError(f"--est {text}: a bare number is minutes ({v / 60:.1f} h). If you meant seconds, "
                         f"write --est {t}s; if you meant minutes, write --est {t}m")
    return v * (unit or 60)


def on_air_policy():
    """-> {policy, by, why, at}: what wins when a live set is on air (ledger:M127). Default 'set_first'."""
    try:
        with open(os.path.join(board_dir(), 'on_air.json'), encoding='utf8') as f:
            got = json.load(f)
        if got.get('policy') in ON_AIR_POLICIES:
            return got
    except (OSError, ValueError):
        pass
    return {'policy': ON_AIR_DEFAULT, 'by': 'the default (hq:D-11)', 'why': 'renders wait while a set is on air'}


def set_on_air_policy(policy, by, why=''):
    if policy not in ON_AIR_POLICIES:
        raise ValueError(f"policy: one of {', '.join(ON_AIR_POLICIES)}")
    if policy == 'render_first':
        raise ValueError("render_first is the future direction in hq:D-11 (the render runs when it is a burden and the "
                         "DJ pauses the set, announcing it); it is not built yet: use set_first or off")
    if not by:
        raise ValueError("by: who decided (the user)")
    rec = {'policy': policy, 'by': by, 'why': why, 'at': time.time()}
    os.makedirs(board_dir(), exist_ok=True)
    with open(os.path.join(board_dir(), 'on_air.json'), 'w', encoding='utf8') as f:
        json.dump(rec, f)
    return rec


def _on_air_why(kind, what, cmd, js):
    """While a live set is on air under 'set_first', a GPU job or a Blender render waits until the set ends."""
    live = [j for j in js if j['kind'] == 'live']
    if not live or on_air_policy()['policy'] != 'set_first':
        return ''
    if kind != 'gpu' and not ON_AIR_HELD.search(' '.join([what or ''] + [os.path.basename(str(c)) for c in cmd or []])):
        return ''
    return (f"a live set is on air ({'; '.join(_describe(j) for j in live)}): GPU jobs and Blender renders wait until it "
            f"ends (Nate's temporary rule, hq:D-11 ledger:M127; it held two dropouts' worth of renders). Wait in line "
            f"(`run --wait`); only the user lifts it (`python -m ismail.machine on-air --policy off --by ...`)")


def on_air_threads():
    """While a set is on air, the CPU jobs beside it share this many threads: half the machine's. ledger:M132: one
    slot ran a render, eq_match and the perceptual model (torch on every core), the CPU went to 88-100 % and the set
    dropped 359 buffers."""
    return max(2, (psutil.cpu_count() or 4) // 2)


def _on_air_threads_why(kind, threads, js):
    """While a set is on air under 'set_first', a CPU job waits when its threads and those of the CPU jobs already
    beside the set would pass on_air_threads(). A job with no thread cap counts as every core."""
    if kind != 'cpu' or not any(j['kind'] == 'live' for j in js) or on_air_policy()['policy'] != 'set_first':
        return ''
    n, budget = psutil.cpu_count() or 8, on_air_threads()
    cpu = [j for j in js if j['kind'] == 'cpu']
    used = sum(j.get('threads', THREADS) or n for j in cpu)     # a board file from before this rule: the default
    want = threads or n
    if used + want <= budget:
        return ''
    room = budget - used
    return (f"a live set is on air and the CPU jobs beside it share {budget} threads ({used} in use"
            + (': ' + '; '.join(_describe(j) for j in cpu) if cpu else '') + "); this job asks for "
            + (f"{want}" if threads else f"all {n} (no thread cap)") + ": "
            + (f"ask for {room} or fewer (`run --threads {room}`, `slot(..., threads={room})`) or wait in line"
               if room > 0 else "wait in line (`run --wait`)")
            + " (ledger:M132: a slot counts jobs, not cores)")


def check(kind, mem_gb=0.0, _jobs=None, who=None, since=None, me=None, disk_gb=0.0, disk_path=None, disk_hint='',
          what=None, cmd=None, threads=THREADS):
    """-> '' when a `kind` job ('gpu' or 'cpu') of mem_gb may start now, else why not and what to do. who/since:
    the asking job's place in the line (a job that is not waiting stands at the back of it, now). disk_gb: what it
    writes, on disk_path's drive (and the songs folder's and the working directory's). what/cmd: the job, so a
    Blender render registered as cpu still waits while a set is on air. threads: its thread cap (None: every core),
    held to the on-air budget while a set plays."""
    js = jobs() if _jobs is None else _jobs
    why = [w for w in (_disk_why(disk_gb, disk_path, disk_hint), _memory_why(js), _on_air_why(kind, what, cmd, js),
                       _on_air_threads_why(kind, threads, js)) if w]
    hot = gpu_trouble(gpu())
    if hot:
        why.append(f"the GPU is {hot}: the machine is hot (CPU and GPU share one cooler), no new heavy job until it cools")
    if kind == 'cpu':
        busy, top = cpu_load()
        if busy >= CPU_BUSY:
            why.append(f"the CPU is {busy:.0f}% busy (limit {CPU_BUSY:.0f}%), mostly load that is not on the board "
                       f"({_top_text(top)}): wait for it to settle; a user who runs other work on this machine can be "
                       f"asked whether something can close, but " + NOVICE)
    same = [j for j in js if j['kind'] == kind or (kind == 'cpu' and j['kind'] == 'live')]
    if len(same) >= SLOTS[kind]:
        why.append(f"the {kind} slots are full ({SLOTS[kind]}): " + '; '.join(_describe(j) for j in same))
    else:
        ahead = _ahead(kind, who or _who(), time.time() if since is None else since, me)
        free = SLOTS[kind] - len(same)
        if len(ahead) >= free:
            pr = priority()
            why.append(f"{len(ahead)} waiting ahead for the {kind} slot: " + '; '.join(
                f"'{w['what']}' ({w['who']}" + (', priority from ' + pr['by'] if pr and w['who'] == pr['who'] else '')
                + ")" for w in ahead) + ": wait in line (`run --wait`), or do lighter work")
    if mem_gb:
        free, limit, _ = memory()
        if mem_gb > free - RESERVE_GB:
            why.append(f"it needs about {mem_gb:.1f} GB and {free:.1f} GB of commit is free (limit {limit:.0f} GB, "
                       f"{RESERVE_GB:.0f} GB kept in reserve): render a shorter window or fewer tracks")
    return '; '.join(why)


_held = threading.local()


def _limit_threads(n):
    """Cap this process's numeric threads at n: the BLAS and OpenMP pools, and torch when it is loaded. -> what
    undoes it."""
    undo = []
    if not n:
        return undo
    try:
        from threadpoolctl import threadpool_limits
        undo.append(threadpool_limits(n).unregister)
    except ImportError:
        pass
    torch = sys.modules.get('torch')
    if torch is not None:
        prev = torch.get_num_threads()
        torch.set_num_threads(n)
        undo.append(lambda: torch.set_num_threads(prev))
    return undo


def _undo(undo):
    for f in reversed(undo):
        try:
            f()
        except Exception:
            pass


def cap_torch():
    """Where ismail loads torch inside a slot (the perceptual model, demucs): torch takes the slot's threads, not
    every core, because a slot counts jobs, not cores (ledger:M132). Outside a slot it does nothing."""
    n = getattr(_held, 'threads', None)
    torch = sys.modules.get('torch')
    if n and torch is not None and torch.get_num_threads() != n:
        torch.set_num_threads(n)


def _handed_down(kind):
    """The slot that `machine run` holds for an ancestor of this process (its id in $ISMAIL_SLOT), when this request
    may run in it: the same kind, or CPU work under a GPU job. A live engine always takes its own slot. A token whose
    job has ended, or that belongs to another board or to no ancestor, is ignored."""
    token = os.environ.get(SLOT_ENV)
    if not token or kind == 'live' or not re.fullmatch(r'[\w.-]+', token):
        return None
    try:
        job = json.loads(_read(os.path.join(board_dir(), 'jobs', token + '.json')) or 'null')
    except ValueError:
        return None
    if not job or not (job['kind'] == kind or (job['kind'] == 'gpu' and kind == 'cpu')):
        return None
    try:
        ancestors = {p.pid for p in psutil.Process().parents()}
    except psutil.Error:
        return None
    return job if job.get('pid') in ancestors and _alive(job) else None


def pick_cores(n):
    """n logical cores for a `run` command (ledger:M153), or None when n covers the machine: the highest-numbered
    cores no other job on the board is pinned to (the live engine and the desktop keep the low ones), then the
    least shared."""
    total = psutil.cpu_count() or 1
    if not n or n >= total:
        return None
    taken = {}
    for j in jobs():
        for c in j.get('cores') or []:
            taken[c] = taken.get(c, 0) + 1
    order = sorted(range(total), key=lambda c: (taken.get(c, 0), -c))
    return sorted(order[:n])


def resume(name):
    """Go on with a job paused for its memory: the job id, its pid, or a word of its name. -> what was done."""
    js = [j for j in jobs() if j.get('suspended')]
    hit = [j for j in js if name in (j['id'], str(j['pid']))] or [j for j in js if name.lower() in str(j['what']).lower()]
    if len(hit) != 1:
        head = f"no paused job matches {name!r}" if not hit else f"{len(hit)} paused jobs match {name!r}: use the id"
        return head + (": paused now: " + '; '.join(f"{j['id']} '{str(j['what'])[:40]}'" for j in js) if js
                       else ': none is paused')
    j = hit[0]
    for pid in j['suspended']['pids']:
        try:
            psutil.Process(pid).resume()
        except psutil.Error:
            pass
    with open(os.path.join(board_dir(), 'jobs', j['id'] + '.resume'), 'w', encoding='utf8') as f:
        f.write(str(time.time()))
    free = memory()[0]
    return (f"resumed '{str(j['what'])[:60]}' ({len(j['suspended']['pids'])} processes); it will not be paused again. "
            f"{free:.1f} GB of commit free" + (": watch it, memory is still low" if free < SUSPEND_COMMIT_GB else ''))


def child_env(job=None, env=None):
    """The environment for a command a slot holder starts: it carries the slot, so a render the command asks for
    runs in it instead of deadlocking on a second slot (ledger:M81), and the slot's thread cap for BLAS, OpenMP and
    torch (ledger:M132). job: the slot's job (default: the one this thread holds). `machine run` uses it; so can a
    script that holds a slot and starts a worker: subprocess.run(cmd, env=machine.child_env())."""
    job = job or getattr(_held, 'job', None)
    e = dict(os.environ if env is None else env)
    if job and job.get('id'):
        e[SLOT_ENV] = job['id']
        if job.get('threads'):
            for k in THREAD_ENV:
                e[k] = str(job['threads'])
    return e

METER_S = 1.0          # the job meter samples CPU, memory and the GPU this often


class _GpuSampler:
    """One nvidia-smi for a whole job, printing the GPU's load and memory every second (never a process per
    sample). The GPU is shared: these are the whole GPU's numbers while the job held its slot."""

    def __init__(self):
        self.busy_s, self.mem_peak_gb, self.n = 0.0, 0.0, 0
        try:
            self.p = subprocess.Popen(['nvidia-smi', '--query-gpu=utilization.gpu,memory.used',
                                       '--format=csv,noheader,nounits', f'--loop-ms={int(METER_S * 1000)}'],
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self.p = None
            return
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _read(self):
        for line in self.p.stdout:
            try:
                util, mem = (float(x) for x in line.split(','))
            except ValueError:
                continue
            self.busy_s += util / 100 * METER_S
            self.mem_peak_gb = max(self.mem_peak_gb, mem / 1024)
            self.n += 1

    def stop(self):
        if self.p is not None:
            self.p.terminate()
            try:
                self.p.wait(5)
            except subprocess.TimeoutExpired:
                self.p.kill()
            return {'gpu_busy_s': round(self.busy_s, 1), 'gpu_mem_peak_gb': round(self.mem_peak_gb, 2),
                    'gpu_samples': self.n}
        return {}


class _Meter:
    """While a job holds its slot: the CPU seconds and peak memory of the process that holds it and of every child
    it starts (a `machine run` command, Blender, a render worker), and the GPU's load."""

    def __init__(self, job, gpu_sampler=True, path=None):
        self.job, self.cpu, self.rss_peak, self.stop_ = job, {}, 0, threading.Event()
        self.path, self.priv_peak, self.wrote, self.noted = path, 0, {}, 0.0
        self.resumed, self.pinned = False, set()
        me = psutil.Process()
        t = me.cpu_times()
        self.base = (me.pid, t.user + t.system)       # the holder's CPU before the slot is not the job's
        self.base_w = _write_bytes(me)
        mi = me.memory_info()
        self.base_priv = getattr(mi, 'private', mi.rss)   # the holder's memory before the slot is not the job's
        self.gpu = _GpuSampler() if gpu_sampler else None
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _sample(self):
        try:
            me = psutil.Process()
            procs = [me] + me.children(recursive=True)
        except psutil.Error:
            return
        rss = priv = 0
        for p in procs:
            try:
                key = (p.pid, p.create_time())
                t = p.cpu_times()
                self.cpu[key] = t.user + t.system
                mi = p.memory_info()
                rss += mi.rss
                own = getattr(mi, 'private', mi.rss)  # Windows: private bytes, what the commit charge counts
                priv += max(0, own - self.base_priv) if p.pid == self.base[0] else own
                w = _write_bytes(p)
                if w is not None:
                    self.wrote[key] = w
                if self.job.get('cores') and p.pid != self.base[0] and key not in self.pinned:
                    if p.cpu_affinity() != self.job['cores']:   # a child that set its own: back to the slot's cores
                        p.cpu_affinity(self.job['cores'])
                    self.pinned.add(key)
            except (psutil.Error, AttributeError, NotImplementedError):
                pass
        self.rss_peak = max(self.rss_peak, rss)
        self.priv_peak = max(self.priv_peak, priv)
        self._note(priv / 2 ** 30)

    def _note(self, now_gb):
        """Past its declared memory by MEM_OVER: say so once in the job's own output and on the board. Every
        JOB_NOTE_S: the memory it uses now, on its board file."""
        decl = self.job.get('mem_gb') or 0
        over = bool(decl) and now_gb > decl * MEM_OVER and now_gb - decl > MEM_OVER_MIN_GB
        if over and not self.job.get('over'):
            free = memory()[0]
            print(f"[ismail.machine] OVER MEMORY: '{str(self.job['what'])[:60]}' uses {now_gb:.1f} GB, declared "
                  f"{decl:g} GB (--mem); {free:.1f} GB of commit is free. Past the commit limit Windows grows the "
                  f"pagefile into the disk. Stop it if it keeps growing; next time declare what it needs or work in "
                  f"smaller pieces.", file=sys.stderr, flush=True)
        if over:
            self.job['over'] = round(max(self.job.get('over') or 0, now_gb), 2)
        self._resume_asked()
        if decl and now_gb >= decl * MEM_SUSPEND and not self.job.get('suspended') and not self.resumed:
            free, limit, _ = memory()
            if free < min(SUSPEND_COMMIT_GB, SUSPEND_COMMIT_FRAC * limit):
                self._suspend(now_gb, free)
        if self.path and (over or time.time() - self.noted >= JOB_NOTE_S):
            self.noted = time.time()
            self.job['mem_now_gb'] = round(now_gb, 2)
            _write_job(self.path, self.job)

    def _suspend(self, now_gb, free):
        """Pause every process the job started (ledger:M154). Only the children: the holder keeps metering, and a
        job whose memory is in the holder itself is only warned about. Reversible: `machine resume`."""
        try:
            kids = psutil.Process().children(recursive=True)
        except psutil.Error:
            kids = []
        paused = []
        for k in kids:
            try:
                k.suspend()
                paused.append(k.pid)
            except psutil.Error:
                pass
        what, decl = str(self.job['what'])[:60], self.job.get('mem_gb') or 0
        if not paused:
            if not self.job.get('suspend_failed'):
                self.job['suspend_failed'] = True
                print(f"[ismail.machine] OVER MEMORY x{now_gb / decl:.0f}: '{what}' uses {now_gb:.1f} GB with "
                      f"{free:.1f} GB of commit free, in this process itself, so it cannot be paused: stop it now",
                      file=sys.stderr, flush=True)
            return
        self.job['suspended'] = {'why': 'OVER', 'at': time.time(), 'gb': round(now_gb, 2), 'pids': paused}
        print(f"[ismail.machine] SUSPENDED: OVER: '{what}' uses {now_gb:.1f} GB, declared {decl:g} GB (--mem), and "
              f"{free:.1f} GB of commit is free, so its processes are paused (not killed; nothing is lost). When memory "
              f"is free again: python -m ismail.machine resume {self.job['id']}. Or end it, and next time declare "
              f"what it needs or work in smaller pieces.", file=sys.stderr, flush=True)
        if self.path:
            _write_job(self.path, self.job)

    def _resume_asked(self):
        """`machine resume` leaves <job>.resume beside the job's board file: go on, and never pause it again."""
        if not self.path:
            return
        marker = self.path[:-5] + '.resume'
        if not os.path.exists(marker):
            return
        for pid in (self.job.get('suspended') or {}).get('pids', []):
            try:
                psutil.Process(pid).resume()
            except psutil.Error:
                pass
        try:
            os.remove(marker)
        except OSError:
            pass
        self.resumed = True
        if self.job.get('suspended'):
            self.job['resumed'] = time.time()
        self.job['suspended'] = None
        _write_job(self.path, self.job)

    def _loop(self):
        while not self.stop_.wait(METER_S):
            self._sample()

    def stop(self):
        self.stop_.set()
        self.t.join(5)
        self._sample()
        for pid, secs in (self.job.get('exited_cpu') or {}).items():   # a child that ended between two samples
            self.cpu = {k: v for k, v in self.cpu.items() if k[0] != pid}
            self.cpu[(pid, 'exited')] = secs
        cpu = sum(v for (pid, _), v in self.cpu.items() if pid != self.base[0])
        cpu += max(0.0, max((v for (pid, _), v in self.cpu.items() if pid == self.base[0]), default=0.0) - self.base[1])
        for pid, b in (self.job.get('exited_write') or {}).items():
            self.wrote = {k: v for k, v in self.wrote.items() if k[0] != pid}
            self.wrote[(pid, 'exited')] = b
        w = sum(v for (pid, _), v in self.wrote.items() if pid != self.base[0])
        if self.base_w is not None:
            w += max(0, max((v for (pid, _), v in self.wrote.items() if pid == self.base[0]), default=0) - self.base_w)
        out = {'cpu_s': round(cpu, 1), 'rss_peak_gb': round(self.rss_peak / 2 ** 30, 2),
               'mem_peak_gb': round(self.priv_peak / 2 ** 30, 2), 'write_gb': round(w / 2 ** 30, 3)}
        if self.job.get('over'):
            out['over_gb'] = self.job['over']
        if self.job.get('suspended') or self.job.get('resumed'):
            out['suspended'] = True
        if self.gpu is not None:
            out.update(self.gpu.stop())
        return out


def _write_bytes(p):
    """Bytes a process has written (files, and on Windows pipes and devices too), or None where it cannot be read."""
    try:
        return p.io_counters().write_bytes
    except (psutil.Error, AttributeError, NotImplementedError):
        return None


def _child_written(p):
    """Bytes a finished child wrote (Windows: from the handle Popen still holds), or None."""
    if sys.platform != 'win32':
        return None
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOps', 'WriteOps', 'OtherOps', 'Read', 'Write', 'Other')]
    io = IO()
    if ctypes.windll.kernel32.GetProcessIoCounters(int(p._handle), ctypes.byref(io)):
        return io.Write
    return None


def _write_job(path, job):
    """Rewrite a running job's board file whole (a reader never sees half of it)."""
    tmp = path[:-5] + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump(job, f)
        os.replace(tmp, path)
    except OSError:
        pass                                           # a reader holds it open: the next note writes it


def _child_cpu_s(p, before=None):
    """CPU seconds a finished child used, read from the system after it exited (a child shorter than one meter
    sample is never seen alive). Windows: the process handle Popen still holds; elsewhere: the children's rusage
    since `before`."""
    if sys.platform == 'win32':
        FT = ctypes.c_ulonglong
        c, e, k, u = FT(), FT(), FT(), FT()
        if ctypes.windll.kernel32.GetProcessTimes(int(p._handle), ctypes.byref(c), ctypes.byref(e), ctypes.byref(k),
                                                  ctypes.byref(u)):
            return (k.value + u.value) / 1e7
        return None
    import resource
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime - (before or 0.0)


def _children_cpu_now():
    if sys.platform == 'win32':
        return None
    import resource
    r = resource.getrusage(resource.RUSAGE_CHILDREN)
    return r.ru_utime + r.ru_stime


def _song_of(cwd):
    """The song a job ran for: the folder under songs/ in its working directory, if any."""
    parts = os.path.normpath(cwd).replace('\\', '/').split('/')
    for i, part in enumerate(parts[:-1]):
        if part == 'songs' and parts[i + 1] and not parts[i + 1].startswith('_'):
            return parts[i + 1]
    return None


def _record(job, wait_s, state, meter, outcome):
    """One line per finished job in <board>/history.jsonl, append only: what ran, for which song, when, how long it
    waited, how it ended, and what it used. Speed claims are made from this file."""
    ended = time.time()
    line = {'what': str(job['what'])[:200], 'who': job['who'], 'song': job.get('song') or _song_of(os.getcwd()),
            'kind': job['kind'], 'cwd': os.getcwd(), 'est_s': job.get('est_s'), 'started': round(job['started'], 2),
            'ended': round(ended, 2), 'seconds': round(ended - job['started'], 1), 'waited_s': round(wait_s, 1),
            'exit': job.get('exit', outcome), 'forced': job['forced'], 'at_start': state}
    line.update(meter)
    d = disks()
    if d:
        line['disk_free_end_gb'] = round(d[-1][1], 2)
    try:
        with _board_lock():
            with open(os.path.join(board_dir(), 'history.jsonl'), 'a', encoding='utf8') as f:
                f.write(json.dumps(line) + '\n')
    except (OSError, MachineBusy):
        pass                                           # a full disk or a stuck board never fails the job itself


@contextlib.contextmanager
def slot(kind, what, est_s=None, mem_gb=0.0, who=None, force=False, threads=THREADS, wait=None, disk_gb=0.0,
         disk_path=None, disk_hint='', cmd=None):
    """Hold a heavy-job slot while the block runs. Re-entrant: a job inside a job of this thread (a fit that
    renders) runs in the slot it already holds, and so does one under `machine run` in a child process (the slot
    handed down in $ISMAIL_SLOT). Raises MachineBusy with what to do when it may not start; with wait (seconds) it
    stands in line until it may, then raises only if the wait runs out. threads: the job's cap on numeric threads
    (BLAS, OpenMP, torch; None: no cap). disk_gb: about what it writes on disk_path's drive; disk_hint: how to write
    less, said when the disk refuses it."""
    if getattr(_held, 'depth', 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    if kind not in SLOTS and kind != 'live':
        raise ValueError(f"kind is 'gpu', 'cpu' or 'live', not {kind!r}")
    parent = _handed_down(kind)
    if parent is not None:     # the run's own meter counts this process: no second board entry, no history line
        _held.depth, _held.threads, _held.job = 1, parent.get('threads'), parent   # the run's cap is what the board counts
        undo = _limit_threads(_held.threads)
        try:
            yield parent
        finally:
            _held.depth, _held.threads, _held.job = 0, None, None
            _undo(undo)
        return
    who = who or _who()
    me = psutil.Process()
    since, wid, wpath = time.time(), None, None
    deadline = since + float(wait) if wait else None
    try:
        while True:
            if kind == 'cpu' and not force:
                cpu_load()             # sample outside the board's lock (it takes CPU_SAMPLE_S); check() reuses it
            with _board_lock():
                why = '' if force or kind == 'live' else check(kind, mem_gb, who=who, since=since, me=wid,
                                                               disk_gb=disk_gb, disk_path=disk_path,
                                                               disk_hint=disk_hint, what=what, cmd=cmd,
                                                               threads=threads)
                # a job with a lighter way to run (disk_hint) is told it at once: a full drive rarely clears in line
                if not why or not deadline or time.time() >= deadline or (disk_hint and why.startswith('disk:')):
                    if why:
                        raise MachineBusy(f"not starting {kind} job '{what}': {why}. Retry when that clears (the "
                                          f"machine op shows the board), wait in line (`run --wait 30m`), do lighter "
                                          f"work meanwhile, or pass force=True only if the user says so.")
                    # on the board inside the same lock as the check: registered later, the slot looked free to the
                    # next waiter in between (this waiter's line file already gone), and two jobs took one GPU slot
                    job = {'kind': kind, 'what': what, 'who': who, 'pid': me.pid, 'pid_start': me.create_time(),
                           'started': time.time(), 'est_s': est_s, 'mem_gb': mem_gb, 'disk_gb': disk_gb,
                           'forced': bool(force), 'threads': threads}
                    # unique per slot: two slots taken in the same millisecond by one process overwrote each other
                    job['id'] = f"{me.pid}_{int(job['started'] * 1000)}_{os.urandom(3).hex()}"
                    path = os.path.join(board_dir(), 'jobs', job['id'] + '.json')
                    with open(path, 'w', encoding='utf8') as f:
                        json.dump(job, f)
                    break
                if wid is None:        # stand in line
                    wid = f"{me.pid}_{int(since * 1000)}_{os.urandom(3).hex()}"
                    os.makedirs(os.path.join(board_dir(), 'waiting'), exist_ok=True)
                    wpath = os.path.join(board_dir(), 'waiting', wid + '.json')
                    with open(wpath, 'w', encoding='utf8') as f:
                        json.dump({'id': wid, 'kind': kind, 'what': what, 'who': who, 'pid': me.pid,
                                   'pid_start': me.create_time(), 'since': since}, f)
            time.sleep(WAIT_POLL_S)
    finally:
        if wpath:
            try:
                os.remove(wpath)
            except OSError:
                pass
    g = gpu()
    state = {'gpu_temp': g['temp'], 'gpu_clock': g['clock'], 'gpu_reasons': hex(g['reasons']),
             'gpu_trouble': gpu_trouble(g) or None} if g else {}
    if _cpu_cache[1] is not None:
        state['cpu_busy'] = round(_cpu_cache[1][0])
    d = disks(disk_path)
    if d:
        state['disk'] = d[-1][0]
        state['disk_free_gb'] = round(d[-1][1], 2)
    state['commit_free_gb'] = round(memory()[0], 2)
    _held.depth, _held.threads, _held.job = 1, threads, job
    undo = []
    meter = _Meter(job, gpu_sampler=g is not None, path=path)
    outcome = 'ok'
    try:
        undo = _limit_threads(threads)
        yield job
    except BaseException as e:
        outcome = type(e).__name__
        raise
    finally:
        _held.depth, _held.threads, _held.job = 0, None, None
        _undo(undo)
        used = meter.stop()
        for f in (path, path[:-5] + '.tmp'):
            try:
                os.remove(f)
            except OSError:
                pass
        _record(job, job['started'] - since, state, used, outcome)


def _who():
    return os.environ.get('ISMAIL_SESSION') or os.path.basename(os.getcwd().rstrip('\\/')) or 'unknown'


def render_memory_gb(n_samples, n_tracks, n_buses=0, n_sources=0):
    """Peak memory of one studio render: the mix, buses, sidechain sources and one track in float64, plus the
    stems as float32 spans (assumed half full)."""
    full = n_samples * 2 * 8
    return (full * (3 + n_buses + n_sources) + n_samples * 2 * 4 * n_tracks * 0.5) / 2 ** 30


# ------------------------------------------------------------------ the board as text

def board():
    g = gpu()
    free, limit, ram = memory()
    L = []
    if g is None:
        L.append("GPU: none found (no nvidia-smi)")
    else:
        hot = gpu_trouble(g)
        L.append(f"GPU: {g['temp']:.0f} C, {g['util']:.0f}% busy, core {g['clock']:.0f}/{g['max_clock']:.0f} MHz"
                 f"{' (idle)' if g['reasons'] & 0x1 else ''}, VRAM {g['mem_used_gb']:.1f}/{g['mem_total_gb']:.1f} GB"
                 + (f"  HOT: {hot}" if hot else ''))
    busy, top = cpu_load()
    L.append(f"CPU: {busy:.0f}% busy over {CPU_SAMPLE_S:.0f} s, {psutil.cpu_count()} threads; top: {_top_text(top)}"
             + (f"  BUSY (limit {CPU_BUSY:.0f}%)" if busy >= CPU_BUSY else ''))
    L.append(f"memory: {free:.1f} GB of {limit:.0f} GB commit free, {ram:.1f} GB RAM free"
             + (f"  LOW (heavy jobs wait below {COMMIT_FLOOR_GB:.0f} GB)" if free < COMMIT_FLOOR_GB else ''))
    L.append(disk_line())
    js = jobs()
    L.append(f"heavy jobs ({len(js)}; slots: gpu {SLOTS['gpu']}, cpu {SLOTS['cpu']}, a live engine holds a cpu slot):")
    L += [f"  {_describe(j)}" for j in js] or ["  none"]
    pr = priority()
    if pr:
        L.append(f"priority: {pr['who']} goes first in line until {_until(pr['until'])}"
                 f" (given by {pr['by']}" + (f": {pr['why']}" if pr.get('why') else '') + ")")
        miss = priority_match()
        if miss:
            L.append('  WARNING: ' + miss)
    if any(j['kind'] == 'live' for j in js):
        pol = on_air_policy()
        L.append(f"ON AIR: a live set plays; policy {pol['policy']} (by {pol['by']}): "
                 + ("GPU jobs and Blender renders wait until it ends (temporary, hq:D-11)" if pol['policy'] == 'set_first'
                    else "no hold"))
    ws = waiters()
    if ws:
        L.append(f"waiting in line ({len(ws)}):")
        L += [f"  {w['kind']} '{w['what']}' ({w['who']}, waiting {_ago(w['since'])})" for w in ws]
    for kind in ('gpu', 'cpu'):
        why = check(kind, _jobs=js)
        L.append(f"a new {kind} job: " + ('go' if not why else f"WAIT: {why}"))
    return '\n'.join(L)


def disk_line():
    """'disk: D: 3.7 GB free LOW ...; pagefile D: 33.6 GB' for the board and live_status."""
    ds = disks()
    worst = min((f for _, f in ds), default=None)
    pf = pagefiles()
    flag = ''
    if worst is not None and worst < DISK_FLOOR_GB:
        flag = f"  LOW (heavy jobs wait below {DISK_FLOOR_GB:.0f} GB; `machine disk` shows where it went)"
    elif worst is not None and worst < DISK_WARN_GB:
        flag = f"  getting low (heavy jobs wait below {DISK_FLOOR_GB:.0f} GB)"
    return ("disk: " + ', '.join(f"{d} {f:.1f} GB free" for d, f in ds)
            + (f"; pagefile " + ', '.join(f"{d} {gb:.1f} GB" for d, gb in pf) if pf else '') + flag)


def pressure_line():
    """'machine: commit 14.1 GB free; D: 3.7 GB free ... LOW' for live_status: a set on air writes takes to disk."""
    free = memory()[0]
    return (f"machine: commit {free:.1f} GB free" + (f" LOW (under {COMMIT_FLOOR_GB:.0f} GB)" if free < COMMIT_FLOOR_GB
                                                      else '') + "; " + disk_line()[len('disk: '):])


def _tree_bytes(path):
    total = 0
    stack = [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        total += e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    return total


def disk_sizes(root=None):
    """-> ({folder under songs/: bytes}, {path of each _reclaim folder: bytes})."""
    root = root or SONGS
    sizes, reclaim = {}, {}
    try:
        entries = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return sizes, reclaim
    for e in entries:
        sizes[e.name] = _tree_bytes(e.path)
        stack = [e.path]
        while stack:                                   # _reclaim folders sit a few levels down at most
            d = stack.pop()
            try:
                subs = [x for x in os.scandir(d) if x.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for x in subs:
                if x.name == '_reclaim':
                    reclaim[os.path.relpath(x.path, root)] = _tree_bytes(x.path)
                elif x.path.count(os.sep) - root.count(os.sep) < 4 and x.name not in ('cache', 'renders', 'sounds'):
                    stack.append(x.path)
    return sizes, reclaim


def disk_text(top=12, root=None):
    """Where the disk went: each drive's free space and pagefile, the biggest folders under songs/ with their growth
    since the last day a snapshot was kept (<board>/disk/<date>.json), and every _reclaim folder."""
    sizes, reclaim = disk_sizes(root)
    sd = os.path.join(board_dir(), 'disk')
    today = time.strftime('%Y-%m-%d')
    before, day = {}, None
    try:
        days = sorted(f[:-5] for f in os.listdir(sd) if f.endswith('.json') and f[:-5] < today)
    except OSError:
        days = []
    if days:
        day = days[-1]
        try:
            with open(os.path.join(sd, day + '.json'), encoding='utf8') as f:
                before = json.load(f)
        except (OSError, ValueError):
            day = None
    snap = os.path.join(sd, today + '.json')
    if not os.path.exists(snap):
        try:
            os.makedirs(sd, exist_ok=True)
            with open(snap, 'w', encoding='utf8') as f:
                json.dump(sizes, f)
        except OSError:
            pass
    G = 2 ** 30
    L = [disk_line(), f"songs/ ({root or SONGS}): {sum(sizes.values()) / G:.1f} GB in {len(sizes)} folders"
         + (f"; growth since {day}" if day else "; first snapshot kept today, growth shows from tomorrow")]
    for name, b in sorted(sizes.items(), key=lambda kv: -kv[1])[:top]:
        grow = ''
        if day:
            d = b - before.get(name, 0)
            grow = f"  {'+' if d >= 0 else '-'}{abs(d) / G:.2f} GB" if abs(d) >= 0.01 * G else '  same'
        L.append(f"  {b / G:7.2f} GB  {name}{grow}")
    if day:
        grown = sorted(((b - before.get(n, 0), n) for n, b in sizes.items()), reverse=True)
        fast = [f"{n} +{d / G:.1f} GB" for d, n in grown[:5] if d >= 0.5 * G]
        if fast:
            L.append("grew most since " + day + ": " + ', '.join(fast))
    if reclaim:
        L.append(f"_reclaim (moved out, waiting for the user to clear): {sum(reclaim.values()) / G:.1f} GB")
        L += [f"  {b / G:7.2f} GB  {p}" for p, b in sorted(reclaim.items(), key=lambda kv: -kv[1])]
    else:
        L.append("_reclaim: none")
    return '\n'.join(L)


def history(song=None, since=None):
    """Finished jobs from <board>/history.jsonl, oldest first (song: only that song's; since: epoch seconds)."""
    out = []
    try:
        with open(os.path.join(board_dir(), 'history.jsonl'), encoding='utf8') as f:
            for line in f:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if (song is None or j.get('song') == song) and (since is None or j.get('started', 0) >= since):
                    out.append(j)
    except OSError:
        pass
    return out


def _since(text):
    t = str(text).strip().lower()
    if t[-1:] == 'd' and t[:-1].replace('.', '', 1).isdigit():
        return time.time() - float(t[:-1]) * 86400
    if t[-1:] in 'hms' and t[:-1].replace('.', '', 1).isdigit():
        return time.time() - duration_s(t)
    try:
        return time.mktime(time.strptime(t, '%Y-%m-%d'))
    except ValueError:
        raise ValueError(f"--since {text!r}: a date (2026-10-04) or a span (7d, 12h)")


def history_text(song=None, since=None, n_jobs=0):
    js = history(song, since)
    if not js:
        return ("no finished jobs recorded" + (f" for {song}" if song else '') + " (the history starts with the first "
                "job that finished after it was added; earlier jobs were never kept)")
    first = time.strftime('%Y-%m-%d %H:%M', time.localtime(js[0]['started']))
    by = {}
    for j in js:
        s = by.setdefault(j.get('song') or '(no song)', {'jobs': 0, 'wall': 0.0, 'cpu': 0.0, 'gpu': 0.0, 'failed': 0,
                                                       'write': 0.0, 'over': 0})
        s['jobs'] += 1
        s['write'] += j.get('write_gb', 0)
        s['over'] += bool(j.get('over_gb'))
        s['wall'] += j.get('seconds', 0)
        s['cpu'] += j.get('cpu_s', 0)
        s['gpu'] += j.get('gpu_busy_s', 0)
        s['failed'] += j.get('exit') not in (0, 'ok')
    L = [f"{len(js)} jobs since {first} (wall = time holding a slot; CPU = seconds of CPU across cores; GPU = whole-GPU "
         f"busy seconds while the job held its slot; the GPU is shared)"]
    for name, s in sorted(by.items(), key=lambda kv: -kv[1]['wall']):
        L.append(f"  {name}: {s['jobs']} jobs, {s['wall'] / 3600:.2f} h wall, {s['cpu'] / 3600:.2f} h CPU, "
                 f"{s['gpu'] / 3600:.2f} h GPU busy, {s['write']:.1f} GB written"
                 + (f", {s['failed']} did not end well" if s['failed'] else '')
                 + (f", {s['over']} went past their declared memory" if s['over'] else ''))
    if n_jobs:
        L.append(f"last {min(n_jobs, len(js))} jobs:")
        for j in js[-n_jobs:]:
            L.append(f"  {time.strftime('%m-%d %H:%M', time.localtime(j['started']))} {j['kind']} '{j['what'][:60]}' "
                     f"({j.get('song') or '-'}) {j.get('seconds', 0) / 60:.1f} min, waited {j.get('waited_s', 0):.0f} s,"
                     f" CPU {j.get('cpu_s', 0):.0f} s, peak {j.get('mem_peak_gb', j.get('rss_peak_gb', 0)):.1f} GB"
                     + (f" (declared {j['mem_gb']:g})" if j.get('mem_gb') else '')
                     + f", wrote {j.get('write_gb', 0):.2f} GB, exit {j.get('exit')}")
    return '\n'.join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description='The shared machine: the board, or run a command in a heavy-job slot.')
    sub = ap.add_subparsers(dest='cmd')
    r = sub.add_parser('run', help='run a command in a slot: python -m ismail.machine run --gpu -- blender -b ...')
    k = r.add_mutually_exclusive_group(required=True)
    k.add_argument('--gpu', action='store_true')
    k.add_argument('--cpu', action='store_true')
    r.add_argument('--mem', type=float, default=0.0, help='expected peak memory, GB')
    r.add_argument('--disk', type=float, default=0.0, help='expected disk written, GB (refused if it would leave the '
                   'drive under the floor)')
    r.add_argument('--est', default=None, help='expected duration: 10m, 600s, 1.5h (a bare number is minutes)')
    r.add_argument('--what', default=None, help='what it is, for the board')
    r.add_argument('--force', action='store_true', help='only when the user says so')
    r.add_argument('--wait', nargs='?', const='30m', default=None,
                   help='stand in line for the slot instead of being refused: a duration (default 30m)')
    r.add_argument('--threads', type=int, default=None,
                   help=f'numeric threads the command may use (BLAS, OpenMP, torch; default {THREADS} for --cpu, no cap '
                        f'for --gpu; 0: no cap). While a set is on air the CPU jobs share {on_air_threads()}')
    r.add_argument('command', nargs=argparse.REMAINDER)
    rs = sub.add_parser('resume', help='go on with a job paused for its memory: resume <job id, pid or name>')
    rs.add_argument('job')
    p = sub.add_parser('priority', help='the user gives a session first place in line: priority vox --for 3h --by "the user"')
    p.add_argument('who', nargs='?', help="the session's name as the board shows it")
    p.add_argument('--for', dest='for_', default='2h', help='how long: 30m, 3h, 3d (default 2h)')
    p.add_argument('--by', default=None, help='who gave it (the user)')
    p.add_argument('--why', default='', help='what it is for, for the board')
    p.add_argument('--clear', action='store_true')
    h = sub.add_parser('history', help='what ran and what it used: compute time per song (history --song tambopata)')
    h.add_argument('--song', default=None)
    h.add_argument('--since', default=None, help='a date (2026-10-04) or a span back from now (7d, 12h)')
    h.add_argument('--jobs', type=int, default=0, help='also list the last N jobs')
    oa = sub.add_parser('on-air', help="what wins while a live set is on air: on-air --policy set_first|off --by 'the user'")
    oa.add_argument('--policy', default=None, choices=ON_AIR_POLICIES)
    oa.add_argument('--by', default=None, help='who decided (the user)')
    oa.add_argument('--why', default='')
    dk = sub.add_parser('disk', help='where the disk went: songs folders and their growth, _reclaim, the pagefile')
    dk.add_argument('--top', type=int, default=12, help='how many songs folders to list')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    if a.cmd == 'priority':
        if a.clear:
            clear_priority()
            print("priority cleared")
            return 0
        if not a.who:
            pr = priority()
            print(f"priority: {pr['who']} until {_until(pr['until'])} (by {pr['by']})" if pr else "no priority in force")
            if pr and priority_match():
                print('WARNING: ' + priority_match())
            return 0
        try:
            pr = set_priority(a.who, duration_s(a.for_), a.by, a.why)
        except ValueError as e:
            ap.error(str(e))
        print(f"priority: {pr['who']} goes first in line until {_until(pr['until'])} "
              f"(given by {pr['by']}); the heat limit and the busy CPU still hold")
        if priority_match():
            print('WARNING: ' + priority_match())
        return 0
    if a.cmd == 'history':
        try:
            print(history_text(a.song, _since(a.since) if a.since else None, a.jobs))
        except ValueError as e:
            ap.error(str(e))
        return 0
    if a.cmd == 'resume':
        print(resume(a.job))
        return 0
    if a.cmd == 'disk':
        print(disk_text(a.top))
        return 0
    if a.cmd == 'on-air':
        if a.policy:
            try:
                set_on_air_policy(a.policy, a.by, a.why)
            except ValueError as e:
                ap.error(str(e))
        pol = on_air_policy()
        print(f"on air: policy {pol['policy']} (by {pol['by']}" + (f": {pol['why']}" if pol.get('why') else '') + ")")
        return 0
    if a.cmd != 'run':
        print(board())
        return 0
    cmd = a.command[1:] if a.command[:1] == ['--'] else a.command
    if not cmd:
        ap.error('run needs a command after --')
    kind = 'gpu' if a.gpu else 'cpu'
    threads = (a.threads if a.threads is not None else THREADS if kind == 'cpu' else None) or None
    try:
        est_s = duration_s(a.est) if a.est else None
    except ValueError as e:
        ap.error(str(e))
    try:
        wait_s = duration_s(a.wait) if a.wait else None
    except ValueError as e:
        ap.error(str(e))
    try:
        with slot(kind, a.what or ' '.join(cmd)[:80], est_s=est_s, mem_gb=a.mem,
                  force=a.force, threads=threads, wait=wait_s, disk_gb=a.disk, cmd=cmd) as job:
            before = _children_cpu_now()
            p = subprocess.Popen(cmd, env=child_env(job))    # its renders run in this slot, with its thread cap
            try:
                psutil.Process(p.pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == 'win32' else 10)
            except (psutil.Error, AttributeError):
                pass
            cores = pick_cores(threads)
            if cores:
                try:                                         # its children inherit it; the meter catches strays
                    psutil.Process(p.pid).cpu_affinity(cores)
                    job['cores'] = cores
                except (psutil.Error, AttributeError, NotImplementedError, ValueError):
                    pass
            job['exit'] = p.wait()
            try:
                secs = _child_cpu_s(p, before)
                if secs is not None:
                    job['exited_cpu'] = {p.pid: secs}
                b = _child_written(p)
                if b is not None:
                    job['exited_write'] = {p.pid: b}
            except (OSError, AttributeError, ValueError):
                pass
            return job['exit']
    except MachineBusy as e:
        print(e, file=sys.stderr)
        return 75                                            # EX_TEMPFAIL: try again later


if __name__ == '__main__':
    sys.exit(main())
