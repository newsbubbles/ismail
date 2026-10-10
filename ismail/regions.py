"""Measuring inside a named area of a picture round (Migration 8, part A; vox songs/vox/notes/HANDOFF_ISMAIL.md).

A person draws a box or a loop on the reveal of an exam_picture_round card and names it. The area is saved as a polygon
in seconds (from the card's window start) and Hz. This module measures the audio inside that polygon on BOTH
pictures' sounds (the real file and ours, each at its own window), so a name gathers numbers over many instances.

A polygon is reduced to its bounding box plus a mask on the picture's own time-frequency grid. The measures are ported
as faithfully as possible from vox's work folder (names below say where each came from; the function bodies are
vox's, only the arguments are made explicit):

  picture, stft_db      vox work/perceptoids.py picture (the dB crop f, t, S, 0 to 16 kHz, HOP 64) on
                        work/eyeword.py stft_db (scipy stft, 1024 points)
  follow, stats         vox work/perceptoids.py follow and stats (stroke life, drift, bend, hold)
  mid_texture           vox work/perceptoids.py mid_texture (the band between the stroke and plume scales)
  held                  vox work/z_texture.py held (lag_ms=20 here)
  level                 plain: mean dB of the picture inside the mask

Where vox measured one pin, a polygon seeds several points inside it and reports the median, never one point:
mid texture is given at the polygon's centroid AND as the median over the seeds; stroke life, drift, bend and hold
are the median over the seeds. Nothing is ported from vox's atlas.py (unstable, being rewritten).

"Mean z" (the diff map's z inside the polygon) is a slot that stays empty until the diff arrives (Migration 4).
"""
import json
import os
import time

import numpy as np
from scipy.ndimage import gaussian_filter, uniform_filter1d

HOP = 64                    # perceptoids.py HOP: the picture's frame step in samples
MAX_POINTS = 40             # a saved polygon is simplified to at most this many points
MEAN_Z_NOTE = 'mean z arrives with the diff (Migration 4); empty until then'


# ---- the polygon: pixels, seconds and Hz, simplifying, the mask ---------------------------------------------------

def frac_to_sec_hz(fx, fy, axes, dur, f_lo, f_hi):
    """A spot on a picture as fractions of the image (fx from the left, fy from the TOP, as a page reads a pointer)
    -> (seconds from the window start, Hz). The same mapping as the comments of the page (words.html at()) and
    analysis.eye_point; a spot off the plot box is held on its edge. axes: the plot box (left, bottom, width, height)
    in figure fractions; dur: the window's length in s; f_lo, f_hi: the band shown."""
    l, b, w, h = axes
    u = min(1.0, max(0.0, (fx - l) / w))
    v = min(1.0, max(0.0, (1 - fy - b) / h))
    return u * dur, f_lo + v * (f_hi - f_lo)


def sec_hz_to_frac(s, hz, axes, dur, f_lo, f_hi):
    """The inverse of frac_to_sec_hz (a page draws a saved area back with it)."""
    l, b, w, h = axes
    return l + (s / dur) * w, 1 - b - ((hz - f_lo) / (f_hi - f_lo)) * h


def _rdp(pts, eps):
    pts = np.asarray(pts, float)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        n = np.hypot(*d)
        seg = pts[a + 1:b] - p
        dist = np.abs(seg[:, 0] * d[1] - seg[:, 1] * d[0]) / n if n > 0 else np.hypot(seg[:, 0], seg[:, 1])
        k = int(np.argmax(dist))
        if dist[k] > eps:
            keep[a + 1 + k] = True
            stack += [(a, a + 1 + k), (a + 1 + k, b)]
    return pts[keep]


def simplify(points, max_points=MAX_POINTS):
    """A closed outline reduced to at most max_points points (Ramer-Douglas-Peucker, the tolerance grown until it
    fits). The closing point is dropped if it repeats the first. -> [[x, y], ...]"""
    pts = [list(map(float, p)) for p in points]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) <= max_points:
        return pts
    scale = max(np.ptp([p[0] for p in pts]), np.ptp([p[1] for p in pts]), 1e-12)
    eps = scale * 1e-3
    out = pts
    while len(out) > max_points:
        out = _rdp(pts + [pts[0]], eps)[:-1].tolist()      # closed: the first point is both ends of the path
        eps *= 1.5
    return out


