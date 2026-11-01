"""The stream server: venues' chat adapters, the chat log, the output, and a small JSON API on 127.0.0.1.

  python -m ismail.stream.server [--port 8875]

Agents reach it through the stream_* / venue_* / chat_* ops (ops.py), which POST {op, args} to /agent."""
import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import adapters, config as C, overlay as OV, scene as SC
from .chatlog import ChatLog
from .levels import Levels

PAGE = Path(__file__).with_name('page')


class Hub:
    def __init__(self, port):
        self.port = port
        self.log = ChatLog(C.HOME / 'chat.jsonl')
        self.adapters = {}            # venue -> Adapter
        self.configs = {}             # venue -> the config its adapter started with
        self.watchers = {}            # who -> last time it listened
        self.lock = threading.RLock()
        self.started = time.time()
        self.output = None            # output.Output, made on the first stream_go or preview
        self.levels = Levels()        # what the visual page moves to, read only while something wants it
        self.scene_cond = threading.Condition()
        try:
            self.scene = SC.check_scene(json.loads((C.HOME / 'scene.json').read_text(encoding='utf8')))
        except (OSError, ValueError):
            self.scene = SC.check_scene(SC.DEFAULT)
        self.scene_v = 1
        self.page_view = None         # (width, height) in device pixels, as the page last reported its canvas
        self.callouts = OV.Callouts() # timed text over the page (overlay.py); gone with the server, like a moment
        self.panels = {}              # kind -> a panel bound to live data, kept in panels.json
        try:
            for kind, raw in json.loads((C.HOME / 'panels.json').read_text(encoding='utf8')).items():
                try:
                    self.panels[kind] = OV.check_panel(kind, raw)
                except ValueError:
                    continue          # a font file that moved: that panel waits for stream_panel again
        except (OSError, ValueError, AttributeError):
            pass
        self.overlay_cond = threading.Condition()
        self.overlay_v = 1
        self.page_drew = None         # {at, callouts, panels}: what an open page last said it drew
        self.reload()

    # ------------------------------------------------------------ venues
    def reload(self):
        venues = C.load_venues()
        with self.lock:
            for name in list(self.adapters):
                v = venues.get(name)
                if not v or not self._chat_on(v) or v != self.configs.get(name):
                    self.adapters.pop(name).stop()
                    self.configs.pop(name, None)
            for name, v in venues.items():
                if self._chat_on(v) and name not in self.adapters:
                    try:
                        a = adapters.make(v, self)
                    except ImportError as e:
                        self.log.add('status', name, platform=v['platform'], state='cannot start',
                                     text=f"{e}: pip install websockets (or ismail[stream])")
                        continue
                    self.adapters[name], self.configs[name] = a, dict(v)
                    a.start()
        return venues

    @staticmethod
    def _chat_on(v):
        return v.get('chat', True) and C.PLATFORMS.get(v.get('platform'), {}).get('chat')

    # ------------------------------------------------------------ ops
    def run(self, op, a):
        fn = getattr(self, 'op_' + op, None)
        if fn is None:
            raise ValueError(f'no such stream server op: {op}')
        return fn(**a)

    def op_health(self):
        return {'ok': True, 'pid': os.getpid(), 'port': self.port}

    def op_reload(self):
        self.reload()
        return self.op_status()

    def op_status(self):
        venues = C.load_venues()
        now = time.time()
        rows = []
        for name, v in sorted(venues.items()):
            a = self.adapters.get(name)
            rows.append({'venue': name, 'platform': v['platform'], 'channel': v.get('channel'),
                         'chat': a.status() if a else ('off' if C.PLATFORMS[v['platform']]['chat'] else 'none'),
                         'output': bool(v.get('output', C.PLATFORMS[v['platform']]['output'])),
                         'keys': C.key_summary(name, v['platform'])})
        return {'up_s': int(now - self.started), 'port': self.port, 'chat_lines': self.log.n,
                'watching': sorted(w for w, t in self.watchers.items() if now - t < 120),
                'venues': rows, 'output': self.output.status() if self.output else {'state': 'off'}}

    def op_listen(self, who, since=None, wait=25.0, venues=None, kinds=None, include_self=False, back_s=0):
        self.watchers[who] = time.time()
        if since is None:                   # a new watcher: from back_s ago, so it sees the room's opening
            cut = time.time() - float(back_s or 0)
            since = max([d['n'] for d in self.log.lines if d.get('at', 0) < cut], default=0) if back_s else self.log.n
        venues = set(venues) if venues else None
        kinds = set(kinds) if kinds else {'chat', 'event', 'status'}

        def pick(d):
            return (d['kind'] in kinds and (venues is None or d['venue'] in venues)
                    and (include_self or not d.get('own')))
        lines = self.log.wait(int(since), float(wait or 0), pick)
        self.watchers[who] = time.time()
        last = lines[-1]['n'] if lines else self.log.n
        return {'since': max(int(since), last if lines else self.log.n), 'lines': lines}

    def op_read(self, since=0, venue=None, user=None, kinds=None, limit=50):
        kinds = set(kinds) if kinds else {'chat', 'event', 'said'}
        u = (user or '').lower()

        def pick(d):
            nm = (d.get('user') or {}).get('name', '')
            return d['kind'] in kinds and (not venue or d['venue'] == venue) and (not u or nm == u)
        lines = self.log.after(int(since or 0), pick)
        return {'lines': lines[-int(limit):], 'more_before': max(0, len(lines) - int(limit)), 'last': self.log.n}

    def op_say(self, text, venue=None, reply_to=None, who=None):
        """One line to one venue, every venue (venue='all'), or as a reply to a chat line (reply_to=<its n>)."""
        target = None
        if reply_to is not None:
            target = next((d for d in self.log.lines if d['n'] == int(reply_to)), None)
            if target is None or target['kind'] not in ('chat', 'event'):
                raise ValueError(f'reply_to={reply_to}: no chat line with that n (chat_read shows the n of each)')
            venue = target['venue']
        if not venue:
            raise ValueError("say where: venue='<name>', venue='all', or reply_to=<a chat line's n>")
        names = [n for n, a in self.adapters.items() if a.state == 'reading'] if venue == 'all' else [venue]
        if not names:
            raise ValueError('no venue is reading chat right now (stream_status says why)')
        out = []
        for name in names:
            a = self.adapters.get(name)
            if a is None:
                out.append({'venue': name, 'ok': False, 'error': f'no chat adapter running for {name} '
                            f'(venue_list shows the venues; is chat on?)'})
                continue
            body, rid = text, None
            if target is not None:
                rid = target.get('id')
                who_to = (target.get('user') or {}).get('display') or (target.get('user') or {}).get('name')
                if who_to and not a.threaded_replies() and not str(text).lstrip().startswith('@'):
                    body = f'@{who_to} {text}'
            try:
                body, mid = a.send(body, reply_to=rid)
                d = self.log.add('said', name, platform=a.platform, text=body, id=mid, by=who,
                                 reply_to=target['n'] if target else None, ok=True)
                out.append({'venue': name, 'ok': True, 'n': d['n']})
            except Exception as e:
                self.log.add('said', name, platform=a.platform, text=body, by=who, ok=False, error=str(e))
                out.append({'venue': name, 'ok': False, 'error': str(e)})
        return out

    def op_viewer(self, text, user='viewer', venue=None):
        """A line typed on the fake venue's page (or by a test)."""
        fakes = [a for a in self.adapters.values() if a.platform == 'fake']
        a = next((x for x in fakes if x.name == venue), fakes[0] if fakes else None)
        if a is None:
            raise ValueError("no fake venue: venue_set(name='practice', platform='fake') first")
        a.viewer(user, text)
        return {'ok': True}

    # ------------------------------------------------------------ the visual page (scene.py, levels.py)
    def op_scene(self, layers=None, background=None, trails=None, replace=True):
        new = {'background': self.scene['background'], 'trails': self.scene['trails'],
               'layers': [] if replace else list(self.scene['layers'])}
        if background is not None:
            new['background'] = background
        if trails is not None:
            new['trails'] = trails
        if layers is not None:
            new['layers'] += list(layers)
        elif replace:
            new['layers'] = list(self.scene['layers'])
        checked = SC.check_scene(new)
        with self.scene_cond:
            self.scene, self.scene_v = checked, self.scene_v + 1
            self.scene_cond.notify_all()
        C.HOME.mkdir(parents=True, exist_ok=True)
        (C.HOME / 'scene.json').write_text(json.dumps(checked, indent=1), encoding='utf8')
        return {'v': self.scene_v, 'scene': checked}

    def op_page(self):
        self.levels.want(5.0)
        _, f = self.levels.wait(-1, 0.5)
        return {'url': f'http://127.0.0.1:{self.port}/visual', 'scene': self.scene, 'frame': f, 'drew': self.page_drew,
                'window': self.output.page_window() if self.output else None}

    # ------------------------------------------------------------ callouts and panels over the page (overlay.py)
    def _overlay_changed(self):
        with self.overlay_cond:
            self.overlay_v += 1
            self.overlay_cond.notify_all()

    def op_callout(self, text=None, clear=False, id=None, slot=None, who=None, **spec):
        """Add a callout, take some down (clear=True: all, or id=, or slot=), or with neither, say what shows."""
        now = OV.now()
        removed, added = [], None
        if not clear and text is None and (id is not None or spec):
            raise ValueError("stream_callout: text= to show one, clear=True (with id= or slot=) to take some down, "
                             "or nothing to see what shows")
        with self.overlay_cond:
            if clear:
                if slot is not None and id is None and not any(c['slot'] == slot for c in self.callouts.items):
                    raise ValueError(f"no callout in slot {slot!r} (in use: "
                                     f"{', '.join(sorted({c['slot'] for c in self.callouts.items})) or 'none'})")
                removed = self.callouts.clear(now, None if id is None else int(id), slot)
            if text is not None:
                added = self.callouts.add(OV.check_callout(text, slot=slot, **spec), now, who)
            sched = self.callouts.schedule(now)
        if removed or added:
            self._overlay_changed()
            C.HOME.mkdir(parents=True, exist_ok=True)
            with open(C.HOME / 'callouts.jsonl', 'a', encoding='utf8') as f:
                ev = ({'ev': 'add', 'id': added['id'], 'who': who, 'slot': added['slot'], 'text': added['text'],
                       'duration_s': added['duration_s']} if added else {'ev': 'clear', 'ids': removed, 'who': who})
                f.write(json.dumps({'at': round(now, 2), **ev}, ensure_ascii=False) + '\n')
        return {'t': now, 'added': added and added['id'], 'removed': removed,
                'callouts': [{k: c[k] for k in ('id', 'text', 'slot', 'state', 'start', 'end', 'priority', 'who')}
                             for c in sched]}

    def op_panel(self, kind=None, on=True, **fields):
        """Show, change (the fields given; the rest keep their value) or take down (on=False) one panel."""
        if kind is not None:
            if kind not in OV.PANELS:
                raise ValueError(f"panel kind {kind!r}: one of {', '.join(OV.PANELS)}")
            if not on:
                if self.panels.pop(kind, None) is None:
                    raise ValueError(f"no {kind} panel is up (up: {', '.join(self.panels) or 'none'})")
            else:
                raw = dict(self.panels[kind]['raw']) if kind in self.panels else {}
                if 'style' in fields and isinstance(fields['style'], dict) and isinstance(raw.get('style'), dict):
                    fields['style'] = {**raw['style'], **fields['style']}
                raw.update({k: v for k, v in fields.items() if v is not None})
                self.panels[kind] = OV.check_panel(kind, raw)
            C.HOME.mkdir(parents=True, exist_ok=True)
            (C.HOME / 'panels.json').write_text(json.dumps({k: p['raw'] for k, p in self.panels.items()}, indent=1),
                                                encoding='utf8')
            self._overlay_changed()
        elif fields or not on:
            raise ValueError(f"stream_panel: kind= one of {', '.join(OV.PANELS)}")
        return {'panels': {k: {**OV.page_panel(p), 'raw': p['raw'], 'shows': OV.panel_rows(p, self.log)}
                           for k, p in self.panels.items()}}

    def live(self):
        """What the page draws over its canvas: the callouts (with their times) and each panel with its rows now."""
        now = OV.now()
        with self.overlay_cond:
            sched = self.callouts.schedule(now)
        panels = []
        for p in list(self.panels.values()):
            try:
                panels.append({**OV.page_panel(p), 'shows': OV.panel_rows(p, self.log)})
            except Exception:     # a queue file mid-write: this panel skips one beat, the page carries on
                continue
        return {'v': self.overlay_v, 't': now, 'panels': panels,
                'callouts': [{k: c[k] for k in ('id', 'text', 'slot', 'state', 'start', 'end', 'place', 'style',
                                                'enter', 'exit', 'anim_s')} for c in sched]}

    # ------------------------------------------------------------ output (output.py)
    def _out(self):
        if self.output is None:
            from .output import Output
            self.output = Output(self)
        return self.output

    def op_go(self, venues, approved=None, video=None):
        return self._out().go(venues, approved, video)

    def op_end(self, venues=None):
        return self._out().end(venues) if self.output else {'state': 'off'}

    def op_visual(self, **params):
        return self._out().set_visual(params)

    def op_text(self, slot, text):
        return self._out().set_text(slot, text)

    def op_look(self):
        return self._out().look()

    def shutdown(self):
        for a in list(self.adapters.values()):
            a.stop()
        if self.output:
            self.output.end(None)


