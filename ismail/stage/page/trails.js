// Trails: a keyed object's path drawn in the room (Nate, 2026-10-14, the v2 shot session: "mark which camera shots, and
// go over their trajectories"). For each shown object: its path between its first and last key, sampled with the
// clock's own curve (stop or smooth, so the line is the move the clock plays), coloured blue (first key) to orange
// (last); at each key a handle with its time over it (a small camera for a camera: its frustum looks where the key
// looks; a ball for anything else); and a bright ball on the path where the playhead is.
// A handle takes the ray: a pinch scrubs the clock to that key; a pinch carried moves the key there (its turn kept),
// written like any key (unsaved until SAVE or anim_save; ◆ Key at the same time replaces it). Drawn on the GIZMO
// layer: the Blender cameras and captures never see it.
//   live: trail {show?: true, names?: [...] | name?}  names left out: every keyed camera (and any keyed object shown
//         before stays shown); show false with no names hides them all. Replies {shown: [{name, keys, seconds}]}.
//   events: trail_key {name, t, location} (a handle moved a key)
import * as THREE from 'three';
import { GIZMO, b2tPos, t2bPos, b2tQuat } from './editor.js';

const STEP_S = 1 / 20, MAX_PTS = 1200, HANDLE_R = 0.06, MOVE_M = 0.02;

