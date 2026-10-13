"""Played keys (live_midi): a MIDI input patched to an instrument in the live engine sounds at the next device
pull, swaps sound on request, follows note-offs, the sustain pedal, channel and transpose, notices inputs coming
and going, and logs every key with the beat heard. All silent: device='none', and a stand-in for the MIDI driver."""
import json
import os

import numpy as np
import pytest

from ismail.dsp import SR
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live import midi_play as MP
from ismail.live.midi_play import MidiPlay


class FakeInput:
    opened = {}

    def __init__(self, name, on_msg):
        self.name, self.on = name, on_msg
        FakeInput.opened[name] = self

    def close(self):
        FakeInput.opened.pop(self.name, None)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 48)              # two octaves render faster than five
    monkeypatch.setattr(MP, 'HI', 72)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.inputs = ['flmcp Request', 'ismail keys']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    FakeInput.opened.clear()
    return e


def key(name, st, d1, d2):
    FakeInput.opened[name].on(0.0, st, d1, d2)


def pulls(e, seconds):
    """The mixer and the device in turn, as the engine's threads run them."""
    out = []
    for _ in range(int(seconds * SR / BLOCK)):
        e._fifo.append(e.mix_block().T.copy())
        e._fifo_n += BLOCK
        out.append(e._pull(BLOCK))
    return np.concatenate(out)


def test_a_key_sounds_at_the_next_pull_and_stops_after_its_release(eng):
    r = eng.cmd_midi(source='keys', instrument='preset:pluck')
    assert 'ismail keys: patched' in r and 'rendering 25 notes' in r and '25/25 notes ready' in r
    assert any('all 25 notes' in n for n in eng.drain_news())
    assert np.max(np.abs(eng._pull(BLOCK))) == 0              # nothing queued, nothing pressed: silence
    key('ismail keys', 0x90, 60, 100)
    assert np.max(np.abs(eng._pull(BLOCK))) > 0.01             # the very next block
    key('ismail keys', 0x80, 60, 0)
    pulls(eng, 0.6)
    assert eng.midi.voices == [] and np.max(np.abs(eng._pull(BLOCK))) == 0
    assert np.max(np.abs(eng.air)) > 0.01                      # live_listen hears the keys
    assert 'ismail keys: plays preset:pluck | 25/25 notes ready | 1 played' in eng.cmd_status()


def test_velocity_transpose_channel_and_the_sustain_pedal(eng):
    eng.cmd_midi(source='ismail keys', instrument='preset:pluck')
    key('ismail keys', 0x90, 60, 30)
    soft = np.max(np.abs(eng._pull(BLOCK)))
    eng.midi.voices = []
    key('ismail keys', 0x90, 60, 127)
    assert np.max(np.abs(eng._pull(BLOCK))) > 3 * soft
    eng.midi.voices = []
    eng.cmd_midi(source='keys', channel=2, transpose=12)
    key('ismail keys', 0x90, 60, 100)                          # channel 1: ignored
    assert eng.midi.voices == []
    key('ismail keys', 0x91, 60, 100)
    assert eng.midi.voices[0].y is eng.midi.patches['ismail keys']['bank'][72]
    key('ismail keys', 0xB1, 64, 127)                          # pedal down: the note-off waits for the pedal
    key('ismail keys', 0x81, 60, 0)
    pulls(eng, 0.5)
    assert len(eng.midi.voices) == 1 and eng.midi.voices[0].rel is None
    key('ismail keys', 0xB1, 64, 0)
    assert eng.midi.voices[0].rel is not None
    key('ismail keys', 0x91, 100, 100)                         # out of the rendered range: counted, silent
    assert eng.midi.patches['ismail keys']['missed'] == 1


def test_swapping_to_a_tracks_sound_and_a_drum_plays_on_every_key(eng):
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    eng.cmd_track('k', instrument={'type': 'kick'})
    r = eng.cmd_midi(source='keys', track='k')
    assert "rendering 1 notes of track k's sound" in r
    key('ismail keys', 0x90, 40, 100)
    key('ismail keys', 0x90, 81, 100)
    assert len(eng.midi.voices) == 2 and eng.midi.voices[0].y is eng.midi.voices[1].y
    key('ismail keys', 0x80, 40, 0)                            # a hit rings out: note-offs do nothing
    assert all(v.rel is None for v in eng.midi.voices)
    with pytest.raises(LiveError, match="no live track 'nope'"):
        eng.cmd_midi(source='keys', track='nope')


