"""stage_* ops: run the VR stage for a song's scenes and drive it (registered into api.OPS on import).

The stage is a three.js WebXR page (desktop or a headset) plus a small server (ismail/stage/server.py) that serves a
song's scenes and carries the live link: the page posts its state and events, agents queue commands and read the
answers. stage_start runs the server; the per-command ops (ops_page.py) drive the page; stage_events reads what the
person did and said. Scenes stay in the song (<song>/video/vr/scenes/<name>/: scene.glb, manifest.json, edits.json,
world.json, takes, voice notes, snapshots); the engine never writes song files except through these ops.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from ..api import OpError, op
from .. import machine
from . import link
from . import world as worldmod


def _scenes_dir(scenes):
    d = Path(scenes).resolve()
    if not d.is_dir():
        raise OpError(f'no folder {d}; pass the song\'s scenes folder, e.g. "<song>/video/vr/scenes"')
    return d


@op(mutates=True)
def stage_start(scenes: str, port: int = 8862, host: str = None, footage: str = None, esbuild: str = None) -> str:
    """Start the stage server for a song's scenes folder (each subfolder with a scene.glb is a scene) and reply with
    the address to open on the desktop or in the headset. A server already serving that folder is reused. host
    defaults to 127.0.0.1 (put `tailscale serve` in front for the headset); footage is where headset recordings
    from upload.html go; esbuild (a path to the esbuild package) lets the page boot as one bundle. Session files
    (logs, the speech cache, the bundle, update notes) go to <scenes>/_stage/."""
    d = _scenes_dir(scenes)
    for r in link.servers():
        if Path(r['scenes']) == d:
            return f'already serving {d} on {r["url"]} (pid {r["pid"]}); scenes: {", ".join(_scene_names(d))}'
        if int(r['port']) == int(port):
            raise OpError(f'port {port} is taken by the stage server for {r["scenes"]}; stage_stop(port={port}) or pick '
                          f'another port')
    state = d / '_stage'
    state.mkdir(exist_ok=True)
    args = [sys.executable, '-m', 'ismail.stage.server', '--scenes', str(d), '--port', str(port)]
    if host:
        args += ['--host', host]
    if footage:
        args += ['--footage', footage]
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2]) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    if esbuild:
        env['ESBUILD'] = esbuild
    log = open(state / 'server.log', 'a', encoding='utf-8')
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if os.name == 'nt' else 0
    p = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags,
                         start_new_session=os.name != 'nt')
    for _ in range(50):
        time.sleep(0.2)
        if p.poll() is not None:
            tail = (state / 'server.log').read_text(encoding='utf-8', errors='replace')[-600:]
            raise OpError(f'the stage server did not start: {tail.strip()}')
        rec = next((r for r in link.servers() if int(r['port']) == int(port)), None)
        if rec:
            try:
                info = link.http(rec, 'stage', timeout=3)
                return (f'stage on {rec["url"]} (pid {rec["pid"]}) for {d}; scenes: {", ".join(info["scenes"]) or "none"}; '
                        f'default {info["default"] or "none"}. Open {rec["url"]}?scene=<name>; log {state / "server.log"}')
            except OpError:
                pass
    raise OpError(f'the stage server started (pid {p.pid}) but did not answer within 10 s; see {state / "server.log"}')


def _scene_names(d):
    return sorted(p.name for p in Path(d).iterdir() if (p / 'scene.glb').is_file())


@op(mutates=True)
def stage_stop(port: int = None, scenes: str = None) -> str:
    """Stop a stage server (by port, or the one serving `scenes`; the only one when neither is given). A page left
    open keeps its scene on screen but loses the live link until a server starts again."""
    srv = link.servers()
    if scenes:
        d = Path(scenes).resolve()
        srv = [r for r in srv if Path(r['scenes']) == d]
    elif port:
        srv = [r for r in srv if int(r['port']) == int(port)]
    elif len(srv) > 1:
        raise OpError('several stage servers run: pass port= (' + ', '.join(f'{r["port"]}: {r["scenes"]}' for r in srv) + ')')
    if not srv:
        return 'no matching stage server is running'
    from ..live.ops import _kill_tree, _pid_alive
    out = []
    for r in srv:
        _kill_tree(r['pid'])
        for _ in range(25):
            if not _pid_alive(r['pid']):
                break
            time.sleep(0.2)
        try:
            (link.registry_dir() / f'{r["port"]}.json').unlink()
        except OSError:
            pass
        out.append(f'stopped the stage on port {r["port"]} ({r["scenes"]})')
    return '; '.join(out)


@op()
def stage_status(scene: str = None) -> str:
    """The running stage servers, their scenes, and which pages are live (scene, mode, seconds since the page last
    posted its state). With scene=, that page's state: VR or desktop, camera position and heading, selection,
    captions queued."""
    srv = link.servers()
    if not srv:
        return 'no stage server is running (stage_start(scenes="<song>/video/vr/scenes"))'
    lines = []
    for r in srv:
        try:
            info = link.http(r, 'stage', timeout=5)
            live = link.http(r, 'live', timeout=5)
        except OpError as e:
            lines.append(f'port {r["port"]}: not answering ({e}); stage_stop(port={r["port"]}) and stage_start again')
            continue
        try:
            h = link.http(r, 'health', timeout=5)
            health = (f'{"well" if h["ok"] else "UNWELL"}: {h["busy"]}/{h["workers"]} workers busy, {h["long_polls"]} '
                      f'long-polls, {h["threads"]} threads, {h["served"]} requests in {h["uptime_s"]} s, '
                      f'{h["state_free_mb"]} MB free' + (f', a write failed {h["disk_warned_s_ago"]} s ago'
                                                       if h.get('disk_warned_s_ago') is not None else ''))
        except OpError:
            health = 'no /health (an older server)'
        pages = ', '.join(f'{p["scene"]} ({p.get("mode") or "?"}, {p["age_s"]} s ago)' for p in live) or 'no page open'
        lines.append(f'{r["url"]} pid {r["pid"]}: {r["scenes"]}; scenes {", ".join(info["scenes"])}; default '
                     f'{info["default"] or "none"}; pages: {pages}; {health}')
    if scene:
        rec = link.server_for(scene)
        st = link.page_state(rec, scene)
        s = st.get('state')
        if not s:
            lines.append(f'{scene}: no page has posted its state')
        else:
            cam = s.get('camera') or {}
            lines.append(f'{scene}: {"in VR" if s.get("xr") else "desktop"}, state {st["age_s"]} s old; camera '
                         f'{[round(v, 2) for v in cam.get("position", [])]} heading {cam.get("heading_deg")}; selection '
                         f'{s.get("selection") or "none"}; captions queued {s.get("captions_queued", 0)}')
    return '\n'.join(lines)


def _event_line(e):
    skip = {'id', 'ts', 'type', 'page'}
    rest = {k: v for k, v in e.items() if k not in skip}
    txt = json.dumps(rest, separators=(',', ':'), default=str)
    return f'{e.get("id")} {str(e.get("ts", ""))[11:19]} {e.get("type")} {txt[:300]}'


@op()
def stage_events(scene: str, since: int = 0, types: list = None, limit: int = 100, wait: float = 0,
                 who: str = None) -> str:
    """What happened on the stage page for a scene, oldest first, one line each: id, time, type, details. since= an
    event id (only newer events; 0 = the most recent `limit`), types= only those types (e.g. ["voice_message",
    "gesture"]), wait= seconds to wait for a newer event (long poll, up to 60). The first line gives the newest id
    to pass as since= next time. Voice notes arrive as voice_in (audio landed) then voice_message (its text);
    commands answer with cmd_done. Following a scene with since= counts as listening to the person there: who= is the
    name the headset shows for you (e.g. "crossroads film"); without it they see "an unnamed agent"."""
    rec = link.server_for(scene)
    path = (f'live/events?{link.q(scene=scene, since=since, wait=min(float(wait or 0), 60), limit=1000, who=who)}' if since
            else f'live/events?{link.q(scene=scene, limit=1000)}')
    got = link.http(rec, path, timeout=(wait or 0) + 15)
    evs = got['events']
    if types:
        evs = [e for e in evs if e.get('type') in set(types)]
    evs = evs[-int(limit):]
    return '\n'.join([f'last {got["last"]}; {len(evs)} events'] + [_event_line(e) for e in evs])


@op()
def stage_listen(who: str, since: int = None, wait: float = 25, port: int = None) -> str:
    """Listen to the person in the headset across every scene: their voice notes (voice_in, then voice_message with
    the text), whether a note was heard (voice_heard), entering and leaving VR (headset, vr_exit) and scene switches,
    oldest first. who= your name as the headset shows it ("Claude is listening": e.g. "crossroads film"); while you
    call this at least every 40 s you count as listening, and a note you were handed is announced as "handed to
    <who>" if you do not answer it within 6 s (a note nobody was handed gets "nobody is listening" said out loud). since= the `last` from
    the previous call (left out: start now), wait= seconds to wait for something new (up to 60). Answer a note with
    stage_say or stage_voice_ack on its scene."""
    rec = link.server_for(port=port)
    if since is None:
        since = link.http(rec, f'live/inbox?{link.q(who=who)}')['last']
    got = link.http(rec, f'live/inbox?{link.q(who=who, since=since, wait=min(float(wait or 0), 60))}',
                    timeout=(wait or 0) + 15)
    lines = [f'last {got["last"]}; {len(got["events"])} events (pass since={got["last"]} next)']
    for e in got['events']:
        lines.append(f'[{e.get("scene")}] ' + _event_line({k: v for k, v in e.items() if k not in ('seq', 'scene')}))
    return '\n'.join(lines)


@op()
def stage_presence(port: int = None) -> str:
    """Is the person in the headset, in which scene, since when; their last voice note and whether it was heard;
    who is listening (stage_listen, or following a scene with stage_events since=); unread notes; the hooks set up
    (~/.ismail/stage_hooks.json; a song's _stage/hooks.json only when that file trusts its scenes folder: commands
    run on entered_vr, left_vr, voice_note, voice_unheard with STAGE_EVENT, STAGE_SCENE, STAGE_TEXT, STAGE_URL ... in their environment)."""
    rec = link.server_for(port=port)
    p = link.http(rec, 'live/presence')
    lv = p.get('last_voice') or {}
    lines = [f"{'IN VR' if p['in_vr'] else 'not in VR'}" + (f" in {p['scene']}" if p.get('scene') else '')
             + (f" for {p['since']} s" if p['in_vr'] and p.get('since') else '')
             + (f"; page seen {p['page_age_s']} s ago" if p.get('page_age_s') is not None else '; no page yet'),
             'listening: ' + (', '.join(p['listening']) or 'NOBODY'),
             f"unread notes: {p['unread']}" + (f" ({rec['scenes']}/_stage/unread.jsonl)" if p['unread'] else '')]
    if lv:
        lines.append(f"last note [{lv.get('scene')}] {lv.get('ts', '')[11:19]} {lv.get('heard') or ''}: {lv.get('text')}")
    lines.append('hooks: ' + (', '.join(f'{k} x{n}' for k, n in p['hooks'].items()) or 'none'))
    return '\n'.join(lines)


@op(mutates=True)
def stage_cmd(scene: str, type: str, fields: dict = None, timeout: float = 30) -> str:
    """A page command that has no op of its own yet, by its type with its fields (the escape hatch; a type that has
    a typed op is refused with that op's name). Waits for the page's answer up to timeout seconds."""
    from .ops_page import TYPED
    if type in TYPED:
        raise OpError(f'{type} has its own op: use {TYPED[type]} (its arguments are typed and documented)')
    return link.page_cmd(scene, type, fields or {}, timeout=timeout)


@op(mutates=True)
def stage_world(scene: str, scenes: str = None, world: dict = None) -> str:
    """Read a scene's world.json (who plays whom, facings, partners, floor, keep-out boxes, spawn, credits, build),
    or with world= write it (checked first; the old one kept in the scene's history/). scenes= the scenes folder when
    no server runs. The page reads it again on its next load or scene switch."""
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(scene)['scenes'])
    if not (d / scene).is_dir():
        raise OpError(f'no scene {scene!r} in {d}')
    if world is None:
        return json.dumps(worldmod.load_world(d, scene), indent=1)
    try:
        f = worldmod.save_world(d, scene, world)
    except ValueError as e:
        raise OpError(str(e))
    return f'wrote {f}: ' + json.dumps(worldmod.load_world(d, scene), separators=(', ', ': '))


def _set_members(d, scene, items):
    """The scene's node names (manifest.json) and people (world.json actors) each pattern of a load set matches."""
    import fnmatch
    names = []
    m = d / scene / 'manifest.json'
    if m.is_file():
        try:
            names = list((json.loads(m.read_text(encoding='utf-8')) or {}).get('objects') or {})
        except ValueError:
            names = []
    people = list(worldmod.load_world(d, scene).get('actors') or {})
    return {p: sorted({n for n in names + people if fnmatch.fnmatchcase(n, p)}) for p in items}


@op()
def stage_sets(scene: str, scenes: str = None) -> str:
    """The scene's load sets (world.json sets): each one's items (node names or globs), what they match in the scene
    (nodes from manifest.json, people from world.json actors), its note, and whether it is unloaded. See
    stage_set_define and stage_set_load."""
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(scene)['scenes'])
    w = worldmod.load_world(d, scene)
    un = set(w.get('unloaded') or [])
    out = {n: {'unloaded': n in un, 'note': s.get('note'), 'items': s['items'],
               'matches': _set_members(d, scene, s['items'])} for n, s in (w.get('sets') or {}).items()}
    return json.dumps(out, indent=1) if out else f'no load sets in {scene} (stage_set_define makes one)'


@op(mutates=True)
def stage_set_define(scene: str, name: str, items: list = None, note: str = None, remove: bool = False,
                     scenes: str = None) -> str:
    """Name a load set: a group of the room (the dancers, the band, the bar people) that can be unloaded to keep the
    Quest light while work goes on elsewhere, and loaded again (stage_set_load). items: node names or globs
    ('person_couple_*'); a matched group carries its children, and matched people (world.json actors) neither play,
    follow nor rest while unloaded. note: what the set is in the film, shown on its placeholder (default
    'unloaded: N in the film'). remove=True drops the set (loading it first if it was unloaded). Every item must match
    a node or a person. Saved in world.json sets (the old one kept in history/)."""
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(scene)['scenes'])
    if not (d / scene).is_dir():
        raise OpError(f'no scene {scene!r} in {d}')
    w = worldmod.load_world(d, scene)
    sets = dict(w.get('sets') or {})
    un = list(w.get('unloaded') or [])
    if remove:
        if name not in sets:
            raise OpError(f'no load set {name!r} in {scene} (sets: {", ".join(sets) or "none"})')
        was = name in un
        sets.pop(name)
        w['sets'], w['unloaded'] = sets, [x for x in un if x != name]
        worldmod.save_world(d, scene, w)
        return f'removed the load set {name} from {scene}' + (_set_now(scene, name, True) if was else '')
    if not items:
        raise OpError('items: the node names or globs in the set, e.g. ["person_couple_*"]')
    items = [items] if isinstance(items, str) else list(items)
    got = _set_members(d, scene, items)
    none = [p for p, v in got.items() if not v]
    if none:
        raise OpError(f'{", ".join(none)} match no node and no person in {scene} (stage_sets lists the sets; node '
                      'names are the Blender object names)')
    sets[name] = {'items': items, **({'note': note} if note else {})}
    w['sets'] = sets
    try:
        f = worldmod.save_world(d, scene, w)
    except ValueError as e:
        raise OpError(str(e))
    n = sorted({x for v in got.values() for x in v})
    more = ', ...' if len(n) > 12 else ''
    return f'load set {name} in {scene}: {len(n)} ({", ".join(n[:12])}{more}); saved in {f}'


