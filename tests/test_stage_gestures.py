"""The gesture map (stage/gestures.py, page/gestures.js): which hand shape means which intent, as data a person or a
scene can rebind, with locked rows nobody can take away and collisions refused by name."""
import json
import re
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from ismail.stage import gestures as gm
import nodejs
from test_stage import FakePage, _get, stage  # noqa: F401  (the fixture)

PAGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page'


def test_the_engine_defaults_do_not_collide_and_name_known_shapes():
    rows, coll = gm.resolve({})
    assert coll == []
    for intent, r in rows.items():
        assert r['owner'] == 'engine'
        assert r['where'] in gm.WHERE and r['hand'] in gm.HANDS
        assert {r['shape'], *r.get('also', [])} <= set(gm.SHAPES), intent
    assert {k for k, r in rows.items() if r.get('lock') == 'locked'} == {'vr_exit', 'stop', 'voice_note'}


def test_a_rebind_reaches_an_open_page_and_follows_the_person(stage):
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_gesture_set'](scene='room', intent='reload', shape='ok')
        assert 'reload: left ok, held 0.7 s, person row' in out and out.endswith('the page has it now')
        assert page.seen[-1]['type'] == 'gestures_reload'
    finally:
        page.stop = True
    assert json.loads(gm.person_file().read_text(encoding='utf-8')) == {'rows': {'reload': {'shape': 'ok'}}}
    listed = OPS['stage_gestures'](scene='attic')                 # the person's rows hold in every scene
    assert 'reload: left ok, held 0.7 s' in listed and '[person]' in listed
    assert 'COLLISION' not in listed


def test_locked_and_fixed_rows_are_refused():
    with pytest.raises(ValueError, match='locked'):
        gm.check_binding({}, 'person', 'vr_exit', shape='ok')
    with pytest.raises(ValueError, match='locked'):
        gm.check_binding({}, 'person', 'stop', off=True)
    with pytest.raises(ValueError, match='fixed .*thumb coming down'):
        gm.check_binding({}, 'person', 'travel', shape='point')


def test_a_collision_is_refused_naming_the_other_row(stage):
    with pytest.raises(OpError, match=r'collides with travel \(either hand gun, engine, fixed\)'):
        OPS['stage_gesture_set'](scene='room', intent='reload', shape='gun')
    with pytest.raises(OpError, match='collides with voice_note'):
        OPS['stage_gesture_set'](scene='room', intent='reload', shape='phone')
    with pytest.raises(OpError, match='collides with answer_no'):
        OPS['stage_gesture_set'](scene='room', intent='drop', hand='right')
    with pytest.raises(OpError, match='collides with hold'):
        OPS['stage_gesture_set'](scene='room', intent='drop', shape='fist')
    assert not gm.person_file().exists()                          # a refusal saves nothing


def test_where_and_which_hand_decide_a_collision():
    with pytest.raises(ValueError, match='collides with select'):  # something selected and the ray on a thing: both hold
        gm.check_binding({}, 'person', 'drop', shape='pinch')
    assert gm._where_meet('pointing', 'question') and not gm._where_meet('pointing', 'on_thing')
    assert not gm._where_meet('performing', 'anywhere')             # every other gesture is off while performing
    assert gm._hands_meet('either', 'left') and not gm._hands_meet('both', 'left')


def test_the_person_wins_over_the_scene_and_reset_goes_back(stage):
    sc = str(stage['scenes'])
    OPS['stage_gesture_set'](scene='room', intent='reload', shape='peace', owner='scene', scenes=sc)
    w = json.loads((stage['scenes'] / 'room' / 'world.json').read_text(encoding='utf-8'))
    assert w['gestures'] == {'reload': {'shape': 'peace'}}
    OPS['stage_gesture_set'](scene='room', intent='reload', shape='ok', hold_ms=900, scenes=sc)
    rows, _ = gm.resolve(gm.layers(stage['scenes'], 'room'))
    assert (rows['reload']['shape'], rows['reload']['hold_ms'], rows['reload']['owner']) == ('ok', 900, 'person')
    out = OPS['stage_gesture_reset'](scene='room', intent='reload', scenes=sc)
    assert 'reset reload' in out
    rows, _ = gm.resolve(gm.layers(stage['scenes'], 'room'))
    assert (rows['reload']['shape'], rows['reload']['owner']) == ('peace', 'scene')
    rows, _ = gm.resolve(gm.layers(stage['scenes'], 'attic'))  # the scene's row stays in its scene
    assert rows['reload']['owner'] == 'engine'
    with pytest.raises(OpError, match='do not set drop'):
        OPS['stage_gesture_reset'](scene='room', intent='drop', scenes=sc)


