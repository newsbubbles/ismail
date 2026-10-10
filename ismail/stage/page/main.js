// The stage's entry (was inline in index.html): served as bundle.js, one file, built by server.py with esbuild
// (build_bundle.mjs). The modules still load one by one when the bundle cannot be built.
import * as THREE from 'three';
import { Editor } from './editor.js';
import { initDesktop } from './desktop.js';
import { initXR } from './xr.js';
import { initLive } from './live.js';
import { initHands } from './hands.js';
import { initVoice } from './voice.js';
import { initTouch } from './touch.js';
import { initUpdates } from './updates.js';
import { initExit } from './exit.js';
import { initPanels } from './panels.js';
import { initBody } from './body.js';
import { initActions } from './actions.js';
import { initTakes4D } from './takes4d.js';
import { initActors } from './actors.js';
import { initGallery } from './gallery.js';
import { initConstruct } from './construct.js';
import { initMusic } from './music.js';
import { initStream } from './stream.js';
import { initReveal } from './reveal.js';
import { initClock } from './clock.js';
import { initTrees } from './trees.js';
import { initScenes } from './scenes.js';
import { initWaypoints } from './waypoints.js';
import { initCues } from './cues.js';
import { initAnchors } from './anchor.js';
import { initPerform } from './perform.js';
import { loadWorld } from './world.js';
import { initLoadSets } from './loadsets.js';
import { initBehaviours } from './behaviours.js';
import { setPickEmit } from './pickcycle.js';
import { CAPTURE, runCapture } from './capture.js';

// the scene: ?scene=, else the server's default (scenes/stage.json "default", else its first scene)
const name = new URLSearchParams(location.search).get('scene')
  || await fetch('stage', { cache: 'no-store' }).then((r) => r.json()).then((j) => j.default).catch(() => null) || '';
