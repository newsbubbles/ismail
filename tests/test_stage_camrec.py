"""Camera trails and a camera move recorded like a take: the thinning (page/interp.js thinKeys, run in node), the
ops reaching the page, and the page pieces wired where the user reaches them."""
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from test_stage import FakePage, stage  # noqa: F401,F811  (the fixture)
from test_stage_interp import _node

PAGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page'


# a head-height orbit around a point, 6 s sampled at 40 per second (built in node: the samples are too long for a
# Windows command line)
ORBIT = """Array.from({length: 241}, (_, i) => { const t = 6 * i / 240, a = 0.6 * t, y = a + Math.PI / 2;
  return {t, location: [3 * Math.cos(a), 3 * Math.sin(a), 1.6], quaternion: [Math.cos(y / 2), 0, 0, Math.sin(y / 2)],
    scale: [1, 1, 1]}; })"""


def test_a_recorded_move_thins_to_few_keys_that_play_back_within_two_cm_and_two_degrees():
    got = _node(f"m.thinKeys({ORBIT}, {{mode: 'smooth'}})")
    assert 3 <= len(got['keys']) <= 40, len(got['keys'])
    assert got['err_m'] <= 0.02 and got['err_deg'] <= 2.0, got
    assert got['keys'][0]['t'] == 0 and abs(got['keys'][-1]['t'] - 6.0) < 1e-6   # the ends are kept


def test_frame_jitter_does_not_turn_into_keys():
    """Samples whose pose is up to 8 ms stale (a busy frame) and whose spacing wobbles: the shake is smoothed away,
    not followed (a moving average left 2 cm of it and the thinning made 56 keys of a 4 s arc)."""
    jittered = """(() => { let r = 7; const rnd = () => (r = (r * 16807) % 2147483647) / 2147483647; const out = []; let s = 0;
      while (s <= 4) { s += (1 + (rnd() - 0.5)) / 60; const w = s - 0.008 * rnd();
        out.push({t: s, location: [2 * Math.cos(w * 0.8), -2 * Math.sin(w * 0.8), 1.7 + 0.1 * Math.sin(w * 2)],
          quaternion: [1, 0, 0, 0], scale: [1, 1, 1]}); }
      return out; })()"""
    got = _node(f"m.thinKeys({jittered}, {{mode: 'smooth'}})")
    assert len(got['keys']) <= 14 and got['err_m'] <= 0.02, (len(got['keys']), got['err_m'])


def test_a_still_camera_thins_to_its_two_ends():
    got = _node("m.thinKeys(Array.from({length: 81}, (_, i) => ({t: i / 40, location: [1, 2, 1.6], quaternion: [1, 0, 0, 0], "
                "scale: [1, 1, 1]})), {mode: 'smooth'})")
    assert len(got['keys']) == 2 and got['err_m'] < 1e-6


def test_trail_and_camera_record_reach_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_trail'](scene='room', object='hall_wide_cam')
        assert (page.seen[-1]['type'], page.seen[-1]['names'], page.seen[-1]['show']) == ('trail', ['hall_wide_cam'], True)
        OPS['stage_trail'](scene='room', show=False)
        assert page.seen[-1].get('names') is None and page.seen[-1]['show'] is False
        OPS['stage_camera_record'](scene='room', camera='hall_wide_cam', source='head', t0=2, t1=8, countdown=0)
        c = page.seen[-1]
        assert (c['type'], c['action'], c['name'], c['source'], c['t0'], c['t1']) == ('cam_record', 'start', 'hall_wide_cam', 'head', 2, 8)
        OPS['stage_camera_record'](scene='room', camera='hall_wide_cam', action='keep')
        assert page.seen[-1]['action'] == 'keep' and 't0' not in page.seen[-1]
        with pytest.raises(OpError, match='start, stop'):
            OPS['stage_camera_record'](scene='room', camera='x', action='rewind')
        OPS['stage_camera_record'](scene='room', camera='hall_wide_cam')
        assert 'source' not in page.seen[-1]                            # the page picks: follow, else head
        with pytest.raises(OpError, match="'head', 'hold' or 'follow'"):
            OPS['stage_camera_record'](scene='room', camera='x', source='drone')
        with pytest.raises(OpError, match='stage_camera_record'):
            OPS['stage_cmd'](scene='room', type='cam_record')
    finally:
        page.stop = True


def test_the_camera_menu_offers_the_path_and_a_recorded_move():
    actions = (PAGE / 'actions.js').read_text(encoding='utf8')
    main = (PAGE / 'main.js').read_text(encoding='utf8')
    camrec = (PAGE / 'camrec.js').read_text(encoding='utf8')
    assert "'〰 Path'" in actions and "'⏺ Record move'" in actions
    assert "ed.unlocked === it ? 'hold' : 'head'" in actions          # carried when unlocked, the eyes otherwise
    assert 'initTrails(ed, live, stageClock, xr)' in main and 'initCamRec(ed, live, stageClock, panels' in main
    assert "'👍 Keep', '↻ Redo', '↶ Undo'" in camrec
    assert 'clock.hold(it.name)' in camrec and 'clock.unhold(rec.name)' in camrec   # the keys never fight the take
