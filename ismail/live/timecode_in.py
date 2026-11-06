"""MIDI Time Code in (MIDI parity step 8, part 2; plan in D:/ismail-hq/proposals/midi-parity.md): a running set
chases another app's timecode (a DAW, a video player, a tape machine) from a named input: their time running, stopped,
located or running backwards, and where the set's bar lines sit on it.

MTC carries a position, not a tempo. The set keeps its own tempo (live_tempo still works) and lays its bar lines on
their time: their `offset` (a timecode, 00:00:00:00 by default) is their bar 1, and from there their bars run at
the set's tempo. When MIDI clock in is on as well, the clock leads (tempo, beat and transport: it carries a tempo);
timecode in then only reads their time and shows it, with no tempo change and no silence of its own.

Reading. The eight quarter frames of a label come in over two frames; ismail.mtc.QuarterFrames tells the direction
from their order and the time once eight came in order, then every piece moves it a quarter frame on. A broken order
(a lost piece) waits for the next eight. Their arrival times go through clock in's filter (ClockFilter, told how
many quarter frames passed, so a burst of them is never a duplicate), so the jitter of a USB cable or of a DAW that
sends a buffer's worth at once is filtered as clock in filters ticks. Their speed is the filtered period against the
rate's own: a tape or a varispeed DAW running 1% fast runs the set 1% fast while it chases.

Following. Their time running forward (the first eight in order after a stop, a locate, a turn round, or turning it
on) silences the set on the speakers and brings it in on the first of their bar lines its render lead can reach,
the way clock in's Start does (one to two bars; their bar lines are their time from `offset` at the set's tempo).
Then the set keeps its beats on theirs as clock in does: a tempo change at most once per step_beats, only past
tempo_tol_bpm or phase_tol_ms, bending at most max_bend_pct. A jump of more than JUMP_Q quarter frames from where
their time should be (a locate while running), a full-frame SysEx (a locate), and a change of their rate relock the
same way. Running backwards: the set cannot, so it is silent until their time runs forward again.

A dropout: no quarter frame for dropout_ms (250 by default). Under it the set plays on through the gap; past it their
transport has stopped (MTC has no stop message: the quarter frames stop) or the cable is out, and the set goes
silent until their time runs again. A rate= pinned in the command and another rate coming in: not followed, said
in the news and in live_status.

Not yet: placing the set at their time (a live set cannot jump back or ahead; it comes in on its next bar line on
their grid), their tempo map (their bars run at the set's tempo), and the silence reaching live_listen, live_record
and the streams (the speakers only, as with clock in).
"""
import math

from .. import mtc as T
from ..notation import fmt_num
from .clock_in import ClockFilter, ClockIn, same_port
from .timeline import fmt_bar

SNAP = 0.001                # before the steady line, a speed within this of 1x is taken as 1x (a crystal is within
                            # 0.0001)
JUMP_Q = 8                  # their time this many quarter frames (two frames) from where it should be: a jump
DEFAULTS = {'bandwidth_hz': 0.5, 'tempo_tol_bpm': 0.25, 'phase_tol_ms': 4.0, 'max_bend_pct': 3.0,
            'step_beats': None, 'dropout_ms': 250.0, 'offset_ms': 0.0}
RANGES = {'bandwidth_hz': (0.05, 5.0, "Hz: lower is steadier, higher follows a speed change sooner"),
          'tempo_tol_bpm': (0.01, 5.0, "BPM: the smallest tempo difference the set changes for"),
          'phase_tol_ms': (0.5, 50.0, "ms: the largest beat offset left alone"),
          'max_bend_pct': (0.5, 25.0, "% of the tempo a step may bend by while the set is heard"),
          'step_beats': (1, 64, "beats between tempo changes"),
          'dropout_ms': (100.0, 5000.0, "ms with no quarter frame before their time counts as stopped"),
          'offset_ms': (-500.0, 2000.0, "ms: positive lands ismail later, for an app whose sound is late")}


