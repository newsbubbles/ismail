"""The zoom ops (ledger:M193, moved from vox's handoff section 7): sound_zoom (one box of a sound at two
resolutions), sound_zoom_diff (where a take differs from the reference takes beyond their own spread, against an
own-source control) and region_measure (power in a box). Registered into api.OPS on import; the method is in zoom.py,
the pictures in analysis.py."""
import os

import soundfile as sf

from . import analysis as A
from . import zoom as Z
from .api import op, OpError, _load

HOT_MAX = 12             # hot spots listed at most, whatever top asks


def _window(P, source, bars=None, seconds=None):
    """(path, t0, t1) like spectrogram: seconds inside bars, or in the file without bars (None = the whole file)."""
    path, g = P.source(source, bars)
    a, b = (g.bar_time(bars[0]), g.bar_time(bars[1] + 1)) if bars else (0.0, None)
    if seconds:
        if len(seconds) != 2 or float(seconds[1]) <= float(seconds[0]):
            raise OpError(f"seconds={seconds!r}: give [t0, t1] with t1 after t0, in s")
        a, b = a + float(seconds[0]), a + float(seconds[1])
    return path, a, b


def _mono(path, sr=None):
    y, r = sf.read(path, dtype='float64')
    y = y.mean(axis=1) if y.ndim > 1 else y
    if sr and r != sr:
        from math import gcd
        from scipy.signal import resample_poly
        k = gcd(int(sr), int(r))
        y, r = resample_poly(y, int(sr) // k, int(r) // k), sr
    return y, r


def _out(P, out, stem):
    outp = out or os.path.join('renders', stem + '.png')
    if not os.path.isabs(outp):
        outp = os.path.join(P.root, outp)  # relative paths are relative to the project
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    return outp


def _name(src):
    return os.path.splitext(os.path.basename(src))[0] if ('/' in src or '\\' in src) else src.replace(':', '_')


@op()
def sound_zoom(project: str, source: str = None, bars: list = None, seconds: list = None, f_lo: float = 0.0,
               f_hi: float = 16000.0, words: list = None, ruler: bool = True, out: str = None) -> str:
    """One time x frequency box of a sound at two resolutions, stacked in one PNG: time-sharp on top (256-point
    window, 32-sample hop at 44.1 kHz: pulses, onsets, stripes, a boundary that sits 50 ms early) and
    frequency-sharp below (2048-point: harmonics, formant ridges). Reach for it when you need to zoom into one word,
    hit or note and see both its timing and its harmonics, or when spectrogram's single view blurs the edge you are
    looking for. Same arguments as spectrogram's eye mode: seconds=[t0, t1] inside bars (or the file), the band
    f_lo..f_hi Hz, words=[{'w', 't0', 't1'}] in s, a ruler in ms. Both panels sit on spectrogram's fixed plot box,
    so a box has the same pixels in each (one panel lower) and a .json beside the PNG maps a pixel to (s, Hz) in
    either panel. Draw the reference and yours on the same window and read them side by side; sound_zoom_diff
    finds where they differ beyond the reference's own spread."""
    P = _load(project)
    path, a, b = _window(P, source, bars, seconds)
    outp = _out(P, out, f"zoom_{_name(source or 'src')}_{a:.2f}")
    try:
        A.zoom_png(path, outp, a, b, f_lo, f_hi, words or [], None, ruler)
    except ValueError as e:
        raise OpError(f"sound_zoom: {e}")
    return (f"{outp}\ntop: time-sharp ({A.ZOOM_VIEWS[0][1]}-point window, {A.ZOOM_VIEWS[0][2]}-sample hop at "
            f"44.1 kHz); bottom: frequency-sharp ({A.ZOOM_VIEWS[1][1]}-point); {a:.3f}-"
            f"{'end' if b is None else f'{b:.3f}'} s, {f_lo:.0f}-{f_hi:.0f} Hz; pixel map in {outp}.json")


def _band(s):
    return f"{s['band_lo_hz'] / 1000:.0f}-{s['band_hi_hz'] / 1000:.0f} kHz"


def _spot_line(i, s, word):
    where = f"quarter {s['quarter']} of '{word}'" if word else f"quarter {s['quarter']} of the window"
    return (f"{i}. {s['scale']}, {_band(s)}, {where}, {s['sign']}, z {s['peak_z_spread']:.1f}, control "
            f"{s['control_cell_rate']:.2f}{' (beyond every control spot there)' if s['beyond_control'] else ''}; "
            f"at {s['t_peak_s']:.3f} s {s['f_peak_hz'] / 1000:.1f} kHz (box {s['t0_s']:.3f}-{s['t1_s']:.3f} s, "
            f"{s['f_lo_hz'] / 1000:.1f}-{s['f_hi_hz'] / 1000:.1f} kHz)")


def hot_spots(D, top=6):
    """(counted, not counted): the spots of every scale, a spot counted only when the control rate in its own cell
    is under its own (1, one run), ranked by that margin then peak z; both capped (top, HOT_MAX)."""
    spots = [s for S in D['scales'].values() for s in S['spots']]
    key = lambda s: (-(1.0 - s['control_cell_rate']), -s['peak_z_spread'])     # noqa: E731
    counted = sorted([s for s in spots if s['control_cell_rate'] is not None and s['control_cell_rate'] < 1.0], key=key)
    ids = {id(s) for s in counted}
    rest = sorted([s for s in spots if id(s) not in ids], key=lambda s: -s['peak_z_spread'])
    n = max(1, min(int(top), HOT_MAX))
    return counted[:n], rest[:n]


@op()
def sound_zoom_diff(project: str, test: str, refs: list, seconds: list = None, ref_seconds: list = None,
                    f_lo: float = None, f_hi: float = None, word: str = None, anchor: int = 0, debias: bool = True,
                    level_lufs: float = -25.0, top: int = 6, params: dict = None, out: str = None) -> str:
    """Where a sound differs from what it should match, beyond the reference's own take-to-take spread: hot-spot
    text first (scale, band, quarter of the word, brighter or darker, z, the control rate in the same cell), then a
    PNG (at most 1600 px) of the test above the anchor take with the spots outlined. Reach for it when you need to
    find where my render, synth or copy differs from the real takes, which part of a word is wrong, or whether a
    fix closed a spot.
    test: the source under test; refs: >= 4 sources, takes of the same words (3 give the spread, the 4th makes the
    own-source control: each take in the test's place, judged by the takes that are neither); with fewer it refuses,
    as there is no absolute threshold to fall back on. seconds: [t0, t1] of the word in test (s in the file; None =
    the whole file); ref_seconds: one [t0, t1] per ref (default: test's, for a clip timed from a take). anchor: the
    ref the test is compared with. f_lo/f_hi: list spots only in this band (Hz). level_lufs: every clip is set to
    this loudness first (whole file, as vox does at -25 LUFS); None when you level-matched them yourself. Cut long
    files to the line first: the level match and the spread assume one line of words.
    Measured on power (blur and band means of power, then dB), never on mean of dB. z is in units of the reference's
    own spread per time-frequency cell, at three scales: grain (cells), strokes, plumes. A spot counts only where the
    control lights its cell less often than the test does. debias=True removes a smooth per-band offset, so a
    deficit across a whole band is absorbed and does not show as a spot: for a band-wide level run debias=False or
    read region_measure on the band. params: zoom.DEFAULTS overrides (z_thr, min_cells, top_spots, ...); the
    defaults are the tested method."""
    P = _load(project)
    refs = list(refs or [])
    if len(refs) < Z.MIN_REFS_CONTROL:
        raise OpError(f"sound_zoom_diff needs at least {Z.MIN_REFS_CONTROL} takes of the same words in refs "
                      f"({Z.MIN_REFS} for their spread, one more for the own-source control); got {len(refs)}. "
                      f"Record or cut more takes of the same line (sound_import cuts one from a file), then call "
                      f"again with every take in refs")
    if ref_seconds is not None and len(ref_seconds) != len(refs):
        raise OpError(f"ref_seconds has {len(ref_seconds)} windows for {len(refs)} refs; give one [t0, t1] per ref, "
                      f"or none to use test's seconds for every ref")
    bad = sorted(set(params or {}) - set(Z.DEFAULTS))
    if bad:
        raise OpError(f"unknown params {bad}; params are {', '.join(sorted(Z.DEFAULTS))} (zoom.DEFAULTS)")
    tpath = P.resolve_audio(test)
    y, sr = _mono(tpath)
    ys = [_mono(P.resolve_audio(r), sr)[0] for r in refs]
    lv = ''
    if level_lufs is not None:
        y, ys = Z.level_match(y, sr, level_lufs), [Z.level_match(r, sr, level_lufs) for r in ys]
        lv = f"every clip set to {level_lufs:.0f} LUFS (whole file)"
    else:
        lv = 'levels as given (you level-matched them)'
    t0, t1 = (float(seconds[0]), float(seconds[1])) if seconds else (None, None)
    spans = [tuple(map(float, s)) for s in ref_seconds] if ref_seconds is not None else None
    kw = dict(params or {}, debias=bool(debias))
    try:
        D = Z.spectrogram_diff(y, sr, t0, t1, f_lo, f_hi, refs=ys, ref_spans=spans, ref_ids=list(refs),
                               anchor=int(anchor), word=word, **kw)
    except Z.ZoomError as e:
        raise OpError(f"sound_zoom_diff: {e}")
    counted, rest = hot_spots(D, top)
    L = [f"zoom diff of {test} against {len(refs)} takes (anchor {D['anchor_id']}, {D['n_controls']} controls); {lv}; "
         f"measured on power{', de-biased per band' if D['debias'] else ', no de-bias'}"]
    L.append('HOT SPOTS (counted: the control lights the same cell less often than the test):' if counted else
             'HOT SPOTS: none counted (no spot past the spread that the control does not light as often)')
    L += ['  ' + _spot_line(i, s, word) for i, s in enumerate(counted, 1)]
    if rest:
        L.append("not counted (the controls light the same cell as often): " +
                 '; '.join(f"{s['scale']} {_band(s)} q{s['quarter']} {s['sign']} z {s['peak_z_spread']:.1f}" for s in rest))
    for sc, S in D['scales'].items():
        L.append(f"  {sc}: mean |z| {S['mean_abs_z_spread']:.2f} (controls {S['control_mean_abs_z_spread']:.2f}), "
                 f"spots {len(S['spots'])}")
    if D['debias']:
        L.append("a level across a whole band does not show here (the de-bias absorbs it): debias=False, or "
                 "region_measure on the band, of test and of a take")
    # the picture: the test's word window above the anchor's, spots outlined (numbered as above) on both
    (wa, wb), (aa, ab) = D['window_s'], D['anchor_window_s']
    marks_t, marks_a = [], []
    for i, s in enumerate(counted, 1):
        m = dict(f_lo=s['f_lo_hz'], f_hi=s['f_hi_hz'], sign=s['sign'], label=str(i))
        marks_t.append(dict(m, t0=s['t0_s'], t1=s['t1_s']))
        f1 = s['frac1'] + 1.0 / len(D['frac'])
        marks_a.append(dict(m, t0=aa + s['frac0'] * (ab - aa), t1=aa + f1 * (ab - aa)))
    lo, hi = (f_lo or 0.0), (f_hi or Z.DEFAULTS['f_max_hz'])
    sp0 = (t0, t1) if t0 is not None else (0.0, len(y) / sr)
    asp = spans[anchor] if spans else sp0
    wt = [{'w': word, 't0': sp0[0], 't1': sp0[1]}] if word else []
    wr = [{'w': word, 't0': asp[0], 't1': asp[1]}] if word else []
    outp = _out(P, out, f"zoomdiff_{_name(test)}_{sp0[0]:.2f}")
    try:
        A._eye_stack(outp, [dict(y=y, sr=sr, t0=wa, t1=wb, words=wt, target=wt[0] if wt else None, name=test,
                                 label=f'test: {test}', marks=marks_t),
                            dict(y=ys[anchor], sr=sr, t0=aa, t1=ab, words=wr, target=wr[0] if wr else None,
                                 name=refs[anchor], label=f'anchor: {refs[anchor]}', marks=marks_a)],
                     lo, hi, True, A.EYE_BOX)
    except ValueError as e:
        L.append(f"(no picture: {e})")
        return '\n'.join(L)
    L.append(f"picture: {outp} (top: test, bottom: anchor; the counted spots outlined and numbered, blue brighter, "
             f"green darker)")
    return '\n'.join(L)


@op()
def region_measure(project: str, source: str = None, bars: list = None, seconds: list = None, f_lo: float = None,
                   f_hi: float = None, ref_f_lo: float = 0.0, ref_f_hi: float = None, level_lufs: float = None) -> str:
    """Power in a zoom box (seconds [t0, t1], f_lo..f_hi Hz) in dB, and against a reference band over the same time
    (default the whole spectrum, so 'share' is the box's part of the power at that moment). Reach for it when you
    need the level of one region of a sound: one word's hiss, one band in one moment, a band-wide level that
    sound_zoom_diff's de-bias absorbs, or whether a fix moved a box by the dB you meant. Measured on power (mean
    power per STFT cell, 1024-point window, 64-sample hop at 44.1 kHz), not on mean of dB. To compare two clips,
    measure each with the same box and level_lufs=-25 (both set to one loudness first, as sound_zoom_diff does)."""
    P = _load(project)
    path, a, b = _window(P, source, bars, seconds)
    y, sr = _mono(path)
    if level_lufs is not None:
        y = Z.level_match(y, sr, level_lufs)
    try:
        r = Z.region_measure(y, sr, a, b, f_lo, f_hi, ref_f_lo, ref_f_hi)
    except Z.ZoomError as e:
        raise OpError(f"region_measure: {e}")
    t1 = len(y) / sr if b is None else b
    return (f"box {a:.3f}-{t1:.3f} s, {(f_lo or 0) / 1000:.2f}-{(f_hi or sr / 2) / 1000:.2f} kHz: "
            f"{r['box_power_db']:.2f} dB; reference band {ref_f_lo / 1000:.2f}-{(ref_f_hi or sr / 2) / 1000:.2f} kHz: "
            f"{r['ref_band_power_db']:.2f} dB; share {r['level_db_re_ref']:+.2f} dB"
            + (f" (levels at {level_lufs:.0f} LUFS)" if level_lufs is not None else ''))
