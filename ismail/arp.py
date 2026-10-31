"""The arpeggiator core (MIDI parity step 7, Nate's spec of 2026-10-14 in songs/_migration/LEDGER.md): one pure,
deterministic step engine shared by the live engine (live_arp: held keys on the engine's beat clock) and the studio
(track_arp: a track's held chords or a step list through the same core at render, or written out as plain notes).
Same settings, same names, same notes on both sides.

Steps sit on a grid of `rate` beats from a bar line (the origin). A step's notes come from one source:
  - pattern: a name ('up', 'down', 'updown', 'downup', 'order' as pressed, 'random', 'chord' all at once) walked
    over the held keys and `octaves`, or a list of held-key numbers (1 = the lowest held key; a number past the
    last goes on up an octave; 0 or None = a rest);
  - degrees: a list of scale degrees above the lowest held key (0 = that key), in key + scale; None = a rest;
  - notes: an exact step list, 'A3 C4 E4 A4 | F3 A3 C4 F4', one bar per '|' group, switching on bar lines, each
    group cycling inside its bar ('.' = a rest, 'C4,E4' = two notes on one step). It plays with no keys held.
With key + scale, held keys are moved onto the scale first. key= on a step list moves it from the key it was
written in (notes_key) to the new one, each note kept on its scale degree; transpose= moves every note by
semitones.

Per-step lists (gate, velocity, accent, slide, octave, cutoff, pan) each cycle on their own length, counted from
the pattern's first step: a 3-step cutoff list over a 4-note pattern makes the swirl. restart: 'key' (a key pressed
starts the pattern again at the next grid line; a chord pressed within one step is one restart), 'bar' (every bar
line), 'off' (counted from the arp's start).
"""
import functools
import math
import random

from .notation import NotationError, fmt_num, midi_to_name, parse_num, pitch_to_midi
from .tune import SCALES

PATTERNS = ('up', 'down', 'updown', 'downup', 'order', 'random', 'chord')
RESTARTS = ('key', 'bar', 'off')
DEFAULTS = {'pattern': 'up', 'degrees': None, 'notes': None, 'notes_key': None, 'rate': 0.25, 'octaves': 1,
            'gate': 0.5, 'velocity': None, 'accent': None, 'slide': None, 'octave': None, 'cutoff': None,
            'pan': None, 'transpose': 0, 'key': None, 'scale': None, 'restart': 'key', 'swing': 0.0}
SETTINGS = tuple(k for k in DEFAULTS if k != 'notes_key')
SOURCES = ('pattern', 'degrees', 'notes')     # one note source at a time
PER_STEP = ('gate', 'velocity', 'accent', 'slide', 'octave', 'cutoff', 'pan')
ACCENT_VEL = 127            # an accented step plays at this velocity
SLIDE_OVER = 0.1            # a slid step lasts into the next by this much of a step (a mono voice glides there)
MAX_LIST = 64
EPS = 1e-6


class ArpError(ValueError):
    pass


# ------------------------------------------------------------------ settings
def _num(x, what):
    try:
        return float(parse_num(x)) if isinstance(x, str) else float(x)
    except (NotationError, TypeError, ValueError):
        raise ArpError(f"{what}: a number (got {x!r})")


def _key_pc(key):
    """A key's pitch class (0 = C), read as note names are everywhere (pitch_to_midi): A, C#, Bb, Cb, E#, F##."""
    t = str(key).strip().replace('♯', '#').replace('♭', 'b')
    if t and t[1:] and set(t[1:]) <= {'#', 'b', 'B'}:
        t = t[0] + t[1:].replace('B', 'b')
    try:
        if not t or not t[0].isalpha() or (t[1:] and not set(t[1:]) <= {'#', 'b'}):
            raise NotationError(t)
        return pitch_to_midi(t[0].upper() + t[1:] + '4') % 12
    except NotationError:
        raise ArpError(f"key {key!r}: a note name such as A, C#, Bb, Cb (the scale goes in scale=)")


