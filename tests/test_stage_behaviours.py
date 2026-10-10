"""Things that do things (behaviours.js): their state is saved with the scene, and agents have the same calls as the
person's press (the ops reach the page as typed commands)."""
import json
import urllib.error

import pytest

from ismail.api import OPS
from ismail.stage import server as S
from ismail.stage.ops_page import TYPED

from test_stage import FakePage, _get, _post, stage  # noqa: F401  (the fixture)


def test_state_is_saved_with_the_scene_and_checked(stage):  # noqa: F811
    p = stage['port']
    assert _get(p, 'behaviour_state?scene=room')[1] == {}
    assert _post(p, 'behaviour_state?scene=room', {'wall_switch': {'on': False}}) == {'ok': True, 'objects': 1}
    assert _get(p, 'behaviour_state?scene=room')[1] == {'wall_switch': {'on': False}}
    assert json.loads((stage['scenes'] / 'room' / 'behaviour_state.json').read_text(encoding='utf-8'))['wall_switch'] == {'on': False}
    for bad in ([1, 2], {'switch': 3}):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(p, 'behaviour_state?scene=room', bad)
        assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError):
        _post(p, 'behaviour_state?scene=nowhere', {})


def test_agents_press_list_and_set_state_like_the_person(stage):  # noqa: F811
    for t in ('behaviours', 'behaviour_run', 'behaviour_state'):
        assert t in S.CMD_TYPES and t in TYPED
    page = FakePage(stage['port'], 'room')
    try:
        assert '"reload": true' in OPS['stage_behaviours'](scene='room', reload=True)
        out = OPS['stage_behaviour_run'](scene='room', object='wall_switch', action='Off', sound=False)
        assert '"object": "wall_switch"' in out and '"sound": false' in out
        assert page.seen[-1]['type'] == 'behaviour_run' and 'value' not in page.seen[-1]
        OPS['stage_behaviour_run'](scene='room', object='wall_switch')
        assert 'sound' not in page.seen[-1] and page.seen[-1]['action'] == 'press'
        OPS['stage_behaviour_state'](scene='room', object='wall_switch', state={'on': True})
        assert page.seen[-1]['state'] == {'on': True}
    finally:
        page.stop = True
