"""Listening together: record what plays on this computer (and a mic, only after the person's yes), mark moments
and typed messages on the same clock, then read it all back: where the music was skipped or changed, what each
stretch measures, and what was said, when.

One clock, many streams. Session time 0 is when the recorder opened (wall0 is the wall time of that moment). Every
audio stream records when its first sample was heard (t_start) and, as anchors, any moment its file fell behind
the clock (frames lost, or a loopback that delivers nothing while nothing plays), so a file time maps to session
time without touching the audio. Marks and notes carry wall times. A new kind of stream (MIDI, phone taps, stage
events) joins by writing events with wall times to events.jsonl.

The recorder and the reading run as background processes (python -m ismail.listen record|read <dir>), so the ops
return at once. A session folder holds:
  request.json   what listen_start asked for
  status.json    the recorder's state (streams, levels, dropouts, anchors), rewritten every 2 s, then the reading's
  <stream>.wav   one file per stream, 32-bit float, as heard
  events.jsonl   marks and typed notes, one JSON line each
  heard.jsonl    words as they were said (listen_heard), one line per utterance or failed chunk
  hear_<mic>.json  how the hearing goes (how far behind, failures, why there are no bars)
  heard/chunk.wav  the chunk the transcriber reads now (16 kHz, under 300 KB)
  news.jsonl     what the listen ops say once at their next reply (a stop because the drive is nearly full)
  tmp/           the background processes' temp folder (never the system drive's)
  rec.log, read.log, read.json, read.md, stretches/

Where sessions go: <project>/listen/ when listen_start names a project, else the running live set's folder, else
$ISMAIL_LISTEN_HOME, else <installed songs folder>/_listen (the checkout's drive). Never the system drive by default
(2026-10-13: a jam's session filled C:, and the speech server failed with it). A session id resolves across all of
these and the old home (~/.ismail/listen), through an index in the default home.

From songs/partisan_club/tools/session_rec.py (the Live DJ, 2026-10-13): the alignment, the speech chunks, the cut
detector and its thresholds (tuned on one 4:41 capture: 11 cuts, the skip rule needs beat strength 0.15 on both
sides) are kept. Changed here: loudness (LUFS, LRA, true peak) is measured in numpy (no ffmpeg), the per-stretch
key is dropped (it disagreed with itself), drift between mic and speakers is fitted from the start and the end,
utterances join across 2 s pauses, and the reading works in blocks so a 90-minute capture fits in memory.
"""
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf

from .handoffs import SONGS

DEFAULT_HOME = Path(SONGS) / '_listen'               # the installed songs folder: the checkout's drive
LEGACY_HOME = Path.home() / '.ismail' / 'listen'     # where sessions went before 2026-10-14: searched, never written
HOME = Path(os.environ.get('ISMAIL_LISTEN_HOME') or DEFAULT_HOME)
SPEAK = os.environ.get('ISMAIL_SPEAK') or 'http://127.0.0.1:8765'

MAX_MIN = 90            # speakers only
MIC_MAX_MIN = 20        # with a mic: a shorter default, then ask whether to go on (the DJ, 10-13)
HARD_MAX_MIN = 240
MIN_READ_S = 16         # a stretch shorter than this is listed, not read
UTTER_GAP_S = 2.0       # he thinks out loud with pauses: 1.2 s cut one thought into pieces
UTTER_BACK_S = 10.0     # an utterance is about the music up to 10 s before it began
TYPE_CPS = 4.0          # typed messages land late by about their length / 4 s
PEEK_S = 30
STATUS_EVERY_S = 2.0
ANCHOR_S = 0.05         # a file that falls this far behind the clock (rolling minimum) gets an anchor
PAUSE_GAP_S = 2.0       # silence this long inside the music is a pause, not a skip
FREE_FLOOR_GB = 2.0     # no session starts with less free on its drive, and a running one stops there (a stream
                        # takes about 1.4 GB an hour at 48 kHz stereo; the reading copies each stretch once more)
DISK_EVERY_S = 10.0
HEAR_SR = 16000         # what the transcriber gets: 16 kHz PCM16 keeps an 8 s chunk under 300 KB, small enough
                        # that the speech server never spools it to its own temp folder (the DJ's 400s, 10-13)
HEAR_MAX_S = 8.0        # a chunk is cut at a pause (UTTER_GAP_S) or at this length, at its quietest 50 ms
HEAR_QUIET_DB = -45.0   # a chunk with no 50 ms frame above this is not sent
HEAR_EVERY_S = 1.0
HEAR_LAG_EVERY_S = 60.0     # the mic's lag behind the speakers is measured again this often
HEAR_TIMEOUT_S = 60.0
HEAR_RETRY_S = 2.0


# ------------------------------------------------------------------ small helpers

def db(v):
    return 20 * np.log10(v + 1e-12)


def mmss(t):
    t = max(float(t), 0.0)
    return f'{int(t // 60)}:{t % 60:04.1f}'


def mono(x):
    return x.mean(1) if x.ndim > 1 else x


