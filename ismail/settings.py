"""ismail's settings: one registry of everything a person or an agent configures (ledger:M368, Nate 10-27: "using env
was never really a production ready move. Let's build config and make sure to validate we've got everything and
what it affects").

Every setting has a dotted key ('songs', 'ffmpeg', 'live.trim_db', 'listen.home'), a type, a default, one line on
what it does, a group, and whether a change applies at once or after a restart. The ops are config_get, config_set
and config_unset (api_config.py); code reads a value with get(key).

Where a value comes from, first match wins:
  1. the old environment variable of the setting (ISMAIL_SONGS, ISMAIL_FFMPEG, ...), when set and valid: every
     script, test, CI job and plugin configuration that set one keeps working, and it overrides the file;
  2. the settings file, ~/.ismail/config.json (ISMAIL_CONFIG names another file; the tests point it at a temp
     folder), a flat JSON object {"songs": "D:/music", "live.trim_db": -3};
  3. the default.

The file is read again only when it changes (its size and modification time), so a setting read at its use takes
a change made with config_set at the next use, in a running server too. A setting whose module keeps its value
from import says so (live=False and `when`): it applies after that process restarts.

PLUMBING lists the variables ismail sets for its own child processes or its tests, and the ones other programs
own: they are not settings and stay variables. tests/test_settings.py fails when ismail reads a variable that is in
neither list, or reads a setting's variable anywhere but here. Stdlib only, and nothing here imports the rest of
ismail at import time.
"""
import difflib
import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

CONFIG_ENV = 'ISMAIL_CONFIG'


def _home(*parts):
    return lambda: os.path.join(os.path.expanduser('~'), '.ismail', *parts)


def _songs_default():
    from . import handoffs                      # lazy: handoffs reads the 'songs' setting at its own import
    return handoffs.songs_fallback()


@dataclass(frozen=True)
class Setting:
    key: str
    env: str                 # the old environment variable: set, it overrides the file
    type: str                # path, paths (a list of folders), list, str, url, float, int, bool
    default: object          # a value, a callable giving it, or None (the code that uses it decides: default_doc)
    doc: str                 # what it does, one line
    group: str
    live: bool = True        # True: a change applies at its next use; False: after a restart (when)
    when: str = ''           # when a change applies, in words, if not at once
    default_doc: str = ''    # the default in words, when it is computed where it is used
    lo: float = None         # numbers: the range a file value must be in
    hi: float = None
    choices: tuple = field(default=None)   # list/str: the values allowed (a callable giving them)


RESTART = 'restart ismail (the MCP server, or quit and reopen the app); a CLI call picks it up at once'
NEXT_START = 'at the next live_start'

