"""A sampler with several sounds on one pitch (ledger:M218, the Live DJ's handoff item 55): 'sounds': [a, b, c] with
'select': round_robin | random | velocity. The pick is keyed on where the note sits in the song, so a window
render, a live deck and the whole song play the same sound for the same note. One 'sound' plays as it always has."""
import json
import os

import numpy as np
import pytest

from ismail import api, instruments as I
from ismail.render import write_wav
from ismail.live import parity
from ismail.live.engine import BLOCK, Engine, _bar_groups
from ismail.render import Renderer

SR = 44100
FREQS = {'hit_a': 300.0, 'hit_b': 600.0, 'hit_c': 1200.0}


def _sounds(root, tmp):
    """Three short hits told apart by pitch."""
    t = np.arange(int(0.12 * SR)) / SR
    for name, f in FREQS.items():
        y = np.sin(2 * np.pi * f * t) * np.exp(-t * 20) * 0.5
        path = os.path.join(tmp, f'{name}.wav')
        write_wav(path, np.vstack([y, y]), SR)
        api.sound_import(root, name, path)


def _which(y, s0):
    """The hit sounding at sample s0, by its strongest frequency."""
    seg = y[0, s0:s0 + 2048] * np.hanning(2048)
    f = np.argmax(np.abs(np.fft.rfft(seg))) * SR / 2048
    return min(FREQS, key=lambda k: abs(FREQS[k] - f))


def _song(tmp_path, inst, notes, bars=4, start_bar=1, repeat=None):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=bars)
    _sounds(root, str(tmp_path))
    api.track_add(root, 'p', instrument=inst)
    api.notes_write(root, 'p', start_bar, notes, repeat=repeat if repeat is not None else bars - start_bar + 1)
    return root


def _notes(beats, vel=100, pitch=36):
    return [(b * 0.5, pitch, 0.25, vel) for b in beats]          # 120 BPM: a beat is 0.5 s


# ------------------------------------------------------------------ the instrument

def test_one_sound_is_as_it_was():
    full = I.normalize({'type': 'sampler', 'sound': 'x'})
    assert full == I.deep_merge(I.SAMPLER_DEFAULT, {'type': 'sampler', 'sound': 'x'})
    assert 'sounds' not in full and 'select' not in full


def test_the_sounds_and_select_are_checked():
    full = I.normalize({'type': 'sampler', 'sounds': ['a', {'sound': 'b', 'gain_db': -3}]})
    assert full['select'] == 'round_robin' and full['sound'] is None
    assert full['sounds'] == [{'sound': 'a', 'vel': None, 'gain_db': 0.0}, {'sound': 'b', 'vel': None, 'gain_db': -3.0}]
    assert I.normalize(full) == full                                              # normalizing twice changes nothing
    bad = [({'sound': 'a', 'sounds': ['b']}, 'not both'),
           ({'sounds': ['a'], 'select': 'shuffle'}, 'round_robin, random, velocity'),
           ({'sound': 'a', 'select': 'random'}, 'nothing to pick'),
           ({'sounds': []}, 'is a list'),
           ({'sounds': [{'sound': 'a', 'pitch': 3}]}, 'unknown keys'),
           ({'sounds': [{'sound': 'a', 'vel': [0, 60]}]}, "for select='velocity'"),
           ({'sounds': [{'sound': 'a', 'vel': [0, 60]}, 'b'], 'select': 'velocity'}, 'or none'),
           ({'sounds': [{'sound': 'a', 'vel': [90, 60]}], 'select': 'velocity'}, 'lo <= hi')]
    for spec, msg in bad:
        with pytest.raises(I.InstrumentError, match=msg):
            I.normalize(dict(spec, type='sampler'))


def test_round_robin_takes_turns_and_carries_on_from_bar_to_bar():
    p = I.normalize({'type': 'sampler', 'sounds': ['a', 'b', 'c']})
    # two hits a bar (beats 1 and 3) over three bars: every sound plays and no hit repeats the one before
    picks = I.sampler_picks(p, _notes([0, 2, 4, 6, 8, 10]))
    assert picks == [0, 1, 2, 0, 1, 2]
    # four a bar: 0 1 2 0 | 1 2 0 1
    assert I.sampler_picks(p, _notes(range(8))) == [0, 1, 2, 0, 1, 2, 0, 1]
    # the order notes arrive in does not matter, their place does
    assert I.sampler_picks(p, _notes([2, 0, 6, 4])) == [1, 0, 0, 2]


