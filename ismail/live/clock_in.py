"""MIDI clock in (MIDI parity step 8, part 1; plan in D:/ismail-hq/proposals/midi-parity.md): a running set follows
another app's or a drum machine's MIDI clock from a named input. 24 ticks per quarter note (0xF8) give the tempo and
the beat; Start (0xFA), Continue (0xFB), Stop (0xFC) and the Song Position Pointer (0xF2) give the transport.

The filter. Ticks arrive with jitter (a USB cable, a driver, the other app's own timer). The first WARM ticks (one
beat) are fitted by least squares; then a second-order delay-locked loop (F. Adriaensen, "Using a DLL to filter
time", 2005) takes over: each tick's error against the predicted time e moves the prediction by b * e and the tick
period by c * e, with w = 2 pi B T (B the loop bandwidth in Hz, T the period), b = sqrt(2) w, c = w^2 (critically
damped). Measured in tests/test_midi_clock_in.py: its tempo follows a steady ramp about 0.215 / B seconds late
(430 ms at the default 0.5 Hz, 205 ms at 1 Hz, 95 ms at 2 Hz), settles on a step change within 0.1 BPM in 1.9 s at
0.5 Hz, and holds a clock with 1 ms of jitter within 0.25 BPM. A tick more than half a period late counts the ticks
lost before it; the error taken from one tick is clipped to half a period, so a burst of buffered ticks moves
nothing far. w is held at most 0.5 (the loop goes unstable from sqrt(2)): a high bandwidth on a slow clock runs
lower than asked, and the reply says below which tempo.

Once the tempo holds, a straight line through the last 2 to 16 s of ticks gives the tempo and the beat instead of
the loop: far steadier on a jittery clock, so a steady clock asks for almost no tempo changes. When the loop and the
line differ by more than tempo_tol_bpm for APART_S, the tempo really changed: the line starts again and the loop
leads until it has 2 s of the new tempo. On the engine (same test file): a steady 126 BPM clock with 1 ms
jitter keeps ismail's beats within 4.1 ms of theirs with 0 to 2 tempo changes in 60 s (four seeds); a ramp of 10 BPM
over 16 s trails by at most 32 ms during the ramp and is back within 4.4 ms after it.

The set follows by tempo changes, the way live_tempo makes them: every change needs its notes rendered again, so
the follower changes the tempo at a beat the renders can reach (the render lead), at most once per step_beats. At
each step it predicts the phase error at that beat (when ismail's beat reaches the speakers against when the other
app's beat arrives, plus offset_ms) and sets the tempo for the step so the error is gone at its end, then the
filter's tempo after it. The bend is held within max_bend_pct while the set is heard, so a large error closes over
a few steps. Nothing changes while the error is within phase_tol_ms and the tempo within tempo_tol_bpm.

The transport. transport='follow': Stop silences the set on the speakers at once (a 5 ms fade; the set's time runs
on and its queue keeps playing silent), and Start or Continue bring it back in on a bar line: ismail mixes ahead,
so it cannot land on the other app's first beat; it picks the first of the other app's bar lines its render lead
can reach, runs its tempo there through the silent stretch (0.67 to 1.5 times the tempo) so its own next bar line
lands on it, and opens there ("their bar 3 = ismail bar 17"). Start counts from their bar 1, Continue from the Song
Position Pointer they sent. A Song Position while running is a jump: the set relocks the same way. Turning clock
in on never silences the set: Stop does. transport='tempo' follows the tempo and lands ismail's beats on theirs
and ignores Start, Stop and Song Position (bar lines need not line up).

A dropout (no tick for dropout_ms) holds the last tempo and says so in the news and live_status; the set keeps
playing. When ticks come back, the ticks missed are counted from the time and the follow goes on.

Clock out (midi_out.py) and the arp (arp.py) place their pulses and steps through the engine's tempo map, so they
follow the followed tempo with no code of their own; MIDI out's notes are held back while the speakers are shut.
Not yet: Ableton Link and MIDI Time Code; their meter (ismail's beats per bar is assumed for their bar lines);
their Start and Stop forwarded to clock out; the silence while they are stopped is on the speakers only:
live_listen, live_record and the streams still carry the set.
"""
import collections
import math
import re
import threading

