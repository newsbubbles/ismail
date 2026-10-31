"""Lanes as MIDI in files (MIDI parity step 6): a track's expression lanes go out with midi_write as pitch bend (with
its RPN range), controllers and pressure, and come back with midi_read onto lanes; the map is data the agent
overrides; MPE puts each note on its own channel and reads per-note bend back by last-note priority. Files only,
no sound."""
import struct

import pytest

from ismail import api
from ismail import midifile as MF
from ismail import midilanes as ML

OPS = api.OPS


def _lead(p, voice='flute'):
    api.project_new(p, bpm=120, length_bars=4)
    api.track_add(p, 'lead', instrument={'type': 'code', 'voice': voice, 'tail': 0.5})
    api.notes_write(p, 'lead', 1, "0 A4 2 100; 2 C5 2 90; 4 D5 4 100; 8 E5 8 100")
    api.automation_set(p, 'lead', 'inst.lane.bend', [[1, 0], [1.5, 0], [2, 1.5], [3, -2], [4, 0]])
    api.automation_set(p, 'lead', 'inst.lane.vib', [[1, 0], [3, 40]])
    api.automation_set(p, 'lead', 'inst.lane.level', [[1, -12], [2, 0], [3, -6]])
    return p


def _lane(p, track, name):
    return api._load(p).track(track)['automation']['inst.lane.' + name]


def _same_curve(a, b, err, t0, t1):
    for i in range(int((t1 - t0) * 32) + 1):
        t = t0 + i / 32
        assert ML.value_at(a, t) == pytest.approx(ML.value_at(b, t), abs=err), t


