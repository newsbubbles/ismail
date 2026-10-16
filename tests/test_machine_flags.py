"""What the governor's board says about jobs and the machine beyond the slots (ledger:M122-M126, hq:D-174): a --cpu
job holding the GPU is MISDECLARED, a job far past its estimate doing nothing is HUNG, a top CPU user whose parent is
gone is an ORPHAN, the pagefile's drive warns when it runs low, and `machine disk` looks beyond songs/. Every probe
is faked here (no real GPU load, no real orphans, no walk of a real drive), except one read of the real per-process
GPU counter, which only has to fail soft."""
import json
import os
import time

from ismail import machine

from test_machine import board  # noqa: F401  (the shared board fixture)


def cpu_job(**kw):
    return dict({'kind': 'cpu', 'what': 'render a scene', 'who': 'someone', 'pid': os.getpid(),
                 'started': 1000.0, 'est_s': None}, **kw)


# ------------------------------------------------------------------ M122 MISDECLARED

def test_a_cpu_job_holding_the_gpu_for_a_minute_is_misdeclared_once_and_takes_the_gpu_slot(board, capsys):
    job, writes = cpu_job(), []
    w = machine._Watch(job, write=lambda: writes.append(dict(job)), ncpu=8)
    for i in range(0, 50, 5):                                  # under a minute: not yet
        w.see(1000.0 + i, i * 2.0, gpu_pct=85.0)
    assert not job.get('misdeclared')
    for i in range(50, 120, 5):
        w.see(1000.0 + i, i * 2.0, gpu_pct=85.0)
    assert job['misdeclared']['gpu'] == 85 and len(writes) == 1
    assert capsys.readouterr().err.count('MISDECLARED') == 1   # told once
    why = machine.check('gpu', _jobs=[job])
    assert 'gpu slots are full' in why and 'MISDECLARED' in why and 'run --gpu' in why
    assert 'cpu slots are full' not in machine.check('cpu', _jobs=[job])   # it still holds one cpu slot of two


def test_a_little_gpu_or_no_reading_never_flags_and_a_gpu_job_is_never_misdeclared():
    for kind, share in (('cpu', 12.0), ('cpu', None), ('gpu', 95.0)):
        job = cpu_job(kind=kind)
        w = machine._Watch(job, ncpu=8)
        for i in range(0, 200, 5):
            w.see(1000.0 + i, i * 2.0, gpu_pct=share)
        assert not job.get('misdeclared'), (kind, share)


def test_a_short_spike_is_averaged_out():
    job = cpu_job()
    w = machine._Watch(job, ncpu=8)
    for i in range(0, 200, 5):
        w.see(1000.0 + i, i * 2.0, gpu_pct=90.0 if i % 60 == 0 else 2.0)
    assert not job.get('misdeclared')


def test_the_meter_reads_the_jobs_tree_gpu_share_and_puts_misdeclared_on_the_board(board, monkeypatch):
    seen = []

    class Busy:
        def tree(self, pids):
            seen.append(set(pids))
            return 70.0

        def close(self):
            pass

    monkeypatch.setattr(machine, '_GpuPids', Busy)
    monkeypatch.setattr(machine, '_GpuSampler', lambda: type('G', (), {'stop': lambda self: {}})())
    monkeypatch.setattr(machine, 'METER_S', 0.02)
    monkeypatch.setattr(machine, 'GPU_PID_S', 0.02)
    monkeypatch.setattr(machine, 'MISDECLARED_S', 0.3)
    with machine.slot('cpu', 'a render said to be cpu', who='someone'):
        t_end = time.time() + 10
        while time.time() < t_end and not machine.jobs()[0].get('misdeclared'):
            time.sleep(0.05)
        js = machine.jobs()
        assert js[0]['misdeclared']['gpu'] == 70
        assert 'MISDECLARED' in machine.board() and 'gpu slots are full' in machine.check('gpu')
    assert os.getpid() in seen[0]                              # the holder's own process is in its tree
    assert machine.history()[-1]['misdeclared_gpu'] == 70


