"""The gesture map: which hand shape, on which hand, means which intent, as data.

The engine's defaults ship here. A person's own rows (~/.ismail/stage/person/gestures.json) and a scene's rows (world.json
"gestures") change them; the page reads the map in force (/gestures?scene=) and takes a change live (page command
gestures_reload). Who wins: locked and fixed rows, then the person, then the scene, then the engine.

Each intent has one binding: shape (what hands.js calls the hand: pinch, thumbs_up, ...), hand (left, right, either,
or both at once), hold_ms (how long it is held before it acts). `where` is the context the engine's code reads the
intent in; a binding cannot move it, so it is not set here. Locked rows (leaving VR, the stop, the voice note) can
never be rebound or switched off, so a bad map cannot trap anyone. Fixed rows are wired to how a shape moves (the
travel click is the thumb coming down out of the gun) and are listed so a rebind can see them and not collide.
"""
import json
from pathlib import Path

# the shapes hands.js names (classify): anything else in a binding is refused
SHAPES = ('pinch', 'ok', 'pinch_middle', 'thumbs_up', 'thumbs_down', 'fist', 'open', 'gun', 'point', 'phone', 'peace',
          'rock')
HANDS = ('left', 'right', 'either', 'both')
WHERE = {
    'anywhere': 'any time the hands are up (not while performing)',
    'pointing': 'the hand ray on a thing or a panel',
    'on_thing': 'the hand at or on a thing',
    'question': 'a panel in view, or a spoken question waiting',
    'selected': 'something selected that can move',
    'updates_waiting': 'the update card over the left wrist',
    'performing': 'a performance, a take or stepping into a person (every other gesture is off)',
}
EXCLUSIVE = {frozenset(('pointing', 'on_thing'))}      # contexts that never hold at once

DEFAULTS = {
    'vr_exit': {'shape': 'thumbs_up', 'hand': 'both', 'hold_ms': 1500, 'where': 'anywhere', 'lock': 'locked',
                'means': 'leave VR without reloading (the page stays)', 'proof': 'vr_exit'},
    'stop': {'shape': 'thumbs_down', 'hand': 'both', 'hold_ms': 1500, 'where': 'performing', 'lock': 'locked',
             'means': 'stop the performance, the take or stepping into a person', 'proof': 'perform_stop'},
    'voice_note': {'shape': 'phone', 'hand': 'either', 'hold_ms': 350, 'where': 'anywhere', 'lock': 'locked',
                   'means': 'talk to the agents: a voice note while held, held 10 s more locks the call',
                   'proof': 'voice_note_start'},
    'travel': {'shape': 'gun', 'hand': 'either', 'hold_ms': 0, 'where': 'anywhere', 'lock': 'fixed',
               'means': 'aim the travel arc; the thumb coming down goes there', 'proof': 'teleport',
               'why_fixed': 'the click is the thumb coming down out of the gun'},
    'frame_shot': {'shape': 'gun', 'hand': 'both', 'hold_ms': 300, 'where': 'anywhere', 'lock': 'fixed',
                   'means': 'a snapshot of what the hands frame', 'proof': 'user_snapshot',
                   'why_fixed': 'the frame is measured from the two L shapes'},
    'select': {'shape': 'pinch', 'hand': 'either', 'hold_ms': 0, 'where': 'pointing', 'lock': 'fixed',
               'means': 'press what the ray is on (a held pinch opens its menu)', 'proof': 'select',
               'why_fixed': "the headset's own select"},
    'hold': {'shape': 'pinch', 'also': ['fist'], 'hand': 'either', 'hold_ms': 0, 'where': 'on_thing', 'lock': 'fixed',
             'means': 'hold the thing the hand is in; it follows the hand', 'proof': 'grab',
             'why_fixed': 'measured at the fingertips and the palm, not from the named shape'},
    'reload': {'shape': 'thumbs_up', 'hand': 'left', 'hold_ms': 700, 'where': 'updates_waiting',
               'means': 'reload to take a code update', 'proof': 'code_update'},
    'answer_yes': {'shape': 'thumbs_up', 'hand': 'right', 'hold_ms': None, 'where': 'question',
                   'means': "answer yes (a panel's first button)", 'proof': 'panel_answer',
                   'hold': '450 ms on a panel, 400 ms for a spoken question'},
    'answer_no': {'shape': 'thumbs_down', 'hand': 'right', 'hold_ms': None, 'where': 'question',
                  'means': "answer no (a panel's last button)", 'proof': 'panel_answer',
                  'hold': '450 ms on a panel, 400 ms for a spoken question'},
    'drop': {'shape': 'thumbs_down', 'hand': 'left', 'hold_ms': 500, 'where': 'selected',
             'means': 'drop the selected thing onto what is under it', 'proof': 'drop'},
}
LAYERS = ('person', 'scene')        # who can own a row besides the engine; the person wins over the scene


def person_file():
    from .link import registry_dir
    return registry_dir() / 'person' / 'gestures.json'     # not beside the server records: link.servers() sweeps those


