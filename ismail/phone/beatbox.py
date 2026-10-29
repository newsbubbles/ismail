"""A beatbox take as drum hits: where each hit is, and whether it is a kick, a snare or a hat (ledger:M222).

One classifier for every script that reads a beatbox voice note. The hits are ranked against each other on the
take, never against fixed numbers: a phone or earbud mic takes most of the low end off a mouth kick (a dark thump
with little under 250 Hz) and puts the level wherever the person held the phone, so a fixed band share or a fixed
level calls kicks snares and snares hats. Three copies of the rule drifted apart in a live set; the absolute one
read 5 of 50 kicks on a take where the person played about 20 in four bars.

The rule (classify):
  1. dark or bright: two-means on the log spectral centroid of each hit's first 60 ms. The dark side is the kick
     when the two sides sit at least an octave apart. A hit with more energy under 250 Hz than above 4 kHz is a
     kick either way (a kick with a click on it). No octave between them (a take of one kind of sound): the
     band rule of a full-range mic decides (little above 4 kHz and much under 250 Hz is a kick).
  2. among the bright hits, two-means on onset strength: the hardest are snares, the rest hats. Strength split
     them better than level on a real take (level also took hats between the snares). Under 4 bright hits, or no
     split: under 4 are all snares, else the harder half.

    from ismail.phone import beatbox
    hits = beatbox.read(y, sr, ref=None)   # [{'t', 'strength', 'lo', 'hi', 'centroid_hz', 'level_db', 'class',
                                           #   'margin'}]
    kinds = beatbox.classify(hits)         # the rule on hits you found yourself (needs centroid_hz, lo, hi, strength)
"""
import numpy as np

SR = 22050                    # hit finding runs at this rate (the frames below are tuned to it)
N_FFT = 1024
HOP = 256
FEAT_FRAMES = 5               # a hit's sound: its first 5 frames (about 60 ms)
FLUX_BAND = (100.0, 10000.0)  # onset flux band: above room rumble, below hiss
LO_HZ, HI_HZ = 250.0, 4000.0  # the band shares: under 250 Hz (body) and above 4 kHz (air)
KEEP = 0.2                    # a hit's flux is at least this share of the strongest
PEAK = 0.7                    # the take is scaled to this peak first (about -3 dBFS, where a raw phone take sits):
#                               the flux is log-compressed, so without it a quiet take loses its quiet hits
DARK_OCT = 1.0                # dark and bright must sit an octave apart to split on centroid
SNARE_GAP = 0.15              # the snares' strength must stand this far above the hats' to split on strength
CLASSES = ('kick', 'snare', 'hat')


def split(x, min_gap):
    """The 1-D two-means cut of x (the midpoint between the two groups), or None when there are under 4 values or
    the two groups' means sit under min_gap apart (one kind of value only)."""
    x = np.sort(np.asarray(x, float))
    if len(x) < 4:
        return None
    j = min(range(1, len(x)), key=lambda j: x[:j].var() * j + x[j:].var() * (len(x) - j))
    return float((x[j - 1] + x[j]) / 2) if x[j:].mean() - x[:j].mean() >= min_gap else None


def _get(h, k):
    return float(h[k])


def classify(hits, margins=False):
    """['kick' | 'snare' | 'hat'] for hits [{'centroid_hz', 'lo', 'hi', 'strength'}], ranked on this take (module
    docstring). lo and hi are the energy shares under 250 Hz and above 4 kHz in the hit's first 60 ms; strength is
    the onset strength on any scale (only its order and spread on the take count).
    margins=True returns (classes, margins): how clearly each hit is its class, in the unit of the cut that decided
    it (octaves of centroid from the dark/bright cut for a kick or a bright hit's side, strength from the
    snare/hat cut within the bright ones; the smaller of the two for snares and hats). None where a fallback rule
    decided."""
    if not hits:
        return ([], []) if margins else []
    cen = np.log2([max(50.0, _get(h, 'centroid_hz')) for h in hits])
    lo = np.array([_get(h, 'lo') for h in hits])
    hi = np.array([_get(h, 'hi') for h in hits])
    w = np.array([_get(h, 'strength') for h in hits])
    c = split(cen, DARK_OCT)
    if c is not None:
        dark = cen < c
    else:                                                       # one kind of sound: a full-range mic's band rule
        dark = (hi <= 0.3) & (lo > 0.35)
    dark = dark | (lo > hi)                                     # more body than air: a kick with a click on it
    bright = w[~dark]
    s = split(bright, SNARE_GAP)
    s_fallback = s is None
    if s is None:
        s = -np.inf if len(bright) < 4 else float(np.median(bright))
    out = ['kick' if d else ('snare' if x >= s else 'hat') for d, x in zip(dark, w)]
    if not margins:
        return out
    m = []
    for k, x, wi in zip(out, cen, w):
        side = None if c is None else (c - x if k == 'kick' else x - c)
        if k == 'kick':
            m.append(None if side is None else round(float(side), 3))
            continue
        sw = None if s_fallback else (wi - s if k == 'snare' else s - wi)
        both = [v for v in (side, sw) if v is not None]
        m.append(round(float(min(both)), 3) if both else None)
    return out, m


