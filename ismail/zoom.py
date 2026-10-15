"""The zoom diff: where a sound differs from what it should match, beyond the reference's own take-to-take spread
(ledger:M193; moved from vox, songs/vox/work/spectrogram_diff.py at eb2a970 and ladder.smooth_path at 56dd638, per
vox's handoff section 7, which names exactly what moved). Pure functions of arrays: no prints, files or plots; the
ops live in api_zoom.py and the pictures in analysis.py.

  spectrogram_diff(y, sr, t0, t1, f_lo, f_hi, refs=..., ...)   z map per scale, control maps, ranked spots
  region_measure(y, sr, t0, t1, f_lo, f_hi, ...)               power in a box, dB re a reference band
  smooth_path, frac                                            the warp (DTW, made smooth and monotone)
  parity_fixture(seed)                                         the synthetic check: a 6 dB box in noise + harmonics
  level_match(y, sr)                                           the caller's level match (-25 LUFS, as vox does)

Method of spectrogram_diff, per scale (grain, strokes, plumes):
  - pictures: STFT power of the word window (+- pad_s), 0 to f_max_hz, resampled to n_cols columns, so a column is a
    fixed fraction of the word window; the anchor reference (refs[anchor]) is the take under test;
  - measure='power' (default): blurring, resampling and the band offsets are done on power, then turned into dB.
    measure='db' (mean of dB) is kept for parity with vox's atlas: in mean of dB a synth's noise floor fills the dark
    pixels and reads as "brighter";
  - align: the other refs are warped onto the anchor. warp='smooth' (default) uses smooth_path on the plume-scale
    pictures (a smooth monotone fractional map; never a plain DTW path, which repeats frames: vox heard 17% repeated
    frames as "doubled sound"); warp='atlas' (DTW per scale, mean of the mapped columns) is kept for parity;
  - floor: spread = rms over the other refs of (warped ref minus anchor) per cell, plus spread_floor_db;
  - de-bias: the test-minus-anchor difference loses its own smooth per-band offset (band means interpolated between
    band centres, smoothed over debias_smooth_hz) before z is formed, robustly: two more passes leave the cells already
    past z_thr out of the band means (else a 6 dB box lifts its band's offset and paints a darker ghost over the rest
    of the band). So a deficit across a whole band is absorbed as the band offset: for a band-wide level, run with
    debias=False or read region_measure (vox's case: one word 4.3 dB darker over its whole 6-10 kHz band in 15 of 16
    pairs vanished from the ranking with the de-bias and came back without it);
  - z = (test minus anchor, de-biased) / spread, in units of the reference's own spread per time-frequency cell;
  - control: each other ref in the test's place, its yardstick built from the refs that are neither (needs 4 refs);
    every spot carries the control's rate in its own cell, and counts only above it;
  - spots: connected cells past z_thr (min_cells, the top_spots strongest by peak z times root area), placed at their
    peak cell: quarter of the word window, band, sign (test brighter or darker than the anchor).
Levels: the caller level-matches every clip first (vox sets each clip to -25 LUFS; level_match does that). Default
sizes match vox at 44.1 kHz (1024-point window, 64-sample hop, 128 columns per word); they are in seconds and Hz, so
another rate keeps the same resolution.
Fixture at the move (parity 0-9, vox 10-08, reproduced here cell for cell, tests/test_zoom_diff.py): the box found at
strokes and plumes in 10 of 10 seeds, region_measure +5.9 dB for the 6 dB box, no stray cell outside the box in 9 of
10 seeds (seed 4: one darker strokes cell beside the box, 3-6 kHz, in its quarter)."""
import numpy as np

SR_REF = 44100.0
N_COLS = 128
# (time sigma as a fraction of the word window, frequency sigma in Hz); vox's (1.5, 3) and (5, 12) in
# (columns of 128, rows of a 1024-point STFT at 44.1 kHz)
SCALES = {'grain': (0.0, 0.0), 'strokes': (1.5 / N_COLS, 3 * SR_REF / 1024), 'plumes': (5.0 / N_COLS, 12 * SR_REF / 1024)}
BANDS_HZ = ((0.0, 1000.0), (1000.0, 3000.0), (3000.0, 6000.0), (6000.0, 10000.0), (10000.0, 16000.0))
BAND_CENTRES_HZ = (500.0, 2000.0, 4500.0, 8000.0, 13000.0)