def _read(f):
    try:
        d = json.loads(Path(f).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def layers(scenes, scene):
    """{'person': {intent: binding}, 'scene': {intent: binding}} as saved."""
    from .world import world_path
    person = _read(person_file()).get('rows') or {}
    w = _read(world_path(scenes, scene)) if scene else {}
    return {'person': person if isinstance(person, dict) else {}, 'scene': w.get('gestures') or {}}


def resolve(lay):
    """The map in force: {intent: row} with the owner of each row, then the collisions in it (a list of lines)."""
    rows = {}
    for intent, d in DEFAULTS.items():
        r = {**d, 'intent': intent, 'owner': 'engine'}
        if not d.get('lock'):
            for owner in LAYERS:                                   # the person first: it wins over the scene
                b = (lay.get(owner) or {}).get(intent)
                if b:
                    r.update({k: v for k, v in b.items() if k in ('shape', 'hand', 'hold_ms', 'off')}, owner=owner)
                    break
        rows[intent] = r
    return rows, collisions(rows)


def _hands_meet(a, b):
    if 'both' in (a, b):
        return a == b
    return 'either' in (a, b) or a == b


def _where_meet(a, b):
    if 'performing' in (a, b):                                     # every other gesture is off while performing
        return a == b
    return 'anywhere' in (a, b) or a == b or frozenset((a, b)) not in EXCLUSIVE


def _shapes(r):
    return {r['shape'], *r.get('also', [])}


def clash(rows, intent, r):
    """The rows a binding for `intent` would share a shape with, in a place where both are read."""
    out = []
    for other, o in rows.items():
        if other == intent or o.get('off') or r.get('off'):
            continue
        if _shapes(r) & _shapes(o) and _hands_meet(r['hand'], o['hand']) and _where_meet(r['where'], o['where']):
            out.append(other)
    return out


def collisions(rows):
    seen, out = set(), []
    for intent, r in rows.items():
        for other in clash(rows, intent, r):
            key = frozenset((intent, other))
            if key not in seen:
                seen.add(key)
                out.append(f'{intent} and {other} both use {r["hand"]} {r["shape"]} ({r["where"]} / {rows[other]["where"]})')
    return out


def describe(r):
    """'left thumbs up, held 0.7 s' for the page's cards and the agents."""
    shape = ' or '.join(x.replace('_', ' ') for x in [r['shape'], *r.get('also', [])])
    if r['hand'] == 'both':
        words = f'both {shape}' if shape.startswith('thumbs') else f'both hands {shape}'
    else:
        words = f'{"either hand" if r["hand"] == "either" else r["hand"]} {shape}'
    return words + (f', held {r["hold_ms"] / 1000:g} s' if r.get('hold_ms') else '')


def check_binding(lay, owner, intent, shape=None, hand=None, hold_ms=None, off=None):
    """The new layer for `owner` with `intent` bound, or ValueError saying why not (naming the row it collides with)."""
    if owner not in LAYERS:
        raise ValueError(f'owner is one of {", ".join(LAYERS)}')
    if intent not in DEFAULTS:
        raise ValueError(f'no intent {intent!r}; intents: {", ".join(DEFAULTS)}')
    d = DEFAULTS[intent]
    if d.get('lock') == 'locked':
        raise ValueError(f'{intent} is locked ({describe(d)}): it can never be rebound or switched off, so nobody is trapped')
    if d.get('lock') == 'fixed':
        raise ValueError(f'{intent} is fixed to {describe(d)}: {d["why_fixed"]}')
    if shape is not None and shape not in SHAPES:
        raise ValueError(f'no shape {shape!r}; shapes: {", ".join(SHAPES)}')
    if hand is not None and hand not in HANDS:
        raise ValueError(f'hand is one of {", ".join(HANDS)}')
    if hold_ms is not None and not (isinstance(hold_ms, (int, float)) and 0 <= hold_ms <= 10000):
        raise ValueError('hold_ms is 0 to 10000')
    b = dict((lay.get(owner) or {}).get(intent) or {})
    b.update({k: v for k, v in (('shape', shape), ('hand', hand), ('hold_ms', hold_ms)) if v is not None})
    if off is not None:
        if off:
            b['off'] = True
        else:
            b.pop('off', None)
    new = {**lay, owner: {**(lay.get(owner) or {}), intent: b}}
    rows, _ = resolve(new)
    hit = clash(rows, intent, rows[intent])
    if hit:
        o = rows[hit[0]]
        raise ValueError(f'{describe(rows[intent])} for {intent} collides with {hit[0]} ({describe(o)}, {o["owner"]}'
                         f'{", " + o["lock"] if o.get("lock") else ""}): both are read {WHERE[o["where"]]}')
    return new[owner]


def save_person(rows):
    f = person_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({'rows': rows}, indent=1), encoding='utf-8')
    return f


def for_page(scenes, scene):
    rows, coll = resolve(layers(scenes, scene))
    return {'rows': {k: {**r, 'words': describe(r)} for k, r in rows.items()}, 'collisions': coll}
