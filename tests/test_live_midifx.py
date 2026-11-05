"""live_midi_fx (MIDI parity step 7b): a chain of MIDI effects on a patched input's keys, through the same core as
track_midi_fx. A key plays the chain's notes, its release ends exactly the notes it started (also after the chain
changed), a change waits for its bar line, a set length ends notes on the beat's sample, the chain feeds live_arp
(the chain runs first), and the live notes equal the studio's. All silent: device='none', no render workers, a
stand-in for the MIDI driver fed synthetic messages."""
import numpy as np
import pytest

from ismail import midifx as MF
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live import midi_play as MP
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.midi_play import MidiPlay

CHORD = [{'type': 'chord', 'intervals': [0, 4, 7]}]


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
    e.inputs = ['test keys']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    FakeInput.opened.clear()
    e.cmd_midi(source='test keys', instrument='preset:pluck')
    return e


def key(st, d1, d2):
    FakeInput.opened['test keys'].on(0.0, st, d1, d2)


def pulls(e, seconds):
    out = []
    for _ in range(int(seconds * SR / BLOCK)):
        e._fifo.append(e.mix_block().T.copy())
        e._fifo_n += BLOCK
        out.append(e._pull(BLOCK))
    return np.concatenate(out) if out else np.zeros((0, 2))


def recent(e):
    """The keys through the chain lately: (beat, key, velocity, [(pitch, velocity)] it started)."""
    return list(e.mfx.fx['test keys']['recent'])


def test_a_key_plays_the_chain_and_its_release_ends_exactly_those_notes(eng):
    r = eng.cmd_midi_fx(source='test', chain=CHORD, at='now')
    assert 'midi fx test keys: on now: chord +0 +4 +7' in r and 'keys pressed now play through it' in r
    key(0x90, 60, 100)
    assert len(eng.midi.voices) == 3
    first = list(eng.midi.voices)
    assert recent(eng)[-1][3] == [(60, 100), (64, 100), (67, 100)]
    eng.cmd_midi_fx(source='test keys', chain=[{'type': 'pitch', 'transpose': -12}], at='now')
    key(0x90, 62, 90)                                               # the new chain: one note, an octave down
    assert recent(eng)[-1][3] == [(50, 90)] and len(eng.midi.voices) == 4
    key(0x80, 60, 0)                                                # the old key ends its three, not the new one
    assert all(v.rel is not None for v in first) and eng.midi.voices[3].rel is None
    s = eng.cmd_status()
    assert 'midi fx test keys: pitch -12 | 2 keys through it | 1 keys held' in s
    key(0x80, 62, 0)
    assert eng.midi.voices[3].rel is not None and eng.midi.patches['test keys']['fxon'] == {}


def test_a_change_waits_for_its_bar_line_and_off_lands_on_one(eng):
    eng.cmd_midi_fx(source='test keys', chain=CHORD, at='now')
    pulls(eng, 0.5)
    r = eng.cmd_midi_fx(source='test keys', chain=[{'type': 'chord', 'intervals': [0, 7]}], at='next_bar')
    assert 'changed at bar 2' in r and 'at bar 2: chord +0 +7' in eng.cmd_status()
    key(0x90, 60, 100)
    key(0x80, 60, 0)
    assert len(recent(eng)[-1][3]) == 3                             # before the bar line: the old chain
    pulls(eng, 2.2)                                                 # past bar 2 (4 beats at 120 BPM)
    key(0x90, 60, 100)
    key(0x80, 60, 0)
    assert [m for m, _ in recent(eng)[-1][3]] == [60, 67]
    assert 'off at bar' in eng.cmd_midi_fx(source='test keys', off=True, at='next_bar')
    pulls(eng, 2.2)
    n = len(eng.midi.voices)
    key(0x90, 64, 100)                                              # after it: a key as pressed
    assert len(eng.midi.voices) == n + 1 and 'test keys' not in eng.mfx.fx
    assert 'no MIDI effects on' in eng.cmd_midi_fx()