export function initTrails(ed, live, clock, xr) {
  const root = new THREE.Group();
  root.name = 'trails';
  ed.scene.add(root);
  const shown = new Set();               // names asked for by name
  let cams = false;                      // every keyed camera
  const drawn = new Map();               // name -> { g, handles: [mesh], head }
  const colorAt = (k) => new THREE.Color().setHSL(0.6 - 0.52 * k, 0.85, 0.6);
  const onTop = (o, order = 990) => { o.traverse((c) => { c.layers.set(GIZMO); c.renderOrder = order; c.frustumCulled = false; }); return o; };

  function names() {
    const out = new Set([...shown].filter((n) => clock.anim.objects[n]));
    if (cams) for (const n of Object.keys(clock.anim.objects)) { const it = ed.byName.get(n); if (it && it.type === 'CAMERA') out.add(n); }
    return [...out];
  }
  function dispose(name) {
    const d = drawn.get(name);
    if (!d) return;
    for (const h of d.handles) xr.removeTarget(h);
    d.g.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
    root.remove(d.g);
    drawn.delete(name);
  }
  function label(text, color) {
    const cv = document.createElement('canvas');
    cv.width = 192; cv.height = 64;
    const c = cv.getContext('2d');
    c.fillStyle = 'rgba(12,14,20,0.75)'; c.fillRect(0, 0, 192, 64);
    c.fillStyle = color; c.font = 'bold 40px sans-serif'; c.textAlign = 'center'; c.fillText(text, 96, 46);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, toneMapped: false }));
    sp.scale.set(0.15, 0.05, 1);
    return sp;
  }
  // a camera in its own Blender frame: looks down -Z, up is +Y (an aimed item's axes are kept, b2tQuat(q, true))
  function frustum(color) {
    const d = 0.16, w = 0.1, h = 0.06, c = [[-w, -h, -d], [w, -h, -d], [w, h, -d], [-w, h, -d]].map((p) => new THREE.Vector3(...p));
    const o = new THREE.Vector3(), pts = [];
    for (const p of c) pts.push(o, p);
    for (let i = 0; i < 4; i++) pts.push(c[i], c[(i + 1) % 4]);
    pts.push(new THREE.Vector3(-w / 2, h, -d), new THREE.Vector3(0, h * 1.9, -d), new THREE.Vector3(0, h * 1.9, -d), new THREE.Vector3(w / 2, h, -d));
    return new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color, toneMapped: false, transparent: true, opacity: 0.95, depthTest: false }));
  }

  function build(name) {
    dispose(name);
    const ks = clock.anim.objects[name], it = ed.byName.get(name);
    if (!ks || !ks.length || !it) return;
    const g = new THREE.Group();
    g.name = 'trail_' + name;
    const t0 = ks[0].t, t1 = ks[ks.length - 1].t, span = Math.max(1e-3, t1 - t0);
    if (ks.length > 1) {
      const n = Math.min(MAX_PTS, Math.max(2, Math.ceil(span / STEP_S) + 1)), pts = [], cols = [];
      for (let i = 0; i < n; i++) {
        const t = t0 + span * i / (n - 1), s = clock.sampleAt(name, t);
        pts.push(b2tPos(s.location));
        const c = colorAt(i / (n - 1)); cols.push(c.r, c.g, c.b);
      }
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      geo.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
      g.add(new THREE.Line(geo, new THREE.LineBasicMaterial({ vertexColors: true, toneMapped: false, transparent: true, opacity: 0.9, depthTest: false })));
    }
    const handles = [];
    ks.forEach((k) => {
      const color = colorAt(ks.length > 1 ? (k.t - t0) / span : 0);
      const at = new THREE.Group();
      at.position.copy(b2tPos(k.location));
      const ball = new THREE.Mesh(new THREE.SphereGeometry(HANDLE_R, 14, 10),
        new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.3, depthTest: false, toneMapped: false }));
      at.add(ball);
      if (it.type === 'CAMERA') {
        const f = frustum(color);
        f.quaternion.copy(b2tQuat(k.quaternion, it.aimed));
        at.add(f);
      }
      const tag = label(`${k.t.toFixed(1)} s`, '#' + color.getHexString());
      tag.position.set(0, HANDLE_R + 0.06, 0);
      at.add(tag);
      g.add(at);
      let drag = null;
      ball.userData.panelApi = {
        press() {
          clock.seek(k.t);
          drag = { off: null, start: at.position.clone(), moved: false };
          this.busy = true;
          live.emit('clock', { t: k.t, playing: clock.st.playing, trail: name });
          return false;                                           // false: the ray keeps hold; carried, it moves the key
        },
        hover(uv) { ball.material.opacity = uv ? 0.65 : 0.3; },
        dragTo(pos) {
          if (!drag) return;
          if (!drag.off) drag.off = at.position.clone().sub(pos);
          const p = pos.clone().add(drag.off);
          if (!drag.moved && p.distanceTo(drag.start) < MOVE_M) return;
          drag.moved = true;
          at.position.copy(p);
        },
        release() {
          const d = drag;
          drag = null;
          this.busy = false;
          if (!d || !d.moved) return;
          const location = t2bPos(at.position).map((v) => +v.toFixed(4));
          clock.keyMany([{ name, t: k.t, location, quaternion: k.quaternion, scale: k.scale }]);   // redraws through onChange
          live.emit('trail_key', { name, t: k.t, location });
          ed.setStatus(`${ed.label(it)}: key ${k.t.toFixed(1)} s moved (SAVE keeps it)`);
        },
      };
      handles.push(ball);
      xr.addTarget(ball);
    });
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.035, 12, 8),
      new THREE.MeshBasicMaterial({ color: 0xff5a4f, depthTest: false, toneMapped: false }));
    head.visible = ks.length > 1;
    g.add(head);
    onTop(g);
    root.add(g);
    drawn.set(name, { g, handles, head });
    place(name);
  }
  function place(name) {
    const d = drawn.get(name), s = d && d.head.visible && clock.sampleAt(name, clock.st.t);
    if (s) d.head.position.copy(b2tPos(s.location));
  }
  function redraw() {
    const want = new Set(names());
    for (const n of [...drawn.keys()]) if (!want.has(n)) dispose(n);
    for (const n of want) build(n);
  }
  // a drag in progress keeps its handle: the redraw waits for the release
  let pending = false;
  const busy = () => [...drawn.values()].some((d) => d.handles.some((h) => h.userData.panelApi && h.userData.panelApi.busy));
  clock.onChange(() => { if (!drawn.size && !cams && !shown.size) return; if (busy()) { pending = true; return; } redraw(); });
  ed.preRender.push(() => {
    if (pending && !busy()) { pending = false; redraw(); }
    for (const n of drawn.keys()) place(n);
  });

  function show(on = true, list = null) {
    if (list && list.length) {
      for (const n of list) {
        if (!ed.byName.get(n)) throw new Error('no object ' + n);
        if (on) shown.add(n); else shown.delete(n);
      }
    } else if (on) cams = true;
    else { cams = false; shown.clear(); }
    redraw();
    return { shown: [...drawn.keys()].map((n) => { const ks = clock.anim.objects[n] || []; return { name: n, keys: ks.length,
      seconds: ks.length ? +(ks[ks.length - 1].t - ks[0].t).toFixed(2) : 0 }; }) };
  }
  const isShown = (name) => drawn.has(name);
  function toggle(name) { return show(!isShown(name), [name]); }
  ed.addEventListener('switched', () => { for (const n of [...drawn.keys()]) dispose(n); shown.clear(); });
  live.handlers.trail = (c) => show(c.show !== false, c.names || (c.name ? [c.name] : null));
  return { show, toggle, isShown, redraw };
}
