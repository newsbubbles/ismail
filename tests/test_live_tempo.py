"""live_tempo: the tempo of a running set changes at a bar, with no restart. Clips stay on the beat across the change,
the clock stays continuous, beat-synced effects and scheduled moves follow, and played keys keep their sounds. All
silent: device='none', inline renders."""
import json

import numpy as np
import pytest

from ismail.dsp import SR
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live import midi_play as MP
from ismail.live.midi_play import MidiPlay


def run(eng, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        eng.tick()
        eng.mix_block()


def onset_times(eng, thresh=0.02, gap=0.25):
    """Sample indices where the air log crosses up through `thresh` (first sample of each hit)."""
    y = np.abs(eng.air[0, :eng.pos])
    above = y > thresh
    idx = np.nonzero(above[1:] & ~above[:-1])[0] + 1
    keep = []
    for i in idx:
        if not keep or i - keep[-1] > SR * gap:
            keep.append(i)
    return keep


@pytest.fixture
def eng(tmp_path):
    return Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')


def kicks(eng):
    eng.cmd_track('k', instrument={'type': 'kick'})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])


def test_a_looping_clip_keeps_its_notes_on_the_beat_across_a_change(eng):
    kicks(eng)
    run(eng, 2.5)                                   # into bar 2 at 120 BPM
    out = eng.cmd_tempo(160)
    S, B, bpm = eng.tmap.segs[-1]
    assert bpm == 160 and B % 4 == 0 and f"tempo 120 -> 160 BPM at bar {int(B // 4) + 1}" in out
    assert 're-renders from' in out and 'k' in out
    run(eng, 8.0)
    hits = onset_times(eng)
    before = [h for h in hits if h < S - SR // 100]
    after = [h for h in hits if h >= S - SR // 100]
    assert len(before) >= 4 and len(after) >= 8
    assert all(abs(b - a - 0.5 * SR) < 0.001 * SR for a, b in zip(before, before[1:]))      # 120: 0.5 s a beat
    assert abs(after[0] - S) < 0.001 * SR                                                  # the change bar's downbeat
    assert all(abs(b - a - 0.375 * SR) < 0.001 * SR for a, b in zip(after, after[1:]))      # 160: 0.375 s a beat
    for h in after:                                 # and each one on its beat of the new grid
        k = round(eng.beat(h))
        assert abs(h - eng.sample(k)) < 0.001 * SR
    assert eng.bpm == 160 and eng.cmd_status().startswith('live 160 BPM')


def test_the_beat_clock_is_continuous_at_the_change(eng):
    kicks(eng)
    run(eng, 1.0)
    eng.cmd_tempo(90, at='bar:3')
    S, B, _ = eng.tmap.segs[-1]
    assert B == 8.0 and S == 4 * SR                 # bar 3 at 120 BPM: 4 s in
    assert abs(eng.beat(S) - B) < 1e-9 and abs(eng.beat(S - 1) - B) < 1e-4
    xs = np.arange(S - 3 * SR, S + 3 * SR, 997)
    bs = [eng.beat(x) for x in xs]
    assert all(b1 > b0 for b0, b1 in zip(bs, bs[1:]))
    assert all(abs(eng.sample(b) - x) <= 1 for b, x in zip(bs, xs))     # sample() and beat() agree both sides
    assert abs(eng.beat(S + 2 * SR) - (B + 3.0)) < 1e-9                  # 90 BPM: 1.5 beats a second
    assert eng.sample(B + 4) == S + int(round(4 * 60 / 90 * SR))


def test_a_change_queued_at_a_bar_lands_there_and_moves_what_is_scheduled_after_it(eng):
    kicks(eng)
    eng.cmd_track('d', instrument='preset:pluck', fx=[{'type': 'delay', 'time_beats': 0.5, 'mix': 0.3}])
    run(eng, 1.0)
    eng.cmd_fx('d', 'delay', {'mix': 0.6}, ramp_beats=4, at='bar:6')            # a sweep after the change
    out = eng.cmd_tempo(150, at='bar:4')
    assert 'tempo 120 -> 150 BPM at bar 4 (bar:4)' in out and 'delay' in out
    assert 'tempo changes queued: 150 BPM at bar 4' in eng.cmd_status()
    r = eng.ramps[('track:d', 0, 'mix')].r[-1]
    assert r.p0 == eng.sample(20) and r.p1 == eng.sample(24)                    # bar 6 on the new grid
    dl = eng.tracks['d']['path'].chain.procs[0]
    assert dl.ds == int(0.5 * 60 / 120 * SR)
    run(eng, 5.0)                                   # bar 4 is at 6 s
    assert eng.bpm == 120 and eng.beat(eng.pos) < 12
    run(eng, 1.5)
    assert eng.bpm == 150 and 'tempo changes queued' not in eng.cmd_status()
    assert dl.ds == int(0.5 * 60 / 150 * SR) and len(dl.bl) == dl.ds + 1        # the delay follows the beat
    with pytest.raises(LiveError, match='Use a step change'):
        eng.cmd_tempo(160, ramp_beats=8)
    with pytest.raises(LiveError, match='from 20 to 400'):
        eng.cmd_tempo(1000)
    with pytest.raises(LiveError, match='already played'):
        eng.cmd_tempo(140, at='bar:2')


def test_a_later_change_replaces_one_queued_after_it(eng):
    kicks(eng)
    eng.cmd_tempo(130, at='bar:5')
    out = eng.cmd_tempo(100, at='bar:3')
    assert 'replaces the change queued at bar 5 (130 BPM)' in out
    assert [t for _, _, t in eng.tmap.segs] == [120.0, 100.0]


def test_a_recording_lists_the_change_and_listen_keeps_one_tempo(eng):
    kicks(eng)
    run(eng, 0.5)
    eng.cmd_record(True)                            # from bar 2
    eng.cmd_tempo(160, at='bar:4')
    meta = json.load(open(eng.rec_path[:-4] + '.json'))
    assert meta['first_bar'] == 2 and meta['bpm'] == 120 and meta['tempo'] == [[4.0, 160.0, 4.0]]
    run(eng, 9.0)
    eng.cmd_record(False)
    d = eng.cmd_listen_dump(bars=8)
    assert d['bpm'] == 160 and d['first'] == 4 and 'left out' in d['note']


def test_played_keys_keep_their_bank_across_a_change(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 60)
    monkeypatch.setattr(MP, 'HI', 64)
    opened = {}

    class FakeInput:
        def __init__(self, name, on_msg):
            self.name, self.on = name, on_msg
            opened[name] = self

        def close(self):
            opened.pop(self.name, None)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.midi = MidiPlay(e, sources=lambda: ['ismail keys'], opener=FakeInput)
    e.cmd_midi(source='keys', instrument='preset:pluck')
    bank = e.midi.patches['ismail keys']['bank']
    notes = dict(bank)
    jobs = e._jid
    out = e.cmd_tempo(150, at='next_bar')
    assert 'played keys keep their sounds' in out
    run(e, 3.0)
    assert e.bpm == 150 and e._jid == jobs                                      # nothing rendered again
    assert e.midi.patches['ismail keys']['bank'] is bank and all(bank[k] is notes[k] for k in notes)
    opened['ismail keys'].on(0.0, 0x90, 62, 100)
    assert np.max(np.abs(e._pull(BLOCK))) > 0.01


def test_the_clock_op_gives_the_tempo_at_the_heard_beat(eng):
    """live's clock op (listen_heard counts beats from it): the mix runs ahead of the speakers, so for a moment the
    mix is past a change the listener has not heard. The clock's bpm is the one at the heard beat."""
    kicks(eng)
    eng.cmd_tempo(150, at='bar:3')
    S = eng.tmap.segs[-1][0]
    while eng.pos <= S:
        eng.tick()
        eng.mix_block()
    assert eng.bpm == 150
    c = eng.cmd_clock()
    assert c['bpm'] == 150 and c['beat'] >= 8                    # no output running: the mixed position
    eng._null_on, eng.played = True, S - SR // 2                 # a silent output half a second behind the mix
    try:
        c = eng.cmd_clock()
        assert c['bpm'] == 120 and c['beat'] < 8                 # heard: still bar 2, at the old tempo
        eng.played = S + SR
        c = eng.cmd_clock()
        assert c['bpm'] == 150 and c['beat'] > 8
    finally:
        eng._null_on = False
