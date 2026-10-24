"""The eye exam page (ledger:M165 step 2, from vox's rec/words.html; the hosted round of M12).

One sound per card, two pictures of the same window: the real one and ours. They sit on top of each other and show
one at a time, so the eye reads any difference as movement (the blink comparator): F flips, hold Shift to peek, B
blinks. The person answers about the picture showing (M: this one is the real one, S: this one is ours, 0: can't
tell), pins comments at a time and frequency (a click, or a dragged box), and the key is served only after an answer.

A round is a folder: manifest.json (what the page shows), key.json (never served before an answer), files/ (the
pictures and the sound) and answers.jsonl (one line per submit).

One server serves rounds from every song (ledger:M300). It looks for /eye/<name> in its own folder first, then in the
round registry, a small JSON file of name -> round folder ($ISMAIL_EXAMPAGE_ROUNDS, else ~/.ismail/exampage/
rounds.json) that it reads on every request. host() and `register` add a round to it; a round whose folder name is
taken by another song's round gets the song's name in front (round2 -> songb-round2), and host() returns that URL.

  python -m ismail.exampage serve <rounds folder> [--port 8871] [--host 127.0.0.1]
  python -m ismail.exampage register <round folder> [--port 8871]
"""
import json
import os
import random
import re
import shutil
import sys
import time

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words.html')
PORT = 8871
SAFE = re.compile(r'^[A-Za-z0-9_.-]+$')
ROUNDS_ENV = 'ISMAIL_EXAMPAGE_ROUNDS'


class CannotServe(RuntimeError):
    """host() can't make a round reachable; the message says why and what to do."""


# ------------------------------------------------------------------ the round registry (ledger:M300)

def registry_path():
    """$ISMAIL_EXAMPAGE_ROUNDS (read at each call), else ~/.ismail/exampage/rounds.json."""
    return os.environ.get(ROUNDS_ENV) or os.path.join(os.path.expanduser('~'), '.ismail', 'exampage', 'rounds.json')


def _live(d):
    return bool(d) and os.path.isfile(os.path.join(d, 'manifest.json'))


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def rounds():
    """The registry: {name: round folder}, only safe names whose folders still hold a manifest."""
    p = registry_path()
    reg = {}
    for i in range(10):                     # a read that lands while Windows swaps the file in tries again
        try:
            with open(p, encoding='utf8') as f:
                reg = json.load(f)
            break
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            if i == 9:
                return {}
            time.sleep(0.02)
    if not isinstance(reg, dict):
        return {}
    return {k: v for k, v in reg.items() if isinstance(k, str) and isinstance(v, str) and SAFE.match(k) and _live(v)}


def _write_rounds(reg):
    p = registry_path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    tmp = f'{p}.{os.getpid()}.tmp'
    with open(tmp, 'w', encoding='utf8') as f:
        json.dump(reg, f, indent=1, sort_keys=True)
    for i in range(50):
        try:
            return os.replace(tmp, p)
        except PermissionError:             # Windows: the server is reading it this instant
            if i == 49:
                raise
            time.sleep(0.02)


def register(folder, server_root=None):
    """Add the round in `folder` to the registry. -> the name it is served under: its folder name, or, when another
    round already has that name (in the registry, or in `server_root`, the folder the running server was started
    on, which it reads first), '<song>-<name>' with the song folder's name (then -2, -3, ...). A folder already in
    the registry keeps its name, so its URL stays the same when the round is rebuilt."""
    folder = os.path.abspath(folder)
    base = os.path.basename(folder)
    if not SAFE.match(base) or not _live(folder):
        raise CannotServe(f"{folder} is not a round (no manifest.json, or its name is not letters, digits, '_', "
                          "'-' or '.')")
    reg = rounds()

    def free(n):
        own = server_root and os.path.join(server_root, n)
        if own and _live(own) and not _same(own, folder):
            return False
        return n not in reg or _same(reg[n], folder)

    mine = sorted((n for n, d in reg.items() if _same(d, folder) and free(n)), key=len)
    if base in mine or (not mine and free(base)):
        name = base
    elif mine:
        name = mine[0]
    else:
        parent = os.path.dirname(folder)
        song = os.path.basename(os.path.dirname(parent)) or os.path.basename(parent)
        stem = re.sub(r'[^A-Za-z0-9_.-]+', '-', song).strip('-.') or 'song'
        name, k = f'{stem}-{base}', 2
        while not free(name):
            name, k = f'{stem}-{base}-{k}', k + 1
    reg[name] = folder
    _write_rounds(reg)
    return name



