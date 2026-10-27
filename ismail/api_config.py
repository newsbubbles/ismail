"""config_*: ismail's settings, seen and changed through ops (ledger:M368, Nate 10-27: "make sure to validate we've
got everything and what it affects ... so that agent usage is only enhanced and they also know how to use it").

The registry is ismail/settings.py: every setting's key, type, default, what it does and when a change applies.
config_get reads them with where each value comes from, config_set validates and writes one to the settings file
(~/.ismail/config.json), config_unset returns one to its default. An old environment variable of a setting, when
set, still wins over the file: the replies say so, since a file value cannot show until it is unset.
"""
import os

from .api import OpError, op
from . import settings as S

DETAIL_MAX = 12      # config_get with no key or group: one line per setting; a group: details up to this many


def _err(e):
    return OpError(str(e).strip('"\''))


def _source_text(s, src):
    if src == 'env':
        return (f"from {s.env}, set in this process's environment, which wins over the settings file "
                f"(config_set writes the file, and the file value shows once {s.env} is unset)")
    if src == 'file':
        return f'from the settings file ({S.path()})'
    return 'the default'


def _line(s):
    v, src, problems = S.resolve(s.key)
    shown = S.show(v) if v not in (None, []) or src != 'default' or not s.default_doc else f'(default: {s.default_doc})'
    tag = {'env': f'  [set by {s.env}]', 'file': '  [settings file]', 'default': ''}[src]
    return f"  {s.key} = {shown}{tag}" + ('  (!)' if problems else '')


def _detail(s):
    v, src, problems = S.resolve(s.key)
    L = [f"{s.key} = {S.show(v)}",
         f"  what: {s.doc}",
         f"  where it comes from: {_source_text(s, src)}",
         f"  takes: {S.what_it_takes(s)}; default: {S.default_text(s)}",
         f"  a change applies: {S.applies(s)}",
         f"  old name: {s.env} (still works, and wins over the file when set)"]
    L += [f"  ignored: {p}" for p in problems]
    return '\n'.join(L)


@op()
def config_get(key: str = None, group: str = None) -> str:
    """ismail's settings: where songs live, the ffmpeg, Blender and browser programs, the live master's levels, the
    phone, stage and listening folders, the MCP server's tool list. No arguments: every setting, one line each, by
    group. key ('songs', 'ffmpeg', 'live.trim_db'): that one in full: its value, where the value comes from (the
    settings file, its old environment variable, or the default), what it takes, and whether a change applies at once
    or after a restart. group ('paths', 'tools', 'live', ...): that group in full. Reach for it when you need to know
    where songs are saved, which ffmpeg or Blender ismail uses, or why a folder is where it is. config_set changes
    one, config_unset returns one to its default."""
    data, err = S.load()
    head = [f"settings file: {S.path()}" + ('' if os.path.exists(S.path()) else ' (none yet: every value is a default '
                                                                             'or an old variable)')]
    if err:
        head.append(f"WARNING: the settings file is ignored: {err}. Fix it by hand; config_set will not overwrite it")
    unknown = sorted(k for k in data if k not in S.REGISTRY)
    if unknown:
        head.append(f"note: the file has keys ismail does not know, ignored: {', '.join(unknown)}")
    if key is not None:
        try:
            s = S.setting(key)
        except KeyError as e:
            raise _err(e)
        return '\n'.join(head + [_detail(s)])
    if group is not None:
        if group not in S.GROUPS:
            raise OpError(f"unknown group {group!r}; groups: {', '.join(S.GROUPS)}. config_get() lists every setting")
        rows = [s for s in S.SETTINGS if s.group == group]
        if len(rows) > DETAIL_MAX:
            return '\n'.join(head + [f"[{group}]"] + [_line(s) for s in rows] +
                             ["config_get(key) shows one in full"])
        return '\n'.join(head + [_detail(s) for s in rows])
    L = head + ['(a value marked [set by ...] comes from an old variable and wins over the file; (!) marks a value '
                'that was set but is ignored: config_get(key) says why)']
    for g in S.GROUPS:
        L.append(f"[{g}]")
        L += [_line(s) for s in S.SETTINGS if s.group == g]
    L.append("config_get(key) shows one in full; config_set(key, value) changes one; config_unset(key) returns it "
             "to its default")
    return '\n'.join(L)


@op(mutates=True)
def config_set(key: str, value) -> str:
    """Change one of ismail's settings: the value is checked against the setting's type (a path, a number in its
    range, true or false, a list) and written to the settings file (~/.ismail/config.json). The reply says the old
    and new value, whether it applies at once or after a restart, and when an old environment variable still wins.
    Reach for it when the person wants their songs in another folder, ismail pointed at an ffmpeg, Blender or
    browser it did not find, or the live master's trim or cap changed for every set. key: as config_get lists them
    ('songs', 'ffmpeg', 'live.trim_db'). value: the new value ('D:/music', -3, true, ['dj', 'mc']). config_get(key)
    reads it back; config_unset(key) returns it to the default."""
    try:
        s = S.setting(key)
    except KeyError as e:
        raise _err(e)
    old, old_src, _ = S.resolve(key)
    try:
        v = S.put(key, value)
    except ValueError as e:
        raise OpError(f"{e}. Nothing was changed. Example: config_set({key!r}, {_example(s)})")
    L = [f"{key} = {S.show(v)} in the settings file {S.path()} (was {S.show(old)}, "
         f"{'the default' if old_src == 'default' else 'from ' + ('the file' if old_src == 'file' else s.env)})"]
    if s.type == 'path' and not os.path.exists(v):
        L.append(f"note: {v} does not exist yet: make it, or check the spelling")
    if s.type == 'paths':
        missing = [p for p in v if not os.path.isdir(p)]
        if missing:
            L.append(f"note: not a folder yet: {', '.join(missing)}")
    if S.source(key) == 'env':
        L.append(f"but {s.env} is set in this process's environment and wins: ismail keeps using "
                 f"{S.show(S.get(key))} until it is unset where it was set (a shell profile, the MCP server's "
                 f"configuration) and ismail restarts")
    else:
        L.append(f"applies: {S.applies(s)}")
    L.append(f"config_get({key!r}) reads it back; config_unset({key!r}) returns it to the default")
    return '\n'.join(L)


def _example(s):
    return {'path': "'D:/music'" if os.name == 'nt' else "'~/Music/ismail'", 'paths': "['D:/voices']",
            'float': f"{s.default if isinstance(s.default, float) else 0}", 'int': '128', 'bool': 'true',
            'url': "'http://127.0.0.1:8765'", 'list': "['dj', 'mc']"}.get(s.type, "'...'")


@op(mutates=True)
def config_unset(key: str) -> str:
    """Return one of ismail's settings to its default: its line leaves the settings file. The reply says the value
    now in use and when it applies. config_get(key) reads it back."""
    try:
        s = S.setting(key)
    except KeyError as e:
        raise _err(e)
    try:
        had = S.drop(key)
    except ValueError as e:
        raise OpError(str(e))
    v, src, _ = S.resolve(key)
    L = [(f"{key}: removed from the settings file" if had else f"{key} was not in the settings file") +
         f"; now {S.show(v)} ({'the default' if src == 'default' else 'from ' + s.env + ', which still wins'})"]
    if had:
        L.append(f"applies: {S.applies(s)}")
    return '\n'.join(L)
