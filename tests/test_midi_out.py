"""MIDI out (ledger:M113 step 3): a live track's notes to an output port at the moment its audio reaches the
speakers, kits on channel 10, MIDI clock at 24 pulses per beat with Start on a bar line, note-offs and All Notes Off
on unroute and at the set's end, a song played to a port from the command line, and errors that name what exists.
Every port here is a stand-in that records (time, bytes); no real MIDI device is opened, no audio is played."""
import sys
import time

import numpy as np
import pytest

from ismail import api
from ismail import midi as M
from ismail import midisend as MS
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.midi_out import MidiOut

import ismail.live.ops  # noqa: F401  (registers the live ops)


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakePort:
    """An output port that records what it was sent and when, on the clock it is given."""
    opened = {}
    clock = time.perf_counter

    def __init__(self, name):
        self.name, self.sent, self.resets, self.closed = name, [], 0, False
        FakePort.opened[name] = self

    def send(self, msg):
        self.sent.append((FakePort.clock(), bytes(msg)))

    def reset(self):
        self.resets += 1

    def close(self):
        self.resets += 1
        self.closed = True


PORTS = ['Test Synth', 'Test Loop']


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    clk = Clock()
    FakePort.opened, FakePort.clock = {}, clk
    e.mout = MidiOut(e, outputs=lambda: list(PORTS), opener=FakePort, now=clk, thread=False)
    e.clk = clk
    return e


def run(e, seconds):
    """The scheduler, the mixer and the device in turn on the fake clock; each message goes out at its own time.
    Returns the audio handed to the device."""
    out = []
    for _ in range(int(seconds * SR / BLOCK)):
        e.tick()
        e._fifo.append(e.mix_block().T.copy())
        e._fifo_n += BLOCK
        out.append(e._pull(BLOCK))
        end = e.clk.t + BLOCK / SR
        while (n := e.mout.sched.next_time()) is not None and n <= end:
            e.clk.t = max(e.clk.t, n)
            e.mout.sched.pump(e.clk.t)
        e.clk.t = end
    return np.concatenate(out)


def speaker_time(e, beat, t0=100.0):
    """Where `beat` leaves the device in run(): the device takes each block as it is mixed, from t0."""
    return t0 + (e.sample(beat) + e.safety.la + G.LAT_BUDGET) / SR


def msgs(port, kind=None):
    return [(t, b) for t, b in port.sent if kind is None or (b[0] & 0xF0 if b[0] < 0xF0 else b[0]) == kind]


def test_notes_go_out_on_their_channels_with_drums_on_10(eng):
    eng.cmd_track('lead', instrument='preset:pluck')
    eng.cmd_track('kick', instrument={'type': 'kick'})
    r = eng.cmd_midi_out(track='lead', port='synth', audio=False)
    assert "lead -> 'Test Synth' channel 1" in r and 'audio off' in r
    assert "channel 10 (drums)" in eng.cmd_midi_out(track='kick', port='Test Synth', audio=False)
    eng.cmd_queue([{'track': 'lead', 'notes': '0 C4 1 100; 2 E4 1 90', 'loop': 1, 'at': 'bar:2'},
                   {'track': 'kick', 'lanes': {'C1': 'x...x...'}, 'step': 0.25, 'loop': 1, 'at': 'bar:2'}])
    audio = run(eng, 4.5)
    assert np.max(np.abs(audio)) == 0                                   # audio=False: the synth is the sound
    port = FakePort.opened['Test Synth']
    ons = [b for _, b in msgs(port, 0x90)]
    assert bytes([0x90, 60, 100]) in ons and bytes([0x90, 64, 90]) in ons
    assert ons.count(bytes([0x99, 24, 100])) == 2                       # kick hits on channel 10
    offs = [b for _, b in msgs(port, 0x80)]
    assert bytes([0x80, 60, 0]) in offs and offs.count(bytes([0x89, 24, 0])) == 2
    assert 'midi out lead' in eng.cmd_status() and '2 notes sent' in eng.cmd_status()


def test_a_note_leaves_when_its_audio_reaches_the_speakers(eng):
    """Timing on a fake clock: each note-on at the speaker time of its beat, its note-off a duration later, and the
    MIDI kick lands on the sample where the audio kick leaves the device."""
    eng.cmd_track('kick', instrument={'type': 'kick'})
    eng.cmd_track('lead', instrument='preset:pluck')
    eng.cmd_midi_out(track='kick', port='Test Synth')                  # audio on: both
    eng.cmd_midi_out(track='lead', port='Test Loop', audio=False, offset_ms=-10)    # a port's own offset
    eng.cmd_queue([{'track': 'kick', 'lanes': {'C1': 'x...'}, 'step': 0.25, 'loop': 1, 'at': 'bar:2'},
                   {'track': 'lead', 'notes': '0.5 G4 1.5 80', 'beats': 4, 'loop': 1, 'at': 'bar:2'}])
    audio = run(eng, 4.5)
    port = FakePort.opened['Test Synth']
    (t_kick, _), = [m for m in msgs(port, 0x90) if m[1][0] == 0x99]
    assert t_kick == pytest.approx(speaker_time(eng, 4), abs=1e-9)    # bar 2 (the queue's next_bar)
    first = int(np.argmax(np.abs(audio[:, 0]) > 1e-4))                 # the audio kick, as the device got it
    assert abs(t_kick - (100.0 + first / SR)) < 0.002
    loop = FakePort.opened['Test Loop']
    (t_on, b_on), = msgs(loop, 0x90)
    (t_off, b_off), = msgs(loop, 0x80)
    assert b_on == bytes([0x90, 67, 80]) and b_off == bytes([0x80, 67, 0])
    assert t_on == pytest.approx(speaker_time(eng, 4.5) - 0.010, abs=1e-9)          # the port's offset
    assert t_off - t_on == pytest.approx(1.5 * 0.5, abs=1e-9)
    assert eng.mout.sched.lateness()['max_ms'] == 0.0