await loadWorld(name);
document.getElementById('scenename').textContent = name;
const ed = new Editor(name);
initReveal(ed);                                    // everything enters a piece at a time (reveal.js)
// shadows are drawn when something moved: while pieces come in, while someone plays or follows, after an edit, and
// once a second regardless (a lamp swung by hand, a door)
{
  let dirty = true, n = 0;
  for (const t of ['change', 'revealed', 'reloaded', 'switched', 'select']) ed.addEventListener(t, () => { dirty = true; });
  ed.preRender.push(function shadows() {
    const moving = ed.staging() || (window.VR_actors && window.VR_actors.playing.size > 0) || (ed.unlocked && ed.selected);
    if (dirty || moving || ++n % 72 === 0) { ed.renderer.shadowMap.needsUpdate = true; dirty = false; window.VR_shadowFrames = (window.VR_shadowFrames || 0) + 1; }
  });
}
// the page starts in the construct (construct.js): VR, voice and the live link work at once, the scene streams in
const construct = initConstruct(ed);
ed.addEventListener('building', () => construct.building());
const desktop = initDesktop(ed);
const xr = initXR(ed, desktop);
const live = initLive(ed, desktop, xr);
const hands = initHands(ed, xr, live.emit);
const voice = initVoice(ed, hands, live);
const touch = initTouch(ed, xr, hands, live.emit);
setPickEmit(live.emit);   // pickcycle.js: pick_cycle events when a pinch again takes the next thing under it
const updates = initUpdates(ed, live, hands, voice);
const exitVR = initExit(ed, hands, live);
const body = initBody(ed, hands);                  // which way the user's body faces (body-anchored panels)
const panels = initPanels(ed, xr, hands, voice, live, body);
const gallery = initGallery(ed, hands, voice, panels, live);
voice.shotHooks.gallery = gallery;
live.handlers.gallery_add = (c) => {               // open: true also opens the gallery on it (the user: "make the gallery go to that render")
  const r = gallery.add(c.url);
  if (c.open) setTimeout(() => gallery.open(gallery.shots.indexOf(c.url)), 600);
  return r;
};
live.handlers.ack = (c) => { const fresh = !c.ts || Date.now() - Date.parse(c.ts) < 10000; if (fresh) voice.EAR.ack(); return { played: fresh }; };
const takes4d = initTakes4D(ed, live);
live.handlers.take_view = (c) => takes4d.show(c);
live.handlers.take_view_clear = () => takes4d.clear();
const actors = initActors(ed, live);
actors.setSource(() => hands.frameNow());   // live follow: the user's body this moment
const loadSets = initLoadSets(ed, live, () => actors);   // named sets unloaded to keep the Quest light (loadsets.js)
window.VR_sets = loadSets;
actors.setUnloaded((p) => loadSets.isUnloadedPerson(p));
// countdown: seconds to count before it starts, so the user can take the pose first (the menu's Follow counts 3)
// an agent's Follow opens the Follow panel too (Stop, Mic off): one an agent began had none, and the user was stuck
// inside its performance for seven minutes (2026-10-05)
live.handlers.actor_follow = async (c) => {
  if (c.countdown) await actions.countdown(c.countdown, c.person);
  const r = await actors.follow(c);
  const it = ed.byName.get(c.person);
  if (it) actions.followPanel(it);
  return r;
};
const perform = initPerform(ed, hands, voice, live, () => actors);   // a Follow is a performance (perform.js)
window.VR_perform = perform;
xr.setPerforming(() => hands.performing);
const music = initMusic(ed, live);
window.VR_music = music;
actors.setMusicClock(() => music.now());            // takes played on the music (stage_actor_play at_music)
initStream(ed, live);
// a re-exported room comes in without the grown trees (they hang under the old scene's tree_k items): grow them again
live.onEmit((type) => { if (type === 'scene_reload' && live.handlers.trees_reload) setTimeout(() => live.handlers.trees_reload({}).catch(() => {}), 500); });
const stageClock = initClock(ed, live, xr, panels);   // the 4D timeline: keyed objects, tree growth
window.VR_clock = stageClock;
window.VR_trees = initTrees(ed, live, stageClock);   // grow_tree.py's trees, grown on the clock
fetch(`scenes/${encodeURIComponent(name)}/names.json`, { cache: 'no-store' })
  .then((r) => (r.ok ? r.json() : {})).then((n) => { ed.names = n; }).catch(() => {});
ed.preRender.push(() => { if (actors.playing.size) wake(); });   // a playing actor keeps the desktop view drawing
live.handlers.actor_play = (c) => actors.play(c);
live.handlers.actor_stop = (c) => actors.stop(c);
window.VR_actors = actors;
// scenes from inside: go to another scene, or take a re-export of this one, under the construct (scenes.js)
window.VR_scenes = initScenes(ed, live, xr, construct, { get actors() { return actors; }, get view() { return takes4d; } });
ed.loadWorld = loadWorld;                          // a scene switch reads the new world before staging (editor.js)
ed.addEventListener('switched', () => fetch(`scenes/${encodeURIComponent(ed.sceneName)}/names.json`, { cache: 'no-store' })
  .then((r) => (r.ok ? r.json() : {})).then((n) => { ed.names = n; }).catch(() => { ed.names = {}; }));