def test_a_set_length_ends_the_notes_on_the_beats_sample(eng):
    eng.cmd_midi_fx(source='test keys', chain=[{'type': 'length', 'beats': 0.5}], at='now')
    pulls(eng, 0.3)
    key(0x90, 60, 100)
    beat = recent(eng)[-1][0]
    v = eng.midi.voices[-1]
    played0 = eng.played
    key(0x80, 60, 0)                                                # let go at once: the note still lasts
    assert v.rel is None
    y = pulls(eng, 0.6)
    lag = eng.safety.la + G.LAT_BUDGET
    end = eng.sample(beat + 0.5) + lag - played0                    # where beat + 0.5's music plays
    rel = int(eng.midi.patches['test keys']['rel_s'] * SR)
    assert v.rel == rel and 0 < end < y.shape[0] - rel
    a = np.abs(y[:, 0])
    assert a[end - 400:end].max() > 1e-4                            # sounding up to the beat
    assert a[end + rel + 10:end + rel + 2000].max() < 1e-6          # faded out over the release after it
    mid = a[end + rel // 4:end + rel // 4 + 40].max()
    assert 0 < mid < a[end - 40:end].max()                          # and fading inside it


def test_a_length_scale_above_one_delays_the_release(eng):
    r = eng.cmd_midi_fx(source='test keys', chain=[{'type': 'length', 'scale': 0.5}], at='now')
    assert 'cannot end before its key is let go' in r
    eng.cmd_midi_fx(source='test keys', chain=[{'type': 'length', 'scale': 3}], at='now')
    key(0x90, 60, 100)
    v = eng.midi.voices[-1]
    pulls(eng, 0.1)
    key(0x80, 60, 0)
    assert v.rel is None and eng.midi.fx_offs                        # held 1 part, ends after 3
    pulls(eng, 0.1)
    assert v.rel is None
    pulls(eng, 0.25)
    assert v.rel is not None and not eng.midi.fx_offs


def test_the_chain_runs_before_the_arp_and_a_shared_pitch_is_held_until_both_let_go(eng):
    eng.cmd_arp(source='test keys', at='now', pattern='up', rate=0.5)
    r = eng.cmd_midi_fx(source='test keys', chain=[{'type': 'chord', 'intervals': [0, 7]}], at='now')
    assert 'the chain runs first' in r
    a, p = eng.arp.arps['test keys'], eng.midi.patches['test keys']
    key(0x90, 60, 100)                                              # C G
    key(0x90, 67, 100)                                              # G D: G twice
    assert sorted(m for m, _ in eng.arp._held(a, p)) == [60, 67, 74] and eng.midi.voices == []
    key(0x80, 60, 0)
    assert sorted(m for m, _ in eng.arp._held(a, p)) == [67, 74]    # G stays: the other key still holds it
    pulls(eng, 1.5)
    assert {s[1][0] for s in a['recent']} <= {60, 67, 74} and 74 in {s[1][0] for s in a['recent']}
    key(0x80, 67, 0)
    assert eng.arp._held(a, p) == [] and a['fxn'] == {}


def test_a_set_length_on_an_arped_input_lets_go_on_its_beat(eng):
    eng.cmd_arp(source='test keys', at='now', pattern='up', rate=0.25)
    eng.cmd_midi_fx(source='test keys', chain=[{'type': 'length', 'beats': 1}], at='now')
    pulls(eng, 0.3)
    key(0x90, 60, 100)
    start = recent(eng)[-1][0]
    pulls(eng, 1.5)
    st = [s[0] for s in eng.arp.arps['test keys']['recent']]
    assert st and max(st) < start + 1 - 1e-9 and max(st) > start + 0.5   # steps until the length, none after
    assert eng.arp.arps['test keys']['keyq'] == []


def test_live_and_the_studio_give_the_same_notes(eng):
    chain = [{'type': 'chord', 'intervals': [0, 3, 7], 'velocity': 0.7},
             {'type': 'random', 'chance': 0.5, 'choices': 2, 'interval': 12, 'sign': 'both', 'seed': 11},
             {'type': 'scale', 'key': 'D', 'scale': 'dorian'}, {'type': 'velocity', 'out': [30, 120], 'random': 12},
             {'type': 'pitch', 'lowest': 'C3', 'highest': 'C6', 'fold': True}]
    eng.cmd_midi_fx(source='test keys', chain=chain, at='now')
    for m, vel in ((62, 100), (65, 80), (69, 60), (60, 110), (64, 90), (57, 70)):
        key(0x90, m, vel)
        pulls(eng, 0.13)
        key(0x80, m, 0)
    live = recent(eng)
    assert len(live) == 6
    tr = {'notes': [[b, m, 0.25, v] for b, m, v, _ in live], 'midi_fx': MF.configure(chain)}
    studio = MF.expand(tr)['notes']
    for b, m, v, outs in live:
        assert sorted(outs) == sorted((n[1], n[3]) for n in studio if n[0] == b)
    assert len({tuple(o) for *_, o in live}) == 6


def test_what_it_says_when_asked_wrong(eng):
    with pytest.raises(LiveError, match="is not patched, and MIDI effects work on a patched input's keys"):
        eng.cmd_midi_fx(source='other', chain=CHORD)
    with pytest.raises(LiveError, match="effect 1 \\(chord\\): intervals"):
        eng.cmd_midi_fx(source='test keys', chain=[{'type': 'chord'}])
    with pytest.raises(LiveError, match="no MIDI effects on 'test keys'"):
        eng.cmd_midi_fx(source='test keys', off=True)
    with pytest.raises(LiveError, match="source: which patched input"):
        eng.cmd_midi_fx(chain=CHORD)
    with pytest.raises(LiveError, match="at="):
        eng.cmd_midi_fx(source='test keys', chain=CHORD, at='bar:0.5')
    assert "no MIDI effects on 'test keys'" in eng.cmd_midi_fx(source='test keys')


def test_all_notes_off_ends_the_chains_notes_and_its_waiting_lengths(eng):
    eng.cmd_midi_fx(source='test keys', chain=CHORD + [{'type': 'length', 'beats': 8}], at='now')
    key(0x90, 60, 100)
    assert len(eng.midi.fx_offs) == 1
    key(0xB0, 123, 0)                                               # all notes off
    assert all(v.rel is not None for v in eng.midi.voices) and eng.midi.fx_offs == []


# ------------------------------------------------------------------ review fixes (adversarial pass, 11-05)
def test_the_voice_cap_holds_for_chain_voices_and_plain_keys(eng, monkeypatch):
    monkeypatch.setattr(MP, 'MAX_VOICES', 16)
    monkeypatch.setattr(MP, 'FADE_ROOM', 4)
    eng.cmd_midi_fx(source='test keys', chain=[{'type': 'chord', 'intervals': list(range(12))}], at='now')
    for m in range(48, 60):                                         # 12 keys x 12 notes
        key(0x90, m, 100)
        assert len(eng.midi.voices) <= 16
    assert sum(1 for v in eng.midi.voices if v.rel is None) <= 12
    eng.cmd_midi_fx(source='test keys', off=True, at='now')
    key(0xB0, 123, 0)
    for m in range(48, 73):                                         # plain keys, never let go
        key(0x90, m, 100)
        assert len(eng.midi.voices) <= 16
    assert sum(1 for v in eng.midi.voices if v.rel is None) <= 12


@pytest.mark.parametrize('chain', [[{'type': 'length', 'beats': 4}], [{'type': 'length', 'scale': 4}],
                                   [{'type': 'chord', 'intervals': [0]}]])
def test_an_mpe_note_let_go_keeps_its_own_bend_whatever_ends_it(tmp_path, monkeypatch, chain):
    from ismail import midilanes as ML
    monkeypatch.setattr(MP, 'LO', 57)
    monkeypatch.setattr(MP, 'HI', 69)
    monkeypatch.setattr(MP.ML, 'voice_lanes', lambda inst, root=None: {'bend', 'vib', 'level'})
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.inputs = ['test keys']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    e.cmd_midi(source='test keys', instrument={'type': 'synth', 'oscs': [{'wave': 'sine'}]}, mpe=True)
    e.cmd_midi_fx(source='test keys', chain=chain, at='now')

    def bend(semis):
        raw = ML.to_raw('bend', -48, 48, semis)[0]
        key(0xE1, raw & 0x7F, raw >> 7)                             # channel 2
    bend(12)
    key(0x91, 60, 100)
    v = e.midi.voices[0]
    pulls(e, 0.05)
    key(0x81, 60, 0)                                                # let go: its tail keeps channel 2's bend of now
    assert bool(e.midi.fx_offs) == (chain[0]['type'] == 'length')   # a set or a later end, or now
    bend(0)                                                         # the controller hands channel 2 to the next note
    key(0x91, 64, 100)
    pulls(e, 0.05)
    assert v.rel is None or chain[0]['type'] == 'chord'
    assert e.midi.xv[v].rate == pytest.approx(2.0, abs=1e-3)   # an octave up still, not the next note's bend


def test_a_stored_chain_given_back_is_taken_as_it_is(eng, tmp_path):
    import json
    import os
    from ismail import api
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=2, master_limiter=False)
    api.track_add(p, 'lead', instrument={'type': 'synth'})
    api.notes_write(p, 'lead', 1, '0 C4 1')
    api.track_midi_fx(p, 'lead', chain=[{'type': 'velocity', 'fixed': 100}, {'type': 'length', 'beats': 0.5},
                                        {'type': 'length', 'scale': 2}, {'type': 'pitch', 'transpose': 3},
                                        {'type': 'random', 'chance': 0.5}, {'type': 'chord', 'intervals': [0, 7]},
                                        {'type': 'scale', 'key': 'A', 'scale': 'minor'}])
    with open(os.path.join(p, 'project.json'), encoding='utf8') as f:
        stored = json.load(f)['tracks']['lead']['midi_fx']
    assert 'MIDI effects unchanged' in api.track_midi_fx(p, 'lead', chain=stored)
    assert 'on now' in eng.cmd_midi_fx(source='test keys', chain=stored, at='now')
    assert eng.mfx.fx['test keys']['chain'] == stored


def test_status_says_off_when_the_first_queued_change_is_an_off(eng):
    pulls(eng, 0.3)
    eng.cmd_midi_fx(source='test keys', chain=CHORD, at='bar:3')
    eng.cmd_midi_fx(source='test keys', off=True, at='bar:2')
    s = eng.cmd_status()
    assert 'midi fx test keys: off at bar 2' in s and 'at bar 3: chord +0 +4 +7' in s
