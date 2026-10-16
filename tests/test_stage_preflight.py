"""stage_preflight: the checks before a VR session, on the scratch scene _preflight, with a fake page (no browser,
no real server) and fake settings (the real ones are never read here, and nothing is ever written to them)."""
import json
import struct
import time
from pathlib import Path

import pytest

from ismail.api import OPS
from ismail.stage import preflight as P
from ismail.stage import presence as PR
from test_stage import FakePage, _get, _post, stage  # noqa: F401

GUARD = Path(__file__).resolve().parents[1] / 'skills' / 'ismail' / 'hooks' / 'named_ops_guard.py'
GOOD = {'permissions': {'allow': ['Bash(python -m ismail:*)']},
        'hooks': {'PreToolUse': [{'matcher': 'Bash|PowerShell|Write|Edit|MultiEdit', 'hooks': [
            {'type': 'command', 'command': f"python -S -c \"p = '{GUARD.as_posix()}'\""}]}]}}


@pytest.fixture()
def pf(stage, monkeypatch, tmp_path):
    """The stage fixture, with good settings, no real hooks, and a fake page in place of the headless browser."""
    monkeypatch.setenv('ISMAIL_STAGE_HOOKS', str(tmp_path / 'no_hooks.json'))
    monkeypatch.setattr(P, 'settings_files', lambda: [('~/.claude/settings.json', GOOD, None)])
    pages = []

    def launch(url):
        base, _, code = url.partition('#pair=')            # a temporary key, claimed by the link (security floor B2)
        assert base.endswith('?scene=_preflight') and len(code) == 6 and code.isdigit(), url
        got = _post(stage['port'], 'pair/claim', {'code': code})
        assert got['temp'] and got['device'].startswith('preflight '), got
        pg = FakePage(stage['port'], '_preflight', answers={'reload': {'selftest': 'PASS'}})
        _post(stage['port'], 'live/event?scene=_preflight',
              {'type': 'page_load', 'page': 'fake', 'objects': 1, 'selftest': 'PASS', 'checked': 1})
        pages.append(pg)
        return {'page': pg}

    monkeypatch.setattr(P, '_launch_page', launch)
    monkeypatch.setattr(P, '_close_page', lambda h: h and setattr(h['page'], 'stop', True))
    stage['pages'] = pages
    yield stage
    for pg in pages:
        pg.stop = True


def _rows(out):
    return {ln.split()[0]: ln.split()[1] for ln in out.splitlines()[1:-1]}


def test_the_scratch_room_is_a_valid_glb():
    b = P.scratch_glb(P.COLOR_A)
    magic, ver, total = struct.unpack('<4sII', b[:12])
    assert magic == b'glTF' and ver == 2 and total == len(b) and len(b) % 4 == 0
    n = struct.unpack('<I', b[12:16])[0]
    doc = json.loads(b[20:20 + n])
    nodes = {nd['name']: nd for nd in doc['nodes']}
    assert set(nodes) == set(P.MANIFEST['objects']) == {P.BOX, P.FLOOR}
    assert nodes[P.BOX]['translation'] == [0.0, 0.5, 0.0]
    assert P.MANIFEST['objects'][P.BOX]['location'] == [0.0, 0.0, 0.5]       # Blender z up = glTF y up
    assert P.MANIFEST['objects'][P.FLOOR]['location'] == [0.0, 0.0, -0.05]
    end = 20 + n
    assert b[end + 4:end + 8] == b'BIN\x00' and struct.unpack('<I', b[end:end + 4])[0] == doc['buffers'][0]['byteLength']
    for acc in doc['accessors']:                          # every accessor fits its view
        v = doc['bufferViews'][acc['bufferView']]
        size = 12 if acc['type'] == 'VEC3' else 2         # 3 floats, or one uint16 index
        assert acc['count'] * size == v['byteLength'] and v['byteOffset'] % 4 == 0
    assert P.scratch_glb(P.COLOR_B) != b


