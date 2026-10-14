"""Named ops for every live edit: stage_asset_install, stage_world update=, stage_restart, and the Claude Code guard
that refuses the off-path forms (python -c, copies into scenes, killing the stage, hand edits of world.json)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from ismail.stage import ops as SO
from ismail.stage import presence as PR
from ismail.stage import server as S
from test_stage import FakePage, _get, _post, stage  # noqa: F401

GUARD = Path(__file__).resolve().parents[1] / 'skills' / 'ismail' / 'hooks' / 'named_ops_guard.py'
GLB = b'glTF\x02\x00\x00\x00' + b'\x00' * 16


def _file(tmp, name, data=GLB):
    f = tmp / 'incoming' / name
    f.parent.mkdir(exist_ok=True)
    f.write_bytes(data) if isinstance(data, bytes) else f.write_text(data, encoding='utf-8')
    return f


# ---- stage_asset_install -----------------------------------------------------------------------------------------

def test_install_a_room_keeps_the_old_one_and_reloads_the_page(stage, tmp_path):
    page = FakePage(stage['port'], 'room')
    try:
        new = _file(tmp_path, 'export.glb', GLB + b'new room')
        out = OPS['stage_asset_install'](scene='room', file=str(new), kind='scene')
        room = stage['scenes'] / 'room'
        assert (room / 'scene.glb').read_bytes() == GLB + b'new room'
        kept = list((room / 'history').glob('scene_*.glb'))
        assert len(kept) == 1 and kept[0].read_bytes() == b'glTF' and 'the old one is in' in out
        assert new.is_file()                                         # a copy: the source stays where it was
        assert [c['type'] for c in page.seen] == ['reload'] and 'the page: reload' in out
        OPS['stage_asset_install'](scene='room', file=str(new), kind='scene')
        assert len(list((room / 'history').glob('scene_*.glb'))) == 2  # a second install in the same second
    finally:
        page.stop = True


def test_install_a_manifest_with_no_page_open(stage, tmp_path):
    new = _file(tmp_path, 'pieces.json', '{"objects": {"chair": {}}}')
    out = OPS['stage_asset_install'](scene='room', file=str(new), kind='manifest', scenes=str(stage['scenes']))
    assert json.loads((stage['scenes'] / 'room' / 'manifest.json').read_text(encoding='utf-8'))['objects'] == {'chair': {}}
    assert list((stage['scenes'] / 'room' / 'history').glob('manifest_*.json'))
    assert 'takes it at its next load' in out


def test_install_an_actor_goes_to_the_assets_scene(stage, tmp_path):
    sc = stage['scenes']
    body = _file(tmp_path, 'body_a.glb')
    out = OPS['stage_asset_install'](scene='room', file=str(body), kind='actor', scenes=str(sc))
    assert (sc / 'room' / 'actors' / 'body_a.glb').read_bytes() == GLB and 'next page load' in out
    assert not (sc / 'room' / 'history').exists()                    # nothing was there to keep
    (sc / 'attic' / 'world.json').write_text('{"assets": "room"}', encoding='utf-8')
    out = OPS['stage_asset_install'](scene='attic', file=str(body), kind='actor', name='body_b', scenes=str(sc))
    assert (sc / 'room' / 'actors' / 'body_b.glb').is_file() and not (sc / 'attic' / 'actors').exists()
    assert 'takes its bodies from room' in out
    OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'body_a.glb', GLB + b'v2')), kind='actor',
                               scenes=str(sc))
    assert list((sc / 'room' / 'history').glob('actors_body_a_*.glb'))


def test_install_a_tree_reloads_the_trees(stage, tmp_path):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'tall.glb')), kind='tree', name='tree_3')
        OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'tall.json', '{"height": 7}')), kind='tree',
                                   name='tree_3')
        trees = stage['scenes'] / 'room' / 'trees'
        assert (trees / 'tree_3.glb').is_file() and (trees / 'tree_3.json').is_file()
        assert [c['type'] for c in page.seen] == ['trees_reload', 'trees_reload']
    finally:
        page.stop = True


def test_install_refuses_with_the_next_step(stage, tmp_path):
    sc = str(stage['scenes'])
    glb = _file(tmp_path, 'thing.glb')
    with pytest.raises(OpError, match='no file .*give file='):
        OPS['stage_asset_install'](scene='room', file=str(tmp_path / 'nothing.glb'), kind='scene', scenes=sc)
    with pytest.raises(OpError, match=r'a scene is a \.glb file'):
        OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'pic.png', b'png')), kind='scene', scenes=sc)
    with pytest.raises(OpError, match='not a binary glTF'):
        OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'fake.glb', b'nope')), kind='scene', scenes=sc)
    with pytest.raises(OpError, match='not a JSON object'):
        OPS['stage_asset_install'](scene='room', file=str(_file(tmp_path, 'bad.json', '[1')), kind='manifest', scenes=sc)
    with pytest.raises(OpError, match='kind is one of .*stage_prop_pin'):
        OPS['stage_asset_install'](scene='room', file=str(glb), kind='prop', scenes=sc)
    with pytest.raises(OpError, match="no scene 'kitchen'.*stage_scenes"):
        OPS['stage_asset_install'](scene='kitchen', file=str(glb), kind='scene', scenes=sc)
    with pytest.raises(OpError, match='tree_<k>, k from 1 to 24'):
        OPS['stage_asset_install'](scene='room', file=str(glb), kind='tree', name='tree_99', scenes=sc)
    with pytest.raises(OpError, match='already that file'):
        OPS['stage_asset_install'](scene='room', file=str(stage['scenes'] / 'room' / 'scene.glb'), kind='scene',
                                   scenes=sc)
    assert (stage['scenes'] / 'room' / 'scene.glb').read_bytes() == b'glTF'    # nothing changed
    assert not (stage['scenes'] / 'room' / 'history').exists()


# ---- stage_world update= -----------------------------------------------------------------------------------------

def test_world_update_changes_only_the_keys_given(stage):
    sc = str(stage['scenes'])
    OPS['stage_world'](scene='room', scenes=sc, world={'actors': {'person_a': 'body_a'}, 'floor': 0.09, 'sky': 'night'})
    out = OPS['stage_world'](scene='room', scenes=sc, update={'floor': 0.2, 'sky': None})
    w = json.loads((stage['scenes'] / 'room' / 'world.json').read_text(encoding='utf-8'))
    assert w['actors'] == {'person_a': 'body_a'} and w['floor'] == 0.2 and 'sky' not in w
    assert out.startswith('updated floor, sky in')
    assert list((stage['scenes'] / 'room' / 'history').glob('world_*.json'))


def test_world_update_and_world_together_is_an_error(stage):
    with pytest.raises(OpError, match='not both'):
        OPS['stage_world'](scene='room', scenes=str(stage['scenes']), world={'floor': 1}, update={'floor': 2})
    with pytest.raises(OpError):
        OPS['stage_world'](scene='room', scenes=str(stage['scenes']), update={})


def test_world_update_is_checked_and_leaves_the_file_alone(stage):
    sc = str(stage['scenes'])
    OPS['stage_world'](scene='room', scenes=sc, world={'floor': 0.09})
    before = (stage['scenes'] / 'room' / 'world.json').read_text(encoding='utf-8')
    with pytest.raises(OpError, match='keep_out'):
        OPS['stage_world'](scene='room', scenes=sc, update={'keep_out': [[1, 0, 0, 1, 0, 1]]})
    assert (stage['scenes'] / 'room' / 'world.json').read_text(encoding='utf-8') == before


def test_world_doc_says_world_resets_what_it_leaves_out():
    doc = ' '.join(SO.stage_world.__doc__.split())
    assert 'world= replaces the whole file: every key left out goes back to its default' in doc


# ---- stage_restart -----------------------------------------------------------------------------------------------

@pytest.fixture()
def fake_restart(monkeypatch):
    """stop and start recorded, not run: the fixture's server lives in the test process."""
    calls = []
    monkeypatch.setattr(SO, 'stage_stop', lambda **kw: calls.append(('stop', kw)) or 'stopped (fake)')
    monkeypatch.setattr(SO, 'stage_start', lambda **kw: calls.append(('start', kw)) or 'started (fake)')
    PR.reset()
    yield calls
    PR.reset()