def test_the_per_process_gpu_probe_fails_soft():
    p = machine._GpuPids()
    try:
        got = p.tree({os.getpid()})                            # the first read has no rate yet, or no counter at all
        assert got is None or 0.0 <= got <= 100.0
    finally:
        p.close()
    dead = machine._GpuPids.__new__(machine._GpuPids)
    dead.q = dead.c = None
    assert dead.by_pid() is None and dead.tree({1}) is None


# ------------------------------------------------------------------ M124 HUNG

def quiet(w, t0, t1, cpu_s=50.0, gpu=0.0, whole=None, step=5):
    for t in range(int(t0), int(t1), step):
        w.see(float(t), cpu_s, gpu_pct=gpu, whole_gpu=whole)


def test_a_job_past_twice_its_estimate_idle_for_five_minutes_is_hung_told_once_and_clears(board, capsys):
    job, writes = cpu_job(est_s=600.0, started=0.0), []
    w = machine._Watch(job, write=lambda: writes.append(1), ncpu=8)
    for t in range(600, 1100, 5):                              # one core busy
        w.see(float(t), t - 600.0, gpu_pct=0.0)
    quiet(w, 1100, 1300, cpu_s=500.0)                          # past 2x its estimate (1200), quiet under 5 min
    assert not job.get('hung')
    quiet(w, 1300, 1560, cpu_s=500.0)                          # five quiet minutes
    assert job['hung'] and job['hung_told']
    assert capsys.readouterr().err.count('HUNG') == 1
    text = machine._describe(dict(job, started=time.time() - 1600, hung={'at': time.time(), 'cpu': 0.0}))
    assert 'HUNG' in text and 'ends it if it is stuck' in text
    for t in range(1560, 1900, 5):                             # it works again: one core busy
        w.see(float(t), 500.0 + (t - 1560), gpu_pct=0.0)
    assert not job.get('hung') and job['hung_told']
    quiet(w, 1900, 2300, cpu_s=500.0 + 340)                    # quiet again: on the board, not told twice
    assert job['hung']
    assert 'HUNG' not in capsys.readouterr().err


def test_a_slow_job_working_or_on_the_gpu_or_without_an_estimate_is_not_hung():
    busy = cpu_job(est_s=60.0, started=0.0)
    w = machine._Watch(busy, ncpu=8)
    for t in range(200, 1000, 5):
        w.see(float(t), t * 0.5, gpu_pct=0.0)                  # half a core: 6% of 8
    assert not busy.get('hung')
    on_gpu = cpu_job(kind='gpu', est_s=60.0, started=0.0)
    quiet(machine._Watch(on_gpu, ncpu=8), 200, 1000, gpu=60.0)
    assert not on_gpu.get('hung')
    no_est = cpu_job(started=0.0)
    quiet(machine._Watch(no_est, ncpu=8), 200, 1000)
    assert not no_est.get('hung')


def test_hung_needs_a_gpu_reading_unless_the_machine_has_no_gpu():
    unknown = cpu_job(est_s=60.0, started=0.0)
    quiet(machine._Watch(unknown, has_gpu=True, ncpu=8), 200, 1000, gpu=None)
    assert not unknown.get('hung')                             # no reading says: no flag
    whole_idle = cpu_job(est_s=60.0, started=0.0)
    quiet(machine._Watch(whole_idle, has_gpu=True, ncpu=8), 200, 1000, gpu=None, whole=1.0)
    assert whole_idle.get('hung')                              # the whole GPU idle: this job too
    whole_busy = cpu_job(est_s=60.0, started=0.0)
    quiet(machine._Watch(whole_busy, has_gpu=True, ncpu=8), 200, 1000, gpu=None, whole=60.0)
    assert not whole_busy.get('hung')                          # someone's GPU work: it cannot tell, so no flag
    no_gpu = cpu_job(est_s=60.0, started=0.0)
    quiet(machine._Watch(no_gpu, has_gpu=False, ncpu=8), 200, 1000, gpu=None)
    assert no_gpu.get('hung')


