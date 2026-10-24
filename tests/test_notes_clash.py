"""notes_clash (ledger:M282): semitone rubs between parts and out-of-key notes, read from the notes (no render).
A tiny project in tmp: a planted clash is found with its bar, a voicing inside one part is not a clash, an
out-of-key note is found, drums are skipped, min_overlap drops a passing tone, and the output stays bounded."""
import json
import os
import re
import shutil
import tempfile

import pytest

from ismail import api

OPS = api.OPS
SYNTH = {'type': 'synth'}


@pytest.fixture
def proj():
    d = tempfile.mkdtemp(prefix='ismail_clash_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=8)
    with open(os.path.join(p, 'project.json'), encoding='utf8') as f:
        pj = json.load(f)
    pj['key'] = 'C major'                     # the project's key, as a song keeps it
    with open(os.path.join(p, 'project.json'), 'w', encoding='utf8') as f:
        json.dump(pj, f)
    for t in ('keys', 'bass'):
        OPS['track_add'](p, name=t, instrument=SYNTH)
    yield p
    shutil.rmtree(d, ignore_errors=True)


def clash_lines(out):
    return [l for l in out.splitlines() if re.match(r'\s+\d+\. bar', l) and ' vs ' in l]


def test_a_planted_semitone_clash_between_two_parts_is_found_with_its_bar(proj):
    OPS['notes_write'](proj, track='keys', bar=3, notes='0 C4,E4,G4 4 90')
    OPS['notes_write'](proj, track='bass', bar=3, notes='1 B2 2 100')        # B2 under C4: a minor 9th
    out = OPS['notes_clash'](proj)
    assert 'semitone clashes between parts: 1 ' in out, out
    [line] = clash_lines(out)
    assert 'bar 3 +1:' in line and 'bass B2 v100' in line and 'keys C4 v90' in line and 'a minor 9th' in line
    assert '2 beat' in line, line
    assert "key: C major (the project's)" in out


def test_a_voicing_inside_one_part_is_not_a_cross_part_clash(proj):
    OPS['notes_write'](proj, track='keys', bar=2, notes='0 B3,C4,E4 4 90')    # a cluster in one part
    OPS['notes_write'](proj, track='bass', bar=2, notes='0 E2 4 100')          # in the chord
    out = OPS['notes_clash'](proj)
    assert 'semitone clashes between parts: 0 ' in out and not clash_lines(out), out


def test_a_close_major_seventh_is_a_chord_colour_not_a_clash(proj):
    OPS['notes_write'](proj, track='keys', bar=1, notes='0 B3 4 90')
    OPS['notes_write'](proj, track='bass', bar=1, notes='0 C3 4 100')          # 11 semitones
    assert 'semitone clashes between parts: 0 ' in OPS['notes_clash'](proj)


def test_an_out_of_key_note_is_found(proj):
    OPS['notes_write'](proj, track='keys', bar=1, notes='0 C4,E4,G4 4 90')
    OPS['notes_write'](proj, track='keys', bar=4, notes='2 F#4 1 70')
    out = OPS['notes_clash'](proj)
    assert 'out of key: 1 (F# 1)' in out, out
    assert re.search(r'1\. bar 4 \+2: keys F#4 v70, 1 beat', out), out
    out = OPS['notes_clash'](proj, key='F# major')
    assert 'key: F# major (as given)' in out and 'out of key: 3 ' in out, out


def test_with_no_key_anywhere_the_key_is_found_from_the_notes():
    d = tempfile.mkdtemp(prefix='ismail_clash_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=120, length_bars=4)
        OPS['track_add'](p, name='keys', instrument=SYNTH)
        OPS['notes_write'](p, track='keys', bar=1, notes='0 D4,F4,A4 2 90; 2 G4,A#4,D5 2 90; 4 D4,F4,A4 4 100')
        out = OPS['notes_clash'](p)
        assert re.search(r'key: D minor with C#, its raised 7th \(found from the notes, r=0\.\d\d\)', out), out
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_minor_key_allows_its_raised_seventh(proj):
    OPS['notes_write'](proj, track='keys', bar=1, notes='0 A3,C4,E4 4 90; 0 E4,G#4,B4 4 90')
    out = OPS['notes_clash'](proj, key='A minor')
    assert 'key: A minor with G#, its raised 7th' in out and 'out of key: 0' in out, out


def test_drums_are_ignored(proj):
    OPS['track_add'](proj, name='beat', instrument={'type': 'kit', 'map': {'C1': {'type': 'kick'},
                                                                          'D1': {'type': 'snare'}}})
    OPS['track_add'](proj, name='hit', instrument={'type': 'snare'})
    OPS['track_add'](proj, name='loop', instrument={'type': 'code', 'voice': 'drums/kit70'})
    OPS['notes_write'](proj, track='bass', bar=1, notes='0 C#1 4 100; 0 D#1 4 100')
    OPS['notes_write'](proj, track='beat', bar=1, notes='0 C1 1 110; 1 D1 1 110')
    OPS['notes_write'](proj, track='hit', bar=1, notes='0 D1 4 110')
    OPS['notes_write'](proj, track='loop', bar=1, notes='0 C1 4 110')
    out = OPS['notes_clash'](proj)
    assert 'semitone clashes between parts: 0 ' in out, out
    assert '1 parts (bass)' in out
    skipped = out.splitlines()[1]
    assert 'beat (pads C1: a kick drum; pads D1: a snare drum)' in skipped and 'hit (a snare drum)' in skipped
    assert 'loop (the drums/kit70 voice' in skipped


def test_a_kit_reads_its_pitched_pads(proj):
    OPS['track_add'](proj, name='blips', instrument={'type': 'kit', 'map': {
        'C#5': {'type': 'synth'}, 'D5': {'type': 'synth', 'oscs': [{'wave': 'square', 'fixed_hz': 540.0}]}}})
    OPS['notes_write'](proj, track='keys', bar=1, notes='0 C4 4 90; 0 D5 4 90')
    OPS['notes_write'](proj, track='blips', bar=1, notes='0 C#5 1 100; 2 D#5 1 100; 3 D5 1 100')
    out = OPS['notes_clash'](proj)
    assert 'pads D#5: unmapped, silent' in out and 'pads D5: a fixed-pitch synth' in out, out
    assert 'semitone clashes between parts: 2 ' in out, out           # C#5 against C4 and D5; D#5 silent


def test_min_overlap_drops_a_passing_tone(proj):
    OPS['notes_write'](proj, track='keys', bar=1, notes='0 C4 4 90')
    OPS['notes_write'](proj, track='bass', bar=1, notes='1 C#4 0.05 100; 2 B2 0.5 100')
    out = OPS['notes_clash'](proj)                                   # the 0.05-beat C#4 passes, the B2 stays
    [line] = clash_lines(out)
    assert 'semitone clashes between parts: 1 ' in out and 'bar 1 +2:' in line and 'B2' in line, out
    assert 'semitone clashes between parts: 2 ' in OPS['notes_clash'](proj, min_overlap=0)
    assert 'semitone clashes between parts: 0 ' in OPS['notes_clash'](proj, min_overlap=1)


def test_the_output_is_bounded_and_counts_cover_all(proj):
    for b in range(1, 9):     # a different clash at 8 places in every bar, and 8 out-of-key notes
        OPS['notes_write'](proj, track='keys', bar=b, notes='; '.join(f"{i / 2:g} C4 0.5 {60 + i}" for i in range(8)))
        OPS['notes_write'](proj, track='bass', bar=b, notes='; '.join(f"{i / 2:g} B2 0.5 {100 + i}" for i in range(8)))
        OPS['notes_write'](proj, track='keys', bar=b, notes='0 C#5 0.5 80', mode='add')
    out = OPS['notes_clash'](proj, n=3)
    assert 'semitone clashes between parts: 64 (8 places in the bar)' in out, out
    assert 'most audible clashes (top 3 of 8)' in out and len(clash_lines(out)) == 3
    assert 'x8 in bars 1-8' in clash_lines(out)[0]
    first = clash_lines(out)[0]
    assert 'v67' in first, out         # the loudest keys note (v67) is the most audible rub
    assert 'out of key: 8 (C# 8)' in out
    with pytest.raises(api.OpError, match='min_overlap'):
        OPS['notes_clash'](proj, min_overlap=-1)
