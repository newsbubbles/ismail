"""Performer voices (a module with perform(): strings that ring on, a kit that resonates) play live in bar chunks,
each rendered with the part before it as context and crossfaded at the bar line; a voice that keys its randomness
on the song beat (beat0) then plays the same live as in a studio render of the whole part. Per-note events with one
generator per render gave every slice different humanization (a kit's hi-hat changed timbre) and an 8 s tail each
(kit70 rendered at 0.5x realtime)."""
import numpy as np

from ismail import instruments
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live.engine import BLOCK, Engine, _chunk_spans
from ismail.live.ops import _mark_performer
from ismail.notation import parse_notes

RIFF = '0 E2 0.5 100; 0.5 G2 0.5 90; 1 A2 1.5 100; 3 E2 0.25 80; 4 E2 0.5 100; 4.5 B2 2 95; 7 A2 1 90'


def capture(eng, track, until):
    got = {}
    orig = G.Chain.process

    def process(self, x, block_for):
        y = orig(self, x, block_for)
        if self is eng.tracks[track]['path'].chain:
            got[block_for(0).pos] = y.copy()
        return y
    G.Chain.process = process
    try:
        while eng.pos < until:
            eng.tick()
            eng.mix_block()
    finally:
        G.Chain.process = orig
    out = np.zeros((2, eng.pos))
    for p0, y in got.items():
        out[:, p0:p0 + y.shape[1]] = y
    return out


def env_db(x):
    f = int(0.05 * SR)
    e = np.array([np.sqrt(np.mean(x[:, i:i + f] ** 2)) for i in range(0, x.shape[1] - f, f)])
    d = 20 * np.log10(e + 1e-9)
    return np.maximum(d, d.max() - 50)


def test_a_keyed_performer_plays_live_as_in_a_whole_part_render(tmp_path):
    inst = _mark_performer({'type': 'code', 'voice': 'electric', 'params': {'kind': 'pbass', 'mode': 'mono'}},
                           str(tmp_path))
    assert inst.get('performer')
    eng = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    eng.cmd_track('b', instrument=inst, fx=[{'type': 'gain'}])     # 0 dB: a chain to capture the track at
    eng.cmd_queue([{'track': 'b', 'notes': RIFF, 'bars': 2, 'loop': 1, 'at': 'bar:2', 'beat0': 32}])
    spans = eng.meta[next(iter(eng.tl.clips))]['groups']
    assert [(s.on, s.end) for s in spans] == [(0, 4), (4, 8)]
    start = eng.sample(4)
    n = int(5.0 * SR)
    live = capture(eng, 'b', start + n)[:, start:start + n]
    notes = [(s * 0.5, m, d * 0.5, v) for s, m, d, v in parse_notes(RIFF)]
    whole = instruments.render_instrument(instruments.normalize(dict(inst)), notes, n, None, 120.0, SR, str(tmp_path),
                                          32.0)
    lv = lambda x: 10 * np.log10(np.mean(x ** 2))  # noqa: E731
    assert abs(lv(live) - lv(whole)) < 0.3
    assert np.corrcoef(env_db(live), env_db(whole))[0, 1] > 0.99
    # the bar line at 2 s: the chunks meet without a click (no jump bigger than the signal's own steps)
    i = int(2.0 * SR)
    seam = np.abs(np.diff(live[0, i - 64:i + 64])).max()
    assert seam < 4 * np.abs(np.diff(whole[0, i - 2000:i + 2000])).max()


def test_chunk_spans_keep_a_long_held_note_in_one_chunk():
    notes = [(0.0, 40, 1.0, 100), (8.0, 40, 40.0, 100), (12.0, 43, 1.0, 100)]   # a drone from bar 3 to bar 12
    spans = _chunk_spans(notes, 64.0, 4, reach=16.0)
    assert (spans[0].on, spans[0].end) == (0, 4)                   # bar 2 is silent: no chunk
    assert spans[1].on == 8 and spans[1].end == 12                 # held under 16 beats: bar by bar
    assert any(s.end == 48 for s in spans)                         # held longer: one chunk to the drone's end


