"""MIDI out from a live set (ledger:M113 step 3, Nate 10-23: "full midi like ableton"; plan in
D:/ismail-hq/proposals/midi-parity.md): a track's notes to an output port and channel (a hardware synth, or a
loopMIDI / IAC port into Ableton or FL Studio), and MIDI clock so another app or a drum machine follows the set.

Timing. The engine mixes about half a second ahead of the device, so a note is not sent when it is mixed: it is
sent when its audio would reach the speakers. Every device pull stamps (wall time, samples handed over); the least
of (time - samples / SR) over the last ANCHOR_S is where sample 0 left, so a beat's speaker time is that anchor plus
the beat's sample, the path lookahead and the safety limiter's lookahead (as the device gets it), plus the output's
latency. A port's offset_ms moves its messages later (or earlier, negative) to meet a synth or a DAW's own delay.
The engine's scheduler pass (every 20 ms) plans the messages that fall within LOOK_S of now into the sender
(ismail.midisend.Scheduler), which puts each on its port at its time from its own thread.

A routed track sends every note its clips play from the route's start, transposed with its deck (kits never are),
the way its audio plays them: a clip cut by the next keeps the notes it started. audio=False keeps its notes off
the speakers (and off the render workers): the synth is its sound. Note-offs on unroute, a removed track, a stop
and the set's end: each held note's own note-off, then All Notes Off (CC 123) on its channel; closing a port resets
it too.

Clock: 24 pulses per quarter note on the engine's beats (one tempo per set, so the pulses keep the set's tempo).
Turned on, it pulses at once (a receiver locks to the tempo), and Start (0xFA) goes out on the next bar line, right
before that bar's first pulse: the other app's bar 1 is that bar. position=True sends the Song Position Pointer of
that bar first, then Continue (0xFB), so a DAW that follows song position lands on the same bar number. Stop (0xFC)
when turned off and when the set ends.
"""
import collections
import itertools
import math
import threading
import time

from .. import midi as M
from .. import midisend as MS
from ..dsp import SR
from ..notation import fmt_num
from . import graph as G
from .clock_in import same_port
from .timeline import EPS, fmt_bar

LOOK_S = 0.3                # messages are handed to the sender this long before their time
ANCHOR_S = 2.0              # device pulls kept for the speaker-time anchor (the least offset among them)
JUMP_S = 0.5                # an anchor this much later than the last (the device stalled or reopened): start over
DRUM_CH = 9                 # channel 10: drums, as every GM synth and DAW expects
MIN_DUR = 1 / 64            # beats: a shorter note still gets a gap between its note-on and note-off
OFFSET_MS = (-500.0, 2000.0)


def _is_drum(inst):
    from .. import instruments
    t = (inst or {}).get('type')
    return t == 'kit' or t in instruments.DRUM_DEFAULTS