def test_every_check_passes_with_a_page(pf):
    room = FakePage(pf['port'], 'room')                   # the person's page, not in VR: the verdict is a caption
    try:
        out = OPS['stage_preflight'](port=pf['port'], wait=10)
    finally:
        room.stop = True
    rows = _rows(out)
    assert list(rows) == ['server', 'presence', 'allow_rule', 'guard_hook', 'scratch', 'load', 'hot_swap',
                          'live_insert', 'say', 'listen'], out
    assert set(rows.values()) == {'PASS'}, out
    assert 'Preflight: pass.' in out.splitlines()[-1]
    assert pf['srv'].auth.health()['temp_keys'] == 0           # its key is unpaired when the test page closes
    seen = [c['type'] for c in pf['pages'][0].seen]
    assert seen == ['reload', 'world_reload', 'load_set', 'load_set', 'say'], seen
    verdict = [c for c in room.seen if c['type'] == 'say']
    assert verdict and verdict[0]['voice'] is False and verdict[0]['from'] == 'preflight'
    assert verdict[0]['text'] == 'Preflight: pass.'
    # hot swap: the new room is in place and the old one kept; nothing outside _preflight changed
    d = pf['scenes'] / '_preflight'
    assert (d / 'scene.glb').read_bytes() == P.scratch_glb(P.COLOR_B)
    assert list((d / 'history').glob('scene_*.glb'))
    assert (pf['scenes'] / 'room' / 'scene.glb').read_bytes() == b'glTF'
    assert not (pf['scenes'] / 'room' / 'world.json').exists()
    # the synthetic ping is marked synthetic, and named the op (not a listener)
    ping = [e for e in _get(pf['port'], 'live/inbox?who=op&since=0')[1]['events'] if e['type'] == P.PING]
    assert ping and ping[0]['synthetic'] is True and ping[0]['scene'] == '_preflight'
    assert _get(pf['port'], 'live/presence')[1]['listening'] == []


def test_a_second_run_overwrites_the_same_scratch_scene(pf, monkeypatch):
    monkeypatch.setattr(P, 'REUSE_AGE_S', 0.5)
    OPS['stage_preflight'](port=pf['port'], wait=10, say=False)
    for pg in pf['pages']:
        pg.stop = True
    time.sleep(1.2)                                       # the first test page is gone: the second run opens its own
    out = OPS['stage_preflight'](port=pf['port'], wait=10, say=False)
    assert 'wrote scene.glb' in out and 'not shown (say=False)' in out
    assert set(_rows(out).values()) == {'PASS'} and len(pf['pages']) == 2, out
    assert sorted(x.name for x in (pf['scenes']).iterdir() if x.name.startswith('_preflight')) == ['_preflight']


def test_someone_in_vr_runs_only_what_they_cannot_see(pf):
    room = FakePage(pf['port'], 'room')
    _post(pf['port'], 'live/event?scene=room', {'type': 'headset', 'state': 'entered VR', 'page': 'quest'})
    P_launch = P._launch_page
    try:
        P._launch_page = lambda url: pytest.fail('no test page while someone is in VR')
        out = OPS['stage_preflight'](port=pf['port'], wait=10)
    finally:
        P._launch_page = P_launch
        room.stop = True
        _post(pf['port'], 'live/event?scene=room', {'type': 'vr_exit', 'page': 'quest'})
    rows = _rows(out)
    for name in ('load', 'hot_swap', 'live_insert', 'say'):
        assert rows[name] == 'SKIPPED', out
    assert rows['listen'] == 'PASS' and rows['scratch'] == 'PASS' and 'IN VR in room' in out
    assert 'load, hot_swap, live_insert, say skipped in VR' in out and 'said in the headset (room)' in out
    say = [c for c in room.seen if c['type'] == 'say']
    assert len(say) == 1 and say[0]['voice'] is False      # the headset speaks it; a desktop page on room only shows it
    assert say[0]['text'] == 'Preflight: pass, 4 checks skipped in VR.'
    assert [c for c in room.seen if c['type'] != 'say'] == []


def test_no_server_is_blocked_with_the_way_to_start_one(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'empty'))
    monkeypatch.setattr(P, 'settings_files', lambda: [('~/.claude/settings.json', GOOD, None)])
    out = OPS['stage_preflight']()
    rows = _rows(out)
    assert rows['server'] == 'BLOCKED' and 'next: stage_start(scenes=' in out
    assert rows['allow_rule'] == 'PASS' and rows['guard_hook'] == 'PASS'
    assert {rows[n] for n in ('presence', 'scratch', 'load', 'hot_swap', 'live_insert', 'say', 'listen')} == {'SKIPPED'}
    assert 'Preflight blocked: no stage server is running.' in out.splitlines()[-1]


def test_a_scratch_folder_preflight_did_not_make_is_left_alone(pf):
    d = pf['scenes'] / '_preflight'
    d.mkdir()
    (d / 'notes.txt').write_text('mine', encoding='utf-8')
    out = OPS['stage_preflight'](port=pf['port'], wait=10, say=False)
    assert _rows(out)['scratch'] == 'BLOCKED' and 'did not make' in out
    assert sorted(x.name for x in d.iterdir()) == ['notes.txt']
    assert pf['pages'] == []