panels.registerPokeable(xr.panelMesh, (uv) => xr.pressUV(uv), (uv) => xr.hoverUV(uv));   // the colour panel takes a fingertip too
live.handlers.panel = (c) => panels.show(c);
live.handlers.panel_close = (c) => panels.close(c.panel_id, 'closed by Claude');
// a take during a performance takes its voice from the performance (its clips, linked in the take's meta);
// otherwise the mic records the whole take
// the pins a take was made with go in its meta, so it plays back seated wherever it plays (actors.js play)
// ... and the Follow's mirror, so it plays back the way it was made
const takeMeta = (id, body) => fetch(`take/meta?scene=${encodeURIComponent(ed.sceneName)}&take=${encodeURIComponent(id)}`, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => {});
const pinMeta = (person, id) => {
  if (!person || !id) return;
  const pins = actors.pinsMeta(person);
  takeMeta(id, { ...(pins ? { pins } : {}), mirror: actors.mirrorOf(person) });
};
live.onEmit((type, d) => {                         // the mirror turned on or off while a take records on that person
  if (type === 'actor_mirror' && d && hands.rec.on && hands.rec.id && hands.rec.name === d.person) takeMeta(hands.rec.id, { mirror: !!d.mirror });
});
const keepLast = (n) => hands.keepLast(n).then((r) => { pinMeta(r.person, r.id); return r; });
const startTake = (name) => {
  const r = hands.startTake(name);
  if (r && !r.already) {
    pinMeta(name, r.id);
    const link = perform.attachTake(r.id, hands.rec.t0);
    if (link) fetch(`take/meta?scene=${encodeURIComponent(ed.sceneName)}&take=${encodeURIComponent(r.id)}`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(link) }).catch(() => {});
    else voice.takeAudioStart(r.id);
  }
  return r;
};
const stopTake = async () => { const r = await hands.stopTake(); if (r && r.id) voice.takeAudioStop(); return r; };
// a performance's way out (perform.js stopAll): the take recording with it, then the Follow (whose actor_stop ends the
// performance below)
perform.setStopAll(async (person, by) => {
  const take = hands.rec.on ? await stopTake().catch(() => null) : null;
  actors.stop({ person, why: 'stopped: ' + by });
  return { stopped: person, by, take: take && take.id ? take.id : null };
});
live.handlers.take_start = (c) => startTake(c.name || '');
xr.setHandState(hands.state);
// every Follow is a performance (perform.js) and is buffered in memory on the performance's clock (hands.js); after
// it stops the user (actions.js) or an agent can keep it as a take
live.onEmit((type, d) => {
  if (type === 'actor_follow' && d) { const p = perform.start(d.person); hands.shadowStart(d.person, p.t0, p); }
  else if (type === 'actor_stop' && d && d.live) { hands.shadowStop(); perform.stop(); }
});
live.handlers.take_keep_last = (c) => keepLast(c.name || null);
// things that do things: a scene's own small functions, run on a press, a menu item or a wire (behaviours.js)
window.VR_behaviours = initBehaviours(ed, live, panels);
const actions = initActions(ed, hands, panels, live, { start: (n) => startTake(n), stop: () => stopTake(), recording: () => hands.rec.on,
  keepLast: (n) => keepLast(n), discardLast: () => hands.discardLast(), lastFollow: () => hands.lastFollowInfo(),
  lastFollowData: () => hands.lastFollowData(), perform,
  get actors() { return actors; }, get view() { return takes4d; }, get ear() { return voice.EAR; }, get clock() { return stageClock; } });   // defined below; used on a menu press