import numpy as np

from ..dsp import SR
from ..notation import fmt_num
from . import graph as G
from .timeline import EPS, fmt_bar

PPQN = 24
WARM = 24                   # ticks fitted by least squares before the loop takes over (one beat)
TICK, SPP, START, CONTINUE, STOP = 0xF8, 0xF2, 0xFA, 0xFB, 0xFC
W_MAX = 0.5                 # the loop's w = 2 pi B T is held at most this (it is unstable from sqrt(2) on)
STEADY_S = (2.0, 16.0)      # the steady tempo: a straight line through the ticks of the last 2 to 16 s
APART_S = 0.25              # the loop and the steady line this long apart by more than tempo_tol_bpm: a real change
FADE_S = 0.005              # the speakers' fade when the transport stops or starts
LOCK_BEND = (0.67, 1.5)     # silent before a lock bar, the set may run this much slower or faster to reach it
LOCK_TRIES = 16             # their bar lines tried for a lock
DEFAULTS = {'bandwidth_hz': 0.5, 'tempo_tol_bpm': 0.25, 'phase_tol_ms': 4.0, 'max_bend_pct': 3.0,
            'step_beats': None, 'dropout_ms': 300.0, 'offset_ms': 0.0}
RANGES = {'bandwidth_hz': (0.05, 5.0, "Hz: lower is steadier, higher follows a tempo change sooner"),
          'tempo_tol_bpm': (0.01, 5.0, "BPM: the smallest tempo difference the set changes for"),
          'phase_tol_ms': (0.5, 50.0, "ms: the largest beat offset left alone"),
          'max_bend_pct': (0.5, 25.0, "% of the tempo a step may bend by while the set is heard"),
          'step_beats': (1, 64, "beats between tempo changes"),
          'dropout_ms': (50.0, 5000.0, "ms with no tick before the clock counts as lost"),
          'offset_ms': (-500.0, 2000.0, "ms: positive lands ismail later, for an app whose sound is late")}


def same_port(a, b):
    """True when an input and an output name the same port (a loopMIDI port has one name for both ends)."""
    norm = lambda s: re.sub(r'\s+', ' ', str(s)).strip().casefold()          # noqa: E731
    return a is not None and b is not None and norm(a) == norm(b)


