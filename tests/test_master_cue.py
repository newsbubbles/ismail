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


def _dense(path, secs=4.0, sr=48000, seed=1):
    """A dense mix: detuned saw chords over a 128 BPM kick, snare and 16th hats, at -18.6 LUFS (the planet rock
    remix's mix level), with a low crest so the limiter's re-gain rounds fall short of the ceiling (ledger:M280)."""
    import pyloudnorm as pyln
    rng = np.random.default_rng(seed)
    t = np.arange(int(secs * sr)) / sr
    y = np.zeros((len(t), 2))
    for f in (55, 110, 165.0, 277.2, 330, 523.3, 659.3, 880):
        for ch, det in ((0, 0.996), (1, 1.004)):
            y[:, ch] += 0.4 * (2 * ((f * det * t + rng.random()) % 1.0) - 1)
    step = int(round(sr * 60 / 128 / 4))
    for k, s in enumerate(range(0, len(t), step)):
        n = min(len(t) - s, int(0.2 * sr))
        tt = np.arange(n) / sr
        if k % 4 == 0:
            y[s:s + n] += (0.3 * np.sin(2 * np.pi * (45 * tt + 150 * (1 - np.exp(-tt * 35)) / 35))
                           * np.exp(-tt * 10))[:, None]
        if k % 8 == 4:
            hit = 0.5 * rng.standard_normal(n) * np.exp(-tt * 22)
            y[s:s + n, 0] += hit
            y[s:s + n, 1] += np.roll(hit, 11)
        n2 = min(len(t) - s, int(0.02 * sr))
        y[s:s + n2] += 0.25 * rng.standard_normal((n2, 2)) * np.exp(-np.arange(n2) / sr * 250)[:, None]
    y *= 10 ** ((-18.6 - pyln.Meter(sr).integrated_loudness(y)) / 20)
    sf.write(path, y, sr, subtype='FLOAT')
    return path, M.true_peak_db(y) + 18.6           # the peak-to-loudness ratio


def _lufs(path):
    import pyloudnorm as pyln
    y, sr = sf.read(path, always_2d=True)
    return pyln.Meter(sr).integrated_loudness(y)


def test_a_dense_mix_ends_at_its_true_peak_ceiling(tmp_path):
    """ledger:M280: 3 dB of limiting asked on a dense mix. The limiter's rounds alone stop 0.3 dB over (the
    master before the catch); now the wav ends at -1 dBTP, and with the mp3 margin under it."""
    src, plr = _dense(str(tmp_path / 'mix.wav'))
    lufs = -1.0 + 3.0 - plr
    OPS['master_cue'](source=src, lufs=lufs, tp=-1.0, out=str(tmp_path / 'old.wav'), mp3_margin_db=0, tp_catch=False)
    y, _ = sf.read(str(tmp_path / 'old.wav'), always_2d=True)
    assert M.true_peak_db(y) > -0.9                 # the shortfall the catch is for
    out = OPS['master_cue'](source=src, lufs=lufs, tp=-1.0, out=str(tmp_path / 'wav.wav'), mp3_margin_db=0)
    y, _ = sf.read(str(tmp_path / 'wav.wav'), always_2d=True)
    assert M.true_peak_db(y) <= -1.0 + 0.05
    assert 'WARNING' not in out
    OPS['master_cue'](source=src, lufs=lufs, tp=-1.0, out=str(tmp_path / 'm.wav'))
    y, _ = sf.read(str(tmp_path / 'm.wav'), always_2d=True)
    assert M.true_peak_db(y) <= -1.0 - M.MP3_MARGIN_DB + 0.05
    out = OPS['master_cue'](source=src, lufs=lufs + 1, tp=-1.0, out=str(tmp_path / 'loud.wav'), mp3_margin_db=0)
    y, _ = sf.read(str(tmp_path / 'loud.wav'), always_2d=True)
    assert 'true-peak catch took' in out           # 4 dB asked: the rounds stop over, the catch takes the rest
    assert M.true_peak_db(y) <= -1.0 + 0.05


def test_the_320k_mp3_of_the_default_master_stays_under_the_ceiling(tmp_path):
    import shutil
    import subprocess
    exe = os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg')
    if not exe:
        pytest.skip("no ffmpeg on PATH (CI installs it on Linux only): the mp3 encoder ismail uses is ffmpeg's lame")
    from ismail.render import write_mp3
    src, plr = _dense(str(tmp_path / 'mix.wav'))
    wav = str(tmp_path / 'm.wav')
    OPS['master_cue'](source=src, lufs=-1.0 + 3.0 - plr, tp=-1.0, out=wav)
    write_mp3(wav, str(tmp_path / 'm.mp3'))
    subprocess.run([exe, '-y', '-loglevel', 'error', '-i', str(tmp_path / 'm.mp3'), '-c:a', 'pcm_f32le',
                    str(tmp_path / 'dec.wav')], check=True)
    d, _ = sf.read(str(tmp_path / 'dec.wav'), always_2d=True)
    assert M.true_peak_db(d) <= -1.0


def test_a_mix_already_under_the_ceiling_keeps_its_loudness(tmp_path):
    """The catch and the margin must not over-limit: a master whose gain alone peaks 0.3 dB under -1 dBTP loses
    under 0.3 LU against the master before the catch."""
    src, plr = _dense(str(tmp_path / 'mix.wav'), seed=2)
    lufs = -1.0 - 0.3 - plr
    OPS['master_cue'](source=src, lufs=lufs, out=str(tmp_path / 'old.wav'), mp3_margin_db=0, tp_catch=False)
    out = OPS['master_cue'](source=src, lufs=lufs, out=str(tmp_path / 'new.wav'))
    assert _lufs(str(tmp_path / 'old.wav')) - _lufs(str(tmp_path / 'new.wav')) < 0.3
    assert 'WARNING' not in out


def test_the_catch_alone_takes_only_what_is_over(tmp_path):
    sr = 48000
    t = np.arange(sr) / sr
    y = np.stack([0.5 * np.sin(2 * np.pi * 440 * t)] * 2, 1)
    y[sr // 2:sr // 2 + 20] = 0.99                   # one burst over a -6 dB tone
    c, gr = M.catch(y, sr, -3.0)
    assert M.true_peak_db(c) <= -3.0 and gr < -2.5
    assert np.array_equal(c[:sr // 4], y[:sr // 4])          # untouched before the burst
    assert np.abs(c[-sr // 4:] - y[-sr // 4:]).max() < 1e-3   # released 250 ms after it
    same, gr0 = M.catch(y * 0.5, sr, -3.0)
    assert gr0 == 0.0 and np.array_equal(same, y * 0.5)
