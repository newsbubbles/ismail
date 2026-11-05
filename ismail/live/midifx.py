"""live_midi_fx: MIDI effects on played keys (MIDI parity step 7b). Each key of a patched input (live_midi) goes
through a chain of chord, scale, pitch, velocity, length and random effects before it sounds, through the same core
the studio renders with (ismail/midifx.py): same chain, same key on the same beat, same notes. A take
(live_played) gives the notes made, kept with each key in the key log (midi_play._fx_on), so a take rendered later
plays what was heard; raw=True gives the keys.

This holds each input's chain and the changes queued for a bar line; the keys themselves are played in midi_play
(_fx_on, _fx_off), which keeps, per key held, the notes it started, so its release ends exactly those notes even
when the chain changed while it was held. A key pressed uses the chain in effect on the beat it sounds against
(a change queued at a bar line lands on it). With live_arp on the same input, the chain's notes are the keys the
arp holds: the chain runs before the arp.

Length, live: a note's start is the beat its key sounded against. {'beats': b} ends every note the key started on
start + b on the set's clock (sample exact on the device, through the tempo map), whatever the key does; the key's
release changes nothing. {'scale': s} ends them at start + s x how long the key was held, known at the release: a
scale above 1 delays the end, one below 1 cannot end a note before its key is let go, so live it ends at the
release (the studio shortens it). The sustain pedal still holds a note past its end, as it holds a key let go.
"""
import collections
import threading

from .. import midifx as MF
from ..notation import fmt_num