live.handlers.take_stop = () => stopTake();
const sayText = live.handlers.say;                 // in VR there is no caption to read: speak it (or with --voice)
// a line queued while the page was away is old news by the time it connects: caption it, do not speak it (the user
// heard five queued lines arrive on top of each other)
live.handlers.say = async (c) => {
  const r = sayText(c);
  const age = c.ts ? Date.now() - Date.parse(c.ts) : 0;
  // performing: the line would be in the recording, so it is shown and not spoken unless said aloud on purpose
  const held = hands.performing && !c.aloud;
  if ((ed.renderer.xr.isPresenting || c.voice) && age < 20000 && !held) voice.speak(c.text, c.voice_name);
  // in VR the line is also shown, for a few seconds, as a caption panel: speech can take 30 s or more to render when
  // the machine is busy, and the user, 2026-10-03: "I still can't hear you, you haven't told me anything yet"
  if (ed.renderer.xr.isPresenting && age < 60000) {
    const words = String(c.text).split(/\s+/).length;
    // long enough to read and to still be there when the speech arrives (the user: "I didn't catch that last card")
    // a message, so it rides with the user on its sender's side (the user, 2026-10-04: "it kind of needs to stick to
    // me"), and says who it is from
    panels.show({ panel_id: 'caption_' + Date.now(), title: c.from ? '' : 'Claude', from: c.from, text: String(c.text),
      seconds: Math.min(120, 25 + words * 0.8), quiet: true, wait: false, anchor: 'body', side: c.side });
  }
  if (held) return { ...r, spoken: false, held: 'performing: shown, not spoken (aloud=True speaks it into the recording)' };
  return age >= 20000 ? { ...r, spoken: false, stale_s: Math.round(age / 1000) } : r;
};
// Claude moves the user in VR too: stand at `position` (Blender xyz, on the floor below it), facing `target`, with a
// whoosh (the user, 2026-10-03: "take me over to a tree... I also want to test like when you move me, what happens")
const gotoDesk = live.handlers.goto;
live.handlers.goto = async (c) => {
  if (!ed.renderer.xr.isPresenting) return gotoDesk(c);
  const P = (v) => new THREE.Vector3(v[0], v[2], -v[1]);
  const p = P(c.position), t = P(c.target), rig = ed.rig, Y = new THREE.Vector3(0, 1, 0);
  const hy = new THREE.Euler().setFromQuaternion(ed.camera.getWorldQuaternion(new THREE.Quaternion()), 'YXZ').y;
  const want = Math.atan2(-(t.x - p.x), -(t.z - p.z));
  rig.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(Y, want - hy));
  rig.updateMatrixWorld(true);
  const head = ed.camera.getWorldPosition(new THREE.Vector3());
  rig.position.x += p.x - head.x; rig.position.z += p.z - head.z;
  const f = desktop.floorBelow(p.clone().setY(p.y + 0.3));   // from just above the eyes: a roof above them is not a floor
  if (f !== null) rig.position.y = f;
  voice.EAR.sent();
  return { arrived: true, xr: true, floor: f };
};
const snapDesk = live.handlers.snapshot;
live.handlers.snapshot = async (c) => (ed.renderer.xr.isPresenting ? voice.eyeSnapshot() : snapDesk(c));
live.handlers.eyecam = (c) => voice.eyecam(c.fps || 1, c.seconds || 10);
live.handlers.ask = (c) => voice.ask(c.text, c.seconds || 60, c.from);
live.handlers.voice_rec = (c) => (c.action === 'stop' ? voice.noteStop() : voice.noteStart('claude'));
xr.setRec(() => { (hands.rec.on ? stopTake() : Promise.resolve(startTake(''))).then(() => xr.redraw()); }, () => hands.rec.on);
const clock = new THREE.Clock();
// Draw on demand on the desktop: an idle stage left open in a browser kept the laptop's GPU busy all day (2026-10-02).
// A frame is drawn while the view camera moves, for 1.5 s after any input or editor event, and once a second
// otherwise (slow pulses, live-link markers). XR always draws every frame.
let awakeUntil = 0, lastDraw = 0, lastPose = '';
const wake = () => { awakeUntil = performance.now() + 1500; };
for (const t of ['pointerdown', 'pointermove', 'wheel', 'keydown', 'keyup', 'resize', 'focus']) addEventListener(t, wake, { passive: true });
for (const t of ['change', 'reloaded', 'select', 'mode', 'saved', 'undone']) ed.addEventListener(t, wake);
// Each part of the frame is guarded: in the headset one module that throws every frame used to stop the frame being
// drawn at all, which the Quest shows as black with no hands (2026-10-03). Now that module is skipped for the frame,
// the error goes to the server (index.html's reporter), and the frame is still drawn.
const guard = (what, f) => { try { return f(); } catch (e) { console.error(`[xr] ${what}:`, e); } };
let xrFrames = 0;
ed.renderer.xr.addEventListener('sessionstart', () => {
  xrFrames = 0; console.log('[xr] session started');
  setTimeout(() => live.emit('headset', { state: 'entered VR' }), 3000);   // cues can greet the user (cues.js)
});
ed.renderer.xr.addEventListener('sessionend', () => console.log(`[xr] session ended after ${xrFrames} frames`));
ed.renderer.setAnimationLoop(() => {
  const dt = Math.min(clock.getDelta(), 0.1);
  guard('xr', () => xr.update(dt));
  if (ed.renderer.xr.isPresenting) {
    guard('hands', () => hands.update()); guard('touch', () => touch.update()); guard('voice', () => voice.update());
    guard('body', () => body.update());
    if (!guard('exitVR', () => exitVR.update())) { guard('updates', () => updates.update()); guard('panels', () => panels.update()); }
    guard('gallery', () => gallery.update()); guard('actions', () => actions.update());
  }
  if (ed.selected) guard('outline', () => ed.outline.update());
  for (const f of ed.preRender) guard(f.name || 'preRender', f);
  if (ed.renderer.xr.isPresenting) {
    guard('render', () => ed.renderer.render(ed.scene, ed.camera));
    if (++xrFrames === 90) console.log(`[xr] 90 frames drawn; construct ${construct.active ? 'up' : 'gone'}, room ${ed.loaded ? 'loaded' : 'loading'}`);
    return;
  }
  const now = performance.now(), cam = desktop.viewCamera();
  const pose = cam.matrixWorld.elements.map((x) => x.toFixed(5)).join(',');
  if (pose !== lastPose) { lastPose = pose; wake(); }
  if (now < awakeUntil || now - lastDraw > 1000) { lastDraw = now; desktop.render(); }
});