class ClockFilter:
    """Tick times in, the tick period and the filtered time of the last tick out. Single-threaded: its owner locks."""

    def __init__(self, bandwidth_hz=DEFAULTS['bandwidth_hz']):
        self.B = float(bandwidth_hz)
        self.reset()

    def reset(self):
        self.n = 0                  # tick periods since the first tick
        self._warm = []             # (tick number, time) while warming
        self.T = None               # seconds per tick
        self.t0 = None              # the filtered time of the last tick
        self.t1 = None              # the predicted time of the next
        self.last = None            # the raw arrival time of the last tick
        self.jit = 0.0              # rms of the errors the loop took, s
        self.hist = collections.deque()     # (tick number, arrival time) of the last STEADY_S[1] seconds
        self.since = 0              # the steady line starts at this tick number (a real tempo change restarts it)

    @property
    def ready(self):
        return self.n >= WARM

    def bpm(self):
        return 60.0 / (PPQN * self.T) if self.T else None

    def tick(self, t):
        """A tick arrived at t. Returns the tick periods since the last one (1, more when ticks were lost; 0 for the
        first)."""
        t = float(t)
        if self.last is None:
            self.last, self.t0, self._warm = t, t, [(0, t)]
            return 0
        k = 1
        if self.n >= WARM:                  # not while warming: a period from a few jittery ticks is too rough
            late = t - self.t1
            # lost ticks: later than half a period plus the jitter seen (a late tick at 400 BPM is not a lost one)
            if late > 0.5 * self.T + min(3.0 * self.jit, self.T):
                k = 1 + int(late / self.T + 0.5)
        self.last = t
        if self.n + k < WARM:
            self.n += k
            self._warm.append((self.n, t))
            self._keep(t)
            x = np.array([w[0] for w in self._warm], dtype=float)
            y = np.array([w[1] for w in self._warm]) - self._warm[0][1]
            T, c = np.polyfit(x, y, 1)
            if T > 0:
                self.T = float(T)
                self.t0 = self._warm[0][1] + float(c) + self.T * self.n
                self.t1 = self.t0 + self.T
                if len(x) > 2:
                    self.jit = float(np.sqrt(np.mean((y - (c + T * x)) ** 2)))
            return k
        T = self.T
        self.t1 += (k - 1) * T
        e = max(-0.5 * T, min(0.5 * T, t - self.t1))
        w = self.w()
        self.t0 = self.t1
        self.t1 += math.sqrt(2.0) * w * e + T
        self.T += w * w * e
        self.n += k
        self._keep(t)
        self.jit = math.sqrt(0.98 * self.jit ** 2 + 0.02 * e * e)
        return k

    def w(self):
        return min(2 * math.pi * self.B * self.T, W_MAX)

    def clamped_below(self):
        """The tempo (BPM) below which this bandwidth is held at W_MAX for stability, or None when it never is in
        20 to 400 BPM."""
        b = 60.0 * 2 * math.pi * self.B / (PPQN * W_MAX)
        return b if b > 20.0 else None

    def _keep(self, t):
        self.hist.append((self.n, t))
        while self.hist and t - self.hist[0][1] > STEADY_S[1]:
            self.hist.popleft()

    def restart_steady(self):
        self.since = self.n

    def steady(self):
        """(seconds per tick, the fitted time of the last tick) from a straight line through the ticks since the
        last real tempo change (2 to 16 s of them), or None while there are fewer than 2 s."""
        h = [x for x in self.hist if x[0] >= self.since]
        if len(h) < 3 or h[-1][1] - h[0][1] < STEADY_S[0]:
            return None
        x = np.array([a for a, _ in h], dtype=float)
        y = np.array([b for _, b in h]) - h[0][1]
        T, c = np.polyfit(x - x[-1], y, 1)
        return (float(T), h[0][1] + float(c)) if T > 0 else None


