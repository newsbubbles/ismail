"""Spectrogram videos with a playhead: a song's spectrogram, time running left to right, and a glowing line at the
play position whose glow follows the music under it (Nate 11-01, ledger:M426: "a single vertical line up the spectro
... bloomy white-blue ... the bloom would exaggerate on the brighter parts").

Two modes, and a camera path:
- 'static': the whole clip's spectrogram fills the frame and the line sweeps across it, start to end.
- 'scroll': the spectrogram moves right to left under a fixed line (at `at`, a share of the width), `window_s`
  seconds in view.
- `camera=[(t_s, window_s), ...]` (supersedes mode; Nate 11-02, ledger:M430: "show the full song at first ... then
  go into that higher zoom ... keeping it rolling, with the line in the middle"): at each key's time (file
  seconds, like start and end) `window_s` seconds are in view ('full' or None: the whole clip). Between keys the
  window eases (smoothstep, in log size) over the whole gap, or over the last `ease_s` seconds before the key
  (ease_s 0: the whole gap). The view never shows past the clip's ends: at the start the line rides in from the
  left edge until it reaches `at`, then the picture rolls under it; near the end the picture stops and the line
  drifts on to the right edge. The view is a pure function of the time, so the line never jumps. CLI:
  `--camera "0:full,4:12,200:12,210:full"`.

Extras the music drives (camera only, each 0 = off):
- `minimap`: a strip that many px high along the bottom with the whole clip's spectrogram and a box for the window
  in view; it fades in as the view leaves the full clip (fully shown at half the clip or tighter).
- `zoom_follow` (0..1, or keys "t:depth,..." for when): the window breathes with the loudness, smoothed over `follow_s`: wider when loud, tighter
  when quiet, up to 2**zoom_follow times either way, on top of the camera keys (none on a full view).
- `kick_pulse` (0..1, or keys "t:depth,..." for when, e.g. only the climaxes): on each low-band onset (below `kick_hz`, from the spectrogram's low rows) the picture punches
  in about the line by up to 5% and brightens by up to 35%, decaying over `kick_decay_s`.

The colours follow the stream's cqt look (ffmpeg showcqt): log frequency, low at the bottom, the left channel's
energy in `cscheme[0:3]`, the right's in `cscheme[3:6]`, so a centred sound is white and a panned one leans orange or
blue. The glow is a screen blend over the picture: a `line_color` core `line_width` px wide and a soft `halo_color` halo of
`bloom_px` px; the halo's strength is `glow * (1 + bloom_gain * k)` and its width `bloom_px * sqrt(1 + bloom_gain * k)`
(bloom_gain 0: the same glow everywhere), where k is how bright the column under the line is
(0 for the clip's quietest columns, 1 for its brightest, smoothed over `smooth_s`); `accent` adds glow where the
pixel under the halo is itself bright, so bright notes flare along the line.

The camera's spectrogram is computed once, at the tightest window's px a second, with halved copies (mean power of
column pairs) for the wider views; a frame takes the nearest columns of the copy just finer than the screen.
`preset` is libx264's (medium; fast or veryfast encode quicker for a little larger file).

    python -m ismail.video spectro <audio> <out.mp4> [--mode scroll] [--start 30 --end 90] [--still 42.5]
    python -m ismail.video spectro <audio> <out.mp4> --camera "0:full,4:12,200:12,210:full" --minimap 72 --kick_pulse 0.6
    python -m ismail.video spectro <audio> <out.mp4> --camera "0:full,6:27.4,27:13.7,55:6.9,82:13.7,106:3.4,110:6.9:snap,137:1.7,165:27.4,294:full" --at "0:0.5,82:0.5,108:0.35,110:0.5:snap" --kick_pulse "0:0,110:0,110.1:0.7,124:0.7,126:0"
    (a song tells its story in many keys: a change every 4 to 16 bars; music-video.md, "Camera craft")
"""
import queue
import subprocess
import threading

import numpy as np
import soundfile as sf

from .. import settings