class TimecodeIn(ClockIn):
    def __init__(self, engine):
        super().__init__(engine)
        self.cfg = dict(DEFAULTS)
        self.pin = None             # rate= asked for (None: whatever they send)
        self.zero_f = 0             # their bar 1, frames since 00:00:00:00 (offset=)
        self.zero_raw = None        # offset= as given (a label is read at their rate once it is known)

    def _reset_state(self):
        super()._reset_state()
        self.mode, self.known = 'follow', True    # clock in's follower keeps D: their bar lines are always known
        self.dec = T.QuarterFrames()
        self.f = ClockFilter(self.cfg['bandwidth_hz'] if hasattr(self, 'cfg') else 0.5)
        self.rate = None            # their rate, from the last whole label
        self.state = 'waiting'
        self.running = False
        self.qlast = None           # their position at the last piece, quarter frames (unwrapped)
        self.tlast = None
        self.pieces = 0
        self.at_tc = None           # the label of the last stop, locate or turn round
        self.anchor = None          # (their seconds, their beat): their bar grid from there at bpm0
        self.bpm0 = None            # the set's own tempo their bars run at
        self.passive = False        # clock in leads: read only
        self.jumps = 0

    # ------------------------------------------------------------------ their time
    def _tq(self, rate=None):
        return float(T.frame_s(rate or self.rate)) / 4

    def _zero_s(self):
        r = self.rate or self.pin or 25
        f = T.offset_frames(self.zero_raw, r) if self.zero_raw is not None else self.zero_f
        return T.frames_to_seconds(f, r)

    def _grid(self):
        """(their seconds, their beat, BPM): their bar grid passes through that point at that tempo."""
        if self.anchor is None:
            return self._zero_s(), 0.0, self.bpm0
        return self.anchor[0], self.anchor[1], self.bpm0

    def _t_ext(self, ref, x):
        """When their beat x arrives: its time on their grid, as a quarter frame, on the arrivals' clock."""
        t0, q, Tp = ref
        s0, x0, bpm = self._grid()
        return t0 + ((s0 + (x - x0) * 60.0 / bpm) / self._tq() - q) * Tp

    def _x_at(self, ref, t):
        t0, q, Tp = ref
        s0, x0, bpm = self._grid()
        return x0 + ((q + (t - t0) / Tp) * self._tq() - s0) * bpm / 60.0

    def _secs_of(self, x):
        s0, x0, bpm = self._grid()
        return s0 + (x - x0) * 60.0 / bpm

    def _label(self, sec):
        r = self.rate or self.pin or 25
        return T.fmt_frames(math.floor(T.seconds_to_frames(round(sec, 9), r) + 1e-6), r)

    def now_label(self):
        """Their time now (running: from the last piece on at their rate), or None."""
        if self.qlast is None:
            return None
        q = self.qlast
        if self.running:
            q += (self.now() - self.tlast) / self._tq()
        return self._label(q * self._tq())

    def _early_ref(self):
        """Before the steady line has its 2 s: their own rate's period when the measured one is within SNAP of it,
        with the arrivals' mean offset from it; else the measured one. None while the measured period is not known
        to SNAP / 2 (a line through a quarter second of arrivals with 1 ms of jitter is 0.3% off, and a lock bar
        4 s on would land 12 ms off): about 0.8 s of quarter frames at 1 ms of jitter, the 24 of the warm-up at
        0.1 ms."""
        f, tq = self.f, self._tq()
        N = len(f.hist)
        if N < 3 or f.jit * math.sqrt(12.0 / (N ** 3 - N)) / tq > SNAP / 2:
            return None
        if abs(f.T / tq - 1) > SNAP:
            return f.t0, self.qlast, f.T
        n1 = f.hist[-1][0]
        c = sum(t - (n - n1) * tq for n, t in f.hist) / len(f.hist)
        return c, self.qlast, tq

    def _dropout_s(self):
        return self.cfg['dropout_ms'] / 1000

    # ------------------------------------------------------------------ messages, on the driver's thread
    def msg(self, t, st, d1=0, d2=0):
        if st != T.QF:
            return
        with self.lock:
            if self.source is None:
                return
            self.pieces += 1
            r = self.dec.feed(d1)
            if r is None:
                if self.state == 'waiting':
                    self.state = 'reading'
                return
            if r.complete and self.pin is not None and r.rate != self.pin:
                if self.state != 'wrong rate':
                    self._halt('wrong rate', None)
                    self.e.news.append(f"timecode in: {self.source!r} sends {T.fmt_rate(r.rate)} and rate="
                                       f"{self.pin} was asked: not following. live_midi_timecode_in(rate={r.rate!r}), "
                                       f"or rate='any' to take what they send")
                self.dec.reset()
                return
            if r.direction < 0:
                if self.state != 'reverse':
                    self._halt('reverse', r.tc and T.fmt_tc(r.tc, r.rate))
                    self.e.news.append("timecode in: their time runs backwards"
                                       + (f" (at {T.fmt_tc(r.tc, r.rate)})" if r.tc else '')
                                       + ": the set cannot, so it is silent until their time runs forward")
                return
            if r.complete and self.rate is not None and r.rate != self.rate and self.running:
                self.e.news.append(f"timecode in: their rate changed from {T.fmt_rate(self.rate)} to "
                                   f"{T.fmt_rate(r.rate)}: the set relocks on the first of their bar lines it can "
                                   f"reach")
                self.rate = r.rate
                self._run(t, r, quiet=True)
                return
            if not self.running:
                if r.complete:
                    self.rate = r.rate
                    self._run(t, r)
                return
            q = T.unwrap(r.q, self.qlast, r.rate)
            if r.complete:
                Tp = self.f.T or self._tq()
                pred = self.qlast + (t - self.tlast) / Tp
                if abs(q - pred) > JUMP_Q:
                    self.jumps += 1
                    self.e.news.append(f"timecode in: they jumped from {self._label(pred * self._tq())} to "
                                       f"{T.fmt_tc(r.tc, r.rate)}: the set relocks on the first of their bar lines "
                                       f"it can reach")
                    self._run(t, r, quiet=True)
                    return
            k = q - self.qlast
            if k <= 0:
                return
            self.f.tick(t, k=k)
            self.qlast, self.tlast = q, t

    def sysex(self, t, data):
        ff = T.read_full_frame(data)
        if ff is None:
            return
        tc, rate = ff
        with self.lock:
            if self.source is None:
                return
            label = T.fmt_tc(tc, rate)
            wrong = self.pin is not None and rate != self.pin
            self._halt('wrong rate' if wrong else 'located', label)
            if wrong:
                self.e.news.append(f"timecode in: they located to {label} at {T.fmt_rate(rate)} and rate={self.pin} "
                                   f"was asked: not following. live_midi_timecode_in(rate={rate!r}), or rate='any'")
                return
            self.rate = rate
            self.qlast, self.tlast = 4 * T.tc_to_frames(tc, rate), t
            self.e.news.append(f"timecode in: they located to {label}: the set is silent until their time runs, "
                               f"then comes in on the first of their bar lines it can reach")

    def _run(self, t, r, quiet=False):
        """Their time runs forward from reading r: a fresh filter, the set silent until it locks on their grid."""
        e = self.e
        self.f = ClockFilter(self.cfg['bandwidth_hz'])
        self.f.tick(t)
        # their time as the day nearest their bar 1 has it: just past midnight after an offset late in the day
        # counts on from it
        q = T.unwrap(r.q, self._zero_s() / self._tq(r.rate), r.rate)
        self.qlast, self.tlast = q, t
        self.running, self.D, self.lock_req, self.locked = True, None, True, None
        self.at_tc, self._steps_seen, self.catching = None, 0, False
        self.next_ok = -math.inf
        if self.bpm0 is None:
            self.bpm0 = e.bpm
        self.anchor = None
        self.state = 'locking'
        if not self.passive:
            self._gate(e.played, False)
            if not quiet:
                e.news.append(f"timecode in: their time runs from {T.fmt_tc(r.tc, r.rate)} "
                              f"({T.fmt_rate(r.rate)}): the set comes in on the first of their bar lines it can reach")

    def _halt(self, state, label):
        """Their time stopped, located or turned round: silent until it runs forward. At a rate not asked for, not
        followed: the set plays on by itself."""
        e = self.e
        was = self.running
        self.running, self.lock_req, self.state, self.at_tc = False, False, state, label
        self.dec.reset()
        if not self.passive:
            self._gate(e.played, state == 'wrong rate')
            if was and state != 'wrong rate':
                e.mout.silence()

    # ------------------------------------------------------------------ the follower (the engine's scheduler pass)
    def plan(self):
        if self.source is None:
            return
        e = self.e
        now = self.now()
        cin = e.cin
        with self.lock:
            lead = cin.source is not None
            if lead != self.passive:
                self.passive = lead
                if lead:
                    self._gate(e.played, True)
                    self.lock_req = False
                    e.news.append(f"timecode in: MIDI clock in from {cin.source!r} leads the tempo, beat and "
                                  f"transport; timecode only reads their time now")
                elif self.running:
                    self.lock_req, self.D, self.locked = True, None, None
                    self.state = 'locking'
                    self._gate(e.played, False)
                    e.news.append("timecode in: clock in is off, so timecode leads again: the set comes in on the "
                                  "first of their bar lines it can reach")
            if not self.running:
                return
            if now - self.tlast > self._dropout_s():
                label = self._label(self.qlast * self._tq())
                self._halt('stopped', label)
                e.news.append(f"timecode in: no timecode from {self.source!r} for {self.cfg['dropout_ms']:g} ms after "
                              f"{label}: their transport stopped (or the cable is out); the set is silent until "
                              f"their time runs again")
                return
            if self.passive or not self.f.ready or self.f.confirming:
                return
            st = self.f.steady()
            if st is not None:
                ref = (st[1], self.qlast, st[0])
            else:
                ref = self._early_ref()
                if ref is None:                             # their speed is not known well enough to place a bar
                    return
            est = self.bpm0 * self._tq() / ref[2]
            lock_req, D = self.lock_req, self.D
        a = e.mout.anchor()
        if a is None or not 20.0 <= est <= 400.0:
            return
        if lock_req:
            self._lock(est, ref, a)
            if self.locked is not None and not self.lock_req:
                self.state = 'locked'
        elif D is not None:
            self._follow(est, ref, a, D)

    @staticmethod
    def _lock_spans(m0, ideal, bpb):
        """Both whole-bar spans around the ideal, nearest first: when the nearest bends too far, the other may not,
        and the set comes in a bar sooner."""
        k = max(0.0, (ideal - m0) / bpb)
        return sorted({m0 + math.floor(k) * bpb, m0 + math.ceil(k) * bpb}, key=lambda m: abs(m - ideal))

    def _said_locked(self, xL, bL, est):
        return (f"timecode in: locked, their {self._label(self._secs_of(xL))} = ismail {fmt_bar(bL, self.e.bpb)}; "
                f"the set comes in there at {fmt_num(round(est, 2))} BPM")

    def retempo(self, beat, bpm):
        """live_tempo moved the set's own tempo at ismail `beat`: their bars run at it from the same point of their
        time on (under the engine's lock)."""
        with self.lock:
            if self.source is None:
                return None
            if self.D is not None:
                x = beat - self.D
                self.anchor = (self._secs_of(x), x)
            self.bpm0 = float(bpm)
            self.next_ok = max(self.next_ok, beat)
        return (f"  timecode in: their bar lines run at {fmt_num(bpm)} BPM from {fmt_bar(beat, self.e.bpb)}, the set "
                f"keeps its place on their time")

    # ------------------------------------------------------------------ the command
    def cmd(self, source=None, on=True, rate=None, offset=None, **params):
        e = self.e
        if not on:
            if self.source is None:
                return "timecode in is off"
            return self._off()
        if source is None and self.source is None:
            if rate is not None or offset is not None or any(v is not None for v in params.values()):
                raise ValueError("timecode in is off: name the input to chase (source=..., midi_sources lists them)")
            return self.describe()
        for k, v in params.items():
            if k not in RANGES:
                raise ValueError(f"unknown setting {k!r}; settings: {sorted(RANGES) + ['offset', 'rate']}")
            if v is None:
                continue
            lo, hi, what = RANGES[k]
            try:
                ok = lo <= float(v) <= hi
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ValueError(f"{k}={v!r}: {lo:g} to {hi:g} ({what})")
        pin = T.rate_of(rate) if rate not in (None, 'any') else None
        if offset is not None:
            T.offset_frames(offset, pin or self.rate or 25)          # a wrong offset says so before anything moves
        name = self.source if source is None else self._find(source)
        tco = e.mout.tc
        if tco is not None and same_port(tco['port'], name):
            raise ValueError(f"timecode out already goes to {tco['port']!r}: timecode in from the same port would "
                             f"chase the set's own time. Take timecode in from another port (a second loopMIDI "
                             f"port), or turn timecode out off first: live_midi_timecode(on=False)")
        lines = []
        if name != self.source:
            if self.source is not None:
                self._off(quiet=True)
            with self.lock:
                self._reset_state()
                self.source = name
            try:
                e.midi._open(name)
            except Exception as ex:
                self.source = None
                raise ValueError(f"MIDI input {name!r} did not open: {ex}")
            lines.append(f"timecode in <- {name!r}")
        with self.lock:
            for k, v in params.items():
                if v is not None:
                    self.cfg[k] = int(v) if k == 'step_beats' else float(v)
            if params.get('bandwidth_hz') is not None:
                self.f.B = self.cfg['bandwidth_hz']
            if rate is not None:
                self.pin = pin
                if pin is not None and self.rate is not None and self.rate != pin and self.running:
                    self._halt('wrong rate', None)
            if offset is not None:
                self.zero_raw = offset
                if self.running:                    # a new grid: lock on it again
                    self.lock_req, self.D, self.locked, self.anchor = True, None, None, None
                    self.state = 'locking'
                    if not self.passive:
                        self._gate(e.played, False)
            if self.bpm0 is None:
                self.bpm0 = e.bpm
        zero = self._label(self._zero_s())
        lines.append(f"chases their time ({'rate ' + T.fmt_rate(self.pin) if self.pin else 'any rate they send'}): "
                     f"MTC carries no tempo, so the set keeps its own ({fmt_num(round(e.bpm, 2))} BPM; live_tempo "
                     f"works) and lays its bar lines on their time, their bar 1 at {zero} (offset)")
        lines.append(f"their time running: the set goes silent and comes in on the first of their bar lines it can "
                     f"reach (one to two bars), then keeps its beats within {self.cfg['phase_tol_ms']:g} ms of theirs "
                     f"(offset_ms {self.cfg['offset_ms']:+g}), a tempo change at most every {fmt_num(self._step())} "
                     f"beats, bending at most {self.cfg['max_bend_pct']:g}% while heard")
        lines.append(f"no quarter frame for {self.cfg['dropout_ms']:g} ms: their transport stopped and the set is "
                     f"silent; a locate (full frame or a jump), a turn backwards or a new rate silences it too until "
                     f"their time runs forward again")
        if e.cin.source is not None:
            lines.append(f"MIDI clock in from {e.cin.source!r} is on and leads (tempo, beat and transport); timecode "
                         f"in only reads their time while it is on")
        lines.append(self.line())
        return '\n'.join(lines)

    def _find(self, source):
        from .midi_play import SCREEN
        name = self.e.midi.find(source)
        if name == SCREEN:
            raise ValueError(f"{SCREEN!r} sends no timecode: name a MIDI input (midi_sources lists them)")
        return name

    def _off(self, quiet=False):
        e = self.e
        with self.lock:
            name, self.source = self.source, None
            self._gate(e.played, True)
            n = self.changes
        e.midi.release(name)
        return (f"timecode in off: {name!r} let go; the set plays on at {fmt_num(round(e.bpm, 2))} BPM ({n} tempo "
                f"changes made chasing)")

    # ------------------------------------------------------------------ what it says
    def line(self):
        if self.source is None:
            return "timecode in: off"
        e = self.e
        with self.lock:
            head = f"timecode in <- {self.source!r}"
            if self.state == 'waiting':
                return head + ": waiting for timecode (nothing heard yet)"
            if self.state == 'reading' and self.qlast is None:
                return head + f": reading ({self.pieces} quarter frames, no eight in order yet)"
            parts = []
            label = self.now_label()
            if label:
                parts.append(f"their {label}" + (f" ({T.fmt_rate(self.rate)})" if self.rate else ''))
            if self.passive:
                parts.append(f"reading only: MIDI clock in from {e.cin.source!r} leads the tempo, beat and transport")
            elif self.state == 'stopped':
                parts.append(f"STOPPED (no timecode for {self.now() - self.tlast:.1f} s): the set is silent until "
                             f"their time runs")
            elif self.state == 'located':
                parts.append(f"LOCATED to {self.at_tc}: the set is silent until their time runs")
            elif self.state == 'reverse':
                parts.append("RUNNING BACKWARDS: the set is silent until their time runs forward")
            elif self.state == 'wrong rate':
                parts.append(f"WRONG RATE: rate={self.pin} asked, they send another; not following")
            elif self.state == 'locking':
                parts.append("locking to the first of their bar lines it can reach (silent until then)")
            elif self.locked is not None:
                xL, bL = self.locked
                parts.append(("running" if self.is_open(e.played) else "comes in") +
                             f", their {self._label(self._secs_of(xL))} = ismail {fmt_bar(bL, e.bpb)}")
            if self.running and self.f.T:
                parts.append(f"their speed {self._tq() / self.f.T:.4f}x" + (f" ({self.f.jit * 1000:.1f} ms jitter)"
                                                                           if self.f.ready else ''))
            parts.append(f"set {fmt_num(round(e.bpm, 2))} BPM")
            if self.last_err is not None and not self.passive:
                parts.append(f"phase {self.last_err * 1000:+.1f} ms (+: ismail late)")
            parts.append(f"{self.changes} tempo changes")
        return head + ": " + ' | '.join(parts)

    def describe(self):
        try:
            names = self.e.midi.sources()
        except Exception:
            names = []
        return '\n'.join([f"MIDI inputs: {', '.join(repr(n) for n in names) if names else 'none'}",
                          self.line() if self.source else "timecode in: off (live_midi_timecode_in(source=...) chases "
                          "another app's MIDI Time Code)"])