def test_clock_pulses_24_per_beat_and_starts_on_a_bar_line(tmp_path):
    e = Engine(str(tmp_path), bpm=90, bpb=3, workers=0, device='none')
    clk = Clock()
    FakePort.opened, FakePort.clock = {}, clk
    e.mout = MidiOut(e, outputs=lambda: list(PORTS), opener=FakePort, now=clk, thread=False)
    e.clk = clk
    run(e, 0.5)
    r = e.cmd_midi_clock(port='loop')
    assert "clock out -> 'Test Loop'" in r and 'Start on bar 2' in r
    run(e, 6.0)
    port = FakePort.opened['Test Loop']
    (t_start, _), = msgs(port, MS.START)
    pulses = [t for t, b in msgs(port, MS.PULSE)]
    assert t_start == pytest.approx(speaker_time(e, 3), abs=1e-9)     # bar 2 at 3 beats a bar
    bar_s = 3 * 60 / 90
    in_bar = [t for t in pulses if t_start - 1e-9 <= t < t_start + bar_s - 1e-9]
    assert len(in_bar) == 72 and in_bar[0] == pytest.approx(t_start, abs=1e-9)    # 24 x 3 beats
    gaps = np.diff(pulses)
    assert np.allclose(gaps, 60 / 90 / 24, atol=1.5 / SR)              # on the sample grid
    assert pulses[0] < t_start                                          # pulsing before Start: the receiver locks
    i = port.sent.index((t_start, bytes([MS.START])))
    assert port.sent[i + 1] == (t_start, bytes([MS.PULSE]))             # Start right before the bar's first pulse
    r = e.cmd_midi_clock(on=False)
    assert 'Stop sent' in r and port.sent[-1][1] == bytes([MS.STOP]) and port.closed


def test_clock_with_song_position_sends_the_bar_then_continue(eng):
    run(eng, 2.2)                                                       # mixed into bar 2
    assert 'Song Position bar 3' in eng.cmd_midi_clock(port='Test Loop', position=True)
    run(eng, 2.5)
    port = FakePort.opened['Test Loop']
    spp = [b for _, b in port.sent if b[0] == 0xF2]
    assert spp == [bytes([0xF2, 32, 0])]                                # bar 3 = 8 beats = 32 sixteenths
    assert msgs(port, MS.CONTINUE) and not msgs(port, MS.START)


def test_unroute_and_the_sets_end_turn_every_note_off(eng):
    eng.cmd_track('pad', instrument='preset:pluck')
    eng.cmd_track('bass', instrument='preset:pluck')
    eng.cmd_midi_out(track='pad', port='Test Synth', channel=3, audio=False)
    eng.cmd_midi_out(track='bass', port='Test Synth', channel=2, audio=False)
    eng.cmd_queue([{'track': 'pad', 'notes': '0 C4 8 100; 0 G4 8 100', 'beats': 8, 'loop': None, 'at': 'bar:2'},
                   {'track': 'bass', 'notes': '0 C2 8 100', 'beats': 8, 'loop': None, 'at': 'bar:2'}])
    run(eng, 2.5)
    port = FakePort.opened['Test Synth']
    assert len(msgs(port, 0x90)) == 3 and not msgs(port, 0x80)
    r = eng.cmd_midi_out(track='pad', remove=True)
    assert '2 held notes turned off' in r
    tail = [b for _, b in port.sent[-3:]]
    assert set(tail[:2]) == {bytes([0x82, 60, 0]), bytes([0x82, 67, 0])} and tail[2] == bytes([0xB2, 123, 0])
    eng.cmd_midi_clock(port='Test Synth')
    run(eng, 0.5)
    eng.mout.end(0.2)                                                   # live_stop with a 0.2 s fade
    t_end = eng.clk.t + 0.2
    run(eng, 1.0)
    late = [t for t, b in port.sent if t > t_end + 1e-9]
    assert late == []                                                   # nothing after the end
    end = [b for t, b in port.sent if t == pytest.approx(t_end)]
    assert end == [bytes([0x81, 36, 0]), bytes([0xB1, 123, 0]), bytes([MS.STOP])]
    eng.shutdown()
    assert port.closed


