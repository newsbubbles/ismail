"""Reading a drifting, thumb-led record (M287): analyze_grid reads the thumb band and the finger band apart and says
when they disagree; analyze_swing(follow=True) reads every 16th step on a beat line that follows the drift."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail import api

SR = 22050
OPS = api.OPS


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp(prefix='ismail_drift_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


def low_hit(sr=SR):
    t = np.arange(int(0.15 * sr)) / sr
    return np.sin(2 * np.pi * 98 * t) * np.sin(0.5 * np.pi * np.minimum(1, t / 0.01)) ** 2 * np.exp(-t * 45)


def high_hit(gain, rng, sr=SR):
    t = np.arange(int(0.03 * sr)) / sr
    noise = np.diff(np.diff(np.r_[0, 0, rng.normal(0, 1, len(t))]))
    return gain * 0.3 * noise * np.exp(-t * 150)


def render(beats, figure, lead_in=0.3, sr=SR, seed=0):
    """A low hit on every beat time in `beats`; a high hit at each (step, gain, offset_ms) of `figure`, a 16-step
    figure over 4 beats laid on the beat line (16ths between two beats split their span evenly)."""
    rng = np.random.default_rng(seed)
    y = rng.normal(0, 1e-4, int((lead_in + beats[-1] + 0.5) * sr))

    def put(h, t):
        a = int((lead_in + t) * sr)
        y[a:a + len(h)] += h[:len(y) - a]
    for b in beats[:-1]:
        put(low_hit(), b)
    for k in range(len(beats) - 1):
        span = beats[k + 1] - beats[k]
        for step, gain, ms in figure:
            if step // 4 == k % 4:
                put(high_hit(gain, rng), beats[k] + span * (step % 4) / 4 + ms / 1000)
    return 0.5 * y / np.abs(y).max()


def steady(bpm, n):
    return np.arange(n + 1) * 60 / bpm


def drifting(bpm0, bpm1, n):
    tempo = np.linspace(bpm0, bpm1, n)
    return np.r_[0, np.cumsum(60 / tempo)]


# 3-3-2 twice over 4 beats (steps 0, 3, 6 and 8, 11, 14), louder on the beats
FIGURE = [(s, 1.0 if s % 4 == 0 else 0.6, 0.0) for s in (0, 3, 6, 8, 11, 14)]


def test_grid_reads_thumb_and_finger_bands_apart(tmp):
    p = os.path.join(tmp, 'bossa.wav')
    sf.write(p, render(steady(92, 40), FIGURE), SR)
    data, txt = A.band_periods(p, 122.67)
    assert abs(data['thumb']['bpm'] - 92) < 1.5, txt
    assert abs(data['finger']['bpm'] - 92 * 4 / 3) < 2, txt
    assert data['agree'] is False and 'BANDS DISAGREE' in txt and '4/3' in txt, txt
    assert 'thumb/bass band is likely the beat' in txt and 'bpm_hint=92' in txt, txt
    proj = os.path.join(tmp, 'p')
    api.project_new(proj, bpm=92, length_bars=8)
    out = OPS['analyze_grid'](proj, source=p)
    assert out.startswith('tempo ') and 'bar 1 starts at' in out and 'local tempo per 30s' in out, out
    assert 'thumb/bass band 55-190 Hz: 9' in out and 'BANDS DISAGREE' in out, out
    # a finger stroke on every beat with the thumb: the bands agree and nothing is flagged
    q = os.path.join(tmp, 'even.wav')
    sf.write(q, render(steady(92, 40), [(s, 1.0, 0.0) for s in (0, 4, 8, 12)]), SR)
    data, txt = A.band_periods(q, 92.0)
    assert data['agree'] is True and 'DISAGREE' not in txt and 'bpm_hint' not in txt, txt


def test_swing_follows_a_drifting_beat_line(tmp):
    planted = {3: 20.0, 6: -15.0, 11: 20.0, 14: -15.0}
    fig = [(s, g, planted.get(s, 0.0)) for s, g, _ in FIGURE]
    p = os.path.join(tmp, 'drift.wav')
    sf.write(p, render(drifting(90, 94, 40), fig), SR)
    data, txt = A.swing_follow(p, 92)
    assert 89 < data['bpm_roll'][0] < 91.5 and 92.5 < data['bpm_roll'][1] < 95, txt
    for step, ms in planted.items():
        assert abs(data['steps'][step + 1]['off_ms'] - ms) < 5, (step + 1, txt)
    for step in (0, 8):
        assert abs(data['steps'][step + 1]['off_ms']) < 5, txt
    assert data['top_patterns'][0][0] == 'X..X..X.X..X..X.', txt
    assert 'most played 16-step strings' in txt and 'timing in the' in txt, txt
    proj = os.path.join(tmp, 'p')
    api.project_new(proj, bpm=92, length_bars=8)
    out = OPS['analyze_swing'](proj, source=p, follow=True)
    assert 'X..X..X.X..X..X.' in out and 'step  4' in out, out
    # the fixed-grid reading stays first and says the beat drifts
    out = OPS['analyze_swing'](proj, source=p)
    assert out.startswith('swing from hi-hat onsets') and 'the beat drifts' in out and 'follow=True' in out, out