@op(mutates=True)
def stage_set_load(scene: str, name: str, loaded: bool = True, scenes: str = None) -> str:
    """Unload a load set (loaded=False) or load it again (loaded=True, the default). Unloaded: its meshes are not
    drawn, cast no shadow, and at the next load are never compiled or uploaded (on an open page, what only the set
    used is freed on the GPU now); the people in it stop and neither play, follow nor rest; a ghost box around each
    member and a label with the set's name stand in its place. Loaded: it comes back a piece at a time. Saved in
    world.json unloaded, so it holds across reloads; an open page changes now (page command load_set) and replies
    with what it hid or showed ({name, items, persons, meshes, tris, freed}). Emits: set_unloaded / set_loaded
    {name, items, persons, meshes, tris}."""
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(scene)['scenes'])
    w = worldmod.load_world(d, scene)
    sets = w.get('sets') or {}
    if name not in sets:
        raise OpError(f'no load set {name!r} in {scene} (sets: {", ".join(sets) or "none"}; stage_set_define makes one)')
    was = list(w.get('unloaded') or [])
    un = [x for x in was if x != name] + ([] if loaded else [name])
    if un != was:
        w['unloaded'] = un
        worldmod.save_world(d, scene, w)
    return f'{name} {"loaded" if loaded else "unloaded"} in {scene}' + _set_now(scene, name, loaded, sets[name])


