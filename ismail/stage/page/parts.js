// Worn parts of a body (stage_actor_part): a piece of costume that is its own mesh in the body's file (glasses, a
// mask, a hat), skinned to one bone, can be hidden, or slid off where it sits and hidden at the end, over a few
// seconds, and put back. The piece is swapped for a plain copy riding that bone, so it keeps nodding with the head
// while it moves. Kept per person: a rest or a take that rebuilds the body shows the pieces the same way.
import * as THREE from 'three';

const ease = (x) => { x = Math.min(Math.max(x, 0), 1); return x * x * (3 - 2 * x); };

export function initParts(ed, live, getActors) {
  const state = new Map();                     // person -> Map(part -> {show, from, to, t0, ms, end})
  const built = new WeakMap();                 // a body's root -> Map(part -> [{orig, copy, base, bone}])
  const roots = new Map();                     // person -> the bodies that got copies (a resting body comes back)

  // the meshes of a part: the node of that name (all meshes under it), and meshes whose name is the part, starts
  // with <part>_ or has it as a word (glasses: dj_glasses_1, dj_glasses_2)
  const named = (name, part) => name === part || name.startsWith(part + '_') || name.split(/[_.\s-]+/).includes(part);
  function meshesOf(root, part) {
    const out = new Set();
    root.traverse((o) => {
      if (o.name === part) o.traverse((m) => { if (m.isSkinnedMesh || m.isMesh) out.add(m); });
      else if ((o.isMesh || o.isSkinnedMesh) && named(o.name, part)) out.add(o);
    });
    return [...out].filter((m) => !m.userData.partCopy);
  }
  // the bone a skinned piece rides: the heaviest bone of its first vertex
  function boneOf(m) {
    const si = m.geometry.attributes.skinIndex, sw = m.geometry.attributes.skinWeight;
    let k = 0;
    for (let c = 1; c < 4; c++) if (sw.getComponent(0, c) > sw.getComponent(0, k)) k = c;
    const i = si.getComponent(0, k);
    return { bone: m.skeleton.bones[i], inv: m.skeleton.boneInverses[i] };
  }
  function copiesFor(person, rig, part) {
    let b = built.get(rig.root);              // per body: a rest, a take and a Follow each show their own
    if (!b) { b = new Map(); built.set(rig.root, b); }
    if (!roots.has(person)) roots.set(person, new Set());
    roots.get(person).add(rig.root);
    if (b.has(part)) return b.get(part);
    const list = [];
    for (const m of meshesOf(rig.root, part)) {
      if (!m.isSkinnedMesh) { list.push({ orig: m, copy: m, base: m.matrix.clone(), plain: true }); continue; }
      const { bone, inv } = boneOf(m);
      rig.root.updateMatrixWorld(true);
      const copy = new THREE.Mesh(m.geometry, m.material);
      copy.userData.partCopy = true;
      copy.name = m.name + '_part';
      copy.matrixAutoUpdate = false;
      const base = new THREE.Matrix4().multiplyMatrices(inv, m.bindMatrix);   // the piece in the bone's frame
      copy.matrix.copy(base);
      copy.frustumCulled = false;
      bone.add(copy);
      m.visible = false;
      list.push({ orig: m, copy, base, bone });
    }
    b.set(part, list);
    return list;
  }
  function apply(person, now) {
    const actors = getActors();
    const rig = actors && actors.shownRig(person);
    const parts = state.get(person);
    if (!rig || !parts) return;
    for (const [part, s] of parts) {
      const k = s.ms > 0 ? ease((now - s.t0) / s.ms) : 1;
      const off = new THREE.Vector3().lerpVectors(s.from, s.to, k);
      const shown = s.show || k < 1;
      for (const c of copiesFor(person, rig, part)) {
        c.copy.matrixAutoUpdate = false;
        c.copy.matrix.copy(c.base).premultiply(new THREE.Matrix4().makeTranslation(off.x, off.y, off.z));
        c.copy.visible = shown;
        c.copy.matrixWorldNeedsUpdate = true;
      }
      if (k >= 1 && s.end) { const e = s.end; s.end = null; live.emit('actor_part', e); }
    }
  }
  function reset(person) {
    for (const root of roots.get(person) || []) {
      for (const list of (built.get(root) || new Map()).values()) for (const c of list) {
        if (c.plain) { c.copy.matrix.copy(c.base); c.copy.visible = true; continue; }
        c.bone.remove(c.copy);
        c.orig.visible = true;
      }
      built.delete(root);
    }
    roots.delete(person);
    state.delete(person);
  }
  ed.preRender.push(() => { const now = performance.now(); for (const p of state.keys()) apply(p, now); });

  // {person, part, show, move: [side, up, front] metres in the person's frame, seconds} or {person, reset: true}
  live.handlers.actor_part = async (c) => {
    const actors = getActors();
    if (!c.person) throw new Error('person: whose body');
    if (c.reset) { reset(c.person); return { person: c.person, reset: true }; }
    if (!c.part) throw new Error('part: the mesh name of the piece (glasses, mask, ...)');
    const rig = actors.shownRig(c.person);
    if (!rig) throw new Error(`${c.person} shows no body now (a start pose, a take or a Follow shows one)`);
    const list = copiesFor(c.person, rig, c.part);
    if (!list.length) {
      const names = [];
      rig.root.traverse((o) => { if ((o.isMesh || o.isSkinnedMesh) && !o.userData.partCopy) names.push(o.name); });
      throw new Error(`no part ${c.part} on ${c.person}: ${[...new Set(names)].sort().join(', ')}`);
    }
    // the move, given in the person's frame (side to their right, up, front), into the bone's frame now, so it rides
    // the head from here on
    const to = new THREE.Vector3();
    const mv = c.move || [0, 0, 0];
    if (mv.some((v) => v)) {
      const { facing } = await actors.eyesOf(c.person);
      const front = (facing || new THREE.Vector3(0, 0, -1)).clone().setY(0).normalize();
      const up = new THREE.Vector3(0, 1, 0), side = new THREE.Vector3().crossVectors(front, up);
      const w = side.multiplyScalar(mv[0]).addScaledVector(up, mv[1]).addScaledVector(front, mv[2]);
      const ref = list[0].bone || list[0].copy.parent;
      ref.updateMatrixWorld(true);
      const q = ref.getWorldQuaternion(new THREE.Quaternion()).invert();
      const s = ref.getWorldScale(new THREE.Vector3());
      to.copy(w.applyQuaternion(q)).divide(s);
    }
    const parts = state.get(c.person) || new Map();
    state.set(c.person, parts);
    const now = performance.now(), old = parts.get(c.part);
    const from = old ? new THREE.Vector3().lerpVectors(old.from, old.to, old.ms > 0 ? ease((now - old.t0) / old.ms) : 1) : new THREE.Vector3();
    const show = c.show !== false;
    const ms = Math.max(0, +(c.seconds || 0)) * 1000;
    parts.set(c.part, { show, from, to, t0: now, ms, end: { person: c.person, part: c.part, show, done: true } });
    apply(c.person, now);
    return { person: c.person, part: c.part, meshes: list.map((x) => x.orig.name), show, seconds: ms / 1000 };
  };
  return { reset, state };
}