SETTINGS = [
    # paths: where ismail keeps things
    Setting('songs', 'ISMAIL_SONGS', 'path', _songs_default,
            'the songs folder: a bare song name (project=\'my_song\') is a folder in it for every op, and the '
            'machine board lives in it', 'paths',
            live=False, when='restart ismail (the MCP server, or quit and reopen the app) and its stage, phone and '
                             'live processes; a bare song name and a CLI call follow it at once',
            default_doc='songs/ of a clone, else Documents/ismail'),
    Setting('voices', 'ISMAIL_VOICES', 'paths', list,
            'more folders of code voices, searched after the song\'s own voices/ and before the built-ins', 'paths'),
    Setting('samples', 'ISMAIL_SAMPLES', 'path', _home('samples'),
            'where samples_fetch stores downloaded sample sets', 'paths'),
    Setting('fx_stacks', 'ISMAIL_FX_STACKS', 'path', _home('fx_stacks'),
            'the library of named effect stacks (fx_stack_save) and their measured costs', 'paths'),
    Setting('lexicon', 'ISMAIL_LEXICON', 'path', None,
            "the file of the person's own words for what they hear (lexicon_*)", 'paths',
            default_doc='<songs>/_user/lexicon.jsonl'),
    Setting('reclaim', 'ISMAIL_RECLAIM', 'path', None,
            'where files moved for the person to delete go (python -m ismail.reclaim, song_compress)', 'paths',
            default_doc='<drive of the songs folder>/_reclaim on Windows, else <songs>/_reclaim'),
    Setting('hrtf', 'ISMAIL_HRTF', 'path', _home('hrtf'),
            'the cache of measured head responses the spatial effect loads', 'paths'),
    Setting('toms_cache', 'ISMAIL_TOMS_CACHE', 'path', _home('cache', 'toms'),
            'the cache of the toms voice\'s prepared strokes', 'paths'),
    Setting('listen.home', 'ISMAIL_LISTEN_HOME', 'path', None,
            'where a listening-together session goes when no project is named and no live set runs', 'paths',
            default_doc='<songs>/_listen'),
    Setting('feedback.home', 'ISMAIL_FEEDBACK_HOME', 'path', _home('feedback'),
            'the feedback pages\' server state and answers (feedback_*)', 'paths',
            live=False, when='stop a running feedback server first (feedback_stop), change it, then start it again: '
                             'a running one keeps its state in the old folder'),
    Setting('phone.home', 'ISMAIL_PHONE_HOME', 'path', _home('phone'),
            "the phone page's state: its inbox, voice notes, hooks.json", 'paths',
            live=False, when='phone_restart() (the phone ops follow at once; a running page server keeps its '
                             'folder until it restarts)'),
    Setting('stream.home', 'ISMAIL_STREAM_HOME', 'path', _home('stream'),
            'the streaming state: sites, keys file, chat, request queue', 'paths',
            live=False, when=RESTART),
    Setting('exampage.rounds', 'ISMAIL_EXAMPAGE_ROUNDS', 'path', _home('exampage', 'rounds.json'),
            'the registry of picture and exam rounds (name -> round folder)', 'paths'),
    Setting('stage.registry', 'ISMAIL_STAGE_REGISTRY', 'path', _home('stage'),
            'where each running stage server leaves a note so agents and the guard find it', 'paths',
            live=False, when='stop the running stage servers first (stage_stop), change it, then stage_start: a '
                             'running one keeps its note in the old folder, where agents no longer look'),
    Setting('stage.hooks', 'ISMAIL_STAGE_HOOKS', 'path', _home('stage_hooks.json'),
            'the stage hooks file: commands run on entered_vr, voice_note and the other stage events', 'paths'),
    Setting('live.registry', 'ISMAIL_LIVE_REGISTRY', 'path', _home('live'),
            'where each running live engine leaves a note so live_start can name the ones holding a device',
            'paths', live=False, when='stop the running live engines first (live_stop), change it, then live_start: '
                                      'a running one keeps its note in the old folder'),
    # the machine board
    Setting('machine.dir', 'ISMAIL_MACHINE_DIR', 'path', None,
            'the machine board every session shares (heavy-job slots, history)', 'machine',
            default_doc='<songs>/_machine'),
    Setting('machine.disk_roots', 'ISMAIL_DISK_ROOTS', 'paths', None,
            'the folders `machine disk` measures beyond the songs folder', 'machine',
            default_doc='<board>/roots.txt, else every folder beside the checkout'),
    # privacy: the person's local files the guards read
    Setting('guards.private_terms', 'ISMAIL_PRIVATE_TERMS', 'path', _home('private_terms.txt'),
            'places and names that must never leave the machine in a reply or a post, one a line', 'privacy'),
    Setting('guards.grants', 'ISMAIL_STANDING_GRANTS', 'path', _home('standing_grants.json'),
            "the person's standing grants and routes (grant_*)", 'privacy'),
    Setting('guards.mic_consent_log', 'ISMAIL_MIC_CONSENT_LOG', 'path', _home('mic_consent.jsonl'),
            "the log of the person's yes to a microphone", 'privacy'),
    # tools ismail runs
    Setting('ffmpeg', 'ISMAIL_FFMPEG', 'path', lambda: shutil.which('ffmpeg'),
            'the ffmpeg program: mp3 renders, the phone stream, video, streaming', 'tools',
            default_doc='ffmpeg on PATH'),
    Setting('blender', 'ISMAIL_BLENDER', 'path', None,
            'the Blender program for music videos and stage exports (a song\'s video/config.json "blender" wins)',
            'tools', default_doc='the usual install folder, else blender on PATH'),
    Setting('browser', 'ISMAIL_BROWSER', 'path', None,
            'a Chromium browser (Edge or Chrome) for stage captures and preflight', 'tools',
            default_doc='Edge or Chrome where they install, else on PATH'),
    Setting('rhubarb', 'ISMAIL_RHUBARB', 'path', None,
            'the Rhubarb Lip Sync program or its folder, for stage_face', 'tools',
            default_doc='searched where it unpacks'),
    Setting('cmudict', 'ISMAIL_CMUDICT', 'path', None,
            'a CMU pronouncing dictionary file, for mouth shapes from words', 'tools',
            default_doc='an installed cmudict package, else the built-in rules'),
    Setting('esbuild', 'ESBUILD', 'path', None,
            'the esbuild program the stage server bundles its page with', 'tools',
            live=False, when='restart the stage server (stage_restart)',
            default_doc='"esbuild" in ~/.ismail/stage.json, else on PATH'),
    Setting('speak', 'ISMAIL_SPEAK', 'url', 'http://127.0.0.1:8765',
            'the speech server that turns voice notes and recordings into words', 'tools',
            live=False, when='restart the phone page, stage and ismail servers that use it'),
    # live sets
    Setting('live.trim_db', 'ISMAIL_LIVE_TRIM_DB', 'float', -6.0,
            "the live master's trim in dB when live_start gets no trim_db", 'live',
            when=NEXT_START, lo=-30.0, hi=12.0),
    Setting('live.cap_db', 'ISMAIL_LIVE_CAP_DB', 'float', -16.0,
            "the live master's sustained level cap (RMS dBFS over ~3 s) when live_start gets no cap_db", 'live',
            when=NEXT_START, lo=-40.0, hi=-8.0),
    Setting('live.ceiling_db', 'ISMAIL_LIVE_CEILING_DB', 'float', -1.0,
            "the live master's peak ceiling in dBFS; the file can only lower it", 'live',
            when=NEXT_START, lo=-12.0, hi=-1.0),
    Setting('live.idle_min', 'ISMAIL_LIVE_IDLE_MIN', 'float', 60.0,
            'minutes a live engine with nothing to play waits before it stops itself', 'live',
            when=NEXT_START, lo=1.0, hi=1440.0),
    # the phone page
    Setting('phone.keep_master', 'ISMAIL_PHONE_KEEP_MASTER', 'bool', True,
            'the phone server takes the live master while an engine plays, page open or not', 'phone',
            live=False, when='restart the phone page server (phone_stop, then phone_start)'),
    # the MCP server's tool list
    Setting('mcp.roles', 'ISMAIL_MCP_ROLES', 'list', list,
            'serve only these roles\' tools (dj, mc, song, ...); --roles wins', 'mcp',
            live=False, when=RESTART, choices=lambda: _api_ops().ROLES),
    Setting('mcp.shelves', 'ISMAIL_MCP_SHELVES', 'list', list,
            'serve only these shelves\' tools (live, stream, phone, ...); --shelves wins', 'mcp',
            live=False, when=RESTART, choices=lambda: [c[0] for c in _api_ops().CATEGORIES]),
    Setting('mcp.max_tools', 'ISMAIL_MCP_MAX_TOOLS', 'int', None,
            'refuse to start the MCP server when it would serve more tools than this; --max-tools wins', 'mcp',
            live=False, when=RESTART, default_doc='no cap', lo=1, hi=100000),
    # sample sets kept outside the store (samples_fetch(name, path=...) registers one the same way)
    Setting('samples.jrhodes3d', 'RHODES_SAMPLES', 'path', None,
            "a folder holding the jrhodes3d set (the rhodes voice), outside the store", 'samples',
            default_doc='a registered folder, else the store'),
    Setting('samples.big_rusty', 'RUSTY_SAMPLES', 'path', None,
            "a folder holding the big_rusty set (the rusty and toms voices), outside the store", 'samples',
            default_doc='a registered folder, else the store'),
    Setting('samples.emilyguitar', 'EMILY_SAMPLES', 'path', None,
            "a folder holding the emilyguitar set (the emily voice), outside the store", 'samples',
            default_doc='a registered folder, else the store'),
    Setting('samples.sadie_d2', 'SADIE_D2_HRTF', 'path', None,
            "a folder holding the sadie_d2 head responses (spatial hrtf='sadie_d2'), outside the store", 'samples',
            default_doc='a registered folder, else the store'),
    Setting('samples.toms', 'TOM_SAMPLES', 'path', None,
            "a Big Rusty Samples folder the toms voice tries first", 'samples',
            default_doc="the project's ref/, else the big_rusty set"),
]