def _set_now(scene, name, loaded, spec=None):
    """Tell an open page (best effort: with no page open it applies at the next load)."""
    try:
        r = link.page_cmd(scene, 'load_set', {'name': name, 'loaded': loaded, **({'set': spec} if spec else {})},
                          timeout=20)
        return '; the page: ' + (r if isinstance(r, str) else json.dumps(r, separators=(', ', ': ')))
    except Exception:                                      # noqa: BLE001  (no page open, or an older page)
        return '; the page takes it at its next load'


BRIDGE = Path(__file__).resolve().parent / 'bridge' / 'blender_bridge.py'


def export_plan(d, scene):
    """The command, cwd and env stage_scene_export runs for a scene (its lineage resolved), without running it."""
    try:
        b = worldmod.build_of(d, scene)
    except ValueError as e:
        raise OpError(str(e))
    if not b:
        raise OpError(f'scene {scene!r} has no build in its world.json, and derives from no scene that has one (add '
                      f'"build": {{"script": "video/rooms/<room>.py"}}, or "derives_from": "<scene>", with stage_world)')
    if not b['script'].is_file():
        raise OpError(f'the build script {b["script"]} does not exist (world.json build.script / build.root of '
                      f'{b["line"][-1]})')
    for p in b['passes']:
        if not p.is_file():
            raise OpError(f'the pass {p} does not exist (world.json "pass", relative to the build root)')
    env = {**b['env'], 'VR_EXPORT': str(d / scene), 'VR_BRIDGE': str(BRIDGE), 'STAGE_SCENE': scene,
           'STAGE_SCENES': str(d)}
    if len(b['line']) > 1:                         # a derived scene: passes, merged edits, the diet in the bridge
        merged = d / '_stage' / f'edits_{scene}_merged.json'
        merged.parent.mkdir(exist_ok=True)
        merged.write_text(json.dumps(worldmod.merged_edits(b['edits']), indent=1), encoding='utf-8')
        env.update({'VR_PASS': os.pathsep.join(str(p) for p in b['passes']), 'VR_EDITS': str(merged), 'VR_DIET': '1'})
    return b, env


