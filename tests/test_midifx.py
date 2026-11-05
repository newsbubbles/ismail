"""MIDI effects as data (ismail/midifx.py) and their studio side, track_midi_fx (MIDI parity step 7b): chord, scale,
pitch, velocity, length and random applied in order to each note, dice that repeat for one note, the chain before
the arp, and a track expanded at render (the renderer, decks and MIDI export see the processed notes) or written
out as plain notes. Pure functions plus project files in a temp folder: nothing renders audio."""
import json
import os

import pytest

from ismail import api
from ismail import arp as A
from ismail import midifx as MF
from ismail.api import OpError


def on(chain, m=60, vel=100, beat=0.0):
    return MF.notes_on(MF.configure(chain), m, vel, beat)


def test_chord_adds_intervals_and_scales_the_added_notes():
    assert on([{'type': 'chord', 'intervals': [0, 4, 7]}]) == [(60, 100), (64, 100), (67, 100)]
    assert on([{'type': 'chord', 'intervals': [0, 7, 12], 'velocity': 0.5}]) == [(60, 100), (67, 50), (72, 50)]
    assert on([{'type': 'chord', 'intervals': [0, 7], 'velocity': [0.8, 0.4]}]) == [(60, 80), (67, 40)]
    assert on([{'type': 'chord', 'intervals': [7, 12]}]) == [(67, 100), (72, 100)]        # 0 left out: added only
    assert on([{'type': 'chord', 'intervals': [0, 4, 4]}]) == [(60, 100), (64, 100)]      # given twice, plays once


def test_scale_moves_notes_onto_it_four_ways():
    sc = lambda mode, m: on([{'type': 'scale', 'key': 'A', 'scale': 'minor', 'mode': mode}], m)   # noqa: E731
    assert sc('nearest', 61) == [(60, 100)]                              # C# -> C (ties go down, as the arp snaps)
    assert sc('up', 61) == [(62, 100)] and sc('down', 61) == [(60, 100)]
    assert sc('drop', 61) == [] and sc('drop', 60) == [(60, 100)]
    assert sc('nearest', 69) == [(69, 100)]                              # on the scale: left alone
    # chord then scale: a major triad on A becomes A minor
    assert [m for m, _ in on([{'type': 'chord', 'intervals': [0, 4, 7]},
                              {'type': 'scale', 'key': 'A', 'scale': 'minor'}], 57)] == [57, 60, 64]
    assert on([{'type': 'scale', 'key': 'C', 'scale': [0, 7]}], 64) == [(67, 100)]       # semitones as a scale


def test_pitch_transposes_and_drops_or_folds_outside_its_range():
    assert on([{'type': 'pitch', 'transpose': -12}]) == [(48, 100)]
    rng = {'type': 'pitch', 'lowest': 'C4', 'highest': 'B4'}
    assert on([rng], 55) == [] and on([rng], 62) == [(62, 100)]
    assert on([dict(rng, fold=True)], 43) == [(67, 100)] and on([dict(rng, fold=True)], 86) == [(62, 100)]
    ch = [{'type': 'chord', 'intervals': [0, 12, 24]}, dict(rng, fold=True)]
    assert on(ch) == [(60, 100)]                                         # three Cs fold onto one, played once
    assert on([{'type': 'pitch', 'transpose': 40}], 100) == []           # past 127: dropped


def test_velocity_curve_out_range_fixed_and_random():
    v = lambda fx, vel: on([dict(fx, type='velocity')], vel=vel)[0][1]   # noqa: E731
    assert v({'out': [40, 110]}, 1) == 40 and v({'out': [40, 110]}, 127) == 110
    assert v({'curve': 0.5}, 32) > 60 and v({'curve': -0.5}, 64) < 40     # drive lifts soft notes, below 0 lowers
    assert v({'fixed': 90}, 20) == 90
    rolls = [MF.notes_on(MF.configure([{'type': 'velocity', 'random': 20}]), 60, 64, b / 4)[0][1] for b in range(32)]
    assert all(44 <= r <= 84 for r in rolls) and len(set(rolls)) > 5
    assert v({'fixed': 120, 'out': [1, 100]}, 64) == 100                 # out still bounds it


