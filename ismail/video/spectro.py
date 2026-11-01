"""Spectrogram videos with a playhead: a song's spectrogram, time running left to right, and a glowing line at the
play position whose glow follows the music under it (Nate 11-01, ledger:M426: "a single vertical line up the spectro
... bloomy white-blue ... the bloom would exaggerate on the brighter parts").

Two modes:
- 'static': the whole clip's spectrogram fills the frame and the line sweeps across it, start to end.
- 'scroll': the spectrogram moves right to left under a fixed line (at `at`, a share of the width), `window_s`
  seconds in view.

The colours follow the stream's cqt look (ffmpeg showcqt): log frequency, low at the bottom, the left channel's
energy in `cscheme[0:3]`, the right's in `cscheme[3:6]`, so a centred sound is white and a panned one leans orange or
blue. The glow is a screen blend over the picture: a `line_color` core `line_width` px wide and a soft `halo_color` halo of
`bloom_px` px; the halo's strength is `glow * (1 + bloom_gain * k)` and its width `bloom_px * sqrt(1 + bloom_gain * k)`
(bloom_gain 0: the same glow everywhere), where k is how bright the column under the line is
(0 for the clip's quietest columns, 1 for its brightest, smoothed over `smooth_s`); `accent` adds glow where the
pixel under the halo is itself bright, so bright notes flare along the line.

    python -m ismail.video spectro <audio> <out.mp4> [--mode scroll] [--start 30 --end 90] [--still 42.5]
"""
import os
import subprocess

import numpy as np
import soundfile as sf

from .. import settings

DEFAULTS = dict(mode='static', width=1920, height=1080, fps=30, start=0.0, end=None, window_s=12.0, at=0.5,
                fmin=30.0, fmax=16000.0, range_db=48.0, gamma=1.8, cscheme=(1, 0.5, 0, 0, 0.5, 1),
                line_color='#f2f9ff', halo_color='#5aaeff', line_width=3, bloom_px=26, glow=0.55, bloom_gain=1.6, accent=0.9,
                smooth_s=0.12, crf=18, audio=True)
N_FFT = 4096


def _rgb(c):
    c = c.lstrip('#')
    if len(c) != 6:
        raise ValueError(f'colour {c!r}: a hex colour like #5aaeff')
    return np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], dtype=np.float32)


def _rows(sr, height, fmin, fmax):
    """Weights from FFT bins to `height` rows on a log frequency axis (row 0 is the top, fmax). A row wider than two
    bins averages them (triangular); a narrower one (the lows) interpolates between the two bins around its centre,
    so the bass is a smooth gradient, not steps. Each row is then tilted by its frequency (+3 dB an octave), as a
    constant-Q transform hears it, so the highs are not buried under the bass."""
    nb = N_FFT // 2 + 1
    bin_hz = sr / N_FFT
    fb = np.arange(nb) * bin_hz
    edges = np.geomspace(fmin, min(fmax, sr / 2 * 0.98), height + 2)
    W = np.zeros((height, nb), dtype=np.float32)
    for r in range(height):
        lo, mid, hi = edges[r], edges[r + 1], edges[r + 2]
        if hi - lo < 2 * bin_hz:
            j = mid / bin_hz
            j0 = min(int(j), nb - 2)
            W[r, j0], W[r, j0 + 1] = 1 - (j - j0), j - j0
        else:
            w = np.clip(np.minimum((fb - lo) / (mid - lo), (hi - fb) / (hi - mid)), 0, None)
            W[r] = w / w.sum()
        W[r] *= mid / 1000.0
    return W[::-1]


