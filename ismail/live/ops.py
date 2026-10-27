"""live_* ops: the agent's controls for the live engine (registered into api.OPS on import).

Each op talks to the engine process of a project over local HTTP; live_start launches it."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ..api import OpError, heavy, op
from .. import analysis as A
from .. import settings

VIEWS = {'bars': A.bar_table, 'envelope': A.envelope, 'pitches': A.pitches, 'drums': A.drums, 'chords': A.chords}


def _info_path(project):
    return os.path.join(os.path.abspath(project), 'live', 'engine.json')


def _call(project, name, /, timeout=30, **args):     # positional-only: an op argument may be called name
    try:
        with open(_info_path(project), encoding='utf8') as f:
            port = json.load(f)['port']
    except (OSError, ValueError, KeyError):
        raise OpError(f"no live engine for {project}; start one with live_start(project='{project}', bpm=...)")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/", data=json.dumps({'op': name, 'args': args}).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OpError(f"the live engine for {project} does not answer ({e}); it may have stopped. live_start again "
                      f"(its log is {os.path.join(os.path.abspath(project), 'live', 'engine.log')})")
    if not res.get('ok'):
        raise OpError(res.get('error', 'engine error'))
    return res['result']


def _alive(project):
    try:
        _call(project, 'status', timeout=3)
        return True
    except OpError:
        return False


def _pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _kill_tree(pid):
    """The engine and its render workers."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(int(pid)), '/T', '/F'], capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _engine_record(project):
    try:
        with open(_info_path(project), encoding='utf8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _other_engines(project):
    """Engines still running for other folders (a forgotten set can hold the speakers for hours)."""
    from .engine import registry_path
    d = registry_path()
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return out
    me = os.path.abspath(project)
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, encoding='utf8') as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not _pid_alive(rec.get('pid', -1)):
            try:
                os.remove(p)                                 # a note left by an engine that died
            except OSError:
                pass
            continue
        if os.path.abspath(rec.get('project', '')) != me:
            out.append(rec)
    return out


@op()
def live_start(project: str, bpm: float, beats_per_bar: int = 4, device: str = 'default', workers: int = None,
               follow_device: bool = True, trim_db: float = None, cap_db: float = None,
               priority: str = 'above_normal') -> str:
    """Start the live engine for `project` (any folder; a project.json there lends its sound bank, song voices and
    'track:<name>' instruments). It plays from bar 1 immediately, silent until you queue clips, and keeps playing
    between your calls: clips loop until replaced. bpm is the starting tempo: live_tempo changes it while it plays.
    device: 'default' (speakers), a device name/index, or 'none' (no audio out; for testing and listen-only
    analysis). workers: render processes (default: cores - 4, from 2 to 4; more for many mimic voices). Output always
    passes the safety chain: trim, loudness cap, limiter, ceiling -1 dBFS. trim_db (-30 to +12) and cap_db (sustained
    RMS dBFS, -40 to -8) set this set's level; left out, they come from the live.trim_db / live.cap_db settings
    (config_set), else -6 and -16 (quiet, about -22 LUFS). A set for listeners: trim_db=4, cap_db=-12 (about -16
    LUFS). priority: the engine process's own priority, 'above_normal' (default), 'high' or 'normal'; its render
    workers stay normal ('high' can starve them). The reply names any other live engine still running on this
    machine (two on one device can freeze both). Next: live_track, then live_queue."""
    from .engine import PRIORITIES
    from .safety import CAP_RANGE, TRIM_RANGE
    if priority not in PRIORITIES:
        raise OpError(f"priority {priority!r}: one of {', '.join(PRIORITIES)} (render workers stay normal)")
    level = {}
    for name, v, (lo, hi) in (('trim_db', trim_db, TRIM_RANGE), ('cap_db', cap_db, CAP_RANGE)):
        if v is None:
            continue
        try:
            level[name] = float(v)
        except (TypeError, ValueError):
            level[name] = None
        if isinstance(v, bool) or level[name] is None or not lo <= level[name] <= hi:
            raise OpError(f"{name} {v!r}: a number from {lo:g} to {hi:g} dB")
    root = os.path.abspath(project)
    if workers is None:
        workers = min(4, max(2, (os.cpu_count() or 4) - 4))
    if _alive(project):
        return ("already running (live_tempo changes the tempo, live_device the output)\n"
                + _call(project, 'status'))
    others = []
    for rec in _other_engines(project):
        mins = (time.time() - rec.get('started', time.time())) / 60
        others.append(f"another live engine is still running: {rec.get('project')} on device {rec.get('device')!r}, "
                      f"started {mins:.0f} min ago (pid {rec.get('pid')}). Two engines on one device can freeze "
                      f"both: live_stop(project='{rec.get('project')}') unless it is meant to play")
    if str(device).lower() not in ('none', 'null'):
        try:
            import sounddevice  # noqa: F401
        except (ImportError, OSError) as e:
            raise OpError(f"playing to speakers needs sounddevice ({e}): pip install -e \".[live]\" (on Linux also "
                          f"PortAudio: sudo apt install libportaudio2), or start with device='none' to run without "
                          f"audio out")
    os.makedirs(os.path.join(root, 'live'), exist_ok=True)
    try:
        os.remove(_info_path(project))
    except OSError:
        pass
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=pkg_root + os.pathsep + os.environ.get('PYTHONPATH', ''))
    log = open(os.path.join(root, 'live', 'engine.log'), 'w', encoding='utf8')
    flags = 0
    if os.name == 'nt':
        # a hidden console, not none: the render workers inherit it instead of each opening a window
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([sys.executable, '-m', 'ismail.live.engine', '--project', root, '--bpm', str(bpm),
                      '--bpb', str(beats_per_bar), '--device', str(device), '--workers', str(workers),
                      '--priority', priority]
                     + ([] if follow_device else ['--no-follow'])
                     + [x for k, v in level.items() for x in ('--' + k.replace('_', '-'), repr(v))],
                     cwd=pkg_root, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                     start_new_session=os.name != 'nt')
    t0 = time.time()
    while time.time() - t0 < 60:
        if os.path.exists(_info_path(project)) and _alive(project):
            prio = (_engine_record(project) or {}).get('priority') or f"priority {priority}: not reported"
            return '\n'.join(others + [f"live engine started ({time.time() - t0:.1f} s, {prio}, render workers normal)",
                                        _call(project, 'status')])
        time.sleep(0.3)
    raise OpError(f"the live engine did not come up in 60 s; read {os.path.join(root, 'live', 'engine.log')}")


