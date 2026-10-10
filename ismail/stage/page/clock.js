// The stage clock and 4D editing (the user, 2026-10-03: "4-D tree growth editing, and normal physical space 4D
// animation editing ... should work like the rest of the animation stuff"). One clock per scene, t in seconds over a
// span; everything that changes over time reads it:
//   object keys   any object can carry keyframes (Blender world transforms, the same values as edits.json); between
//                 keys it eases (smoothstep) and turns (slerp). With AUTO on, every move the user finishes keys that
//                 object at the playhead, the way Blender's auto-keying works.
//   growth        meshes that carry growth attributes (grow_tree.py's VR export: _born, _axis, _rad on the wood,
//                 _born, _pivot on the leaf cards) grow in the vertex shader from one uniform: scrubbing a tree's life
//                 costs nothing per frame. Each tree maps the clock through its own [t0, t1] (anim.growth).
// The timeline: a bar that floats in front of the user (the T button on the menu, or live `timeline`): tap or drag
// along it to scrub, buttons to play, step, key, toggle AUTO, save. Saved to scenes/<scene>/anim.json (POST /anim),
// read by blue_front_block.py for renders (BF_ANIM) so the moves made in the headset are the moves in the shot.
// Live: clock {action: play|pause|seek|rate|span, t, rate, span}, key {name, t?} or {keys, replace?, interp?}, key_delete {name, t?},
// anim_save, anim_clear {name?}, key_interp {name, mode}, timeline {show}, growth {name, t0, t1}. Events: clock {t, playing}, keyed, anim_saved.
import * as THREE from 'three';
import { GIZMO } from './editor.js';
import { world } from './world.js';

const W = 0.9, H = 0.16, CW = 1800, CH = 320;                  // the bar in metres and its canvas
const BTN = [['play', '▶'], ['back', '⏮'], ['step-', '◀'], ['step+', '▶▶'], ['key', '◆'], ['auto', 'AUTO'], ['save', 'SAVE']];
import { vec, segment, slerpK, lookQuat } from './interp.js';