DEFAULTS = dict(pad_s=0.06, win_s=1024 / SR_REF, hop_s=64 / SR_REF, f_max_hz=16000.0, n_cols=N_COLS, eps_power=1e-14,
                scales=SCALES, measure='power', warp='smooth', warp_test=False, warp_sd_frac=4.0 / N_COLS,
                debias=True, debias_robust_iter=2, bands_hz=BANDS_HZ, band_centres_hz=BAND_CENTRES_HZ, debias_smooth_hz=10 * SR_REF / 1024,
                spread_floor_db=0.5, z_thr=2.5, min_cells=12, top_spots=4)

MIN_REFS = 3             # takes of the same words for a spread
MIN_REFS_CONTROL = 4     # for the own-source control (each ref in the test's place, judged by the refs that are neither)
LEVEL_LUFS = -25.0       # vox's level for every clip before a diff


class ZoomError(ValueError):
    """Raised with a message that says what to do next."""


def _p(params):
    p = dict(DEFAULTS)
    p.update({k: v for k, v in params.items() if k in DEFAULTS})
    return p


# ---------------------------------------------------------------- the warp (vox ladder.smooth_path and frac)

def smooth_path(sp_ref, sp_other, sd=4.0):
    """For each frame of ref, a fractional frame of other: DTW on a 40-band log envelope, the mean partner per frame,
    a gaussian over sd frames, kept monotone. A plain DTW path repeats frames (vox: 17% repeats, heard as "doubled
    sound"); this one about 3%. sp_*: [frames, bins] power envelopes. Use with frac()."""
    import librosa
    from scipy.ndimage import gaussian_filter1d

    def feat(sp):
        L = np.log(sp + 1e-12)
        idx = np.unique(np.geomspace(2, sp.shape[1] - 1, 40).astype(int))
        return (L[:, idx] - L[:, idx].mean(1, keepdims=True)).T
    _, wp = librosa.sequence.dtw(X=feat(sp_ref), Y=feat(sp_other), metric='euclidean')
    acc, cnt = np.zeros(len(sp_ref)), np.zeros(len(sp_ref))
    for i, j in wp:
        acc[i] += j
        cnt[i] += 1
    q = np.maximum.accumulate(gaussian_filter1d(acc / cnt, sd, mode='nearest'))
    return np.clip(q, 0, len(sp_other) - 1)


def frac(X, q):
    """Rows of X at fractional frame positions q (linear between frames)."""
    k0 = np.floor(q).astype(int)
    k1 = np.minimum(k0 + 1, len(X) - 1)
    w = (q - k0).reshape(-1, *([1] * (X.ndim - 1)))
    return X[k0] * (1 - w) + X[k1] * w


# ---------------------------------------------------------------- internals

def _picture(y, sr, t0, t1, p):
    """STFT power (or dB) of [t0 - pad, t1 + pad], 0 to f_max_hz, resampled to n_cols columns.
    Returns (picture, f_hz, window start s, window length s)."""
    from scipy.ndimage import zoom
    from scipy.signal import stft
    a, b = t0 - p['pad_s'], t1 + p['pad_s']
    i0 = max(0, int(a * sr))
    seg = y[i0:int(b * sr)]
    n, hop = int(round(p['win_s'] * sr)), int(round(p['hop_s'] * sr))
    if len(seg) < n:
        raise ZoomError(f"the window {t0:.3f}-{t1:.3f} s holds {len(seg) / sr * 1000:.0f} ms of audio, under one "
                        f"{p['win_s'] * 1000:.0f} ms frame; widen seconds or check that it lies inside the file "
                        f"({len(y) / sr:.2f} s)")
    f, _, Z = stft(seg, sr, nperseg=n, noverlap=n - hop, boundary=None)
    P = np.abs(Z) ** 2 + p['eps_power']
    keep = f <= p['f_max_hz']
    P, f = P[keep], f[keep]
    X = 10 * np.log10(P) if p['measure'] == 'db' else P
    X = zoom(X, (1, p['n_cols'] / X.shape[1]), order=1)
    return X, f, i0 / sr, len(seg) / sr


def _sig(scale, p, bin_hz):
    ft, fhz = p['scales'][scale]
    return fhz / bin_hz, ft * p['n_cols']          # (rows, columns)


def _level(X, sig, p):
    """The picture at one scale, in dB: blur of dB (measure='db') or dB of the blurred power."""
    from scipy.ndimage import gaussian_filter
    B = X if sig == (0.0, 0.0) else gaussian_filter(X, sig)
    return B if p['measure'] == 'db' else 10 * np.log10(np.maximum(B, p['eps_power']))