class Handler(BaseHTTPRequestHandler):
    hub = None

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return self.rfile.read(min(n, 1 << 20)) if n else b''

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/health':
            return self._json(200, self.hub.op_health())
        if u.path in ('/fake', '/fake/'):
            b = (PAGE / 'fake.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if u.path == '/fake/lines':
            since = int(q.get('since', ['0'])[0] or 0)
            lines = self.hub.log.wait(since, min(20.0, float(q.get('wait', ['0'])[0] or 0)),
                                      lambda d: d['kind'] in ('chat', 'event', 'said')
                                      and self.hub.configs.get(d['venue'], {}).get('platform') == 'fake')
            return self._json(200, {'lines': lines[-200:], 'last': self.hub.log.n})
        if u.path in ('/visual', '/visual/'):
            b = (PAGE / 'visual.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if u.path == '/visual/scene':
            v, wait = int(q.get('v', ['0'])[0] or 0), min(25.0, float(q.get('wait', ['0'])[0] or 0))
            end = time.time() + wait
            with self.hub.scene_cond:
                while self.hub.scene_v == v and time.time() < end:
                    self.hub.scene_cond.wait(min(5.0, max(0.01, end - time.time())))
                return self._json(200, {'v': self.hub.scene_v, 'scene': self.hub.scene})
        if u.path == '/visual/live':                      # callouts and panels, at once on a change, else each wait s
            v, wait = int(q.get('v', ['0'])[0] or 0), min(5.0, float(q.get('wait', ['0'])[0] or 0))
            if 'drew' in q:                               # what the page drew last frame, for stream_page
                self.hub.page_drew = {'at': time.time(),
                                      'callouts': [int(x) for x in q.get('c', [''])[0].split(',') if x.isdigit()],
                                      'panels': [x for x in q.get('p', [''])[0].split(',') if x in OV.PANELS]}
            with self.hub.overlay_cond:
                if self.hub.overlay_v == v:
                    self.hub.overlay_cond.wait(max(0.0, wait))
            try:
                return self._json(200, self.hub.live())
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return None                               # the page closed mid-poll
        if u.path.startswith('/visual/font/'):
            p = OV.FONT_FILES.get(u.path.rsplit('/', 1)[-1])
            if p is None or not p.is_file():
                return self._json(404, {'error': 'no such font: a style names its font file first'})
            b = p.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', OV.FONT_TYPES[p.suffix.lower()])
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            return self.wfile.write(b)
        if u.path == '/visual/size':                      # the page says how big it draws, so the capture can crop
            try:
                self.hub.page_view = (int(float(q['w'][0])), int(float(q['h'][0])))
            except (KeyError, ValueError):
                return self._json(400, {'error': 'w and h: the canvas size in device pixels'})
            return self._json(200, {'ok': True})
        if u.path == '/levels':
            return self._levels()
        if u.path == '/preview.jpg' and self.hub.output:
            p = self.hub.output.preview_path()
            if p and p.exists():
                b = p.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                return self.wfile.write(b)
        if u.path == '/pcm' and self.hub.output:
            return self.hub.output.serve_pcm(self)
        return self._json(404, {'error': 'no such page'})

    def _levels(self):
        """Server-sent events: one level frame per message, about 30 a second, while the page reads."""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        lv, seq = self.hub.levels, -1
        try:
            while True:
                lv.want(15.0)
                seq, f = lv.wait(seq, 1.0)
                self.wfile.write(b'data: ' + json.dumps(f).encode() + b'\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        try:
            req = json.loads(self._body() or b'{}')
            if u.path == '/agent':
                return self._json(200, {'ok': True, 'result': self.hub.run(req['op'], dict(req.get('args') or {}))})
            if u.path == '/fake/say':
                return self._json(200, self.hub.op_viewer(str(req.get('text', ''))[:500],
                                                          str(req.get('user') or 'viewer')[:25]))
        except (ValueError, TypeError, KeyError) as e:
            return self._json(400, {'ok': False, 'error': str(e)})
        except Exception as e:
            return self._json(500, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
        return self._json(404, {'error': 'no such page'})


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m ismail.stream.server')
    ap.add_argument('--port', type=int, default=C.PORT)
    a = ap.parse_args(argv)
    C.HOME.mkdir(parents=True, exist_ok=True)
    hub = Hub(a.port)
    Handler.hub = hub
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    srv.daemon_threads = True
    (C.HOME / 'server.json').write_text(json.dumps({'port': a.port, 'pid': os.getpid(), 'started': time.time()}),
                                        encoding='utf8')
    print(f'[stream] up on 127.0.0.1:{a.port}, home {C.HOME}', flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        hub.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
