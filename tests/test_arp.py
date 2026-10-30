"""The arpeggiator core (ismail/arp.py) and its studio side, track_arp (MIDI parity step 7): patterns over held
keys, held-key numbers, scale degrees, exact step lists switching on bar lines, key moves, per-step lists cycling on
their own lengths, restarts, swing; the studio expands a track at render (the renderer, decks and MIDI export all
see the steps) and writes the steps out as plain notes. Pure functions plus one tiny render in a temp folder."""
import json
import os

import numpy as np
import pytest

from ismail import api
from ismail import arp as A

C_MAJ = [(60, 90), (64, 80), (67, 100)]


def cfg(**kw):
    return A.configure(None, kw)[0]


def pitches(st):
    return [[m for m, _ in ns] for _, ns, _, _, _ in st]


def test_named_patterns_walk_the_held_keys_over_octaves():
    run = lambda **kw: pitches(A.steps(cfg(**kw), C_MAJ, 0, 4, 0, 0, 4))       # noqa: E731
    assert run()[:4] == [[60], [64], [67], [60]]
    assert run(pattern='down')[:3] == [[67], [64], [60]]
    assert run(pattern='updown')[:6] == [[60], [64], [67], [64], [60], [64]]
    assert run(pattern='up', octaves=2)[:7] == [[60], [64], [67], [72], [76], [79], [60]]
    assert run(pattern='chord')[0] == [60, 64, 67]
    held_order = [(67, 100), (60, 90), (64, 80)]                                 # pressed G, C, E
    assert pitches(A.steps(cfg(pattern='order'), held_order, 0, 1, 0, 0, 4)) == [[67], [60], [64], [67]]
    r1 = pitches(A.steps(cfg(pattern='random'), C_MAJ, 0, 4, 0, 0, 4))
    assert r1 == pitches(A.steps(cfg(pattern='random'), C_MAJ, 0, 4, 0, 0, 4))  # deterministic
    assert len({x[0] for x in r1}) > 1


def test_held_key_numbers_degrees_and_snapping_to_the_scale():
    st = A.steps(cfg(pattern=[1, 3, 0, 4]), C_MAJ, 0, 1, 0, 0, 4)
    assert pitches(st) == [[60], [67], [72]]                                      # 0 a rest, 4 = C up an octave
    assert [round(b, 3) for b, *_ in st] == [0, 0.25, 0.75]
    d = cfg(degrees=[0, 2, 4, None, 7], key='A', scale='minor')
    assert pitches(A.steps(d, [(57, 100)], 0, 1.25, 0, 0, 4)) == [[57], [60], [64], [69]]
    # a key off the scale is moved onto it: C# in A minor -> C (ties go down) or D
    assert pitches(A.steps(cfg(key='A', scale='minor'), [(61, 100)], 0, 0.25, 0, 0, 4)) == [[60]]
    with pytest.raises(A.ArpError, match="degrees are scale steps: give key= and scale="):
        cfg(degrees=[0, 2])


def test_a_step_list_switches_on_bar_lines_and_moves_key_by_scale_degree():
    c = cfg(notes='A3 C4 E4 A4 | F3 A3 . F4,A4', key='A', scale='minor', rate=0.5)
    st = A.steps(c, [], 0, 8, 0, 0, 4)
    assert pitches(st)[:8] == [[57], [60], [64], [69], [57], [60], [64], [69]]   # 4 steps cycle inside bar 1
    assert pitches(st)[8:11] == [[53], [57], [65, 69]]                          # bar 2's group, '.' a rest
    assert [b for b, *_ in st][8:11] == [4.0, 4.5, 5.5]
    moved, ch = A.configure(c, {'key': 'C', 'scale': 'major'})
    assert ch == ['key', 'scale']
    assert pitches(A.steps(moved, [], 0, 2, 0, 0, 4)) == [[60], [64], [67], [72]]   # i in A minor -> I in C major
    assert pitches(A.steps(A.configure(c, {'transpose': 2})[0], [], 0, 0.5, 0, 0, 4)) == [[59]]
    with pytest.raises(A.ArpError, match="Give it with them once"):
        A.configure(cfg(notes='A3 C4'), {'key': 'C'})


