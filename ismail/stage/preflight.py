"""stage_preflight: one op to run before a VR session or a demo. On its own scratch scene (_preflight, hidden from the
page's scene list) it loads a page, swaps the room file under it, changes the world and a load set live, shows a
line and sends a synthetic event round the listen inbox; it also reads (never writes) the Claude Code settings for
the allow rule and the named-ops guard. One line per check (PASS, BLOCKED or SKIPPED, the time, the next step for a
BLOCKED one), then the verdict, which the stage shows (and in VR, speaks)."""
import fnmatch
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
from pathlib import Path

from ..api import OpError, op
from . import link

SCENE = '_preflight'                 # a leading _ keeps it out of the page's scene list
MARK = 'PREFLIGHT.txt'               # in the scratch folder: preflight made it, so preflight may overwrite it
SET = 'preflight_set'
BOX = 'preflight_box'
FLOOR = 'preflight_floor'
PING = 'preflight_ping'              # the synthetic event (presence.INBOX_TYPES lets it reach stage_listen)
OP_CMD = 'python -m ismail stage_status'
REPO = Path(__file__).resolve().parents[2]
COLOR_A, COLOR_B = (0.2, 0.6, 0.9), (0.9, 0.5, 0.1)
REUSE_AGE_S = 5                      # a page that posted within this long is open now (a visible page beats every 2 s)
REASON = {'server': 'no stage server is running', 'allow_rule': 'no allow rule for ismail ops',
          'guard_hook': 'the guard hook is not set up', 'scratch': 'the scratch scene could not be made',
          'load': 'the test page did not load', 'hot_swap': 'the hot swap failed', 'live_insert': 'the live insert failed',
          'say': 'stage say failed', 'listen': 'the listen check failed'}


class Blocked(Exception):
    def __init__(self, detail, next_step=''):
        super().__init__(detail)
        self.detail, self.next = detail, next_step


class Skipped(Exception):
    pass


def _home():
    return Path.home()


def _short(p):
    """A path for the output: the home folder as ~ (it holds the user's name)."""
    s, h = str(p), str(_home())
    return '~' + s[len(h):].replace('\\', '/') if s.lower().startswith(h.lower()) else s.replace('\\', '/')


# ---- the scratch room: one box, built here (no Blender, no download) ------------------------------------------------

def _box(hx, hy, hz):
    """A box's 24 corners (4 a face, so each face has its own normal) and 36 indices, faces wound outwards."""
    half, pos, nrm, idx = (hx, hy, hz), [], [], []
    for a in range(3):
        u, v = (a + 1) % 3, (a + 2) % 3
        for s in (1, -1):
            corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
            if s < 0:
                corners.reverse()
            base = len(pos)
            for cu, cv in corners:
                p, n = [0.0] * 3, [0.0] * 3
                p[a], p[u], p[v], n[a] = s * half[a], cu * half[u], cv * half[v], float(s)
                pos.append(p)
                nrm.append(n)
            idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return pos, nrm, idx


# (node, half sizes in glTF axes, glTF translation, colour or None for the colour the call gives). The floor is
# "big" (over 4 m across), so the page reveals it without the small-thing pop and its selftest can check it at load.
PIECES = [(FLOOR, (3.0, 0.05, 3.0), [0.0, -0.05, 0.0], (0.55, 0.55, 0.58)),
          (BOX, (0.25, 0.25, 0.25), [0.0, 0.5, 0.0], None)]