def _in_vr(port, scene):
    _post(port, f'live/state?scene={scene}', {'scene': scene, 'xr': True, 'camera': {'position': [0, 1.6, 0]}})


def test_restart_is_refused_in_vr_and_says_so_in_the_headset(stage, fake_restart):
    page = FakePage(stage['port'], 'room')
    try:
        _in_vr(stage['port'], 'room')
        with pytest.raises(OpError, match=r'in VR in room.*vr_exit.*stage_ask.*now=True'):
            OPS['stage_restart'](port=stage['port'], who='Builder')
        says = [c for c in page.seen if c['type'] == 'say']
        assert says and 'Builder wants to restart the room server' in says[0]['text']
        assert fake_restart == []
    finally:
        page.stop = True


def test_restart_now_goes_ahead_in_vr(stage, fake_restart):
    page = FakePage(stage['port'], 'room')
    try:
        _in_vr(stage['port'], 'room')
        out = OPS['stage_restart'](port=stage['port'], now=True)
        assert [c[0] for c in fake_restart] == ['stop', 'start'] and 'now=True' in out
        assert not [c for c in page.seen if c['type'] == 'say']
    finally:
        page.stop = True


def test_restart_with_no_page_uses_the_start_args(stage, fake_restart):
    S.register(stage['port'], 'http', '127.0.0.1', start={'host': '0.0.0.0', 'footage': 'D:/clips', 'esbuild': 'D:/eb',
                                                          'tls': False})
    out = OPS['stage_restart'](scenes=str(stage['scenes']))
    assert fake_restart == [('stop', {'port': stage['port']}),
                            ('start', {'scenes': str(stage['scenes']), 'port': stage['port'], 'host': '0.0.0.0',
                                       'footage': 'D:/clips', 'esbuild': 'D:/eb'})]
    assert out.startswith(f'restarted the stage on port {stage["port"]} for {stage["scenes"]}') and 'health:' in out


