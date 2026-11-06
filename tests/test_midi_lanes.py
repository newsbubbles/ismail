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


def test_poly_pressure_follows_the_newest_key_held_onto_the_channels_pressure_lane(tmp_path):
    path = str(tmp_path / 'poly.mid')
    poly = [(0, 60, 40), (1, 64, 100), (1.5, 64, 110), (2.5, 60, 20)]          # (beat, key, pressure)
    MF.write(path, [{'name': 'keys', 'channel': 0, 'notes': [(0, 60, 4, 100), (1, 64, 1, 100)],
                     'events': [(b, bytes([0xA0, k, v])) for b, k, v in poly]}], bpm=120)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=2)
    api.track_add(q, 'keys', instrument={'type': 'synth', 'oscs': [{'wave': 'saw'}]})
    out = OPS['midi_read'](q, path, into={'keys': 'keys'})
    assert "not read: poly pressure (4 messages); poly pressure goes onto the lane that takes pressure" in out
    out = OPS['midi_read'](q, path, into={'keys': 'keys'}, lanes={'level': {'midi': 'pressure', 'range': [0, 127]}})
    assert "poly pressure: 4 per-key messages read as their channel's pressure (the newest key still held wins" in out
    lane = _lane(q, 'keys', 'level')
    for beat, want in ((0.5, 40), (1.25, 100), (1.75, 110), (2.25, 40), (3, 20)):   # E4 lets go at 2: C4's again
        assert ML.value_at(lane, beat) == pytest.approx(want, abs=0.6), beat


def test_midi_read_replace_clears_a_lanes_old_points_over_the_notes_it_replaced_and_keeps_the_one_after(tmp_path):
    path = str(tmp_path / 'bend.mid')
    MF.write(path, [{'name': 'keys', 'channel': 0, 'notes': [(0, 60, 4, 100)],
                     'events': [(0, bytes([0xE0, 0, 96])), (1, bytes([0xE0, 0, 64]))]}], bpm=120)
    q = str(tmp_path / 'q')
    api.project_new(q, bpm=120, length_bars=4)
    api.track_add(q, 'keys', instrument={'type': 'synth', 'oscs': [{'wave': 'saw'}]})
    api.automation_set(q, 'keys', 'inst.lane.bend', [[1.5, -1], [2, -2], [2.5, 0]])   # beats 2, 4 and 6
    OPS['midi_read'](q, path, into={'keys': 'keys'})
    bend = _lane(q, 'keys', 'bend')
    assert [2.0, -1.0] not in bend and [2, -1] not in bend              # under the file's note: replaced
    assert ML.value_at(bend, 3.0) == pytest.approx(0, abs=0.01)
    assert ML.value_at(bend, 4.0) == -2 and ML.value_at(bend, 6.0) == 0   # from the note's end on: the song's


def test_poly_pressure_on_a_channel_with_channel_pressure_is_left_to_it():
    notes = [(0, 60, 2, 100, 0), (0, 62, 2, 100, 1)]
    ctl = [(0, 0, 'poly:60', 50), (0.5, 0, 'pressure', 70), (0, 1, 'poly:62', 30), (1, 1, 'poly:62', 90)]
    out, read, kept = ML.poly_to_channel(notes, ctl)
    assert (read, kept) == (2, 1)
    assert [c for c in out if c[1] == 1] == [(0, 1, 'pressure', 30), (1, 1, 'pressure', 90)]
    assert (0, 0, 'poly:60', 50) in out and (0.5, 0, 'pressure', 70) in out