REGISTRY = {s.key: s for s in SETTINGS}
GROUPS = list(dict.fromkeys(s.group for s in SETTINGS))

# Variables that are not settings. Each one is set by ismail for a process it starts or by the tests, or belongs to
# another program; the reason says which. tests/test_settings.py holds every variable ismail reads to this list or
# to a setting.
PLUMBING = {
    CONFIG_ENV: 'where the settings file is; the tests point it at a temp folder',
    'ISMAIL_SLOT': '`machine run` hands its slot to the command it starts, so a render inside it uses that slot',
    'ISMAIL_SESSION': "a process's own name on the machine board (`machine run --as <name>` sets it); one per "
                      "session, so not a machine-wide setting",
    'ISMAIL_PROJECT': "the CLI's default project for one shell (-p wins); per shell, so not a setting",
    'ISMAIL_WORKTREES_NOW': 'the tests fix the clock that ages worktree commits',
    'ISMAIL_FIRST_SESSION': "the tests move the first-session mark (~/.ismail/first_session_done)",
    # the old way to hand these to Blender, still read until 0.5; now sys.ismail_video_dir and sys.vr_job (M382)
    'ISMAIL_VIDEO_KIT': 'the Blender shot scripts of older songs find the kit through it (until 0.5)',
    'ISMAIL_VIDEO_DIR': "the Blender kit's song video folder before sys.ismail_video_dir (until 0.5)",
    'VR_BRIDGE': "a room script's engine bridge before sys.vr_job['bridge'] (until 0.5)",
    'VR_PASS': "the bridge's passes before sys.vr_job['passes'] (until 0.5)",
    'VR_EDITS': "the bridge's edits file before sys.vr_job['edits'] (until 0.5)",
    'VR_DIET': "the Quest diet switch before sys.vr_job['diet'] (until 0.5)",
    'VR_EXPORT': "the export folder before sys.vr_job['export'] (until 0.5)",
    'VR_DETAIL': "the detailed export, now a world.json switch in sys.vr_job['switches'] (until 0.5)",
    'VR_TRI_MAX': "the Quest diet's triangle budget, now a switch in sys.vr_job['switches'] (until 0.5)",
    'TEMP': "the system's temporary folder",
    'PYTHONPATH': 'ismail puts itself on the path of the Python processes it starts',
    'PYTHONWARNINGS': 'the MCP server keeps library warnings off its protocol stream',
    'HF_HOME': "the model cache of the Hugging Face libraries (ismail sets a default under the checkout)",
}

