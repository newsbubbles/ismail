"""live_arp: the arpeggiator on played keys (MIDI parity step 7). The keys of a patched input (live_midi: a MIDI
keyboard, a loopMIDI port or the on-screen keys page) stop sounding as they are pressed and drive a step clock on
the engine's beat instead, through the same core the studio renders with (ismail/arp.py): same settings, same notes.

Timing: the keys' notes are added where the audio goes to the device (midi_play), and a device buffer starting at
sample `played` carries the music of beat(played - path lookahead). So a step at beat b is placed at sample
sample(b) + lookahead in device time: it lands on the beat the person hears, sample exact, as the set's own notes
do. A key pressed now changes the steps from the next device buffer on.

Each step plays the patch's rendered note (its bank: C2 to C7, or a kit's pads) for the step's gate, then fades
with the patch's release. Per-step movement the sound follows: pan (equal power, as the studio pans) and cutoff
(a 12 dB/octave low-pass on that step's note, any patch). Not yet: slide glides only in the studio (a mono synth
with glide); live, a slid step is a legato tie with a fresh attack. The input's expression (live_midi lanes: bend,
pressure, the mod wheel) does not reach the steps yet: a step plays its rendered note as it is.
"""
import collections
import threading

import numpy as np
from scipy.signal import butter, lfilter

from .. import arp as A
from .. import dsp
from ..dsp import SR

MAX_VOICES = 32
FADE_ROOM = 8                # of those, kept for voices fading out after a steal
LIVE_ONLY = ('latch',)


class ArpVoice:
    __slots__ = ('y', 'i', 'wait', 'g', 'gate', 'rel', 'left', 'ba', 'zi', 'stolen')

    def __init__(self, y, wait, gain, pan, gate, rel, cutoff):
        gl, gr = dsp.pan_gains(pan or 0.0)
        self.y, self.i, self.wait = y, 0, int(wait)
        self.g = np.array([gl * gain, gr * gain], dtype=np.float32)
        self.gate, self.rel, self.left = max(1, int(gate)), max(1, int(rel)), None
        self.ba, self.stolen = None, False
        if cutoff is not None and cutoff < 0.45 * SR:
            self.ba = butter(2, float(cutoff) / (SR / 2), 'low')
            self.zi = np.zeros((2, 2))

    def steal(self, n):
        """End within n samples: a fade from where it is (a voice still waiting never starts)."""
        self.stolen = True
        if self.wait > 0 and self.i == 0:
            self.i = self.y.shape[1]              # never sounded: done
        elif self.left is None:
            self.gate = min(self.gate, self.i + 1)
            self.rel = min(self.rel, n)
        elif self.left > n:                       # already fading: the rest of the fade, faster, from its level
            self.rel = max(1, int(self.rel * n / self.left))
            self.left = n

    def render(self, n):
        """((n, 2) of this voice for the next n samples, whether it is done): the wait, the note up to its gate,
        then the release as a linear fade."""
        out = np.zeros((n, 2), dtype=np.float32)
        s = min(self.wait, n)
        self.wait -= s
        L = self.y.shape[1]
        i0, env = self.i, []
        if self.left is None:
            k = max(0, min(n - s, self.gate - self.i, L - self.i))
            env.append(np.ones(k, dtype=np.float32))
            self.i += k
            if self.i >= self.gate:
                self.left = self.rel
        if self.left is not None:
            j = max(0, min(n - s - (self.i - i0), self.left, L - self.i))
            env.append(np.arange(self.left, self.left - j, -1, dtype=np.float32) / self.rel)
            self.i += j
            self.left -= j
        x = self.y[:, i0:self.i]
        if x.shape[1]:
            if self.ba is not None:
                x, self.zi = lfilter(self.ba[0], self.ba[1], x, axis=1, zi=self.zi)
            out[s:s + x.shape[1]] = (x.T * self.g) * np.concatenate(env)[:, None]
        return out, self.i >= L or (self.left is not None and self.left <= 0)


