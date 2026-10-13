"""listen_*: listening together. Record what this computer plays (and a mic, only after the person's yes) while you
and the person listen and talk about it, mark moments and their typed messages on the same clock, then read it back:
skips and changes in the music, each stretch's loudness and grid, and what was said, when (ismail/listen.py)."""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psutil

from .api import OpError, op
from . import listen as LI

MIC_CONSENT = ("A mic records the room, so it starts only after the person says yes to it for this session. Ask "
               "them in words (\"may I record the mic while we listen, so I can catch what you say?\"), then call "
               "again with mic_ok=True. Without a mic, they can type and you add their messages with listen_note.")


def _dir(session=None, want_live=False):
    """A session folder from its id (or a path); None: the one recording now, else the newest."""
    if session:
        p = Path(session)
        if not p.is_absolute() and not p.exists():
            p = LI.HOME / session
        if not (p / 'request.json').exists():
            raise OpError(f"no listen session {session!r}: listen_list() names them")
        return p
    rows = _sessions()
    live = [p for p, st in rows if (st or {}).get('state') in ('starting', 'recording')]
    if live:
        return live[0]
    if want_live:
        raise OpError("nothing is recording: listen_start() starts a session")
    if not rows:
        raise OpError("no listen sessions yet: listen_start() starts one")
    return rows[0][0]


def _sessions():
    if not LI.HOME.exists():
        return []
    rows = [(p, LI.read_json(p / 'status.json')) for p in LI.HOME.iterdir() if (p / 'request.json').exists()]
    return sorted(rows, key=lambda r: r[0].name, reverse=True)


def _alive(pid):
    try:
        return bool(pid) and psutil.Process(pid).is_running() and 'python' in psutil.Process(pid).name().lower()
    except psutil.Error:
        return False


def _status(d):
    """The session's status; a recorder that died without saying so is marked stopped."""
    st = LI.read_json(d / 'status.json') or {}
    if st.get('state') in ('starting', 'recording') and not _alive(st.get('pid')):
        st.update(state='stopped', stop_why='the recorder ended without stopping cleanly (see rec.log)')
        LI.write_json(d / 'status.json', st)
    if st.get('state') == 'reading' and not _alive(st.get('reader_pid')) and             time.time() - float(st.get('read_asked') or 0) > 60:
        st.update(state='read failed', read_error='the reading ended without finishing (see read.log)')
        LI.write_json(d / 'status.json', st)
    return st


def _spawn(d, cmd, logname):
    log = open(d / logname, 'a', encoding='utf8')
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == 'nt' else 0   # detached on Windows
    return subprocess.Popen([sys.executable, '-m', 'ismail.listen', cmd, str(d)],
                            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), stdout=log,
                            stderr=subprocess.STDOUT, creationflags=flags, start_new_session=os.name != 'nt').pid


def _phone(text=None, unsay=None):
    """The mic's state where the person is (the DJ, 10-13: he never saw the "recording is ON" line on the screen)."""
    try:
        from .phone import ops as P
        if unsay:
            P.phone_unsay(match=unsay)
        if text:
            P.phone_say(text=text, pin=True, priority='needs you' if 'ON' in text else None)
        return 'the phone page shows it'
    except Exception:
        return 'the phone page is not running, so tell them in words'


def _levels(st):
    lv = st.get('levels') or {}
    return ', '.join(f"{k} {v['rms_db']:.0f} dB (peak {v['peak_db']:.0f})" for k, v in lv.items()) or 'no sound yet'


