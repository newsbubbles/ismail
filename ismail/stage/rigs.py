"""Actors' rigs and profiles (the user, 2026-10-04: maps and anchors live "with the actual object itself so that it
could go to other scenes ... a library that is global for each type of ... named subgraph of armature").

An actor's profile sits beside its body: scenes/<scene>/actors/<who>.json next to <who>.glb, so it travels with it.

  {"rig": "human_game_engine",                         (read from the glb: the bone names say what kind of rig)
   "parts": {"arm_l": ["upperarm_l", "lowerarm_l", "hand_l"], ...},   (named bone subgraphs; derived, extendable)
   "maps": {"seated": {"pins": {"hips": "stool_3"}, "drives": [...]}, ...}}   (control maps by situation)

A part is a chain of bones, parents first; a part with an `effector` (its last bone) can be reached for (two-bone IK
for arms and legs); every part can hold, mimic a joint's turn, or follow its default. The page reads the profile
through GET /actor/profile, which fills in the rig and the derived parts for a body that has no file yet.
"""
import json
import struct

HUMAN = {'pelvis', 'spine_01', 'spine_03', 'neck_01', 'head', 'upperarm_l', 'lowerarm_l', 'hand_l', 'upperarm_r',
         'lowerarm_r', 'hand_r', 'thigh_l', 'calf_l', 'foot_l', 'thigh_r', 'calf_r', 'foot_r'}
FINGERS = ('thumb', 'index', 'middle', 'ring', 'pinky')
# the human parts (MakeHuman's game_engine rig, mhx rig "game_engine"): name -> bones, parents first; effector parts
# are the two-bone chains the page can reach with
HUMAN_PARTS = {
    'head': ['neck_01', 'head'],
    'spine': ['spine_01', 'spine_02', 'spine_03'],
    'arm_l': ['upperarm_l', 'lowerarm_l', 'hand_l'], 'arm_r': ['upperarm_r', 'lowerarm_r', 'hand_r'],
    'leg_l': ['thigh_l', 'calf_l', 'foot_l'], 'leg_r': ['thigh_r', 'calf_r', 'foot_r'],
    **{f'{fg}_{sd}': [f'{fg}_0{k}_{sd}' for k in (1, 2, 3)] for fg in FINGERS for sd in 'lr'},
    'fingers_l': [f'{fg}_0{k}_l' for fg in FINGERS for k in (1, 2, 3)],
    'fingers_r': [f'{fg}_0{k}_r' for fg in FINGERS for k in (1, 2, 3)],
}
EFFECTOR_PARTS = {'arm_l', 'arm_r', 'leg_l', 'leg_r'}


def bones_of(glb_bytes):
    """[(name, parent name or None)] of the first skin's joints, from a .glb's JSON chunk."""
    if glb_bytes[:4] != b'glTF':
        raise ValueError('not a .glb')
    n = struct.unpack_from('<I', glb_bytes, 12)[0]
    j = json.loads(glb_bytes[20:20 + n])
    if not j.get('skins'):
        return []
    nodes = j['nodes']
    parent = {c: i for i, nd in enumerate(nodes) for c in nd.get('children', [])}
    joints = j['skins'][0]['joints']
    js = set(joints)
    name = lambda i: nodes[i].get('name', f'node_{i}')
    return [(name(i), name(parent[i]) if parent.get(i) in js else None) for i in joints]


def rig_type(names):
    names = set(names)
    if HUMAN <= names:
        return 'human_game_engine'
    return f'unknown_{len(names)}_bones'


def derived_parts(bones):
    """Named parts for a rig: the human table where the bones exist; for another rig, each unbranched chain of bones
    (a tail, a leg, a neck: it starts below a fork and runs until the next one), parents first and named after its
    first bone, so any armature has parts to map."""
    names = [b for b, _ in bones]
    have = set(names)
    if rig_type(names).startswith('human'):
        return {k: v for k, v in HUMAN_PARTS.items() if all(b in have for b in v)}
    kids = {}
    for b, p in bones:
        kids.setdefault(p, []).append(b)
    parts = {}
    for b, p in bones:
        if p is not None and len(kids.get(p, [])) == 1:
            continue                                   # inside a chain: its start names it
        chain = [b]
        while len(kids.get(chain[-1], [])) == 1:
            chain.append(kids[chain[-1]][0])
        parts[b] = chain
    return parts


def profile(actors_dir, who):
    """The actor's profile: its file (if any) over what its body says (rig, parts). Raises FileNotFoundError when
    there is no body."""
    glb = actors_dir / f'{who}.glb'
    bones = bones_of(glb.read_bytes())
    out = {'actor': who, 'rig': rig_type(b for b, _ in bones), 'bones': len(bones), 'parts': derived_parts(bones),
           'effectors': sorted(EFFECTOR_PARTS & set(derived_parts(bones))), 'maps': {}}
    f = actors_dir / f'{who}.json'
    if f.is_file():
        own = json.loads(f.read_text(encoding='utf-8'))
        out['parts'] = {**out['parts'], **own.get('parts', {})}
        out['maps'] = own.get('maps', {})
        if own.get('start'):
            out['start'] = own['start']
        out['file'] = f.name
    return out


