"""MIDI as a control source for live_map (MIDI parity step 5): a controller's CC, 14-bit CC pair, pitch bend,
channel pressure or a pad's note drives a live param with the map's range, curve and timing, in modes absolute,
pickup (takeover), relative (two encoder codings) and toggle; one control drives several maps; a bound message is a
control and never a key; every move is logged for live_controls. All silent: device='none', no render workers, and
a stand-in for the MIDI driver fed synthetic messages."""
import json
import os
import threading

import pytest

from ismail.dsp import SR
from ismail.live import outputs as O
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.midi_play import MidiPlay


class FakeInput:
    opened = {}

    def __init__(self, name, on_msg):
        self.name, self.on = name, on_msg
        FakeInput.opened[name] = self

    def close(self):
        FakeInput.opened.pop(self.name, None)


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.inputs = ['knob box', 'pad grid']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    FakeInput.opened.clear()
    e.cmd_track('lead', instrument={'type': 'kick'}, fx=[{'type': 'filter', 'mode': 'lp', 'cutoff': 8000},
                                                       {'type': 'delay', 'mix': 0.2}], at='now')
    e.cmd_track('pad', instrument={'type': 'kick'})
    e.cmd_bus('room')
    return e


def send(name, st, d1, d2):
    FakeInput.opened[name].on(0.0, st, d1, d2)


def cutoff(e):
    return e.tracks['lead']['path'].chain.fx[0]['cutoff']


def run(e, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        e.tick()
        e.mix_block()


def test_a_cc_drives_an_effect_param_through_its_range_and_curve_and_is_logged(eng):
    r = eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], curve='log',
                    midi='cc:74', source='knob', channel=1)
    assert "tone -> lead fx:filter.cutoff (log 200..8000) <- MIDI cc:74 ch1, on 'knob box', absolute" in r
    assert 'knob box' in FakeInput.opened                      # opened by the map, with no keys patched
    send('knob box', 0xB0, 74, 127)
    assert cutoff(eng) == pytest.approx(8000)
    send('knob box', 0xB0, 74, 0)
    assert cutoff(eng) == pytest.approx(200)
    send('knob box', 0xB1, 74, 127)                            # channel 2: not this map's
    assert cutoff(eng) == pytest.approx(200)
    run(eng, 1.0)
    send('knob box', 0xB0, 74, 64)
    assert cutoff(eng) == pytest.approx(200 * 40 ** (64 / 127))
    eng.controls.flush()
    recs = [json.loads(x) for x in open(os.path.join(eng.root, 'live', 'controls.jsonl'))]
    assert [r['via'] for r in recs] == ['midi cc:74 ch1'] * 3 and recs[0]['value'] == 1.0
    back = eng.cmd_controls('tone')
    assert 'tone -> lead fx:filter.cutoff: 3 moves' in back
    pts = json.loads(back.split('automation points [bar, value]: ')[1])
    assert [round(p[1]) for p in pts] == [8000, 200, round(200 * 40 ** (64 / 127))] and pts[2][0] > pts[0][0] >= 1
    assert '<- MIDI cc:74' in eng.cmd_status()


def test_one_cc_drives_several_params_and_a_reversed_range_moves_the_other_way(eng):
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:1', source='knob')
    r = eng.cmd_map('wet', target='lead', param='fx:delay.mix', range=[0.6, 0.0], midi='cc:1', source='knob')
    assert 'the same MIDI control also drives tone' in r
    eng.cmd_map('level', target='bus:room', param='volume_db', range=[-40, 0], midi='cc:1', source='knob')
    send('knob box', 0xB0, 1, 127)
    fx = eng.tracks['lead']['path'].chain.fx
    assert fx[0]['cutoff'] == pytest.approx(8000) and fx[1]['mix'] == pytest.approx(0.0)
    assert eng.buses['room']['volume_db'] == 0
    send('knob box', 0xB0, 1, 0)
    assert fx[1]['mix'] == pytest.approx(0.6) and eng.buses['room']['volume_db'] == -40