def test_a_broken_watch_never_stops_the_job(board, monkeypatch, capsys):
    monkeypatch.setattr(machine, '_GpuSampler', lambda: type('G', (), {'stop': lambda self: {}})())
    monkeypatch.setattr(machine, 'METER_S', 0.02)

    def boom(self, *a, **k):
        raise ZeroDivisionError('made up')

    monkeypatch.setattr(machine._Watch, 'see', boom)
    with machine.slot('cpu', 'a job', who='someone'):
        time.sleep(0.2)
    assert capsys.readouterr().err.count('the job watch stopped') == 1
    assert machine.history()[-1]['exit'] == 'ok'


# ------------------------------------------------------------------ M123 ORPHAN

class FakeProc:
    def __init__(self, pid, ppid, born, name='find.exe'):
        self.pid, self._ppid, self._born, self._name = pid, ppid, born, name

    def ppid(self):
        return self._ppid

    def create_time(self):
        return self._born

    def name(self):
        return self._name


def fake_table(monkeypatch, procs):
    def process(pid):
        if pid not in procs:
            raise machine.psutil.NoSuchProcess(pid)
        return procs[pid]
    monkeypatch.setattr(machine.psutil, 'Process', process)


def test_a_top_process_whose_parent_is_gone_is_an_orphan_with_its_age(monkeypatch):
    now = time.time()
    fake_table(monkeypatch, {
        10: FakeProc(10, 99, now - 3 * 3600),                  # parent 99 is gone
        11: FakeProc(11, 12, now - 600),                       # parent 12 lives, born before it
        12: FakeProc(12, 1, now - 7200, 'shell.exe'),
        13: FakeProc(13, 14, now - 600),                       # pid 14 was reused by a later process
        14: FakeProc(14, 1, now - 60, 'other.exe'),
        15: FakeProc(15, 99, now - 600, 'explorer.exe'),       # parentless by design
        16: FakeProc(16, 0, now - 600, 'top.exe'),
        17: FakeProc(17, 99, now - 5 * 3600 + 90, 'browser.exe'),   # started with the desktop, its launcher gone
    })
    monkeypatch.setattr(machine, '_desktop_start', lambda: now - 5 * 3600)
    assert 3 * 3600 - 5 < machine.orphan_age(10) < 3 * 3600 + 5
    assert machine.orphan_age(11) is None and machine.orphan_age(15) is None and machine.orphan_age(16) is None
    assert machine.orphan_age(13) is not None
    assert machine.orphan_age(404) is None                     # gone or unreadable: not an orphan
    assert machine.orphan_age(17) is None                      # a sign-in app, not a leftover
    monkeypatch.setattr(machine, '_desktop_start', lambda: None)
    assert machine.orphan_age(17) is not None
    text = machine._top_text([(45.0, 'find.exe', 10), (20.0, 'find.exe', 11)])
    assert text.startswith('find.exe 45% ORPHAN (parent gone, running 3.0 h)') and text.endswith('find.exe 20%')


def test_the_board_and_a_refusal_name_the_orphan(board, monkeypatch):
    monkeypatch.setattr(machine, 'cpu_load', lambda: (95.0, [(50.0, 'grep.exe', 7), (5.0, 'app.exe', 8)]))
    monkeypatch.setattr(machine, 'orphan_age', lambda pid: 75 * 60.0 if pid == 7 else None)
    assert 'grep.exe 50% ORPHAN (parent gone, running 75 min)' in machine.board()
    assert 'ORPHAN' in machine.check('cpu') and 'app.exe 5% ORPHAN' not in machine.check('cpu')


# ------------------------------------------------------------------ M126 the pagefile's drive

def test_the_pagefile_drive_warns_under_ten_gb_free_and_shows_its_free_space(board, monkeypatch):
    monkeypatch.setattr(machine, 'pagefiles', lambda: [('C:', 26.3)])
    free = {'C:': 40.0}
    monkeypatch.setattr(machine, 'free_gb', lambda d: free.get(d))
    line = machine.disk_line()
    assert 'pagefile C: 26.3 GB (40.0 GB free)' in line and 'PAGEFILE DRIVE LOW' not in line
    free['C:'] = 8.2
    line = machine.disk_line()
    assert 'PAGEFILE DRIVE LOW: C: has 8.2 GB free' in line and 'grows into C:' in line
    assert 'PAGEFILE DRIVE LOW' in machine.board() and 'PAGEFILE DRIVE LOW' in machine.pressure_line()
    assert machine.check('cpu') == ''                          # a warning, not a gate
    free.clear()                                               # unreadable: no flag
    assert 'PAGEFILE DRIVE LOW' not in machine.disk_line()


