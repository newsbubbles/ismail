"""track_freeze (ledger:M292): some tracks rendered into one audio track, and placed audio on a live deck. A deck
skipped every audio clip, so the Live DJ hand-built a song voice to carry a frozen bed (stream set 1022); a frozen
track is an audio track, and live_load plays audio tracks through the placed_audio voice, sample for sample.
Renders stay tiny: 4 bars at 120 BPM, a kick and a short synthesized sound."""
import json
import os

import numpy as np
import pytest
import soundfile as sf

from ismail import api
from ismail.api import OpError
from ismail.dsp import SR
from ismail.live import decks as D, parity
from ismail.api_freeze import track_freeze as freeze
from ismail.render import Renderer

BAR_S = 2.0                     # 4 beats at 120 BPM


def song(root):
    """Track a: a placed sound at bar 3. Track b: kicks in bar 2 only. Track c: a part that stays live."""
    api.project_new(root, bpm=120, length_bars=4)
    api.sound_make(root, 'tone', 'preset:pluck', notes='0 C3 1; 1 G3 1', tail_sec=0.3)
    api.track_add(root, 'a')
    api.audio_place(root, 'a', 'tone', 3, gain_db=-3)
    api.track_add(root, 'b', instrument='preset:kick')
    api.notes_write(root, 'b', 2, '0 C1 1 110; 2 C1 1 100')
    api.track_set(root, 'b', volume_db=-4, pan=0.3)
    api.track_add(root, 'c', instrument='preset:kick')
    api.notes_write(root, 'c', 1, '0 C1 1 90')
    return root


def proj(root):
    with open(os.path.join(root, 'project.json'), encoding='utf8') as f:
        return json.load(f)


def mix(root):
    return Renderer(proj(root), root, cache=False).run()[0]


def onset_s(y, from_s=0.0, thresh=1e-3):
    i0 = int(from_s * SR)
    return (i0 + int(np.argmax(np.abs(y[:, i0:]).max(0) > thresh))) / SR


def test_freeze_renders_tracks_into_one_audio_track_bar_aligned_and_the_song_sounds_the_same(tmp_path):
    root = song(str(tmp_path / 's'))
    before = mix(root)
    out = freeze(root, ['a', 'b'], into='bed')
    assert "froze a, b into 'bed'" in out and 'originals muted: a, b' in out
    d = proj(root)
    bed = d['tracks']['bed']
    assert bed['instrument'] is None and bed['frozen']['tracks'] == ['a', 'b'] and bed['frozen']['muted'] == ['a', 'b']
    assert d['tracks']['a']['mute'] and d['tracks']['b']['mute'] and not d['tracks']['c']['mute']
    assert bed['audio'] == [{'sound': 'frozen_bed', 'at_beat': 0, 'gain_db': 0.0, 'offset_sec': 0.0,
                             'length_beats': None}]
    y, sr = sf.read(os.path.join(root, d['sounds']['frozen_bed']['file']), always_2d=True)
    assert sr == SR and abs(y.shape[0] - int((4 * BAR_S + d['tail_sec']) * SR)) <= 1      # 4 bars + the tail
    # bar 1 at 0 s: nothing before the kick on bar 2, the placed sound comes in on bar 3
    assert np.abs(y[:int(BAR_S * SR) - 1]).max() < 1e-6
    assert abs(onset_s(y.T) - BAR_S) < 0.002
    # the mix (pan and fader of b, gain of a's clip) is in the file: the song renders as before
    after = mix(root)
    assert after.shape == before.shape and np.abs(after - before).max() < 1e-4


def test_a_deck_loads_the_frozen_track_and_plays_it_as_the_studio_does(tmp_path):
    root = song(str(tmp_path / 's'))
    freeze(root, ['a', 'b'], into='bed')
    info, _, tracks, clips, skipped = D.read_song(root, 'X', None, 4)
    bed = next(t for t in tracks if t['track'] == 'X.bed')
    assert bed['instrument']['voice'] == 'placed_audio' and bed['instrument']['performer']
    # one gate note per bar, on the bar lines
    assert next(c for c in clips if c['track'] == 'X.bed')['notes'] == '0 60 4 100; 4 60 4 100; 8 60 4 100; 12 60 4 100'
    assert skipped['placed audio'] == 0 and 'bed' not in skipped['no instrument'] and info['audio_tracks'] == ['bed']
    res, takes, head = parity.run(root, [1, 4])
    assert 'placed audio' not in head
    for k in ('track:bed', 'mix'):
        assert parity.verdict(res[k]) == 'ok' and res[k]['resid_db'] < -80, (k, res[k])
    studio, live = takes['track:bed']
    assert live.shape[1] >= int(4 * BAR_S * SR) and abs(onset_s(live) - BAR_S) < 0.002