def resample(x, sr, to):
    from scipy.signal import resample_poly
    g = gcd(int(sr), int(to))
    return resample_poly(x, int(to) // g, int(sr) // g).astype(np.float32)


def slug(spec):
    return re.sub(r'[^a-z0-9]+', '_', spec.lower()).strip('_')


def is_mic(spec):
    return spec.split(':', 1)[0].strip().lower() == 'mic'


def check_spec(spec):
    kind = spec.split(':', 1)[0].strip().lower()
    if kind not in ('speakers', 'loop', 'mic'):
        raise ValueError(f"unknown stream {spec!r}: use 'speakers', 'loop:<device name part>', 'mic' or "
                         "'mic:<device name part>' (listen_list(devices=True) names them)")
    if kind == 'loop' and ':' not in spec:
        raise ValueError("'loop' needs a device name part: 'loop:headphones'")
    return spec.strip()


def write_json(path, d):
    """Atomic, so a reader in another process never sees half a file (retried: Windows refuses a replace while
    the target is open for a moment)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(d, indent=1), encoding='utf8')
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, path)


def read_json(path, default=None):
    for _ in range(20):
        try:
            return json.loads(Path(path).read_text(encoding='utf8'))
        except FileNotFoundError:
            return default
        except (ValueError, PermissionError):
            time.sleep(0.02)
    return default


def events(out):
    p = Path(out) / 'events.jsonl'
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding='utf8').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    return rows


def add_event(out, ev):
    with open(Path(out) / 'events.jsonl', 'a', encoding='utf8') as f:
        f.write(json.dumps(ev) + '\n')


def add_news(out, text, **kw):
    """A line the listen ops say once, at their next reply (the recorder runs alone: this is how it speaks)."""
    with open(Path(out) / 'news.jsonl', 'a', encoding='utf8') as f:
        f.write(json.dumps({'wall': time.time(), 'text': text, **kw}) + '\n')


def take_news(out):
    """The news lines not said yet, marked said."""
    p = Path(out) / 'news.jsonl'
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding='utf8').splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            pass
    said = Path(out) / 'news.said'
    try:
        k = int(said.read_text())
    except (OSError, ValueError):
        k = 0
    if len(rows) > k:
        said.write_text(str(len(rows)))
    return rows[k:]


# ------------------------------------------------------------------ where sessions live

def home():
    """Where a session goes when no project is given and no live set runs: $ISMAIL_LISTEN_HOME (read at each call,
    so setting it needs no restart), else HOME."""
    env = os.environ.get('ISMAIL_LISTEN_HOME')
    return Path(env) if env else HOME


def free_gb(path):
    p = Path(os.path.abspath(path))
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free / 2 ** 30


def disk_short(path):
    """None, or what is short when path's drive has less than FREE_FLOOR_GB free."""
    try:
        free = free_gb(path)
    except OSError:
        return None
    if free >= FREE_FLOOR_GB:
        return None
    drive = os.path.splitdrive(os.path.abspath(path))[0] or str(path)
    return f"only {free:.1f} GB free on {drive} (for {path})"


def live_engines():
    """The live engines running now, newest first: each one's engine.json record ('project', 'port', 'bpm', ...).
    Every engine leaves a note in the live registry (ismail/live/engine.py registry_path, read here without
    importing the engine)."""
    reg = Path(os.environ.get('ISMAIL_LIVE_REGISTRY') or Path.home() / '.ismail' / 'live')
    try:
        files = list(reg.glob('*.json'))
    except OSError:
        return []
    import psutil
    out = []
    for f in files:
        rec = read_json(f) or {}
        try:
            alive = psutil.pid_exists(int(rec.get('pid')))
        except (TypeError, ValueError):
            alive = False
        if alive and rec.get('project') and (Path(rec['project']) / 'live' / 'engine.json').exists():
            out.append(rec)
    return sorted(out, key=lambda r: -float(r.get('started') or 0))


def pick_home(project=None):
    """-> (the folder a new session goes in, the project whose live engine gives the bars or None, why there)."""
    eng = live_engines()
    if project:
        p = Path(project).expanduser()
        if not p.is_dir():
            raise ValueError(f"project {project!r}: no such folder. Give the song's own folder (the installed songs "
                             f"folder is {SONGS})")
        p = p.resolve()
        mine = [e['project'] for e in eng if Path(e['project']).resolve() == p]
        return p / 'listen', (mine or [e['project'] for e in eng] or [None])[0], 'the project folder'
    if eng:
        return Path(eng[0]['project']) / 'listen', eng[0]['project'], "the running live set's folder"
    if os.environ.get('ISMAIL_LISTEN_HOME'):
        return home(), None, '$ISMAIL_LISTEN_HOME'
    return home(), None, 'the listening home'


def remember(sid, d):
    """Index a new session by its id in the default home, so the id resolves wherever the session went."""
    try:
        DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
        with open(DEFAULT_HOME / 'index.jsonl', 'a', encoding='utf8') as f:
            f.write(json.dumps({'id': sid, 'path': str(Path(d).resolve())}) + '\n')
    except OSError:
        pass


def known_homes():
    out = []
    for h in [home(), HOME, DEFAULT_HOME, LEGACY_HOME] + [Path(e['project']) / 'listen' for e in live_engines()]:
        if h not in out:
            out.append(h)
    return out


def _indexed():
    p = DEFAULT_HOME / 'index.jsonl'
    out = []
    if p.exists():
        for line in p.read_text(encoding='utf8').splitlines():
            try:
                r = json.loads(line)
                out.append((r['id'], Path(r['path'])))
            except (ValueError, KeyError):
                pass
    return out


def session_dirs():
    """Every session folder this machine knows of: the indexed ones and those in the known homes."""
    seen, out = set(), []
    cands = [p for _, p in _indexed()]
    for h in known_homes():
        if h.is_dir():
            cands += list(h.iterdir())
    for p in cands:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen and (p / 'request.json').exists():
            seen.add(k)
            out.append(p)
    return out


def find_session(session):
    """A session folder from its id or its path, wherever it went; None if there is none."""
    p = Path(session)
    if (p / 'request.json').exists():
        return p
    for sid, q in reversed(_indexed()):
        if sid == session and (q / 'request.json').exists():
            return q
    for h in known_homes():
        if (h / session / 'request.json').exists():
            return h / session
    return None


# ------------------------------------------------------------------ the clock

def to_session(st, ft):
    """Session time of a stream's file time: t_start + ft + how far the file had fallen behind by then."""
    late = 0.0
    for a_ft, a_late in st.get('anchors') or []:
        if a_ft <= ft:
            late = a_late
        else:
            break
    return st['t_start'] + ft + late


def to_file(st, s):
    """A stream's file time at session time s (inside a stretch it did not hear, the point where it resumed)."""
    anchors = [(0.0, 0.0)] + [tuple(a) for a in st.get('anchors') or []]
    for k, (a_ft, a_late) in enumerate(anchors):
        nxt = anchors[k + 1][0] if k + 1 < len(anchors) else float('inf')
        ft = s - st['t_start'] - a_late
        if ft < a_ft:
            return max(0.0, a_ft)
        if ft < nxt:
            return ft
    return max(0.0, s - st['t_start'] - anchors[-1][1])


def wall_to_session(status, wall):
    return wall - status['wall0']


# ------------------------------------------------------------------ devices (Windows WASAPI, pyaudiowpatch)

def _pa():
    try:
        import pyaudiowpatch as pa
    except ImportError:
        raise RuntimeError(
            "recording what this computer plays needs pyaudiowpatch, which works on Windows only: "
            "pip install pyaudiowpatch (or pip install 'ismail[listen]'). On macOS and Linux there is no "
            "speaker capture here yet; a reading of a file you already have works anywhere.")
    return pa


def wasapi_devices(p):
    pa = _pa()
    hi = p.get_host_api_info_by_type(pa.paWASAPI)['index']
    return list(p.get_device_info_generator_by_host_api(host_api_index=hi))


def pick(p, spec):
    devs = wasapi_devices(p)
    kind, _, part = spec.partition(':')
    kind, part = kind.strip().lower(), part.strip().lower()
    if kind == 'speakers':
        return p.get_default_wasapi_loopback()
    if kind == 'loop':
        c = [d for d in devs if d.get('isLoopbackDevice') and part in d['name'].lower()]
    else:
        ins = [d for d in devs if d['maxInputChannels'] > 0 and not d.get('isLoopbackDevice')]
        if not part:
            try:
                return p.get_default_wasapi_device(d_in=True)
            except Exception:
                if not ins:
                    raise RuntimeError('no microphone found')
                return ins[0]
        c = [d for d in ins if part in d['name'].lower()]
    if not c:
        raise RuntimeError(f"no device matches {spec!r}: listen_list(devices=True) names them")
    return c[0]


def devices():
    pa = _pa()
    p = pa.PyAudio()
    try:
        L = [f"speakers (default): {p.get_default_wasapi_loopback()['name']}"]
        for d in wasapi_devices(p):
            if d['maxInputChannels'] > 0:
                k = 'loop' if d.get('isLoopbackDevice') else 'mic'
                L.append(f"{k}: {d['name']}  ({int(d['defaultSampleRate'])} Hz, {d['maxInputChannels']} ch)")
        return L
    finally:
        p.terminate()


# ------------------------------------------------------------------ recording (the background process)

def record(out):
    out = Path(out)
    req = read_json(out / 'request.json')
    specs, max_s = req['streams'], float(req['max_s'])
    stop = out / 'stop'
    pa = _pa()
    p = pa.PyAudio()
    t0 = time.perf_counter()
    wall0 = time.time()
    status = {'state': 'starting', 'pid': os.getpid(), 'wall0': wall0, 'date': time.strftime('%Y-%m-%d %H:%M:%S'),
              'max_s': max_s, 'mic': any(is_mic(s) for s in specs), 'label': req.get('label') or '',
              'streams': [], 'levels': {}, 'elapsed_s': 0.0}
    streams, threads = [], []
    lock = threading.Lock()
    hear_stop, hear_th = threading.Event(), None

    def run(st):
        d, sr, ch = st['dev'], st['sr'], st['ch']
        try:
            s = p.open(format=pa.paFloat32, channels=ch, rate=sr, input=True, input_device_index=d['index'],
                       frames_per_buffer=1024)
        except Exception as e:
            st['error'] = f'{type(e).__name__}: {e}'
            return
        recent = deque()                                   # (time, late): the rolling minimum filters read jitter
        with sf.SoundFile(out / st['file'], 'w', samplerate=sr, channels=ch, subtype='FLOAT') as f:
            first = True
            while not st['halt'].is_set():
                x = np.frombuffer(s.read(1024, exception_on_overflow=False), np.float32).reshape(-1, ch)
                now = time.perf_counter() - t0
                if first:          # the first buffer's first sample was heard one buffer before read returned
                    st['t_start'] = round(now - len(x) / sr, 4)
                    first = False
                f.write(x)
                st['frames'] += len(x)
                late = now - st['t_start'] - st['frames'] / sr
                recent.append((now, late))
                while recent and recent[0][0] < now - 2.0:
                    recent.popleft()
                floor = min(v for _, v in recent)
                if abs(floor - st['late']) > ANCHOR_S and now - st['t_start'] > 2.0:
                    ft = round(st['frames'] / sr, 3)
                    if floor > st['late']:
                        st['dropouts'].append([ft, round((floor - st['late']) * 1000)])
                    st['anchors'].append([ft, round(floor, 4)])
                    st['late'] = floor
                    recent.clear()
                with lock:
                    st['lv'].append((now, float((x ** 2).mean()), float(np.abs(x).max()) if len(x) else 0.0))
                    if st['ring'] is not None:
                        st['ring'].append(x.copy())
                        st['ring_n'] += len(x)
                        while st['ring_n'] - len(st['ring'][0]) >= PEEK_S * sr:
                            st['ring_n'] -= len(st['ring'].popleft())
        s.close()

    try:
        for spec in specs:
            d = pick(p, spec)
            name = slug(spec)
            st = {'name': name, 'spec': spec, 'device': d['name'], 'dev': d, 'sr': int(d['defaultSampleRate']),
                  'ch': int(d['maxInputChannels']), 'file': name + '.wav', 'frames': 0, 'halt': threading.Event(),
                  'loopback': bool(d.get('isLoopbackDevice')), 'dropouts': [], 'anchors': [], 'late': 0.0,
                  't_start': None, 'lv': deque(), 'ring': None, 'ring_n': 0}
            streams.append(st)
        ref = next((s for s in streams if s['loopback']), streams[0])
        ref['ring'] = deque()
        for st in streams:
            th = threading.Thread(target=run, args=(st,), daemon=True)
            th.start()
            threads.append(th)
            print(f"recording {st['name']}: {st['device']} {st['sr']} Hz {st['ch']} ch -> {out / st['file']}",
                  flush=True)
        hearers = [Hearer(out, st, ref if ref['loopback'] and ref is not st else None, wall0, req.get('live'))
                   for st in streams if is_mic(st['spec'])]

        def hear():                                        # words as they are said, one mic after another
            while True:
                last = hear_stop.wait(HEAR_EVERY_S)
                for h in hearers:
                    try:
                        h.step(final=last)
                    except Exception as e:
                        print(f"hearing {h.mic['name']}: {type(e).__name__}: {e}", flush=True)
                if last:
                    return
        if hearers:
            hear_th = threading.Thread(target=hear, daemon=True)
            hear_th.start()

        def snapshot(state):
            now = time.perf_counter() - t0
            lv = {}
            with lock:
                for st in streams:
                    while st['lv'] and st['lv'][0][0] < now - 10:
                        st['lv'].popleft()
                    if st['lv']:
                        lv[st['name']] = {'rms_db': round(float(db(np.sqrt(np.mean([v[1] for v in st['lv']])))), 1),
                                          'peak_db': round(float(db(max(v[2] for v in st['lv']))), 1)}
            status.update(state=state, elapsed_s=round(now, 1), levels=lv, streams=[
                {k: v for k, v in st.items() if k not in ('dev', 'halt', 'lv', 'ring', 'ring_n', 'late')}
                for st in streams])
            write_json(out / 'status.json', status)

        why = 'max'
        nxt = disk_at = 0.0
        while time.perf_counter() - t0 < max_s:
            if stop.exists():
                why = 'asked'
                break
            bad = [st for st in streams if st.get('error')]
            if bad:
                why = 'error'
                status['error'] = '; '.join(f"{st['name']}: {st['error']}" for st in bad)
                break
            if (out / 'peek.req').exists():
                with lock:
                    x = np.concatenate(list(ref['ring'])) if ref['ring'] else np.zeros((0, ref['ch']), np.float32)
                sf.write(out / 'peek.wav', x, ref['sr'], subtype='FLOAT')
                (out / 'peek.req').unlink()
            el = time.perf_counter() - t0
            if el >= nxt:
                started = all(st['t_start'] is not None for st in streams)
                snapshot('recording' if started else 'starting')
                nxt = el + STATUS_EVERY_S
            if el >= disk_at:
                disk_at = el + DISK_EVERY_S
                short = disk_short(out)
                if short:
                    why, status['disk'] = 'disk', short
                    break
            time.sleep(0.1)
    except KeyboardInterrupt:
        why = 'asked'
    except Exception as e:
        why = 'error'
        status['error'] = f'{type(e).__name__}: {e}'
    for st in streams:
        st['halt'].set()
    for th in threads:
        th.join(timeout=5)
    p.terminate()
    status['stopped_at_s'] = round(time.perf_counter() - t0, 2)
    status['stop_why'] = why
    for st in streams:
        st['ring'] = None
    snapshot('failed' if why == 'error' else 'stopped')
    print(f"stopped at {status['stopped_at_s']} s ({why})", flush=True)
    if why == 'disk':
        add_news(out, f"Listening session {out.name} stopped by itself at {mmss(status['stopped_at_s'])}: "
                      f"{status['disk']}, under the {FREE_FLOOR_GB:g} GB it keeps free so the drive never fills. What "
                      f"it recorded is kept in {out}. Free some space, then listen_stop('{out.name}') reads it.",
                 mic=status['mic'])
    hear_stop.set()
    if hear_th is not None:
        hear_th.join(timeout=60)                           # the last words; the reading hears them all anyway
    return status


# ------------------------------------------------------------------ alignment (mic against the speakers)

def _band4k(x, sr):
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, [200, 1800], 'band', fs=4000, output='sos')
    return sosfiltfilt(sos, resample(mono(x), sr, 4000))


