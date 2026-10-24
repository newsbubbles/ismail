"""mimic: an instrument measured from recordings and played back as harmonics + noise + body.

measure() reads a few recorded notes of one instrument and writes a profile: per note, every partial's level and
envelope (attack, sustain or two-stage decay, release), the noise between the partials (attack burst and
sustained), vibrato and pitch drift, inharmonicity. Across notes it splits the partial levels into a body curve
fixed in frequency (resonances, formants) plus a per-note source slope, so an unmeasured pitch keeps the body in
place instead of stretching it.

render() plays one note from a profile. Every partial reads the body at its current frequency (vibrato flutters
through the resonances), temporal parameters are blended between the two nearest measured notes at the same
absolute frequency, noise is shaped white noise in the measured band levels, and small per-note random variation
keeps repeated notes from being identical.
"""
import json
import os

import numpy as np

SR = 44100
HOP = 256
CTRL = 64                      # control-rate hop for synthesis envelopes
BODY_HZ = 2 ** np.arange(np.log2(30), np.log2(18000), 1 / 12)
NOISE_HZ = 2 ** np.arange(np.log2(20), np.log2(21000), 1 / 3)
ATK_NFFT, ATK_HOP, ATK_FRAMES = 1024, 256, 28   # attack map: 23 ms windows, 5.8 ms hop, first ~0.17 s
LOWER_HALF_MEAN = 0.3069       # mean of the lower half of an exponential(1) variable: noise power calibration
# Per-note random variation, the room's included, may move a note's level while the key is down by half of this
# either way at most, so two takes of one note differ by this at most (ledger M301). Unbounded, one velocity drew a
# take 8 dB louder than its neighbours (cs80_lead D5 vel 90, 2026-10-23): on a note shorter than the swell's wander
# the "unit std" slow curves are short pieces scaled up, so their means became level offsets of many dB, and a
# random room IR summed in or out of phase with a steady tone. The timbre variation (which partial is louder, how
# each moves) is kept.
LEVEL_CAP_DB = 3.0


# ------------------------------------------------------------------ analysis

def _midi(f):
    return 69 + 12 * np.log2(f / 440.0)


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _load(path, sr=SR):
    import librosa
    import soundfile as sf
    try:
        y, s = sf.read(path, always_2d=True)
        y = y.mean(axis=1)
    except Exception:
        y, s = librosa.load(path, sr=None, mono=True)
    if s != sr:
        y = librosa.resample(y, orig_sr=s, target_sr=sr)
    return y.astype(np.float64)


def _band_attack(y, kind, sr=SR):
    """Per NOISE_HZ band: seconds from onset until the band reaches its level (sustain - 3 dB, or its peak for
    decaying kinds), and the early overshoot above what the sustain reaches (dB), from 23 ms windows, 3 ms hop."""
    import librosa
    X = np.abs(librosa.stft(y[:int(2.5 * sr)], n_fft=1024, hop_length=128, center=False)) ** 2
    fr = np.fft.rfftfreq(1024, 1 / sr)
    tt = (np.arange(X.shape[1]) * 128 + 512) / sr
    edges = np.concatenate([[NOISE_HZ[0] / 2 ** (1 / 6)], np.sqrt(NOISE_HZ[:-1] * NOISE_HZ[1:]),
                            [NOISE_HZ[-1] * 2 ** (1 / 6)]])
    atk = np.full(len(NOISE_HZ), np.nan)
    ov = np.zeros(len(NOISE_HZ))
    for i in range(len(NOISE_HZ)):
        b = (fr >= max(edges[i], 40.0)) & (fr < edges[i + 1])
        if not b.any():
            continue
        e = 10 * np.log10(np.convolve(X[b].sum(axis=0), np.ones(5) / 5, 'same') + 1e-20)
        if kind == 'sustained':
            sus = e[(tt > 0.4) & (tt < 2.0)]
            if len(sus) < 10 or sus.max() < e.max() - 30:
                continue
            target = np.median(sus) - 3
            ov[i] = max(0.0, float(e[tt < 0.4].max() - np.percentile(sus, 90)))
        else:
            target = e[tt < 0.3].max() - 1
        hit = np.where(e >= target)[0]
        if len(hit):
            atk[i] = tt[hit[0]]
    ok = np.isfinite(atk)
    if ok.sum() >= 2:
        atk = np.interp(np.log2(NOISE_HZ), np.log2(NOISE_HZ[ok]), atk[ok])
    else:
        atk = np.full(len(NOISE_HZ), 0.03)
    return np.clip(atk, 0.002, 0.8), ov


def _fit_decay(t, db):
    """Least-squares slope of dB over time -> T60 (s); inf when not decaying."""
    if len(t) < 3:
        return float('inf')
    a = np.polyfit(t, db, 1)[0]
    return float(-60.0 / a) if a < -1e-3 else float('inf')


