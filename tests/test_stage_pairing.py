"""The security floor part B1 (research/multiplayer/security-floor.md, tests 1 to 9): devices pair with a short code,
agents carry a token, the server stamps every event and command with who sent it, report mode accepts everyone and
counts the unpaired, enforce mode refuses them, and an unpaired device is shut out at once."""
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from ismail.api import OPS, OpError
from ismail.stage import pairing as PA
from ismail.stage import server as S
from test_stage import stage  # noqa: F401  (the fixture)


def _req(port, path, body=None, headers=None, method=None):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(f'http://127.0.0.1:{port}/{path}', data=data, method=method or ('POST' if data is not None else 'GET'),
                               headers={**({'Content-Type': 'application/json'} if data is not None else {}), **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=15) as f:
            return f.status, json.loads(f.read() or b'null')
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b'null')


def _agent(stage):
    return {'X-Stage-Agent': stage['srv'].auth.token, 'X-Stage-Who': 'test agent'}


def _mode(stage, mode):
    (stage['srv'].auth.home / 'stage.json').write_text(json.dumps({'auth': mode, 'person': 'nate'}), encoding='utf-8')
    stage['srv'].auth._conf = (0.0, {})


def _paired(stage, name='Quest 3', kind='headset'):
    p = stage['port']
    code = _req(p, 'pair/start', {'name': name, 'kind': kind})[1]['code']
    st, got = _req(p, 'pair/claim', {'code': code})
    assert st == 200, got
    return got['key'], got


def test_a_code_pairs_once_and_expires(stage, monkeypatch):
    _mode(stage, 'report')
    out = OPS['stage_pair'](name='Quest 3', kind='headset')                       # 1: the op, through the agent token
    code = out.split()[1]
    assert len(code) == 6 and 'good for 120 s, once' in out
    st, got = _req(stage['port'], 'pair/claim', {'code': code})
    assert st == 200 and len(got['key']) > 30 and got['device'] == 'Quest 3' and got['person'] == 'nate'
    assert _req(stage['port'], 'pair/claim', {'code': code})[0] == 403          # once
    code2 = _req(stage['port'], 'pair/start', {'name': 'phone', 'kind': 'phone'})[1]['code']
    t = time.time()
    with monkeypatch.context() as m:
        m.setattr(PA.time, 'time', lambda: t + PA.CODE_S + 1)                    # two minutes later
        assert _req(stage['port'], 'pair/claim', {'code': code2})[0] == 403
    assert 'Quest 3 (headset) for nate' in OPS['stage_devices']()


def test_pairing_starts_only_on_this_pc_or_from_an_agent(stage):
    p = stage['port']
    for h in ({'X-Forwarded-For': '100.64.0.7'}, {'X-Forwarded-Host': 'dumbass.tailnet.ts.net'}, {'Tailscale-User-Login': 'x@y'}):
        st, got = _req(p, 'pair/start', {'kind': 'phone'}, h)                    # 2: through the proxy
        assert st == 403 and got['refused'] == 'pairing from outside'
    (stage['srv'].auth.home / 'stage.json').write_text(json.dumps({'hosts': ['box.example']}), encoding='utf-8')
    stage['srv']._hosts = (0.0, set())
    assert _req(p, 'pair/start', {'kind': 'phone'}, {'Host': 'box.example'})[0] == 403   # a name, not loopback
    assert _req(p, 'pair/start', {'kind': 'phone'})[0] == 200                    # this PC directly
    assert _req(p, 'pair/start', {'kind': 'phone'}, {**_agent(stage), 'X-Forwarded-For': '100.64.0.7'})[0] == 200   # an agent
    assert _req(p, 'pair/devices', headers={'X-Forwarded-For': '1.2.3.4'})[0] == 403


def test_wrong_codes_are_rate_limited(stage):
    p = stage['port']
    for _ in range(5):
        assert _req(p, 'pair/claim', {'code': '000000'})[0] == 403               # 3
    good = _req(p, 'pair/start', {'kind': 'phone'})[1]['code']
    st, got = _req(p, 'pair/claim', {'code': good})
    assert st == 429 and 'wait a minute' in got['error']
    h = _req(p, 'health')[1]
    assert h['refused']['wrong pairing code'] == 5 and h['refused']['pairing rate-limited'] == 1