def test_random_moves_by_choices_times_interval_and_repeats_for_one_note():
    fx = [{'type': 'random', 'chance': 0.5, 'choices': 2, 'interval': 12, 'seed': 7}]
    rolls = [on(fx, beat=b / 4)[0][0] for b in range(64)]
    assert set(rolls) == {60, 72, 84}                                    # stays, or 1 or 2 octaves up
    assert 15 < rolls.count(60) < 49                                     # about half move
    assert rolls == [on(fx, beat=b / 4)[0][0] for b in range(64)]       # the same every time
    assert rolls != [on([dict(fx[0], seed=8)], beat=b / 4)[0][0] for b in range(64)]
    down = {on([dict(fx[0], chance=1, sign='down')], beat=b / 4)[0][0] for b in range(16)}
    assert down == {48, 36}
    both = {on([dict(fx[0], chance=1, sign='both', choices=1)], beat=b / 4)[0][0] for b in range(32)}
    assert both == {48, 72}
    sc = [{'type': 'random', 'chance': 1, 'choices': 1, 'interval': 2, 'key': 'C', 'scale': 'major'}]
    assert on(sc, 60) == [(64, 100)] and on(sc, 61) == [(64, 100)]       # two scale steps up, from on the scale


def test_length_sets_or_scales_and_composes_in_order():
    assert MF.length_rule(MF.configure([{'type': 'length', 'beats': 0.5}])) == (0.5, 1.0)
    assert MF.note_len(MF.configure([{'type': 'length', 'scale': 2}]), 1.5) == 3.0
    two = MF.configure([{'type': 'length', 'beats': 0.5}, {'type': 'length', 'scale': 3}])
    assert MF.note_len(two, 8) == 1.5
    assert on([{'type': 'length', 'beats': 1}]) == [(60, 100)]           # pitches untouched


def test_the_chain_says_what_is_wrong_and_what_to_do():
    for bad, msg in [([], "off=True"), ([{'type': 'arp'}], "track_arp in the studio"),
                     ([{'type': 'chord'}], "intervals, a list"), ([{'type': 'chord', 'intervals': [0], 'x': 1}],
                                                                  "unknown settings \\['x'\\]"),
                     ([{'type': 'scale', 'key': 'A'}], "give key and scale"),
                     ([{'type': 'scale', 'key': 'H', 'scale': 'minor'}], "effect 1 \\(scale\\): key 'H'"),
                     ([{'type': 'scale', 'key': 'A', 'scale': 'minor', 'mode': 'jump'}], "'drop'"),
                     ([{'type': 'pitch', 'lowest': 'C5', 'highest': 'C4'}], "is above highest"),
                     ([{'type': 'pitch', 'lowest': 'C4', 'highest': 'E4', 'fold': True}], "at least 11"),
                     ([{'type': 'velocity', 'out': [100, 40]}], "lo <= hi"),
                     ([{'type': 'velocity', 'fixed': 90, 'curve': 0.5}], "give one of them"),
                     ([{'type': 'length'}], "one of beats"), ([{'type': 'length', 'beats': 1, 'scale': 2}], "one of"),
                     ([{'type': 'random', 'chance': 2}], "chance: 0 to 1"),
                     ([{'type': 'random', 'key': 'A'}], "key and scale go together"),
                     ([{'type': 'chord', 'intervals': [0, 4], 'velocity': [1]}], "one scale per interval"),
                     ([{'type': 'scale', 'key': 'C', 'scale': {'a': 1}}], "scale a name"),
                     ([{'type': 'scale', 'key': 'C', 'scale': [0, 'x']}], "list of whole semitones"),
                     ([{'type': 'random', 'key': ['C'], 'scale': 'major'}], "a note name such as A"),
                     ([{'type': 'pitch', 'fold': 'false'}], "fold True"),
                     ([{'type': 'velocity', 'fixed': True}], "a number, not True"),
                     ([{'type': 'pitch', 'lowest': True}], "a number, not True")]:
        with pytest.raises(MF.MidiFxError, match=msg):
            MF.configure(bad)
    c = MF.configure([{'type': 'chord', 'intervals': [0, 4, 7]}, {'type': 'scale', 'key': 'A', 'scale': 'minor'},
                      {'type': 'velocity', 'out': [40, 110]}, {'type': 'length', 'beats': 0.5},
                      {'type': 'random', 'chance': 0.25}])
    assert MF.describe(c) == ('chord +0 +4 +7 > scale A minor > velocity out 40-110 > length 0.5 beats > '
                              'random 25% up 1-1 x 12 st')
    assert MF.configure({'type': 'pitch', 'transpose': 2})[0]['transpose'] == 2   # one effect alone
    assert MF.configure(c) == c                                                  # its stored form is taken back


# ------------------------------------------------------------------ the studio
@pytest.fixture
def proj(tmp_path):
    p = str(tmp_path / 'fxsong')
    api.project_new(p, bpm=120, length_bars=4, master_limiter=False)
    api.track_add(p, 'lead', instrument={'type': 'synth'})
    api.notes_write(p, 'lead', 1, '0 C4 1; 1 D4 1; 2 E4 2')
    return p


def _d(p):
    with open(os.path.join(p, 'project.json'), encoding='utf8') as f:
        return json.load(f)


