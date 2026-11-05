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


def test_a_pad_fires_a_scene_on_each_press_and_never_plays(eng):
    eng.cmd_midi(source='pad grid', track='pad')
    scene = {'op': 'live_queue', 'section': 'drop',
             'clips': [{'track': 'lead', 'notes': '0 C4 1 100', 'bars': 1}, {'track': 'pad', 'stop': True}]}
    r = eng.cmd_map('drop', action=scene, midi='note:C2', source='pad grid', channel=10)
    assert "drop -> fires live_queue (2 clips, section drop) <- MIDI note:36 ch10, on 'pad grid', trigger" in r
    assert 'each press fires it at once' in r
    played = eng.midi.patches['pad grid']['played']
    send('pad grid', 0x99, 36, 100)                            # the press: the scene queues at the next bar
    send('pad grid', 0x89, 36, 0)                              # the release does nothing
    assert eng.midi.patches['pad grid']['played'] == played    # a bound pad is a control, never a key
    clips = [c for c in eng.tl.clips.values() if c.section == 'drop']
    assert [c.track for c in clips] == ['lead'] and clips[0].start == 4.0
    news = eng.drain_news()
    assert any(n.startswith('drop (midi note:36 ch10) fired live_queue: ') for n in news)
    send('pad grid', 0x99, 36, 100)                            # a second press queues it again (the same bar:
    assert any('fired live_queue' in n for n in eng.drain_news())    # its clip replaces the first)
    assert 'drop fired live_queue 2 times, at bars 1, 1' in eng.cmd_controls()
    assert 'drop -> fires live_queue' in eng.cmd_status()
    assert scene['clips'][0] == {'track': 'lead', 'notes': '0 C4 1 100', 'bars': 1}   # the map keeps its own copy


def test_a_button_and_the_agent_fire_a_tempo_change(eng):
    eng.cmd_map('faster', action={'op': 'live_tempo', 'bpm': 128, 'at': 'next_bar'}, midi='cc:80', source='knob')
    send('knob box', 0xB0, 80, 127)
    send('knob box', 0xB0, 80, 0)
    news = [n for n in eng.drain_news() if 'fired live_tempo' in n]
    assert len(news) == 1 and 'tempo 120 -> 128 BPM at bar 2' in news[0]      # the release fires nothing
    assert eng.tmap.segs[-1][2] == 128
    eng.cmd_map('slower', action={'op': 'live_tempo', 'bpm': 100})   # no MIDI: the stage or the agent fires it
    r = eng.cmd_control('slower', 1)
    assert r['applied'] == 1.0 and 'fired' in r
    assert eng.cmd_control('slower', 0)['applied'] == 0.0      # below 0.5: nothing fires


def test_an_action_map_says_what_is_wrong_and_keeps_the_old_map(eng):
    with pytest.raises(LiveError, match=r"action: \{'op': one of live_queue, live_tempo, live_transition"):
        eng.cmd_map('x', action={'op': 'live_mute'}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match=r"action live_queue: unknown arguments \['bpm'\]"):
        eng.cmd_map('x', action={'op': 'live_queue', 'bpm': 1}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match="no live track 'nope'"):
        eng.cmd_map('x', action={'op': 'live_queue', 'clips': [{'track': 'nope', 'stop': True}]}, midi='note:36',
                    source='pad')
    with pytest.raises(LiveError, match='a tempo from 20 to 400'):
        eng.cmd_map('x', action={'op': 'live_tempo', 'bpm': 900}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match='deck'):
        eng.cmd_map('x', action={'op': 'live_transition', 'to': 'Z'}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match="fires on a button press: a 'note:<n>' pad, a 'cc:<n>' button or a 'program"):
        eng.cmd_map('x', action={'op': 'live_tempo', 'bpm': 100}, midi='bend', source='knob')
    with pytest.raises(LiveError, match="target, param, range and at belong to a map that moves a param"):
        eng.cmd_map('x', target='lead', action={'op': 'live_tempo', 'bpm': 100}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match="mode 'toggle' is for a map that moves a param"):
        eng.cmd_map('x', action={'op': 'live_tempo', 'bpm': 100}, midi='note:36', source='pad', mode='toggle')
    with pytest.raises(LiveError, match="mode 'trigger' fires an action"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='note:36', source='pad', mode='trigger')
    assert 'x' not in eng.controls.maps and 'pad grid' not in FakeInput.opened
    eng.cmd_map('hit', action={'op': 'live_tempo', 'bpm': 100}, midi='note:36', source='pad')
    with pytest.raises(LiveError, match=r"hit keeps its old map \(fires live_tempo \(100 BPM\)"):
        eng.cmd_map('hit', action={'op': 'live_queue', 'clips': [{'track': 'nope', 'stop': True}]},
                    midi='note:36', source='pad')


