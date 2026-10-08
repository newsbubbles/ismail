// Live link: the page tells the server what the user is doing (full state, debounced, plus a 2 s heartbeat, and
// discrete events), and runs commands that Claude queues on the server (captions, camera flights, focus, select,
// highlights, 3D markers, moving objects, lights, walk). Everything crossing the wire is in Blender world space, Z-up.
import * as THREE from 'three';
import { GIZMO, b2tPos, t2bPos } from './editor.js';

const POLL_MS = 400, DEBOUNCE_MS = 300, HEARTBEAT_MS = 2000;
const rn = (x, n = 4) => Math.round(x * 10 ** n) / 10 ** n;
const rv = (a, n = 4) => a.map((x) => rn(x, n));
const lin = (rgb) => new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.LinearSRGBColorSpace);
const rgbOf = (c) => [c.r, c.g, c.b];
const qb = ([w, x, y, z]) => new THREE.Quaternion(x, y, z, w);   // a Blender [w,x,y,z] as a quaternion (math only)

// Blender-space change from a to b: location in cm, the rotation that takes a to b (angle, world axis, XYZ euler), scale ratio
export function delta(a, b) {
  const d = { move_cm: rv(b.location.map((v, i) => (v - a.location[i]) * 100), 2) };
  const dq = qb(b.quaternion).multiply(qb(a.quaternion).invert()).normalize();
  if (dq.w < 0) dq.set(-dq.x, -dq.y, -dq.z, -dq.w);
  const ang = 2 * Math.acos(Math.min(1, dq.w)), s = Math.sqrt(Math.max(0, 1 - dq.w * dq.w));
  d.rot_deg = rn(THREE.MathUtils.radToDeg(ang), 2);
  if (d.rot_deg >= 0.01 && s > 1e-9) d.rot_axis = rv([dq.x / s, dq.y / s, dq.z / s], 3);
  const e = new THREE.Euler().setFromQuaternion(dq, 'XYZ');
  d.rot_xyz_deg = rv([e.x, e.y, e.z].map(THREE.MathUtils.radToDeg), 2);
  if (a.scale && b.scale) d.scale_ratio = rv(b.scale.map((v, i) => v / (a.scale[i] || 1)), 4);
  return d;
}
const tf = (t) => ({ location: rv(t.location, 5), quaternion: rv(t.quaternion, 5), scale: rv(t.scale, 5) });

