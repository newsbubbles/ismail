"""ismail's settings (ledger:M368): one registry, the ops that read and change it, and the checks that hold the code
and the docs to it. Every variable ismail reads is a setting's old name or listed in PLUMBING with its reason, and
only ismail/settings.py reads a setting's old name."""
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ismail import api, settings as S

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / 'ismail'
NAME = re.compile(r'\bISMAIL_[A-Z][A-Z0-9_]*\b')
ENVS = {s.env for s in S.SETTINGS}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A settings file of the test's own, and none of the old variables set."""
    p = tmp_path / 'cfg' / 'config.json'
    monkeypatch.setenv(S.CONFIG_ENV, str(p))
    for s in S.SETTINGS:
        monkeypatch.delenv(s.env, raising=False)
    return p


# ------------------------------------------------------------------ the registry is complete

def _is_environ(node):
    """os.environ or a bare environ."""
    return (isinstance(node, ast.Attribute) and node.attr == 'environ' and isinstance(node.value, ast.Name)
            and node.value.id == 'os') or (isinstance(node, ast.Name) and node.id == 'environ')


def _env_reads(tree):
    """[(line, the name read or None when it cannot be told)]: os.environ.get / [] / setdefault / pop, os.getenv
    and `in os.environ`. A key held in a module constant (FOO_ENV = 'ISMAIL_FOO') is followed to its value."""
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = n.value.value

    def key(k):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            return k.value
        if isinstance(k, ast.Name) and k.id in consts:
            return consts[k.id]
        return None

    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            f = n.func
            if f.attr in ('get', 'setdefault', 'pop') and _is_environ(f.value) and n.args:
                out.append((n.lineno, key(n.args[0])))
            elif f.attr == 'getenv' and isinstance(f.value, ast.Name) and f.value.id == 'os' and n.args:
                out.append((n.lineno, key(n.args[0])))
        elif isinstance(n, ast.Subscript) and _is_environ(n.value) and isinstance(n.ctx, ast.Load):
            out.append((n.lineno, key(n.slice)))
        elif isinstance(n, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in n.ops) and \
                any(_is_environ(c) for c in n.comparators):
            out.append((n.lineno, key(n.left)))
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Attribute) and n.value.attr == 'get' and \
                _is_environ(n.value.value):
            out.append((n.lineno, None))                      # env = os.environ.get: a read by another name
    return out


def _sources():
    for p in sorted(PKG.rglob('*.py')):
        yield p, p.read_text(encoding='utf8')


def test_every_variable_ismail_reads_is_a_setting_or_plumbing():
    """A variable read nowhere listed: make it a setting in ismail/settings.py (and read it with settings.get), or
    add it to PLUMBING with the reason it is not one."""
    unknown, dynamic, outside = [], [], []
    for p, src in _sources():
        rel = p.relative_to(ROOT).as_posix()
        for line, name in _env_reads(ast.parse(src)):
            where = f'{rel}:{line}'
            if name is None:
                if rel != 'ismail/settings.py':
                    dynamic.append(where)
            elif name in ENVS:
                if rel != 'ismail/settings.py':
                    outside.append(f'{where} {name}')
            elif name not in S.PLUMBING:
                unknown.append(f'{where} {name}')
    assert unknown == [], 'read but neither a setting nor plumbing: ' + ', '.join(unknown)
    assert outside == [], "a setting's old variable read outside settings.py (use settings.get): " + ', '.join(outside)
    assert dynamic == [], 'a variable read by a name the scan cannot tell: ' + ', '.join(dynamic)


def test_every_ismail_name_in_the_code_is_known():
    """Every ISMAIL_* string in the package (code, messages, docstrings) names a setting's old variable or plumbing."""
    bad = []
    for p, src in _sources():
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                bad += [f'{p.relative_to(ROOT).as_posix()}:{n.lineno} {m}' for m in NAME.findall(n.value)
                        if m not in ENVS and m not in S.PLUMBING]
    assert bad == []


def test_every_plumbing_name_is_still_used():
    """A name nothing in the package mentions any more leaves PLUMBING (ISMAIL_CONFIG is settings.py's own)."""
    text = '\n'.join(src for p, src in _sources() if p.name != 'settings.py')
    assert [k for k in S.PLUMBING if k != S.CONFIG_ENV and not re.search(r'\b' + k + r'\b', text)] == []


def test_the_scan_sees_every_way_of_reading():
    src = ("import os\nFOO_ENV = 'ISMAIL_FOO'\n"
           "a = os.environ.get('A')\nb = os.environ['B']\nc = os.getenv('C')\nd = 'D' in os.environ\n"
           "e = os.environ.get(FOO_ENV)\nf = os.environ.get(x)\ng = os.environ.get\n")
    names = [n for _, n in sorted(_env_reads(ast.parse(src)), key=lambda r: r[0])]
    assert names == ['A', 'B', 'C', 'D', 'ISMAIL_FOO', None, None]