def scratch_glb(color):
    """A binary glTF of the scratch room: a 6 m floor and a 0.5 m box standing on it, the box in `color`."""
    binary, views, accessors, nodes, meshes, materials = b'', [], [], [], [], []
    for i, (name, half, at, col) in enumerate(PIECES):
        pos, nrm, idx = _box(*half)
        for data, target, ctype, count, typ, extra in (
                (b''.join(struct.pack('<3f', *p) for p in pos), 34962, 5126, len(pos), 'VEC3',
                 {'min': [-x for x in half], 'max': list(half)}),
                (b''.join(struct.pack('<3f', *n) for n in nrm), 34962, 5126, len(nrm), 'VEC3', {}),
                (struct.pack(f'<{len(idx)}H', *idx), 34963, 5123, len(idx), 'SCALAR', {})):
            binary += b'\x00' * (-len(binary) % 4)
            views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(data), 'target': target})
            accessors.append({'bufferView': len(views) - 1, 'componentType': ctype, 'count': count, 'type': typ, **extra})
            binary += data
        a0 = 3 * i
        nodes.append({'name': name, 'mesh': i, 'translation': at})
        meshes.append({'name': name, 'primitives': [{'attributes': {'POSITION': a0, 'NORMAL': a0 + 1}, 'indices': a0 + 2,
                                                     'material': i}]})
        materials.append({'name': name, 'pbrMetallicRoughness': {'baseColorFactor': [*(col or color), 1.0],
                                                                 'metallicFactor': 0.0, 'roughnessFactor': 0.8}})
    binary += b'\x00' * (-len(binary) % 4)
    doc = {'asset': {'version': '2.0', 'generator': 'ismail stage_preflight'},
           'scene': 0, 'scenes': [{'nodes': list(range(len(nodes)))}], 'nodes': nodes, 'meshes': meshes,
           'materials': materials, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views, 'accessors': accessors}
    js = json.dumps(doc, separators=(',', ':')).encode()
    js += b' ' * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (b'glTF' + struct.pack('<II', 2, total) + struct.pack('<I', len(js)) + b'JSON' + js
            + struct.pack('<I', len(binary)) + b'BIN\x00' + binary)


# the pieces in Blender coordinates (z up): glTF (x, y, z) is Blender (x, -z, y); the page's selftest compares them
MANIFEST = {'objects': {name: {'type': 'MESH', 'location': [at[0], -at[2] + 0.0, at[1]], 'quaternion': [1.0, 0.0, 0.0, 0.0],
                               'scale': [1.0, 1.0, 1.0]} for name, _, at, _ in PIECES}, 'lights': {}, 'materials': {}}
WORLD = {'sets': {SET: {'items': [BOX], 'note': 'the preflight box'}}, 'unloaded': []}