def polygon_to_sec_hz(frac_points, axes, dur, f_lo, f_hi, max_points=MAX_POINTS):
    """What the page posts: [[fx, fy]] fractions of the image -> the saved polygon [[s, Hz]], simplified to at most
    max_points (in image space, where a loop is isotropic) before it is mapped."""
    return [[round(s, 5), round(hz, 2)] for s, hz in
            (frac_to_sec_hz(x, y, axes, dur, f_lo, f_hi) for x, y in simplify(frac_points, max_points))]


def box_polygon(s0, s1, f0, f1):
    return [[s0, f0], [s1, f0], [s1, f1], [s0, f1]]


def _inside(px, py, poly):
    """Even-odd test of grid points (arrays px, py) against a polygon [(x, y)...]."""
    inside = np.zeros(px.shape, bool)
    n = len(poly)
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[i - 1]
        dy = (yj - yi) or 1e-30
        inside ^= ((yi > py) != (yj > py)) & (px < (xj - xi) * (py - yi) / dy + xi)
    return inside


def centroid(poly):
    """The area centroid of a polygon [(x, y)...] (the vertex mean when it has no area)."""
    p = np.asarray(poly, float)
    x, y = p[:, 0], p[:, 1]
    x1, y1 = np.roll(x, -1), np.roll(y, -1)
    cr = x * y1 - x1 * y
    a = cr.sum() / 2
    if abs(a) < 1e-18:
        return float(x.mean()), float(y.mean())
    return float(((x + x1) * cr).sum() / (6 * a)), float(((y + y1) * cr).sum() / (6 * a))


def mask_on_grid(poly, f, t):
    """The polygon [(t_s, f_hz)...] on a picture's grid (f rows, t columns) -> (rows slice, cols slice, mask over
    that bounding box). A polygon too small to hold a cell keeps the cell nearest its centroid."""
    p = np.asarray(poly, float)
    t0, t1, f0, f1 = p[:, 0].min(), p[:, 0].max(), p[:, 1].min(), p[:, 1].max()
    ri = np.where((f >= f0) & (f <= f1))[0]
    ci = np.where((t >= t0) & (t <= t1))[0]
    if len(ri) == 0:
        ri = np.array([int(np.argmin(np.abs(f - (f0 + f1) / 2)))])
    if len(ci) == 0:
        ci = np.array([int(np.argmin(np.abs(t - (t0 + t1) / 2)))])
    rows, cols = slice(ri[0], ri[-1] + 1), slice(ci[0], ci[-1] + 1)
    TT, FF = np.meshgrid(t[cols], f[rows])
    m = _inside(TT, FF, [tuple(q) for q in p])
    if not m.any():
        cx, cy = centroid(p)
        m[int(np.argmin(np.abs(f[rows] - cy))), int(np.argmin(np.abs(t[cols] - cx)))] = True
    return rows, cols, m


def seeds(poly, f, t, rows, cols, mask, grid=3):
    """The points the trackers and the median texture start from: the centroid first (its cell, or the nearest cell
    inside when the loop is hollow there), then a grid x grid lattice over the bounding box, those inside the mask.
    -> [(i, j)] cell indices into the picture, unique."""
    ri0, ci0 = rows.start, cols.start
    cx, cy = centroid(poly)
    cells = np.argwhere(mask)

    def nearest(x, y):
        d = ((t[ci0 + cells[:, 1]] - x) / max(np.ptp(t[cols]), 1e-9)) ** 2 + \
            ((f[ri0 + cells[:, 0]] - y) / max(np.ptp(f[rows]), 1e-9)) ** 2
        r, c = cells[int(np.argmin(d))]
        return ri0 + int(r), ci0 + int(c)

    out = [nearest(cx, cy)]
    p = np.asarray(poly, float)
    for ys in np.linspace(p[:, 1].min(), p[:, 1].max(), grid + 2)[1:-1]:
        for xs in np.linspace(p[:, 0].min(), p[:, 0].max(), grid + 2)[1:-1]:
            i, j = int(np.argmin(np.abs(f - ys))), int(np.argmin(np.abs(t - xs)))
            if rows.start <= i < rows.stop and cols.start <= j < cols.stop and mask[i - ri0, j - ci0] \
                    and (i, j) not in out:
                out.append((i, j))
    return out


# ---- the measures (vox) -------------------------------------------------------------------------------------------

