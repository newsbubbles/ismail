"""The stage server: the page (ismail/stage/page), a song's scenes, and the live link between the page and agents.

  python -m ismail.stage.server --scenes <song>/video/vr/scenes            http://127.0.0.1:8862/   (desktop)
  python -m ismail.stage.server --scenes <dir> --tls --cert c.pem --key k.pem   https on the LAN (Quest)
  (behind `tailscale serve`, plain http on 127.0.0.1 is enough: the tailnet gives the headset https)

The page is served from the package; everything a session writes (logs, the speech cache, the built bundle, the
update notes) goes to <scenes>/_stage/, never into the package. One server per port: a busy port is refused (on
Windows two servers could both bind one port and split the live link between them). A running server records
itself in ~/.ismail/stage/<port>.json, which the stage_* ops read.

GET  /scenes             -> ["lucy", ...]  (folders under scenes/ that hold a scene.glb)
GET  /edits?scene=<name> -> the scene's edits.json, or null
GET  /takes?scene=<name> -> recorded performance takes (scenes/<name>/takes/<id>/meta.json + frames.jsonl)
POST /take/frames|/take/meta?scene=&take=  the page appends samples / merges metadata (hands.js)
POST /voice/in?scene=&kind=message|take[&take=]  audio from the headset mic (voice.js): a message is saved to
                         scenes/<name>/voice/ and transcribed by speakwright (CPU whisper, 127.0.0.1:8765) into a
                         `voice_message` event; a take's audio goes to takes/<id>/audio.<ext> and is transcribed
                         into takes/<id>/voice.json and a `take_voice` event (takes.py)
POST /voice/perf?scene=&perf=&clip=N&seq=K   a performance clip's audio, chunk K appended (perform.js; a Follow is
                         a performance); ...&end=1&at=&seconds=&by= closes clip N: transcribed with word times snapped
                         onto the voice (perform.py) into a `perform_clip` event and performances/<perf>/perf.json
POST /perf/meta?scene=&perf=   the page merges a performance's meta (person, markers, ended, take)
GET  /actor/profile?scene=&who=   an actor's profile (rigs.py): rig, named parts, control map presets
GET  /voice/say?text=&voice=   speech for the headset (speakwright's Kokoro), audio/wav
POST /snapshot?scene=<name>&tag=<camera>  (png body) -> scenes/<name>/snapshots/<tag>_<time>.png
POST /save?scene=<name>  -> writes scenes/<name>/edits.json; the previous one moves to scenes/<name>/history/ first

Live link (scene= may be left out: it then means the scene whose page posted state most recently):
POST /live/state?scene=      page -> full editor state; kept in memory and written to scenes/<name>/live/state.json
GET  /live/state?scene=      -> {"state": ..., "age_s": seconds since the page posted it}
POST /live/event?scene=      page -> one event or a list; each gets "id" (per scene, increasing) and "ts", and is
                             appended as one line to scenes/<name>/live/events.jsonl
GET  /live/events?scene=&since=N&wait=S&limit=L   -> {"last": id, "events": [id > N]}; wait=S long-polls up to S s
POST /live/cmd?scene=        Claude -> a command {"type": ..., ...} or a list; each gets an id; logged to live/cmds.jsonl
GET  /live/cmd?scene=&since=N&wait=S              -> {"last": id, "cmds": [id > N]}; since=-1 gives only "last"
GET  /live/version?scene=    -> {"glb": [mtime_ns, size], "manifest": [mtime_ns, size], "code": mtime_ns}
                               (the page hot-reloads the scene on change, and offers a reload when the code changes)
GET  /live                   -> every scene with a live page: scene, page, age_s, mode
GET  /livestream?name=bus:lucy[&port=P]  ismail live's stream (raw int16 stereo PCM) relayed from the engine's localhost
                             port, so the headset plays it same origin over https (stream.js); only engines in ismail's
                             registry (~/.ismail/live), the newest when port= is left out
"""
import argparse
import datetime
import json
import os
import re
import shutil
import ssl
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import perform, presence, takes

PAGE = Path(__file__).resolve().parent / 'page'
SCENES = Path('scenes').resolve()                          # set by configure(): the song's scenes folder
STATE = SCENES / '_stage'                                  # logs, speech cache, bundle, update notes
FOOTAGE = STATE / 'footage'                                # headset recordings sent from upload.html
PHRASES = STATE / 'speech'                                 # short lines Claude says, rendered once (spoken earcons)
CONFIG = {}                                                # ~/.ismail/stage.json: {"esbuild": path, "speak": url}
NAME = re.compile(r'^[A-Za-z0-9_\-]+$')


def registry_dir():
    return Path(os.environ.get('ISMAIL_STAGE_REGISTRY') or Path.home() / '.ismail' / 'stage')


def configure(scenes, footage=None, state=None):
    """Point the server at a scenes folder (and where headset footage and session files go)."""
    global SCENES, STATE, FOOTAGE, PHRASES, SPEAK
    SCENES = Path(scenes).resolve()
    STATE = Path(state).resolve() if state else SCENES / '_stage'
    FOOTAGE = Path(footage).resolve() if footage else STATE / 'footage'
    PHRASES = STATE / 'speech'
    STATE.mkdir(parents=True, exist_ok=True)
    presence.reset()
    try:
        CONFIG.update(json.loads((Path.home() / '.ismail' / 'stage.json').read_text(encoding='utf-8')))
    except (OSError, ValueError):
        pass
    SPEAK = os.environ.get('ISMAIL_SPEAK') or CONFIG.get('speak') or SPEAK


def scene_names():
    return sorted(p.name for p in SCENES.iterdir() if (p / 'scene.glb').is_file()) if SCENES.is_dir() else []


SRC_MTIME = Path(__file__).stat().st_mtime_ns     # a server running older code than server.py says so on every save


def code_version():
    """The newest page file (js, html, css, earcons): the page offers a reload when this changes."""
    fs = [*PAGE.glob('*.js'), *PAGE.glob('*.html'), *PAGE.glob('*.css'), *PAGE.glob('sounds/*.wav')]
    return max((f.stat().st_mtime_ns for f in fs), default=0)


BUNDLE_LOCK = threading.Lock()


def esbuild_path():
    """esbuild for the bundle: ESBUILD, or ~/.ismail/stage.json "esbuild", or none (the page loads its modules)."""
    p = os.environ.get('ESBUILD') or CONFIG.get('esbuild')
    return p if p and Path(p).exists() else None


