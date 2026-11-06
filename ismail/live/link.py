"""Ableton Link (MIDI parity step 8b): a running set joins a Link session (Ableton Live, Bitwig, a phone app, any
Link app on the same network or computer), follows its tempo and beat phase, leads it, or both, and optionally
shares its start and stop.

Link is an add-on. It comes from the PyPI package aalink (GPLv3+, it includes Ableton's Link library), installed with
the extra `ismail[link]`. ismail stays MIT: nothing here imports aalink until live_link is called (load_aalink), and
without it the call says what to run.

Threads. aalink is asyncio based: a Link must be made inside a running event loop, and its tempo, peers and start
and stop callbacks come through that loop (loop.call_soon_threadsafe from Link's own threads). LinkSession runs the
loop on a thread of its own, polls the Link there every POLL_S (and on every callback), and keeps a snapshot under its
lock: when (on the engine's clock) the session's beat was read, that beat, the tempo, the peers, playing, the
quantum. The engine reads only the snapshot, and the snapshot is a line in time (their beat at t is beat + (t - t0) *
tempo / 60), so a snapshot a few ms old places their next bar line as well as a fresh one. Commands (a tempo, a beat
request, the quantum, start and stop) are posted to the loop and never waited for; each has a number, and the
snapshot says the last one done, so the follower waits for its own request before it reads the beat again. Closing
runs on the loop: the callbacks come off, Link is disabled, the Link object goes (its threads end in its destructor,
with the GIL released), then the loop stops and the thread is joined. A callback already queued when close starts
finds the session closed and does nothing. threaded=False runs everything on the caller's thread (the tests, with a
stand-in Link).

Following. Link gives a timeline, not ticks: no filter is needed. The follower is clock in's (ClockIn: the lock on a
bar line, the tempo steps, the bend) with Link's line as its reference and Link's quantum as their bar length. Their
bar lines are the beats where Link's phase is 0 (multiples of the quantum, ismail's beats per bar by default).
Joining waits JOIN_S for peers to be found. Alone, the session's beat is set to the set's (request_beat), so nothing
is heard to change. With peers, the set goes silent and comes in on the session's next bar line it can reach (one to
two bars), as clock in's Start does. A peer's tempo change is followed as clock in follows a step (the set's change
lands after the render lead, so it bends up to STEP_BEND_PCT to catch the beat up); the beat off by more than
RELOCK_BEATS, or their timeline jumping (a peer forcing its beat, two sessions meeting), relocks the same way.

Leading. lead=True with follow: live_tempo works and its change goes to the session at the moment its bar is heard,
so the session and the set change together. follow=False, lead=True: ismail is the session's clock: its tempo and
its beat go to the session at once (force_beat: the peers jump to ismail's bar lines, as Link does for an external
clock), a peer's tempo change is set back, and a drift past phase_tol_ms is forced again (at most once a bar).

Start and stop (start_stop=True: Link's start/stop sync). Following, the session's stop silences the set on the
speakers (its time and queue run on, as clock in's Stop) and its start brings it in on the next bar line; leading,
joining starts the session and leaving (or the set's end) stops it.

Not yet: bridging MIDI clock in or timecode in into a Link session (one leader of the set's tempo at a time), Link's
own start time for the set's bar 1, and the stopped silence reaching live_listen, live_record and the streams.
"""
import asyncio
import collections
import concurrent.futures
import importlib
import math
import sys
import threading
from dataclasses import dataclass

from ..notation import fmt_num
from .clock_in import PPQN, ClockIn
from .timeline import EPS, fmt_bar