@op(mutates=True)
def stage_scene_export(scene: str, scenes: str = None, args: list = None, wait_min: float = 30) -> str:
    """Rebuild a scene in Blender, in one of the machine's heavy-job slots (stands in line up to wait_min minutes):
    its world.json "build" script (script, root, env) with VR_EXPORT set to the scene folder. A derived scene
    (world.json "derives_from") is built from its ancestor's full build, then its line's passes ("pass", oldest
    first), then the edits of its line merged (its own win), then the Quest diet. Room scripts should exec
    os.environ["VR_BRIDGE"] (the engine's bridge, which runs the passes and the diet). Scripts can scope their
    outputs by STAGE_SCENE (renders/<scene>/...). An open page swaps the new room in under the construct (never while
    the person is in VR). Replies with the export's object count and the log path."""
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(scene)['scenes'])
    b, env = export_plan(d, scene)
    from ..video import blender_exe
    exe = blender_exe()
    log = d / '_stage' / f'export_{scene}.log'
    log.parent.mkdir(exist_ok=True)
    cmd = [exe, '-b', '--factory-startup', '-P', str(b['script'])] + (['--'] + [str(a) for a in args] if args else [])
    t0 = time.time()
    with machine.slot('cpu', f'stage_scene_export {scene}', est_s=600, wait=wait_min * 60):
        with open(log, 'w', encoding='utf-8') as fh:
            r = subprocess.run(cmd, cwd=b['cwd'], stdout=fh, stderr=subprocess.STDOUT, env={**os.environ, **env})
    text = log.read_text(encoding='utf-8', errors='replace')
    done = [ln for ln in text.splitlines() if ln.startswith('VR EXPORT')]
    if r.returncode != 0 or not done:
        raise OpError(f'the export failed (exit {r.returncode}); the end of {log}: ' + text[-800:].strip())
    passes = [ln for ln in text.splitlines() if ln.startswith('VR PASS')]
    line = ' <- '.join(b['line'])
    if len(b['line']) > 1 and len(passes) < len(b['passes']):
        raise OpError(f'exported, but {len(passes)} of {len(b["passes"])} passes ran: the build script {b["script"]} '
                      f'does not exec the engine bridge (os.environ["VR_BRIDGE"]); {log}')
    return f'{done[-1]} in {time.time() - t0:.0f} s (lineage {line}, {len(passes)} passes); log {log}'


@op(mutates=True)
def stage_scene_new(name: str, source: str, scenes: str = None, pass_script: str = None, export: bool = False) -> str:
    """A new scene derived from an existing one (a remodel, another era, the same place at dawn): world.json says
    derives_from=source (so its room is built from the source's full build, never from a blockout), with
    pass=pass_script (the variant's changes on the built room, relative to the build root), assets=source (the
    actors' bodies), and the source's people, facings, partners, floor, keep-out boxes and sky. The page's own pieces
    are copied: trees, names, cues, waypoints. The person's takes, voice notes and snapshots stay with the source.
    export=True runs stage_scene_export right away (a heavy job); without it the scene appears after its first
    export. Before showing it to the person, look at the variant beside its source from the same camera."""
    import shutil
    d = _scenes_dir(scenes) if scenes else Path(link.server_for(source)['scenes'])
    if not worldmod.NAME_OK(name):
        raise OpError('a scene name is letters, digits, _ and - only')
    if not (d / source).is_dir():
        raise OpError(f'no scene {source!r} in {d}')
    if (d / name).exists():
        raise OpError(f'{d / name} exists already; pick another name or edit its world.json with stage_world')
    src = worldmod.load_world(d, source)
    w = {k: src[k] for k in ('actors', 'facings', 'partners', 'floor', 'keep_out', 'spawn', 'credits', 'sky') if src.get(k)}
    w.update({'derives_from': source, 'assets': src.get('assets') or source})
    if pass_script:
        w['pass'] = pass_script
    (d / name).mkdir()
    copied = []
    for piece in worldmod.COPY_FROM_PARENT:
        f = d / source / piece
        if f.is_dir():
            shutil.copytree(f, d / name / piece)
            copied.append(piece + '/')
        elif f.is_file():
            shutil.copy2(f, d / name / piece)
            copied.append(piece)
    try:
        worldmod.save_world(d, name, w)
        worldmod.lineage(d, name)
        export_plan(d, name)
    except (ValueError, OpError) as e:
        raise OpError(f'made {d / name} but its world.json is not ready: {e}')
    out = (f'scene {name} derives from {source} (pass {pass_script or "none yet"}); copied {", ".join(copied) or "nothing"}; '
           f'world.json: {json.dumps(w, separators=(", ", ": "))}')
    if export:
        out += '\n' + stage_scene_export(scene=name, scenes=str(d))
    else:
        out += f'\nnext: stage_scene_export(scene="{name}", scenes="{d}") builds it'
    return out


@op(mutates=True)
def stage_note(scenes: str, title: str, level: str = 'normal') -> str:
    """Log a change to the stage for the "updates ready" card in the headset (updates.json in <scenes>/_stage): the
    card counts the notes newer than the code the page runs and shows the most important. level: normal (green),
    important (amber: something the person asked for or will notice), critical (orange: a fix for something broken
    or sickening). Write it when the change lands."""
    if level not in ('normal', 'important', 'critical'):
        raise OpError('level is normal, important or critical')
    f = _scenes_dir(scenes) / '_stage' / 'updates.json'
    f.parent.mkdir(exist_ok=True)
    d = json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {'updates': []}
    d['updates'].append({'t_ms': int(time.time() * 1000), 'title': title, 'level': level})
    f.write_text(json.dumps(d, indent=1), encoding='utf-8')
    return f'{len(d["updates"])} notes; {level}: {title}'


from . import ops_page  # noqa: E402,F401  (registers the page command ops)


@op()
def stage_performance(scene: str, perf: str = None) -> str:
    """A performance as text (the newest when perf= is not given): who was followed, how long, the markers, and each
    voice clip with its words and their times on the Follow clock (seconds since the Follow began), snapped onto the
    measured voice. A take kept from it plays its voice with it (meta: performance, perf_shift = take time 0 on the
    Follow clock). Files: <scenes>/<scene>/performances/<perf>/perf.json and clip_<n>.<ext>."""
    from . import perform
    d = Path(link.server_for(scene)['scenes'])
    out = perform.summary(d, scene, perf)
    if out is None:
        raise OpError(f'no performance {perf!r} in {scene}' if perf else f'no performances in {scene} yet (a Follow makes one)')
    return out



def _body_of(scene, person):
    """(actors folder, body name) for a person in a scene: world.json actors says which body plays them; a derived
    scene's bodies are in its assets scene."""
    from .world import load_world
    d = Path(link.server_for(scene)['scenes'])
    w = load_world(d, scene)
    who = (w.get('actors') or {}).get(person)
    if not who:
        raise OpError(f'no body for {person!r} in {scene}: world.json actors casts {sorted(w.get("actors") or {})}')
    return d / (w.get('assets') or scene) / 'actors', who


@op()
def stage_actor_profile(scene: str, person: str) -> str:
    """A person's actor profile (person= the scene's person, as for stage_control_set; world.json actors says which
    body plays them): the rig type read from the body, its named parts (bone chains, parents first; arms and legs can
    reach) and the control maps saved for it. The profile lives beside the body (actors/<body>.json next to
    <body>.glb), so it goes wherever the body goes, to whoever it plays. stage_actor_map_save stores a map;
    stage_control_map(preset=) applies one."""
    from . import rigs
    adir, who = _body_of(scene, person)
    try:
        return json.dumps(rigs.profile(adir, who), indent=1)
    except FileNotFoundError:
        raise OpError(f'{person} is played by {who}, but there is no {adir.parent.name}/actors/{who}.glb')