@op()
def listen_start(streams: list = None, max_min: float = None, mic_ok: bool = False, label: str = None,
                 read_on_max: bool = True) -> str:
    """Start listening together: record what this computer plays while you and the person listen to a song and
    talk about it. streams: 'speakers' (the default output, the default), 'loop:<device name part>', 'mic' or
    'mic:<name part>'. A mic needs mic_ok=True, given only after the person said yes for this session. It stops by
    itself after max_min (90 min, 20 with a mic: then ask whether to go on). While it runs: listen_peek (levels and the
    last 30 s), listen_mark (a moment), listen_note (their typed messages); listen_stop ends it and reads it."""
    streams = list(streams or ['speakers'])
    try:
        streams = [LI.check_spec(s) for s in streams]
    except ValueError as e:
        raise OpError(str(e))
    mic = any(LI.is_mic(s) for s in streams)
    if mic and not mic_ok:
        raise OpError(MIC_CONSENT)
    try:
        LI._pa()
    except RuntimeError as e:
        raise OpError(str(e))
    for p, st in _sessions():
        if (st or {}).get('state') in ('starting', 'recording') and _alive(st.get('pid')):
            raise OpError(f"session {p.name} is recording already: listen_stop('{p.name}') first (one at a time)")
    mm = float(max_min) if max_min else (LI.MIC_MAX_MIN if mic else LI.MAX_MIN)
    if not 0 < mm <= LI.HARD_MAX_MIN:
        raise OpError(f"max_min: up to {LI.HARD_MAX_MIN} minutes")
    sid = time.strftime('%Y-%m-%d_%H%M%S')
    d = LI.HOME / sid
    d.mkdir(parents=True, exist_ok=False)
    LI.write_json(d / 'request.json', {'streams': streams, 'max_s': mm * 60, 'label': label or '',
                                       'read_on_max': bool(read_on_max)})
    _spawn(d, 'record', 'rec.log')
    end = time.time() + 15
    st = {}
    while time.time() < end:
        st = LI.read_json(d / 'status.json') or {}
        if st.get('state') in ('recording', 'failed', 'stopped'):
            break
        time.sleep(0.3)
    if st.get('state') != 'recording':
        tail = (d / 'rec.log').read_text(encoding='utf8', errors='replace')[-600:] if (d / 'rec.log').exists() else ''
        raise OpError(f"the recorder did not start ({st.get('error') or 'no word in 15 s'}). Its log: {tail.strip()}")
    L = [f"Listening together: session {sid}. Live inputs, every one:"]
    for s in st['streams']:
        L.append(f"- {s['name']}: {s['device']} ({s['sr']} Hz, {s['ch']} ch)" + (' <- THE MIC IS ON' if
                                                                                    LI.is_mic(s['spec']) else ''))
    if mic:
        where = _phone(f"Mic is ON: recording the room while we listen (up to {mm:.0f} min). Say stop any time.")
        L.append(f"The mic is ON: {where}. Say \"stop\" or \"delete that\" works any time "
                 f"(listen_stop(discard=True) deletes it unread).")
    else:
        L.append("No mic: only what the computer plays. Add their typed messages with listen_note as they arrive.")
    L.append(f"It stops by itself after {mm:.0f} min. Now: listen_peek, listen_mark('<what>'), listen_note('<their "
             f"words>'); listen_stop() ends it and reads it in the background.")
    return '\n'.join(L)


