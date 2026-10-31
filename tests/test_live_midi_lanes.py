"""Played expression (MIDI parity step 6): pitch bend, channel and poly pressure, the mod wheel and MPE's per-note
bend drive the playing note's lanes (bend, vib, level) when the patch's sound has them or the agent maps them; a
message bound with live_map stays a control. All silent: device='none', no render workers, a stand-in for the MIDI
driver fed synthetic messages."""
import json
import os

import numpy as np
import pytest

from ismail import midilanes as ML
from ismail.dsp import SR
from ismail.live import midi_play as MP
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
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 57)              # an octave renders fast
    monkeypatch.setattr(MP, 'HI', 69)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.inputs = ['keys in']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    FakeInput.opened.clear()
    return e


@pytest.fixture
def lanes_voice(monkeypatch):
    """The patch's sound counts as a performer voice with bend, vib and level lanes (rendering a real one here
    would take a minute; test_a_real_performer_names_its_lanes checks the lookup)."""
    monkeypatch.setattr(MP.ML, 'voice_lanes', lambda inst, root=None: {'bend', 'vib', 'level', 'mute'})


def msg(st, d1, d2):
    FakeInput.opened['keys in'].on(0.0, st, d1, d2)


def bend(semis, rng=2.0, ch=1):
    raw = ML.to_raw('bend', -rng, rng, semis)[0]
    msg(0xE0 | (ch - 1), raw & 0x7F, raw >> 7)


def hz(y):
    """The pitch of a stretch of audio, from its rising zero crossings."""
    s = y[:, 0] if y.ndim == 2 else y
    z = np.flatnonzero((s[:-1] <= 0) & (s[1:] > 0))
    return (len(z) - 1) / ((z[-1] - z[0]) / SR)


def play(e, seconds):
    out = []
    for _ in range(int(seconds * SR / BLOCK)):
        out.append(e._pull(BLOCK).copy())
    return np.concatenate(out)


def test_a_real_performer_names_its_lanes():
    assert {'bend', 'vib', 'level'} <= ML.voice_lanes({'type': 'code', 'voice': 'flute'})
    assert ML.voice_lanes({'type': 'synth', 'oscs': [{'wave': 'saw'}]}) == set()


def test_a_sound_with_no_lanes_leaves_bend_unmapped_and_says_so(eng):
    r = eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    assert 'expression' not in r
    msg(0x90, 60, 100)
    bend(1.0)
    assert eng.midi.xv == {} and "'keys in' bend ch1" in '\n'.join(eng.midi.heard_lines())
    assert "the sound has no expression lanes; lanes={'bend'" in eng.cmd_midi()


def test_bend_moves_the_pitch_and_pressure_the_level(eng, lanes_voice):
    r = eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]},
                     lanes={'bend': {'range': 12}})
    assert 'expression bend <- bend -12..12, vib <- cc:1 0..50, level <- pressure 0..6' in r
    msg(0x90, 60, 100)
    flat = play(eng, 0.3)
    assert hz(flat[2000:]) == pytest.approx(261.6, rel=0.01)
    bend(12, rng=12)
    play(eng, 0.05)                                    # the ramp: one buffer
    up = play(eng, 0.3)
    assert hz(up) == pytest.approx(523.3, rel=0.01)
    bend(0, rng=12)
    msg(0x90, 62, 100)
    play(eng, 0.05)
    quiet = np.sqrt(np.mean(play(eng, 0.2) ** 2))
    msg(0xD0, 127, 0)                                  # full pressure: +6 dB on every key of the channel
    play(eng, 0.05)
    loud = np.sqrt(np.mean(play(eng, 0.2) ** 2))
    assert 20 * np.log10(loud / quiet) == pytest.approx(6.0, abs=1.0)
    log = [json.loads(x) for x in open(os.path.join(eng.root, 'live', 'midi', 'keys_in.jsonl'))] \
        if eng.midi.flush() is None else []
    assert {r['st'] for r in log} >= {0x90, 0xE0, 0xD0}          # expression is in the key log too