def test_poly_pressure_a_key_with_none_of_its_own_is_at_rest_even_struck_again():
    # the newest key sends no pressure: the lane is at 0 under it, not the older key's value
    notes = [(0.0, 60, 4.0, 90, 0), (1.0, 64, 2.0, 90, 0)]
    out = ML.poly_to_channel(notes, [(0.5, 0, 'poly:60', 100)])[0]
    assert out == [(0.0, 0, 'pressure', 0), (0.5, 0, 'pressure', 100), (1.0, 0, 'pressure', 0),
                   (3.0, 0, 'pressure', 100)]
    # a key struck again starts from nothing, not from its last strike's pressure
    notes = [(0.0, 60, 1.0, 90, 0), (2.0, 60, 1.0, 90, 0)]
    out = ML.poly_to_channel(notes, [(0.5, 0, 'poly:60', 90)])[0]
    assert out[-1] == (2.0, 0, 'pressure', 0)


# ------------------------------------------------------------------ MPE's upper zone and zone sizes (MIDI parity, D-244)
def test_an_mpe_configuration_sizes_a_zone_and_the_two_zones_share_the_channels():
    assert ML.mcm({}, 0, 15) == {0: 15} and ML.mcm({}, 15, 15) == {15: 15}
    assert ML.mcm({0: 15}, 15, 5) == {15: 5, 0: 9}             # the lower zone shrinks to make room
    assert ML.mcm({15: 15}, 0, 15) == {0: 15}                  # a full zone takes the other's channels: it is off
    assert ML.mcm({0: 7, 15: 7}, 15, 0) == {0: 7}               # 0 members: that zone off
    assert ML.mcm({0: 7}, 4, 3) == {0: 7}                      # RPN 6 is a master's (channel 1 or 16) only
    assert ML.zone_members(0, 3) == [1, 2, 3] and ML.zone_members(15, 3) == [14, 13, 12]
    assert ML.zone_of({0: 7, 15: 7}, 8) == 15 and ML.zone_of({0: 3}, 8) is None
    assert ML.zone_text({15: 15}) == 'upper zone, channel 16 the master, 1-15 a note each'
    assert ML.zone_text({0: 7, 15: 7}) == ('lower zone, channel 1 the master, 2-8 a note each; upper zone, channel 16 '
                                           'the master, 9-15 a note each')
    assert ML.zone_text({0: 15}) == 'channel 1 the master, 2-16 a note each'
    assert ML.mpe_zone(True) == {0: 15} and ML.mpe_zone('Upper') == {15: 15} and ML.mpe_zone(False) == {}
    with pytest.raises(ML.LaneMapError, match=r"'upper' \(the upper zone"):
        ML.mpe_zone('middle')


