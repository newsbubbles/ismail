"""notes_clash: melodic leftovers read from the arrangement, no render (ledger:M282).

Two checks on the notes themselves: notes from different pitched parts that sound together a semitone apart (a
minor 2nd, a minor 9th, or those plus octaves), and notes outside the key. Ported from the planet_rock_studio
prototype (songs/planet_rock_studio/work/clash.py), which found every leftover melody Nate had called "weird" on
that song; it serves his 10-20 rule, "no weird melodies left over".

The interval rule is the prototype's: pitch classes a semitone apart, except a close major 7th (11 semitones, a
chord colour). A compound major 7th (23, 35 semitones) is still listed, as the prototype listed it: on that song it
was a blip over the chord's own major 7th, and the person decides. Notes of one part never clash with each other
(a chord voicing is the part's own choice). A minor key allows its raised 7th, as the prototype did (G minor with
F#, the third of the D chord).
"""
import math
import re
from collections import defaultdict

import numpy as np

from .api import OpError, _load, op
from .notation import NAMES, midi_to_name, pitch_to_midi

# words in a code voice's name that mean a drum or an effect, not a pitched part ('eh_drums', 'drums/kit70', 'eh_fx')
_UNPITCHED_WORDS = {'drum', 'drums', 'kit', 'perc', 'percussion', 'kick', 'snare', 'hat', 'hats', 'hihat', 'clap',
                    'tom', 'toms', 'cymbal', 'cymbals', 'ride', 'crash', 'fx', 'sfx', 'noise', 'riser', 'impact'}


def _unpitched(inst):
    """Why an instrument plays no pitch (its notes only trigger it), or None when it is pitched."""
    from .instruments import DRUM_DEFAULTS
    t = (inst or {}).get('type')
    if t is None:
        return 'audio only'
    if t in DRUM_DEFAULTS:
        return f'a {t} drum'
    if t == 'sampler' and inst.get('pitch_track') is False:
        return 'a fixed-pitch sample'
    if t in ('synth', 'sprite'):
        oscs = inst.get('oscs') or []
        if oscs and all(o.get('fixed_hz') for o in oscs):
            return 'a fixed-pitch synth'
    if t == 'code':
        words = [w for w in re.split(r'[^a-z0-9]+', str(inst.get('voice') or '').lower()) if w]
        if any(w in _UNPITCHED_WORDS or w.startswith(('drum', 'kit')) for w in words):
            return f"the {inst.get('voice')} voice, a drum or effect"
    return None


def _pitched_notes(P, tracks, bars):
    """([(track, start, end, midi, vel, level_db)], {track: why skipped}). A kit is read pad by pad: a pad that
    plays its note's pitch (a synth blip) is pitched, a drum, a fixed-pitch sample or synth is not, an unmapped
    pitch is silent. A track named in `tracks` is read whole, as pitched."""
    names = list(P.d['tracks'])
    if tracks:
        bad = [t for t in tracks if t not in P.d['tracks']]
        if bad:
            raise OpError(f"no track {', '.join(bad)}; tracks: {', '.join(names) or 'none'}")
        names = list(tracks)
    b0, b1 = (P.bar_to_beat(bars[0]), P.bar_to_beat(bars[1] + 1)) if bars else (-math.inf, math.inf)
    out, skipped = [], {}
    for name in names:
        tr = P.d['tracks'][name]
        named = bool(tracks)
        notes = [n for n in tr.get('notes', []) if n[0] + n[2] > b0 and n[0] < b1 and n[2] > 0]
        if not notes:
            continue
        if tr.get('mute') and not named:
            skipped[name] = 'muted'
            continue
        inst = tr.get('instrument') or {}
        fader = float(tr.get('volume_db', 0.0))
        if inst.get('type') == 'kit' and not named:
            pads = {pitch_to_midi(k): v for k, v in (inst.get('map') or {}).items()}
            keep, why = [], defaultdict(set)
            for n in notes:
                pad = pads.get(int(n[1]))
                w = 'unmapped, silent' if pad is None else _unpitched(pad)
                if w:
                    why[w].add(int(n[1]))
                else:
                    keep.append(n)
            if why:
                skipped[name] = '; '.join(f"pads {' '.join(midi_to_name(m) for m in sorted(ms))}: {w}"
                                          for w, ms in why.items()) + ('; its pitched pads are read' if keep else '')
            notes = keep
        elif not named:
            w = _unpitched(inst)
            if w:
                skipped[name] = w
                continue
        for n in notes:
            v = max(1, int(n[3]))
            out.append((name, float(n[0]), float(n[0] + n[2]), int(n[1]), v, 20 * math.log10(v / 127) + fader))
    return out, skipped