window.VR = {
  ed, desktop, xr, live, THREE, body, panels, hands, voice,
  selftest: () => ed.selftest(),
  save: () => ed.save(),
  undo: () => ed.undo(),
  edits: () => ed.computeEdits(),
  select: (n) => ed.select(n),
  lookThrough: (n) => desktop.lookThrough(n ? ed.byName.get(n) : null),
  // move an object by a Blender-space offset (metres), as one undoable edit
  moveBlender(n, [dx, dy, dz]) {
    const it = ed.byName.get(n);
    ed.beginEdit();
    it.obj.position.add(new THREE.Vector3(dx, dz, -dy));
    ed.endEdit();
    return ed.blenderTransform(it);
  },
  // rotate an object about a Blender world axis through its own origin
  rotateBlenderWorld(n, [ax, ay, az], deg) {
    const it = ed.byName.get(n);
    ed.beginEdit();
    it.obj.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(ax, az, -ay).normalize(), THREE.MathUtils.degToRad(deg)));
    ed.endEdit();
    return ed.blenderTransform(it);
  },
};
// auto-save: 20 s after the last change (never mid-drag), and whenever the tab is hidden or closed
const AUTOSAVE_IDLE_MS = 20000;
let autosaveTimer = null;
function scheduleAutosave() {
  clearTimeout(autosaveTimer);
  if (!ed.dirty()) return;
  autosaveTimer = setTimeout(() => {
    if (ed.editing) return scheduleAutosave();
    if (ed.dirty()) ed.save().then(() => ed.setStatus('auto-saved ' + new Date().toLocaleTimeString())).catch(() => {});
  }, AUTOSAVE_IDLE_MS);
}
ed.addEventListener('change', scheduleAutosave);
function flushSave() {
  if (!ed.dirty()) return;
  const body = new Blob([JSON.stringify(ed.computeEdits())], { type: 'application/json' });
  if (navigator.sendBeacon(`save?scene=${encodeURIComponent(name)}`, body)) ed.savedJSON = JSON.stringify(ed.computeEdits());
}
addEventListener('pagehide', flushSave);
document.addEventListener('visibilitychange', () => { if (document.hidden) flushSave(); });