def scale_steps(scale):
    if scale is None:
        return None
    if isinstance(scale, (list, tuple)):
        st = sorted({int(s) % 12 for s in scale})
        if not st or st[0] != 0:
            raise ArpError(f"scale {list(scale)}: semitones from the key, starting at 0, e.g. [0, 2, 3, 5, 7, 8, 10]")
        return tuple(st)
    if scale not in SCALES:
        raise ArpError(f"scale {scale!r}: one of {', '.join(SCALES)}, or a list of semitones from the key")
    return tuple(SCALES[scale])


def _per_step(name, v):
    """A per-step setting: a number, or a list cycling on its own length. [] clears a list setting."""
    lo, hi = {'gate': (0.05, 4.0), 'velocity': (1, 127), 'accent': (0, 1), 'slide': (0, 1), 'octave': (-3, 3),
              'cutoff': (20.0, 20000.0), 'pan': (-1.0, 1.0)}[name]
    if isinstance(v, (list, tuple)):
        if not v:
            return DEFAULTS[name]
        if len(v) > MAX_LIST:
            raise ArpError(f"{name}: at most {MAX_LIST} steps in a list (got {len(v)})")
        vals = list(v)
    else:
        vals = [v]
    out = []
    for x in vals:
        if name in ('accent', 'slide') and isinstance(x, bool):
            x = int(x)
        f = _num(x, f"{name} step")
        if not lo <= f <= hi:
            raise ArpError(f"{name}: each step {lo:g} to {hi:g} (got {x!r})" + (
                "; gate is a fraction of a step (0.5 = half)" if name == 'gate' else
                "; cutoff is in Hz" if name == 'cutoff' else ''))
        out.append(int(round(f)) if name in ('velocity', 'accent', 'slide', 'octave') else f)
    return out if isinstance(v, (list, tuple)) else out[0]


def _index_list(name, v, zero_rest):
    if not isinstance(v, (list, tuple)) or not v:
        raise ArpError(f"{name}: a list of whole numbers, e.g. [1, 3, 2, 4]" if name == 'pattern' else
                       f"{name}: a list of scale degrees, e.g. [0, 2, 4, 7] (0 = the lowest held key)")
    if len(v) > MAX_LIST:
        raise ArpError(f"{name}: at most {MAX_LIST} steps (got {len(v)})")
    out = []
    for x in v:
        if x is None or (isinstance(x, str) and x.strip() in ('.', 'r', 'rest')):
            out.append(None)
            continue
        try:
            i = int(x)
        except (TypeError, ValueError):
            raise ArpError(f"{name}: whole numbers, or None for a rest (got {x!r})")
        if zero_rest and i < 0:
            raise ArpError(f"pattern: held-key numbers from 1 (the lowest), 0 or None for a rest (got {i})")
        out.append(None if (zero_rest and i == 0) else i)
    return out


@functools.lru_cache(maxsize=64)
def parse_step_list(text):
    """'A3 C4 E4 A4 | F3 A3 C4 F4' -> ((57, 60, 64, 69), (53, 57, 60, 65)) as groups of steps, each step a tuple of
    MIDI notes (() = a rest)."""
    groups = []
    for gi, g in enumerate(str(text).split('|'), 1):
        toks = g.split()
        if not toks:
            raise ArpError(f"notes: bar group {gi} is empty; each '|' group is one bar of steps, e.g. "
                           f"'A3 C4 E4 A4 | F3 A3 C4 F4' ('.' is a rest)")
        steps = []
        for t in toks:
            if t in ('.', '-', 'r'):
                steps.append(())
                continue
            try:
                steps.append(tuple(pitch_to_midi(p) for p in t.split(',')))
            except NotationError:
                raise ArpError(f"notes: {t!r} in bar group {gi} is not a note; write names like A3, C#4, Bb2, "
                               f"'C4,E4' for two at once, '.' for a rest")
        groups.append(tuple(steps))
    if len(groups) > MAX_LIST:
        raise ArpError(f"notes: at most {MAX_LIST} bar groups")
    return tuple(groups)