def _key_from_notes(notes):
    """(label, pitch classes, r) of the best Krumhansl key for the notes, weighted by length and velocity."""
    from .analysis import KS_MAJ, KS_MIN
    w = np.zeros(12)
    for _, a, b, m, v, _ in notes:
        w[m % 12] += (b - a) * v
    best = max(((float(np.corrcoef(np.roll(prof, r), w)[0, 1]), r, mode)
                for r in range(12) for prof, mode in ((KS_MAJ, 'major'), (KS_MIN, 'minor'))), key=lambda x: x[0])
    r, tonic, mode = best
    steps = (0, 2, 4, 5, 7, 9, 11) if mode == 'major' else (0, 2, 3, 5, 7, 8, 10)
    return f"{NAMES[tonic]} {mode}", {(tonic + s) % 12 for s in steps}, r


def _raised_seventh(label, pcs):
    """A minor key also allows its raised 7th (the major V chord's third): returns (pcs, note for the header)."""
    m = re.fullmatch(r'([A-G][#b]?) minor', label or '')
    if not m:
        return pcs, ''
    tonic = pitch_to_midi(m.group(1) + '0') % 12
    lead = (tonic + 11) % 12
    return pcs | {lead}, f" with {NAMES[lead]}, its raised 7th"


def _interval(d):
    octs = d // 12
    base = 'a semitone' if d % 12 == 1 else 'a major 7th'
    if d == 13:
        return 'a minor 9th'
    return base + (f" + {octs} octave{'s' if octs > 1 else ''}" if octs else '')


def _ranges(bars, cap=10):
    bars = sorted(set(bars))
    runs = []
    for b in bars:
        if runs and b == runs[-1][1] + 1:
            runs[-1][1] = b
        else:
            runs.append([b, b])
    txt = [f"{a}" if a == b else f"{a}-{b}" for a, b in runs]
    return ', '.join(txt[:cap]) + (f" +{len(txt) - cap} more" if len(txt) > cap else '')