@op(mutates=True)
def stage_actor_map_save(scene: str, person: str, name: str, pins: dict = None, drives: list = None) -> str:
    """Save a control map on a person's actor (in the body's profile, actors/<body>.json; the previous file kept as
    .json.prev), to apply later with stage_control_map(preset=name). pins: {"hips": "<seat object>" | [x, y, z],
    "foot_l": ..., "foot_r": ..., "feet": ...}; drives: [{part, mode, joint, at, scale, touch}] as for
    stage_control_set, each checked against the body's parts. A map named "default" applies by itself when a Follow
    of anyone this body plays starts and nothing was set in the session."""
    from . import rigs
    adir, who = _body_of(scene, person)
    if not (pins or drives):
        raise OpError('a map needs pins= or drives= (or both)')
    try:
        prof = rigs.profile(adir, who)
        checked = [rigs.check_drive(x, prof['parts']) for x in drives or []]
    except FileNotFoundError:
        raise OpError(f'{person} is played by {who}, but there is no {adir.parent.name}/actors/{who}.glb')
    except ValueError as e:
        raise OpError(str(e))
    if set(pins or {}) - {'hips', 'foot_l', 'foot_r', 'feet'}:
        raise OpError("pins are hips, foot_l, foot_r or feet")
    f = rigs.save_map(adir, who, name, {**({'pins': pins} if pins else {}), **({'drives': checked} if checked else {})})
    return (f'saved map {name!r} on {who} (plays {person}) in {f}: {len(checked)} drives, pins: {", ".join(pins or {}) or "none"}; '
            f'presets now: {", ".join(rigs.profile(adir, who)["maps"])}')



def _scene_dir(scene):
    d = Path(link.server_for(scene)['scenes']) / scene
    if not d.is_dir():
        raise OpError(f'no scene {scene!r}')
    return d


@op()
def stage_takes(scene: str, query: str = None, person: str = None, kept: bool = None, limit: int = 30) -> str:
    """A scene's takes, newest first, with what the person said while recording each (the take's own audio, or the
    voice clips of the performance it came from), its label and notes. query= words that must all appear in what was
    said, the label or the notes (case-insensitive; matches show the word and its second in the take); person= one
    person's takes; kept=True only the kept ones. Lines: id, person, seconds, kept, label, what was said (shortened),
    hits, notes. stage_take_note names a take or adds a note; stage_take_transcribe fills in an older take's words."""
    from . import takes
    rows = takes.listing(_scene_dir(scene), query=query, person=person, kept=kept, limit=limit)
    if not rows:
        return f'no takes in {scene}' + (f' matching {query!r}' if query else '') + (f' for {person}' if person else '')
    out = []
    for r in rows:
        said = (r['said'][:140] + '...') if len(r['said']) > 140 else r['said']
        line = (f"{r['id']}  {r['person'] or '-'}  {r['seconds'] or '?'} s{'  KEPT' if r['kept'] else ''}"
                + (f"  [{r['label']}]" if r['label'] else '') + (f'  said: "{said}"' if said else '  (nothing said)'))
        if r['hits']:
            line += '  hits: ' + ', '.join(f'{w}@{t:.1f}s' for w, t in r['hits'])
        for n in r['notes']:
            line += f"\n    note{' @' + str(n['at']) + 's' if 'at' in n else ''} ({n.get('by')}): {n.get('text')}"
        out.append(line)
    return '\n'.join(out)


@op(mutates=True)
def stage_take_note(scene: str, take: str, label: str = None, note: str = None, at: float = None, sender: str = None) -> str:
    """Name a take (label=, e.g. what the person called it while recording) and/or add a note (note=, at= seconds on
    the take's clock if it is about a moment), kept in the take's meta.json and found by stage_takes(query=). sender=
    who adds it (default "agent")."""
    from . import takes
    if label is None and not note:
        raise OpError('give label= and/or note=')
    try:
        m = takes.note(_scene_dir(scene), take, label=label, text=note, at=at, by=sender or 'agent')
    except FileNotFoundError:
        raise OpError(f'no take {take!r} in {scene}; stage_takes lists them')
    return f"{take}: label {m.get('label')!r}, {len(m.get('notes') or [])} notes"


@op(mutates=True)
def stage_take_transcribe(scene: str, take: str) -> str:
    """Transcribe a take's own audio now (takes recorded before takes were transcribed as they land, or one whose
    transcription failed): writes takes/<id>/voice.json with the words on the take's clock, snapped onto the
    measured voice. Needs the speech server (speakwright) running."""
    from . import takes
    from .server import stt_words
    d = _scene_dir(scene) / 'takes' / take
    try:
        v = takes.transcribe(d, stt_words)
    except FileNotFoundError as e:
        raise OpError(f'{e}; stage_takes lists the takes')
    except OSError as e:
        raise OpError(f'the speech server did not answer ({e}); is speakwright running?')
    return f"{take}: \"{v['text']}\"" + (f" ({len(v['words'])} words)" if v.get('words') else f" ({v.get('words_missing')})")


def _take_dir(scene, take):
    d = _scene_dir(scene) / 'takes' / take
    if not (d / 'frames.jsonl').is_file():
        raise OpError(f'no take {take!r} in {scene}; stage_takes lists them')
    return d


@op()
def stage_take_sync(scene: str, takes: list, bpm: float, loops: bool = False, bars: list = None, top: int = 4) -> str:
    """How takes keep time with a song and with each other (no page needed). Per take: its pulse (the period of its
    main repeating motion from the head and wrists, and how clear it is, 0 to 1), its own BPM, the playback rate that
    puts the pulse on 1/2, 1, 2 or 4 beats of bpm and its length in bars at that rate, its first low point (the down of
    a bounce) in beats, and the seam when it wraps (jump in cm and in typical steps, per channel). Per pair: the lag of
    the second behind the first in beats, and how well their movement matches (0 to 1). loops=True: for each take,
    the best whole-bar windows to cut as loops (bars=[2, 3, 4], top=4), scored by a clear pulse on the beat, a small
    seam, enough movement and little travel on the floor; give one to stage_take_loop(start=, end=)."""
    from . import takesync
    if isinstance(takes, str):
        takes = [takes]
    if not takes or not bpm or bpm <= 0:
        raise OpError('takes=[take id, ...] and bpm= the song tempo')
    dirs = [_take_dir(scene, t) for t in takes]
    try:
        if loops:
            res = {d.name: takesync.best_loops(d, bpm, bars=tuple(bars or (2, 3, 4)), top=top) for d in dirs}
            out = []
            for name, rows in res.items():
                out.append(f'{name}: ' + ('no window with a clear pulse' if not rows else ''))
                out += [f"  {r['start']}-{r['end']} s ({r['bars']} bars): score {r['score']}, pulse {r['pulse_beats']} beat "
                        f"{r['pulse_off_pct']}% off, clarity {r['clarity']}, seam {r['seam_x_step']}x a step, "
                        f"{r['energy_m_s']} m/s, drift {r['drift_m']} m" for r in rows]
            return '\n'.join(out)
        res = takesync.measure(dirs, bpm)
    except ValueError as e:
        raise OpError(str(e))
    out = [f'at {bpm} BPM:']
    for r in res['takes']:
        sm = r.pop('seam')
        out.append(f"{r.pop('take')}: " + ', '.join(f'{k} {v}' for k, v in r.items()))
        out.append('   seam: ' + '; '.join(f"{k} {v['jump_cm']} cm ({v['x_step']}x a step), speed jump {v['speed_jump_cm_s']} cm/s"
                                       for k, v in sm.items()))
    out += [f"pair {p['a']} / {p['b']}: lag {p['lag_beats']} beats, movement match {p['match']}" for p in res['pairs']]
    return '\n'.join(out)