def _not_finite(word):
    """json.loads' parse_constant: NaN and Infinity are refused, since a page reading them back fails to parse the
    command list and its queue stalls (the user, 2026-10-08)."""
    raise ValueError(f'{word} is not a number the page can read')


def bundle():
    """bundle.js in the state folder, rebuilt (build_bundle.mjs, esbuild) when a page module is newer: the headset
    boots in one request. None without esbuild."""
    out = STATE / 'bundle.js'
    eb = esbuild_path()
    with BUNDLE_LOCK:
        newest = max(f.stat().st_mtime_ns for f in PAGE.glob('*.js'))
        if not out.is_file() or out.stat().st_mtime_ns < newest:
            if not eb:
                return None
            import subprocess
            tmp = out.with_suffix('.js.tmp')
            try:
                r = subprocess.run(['node', str(PAGE / 'build_bundle.mjs')], cwd=PAGE, capture_output=True, text=True,
                                   timeout=120, env={**os.environ, 'ESBUILD': eb, 'BUNDLE_OUT': str(tmp)})
                print('[bundle]', (r.stdout + r.stderr).strip()[-600:], flush=True)
                if r.returncode != 0:
                    raise OSError(f'esbuild exit {r.returncode}')
                tmp.replace(out)
            except (OSError, subprocess.SubprocessError) as e:
                # a full disk or a broken build: the last good bundle (older code, but it boots), else the modules
                print(f'[bundle] rebuild failed ({e}); serving {"the last good bundle" if out.is_file() else "modules"}',
                      flush=True)
                return out if out.is_file() else None
    return out


def stale():
    return Path(__file__).stat().st_mtime_ns != SRC_MTIME


def save_edits(name, edits):
    d = SCENES / name
    if not NAME.match(name) or not (d / 'scene.glb').is_file():
        raise ValueError(f'no scene {name!r}')
    for key in edits:
        if key not in ('objects', 'lights', 'materials', 'reverted'):
            raise ValueError(f'unknown key {key!r}')
    # names the page put back where the export has them: the merge below would keep their old entry forever (a door
    # the user knocked out of its frame came back after every restore), so they are dropped from edits.json
    reverted = edits.pop('reverted', None) or {}
    out = d / 'edits.json'
    moved = None
    # the page sends what differs from the last export, and an export bakes earlier edits into the manifest, so a
    # save must merge into edits.json (the newest value wins) or every edit made before the last export is lost
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prev = {}
        merged = {}
        for key in ('objects', 'lights', 'materials'):
            m = dict(prev.get(key) or {})
            for n in reverted.get(key) or []:
                m.pop(n, None)
            m.update(edits.get(key) or {})
            merged[key] = m
        edits = merged
    if out.exists():
        hist = d / 'history'
        hist.mkdir(exist_ok=True)
        stamp = time.strftime('%Y%m%d_%H%M%S')
        moved = hist / f'edits_{stamp}.json'
        n = 1
        while moved.exists():
            moved = hist / f'edits_{stamp}_{n}.json'
            n += 1
        shutil.move(str(out), str(moved))
    tmp = d / 'edits.json.tmp'
    tmp.write_text(json.dumps(edits, indent=1), encoding='utf-8')
    tmp.replace(out)
    return out, moved


# ---- live link
CMD_TYPES = {'cue', 'cue_remove', 'cues_clear', 'cues_list', 'waypoint', 'waypoint_remove', 'waypoints_clear', 'waypoints_list', 'waypoint_go', 'sky', 'scene_go', 'scene_list', 'actor_follow', 'trees_reload', 'clock', 'key', 'key_delete', 'anim_save', 'anim_clear', 'timeline', 'growth', 'music', 'take_start', 'take_stop', 'eyecam', 'voice_rec', 'say', 'goto', 'goto_camera', 'focus', 'select', 'deselect', 'highlight', 'marker', 'clear_markers', 'set',
             'light', 'walk', 'look_through', 'snapshot', 'reload', 'undo', 'ask', 'panel', 'panel_close',
             'ack', 'gallery_add', 'drop', 'take_view', 'take_view_clear', 'actor_play', 'actor_stop', 'stream', 'anchor', 'anchor_release',
             'take_keep_last', 'follow_anchor', 'actor_rest', 'music_time', 'load_set', 'load_sets', 'actor_pose', 'perform', 'batch', 'control_set', 'control_map', 'key_interp',
             'behaviours', 'behaviour_run', 'behaviour_state'}
COND = threading.Condition()
LIVE = {}                      # scene -> {'state', 'state_t', 'events': [...], 'ev_id', 'cmds': [...], 'cmd_id'}


def _last_id(f):
    if not f.is_file():
        return 0
    last = 0
    with open(f, 'rb') as fh:
        fh.seek(max(0, f.stat().st_size - 65536))
        for line in fh.read().splitlines():
            try:
                last = max(last, int(json.loads(line)['id']))
            except (ValueError, KeyError, TypeError):
                pass
    return last


def live(name):
    """The live record of a scene (call with COND held). Ids continue from the files after a server restart."""
    if name not in LIVE:
        d = SCENES / name / 'live'
        events = []
        if (d / 'events.jsonl').is_file():
            for line in (d / 'events.jsonl').read_text(encoding='utf-8').splitlines()[-2000:]:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    pass
        LIVE[name] = {'state': None, 'state_t': 0.0, 'events': events,
                      'ev_id': _last_id(d / 'events.jsonl'), 'cmds': [], 'cmd_id': _last_id(d / 'cmds.jsonl')}
    return LIVE[name]


def server_event(name, ev):
    """An event the server itself adds to a scene's live log (a transcribed voice message)."""
    with COND:
        L = live(name)
        L['ev_id'] += 1
        e = {'id': L['ev_id'], 'ts': now_iso(), **ev}
        append_lines(live_dir(name) / 'events.jsonl', [e])
        L['events'].append(e)
        del L['events'][:-2000]
        COND.notify_all()
    presence.on_events(name, [e])
    return e


