"""Actor profiles beside their bodies (rig type, named parts, control map presets) and the control map ops."""
import json
import struct
import urllib.error

import pytest

from ismail.api import OPS, OpError
from ismail.stage import rigs
from ismail.stage import world as W
from test_stage import FakePage, _get, stage  # noqa: F401  (the fixture)

HUMAN_BONES = ['Root', 'pelvis', 'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head',
               'clavicle_l', 'upperarm_l', 'lowerarm_l', 'hand_l', 'clavicle_r', 'upperarm_r', 'lowerarm_r', 'hand_r',
               'thigh_l', 'calf_l', 'foot_l', 'ball_l', 'thigh_r', 'calf_r', 'foot_r', 'ball_r']
HUMAN_PARENT = {'pelvis': 'Root', 'spine_01': 'pelvis', 'spine_02': 'spine_01', 'spine_03': 'spine_02', 'neck_01': 'spine_03',
                'head': 'neck_01', 'clavicle_l': 'spine_03', 'upperarm_l': 'clavicle_l', 'lowerarm_l': 'upperarm_l',
                'hand_l': 'lowerarm_l', 'clavicle_r': 'spine_03', 'upperarm_r': 'clavicle_r', 'lowerarm_r': 'upperarm_r',
                'hand_r': 'lowerarm_r', 'thigh_l': 'pelvis', 'calf_l': 'thigh_l', 'foot_l': 'calf_l', 'ball_l': 'foot_l',
                'thigh_r': 'pelvis', 'calf_r': 'thigh_r', 'foot_r': 'calf_r', 'ball_r': 'foot_r'}
for sd in 'lr':
    for fg in rigs.FINGERS:
        for k in (1, 2, 3):
            HUMAN_BONES.append(f'{fg}_0{k}_{sd}')
            HUMAN_PARENT[f'{fg}_0{k}_{sd}'] = f'hand_{sd}' if k == 1 else f'{fg}_0{k - 1}_{sd}'


def glb(names, parent):
    """A .glb with one skin over these bones (no meshes or buffers: enough for the profile)."""
    idx = {n: i for i, n in enumerate(names)}
    nodes = [{'name': n, 'children': [idx[c] for c in names if parent.get(c) == n]} for n in names]
    j = json.dumps({'asset': {'version': '2.0'}, 'nodes': nodes, 'skins': [{'joints': list(range(len(names)))}]}).encode()
    j += b' ' * (-len(j) % 4)
    return b'glTF' + struct.pack('<II', 2, 12 + 8 + len(j)) + struct.pack('<I', len(j)) + b'JSON' + j


DOG = ['root', 'hips', 'spine', 'neck', 'skull', 'tail_1', 'tail_2', 'tail_3', 'leg_fl', 'paw_fl']
DOG_PARENT = {'hips': 'root', 'spine': 'hips', 'neck': 'spine', 'skull': 'neck', 'tail_1': 'hips', 'tail_2': 'tail_1',
              'tail_3': 'tail_2', 'leg_fl': 'spine', 'paw_fl': 'leg_fl'}


def test_a_human_body_reads_as_a_human_rig_with_its_parts():
    bones = rigs.bones_of(glb(HUMAN_BONES, HUMAN_PARENT))
    assert ('hand_l', 'lowerarm_l') in bones and ('Root', None) in bones
    assert rigs.rig_type(b for b, _ in bones) == 'human_game_engine'
    parts = rigs.derived_parts(bones)
    assert parts['leg_l'] == ['thigh_l', 'calf_l', 'foot_l'] and parts['index_r'] == ['index_01_r', 'index_02_r', 'index_03_r']
    assert len(parts['fingers_l']) == 15


def test_any_other_rig_gets_a_part_per_branch():
    bones = rigs.bones_of(glb(DOG, DOG_PARENT))
    assert rigs.rig_type(b for b, _ in bones) == 'unknown_10_bones'
    parts = rigs.derived_parts(bones)
    assert parts == {'root': ['root', 'hips'], 'spine': ['spine'], 'neck': ['neck', 'skull'],
                     'tail_1': ['tail_1', 'tail_2', 'tail_3'], 'leg_fl': ['leg_fl', 'paw_fl']}