def test_lanes_go_out_as_bend_controllers_and_rpn_and_read_back_onto_lanes(tmp_path):
    p = _lead(str(tmp_path / 'song'))
    api.automation_set(p, 'lead', 'inst.lane.tongue', [[1, 0.5]])
    path = str(tmp_path / 'l.mid')
    out = OPS['midi_write'](p, out=path)
    assert 'lanes: ' in out and 'bend -> pitch bend +-2' in out and 'vib -> cc:1 0..50' in out
    assert 'level -> cc:11 -40..0' in out and 'lanes not written (no MIDI message for them): tongue' in out
    tr = MF.read(path)['tracks'][1]
    assert tr['bend_range'] == {tr['notes'][0][4]: 2.0}
    kinds = {w for _, _, w, _ in tr['ctl']}
    assert kinds == {'bend', 'cc:1', 'cc:11'}
    bends = [raw for _, _, w, raw in tr['ctl'] if w == 'bend']
    assert bends[0] == 8192 and max(bends) == 8192 + round(0.75 * 8191) and min(bends) == 1

    q = str(tmp_path / 'other')
    api.project_new(q, bpm=120, length_bars=4)
    api.track_add(q, 'solo', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    listing = OPS['midi_read'](q, path)
    assert 'expression: bend' in listing and 'cc:1' in listing and '(bend range +-2)' in listing
    back = OPS['midi_read'](q, path, into={'lead': 'solo'})
    assert 'lane bend:' in back and 'lane vib:' in back and 'lane level:' in back and 'WARNING' not in back
    src, dst = api._load(p).track('lead')['automation'], api._load(q).track('solo')['automation']
    for name, err in (('bend', 4 / 8191 + 1e-6), ('vib', 50 / 127 + 1e-6), ('level', 40 / 127 + 1e-6)):
        _same_curve(src['inst.lane.' + name], dst['inst.lane.' + name], err, 0, 16)
    assert len(dst['inst.lane.vib']) <= 12                   # a straight ramp comes back as a few points


def test_the_map_is_data_ranges_clamp_and_a_lane_can_be_left_out(tmp_path):
    p = _lead(str(tmp_path / 'song'))
    api.automation_set(p, 'lead', 'inst.lane.flutter', [[1, 0], [2, 1]])
    api.automation_set(p, 'lead', 'inst.lane.level', [[1, -12], [2, 3]])
    path = str(tmp_path / 'm.mid')
    out = OPS['midi_write'](p, out=path, lanes={'bend': {'range': 12}, 'flutter': {'midi': 'cc:20', 'range': [0, 1]},
                                                'vib': None})
    assert 'bend -> pitch bend +-12' in out and 'flutter -> cc:20 0..1' in out and 'vib ->' not in out
    assert 'WARNING level:' in out and "lanes={'level': {'range': [lo, hi]}} widens it" in out
    f = MF.read(path)
    assert set(f['tracks'][1]['bend_range'].values()) == {12.0}
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=4)
    api.track_add(q, 'solo', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    back = OPS['midi_read'](q, path, into={'#1': 'solo'}, lanes={'flutter': {'midi': 'cc:20', 'range': [0, 1]}})
    assert '(bend range +-12)' in back                       # the file's RPN, not the default
    _same_curve(_lane(p, 'lead', 'bend'), _lane(q, 'solo', 'bend'), 24 / 8191 + 1e-6, 0, 16)
    _same_curve(_lane(p, 'lead', 'flutter'), _lane(q, 'solo', 'flutter'), 1 / 127 + 1e-6, 0, 16)
    assert max(v for _, v in _lane(q, 'solo', 'level')) == pytest.approx(0)   # clamped at the range's top


def test_a_file_from_another_program_reads_its_bend_range_and_lists_what_no_lane_took(tmp_path):
    c = 0xB0
    ev = bytes([0, c, 101, 0, 0, c, 100, 0, 0, c, 6, 12, 0, c, 38, 0])          # RPN 0: bend range 12
    ev += bytes([0, 0x90, 64, 100, 0, 0xE0, 0x7F, 0x7F, 0, c, 74, 90])          # top of the bend; cc:74
    ev += bytes([0x83, 0x60, 0xE0, 0, 0x40])                                    # 480 ticks: back to the centre
    ev += bytes([0x83, 0x60, 0x80, 64, 0]) + b'\x00\xff\x2f\x00'
    data = b'MThd' + struct.pack('>IHHH', 6, 0, 1, 480) + b'MTrk' + struct.pack('>I', len(ev)) + ev
    path = tmp_path / 'other.mid'
    path.write_bytes(data)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=2)
    api.track_add(q, 'keys', instrument={'type': 'synth', 'oscs': [{'wave': 'saw'}]})
    out = OPS['midi_read'](q, str(path), into={'#0': 'keys'}, bar=2)
    assert 'lane bend: 3 points from 2 bend messages (bend range +-12)' in out
    assert "not read: cc:74 (1 message); lanes={'<lane>': {'midi': 'cc:74', 'range': [lo, hi]}} reads one" in out
    assert 'WARNING keys plays a sound with no bend lane' in out and 'voice_help' in out
    assert _lane(q, 'keys', 'bend') == [[4.0, pytest.approx(12.0)], [pytest.approx(5.0 - 1 / 960), pytest.approx(12.0)],
                                        [5.0, 0.0]]


