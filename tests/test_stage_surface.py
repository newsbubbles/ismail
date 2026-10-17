"""Live surfaces (stage_surface, page/surfaces.js): an image, a video, text or a camera on a material."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from test_stage import FakePage, stage  # noqa: F401  (the fixture)

SURFACES = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'surfaces.js').read_text(encoding='utf-8')


def test_each_source_reaches_the_page_with_its_own_fields(stage):
    (stage['scenes'] / 'room' / 'slides').mkdir()
    (stage['scenes'] / 'room' / 'slides' / 's01.png').write_bytes(b'png')
    (stage['scenes'] / 'room' / 'loop.mp4').write_bytes(b'mp4')
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_surface'](scene='room', material='board', image='slides/s01.png', fit='cover')
        c = page.seen[-1]
        assert (c['type'], c['material'], c['image'], c['fit']) == ('surface', 'board', 'slides/s01.png', 'cover')
        assert 'text' not in c and 'camera' not in c
        OPS['stage_surface'](scene='room', material='board', video='loop.mp4', start=2.5)
        c = page.seen[-1]
        assert (c['video'], c['from'], c['loop'], c['sound']) == ('loop.mp4', 2.5, True, False)
        OPS['stage_surface'](scene='room', object='prompter', text='one\ntwo', lines=6, scroll=0.5)
        c = page.seen[-1]
        assert (c['object'], c['text'], c['lines'], c['scroll']) == ('prompter', 'one\ntwo', 6, 0.5)
        OPS['stage_surface'](scene='room', material='monitor_screen', camera='wide_cam', fps=10)
        assert (page.seen[-1]['camera'], page.seen[-1]['fps']) == ('wide_cam', 10)
        OPS['stage_surface'](scene='room', clear=True)
        c = page.seen[-1]
        assert c['clear'] is True and c.get('material') is None and 'image' not in c
        with pytest.raises(OpError, match='stage_surface'):
            OPS['stage_cmd'](scene='room', type='surface')
    finally:
        page.stop = True


def test_bad_calls_fail_before_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        n = len(page.seen)
        with pytest.raises(OpError, match='material'):
            OPS['stage_surface'](scene='room', text='hi')
        with pytest.raises(OpError, match='one of'):
            OPS['stage_surface'](scene='room', material='board')
        with pytest.raises(OpError, match='one of'):
            OPS['stage_surface'](scene='room', material='board', text='hi', camera='cam')
        with pytest.raises(OpError, match='fit'):
            OPS['stage_surface'](scene='room', material='board', text='hi', fit='zoom')
        with pytest.raises(OpError, match='not a file in the scene folder'):
            OPS['stage_surface'](scene='room', material='board', image='missing.png')
        outside = stage['scenes'] / 'outside.png'
        outside.write_bytes(b'png')
        with pytest.raises(OpError, match='not a file in the scene folder'):
            OPS['stage_surface'](scene='room', material='board', image='../outside.png')
        assert len(page.seen) == n
    finally:
        page.stop = True


def _node(src):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    r = subprocess.run(['node', '--input-type=module', '-e', src], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_fit_and_the_wrap_place_things_where_they_should():
    # the two pure helpers, lifted out of the module (it imports three and the page)
    fit = SURFACES[SURFACES.index('function fitRect'):SURFACES.index('// text wrapped')]
    wrap = SURFACES[SURFACES.index('function wrap'):SURFACES.index('export function initSurfaces')]
    out = _node(fit + wrap + """
const ctx = { measureText: (s) => ({ width: s.length * 10 }) };
console.log(JSON.stringify({
  contain: fitRect(1600, 900, 1000, 340, 'contain'),
  cover: fitRect(1600, 900, 1000, 340, 'cover'),
  stretch: fitRect(1600, 900, 1000, 340, 'stretch'),
  wrap: wrap(ctx, 'aaa bbb ccc\\n\\nddd', 75),
}));""")
    x, y, w, h = out['contain']
    assert h == pytest.approx(340) and w == pytest.approx(340 * 16 / 9) and x == pytest.approx((1000 - w) / 2) and y == 0
    x, y, w, h = out['cover']
    assert w == pytest.approx(1000) and h == pytest.approx(562.5) and y == pytest.approx((340 - 562.5) / 2)
    assert out['stretch'] == [0, 0, 1000, 340]
    assert out['wrap'] == ['aaa bbb', 'ccc', '', 'ddd']