def configure(old, changes):
    """old settings (None for a new arp) + the settings given -> (new settings, [names changed]). Only the settings
    given change; pattern, degrees and notes are one choice (setting one clears the others)."""
    cfg = dict(DEFAULTS, **(old or {}))
    ch = {k: v for k, v in changes.items() if v is not None}
    bad = sorted(set(ch) - set(SETTINGS))
    if bad:
        raise ArpError(f"unknown arp settings {bad}; settings: {', '.join(SETTINGS)}")
    src = [k for k in ('pattern', 'degrees', 'notes') if k in ch]
    if len(src) > 1:
        raise ArpError(f"{' and '.join(src)}: one note source at a time: pattern= (over the held keys), degrees= "
                       f"(scale steps above the lowest held key) or notes= (an exact step list)")
    if 'pattern' in ch:
        p = ch['pattern']
        if isinstance(p, str):
            if p not in PATTERNS:
                raise ArpError(f"pattern {p!r}: one of {', '.join(PATTERNS)}, or a list of held-key numbers "
                               f"(1 = the lowest held key), e.g. [1, 3, 2, 4]")
        else:
            p = _index_list('pattern', p, zero_rest=True)
        cfg.update(pattern=p, degrees=None, notes=None, notes_key=None)
    if 'degrees' in ch:
        cfg.update(degrees=_index_list('degrees', ch['degrees'], zero_rest=False), pattern=None, notes=None,
                   notes_key=None)
    if 'key' in ch:
        _key_pc(ch['key'])
        cfg['key'] = str(ch['key']).strip()
    if 'scale' in ch:
        scale_steps(ch['scale'])
        cfg['scale'] = list(ch['scale']) if isinstance(ch['scale'], (list, tuple)) else ch['scale']
    if 'notes' in ch:
        parse_step_list(str(ch['notes']))
        cfg.update(notes=' '.join(str(ch['notes']).split()), pattern=None, degrees=None,
                   notes_key=[cfg['key'], cfg['scale']] if cfg['key'] else None)
    elif 'key' in ch and cfg['notes'] is not None and cfg['notes_key'] is None:
        raise ArpError("key= moves a step list from the key it was written in, but these notes were given with no "
                       "key. Give it with them once (notes=..., key='A', scale='minor'), then key= moves them; "
                       "transpose= moves them by semitones with no key")
    if cfg['degrees'] is not None and not (cfg['key'] and cfg['scale'] is not None):
        raise ArpError("degrees are scale steps: give key= and scale= too, e.g. key='A', scale='minor'")
    if 'rate' in ch:
        r = _num(ch['rate'], 'rate')
        if not 1 / 64 - EPS <= r <= 4:
            raise ArpError(f"rate: beats per step, 1/64 to 4 (0.25 = 16ths, 1/3 = 8th triplets); got {ch['rate']!r}")
        cfg['rate'] = r
    if 'octaves' in ch:
        o = int(_num(ch['octaves'], 'octaves'))
        if not 1 <= o <= 4:
            raise ArpError("octaves: 1 to 4 (how many octaves up the pattern walks)")
        cfg['octaves'] = o
    for k in PER_STEP:
        if k in ch:
            cfg[k] = _per_step(k, ch[k])
    if 'transpose' in ch:
        t = int(_num(ch['transpose'], 'transpose'))
        if abs(t) > 48:
            raise ArpError("transpose: semitones, -48 to 48")
        cfg['transpose'] = t
    if 'restart' in ch:
        if ch['restart'] not in RESTARTS:
            raise ArpError(f"restart {ch['restart']!r}: 'key' (each key pressed starts the pattern again), 'bar' "
                           f"(every bar line) or 'off' (one count from the arp's start)")
        cfg['restart'] = ch['restart']
    if 'swing' in ch:
        s = _num(ch['swing'], 'swing')
        if not 0 <= s <= 0.5:
            raise ArpError("swing: 0 to 0.5, how late every second step comes, as a fraction of a step")
        cfg['swing'] = s
    changed = [k for k in SETTINGS if cfg.get(k) != dict(DEFAULTS, **(old or {})).get(k)]
    return cfg, changed