def test_mpe_gives_each_note_its_channel_and_reads_back_by_last_note_priority(tmp_path):
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=2)
    api.track_add(p, 'lead', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    api.notes_write(p, 'lead', 1, "0 C5 1 100; 1 D5 1 100; 2 E5,G5 2 100")
    api.automation_set(p, 'lead', 'inst.lane.bend', [[1, 0], [2, 2], [2.5, -1]])
    api.automation_set(p, 'lead', 'inst.lane.level', [[1, -20], [2, -3]])
    path = str(tmp_path / 'mpe.mid')
    out = OPS['midi_write'](p, out=path, mpe=True)
    assert 'MPE (channel 1 the master, a channel of 2-16 per note)' in out
    assert 'bend -> pitch bend +-48' in out and 'level -> pressure -40..0' in out
    tr = MF.read(path)['tracks'][1]
    assert tr['mpe'] == 15 and set(tr['bend_range'].values()) == {48.0}
    chans = [n[4] for n in tr['notes']]
    assert 0 not in chans and len(set(chans[2:])) == 2       # the chord's notes on two channels
    for n in tr['notes']:                                    # each note's bend is on its own channel, at its start
        first = [raw for b, ch, w, raw in tr['ctl'] if w == 'bend' and ch == n[4] and b == pytest.approx(n[0])]
        assert first and ML.from_raw('bend', -48, 48, first[0]) == pytest.approx(
            ML.value_at(_lane(p, 'lead', 'bend'), n[0]), abs=0.01)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=2)
    api.track_add(q, 'solo', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    assert 'MPE, expression' in OPS['midi_read'](q, path)
    back = OPS['midi_read'](q, path, into={'lead': 'solo'})
    assert 'per note (MPE)' in back and '(bend range +-48)' in back
    assert '1 notes overlap a newer one' in back             # the chord's two notes share one lane
    _same_curve(_lane(p, 'lead', 'bend'), _lane(q, 'solo', 'bend'), 96 / 8191 + 1e-6, 0, 3.95)
    _same_curve(_lane(p, 'lead', 'level'), _lane(q, 'solo', 'level'), 40 / 127 + 1e-6, 0, 3.95)


def test_an_independent_reader_sees_the_bend_and_controllers(tmp_path):
    mido = pytest.importorskip('mido')
    p = _lead(str(tmp_path / 'song'))
    path = str(tmp_path / 'x.mid')
    OPS['midi_write'](p, out=path)
    msgs = [m for m in mido.MidiFile(path).tracks[1]]
    assert any(m.type == 'pitchwheel' and m.pitch == -8191 for m in msgs)          # -2 semitones
    assert any(m.type == 'control_change' and m.control == 1 for m in msgs)
    rpn = [(m.control, m.value) for m in msgs if m.type == 'control_change' and m.control in (101, 100, 6)][:3]
    assert rpn == [(101, 0), (100, 0), (6, 2)]


def test_lane_maps_say_what_to_do_when_asked_wrong(tmp_path):
    p = _lead(str(tmp_path / 'song'))
    cases = [({'bend': {'range': [-12, 2]}}, r'\[-r, r\]'),
             ({'vib': {'midi': 'cc:64'}}, 'pedals'),
             ({'vib': {'midi': 'cc:11'}}, "'level' and 'vib' both use cc:11|'vib' and 'level' both use cc:11"),
             ({'mute': {'range': [0, 1]}}, "has no default message"),
             ({'mute': 'cc:20'}, "'range': \\[lo, hi\\]"),
             ({'vib': {'midi': 'wheel'}}, "'bend', 'pressure' or 'cc:<n>'"),
             ('bend', 'lanes: ')]
    for lanes, msg in cases:
        with pytest.raises(api.OpError, match=msg):
            OPS['midi_write'](p, out=str(tmp_path / 'e.mid'), lanes=lanes)
    with pytest.raises(api.OpError, match='mpe: True'):
        OPS['midi_write'](p, out=str(tmp_path / 'e.mid'), mpe=['nope'])


def test_held_values_come_back_as_steps_not_ramps():
    pts = ML.lane_points([(0.0, 8192), (4.0, 16383)], 'bend', -2, 2)
    assert pts == [[0.0, 0.0], [round(4 - 1 / 960, 6), 0.0], [4.0, 2.0]]
    evs, clamped = ML.lane_events([[0, 0], [1, 1]], 'cc', 0, 1, 0, 2)
    assert evs[0] == (0, 0) and evs[-1] == (1.0, 127) and clamped == 0 and len(evs) <= 65
    assert ML.lane_points(evs, 'cc', 0, 1) == [[0.0, 0.0], [1.0, 1.0]]


def test_mpe_priority_goes_back_to_an_older_note_still_held():
    a, b = (0.0, 60, 4.0, 100, 1), (1.0, 64, 0.5, 100, 2)            # A held 0-4, B tapped 1-1.5
    ctl = [(0.0, 1, 'bend', 8192), (1.0, 2, 'bend', 12000), (2.0, 1, 'bend', 10000), (3.0, 1, 'bend', 11000)]
    evs, over, _ = ML.mpe_merge([a, b], ctl, 'bend')
    assert evs == [(0.0, 8192), (1.0, 12000), (1.5, 8192), (2.0, 10000), (3.0, 11000)] and over == 1
    c = (2.5, 67, 0.25, 100, 3)                                       # staggered: C inside A, after B
    evs, over, _ = ML.mpe_merge([a, b, c], ctl + [(2.5, 3, 'bend', 9000)], 'bend')
    assert evs == [(0.0, 8192), (1.0, 12000), (1.5, 8192), (2.0, 10000), (2.5, 9000), (2.75, 10000),
                   (3.0, 11000)] and over == 1


def test_a_map_that_only_clashes_with_the_mpe_defaults_works_without_mpe(tmp_path):
    p = _lead(str(tmp_path / 'song'))
    api.automation_set(p, 'lead', 'inst.lane.breath', [[1, 0], [2, 1]])
    out = OPS['midi_write'](p, out=str(tmp_path / 'b.mid'), lanes={'breath': {'midi': 'pressure', 'range': [0, 1]}})
    assert 'breath -> pressure 0..1' in out
    with pytest.raises(api.OpError, match='both use pressure'):
        OPS['midi_write'](p, out=str(tmp_path / 'b.mid'), lanes={'breath': {'midi': 'pressure', 'range': [0, 1]}},
                          mpe=True)


def test_a_reused_channel_ends_its_old_note_before_the_new_notes_bend(tmp_path):
    path = str(tmp_path / 'r.mid')
    MF.write(path, [{'name': 'x', 'channel': 1, 'notes': [(0, 60, 1, 100, 1), (1, 64, 1, 100, 1)],
                     'events': [(1.0, ML.message('bend', None, 1, 12000))]}], bpm=120)
    data = open(path, 'rb').read()
    assert data.index(bytes([0x81, 60, 0])) < data.index(bytes([0xE1, 12000 & 0x7F, 12000 >> 7])) < \
        data.index(bytes([0x91, 64, 100]))


def test_a_multichannel_gm_file_is_not_mpe_unless_it_says_so_or_the_caller_does(tmp_path):
    path = str(tmp_path / 'gm.mid')                                   # no RPN at all: GM's +-2 holds
    top = ML.message('bend', None, 0, 16383), ML.message('bend', None, 1, 16383)
    MF.write(path, [{'name': 'band', 'channel': 0, 'notes': [(0, 60, 2, 100, 0), (0, 48, 2, 100, 1)],
                     'events': [(0.0, top[0]), (0.0, top[1])]}], bpm=120)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=2)
    api.track_add(q, 'solo', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    assert 'MPE' not in OPS['midi_read'](q, path)
    out = OPS['midi_read'](q, path, into={'band': 'solo'})
    assert '(bend range +-2)' in out and 'MPE' not in out and _lane(q, 'solo', 'bend')[0][1] == pytest.approx(2)
    out = OPS['midi_read'](q, path, into={'band': 'solo'}, mpe=True)
    assert 'per note (MPE)' in out and '(bend range +-48)' in out and _lane(q, 'solo', 'bend')[0][1] == \
        pytest.approx(48)


def _raw_file(path, events, ppq=480, tracks=None):
    """A file as another program writes it: one track of raw events (delta-time bytes included), or several."""
    trks = tracks or [events]
    body = b''
    for evs in trks:
        ev = b''.join(evs) + b'\x00\xff\x2f\x00'
        body += b'MTrk' + struct.pack('>I', len(ev)) + ev
    path.write_bytes(b'MThd' + struct.pack('>IHHH', 6, 0 if len(trks) == 1 else 1, len(trks), ppq) + body)
    return str(path)


def _rpn(ch, lsb, value):
    c = 0xB0 | ch
    return bytes([0, c, 101, 0, 0, c, 100, lsb, 0, c, 6, value])


def _solo(tmp_path, name='q'):
    q = str(tmp_path / name)
    api.project_new(q, bpm=120, length_bars=2)
    api.track_add(q, 'solo', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    return q


def test_an_mpe_configuration_with_no_members_is_mpe_off(tmp_path):
    path = _raw_file(tmp_path / 'off.mid', [_rpn(0, 6, 0), bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100]),
                                            bytes([0x83, 0x60, 0x81, 60, 0])])
    assert MF.read(path)['tracks'][0]['mpe'] is None
    q = _solo(tmp_path)
    out = OPS['midi_read'](q, path, into={'#0': 'solo'})
    assert 'MPE' not in out and '(bend range +-2)' in out and _lane(q, 'solo', 'bend') == [[0.0, 2.0]]