def test_track_midi_fx_plays_processed_notes_at_render_and_keeps_them_written(proj):
    chain = [{'type': 'chord', 'intervals': [0, 4, 7]}, {'type': 'scale', 'key': 'C', 'scale': 'major'},
             {'type': 'length', 'beats': 0.5}]
    r = api.track_midi_fx(proj, 'lead', chain=chain)
    assert 'lead: MIDI effects on: chord +0 +4 +7 > scale C major > length 0.5 beats' in r
    assert '3 notes as written play as 9' in r and 'bar 1: C4,E4,G4 D4,F4,A4 E4,G4,B4' in r
    assert len(_d(proj)['tracks']['lead']['notes']) == 3                       # the notes stay as written
    assert 'MIDI FX (track_midi_fx' in api.project_info(proj)
    from ismail.render import Renderer
    R = Renderer(_d(proj), proj, cache=False)
    t = R.project['tracks']['lead']
    assert len(t['notes']) == 9 and t['notes'][3][:4] == [1.0, 62, 0.5, 100]   # D minor triad, half a beat
    from ismail.live.decks import read_song
    _, _, _, clips, _ = read_song(proj, 'A', None, 4)                         # a deck plays the same notes
    assert len(clips[0]['notes'].split(';')) == 9
    assert 'MIDI effects: chord' in api.track_midi_fx(proj, 'lead')            # no chain: says what is on
    assert 'changed' in api.track_midi_fx(proj, 'lead', chain=chain[:1])


def test_write_bakes_the_notes_off_keeps_them_and_undo_brings_them_back(proj):
    api.track_midi_fx(proj, 'lead', chain=[{'type': 'pitch', 'transpose': 12}])
    r = api.track_midi_fx(proj, 'lead', write=True)
    assert 'wrote the MIDI effects (pitch +12) into the track: 3 notes now, 3 before' in r
    t = _d(proj)['tracks']['lead']
    assert 'midi_fx' not in t and [n[1] for n in t['notes']] == [72, 74, 76]
    api.undo(proj)
    t = _d(proj)['tracks']['lead']
    assert t.get('midi_fx') and [n[1] for n in t['notes']] == [60, 62, 64]
    assert 'MIDI effects off' in api.track_midi_fx(proj, 'lead', off=True)
    assert 'midi_fx' not in _d(proj)['tracks']['lead']
    with pytest.raises(OpError, match="has no MIDI effects; track_midi_fx"):
        api.track_midi_fx(proj, 'lead', off=True)
    with pytest.raises(OpError, match="no MIDI effects to write: give chain"):
        api.track_midi_fx(proj, 'lead', write=True)
    with pytest.raises(OpError, match="effect 1 \\(chord\\): intervals"):
        api.track_midi_fx(proj, 'lead', chain=[{'type': 'chord'}])
    assert 'no MIDI effects' in api.track_midi_fx(proj, 'lead')


def test_the_chain_runs_before_the_arp(proj):
    api.notes_clear(proj, 'lead', [1, 4])
    api.notes_write(proj, 'lead', 1, '0 C4 4')
    api.track_midi_fx(proj, 'lead', chain=[{'type': 'chord', 'intervals': [0, 4, 7]}])
    r = api.track_arp(proj, 'lead', pattern='up', rate=1)
    assert 'bar 1: C4 E4 G4 C4' in r                                     # the arp steps the chain's triad
    from ismail.render import Renderer
    t = Renderer(_d(proj), proj, cache=False).project['tracks']['lead']
    assert [n[1] for n in t['notes']] == [60, 64, 67, 60]
    with pytest.raises(OpError, match="track_midi_fx\\(project, 'lead', write=True\\) writes them into the chords"):
        api.track_arp(proj, 'lead', write=True)
    api.track_midi_fx(proj, 'lead', write=True)                          # bake the chain, then the arp
    assert 'wrote the arp as 4 steps' in api.track_arp(proj, 'lead', write=True)


def test_midi_export_carries_the_processed_notes(proj, tmp_path):
    api.track_midi_fx(proj, 'lead', chain=[{'type': 'chord', 'intervals': [0, 12]}])
    out = str(tmp_path / 'fx.mid')
    api.midi_write(proj, out=out)
    from ismail import midifile as MFile
    assert sum(len(t['notes']) for t in MFile.read(out)['tracks']) == 6


def test_a_render_is_the_same_every_time_with_random(proj):
    api.notes_write(proj, 'lead', 2, '; '.join(f"{i / 4} C4 0.25" for i in range(16)))
    api.track_midi_fx(proj, 'lead', chain=[{'type': 'random', 'chance': 0.5, 'choices': 2, 'seed': 3},
                                           {'type': 'velocity', 'random': 15}])
    a = A.expand_project(_d(proj))['tracks']['lead']['notes']
    b = A.expand_project(_d(proj))['tracks']['lead']['notes']
    assert a == b and len({n[1] for n in a}) > 1