POLL_S = 0.01               # the loop reads the session this often (and on every callback)
SAMPLE_S = 0.002            # a beat read that took longer (the GIL elsewhere) is read again
JUMP_BEATS = 0.05           # their beat this far off its own line between two readings: their timeline jumped
JOIN_S = 1.0                # joining waits this long for peers to be found before it decides
RELOCK_BEATS = 1.0          # the set's beat this far off theirs: relock on their next bar line
HOLD_S = 0.05               # a led tempo change: the follow waits this long after it reaches the session
START_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 3.0
SOURCE = 'Ableton Link'
INSTALL = 'pip install "ismail[link]"'
DEFAULTS = {'tempo_tol_bpm': 0.25, 'phase_tol_ms': 4.0, 'max_bend_pct': 3.0, 'step_beats': None, 'offset_ms': 0.0}
RANGES = {'tempo_tol_bpm': (0.01, 5.0, "BPM: the smallest tempo difference the set changes for"),
          'phase_tol_ms': (0.5, 50.0, "ms: the largest beat offset left alone"),
          'max_bend_pct': (0.5, 25.0, "% of the tempo a step may bend by while the set is heard"),
          'step_beats': (1, 64, "beats between tempo changes"),
          'offset_ms': (-500.0, 2000.0, "ms: positive lands ismail later, for an app whose sound is late"),
          'quantum': (1, 64, "beats from one of the session's bar lines to the next")}


class LinkMissing(ValueError):
    pass


def missing_text():
    exe = sys.executable
    return (f"Ableton Link needs the add-on aalink, which is not installed in the Python that runs this set ({exe}). "
            f"Install it there: {INSTALL} (or \"{exe}\" -m pip install aalink; from uvx: uvx --from \"ismail[link]\" "
            f"ismail-mcp), then call live_link again. aalink is GPLv3+ (it includes Ableton's Link library); ismail "
            f"stays MIT and loads it only when live_link is called. Nothing was installed.")


def load_aalink():
    """aalink.Link, imported now (never when ismail is imported), or LinkMissing saying what to install."""
    importlib.invalidate_caches()           # installed since the last try: found without a restart
    try:
        mod = importlib.import_module('aalink')
        return mod.Link
    except ImportError:
        raise LinkMissing(missing_text()) from None


@dataclass(frozen=True)
class Snap:
    t: float                # when the beat was read, on the engine's clock
    beat: float             # the session's beat then (Link's beat at our quantum)
    tempo: float
    peers: int
    playing: bool
    quantum: float
    enabled: bool
    start_stop: bool
    jumps: int              # times their timeline jumped (not by our own requests)
    done: int               # the last command applied
    starts: int             # times playing changed

    def beat_at(self, t):
        return self.beat + (t - self.t) * self.tempo / 60.0