def test_random_is_the_same_draw_for_the_same_place():
    p = I.normalize({'type': 'sampler', 'sounds': ['a', 'b', 'c'], 'select': 'random'})
    notes = _notes(np.arange(64) * 0.5)
    a = I.sampler_picks(p, notes)
    assert a == I.sampler_picks(p, notes) and set(a) == {0, 1, 2}
    # the same note seen from a window starting at beat 8 (sample 0 is song beat 8) draws the same
    tail = [(st - 4.0, m, d, v) for st, m, d, v in notes if st >= 4.0]
    assert I.sampler_picks(p, tail, beat0=8.0) == a[16:]


def test_velocity_layers():
    p = I.normalize({'type': 'sampler', 'select': 'velocity',
                     'sounds': [{'sound': 'soft', 'vel': [0, 70]}, {'sound': 'hard', 'vel': [71, 127]}]})
    assert I.sampler_picks(p, [(0.0, 36, 0.1, 40), (0.5, 36, 0.1, 100), (1.0, 36, 0.1, 71)]) == [0, 1, 1]
    even = I.normalize({'type': 'sampler', 'select': 'velocity', 'sounds': ['p', 'mf', 'f']})
    assert [l['vel'] for l in even['sounds']] == [[0, 42], [43, 85], [86, 127]]
    # overlapping ranges: the sounds that hold the velocity take turns
    rr = I.normalize({'type': 'sampler', 'select': 'velocity',
                      'sounds': [{'sound': 'a', 'vel': [0, 127]}, {'sound': 'b', 'vel': [0, 127]},
                                 {'sound': 'hard', 'vel': [120, 127]}]})
    assert I.sampler_picks(rr, _notes(range(4), vel=90)) == [0, 1, 0, 1]
    assert I.select_cycle(rr) == 6                      # 2 sounds share 0-119, 3 share 120-127
    assert I.select_cycle({'type': 'sampler', 'sounds': ['a', 'b', 'c']}) == 3
    assert I.select_cycle({'type': 'kit', 'map': {'36': {'type': 'sampler', 'sounds': ['a', 'b']},
                                                  '38': {'type': 'snare'}}}) == 2
    assert I.select_cycle({'type': 'sampler', 'sound': 'a'}) == 0


def test_a_render_plays_each_sound_with_its_gain(tmp_path):
    root = _song(tmp_path, {'type': 'sampler', 'one_shot': True, 'pitch_track': False,
                            'sounds': ['hit_a', 'hit_b', 'hit_c']}, '0 C2 0.5 100; 2 C2 0.5 100')
    d = json.load(open(os.path.join(root, 'project.json')))
    y, _ = Renderer(d, root, None, None, None, False).run()
    hits = [int(round(b * 0.5 * SR)) for b in (0, 2, 4, 6, 8, 10, 12, 14)]
    assert [_which(y, s) for s in hits] == ['hit_a', 'hit_b', 'hit_c', 'hit_a', 'hit_b', 'hit_c', 'hit_a', 'hit_b']
    one = I.normalize({'type': 'sampler', 'sound': 'hit_a'})
    many = I.normalize({'type': 'sampler', 'sounds': ['hit_a']})
    quiet = I.normalize({'type': 'sampler', 'sounds': [{'sound': 'hit_a', 'gain_db': -6}]})
    r = Renderer(d, root, None, None, None, False)          # sets the sound resolver for this song
    notes = [(0.0, 60, 0.1, 100), (0.5, 64, 0.1, 80)]
    a = I.render_sampler(one, notes, SR, None, SR)
    assert np.array_equal(a, I.render_sampler(many, notes, SR, None, SR))     # one sound in a list plays the same
    assert np.allclose(I.render_sampler(quiet, notes, SR, None, SR), a * 10 ** (-6 / 20))
    assert r is not None