def test_drives_are_checked_against_the_rig():
    parts = rigs.derived_parts(rigs.bones_of(glb(HUMAN_BONES, HUMAN_PARENT)))
    assert rigs.check_drive({'part': 'leg_l', 'mode': 'effector', 'joint': 'hand_l', 'scale': None}, parts) == \
        {'part': 'leg_l', 'mode': 'effector', 'joint': 'hand_l'}
    for bad, msg in [({'part': 'tail', 'mode': 'hold'}, 'part is one of'),
                     ({'part': 'head', 'mode': 'effector', 'joint': 'hand_l'}, 'reaches'),
                     ({'part': 'leg_l', 'mode': 'effector'}, 'joint='),
                     ({'part': 'leg_l', 'mode': 'effector', 'joint': 'knee'}, 'joint='),
                     ({'part': 'index_l', 'mode': 'mimic', 'joint': ['hand_r:index-finger-tip'] * 2}, '1:1'),
                     ({'part': 'arm_r', 'mode': 'pin'}, 'at=')]:
        with pytest.raises(ValueError, match=msg):
            rigs.check_drive(bad, parts)


def _body(stage, who='body_b', names=HUMAN_BONES, parent=HUMAN_PARENT, person='person_b'):
    d = stage['scenes'] / 'room' / 'actors'
    d.mkdir(exist_ok=True)
    (d / f'{who}.glb').write_bytes(glb(names, parent))
    wf = stage['scenes'] / 'room' / 'world.json'
    w = json.loads(wf.read_text(encoding='utf-8')) if wf.is_file() else {}
    w.setdefault('actors', {})[person] = who
    wf.write_text(json.dumps(w), encoding='utf-8')
    return d


def test_reading_a_profile_changes_nothing():
    assert 'stage_actor_profile' not in __import__('ismail.api', fromlist=['MUTATING']).MUTATING
    assert 'stage_actor_map_save' in __import__('ismail.api', fromlist=['MUTATING']).MUTATING


def test_the_profile_lives_beside_the_body(stage):
    d = _body(stage)
    prof = json.loads(OPS['stage_actor_profile'](scene='room', person='person_b'))
    assert prof['rig'] == 'human_game_engine' and prof['maps'] == {} and 'leg_r' in prof['effectors']
    out = OPS['stage_actor_map_save'](scene='room', person='person_b', name='seated', pins={'hips': 'stool_3'},
                                      drives=[{'part': 'leg_l', 'mode': 'effector', 'joint': 'hand_l', 'touch': True}])
    assert "saved map 'seated' on body_b (plays person_b)" in out and 'presets now: seated' in out
    own = json.loads((d / 'body_b.json').read_text(encoding='utf-8'))
    assert own['maps']['seated']['pins'] == {'hips': 'stool_3'}
    OPS['stage_actor_map_save'](scene='room', person='person_b', name='default', drives=[{'part': 'head', 'mode': 'hold'}])
    assert (d / 'body_b.json.prev').is_file()
    got = _get(stage['port'], 'actor/profile?scene=room&who=body_b')[1]
    assert set(got['maps']) == {'seated', 'default'} and got['file'] == 'body_b.json'


def test_a_derived_scene_finds_the_bodies_in_its_assets(stage):
    _body(stage)
    (stage['scenes'] / 'attic' / 'world.json').write_text(json.dumps({'assets': 'room', 'actors': {'sam': 'body_b'}}), encoding='utf-8')
    assert json.loads(OPS['stage_actor_profile'](scene='attic', person='sam'))['actor'] == 'body_b'
    assert _get(stage['port'], 'actor/profile?scene=attic&who=body_b')[1]['rig'] == 'human_game_engine'
    with pytest.raises(urllib.error.HTTPError):
        _get(stage['port'], 'actor/profile?scene=attic&who=nobody')


