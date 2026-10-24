"""The eye exam page (ledger:M165 step 2): a round builds, serves, hides its key until an answer, and scores."""
import json
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest
import soundfile as sf

from ismail import exampage as X

SR = 44100


@pytest.fixture
def round_dir(tmp_path):
    rng = np.random.default_rng(1)
    t = np.arange(SR) / SR
    real, ours = tmp_path / 'real.wav', tmp_path / 'ours.wav'
    sf.write(str(real), 0.3 * np.sin(2 * np.pi * 300 * t) + 0.05 * rng.standard_normal(SR), SR)
    sf.write(str(ours), 0.3 * np.sin(2 * np.pi * 300 * t), SR)
    items = [{'word': f'w{i}', 'real': str(real), 'other': str(ours), 'window': [0.1 * i, 0.1 * i + 0.4],
              'words': [[0.1 * i + 0.05, 0.1 * i + 0.3, f'w{i}']], 'other_label': 'no noise', 'version': 'clean'}
             for i in range(4)]
    items[0]['ask'] = 'Where does the hiss start?'
    out = tmp_path / 'rounds' / 'p1'
    X.build(str(out), items, 'test round', seed=3)
    return out


def test_a_round_is_balanced_and_its_files_are_neutral(round_dir):
    man = json.loads((round_dir / 'manifest.json').read_text())
    key = json.loads((round_dir / 'key.json').read_text())
    assert len(man['items']) == 4 and sorted(k['real'] for k in key.values()) == ['1', '1', '2', '2']
    for it in man['items']:
        assert all((round_dir / p).exists() for p in list(it['img'].values()) + [it['audio']])
        assert it['words'][0]['ms'] == [50, 300] and 'real' not in json.dumps(it) and 'ours' not in json.dumps(it)
    assert [it.get('ask') for it in man['items']] == ['Where does the hiss start?', None, None, None]  # its own question


def test_the_page_hides_the_key_until_an_answer_and_serves_ranges(round_dir):
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), X._handler(str(round_dir.parent)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{httpd.server_address[1]}/eye/p1/'
    try:
        assert b'blink' in urllib.request.urlopen(base, timeout=5).read().lower()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + 'key', timeout=5)
        assert e.value.code == 403
        man = json.loads(urllib.request.urlopen(base + 'manifest.json', timeout=5).read())
        r = urllib.request.urlopen(urllib.request.Request(base + man['items'][0]['audio'],
                                                          headers={'Range': 'bytes=10-19'}), timeout=5)
        assert r.status == 206 and len(r.read()) == 10
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + 'files/..%2Fkey.json', timeout=5)
        key = json.loads((round_dir / 'key.json').read_text())
        ans = [{'q': 1, 'pick': key['1']['real'], 'comments': [{'n': 1, 'ms': 120, 'khz': 9.5, 'word': 'w0',
                                                                'picture': key['1']['real'], 'text': 'hiss'}]},
               {'q': 2, 'pick': '1' if key['2']['real'] == '2' else '2'}, {'q': 3, 'pick': 'cant'}, {'q': 4, 'pick': None}]
        req = urllib.request.Request(base + 'answer', data=json.dumps({'answers': ans}).encode(), method='POST')
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())['ok']
        assert json.loads(urllib.request.urlopen(base + 'key', timeout=5).read())['1']['label'] == 'no noise'
    finally:
        httpd.shutdown()
    s = X.score(str(round_dir))
    assert 'q1 w0' in s and ' right ' in s and ' WRONG ' in s and ' cant ' in s and ' none ' in s
    assert '"w0" on REAL: hiss' in s and 'clean right 1, WRONG 1, cant 1, none 1' in s


# ------------------------------------------------------------------ one server, rounds from every song (ledger:M300)

def _song_round(song_dir, name, title):
    d = song_dir / 'exam' / name
    (d / 'files').mkdir(parents=True)
    (d / 'manifest.json').write_text(json.dumps({'round': name, 'title': title, 'items': []}), encoding='utf8')
    (d / 'key.json').write_text('{}', encoding='utf8')
    return d


def _serve(handler):
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _get(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}{path}', timeout=5) as r:
        return r.read()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    p = tmp_path / 'home' / 'rounds.json'
    monkeypatch.setenv(X.ROUNDS_ENV, str(p))
    return p


