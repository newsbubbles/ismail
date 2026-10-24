"""mimic's slow random curves (pitch drift, vibrato wander, swell, shimmer) keep the same depth on short and long
notes (ledger:M306). Uses the curve generator directly, so it stays fast."""
import numpy as np

from ismail import mimic

SR_C = mimic.SR / mimic.CTRL
# (rate_hz, control rate) for every caller of _smooth_noise in mimic.render and mimic._vibrato
CALLERS = {'pitch drift': (1.5, SR_C), 'swell': (0.8, SR_C), 'shimmer': (6.0, SR_C),
           'vibrato rate wander': (0.9, 20.0), 'vibrato depth wander': (0.7, 20.0)}


def _rms(rate, sr_c, seconds, seeds):
    n = max(1, int(seconds * sr_c))
    x = np.concatenate([mimic._smooth_noise(np.random.default_rng(s), n, rate, sr_c) for s in range(seeds)])
    return float(np.sqrt(np.mean(x ** 2)))


def test_short_note_not_inflated():
    for name, (rate, sr_c) in CALLERS.items():
        short = _rms(rate, sr_c, 0.1, 200)
        long = _rms(rate, sr_c, 10.0, 16)
        assert 0.75 < short / long < 1.3, (name, short, long)


def test_long_note_depth_unchanged():
    # before M306 a long curve was stretched to std 1 by its own std; that is the depth long notes must keep
    for name, (rate, sr_c) in CALLERS.items():
        assert 0.9 < _rms(rate, sr_c, 10.0, 16) < 1.1, name


def test_short_note_matches_a_window_of_a_long_curve():
    # the old code sent a 0.1 s pitch drift curve to about 3.5x its depth; now it sits near 1
    assert _rms(1.5, SR_C, 0.1, 200) < 1.3


def test_deterministic_with_seed():
    for rate, sr_c in CALLERS.values():
        a = mimic._smooth_noise(np.random.default_rng(7), 300, rate, sr_c)
        b = mimic._smooth_noise(np.random.default_rng(7), 300, rate, sr_c)
        c = mimic._smooth_noise(np.random.default_rng(8), 300, rate, sr_c)
        assert a.shape == (300,) and np.array_equal(a, b) and not np.array_equal(a, c)
    one = mimic._smooth_noise(np.random.default_rng(1), 1, 1.5, SR_C)
    assert one.shape == (1,) and np.isfinite(one).all()