def measure_note(y, midi, sr=SR, vel=0.7, kind='auto'):
    import librosa
    f_nom = _hz(midi)
    env = np.sqrt(np.convolve(y ** 2, np.ones(220) / 220, 'same'))
    pk = env.max()
    if pk <= 0:
        raise ValueError('silent recording')
    on = int(np.argmax(env > 0.02 * pk))
    y = y[max(0, on - int(0.005 * sr)):]
    nfft = 8192 if f_nom < 160 else 4096
    win = np.hanning(nfft)
    X = librosa.stft(y, n_fft=nfft, hop_length=HOP, window=win, center=False)
    P = np.abs(X) ** 2
    M = np.abs(X) * 2 / win.sum()                    # sinusoid amplitude scale
    nb, nf = M.shape
    fr = np.fft.rfftfreq(nfft, 1 / sr)
    tf = (np.arange(nf) * HOP + nfft / 2) / sr        # frame centre times

    f0, vflag, _ = librosa.pyin(y, fmin=f_nom * 0.85, fmax=f_nom * 1.18, sr=sr, frame_length=max(2048, nfft // 2),
                                hop_length=HOP, center=False)
    f0t = np.full(nf, np.nan)
    m = min(nf, len(f0))
    f0t[:m] = np.where(vflag[:m], f0[:m], np.nan)
    good = np.isfinite(f0t)
    f_ref = float(np.nanmedian(f0t)) if good.sum() > 5 else f_nom
    f0t = np.where(good, f0t, f_ref)

    # inharmonicity from strong peaks in the loudest frames
    lvl = M.sum(axis=0)
    jloud = np.argsort(lvl)[-12:]
    ks, fk = [], []
    for k in range(1, 40):
        target = k * f_ref
        if target > min(sr / 2 * 0.9, 12000):
            break
        lo, hi = np.searchsorted(fr, [target * (1 - 0.3 / k), target * (1 + 0.3 / k + 0.02)])
        if hi - lo < 2:
            continue
        spec = M[lo:hi, jloud].mean(axis=1)
        i = int(np.argmax(spec))
        if 0 < i < len(spec) - 1 and spec[i] > 1e-5 * M[:, jloud].max():
            a, b, c = np.log(spec[i - 1:i + 2] + 1e-12)
            d = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
            ks.append(k)
            fk.append(fr[lo + i] + d * (fr[1] - fr[0]))
    B = 0.0
    if len(ks) >= 6:
        ks_, fk_ = np.array(ks, float), np.array(fk)
        A = np.stack([np.ones_like(ks_), ks_ ** 2], 1)
        sol = np.linalg.lstsq(A, (fk_ / ks_) ** 2, rcond=None)[0]
        if sol[0] > 0:
            B = float(np.clip(sol[1] / sol[0], 0, 2e-3))
            f_ref = float(np.sqrt(sol[0]))

    # partial tracks
    kmax = int(min(sr / 2 * 0.95, 20000) / f_ref)
    kmax = max(1, min(kmax, 200))
    kk = np.arange(1, kmax + 1)
    stretch = np.sqrt(1 + B * kk ** 2)
    df = fr[1] - fr[0]
    halfw = max(2, int(0.25 * f_ref / df))
    offs = np.arange(-halfw, halfw + 1)
    A_db = np.full((kmax, nf), -120.0)
    for i, k in enumerate(kk):
        fc = k * stretch[i] * f0t
        bins = np.clip(np.round(fc / df).astype(int)[None, :] + offs[:, None], 0, nb - 1)
        win_m = M[bins, np.arange(nf)[None, :]]
        amp = win_m.max(axis=0)
        if halfw >= 4:
            # the window's loudest bin overstates a weak harmonic sitting on noise (it picks the noise's peaks),
            # which made upper harmonics too strong ('trumpet'); take the noise floor out, in power
            floor = np.median(win_m, axis=0)
            amp = np.sqrt(np.maximum(amp ** 2 - floor ** 2, (0.1 * amp) ** 2))
        A_db[i] = 20 * np.log10(amp + 1e-9)

    # noise between partials: lower-half mean power per 1/3 octave, harmonic bins masked
    mask = np.ones((nb, nf), bool)
    for i, k in enumerate(kk):
        fc = k * stretch[i] * f0t
        for o in range(-halfw - 1, halfw + 2):
            b = np.clip(np.round(fc / df).astype(int) + o, 0, nb - 1)
            mask[b, np.arange(nf)] = False
    edges = np.sqrt(NOISE_HZ[:-1] * NOISE_HZ[1:])
    edges = np.concatenate([[NOISE_HZ[0] / 2 ** (1 / 6)], edges, [NOISE_HZ[-1] * 2 ** (1 / 6)]])
    noise_db = np.full((len(NOISE_HZ), nf), -150.0)
    wss = (win ** 2).sum()
    for bi in range(len(NOISE_HZ)):
        lo, hi = np.searchsorted(fr, [edges[bi], edges[bi + 1]])
        if hi - lo < 2 or edges[bi] > sr / 2:
            continue
        Pb = np.where(mask[lo:hi], P[lo:hi], np.nan)
        med = np.nanmedian(Pb, axis=0)
        lowhalf = np.nanmean(np.where(Pb <= med[None, :], Pb, np.nan), axis=0)
        var = lowhalf / LOWER_HALF_MEAN / wss       # white-noise variance density that would give this power
        noise_db[bi] = np.where(np.isfinite(var), 10 * np.log10(np.maximum(var, 1e-15)), np.nan)
    # bands with no free bins (inside a partial's mask) read their neighbours, per frame
    lb = np.log2(NOISE_HZ)
    inband = NOISE_HZ < sr / 2
    for j in range(nf):
        col = noise_db[:, j]
        ok = np.isfinite(col) & (col > -149) & inband
        if ok.sum() >= 2:
            noise_db[:, j] = np.where(ok | ~inband, col, np.interp(lb, lb[ok], col[ok]))
    noise_db = np.nan_to_num(noise_db, nan=-150.0)

    # note shape
    tot = 10 * np.log10((10 ** (A_db / 10)).sum(axis=0) + 1e-15)
    jpk = int(np.argmax(tot))
    peak_db = float(tot[jpk])
    alive = np.where(tot > peak_db - 50)[0]
    jend = int(alive[-1]) if len(alive) else nf - 1
    dur = tf[jend]
    if kind == 'auto':
        # sustained if the level half way through the note is within 10 dB of its peak
        jm = jpk + (jend - jpk) // 2
        kind = 'sustained' if tot[jm] > peak_db - 10 and dur > 0.6 else 'decaying'

    parts = []
    t_on = tf[0]
    swell = 0.0
    # attack timing per band from SHORT windows: the long partial-tracking frames (90-190 ms) swallow the rise and
    # made every note start at full level ("hits the string" where the player eases in)
    b_atk, b_ov = _band_attack(y, kind, sr)
    if kind == 'sustained':
        # the held part is the longest stretch within 12 dB of the note's median level (gaps under 60 ms bridged).
        # Anchoring it at the loudest frame measured a note that swells into its cut-off, or runs into the next
        # note, in its release
        med = np.median(tot[:jend + 1])
        idx = np.flatnonzero(tot[:jend + 1] > med - 12)
        cuts = np.flatnonzero(np.diff(idx) > int(0.06 * sr / HOP))
        starts, ends = np.r_[idx[0], idx[cuts + 1]], np.r_[idx[cuts], idx[-1]]
        r = int(np.argmax(ends - starts))
        j0, j1 = int(starts[r]), int(ends[r])
        jstop = int(starts[r + 1]) if r + 1 < len(starts) else nf     # the next note, if the window caught one
        trim = min(int(0.35 * sr / HOP), (j1 - j0) // 4)
        sus = np.arange(j0 + trim, j1 - trim + 1)
        if len(sus) < 4:
            sus = np.arange(j0, min(j0 + 4, nf))
        sus_lvl = np.median(tot[sus])
        # attack ends where the level first reaches 3 dB under the held level; release starts where it falls
        # 6 dB under it for good
        jatk_end = j0 + int(np.argmax(tot[j0:sus[0] + 1] > sus_lvl - 3))
        above = np.flatnonzero(tot[j0:j1 + 1] > sus_lvl - 6)
        jrel = max(j0 + int(above[-1]) if len(above) else j1, int(sus[-1]))
        peak_db = float(tot[:jrel + 1].max())
        alive = np.flatnonzero(tot[:jstop] > peak_db - 50)
        jend = int(alive[-1]) if len(alive) else jstop - 1
        dur = tf[jend]
        for i in range(kmax):
            a = A_db[i]
            s_lvl = float(np.median(a[sus]))
            lf_i = np.log2((i + 1) * stretch[i] * f_ref)
            atk = float(np.interp(lf_i, np.log2(NOISE_HZ), b_atk))
            ov = float(np.interp(lf_i, np.log2(NOISE_HZ), b_ov))
            fl = float(np.std(a[sus] - np.convolve(a[sus], np.ones(9) / 9, 'same')[:len(sus)])) if len(sus) > 12 else 0.5
            rel = a[jrel:min(jrel + int(1.5 * sr / HOP), jstop)]
            rt = tf[jrel:jrel + len(rel)] - tf[jrel]
            live = rel > s_lvl - 45
            t60 = _fit_decay(rt[live], rel[live]) if live.sum() > 3 else 0.3
            parts.append((s_lvl, atk, ov, fl, min(t60, 8.0)))
        level = np.array([p[0] for p in parts])
        arr = {'attack': [p[1] for p in parts], 'overshoot': [p[2] for p in parts],
               'shimmer': [p[3] for p in parts], 'release': [p[4] for p in parts]}
        noise_sus = np.median(noise_db[:, sus], axis=1) - np.median(tot[sus])
        vib_frames = sus
        # slow swells of the whole note (bow pressure, breath): the level's wander over ~0.3 s windows, trend removed
        if len(sus) > 60:
            ts = tot[sus]
            sm = np.convolve(ts, np.ones(51) / 51, 'valid')
            xs = np.arange(len(sm))
            swell = float(np.std(sm - np.polyval(np.polyfit(xs, sm, 1), xs)))
    else:
        for i in range(kmax):
            a = A_db[i]
            jp = int(np.argmax(a[:jpk + int(0.2 * sr / HOP)]))
            lp = float(a[jp])
            tt = tf[jp:jend + 1] - tf[jp]
            aa = a[jp:jend + 1]
            live = aa > lp - 50
            tt, aa = tt[live], aa[live]
            # two-stage: prompt over the first 20 dB, then the rest
            cut = np.where(aa < lp - 20)[0]
            c = int(cut[0]) if len(cut) else len(aa)
            t1 = _fit_decay(tt[:c], aa[:c]) if c > 3 else _fit_decay(tt, aa)
            t2 = _fit_decay(tt[c:], aa[c:]) if len(aa) - c > 3 else t1
            # beating between unison strings: the envelope's ripple around a smooth decay over the first 3 s
            bhz, bdb = 0.0, 0.0
            win = tt < 3.0
            if win.sum() > 40:
                rr = aa[win] - np.polyval(np.polyfit(tt[win], aa[win], 2), tt[win])
                sp = np.abs(np.fft.rfft(rr * np.hanning(len(rr))))
                ff = np.fft.rfftfreq(len(rr), HOP / sr)
                band = (ff > 0.3) & (ff < 8)
                if band.any():
                    j = int(np.argmax(np.where(band, sp, 0)))
                    if sp[j] > 2.5 * np.median(sp[band] + 1e-9):
                        bhz = float(ff[j])
                        bdb = float(min(np.percentile(rr, 95) - np.percentile(rr, 5), 30.0))
            atk = float(np.interp(np.log2((i + 1) * stretch[i] * f_ref), np.log2(NOISE_HZ), b_atk))
            parts.append((lp, atk, min(t1, 60.0), min(t2, 60.0), bhz, bdb))
        level = np.array([p[0] for p in parts])
        arr = {'attack': [p[1] for p in parts], 't60_prompt': [p[2] for p in parts],
               't60_after': [p[3] for p in parts], 'beat_hz': [p[4] for p in parts], 'beat_db': [p[5] for p in parts]}
        noise_sus = np.median(noise_db[:, jpk:jpk + max(4, int(0.3 * sr / HOP))], axis=1) - peak_db
        vib_frames = np.arange(jpk, min(jend, jpk + int(1.0 * sr / HOP)))
        jatk_end = jpk
    atk_frames = np.arange(0, max(1, jatk_end + 1))
    noise_atk = noise_db[:, atk_frames].max(axis=1) - peak_db

    # vibrato cycle by cycle (rate, depth, their spread, how it builds up) and slow drift, in cents
    vib = {'vib_rate': 0.0, 'vib_depth': 0.0, 'vib_rate_sd': 0.0, 'vib_depth_sd': 0.0, 'vib_onset': 0.0}
    drift = 0.0
    frames = np.arange(0, int(vib_frames[-1]) + 1) if len(vib_frames) else np.arange(0)
    gv = good[frames] if len(frames) else np.zeros(0, bool)
    if gv.sum() > 30:
        tv = tf[frames][gv] - t_on
        c = 1200 * np.log2(f0t[frames][gv] / f_ref)
        trend = np.convolve(c, np.ones(31) / 31, 'same')
        dev = c - trend
        drift = float(np.std(trend[len(trend) // 5:]))
        zc = np.where((dev[:-1] < 0) & (dev[1:] >= 0))[0]
        cyc = []
        for a, b in zip(zc[:-1], zc[1:]):
            per = tv[b] - tv[a]
            if 1 / 9.5 < per < 1 / 3.0:
                cyc.append((tv[a], 1 / per, (dev[a:b + 1].max() - dev[a:b + 1].min()) / 2))
        if len(cyc) >= 4:
            cyc = np.array(cyc)
            late = cyc[cyc[:, 0] > 0.6] if (cyc[:, 0] > 0.6).sum() >= 3 else cyc
            rate, depth = float(np.median(late[:, 1])), float(np.median(late[:, 2]))
            if depth > 2.0:
                sm = np.convolve(cyc[:, 2], np.ones(2) / 2, 'same')       # two-cycle average, past the onset wobble
                reach = np.where((sm >= 0.8 * depth) & (cyc[:, 0] > 0.12))[0]
                vib = {'vib_rate': rate, 'vib_depth': depth,
                       'vib_rate_sd': float(np.std(late[:, 1]) / rate), 'vib_depth_sd': float(np.std(late[:, 2]) / depth),
                       'vib_onset': float(cyc[reach[0], 0]) if len(reach) else 0.3}

    return {'midi': float(_midi(f_ref)), 'f0': f_ref, 'vel': vel, 'kind': kind, 'B': B, 'peak_db': peak_db,
            'dur': float(dur), 'partials': len(level), 'level': [round(float(x), 2) for x in level],
            **{k: [round(float(x), 4) for x in v] for k, v in arr.items()},
            'noise_sus': [round(float(x), 2) for x in noise_sus], 'noise_atk': [round(float(x), 2) for x in noise_atk],
            **{k: round(v, 4) for k, v in vib.items()}, 'drift': drift, 'swell': round(swell, 3)}


def _body_fit(notes, iters=10, smooth=5):
    """L[n,k] ~ body(f_nk) + s_n*log2(k) + g_n. Returns body (dB on BODY_HZ), slopes, gains, residuals."""
    rows = []
    for n in notes:
        k = np.arange(1, n['partials'] + 1)
        f = k * n['f0'] * np.sqrt(1 + n['B'] * k ** 2)
        lv = np.array(n['level'])
        top = lv.max()
        ok = lv > top - 60                         # ignore partials lost in the floor
        rows.append((k[ok], f[ok], lv[ok]))
    s = np.zeros(len(rows))
    g = np.array([r[2].max() for r in rows])
    body = np.zeros(len(BODY_HZ))
    lb = np.log2(BODY_HZ)
    for _ in range(iters):
        acc = np.zeros(len(BODY_HZ))
        cnt = np.zeros(len(BODY_HZ))
        for (k, f, lv), si, gi in zip(rows, s, g):
            r = lv - si * np.log2(k) - gi
            idx = np.clip(np.round((np.log2(f) - lb[0]) / (lb[1] - lb[0])).astype(int), 0, len(BODY_HZ) - 1)
            np.add.at(acc, idx, r)
            np.add.at(cnt, idx, 1)
        have = cnt > 0
        body = np.interp(lb, lb[have], acc[have] / cnt[have]) if have.any() else body
        if smooth > 1:
            body = np.convolve(np.pad(body, smooth // 2, mode='edge'), np.ones(smooth) / smooth, 'valid')
        body -= body[have].mean() if have.any() else 0
        for j, (k, f, lv) in enumerate(rows):
            r = lv - np.interp(np.log2(f), lb, body)
            A = np.stack([np.log2(k), np.ones_like(k, dtype=float)], 1)
            s[j], g[j] = np.linalg.lstsq(A, r, rcond=None)[0]
    resid = []
    for n, si, gi in zip(notes, s, g):
        k = np.arange(1, n['partials'] + 1)
        f = k * n['f0'] * np.sqrt(1 + n['B'] * k ** 2)
        resid.append([round(float(x), 2) for x in np.array(n['level']) - np.interp(np.log2(f), lb, body)
                      - si * np.log2(k) - gi])
    return body, s, g, resid


def _gap_bands(y, f0, t0, t1, sr=SR):
    """Energy between the harmonics per NOISE_HZ band (dB), over [t0, t1] s: what the noise has to fill."""
    import librosa
    y = y[int(t0 * sr):int(t1 * sr)]
    if len(y) < 4096:
        return None
    X = (np.abs(librosa.stft(y, n_fft=4096, hop_length=512)) ** 2).mean(axis=1)
    fr = np.fft.rfftfreq(4096, 1 / sr)
    k = np.maximum(np.round(fr / f0), 1)
    gap = np.abs(fr - k * f0) > 0.3 * f0
    edges = np.concatenate([[NOISE_HZ[0] / 2 ** (1 / 6)], np.sqrt(NOISE_HZ[:-1] * NOISE_HZ[1:]),
                            [NOISE_HZ[-1] * 2 ** (1 / 6)]])
    out = np.full(len(NOISE_HZ), np.nan)
    for i in range(len(NOISE_HZ)):
        b = gap & (fr >= edges[i]) & (fr < edges[i + 1])
        if b.sum() >= 2:
            out[i] = 10 * np.log10(X[b].mean() + 1e-20)
    return out


def calibrate_noise(y, note, rounds=2, sr=SR):
    """Analysis by synthesis for the noise: rebuild the note from its own measurement and move each noise band
    until the energy between the harmonics matches the recording (the direct estimate reads low when the noise
    is uneven within a band, as bow and breath noise are)."""
    env = np.sqrt(np.convolve(y ** 2, np.ones(220) / 220, 'same'))
    on = int(np.argmax(env > 0.02 * env.max()))
    y = y[max(0, on - int(0.005 * sr)):]
    sus = note['kind'] == 'sustained'
    t0, t1 = (0.3, min(2.0, note['dur'] * 0.8)) if sus else (0.03, min(1.0, note['dur'] * 0.5))
    if t1 - t0 < 0.2:
        return note
    real = _gap_bands(y, note['f0'], t0, t1, sr)
    if real is None:
        return note
    for _ in range(rounds):
        prof = profile_from_notes([note], body_smooth=1)
        gate = t1 + 0.3 if sus else 3.0
        z = render(prof, note['f0'], np.arange(int((t1 + 0.4) * sr)) / sr, note['vel'], gate, sr,
                   variation=0.0, width=0.0).mean(axis=0)
        z *= np.sqrt(np.mean(y[int(t0 * sr):int(t1 * sr)] ** 2) / (np.mean(z[int(t0 * sr):int(t1 * sr)] ** 2) + 1e-20))
        synth = _gap_bands(z, note['f0'], t0, t1, sr)
        corr = np.nan_to_num(real - synth, nan=0.0)
        corr = np.clip(corr, -30, 30)
        # the gap energy is noise plus partial skirts; only raise/lower noise where noise dominates or is missing
        note['noise_sus'] = [round(float(a + c), 2) for a, c in zip(note['noise_sus'], corr)]
    return note


def _atk_bands(y, f0, sr=SR):
    """Power per NOISE_HZ band x attack frame (short windows), counting only bins away from the harmonics (below
    the fundamental and in the gaps), so the map holds knock and scrape, never the partials themselves."""
    import librosa
    from scipy.signal import butter, sosfilt
    need = ATK_NFFT + ATK_HOP * (ATK_FRAMES - 1)
    y = sosfilt(butter(2, 20.0, 'highpass', fs=sr, output='sos'), y[:need])
    y = np.pad(y, (0, max(0, need - len(y))))
    X = np.abs(librosa.stft(y, n_fft=ATK_NFFT, hop_length=ATK_HOP, center=False)) ** 2
    fr = np.fft.rfftfreq(ATK_NFFT, 1 / sr)
    edges = np.concatenate([[NOISE_HZ[0] / 2 ** (1 / 6)], np.sqrt(NOISE_HZ[:-1] * NOISE_HZ[1:]),
                            [NOISE_HZ[-1] * 2 ** (1 / 6)]])
    out = np.full((len(NOISE_HZ), X.shape[1]), np.nan)
    for i in range(len(NOISE_HZ)):
        b = (fr >= edges[i]) & (fr < edges[i + 1])
        b &= fr >= 40.0                         # a 1024-point window can't resolve lower (bin 0 is DC)
        k = np.maximum(np.round(fr / f0), 1)
        b &= (fr < 0.6 * f0) | (np.abs(fr - k * f0) > 0.35 * f0 + 50.0)
        if b.any():
            out[i] = X[b].mean(axis=0)
    return out[:, :ATK_FRAMES]


def _lineness(y, f0, t0, t1, sr=SR):
    """Per harmonic k (6..30, under 12 kHz): share of its energy within +-3 bins of the line, 8192-point frames.
    A clean sinusoid is ~1; a string's high harmonics are smeared by cycle-to-cycle period jitter (~0.2-0.5)."""
    import librosa
    seg = y[int(t0 * sr):int(t1 * sr)]
    if len(seg) < 8192:
        return None, None
    X = (np.abs(librosa.stft(seg, n_fft=8192, hop_length=1024)) ** 2).sum(axis=1)
    df = sr / 8192
    ks, out = [], []
    for k in range(2, 31):
        if k * f0 > 12000:
            break
        c = int(round(k * f0 / df))
        w = int(0.3 * f0 / df)
        if c + w + 1 >= len(X) or w < 5:
            continue
        ks.append(k)
        out.append(X[c - 3:c + 4].sum() / (X[c - w:c + w + 1].sum() + 1e-20))
    return (np.array(ks), np.array(out)) if ks else (None, None)


def _skirt_bw(f0):
    """Half-width (Hz) of the noise skirt around each harmonic: a recording's skirts spread over most of the gap."""
    return float(min(0.25 * f0, 400.0))


def _core_frac(f0, sr=SR):
    """Share of a flat skirt of that width that lands inside the +-3-bin 'line' _lineness measures."""
    return float(min(0.9, 7 * sr / 8192 / (2 * _skirt_bw(f0))))


def calibrate_smear(y, note, sr=SR):
    """Noise skirts around the harmonics (sustained kinds). Bow and breath noise is pulsed by the vibration
    itself, so it clusters around each harmonic instead of filling the gaps evenly; clean lines where the
    recording has skirts sound 'digital'. Per harmonic: how much less line-like the recording is than a skirt-free
    rebuild (which already includes vibrato) gives the skirt's power relative to the line, directly."""
    note['smear'] = []
    if note['kind'] != 'sustained':
        return note
    env = np.sqrt(np.convolve(y ** 2, np.ones(220) / 220, 'same'))
    on = int(np.argmax(env > 0.02 * env.max()))
    y = y[max(0, on - int(0.005 * sr)):]
    t0, t1 = 0.4, min(2.2, note['dur'] * 0.8)
    ks, real = _lineness(y, note['f0'], t0, t1, sr)
    if ks is None or len(ks) < 2:
        return note
    prof = profile_from_notes([note], body_smooth=1)
    z = render(prof, note['f0'], np.arange(int((t1 + 0.3) * sr)) / sr, note['vel'], t1 + 0.2, sr,
               width=0.0, seed=11, smear=0.0).mean(axis=0)
    k2, syn = _lineness(z, note['f0'], t0, t1, sr)
    if k2 is None or len(k2) != len(ks):
        return note
    cf = _core_frac(note['f0'], sr)
    L = np.clip(real / np.maximum(syn, 1e-3), cf + 0.02, 1.0)
    rho = np.clip((1 - L) / (L - cf), 0, 20)                  # skirt power / line power
    kk = np.arange(1, note['partials'] + 1)
    note['smear'] = [round(float(v), 3) for v in np.interp(kk, ks, rho, left=0.0, right=float(rho[-1]))]
    return note


def calibrate_attack(y, note, rounds=2, sr=SR):
    """Analysis by synthesis for the attack: where the recording's first ~170 ms has more energy than the rebuilt
    note in a band and frame (hammer knock, soundboard thump, bow scrape, pick), add that much noise there."""
    env = np.sqrt(np.convolve(y ** 2, np.ones(220) / 220, 'same'))
    on = int(np.argmax(env > 0.02 * env.max()))
    y = y[max(0, on - int(0.005 * sr)):]
    sus = note['kind'] == 'sustained'
    t0, t1 = (0.3, min(2.0, note['dur'] * 0.8)) if sus else (0.03, min(1.0, note['dur'] * 0.5))
    # a short note (a 0.3 s panpipe) ends before the usual level window starts: its level was the mean of nothing
    if t1 - t0 < 0.05:
        t1 = min(max(t1, 0.08), len(y) / sr)
        t0 = min(t0, t1 * 0.5)
    wss = (np.hanning(ATK_NFFT) ** 2).sum()
    real = _atk_bands(y, note['f0'], sr)
    note['atk_tf'] = [[-150.0] * ATK_FRAMES for _ in NOISE_HZ]
    note['atk_ref_db'] = round(float(20 * np.log10(np.sqrt(np.mean(y[int(t0 * sr):int(t1 * sr)] ** 2)) + 1e-12)), 2)
    for _ in range(rounds):
        prof = profile_from_notes([note], body_smooth=1)
        gate = t1 + 0.3 if sus else 3.0
        z = render(prof, note['f0'], np.arange(int((max(t1, 0.4) + 0.4) * sr)) / sr, note['vel'], gate, sr,
                   variation=0.0, width=0.0).mean(axis=0)
        z *= np.sqrt(np.mean(y[int(t0 * sr):int(t1 * sr)] ** 2) / (np.mean(z[int(t0 * sr):int(t1 * sr)] ** 2) + 1e-20))
        synth = _atk_bands(z, note['f0'], sr)
        miss = np.nan_to_num(np.minimum(real - synth, real), nan=0.0)
        cur = 10 ** (np.array(note['atk_tf']) / 10)
        add = np.maximum(miss, 0) / wss                    # missing power -> white-noise variance density
        new = 10 * np.log10(cur + add + 1e-20)
        note['atk_tf'] = [[round(float(v), 1) for v in row] for row in np.maximum(new, -150)]
    return note


def measure_notes(items, kind='auto', calibrate=True):
    """items: [(path_or_array, midi, vel)] one recorded note each -> measured notes, all of one kind."""
    ys = [(_load(src) if isinstance(src, str) else np.asarray(src, float), src, midi, vel) for src, midi, vel in items]
    notes = [measure_note(y, midi, vel=vel, kind=kind) for y, _, midi, vel in ys]
    kinds = [n['kind'] for n in notes]
    kind = max(set(kinds), key=kinds.count)
    for i, (y, src, midi, vel) in enumerate(ys):
        if notes[i]['kind'] != kind:       # one instrument, one kind: re-measure the odd ones out
            notes[i] = measure_note(y, midi, vel=vel, kind=kind)
        notes[i]['file'] = os.path.basename(src) if isinstance(src, str) else None
        if calibrate:
            notes[i] = calibrate_smear(y, notes[i])      # first: skirts around the harmonics
            notes[i] = calibrate_noise(y, notes[i])
            notes[i] = calibrate_attack(y, notes[i])
    return notes


def measure(items, name='mimic', kind='auto', source=None):
    """items: [(path_or_array, midi, vel)] one recorded note each. Returns a profile dict."""
    return profile_from_notes(measure_notes(items, kind), name, source)


def profile_from_notes(notes, name='mimic', source=None, body_smooth=1):
    import copy
    notes = sorted(copy.deepcopy(notes), key=lambda n: n['midi'])
    kind = notes[0]['kind']
    body, s, g, resid = _body_fit(notes, smooth=body_smooth)
    for n, si, gi, r in zip(notes, s, g, resid):
        n['slope'], n['gain'], n['resid'] = round(float(si), 3), round(float(gi), 2), r
    # play the loudest measured note at about -12 dBFS, keeping the recorded balance between registers
    norm = -12.0 - max(n['peak_db'] for n in notes)
    # the recordings' room: after the bow or breath stops, every strong harmonic dies at about the same rate
    # whatever its frequency (~0.4-0.6 s in a hall); a string alone would stop faster and unevenly
    room_rt = 0.0
    if kind == 'sustained':
        rts = []
        for n in notes:
            lv, rl = np.array(n['level']), np.array(n['release'])
            strong = lv > lv.max() - 30
            if strong.any():
                rts.append(float(np.median(rl[strong])))
        if rts:
            room_rt = float(np.clip(np.median(rts), 0.2, 2.5))
    # the part of the harmonic skirts that carries over between notes: a smooth curve over frequency (median per
    # 1/3 octave across all notes, smoothed over an octave); each note's own spikes stay with that note
    sm_hz = 2 ** np.arange(np.log2(50), np.log2(20000), 1 / 3)
    pts = [[] for _ in sm_hz]
    for n in notes:
        k = np.arange(1, len(n.get('smear') or []) + 1)
        f = k * n['f0'] * np.sqrt(1 + n['B'] * k ** 2)
        for fi, v in zip(f, n.get('smear') or []):
            j = int(np.clip(np.round((np.log2(fi) - np.log2(sm_hz[0])) * 3), 0, len(sm_hz) - 1))
            pts[j].append(v)
    have = np.array([len(p) > 0 for p in pts])
    smear_curve = np.zeros(len(sm_hz))
    if have.sum() >= 2:
        med = np.array([np.median(p) if p else 0.0 for p in pts])
        smear_curve = np.interp(np.log2(sm_hz), np.log2(sm_hz[have]), med[have])
        smear_curve = np.convolve(np.pad(smear_curve, 1, mode='edge'), np.ones(3) / 3, 'valid')
    return {'mimic': 1, 'name': name, 'kind': kind, 'source': source, 'norm_db': round(float(norm), 2),
            'room_rt': round(room_rt, 3), 'smear_hz': [round(float(x), 1) for x in sm_hz], 'smear_curve': [round(float(x), 3) for x in smear_curve], 'body_hz': [round(float(x), 2) for x in BODY_HZ],
            'body_db': [round(float(x), 2) for x in body], 'noise_hz': [round(float(x), 1) for x in NOISE_HZ],
            'notes': notes}


# ------------------------------------------------------------------ synthesis

DEFAULT_PARAMS = {
    'vib': None,          # vibrato depth in cents (None = as measured)
    'vib_rate': None,     # Hz (None = as measured)
    'vib_delay': None,    # s for vibrato to build to full depth (None = as measured)
    'vib_var': 1.0,       # scale on the measured cycle-to-cycle variation of vibrato rate and depth
    'smear': 0.0,         # noise skirts around the harmonics, as measured (1 = the instrument's average skirt curve).
                          # Off by default: on held-out notes it scored worse (violin 11.9 -> 14.1); an option to try
                          # when the top end sounds 'digital'
    'players': 1,         # a section: detuned copies with their own vibrato, timing and shimmer
    'detune': 8.0,        # cents spread between players
    'width': 0.5,         # 0 mono .. 1 wide (per-partial phase and level between channels)
    'noise': 0.0,         # dB offset on the measured noise
    'bright': 0.0,        # dB per octave of partial number, added
    'vel_bright': 6.0,    # dB per octave of partial number per unit of velocity above the measured one
    'attack': 1.0,        # scale on measured attack times
    'release': 1.0,       # scale on measured release times (sustained) / damper time (decaying)
    'damp': 0.25,         # decaying kinds: seconds to die after the key is released (None = ring on)
    'detail': 1.0,        # 0..1: how much of each measured note's own partial pattern to keep (vs the smooth model)
    'variation': 1.0,     # per-note random variation (brightness, timing of partials, level: two takes of a note
                          # differ by LEVEL_CAP_DB at most, room included)
    'seed': None,
    'swell': 1.0,         # scale on the measured slow swells of a sustained note (bow pressure, breath)
    'room': None,         # reverb time (RT60, s) of a room around the instrument; None = the room measured from
                          # the recordings' release tails (sustained kinds), 0 = dry
    'room_mix': -10.0,    # room level against the dry sound, dB
    'ring': None,         # dB: body modes as ringing resonators (peaks of the measured body); None = off
    'ring_q': 25.0,       # their sharpness
    'strings': None,      # open strings that ring in sympathy, e.g. ['G3', 'D4', 'A4', 'E5'] (violin)
    'sympathy': -18.0,    # their level at exact coincidence, dB
    'string_t60': 3.0,    # how long they ring (s)
    'beat': 0.5,          # decaying kinds: scale on the measured beating between unison strings (0 = one string);
                          # 0.5 because the measured depth runs high and a listener preferred less (2026-09-29)
    'knock': 1.0,         # scale on the measured attack map (hammer knock, thump, scrape, pick); 0 = off
    'mono': False,        # compute one channel and copy it to both (for a caller that sums to mono)
    'floor': None,        # dB: skip partials this far under the strongest (None = all). 60 renders drafts faster
                          # but dulls the top of low notes by 3 to 5 dB (cello C2, 2026-10-04)
}

_PROFILES = {}


def load_profile(name, root=None):
    from . import voices
    for _, d in voices.search_path(root):
        p = os.path.join(d, name + '.mimic.json')
        if os.path.isfile(p):
            key = (p, os.path.getmtime(p))
            if key not in _PROFILES:
                _PROFILES[key] = json.load(open(p, encoding='utf-8'))
            return _PROFILES[key]
    have = sorted({f[:-11] for _, d in voices.search_path(root) if os.path.isdir(d)
                   for f in os.listdir(d) if f.endswith('.mimic.json')})
    raise ValueError(f"no mimic profile {name!r}; available: {', '.join(have) or 'none'} (make one with mimic_measure)")


def _neighbours(notes, midi):
    ms = np.array([n['midi'] for n in notes])
    if midi <= ms[0]:
        return notes[0], notes[0], 0.0
    if midi >= ms[-1]:
        return notes[-1], notes[-1], 0.0
    j = int(np.searchsorted(ms, midi))
    a, b = notes[j - 1], notes[j]
    return a, b, float((midi - a['midi']) / (b['midi'] - a['midi']))


def _curve(n, key, f):
    """A per-partial array of note n, read at absolute frequencies f (log-frequency interpolation)."""
    k = np.arange(1, n['partials'] + 1)
    fk = k * n['f0'] * np.sqrt(1 + n['B'] * k ** 2)
    v = np.asarray(n[key], float)
    return np.interp(np.log2(f), np.log2(fk), v)


def _blend(a, b, w, key, f, log=False):
    va, vb = _curve(a, key, f), _curve(b, key, f)
    if log:
        va, vb = np.log(np.maximum(va, 1e-4)), np.log(np.maximum(vb, 1e-4))
        return np.exp(va * (1 - w) + vb * w)
    return va * (1 - w) + vb * w


def _vibrato(rng, tc, delay, rate, depth, rate_sd, depth_sd, onset):
    """Cycle by cycle, around the measured rate and depth. Most of the variation WANDERS (a slow random curve over
    about a second: 'fast, then slightly slow, then fast again'), a little is new every cycle; depth builds up
    over `onset` seconds as a player's does."""
    t_end = tc[-1] + 1.0
    grid = np.arange(0, t_end + 2.0, 0.05)
    slow_r = _smooth_noise(rng, len(grid), 0.9, 20.0)            # ~1 s wander, unit std
    slow_d = _smooth_noise(rng, len(grid), 0.7, 20.0)
    knots_t, knots_r, knots_d = [], [], []
    t = 0.0
    while t < t_end:
        wr, wd = np.interp(t, grid, slow_r), np.interp(t, grid, slow_d)
        r = max(2.5, rate * (1 + rate_sd * (0.85 * wr + 0.5 * rng.standard_normal())))
        d = max(0.0, depth * (1 + depth_sd * (0.85 * wd + 0.5 * rng.standard_normal())))
        knots_t.append(t)
        knots_r.append(r)
        knots_d.append(d)
        t += 1.0 / r
    kt = np.array(knots_t)
    r_c = np.interp(tc, kt, knots_r)
    d_c = np.interp(tc, kt, knots_d)
    ph = 2 * np.pi * np.cumsum(r_c) * (tc[1] - tc[0] if len(tc) > 1 else 0.0) + rng.uniform(0, 2 * np.pi)
    build = np.clip((tc - delay) / max(onset, 0.05), 0, 1) ** 1.5
    return d_c * build * np.sin(ph)


def _smooth_noise(rng, n, rate_hz, sr_ctrl):
    """Band-limited random walk-ish signal, unit std in expectation, at control rate. Every value sees the whole
    smoothing window, and the scale is the window's own (white noise through h has std sqrt(sum(h**2))), not the
    slice's: a short note gets a short stretch of the same slow curve a long note has. Dividing by the slice's
    own std stretched a nearly flat slice to unit std, so short notes came out tens of cents off (ledger:M306)."""
    k = max(1, int(sr_ctrl / max(rate_hz, 0.1)))
    h = np.hanning(2 * k + 1)
    x = rng.standard_normal(n + 2 * k)
    return np.convolve(x, h, 'valid') / np.sqrt(np.sum(h ** 2))


def render(profile, freq, t, vel, gate, sr=SR, **params):
    """One note -> (2, len(t)) array."""
    p = dict(DEFAULT_PARAMS)
    p.update(profile.get('defaults') or {})          # a profile can carry its instrument's own settings
    p.update({k: v for k, v in params.items() if v is not None or k in ('damp', 'floor')})
    unknown = set(params) - set(DEFAULT_PARAMS)
    if unknown:
        raise ValueError(f"unknown mimic params {sorted(unknown)}; valid: {sorted(DEFAULT_PARAMS)}")
    n = len(t)
    out = np.zeros((2, n))
    if n == 0:
        return out
    notes = profile['notes']
    midi = float(_midi(freq))
    a, b, w = _neighbours(notes, midi)
    seed = p['seed'] if p['seed'] is not None else int(freq * 1000 + vel * 7919 + gate * 131) % (2 ** 31)
    rng = np.random.default_rng(seed)
    body_hz, body_db = np.log2(np.array(profile['body_hz'])), np.array(profile['body_db'])
    kind = profile['kind']
    B = a['B'] * (1 - w) + b['B'] * w
    slope = a['slope'] * (1 - w) + b['slope'] * w
    gain = a['gain'] * (1 - w) + b['gain'] * w
    vel_ref = a['vel'] * (1 - w) + b['vel'] * w
    def mix(key, default=0.0):
        return a.get(key, default) * (1 - w) + b.get(key, default) * w
    vib_d = p['vib'] if p['vib'] is not None else mix('vib_depth')
    vib_r = p['vib_rate'] if p['vib_rate'] is not None else mix('vib_rate') or 5.5
    vib_rsd, vib_dsd = mix('vib_rate_sd') * p['vib_var'], mix('vib_depth_sd') * p['vib_var']
    vib_on = p['vib_delay'] if p['vib_delay'] is not None else mix('vib_onset', 0.3)
    drift = a['drift'] * (1 - w) + b['drift'] * w
    def by_k(key):
        va, vb = np.asarray(a.get(key) or [0.0]), np.asarray(b.get(key) or [0.0])
        return (np.interp(kk, np.arange(1, len(va) + 1), va) * (1 - w) +
                np.interp(kk, np.arange(1, len(vb) + 1), vb) * w)
    nyq = min(sr / 2 * 0.95, 20000)
    kmax = max(1, min(int(nyq / freq), 200))
    kk = np.arange(1, kmax + 1)
    stretch = np.sqrt(1 + B * kk ** 2)
    fk = kk * stretch * freq
    keep = fk < nyq
    kk, stretch, fk = kk[keep], stretch[keep], fk[keep]
    lk = np.log2(kk)
    # skirt amplitude relative to the line, read at absolute frequency like the body it comes from (a harmonic on
    # the flank of a sharp body resonance gets its level wobbled by vibrato, which spreads noise around it)
    if profile.get('smear_curve'):
        smear = np.sqrt(np.maximum(np.interp(np.log2(fk), np.log2(profile['smear_hz']), profile['smear_curve'])
                                   * p['smear'], 0.0))
    else:
        smear = np.zeros(len(kk))

    # static level per partial: smooth model + measured detail (by partial number, blended)
    ra = np.asarray(a['resid']); rb = np.asarray(b['resid'])
    det = (np.interp(kk, np.arange(1, len(ra) + 1), ra) * (1 - w) + np.interp(kk, np.arange(1, len(rb) + 1), rb) * w)
    det = np.where(kk <= min(len(ra), len(rb)), det, 0.0)
    dv = vel - vel_ref
    base = slope * lk + gain + p['detail'] * det + (p['bright'] + p['vel_bright'] * dv) * lk
    base += 20 * np.log10(max(vel, 1e-3) / max(vel_ref, 1e-3)) + profile.get('norm_db', 0.0)
    bvar = p['variation'] * rng.normal(0, 0.7, len(kk))
    base += bvar

    tc = np.arange(0, n + CTRL, CTRL) / sr                # control times
    ncs = len(tc)
    sr_c = sr / CTRL
    atk0 = _blend(a, b, w, 'attack', fk) * p['attack']
    atk = np.maximum(atk0 * (1 + p['variation'] * rng.normal(0, 0.08, len(kk))), 0.002)
    atk0 = np.maximum(atk0, 0.002)
    if kind == 'sustained':
        ov = _blend(a, b, w, 'overshoot', fk)
        shim = _blend(a, b, w, 'shimmer', fk)
        rel = np.maximum(_blend(a, b, w, 'release', fk, log=True) * p['release'], 0.02)
    else:
        t1 = np.maximum(_blend(a, b, w, 't60_prompt', fk, log=True), 0.05)
        t2 = np.maximum(_blend(a, b, w, 't60_after', fk, log=True), 0.05)
        if 'beat_hz' in a and 'beat_hz' in b and p['beat'] > 0:
            bhz = _blend(a, b, w, 'beat_hz', fk)
            bdb = _blend(a, b, w, 'beat_db', fk) * p['beat']
        else:
            bhz = bdb = np.zeros(len(kk))

    # partials far under the strongest are inaudible in the sum: skip them (low notes reach 200 partials)
    lvl0 = base + np.interp(np.log2(fk), body_hz, body_db)
    weak = lvl0 < lvl0.max() - p['floor'] if p['floor'] is not None else np.zeros(len(kk), bool)
    mono = bool(p['mono'])
    players = max(1, int(p['players']))
    phi_c = phi0 = None
    ts = np.arange(n) / sr                               # sample times, shared by every partial and layer
    ar = np.arange(n)
    for pl in range(players):
        prng = np.random.default_rng(seed + 7919 * (pl + 1))
        cents = 0.0 if players == 1 else prng.uniform(-1, 1) * p['detune']
        delay = 0.0 if players == 1 else abs(prng.normal(0, 0.012))
        vr = vib_r * (1 + (prng.uniform(-0.08, 0.08) if players > 1 else 0))
        vib = _vibrato(prng, tc, delay, vr, vib_d, vib_rsd, vib_dsd, vib_on) if vib_d > 0 else np.zeros(ncs)
        vib += drift * _smooth_noise(prng, ncs, 1.5, sr_c)
        f0c = freq * 2 ** ((cents + vib) / 1200)
        f0s = np.interp(ts, tc, f0c)
        phase = 2 * np.pi * np.cumsum(f0s) / sr
        pan = 0.0 if players == 1 else prng.uniform(-0.6, 0.6)
        if mono:
            pan = 0.0
        one = mono or (p['width'] == 0 and pan == 0)       # both channels would be the same: compute one
        te = np.maximum(tc - delay, 0)
        yl = np.zeros(n)
        yr = yl if one else np.zeros(n)
        tot_env = np.zeros(ncs)
        tot0 = np.zeros(ncs)                              # the same partials without the random parts
        # the whole note swells and dips slowly (bow pressure, breath), fading in after the attack
        swell_db = (mix('swell') * p['swell'] * _smooth_noise(prng, ncs, 0.8, sr_c) *
                    np.clip(te / 0.4, 0, 1)) if kind == 'sustained' else 0.0
        for i, k in enumerate(kk):
            if weak[i]:
                continue
            # body at the partial's current frequency: vibrato moves it across the resonances
            fi = k * stretch[i] * f0c
            lvl = base[i] + np.interp(np.log2(fi), body_hz, body_db)
            if kind == 'sustained':
                env_db = _env_sustained(te, atk[i], ov[i], gate, rel[i])
                env0 = _env_sustained(te, atk0[i], ov[i], gate, rel[i])
                env_db += shim[i] * p['variation'] * _smooth_noise(prng, ncs, 6.0, sr_c) + swell_db
            else:
                damp_t = (max(p['damp'] * p['release'] * (1 + 200 / fk[i]), 0.01) if p['damp'] is not None
                          else None)
                env_db = _env_decaying(te, atk[i], t1[i], t2[i], gate, damp_t)
                env0 = _env_decaying(te, atk0[i], t1[i], t2[i], gate, damp_t)
            tot0 += 10 ** ((lvl0[i] - bvar[i] + env0) / 10)   # at the nominal pitch: no vibrato, no drift
            amp_c = 10 ** ((lvl + env_db) / 20)
            if amp_c.max() < 1e-5:
                continue
            tot_env += amp_c ** 2
            # compute the partial only while it sounds: a long tail after the release is mostly silence
            m = min(n, (int(np.nonzero(amp_c >= 1e-5)[0][-1]) + 2) * CTRL)
            amp = np.interp(ts[:m], tc, amp_c)
            ph0 = prng.uniform(0, 2 * np.pi)
            dphi = p['width'] * prng.uniform(-np.pi / 2, np.pi / 2) if k > 1 else 0.0
            dl = 1 + p['width'] * prng.uniform(-0.12, 0.12)
            if mono:
                dphi, dl = 0.0, 1.0
            th = k * stretch[i] * phase[:m] + ph0
            if kind != 'sustained' and bhz[i] > 0 and bdb[i] > 0.5:
                # a second unison string, detuned by the measured beat rate: the partial swells and dips
                g = 10 ** (bdb[i] / 20)
                # struck together, the strings start in phase: the first cancellation comes 1/(2*beat) s later,
                # the dip a listener hears as the note "fading out in the middle"
                th2 = th + 2 * np.pi * bhz[i] * ar[:m] / sr
                mg = (g - 1) / (g + 1)
                yl[:m] += amp * (np.sin(th) + mg * np.sin(th2)) / np.sqrt(1 + mg * mg) * dl
                if not one:
                    yr[:m] += amp * (np.sin(th + dphi) + mg * np.sin(th2 + dphi * 1.3)) / np.sqrt(1 + mg * mg) / dl
            else:
                yl[:m] += amp * np.sin(th) * dl
                if not one:
                    yr[:m] += amp * np.sin(th + dphi) / dl
            if smear[i] > 0.02:
                # the noise skirt: the line's own amplitude wobbling fast and randomly, so noise clusters around
                # the harmonic the way bow and breath noise does
                spec = np.fft.rfft(prng.standard_normal(m))
                spec[np.fft.rfftfreq(m, 1 / sr) > _skirt_bw(freq)] = 0
                nz = np.fft.irfft(spec, m)
                nz /= np.sqrt(np.mean(nz ** 2)) + 1e-12
                sk = amp * smear[i] * nz
                yl[:m] += sk * np.sin(th + 1.1) * dl
                if not one:
                    yr[:m] += sk * np.sin(th + 1.1 + dphi) / dl
        gl, gr = np.sqrt(0.5 * (1 - pan)), np.sqrt(0.5 * (1 + pan))
        out[0] += yl * gl * np.sqrt(2)
        out[1] += yr * gr * np.sqrt(2)
        phi_c = tot_env if phi_c is None else phi_c + tot_env
        phi0 = tot0 if phi0 is None else phi0 + tot0

    # how far the random parts moved the note's level while the key is down (_post bounds it, with the room's)
    held = max(1, int(np.searchsorted(tc, max(gate, 0.05))))
    e_var, e_0 = float(np.sum(phi_c[:held])), float(np.sum(phi0[:held]))
    dev_db = 10 * np.log10(e_var / e_0) if e_var > 0 and e_0 > 0 else 0.0

    # noise: sustained part follows the harmonic envelope, attack part is a burst at the onset
    nz_hz = np.log2(np.array(profile['noise_hz']))
    ns = np.array(a['noise_sus']) * (1 - w) + np.array(b['noise_sus']) * w + p['noise']
    na = np.array(a['noise_atk']) * (1 - w) + np.array(b['noise_atk']) * w + p['noise']
    harm_db = 10 * np.log10(phi_c / players + 1e-15)
    ref_db = harm_db.max()
    if kind == 'sustained':
        sus_env = 10 ** ((harm_db - ref_db) / 20)
    else:
        sus_env = 10 ** ((harm_db - ref_db) / 20)
    atk_t = max(float(np.mean(atk[:3])), 0.01)
    atk_env = np.exp(-np.maximum(tc, 0) / (0.5 * atk_t + 0.015))
    fr, lf = _freq_grid(n, sr)
    layers = [(ns, sus_env, ref_db)]
    if not ('atk_tf' in a and 'atk_tf' in b):       # the calibrated attack map replaces the generic burst
        layers.append((na, atk_env, ref_db))
    for spec_db, envc, level_ref in layers:
        g = 10 ** ((np.interp(lf, nz_hz, spec_db, left=-150, right=-150) + level_ref) / 20)
        g[fr < 20] = 0
        for ch in range(1 if mono else 2):
            wn = rng.standard_normal(n)
            shaped = np.fft.irfft(np.fft.rfft(wn) * g, n)
            out[ch] += shaped * np.interp(ts, tc, envc)
        if mono:
            out[1] = out[0]
    if 'atk_tf' in a and 'atk_tf' in b and p['knock'] > 0:
        out += _attack_noise(a, b, w, rng, n, sr, p['knock'], _rms_window(out, a['kind'], sr))
    out /= players ** 0.5
    return _post(out, profile, p, sr, seed, int(max(gate, 0.05) * sr), dev_db)


def _env_sustained(te, atk, ov, gate, rel):
    """dB envelope of a sustained partial: an S-shaped rise (the player eases in, no corner), the measured
    overshoot, the release after the gate."""
    x = np.clip(te / atk, 0, 1)
    up = x * x * (3 - 2 * x)
    env = 20 * np.log10(up + 1e-4) + ov * np.exp(-np.maximum(te - atk, 0) / 0.08) * (te >= atk * 0.5)
    return env - 60 * np.maximum(te - gate, 0) / rel


def _env_decaying(te, atk, t1, t2, gate, damp_t):
    """dB envelope of a decaying partial: a rise, the prompt decay for 20 dB then the after decay, and the damper
    (damp_t seconds per 60 dB, None = ring on) after the gate."""
    up = np.clip(te / atk, 0, 1)
    d = np.maximum(te - atk, 0)
    tb = t1 / 3.0                                       # 20 dB into the prompt decay
    env = 20 * np.log10(up + 1e-4) - np.where(d < tb, 60 * d / t1, 20 + 60 * (d - tb) / t2)
    if damp_t is not None:
        env = env - 60 * np.maximum(te - gate, 0) / damp_t
    return env


_FREQ_GRIDS = {}


def _freq_grid(n, sr):
    """rfft bin frequencies of an n-sample note and their log2 (floored at 1 Hz): the notes of a chord share n."""
    v = _FREQ_GRIDS.get((n, sr))
    if v is None:
        fr = np.fft.rfftfreq(n, 1 / sr)
        v = (fr, np.log2(np.maximum(fr, 1.0)))
        if len(_FREQ_GRIDS) > 32:
            _FREQ_GRIDS.clear()
        _FREQ_GRIDS[(n, sr)] = v
    return v


def _hann(nfft):
    """Periodic Hann window (scipy get_window('hann', nfft), as librosa's stft uses)."""
    return 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(nfft) / nfft)


def _stft(x, nfft, hop, win):
    """librosa.stft(x, n_fft=nfft, hop_length=hop, center=True) with zero padding, as (frames, bins). numpy only:
    librosa's first stft in a process costs seconds (lazy imports, numba), and that landed on a deck's first bars."""
    xp = np.pad(x, nfft // 2)
    nfr = 1 + (len(xp) - nfft) // hop
    frames = np.lib.stride_tricks.sliding_window_view(xp, nfft)[::hop][:nfr]
    return np.fft.rfft(frames * win, axis=1)


def _istft(Z, nfft, hop, win, length):
    """librosa.istft(Z.T, hop_length=hop, n_fft=nfft, center=True, length=length) for (frames, bins) Z."""
    pad = nfft // 2
    nfr = min(len(Z), int(np.ceil((length + 2 * pad) / hop)))
    frames = np.fft.irfft(Z[:nfr], n=nfft, axis=1) * win
    y = np.zeros(nfft + hop * (nfr - 1))
    wss = np.zeros_like(y)
    w2 = win ** 2
    for j in range(nfr):
        y[j * hop:j * hop + nfft] += frames[j]
        wss[j * hop:j * hop + nfft] += w2
    y, wss = y[pad:pad + length], wss[pad:pad + length]
    if len(y) < length:
        y, wss = np.pad(y, (0, length - len(y))), np.pad(wss, (0, length - len(wss)))
    nz = wss > np.finfo(wss.dtype).tiny
    y[nz] /= wss[nz]
    return y


def _rms_window(out, kind, sr):
    t0, t1 = (0.3, 2.0) if kind == 'sustained' else (0.03, 1.0)
    seg = out.mean(axis=0)[int(t0 * sr):int(t1 * sr)]
    return float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0


def _attack_noise(a, b, w, rng, n, sr, gain, rms_now):
    """The attack map as STFT-shaped noise. Levels were measured with the recording at atk_ref_db rms over the
    calibration window; scale to this render's rms there."""
    ta = 10 ** (np.array(a['atk_tf']) / 10)
    tb = 10 ** (np.array(b['atk_tf']) / 10)
    var = ta * (1 - w) + tb * w                            # variance density per band x frame
    ref = 10 ** ((a['atk_ref_db'] * (1 - w) + b['atk_ref_db'] * w) / 20)
    k = (rms_now / ref) ** 2 if ref > 0 else 0.0
    fr = np.fft.rfftfreq(ATK_NFFT, 1 / sr)
    edges = np.concatenate([[NOISE_HZ[0] / 2 ** (1 / 6)], np.sqrt(NOISE_HZ[:-1] * NOISE_HZ[1:]),
                            [NOISE_HZ[-1] * 2 ** (1 / 6)]])
    band = np.clip(np.searchsorted(edges, fr) - 1, 0, len(NOISE_HZ) - 1)   # each bin reads its own band, as measured
    G = np.sqrt(var[band, :] * k) * gain
    G[fr < 20] = 0
    # centred frames (padded, so no near-zero window sum at the edges); analysis frame j (not centred) has its
    # centre at j*hop + nfft/2, which is centred frame j + nfft/(2*hop)
    sh = ATK_NFFT // (2 * ATK_HOP)
    need = ATK_NFFT + ATK_HOP * (ATK_FRAMES - 1)
    res = np.zeros((2, n))
    win = _hann(ATK_NFFT)
    for ch in range(2):
        wn = rng.standard_normal(need + ATK_NFFT)
        Z = _stft(wn, ATK_NFFT, ATK_HOP, win)               # (frames, bins)
        Gc = np.zeros((Z.shape[0], G.shape[0]))
        k = max(0, min(G.shape[1], Z.shape[0] - sh))
        Gc[sh:sh + k] = G[:, :k].T
        z = _istft(Z * Gc, ATK_NFFT, ATK_HOP, win, len(wn))
        m = min(n, need)
        res[ch, :m] = z[:m]
    return res


def _resonators(x, freqs, t60s, gains, sr):
    """Sum of 2-pole resonators (bandpass, peak gain ~ gains) driven by x."""
    from scipy.signal import lfilter
    y = np.zeros_like(x)
    for f, t60, g in zip(freqs, t60s, gains):
        if f >= sr / 2 * 0.95:
            continue
        r = 10 ** (-3 / (t60 * sr))
        th = 2 * np.pi * f / sr
        b = [(1 - r) * g, 0, -(1 - r) * g]
        a = [1, -2 * r * np.cos(th), r * r]
        y += lfilter(b, a, x)
    return y


def _room_ir(rt, sr, rng):
    """Stereo decorrelated exponentially decaying noise, highs dying faster, 12 ms pre-delay, a few early taps."""
    n = int(min(rt * 1.2, 4.0) * sr)
    t = np.arange(n) / sr
    ir = np.zeros((2, n))
    fr = np.fft.rfftfreq(n, 1 / sr)
    for ch in range(2):
        nz = rng.standard_normal(n)
        # frequency-dependent decay: split into 3 bands with RT scaled 1.2 / 1.0 / 0.6
        spec = np.fft.rfft(nz)
        out = np.zeros(n)
        for lo, hi, sc in ((0, 500, 1.2), (500, 4000, 1.0), (4000, sr / 2 + 1, 0.6)):
            band = np.fft.irfft(spec * ((fr >= lo) & (fr < hi)), n)
            out += band * np.exp(-6.91 * t / (rt * sc))
        ir[ch] = out
    pre = int(0.012 * sr)
    ir = np.concatenate([np.zeros((2, pre)), ir[:, :n - pre]], axis=1)
    for d, g in ((0.007, 0.5), (0.013, 0.35), (0.021, 0.3), (0.029, 0.22)):
        ir[int(rng.integers(0, 2)), int(d * sr)] += g * 40 / np.sqrt(n)
    return ir / np.sqrt((ir ** 2).sum() / 2)


def _post(out, profile, p, sr, seed, held_n=None, dev_db=0.0):
    """Resonators, room, mono. held_n: samples while the key is down; dev_db: how far the random parts moved the
    note's level there. The level move with the room's is held within +-LEVEL_CAP_DB / 2."""
    rng = np.random.default_rng(seed + 17)
    n = out.shape[1]
    mono = out.mean(axis=0)
    add = np.zeros_like(out)
    if p['ring'] is not None:
        hz = np.array(profile['body_hz'])
        body = np.array(profile['body_db'])
        pk = [i for i in range(1, len(body) - 1) if body[i] > body[i - 1] and body[i] >= body[i + 1] and body[i] > 3]
        if pk:
            fs = hz[pk]
            t60 = np.array([p['ring_q'] * 2.2 / (np.pi * f) * 3 for f in fs])     # T60 of a mode with that Q
            g = 10 ** ((body[pk] - body[pk].max()) / 20) * 10 ** (p['ring'] / 20)
            r = _resonators(mono, fs, t60, g, sr)
            add += np.stack([r, r * 0.9])
    if p['strings']:
        import librosa
        fs, ts, gs = [], [], []
        for s in p['strings']:
            f = librosa.note_to_hz(s) if isinstance(s, str) else 440.0 * 2 ** ((float(s) - 69) / 12)
            for m in range(1, 25):
                if m * f < min(sr / 2 * 0.9, 12000):
                    fs.append(m * f)
                    ts.append(p['string_t60'] / (1 + 0.15 * (m - 1)))
                    gs.append(1.0 / m ** 0.5)
        r = _resonators(mono, fs, ts, np.array(gs) * 10 ** (p['sympathy'] / 20), sr)
        add += np.stack([r * 0.8, r])
    out = out + add
    room = p['room'] if p['room'] is not None else profile.get('room_rt', 0.0)
    if room and room > 0:
        from scipy.signal import fftconvolve
        ir = _room_ir(room, sr, rng)
        if p['mono']:
            w = fftconvolve(out.mean(axis=0), ir[0])[:n]
            wet = np.stack([w, w])
        else:
            wet = np.stack([fftconvolve(out[0], ir[0])[:n], fftconvolve(out[1], ir[1])[:n]])
        g = 10 ** (p['room_mix'] / 20)
        # a steady tone meets one random complex gain of the IR, so dry and wet sum in or out of phase: count how
        # far that moves the level from dry and wet adding in power
        h = slice(0, held_n or n)
        e_dry = float(np.sum(out[:, h] ** 2))
        out = out + wet * g
        e_tot = float(np.sum(out[:, h] ** 2))
        if e_dry > 0 and e_tot > 0:
            dev_db += 10 * np.log10(e_tot / (e_dry * (1 + g * g)))
    if abs(dev_db) > LEVEL_CAP_DB / 2:      # the timbre and the room's colour stay; only the level is pulled in
        out = out * 10 ** ((np.clip(dev_db, -LEVEL_CAP_DB / 2, LEVEL_CAP_DB / 2) - dev_db) / 20)
    if p['mono']:
        out = np.repeat(out.mean(axis=0, keepdims=True), 2, axis=0)
    return out