def _warp_atlas(A, ref, plume_sig):
    """DTW on both pictures blurred to the plume scale, mean of the A columns mapped to each ref column."""
    import librosa
    from scipy.ndimage import gaussian_filter
    a, r = gaussian_filter(A, plume_sig), gaussian_filter(ref, plume_sig)
    _, wp = librosa.sequence.dtw(X=r, Y=a, metric='euclidean')
    out, cnt = np.zeros_like(ref), np.zeros(ref.shape[1])
    for i, j in wp:
        out[:, i] += A[:, j]
        cnt[i] += 1
    return out / np.maximum(cnt, 1)


def _smooth_map(X_other, X_ref, plume_sig, p):
    """smooth_path on the plume-scale power pictures: fractional column of other for each column of ref."""
    from scipy.ndimage import gaussian_filter
    pw = (lambda X: 10 ** (X / 10)) if p['measure'] == 'db' else (lambda X: X)
    o, r = gaussian_filter(pw(X_other), plume_sig), gaussian_filter(pw(X_ref), plume_sig)
    return smooth_path(r.T, o.T, sd=p['warp_sd_frac'] * p['n_cols'])


def _apply_map(X, q):
    return frac(X.T, q).T


def _offset(d, L_test, L_ref, f, p, bin_hz, keep=None):
    """Smooth per-band offset of d = L_test - L_ref (dB): band means (of dB, or a power ratio), interpolated between the
    band centres and smoothed over debias_smooth_hz, so no band edge is drawn. keep: cells to use (the robust pass
    leaves out the cells already past z_thr, so a spot does not lift its own band and paint a darker ghost there)."""
    from scipy.ndimage import gaussian_filter1d
    m = []
    for lo, hi in p['bands_hz']:
        k = ((f >= lo) & (f < hi))[:, None] & np.ones(d.shape[1], bool)
        if keep is not None and (k & keep).sum() >= 0.25 * k.sum():
            k = k & keep
        if p['measure'] == 'db':
            m.append(d[k].mean())
        else:
            m.append(10 * np.log10(np.mean(10 ** (L_test[k] / 10))) - 10 * np.log10(np.mean(10 ** (L_ref[k] / 10))))
    return gaussian_filter1d(np.interp(f, p['band_centres_hz'], m), p['debias_smooth_hz'] / bin_hz)[:, None]


def _band_name(fhz, bands):
    for lo, hi in bands:
        if lo <= fhz < hi:
            return lo, hi
    return bands[-1]


def _spots(z, f, rows0, win, p, word, scale):
    """Connected cells past z_thr, placed at the peak cell and addressed in seconds, Hz and quarters."""
    from scipy.ndimage import label
    n_cols = z.shape[1]
    a0, dur = win
    col_s, bin_hz = dur / n_cols, (f[1] - f[0]) if len(f) > 1 else 1.0
    out = []
    for sgn in (1, -1):
        lab, n = label(sgn * z > p['z_thr'])
        for k in range(1, n + 1):
            r, c = np.nonzero(lab == k)
            if len(r) < p['min_cells']:
                continue
            v = sgn * z[r, c]
            i = int(np.argmax(v))
            pk = float(v[i])
            fr_pk = c[i] / n_cols
            f_pk = float(f[rows0 + r[i]])
            blo, bhi = _band_name(f_pk, p['bands_hz'])
            out.append((pk * len(r) ** 0.5, dict(
                word=word, scale=scale, sign='brighter' if sgn > 0 else 'darker',
                frac0=c.min() / n_cols, frac1=c.max() / n_cols, frac_peak=fr_pk, quarter=min(int(fr_pk * 4), 3) + 1,
                t0_s=a0 + c.min() * col_s, t1_s=a0 + (c.max() + 1) * col_s, t_peak_s=a0 + (c[i] + 0.5) * col_s,
                f_lo_hz=float(f[rows0 + r.min()]), f_hi_hz=float(f[rows0 + r.max()]), f_peak_hz=f_pk,
                band_lo_hz=blo, band_hi_hz=bhi, peak_z_spread=pk, area_cells=int(len(r)),
                area_s_hz=float(len(r) * col_s * bin_hz))))
    return [s for _, s in sorted(out, key=lambda x: -x[0])[:p['top_spots']]]


def _cell(s):
    return (s['sign'], s['quarter'], s['band_lo_hz'], s['band_hi_hz'])


# ---------------------------------------------------------------- the family