export function initLive(ed, desktop, xr) {
  let q = 'scene=' + encodeURIComponent(ed.sceneName);   // follows a scene switch (scenes.js)
  const pageId = Math.random().toString(36).slice(2, 8);
  const $ = (id) => document.getElementById(id);
  const presenting = () => ed.renderer.xr.isPresenting;

  // ---- LIVE badge: up while the server answered in the last 3.5 s
  let lastOk = 0;
  const ok = () => { lastOk = performance.now(); };
  setInterval(() => $('live').classList.toggle('up', performance.now() - lastOk < 3500), 500);

  const desc = (it) => (it ? { object: it.name, object_type: it.type, group: it.top.name, is_group: it.children.length > 0,
    children: it.children.length, path: it.path.map((x) => x.name) } : null);
  function find(name) {
    if (!name) throw new Error('no object given');
    const it = ed.byName.get(name) || ed.items.find((x) => x.name.toLowerCase() === String(name).toLowerCase());
    if (!it) throw new Error(`no object "${name}"`);
    return it;
  }
  const countDesc = (it) => it.children.reduce((a, c) => a + 1 + countDesc(c), 0);

  // ---- events out: batched, kept and retried while the server is down
  const pending = [];
  let sending = false, retry = null;
  const watchers = [];                               // cues.js: menus Claude pre-attaches to the user's events
  function emit(type, data = {}) {
    for (const w of watchers) { try { w(type, data); } catch (_) { /* a watcher never breaks the event */ } }
    pending.push({ page: pageId, ...data, type });   // the event type always wins
    if (pending.length > 300) pending.shift();
    flush();
  }
  async function flush() {
    if (sending || retry || !pending.length) return;
    sending = true;
    const batch = pending.splice(0, pending.length);
    try {
      const r = await fetch('live/event?' + q, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(batch) });
      if (!r.ok) throw new Error(r.status);
      ok();
    } catch (_) {
      pending.unshift(...batch);
      retry = setTimeout(() => { retry = null; flush(); }, 2000);
    }
    sending = false;
    if (pending.length && !retry) flush();
  }

  ed.addEventListener('select', (e) => {
    if (ed.selected) emit('select', { ...desc(ed.selected), via: e.via, prev: e.prev ? e.prev.name : null });
    else if (e.prev) emit('deselect', { prev: e.prev.name, via: e.via });
    markDirty();
  });
  ed.addEventListener('edited', (e) => {
    for (const { it, before } of e.moved) {
      const after = ed.blenderTransform(it);
      emit('transform_end', { ...desc(it), via: e.via, before: tf(before), after: tf(after), delta: delta(before, after),
        carried: countDesc(it) });
    }
    for (const { it, before } of e.lights) {
      emit('light_change', { light: it.name, via: e.via, before: { energy: rn(before.energy, 4), color: rv(before.color) },
        after: { energy: rn(ed.energy(it), 4), color: rv(rgbOf(it.light.color)) } });
    }
    for (const { name, before } of e.materials) {
      emit('material_change', { material: name, via: e.via, before: rv(before), after: rv(rgbOf(ed.materials.get(name).mats[0].color)) });
    }
    markDirty();
  });
  ed.addEventListener('undone', (e) => {
    emit('undo', { objects: e.moved.map((m) => m.it.name), lights: e.lights.map((l) => l.it.name),
      materials: e.materials.map((m) => m.name), depth: e.depth });
    markDirty();
  });
  ed.addEventListener('saved', (e) => {
    emit('save', { path: e.result.path, previous: e.result.previous, counts: e.result.counts });
    markDirty();
  });
  ed.addEventListener('mode', (e) => { emit('mode', { mode: e.mode, camera: e.camera, via: e.via }); markDirty(); });
  ed.addEventListener('focus', (e) => { emit('focus', { ...desc(e.item), via: e.via }); markDirty(); });
  ed.addEventListener('change', () => markDirty());

  // ---- captions: one at a time, queued
  const capQ = [];
  let capOn = false;
  function say(text, seconds = 8, from = null) {
    const ahead = capQ.length + (capOn ? 1 : 0);
    capQ.push({ text: String(text), seconds: Math.max(1, +seconds || 8), from });
    pumpCaptions();
    return { captions_ahead: ahead };
  }
  function pumpCaptions() {
    if (capOn || !capQ.length || document.visibilityState !== 'visible') return;   // never fade out unseen
    const c = capQ.shift(), el = document.createElement('div');
    capOn = c;
    el.className = 'caption';
    const b = document.createElement('b');
    b.textContent = c.from ? String(c.from).toUpperCase() : 'CLAUDE';
    el.append(b, document.createTextNode(c.text));
    $('captions').appendChild(el);
    setTimeout(() => el.classList.add('show'), 30);
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => { el.remove(); capOn = false; markDirty(); pumpCaptions(); }, 400);
    }, c.seconds * 1000);
    markDirty();
  }

  // ---- markers: a pin (ball + stem, drawn on top, never seen by the Blender cameras) and an HTML label
  const markers = new Map();
  const STEM = 0.3;
  function setMarker(id, position, label = '', color = '#d97757') {
    clearMarkers([id]);
    const p = b2tPos(position), col = new THREE.Color(color);
    const g = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ color: col, toneMapped: false, depthTest: false, transparent: true });
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.03, 16, 12), mat);
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.012, 10, 8), mat);
    const stem = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, STEM, 0)]),
      new THREE.LineBasicMaterial({ color: col, toneMapped: false, depthTest: false, transparent: true }));
    ball.position.y = STEM;
    g.add(ball, tip, stem);
    g.position.copy(p);
    g.traverse((o) => { o.layers.set(GIZMO); o.renderOrder = 998; });
    ed.scene.add(g);
    const el = document.createElement('div');
    el.className = 'mlabel';
    el.style.setProperty('--c', color);
    el.textContent = label || id;
    $('labels').appendChild(el);
    markers.set(String(id), { id: String(id), g, el, position: rv(position), label, color });
    markDirty();
    return { id: String(id) };
  }
  function clearMarkers(ids) {
    for (const [id, m] of markers) {
      if (ids && !ids.map(String).includes(id)) continue;
      ed.scene.remove(m.g);
      m.g.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
      m.el.remove();
      markers.delete(id);
    }
    markDirty();
  }

  // ---- highlights: a pulsing box outline around each object (or group), drawn on top
  const highlights = [];
  function highlight(names, color = '#38bdf8', seconds = 6) {
    const its = names.map(find);
    const until = +seconds > 0 ? performance.now() + seconds * 1000 : Infinity;
    for (const it of its) {
      const prev = highlights.findIndex((h) => h.it === it);
      if (prev >= 0) removeHighlight(prev);
      const helper = new THREE.Box3Helper(new THREE.Box3(), new THREE.Color(color));
      helper.material.depthTest = false;
      helper.material.transparent = true;
      helper.material.toneMapped = false;
      helper.renderOrder = 997;
      helper.layers.set(GIZMO);
      ed.scene.add(helper);
      highlights.push({ it, helper, until, color: new THREE.Color(color) });
    }
    markDirty();
    return { objects: its.map((it) => it.name), seconds: +seconds > 0 ? +seconds : null };
  }
  function removeHighlight(i) {
    const h = highlights[i];
    ed.scene.remove(h.helper);
    h.helper.geometry.dispose();
    highlights.splice(i, 1);
  }
  const white = new THREE.Color(1, 1, 1), sp = new THREE.Vector3();
  ed.preRender.push(() => {
    const now = performance.now();
    for (let i = highlights.length - 1; i >= 0; i--) {
      const h = highlights[i];
      if (now > h.until) { removeHighlight(i); markDirty(); continue; }
      const k = 0.5 + 0.5 * Math.sin(now / 1000 * 2 * Math.PI * 1.2);
      h.helper.box.copy(desktop.boxOf(h.it)).expandByScalar(0.01 + 0.012 * k);
      h.helper.material.opacity = 0.45 + 0.55 * k;
      h.helper.material.color.copy(h.color).lerp(white, 0.45 * k);
    }
    // marker labels follow their pins on screen (hidden in look-through and in XR, where the pins are not drawn)
    const cam = desktop.viewCamera(), hide = desktop.view.through || presenting();
    const rect = ed.renderer.domElement.getBoundingClientRect();
    for (const m of markers.values()) {
      sp.copy(m.g.position).y += STEM + 0.03;
      sp.project(cam);
      const vis = !hide && sp.z < 1 && sp.z > -1 && Math.abs(sp.x) < 1.2 && Math.abs(sp.y) < 1.2;
      m.el.hidden = !vis;
      if (vis) {
        m.el.style.left = rect.left + (sp.x + 1) / 2 * rect.width + 'px';
        m.el.style.top = rect.top + (1 - sp.y) / 2 * rect.height + 'px';
      }
    }
  });

  // ---- state out: debounced after any change (the view camera moving counts), and a heartbeat
  let seq = 0, dirtyT = null, inflight = false, again = false, lastPose = '';
  function markDirty() { if (!dirtyT) dirtyT = setTimeout(() => { dirtyT = null; postState(); }, DEBOUNCE_MS); }
  ed.preRender.push(() => {
    const c = desktop.viewCamera();
    const e = c.matrixWorld.elements, key = e.map((x) => x.toFixed(4)).join(',');
    if (key !== lastPose) { lastPose = key; markDirty(); }
  });
  function gather() {
    const cam = desktop.viewCamera();
    cam.updateMatrixWorld(true);
    const p = cam.getWorldPosition(new THREE.Vector3()), f = cam.getWorldDirection(new THREE.Vector3());
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.getWorldQuaternion(new THREE.Quaternion()));
    const fb = t2bPos(f);
    const la = desktop.lookingAt();
    const changed = {}, carried = {}, lights = {}, materials = {};
    for (const it of ed.items) {
      if (ed.changed(it)) {
        const t = ed.blenderTransform(it);
        changed[it.name] = { type: it.type, group: it.top.name, ...tf(t),
          delta: delta({ location: it.man.location, quaternion: it.man.quaternion, scale: it.man.scale }, t) };
        if (it.aimed) { delete changed[it.name].scale; delete changed[it.name].delta.scale_ratio; }
        if (it.children.length) changed[it.name].carries = countDesc(it);
      } else {
        const anc = it.path.find((x) => x !== it && ed.changed(x));
        if (anc) carried[anc.name] = (carried[anc.name] || 0) + 1;
      }
      const lh = it.light && it.lightHome;                       // set by a behaviour: the edit is what is reported
      if (lh ? lh.changed : it.light && ed.lightChanged(it)) {
        lights[it.name] = { energy: rn(lh ? lh.energy : ed.energy(it), 4), energy0: ed.manifest.lights[it.name].energy,
          color: rv(lh ? lh.color : rgbOf(it.light.color)), color0: ed.manifest.lights[it.name].color.map((x) => rn(x, 4)) };
      }
    }
    for (const [name, e] of ed.materials) {
      const c = e.mats[0].color;
      if (!c.equals(e.color0)) materials[name] = { color: rv(rgbOf(c)), color0: rv(rgbOf(e.color0)) };
    }
    const sel = ed.selected;
    const edits = ed.computeEdits();
    return {
      scene: ed.sceneName, page: pageId, seq: ++seq, t: new Date().toISOString(),
      mode: desktop.mode(), look_through: desktop.view.through ? desktop.view.through.name : null, xr: presenting(),
      focused: document.hasFocus(), visible: document.visibilityState === 'visible', flying: desktop.flying(),
      camera: { position: rv(t2bPos(p), 3), forward: rv(fb, 3), up: rv(t2bPos(up), 3), fov: rn(cam.fov, 2),
        heading_deg: rn(THREE.MathUtils.radToDeg(Math.atan2(fb[1], fb[0])), 1),
        pitch_deg: rn(THREE.MathUtils.radToDeg(Math.asin(THREE.MathUtils.clamp(fb[2], -1, 1))), 1) },
      looking_at: la ? { ...desc(la.it), distance: rn(la.distance, 3), point: rv(t2bPos(la.point), 3) } : null,
      selection: sel ? { ...desc(sel), ...tf(ed.blenderTransform(sel)) } : null,
      changed, carried, lights, materials,
      unsaved: ed.dirty(),
      sets: window.VR_sets ? window.VR_sets.state() : null,   // load sets: which are unloaded (loadsets.js)
      behaviours: window.VR_behaviours ? window.VR_behaviours.state() : null,   // things with their own functions
      edit_counts: Object.fromEntries(Object.entries(edits).map(([k, v]) => [k, Object.keys(v).length])),
      undo_depth: ed.undoStack.length,
      walk: desktop.walk.on ? { floor_z: rn(desktop.walk.floorY, 3), eye_above_floor: rn(p.y - desktop.walk.floorY, 3),
        pointer_locked: desktop.walk.locked } : null,
      markers: [...markers.values()].map((m) => ({ id: m.id, position: m.position, label: m.label })),
      highlights: highlights.map((h) => h.it.name),
      caption: capOn ? capOn.text : null, captions_queued: capQ.length,
      music: window.VR_music ? window.VR_music.now() : null,   // the song's time as heard (music.js)
    };
  }
  async function postState() {
    if (inflight) { again = true; return; }
    inflight = true;
    try {
      const r = await fetch('live/state?' + q, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(gather()) });
      if (r.ok) ok();
    } catch (_) { /* server down: the badge goes grey */ }
    inflight = false;
    if (again) { again = false; markDirty(); }
  }
  setInterval(postState, HEARTBEAT_MS);

  // ---- commands in
  const handlers = {
    say: (c) => say(c.text, c.seconds, c.from),
    async goto(c) {
      if (presenting()) throw new Error('not in XR');
      if (!c.position || !c.target) throw new Error('goto needs position and target');
      if (desktop.walk.on) await desktop.setWalk(false, 'claude');
      const arrived = await desktop.flyTo({ position: b2tPos(c.position), target: b2tPos(c.target), seconds: c.seconds ?? 1.2 });
      return { arrived };
    },
    async goto_camera(c) {
      if (presenting()) throw new Error('not in XR');
      const it = find(c.name || c.camera);
      if (it.type !== 'CAMERA') throw new Error(`"${it.name}" is a ${it.type}, not a camera`);
      if (desktop.walk.on) await desktop.setWalk(false, 'claude');
      const o = it.obj, p = o.getWorldPosition(new THREE.Vector3()), qw = o.getWorldQuaternion(new THREE.Quaternion());
      const target = p.clone().addScaledVector(o.getWorldDirection(new THREE.Vector3()), 2);
      const arrived = await desktop.flyTo({ position: p, quaternion: qw, target, seconds: c.seconds ?? 1.2 });
      desktop.lookThrough(it, 'claude');
      return { arrived, camera: it.name };
    },
    async focus(c) {
      if (presenting()) throw new Error('not in XR');
      const it = find(c.object);
      const arrived = await desktop.frameItem(it, c.seconds ?? 1.0, 'claude');
      return { arrived, ...desc(it) };
    },
    select: (c) => {
      if (c.object == null) { ed.select(null, 'claude'); return { selected: null }; }
      const it = find(c.object);
      ed.select(it, 'claude');
      return desc(it);
    },
    deselect: () => { ed.select(null, 'claude'); return { selected: null }; },
    highlight: (c) => highlight([c.objects || c.object].flat(), c.color || undefined, c.seconds ?? 6),
    marker: (c) => {             // c.id is the command id (the server sets it); the pin's own id is marker_id
      if (c.marker_id == null || !c.position) throw new Error('marker needs marker_id and position');
      return setMarker(c.marker_id, c.position, c.label || '', c.color || undefined);
    },
    clear_markers: (c) => {
      const n = markers.size;
      clearMarkers(c.ids ? [c.ids].flat() : null);
      if (!c.ids) while (highlights.length) removeHighlight(0);
      return { cleared: n - markers.size };
    },
    undo: () => { const n = ed.undoStack.length; ed.undo(); return { undone: n > ed.undoStack.length, undo_depth: ed.undoStack.length }; },
    drop: (c) => {                 // {"type": "drop", "object": name}: stand it up and let it fall onto what is below
      const it = find(c.object), top = ed.drop(it, 'claude');
      return { object: it.name, onto_y_up: top };
    },
    set: (c) => {
      const it = find(c.object);
      const cur = ed.blenderTransform(it), t = {};
      if (c.location) t.location = c.location;
      if (c.offset) t.location = (c.location || cur.location).map((v, i) => v + c.offset[i]);
      if (c.quaternion) t.quaternion = c.quaternion;
      if (c.scale && !it.aimed) t.scale = c.scale;
      if (!Object.keys(t).length) throw new Error('set needs location, offset, quaternion or scale');
      // trial: a test move that never reaches edits.json (the film assistant, 2026-10-05: a test camera move autosaved
      // into the file the build reads). Where it was before the first trial move is what saves, until a real edit
      if (c.trial && !it.obj.userData.trialHome) it.obj.userData.trialHome = { changed: ed.changed(it), t: ed.blenderTransform(it) };
      ed.beginEdit(c.trial ? 'claude-trial' : 'claude');
      ed.setBlenderWorld(it, t);
      ed.endEdit();
      if (ed.selected) ed.outline.setFromObject(ed.selected.obj);
      return { object: it.name, ...tf(ed.blenderTransform(it)), ...(it.obj.userData.trialHome ? { trial: true } : {}) };
    },
    light: (c) => {
      const it = find(c.name || c.light);
      if (!it.light) throw new Error(`"${it.name}" is not a light`);
      ed.beginEdit('claude');
      if (c.energy != null) ed.setEnergy(it, +c.energy);
      if (c.color) ed.setLightColor(it, lin(c.color));
      ed.endEdit();
      return { light: it.name, energy: rn(ed.energy(it), 4), color: rv(rgbOf(it.light.color)) };
    },
    async walk(c) {
      if (presenting()) throw new Error('not in XR');
      await desktop.setWalk(!!c.on, 'claude');
      return { mode: desktop.mode() };
    },
    look_through: (c) => {
      const it = c.name ? find(c.name) : null;
      desktop.lookThrough(it, 'claude');
      return { mode: desktop.mode(), camera: desktop.view.through ? desktop.view.through.name : null };
    },
    snapshot: async () => desktop.snapshot(),
    reload: () => hotReload('command'),
  };

  // a real edit of a thing moved on trial (by hand, by an agent's plain set, by undo past it) makes it save again
  ed.addEventListener('edited', (e) => {
    if (e.via === 'claude-trial') return;
    for (const m of e.moved || []) if (m.it) { delete m.it.obj.userData.trialHome; delete m.it.obj.userData.behaviourHome; }
  });

  // many commands as one (stage_batch): run in order inside one command, so the ones that answer at once land in the
  // same frame; with stop_on_error the first failure stops the rest. Replies [{type, ok, result | error, ms}].
  handlers.batch = async (c) => {
    const out = [];
    for (const x of Array.isArray(c.cmds) ? c.cmds : []) {
      const t0 = performance.now();
      try {
        const h = x && x.type !== 'batch' ? handlers[x.type] : null;
        if (!h) throw new Error(`unknown command "${x && x.type}"`);
        out.push({ type: x.type, ok: true, result: await h(x), ms: Math.round(performance.now() - t0) });
      } catch (e) {
        out.push({ type: x && x.type, ok: false, error: e.message || String(e), ms: Math.round(performance.now() - t0) });
        if (c.stop_on_error !== false) break;
      }
    }
    return out;
  };

  const queue = [];
  let running = false, since = -1;
  async function run() {
    if (running) return;
    running = true;
    while (queue.length) {
      const c = queue.shift(), t0 = performance.now();
      let result, error = null;
      try {
        const h = handlers[c.type];
        if (!h) throw new Error(`unknown command "${c.type}"`);
        result = await h(c);
      } catch (e) { error = e.message || String(e); }
      emit('cmd_done', { cmd_id: c.id, cmd: c.type, ok: !error, ...(error ? { error } : { result }), ms: Math.round(performance.now() - t0) });
      markDirty();
    }
    running = false;
  }
  // polled from a timer (not the render loop), one request at a time, each with a 5 s timeout. A hidden tab still
  // receives commands (browsers slow its timers to about 1/s); captions wait until the tab is visible, and flights
  // run when the render loop resumes, so a queued goto plays as soon as the user comes back.
  let polling = false;
  async function poll() {
    if (polling) return;
    polling = true;
    const ac = new AbortController(), to = setTimeout(() => ac.abort(), 5000);
    try {
      const r = await fetch(`live/cmd?${q}&since=${since}`, { signal: ac.signal });
      if (!r.ok) throw new Error(r.status);
      const j = await r.json();
      ok();
      if (since < 0 || j.last < since) since = j.last;        // first poll (old commands are not replayed), or a reset
      else {
        for (const c of j.cmds) if (c.id > since) { since = c.id; queue.push(c); }
        run();
      }
    } catch (_) { /* server down or timed out */ }
    clearTimeout(to);
    polling = false;
  }
  setInterval(poll, POLL_MS);
  poll();
  // a scene switch (scenes.js): commands, events, state and the version watch follow the page to the new scene's bus
  ed.addEventListener('switched', () => {
    q = 'scene=' + encodeURIComponent(ed.sceneName);
    since = -1;
    loadedV = seenV = null;
    gen++;
    const sn = document.getElementById('scenename');
    if (sn) sn.textContent = ed.sceneName;
    markDirty();
  });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    poll();
    run();
    pumpCaptions();
    postState();
  });

  // ---- hot reload: when Blender re-exports, swap the scene in place. The version is the two files' mtimes and
  // sizes (GET live/version; on an older server.py, HEAD requests give Last-Modified and Content-Length). A new
  // version must read the same twice in a row, 1.5 s apart, and the manifest must not be older than the glb (the
  // bridge writes scene.glb first and manifest.json last, so a newer glb means an export still in progress). Never in
  // the middle of a drag, never in VR (it waits for both to end).
  const VERSION_MS = 1500;
  // the page's own code: the first stamp is what this page runs; a new stamp seen twice in a row is an update
  // (every new stamp fires again, so the card can count updates that pile up while the user stays in)
  const codeV = { loaded: null, seen: null, ready: false, notified: null, on: [], first: [] };
  function code(v) {
    if (!v) return;
    if (codeV.loaded === null) { codeV.loaded = codeV.seen = v; for (const fn of codeV.first) fn(v); return; }
    const stable = v === codeV.seen;
    codeV.seen = v;
    if (v !== codeV.loaded && stable && v !== codeV.notified) {
      codeV.ready = true; codeV.notified = v;
      emit('code_update', { state: 'ready', running: codeV.loaded, new: v });
      for (const fn of codeV.on) fn(v);
    }
  }
  // who is listening (the server's presence: an agent following the live log) and which server process answers; the
  // user spent nine minutes in VR talking to nobody (2026-10-04), so the headset shows it
  const heard = { listening: null, server: null, on: [] };
  function presence(j) {
    if (!Array.isArray(j.listening)) return;
    const was = heard.listening, wasSrv = heard.server, now = JSON.stringify(j.listening);
    if (now === was && j.server === wasSrv) return;
    heard.listening = now; heard.server = j.server;
    const ch = { listening: j.listening, first: was === null, server_changed: wasSrv !== null && j.server !== wasSrv };
    for (const fn of heard.on) fn(ch);
  }
  let loadedV = null, seenV = null, reloading = false, versionPath = 'live';
  let wrapSwap = null;                                       // scenes.js: run a reload under the construct
  async function readVersion() {
    if (versionPath === 'live') {
      const r = await fetch(`live/version?${q}`, { cache: 'no-store' });
      if (r.ok) {
        const j = await r.json();
        if (!j.glb || !j.manifest) throw new Error('missing file');
        if (j.code_name) codeV.name = j.code_name;
        code(j.code);
        presence(j);
        return { key: JSON.stringify([j.glb, j.manifest]), complete: j.manifest[0] >= j.glb[0] };
      }
      if (r.status !== 404) throw new Error(r.status);
      versionPath = 'head';                                  // server.py from before /live/version
    }
    const base = `scenes/${encodeURIComponent(ed.sceneName)}/`;
    const hs = await Promise.all(['scene.glb', 'manifest.json'].map((f) => fetch(base + f, { method: 'HEAD', cache: 'no-store' })));
    if (!hs.every((h) => h.ok)) throw new Error('missing file');
    const lm = hs.map((h) => Date.parse(h.headers.get('Last-Modified')));       // 1 s resolution
    return { key: JSON.stringify(hs.map((h) => [h.headers.get('Last-Modified'), h.headers.get('Content-Length')])),
      complete: lm[1] >= lm[0] };
  }
  function toast(text) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.classList.add('show'), 30);
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 400); }, 3000);
  }
  async function hotReload(why) {
    if (reloading) throw new Error('a reload is already running');
    reloading = true;
    const v = await readVersion().then((x) => x.key).catch(() => null);
    try {
      const info = await (wrapSwap ? wrapSwap(() => ed.reload(Date.now())) : ed.reload(Date.now()));
      loadedV = seenV = v;
      // highlights point at the old objects: carry them over by name
      for (let i = highlights.length - 1; i >= 0; i--) {
        const h = highlights[i], it = ed.byName.get(h.it.name);
        if (it) h.it = it; else removeHighlight(i);
      }
      const st = ed.selftest();
      const res = { ...info, why, mode: desktop.mode(), selftest: st.pass ? 'PASS' : 'FAIL', checked: st.checked,
        selftest_fails: st.fails.map((f) => f.name), version: v };
      emit('scene_reload', res);
      toast('scene updated' + (info.dropped.length ? ` (${info.dropped.length} unsaved edits dropped)` : ''));
      markDirty();
      return res;
    } catch (e) {
      loadedV = v;                                           // do not retry the same broken files every 1.5 s
      emit('scene_reload_failed', { why, error: e.message || String(e) });
      ed.setStatus('scene reload failed: ' + (e.message || e));
      throw e;
    } finally {
      reloading = false;
    }
  }
  let gen = 0;                                             // bumped by a scene switch: a read from before it is stale
  async function watch() {
    if (!ed.loaded) return;                                // the first scene is still coming in (construct.js)
    if (reloading) return;
    const g0 = gen;
    let r;
    try { r = await readVersion(); } catch (_) { return; }
    if (g0 !== gen) return;
    const v = r.key;
    if (loadedV === null) { loadedV = seenV = v; return; }  // first read after page load is the baseline
    const stable = v === seenV;
    seenV = v;
    if (v === loadedV || !stable || !r.complete) return;
    if (ed.editing || desktop.tc.dragging) return;   // after the drag (in VR too: the user edits the club from inside it)
    hotReload('re-export').catch(() => {});
  }
  setInterval(watch, VERSION_MS);
  watch();

  function pageLoaded(st) {
    emit('page_load', { scene: ed.sceneName, url: location.href, objects: ed.items.length,
      groups: ed.items.filter((it) => it.children.length).map((it) => it.name),
      selftest: st.pass ? 'PASS' : 'FAIL', checked: st.checked,
      edits_loaded: ed.loadedEdits ? Object.fromEntries(Object.entries(ed.loadedEdits).map(([k, v]) => [k, Object.keys(v || {}).length])) : null });
    markDirty();
  }

  const setWrapSwap = (fn) => { wrapSwap = fn; };
  return { pageId, emit, onEmit: (fn) => watchers.push(fn), gather, postState, handlers, say, setMarker, clearMarkers, highlight, markers, highlights, pageLoaded, hotReload, codeV, heard, setWrapSwap };
}