def test_an_upper_zone_file_reads_channel_16_as_its_master(tmp_path):
    path = _raw_file(tmp_path / 'up.mid', [_rpn(15, 6, 15), bytes([0, 0xE0, 0x7F, 0x7F, 0, 0x90, 60, 100]),
                                           bytes([0, 0xEF, 0, 0]), bytes([0x83, 0x60, 0x80, 60, 0])])
    f = MF.read(path)
    assert f['tracks'][0]['mpe'] == 15 and f['tracks'][0]['mpe_master'] == 15
    q = _solo(tmp_path)
    assert 'MPE (upper zone, master channel 16)' in OPS['midi_read'](q, path)
    out = OPS['midi_read'](q, path, into={'#0': 'solo'})
    assert 'per note (MPE)' in out and 'master channel (16) were left out' in out
    assert _lane(q, 'solo', 'bend') == [[0.0, 48.0]]          # channel 1 is a member here, its note bent to the top


def test_a_reversed_bend_range_is_refused(tmp_path):
    p = _lead(str(tmp_path / 'song'))
    with pytest.raises(api.OpError, match=r'\[-r, r\] with r > 0'):
        OPS['midi_write'](p, out=str(tmp_path / 'r.mid'), lanes={'bend': {'range': [2, -2]}})
    out = OPS['midi_write'](p, out=str(tmp_path / 'r.mid'), lanes={'level': {'range': [0, -40]}})
    assert 'level -> cc:11 0..-40' in out                     # a controller may run the other way


