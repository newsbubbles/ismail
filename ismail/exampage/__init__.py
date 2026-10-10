"""The eye exam page (ledger:M165 step 2, from vox's rec/words.html; the hosted round of M12).

One sound per card, two pictures of the same window: the real one and ours. They sit on top of each other and show
one at a time, so the eye reads any difference as movement (the blink comparator): F flips, hold Shift to peek, B
blinks. The person answers about the picture showing (M: this one is the real one, S: this one is ours, 0: can't
tell), pins comments at a time and frequency (a click, or a dragged box), and the key is served only after an answer.

A round is a folder: manifest.json (what the page shows), key.json (never served before an answer), files/ (the
pictures and the sound), sources.json (the real and other sound files and windows of each card; never served; what
region_measure reads), answers.jsonl (one line per submit) and, once a person names areas on the reveal, areas.jsonl
(one line per area) and areas_measured.jsonl (region_measure's numbers).

  python -m ismail.exampage serve <rounds folder> [--port 8871] [--host 127.0.0.1]
"""
import json
import os
import random
import re
import shutil
import sys
import time

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words.html')
VOCAB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vocab.json')
PORT = 8871
SAFE = re.compile(r'^[A-Za-z0-9_.-]+$')


def build(out, items, title, intro='', seed=0, f_lo=0.0, f_hi=16000.0, labels=None):
    """A round in `out` from items [{word, real, other, window: [t0, t1] (s), other_window: [t0, t1] (default the
    same), words: [[t0, t1, text]] (s, in the real file), other_label: what the other picture is (shown after the
    answer), version, pair}]. The sound card plays is the real file's window. -> the manifest."""
    import numpy as np
    import soundfile as sf
    from .. import analysis as A
    files = os.path.join(out, 'files')
    os.makedirs(files, exist_ok=True)
    rng = random.Random(seed)
    sides = [1, 1, 2, 2] * (len(items) // 4 + 1)       # balanced: as many real-first as real-second, seeded order
    sides = sides[:len(items)]
    rng.shuffle(sides)
    man = {'round': os.path.basename(os.path.abspath(out)), 'axes': list(A.EYE_BOX), 'timed': True, 'title': title,
           'intro': intro, 'f_lo': f_lo, 'f_hi': f_hi, 'labels': labels or {}, 'items': []}
    key, sources = {}, {}
    for q, (it, real_side) in enumerate(zip(items, sides), 1):
        t0, t1 = it['window']
        o0, o1 = it.get('other_window') or (t0, t1)
        words = [{'w': w[2], 't0': w[0], 't1': w[1]} for w in it.get('words') or []]
        owords = [{'w': w['w'], 't0': w['t0'] - t0 + o0, 't1': w['t1'] - t0 + o0} for w in words]
        name = it.get('target', it['word'])                  # the word under test gets the bright bounds
        pics = {}
        for side, (src, a, b, ws) in ((real_side, (it['real'], t0, t1, words)),
                                      (3 - real_side, (it['other'], o0, o1, owords))):
            png = os.path.join(files, f'q{q:02d}_{side}.png')
            target = next((w for w in ws if w['w'] == name), None)
            A.spectrogram_png(src, png, a, b, f_lo=f_lo, f_hi=f_hi, words=ws, target=target, ruler=True)
            pics[str(side)] = f'files/q{q:02d}_{side}.png'
        y, sr = sf.read(it['real'], dtype='float32', always_2d=True)
        cut = y[int(t0 * sr):int(t1 * sr)]
        wav = os.path.join(files, f'q{q:02d}_word.wav')
        sf.write(wav, cut, sr)
        sources[str(q)] = {'real': {'file': os.path.abspath(it['real']), 'window': [t0, t1]},
                           'other': {'file': os.path.abspath(it['other']), 'window': [o0, o1]}}
        man['items'].append({'q': q, 'word': it['word'], 'audio': f'files/q{q:02d}_word.wav', 'window': [t0, t1],
                             'img': pics, 'words': [{'w': w['w'], 'ms': [round(1000 * (w['t0'] - t0)),
                                                                         round(1000 * (w['t1'] - t0))]}
                                                    for w in words]})
        key[str(q)] = {'real': str(real_side), 'word': it['word'], 'label': it.get('other_label', 'ours'),
                       'version': it.get('version', 'ours'), 'pair': it.get('pair', q)}
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf8') as f:
        json.dump(man, f, indent=1)
    with open(os.path.join(out, 'key.json'), 'w', encoding='utf8') as f:
        json.dump(key, f, indent=1)
    with open(os.path.join(out, 'sources.json'), 'w', encoding='utf8') as f:      # never served; region_measure reads it
        json.dump(sources, f, indent=1)
    return man


def answers(out):
    """The rows a round's page saved, preflight rows left out."""
    try:
        with open(os.path.join(out, 'answers.jsonl'), encoding='utf8') as f:
            rows = [json.loads(l) for l in f if l.strip()]
    except OSError:
        return []
    return [r for r in rows if not r.get('preflight')]


def score(out):
    """The last submit of a round against its key, as text (vox work/score_words.py): per card right, WRONG, cant or
    none, the flips, blinks and plays, the note, and every pinned comment on the real picture (REAL) or ours (OURS);
    then the tally per version."""
    rows = answers(out)
    if not rows:
        return f"no answers yet in {os.path.join(out, 'answers.jsonl')}"
    a = rows[-1]
    with open(os.path.join(out, 'key.json'), encoding='utf8') as f:
        k = json.load(f)
    L, tally = [], {}
    for x in a['answers']:
        kk = k[str(x['q'])]
        ok = 'cant' if x['pick'] == 'cant' else ('none' if not x['pick'] else
                                                  ('right' if x['pick'] == kk['real'] else 'WRONG'))
        tally.setdefault(kk['version'], []).append(ok)
        L.append(f"q{x['q']} {kk['word']:6} {kk['version']:8} p{kk['pair']} {ok:5} flips {x.get('flips', 0)} "
                 f"blinks {x.get('blinks', 0)} plays {x.get('plays', 0)}")
        if x.get('note'):
            L.append(f"   note: {x['note']}")
        for c in x.get('comments', []):
            side = 'REAL' if c['picture'] == kk['real'] else 'OURS'
            where = (f"{c['ms']}-{c['ms2']} ms {c['khz']}-{c['khz2']} kHz" if 'ms2' in c
                     else f"{c['ms']} ms {c['khz']} kHz")
            L.append(f"   #{c['n']} {where} \"{c['word']}\" on {side}: {c['text']}")
    for v, r in tally.items():
        L.append(f"{v} " + ', '.join(f"{s} {r.count(s)}" for s in ('right', 'WRONG', 'cant', 'none') if r.count(s)))
    L.append(f"saved {a.get('saved_at')}")
    return '\n'.join(L)


def machine_vocab_path():
    return os.environ.get('ISMAIL_EXAM_VOCAB') or os.path.join(os.path.expanduser('~'), '.ismail', 'exam_vocab.json')


def _clean_name(name):
    return re.sub(r'\s+', ' ', re.sub(r'[\x00-\x1f<>"]', ' ', str(name or ''))).strip().lower()[:40]


def _names(path):
    try:
        with open(path, encoding='utf8') as f:
            return [n for n in (_clean_name(x) for x in json.load(f).get('names', [])) if n]
    except (OSError, ValueError, AttributeError):
        return []


def vocab():
    """The names an area can take: {'names': all (the repo's first, then this machine's), 'repo': [...], 'mine':
    [...]}. The repo file (vocab.json) is shared; names people add live in a per-machine copy and are only added to."""
    repo, mine = _names(VOCAB), _names(machine_vocab_path())
    mine = [n for n in dict.fromkeys(mine) if n not in repo]
    return {'names': repo + mine, 'repo': repo, 'mine': mine}


def add_name(name):
    """Remember a new name in this machine's copy (never in the repo file). -> (clean name, True if it was new)."""
    name = _clean_name(name)
    if not name:
        raise ValueError('an area needs a name')
    if name in vocab()['names']:
        return name, False
    path = machine_vocab_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as f:
        json.dump({'names': _names(path) + [name]}, f, indent=1)
    return name, True


def areas(out):
    """The areas a round's page saved, one dict per line of areas.jsonl."""
    from .. import regions
    return regions.read_jsonl(os.path.join(out, 'areas.jsonl'))


def save_area(out, body):
    """Validate and append one named area to <out>/areas.jsonl. body from the page: {q, picture ('1' or '2' as shown),
    name, note, poly: [[fx, fy]] fractions of the picture from the left and from the top}. The key says whether the
    picture shown was the real one or ours. -> the saved row. Raises ValueError with what is wrong."""
    from .. import regions
    with open(os.path.join(out, 'manifest.json'), encoding='utf8') as f:
        man = json.load(f)
    with open(os.path.join(out, 'key.json'), encoding='utf8') as f:
        key = json.load(f)
    it = next((i for i in man['items'] if i['q'] == body.get('q')), None)
    if it is None:
        raise ValueError(f"no card {body.get('q')!r} in this round")
    pic = str(body.get('picture'))
    if pic not in it['img']:
        raise ValueError('picture must be "1" or "2"')
    try:
        frac = [[float(x), float(y)] for x, y in body.get('poly') or []]
    except (TypeError, ValueError):
        raise ValueError('poly must be a list of [fx, fy] fractions of the picture')
    if len(frac) < 3 or any(not (-0.5 <= v <= 1.5) for p in frac for v in p):
        raise ValueError('poly needs at least 3 points, as fractions of the picture')
    name, new = add_name(body.get('name'))
    t0, t1 = it['window']
    poly = regions.polygon_to_sec_hz(frac, man['axes'], t1 - t0, man.get('f_lo', 0.0), man['f_hi'])
    if len(poly) < 3:
        raise ValueError('the outline collapsed to fewer than 3 points')
    row = {'id': f"{man['round']}.a{len(areas(out)) + 1:03d}", 'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
           'round': man['round'], 'q': it['q'], 'word': it.get('word'), 'picture': pic,
           'shown': 'real' if pic == key[str(it['q'])]['real'] else 'ours', 'name': name, 'new_name': new,
           'note': str(body.get('note') or '')[:2000], 'window': [t0, t1], 'poly': poly,
           'f_lo': man.get('f_lo', 0.0), 'f_hi': man['f_hi']}
    with open(os.path.join(out, 'areas.jsonl'), 'a', encoding='utf8') as f:
        f.write(json.dumps(row) + '\n')
    return row


def _handler(root):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='text/plain; charset=utf-8', extra=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _round(self, name):
            d = os.path.join(root, name)
            return d if SAFE.match(name) and os.path.isfile(os.path.join(d, 'manifest.json')) else None

        def _file(self, path):
            """A file with HTTP byte ranges, so long audio can seek (M12: http.server answered 200 to every Range)."""
            size = os.path.getsize(path)
            ctype = {'.png': 'image/png', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.json': 'application/json'}.get(
                os.path.splitext(path)[1].lower(), 'application/octet-stream')
            m = re.match(r'bytes=(\d*)-(\d*)$', self.headers.get('Range', ''))
            with open(path, 'rb') as f:
                if m and (m.group(1) or m.group(2)):
                    a = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
                    b = min(size - 1, int(m.group(2))) if m.group(1) and m.group(2) else size - 1
                    if a >= size:
                        return self._send(416, b'', extra={'Content-Range': f'bytes */{size}'})
                    f.seek(a)
                    return self._send(206, f.read(b - a + 1), ctype,
                                      {'Content-Range': f'bytes {a}-{b}/{size}', 'Accept-Ranges': 'bytes'})
                return self._send(200, f.read(), ctype, {'Accept-Ranges': 'bytes'})

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            p = self.path.split('?')[0]
            if p == '/health':
                return self._send(200, b'{"ok": true, "what": "ismail exam page"}', 'application/json')
            if p in ('/', '/eye', '/eye/'):
                rounds = sorted(d for d in os.listdir(root) if self._round(d))
                body = '<!doctype html><meta charset="utf-8"><title>Eye exams</title><ul>' + ''.join(
                    f'<li><a href="/eye/{r}">{r}</a></li>' for r in rounds) + '</ul>'
                return self._send(200, body.encode(), 'text/html; charset=utf-8')
            m = re.match(r'^/eye/([^/]+)/?(.*)$', p)
            if not m or not self._round(m.group(1)):
                return self._send(404, b'no such round')
            d, rest = self._round(m.group(1)), m.group(2)
            if rest == '':
                with open(PAGE, 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if rest == 'manifest.json':
                return self._file(os.path.join(d, 'manifest.json'))
            if rest == 'key':
                if not answers(d):
                    return self._send(403, b'answer the round first')
                return self._file(os.path.join(d, 'key.json'))
            if rest == 'vocab':
                return self._send(200, json.dumps(vocab()).encode(), 'application/json')
            if rest == 'areas':                     # the reveal only, like the key
                if not answers(d):
                    return self._send(403, b'answer the round first')
                return self._send(200, json.dumps(areas(d)).encode(), 'application/json')
            m2 = re.match(r'^files/([^/]+)$', rest)
            if m2 and SAFE.match(m2.group(1)) and os.path.isfile(os.path.join(d, 'files', m2.group(1))):
                return self._file(os.path.join(d, 'files', m2.group(1)))
            return self._send(404, b'not found')

        def do_POST(self):
            m = re.match(r'^/eye/([^/]+)/(answer|areas)$', self.path.split('?')[0])
            d = m and self._round(m.group(1))
            if not d:
                return self._send(404, b'no such round')
            try:
                row = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode('utf8'))
            except ValueError:
                return self._send(400, b'not JSON')
            if m.group(2) == 'areas':               # named areas: the reveal only, so only after an answer
                if not answers(d):
                    return self._send(403, b'answer the round first')
                try:
                    saved = save_area(d, row)
                except (ValueError, KeyError, OSError, AttributeError) as e:
                    return self._send(400, str(e).encode())
                return self._send(200, json.dumps({'ok': True, 'id': saved['id'], 'name': saved['name'],
                                                   'new_name': saved['new_name']}).encode(), 'application/json')
            row['saved_at'] = time.strftime('%Y-%m-%dT%H:%M:%S')
            with open(os.path.join(d, 'answers.jsonl'), 'a', encoding='utf8') as f:
                f.write(json.dumps(row) + '\n')
            return self._send(200, b'{"ok": true}', 'application/json')

    return H


def serve(root, port=PORT, host='127.0.0.1'):
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer((host, port), _handler(os.path.abspath(root)))
    print(f'[exampage] serving {root} on http://{host}:{port}/eye/', flush=True)
    httpd.serve_forever()


def running(port=PORT):
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            return b'exam page' in r.read()
    except OSError:
        return False


def host(root, port=PORT):
    """Start the page server for `root` in the background unless one answers on `port`. -> its base URL."""
    import subprocess
    if not running(port):
        os.makedirs(root, exist_ok=True)
        log = open(os.path.join(root, 'server.log'), 'a', encoding='utf8')
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == 'nt' else 0
        subprocess.Popen([sys.executable, '-m', 'ismail.exampage', 'serve', os.path.abspath(root), '--port',
                          str(port)], stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                         start_new_session=os.name != 'nt',
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for _ in range(50):
            if running(port):
                break
            time.sleep(0.1)
    return f'http://127.0.0.1:{port}/eye/'


def main(argv):
    if len(argv) >= 2 and argv[0] == 'serve':
        a = argv[1:]
        port = int(a[a.index('--port') + 1]) if '--port' in a else PORT
        hst = a[a.index('--host') + 1] if '--host' in a else '127.0.0.1'
        serve(a[0], port, hst)
    else:
        print(__doc__)