DEFAULTS = dict(mode='static', camera=None, ease_s=0.0, snap_s=0.12, width=1920, height=1080, fps=30, start=0.0, end=None,
                window_s=12.0, at=0.5, fmin=30.0, fmax=16000.0, range_db=48.0, gamma=1.8, cscheme=(1, 0.5, 0, 0, 0.5, 1),
                line_color='#f2f9ff', halo_color='#5aaeff', line_width=3, bloom_px=26, glow=0.55, bloom_gain=1.6, accent=0.9,
                smooth_s=0.12, minimap=0, zoom_follow=0.0, follow_s=4.0, kick_pulse=0.0, kick_hz=150.0,
                kick_decay_s=0.15, crf=18, preset='medium', audio=True)
N_FFT = 4096
KICK_SCALE, KICK_LIFT = 0.05, 0.35        # kick_pulse 1: a 5% punch-in, 35% brighter


def _rgb(c):
    c = c.lstrip('#')
    if len(c) != 6:
        raise ValueError(f'colour {c!r}: a hex colour like #5aaeff')
    return np.array([int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)], dtype=np.float32)


def _edges(sr, height, fmin, fmax):
    return np.geomspace(fmin, min(fmax, sr / 2 * 0.98), height + 2)


def _rows(sr, height, fmin, fmax):
    """Weights from FFT bins to `height` rows on a log frequency axis (row 0 is the top, fmax). A row wider than two
    bins averages them (triangular); a narrower one (the lows) interpolates between the two bins around its centre,
    so the bass is a smooth gradient, not steps. Each row is then tilted by its frequency (+3 dB an octave), as a
    constant-Q transform hears it, so the highs are not buried under the bass."""
    nb = N_FFT // 2 + 1
    bin_hz = sr / N_FFT
    fb = np.arange(nb) * bin_hz
    edges = _edges(sr, height, fmin, fmax)
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
            cc = col[i:i + 512]                              # rising, so sum each column's run of frames at once
            first = np.flatnonzero(np.r_[True, cc[1:] != cc[:-1]])
            out[c][:, cc[first]] += np.add.reduceat(rows, first, axis=0).T
    return out / np.maximum(cnt, 1)


def picture(P, range_db, gamma, cscheme, top=None):
    """Brightness (channels, rows, cols) in 0..1 and the rgb picture (rows, cols, 3) float32 from power P. `top`
    (dB) is the level shown full bright, by default the clip's 99.7th percentile."""
    db = 10 * np.log10(P + 1e-12)
    if top is None:
        top = np.percentile(db, 99.7)
    B = np.clip((db - (top - range_db)) / range_db, 0, 1) ** gamma
    cs = np.asarray(cscheme, dtype=np.float32)
    L, R = B[0], B[-1]
    img = L[..., None] * cs[:3] + R[..., None] * cs[3:6]
    return B.mean(axis=0), np.clip(img, 0, 1)


def _u8(a):
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