def parity_fixture(seed=0, n_refs=6, timing_jitter_s=0.003):
    """Synthetic fixture: n_refs takes of one 'word' (harmonics on a gliding pitch, plus breath noise, a raised-cosine
    envelope; every take its own noise, +-2% pitch, +-timing_jitter_s timing, +-0.5 dB level) and a test take made the
    same way, timed and pitched from the first take (as a synth is timed from a real take), with a 6 dB boost in the
    box 0.35-0.50 s, 2-4 kHz; plus the same test realisation without the boost (the "after" clip).
    Returns (inputs, expected, tolerance)."""
    from scipy.signal import istft, stft
    sr = int(SR_REF)
    rng = np.random.default_rng(seed)

    def take(boost=False, timing=None):
        rs = rng
        dur = 0.8
        t = np.arange(int(dur * sr)) / sr
        sh, fj = timing
        f0 = 140 * (1 + fj) * (1 + 0.08 * np.sin(2 * np.pi * 1.2 * (t - sh)))
        ph = 2 * np.pi * np.cumsum(f0) / sr
        h = sum(np.sin(k * ph) / k for k in range(1, 57))
        env = np.clip((t - 0.1 - sh) / 0.6, 0, 1)
        env = np.sin(np.pi * env) ** 0.5
        y = env * (0.1 * h + 0.03 * rs.standard_normal(len(t))) + 0.002 * rs.standard_normal(len(t))
        y *= 10 ** (rs.uniform(-0.5, 0.5) / 20)
        if boost:
            ff, tt, Z = stft(y, sr, nperseg=512)
            Z[np.ix_((ff >= 2000) & (ff < 4000), (tt >= 0.35) & (tt < 0.50))] *= 2.0
            y = istft(Z, sr, nperseg=512)[1][:len(t)]
        return y
    tim = [(rng.uniform(-timing_jitter_s, timing_jitter_s), rng.uniform(-0.02, 0.02)) for _ in range(n_refs)]
    refs = [take(timing=x) for x in tim]
    state = rng.bit_generator.state
    test = take(boost=True, timing=tim[0])    # timed from the anchor take, as a synth is timed from a real take
    rng.bit_generator.state = state
    clean = take(boost=False, timing=tim[0])  # the same realisation as the test, without the box: the "fixed" clip
    inputs = dict(y=test, sr=sr, t0=0.1, t1=0.7, refs=refs, after=clean)
    expected = dict(box=dict(t0_s=0.35, t1_s=0.50, f_lo_hz=2000.0, f_hi_hz=4000.0, sign='brighter', boost_db=6.0),
                    scales_must_find=('strokes', 'plumes'))
    tolerance = dict(box_margin_s=0.03, box_margin_hz=300.0, boost_db=1.5, stray_margin=0.5)
    return inputs, expected, tolerance


