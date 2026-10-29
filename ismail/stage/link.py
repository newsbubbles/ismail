"""The agent's side of the live link: find the stage server that serves a scene, queue a command for its page, wait
for the page's answer, read events. Plain HTTP to 127.0.0.1 (the server's registry, ~/.ismail/stage/<port>.json)."""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .. import settings

PAGE_FRESH_S = 120         # a page that posted its state within this long is "showing the scene" (a hidden
                           # tab or a lifted headset posts about once a minute; the command waits in its queue)


def _op_error(msg):
    # ismail.api imports the stage ops, which import this module: a top-level import of api here is a cycle
    # whenever link is the first ismail module loaded (python -m ismail.stage.ear)
    from ..api import OpError
    return OpError(msg)


def registry_dir():
    return Path(settings.get('stage.registry'))


def _pid_alive(pid):
    from ..live.ops import _pid_alive as alive
    return alive(pid)


def servers():
    """Running stage servers, newest first (records of dead ones are removed)."""
    out = []
    d = registry_dir()
    for f in d.glob('*.json') if d.is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not _pid_alive(rec.get('pid', -1)):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        out.append(rec)
    return sorted(out, key=lambda r: -r.get('started', 0))


def server_for(scene=None, port=None):
    """The server for a port, or the one whose scenes folder holds `scene`, or the only/newest one."""
    srv = servers()
    if not srv:
        raise _op_error('no stage server is running; start one with stage_start(scenes="<song>/video/vr/scenes")')
    if port:
        for r in srv:
            if int(r['port']) == int(port):
                return r
        raise _op_error(f'no stage server on port {port}; running: {[r["port"] for r in srv]}')
    if scene:
        # a copy under a hidden folder (a test server's .scratch) never wins over the real scene: the newest first
        # put every agent's ops for a scene on a test copy (2026-10-10)
        hidden = lambda r: any(part.startswith('.') and part not in ('.', '..') for part in Path(r['scenes']).parts)
        for r in sorted(srv, key=hidden):
            if (Path(r['scenes']) / scene / 'scene.glb').is_file():
                return r
        raise _op_error(f'no running stage server has a scene {scene!r}; servers: '
                      + '; '.join(f'{r["port"]}: {r["scenes"]}' for r in srv) + ' (stage_scenes lists them)')
    return srv[0]


def agent_headers():
    """An agent's credentials for the stage (pairing.py): the token the server keeps beside the registry, readable by
    this user only, and the agent's name (its session name, ISMAIL_SESSION, else "agent"). Empty when no server has made the token yet."""
    try:
        t = (registry_dir() / 'agent_token').read_text(encoding='utf-8').strip()
    except OSError:
        return {}
    return {'X-Stage-Agent': t, 'X-Stage-Who': os.environ.get('ISMAIL_SESSION') or 'agent'} if t else {}


def http(rec, path, body=None, timeout=30):
    url = f'http://127.0.0.1:{rec["port"]}/{path.lstrip("/")}'
    try:                                                   # NaN or Infinity is no JSON the page can read: it would stall its queue
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
    except ValueError:
        raise _op_error(f'a number in this command is not finite (NaN or Infinity): {str(body)[:200]}') from None
    req = urllib.request.Request(url, data=data, headers={**({'Content-Type': 'application/json'} if data else {}), **agent_headers()},
                                 method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b'null')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error')
        except (ValueError, AttributeError):
            msg = str(e)
        raise _op_error(f'stage server: {msg}')
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise _op_error(f'the stage server on port {rec["port"]} does not answer ({e}); stage_status, or stage_start again')


def q(**kw):
    return urllib.parse.urlencode({k: v for k, v in kw.items() if v is not None})


def page_state(rec, scene):
    return http(rec, f'live/state?{q(scene=scene)}', timeout=10)


# stage_batch: while a batch collects, page commands are gathered (one page command 'batch' sends them together)
_BATCH = threading.local()
QUEUED = '(queued in the batch)'


def collecting(scene):
    _BATCH.scene, _BATCH.cmds, _BATCH.timeout = scene, [], 0.0


def collected():
    """The commands gathered since collecting() (or the last collected()), and the time they may take."""
    cmds, t = getattr(_BATCH, 'cmds', None) or [], getattr(_BATCH, 'timeout', 0.0)
    if getattr(_BATCH, 'cmds', None) is not None:
        _BATCH.cmds, _BATCH.timeout = [], 0.0
    return cmds, t


def done_collecting():
    _BATCH.cmds = None


