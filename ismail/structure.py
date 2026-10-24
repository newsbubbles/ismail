"""Macro structure from step features: arrangement map, sections, repetition, loop length."""
import numpy as np

from . import features as FE
from .notation import NAMES

DIGITS = '0123456789'
# absolute floor (dB on the features' level scale): digital silence reads about -117 dB per bar, real material far
# above -100. A row that never rises above it is empty (a stem group with no tracks), not "loud at its own top".
SILENT_ABS_DB = -100.0


def bar_matrix(F, key):
    spb = FE.steps_per_bar(F)
    x = F[key]
    nb = len(x) // spb
    return x[:nb * spb].reshape((nb, spb) + x.shape[1:])


def bar_levels(F):
    """Energy-mean level per bar (dB)."""
    L = bar_matrix(F, 'level')
    return 10 * np.log10(np.mean(10 ** (L / 10), axis=1) + 1e-12)


def level_row(levels, top=None, step_db=4.0, silent_db=36.0):
    top = np.max(levels) if top is None else top
    out = []
    for v in levels:
        d = v - top
        out.append('.' if d < -silent_db or v < SILENT_ABS_DB else DIGITS[int(np.clip(round(9 + d / step_db), 0, 9))])
    return ''.join(out)


def ruler(nb, start=1):
    marks = ''.join(str((b // 10) % 10) if b % 10 == 0 else ' ' for b in range(start, start + nb))
    ones = ''.join(str(b % 10) for b in range(start, start + nb))
    return marks, ones


def bar_vectors(F):
    """Per-bar descriptor for similarity: pitch-class profile of sounding notes, band shape, drum-lane densities."""
    spb = FE.steps_per_bar(F)
    sets = FE.note_sets(F)
    nb = len(F['level']) // spb
    vecs = []
    for b in range(nb):
        sl = slice(b * spb, (b + 1) * spb)
        pc = np.zeros(12)
        for s in range(sl.start, sl.stop):
            for p in np.nonzero(sets[s])[0]:
                pc[(p + FE.PITCH_BASE) % 12] += 1
        pc = pc / (pc.sum() + 1e-9)
        bands = F['bands'][sl].mean(0)
        bands = (bands - bands.max()) / 20
        hits = (F['hits'][sl] > -16).mean(0)
        lvl = F['level'][sl].mean() / 20
        vecs.append(np.r_[pc * 3, bands, hits, lvl])
    return np.array(vecs)


def sim_matrix(V):
    Z = (V - V.mean(0)) / (V.std(0) + 1e-6)
    Z = Z / (np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9)
    return Z @ Z.T


def arrangement_vectors(F_mix, stems=None):
    """Per-bar arrangement descriptor (no pitch): mix band levels, stem levels, drum-lane densities."""
    spb = FE.steps_per_bar(F_mix)
    nb = len(F_mix['level']) // spb
    B = bar_matrix(F_mix, 'bands')[:nb]
    bl = 10 * np.log10(np.mean(10 ** (B / 10), axis=1) + 1e-12)
    cols = [(bl - bl.max()) / 10]
    for Fs in (stems or {}).values():
        lv = bar_levels(Fs)[:nb]
        cols.append(((lv - lv.max()) / 10)[:, None])
    drum = (stems or {}).get('drums', F_mix)
    H = (drum['hits'][:nb * spb] > -16).reshape(nb, spb, 3).mean(1)
    cols.append(H * 2)
    return np.concatenate(cols, 1)


ROOT_CHARS = 'CcDdEFfGgAaB'  # lowercase = sharp (c = C#)


def root_row(F, min_frac=0.25):
    """One char per bar: pitch class of the lowest note sounding in >= min_frac of the bar's steps."""
    spb = FE.steps_per_bar(F)
    sets = FE.note_sets(F)
    nb = len(sets) // spb
    out = []
    for b in range(nb):
        frac = sets[b * spb:(b + 1) * spb].mean(0)
        ps = np.nonzero(frac >= min_frac)[0]
        out.append(ROOT_CHARS[(ps[0] + FE.PITCH_BASE) % 12] if len(ps) else '.')
    return ''.join(out)


def sections(F, min_bars=4, kernel=4, thresh=0.35, V=None):
    V = bar_vectors(F) if V is None else V
    Sm = sim_matrix(V)
    nb = len(V)
    k = kernel
    kern = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((k, k)))
    nov = np.zeros(nb)
    P = np.pad(Sm, k, mode='edge')
    for i in range(nb):
        nov[i] = (P[i:i + 2 * k, i:i + 2 * k] * kern).sum()
    lv = bar_levels(F)
    jump = np.r_[0, np.abs(np.diff(lv))]
    score = np.clip(nov, 0, None) / (np.clip(nov, 0, None).max() + 1e-9) + jump / (jump.max() + 1e-9) * 0.8
    bounds = [0]
    for i in np.argsort(-score):
        if score[i] < thresh:
            break
        if all(abs(i - b) >= min_bars for b in bounds) and nb - i >= min_bars:
            bounds.append(int(i))
    bounds = sorted(bounds) + [nb]
    segs = [{'start': a + 1, 'end': b, 'vec': V[a:b].mean(0)} for a, b in zip(bounds[:-1], bounds[1:])]
    protos = []
    for s in segs:
        best = None
        for li, pv in enumerate(protos):
            c = float(np.dot(s['vec'] - V.mean(0), pv - V.mean(0)) /
                      (np.linalg.norm(s['vec'] - V.mean(0)) * np.linalg.norm(pv - V.mean(0)) + 1e-9))
            if c > 0.8 and (best is None or c > best[1]):
                best = (li, c)
        if best is None:
            protos.append(s['vec'])
            s['label'] = chr(65 + len(protos) - 1)
        else:
            s['label'] = chr(65 + best[0]) + "'"
        del s['vec']
    return segs, Sm


def loop_length(Sm, max_lag=16):
    """Dominant repetition period in bars: mean similarity along each diagonal."""
    nb = len(Sm)
    scores = []
    for lag in range(1, min(max_lag, nb - 2) + 1):
        scores.append((float(np.mean(np.diag(Sm, lag))), lag))
    scores.sort(reverse=True)
    return scores[:3]


def repeats(Sm, segs, min_sim=0.6):
    """For each section, the most similar earlier stretch of the same length (bar-by-bar diagonal mean)."""
    out = []
    for s in segs:
        a, L = s['start'] - 1, s['end'] - s['start'] + 1
        best = None
        for j in range(0, a - L + 1):
            v = float(np.mean([Sm[a + t, j + t] for t in range(L)]))
            if best is None or v > best[0]:
                best = (v, j + 1)
        if best and best[0] >= min_sim:
            out.append((s, best[1], best[1] + L - 1, best[0]))
    return out


def top_pitch_classes(F, b0, b1, n=4):
    spb = FE.steps_per_bar(F)
    sets = FE.note_sets(F)[(b0 - 1) * spb:b1 * spb]
    pc = np.zeros(12)
    for row in sets:
        for p in np.nonzero(row)[0]:
            pc[(p + FE.PITCH_BASE) % 12] += 1
    order = np.argsort(-pc)[:n]
    return ' '.join(NAMES[i] for i in order if pc[i] > 0)


def common_hit_pattern(F, b0, b1, lane):
    spb = FE.steps_per_bar(F)
    H = FE.hit_sets(F)[(b0 - 1) * spb:b1 * spb, FE.LANES.index(lane)]
    nb = len(H) // spb
    if nb == 0:
        return ''
    frac = H[:nb * spb].reshape(nb, spb).mean(0)
    return ''.join('X' if f > 0.66 else 'x' if f > 0.33 else '.' for f in frac)


def describe(F_mix, stems=None, max_bars=None):
    """Text: arrangement map (mix bands, stems, bass root per bar), arrangement sections, harmonic loop, repeats."""
    stems = stems or {}
    nb = len(F_mix['level']) // FE.steps_per_bar(F_mix)
    if max_bars:
        nb = min(nb, max_bars)
    segs, _ = sections(F_mix, V=arrangement_vectors(F_mix, stems))
    Sm = sim_matrix(bar_vectors(stems.get('other', F_mix)))
    lines = ["ARRANGEMENT MAP (one char per bar; 9 = that row's loudest bar, each step down = 4 dB, '.' = silent;"
             " root = lowest steady note, lowercase = sharp)"]
    marks, ones = ruler(nb)
    lines += [f"{'':>9} {marks}", f"{'bar':>9} {ones}"]
    lines.append(f"{'mix':>9} {level_row(bar_levels(F_mix)[:nb])}")
    B = bar_matrix(F_mix, 'bands')
    for i, name in enumerate(FE.BAND_NAMES):
        bl = 10 * np.log10(np.mean(10 ** (B[:nb, :, i] / 10), axis=1) + 1e-12)
        lines.append(f"{'~' + name:>9} {level_row(bl)}")
    for name, Fs in stems.items():
        lines.append(f"{name:>9} {level_row(bar_levels(Fs)[:nb])}")
    lines.append(f"{'root':>9} {root_row(stems.get('other', F_mix))[:nb]}")
    lab = [' '] * nb
    for s in segs:
        for b in range(s['start'] - 1, min(s['end'], nb)):
            lab[b] = s['label'][0] if b == s['start'] - 1 else ('-' if b < s['end'] - 1 else '|')
    lines.append(f"{'section':>9} {''.join(lab)}")
    lines.append("\nSECTIONS by arrangement (letters = similar arrangement; ' = variant)")
    for s in segs:
        a, b = s['start'], s['end']
        act = [n for n, Fs in stems.items()
               if np.max(bar_levels(Fs)[a - 1:b]) > max(np.max(bar_levels(Fs)) - 18, SILENT_ABS_DB)]
        line = f"{s['label']:<3} bars {a:>3}-{b:<3} ({b - a + 1:>2})  level {np.mean(bar_levels(F_mix)[a - 1:b]):6.1f} dB" \
               f"  pcs: {top_pitch_classes(stems.get('other', F_mix), a, b) or '-'}"
        if stems:
            line += f"  loud stems: {', '.join(act) or '-'}"
        drum = stems.get('drums', F_mix)
        for lane in ('low', 'snare', 'hat'):
            k = common_hit_pattern(drum, a, b, lane)
            if 'X' in k or 'x' in k:
                line += f"  {lane} {k}"
        lines.append(line)
        s['active'] = act
    ll = loop_length(Sm)
    lines.append("\nHARMONIC LOOP: strongest repetition periods of the pitch content (bars, similarity): " +
                 ', '.join(f"{lag} ({v:.2f})" for v, lag in ll))
    rep = repeats(Sm, segs)
    if rep:
        lines.append("REPEATS (arrangement section ~ earlier bars with the same pitch content, similarity)")
        for s, j0, j1, v in rep:
            lines.append(f"  bars {s['start']}-{s['end']} ~ bars {j0}-{j1} ({v:.2f})")
    return {'sections': segs, 'loop': ll}, '\n'.join(lines)