def test_a_deck_section_starting_inside_a_placed_clip_plays_from_the_right_place(tmp_path):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=4)
    api.sound_make(root, 'long', 'preset:pad', notes='0 C3 6', tail_sec=0.2)
    api.track_add(root, 'a')
    api.audio_place(root, 'a', 'long', 1.5)
    res, _, _ = parity.run(root, [2, 3])
    assert parity.verdict(res['track:a']) == 'ok' and res['track:a']['resid_db'] < -80, res['track:a']


def test_placed_audio_beside_notes_is_still_reported_as_left_behind(tmp_path):
    root = song(str(tmp_path / 's'))
    api.audio_place(root, 'c', 'tone', 2)
    _, _, tracks, _, skipped = D.read_song(root, 'X', None, 4)
    assert skipped['placed audio'] == 1 and 'X.a' in [t['track'] for t in tracks]


def test_refreeze_replaces_the_frozen_track_and_lifts_its_old_mutes(tmp_path):
    root = song(str(tmp_path / 's'))
    freeze(root, ['a', 'b'], into='bed')
    api.track_set(root, 'bed', volume_db=-2)
    out = freeze(root, ['a'], into='bed', bars=[2, 3])
    assert "'bed' (replaced audio track): bars 2-3" in out and 'on again' in out and 'b' in out.split('on again')[1]
    d = proj(root)
    assert list(d['tracks']).count('bed') == 1 and d['tracks']['bed']['frozen']['tracks'] == ['a']
    assert d['tracks']['bed']['volume_db'] == -2                      # its own fader stays
    assert d['tracks']['a']['mute'] and not d['tracks']['b']['mute']
    assert d['tracks']['bed']['audio'][0]['at_beat'] == 4              # bar 2 is the file's 0 s
    y, _ = sf.read(os.path.join(root, 'sounds', 'frozen_bed.wav'), always_2d=True)
    assert abs(y.shape[0] - int((2 * BAR_S + d['tail_sec']) * SR)) <= 1
    # bar 3 of the song (the placed sound) is 1 bar into the file
    assert abs(onset_s(y.T) - BAR_S) < 0.002
    freeze(root, ['b'], into='bed2', originals='keep')
    assert not proj(root)['tracks']['b']['mute']


def test_what_every_frozen_track_shares_comes_over_and_the_rest_is_named(tmp_path):
    root = song(str(tmp_path / 's'))
    api.bus_add(root, 'verb', fx=[{'type': 'reverb', 'mix': 1.0}])
    api.bus_add(root, 'grp')
    api.track_set(root, 'a', sends={'verb': -10}, output='grp')
    api.track_set(root, 'b', sends={'verb': -10}, output='grp')
    freeze(root, ['a', 'b'], into='bed')
    bed = proj(root)['tracks']['bed']
    assert bed['output'] == 'grp' and bed['sends'] == {'verb': -10}
    api.track_set(root, 'b', sends={'verb': -4}, output='master')
    out = freeze(root, ['a', 'b'], into='bed')
    bed = proj(root)['tracks']['bed']
    assert bed['output'] == 'master' and bed['sends'] == {}
    assert 'a -> grp, b -> master' in out and 'sends to verb differ (a -10, b -4): not sent' in out


def test_freeze_errors_point_forward(tmp_path):
    root = song(str(tmp_path / 's'))
    with pytest.raises(OpError, match=r"no track 'zz'; tracks: \['a', 'b', 'c'\]"):
        freeze(root, ['a', 'zz'])
    with pytest.raises(OpError, match='exists and is not a frozen track'):
        freeze(root, ['a'], into='c')
    with pytest.raises(OpError, match='the song has bars 1-4'):
        freeze(root, ['a'], bars=[3, 9])
    with pytest.raises(OpError, match="originals: 'mute'"):
        freeze(root, ['a'], originals='solo')