def test_mpe_upper_writes_channel_16_as_the_master_and_reads_back_per_note(tmp_path):
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=2)
    api.track_add(p, 'lead', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    api.notes_write(p, 'lead', 1, "0 C5 1 100; 1 D5 1 100; 2 E5,G5 2 100")
    api.automation_set(p, 'lead', 'inst.lane.bend', [[1, 0], [2, 2], [2.5, -1]])
    path = str(tmp_path / 'up.mid')
    out = OPS['midi_write'](p, out=path, mpe='upper')
    assert 'MPE upper zone (channel 16 the master, a channel of 1-15 per note)' in out
    tr = MF.read(path)['tracks'][1]
    assert tr['mpe_zones'] == {15: 15} and tr['mpe_master'] == 15
    chans = [n[4] for n in tr['notes']]
    assert 15 not in chans and chans[0] == 14 and len(set(chans[2:])) == 2   # from channel 15 down
    q = _solo(tmp_path)
    assert 'MPE (upper zone, master channel 16)' in OPS['midi_read'](q, path)
    back = OPS['midi_read'](q, path, into={'lead': 'solo'})
    assert 'per note (MPE), upper zone, channel 16 the master, 1-15 a note each' in back
    assert '(bend range +-48)' in back
    _same_curve(_lane(p, 'lead', 'bend'), _lane(q, 'solo', 'bend'), 96 / 8191 + 1e-6, 0, 3.95)


def test_a_split_synth_gets_both_zones_seven_members_each_and_each_part_reads_back_in_its_own(tmp_path):
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=2)
    for name, notes, bend in (('low', "0 C3 2 100; 2 D3 2 100", 1), ('high', "0 C5 2 100; 2 E5,G5 2 100", -1)):
        api.track_add(p, name, instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
        api.notes_write(p, name, 1, notes)
        api.automation_set(p, name, 'inst.lane.bend', [[1, 0], [2, bend]])
    path = str(tmp_path / 'split.mid')
    out = OPS['midi_write'](p, out=path, mpe={'low': 'lower', 'high': 'upper'})
    assert 'MPE lower zone (channel 1 the master, a channel of 2-8 per note)' in out
    assert 'MPE upper zone (channel 16 the master, a channel of 9-15 per note)' in out
    f = MF.read(path)
    low, high = f['tracks'][1], f['tracks'][2]
    assert low['mpe_zones'] == {0: 7} and high['mpe_zones'] == {15: 7}
    assert {n[4] for n in low['notes']} <= set(range(1, 8)) and {n[4] for n in high['notes']} <= set(range(8, 15))
    q = _solo(tmp_path)
    api.track_add(q, 'solo2', instrument={'type': 'code', 'voice': 'flute', 'tail': 0.5})
    back = OPS['midi_read'](q, path, into={'low': 'solo', 'high': 'solo2'})
    assert 'per note (MPE), lower zone, channel 1 the master, 2-8 a note each' in back
    assert 'per note (MPE), upper zone, channel 16 the master, 9-15 a note each' in back
    _same_curve(_lane(p, 'low', 'bend'), _lane(q, 'solo', 'bend'), 96 / 8191 + 1e-6, 0, 3.95)
    _same_curve(_lane(p, 'high', 'bend'), _lane(q, 'solo2', 'bend'), 96 / 8191 + 1e-6, 0, 3.95)


def test_a_file_that_does_not_say_reads_as_the_upper_zone_when_asked_and_a_zone_counts_its_members_only(tmp_path):
    # notes on channels 15 and 14, the master (16) bent: read as the upper zone, the master is left out
    path = _raw_file(tmp_path / 'u.mid', [bytes([0, 0xEF, 0x7F, 0x7F, 0, 0xEE, 0x7F, 0x7F, 0, 0x9E, 60, 100]),
                                          bytes([0x83, 0x60, 0x8E, 60, 0, 0, 0xED, 0, 0, 0, 0x9D, 62, 100]),
                                          bytes([0x83, 0x60, 0x8D, 62, 0])])
    q = _solo(tmp_path)
    out = OPS['midi_read'](q, path, into={'#0': 'solo'}, mpe='upper')
    assert 'master channel (16) were left out' in out and '(bend range +-48)' in out
    assert _lane(q, 'solo', 'bend')[0] == [0.0, 48.0] and ML.value_at(_lane(q, 'solo', 'bend'), 1.5) == -48
    with pytest.raises(api.OpError, match="mpe 'side'"):
        OPS['midi_read'](q, path, into={'#0': 'solo'}, mpe='side')
    # a lower zone of 3 members (channels 2-4): a bent note on channel 9 is not the zone's
    path = _raw_file(tmp_path / 'z.mid', [_rpn(0, 6, 3), bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100]),
                                          bytes([0x81, 0x70, 0xE8, 0, 0, 0, 0x98, 64, 100]),
                                          bytes([0x81, 0x70, 0x81, 60, 0, 0, 0x88, 64, 0])])
    q = _solo(tmp_path, 'q2')
    assert 'MPE (lower zone, channel 1 the master, 2-4 a note each), expression' in OPS['midi_read'](q, path)
    out = OPS['midi_read'](q, path, into={'#0': 'solo'})
    assert 'per note (MPE), lower zone, channel 1 the master, 2-4 a note each' in out
    assert _lane(q, 'solo', 'bend') == [[0.0, 48.0]]               # channel 9's bend to the bottom is not read


