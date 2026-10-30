"""live_arp (MIDI parity step 7): an arpeggiator on a patched input's keys, on the engine's beat clock, through the
same core as track_arp. Held keys drive the steps (they no longer sound as keys), each step lands sample exact on
the beat heard, a change waits for its bar line and changes only what was given, a step list plays with no keys,
per-step pan and cutoff shape each step, latch and the pedal hold keys, and the live steps equal the studio's.
All silent: device='none', no render workers, a stand-in for the MIDI driver fed synthetic messages."""
import numpy as np
import pytest

from ismail import arp as A
from ismail.dsp import SR
from ismail.live import graph as G
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
    return np.concatenate(out)


def recent(e):
    return list(e.arp.arps['test keys']['recent'])


def test_held_keys_drive_steps_on_the_beat_heard_not_as_keys(eng):
    r = eng.cmd_arp(source='test', at='now', rate=0.5, gate=0.5)
    assert 'arp test keys: on now: pattern up, rate 0.5' in r and 'keys pressed now drive it' in r
    for m in (60, 64, 67):
        key(0x90, m, 100)
    assert eng.midi.voices == []                                   # the keys do not sound as keys
    y = pulls(eng, 3.0)
    st = recent(eng)
    assert [n for _, n in st[:4]] == [[60], [64], [67], [60]]
    assert all(abs((b1 - b0) - 0.5) < 1e-9 for (b0, _), (b1, _) in zip(st, st[1:]))
    assert all(abs(b * 2 - round(b * 2)) < 1e-9 for b, _ in st)    # on the grid
    lag = eng.safety.la + G.LAT_BUDGET
    first = int(np.argmax(np.abs(y[:, 0]) > 1e-6))
    p = eng.midi.patches['test keys']
    attack = int(np.argmax(np.abs(p['bank'][60][0]) * 10 ** (p['vol'] / 20) > 1e-6))   # the note's own onset
    assert abs(first - (eng.sample(st[0][0]) + lag + attack)) <= 2  # where the device plays that beat's music
    s = eng.cmd_status()
    assert 'arp test keys: on | pattern up, rate 0.5' in s and 'holding C4 E4 G4' in s and 'steps played' in s
    for m in (60, 64, 67):
        key(0x80, m, 0)
    n = len(recent(eng))
    pulls(eng, 1.0)
    assert len(recent(eng)) == n                                   # let go: no more steps


def test_a_change_waits_for_its_bar_line_and_changes_only_what_was_given(eng):
    eng.cmd_arp(source='test keys', at='now', rate=0.25, cutoff=[400, 4000])
    key(0x90, 60, 100)
    key(0x90, 67, 100)
    pulls(eng, 0.5)
    assert 'rate changed at bar' in eng.cmd_arp(source='test keys', rate=0.5, at='next_bar')
    a = eng.arp.arps['test keys']
    when = a['pending'][0][0]
    assert when % 4 == 0 and 'changes queued at bar' in eng.cmd_status()
    pulls(eng, 4.5)
    st = recent(eng)
    before = [b for b, _ in st if b < when]
    after = [b for b, _ in st if b >= when]
    assert before[-1] - before[-2] == pytest.approx(0.25) and after[1] - after[0] == pytest.approx(0.5)
    assert a['cfg']['cutoff'] == [400, 4000] and a['cfg']['pattern'] == 'up'
    assert 'nothing changed' in eng.cmd_arp(source='test keys', rate=0.5, at='now')