def test_errors_name_what_there_is(eng, monkeypatch):
    eng.cmd_track('lead', instrument='preset:pluck')
    with pytest.raises(LiveError, match=r"no MIDI output 'nope'; outputs: \['Test Synth', 'Test Loop'\]"):
        eng.cmd_midi_out(track='lead', port='nope')
    with pytest.raises(LiveError, match=r"matches \['Test Synth', 'Test Loop'\]"):
        eng.cmd_midi_out(track='lead', port='test')
    with pytest.raises(LiveError, match=r"no live track 'bas'; tracks: \['lead'\]"):
        eng.cmd_midi_out(track='bas', port='Test Synth')
    with pytest.raises(LiveError, match="sends no MIDI yet: name the port"):
        eng.cmd_midi_out(track='lead')
    with pytest.raises(LiveError, match="channel: 1 to 16"):
        eng.cmd_midi_out(track='lead', port='Test Synth', channel=17)
    assert 'MIDI outputs' in eng.cmd_midi_out() and 'no track sends MIDI' in eng.cmd_midi_out()
    monkeypatch.setattr(M.sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'mido', None)
    with pytest.raises(M.MidiError, match="pip install mido python-rtmidi"):
        M.find_output('anything', [])


def test_the_sender_thread_keeps_time(monkeypatch):
    """The sender on the real clock: clock pulses at 120 BPM for a second to a recording port. Reports the
    lateness; the bound is loose for shared CI machines (measured locally: about 0.1 ms at the median)."""
    FakePort.clock = time.perf_counter
    port = FakePort('Jitter')
    s = MS.Scheduler()
    t0 = s.now() + 0.05
    for k in range(48):
        s.at(t0 + k * 0.5 / 24, lambda: port.send(bytes([MS.PULSE])))
    s.start()
    try:
        while s.pending():
            time.sleep(0.01)
    finally:
        s.stop()
    late = s.lateness()
    assert late['n'] == 48 and len(port.sent) == 48
    sent = np.array([t for t, _ in port.sent]) - (t0 + np.arange(48) * 0.5 / 24)
    # never early, in order; the lateness bound is loose (a macOS CI runner's median was 8 ms, this laptop's 0.1)
    assert sent.min() >= -1e-4 and np.all(np.diff([t for t, _ in port.sent]) >= 0) and np.median(sent) < 0.05
    print(f"sender lateness: {late}")


def test_held_notes_end_by_their_own_note_off():
    p = FakePort('n')
    n = MS.Notes(p)
    n.on(0, 60, 100, 1)
    n.on(0, 60, 90, 2)                          # struck again while it sounds: the old one ends first
    n.off(0, 60, 1)                             # the first note's off must not cut the second
    assert [b for _, b in p.sent] == [bytes([0x90, 60, 100]), bytes([0x80, 60, 0]), bytes([0x90, 60, 90])]
    n.off(0, 60, 2)
    assert p.sent[-1][1] == bytes([0x80, 60, 0]) and not n.held
    assert MS.song_position(12) == bytes([0xF2, 48, 0]) and MS.song_position(4096) == bytes([0xF2, 127, 127])
    assert list(MS.pulses(0, 1)) == list(range(24)) and list(MS.pulses(-1, 0)) == list(range(-24, 0))


def test_midi_play_sends_a_song_to_a_port(tmp_path, monkeypatch):
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=240, length_bars=2)
    api.track_add(p, 'drums', instrument='preset:kit_basic')
    api.track_add(p, 'keys', instrument={"type": "synth", "oscs": [{"wave": "saw"}]})
    api.pattern_write(p, 'drums', 1, {"C1": "x...x..."})
    api.notes_write(p, 'keys', 1, "0 C4 1 100; 1 E4 1 80")
    FakePort.opened, FakePort.clock = {}, time.perf_counter
    monkeypatch.setattr(M, 'outputs', lambda: ['Test Synth'])
    monkeypatch.setattr(M, 'Output', FakePort)
    out = api.OPS['midi_play'](p, port='synth', bars=[1, 1], clock=True)
    assert "played 4 notes to 'Test Synth'" in out and 'keys: 2 notes, channel 1' in out and 'channel 10' in out
    port = FakePort.opened['Test Synth']
    assert port.closed and port.sent[-1][1] == bytes([MS.STOP])
    assert len(msgs(port, MS.PULSE)) == 24 * 2 + 24                    # a beat of pre-roll, then the notes' span
    (t_start, _), = msgs(port, MS.START)
    (t_c4, _), = [m for m in msgs(port, 0x90) if m[1][1] == 60]
    assert abs(t_c4 - t_start) < 0.01                                   # bar 1 starts with Start
    with pytest.raises(api.OpError, match=r"no MIDI output 'nope'; outputs: \['Test Synth'\]"):
        api.OPS['midi_play'](p, port='nope')