def test_a_press_that_fails_says_so_in_the_news_once(eng):
    eng.cmd_map('go', action={'op': 'live_queue', 'clips': [{'track': 'pad', 'notes': '0 C2 1', 'bars': 1}]},
                midi='note:40', source='pad')
    eng.cmd_track('pad', remove=True)                          # the scene's track went away after the map
    send('pad grid', 0x90, 40, 100)
    send('pad grid', 0x90, 40, 100)
    news = [n for n in eng.drain_news() if n.startswith('control go')]
    assert len(news) == 1 and "no live track 'pad'" in news[0] and 'live_map it again' in news[0]


def test_an_action_is_checked_at_map_time_as_its_op_would_check_it(eng):
    eng.cmd_deck('B')
    clip = {'track': 'lead', 'notes': '0 C4 1', 'bars': 1}
    for action, says in (
            ({'op': 'live_tempo', 'bpm': 130, 'at': 'bogus'}, "action live_tempo: at='bogus': use next_beat"),
            ({'op': 'live_queue', 'clips': [clip], 'cancel_at': 'next_bar'}, 'cancel_at goes with cancel_section'),
            ({'op': 'live_queue', 'clips': [dict(clip, at='bogus')]}, r"clip \[0\]: at='bogus'"),
            ({'op': 'live_queue', 'clips': [dict(clip, beat0='x')]}, 'beat0 is a number of beats'),
            ({'op': 'live_queue', 'clips': [dict(clip, context=[5])]}, r'context a list of \[beat, midi, dur, vel\]'),
            ({'op': 'live_queue', 'clips': [dict(clip, loop=[1])]}, r"loop=\[1\]; use a count >= 1 or 'forever'"),
            ({'op': 'live_transition', 'to': 'B', 'style': 'bogus'}, "style='bogus': use one of blend"),
            ({'op': 'live_transition', 'to': 'B', 'bars': 'x'}, "bars='x', a whole number of bars"),
            ({'op': 'live_transition', 'to': 'B', 'at': 'whenever'}, "at='whenever'"),
            ({'op': 'live_transition', 'to': 'B', 'stop_from': 'maybe'}, "stop_from='maybe', true")):
        with pytest.raises(LiveError, match=says):
            eng.cmd_map('x', action=action, midi='note:44', source='pad')
    assert 'x' not in eng.controls.maps
    eng.cmd_map('x', action={'op': 'live_queue', 'clips': [dict(clip, at='bar:9', beat0=0.5)]}, midi='note:44',
                source='pad')                                  # what the op takes is mapped
    assert 'x' in eng.controls.maps


def test_a_press_whose_op_raises_anything_else_is_a_news_line_and_the_other_moves_still_apply(eng):
    eng.cmd_map('ctx', action={'op': 'live_queue', 'clips': [{'track': 'lead', 'notes': '0 C4 1', 'bars': 1}]},
                midi='note:50', source='pad')
    eng.cmd_map('knob', target='lead', param='pan', range=[-1, 1], midi='cc:10', source='pad')
    eng.controls.maps['ctx']['action']['args']['clips'][0]['context'] = [5]    # a press that fails late
    send('pad grid', 0x90, 50, 100)                            # no control thread: applied on the driver's thread
    news = [n for n in eng.drain_news() if n.startswith('control ctx')]
    assert len(news) == 1 and 'live_queue could not take this action' in news[0] and 'TypeError' in news[0]
    eng._ctl_thread = True                                     # a running set: the press and a knob, one drain
    send('pad grid', 0x90, 50, 100)
    send('pad grid', 0xB0, 10, 127)
    eng.drain_controls()
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0) and not eng._ctl_q
    with pytest.raises(LiveError, match='give it the arguments live_queue takes'):
        eng.cmd_control('ctx', 1)