def test_the_registry_is_well_formed():
    keys = [s.key for s in S.SETTINGS]
    assert len(keys) == len(set(keys)) and len(ENVS) == len(keys)
    for s in S.SETTINGS:
        assert re.fullmatch(r'[a-z][a-z0-9_]*(\.[a-z0-9_]+)*', s.key), s.key
        assert s.type in ('path', 'paths', 'list', 'str', 'url', 'float', 'int', 'bool'), s.key
        assert s.doc and s.group and s.env not in S.PLUMBING, s.key
        assert s.live or s.when, f'{s.key}: a restart-only setting says what to restart'
        if s.default is not None and not callable(s.default):
            assert S.coerce(s, s.default) == s.default, s.key
    assert set(S.PLUMBING) & ENVS == set()


def test_every_sample_set_has_its_setting():
    from ismail import samples
    for name, s in samples.SETS.items():
        assert S.setting('samples.' + name).env == s['env'], name


# ------------------------------------------------------------------ the docs table is the registry

def _doc_table():
    text = (ROOT / 'docs' / 'reference.md').read_text(encoding='utf8')
    start = text.index('| setting | type |')
    end = text.index('\n\n', start)
    return text[start:end]


def test_the_reference_table_is_generated_from_the_registry():
    """Regenerate it with `python -m ismail.settings table` and paste it over the table in docs/reference.md."""
    assert _doc_table() == S.table()


# ------------------------------------------------------------------ resolution

def test_default_then_file_then_old_variable(cfg, monkeypatch):
    assert S.resolve('live.trim_db')[:2] == (-6.0, 'default')
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'live.trim_db': -3}), encoding='utf8')
    assert S.resolve('live.trim_db')[:2] == (-3.0, 'file')
    monkeypatch.setenv('ISMAIL_LIVE_TRIM_DB', '-9')
    assert S.resolve('live.trim_db')[:2] == (-9.0, 'env')
    monkeypatch.setenv('ISMAIL_LIVE_TRIM_DB', '')                 # empty is unset, as it always was
    assert S.source('live.trim_db') == 'file'


def test_a_bad_value_is_skipped_and_named(cfg, monkeypatch):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'live.cap_db': 5}), encoding='utf8')          # louder than the cap's range
    v, src, problems = S.resolve('live.cap_db')
    assert (v, src) == (-16.0, 'default') and 'from -40 to -8' in problems[0]
    monkeypatch.setenv('ISMAIL_LIVE_CAP_DB', 'loud')
    assert S.get('live.cap_db') == -16.0
    with pytest.raises(ValueError, match='ISMAIL_LIVE_CAP_DB'):
        S.get('live.cap_db', strict=True)


def test_a_file_change_is_seen_without_a_restart(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'live.idle_min': 30}), encoding='utf8')
    assert S.get('live.idle_min') == 30.0
    cfg.write_text(json.dumps({'live.idle_min': 45.5}), encoding='utf8')
    st = cfg.stat()
    os.utime(cfg, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))            # a coarse clock still sees a change
    assert S.get('live.idle_min') == 45.5


def test_a_broken_file_is_ignored_and_never_overwritten(cfg):
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"songs": ', encoding='utf8')
    assert S.source('songs') == 'default'
    assert 'WARNING' in api.OPS['config_get']()
    with pytest.raises(api.OpError, match='fix it by hand'):
        api.OPS['config_set']('live.trim_db', -3)
    assert cfg.read_text(encoding='utf8') == '{"songs": '


def test_the_songs_folder_follows_the_setting(cfg, tmp_path):
    from ismail import handoffs
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'songs': str(tmp_path / 'music')}), encoding='utf8')
    assert handoffs._songs_default() == str(tmp_path / 'music')


# ------------------------------------------------------------------ the ops

def test_set_get_unset_round_trip(cfg, tmp_path):
    d = tmp_path / 'stacks'
    d.mkdir()
    out = api.OPS['config_set']('fx_stacks', str(d))
    assert f'fx_stacks = {d}' in out and 'applies: at its next use' in out and 'config_unset' in out
    assert json.loads(cfg.read_text(encoding='utf8')) == {'fx_stacks': str(d)}
    from ismail import api_fxstack
    assert api_fxstack.library() == str(d)                       # read at use: no restart
    got = api.OPS['config_get'](key='fx_stacks')
    assert str(d) in got and 'from the settings file' in got
    out = api.OPS['config_unset']('fx_stacks')
    assert 'removed from the settings file' in out and json.loads(cfg.read_text(encoding='utf8')) == {}
    assert api.OPS['config_get'](key='fx_stacks').count('the default') >= 1
    assert 'was not in the settings file' in api.OPS['config_unset']('fx_stacks')