def test_per_step_lists_cycle_on_their_own_lengths():
    c = cfg(velocity=[100, 60], accent=[0, 0, 1], cutoff=[300, 900, 2700], pan=[-1, 1], gate=[0.5, 1.0],
            octave=[0, 0, 0, 1])
    st = A.steps(c, [(60, 90)], 0, 3, 0, 0, 4)
    assert [ns[0][1] for _, ns, *_ in st][:6] == [100, 60, 127, 60, 100, 127]
    assert [s[3] for s in st][:4] == [300, 900, 2700, 300]
    assert [s[4] for s in st][:3] == [-1, 1, -1]
    assert [s[2] for s in st][:2] == [0.125, 0.25]
    assert [ns[0][0] for _, ns, *_ in st][:4] == [60, 60, 60, 72]


def test_slide_swing_and_the_three_restarts():
    c = cfg(slide=[1, 0], swing=0.2)
    st = A.steps(c, [(60, 90)], 0, 1, 0, 0, 4)
    assert [round(b, 3) for b, *_ in st] == [0, 0.3, 0.5, 0.8]
    assert st[0][2] == pytest.approx(0.3 + A.SLIDE_OVER * 0.25)                # lasts into the swung next step
    k = cfg(pattern='up')
    # restart 'key': a key at 1.1 -> the pattern starts again at the next line, 1.25
    st = A.steps(k, C_MAJ, 0, 2, 0, 1.1, 4)
    assert st[0][0] == 1.25 and pitches(st)[:2] == [[60], [64]]
    off = cfg(restart='off')
    assert pitches(A.steps(off, C_MAJ, 1.25, 1.5, 0, 0, 4)) == [[67]]          # counted from 0: step 5
    bar = cfg(restart='bar')
    assert pitches(A.steps(bar, C_MAJ, 4, 4.5, 0, 0, 4)) == [[60], [64]]        # every bar line


def test_settings_change_only_what_is_given_and_say_what_to_do():
    c, ch = A.configure(None, {'rate': '1/8', 'cutoff': [400, 4000]})
    assert ch == ['rate', 'cutoff'] and c['pattern'] == 'up'
    c2, ch2 = A.configure(c, {'gate': 0.9})
    assert ch2 == ['gate'] and c2['rate'] == 0.125 and c2['cutoff'] == [400, 4000]
    assert A.configure(c2, {'cutoff': []})[0]['cutoff'] is None                  # [] clears a list
    for bad, msg in [({'pattern': 'sideways'}, "one of up, down"), ({'rate': 0}, "1/64 to 4"),
                     ({'pattern': 'up', 'notes': 'C4'}, "one note source at a time"),
                     ({'cutoff': [5]}, "cutoff is in Hz"), ({'notes': 'C4 | '}, "bar group 2 is empty"),
                     ({'notes': 'C4 H9'}, "is not a note"), ({'scale': 'lydian_x'}, "one of chromatic"),
                     ({'restart': 'never'}, "'key'"), ({'colour': 1}, "unknown arp settings")]:
        with pytest.raises(A.ArpError, match=msg):
            A.configure(None, bad)


# ------------------------------------------------------------------ the studio
@pytest.fixture
def proj(tmp_path):
    p = str(tmp_path / 'arpsong')
    api.project_new(p, bpm=120, length_bars=4, master_limiter=False)
    api.track_add(p, 'lead', instrument={'type': 'synth', 'filter': {'type': 'lp12', 'cutoff': 8000}})
    api.notes_write(p, 'lead', 1, '0 C4,E4,G4 4; 4 A3,C4,E4 4')
    return p


def _tr(p, name='lead'):
    with open(os.path.join(p, 'project.json'), encoding='utf8') as f:
        return json.load(f)['tracks'][name]


