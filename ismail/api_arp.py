"""track_arp: the arpeggiator in the studio (MIDI parity step 7; live_arp is its live twin, both on the core in arp.py).
A track's held chords, or a step list over its bars, go through the arp at render; write=True writes the result
into the track as plain notes to edit. Registered into api.OPS on import."""
from . import arp as A
from .api import OpError, op, _load


@op(mutates=True)
def track_arp(project: str, track: str, pattern=None, degrees: list = None, notes: str = None, rate=None,
              octaves: int = None, gate=None, velocity=None, accent: list = None, slide: list = None,
              octave: list = None, cutoff=None, pan=None, transpose: int = None, key: str = None, scale=None,
              restart: str = None, swing: float = None, bars: list = None, write: bool = False,
              off: bool = False) -> str:
    """Arpeggiate a track at render: its held chords (the notes written on it) play as steps, or an exact step list
    over bars=[a, b]. The same core and settings as live_arp, so a part sounds the same played live and rendered.
    Any subset of settings changes only those; the first call starts with the defaults for the rest. The track's
    notes stay as written (notes_read shows the chords); the render plays the arp in their place within its bars.
    Note source (one of): pattern 'up' (default), 'down', 'updown', 'downup', 'order' (the order the notes start,
    low first in a chord), 'random', 'chord', or a list of held-note numbers ([1, 3, 2, 4]; 1 = the lowest, 0 = a
    rest); degrees: scale steps above the lowest held note ([0, 2, 4, 7], needs key and scale); notes: an exact
    step list 'A3 C4 E4 A4 | F3 A3 C4 F4', one bar per '|' group from bars[0], cycling (needs bars when the track
    has no notes). rate: beats per step (default 0.25 = 16ths). octaves: 1 to 4 (default 1). key + scale: held
    notes snap onto the scale; on a step list, key= moves it from the key it was given with, each note kept on its scale degree.
    transpose: semitones. Per step (a number, or a list cycling on its own length; [] clears): gate (fraction of
    a step, 0.5), velocity (1-127; default each note's own), accent (0/1: velocity 127), slide (0/1: the step
    overlaps
    the next, so a synth with mono and glide glides), octave (-3 to 3), cutoff (Hz: written as the instrument's
    filter.cutoff on a synth or a sampler with a filter, else the track's first filter effect), pan (-1 to 1, as
    the track's pan automation over the arp's bars). restart: 'key' (default: each chord starts the pattern
    again), 'bar', 'off'. swing: 0 to 0.5 of a step. bars=[a, b]: where it plays (default the bars of the
    track's notes). write=True writes the arp's notes into the track in place of the chords (and the cutoff and
    pan as automation), and turns the arp off: plain notes to edit (undo brings the chords back). off=True turns
    it off and keeps the chords."""
    P = _load(project)
    tr = P.track(track)
    bpb = P.bpb
    settings = dict(pattern=pattern, degrees=degrees, notes=notes, rate=rate, octaves=octaves, gate=gate,
                    velocity=velocity, accent=accent, slide=slide, octave=octave, cutoff=cutoff, pan=pan,
                    transpose=transpose, key=key, scale=scale, restart=restart, swing=swing)
    old = tr.get('arp')
    if off:
        if not old:
            raise OpError(f"track {track!r} has no arp; track_arp(project, track, pattern='up') starts one")
        del tr['arp']
        P.save()
        return f"{track}: arp off; the track plays its notes as written ({len(tr.get('notes') or [])} notes)"
    if not (tr.get('instrument')):
        raise OpError(f"track {track!r} has no instrument to play the arp; instrument_set first")
    try:
        cfg, changed = A.configure({k: v for k, v in (old or {}).items() if k != 'bars'}, settings)
    except A.ArpError as e:
        raise OpError(str(e))
    if bars is not None:
        if not (isinstance(bars, (list, tuple)) and len(bars) == 2 and 1 <= int(bars[0]) <= int(bars[1])):
            raise OpError(f"bars=[a, b]: the first and last bar the arp plays, a <= b, from 1 (got {bars!r})")
        cfg['bars'] = [int(bars[0]), int(bars[1])]
        changed.append('bars')
    elif old and old.get('bars'):
        cfg['bars'] = old['bars']
    if cfg.get('notes') is None and not tr.get('notes'):
        raise OpError(f"track {track!r} has no notes to arpeggiate: write the chords to hold (notes_write, e.g. "
                      f"'0 A3,C4,E4 4'), or give a step list with notes='A3 C4 E4 A4 | F3 A3 C4 F4' and bars=[a, b]")
    if cfg.get('notes') is not None and not cfg.get('bars') and not tr.get('notes'):
        raise OpError("a step list on a track with no notes needs bars=[a, b]: where it plays")
    full = dict(A.DEFAULTS, **cfg)
    st, sp = A.track_steps(tr, full, bpb)
    L = []
    target = A.cutoff_target(tr)
    if full.get('cutoff') is not None and target is None:
        L.append("cutoff: this voice exposes no filter cutoff (not a synth, no sampler filter, no filter effect on "
                 "the track), so the cutoff steps are not heard; add one with fx_add(..., {'type': 'filter', "
                 "'mode': 'lp', 'cutoff': 2000}) and the steps move it")
    span = f"bars {int(sp[0] // bpb) + 1}-{int((sp[1] - 1e-6) // bpb) + 1}" if sp else "no bars"
    if write:
        tr['arp'] = cfg
        ex = A.expand(tr, bpb)
        n_old = len(tr.get('notes') or [])
        tr['notes'] = ex['notes']
        tr['automation'] = ex.get('automation', tr.get('automation', {}))
        del tr['arp']
        P.save()
        moved = [k for k, on in (((target or 'cutoff'), full.get('cutoff') is not None and target),
                                 ('pan', full.get('pan') is not None)) if on]
        return '\n'.join([f"{track}: wrote the arp as {len(st)} steps ({len(ex['notes'])} notes now, {n_old} "
                          f"before) over {span}" + (f"; {', '.join(moved)} written as automation" if moved else '')
                          + ". The arp is off: these are plain notes to edit (undo brings the chords back)"]
                         + A.preview(st, bpb) + L)
    tr['arp'] = cfg
    P.save()
    head = (f"{track}: arp " + ("on" if not old else f"{', '.join(changed) or 'nothing'} changed") +
            f": {A.describe(full)} | {span}, {len(st)} steps")
    return '\n'.join([head] + A.preview(st, bpb) + L +
                     ["the notes stay as written; render plays the arp. write=True makes the steps plain notes"])