def spectrogram_diff(y, sr, t0=None, t1=None, f_lo=None, f_hi=None, refs=(), ref_spans=None, ref_ids=None, anchor=0,
                     word=None, **params):
    """Where does y differ from the reference takes of the same words, beyond their own spread?

    y: the clip under test; refs: >= 3 takes of the same words (same sr, level-matched first, e.g. level_match to
    -25 LUFS; 4 or more for the own-source control); t0, t1: the word in y (s; None = whole clip); ref_spans:
    (t0, t1) of the word in each ref (default: y's, for a clip timed from the take); anchor: which ref the test is
    compared with (the others are its yardstick); f_lo, f_hi: limit the spots to this band (Hz; the pictures, offsets
    and spread always use 0 to f_max_hz).
    Params (DEFAULTS): pad_s, win_s, hop_s, f_max_hz, n_cols, eps_power, scales {name: (sigma frac of window, sigma
    Hz)}, measure 'power'|'db', warp 'smooth'|'atlas', warp_test, warp_sd_frac, debias (False keeps a band-wide
    level, which the de-bias absorbs; or read region_measure), debias_robust_iter (0 = plain band means; n = n passes
    that leave the cells past z_thr out of the band offsets), bands_hz, band_centres_hz, debias_smooth_hz,
    spread_floor_db, z_thr, min_cells, top_spots.
    Returns: f_hz, frac, t_s (test clip times of the columns), n_refs, n_controls, and per scale
    {z_spread (map), spread_db (map), control_z_spread {ref id: map}, spots [...], control_spots {ref id: [...]},
    mean_abs_z_spread, control_mean_abs_z_spread}. Each spot: word, scale, sign, frac0/frac1/frac_peak, quarter,
    t0_s/t1_s/t_peak_s, f_lo_hz/f_hi_hz/f_peak_hz, band_lo_hz/band_hi_hz, peak_z_spread, area_cells, area_s_hz,
    control_cell_rate (share of controls with a spot of the same sign in the same quarter and band),
    control_cell_peak_z_max, beyond_control (peak above every control spot in that cell)."""
    p = _p(params)
    refs = list(refs)
    if len(refs) < MIN_REFS:
        raise ZoomError(f"the zoom diff needs at least {MIN_REFS} takes of the same words for their spread "
                        f"({MIN_REFS_CONTROL} or more for the own-source control); got {len(refs)}. Record or cut more "
                        f"takes of the same words, then call again; there is no absolute threshold to fall back on")
    if not 0 <= anchor < len(refs):
        raise ZoomError(f"anchor {anchor} is not one of the {len(refs)} refs; pass 0 to {len(refs) - 1}")
    if t0 is None:
        t0, t1 = 0.0, len(y) / sr
    ref_spans = list(ref_spans) if ref_spans is not None else [(t0, t1)] * len(refs)
    if len(ref_spans) != len(refs):
        raise ZoomError(f"ref_spans has {len(ref_spans)} spans for {len(refs)} refs; give one (t0, t1) per ref or none")
    ids = list(ref_ids) if ref_ids is not None else [f'ref{i}' for i in range(len(refs))]
    order = [anchor] + [i for i in range(len(refs)) if i != anchor]
    Xs, f, wa, wd = _picture(y, sr, t0, t1, p)
    X = {}
    for i in order:
        X[i], _, a_, d_ = _picture(refs[i], sr, *ref_spans[i], p)
        if i == anchor:
            win_anchor = (a_, d_)
    bin_hz = f[1] - f[0]
    others = order[1:]
    plume = _sig('plumes', p, bin_hz)
    rows = np.ones(len(f), bool)
    if f_lo is not None:
        rows &= f >= f_lo
    if f_hi is not None:
        rows &= f < f_hi
    if not rows.any():
        raise ZoomError(f"the band {f_lo}-{f_hi} Hz holds no row of the picture (0 to {f[-1]:.0f} Hz); widen it")
    r0 = int(np.argmax(rows))
    qmap = {}
    if p['warp'] == 'smooth':
        for k in others:
            qmap[k] = _smooth_map(X[k], X[anchor], plume, p)
        if p['warp_test']:
            qmap['test'] = _smooth_map(Xs, X[anchor], plume, p)
    tw = win_anchor if p['warp_test'] else (wa, wd)          # the test map lies on the anchor's timeline when warped
    out = dict(f_hz=f, frac=np.arange(p['n_cols']) / p['n_cols'], t_s=tw[0] + (np.arange(p['n_cols']) + 0.5) * tw[1] / p['n_cols'],
               anchor_id=ids[anchor], ref_ids=[ids[i] for i in order], word=word, n_refs=len(refs),
               n_controls=len(others) if len(others) >= 3 else 0, window_s=(tw[0], tw[0] + tw[1]), anchor_window_s=(win_anchor[0], sum(win_anchor)),
               measure=p['measure'], warp=p['warp'], debias=p['debias'], scales={})

    def lv(Xi, s, q=None):
        if p['warp'] == 'smooth' and q is not None:
            Xi = _apply_map(Xi, q)
        return _level(Xi, s, p)
    for scale in p['scales']:
        s = _sig(scale, p, bin_hz)
        ref = lv(X[anchor], s)
        if p['warp'] == 'atlas':
            Lk = {k: _warp_atlas(lv(X[k], s), ref, plume) for k in others}
            Lt = _warp_atlas(lv(Xs, s), ref, plume) if p['warp_test'] else lv(Xs, s)
        else:
            Lk = {k: lv(X[k], s, qmap[k]) for k in others}
            Lt = lv(Xs, s, qmap.get('test'))
        W = {k: Lk[k] - ref for k in others}

        def spread(skip=None):
            return np.sqrt(np.mean([W[k] ** 2 for k in others if k != skip], 0)) + p['spread_floor_db']

        def zof(L, d, sd):
            if not p['debias']:
                return d / sd
            z = (d - _offset(d, L, ref, f, p, bin_hz)) / sd
            for _ in range(p['debias_robust_iter']):
                z = (d - _offset(d, L, ref, f, p, bin_hz, keep=np.abs(z) <= p['z_thr'])) / sd
            return z
        sd = spread()
        z = zof(Lt, Lt - ref, sd)
        ctl = {ids[k]: zof(Lk[k], W[k], spread(k)) for k in others} if len(others) >= 3 else {}
        sp = _spots(z[rows], f, r0, tw, p, word, scale)
        csp = {kid: _spots(zc[rows], f, r0, win_anchor, p, word, scale) for kid, zc in ctl.items()}
        for x in sp:
            hits = [max([c['peak_z_spread'] for c in cs if _cell(c) == _cell(x)], default=None) for cs in csp.values()]
            hits = [h for h in hits if h is not None]
            x['control_cell_rate'] = len(hits) / len(csp) if csp else None
            x['control_cell_peak_z_max'] = max(hits, default=0.0)
            x['beyond_control'] = bool(x['peak_z_spread'] > x['control_cell_peak_z_max'])
        out['scales'][scale] = dict(z_spread=z, spread_db=sd, control_z_spread=ctl, spots=sp, control_spots=csp,
                                    mean_abs_z_spread=float(np.mean(np.abs(z[rows]))),
                                    control_mean_abs_z_spread=float(np.mean([np.mean(np.abs(c[rows])) for c in ctl.values()])) if ctl else None)
    return out


