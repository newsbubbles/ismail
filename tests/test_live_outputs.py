"""Where a live set's sound goes and what reaches back into it (ledger M34, M35): the output moves to a new default
device mid-set, a bus or the master streams as PCM over the engine's localhost port (each with its own limiter), and
named controls (a knob on the VR stage) drive live params at once, logged by bar as automation. All silent:
device='none'."""
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

from ismail.dsp import SR
from ismail.live import outputs as O
from ismail.live.engine import BLOCK, Engine, LiveError, serve


def run(eng, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        eng.tick()
        eng.mix_block()


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.cmd_bus('singer')
    e.cmd_track('p', instrument='preset:pluck', output='singer')
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'p', 'notes': '0 C4 1; 1 E4 1; 2 G4 1; 3 C5 1', 'bars': 1},
                 {'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    return e


def pcm(b):
    return np.frombuffer(b, dtype='<i2').reshape(-1, 2).astype(np.float32) / 32767.0


def test_a_bus_streams_on_its_own_and_the_master_streams_everything(eng):
    run(eng, 4.0)                                   # the clips start on bar 2; one loop in, every render is done
    singer, master = eng.hub.subscribe('bus:singer'), eng.hub.subscribe('master')
    run(eng, 0.9)                                   # under the 1 s a listener's queue keeps
    a, m = pcm(singer.get(0.1)), pcm(master.get(0.1))
    assert len(a) > 0.8 * SR and len(m) > 0.8 * SR
    assert np.max(np.abs(a)) > 0.01 and np.max(np.abs(m)) > 0.01
    # the kick is not on singer: singer's stream has the pluck only, so it has next to no energy under 100 Hz where the
    # master has the kicks (a loudness comparison flaked: the master's trim and the window's alignment move it)
    def low(x):
        f = np.fft.rfftfreq(len(x), 1 / SR)
        return float(np.sum(np.abs(np.fft.rfft(x.mean(1))[(f > 30) & (f < 100)]) ** 2))
    assert low(a) < 0.1 * low(m)
    assert np.max(np.abs(a)) <= 1.0
    s = eng.cmd_stream('bus:singer')
    assert s['path'] == '/stream?name=bus:singer' and s['format'] == 's16le' and s['listening'] == 1
    with pytest.raises(LiveError, match="'bus:singer'"):
        eng.cmd_stream('bus:radio')
    assert 'streams: bus:singer (1 listening)' in eng.cmd_status()


def test_a_slow_listener_loses_its_oldest_audio_and_never_slows_the_set(eng):
    sub = eng.hub.subscribe('master')
    run(eng, 3.0)                                   # nobody reads for 3 s; the queue keeps about 1 s
    assert sub.n <= int(O.MAX_QUEUE_S * SR) * 4 + BLOCK * 4 * 2
    assert eng.hub.dropped['master'] > 0


def test_a_stream_is_a_plain_http_get_on_localhost(eng):
    run(eng, 2.0)                                   # the clips start on bar 2
    httpd = serve(eng, 0)
    port = httpd.server_address[1]
    got = {}

    def listen():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream?name=bus:singer", timeout=10) as r:
            got['headers'] = dict(r.headers)
            got['body'] = r.read(SR * 4 // 2)          # half a second of stereo int16
    t = threading.Thread(target=listen)
    t.start()
    for _ in range(200):
        if eng.hub.wants('bus:singer'):
            break
        threading.Event().wait(0.01)
    run(eng, 1.5)
    t.join(10)
    assert got['headers']['X-Format'] == 's16le' and got['headers']['X-Sample-Rate'] == str(SR)
    assert len(got['body']) == SR * 4 // 2 and np.max(np.abs(pcm(got['body']))) > 0.01
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/stream?name=bus:radio", timeout=5)
    assert e.value.code == 404
    httpd.shutdown()


def test_a_knob_drives_a_live_param_at_once_and_is_logged_by_bar(eng):
    assert 'p -> ' not in eng.cmd_status()
    eng.cmd_track('p', fx=[{'type': 'filter', 'mode': 'lp', 'cutoff': 8000}], at='now')
    run(eng, 0.5)
    out = eng.cmd_map('singer.volume', target='bus:singer', param='volume_db', range=[-40, 0])
    assert 'singer.volume -> bus:singer volume_db (linear -40..0)' in out
    eng.cmd_map('singer.tone', target='p', param='fx:filter.cutoff', range=[200, 8000], curve='log')
    eng.cmd_map('singer.power', target='k', param='volume_db', range=[-120, 0], curve='switch')
    r = eng.cmd_control('singer.volume', 0.5)
    assert r['applied'] == -20 and eng.buses['singer']['volume_db'] == -20
    r = eng.cmd_control('singer.tone', 0.5)
    assert r['applied'] == pytest.approx(1264.9, abs=0.5)
    eng.cmd_control('singer.power', 0.2)
    assert eng.tracks['k']['volume_db'] == -120
    run(eng, 1.0)
    eng.cmd_control('singer.volume', 1.0)
    assert 'controls: singer.volume -> bus:singer volume_db' in eng.cmd_status()
    back = eng.cmd_controls('singer.volume')
    pts = json.loads(back.split('automation points [bar, value]: ')[1])
    assert [p[1] for p in pts] == [-20, 0] and pts[1][0] > pts[0][0] >= 1
    assert 'singer.tone -> p fx:filter.cutoff: 1 moves' in eng.cmd_controls()


def test_controls_say_what_to_do_when_they_are_wrong(eng):
    with pytest.raises(LiveError, match='live_map it first'):
        eng.cmd_control('nope', 0.5)
    with pytest.raises(LiveError, match="fx:<index or type>"):
        eng.cmd_map('x', target='p', param='cutoff', range=[0, 1])
    with pytest.raises(LiveError, match='range'):
        eng.cmd_map('x', target='p', param='volume_db')
    with pytest.raises(LiveError, match='log curve'):
        eng.cmd_map('x', target='p', param='volume_db', range=[-40, 0], curve='log')
    with pytest.raises(LiveError, match='no bus'):
        eng.cmd_map('x', target='bus:radio', param='volume_db', range=[-40, 0])
    eng.cmd_map('x', target='p', param='pan', curve='raw')
    assert eng.cmd_control('x', -0.3)['applied'] == -0.3 and eng.tracks['p']['pan'] == -0.3
    assert 'unmapped' in eng.cmd_map('x', remove=True)


class _FakeStream:
    device = 0

    def stop(self):
        pass

    def close(self):
        pass


def test_the_output_follows_a_new_default_and_recovers_a_vanished_device(eng, monkeypatch):
    opened = []

    def fake_open(device, rescan=True):
        opened.append(device)
        eng.stream, eng.out_name = _FakeStream(), {'default': 'Speakers (JBL Go 3)'}.get(device, str(device))
        eng.last_pull = __import__('time').time()
    monkeypatch.setattr(eng, '_open_output', fake_open)
    eng.device, eng.stream, eng.out_name, eng.last_pull = 'default', _FakeStream(), 'Laptop speakers', \
        __import__('time').time()
    monkeypatch.setattr(O, 'default_output_name', lambda timeout=5.0: 'Speakers (JBL Go 3)')
    eng._check_output()
    assert opened == ['default'] and eng.out_name == 'Speakers (JBL Go 3)'
    assert any('moved from Laptop speakers to Speakers (JBL Go 3)' in x for x in eng.news)
    eng._check_output()                               # nothing changed: nothing happens
    assert opened == ['default']
    # a named device that stops taking audio falls back to the default
    eng.device, eng.out_name, eng._out_tried = 'Headphones', 'Headphones', 0.0
    eng.last_pull -= 10
    eng._check_output()
    assert opened[-1] == 'default' and eng.device == 'default'
    assert 'output Speakers (JBL Go 3) (follows the default)' in eng.cmd_status()


def test_moving_to_no_device_and_back_keeps_the_clock(eng):
    run(eng, 0.5)
    pos = eng.pos
    out = eng.cmd_device('none')
    assert 'output: none' in out and eng.stream is None
    run(eng, 0.5)
    assert eng.pos > pos


def test_the_mapping_curves():
    assert O.scale(0.25, [-40, 0], 'linear') == -30
    assert O.scale(1.5, [-40, 0], 'linear') == 0
    assert O.scale(0.5, [100, 10000], 'log') == pytest.approx(1000)
    assert O.scale(0.49, [-120, 0], 'switch') == -120 and O.scale(0.5, [-120, 0], 'switch') == 0
    assert O.scale(440.0, None, 'raw') == 440.0


def test_a_quiet_bus_streams_silence_and_live_stream_takes_a_name(eng, tmp_path):
    # from the VR stage: a dormant bus pushed nothing, so listeners saw gaps and fell out of step;
    # live_stream(project, name=...) clashed with the dispatcher's own `name`
    eng.cmd_bus('radio')                             # nothing plays on it: it goes dormant
    run(eng, 2.0)
    sub = eng.hub.subscribe('bus:radio')
    run(eng, 0.5)
    b = pcm(sub.get(0.1))
    assert len(b) > 0.4 * SR and np.max(np.abs(b)) == 0.0
    import inspect
    from ismail.live import ops
    assert inspect.signature(ops._call).parameters['name'].kind is inspect.Parameter.POSITIONAL_ONLY
