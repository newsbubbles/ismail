"""A take replayed as input (the page's hands, touch and grab driven by a recorded take while nobody is in VR), and a
test take asked for, recorded and labelled by an agent."""
import json
import threading
from pathlib import Path

from ismail.api import OPS
from ismail.stage import ops_page
from ismail.stage import server as S
from test_stage import FakePage, _post, stage  # noqa: F401  (the fixture)

PAGE = Path(ops_page.__file__).parent / 'page'


def test_replay_is_wired_into_the_page_and_the_link():
    main = (PAGE / 'main.js').read_text(encoding='utf-8')
    assert "import { initReplay } from './replay.js';" in main
    assert 'initReplay(ed, live, xr, hands, grab, desktop)' in main
    # hands, touch and grab run while a take plays, not only in VR
    assert 'if (ed.renderer.xr.isPresenting || replaying)' in main
    rp = (PAGE / 'replay.js').read_text(encoding='utf-8')
    assert 'live.handlers.replay_input' in rp
    assert 'isPresenting' in rp                         # refused while someone is in VR, stopped if VR starts
    assert "live.emit('replay_done'" in rp
    assert 'replay_input' in S.CMD_TYPES
    assert ops_page.TYPED['replay_input'] == 'stage_take_replay'


def test_replay_reports_what_the_page_did(stage):
    page = FakePage(stage['port'], 'room', answers={'replay_input': {'seconds': 0.2, 'frames': 30, 'hands': ['right']}})
    done = {'type': 'replay_done', 'take': 'reach', 'why': 'end', 'seconds': 0.2, 'frames': 30, 'held': [],
            'events': [{'t': 1.5, 'type': 'grabbed', 'object': 'cup', 'side': 'right'},
                       {'t': 3.0, 'type': 'grab_landed', 'object': 'cup', 'onto': 'shelf'}],
            'recorded': [{'t': 1.4, 'type': 'grab'}]}
    threading.Timer(0.5, lambda: _post(stage['port'], 'live/event?scene=room', done)).start()
    try:
        out = OPS['stage_take_replay'](scene='room', take='reach', start=1, end=4, grab=True)
    finally:
        page.stop = True
    sent = [c for c in page.seen if c['type'] == 'replay_input'][0]
    assert sent['take'] == 'reach' and sent['from'] == 1 and sent['to'] == 4 and sent['grab'] is True
    assert 'grabbed: cup; landed: cup onto shelf' in out
    assert 'the take recorded 1 marks' in out


def test_verdict_names_what_was_still_held():
    v = ops_page._replay_verdict({'take': 'x', 'frames': 2, 'seconds': 1, 'why': 'stopped', 'events': [],
                                  'recorded': [], 'held': ['box']})
    assert 'grabbed: nothing; landed: nothing; still held at the end (let go): box' in v


def test_a_test_take_is_asked_recorded_and_labelled(stage):
    d = stage['scenes'] / 'room' / 'takes' / 't1'
    d.mkdir(parents=True)
    (d / 'meta.json').write_text(json.dumps({'id': 't1'}), encoding='utf-8')
    page = FakePage(stage['port'], 'room', answers={'take_start': {'id': 't1'},
                                                    'take_stop': {'id': 't1', 'frames': 0, 'seconds': 1.1}})
    try:
        out = OPS['stage_test_take'](scene='room', ask='Pick up the cup and put it on the shelf.', name='cup to shelf',
                                     seconds=1, countdown=0, voice=False, sender='test agent')
    finally:
        page.stop = True
    kinds = [c['type'] for c in page.seen]
    assert kinds == ['say', 'take_start', 'say', 'take_stop', 'say']
    assert page.seen[0]['text'].startswith('Pick up the cup') and page.seen[0]['aloud'] is True
    assert page.seen[1]['name'] == 'test_cup_to_shelf'
    assert 'no frames' in out                          # the fake page is not in VR
    m = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
    assert m['kind'] == 'test' and m['ask'].startswith('Pick up the cup') and m['label'] == 'test cup to shelf'
    assert m['notes'][0]['by'] == 'test agent'
    assert 't1' in OPS['stage_takes'](scene='room', query='shelf')


def test_the_last_two_minutes_in_vr_can_be_kept():
    hands = (PAGE / 'hands.js').read_text(encoding='utf-8')
    assert 'const RECENT_S = 120, RECENT_HZ = 15;' in hands
    # sampled only in VR: a replay drives the same hands and must never fill it
    assert 'if (xr.isPresenting && now - recent.last >= 1000 / RECENT_HZ - 1) sampleRecent(now);' in hands
    assert "from: 'recent'" in hands
    main = (PAGE / 'main.js').read_text(encoding='utf-8')
    assert 'live.handlers.take_keep_recent' in main
    assert 'take_keep_recent' in S.CMD_TYPES
    assert ops_page.TYPED['take_keep_recent'] == 'stage_take_keep_recent'


def test_keep_recent_sends_how_much_to_keep(stage):
    page = FakePage(stage['port'], 'room', answers={'take_keep_recent': {'id': 'r1', 'frames': 450, 'seconds': 30}})
    try:
        out = OPS['stage_take_keep_recent'](scene='room', seconds=30, name='reach')
    finally:
        page.stop = True
    sent = [c for c in page.seen if c['type'] == 'take_keep_recent'][0]
    assert sent['seconds'] == 30 and sent['name'] == 'reach' and 'info' not in sent
    assert '"id": "r1"' in out
