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
//     now_light_switch: {
//       sound: 'sounds/switch.wav',                  // under scenes/<scene>/; every press plays it, at the object
//       volume: 0.8,                                 // of that sound (0 to 1)
//       state: { club: true },                       // its starting state; saved per scene as it changes
//       apply(s) {                                   // state -> room, run at load and after every change of state
//         const club = s.get('club');
//         s.light(['cf_house_old', 'cf_house_hall'], { energy: club ? 0 : [300, 900], seconds: 0.4 });
//         s.light('cf_floor_*', { energy: club ? null : 0 });       // null: back to how the scene was built
//       },
//       press(s) { s.set('club', !s.get('club')); },  // a pinch or trigger on it in VR, or stage_behaviour_run
//       menu: { 'Club': (s) => s.set('club', true), 'After hours': (s) => s.set('club', false) },
//       inputs: { power(s, on) { s.set('club', !!on); } },          // what other objects can send it (s.send)
//     },
//   };
// s: me, get(key?), set(key, value | {..}), state(object, key?), send(object, input, value), light(names, {energy,
// color, seconds}), show(names, on), move(name, {by, to, turn, seconds}), sound(file, {at, volume}), emit(type, data),
// do(command, fields), after(seconds, fn), every(seconds, fn). Names take a list or a glob (cf_floor_*). Positions are
// Blender metres, z up; turn is [x, y, z] degrees about the object's own origin.
import * as THREE from 'three';
import { listenerOf } from './stream.js';

const PRESS_GAP_MS = 350;              // one press per object at most this often (a held pinch re-presses)
const ACTIONS = ['press', 'apply'];

export function initBehaviours(ed, live, panels) {
  let defs = {}, states = {}, report = { file: false, objects: [], errors: [], warnings: [] };
  let timers = [], anims = [], saveTimer = null, gen = 0;
  const lastPress = new Map(), buffers = new Map();
  let listener = null;
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
      q1 = new THREE.Quaternion().setFromEuler(new THREE.Euler(r[0], r[2], -r[1], 'XYZ')).multiply(q0);   // Blender xyz
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
    });
    return h;
  }
  function guard(name, action, fn) {
    try {
      const r = fn();
      if (r && r.catch) r.catch((e) => fail(name, action, e));
      return r;
    } catch (e) { fail(name, action, e); return undefined; }
  }
  function fail(name, action, e) {
    const error = String((e && e.message) || e);
    report.errors.push({ object: name, action, error });
    live.emit('behaviour_error', { object: name, action, error });
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
    if (report.errors.length > errs) res.error = report.errors[report.errors.length - 1].error;
    live.emit('behaviour_run', res);
    return res;
  }

  // ---- loading: the scene's file, its saved state, apply() on each object
  async function load(why = 'load') {
    gen++;
    for (const t of timers) t();
    timers = []; anims = []; defs = {}; warned.clear();
    report = { file: false, objects: [], errors: [], warnings: [], why };
    const scene = scn();
    const r = await fetch(base() + 'behaviours.js', { cache: 'no-store' }).catch(() => null);
    if (!r || !r.ok) { live.emit('behaviours_loaded', report); return report; }
    report.file = true;
    let mod = null;
    const src = await r.text(), url = URL.createObjectURL(new Blob([src], { type: 'text/javascript' }));
    try { mod = await import(/* @vite-ignore */ url); } catch (e) {
      report.errors.push({ object: null, action: 'load', error: String((e && e.message) || e) });
    } finally { URL.revokeObjectURL(url); }
    if (scene !== scn()) return report;                        // the scene changed while it loaded
    const all = (mod && mod.default) || {};
    if (mod && (typeof all !== 'object' || Array.isArray(all))) report.errors.push({ object: null, action: 'load', error: 'export default must be an object: { objectName: { press, menu, ... } }' });
    const saved = await fetch(`behaviour_state?scene=${encodeURIComponent(scene)}`, { cache: 'no-store' }).then((x) => (x.ok ? x.json() : {})).catch(() => ({}));
    for (const [name, d] of Object.entries(typeof all === 'object' && all ? all : {})) {
      if (!ed.byName.has(name)) { report.errors.push({ object: name, action: 'load', error: `no object "${name}" in ${scene}` }); continue; }
      if (!d || typeof d !== 'object') { report.errors.push({ object: name, action: 'load', error: 'must be an object' }); continue; }
      defs[name] = d;
      states[name] = { ...(d.state || {}), ...((saved && saved[name]) || {}) };
      if (!d.sound) warn(name, 'no sound: every interaction needs one (render it with ismail, put it under sounds/, name it in `sound`)');
      report.objects.push({ object: name, actions: actionsOf(name), state: states[name], sound: d.sound || null });
    }
    for (const name of Object.keys(defs)) if (defs[name].apply) call(name, 'apply', undefined, 'load');
    live.emit('behaviours_loaded', { ...report, objects: report.objects.map((o) => o.object) });
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
    if (!name || editing === it) return false;
    const d = defs[name];
    if (!d.press && !d.menu) return false;
    const now = performance.now();
    if (now - (lastPress.get(name) || 0) < PRESS_GAP_MS) { ed.select(null, 'behaviour'); return true; }
    lastPress.set(name, now);
    ed.select(null, 'behaviour');
    if (d.press) run(name, 'press', undefined, 'person').catch((e) => fail(name, 'press', e));
    if (d.menu) menu(name, it);
    return true;
  }
  async function menu(name, it) {
    const d = defs[name], labels = Object.keys(d.menu);
    const head = ed.camera.getWorldPosition(new THREE.Vector3()), look = ed.camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    const near = head.clone().addScaledVector(look, 0.55); near.y = head.y - 0.18;
    const r = await panels.show({ panel_id: 'behaviour_' + name + '_' + Date.now(), title: d.title || ed.label(it),
      text: d.text || '', buttons: [...labels, '⚙ Edit', '✕'], near, width: labels.length > 3 ? 0.42 : 0.34, quiet: true });
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
    return { file: r.file, objects: report.objects.map((o) => ({ ...o, state: states[o.object] || {} })), errors: r.errors.slice(-20), warnings: r.warnings };
  };
  live.handlers.behaviour_run = (c) => run(c.object, c.action || 'press', c.value, c.via || 'agent', c.sound !== false);
  live.handlers.behaviour_state = (c) => (c.state ? setState(c.object, c.state, 'agent') : (defs[c.object] ? { ...(states[c.object] || {}) } : (() => { throw new Error(`"${c.object}" has no behaviour in ${scn()}`); })()));
  return { load, run, onSelect, ownerOf, state: () => ({ objects: Object.keys(defs), errors: report.errors.length }) };
}
