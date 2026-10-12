"""The exam pre-flight (D-7, Nate 2026-10-05: self-checks live in tools). A blind exam that leaks its key, a clip that
does not play or is quieter than the rest, or a Submit that lands nowhere wastes a round of the person's ear."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf

from ismail import exam_check as EC

SR = 44100


def tone(path, f=220, s=1.0, db=-20, lead=0.0, sr=SR, ch=1):
    t = np.arange(int(s * sr)) / sr
    y = 10 ** (db / 20) * np.sqrt(2) * np.sin(2 * np.pi * f * t)
    y = np.concatenate([np.zeros(int(lead * sr)), y])
    sf.write(str(path), np.stack([y] * ch, axis=1) if ch > 1 else y, sr)
    return str(path)


def text(lines):
    return '\n'.join(lines)


def test_a_fair_exam_is_ready_and_a_broken_one_is_not(tmp_path):
    a, b = tone(tmp_path / 'clip_1.wav', 220), tone(tmp_path / 'clip_2.wav', 230)
    ok, lines = EC.run(clips=[{'label': 'A', 'path': a}, {'label': 'B', 'path': b}], key={'A': 'record', 'B': 'mine'},
                       answers_path=str(tmp_path / 'answers.jsonl'))
    assert ok and lines[0].startswith('READY'), text(lines)
    loud = tone(tmp_path / 'clip_3.wav', 220, db=-10)
    (tmp_path / 'broken.wav').write_bytes(b'RIFF not audio')
    ok, lines = EC.run(clips=[a, loud, str(tmp_path / 'broken.wav'), str(tmp_path / 'gone.wav')])
    out = text(lines)
    assert not ok and 'NOT READY' in lines[0]
    assert 'does not decode' in out and 'missing' in out and 'loudness spread' in out


def test_blind_leaks_are_caught(tmp_path):
    rec = tone(tmp_path / 'hendrix_record.wav', 220)
    mine = tone(tmp_path / 'b.wav', 230, s=1.6, sr=48000)
    ok, lines = EC.run(clips=[{'label': 'A', 'path': rec}, {'label': 'B', 'path': mine}],
                       key={'A': 'record', 'B': 'ismail'}, secrets=['hendrix'])
    out = text(lines)
    assert not ok
    assert "says 'hendrix'" in out and "says 'record'" in out             # the file name gives it away
    assert 'formats differ by class' in out                                 # 44.1 vs 48 kHz
    assert 'differ in length' in out
    # order: the record first in every trial
    clips, key = [], {}
    for i in range(3):
        r, m = tone(tmp_path / f'r{i}.wav', 220 + i), tone(tmp_path / f'm{i}.wav', 225 + i)
        clips += [{'label': f't{i}a', 'path': r}, {'label': f't{i}b', 'path': m}]
        key.update({f't{i}a': 'record', f't{i}b': 'mine'})
    ok, lines = EC.run(clips=clips, key=key)
    assert not ok and 'the order follows the key' in text(lines)


def test_the_page_source_and_submit_are_checked(tmp_path):
    a, b = tone(tmp_path / 'x1.wav', 220), tone(tmp_path / 'x2.wav', 230)
    page = tmp_path / 'index.html'
    page.write_text('<html><script src="app.js"></script><audio src="x1.wav"></audio><audio src="x2.wav"></audio>'
                    '<p>Which one is the record?</p></html>', encoding='utf8')
    (tmp_path / 'app.js').write_text('const clips = {"x1.wav": "record", "x2.wav": "mine"}; fetch("answer_key.json");',
                                     encoding='utf8')
    ok, lines = EC.run(page=str(page), key={'x1.wav': 'record', 'x2.wav': 'mine'})
    out = text(lines)
    assert not ok and 'answer_key.json' in out and "ties 'record'" in out
    assert 'all 2 clips exist and decode' in out                            # found from the page itself
    (tmp_path / 'app.js').write_text('const clips = ["x1.wav", "x2.wav"];', encoding='utf8')
    ans = tmp_path / 'answers.jsonl'

    class Sub(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            if self.path == '/good':
                with open(ans, 'ab') as f:
                    f.write(body + b'\n')
            self.send_response(200)
            self.end_headers()
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Sub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{srv.server_address[1]}'
    ok, lines = EC.run(page=str(page), key={'x1.wav': 'record', 'x2.wav': 'mine'}, submit_url=base + '/good',
                       answers_path=str(ans))
    assert ok and 'Submit lands' in text(lines), text(lines)
    assert json.loads(ans.read_text().splitlines()[-1])['preflight'] is True
    ok, lines = EC.run(page=str(page), submit_url=base + '/lost', answers_path=str(ans))
    assert not ok and 'nothing new reached' in text(lines)
    srv.shutdown()


def test_the_op_reports_and_the_phone_refuses_a_leaky_exam(tmp_path, monkeypatch):
    import pytest
    from ismail import api
    from ismail.api import OpError
    from ismail.phone import ops as P
    a, b = tone(tmp_path / 'real.wav', 220), tone(tmp_path / 'b.wav', 230)
    out = api.call('exam_check', clips=[a, b], key={'real.wav': 'real', 'b.wav': 'fake'}) if hasattr(api, 'call') \
        else __import__('ismail.api_exam', fromlist=['x']).exam_check(clips=[a, b], key={'real.wav': 'real', 'b.wav': 'fake'})
    assert out.startswith('NOT READY') and "says 'real'" in out
    monkeypatch.setattr(P, '_call', lambda *a, **k: 'shown')
    with pytest.raises(OpError, match='NOT READY'):
        P.phone_exam('round 1', [{'label': 'A', 'path': a}, {'label': 'B', 'path': b}],
                     key={'A': 'real', 'B': 'fake'})
    assert P.phone_exam('round 1', [{'label': 'A', 'path': a}, {'label': 'B', 'path': b}], key={'A': 'real', 'B': 'fake'},
                        check=False) == 'shown'


def noise(path, cut=None, s=1.5, seed=0, db=-23):
    y = np.random.default_rng(seed).standard_normal(int(s * SR))
    if cut:                                                # a 16 kHz recording: nothing at all above its cut
        Y = np.fft.rfft(y)
        Y[np.fft.rfftfreq(len(y), 1 / SR) > cut] = 0
        y = np.fft.irfft(Y, len(y))
    y *= 10 ** (db / 20) / np.sqrt((y ** 2).mean())
    sf.write(str(path), y, SR)
    return str(path)


def test_a_band_limited_class_and_lopsided_sides_are_heard(tmp_path):
    """ledger:M146 (vox:r39, r41): the real takes went through an earbud mic (nothing above 7 kHz) while the synth
    filled that band, and exam_check said READY; a build put the real take first in every pair."""
    clips, key = [], {}
    for i in range(4):                                     # 4 pairs: real band-limited, mine full band
        for side, cls, cut in (('A', 'real', 7000), ('B', 'mine', None)) if i % 2 else (('A', 'mine', None), ('B', 'real', 7000)):
            lab = f'{i + 1}{side}'
            clips.append({'label': lab, 'path': noise(tmp_path / f'c{i}{side}.wav', cut, seed=i * 2 + (side == 'B'))})
            key[lab] = cls
    ok, lines = EC.run(clips=clips, key=key, answers_path=str(tmp_path / 'a.jsonl'))
    out = text(lines)
    assert not ok and 'differ above 6 kHz' in out and 'nothing above 8 kHz' in out, out
    full = [{'label': c['label'], 'path': noise(tmp_path / f"f{c['label']}.wav", None, seed=j)} for j, c in enumerate(clips)]
    ok, lines = EC.run(clips=full, key=key, answers_path=str(tmp_path / 'a.jsonl'))
    assert ok and '6 kHz' not in text(lines), text(lines)
    key2 = {c['label']: ('real' if c['label'].endswith('A') else 'mine') for c in full}   # r41: real on A, every pair
    ok, lines = EC.run(clips=full, key=key2, answers_path=str(tmp_path / 'a.jsonl'))
    assert not ok and "every trial starts with 'real'" in text(lines), text(lines)


def test_trials_are_read_by_their_pair_labels_whatever_the_number_of_classes(tmp_path):
    """A key with three classes over 2-clip pairs (control pairs 'real' beside 'record' and 'mine') was read in
    triples across the pairs and failed a balanced exam ("every trial starts with 'record'")."""
    plan = [('record', 'mine'), ('mine', 'record'), ('real', 'real'), ('record', 'mine'), ('mine', 'record'), ('record', 'mine')]
    clips, key = [], {}
    for i, sides in enumerate(plan, 1):
        for ab, cls in zip('AB', sides):
            clips.append({'label': f'{i}{ab}', 'path': noise(tmp_path / f'c{i}{ab}.wav', None, seed=i * 2 + (ab == 'B'))})
            key[f'{i}{ab}'] = cls
    ok, lines = EC.run(clips=clips, key=key, answers_path=str(tmp_path / 'a.jsonl'))
    assert 'the order follows the key' not in text(lines), text(lines)
    lop = {c['label']: 'record' if c['label'].endswith('A') else ('mine' if c['label'][0] in '13' else 'mine_v2')
           for c in clips[:4] + clips[6:]}                                         # the record first in every pair, 3 classes
    ok, lines = EC.run(clips=clips[:4] + clips[6:], key=lop, answers_path=str(tmp_path / 'a.jsonl'))
    assert not ok and "every trial starts with 'record'" in text(lines), text(lines)


def test_the_blind_crop_check_hides_the_key_and_says_when_the_picture_gives_it_away(tmp_path):
    """ledger:M158 (Voice, vox:r44-r45: 15 of 16 picked from the picture): a gate before a real-vs-made round."""
    from ismail import api_exam as X
    pairs = [[noise(tmp_path / f'r{i}.wav', None, seed=i), noise(tmp_path / f'm{i}.wav', 6000, seed=10 + i)]
             for i in range(3)]
    out = str(tmp_path / 'eye')
    msg = X.exam_eye_crops(pairs=pairs, out=out, n_windows=3)
    assert msg.startswith('9 crop pairs') and 'Do not open key.json' in msg
    qs = json.load(open(os.path.join(out, 'questions.json'), encoding='utf8'))
    assert len(qs) == 9 and all('real' not in q for q in qs) and os.path.exists(os.path.join(out, 'q09.png'))
    key = {k['q']: k['real'] for k in json.load(open(os.path.join(out, 'key.json'), encoding='utf8'))}
    sure = X.exam_eye_score(out, key)                                  # an eye that sees it every time
    assert sure.startswith('NOT READY: blind crop check: picked the real side in 9 of 9') and 'zoom two ways' in sure
    guess = {q: ('1' if q % 2 else '2') for q in key}                   # an eye that cannot tell
    hits = sum(guess[q] == key[q] for q in key)
    out2 = X.exam_eye_score(out, guess)
    assert (out2.startswith('READY') if hits <= 7 else out2.startswith('NOT READY')), out2
    assert json.load(open(os.path.join(out, 'answers.json'), encoding='utf8'))
    import pytest
    from ismail.api import OpError
    with pytest.raises(OpError, match='no answers'):
        X.exam_eye_score(out, {99: '1'})
