"""MIDI effects as data (MIDI parity step 7b): Ableton's Chord, Scale, Pitch, Velocity, Note Length and Random as one
chain, one pure core shared by the live engine (live_midi_fx: a patched input's keys) and the studio (track_midi_fx:
a track's notes at render, or written out as plain notes). Same chain, same notes on both sides. The arpeggiator
(arp.py) is the seventh MIDI effect and has its own ops.

A chain is a list of effects, applied in order to each note a key (or a written note) starts:
  {'type': 'chord', 'intervals': [0, 4, 7], 'velocity': 0.8}     the notes each key plays, semitones from it (0 = the
      key itself; leave 0 out and only the added notes sound); velocity scales the added notes (a number), or each
      interval's note (a list, one per interval)
  {'type': 'scale', 'key': 'A', 'scale': 'minor', 'mode': 'nearest'}   notes off the scale move onto it: 'nearest'
      (ties go down, as the arp snaps), 'up', 'down', or 'drop' (they are not played)
  {'type': 'pitch', 'transpose': 12, 'lowest': 'C3', 'highest': 'C6', 'fold': False}   semitones, then a range:
      notes outside it are dropped, or with fold=True moved by octaves into it
  {'type': 'velocity', 'out': [40, 110], 'curve': 0.5, 'random': 10}   the key's velocity on a curve (-1 to 1:
      above 0 drives soft notes up, below 0 pushes them down) mapped into out [lo, hi], then +- random; or
      'fixed': 100 for one velocity (random still applies, out still bounds it)
  {'type': 'length', 'beats': 0.5} or {'type': 'length', 'scale': 2}   every note the key starts lasts that many
      beats, or that multiple of how long the key was held (wherever the effect sits in the chain)
  {'type': 'random', 'chance': 0.5, 'choices': 2, 'interval': 12, 'sign': 'up', 'seed': 0}   with that chance a
      note moves by 1 to `choices` times `interval` semitones ('up', 'down' or 'both' ways); with key + scale the
      interval counts scale steps and the moved note lands on the scale
Notes past 0..127 are dropped; a pitch two effects both make plays once (the first one made).

Dice (random, velocity random) are a note's own: the seed, the effect's place in the chain, the note's pitch there
and the beat it starts on (in ticks of 1/960 beat). So a render is the same every time, and a key pressed live on
the beat a written note starts rolls what the render rolls.

Order: the chain runs BEFORE the arp on a source that has both. Live, its notes are the keys the arp holds; in the
studio, its notes are the held chords the arp steps.
"""
import random

from . import arp as A
from .notation import NotationError, fmt_num, midi_to_name, pitch_to_midi

TYPES = ('chord', 'scale', 'pitch', 'velocity', 'length', 'random')
FIELDS = {'chord': ('intervals', 'velocity'), 'scale': ('key', 'scale', 'mode'),
          'pitch': ('transpose', 'lowest', 'highest', 'fold'),
          'velocity': ('out', 'curve', 'random', 'fixed'), 'length': ('beats', 'scale'),
          'random': ('chance', 'choices', 'interval', 'sign', 'key', 'scale', 'seed')}
SCALE_MODES = ('nearest', 'up', 'down', 'drop')
SIGNS = ('up', 'down', 'both')
TICKS = 960                 # a note's dice know its start to 1/960 beat
MAX_FX = 16
MAX_INTERVALS = 12
EPS = 1e-6


class MidiFxError(ValueError):
    pass


EXAMPLE = "[{'type': 'chord', 'intervals': [0, 4, 7]}, {'type': 'scale', 'key': 'A', 'scale': 'minor'}]"


# ------------------------------------------------------------------ the chain as data
def _num(x, what, lo, hi, whole=False):
    try:
        f = float(A._num(x, what))
    except A.ArpError as e:
        raise MidiFxError(str(e))
    if not lo - EPS <= f <= hi + EPS:
        raise MidiFxError(f"{what}: {fmt_num(lo)} to {fmt_num(hi)} (got {x!r})")
    if whole:
        if abs(f - round(f)) > EPS:
            raise MidiFxError(f"{what}: a whole number (got {x!r})")
        return int(round(f))
    return f


