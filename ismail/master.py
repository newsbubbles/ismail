"""Mastering a finished cue for delivery (ledger:M207): trim, fade, resample, one fixed gain to a loudness target, and
a true-peak limiter that takes only the excess over the ceiling, then a lookahead true-peak catch as the last stage
(ledger:M280) so the file ends at or under its ceiling.

Generalised from songs/d3_next_teaser/work/master.py (10-14). A fixed gain keeps the cue's dynamics as mixed: ffmpeg
loudnorm falls back to its dynamic mode on some files and quietly rides the level, so this never does. The limiter
runs only when the gain would put the true peak over the ceiling, and its gain reduction is reported.
"""
from math import gcd

import numpy as np
from scipy.signal import resample_poly

OS = 4                    # true-peak oversampling (BS.1770 asks for at least 4x at 48 kHz)
MARGIN_DB = 0.15          # the limiter aims this far under the ceiling, so the re-gain cannot push it over
ROUNDS = 3                # limiter rounds before ledger:M280 (tp_catch=False)
TP_ROUNDS = 6             # with the catch: each round after the first aims lower by what the last re-gain put over,
AIM_DEPTH_DB = 1.5        # at most this far under the ceiling (a target too loud for the mix is the catch's to take)
MP3_MARGIN_DB = 1.2       # a 320k mp3 (ffmpeg lame) of a limited master peaked 0.1-1.25 dB over its wav (M280)


def true_peak_db(y):
    """True peak (dBTP) of a (n, ch) array: the 4x oversampled maximum."""
    if not len(y):
        return -np.inf
    return float(20 * np.log10(np.max(np.abs(resample_poly(y, OS, 1, axis=0))) + 1e-12))


def limit(y, sr, ceil_db, release_ms=60.0, look_ms=1.5):
    """Feed-forward peak limiter on the 4x true-peak envelope: lookahead, instant attack, smooth release. One gain for
    all channels, so the image holds. Returns (limited, the deepest gain reduction in dB, a negative number)."""
    c = 10 ** (ceil_db / 20)
    env = np.max(np.abs(resample_poly(y, OS, 1, axis=0)), axis=1)
    env = np.pad(env, (0, (-len(env)) % OS)).reshape(-1, OS).max(axis=1)[:len(y)]
    need = np.minimum(1.0, c / np.maximum(env, 1e-12))
    la = int(look_ms * sr / 1000)
    if la:
        need = np.lib.stride_tricks.sliding_window_view(np.concatenate([need, np.ones(la)]), la + 1).min(axis=1)
    a = np.exp(-1.0 / (release_ms * sr / 1000))
    g = np.empty_like(need)
    cur = 1.0
    for i, v in enumerate(need):
        cur = v if v < cur else a * cur + (1 - a) * v
        g[i] = cur
    return y * g[:, None], float(20 * np.log10(g.min()))