def _put(f, data):
    """Write f only when it differs (an unchanged room file does not wake an open page's hot reload)."""
    if f.is_file() and f.read_bytes() == data:
        return False
    tmp = f.with_name(f.name + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(f)
    return True


def refresh_scratch(scenes):
    """Make or refresh scenes/_preflight from the built-in box. Refuses a _preflight folder preflight did not make."""
    d = Path(scenes) / SCENE
    if d.is_dir() and not (d / MARK).is_file() and any(d.iterdir()):
        raise Blocked(f'{_short(d)} holds files preflight did not make',
                      'that scene is not preflight\'s: move it to another name, then run stage_preflight again')
    d.mkdir(parents=True, exist_ok=True)
    (d / MARK).write_text('Made by stage_preflight. It overwrites this scratch scene on every run.\n', encoding='utf-8')
    changed = [n for n, data in (('scene.glb', scratch_glb(COLOR_A)),
                                 ('manifest.json', json.dumps(MANIFEST, indent=1).encode()),
                                 ('world.json', json.dumps(WORLD, indent=1).encode())) if _put(d / n, data)]
    return d, changed


# ---- the Claude Code settings: read only ---------------------------------------------------------------------------

def _main_repo():
    """The main checkout this code belongs to (a worktree's .git file points at it), for the hook line."""
    g = REPO / '.git'
    if g.is_file():
        m = re.match(r'gitdir:\s*(.+)', g.read_text(encoding='utf-8', errors='replace').strip())
        if m:
            common = Path(m.group(1).strip())
            if not common.is_absolute():
                common = (REPO / common).resolve()
            for p in [common, *common.parents]:
                if p.name == '.git':
                    return p.parent
    return REPO


def guard_path():
    return _main_repo() / 'skills' / 'ismail' / 'hooks' / 'named_ops_guard.py'


def hook_entry():
    """The PreToolUse entry skills/ismail/hooks/README.md gives, with this machine's path."""
    p = guard_path().as_posix()
    cmd = f"python -S -c \"import os, runpy; p = '{p}'; os.path.isfile(p) and runpy.run_path(p, run_name='__main__')\""
    return {'matcher': 'Bash|PowerShell|Write|Edit|MultiEdit', 'hooks': [{'type': 'command', 'command': cmd}]}


def settings_files():
    """[(label, settings dict or None, error)] for ~/.claude/settings.json and the project's .claude/settings.json and
    settings.local.json (the working folder's and this repository's). Read only."""
    cands = [_home() / '.claude' / 'settings.json']
    for base in dict.fromkeys([Path.cwd(), REPO]):
        cands += [base / '.claude' / 'settings.json', base / '.claude' / 'settings.local.json']
    out, seen = [], set()
    for f in cands:
        try:
            key = str(f.resolve()).lower()
        except OSError:
            key = str(f).lower()
        if key in seen or not f.is_file():
            continue
        seen.add(key)
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
            out.append((_short(f), d if isinstance(d, dict) else {}, None if isinstance(d, dict) else 'not a JSON object'))
        except (OSError, ValueError) as e:
            out.append((_short(f), {}, str(e)[:120]))
    return out


def rule_covers(rule, cmd=OP_CMD):
    """The tool (Bash or PowerShell) a permission rule lets run `cmd`, or None."""
    m = re.fullmatch(r'\s*(Bash|PowerShell)\s*(?:\((.*)\))?\s*', str(rule), re.S)
    if not m:
        return None
    spec = (m.group(2) or '').strip()
    if spec in ('', '*'):
        return m.group(1)
    if spec.endswith(':*'):
        return m.group(1) if cmd.startswith(spec[:-2]) else None
    return m.group(1) if fnmatch.fnmatchcase(cmd, spec) else None


def check_allow(files):
    allow, deny = {}, []
    for label, d, _ in files:
        perm = d.get('permissions') if isinstance(d.get('permissions'), dict) else {}
        if perm.get('defaultMode') == 'bypassPermissions':
            for t in ('Bash', 'PowerShell'):
                allow.setdefault(t, f'defaultMode bypassPermissions in {label}')
        for r in perm.get('allow') or []:
            t = rule_covers(r)
            if t:
                allow.setdefault(t, f'{r} in {label}')
        for r in perm.get('deny') or []:
            if rule_covers(r):
                deny.append((r, label))
    if deny:
        r, label = deny[0]
        raise Blocked(f'a deny rule {r} in {label} stops python -m ismail',
                      f'remove "{r}" from "permissions" > "deny" in {label}')
    if not allow:
        raise Blocked('no allow rule covers python -m ismail (every op would ask first, which throws a person out of VR)',
                      'add "Bash(python -m ismail:*)" to "permissions" > "allow" in ~/.claude/settings.json')
    out = '; '.join(f'{t}: {w}' for t, w in sorted(allow.items()))
    if os.name == 'nt' and len(allow) == 1:
        other = 'PowerShell' if 'Bash' in allow else 'Bash'
        out += f'; {other} has none (add "{other}(python -m ismail:*)" if agents run ops there)'
    return out


def _matcher_covers_bash(m):
    m = (m or '').strip()
    if m in ('', '*'):
        return True
    try:
        return bool(re.fullmatch(m, 'Bash'))
    except re.error:
        return 'Bash' in m.split('|')


def check_hook(files):
    where = []
    for label, d, _ in files:
        hooks = d.get('hooks') if isinstance(d.get('hooks'), dict) else {}
        for ent in hooks.get('PreToolUse') or []:
            if not isinstance(ent, dict):
                continue
            for h in ent.get('hooks') or []:
                cmd = str(h.get('command') or '') if isinstance(h, dict) else ''
                if 'named_ops_guard' in cmd:
                    where.append((label, ent.get('matcher'), cmd))
    line = json.dumps(hook_entry())
    if not where:
        return None, Blocked('named_ops_guard is not in the PreToolUse hooks',
                             f'add this entry to the "hooks" > "PreToolUse" list in ~/.claude/settings.json (make '
                             f'the list if it is not there): {line}')
    label, matcher, cmd = where[0]
    if not any(_matcher_covers_bash(m) for _, m, _ in where):
        return None, Blocked(f'named_ops_guard is in {label}, but its matcher {matcher!r} does not cover Bash',
                             f'set that entry\'s "matcher" to "Bash|PowerShell|Write|Edit|MultiEdit"')
    m = re.search(r"""['"]([^'"]*named_ops_guard\.py)['"]""", cmd) or re.search(r'(\S*named_ops_guard\.py)', cmd)
    if m and not Path(m.group(1)).expanduser().is_file():
        return None, Blocked(f'the hook in {label} points at {_short(m.group(1))}, which is not there',
                             f'in {label}, replace that hook entry with: {line}')
    return f'named_ops_guard in {label} (matcher {matcher!r})', None


# ---- the test page --------------------------------------------------------------------------------------------------

def _launch_page(url):
    """A headless, muted browser on the scratch scene, with its own throwaway profile. None when there is no browser."""
    from .capture import browser
    b = browser()
    if not b:
        return None
    prof = Path(tempfile.mkdtemp(prefix='ismail_preflight_'))
    p = subprocess.Popen([b, '--headless=new', '--mute-audio', '--window-size=640,360', f'--user-data-dir={prof}',
                          '--no-first-run', '--no-default-browser-check', '--disable-extensions',
                          '--disable-background-timer-throttling', '--disable-renderer-backgrounding', url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {'proc': p, 'profile': prof}


def _close_page(h):
    if not h or 'proc' not in h:
        return
    from .capture import _kill
    _kill(h['proc'])
    for _ in range(10):                            # the browser lets go of its profile a moment after it ends
        shutil.rmtree(h['profile'], ignore_errors=True)
        if not h['profile'].exists():
            break
        time.sleep(0.3)


def _events(rec, since, wait, types=None):
    got = link.http(rec, f'live/events?{link.q(scene=SCENE, since=since, wait=wait, limit=500, who="op")}',
                    timeout=wait + 15)
    return got['last'], [e for e in got['events'] if types is None or e.get('type') in types]


def _last_event(rec):
    return link.http(rec, f'live/events?{link.q(scene=SCENE, limit=1)}')['last']


def _page_fresh(rec, max_age=15):
    st = link.page_state(rec, SCENE)
    age = st.get('age_s')
    return st.get('state') is not None and age is not None and age <= max_age


def _reply_json(text, cmd):
    """The page's answer inside an op's reply line ('... the page: reload (12 ms): {...}')."""
    m = re.search(re.escape(cmd) + r' \((\S+) ms\): (\{.*\})', text, re.S)
    if not m:
        return None, None
    try:
        return m.group(1), json.loads(m.group(2))
    except ValueError:
        return m.group(1), None


# ---- the op ---------------------------------------------------------------------------------------------------------

def _pick_server(port):
    if port:
        return link.server_for(port=port)
    srv = link.servers()
    if not srv:
        return link.server_for()                   # raises the usual "no stage server" line
    hidden = lambda r: any(p.startswith('.') and p not in ('.', '..') for p in Path(r['scenes']).parts)
    return sorted(srv, key=hidden)[0]


@op(mutates=True)
def stage_preflight(port: int = None, say: bool = True, wait: float = 60, headless: bool = True) -> str:
    """Check the stage before a VR session or a demo, on a scratch scene of its own (_preflight, hidden from the
    page's scene list, overwritten on every run; nothing else is loaded over, moved or deleted). Checks, in order:
    server (it answers), presence (is someone in VR), allow_rule and guard_hook (read ~/.claude/settings.json and the
    project's .claude/settings.json and settings.local.json; never writes them), scratch (make or refresh the
    scratch scene), load (a headless, muted browser opens it and the page's selftest passes), hot_swap
    (stage_asset_install of a new scene.glb, answered by the page's reload), live_insert (stage_world update= and
    stage_set_load unload then load, each answered by the page), say (stage_say on the scratch page, a caption) and
    listen (a synthetic event, marked synthetic, goes round the inbox and stage_listen returns it). Each line is
    PASS, BLOCKED (with the next step) or SKIPPED. While someone is in VR only the checks that change nothing they
    see run, and the rest say SKIPPED. The verdict ("Preflight: pass." or "Preflight blocked: <first reason>.") is
    shown on the stage: in VR the headset says it out loud; otherwise it is a caption on the open page, never
    spoken on the computer. port= the stage server (default: the newest one serving a real scenes folder); say=False
    keeps the verdict off the stage; wait= seconds the test page may take to load; headless=False opens no browser
    (a page you opened on ?scene=_preflight is used)."""
    t_all = time.time()
    rows, state = [], {}

    def run(name, fn):
        t = time.time()
        try:
            detail, status, nxt = fn(), 'PASS', ''
        except Blocked as e:
            detail, status, nxt = e.detail, 'BLOCKED', e.next
        except Skipped as e:
            detail, status, nxt = str(e), 'SKIPPED', ''
        except OpError as e:
            detail, status, nxt = str(e).splitlines()[0][:300], 'BLOCKED', 'fix that, then run stage_preflight again'
        except Exception as e:                     # noqa: BLE001  (one check's surprise never hides the others)
            detail, status, nxt = f'{type(e).__name__}: {str(e)[:250]}', 'BLOCKED', 'run stage_preflight again; if it repeats, report this line'
        rows.append({'name': name, 'status': status, 'secs': time.time() - t, 'detail': detail, 'next': nxt})
        if status == 'BLOCKED' and 'took no answer' in detail and state.get('page'):
            state['page'], state['page_gone'] = False, name   # the page stopped answering: do not wait on it again
        return status

    # server
    def c_server():
        try:
            rec = _pick_server(port)
        except OpError as e:
            raise Blocked(str(e).split(';')[0], 'stage_start(scenes="<song>/video/vr/scenes"), then run stage_preflight again')
        h = link.http(rec, 'health', timeout=10)
        state['rec'] = rec
        return f'port {rec["port"]}, scenes {_short(rec["scenes"])}' + (f', pid {h.get("pid")}' if isinstance(h, dict) and h.get('pid') else '')
    run('server', c_server)
    rec = state.get('rec')

    def need_server():
        if not rec:
            raise Skipped('needs the stage server')

    # presence
    def c_presence():
        need_server()
        p = link.http(rec, 'live/presence', timeout=10)
        state['vr'] = bool(p.get('in_vr'))
        state['vr_scene'] = p.get('scene')
        if state['vr']:
            return f'IN VR in {p.get("scene")}: only the checks that change nothing they see run'
        return 'not in VR'
    run('presence', c_presence)
    in_vr = state.get('vr', False)

    files = settings_files()

    def c_allow():
        return check_allow(files)
    run('allow_rule', c_allow)

    def c_hook():
        ok, err = check_hook(files)
        if err:
            raise err
        return ok
    run('guard_hook', c_hook)

    # scratch
    def c_scratch():
        need_server()
        if in_vr and state.get('vr_scene') == SCENE:
            raise Skipped(f'the person is in {SCENE}; run again after they leave VR')
        d, changed = refresh_scratch(rec['scenes'])
        try:
            via = link.server_for(SCENE)
        except OpError:
            via = rec
        if int(via['port']) != int(rec['port']):
            raise Blocked(f'the stage ops for {SCENE} go to the server on port {via["port"]}, not {rec["port"]}',
                          f'run stage_preflight(port={via["port"]})')
        state['scratch'] = d
        return f'{_short(d)} ' + (f'(wrote {", ".join(changed)})' if changed else '(already fresh)')
    run('scratch', c_scratch)

    page = None

    def need_page(name):
        need_server()
        if in_vr:
            raise Skipped('someone is in VR (a test page is felt as lag); run again after they leave VR')
        if state.get('page_gone'):
            raise Skipped(f'the test page stopped answering at {state["page_gone"]}')
        if name != 'load' and not state.get('page'):
            raise Skipped('needs the test page (load)')
        if not state.get('scratch'):
            raise Skipped('needs the scratch scene')

    # load
    def c_load():
        nonlocal page
        need_page('load')
        if _page_fresh(rec, REUSE_AGE_S):
            state['page'] = True
            return f'a page already shows {SCENE}; it is used as it is (the load itself was not redone)'
        url = f'http://127.0.0.1:{rec["port"]}/?scene={SCENE}'
        if not headless:
            raise Blocked(f'no page shows {SCENE}', f'open {url} in a browser, then run stage_preflight again')
        since = _last_event(rec)
        page = _launch_page(url)
        if page is None:
            raise Blocked('no Edge or Chrome to open a test page with',
                          f'set ISMAIL_BROWSER to a Chromium browser, or open {url} yourself and run with headless=False')
        t_end = time.time() + float(wait)
        loaded = None
        while time.time() < t_end and loaded is None:
            since, evs = _events(rec, since, min(5.0, max(0.5, t_end - time.time())),
                                 {'page_load', 'page_error', 'scene_reload_failed'})
            for e in evs:
                if e['type'] == 'page_load':
                    loaded = e
                elif e['type'] in ('page_error', 'scene_reload_failed'):
                    raise Blocked(f'the page: {e["type"]}: {str(e.get("error") or e.get("message") or e)[:200]}',
                                  f'stage_events(scene="{SCENE}", types=["page_error"]) has the whole error')
            proc = page.get('proc')
            if proc is not None and proc.poll() is not None and loaded is None:
                raise Blocked('the test browser closed by itself', 'run stage_preflight again; if it repeats, open '
                              f'{url} in a browser and run with headless=False')
        if loaded is None:
            raise Blocked(f'no page_load from the test page within {wait:.0f} s',
                          f'run with wait={int(wait * 2)}, or open {url} in a browser to see what it shows')
        if loaded.get('selftest') != 'PASS':
            raise Blocked(f'the page loaded but its selftest is {loaded.get("selftest")} ({loaded.get("objects")} objects)',
                          f'stage_events(scene="{SCENE}", types=["page_load"]) has the details')
        t_end = time.time() + 15
        while not _page_fresh(rec) and time.time() < t_end:
            time.sleep(0.3)
        if not _page_fresh(rec):
            raise Blocked('the page loaded but posts no state', 'stage_status shows the pages; run again')
        state['page'] = True
        return f'headless page loaded {loaded.get("objects")} object(s), selftest PASS ({loaded.get("checked")} checked)'

    def c_hot_swap():
        need_page('hot_swap')
        src = Path(tempfile.mkdtemp(prefix='ismail_preflight_src_'))
        try:
            f = src / 'scene.glb'
            f.write_bytes(scratch_glb(COLOR_B))
            since = _last_event(rec)
            from .ops import stage_asset_install
            r = stage_asset_install(scene=SCENE, file=str(f), kind='scene')
        finally:
            shutil.rmtree(src, ignore_errors=True)
        if '\nthe page: ' in r:
            ms, res = _reply_json(r, 'reload')
            if isinstance(res, dict) and res.get('selftest') not in (None, 'PASS'):
                raise Blocked(f'the page reloaded but its selftest is {res.get("selftest")}',
                              f'stage_events(scene="{SCENE}", types=["scene_reload"]) has the details')
            return f'new scene.glb installed; the page reloaded it ({ms} ms' + (
                f', selftest {res["selftest"]})' if isinstance(res, dict) and res.get('selftest') else ')')
        # the page may have taken the new file by itself (its own hot reload) and refused a second reload meanwhile
        _, evs = _events(rec, since, 8, {'scene_reload', 'scene_reload_failed'})
        ok = [e for e in evs if e['type'] == 'scene_reload']
        if ok and ok[-1].get('selftest') in (None, 'PASS'):
            return 'new scene.glb installed; the page reloaded it by itself'
        why = r.split('\n', 1)[1] if '\n' in r else r
        raise Blocked(f'installed, but no page reload: {why[:200]}',
                      f'stage_events(scene="{SCENE}", types=["scene_reload", "scene_reload_failed", "cmd_done"])')

    def c_live_insert():
        need_page('live_insert')
        from .ops import stage_set_load, stage_world
        out = []
        r = stage_world(scene=SCENE, update={'floor': 0.0})
        if '\nthe page: world_reload' not in r:
            raise Blocked('world.json saved, but the page took no world_reload', f'stage_events(scene="{SCENE}", types=["cmd_done"])')
        out.append(f'world_reload {_reply_json(r, "world_reload")[0]} ms')
        for loaded in (False, True):
            r = stage_set_load(scene=SCENE, name=SET, loaded=loaded)
            if '; the page: load_set' not in r:
                raise Blocked(f'{SET} {"loaded" if loaded else "unloaded"} in world.json, but the page took no load_set',
                              f'stage_events(scene="{SCENE}", types=["cmd_done"]); then stage_set_load(scene="{SCENE}", '
                              f'name="{SET}", loaded=True)')
            out.append(f'{"load" if loaded else "unload"} {_reply_json(r, "load_set")[0]} ms')
        return 'answered: ' + ', '.join(out)

    def c_say():
        need_page('say')
        from .ops_page import stage_say
        r = stage_say(scene=SCENE, text='Preflight say check.', seconds=3, voice=False, sender='preflight')
        return f'caption shown on the test page ({_reply_json(r, "say")[0]} ms; a caption, not speech)'

    try:
        run('load', c_load)
        run('hot_swap', c_hot_swap)
        run('live_insert', c_live_insert)
        run('say', c_say)
    finally:
        _close_page(page)

    # listen: a synthetic event, never a voice type (those say "nobody is listening" and fire the hooks)
    def c_listen():
        need_server()
        if not state.get('scratch') and not (Path(rec['scenes']) / SCENE / 'scene.glb').is_file():
            raise Skipped('needs the scratch scene')
        from .ops import stage_listen
        before = link.http(rec, f'live/inbox?{link.q(who="op")}')['last']
        tag = f'preflight {int(time.time() * 1000)}'
        link.http(rec, f'live/event?{link.q(scene=SCENE)}',
                  {'type': PING, 'synthetic': True, 'tag': tag, 'text': 'synthetic preflight check, not the person'})
        r = stage_listen(who='op', since=before, wait=3, port=int(rec['port']))
        if tag in r:
            return 'a synthetic preflight_ping (marked synthetic, not the person) came back through stage_listen'
        _, evs = _events(rec, 0, 0, {PING})
        if any(e.get('tag') == tag for e in evs):
            raise Blocked('the scene got the synthetic event, but the inbox did not: the server runs older code',
                          'stage_restart when nobody is in VR, then run stage_preflight again')
        raise Blocked('the synthetic event did not come back through stage_listen',
                      f'stage_events(scene="{SCENE}", types=["{PING}"]) and stage_presence')
    run('listen', c_listen)

    # the verdict
    blocked = [r for r in rows if r['status'] == 'BLOCKED']
    skipped = [r for r in rows if r['status'] == 'SKIPPED']
    vr_skips = [r['name'] for r in skipped if 'VR' in r['detail']]
    text = (f'Preflight blocked: {REASON.get(blocked[0]["name"], blocked[0]["name"])}.' if blocked else 'Preflight: pass.')
    spoken = ' (' + ', '.join(vr_skips) + ' skipped in VR)' if vr_skips else ''
    said = 'not shown (say=False)'
    if say and rec:
        said = _say_verdict(rec, in_vr, state.get('vr_scene'), (text[:-1] + f', {len(vr_skips)} checks skipped in VR.' if vr_skips else text))
    lines = [f'stage preflight, port {rec["port"] if rec else "-"}, {time.time() - t_all:.1f} s']
    for r in rows:
        ln = f'{r["name"]:<11} {r["status"]:<7} {r["secs"]:5.1f} s  {r["detail"][:300]}'
        if r['next']:
            ln += f'; next: {r["next"]}'
        lines.append(ln)
    lines.append(f'verdict: {text}{spoken} On the stage: {said}.')
    return '\n'.join(lines)


def _say_verdict(rec, in_vr, vr_scene, text):
    """In VR: the headset's scene; the headset speaks every line in VR, so voice stays False and a desktop page on the
    same scene only shows it. Not in VR: a caption on the freshest open page. Never voice=True: that would speak on
    the computer."""
    from .ops_page import stage_say
    if in_vr and vr_scene:
        scene, how = vr_scene, f'said in the headset ({vr_scene})'
    else:
        try:
            pages = [p for p in link.http(rec, 'live', timeout=10) if p.get('scene') != SCENE and p.get('age_s', 1e9) < 30]
        except OpError:
            pages = []
        if not pages:
            return 'not shown (no page open, nobody in VR)'
        scene, how = pages[0]['scene'], f'a caption on the page showing {pages[0]["scene"]} (nobody in VR, so not spoken)'
    try:
        stage_say(scene=scene, text=text, seconds=8, voice=False, sender='preflight')
        return how
    except OpError as e:
        return f'not shown ({str(e).splitlines()[0][:120]})'