def test_a_looping_performer_clip_takes_context_across_the_loop(tmp_path):
    inst = _mark_performer({'type': 'code', 'voice': 'electric', 'params': {'kind': 'strat'}}, str(tmp_path))
    eng = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    eng.cmd_track('g', instrument=inst)
    eng.cmd_queue([{'track': 'g', 'notes': '0 E3 1; 2 G3 2.5', 'bars': 1, 'loop': 3, 'at': 'bar:2'}])
    c = next(iter(eng.tl.clips.values()))
    assert eng._variant(c, 0, 0) == (False, True, False)          # pass 1: no context, cut (pass 2 follows)
    assert eng._variant(c, 0, 1) == (True, True, False)           # pass 2: context from pass 1
    assert eng._variant(c, 0, 2) == (True, False, False)          # last pass: rings out
    lo, ns, chunk = eng._chunk(c, 0, 1)
    assert lo < 0 and chunk['fade_in'] and any(s < 0 for s, *_ in ns)   # the G held over the loop point is context
    run_to = eng.sample(4 + 12) + SR
    while eng.pos < run_to:
        eng.tick()
        eng.mix_block()
    assert eng.stats['late'] == 0


def test_a_deck_performer_hears_what_came_before_its_window(tmp_path):
    from ismail import api
    song = str(tmp_path / 'song')
    api.project_new(song, bpm=120, length_bars=8)
    api.track_add(song, 'g', instrument={'type': 'code', 'voice': 'electric', 'params': {'kind': 'strat'}})
    api.notes_write(song, 'g', 1, '0 E3 2 100; 6 G3 4 100; 8 A3 1 100; 12 B3 1 100')   # G3 held across bar 3
    eng = Engine(str(tmp_path / 'eng'), bpm=120, bpb=4, workers=0, device='none')
    eng.cmd_load('A', song, bars=[3, 4], at='bar:2', loop=False, performers=['g'])
    c = next(iter(eng.tl.clips.values()))
    assert [n[1] for n in c.notes] == [57, 59]                     # the held G3 is not struck again on beat 1
    assert sorted(n[1] for n in c.context) == [52, 55]             # the 8 beats before the window
    lo, ns, chunk = eng._chunk(c, 0, 0)
    assert lo == -2 and chunk['pre'] == 2.0 and ns[0][1] == 55    # the held G3 from its start (beats); E3 is past 1 s


def test_a_looping_performer_never_sees_a_negative_song_time(tmp_path):
    # M50: the context of a wrapped chunk sits at negative clip beats; a voice that seeds on the song time (the birds
    # voice keys each note on its time in ms) got a negative seed
    (tmp_path / 'voices').mkdir()
    (tmp_path / 'voices' / 'keyed.py').write_text(
        "import numpy as np\n"
        "SEEN = []\n"
        "def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0):\n"
        "    SEEN.extend(beat0 + st * bpm / 60.0 for st, *_ in notes)\n"
        "    return np.zeros(total_n)\n")
    inst = _mark_performer({'type': 'code', 'voice': 'keyed', 'tail': 0.5}, str(tmp_path))
    assert inst.get('performer')
    eng = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    eng.cmd_track('k', instrument=inst)
    eng.cmd_queue([{'track': 'k', 'notes': '0 E3 1; 2 G3 2.5', 'bars': 1, 'loop': 3, 'at': 'bar:2'}])
    c = next(iter(eng.tl.clips.values()))
    assert eng._chunk(c, 0, 1)[0] < 0                               # the wrapped context really is before beat 0
    run_to = eng.sample(4 + 12) + SR
    while eng.pos < run_to:
        eng.tick()
        eng.mix_block()
    from ismail import voices
    seen = voices.load('keyed', str(tmp_path)).SEEN
    assert seen and min(seen) >= 0