FLUTE = {'type': 'code', 'voice': 'flute', 'tail': 0.5}


def _song(p, tracks):
    api.project_new(p, bpm=120, length_bars=2)
    for name, inst, notes in tracks:
        api.track_add(p, name, instrument=inst)
        if notes:
            api.notes_write(p, name, 1, notes)
    return p


def test_a_plain_track_beside_mpe_gets_a_channel_outside_the_zones_and_its_lanes_read_back(tmp_path):
    p = _song(str(tmp_path / 'split'), [('low', FLUTE, "0 C3 2 100"), ('high', FLUTE, "0 C5 1 100; 1 E5 1 100"),
                                        ('bass', FLUTE, "0 C2 4 100")])
    api.automation_set(p, 'bass', 'inst.lane.bend', [[1, 0], [2, 1.5], [3, -1]])
    path = str(tmp_path / 'split.mid')
    out = OPS['midi_write'](p, out=path, mpe={'low': 'lower', 'high': 'upper'})
    assert "MPE lower zone: 6 members, not 7, so bass gets a channel outside it" in out
    assert 'bass: 1 notes, channel 8' in out and 'a channel of 2-7 per note' in out
    f = MF.read(path)
    assert f['tracks'][1]['mpe_zones'] == {0: 6} and {n[4] for n in f['tracks'][3]['notes']} == {7}
    q = _solo(tmp_path)
    back = OPS['midi_read'](q, path, into={'bass': 'solo'})
    assert 'lane bend: 3 points from 512 bend messages (bend range +-2)' in back and 'MPE' not in back
    _same_curve(_lane(p, 'bass', 'bend'), _lane(q, 'solo', 'bend'), 2 / 8191 * 2 + 1e-6, 0, 3.9)
    # the lower zone alone, as before: 15 members leave no channel, so it gives one up
    out = OPS['midi_write'](p, out=path, mpe=['low', 'high'])
    assert "MPE lower zone: 14 members, not 15, so bass gets a channel outside it" in out
    assert 'bass: 1 notes, channel 16' in out


def test_a_kit_keeps_channel_10_and_a_zone_that_held_it_shrinks_and_says_so(tmp_path):
    kit = {'type': 'kit', 'map': {'C2': {'type': 'kick'}}}
    p = _song(str(tmp_path / 'k'), [('lead', FLUTE, "0 C5 1 100; 1 E5 1 100"), ('drums', kit, "0 C2 1 100")])
    path = str(tmp_path / 'k.mid')
    out = OPS['midi_write'](p, out=path, mpe=True)
    assert "MPE lower zone: 8 members, not 15, so channel 10 stays the kit's" in out
    assert 'a channel of 2-9 per note' in out and 'drums: 1 notes, channel 10' in out
    out = OPS['midi_write'](p, out=path, mpe='upper')
    assert "MPE upper zone: 5 members, not 15, so channel 10 stays the kit's" in out
    assert 'a channel of 11-15 per note' in out


def test_a_part_on_a_zones_master_or_outside_every_zone_reads_as_a_plain_part(tmp_path):
    # the file configures the upper zone of 7 (members 9-15); one track plays on channel 16 (the master), one on 2
    path = _raw_file(tmp_path / 'm.mid', None, tracks=[
        [_rpn(15, 6, 7)],
        [bytes([0, 0xEF, 0x7F, 0x7F, 0, 0x9F, 60, 100, 0x83, 0x60, 0x8F, 60, 0])],
        [bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 62, 100, 0x83, 0x60, 0x81, 62, 0])]])
    q = _solo(tmp_path)
    api.track_add(q, 'solo2', instrument=FLUTE)
    back = OPS['midi_read'](q, path, into={'#1': 'solo', '#2': 'solo2'})
    assert back.count('lane bend: 1 points from 1 bend messages (bend range +-2)') == 2 and 'MPE' not in back
    assert _lane(q, 'solo', 'bend') == [[0.0, 2.0]] and _lane(q, 'solo2', 'bend') == [[0.0, 2.0]]


