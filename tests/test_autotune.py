"""autotune: the voice's pitch moved onto notes, its character kept. Pitch is read back with librosa's pYIN, an
analysis independent of the WORLD one the effect uses."""
import numpy as np
import pytest

from ismail import fx as fxmod
from ismail import tune

SR = 44100


def voice(f0_curve, sr=SR):
    """A voice-like test tone: a harmonic series under three vowel-like resonances, following f0_curve (Hz)."""
    ph = 2 * np.pi * np.cumsum(f0_curve) / sr
    out = np.zeros_like(ph)
    for k in range(1, 40):
        f = k * np.median(f0_curve)
        if f > 6000:
            break
        w = sum(g / (1 + ((f - fc) / bw) ** 2) for fc, bw, g in ((700, 130, 1.0), (1200, 150, 0.6), (2600, 250, 0.3)))
        out += (w + 0.02) / k * np.sin(k * ph)
    return 0.3 * out / np.max(np.abs(out))


def cents_track(y, sr=SR):
    import librosa
    f0, vf, _ = librosa.pyin(y.astype(np.float32), fmin=150, fmax=800, sr=sr, frame_length=2048)
    f = f0[vf & np.isfinite(f0)]
    return 1200 * np.log2(f / 440.0)          # cents from A4


def steady(hz, s=1.0):
    return np.full(int(SR * s), float(hz))


def test_key_and_scale_are_checked_without_world():
    with pytest.raises(fxmod.FxError, match='key'):
        fxmod.normalize({'type': 'autotune', 'key': 'H'})
    with pytest.raises(fxmod.FxError, match='scale'):
        fxmod.normalize({'type': 'autotune', 'scale': 'lydian dominant'})
    assert tune.scale_classes('A', 'minor') == [0, 2, 4, 5, 7, 9, 11]
    assert tune.scale_classes('F#', [0, 7]) == [1, 6]


def test_a_sharp_note_snaps_to_the_scale_note():
    pytest.importorskip('pyworld')
    sung = 440 * 2 ** (46 / 1200)                     # A4, 46 cents sharp
    before = np.median(cents_track(voice(steady(sung))))
    y, r = tune.retune(voice(steady(sung)), SR, key='C', scale='major', speed_ms=0)
    after = cents_track(y)
    assert abs(before - 46) < 8                        # the test tone is what we think it is
    assert abs(np.median(after)) < 8                   # now on A4
    assert 30 < r['median_cents_moved'] < 60


def test_retune_speed_keeps_the_vibrato_or_takes_it_away():
    pytest.importorskip('pyworld')
    t = np.arange(int(SR * 1.5)) / SR
    curve = 446 * 2 ** (35 * np.sin(2 * np.pi * 5.5 * t) / 1200)    # 24 cents sharp of A4, 35 cents of vibrato
    hard, _ = tune.retune(voice(curve), SR, key='A', scale='major', speed_ms=0)
    soft, _ = tune.retune(voice(curve), SR, key='A', scale='major', speed_ms=150)
    ch, cs = cents_track(hard), cents_track(soft)
    assert np.std(ch) < 10                             # the hard snap flattens the vibrato (the robot sound)
    assert np.std(cs) > 15                             # a slow retune keeps it
    assert abs(np.median(ch)) < 10 and abs(np.median(cs)) < 15        # both pull the centre onto A4


def test_notes_from_a_part_lead_the_voice_in_its_own_octave():
    pytest.importorskip('pyworld')
    # sung at 300 Hz throughout; the part plays D3 then E3 (an octave below): the voice goes to D4 then E4
    y, _ = tune.retune(voice(steady(300.0, 1.2)), SR, notes=[(0.0, 50, 0.6, 100), (0.6, 52, 0.6, 100)], speed_ms=0)
    half = len(y) // 2
    d4, e4 = 1200 * np.log2(293.66 / 440), 1200 * np.log2(329.63 / 440)
    assert abs(np.median(cents_track(y[:half - 2000])) - d4) < 10
    assert abs(np.median(cents_track(y[half + 2000:])) - e4) < 10


def test_formant_moves_the_voice_not_its_pitch():
    pytest.importorskip('pyworld')
    x = voice(steady(440.0))

    def centroid(y):
        s = np.abs(np.fft.rfft(y * np.hanning(len(y))))
        f = np.fft.rfftfreq(len(y), 1 / SR)
        return float((s * f).sum() / s.sum())
    same, _ = tune.retune(x, SR, speed_ms=0)
    up, _ = tune.retune(x, SR, speed_ms=0, formant=4)
    assert abs(np.median(cents_track(up)) - np.median(cents_track(same))) < 10
    assert centroid(up) > centroid(same) * 1.1


def test_the_effect_in_a_chain_follows_another_tracks_notes():
    pytest.importorskip('pyworld')

    class Ctx:
        sr = SR
        bpm = 120

        def param(self, idx, name, default):
            return default

        def track_notes(self, name):
            assert name == 'melody'
            return [(0.0, 69, 2.0, 100)]                  # A4
    x = voice(steady(430.0))                              # 40 cents flat
    f = fxmod.normalize({'type': 'autotune', 'notes_from': 'melody', 'speed_ms': 0})
    y = fxmod.apply_fx(np.stack([x, x]), f, Ctx(), 0)
    assert y.shape == (2, len(x))
    assert abs(np.median(cents_track(y[0]))) < 8


def test_a_take_on_disk_comes_back_tuned_beside_it(tmp_path):
    pytest.importorskip('pyworld')
    import soundfile as sf
    from ismail import api_sound
    take = tmp_path / 'take.wav'
    sf.write(str(take), voice(steady(440 * 2 ** (40 / 1200))).astype(np.float32), SR)
    out = api_sound.sound_autotune(str(take), key='A', scale='minor', speed_ms=0)
    assert 'take_tuned.wav' in out and 'moved' in out
    y, _ = sf.read(str(tmp_path / 'take_tuned.wav'))
    assert abs(np.median(cents_track(y))) < 8
    again = api_sound.sound_autotune(str(take), key='A', scale='minor')
    assert 'take_tuned_2.wav' in again                   # never overwritten