class LiveMidiFx:
    """Every input's MIDI effect chain, by its input's name."""

    def __init__(self, engine):
        self.e = engine
        self.lock = threading.Lock()
        self.fx = {}          # input -> {'chain': [...] or None, 'pending': [(beat, chain or None)], 'keys', 'recent'}

    def _patched(self, source):
        pats = self.e.midi.patches
        if source in pats:
            return source
        hits = [n for n in pats if source.lower() in n.lower()]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise MF.MidiFxError(f"source {source!r} matches {hits}: name one")
        raise MF.MidiFxError(f"source {source!r} is not patched, and MIDI effects work on a patched input's keys: "
                             f"live_midi(source={source!r}, instrument=... or track=...) first, then live_midi_fx. "
                             f"patched: {list(pats) or 'none'}")

    def _when(self, at):
        if at in (None, 'now', 'asap'):
            return None
        now = self.e.midi.sound_beat()
        try:
            beat, _ = self.e.tl.resolve_at(at, now, now)
        except ValueError as ex:
            raise MF.MidiFxError(f"{ex} (at= takes 'now', 'next_bar', 'next_4', 'bar:<n>')")
        return beat

    def cmd(self, source=None, chain=None, at=None, off=False):
        if source is None:
            if chain is not None or off:
                raise MF.MidiFxError("source: which patched input the effects are on (live_midi lists them), e.g. "
                                     "live_midi_fx(source='screen keys', chain=[{'type': 'chord', 'intervals': "
                                     "[0, 4, 7]}])")
            return '\n'.join(self.lines()) or ("no MIDI effects on: live_midi_fx(source=<a patched input>, "
                                               f"chain={MF.EXAMPLE}) puts a chain on its keys")
        name = self._patched(source)
        when = self._when(at)
        bpb = self.e.bpb
        with self.lock:
            f = self.fx.get(name)
            if f is not None:
                self._land(f, self.e.midi.sound_beat())
            if off:
                if f is None or (f['chain'] is None and not any(c for _, c in f['pending'])):
                    raise MF.MidiFxError(f"no MIDI effects on {name!r}; on: {self._on() or 'none'}")
                if when is None:
                    del self.fx[name]
                    return (f"midi fx {name}: off now ({f['keys']} keys went through it); keys play as pressed. "
                            f"Keys held now end the notes they started")
                self._queue(f, when, None)
                return f"midi fx {name}: off at {_bar(when, bpb)}"
            if chain is None:
                if f is None:
                    return (f"no MIDI effects on {name!r}: live_midi_fx(source={name!r}, chain={MF.EXAMPLE}) puts "
                            f"a chain on its keys")
                return self._line(name, f)
            cfg = MF.configure(chain)
            new = f is None
            if new:
                f = self.fx[name] = {'chain': None, 'pending': [], 'keys': 0, 'recent': collections.deque(maxlen=64)}
            if when is None:
                f['chain'] = cfg
                f['pending'] = [x for x in f['pending'] if x[0] > self.e.midi.sound_beat()]
            else:
                self._queue(f, when, cfg)
        arp = self.e.arp.arps.get(name) if getattr(self.e, 'arp', None) is not None else None
        base, mult = MF.length_rule(cfg)
        L = [f"midi fx {name}: " + ("on" if new else "changed") + (" now" if when is None else
                                                                   f" at {_bar(when, bpb)}") + f": {MF.describe(cfg)}",
             "keys pressed " + ("now" if when is None else "from then") + " play through it; keys held now end the "
             "notes they started"]
        if arp is not None:
            L.append("live_arp is on this input: the chain runs first, the arp steps the notes it makes")
        if base is None and mult < 1:
            L.append(f"length x{fmt_num(mult)}: live a note cannot end before its key is let go, so it ends at the "
                     f"release (the studio's track_midi_fx shortens it); length beats=... ends notes early live too")
        return '\n'.join(L)

    @staticmethod
    def _queue(f, when, cfg):
        """A change at `when` replaces one queued at that beat."""
        f['pending'] = sorted([x for x in f['pending'] if abs(x[0] - when) > MF.EPS] + [(when, cfg)],
                              key=lambda x: x[0])

    @staticmethod
    def _land(f, beat):
        """The changes queued at or before `beat` take effect (under the lock)."""
        while f['pending'] and f['pending'][0][0] <= beat + MF.EPS:
            f['chain'] = f['pending'].pop(0)[1]

    def _on(self):
        return [n for n, f in self.fx.items() if f['chain'] is not None or f['pending']]

    # ------------------------------------------------------------------ keys (the driver's thread)
    def chain_at(self, source, beat):
        """The chain a key of `source` pressed on `beat` plays through, or None."""
        if source not in self.fx:
            return None
        with self.lock:
            f = self.fx.get(source)
            if f is None:
                return None
            self._land(f, beat)
            if f['chain'] is None and not f['pending']:
                del self.fx[source]               # turned off at its bar line
                self.e.news.append(f"midi fx {source}: off ({f['keys']} keys went through it)")
            return f['chain']

    def played(self, source, beat, m, vel, outs):
        """A key went through the chain: kept for live_status and the tests."""
        with self.lock:
            f = self.fx.get(source)
            if f is not None:
                f['keys'] += 1
                f['recent'].append((beat, m, vel, list(outs)))

    # ------------------------------------------------------------------ what it says
    def _line(self, name, f):
        bpb = self.e.bpb
        p = self.e.midi.patches.get(name)
        if f['chain'] is not None:
            what = MF.describe(f['chain'])
        elif f['pending']:
            what = ("starts at " if f['pending'][0][1] is not None else "off at ") + _bar(f['pending'][0][0], bpb)
        else:
            what = 'off'
        bits = [f"midi fx {name}: {what}", f"{f['keys']} keys through it"]
        if p is not None and p.get('fxon'):
            bits.append(f"{len(p['fxon'])} keys held")
        for b, c in f['pending'] if f['chain'] is not None else f['pending'][1:]:
            bits.append(f"at {_bar(b, bpb)}: " + (MF.describe(c) if c is not None else 'off'))
        if p is None:
            bits.append("its input is no longer patched: live_midi(source=..., instrument=...) again")
        return ' | '.join(bits)

    def lines(self):
        with self.lock:
            snap = [(n, dict(f, pending=list(f['pending']))) for n, f in self.fx.items()]
        return [self._line(n, f) for n, f in snap]


def _bar(beat, bpb):
    b, r = divmod(beat, bpb)
    return f"bar {int(b) + 1}" + (f" +{fmt_num(r)}" if r > MF.EPS else '')