def test_each_bend_is_read_at_the_range_in_force_when_it_was_sent(tmp_path):
    ev = [_rpn(0, 0, 2), bytes([0, 0x90, 60, 100, 0, 0xE0, 0x7F, 0x7F]),            # +2 at range 2
          bytes([0x83, 0x60, 0xB0, 101, 0, 0, 0xB0, 100, 0, 0, 0xB0, 6, 12]),        # beat 1: range 12
          bytes([0, 0xE0, 0x7F, 0x7F]), bytes([0x83, 0x60, 0x80, 60, 0])]            # +12 at range 12
    path = _raw_file(tmp_path / 'rng.mid', ev)
    q = _solo(tmp_path)
    assert '(bend range +-2/12)' in OPS['midi_read'](q, path)
    out = OPS['midi_read'](q, path, into={'#0': 'solo'})
    assert '(bend range +-2/12)' in out
    pts = _lane(q, 'solo', 'bend')
    assert pts[0] == [0.0, pytest.approx(2.0)] and pts[-1] == [1.0, pytest.approx(12.0)]


def test_messages_on_one_tick_keep_their_file_order_by_channel_too(tmp_path):
    path = _raw_file(tmp_path / 'same.mid', [bytes([0, 0x90, 60, 100]),
                                             bytes([0x83, 0x60, 0xB0, 11, 127, 0, 0xB0, 11, 0]),
                                             bytes([0x83, 0x60, 0x80, 60, 0])])
    for key in ('#0', 'ch1'):
        q = _solo(tmp_path, 'q' + key[-1])
        OPS['midi_read'](q, path, into={key: 'solo'})
        assert _lane(q, 'solo', 'level') == [[1.0, -40.0]], key      # 127 then 0: the 0 is the value


def test_an_mpe_configuration_in_a_conductor_track_counts_for_the_notes_track(tmp_path):
    path = _raw_file(tmp_path / 'cond.mid', None, tracks=[
        [_rpn(0, 6, 15)],
        [bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100, 0, 0xE2, 0, 0x40]),
         bytes([0x83, 0x60, 0x92, 64, 100]), bytes([0x83, 0x60, 0x81, 60, 0, 0, 0x82, 64, 0])]])
    q = _solo(tmp_path)
    assert '#1 (no name): 2 notes, channel 2/3' in OPS['midi_read'](q, path) and 'MPE, expression' in \
        OPS['midi_read'](q, path)
    out = OPS['midi_read'](q, path, into={'#1': 'solo'})
    assert 'per note (MPE)' in out and '(bend range +-48)' in out
    assert _lane(q, 'solo', 'bend') == [[0.0, 48.0], [pytest.approx(1 - 1 / 960), 48.0], [1.0, 0.0]]
    out = OPS['midi_read'](q, path, into={'#1': 'solo'}, mpe=False)        # one plain part: +-2, channels merged
    assert 'MPE' not in out and '(bend range +-2)' in out