// a heartbeat to the server every 5 s (clientlog.jsonl, not printed): a page that dies (the Quest's tab, 2026-10-03,
// went silent the moment the room was in) leaves its last beat: memory, GPU objects, frames, VR or not
let beatFrames = 0;
// frame pacing for the beat: the worst frame and how many ran long in the last 5 s (comfort is measured, not guessed)
// where the head was and what was drawn in the worst frame (a stall is read against the view: what was in it, which
// way they looked), in Blender coordinates (yaw 0 looks along +Y, 90 along +X); info.render still holds the frame that
// just ran long
const fp = { last: 0, worst: 0, long: 0, n: 0, at: null };
const headAt = () => {
  const p = ed.camera.getWorldPosition(new THREE.Vector3()), f = ed.camera.getWorldDirection(new THREE.Vector3());
  return { pos: [+p.x.toFixed(2), +(-p.z).toFixed(2), +p.y.toFixed(2)],
    yaw: Math.round(THREE.MathUtils.radToDeg(Math.atan2(f.x, -f.z))), pitch: Math.round(THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(f.y, -1, 1)))) };
};
ed.preRender.push(function framePace() {
  const t = performance.now(), d = fp.last ? t - fp.last : 0;
  fp.last = t; fp.n++;
  if (d > fp.worst) { fp.worst = d; fp.at = { ...headAt(), tris: ed.renderer.info.render.triangles, calls: ed.renderer.info.render.calls }; }
  if (d > 20) fp.long++;
});
setInterval(() => {
  if (!window.VR_log) return;
  const m = performance.memory, inf = ed.renderer.info;
  const pace = { frames: fp.n, worstMs: Math.round(fp.worst), longFrames: fp.long, programs: inf.programs ? inf.programs.length : null,
    staging: ed.staging ? ed.staging() : false, head: headAt(), worstAt: fp.at,
    // what the frame cost comes from: frames that re-drew the shadow maps (a point light's is six renders of every
    // caster; they re-draw while anyone plays or follows), and how many people were playing
    shadowFrames: window.VR_shadowFrames || 0, playing: window.VR_actors ? window.VR_actors.playing.size : 0,
    eyes: ed.renderer.xr.isPresenting ? 2 : 1 };
  fp.worst = 0; fp.long = 0; fp.n = 0; fp.at = null; window.VR_shadowFrames = 0;
  window.VR_log('beat', JSON.stringify({ heapMB: m ? Math.round(m.usedJSHeapSize / 1e6) : null, limitMB: m ? Math.round(m.jsHeapSizeLimit / 1e6) : null,
    geo: inf.memory.geometries, tex: inf.memory.textures, tris: inf.render.triangles, calls: inf.render.calls,
    xr: ed.renderer.xr.isPresenting, xrFrames, loaded: ed.loaded, construct: construct.active, ...pace }));
}, 5000);
live.handlers.sky = (c) => ed.setSky(c.mode || 'scene');
// pins with notes and TODOs, both ways, kept per scene (waypoints.js)
const waypoints = initWaypoints(ed, live, panels, hands, voice);
window.VR_waypoints = waypoints;
// menus Claude attaches ahead of time to the user's actions, opened where they happen (cues.js)
window.VR_cues = initCues(ed, live, panels);
window.VR_anchors = initAnchors(ed, xr, live);   // objects pinned to a hand joint, session only
live.emit('page_boot', { scene: name, in_construct: true });
for (let n = 1; !ed.loaded; n++) {                 // keep trying: the user can stand and talk in the construct meanwhile
  try {
    await ed.load();
  } catch (e) {
    const msg = `could not load "${name}" (${e.message || e}); trying again`;
    document.getElementById('loading').textContent = msg;
    construct.failed(msg);
    live.emit('voice_error', { where: 'scene load', error: String(e.message || e), attempt: n });
    await new Promise((r) => setTimeout(r, Math.min(15000, 2000 * n)));
  }
}
document.getElementById('loading').remove();
construct.dissolve();
wake();
const st0 = ed.selftest();
console.log('[vr] ready:', ed.items.length, 'objects; selftest on load:', st0.pass ? 'PASS' : 'FAIL');
live.pageLoaded(st0);
if (CAPTURE) runCapture(ed, { music, clock: stageClock, construct, live, actors, sets: loadSets });   // a frame-locked capture (capture.js)