def server_cmd(name, cmd):
    """A command the server itself queues for a scene's page (presence: an ack, or "nobody is listening")."""
    with COND:
        L = live(name)
        L['cmd_id'] += 1
        c = {'id': L['cmd_id'], 'ts': now_iso(), **cmd}
        append_lines(live_dir(name) / 'cmds.jsonl', [c])
        L['cmds'].append(c)
        del L['cmds'][:-500]
        COND.notify_all()
    return c


SPEAK = 'http://127.0.0.1:8765'           # speech in and out: an OpenAI-style audio server (here speakwright)


def stt(audio, filename):
    import urllib.request
    b = '----stage' + str(int(time.time() * 1000))
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + audio + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request(SPEAK + '/v1/audio/transcriptions', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get('text', '').strip()


def stt_words(audio, filename):
    """{text, words: [{word, start, end}] or None} (None: the speech server predates verbose_json)."""
    import urllib.request
    b = '----stage' + str(int(time.time() * 1000))
    body = (f'--{b}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\nverbose_json\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + audio + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request(SPEAK + '/v1/audio/transcriptions', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    with urllib.request.urlopen(req, timeout=300) as r:
        got = json.loads(r.read())
    return {'text': (got.get('text') or '').strip(), 'words': got.get('words')}


def tts(text, voice=None):
    import urllib.request
    req = urllib.request.Request(SPEAK + '/v1/audio/speech', method='POST', headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'input': text, 'voice': voice or 'af_heart'}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


PHRASE_MAX = 160


def say_cached(text, voice=None):
    """Kokoro speech; a short line is kept in sounds/speech/ (index.json counts its uses) and replays instantly."""
    import hashlib
    voice = voice or 'af_heart'
    if len(text) > PHRASE_MAX:
        return tts(text, voice)
    PHRASES.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f'{voice}|{text}'.encode()).hexdigest()[:12]
    f, idx_f = PHRASES / f'{key}.wav', PHRASES / 'index.json'
    with COND:
        idx = json.loads(idx_f.read_text(encoding='utf-8')) if idx_f.is_file() else {}
    if f.is_file():
        wav = f.read_bytes()
    else:
        wav = tts(text, voice)
        f.write_bytes(wav)
    with COND:
        e = idx.setdefault(key, {'text': text, 'voice': voice, 'uses': 0})
        e['uses'] += 1
        e['last'] = now_iso()
        idx_f.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding='utf-8')
    return wav


def during_note(name, seconds, end):
    """What the user pointed at, selected, grabbed or framed while they talked (the user: Claude must see which table
    they meant when they said "this table right here"), as short lines with the second into the note."""
    keep = {'select', 'touch', 'grab', 'release', 'user_snapshot', 'teleport', 'transform_end'}
    start = end - float(seconds or 0) - 1.0
    out = []
    with COND:
        evs = list(live(name)['events'])
    for e in evs:
        if e.get('type') not in keep:
            continue
        try:
            t = time.mktime(time.strptime(e['ts'][:19], '%Y-%m-%dT%H:%M:%S'))
        except (KeyError, ValueError):
            continue
        if start <= t <= end + 1:
            what = e.get('item') or e.get('name') or e.get('object') or (e.get('to') and 'to ' + str(e.get('to'))) or e.get('path', '')
            out.append(f"{max(0, round(t - start - 1))}s {e['type']} {e.get('hand', '')} {what}".replace('  ', ' ').strip())
    return out[-30:]


def transcribe_message(name, f, rel, extra):
    t0 = time.time()
    try:
        text = stt(f.read_bytes(), f.name)
        ctx = during_note(name, extra.get('seconds'), t0)
        server_event(name, {'type': 'voice_message', 'file': rel, 'text': text, 'stt_ms': int((time.time() - t0) * 1000),
                            **extra, **({'during': ctx} if ctx else {})})
    except Exception as e:                                        # noqa: BLE001
        server_event(name, {'type': 'voice_message', 'file': rel, 'text': None, 'error': str(e), **extra})


def live_dir(name):
    d = SCENES / name / 'live'
    d.mkdir(exist_ok=True)
    return d


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='milliseconds')


DISK = {'warned': 0.0}


def _disk_failed(f, e):
    """A write that failed (a full disk): the live link carries on in memory; say so at most once a minute."""
    if time.time() - DISK['warned'] > 60:
        DISK['warned'] = time.time()
        print(f'[disk] cannot write {f} ({e}); the live link keeps going in memory', flush=True)


def write_atomic(f, text):
    tmp = f.with_suffix(f'{f.suffix}.{threading.get_ident()}.tmp')   # one per thread: two posts at once raced on one tmp
    try:
        tmp.write_text(text, encoding='utf-8')
    except OSError as e:
        return _disk_failed(f, e)
    for _ in range(5):             # Windows: a reader holding the file open makes replace fail for a moment
        try:
            tmp.replace(f)
            return
        except PermissionError:
            time.sleep(0.02)
        except OSError as e:
            return _disk_failed(f, e)


def append_lines(f, objs):
    try:
        with open(f, 'a', encoding='utf-8') as fh:
            for o in objs:
                fh.write(json.dumps(o, separators=(',', ':')) + '\n')
    except OSError as e:
        _disk_failed(f, e)