def test_the_mod_wheel_is_vibrato_and_neutral_expression_is_the_plain_note(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    msg(0x90, 60, 100)
    v = eng.midi.voices[0]
    plain = v.y[:, :BLOCK * 4].T * v.g
    got = play(eng, BLOCK * 4 / SR)
    assert np.array_equal(got, plain)                    # bend 0, no wheel, no pressure: the same samples
    msg(0xB0, 1, 127)                                   # mod wheel up: 50 cents of vibrato at 5.5 Hz
    play(eng, 0.05)
    x = eng.midi.xv[v]
    rates = []
    for _ in range(40):
        p0 = x.pos
        eng._pull(BLOCK)
        rates.append((x.pos - p0) / BLOCK)
    cents = 1200 * np.log2(np.array(rates))
    assert cents.max() > 25 and cents.min() < -25 and abs(cents.mean()) < 10


def test_bend_is_per_channel_and_mpe_gives_each_note_its_own(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    msg(0x90, 60, 100)                                  # channel 1
    msg(0x91, 64, 100)                                  # channel 2
    a, b = eng.midi.voices
    bend(2, ch=2)
    play(eng, 0.05)
    assert eng.midi.xv[a].rate == 1.0 and eng.midi.xv[b].rate == pytest.approx(2 ** (2 / 12), abs=1e-3)
    msg(0xB1, 121, 0)                                   # reset all controllers on channel 2
    play(eng, 0.05)
    assert eng.midi.xv[b].rate == 1.0

    eng.midi.voices, eng.midi.xv = [], {}
    r = eng.cmd_midi(source='keys', mpe=True)
    assert 'bend <- bend -48..48' in r and 'MPE: channel 1 the master' in r
    bend(12, rng=48, ch=2)                              # the note's own bend comes before its note-on
    msg(0x91, 60, 100)
    msg(0x92, 64, 100)
    c, d = eng.midi.voices
    assert eng.midi.xv[c].rate == pytest.approx(2.0, abs=1e-3)   # no glide into it
    play(eng, 0.05)
    assert eng.midi.xv[d].rate == 1.0
    bend(2, rng=2, ch=1)                                # the master's +-2 adds to every note
    msg(0xD2, 127, 0)                                   # note d's own pressure
    play(eng, 0.05)
    assert eng.midi.xv[c].rate == pytest.approx(2 ** (14 / 12), abs=1e-3)
    assert eng.midi.xv[d].rate == pytest.approx(2 ** (2 / 12), abs=1e-3)
    assert eng.midi.xv[d].g == pytest.approx(2.0, abs=0.01) and eng.midi.xv[c].g == 1.0


def test_poly_pressure_is_one_keys_own(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    msg(0x90, 60, 100)
    msg(0x90, 64, 100)
    a, b = eng.midi.voices
    msg(0xA0, 64, 127)
    play(eng, 0.05)
    assert eng.midi.xv[b].g == pytest.approx(2.0, abs=0.01) and eng.midi.xv[a].g == 1.0


def test_a_message_bound_by_live_map_stays_a_control(eng, lanes_voice):
    eng.cmd_track('pad', instrument={'type': 'kick'})
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    eng.cmd_map('wheel', target='pad', param='pan', range=[-1, 1], midi='bend', source='keys')
    msg(0x90, 60, 100)
    bend(2)
    play(eng, 0.05)
    assert eng.tracks['pad']['pan'] == pytest.approx(1.0)
    assert eng.midi.xv[eng.midi.voices[0]].rate == 1.0
    msg(0xD0, 127, 0)                                   # pressure is not mapped: it is the level lane
    play(eng, 0.05)
    assert eng.midi.xv[eng.midi.voices[0]].g == pytest.approx(2.0, abs=0.01)


def test_lanes_say_what_to_do_when_asked_wrong(eng, lanes_voice):
    with pytest.raises(LiveError, match='pitch or gain'):
        eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]},
                     lanes={'mute': {'midi': 'cc:20', 'range': [0, 1]}})
    assert 'keys in' not in eng.midi.patches            # a refused patch is not half made
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    with pytest.raises(LiveError, match='leave channel out'):
        eng.cmd_midi(source='keys', mpe=True, channel=2)
    with pytest.raises(LiveError, match=r'\[-r, r\]'):
        eng.cmd_midi(source='keys', lanes={'bend': {'range': [0, 2]}})
    r = eng.cmd_midi(source='keys', lanes={'vib': None, 'level': None})
    assert 'expression bend <- bend -2..2' in r and 'vib <-' not in r
    msg(0x90, 60, 100)
    msg(0xB0, 1, 127)                                   # the wheel is nobody's now: heard, listed
    assert "'keys in' cc:1 ch1" in '\n'.join(eng.midi.heard_lines())


