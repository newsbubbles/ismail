"""midi_write / midi_read (ledger:M113, Nate 10-23 "full midi like ableton"): a song's notes out to a type 1 .mid and
back, checked against an independent reader when one is installed, plus the listing, windows, markers, nudges,
overlaps, kits on channel 10 and the tempo and kit warnings."""
import struct

import pytest

from ismail import api
from ismail import midifile as MF

OPS = api.OPS


def _song(p):
    api.project_new(p, bpm=128, length_bars=8)
    api.track_add(p, 'drums', instrument='preset:kit_basic')
    api.track_add(p, 'bass', instrument={"type": "synth", "oscs": [{"wave": "saw"}], "mono": True})
    api.track_add(p, 'lead', instrument={"type": "synth", "oscs": [{"wave": "square"}]})
    api.pattern_write(p, 'drums', 1, {"C1": "x...x...x...x...", "D1": "....X.......x..."}, repeat=8)
    api.notes_write(p, 'bass', 1, "0 E2 1 90; 1 G2 1; 2 A2 1 64; 3 D3 1", repeat=8)
    api.notes_write(p, 'lead', 3, "0 E4 2 110 @-20ms; 2 G4,B4 2")
    return p


def test_a_song_goes_out_as_a_type_1_file_with_tempo_meter_markers_and_channels(tmp_path):
    p = _song(str(tmp_path / 'song'))
    out = OPS['midi_write'](p, markers={'verse': 1, 'lift': 5})
    path = str(tmp_path / 'song' / 'exports' / 'song.mid')
    assert path in out and 'channel 10' in out and 'kick' in out
    f = MF.read(path)
    assert f['format'] == 1 and f['tempos'] == [(0.0, pytest.approx(128, abs=0.01))] and f['meters'][0][1:] == (4, 4)
    assert [t for b, t in f['markers']] == ['verse', 'lift'] and f['markers'][1][0] == 16
    by = {t['name']: t for t in f['tracks'][1:]}
    assert set(by) == {'drums', 'bass', 'lead'} and by['drums']['channels'] == {9}
    assert by['bass']['channels'].isdisjoint(by['lead']['channels'] | {9})
    bass = by['bass']['notes']
    assert len(bass) == 32 and bass[0][:4] == (0.0, 40, 1.0, 90) and bass[2][3] == 64
    lead = by['lead']['notes']
    nudge = 20 / 1000 * 128 / 60                          # 20 ms early, in beats
    assert lead[0][0] == pytest.approx(8 - nudge, abs=1 / MF.PPQ) and [n[1] for n in lead[1:]] == [67, 71]
    assert sum(1 for n in by['drums']['notes'] if n[3] == 127) == 8         # the accents survive


def test_an_independent_reader_agrees(tmp_path):
    mido = pytest.importorskip('mido')
    p = _song(str(tmp_path / 'song'))
    path = str(tmp_path / 'x.mid')
    OPS['midi_write'](p, out=path)
    m = mido.MidiFile(path)
    assert m.type == 1 and m.ticks_per_beat == MF.PPQ and len(m.tracks) == 4
    ons = [msg for tr in m.tracks for msg in tr if msg.type == 'note_on' and msg.velocity > 0]
    assert len(ons) == 32 + 3 + 8 * 6
    assert any(msg.type == 'set_tempo' and round(mido.tempo2bpm(msg.tempo)) == 128 for msg in m.tracks[0])


def test_a_window_moves_to_bar_1_and_round_trips_through_midi_read(tmp_path):
    p = _song(str(tmp_path / 'song'))
    path = str(tmp_path / 'w.mid')
    out = OPS['midi_write'](p, out=path, tracks=['bass', 'lead'], bars=[3, 4])
    assert "bar 3 is the file's bar 1" in out and 'lead: 3 notes' in out
    q = str(tmp_path / 'other')
    api.project_new(q, bpm=128, length_bars=8)
    api.track_add(q, 'b', instrument={"type": "synth", "oscs": [{"wave": "saw"}]})
    listing = OPS['midi_read'](q, path)
    assert '#1 bass: 8 notes' in listing and '#2 lead: 3 notes' in listing and '128 BPM' in listing
    out = OPS['midi_read'](q, path, into={'bass': 'b'}, bar=5)
    assert 'bass -> b: 8 notes, bars 5-6' in out
    got = api.notes_read(q, 'b', bars=[5, 5], view='rel')
    assert got.splitlines()[0] == '0 E2 1 90' and len(got.splitlines()) == 4


def test_a_gm_drum_file_reads_by_channel_and_names_the_silent_pads(tmp_path):
    path = str(tmp_path / 'gm.mid')
    MF.write(path, [{'name': 'kit', 'channel': 9, 'notes': [(0, 36, 0.25, 100), (1, 38, 0.25, 100), (1.5, 42, 0.25, 80)]},
                    {'name': 'keys', 'channel': 0, 'notes': [(0, 60, 4, 70)]}], bpm=100)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=4)
    api.track_add(q, 'drums', instrument='preset:kit_basic')
    out = OPS['midi_read'](q, path, into={'ch10': 'drums'}, quantize=0.5)
    assert 'ch10 -> drums: 3 notes' in out and 'silent' in out and 'at 100 BPM, the project at 120' in out
    starts = [n[0] for n in api._load(q).track('drums')['notes']]
    assert starts == [0.0, 1.0, 1.5]


def test_overlapping_same_pitch_notes_are_cut_so_each_off_is_its_own(tmp_path):
    path = str(tmp_path / 'o.mid')
    MF.write(path, [{'name': 'x', 'channel': 0, 'notes': [(0, 60, 2, 100), (1, 60, 2, 100)]}], bpm=120)
    notes = MF.read(path)['tracks'][1]['notes']
    assert [(n[0], n[2]) for n in notes] == [(0.0, 1.0), (1.0, 2.0)]


def test_running_status_and_note_on_zero_from_other_writers_are_read(tmp_path):
    ev = bytes([0x00, 0x90, 60, 100, 0x60, 62, 90])            # on C4, then running status: on D4 at 96 ticks
    ev += bytes([0x60, 60, 0, 0x00, 62, 0])                      # note-on velocity 0 = off, both
    ev += b'\x00\xff\x2f\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, 96) + b'MTrk' + struct.pack('>I', len(ev)) + ev
    path = tmp_path / 'r.mid'
    path.write_bytes(data)
    notes = MF.read(str(path))['tracks'][0]['notes']
    assert [(n[0], n[1], n[2]) for n in notes] == [(0.0, 60, 2.0), (1.0, 62, 1.0)]


def test_errors_point_forward(tmp_path):
    p = str(tmp_path / 'e')
    api.project_new(p, bpm=120, length_bars=4)
    with pytest.raises(api.OpError, match='notes_write'):
        OPS['midi_write'](p)
    (tmp_path / 'bad.mid').write_bytes(b'nope')
    with pytest.raises(api.OpError, match='not a MIDI file'):
        OPS['midi_read'](p, str(tmp_path / 'bad.mid'))