def test_a_bad_map_is_not_saved(stage):
    d = _body(stage, 'dog', DOG, DOG_PARENT, person='rex')
    with pytest.raises(OpError, match='reaches'):
        OPS['stage_actor_map_save'](scene='room', person='rex', name='x', drives=[{'part': 'tail_1', 'mode': 'effector', 'joint': 'hand_r'}])
    with pytest.raises(OpError, match='pins are'):
        OPS['stage_actor_map_save'](scene='room', person='rex', name='x', pins={'tail': 'x'})
    with pytest.raises(OpError, match='pins= or drives='):
        OPS['stage_actor_map_save'](scene='room', person='rex', name='x')
    with pytest.raises(OpError, match='no body for'):
        OPS['stage_actor_profile'](scene='room', person='cat')
    assert not (d / 'dog.json').exists()
    OPS['stage_actor_map_save'](scene='room', person='rex', name='wag', drives=[
        {'part': 'tail_1', 'mode': 'mimic', 'joint': ['hand_r:index-finger-metacarpal', 'hand_r:index-finger-phalanx-proximal', 'hand_r:index-finger-tip']}])
    assert json.loads((d / 'dog.json').read_text(encoding='utf-8'))['maps']['wag']['drives'][0]['part'] == 'tail_1'


def test_control_ops_reach_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_control_set'](scene='room', person='person_b', part='leg_l', mode='effector', joint='hand_l', touch=True)
        c = page.seen[-1]
        assert (c['type'], c['part'], c['mode'], c['joint'], c['touch']) == ('control_set', 'leg_l', 'effector', 'hand_l', True)
        OPS['stage_control_set'](scene='room', person='person_b', part='arm_r', mode='hold')
        assert 'touch' not in page.seen[-1] and 'joint' not in page.seen[-1]
        OPS['stage_control_map'](scene='room', person='person_b', preset='seated', clear=True)
        assert (page.seen[-1]['type'], page.seen[-1]['preset'], page.seen[-1]['clear']) == ('control_map', 'seated', True)
        for kw, msg in [({'mode': 'fly'}, 'mode is one of'), ({'mode': 'mimic'}, 'joint='), ({'mode': 'pin'}, 'at=')]:
            with pytest.raises(OpError, match=msg):
                OPS['stage_control_set'](scene='room', person='p', part='leg_l', **kw)
        with pytest.raises(OpError, match='stage_control_set'):
            OPS['stage_cmd'](scene='room', type='control_set')
    finally:
        page.stop = True


def test_a_start_pose_is_kept_with_the_body(stage):
    d = _body(stage)
    out = OPS['stage_actor_start'](scene='room', person='person_b', pose={'take': '20261005_175000_sam', 'frame': 12})
    assert 'take 20261005_175000_sam frame 12, relative' in out
    assert json.loads(OPS['stage_actor_profile'](scene='room', person='person_b'))['start'] == {
        'pose': {'take': '20261005_175000_sam', 'frame': 12}, 'mode': 'relative', 'idle': True}
    assert 'the page shows it at its next load' in out                 # no page open: it applies at the next load
    out = OPS['stage_actor_start'](scene='room', person='person_b', pose='rest', idle=False)
    assert 'rests in it' not in out and json.loads((d / 'body_b.json').read_text(encoding='utf-8'))['start']['idle'] is False
    fr = {'head': [0, 0, 1], 'tail': [0, 0, 1.1], 'x': [1, 0, 0]}
    out = OPS['stage_actor_start'](scene='room', person='person_b', pose={'bones': {'pelvis': {'rest': fr, 'pose': fr}}}, mode='snap')
    assert 'a Blender pose of 1 bones, snap' in out
    got = _get(stage['port'], 'actor/profile?scene=room&who=body_b')[1]
    assert got['start']['pose']['bones']['pelvis']['pose']['tail'] == [0, 0, 1.1] and got['start']['mode'] == 'snap'
    for kw, msg in [({'mode': 'slow'}, "mode is 'relative'"), ({'pose': 'lean'}, "pose is 'rest'"),
                    ({'pose': {'bones': {'pelvis': {'rest': fr}}}}, 'pose needs head, tail and x'),
                    ({'pose': {'bones': {'head': {'rest': fr, 'pose': fr}}}}, 'at least the pelvis')]:
        with pytest.raises(OpError, match=msg):
            OPS['stage_actor_start'](scene='room', person='person_b', **kw)
    OPS['stage_actor_start'](scene='room', person='person_b', clear=True)
    assert 'start' not in json.loads((d / 'body_b.json').read_text(encoding='utf-8'))
    assert 'stage_actor_start' in __import__('ismail.api', fromlist=['MUTATING']).MUTATING


