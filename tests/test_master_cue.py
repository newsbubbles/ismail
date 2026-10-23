"""master_cue (ledger:M207): one fixed gain to the loudness target, a limiter only for the true-peak excess, the cut
and fades, the resample, and an existing master moved to old/, never overwritten."""
import os

import numpy as np
import pytest
import soundfile as sf

pytest.importorskip('pyloudnorm')

from ismail.api import OPS, OpError  # noqa: E402
from ismail import master as M  # noqa: E402


def _tone(path, secs=4.0, sr=44100, amp=0.05, clicks=False):
    t = np.arange(int(secs * sr)) / sr
    y = amp * np.sin(2 * np.pi * 220 * t)
    if clicks:                                   # sharp peaks over a quiet bed: the gain alone would clip them
        y[::sr // 2] = 0.9
    sf.write(path, np.stack([y, y * 0.8], 1), sr)
    return path


def _num(out, after):
    return float(out.split(after)[1].split()[0])


def test_a_quiet_cue_gets_one_gain_and_no_limiting(tmp_path):
    src = _tone(str(tmp_path / 'cue.wav'))
    out = OPS['master_cue'](source=src, start_s=0.5, end_s=3.5)
    y, sr = sf.read(str(tmp_path / 'cue_master.wav'), always_2d=True)
    assert sr == 48000 and y.shape[1] == 2 and abs(len(y) / sr - 3.0) < 0.01
    assert sf.info(str(tmp_path / 'cue_master.wav')).subtype == 'PCM_24'
    assert abs(_num(out, 'measured: ') - -16.0) < 0.2
    assert 'no limiting' in out
    assert abs(y[-1]).max() < 1e-3                # the fade ends in silence
    import pyloudnorm as pyln
    assert abs(pyln.Meter(sr).integrated_loudness(y) - -16.0) < 0.2


def test_peaks_over_the_ceiling_are_limited_and_reported(tmp_path):
    src = _tone(str(tmp_path / 'hits.wav'), clicks=True, amp=0.02)
    out = OPS['master_cue'](source=src)
    y, sr = sf.read(str(tmp_path / 'hits_master.wav'), always_2d=True)
    assert 'limiter took' in out
    assert M.true_peak_db(y) <= -1.0 + 0.05
    assert _num(out, 'true peak ') <= -0.95


def test_an_existing_master_moves_to_old_and_the_source_is_never_written(tmp_path):
    src = _tone(str(tmp_path / 'cue.wav'))
    OPS['master_cue'](source=src)
    out = OPS['master_cue'](source=src, lufs=-14)
    assert 'the old one is now' in out
    assert len(os.listdir(tmp_path / 'old')) == 1
    with pytest.raises(OpError, match='never overwritten'):
        OPS['master_cue'](source=src, out=src)


def test_bad_asks_say_what_to_change(tmp_path):
    src = _tone(str(tmp_path / 'cue.wav'))
    with pytest.raises(OpError, match='no such file'):
        OPS['master_cue'](source=str(tmp_path / 'nope.wav'))
    with pytest.raises(OpError, match='nothing between'):
        OPS['master_cue'](source=src, start_s=3, end_s=2)
    with pytest.raises(OpError, match='clipping'):
        OPS['master_cue'](source=src, tp=0.5)
    sf.write(str(tmp_path / 'silent.wav'), np.zeros((48000, 2)), 48000)
    with pytest.raises(OpError, match='silent'):
        OPS['master_cue'](source=str(tmp_path / 'silent.wav'))
