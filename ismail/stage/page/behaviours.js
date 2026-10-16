// Things that do things: each scene's behaviours.js (scenes/<scene>/behaviours.js) gives objects in the room their own
// small functions, written by an agent and run here, on a press, a menu item or a wire from another object, with no
// agent in the loop (the user, 2026-10-08: "I hit the switch and directly ... the film agent was able to put code
// into the switch that controls other objects"; spending tokens on every press, or saving the work as deterministic
// code). The file reloads with the scene and on stage_behaviours(reload=True); agents get the same calls
// (stage_behaviour_run), see what is defined and what failed (stage_behaviours) and read or set state
// (stage_behaviour_state). Every interaction sounds (the user: audio feedback is the standard): the object's own
// `sound`, else a soft click and a warning to the agent that wrote it.
//
// The file (plain JavaScript, no imports; `s` is the object's handle on the stage, below):
//   export default {
//     wall_switch: {
//       sound: 'sounds/switch.wav',                  // under scenes/<scene>/; every press plays it, at the object
//       volume: 0.8,                                 // of that sound (0 to 1)
//       state: { on: false },                        // its starting state; saved per scene as it changes
//       apply(s) {                                   // state -> room, run at load and after every change of state
//         const on = s.get('on');
//         s.light(['ceiling_lamp', 'desk_lamp'], { energy: on ? [300, 60] : 0, seconds: 0.4 });
//         s.light('accent_*', { energy: on ? 0 : null });            // null: back to how the scene was built
//       },
//       press(s) { s.set('on', !s.get('on')); },      // a quick pinch or trigger on it in VR, or stage_behaviour_run
//       menu: { 'On': (s) => s.set('on', true), 'Off': (s) => s.set('on', false) },   // a held pinch (or no press)
//       inputs: { power(s, on) { s.set('on', !!on); } },           // what other objects can send it (s.send)
//     },
//   };
// s: me, get(key?), set(key, value | {..}), state(object, key?), send(object, input, value), light(names, {energy,
// color, seconds}), show(names, on), move(name, {by, to, turn, seconds}), sound(file, {at, volume}), emit(type, data),
// do(command, fields), after(seconds, fn), every(seconds, fn), clock() -> {t, playing}, where(target), place(name,
// {location, rotation, look}), follow(target, {...}). Names take a list or a glob (accent_*). Positions are Blender
// metres, z up; turn and rotation are [x, y, z] degrees (Blender's XYZ Euler; turn is about the object's own origin).
//
// Following (the user, 2026-10-16: a camera that soft-follows an object, a person's joint or the user, for a chase,
// a drone shot, fast action, its path baked into keys for the Blender render). A target is an object's name,
// 'person' (the user's eyes: the headset in VR, the view on a desktop) or '<person>:<bone>' (a joint of a body the
// scene shows: runner:head, runner:hand_r, runner:pelvis; the error lists the bones).
//   s.where(target) -> {location, rotation, quaternion, forward, facing}: forward is where it looks, facing the same
//     on the floor (a body: where its hips face).
//   s.place(name, {location, rotation | quaternion, look}) sets a pose at once, no tween (for per-frame code).
//   s.follow(target, {offset: [right, forward, up] metres from the target (space 'target', the default: it turns with
//     where the target faces) or [x, y, z] (space 'world'), lag: seconds to close most of the gap (0.5; 0 = locked
//     on), look: true (at the target; the default for cameras and lights) | false | a target | [x, y, z],
//     look_offset: [x, y, z] added to the aim point, look_lag (0.25), orbit: degrees a second around the target (a
//     drone circling), object: who follows (default s.me), snap: start on the spot}) moves the object every frame
//     until s.follow(null). While it follows, its keys on the clock wait. Live only: bake it with cam_record source
//     'follow' (stage_camera_record, or ⏺ Record move on a following camera), which plays the clock, samples the
//     path into keys and stops the follow so the keys play; Keep saves them to anim.json, which the render reads.
//   frame(s, dt) in an object's definition runs every frame (dt in seconds); its first error stops it and reports.
//   A chase cam: chase_cam: { sound: 'sounds/beep.wav', state: { on: true },
//     apply(s) { s.follow(s.get('on') ? 'runner:pelvis' : null, { offset: [0, -3, 1.4], lag: 0.6, look_offset: [0, 0, 0.5] }); },
//     menu: { 'Follow': (s) => s.set('on', true), 'Stop': (s) => s.set('on', false) } }
//
// Errors (the user, 2026-10-08, after a touch did nothing: "you should have an event hook on object functionality
// errors"): every one goes to the agents as behaviour_error {object, phase, action, error, line}, load errors too, and
// a person's press that fails buzzes and says so at the object, so a broken switch never passes for one that is off.
import * as THREE from 'three';
import { listenerOf } from './stream.js';
import { t2bPos, t2bQuat, b2tQuat } from './editor.js';