def live_engines():
    """The ismail live engines running on this machine (their own registry, one json per process): port -> record."""
    d = Path(os.environ.get('ISMAIL_LIVE_REGISTRY') or Path.home() / '.ismail' / 'live')
    out = {}
    for f in d.glob('*.json'):
        try:
            r = json.loads(f.read_text(encoding='utf8'))
            out[int(r['port'])] = r
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      '.js': 'text/javascript', '.mjs': 'text/javascript', '.json': 'application/json',
                      '.glb': 'model/gltf-binary', '.html': 'text/html', '.css': 'text/css'}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(PAGE), **kw)

    def translate_path(self, path):
        """The page from the package; scenes/<name>/... from the scenes folder (nothing above it)."""
        p = urlparse(path).path
        if p.startswith('/scenes/'):
            from urllib.parse import unquote
            rel = [x for x in unquote(p[len('/scenes/'):]).split('/') if x and x not in ('.', '..')]
            return str(SCENES.joinpath(*rel))
        return super().translate_path(path)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(n) or b'null', parse_constant=_not_finite)

    def _livestream(self, q):
        """Relay one ismail live stream to the page until either side leaves (stream.js plays it at an object)."""
        import http.client
        from urllib.parse import quote
        name = q.get('name', ['master'])[0]
        if not re.fullmatch(r'master|(bus|deck):[\w.-]{1,40}', name):
            return self._json(400, {'error': 'name: master, bus:<name> or deck:<name>'})
        eng = live_engines()
        port = q.get('port', [''])[0]
        port = int(port) if port.isdigit() else max(eng, key=lambda p: eng[p].get('started', 0), default=None)
        if port not in eng:
            return self._json(404, {'error': 'no ismail live engine running' if not eng else f'no engine on port {port}; running: {sorted(eng)}'})
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=None)
        try:
            c.request('GET', '/stream?name=' + quote(name, safe=':'))
            r = c.getresponse()
            if r.status != 200:
                return self._json(502, {'error': f'engine: {r.status} {r.read(300).decode("utf8", "replace")}'})
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            for h in ('X-Sample-Rate', 'X-Channels', 'X-Format'):
                self.send_header(h, r.getheader(h) or '')
            self.end_headers()
            print(f'[stream] {name} from :{port} -> {self.client_address[0]}', flush=True)
            # the engine sends nothing for a bus whose instruments rest (it skips dormant buses): fill those gaps with
            # silence on the wall clock, so the page's buffer never runs dry and Lucy, the bass and the drums stay
            # in step; silence goes in only between whole frames (4 bytes), never inside one
            import queue
            rate = int(r.getheader('X-Sample-Rate') or 44100) * 4
            q, t0, n, real = queue.Queue(), time.time(), 0, 0

            def pump():
                try:
                    while True:
                        b = r.read1(16384)
                        q.put(b)
                        if not b:
                            return
                except OSError:
                    q.put(b'')
            threading.Thread(target=pump, daemon=True).start()
            while True:
                try:
                    b = q.get(timeout=0.05)
                    if not b:
                        break
                    real += len(b)
                except queue.Empty:
                    behind = int((time.time() - t0 - 0.15) * rate) - n
                    if real % 4 or behind < 4 * 512:
                        continue
                    b = bytes(behind - behind % 4)
                self.wfile.write(b)
                self.wfile.flush()
                n += len(b)
            print(f'[stream] {name} ended ({n / rate:.0f} s, {real / rate:.0f} s from the engine)', flush=True)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as e:
            print(f'[stream] {name} closed: {type(e).__name__}', flush=True)
        finally:
            c.close()

    def _scene(self, q):
        """scene= from the query, else the scene whose page posted state last, else the only scene."""
        name = q.get('scene', [''])[0]
        if not name:
            with COND:
                live_ones = sorted(((v['state_t'], k) for k, v in LIVE.items() if v['state']), reverse=True)
            if live_ones:
                name = live_ones[0][1]
            elif len(scene_names()) == 1:
                name = scene_names()[0]
        if not NAME.match(name or '') or not (SCENES / name / 'scene.glb').is_file():
            raise ValueError(f'no scene {name!r} (pass scene=<name>; scenes: {scene_names()})')
        return name

    def _live_get(self, u):
        q = parse_qs(u.query)
        if u.path == '/live/presence':            # in VR or not, the last note, who is listening (presence.py)
            return self._json(200, presence.snapshot())
        if u.path == '/live/inbox':               # every scene's voice notes and headset events: a listener for all
            return self._live_inbox(q)
        if u.path == '/live':
            with COND:
                out = [{'scene': k, 'page': (v['state'] or {}).get('page'), 'mode': (v['state'] or {}).get('mode'),
                        'age_s': round(time.time() - v['state_t'], 2), 'last_event': v['ev_id'], 'last_cmd': v['cmd_id']}
                       for k, v in LIVE.items() if v['state']]
            return self._json(200, sorted(out, key=lambda x: x['age_s']))
        name = self._scene(q)
        if u.path == '/live/version':             # the page polls this to hot-reload after a re-export
            fs = [SCENES / name / 'scene.glb', SCENES / name / 'manifest.json']
            st = [f.stat() if f.is_file() else None for f in fs]
            cv = code_version()
            return self._json(200, {'scene': name, 'code': cv, 'code_name': time.strftime('v%m%d.%H%M', time.localtime(cv / 1e9)), **{k: [s.st_mtime_ns, s.st_size] if s else None
                                                      for k, s in zip(('glb', 'manifest'), st)},
                                    'listening': presence.listening(name), 'server': os.getpid()})
        since = int(q.get('since', ['-1'])[0])
        wait = min(float(q.get('wait', ['0'])[0]), 60.0)
        if wait > 0 and not self.server.longpoll_enter(self.client_address[0]):
            wait = 0.0                             # over the cap: answer now (the caller polls again)
        try:
            return self._live_get_answer(u, q, name, since, wait)
        finally:
            if wait > 0:
                self.server.longpoll_leave(self.client_address[0])

    def _live_get_answer(self, u, q, name, since, wait):
        if u.path == '/live/state':
            with COND:
                L = live(name)
                st, age = L['state'], round(time.time() - L['state_t'], 2) if L['state'] else None
            return self._json(200, {'scene': name, 'state': st, 'age_s': age})
        if u.path == '/live/events':
            limit = int(q.get('limit', ['500'])[0])
            who = q.get('who', [None])[0]
            if since >= 0:                         # following the log: a listener (presence.py), unless who=op
                presence.seen(who, name, delivered=since)
            with COND:
                L = live(name)
                if wait > 0 and since >= 0:
                    COND.wait_for(lambda: L['ev_id'] > since, timeout=wait)
                evs = [e for e in L['events'] if e.get('id', 0) > since] if since >= 0 else L['events'][-limit:]
                last = L['ev_id']
            if since >= 0:
                presence.seen(who, name, delivered=last)
            return self._json(200, {'scene': name, 'last': last, 'events': evs[-limit:]})
        if u.path == '/live/cmd':
            with COND:
                L = live(name)
                if wait > 0 and since >= 0:
                    COND.wait_for(lambda: L['cmd_id'] > since, timeout=wait)
                cmds = [c for c in L['cmds'] if c['id'] > since] if since >= 0 else []
                last = L['cmd_id']
            return self._json(200, {'scene': name, 'last': last, 'cmds': cmds})
        return self._json(404, {'error': 'not found'})

    def _live_inbox(self, q):
        who = q.get('who', [None])[0]
        since = int(q.get('since', ['-1'])[0])
        wait = min(float(q.get('wait', ['0'])[0]), 60.0)
        limit = int(q.get('limit', ['200'])[0])
        if since < 0:                              # where the inbox is now (no listening yet)
            return self._json(200, {'last': presence.SEQ[0], 'events': presence.inbox_after(presence.SEQ[0] - limit, limit)})
        if wait > 0 and not self.server.longpoll_enter(self.client_address[0]):
            wait = 0.0
        presence.seen(who, '*', inbox=since)
        try:
            with COND:
                if wait > 0:
                    COND.wait_for(lambda: presence.SEQ[0] > since, timeout=wait)
                evs, last = presence.inbox_after(since, limit), presence.SEQ[0]
        finally:
            if wait > 0:
                self.server.longpoll_leave(self.client_address[0])
        presence.seen(who, '*', inbox=last)
        return self._json(200, {'last': last, 'events': evs})

    def _live_post(self, u):
        name = self._scene(parse_qs(u.query))
        body = self._body()
        if u.path == '/live/state':
            if not isinstance(body, dict):
                raise ValueError('state must be an object')
            with COND:
                L = live(name)
                L['state'], L['state_t'] = body, time.time()
                COND.notify_all()
            presence.page_alive(name)
            write_atomic(live_dir(name) / 'state.json', json.dumps({'received': now_iso(), **body}, indent=1))
            return self._json(200, {'ok': True})
        if u.path == '/live/event':
            evs = body if isinstance(body, list) else [body]
            if not all(isinstance(e, dict) and isinstance(e.get('type'), str) for e in evs):
                raise ValueError('events need a "type"')
            with COND:
                L = live(name)
                out = []
                for e in evs:
                    L['ev_id'] += 1
                    out.append({'id': L['ev_id'], 'ts': now_iso(), **{k: v for k, v in e.items() if k not in ('id', 'ts')}})
                append_lines(live_dir(name) / 'events.jsonl', out)
                L['events'].extend(out)
                del L['events'][:-2000]
                COND.notify_all()
            presence.on_events(name, out)
            return self._json(200, {'ok': True, 'ids': [e['id'] for e in out]})
        if u.path == '/live/cmd':
            cmds = body if isinstance(body, list) else [body]
            for c in cmds:
                if not isinstance(c, dict):
                    raise ValueError('a command is a JSON object')
                if 'type' not in c:
                    c['type'] = c.pop('cmd', None)
                if c['type'] not in CMD_TYPES:
                    raise ValueError(f"unknown command {c['type']!r}; known: {sorted(CMD_TYPES)}")
                for x in (c.get('cmds') or []) if c['type'] == 'batch' else [c]:     # stage_batch: each one inside too
                    if not isinstance(x, dict) or x.get('type') not in CMD_TYPES or (x is not c and x.get('type') == 'batch'):
                        raise ValueError(f"unknown command {x.get('type') if isinstance(x, dict) else x!r} in a batch")
                    if x['type'] == 'marker' and 'id' in x:    # "id" is the command id below; keep the pin's own id
                        x.setdefault('marker_id', x.pop('id'))
            with COND:
                L = live(name)
                out = []
                for c in cmds:
                    L['cmd_id'] += 1
                    out.append({'id': L['cmd_id'], 'ts': now_iso(), **{k: v for k, v in c.items() if k not in ('id', 'ts')}})
                append_lines(live_dir(name) / 'cmds.jsonl', out)
                L['cmds'].extend(out)
                del L['cmds'][:-500]
                COND.notify_all()
            presence.on_cmds(name, out)
            return self._json(200, {'ok': True, 'scene': name, 'ids': [c['id'] for c in out]})
        return self._json(404, {'error': 'not found'})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/live' or u.path.startswith('/live/'):
            try:
                return self._live_get(u)
            except ValueError as e:
                return self._json(400, {'error': str(e)})
        if u.path == '/scenes':
            return self._json(200, scene_names())
        if u.path == '/health':                    # is the server well: workers, long-polls, threads, disk
            return self._json(200, self.server.health())
        if u.path == '/stage':                     # what this server serves: the default scene first
            from .world import default_scene
            return self._json(200, {'scenes': scene_names(), 'default': default_scene(SCENES), 'code': code_version()})
        if u.path == '/actor/profile':             # an actor's profile: its file beside its body over what the body says
            from .world import load_world
            from . import rigs
            qs = parse_qs(u.query)
            name, who = qs.get('scene', [''])[0], qs.get('who', [''])[0]
            if not NAME.match(name) or not NAME.match(who) or not (SCENES / name).is_dir():
                return self._json(400, {'error': 'bad scene or actor'})
            src = load_world(SCENES, name).get('assets') or name
            try:
                return self._json(200, rigs.profile(SCENES / src / 'actors', who))
            except FileNotFoundError:
                return self._json(404, {'error': f'no actor body {src}/actors/{who}.glb'})
            except ValueError as e:
                return self._json(400, {'error': str(e)})
        if u.path == '/world':                     # a scene's world.json with defaults (who plays whom, facings, ...)
            from .world import load_world
            name = parse_qs(u.query).get('scene', [''])[0]
            if not NAME.match(name) or not (SCENES / name).is_dir():
                return self._json(400, {'error': f'no scene {name!r}'})
            return self._json(200, load_world(SCENES, name))
        if u.path == '/updates.json':              # the update notes (update_note), newest code changes first
            f = STATE / 'updates.json'
            return self._json(200, json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {'updates': []})
        if u.path == '/livestream':
            return self._livestream(parse_qs(u.query))
        if u.path in ('/waypoints', '/cues'):     # the scene's pins (waypoints.js) or cues (cues.js), [] when none
            name = parse_qs(u.query).get('scene', [''])[0]
            f = SCENES / name / (u.path[1:] + '.json')
            return self._json(200, json.loads(f.read_text(encoding='utf-8')) if NAME.match(name) and f.is_file() else [])
        if u.path == '/behaviour_state':           # what each thing with a behaviour is now (behaviours.js), {} when none
            name = parse_qs(u.query).get('scene', [''])[0]
            f = SCENES / name / 'behaviour_state.json'
            return self._json(200, json.loads(f.read_text(encoding='utf-8')) if NAME.match(name) and f.is_file() else {})
        if u.path == '/bundle.js':
            try:
                f = bundle()
            except Exception as e:                   # node missing or hung: the page loads its modules one by one
                print('[bundle] failed:', e, flush=True)
                f = None
            if not f:
                return self._json(404, {'error': 'no bundle'})
            data = f.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if u.path == '/edits':                     # the saved edits.json, or null (no 404 noise in the console)
            name = parse_qs(u.query).get('scene', [''])[0]
            f = SCENES / name / 'edits.json'
            return self._json(200, json.loads(f.read_text(encoding='utf-8')) if NAME.match(name) and f.is_file() else None)
        if u.path == '/voice/say':                 # Kokoro speech for the headset
            q = parse_qs(u.query)
            text = q.get('text', [''])[0].strip()
            if not text:
                return self._json(400, {'error': 'no text'})
            try:
                wav = say_cached(text[:3000], q.get('voice', [None])[0])
            except Exception as e:                                # noqa: BLE001
                return self._json(502, {'error': f'speakwright: {e}'})
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(wav)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(wav)
            return None
        if u.path == '/snapshots':                 # the user's shots for the gallery: urls, oldest first
            q = parse_qs(u.query)
            name, tag = q.get('scene', [''])[0], q.get('tag', ['shot'])[0]
            d = SCENES / name / 'snapshots'
            if not NAME.match(name) or not NAME.match(tag) or not d.is_dir():
                return self._json(200, [])
            return self._json(200, [f'scenes/{name}/snapshots/{f.name}' for f in sorted(d.glob(f'{tag}_*.png'))])
        if u.path == '/takes':                     # recorded takes: [{id, name, frames, seconds, started}]
            name = parse_qs(u.query).get('scene', [''])[0]
            d = SCENES / name / 'takes'
            out = []
            if NAME.match(name) and d.is_dir():
                for t in sorted(d.iterdir()):
                    mf = t / 'meta.json'
                    m = json.loads(mf.read_text(encoding='utf-8')) if mf.exists() else {}
                    out.append({k: m.get(k) for k in ('id', 'name', 'frames', 'seconds', 'started', 'ended')} | {'id': t.name})
            return self._json(200, out)
        if u.path == '/':
            self.path = '/index.html' + (('?' + u.query) if u.query else '')
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == '/upload/file':               # footage from the headset (upload.html): streamed to the song's footage folder
            q = parse_qs(u.query)
            raw = Path(q.get('name', ['clip.mp4'])[0]).name
            safe = re.sub(r'[^A-Za-z0-9._\-]+', '_', raw).strip('._') or 'clip.mp4'
            FOOTAGE.mkdir(parents=True, exist_ok=True)
            out = FOOTAGE / safe
            k = 1
            while out.exists():
                out = FOOTAGE / f'{Path(safe).stem}_{k}{Path(safe).suffix}'
                k += 1
            n = int(self.headers.get('Content-Length', 0))
            part = out.with_suffix(out.suffix + '.part')
            got = 0
            with open(part, 'wb') as fh:
                while got < n:
                    chunk = self.rfile.read(min(4 << 20, n - got))
                    if not chunk:
                        break
                    fh.write(chunk)
                    got += len(chunk)
            if got != n:
                return self._json(400, {'error': f'connection dropped at {got} of {n} bytes', 'partial': str(part)})
            part.replace(out)
            mt = q.get('mtime', [''])[0]
            if mt.isdigit():
                os.utime(out, (int(mt) / 1000, int(mt) / 1000))
            print('FOOTAGE', out, round(n / 1e6, 1), 'MB', flush=True)
            return self._json(200, {'saved': out.name, 'bytes': n})
        if u.path.startswith('/live/'):
            try:
                return self._live_post(u)
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})
        if u.path == '/snapshot':
            q = parse_qs(u.query)
            name, tag = q.get('scene', [''])[0], q.get('tag', ['view'])[0]
            if not NAME.match(name) or not NAME.match(tag) or not (SCENES / name).is_dir():
                return self._json(400, {'error': 'bad scene or tag'})
            d = SCENES / name / 'snapshots'
            d.mkdir(exist_ok=True)
            f = d / f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.png"
            f.write_bytes(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            return self._json(200, {'path': str(f)})
        if u.path == '/voice/in':                 # audio from the headset mic
            qs = parse_qs(u.query)
            name, kind, take = qs.get('scene', [''])[0], qs.get('kind', ['message'])[0], qs.get('take', [''])[0]
            ext = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/wav': 'wav'}.get(
                (self.headers.get('Content-Type') or '').split(';')[0].strip(), 'webm')
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file() or kind not in ('message', 'take'):
                return self._json(400, {'error': 'bad scene or kind'})
            audio = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            extra = {k: qs[k][0] for k in ('seconds', 'via', 'page', 'snap') if k in qs}
            if 'snaps' in qs:
                extra['snaps'] = qs['snaps'][0].split('|')
            if kind == 'take':
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', take):
                    return self._json(400, {'error': 'bad take'})
                d = SCENES / name / 'takes' / take
                d.mkdir(parents=True, exist_ok=True)
                f = d / f'audio.{ext}'
                f.write_bytes(audio)
                server_event(name, {'type': 'take_audio', 'take': take, 'file': str(f.relative_to(SCENES / name)), 'bytes': len(audio), **extra})
                # what the person said during the take, kept with it (takes.py): take_voice when it is transcribed
                threading.Thread(target=takes.transcribe_and_tell, daemon=True,
                                 args=(name, d, stt_words, lambda ev: server_event(name, ev))).start()
                return self._json(200, {'ok': True, 'file': str(f)})
            d = SCENES / name / 'voice'
            d.mkdir(exist_ok=True)
            f = d / f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.{ext}"
            f.write_bytes(audio)
            rel = str(f.relative_to(SCENES / name))
            # the moment the audio lands, before transcription (2-40 s): an agent's "got it" keys on this
            server_event(name, {'type': 'voice_in', 'file': rel, 'bytes': len(audio), **{k: v for k, v in extra.items() if k in ('seconds', 'via')}})
            threading.Thread(target=transcribe_message, args=(name, f, rel, extra), daemon=True).start()
            return self._json(200, {'ok': True, 'file': str(f)})
        if u.path in ('/voice/perf', '/perf/meta'):               # a performance (perform.js / perform.py)
            qs = parse_qs(u.query)
            name, perf_id = qs.get('scene', [''])[0], qs.get('perf', [''])[0]
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file() or not perform.ID.match(perf_id):
                return self._json(400, {'error': 'bad scene or perf'})
            d = perform.folder(SCENES, name, perf_id)
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if u.path == '/perf/meta':
                try:
                    patch = json.loads(body or b'{}')
                except ValueError:
                    return self._json(400, {'error': 'bad json'})
                if not isinstance(patch, dict):
                    return self._json(400, {'error': 'meta must be an object'})
                perform.merge(d, patch)
                return self._json(200, {'ok': True})
            try:
                n = int(qs.get('clip', [''])[0])
            except ValueError:
                return self._json(400, {'error': 'bad clip'})
            if 'end' in qs:
                at, secs = float(qs.get('at', ['0'])[0]), float(qs.get('seconds', ['0'])[0])
                by = qs.get('by', ['page'])[0][:40]
                server_event(name, {'type': 'perform_clip_in', 'perf': perf_id, 'clip': n, 'at': at, 'seconds': secs, 'by': by})
                files = sorted(d.glob(f'clip_{n}.*'))
                quiet = 'quiet' in qs                              # the page heard nothing in it
                perform.set_clip(d, {'n': n, 'at': at, 'seconds': secs, 'by': by, 'file': files[0].name if files else None,
                                     'state': 'quiet' if quiet else 'transcribing'})

                def heard(ev, name=name, perf_id=perf_id):
                    server_event(name, ev)
                    said = perform.asks_stop(ev.get('text'))
                    if said:                                       # "stop the performance": always a way out
                        server_cmd(name, {'type': 'perform', 'action': 'stop', 'by': f'voice: "{said}"', 'perf': perf_id})
                stt = (lambda audio, fn: {'text': '', 'words': []}) if quiet else stt_words
                threading.Thread(target=perform.transcribe_clip, daemon=True, args=(d, n, at, secs, by, stt, heard)).start()
                return self._json(200, {'ok': True})
            ext = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/wav': 'wav'}.get(
                (self.headers.get('Content-Type') or '').split(';')[0].strip(), 'webm')
            d.mkdir(parents=True, exist_ok=True)
            with open(d / f'clip_{n}.{ext}', 'wb' if qs.get('seq', ['0'])[0] == '0' else 'ab') as fh:
                fh.write(body)
            return self._json(200, {'ok': True, 'bytes': len(body)})
        if u.path in ('/take/frames', '/take/meta'):                # a performance take from the page (hands.js)
            qs = parse_qs(u.query)
            name, take = qs.get('scene', [''])[0], qs.get('take', [''])[0]
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file() or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', take):
                return self._json(400, {'error': 'bad scene or take'})
            d = SCENES / name / 'takes' / take
            d.mkdir(parents=True, exist_ok=True)
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'null')
            if u.path == '/take/frames':
                if not isinstance(body, list):
                    return self._json(400, {'error': 'frames must be a list'})
                with open(d / 'frames.jsonl', 'a', encoding='utf-8') as f:
                    for fr in body:
                        f.write(json.dumps(fr, separators=(',', ':')) + chr(10))
                return self._json(200, {'ok': True, 'n': len(body)})
            mf = d / 'meta.json'
            meta = json.loads(mf.read_text(encoding='utf-8')) if mf.exists() else {}
            meta.update(body if isinstance(body, dict) else {})
            mf.write_text(json.dumps(meta, indent=1), encoding='utf-8')
            return self._json(200, {'ok': True})
        if u.path == '/behaviour_state':           # the page saves it as it changes: state, not authored, so no history
            name = parse_qs(u.query).get('scene', [''])[0]
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file():
                return self._json(400, {'error': 'bad scene'})
            try:
                body = self._body()
                if not isinstance(body, dict) or not all(isinstance(v, dict) for v in body.values()):
                    raise ValueError('behaviour_state must be {object: {key: value}}')
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})
            out = SCENES / name / 'behaviour_state.json'
            tmp = out.with_suffix('.tmp')
            tmp.write_text(json.dumps(body, indent=1), encoding='utf-8')
            tmp.replace(out)
            return self._json(200, {'ok': True, 'objects': len(body)})
        if u.path in ('/waypoints', '/cues'):     # the whole list; the previous one kept in history/
            name = parse_qs(u.query).get('scene', [''])[0]
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file():
                return self._json(400, {'error': 'bad scene'})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'[]')
                if not isinstance(body, list):
                    raise ValueError(u.path[1:] + ' must be a list')
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})
            out = SCENES / name / (u.path[1:] + '.json')
            if out.exists():
                (SCENES / name / 'history').mkdir(exist_ok=True)
                shutil.copy(out, SCENES / name / 'history' / time.strftime(u.path[1:] + '_%Y%m%d_%H%M%S.json'))
            out.write_text(json.dumps(body, indent=1), encoding='utf-8')
            return self._json(200, {'ok': True, 'n': len(body)})
        if u.path == '/clientlog':                 # the page's console errors and boot steps (index.html), headset included
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {'error': 'bad json'})
            who = f"{self.client_address[0]} {str(body.get('ua', ''))[:60]}"
            ents = [e for e in body.get('entries', []) if isinstance(e, dict)][:200]
            stamp = time.strftime('%H:%M:%S')
            append_lines(STATE / 'clientlog.jsonl', [{'at': stamp, 'who': who, 'page': body.get('page'), **e} for e in ents])
            for e in ents:
                if e.get('level') == 'beat':
                    continue
                print(f"[client {stamp} {self.client_address[0]}] {e.get('level')}: {str(e.get('msg'))[:400]}", flush=True)
            name = str(body.get('scene') or '')
            errs = [e for e in ents if e.get('level') == 'error']
            if errs and NAME.match(name) and (SCENES / name / 'scene.glb').is_file():
                server_event(name, {'type': 'page_error', 'errors': [str(e.get('msg'))[:400] for e in errs[:10]], 'who': who})
            return self._json(200, {'ok': True})
        if u.path == '/anim':                      # the scene's keyframes and growth spans (clock.js), the old one kept
            name = parse_qs(u.query).get('scene', [''])[0]
            if not NAME.match(name) or not (SCENES / name / 'scene.glb').is_file():
                return self._json(400, {'error': 'bad scene'})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                if not isinstance(body, dict) or not isinstance(body.get('objects', {}), dict):
                    raise ValueError('anim must be {span, objects, growth}')
            except (ValueError, json.JSONDecodeError) as e:
                return self._json(400, {'error': str(e)})
            out = SCENES / name / 'anim.json'
            if out.exists():
                (SCENES / name / 'history').mkdir(exist_ok=True)
                out.replace(SCENES / name / 'history' / time.strftime('anim_%Y%m%d_%H%M%S.json'))
            out.write_text(json.dumps(body, indent=1), encoding='utf-8')
            server_event(name, {'type': 'anim_saved', 'objects': len(body.get('objects', {}))})
            return self._json(200, {'ok': True, 'path': str(out)})
        if u.path != '/save':
            return self._json(404, {'error': 'not found'})
        name = parse_qs(u.query).get('scene', [''])[0]
        try:
            n = int(self.headers.get('Content-Length', 0))
            edits = json.loads(self.rfile.read(n) or b'{}')
            out, moved = save_edits(name, edits)
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {'error': str(e)})
        print('SAVED', out, '(previous moved to %s)' % moved if moved else '', flush=True)
        if stale():
            print('WARNING: server.py changed since this server started; restart it', flush=True)
        self._json(200, {'ok': True, 'path': str(out), 'previous': str(moved) if moved else None,
                         'counts': {k: len(v) for k, v in edits.items()}, 'server_stale': stale()})

    def log_message(self, fmt, *args):
        a0 = str(args[0]) if args else ''
        if args and ('POST' in a0 and '/live/' not in a0 or a0.startswith('GET') and '/live/' not in a0
                     and '/snapshots' not in a0):   # every page file: a stalled headset load shows which file stopped   # page loads: to see whether a stuck headset load ever arrived
            super().log_message(fmt, *args)


