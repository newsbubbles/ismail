"""track_midi_fx: MIDI effects in the studio (MIDI parity step 7b; live_midi_fx is its live twin, both on the core in
midifx.py). A track's written notes go through a chain of chord, scale, pitch, velocity, length and random effects
at render; write=True writes the result into the track as plain notes to edit. Registered into api.OPS on import."""
from . import midifx as MF
from .api import OpError, op, _load


@op(mutates=True)
def track_midi_fx(project: str, track: str, chain: list = None, off: bool = False, write: bool = False) -> str:
    """MIDI effects on a track at render, Ableton's Chord, Scale, Pitch, Velocity, Note Length and Random as one
    chain: the track's notes stay as written (notes_read shows them) and the render plays what the chain makes of
    them. The same core as live_midi_fx: a key on a beat live makes what a note on that beat renders. chain: a list of
    effects, applied in order to every note; a new chain replaces the old one. Effects:
    {'type': 'chord', 'intervals': [0, 4, 7], 'velocity': 0.8} (each note plays these semitones from it, 0 = itself;
    velocity scales the added notes, or a list one per interval);
    {'type': 'scale', 'key': 'A', 'scale': 'minor', 'mode': 'nearest'} (notes off the scale move onto it: 'nearest',
    'up', 'down', or 'drop' = not played; scale a name such as 'major', 'dorian', 'minor_pentatonic' or semitones);
    {'type': 'pitch', 'transpose': 12, 'lowest': 'C3', 'highest': 'C6', 'fold': False} (semitones, then notes outside
    the range are dropped, or folded by octaves into it);
    {'type': 'velocity', 'out': [40, 110], 'curve': 0.5, 'random': 10} (curve -1 to 1, above 0 lifts soft notes;
    out the range it maps into; random +- that much; or 'fixed': 100);
    {'type': 'length', 'beats': 0.5} or {'type': 'length', 'scale': 2} (every note that long, or that multiple of
    its written length);
    {'type': 'random', 'chance': 0.5, 'choices': 2, 'interval': 12, 'sign': 'up', 'seed': 0} (with that chance a note
    moves by 1 to choices times interval semitones, 'up', 'down' or 'both'; key + scale makes the interval scale steps;
    the seed makes the render the same every time). Notes past 0..127 are dropped; a pitch made twice plays once.
    With track_arp on the same track the chain runs first: the arp steps the notes it makes. No chain: says what is
    on the track. write=True writes the processed notes into the track in place of the written ones and takes the
    chain off: plain notes to edit (undo brings the written notes and the chain back). off=True takes the chain off
    and keeps the notes as written. Reach for it when the person wants chords from single notes, a part forced into
    a key, a range, a velocity shape, staccato or held notes, or random octave jumps, without rewriting the notes."""
    P = _load(project)
    tr = P.track(track)
    bpb = P.bpb
    old = tr.get('midi_fx')
    n_in = len(tr.get('notes') or [])
    if off:
        if not old:
            raise OpError(f"track {track!r} has no MIDI effects; track_midi_fx(project, {track!r}, chain=[{{'type': "
                          f"'chord', 'intervals': [0, 4, 7]}}]) puts some on")
        del tr['midi_fx']
        P.save()
        return f"{track}: MIDI effects off; the track plays its {n_in} notes as written"
    if chain is None and not write:
        if not old:
            return (f"{track}: no MIDI effects. track_midi_fx(project, {track!r}, chain=[{{'type': 'chord', "
                    f"'intervals': [0, 4, 7]}}, {{'type': 'scale', 'key': 'A', 'scale': 'minor'}}]) puts a chain on")
        ex = MF.expand(tr, bpb)['notes']
        return '\n'.join([f"{track}: MIDI effects: {MF.describe(old)} | {n_in} notes as written play as {len(ex)}"]
                         + MF.preview(ex, bpb))
    if chain is not None:
        try:
            cfg = MF.configure(chain)
        except MF.MidiFxError as e:
            raise OpError(str(e))
    elif old:
        cfg = old
    else:
        raise OpError(f"track {track!r} has no MIDI effects to write: give chain=[...] with write=True, or "
                      f"track_midi_fx(project, {track!r}, chain=[...]) first")
    tr['midi_fx'] = cfg
    ex = MF.expand(tr, bpb)['notes']
    arp = " The track's arp (track_arp) steps these notes: the chain runs first." if tr.get('arp') else ''
    if write:
        tr['notes'] = ex
        del tr['midi_fx']
        P.save()
        return '\n'.join([f"{track}: wrote the MIDI effects ({MF.describe(cfg)}) into the track: {len(ex)} notes now, "
                          f"{n_in} before. The effects are off: these are plain notes to edit (undo brings the "
                          f"written notes back).{arp}"] + MF.preview(ex, bpb))
    P.save()
    head = (f"{track}: MIDI effects " + ("on" if not old else "changed" if old != cfg else "unchanged") +
            f": {MF.describe(cfg)} | {n_in} notes as written play as {len(ex)}")
    tail = ("the notes stay as written; render plays the effects. write=True makes them plain notes" if n_in else
            "the track has no notes yet: the chain plays what notes_write puts there")
    return '\n'.join([head] + MF.preview(ex, bpb) + ([arp.strip()] if arp else []) + [tail])