def region_measure(y, sr, t0=None, t1=None, f_lo=None, f_hi=None, ref_f_lo=0.0, ref_f_hi=None, win_s=1024 / SR_REF,
                   hop_s=64 / SR_REF, eps_power=1e-14):
    """Power in a zoom box (t0-t1 s, f_lo-f_hi Hz), in dB re a reference band over the same time (default: the whole
    spectrum, 0 to sr/2, so level_db_re_ref is the box's share of the power at that moment). Measured on power (the
    mean of the STFT power per cell, then dB), never as a mean of dB. box_power_db and ref_band_power_db: 10 log10
    of the mean power per cell (scipy stft scaling, a full-scale sine about -9 dB)."""
    from scipy.signal import stft
    t0 = 0.0 if t0 is None else t0
    t1 = len(y) / sr if t1 is None else t1
    f_lo = 0.0 if f_lo is None else f_lo
    f_hi = sr / 2 if f_hi is None else f_hi
    ref_f_hi = sr / 2 if ref_f_hi is None else ref_f_hi
    n, hop = int(round(win_s * sr)), int(round(hop_s * sr))
    seg = y[max(0, int(t0 * sr)):int(t1 * sr)]
    if len(seg) < n:
        seg = np.pad(seg, (0, n - len(seg)))
    f, _, Z = stft(seg, sr, nperseg=n, noverlap=n - hop, boundary=None)
    P = np.abs(Z) ** 2
    inb, inr = (f >= f_lo) & (f <= f_hi), (f >= ref_f_lo) & (f <= ref_f_hi)
    if not inb.any() or not inr.any():
        raise ZoomError(f"the box {f_lo:.0f}-{f_hi:.0f} Hz or the reference band {ref_f_lo:.0f}-{ref_f_hi:.0f} Hz "
                        f"holds no STFT bin (0 to {sr / 2:.0f} Hz, {f[1] - f[0]:.0f} Hz apart); widen it")
    box = 10 * np.log10(np.mean(P[inb]) + eps_power)
    ref = 10 * np.log10(np.mean(P[inr]) + eps_power)
    return dict(box_power_db=float(box), ref_band_power_db=float(ref), level_db_re_ref=float(box - ref))


def loudness_db(y, sr):
    """Integrated loudness (BS.1770, LUFS) of a mono clip; under 0.4 s, or with nothing pyloudnorm can gate, the RMS
    level in dB minus 0.691 (the same scale for a steady sound). Returns (level, 'LUFS' | 'dB RMS')."""
    try:
        import pyloudnorm as pyln
        if len(y) >= int(0.45 * sr):
            v = float(pyln.Meter(sr).integrated_loudness(y))
            if np.isfinite(v):
                return v, 'LUFS'
    except Exception:
        pass
    return 20 * np.log10(float(np.sqrt(np.mean(y ** 2))) + 1e-12) - 0.691, 'dB RMS'


def level_match(y, sr, lufs=LEVEL_LUFS):
    """y scaled to a loudness (default -25 LUFS, as vox sets every clip before a diff). No peak limit: this is for
    measuring, never for playback."""
    lv, _ = loudness_db(y, sr)
    return y * 10 ** ((lufs - lv) / 20)
