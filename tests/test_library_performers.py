"""The library's performer voices (guitar/electric, drums/kit70): each preset renders through the rig it was fitted
with, the rigs voice_help lists are valid fx chains, and a lane moves the lead."""
import os

import numpy as np
import pytest
import soundfile as sf

from ismail import api, voices
from ismail.presets import PRESETS

RIGGED = [('strat70_lead', 'electric', 'lead', '0 E4 2 100; 2 G4 2 90'),
          ('strat70_rhythm', 'electric', 'rhythm_vibe', '0 E3 4 90; 0 B3 4 90; 0 E4 4 90'),
          ('strat70_rotary', 'electric', 'rhythm_rotary', '0 A2 4 90; 0 E3 4 90; 0 A3 4 90'),
          ('pbass70', 'electric', 'bass', '0 E1 1 100; 1 E1 1 90; 2 G1 1 100; 3 A1 1 90'),
          ('kit70', 'kit70', 'kit', '0 C2 1 110; 1 D2 1 100; 2 C2 1 110; 3 D2 1 100; 0 F#2 0.5 70; 0.5 F#2 0.5 60')]


def song(root, preset, voice, rig, notes):
    api.project_new(root, bpm=120, length_bars=1)
    api.track_add(root, 'p', instrument=f'preset:{preset}')
    for f in voices.load(voice).INFO['rigs'][rig]['fx']:
        api.fx_add(root, 'p', f)
    api.notes_write(root, 'p', 1, notes)
    return root


@pytest.mark.parametrize('preset, voice, rig, notes', RIGGED)
def test_preset_renders_through_its_rig(tmp_path, preset, voice, rig, notes):
    assert voices.load(voice).INFO['rigs'][rig]['preset'] == preset and preset in PRESETS
    root = song(str(tmp_path / 's'), preset, voice, rig, notes)
    api.render(root)
    y, sr = sf.read(os.path.join(root, 'renders', 'latest.wav'))
    assert np.isfinite(y).all()
    rms = np.sqrt(np.mean(y[:2 * sr] ** 2))
    assert rms > 1e-3 and np.abs(y).max() <= 1.0, (preset, rms)


def test_voice_help_prints_the_rigs():
    out = api.voice_help(name='electric')
    assert 'preset:strat70_lead' in out and '"type": "wah"' in out
    assert 'preset:kit70' in api.voice_help(name='kit70')


def test_lead_bends_on_its_lane(tmp_path):
    def peak_hz(x, sr):
        x = x.mean(1)
        S = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        f = np.fft.rfftfreq(len(x), 1 / sr)
        S[f < 150] = 0
        return f[np.argmax(S)]
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=1)
    api.track_add(root, 'p', instrument='preset:strat70_lead')
    api.notes_write(root, 'p', 1, '0 A4 4 100')
    api.automation_set(root, 'p', 'inst.lane.bend', [[1, 0], [1.5, 0], [1.5625, 2]])
    api.render(root)
    y, sr = sf.read(os.path.join(root, 'renders', 'latest.wav'))
    flat, bent = peak_hz(y[int(0.1 * sr):int(0.45 * sr)], sr), peak_hz(y[int(1.2 * sr):int(1.9 * sr)], sr)
    ratio = bent / flat
    assert abs(12 * np.log2(ratio) - 2) < 0.6, (flat, bent)


def test_flute_renders_in_a_song_and_follows_its_lanes(tmp_path):
    """The flute performer (no rig, no preset) through a track: it renders, and its bend lane moves the pitch."""
    def peak_hz(x, sr):
        x = x.mean(1)
        S = np.abs(np.fft.rfft(x * np.hanning(len(x)), 4 * len(x)))
        f = np.fft.rfftfreq(4 * len(x), 1 / sr)
        S[f < 150] = 0
        return f[np.argmax(S)]
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=1)
    api.track_add(root, 'p', instrument={'type': 'code', 'voice': 'flute', 'tail': 1.5, 'params': {'vib': 0}})
    api.notes_write(root, 'p', 1, '0 A4 4 90')
    api.automation_set(root, 'p', 'inst.lane.bend', [[1, 0], [1.5, 0], [1.5625, 2]])
    api.render(root)
    y, sr = sf.read(os.path.join(root, 'renders', 'latest.wav'))
    assert np.isfinite(y).all() and 1e-3 < np.sqrt(np.mean(y[:2 * sr] ** 2)) and np.abs(y).max() <= 1.0
    flat, bent = peak_hz(y[int(0.3 * sr):int(0.95 * sr)], sr), peak_hz(y[int(1.2 * sr):int(1.9 * sr)], sr)
    assert abs(12 * np.log2(bent / flat) - 2) < 0.3, (flat, bent)


def test_trumpet_renders_in_a_song_and_follows_its_lanes(tmp_path):
    """The trumpet performer (no rig, no preset) through a track: it renders, and its bend lane moves the pitch."""
    def peak_hz(x, sr, f0):
        x = x.mean(1)
        S = np.abs(np.fft.rfft(x * np.hanning(len(x)), 4 * len(x)))
        f = np.fft.rfftfreq(4 * len(x), 1 / sr)
        S[(f < 0.8 * f0) | (f > 1.3 * f0)] = 0                  # the fundamental's band (the trumpet's H1 is weak)
        return f[np.argmax(S)]
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=1)
    api.track_add(root, 'p', instrument={'type': 'code', 'voice': 'trumpet', 'tail': 1.5})
    api.notes_write(root, 'p', 1, '0 A4 4 100')
    api.automation_set(root, 'p', 'inst.lane.bend', [[1, 0], [1.5, 0], [1.5625, 2]])
    api.render(root)
    y, sr = sf.read(os.path.join(root, 'renders', 'latest.wav'))
    assert np.isfinite(y).all() and 1e-3 < np.sqrt(np.mean(y[:2 * sr] ** 2)) and np.abs(y).max() <= 1.0
    flat, bent = peak_hz(y[int(0.3 * sr):int(0.95 * sr)], sr, 440), peak_hz(y[int(1.2 * sr):int(1.9 * sr)], sr, 494)
    assert abs(12 * np.log2(bent / flat) - 2) < 0.3, (flat, bent)