def test_world_json_gestures_are_checked(stage):
    sc = str(stage['scenes'])
    for bad, why in (({'vr_exit': {'shape': 'ok'}}, 'locked'), ({'reload': {'shape': 'gun'}}, 'collides with travel'),
                     ({'reload': {'colour': 'red'}}, 'must be'), ({'nod': {'shape': 'ok'}}, 'no intent')):
        with pytest.raises(OpError, match=why):
            OPS['stage_world'](scene='room', scenes=sc, world={'gestures': bad})
    OPS['stage_world'](scene='room', scenes=sc, world={'gestures': {'drop': {'hold_ms': 800}}})


def test_the_page_reads_the_map_in_force(stage):
    OPS['stage_gesture_set'](scene='room', intent='drop', off=True, scenes=str(stage['scenes']))
    got = _get(stage['port'], 'gestures?scene=room')[1]
    assert got['collisions'] == []
    assert got['rows']['drop']['off'] is True and got['rows']['drop']['owner'] == 'person'
    assert got['rows']['vr_exit']['words'] == 'both thumbs up, held 1.5 s'
    assert _get(stage['port'], 'gestures?scene=../x')[1]['rows']['reload']['owner'] == 'engine'


def test_the_answers_can_be_rebound_but_never_switched_off(stage):
    with pytest.raises(OpError, match='answer_yes cannot be switched off: stage_ask'):
        OPS['stage_gesture_set'](scene='room', intent='answer_yes', off=True)
    with pytest.raises(ValueError, match='answer_no cannot be switched off'):
        gm.check_binding({}, 'scene', 'answer_no', off=True)
    with pytest.raises(OpError, match='answer_no cannot be switched off'):
        OPS['stage_world'](scene='room', scenes=str(stage['scenes']), world={'gestures': {'answer_no': {'off': True}}})
    rows, coll = gm.resolve({'person': {'answer_yes': {'off': True, 'hold_ms': 600}}})   # a hand edit
    assert not rows['answer_yes'].get('off') and rows['answer_yes']['hold_ms'] == 600
    assert coll == ['IGNORED person row answer_yes: off True (stage_ask is answered with it)']


def test_a_hand_edited_person_file_is_checked_on_read(stage):
    f = gm.person_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({'rows': {'reload': {'hold_ms': 'long', 'shape': 'ok'}, 'drop': {'hand': 'third'},
                                      'answer_no': 'nope', 'select': {'shape': 'ok'}}}), encoding='utf-8')
    listed = OPS['stage_gestures'](scene='room')
    assert "IGNORED person row reload: hold_ms 'long' is not 0 to 10000" in listed
    assert 'reload: left ok, held 0.7 s' in listed                 # the good value holds, the engine's hold stands
    assert "IGNORED person row drop: no hand 'third'" in listed
    assert "IGNORED person row answer_no: 'nope' is not a binding" in listed
    assert 'select: either hand pinch' in listed                   # a fixed row is never overridden, not even by hand
    assert 'IGNORED person row select: it is fixed' in listed
    got = _get(stage['port'], 'gestures?scene=room')[1]
    assert got['rows']['reload']['hold_ms'] == 700 and any('IGNORED' in c for c in got['collisions'])
    OPS['stage_gesture_set'](scene='room', intent='reload', hold_ms=900)    # the next save drops the junk
    assert json.loads(f.read_text(encoding='utf-8'))['rows']['reload'] == {'shape': 'ok', 'hold_ms': 900}


# ---- the page side, run in node

MAP = PAGE.joinpath('gestures.js').as_uri()