def test_restart_refuses_tls_and_unknown_ports(stage, fake_restart):
    with pytest.raises(OpError, match='no matching stage server'):
        OPS['stage_restart'](port=1)
    S.register(stage['port'], 'http', '127.0.0.1', start={'tls': True})
    with pytest.raises(OpError, match='https'):
        OPS['stage_restart'](port=stage['port'])
    assert fake_restart == []


def test_restart_is_not_batchable():
    assert 'stage_restart' in SO.NOT_IN_BATCH


# ---- the guard ---------------------------------------------------------------------------------------------------

@pytest.fixture()
def guard_env(tmp_path, monkeypatch):
    sreg, lreg = tmp_path / 'sreg', tmp_path / 'lreg'
    sreg.mkdir()
    lreg.mkdir()
    known = tmp_path / 'known_scenes'
    (sreg / '8870.json').write_text(json.dumps({'pid': 4242, 'port': 8870, 'scenes': str(known)}), encoding='utf-8')
    (lreg / '5151.json').write_text(json.dumps({'pid': 5151, 'port': 9001, 'project': 'song_a'}), encoding='utf-8')
    proj = tmp_path / 'proj'
    (proj / 'scenes' / 'hall').mkdir(parents=True)
    (proj / 'scenes' / 'hall' / 'scene.glb').write_bytes(b'glTF')
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(sreg))
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(lreg))
    return {'known': known, 'proj': proj}