def _pitch(x, what):
    if isinstance(x, str):
        try:
            return pitch_to_midi(x.strip())
        except NotationError:
            raise MidiFxError(f"{what}: a note name such as C3, F#4, or a MIDI number 0 to 127 (got {x!r})")
    return _num(x, what, 0, 127, whole=True)


def _one(fx, i):
    """One effect checked and filled in: (its normal form)."""
    if not isinstance(fx, dict) or 'type' not in fx:
        raise MidiFxError(f"effect {i + 1}: a dict with a 'type' ({', '.join(TYPES)}), e.g. {EXAMPLE}")
    t = fx['type']
    if t not in TYPES:
        hint = (" (the arpeggiator has its own op: live_arp live, track_arp in the studio)"
                if t in ('arp', 'arpeggiator') else '')
        raise MidiFxError(f"effect {i + 1}: type {t!r} is not a MIDI effect; one of {', '.join(TYPES)}{hint}")
    bad = sorted(set(fx) - {'type'} - set(FIELDS[t]))
    if bad:
        raise MidiFxError(f"effect {i + 1} ({t}): unknown settings {bad}; a {t} takes {', '.join(FIELDS[t])}")
    w = f"effect {i + 1} ({t})"
    try:
        if t == 'chord':
            iv = fx.get('intervals')
            if not isinstance(iv, (list, tuple)) or not iv or len(iv) > MAX_INTERVALS:
                raise MidiFxError(f"{w}: intervals, a list of 1 to {MAX_INTERVALS} semitones from the key, e.g. "
                                  f"[0, 4, 7] (0 = the key itself)")
            vel = fx.get('velocity', 1.0)
            per = isinstance(vel, (list, tuple))
            if per and len(vel) != len(iv):
                raise MidiFxError(f"{w}: velocity as a list has one scale per interval ({len(iv)}), got "
                                  f"{len(vel)}; or give one number for every added note")
            ivs, vs = [], []
            for j, x in enumerate(iv):
                s = _num(x, f"{w} interval", -48, 48, whole=True)
                if s not in ivs:                      # an interval given twice plays once
                    ivs.append(s)
                    if per:
                        vs.append(_num(vel[j], f"{w} velocity", 0, 2))
            vel = vs if per else _num(vel, f"{w} velocity", 0, 2)
            return {'type': t, 'intervals': ivs, 'velocity': vel}
        if t == 'scale':
            if fx.get('key') is None or fx.get('scale') is None:
                raise MidiFxError(f"{w}: give key and scale, e.g. {{'type': 'scale', 'key': 'A', 'scale': 'minor'}}")
            A._key_pc(fx['key'])
            A.scale_steps(fx['scale'])
            mode = fx.get('mode', 'nearest')
            if mode not in SCALE_MODES:
                raise MidiFxError(f"{w}: mode {mode!r}: 'nearest' (ties go down), 'up', 'down' or 'drop' (notes off "
                                  f"the scale are not played)")
            sc = list(fx['scale']) if isinstance(fx['scale'], (list, tuple)) else fx['scale']
            return {'type': t, 'key': str(fx['key']).strip(), 'scale': sc, 'mode': mode}
        if t == 'pitch':
            out = {'type': t, 'transpose': _num(fx.get('transpose', 0), f"{w} transpose", -48, 48, whole=True),
                   'lowest': None if fx.get('lowest') is None else _pitch(fx['lowest'], f"{w} lowest"),
                   'highest': None if fx.get('highest') is None else _pitch(fx['highest'], f"{w} highest"),
                   'fold': bool(fx.get('fold', False))}
            lo, hi = out['lowest'], out['highest']
            if lo is not None and hi is not None and lo > hi:
                raise MidiFxError(f"{w}: lowest {midi_to_name(lo)} is above highest {midi_to_name(hi)}")
            if out['fold'] and (lo is None or hi is None or hi - lo < 11):
                raise MidiFxError(f"{w}: fold moves notes by octaves into lowest..highest, so give both, at least "
                                  f"11 semitones apart (C3 to B3 holds every pitch); or fold=False drops them")
            return out
        if t == 'velocity':
            o = fx.get('out', [1, 127])
            if not isinstance(o, (list, tuple)) or len(o) != 2:
                raise MidiFxError(f"{w}: out, [lo, hi] velocities 1 to 127 (got {o!r})")
            lo, hi = (_num(x, f"{w} out", 1, 127, whole=True) for x in o)
            if lo > hi:
                raise MidiFxError(f"{w}: out [lo, hi] with lo <= hi (got {list(o)})")
            fixed = fx.get('fixed')
            if fixed is not None and 'curve' in fx:
                raise MidiFxError(f"{w}: fixed sets one velocity, a curve shapes the played ones: give one of them")
            return {'type': t, 'out': [lo, hi], 'curve': _num(fx.get('curve', 0), f"{w} curve", -1, 1),
                    'random': _num(fx.get('random', 0), f"{w} random", 0, 127, whole=True),
                    'fixed': None if fixed is None else _num(fixed, f"{w} fixed", 1, 127, whole=True)}
        if t == 'length':
            if ('beats' in fx) == ('scale' in fx):
                raise MidiFxError(f"{w}: one of beats (a fixed length, e.g. 0.5) or scale (a multiple of the "
                                  f"played length, e.g. 2)")
            if 'beats' in fx:
                return {'type': t, 'beats': _num(fx['beats'], f"{w} beats", 1 / 64, 64), 'scale': None}
            return {'type': t, 'beats': None, 'scale': _num(fx['scale'], f"{w} scale", 0.05, 16)}
        # random
        if (fx.get('key') is None) != (fx.get('scale') is None):
            raise MidiFxError(f"{w}: key and scale go together (moves in scale steps), or neither (semitones)")
        if fx.get('key') is not None:
            A._key_pc(fx['key'])
            A.scale_steps(fx['scale'])
        sign = fx.get('sign', 'up')
        if sign not in SIGNS:
            raise MidiFxError(f"{w}: sign {sign!r}: 'up', 'down' or 'both'")
        sc = fx.get('scale')
        return {'type': t, 'chance': _num(fx.get('chance', 0.5), f"{w} chance", 0, 1),
                'choices': _num(fx.get('choices', 1), f"{w} choices", 1, 24, whole=True),
                'interval': _num(fx.get('interval', 12), f"{w} interval", 1, 24, whole=True), 'sign': sign,
                'key': None if fx.get('key') is None else str(fx['key']).strip(),
                'scale': list(sc) if isinstance(sc, (list, tuple)) else sc,
                'seed': _num(fx.get('seed', 0), f"{w} seed", 0, 2 ** 31 - 1, whole=True)}
    except A.ArpError as e:
        raise MidiFxError(f"{w}: {e}")


