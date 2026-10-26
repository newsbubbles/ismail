"""The limiter's true_peak option holds the ceiling between samples (ledger:M271h, M277a)."""
import numpy as np

from ismail import fx as fxmod
from ismail.master import true_peak_db


class Ctx:
    sr, bpm = 48000, 120

    def param(self, i, name, default):
        return default

    def note_gr(self, i, db):
        self.gr = db


def _bright():
    # a near-Nyquist tone sampled off its peaks: the sample peak reads well under the true peak
    sr = 48000
    t = np.arange(sr) / sr
    y = np.sin(2 * np.pi * 11999.0 * t + 0.7)
    return np.stack([y, y])


def _limit(x, **kw):
    f = fxmod.normalize(dict({'type': 'limiter', 'ceiling_db': -1.0, 'gain_db': 6.0}, **kw))
    return fxmod.apply_fx(x, f, Ctx(), 0)


def test_plain_limiter_goes_over_between_samples():
    assert true_peak_db(_limit(_bright()).T) > -1.0


def test_true_peak_limiter_holds_the_ceiling():
    y = _limit(_bright(), true_peak=True)
    assert -2.0 < true_peak_db(y.T) <= -1.0
