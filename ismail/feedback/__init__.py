"""Feedback pages: questions the person answers by typing or by voice, kept on this computer (hq:D-166).

A desk page that takes the person's voice must be hosted locally, open in their own default browser (an agent app's
browser pane has no microphone) and bring back everything they typed or said when they reopen it. Agents kept
building this by hand (a shot-notes server, a recording server); feedback_page switches it on.

A round is a folder in the project, <project>/work/feedback/<round>/:
  manifest.json      what the page shows: title, items (clips, pictures, text), numbered questions, voice on or off
  answers.json       per question: the typed text (saved as they type) and every voice clip with its transcript
  answers_log.jsonl  every write in order, so no earlier wording is lost to a later edit
  done.json          written when the person presses Send: every send time, and when they changed something after
                     the last one (a late addition), so an agent acts only on a round they sent (ledger:M246)
  names.json         the person's names for the parts in the parts block, {track: {text, at}} (ledger:M253)
  moments.json       with moments on: the moments the person marked while a clip played, {'moments': [{id, t (seconds
                     in the clip), tags, parts, text, at}], 'at'} (ledger:M263)
  parts/             the parts block's clips: each part alone and in the mix at its most exposed bars
  voice/             the voice clips (q<n>_<stamp>.webm, .ogg, .m4a or .wav), each transcript beside it as .txt
  share/             with share=True: a standalone copy of the page (index.html and the clips beside it) for a
                     listener who is not at this computer (ledger:M255). It needs no server: it is hosted anywhere,
                     keeps their answers in their browser, and sends them back as a message (the share menu, or a copy
                     to paste, with the voice notes as files); feedback_paste reads that message into the round

One shared server serves every round on 127.0.0.1:

  python -m ismail.feedback serve [--port 8890] [--home <dir>] [--no-stt]

feedback_page finds it or starts it. The rounds it serves are in <home>/rounds.json and its port in
<home>/server.json (<home>: $ISMAIL_FEEDBACK_HOME, else ~/.ismail/feedback). Voice clips are transcribed in the
background by the speech server the phone page uses (ismail.phone.server.stt, $ISMAIL_SPEAK), waiting while the CPU
is busy; a clip with a .txt beside it counts as transcribed, and clips left waiting when the server stopped go back
in line when it starts again.
"""
import collections
import datetime
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

PAGE = Path(__file__).resolve().parent / 'page.html'
PORT0, PORT_SPAN = 8890, 100
# ports other ismail servers and live sessions use: a feedback server never takes one of them
RESERVED_PORTS = {8765, 8813, 8862, 8870, 8871, 8879, 8886, 8887}
SAFE = re.compile(r'^[A-Za-z0-9_.-]+$')
WHAT = 'ismail feedback pages'
AUDIO = {'.wav', '.mp3', '.ogg', '.flac', '.m4a', '.aac', '.opus'}
VIDEO = {'.mp4', '.webm', '.mov', '.mkv', '.m4v'}
IMAGE = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}
CTYPES = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.flac': 'audio/flac', '.m4a': 'audio/mp4',
          '.aac': 'audio/aac', '.opus': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm',
          '.mov': 'video/quicktime', '.mkv': 'video/x-matroska', '.m4v': 'video/mp4', '.png': 'image/png',
          '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp',
          '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8', '.json': 'application/json'}
VOICE_MAX_BYTES = 64 << 20
STT_RETRY_S = 20
CPU_WAIT_S = 10


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def parse_time(s):
    """An ISO time (with or without its offset; without one it is this computer's local time) -> aware datetime."""
    t = datetime.datetime.fromisoformat(str(s).strip().replace('Z', '+00:00'))
    return t if t.tzinfo else t.astimezone()


def home():
    return Path(os.environ.get('ISMAIL_FEEDBACK_HOME') or Path.home() / '.ismail' / 'feedback')


