"""jRhodes3d (Jeff Learman: a 1977 Rhodes Mark I Stage 73, recorded from the harp connector) as a performer.
Licence: the author grants musicians CC0 use of the sounds in their music; redistributing the samples is CC BY-NC 4.0,
so the samples stay out of git: samples_fetch('jrhodes3d') downloads them on the person's word (ismail.samples).
Per note: the nearest sampled key (every 4th white key, played up to 3 semitones faster or slower), the velocity layer
from the note's velocity (5 layers, the author's sfz split 47/72/95/111), a small keyed spread so repeated chords do
not hit the same layer twice, the level from the note's velocity, a cent or two of keyed detune, and the damper at the
note's end (60 dB over the author's release time: 0.5 s low, 0.2 s high). Seeds by song time (beat0), so windows
render as the whole song does. Mono out (a harp-connector DI), panned; put tremolo/chorus/amp after it.
Level: the samples are each normalised near full scale, so a soft layer reads 2 to 7 dB hotter than the hard one
(the author's sfz leaves the level to the player's default velocity curve). Each layer is matched in level to the
key's hardest layer (RMS of its first 0.4 s), so a layer is timbre only, and the level follows the note's velocity:
vel_db dB from velocity 127 down to 12.7 (0.1), amplitude (v/127) ** (vel_db / 20). The spread moves the layer, never
the level. Measured at C4 (a 0.15 s note, RMS), vel_db 30: 0.1 -30.7, 0.2 -21.7, 0.4 -12.6, 0.6 -7.2, 0.8 -3.2,
1.0 0 dB."""
import glob
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
INFO = {
    "summary": "1977 Rhodes Mark I Stage 73 (jRhodes3d, Jeff Learman) DI samples, 5 velocity layers, damper release",
    "range": "A0-C8 (samples every 4th white key F1..C7, shifted up to 3 semitones)",
    "velocity": "picks the layer (<=47, <=72, <=95, <=111, above; the soft layers are mellow, the top ones bark) and "
                "the level: vel_db (30) dB from 1.0 down to 0.1, the layers matched in level",
    "params": {"level": "output gain", "pan": "-1..1", "humanize_ms": "timing jitter, ms", "spread": "layer spread, layers",
               "detune_cents": "keyed per-note detune, cents", "release_scale": "damper time multiplier",
               "vel_db": "level drop from velocity 127 to 12.7, dB (0 = one level for every velocity)"},
    "source": "https://github.com/sfzinstruments/jlearman.jRhodes3d (samples CC BY-NC 4.0; CC0 for musicians' music)",
    "measured": "sampled: the real instrument's notes, played back (samples_fetch('jrhodes3d'))",
}
LAYER_TOP = [(5, 47), (4, 72), (3, 95), (2, 111), (1, 127)]
_cache = {}


def _root():
    from ismail import samples
    return samples.need('jrhodes3d')


def _index():
    if 'idx' in _cache:
        return _cache['idx']
    idx = {}
    for f in glob.glob(os.path.join(_root(), '*.flac')):
        m = re.match(r'A_(\d+)__\w+_(\d)\.flac$', os.path.basename(f))
        if m:
            idx.setdefault(int(m.group(1)), {})[int(m.group(2))] = f
    _cache['idx'] = idx
    return idx


def _load(f, sr, semis, cents):
    key = (f, sr, round(semis + cents / 100.0, 4))
    if key in _cache:
        return _cache[key]
    import soundfile as sf
    from fractions import Fraction
    from scipy import signal
    y, fsr = sf.read(f, dtype='float64', always_2d=True)
    y = y.mean(1)
    ratio = fsr / sr * 2 ** ((semis + cents / 100.0) / 12.0)
    if abs(ratio - 1) > 1e-6:
        fr = Fraction(ratio).limit_denominator(2000)
        y = signal.resample_poly(y, fr.denominator, fr.numerator)
    _cache[key] = y
    return y


def _level(f):
    """RMS of a sample's first 0.4 s (the attack and the body a short note plays)."""
    key = ('lv', f)
    if key not in _cache:
        import soundfile as sf
        info = sf.info(f)
        y, _ = sf.read(f, frames=int(0.4 * info.samplerate), dtype='float64', always_2d=True)
        _cache[key] = float(np.sqrt(np.mean(y.mean(1) ** 2))) + 1e-12
    return _cache[key]


def _rng(beat, m, salt):
    return np.random.default_rng([int(round(beat * 960)) % (2 ** 31), int(m), int(salt)])


def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, level=1.0, pan=0.0, humanize_ms=2.0, spread=0.35,
            detune_cents=1.5, release_scale=1.0, vel_db=30.0, **_):
    out = np.zeros(total_n)
    idx = _index()
    keys = sorted(idx)
    for st, m, dur, v in notes:
        m = int(m)
        beat = beat0 + float(st) * bpm / 60.0
        r = _rng(beat, m, 11)
        base = min(keys, key=lambda k: (abs(k - m), k))
        vv = float(np.clip(v + r.normal(0, spread * 12), 1, 127))
        want = next(n for n, top in LAYER_TOP if vv <= top)
        have = idx[base]
        layer = want if want in have else min(have, key=lambda k: (abs(k - want), k))
        g = _level(have[min(have)]) / _level(have[layer])          # the layer at the key's hardest layer's level
        g *= (float(np.clip(v, 1, 127)) / 127.0) ** (vel_db / 20.0)  # the level from the played velocity
        y = _load(have[layer], sr, m - base, r.normal(0, detune_cents))
        i0 = max(0, int(round((float(st) + r.normal(0, humanize_ms / 1000.0)) * sr)))
        if i0 >= total_n:
            continue
        rel = float(np.interp(m, [29, 60, 96], [0.5, 0.35, 0.2])) * release_scale
        n_note = int(float(dur) * sr)
        n_rel = int(rel * sr)
        n = min(len(y), n_note + n_rel, total_n - i0)
        if n <= 0:
            continue
        w = y[:n].copy()
        if n > n_note:
            t = np.arange(n - n_note) / sr
            w[n_note:] *= 10 ** (-3.0 * t / rel)                    # 60 dB over the release time
        a = int(0.002 * sr)                                          # declick the sample start
        w[:a] *= np.linspace(0, 1, a)
        out[i0:i0 + n] += g * w
    gl, gr = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    return np.stack([out * gl, out * gr]) * level