@op()
def live_stop(project: str, fade_sec: float = 1.0) -> str:
    """Fade out and stop the live engine (closes any recording). Waits until the process has gone; an engine
    that does not stop (a dead audio device, a hung process) is killed with its render workers."""
    rec = _engine_record(project)
    if rec is None:
        raise OpError(f"no live engine for {project} (nothing to stop)")
    try:
        msg = _call(project, 'stop', timeout=5, fade_s=fade_sec)
    except OpError as e:
        msg = f"the engine did not answer ({str(e).split(';')[0]})"
    pid = rec.get('pid')
    t0 = time.time()
    while pid and _pid_alive(pid) and time.time() - t0 < float(fade_sec) + 8:
        time.sleep(0.2)
    if pid and _pid_alive(pid):
        _kill_tree(pid)
        time.sleep(0.5)
        msg += f"; it did not exit, so it was killed (pid {pid}, with its render workers)"
        try:
            os.remove(_info_path(project))
        except OSError:
            pass
    else:
        msg += f"; stopped ({time.time() - t0:.1f} s)"
    return msg


@op()
def live_status(project: str, deck: str = None) -> str:
    """Where the live set is: bar heard, each deck (on air or cued, fader, eq, filter, level, song), each track
    not on a deck (instrument, fader, level now and the loudest over the last 10 s, playing/next clip, render
    cost, errors), the safety chain's gain reduction since the last status, runway (the last scheduled change and
    what loops after it) and render health (backlog, late events, underruns). A STALLED line means the audio
    device stopped asking for audio. `mixer:` is the time a block takes to mix against its length (it climbs when
    other programs load the machine); `headroom:` is the real risk: the mixed audio still waiting when the device
    pulled, at its thinnest since the previous status, out of the 500 ms kept ahead (0 = a dropout, THIN under
    125 ms). Judge a pause by headroom, not by the mixer %. deck='B' lists that deck's tracks and buses instead.
    Problems that arrive between calls (a dropped note, a stall) are added to the next reply of any live op. The
    last line is the machine's free commit and disk (takes are written to disk; a full disk stops the recording)."""
    out = _call(project, 'status', deck=deck)
    try:
        from .. import machine
        out += '\n' + machine.pressure_line()
    except Exception:
        pass
    return out


