"""A feedback round's parts block (ledger:M253): each part as a short clip, alone and in the mix, at the bars where it
is most exposed, so the person can name it by ear.

A listener said the bass overpowered the track; they meant the answering bass, the agent changed another one, and
three rounds went by before anyone noticed. Nobody had checked that the agent's names for the parts and the
person's were the same. A part's name is confirmed only by the person, after hearing it alone.
"""
import os

QUIET_DB = -20.0     # stretches this far under the part's own loudest are left out: a part is named where it plays
FADE_S = 0.01


def _mono(y):
    return y.mean(axis=1) if y.ndim > 1 else y


def _ms(y, sr, g, a, b):
    i, j = max(int(g.bar_time(a) * sr), 0), max(int(g.bar_time(b) * sr), 0)
    seg = y[i:j]
    return float((seg.astype('float64') ** 2).mean()) if len(seg) else 0.0


def exposed(part, mix, sr, g, first, end, span):
    """-> (a, b) bars [a, b) where the part's share of the mix is highest, among the stretches where it plays (within
    QUIET_DB of its loudest), or None when it never plays."""
    starts = list(range(first, max(first + 1, end - span + 1)))
    rows = []
    for a in starts:
        b = min(a + span, end)
        if b - a < 1:
            continue
        rows.append((a, b, _ms(part, sr, g, a, b), _ms(mix, sr, g, a, b)))
    top = max((r[2] for r in rows), default=0.0)
    if top <= 1e-12:
        return None
    floor = top * 10 ** (QUIET_DB / 10)
    best = max((r for r in rows if r[2] >= floor), key=lambda r: (r[2] / (r[3] + 1e-12), r[2]))
    return best[0], best[1]


def _cut(y, sr, g, a, b):
    import numpy as np
    i, j = max(int(g.bar_time(a) * sr), 0), max(int(g.bar_time(b) * sr), 0)
    seg = np.array(y[i:j], dtype='float32')
    n = min(int(FADE_S * sr), len(seg) // 2)
    if n:
        ramp = np.linspace(0.0, 1.0, n, dtype='float32')
        ramp = ramp[:, None] if seg.ndim > 1 else ramp
        seg[:n] *= ramp
        seg[-n:] *= ramp[::-1]
    return seg


def clips(P, tracks, out_dir, span=4):
    """Write each part's clips into out_dir: <track>-solo.wav and <track>-mix.wav at its most exposed bars.
    -> ([{track, bars: [a, b] (inclusive), solo, mix}], [(track, why left out)]). Needs the latest render's stems
    (render(stems=True)); raises ValueError when there is no render to cut from."""
    import soundfile as sf
    mix_path, g = P.source('render')
    mix, sr = sf.read(mix_path, always_2d=True, dtype='float32')
    w = P.render_window(mix_path)
    first = w['bars'][0] if w else 1
    end = (w['bars'][1] + 1) if w else int(g.time_to_bar(len(mix) / sr))
    os.makedirs(out_dir, exist_ok=True)
    out, skipped = [], []
    for t in tracks:
        try:
            path, _ = P.source(f'track:{t}')
        except Exception:                  # noqa: BLE001  no stem for it in the latest render
            skipped.append((t, 'no stem in the latest render'))
            continue
        y, sr_t = sf.read(path, always_2d=True, dtype='float32')
        if sr_t != sr:
            skipped.append((t, f'its stem is {sr_t} Hz and the mix {sr} Hz'))
            continue
        span_bars = exposed(_mono(y), _mono(mix), sr, g, first, end, span)
        if not span_bars:
            skipped.append((t, 'silent in the latest render'))
            continue
        a, b = span_bars
        safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in t)
        solo, full = os.path.join(out_dir, f'{safe}-solo.wav'), os.path.join(out_dir, f'{safe}-mix.wav')
        sf.write(solo, _cut(y, sr, g, a, b), sr)
        sf.write(full, _cut(mix, sr, g, a, b), sr)
        out.append({'track': t, 'bars': [a, b - 1], 'solo': os.path.abspath(solo), 'mix': os.path.abspath(full)})
    return out, skipped


# ------------------------------------------------------------------ which parts a note's words could mean

FAMILY = ('bass', 'sub', 'kick', 'snare', 'clap', 'hat', 'hats', 'cymbal', 'drums', 'drum', 'perc', 'lead', 'pad',
          'pads', 'chord', 'chords', 'keys', 'piano', 'synth', 'vocal', 'vocals', 'voice', 'vox', 'guitar', 'strings',
          'arp', 'pluck', 'fx', 'riser', 'growl', 'reese', 'stab', 'organ', 'brass', 'horn', 'flute', 'choir')


def _tokens(text):
    import re
    return re.findall(r"[a-z0-9]+", str(text or '').lower())


def part_words(d):
    """{track: set of words that could name it}: its name's words, its stem in the stem map, its instrument's type
    and preset, so 'the bass' finds every part filed or built as a bass."""
    sm = d.get('stem_map') or {}
    out = {}
    for t, tr in (d.get('tracks') or {}).items():
        inst = tr.get('instrument')
        bits = [t, sm.get(t, '')]
        if isinstance(inst, dict):
            bits += [inst.get('type', ''), inst.get('preset', ''), inst.get('profile', ''), inst.get('voice', '')]
        elif isinstance(inst, str):
            bits.append(inst.split(':', 1)[-1])
        words = set()
        for b in bits:
            words |= set(_tokens(str(b).replace('_', ' ')))
        words |= {w.rstrip('s') for w in words}
        out[t] = words
    return out


def ambiguous(text, d, names=None):
    """[(word, [tracks])]: words in a note that name a family several parts belong to, where the note names none of
    those parts (by the agent's name or a name the person confirmed). names: {track: the person's name for it}."""
    words = part_words(d)
    said = set(_tokens(text))
    low = ' ' + ' '.join(_tokens(text)) + ' '
    out = []
    for w in FAMILY:
        if w not in said:
            continue
        root = w.rstrip('s')
        cands = sorted(t for t, ws in words.items() if w in ws or root in ws)
        if len(cands) < 2:
            continue
        named = [t for t in cands if f" {' '.join(_tokens(t.replace('_', ' ')))} " in low
                 or (names and names.get(t) and f" {' '.join(_tokens(names[t]))} " in low)]
        if not named:
            out.append((w, cands))
    return out


def named(text, d, names=None):
    """The one part a note names (by the agent's name for it, a name the person confirmed, or a family word only one
    part belongs to), or None when it names none or several."""
    words = part_words(d)
    said = set(_tokens(text))
    low = ' ' + ' '.join(_tokens(text)) + ' '
    hit = {t for t in words if f" {' '.join(_tokens(t.replace('_', ' ')))} " in low
           or (names and names.get(t) and f" {' '.join(_tokens(names[t]))} " in low)}
    for w in FAMILY:
        if w in said:
            root = w.rstrip('s')
            cands = [t for t, ws in words.items() if w in ws or root in ws]
            if len(cands) == 1:
                hit.add(cands[0])
    return next(iter(hit)) if len(hit) == 1 else None