def test_14_bit_pairs_bend_and_pressure(eng):
    eng.cmd_map('fine', target='lead', param='pan', range=[-1, 1], midi='cc14:7', source='knob')
    send('knob box', 0xB0, 7, 64)                              # MSB alone: 7 bits until an LSB is seen
    assert eng.tracks['lead']['pan'] == pytest.approx(-1 + 2 * 64 / 127)
    send('knob box', 0xB0, 39, 0)                              # LSB (7 + 32): now 14 bits
    assert eng.tracks['lead']['pan'] == pytest.approx(-1 + 2 * 8192 / 16383)
    send('knob box', 0xB0, 7, 127)
    send('knob box', 0xB0, 39, 127)
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0)
    eng.cmd_map('wheel', target='pad', param='pan', range=[-1, 1], midi='bend', source='knob')
    send('knob box', 0xE0, 0, 64)                              # centre (8192)
    assert eng.tracks['pad']['pan'] == pytest.approx(0.0)
    send('knob box', 0xE0, 127, 127)
    assert eng.tracks['pad']['pan'] == pytest.approx(1.0)
    send('knob box', 0xE0, 0, 0)
    assert eng.tracks['pad']['pan'] == pytest.approx(-1.0)
    eng.cmd_map('press', target='bus:room', param='volume_db', range=[-30, 0], midi='pressure', source='knob')
    send('knob box', 0xD0, 127, 0)
    assert eng.buses['room']['volume_db'] == 0


def test_pickup_waits_for_the_knob_to_reach_the_param_and_lets_go_when_something_else_moves_it(eng):
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[0, 10000], midi='cc:74', source='knob',
                mode='pickup')
    eng.cmd_fx('lead', 'filter', {'cutoff': 5000})             # the agent set it: the knob is elsewhere
    send('knob box', 0xB0, 74, 10)
    send('knob box', 0xB0, 74, 30)
    assert cutoff(eng) == 5000
    assert 'waiting: the knob is at 0.24, the param at 0.50' in eng.cmd_status()
    send('knob box', 0xB0, 74, 70)                             # crossed 0.5 on the way: it takes over
    assert cutoff(eng) == pytest.approx(10000 * 70 / 127)
    send('knob box', 0xB0, 74, 90)
    assert cutoff(eng) == pytest.approx(10000 * 90 / 127)
    eng.cmd_control('tone', 0.1)                               # the stage moved it: the knob lets go
    send('knob box', 0xB0, 74, 95)
    assert cutoff(eng) == pytest.approx(1000)
    send('knob box', 0xB0, 74, 13)                             # within 2/127 of the param: caught
    assert cutoff(eng) == pytest.approx(10000 * 13 / 127)
    eng.cmd_map('fresh', target='pad', param='volume_db', range=[-40, 0], midi='cc:75', source='knob', mode='pickup')
    eng.cmd_track('pad', volume_db=-40)
    send('knob box', 0xB0, 75, 127)                            # far off, no crossing yet
    assert eng.tracks['pad']['volume_db'] == -40


def test_relative_encoders_toggles_and_pads(eng):
    eng.cmd_map('enc', target='lead', param='pan', range=[-1, 1], midi='cc:20', source='knob', mode='relative')
    eng.cmd_track('lead', pan=0.0)
    send('knob box', 0xB0, 20, 1)                              # one step up from where the param is
    assert eng.tracks['lead']['pan'] == pytest.approx(2 / 128)
    send('knob box', 0xB0, 20, 127)                            # two's complement: 127 = one step down
    send('knob box', 0xB0, 20, 126)                            # 126 = two steps down
    assert eng.tracks['lead']['pan'] == pytest.approx(-4 / 128)  # a step is 1/128 of the range: 2/128 of pan
    eng.cmd_map('enc64', target='pad', param='pan', range=[-1, 1], midi='cc:21', source='knob', mode='relative64')
    eng.cmd_track('pad', pan=0.0)
    send('knob box', 0xB0, 21, 67)
    n = len(eng.controls.read('enc64'))
    send('knob box', 0xB0, 21, 64)                             # 64 = no step: nothing moves, nothing logged
    assert eng.tracks['pad']['pan'] == pytest.approx(6 / 128) and len(eng.controls.read('enc64')) == n == 1
    send('knob box', 0xB0, 21, 0)                              # 64 steps down, twice: clamps at the end
    send('knob box', 0xB0, 21, 0)
    assert eng.tracks['pad']['pan'] == -1.0
    eng.cmd_map('mute', target='bus:room', param='volume_db', range=[0, -120], curve='switch', midi='cc:30',
                source='knob', mode='toggle')
    send('knob box', 0xB0, 30, 127)
    send('knob box', 0xB0, 30, 0)                              # the release does not flip it back
    assert eng.buses['room']['volume_db'] == -120
    send('knob box', 0xB0, 30, 127)
    assert eng.buses['room']['volume_db'] == 0
    eng.cmd_map('hold', target='lead', param='fx:delay.mix', range=[0, 1], midi='note:C2', source='pad grid')
    send('pad grid', 0x99, 36, 100)
    assert eng.tracks['lead']['path'].chain.fx[1]['mix'] == 1.0
    send('pad grid', 0x89, 36, 0)
    assert eng.tracks['lead']['path'].chain.fx[1]['mix'] == 0.0
    eng.cmd_map('flip', target='pad', param='volume_db', range=[-120, 0], curve='switch', midi='note:37',
                source='pad grid', mode='toggle')
    eng.cmd_track('pad', volume_db=0)
    send('pad grid', 0x99, 37, 100)
    send('pad grid', 0x89, 37, 0)
    assert eng.tracks['pad']['volume_db'] == -120