def _project_fx(project, spec):
    """'track:<name>' | 'bus:<name>' -> that chain from the folder's project.json."""
    try:
        with open(os.path.join(os.path.abspath(project), 'project.json'), encoding='utf8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        raise OpError(f"fx={spec!r} copies a chain from project.json, but {project} has none")
    kind, name = spec.split(':', 1)
    src = d.get('tracks' if kind == 'track' else 'buses', {}).get(name)
    if src is None:
        raise OpError(f"fx={spec!r}: project.json has no {kind} {name!r}")
    return src.get('fx', [])


def _mark_performer(inst, project):
    """A code voice the studio plays with perform() (fn='perform', or a module with perform() and no function named
    fn: instruments.performer_fn) plays whole phrases: flag it so the engine groups overlapping notes into one event
    and passes clip expr lanes (the module is imported here, in the op process, never in the engine). A mimic with
    perform on plays phrases the same way."""
    if isinstance(inst, dict) and inst.get('type') == 'mimic' and inst.get('perform'):
        return dict(inst, performer=True)
    if not isinstance(inst, dict) or inst.get('type') != 'code' or not inst.get('voice'):
        return inst
    from .. import instruments, voices
    try:
        mod = voices.load(inst['voice'], os.path.abspath(project))
    except Exception:
        return inst
    if instruments.performer_fn(mod, inst.get('fn')) is not None:
        return dict(inst, performer=True)
    return inst


def _performer_gaps(inst, project, where):
    """Warnings for the performers the live engine cannot play as the studio does: a kit pad whose code voice is a
    performer gets the pad's whole part in the studio, but live renders a kit hit by hit (ledger:M284)."""
    if not isinstance(inst, dict) or inst.get('type') != 'kit':
        return []
    pads = [k for k, v in (inst.get('map') or {}).items() if _mark_performer(v, project) is not v]
    if not pads:
        return []
    return [f"warning: {where}: kit pad(s) {', '.join(map(str, pads))} play a performer voice (perform()); the studio "
            f"gives such a pad its whole part, live plays it hit by hit and will not match. Put that voice on a "
            f"track of its own to play it live as in the studio."]


@op()
def live_track(project: str, track: str, instrument=None, volume_db: float = None, pan: float = None,
               fx=None, sends: dict = None, remove: bool = False, at: str = 'next_bar', deck: str = None,
               output: str = None) -> str:
    """Create or change a live track. instrument: a dict (instrument_help), 'preset:<name>' (presets_list),
    {'type': 'code', 'voice': '<name>'} (voices_list) or 'track:<name>' (copy from the folder's project.json).
    fx: the track's whole effect chain, a list of effect dicts (fx_help), 'stack:<name>' for a saved effect stack
    (fx_stack_list; the reply says what it costs on this machine), or 'track:<name>' to copy a project track's
    chain; [] removes all. Sidechain/duck/vocoder sources must be live tracks. sends: {bus: dB} replaces
    the track's sends (post-fader; live_bus creates buses). output: a bus the new track plays into instead of
    straight into the master (or its deck); the bus still reaches the master. A stream of that bus
    (live_stream(name='bus:<b>')) then carries only the tracks routed to it, which a send cannot do (the dry track
    stays on the master). It is set when the track is created; to move a track, remove it and create it again.
    A new instrument or chain on a playing track takes over at `at` (live_queue values, or 'now'); a replaced
    chain's tail rings out. New instruments warm up off the air first (live_status shows WARMING). volume_db <= +6.
    remove=True silences and deletes the track now. To move one effect param (a sweep, a fade), use live_fx instead of resending the chain. deck='B' puts a new
    track on deck B (created if needed): it then plays through that deck's strip and cue."""
    stack = None
    if isinstance(fx, str) and fx.startswith('stack:'):            # a saved effect stack (ledger:M177)
        from .. import api_fxstack as X
        stack = fx[6:]
        fx = X.find(stack, project)[1]['chain']
    elif isinstance(fx, str):
        if not fx.startswith(('track:', 'bus:')):
            raise OpError("fx: a list of effect dicts, 'stack:<name>' (fx_stack_list), or 'track:<name>' / "
                          "'bus:<name>' to copy from project.json")
        fx = _project_fx(project, fx)
    if instrument is not None:
        from .. import api
        P = api.Project(project) if os.path.exists(os.path.join(os.path.abspath(project), 'project.json')) else None
        if isinstance(instrument, str) and instrument.startswith('track:') and P is None:
            raise OpError(f"instrument {instrument!r} copies a project track, but {project} has no project.json; "
                          f"pass a dict or 'preset:<name>'")
        # a folder without project.json still has song voices in <folder>/voices: check against that root
        instrument = api._resolve_instrument(instrument, P or type('Root', (), {'root': os.path.abspath(project)})())
        instrument = _mark_performer(instrument, project)
    gaps = _performer_gaps(instrument, project, f"track {track!r}") if instrument is not None else []
    if output is not None and not (isinstance(output, str) and output):
        raise OpError(f"output {output!r}: the name of a live bus (live_bus creates one)")
    out = _call(project, 'track', track=track, instrument=instrument, volume_db=volume_db, pan=pan, remove=remove,
                at=at, fx=fx, sends=sends, deck=deck, output=output)
    if fx:
        from .. import api_fxstack as X
        cost = X.chain_costs([stack or fx])
        if cost:
            out = str(out) + '\n' + '\n'.join(cost)
    if gaps:
        out = str(out) + '\n' + '\n'.join(gaps)
    return out


@op()
def live_deck(project: str, deck: str, volume_db: float = None, low_db: float = None, mid_db: float = None,
              high_db: float = None, filter: float = None, transpose: int = None, cue: bool = None,
              ramp_beats: float = 0, at: str = 'now', remove: bool = False) -> str:
    """A deck's channel strip (a deck = a group of tracks, e.g. a loaded song). volume_db: fader (-60 or less =
    off). low_db / mid_db / high_db: 3-band isolator, bands split at 250 Hz and 2.5 kHz; -40 or less kills the
    band. filter: one knob, -1 (low-pass closed) .. 0 (off) .. +1 (high-pass closed). These glide over ramp_beats
    from `at` ('now' or a live_queue value), like live_fx. transpose: semitones for the deck's pitched tracks
    (drums stay), from `at`. cue=True takes the deck off the air (live_listen(deck=...) still hears it: prepare
    the next part there); cue=False puts it on air. remove=True deletes the deck and its tracks."""
    return _call(project, 'deck', deck=deck, volume_db=volume_db, low_db=low_db, mid_db=mid_db, high_db=high_db,
                 filter=filter, transpose=transpose, cue=cue, ramp_beats=ramp_beats, at=at, remove=remove)


@op()
def live_load(project: str, deck: str, song: str, bars: list = None, at: str = 'next_bar', loop: bool = True,
              cue: bool = None) -> str:
    """Load an ismail song (its project folder: tracks, instruments, effects, buses, notes) onto a deck; every
    track starts on the same bar. bars=[a, b] takes a section. It plays at the house tempo (re-rendered, not
    time-stretched) and loops unless loop=False. Cued (off air) by default while another deck is on air: listen
    with live_listen(deck=...), then bring it in with live_transition. The song's mix comes over as rendered:
    its automation (effect params and volume as ramps, instrument params rendered with the notes, the master fade;
    repeated every pass on a looping deck) and its group buses (a reverb on a drum bus keeps the dry drums). Audio
    tracks (placed clips, track_freeze) play from the song's files bar by bar at the song's own speed; audio placed
    on a track that also has notes does not (the reply lists what was left out). The song's master chain (its
    limiter) runs on the deck.
    Loading replaces what a cued deck held. A cued deck plays from `at` (off air), so a song loaded early with
    at='next_bar' is already bars into itself when the transition brings it in: load it with at='bar:<the
    transition's bar>' to start it from its top on air. deck_cost(song) says beforehand how long its first bar
    takes to render here, and which track makes the wait."""
    if not os.path.isabs(song):
        cand = os.path.join(os.path.abspath(project), song)
        song = cand if os.path.exists(cand) else settings.project_dir(song)   # bare: the songs folder (M375)
    out = _call(project, 'load', deck=deck, song=song, bars=bars, at=at, loop=loop, cue=cue,
                performers=_song_performers(song), timeout=120)
    gaps = _song_performer_gaps(song)
    if gaps:
        out = str(out) + '\n' + '\n'.join(gaps)
    try:                                                    # its saved stacks, and what they cost here (ledger:M177)
        from .. import api_fxstack as X
        with open(os.path.join(song, 'project.json'), encoding='utf8') as f:
            d = json.load(f)
        cost = X.chain_costs([t.get('fx') for t in d.get('tracks', {}).values() if t.get('fx')])
        if cost:
            out = str(out) + '\n' + '\n'.join(cost)
    except (OSError, ValueError):
        pass
    return out


@op()
@heavy()
def deck_cost(project: str, bars: list = None, bpm: float = None, workers: int = None, wait: str = '10m') -> str:
    """What a song costs as a deck, before it plays: its first bar rendered offline the way the live engine renders
    it, timed (no live_start, nothing played, no engine touched). Reach for it when planning a set or a reset: which
    deck leaves a silent gap after live_load, how long, and which track makes it. project: the song's project
    folder, as live_load takes it. bars=[a, b]: the deck window live_load will get (its first bar is measured);
    bpm: the house tempo (default the song's). The report: each track's render seconds for that bar and its real-time
    factor (render seconds / the bar's seconds; above 1 that track alone renders slower than it plays), its slowest
    event, its first note (first-use cost), its effects' ms per mixer block; the total, the wait for the first bar
    with `workers` render workers (live_start's default), and the mixer's load for this deck alone. Then: load a
    heavy deck that many bars ahead and keep it cued, pick a lighter section, or track_freeze its heavy parts. The
    numbers are this machine as it is now: a busy machine reads slower. wait: stand in line this long when the
    machine is busy."""
    from . import cost
    song = os.path.abspath(project)
    if bars is not None and (not isinstance(bars, (list, tuple)) or len(bars) != 2):
        raise OpError("bars=[first, last], the deck window (inclusive), e.g. [17, 32]")
    try:
        r = cost.run(song, bars, bpm, workers, _song_performers(song))
    except cost.CostError as e:
        raise OpError(str(e))
    return cost.report(r)


def _song_performers(song):
    """Names of a song's tracks whose voice is a performer (checked here, in the op process, like live_track)."""
    try:
        with open(os.path.join(song, 'project.json'), encoding='utf8') as f:
            tracks = json.load(f).get('tracks', {})
    except (OSError, ValueError):
        return []
    return [n for n, t in tracks.items() if isinstance(t.get('instrument'), dict) and
            _mark_performer(t['instrument'], song).get('performer')]


def _song_performer_gaps(song):
    """_performer_gaps for each of a song's tracks, as live_load says them."""
    try:
        with open(os.path.join(song, 'project.json'), encoding='utf8') as f:
            tracks = json.load(f).get('tracks', {})
    except (OSError, ValueError):
        return []
    return [w for n, t in tracks.items() for w in _performer_gaps(t.get('instrument'), song, f"track {n!r}")]


@op()
def live_transition(project: str, to: str, from_deck: str = None, at: str = 'next_8', bars: int = 16,
                    style: str = 'blend', stop_from: bool = True) -> str:
    """Queue a mix from one deck to another as ramps on their strips, starting at `at` (a phrase boundary).
    style: blend (new deck fades up without bass, bass swap half-way, old fades out) | bass_swap (both full, one
    bass at a time, old leaves in the last quarter) | filter (old thins out through a rising high-pass while the
    new opens from a low-pass) | cut (switch on the boundary; bars is ignored, and the old deck stops and goes off air on that same
    boundary). The `to` deck must be playing by then (live_load
    or live_queue it first, cued); it goes on air at the start. from_deck defaults to the one deck on air. With
    stop_from, the old deck's tracks stop when the transition ends and it goes off air (cued), ready for the next
    live_load. The reply is the timeline of every move."""
    return _call(project, 'transition', to=to, from_deck=from_deck, at=at, bars=bars, style=style,
                 stop_from=stop_from)


@op()
def live_bus(project: str, bus: str, fx=None, volume_db: float = None, remove: bool = False, at: str = 'now') -> str:
    """Create or change a send bus (a shared effect such as one hall for many tracks; tracks feed it with
    live_track(sends={bus: dB})). fx: its chain (list, or 'bus:<name>' / 'track:<name>' to copy from project.json);
    reverbs and delays on a bus output only the wet signal unless given 'dry'. A replaced chain's tail rings out.
    remove=True deletes it and the sends into it."""
    if isinstance(fx, str):
        if not fx.startswith(('track:', 'bus:')):
            raise OpError("fx: a list of effect dicts, or 'track:<name>' / 'bus:<name>' to copy from project.json")
        fx = _project_fx(project, fx)
    return _call(project, 'bus', bus=bus, fx=fx, volume_db=volume_db, remove=remove, at=at)


def _older_engine(call):
    """A running engine started before section= and cancel_section= refuses them: say what to do."""
    try:
        return call()
    except OpError as e:
        if 'unexpected keyword' not in str(e):
            raise
        raise OpError(f"{e}. This engine was started before section= and cancel_section=: live_stop and live_start "
                      f"again to use them (a plain live_queue or live_fx still works on it)") from None


@op()
def live_fx(project: str, target: str = None, index=None, params: dict = None, ramp_beats: float = 0,
            at: str = 'now', clear: bool = False, moves: list = None, section: str = None) -> str:
    """Change params of one effect in a live chain. target: a track, or 'bus:<name>'; index: 0-based position in
    its chain (live_status lists chains), or the effect's type ('filter'; 'filter:2' = the second filter), which
    survives edits to the chain. Automatable params (fx_help lists them: cutoff, mix, gain_db, depth ...)
    glide from their current value to the new one over ramp_beats, starting at `at` ('now' or a live_queue
    value): one call = a filter sweep over 8 bars, a fade, a build. Frequencies glide in log space. Other params
    rebuild that chain at `at` (the old tail rings out).
    A chain scheduled with live_track(fx=..., at=...) can be swept before it plays: an `at` at or after its swap
    targets the new chain, and a swap drops the sweeps written for the old one.
    clear=True cancels the scheduled moves on the target from `at` on (one effect with index, or every effect and
    the volume without it): each param holds where it is. Use it before a section reuses a track whose old
    choreography is still scheduled.
    moves: a list of these calls as dicts, [{target, index, params, ramp_beats, at}, ...], scheduled in one call
    (a whole cycle of throws, sweeps and gaps; ismail.live.djkit builds them).
    section: a set section's name on the glides (or 'section' in each move): live_queue(cancel_section=) then
    takes them back with that section's clips."""
    if moves is not None:
        if section:
            moves = [dict(m, section=section) if isinstance(m, dict) and 'section' not in m else m for m in moves]
        return _call(project, 'moves', moves=moves)
    if target is None:
        raise OpError("live_fx needs a target (or moves=[...])")
    if not clear and (index is None or not params):
        raise OpError("live_fx needs index and params (or clear=True to cancel scheduled moves)")
    # section is sent only when given: an engine started before it existed still takes every other call
    return _older_engine(lambda: _call(project, 'fx', target=target, index=index, params=params,
                                       ramp_beats=ramp_beats, at=at, clear=clear,
                                       **({'section': section} if section else {})))


@op()
def live_tempo(project: str, bpm: float, at: str = 'next_bar', ramp_beats: float = 0) -> str:
    """Change the running set's tempo, with no restart and no gap. at: when, in live_queue's words (next_bar,
    next_4, bar:N, now ...). Clips are written in beats, so every part keeps its notes on the beat: from the change
    bar on they render again at the new tempo, while the notes before it keep playing up to it. When those renders
    cannot be ready in time, the change moves to a later bar and the reply names it. Beat-synced effects (delays in
    beats, gates, ducks every_beats, LFOs in beats) follow at the same bar; played keys (live_midi) keep their
    sounds. A change replaces any change queued at or after its bar. ramp_beats: not built; for a gradual change
    queue a few step changes at successive bars. The reply says what changes at which bar and what renders again;
    live_status lists the tempo changes still to come."""
    return _call(project, 'tempo', bpm=bpm, at=at, ramp_beats=ramp_beats)


@op()
def live_queue(project: str, clips: list = None, section: str = None, cancel_section: str = None,
               cancel_at: str = None) -> str:
    """Queue clips (a batch; all or nothing). Each clip: {track, notes and/or lanes, bars, loop, at}.
      notes: '<beat> <pitch> <dur_beats> [vel]; ...' with beats from the clip start (same as notes_write).
      lanes: {pitch: 'x...x...'} step strings (same as pattern_write), step = beats per char (0.25).
      bars (or beats, for odd lengths): clip length; default = whole bars covering the notes. It repeats every length.
      loop: repeats before it ends (default 'forever': it plays until something replaces it).
      expr: {lane: [[beat, value], ...]} expression for a performer voice (bend in semitones, vib in cents ...),
          beats from the clip start; the voice's INFO lists its lanes.
      at: next_bar (default) | next_beat | next_2 | next_4 | next_8 | next_16 (phrase boundaries counted from bar 1)
          | asap | bar:<n> | after:<clip id> (when that clip ends; chain clips to pre-program an arc)
          | after:#<k> (when item k of this same batch ends: a whole arc in one call, no ids needed).
    A clip on a track replaces whatever that track would play from its start (a playing clip is cut there; its
    last notes ring out). {track, stop: true, at} silences a track. The reply gives each clip's id and the exact
    bar it lands on (moved later when its first notes cannot render in time) and the runway: how long until the
    queue stops changing. Before a long job (sound design, fitting), queue enough that the runway covers it.
    section: a name for the set section (chapter) these clips and stops belong to; a clip's own 'section' key wins.
    cancel_section: cut over mid-chapter. Takes back what that section still has to do from cancel_at (a live_queue
    `at` value; default where this batch's first clip lands, or the next bar when clips is empty): its clips that
    start then or later are removed, the ones still playing are cut there (last notes ring out), its stops after
    it and its effect moves sent with section= are dropped (a glide in progress holds where it is). The batch's own
    clips are kept even when they reuse the name. Clips queued without a section are never touched."""
    args = {'clips': clips if clips is not None else []}
    # the new arguments go only when given: an engine started before them still takes a plain queue
    args.update({k: v for k, v in (('section', section), ('cancel_section', cancel_section),
                                   ('cancel_at', cancel_at)) if v is not None})
    return _older_engine(lambda: _call(project, 'queue', **args))


@op()
def live_cancel(project: str, clips: list) -> str:
    """Remove clips that have not started yet (ids from live_queue / live_view). A playing clip cannot be
    cancelled: queue {track, stop: true} or a replacement instead."""
    return _call(project, 'cancel', clips=clips)


@op()
def live_view(project: str, bars: int = 8, clip: str = None) -> str:
    """The queue ahead: which clip each track plays at each bar for the next `bars` bars (max 64), plus the
    runway. clip='c3' shows that clip's notes and timing instead."""
    return _call(project, 'view', bars=bars, clip=clip)


@op()
def live_listen(project: str, bars=4, view: str = 'bars', band: str = None, recording: str = None,
                deck: str = None) -> str:
    """Hear the live output: analyse the last `bars` complete bars (max 32; bar numbers are the live set's).
    view: bars (level, bands, centroid, onsets, chord per bar) | envelope (level per 16th, max 8 bars; band=
    sub|bass|lowmid|mid|himid|air) | pitches | drums | chords. This is the audio after the safety chain.
    recording='rec_<time>.wav' (in <project>/live/) analyses a finished live_record take instead, with bars=[a, b]
    in the set's own bar numbers; works after live_stop too. deck='B' hears only that deck, after its strip, even
    while it is cued (off the air): the headphones for preparing the next part."""
    if view not in VIEWS:
        raise OpError(f"view={view!r}; use one of {', '.join(VIEWS)}")
    if recording:
        path = recording if os.path.isabs(recording) else os.path.join(os.path.abspath(project), 'live', recording)
        try:
            with open(path[:-4] + '.json', encoding='utf8') as f:
                meta = json.load(f)
        except (OSError, ValueError):
            raise OpError(f"no recording {path} with its .json sidecar; recordings: " + ', '.join(
                sorted(x for x in os.listdir(os.path.join(os.path.abspath(project), 'live')) if x.endswith('.wav'))))
        bpb, first = meta['beats_per_bar'], meta['first_bar']
        if not isinstance(bars, (list, tuple)) or len(bars) != 2:
            raise OpError(f"with recording=, bars is [first, last] in set bars; this take starts at bar {first}")
        # tempo segments [bar, bpm, seconds into the file]; the analysis grid has one tempo, so bars stay in one
        segs = [[first, meta['bpm'], 0.0]] + [x for x in meta.get('tempo', []) if x[0] > first]
        cross = [x for x in segs[1:] if bars[0] < x[0] <= bars[1]]
        if cross:
            c = cross[0][0]
            raise OpError(f"bars {bars[0]}-{bars[1]} cross the tempo change at bar {c:g} (to {cross[0][1]:g} BPM): "
                          f"ask for [{bars[0]}, {c - 1:g}] and [{c:g}, {bars[1]}] separately")
        bar0, bpm, sec = [x for x in segs if x[0] <= bars[0]][-1] if bars[0] >= first else segs[0]
        g = A.Grid(bpm, sec - (bar0 - 1) * bpb * 60.0 / bpm, bpb)
        kw = {'band': band} if view == 'envelope' else {}
        head = f"{os.path.basename(path)} bars {bars[0]}-{bars[1]} ({view})"
        return head + '\n' + VIEWS[view](path, g, list(bars), **kw)[1]
    d = _call(project, 'listen', bars=bars, deck=deck)
    bar_sec = d['bpb'] * 60.0 / d['bpm']
    g = A.Grid(d['bpm'], -(d['first'] - 1) * bar_sec, d['bpb'])
    rng = [d['first'], d['last']]
    kw = {'band': band} if view == 'envelope' else {}
    txt = VIEWS[view](d['path'], g, rng, **kw)[1]
    return f"live bars {d['first']}-{d['last']} ({view})\n" + (f"note: {d['note']}\n" if d.get('note') else '') + txt


@op()
def live_record(project: str, on: bool = True) -> str:
    """Record the live output (after the safety chain) to <project>/live/rec_<time>.wav; on=False stops it."""
    return _call(project, 'record', on=on)


@op()
@heavy()
def live_parity(song: str, bars: list, tracks: list = None) -> str:
    """Does this song play on a deck as it renders in the studio? Renders bars=[a, b] in the studio and plays them
    on a silent engine of its own (no live_start needed, nothing is late), then compares each track, each bus and
    the mix: level, envelope, octave bands, and the residual (how close to sample for sample; -100 dB and below is
    identical; under -80 dB nobody can tell). Judged from the second bar: in the first, the studio rings in what came before the window and a
    deck starts clean (its own line, 'edge'). Each part is judged at its best lag: within 3 ms it reads 'ok (same,
    offset N samples ...)', further off it DIFFERS (looked for up to 100 ms either way). Each track says how fast it
    renders live (x realtime, in one process, a worker's pace). tracks limits both sides to those tracks. Takes about
    as long as rendering the bars twice. Also api.live_parity."""
    from . import parity
    song = settings.project_dir(song)                     # a bare song name: the songs folder (ledger:M375)
    if not os.path.exists(os.path.join(song, 'project.json')):
        raise OpError(f"no project at {song}")
    if not isinstance(bars, (list, tuple)) or len(bars) != 2:
        raise OpError("bars=[first, last], song bars (inclusive), e.g. [9, 16]")
    try:
        res, _, head = parity.run(song, [int(bars[0]), int(bars[1])], tracks)
    except ValueError as e:
        raise OpError(str(e))
    bad = [k for k, m in res.items() if not parity.verdict(m).startswith('ok') and not k.endswith('(edge)')]
    L = [f"studio vs live, {os.path.basename(song)} bars {bars[0]}-{bars[1]}: "
         + (f"{len(bad)} of {len(res)} differ ({', '.join(bad)})" if bad else f"all {len(res)} match")]
    L.append(parity.report(res, head))
    if bad:
        L.append("  a part that DIFFERS sounds different on a deck than in a render: that is an engine gap. Say so "
                 "(the song's HANDOFF.md, for the dev agent) instead of changing the song to hide it.")
    return '\n'.join(L)


@op()
def live_device(project: str, device: str = 'default', follow: bool = None, reopen: bool = True) -> str:
    """Move a running set to another audio output without stopping it: the timeline, the queue and the audio mixed
    ahead carry on, with a gap of about a second. device: 'default' (the system's default output now: use it after
    connecting a Bluetooth speaker), a name or part of one ('JBL'), an index, or 'none'. With device 'default' the
    engine also follows the system default by itself (checked every few seconds; follow=False stops that), and a
    device that stops taking audio (a speaker switched off) falls back to the default. reopen=False only sets
    follow and stays on the output it has (no gap); the phone page's Output switch does the same.
    device='none' turns the room speaker off mid-set (listening on the phone instead): the clock, the queue, streams,
    recording and live_listen carry on, and device='default' (or a name) turns it back on, no restart."""
    if not reopen:
        return _call(project, 'device', follow=follow, reopen=False, timeout=20)
    return _call(project, 'device', device=device, follow=follow, timeout=20)


@op()
def live_stream(project: str, name: str = 'master') -> str:
    """Where to listen to the set over the network: the master, a bus ('bus:lucy') or a deck ('deck:A') as raw PCM
    (int16 little-endian, stereo, 44.1 kHz) on the engine's localhost port, GET /stream?name=.... A bus streamed on
    its own lets a VR stage play each object's sound at its place (route Lucy's tracks to bus 'lucy'); each stream
    has its own safety limiter. The engine listens on localhost only: a page on another device reaches it through
    its own server, which relays the stream. A stream plays sound wherever it is received: tell the user first."""
    r = _call(project, 'stream', name=name)
    with open(_info_path(project), encoding='utf8') as f:
        port = json.load(f)['port']
    return (f"stream {name}: http://127.0.0.1:{port}{r['path']} | {r['format']} {r['channels']} ch {r['rate']} Hz | "
            f"{r['listening']} listening")


@op()
def live_map(project: str, control: str, target: str = None, param: str = 'volume_db', range: list = None,
             curve: str = 'linear', at: str = 'now', remove: bool = False) -> str:
    """Map a named control (a knob, slider or switch on the VR stage, or any client) to a live parameter, so its
    moves apply in the engine at once with no agent in the loop. target: a track, 'bus:<name>' or 'deck:<name>'.
    param: 'volume_db', 'pan' (tracks), a deck's 'low_db'/'mid_db'/'high_db'/'filter'/'volume_db', or an effect
    param 'fx:<index or type>.<param>' ('fx:filter.cutoff', 'fx:delay.mix'). The control sends 0..1; range=[value
    at 0, value at 1]; curve 'linear', 'log' (Hz, ratios), 'switch' (below 0.5 = the first value), or 'raw' (the
    value passes in the param's own units). at: when a move lands for effect and deck params ('now', 'next_bar').
    Every move is logged by bar: live_controls reads them back as automation. remove=True unmaps."""
    return _call(project, 'map', control=control, target=target, param=param, range=range, curve=curve, at=at,
                 remove=remove)


@op()
def live_control(project: str, control: str, value: float) -> str:
    """Move a mapped control (what the stage sends when a knob turns; an agent can turn one too, the same way).
    value: 0..1, or the param's own units for a 'raw' map. Applies at once and is logged with its bar."""
    r = _call(project, 'control', control=control, value=value)
    return f"{control} = {value:g} -> {r['applied']:g} at {r['bar']}"


@op()
def live_controls(project: str, control: str = None, last: int = 20) -> str:
    """The control moves of this set as automation: for each control, its target and [bar, value] points in this
    engine's bars (the last `last` of them), ready to become automation_set points in a studio project."""
    return _call(project, 'controls', control=control, last=last)


def _check_instrument(project, instrument):
    """An instrument as live_track takes it, checked here (voice modules are imported in this process, never in
    the engine)."""
    from .. import api
    P = api.Project(project) if os.path.exists(os.path.join(os.path.abspath(project), 'project.json')) else None
    if isinstance(instrument, str) and instrument.startswith('track:') and P is None:
        raise OpError(f"instrument {instrument!r} copies a project track, but {project} has no project.json; "
                      f"pass a dict or 'preset:<name>'")
    instrument = api._resolve_instrument(instrument, P or type('Root', (), {'root': os.path.abspath(project)})())
    return _mark_performer(instrument, project)


@op()
def midi_sources() -> str:
    """The MIDI inputs this computer has now (keyboards, pads, loopMIDI ports), by name. A running live engine also
    notices one plugged in and says so; live_midi plays one through the set."""
    from .. import midi
    names = midi.sources()
    if not names:
        return ("no MIDI inputs. A keyboard plugged in over USB shows up here; on Windows, loopMIDI makes a "
                "virtual one (a port named for it, e.g. 'ismail keys'). With no instrument at all, live_keys_page "
                "puts keys on a screen.")
    return "MIDI inputs:\n" + '\n'.join(f"  {n}" for n in names)


@op()
def live_midi(project: str, source: str = None, track: str = None, instrument=None, volume_db: float = None,
              transpose: int = None, channel: int = None, remove: bool = False) -> str:
    """Play a MIDI input (a keyboard, a pad, a loopMIDI port) through the live set: the person plays along from the
    same speakers. source: the input's name or a unique part of it (midi_sources), or 'screen keys' (the on-screen
    keys page, live_keys_page); with no source, says what is plugged in and what is patched. What it plays: track='<live track>' (that track's sound, its baked effects
    included) or instrument=... (as live_track takes it: 'preset:<name>', a dict, a code voice). Call again with
    another track or instrument to swap: the old sound plays until the new notes land. The notes render once on
    the render workers (C2 to C7, or a kit's pads; a few seconds to a minute); a key then sounds one output buffer
    after the press (live_status says how long). Played keys skip the track's effects and the safety limiter (a
    soft clip guards them). volume_db: the keys' level, at most +6 (default: the track's fader, or -6 dB for an
    instrument). transpose: semitones; channel: 1 to 16 (0 = every channel, the
    default). Every key is logged with the beat it sounded against to <project>/live/midi/<source>.jsonl
    (live_played reads it back as notes). remove=True unpatches.
    Tell the person before their keys first sound."""
    if instrument is not None:
        instrument = _check_instrument(project, instrument)
    return _call(project, 'midi', source=source, track=track, instrument=instrument, volume_db=volume_db,
                 transpose=transpose, channel=channel, remove=remove)


@op()
def live_midi_out(project: str, track: str = None, port: str = None, channel: int = None, audio: bool = None,
                  offset_ms: float = None, at: str = 'now', remove: bool = False) -> str:
    """Send a live track's notes out as MIDI to a port: a hardware synth, or a loopMIDI port (Windows) or the IAC
    bus (Mac) into Ableton, FL Studio or any DAW, whose instrument then plays the part. Reach for it when the person
    wants a set's part played by their own synth or plugin, or ismail's notes in their DAW while the set plays.
    track: a live track, or a deck's part ('A.bass'); port: an output's name or a unique part of it (midi_ports
    lists them); channel: 1 to 16 (default: 10 for a kit or drum, else the lowest free one on that port). Every note
    its clips play from `at` (default 'now': the first beat the mixer has not mixed) goes out with its velocity and
    length, transposed with its deck, at the moment its audio would reach the speakers, so the synth lines up with
    the set. audio=False takes the track off the speakers and the render workers (the synth is its sound); True
    brings it back (from its next renders, a few seconds). offset_ms: moves every message on that port later, or
    earlier when negative, to meet a synth's or a DAW's own delay (-500 to 2000; set it by ear against the kick).
    Call again to change channel, audio or offset; another port moves it. remove=True stops it: each held note gets
    its note-off, then All Notes Off (CC 123) on its channel. A stop or the set's end does the same. With no track:
    the outputs, the routes and the clock. Clock out: live_midi_clock."""
    return _call(project, 'midi_out', track=track, port=port, channel=channel, audio=audio, offset_ms=offset_ms,
                 at=at, remove=remove)


@op()
def live_midi_clock(project: str, port: str = None, on: bool = True, position: bool = False,
                    offset_ms: float = None) -> str:
    """Send MIDI clock from the live set to a port, so Ableton, FL Studio or a drum machine follows the set's tempo
    and starts on its bar line. Reach for it when another app or a hardware box should play in time with the set.
    24 pulses per quarter note at the set's tempo (one tempo per set) start at once, so the receiver can lock to it;
    Start goes out right before the first pulse of the next bar line, which becomes the other app's bar 1.
    position=True sends that bar's Song Position Pointer first and Continue instead of Start, for a DAW that follows
    song position (it lands on the same bar number). on=False sends Stop; the set's end sends Stop too. offset_ms:
    as live_midi_out (shared by the port). The other app must take clock in from that port: in Ableton, the
    port's Sync on (Preferences, Link/Tempo/MIDI), then EXT; in FL Studio, the port enabled as an input in MIDI
    settings with its sync on, then external sync. With no port: what is sending (live_status says it too)."""
    return _call(project, 'midi_clock', port=port, on=on, position=position, offset_ms=offset_ms)


@op(mutates=True)
def live_played(project: str, source: str = None, bars=8, fit: bool = False, from_bar: int = None, into: str = None,
                bar: int = 1, mode: str = 'replace', placement: str = 'A') -> str:
    """What the person played on their keys, as notes text: 'bar | +beat | pitch | d= | v=' lines in this engine's
    bars, the same notes format tracks take (note-offs held while the sustain pedal was down). bars: the last
    `bars` bars that had keys, or [a, b] for those bars (ask the player which part; never guess). Per input
    (source: one input). Read it to answer what they played or to quote a phrase back.
    Clock: each note is where it sounded against the beat the player heard (the press plus the keys' delay to the
    speakers, which the header names in ms), because a player by ear lines up what they hear with the kick.
    To loop a take back, use fit=True rather than rounding the lines to the grid: it groups the hits (keys within
    0.15 beat are one), fits a 16th grid with a free offset, says the offset, the rms error in ms, the drift per
    bar and the rhythm's shape in 16ths (3-3-2), and prints the snapped notes twice as live_queue notes (beats from
    the clip start, bar 1 = the take's first bar, or from_bar): A as played, B with the first hit on a downbeat.
    Where the take sits against the kick is a musical choice the data cannot make: play both and let the player
    pick by ear. When every key has the same velocity (a keyboard that sends one), the reply says so and suggests
    an accent shape; the lines keep the velocity.
    Recording into the song: into='<track>' writes the take into that track of the project in this folder (an
    existing track: track_add first, with the sound to keep), one input at a time. Reach for it when the person
    wants to record what they played on their MIDI keyboard into a track of the song (keep a take), instead of
    copying the lines into notes_write. bar: where the take's first bar lands (from_bar picks which of the engine's
    bars is that first bar; default the first bar with a note). mode='replace' clears the bars the take covers on
    the track first, 'add' merges. Without fit it writes the take as it sounded: off the grid where it was played,
    each note's velocity and length, the pedal's holds. With fit=True it writes the snapped notes, placement='A'
    (as played, the default) or 'B' (the groups from beat 1) as the reply named them; ask the player first. The
    reply says what was written, how to hear it, and undo() takes it back."""
    if into is None:
        if bar != 1 or mode != 'replace' or str(placement).upper() != 'A':
            raise OpError("bar, mode and placement say where and how into='<track>' writes the take; add into=, or "
                          "leave them out to read the lines")
        return _call(project, 'played', source=source, bars=bars, fit=fit, from_bar=from_bar)
    return _record(project, into, source, bars, fit, from_bar, bar, mode, placement)


def _record(project, into, source, bars, fit, from_bar, bar, mode, placement):
    """live_played(into=...): the engine's take written into a song track, as midi_read writes a file's notes."""
    from ..api import _clear, _kit_warning, _load
    from ..notation import fmt_num, midi_to_name
    if mode not in ('replace', 'add'):
        raise OpError(f"mode {mode!r}: 'replace' (clears the take's bars on the track first) or 'add' (merges)")
    try:
        bar = int(bar)
    except (TypeError, ValueError):
        bar = 0
    if bar < 1:
        raise OpError("bar: the song bar the take's first bar lands on, from 1")
    P = _load(project)
    if into not in P.d['tracks']:
        raise OpError(f"no track {into!r} in {P.root}: track_add(project={project!r}, name={into!r}, instrument=...) "
                      f"first, with the sound the take should keep (tracks now: {list(P.d['tracks']) or 'none'})")
    tr = P.track(into)
    t = _call(project, 'take', source=source, bars=bars, fit=fit, from_bar=from_bar, placement=placement)
    bpb = P.bpb
    base = P.bar_to_beat(bar)
    rows = [[round(base + s, 6), int(p), d, int(v)] for s, p, d, v in t['notes']]
    last = bar + int((max(r[0] for r in rows) - base) // bpb)
    removed = _clear(tr, base, P.bar_to_beat(last + 1)) if mode == 'replace' else 0
    tr['notes'] = sorted(tr['notes'] + rows)
    P.save()
    pitches, vels = [r[1] for r in rows], [r[3] for r in rows]
    eng = (t['first_bar'], t['first_bar'] + last - bar)
    L = [f"{into}: wrote {len(rows)} notes from {t['source']} (the engine's bars {eng[0]}-{eng[1]}) into bars "
         f"{bar}-{last} ({mode}" + (f", replaced {removed} notes" if removed else '') + f"), "
         f"{midi_to_name(min(pitches))}-{midi_to_name(max(pitches))}, v={min(vels)}-{max(vels)}"]
    L += ['  ' + x for x in t['lines']]
    if abs(t['bpm'] - P.d['bpm']) > 0.01:
        L.append(f"the set plays at {fmt_num(round(t['bpm'], 2))} BPM, the song at {fmt_num(P.d['bpm'])}: the notes "
                 f"keep their beats, so the take plays at the song's tempo")
    if t['bpb'] != bpb:
        L.append(f"the set has {t['bpb']} beats a bar, the song {bpb}: the notes keep their beats, so bar numbers "
                 f"differ")
    if last > P.d.get('length_bars', last):
        L.append(f"the song is {P.d['length_bars']} bars long and the take ends in bar {last}: "
                 f"project_set(length_bars={last}) to hear it all in a full render")
    L.append(f"hear it: render(project={project!r}, bars=[{bar}, {last}]), then play_file the render (say first that "
             f"it plays out loud); notes_read(project={project!r}, track={into!r}, bars=[{bar}, {last}]) shows the "
             f"lines")
    L.append(f"undo: undo(project={project!r}) puts {into} back as it was before this write")
    return '\n'.join(L) + _kit_warning(tr, rows)


@op()
def live_keys_page(project: str, phone: bool = False, sender: str = None) -> str:
    """Optional: an on-screen keyboard for someone with no MIDI instrument who wants to play a part (a jam, a lead
    over the set). A real keyboard needs none of this (midi_sources, live_midi). The page plays straight into this
    project's running engine as the input 'screen keys', with no virtual MIDI port: patch it like any input,
    live_midi(source='screen keys', track=... or instrument=...), and live_played(source='screen keys') reads back
    what they played. It has two to three octaves (two rows on a phone held upright), octave shift, a sustain
    toggle, velocity from where the key is pressed (near the top is soft), multi-touch chords, the mouse, and the
    computer keyboard (Z and Q rows, arrows for octave, Space for the pedal); it says whether it is connected and
    what it plays. The reply gives the address on this computer (the engine serves it) and, while the phone server
    runs, the phone's. phone=True also puts a panel on the phone page with a button that opens the keys (sender:
    your name, as phone_panel_show takes it). The keys sound from this computer's speakers, not in the phone's
    stream, and opening them leaves the phone page (its stream stops): for someone in the room. Tell the person
    before their keys first sound."""
    st = _call(project, 'keys')
    port = (_engine_record(project) or {}).get('port')
    L = [f"keys page here: http://127.0.0.1:{port}/keys (this computer's browser: mouse, touch screen or the "
         f"computer keyboard)"]
    from ..phone import ops as PO
    pport = PO._port()
    if pport is None:
        if phone:
            raise OpError("the phone server is not running: phone_start() first, then live_keys_page(phone=True)")
        L.append("on a phone: phone_start(), then live_keys_page(phone=True)")
    else:
        url, hint = PO._tailnet(pport)
        L.append(f"on the phone: {url}keys" if url else f"on the phone: not reachable yet ({hint})")
        if phone:
            PO._call('panel_show', title='Play along', link='/keys?from=phone', link_label='Open the keys',
                     text='Keys on the screen that play into the set from the speakers in the room. Near the top '
                          'of a key is soft, near the bottom is loud.', who=sender)
            L.append("posted a panel on the phone page with a button that opens the keys")
    if st.get('patched'):
        ready = '' if st['ready'] >= st['of'] else f" ({st['ready']} of {st['of']} notes ready)"
        L.append(f"the keys play {st['what']}{ready}")
    else:
        L.append(f"the keys play nothing yet: live_midi(project={project!r}, source='screen keys', track='<a live "
                 f"track>' or instrument=...) first; until then the page says no sound is chosen")
    L.append("they sound from this computer's speakers; live_played(source='screen keys') reads what was played")
    return '\n'.join(L)