def test_what_it_says_when_asked_wrong(eng):
    assert "MIDI inputs: 'flmcp Request', 'ismail keys'" in eng.cmd_midi()
    assert 'nothing patched' in eng.cmd_midi()
    with pytest.raises(LiveError, match="no such input; inputs: \\['flmcp Request', 'ismail keys'\\]"):
        eng.cmd_midi(source='piano', instrument='preset:pluck')
    with pytest.raises(LiveError, match='is not patched yet: say what it plays'):
        eng.cmd_midi(source='keys')
    with pytest.raises(LiveError, match='volume_db <= \\+6'):
        eng.cmd_midi(source='keys', instrument='preset:pluck', volume_db=12)
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    assert 'unpatched' in eng.cmd_midi(source='keys', remove=True) and 'ismail keys' not in FakeInput.opened


def test_inputs_plugged_in_and_out_are_noticed_and_a_patched_one_comes_back(eng):
    eng.midi.poll()
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    eng.drain_news()
    eng.inputs.append('Digital Piano')
    eng.midi.poll()
    assert any("MIDI input plugged in: 'Digital Piano'. live_midi(source='Digital Piano'" in n for n in eng.drain_news())
    key('ismail keys', 0x90, 60, 100)
    eng.inputs.remove('ismail keys')
    eng.midi.poll()
    assert any('keys ismail keys: unplugged' in n for n in eng.drain_news())
    assert 'ismail keys' not in FakeInput.opened and eng.midi.voices[0].rel is not None
    assert '(UNPLUGGED)' in eng.cmd_status()
    eng.inputs.append('ismail keys')
    eng.midi.poll()
    assert any('plugged back in' in n for n in eng.drain_news()) and 'ismail keys' in FakeInput.opened


def test_every_key_is_logged_with_the_beat_heard(eng, tmp_path):
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    pulls(eng, 2.0)
    key('ismail keys', 0x90, 64, 90)
    key('ismail keys', 0x80, 64, 0)
    eng.midi.flush()
    recs = [json.loads(x) for x in open(os.path.join(str(tmp_path), 'live', 'midi', 'ismail_keys.jsonl'))]
    assert [(r['st'], r['d1'], r['d2']) for r in recs] == [(0x90, 64, 90), (0x80, 64, 0)]
    assert 3.0 < recs[0]['beat'] < 4.0             # 2 s pulled at 120 BPM, less the path's lookahead


def test_loud_keys_on_a_loud_set_are_soft_clipped_not_wrapped(eng):
    eng.cmd_midi(source='keys', instrument='preset:pluck', volume_db=6)
    for m in range(48, 72):
        key('ismail keys', 0x90, m, 127)
    out = np.full((BLOCK, 2), 0.8, dtype=np.float32)
    eng.midi.mix(out)
    assert np.max(np.abs(out)) <= 1.0
    assert 'soft-clipped' in eng.cmd_midi()


def test_what_was_played_reads_back_as_notes_text(eng):
    assert 'no keys played yet' in eng.cmd_played()
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    pulls(eng, 2.0)
    key('ismail keys', 0x90, 60, 90)
    key('ismail keys', 0xB0, 64, 127)          # pedal down: C4's release waits for it
    key('ismail keys', 0x80, 60, 0)
    pulls(eng, 0.5)
    key('ismail keys', 0x90, 64, 70)
    key('ismail keys', 0x80, 64, 0)
    pulls(eng, 0.5)
    key('ismail keys', 0xB0, 64, 0)
    key('ismail keys', 0x90, 67, 100)          # still held when read
    r = eng.cmd_played(source='keys')
    lines = r.splitlines()
    assert lines[0].startswith('ismail_keys: 3 of 3 notes, bars 1-2 (1 still held)'), r
    c4 = next(x for x in lines if 'C4' in x)
    assert 'bar   1' in c4 and 'd=1.95' in c4 and 'v=90' in c4  # two pulls of whole blocks at 120 BPM: the pedal's hold
    assert any('E4' in x and 'v=70' in x for x in lines) and any('G4' in x for x in lines)