def _spec(y):
    import librosa
    S = np.abs(librosa.stft(np.asarray(y, np.float32), n_fft=N_FFT, hop_length=HOP))
    return S, librosa.fft_frequencies(sr=SR, n_fft=N_FFT)


def _flux(S, f):
    B = np.log1p(10 * S[(f > FLUX_BAND[0]) & (f < FLUX_BAND[1])])
    return np.maximum(0, np.diff(B, axis=1, prepend=B[:, :1])).sum(0)


def read(y, sr=SR, ref=None, keep=KEEP):
    """The hits of a beatbox take, classified: [{'t' (s), 'strength' (0..1 of the strongest), 'lo', 'hi' (energy
    shares under 250 Hz / above 4 kHz in the first 60 ms), 'centroid_hz', 'level_db' (energy of the first 60 ms
    with the take's peak at -3 dBFS: only differences on the take mean anything), 'class', 'margin'}] in time order.
    Onsets: spectral flux from 100 Hz to 10 kHz, peak-picked against its own 75th percentile. ref is the music the
    mic heard under the take, already lined up with it and at the same rate (the phone server saves it as
    <id>_ref.wav); its flux, scaled by a least-squares gain, is taken off the take's so the speakers' beat is not
    read as the person's hits. Hits under keep times the strongest are dropped."""
    import librosa
    y = np.asarray(y, np.float32)
    if y.ndim > 1:
        y = y.mean(axis=0 if y.shape[0] < y.shape[1] else 1)
    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if len(y) < N_FFT or not np.abs(y).max() > 0:
        return []
    scale = PEAK / float(np.abs(y).max())
    S, f = _spec(y * scale)
    d = _flux(S, f)
    if ref is not None:
        r = np.asarray(ref, np.float32)
        if r.ndim > 1:
            r = r.mean(axis=0 if r.shape[0] < r.shape[1] else 1)
        if sr != SR:
            r = librosa.resample(r, orig_sr=sr, target_sr=SR)
        if len(r) >= N_FFT and np.abs(r).max() > 0:
            dr = _flux(*_spec(r * scale))[:len(d)]
            dr = np.pad(dr, (0, len(d) - len(dr)))
            g = float(np.clip(np.dot(d, dr) / (np.dot(dr, dr) + 1e-9), 0.0, 2.0))
            d = np.maximum(0, d - g * dr)
    if not d.any():
        return []
    pk = librosa.util.peak_pick(d, pre_max=3, post_max=3, pre_avg=8, post_avg=8,
                                delta=float(np.percentile(d, 75)) * 0.5, wait=5)
    if not len(pk):
        return []
    m = d[pk].max()
    hits = []
    for i in pk:
        if d[i] < keep * m:
            continue
        e = (S[:, i:i + FEAT_FRAMES] ** 2).sum(1)
        tot = float(e.sum()) + 1e-12
        hits.append({'t': round(float(i * HOP / SR), 4), 'strength': round(float(d[i] / m), 3),
                     'lo': round(float(e[f < LO_HZ].sum() / tot), 3), 'hi': round(float(e[f > HI_HZ].sum() / tot), 3),
                     'centroid_hz': round(float((e * f).sum() / tot), 1),
                     'level_db': round(float(10 * np.log10(tot)), 1)})
    kinds, margins = classify(hits, margins=True)
    for h, k, mg in zip(hits, kinds, margins):
        h['class'], h['margin'] = k, mg
    return hits
