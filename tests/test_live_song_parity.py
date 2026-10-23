"""The standing parity check (ismail.live.parity, op live_parity): every instrument type and library voice plays on a
deck as the studio renders it. Judged on bars 2-3 of a part that starts in bar 2 (bar 1 is silent, so nothing
rings into the window and no note sits at the start of a render): voices that are not performers match sample for
sample, performers (rendered live in bar chunks with context) by ear.
Run after any change to the engine, an instrument or a voice."""
import json
import os

import numpy as np
import pytest

from ismail import api, voices
from ismail.live import parity
from ismail.render import Renderer

MEL = '0 C4 0.5 100; 0.5 E4 0.5 90; 1 G4 1 100; 2 C5 0.5 80; 2.5 B4 0.25 70; 3 G4 1 90'
BASS = '0 C2 0.5 100; 0.5 E2 0.5 90; 1 G2 1 100; 2 C3 0.5 80; 3 G2 1 90'
KIT = '0 C1 1 110; 1 D1 1 100; 2 C1 1 110; 3 D#1 1 100; 0 F#1 0.5 70; 0.5 F#1 0.5 60; 1.5 A#1 0.5 80'
KIT70 = '0 C2 1 110; 1 D2 1 100; 2 C2 1 110; 3 D2 1 100; 0 F#2 0.5 70; 0.5 F#2 0.5 60'
FX = '0 C4 0.5 10; 1 C4 0.5 50; 2 C4 1 70; 3 C4 0.5 40'

GLIDE = pytest.param('acid_bass', BASS, marks=pytest.mark.xfail(strict=True, reason=(
    "a mono synth's phrase glides from the last phrase's pitch and is cut where the next begins; live renders "
    "each phrase alone")))
EXACT = [('saw_lead', MEL), ('pad', MEL), ('pluck', MEL), GLIDE, ('fm_bell', MEL),
         ('noise_riser', '0 C4 4 100'), ('kick', KIT), ('snare', MEL), ('clap', MEL), ('hat_open', MEL),
         ('kit_basic', KIT), ('grand_piano', MEL), ('additive_piano', MEL), ('growl', BASS), ('sfx', FX),
         ({'type': 'code', 'voice': 'transitions', 'tail': 3.0}, '0 C1 1 100; 1 D1 1 100; 2 E1 1 100; 3 F1 1 100'),
         ({'type': 'sprite', 'oscs': [{'wave': 'square'}]}, MEL), ({'type': 'mimic', 'profile': 'cello'}, BASS)]
PERFORMERS = [('strat70_lead', 'electric', 'lead', MEL.replace('4', '3').replace('5', '4')),
              ('pbass70', 'electric', 'bass', BASS), ('kit70', 'kit70', 'kit', KIT70)]


def song(root, instrument, notes, fx=(), sound=None):
    api.project_new(root, bpm=120, length_bars=4)
    if sound:
        api.sound_make(root, sound, 'preset:pluck', notes='0 C4 1')
    api.track_add(root, 'p', instrument=instrument)
    for f in fx:
        api.fx_add(root, 'p', f)
    api.notes_write(root, 'p', 2, notes, repeat=3)
    return root


def name(x):
    return x if isinstance(x, str) else x['type']


@pytest.mark.parametrize('inst, notes', EXACT, ids=[name(p.values[0] if hasattr(p, 'values') else p[0]) for p in EXACT])
def test_a_voice_plays_live_sample_for_sample(tmp_path, inst, notes):
    root = song(str(tmp_path / 's'), f'preset:{inst}' if isinstance(inst, str) else inst, notes)
    res, _, _ = parity.run(root, [2, 3])
    for k in ('track:p', 'mix'):
        assert parity.verdict(res[k]) == 'ok', (k, res[k])
        assert res[k]['resid_db'] < -80, (k, res[k])


def test_a_sampler_plays_live_sample_for_sample(tmp_path):
    root = song(str(tmp_path / 's'), {'type': 'sampler', 'sound': 'tone', 'root': 'C4'}, MEL, sound='tone')
    res, _, _ = parity.run(root, [2, 3])
    assert parity.verdict(res['track:p']) == 'ok' and res['track:p']['resid_db'] < -80, res['track:p']


@pytest.mark.parametrize('preset, voice, rig, notes', PERFORMERS, ids=[p for p, *_ in PERFORMERS])
def test_a_performer_plays_live_as_in_the_studio_by_ear(tmp_path, preset, voice, rig, notes):
    root = song(str(tmp_path / 's'), f'preset:{preset}', notes, voices.load(voice).INFO['rigs'][rig]['fx'])
    res, _, _ = parity.run(root, [2, 3])
    assert parity.verdict(res['track:p']) == 'ok', res['track:p']


def test_a_section_with_a_bus_sends_and_track_effects_plays_live_as_in_the_studio(tmp_path):
    root = song(str(tmp_path / 's'), 'preset:pluck', MEL, [{'type': 'eq', 'bands': [{'freq': 400, 'gain_db': 3}]},
                                                           {'type': 'compressor', 'threshold_db': -20}])
    api.track_add(root, 'd', instrument='preset:kit_basic')
    api.notes_write(root, 'd', 2, KIT, repeat=3)
    api.bus_add(root, 'verb', fx=[{'type': 'reverb', 'mix': 1.0}])
    api.track_set(root, 'p', sends={'verb': -10})
    api.track_set(root, 'd', sends={'verb': -18})
    api.automation_set(root, 'p', 'volume_db', [[2, 0], [3, -6], [4, 0]])
    res, _, head = parity.run(root, [2, 3])
    for k in ('mix', 'track:p', 'track:d', 'bus:verb'):
        assert parity.verdict(res[k]) == 'ok', (k, res[k])
    assert res['track:d']['resid_db'] < -80


@pytest.mark.parametrize('inst, notes', [('snare', MEL), ('saw_lead', MEL), ('sfx', FX)])
def test_a_window_renders_its_bars_as_the_whole_song_does(tmp_path, inst, notes):
    """Random phase and noise are seeded on where a note sits in its bar, and a voice gets each note whole: a
    window render (and so a live deck) sounds as those bars do in the song, not like a new take."""
    root = song(str(tmp_path / 's'), f'preset:{inst}', notes)
    d = json.load(open(os.path.join(root, 'project.json')))
    full, _ = Renderer(d, root, None, None, None, False).run()
    win, _ = Renderer(d, root, 2, 4, None, False).run()
    bar = 2 * 44100
    end = 2 * bar - 2205                # a window fades out over its last few ms
    a, b = full[:, bar:bar + end], win[:, :end]
    assert 10 * np.log10(np.mean((a - b) ** 2) + 1e-30) - 10 * np.log10(np.mean(a ** 2)) < -100


def test_the_op_says_what_differs_and_where_it_goes(tmp_path):
    root = song(str(tmp_path / 's'), 'preset:pluck', MEL)
    out = api.OPS['live_parity'](root, bars=[2, 3])
    assert out.splitlines()[0].endswith('all 3 match') and '(edge)' in out