@op(mutates=True)
def stage_take_loop(scene: str, take: str, name: str, start: float = None, end: float = None, bpm: float = None,
                    bars: list = None, blend: float = 0.4) -> str:
    """Cut a window of a take into a new silent take that loops without a jump: the last blend seconds (0.4, tuned
    with the user) cross-fade into the frames just before start, so the wrap is continuous in position and speed.
    start/end in the take's seconds; or bpm= (and bars=[2, 3, 4]) to take the best whole-bar window that
    stage_take_sync(loops=True) would list first. blend=0: a plain window. The new take is <timestamp>_<name> beside
    the source, without its performance link (no borrowed voice), its trim or its label; it plays with
    stage_actor_play(take=, loop=True). Returns its id, the window and the seam after the blend."""
    from . import takesync
    src = _take_dir(scene, take)
    try:
        picked = None
        if start is None or end is None:
            if not bpm:
                raise OpError('give start= and end= (seconds), or bpm= to pick the best whole-bar window')
            rows = takesync.best_loops(src, bpm, bars=tuple(bars or (2, 3, 4)), top=1)
            if not rows:
                raise OpError(f'no whole-bar window of {take} has a clear pulse at {bpm} BPM; give start= and end=')
            picked = rows[0]
            start, end = picked['start'], picked['end']
        tid, m, sm = takesync.cut(src, name, float(start), float(end), blend=float(blend))
    except ValueError as e:
        raise OpError(str(e))
    why = f" (best window: {picked['bars']} bars, score {picked['score']})" if picked else ''
    return (f"{tid}: {take} {start}-{end} s{why}, {m['frames']} frames, {m['seconds']} s, blend {m['loop_blend_s']} s; "
            'seam ' + '; '.join(f"{k} {v['jump_cm']} cm ({v['x_step']}x a step)" for k, v in sm.items()))


@op(mutates=True)
def stage_take_warp(scene: str, take: str, name: str, bpm: float, bars: int = 2, sub: float = 1.0) -> str:
    """Put a take's hits on the beat: a new silent take of exactly bars bars at bpm (a loop), its time warped
    piecewise-linearly so each hit (an accent: a hand or the head stopping or turning hard, at least 0.6 beat apart)
    lands on the nearest beat (sub=0.5: half beat), no segment under 0.75x or over 1.33x speed, resampled at 30 Hz
    with the frames between samples mixed. Cut the window first (stage_take_loop) so the take is about bars long
    (refused when more than 2x off). Hits and knots go in its meta.beat_warp. To keep it on the song as it plays:
    stage_actor_play(take=, loop=True, at_music=<the song second of its first frame>). Returns its id and where the
    hits landed in grid units."""
    from . import takesync
    src = _take_dir(scene, take)
    try:
        tid, m, bw = takesync.warp(src, name, float(bpm), bars=int(bars), sub=float(sub))
    except ValueError as e:
        raise OpError(str(e))
    snapped = len(bw['knots_src']) - 2
    return (f"{tid}: {take} -> {bars} bars at {bpm} BPM ({m['seconds']} s, {m['frames']} frames at 30 Hz); "
            f"{len(bw['hits_src_s'])} hits, {snapped} snapped; hits at grid units {bw['hits_on_grid_units']}")


@op(mutates=True)
def stage_actor_start(scene: str, person: str, pose: str | dict = 'rest', mode: str = 'relative', clear: bool = False,
                      idle: bool = True) -> str:
    """A person's start pose, saved in their actor's profile (actors/<body>.json "start"), used by every Follow and
    every playback of a take on them. mode 'relative': at GO (after the countdown) they hold the start pose and the
    user's motion plays as changes from the user's pose at GO (head and spine turn as the head turns, hands move as
    the wrists move, scaled to the body, fingers turn as the user's turn; hips and legs keep the pose); 'snap': they
    take the user's pose (as without a start pose). pose: 'rest' (the body's own rest pose, standing at the person's
    spot), {"take": id, "frame": n} (a frame of a recorded take), or a pose exported from Blender:
    {"bones": {name: {"rest": {"head", "tail", "x"}, "pose": {"head", "tail", "x"}}}}, Blender metres, rest in armature
    space (bone.head_local, bone.tail_local, bone.matrix_local x axis) and pose in world (armature.matrix_world @
    pose_bone.head / .tail / x axis of armature.matrix_world @ pose_bone.matrix), the pelvis at least. Each bone takes
    the world turn from its rest frame to its posed frame, the pelvis goes where the pose has it. clear=True removes
    it. idle=True (the default): the person also rests in it whenever nothing plays on them, at load and after every
    stop, instead of the statue baked into the scene; idle=False keeps the statue. stage_actor_pose reads the joints
    back. The page reads it at the next Follow or playback, and re-poses the resting person now if the page is open."""
    from . import rigs
    adir, who = _body_of(scene, person)
    if not (adir / f'{who}.glb').is_file():
        raise OpError(f'{person} is played by {who}, but there is no {adir.parent.name}/actors/{who}.glb')
    if clear:
        f = rigs.save_start(adir, who, None)
        _rest_now(scene, person)
        return f'removed the start pose of {who} (plays {person}) in {f}'
    try:
        start = rigs.check_start(pose, mode, idle)
    except ValueError as e:
        raise OpError(str(e))
    f = rigs.save_start(adir, who, start)
    rested = _rest_now(scene, person)
    what = 'rest' if start['pose'] == 'rest' else (f"take {start['pose']['take']} frame {start['pose']['frame']}"
                                                  if 'take' in start['pose'] else f"a Blender pose of {len(start['pose']['bones'])} bones")
    return f"start pose of {who} (plays {person}): {what}, {mode}{', rests in it' if idle else ''}; saved in {f}" + rested


