"""Master safety chain for live output. Block in, block out, state carried between blocks.

trim -> level rider (slow cap on sustained loudness) -> lookahead peak limiter -> hard clip at the ceiling.
The numbers come from the environment, not from any tool, so an agent cannot turn them up:
  ISMAIL_LIVE_TRIM_DB     master trim before everything (default -6)
  ISMAIL_LIVE_CAP_DB      sustained level cap, RMS dBFS over ~3 s (default -16)
  ISMAIL_LIVE_CEILING_DB  peak ceiling (default -1)
"""
import os

import numpy as np
from numba import njit

from ..dsp import SR


BAD_ABS = 31.6           # +30 dBFS: past this a sample is a fault and is zeroed like a NaN
MS_CLIP = 4.0            # the rider's level reading never sees more than +12 dBFS a sample
RIDER_FLOOR_DB = -40.0   # the rider never pulls deeper than this


def _env(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


@njit(cache=True)
def _limit(buf, la, ceil, att, rel, g):
    """buf: (2, la + n), the first la samples are the previous block's tail. Returns gains for buf[:, :n] and the
    last gain. The gain falls ahead of a peak (lookahead window la) and recovers with `rel`."""
    n = buf.shape[1] - la
    out = np.empty(n)
    for i in range(n):
        m = 0.0
        for j in range(i, i + la + 1):
            a = abs(buf[0, j])
            b = abs(buf[1, j])
            if a > m:
                m = a
            if b > m:
                m = b
        target = 1.0 if m <= ceil else ceil / m
        if target < g:
            g = target + (g - target) * att
        else:
            g = target + (g - target) * rel
        out[i] = g
    return out, g


class Safety:
    def __init__(self, sr=SR):
        self.sr = sr
        self.trim_db = _env('ISMAIL_LIVE_TRIM_DB', -6.0)
        self.cap_db = _env('ISMAIL_LIVE_CAP_DB', -16.0)
        self.ceiling_db = _env('ISMAIL_LIVE_CEILING_DB', -1.0)
        self.ceil = 10 ** (self.ceiling_db / 20)
        self.la = int(0.003 * sr)
        self.att = float(np.exp(-1.0 / (self.la / 4)))
        self.rel = float(np.exp(-1.0 / (0.15 * sr)))
        self.hist = np.zeros((2, self.la))
        self.g = 1.0
        self.ms = 0.0                      # running mean square (post trim, pre rider)
        self.rider_db = 0.0
        self.min_lim_db = 0.0              # deepest limiter gain since the last report
        self.min_rider_db = 0.0
        self.bad_blocks = 0

    def process(self, x):
        n = x.shape[1]
        # a sample past BAD_ABS (+30 dBFS) is a fault, not music: it goes like a NaN. One huge finite sample once
        # pushed the 3 s mean square ~400 dB over the cap and the rider held the set silent for minutes (live DJ
        # 10-07 12:31, "rider -423.1 dB")
        bad = ~np.isfinite(x) | (np.abs(np.nan_to_num(x)) > BAD_ABS)
        if bad.any():
            self.bad_blocks += 1
            x = np.where(bad, 0.0, x)
        x = x * 10 ** (self.trim_db / 20)
        # rider: mean square with a ~3 s time constant; above the cap it pulls down at 12 dB/s, recovers at 2 dB/s,
        # never past RIDER_FLOOR_DB (the limiter after it holds the peaks), so it comes back within seconds
        dt = n / self.sr
        a = np.exp(-dt / 3.0)
        self.ms = self.ms * a + float(np.mean(np.clip(x, -MS_CLIP, MS_CLIP) ** 2)) * (1 - a)
        lvl = 10 * np.log10(self.ms + 1e-12)
        want = max(RIDER_FLOOR_DB, min(0.0, self.cap_db - lvl))
        prev = self.rider_db
        if want < self.rider_db:
            self.rider_db = max(want, self.rider_db - 12 * dt)
        else:
            self.rider_db = min(want, self.rider_db + 2 * dt)
        self.min_rider_db = min(self.min_rider_db, self.rider_db)
        x = x * (10 ** (np.linspace(prev, self.rider_db, n) / 20))
        buf = np.concatenate([self.hist, x], axis=1)
        gains, self.g = _limit(buf, self.la, self.ceil, self.att, self.rel, self.g)
        y = buf[:, :n] * gains
        self.hist = buf[:, n:]
        self.min_lim_db = min(self.min_lim_db, 20 * np.log10(max(float(gains.min()), 1e-9)))
        return np.clip(y, -self.ceil, self.ceil)

    def report(self, reset=True):
        s = (f"trim {self.trim_db:g} dB, rider {self.min_rider_db:.1f} dB (cap {self.cap_db:g} dBFS rms), "
             f"limiter {self.min_lim_db:.1f} dB (ceiling {self.ceiling_db:g} dBFS)")
        if self.bad_blocks:
            s += f", {self.bad_blocks} blocks had NaN/inf (zeroed)"
        if reset:
            self.min_lim_db = 0.0
            self.min_rider_db = self.rider_db
        return s