def _same(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


# ------------------------------------------------------------------ files, read and written with retries
# Windows refuses os.replace while another process holds the target open (feedback_read, a second tab's request), so
# a write tries again for a moment, and a read that lands mid-write tries again too.

def read_json(path, default=None):
    for i in range(20):
        try:
            with open(path, encoding='utf8') as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except (PermissionError, ValueError):
            time.sleep(0.05 * (i + 1))
    return default


def write_json(path, data):
    path = str(path)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, 'w', encoding='utf8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for i in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    os.replace(tmp, path)


# ------------------------------------------------------------------ a round

def round_dir(project, rnd):
    return Path(project).resolve() / 'work' / 'feedback' / str(rnd)


def sent_state(d):
    """Where a round stands (ledger:M246): {'state': 'sent' | 'changed' | 'writing', 'sent': [every send time],
    'at': the last send or None, 'changed_at': the first change after the last send or None, 'last_save': the newest
    typed or said time or None}. 'changed' is a late addition: sent, then changed, not sent again yet."""
    d = Path(d)
    done = read_json(d / 'done.json', None) or {}
    ans = read_json(d / 'answers.json', {}) or {}
    saves = [a.get('typed_at') for a in ans.values() if a.get('typed_at')]
    saves += [c.get('at') for a in ans.values() for c in a.get('said') or [] if c.get('at')]
    saves += [m.get('at') for m in moments(d) if m.get('at')]
    last = max(saves, key=parse_time) if saves else None
    sent = list(done.get('sent') or ([done['at']] if done.get('at') else []))
    at = sent[-1] if sent else None
    changed = done.get('changed_at') if at else None
    return {'state': 'writing' if not at else 'changed' if changed else 'sent', 'sent': sent, 'at': at,
            'changed_at': changed, 'last_save': last}


def _kind(path):
    ext = os.path.splitext(str(path))[1].lower()
    return 'audio' if ext in AUDIO else 'video' if ext in VIDEO else 'image' if ext in IMAGE else None


def _item(n, it, project):
    """One item of the page -> {n, label, kind, path | text}."""
    if isinstance(it, str):
        it = {'path': it} if _kind(it) else {'text': it}
    if not isinstance(it, dict):
        raise ValueError(f"item {n}: give {{label, path}} for a clip or picture, or {{label, text}} for text")
    path = it.get('path') or it.get('file') or it.get('src')
    text = it.get('text')
    if path:
        p = Path(path)
        if not p.is_absolute():
            p = (Path(project) / p) if (Path(project) / p).exists() else p.resolve()
        if not p.is_file():
            raise ValueError(f"item {n}: no file {path} (paths are absolute, or relative to the project folder)")
        kind = it.get('kind') or _kind(p)
        if kind not in ('audio', 'video', 'image'):
            raise ValueError(f"item {n}: {p.name} is not a clip or a picture the page can show (audio "
                             f"{sorted(AUDIO)}, video {sorted(VIDEO)}, pictures {sorted(IMAGE)}); put words in "
                             f"text= instead")
        return {'n': n, 'label': str(it.get('label') or p.stem), 'kind': kind, 'path': str(p.resolve())}
    if text is None:
        raise ValueError(f"item {n}: give it a path (a clip or a picture) or text")
    return {'n': n, 'label': str(it.get('label') or ''), 'kind': 'text', 'text': str(text)}


def _question(n, q):
    if isinstance(q, dict):
        q = q.get('text') or q.get('q') or q.get('question')
    if not q or not str(q).strip():
        raise ValueError(f"question {n} is empty: each question is a string (or {{text}})")
    return {'n': n, 'text': str(q).strip()}


def build(project, rnd, items, questions, voice=True, title=None, folder=None, footer=None, parts=None,
          moments=None):
    """Write (or rewrite) a round's manifest; answers already given stay. folder: where the round goes (default
    <project>/work/feedback/<round>; a song's proj/ passes the song folder's, which a rebuild leaves alone).
    footer: sources.footer()'s dict, shown at the bottom of the page (ledger:M254), or None. parts: the parts block,
    [{track, bars, solo, mix}] from feedback.parts.clips (ledger:M253), or None. moments: {'item': the audio item's
    number (1-based), 'parts': [{label, tracks}], 'grid': {bpm, bpb, offset_sec, start_sec} or None} (ledger:M263),
    or None. -> (folder, manifest, kept answers)."""
    d = Path(folder) if folder else round_dir(project, rnd)
    its = [_item(n, it, project) for n, it in enumerate(items or [], 1)]
    if not questions:
        raise ValueError("no questions: give at least one, e.g. questions=['What do you hear first?']")
    qs = [_question(n, q) for n, q in enumerate(questions, 1)]
    (d / 'voice').mkdir(parents=True, exist_ok=True)
    old = read_json(d / 'manifest.json', {}) or {}
    name = d.parents[2].name if folder else Path(project).resolve().name
    man = {'title': title or old.get('title') or f"{name}: {rnd}", 'round': str(rnd),
           'project': str(Path(project).resolve()), 'voice': bool(voice), 'items': its, 'questions': qs,
           'made': old.get('made') or now(), 'updated': now()}
    if parts:
        man['parts'] = [dict(p, n=i) for i, p in enumerate(parts, 1)]
    if moments:
        it = next((x for x in its if x['n'] == moments['item']), None)
        if not it or it['kind'] != 'audio':
            raise ValueError(f"moments go on an audio item, and item {moments['item']} is not one")
        man['moments'] = {'item': it['n'], 'tags': list(MOMENT_TAGS), 'parts': list(moments.get('parts') or []),
                          'grid': moments.get('grid')}
    if footer:
        from ..sources import FOOTER_LEAD
        man['footer'] = {'lead': FOOTER_LEAD, 'link': footer['link'], 'how': footer.get('how') or ''}
    write_json(d / 'manifest.json', man)
    ans = read_json(d / 'answers.json', None)
    if ans is None:
        write_json(d / 'answers.json', {})
        ans = {}
    kept = sum(1 for a in ans.values() if (a.get('text') or '').strip() or a.get('said'))
    return d, man, kept


def public_manifest(man):
    """What the page sees: no paths on this computer, each file by its number."""
    out = {k: man.get(k) for k in ('title', 'round', 'voice', 'questions', 'footer')}
    mo = man.get('moments')
    if mo:
        out['moments'] = {'item': mo['item'], 'tags': mo.get('tags') or list(MOMENT_TAGS),
                          'parts': [p['label'] for p in mo.get('parts') or []], 'grid': mo.get('grid')}
    out['items'] = [{k: v for k, v in it.items() if k != 'path'} | ({'src': f"item/{it['n']}"} if it.get('path')
                                                                     else {}) for it in man.get('items', [])]
    out['parts'] = [{'n': p['n'], 'track': p['track'], 'bars': p.get('bars'), 'solo': f"part/{p['n']}/solo",
                     'mix': f"part/{p['n']}/mix"} for p in man.get('parts') or []]
    return out


# ------------------------------------------------------------------ moments (ledger:M263)
# The listener marks a moment while the clip plays: a time in that clip, tick boxes and their words.

MOMENT_TAGS = ('Love this', 'Too much', 'Too empty', 'A sound is off', 'Wants a change here', 'Hit harder')
MOMENTS_MAX = 500
MOMENTS_HEAD = 'Moments:'


def moments(d):
    """The moments kept in a round, in time order: [{id, t, tags, parts, text, at}]."""
    ms = (read_json(Path(d) / 'moments.json', {}) or {}).get('moments') or []
    return sorted((m for m in ms if isinstance(m, dict)), key=lambda m: float(m.get('t') or 0))


def _words(xs):
    return [str(x).strip()[:80] for x in (xs if isinstance(xs, list) else []) if str(x).strip()][:20]


def clean_moments(rows):
    """Moments from the page or a paste, checked: [{id, t (seconds, >= 0), tags, parts, text}]. Raises ValueError."""
    if not isinstance(rows, list):
        raise ValueError('moments: a list of {t, tags, parts, text}')
    if len(rows) > MOMENTS_MAX:
        raise ValueError(f'moments: at most {MOMENTS_MAX}')
    out = []
    for i, m in enumerate(rows, 1):
        if not isinstance(m, dict):
            raise ValueError(f'moment {i}: give {{t, tags, parts, text}}')
        t = float(m.get('t'))
        if not 0 <= t <= 24 * 3600:
            raise ValueError(f'moment {i}: t is the seconds into the clip')
        out.append({'id': str(m.get('id') or f'm{i}')[:40], 't': round(t, 1), 'tags': _words(m.get('tags')),
                    'parts': _words(m.get('parts')), 'text': str(m.get('text') or '').strip()[:4000]})
    return out


def clock(t):
    t = int(float(t or 0))
    return f"{t // 60}:{t % 60:02d}"


def moment_bar(t, grid):
    """The song bar (1-based) a time in the clip falls in, or None when the round is not on a song."""
    if not grid or not grid.get('bpm'):
        return None
    bar_s = float(grid.get('bpb') or 4) * 60.0 / float(grid['bpm'])
    song_t = float(grid.get('start_sec') or 0) + float(t)
    return int((song_t - float(grid.get('offset_sec') or 0)) // bar_s) + 1


def moment_line(m, grid=None):
    """One moment as the page's Copy text writes it: '1:23 (bar 33): Too much, bass. <their words>'."""
    bar = moment_bar(m['t'], grid)
    head = ', '.join(list(m.get('tags') or []) + list(m.get('parts') or []))
    words = ' '.join(str(m.get('text') or '').split())
    return clock(m['t']) + (f" (bar {bar})" if bar else '') + ': ' + ('. '.join(x for x in (head, words) if x) or '-')


_MOMENT_LINE = re.compile(r'^\s*(\d{1,3}):([0-5]\d)(?:\.(\d+))?\s*(?:\([^)]*\))?\s*:\s*(.*?)\s*$')


def split_moments(text):
    """-> (the text without its 'Moments:' block, [the block's moment lines]). The block is the heading and the
    moment lines under it, up to the first line that is not one."""
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    i = next((k for k, ln in enumerate(lines) if ln.strip().lower() == MOMENTS_HEAD.lower()), None)
    if i is None:
        return '\n'.join(lines), []
    j = i + 1
    while j < len(lines) and (not lines[j].strip() or _MOMENT_LINE.match(lines[j])):
        j += 1
    return '\n'.join(lines[:i] + lines[j:]), [ln for ln in lines[i + 1:j] if ln.strip()]


def parse_moments(text, tags=MOMENT_TAGS, parts=()):
    """The 'Moments:' block of a pasted message -> [{id, t, tags, parts, text}]. Before the first '. ', words that
    are all tick-box names (tags or parts) read as ticks; the rest is their words."""
    known_t = {str(x).lower(): x for x in tags}
    known_p = {str(x).lower(): x for x in parts}
    out = []
    for k, ln in enumerate(split_moments(text)[1], 1):
        m = _MOMENT_LINE.match(ln)
        t = int(m.group(1)) * 60 + int(m.group(2)) + (float('0.' + m.group(3)) if m.group(3) else 0.0)
        rest = '' if m.group(4).strip() == '-' else m.group(4).strip()
        head, _, tail = rest.partition('. ')
        bits = [b.strip().rstrip('.') for b in head.split(',') if b.strip()]
        tg, pt, words = [], [], rest
        if bits and all(b.lower() in known_t or b.lower() in known_p for b in bits):
            tg = [known_t[b.lower()] for b in bits if b.lower() in known_t]
            pt = [known_p[b.lower()] for b in bits if b.lower() not in known_t]
            words = tail.strip()
        out.append({'id': f'p{k}', 't': round(t, 1), 'tags': tg, 'parts': pt, 'text': words})
    return out


def names(d):
    """{track: the person's name for it} from a round's parts block (ledger:M253)."""
    return {t: (v.get('text') or '').strip() for t, v in (read_json(Path(d) / 'names.json', {}) or {}).items()
            if (v.get('text') or '').strip()}


# ------------------------------------------------------------------ a shared copy, and its answers coming back

SHARE_MARK = '<!--SHARED-->'


def share(d, man):
    """Write a round's standalone copy into <round>/share/ (ledger:M255): index.html with the manifest inside it and
    each clip or picture beside it (item<n><ext>), no path on this computer. -> (folder, [file names], bytes)."""
    import shutil
    out = Path(d) / 'share'
    out.mkdir(parents=True, exist_ok=True)
    pub = public_manifest(man)
    files = []
    for it, src in zip(man.get('items', []), pub['items']):
        if it.get('path'):
            name = f"item{it['n']}{os.path.splitext(it['path'])[1].lower()}"
            shutil.copyfile(it['path'], out / name)
            src['src'] = name
            files.append(name)
    for part, src in zip(man.get('parts') or [], pub['parts']):
        for k in ('solo', 'mix'):
            name = f"part{part['n']}-{k}{os.path.splitext(part[k])[1].lower()}"
            shutil.copyfile(part[k], out / name)
            src[k] = name
            files.append(name)
    rid = re.sub(r'[^A-Za-z0-9_.-]+', '-', f"{Path(d).parents[2].name}-{Path(d).name}").strip('-.') or 'round'
    data = json.dumps({'id': rid, 'manifest': pub}, ensure_ascii=False)
    data = data.replace('<', '\\u003c')                  # no '</script>' or '<!--' can end the script early
    html = PAGE.read_text(encoding='utf8').replace(SHARE_MARK, f'<script>window.FEEDBACK_SHARED = {data};</script>', 1)
    (out / 'index.html').write_text(html, encoding='utf8', newline='\n')
    size = sum((out / f).stat().st_size for f in files + ['index.html'])
    return out, files, size


_NUM = re.compile(r'^\s*(?:q(?:uestion)?\s*)?(\d{1,2})\s*[.):-]\s*(.*)$', re.I)
_VOICE_LINE = re.compile(r'^\s*\(voice note\b.*\)\s*$', re.I)


def _norm(s):
    return re.sub(r'[^a-z0-9]+', ' ', str(s).lower()).strip()


_NAMES_HEAD = re.compile(r'^\s*names for the parts\b', re.I)
_NAME_LINE = re.compile(r'^\s*[-*]\s*(.+?)\s*:\s*(.*?)\s*$')


def parse_names(text, tracks):
    """{track: name} from the 'Names for the parts' block of a pasted message ('- <track>: <their name>')."""
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    cut = next((i for i, ln in enumerate(lines) if _NAMES_HEAD.match(ln)), None)
    if cut is None:
        return {}
    out, known = {}, {str(t).lower(): t for t in tracks}
    for ln in lines[cut + 1:]:
        m = _NAME_LINE.match(ln)
        if m and m.group(1).lower() in known and m.group(2) and m.group(2) != '(no name)':
            out[known[m.group(1).lower()]] = m.group(2)
    return out


def parse_answers(text, questions):
    """A pasted message of answers (the page's 'Copy my answers' text, or any numbered reply) -> ({question n:
    answer}, [lines before the first answer that were left out]). Numbered lines that repeat a question's words head
    its answer, so a numbered list inside an answer stays in it; with no such line, bare numbers ('1. too loud')
    head them; one question takes the whole message. Raises ValueError when no numbered answer is found."""
    qs = {q['n']: _norm(q['text']) for q in questions}
    lines = str(text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    cut = next((i for i, ln in enumerate(lines) if _NAMES_HEAD.match(ln)), None)
    if cut is not None:
        lines = lines[:cut]
        text = '\n'.join(lines)
    text = split_moments(text)[0]                   # '1:23 (bar 33): ...' is a moment, not answer 1
    lines = text.split('\n')
    cands = []
    for i, ln in enumerate(lines):
        m = _NUM.match(ln)
        if m and int(m.group(1)) in qs:
            n, rest = int(m.group(1)), m.group(2).strip()
            r, qn = _norm(rest), qs[int(m.group(1))]
            echo = bool(r) and len(r) >= min(12, len(qn)) and (r[:30] == qn[:30] or qn.startswith(r) or r.startswith(qn))
            cands.append((i, n, rest, echo))

    def ordered(cs):
        heads, last = [], 0
        for c in cs:
            if c[1] > last:
                heads.append(c)
                last = c[1]
        return heads
    heads = ordered([c for c in cands if c[3]])
    echoed = bool(heads)
    if not heads:
        heads = ordered(cands)
    if not heads:
        if len(qs) == 1 and str(text or '').strip():
            body = [ln for ln in lines if not _VOICE_LINE.match(ln)]
            return {next(iter(qs)): '\n'.join(body).strip()}, []
        raise ValueError("no numbered answers in the text: paste the listener's message as they sent it (the page's "
                         "'Copy my answers' text numbers each answer as its question, e.g. '2. <the question>' and "
                         "the answer under it)")
    out = {}
    for k, (i, n, rest, _) in enumerate(heads):
        j = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        body = ([] if echoed else [rest]) + lines[i + 1:j]
        body = [ln for ln in body if not _VOICE_LINE.match(ln)]
        a = '\n'.join(body).strip()
        if a and a != '(no answer)':
            out[n] = a
    before = [ln.strip() for ln in lines[:heads[0][0]] if ln.strip() and not ln.strip().endswith('my answers')]
    return out, before


# ------------------------------------------------------------------ the registry: which rounds the server serves

def rounds(h=None):
    return read_json(Path(h or home()) / 'rounds.json', {}) or {}


def register(folder, h=None):
    """-> the round's id in URLs (<project>-<round>), the same one every time for the same folder."""
    h = Path(h or home())
    h.mkdir(parents=True, exist_ok=True)
    reg = rounds(h)
    for k, v in reg.items():
        if _same(v, folder):
            return k
    folder = Path(folder)
    base = re.sub(r'[^A-Za-z0-9_.-]+', '-', f"{folder.parents[2].name}-{folder.name}").strip('-.') or 'round'
    rid, i = base, 2
    while rid in reg:
        rid, i = f"{base}-{i}", i + 1
    reg[rid] = str(folder.resolve())
    write_json(h / 'rounds.json', reg)
    return rid


# ------------------------------------------------------------------ the server, found or started

def health(port, timeout=1.5):
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=timeout) as r:
            d = json.loads(r.read())
        return d if d.get('what') == WHAT else None
    except (OSError, ValueError):
        return None


def running(h=None):
    """-> the port our server (this home's) answers on, or None."""
    h = Path(h or home())
    st = read_json(h / 'server.json', {}) or {}
    port = st.get('port')
    if port:
        d = health(port)
        if d and _same(d.get('home', ''), h):
            return port
    return None


def port_free(port):
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(h=None):
    st = read_json(Path(h or home()) / 'server.json', {}) or {}
    first = [st['port']] if st.get('port') else []
    for p in first + list(range(PORT0, PORT0 + PORT_SPAN)):
        if p not in RESERVED_PORTS and port_free(p):
            return p
    raise RuntimeError(f"no free port in {PORT0}-{PORT0 + PORT_SPAN - 1}: pass port= with a free one")


def ensure(port=None, h=None, stt=True):
    """Find this home's server, or start it in the background. -> (port, started now?)."""
    import subprocess
    h = Path(h or home())
    up = running(h)
    if up:
        if port and int(port) != up:
            raise RuntimeError(f"the feedback server already runs on port {up}; leave port out to use it, or "
                               f"feedback_stop() first to move it to {port}")
        return up, False
    if port:
        port = int(port)
        if port in RESERVED_PORTS:
            raise RuntimeError(f"port {port} belongs to another ismail server or session; leave port out and one is "
                               f"picked")
        if not port_free(port):
            raise RuntimeError(f"port {port} is taken by something else; leave port out and a free one is picked")
    else:
        port = pick_port(h)
    h.mkdir(parents=True, exist_ok=True)
    log = open(h / 'server.log', 'a', encoding='utf8')
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == 'nt' else 0   # detached: outlives us
    cmd = [sys.executable, '-m', 'ismail.feedback', 'serve', '--port', str(port), '--home', str(h)]
    subprocess.Popen(cmd + ([] if stt else ['--no-stt']), stdout=log, stderr=subprocess.STDOUT,
                     stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != 'nt',
                     cwd=str(Path(__file__).resolve().parents[2]))
    for _ in range(100):
        d = health(port, timeout=0.5)
        if d and _same(d.get('home', ''), h):
            return port, True
        time.sleep(0.1)
    raise RuntimeError(f"the feedback server did not answer on port {port} within 10 s; its log is {h / 'server.log'}")


def stop(h=None):
    """-> the port it stopped on, or None when none ran."""
    import urllib.request
    h = Path(h or home())
    port = running(h)
    if not port:
        return None
    try:
        urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/shutdown', data=b'{}',
                                                      method='POST'), timeout=3).read()
    except OSError:
        pass
    for _ in range(50):
        if not health(port, timeout=0.3):
            break
        time.sleep(0.1)
    return port