class Server(ThreadingHTTPServer):
    """A fixed pool of worker threads (a thread per request leaked: Python 3.11 keeps every finished request thread
    of a non-daemon ThreadingHTTPServer, and the page polls 2.5 times a second; the old server died of a MemoryError
    after about 40,500 threads in six hours). Long-polls are capped per client so they cannot hold every worker."""
    allow_reuse_address = os.name != 'nt'          # Windows: SO_REUSEADDR lets a second server bind the same port
    daemon_threads = True
    workers = 32
    longpoll_per_client = 8                         # everything local (agents, and the headset through
                                                    # tailscale serve) arrives as 127.0.0.1

    def __init__(self, *a, **kw):
        from concurrent.futures import ThreadPoolExecutor
        self.pool = ThreadPoolExecutor(self.workers, thread_name_prefix='stage')   # before the bind: a busy port
        self.stats_lock = threading.Lock()                                          # calls server_close at once
        self.busy = self.served = 0
        self.polls = {}
        self.t0 = time.time()
        super().__init__(*a, **kw)

    def process_request(self, request, client_address):
        self.pool.submit(self._work, request, client_address)

    def _work(self, request, client_address):
        with self.stats_lock:
            self.busy += 1
        try:
            self.finish_request(request, client_address)
        except Exception:                          # noqa: BLE001  (as socketserver does: log it, keep serving)
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            with self.stats_lock:
                self.busy -= 1
                self.served += 1

    def longpoll_enter(self, who):
        with self.stats_lock:
            if self.polls.get(who, 0) >= self.longpoll_per_client or sum(self.polls.values()) >= self.workers // 2:
                return False
            self.polls[who] = self.polls.get(who, 0) + 1
            return True

    def longpoll_leave(self, who):
        with self.stats_lock:
            self.polls[who] = max(0, self.polls.get(who, 0) - 1)

    def health(self):
        with self.stats_lock:
            busy, served, polls = self.busy, self.served, sum(self.polls.values())
        try:
            free = round(shutil.disk_usage(STATE).free / 1e6)
        except OSError:
            free = None
        return {'ok': free is None or free > 200, 'pid': os.getpid(), 'uptime_s': round(time.time() - self.t0),
                'workers': self.workers, 'busy': busy, 'served': served, 'long_polls': polls,
                'threads': threading.active_count(), 'scenes': len(scene_names()), 'state_free_mb': free,
                'disk_warned_s_ago': round(time.time() - DISK['warned']) if DISK['warned'] else None}

    def server_close(self):
        super().server_close()
        self.pool.shutdown(wait=False, cancel_futures=True)