def test_one_server_serves_rounds_from_two_songs(tmp_path, registry):
    a1 = _song_round(tmp_path / 'song_a', 'round1', 'song a, round 1')
    b1 = _song_round(tmp_path / 'song_b', 'round1', 'song b, round 1')   # the same folder name in another song
    b2 = _song_round(tmp_path / 'song_b', 'round2', 'song b, round 2')
    httpd, port = _serve(X._handler(str(a1.parent)))                      # the server runs on song a's rounds
    try:
        assert json.loads(_get(port, '/health'))['registry'] is True
        url_a = X.host(str(a1.parent), port, 'round1')
        url_b1 = X.host(str(b1.parent), port, 'round1')
        url_b2 = X.host(str(b2.parent), port, 'round2')
        assert url_a == f'http://127.0.0.1:{port}/eye/round1'
        assert url_b1 == f'http://127.0.0.1:{port}/eye/song_b-round1'               # the name was taken: the song's
        assert url_b2 == f'http://127.0.0.1:{port}/eye/round2'
        assert json.loads(_get(port, '/eye/round1/manifest.json'))['title'] == 'song a, round 1'
        assert json.loads(_get(port, '/eye/song_b-round1/manifest.json'))['title'] == 'song b, round 1'
        assert json.loads(_get(port, '/eye/round2/manifest.json'))['title'] == 'song b, round 2'
        assert b'Eye Exam' in _get(port, '/eye/round2')
        listing = _get(port, '/eye/')
        assert b'/eye/round1"' in listing and b'/eye/song_b-round1"' in listing and b'/eye/round2"' in listing
        # an answer lands in its own song's folder
        req = urllib.request.Request(url_b2 + '/answer', data=b'{"answers": []}', method='POST')
        assert json.loads(urllib.request.urlopen(req, timeout=5).read())['ok']
        assert (b2 / 'answers.jsonl').exists() and not (a1 / 'answers.jsonl').exists()
        # a rebuilt round keeps its URL; the registry is a plain file any process reads
        assert X.host(str(b1.parent), port, 'round1') == url_b1
        reg = json.loads(registry.read_text(encoding='utf8'))
        assert set(reg) == {'round1', 'song_b-round1', 'round2'}
        # the base URL, and every round in a folder, as before
        assert X.host(str(b1.parent), port) == f'http://127.0.0.1:{port}/eye/'
    finally:
        httpd.shutdown()


def test_a_second_server_reads_the_same_registry(tmp_path, registry):
    a1 = _song_round(tmp_path / 'song_a', 'round1', 'a')
    b3 = _song_round(tmp_path / 'song_b', 'round3', 'b')
    X.register(str(b3))
    httpd, port = _serve(X._handler(str(a1.parent)))    # started after the registration: still finds it
    try:
        assert json.loads(_get(port, '/eye/round3/manifest.json'))['title'] == 'b'
        assert json.loads(_get(port, '/eye/round1/manifest.json'))['title'] == 'a'
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(port, '/eye/round9/manifest.json')
        assert e.value.code == 404
    finally:
        httpd.shutdown()
    # a round whose folder is gone drops out of the registry
    (b3 / 'manifest.json').rename(b3 / 'manifest.old')
    assert 'round3' not in X.rounds()


def test_host_says_so_when_the_running_server_predates_the_registry(tmp_path, registry):
    from http.server import BaseHTTPRequestHandler
    _song_round(tmp_path / 'song_a', 'round1', 'a')
    b1 = _song_round(tmp_path / 'song_b', 'round1', 'b')

    class Old(BaseHTTPRequestHandler):                  # the health line before the registry, and a 404 for the round
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = b'{"ok": true, "what": "ismail exam page"}' if self.path == '/health' else b'no such round'
            self.send_response(200 if self.path == '/health' else 404)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd, port = _serve(Old)
    try:
        with pytest.raises(X.CannotServe) as e:
            X.host(str(b1.parent), port, 'round1')
        assert 'older than the round registry' in str(e.value) and 'Restart it' in str(e.value)
    finally:
        httpd.shutdown()


def test_host_says_so_when_no_server_starts(tmp_path, registry, monkeypatch):
    import subprocess
    b1 = _song_round(tmp_path / 'song_b', 'round1', 'b')
    monkeypatch.setattr(X, '_health', lambda port: None)       # nothing answers, and the start does nothing
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: None)
    monkeypatch.setattr(X.time, 'sleep', lambda s: None)
    with pytest.raises(X.CannotServe) as e:
        X.host(str(b1.parent), 1, 'round1')
    assert 'did not start' in str(e.value) and 'server.log' in str(e.value)