def configure(chain):
    """A chain as given (a list of effect dicts, or one dict) -> its normal form, every setting filled in."""
    if isinstance(chain, dict):
        chain = [chain]
    if not isinstance(chain, (list, tuple)) or not chain:
        raise MidiFxError(f"chain: a list of MIDI effects in the order they apply, e.g. {EXAMPLE}; to take them off, "
                          f"off=True")
    if len(chain) > MAX_FX:
        raise MidiFxError(f"chain: at most {MAX_FX} effects (got {len(chain)})")
    return [_one(fx, i) for i, fx in enumerate(chain)]


def _kname(key, scale):
    return f"{key} {scale if isinstance(scale, str) else A._lst(scale)}"


def describe(chain):
    """The chain in one line, effects in order: 'chord +0 +4 +7 > scale A minor > velocity 40-110 curve 0.5'."""
    out = []
    for fx in chain:
        t = fx['type']
        if t == 'chord':
            v = fx['velocity']
            out.append('chord ' + ' '.join(f"{s:+d}" for s in fx['intervals'])
                       + ('' if v == 1 else f" velocity {A._lst(v) if isinstance(v, list) else fmt_num(v)}"))
        elif t == 'scale':
            out.append(f"scale {_kname(fx['key'], fx['scale'])}"
                       + ('' if fx['mode'] == 'nearest' else f" {fx['mode']}"))
        elif t == 'pitch':
            bits = [f"{fx['transpose']:+d}"] if fx['transpose'] else []
            if fx['lowest'] is not None or fx['highest'] is not None:
                rng = (f"{midi_to_name(fx['lowest']) if fx['lowest'] is not None else 'any'} to "
                       f"{midi_to_name(fx['highest']) if fx['highest'] is not None else 'any'}")
                bits.append(rng + (' folded' if fx['fold'] else ', others dropped'))
            out.append('pitch ' + (' '.join(bits) or '+0'))
        elif t == 'velocity':
            bits = [f"fixed {fx['fixed']}"] if fx['fixed'] is not None else []
            if fx['out'] != [1, 127]:
                bits.append(f"out {fx['out'][0]}-{fx['out'][1]}")
            if fx['curve']:
                bits.append(f"curve {fmt_num(fx['curve'])}")
            if fx['random']:
                bits.append(f"random +-{fx['random']}")
            out.append('velocity ' + (' '.join(bits) or 'as played'))
        elif t == 'length':
            out.append(f"length {fmt_num(fx['beats'])} beats" if fx['beats'] is not None else
                       f"length x{fmt_num(fx['scale'])} as held")
        else:
            out.append(f"random {fmt_num(round(fx['chance'] * 100, 1))}% {fx['sign']} 1-{fx['choices']} x "
                       f"{fx['interval']} " + (f"steps of {_kname(fx['key'], fx['scale'])}" if fx['key'] else 'st')
                       + (f" seed {fx['seed']}" if fx['seed'] else ''))
    return ' > '.join(out)