def describe(cfg):
    """One line of the settings that are not the defaults (and the note source always)."""
    if cfg.get('notes') is not None:
        src = f"notes '{cfg['notes']}'" + (f" (written in {_kname(cfg['notes_key'])})" if cfg.get('notes_key') else '')
    elif cfg.get('degrees') is not None:
        src = f"degrees {_lst(cfg['degrees'])}"
    else:
        src = f"pattern {cfg['pattern'] if isinstance(cfg['pattern'], str) else _lst(cfg['pattern'])}"
    out = [src, f"rate {fmt_num(cfg['rate'])}"]
    for k in ('octaves', 'gate', 'velocity', 'accent', 'slide', 'octave', 'cutoff', 'pan', 'transpose', 'swing',
              'restart'):
        if cfg.get(k) != DEFAULTS[k]:
            out.append(f"{k} {_lst(cfg[k]) if isinstance(cfg[k], list) else cfg[k]}")
    if cfg.get('key'):
        out.append(f"key {_kname([cfg['key'], cfg.get('scale')])}")
    return ', '.join(out)


def _lst(v):
    return '[' + ', '.join('.' if x is None else fmt_num(x) if isinstance(x, (int, float)) else str(x) for x in v) + ']'


def _kname(k):
    return f"{k[0]} {k[1]}" if k[1] is not None else str(k[0])


# ------------------------------------------------------------------ pitches
def _snap(m, root, steps):
    """The nearest note to m in the scale (ties go down)."""
    best = None
    for d in range(-6, 7):
        if (m + d - root) % 12 in steps and (best is None or abs(d) < abs(best)):
            best = d
    return m + (best or 0)


def _degree(m, deg, root, steps):
    """The note `deg` scale steps above m (m on the scale), below for a negative deg."""
    rel = m - root
    octv, pc = divmod(rel, 12)
    i = steps.index(pc) if pc in steps else 0
    j = i + deg
    o, jj = divmod(j, len(steps))
    return root + 12 * (octv + o) + steps[jj]


def move_key(m, frm, to):
    """A written note moved from key `frm` [key, scale] to `to` [key, scale]: kept on its scale degree when both
    scales are known (chromatic notes move with the root), else by the roots' interval; the nearest way (at most a
    tritone up or down)."""
    r0, r1 = _key_pc(frm[0]), _key_pc(to[0])
    shift = (r1 - r0 + 6) % 12 - 6
    s0, s1 = scale_steps(frm[1]), scale_steps(to[1])
    if s0 is None or s1 is None:
        return m + shift
    octv, pc = divmod(m - r0, 12)
    if pc not in s0:
        return m + shift
    i = s0.index(pc)
    o, j = divmod(i, len(s1))
    return r0 + shift + 12 * (octv + o) + s1[j]


def _at(v, k):
    return v[k % len(v)] if isinstance(v, list) else v


def _walk(cfg, held):
    """The held keys [(midi, vel)] as the sequence a named pattern walks, over `octaves`."""
    keys = held
    if cfg.get('key') and cfg.get('scale') is not None:
        root, steps = _key_pc(cfg['key']), scale_steps(cfg['scale'])
        keys = [(_snap(m, root, steps), v) for m, v in held]
    seen, order = set(), []
    for m, v in keys:
        if m not in seen:
            seen.add(m)
            order.append((m, v))
    srt = sorted(order)
    octs = range(int(cfg.get('octaves') or 1))
    # over octaves a wide chord's copies overlap (C3 C4 up an octave is C4 C5): each pitch once, low to high
    up = _once(sorted(((m + 12 * o, v) for o in octs for m, v in srt), key=lambda x: x[0]))
    p = cfg['pattern']
    if p == 'down':
        return up[::-1]
    if p == 'updown':
        return up + up[-2:0:-1]
    if p == 'downup':
        d = up[::-1]
        return d + d[-2:0:-1]
    if p == 'order':
        return _once([(m + 12 * o, v) for o in octs for m, v in order])
    return up