def stft_db(y, sr, n=1024, hop=128):
    """vox work/eyeword.py stft_db."""
    from scipy.signal import stft
    f, t, Z = stft(y, sr, nperseg=n, noverlap=n - hop, boundary=None)
    return f, t, 10 * np.log10(np.abs(Z) ** 2 + 1e-14)


def picture(y, sr, t0, t1, pad=0.12):
    """vox work/perceptoids.py picture: the dB crop of t0..t1 s (padded) -> f (0 to 16 kHz), t (s in the file), S."""
    a = max(0.0, t0 - pad)
    seg = y[int(a * sr):int((t1 + pad) * sr)]
    f, t, S = stft_db(seg, sr, 1024, HOP)
    keep = f <= 16000
    return f[keep], t + a, S[keep]


def follow(P, f, t, i0, j0, step=4, ph=8, pf=15, search=6, stop=0.6, reach=0.08):
    """vox work/perceptoids.py follow. From (row i0, column j0): the patch (2pf+1 rows x 2ph+1 columns) carried along
    time, re-found each step within +-search rows; returns the path [(t, f)] and the mean correlation along it."""
    def patch(i, j):
        if i - pf < 0 or i + pf >= P.shape[0] or j - ph < 0 or j + ph >= P.shape[1]:
            return None
        x = P[i - pf:i + pf + 1, j - ph:j + ph + 1]
        x = x - x.mean()
        return x / (np.linalg.norm(x) + 1e-9)
    path, cors = [(t[j0], f[i0])], []
    for d in (1, -1):
        tpl, i, j = patch(i0, j0), i0, j0
        while tpl is not None:
            j += d * step
            if j < 0 or j >= len(t) or abs(t[j] - t[j0]) > reach:
                break
            best = max(((float((patch(i + k, j) * tpl).sum()), i + k) for k in range(-search, search + 1)
                        if patch(i + k, j) is not None), default=(None, None))
            if best[0] is None or best[0] < stop:
                break
            i = best[1]
            cors.append(best[0])
            path.append((t[j], f[i]))
            tpl = 0.5 * tpl + 0.5 * patch(i, j)
            tpl = tpl / (np.linalg.norm(tpl) + 1e-9)
    path.sort()
    return np.array(path), (np.mean(cors) if cors else np.nan)


def stats(path):
    """vox work/perceptoids.py stats: life (ms), drift (kHz per 10 ms), bend (Hz off a straight path)."""
    if len(path) < 3:
        return dict(life=0.0, drift=np.nan, bend=np.nan)
    tt, ff = path[:, 0], path[:, 1]
    fit = np.polyval(np.polyfit(tt, ff, 1), tt)
    return dict(life=1000 * (tt[-1] - tt[0]), drift=np.median(np.abs(np.diff(ff) / np.diff(tt))) * 1e-4,   # kHz/10ms
                bend=float(np.sqrt(np.mean((ff - fit) ** 2))))


def mid_band(S):
    """The picture band-passed between the stroke and plume scales (the body of perceptoids.mid_texture)."""
    return gaussian_filter(S, (3.0, 1.5)) - gaussian_filter(S, (12.0, 5.0))


def mid_texture(S, f, t, tc, fc, band=None):
    """vox work/perceptoids.py mid_texture: dB rms of the mid-scale band in a 40 ms x 2 kHz box at (tc, fc).
    band: mid_band(S) when the caller has it already (the same numbers, computed once)."""
    if band is None:
        band = mid_band(S)
    m = (np.abs(t - tc) <= 0.02)
    r = (np.abs(f - fc) <= 1000)
    return float(np.sqrt(np.mean(band[np.ix_(r, m)] ** 2)))