@op()
def listen_peek(session: str = None) -> str:
    """While listening: how long it has run, each input's level over the last 10 s, dropouts so far, and a quick
    read of the last 30 s (tempo, loudness), to answer "what's this bit?" before it ends."""
    d = _dir(session, want_live=True)
    st = _status(d)
    if st.get('state') not in ('starting', 'recording'):
        raise OpError(f"session {d.name} is {st.get('state')}: listen_read('{d.name}') for its reading")
    L = [f"Session {d.name}: {LI.mmss(st.get('elapsed_s', 0))} in, of {st['max_s'] / 60:.0f} min at most. Last 10 s: "
         f"{_levels(st)}."]
    drops = [(s['name'], x) for s in st['streams'] for x in s.get('dropouts') or []]
    if drops:
        L.append('Dropouts: ' + ', '.join(f"{n} at {LI.mmss(x[0])} ({x[1]} ms)" for n, x in drops[-5:]))
    (d / 'peek.wav').unlink(missing_ok=True)
    (d / 'peek.req').write_text('1')
    end = time.time() + 5
    while time.time() < end and ((d / 'peek.req').exists() or not (d / 'peek.wav').exists()):
        time.sleep(0.1)
    if (d / 'peek.wav').exists():
        import soundfile as sf
        x, sr = sf.read(d / 'peek.wav', dtype='float32', always_2d=True)
        if len(x) >= sr * 4 and LI.db(np.abs(x).max()) < -70:
            L.append(f"Last {len(x) / sr:.0f} s: silent (nothing playing, or it plays somewhere this does not hear).")
        elif len(x) >= sr * 4:
            m = LI.Loudness(sr, x.shape[1])
            m.feed(x)
            lo = m.result()
            line = f"Last {len(x) / sr:.0f} s: {lo['lufs']} LUFS, true peak {lo['true_peak']} dBTP"
            try:
                from . import analysis as A
                g, _ = A.beat_grid(str(d / 'peek.wav'))
                line += f", about {g['bpm']:.0f} BPM (half or double possible)"
            except Exception:
                line += ', no steady beat found'
            L.append(line + '.')
        else:
            L.append('Under 4 s of sound so far: nothing to measure yet.')
    else:
        L.append('The recorder did not hand over the last 30 s in 5 s (busy); levels above are current.')
    return '\n'.join(L)


@op()
def listen_mark(label: str = '', session: str = None, ago_s: float = 0.0) -> str:
    """Mark a moment while listening ("this!", a drop, where they laughed): it lands on the session's clock, now or
    ago_s seconds back. Cheaper than a note; the reading shows it on the timeline with its bar."""
    d = _dir(session, want_live=True)
    st = _status(d)
    if st.get('state') not in ('starting', 'recording'):
        raise OpError(f"session {d.name} stopped already: a mark after the stop has no music under it")
    LI.add_event(d, {'kind': 'mark', 'wall': time.time(), 'label': label or '', 'back_s': float(ago_s or 0)})
    return f"marked{' ' + repr(label) if label else ''} at {LI.mmss(time.time() - st['wall0'] - float(ago_s or 0))}"


@op()
def listen_note(text: str, session: str = None, ago_s: float = None) -> str:
    """Add the person's typed message (their words as written) to the listening timeline. Typing takes time, so it
    is placed over the window it took to type (about its length / 4 s, before now); ago_s: they said when ("that bit
    20 seconds ago"), placed there, within 5 s either side."""
    d = _dir(session, want_live=True)
    st = _status(d)
    if st.get('state') not in ('starting', 'recording'):
        raise OpError(f"session {d.name} stopped already: a note after the stop is off its clock")
    text = str(text).strip()
    if not text:
        raise OpError("text: their message, as they wrote it")
    back = min(120.0, len(text) / LI.TYPE_CPS)
    ev = {'kind': 'note', 'wall': time.time(), 'text': text, 'back_s': round(back, 1)}
    if ago_s is not None:
        ev['ago_s'] = float(ago_s)
    LI.add_event(d, ev)
    now = time.time() - st['wall0']
    where = (f"around {LI.mmss(now - float(ago_s))}" if ago_s is not None
             else f"over {LI.mmss(now - back)} to {LI.mmss(now)}")
    return f"noted {where}"