MODES = ('default', 'hold', 'effector', 'pin', 'mimic')
SOURCES = ('head', 'hand_l', 'hand_r')


def check_drive(d, parts):
    """A drive {part, mode, joint, at, scale, touch}: what it asks is possible on this rig. Raises ValueError."""
    if not isinstance(d, dict) or d.get('part') not in parts:
        raise ValueError(f"drive {d!r}: part is one of {sorted(parts)}")
    mode = d.get('mode', 'default')
    if mode not in MODES:
        raise ValueError(f"drive for {d['part']}: mode is one of {MODES}")
    if mode in ('effector', 'pin') and d['part'] not in EFFECTOR_PARTS:
        raise ValueError(f"{mode} needs a part that reaches ({', '.join(sorted(EFFECTOR_PARTS))}); {d['part']} can hold, mimic or default")
    if mode in ('effector', 'mimic'):
        js = d.get('joint')
        for j in js if isinstance(js, list) else [js]:
            if not isinstance(j, str) or j.split(':')[0] not in SOURCES:
                raise ValueError(f"{mode} needs joint= one of {SOURCES}, or 'hand_l:<webxr joint>' (e.g. hand_r:index-finger-tip)"
                                 + ("; a list of them maps 1:1 onto the part's bones" if mode == 'mimic' else ''))
        if isinstance(js, list) and len(js) != len(parts[d['part']]):
            raise ValueError(f"mimic with a list of joints maps 1:1: {d['part']} has {len(parts[d['part']])} bones, got {len(js)} joints")
    if mode == 'pin' and d.get('at') is None:
        raise ValueError("pin needs at= an object name, [x, y, z] in Blender metres, or 'here'")
    return {k: v for k, v in d.items() if v is not None}


def check_start(pose, mode, idle=True, hands='user'):
    """A start pose: 'rest', {take, frame}, or {bones: {name: {rest: {head, tail, x}, pose: {head, tail, x}}}} (Blender
    metres; rest in armature space, pose in world). hands: 'user' (the user's own hands from GO) or 'pose' (the start
    pose's grip, the user's motion added to it). Raises ValueError."""
    if mode not in ('relative', 'snap'):
        raise ValueError("mode is 'relative' (hold the start pose, the user's motion as changes from GO) or 'snap'")
    if hands not in ('user', 'pose'):
        raise ValueError("hands is 'user' (the user's own hands from GO) or 'pose' (the start pose's grip plus the user's motion)")
    out = _check_start_pose(pose, mode, idle)
    if hands == 'pose':
        out['hands'] = 'pose'
    return out


def _check_start_pose(pose, mode, idle):
    if pose == 'rest':
        return {'pose': 'rest', 'mode': mode, 'idle': bool(idle)}
    if isinstance(pose, dict) and pose.get('take'):
        return {'pose': {'take': str(pose['take']), 'frame': int(pose.get('frame') or 0),
                         **({'scene': pose['scene']} if pose.get('scene') else {})}, 'mode': mode, 'idle': bool(idle)}
    if isinstance(pose, dict) and isinstance(pose.get('bones'), dict):
        def vec(v):
            return isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)
        for name, e in pose['bones'].items():
            for side in ('rest', 'pose'):
                fr = (e or {}).get(side) or {}
                if not all(vec(fr.get(k)) for k in ('head', 'tail', 'x')):
                    raise ValueError(f"bone {name}: {side} needs head, tail and x, each [x, y, z] in Blender metres")
        if 'pelvis' not in pose['bones']:
            raise ValueError('a Blender pose needs at least the pelvis (it places the body)')
        return {'pose': {'bones': pose['bones']}, 'mode': mode, 'idle': bool(idle)}
    raise ValueError("pose is 'rest', {'take': id, 'frame': n}, or {'bones': {...}} exported from Blender")


def save_start(actors_dir, who, start):
    """Store the start pose in the actor's profile (the previous file kept as <who>.json.prev); None removes it."""
    f = actors_dir / f'{who}.json'
    own = json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {}
    if f.is_file():
        (actors_dir / f'{who}.json.prev').write_text(f.read_text(encoding='utf-8'), encoding='utf-8')
    if start is None:
        own.pop('start', None)
    else:
        own['start'] = start
    f.write_text(json.dumps(own, indent=1), encoding='utf-8')
    return f


def save_map(actors_dir, who, name, m):
    """Store a control map preset in the actor's profile file (the previous file kept as <who>.json.prev)."""
    f = actors_dir / f'{who}.json'
    own = json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {}
    if f.is_file():
        (actors_dir / f'{who}.json.prev').write_text(f.read_text(encoding='utf-8'), encoding='utf-8')
    own.setdefault('maps', {})[name] = m
    f.write_text(json.dumps(own, indent=1), encoding='utf-8')
    return f