def _at(beat, bpb):
    bar = int(beat // bpb) + 1
    off = round(beat - (bar - 1) * bpb, 3)
    return bar, f"{off:g}"


@op()
def notes_clash(project: str, key: str = None, min_overlap: float = 0.1, tracks: list = None, bars: list = None,
                n: int = 20) -> str:
    """Find melodic leftovers in the arrangement, from the notes (no render, ledger:M282): notes from DIFFERENT
    pitched parts sounding together a semitone apart (a minor 2nd, a minor 9th, or those plus octaves; a close
    major 7th of 11 semitones is a chord colour and not listed) for at least min_overlap beats, and notes out of
    the key. Reach for it when a melody sounds weird, wrong or left over, when two parts rub or clash, before an
    exam, and after removing or moving notes ("no weird melodies left over").
    key: 'G minor', 'Am', 'D dorian' (default the project's or the sketch's key; with none, found from the notes,
    weighted by length and velocity, and named with its fit r). A minor key allows its raised 7th (the major V
    chord's third). min_overlap (beats, default 0.1): shorter overlaps are passing tones and not listed; raise it
    to keep only held rubs. tracks=[names]: read only these, each as pitched. bars=[a, b] (inclusive) cuts the
    range. n: the most audible findings shown per list (counts cover all).
    Pitched parts only: kit pads that play their note's pitch (a synth blip) are read, drums, fixed-pitch samples
    and synths (a cowbell), code voices named as drums or effects ('eh_drums', 'drums/kit70'), unmapped pads and
    muted tracks are skipped and named. Notes of one part never clash with each other (a voicing).
    Returns a header (parts read and skipped, the key and where it came from), the clashes per pair of parts with
    their bars, then the top n clashes and the top n out-of-key notes, most audible first. A finding is one place
    in the bar that repeats: bar and beat in the bar from 0 (as notes_read shows it) of its loudest time, the two
    parts and their notes with velocity, the overlap in beats, the interval, how many times and in which bars.
    Audible = the quieter note's level (velocity and fader, dB) times the square root of the overlap."""
    if min_overlap < 0:
        raise OpError(f"min_overlap={min_overlap}: give 0 or more beats (0.1 ignores passing tones, 0.5 keeps only "
                      "held rubs)")
    n = int(n)
    if not 1 <= n <= 200:
        raise OpError(f"n={n}: give 1-200 findings per list")
    if bars and (len(bars) != 2 or bars[0] > bars[1]):
        raise OpError(f"bars={bars}: give [first, last], e.g. [9, 16]")
    P = _load(project)
    bpb = P.bpb
    notes, skipped = _pitched_notes(P, tracks, bars)
    from .api_measure import _song_key
    klabel, kpcs = _song_key(P, key)
    if klabel:
        where = 'as given' if key else "the project's"
    elif notes:
        klabel, kpcs, r = _key_from_notes(notes)
        where = f"found from the notes, r={r:.2f}"
    else:
        where = ''
    kpcs, raised = _raised_seventh(klabel, kpcs) if klabel else (kpcs, '')
    parts = sorted({x[0] for x in notes})
    L = [f"notes_clash{f' bars {bars[0]}-{bars[1]}' if bars else ''}: {len(notes)} pitched notes in {len(parts)} "
         f"parts ({', '.join(parts) or 'none'}); overlap at least {min_overlap:g} beat"]
    if skipped:
        L.append("skipped: " + '; '.join(f"{t} ({w})" for t, w in skipped.items()))
    if not notes:
        L.append("no pitched notes to check" + ('' if tracks else "; name a part with tracks=[...] to read it as "
                                                "pitched"))
        return '\n'.join(L)
    L.append(f"key: {klabel}{raised} ({where})")

    # semitone clashes between parts
    srt = sorted(notes, key=lambda x: x[1])
    groups = defaultdict(list)
    for i, (t1, a1, b1, m1, v1, l1) in enumerate(srt):
        for t2, a2, b2, m2, v2, l2 in srt[i + 1:]:
            if a2 >= b1:
                break
            if t1 == t2:
                continue
            ov = min(b1, b2) - max(a1, a2)
            d = abs(m1 - m2)
            if ov < max(min_overlap, 1e-9) or d % 12 not in (1, 11) or d == 11:
                continue
            (ta, ma, va, la), (tb, mb, vb, lb) = sorted([(t1, m1, v1, l1), (t2, m2, v2, l2)])
            at = max(a1, a2)
            bar, off = _at(at, bpb)
            score = 10 ** (min(la, lb) / 20) * math.sqrt(ov)
            groups[(ta, tb, ma, mb, off)].append((score, bar, off, va, vb, round(ov, 3), min(la, lb)))
    pairs = defaultdict(lambda: [0, set()])
    for (ta, tb, *_), xs in groups.items():
        pairs[(ta, tb)][0] += len(xs)
        pairs[(ta, tb)][1].update(x[1] for x in xs)
    total = sum(len(xs) for xs in groups.values())
    places = f"{len(groups)} place{'' if len(groups) == 1 else 's'} in the bar"
    L.append(f"semitone clashes between parts: {total} ({places})")
    for (ta, tb), (cnt, bs) in sorted(pairs.items(), key=lambda kv: -kv[1][0]):
        L.append(f"  {ta} x {tb}: {cnt} in bars {_ranges(bs)}")
    ranked = sorted(groups.items(), key=lambda kv: (-max(x[0] for x in kv[1]), -len(kv[1])))
    if ranked:
        L.append(f"most audible clashes (top {min(n, len(ranked))} of {len(ranked)}):")
    for k, ((ta, tb, ma, mb, _), xs) in enumerate(ranked[:n], 1):
        s, bar, off, va, vb, ov, lv = max(xs)
        L.append(f"  {k}. bar {bar} +{off}: {ta} {midi_to_name(ma)} v{va} vs {tb} {midi_to_name(mb)} v{vb}, "
                 f"{_interval(abs(ma - mb))}, {ov:g} beat, quieter note {lv:.0f} dB"
                 + (f"; x{len(xs)} in bars {_ranges(x[1] for x in xs)}" if len(xs) > 1 else ''))

    # out of key
    out = defaultdict(list)
    for t, a, b, m, v, lv in notes:
        if m % 12 not in kpcs:
            bar, off = _at(a, bpb)
            out[(t, m, off)].append((10 ** (lv / 20) * math.sqrt(b - a), bar, off, v, round(b - a, 3), lv))
    total = sum(len(xs) for xs in out.values())
    by_pc = defaultdict(int)
    for (_, m, _), xs in out.items():
        by_pc[NAMES[m % 12]] += len(xs)
    pcs = ', '.join(f'{p} {c}' for p, c in sorted(by_pc.items(), key=lambda kv: -kv[1]))
    L.append(f"out of key: {total}" + (f" ({pcs})" if total else ''))
    ranked = sorted(out.items(), key=lambda kv: (-max(x[0] for x in kv[1]), -len(kv[1])))
    if ranked:
        L.append(f"most audible out of key (top {min(n, len(ranked))} of {len(ranked)}):")
    for k, ((t, m, _), xs) in enumerate(ranked[:n], 1):
        s, bar, off, v, dur, lv = max(xs)
        L.append(f"  {k}. bar {bar} +{off}: {t} {midi_to_name(m)} v{v}, {dur:g} beat, {lv:.0f} dB"
                 + (f"; x{len(xs)} in bars {_ranges(x[1] for x in xs)}" if len(xs) > 1 else ''))
    if groups or out:
        L.append("each is a candidate leftover: keep it if it is meant (a short colour), else move or remove the "
                 "note (notes_read shows the bar, notes_clear removes a pitch)")
    return '\n'.join(L)