class ClockIn:
    def __init__(self, engine):
        self.e = engine
        self.lock = threading.Lock()
        self.source = None
        self.cfg = dict(DEFAULTS)
        self.mode = 'follow'
        self.f = ClockFilter()
        self.gates = ()             # (played sample, open) from that sample on, in order; open before the first
        self._g = 1.0               # the speakers' gain the last device buffer ended on
        self._reset_state()

    def _reset_state(self):
        self.running = None         # the other app's transport: None unknown, True playing, False stopped
        self.known = False          # their song position is known (a Start or a Continue was seen)
        self.fresh = False          # the next tick is the first after Start / Continue
        self.pos_next = 0           # the song position (ticks) the next tick has after Start / Continue
        self.pos = 0                # the song position (ticks) of the last tick
        self.count = 0              # ticks since clock in was turned on (the position when theirs is unknown)
        self.D = None               # ismail's beat minus theirs, once locked
        self.lock_req = False
        self.locked = None          # (their beat, ismail beat) of the last lock bar
        self.next_ok = -math.inf    # the earliest beat the follower decides again at
        self.dropped = None         # when the clock went missing (the last tick's time)
        self.changes = 0
        self.last_err = None        # the phase error at the last decision, s (+: ismail late)
        self.events = []            # transport messages seen, newest last (their names)
        self.ignored = 0
        self._apart = None          # when the loop and the steady line first disagreed

    def now(self):
        return self.e.mout.now()

    @property
    def lag(self):
        return self.e.safety.la + G.LAT_BUDGET

    # ------------------------------------------------------------------ the speakers' gate (transport='follow')
    def _gate(self, sample, opened):
        """From played sample `sample` on the speakers are open or shut; a later transition is replaced."""
        self.gates = tuple(g for g in self.gates if g[0] < sample) + ((int(sample), bool(opened)),)

    def is_open(self, sample):
        st = True
        for s, o in self.gates:
            if s > sample:
                break
            st = o
        return st

    def gain(self, played, n):
        """The speakers' gain over the device buffer [played, played + n), or None for all 1."""
        gates = self.gates
        if not gates and self._g == 1.0:
            return None
        base = True
        inside = []
        for s, o in gates:
            if s <= played:
                base = o
            elif s < played + n:
                inside.append((s - played, o))
        if not inside and self._g == (1.0 if base else 0.0):
            if base and gates and gates[-1][0] <= played and self.lock.acquire(blocking=False):
                try:                                    # all passed and open: nothing left to apply
                    if self.gates is gates:
                        self.gates = ()
                finally:
                    self.lock.release()
            return None if base else np.zeros(n, dtype=np.float32)
        step = 1.0 / (FADE_S * SR)
        g = np.empty(n, dtype=np.float32)
        cuts = [0] + [i for i, _ in inside] + [n]
        tgts = [base] + [o for _, o in inside]
        cur = self._g
        for (a, b), tg in zip(zip(cuts, cuts[1:]), tgts):
            if b <= a:
                continue
            want = 1.0 if tg else 0.0
            r = cur + np.sign(want - cur) * step * np.arange(1, b - a + 1)
            r = np.minimum(r, want) if want >= cur else np.maximum(r, want)
            g[a:b] = r
            cur = float(r[-1])
        self._g = cur
        return g

    # ------------------------------------------------------------------ messages, on the driver's thread
    def msg(self, t, st, d1=0, d2=0):
        with self.lock:
            if self.source is None:
                return
            if st == TICK:
                self._tick(t)
                return
            if st not in (START, CONTINUE, STOP, SPP):
                return
            name = {START: 'Start', CONTINUE: 'Continue', STOP: 'Stop', SPP: 'Song Position'}[st]
            self.events = (self.events + [name])[-4:]
            if self.mode != 'follow':
                self.ignored += 1
                return
            e = self.e
            if st == SPP:
                spp = (d1 & 0x7F) | ((d2 & 0x7F) << 7)
                self.pos_next = spp * 6                     # 16ths -> ticks
                if self.running:
                    self.known, self.fresh, self.lock_req = True, True, True
                    self._gate(e.played, False)
                    e.news.append(f"clock in: they jumped to song position {spp} (bar "
                                  f"{fmt_num(spp / 4 / e.bpb + 1)}); the set relocks on a bar line")
                return
            if st == STOP:
                if self.running:
                    self.pos_next = self.pos + 1                # Continue goes on from the next tick
                was, self.running, self.lock_req = self.running, False, False
                self._gate(e.played, False)
                if was is not False:
                    e.news.append(f"clock in: Stop from {self.source!r}: the set is silent until Start or Continue "
                                  f"(its time and queue run on)")
                return
            if self.f.last is not None and t - self.f.last > self.cfg['dropout_ms'] / 1000:
                self.f.reset()                              # their clock starts again with the transport
            if st == START:
                self.pos_next = 0
            self.running, self.known, self.fresh, self.lock_req = True, True, True, True
            self._gate(e.played, False)
            e.news.append(f"clock in: {name} from {self.source!r}: the set comes in on the first of their bar lines "
                          f"it can reach")

    def _tick(self, t):
        f = self.f
        gap = None if f.last is None else t - f.last
        if gap is not None and gap > self.cfg['dropout_ms'] / 1000 and not (self.mode == 'follow' and self.running):
            f.reset()                                       # no position to carry over: their beats start again
            self.D = None
        k = f.tick(t)
        self.count += k
        if self.mode == 'follow' and self.running:
            if self.fresh:
                self.pos, self.fresh = self.pos_next, False
            else:
                self.pos += k

    def _ref(self):
        """(filtered time of the last tick, its position in ticks, seconds per tick): theirs as a line in time."""
        p = self.pos if (self.mode == 'follow' and self.known) else self.count
        return self.f.t0, p, self.f.T

    @staticmethod
    def _t_ext(ref, x):
        """When their beat x arrives (on the ticks' clock)."""
        t0, p, T = ref
        return t0 + (x * PPQN - p) * T

    # ------------------------------------------------------------------ the follower (the engine's scheduler pass)
    def plan(self):
        """Called under the engine's lock every scheduler pass: decide a tempo change, a lock, or nothing."""
        if self.source is None:
            return
        e = self.e
        now = self.now()
        with self.lock:
            last, ready = self.f.last, self.f.ready
            if last is None:
                return
            stopped = self.mode == 'follow' and self.running is False
            if now - last > self.cfg['dropout_ms'] / 1000:
                if self.dropped is None and not stopped:    # a stopped app may well stop its clock too
                    self.dropped = last
                    e.news.append(f"clock in: no clock from {self.source!r} for {self.cfg['dropout_ms']:g} ms: "
                                  f"holding {fmt_num(round(e.bpm, 2))} BPM (the set plays on)")
                return
            if self.dropped is not None and not stopped:
                e.news.append(f"clock in: the clock from {self.source!r} is back after "
                              f"{last - self.dropped:.1f} s; following again")
            self.dropped = None
            if not ready:
                return
            ref = self._ref()
            st = self.f.steady()
            if st is not None:
                if abs(60.0 / (PPQN * st[0]) - 60.0 / (PPQN * ref[2])) > self.cfg['tempo_tol_bpm']:
                    if self._apart is None:
                        self._apart = now
                    elif now - self._apart > APART_S:       # the loop has gone somewhere else: a real change
                        self.f.restart_steady()
                        self._apart, st = None, None
                else:
                    self._apart = None
            if st is not None:                              # steady: the line, far quieter than the loop
                ref = (st[1], ref[1], st[0])
            est = 60.0 / (PPQN * ref[2])
            running, lock_req, fresh = self.running, self.lock_req, self.fresh
        a = e.mout.anchor()
        if a is None:
            return
        if not 20.0 <= est <= 400.0:
            return
        follow = self.mode == 'follow'
        if follow and running is False:
            self._tempo_only(est)                           # stopped: their position stands still
        elif follow and lock_req:
            if not fresh:                                   # their first tick after it gives their position
                self._lock(est, ref, a)
        else:
            self._follow(est, ref, a)

    def _earliest(self, bpm):
        """The first whole beat a tempo change can land on: past the mix, behind the renders it needs."""
        e = self.e
        now = e.beat(e.pos)
        want = math.ceil(now - EPS)
        if want < now + EPS:
            want += 1
        ready = e.beat(e.pos + int(e._tempo_lead_s(want, bpm) * SR))
        return max(want, math.ceil(ready - EPS))

    def _change(self, beat, bpm):
        self.e._tempo_change(beat, bpm)
        self.changes += 1

    def _tempo_only(self, est):
        e = self.e
        b = max(math.ceil(e.beat(e.pos) - EPS) + 1, self.next_ok)
        if abs(e.tmap.at_beat(b)[2] - est) <= self.cfg['tempo_tol_bpm']:
            return
        b0 = self._earliest(est)
        if b0 < self.next_ok - EPS:                         # one change per step of the mix
            return
        self._change(b0, est)
        self.next_ok = b0 + self._step()

    def _step(self):
        return float(self.cfg['step_beats'] or self.e.bpb)

    def _err(self, beat, ref, a):
        """Seconds ismail's `beat` reaches the speakers after their beat for it arrives (plus offset_ms)."""
        return self.e.mout.wall(beat, 0.0, a) - (self._t_ext(ref, beat - self.D) + self.cfg['offset_ms'] / 1000)

    def _follow(self, est, ref, a):
        e = self.e
        off = self.cfg['offset_ms'] / 1000
        if self.D is None:                                  # first lock on the beat: the nearest of their beats
            heard = e.mout.beat_at(self.now(), a)
            x = (self.now() - off - ref[0]) / ref[2] / PPQN + ref[1] / PPQN
            self.D = float(round(heard - x))
        bp = max(math.ceil(e.beat(e.pos) - EPS) + 1, self.next_ok)
        err = self._err(bp, ref, a)
        cur = e.tmap.at_beat(bp)[2]
        if abs(err) * 1000 <= self.cfg['phase_tol_ms'] and abs(cur - est) <= self.cfg['tempo_tol_bpm']:
            self.last_err = err
            return
        b0 = self._earliest(est)
        if b0 < self.next_ok - EPS:                         # one decision per step of the mix
            return
        err = self._err(b0, ref, a)
        self.last_err = err
        L = self._step()
        span = L * 60.0 / est - err
        bend = self.cfg['max_bend_pct'] / 100
        bpm1 = 60.0 * L / span if span > 0 else est * (1 + bend)
        bpm1 = min(max(bpm1, est * (1 - bend)), est * (1 + bend), 400.0)
        bpm1 = max(bpm1, 20.0)
        self._change(b0, bpm1)
        if abs(bpm1 - est) > 1e-9:
            self._change(b0 + L, est)
        self.next_ok = b0 + L

    def _lock(self, est, ref, a):
        """Their transport just started: the set (silent since) lands its next reachable bar line on theirs."""
        e = self.e
        bpb = e.bpb
        off = self.cfg['offset_ms'] / 1000
        b0 = self._earliest(est)
        W0 = e.mout.wall(b0, 0.0, a)
        x0 = (W0 - off - ref[0]) / ref[2] / PPQN + ref[1] / PPQN       # their beat at ismail's b0
        first = math.ceil(b0 / bpb + EPS) * bpb                         # ismail's first bar line after b0
        xb = math.ceil(x0 / bpb + EPS) * bpb
        lo, hi = LOCK_BEND
        pick = None
        for i in range(LOCK_TRIES):
            xL = xb + i * bpb
            dt = self._t_ext(ref, xL) + off - W0
            if dt <= 0:
                continue
            ideal = dt * est / 60.0
            m = first - b0 + max(0, round((ideal - (first - b0)) / bpb)) * bpb
            bpm = 60.0 * m / dt
            if lo * est <= bpm <= hi * est and 20.0 <= bpm <= 400.0:
                pick = (xL, b0 + m, bpm)
                break
        if pick is None:
            return                                          # try again next pass
        xL, bL, bpm = pick
        self._change(b0, bpm)
        if abs(bpm - est) > 1e-9:
            self._change(bL, est)
        self.D = bL - xL
        with self.lock:
            if not self.lock_req:                           # a Stop came in meanwhile: stay silent
                return
            self.lock_req = False
            self._gate(e.sample(bL) + self.lag, True)
        self.locked = (xL, bL)
        self.next_ok = bL
        e.news.append(f"clock in: locked, their {fmt_bar(xL, bpb)} = ismail {fmt_bar(bL, bpb)}; the set comes in "
                      f"there at {fmt_num(round(est, 2))} BPM")

    # ------------------------------------------------------------------ the command
    def cmd(self, source=None, on=True, transport=None, **params):
        e = self.e
        if not on:
            if self.source is None:
                return "clock in is off"
            return self._off()
        if source is None and self.source is None:
            if transport is not None or any(v is not None for v in params.values()):
                raise ValueError("clock in is off: name the input to follow (source=..., midi_sources lists them)")
            return self.describe()
        for k, v in params.items():
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
        if transport is not None and transport not in ('follow', 'tempo'):
            raise ValueError(f"transport={transport!r}: 'follow' (Stop silences the set, Start and Continue bring "
                             f"it in on their bar line) or 'tempo' (tempo and beat only)")
        name = self.source if source is None else self._find(source)
        clk = e.mout.clock
        if clk is not None and same_port(clk['port'], name):
            raise ValueError(f"clock out already goes to {clk['port']!r}: clock in from the same port would hear its "
                             f"own clock and chase itself. Take clock in from another port (a second loopMIDI port), "
                             f"or turn clock out off first: live_midi_clock(on=False)")
        lines = []
        if name != self.source:
            if self.source is not None:
                self._off(quiet=True)
            with self.lock:
                self.f = ClockFilter(self.cfg['bandwidth_hz'])
                self._reset_state()
                self.source = name
            try:
                e.midi._open(name)
            except Exception as ex:
                self.source = None
                raise ValueError(f"MIDI input {name!r} did not open: {ex}")
            lines.append(f"clock in <- {name!r}")
        with self.lock:
            for k, v in params.items():
                if v is not None:
                    self.cfg[k] = int(v) if k == 'step_beats' else float(v)
            if params.get('bandwidth_hz') is not None:
                self.f.B = self.cfg['bandwidth_hz']
            if transport is not None and transport != self.mode:
                self.mode = transport
                self.D, self.lock_req = None, False
                if transport == 'tempo':
                    self._gate(e.played, True)
        B = self.cfg['bandwidth_hz']
        lines.append(f"follows its tempo: a {B:g} Hz loop filter (a steady tempo change shows about "
                     f"{0.215 / B * 1000:.0f} ms late), a tempo change at most every {fmt_num(self._step())} beats, "
                     f"at the first beat its renders can reach; beats kept within {self.cfg['phase_tol_ms']:g} ms "
                     f"of theirs (offset {self.cfg['offset_ms']:+g} ms), bending at most "
                     f"{self.cfg['max_bend_pct']:g}% while heard")
        cb = self.f.clamped_below()
        if cb is not None:
            lines.append(f"below {cb:.0f} BPM the loop runs below {B:g} Hz: it is held there to stay stable")
        if self.mode == 'follow':
            lines.append("transport follow: their Stop silences the set (its time and queue run on); Start or "
                         "Continue bring it back in on the first of their bar lines it can reach. transport='tempo' "
                         "ignores their transport")
        else:
            lines.append("transport tempo: their Start, Stop and Song Position are ignored; ismail's beats land on "
                         "theirs, bar lines need not")
        lines.append(f"no tick for {self.cfg['dropout_ms']:g} ms: the last tempo holds. live_tempo is refused "
                     f"while clock in is on; live_status shows the follow")
        lines.append(self.line())
        return '\n'.join(lines)

    def _find(self, source):
        from .midi_play import SCREEN
        name = self.e.midi.find(source)
        if name == SCREEN:
            raise ValueError(f"{SCREEN!r} sends no clock: name a MIDI input (midi_sources lists them)")
        return name

    def _off(self, quiet=False):
        e = self.e
        with self.lock:
            name, self.source = self.source, None
            self._gate(e.played, True)
            n = self.changes
        e.midi.release(name)
        return f"clock in off: {name!r} let go; the set holds {fmt_num(round(e.bpm, 2))} BPM ({n} tempo changes " \
               f"followed); live_tempo works again"

    # ------------------------------------------------------------------ what it says
    def line(self):
        if self.source is None:
            return "clock in: off"
        e = self.e
        with self.lock:
            f, src = self.f, self.source
            head = f"clock in <- {src!r}"
            if f.last is None:
                return head + ": waiting for clock (nothing heard yet)"
            parts = []
            if f.T:
                parts.append(f"theirs {fmt_num(round(f.bpm(), 2))} BPM" +
                             (f" ({f.jit * 1000:.1f} ms jitter)" if f.ready else " (warming up)"))
            parts.append(f"set {fmt_num(round(e.bpm, 2))} BPM")
            stopped = self.mode == 'follow' and self.running is False
            if not stopped and (self.dropped is not None or self.now() - f.last > self.cfg['dropout_ms'] / 1000):
                parts.append(f"NO CLOCK for {self.now() - f.last:.1f} s: holding the last tempo")
            if self.mode == 'follow':
                if self.running is False:
                    parts.append("STOPPED: the set is silent until Start or Continue")
                elif self.lock_req:
                    parts.append(f"{self.events[-1] if self.events else 'Start'}: locking to their next bar line")
                elif self.locked is not None:
                    xL, bL = self.locked
                    gate_open = self.is_open(e.played)
                    parts.append(("running" if gate_open else "comes in") + f", their {fmt_bar(xL, e.bpb)} = ismail "
                                 f"{fmt_bar(bL, e.bpb)}")
                else:
                    parts.append("transport unknown (no Start or Continue yet): beats locked, bars not")
            else:
                parts.append("transport ignored (tempo only)" + (f", {self.ignored} transport messages" if
                                                                 self.ignored else ''))
            if self.last_err is not None:
                parts.append(f"phase {self.last_err * 1000:+.1f} ms (+: ismail late)")
            parts.append(f"{self.changes} tempo changes")
        return head + ": " + ' | '.join(parts)

    def status_lines(self):
        return [self.line()] if self.source is not None else []

    def describe(self):
        try:
            names = self.e.midi.sources()
        except Exception:
            names = []
        return '\n'.join([f"MIDI inputs: {', '.join(repr(n) for n in names) if names else 'none'}",
                          self.line() if self.source else "clock in: off (live_midi_clock_in(source=...) follows "
                          "another app's or a drum machine's MIDI clock)"])