def _once(seq):
    """seq [(midi, vel)] with each pitch once, where it first comes."""
    seen, out = set(), []
    for m, v in seq:
        if m not in seen:
            seen.add(m)
            out.append((m, v))
    return out


def step_notes(cfg, held, k, j, bar_i):
    """The notes [(midi, vel)] of one step: k steps since the pattern's start, j steps since its bar line, bar_i bars
    since the arp's first bar (a step list's group). [] is a rest."""
    if cfg.get('notes') is not None:
        groups = parse_step_list(cfg['notes'])
        g = groups[bar_i % len(groups)]
        ms = g[j % len(g)]
        if cfg.get('notes_key') and cfg.get('key') and [cfg['key'], cfg.get('scale')] != list(cfg['notes_key']):
            ms = tuple(move_key(m, cfg['notes_key'], [cfg['key'], cfg.get('scale')]) for m in ms)
        out = [(m, 100) for m in ms]
    elif not held:
        return []
    elif cfg.get('degrees') is not None:
        d = cfg['degrees'][k % len(cfg['degrees'])]
        if d is None:
            return []
        root, steps = _key_pc(cfg['key']), scale_steps(cfg['scale'])
        low, vel = min(held)
        out = [(_degree(_snap(low, root, steps), d, root, steps), vel)]
    else:
        seq = _walk(cfg, held)
        p = cfg['pattern']
        if p == 'chord':
            out = seq
        elif p == 'random':
            out = [seq[random.Random(k * 7919 + len(seq)).randrange(len(seq))]]
        elif isinstance(p, list):
            i = p[k % len(p)]
            if i is None:
                return []
            srt = sorted({m: v for m, v in _walk(dict(cfg, pattern='up', octaves=1), held)}.items())
            o, ii = divmod(i - 1, len(srt))
            out = [(srt[ii][0] + 12 * o, srt[ii][1])]
        else:
            out = [seq[k % len(seq)]]
    oc = _at(cfg.get('octave'), k) or 0
    tr = int(cfg.get('transpose') or 0) + 12 * int(oc)
    vel = _at(cfg.get('velocity'), k)
    if _at(cfg.get('accent'), k):
        vel = ACCENT_VEL
    return [(m + tr, int(vel if vel is not None else v)) for m, v in out if 0 <= m + tr <= 127]


# ------------------------------------------------------------------ the step clock
def _line(cfg, origin, t):
    """The first grid line number at or after beat t."""
    return math.ceil((t - origin) / cfg['rate'] - EPS)


def sound_time(cfg, origin, n, bpb):
    """Where grid step n sounds: its line, every second step of its bar late by swing (counted from the bar line, so
    every bar starts on its downbeat in any meter, live and in the studio alike)."""
    rate = cfg['rate']
    t = origin + n * rate
    if not cfg['swing']:
        return t
    bar_line = origin + math.floor((t - origin) / bpb + EPS) * bpb
    j = n - _line(cfg, origin, bar_line)
    return t + (cfg['swing'] * rate if j % 2 else 0.0)


def steps(cfg, held, t0, t1, origin, anchor, bpb):
    """The steps that sound in [t0, t1) beats: [(beat, [(midi, vel)], dur beats, cutoff or None, pan or None)].

    The grid runs every `rate` beats from `origin` (a bar line). Step 0 of the pattern is the first line at or after
    `anchor` (restart 'key': the last key pressed; 'off': the arp's start), or each bar line's first (restart 'bar').
    held: [(midi, vel)] in the order pressed (a step list plays without). Pure: the same call gives the same steps."""
    rate = cfg['rate']
    out = []
    n = _line(cfg, origin, t0 - rate)
    n_anchor = _line(cfg, origin, anchor)
    while True:
        t = sound_time(cfg, origin, n, bpb)
        if t >= t1 - EPS:
            break
        nominal = origin + n * rate
        if t >= t0 - EPS and nominal >= anchor - EPS:
            bar_i = int(math.floor((nominal - origin) / bpb + EPS))
            bar_line = origin + bar_i * bpb
            j = n - _line(cfg, origin, bar_line)
            k = j if cfg['restart'] == 'bar' else n - n_anchor
            if k >= 0:
                ns = step_notes(cfg, held, k, j, bar_i)
                if ns:
                    if _at(cfg.get('slide'), k):
                        dur = sound_time(cfg, origin, n + 1, bpb) - t + SLIDE_OVER * rate
                    else:
                        dur = rate * float(_at(cfg['gate'], k))
                    out.append((t, ns, dur, _at(cfg.get('cutoff'), k), _at(cfg.get('pan'), k)))
        n += 1
    return out