def _node(body, rows=None):
    fetch = (f"async () => ({{ json: async () => ({json.dumps({'rows': rows})}) }})" if rows
             else "async () => { throw new Error('no server'); }")
    js = (f"globalThis.fetch = {fetch};\n"
          "const hands = { state: { left: { g: 'none', cand: 'none', f: null }, right: { g: 'none', cand: 'none', f: null } } };\n"
          "const hand = (side, g, more = {}) => Object.assign(hands.state[side], { g, cand: g, f: {}, resting: false }, more);\n"
          f"import('{MAP}').then(async (m) => {{ const G = m.initGestures(hands, () => 'room'); "
          "await new Promise((r) => setTimeout(r, 0)); " + body + " });")
    return nodejs.run(js)


def test_the_page_defaults_match_the_engine():
    got = _node("console.log(JSON.stringify(G.rows));")
    for intent, d in gm.DEFAULTS.items():
        assert {k: got[intent].get(k) for k in ('shape', 'hand', 'hold_ms')} == {k: d[k] for k in ('shape', 'hand', 'hold_ms')}
        assert got[intent].get('also', []) == d.get('also', [])
    assert set(got) == set(gm.DEFAULTS)


def test_the_page_reads_hands_through_the_map():
    got = _node("""
      const out = {};
      hand('left', 'thumbs_up'); out.reload = G.on('reload'); out.exitOne = G.on('vr_exit');
      hand('right', 'thumbs_up'); out.exitBoth = G.on('vr_exit'); out.yes = G.on('answer_yes');
      hand('left', 'thumbs_up', { resting: true }); out.resting = G.on('reload');
      hand('left', 'thumbs_down', { f: null }); hand('right', 'thumbs_down', { f: null });
      out.stopLost = G.on('stop', { lost: true }); out.stopTracked = G.on('stop');
      hand('right', 'none', { cand: 'phone' }); out.phoneForming = G.on('voice_note', { cand: true }); out.phone = G.on('voice_note');
      out.words = [G.words('reload'), G.words('vr_exit'), G.words('voice_note'), G.words('frame_shot')];
      out.hold = [G.hold('reload', 1), G.hold('answer_yes', 450)];
      console.log(JSON.stringify(out));""")
    assert (got['reload'], got['exitOne'], got['exitBoth'], got['yes']) == ('left', None, 'both', 'right')
    assert got['resting'] is None
    assert (got['stopLost'], got['stopTracked']) == ('both', None)   # the stop holds through a tracking dropout
    assert (got['phoneForming'], got['phone']) == ('right', None)
    assert got['words'] == ['left thumbs up', 'both thumbs up', 'either hand phone', 'both hands gun']
    assert got['hold'] == [700, 450]                                  # answers keep each module's own hold


def test_a_rebound_map_moves_the_gesture_on_the_page():
    rows = {**{k: dict(v) for k, v in gm.DEFAULTS.items()}}
    rows['reload'].update(shape='ok', hand='right', hold_ms=900)
    rows['answer_no']['off'] = True
    got = _node("""
      await G.reload();
      hand('left', 'thumbs_up'); hand('right', 'thumbs_down');
      const a = [G.on('reload'), G.on('answer_no'), G.handOf('reload')];
      hand('right', 'ok');
      console.log(JSON.stringify([...a, G.on('reload'), G.hold('reload', 700), G.words('reload')]));""", rows=rows)
    assert got == [None, None, 'right', 'right', 900, 'right ok']


def test_the_modules_ask_the_map_instead_of_naming_shapes():
    """A module that compares a hand to a bound shape itself would not follow a rebind."""
    for f in ('updates.js', 'exit.js', 'perform.js', 'touch.js', 'panels.js'):
        src = (PAGE / f).read_text(encoding='utf-8')
        assert not re.search(r"\.(g|cand) === '(thumbs_up|thumbs_down|phone)'", src), f
    voice = (PAGE / 'voice.js').read_text(encoding='utf-8')
    assert "gestures.on('voice_note'" in voice and "G.on('answer_yes')" in voice
    main = (PAGE / 'main.js').read_text(encoding='utf-8')
    assert 'hands.gestures = initGestures(' in main and 'live.handlers.gestures_reload' in main
