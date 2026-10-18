"""analyze_voice_heard: a voice stem against the sum of the other stems, per band, while the voice speaks."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api
from ismail.api import OPS

SR = 44100


def _voice():
    t = np.arange(4 * SR) / SR
    y = sum(np.sin(2 * np.pi * 200 * k * t) / k for k in range(1, 40))   # a 200 Hz buzz up to 8 kHz
    return y * 0.2 * ((t % 0.5) < 0.3)                                     # 0.3 s bursts, as words


def _noise(lo=None, hi=None, amp=0.05, seed=1):
    import scipy.signal as ss
    y = np.random.default_rng(seed).standard_normal(4 * SR)
    if lo:
        y = ss.sosfilt(ss.butter(4, [lo / (SR / 2), hi / (SR / 2)], 'band', output='sos'), y)
    return y * amp / (np.std(y) + 1e-12)


@pytest.fixture()
def proj():
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=2)
    for n in ('voice', 'pad', 'hats'):
        api.track_add(p, n)
    os.makedirs(os.path.join(p, 'renders', 'stems'))

    def stems(**parts):
        for n, y in parts.items():
            sf.write(os.path.join(p, 'renders', 'stems', n + '.wav'), np.stack([y, y], 1), SR)
    yield p, stems
    shutil.rmtree(d, ignore_errors=True)


def test_analyze_voice_heard_over_quiet_noise(proj):
    p, stems = proj
    stems(voice=_voice(), pad=_noise(amp=0.002), hats=_noise(amp=0.001, seed=2))
    out = OPS['analyze_voice_heard'](p, 'voice')
    assert 'verdict: heard: the voice is over the rest in every band' in out, out
    assert '300-1000' in out and '4000-8000' in out


def test_analyze_voice_heard_names_the_band_and_part_that_mask_it(proj):
    p, stems = proj
    stems(voice=_voice(), pad=_noise(450, 550, amp=0.3), hats=_noise(amp=0.001, seed=2))
    out = OPS['analyze_voice_heard'](p, 'voice')
    assert 'the words are clear' in out and '300-1000 Hz (body) masks it' in out and 'mostly pad' in out, out
    stems(voice=_voice(), pad=_noise(amp=0.002), hats=_noise(amp=0.5, seed=2))
    out = OPS['analyze_voice_heard'](p, 'voice', bars=[1, 2])
    assert 'verdict: masked:' in out and 'mostly hats' in out, out


def test_analyze_voice_heard_needs_the_stems(proj):
    p, _ = proj
    with pytest.raises(api.OpError):
        OPS['analyze_voice_heard'](p, 'voice')