def test_next_bar_timing_schedules_the_move_on_the_bar_line(eng):
    run(eng, 0.6)
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], at='next_bar', midi='cc:74',
                source='knob')
    send('knob box', 0xB0, 74, 127)
    ramp = eng.ramps[('track:lead', 0, 'cutoff')].r[-1]
    assert ramp.p0 == eng.sample(4.0) and ramp.v1 == pytest.approx(8000)


def test_a_bound_note_is_a_control_not_a_key_and_unmapped_messages_are_listed(eng, tmp_path):
    eng.cmd_midi(source='pad grid', track='pad')
    eng.cmd_map('hold', target='lead', param='fx:delay.mix', range=[0, 1], midi='note:36', source='pad grid')
    played = eng.midi.patches['pad grid']['played']
    send('pad grid', 0x90, 36, 100)                            # the bound pad: a control move
    send('pad grid', 0x90, 38, 100)                            # another pad: a key
    assert eng.midi.patches['pad grid']['played'] == played + 1
    assert eng.tracks['lead']['path'].chain.fx[1]['mix'] == 1.0
    eng.midi.flush()
    keys = [json.loads(x) for x in open(os.path.join(str(tmp_path), 'live', 'midi', 'pad_grid.jsonl'))]
    assert [k['d1'] for k in keys] == [38]
    send('pad grid', 0xB2, 22, 99)                             # a knob nothing maps
    assert "'pad grid' cc:22 ch3: last 99, 1 messages" in eng.cmd_controls()
    r = eng.cmd_controls(source='knob')                        # listen to a controller with nothing mapped
    assert "listening on 'knob box'" in r and 'knob box' in FakeInput.opened
    send('knob box', 0xE1, 0, 96)
    assert "'knob box' bend ch2: last 4096" in eng.cmd_controls()


def test_inputs_open_once_close_when_unused_and_come_back_with_their_maps(eng):
    eng.midi.poll()
    eng.cmd_midi(source='knob', track='pad')
    first = FakeInput.opened['knob box']
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    assert FakeInput.opened['knob box'] is first               # one handle, shared by the keys and the map
    eng.cmd_midi(source='knob', remove=True)
    assert 'knob box' in FakeInput.opened                      # the map still uses it
    eng.inputs.remove('knob box')
    eng.midi.poll()
    assert any('controls on knob box: unplugged' in n for n in eng.drain_news()) and 'knob box' not in FakeInput.opened
    eng.inputs.append('knob box')
    eng.midi.poll()
    assert any('controls on knob box: plugged back in' in n for n in eng.drain_news())
    send('knob box', 0xB0, 74, 127)
    assert cutoff(eng) == pytest.approx(8000)
    assert 'unmapped' in eng.cmd_map('tone', remove=True) and 'knob box' not in FakeInput.opened