def test_a_key_has_its_expression_from_its_first_buffer_and_an_mpe_tail_keeps_its_own(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]}, mpe=True)
    bend(12, rng=48, ch=2)
    p = eng.midi.patches['keys in']
    eng.midi.note_on(p, 60, 100, 2)                    # as any caller of note_on would: no step after it
    v = eng.midi.voices[0]
    assert v in eng.midi.xv                            # attached inside note_on, before a buffer can mix it
    eng._pull(BLOCK)
    assert eng.midi.xv[v].pos == pytest.approx(BLOCK * 2.0, rel=1e-3)   # the first buffer is already an octave up
    msg(0x81, 60, 0)                                   # let go: the tail rings on channel 2's values of now
    bend(0, rng=48, ch=2)                              # the controller hands channel 2 to the next note
    msg(0x91, 64, 100)
    eng._pull(BLOCK)
    w = eng.midi.voices[1]
    assert eng.midi.xv[v].rate == pytest.approx(2.0, abs=1e-3) and eng.midi.xv[w].rate == 1.0
    bend(2, rng=2, ch=1)                               # the master still moves the tail
    eng._pull(BLOCK)
    assert eng.midi.xv[v].rate == pytest.approx(2 ** (14 / 12), abs=1e-3)


def test_mpe_two_fingers_on_one_pitch_are_two_keys(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]}, mpe=True)
    msg(0x91, 60, 100)
    msg(0x92, 60, 100)
    a, b = eng.midi.voices
    p = eng.midi.patches['keys in']
    assert p['held'] == {(2, 60): a, (3, 60): b} and a.rel is None
    bend(12, rng=48, ch=3)
    msg(0x81, 60, 0)                                   # the first finger lifts: only its own key ends
    assert a.rel is not None and b.rel is None and (3, 60) in p['held']
    assert eng.midi.xv[b].chan is p['xs'][3]           # still live, not frozen
    bend(0, rng=48, ch=3)
    assert eng.midi.xv[b].val('bend') == 0.0
    msg(0xA2, 60, 127)                                 # poly pressure finds the key by channel
    assert eng.midi.xv[b].poly == {'level': pytest.approx(6.0)} and eng.midi.xv[a].poly == {}


def test_reset_all_controllers_clears_a_keys_poly_pressure_too(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]})
    msg(0x90, 60, 100)
    v = eng.midi.voices[0]
    msg(0xA0, 60, 127)
    msg(0xD0, 127, 0)
    assert eng.midi.xv[v].val('level') == pytest.approx(12.0)
    msg(0xB0, 121, 0)
    assert eng.midi.xv[v].val('level') == 0.0 and eng.midi.xv[v].poly == {}


def test_an_mpe_channel_reused_drops_the_old_notes_pressure_but_keeps_a_fresh_bend(eng, lanes_voice):
    eng.cmd_midi(source='keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]}, mpe=True)
    msg(0x92, 60, 100)
    msg(0xD2, 127, 0)                                  # full pressure on channel 3
    msg(0xB2, 1, 127)                                  # and the mod wheel
    msg(0x82, 60, 0)
    bend(12, rng=48, ch=3)                             # the next note's bend, just before its note-on
    msg(0x92, 64, 100)
    w = eng.midi.voices[1]
    x = eng.midi.xv[w]
    assert x.val('level') == 0.0 and x.val('vib') == 0.0 and x.g == 1.0
    assert x.val('bend') == pytest.approx(12.0, abs=0.01)
    msg(0x82, 64, 0)
    msg(0xD2, 64, 0)                                   # a pressure sent before the next note-on is that note's
    msg(0x92, 67, 100)
    assert eng.midi.xv[eng.midi.voices[2]].val('level') == pytest.approx(6 * 64 / 127)
