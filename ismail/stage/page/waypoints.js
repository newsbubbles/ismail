// Waypoints: pins in the scene that carry a label and a note, left by Claude or by the user, kept per scene in
// scenes/<scene>/waypoints.json. The user, 2026-10-03: "be able to set me waypoints or something not too intrusive
// visually that you can use to point to things ... along with actually placing me facing something in some location
// ... leave me notes at waypoints ... TODO's etc. can be marked in the actual VR experience of the scene".
// Quiet by design: a thin stem and a small head, faint far away, brighter as you come near; the label shows when you
// are within LABEL_M or looking straight at the pin, the note when you are within NOTE_M. Poking the head opens the note
// with Done / Go / Close. The pin's colour says what it is: note amber, todo red-orange, done green, look cyan.
//   live: waypoint {wp?: its id, position, label?, note?, kind?, target?}   (Blender xyz; id defaults to a new one)
//         waypoint_remove {wp} | waypoints_clear {kind?} | waypoints_list
//         waypoint_go {wp | next: true}   stand in front of it, facing its target (or the pin)
//   events: waypoint_set, waypoint_done, waypoint_pinned (the user's "📌 Pin"), waypoint_go
import * as THREE from 'three';
import { GIZMO, b2tPos, t2bPos } from './editor.js';

const COLOR = { note: '#f5b942', todo: '#ff6b3d', done: '#5fd38d', look: '#5cc8ff' };
const STEM_H = 1.1, LABEL_M = 4.5, NOTE_M = 2.2, GAZE_DEG = 7;

// the server numbers every command in `id`, so a pin's own id travels as `wp` (or `name`); a numeric id is the command's
const widOf = (c) => (c.wp != null ? String(c.wp) : c.name != null ? String(c.name) : typeof c.id === 'string' ? c.id : null);