def test_a_pagefile_on_a_drive_the_floor_watches_is_left_to_the_floor(board, monkeypatch):
    monkeypatch.setattr(machine, 'pagefiles', lambda: [('D:', 30.0)])
    monkeypatch.setattr(machine, 'free_gb', lambda d: 5.0)
    assert machine.pagefile_low() == [] and 'pagefile D: 30.0 GB' in machine.disk_line()


# ------------------------------------------------------------------ M125 beyond songs/

def test_machine_disk_lists_the_folders_beyond_songs_with_their_daily_growth(board, tmp_path):
    songs, top = tmp_path / 'songs', tmp_path / 'drive'
    (songs / 'a_song').mkdir(parents=True)
    (top / 'videos' / 'launch').mkdir(parents=True)
    (top / 'videos' / 'launch' / 'cut.mp4').write_bytes(b'0' * 3 * 2 ** 20)
    (top / 'unpack').mkdir()
    (top / 'unpack' / 'x.bin').write_bytes(b'0' * 2 ** 20)
    roots = [str(top / 'videos'), str(top / 'unpack')]
    (board / 'disk' / 'roots').mkdir(parents=True)
    (board / 'disk' / 'roots' / '2000-01-01.json').write_text(json.dumps({roots[0]: 0, roots[1]: 2 ** 20}))
    text = machine.disk_text(root=str(songs), roots=roots)
    assert 'beyond songs/ (2 folders' in text and 'growth since 2000-01-01' in text
    assert any(l.strip().startswith('0.00 GB') and 'videos' in l for l in text.splitlines())
    assert any('unpack' in l and l.rstrip().endswith('same') for l in text.splitlines())
    assert sorted(os.listdir(board / 'disk' / 'roots')) == ['2000-01-01.json', time.strftime('%Y-%m-%d') + '.json',
                                                           'latest.json']
    # the walk is reused for a while; --fresh walks again
    (top / 'unpack' / 'y.bin').write_bytes(b'0' * 20 * 2 ** 20)
    again = machine._roots_measured(roots)[0]
    assert again[roots[1]] == 2 ** 20
    assert machine._roots_measured(roots, fresh=True)[0][roots[1]] == 21 * 2 ** 20
    assert 'beyond songs/' not in machine.disk_text(root=str(songs), roots=[])


def test_the_roots_come_from_the_environment_a_roots_file_or_beside_the_checkout(monkeypatch, tmp_path):
    a, b, board = tmp_path / 'one', tmp_path / 'two', tmp_path / 'board'
    a.mkdir(), b.mkdir()
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(board))
    monkeypatch.setenv('ISMAIL_DISK_ROOTS', os.pathsep.join([str(a), str(tmp_path / 'missing'), str(b)]))
    assert machine.disk_roots() == [str(a), str(b)]
    monkeypatch.delenv('ISMAIL_DISK_ROOTS')
    board.mkdir(parents=True, exist_ok=True)
    (board / 'roots.txt').write_text(f"# the big ones\n{b}\n\n{a}  # videos\n")
    assert machine.disk_roots() == [str(b), str(a)]
    os.remove(board / 'roots.txt')
    checkout = tmp_path / 'drive' / 'checkout'
    (checkout / 'songs').mkdir(parents=True)
    (tmp_path / 'drive' / 'games').mkdir()
    (tmp_path / 'drive' / '$RECYCLE.BIN').mkdir()
    (tmp_path / 'drive' / 'System Volume Information').mkdir()
    monkeypatch.setattr(machine, 'SONGS', str(checkout / 'songs'))
    assert machine.disk_roots() == [str(tmp_path / 'drive' / 'checkout'), str(tmp_path / 'drive' / 'games')]