def test_the_pose_read_back_reaches_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_actor_pose'](scene='room', person='person_b', t=2.5)
        assert (page.seen[-1]['type'], page.seen[-1]['person'], page.seen[-1]['t']) == ('actor_pose', 'person_b', 2.5)
        with pytest.raises(OpError, match='stage_actor_pose'):
            OPS['stage_cmd'](scene='room', type='actor_pose')
    finally:
        page.stop = True


def test_a_start_pose_is_shown_now_on_an_open_page(stage):
    _body(stage)
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_actor_start'](scene='room', person='person_b', pose='rest')
        assert 'rests in it' in out and 'the page shows it now' in out
        assert (page.seen[-1]['type'], page.seen[-1]['person']) == ('actor_rest', 'person_b')
    finally:
        page.stop = True


def test_load_sets_define_unload_and_tell_an_open_page(stage):
    sc = stage['scenes']
    (sc / 'room' / 'manifest.json').write_text(json.dumps({'objects': {
        'person_couple_1_m': {}, 'person_couple_1_f': {}, 'bar_counter': {}}}), encoding='utf-8')
    OPS['stage_world'](scene='room', world={'actors': {'person_couple_1_m': 'body_c', 'player': 'player'}})
    with pytest.raises(OpError, match=r'band_\* match no node and no person'):
        OPS['stage_set_define'](scene='room', name='dancers', items=['person_couple_*', 'band_*'])
    out = OPS['stage_set_define'](scene='room', name='dancers', items=['person_couple_*'], note='six dancers')
    assert 'load set dancers in room: 2 (person_couple_1_f, person_couple_1_m)' in out
    OPS['stage_set_define'](scene='room', name='band', items=['player'])          # a person with no node yet
    with pytest.raises(OpError, match="no load set 'bar'"):
        OPS['stage_set_load'](scene='room', name='bar', loaded=False)
    out = OPS['stage_set_load'](scene='room', name='dancers', loaded=False)       # no page open: the next load
    assert out.startswith('dancers unloaded in room') and 'next load' in out
    assert W.load_world(sc, 'room')['unloaded'] == ['dancers']
    got = json.loads(OPS['stage_sets'](scene='room'))
    assert got['dancers']['unloaded'] and got['dancers']['note'] == 'six dancers' and not got['band']['unloaded']
    assert got['dancers']['matches'] == {'person_couple_*': ['person_couple_1_f', 'person_couple_1_m']}
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_set_load'](scene='room', name='dancers')
        assert out.startswith('dancers loaded in room; the page:')
        c = page.seen[-1]
        assert (c['type'], c['name'], c['loaded'], c['set']['items']) == ('load_set', 'dancers', True, ['person_couple_*'])
        assert W.load_world(sc, 'room')['unloaded'] == []
        OPS['stage_set_load'](scene='room', name='band', loaded=False)
        out = OPS['stage_set_define'](scene='room', name='band', remove=True)   # unloaded: loaded back first
        assert 'removed the load set band' in out and page.seen[-1]['loaded'] is True
        assert set(W.load_world(sc, 'room')['sets']) == {'dancers'}
    finally:
        page.stop = True
    with pytest.raises(OpError, match='not in sets'):
        OPS['stage_world'](scene='room', world={'sets': {}, 'unloaded': ['ghosts']})
    with pytest.raises(OpError, match='at least one item'):
        OPS['stage_world'](scene='room', world={'sets': {'x': {'items': []}}})


def test_music_time_and_takes_on_the_music_reach_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_music_time'](scene='room')
        assert page.seen[-1]['type'] == 'music_time'
        OPS['stage_music'](scene='room', url='scenes/room/music/song.wav', start=12.5, volume=0)
        assert (page.seen[-1]['type'], page.seen[-1]['from']) == ('music', 12.5)
        OPS['stage_actor_play'](scene='room', person='person_b', take='t1', at_music=8.0)
        assert page.seen[-1]['at_music'] == 8.0
        with pytest.raises(OpError, match='stage_music_time'):
            OPS['stage_cmd'](scene='room', type='music_time')
    finally:
        page.stop = True
