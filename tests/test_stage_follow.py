"""Cameras that follow (behaviours.js s.where, s.place, s.follow and frame(s, dt); the op stage_follow) and a follow
baked into keys for the render (camrec.js source 'follow')."""
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from ismail.stage import server as S
from ismail.stage.ops_page import TYPED

from test_stage import FakePage, stage  # noqa: F401  (the fixture)

PAGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page'


def test_follow_reaches_the_page_with_only_the_options_given(stage):  # noqa: F811
    assert 'follow' in S.CMD_TYPES and TYPED['follow'] == 'stage_follow'
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_follow'](scene='room', object='chase_cam', target='actor_a:pelvis', offset=[0, -3, 1.4], lag=0.6,
                            look_offset=[0, 0, 0.5])
        c = page.seen[-1]
        assert (c['type'], c['object'], c['target']) == ('follow', 'chase_cam', 'actor_a:pelvis')
        assert c['options'] == {'offset': [0, -3, 1.4], 'space': 'target', 'lag': 0.6, 'look_offset': [0, 0, 0.5],
                                'look_lag': 0.25}
        OPS['stage_follow'](scene='room', object='drone_cam', target='person', orbit=20, snap=True, look=False)
        assert page.seen[-1]['options']['orbit'] == 20 and page.seen[-1]['options']['look'] is False
        OPS['stage_follow'](scene='room', object='chase_cam')                       # no target: stop
        assert page.seen[-1].get('target') is None and page.seen[-1]['object'] == 'chase_cam'
        OPS['stage_follow'](scene='room', where='actor_a:head')
        assert page.seen[-1]['where'] == 'actor_a:head' and 'object' not in page.seen[-1]
        OPS['stage_follow'](scene='room')
        assert set(page.seen[-1]) - {'principal'} == {'type', 'id', 'ts'}        # a list of what follows what (who sent it is stamped)
        with pytest.raises(OpError, match="'target' or 'world'"):
            OPS['stage_follow'](scene='room', object='chase_cam', target='person', space='camera')
        with pytest.raises(OpError, match=r'offset must be \[x, y, z\]'):
            OPS['stage_follow'](scene='room', object='chase_cam', target='person', offset=[1, 2])
        with pytest.raises(OpError, match='stage_follow'):
            OPS['stage_cmd'](scene='room', type='follow')
    finally:
        page.stop = True


def test_the_object_library_has_where_place_follow_and_a_frame_hook():
    b = (PAGE / 'behaviours.js').read_text(encoding='utf8')
    for call in ('where: (target) => where(target)', 'place: (name, o) => place(name, o)',
                 'follow: (target, o) => follow(me, target, o)', 'clock: () =>'):
        assert call in b, call
    assert "typeof defs[name].frame === 'function'" in b and "fail(name, 'frame', e)" in b   # one error stops it
    assert 'clock.hold(it.name)' in b and 'clock.unhold(name)' in b                        # its keys wait while it follows
    assert "unfollow(name, 'reload')" in b                                                 # a reload ends every follow
    assert 'performance.now()' in b and 'Math.exp(-dt / spec.lag)' in b                    # frame-rate free, capture-locked
    # the documented calls at the top match the handle
    assert 'where(target), place(name,' in b and "s.follow(null)" in b


def test_a_follow_bakes_into_keys_and_the_menu_offers_it():
    main = (PAGE / 'main.js').read_text(encoding='utf8')
    camrec = (PAGE / 'camrec.js').read_text(encoding='utf8')
    actions = (PAGE / 'actions.js').read_text(encoding='utf8')
    assert 'initBehaviours(ed, live, panels, hands, { clock: stageClock, actors })' in main
    assert 'follows: window.VR_behaviours.follows' in main
    assert main.index('initBehaviours(') < main.index('initCamRec(')   # the follow moves the camera before it is sampled
    assert "['head', 'hold', 'follow']" in camrec
    assert 'follows.snap(rec.name)' in camrec and "follows.stop(rec.name, 'baked')" in camrec
    assert 'follows.start(rec.name, rec.follow)' in camrec              # Undo and Redo follow again
    assert "fol && fol.of(it.name) ? 'follow'" in actions