def test_an_older_server_without_the_ping_says_restart(pf, monkeypatch):
    monkeypatch.setattr(PR, 'INBOX_TYPES', PR.INBOX_TYPES - {P.PING})
    out = OPS['stage_preflight'](port=pf['port'], wait=10, say=False)
    assert _rows(out)['listen'] == 'BLOCKED' and 'stage_restart when nobody is in VR' in out
    assert 'Preflight blocked: the listen check failed.' in out


def test_headless_false_opens_no_browser(pf, monkeypatch):
    monkeypatch.setattr(P, '_launch_page', lambda url: pytest.fail('headless=False opens no browser'))
    out = OPS['stage_preflight'](port=pf['port'], headless=False, say=False)
    rows = _rows(out)
    assert rows['load'] == 'BLOCKED' and '/?scene=_preflight in a browser' in out
    assert rows['hot_swap'] == rows['live_insert'] == rows['say'] == 'SKIPPED'


# ---- the settings: read only -------------------------------------------------------------------------------------

def test_rule_matching():
    assert P.rule_covers('Bash(python -m ismail:*)') == 'Bash'
    assert P.rule_covers('PowerShell(*)') == 'PowerShell'
    assert P.rule_covers('Bash') == 'Bash'
    assert P.rule_covers('Bash(python -m ismail *)') == 'Bash'
    assert P.rule_covers('Bash(python -m ismail.stage.ear:*)') is None
    assert P.rule_covers('Read(*)') is None


def _settings_home(tmp_path, monkeypatch, settings):
    home, work = tmp_path / 'home', tmp_path / 'work'
    (home / '.claude').mkdir(parents=True)
    work.mkdir()
    f = home / '.claude' / 'settings.json'
    f.write_text(json.dumps(settings), encoding='utf-8')
    monkeypatch.setattr(P, '_home', lambda: home)
    monkeypatch.setattr(P, 'REPO', work)
    monkeypatch.chdir(work)
    return f


def test_missing_rule_and_hook_are_blocked_with_the_exact_lines_and_nothing_is_written(tmp_path, monkeypatch):
    f = _settings_home(tmp_path, monkeypatch, {'permissions': {'allow': ['Read(*)']}, 'hooks': {}})
    before, mtime = f.read_bytes(), f.stat().st_mtime_ns
    files = P.settings_files()
    assert [x[0] for x in files] == ['~/.claude/settings.json']
    with pytest.raises(P.Blocked) as e:
        P.check_allow(files)
    assert '"Bash(python -m ismail:*)"' in e.value.next and '~/.claude/settings.json' in e.value.next
    ok, err = P.check_hook(files)
    assert ok is None and '"PreToolUse"' in err.next and '~/.claude/settings.json' in err.next
    entry = json.loads(err.next[err.next.index('{'):])
    assert entry['matcher'] == 'Bash|PowerShell|Write|Edit|MultiEdit'
    assert 'skills/ismail/hooks/named_ops_guard.py' in entry['hooks'][0]['command']
    assert 'os.path.isfile(p)' in entry['hooks'][0]['command']
    assert f.read_bytes() == before and f.stat().st_mtime_ns == mtime
    assert not (tmp_path / 'work' / '.claude').exists()


def test_a_deny_rule_and_a_hook_with_a_wrong_path_are_blocked(tmp_path, monkeypatch):
    bad = {'permissions': {'allow': ['Bash(python -m ismail:*)'], 'deny': ['Bash(python:*)']},
           'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
               {'type': 'command', 'command': "python -S -c \"p = '/nowhere/named_ops_guard.py'\""}]}]}}
    _settings_home(tmp_path, monkeypatch, bad)
    files = P.settings_files()
    with pytest.raises(P.Blocked) as e:
        P.check_allow(files)
    assert 'deny' in e.value.next
    ok, err = P.check_hook(files)
    assert 'which is not there' in err.detail


def test_the_project_settings_are_read_too(tmp_path, monkeypatch):
    _settings_home(tmp_path, monkeypatch, {})
    proj = tmp_path / 'work' / '.claude'
    proj.mkdir()
    (proj / 'settings.local.json').write_text(json.dumps(GOOD), encoding='utf-8')
    files = P.settings_files()
    assert len(files) == 2
    assert 'Bash(python -m ismail:*)' in P.check_allow(files)
    assert P.check_hook(files)[1] is None