def pending():
    """How many page commands the batch holds now (None: no batch is collecting)."""
    cmds = getattr(_BATCH, 'cmds', None)
    return None if cmds is None else len(cmds)


# the fields of each page command the person reads in the headset or on the desktop page: scanned for private terms
SHOWN = {'say': ('text',), 'ask': ('text',), 'panel': ('title', 'text', 'buttons'), 'cue': ('title', 'text', 'buttons'),
         'marker': ('label',)}


def page_cmd(scene, ctype, fields, timeout=30):
    """Queue one command for the page showing `scene`; wait for its cmd_done; reply with the page's answer. A command
    that shows text (SHOWN) is refused when the text names a private term (ismail.guards)."""
    note = ''
    if ctype in SHOWN:
        from .. import guards
        note = guards.check_private(*[fields.get(f) for f in SHOWN[ctype]], where='the stage')
    if getattr(_BATCH, 'cmds', None) is not None:
        if scene != _BATCH.scene:
            raise _op_error(f'a batch drives one scene ({_BATCH.scene!r}); {ctype} asked for {scene!r}')
        if ctype == 'scene_go':
            raise _op_error('stage_scene_go cannot be inside a batch (the page leaves the scene the batch drives); send it on its own')
        _BATCH.cmds.append({'type': ctype, **{k: v for k, v in fields.items() if v is not None}})
        _BATCH.timeout += timeout
        return QUEUED
    e = send(scene, ctype, fields, timeout)
    out = e if isinstance(e, str) else reply(ctype, e)
    if note and isinstance(out, str):
        out += '\n' + note
    return out


def send(scene, ctype, fields, timeout=30):
    """page_cmd without the formatting: the page's cmd_done event (or, for a scene switch, a line saying so)."""
    rec = server_for(scene)
    st = page_state(rec, scene)
    age = st.get('age_s')                                  # 0.0 is a page seen just now: never `age or default`
    if st.get('state') is None or age is None or age > PAGE_FRESH_S:
        raise _op_error(f'no page is showing scene {scene!r} (last seen {st.get("age_s")} s ago); open '
                      f'{rec["url"]}?scene={scene} in the headset or a browser, then try again')
    cmd = {'type': ctype, **{k: v for k, v in fields.items() if v is not None}}
    # a scene switch answers on the scene it switched to (the page listens there from then on)
    watch = [scene] + ([fields['name']] if ctype == 'scene_go' and fields.get('name') not in (None, scene) else [])
    since = {s: http(rec, f'live/events?{q(scene=s, limit=1)}')['last'] for s in watch if (Path(rec['scenes']) / s).is_dir()}
    ids = http(rec, f'live/cmd?{q(scene=scene)}', cmd)['ids']
    t_end = time.time() + timeout
    while time.time() < t_end:
        for s in since:
            got = http(rec, f'live/events?{q(scene=s, since=since[s], wait=min(10 if len(since) == 1 else 1, max(1, t_end - time.time())), limit=500, who="op")}',
                       timeout=20)
            since[s] = got['last']
            for e in got['events']:
                if e.get('type') == 'cmd_done' and e.get('cmd_id') in ids and s == scene:
                    return e
                if ctype == 'scene_go' and s != scene and e.get('type') == 'scene_switched':
                    return f'scene_go: switched to {s} ({e.get("objects")} objects, {e.get("ms")} ms); send further ops with scene="{s}"'
    raise _op_error(f'the page took no answer to {ctype} within {timeout:.0f} s (command {ids[0]}); a long command before '
                  f'it (a flight, a load) holds the queue: stage_events(scene="{scene}", types=["cmd_done"]) shows it')


def reply(ctype, e):
    if e.get('ok') is False or e.get('error'):
        raise _op_error(f'{ctype}: {e.get("error") or "the page refused it"}')
    res = e.get('result')
    txt = json.dumps(res, separators=(', ', ': '), default=str) if res is not None else 'done'
    # a handler that answers {error: ...} failed (no mic, already asking, a behaviour function that threw): until
    # 2026-10-12 that came back as done with the error buried in the text
    if isinstance(res, dict) and res.get('error'):
        rest = {k: v for k, v in res.items() if k != 'error'}
        raise _op_error(f'{ctype}: {res["error"]}' + (f' ({json.dumps(rest, default=str)[:1500]})' if rest else ''))
    if len(txt) > 2000:
        txt = txt[:2000] + ' ...'
    return f'{ctype} ({e.get("ms", "?")} ms): {txt}'