def test_a_step_list_plays_with_no_keys_switches_on_bar_lines_and_moves_key(eng):
    eng.cmd_arp(source='test keys', at='now', notes='A3 C4 E4 A4 | F3 A3 C4 F4', key='A', scale='minor', rate=1)
    pulls(eng, 5.0)
    st = recent(eng)
    by_bar = {}
    for b, n in st:
        by_bar.setdefault(int(b // 4), []).append(n[0])
    full = [v for v in by_bar.values() if len(v) == 4]
    assert [57, 60, 64, 69] in full and [53, 57, 60, 65] in full
    eng.cmd_arp(source='test keys', key='C', scale='major', at='now')
    pulls(eng, 8.0)
    later = [n[0] for _, n in recent(eng)[-8:]]                     # two bars: i -> I (C E G C), VI -> vi (A C E A)
    assert set(later) <= {57, 60, 64, 67, 69, 72} and 67 in later and 57 in later


def test_per_step_pan_and_cutoff_shape_each_step(eng):
    eng.cmd_arp(source='test keys', at='now', rate=1, gate=0.9, pan=[-1, 1], cutoff=[300, 12000])
    key(0x90, 60, 100)
    pulls(eng, 0.3)
    y = pulls(eng, 4.0)
    vs = eng.arp.voices
    assert vs                                                     # some sounding now
    lag = eng.safety.la + G.LAT_BUDGET
    st = recent(eng)
    played0 = eng.played - y.shape[0]
    segs = []
    for b, _ in st:
        s = eng.sample(b) + lag - played0
        if 0 <= s and s + SR // 4 < y.shape[0]:
            segs.append(y[s:s + SR // 4])
    assert len(segs) >= 2
    lefts = [float(np.sqrt(np.mean(g[:, 0] ** 2))) for g in segs]
    rights = [float(np.sqrt(np.mean(g[:, 1] ** 2))) for g in segs]
    hard = [(l > 10 * r) or (r > 10 * l) for l, r in zip(lefts, rights)]
    assert all(hard) and (lefts[0] > rights[0]) != (lefts[1] > rights[1])

    def hf(g):
        sp = np.abs(np.fft.rfft(g.sum(1)))
        f = np.fft.rfftfreq(g.shape[0], 1 / SR)
        return sp[f > 2000].sum() / (sp.sum() + 1e-12)
    dark, bright = sorted([hf(segs[0]), hf(segs[1])])
    assert bright > 3 * dark                                      # the 300 Hz step is dark, the 12 kHz one is not


def test_latch_the_pedal_and_off(eng):
    eng.cmd_arp(source='test keys', at='now', latch=True, rate=0.5)
    key(0x90, 60, 100)
    key(0x90, 64, 100)
    key(0x80, 60, 0)
    key(0x80, 64, 0)
    pulls(eng, 1.5)
    assert {n[0] for _, n in recent(eng)} == {60, 64}               # still playing after release
    key(0x90, 67, 100)                                              # a new chord replaces the latched one
    key(0x80, 67, 0)
    n0 = len(recent(eng))
    pulls(eng, 1.5)
    assert {n[0] for _, n in recent(eng)[n0 + 1:]} == {67}
    eng.cmd_arp(source='test keys', latch=False, at='now')
    key(0xB0, 64, 127)                                              # the pedal holds released keys
    key(0x90, 62, 100)
    key(0x80, 62, 0)
    n1 = len(recent(eng))
    pulls(eng, 1.0)
    assert len(recent(eng)) > n1
    key(0xB0, 64, 0)
    pulls(eng, 0.3)
    n2 = len(recent(eng))
    pulls(eng, 1.0)
    assert len(recent(eng)) == n2
    assert 'off (' in eng.cmd_arp(source='test keys', off=True, at='now')
    key(0x90, 60, 100)
    assert len(eng.midi.voices) == 1                                # a key again
    assert 'no arp on' in eng.cmd_arp()


def test_live_and_the_studio_give_the_same_steps(eng):
    eng.cmd_arp(source='test keys', at='bar:2', pattern='updown', octaves=2, velocity=[90, 60], rate=0.25)
    for m in (57, 60, 64):
        key(0x90, m, 100)
    pulls(eng, 4.5)                                                 # into bar 3
    live = [(b, n) for b, n in recent(eng) if 4 <= b < 8]
    tr = {'notes': [[4.0, 57, 4, 100], [4.0, 60, 4, 100], [4.0, 64, 4, 100]],
          'arp': dict(A.configure(None, {'pattern': 'updown', 'octaves': 2, 'velocity': [90, 60],
                                         'rate': 0.25})[0])}
    st, _ = A.track_steps(tr, dict(A.DEFAULTS, **tr['arp']), 4)
    studio = [(round(b, 6), [m for m, _ in ns]) for b, ns, *_ in st]
    assert live == studio and len(live) == 16


def test_what_it_says_when_asked_wrong(eng):
    with pytest.raises(LiveError, match="is not patched, and the arp plays a patched input's sound: live_midi"):
        eng.cmd_arp(source='other', pattern='up')
    with pytest.raises(LiveError, match="one of up, down"):
        eng.cmd_arp(source='test keys', pattern='sideways')
    with pytest.raises(LiveError, match="degrees are scale steps"):
        eng.cmd_arp(source='test keys', degrees=[0, 2])
    with pytest.raises(LiveError, match="no arp on 'test keys'"):
        eng.cmd_arp(source='test keys', off=True)
    with pytest.raises(LiveError, match="source: which patched input"):
        eng.cmd_arp(pattern='up')
    with pytest.raises(LiveError, match="at="):
        eng.cmd_arp(source='test keys', at='bar:0.5')