def residual_at(ref_path, ref_st, mic_path, mic_st, s0, dur, search=0.5):
    """How late the mic hears the speakers at session time s0, beyond what the clock says (s), and the
    correlation's strength. The mic hears the music the person heard, so a word moves back by this much."""
    fs = 4000
    rsr, msr = ref_st['sr'], mic_st['sr']
    rf = to_file(ref_st, s0)
    a, _ = sf.read(ref_path, start=int(rf * rsr), stop=int((rf + dur) * rsr), dtype='float32', always_2d=True)
    mf = to_file(mic_st, s0) - search
    b, _ = sf.read(mic_path, start=max(0, int(mf * msr)), stop=int((mf + dur + 2 * search) * msr),
                   dtype='float32', always_2d=True)
    if len(a) < rsr * 5 or len(b) < msr * 5:
        return None, 0.0
    a, b = _band4k(a, rsr), _band4k(b, msr)
    pad = int(round(-min(0.0, mf) * fs))                     # the mic file began after s0 - search
    b = np.r_[np.zeros(pad), b]
    want = len(a) + int(2 * search * fs)                      # the mic file may end before the window does
    if len(b) < want:
        b = np.r_[b, np.zeros(want - len(b))]
    from scipy.signal import correlate
    c = correlate(b, a, mode='valid', method='fft')
    k = int(np.argmax(np.abs(c)))
    seg = b[k:k + len(a)]
    strength = float(np.abs(c[k]) / (np.sqrt((a ** 2).sum() * (seg ** 2).sum()) + 1e-12))
    return k / fs - search, strength