# ------------------------------------------------------------------ the store: answers.json, one writer (the server)

class Store:
    def __init__(self, h, stt=True):
        self.home = Path(h)
        self.lock = threading.Lock()
        self.queue = collections.deque()       # (folder, q, clip id)
        self.tries = {}
        self.stt = stt
        self.stopped = threading.Event()

    def folder(self, rid):
        f = rounds(self.home).get(rid) if SAFE.match(rid or '') else None
        return Path(f) if f and (Path(f) / 'manifest.json').is_file() else None

    def _log(self, d, row):
        with open(d / 'answers_log.jsonl', 'a', encoding='utf8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')

    def type(self, d, q, text):
        with self.lock:
            ans = read_json(d / 'answers.json', {}) or {}
            row = ans.setdefault(str(q), {'text': '', 'typed_at': None, 'said': []})
            if row.get('text', '') == text:
                return row.get('typed_at')
            row['text'], row['typed_at'] = text, now()
            write_json(d / 'answers.json', ans)
            self._log(d, {'at': row['typed_at'], 'q': q, 'typed': text})
            self._changed_after_send(d, row['typed_at'])
            return row['typed_at']

    def _changed_after_send(self, d, t):
        """A change after Send (called under the lock): the round reads 'changed after sending' until Send again;
        the earlier send times stay."""
        done = read_json(d / 'done.json', None)
        if done and not done.get('changed_at'):
            done['changed_at'] = t
            write_json(d / 'done.json', done)

    def name(self, d, track, text):
        """The person's name for a part in the parts block (ledger:M253)."""
        with self.lock:
            nm = read_json(d / 'names.json', {}) or {}
            if (nm.get(track) or {}).get('text', '') == text:
                return nm[track].get('at')
            nm[track] = {'text': text, 'at': now()}
            write_json(d / 'names.json', nm)
            self._log(d, {'at': nm[track]['at'], 'part': track, 'named': text})
            self._changed_after_send(d, nm[track]['at'])
            return nm[track]['at']

    def moments(self, d, rows):
        """The person's moments, the whole list as the page holds it (ledger:M263). A moment kept unchanged keeps its
        save time; a new or changed one gets now. -> the time of this save, or of the last one when nothing changed."""
        rows = clean_moments(rows)
        same = lambda a, b: all(a.get(k) == b.get(k) for k in ('t', 'tags', 'parts', 'text'))   # noqa: E731
        with self.lock:
            old = read_json(d / 'moments.json', {}) or {}
            before = {m.get('id'): m for m in old.get('moments') or []}
            if len(rows) == len(before) and all(m['id'] in before and same(before[m['id']], m) for m in rows):
                return old.get('at')
            t = now()
            for m in rows:
                o = before.get(m['id'])
                m['at'] = o.get('at') if o and same(o, m) else t
            write_json(d / 'moments.json', {'moments': rows, 'at': t})
            self._log(d, {'at': t, 'moments': rows})
            self._changed_after_send(d, t)
            return t

    def send(self, d):
        """The person pressed Send: one more send time, and no change after it yet. -> the time."""
        with self.lock:
            done = read_json(d / 'done.json', None) or {}
            t = now()
            done['sent'] = list(done.get('sent') or []) + [t]
            done['at'], done['changed_at'] = t, None
            write_json(d / 'done.json', done)
            self._log(d, {'at': t, 'sent': True})
            return t

    def voice(self, d, q, data, mime, secs):
        ext = ('webm' if 'webm' in mime else 'ogg' if 'ogg' in mime else 'm4a' if 'mp4' in mime or 'm4a' in mime
               else 'wav' if 'wav' in mime else 'mp3' if 'mpeg' in mime or 'mp3' in mime else 'webm')
        t = time.time()
        vid = f"q{q}_{time.strftime('%Y%m%d-%H%M%S', time.localtime(t))}_{int(t * 1000) % 1000:03d}"
        (d / 'voice').mkdir(exist_ok=True)
        (d / 'voice' / f"{vid}.{ext}").write_bytes(data)
        clip = {'id': vid, 'file': f"{vid}.{ext}", 'at': now(), 'secs': round(float(secs or 0), 1), 'text': None,
                'state': 'waiting' if self.stt else 'waiting: transcription is off on this server'}
        with self.lock:
            ans = read_json(d / 'answers.json', {}) or {}
            ans.setdefault(str(q), {'text': '', 'typed_at': None, 'said': []}).setdefault('said', []).append(clip)
            write_json(d / 'answers.json', ans)
            self._log(d, {'at': clip['at'], 'q': q, 'said': clip['file'], 'secs': clip['secs']})
            self._changed_after_send(d, clip['at'])
            self.queue.append((d, str(q), vid))
        return clip

    def set_clip(self, d, q, vid, **kw):
        with self.lock:
            ans = read_json(d / 'answers.json', {}) or {}
            for c in (ans.get(str(q)) or {}).get('said', []):
                if c['id'] == vid:
                    c.update(kw)
            write_json(d / 'answers.json', ans)
            if kw.get('state') == 'done':
                self._log(d, {'at': now(), 'q': int(q), 'heard': vid, 'text': kw.get('text')})

    def requeue(self):
        """Clips said before a restart and never transcribed go back in line."""
        for rid, f in rounds(self.home).items():
            ans = read_json(Path(f) / 'answers.json', {}) or {}
            for q, row in ans.items():
                for c in row.get('said', []):
                    if c.get('state') != 'done':
                        self.queue.append((Path(f), q, c['id']))

    # -- the background transcriber
    def _clip_file(self, d, q, vid):
        ans = read_json(d / 'answers.json', {}) or {}
        c = next((c for c in (ans.get(q) or {}).get('said', []) if c['id'] == vid), None)
        return (d / 'voice' / c['file']) if c else None

    def run_transcriber(self):
        while not self.stopped.is_set():
            if not self.queue:
                self.stopped.wait(0.3)
                continue
            d, q, vid = self.queue[0]
            f = self._clip_file(d, q, vid)
            if f is None or not f.is_file():
                self.queue.popleft()
                continue
            given = f.with_suffix('.txt')        # its words, written beside it by whoever transcribed it elsewhere
            if given.is_file():
                text = given.read_text(encoding='utf8').strip()
            elif not self.stt:
                self.queue.rotate(-1)
                self.stopped.wait(1.0)
                continue
            else:
                why = cpu_busy()
                if why:
                    self.set_clip(d, q, vid, state='waiting: ' + why)
                    self.stopped.wait(CPU_WAIT_S)
                    continue
                self.set_clip(d, q, vid, state='transcribing')
                tries = self.tries.get(vid, 0)
                try:
                    text = transcribe(f, tries)
                except Exception as e:         # noqa: BLE001  the speech server busy or down: keep the clip, try again
                    self.tries[vid] = tries + 1
                    self.set_clip(d, q, vid, state=f"waiting: the speech server does not answer "
                                                   f"({type(e).__name__}, {tries + 1} tries)")
                    self.stopped.wait(STT_RETRY_S)
                    if len(self.queue) > 1 and self.queue[0][2] == vid:
                        self.queue.rotate(-1)
                    continue
                given.write_text(text, encoding='utf8')
            self.tries.pop(vid, None)
            self.set_clip(d, q, vid, text=text, state='done', heard_at=now())
            try:
                self.queue.remove((d, q, vid))
            except ValueError:
                pass