def test_the_key_is_kept_as_a_hash_and_never_echoed(stage):
    key, _ = _paired(stage)
    p = stage['port']
    _req(p, 'live/event?scene=room', {'type': 'gesture', 'hand': 'left'}, {'X-Stage-Key': key})      # 4
    _req(p, 'live/state?scene=room', {'scene': 'room', 'xr': True}, {'X-Stage-Key': key})
    dev = stage['srv'].auth.devices_file.read_text(encoding='utf-8')
    assert key not in dev and PA._h(key) in dev
    for f in (stage['scenes'] / 'room' / 'live').glob('*'):
        assert key not in f.read_text(encoding='utf-8')
    assert key not in json.dumps(_req(p, 'pair/devices')[1]) and key not in json.dumps(_req(p, 'health')[1])
    assert key not in json.dumps(_req(p, 'live/events?scene=room')[1])


def test_every_event_and_command_is_stamped_with_who_sent_it(stage):
    key, _ = _paired(stage)
    p = stage['port']
    _req(p, 'live/event?scene=room', {'type': 'gesture', 'principal': {'person': 'ana'}}, {'X-Stage-Key': key})   # 5
    _req(p, 'live/event?scene=room', {'type': 'gesture'}, _agent(stage))
    _req(p, 'live/event?scene=room', {'type': 'gesture'})
    S.server_event('room', {'type': 'voice_in', 'file': 'x.webm'})
    evs = _req(p, 'live/events?scene=room')[1]['events'][-4:]
    assert evs[0]['principal'] == {'person': 'owner', 'device': 'Quest 3'} and evs[0]['said_by'] == '{"person": "ana"}'
    assert evs[1]['principal'] == {'agent': 'test agent', 'for': 'owner'}
    assert evs[2]['principal'] == {'unpaired': True}
    assert evs[3]['principal'] == {'server': True}
    st, got = _req(p, 'live/event?scene=room', {'type': 'gesture', 'who': 'server'}, {'X-Stage-Key': key})
    assert st == 403                                                             # claiming the server: part A refuses it


def test_report_mode_accepts_and_counts_the_unpaired(stage):
    p = stage['port']                                                            # 6 (the whole test_stage.py: report)
    assert _req(p, 'live/event?scene=room', {'type': 'gesture'})[0] == 200
    assert _req(p, 'live/cmd?scene=room', {'type': 'say', 'text': 'hi'})[0] == 200
    a = _req(p, 'health')[1]['auth']
    assert a['mode'] == 'report' and a['unpaired'] >= 2


def test_enforce_mode_refuses_the_unpaired(stage, monkeypatch):
    called = []
    monkeypatch.setattr(S, 'transcribe_message', lambda *a: called.append(a))
    key, _ = _paired(stage)
    _mode(stage, 'enforce')
    p = stage['port']
    for path, body in (('live/event?scene=room', {'type': 'gesture'}), ('live/cmd?scene=room', {'type': 'say', 'text': 'x'})):
        st, got = _req(p, path, body)                                            # 7
        assert st == 401 and got['refused'] == 'unpaired'
        assert _req(p, path, body, {'X-Stage-Key': key})[0] == 200
        assert _req(p, path, body, _agent(stage))[0] == 200
    assert _req(p, 'live/events?scene=room&since=0&wait=1')[0] == 401
    assert _req(p, 'live/events?scene=room&since=0&wait=0', headers={'X-Stage-Key': key})[0] == 200
    audio = urllib.request.Request(f'http://127.0.0.1:{p}/voice/in?scene=room&kind=message', data=b'RIFF0000WAVE',
                                   headers={'Content-Type': 'audio/wav'})
    for h, want in (({}, 401), (_agent(stage), 401), ({'X-Stage-Key': key}, 200)):
        for k, v in h.items():
            audio.add_header(k, v)
        try:
            with urllib.request.urlopen(audio, timeout=10) as f:
                code = f.status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == want, h
    time.sleep(0.2)
    assert len(called) == 1                                                       # only the paired device's audio
    with urllib.request.urlopen(f'http://127.0.0.1:{p}/index.html', timeout=10) as f:
        assert f.status == 200                                                     # the page's files stay open


