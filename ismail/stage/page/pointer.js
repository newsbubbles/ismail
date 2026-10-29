// A pointer only the cameras see (live `pointer`, stage_pointer): where a person's hand points, a giant foam finger
// lands on what it points at (the user, 2026-10-17, for a talk given from the stage: "wherever my hands point, the
// camera view shows some kind of pointer ... funny"). It follows a person on the stage (a body following the user live,
// or playing a take), so the same finger shows in the live monitor feed (surfaces.js) and in a capture of the take.
// The ray runs from the shoulder through the index fingertip (steadier than the finger alone); with when 'point' the
// finger shows only while that hand points (index straight, middle and ring curled, the hand out from the shoulder).
// It is drawn on CAM_ONLY, a layer the headset's eyes never enable: scene cameras (captures, camera surfaces) do.
import * as THREE from 'three';
import { CAM_ONLY } from './editor.js';

const STYLES = new Set(['foam_finger']);
const HOLD_MS = 120, FAR = 25, MISS = 2.5, REACH = 0.4;

// the foam finger, tip at the origin, pointing along +Z, palm side down (-Y); about 0.85 m long at size 1
function foamFinger() {
  const foam = new THREE.MeshStandardMaterial({ color: 0xffc400, roughness: 0.95 });
  const cuff = new THREE.MeshStandardMaterial({ color: 0x1f4fff, roughness: 0.8 });
  const g = new THREE.Group();
  const add = (geo, mat, pos, rot) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(...pos);
    if (rot) m.rotation.set(...rot);
    m.castShadow = true;
    g.add(m);
    return m;
  };
  add(new THREE.CapsuleGeometry(0.07, 0.3, 6, 12), foam, [0, 0, -0.22], [Math.PI / 2, 0, 0]);          // the finger
  add(new THREE.SphereGeometry(1, 20, 14), foam, [0, -0.02, -0.5]).scale.set(0.2, 0.11, 0.2);           // the mitt
  add(new THREE.CapsuleGeometry(0.068, 0.24, 6, 12), foam, [0, -0.07, -0.36], [0, 0, Math.PI / 2]);    // curled fingers
  add(new THREE.CapsuleGeometry(0.055, 0.12, 6, 10), foam, [0.17, -0.01, -0.44], [Math.PI / 2, 0.6, 0]);  // the thumb
  add(new THREE.CylinderGeometry(0.15, 0.15, 0.14, 24), cuff, [0, -0.02, -0.72], [Math.PI / 2, 0, 0]);  // the cuff
  g.traverse((o) => o.layers.set(CAM_ONLY));
  return g;
}

export function initPointer(ed, live, actors) {
  const st = { on: false, person: null, hand: 'auto', when: 'point', size: 1, style: 'foam_finger' };
  const fingers = { l: null, r: null };
  const ray = new THREE.Raycaster();
  ray.layers.set(0);
  ray.far = FAR;
  const v = () => new THREE.Vector3();
  let lastCast = -Infinity;

  function rigNow() {
    const ps = actors.playing;
    if (!ps || !ps.size) return null;
    const p = st.person ? ps.get(st.person) : ps.values().next().value;
    return p && p.rig;
  }

  // index straight, middle and ring curled, the fingertip out from the shoulder
  function pointing(rig, sd) {
    const P = (n) => rig.bones[`${n}_${sd}`] && rig.bones[`${n}_${sd}`].getWorldPosition(v());
    const hand = P('hand'), i1 = P('index_01'), i3 = P('index_03'), m1 = P('middle_01'), m3 = P('middle_03'),
      r1 = P('ring_01'), r3 = P('ring_03'), sh = P('upperarm');
    if (!hand || !i1 || !i3 || !sh) return null;
    const ray0 = { from: sh, tip: i3 };
    if (st.when === 'always') return ray0;
    if (i3.distanceTo(sh) < REACH) return null;
    const bend = (b, a, c) => THREE.MathUtils.radToDeg(a.clone().sub(b).angleTo(c.clone().sub(a)));
    if (bend(hand, i1, i3) > 40) return null;
    for (const [a, c] of [[m1, m3], [r1, r3]]) if (a && c && bend(hand, a, c) < 60) return null;
    return ray0;
  }

  function hitOf(rig, from, dir) {
    ray.set(from, dir);
    ray.camera = ed.camera;      // sprites (labels) cannot be tested without one
    const skip = (o) => { for (let p = o; p; p = p.parent) if (p === rig.root || p.userData.pointer) return true; return false; };
    for (const h of ray.intersectObjects(ed.scene.children, true)) {
      if (!h.object.isMesh || h.object.isSkinnedMesh || !h.object.visible || skip(h.object)) continue;
      return h.point;
    }
    return null;
  }

  function finger(sd) {
    if (!fingers[sd]) {
      const g = foamFinger();
      g.userData.pointer = true;
      g.visible = false;
      g.userData.k = 0;          // shown 0..1 (it grows in and shrinks away)
      g.userData.since = 0;      // when the hand last changed between pointing and not
      g.userData.want = false;
      ed.scene.add(g);
      fingers[sd] = g;
    }
    return fingers[sd];
  }

  function update() {
    if (!st.on) return;
    const rig = rigNow(), now = performance.now();
    const cast = now - lastCast > 30;          // about 30 rays a second is plenty for a pointer
    if (cast) lastCast = now;
    for (const sd of ['l', 'r']) {
      if (st.hand !== 'auto' && st.hand !== sd) { if (fingers[sd]) fingers[sd].visible = false; continue; }
      const g = finger(sd), u = g.userData;
      const r = rig && pointing(rig, sd);
      if (!!r !== u.want) { u.want = !!r; u.since = now; }
      const show = u.want && now - u.since >= HOLD_MS;
      if (r && cast) {
        const dir = r.tip.clone().sub(r.from).normalize();
        const hit = hitOf(rig, r.tip, dir);
        const tip = hit ? hit.addScaledVector(dir, -0.02) : r.tip.clone().addScaledVector(dir, MISS);
        const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(dir, new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 1, 0)));
        if (!u.placed || u.k === 0) { g.position.copy(tip); g.quaternion.copy(q); u.placed = true; }
        else { g.position.lerp(tip, 0.35); g.quaternion.slerp(q, 0.35); }
      }
      u.k = THREE.MathUtils.clamp(u.k + (show ? 0.2 : -0.2), 0, 1);
      g.visible = u.k > 0;
      g.scale.setScalar(st.size * (u.k < 1 ? 0.5 - 0.5 * Math.cos(Math.PI * u.k) : 1));
    }
  }
  ed.preRender.push(function pointer() { update(); });

  live.handlers.pointer = (c) => {
    if (c.off) {
      st.on = false;
      for (const g of Object.values(fingers)) if (g) { g.visible = false; g.userData.k = 0; }
      return { pointer: 'off' };
    }
    const style = c.style || 'foam_finger';
    if (!STYLES.has(style)) throw new Error(`style is ${[...STYLES].join(', ')}`);
    if (c.person && !(actors.playing && actors.playing.has(c.person))) throw new Error(`"${c.person}" is not following anyone or playing a take`);
    Object.assign(st, { on: true, person: c.person || null, hand: c.hand || 'auto', when: c.when || 'point', size: c.size || 1, style });
    const rig = rigNow();
    return { pointer: style, person: st.person || (rig ? rig.who : null), hand: st.hand, when: st.when, size: st.size,
      ...(rig ? {} : { note: 'nobody is following or playing yet: the finger shows once someone does' }) };
  };
  return { st, fingers };
}
