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