def test_quick_presses_before_one_drain_each_fire(eng):
    eng.cmd_map('hit', action={'op': 'live_queue', 'clips': [{'track': 'lead', 'notes': '0 C4 1', 'bars': 1,
                                                                'at': 'now'}]}, midi='note:36', source='pad')
    eng._ctl_thread = True
    for _ in range(3):
        send('pad grid', 0x90, 36, 100)
        send('pad grid', 0x80, 36, 0)
    eng.drain_controls()
    assert len([n for n in eng.drain_news() if 'fired' in n]) == 3
    assert 'hit fired live_queue 3 times' in eng.cmd_controls()


def test_a_program_change_fires_an_action_or_flips_a_param(eng):
    eng.cmd_midi(source='pad grid', track='pad')
    send('pad grid', 0xC0, 4, 0)                               # program 5, nothing mapped: heard, numbered from 1
    assert "'pad grid' program:5 ch1: last 5, 1 messages" in eng.cmd_controls()
    r = eng.cmd_map('verse', action={'op': 'live_tempo', 'bpm': 100}, midi='program:5', source='pad grid')
    assert "fires live_tempo (100 BPM) <- MIDI program:5, any channel, on 'pad grid', trigger" in r
    assert 'program:5' not in eng.cmd_controls()
    send('pad grid', 0xC0, 5, 0)                               # program 6: not this map's
    assert not any('fired' in n for n in eng.drain_news())
    send('pad grid', 0xC0, 4, 0)
    assert any(n.startswith('verse (midi program:5 ch1) fired live_tempo') for n in eng.drain_news())
    eng.cmd_map('mute', target='lead', param='volume_db', range=[0, -120], curve='switch', midi='program:6',
                source='pad grid', mode='toggle')
    send('pad grid', 0xC0, 5, 0)
    assert eng.tracks['lead']['volume_db'] == -120
    send('pad grid', 0xC0, 5, 0)
    assert eng.tracks['lead']['volume_db'] == 0
    with pytest.raises(LiveError, match=r"program:<1-128>, numbered as a GM list"):
        eng.cmd_map('x', action={'op': 'live_tempo', 'bpm': 100}, midi='program:0', source='pad grid')
    with pytest.raises(LiveError, match="a 'program:<n>' is one message, with no release"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='program:3', source='pad grid')