# ------------------------------------------------------------------ the notes one key starts
def _die(fx, i, m, tick, what):
    """A note's own die in [0, 1): the same seed, place in the chain, pitch and start tick roll the same."""
    return random.Random(f"{fx.get('seed', 0)}/{i}/{m}/{tick}/{what}").random()


def _snap_dir(m, root, steps, mode):
    if mode == 'nearest':
        return A._snap(m, root, steps)
    rng = range(0, 12) if mode == 'up' else range(0, -12, -1)
    for d in rng:
        if (m + d - root) % 12 in steps:
            return m + d
    return m


def _apply(fx, i, notes, tick):
    t = fx['type']
    out = []
    if t == 'chord':
        v = fx['velocity']
        for m, vel in notes:
            for j, s in enumerate(fx['intervals']):
                k = v[j] if isinstance(v, list) else (1.0 if s == 0 else v)
                out.append((m + s, max(1, min(127, int(round(vel * k))))))
    elif t == 'scale':
        root, steps = A._key_pc(fx['key']), A.scale_steps(fx['scale'])
        for m, vel in notes:
            on = (m - root) % 12 in steps
            if fx['mode'] == 'drop':
                if on:
                    out.append((m, vel))
            else:
                out.append((m if on else _snap_dir(m, root, steps, fx['mode']), vel))
    elif t == 'pitch':
        lo, hi = fx['lowest'], fx['highest']
        for m, vel in notes:
            m += fx['transpose']
            if fx['fold']:
                while m < lo:
                    m += 12
                while m > hi:
                    m -= 12
            if (lo is None or m >= lo) and (hi is None or m <= hi):
                out.append((m, vel))
    elif t == 'velocity':
        lo, hi = fx['out']
        for m, vel in notes:
            if fx['fixed'] is not None:
                v = float(fx['fixed'])
            else:
                x = (max(1, min(127, vel)) - 1) / 126.0
                v = lo + (hi - lo) * x ** (4.0 ** -fx['curve'])
            if fx['random']:
                v += (2 * _die(fx, i, m, tick, 'vel') - 1) * fx['random']
            out.append((m, int(max(lo, min(hi, round(v))))))
    elif t == 'random':
        for m, vel in notes:
            if _die(fx, i, m, tick, 'chance') >= fx['chance']:
                out.append((m, vel))
                continue
            k = 1 + int(_die(fx, i, m, tick, 'choice') * fx['choices'])
            if fx['sign'] == 'down' or (fx['sign'] == 'both' and _die(fx, i, m, tick, 'sign') < 0.5):
                k = -k
            if fx['key']:
                root, steps = A._key_pc(fx['key']), A.scale_steps(fx['scale'])
                out.append((A._degree(A._snap(m, root, steps), k * fx['interval'], root, steps), vel))
            else:
                out.append((m + k * fx['interval'], vel))
    else:                                       # length: no change to the pitches
        out = list(notes)
    return out


