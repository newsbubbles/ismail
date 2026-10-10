"""Frame-locked capture (stage_capture): the capture's own server serves the job and takes frames in order, and
answers every other write "not saved" so a capture never changes the scene or reaches the live link; the op checks
its camera and writes the job."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from ismail.api import OpError
from ismail.stage import capture as C
from ismail.stage import ops as O
from ismail.stage import server as S

PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 32


def _get(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/{path}', timeout=10) as r:
        return json.loads(r.read())


def _post(port, path, data, json_body=True):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/{path}', data=json.dumps(data).encode() if json_body else data,
                                 headers={'Content-Type': 'application/json' if json_body else 'image/png'})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _scenes(tmp_path):
    scenes = tmp_path / 'scenes'
    (scenes / 'room').mkdir(parents=True)
    (scenes / 'room' / 'scene.glb').write_bytes(b'glTF')
    (scenes / 'room' / 'manifest.json').write_text('{"objects": {}}', encoding='utf-8')
    (scenes / 'room' / 'anim.json').write_text('{"span": [0, 4]}', encoding='utf-8')
    return scenes


class Encoder:                                       # ffmpeg's stdin, kept in memory
    def __init__(self):
        self.stdin = self
        self.got = []

    def write(self, b):
        self.got.append(b)


@pytest.fixture()
def capture(tmp_path, monkeypatch):
    scenes = _scenes(tmp_path)
    jd = scenes / 'room' / 'captures' / 'job_a'
    jd.mkdir(parents=True)
    job = {'id': 'job_a', 'scene': 'room', 'scenes': str(scenes), 't0': 1.0, 't1': 1.1, 'fps': 30, 'size': [64, 36],
           'camera': {'object': 'cam'}, 'setup': [], 'keep_frames': True}
    enc = Encoder()
    cap = C.Capture(job, jd, enc)
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(tmp_path / 'reg'))
    S.LIVE.clear()
    S.configure(scenes, state=str(tmp_path / 'state'))
    monkeypatch.setattr(S, 'CAPTURE', cap)
    srv = S.Server(('127.0.0.1', 0), S.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield {'port': srv.server_address[1], 'scenes': scenes, 'cap': cap, 'enc': enc, 'dir': jd}
    srv.shutdown()
    srv.server_close()


def test_the_page_gets_its_job_and_frames_go_in_order(capture):
    port, cap = capture['port'], capture['cap']
    assert _get(port, 'capture/job')['camera'] == {'object': 'cam'}
    assert cap.frames == 3
    for n in range(3):
        assert _post(port, f'capture/frame?n={n}', PNG, json_body=False) == (200, {'ok': True, 'n': n})
    assert len(capture['enc'].got) == 3
    assert sorted(p.name for p in (capture['dir'] / 'frames').iterdir()) == ['f_00000.png', 'f_00001.png', 'f_00002.png']
    code, j = _post(port, 'capture/frame?n=7', PNG, json_body=False)
    assert code == 400 and 'frame 3 was due' in j['error']
    code, j = _post(port, 'capture/frame?n=3', b'not a png', json_body=False)
    assert code == 400 and 'not a PNG' in j['error']
    assert json.loads((capture['dir'] / 'status.json').read_text())['frame'] == 3


def test_a_capture_never_writes_to_the_scene_or_the_live_link(capture):
    port, scenes = capture['port'], capture['scenes']
    before = (scenes / 'room' / 'anim.json').read_text()
    for path, body in (('anim?scene=room', {'span': [0, 9]}), ('save?scene=room', {}), ('behaviour_state?scene=room', {'switch': {'on': True}}),
                       ('live/event?scene=room', [{'type': 'page_load'}]), ('live/state?scene=room', {'scene': 'room'})):
        code, j = _post(port, path, body)
        assert code == 200 and 'not saved' in j['capture'], path
    assert (scenes / 'room' / 'anim.json').read_text() == before
    assert not (scenes / 'room' / 'behaviour_state.json').exists()
    assert not (scenes / 'room' / 'live').exists()
    S.live('room')['cmds'].append({'id': 1, 'type': 'say', 'text': 'for the headset'})   # a command meant for the person
    assert _get(port, 'live/cmd?scene=room&since=0')['cmds'] == []


def test_the_page_status_and_its_errors_reach_the_status_file(capture):
    port, cap = capture['port'], capture['cap']
    _post(port, 'clientlog', {'entries': [{'level': 'error', 'msg': 'boom in a behaviour'}, {'level': 'beat', 'msg': '{}'}]})
    _post(port, 'capture/status', {'state': 'done', 'frames': 3, 'errors': ['clock: no keys']})
    assert cap.page['state'] == 'done'
    s = cap.state(state='rendering')
    assert s['page_errors'] == ['boom in a behaviour', 'clock: no keys']
    assert 'boom in a behaviour' in (capture['dir'] / 'log.txt').read_text()


@pytest.fixture()
def op_scenes(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(C, 'browser', lambda: 'browser.exe')
    monkeypatch.setattr(C, 'ffmpeg', lambda: 'ffmpeg.exe')
    monkeypatch.setattr(O.subprocess, 'Popen', lambda args, **kw: started.append(args))
    return _scenes(tmp_path), started


def test_the_op_writes_the_job_for_a_story_shot(op_scenes, tmp_path):
    scenes, started = op_scenes
    cams = tmp_path / 'cameras.json'
    cams.write_text(json.dumps({'shots': [{'id': 'push_in', 'seconds': 4, 'keys': [
        {'pos': [0, -5, 1.5], 'look': [0, 0, 1.5], 'lens': 24}, {'pos': [0, -2, 1.5], 'look': [0, 0, 1.5], 'lens': 35}]}]}))
    r = O.stage_capture('room', 10, 14, shot='push_in', cameras=str(cams), scenes=str(scenes), name='shot_1',
                        setup=[{'type': 'sky', 'mode': 'night'}], size=[1440, 1080])
    assert '120 frames at 30 fps, 1440x1080' in r
    job = json.loads((scenes / 'room' / 'captures' / 'shot_1' / 'job.json').read_text())
    assert job['camera']['path']['seconds'] == 4.0 and job['camera']['path']['at'] == 10.0
    assert job['clock_at'] == 10.0 and job['setup'] == [{'type': 'sky', 'mode': 'night'}]
    assert started and started[0][-1].endswith('job.json')
    assert O.stage_capture_status('room', scenes=str(scenes)).startswith('shot_1, starting')


def test_the_op_wants_one_camera_and_a_sane_size(op_scenes, tmp_path):
    scenes, _ = op_scenes
    cams = tmp_path / 'cameras.json'
    cams.write_text('{"shots": []}')
    with pytest.raises(OpError, match='give one camera'):
        O.stage_capture('room', 0, 1, scenes=str(scenes))
    with pytest.raises(OpError, match='give one camera'):
        O.stage_capture('room', 0, 1, camera='cam', view={'pos': [0, 0, 1], 'fwd': [0, 1, 0]}, scenes=str(scenes))
    with pytest.raises(OpError, match='even pixels'):
        O.stage_capture('room', 0, 1, camera='cam', size=[641, 360], scenes=str(scenes))
    with pytest.raises(OpError, match='t1 > t0'):
        O.stage_capture('room', 2, 1, camera='cam', scenes=str(scenes))
    with pytest.raises(OpError, match='no shot'):
        O.stage_capture('room', 0, 1, shot='nope', cameras=str(cams), scenes=str(scenes))
    assert not (scenes / 'room' / 'captures').exists()
