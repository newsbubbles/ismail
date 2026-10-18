# Transition effects: ismail `code` instrument. The note picks the effect; the uplifter, downlifter and reverse crash
# last as long as the note (gate), so a 2-bar note is a 2-bar sweep.
# C1 impact, D1 uplifter (noise sweep up), E1 downlifter (noise sweep down), any other note a reverse crash.
# Built in a song and proven there; designed by ear, no recordings.
import numpy as np

from ismail import dsp

INFO = {
    "designed": "transition effects designed by ear, not modeled on recordings; built in a song and proven there",
    "summary": "transitions for builds and drops: impact boom, noise uplifter and downlifter, reverse crash; "
               "sweeps last as long as the note",
    "velocity": "level; the sweeps peak near 1.7 at full velocity (1.0), so give the track -6 dB or play them softer",
    "range": "the note picks the effect (C4 = 60, so C1 is MIDI 24): C1 impact, D1 uplifter, E1 downlifter, "
             "any other note (F1) reverse crash. The uplifter, downlifter and reverse crash last the note's length "
             "(at least 0.2 s): a 2-bar note is a 2-bar sweep ending on the next downbeat",
    "tail": "3 s (the impact rings about 2 s)",
}

IMPACT, UPLIFTER, DOWNLIFTER = 24, 26, 28          # C1, D1, E1


def _nz(n, seed):
    return np.random.default_rng(seed).standard_normal(n)


def impact(t, sr):
    n = len(t)
    f = 38.0 + 90.0 * np.exp(-t / 0.05)
    boom = np.tanh(2.0 * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-t / 0.7))
    out = []
    for s in (1, 2):
        nz = dsp.filt(dsp.filt(_nz(n, s), 'lp12', 7000.0, 0.0, sr), 'hp12', 200.0, 0.0, sr)
        out.append(0.8 * boom + 0.35 * nz * np.exp(-t / 0.5))
    return np.stack(out) * np.clip(t / 0.001, 0, 1) * 0.8


def sweep(t, sr, gate, up=True):
    n = len(t)
    L = max(gate, 0.2)
    x = np.clip(t / L, 0, 1)
    if up:
        cut = 250.0 * (9000.0 / 250.0) ** (x ** 1.5)
        amp = x ** 2 * (t <= L) + (t > L) * np.exp(-np.maximum(t - L, 0) / 0.05)
    else:
        cut = 9000.0 * (180.0 / 9000.0) ** (x ** 0.7)
        amp = np.clip(t / 0.01, 0, 1) * (1 - x) ** 1.5
    out = []
    for s in (5, 6):
        y = dsp.filt(_nz(n, s), 'bp', cut, 0.55, sr)
        out.append(y * amp)
    return np.stack(out) * 0.9


def reverse_crash(t, sr, gate):
    n = len(t)
    L = max(gate, 0.2)
    x = np.clip(t / L, 0, 1)
    env = np.where(t <= L, np.exp(-(1 - x) * L / 0.5), 0.0)
    out = []
    for s in (9, 10):
        nz = dsp.filt(dsp.filt(_nz(n, s), 'hp12', 3500.0, 0.0, sr), 'lp12', 13000.0, 0.0, sr)
        out.append(nz * env)
    return np.stack(out) * 0.6


def voice(freq, t, vel, gate, sr):
    m = int(round(69 + 12 * np.log2(freq / 440.0)))     # ismail note numbers: C4 = 60, C1 = 24
    if m == IMPACT:
        y = impact(t, sr)
    elif m == UPLIFTER:
        y = sweep(t, sr, gate, up=True)
    elif m == DOWNLIFTER:
        y = sweep(t, sr, gate, up=False)
    else:
        y = reverse_crash(t, sr, gate)
    return y * vel
