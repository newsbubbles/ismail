"""A mimic note's random variation moves its level by LEVEL_CAP_DB at most between takes (ledger M301): the seed
comes from pitch, velocity and gate, and one velocity drew a take 8 dB over its neighbours, every repeat as loud."""
import numpy as np
import pytest

from ismail import instruments, mimic
from ismail.dsp import SR

GATE = 0.3
VELS = (84, 86, 92, 93)        # violin D5 takes: 8.4 dB apart per unit velocity before the cap


@pytest.fixture(scope='module')
def violin():
    return mimic.load_profile('violin')


def levels(prof):
    out = []
    for v in VELS:
        y = mimic.render(prof, mimic._hz(74), np.arange(int((GATE + 0.2) * SR)) / SR, v / 127, GATE, SR, mono=True)
        out.append(20 * np.log10(np.sqrt(np.mean(y[:, :int(GATE * SR)] ** 2))) - 20 * np.log10(v / 127))
    return np.array(out)


def test_neighbouring_takes_stay_within_the_cap(violin, monkeypatch):
    capped, cap = levels(violin), mimic.LEVEL_CAP_DB
    monkeypatch.setattr(mimic, 'LEVEL_CAP_DB', 1e9)          # the variation as it was
    free = levels(violin)
    assert free.max() - free.min() > cap + 2, free   # the free variation goes well past the cap
    # the cap, plus a little for the brighter tone of a higher velocity
    assert capped.max() - capped.min() <= cap + 0.5, capped
    assert capped.std() > 0.3, capped                      # the takes still differ


def test_outlier_takes_are_named():
    takes = [(0.0, 74, 90, 0.46, -14.0), (2.0, 74, 90, 0.46, -14.0), (4.0, 74, 96, 0.46, -22.0),
             (6.0, 73, 90, 0.46, -21.5), (8.0, 76, 88, 0.46, -22.5)]
    lines = instruments.mimic_outliers(takes)
    assert len(lines) == 1 and 'D5 vel 90 (2x, first at 0.0 s)' in lines[0] and '8.0 dB' in lines[0], lines
    assert instruments.mimic_outliers(takes[2:]) == []