def test_an_encoder_map_takes_its_own_step_size(eng):
    r = eng.cmd_map('coarse', target='lead', param='pan', range=[-1, 1], midi='cc:20', source='knob',
                    mode='relative', step=1 / 32)
    assert 'relative, a tick 0.03125 of the range' in r
    eng.cmd_map('fine', target='pad', param='pan', range=[-1, 1], midi='cc:21', source='knob', mode='relative64',
                step=1 / 512)
    eng.cmd_track('lead', pan=0.0)
    eng.cmd_track('pad', pan=0.0)
    send('knob box', 0xB0, 20, 2)                              # two ticks up: 2/32 of the range
    send('knob box', 0xB0, 21, 63)                             # one tick down: 1/512
    assert eng.tracks['lead']['pan'] == pytest.approx(2 * 2 / 32)
    assert eng.tracks['pad']['pan'] == pytest.approx(-2 / 512)
    with pytest.raises(LiveError, match="step is how far one tick of an endless encoder moves"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:22', source='knob', step=0.1)
    with pytest.raises(LiveError, match=r"step 0: a fraction of the range per tick, above 0 and at most 1"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:22', source='knob', mode='relative',
                    step=0)
    with pytest.raises(LiveError, match="add midi='cc:<n>'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], step=0.1)


# ------------------------------------------------------------------ encoder acceleration (D-244)
def test_acceleration_is_one_step_a_tick_when_slow_and_grows_with_speed_up_to_its_cap():
    f = O.accel_factor
    assert f(None, 1, 4) == 1 and f(0.01, 1, None) == 1        # the first tick (or a turn back), and off
    assert f(O.ACCEL_TICK_S, 1, 4) == 1 and f(0.5, 1, 4) == 1    # 10 ticks a second or slower: one step each
    assert f(0.05, 1, 4) == pytest.approx(2)                     # twice as fast, twice as far
    assert f(0.05, 2, 4) == pytest.approx(4)                     # two ticks in one message: faster still
    assert f(0.001, 1, 4) == 4 and f(0.001, 1, 16) == 16         # never past the cap


def test_an_encoder_with_accel_moves_further_when_turned_fast_and_one_without_does_not(eng, monkeypatch):
    t = [100.0]
    monkeypatch.setattr(O.Controls, 'clock', staticmethod(lambda: t[0]))
    r = eng.cmd_map('sweep', target='lead', param='pan', range=[-1, 1], midi='cc:20', source='knob',
                    mode='relative', accel=8)
    assert 'relative, accel up to x8 when turned fast' in r
    eng.cmd_map('plain', target='pad', param='pan', range=[-1, 1], midi='cc:21', source='knob', mode='relative')
    status = eng.cmd_status()
    assert 'sweep -> lead pan' in status and 'accel up to x8 when turned fast' in status
    assert status.count('accel up to') == 1                     # off by default
    eng.cmd_track('lead', pan=0.0)
    eng.cmd_track('pad', pan=0.0)
    for _ in range(5):                                          # a fast turn: a tick every 25 ms
        t[0] += 0.025
        send('knob box', 0xB0, 20, 1)
        send('knob box', 0xB0, 21, 1)
    # the first tick is one step, the next four 4 steps each (0.1 s / 25 ms); each step 1/128 of a range 2 wide
    assert eng.tracks['lead']['pan'] == pytest.approx((1 + 4 * 4) * 2 / 128)
    assert eng.tracks['pad']['pan'] == pytest.approx(5 * 2 / 128)
    lead = eng.tracks['lead']['pan']
    t[0] += 0.5
    send('knob box', 0xB0, 20, 1)                               # slow again: one step
    assert eng.tracks['lead']['pan'] == pytest.approx(lead + 2 / 128)
    t[0] += 0.01
    send('knob box', 0xB0, 20, 127)                             # turned back: it starts slow, one step down
    assert eng.tracks['lead']['pan'] == pytest.approx(lead)
    assert 'accel up to x4' in eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:22',
                                           source='knob', mode='relative64', accel=True)
    with pytest.raises(LiveError, match="accel makes a fast turn of an endless encoder move further"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:22', source='knob', accel=4)
    with pytest.raises(LiveError, match=r"accel 20: the most steps one tick moves when turned fast, above 1 and at "
                                        r"most 16 \(True = 4\)"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='cc:22', source='knob', mode='relative',
                    accel=20)
    with pytest.raises(LiveError, match="add midi='cc:<n>'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], accel=4)


# ------------------------------------------------------------------ poly pressure and MPE expression as controls (D-244)
def test_poly_pressure_drives_a_control_by_one_key_or_the_hardest_pressed_key(eng):
    r = eng.cmd_map('press', target='lead', param='pan', range=[-1, 1], midi='poly', source='pad grid')
    assert "<- MIDI poly (the hardest-pressed key held), any channel, on 'pad grid', absolute" in r
    assert "The keys still play; each key's poly pressure is this control's now" in r
    eng.cmd_map('c4', target='pad', param='pan', range=[0, 1], midi='poly:C4', source='pad grid')
    send('pad grid', 0x90, 60, 100)
    send('pad grid', 0x90, 64, 100)
    send('pad grid', 0xA0, 60, 127)                            # C4 hard
    send('pad grid', 0xA0, 64, 0x40)                           # E4 softer
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0)     # the hardest key held: C4
    assert eng.tracks['pad']['pan'] == pytest.approx(1.0)
    send('pad grid', 0x80, 60, 0)                              # C4 let go: E4's pressure now, C4's map at rest
    assert eng.tracks['lead']['pan'] == pytest.approx(-1 + 2 * 64 / 127)
    assert eng.tracks['pad']['pan'] == 0.0
    send('pad grid', 0xA0, 64, 0)
    assert eng.tracks['lead']['pan'] == -1.0
    assert [x['via'] for x in eng.controls.read('press')][0] == 'midi poly ch1'     # live_controls reads it
    with pytest.raises(LiveError, match="sends a pressure or a bend as it moves: mode 'absolute' or 'pickup'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='poly', source='pad grid', mode='toggle')
    with pytest.raises(LiveError, match="'poly:<0-127 or a name like C4>'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='poly:X9', source='pad grid')


def test_a_poly_map_never_takes_a_key_and_its_pressure_drives_no_lane(eng):
    eng.cmd_track('keys', instrument={'type': 'kick'})
    eng.cmd_midi(source='pad grid', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]},
                 lanes={'level': {'midi': 'pressure', 'range': [0, 6]}})
    eng.cmd_map('press', target='lead', param='pan', range=[-1, 1], midi='poly', source='pad grid')
    played = eng.midi.patches['pad grid']['played']
    send('pad grid', 0x90, 60, 100)
    assert eng.midi.patches['pad grid']['played'] == played + 1      # the key plays
    v = eng.midi.voices[-1]
    send('pad grid', 0xA0, 60, 127)
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0) and eng.midi.xv[v].poly == {}   # the map's, not the lane's


def test_an_mpe_notes_own_pressure_bend_and_timbre_drive_controls_newest_note_first(eng):
    r = eng.cmd_map('swell', target='lead', param='fx:delay.mix', range=[0, 1], midi='mpe:pressure', source='knob')
    assert "<- MIDI mpe:pressure (the newest note's own, on its zone's member channels), on 'knob box'" in r
    eng.cmd_map('slide', target='lead', param='pan', range=[-1, 1], midi='mpe:bend', source='knob')
    eng.cmd_map('tone', target='lead', param='fx:filter.cutoff', range=[200, 8000], curve='log',
                midi='mpe:timbre', source='knob')
    mix = lambda: eng.tracks['lead']['path'].chain.fx[1]['mix']          # noqa: E731
    send('knob box', 0xB1, 74, 127)                            # the note's timbre, just before its note-on
    send('knob box', 0x91, 60, 100)                            # a note on channel 2
    assert cutoff(eng) == pytest.approx(8000)
    send('knob box', 0xD1, 100, 0)
    assert mix() == pytest.approx(100 / 127)
    send('knob box', 0xB2, 74, 0)
    send('knob box', 0x92, 64, 100)                            # a newer note on channel 3 takes over
    assert cutoff(eng) == pytest.approx(200) and mix() == 0.0    # its own pressure: none yet
    send('knob box', 0xD1, 120, 0)                             # the older note's pressure: not the control's now
    assert mix() == 0.0
    send('knob box', 0xE2, 0x7F, 0x7F)                         # the newest note bends up
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0)
    send('knob box', 0x82, 64, 0)                              # it lets go: back to the older note's values
    assert mix() == pytest.approx(120 / 127) and cutoff(eng) == pytest.approx(8000)
    assert eng.tracks['lead']['pan'] == pytest.approx(0.0, abs=1e-3)   # channel 2 never bent: at rest
    send('knob box', 0xE0, 0x7F, 0x7F)                         # the master channel bends: not a note's own
    assert eng.tracks['lead']['pan'] == pytest.approx(0.0, abs=1e-3)
    with pytest.raises(LiveError, match="leave channel out"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='mpe:bend', source='knob', channel=2)
    with pytest.raises(LiveError, match="'mpe:bend', 'mpe:pressure' or 'mpe:timbre'"):
        eng.cmd_map('x', target='lead', param='pan', range=[-1, 1], midi='mpe:slide', source='knob')