@op()
def listen_stop(session: str = None, read: bool = True, discard: bool = False) -> str:
    """End a listening session. read=True reads it in the background (listen_read shows it). discard=True deletes it
    unread, recordings and all: when the person says "delete that". On a session that stopped already, read=True
    reads it again (after the speech server came back, say)."""
    d = _dir(session)
    st = _status(d)
    L = []
    if st.get('state') in ('starting', 'recording'):
        (d / 'stop').write_text('1')
        end = time.time() + 20
        while time.time() < end:
            st = _status(d)
            if st.get('state') not in ('starting', 'recording'):
                break
            time.sleep(0.3)
        if st.get('state') in ('starting', 'recording'):
            try:
                psutil.Process(st['pid']).kill()
            except psutil.Error:
                pass
            st.update(state='stopped', stop_why='did not stop in 20 s, ended')
            LI.write_json(d / 'status.json', st)
        L.append(f"Stopped {d.name} at {LI.mmss(st.get('stopped_at_s') or st.get('elapsed_s') or 0)}.")
        if st.get('mic'):
            L.append(f"Mic off: {_phone('Mic is off.', unsay='Mic is ON')}.")
    elif st.get('state') == 'reading' and not discard:
        return f"{d.name} is being read now: listen_read('{d.name}')"
    if discard:
        if st.get('state') == 'reading':
            try:
                psutil.Process(st.get('reader_pid')).kill()
            except (psutil.Error, TypeError, ValueError):
                pass
        for _ in range(20):
            try:
                shutil.rmtree(d)
                break
            except PermissionError:
                time.sleep(0.25)
        if d.exists():
            raise OpError(f"could not delete {d} (a file is still open); listen_stop('{d.name}', discard=True) again")
        L.append(f"Deleted {d.name} unread: the recordings and everything with them are gone.")
        return '\n'.join(L)
    if read:
        st = LI.read_json(d / 'status.json') or {}
        st.update(state='reading', reader_pid=None, read_asked=time.time(), read_error=None)
        LI.write_json(d / 'status.json', st)
        _spawn(d, 'read', 'read.log')
        L.append(f"Reading it in the background (about a minute per 10 minutes, more with a mic for the words): "
                 f"listen_read('{d.name}').")
    else:
        L.append(f"Not read. listen_stop('{d.name}') later reads it; discard=True deletes it.")
    return '\n'.join(L)


@op()
def listen_read(session: str = None, part: str = 'summary', start: float = None, end: float = None,
                limit: int = 60) -> str:
    """The reading of a listening session (newest by default). part: 'summary' (inputs, cuts, stretches with
    LUFS, LRA, true peak, BPM), 'timeline' (skips, changes, what was said, typed and marked, in order; start= and
    end= in seconds since it began, up to limit lines), or 'stretch:<k>' (one stretch's grid and sections). Times
    are minutes:seconds since the session began; a bar is in the stretch's own grid."""
    d = _dir(session)
    st = _status(d)
    state = st.get('state')
    if state in ('starting', 'recording'):
        return f"{d.name} is still recording ({LI.mmss(st.get('elapsed_s', 0))} in): listen_peek for now, listen_stop to end it."
    if state == 'reading':
        tail = (d / 'read.log').read_text(encoding='utf8', errors='replace').strip().splitlines()[-3:] \
            if (d / 'read.log').exists() else []
        return f"{d.name} is being read: " + (' / '.join(tail) or 'starting') + ". Ask again in a minute."
    if state == 'read failed':
        return f"The reading of {d.name} failed: {st.get('read_error')}. listen_stop('{d.name}') tries again."
    report = LI.read_json(d / 'read.json')
    if not report:
        return f"{d.name} is {state} and not read: listen_stop('{d.name}') reads it."
    if not (part in ('summary', 'timeline') or part.startswith('stretch:')):
        raise OpError("part: 'summary', 'timeline' or 'stretch:<k>'")
    return '\n'.join(LI.render(st, report, part, start, end, max(1, int(limit))))


@op()
def listen_list(devices: bool = False, n: int = 10) -> str:
    """Listening sessions, newest first (id, state, length, mic or not), or with devices=True the inputs this
    computer can record (the default speakers, other outputs as loop:<name>, mics as mic:<name>)."""
    if devices:
        try:
            return '\n'.join(LI.devices())
        except RuntimeError as e:
            raise OpError(str(e))
    rows = _sessions()[:max(1, int(n))]
    if not rows:
        return "no listen sessions yet: listen_start() starts one"
    L = []
    for p, st in rows:
        st = _status(p) if st else {}
        ln = st.get('stopped_at_s') or st.get('elapsed_s') or 0
        L.append(f"{p.name}  {st.get('state', '?')}  {LI.mmss(ln)}  {'mic' if st.get('mic') else 'no mic'}"
                 + (f"  {st['label']}" if st.get('label') else ''))
    return '\n'.join(L)