def register(port, scheme, host):
    d = registry_dir()
    d.mkdir(parents=True, exist_ok=True)
    rec = {'pid': os.getpid(), 'port': port, 'host': host, 'scheme': scheme, 'scenes': str(SCENES), 'state': str(STATE),
           'started': time.time(), 'url': f'{scheme}://127.0.0.1:{port}/'}
    (d / f'{port}.json').write_text(json.dumps(rec, indent=1), encoding='utf-8')
    return d / f'{port}.json'


def main(argv=None):
    ap = argparse.ArgumentParser(description='The ismail stage server (VR scene editor and live link).')
    ap.add_argument('--scenes', required=True, help="the song's scenes folder (one subfolder per scene, each with scene.glb)")
    ap.add_argument('--port', type=int)
    ap.add_argument('--host')
    ap.add_argument('--footage', help='where headset recordings from upload.html go (default <scenes>/_stage/footage)')
    ap.add_argument('--tls', action='store_true', help='https on the LAN (needs --cert and --key)')
    ap.add_argument('--cert')
    ap.add_argument('--key')
    a = ap.parse_args(argv)
    configure(a.scenes, a.footage)
    port = a.port or (8863 if a.tls else 8862)
    host = a.host or ('0.0.0.0' if a.tls else '127.0.0.1')
    try:
        srv = Server((host, port), Handler)
    except OSError as e:
        raise SystemExit(f'port {port} is busy ({e}); another stage server may be running: stage_status, or pick --port')
    scheme = 'http'
    if a.tls:
        if not (a.cert and a.key and Path(a.cert).is_file() and Path(a.key).is_file()):
            raise SystemExit('--tls needs --cert and --key (a self-signed pair, e.g. from the openssl command line)')
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = 'https'
    reg = register(port, scheme, host)
    presence.URL[0] = f'{scheme}://127.0.0.1:{port}'
    print(f'ismail stage on {scheme}://{host}:{port}/  scenes {SCENES}: {scene_names()}', flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            reg.unlink()
        except OSError:
            pass


if __name__ == '__main__':
    main()