class LinkSession:
    """One Link, on a loop of its own (threaded) or on the caller's thread. make(bpm): aalink.Link or a stand-in with
    its attributes (beat, tempo, num_peers, playing, quantum, enabled, start_stop_sync_enabled, request_beat,
    force_beat, the add_/remove_ callbacks)."""

    def __init__(self, make, bpm, quantum, now, threaded=True, start_stop=False):
        self.make, self.now, self.threaded = make, now, threaded
        self._lock = threading.Lock()
        self._snap = None
        self._closed = False
        self._link = None
        self._seq = 0
        self._done = 0
        self._expect = False                # our own request moved the beat: the next reading is no jump
        self._later = []                    # inline: (time, seq, kind, args) to apply when it comes
        self.problems = collections.deque(maxlen=8)
        self._cbs = (('tempo', self._on_event), ('num_peers', self._on_event), ('start_stop', self._on_event))
        self.loop = self.thread = None
        args = (float(bpm), float(quantum), bool(start_stop))
        if not threaded:
            self._boot(*args)
            return
        ready = concurrent.futures.Future()
        self.thread = threading.Thread(target=self._run, args=(ready,) + args, name='ismail-link', daemon=True)
        self.thread.start()
        try:
            ready.result(START_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            self._closed = True
            raise RuntimeError(f"Ableton Link did not start in {START_TIMEOUT_S:g} s")

    # ------------------------------------------------------------------ the loop's thread
    def _boot(self, bpm, quantum, start_stop):
        L = self.make(bpm)
        L.quantum = quantum
        L.start_stop_sync_enabled = start_stop
        for name, cb in self._cbs:
            getattr(L, f'add_{name}_callback')(cb)
        L.enabled = True
        self._link = L
        self._poll()

    def _run(self, ready, *args):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop

        async def boot():
            self._boot(*args)
            loop.create_task(self._poller())
        try:
            loop.run_until_complete(boot())
        except BaseException as ex:
            loop.close()
            ready.set_exception(ex)
            return
        ready.set_result(True)
        try:
            loop.run_forever()
        finally:
            tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in tasks:
                t.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            loop.close()

    async def _poller(self):
        while not self._closed:
            self._poll()
            await asyncio.sleep(POLL_S)

    def _on_event(self, *_):
        """A tempo, peers or start/stop callback (on the loop): read the session again at once."""
        if not self._closed:
            self._poll()

    def _poll(self):
        L = self._link
        if L is None or self._closed:
            return
        try:
            for _ in range(3):
                t0 = self.now()
                b = float(L.beat)
                t1 = self.now()
                if t1 - t0 <= SAMPLE_S:
                    break
            else:
                return                                  # keep the last reading
            tempo, peers, playing = float(L.tempo), int(L.num_peers), bool(L.playing)
            q, en, ss = float(L.quantum), bool(L.enabled), bool(L.start_stop_sync_enabled)
        except Exception as ex:                         # a Link going away under a late callback
            self.problems.append(f"reading the session failed ({type(ex).__name__}: {ex})")
            return
        t = (t0 + t1) / 2
        with self._lock:
            if self._closed:
                return
            p = self._snap
            jumps, starts = (p.jumps, p.starts + (playing != p.playing)) if p else (0, 0)
            if p is not None and not self._expect:
                dt = t - p.t
                if abs(b - p.beat_at(t)) > JUMP_BEATS + dt * abs(tempo - p.tempo) / 60.0:
                    jumps += 1
            self._expect = False
            self._snap = Snap(t, b, tempo, peers, playing, q, en, ss, jumps, self._done, starts)

    def _apply(self, seq, kind, args):
        L = self._link
        if L is None or self._closed:
            return
        try:
            if kind == 'tempo':
                L.tempo = float(args[0])
            elif kind in ('request', 'force'):
                b0, t0, bpm = args
                b = b0 + (self.now() - t0) * bpm / 60.0
                (L.request_beat if kind == 'request' else L.force_beat)(b)
                self._expect = True
            elif kind == 'quantum':
                L.quantum = float(args[0])
                self._expect = True                     # the beat's numbering may move with it
            elif kind == 'start_stop':
                L.start_stop_sync_enabled = bool(args[0])
            elif kind == 'playing':
                L.playing = bool(args[0])
        except Exception as ex:
            self.problems.append(f"{kind} {args!r} failed ({type(ex).__name__}: {ex})")
        self._done = max(self._done, seq)
        self._poll()

    # ------------------------------------------------------------------ the engine's side
    def snapshot(self):
        if not self.threaded and not self._closed:
            self._due()
            self._poll()
        with self._lock:
            return self._snap

    def _due(self):
        now = self.now()
        due = [x for x in self._later if x[0] <= now]
        self._later = [x for x in self._later if x[0] > now]
        for _, seq, kind, args in sorted(due):
            self._apply(seq, kind, args)

    def post(self, kind, *args, at=None):
        """Send a command to the Link; it is applied on the loop, never waited for. at: a time on the engine's
        clock to apply it at (a led tempo change, when its bar is heard). Returns its number (Snap.done says when it
        was done; one applied at a time does not count there)."""
        with self._lock:
            if self._closed:
                return 0
            self._seq += 1
            seq = self._seq
        if not self.threaded:
            if at is not None and at > self.now():
                self._later.append((at, seq, kind, args))
            else:
                self._apply(seq, kind, args)
            return seq
        try:
            if at is None:
                self.loop.call_soon_threadsafe(self._apply, seq, kind, args)
            else:
                self.loop.call_soon_threadsafe(self._at, seq, kind, args, at)
        except RuntimeError:                            # the loop closed under us
            pass
        return seq if at is None else 0

    def _at(self, seq, kind, args, at):
        self.loop.call_later(max(0.0, at - self.now()), self._apply, -1, kind, args)

    def drain(self):
        out = []
        while self.problems:
            out.append(self.problems.popleft())
        return out

    def close(self, stop_playing=False):
        """Leave the session: callbacks off, Link disabled and dropped on its own thread, the loop stopped and the
        thread joined. Returns a problem as text, or None."""
        with self._lock:
            if self._closed:
                return None
            self._closed = True
            self._snap = None
        if not self.threaded:
            return self._teardown(stop_playing)
        box = []
        try:
            self.loop.call_soon_threadsafe(lambda: box.append(self._teardown(stop_playing)) or self.loop.stop())
        except RuntimeError:
            return None                                 # the loop already ended
        self.thread.join(CLOSE_TIMEOUT_S)
        if self.thread.is_alive():
            return f"Link's thread did not end in {CLOSE_TIMEOUT_S:g} s (it is left behind; the set plays on)"
        return box[0] if box else None

    def _teardown(self, stop_playing):
        L, self._link = self._link, None
        if L is None:
            return None
        problem = None
        try:
            for name, cb in self._cbs:
                getattr(L, f'remove_{name}_callback')(cb)
            if stop_playing:
                L.playing = False
            L.enabled = False
        except Exception as ex:
            problem = f"leaving the session: {type(ex).__name__}: {ex}"
        del L                                           # the last reference: Link's destructor runs here
        return problem


class LinkIn(ClockIn):
    """The set and an Ableton Link session: follows it, leads it, or both (live_link)."""

    def __init__(self, engine, factory=None, threaded=True):
        super().__init__(engine)
        self.cfg = dict(DEFAULTS)
        self.factory = factory                  # None: aalink.Link (loaded when live_link is called)
        self.threaded = threaded
        self.session = None
        self.follow_on, self.lead_on, self.start_stop = True, False, False
        self.q = None                           # quantum asked for (None: the set's beats per bar)

    def _reset_state(self):
        super()._reset_state()
        self.mode, self.known = 'follow', True  # their bar lines are always known (phase 0 of the quantum)
        self.running = True
        self.state = 'joining'
        self.snap = None
        self.joined_at = None
        self.wait_seq = 0
        self.jumps_seen = None
        self.starts_seen = None
        self.peers_seen = None
        self.prev_tempo = None
        self.hold_until = -math.inf
        self.next_force = -math.inf
        self.next_push = -math.inf
        self.forces = 0
        self.pushed = 0
        self.relocks = 0
        self.wild = False

    def now(self):
        return self.e.mout.now()

    def _their_bar(self):
        s = self.snap
        return s.quantum if s is not None else float(self.q or self.e.bpb)

    def _off_s(self):
        return self.cfg['offset_ms'] / 1000

    def _heard(self, t, a):
        """ismail's beat heard at t + offset_ms (their beat then, when the set is on it), and its tempo."""
        e = self.e
        h = e.mout.beat_at(t + self._off_s(), a)
        return h, e.tmap.at_beat(h)[2]

    # ------------------------------------------------------------------ the follower (the engine's scheduler pass)
    def plan(self):
        s = self.session
        if s is None:
            return
        e = self.e
        snap = s.snapshot()
        for p in s.drain():
            e.news.append(f"Link: {p}")
        a = e.mout.anchor()
        if snap is None or a is None:
            return
        now = self.now()
        with self.lock:
            if self.session is not s:
                return
            self._peers_news(snap)
            self.snap = snap
            if snap.done < self.wait_seq:
                return                                  # our own request is not done yet
            if not 20.0 <= snap.tempo <= 400.0:
                if not self.wild:
                    self.wild = True
                    e.news.append(f"Link: the session runs at {fmt_num(round(snap.tempo, 2))} BPM, outside the "
                                  f"set's 20 to 400: not followed until it is back inside")
                return
            self.wild = False
            if self.state == 'joining':
                if now - self.joined_at >= JOIN_S:
                    self._decide(snap, now, a)
                return
            if not self.follow_on:
                self._lead_pass(snap, now, a)
                return
            self._transport(snap)
            if self.jumps_seen != snap.jumps:
                if self.jumps_seen is not None and self.running:
                    self._relock("the session's beat jumped (a peer moved it, or two sessions met)")
                self.jumps_seen = snap.jumps
            stepped = self.prev_tempo is not None and abs(snap.tempo - self.prev_tempo) > self.cfg['tempo_tol_bpm']
            self.prev_tempo = snap.tempo
            if now < self.hold_until:
                return                                  # a led tempo change is on its way to the session
            if stepped and self.D is not None:
                self.catching = True                    # a peer's step: catch their beat up, as clock in does
            running, lock_req, D = self.running, self.lock_req, self.D
        est = snap.tempo
        ref = (snap.t, snap.beat * PPQN, 60.0 / (PPQN * est))
        if running is False:
            self._tempo_only(est)                       # stopped: keep their tempo, silent
            return
        if lock_req:
            self._lock(est, ref, a)
            with self.lock:
                if self.locked is not None and not self.lock_req:
                    self.state = 'locked'
            return
        if D is None:
            return
        bp = max(math.ceil(e.beat(e.pos) - EPS) + 1, self.next_ok)     # past a lock bar's bend, as _follow
        err = self._err(bp, ref, a, D)
        if abs(err) * est / 60.0 > RELOCK_BEATS:
            with self.lock:
                self._relock(f"the set's beat is {abs(err) * est / 60.0:.1f} beats off the session's")
            return
        self._follow(est, ref, a, D)

    def _peers_news(self, snap):
        if self.peers_seen is not None and snap.peers != self.peers_seen:
            self.e.news.append(f"Link: {self._peers(snap.peers)} in the session now (was {self.peers_seen})")
        self.peers_seen = snap.peers

    @staticmethod
    def _peers(n):
        return f"{n} peer" + ('' if n == 1 else 's')

    def _decide(self, snap, now, a):
        """JOIN_S after joining (under self.lock): alone, the session takes the set's beat; with peers, the set
        locks on theirs; leading, the session takes the set's tempo and beat."""
        e = self.e
        h, bpm = self._heard(now, a)
        self.jumps_seen, self.starts_seen, self.prev_tempo = None, snap.starts, snap.tempo
        if self.lead_on and self.start_stop and not snap.playing:
            self.session.post('playing', True)
        if not self.follow_on:
            self.session.post('tempo', bpm)
            self.wait_seq = self.session.post('force', h, now, bpm)
            self.D, self.state, self.forces = 0.0, 'leading', 1
            self.next_force = now + e.bpb * 60.0 / bpm
            e.news.append(f"Link: leading {self._peers(snap.peers)}: the session takes the set's "
                          f"{fmt_num(round(bpm, 2))} BPM and its bar lines")
            return
        self.running = bool(snap.playing) or not self.start_stop or self.lead_on
        if snap.peers == 0:
            self.wait_seq = self.session.post('request', h, now, bpm)
            self.D, self.state, self.locked = 0.0, 'locked', None
            e.news.append(f"Link: no peers yet: the session's beat is the set's ({fmt_num(round(bpm, 2))} BPM); "
                          f"a peer that joins takes it")
        elif self.running:
            self._relock(f"joined {self._peers(snap.peers)} at {fmt_num(round(snap.tempo, 2))} BPM", quiet=True)
            e.news.append(f"Link: joined {self._peers(snap.peers)} at {fmt_num(round(snap.tempo, 2))} BPM: the set "
                          f"goes silent and comes in on the session's next bar line it can reach")
        if not self.running:
            self.state = 'stopped'
            self._gate(e.played, False)
            e.news.append("Link: the session is stopped (start/stop sync): the set is silent until a peer starts it")

    def _relock(self, why, quiet=False):
        e = self.e
        self.lock_req, self.D, self.locked, self.state = True, None, None, 'locking'
        self.catching = False
        self.relocks += 1
        self._gate(e.played, False)
        if not quiet:
            e.news.append(f"Link: {why}: the set goes silent and comes in on the session's next bar line it can "
                          f"reach")

    def _transport(self, snap):
        """start_stop: their stop silences the set, their start brings it in on their next bar line."""
        if self.starts_seen == snap.starts or not self.start_stop:
            self.starts_seen = snap.starts
            return
        self.starts_seen = snap.starts
        e = self.e
        if not snap.playing and self.running:
            self.running, self.lock_req, self.state = False, False, 'stopped'
            self._gate(e.played, False)
            e.mout.silence()                            # notes held on their synths go off with the set
            e.news.append("Link: the session stopped: the set is silent until it starts (its time and queue run on)")
        elif snap.playing and not self.running:
            self.running = True
            self._relock("the session started", quiet=True)
            e.news.append("Link: the session started: the set comes in on the session's next bar line it can reach")

    def _lead_pass(self, snap, now, a):
        """follow=False, lead=True (under self.lock): the session keeps the set's tempo and bar lines."""
        e = self.e
        if now < self.hold_until:
            return
        h, bpm = self._heard(now, a)
        if abs(snap.tempo - bpm) > self.cfg['tempo_tol_bpm'] and now >= self.next_push:
            self.session.post('tempo', bpm)
            self.pushed += 1
            self.next_push = now + 1.0
            e.news.append(f"Link: the session went to {fmt_num(round(snap.tempo, 2))} BPM; ismail leads "
                          f"(follow=False), so it is set back to {fmt_num(round(bpm, 2))} BPM")
            return
        err = (snap.beat_at(now) - h) * 60.0 / bpm      # +: their beat ahead, ismail late
        self.last_err = err
        if abs(err) * 1000 > self.cfg['phase_tol_ms'] and now >= self.next_force:
            self.wait_seq = self.session.post('force', h, now, bpm)
            self.forces += 1
            self.next_force = now + e.bpb * 60.0 / bpm

    def retempo(self, beat, bpm):
        """live_tempo at ismail `beat` (under the engine's lock): leading, the session takes it when that beat is
        heard. Returns a line for the reply, or None."""
        with self.lock:
            if self.session is None or not self.lead_on:
                return None
            e = self.e
            a = e.mout.anchor()
            at = (e.mout.wall(beat, 0.0, a) if a is not None else self.now()) - self._off_s()
            self.session.post('tempo', float(bpm), at=at)
            self.hold_until = max(self.hold_until, at + HOLD_S)
            self.next_ok = max(self.next_ok, beat)
            peers = self.snap.peers if self.snap is not None else 0
        return (f"  Link: the session goes to {fmt_num(bpm)} BPM when {fmt_bar(beat, e.bpb)} is heard "
                f"({self._peers(peers)} " + ('follows' if peers == 1 else 'follow') + " it)")

    def refuses_tempo(self):
        """live_tempo's refusal while the set follows the session and does not lead it, or None."""
        if self.session is None or self.lead_on:
            return None
        return ("the set follows the Ableton Link session, so its tempo is the session's: change it in the other "
                "app, or live_link(lead=True) to lead the session from live_tempo too, or live_link(on=False) to "
                "leave. The tempo did not change.")

    def _said_locked(self, xL, bL, est):
        return (f"Link: locked, the session's bar line = ismail {fmt_bar(bL, self.e.bpb)}; the set comes in there at "
                f"{fmt_num(round(est, 2))} BPM")

    # ------------------------------------------------------------------ the command
    def cmd(self, on=True, follow=None, lead=None, quantum=None, start_stop=None, **params):
        e = self.e
        if not on:
            if self.session is None:
                return "Link is off (live_link() joins the session)"
            return self._leave()
        for k, v in list(params.items()) + [('quantum', quantum)]:
            if k not in RANGES:
                raise ValueError(f"unknown setting {k!r}; settings: {sorted(RANGES)}")
            if v is None:
                continue
            lo, hi, what = RANGES[k]
            try:
                ok = lo <= float(v) <= hi
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ValueError(f"{k}={v!r}: {lo:g} to {hi:g} ({what})")
        joining = self.session is None
        fol = (True if joining else self.follow_on) if follow is None else bool(follow)
        led = (False if joining else self.lead_on) if lead is None else bool(lead)
        ss = (False if joining else self.start_stop) if start_stop is None else bool(start_stop)
        if not fol and not led:
            raise ValueError("follow=False and lead=False leave Link nothing to do: follow=True follows the session's "
                             "tempo and beat, lead=True leads it with the set's own, both is a peer like any other. "
                             "live_link(on=False) leaves the session")
        for f, call in ((e.cin, 'live_midi_clock_in'), (e.tcin, 'live_midi_timecode_in')):
            if f.source is not None:
                kind = 'MIDI clock in' if f is e.cin else 'timecode in'
                raise ValueError(f"{kind} from {f.source!r} leads the set's tempo now; Link would be a second leader. "
                                 f"{call}(on=False) first, then live_link")
        lines = []
        if joining:
            make = self.factory or load_aalink()
            q = float(quantum) if quantum is not None else float(e.bpb)
            try:
                session = LinkSession(make, e.bpm, q, self.now, threaded=self.threaded, start_stop=ss)
            except LinkMissing:
                raise
            except Exception as ex:
                raise ValueError(f"Ableton Link did not start: {type(ex).__name__}: {ex}")
            with e.lock, self.lock:
                self._reset_state()
                for k, v in params.items():
                    if v is not None:
                        self.cfg[k] = int(v) if k == 'step_beats' else float(v)
                self.session, self.source = session, SOURCE
                self.follow_on, self.lead_on, self.start_stop, self.q = fol, led, ss, quantum
                self.joined_at = self.now()
            lines.append(f"Link: joined (quantum {q:g}); in {JOIN_S:g} s, once its peers are found, the set "
                         + ("follows" if fol and not led else "follows and leads" if fol else "leads") + " the session")
        else:
            with e.lock, self.lock:
                s = self.session
                for k, v in params.items():
                    if v is not None:
                        self.cfg[k] = int(v) if k == 'step_beats' else float(v)
                if quantum is not None and float(quantum) != self._their_bar():
                    self.q = quantum
                    self.wait_seq = s.post('quantum', float(quantum))
                    if self.follow_on and self.state != 'joining':
                        self._relock(f"quantum {float(quantum):g}", quiet=True)
                    lines.append(f"Link: quantum {float(quantum):g}: the session's bar lines are every "
                                 f"{float(quantum):g} beats" + (", and the set relocks on the next" if self.follow_on
                                                               else ''))
                if ss != self.start_stop:
                    self.start_stop = ss
                    s.post('start_stop', ss)
                    self.starts_seen = None if not ss else (self.snap.starts if self.snap else None)
                    if not ss and self.running is False:
                        self.running = True
                        self._relock("start/stop sync off", quiet=True)
                    if ss and self.lead_on and self.snap is not None and not self.snap.playing:
                        s.post('playing', True)
                if fol != self.follow_on and self.state != 'joining':
                    # following starts or stops: decide again at once, as at the join (lead alone changes nothing
                    # heard: following, it only lets live_tempo through)
                    self.state, self.joined_at = 'joining', self.now() - JOIN_S
                    self.lock_req, self.locked, self.catching = False, None, False
                    if not fol:
                        self._gate(e.played, True)
                self.follow_on, self.lead_on = fol, led
        lines += self._describe_lines()
        lines.append(self.line())
        return '\n'.join(lines)

    def _describe_lines(self):
        c = self.cfg
        out = []
        if self.follow_on:
            out.append(f"follows: the session's tempo and beat; alone, the session takes the set's beat; with "
                       f"peers the set goes silent and comes in on the session's next bar line (one to two bars). "
                       f"Then beats within {c['phase_tol_ms']:g} ms of theirs (offset_ms {c['offset_ms']:+g}), a "
                       f"tempo change at most every {fmt_num(self._step())} beats, bending at most "
                       f"{c['max_bend_pct']:g}% while heard")
        if self.lead_on and self.follow_on:
            out.append("leads too: live_tempo works, and its change reaches the session when its bar is heard")
        elif self.lead_on:
            out.append("leads: the session takes the set's tempo and bar lines (its peers jump to them), a peer's "
                       "tempo change is set back, and live_tempo's changes reach it when their bar is heard")
        else:
            out.append("live_tempo is refused while the set only follows (live_link(lead=True) leads too)")
        if self.start_stop:
            out.append("start/stop sync on: " + ("the session's stop silences the set, its start brings it in on "
                                                 "the next bar line" if self.follow_on else
                                                 "joining starts the session, leaving stops it"))
        return out

    def _leave(self):
        e = self.e
        with e.lock, self.lock:
            s, self.session, self.source = self.session, None, None
            self._gate(e.played, True)
            n = self.changes
            stop = self.lead_on and self.start_stop
        problem = s.close(stop_playing=stop)
        return (f"Link: left the session ({n} tempo changes followed); the set plays on at "
                f"{fmt_num(round(e.bpm, 2))} BPM and live_tempo is its own again"
                + ("; the session was stopped (start/stop sync)" if stop else '')
                + (f". {problem}" if problem else ''))

    def close(self):
        """The set's end: leave the session quietly."""
        s, self.session, self.source = self.session, None, None
        if s is not None:
            s.close(stop_playing=self.lead_on and self.start_stop)

    # ------------------------------------------------------------------ what it says
    def line(self):
        s = self.session
        if s is None:
            return "Link: off"
        e = self.e
        with self.lock:
            snap = self.snap if self.snap is not None else s.snapshot()
            if snap is None:
                return "Link: joining (no reading yet)"
            now = self.now()
            q = snap.quantum
            ph = snap.beat_at(now) % q
            parts = [self._peers(snap.peers) if snap.peers else "alone (no peers yet)",
                     f"session {fmt_num(round(snap.tempo, 2))} BPM, beat {ph + 1:.2f} of {q:g}"]
            parts.append("follows and leads" if self.follow_on and self.lead_on else
                         "follows" if self.follow_on else "leads")
            if self.state == 'joining':
                parts.append(f"joining: looking for peers ({max(0.0, JOIN_S - (now - self.joined_at)):.1f} s left)")
            elif self.state == 'stopped':
                parts.append("STOPPED (start/stop sync): the set is silent until the session starts")
            elif self.state == 'locking':
                parts.append("locking to the session's next bar line (silent until then)")
            elif self.state == 'leading':
                parts.append(f"the session keeps the set's bar lines ({self.forces} beat forced, {self.pushed} tempo "
                             f"set back)")
            elif self.locked is not None:
                parts.append(("running" if self.is_open(e.played) else "comes in") +
                             f", the session's bar line = ismail {fmt_bar(self.locked[1], e.bpb)}")
            elif self.D is not None:
                parts.append("running, the session's beat is the set's")
            if self.start_stop:
                parts.append("start/stop sync on, " + ("playing" if snap.playing else "stopped"))
            parts.append(f"set {fmt_num(round(e.bpm, 2))} BPM")
            if self.last_err is not None:
                parts.append(f"phase {self.last_err * 1000:+.1f} ms (+: ismail late)")
            parts.append(f"{self.changes} tempo changes")
        return "Link: " + ' | '.join(parts)

    def describe(self):
        return self.line() if self.session else "Link: off (live_link() joins the Ableton Link session)"