def picture8(P, range_db, gamma, cscheme, top=None):
    """picture() as uint8 (brightness (rows, cols), rgb (rows, cols, 3)), a few thousand columns at a time so a long
    clip's float copies stay small, and the `top` it used. A long clip's top is taken from every few columns."""
    if top is None:
        step = max(1, P.shape[2] // 8192)
        top = float(np.percentile(10 * np.log10(P[..., ::step] + 1e-12), 99.7))
    n = P.shape[2]
    B8 = np.empty(P.shape[1:], np.uint8)
    img8 = np.empty(P.shape[1:] + (3,), np.uint8)
    for a in range(0, n, 4096):
        b, im = picture(P[..., a:a + 4096], range_db, gamma, cscheme, top)
        B8[:, a:a + 4096], img8[:, a:a + 4096] = _u8(b), _u8(im)
    return B8, img8, top


def _halve(P):
    """Mean power of column pairs (an odd last column stays as it is)."""
    m = P.shape[2] // 2
    h = (P[..., 0:2 * m:2] + P[..., 1:2 * m:2]) * 0.5
    return np.concatenate([h, P[..., 2 * m:]], axis=2) if P.shape[2] % 2 else h


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


def _split_keys(s, what, example):
    """'t:value[:snap],...' or [(t, value[, 'snap']), ...] as [(t, value, shape)], shape 'ease' or 'snap'."""
    try:
        ks = [p.split(':') for p in s.replace(' ', '').split(',') if p] if isinstance(s, str) else list(s)
        out = []
        for k in ks:
            if not 2 <= len(k) <= 3 or (len(k) == 3 and str(k[2]).lower() not in ('ease', 'snap')):
                raise ValueError
            out.append((float(k[0]), k[1], str(k[2]).lower() if len(k) == 3 else 'ease'))
    except (ValueError, TypeError):
        raise ValueError(f'{what} {s!r}: keys like "{example}" (seconds:value, and :snap on a key to arrive '
                         'there fast)') from None
    return out


def parse_camera(s):
    """'0:full,4:12,200:12,210:full' (seconds:window_s, 'full' = the whole clip; a third field 'snap' arrives at
    that key in the last snap_s instead of easing over the gap, e.g. '64:4:snap' on a drop) or a list of pairs, as
    [(t_s, window_s or None), ...] sorted by time (a snap key as (t_s, window_s, 'snap'))."""
    keys = []
    for t, w, shape in _split_keys(s, 'camera', '0:full,4:12,200:12'):
        w = None if w is None or str(w).lower() == 'full' else float(w)
        if w is not None and w <= 0.05:
            raise ValueError(f'camera key {t}:{w}: a window is more than 0.05 s (or full)')
        keys.append((t, w) if shape == 'ease' else (t, w, shape))
    if not keys:
        raise ValueError('camera: at least one key, e.g. "0:full,4:12"')
    return sorted(keys, key=lambda k: k[0])


def keyed(v, t, t0=0.0):
    """A depth at clip times t (array): v is a number (all through), or keys "t:value,..." / [(t_s, value), ...] in
    file seconds like the camera's, linear between keys and held past the first and last (so "0:0,180:0,182:0.6,
    200:0.6,202:0" is on only from 182 to 200 s, with 2 s ramps)."""
    t = np.asarray(t, dtype=float)
    if v is None or v == '':
        return np.zeros_like(t)
    if not isinstance(v, (str, list, tuple)):
        return np.full_like(t, float(v))
    if isinstance(v, str) and ':' not in v:
        return np.full_like(t, float(v))
    try:
        ks = [p.split(':') for p in v.replace(' ', '').split(',') if p] if isinstance(v, str) else v
        ks = sorted((float(a) - t0, float(b)) for a, b in ks)
    except ValueError:
        raise ValueError(f'{v!r}: a number or keys like "0:0,180:0,182:0.6" (file seconds:value)') from None
    return np.interp(t, [k[0] for k in ks], [k[1] for k in ks])


def _eased(kt, kv, snap, t, ease_s=0.0, snap_s=0.12):
    """Values at times t: each key's value is reached at its time, easing (smoothstep) over the whole gap from the
    key before, or over its last ease_s (ease_s > 0), or for a snap key over its last snap_s; held before the first
    key and after the last."""
    t = np.asarray(t, dtype=float)
    kt, kv, snap = np.asarray(kt, dtype=float), np.asarray(kv, dtype=float), np.asarray(snap, dtype=bool)
    if len(kt) == 1:
        return np.full_like(t, kv[0])
    j = np.clip(np.searchsorted(kt, t, side='right'), 1, len(kt) - 1)
    a, b = kt[j - 1], kt[j]
    span = b - a if ease_s <= 0 else np.minimum(b - a, ease_s)
    span = np.where(snap[j], np.minimum(b - a, snap_s), span)
    u = np.clip((t - (b - span)) / np.maximum(span, 1e-9), 0, 1)
    u = u * u * (3 - 2 * u)                              # smoothstep
    v = kv[j - 1] + (kv[j] - kv[j - 1]) * u
    return np.where(t < kt[0], kv[0], np.where(t >= kt[-1], kv[-1], v))


def needle(v, t, t0=0.0, ease_s=0.0, snap_s=0.12):
    """The line's place on screen (a share of the width, 0..1) at clip times t: a number all through, or keys
    "t:at[:snap],..." in file seconds, eased like the camera's (so "0:0.5,60:0.35,64:0.5:snap" holds the needle
    back over a build to 60 s and puts it back in place on the drop at 64 s)."""
    t = np.asarray(t, dtype=float)
    if not isinstance(v, (str, list, tuple)) or (isinstance(v, str) and ':' not in v):
        return np.full_like(t, float(v))
    ks = sorted((k[0] - t0, float(k[1]), k[2]) for k in _split_keys(v, 'at', '0:0.5,60:0.35,64:0.5:snap'))
    if any(not 0 <= k[1] <= 1 for k in ks):
        raise ValueError(f'at {v!r}: each value is a share of the width, 0 to 1')
    return _eased([k[0] for k in ks], [k[1] for k in ks], [k[2] == 'snap' for k in ks], t, ease_s, snap_s)


def camera_path(keys, t, dur, ease_s=0.0, at=0.5, snap_s=0.12):
    """The camera at clip times t (array, seconds from the clip's start, 0..dur): (window_s, the view's left edge in
    clip seconds, the line's x as a share of the width), each an array like t. Keys are [(t_s, window_s or None)]
    in clip seconds, sorted; None = the whole clip; a key (t_s, window_s, 'snap') is reached in its last snap_s.
    at: the line's share of the width, a number or an array like t (needle())."""
    t = np.asarray(t, dtype=float)
    kw = np.log(np.array([min(dur, k[1]) if k[1] else dur for k in keys]))
    lw = _eased([k[0] for k in keys], kw, [len(k) > 2 and k[2] == 'snap' for k in keys], t, ease_s, snap_s)
    w = np.minimum(np.exp(lw), dur)
    left = np.clip(t - at * w, 0, dur - w)
    return w, left, (t - left) / w


def _kick_env(B8, rows_low, pps, n_frames, fps, decay_s):
    """0..1 at each frame: low-band onsets (the rise of the low rows' brightness over 50 ms, against the clip's own
    biggest rises), held at their peak and decaying by e every decay_s."""
    e = B8[rows_low].mean(axis=0) / 255
    d = max(1, int(round(0.05 * pps)))
    o = np.maximum(0, e[d:] - e[:-d])
    o = np.r_[np.zeros(d), o]
    o = np.clip((o / max(np.percentile(o, 99.5), 0.1) - 0.2) / 0.8, 0, 1)     # a rise under 0.02 is no kick
    cidx = np.minimum((np.arange(n_frames) / fps * pps).astype(int), len(o) - 1)
    peak = np.maximum.reduceat(o, np.unique(cidx))[np.searchsorted(np.unique(cidx), cidx)]
    env = np.empty(n_frames)
    fall, v = np.exp(-1 / (fps * max(decay_s, 1e-3))), 0.0
    for i in range(n_frames):
        v = max(peak[i], v * fall)
        env[i] = v
    return env


def render(src, out, **kw):
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
    cam = o['camera'] is not None
    if not cam and ':' in str(o['at']):
        raise ValueError('timed at keys work with a camera path (for the whole clip: camera "0:full")')
    extras = [k for k in ('minimap', 'zoom_follow', 'kick_pulse') if o[k]]
    if extras and not cam:
        raise ValueError(f'{", ".join(extras)} work with a camera path (for the whole clip: camera "0:full")')
    Wd, Ht, fps = int(o['width']), int(o['height']), float(o['fps'])
    y, sr = sf.read(src, dtype='float32', always_2d=True)
    t0 = float(o['start'])
    t1 = float(o['end']) if o['end'] is not None else y.shape[0] / sr
    if not 0 <= t0 < t1:
        raise ValueError(f'start {t0} / end {t1}: need 0 <= start < end (the file is {y.shape[0] / sr:.1f} s)')
    dur = t1 - t0
    n_frames = max(1, int(round(dur * fps)))
    ft = np.arange(n_frames) / fps                    # frame times, clip seconds
    lead = 0
    if cam:
        keys = [(k[0] - t0,) + tuple(k[1:]) for k in parse_camera(o['camera'])]
        follow = keyed(o['zoom_follow'], ft, t0)
        w_min = min([min(dur, k[1]) for k in keys if k[1]] or [dur]) * 2 ** -float(follow.max())
        px_per_s = Wd / w_min
    elif o['mode'] == 'static':
        px_per_s = Wd / dur
    else:
        px_per_s = Wd / float(o['window_s'])
        lead = int(Wd * float(o['at']))          # columns of black before t0 so the line starts on the first sound
    seg = y[int(t0 * sr):int(t1 * sr)].T
    P = spectrogram(seg, sr, px_per_s, Ht, o['fmin'], o['fmax'])
    B8, img8, top = picture8(P, o['range_db'], o['gamma'], o['cscheme'])
    k_col = column_drive(B8, int(round(o['smooth_s'] * px_per_s)))
    levels = [(B8, img8)]
    if cam:                                         # halved copies for the wider views, down to the screen's width
        while P.shape[2] >= 2 * Wd:
            P = _halve(P)
            levels.append(picture8(P, o['range_db'], o['gamma'], o['cscheme'], top)[:2])
    del P
    if o['mode'] == 'scroll' and not cam:
        tail = Wd - lead
        B8 = np.pad(B8, ((0, 0), (lead, tail)))
        img8 = np.pad(img8, ((0, 0), (lead, tail), (0, 0)))
        k_col = np.pad(k_col, (lead, tail))
    color, halo_rgb = _rgb(o['line_color']), _rgb(o['halo_color'])
    reach = int(o['bloom_px'] * 1.4 * 3) + int(o['line_width']) + 2

    if cam:
        at = needle(o['at'], ft, t0, float(o['ease_s']), float(o['snap_s']))
        w_cam = camera_path(keys, ft, dur, float(o['ease_s']), at, float(o['snap_s']))[0]
        win = w_cam
        if follow.any():                            # loudness 0..1 over the clip, heavily smoothed
            loud = column_drive(levels[0][0], int(round(float(o['follow_s']) * px_per_s)))
            loud = loud[np.minimum((ft * px_per_s).astype(int), len(loud) - 1)]
            zoomed = np.clip(np.log2(dur / w_cam), 0, 1)
            win = np.minimum(dur, w_cam * 2 ** (follow * zoomed * (2 * loud - 1)))
        left = np.clip(ft - at * win, 0, dur - win)
        kick = np.zeros(n_frames)
        depth = keyed(o['kick_pulse'], ft, t0)
        if depth.any():
            mid = _edges(sr, Ht, o['fmin'], o['fmax'])[1:-1][::-1]
            low = np.flatnonzero(mid < float(o['kick_hz']))
            if len(low):
                kick = depth * _kick_env(B8, low, px_per_s, n_frames, fps, float(o['kick_decay_s']))
        mh = min(int(o['minimap']), Ht // 2)
        if mh:                                      # the whole clip, Wd columns and mh rows (row bands averaged)
            fb, fimg = levels[-1]
            cols = np.minimum(((np.arange(Wd) + 0.5) * fimg.shape[1] / Wd).astype(int), fimg.shape[1] - 1)
            rb = np.linspace(0, Ht, mh + 1).astype(int)
            mm = np.add.reduceat(fimg[:, cols].astype(np.float32), rb[:-1], axis=0) / np.diff(rb)[:, None, None]
            mm = (mm * 0.85 + 0.5).astype(np.uint8)
            mm[0] = (halo_rgb * 255 * 0.6).astype(np.uint8)
            bw = max(1, Ht // 540)
        xs = np.arange(Wd) + 0.5
        ys = np.arange(Ht) + 0.5

    def view(i):
        """The frame's picture (uint8, rows x Wd x 3), its brightness under the glow strip's columns, and the
        line's x on screen."""
        t = ft[i]
        if not cam:
            if o['mode'] == 'static':
                x, view0 = t * px_per_s, 0
            else:
                x = lead + t * px_per_s              # the play position in the padded picture
                view0 = int(round(x)) - lead
            fr = img8[:, view0:view0 + Wd].copy()
            return fr, lambda a, b: B8[:, view0 + a:view0 + b], x - view0, int(x)
        w, L = win[i], left[i]
        s = 1 + KICK_SCALE * kick[i]
        xl = (t - L) / w * Wd
        r = w * px_per_s / Wd                       # base columns a screen px
        lv = int(np.clip(np.floor(np.log2(max(r, 1e-9))), 0, len(levels) - 1))
        Bl, Il = levels[lv]
        cols = np.clip(((t + (xs - xl) * w / (Wd * s)) * px_per_s / 2 ** lv).astype(int), 0, Il.shape[1] - 1)
        if s > 1.001:
            rows = np.clip(((ys - Ht / 2) / s + Ht / 2).astype(int), 0, Ht - 1)
            fr = Il[rows[:, None], cols]
            Bv = lambda a, b: Bl[rows[:, None], cols[a:b]]
        else:
            fr = np.take(Il, cols, axis=1)
            Bv = lambda a, b: Bl[:, cols[a:b]]
        if kick[i] > 0.004:
            g = 1 + KICK_LIFT * kick[i]
            fr = np.minimum(np.arange(256) * g + 0.5, 255).astype(np.uint8)[fr]
        return fr, Bv, xl, int(t * px_per_s)

    def frame(i):
        fr, Bv, xi, xc = view(i)
        a, b = max(0, int(xi) - reach), min(Wd, int(xi) + reach + 1)
        if a < b:
            k = float(k_col[min(xc, len(k_col) - 1)])
            core, halo = glow_strip(Bv(a, b).astype(np.float32) / 255, xi - a, k, o)
            light = np.clip(halo[..., None] * halo_rgb + core[..., None] * color, 0, 1)
            fr[:, a:b] = _u8(1 - (1 - fr[:, a:b] / np.float32(255)) * (1 - light))    # screen blend
        if cam and mh:
            fade = float(np.clip(np.log2(dur / win[i]), 0, 1))
            if fade > 0.004:
                m = mm.copy()
                x0 = int(left[i] / dur * Wd)
                x1 = max(x0 + 2 * bw, int((left[i] + win[i]) / dur * Wd))
                ink = (color * 255).astype(np.uint8)
                m[:bw, x0:x1] = m[-bw:, x0:x1] = ink
                m[:, x0:x0 + bw] = m[:, max(x0, x1 - bw):x1] = ink
                if fade < 0.996:
                    m = (fr[-mh:] * (1 - fade) + m * fade + 0.5).astype(np.uint8)
                fr[-mh:] = m
        return fr

    ff = settings.get('ffmpeg')
    if not ff:
        raise RuntimeError("ffmpeg not found: install it (it must be on PATH) or name its path in the 'ffmpeg' "
                           "setting (ISMAIL_FFMPEG); spectro writes every frame through it")
    what = f'camera {len(keys)} keys' if cam else o['mode']
    if still is not None:
        i = min(n_frames - 1, max(0, int(round((float(still) - t0) * fps))))
        cmd = [ff, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Wd}x{Ht}',
               '-i', '-', '-frames:v', '1', out]
        subprocess.run(cmd, input=frame(i).tobytes(), check=True)
        return f'{out}: frame {i} ({t0 + i / fps:.2f} s), {what}, {Wd}x{Ht}'
    cmd = [ff, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{Wd}x{Ht}',
           '-r', f'{fps:g}', '-i', '-']
    if o['audio']:
        cmd += ['-ss', f'{t0:.3f}', '-t', f'{dur:.3f}', '-i', src, '-map', '0:v', '-map', '1:a', '-c:a', 'aac',
                '-b:a', '320k']
    cmd += ['-c:v', 'libx264', '-preset', str(o['preset']), '-crf', str(o['crf']), '-pix_fmt', 'yuv420p', '-shortest',
            out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    q, dead = queue.Queue(maxsize=4), []          # a writer thread feeds ffmpeg while the next frames are drawn

    def feed():
        try:
            while (fr := q.get()) is not None:
                p.stdin.write(fr)
        except OSError:                 # ffmpeg stopped: its exit code says why
            dead.append(1)
            while q.get() is not None:
                pass
    th = threading.Thread(target=feed, daemon=True)
    th.start()
    try:
        for i in range(n_frames):
            if dead:
                break
            q.put(np.ascontiguousarray(frame(i)))
    finally:
        q.put(None)
        th.join()
        p.stdin.close()
        rc = p.wait()
    if rc:
        raise RuntimeError(f'ffmpeg exit {rc} writing {out}')
    if cam:
        span = f'{win.min():.1f} to {win.max():.1f} s in view, {px_per_s:.1f} px a second at the tightest'
    else:
        span = f'{px_per_s:.1f} px a second'
    return (f'{out}: {dur:.1f} s, {n_frames} frames at {fps:g} fps, {what}, {Wd}x{Ht}, {span}'
            + (', with audio' if o['audio'] else ''))