def _guard(tool, value, cwd=''):
    key = 'file_path' if tool in ('Write', 'Edit', 'MultiEdit') else 'command'
    payload = value if isinstance(value, str) and tool == 'raw' else json.dumps(
        {'tool_name': tool, 'tool_input': {key: value}, 'cwd': cwd})
    r = subprocess.run([sys.executable, str(GUARD)], input=payload, capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


VR = 'D:/song_a/video/vr/scenes/room'
TEMP = 'C:/Users/someone/AppData/Local/Temp/claude/session/scratchpad'

REFUSED = [
    # python -c and heredocs with ismail code
    ('Bash', 'python -c "from ismail.stage import link; print(link.servers())"', 'python -m ismail'),
    ('Bash', "python3 -c 'import ismail'", 'python -m ismail'),
    ('PowerShell', 'py -3 -c "import ismail.api as a; a.OPS"', 'python -m ismail'),
    ('Bash', "python - <<'EOF'\nfrom ismail.stage import link\nprint(1)\nEOF", 'python -m ismail'),
    ('PowerShell', "@'\nimport ismail\n'@ | python -", 'python -m ismail'),
    # copies into a scenes folder, both slash styles and drive forms
    ('Bash', f'cp body.glb {VR}/actors/', 'stage_asset_install'),
    ('Bash', 'cp body.glb /d/song_a/video/vr/scenes/room/actors/body.glb', 'stage_asset_install'),
    ('Bash', 'mv new.glb D:\\song_a\\video\\vr\\scenes\\room\\scene.glb', 'stage_asset_install'),
    ('Bash', 'cp -t /d/song_a/video/vr/scenes/room a.glb', 'stage_asset_install'),
    ('Bash', f'cp {TEMP}/body.glb /d/work/scenes/room/actors/body.glb', 'stage_asset_install'),
    ('Bash', 'cp /c/Users/someone/AppData/Local/Temp/claude/x/a.glb D:/work/scenes/room/scene.glb', 'stage_asset_install'),
    ('PowerShell', 'copy /Y C:\\work\\a.glb D:\\song_a\\video\\vr\\scenes\\room\\scene.glb', 'stage_asset_install'),
    ('PowerShell', 'xcopy C:\\work\\a.glb D:\\song_a\\video\\vr\\scenes\\room\\ /Y', 'stage_asset_install'),
    ('PowerShell', 'robocopy D:\\work D:\\song_a\\video\\vr\\scenes\\room scene.glb /XO', 'stage_asset_install'),
    ('PowerShell', 'Copy-Item -Path C:\\Users\\someone\\AppData\\Local\\Temp\\claude\\s\\a.glb -Destination '
                   'D:\\work\\scenes\\room\\scene.glb -Force', 'stage_asset_install'),
    ('PowerShell', f'Move-Item a.glb {VR}/actors/b.glb', 'stage_asset_install'),
    ('PowerShell', f'powershell -NoProfile -Command "Copy-Item a.glb {VR}/scene.glb"', 'stage_asset_install'),
    ('Bash', f'python -c "import shutil; shutil.copy(\'a.glb\', \'{VR}/scene.glb\')"', 'stage_asset_install'),
    ('Bash', f'python -c "import shutil; shutil.copy(\'{TEMP}/w.json\', \'D:/work/scenes/room/world.json\')"',
     'stage_world'),
    # killing the stage or a live engine
    ('PowerShell', 'Stop-Process -Name python -Force', 'stage_restart'),
    ('PowerShell', 'Get-Process python | Stop-Process', 'stage_restart'),
    ('PowerShell', 'Get-NetTCPConnection -LocalPort 8862 | % { Stop-Process -Id $_.OwningProcess }', 'stage_restart'),
    ('PowerShell', 'Stop-Process -Id 5151', 'live_stop'),
    ('Bash', 'taskkill /F /IM python.exe', 'stage_restart'),
    ('Bash', 'kill 4242', 'stage_restart'),
    ('Bash', 'kill -9 5151', 'live_stop'),
    ('Bash', 'pkill -f ismail.stage.server', 'stage_restart'),
    # hand edits of world.json, scene.glb, manifest.json
    ('Edit', 'D:\\song_a\\video\\vr\\scenes\\room\\world.json', 'stage_world'),
    ('Write', '/d/song_a/video/vr/scenes/room/manifest.json', 'stage_asset_install'),
    ('MultiEdit', f'{VR}/scene.glb', 'stage_asset_install'),
    ('Bash', 'echo {} > /d/song_a/video/vr/scenes/room/world.json', 'stage_world'),
    ('PowerShell', "Set-Content -Path D:\\song_a\\video\\vr\\scenes\\room\\world.json -Value '{}'", 'stage_world'),
    ('Bash', f"sed -i 's/0.1/0.2/' {VR}/world.json", 'stage_world'),
]


@pytest.mark.parametrize('tool,value,op', REFUSED, ids=[f'{t}:{v[:40]}' for t, v, _ in REFUSED])
def test_guard_refuses_and_names_the_op(guard_env, tool, value, op):
    code, err = _guard(tool, value)
    assert code == 2, (tool, value)
    assert err.startswith('ismail named-ops guard:') and op in err and 'python -m ismail' in err


def test_guard_knows_scenes_from_the_registry_and_from_disk(guard_env):
    assert _guard('Bash', f'cp a.glb {guard_env["known"]}/room/scene.glb')[0] == 2
    assert _guard('Bash', 'cp a.glb scenes/hall/', cwd=str(guard_env['proj']))[0] == 2
    assert _guard('Bash', 'cp a.glb scenes/hall/', cwd=str(guard_env['known']))[0] == 0   # no hall there


PASSES = [
    ('Bash', 'python -m ismail stage_status'),
    ('Bash', 'python -m ismail stage_world scene=room \'update={"floor": 0.1}\''),
    ('PowerShell', f'py -m ismail stage_asset_install scene=room file={TEMP}/a.glb kind=scene'),
    ('Bash', 'python -m ismail ops | head -5'),
    ('Bash', 'cp a.txt b.txt'),
    ('Bash', f'cp {VR}/scene.glb D:/backup/'),
    ('PowerShell', 'Copy-Item a.txt D:\\work\\other\\'),
    ('Bash', 'git status && git log --oneline -3'),
    ('Bash', f'git add {VR}/world.json'),
    ('Bash', 'git commit -m "no more kill python by hand"'),
    ('PowerShell', 'Stop-Process -Name notepad'),
    ('Bash', 'kill 1234'),
    ('Bash', 'taskkill /IM notepad.exe'),
    ('Bash', 'python -c "print(1)"'),
    ('Bash', 'python tools/report.py --out report.txt'),
    ('Edit', f'{VR}/edits.json'),
    ('Write', 'D:/work/world.json'),
    ('Read', f'{VR}/world.json'),
]


@pytest.mark.parametrize('tool,value', PASSES, ids=[f'{t}:{v[:40]}' for t, v in PASSES])
def test_guard_lets_everything_else_through(guard_env, tool, value):
    assert _guard(tool, value) == (0, '')


def test_guard_never_blocks_on_bad_input(guard_env):
    for raw in ('not json', '[]', '{"tool_name": "Bash", "tool_input": "x"}', ''):
        r = subprocess.run([sys.executable, str(GUARD)], input=raw, capture_output=True, text=True, timeout=30)
        assert (r.returncode, r.stderr) == (0, '')


def test_guard_decides_in_well_under_100_ms(guard_env):
    sys.path.insert(0, str(GUARD.parent))
    try:
        import named_ops_guard as g
    finally:
        sys.path.remove(str(GUARD.parent))
    g._CACHE.clear()
    t = time.perf_counter()
    for tool, value, _ in REFUSED:
        key = 'file_path' if tool in ('Write', 'Edit', 'MultiEdit') else 'command'
        assert g.decide({'tool_name': tool, 'tool_input': {key: value}})
    assert (time.perf_counter() - t) / len(REFUSED) < 0.1
