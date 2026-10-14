"""Edits that apply on an open page without reloading the room: material colours, world.json, merged pieces."""
import json
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from test_stage import FakePage, stage  # noqa: F401  (the fixture)

PAGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page'
BRIDGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'bridge'


def test_a_material_colour_reaches_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_material_set'](scene='room', material='paint_a', color=[0.8, 0.1, 0.1])
        c = page.seen[-1]
        assert (c['type'], c['material'], c['color']) == ('material', 'paint_a', [0.8, 0.1, 0.1])
        OPS['stage_material_set'](scene='room', object='chair', color=[0, 0, 1])
        assert page.seen[-1]['object'] == 'chair' and 'material' not in page.seen[-1]
        with pytest.raises(OpError, match='stage_material_set'):
            OPS['stage_cmd'](scene='room', type='material')
    finally:
        page.stop = True


def test_a_world_change_is_taken_by_an_open_page_at_once(stage):
    page = FakePage(stage['port'], 'room', answers={'world_reload': {'changed': ['floor'], 'applied': []}})
    try:
        out = OPS['stage_world'](scene='room', world={'floor': 0.2})
        assert page.seen[-1]['type'] == 'world_reload'
        assert 'the page:' in out and '"changed": ["floor"]' in out
        with pytest.raises(OpError, match='stage_world'):
            OPS['stage_cmd'](scene='room', type='world_reload')
    finally:
        page.stop = True


def test_a_world_change_with_no_page_waits_for_the_next_load(stage):
    out = OPS['stage_world'](scene='room', world={'floor': 0.1})
    assert out.endswith('the page takes it at its next load')


def test_merged_pieces_keep_their_names_for_the_page():
    """quest.py numbers each piece it joins (_part) and the bridge lists them in the manifest (merged), so live.js
    can split one back out by name (editor.unmerge) instead of failing with no object."""
    q = (BRIDGE / 'quest.py').read_text(encoding='utf-8')
    assert "attributes.new('_part', 'FLOAT', 'POINT')" in q and "S['vr_merged']" in q
    b = (BRIDGE / 'blender_bridge.py').read_text(encoding='utf-8')
    assert "man['merged']" in b
    live = (PAGE / 'live.js').read_text(encoding='utf-8')
    assert 'ed.unmerge(name)' in live
    ed = (PAGE / 'editor.js').read_text(encoding='utf-8')
    assert 'unmerge(name) {' in ed and 'this.unmerge(name)' in ed      # find() and saved edits both split pieces


def test_every_typed_page_command_is_known_to_the_server():
    from ismail.stage.ops_page import TYPED
    from ismail.stage.server import CMD_TYPES
    assert not set(TYPED) - CMD_TYPES, json.dumps(sorted(set(TYPED) - CMD_TYPES))