def fit_residual(ref_path, ref_st, mic_path, mic_st, dur=30.0):
    """The mic's lag, measured near the start and near the end, as a line over session time (the two clocks
    drift apart over a long session). 30 s windows: two devices' clocks drift some 10 to 50 ppm apart, and a
    window much longer than this smears the match. -> (fn(s) -> lag, a description)."""
    s_lo = max(ref_st['t_start'], mic_st['t_start'])
    s_hi = min(to_session(ref_st, ref_st['frames'] / ref_st['sr']), to_session(mic_st, mic_st['frames'] / mic_st['sr']))
    span = s_hi - s_lo
    if span < 5:
        return (lambda s: 0.0), {'how': 'clock (too short to line up)', 'lag_s': 0.0, 'strength': 0.0}
    w = min(dur, span)
    pts = []
    for s0 in ([s_lo] if span < 2.5 * w else [s_lo, s_hi - w]):
        r, strength = residual_at(ref_path, ref_st, mic_path, mic_st, s0, w)
        if r is not None and strength >= 0.05:
            pts.append((s0 + w / 2, r, strength))
    if not pts:
        return (lambda s: 0.0), {'how': 'clock (the mic does not hear the music clearly enough)', 'lag_s': 0.0,
                                 'strength': 0.0}
    if len(pts) == 1:
        r = pts[0][1]
        return (lambda s: r), {'how': 'correlation', 'lag_s': round(r, 3), 'strength': round(pts[0][2], 3)}
    (c0, r0, k0), (c1, r1, k1) = pts
    slope = (r1 - r0) / (c1 - c0)
    return ((lambda s: r0 + slope * (s - c0)),
            {'how': 'correlation at the start and the end', 'lag_s': round(r0, 3), 'lag_end_s': round(r1, 3),
             'drift_ms_per_10min': round(slope * 600 * 1000, 1), 'strength': round(min(k0, k1), 3)})


# ------------------------------------------------------------------ speech

def stt_words(path, chunk_s=120, overlap_s=2, timeout=900):
    """Words with times (s, in the file), from the speech server, chunk by chunk."""
    info = sf.info(str(path))
    sr, total = info.samplerate, info.frames / info.samplerate
    words = []
    step = chunk_s - overlap_s
    for k, s0 in enumerate(np.arange(0, max(total, 0.01), step)):
        seg, _ = sf.read(path, start=int(s0 * sr), stop=int((s0 + chunk_s) * sr), dtype='float32', always_2d=True)
        seg = mono(seg)
        if len(seg) < sr * 0.5:
            continue
        bio = io.BytesIO()
        sf.write(bio, seg, sr, format='WAV')
        b = '----listen' + str(int(time.time() * 1000))

        def part(n, v):
            return f'--{b}\r\nContent-Disposition: form-data; name="{n}"\r\n\r\n{v}\r\n'.encode()
        body = (part('response_format', 'verbose_json') + part('timestamp_granularities[]', 'word')
                + f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="c{k}.wav"\r\n'
                  f'Content-Type: application/octet-stream\r\n\r\n'.encode() + bio.getvalue()
                + f'\r\n--{b}--\r\n'.encode())
        req = urllib.request.Request(SPEAK + '/v1/audio/transcriptions', data=body, method='POST',
                                     headers={'Content-Type': f'multipart/form-data; boundary={b}'})
        r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        for w in r.get('words') or []:
            t = s0 + w['start']
            if k and w['start'] < overlap_s / 2:          # said in the overlap: the previous chunk has it
                continue
            if t >= s0 + chunk_s - overlap_s / 2 and s0 + step < total:
                continue
            words.append({'t': round(float(t), 2), 'end': round(float(s0 + w['end']), 2),
                          'word': str(w['word']).strip(), 'p': round(float(w.get('probability', 1.0)), 2)})
    words.sort(key=lambda w: w['t'])
    return words


def utterances(words, gap=UTTER_GAP_S):
    out = []
    for w in words:
        if out and w['t'] - out[-1]['end'] <= gap:
            out[-1]['text'] += ' ' + w['word']
            out[-1]['end'] = w['end']
        else:
            out.append({'t': w['t'], 'end': w['end'], 'text': w['word']})
    return out


_NUM = r'(\d{1,3})(?:\s*(?:s|sec|secs|second|seconds))?'


def said_times(text):
    """Track times said out loud ("starts at 18", "around 38s", "at 1:20"), in seconds: times in the TRACK, which
    the capture's clock cannot know, so they are shown beside the stamp, never instead of it."""
    out = []
    for m in re.finditer(r'\b(\d{1,2}):(\d{2})\b', text):
        out.append(int(m.group(1)) * 60 + int(m.group(2)))
    for m in re.finditer(r'\b(?:at|around|about|from|by)\s+' + _NUM + r'\b', text, re.I):
        v = int(m.group(1))
        if v not in out and not re.match(r'\d{1,2}:\d{2}', text[m.start(1):m.start(1) + 5]):
            out.append(v)
    return out


# ------------------------------------------------------------------ words as they are said (listen_heard)

def speech_up(timeout=1.0):
    """Whether the speech server answers at all (any HTTP answer counts; nothing is started here)."""
    try:
        urllib.request.urlopen(SPEAK, timeout=timeout)
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False
    return True


def stt_error(e):
    """-> (kind, words for the agent): 'down' when the speech server does not answer, else 'failed'."""
    if isinstance(e, urllib.error.HTTPError):
        hint = (' (the DJ saw 400 and 500 here while the system drive was full: the server spools to its temp '
                'folder; machine_status shows free space)' if e.code in (400, 500) else '')
        return 'failed', f"the speech server answered {e.code} {e.reason}{hint}"
    if isinstance(e, (urllib.error.URLError, ConnectionError)):
        return 'down', f"the speech server at {SPEAK} does not answer ({getattr(e, 'reason', e)})"
    if isinstance(e, TimeoutError):
        return 'failed', f"the speech server took over {HEAR_TIMEOUT_S:.0f} s"
    return 'failed', f'{type(e).__name__}: {e}'


def engine_clock(project):
    """-> ({'wall', 'beat', 'bpm', 'bpb'}, None): the live engine's heard beat at a wall time (the beat its keys
    log uses), or (None, why there is none). Only an engine whose engine.json says 'clock' is asked: the call
    is exempt from the engine's news, which an older engine would hand to this caller instead of the agent."""
    if not project:
        return None, 'no live set runs'
    rec = read_json(Path(project) / 'live' / 'engine.json')
    if not rec:
        return None, 'no live set runs'
    if not rec.get('clock'):
        return None, 'the live engine running is older than listen_heard; bars come once it is restarted'
    req = urllib.request.Request(f"http://127.0.0.1:{rec['port']}/", headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'op': 'clock', 'args': {}}).encode())
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=2).read())
    except Exception as e:
        return None, f'the live engine does not answer ({e})'
    if not res.get('ok'):
        return None, f"the live engine says: {res.get('error')}"
    return res['result'], None


def newest_clock():
    eng = live_engines()
    return engine_clock(eng[0]['project']) if eng else (None, 'no live set runs')