_lock = threading.Lock()
_cache = {'key': None, 'data': {}, 'error': None}


def _api_ops():
    from . import api_ops
    return api_ops


def path():
    """The settings file: $ISMAIL_CONFIG, else ~/.ismail/config.json. A relative $ISMAIL_CONFIG is made absolute
    against this process's folder at its first read, and the variable is set to that, so a process ismail starts
    from another folder reads the same file."""
    raw = os.environ.get(CONFIG_ENV)
    if raw:
        p = os.path.abspath(os.path.expanduser(raw))
        if p != raw:
            os.environ[CONFIG_ENV] = p
        return p
    return os.path.expanduser(os.path.join('~', '.ismail', 'config.json'))


def _json_problem(p, e):
    """Why the file does not read, with where and the fix."""
    if isinstance(e, json.JSONDecodeError):
        why = f"{e.msg} at line {e.lineno}, column {e.colno}"
        fix = ('a Windows path needs forward slashes ("D:/music") or doubled backslashes ("D:\\\\music")'
               if 'escape' in e.msg.lower() else 'fix that spot by hand')
        return f"the settings file {p} does not read as JSON: {why}; {fix}"
    if isinstance(e, UnicodeDecodeError):
        return f"the settings file {p} is not UTF-8 text ({e.reason} at byte {e.start}); save it as UTF-8"
    return f"the settings file {p} cannot be read ({e})"