def build(out, items, title, intro='', seed=0, f_lo=0.0, f_hi=16000.0, labels=None):
    """A round in `out` from items [{word, real, other, window: [t0, t1] (s), other_window: [t0, t1] (default the
    same), words: [[t0, t1, text]] (s, in the real file), other_label: what the other picture is (shown after the
    answer), version, pair, ask: this card's own question, shown above its pictures}]. The sound card plays is the real file's window. -> the manifest."""
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
           'intro': intro, 'f_hi': f_hi, 'labels': labels or {}, 'items': []}
    key = {}
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
        man['items'].append({'q': q, 'word': it['word'], 'audio': f'files/q{q:02d}_word.wav', 'window': [t0, t1],
                             'img': pics, 'words': [{'w': w['w'], 'ms': [round(1000 * (w['t0'] - t0)),
                                                                         round(1000 * (w['t1'] - t0))]}
                                                    for w in words]})
        if it.get('ask'):
            man['items'][-1]['ask'] = str(it['ask'])
        key[str(q)] = {'real': str(real_side), 'word': it['word'], 'label': it.get('other_label', 'ours'),
                       'version': it.get('version', 'ours'), 'pair': it.get('pair', q)}
    with open(os.path.join(out, 'manifest.json'), 'w', encoding='utf8') as f:
        json.dump(man, f, indent=1)
    with open(os.path.join(out, 'key.json'), 'w', encoding='utf8') as f:
        json.dump(key, f, indent=1)
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
            """The round's folder: in this server's own folder first, then in the registry (another song's)."""
            if not SAFE.match(name):
                return None
            d = os.path.join(root, name)
            return d if _live(d) else rounds().get(name)

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
                return self._send(200, json.dumps({'ok': True, 'what': 'ismail exam page', 'root': root,
                                                   'registry': True}).encode(), 'application/json')
            if p in ('/', '/eye', '/eye/'):
                own = [d for d in os.listdir(root) if SAFE.match(d) and _live(os.path.join(root, d))]
                names = sorted(set(own) | set(rounds()))
                body = '<!doctype html><meta charset="utf-8"><title>Eye exams</title><ul>' + ''.join(
                    f'<li><a href="/eye/{r}">{r}</a></li>' for r in names) + '</ul>'
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
            m2 = re.match(r'^files/([^/]+)$', rest)
            if m2 and SAFE.match(m2.group(1)) and os.path.isfile(os.path.join(d, 'files', m2.group(1))):
                return self._file(os.path.join(d, 'files', m2.group(1)))
            return self._send(404, b'not found')

        def do_POST(self):
            m = re.match(r'^/eye/([^/]+)/answer$', self.path.split('?')[0])
            d = m and self._round(m.group(1))
            if not d:
                return self._send(404, b'no such round')
            try:
                row = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))).decode('utf8'))
            except ValueError:
                return self._send(400, b'not JSON')
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


def _health(port):
    """What the exam page server on `port` says about itself ({} from one older than the registry), or None when
    no exam page answers there."""
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            body = r.read()
    except OSError:
        return None
    if b'exam page' not in body:
        return None
    try:
        h = json.loads(body)
    except ValueError:
        return {}
    return h if isinstance(h, dict) else {}


def running(port=PORT):
    return _health(port) is not None


def _served(port, name, folder):
    """True when /eye/<name>/manifest.json on `port` is this folder's manifest, byte for byte."""
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/eye/{name}/manifest.json', timeout=5) as r:
            got = r.read()
        with open(os.path.join(folder, 'manifest.json'), 'rb') as f:
            return got == f.read()
    except OSError:
        return False


def host(root, port=PORT, rnd=None):
    """Make rounds reachable on 127.0.0.1:`port`: start the page server on `root` in the background unless one
    answers there, then add the round `rnd` (a folder in `root`; every round in `root` when None) to the round
    registry, which that server reads on each request, so one server serves rounds from every song (ledger:M300).
    Each round is then read back through the server. -> the round's own URL when `rnd` is given (its name may carry
    the song's name, see register()), else the base URL. Raises CannotServe, saying why, instead of returning a URL
    that 404s: no server would start, the running server is older than the registry (restart it), or the round
    can't be read back through it."""
    import subprocess
    root = os.path.abspath(root)
    h = _health(port)
    if h is None:
        os.makedirs(root, exist_ok=True)
        log = open(os.path.join(root, 'server.log'), 'a', encoding='utf8')
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == 'nt' else 0
        subprocess.Popen([sys.executable, '-m', 'ismail.exampage', 'serve', root, '--port', str(port)],
                         stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                         start_new_session=os.name != 'nt',
                         cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        for _ in range(50):
            h = _health(port)
            if h is not None:
                break
            time.sleep(0.1)
        if h is None:
            raise CannotServe(f"the exam page server did not start on port {port} (is something else on that "
                              f"port? see {os.path.join(root, 'server.log')})")
    names = [rnd] if rnd is not None else sorted(d for d in os.listdir(root) if _live(os.path.join(root, d)))
    urls = []
    for n in names:
        folder = os.path.join(root, n)
        name = register(folder, h.get('root'))
        if not _served(port, name, folder):
            if not h.get('registry'):
                raise CannotServe(
                    f"the exam page server on port {port} is older than the round registry and serves another "
                    f"folder, so /eye/{name} would 404. Restart it: stop the running one, then python -m "
                    f"ismail.exampage serve {root} --port {port}. The new one serves rounds from every song.")
            raise CannotServe(f"the exam page server on port {port} does not serve {folder} as /eye/{name} "
                              f"(the registry is {registry_path()})")
        urls.append(f'http://127.0.0.1:{port}/eye/{name}')
    return urls[0] if rnd is not None else f'http://127.0.0.1:{port}/eye/'


def main(argv):
    if len(argv) >= 2 and argv[0] == 'serve':
        a = argv[1:]
        port = int(a[a.index('--port') + 1]) if '--port' in a else PORT
        hst = a[a.index('--host') + 1] if '--host' in a else '127.0.0.1'
        serve(a[0], port, hst)
    elif len(argv) >= 2 and argv[0] == 'register':
        a = argv[1:]
        port = int(a[a.index('--port') + 1]) if '--port' in a else PORT
        d = os.path.abspath(a[0])
        try:
            print(host(os.path.dirname(d), port, os.path.basename(d)))
        except CannotServe as e:
            print(f'[exampage] {e}', file=sys.stderr)
            sys.exit(1)
    else:
        print(__doc__)