def test_track_arp_plays_the_held_chords_at_render_and_keeps_them_written(proj):
    r = api.track_arp(proj, 'lead', pattern='up', rate=0.5, cutoff=[500, 4000], pan=[-0.5, 0.5])
    assert 'lead: arp on: pattern up, rate 0.5' in r and 'bars 1-2, 16 steps' in r
    assert 'bar 1: C4 E4 G4 C4 E4 G4 C4 E4' in r and 'bar 2: A3 C4 E4 A3' in r     # each chord restarts it
    assert len(_tr(proj)['notes']) == 6                                         # the chords stay as written
    assert 'ARP (track_arp: the render plays its steps)' in api.project_info(proj)
    from ismail.render import Renderer
    with open(os.path.join(proj, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    R = Renderer(d, proj, cache=False)
    t = R.project['tracks']['lead']
    assert len(t['notes']) == 16 and t['notes'][1][:3] == [0.5, 64, 0.25]
    assert t['automation']['inst.filter.cutoff'][:4] == [[0.0, 500.0], [0.4999, 500.0], [0.5, 4000.0],
                                                         [0.9999, 4000.0]]
    assert t['automation']['pan'][0] == [0.0, -0.5]
    from ismail.live.decks import read_song
    song, _, _, clips, skipped = read_song(proj, 'A', None, 4)          # a deck plays the same steps
    assert len(clips[0]['notes'].split(';')) == 16
    assert 'inst.filter.cutoff' in song['automation']['track:A.lead'] and 'lead pan' in skipped['automation']
    r2 = api.track_arp(proj, 'lead', rate=0.25)
    assert 'rate changed' in r2 and 'pattern up, rate 0.25' in r2 and 'cutoff [500, 4000]' in r2


def test_a_tiny_render_follows_the_per_step_pan(proj, tmp_path):
    api.track_arp(proj, 'lead', pattern='up', rate=1, pan=[-1, 1], bars=[1, 1])
    from ismail.render import Renderer
    with open(os.path.join(proj, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    R = Renderer(d, proj, start_bar=1, end_bar=2, cache=False)
    y, _ = R.run()
    spb = int(0.5 * R.sr)
    l0, r0 = (np.sqrt(np.mean(y[c, :spb] ** 2)) for c in (0, 1))
    l1, r1 = (np.sqrt(np.mean(y[c, spb:2 * spb] ** 2)) for c in (0, 1))
    assert l0 > 10 * r0 and r1 > 10 * l1                                      # step 1 hard left, step 2 hard right


def test_write_makes_the_steps_plain_notes_and_off_keeps_the_chords(proj):
    api.track_arp(proj, 'lead', pattern='down', rate=1, cutoff=[600, 3000])
    r = api.track_arp(proj, 'lead', write=True)
    assert 'wrote the arp as 8 steps (8 notes now, 6 before) over bars 1-2' in r
    assert 'inst.filter.cutoff written as automation' in r and 'The arp is off' in r
    t = _tr(proj)
    assert 'arp' not in t and [n[1] for n in t['notes']][:4] == [67, 64, 60, 67]
    assert t['automation']['inst.filter.cutoff'][2] == [1.0, 3000.0]
    api.undo(proj)
    assert len(_tr(proj)['notes']) == 6 and _tr(proj).get('arp')
    assert 'arp off' in api.track_arp(proj, 'lead', off=True)
    assert 'arp' not in _tr(proj) and len(_tr(proj)['notes']) == 6


def test_a_step_list_on_an_empty_track_and_what_it_says_when_asked_wrong(proj):
    api.track_add(proj, 'pad', instrument={'type': 'synth'})
    from ismail.api import OpError
    with pytest.raises(OpError, match="needs bars=\\[a, b\\]"):
        api.track_arp(proj, 'pad', notes='A3 C4 E4 A4 | F3 A3 C4 F4')
    r = api.track_arp(proj, 'pad', notes='A3 C4 E4 A4 | F3 A3 C4 F4', bars=[1, 4], key='A', scale='minor')
    assert 'bars 1-4, 64 steps' in r and 'bar 2: F3 A3 C4 F4' in r
    r = api.track_arp(proj, 'pad', key='C', scale='major')
    assert 'bar 1: C4 E4 G4 C5' in r
    api.track_add(proj, 'empty', instrument={'type': 'synth'})
    with pytest.raises(OpError, match="no notes to arpeggiate: write the chords"):
        api.track_arp(proj, 'empty', pattern='up')
    with pytest.raises(OpError, match="has no arp"):
        api.track_arp(proj, 'empty', off=True)
    with pytest.raises(OpError, match="one of up, down"):
        api.track_arp(proj, 'lead', pattern='sideways')
    api.track_add(proj, 'drum', instrument={'type': 'kick'})
    api.notes_write(proj, 'drum', 1, '0 C2 4')
    assert 'exposes no filter cutoff' in api.track_arp(proj, 'drum', cutoff=[300, 900])


def test_midi_export_carries_the_steps(proj, tmp_path):
    api.track_arp(proj, 'lead', pattern='up', rate=1)
    out = str(tmp_path / 'arp.mid')
    api.midi_write(proj, out=out)
    from ismail import midifile as MF
    f = MF.read(out)
    assert sum(len(t['notes']) for t in f['tracks']) == 8