def _rest_now(scene, person):
    """Re-pose a resting person on the open page (best effort: no page, no harm; it applies at the next load)."""
    try:
        from .link import page_cmd
        page_cmd(scene, 'actor_rest', {'person': person}, timeout=8)
        return '; the page shows it now'
    except Exception:                                      # noqa: BLE001  (no page open, or an older page)
        return '; the page shows it at its next load'


NOT_IN_BATCH = {'stage_batch', 'stage_start', 'stage_stop', 'stage_listen', 'stage_scene_export', 'stage_scene_new',
                'stage_scene_go'}


@op(mutates=True)
def stage_batch(scene: str, ops: list, stop_on_error: bool = True) -> str:
    """Many stage ops in one call, in order, on one scene (scene is implied): ops = [{"op": "stage_follow_anchor",
    "person": ..., "joint": "hips", "to": "stool_3"}, {"op": "stage_perform", "action": "mark", "label": "legs"}, ...].
    Ops that are page commands go to the page together as one command and run back to back there (the ones that
    answer at once land in the same frame); an op that runs here (stage_world, stage_events, stage_performance, ...)
    first sends the page commands before it, so the order holds. With stop_on_error (default) the first failure stops
    the batch: the scene folder's top-level .json files that the ops here changed (world.json and the like) go back
    to what they were; takes, performances and voice files are not put back, and what the page already did stays
    (the page cannot roll back; the reply says what ran). Not in a batch: stage_batch, stage_start,
    stage_stop, stage_listen, stage_scene_export, stage_scene_new, stage_scene_go. Replies one line per op:
    [i] op: its answer."""
    import inspect
    from ..api import OPS
    if not isinstance(ops, list) or not ops:
        raise OpError('ops is a list of {"op": "stage_...", ...fields}')
    specs = []
    for i, spec in enumerate(ops):
        name = spec.get('op') if isinstance(spec, dict) else None
        if not name or not name.startswith('stage_') or name not in OPS:
            raise OpError(f'op #{i}: {name!r} is not a stage op (stage_* only)')
        if name in NOT_IN_BATCH:
            raise OpError(f'op #{i}: {name} cannot be in a batch; call it on its own')
        kw = {k: v for k, v in spec.items() if k != 'op'}
        if 'scene' in inspect.signature(OPS[name]).parameters:
            if kw.get('scene', scene) != scene:
                raise OpError(f'op #{i}: a batch drives one scene ({scene!r}); it asked for {kw["scene"]!r}')
            kw['scene'] = scene
        on_page = OPS[name].__module__.endswith('ops_page') or name == 'stage_cmd'
        specs.append((i, name, kw, on_page))
    sdir = Path(link.server_for(scene)['scenes']) / scene
    changed = {}                                   # file -> its bytes before the batch, for files the ops here changed
    out, queued = [], []
    failed = None

    def flush():
        nonlocal failed
        cmds, t = link.collected()
        if not cmds:
            return
        link.done_collecting()
        try:
            e = link.send(scene, 'batch', {'cmds': cmds, 'stop_on_error': stop_on_error}, timeout=t + 30)
        finally:
            link.collecting(scene)
        if e.get('ok') is False:
            raise OpError(f'the page refused the batch: {e.get("error")}')
        res = e.get('result') or []
        for (i, name), r in zip(queued, res):
            try:
                out.append(f'[{i}] {name}: ' + link.reply(r.get('type'), r))
            except OpError as err:
                out.append(f'[{i}] {name}: ERROR {err}')
                failed = failed or (i, name)
        for i, name in queued[len(res):]:
            out.append(f'[{i}] {name}: not run (an op before it failed)')
        queued.clear()

    link.collecting(scene)
    try:
        for i, name, kw, on_page in specs:
            if not on_page:
                flush()
            if failed and stop_on_error:
                break
            before = None if on_page else {f: f.read_bytes() for f in sdir.glob('*.json')}

            def note_changes():                        # a file this op changed here, failed or not
                for f in set(before) | set(sdir.glob('*.json')):
                    if f not in changed and (not f.exists() or f.read_bytes() != before.get(f)):
                        changed[f] = before.get(f)    # None: the op made it
            try:
                r = OPS[name](**kw)
            except Exception as err:                   # any failure rolls back, not only OpError
                if before is not None:
                    note_changes()
                if not isinstance(err, OpError):
                    err = f'{type(err).__name__}: {err}'
                if stop_on_error:                  # nothing queued after the last send has run: it does not run now
                    out.extend(f'[{j}] {n}: not run (an op after it failed first)' for j, n in queued)
                    queued.clear()
                    link.collected()
                out.append(f'[{i}] {name}: ERROR {err}')
                failed = failed or (i, name)
                continue
            if on_page:
                queued.append((i, name))
                continue
            note_changes()
            out.append(f'[{i}] {name}: {r}')
        if not (failed and stop_on_error):
            flush()
    finally:
        link.done_collecting()
    if failed and stop_on_error:
        for f, b in changed.items():                   # back to before the batch (a file it made goes again)
            if b is None:
                f.unlink(missing_ok=True)
            else:
                f.write_bytes(b)
        raise OpError('\n'.join(sorted(out, key=_batch_index)) + f'\nop #{failed[0]} ({failed[1]}) failed; the batch stopped there.'
                      + (f' Put back: {", ".join(f.name for f in changed)}.' if changed else ''))
    return '\n'.join(sorted(out, key=_batch_index))


def _batch_index(line):
    return int(line[1:line.index(']')])