# ------------------------------------------------------------------ the studio: a track's held chords at render
def span_of(tr, cfg, bpb):
    """The beats [lo, hi) a track's arp covers: bars=[a, b] when given, else the bars its notes are in."""
    bars = cfg.get('bars')
    if bars:
        return (int(bars[0]) - 1) * bpb, int(bars[1]) * bpb
    ns = tr.get('notes') or []
    if not ns:
        return None
    lo = math.floor(min(n[0] for n in ns) / bpb + EPS) * bpb
    return lo, max(n[0] + n[2] for n in ns)


def track_steps(tr, cfg, bpb):
    """A track's arp as steps on the song's beats: its held chords (the notes in its span) through the core, or the
    step list over its bars. -> (steps, (lo, hi)) or ([], None) when there is nothing to play."""
    sp = span_of(tr, cfg, bpb)
    if sp is None:
        return [], None
    lo, hi = sp
    if cfg.get('notes') is not None:
        return steps(cfg, [], lo, hi, lo, lo, bpb), sp
    ns = sorted(_inside(tr.get('notes') or [], lo, hi), key=lambda n: (n[0], n[1]))
    if not ns:
        return [], sp
    cuts = sorted({lo, hi} | {n[0] for n in ns} | {min(hi, n[0] + n[2]) for n in ns})
    out, anchor = [], lo
    for a, b in zip(cuts, cuts[1:]):
        if b <= a + EPS:
            continue
        held = [(int(n[1]), int(n[3])) for n in ns if n[0] <= a + EPS and n[0] + n[2] > a + EPS]
        if any(abs(n[0] - a) < EPS for n in ns) and cfg['restart'] == 'key':
            anchor = a
        if held:
            out += steps(cfg, held, a, b, lo, anchor, bpb)
    return out, sp


def _inside(notes, lo, hi):
    """The parts of notes that sound in [lo, hi), each clipped to it: a chord held from before the arp's bars counts
    as pressed at their start."""
    out = []
    for n in notes:
        a, b = max(n[0], lo), min(n[0] + n[2], hi)
        if n[0] < hi - EPS and b > a + EPS:
            out.append([a, n[1], b - a, n[3]] + list(n[4:]))
    return out


def _outside(notes, lo, hi):
    """The parts of notes outside [lo, hi), as written there: a note running into the arp's bars ends at them, one
    running past them comes back after."""
    out = []
    for n in notes:
        st, end = n[0], n[0] + n[2]
        if end <= lo + EPS or st >= hi - EPS:
            out.append(list(n))
            continue
        if st < lo - EPS:
            out.append([st, n[1], round(min(end, lo) - st, 6), n[3]] + list(n[4:]))
        if end > hi + EPS:
            a = max(st, hi)
            out.append([a, n[1], round(end - a, 6), n[3]] + list(n[4:]))
    return out


def cutoff_target(tr):
    """Which automation the per-step cutoff moves on this track: the instrument's own filter (a synth, a sampler with
    a filter; a sprite is a synth), else the first filter effect on the track; None when the voice exposes no cutoff."""
    inst = tr.get('instrument') or {}
    if inst.get('type') in ('synth', 'sprite') or (inst.get('type') == 'sampler' and inst.get('filter')):
        return 'inst.filter.cutoff'
    for i, f in enumerate(tr.get('fx') or []):
        if f.get('type') == 'filter':
            return f'fx.{i}.cutoff'
    return None


