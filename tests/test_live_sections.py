"""Cutting over mid-chapter (ledger M129): clips, stops and effect moves carry a set section's name, and
live_queue(cancel_section=) takes back what that section still has to do from a beat on. Headless: no device."""
import pytest

from ismail.live import djkit as K
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.timeline import Timeline


def test_the_timeline_takes_a_section_back_from_a_beat():
    tl = Timeline(4)
    a = tl.add('bass', [(0, 40, 1, 100)], 4, None, 0, 'bar:1')
    a.section = 'one'
    b = tl.add('lead', [(0, 60, 1, 100)], 4, None, 32, 'bar:9')
    b.section = 'one'
    other = tl.add('pad', [(0, 50, 1, 100)], 4, None, 32, 'bar:9')
    mine = tl.add('hat', [(0, 42, 1, 100)], 4, None, 32, 'bar:9')
    mine.section = 'one'
    tl.stop('bass', 44, 'one')
    tl.stop('pad', 48)
    assert tl.sections(0) == ['one']
    gone, cut, stops = tl.cancel_section('one', 16, keep={mine.id})
    assert gone == [b.id] and cut == [a.id] and stops == [('bass', 44)]
    assert tl.clips[a.id].end == 16                  # cut there: its last notes ring out
    assert other.id in tl.clips and mine.id in tl.clips and tl.stops['pad'] == [48]
    assert tl.cancel_section('two', 16) == ([], [], [])
    # a trial copy keeps the tags
    assert tl.copy().clips[a.id].section == 'one'


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    for t in ('k', 'u'):
        e.cmd_track(t, instrument={'type': 'kick'})
    e.cmd_track('h', instrument={'type': 'kick'},
                fx=[{'type': 'filter', 'cutoff': 8000}, {'type': 'delay', 'mix': 0.1}])
    return e


def run(eng, blocks):
    for _ in range(blocks):
        eng.tick()
        eng.mix_block()


def test_cancel_section_drops_clips_stops_and_moves(eng):
    out = eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}},
                         {'track': 'h', 'lanes': {'C1': 'x.x.x.x.x.x.x.x.'}, 'at': 'bar:9'},
                         {'track': 'k', 'stop': True, 'at': 'bar:12'},
                         {'track': 'u', 'lanes': {'C1': 'x...'}, 'at': 'bar:9', 'section': None}],
                        section='intro')
    assert 'c1 k: bar' in out and '[intro]' in out             # djkit reads ids with r'(c\d+) \S+: bar'
    eng.cmd_fx('h', 'filter', {'cutoff': 500}, ramp_beats=16, at='bar:3', section='intro')
    eng.cmd_moves([{'target': 'h', 'index': 'filter', 'params': {'cutoff': 3000}, 'at': 'bar:7', 'section': 'intro'},
                   {'target': 'h', 'index': 'delay', 'params': {'mix': 0.5}, 'at': 'bar:7'}])
    run(eng, 20)
    assert 'sections ahead: intro' in eng.cmd_status()
    out = eng.cmd_queue(cancel_section='intro', cancel_at='bar:5')
    assert 'cancel section intro from bar 5' in out
    assert 'removed c2' in out and 'cut c1 there' in out and 'dropped stops k at bar 12' in out
    assert 'dropped 1 effect moves' in out and 'h fx[0].cutoff holds 2000' in out
    assert eng.tl.clips['c1'].end == 16 and 'c2' not in eng.tl.clips and 'c3' in eng.tl.clips
    assert not eng.tl.stops.get('k')
    # the glide in progress stops where it is at bar 5 (half way, in log space: 2000) and stays there
    cut = eng.ramps[('track:h', 0, 'cutoff')]
    for beat in (16, 24, 40):
        assert cut.value(eng.sample(beat)) == pytest.approx(2000, rel=1e-3)
    assert eng.tracks['h']['path'].chain.fx[0]['cutoff'] == pytest.approx(2000, rel=1e-3)
    # a move sent without the section is kept
    assert eng.ramps[('track:h', 1, 'mix')].target.v1 == 0.5
    # nothing the section had is left to play from bar 5: no live segments of its clips after it
    s16 = eng.sample(16)
    assert not [s for s in eng.active + [p[2] for p in eng.pending]
                if s.cid in ('c1', 'c2') and not s.dead and s.start >= s16]


def test_a_batch_cuts_over_where_it_lands_and_keeps_its_own_clips(eng):
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...'}}, {'track': 'h', 'lanes': {'C1': 'x...'}}],
                  section='a')
    out = eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x.x.'}, 'at': 'bar:9', 'section': 'a'}],
                        cancel_section='a')
    assert 'cancel section a from bar 9: cut c2 there' in out
    assert eng.tl.clips['c2'].end == 32 and 'c3' in eng.tl.clips and eng.tl.clips['c3'].section == 'a'
    assert 'nothing of it left' in eng.cmd_queue(cancel_section='b')
    with pytest.raises(LiveError, match='cancel_at goes with cancel_section'):
        eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...'}}], cancel_at='bar:3')
    with pytest.raises(LiveError, match='cancel_section'):
        eng.cmd_queue([])


def test_a_cancel_leaves_a_param_with_nothing_scheduled_where_it_was(eng):
    eng.cmd_fx('h', 'filter', {'cutoff': 400}, at='bar:9', section='drop')
    out = eng.cmd_queue(cancel_section='drop', cancel_at='bar:5')
    assert 'dropped 1 effect moves' in out
    assert eng.ramps[('track:h', 0, 'cutoff')].value(eng.sample(40)) == pytest.approx(8000)
    run(eng, 4)                                                # the mixer still reads the param
    assert eng.pos == 4 * BLOCK


def test_the_dj_kit_tags_its_sections_and_cuts_them(tmp_path):
    eng = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    eng.cmd_track('k', instrument={'type': 'kick'}, fx=K.with_gain([]))
    ops = {'live_queue': lambda P, clips, **kw: eng.cmd_queue(clips, **kw),
           'live_fx': lambda P, moves=None, **kw: eng.cmd_moves(moves)}
    S = K.Set(str(tmp_path), ops=ops)
    S.q([{'track': 'k', 'lanes': {'C1': 'x...'}}], section='s01')
    S.moves([K.sweep('k', 'gain', 'gain_db', -12, 8, at=9)], section='s01')
    assert eng.tl.clips['c1'].section == 's01' and S.sections == {'s01': ['c1']}
    out = S.cut('s01', at=5)
    assert 'cancel section s01 from bar 5: cut c1 there' in out and 'dropped 1 effect moves' in out
    out = S.q([{'track': 'k', 'lanes': {'C1': 'x.x.'}, 'at': 'bar:7'}], section='s02', cut='s02')
    assert 'nothing of it left' in out


def test_the_dj_kit_still_queues_on_an_engine_from_before_sections(tmp_path):
    sent = []

    def old_queue(P, clips):
        if any('section' in c for c in clips):
            raise RuntimeError("clip [0]: unknown keys ['section']; valid: [...]")
        sent.append(clips)
        return 'c1 k: bar 2'
    S = K.Set(str(tmp_path), ops={'live_queue': old_queue})
    assert S.q([{'track': 'k', 'notes': '0 C1'}], section='s01') == 'c1 k: bar 2'
    assert sent == [[{'track': 'k', 'notes': '0 C1'}]] and S.sections == {'s01': ['c1']}