def notes_on(chain, m, vel, beat):
    """The notes [(midi, vel)] one key (or one written note) starts: pitch m at velocity vel, starting on `beat`,
    through the chain in order. Pure: the same call gives the same notes."""
    notes = [(int(m), int(vel))]
    tick = int(round(beat * TICKS))
    for i, fx in enumerate(chain or ()):
        notes = _apply(fx, i, notes, tick)
    seen, out = set(), []
    for n, v in notes:
        if 0 <= n <= 127 and n not in seen:
            seen.add(n)
            out.append((n, v))
    return out


def length_rule(chain):
    """How long the notes a key starts last: (fixed beats or None for as held, multiple). The length effects
    compose in order: beats sets the length, scale multiplies it."""
    base, mult = None, 1.0
    for fx in chain or ():
        if fx['type'] == 'length':
            if fx['beats'] is not None:
                base, mult = fx['beats'], 1.0
            else:
                mult *= fx['scale']
    return base, mult


def note_len(chain, dur):
    base, mult = length_rule(chain)
    return (dur if base is None else base) * mult


# ------------------------------------------------------------------ the studio: a track's notes at render
def expand(tr, bpb=4):
    """The track as the renderer plays it: each written note replaced by the notes the chain starts from it (a
    note's extra fields, its nudge, kept). A track with no chain comes back as it is."""
    chain = tr.get('midi_fx')
    if not chain:
        return tr
    out = []
    for n in tr.get('notes') or []:
        d = round(note_len(chain, n[2]), 6)
        for m, v in notes_on(chain, n[1], n[3], n[0]):
            out.append([n[0], m, d, v] + list(n[4:]))
    return dict(tr, notes=sorted(out))


def expand_project(d):
    """A project dict with every MIDI effect chain expanded (a shallow copy); the same dict when no track has one.
    arp.expand_project runs this first: the chain comes before the arp."""
    tracks = d.get('tracks') or {}
    if not any(t.get('midi_fx') for t in tracks.values()):
        return d
    bpb = d.get('beats_per_bar', 4)
    return dict(d, tracks={k: expand(t, bpb) for k, t in tracks.items()})


def preview(notes, bpb, bars=2):
    """The first bars of notes as text lines, notes starting together joined: 'bar 1: C4,E4,G4 A3,C4,E4'."""
    if not notes:
        return []
    first = int(min(n[0] for n in notes) // bpb)
    by = {}
    for n in sorted(notes, key=lambda n: (n[0], n[1])):
        bi = int(n[0] // bpb)
        if bi - first < bars:
            by.setdefault(bi, {}).setdefault(round(n[0], 6), []).append(midi_to_name(n[1]))
    return [f"bar {bi + 1}: {' '.join(','.join(v) for _, v in sorted(g.items()))}" for bi, g in sorted(by.items())]
