"""A scene's world: the scene data the stage runtime must never hard-code (scenes/<name>/world.json).

    {
      "actors":   {"person_bartender": "bf_pete"},          who plays whom: person object -> actors/<name>.glb
      "facings":  {"person_bartender": [0, -1]},            which way a person faces (Blender x, y)
      "partners": {"person_couple_1_m": "person_couple_1_f"},  who faces whom (a couple)
      "floor":    0.09,                                     the floor people stand on (Blender z, metres)
      "keep_out": [[6.4, 19.6, 6.6, 14.4, -1, 3.05]],       boxes grown trees never draw inside (x0 x1 y0 y1 z0 z1)
      "spawn":    {"position": [x, y, z], "target": [x, y, z]},   where a first visit starts (optional)
      "credits":  ["SOURCES.md#lucy"],                      asset credits for what the scene shows
      "build":    {"script": "video/rooms/blue_front_block.py", "root": "../../../..", "env": {"VR_DETAIL": "1"}},
      "derives_from": "bluefront",                          a derived scene: built from that scene's full build
      "pass":     "video/rooms/crossfade_dress.py",         ... plus this pass on the built scene (relative to root;
      "pass_env": {"CROSSFADE": "1"},                       pass_env is added to the build's env)
      "assets":   "bluefront",                              actor bodies come from that scene's actors/
      "sky":      "night"
    }

A derived scene (derives_from) has no build of its own: stage_scene_export runs its root ancestor's build script
with that build's env, the passes of every scene in the line (oldest first) in the bridge (VR_PASS), the edits of
every scene in the line merged (the variant's win), then the Quest diet (VR_DIET), and exports to the variant's
folder. So a variant can never start from a blockout by accident.

`build.script` is relative to `build.root`, which is relative to the scene folder (default "../../../..": the song
folder, for scenes in <song>/video/vr/scenes/<name>); scene_export runs the script in Blender with VR_EXPORT set to
the scene folder.
scenes/stage.json may name the default scene: {"default": "bluefront"}.
"""
import json
from pathlib import Path

DEFAULTS = {'actors': {}, 'facings': {}, 'partners': {}, 'floor': 0.0, 'keep_out': [], 'spawn': None, 'credits': [],
            'build': None, 'derives_from': None, 'pass': None, 'assets': None, 'sky': None, 'sets': {}, 'unloaded': [],
            'props': {}, 'faces': {}, 'grab': None, 'gestures': {}}
GRAB_KEYS = {'on', 'hold_s', 'release_s', 'regrab_s', 'objects', 'rules'}


def NAME_OK(name):
    import re
    return bool(re.fullmatch(r'[A-Za-z0-9_\-]{1,64}', name or '')) and not name.startswith('_')


def world_path(scenes, name):
    return Path(scenes) / name / 'world.json'


def load_world(scenes, name):
    """The scene's world.json over the defaults; a missing file is an empty world (no actors, floor 0)."""
    w = json.loads(json.dumps(DEFAULTS))
    f = world_path(scenes, name)
    if f.is_file():
        try:
            got = json.loads(f.read_text(encoding='utf-8'))
        except ValueError as e:
            raise ValueError(f'{f}: not valid JSON ({e})')
        if not isinstance(got, dict):
            raise ValueError(f'{f}: world.json must be an object')
        w.update(got)
    return w