def load():
    """(the file's settings as a dict, None or why it could not be read: the file, the line and column, the fix).
    Read again only when its size or modification time changed; a missing file is an empty one. A byte order mark
    (Notepad's UTF-8) is fine."""
    p = path()
    try:
        st = os.stat(p)
        key = (p, st.st_mtime_ns, st.st_size)
    except OSError:
        return {}, None
    with _lock:
        if _cache['key'] == key:
            return _cache['data'], _cache['error']
    try:
        with open(p, encoding='utf-8-sig') as f:
            data = json.load(f)
        err = None if isinstance(data, dict) else (f'the settings file {p} is not a JSON object of "key": value '
                                                   f'({{"songs": "D:/music"}})')
    except (OSError, ValueError) as e:
        data, err = {}, _json_problem(p, e)
    if err:
        data = {}
    with _lock:
        _cache.update(key=key, data=data, error=err)
    return data, err


def setting(key):
    """The Setting for key, or KeyError naming the closest keys."""
    try:
        return REGISTRY[key]
    except KeyError:
        near = difflib.get_close_matches(str(key), REGISTRY, n=3, cutoff=0.5) or \
            [k for k in REGISTRY if str(key).split('.')[-1] in k][:3]
        raise KeyError(f"unknown setting {key!r}" + (f"; closest: {', '.join(near)}" if near else '') +
                       '. config_get() lists every setting') from None


def env_name(key):
    """The old environment variable of a setting (for code and tests that set it as an override)."""
    return setting(key).env


def _choices(s):
    c = s.choices() if callable(s.choices) else s.choices
    return list(c) if c is not None else None


def what_it_takes(s):
    """A setting's type in words, with its range or choices: for refusals and config_get."""
    if s.type in ('float', 'int'):
        kind = 'a number' if s.type == 'float' else 'a whole number'
        if s.lo is not None and s.hi is not None:
            return f'{kind} from {s.lo:g} to {s.hi:g}'
        return kind
    if s.type == 'bool':
        return 'true or false'
    if s.type == 'path':
        return 'a path to a file or folder'
    if s.type == 'paths':
        return 'a list of folders'
    if s.type == 'list':
        c = _choices(s)
        return 'a list of names' + (f" from: {', '.join(c)}" if c else '')
    if s.type == 'url':
        return 'an http:// or https:// address'
    return 'text'


def coerce(s, value, from_env=False):
    """value (from the file, an op or an old variable) as the setting's type, or ValueError saying what it takes.
    Numbers out of range are refused from the file and the ops; an old variable's number is taken as it is, as it
    always was."""
    t = s.type
    bad = ValueError(f"{s.key} takes {what_it_takes(s)}, not {value!r}")
    if value is None:
        raise bad
    if t in ('path', 'str', 'url'):
        if not isinstance(value, str) or not value.strip():
            raise bad
        v = value.strip()
        if t == 'path':
            return os.path.expanduser(v)
        if t == 'url' and not v.startswith(('http://', 'https://')):
            raise bad
        return v
    if t in ('paths', 'list'):
        if isinstance(value, str):
            sep = os.pathsep if t == 'paths' else ','
            value = value.split(sep) if t == 'paths' or from_env else value.replace(os.pathsep, ',').split(',')
        if not isinstance(value, (list, tuple)) or not all(isinstance(x, str) for x in value):
            raise bad
        out = [os.path.expanduser(x.strip()) if t == 'paths' else x.strip() for x in value if x.strip()]
        c = _choices(s)
        if c is not None and not from_env:
            wrong = [x for x in out if x not in c]
            if wrong:
                raise ValueError(f"{s.key}: unknown {', '.join(wrong)}; the names are: {', '.join(c)}")
        return out
    if t == 'bool':
        if isinstance(value, bool):
            return value
        w = str(value).strip().lower()
        if w in ('1', 'true', 'yes', 'on'):
            return True
        if w in ('0', 'false', 'no', 'off'):
            return False
        raise bad
    if t in ('float', 'int'):
        if isinstance(value, bool):
            raise bad
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise bad from None
        if v != v:
            raise bad
        if t == 'int':
            if v != int(v):
                raise bad
            v = int(v)
        if not from_env and ((s.lo is not None and v < s.lo) or (s.hi is not None and v > s.hi)):
            raise bad
        return v
    raise bad