def spectrogram(y, sr, px_per_s, height, fmin, fmax):
    """Power per (row, column) for each channel of y (channels, samples): `px_per_s` columns a second. Each column
    is the mean power of the FFT frames inside it (frames every N_FFT/4 samples or closer), so short hits are kept."""
    hop = int(max(64, min(N_FFT // 4, sr / px_per_s / 2)))
    n_cols = max(1, int(round(y.shape[1] / sr * px_per_s)))
    W = _rows(sr, height, fmin, fmax)
    win = np.hanning(N_FFT).astype(np.float32)
    pad = np.pad(y, ((0, 0), (N_FFT // 2, N_FFT)))
    starts = np.arange(0, y.shape[1], hop)
    col = np.minimum((starts / sr * px_per_s).astype(int), n_cols - 1)
    out = np.zeros((y.shape[0], height, n_cols), dtype=np.float32)
    cnt = np.bincount(col, minlength=n_cols).astype(np.float32)
    for c in range(y.shape[0]):
        for i in range(0, len(starts), 512):
            s = starts[i:i + 512]
            fr = np.stack([pad[c, a:a + N_FFT] for a in s]) * win
            p = (np.abs(np.fft.rfft(fr, axis=1)) ** 2).astype(np.float32)
            rows = p @ W.T                                   # frames x height
            np.add.at(out[c].T, col[i:i + 512], rows)
    return out / np.maximum(cnt, 1)


def picture(P, range_db, gamma, cscheme):
    """Brightness (channels, rows, cols) in 0..1 and the rgb picture (rows, cols, 3) float32 from power P."""
    db = 10 * np.log10(P + 1e-12)
    top = np.percentile(db, 99.7)
    B = np.clip((db - (top - range_db)) / range_db, 0, 1) ** gamma
    cs = np.asarray(cscheme, dtype=np.float32)
    L, R = B[0], B[-1]
    img = L[..., None] * cs[:3] + R[..., None] * cs[3:6]
    return B.mean(axis=0), np.clip(img, 0, 1)


def _smooth(k, n):
    if n <= 1:
        return k
    ker = np.hanning(2 * n + 1)
    return np.convolve(np.pad(k, n, mode='edge'), ker / ker.sum(), mode='valid')


def column_drive(B, smooth_cols):
    """k per column: 0 at the clip's quiet columns (10th percentile of column brightness), 1 at its bright ones
    (98th), smoothed."""
    colb = B.mean(axis=0)
    lo, hi = np.percentile(colb, 10), np.percentile(colb, 98)
    k = np.clip((colb - lo) / max(hi - lo, 1e-6), 0, 1)
    return _smooth(k, smooth_cols)


def glow_strip(B_col_region, x_off, k, o):
    """The glow over a strip of columns: (core, halo) strengths (rows, cols), 0..1, for the line at x_off (float,
    in strip columns). The core is a hard line, the halo a gaussian; both grow with k, and the halo grows where the
    picture itself is bright (accent)."""
    h, w = B_col_region.shape
    dx = np.arange(w, dtype=np.float32) - x_off
    lift = o['glow'] * (1 + o['bloom_gain'] * k)
    sig = o['bloom_px'] * np.sqrt(1 + o['bloom_gain'] * k)
    halo = np.exp(-0.5 * (dx / max(sig, 1e-3)) ** 2)
    core = np.clip(o['line_width'] / 2 + 0.5 - np.abs(dx), 0, 1)
    halo = np.clip(halo[None, :] * lift * (0.35 + o['accent'] * B_col_region), 0, 1)
    core = np.broadcast_to(core[None, :] * min(1.0, 0.75 + 0.5 * lift), (h, w))
    return core, halo


def render(audio, out, **kw):
    """Render the video (or, with still=<seconds>, one png frame) and return a short report."""
    still = kw.pop('still', None)
    o = dict(DEFAULTS)
    bad = set(kw) - set(o)
    if bad:
        raise ValueError(f'unknown options {sorted(bad)}; known: {sorted(o)}')
    o.update({k: v for k, v in kw.items() if v is not None})
    if o['mode'] not in ('static', 'scroll'):
        raise ValueError(f"mode {o['mode']!r}: 'static' (the line sweeps the whole clip) or 'scroll' "
                         "(the picture moves under a fixed line)")
    Wd, Ht, fps = int(o['width']), int(o['height']), float(o['fps'])
    y, sr = sf.read(audio, dtype='float32', always_2d=True)
    t0 = float(o['start'])
    t1 = float(o['end']) if o['end'] is not None else y.shape[0] / sr
    if not 0 <= t0 < t1:
        raise ValueError(f'start {t0} / end {t1}: need 0 <= start < end (the file is {y.shape[0] / sr:.1f} s)')
    dur = t1 - t0
    if o['mode'] == 'static':
        px_per_s, lead = Wd / dur, 0
    else:
        px_per_s = Wd / float(o['window_s'])
        lead = int(Wd * float(o['at']))          # columns of black before t0 so the line starts on the first sound
    seg = y[int(t0 * sr):int(t1 * sr)].T
    P = spectrogram(seg, sr, px_per_s, Ht, o['fmin'], o['fmax'])
    B, img = picture(P, o['range_db'], o['gamma'], o['cscheme'])
    k_col = column_drive(B, int(round(o['smooth_s'] * px_per_s)))
    if o['mode'] == 'scroll':
        tail = Wd - lead
        B = np.pad(B, ((0, 0), (lead, tail)))
        img = np.pad(img, ((0, 0), (lead, tail), (0, 0)))
        k_col = np.pad(k_col, (lead, tail))
    color, halo_rgb = _rgb(o['line_color']), _rgb(o['halo_color'])
    reach = int(o['bloom_px'] * 1.4 * 3) + int(o['line_width']) + 2
    n_frames = max(1, int(round(dur * fps)))

    def frame(i):
        t = i / fps
        if o['mode'] == 'static':
            x = t * px_per_s
            view0 = 0
        else:
            x = lead + t * px_per_s                  # the play position in the padded picture
            view0 = int(round(x)) - lead
        base = img[:, view0:view0 + Wd] if o['mode'] == 'scroll' else img
        fr = base.copy()
        xi = x - view0
        a, b = max(0, int(xi) - reach), min(Wd, int(xi) + reach + 1)
        if a < b:
            k = float(k_col[min(int(x), len(k_col) - 1)])
            core, halo = glow_strip(B[:, view0 + a:view0 + b], xi - a, k, o)
            light = np.clip(halo[..., None] * halo_rgb + core[..., None] * color, 0, 1)
            fr[:, a:b] = 1 - (1 - fr[:, a:b]) * (1 - light)                             # screen blend
        return (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)

    ff = settings.get('ffmpeg')
    if not ff:
        raise RuntimeError("ffmpeg not found: install it (it must be on PATH) or name its path in the 'ffmpeg' "
                           "setting (ISMAIL_FFMPEG); spectro writes every frame through it")
    if still is not None:
        i = min(n_frames - 1, max(0, int(round((float(still) - t0) * fps))))
        cmd = [ff, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Wd}x{Ht}',
               '-i', '-', '-frames:v', '1', out]
        subprocess.run(cmd, input=frame(i).tobytes(), check=True)
        return f'{out}: frame {i} ({t0 + i / fps:.2f} s), {o["mode"]}, {Wd}x{Ht}'
    cmd = [ff, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Wd}x{Ht}',
           '-r', f'{fps:g}', '-i', '-']
    if o['audio']:
        cmd += ['-ss', f'{t0:.3f}', '-t', f'{dur:.3f}', '-i', audio, '-map', '0:v', '-map', '1:a', '-c:a', 'aac',
                '-b:a', '320k']
    cmd += ['-c:v', 'libx264', '-preset', 'medium', '-crf', str(o['crf']), '-pix_fmt', 'yuv420p', '-shortest', out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n_frames):
            p.stdin.write(frame(i).tobytes())
    finally:
        p.stdin.close()
        rc = p.wait()
    if rc:
        raise RuntimeError(f'ffmpeg exit {rc} writing {out}')
    return (f'{out}: {dur:.1f} s, {n_frames} frames at {fps:g} fps, {o["mode"]}, {Wd}x{Ht}, '
            f'{px_per_s:.1f} px a second' + (', with audio' if o['audio'] else ''))
