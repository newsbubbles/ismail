"""The stage without a headset: the server on a scratch scenes folder, a fake page that answers commands the way
live.js does, and the stage_* ops against both."""
import json
from pathlib import Path
import threading
import time
import urllib.request

import pytest

from ismail.api import OPS, OpError
from ismail.stage import server as S
from ismail.stage import world as W


def _get(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/{path}', timeout=10) as r:
        body = r.read()
        return r.status, (json.loads(body) if r.headers.get('Content-Type', '').startswith('application/json') else body)


def _post(port, path, obj):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/{path}', data=json.dumps(obj).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


@pytest.fixture()
def stage(tmp_path, monkeypatch):
    scenes = tmp_path / 'scenes'
    for name in ('room', 'attic'):
        (scenes / name).mkdir(parents=True)
        (scenes / name / 'scene.glb').write_bytes(b'glTF')
        (scenes / name / 'manifest.json').write_text('{"objects": {}}', encoding='utf-8')
    (scenes / 'stage.json').write_text('{"default": "room"}', encoding='utf-8')
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'reg'))
    S.LIVE.clear()
    S.configure(scenes)
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    port = srv.server_address[1]
    S.register(port, 'http', '127.0.0.1')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {'port': port, 'scenes': scenes, 'srv': srv}
    srv.shutdown()
    srv.server_close()


class FakePage:
    """Posts its state and answers each command with cmd_done, like live.js (refuse= types answer with an error)."""

    def __init__(self, port, scene, refuse=()):
        self.port, self.scene, self.refuse, self.stop = port, scene, set(refuse), False
        self.seen = []
        _post(port, f'live/state?scene={scene}', {'scene': scene, 'xr': False, 'camera': {'position': [1, 2, 1.6]}})
        self.since = _get(port, f'live/cmd?scene={scene}&since=-1')[1]['last']
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        try:
            self.loop()
        except OSError:                               # the test's server closed under a poll: the page is gone
            pass

    def loop(self):
        while not self.stop:
            got = _get(self.port, f'live/cmd?scene={self.scene}&since={self.since}&wait=1')[1]
            self.since = got['last']
            for c in got['cmds']:
                self.seen.append(c)
                if c['type'] in self.refuse:
                    ev = {'type': 'cmd_done', 'cmd_id': c['id'], 'cmd': c['type'], 'ok': False, 'error': 'no object chair', 'ms': 3}
                else:
                    ev = {'type': 'cmd_done', 'cmd_id': c['id'], 'cmd': c['type'], 'ok': True, 'ms': 4,
                          'result': {k: v for k, v in c.items() if k not in ('id', 'ts', 'type')}}
                _post(self.port, f'live/event?scene={self.scene}', ev)


def test_serves_page_scenes_and_world(stage):
    p = stage['port']
    assert _get(p, 'stage')[1] == {'scenes': ['attic', 'room'], 'default': 'room', 'code': S.code_version()}
    assert _get(p, 'scenes/room/manifest.json')[1] == {'objects': {}}
    assert b'<html' in _get(p, 'index.html')[1].lower()
    w = _get(p, 'world?scene=room')[1]
    assert w['actors'] == {} and w['floor'] == 0.0
    with pytest.raises(urllib.error.HTTPError):
        _get(p, 'scenes/../server.py')


def test_busy_port_is_refused(stage):
    with pytest.raises(OSError):
        S.Server(('127.0.0.1', stage['port']), S.Handler)


def test_session_files_go_to_the_state_folder(stage):
    _post(stage['port'], 'clientlog', {'page': 'x', 'scene': 'room', 'entries': [{'level': 'info', 'msg': 'hi'}]})
    assert (stage['scenes'] / '_stage' / 'clientlog.jsonl').is_file()
    assert 'room' in [n for n in S.scene_names()] and '_stage' not in S.scene_names()


def test_page_command_op_waits_for_the_answer(stage):
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_object_set'](scene='room', object='chair', location=[1, 2, 0])
        assert out.startswith('set (4 ms):') and '"location": [1, 2, 0]' in out
        assert page.seen[-1]['type'] == 'set' and 'offset' not in page.seen[-1]    # None fields are left out
    finally:
        page.stop = True