def test_mpe_controls_follow_the_inputs_zone_and_unmapped_ones_are_listed(eng):
    eng.cmd_map('swell', target='lead', param='pan', range=[0, 1], midi='mpe:pressure', source='knob')
    for d1, d2 in ((101, 0), (100, 6), (6, 15)):               # the controller: upper zone, channel 16 the master
        send('knob box', 0xBF, d1, d2)
    send('knob box', 0x9F, 60, 100)                            # channel 16 is the master now: not a note's
    send('knob box', 0xDF, 127, 0)
    assert eng.tracks['lead']['pan'] == 0.0
    send('knob box', 0x90, 62, 100)                            # channel 1 is a member in the upper zone
    send('knob box', 0xD0, 127, 0)
    assert eng.tracks['lead']['pan'] == pytest.approx(1.0)
    send('knob box', 0xE4, 0, 96)                              # a member's bend and timbre nobody maps
    send('knob box', 0xB5, 74, 30)
    heard = eng.cmd_controls()
    assert "'knob box' mpe:bend member channels: last 4096, 1 messages" in heard
    assert "'knob box' mpe:timbre member channels: last 30" in heard and 'cc:101' not in heard
    eng.cmd_controls(source='pad grid')                         # listen to the pads
    send('pad grid', 0xA1, 61, 90)                             # poly pressure nobody maps
    assert "'pad grid' poly ch2: last 90 (key C#4), 1 messages" in eng.cmd_controls()
    eng.cmd_map('tone', target='pad', param='pan', range=[0, 1], midi='mpe:timbre', source='knob')
    assert 'mpe:timbre' not in eng.cmd_controls()              # mapped: off the unmapped list