class MidiOut:
    def __init__(self, engine, outputs=None, opener=None, now=None, thread=True):
        """outputs, opener: stand-ins for midi.outputs and midi.Output; now: the clock (time.perf_counter); thread
        False: no sender thread (tests pump the scheduler on a fake clock)."""
        self.e = engine
        self.outputs = outputs or M.outputs
        self.opener = opener or M.Output
        self.now = now or time.perf_counter
        self.thread = thread
        self.sched = MS.Scheduler(self.now)
        self.lock = threading.RLock()
        self.ports = {}             # name -> {'out', 'notes': Notes, 'offset': s, 'users': set}
        self.routes = {}            # track -> {'port', 'ch', 'audio', 'from', 'planned', 'sent'}
        self.clock = None           # {'port', 'start', 'k', 'position', 'started', 'pulses'}
        self.pulls = collections.deque()
        self._anchor = None
        self._tok = itertools.count(1)
        self.ending = None          # time the set's end sends its note-offs and Stop at
        self.ended = False

    # ------------------------------------------------------------------ the clock the speakers run on
    def pulled(self, played):
        """From the device's pull, before `played` grows: the samples handed over so far, stamped now."""
        t = self.now()
        off = t - played / SR
        if self.pulls and off - self.pulls[-1][1] > JUMP_S:
            self.pulls.clear()
        self.pulls.append((t, off))
        while len(self.pulls) > 2 and t - self.pulls[0][0] > ANCHOR_S:
            self.pulls.popleft()
        self._anchor = min(o for _, o in self.pulls)       # here, on the one thread that changes the pulls

    def anchor(self):
        return self._anchor

    def _out_s(self):
        return self.e.midi.out_latency() / SR

    def _lag(self):
        return self.e.safety.la + G.LAT_BUDGET

    def wall(self, beat, offset=0.0, a=None):
        """When `beat` of the set reaches the speakers, on the `now` clock (plus a port's offset)."""
        a = self.anchor() if a is None else a
        return a + (self.e.sample(beat) + self._lag()) / SR + self._out_s() + offset

    def beat_at(self, t, a=None):
        a = self.anchor() if a is None else a
        return self.e.beat((t - a - self._out_s()) * SR - self._lag())

    # ------------------------------------------------------------------ ports
    def find(self, port):
        try:
            return M.find_output(port, self.outputs())
        except M.MidiError as ex:
            raise ValueError(str(ex))

    def _port(self, name, offset_ms=None):
        p = self.ports.get(name)
        if p is None:
            try:
                out = self.opener(name)
            except M.MidiError as ex:
                raise ValueError(str(ex))
            p = self.ports[name] = {'out': out, 'notes': MS.Notes(out), 'offset': 0.0, 'users': set()}
        if offset_ms is not None:
            lo, hi = OFFSET_MS
            if not lo <= float(offset_ms) <= hi:
                raise ValueError(f"offset_ms: {lo:g} to {hi:g} (negative sends earlier, for a synth or DAW that "
                                 f"sounds late)")
            p['offset'] = float(offset_ms) / 1000
        return p

    def _let_go(self, name):
        p = self.ports.get(name)
        if p is not None and not p['users']:
            try:
                p['out'].close()
            except Exception:
                pass
            del self.ports[name]

    def _start(self):
        if self.thread:
            self.sched.start()

    # ------------------------------------------------------------------ routes: a track's notes to a port
    def route(self, track=None, port=None, channel=None, audio=None, offset_ms=None, at='now', remove=False):
        e = self.e
        if track is None:
            return self.describe()
        with e.lock, self.lock:
            r = self.routes.get(track)
            if remove:
                if r is None:
                    raise ValueError(f"track {track!r} sends no MIDI; routed: {list(self.routes) or 'none'}")
                n = self._unroute(track)
                return f"{track}: MIDI out off ({r['sent']} notes sent, {n} held notes turned off)" + \
                    ("; its audio is back from its next renders" if not r['audio'] else '')
            if track not in e.tracks:
                part = next((k for k, t in e.tracks.items() if t['deck']), None)
                raise ValueError(f"no live track {track!r}; tracks: {list(e.tracks) or 'none'}"
                                 + (f" (a deck's parts are '<deck>.<track>', e.g. {part!r})" if part else ''))
            if r is None and port is None:
                raise ValueError(f"track {track!r} sends no MIDI yet: name the port (port=..., midi_ports lists the "
                                 f"outputs)")
            name = self.find(port) if port is not None else r['port']
            if channel is not None and not 1 <= int(channel) <= 16:
                raise ValueError("channel: 1 to 16 (10 is drums)")
            msg = []
            if r is not None and name != r['port']:
                audio = r['audio'] if audio is None else audio
                self._unroute(track)
                r = None
            p = self._port(name, offset_ms)
            if r is None:
                ch = int(channel) - 1 if channel is not None else self._free_channel(name, e.tracks[track]['inst'])
                start, note = e.tl.resolve_at(at, e.beat(e.pos), e.beat(e.pos))
                r = self.routes[track] = {'port': name, 'ch': ch, 'audio': True, 'from': start, 'planned': start,
                                          'sent': 0}
                p['users'].add(('track', track))
                msg.append(f"{track} -> {name!r} channel {ch + 1}" + (" (drums)" if ch == DRUM_CH else '')
                           + f" from {fmt_bar(start, e.bpb)}" + (f" ({note})" if note else ''))
            elif channel is not None and int(channel) - 1 != r['ch']:
                p['notes'].release(owner=track, all_off=False)
                r['ch'] = int(channel) - 1
                msg.append(f"{track}: now on channel {r['ch'] + 1}")
            if audio is not None and bool(audio) != r['audio']:
                r['audio'] = bool(audio)
                self._audio(track, r)
                msg.append(f"{track}: audio {'on' if r['audio'] else 'off (the synth is its sound)'}")
            self._start()
            return '\n'.join(msg + [self.line(track, r)])

    def _free_channel(self, port, inst):
        if _is_drum(inst):
            return DRUM_CH
        used = {r['ch'] for r in self.routes.values() if r['port'] == port}
        free = [c for c in range(16) if c != DRUM_CH and c not in used]
        return free[0] if free else 0

    def _audio(self, track, r):
        """Track audio off (no renders, nothing placed from the route's start) or back on (renders again)."""
        e = self.e
        tr = e.tracks.get(track)
        if tr is None:
            return
        tr['midi_only'] = not r['audio']
        e._rerender_from(track, max(r['from'], e.beat(e.pos)) if not r['audio'] else e.beat(e.pos))

    def _unroute(self, track):
        r = self.routes.pop(track)
        self.sched.drop(tag=('track', track))
        p = self.ports.get(r['port'])
        n = 0
        if p is not None:
            shared = any(o['port'] == r['port'] and o['ch'] == r['ch'] for o in self.routes.values())
            n = p['notes'].release(owner=track, channels=() if shared else (r['ch'],), all_off=not shared)
            p['users'].discard(('track', track))
            self._let_go(r['port'])
        if not r['audio'] and track in self.e.tracks:
            r['audio'] = True
            self._audio(track, r)
        return n

    # ------------------------------------------------------------------ clock out
    def clock_cmd(self, port=None, on=True, position=False, offset_ms=None):
        e = self.e
        with e.lock, self.lock:
            if not on:
                if self.clock is None:
                    return "clock out is off"
                c = self._clock_off()
                return f"clock out off: Stop sent to {c['port']!r} after {c['pulses']} pulses"
            if port is None and self.clock is None:
                return self.describe()
            name = self.find(port) if port is not None else self.clock['port']
            cin = getattr(e, 'cin', None)
            if cin is not None and cin.source is not None and same_port(cin.source, name):
                raise ValueError(f"clock in already follows {cin.source!r}: clock out to the same port would send the "
                                 f"set's clock back to itself and it would chase its own tempo. Send clock out to "
                                 f"another port (a second loopMIDI port), or turn clock in off first: "
                                 f"live_midi_clock_in(on=False)")
            if self.clock is not None:
                if name == self.clock['port'] and bool(position) == self.clock['position']:
                    if offset_ms is not None:
                        self._port(name, offset_ms)
                    return self.clock_line()
                self._clock_off()
            p = self._port(name, offset_ms)
            p['users'].add(('clock',))
            now = e.beat(e.pos)
            start = math.ceil(now / e.bpb - EPS) * e.bpb
            if start < now + EPS:
                start += e.bpb
            a = self.anchor()
            k0 = math.ceil((self.beat_at(self.now() + 0.02, a) if a is not None else now) * MS.PPQN - 1e-9)
            self.clock = {'port': name, 'start': start, 'k': min(k0, int(round(start * MS.PPQN))),
                          'position': bool(position), 'started': False, 'pulses': 0}
            if position:
                self.sched.at(self.now(), lambda: p['out'].send(MS.song_position(start)), tag=('clock',))
            self._start()
            return (f"clock out -> {name!r}: 24 pulses per beat at {fmt_num(e.bpm)} BPM from now; "
                    + (f"Song Position bar {fmt_bar(start, e.bpb)[4:]} then Continue" if position else "Start")
                    + f" on {fmt_bar(start, e.bpb)} (the other app's "
                    + ("same bar" if position else "bar 1") + "); Stop when turned off or when the set ends")

    def _clock_off(self):
        c, self.clock = self.clock, None
        self.sched.drop(tag=('clock',))
        p = self.ports.get(c['port'])
        if p is not None:
            try:
                p['out'].send(bytes([MS.STOP]))
            except Exception:
                pass
            p['users'].discard(('clock',))
            self._let_go(c['port'])
        return c

    # ------------------------------------------------------------------ planning (the engine's scheduler pass)
    def plan(self):
        """Hand the sender every message due within LOOK_S. Called under the engine's lock."""
        if not (self.routes or self.clock) or self.ended:
            return
        a = self.anchor()
        if a is None:
            return
        e = self.e
        now = self.now()
        with self.lock:
            early = max([0.0] + [-p['offset'] for p in self.ports.values()])
            # never past what the mixer has reached: a clip queued 'now' starts there, and must not land in a span
            # already planned
            hi = min(self.beat_at(now + LOOK_S + early, a), e.beat(e.pos))
            for track in list(self.routes):
                r = self.routes[track]
                if track not in e.tracks:
                    self._unroute(track)
                    e.news.append(f"MIDI out: track {track} was removed; its notes to {r['port']!r} stopped")
                    continue
                if not r['audio'] and not e.tracks[track].get('midi_only'):      # a deck reloaded the track
                    self._audio(track, r)
                if hi > r['planned']:
                    self._plan_notes(track, r, r['planned'], hi, a)
                    r['planned'] = hi
            if self.clock is not None:
                self._plan_clock(hi, a)

    def _plan_notes(self, track, r, lo, hi, a):
        e = self.e
        p = self.ports[r['port']]
        off, ch, notes = p['offset'], r['ch'], p['notes']
        for c in e.tl.track_clips(track):
            end = c.end
            b0, b1 = max(lo, c.start), hi if end is None else min(hi, end)
            if b1 <= b0 or c.length <= 0:
                continue
            k0 = max(0, int(math.floor((b0 - c.start) / c.length)))
            k1 = int(math.ceil((b1 - c.start) / c.length))
            for k in range(k0, k1 + 1):
                if c.loop is not None and k >= c.loop:
                    break
                base = c.start + k * c.length
                for s, m, d, v in c.notes:
                    on = base + s
                    if on < b0 - EPS or on >= b1 - EPS or s >= c.length - EPS:
                        continue
                    pitch = m + e._transpose(track, on)
                    if not 0 <= pitch <= 127:
                        continue
                    t_on, t_off = self.wall(on, off, a), self.wall(on + max(d, MIN_DUR), off, a)
                    if self.ending is not None and t_on >= self.ending:
                        continue
                    tok = next(self._tok)
                    self.sched.at(t_on, self._on(notes, ch, pitch, v, tok, track, r, e.sample(on) + self._lag()),
                                  tag=('track', track))
                    self.sched.at(t_off, lambda n=notes, c_=ch, p_=pitch, t_=tok: n.off(c_, p_, t_),
                                  tag=('track', track))

    def _on(self, notes, ch, pitch, vel, tok, track, r, played=None):
        def act():
            cin = getattr(self.e, 'cin', None)
            if cin is not None and played is not None and not cin.is_open(played):
                return                             # clock in: the other app is stopped, the set is silent
            notes.on(ch, pitch, vel, tok, track)
            r['sent'] += 1
            self.e.sounded = self.e.pos             # the synth sounds in the room: not SILENT ON AIR
        return act

    def _plan_clock(self, hi, a):
        c = self.clock
        p = self.ports[c['port']]
        out, off = p['out'], p['offset']
        s0 = int(round(c['start'] * MS.PPQN))
        for k in MS.pulses(c['k'] / MS.PPQN, hi):
            if k < c['k']:
                continue
            t = self.wall(k / MS.PPQN, off, a)
            if self.ending is not None and t >= self.ending:
                break
            if k == s0:
                self.sched.at(t, self._start_msg(out, c), tag=('clock',))
            self.sched.at(t, self._pulse(out, c), tag=('clock',))
            c['k'] = k + 1

    @staticmethod
    def _start_msg(out, c):
        def act():
            out.send(bytes([MS.CONTINUE if c['position'] else MS.START]))
            c['started'] = True
        return act

    @staticmethod
    def _pulse(out, c):
        def act():
            out.send(bytes([MS.PULSE]))
            c['pulses'] += 1
        return act

    # ------------------------------------------------------------------ the set's end
    def silence(self):
        """Clock in, their Stop: every note held on a routed port gets its note-off now (the set has gone silent;
        notes due later are held back by the gate)."""
        with self.lock:
            for name, p in self.ports.items():
                chans = {r['ch'] for r in self.routes.values() if r['port'] == name}
                if chans:
                    try:
                        p['notes'].release(channels=chans, all_off=False)
                    except Exception:
                        pass

    def end(self, after_s=0.0):
        """live_stop: notes stop with the fade; at its end every held note goes off and the clock stops."""
        if not (self.routes or self.clock) or self.ending is not None:
            return
        self.ending = self.now() + max(0.0, float(after_s))
        self.sched.at(self.ending, self._finish, tag=('end',))

    def _finish(self):
        with self.lock:
            if self.ended:
                return
            self.ended = True
            self.sched.drop(keep=lambda tag: tag == ('end',))
            for name, p in self.ports.items():
                chans = {r['ch'] for r in self.routes.values() if r['port'] == name}
                try:
                    p['notes'].release(channels=chans)
                    if self.clock is not None and self.clock['port'] == name:
                        p['out'].send(bytes([MS.STOP]))
                except Exception:
                    pass

    def close(self):
        """The engine's shutdown: offs and Stop if the end has not sent them, then each port reset and closed."""
        self._finish()
        self.sched.stop()
        with self.lock:
            for p in self.ports.values():
                try:
                    p['out'].close()
                except Exception:
                    pass
            self.ports = {}

    # ------------------------------------------------------------------ what it says
    def line(self, track, r):
        p = self.ports.get(r['port'])
        off = p['offset'] * 1000 if p else 0.0
        return (f"midi out {track} -> {r['port']!r} ch {r['ch'] + 1} | {r['sent']} notes sent | audio "
                + ("on" if r['audio'] else "OFF") + (f" | offset {off:+.0f} ms" if off else '') + self._late())

    def clock_line(self):
        c = self.clock
        if c is None:
            return "clock out: off"
        p = self.ports.get(c['port'])
        off = p['offset'] * 1000 if p else 0.0
        state = (f"running, {'Continue' if c['position'] else 'Start'} sent on {fmt_bar(c['start'], self.e.bpb)}"
                 if c['started'] else f"pulsing, {'Continue' if c['position'] else 'Start'} on "
                 f"{fmt_bar(c['start'], self.e.bpb)}")
        return (f"clock out -> {c['port']!r}: {state} | {c['pulses']} pulses at {fmt_num(self.e.bpm)} BPM"
                + (f" | offset {off:+.0f} ms" if off else '') + self._late())

    def _late(self):
        s = self.sched.lateness()
        err = f" | port errors: {'; '.join(self.sched.errors)}" if self.sched.errors else ''
        return (f" | sent late by {s['median_ms']:.1f} ms median, {s['max_ms']:.1f} max" if s['n'] else '') + err

    def status_lines(self):
        out = [self.line(t, r) for t, r in self.routes.items()]
        if self.clock is not None:
            out.append(self.clock_line())
        return out

    def describe(self):
        try:
            names = self.outputs()
        except Exception:
            names = []
        miss = M.backend_missing()
        L = ["MIDI outputs: " + (', '.join(repr(n) for n in names) if names else
                                 (miss or "none (a synth's USB MIDI, or a loopMIDI port on Windows, shows up here)"))]
        L += self.status_lines() or ["no track sends MIDI: live_midi_out(track=..., port=...) routes one; "
                                     "live_midi_clock(port=...) sends clock"]
        if self.clock is None and self.routes:
            L.append("clock out: off (live_midi_clock(port=...))")
        return '\n'.join(L)