class LiveArps:
    """Every live arp of the engine, one per patched input, by its input's name."""

    def __init__(self, engine):
        self.e = engine
        self.lock = threading.Lock()
        self.arps = {}
        self.voices = []

    # ------------------------------------------------------------------ the command
    def _patched(self, source):
        pats = self.e.midi.patches
        if source in pats:
            return source
        hits = [n for n in pats if source.lower() in n.lower()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise A.ArpError(f"source {source!r} matches {hits}: name one")
        raise A.ArpError(f"source {source!r} is not patched, and the arp plays a patched input's sound: "
                         f"live_midi(source={source!r}, instrument=... or track=...) first, then live_arp. "
                         f"patched: {list(pats) or 'none'}")

    def _when(self, at):
        """at -> the beat it takes effect on the heard clock (None = now)."""
        if at in (None, 'now', 'asap'):
            return None
        now = self.e.midi.sound_beat()
        try:
            beat, _ = self.e.tl.resolve_at(at, now, now)
        except ValueError as ex:
            raise A.ArpError(f"{ex} (at= takes 'now', 'next_bar', 'next_4', 'bar:<n>')")
        return beat

    def cmd(self, source=None, at=None, off=False, latch=None, **settings):
        if source is None:
            if any(v is not None for v in settings.values()) or off or latch is not None:
                raise A.ArpError("source: which patched input to arp (live_midi lists them), e.g. "
                                 "live_arp(source='screen keys', pattern='up')")
            return '\n'.join(self.lines()) or ("no arp on: live_arp(source=<a patched input>, pattern='up', "
                                               "rate=0.25) starts one; any key pressed then drives it")
        name = self._patched(source)
        a = self.arps.get(name)
        when = self._when(at)
        bpb = self.e.bpb
        if off:
            if a is None:
                raise A.ArpError(f"no arp on {name!r}; on: {list(self.arps) or 'none'}")
            if when is None:
                with self.lock:
                    del self.arps[name]
                return f"arp {name}: off ({a['steps']} steps played); its keys play as keys again"
            with self.lock:
                a['stop'] = when
            return f"arp {name}: off at {_bar(when, bpb)}"
        if a is None:
            new_cfg, _ = A.configure(None, settings)
            start = when if when is not None else self.e.midi.sound_beat()
            a = {'source': name, 'cfg': new_cfg, 'latch': bool(latch), 'down': collections.OrderedDict(),
                 'held': collections.OrderedDict(), 'latched': [], 'restart': False, 'origin': (start // bpb) * bpb, 'anchor': start,
                 'start': start, 'done': start, 'stop': None, 'pending': [], 'steps': 0, 'missed': 0,
                 'recent': collections.deque(maxlen=64)}
            p = self.e.midi.patches[name]
            with self.lock:
                self.arps[name] = a
            self.e.midi.release_all(p)             # keys sounding as keys stop: the arp has them now
            return (f"arp {name}: on " + (f"from {_bar(start, bpb)}" if when is not None else "now") +
                    f": {A.describe(new_cfg)}" + (" | latch" if a['latch'] else '') +
                    "\nkeys pressed now drive it" + ('' if new_cfg['notes'] is None else
                                                     " (a step list plays with no keys held)"))
        delta = {k: v for k, v in settings.items() if v is not None}
        with self.lock:
            # what plays at `when`: the settings now, then each change queued at or before it. A change is kept as
            # the settings it gives (a delta), so a later one never reverts an earlier one, and one at the same beat
            # merges into it.
            pend = [list(x) for x in a['pending']]
            base = a['cfg']
            if when is not None:
                for b, d in pend:
                    if b <= when + A.EPS:
                        base = A.configure(base, d)[0]
            new_cfg, changed = A.configure(base, delta)
            if when is None:
                now_cfg = new_cfg
            else:
                now_cfg = a['cfg']
                same = next((x for x in pend if abs(x[0] - when) <= A.EPS), None)
                if same is not None:
                    old = same[1]
                    if any(k in delta for k in A.SOURCES):    # a new note source replaces the one queued there
                        old = {k: v for k, v in old.items() if k not in A.SOURCES}
                    same[1] = dict(old, **delta)
                elif delta:
                    pend.append([when, delta])
                pend.sort(key=lambda x: x[0])
            chain = now_cfg
            for b, d in pend:                   # every queued change still holds on top of this one
                try:
                    chain = A.configure(chain, d)[0]
                except A.ArpError as ex:
                    raise A.ArpError(f"{ex} (with the change queued at {_bar(b, bpb)} on top; live_status lists "
                                     f"the queued changes)")
            if latch is not None:
                if latch and not a['latch']:
                    p = self.e.midi.patches.get(name)
                    if p is not None:
                        self._prune(a, p)
                    a['latched'] = list(a['held'].items())     # the chord held now keeps playing
                a['latch'] = bool(latch)
                if not latch:
                    a['latched'] = []
                changed.append('latch')
            if not changed:
                return f"arp {name}: nothing changed: {A.describe(new_cfg)}"
            if when is None:
                self._apply(a, new_cfg, None, any(k in changed for k in ('pattern', 'degrees', 'notes', 'rate')))
            a['pending'] = [tuple(x) for x in pend]
        return (f"arp {name}: {', '.join(changed)} changed " + ("now" if when is None else f"at {_bar(when, bpb)}")
                + f": {A.describe(new_cfg)}" + (" | latch" if a['latch'] else ''))

    def _land(self, a, beat, delta):
        """A queued change reaches its beat (the device's thread, under the lock)."""
        try:
            cfg, changed = A.configure(a['cfg'], delta)
        except A.ArpError as ex:
            self.e.news.append(f"arp {a['source']}: the change queued at {_bar(beat, self.e.bpb)} did not apply: {ex}")
            return
        self._apply(a, cfg, beat, any(k in changed for k in ('pattern', 'degrees', 'notes', 'rate')))

    def _apply(self, a, cfg, beat, moved):
        old = a['cfg']
        a['cfg'] = cfg
        if moved:
            b = beat if beat is not None else a['done']
            a['anchor'] = b
            if cfg['notes'] is not None and cfg['notes'] != old.get('notes'):
                a['origin'] = (b // self.e.bpb) * self.e.bpb

    # ------------------------------------------------------------------ keys (the driver's thread)
    def key(self, p, note, vel):
        """A key of patch p: True when an arp takes it (then it does not sound as a key)."""
        a = self.arps.get(p['source'])
        if a is None:
            return False
        m = note                                  # by the key itself: a transpose change never strands a key
        with self.lock:
            self._prune(a, p)
            if vel:
                if a['latch'] and not a['held']:
                    a['latched'] = []             # a new chord after all keys were let go replaces the latched one
                a['down'][m] = vel
                a['held'].pop(m, None)            # pressed again: it moves to the end of the order
                a['held'][m] = vel
                if a['latch'] and m not in [x for x, _ in a['latched']]:
                    a['latched'].append((m, vel))
                a['restart'] = True
            else:
                a['down'].pop(m, None)
                if not p['sustain']:
                    a['held'].pop(m, None)        # under the pedal it stays held, in its place
        return True

    def clear(self, p):
        """Every key of patch p let go (all notes off, unplugged, unpatched)."""
        a = self.arps.get(p['source'])
        if a is not None:
            with self.lock:
                a['down'].clear()
                a['held'].clear()
                a['latched'] = []

    @staticmethod
    def _prune(a, p):
        """The pedal is up: the keys it held (let go meanwhile) are let go now."""
        if not p['sustain'] and len(a['held']) != len(a['down']):
            for m in [m for m in a['held'] if m not in a['down']]:
                del a['held'][m]

    def _held(self, a, p):
        """The keys the arp holds now, [(midi, vel)] in the order pressed (the pedal's included, in their places),
        with the patch's transpose (as its keys sound). Called under the lock."""
        self._prune(a, p)
        keys = list(a['latched']) if a['latch'] else list(a['held'].items())
        t = p['transpose']
        return [(m + t, v) for m, v in keys]

    # ------------------------------------------------------------------ audio (the device's thread)
    def mix(self, frames, played):
        """The arps' steps that sound in the device buffer [played, played + frames): (frames, 2) or None."""
        if not self.arps and not self.voices:
            return None
        from . import graph as G
        e = self.e
        lag = e.safety.la + G.LAT_BUDGET
        b0, b1 = e.beat(played - lag), e.beat(played + frames - lag)
        with self.lock:
            for name in list(self.arps):
                a = self.arps[name]
                p = e.midi.patches.get(name)
                if p is None:
                    continue
                self._run(a, p, b0, b1, played, lag, frames)
                if a['stop'] is not None and a['done'] >= a['stop'] - A.EPS:
                    del self.arps[name]
                    e.news.append(f"arp {name}: off ({a['steps']} steps played)")
            if not self.voices:
                return None
            acc = np.zeros((frames, 2), dtype=np.float32)
            keep = []
            for v in self.voices:
                y, done = v.render(frames)
                acc += y
                if not done:
                    keep.append(v)
            self.voices = keep
        return acc

    def _run(self, a, p, b0, b1, played, lag, frames):
        lo, hi = max(a['done'], b0), b1
        if a['stop'] is not None:
            hi = min(hi, a['stop'])
        if hi <= lo + A.EPS:
            a['done'] = max(a['done'], hi)
            return
        cuts = [lo] + [x[0] for x in a['pending'] if lo < x[0] < hi] + [hi]
        for s, t in zip(cuts, cuts[1:]):
            while a['pending'] and a['pending'][0][0] <= s + A.EPS:
                beat, delta = a['pending'].pop(0)
                self._land(a, beat, delta)
            if a['restart']:
                a['restart'] = False
                if a['cfg']['restart'] == 'key' and a['cfg']['notes'] is None:
                    a['anchor'] = s
            held = self._held(a, p)
            for beat, ns, dur, cut, pan in A.steps(a['cfg'], held, s, t, a['origin'], a['anchor'], self.e.bpb):
                self._sound(a, p, beat, ns, dur, cut, pan, played, lag, frames)
        a['done'] = hi

    def _sound(self, a, p, beat, ns, dur, cut, pan, played, lag, frames):
        e = self.e
        s0 = e.sample(beat)
        wait = min(max(0, s0 + lag - played), frames - 1)
        gate = e.sample(beat + dur) - s0
        rel = int(p['rel_s'] * SR)
        for m, v in ns:
            if p['oneshot'] and len(p['want']) == 1:
                m = p['want'][0]
            y = p['bank'].get(m)
            if y is None:
                y = p['prev'].get(m)
            if y is None:
                a['missed'] += 1
                continue
            self._make_room()
            g = 10 ** (p['vol'] / 20) * (v / 100.0) ** 1.3
            self.voices.append(ArpVoice(y, wait, g, pan, gate, rel, cut))
        a['steps'] += 1
        a['recent'].append((round(beat, 6), [m for m, _ in ns], [v for _, v in ns], round(dur, 6)))

    def _make_room(self):
        """Before a new voice: with MAX_VOICES - FADE_ROOM sounding, the oldest one not already ending fades out over
        20 ms; voices past MAX_VOICES (ending ones first, oldest first) are dropped. Never more than MAX_VOICES."""
        live = [v for v in self.voices if not v.stolen]
        if len(live) >= MAX_VOICES - FADE_ROOM:
            live[0].steal(int(0.02 * SR))
        while len(self.voices) >= MAX_VOICES:
            self.voices.remove(next((v for v in self.voices if v.stolen), self.voices[0]))

    # ------------------------------------------------------------------ what it says
    def lines(self):
        bpb, out = self.e.bpb, []
        now = self.e.midi.sound_beat()
        with self.lock:                         # a copy: the device's thread changes the arps as it plays
            snap = []
            for name, a in self.arps.items():
                p = self.e.midi.patches.get(name)
                snap.append((name, dict(a, cfg=dict(a['cfg']), pending=list(a['pending'])), p,
                             self._held(a, p) if p is not None else []))
        for name, a, p, held in snap:
            bits = [f"arp {name}: " + (f"starts {_bar(a['start'], bpb)}" if a['done'] <= a['start'] + A.EPS
                                       and a['steps'] == 0 and a['start'] > now else "on"),
                    A.describe(a['cfg']) + (' | latch' if a['latch'] else '')]
            if a['cfg']['notes'] is None:
                bits.append("holding " + (' '.join(A.midi_to_name(m) for m, _ in sorted(held)) if held else
                                          "no keys (press some)"))
            bits.append(f"{a['steps']} steps played")
            if a['pending']:
                bits.append("changes queued at " + ', '.join(_bar(x[0], bpb) for x in a['pending']))
            if a['stop'] is not None:
                bits.append(f"off at {_bar(a['stop'], bpb)}")
            if a['missed']:
                bits.append(f"{a['missed']} notes outside the patch's rendered notes (C2 to C7) were silent")
            if p is None:
                bits.append("its input is no longer patched: live_midi(source=..., instrument=...) again")
            out.append(' | '.join(bits))
        return out


def _bar(beat, bpb):
    b, r = divmod(beat, bpb)
    return f"bar {int(b) + 1}" + (f" +{A.fmt_num(r)}" if r > A.EPS else '')
