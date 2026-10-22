"""The visual page's scene: layers the agent sets with raw numbers, checked here so the page only draws.

Positions and sizes are fractions of the frame (0-1). Bands are indices into the 16 log-spaced bands, 40 Hz to
16 kHz (0-3 sub and bass, 4-7 low mids, 8-11 mids and presence, 12-15 air). Colours are '#rrggbb'. `drive` picks
what moves a layer: 'level' (the whole mix), 'band:<i>' or 'bands:<lo>-<hi>' (their mean), 'beat' (a pulse on
every beat, decaying), or 'track:<name>' (a flash on each of that track's notes)."""
import re

COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
DRIVE = re.compile(r'^(level|beat|band:\d{1,2}|bands:\d{1,2}-\d{1,2}|track:[\w .:-]{1,40})$')

COMMON = {'kind': str, 'x': float, 'y': float, 'w': float, 'h': float, 'color': str, 'colors': list,
          'opacity': float, 'gain': float, 'smooth': float, 'drive': str, 'blend': str}
KINDS = {
    'bars': {'bands': list, 'gap': float, 'mirror': bool, 'radius': float},
    'ring': {'bands': list, 'r': float, 'thickness': float, 'spin': float},
    'pulse': {'r0': float, 'r1': float, 'decay': float, 'fill': bool},
    'line': {'bands': list, 'thickness': float, 'fill': bool},
    'hits': {'tracks': dict, 'shape': str, 'decay': float},
    'particles': {'count': int, 'speed': float, 'size': float, 'spawn': str},
    'text': {'text': str, 'size': float, 'font': str, 'align': str},
    'grid': {'cols': int, 'rows': int, 'thickness': float, 'scroll': float},
}
BLENDS = ('source-over', 'lighter', 'screen', 'multiply', 'overlay')
SHAPES = ('circle', 'square', 'bar')
ALIGN = ('left', 'center', 'right')
DEFAULT = {'background': ['#07080c', '#141a2a'], 'trails': 0.0, 'layers': [
    {'kind': 'bars', 'x': 0.05, 'y': 0.3, 'w': 0.9, 'h': 0.5, 'bands': [0, 15], 'colors': ['#5ec8ff', '#ff6ec7'],
     'gap': 0.25, 'mirror': True, 'smooth': 0.6, 'gain': 1.0},
    {'kind': 'pulse', 'x': 0.5, 'y': 0.55, 'r0': 0.05, 'r1': 0.2, 'drive': 'beat', 'color': '#ffffff',
     'opacity': 0.25, 'decay': 0.25, 'blend': 'lighter'},
]}


def _num(v, name, lo, hi):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= float(v) <= hi:
        raise ValueError(f'{name}: a number from {lo} to {hi}')


def check_layer(i, d):
    if not isinstance(d, dict) or d.get('kind') not in KINDS:
        raise ValueError(f"layer {i}: kind is one of {', '.join(KINDS)}")
    allowed = {**COMMON, **KINDS[d['kind']]}
    bad = sorted(set(d) - set(allowed))
    if bad:
        raise ValueError(f"layer {i} ({d['kind']}): unknown {bad}; it takes {sorted(allowed)}")
    for k, v in d.items():
        t = allowed[k]
        if t is float:
            lo, hi = {'gain': (0, 20), 'spin': (-720, 720), 'scroll': (-5, 5), 'size': (0, 0.5),
                      'speed': (0, 5)}.get(k, (0, 1))
            if k in ('x', 'y'):
                lo, hi = -0.5, 1.5
            _num(v, f'layer {i} {k}', lo, hi)
        elif t is int:
            _num(v, f'layer {i} {k}', 1, {'count': 400, 'cols': 64, 'rows': 64}.get(k, 100))
        elif t is bool and not isinstance(v, bool):
            raise ValueError(f'layer {i} {k}: true or false')
        elif t is str and not isinstance(v, str):
            raise ValueError(f'layer {i} {k}: a string')
    for c in ([d['color']] if 'color' in d else []) + list(d.get('colors') or []):
        if not isinstance(c, str) or not COLOR.match(c):
            raise ValueError(f"layer {i}: colour {c!r}: '#rrggbb'")
    if 'colors' in d and not 1 <= len(d['colors']) <= 6:
        raise ValueError(f'layer {i} colors: 1 to 6 colours (a gradient)')
    if 'bands' in d:
        b = d['bands']
        if not (isinstance(b, list) and len(b) == 2 and all(isinstance(x, int) and 0 <= x <= 15 for x in b)
                and b[0] <= b[1]):
            raise ValueError(f'layer {i} bands: [lo, hi], band indices 0-15, lo <= hi')
    if 'drive' in d and not DRIVE.match(d['drive']):
        raise ValueError(f"layer {i} drive: 'level', 'beat', 'band:<0-15>', 'bands:<lo>-<hi>' or 'track:<name>'")
    if 'spawn' in d and not DRIVE.match(d['spawn']):
        raise ValueError(f"layer {i} spawn: what makes particles: 'beat', 'level', 'band:<i>' or 'track:<name>'")
    if d.get('blend', 'source-over') not in BLENDS:
        raise ValueError(f"layer {i} blend: one of {', '.join(BLENDS)}")
    if d.get('shape', 'circle') not in SHAPES:
        raise ValueError(f"layer {i} shape: one of {', '.join(SHAPES)}")
    if d.get('align', 'left') not in ALIGN:
        raise ValueError(f"layer {i} align: one of {', '.join(ALIGN)}")
    if 'tracks' in d:
        t = d['tracks']
        if not isinstance(t, dict) or not 1 <= len(t) <= 32 or not all(
                isinstance(k, str) and isinstance(c, str) and COLOR.match(c) for k, c in t.items()):
            raise ValueError(f"layer {i} tracks: {{'<track name>': '#rrggbb', ...}}, 1-32 tracks")
    if d['kind'] == 'text' and len(d.get('text', '')) > 200:
        raise ValueError(f'layer {i} text: at most 200 characters')
    return d


def check_scene(scene):
    bg = scene.get('background', DEFAULT['background'])
    bgs = [bg] if isinstance(bg, str) else bg
    if not isinstance(bgs, list) or not 1 <= len(bgs) <= 3 or not all(isinstance(c, str) and COLOR.match(c)
                                                                       for c in bgs):
        raise ValueError("background: '#rrggbb', or a gradient of 2-3 colours top to bottom")
    _num(scene.get('trails', 0.0), 'trails', 0, 0.98)
    layers = scene.get('layers', [])
    if not isinstance(layers, list) or len(layers) > 24:
        raise ValueError('layers: a list of at most 24')
    return {'background': bgs, 'trails': float(scene.get('trails', 0.0)),
            'layers': [check_layer(i, dict(d)) for i, d in enumerate(layers)]}