def default(key):
    s = setting(key)
    return s.default() if callable(s.default) else s.default


def resolve(key, strict=False):
    """(value, source, problems): source 'env', 'file' or 'default'; problems lists an old variable or a file
    value that was set but could not be used (it is skipped). strict raises ValueError on such a value instead."""
    s = setting(key)
    problems = []
    raw = os.environ.get(s.env)
    if raw not in (None, ''):
        try:
            return coerce(s, raw, from_env=True), 'env', problems
        except ValueError as e:
            if strict:
                raise ValueError(f"{s.env}={raw!r}: {e}") from None
            problems.append(f"{s.env}={raw!r} is ignored: {e}")
    data, err = load()
    if err:
        problems.append(f"{err} (it is ignored until then)")
    if key in data:
        try:
            return coerce(s, data[key]), 'file', problems
        except ValueError as e:
            if strict:
                raise ValueError(f"{path()}: {e}") from None
            kept = _known_names(s, data[key])
            if kept is not None:
                problems.append(f"the file's value: {e}; those are skipped, the rest is used")
                return kept, 'file', problems
            problems.append(f"the file's value is ignored: {e}")
    return default(key), 'default', problems


def _known_names(s, value):
    """A file's list of names with the unknown ones left out (the MCP server serves the rest), or None when the
    value is not a list of names at all."""
    c = _choices(s)
    if s.type != 'list' or c is None:
        return None
    try:
        names = coerce(s, value, from_env=True)
    except ValueError:
        return None
    kept = [x for x in names if x in c]
    return kept or None


def is_bare_name(p):
    """True for a bare song name ('my_song'): text with no folder separator that is not '.', '..', a drive
    ('C:x') or a home path ('~x'). Anything else is a path, taken as given."""
    return (isinstance(p, str) and bool(p.strip()) and p not in ('.', '..') and not re.search(r'[\\/]', p)
            and not os.path.isabs(p) and not re.match(r'^[A-Za-z]:', p) and not p.startswith('~'))


def project_dir(p, new='proj'):
    """The folder a project argument names (ledger:M375, Nate 10-27): a bare song name is always a folder in the
    songs setting, read now, so config_set('songs') moves where it lands at the next call; '.', '..' and any path
    with a separator are taken as given, from the working folder.

    Inside the song's folder (ledger:M376) the project is <song>/proj when that holds a project.json (the song layout
    of SKILL.md: build.py beside proj/), else <song> itself when that does (older songs, live sets). A song with
    neither gets <song>/<new>: 'proj' for a song, '' for a live set's folder (live.md)."""
    if is_bare_name(p):
        song = os.path.join(os.path.abspath(get('songs')), p.strip())
        for d in (os.path.join(song, 'proj'), song):
            if os.path.isfile(os.path.join(d, 'project.json')):
                return d
        return os.path.join(song, new) if new else song
    return os.path.abspath(p)


def song_name(root):
    """A project's song name: its folder's, or the song folder's for a project in <song>/proj (ledger:M376)."""
    root = os.path.abspath(root)
    base = os.path.basename(root)
    return os.path.basename(os.path.dirname(root)) if base.lower() == 'proj' else base


def get(key, strict=False):
    """A setting's value: its old variable when set, else the settings file, else the default."""
    return resolve(key, strict)[0]


def source(key):
    """'env', 'file' or 'default': where get(key) takes its value from now."""
    return resolve(key)[1]


def is_set(key):
    """True when the setting is set anywhere: its old variable is in the environment with any value (even empty or
    not a number, as code that kept out of the way of a set variable always saw it), or the file holds a usable
    value."""
    return setting(key).env in os.environ or resolve(key)[1] != 'default'


WRITE_TRIES, WRITE_WAIT_S = 3, 0.05     # Windows refuses a replace while another program has the file open
LOCK_WAIT_S, LOCK_STALE_S = 3.0, 30.0   # two processes changing it at once take turns; a lock this old was left