def held(y, sr, t0, t1, lag_ms=20):
    """vox work/z_texture.py held. How alike the structure stays: the 4-12 kHz log spectrum smoothed over 400 Hz (the
    grain removed) and its 2 kHz trend removed; the correlation of that residual between frames lag_ms apart (1 =
    held still). Over t0..t1 s of the file; it always reads the 4-12 kHz band, as vox's did."""
    n, h = 2048, int(0.005 * sr)
    a, b = int(t0 * sr), int(t1 * sr)
    fr = [y[i - n // 2:i + n // 2] for i in range(max(a, n // 2), min(b, len(y) - n // 2), h)]
    k = int(lag_ms / 5)
    if len(fr) <= k + 1:
        return np.nan
    f = np.fft.rfftfreq(n, 1 / sr)
    S = np.array([10 * np.log10(np.abs(np.fft.rfft(x * np.hanning(n))) ** 2 + 1e-12) for x in fr])
    df = f[1] - f[0]
    R = (uniform_filter1d(S, int(400 / df), axis=1) - uniform_filter1d(S, int(2000 / df), axis=1))[:, (f >= 4000) & (f <= 12000)]
    R = R - R.mean(1, keepdims=True)
    c = [(R[i] * R[i + k]).sum() / np.sqrt((R[i] ** 2).sum() * (R[i + k] ** 2).sum() + 1e-12) for i in range(len(R) - k)]
    return float(np.mean(c))


def mean_z(poly, diff=None):
    """The diff map's z averaged inside the polygon. EMPTY SLOT: it arrives with the diff (Migration 4); until then
    this returns None and measure_picture reports mean_z as None."""
    return None


# ---- one polygon on one picture -----------------------------------------------------------------------------------

def _nan_to_none(d):
    return {k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in d.items()}


def _med(x):
    x = np.array([v for v in x if v is not None and np.isfinite(v)], float)
    return float(np.median(x)) if len(x) else np.nan


def measure_picture(y, sr, window, poly):
    """Everything measured inside polygon [(s, Hz)...] (seconds from the window start) of the sound y (mono) at
    window [t0, t1] s of that file. -> dict: level (mean dB in the mask), mid_centroid, mid_median, life (ms), drift
    (kHz per 10 ms), bend (Hz), hold, held20, n_cells, n_seeds, bbox [s0, s1, Hz0, Hz1], mean_z (None for now)."""
    t0, t1 = window
    p = [(s + t0, hz) for s, hz in poly]
    f, t, S = picture(y, sr, t0, t1)
    rows, cols, mask = mask_on_grid(p, f, t)
    level = float(S[rows, cols][mask].mean())
    cx, cy = centroid(p)
    band = mid_band(S)
    pts = seeds(p, f, t, rows, cols, mask)
    Sm = gaussian_filter(S, (1.0, 1.0))
    per = []
    for i, j in pts:
        path, hold = follow(Sm, f, t, i, j)
        per.append(dict(**stats(path), hold=hold, mid=mid_texture(S, f, t, t[j], f[i], band)))
    arr = np.array(p, float)
    h20 = held(y, sr, float(arr[:, 0].min()), float(arr[:, 0].max()), lag_ms=20)
    return _nan_to_none(dict(
        level=level, mid_centroid=mid_texture(S, f, t, cx, cy, band), mid_median=_med([r['mid'] for r in per]),
        life=_med([r['life'] for r in per]), drift=_med([r['drift'] for r in per]),
        bend=_med([r['bend'] for r in per]), hold=_med([r['hold'] for r in per]), held20=h20,
        n_cells=int(mask.sum()), n_seeds=len(pts),
        bbox=[round(float(a), 4) for a in (min(s for s, _ in poly), max(s for s, _ in poly),
                                            min(h for _, h in poly), max(h for _, h in poly))],
        mean_z=mean_z(p)))


# ---- a round's saved areas ---------------------------------------------------------------------------------------

def read_jsonl(path):
    try:
        with open(path, encoding='utf8') as f:
            return [json.loads(l) for l in f if l.strip()]
    except OSError:
        return []


def _load_mono(path, cache):
    if path not in cache:
        import soundfile as sf
        y, sr = sf.read(path, dtype='float64', always_2d=True)
        cache[path] = (y.mean(axis=1), sr)
    return cache[path]


def measure_area(area, sources, cache=None):
    """One saved area on both sounds: {'real': measure_picture(...), 'ours': ...} (a side that cannot be read holds
    {'error': why}). sources: the round's sources.json entry for the area's card ({real|other: {file, window}})."""
    cache = {} if cache is None else cache
    out = {}
    for role, key in (('real', 'real'), ('ours', 'other')):
        try:
            s = sources[key]
            y, sr = _load_mono(s['file'], cache)
            out[role] = measure_picture(y, sr, s['window'], area['poly'])
        except (OSError, KeyError, ValueError, RuntimeError) as e:
            out[role] = {'error': f'{type(e).__name__}: {e}'}
    return out


def measure_round(out, area=None):
    """Measure the saved areas of a round folder `out` (all, or the one with id `area`) and append one line each to
    <out>/areas_measured.jsonl. -> the lines written."""
    areas = read_jsonl(os.path.join(out, 'areas.jsonl'))
    if area:
        areas = [a for a in areas if a.get('id') == area]
        if not areas:
            raise ValueError(f"no area {area!r} in {os.path.join(out, 'areas.jsonl')}")
    if not areas:
        raise ValueError(f"no saved areas in {out} (areas.jsonl): name some on the reveal of the picture round first")
    try:
        with open(os.path.join(out, 'sources.json'), encoding='utf8') as f:
            src = json.load(f)
    except OSError:
        raise ValueError(f"{out} has no sources.json (a round built before region_measure): rebuild it, or write "
                         f"sources.json as {{q: {{real: {{file, window}}, other: {{file, window}}}}}}")
    cache, lines = {}, []
    for a in areas:
        row = {'id': a['id'], 'ts': time.strftime('%Y-%m-%dT%H:%M:%S'), 'round': a.get('round'), 'q': a.get('q'),
               'name': a.get('name'), 'shown': a.get('shown'), 'picture': a.get('picture')}
        s = src.get(str(a.get('q')))
        if s is None:
            row['error'] = f"no source for card {a.get('q')}"
        else:
            row.update(measure_area(a, s, cache))
        lines.append(row)
    with open(os.path.join(out, 'areas_measured.jsonl'), 'a', encoding='utf8') as f:
        for r in lines:
            f.write(json.dumps(r) + '\n')
    return lines


COLS = (('level', 'level dB', 1), ('mid_centroid', 'mid@c dB', 2), ('mid_median', 'mid med dB', 2),
        ('life', 'life ms', 0), ('drift', 'drift kHz/10ms', 2), ('bend', 'bend Hz', 0), ('held20', 'held20', 2))


def _f(v, nd):
    return '-' if v is None else f'{v:.{nd}f}'


def table(rows):
    """Measured rows (several rounds may be mixed) as text grouped by name: per name its instances, each with the
    numbers real / ours, then the median of the instances. A name gathers instances over rounds."""
    by = {}
    for r in rows:
        by.setdefault(r.get('name') or '(no name)', []).append(r)
    L = [f"measured inside the areas, real / ours ({MEAN_Z_NOTE})"]
    for name in sorted(by):
        inst = by[name]
        L.append(f"{name}: {len(inst)} instance{'s' if len(inst) != 1 else ''}")
        L.append('   ' + f"{'area':22}" + ' '.join(f'{lab:>16}' for _, lab, _ in COLS))
        for r in inst:
            if 'error' in r and 'real' not in r:
                L.append(f"   {r['id']:22}{r['error']}")
                continue
            cells = []
            for k, _, nd in COLS:
                cells.append(f"{_f(r['real'].get(k), nd)} / {_f(r['ours'].get(k), nd)}")
            L.append('   ' + f"{r['id'][:21]:22}" + ' '.join(f'{c:>16}' for c in cells))
        good = [r for r in inst if 'real' in r and 'ours' in r]
        if len(good) > 1:
            cells = [f"{_f(_nn(_med([r['real'].get(k) for r in good])), nd)} / "
                     f"{_f(_nn(_med([r['ours'].get(k) for r in good])), nd)}" for k, _, nd in COLS]
            L.append('   ' + f"{'median':22}" + ' '.join(f'{c:>16}' for c in cells))
        for r in inst:
            for role in ('real', 'ours'):
                if isinstance(r.get(role), dict) and 'error' in r[role]:
                    L.append(f"   {r['id']} {role}: {r[role]['error']}")
    return '\n'.join(L)


def _nn(v):
    return None if v is None or not np.isfinite(v) else float(v)


def rounds_in(path):
    """The round folders to measure: `path` itself when it is a round (has manifest.json), else the rounds under it
    that hold saved areas."""
    if os.path.isfile(os.path.join(path, 'manifest.json')):
        return [path]
    return sorted(os.path.join(path, d) for d in os.listdir(path)
                  if os.path.isfile(os.path.join(path, d, 'areas.jsonl')))


def latest_by_id(rows):
    """A re-measured area keeps its newest line."""
    return list({r['id']: r for r in rows}.values())