@op(mutates=True)
def stage_capture(scene: str, t0: float, t1: float, camera: str = None, shot: str = None, cameras: str = None,
                  path: dict = None, view: dict = None, fps: int = 30, size: list = None, clock_at: float = None,
                  clock: bool = True, setup: list = None, audio: str = None, video: bool = True,
                  keep_frames: bool = False, sets: bool = True, scenes: str = None, name: str = None) -> str:
    """Capture the stage on the PC, frame-locked: frame n shows the song at t0 + n / fps (takes and loops, keyed
    objects, tree growth, lights and behaviours all at that time), however long a frame takes to draw; nothing drops.
    Runs in the background with no headset and without touching the stage the person uses (its own server and a
    headless browser; nothing it does is saved to the scene or reaches the live link), on the GPU slot. Replies at
    once with the job; stage_capture_status(scene, job) follows it.
    The camera, one of: camera= a camera object in the scene (keyed ones fly their keys on the stage clock);
    shot= a story path from cameras.json (cameras= its file; keys evenly spaced, eased in and out over the shot's
    seconds from t0); path= {"keys": [{"pos", "look", "lens"}], "seconds", "at"} the same inline; view= {"pos",
    "fwd", "up", "vfov"} fixed. All in Blender coordinates (metres, Z up), lens in mm on a 36 mm sensor; "vfov"
    (degrees) in path or view overrides the lens.
    t0, t1: song seconds. size [w, h] (default [1920, 1080]; [1440, 1080] for 4:3). The stage clock is set to
    clock_at + (song t - t0) every frame (clock_at defaults to t0; clock=False leaves it alone). setup: live commands
    run once before the first frame, e.g. [{"type": "actor_play", "person": "body_a", "take": "t1", "at_music": 0},
    {"type": "sky", "mode": "night"}] (per-shot overrides; the scene files are not changed). Every load set is in
    the shot unless sets=False (setup can unload one: {"type": "load_set", "name": ..., "loaded": false}).
    audio: the song file, cut to t0..t1 under the video. Out: <scene>/captures/<job>/capture.mp4 (H.264, 4:2:0), frames/f_00000.png with
    keep_frames (or video=False)."""
    import re
    import uuid
    d = (Path(scenes).resolve() / scene) if scenes else _scene_dir(scene)
    if not (d / 'scene.glb').is_file():
        raise OpError(f'no scene {scene!r} in {d.parent}')
    if not t1 > t0 >= 0:
        raise OpError('t0 and t1 are song seconds with t1 > t0 >= 0')
    size = list(size or [1920, 1080])
    if len(size) != 2 or not all(isinstance(v, int) and 16 <= v <= 4096 for v in size) or any(v % 2 for v in size):
        raise OpError('size is [width, height] in even pixels, 16 to 4096')
    if not 1 <= int(fps) <= 120:
        raise OpError('fps is 1 to 120')
    given = [k for k, v in (('camera', camera), ('shot', shot), ('path', path), ('view', view)) if v]
    if len(given) != 1:
        raise OpError('give one camera: camera=, shot= (with cameras=), path= or view=' + (f' (got {given})' if given else ''))
    if camera:
        cam = {'object': camera}
    elif shot:
        if not cameras or not Path(cameras).is_file():
            raise OpError('shot= needs cameras= (the cameras.json file)')
        shots = json.loads(Path(cameras).read_text(encoding='utf-8')).get('shots', [])
        s = next((x for x in shots if x.get('id') == shot), None)
        if not s:
            raise OpError(f'no shot {shot!r} in {cameras}; shots: {", ".join(x.get("id", "?") for x in shots)}')
        cam = {'path': {'keys': s['keys'], 'seconds': float(s['seconds']), 'at': float(t0)}}
    elif path:
        if not path.get('keys') or not path.get('seconds'):
            raise OpError('path is {"keys": [{"pos", "look", "lens"}, ...], "seconds", "at"?}')
        cam = {'path': {'at': float(t0), **path}}
        if path.get('vfov'):
            cam['vfov'] = path['vfov']
    else:
        if not view.get('pos') or not view.get('fwd'):
            raise OpError('view is {"pos": [x, y, z], "fwd": [x, y, z], "up"?, "vfov"?} in Blender coordinates')
        cam = {k: view[k] for k in ('pos', 'fwd', 'up', 'vfov') if view.get(k) is not None}
    if audio and not Path(audio).is_file():
        raise OpError(f'no audio file {audio}')
    for c in setup or []:
        if not isinstance(c, dict) or not c.get('type'):
            raise OpError('setup is a list of live commands: {"type": "actor_play", ...}')
    job_id = name or time.strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:4]
    if not re.match(r'^[A-Za-z0-9_\-]+$', job_id):
        raise OpError('name: letters, digits, _ and - only')
    jd = d / 'captures' / job_id
    if jd.exists():
        raise OpError(f'a capture {job_id!r} is already there ({jd}); pass another name')
    from . import capture
    if not capture.browser():
        raise OpError('no Edge or Chrome to draw with (set ISMAIL_BROWSER to one)')
    if video and not capture.ffmpeg():
        raise OpError('no ffmpeg (on PATH or ISMAIL_FFMPEG); or video=False with keep_frames')
    jd.mkdir(parents=True)
    job = {'id': job_id, 'scene': scene, 'scenes': str(d.parent), 't0': float(t0), 't1': float(t1), 'fps': int(fps),
           'size': size, 'camera': cam, 'clock': bool(clock), 'clock_at': float(t0 if clock_at is None else clock_at),
           'setup': setup or [], 'audio': str(Path(audio).resolve()) if audio else None, 'video': bool(video),
           'keep_frames': bool(keep_frames or not video), 'sets': bool(sets), 'made': time.strftime('%Y-%m-%d %H:%M:%S')}
    capture.write_json(jd / 'job.json', job)
    capture.write_json(jd / 'status.json', {'job': job_id, 'scene': scene, 'state': 'starting'})
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2]) + os.pathsep + os.environ.get('PYTHONPATH', '')}
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if os.name == 'nt' else 0
    subprocess.Popen([sys.executable, '-m', 'ismail.stage.capture', str(jd / 'job.json')], env=env,
                     stdout=open(jd / 'worker.txt', 'w'), stderr=subprocess.STDOUT, creationflags=flags,
                     start_new_session=os.name != 'nt')
    frames = int(round((t1 - t0) * fps))
    return (f'capture {job_id}: {scene}, song {t0:g} to {t1:g} s, {frames} frames at {fps} fps, {size[0]}x{size[1]}; '
            f'started in the background (stage_capture_status(scene={scene!r}, job={job_id!r})). Out: {jd}')


@op()
def stage_capture_status(scene: str, job: str = None, scenes: str = None) -> str:
    """Where a capture is (stage_capture): its state (waiting for the GPU, loading the room, rendering, done,
    failed), frames drawn of all, frames a second, time left, the video when done and the page's errors. Without
    job: the scene's captures, newest first."""
    d = ((Path(scenes).resolve() / scene) if scenes else _scene_dir(scene)) / 'captures'
    if not job:
        js = sorted(d.glob('*/status.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        if not js:
            return f'no captures in {d}'
        return '\n'.join(_capture_line(json.loads(p.read_text(encoding='utf-8'))) for p in js)
    f = d / job / 'status.json'
    if not f.is_file():
        raise OpError(f'no capture {job!r} in {d}')
    return json.dumps(json.loads(f.read_text(encoding='utf-8')), indent=1)


def _capture_line(s):
    bits = [s.get('job', '?'), s.get('state', '?'), f"{s.get('frame', 0)}/{s.get('frames', '?')} frames"]
    if s.get('eta_s') is not None and s.get('state') == 'rendering':
        bits.append(f"about {s['eta_s']} s left")
    if s.get('video'):
        bits.append(s['video'])
    if s.get('error'):
        bits.append('error: ' + str(s['error'])[:200])
    return ', '.join(bits)