def test_a_refused_command_raises_with_the_reason(stage):
    page = FakePage(stage['port'], 'room', refuse={'select'})
    try:
        with pytest.raises(OpError, match='no object chair'):
            OPS['stage_object_select'](scene='room', object='chair')
    finally:
        page.stop = True


def test_no_page_says_where_to_open_it(stage):
    with pytest.raises(OpError, match=r'no page is showing scene .room.*\?scene=room'):
        OPS['stage_say'](scene='room', text='hello')


def test_unknown_command_and_scene(stage):
    page = FakePage(stage['port'], 'room')
    try:
        with pytest.raises(OpError, match='unknown command'):
            OPS['stage_cmd'](scene='room', type='fly_to_the_moon')
        with pytest.raises(OpError, match='use stage_object_set'):
            OPS['stage_cmd'](scene='room', type='set', fields={'object': 'chair'})
        with pytest.raises(OpError, match="no running stage server has a scene 'kitchen'"):
            OPS['stage_say'](scene='kitchen', text='hi')
    finally:
        page.stop = True


def test_events_filtered_by_type(stage):
    p = stage['port']
    _post(p, 'live/event?scene=room', [{'type': 'gesture', 'hand': 'left'}, {'type': 'voice_message', 'text': 'more light'}])
    out = OPS['stage_events'](scene='room', types=['voice_message'])
    assert out.splitlines()[0].startswith('last 2;') and 'more light' in out and 'gesture' not in out


def test_world_roundtrip_and_checks(stage):
    sc = str(stage['scenes'])
    out = OPS['stage_world'](scene='room', scenes=sc, world={'actors': {'person_a': 'body_a'}, 'floor': 0.09,
                                                            'build': {'script': 'video/rooms/room.py'}})
    assert 'body_a' in out and _get(stage['port'], 'world?scene=room')[1]['floor'] == 0.09
    with pytest.raises(OpError, match='keep_out'):
        OPS['stage_world'](scene='room', scenes=sc, world={'keep_out': [[1, 0, 0, 1, 0, 1]]})
    b = W.build_of(stage['scenes'], 'room')
    assert b['script'] == (stage['scenes'] / 'room' / '../../../..' / 'video' / 'rooms' / 'room.py').resolve()
    assert b['line'] == ['room'] and b['passes'] == []


def test_update_notes_reach_the_page(stage):
    OPS['stage_note'](scenes=str(stage['scenes']), title='Panels on top', level='important')
    got = _get(stage['port'], 'updates.json')[1]
    assert got['updates'][-1]['title'] == 'Panels on top' and got['updates'][-1]['level'] == 'important'


def test_scene_go_answers_from_the_new_scene(stage):
    p = stage['port']
    _post(p, 'live/state?scene=room', {'scene': 'room'})

    def page():                                       # takes the command on room, answers on attic, like scenes.js
        since = _get(p, 'live/cmd?scene=room&since=-1')[1]['last']
        for _ in range(50):
            got = _get(p, f'live/cmd?scene=room&since={since}&wait=1')[1]
            since = got['last']
            if got['cmds']:
                _post(p, 'live/event?scene=attic', {'type': 'scene_switched', 'from': 'room', 'to': 'attic', 'objects': 3, 'ms': 900})
                return
    threading.Thread(target=page, daemon=True).start()
    time.sleep(0.2)
    out = OPS['stage_scene_go'](scene='room', name='attic')
    assert 'switched to attic' in out and 'scene="attic"' in out


def test_status_lists_the_server(stage):
    out = OPS['stage_status']()
    assert str(stage['scenes']) in out and 'well: ' in out and '/32 workers busy' in out


def test_no_server_says_how_to_start_one(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'empty'))
    with pytest.raises(OpError, match=r'no stage server is running; start one with stage_start'):
        OPS['stage_say'](scene='room', text='hello')