export function initWaypoints(ed, live, panels, hands, voice) {
  const scn = () => ed.sceneName;
  const pins = new Map();                       // id -> { w (data), g, head, stem, card, cv, tex, drawn }
  let saveT = null;

  const card = (w) => {
    const cv = document.createElement('canvas'); cv.width = 768; cv.height = 256;
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const m = new THREE.Mesh(new THREE.PlaneGeometry(0.6, 0.2), new THREE.MeshBasicMaterial({ map: tex, transparent: true,
      toneMapped: false, depthTest: false, opacity: 0 }));
    m.renderOrder = 996; m.layers.set(GIZMO); m.visible = false;
    return { m, cv, tex };
  };
  // the card grows to fit: the title and the note wrap inside it (the user, 2026-10-03: the text "clips off of the side
  // ... I can't read the full text")
  function wrapLines(c, text, maxW) {
    const out = [];
    let line = '';
    for (const wd of String(text).split(/\s+/)) {
      const t = line ? line + ' ' + wd : wd;
      if (c.measureText(t).width > maxW && line) { out.push(line); line = wd; } else line = t;
    }
    if (line) out.push(line);
    return out;
  }
  function drawCard(p, withNote) {
    const { w } = p, c = p.cv.getContext('2d'), W = 768, PAD = 36, MAXW = W - 2 * PAD - 12;
    c.font = 'bold 42px system-ui, sans-serif';
    const title = wrapLines(c, (w.kind === 'todo' ? 'TODO  ' : w.kind === 'done' ? '✓  ' : '') + (w.label || w.id), MAXW).slice(0, 2);
    c.font = '30px system-ui, sans-serif';
    const note = withNote && w.note ? wrapLines(c, w.note, MAXW).slice(0, 9) : [];
    const H = 24 + title.length * 50 + (note.length ? 14 + note.length * 38 : 0) + 20;
    if (p.cv.height !== H) {
      p.cv.height = H;
      p.card.geometry.dispose();
      p.card.geometry = new THREE.PlaneGeometry(0.6, 0.6 * H / W).translate(0, 0.3 * H / W, 0);   // grows upward from its foot
    }
    c.clearRect(0, 0, W, H);
    c.fillStyle = 'rgba(14,14,18,0.86)'; c.beginPath(); c.roundRect(4, 4, W - 8, H - 8, 22); c.fill();
    c.fillStyle = COLOR[w.kind] || COLOR.note; c.fillRect(4, 4, 12, H - 8);
    if (p.hot) { c.strokeStyle = COLOR[w.kind] || COLOR.note; c.lineWidth = 8; c.beginPath(); c.roundRect(6, 6, W - 12, H - 12, 20); c.stroke(); }
    c.textBaseline = 'top';
    let y = 22;
    c.fillStyle = '#fff'; c.font = 'bold 42px system-ui, sans-serif';
    for (const t of title) { c.fillText(t, PAD, y); y += 50; }
    if (note.length) {
      y += 14; c.fillStyle = '#ddd'; c.font = '30px system-ui, sans-serif';
      for (const t of note) { c.fillText(t, PAD, y); y += 38; }
    }
    p.tex.needsUpdate = true; p.drawn = withNote ? 'note' : 'label';
  }

  function build(w) {
    remove(w.id, true);
    const col = new THREE.Color(COLOR[w.kind] || COLOR.note);
    const g = new THREE.Group();
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.004, 0.004, STEM_H, 6).translate(0, STEM_H / 2, 0),
      new THREE.MeshBasicMaterial({ color: col, toneMapped: false, transparent: true, opacity: 0.5 }));
    const head = new THREE.Mesh(new THREE.OctahedronGeometry(0.035), new THREE.MeshBasicMaterial({ color: col, toneMapped: false, transparent: true }));
    head.position.y = STEM_H;
    const foot = new THREE.Mesh(new THREE.RingGeometry(0.05, 0.065, 24).rotateX(-Math.PI / 2),
      new THREE.MeshBasicMaterial({ color: col, toneMapped: false, transparent: true, opacity: 0.5, side: THREE.DoubleSide }));
    foot.position.y = 0.005;
    const cd = card(w);
    cd.m.position.y = STEM_H + 0.07;
    g.add(stem, head, foot, cd.m);
    g.traverse((o) => o.layers.set(GIZMO));
    g.position.copy(b2tPos(w.position));
    g.name = '_waypoint_' + w.id;
    ed.scene.add(g);
    const p = { w, g, head, stem, foot, card: cd.m, cv: cd.cv, tex: cd.tex, drawn: '', hot: false };
    pins.set(w.id, p);
    drawCard(p, false);
    // the pointer ray stops on the card (and the diamond), lights it, and a pinch opens it, so a pin can be worked
    // from a few steps away (the user, 2026-10-04, #4240: the ray went through the card onto the bottles behind)
    if (panels && panels.addRayTarget) {
      p.ray = { press: (uv, via) => { live.emit('pin_touch', { id: w.id, on: 'ray', via }); openNote(w.id); return true; },
        hover: (uv) => { const on = !!uv; if (p.hot !== on) { p.hot = on; drawCard(p, p.drawn === 'note'); } }, isControl: () => true };
      panels.addRayTarget(cd.m, p.ray); panels.addRayTarget(head, p.ray);
    }
    return p;
  }
  function remove(id, quiet) {
    const p = pins.get(id);
    if (!p) return false;
    ed.scene.remove(p.g);
    if (p.ray && panels.removeRayTarget) { panels.removeRayTarget(p.card); panels.removeRayTarget(p.head); }
    p.g.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
    pins.delete(id);
    if (!quiet) save();
    return true;
  }
  const list = () => [...pins.values()].map((p) => p.w);
  function save() {
    clearTimeout(saveT);
    // the scene and the list as they are now: a switch inside the wait never writes one scene's waypoints into another
    const url = `waypoints?scene=${encodeURIComponent(scn())}`, body = JSON.stringify(list());
    saveT = setTimeout(() => fetch(url, { method: 'POST', body })
      .catch((e) => live.emit('voice_error', { where: 'waypoints save', error: String(e.message || e) })), 400);
  }
  async function load() {
    for (const id of [...pins.keys()]) remove(id, true);
    const ws = await fetch(`waypoints?scene=${encodeURIComponent(scn())}`, { cache: 'no-store' }).then((r) => r.json()).catch(() => []);
    for (const w of Array.isArray(ws) ? ws : []) if (w && w.id && w.position) build(w);
  }

  function set(c) {
    const wid = widOf(c), old = wid && pins.get(wid);
    const w = { ...(old ? old.w : {}), ...c, id: wid || 'wp_' + Date.now().toString(36) };
    delete w.type; delete w.ts; delete w.wp;
    w.kind = w.kind || 'note'; w.by = w.by || 'claude'; w.at = w.at || new Date().toISOString();
    if (!w.position) throw new Error('waypoint needs a position (Blender xyz)');
    build(w); save();
    live.emit('waypoint_set', { id: w.id, label: w.label, kind: w.kind });
    return { id: w.id, kind: w.kind };
  }

  // stand in front of the pin, on the side the user is on, facing its target (or the pin itself)
  function go(c) {
    const wid = widOf(c);
    let p = wid ? pins.get(wid) : null;
    if (!p && c.next) p = [...pins.values()].find((q) => q.w.kind === 'todo') || [...pins.values()].find((q) => q.w.kind === 'note');
    if (!p) throw new Error(wid ? 'no waypoint ' + wid : 'no open waypoint');
    const at = new THREE.Vector3(...p.w.position);                       // Blender
    const target = p.w.target ? new THREE.Vector3(...p.w.target) : at.clone();
    let stand;
    if (p.w.stand) stand = new THREE.Vector3(...p.w.stand);
    else {
      const me = new THREE.Vector3(...t2bPos(ed.camera.getWorldPosition(new THREE.Vector3())));
      const away = me.clone().sub(at).setZ(0);
      if (away.lengthSq() < 0.01) away.set(-1, 0, 0);
      stand = at.clone().add(away.normalize().multiplyScalar(c.distance || 1.6)).setZ(at.z);
    }
    live.emit('waypoint_go', { id: p.w.id });
    return live.handlers.goto({ position: [stand.x, stand.y, stand.z], target: [target.x, target.y, target.z] });
  }

  let noteOpen = null, quietUntil = 0;
  async function openNote(id) {
    const p = pins.get(id);
    if (!p || noteOpen || performance.now() < quietUntil) return;
    noteOpen = id;
    if (voice) voice.EAR.pinOpen();
    try { await noteFor(p, id); } finally { noteOpen = null; quietUntil = performance.now() + 1500; }   // a close stays closed
  }
  async function noteFor(p, id) {
    // the note stays put beside its pin, at chest height, on the user's right of it, turned to them once; it does
    // not follow the user (the user, 2026-10-03: "it should only show up ... in a fixed location next to the pin
    // itself, and not obstructing anything")
    p.head.getWorldPosition(hp); ed.camera.getWorldPosition(eye);
    const side = new THREE.Vector3(hp.x - eye.x, 0, hp.z - eye.z).normalize().cross(new THREE.Vector3(0, 1, 0)).multiplyScalar(-0.42);
    const near = new THREE.Vector3(hp.x, Math.max(1.0, eye.y - 0.25), hp.z).add(side);
    const a = await panels.show({ panel_id: 'wp_' + id, title: (p.w.kind === 'todo' ? 'TODO: ' : '') + (p.w.label || id),
      text: p.w.note || '', buttons: [p.w.kind === 'done' ? '↺ Reopen' : '✓ Done', '➜ Next', '✕'], width: 0.36, wait: true, near, quiet: true });
    p.card.visible = false;
    const ans = a && (a.answer || a);
    if (ans === '✓ Done' || ans === '↺ Reopen') {
      const done = ans === '✓ Done';
      set({ ...p.w, kind: done ? 'done' : (p.w.was || 'todo'), was: done ? p.w.kind : undefined });
      if (voice && done) voice.EAR.pinDone();
      live.emit('waypoint_done', { id, label: p.w.label, done });
    } else if (ans === '➜ Next') {
      const open = [...pins.values()].filter((q) => q.w.kind === 'todo' && q.w.id !== id);
      if (open.length) go({ id: open[0].w.id });
    }
  }

  // the user's pin, from an object's menu: on top of the object; the voice note that follows becomes its note (Claude)
  function pinObject(it) {
    const box = new THREE.Box3().setFromObject(it.obj), top = new THREE.Vector3((box.min.x + box.max.x) / 2, box.max.y, (box.min.z + box.max.z) / 2);
    const r = set({ position: t2bPos(top), label: ed.label(it), kind: 'note', by: 'user', object: it.name });
    live.emit('waypoint_pinned', { id: r.id, object: it.name });
    return r;
  }

  // quiet by distance; the label near or in the gaze, the note close
  const eye = new THREE.Vector3(), fwd = new THREE.Vector3(), to = new THREE.Vector3(), hp = new THREE.Vector3();
  // touching a pin with an index fingertip opens its note, once per touch: the diamond (within TOUCH_M) or its card
  // (within TOUCH_M of the card's face, while the card shows). The user, 2026-10-04: "sometimes when I'm trying to touch
  // a to-do pin with my hand it does not touch": the card he reached for took no touch, and a pin stayed shut until
  // the tip had left by 18 cm. It re-arms when the tip leaves by RELEASE_M or after REARM_MS. Looking roughly toward it
  // (within 60 degrees) is still needed: a hand behind the head opened pins (2026-10-03). A tip close to a pin that
  // does not open it says why (pin_touch_missed), so a miss can be measured.
  const TOUCH_M = 0.07, RELEASE_M = 0.10, REARM_MS = 1200, GAZE_DOT = 0.5, NEAR_MISS_M = 0.12;
  const armed = new Map(), cardP = new THREE.Vector3(), missedAt = new Map();
  function cardDistance(p, tip) {
    if (!p.card.visible || p.card.material.opacity < 0.3) return Infinity;
    p.card.updateMatrixWorld();
    cardP.copy(tip); p.card.worldToLocal(cardP);
    const pg = p.card.geometry.parameters, s = p.card.scale.x;
    p.card.geometry.computeBoundingBox();
    const bb = p.card.geometry.boundingBox;
    const dx = Math.max(0, bb.min.x - cardP.x, cardP.x - bb.max.x) * s, dy = Math.max(0, bb.min.y - cardP.y, cardP.y - bb.max.y) * s;
    return Math.hypot(dx, dy, cardP.z * s);
  }
  function missed(p, side, d, why) {
    const key = side + p.w.id, now = performance.now();
    if (now - (missedAt.get(key) || 0) < 2000) return;
    missedAt.set(key, now);
    live.emit('pin_touch_missed', { id: p.w.id, hand: side, cm: Math.round(d * 100), why });
  }
  function touches() {
    if (!hands || !hands.state) return;
    const now = performance.now();
    for (const side of ['left', 'right']) {
      const f = hands.state[side] && hands.state[side].f;
      if (!f || !f.indexTip) continue;
      for (const p of pins.values()) {
        p.head.getWorldPosition(hp);
        const dHead = hp.distanceTo(f.indexTip), dCard = cardDistance(p, f.indexTip), d = Math.min(dHead, dCard);
        const key = side + p.w.id, a = armed.get(key) ?? true;
        if (d > RELEASE_M || (a !== true && now - a > REARM_MS)) armed.set(key, true);
        else if (!armed.has(key)) armed.set(key, true);
        if (d >= TOUCH_M) continue;
        if (noteOpen) { if (noteOpen !== p.w.id) missed(p, side, d, 'another note is open'); continue; }
        if (now < quietUntil) { missed(p, side, d, 'just closed a note'); continue; }
        if (armed.get(key) !== true) { missed(p, side, d, 'touched a moment ago: pull back 10 cm'); continue; }
        to.copy(dHead <= dCard ? hp : f.indexTip).sub(eye).normalize();
        if (fwd.dot(to) < GAZE_DOT) { missed(p, side, d, 'not looking toward it'); continue; }
        armed.set(key, now);
        live.emit('pin_touch', { id: p.w.id, hand: side, on: dHead <= dCard ? 'diamond' : 'card', cm: Math.round(d * 100) });
        openNote(p.w.id);
        return;
      }
    }
  }
  // a fingertip at a pin is a touch, not a travel (hands.js: a poke at UI wins over travel)
  if (hands && hands.addUIGuard) hands.addUIGuard((side, tip) => {
    for (const p of pins.values()) {
      p.head.getWorldPosition(hp);
      const d = Math.min(hp.distanceTo(tip), cardDistance(p, tip));
      if (d < 0.10) return { why: 'a pin is within 10 cm', cm: Math.round(d * 100) };
    }
    return null;
  });
  ed.preRender.push(function waypointsFrame() {
    if (!pins.size) return;
    ed.camera.getWorldPosition(eye); ed.camera.getWorldDirection(fwd);
    touches();
    const t = performance.now() / 1000;
    for (const p of pins.values()) {
      p.head.getWorldPosition(hp);
      const d = hp.distanceTo(eye);
      const near = THREE.MathUtils.clamp(1 - (d - 3) / 22, 0.12, 1);             // faint far away
      p.head.material.opacity = near;
      p.stem.material.opacity = 0.45 * near;
      p.foot.material.opacity = 0.45 * near;
      p.head.rotation.y = t * 0.8;
      to.copy(hp).sub(eye).normalize();
      const gaze = THREE.MathUtils.radToDeg(Math.acos(THREE.MathUtils.clamp(fwd.dot(to), -1, 1))) < GAZE_DEG;
      const show = d < LABEL_M || gaze;
      p.card.visible = show;
      if (show) {
        const wantNote = d < NOTE_M && !!p.w.note;
        if (p.drawn !== (wantNote ? 'note' : 'label')) drawCard(p, wantNote);
        p.card.scale.setScalar(THREE.MathUtils.clamp(d / 2.5, 0.6, 3));        // readable from farther away when gazed at
        p.card.material.opacity = Math.min(1, p.card.material.opacity + 0.08);
        p.card.lookAt(eye);
      } else p.card.material.opacity = 0;
    }
  });

  live.handlers.waypoint = (c) => set(c);
  live.handlers.waypoint_remove = (c) => ({ removed: remove(widOf(c)) });
  live.handlers.waypoints_clear = (c) => { let n = 0; for (const p of [...pins.values()]) if (!c.kind || p.w.kind === c.kind) { remove(p.w.id, true); n++; } save(); return { cleared: n }; };
  live.handlers.waypoints_list = () => list();
  live.handlers.waypoint_go = (c) => go(c);
  ed.addEventListener('switched', () => load());
  load();
  return { set, remove, go, list, pinObject, load, pins };
}