def test_set_coerces_types(cfg):
    api.OPS['config_set']('live.trim_db', '-3.5')
    api.OPS['config_set']('phone.keep_master', 'false')
    api.OPS['config_set']('mcp.roles', 'dj, mc')
    api.OPS['config_set']('mcp.max_tools', 120.0)
    assert json.loads(cfg.read_text(encoding='utf8')) == {'live.trim_db': -3.5, 'mcp.max_tools': 120,
                                                          'mcp.roles': ['dj', 'mc'], 'phone.keep_master': False}
    assert S.get('mcp.roles') == ['dj', 'mc'] and S.get('phone.keep_master') is False


@pytest.mark.parametrize('key,value,says', [
    ('live.trim_db', 'loud', 'a number from -30 to 12'),
    ('live.trim_db', 40, 'a number from -30 to 12'),
    ('live.ceiling_db', 0, 'from -12 to -1'),                   # the ceiling can only go down
    ('mcp.max_tools', 1.5, 'a whole number'),
    ('phone.keep_master', 'maybe', 'true or false'),
    ('speak', 'localhost:8765', 'http://'),
    ('mcp.roles', ['dj', 'nobody'], 'unknown nobody'),
    ('songs', '', 'a path'),
])
def test_set_refuses_a_bad_value_and_changes_nothing(cfg, key, value, says):
    with pytest.raises(api.OpError) as e:
        api.OPS['config_set'](key, value)
    assert says in str(e.value) and 'Nothing was changed' in str(e.value) and 'Example: config_set(' in str(e.value)
    assert not cfg.exists()


def test_an_unknown_key_names_the_closest(cfg):
    for op, args in (('config_get', {'key': 'live.trim'}), ('config_set', {'key': 'live.trim', 'value': 1}),
                     ('config_unset', {'key': 'live.trim'})):
        with pytest.raises(api.OpError) as e:
            api.OPS[op](**args)
        assert 'live.trim_db' in str(e.value) and 'config_get()' in str(e.value), op
    with pytest.raises(api.OpError, match='groups: paths'):
        api.OPS['config_get'](group='nope')


def test_an_old_variable_that_wins_is_reported(cfg, monkeypatch, tmp_path):
    monkeypatch.setenv('ISMAIL_SAMPLES', str(tmp_path / 'from_env'))
    out = api.OPS['config_set']('samples', str(tmp_path / 'from_file'))
    assert 'ISMAIL_SAMPLES is set' in out and 'wins' in out and str(tmp_path / 'from_env') in out
    assert 'does not exist yet' in out
    assert S.get('samples') == str(tmp_path / 'from_env')
    assert '[set by ISMAIL_SAMPLES]' in api.OPS['config_get']()
    monkeypatch.delenv('ISMAIL_SAMPLES')
    assert S.get('samples') == str(tmp_path / 'from_file')


def test_live_and_restart_settings_say_when_they_apply(cfg, tmp_path):
    out = api.OPS['config_set']('live.trim_db', -3)
    assert 'applies: at the next live_start' in out
    out = api.OPS['config_set']('songs', str(tmp_path))
    assert 'applies: after a restart' in out and 'restart ismail' in out
    assert 'a change applies: after a restart' in api.OPS['config_get'](key='songs')


def test_the_write_is_atomic_and_keeps_other_keys(cfg, monkeypatch):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'someone.elses': 1, 'live.trim_db': -2}), encoding='utf8')
    seen = []
    real = os.replace
    monkeypatch.setattr(S.os, 'replace', lambda a, b: (seen.append((a, b)), real(a, b)))
    api.OPS['config_set']('live.cap_db', -14)
    assert seen and seen[0][1] == str(cfg) and seen[0][0] != str(cfg)
    assert json.loads(cfg.read_text(encoding='utf8')) == {'live.cap_db': -14.0, 'live.trim_db': -2,
                                                          'someone.elses': 1}
    assert [p.name for p in cfg.parent.iterdir()] == ['config.json']    # no temp file left
    assert 'someone.elses' in api.OPS['config_get']()                     # an unknown key is named, not lost


def test_config_get_lists_every_setting_bounded(cfg):
    out = api.OPS['config_get']()
    for s in S.SETTINGS:
        assert f'  {s.key} = ' in out, s.key
    assert len(out.splitlines()) < len(S.SETTINGS) + len(S.GROUPS) + 6
    live = api.OPS['config_get'](group='live')
    assert 'live.ceiling_db' in live and 'songs' not in live