const PRESS_GAP_MS = 350;              // one press per object at most this often (a held pinch re-presses)
// a thing with both a press and a menu: a quick pinch presses, a pinch held this long opens its menu instead (the user,
// 2026-10-08: pinching the orb ran its press AND opened a menu, and every pinch stacked another one)
const HOLD_FOR_MENU_MS = 450;
const ACTIONS = ['press', 'apply'];

export function initBehaviours(ed, live, panels, hands = null, { clock = null, actors = null } = {}) {
  let defs = {}, states = {}, report = { file: false, objects: [], errors: [], warnings: [] };
  let timers = [], anims = [], saveTimer = null, gen = 0;
  const lastPress = new Map(), buffers = new Map(), broken = new Map();   // broken: object -> why its code is not running
  let listener = null, modUrl = null;
  const scn = () => ed.sceneName;
  const base = () => `scenes/${encodeURIComponent(scn())}/`;
  const b2t = (v) => new THREE.Vector3(v[0], v[2], -v[1]);           // Blender (z up) to three (y up)

  // ---- names: one, a list, or a glob
  function items(names) {
    const out = [];
    for (const n of [].concat(names)) {
      if (typeof n !== 'string') continue;
      if (n.includes('*')) {
        const re = new RegExp('^' + n.split('*').map((x) => x.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
        for (const [k, it] of ed.byName) if (re.test(k)) out.push(it);
      } else {
        const it = ed.byName.get(n);
        if (!it) throw new Error(`no object "${n}" in ${scn()}`);
        out.push(it);
      }
    }
    return out;
  }

  // ---- sound: the object's own, else a soft click (and the agent hears about it once, at load)
  function ctx() {
    if (!listener) listener = listenerOf(ed);
    if (listener.context.state !== 'running') listener.context.resume().catch(() => {});
    return listener.context;
  }
  function click() {
    const a = ctx(), t = a.currentTime, o = a.createOscillator(), g = a.createGain();
    o.type = 'triangle'; o.frequency.setValueAtTime(1800, t); o.frequency.exponentialRampToValueAtTime(600, t + 0.03);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.003); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    o.connect(g).connect(a.destination); o.start(t); o.stop(t + 0.08);
  }
  function buzz() {                                             // a failed press: two low falling buzzes, unlike any click
    const a = ctx(), t = a.currentTime;
    for (const dt of [0, 0.16]) {
      const o = a.createOscillator(), g = a.createGain();
      o.type = 'sawtooth'; o.frequency.setValueAtTime(190, t + dt); o.frequency.exponentialRampToValueAtTime(95, t + dt + 0.12);
      g.gain.setValueAtTime(0.0001, t + dt); g.gain.exponentialRampToValueAtTime(0.18, t + dt + 0.01); g.gain.exponentialRampToValueAtTime(0.0001, t + dt + 0.13);
      o.connect(g).connect(a.destination); o.start(t + dt); o.stop(t + dt + 0.15);
    }
  }
  async function sound(file, o = {}) {
    const url = new URL(base() + file, location.href).href;
    let buf = buffers.get(url);
    if (!buf) { buf = await new THREE.AudioLoader().loadAsync(url); buffers.set(url, buf); }
    const at = o.at ? items(o.at)[0] : null, holder = new THREE.Object3D();
    if (at) at.obj.getWorldPosition(holder.position); else ed.camera.getWorldPosition(holder.position);
    ed.scene.add(holder);
    const src = new THREE.PositionalAudio(listenerOf(ed));
    ctx();
    src.setBuffer(buf); src.setRefDistance(o.ref ?? 1.5); src.setVolume(o.volume ?? 0.8); src.setLoop(false);
    holder.add(src); src.play();
    src.source.onended = () => { holder.removeFromParent(); src.disconnect(); };
    return { sound: file, seconds: +buf.duration.toFixed(2) };
  }
  async function feedback(name) {
    const d = defs[name];
    if (d && d.sound) await sound(d.sound, { at: name, volume: d.volume }).catch((e) => { click(); warn(name, `sound ${d.sound}: ${e.message || e}`); });
    else click();
  }

  // ---- the room: lights, visibility, moves; run-time only (editor.js keeps the edited state for the build)
  function step() {
    const now = performance.now();
    for (const a of anims) {
      const k = Math.min(1, (now - a.t0) / a.ms), e = k * k * (3 - 2 * k);
      a.at(e);
      if (k >= 1) a.done = true;
    }
    if (anims.some((a) => a.done)) anims = anims.filter((a) => !a.done);
    if (anims.length) ed.emit('change');
  }
  ed.preRender.push(step);
  function tween(key, seconds, at) {
    anims = anims.filter((a) => a.key !== key);
    if (!(seconds > 0)) { at(1); ed.emit('change'); return; }
    anims.push({ key, t0: performance.now(), ms: seconds * 1000, at });
  }
  function light(names, o = {}) {
    const list = items(names).filter((it) => it.light), n = list.length;
    list.forEach((it, i) => {
      const home = it.lightHome;
      let w = Array.isArray(o.energy) ? o.energy[i % o.energy.length] : o.energy;
      if (w === null) w = home ? home.energy : ed.energy(it);                       // null: back to as built
      if (w !== undefined && !(Number.isFinite(+w) && +w >= 0)) throw new Error(`light ${it.name}: energy must be watts, 0 or more`);
      const c = o.color === null ? (home ? home.color : null) : o.color;
      const w0 = ed.energy(it), c0 = it.light.color.clone(), c1 = c ? new THREE.Color().setRGB(c[0], c[1], c[2], THREE.LinearSRGBColorSpace) : null;
      tween('light:' + it.name, o.seconds, (e) => ed.runLight(it, w === undefined ? undefined : w0 + (w - w0) * e,
        c1 ? [c0.r + (c1.r - c0.r) * e, c0.g + (c1.g - c0.g) * e, c0.b + (c1.b - c0.b) * e] : null));
    });
    return n;
  }
  function show(names, on = true) {
    const list = items(names);
    for (const it of list) { ed.runHome(it); it.obj.visible = !!on; }
    ed.emit('change');
    return list.length;
  }
  function move(name, o = {}) {
    const it = items(name)[0];
    ed.runHome(it);
    const t0 = ed.blenderTransform(it), p0 = b2t(t0.location);
    const p1 = o.to ? b2t(o.to) : o.by ? p0.clone().add(b2t(o.by)) : p0.clone();
    const q0 = it.obj.getWorldQuaternion(new THREE.Quaternion());
    let q1 = q0.clone();
    if (o.turn) {
      const r = o.turn.map((d) => THREE.MathUtils.degToRad(d));
      q1 = new THREE.Quaternion().setFromEuler(new THREE.Euler(r[0], r[2], -r[1], 'YZX')).multiply(q0);   // Blender XYZ (Rz Ry Rx) in three's axes
    }
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), m = new THREE.Matrix4(), s = new THREE.Vector3();
    it.obj.getWorldScale(s);
    tween('move:' + it.name, o.seconds ?? 0.4, (e) => {
      p.lerpVectors(p0, p1, e); q.slerpQuaternions(q0, q1, e);
      m.compose(p, q, s);
      if (it.obj.parent) { it.obj.parent.updateWorldMatrix(true, false); m.premultiply(new THREE.Matrix4().copy(it.obj.parent.matrixWorld).invert()); }
      m.decompose(it.obj.position, it.obj.quaternion, it.obj.scale);
    });
    return it.name;
  }

  // ---- where things are, placing them, following them (see the top: s.where, s.place, s.follow)
  const UP = new THREE.Vector3(0, 1, 0), RAD = Math.PI / 180;
  const vec3 = (v, what) => {
    if (!Array.isArray(v) || v.length !== 3 || !v.every((x) => Number.isFinite(+x))) throw new Error(`${what} must be [x, y, z]`);
    return v.map(Number);
  };
  // a target's pose in three's space: p, q, look (where it looks), facing (that on the floor), aimed (a camera's
  // frame); null while the body a target names is not shown (a take not playing yet): a follow waits where it is
  function poseOf(target) {
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
    let look, aimed = false;
    if (target === 'person') {
      ed.camera.updateMatrixWorld(true); ed.camera.matrixWorld.decompose(p, q, sc);
      look = new THREE.Vector3(0, 0, -1).applyQuaternion(q); aimed = true;
    } else if (typeof target === 'string' && target.includes(':') && !ed.byName.has(target)) {
      const [who, bone] = target.split(':');
      if (!ed.byName.has(who)) throw new Error(`no person "${who}" in ${scn()}`);
      if (!actors) throw new Error('no bodies on this page');
      const rig = actors.shownRig(who);
      if (!rig) return null;
      const b = rig.bones[bone];
      if (!b) throw new Error(`${who} has no bone "${bone}"; some: ${Object.keys(rig.bones).filter((n) => !/_0\d_|twist|ik_/.test(n)).sort().join(', ')}`);
      b.updateWorldMatrix(true, false); b.matrixWorld.decompose(p, q, sc);
      const pel = rig.bones.pelvis, rest = rig.rest && rig.rest.pelvis;   // a body faces where its hips face
      look = pel && rest && rig.fwd ? rig.fwd.clone().applyQuaternion(pel.getWorldQuaternion(new THREE.Quaternion()).multiply(rest.q.clone().invert()))
        : new THREE.Vector3(0, 0, 1).applyQuaternion(q);
    } else {
      if (typeof target !== 'string' || target.includes('*')) throw new Error(`a target is one name, 'person' or '<person>:<bone>', not ${JSON.stringify(target)}`);
      const it = items(target)[0];
      it.obj.updateWorldMatrix(true, false); it.obj.matrixWorld.decompose(p, q, sc);
      aimed = !!it.aimed;
      look = new THREE.Vector3(0, 0, aimed ? -1 : 1).applyQuaternion(q);   // cameras and lights look down -Z, things face +Z
    }
    const facing = look.clone().setY(0);
    if (facing.lengthSq() < 1e-8) facing.set(0, 0, -1);
    return { p, q, look: look.normalize(), facing: facing.normalize(), aimed };
  }
  const r4 = (a) => a.map((v) => +(+v).toFixed(4));
  function where(target) {
    const w = poseOf(target);
    if (!w) return { target, shown: false };
    const bq = t2bQuat(w.q, w.aimed), e = new THREE.Euler().setFromQuaternion(new THREE.Quaternion(bq[1], bq[2], bq[3], bq[0]), 'ZYX');
    return { target, location: r4(t2bPos(w.p)), rotation: r4([e.x / RAD, e.y / RAD, e.z / RAD]), quaternion: r4(bq),
      forward: r4(t2bPos(w.look)), facing: r4(t2bPos(w.facing)) };
  }
  // a world pose (three's space) onto an object, whatever its parent
  function setWorld(it, p, q) {
    const sc = new THREE.Vector3(), m = new THREE.Matrix4();
    it.obj.getWorldScale(sc);
    m.compose(p, q, sc);
    if (it.obj.parent) { it.obj.parent.updateWorldMatrix(true, false); m.premultiply(new THREE.Matrix4().copy(it.obj.parent.matrixWorld).invert()); }
    m.decompose(it.obj.position, it.obj.quaternion, it.obj.scale);
    it.obj.updateMatrixWorld(true);
  }
  // the turn that points it from `from` at `to` (three's space), the horizon level
  function aimAt(it, from, to) {
    if (from.distanceToSquared(to) < 1e-8) return null;
    const m = it.aimed ? new THREE.Matrix4().lookAt(from, to, UP) : new THREE.Matrix4().lookAt(to, from, UP);
    return new THREE.Quaternion().setFromRotationMatrix(m);
  }
  function aimPoint(look, w) {
    if (look === true) return w ? w.p.clone() : null;
    if (Array.isArray(look)) return b2t(vec3(look, 'look'));
    const lw = poseOf(look);
    return lw ? lw.p : null;
  }
  function place(name, o = {}) {
    const it = items(name)[0];
    ed.runHome(it);
    anims = anims.filter((a) => a.key !== 'move:' + it.name);
    const p = o.location ? b2t(vec3(o.location, 'location')) : it.obj.getWorldPosition(new THREE.Vector3());
    let q = it.obj.getWorldQuaternion(new THREE.Quaternion());
    if (o.quaternion) q = b2tQuat(o.quaternion, it.aimed);
    else if (o.rotation) {
      const r = vec3(o.rotation, 'rotation'), bq = new THREE.Quaternion().setFromEuler(new THREE.Euler(r[0] * RAD, r[1] * RAD, r[2] * RAD, 'ZYX'));
      q = b2tQuat([bq.w, bq.x, bq.y, bq.z], it.aimed);
    } else if (o.look != null && o.look !== false) {
      const to = aimPoint(o.look, null);
      if (!to) throw new Error(`place ${it.name}: ${JSON.stringify(o.look)} is not shown now`);
      q = aimAt(it, p, to) || q;
    }
    setWorld(it, p, q);
    ed.emit('change');
    return it.name;
  }

  // follows: object name -> {it, owner, spec, p, q, start, last, waiting}; one per object, the newest wins
  const follows = new Map();
  function follow(owner, target, o = {}) {
    const name = o.object || owner, it = items(name)[0];
    if (target == null || target === false) return unfollow(it.name, 'stopped by ' + owner);
    const w = poseOf(target);                                   // a bad target fails here, in the caller's phase
    if (o.offset != null) vec3(o.offset, 'offset');
    if (o.look_offset != null) vec3(o.look_offset, 'look_offset');
    if (o.space && !['target', 'world'].includes(o.space)) throw new Error("space is 'target' or 'world'");
    if (Array.isArray(o.look)) vec3(o.look, 'look');
    else if (typeof o.look === 'string') poseOf(o.look);
    const old = follows.get(it.name);
    const spec = { offset: o.offset || [0, 0, 0], space: o.space || 'target', lag: o.lag != null ? Math.max(0, +o.lag) : 0.5,
      look: o.look != null ? o.look : !!it.aimed, look_offset: o.look_offset || [0, 0, 0],
      look_lag: o.look_lag != null ? Math.max(0, +o.look_lag) : 0.25, orbit: +o.orbit || 0, snap: !!o.snap, target };
    ed.runHome(it);
    anims = anims.filter((a) => a.key !== 'move:' + it.name);
    if (clock) clock.hold(it.name);
    const f = { it, owner, spec, p: old ? old.p : null, q: old ? old.q : null, start: old ? old.start : performance.now(), last: old ? old.last : null, waiting: !w };
    follows.set(it.name, f);
    if (spec.snap && !old) followStep(it.name, f, performance.now(), true);
    if (!old || old.spec.target !== target) live.emit('follow', { object: it.name, target, by: owner, following: true });
    return { object: it.name, target, following: true, ...(w ? {} : { waiting: 'the body is not shown yet' }) };
  }
  function unfollow(name, why = 'stopped') {
    const f = follows.get(name);
    if (!f) return { object: name, following: false };
    follows.delete(name);
    if (clock) clock.unhold(name);
    live.emit('follow', { object: name, target: f.spec.target, following: false, why });
    return { object: name, following: false, why };
  }
  function followStep(name, f, now, snap = false) {
    const { it, spec } = f;
    if (ed.editing && ed.selected === it) { f.last = now; f.p = null; f.q = null; return; }   // a hand that holds it wins
    const w = poseOf(spec.target);
    if (!w) { f.waiting = true; f.last = now; return; }
    f.waiting = false;
    // the smoothing is exact for any step, so a slow frame moves it as far as the time says (capped at 1 s after a stall)
    const dt = f.last == null ? 0 : Math.min(1, (now - f.last) / 1000);
    f.last = now;
    let off;
    if (spec.space === 'world') off = b2t(spec.offset);
    else {
      const fw = w.facing.clone(), right = fw.clone().cross(UP).normalize();
      off = right.multiplyScalar(spec.offset[0]).addScaledVector(fw, spec.offset[1]).addScaledVector(UP, spec.offset[2]);
    }
    if (spec.orbit) off.applyAxisAngle(UP, spec.orbit * RAD * (now - f.start) / 1000);   // counterclockwise seen from above
    const goal = w.p.clone().add(off);
    if (!f.p || snap) f.p = snap ? goal.clone() : it.obj.getWorldPosition(new THREE.Vector3());
    if (!f.q) f.q = it.obj.getWorldQuaternion(new THREE.Quaternion());
    f.p.lerp(goal, snap || spec.lag <= 0 ? 1 : 1 - Math.exp(-dt / spec.lag));
    if (spec.look !== false) {
      const to = aimPoint(spec.look, w);
      if (to) {
        to.add(b2t(spec.look_offset));
        const q1 = aimAt(it, f.p, to);
        if (q1) f.q.slerp(q1, snap || spec.look_lag <= 0 ? 1 : 1 - Math.exp(-dt / spec.look_lag));
      }
    }
    setWorld(it, f.p, f.q);
  }

  // every frame: the objects' own frame(s, dt), then the follows (this runs after the clock and the bodies have moved;
  // performance.now is the capture's clock in stage_capture, so a follow renders frame by frame there too)
  let lastTick = null;
  const frameOwners = new Set(), handles = new Map();
  const handleOf = (name) => { if (!handles.has(name)) handles.set(name, handle(name)); return handles.get(name); };
  function tick() {
    const now = performance.now(), dt = lastTick == null ? 0 : Math.min(0.1, (now - lastTick) / 1000);
    lastTick = now;
    for (const name of [...frameOwners]) {
      const stop = (e) => { if (frameOwners.delete(name)) fail(name, 'frame', e); };
      try { const r = defs[name].frame(handleOf(name), dt); if (r && r.catch) r.catch(stop); } catch (e) { stop(e); }
    }
    for (const [name, f] of [...follows]) {
      try { followStep(name, f, now); } catch (e) { unfollow(name, 'error'); fail(f.owner, 'follow', e); }
    }
    if (follows.size || frameOwners.size) ed.emit('change');
  }
  ed.preRender.push(tick);
  const followsOf = () => [...follows].map(([object, f]) => ({ object, target: f.spec.target, by: f.owner, ...(f.waiting ? { waiting: true } : {}) }));

  // ---- state: per object, saved with the scene (behaviour_state.json beside it)
  function save() {
    clearTimeout(saveTimer);
    const scene = scn(), body = JSON.stringify(states);
    saveTimer = setTimeout(() => fetch(`behaviour_state?scene=${encodeURIComponent(scene)}`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' }, body }).catch(() => {}), 300);
  }
  function setState(name, patch, why) {
    if (!defs[name]) throw new Error(`"${name}" has no behaviour in ${scn()}`);
    states[name] = { ...(states[name] || {}), ...patch };
    save();
    live.emit('behaviour_state', { object: name, state: states[name], why });
    if (defs[name].apply) call(name, 'apply', undefined, 'state');
    return states[name];
  }

  // ---- running a function: the object's handle, errors reported to the agents, never thrown into the frame loop
  const warned = new Set();
  function warn(name, problem) {
    const k = name + '|' + problem;
    if (warned.has(k)) return;
    warned.add(k); report.warnings.push({ object: name, problem });
    live.emit('behaviour_warning', { object: name, problem });
  }
  function handle(me) {
    const h = Object.freeze({
      me,
      get: (k) => (k == null ? { ...(states[me] || {}) } : (states[me] || {})[k]),
      set: (k, v) => setState(me, typeof k === 'object' && k ? k : { [k]: v }, 'set by ' + me),
      state: (obj, k) => (k == null ? { ...(states[obj] || {}) } : (states[obj] || {})[k]),
      send: (obj, input, value) => run(obj, input, value, 'wire from ' + me, false),
      light: (names, o) => light(names, o),
      show: (names, on) => show(names, on),
      move: (name, o) => move(name, o),
      sound: (file, o = {}) => sound(file, { at: me, ...o }),
      emit: (type, data = {}) => live.emit('object_message', { from: me, message: String(type), data }),
      do: (type, fields = {}) => {
        const h = live.handlers[type];
        if (!h) throw new Error(`no page command "${type}"`);
        return h({ type, ...fields });
      },
      after: (sec, fn) => { const g = gen, id = setTimeout(() => { if (g === gen) guard(me, 'after', () => fn(h)); }, sec * 1000); timers.push(() => clearTimeout(id)); return id; },
      every: (sec, fn) => { const g = gen, id = setInterval(() => { if (g === gen) guard(me, 'every', () => fn(h)); }, Math.max(50, sec * 1000)); timers.push(() => clearInterval(id)); return id; },
      clock: () => (clock ? { t: clock.st.t, playing: !!clock.st.playing } : { t: 0, playing: false }),
      where: (target) => where(target),
      place: (name, o) => place(name, o),
      follow: (target, o) => follow(me, target, o),
    });
    return h;
  }
  function guard(name, action, fn) {
    try {
      const r = fn();
      if (r && r.catch) return r.catch((e) => { fail(name, action, e); });   // reported once, never rethrown
      return r;
    } catch (e) { fail(name, action, e); return undefined; }
  }
  function phaseOf(name, action) {
    if (['load', 'apply', 'press', 'after', 'every', 'frame', 'follow'].includes(action)) return action;
    const d = defs[name] || {};
    return d.menu && d.menu[action] ? 'menu' : d.inputs && d.inputs[action] ? 'input' : action;
  }
  function lineOf(e) {                                          // the line in behaviours.js, from the stack, when it is there
    const m = modUrl && e && e.stack && e.stack.match(new RegExp(modUrl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ':(\\d+)'));
    return m ? +m[1] : (e && e.lineNumber) || null;
  }
  function fail(name, action, e, line = lineOf(e)) {
    const error = String((e && e.message) || e), err = { object: name, phase: phaseOf(name, action), action, error, line };
    report.errors.push(err);
    live.emit('behaviour_error', err);
    return err;
  }
  function nearHead() {
    const head = ed.camera.getWorldPosition(new THREE.Vector3()), look = ed.camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    const near = head.clone().addScaledVector(look, 0.55); near.y = head.y - 0.18;
    return near;
  }
  // a person's press that failed: a buzz, and a short note at the object (gone by itself), with the edit menu one tap away
  async function failedPress(name, error) {
    try { buzz(); } catch (e) { /* no sound must not cost the note */ }
    const it = ed.byName.get(name);
    const r = await panels.show({ panel_id: 'behaviour_error_' + name, title: (it ? ed.label(it) : name) + ': its code failed',
      text: error.length > 140 ? error.slice(0, 137) + '...' : error, buttons: ['⚙ Edit', '✕'], near: nearHead(), width: 0.4,
      quiet: true, seconds: 8 }).catch(() => null);
    if (r && r.answer === '⚙ Edit' && it) { editing = it; ed.select(it, 'behaviour-edit'); setTimeout(() => { if (editing === it) editing = null; }, 60000); }
  }
  function fnOf(d, action) {
    if (ACTIONS.includes(action)) return d[action];
    return (d.menu && d.menu[action]) || (d.inputs && d.inputs[action]) || null;
  }
  function call(name, action, value, via) {
    const d = defs[name], fn = d && fnOf(d, action);
    if (!fn) throw new Error(`"${name}" has no ${action} (it has: ${actionsOf(name).join(', ') || 'nothing'})`);
    return guard(name, action, () => fn(handle(name), value));
  }
  function actionsOf(name) {
    const d = defs[name] || {};
    return [...(d.press ? ['press'] : []), ...Object.keys(d.menu || {}), ...Object.keys(d.inputs || {})];
  }
  // a person's press, a menu item, an agent's run, or a wire from another object (sounds, unless it is a wire)
  async function run(name, action = 'press', value, via = 'agent', audible = true) {
    if (!defs[name]) throw new Error(`"${name}" has no behaviour in ${scn()} (stage_behaviours lists them)`);
    if (audible) feedback(name);
    const errs = report.errors.length;
    await call(name, action, value, via);
    const res = { object: name, action, via, state: states[name] || {} };
    if (report.errors.length > errs) {
      res.error = report.errors[report.errors.length - 1].error;
      if (via === 'person') failedPress(name, res.error);
    }
    live.emit('behaviour_run', res);
    return res;
  }

  // ---- loading: the scene's file, its saved state, apply() on each object
  // a module that does not parse says where only to a script tag (the window's error event has the line), so ask one
  function syntaxLine(src) {
    return new Promise((res) => {
      const url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' })), el = document.createElement('script');
      let over = false;
      const done = (line) => { if (over) return; over = true; window.removeEventListener('error', on, true); el.remove(); URL.revokeObjectURL(url); res(line); };
      const on = (ev) => { if (ev.filename === url) { ev.preventDefault(); done(ev.lineno || null); } };
      window.addEventListener('error', on, true);
      el.type = 'module'; el.src = url; el.onload = () => done(null); el.onerror = () => done(null);
      document.head.appendChild(el); setTimeout(() => done(null), 1500);
    });
  }
  const esc = (x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const named = (name, src) => new RegExp(`(^|[\\s{,])['"]?${esc(name)}['"]?\\s*:`, 'm').test(src);   // a key in the file

  // ---- loading: the scene's file, its saved state, apply() on each object
  async function load(why = 'load') {
    gen++;
    for (const t of timers) t();
    timers = []; anims = []; defs = {}; warned.clear(); broken.clear();
    for (const name of [...follows.keys()]) unfollow(name, 'reload');
    frameOwners.clear(); handles.clear();
    report = { file: false, objects: [], errors: [], warnings: [], why };
    const scene = scn();
    const r = await fetch(base() + 'behaviours.js', { cache: 'no-store' }).catch(() => null);
    if (!r || !r.ok) { live.emit('behaviours_loaded', report); return report; }
    report.file = true;
    let mod = null;
    const src = await r.text(), url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    modUrl = url;
    try { mod = await import(/* @vite-ignore */ url); } catch (e) {
      const line = lineOf(e) || (e instanceof SyntaxError ? await syntaxLine(src) : null);
      const err = fail(null, 'load', e, line);
      // the whole file is out: the objects it names answer a press with a buzz, not the edit menu
      for (const name of ed.byName.keys()) if (named(name, src)) broken.set(name, 'the file did not load: ' + err.error + (line ? ` (line ${line})` : ''));
    } finally { URL.revokeObjectURL(url); }
    if (scene !== scn()) return report;                        // the scene changed while it loaded
    const all = (mod && mod.default) || {};
    if (mod && (typeof all !== 'object' || Array.isArray(all))) fail(null, 'load', 'export default must be an object: { objectName: { press, menu, ... } }', null);
    const saved = await fetch(`behaviour_state?scene=${encodeURIComponent(scene)}`, { cache: 'no-store' }).then((x) => (x.ok ? x.json() : {})).catch(() => ({}));
    for (const [name, d] of Object.entries(typeof all === 'object' && all ? all : {})) {
      if (!ed.byName.has(name)) { fail(name, 'load', `no object "${name}" in ${scene}`, null); continue; }
      if (!d || typeof d !== 'object') { broken.set(name, fail(name, 'load', 'must be an object: { sound, press, menu, ... }', null).error); continue; }
      defs[name] = d;
      states[name] = { ...(d.state || {}), ...((saved && saved[name]) || {}) };
      if (!d.sound) warn(name, 'no sound: every interaction needs one (render it with ismail, put it under sounds/, name it in `sound`)');
      report.objects.push({ object: name, actions: actionsOf(name), state: states[name], sound: d.sound || null });
    }
    for (const name of Object.keys(defs)) if (defs[name].apply) call(name, 'apply', undefined, 'load');
    for (const name of Object.keys(defs)) if (typeof defs[name].frame === 'function') frameOwners.add(name);
    live.emit('behaviours_loaded', { ...report, objects: report.objects.map((o) => o.object), broken: [...broken.keys()] });
    return report;
  }

  // ---- in VR: a pinch or trigger on a thing with a behaviour presses it (or opens its menu) instead of the edit menu
  function ownerOf(it) {
    for (const x of [...(it.path || [it])].reverse()) if (defs[x.name]) return x.name;
    return defs[it.name] ? it.name : null;
  }
  let editing = null;
  function onSelect(it, via) {
    if (typeof it === 'string') it = ed.byName.get(it) || null;
    if (!it || !['xr', 'touch'].includes(via)) return false;   // the desktop selects to edit; agents use the ops
    const name = ownerOf(it);
    if (!name && editing !== it) {                              // code that did not load: say so, never a silent edit menu
      const bad = [...(it.path || [it])].reverse().map((x) => x.name).find((n) => broken.has(n));
      if (bad) {
        const now = performance.now();
        ed.select(null, 'behaviour');
        if (now - (lastPress.get(bad) || 0) >= PRESS_GAP_MS) { lastPress.set(bad, now); failedPress(bad, fail(bad, 'press', broken.get(bad), null).error); }
        return true;
      }
    }
    if (!name || editing === it) return false;
    const d = defs[name];
    if (!d.press && !d.menu) return false;
    const now = performance.now();
    if (now - (lastPress.get(name) || 0) < PRESS_GAP_MS) { ed.select(null, 'behaviour'); return true; }
    lastPress.set(name, now);
    ed.select(null, 'behaviour');
    const press = () => run(name, 'press', undefined, 'person').catch((e) => fail(name, 'press', e));
    if (!d.menu) press();
    else if (!d.press) menu(name, it);
    else whenHeld(() => menu(name, it), press);
    return true;
  }
  // a pinch held past HOLD_FOR_MENU_MS -> held(), let go before -> quick(). Let go is the session's selectend (a
  // controller's trigger or the system pinch); a hand's own pinch (touch.js) reads as still held while hands.js sees it
  let holding = null;
  function whenHeld(held, quick) {
    const session = ed.renderer.xr.getSession && ed.renderer.xr.getSession();
    const pinching = () => !!hands && ['left', 'right'].some((sd) => hands.state[sd] && ['pinch', 'ok'].includes(hands.state[sd].g));
    if (!session && !pinching()) { quick(); return; }           // nothing to hold (a desktop test): a press
    if (holding) holding.cancel();
    let ended = false, over = false;
    const onEnd = () => { ended = true; if (!over) finish(false); };
    const finish = (long) => {
      if (over) return;
      over = true; clearTimeout(timer); if (session) session.removeEventListener('selectend', onEnd);
      if (holding === h) holding = null;
      (long ? held : quick)();
    };
    // tracked hands: still pinching is the truth (a near pinch may never reach the session); controllers: no selectend yet
    const tracked = () => !!hands && ['left', 'right'].some((sd) => hands.state[sd] && hands.state[sd].f);
    const timer = setTimeout(() => finish(!ended && (tracked() ? pinching() : !!session)), HOLD_FOR_MENU_MS);
    const h = { cancel: () => { over = true; clearTimeout(timer); if (session) session.removeEventListener('selectend', onEnd); } };
    holding = h;
    if (session) session.addEventListener('selectend', onEnd);
  }
  // a pinch on a thing whose menu is up does nothing: the menu stays where it is (the user, 2026-10-10: "if I want to
  // be like a kid with a light switch, I could DDOS the menu system")
  const menusUp = new Set();
  async function menu(name, it) {
    const id = 'behaviour_' + name;
    if (menusUp.has(id) || (panels.panels && panels.panels.has(id))) return;
    menusUp.add(id);
    try { await menuOpen(name, it, id); } finally { menusUp.delete(id); }
  }
  async function menuOpen(name, it, id) {
    const d = defs[name], labels = Object.keys(d.menu);
    const r = await panels.show({ panel_id: id, title: d.title || ed.label(it),     // one per thing
      text: d.text || '', buttons: [...labels, '⚙ Edit', '✕'], near: nearHead(), width: labels.length > 3 ? 0.42 : 0.34, quiet: true });
    const a = r && r.answer;
    if (!a || a === '✕') return;
    if (a === '⚙ Edit') { editing = it; ed.select(it, 'behaviour-edit'); setTimeout(() => { if (editing === it) editing = null; }, 60000); return; }
    run(name, a, undefined, 'person').catch((e) => fail(name, a, e));
  }
  ed.addEventListener('select', () => { if (editing && ed.selected !== editing) editing = null; });

  // once per room, as soon as its objects exist (not after the piece-by-piece reveal: the switch works at once)
  let room = null;
  const fresh = () => { if (ed.root && ed.root !== room) { room = ed.root; load('scene').catch((e) => fail(null, 'load', e)); } };
  for (const t of ['loaded', 'switched', 'reloaded', 'revealed']) ed.addEventListener(t, fresh);
  if (ed.items.length) fresh();                                // the room came before this module did
  live.handlers.behaviours = async (c) => {
    const r = c.reload ? await load('reload') : report;
    return { file: r.file, objects: report.objects.map((o) => ({ ...o, state: states[o.object] || {} })), errors: r.errors.slice(-20), warnings: r.warnings, broken: Object.fromEntries(broken), following: followsOf() };
  };
  live.handlers.behaviour_run = (c) => run(c.object, c.action || 'press', c.value, c.via || 'agent', c.sound !== false);
  live.handlers.behaviour_state = (c) => (c.state ? setState(c.object, c.state, 'agent') : (defs[c.object] ? { ...(states[c.object] || {}) } : (() => { throw new Error(`"${c.object}" has no behaviour in ${scn()}`); })()));
  // an agent's follow, on trial (no code): the same follow a behaviour starts; a reload of behaviours.js ends it
  live.handlers.follow = (c) => {
    if (c.where) return where(c.where);
    if (!c.object) return { following: followsOf() };
    return follow('agent', c.target == null ? null : c.target, { ...(c.options || {}), object: c.object });
  };
  // for cam_record (camrec.js): bake a follow into keys, stop it so the keys play, start it again on Undo or Redo
  const followApi = {
    of: (name) => { const f = follows.get(name); return f ? { owner: f.owner, target: f.spec.target, spec: { ...f.spec } } : null; },
    stop: (name, why) => unfollow(name, why),
    start: (name, saved) => follow(saved.owner, saved.target, { ...saved.spec, object: name }),
    snap: (name) => { const f = follows.get(name); if (f) followStep(name, f, performance.now(), true); },
  };
  return { load, run, onSelect, ownerOf, follows: followApi, where, state: () => ({ objects: Object.keys(defs), errors: report.errors.length, broken: [...broken.keys()], following: followsOf() }) };
}
