"""Voice library: `code` instruments kept as Python modules instead of code pasted into project.json.

A voice is a module `<name>.py` defining `voice(freq, t, vel, gate, sr)` -> mono (n,) or stereo (2, n) array
(t: seconds from note start, covering gate + tail; vel 0..1; gate: held seconds). It may define more functions
(a track picks one with "fn") and may accept keyword arguments: `bpm` is passed when the function takes it, and
the track's "params" dict is passed as keywords. A module-level INFO dict documents it for voices_list/voice_help:
{"summary": ..., "range": ..., "velocity": ..., "functions": {...}, "params": {...}}.

A track uses one with {"type": "code", "voice": "<name>", "fn": "voice", "params": {}, "tail": 1.0}.
Search order: <project>/voices/, then each folder of the voices setting (config_get('voices')), then these
built-ins; each with its family subfolders (strings/, keys/, bass/, fx/ ...). Names are flat: a track says
"voice": "grand_piano" wherever the file sits. Mimic profiles (<name>.mimic.json) are found the same way. A song voice with a built-in's name overrides it; it can also extend one
(`from ismail.voices.growl import *`). Data files sit next to the module and are found through __file__.
"""
import hashlib
import importlib
import importlib.util
import inspect
import os
import sys
from .. import settings

BUILTIN = os.path.dirname(os.path.abspath(__file__))
_LOADED = {}


class VoiceError(ValueError):
    pass


def _tree(d):
    """d and its family subfolders (strings/, keys/ ...), depth first, sorted; skips caches and private folders."""
    out = [d]
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            sub = os.path.join(d, f)
            if os.path.isdir(sub) and not f.startswith(('_', '.')):
                out += _tree(sub)
    return out


def search_path(root=None):
    """[(origin, dir)]: the song's voices/, each folder of the voices setting, then the built-ins, each with its family
    subfolders. origin reads 'song', 'env' or 'built-in', plus '/<family>' for a subfolder."""
    tops = []
    if root:
        tops.append(('song', os.path.join(root, 'voices')))
    for d in settings.get('voices') or []:
        tops.append(('env', d))
    tops.append(('built-in', BUILTIN))
    dirs = []
    for origin, top in tops:
        for d in _tree(top):
            rel = os.path.relpath(d, top).replace(os.sep, '/')
            dirs.append((origin if rel == '.' else f'{origin}/{rel}', d))
    return dirs


def _names(d):
    if not os.path.isdir(d):
        return []
    return sorted(f[:-3] for f in os.listdir(d) if f.endswith('.py') and not f.startswith('_'))


def find(name, root=None):
    for origin, d in search_path(root):
        p = os.path.join(d, name + '.py')
        if os.path.isfile(p):
            return origin, p
    have = sorted({n for _, d in search_path(root) for n in _names(d)})
    raise VoiceError(f"no voice {name!r}; available: {', '.join(have) or 'none'} (voices_list shows them; a song "
                     f"voice goes in <project>/voices/{name}.py, or a family subfolder like voices/strings/)")


def load(name, root=None):
    origin, path = find(name, root)
    if origin.startswith('built-in'):
        rel = os.path.relpath(path, BUILTIN)[:-3].replace(os.sep, '.')
        return importlib.import_module(f"{__name__}.{rel}")
    key = (path, os.path.getmtime(path))
    if key not in _LOADED:
        mod_name = f"ismail_voice_{name}_{hashlib.sha1(path.encode()).hexdigest()[:8]}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        _LOADED[key] = mod
    return _LOADED[key]


def function(name, fn='voice', root=None):
    mod = load(name, root)
    f = getattr(mod, fn or 'voice', None)
    if not callable(f):
        fns = [k for k, v in vars(mod).items() if callable(v) and not k.startswith('_') and
               getattr(v, '__module__', None) == mod.__name__]
        raise VoiceError(f"voice {name!r} has no function {fn!r}; it has: {', '.join(fns)}")
    return f


def call(f, freq, t, vel, gate, sr, bpm, params):
    """Call a voice function, passing bpm and params only to functions that accept them."""
    sig = inspect.signature(f)
    takes_any = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    kw = {}
    if 'bpm' in sig.parameters or takes_any:
        kw['bpm'] = bpm
    for k, v in (params or {}).items():
        if k not in sig.parameters and not takes_any:
            raise VoiceError(f"voice function {f.__name__} takes no parameter {k!r}; it takes "
                             f"{[p for p in sig.parameters if p not in ('freq', 't', 'vel', 'gate', 'sr')]}")
        kw[k] = v
    return f(freq, t, vel, gate, sr, **kw)


