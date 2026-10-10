// Load sets: named groups of the room (the dancers, the band, the bar people) that can be unloaded to keep the Quest
// light while work goes on elsewhere, and loaded again (the user, 2026-10-06, voice note #19516: "have the dancers not
// loaded into the scene ... we just need to know that those other certain elements are there ... some sort of a
// placeholder pin"). world.json: sets {name: {items: [node name or glob, ...], note}}, unloaded [name, ...].
//
// An unloaded set's meshes are hidden before the room is staged (reveal.js skips hidden meshes: no shader compile, no
// texture upload, no draw, no shadow), and the GPU copies of what only it uses are freed when it is unloaded later.
// People in it (world.json actors) neither play, follow nor rest. In its place: a ghost box around each member and
// one label with the set's name, two draw calls for the whole set. Loading it stages it back in, paced like the room.
import * as THREE from 'three';
import { world } from './world.js';
import { GIZMO } from './editor.js';

export function initLoadSets(ed, live, getActors) {
  const out = new Map();                       // set name -> { items, persons, meshes, ghost }
  const globRe = (p) => new RegExp('^' + p.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');

  // a set's members on this page: matching items (each with its whole subtree) and matching people
  function members(spec) {
    const pats = ((spec && spec.items) || []).map(globRe);
    const hit = (n) => pats.some((re) => re.test(n));
    const items = ed.items.filter((it) => hit(it.name));
    const tops = items.filter((it) => !it.path.slice(0, -1).some((p) => items.includes(p)));   // a matched group covers its children
    const persons = Object.keys(world().actors || {}).filter((p) => hit(p) || tops.some((t) => t.name === p || (ed.byName.get(p) && ed.byName.get(p).path.includes(t))));
    const meshes = [];
    for (const it of tops) it.obj.traverse((o) => { if (o.isMesh || o.isPoints || o.isLine) meshes.push(o); });
    return { items: tops, persons, meshes };
  }

  function label(text, sub) {
    const cv = document.createElement('canvas'); cv.width = 512; cv.height = 128;
    const g = cv.getContext('2d');
    g.fillStyle = 'rgba(20,28,40,0.55)'; g.beginPath(); g.roundRect(4, 4, 504, 120, 24); g.fill();
    g.strokeStyle = 'rgba(150,200,255,0.8)'; g.lineWidth = 3; g.setLineDash([14, 10]); g.stroke();
    g.fillStyle = '#dcebff'; g.textAlign = 'center'; g.font = 'bold 48px sans-serif'; g.fillText(text, 256, 62);
    g.font = '28px sans-serif'; g.fillStyle = '#9fb8d8'; g.fillText(sub, 256, 104);
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
    s.scale.set(1.2, 0.3, 1);
    return s;
  }

  // a ghost box around each member and the set's name over them: one LineSegments and one Sprite
  function ghostOf(name, m, note) {
    const pos = [], all = new THREE.Box3(), b = new THREE.Box3();
    const E = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
    for (const it of m.items) {
      b.setFromObject(it.obj);
      if (b.isEmpty()) continue;
      all.union(b);
      const c = [0, 1, 2, 3, 4, 5, 6, 7].map((i) => [i & 1 ? b.max.x : b.min.x, i & 2 ? b.max.y : b.min.y, i & 4 ? b.max.z : b.min.z]);
      for (const [a, z] of E) pos.push(...c[a], ...c[z]);
    }
    const ghost = new THREE.Group();
    ghost.name = 'loadset:' + name;
    ghost.userData.gizmo = true;
    if (pos.length) {
      const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      const lines = new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x8fc4ff, transparent: true, opacity: 0.45, depthWrite: false }));
      lines.userData.gizmo = true;
      ghost.add(lines);
    }
    if (!all.isEmpty()) {
      const s = label(name, note || `unloaded: ${m.items.length} in the film`);
      s.position.set((all.min.x + all.max.x) / 2, all.max.y + 0.35, (all.min.z + all.max.z) / 2);
      s.userData.gizmo = true;
      ghost.add(s);
    }
    ghost.traverse((o) => o.layers.set(GIZMO));   // a helper: seen in the editor and the headset, never in a capture
    ed.scene.add(ghost);
    return ghost;
  }

  function dropGhost(g) {
    if (!g) return;
    g.removeFromParent();
    g.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) { if (o.material.map) o.material.map.dispose(); o.material.dispose(); } });
  }

  const tris = (o) => { const g = o.geometry; if (!g) return 0; return Math.round((g.index ? g.index.count : (g.attributes.position ? g.attributes.position.count : 0)) / 3); };

  // the people in it stop, lose their resting body, and refuse plays until the set is back
  function quietPeople(m, why) {
    const A = getActors();
    for (const p of m.persons) {
      try { if (A && A.playing && A.playing.has(p)) A.stop({ person: p, why }); } catch (e) { console.warn('[loadsets] stop', p, e); }
      try { if (A && A.unrest) A.unrest(p); } catch (_) { /* an older actors.js */ }
    }
  }

  // GPU copies only this set uses (geometry, and textures and materials no other shown mesh shares): freed; three
  // uploads them again when the set is staged back in
  function freeGpu(meshes) {
    const mine = new Set(meshes), keepMat = new Set(), keepTex = new Set();
    ed.scene.traverse((o) => {
      if (!(o.isMesh || o.isPoints || o.isLine) || mine.has(o) || !o.material) return;
      for (const m of [o.material].flat()) { keepMat.add(m); for (const v of Object.values(m)) if (v && v.isTexture) keepTex.add(v); }
    });
    const geos = new Set(), mats = new Set(), texs = new Set();
    for (const o of meshes) {
      if (o.geometry) geos.add(o.geometry);
      for (const m of [o.material].flat()) if (m && !keepMat.has(m)) { mats.add(m); for (const v of Object.values(m)) if (v && v.isTexture && !keepTex.has(v)) texs.add(v); }
    }
    for (const g of geos) g.dispose();
    for (const t of texs) t.dispose();
    for (const m of mats) m.dispose();
    return { geometries: geos.size, materials: mats.size, textures: texs.size };
  }

  function unload(name, spec, { free = true } = {}) {
    if (out.has(name)) return { name, unloaded: true, already: true };
    const m = members(spec);
    for (const it of m.items) for (const it2 of [it, ...ed.items.filter((x) => x.path.includes(it))]) it2.unloaded = name;
    m.persons.forEach((p) => { const it = ed.byName.get(p); if (it) it.unloaded = name; });
    m.unloadedPersons = new Set(m.persons);
    quietPeople(m, 'unloaded');
    if (ed.selected && ed.selected.unloaded) ed.select(null, 'unload');
    let t = 0;
    const hid = [];
    for (const o of m.meshes) if (o.visible) { o.visible = false; hid.push(o); t += tris(o); }
    for (const o of m.meshes) o.userData.loadsetHidden = true;
    const freed = free && hid.length ? freeGpu(hid) : null;
    m.ghost = ghostOf(name, m, spec && spec.note);
    out.set(name, m);
    live.emit('set_unloaded', { name, items: m.items.length, persons: m.persons, meshes: hid.length, tris: t });
    return { name, unloaded: true, items: m.items.map((it) => it.name), persons: m.persons, meshes: hid.length, tris: t, freed };
  }

  async function load(name) {
    const m = out.get(name);
    if (!m) return { name, loaded: true, already: true };
    out.delete(name);
    dropGhost(m.ghost);
    for (const it of ed.items) if (it.unloaded === name) delete it.unloaded;
    const meshes = m.meshes.filter((o) => o.userData.loadsetHidden);
    for (const o of meshes) { delete o.userData.loadsetHidden; o.visible = true; }
    // back in a piece at a time, compiled and uploaded before it shows (reveal.js), each member its own job
    let t = 0; for (const o of meshes) t += tris(o);
    await Promise.all(m.items.map((it) => ed.stage(it.obj, { parent: null, minS: 0.6, pop: false })));
    const A = getActors();
    for (const p of m.persons) if (A && A.rest) A.rest(p).catch(() => {});
    live.emit('set_loaded', { name, items: m.items.length, persons: m.persons, meshes: meshes.length, tris: t });
    return { name, loaded: true, items: m.items.map((it) => it.name), persons: m.persons, meshes: meshes.length, tris: t };
  }

  // before every staging of the room (load, hot reload, scene switch): the unloaded sets never reach the GPU
  ed.addEventListener('staging', () => {
    for (const m of out.values()) dropGhost(m.ghost);
    out.clear();
    const W = world(), sets = W.sets || {};
    ed.root && ed.root.updateMatrixWorld(true);
    for (const name of W.unloaded || []) if (sets[name]) unload(name, sets[name], { free: false });
  });

  const isUnloadedPerson = (p) => { for (const [n, m] of out) if (m.unloadedPersons.has(p)) return n; return null; };

  // page command load_set {name, loaded, set?}: set is the set's definition when the page's world is older
  live.handlers.load_set = async (c) => {
    const W = world();
    if (c.set) { W.sets = { ...(W.sets || {}), [c.name]: c.set }; }
    const spec = (W.sets || {})[c.name];
    if (!spec && c.loaded === false) throw new Error(`no load set ${c.name} (world.json sets: ${Object.keys(W.sets || {}).join(', ') || 'none'})`);
    const un = new Set(W.unloaded || []);
    if (c.loaded === false) un.add(c.name); else un.delete(c.name);
    W.unloaded = [...un];
    return c.loaded === false ? unload(c.name, spec) : load(c.name);
  };
  live.handlers.load_sets = () => state();

  function state() {
    const sets = world().sets || {};
    return Object.fromEntries(Object.keys(sets).map((n) => [n, out.has(n) ? { loaded: false, persons: out.get(n).persons, items: out.get(n).items.length } : { loaded: true }]));
  }

  return { unload, load, state, isUnloadedPerson };
}