def test_a_poly_or_mpe_map_lets_go_of_its_keys_on_all_notes_off_an_unpatch_and_an_unplug(eng):
    eng.midi.poll()                                             # the inputs there are, known
    for how in ('cc123', 'cc120', 'repatch', 'unplug'):
        eng.cmd_midi(source='pad grid', instrument={'type': 'kick'})
        eng.cmd_map('press', target='lead', param='pan', range=[0, 1], midi='poly', source='pad grid')
        send('pad grid', 0x90, 60, 100)
        send('pad grid', 0xA0, 60, 127)                         # C4 pressed hard
        if how == 'cc123':
            send('pad grid', 0xB0, 123, 0)
        elif how == 'cc120':
            send('pad grid', 0xB0, 120, 0)
        elif how == 'repatch':
            eng.cmd_midi(source='pad grid', remove=True)
            eng.cmd_midi(source='pad grid', instrument={'type': 'kick'})
        else:
            eng.inputs.remove('pad grid')
            eng.midi.poll()
            eng.inputs.append('pad grid')
            eng.midi.poll()
        send('pad grid', 0x90, 64, 100)
        send('pad grid', 0xA0, 64, 20)                          # the only key held now, pressed softly
        assert eng.tracks['lead']['pan'] == pytest.approx(20 / 127), how
        eng.cmd_map('press', remove=True)
        eng.cmd_midi(source='pad grid', remove=True)
    eng.cmd_map('slide', target='lead', param='pan', range=[-1, 1], midi='mpe:bend', source='pad grid')
    send('pad grid', 0x91, 60, 100)                             # a note on channel 2
    send('pad grid', 0xE1, 0x7F, 0x7F)                          # bent to the top
    send('pad grid', 0xB0, 123, 0)                              # All Notes Off on the master: every note of the zone
    send('pad grid', 0x92, 64, 100)                             # a new note on channel 3, at rest
    send('pad grid', 0x82, 64, 0)                               # it ends, and no older note is held
    assert eng.tracks['lead']['pan'] == pytest.approx(0, abs=1e-3)


def test_live_controls_lists_an_nrpn_knob_and_hides_only_bend_range_and_mpe_settings(eng):
    eng.cmd_controls(source='knob')
    for v in (10, 40):                                          # an NRPN knob: select 1/16, data entry
        for d1, d2 in ((99, 1), (98, 16), (6, v), (38, 0)):
            send('knob box', 0xB0, d1, d2)
    for d1, d2 in ((101, 0), (100, 0), (6, 12), (38, 0)):       # RPN 0, the bend range: a setting
        send('knob box', 0xB1, d1, d2)
    out = eng.cmd_controls()
    assert "'knob box' cc:99 ch1" in out and "'knob box' cc:6 ch1: last 40" in out and "cc:38 ch1" in out
    assert 'ch2' not in out
    eng.cmd_map('k', target='lead', param='pan', range=[0, 1], midi='poly:60', source='pad grid')
    send('pad grid', 0xA0, 62, 30)                              # another key's pressure: heard, then the map takes
    eng.cmd_map('k2', target='lead', param='pan', range=[0, 1], midi='poly:C4', source='pad grid')
    assert 'poly' not in eng.cmd_controls(source='pad grid')    # the poly row, by any key's map