def catch(y, sr, ceil_db, look_ms=1.5, release_ms=25.0):
    """Lookahead true-peak catch (ledger:M280), ported from songs/planet_rock_axel_f_remix/work/master.py: the 4x
    oversampled peak of each sample, the gain it needs min-filtered over +-look_ms (so every sample near a peak is
    already down), an instant attack and an exponential release, then a look_ms moving average to smooth the gain
    without letting a peak back over. Aims 0.995 (0.04 dB) under the ceiling. One gain for all channels. Returns
    (caught, the deepest gain reduction in dB, a negative number or 0)."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    n = len(y)
    pk = np.max(np.abs(resample_poly(y, OS, 1, axis=0)), axis=1)[:n * OS]
    pk = np.pad(pk, (0, n * OS - len(pk))).reshape(n, OS).max(axis=1)
    need = np.minimum(1.0, 10 ** (ceil_db / 20) * 0.995 / np.maximum(pk, 1e-12))
    if need.min() >= 1.0:
        return y, 0.0
    w = max(3, int(sr * look_ms / 1000)) | 1
    g = minimum_filter1d(need, size=2 * w + 1)
    a = np.exp(-1.0 / (sr * release_ms / 1000))
    out = np.empty_like(g)
    cur = 1.0
    for i, v in enumerate(g):
        cur = v if v < cur else a * cur + (1 - a) * v
        out[i] = cur
    out = uniform_filter1d(np.minimum(out, g), size=w)
    return y * out[:, None], float(20 * np.log10(out.min()))


def master(y, sr, start_s=0.0, end_s=None, lufs=-16.0, tp=-1.0, out_sr=48000, fade_in_s=0.0, fade_out_s=0.4,
           mp3_margin_db=MP3_MARGIN_DB, tp_catch=True):
    """The cue mastered: y is (n, ch) float. Returns (y, out_sr, report dict). Raises ValueError with what to change.
    The limiter rounds and the catch aim at tp - mp3_margin_db (the wav's ceiling, so its mp3 lands under tp).
    tp_catch=False keeps the limiter rounds from before ledger:M280 (3 plain rounds, no catch); with mp3_margin_db=0
    too it is that master exactly."""
    import pyloudnorm as pyln
    n0 = len(y)
    a = int(round(max(0.0, start_s) * sr))
    b = n0 if end_s is None else min(n0, int(round(end_s * sr)))
    if b <= a:
        raise ValueError(f"nothing between start_s={start_s:g} and end_s={end_s} (the file is {n0 / sr:.2f} s)")
    y = np.array(y[a:b], dtype=np.float64)
    for secs, head in ((fade_in_s, True), (fade_out_s, False)):
        nf = min(len(y), int(round(max(0.0, secs) * sr)))
        if nf:
            w = np.cos(np.linspace(0, np.pi / 2, nf))[:, None] ** 2      # cos^2: equal-power out, sin^2 in
            if head:
                y[:nf] *= w[::-1]
            else:
                y[-nf:] *= w
    if sr != out_sr:
        k = gcd(int(out_sr), int(sr))
        y = resample_poly(y, int(out_sr) // k, int(sr) // k, axis=0)
    meter = pyln.Meter(out_sr)
    if len(y) < int(0.4 * out_sr):
        raise ValueError(f"the cue is {len(y) / out_sr:.2f} s; integrated loudness needs at least 0.4 s")
    before = meter.integrated_loudness(y)
    if not np.isfinite(before):
        raise ValueError("the cue is silent (no loudness to measure): check start_s/end_s and the source")
    gain = lufs - before
    y = y * 10 ** (gain / 20)
    peak_after_gain = true_peak_db(y)
    ceil = tp - max(0.0, mp3_margin_db)
    gr = 0.0
    aim = ceil - MARGIN_DB
    for k in range(TP_ROUNDS if tp_catch else ROUNDS):
        over = true_peak_db(y) - ceil
        if over <= 0:
            break
        if tp_catch and k:       # the re-gain undid this much of the last round (3 plain rounds stop over, M280)
            aim = max(aim - over, ceil - MARGIN_DB - AIM_DEPTH_DB)
        y, g = limit(y, out_sr, aim)
        gr = min(gr, g)
        y = y * 10 ** ((lufs - meter.integrated_loudness(y)) / 20)
    catch_gr = 0.0
    if tp_catch and true_peak_db(y) > ceil:
        y, catch_gr = catch(y, out_sr, ceil)
    rep = {'length_s': len(y) / out_sr, 'sr': int(out_sr), 'channels': y.shape[1], 'source_lufs': float(before),
           'gain_db': float(gain), 'peak_after_gain_dbtp': peak_after_gain, 'limiter_gr_db': gr,
           'catch_gr_db': catch_gr, 'ceiling_dbtp': ceil,
           'lufs': float(meter.integrated_loudness(y)), 'true_peak_dbtp': true_peak_db(y),
           'trimmed': [a / sr, b / sr]}
    rep['over_ceiling'] = rep['true_peak_dbtp'] > ceil + 0.05
    return y, out_sr, rep