def test_a_page_seen_just_now_counts(stage, monkeypatch):
    """age_s 0.0 (Linux and macOS clocks) is a live page, not a missing one (CI caught `age or 1e9`)."""
    from ismail.stage import link
    page = FakePage(stage['port'], 'room')
    real = link.page_state
    monkeypatch.setattr(link, 'page_state', lambda rec, scene: {**real(rec, scene), 'age_s': 0.0})
    try:
        assert OPS['stage_object_deselect'](scene='room').startswith('deselect (')
    finally:
        page.stop = True


def _scene(d, name, world=None, edits=None):
    (d / name).mkdir(parents=True, exist_ok=True)
    (d / name / 'scene.glb').write_bytes(b'glTF')
    if world is not None:
        (d / name / 'world.json').write_text(json.dumps(world), encoding='utf-8')
    if edits is not None:
        (d / name / 'edits.json').write_text(json.dumps(edits), encoding='utf-8')


def test_a_derived_scene_builds_from_its_parents_full_build(tmp_path):
    from ismail.stage.ops import export_plan
    song = tmp_path / 'song'
    d = song / 'video' / 'vr' / 'scenes'
    (song / 'video' / 'rooms').mkdir(parents=True)
    for f in ('club.py', 'remodel.py', 'dawn.py'):
        (song / 'video' / 'rooms' / f).write_text('# room', encoding='utf-8')
    _scene(d, 'club', {'build': {'script': 'video/rooms/club.py', 'env': {'VR_DETAIL': '1'}}, 'floor': 0.09},
           {'objects': {'stool': {'location': [1, 2, 0]}, 'door': {'location': [0, 0, 0]}}})
    _scene(d, 'now', {'derives_from': 'club', 'pass': 'video/rooms/remodel.py', 'pass_env': {'VARIANT': '1'}},
           {'objects': {'stool': {'location': [5, 5, 0]}}})
    _scene(d, 'dawn', {'derives_from': 'now', 'pass': 'video/rooms/dawn.py'})
    b, env = export_plan(d, 'dawn')
    assert b['line'] == ['dawn', 'now', 'club'] and b['script'].name == 'club.py'
    assert [p.name for p in b['passes']] == ['remodel.py', 'dawn.py']             # oldest first
    assert env['VR_DETAIL'] == '1' and env['VARIANT'] == '1' and env['VR_DIET'] == '1' and env['STAGE_SCENE'] == 'dawn'
    assert env['VR_EXPORT'].endswith('dawn') and env['VR_BRIDGE'].endswith('blender_bridge.py')
    merged = json.loads(Path(env['VR_EDITS']).read_text(encoding='utf-8'))
    assert merged['objects']['stool']['location'] == [5, 5, 0] and 'door' in merged['objects']   # the variant's win
    b0, env0 = export_plan(d, 'club')
    assert 'VR_PASS' not in env0 and 'VR_DIET' not in env0                        # a root scene builds as it always did


def test_lineage_refuses_loops_and_a_build_of_its_own(tmp_path):
    d = tmp_path / 'scenes'
    _scene(d, 'a', {'derives_from': 'b'})
    _scene(d, 'b', {'derives_from': 'a'})
    with pytest.raises(ValueError, match='loop'):
        W.lineage(d, 'a')
    assert any('no build of its own' in p for p in W.check_world({**W.DEFAULTS, 'derives_from': 'a', 'build': {'script': 'x.py'}}))


def test_scene_new_copies_the_page_pieces_and_writes_the_lineage(tmp_path):
    song = tmp_path / 'song'
    d = song / 'video' / 'vr' / 'scenes'
    (song / 'video' / 'rooms').mkdir(parents=True)
    (song / 'video' / 'rooms' / 'club.py').write_text('# room', encoding='utf-8')
    (song / 'video' / 'rooms' / 'remodel.py').write_text('# pass', encoding='utf-8')
    _scene(d, 'club', {'build': {'script': 'video/rooms/club.py'}, 'actors': {'p': 'body_a'}, 'floor': 0.09, 'sky': 'night'})
    (d / 'club' / 'trees').mkdir()
    (d / 'club' / 'trees' / 'tree_1.json').write_text('{}', encoding='utf-8')
    (d / 'club' / 'names.json').write_text('{"stool": "a stool"}', encoding='utf-8')
    (d / 'club' / 'takes').mkdir()                                                 # the person's: stays with its scene
    out = OPS['stage_scene_new'](name='now', source='club', scenes=str(d), pass_script='video/rooms/remodel.py')
    assert 'derives from club' in out and 'stage_scene_export' in out
    w = W.load_world(d, 'now')
    assert w['derives_from'] == 'club' and w['assets'] == 'club' and w['actors'] == {'p': 'body_a'} and w['sky'] == 'night'
    assert (d / 'now' / 'trees' / 'tree_1.json').is_file() and (d / 'now' / 'names.json').is_file()
    assert not (d / 'now' / 'takes').exists()
    with pytest.raises(OpError, match='exists already'):
        OPS['stage_scene_new'](name='now', source='club', scenes=str(d))