export function initClock(ed, live, xrApi, panels) {
  const scn = () => ed.sceneName;                      // live: scenes.js can switch it
  const st = { t: 0, playing: false, rate: 1, auto: false, dirty: false };
  let anim = { span: [0, 30], objects: {}, growth: {} };
  const growers = [];                                           // {root name, uniform}

  // ---- keys
  const keysOf = (name) => (anim.objects[name] = anim.objects[name] || []);
  function key(name, t = st.t) {
    const it = ed.byName.get(name);
    if (!it) throw new Error('no object ' + name);
    const tr = ed.blenderTransform(it), ks = keysOf(name);
    const r = (a) => a.map((v) => +v.toFixed(6));
    const k = { t: +t.toFixed(3), location: r(tr.location), quaternion: r(tr.quaternion), scale: r(tr.scale) };
    const i = ks.findIndex((x) => Math.abs(x.t - k.t) < 1e-3);
    if (i >= 0) ks[i] = k; else { ks.push(k); ks.sort((a, b) => a.t - b.t); }
    st.dirty = true;
    live.emit('keyed', { name, t: k.t, keys: ks.length });
    draw();
    return { name, t: k.t, keys: ks.length };
  }
  // many keys in one command, from values (Film, 2026-10-10: a camera move or an object's path in one go, without
  // moving the thing to each spot first): [{name, t, location?, quaternion?, scale?, look?}], Blender world values;
  // what a key does not give is the object's transform now; look (a Blender point) aims the key's -Z at it, world Z
  // up (a camera or a light). replace: names whose keys go first; interp: {name: 'stop' | 'smooth'}. Every key is
  // checked before any is written. The span grows to take keys past its end.
  function keyMany(list, { replace = [], interp = {} } = {}) {
    const fin = (v, n) => Array.isArray(v) && v.length === n && v.every((x) => typeof x === 'number' && Number.isFinite(x));
    list.forEach((k, i) => {
      if (!k || !ed.byName.get(k.name)) throw new Error(`key ${i}: no object ${k && k.name}`);
      if (typeof k.t !== 'number' || !Number.isFinite(k.t) || k.t < 0) throw new Error(`key ${i} (${k.name}): t is seconds, 0 or more`);
      for (const [f, n] of [['location', 3], ['quaternion', 4], ['scale', 3], ['look', 3]]) {
        if (k[f] != null && !fin(k[f], n)) throw new Error(`key ${i} (${k.name}): ${f} is ${n} numbers`);
      }
      if (k.look && k.quaternion) throw new Error(`key ${i} (${k.name}): look or quaternion, not both`);
    });
    for (const [n, m] of Object.entries(interp)) if (!['stop', 'smooth'].includes(m)) throw new Error(`interp ${n}: 'stop' or 'smooth'`);
    for (const n of replace) delete anim.objects[n];
    const r = (a) => a.map((v) => +v.toFixed(6)), touched = new Set();
    for (const k of list) {
      const tr = ed.blenderTransform(ed.byName.get(k.name)), ks = keysOf(k.name);
      const location = k.location || tr.location;
      const nk = { t: +k.t.toFixed(3), location: r(location), quaternion: r(k.look ? lookQuat(location, k.look) : k.quaternion || tr.quaternion),
        scale: r(k.scale || tr.scale) };
      const i = ks.findIndex((x) => Math.abs(x.t - nk.t) < 1e-3);
      if (i >= 0) ks[i] = nk; else { ks.push(nk); ks.sort((a, b) => a.t - b.t); }
      if (nk.t > anim.span[1]) anim.span[1] = nk.t;
      touched.add(k.name);
    }
    if (Object.keys(interp).length) {
      anim.interp = anim.interp || {};
      for (const [n, m] of Object.entries(interp)) { if (m === 'stop') delete anim.interp[n]; else anim.interp[n] = m; }
    }
    st.dirty = true;
    apply(); draw();
    const keys = Object.fromEntries([...touched].map((n) => [n, anim.objects[n].length]));
    live.emit('keyed', { names: keys, many: list.length });
    return { keyed: list.length, keys, span: anim.span };
  }
  function keyDelete(name, t) {
    const ks = anim.objects[name];
    if (!ks) return { deleted: 0 };
    const before = ks.length;
    anim.objects[name] = t === undefined ? [] : ks.filter((x) => Math.abs(x.t - t) > 1e-3);
    if (!anim.objects[name].length) delete anim.objects[name];
    st.dirty = true; draw();
    return { deleted: before - (anim.objects[name] || []).length };
  }
  const qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
  // between keys: "stop" eases each segment, "smooth" glides through the keys (interp.js has the exact math)
  function sample(ks, t, mode = 'stop') {
    if (t <= ks[0].t) return ks[0];
    if (t >= ks[ks.length - 1].t) return ks[ks.length - 1];
    const { i, u } = segment(ks, t), a = ks[i], b = ks[i + 1], k = slerpK(u, mode);
    qa.set(a.quaternion[1], a.quaternion[2], a.quaternion[3], a.quaternion[0]);
    qb.set(b.quaternion[1], b.quaternion[2], b.quaternion[3], b.quaternion[0]);
    if (qa.dot(qb) < 0) qb.set(-qb.x, -qb.y, -qb.z, -qb.w);
    qa.slerp(qb, k);
    return { location: vec(ks, t, 'location', mode), quaternion: [qa.w, qa.x, qa.y, qa.z], scale: vec(ks, t, 'scale', mode) };
  }
  function apply() {
    for (const [name, ks] of Object.entries(anim.objects)) {
      if (ks.length < 2) continue;                              // one key holds nothing: the object stays where it is
      const it = ed.byName.get(name);
      if (!it || (ed.editing && ed.selected === it)) continue;  // never fight the hand that holds it
      ed.setBlenderWorld(it, sample(ks, st.t, (anim.interp || {})[name]));
    }
    for (const g of growers) {
      const sp = anim.growth[g.name] || { t0: anim.span[0], t1: anim.span[1] };
      g.u.value = THREE.MathUtils.clamp((st.t - sp.t0) / Math.max(1e-3, sp.t1 - sp.t0), 0, 1);
    }
  }

  // ---- no tree inside a building: a fragment of a tree that falls inside one of these (Blender x0 x1 y0 y1 z0 z1, up to
  // the eaves; above them the roof hides it) is not drawn (the user in VR, 2026-10-03: branches came through the storage
  // room's ceiling and the posters). The trees themselves should grow around the buildings (grow_tree.py); this is the
  // stage's safety net until then.
  // the boxes come from the scene's world.json (keep_out); with none, one empty box keeps the shader valid
  function keepOut() {
    const ko = world().keep_out && world().keep_out.length ? world().keep_out : [[1e9, -1e9, 1e9, -1e9, 1e9, -1e9]];
    return { n: ko.length, min: ko.map(([x0, , , y1, z0]) => new THREE.Vector3(x0, z0, -y1)),     // Blender -> three (x, z, -y)
      max: ko.map(([, x1, y0, , , z1]) => new THREE.Vector3(x1, z1, -y0)) };
  }

  // ---- growth: patch the materials of meshes that carry the attributes, once per tree (its top-level item)
  // root: only under it (trees.js patches a tree before it is staged, so its growth shaders are compiled with the rest
  // before it shows: patched after, every tree's programs compiled in one drawn frame, 1.6 s on the Quest)
  function patchGrowth(root = ed.scene) {
    const done = new Set();
    root.traverse((o) => {
      const at = o.isMesh && o.geometry && o.geometry.attributes;
      if (!at || !at._born || done.has(o) || o.userData.growPatched) return;
      done.add(o);
      o.userData.growPatched = true;
      const it = ed.itemOf(o), name = it ? (it.path ? it.path[0].name : it.name) : o.name;
      let g = growers.find((x) => x.name === name);
      if (!g) { g = { name, u: { value: 1 } }; growers.push(g); }
      const wood = !!at._axis, card = !!at._pivot, from = wood && !!at._from;
      o.material = o.material.clone();
      const KO = keepOut(), KO_GLSL = `uniform vec3 uKoMin[${KO.n}]; uniform vec3 uKoMax[${KO.n}]; varying vec3 vKoW;`;
      o.material.onBeforeCompile = (sh) => {
        sh.uniforms.uGrow = g.u;
        sh.uniforms.uKoMin = { value: KO.min };
        sh.uniforms.uKoMax = { value: KO.max };
        sh.fragmentShader = sh.fragmentShader.replace('#include <common>', `#include <common>
${KO_GLSL}`).replace('#include <clipping_planes_fragment>', `#include <clipping_planes_fragment>
for (int i = 0; i < ${KO.n}; i++) if (all(greaterThan(vKoW, uKoMin[i])) && all(lessThan(vKoW, uKoMax[i]))) discard;`);
        sh.vertexShader = sh.vertexShader.replace('#include <common>', `#include <common>
${KO_GLSL}
uniform float uGrow; attribute float _born;
${wood ? 'attribute vec3 _axis; attribute vec4 _rad;' : ''}${from ? 'attribute vec3 _from;' : ''}${card ? 'attribute vec3 _pivot;' : ''}`)
          .replace('#include <begin_vertex>', `#include <begin_vertex>
{ float on = clamp((uGrow - _born) / 0.04, 0.0, 1.0);
${wood ? `  float g = uGrow; float rr = g < 0.25 ? _rad.x * g / 0.25 : g < 0.5 ? mix(_rad.x, _rad.y, (g - 0.25) / 0.25)
    : g < 0.75 ? mix(_rad.y, _rad.z, (g - 0.5) / 0.25) : mix(_rad.z, _rad.w, (g - 0.75) / 0.25);
  vec3 ax = ${from ? 'mix(_from, _axis, on)' : '_axis'};   // a new segment slides out of the node it grew from
  transformed = ax + (transformed - _axis) * rr * on;` : ''}
${card ? '  transformed = _pivot + (transformed - _pivot) * smoothstep(_born, _born + 0.06, uGrow);' : ''}
${!wood && !card ? '  transformed *= on;' : ''} }`)
          .replace('#include <project_vertex>', `#include <project_vertex>
vKoW = (modelMatrix * vec4(transformed, 1.0)).xyz;`);
      };
      o.material.customProgramCacheKey = () => 'grow' + (wood ? 'w' : '') + (from ? 'f' : '') + (card ? 'c' : '') + 'ko' + KO.n;
      o.frustumCulled = false;                                  // the bounds are the grown tree's; a sapling sits inside them
    });
    return growers.map((g) => g.name);
  }

  // ---- the bar
  const cv = document.createElement('canvas');
  cv.width = CW; cv.height = CH;
  const ctx = cv.getContext('2d');
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  const bar = new THREE.Mesh(new THREE.PlaneGeometry(W, H), new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false, depthTest: false }));
  bar.renderOrder = 999;
  bar.layers.set(GIZMO);
  bar.visible = false;
  bar.name = 'timeline';
  const TRACK = { x0: 60, x1: CW - 60, y0: 40, y1: 150 };
  const btnRects = BTN.map(([id, label], i) => ({ id, label, x: 60 + i * ((CW - 120) / BTN.length), y: 190, w: (CW - 120) / BTN.length - 16, h: 100 }));
  const tOf = (u) => anim.span[0] + THREE.MathUtils.clamp((u * CW - TRACK.x0) / (TRACK.x1 - TRACK.x0), 0, 1) * (anim.span[1] - anim.span[0]);
  const xOf = (t) => TRACK.x0 + (t - anim.span[0]) / (anim.span[1] - anim.span[0]) * (TRACK.x1 - TRACK.x0);
  let hoverId = null;
  function draw() {
    if (!bar.visible) return;
    ctx.clearRect(0, 0, CW, CH);
    ctx.fillStyle = 'rgba(12,14,20,0.82)'; ctx.fillRect(0, 0, CW, CH);
    ctx.fillStyle = '#2a3140'; ctx.fillRect(TRACK.x0, TRACK.y0, TRACK.x1 - TRACK.x0, TRACK.y1 - TRACK.y0);
    for (let s = Math.ceil(anim.span[0]); s <= anim.span[1]; s++) {         // seconds, every fifth one labelled
      const x = xOf(s);
      ctx.fillStyle = s % 5 ? '#55607a' : '#b6c2dd';
      ctx.fillRect(x - 1, TRACK.y1 - (s % 5 ? 18 : 34), 2, s % 5 ? 18 : 34);
      if (s % 5 === 0) { ctx.font = '26px sans-serif'; ctx.fillText(String(s), x + 6, TRACK.y1 - 10); }
    }
    const sel = ed.selected && ed.selected.name;
    for (const [name, ks] of Object.entries(anim.objects)) {               // keys: the selected object's bright
      for (const k of ks) {
        const x = xOf(k.t), y = TRACK.y0 + 30;
        ctx.fillStyle = name === sel ? '#ffd23f' : '#7c86a0';
        ctx.beginPath(); ctx.moveTo(x, y - 16); ctx.lineTo(x + 12, y); ctx.lineTo(x, y + 16); ctx.lineTo(x - 12, y); ctx.fill();
      }
    }
    const px = xOf(st.t);
    ctx.fillStyle = '#ff5a4f'; ctx.fillRect(px - 3, TRACK.y0 - 14, 6, TRACK.y1 - TRACK.y0 + 28);
    ctx.font = 'bold 34px sans-serif'; ctx.fillStyle = '#fff';
    ctx.fillText(`${st.t.toFixed(2)} s` + (sel ? `   ${sel}` : '') + (st.dirty ? '   (unsaved)' : ''), TRACK.x0, 32);
    for (const b of btnRects) {
      const on = (b.id === 'auto' && st.auto) || (b.id === 'play' && st.playing);
      ctx.fillStyle = on ? '#d9480f' : b.id === hoverId ? '#3b4a66' : '#263042';
      ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.fillStyle = '#fff'; ctx.font = 'bold 44px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText(b.id === 'play' && st.playing ? '❚❚' : b.label, b.x + b.w / 2, b.y + 66);
      ctx.textAlign = 'left';
    }
    tex.needsUpdate = true;
  }
  const btnAt = (uv) => { const x = uv.x * CW, y = (1 - uv.y) * CH; return btnRects.find((b) => x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h); };
  const onTrack = (uv) => { const y = (1 - uv.y) * CH; return y >= TRACK.y0 - 20 && y <= TRACK.y1 + 20; };
  function button(id) {
    if (id === 'play') st.playing = !st.playing;
    else if (id === 'back') seek(anim.span[0]);
    else if (id === 'step-' || id === 'step+') {
      const all = [...new Set(Object.values(anim.objects).flat().map((k) => k.t))].sort((a, b) => a - b);
      const nxt = id === 'step+' ? all.find((t) => t > st.t + 1e-3) : all.reverse().find((t) => t < st.t - 1e-3);
      seek(nxt ?? st.t + (id === 'step+' ? 1 : -1));
    } else if (id === 'key' && ed.selected) key(ed.selected.name);
    else if (id === 'auto') st.auto = !st.auto;
    else if (id === 'save') save();
    live.emit('clock', { t: st.t, playing: st.playing, auto: st.auto, button: id });
    draw();
  }
  const api = {
    press(uv) {
      const b = btnAt(uv);
      if (b) { button(b.id); return true; }
      if (onTrack(uv)) { seek(tOf(uv.x)); return false; }      // false: the ray keeps hold, dragTo scrubs
      return true;
    },
    hover(uv) { const b = uv && btnAt(uv); const id = b ? b.id : null; if (id !== hoverId) { hoverId = id; draw(); } },
    dragTo(pos) {                                               // the ray's point, projected onto the bar
      const p = bar.worldToLocal(pos.clone());
      seek(tOf(p.x / W + 0.5));
    },
  };
  bar.userData.panelApi = api;
  ed.scene.add(bar);
  if (xrApi && xrApi.addTarget) xrApi.addTarget(bar);
  if (panels && panels.registerPokeable) panels.registerPokeable(bar, (uv) => api.press(uv), (uv) => api.hover(uv));

  function show(on = true) {
    bar.visible = on;
    if (on) {                                                   // in front, a little below the eyes, facing them
      const head = ed.camera.getWorldPosition(new THREE.Vector3());
      const fwd = ed.camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
      bar.position.copy(head).addScaledVector(fwd, 0.6).add(new THREE.Vector3(0, -0.3, 0));
      bar.lookAt(head.x, bar.position.y, head.z);
      draw();
    }
    return { shown: on };
  }
  function seek(t) {
    st.t = THREE.MathUtils.clamp(t, anim.span[0], anim.span[1]);
    apply(); draw();
    live.emit('clock', { t: +st.t.toFixed(3), playing: st.playing });
    return { t: st.t };
  }
  let last = performance.now(), lastDraw = 0;
  ed.preRender.push(() => {
    const now = performance.now(), dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    if (!st.playing) return;
    st.t += dt * st.rate;
    if (st.t > anim.span[1]) st.t = anim.span[0];             // loops
    apply();
    if (now - lastDraw > 100) { lastDraw = now; draw(); }
  });
  ed.addEventListener('edited', (e) => {                       // AUTO: a finished move keys what moved
    if (!st.auto) return;
    for (const n of e.moved || []) { const name = n.it ? n.it.name : n.name || n; if (ed.byName.has(name)) key(name); }
  });
  ed.addEventListener('select', () => draw());

  async function load() {
    const j = await fetch(`scenes/${encodeURIComponent(scn())}/anim.json`, { cache: 'no-store' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    if (j) anim = { span: j.span || [0, 30], objects: j.objects || {}, growth: j.growth || {}, interp: j.interp || {} };
    patchGrowth();
    apply();
    return { keys: Object.keys(anim.objects).length, growers: growers.map((g) => g.name) };
  }
  async function save() {
    const r = await fetch(`anim?scene=${encodeURIComponent(scn())}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(anim) });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.status);
    st.dirty = false; draw();
    live.emit('anim_saved', { objects: Object.keys(anim.objects).length, path: j.path });
    return j;
  }
  if (ed.loaded) load(); else ed.addEventListener('loaded', () => load());
  ed.addEventListener('reloaded', () => load());

  live.handlers.clock = (c) => {
    if (c.span) anim.span = c.span;
    if (c.rate !== undefined) st.rate = c.rate;
    if (c.action === 'play') st.playing = true;
    if (c.action === 'pause') st.playing = false;
    if (c.t !== undefined) seek(c.t); else draw();
    return { t: st.t, playing: st.playing, rate: st.rate, span: anim.span };
  };
  live.handlers.key = (c) => (c.keys ? keyMany(c.keys, c) : key(c.name, c.t ?? st.t));
  live.handlers.key_delete = (c) => keyDelete(c.name, c.t);
  live.handlers.anim_save = () => save();
  // key_interp {name, mode}: how this object moves between its keys ("stop" or "smooth"; interp.js), saved with anim_save
  live.handlers.key_interp = (c) => {
    if (!['stop', 'smooth'].includes(c.mode)) throw new Error("mode is 'stop' or 'smooth'");
    if (!anim.objects[c.name]) throw new Error(`${c.name} has no keys`);
    anim.interp = anim.interp || {};
    if (c.mode === 'stop') delete anim.interp[c.name]; else anim.interp[c.name] = c.mode;
    st.dirty = true; apply(); draw();
    return { name: c.name, mode: c.mode, keys: anim.objects[c.name].length };
  };
  live.handlers.anim_clear = (c) => { if (c.name) delete anim.objects[c.name]; else anim.objects = {}; st.dirty = true; draw(); return { cleared: c.name || 'all' }; };
  live.handlers.timeline = (c) => show(c.show !== false);
  live.handlers.growth = (c) => { anim.growth[c.name] = { t0: c.t0, t1: c.t1 }; st.dirty = true; apply(); return { name: c.name, ...anim.growth[c.name], growers: patchGrowth() }; };
  return { st, get anim() { return anim; }, key, seek, show, save, load, patchGrowth, bar };
}