def _stepped(st, lo, hi):
    """Steps [(beat, value)] as automation points [[beat, value]] held flat until the next step (and to hi)."""
    pts = []
    for i, (b, v) in enumerate(st):
        end = st[i + 1][0] if i + 1 < len(st) else hi
        pts.append([round(b, 6), float(v)])
        pts.append([round(max(b, end - 1e-4), 6), float(v)])
    return pts


def _value_at(pts, b, log):
    """An automation lane's value at beat b, as the renderer reads it: linear between points (log for Hz), held
    before the first and after the last."""
    import numpy as np
    pts = sorted(pts)
    xs = np.array([p[0] for p in pts], dtype=float)
    vs = np.array([p[1] for p in pts], dtype=float)
    if log and np.all(vs > 0):
        return float(np.exp(np.interp(b, xs, np.log(vs))))
    return float(np.interp(b, xs, vs))


def _static(tr, key):
    """What `key` is on this track with no automation: the track's pan, the instrument's filter cutoff, or the
    filter effect's cutoff."""
    if key == 'pan':
        return float(tr.get('pan', 0.0) or 0.0)
    if key == 'inst.filter.cutoff':
        from . import instruments
        inst = instruments.normalize(tr['instrument'])
        return float((inst.get('filter') or {}).get('cutoff', 20000.0))
    from . import fx as fxmod
    i = int(key.split('.')[1])
    return float(fxmod.normalize(tr['fx'][i]).get('cutoff', 20000.0))


def expand(tr, bpb):
    """The track as the renderer plays it: the arp's notes in place of the held chords in its span (notes outside it
    stay), the per-step cutoff and pan as automation over the span. A track with no arp comes back as it is."""
    cfg = tr.get('arp')
    if not cfg:
        return tr
    cfg = dict(DEFAULTS, **cfg)
    st, sp = track_steps(tr, cfg, bpb)
    if sp is None:
        return tr
    lo, hi = sp
    keep = _outside(tr.get('notes') or [], lo, hi)
    notes = keep + [[round(b, 6), m, round(d, 6), v] for b, ns, d, _, _ in st for m, v in ns]
    out = dict(tr, notes=sorted(notes))
    auto = {k: list(v) for k, v in (tr.get('automation') or {}).items()}
    for key, idx in ((cutoff_target(tr), 3), ('pan', 4)):
        if key is None or cfg.get('cutoff' if idx == 3 else 'pan') is None:
            continue
        pts = _stepped([(s[0], s[idx]) for s in st], lo, hi)
        if pts:
            # outside the arp's bars the lane is what it was: the track's own automation there, else its static
            # value (the renderer holds a lane's first and last points out to the song's ends)
            own = auto.get(key) or []
            log = 'cutoff' in key
            edge = (lambda b: _value_at(own, b, log)) if own else (lambda b: _static(tr, key))
            outside = [p for p in own if not lo - EPS <= p[0] <= hi + EPS]
            ends = ([[round(lo - 1e-4, 6), edge(lo)]] if lo > 1e-4 else []) + [[round(hi, 6), edge(hi)]]
            auto[key] = sorted(outside + ends + pts)
    out['automation'] = auto
    return out


def expand_project(d):
    """A project dict with every arp track expanded (a shallow copy); the same dict when no track has an arp."""
    tracks = d.get('tracks') or {}
    if not any(t.get('arp') for t in tracks.values()):
        return d
    bpb = d.get('beats_per_bar', 4)
    return dict(d, tracks={k: expand(t, bpb) for k, t in tracks.items()})


def preview(st, bpb, bars=2):
    """The first bars of steps as text lines: 'bar 1: A3 C4 E4 A4 ...'."""
    if not st:
        return []
    first = int(st[0][0] // bpb)
    by = {}
    for b, ns, _, _, _ in st:
        bi = int(b // bpb)
        if bi - first < bars:
            by.setdefault(bi, []).append(','.join(midi_to_name(m) for m, _ in ns))
    return [f"bar {bi + 1}: {' '.join(v)}" for bi, v in sorted(by.items())]