def check_world(w):
    """Problems in a world, as short lines (empty when it is fine)."""
    out = []
    for k in ('actors', 'facings', 'partners'):
        if not isinstance(w.get(k), dict):
            out.append(f'{k} must be an object')
    for p, v in (w.get('facings') or {}).items():
        if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v)):
            out.append(f'facings.{p} must be [x, y]')
    for i, b in enumerate(w.get('keep_out') or []):
        if not (isinstance(b, list) and len(b) == 6 and b[0] < b[1] and b[2] < b[3] and b[4] < b[5]):
            out.append(f'keep_out[{i}] must be [x0, x1, y0, y1, z0, z1] with each min below its max')
    if not isinstance(w.get('floor'), (int, float)):
        out.append('floor must be a number')
    b = w.get('build')
    if b is not None and not (isinstance(b, dict) and isinstance(b.get('script'), str)):
        out.append('build must be {"script": "<path>", ...}')
    for k in ('derives_from', 'pass', 'assets', 'sky'):
        if w.get(k) is not None and not isinstance(w[k], str):
            out.append(f'{k} must be a string')
    sets = w.get('sets')
    if not isinstance(sets, dict):
        out.append('sets must be an object {name: {"items": [...], "note": ...}}')
        sets = {}
    for n, s in sets.items():
        if not NAME_OK(n):
            out.append(f'sets: {n!r} is not a name (letters, digits, _ and -)')
        if not (isinstance(s, dict) and isinstance(s.get('items'), list) and s['items']
                and all(isinstance(x, str) and x for x in s['items'])):
            out.append(f'sets.{n} must be {{"items": [node name or glob, ...]}} with at least one item')
        elif s.get('note') is not None and not isinstance(s['note'], str):
            out.append(f'sets.{n}.note must be a string')
    un = w.get('unloaded')
    if not (isinstance(un, list) and all(isinstance(x, str) for x in un)):
        out.append('unloaded must be a list of set names')
    else:
        for x in un:
            if x not in sets:
                out.append(f'unloaded names {x!r}, which is not in sets')
    props = w.get('props')
    if not isinstance(props, dict):
        out.append('props must be an object {object: {"person", "bone", "rel"}}')
        props = {}
    for n, p in props.items():
        if not (isinstance(p, dict) and isinstance(p.get('person'), str) and isinstance(p.get('bone'), str)
                and isinstance(p.get('rel'), list) and len(p['rel']) == 16
                and all(isinstance(x, (int, float)) for x in p['rel'])):
            out.append(f'props.{n} must be {{"person": name, "bone": name, "rel": [16 numbers]}}')
    faces = w.get('faces')
    if not isinstance(faces, dict):
        out.append('faces must be an object {person: [{"file", "at"}]}')
        faces = {}
    for n, lst in faces.items():
        if not (isinstance(lst, list) and all(isinstance(e, dict) and isinstance(e.get('file'), str)
                                              and isinstance(e.get('at'), (int, float)) for e in lst)):
            out.append(f'faces.{n} must be a list of {{"file": "<name>.json" in faces/, "at": song seconds}}')
    out += check_grab(w.get('grab'))
    out += check_gestures(w.get('gestures'))
    if w.get('derives_from') and b:
        out.append('a derived scene has no build of its own (it is built from derives_from); drop "build"')
    return out


def check_gestures(g):
    """Problems with world.json gestures (the scene's rows of the gesture map, gestures.py): {intent: binding}."""
    from .gestures import DEFAULTS, check_binding
    if not isinstance(g, dict):
        return ['gestures must be an object {intent: {"shape", "hand", "hold_ms", "off"}}']
    out = []
    for intent, b in g.items():
        if not isinstance(b, dict) or set(b) - {'shape', 'hand', 'hold_ms', 'off'}:
            out.append(f'gestures.{intent} must be {{"shape", "hand", "hold_ms", "off"}}')
            continue
        try:
            check_binding({'scene': {k: v for k, v in g.items() if k != intent and k in DEFAULTS}}, 'scene', intent, **b)
        except (ValueError, TypeError) as e:
            out.append(f'gestures.{intent}: {e}')
    return out


def check_grab(g):
    """Problems with world.json grab (grab by touch, page/grab.js): {on, hold_s, release_s, regrab_s, objects, rules}."""
    if g is None:
        return []
    if not isinstance(g, dict):
        return ['grab must be an object {"on", "hold_s", "release_s", "objects", "rules"}']
    out = [f'grab has an unknown key {k!r} (keys: {", ".join(sorted(GRAB_KEYS))})' for k in g if k not in GRAB_KEYS]
    if 'on' in g and not isinstance(g['on'], bool):
        out.append('grab.on must be true or false')
    for k, lo, hi in (('hold_s', 0.2, 10), ('release_s', 0.1, 10), ('regrab_s', 0, 5)):
        v = g.get(k)
        if k in g and not (isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi):
            out.append(f'grab.{k} must be seconds from {lo} to {hi}')
    o = g.get('objects', 'unlocked')
    if not (o == 'unlocked' or (isinstance(o, list) and all(isinstance(x, str) and x for x in o))):
        out.append('grab.objects must be "unlocked" (every small thing not locked) or a list of names (globs: drum_stick_*)')
    rules = g.get('rules', {})
    if not isinstance(rules, dict):
        out.append('grab.rules must be an object {object: {"grab": bool, "fixed": bool}}')
        rules = {}
    for n, r in rules.items():
        if not (isinstance(r, dict) and set(r) <= {'grab', 'fixed'} and all(isinstance(v, bool) for v in r.values())):
            out.append(f'grab.rules.{n} must be {{"grab": true|false, "fixed": true|false}}')
    return out


