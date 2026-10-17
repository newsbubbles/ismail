"""The camera crew's tools: cutting to a camera (stage_on_air, its log) and the pointer only cameras see."""
import json

import pytest

from ismail.api import OPS, OpError
from test_stage import FakePage, stage  # noqa: F401  (the fixture)


def _cameras(scenes, *names):
    objs = {n: {'type': 'CAMERA'} for n in names}
    objs['desk'] = {'type': 'MESH'}
    (scenes / 'room' / 'manifest.json').write_text(json.dumps({'objects': objs}), encoding='utf-8')


def test_a_cut_puts_the_camera_on_the_monitor_and_is_logged(stage):
    _cameras(stage['scenes'], 'cam_a', 'cam_b')
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_on_air'](scene='room', camera='cam_a', note='the opening')
        c = page.seen[-1]
        assert (c['type'], c['material'], c['camera']) == ('surface', 'monitor_screen', 'cam_a')
        assert out.startswith('on air: cam_a at ')
        OPS['stage_on_air'](scene='room', camera='cam_b', monitor='screen_2', fps=10)
        assert (page.seen[-1]['material'], page.seen[-1]['fps']) == ('screen_2', 10)
        n = len(page.seen)
        OPS['stage_on_air'](scene='room', camera='cam_a', monitor=None)
        assert len(page.seen) == n                     # no monitor: logged only
        recs = [json.loads(x) for x in (stage['scenes'] / 'room' / 'on_air.jsonl').read_text().splitlines()]
        assert [r['camera'] for r in recs] == ['cam_a', 'cam_b', 'cam_a'] and recs[0]['note'] == 'the opening'
        lines = OPS['stage_on_air_log'](scene='room').splitlines()
        assert len(lines) == 3 and 'cam_a' in lines[0] and 'the opening' in lines[0] and lines[0].split()[3] == 's'
        assert lines[-1].split()[-2:] == ['on', 'air']
        assert 'no cuts' in OPS['stage_on_air_log'](scene='room', since='2999-01-01')
    finally:
        page.stop = True


def test_a_camera_the_scene_lacks_is_refused_and_not_logged(stage):
    _cameras(stage['scenes'], 'cam_a')
    page = FakePage(stage['port'], 'room')
    try:
        with pytest.raises(OpError, match="no camera 'desk'.*cam_a"):
            OPS['stage_on_air'](scene='room', camera='desk')
        assert not (stage['scenes'] / 'room' / 'on_air.jsonl').exists()
        assert 'no cuts yet' in OPS['stage_on_air_log'](scene='room')
    finally:
        page.stop = True


def test_the_pointer_reaches_the_page_and_bad_calls_do_not(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_pointer'](scene='room', person='body_a', hand='r', size=1.5)
        c = page.seen[-1]
        assert (c['type'], c['person'], c['hand'], c['when'], c['size'], c['style']) == ('pointer', 'body_a', 'r', 'point', 1.5, 'foam_finger')
        OPS['stage_pointer'](scene='room', off=True)
        assert page.seen[-1]['off'] is True and 'hand' not in page.seen[-1]
        n = len(page.seen)
        for bad in ({'hand': 'both'}, {'when': 'sometimes'}, {'style': 'laser'}):
            with pytest.raises(OpError):
                OPS['stage_pointer'](scene='room', **bad)
        assert len(page.seen) == n
        with pytest.raises(OpError, match='stage_pointer'):
            OPS['stage_cmd'](scene='room', type='pointer')
    finally:
        page.stop = True