def test_one_track_with_both_zones_notes_warns_that_the_other_zones_expression_is_left_out(tmp_path):
    path = _raw_file(tmp_path / 'b.mid', None, tracks=[[_rpn(0, 6, 7), _rpn(15, 6, 7)], [bytes([
        0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100, 0, 0x92, 64, 100, 0, 0xEE, 0x7F, 0x7F, 0, 0x9E, 72, 100,
        0x83, 0x60, 0x81, 60, 0, 0, 0x82, 64, 0, 0, 0x8E, 72, 0])]])
    back = OPS['midi_read'](_solo(tmp_path), path, into={'#1': 'solo'})
    assert ('WARNING solo: 1 notes are on channel 15, outside the lower zone, channel 1 the master, 2-8 a note each '
            'this part is read in: the notes are written, their own 1 expression messages left out') in back


def test_an_mpe_configuration_after_the_notes_does_not_change_how_they_read_and_one_during_them_warns(tmp_path):
    def off_at(dt):                     # the lower zone, a bent note on channel 2, then MPE off dt ticks in
        return [_rpn(0, 6, 15), bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100]), bytes([0x83, 0x60, 0x81, 60, 0]),
                bytes([dt, 0xB0, 101, 0, 0, 0xB0, 100, 6, 0, 0xB0, 6, 0]),
                bytes([0, 0x91, 62, 100, 0x83, 0x60, 0x81, 62, 0])]
    q = _solo(tmp_path)
    back = OPS['midi_read'](q, _raw_file(tmp_path / 'm.mid', off_at(0x10)), into={'#0': 'solo'})
    assert 'bend range +-48), per note (MPE)' in back and _lane(q, 'solo', 'bend')[0] == [0.0, 48.0]
    assert ('WARNING solo: the file changes its MPE configuration at bar 1, while these notes play; read as at the '
            'first note (channel 1 the master, 2-16 a note each)') in back
    # the configurations of a format 1 file count in time order, whichever track holds them
    path = _raw_file(tmp_path / 'f1.mid', None, tracks=[
        [bytes([0x87, 0x40, 0xB0, 101, 0, 0, 0xB0, 100, 6, 0, 0xB0, 6, 0])],      # MPE off, 2 beats in
        [_rpn(0, 6, 15), bytes([0, 0xE1, 0x7F, 0x7F, 0, 0x91, 60, 100, 0x83, 0x60, 0x81, 60, 0])]])
    back = OPS['midi_read'](_solo(tmp_path, 'q2'), path, into={'#1': 'solo'})
    assert 'per note (MPE)' in back and 'WARNING' not in back


def test_midi_write_takes_mpe_1_and_0_and_a_zone_per_track_as_true_and_a_split_with_one_part_silent(tmp_path):
    p = _song(str(tmp_path / 's'), [('lead', FLUTE, "0 C5 1 100"), ('pad', FLUTE, None)])
    path = str(tmp_path / 's.mid')
    assert 'MPE (channel 1 the master, a channel of 2-16 per note)' in OPS['midi_write'](p, out=path, mpe=1,
                                                                                          tracks=['lead'])
    assert 'lead: 1 notes, channel 1' in OPS['midi_write'](p, out=path, mpe=0, tracks=['lead'])
    assert 'MPE (channel 1 the master' in OPS['midi_write'](p, out=path, mpe={'lead': True}, tracks=['lead'])
    api.notes_write(p, 'pad', 3, "0 C3 1 100")          # outside bars 1-2
    out = OPS['midi_write'](p, out=path, mpe={'lead': 'lower', 'pad': 'upper'}, bars=[1, 2])
    assert 'MPE (channel 1 the master, a channel of 2-16 per note)' in out   # the only zone written: all 15
