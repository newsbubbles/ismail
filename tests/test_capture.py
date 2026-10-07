"""Capture faults found before a recording is measured (ledger:M172)."""
import json
import os
import shutil
import tempfile

import numpy as np
import soundfile as sf

from ismail import api, capture

SR = 44100


def _voiceish(seconds=3.0, seed=0, amp=0.5):
    """A sung-like note: harmonics of 220 Hz plus breath noise across the band, as a normal mic hears it."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * SR)) / SR
    y = sum(0.5 / k * np.sin(2 * np.pi * 220 * k * t) for k in range(1, 30))
    y = y + 0.05 * rng.standard_normal(len(t))
    return amp * y / np.max(np.abs(y))


def _lowpass(y, hz):
    Y = np.fft.rfft(y)
    Y[np.fft.rfftfreq(len(y), 1 / SR) > hz] = 0
    return np.fft.irfft(Y, len(y))


def test_a_clean_take_raises_nothing():
    c = capture.check(_voiceish(), SR)
    assert c['warnings'] == [] and c['plateau'] is None
    assert -45 < c['air_db'] < -5


def test_peaks_flattened_near_0_896_are_found_where_a_clip_test_sees_nothing():
    y = np.clip(_voiceish(amp=1.6), -0.896, 0.896)          # the browser mic chain: a plateau, not a clip
    assert np.max(np.abs(y)) < 0.98
    c = capture.check(y, SR)
    assert c['plateau'] and abs(c['plateau'][0] - 0.896) < 0.002
    assert any('flattened' in w for w in c['warnings'])
    assert capture.plateau(np.clip(_voiceish(amp=1.6), -1, 1), SR) is None     # real clipping is another check's
    assert capture.plateau(_voiceish(amp=0.9), SR) is None                    # a loud clean take is not a plateau


def test_a_bluetooth_mic_top_is_found_and_no_top_silences_it():
    y = _lowpass(_voiceish(), 7500)
    c = capture.check(y, SR)
    assert c['air_db'] < capture.AIR_MIN_DB
    assert any('8 kHz' in w for w in c['warnings'])
    assert capture.check(y, SR, no_top=True)['warnings'] == []
    assert capture.air_db(y, 16000) < capture.AIR_MIN_DB                    # 16 kHz audio has no 8-12 kHz at all
    assert capture.air_db(np.zeros(SR), SR) is None                           # silence says nothing


def test_long_files_average_frames_and_agree_on_the_verdict():
    y = np.tile(_voiceish(seconds=2.0), 50)                                   # 100 s: over FULL_MAX samples
    assert len(y) > capture.FULL_MAX
    assert -45 < capture.air_db(y, SR) < -5
    assert capture.air_db(_lowpass(y, 7500), SR) < capture.AIR_MIN_DB


def test_mimic_measure_and_sound_import_say_so_and_the_profile_keeps_it(monkeypatch, tmp_path):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))      # an empty board: the op takes a cpu slot
    d = tempfile.mkdtemp(prefix='ismail_test_')
    try:
        p = os.path.join(d, 'p')
        api.project_new(p, bpm=120, length_bars=1)
        rec = os.path.join(d, 'rec')
        os.makedirs(rec)
        for i, (name, midi) in enumerate((('A3', 57), ('E4', 64), ('A4', 69))):
            t = np.arange(int(2.0 * SR)) / SR
            f = 440.0 * 2 ** ((midi - 69) / 12)
            y = sum(0.3 / k * np.sin(2 * np.pi * k * f * t) for k in range(1, 12))
            y = y + 0.01 * np.random.default_rng(i).standard_normal(len(t))
            y *= np.minimum(1, t / 0.02) * np.minimum(1, (t[-1] - t) / 0.2)
            sf.write(os.path.join(rec, name + '.wav'), _lowpass(y, 7000) if name == 'E4' else y, SR)
        from ismail.api import OPS
        out = OPS['mimic_measure'](p, 'bt', folder=rec, check=False)
        assert 'RECORDING WARNINGS (1 of 3' in out and 'E4.wav' in out
        prof = json.load(open(os.path.join(p, 'voices', 'bt.mimic.json'), encoding='utf8'))
        assert list(prof['capture']) == ['E4.wav (E4)']
        quiet = OPS['mimic_measure'](p, 'bt2', folder=rec, check=False, no_top=True)
        assert 'RECORDING WARNINGS' not in quiet
        assert json.load(open(os.path.join(p, 'voices', 'bt2.mimic.json'), encoding='utf8'))['no_top'] is True
        out = OPS['sound_import'](p, 'e4', os.path.join(rec, 'E4.wav'))
        assert 'RECORDING WARNING' in out and '8 kHz' in out
        assert 'WARNING' not in OPS['sound_import'](p, 'a4', os.path.join(rec, 'A4.wav'))
    finally:
        shutil.rmtree(d, ignore_errors=True)