def test_a_fixed_pool_serves_thousands_of_requests_without_new_threads(stage):
    """The old server made a thread per request and kept them (MemoryError after ~40,500 in six hours)."""
    import concurrent.futures as cf
    p = stage['port']
    _get(p, 'health')
    before = threading.active_count()
    with cf.ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda _: _get(p, 'live/cmd?scene=room&since=0'), range(600)))
    h = _get(p, 'health')[1]
    assert h['served'] >= 600 and h['busy'] <= 1 and h['workers'] == 32
    assert threading.active_count() <= before + 32                 # at most the pool's workers, never one per request


def test_long_polls_are_capped_per_client(stage):
    p, srv = stage['port'], stage['srv']
    srv.longpoll_per_client = 2
    t0 = time.time()
    hold = [threading.Thread(target=_get, args=(p, 'live/events?scene=room&since=999&wait=3')) for _ in range(2)]
    for t in hold:
        t.start()
    time.sleep(0.4)
    assert _get(p, 'health')[1]['long_polls'] == 2
    _get(p, 'live/events?scene=room&since=999&wait=3')             # the third: answered at once, not after 3 s
    assert time.time() - t0 < 2.0
    for t in hold:
        t.join()
    assert _get(p, 'health')[1]['long_polls'] == 0


def test_a_full_disk_keeps_the_live_link_in_memory(stage, monkeypatch):
    def full(*a, **kw):
        raise OSError(28, 'No space left on device')
    monkeypatch.setattr(S.Path, 'write_text', full)
    monkeypatch.setattr('builtins.open', full)
    assert _post(stage['port'], 'live/state?scene=room', {'scene': 'room', 'mode': 'orbit'}) == {'ok': True}
    monkeypatch.undo()
    assert _post(stage['port'], 'live/event?scene=room', {'type': 'gesture'})['ok']
    st = _get(stage['port'], 'live/state?scene=room')[1]
    assert st['state']['mode'] == 'orbit'
    assert _get(stage['port'], 'health')[1]['disk_warned_s_ago'] is not None


def test_a_failed_rebuild_serves_the_last_good_bundle(stage, monkeypatch):
    import subprocess
    out = S.STATE / 'bundle.js'
    out.write_text('// the last good bundle', encoding='utf-8')
    import os
    os.utime(out, (1, 1))                                          # older than every page module: a rebuild is due
    monkeypatch.setattr(S, 'esbuild_path', lambda: 'esbuild')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: (_ for _ in ()).throw(OSError(28, 'No space left on device')))
    assert S.bundle() == out and out.read_text(encoding='utf-8') == '// the last good bundle'


# ---- presence: who is in the headset, who is listening, and no silent notes (presence.py)
from ismail.stage import presence as PR  # noqa: E402


def _voice_in(port, scene='room', file='voice/n1.webm', seconds='3.0'):
    return _post(port, f'live/event?scene={scene}', {'type': 'voice_in', 'file': file, 'seconds': seconds, 'via': 'test'})['ids'][0]


def _cmds(port, scene='room'):
    return _get(port, f'live/cmd?scene={scene}&since=0')[1]['cmds']