def test_a_window_picks_the_sounds_the_whole_song_does(tmp_path):
    kit = {'type': 'kit', 'map': {'C2': {'type': 'sampler', 'one_shot': True, 'pitch_track': False,
                                         'sounds': ['hit_a', 'hit_b', 'hit_c']},
                                  'D2': {'type': 'sampler', 'one_shot': True, 'pitch_track': False, 'select': 'random',
                                         'sounds': ['hit_a', 'hit_b', 'hit_c']}}}
    root = _song(tmp_path, kit, '0 C2 0.5 100; 1.5 D2 0.5 90; 2 C2 0.5 100; 3 D2 0.5 90', bars=6)
    d = json.load(open(os.path.join(root, 'project.json')))
    full, _ = Renderer(d, root, None, None, None, False).run()
    win, _ = Renderer(d, root, 4, 6, None, False).run()
    bar = 4 * int(0.5 * SR)
    a, b = full[:, 3 * bar:5 * bar - 2205], win[:, :2 * bar - 2205]
    assert 10 * np.log10(np.mean((a - b) ** 2) + 1e-30) - 10 * np.log10(np.mean(a ** 2)) < -100
    # and the kit's round robin carries on across bars: a b | c a | b c
    assert [_which(full, k * bar + int(round(x * 0.5 * SR))) for k in range(3) for x in (0, 2)] ==         ['hit_a', 'hit_b', 'hit_c', 'hit_a', 'hit_b', 'hit_c']


def test_a_deck_plays_the_sounds_the_studio_does(tmp_path):
    """The live engine renders a multi-sound sampler a bar per event, keyed on the deck's song beat."""
    inst = {'type': 'sampler', 'root': 'C4', 'sounds': ['hit_a', 'hit_b', 'hit_c']}
    root = _song(tmp_path, inst, '0 C4 0.5 100; 1 E4 0.5 90; 2 G4 0.5 100; 3 C4 0.5 80', start_bar=2, repeat=2)
    res, _, _ = parity.run(root, [2, 3])
    assert parity.verdict(res['track:p']) == 'ok' and res['track:p']['resid_db'] < -80, res['track:p']
    rnd = _song(tmp_path / 'r', dict(inst, select='random'), '0 C4 0.5 100; 1 E4 0.5 90; 2.5 G4 0.5 100',
                start_bar=2, repeat=2)
    res, _, _ = parity.run(rnd, [2, 3])
    assert parity.verdict(res['track:p']) == 'ok' and res['track:p']['resid_db'] < -80, res['track:p']


def test_live_groups_a_bar_per_event():
    notes = [(0.0, 36, 0.5, 100), (2.0, 36, 0.5, 100), (4.0, 36, 0.5, 100), (5.5, 38, 0.5, 90), (3.9999999, 36, 0.1, 9)]
    assert _bar_groups(notes, 4) == [[0, 1], [4, 2, 3]]


def test_a_live_loop_carries_the_turns_on_from_pass_to_pass(tmp_path):
    """A looped one-bar clip plays its hits as the song would if the bar were written out again (a b | c a | b c),
    not the first pass over and over (the beatbox kit's complaint, 10-16)."""
    root = _song(tmp_path, {'type': 'sampler', 'sound': 'hit_a'}, '0 C2 0.5 100')
    eng = Engine(root, bpm=120, bpb=4, workers=0, device='none')
    try:
        eng.cmd_track('p', instrument={'type': 'sampler', 'one_shot': True, 'pitch_track': False,
                                       'sounds': ['hit_a', 'hit_b', 'hit_c']})
        eng.cmd_queue([{'track': 'p', 'notes': '0 C2 0.5 100; 2 C2 0.5 100', 'bars': 1, 'at': 'next_bar'}])
        for _ in range(int(11.0 * SR / BLOCK)):           # bars 2 to 5 at 2 s a bar
            eng.tick()
            eng.mix_block()
        y = eng.air[:, :eng.pos]
        got = [_which(y, int(round((4 + 2 * k) * 0.5 * SR))) for k in range(8)]
    finally:
        eng.shutdown()
    assert got == ['hit_a', 'hit_b', 'hit_c', 'hit_a', 'hit_b', 'hit_c', 'hit_a', 'hit_b']