def cpu_busy():
    """Why transcription should wait now ('' when it may go): the machine governor's CPU limit."""
    try:
        from .. import machine
        busy, _ = machine.cpu_load()
        return f'the CPU is {busy:.0f}% busy' if busy >= machine.CPU_BUSY else ''
    except Exception:                          # noqa: BLE001  no governor reading: do not hold the words back
        return ''


def transcribe(path, tries=0):
    """The words in a voice clip, from the speech server the phone page uses (ismail.phone.server.stt)."""
    from ..phone.server import stt, STT_TIMEOUT_S
    return stt(Path(path).read_bytes(), Path(path).name, timeout=min(STT_TIMEOUT_S * 2 ** tries, 3600))


# ------------------------------------------------------------------ HTTP

def handler(store, page_html):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype='application/json', extra=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                try:
                    self.wfile.write(body)
                except (ConnectionError, OSError):
                    pass

        def _file(self, path):
            """A file with HTTP byte ranges, so audio and video seek."""
            size = os.path.getsize(path)
            ctype = CTYPES.get(os.path.splitext(str(path))[1].lower(), 'application/octet-stream')
            m = re.match(r'bytes=(\d*)-(\d*)$', self.headers.get('Range', ''))
            with open(path, 'rb') as f:
                if m and (m.group(1) or m.group(2)):
                    a = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
                    b = min(size - 1, int(m.group(2))) if m.group(1) and m.group(2) else size - 1
                    if a >= size:
                        return self._send(416, b'', extra={'Content-Range': f'bytes */{size}'})
                    b = min(b, a + (8 << 20) - 1)        # 8 MB a request: a long video still seeks quickly
                    f.seek(a)
                    return self._send(206, f.read(b - a + 1), ctype,
                                      {'Content-Range': f'bytes {a}-{b}/{size}', 'Accept-Ranges': 'bytes'})
                return self._send(200, f.read(), ctype, {'Accept-Ranges': 'bytes'})

        def _body(self, limit=VOICE_MAX_BYTES):
            n = int(self.headers.get('Content-Length') or 0)
            if n > limit:
                raise ValueError('too large')
            return self.rfile.read(n)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            from urllib.parse import urlparse
            p = urlparse(self.path).path
            if p == '/health':
                return self._send(200, {'ok': True, 'what': WHAT, 'home': str(store.home), 'pid': os.getpid(),
                                        'stt': store.stt, 'waiting': len(store.queue), 'send': True})
            if p in ('/', '/f', '/f/'):
                rows = ''.join(f'<li><a href="/f/{k}/">{k}</a></li>' for k in sorted(rounds(store.home))
                               if store.folder(k))
                return self._send(200, ('<!doctype html><meta charset="utf-8"><title>Feedback pages</title>'
                                        f'<h1>Feedback pages</h1><ul>{rows}</ul>').encode(),
                                  'text/html; charset=utf-8')
            m = re.match(r'^/f/([^/]+)(/?)(.*)$', p)
            d = m and store.folder(m.group(1))
            if not d:
                return self._send(404, {'error': 'no such round'})
            rest = m.group(3)
            if not m.group(2):
                return self._send(301, b'', extra={'Location': f'/f/{m.group(1)}/'})
            if rest == '':
                return self._send(200, page_html, 'text/html; charset=utf-8')
            if rest == 'manifest':
                return self._send(200, public_manifest(read_json(d / 'manifest.json', {})))
            if rest == 'state':
                st = sent_state(d)
                return self._send(200, {'answers': read_json(d / 'answers.json', {}) or {}, 'stt': store.stt,
                                        'names': read_json(d / 'names.json', {}) or {}, 'moments': moments(d),
                                        'sent': {k: st[k] for k in ('state', 'at', 'changed_at')}})
            mi = re.match(r'^item/(\d+)$', rest)
            if mi:
                it = next((i for i in read_json(d / 'manifest.json', {}).get('items', [])
                           if i['n'] == int(mi.group(1)) and i.get('path')), None)
                if it and os.path.isfile(it['path']):
                    return self._file(it['path'])
                return self._send(404, {'error': 'no such item'})
            mp = re.match(r'^part/(\d+)/(solo|mix)$', rest)
            if mp:
                part = next((x for x in read_json(d / 'manifest.json', {}).get('parts') or []
                             if x['n'] == int(mp.group(1))), None)
                if part and os.path.isfile(part[mp.group(2)]):
                    return self._file(part[mp.group(2)])
                return self._send(404, {'error': 'no such part'})
            mv = re.match(r'^voice/([A-Za-z0-9_.-]+)$', rest)
            if mv and os.path.isfile(d / 'voice' / mv.group(1)):
                return self._file(d / 'voice' / mv.group(1))
            return self._send(404, {'error': 'not found'})

        def _foreign(self):
            """A write from another site: any web page open in the person's browser can POST to 127.0.0.1, and its
            answers would reach an agent as the person's words. The page itself sends its own origin; the ops and
            urllib send none."""
            port = self.server.server_address[1]
            origin = self.headers.get('Origin')
            if origin is not None and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
                return True
            site = self.headers.get('Sec-Fetch-Site')
            return site is not None and site not in ('same-origin', 'none')

        def do_POST(self):
            from urllib.parse import parse_qs, urlparse
            if self._foreign():
                return self._send(403, {'error': 'writes come only from this page'})
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == '/shutdown':
                self._send(200, {'ok': True})
                store.stopped.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            m = re.match(r'^/f/([^/]+)/(text|voice|done|name|moments)$', u.path)
            d = m and store.folder(m.group(1))
            if not d:
                return self._send(404, {'error': 'no such round'})
            if m.group(2) == 'done':                   # the Send button (ledger:M246)
                return self._send(200, {'ok': True, 'at': store.send(d)})
            man = read_json(d / 'manifest.json', {})
            nq = len(man.get('questions', []))
            try:
                if m.group(2) == 'moments':                # marked while the clip plays (ledger:M263)
                    if not man.get('moments'):
                        raise ValueError('this round has no moments')
                    r = json.loads(self._body(1 << 20).decode('utf8'))
                    return self._send(200, {'ok': True, 'at': store.moments(d, r.get('moments'))})
                if m.group(2) == 'name':
                    r = json.loads(self._body(1 << 16).decode('utf8'))
                    track = str(r.get('track') or '')
                    if track not in {x['track'] for x in man.get('parts') or []}:
                        raise ValueError('no such part')
                    return self._send(200, {'ok': True, 'at': store.name(d, track, str(r.get('text') or '').strip())})
                if m.group(2) == 'text':
                    r = json.loads(self._body(1 << 20).decode('utf8'))
                    qn = int(r['q'])
                    if not 1 <= qn <= nq:
                        raise ValueError('no such question')
                    return self._send(200, {'ok': True, 'at': store.type(d, qn, str(r.get('text') or ''))})
                qn = int(q.get('q', 0))
                if not 1 <= qn <= nq:
                    raise ValueError('no such question')
                data = self._body()
                if len(data) < 16:
                    raise ValueError('empty recording')
                clip = store.voice(d, qn, data, (self.headers.get('Content-Type') or '').lower(), q.get('secs'))
                return self._send(200, {'ok': True, 'clip': clip})
            except (ValueError, KeyError, TypeError) as e:
                return self._send(400, {'error': str(e)})

    return H


def make_server(h=None, port=PORT0, stt=True):
    """The server object (not yet serving) with its store and the transcriber thread running."""
    from http.server import ThreadingHTTPServer
    store = Store(Path(h or home()).resolve(), stt=stt)
    store.home.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), handler(store, PAGE.read_bytes()))
    httpd.daemon_threads = True
    httpd.store = store
    store.requeue()
    threading.Thread(target=store.run_transcriber, daemon=True).start()
    return httpd


def serve(h=None, port=PORT0, stt=True):
    httpd = make_server(h, port, stt)
    real = httpd.server_address[1]
    write_json(httpd.store.home / 'server.json', {'port': real, 'pid': os.getpid(), 'started': now(), 'stt': stt})
    print(f'[feedback] serving the rounds in {httpd.store.home / "rounds.json"} on http://127.0.0.1:{real}/',
          flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.store.stopped.set()


def main(argv):
    if argv and argv[0] == 'serve':
        a = argv[1:]
        port = int(a[a.index('--port') + 1]) if '--port' in a else PORT0
        h = a[a.index('--home') + 1] if '--home' in a else None
        serve(h, port, stt='--no-stt' not in a)
    else:
        print(__doc__)