def test_what_it_says_when_asked_wrong(eng):
    with pytest.raises(LiveError, match=r"source: which MIDI input .*'knob box', 'pad grid'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:1')
    with pytest.raises(LiveError, match=r"one of 'cc:<0-127>'.*live_controls\(source=<the input>\) listens"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='knob 1', source='knob')
    with pytest.raises(LiveError, match=r'cc14:<0-31>'):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc14:40', source='knob')
    with pytest.raises(LiveError, match="endless encoder's steps on a 'cc:<n>'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='bend', source='knob', mode='relative')
    with pytest.raises(LiveError, match="sends positions, not the param's units"):
        eng.cmd_map('x', target='lead', param='pan', curve='raw', midi='cc:1', source='knob')
    with pytest.raises(LiveError, match="add midi='cc:<n>'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], source='knob')
    with pytest.raises(LiveError, match="no live track 'nope'"):
        eng.cmd_map('x', target='nope', param='pan', range=[-1, 1], midi='cc:1', source='knob')
    assert 'x' not in eng.controls.maps and 'knob box' not in FakeInput.opened
    eng.inputs = ['knob box']
    assert "on 'knob box'" in eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:1')


def test_unscale_inverts_scale():
    for rng, curve in (([-40, 0], 'linear'), ([200, 8000], 'log'), ([0.6, 0.0], 'linear')):
        for v in (0.0, 0.3, 1.0):
            assert O.unscale(O.scale(v, rng, curve), rng, curve) == pytest.approx(v)
    assert O.unscale(-120, [-120, 0], 'switch') == 0.0 and O.unscale(3.0, None, 'raw') is None


def test_binding_a_held_note_or_the_pedal_lets_go_of_them(eng, monkeypatch):
    from ismail.live import midi_play as MP
    monkeypatch.setattr(MP, 'LO', 58)                          # a few notes render fast
    monkeypatch.setattr(MP, 'HI', 62)
    eng.cmd_midi(source='pad grid', instrument='preset:pluck')
    p = eng.midi.patches['pad grid']
    send('pad grid', 0x90, 60, 100)
    send('pad grid', 0xB0, 64, 127)                            # pedal down
    assert p['held'] and p['sustain']
    eng.cmd_map('hit', target='lead', param='fx:delay.mix', range=[0, 1], midi='note:60', source='pad grid')
    assert not p['held'] and not p['sustain'] and all(v.rel is not None for v in eng.midi.voices)
    send('pad grid', 0x80, 60, 0)                              # its release is now the control's
    send('pad grid', 0x90, 61, 100)
    send('pad grid', 0xB0, 64, 127)
    eng.cmd_map('ped', target='pad', param='pan', range=[-1, 1], midi='cc:64', source='pad grid')
    assert not p['held'] and not p['sustain']
    send('pad grid', 0xB0, 64, 0)                              # a control now: the keys never wait for it
    assert eng.tracks['pad']['pan'] == -1


def test_a_failed_remap_keeps_the_old_map_and_its_input(eng):
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    with pytest.raises(LiveError, match=r"no live track 'nope'.*tone keeps its old map \(lead fx:filter.cutoff"):
        eng.cmd_map('tone', target='nope', param='pan', range=[-1, 1], midi='cc:74', source='pad')
    with pytest.raises(LiveError, match="mode 'sideways'.*tone keeps its old map"):
        eng.cmd_map('tone', target='lead', param='pan', range=[-1, 1], midi='cc:74', source='knob', mode='sideways')

    def refuse(name, on_msg):
        raise OSError(f"could not open MIDI input {name!r}")
    eng.midi.opener = refuse
    with pytest.raises(LiveError, match="could not open.*tone keeps its old map.*call live_map again"):
        eng.cmd_map('tone', target='pad', param='pan', range=[-1, 1], midi='cc:74', source='pad')
    assert 'knob box' in FakeInput.opened and 'pad grid' not in eng.midi.ins
    send('knob box', 0xB0, 74, 127)
    assert cutoff(eng) == pytest.approx(8000)


def test_a_move_in_flight_never_lands_on_a_new_target_and_moves_coalesce(eng):
    eng._ctl_thread = True                                     # as in a running set: moves wait for the control thread
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    for v in (10, 60, 127):
        send('knob box', 0xB0, 74, v)
    assert list(eng._ctl_q) == ['tone'] and cutoff(eng) == 8000   # queued: nothing applied on the driver's thread
    eng.drain_controls()
    assert cutoff(eng) == pytest.approx(8000) and len(eng.controls.read('tone')) == 1   # the latest value only
    send('knob box', 0xB0, 74, 0)
    eng.cmd_map('tone', target='pad', param='pan', range=[-1, 1], midi='cc:74', source='knob')
    eng.drain_controls()                                       # the old map's move is dropped, not put on pad's pan
    assert cutoff(eng) == pytest.approx(8000) and eng.tracks['pad']['pan'] == 0


def test_the_unmapped_list_reads_safely_while_a_knob_streams(eng):
    errors, stop = [], threading.Event()

    def stream():
        i = 0
        while not stop.is_set():
            eng.midi._hear('knob box', 0xB0 | (i % 16), i % 120, i % 128)   # new keys churn the list
            i += 1
    t = threading.Thread(target=stream)
    t.start()
    try:
        for _ in range(3000):
            try:
                eng.midi.heard_lines()
            except RuntimeError as e:
                errors.append(e)
                break
    finally:
        stop.set()
        t.join()
    assert errors == []


def test_listening_closes_the_input_after_a_while_and_a_map_takes_it_over(eng, monkeypatch):
    from ismail.live import midi_play as MP
    assert 'for 120 s more' in eng.cmd_controls(source='knob')
    assert 'knob box' in FakeInput.opened
    eng.midi.listening['knob box'] = 0                         # its time is up
    eng.midi.poll()
    eng.midi.poll()
    assert 'knob box' not in FakeInput.opened and not eng.midi.listening
    eng.cmd_controls(source='knob')
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    assert 'knob box' not in eng.midi.listening and 'knob box' in FakeInput.opened

    def refuse(name, on_msg):
        raise OSError(f"could not open MIDI input {name!r}")
    monkeypatch.setattr(eng.midi, 'opener', refuse)
    with pytest.raises(LiveError, match='could not open'):
        eng.cmd_controls(source='pad')
    assert 'pad grid' not in eng.midi.listening and MP.LISTEN_S == 120


def test_a_bound_control_leaves_the_unmapped_list(eng):
    eng.cmd_controls(source='knob')                            # listening: the person turns two knobs
    send('knob box', 0xB0, 74, 90)
    send('knob box', 0xB0, 75, 40)
    send('knob box', 0xB1, 74, 12)                             # cc:74 on channel 2 too
    r = eng.cmd_controls()
    assert "'knob box' cc:74 ch1: last 90" in r and "'knob box' cc:75 ch1" in r
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob',
                channel=1)
    r = eng.cmd_controls()
    assert "cc:74 ch1" not in r and "'knob box' cc:75 ch1: last 40" in r
    assert "'knob box' cc:74 ch2: last 12" in r                # another channel: still not mapped
    send('knob box', 0xB0, 74, 100)                            # a bound move is a control, never heard
    assert "cc:74 ch1" not in eng.cmd_controls()
    eng.cmd_map('fine', target='lead', param='pan', range=[-1, 1], midi='cc14:3', source='knob')
    send('knob box', 0xB0, 3, 1)
    send('knob box', 0xB0, 35, 1)
    eng.cmd_map('fine', remove=True)
    assert "cc:3 " not in eng.cmd_controls()                   # heard while bound: never listed
    eng.midi.heard[('knob box', 'cc:35', 1)] = [5, 1]          # heard before a bind of its pair
    eng.cmd_map('fine', target='lead', param='pan', range=[-1, 1], midi='cc14:3', source='knob')
    assert "cc:35" not in eng.cmd_controls()


def test_queued_moves_add_up_for_relative_pickup_and_toggle(eng):
    eng._ctl_thread = True                                     # a running set: moves wait for the control thread
    eng.cmd_map('enc', target='lead', param='pan', range=[-1, 1], midi='cc:20', source='knob', mode='relative')
    eng.cmd_track('lead', pan=0.0)
    for _ in range(10):
        send('knob box', 0xB0, 20, 1)                          # ten steps before one drain
    eng.drain_controls()
    assert eng.tracks['lead']['pan'] == pytest.approx(20 / 128)
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[0, 10000], midi='cc:74', source='knob',
                mode='pickup')
    eng.cmd_fx('lead', 'filter', {'cutoff': 5000})
    seq = list(range(60, 128))                                 # crosses the param, then keeps turning up
    for i in range(0, len(seq), 3):
        for v in seq[i:i + 3]:
            send('knob box', 0xB0, 74, v)
        eng.drain_controls()
    assert cutoff(eng) == pytest.approx(10000)                 # it held on: never let go of its own moves
    eng.cmd_map('mute', target='bus:room', param='volume_db', range=[0, -120], curve='switch', midi='cc:30',
                source='knob', mode='toggle')
    for v in (127, 0, 127, 0):                                 # two presses before one drain
        send('knob box', 0xB0, 30, v)
    eng.drain_controls()
    assert eng.buses['room']['volume_db'] == 0
    for v in (127, 0, 127, 0, 127, 0):
        send('knob box', 0xB0, 30, v)
    eng.drain_controls()
    assert eng.buses['room']['volume_db'] == -120


def test_a_trigger_button_that_never_sends_0_still_toggles(eng):
    t = [100.0]
    eng.controls.clock = lambda: t[0]
    eng.cmd_map('mute', target='bus:room', param='volume_db', range=[0, -120], curve='switch', midi='cc:30',
                source='knob', mode='toggle')
    send('knob box', 0xB0, 30, 127)
    assert eng.buses['room']['volume_db'] == -120
    t[0] += 0.02
    send('knob box', 0xB0, 30, 127)                            # a repeat at once: the same press
    assert eng.buses['room']['volume_db'] == -120
    t[0] += 0.5
    send('knob box', 0xB0, 30, 127)                            # a repeat after a gap: a new press
    assert eng.buses['room']['volume_db'] == 0


def test_cc14_lsb_first_and_pairs_per_channel(eng):
    eng.cmd_map('fine', target='lead', param='pan', range=[0, 1], midi='cc14:7', source='knob', channel=1)
    send('knob box', 0xB0, 7, 11)
    send('knob box', 0xB0, 39, 5)                              # an LSB: 14 bits from now on
    send('knob box', 0xB0, 39, 9)                              # LSB first, then its MSB
    send('knob box', 0xB0, 7, 12)
    assert eng.tracks['lead']['pan'] == pytest.approx(((12 << 7) | 9) / 16383)
    eng.cmd_map('wide', target='pad', param='pan', range=[0, 1], midi='cc14:8', source='knob')   # every channel
    send('knob box', 0xB0, 8, 100)                             # ch1 MSB
    send('knob box', 0xB1, 40, 0)                              # ch2 LSB: not the ch1 pair's
    assert eng.tracks['pad']['pan'] == pytest.approx(100 / 127)
    send('knob box', 0xB1, 8, 3)                               # ch2's own MSB, with its LSB
    assert eng.tracks['pad']['pan'] == pytest.approx((3 << 7) / 16383)


def test_an_input_that_fails_to_reopen_is_tried_again(eng):
    eng.midi.poll()
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    eng.inputs.remove('knob box')
    eng.midi.poll()
    held = {'busy': True}

    def opener(name, on_msg):
        if held['busy']:
            raise OSError('another program has it open')
        return FakeInput(name, on_msg)
    eng.midi.opener = opener
    eng.inputs.append('knob box')
    eng.midi.poll()
    assert any('back, but it did not open' in n for n in eng.drain_news()) and 'knob box' not in FakeInput.opened
    eng.midi.poll()                                            # still held: tried again, said once
    assert not any('did not open' in n for n in eng.drain_news())
    with pytest.raises(LiveError, match="did not open.*close it in the program that holds it, then call again"):
        eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], midi='cc:74', source='knob')
    with pytest.raises(LiveError, match='did not open'):
        eng.cmd_controls(source='knob')
    held['busy'] = False
    eng.midi.poll()                                            # the next poll opens it
    assert any('controls on knob box: plugged back in' in n for n in eng.drain_news())
    send('knob box', 0xB0, 74, 127)
    assert cutoff(eng) == pytest.approx(8000)