def test_a_note_nobody_hears_is_said_out_loud_and_kept(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.3)
    port = stage['port']
    # an op waiting for its own answer is not a listener
    _get(port, 'live/events?scene=room&since=0&who=op')
    nid = _voice_in(port)
    time.sleep(0.8)
    says = [c for c in _cmds(port) if c['type'] == 'say']
    assert says and says[-1]['text'] == PR.NOBODY
    p = _get(port, 'live/presence')[1]
    assert p['unread'] == 1 and p['listening'] == []
    unread = (stage['scenes'] / '_stage' / 'unread.jsonl').read_text(encoding='utf-8')
    assert json.loads(unread.splitlines()[-1])['event_id'] == nid
    heard = [e for e in _get(port, 'live/events?scene=room&limit=50')[1]['events'] if e['type'] == 'voice_heard']
    assert heard[-1]['state'] == 'unheard'


def test_a_listener_is_handed_the_note_and_the_headset_says_so(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.5)
    port = stage['port']
    start = _get(port, 'live/inbox?who=film')[1]['last']
    got = {}
    t = threading.Thread(target=lambda: got.update(_get(port, f'live/inbox?who=film&since={start}&wait=5')[1]))
    t.start()
    time.sleep(0.3)
    assert _get(port, 'live/version?scene=room')[1]['listening'] == ['film']
    _voice_in(port, scene='attic')
    t.join()
    assert [e['type'] for e in got['events']] == ['voice_in'] and got['events'][0]['scene'] == 'attic'
    time.sleep(0.8)
    cmds = _cmds(port, 'attic')
    assert [(c['type'], c['text']) for c in cmds] == [('say', 'Handed to film.')]
    assert _get(port, 'live/presence')[1]['unread'] == 0


def test_an_answered_note_gets_nothing_more(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.4)
    port = stage['port']
    _voice_in(port)
    _post(port, 'live/cmd?scene=room', {'type': 'say', 'text': 'got it'})
    time.sleep(0.8)
    assert [c.get('text') for c in _cmds(port)] == ['got it']


def test_following_a_scene_counts_as_listening_there(stage):
    port = stage['port']
    _get(port, 'live/events?scene=room&since=0')
    assert _get(port, 'live/version?scene=room')[1]['listening'] == ['unnamed']
    assert _get(port, 'live/version?scene=attic')[1]['listening'] == []


def test_hooks_run_on_entering_vr_and_presence_tracks_it(stage, tmp_path, monkeypatch):
    port = stage['port']
    out = stage['scenes'] / '_stage' / 'hooked.txt'
    cmd = ['python', '-c', 'import os; open("hooked.txt", "w").write(os.environ["STAGE_EVENT"] + " " + os.environ["STAGE_SCENE"])']
    home = tmp_path / 'stage_hooks.json'
    monkeypatch.setenv('ISMAIL_STAGE_HOOKS', str(home))
    # a hooks.json inside the scenes folder is ignored: the folder travels, so it never runs commands by itself
    (stage['scenes'] / '_stage' / 'hooks.json').write_text(json.dumps({'left_vr': [cmd]}), encoding='utf-8')
    home.write_text(json.dumps({'entered_vr': [cmd]}), encoding='utf-8')
    assert _get(port, 'live/presence')[1]['hooks'] == {'entered_vr': 1}
    _post(port, 'live/event?scene=attic', {'type': 'headset', 'state': 'entered VR', 'page': 'p1'})
    for _ in range(50):
        if out.is_file() and out.read_text():
            break
        time.sleep(0.1)
    assert out.read_text() == 'entered_vr attic'
    p = _get(port, 'live/presence')[1]
    assert p['in_vr'] and p['scene'] == 'attic' and p['hooks'] == {'entered_vr': 1}
    _post(port, 'live/event?scene=attic', {'type': 'headset', 'state': 'off', 'page': 'p1'})
    assert not _get(port, 'live/presence')[1]['in_vr']
    assert json.loads((stage['scenes'] / '_stage' / 'presence.json').read_text(encoding='utf-8'))['in_vr'] is False
    # the home file can trust this song's folder: then its hooks.json counts too
    home.write_text(json.dumps({'trust': [str(stage['scenes'])], 'entered_vr': [cmd]}), encoding='utf-8')
    assert _get(port, 'live/presence')[1]['hooks'] == {'entered_vr': 1, 'left_vr': 1}