def _write(data):
    """Write the file whole and atomically (a temp file, then a replace); ValueError when it cannot be, with the
    temp file removed and the old file as it was."""
    p = path()
    tmp = f'{p}.{os.getpid()}.{threading.get_ident()}.tmp'
    last = None
    try:
        os.makedirs(os.path.dirname(p) or '.', exist_ok=True)
        with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(dict(sorted(data.items())), f, indent=1, ensure_ascii=False)
            f.write('\n')
        for i in range(WRITE_TRIES):
            try:
                os.replace(tmp, p)
                return
            except OSError as e:
                last = e
                time.sleep(WRITE_WAIT_S)
    except OSError as e:
        last = e
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise ValueError(f"the settings file {p} could not be written ({last}); another program may have it open, or "
                     f"the folder is read-only: try again in a moment")


@contextmanager
def _locked():
    """One process at a time changes the file (a lock file beside it; one older than LOCK_STALE_S was left by a
    process that died, and is taken over)."""
    lock = path() + '.lock'
    end = time.monotonic() + LOCK_WAIT_S
    fd = None
    while fd is None:
        try:
            os.makedirs(os.path.dirname(lock) or '.', exist_ok=True)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - os.stat(lock).st_mtime > LOCK_STALE_S:
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.monotonic() > end:
                raise ValueError(f"the settings file {path()} is being changed by another process ({lock}): try "
                                 f"again in a moment") from None
            time.sleep(WRITE_WAIT_S)
        except OSError as e:
            raise ValueError(f"the settings file {path()} cannot be changed: {e}") from None
    try:
        yield
    finally:
        os.close(fd)
        try:
            os.remove(lock)
        except OSError:
            pass


def _editable():
    data, err = load()
    if err:
        raise ValueError(f"{err}. It is never overwritten while broken: fix it by hand first")
    return dict(data)


def put(key, value):
    """Validate value, write it to the file atomically; returns the stored value. ValueError says what it takes."""
    s = setting(key)
    v = coerce(s, value)
    if s.type == 'path':
        v = os.path.abspath(v)
    elif s.type == 'paths':
        v = [os.path.abspath(x) for x in v]
    with _locked():
        data = _editable()
        data[key] = v
        _write(data)
    return v


def drop(key):
    """Remove key from the file; returns True if it was there."""
    setting(key)
    with _locked():
        data = _editable()
        if key not in data:
            return False
        del data[key]
        _write(data)
    return True


def show(value):
    """A value as one line of text."""
    if value is None:
        return '(not set)'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (list, tuple)):
        return ', '.join(map(str, value)) if value else '(none)'
    if isinstance(value, float):
        return f'{value:g}'
    return str(value)


def default_text(s):
    if s.default_doc:
        return s.default_doc
    d = default(s.key)
    return show(d)


def applies(s):
    """When a change to s takes effect, in words."""
    if s.live:
        return s.when or 'at its next use'
    return f'after a restart: {s.when}'


def table():
    """The settings table of docs/reference.md, generated from the registry (tests/test_settings.py holds the doc to
    it; `python -m ismail.settings table` prints it)."""
    rows = ['| setting | type | default | what it sets | a change applies | old variable |',
            '|---|---|---|---|---|---|']
    for s in SETTINGS:
        d = _doc_default(s).replace('|', '/')
        d = f'`{d}`' if re.search(r'[<~*_]', d) else d
        rows.append(f"| `{s.key}` | {s.type} | {d} | {s.doc.replace('|', '/')} | "
                    f"{applies(s)} | `{s.env}` |")
    return '\n'.join(rows)


def _doc_default(s):
    """The default as the docs write it, the same on every machine (~/.ismail/..., never this one's home)."""
    if s.default_doc:
        return s.default_doc
    if s.default is None or s.default is list:
        return 'none'
    if callable(s.default):
        here = os.path.join(os.path.expanduser('~'), '.ismail')
        return '~/.ismail/' + os.path.relpath(s.default(), here).replace(os.sep, '/')
    return show(s.default)


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['table']:
        print(table())
        return 0
    if argv[:1] == ['path']:
        print(path())
        return 0
    print('python -m ismail.settings table   the settings table for docs/reference.md\n'
          'python -m ismail.settings path    where the settings file is\n'
          'The ops: config_get, config_set, config_unset (python -m ismail config_get)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