def test_the_config_ops_are_on_every_filtered_server():
    from ismail.api_ops import ops_for
    for role in ('mc', 'stage'):
        assert {'config_get', 'config_set', 'config_unset'} <= set(ops_for(roles=[role]))


# ------------------------------------------------------------------ settings at their use

def test_the_live_safety_reads_the_settings(cfg, monkeypatch):
    from ismail.live.safety import Safety
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'live.trim_db': -4, 'live.cap_db': -20, 'live.ceiling_db': -2}), encoding='utf8')
    s = Safety()
    assert (s.trim_db, s.cap_db, s.ceiling_db) == (-4.0, -20.0, -2.0)
    monkeypatch.setenv('ISMAIL_LIVE_CEILING_DB', '-0.5')        # the owner's old variable is taken as it is
    assert Safety(trim_db=0).ceiling_db == -0.5


def test_djkit_leaves_a_configured_trim_to_the_engine(cfg):
    from ismail.live.djkit import Set
    got = {}
    kit = Set.__new__(Set)
    cfg.parent.mkdir(parents=True)
    kit.P, kit.log_path = str(cfg.parent), str(cfg.parent / 'log.txt')
    kit.O = {'live_start': lambda p, **kw: (got.update(kw), 'started')[1]}
    kit.start(120)
    assert (got['trim_db'], got['cap_db']) == (4, -12)
    api.OPS['config_set']('live.trim_db', -1)
    kit.start(120)
    assert (got['trim_db'], got['cap_db']) == (None, -12)


def test_the_mcp_server_reads_its_tool_list_settings(cfg):
    from ismail import mcp_server
    api.OPS['config_set']('mcp.roles', ['mc'])
    api.OPS['config_set']('mcp.max_tools', 3)
    with pytest.raises(SystemExit):                              # mc is more than 3 tools: refused, with the sizes
        mcp_server.main([])


def test_machine_run_as_names_the_session(tmp_path):
    from ismail import machine
    env = dict(os.environ, ISMAIL_MACHINE_DIR=str(tmp_path / 'board'), CI='1')
    env.pop('ISMAIL_SESSION', None)
    env.pop('ISMAIL_SLOT', None)
    r = subprocess.run([sys.executable, '-m', 'ismail.machine', 'run', '--cpu', '--force', '--as', 'crew-a', '--',
                        sys.executable, '-c', f'import os; print(os.environ.get({machine.SESSION_ENV!r}))'],
                       capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, r.stderr
    assert 'crew-a' in r.stdout.splitlines()


def test_the_video_shot_gets_the_kit_on_its_path(tmp_path):
    from ismail.video.__main__ import KIT, blender_cmd
    cmd = blender_cmd(str(tmp_path), 's01.py', ['--still', '1'])
    i, j = cmd.index('--python-expr'), cmd.index('-P')
    assert i < j and repr(KIT) in cmd[i + 1] and cmd[j + 1].endswith(os.path.join('shots', 's01.py'))
    tpl = (PKG / 'video' / 'templates' / 'shots' / 's01_example.py').read_text(encoding='utf8')
    assert 'import kit' in tpl and 'ISMAIL_VIDEO_KIT' not in tpl


# ------------------------------------------------------------------ the named-ops guard reads the file

def _guard(cfg):
    import importlib.util
    spec = importlib.util.spec_from_file_location('named_ops_guard_t',
                                                  ROOT / 'skills' / 'ismail' / 'hooks' / 'named_ops_guard.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CONFIG_FILE = str(cfg)
    return m


def test_the_guard_reads_the_registries_from_the_settings_file(cfg, tmp_path, monkeypatch):
    reg = tmp_path / 'stage_reg'
    reg.mkdir()
    (reg / '1.json').write_text(json.dumps({'port': 9001, 'scenes': 'X:/work/scenes'}), encoding='utf8')
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({'stage.registry': str(reg), 'live.registry': str(tmp_path / 'none')}), encoding='utf8')
    g = _guard(cfg)
    assert g.stage_records() == [{'port': 9001, 'scenes': 'X:/work/scenes'}] and g.live_records() == []
    g2 = _guard(cfg)
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'elsewhere'))   # the old variable still wins
    assert g2.stage_records() == []


@pytest.mark.parametrize('text', ['not json', '[1, 2]', '{"stage.registry": 5}', '{"stage.registry": ""}'])
def test_the_guard_never_raises_on_a_broken_file(cfg, text):
    cfg.parent.mkdir(parents=True)
    cfg.write_text(text, encoding='utf8')
    g = _guard(cfg)
    assert g.setting('stage.registry') is None
    assert isinstance(g.stage_records(), list) and isinstance(g.live_records(), list)