def fingerprint(inst, root=None):
    """Hash of every voice file (and its sibling data files) an instrument uses, for the render cache."""
    names = set()
    profiles = set()

    def walk(i):
        if isinstance(i, dict):
            if i.get('type') == 'code' and i.get('voice'):
                names.add(i['voice'])
            if i.get('type') == 'mimic' and i.get('profile'):
                profiles.add(i['profile'])
            for v in (i.get('map') or {}).values():
                walk(v)
    walk(inst)
    h = hashlib.sha1()
    for n in sorted(names):
        try:
            _, p = find(n, root)
        except VoiceError:
            h.update(n.encode())
            continue
        d, base = os.path.dirname(p), n
        for f in sorted(os.listdir(d)):
            if f == base + '.py' or (f.startswith(base + '.') or f.startswith(base + '_')) and not f.endswith('.pyc'):
                fp = os.path.join(d, f)
                if os.path.isfile(fp):
                    with open(fp, 'rb') as fh:
                        h.update(fh.read())
    for n in sorted(profiles):
        for _, d in search_path(root):
            fp = os.path.join(d, n + '.mimic.json')
            if os.path.isfile(fp):
                with open(fp, 'rb') as fh:
                    h.update(fh.read())
                break
        else:
            h.update(n.encode())
    return h.hexdigest()[:12] if names or profiles else ''


def info(name, root=None):
    origin, path = find(name, root)
    mod = load(name, root)
    meta = dict(getattr(mod, 'INFO', {}) or {})
    doc = (mod.__doc__ or '').strip()
    if 'summary' not in meta:
        meta['summary'] = doc.split('\n')[0] if doc else '(no description)'
    return origin, path, meta, doc


def entry_fns(name, root=None):
    """The functions a track can call on a voice, the one to name first: 'perform' (a voice that plays whole parts:
    kits, guitars, sampled sets) or 'voice' (one note at a time), then any other voice_* variants."""
    mod = load(name, root)
    fns = [f for f in ('perform', 'voice') if callable(getattr(mod, f, None))]
    return fns + sorted(f for f in dir(mod) if f.startswith('voice_') and callable(getattr(mod, f)))


def available(root=None):
    """[(name, origin, summary)] with song and env voices shadowing built-ins of the same name."""
    seen, out = set(), []
    for origin, d in search_path(root):
        for n in _names(d):
            if n in seen:
                continue
            seen.add(n)
            try:
                _, _, meta, _ = info(n, root)
                out.append((n, origin, meta.get('summary', '')))
            except Exception as e:  # a broken song voice should not hide the others
                out.append((n, origin, f"(fails to load: {e})"))
    return out


def mimic_profiles(root=None):
    """[(name, origin, summary)] for every <name>.mimic.json on the search path (song first)."""
    import json
    seen, out = set(), []
    for origin, d in search_path(root):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith('.mimic.json') or f[:-11] in seen:
                continue
            seen.add(f[:-11])
            try:
                p = json.load(open(os.path.join(d, f), encoding='utf-8'))
                ms = [n['midi'] for n in p['notes']]
                lo, hi = (_note_name(min(ms)), _note_name(max(ms)))
                out.append((f[:-11], origin, f"mimic, {p['kind']}, {len(ms)} notes measured {lo}-{hi}"
                                            + (f", from {p['source']}" if p.get('source') else '')))
            except Exception as e:
                out.append((f[:-11], origin, f"(unreadable mimic profile: {e})"))
    return out


def _note_name(m):
    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
    m = int(round(m))
    return f"{names[m % 12]}{m // 12 - 1}"


class _FamilyAlias:
    """`import ismail.voices.growl` keeps working after the built-ins moved into family folders
    (ismail/voices/bass/growl.py): the old flat name resolves to the same module object."""

    def find_spec(self, fullname, path=None, target=None):
        parts = fullname.split('.')
        if len(parts) != 3 or parts[:2] != [__name__.split('.')[0], 'voices'] or parts[2].startswith('_'):
            return None
        for d in _tree(BUILTIN)[1:]:
            if os.path.isfile(os.path.join(d, parts[2] + '.py')):
                real = __name__ + '.' + os.path.relpath(os.path.join(d, parts[2]), BUILTIN).replace(os.sep, '.')
                return importlib.util.spec_from_loader(fullname, _AliasLoader(real))
        return None


class _AliasLoader:
    def __init__(self, real):
        self.real = real

    def create_module(self, spec):
        return importlib.import_module(self.real)

    def exec_module(self, module):
        pass


if not any(isinstance(f, _FamilyAlias) for f in sys.meta_path):
    sys.meta_path.append(_FamilyAlias())