def save_world(scenes, name, w):
    """Write world.json (the previous one kept in the scene's history/)."""
    probs = check_world({**DEFAULTS, **w})
    if probs:
        raise ValueError('world.json: ' + '; '.join(probs))
    f = world_path(scenes, name)
    if f.is_file():
        import time
        h = f.parent / 'history'
        h.mkdir(exist_ok=True)
        f.replace(h / time.strftime('world_%Y%m%d_%H%M%S.json'))
    f.write_text(json.dumps(w, indent=1), encoding='utf-8')
    return f


def default_scene(scenes):
    """scenes/stage.json "default", else the first scene (alphabetical), else ''."""
    scenes = Path(scenes)
    try:
        d = json.loads((scenes / 'stage.json').read_text(encoding='utf-8')).get('default')
        if d and (scenes / d / 'scene.glb').is_file():
            return d
    except (OSError, ValueError, AttributeError):
        pass
    names = sorted(p.name for p in scenes.iterdir() if (p / 'scene.glb').is_file()) if scenes.is_dir() else []
    return names[0] if names else ''


def lineage(scenes, name):
    """The scene and its ancestors, the scene first: [name, parent, grandparent, ...]. Raises on a loop or a parent
    that does not exist."""
    line = [name]
    while True:
        parent = load_world(scenes, line[-1]).get('derives_from')
        if not parent:
            return line
        if parent in line:
            raise ValueError(f'world.json derives_from makes a loop: {" -> ".join(line + [parent])}')
        if not (Path(scenes) / parent).is_dir():
            raise ValueError(f'{line[-1]} derives from {parent!r}, which is not a scene in {scenes}')
        line.append(parent)


def build_of(scenes, name):
    """How to export a scene: {script, cwd, env, passes, edits}, or None when nothing in its line has a build. A
    derived scene uses its root ancestor's build and env, its line's passes (oldest first; relative to that
    build's root) and every edits.json in the line (the variant's last, so they win)."""
    line = lineage(scenes, name)
    top = line[-1]
    w0 = load_world(scenes, top)
    b = w0.get('build')
    if not b:
        return None
    root = (Path(scenes) / top / b.get('root', '../../../..')).resolve()
    script = (root / b['script']).resolve()
    env = {str(k): str(v) for k, v in (b.get('env') or {}).items()}
    passes = []
    for s in reversed(line[:-1]):
        w = load_world(scenes, s)
        env.update({str(k): str(v) for k, v in ((w.get('pass_env') or {}).items())})
        if w.get('pass'):
            passes.append((root / w['pass']).resolve())
    edits = [Path(scenes) / s / 'edits.json' for s in reversed(line) if (Path(scenes) / s / 'edits.json').is_file()]
    return {'script': script, 'cwd': script.parent, 'env': env, 'passes': passes, 'edits': edits, 'line': line}


def merged_edits(files):
    """edits.json files merged in order (later files win per object, light and material)."""
    out = {'objects': {}, 'lights': {}, 'materials': {}}
    for f in files:
        try:
            e = json.loads(Path(f).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for k in out:
            out[k].update(e.get(k) or {})
    return out


# the pieces a new scene takes from the one it derives from: the page's own files (the room itself comes from the
# export); takes, voice notes, snapshots and history are the person's and stay with their scene
COPY_FROM_PARENT = ('trees', 'names.json', 'cues.json', 'waypoints.json')