class Hearer:
    """Words as they are said, from one mic of a running session: its new audio is cut at a pause (UTTER_GAP_S) or
    at HEAR_MAX_S, sent to the transcriber the reading uses (stt_words, the speech server), and each utterance is
    placed as the reading places words: the mic file's time on the session clock, less the mic's lag behind the
    speakers, and, when a live set runs, the bar the engine had sounding then. A chunk that fails is tried once
    more, then written as a failed line: never dropped. Lines go to heard.jsonl; the state to hear_<mic>.json.
    stt, clock: stand-ins for tests (path -> words; () -> (clock, why))."""

    def __init__(self, out, mic, ref=None, wall0=0.0, live=None, stt=None, clock=None, retry_s=HEAR_RETRY_S):
        self.out, self.mic, self.ref, self.wall0 = Path(out), mic, ref, float(wall0)
        self.stt = stt or (lambda p: stt_words(p, timeout=HEAR_TIMEOUT_S))
        self.clock = clock or ((lambda: engine_clock(live)) if live else newest_clock)
        self.retry_s = retry_s
        self.pos = 0                                       # mic file frames read so far
        self.buf, self.buf_ft = np.zeros(0, np.float32), 0.0   # 16 kHz mono not cut yet, and its file time
        self.floor = deque(maxlen=int(30 / 0.05))          # 50 ms levels of the last 30 s cut: the room's floor
        self.lag, self.lag_at = 0.0, -1e18
        (self.out / 'heard').mkdir(exist_ok=True)
        self.state = {'who': mic['name'], 'heard_to_s': None, 'chunks': 0, 'failed': 0, 'last_ok': None,
                      'last_error': None, 'last_error_kind': None, 'last_error_wall': None, 'lag_s': 0.0,
                      'bars': None, 'updated': None}

    def _pull(self):
        n = int(self.mic.get('frames') or 0)
        if n <= self.pos:
            return
        try:
            x, sr = sf.read(self.out / self.mic['file'], start=self.pos, stop=n, dtype='float32', always_2d=True)
        except Exception:                                  # mid-write: next time
            return
        if not len(x):
            return
        self.pos += len(x)
        y = mono(x)
        y = y if sr == HEAR_SR else resample(y, sr, HEAR_SR)
        self.buf = np.r_[self.buf, y.astype(np.float32)]
        self.buf_ft = self.pos / sr - len(self.buf) / HEAR_SR     # anchored to the file each time: no creep

    def _cut(self, final):
        """-> (samples of buf to take now, whether they hold sound): at the first pause after sound, at HEAR_MAX_S
        (its quietest frame in the last 2 s), or everything when final. Leading quiet is dropped unsent."""
        F = int(HEAR_SR * 0.05)
        nf = len(self.buf) // F
        if nf == 0:
            return (len(self.buf), False) if final and len(self.buf) else (0, False)
        lv = db(np.sqrt((self.buf[:nf * F].reshape(nf, F) ** 2).mean(1)))
        ref = np.r_[np.array(self.floor), lv]
        loud = lv > max(HEAR_QUIET_DB, float(np.percentile(ref, 20)) + 8.0)
        gap = int(round(UTTER_GAP_S / 0.05))
        seen, run = False, 0
        mx = int(HEAR_MAX_S / 0.05)
        for i, v in enumerate(loud[:mx]):
            if v:
                seen, run = True, 0
                continue
            run += 1
            if run >= gap:
                if seen:
                    return (i - gap // 2 + 1) * F, True    # the middle of the pause
                if i + 1 - gap // 2 > 0:
                    return (i + 1 - gap // 2) * F, False   # only quiet so far: drop it, keep 1 s of lead-in
        if nf >= mx:
            k = mx - int(2 / 0.05) + int(np.argmin(lv[mx - int(2 / 0.05):mx])) + 1
            return k * F, bool(loud[:k].any())
        if final:
            return len(self.buf), bool(loud.any())
        return 0, False

    def step(self, final=False):
        """Cut and hear what is ready; -> how many chunks were sent."""
        if self.mic.get('t_start') is None:
            return 0
        self._pull()
        sent = 0
        while True:
            k, sound = self._cut(final)
            if k <= 0:
                break
            seg, ft0 = self.buf[:k], self.buf_ft
            self.buf, self.buf_ft = self.buf[k:], self.buf_ft + k / HEAR_SR
            F = int(HEAR_SR * 0.05)
            if len(seg) >= F:
                self.floor.extend(db(np.sqrt((seg[:len(seg) // F * F].reshape(-1, F) ** 2).mean(1))))
            if sound:
                self._hear(seg, ft0)
                sent += 1
        if self.pos:
            self.state['heard_to_s'] = round(to_session(self.mic, self.buf_ft), 2)
        self.state['updated'] = time.time()
        write_json(self.out / f"hear_{self.mic['name']}.json", self.state)
        return sent

    def _measure_lag(self):
        """The mic's lag behind the speakers over the last 30 s, as fit_residual measures it at each end."""
        if self.ref is None or time.time() - self.lag_at < HEAR_LAG_EVERY_S:
            return
        r, m = self.ref, self.mic
        lo = max(r['t_start'], m['t_start'])
        hi = min(to_session(r, r['frames'] / r['sr']), to_session(m, m['frames'] / m['sr']))
        if hi - lo < 10:
            return
        self.lag_at = time.time()
        w = min(30.0, hi - lo)
        try:
            lag, strength = residual_at(self.out / r['file'], r, self.out / m['file'], m, hi - w, w)
        except Exception:
            return
        if lag is not None and strength >= 0.05:
            self.lag = lag
            self.state['lag_s'] = round(lag, 3)

    def _hear(self, seg, ft0):
        path = self.out / 'heard' / 'chunk.wav'
        sf.write(path, seg, HEAR_SR, subtype='PCM_16')
        s0 = to_session(self.mic, ft0)
        s1 = to_session(self.mic, ft0 + len(seg) / HEAR_SR)
        self.state['chunks'] += 1
        err = None
        for attempt in (0, 1):
            try:
                words = self.stt(path)
                err = None
                break
            except Exception as e:
                err = e
                if attempt == 0:
                    time.sleep(self.retry_s)
        if err is not None:
            kind, why = stt_error(err)
            self.state.update(failed=self.state['failed'] + 1, last_error=why, last_error_kind=kind,
                              last_error_wall=time.time())
            self._add({'t': round(s0, 2), 'end': round(s1, 2), 'failed': True, 'error': why, 'kind': kind})
            return
        self.state['last_ok'] = time.time()
        self._measure_lag()
        for w in words:
            for k in ('t', 'end'):
                w[k] = round(to_session(self.mic, ft0 + float(w[k])) - self.lag, 2)
        clock, why = self.clock()
        self.state['bars'] = why
        for u in utterances(words):
            line = {'t': u['t'], 'end': u['end'], 'text': u['text']}
            if clock:
                beat = clock['beat'] + (self.wall0 + u['t'] - clock['wall']) * clock['bpm'] / 60.0
                line['bar'] = round(beat / clock['bpb'] + 1, 1)
            self._add(line)

    def _add(self, line):
        p = self.out / 'heard.jsonl'
        n = len(p.read_text(encoding='utf8').splitlines()) if p.exists() else 0
        line = {'n': n + 1, 'who': self.mic['name'], 'wall': round(self.wall0 + line['t'], 2), **line}
        with open(p, 'a', encoding='utf8') as f:
            f.write(json.dumps(line) + '\n')


def heard_lines(out):
    p = Path(out) / 'heard.jsonl'
    rows = []
    if p.exists():
        for line in p.read_text(encoding='utf8').splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def hear_states(out):
    return [s for s in (read_json(p) for p in sorted(Path(out).glob('hear_*.json'))) if s]


# ------------------------------------------------------------------ loudness (EBU R128) in numpy

def _kweight(sr):
    """BS.1770 K-weighting at any rate (libebur128's two biquads)."""
    f0, G, Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    K = np.tan(np.pi * f0 / sr)
    Vh = 10 ** (G / 20)
    Vb = Vh ** 0.4996667741545416
    a0 = 1 + K / Q + K * K
    b1 = np.array([Vh + Vb * K / Q + K * K, 2 * (K * K - Vh), Vh - Vb * K / Q + K * K]) / a0
    a1 = np.array([1, 2 * (K * K - 1) / a0, (1 - K / Q + K * K) / a0])
    f0, Q = 38.13547087602444, 0.5003270373238773
    K = np.tan(np.pi * f0 / sr)
    d = 1 + K / Q + K * K
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1, 2 * (K * K - 1) / d, (1 - K / Q + K * K) / d])
    return (b1, a1), (b2, a2)


class Loudness:
    """Integrated loudness (LUFS), loudness range (LRA, LU) and true peak (dBTP, 4x oversampled), fed in blocks."""

    def __init__(self, sr, ch):
        from scipy.signal import lfilter_zi
        self.sr, self.ch = sr, ch
        self.f = _kweight(sr)
        self.zi = [[lfilter_zi(b, a) * 0 for b, a in self.f] for _ in range(ch)]
        self.blk = int(round(sr * 0.1))
        self.carry = np.zeros(0)
        self.e = []                                       # mean square per 100 ms block, channels summed
        self.tp = 0.0

    def feed(self, x):
        from scipy.signal import lfilter, resample_poly
        x = np.asarray(x, np.float64)
        if x.ndim == 1:
            x = x[:, None]
        y = np.zeros(len(x))
        for c in range(x.shape[1]):
            v = x[:, c]
            for i, (b, a) in enumerate(self.f):
                v, self.zi[c][i] = lfilter(b, a, v, zi=self.zi[c][i])
            y += v * v
            if len(x):
                self.tp = max(self.tp, float(np.abs(resample_poly(x[:, c], 4, 1)).max()))
        y = np.r_[self.carry, y]
        n = len(y) // self.blk
        if n:
            self.e.extend(y[:n * self.blk].reshape(n, self.blk).mean(1))
        self.carry = y[n * self.blk:]

    def result(self):
        e = np.array(self.e)
        lk = lambda m: -0.691 + 10 * np.log10(m + 1e-20)   # noqa: E731
        out = {'lufs': None, 'lra': None, 'true_peak': round(float(db(self.tp)), 1)}
        if len(e) >= 4:
            m = np.convolve(e, np.ones(4) / 4, 'valid')    # 400 ms blocks, 75 % overlap
            g = m[lk(m) > -70]
            if len(g):
                g = g[lk(g) > lk(g.mean()) - 10]
                out['lufs'] = round(float(lk(g.mean())), 1)
        if len(e) >= 30:
            s = np.convolve(e, np.ones(30) / 30, 'valid')  # 3 s short-term, 100 ms hop
            g = s[lk(s) > -70]
            if len(g):
                g = lk(g[lk(g) > lk(g.mean()) - 20])
                out['lra'] = round(float(np.percentile(g, 95) - np.percentile(g, 10)), 1)
        return out


# ------------------------------------------------------------------ cuts in the music

def features(path, start=0.0, stop=None, fs=22050, hop_s=0.02, win_s=0.04, block_s=60.0):
    """Per 20 ms frame: 40 log bands and the level (dB), at 22.05 kHz, read in blocks; plus the clicks (s), found at
    the file's own rate."""
    info = sf.info(str(path))
    sr = info.samplerate
    stop = info.frames / sr if stop is None else stop
    hop, win = int(fs * hop_s), int(fs * win_s)
    edges = np.geomspace(30, min(16000, fs / 2 - 1), 41)
    freqs = np.fft.rfftfreq(win, 1 / fs)
    masks = [(freqs >= lo) & (freqs < hi) for lo, hi in zip(edges[:-1], edges[1:])]
    hann = np.hanning(win)
    L, lev, clicks = [], [], []
    pad = 1.0
    t = start
    while t < stop:
        t1 = min(stop, t + block_s)
        a0 = max(0.0, t - pad)
        x, _ = sf.read(path, start=int(a0 * sr), stop=int(min(stop, t1 + pad) * sr), dtype='float32', always_2d=True)
        x = mono(x)
        # clicks: a jump much bigger than the 100 samples around it, in this block's own span
        i0, i1 = int((t - a0) * sr), int((t1 - a0) * sr)
        d = np.abs(np.diff(x))
        loc = np.convolve(d, np.ones(101) / 101, 'same') + 1e-9
        hit = np.flatnonzero(d[i0:i1] / loc[i0:i1] > 40)
        clicks.extend((hit + i0) / sr + a0)
        y = resample(x, sr, fs)
        f0 = int(np.ceil((t - start) * fs / hop))
        f1 = int(np.ceil((t1 - start) * fs / hop))
        base = int(round((a0 - start) * fs))
        idx = np.arange(f0, f1) * hop - base
        idx = idx[(idx >= 0) & (idx + win <= len(y))]
        if len(idx):
            F = y[idx[:, None] + np.arange(win)[None, :]] * hann[None, :]
            S = np.abs(np.fft.rfft(F, axis=1))
            L.append(np.log(np.stack([S[:, m].sum(1) for m in masks], 1) + 1e-6))
            lev.append(db(np.sqrt((F ** 2).mean(1))))
        t = t1
    L = np.concatenate(L) if L else np.zeros((0, 40))
    lev = np.concatenate(lev) if lev else np.zeros(0)
    # cluster clicks within 1 s: one candidate each
    cl = []
    for c in sorted(clicks):
        if not cl or c - cl[-1] > 1.0:
            cl.append(float(c))
    return L, lev, cl, fs / hop


def _period(flux, fps, a, b):
    """The beat period (s) of flux[a:b], from its autocorrelation, 95 to 180 BPM."""
    seg = flux[max(0, a):b]
    if len(seg) < 2 * fps:
        return None
    seg = seg - seg.mean()
    ac = np.fft.irfft(np.abs(np.fft.rfft(seg, 2 * len(seg))) ** 2)[:len(seg)]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 95)
    if hi >= len(ac):
        return None
    return (lo + 1 + int(np.argmax(ac[lo + 1:hi]))) / fps


def cuts(path, start=0.0, stop=None):
    """Cut points in the music: novelty peaks of the spectrum (1 s before vs 1 s after), gaps and clicks. Each is
    a 'skip' when the beat's phase breaks across it (or a gap or click marks it), a 'pause' when the music stops for
    2 s or more, else a 'change'. The beat period is measured in the 8 s before each cut. Blind spots (the DJ,
    10-13): a skip into a beatless part (only a gap or click can show it) and a phase-locked seek."""
    L, lev, clicks, fps = features(path, start, stop)
    n = len(L)
    w = int(fps)
    nov = np.zeros(n)
    if n > 2 * w:
        cs = np.cumsum(np.vstack([np.zeros(L.shape[1]), L]), 0)
        i = np.arange(w, n - w)
        nov[i] = np.linalg.norm((cs[i] - cs[i - w]) / w - (cs[i + w] - cs[i]) / w, axis=1) / np.sqrt(L.shape[1])
    flux = np.r_[0, np.maximum(np.diff(L, axis=0), 0).sum(1)] if n else np.zeros(0)
    if n > fps:
        flux = flux - np.convolve(flux, np.ones(int(fps)) / int(fps), 'same')
    med = np.median(nov) if n else 0.0
    thr = med + 6 * (np.median(np.abs(nov - med)) + 1e-9)
    peaks = []
    for i in np.argsort(-nov):
        if nov[i] < thr:
            break
        if all(abs(i - j) > 4 * fps for j in peaks):
            peaks.append(int(i))
    quiet = lev < -55
    gaps = []
    i = 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) / fps >= 0.04 and i > fps and j < n - fps:
                gaps.append(((i + j) / 2, (j - i) / fps))
            i = j
        else:
            i += 1

    def phase(t0, t1, P):
        a, b = max(0, int(t0 * fps)), min(len(flux), int(t1 * fps))
        if b - a < fps:
            return None
        tt = np.arange(a, b) / fps
        z = (flux[a:b] * np.exp(2j * np.pi * tt / P)).sum()
        return np.angle(z) / (2 * np.pi), abs(z) / (np.abs(flux[a:b]).sum() + 1e-9)

    cands = {round(i / fps, 2): ['novelty'] for i in peaks}
    for t, dur in gaps:
        near = [c for c in cands if abs(c - t / fps) < 1.0]
        (cands[near[0]] if near else cands.setdefault(round(t / fps, 2), [])).append(f'gap {dur * 1000:.0f} ms')
    for t in clicks:
        t = t - start
        near = [c for c in cands if abs(c - t) < 1.0]
        if near:
            cands[near[0]].append('click')
        else:
            cands[round(float(t), 2)] = ['click']
    out = []
    for t in sorted(cands):
        i = int(t * fps)
        P = _period(flux, fps, i - int(8 * fps), i - int(0.1 * fps)) or _period(flux, fps, i, i + int(8 * fps))
        pa_ = pb = None
        jump = None
        if P:
            pa_, pb = phase(t - 4, t - 0.1, P), phase(t + 0.1, t + 4, P)
            if pa_ and pb and pa_[1] > 0.04 and pb[1] > 0.04:
                jump = abs((pb[0] - pa_[0] + 0.5) % 1 - 0.5)
        why = cands[t]
        long_gap = any(r.startswith('gap') and float(r.split()[1]) >= PAUSE_GAP_S * 1000 for r in why)
        skip = (jump is not None and jump > 0.12 and min(pa_[1], pb[1]) >= 0.15) or \
            any(r.startswith('gap') or r == 'click' for r in why)
        out.append({'t': round(start + t, 2), 'kind': 'pause' if long_gap else ('skip' if skip else 'change'),
                    'why': why, 'phase_jump': None if jump is None else round(float(jump), 2),
                    'beat_strength': None if not (pa_ and pb) else round(float(min(pa_[1], pb[1])), 3),
                    'bpm_before': None if not P else round(60 / P, 1),
                    'novelty': round(float(nov[min(i, n - 1)]), 2) if n else 0.0,
                    'level_before': round(float(np.median(lev[max(0, i - int(2 * fps)):i])), 1) if i > 2 else -99.0,
                    'level_after': round(float(np.median(lev[i:i + int(2 * fps)])), 1) if i < n else -99.0})
    return out


def splice_test(path, n=20, seed=0, length_s=40.0):
    """The cut detector against known answers (the DJ's offline test): n splices of one track, each joining two
    random points, half of them on the beat's phase (expect 'change') and half off it (expect 'skip'). Needs no
    audio played. -> {'found': hits, 'kind_right': ..., 'false': cuts where no splice was, 'rows': [...]}."""
    from . import analysis as A
    rng = np.random.default_rng(seed)
    x, sr = sf.read(path, dtype='float32', always_2d=True)
    g, _ = A.beat_grid(path)
    spb = 60.0 / g['bpm']
    dur = len(x) / sr
    rows = []
    tmp = Path(os.environ.get('TEMP', '/tmp')) / f'listen_splice_{os.getpid()}.wav'
    try:
        for k in range(n):
            on = k % 2 == 0
            a = rng.uniform(length_s / 2, dur - length_s)
            b = rng.uniform(length_s / 2, dur - length_s)
            nb = round((b - a) / spb)
            b = a + nb * spb + (0.0 if on else spb * rng.uniform(0.25, 0.5))
            if not (0 < b < dur - length_s / 2):
                continue
            h = length_s / 2
            y = np.concatenate([x[int((a - h) * sr):int(a * sr)], x[int(b * sr):int((b + h) * sr)]])
            sf.write(tmp, y, sr, subtype='FLOAT')
            cs = cuts(tmp)
            hit = [c for c in cs if abs(c['t'] - h) < 0.5]
            rows.append({'splice': 'on phase' if on else 'off phase', 'from': round(a, 2), 'to': round(b, 2),
                         'found': bool(hit), 'kind': hit[0]['kind'] if hit else None,
                         'false': len([c for c in cs if abs(c['t'] - h) >= 0.5])})
    finally:
        if tmp.exists():
            tmp.unlink()
    want = lambda r: 'change' if r['splice'] == 'on phase' else 'skip'   # noqa: E731
    return {'found': sum(r['found'] for r in rows), 'n': len(rows),
            'kind_right': sum(r['kind'] == want(r) for r in rows), 'false': sum(r['false'] for r in rows),
            'rows': rows}


# ------------------------------------------------------------------ the reading of one stretch

def read_stretch(out, ref_path, sr, ch, a, b, k):
    from . import analysis as A
    d = out / 'stretches'
    d.mkdir(exist_ok=True)
    wav = d / f's{k:02d}.wav'
    meter = Loudness(sr, ch)
    with sf.SoundFile(wav, 'w', samplerate=sr, channels=ch, subtype='FLOAT') as f:
        t = a
        while t < b:
            t1 = min(b, t + 10.0)
            x, _ = sf.read(ref_path, start=int(t * sr), stop=int(t1 * sr), dtype='float32', always_2d=True)
            f.write(x)
            meter.feed(x)
            t = t1
    res = {'loudness': meter.result(), 'file': str(wav)}
    try:
        g, gtxt = A.beat_grid(str(wav))
        res['bpm'], res['bar1'] = round(float(g['bpm']), 2), round(float(g['offset_sec']), 3)
        res['grid'] = gtxt
        _, otxt = A.overview(str(wav), A.Grid(g['bpm'], g['offset_sec']))
        res['overview'] = '\n'.join(l for l in otxt.splitlines() if not l.lower().startswith('key'))
    except Exception as e:                                # a stretch with no beat to find still has its loudness
        res['grid_error'] = f'{type(e).__name__}: {e}'
    return res


# ------------------------------------------------------------------ the reading (the background process)

def log(out, line):
    print(line, flush=True)


def read(out, stt=None):
    """Read a stopped session: cuts, stretches, the mics lined up and their words, marks and notes, all on
    session time. stt: a function path -> words (tests pass one; default: the speech server)."""
    from . import machine
    out = Path(out)
    status = read_json(out / 'status.json')
    status.update(state='reading', read_error=None, reader_pid=os.getpid())
    write_json(out / 'status.json', status)
    try:
        with machine.slot('cpu', f'listen read {out.name}', wait=1800):
            report = _read(out, status, stt or stt_words)
    except Exception as e:
        status.update(state='read failed', read_error=f'{type(e).__name__}: {e}')
        write_json(out / 'status.json', status)
        log(out, f'reading failed: {status["read_error"]}')
        raise
    status.update(state='read')
    write_json(out / 'status.json', status)
    return report


def _read(out, status, stt):
    sts = [s for s in status['streams'] if s.get('t_start') is not None and s.get('frames')]
    if not sts:
        raise RuntimeError('no stream recorded any sound')
    ref = next((s for s in sts if s['loopback']), sts[0])
    ref_path = out / ref['file']
    rsr = ref['sr']
    dur_f = ref['frames'] / rsr
    S = lambda ft: round(to_session(ref, ft), 2)          # noqa: E731  ref file time -> session time
    report = {'ref': ref['name'], 'mics': [], 'cuts': [], 'stretches': [], 'utterances': [], 'marks': [],
              'notes': [], 'dropouts': [], 'duration_s': round(S(dur_f), 2)}
    log(out, 'reading: cuts in the music ...')
    cl = cuts(ref_path)
    for c in cl:
        c['t_file'] = c['t']
        c['t'] = S(c['t'])
    report['cuts'] = cl
    for s in sts:
        for ft, ms in s.get('dropouts') or []:
            report['dropouts'].append({'t': round(to_session(s, ft), 2), 'stream': s['name'], 'ms': ms,
                                       'loopback': s['loopback']})
    bounds = [0.0] + [c['t_file'] for c in cl if c['kind'] == 'skip'] + [dur_f]
    for k, (a, b) in enumerate(zip(bounds[:-1], bounds[1:]), 1):
        st = {'k': k, 'from': S(a), 'to': S(b), 'dur': round(b - a, 2), 'from_file': round(a, 2)}
        if b - a >= MIN_READ_S:
            log(out, f'reading: stretch {k} ({mmss(st["from"])} to {mmss(st["to"])}) ...')
            st.update(read_stretch(out, ref_path, rsr, ref['ch'], a, b, k))
        else:
            st['not_read'] = f'shorter than {MIN_READ_S} s'
        report['stretches'].append(st)
    for s in sts:
        if s is ref:
            continue
        lagf, info = fit_residual(ref_path, ref, out / s['file'], s)
        m = {'name': s['name'], 'device': s['device'], **info}
        log(out, f"reading: {s['name']} lined up ({info['how']}, lag {info['lag_s']:+.3f} s)")
        try:
            words = stt(out / s['file'])
            for w in words:
                for k in ('t', 'end'):
                    ss = to_session(s, w[k])
                    w[k] = round(ss - lagf(ss), 2)
            m['words'] = len(words)
            for u in utterances(words):
                u['who'] = s['name']
                report['utterances'].append(u)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            m['stt_error'] = (f"the speech server at {SPEAK} does not answer ({e}): the words were not read. Start "
                              "it, then listen_stop(session) reads this session again.")
        except Exception as e:
            m['stt_error'] = f'{type(e).__name__}: {e}'
        report['mics'].append(m)
    for ev in events(out):
        t = round(wall_to_session(status, ev['wall']), 2)
        back = float(ev.get('back_s') or 0.0)
        if ev['kind'] == 'mark':
            report['marks'].append({'t': round(t - back, 2), 'label': ev.get('label') or ''})
        elif ev['kind'] == 'note':
            if ev.get('ago_s') is not None:
                p = t - float(ev['ago_s'])
                win = [round(p - 5, 2), round(p + 5, 2)]
            else:
                win = [round(t - back, 2), round(t, 2)]
            report['notes'].append({'t': win[0], 'window': win, 'text': ev['text']})
    report['utterances'].sort(key=lambda u: u['t'])

    def place(item, t):
        s = next((s for s in report['stretches'] if s['from'] <= t < s['to']), None)
        if s:
            item['stretch'] = s['k']
            if s.get('bpm'):
                ft = to_file(ref, t) - s['from_file']
                item['bar'] = round((ft - s['bar1']) / (240 / s['bpm']) + 1, 1)
    for u in report['utterances']:
        u['window'] = [round(max(0.0, u['t'] - UTTER_BACK_S), 2), u['end']]
        place(u, u['t'])
        st = said_times(u['text'])
        if st:
            u['said_times'] = st
    for m in report['marks']:
        place(m, m['t'])
    for n_ in report['notes']:
        place(n_, n_['window'][1])
    write_json(out / 'read.json', report)
    (out / 'read.md').write_text('\n'.join(render(status, report, 'all')), encoding='utf8')
    log(out, f"read: {out / 'read.md'}")
    return report


# ------------------------------------------------------------------ the reading as text

def _stretch_row(s):
    lo = s.get('loudness') or {}
    if s.get('not_read'):
        return f"| {s['k']} | {mmss(s['from'])} | {mmss(s['to'])} | {s['dur']:.1f} s | not read ({s['not_read']}) |||| "
    return (f"| {s['k']} | {mmss(s['from'])} | {mmss(s['to'])} | {s['dur']:.1f} s | {lo.get('lufs')} | {lo.get('lra')} | "
            f"{lo.get('true_peak')} | {s.get('bpm', '') or ''} |")


def timeline(report):
    ev = []
    for c in report['cuts']:
        ev.append((c['t'], f"**{c['kind']}** ({', '.join(c['why'])}; phase jump {c['phase_jump']}; beat strength "
                           f"{c['beat_strength']}; level {c['level_before']} to {c['level_after']} dB)"))
    for u in report['utterances']:
        extra = [f"about {mmss(u['window'][0])} to {mmss(u['window'][1])}"]
        if 'stretch' in u:
            extra.append(f"stretch {u['stretch']}" + (f", bar {u['bar']}" if 'bar' in u else ''))
        if u.get('said_times'):
            extra.append('track time said: ' + ', '.join(mmss(v) for v in u['said_times']))
        ev.append((u['t'], f"said: \"{u['text']}\" ({'; '.join(extra)})"))
    for m in report['marks']:
        ev.append((m['t'], f"mark: {m['label'] or '(no label)'}" + (f" (bar {m['bar']})" if 'bar' in m else '')))
    for n in report['notes']:
        ev.append((n['t'], f"typed: \"{n['text']}\" (about {mmss(n['window'][0])} to {mmss(n['window'][1])})"))
    for d in report['dropouts']:
        what = ('nothing came from the speakers (paused, or nothing playing)' if d['loopback'] and d['ms'] >= 250
                else f"dropout in {d['stream']}")
        ev.append((d['t'], f"{what}: {d['ms']} ms"))
    return sorted(ev, key=lambda e: e[0])


def render(status, report, part='summary', start=None, end=None, limit=60):
    L = []
    if part in ('summary', 'all'):
        L.append(f"Session {status.get('date', '')}" + (f" ({status['label']})" if status.get('label') else '')
                 + f": {mmss(report['duration_s'])} long; times below are minutes:seconds since it started.")
        for s in status['streams']:
            L.append(f"- {s['name']}: {s['device']}, {s['sr']} Hz, {s['ch']} ch" +
                     (f"; {len(s['dropouts'])} dropouts" if s.get('dropouts') else ''))
        for m in report['mics']:
            L.append(f"- {m['name']} lined up on {report['ref']} by {m['how']}: lag {m['lag_s']:+.3f} s" +
                     (f" to {m['lag_end_s']:+.3f} s at the end ({m['drift_ms_per_10min']:+.1f} ms per 10 min)"
                      if 'lag_end_s' in m else '') + f"; {m.get('words', 0)} words" +
                     (f". {m['stt_error']}" if m.get('stt_error') else ''))
        kinds = {k: sum(c['kind'] == k for c in report['cuts']) for k in ('skip', 'change', 'pause')}
        L.append(f"Cuts: {kinds['skip']} skips, {kinds['change']} changes, {kinds['pause']} pauses. "
                 f"{len(report['utterances'])} things said, {len(report['notes'])} typed, {len(report['marks'])} marks.")
        L += ['', 'Stretches (between skips; a short stay is an answer too):', '',
              '| # | from | to | length | LUFS | LRA | true peak dBTP | BPM |', '|---|---|---|---|---|---|---|---|']
        L += [_stretch_row(s) for s in report['stretches']]
        longest = sorted(report['stretches'], key=lambda s: -s['dur'])[:3]
        L += ['', 'Longest stays: ' + ', '.join(f"#{s['k']} ({s['dur']:.0f} s)" for s in longest)]
        if part == 'summary':
            L += ['', "More: part='timeline' (start=, end= in seconds), part='stretch:<k>' for a stretch's grid "
                      "and sections."]
    if part in ('timeline', 'all'):
        ev = [e for e in timeline(report) if (start is None or e[0] >= start) and (end is None or e[0] <= end)]
        if part == 'all':
            L += ['', '## Timeline', '']
        shown = ev if part == 'all' else ev[:limit]
        L += [f'- {mmss(t)}  {e}' for t, e in shown]
        if len(ev) > len(shown):
            L.append(f"... {len(ev) - len(shown)} more: part='timeline', start={ev[len(shown)][0]:.0f}")
        if not ev:
            L.append('nothing in that window')
    if part.startswith('stretch:') or part == 'all':
        ks = [s['k'] for s in report['stretches']] if part == 'all' else [int(part.split(':')[1])]
        for k in ks:
            s = next((s for s in report['stretches'] if s['k'] == k), None)
            if s is None:
                L.append(f"no stretch {k}: there are {len(report['stretches'])}")
                continue
            if part == 'all' and not s.get('overview'):
                continue
            L += ['', f"Stretch {k} ({mmss(s['from'])} to {mmss(s['to'])}, {s['dur']:.1f} s)"]
            if s.get('not_read'):
                L.append(f"not read: {s['not_read']}")
                continue
            L.append('loudness: ' + ', '.join(f'{k2} {v}' for k2, v in (s.get('loudness') or {}).items()))
            L += [s.get('grid', ''), s.get('overview', '') or s.get('grid_error', '')]
            L.append(f"audio: {s.get('file')}")
    return L


# ------------------------------------------------------------------ entry for the background processes

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ''
    if cmd == 'record':
        out = Path(argv[1])
        record(out)
        req = read_json(out / 'request.json') or {}
        st = read_json(out / 'status.json') or {}
        if req.get('read_on_max') and st.get('stop_why') == 'max':
            read(out)
    elif cmd == 'read':
        read(Path(argv[1]))
    elif cmd == 'devices':
        print('\n'.join(devices()))
    elif cmd == 'splice-test':
        r = splice_test(argv[1], n=int(argv[2]) if len(argv) > 2 else 20)
        print(json.dumps({k: v for k, v in r.items() if k != 'rows'}))
        for row in r['rows']:
            print(row)
    else:
        print('usage: python -m ismail.listen record|read <session dir> | devices | splice-test <wav> [n]')


if __name__ == '__main__':
    main()