def test_unpairing_shuts_the_device_out_at_once(stage):
    key, rec = _paired(stage)
    p = stage['port']
    got = {}

    def poll():
        t = time.time()
        got['r'] = _req(p, f'live/events?scene=room&since={10 ** 9}&wait=30', headers={'X-Stage-Key': key})
        got['s'] = time.time() - t
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(0.5)
    assert 'unpaired Quest 3' in OPS['stage_unpair'](device='Quest 3')          # 8
    th.join(10)
    assert got['r'][0] == 401 and got['s'] < 5
    _mode(stage, 'enforce')
    st, got = _req(p, 'live/event?scene=room', {'type': 'gesture'}, {'X-Stage-Key': key})
    assert st == 401, (got, stage['srv'].auth.mode(), stage['srv'].auth.listing())
    with pytest.raises(OpError, match='stage_devices'):
        OPS['stage_unpair'](device='Quest 3')


def test_commands_reach_the_page_with_their_principal(stage):
    p = stage['port']
    _req(p, 'live/cmd?scene=room', {'type': 'say', 'text': 'hello'}, _agent(stage))
    cmds = _req(p, 'live/cmd?scene=room&since=0')[1]['cmds']                     # 9
    assert cmds[-1]['principal'] == {'agent': 'test agent', 'for': 'owner'}
    S.server_cmd('room', {'type': 'ack'})
    assert _req(p, 'live/cmd?scene=room&since=0')[1]['cmds'][-1]['principal'] == {'server': True}


def test_enforce_guards_the_scene_files_too(stage):
    key, _ = _paired(stage)
    room = stage['scenes'] / 'room'
    (room / 'voice').mkdir()
    (room / 'voice' / 'note.webm').write_bytes(b'audio')
    (room / 'live').mkdir(exist_ok=True)
    (room / 'live' / 'events.jsonl').write_text('{}\n', encoding='utf-8')
    (room / 'takes' / 't1').mkdir(parents=True)
    (room / 'takes' / 't1' / 'frames.jsonl').write_text('{}\n', encoding='utf-8')
    _mode(stage, 'enforce')
    p = stage['port']

    def code(path, h=None):
        r = urllib.request.Request(f'http://127.0.0.1:{p}/{path}', headers=h or {})
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                return f.status
        except urllib.error.HTTPError as e:
            return e.code
    paths = ['scenes/room/voice/note.webm', 'scenes/room/live/events.jsonl', 'scenes/room/takes/t1/frames.jsonl',
             'scenes/room/scene.glb', 'world?scene=room', 'edits?scene=room', 'takes?scene=room', 'snapshots?scene=room',
             'updates.json', 'waypoints?scene=room']
    for path in paths:                                                           # 9a
        assert code(path) == 401, path
        assert code(path, {'X-Stage-Key': key}) == 200, path
        assert code(path, _agent(stage)) == 200, path
    for path in ('index.html', 'main.js', 'health'):                             # the page's own files stay open
        assert code(path) == 200, path
    assert _req(p, 'pair/claim', {'code': '123456'})[0] == 403                   # open: answered, not 401


def test_wrong_codes_from_everywhere_pause_pairing(stage):
    p = stage['port']
    pending = _req(p, 'pair/start', {'kind': 'phone'})[1]['code']
    for i in range(21):                                                          # 9b: 21 "addresses", 1 try each
        st, _ = _req(p, 'pair/claim', {'code': '000000'}, {'X-Forwarded-For': f'6.6.6.{i}, 100.64.0.{i}'})
        assert st == 403
    st, got = _req(p, 'pair/claim', {'code': pending})
    assert st == 429 and 'paused' in got['error']
    a = _req(p, 'health')[1]['auth']
    assert a['pairing_paused_s'] > 50 and a['pending_codes'] == 0 and a['pauses'] == 1


def test_the_rate_limit_counts_the_proxys_own_address(stage):
    p = stage['port']
    for i in range(5):                                                           # the client's first entry changes,
        _req(p, 'pair/claim', {'code': '000000'}, {'X-Forwarded-For': f'9.9.9.{i}, 100.64.0.7'})   # the proxy's does not
    good = _req(p, 'pair/start', {'kind': 'phone'})[1]['code']
    st, got = _req(p, 'pair/claim', {'code': good}, {'X-Forwarded-For': '1.1.1.1, 100.64.0.7'})
    assert st == 429 and 'wait a minute' in got['error']
    assert _req(p, 'pair/claim', {'code': good}, {'X-Forwarded-For': '100.64.0.8'})[0] == 200   # another device: fine