def test_listen_and_presence_ops(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.3)
    port = stage['port']
    first = OPS['stage_listen'](who='tester', wait=0)
    last = int(first.split()[1].rstrip(';'))
    _voice_in(port)
    _post(port, 'live/event?scene=room', {'type': 'voice_message', 'file': 'voice/n1.webm', 'text': 'play something over Lucy'})
    out = OPS['stage_listen'](who='tester', since=last, wait=2)
    assert '[room]' in out and 'voice_message' in out and 'over Lucy' in out
    time.sleep(0.6)
    pr = OPS['stage_presence']()
    assert 'listening: tester' in pr and 'over Lucy' in pr and 'handed' in pr


def test_a_panel_can_ride_with_the_person(stage):
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_panel_show'](scene='room', title='Lucy', text='the amp moved', anchor='body', side='left',
                                      seconds=20)
        c = page.seen[-1]
        # a body panel is a message: it never holds the command queue unless asked to
        assert (c['type'], c['anchor'], c['side'], c['seconds'], c['wait']) == ('panel', 'body', 'left', 20, False)
        OPS['stage_say'](scene='room', text='the cables are in', sender='film agent')
        assert (page.seen[-1]['type'], page.seen[-1]['from']) == ('say', 'film agent')
        OPS['stage_panel_show'](scene='room', title='from the dev', anchor='body', sender='stage dev')
        c = page.seen[-1]
        assert c['from'] == 'stage dev' and 'side' not in c    # the page puts a sender on its own side
        assert 'width' not in c                       # the page picks the narrower body width
        assert 'panel' in out
        with pytest.raises(OpError, match="anchor is 'world' or 'body'"):
            OPS['stage_panel_show'](scene='room', title='x', anchor='hip')
    finally:
        page.stop = True


def test_an_ask_says_who_asks(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_ask'](scene='room', text='Brighter lamp?', seconds=5, sender='film agent')
        c = page.seen[-1]
        assert (c['type'], c['text'], c['from']) == ('ask', 'Brighter lamp?', 'film agent')
    finally:
        page.stop = True


def test_an_agent_following_a_scene_can_name_itself(stage):
    OPS['stage_events'](scene='room', since=1, who='vr dev')
    assert _get(stage['port'], 'live/presence')[1]['listening'] == ['vr dev']


def test_the_last_follow_can_be_kept_by_an_agent(stage):
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_take_keep_last'](scene='room', name='person_a')
        assert page.seen[-1]['type'] == 'take_keep_last' and page.seen[-1]['name'] == 'person_a'
        assert 'take_keep_last' in out
        with pytest.raises(OpError, match='stage_take_keep_last'):
            OPS['stage_cmd'](scene='room', type='take_keep_last')
    finally:
        page.stop = True


def test_a_follow_pin_goes_to_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_follow_anchor'](scene='room', person='person_b', to='stool_3')
        c = page.seen[-1]
        assert (c['type'], c['person'], c['joint'], c['to'], c['legs']) == ('follow_anchor', 'person_b', 'hips', 'stool_3', 'keep_pose')
        OPS['stage_follow_anchor'](scene='room', person='person_b', joint='feet', to=[1.0, 2.0, 0.3])
        assert page.seen[-1]['to'] == [1.0, 2.0, 0.3]
        with pytest.raises(OpError, match="joint is"):
            OPS['stage_follow_anchor'](scene='room', person='p', joint='knee', to='stool_3')
        with pytest.raises(OpError, match='to='):
            OPS['stage_follow_anchor'](scene='room', person='p')
    finally:
        page.stop = True


def test_follow_anchor_takes_to_from_the_cli_as_a_list_or_a_word(stage):
    from ismail.__main__ import parse_args
    page = FakePage(stage['port'], 'room')
    try:
        for arg, want in (('to=[1,2,0]', [1, 2, 0]), ('to=here', 'here'), ('to=stool_3', 'stool_3')):
            kw = parse_args(['person=person_b', arg])
            assert kw['to'] == want
            OPS['stage_follow_anchor'](scene='room', **kw)
            assert page.seen[-1]['to'] == want
    finally:
        page.stop = True
