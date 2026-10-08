// A take played on a person: their skinned MakeHuman body (scenes/<scene>/actors/<who>.glb, rooms/actor_export.py)
// stands in for the baked statue while the take plays, driven by the user's recorded head and hands through the human
// puppet map:
//   head      -> head and neck, the turn spread down the spine; the hips follow under the head
//   wrists    -> targets the arms reach for (two-bone IK, elbows down and out)
//   fingers   -> the 25 WebXR joints onto the three-bone fingers (orientation, world space)
//   legs      -> feet planted on the floor, stepping when the hips leave them (procedural; a leg pass comes later)
// The take is scaled to the actor's height about where it started. Live: {"type": "actor_play", "person":
// "person_couple_2_m", "take": "<id>", "loop": true}, {"type": "actor_stop", "person": ...}. Event actor_play / actor_stop.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { JOINTS } from './hands.js';
import { cutout, b2tPos } from './editor.js';
import { world } from './world.js';
import { initControl } from './control.js';
import { mixTake } from './interp.js';

// who plays whom, facings, partners and the floor are the scene's (world.json via world.js), not the runtime's
// where a person stands: the bottom of their stand-in (the band is up on the stage), the room's floor without one
// which way a person faces: a person keeps THEIR OWN facing while the user drives them, whichever way the user faces
// (the user, 2026-10-03, at the bar: "I want him to be facing the same direction that he's facing ... I can see what
// his hands are doing and I can see his face"). A couple faces the partner (person_couple_k_m <-> _f; the posed
// people's facing is in their meshes, not their objects); the people with a known spot face the way the room says
// (Blender xy: the bartender the room, Sam the bar, the band the floor); anyone else faces the user where they stand.
function facingOf(ed, person, at, user) {
  const pn = world().partners[person];
  const other = pn && ed.byName.get(pn);
  if (other) {
    const d = other.obj.getWorldPosition(new THREE.Vector3()).sub(at).setY(0);
    if (d.lengthSq() > 1e-4) return d.normalize();
  }
  const fb = world().facings[person];
  if (fb) return new THREE.Vector3(fb[0], 0, -fb[1]).normalize();            // Blender (x, y) -> three (x, -y)
  if (user) {
    const d = user.clone().sub(at).setY(0);
    if (d.lengthSq() > 1e-4) return d.normalize();
  }
  return null;
}
// the turn that carries the user's heading at the start onto the person's facing (the user, 2026-10-03: they had to go
// and stand facing the way he faced); null when the person has no facing of their own
function turnFor(headQ, facing) {
  if (!facing) return null;
  const f = new THREE.Vector3(0, 0, -1).applyQuaternion(headQ).setY(0);
  if (f.lengthSq() < 1e-6) return null;
  return new THREE.Quaternion().setFromUnitVectors(f.normalize(), facing);
}
const _v = new THREE.Vector3(), _q = new THREE.Quaternion();
function turnArr(a, st) {                     // [x, y, z, qx, qy, qz, qw]: turned about the anchor
  if (!a) return a;
  _v.set(a[0], a[1], a[2]).sub(st.anchor).applyQuaternion(st.turn).add(st.anchor);
  const out = [_v.x, _v.y, _v.z];
  if (a.length >= 7) { _q.set(a[3], a[4], a[5], a[6]).premultiply(st.turn); out.push(_q.x, _q.y, _q.z, _q.w); }
  return out;
}
function turnFrame(f, st) {
  const g = { ...f, head: turnArr(f.head, st) };
  for (const h of Object.keys(f)) if (f[h] && Array.isArray(f[h].j)) g[h] = { ...f[h], j: f[h].j.map((x) => turnArr(x, st)) };
  return g;
}
// Mirror (the user: "my right is his right ... or he mirrors me"): the frame reflected through the vertical plane of
// the person's facing, left and right swapped, so facing him you move like his reflection. A reflected joint frame is
// left-handed: its local x is flipped to make it a rotation again.
const _m = new THREE.Matrix4(), _cx = new THREE.Vector3(), _cy = new THREE.Vector3(), _cz = new THREE.Vector3(), _d = new THREE.Vector3();
function mirrorArr(a, st) {
  if (!a) return a;
  const n = st.mirrorN;
  _v.set(a[0], a[1], a[2]);
  _v.addScaledVector(n, -2 * _d.copy(_v).sub(st.anchor).dot(n));
  const out = [_v.x, _v.y, _v.z];
  if (a.length >= 7) {
    _m.makeRotationFromQuaternion(_q.set(a[3], a[4], a[5], a[6])).extractBasis(_cx, _cy, _cz);
    for (const c of [_cx, _cy, _cz]) c.addScaledVector(n, -2 * c.dot(n));
    _cx.negate();
    _q.setFromRotationMatrix(_m.makeBasis(_cx, _cy, _cz));
    out.push(_q.x, _q.y, _q.z, _q.w);
  }
  return out;
}
function mirrorFrame(f, st) {
  const g = { ...f, head: mirrorArr(f.head, st) };
  const mh = (h) => (f[h] && Array.isArray(f[h].j) ? { ...f[h], j: f[h].j.map((x) => mirrorArr(x, st)) } : f[h]);
  g.left = mh('right'); g.right = mh('left');
  return g;
}
const groundOf = (it) => {
  const FLOOR = world().floor || 0;
  if (!it) return FLOOR;
  const y = new THREE.Box3().setFromObject(it.obj).min.y;
  return Number.isFinite(y) && y > -0.5 && y < 3 ? Math.max(FLOOR, y) : FLOOR;
};
const FINGER_JOINTS = {
  thumb: ['thumb-metacarpal', 'thumb-phalanx-proximal', 'thumb-phalanx-distal'],
  index: ['index-finger-phalanx-proximal', 'index-finger-phalanx-intermediate', 'index-finger-phalanx-distal'],
  middle: ['middle-finger-phalanx-proximal', 'middle-finger-phalanx-intermediate', 'middle-finger-phalanx-distal'],
  ring: ['ring-finger-phalanx-proximal', 'ring-finger-phalanx-intermediate', 'ring-finger-phalanx-distal'],
  pinky: ['pinky-finger-phalanx-proximal', 'pinky-finger-phalanx-intermediate', 'pinky-finger-phalanx-distal'],
};
const SPINE = [['spine_01', 0.15], ['spine_02', 0.3], ['spine_03', 0.5], ['neck_01', 0.75], ['head', 1.0]];
const STEP_AT = 0.22, STEP_S = 0.28, LIFT = 0.07;
// a standing body from a headset (the user, 2026-10-08: "when I move my head ... sometimes the rest of my body moves,
// but that's not actually what's going on"): the hips stay put while the head moves within HIP_SLACK of above them (a
// lean, a nod: the spine bends toward the head instead) and trail it beyond; the body turns only when the head is
// turned more than YAW_SLACK from it (a look aside twists the neck and spine, not the feet)
const HIP_SLACK = 0.12, YAW_SLACK = THREE.MathUtils.degToRad(35);
// pins while following (the user, 2026-10-04, sitting in real life while following a man on a bar stool: "anchors his
// butt to the chair, so if I do anything with my head and hands his butt's gonna be anchored unless I move completely
// away from the chair"). Pinned hips stay on the seat, facing the person's own way; the user's head bends the spine
// toward it (direction only, so sitting lower or higher does not sink or lift him) and the hands drive the arms; the
// feet plant in front of the seat (a seated pose) or on their own pins. The head clearly away from the seat
// (PIN_AWAY_M for PIN_AWAY_MS) lets go for that follow.
const GAP_S = 0.25;                                  // playback: samples further apart than this are held, not mixed
const SEAT_ABOVE = 0.09, SEAT_FOOT = 0.42, LEAN_MAX = 0.9, PIN_AWAY_M = 0.7, PIN_AWAY_MS = 600;
const Y = new THREE.Vector3(0, 1, 0);

export function initActors(ed, live) {
  const { scene } = ed;
  const scn = () => ed.sceneName;                      // live: scenes.js can switch it
  const loaded = new Map();               // who -> Promise<rig>
  let source = null;                      // hands.frameNow: the user's body right now (follow)
  const playing = new Map();              // person -> state

  // ---- an actor: its bones and their rest pose in world space
  function load(who, base) {
    if (!loaded.has(who)) {
      loaded.set(who, new GLTFLoader().loadAsync(`${base}actors/${who}.glb`).then((g) => {
        const root = g.scene;
        root.traverse((o) => { if (o.isMesh) { o.castShadow = true; o.frustumCulled = false; [o.material].flat().forEach(cutout); } });
        const bones = {};
        root.traverse((o) => { if (o.isBone) bones[o.name] = o; });
        root.updateMatrixWorld(true);
        const rest = {};
        for (const [n, b] of Object.entries(bones)) rest[n] = { q: b.getWorldQuaternion(new THREE.Quaternion()), p: b.getWorldPosition(new THREE.Vector3()) };
        const fwd = rest.ball_l.p.clone().sub(rest.foot_l.p).setY(0).normalize();
        const left = rest.upperarm_l.p.clone().sub(rest.upperarm_r.p).setY(0).normalize();
        return { who, root, bones, rest, fwd, left, cal: handCalibration(bones, rest), local: Object.fromEntries(Object.entries(bones).map(([n, b]) => [n, b.quaternion.clone()])),
          localP: Object.fromEntries(Object.entries(bones).map(([n, b]) => [n, b.position.clone()])) };
      }).then(async (rig) => { await readProfile(rig); return rig; }));
    }
    return loaded.get(who);
  }

  // its profile (rigs.py): rig type, named parts, control map presets; a body without one still has its parts. Read
  // again before a preset is applied (an agent may have just saved it)
  async function readProfile(rig) {
    rig.profile = await fetch(`actor/profile?scene=${encodeURIComponent(scn())}&who=${encodeURIComponent(rig.who)}`, { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null)).catch(() => null) || rig.profile || { rig: 'unknown', parts: {}, effectors: [], maps: {} };
    return rig;
  }

  // ---- hands: each WebXR joint straight onto its bone, no rest-pose guess. A WebXR joint has -Z along the bone toward
  // the tip and +Y out of the back of the hand (the nail). Per bone, cal = C^-1 where C carries those axes into the
  // bone's local frame; then bone world = joint world * cal. The hand and the four fingers take "back" from the rig's
  // own palm (the hand bone is rolled ~41 deg off it); the thumbs from the rig's nail axis (-Z local, the fingers'
  // convention measured on these MakeHuman rigs), since a thumb's nail does not face the back of the hand. (The user
  // on the first played take: "his thumbs are completely the wrong way"; the old map assumed every joint lay along
  // the arm at rest, and this rig rests in an A-pose.)
  function handCalibration(bones, rest) {
    const cal = {};
    const P_ = (n) => rest[n].p;
    const make = (n, alongW, backW) => {
      const qi = rest[n].q.clone().invert();
      const al = alongW.clone().applyQuaternion(qi).normalize();
      const bk = backW.clone().applyQuaternion(qi);
      bk.sub(al.clone().multiplyScalar(bk.dot(al))).normalize();
      const zx = al.clone().negate(), yx = bk, xx = new THREE.Vector3().crossVectors(yx, zx);
      cal[n] = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(xx, yx, zx)).invert();
    };
    for (const sd of ['l', 'r']) {
      if (!bones[`hand_${sd}`]) continue;
      const back = new THREE.Vector3().crossVectors(P_(`index_01_${sd}`).clone().sub(P_(`pinky_01_${sd}`)),
        P_(`middle_01_${sd}`).clone().sub(P_(`hand_${sd}`))).normalize();
      if (sd === 'r') back.negate();
      make(`hand_${sd}`, P_(`middle_01_${sd}`).clone().sub(P_(`hand_${sd}`)), back);
      for (const fg of ['index', 'middle', 'ring', 'pinky', 'thumb']) {
        for (let k = 1; k <= 3; k++) {
          const n = `${fg}_0${k}_${sd}`;
          if (!rest[n]) continue;
          const yW = new THREE.Vector3(0, 1, 0).applyQuaternion(rest[n].q);       // along the bone (+Y on these rigs)
          make(n, yW, fg === 'thumb' ? new THREE.Vector3(0, 0, -1).applyQuaternion(rest[n].q) : back);
        }
      }
    }
    return cal;
  }

  // ---- setting a bone's world rotation / aiming it, parents first
  const tq = new THREE.Quaternion(), tq2 = new THREE.Quaternion(), tv = new THREE.Vector3(), tv2 = new THREE.Vector3();
  function setWorldQ(b, qw) {
    b.parent.getWorldQuaternion(tq).invert();
    b.quaternion.copy(tq.multiply(qw));
    b.updateMatrixWorld(true);
  }
  function aim(b, child, target) {                          // turn b so its child lies toward target
    const p = b.getWorldPosition(tv), c = child.getWorldPosition(tv2);
    const cur = c.sub(p).normalize(), want = target.clone().sub(p).normalize();
    const d = new THREE.Quaternion().setFromUnitVectors(cur, want);
    setWorldQ(b, d.multiply(b.getWorldQuaternion(tq2)));
  }
  function twoBone(rig, a, bn, cn, target, pole) {         // a -> b -> c reaches target, bending toward pole
    const A = rig.bones[a], B = rig.bones[bn], C = rig.bones[cn];
    const S = A.getWorldPosition(new THREE.Vector3());
    const la = rig.rest[bn].p.distanceTo(rig.rest[a].p), lb = rig.rest[cn].p.distanceTo(rig.rest[bn].p);
    const want = S.distanceTo(target), d = Math.min(want, la + lb - 1e-4);
    // a target past the limb's length: the end stops at full reach, and how far short it fell is kept for the read-back
    // (stage_actor_pose: a hand that cannot reach the bar is a number, not a surprise)
    if (rig.short) rig.short[cn] = Math.max(0, want - (la + lb));
    const u = target.clone().sub(S).normalize();
    const v = pole.clone().sub(S); v.sub(u.clone().multiplyScalar(v.dot(u))).normalize();
    const ca = THREE.MathUtils.clamp((la * la + d * d - lb * lb) / (2 * la * d), -1, 1);
    const E = S.clone().addScaledVector(u, la * ca).addScaledVector(v, la * Math.sqrt(1 - ca * ca));
    aim(A, B, E);
    aim(B, C, S.clone().addScaledVector(u, d));
  }

  // ---- one frame of the take onto the rig: the built-in human map, then the control map's drives (control.js)
  function prep(st, raw) {                                 // a frame as the person takes it: turned, mirrored
    let f = raw;
    if (st.turn) f = turnFrame(f, st);
    if (st.mirror && st.mirrorN) f = mirrorFrame(f, st);
    return f;
  }
  function pose(st, raw) {
    st.rig.short = {};                                       // twoBone fills it: limb ends that fell short this frame
    const f = prep(st, raw);
    if (st.start) poseRelative(st, f); else poseBase(st, f);
    control.apply(st, f, raw);
  }
  function poseBase(st, f) {
    const { rig, s, anchor, J } = st, to = st.to || anchor;
    const P = (a) => to.clone().add(new THREE.Vector3(a[0], a[1], a[2]).sub(anchor).multiplyScalar(s));
    for (const [n, q] of Object.entries(rig.local)) rig.bones[n].quaternion.copy(q);   // from rest each frame
    rig.root.updateMatrixWorld(true);
    // the head: the camera's turn relative to "looking along the actor's rest forward"
    const qc = new THREE.Quaternion(f.head[3], f.head[4], f.head[5], f.head[6]);
    const delta = qc.clone().multiply(st.alignInv);
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(qc).setY(0);
    const yaw = new THREE.Quaternion().setFromUnitVectors(rig.fwd, fwd.lengthSq() > 1e-6 ? fwd.normalize() : rig.fwd);
    const head = P(f.head);
    const pin = st.pins && st.pins.hips;
    // the hips under the head, turned with it; or pinned to a seat, facing the person's own way
    const pel = rig.bones.pelvis;
    let base, hipAt;
    if (pin) { base = st.pinYaw || yaw; hipAt = pin.clone(); st.body = null; }   // let go of the seat: the body starts there
    else {
      const b = st.body || (st.body = { yaw: yaw.clone(), hip: null });
      const ang = b.yaw.angleTo(yaw);
      if (ang > YAW_SLACK) b.yaw.rotateTowards(yaw, ang - YAW_SLACK);
      base = b.yaw;
      const under = head.clone().add(rig.rest.pelvis.p.clone().sub(rig.rest.head.p).applyQuaternion(base));
      under.y = Math.min(under.y, st.floor + rig.rest.pelvis.p.y - rig.rest.foot_l.p.y + 0.08);   // never off the ground
      if (!b.hip) b.hip = under.clone();
      const dx = under.x - b.hip.x, dz = under.z - b.hip.z, d = Math.hypot(dx, dz), slack = HIP_SLACK * s;
      if (d > slack) { b.hip.x += dx * (d - slack) / d; b.hip.z += dz * (d - slack) / d; }
      b.hip.y = under.y;
      hipAt = b.hip.clone();
    }
    st.lastHip = hipAt.clone(); st.lastYaw = base.clone();             // the body's facing, not the head's
    pel.parent.updateMatrixWorld(true);
    pel.position.copy(pel.parent.worldToLocal(hipAt.clone()));
    setWorldQ(pel, base.clone().multiply(rig.rest.pelvis.q));
    // the spine leans from the hips (or the seat) toward where the user's head is (its length kept)
    let lean = null;
    {
      const up = rig.rest.head.p.clone().sub(rig.rest.pelvis.p).applyQuaternion(base).normalize();
      const to = head.clone().sub(hipAt).normalize();
      lean = new THREE.Quaternion().setFromUnitVectors(up, to);
      const ang = 2 * Math.acos(Math.min(1, Math.abs(lean.w)));
      if (ang > LEAN_MAX) lean = new THREE.Quaternion().slerp(lean, LEAN_MAX / ang);
    }
    for (const [n, k] of SPINE) {
      const q = new THREE.Quaternion().slerpQuaternions(base, delta, k);
      if (lean) q.premultiply(new THREE.Quaternion().slerp(lean, Math.min(1, k * 1.25)));
      setWorldQ(rig.bones[n], q.multiply(rig.rest[n].q));
    }
    // arms and hands, fingers
    const side = (sd, h) => {
      const hd = f[h];
      const sgn = sd === 'l' ? 1 : -1;
      if (!hd || !hd.j || !hd.j[0]) {                      // the hand out of tracking: the arm hangs at the side
        const sh0 = rig.bones[`upperarm_${sd}`].getWorldPosition(new THREE.Vector3());
        const lat = rig.left.clone().applyQuaternion(base).multiplyScalar(sgn * 0.12);
        twoBone(rig, `upperarm_${sd}`, `lowerarm_${sd}`, `hand_${sd}`, sh0.clone().add(lat).add(new THREE.Vector3(0, -0.6, 0)),
          sh0.clone().add(rig.fwd.clone().applyQuaternion(base).multiplyScalar(-1)));
        return;
      }
      const lateral = rig.left.clone().applyQuaternion(base).multiplyScalar(sgn);
      const back = rig.fwd.clone().applyQuaternion(base).multiplyScalar(-1);
      const sh = rig.bones[`upperarm_${sd}`].getWorldPosition(new THREE.Vector3());
      twoBone(rig, `upperarm_${sd}`, `lowerarm_${sd}`, `hand_${sd}`, P(hd.j[0]), sh.clone().add(new THREE.Vector3(0, -1, 0)).addScaledVector(lateral, 0.5).addScaledVector(back, 0.4));
      const jq = (i) => { const a = hd.j[i]; return a ? new THREE.Quaternion(a[3], a[4], a[5], a[6]) : null; };
      const w = jq(0);
      if (w && rig.cal[`hand_${sd}`]) setWorldQ(rig.bones[`hand_${sd}`], w.multiply(rig.cal[`hand_${sd}`]));
      for (const [fg, names] of Object.entries(FINGER_JOINTS)) {
        names.forEach((jn, k) => {
          const b = rig.bones[`${fg}_0${k + 1}_${sd}`], q = jq(J[jn]);
          if (b && q && rig.cal[b.name]) setWorldQ(b, q.multiply(rig.cal[b.name]));
        });
      }
    };
    side('l', 'left'); side('r', 'right');
    // legs: planted feet, a step when the hips leave them; pinned hips: the feet in front of the seat, or on their pins
    const now = f.t;
    if (pin) {
      const fw = rig.fwd.clone().applyQuaternion(base);
      for (const sd of ['l', 'r']) {
        const own = st.pins['foot_' + sd];
        // on the floor in front of the seat; a seat too high for that (a bar stool) rests them on a rung, knees bent
        const leg = rig.rest[`thigh_${sd}`].p.distanceTo(rig.rest[`calf_${sd}`].p) + rig.rest[`calf_${sd}`].p.distanceTo(rig.rest[`foot_${sd}`].p);
        const floorY = st.floor + rig.rest[`foot_${sd}`].p.y, high = hipAt.y - floorY > 0.8 * leg;
        const at = own ? own.clone() : hipAt.clone().addScaledVector(fw, high ? SEAT_FOOT * 0.45 : SEAT_FOOT)
          .add(rig.rest[`foot_${sd}`].p.clone().sub(rig.rest.pelvis.p).setY(0).applyQuaternion(base));
        if (!own) at.y = high ? hipAt.y - 0.62 * leg : floorY;
        const knee = rig.bones[`thigh_${sd}`].getWorldPosition(new THREE.Vector3()).addScaledVector(fw, 1.0).add(new THREE.Vector3(0, 0.4, 0));
        twoBone(rig, `thigh_${sd}`, `calf_${sd}`, `foot_${sd}`, at, knee);
        setWorldQ(rig.bones[`foot_${sd}`], base.clone().multiply(rig.rest[`foot_${sd}`].q));
      }
      return;
    }
    for (const sd of ['l', 'r']) {
      const ft = st.feet[sd];
      const want = hipAt.clone().add(rig.rest[`foot_${sd}`].p.clone().sub(rig.rest.pelvis.p).applyQuaternion(base));
      want.y = st.floor + rig.rest[`foot_${sd}`].p.y;
      if (!ft.at) ft.at = want.clone();
      const other = st.feet[sd === 'l' ? 'r' : 'l'];
      if (!ft.step && !other.step && ft.at.distanceTo(want) > STEP_AT) ft.step = { from: ft.at.clone(), to: want.clone(), t0: now };
      let at = ft.at;
      if (ft.step) {
        const k = Math.min(1, (now - ft.step.t0) / STEP_S);
        at = ft.step.from.clone().lerp(ft.step.to, k); at.y += Math.sin(Math.PI * k) * LIFT;
        if (k >= 1) { ft.at = ft.step.to; ft.step = null; }
      }
      const knee = rig.bones[`thigh_${sd}`].getWorldPosition(new THREE.Vector3()).addScaledVector(rig.fwd.clone().applyQuaternion(base), 1.0);
      twoBone(rig, `thigh_${sd}`, `calf_${sd}`, `foot_${sd}`, at, knee);
      setWorldQ(rig.bones[`foot_${sd}`], base.clone().multiply(rig.rest[`foot_${sd}`].q));
    }
  }

  // ---- play / stop
  async function playNow(c) {
    const person = c.person, who = c.actor || world().actors[person];
    if (!who) throw new Error('no actor for ' + person + ' (world.json actors, or pass actor)');
    const gone = unloadedOf(person);
    if (gone) throw new Error(`${person} is in the unloaded set ${gone}: load it first (stage_set_load)`);
    const base = `scenes/${encodeURIComponent(c.assets || world().assets || scn())}/`;   // a derived scene's bodies: its source's
    const tbase = `scenes/${encodeURIComponent(c.takes || scn())}/takes/${encodeURIComponent(c.take)}/`;
    // c.frames: frames in memory (the last Follow, played back before it is kept: perform.js), with c.meta
    const [rig0, meta, txt] = await Promise.all([load(who, base), c.frames ? { joints: JOINTS, ...(c.meta || {}) } : fetch(tbase + 'meta.json', { cache: 'no-store' }).then((r) => r.json()),
      c.frames ? null : fetch(tbase + 'frames.jsonl', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('no take ' + c.take); return r.text(); })]);
    stop({ person });
    if (unloadedOf(person)) throw new Error(`${person}'s set ${unloadedOf(person)} was unloaded while the take loaded`);
    unrest(person);
    let frames = (c.frames || txt.split('\n').filter(Boolean).map((l) => JSON.parse(l))).filter((f) => f.head);
    if (!frames.length) throw new Error('take has no frames');
    // a trimmed take plays only its kept part (actions.js review: Start here / End here; saved in meta.trim)
    const trim = c.trim === null ? null : c.trim || meta.trim;
    if (Array.isArray(trim) && trim.length === 2) {
      const cut = frames.filter((f) => f.t >= trim[0] && f.t <= trim[1]);
      if (cut.length > 1) frames = cut;
    }
    const rig = playing.size && [...playing.values()].some((p) => p.rig.who === who) ? await cloneRig(rig0) : rig0;
    const J = Object.fromEntries((meta.joints || []).map((n, i) => [n, i]));
    // scale: the user's standing head height to the actor's
    const floor = world().floor || 0;                       // the user's floor while recording
    const heads = frames.map((f) => f.head[1]).sort((a, b) => a - b);
    // the median: a take can hold moments raised on the thumbstick, which a high percentile took for standing height
    const userH = heads[Math.floor(heads.length * 0.5)] - floor, actorH = rig.rest.head.p.y - rig.rest.foot_l.p.y + 0.08;
    const s = THREE.MathUtils.clamp(actorH / Math.max(0.5, userH), 0.6, 1.4);
    const anchor = new THREE.Vector3(frames[0].head[0], floor, frames[0].head[2]);
    const align = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rig.fwd);
    const it = ed.byName.get(person);
    // a take borrowed from someone else plays where this person stands (the user, 2026-10-03: "make the people that
    // are inside the place who can dance, dance"): its start moves onto them; their own take plays where it was made
    // and since live follow (the user, 2026-10-03: "they stand where they are, and they follow along with my
    // movements"), every take plays where its person stands, the way it looked while it was recorded; c.in_place
    // plays it where the user stood
    let to = null, ground = floor;
    if (it && !c.in_place) {
      const w = it.obj.getWorldPosition(new THREE.Vector3());
      ground = groundOf(it);
      to = new THREE.Vector3(w.x, ground, w.z);
    }
    if (it) it.obj.visible = false;
    scene.add(rig.root);
    const h0 = frames[0].head;
    const fc = to ? facingOf(ed, person, to) : null;
    const turn = to && h0.length >= 7 ? turnFor(new THREE.Quaternion(h0[3], h0[4], h0[5], h0[6]), fc) : null;
    // mirrored as it was made (the user, 2026-10-05: a take recorded with Follow's mirror played back the other way):
    // the take keeps meta.mirror; the plane is the person's facing through the take's anchor, as in the Follow
    const mirror = !!(c.mirror ?? meta.mirror);
    const st = { person, rig, frames, J, s, anchor, to, floor: ground, alignInv: align.clone().invert(), feet: { l: {}, r: {} },
      t0: performance.now(), loop: c.loop !== false, it, take: c.take, i: 0, rate: c.rate || 1, turn, meta,
      mirror, mirrorN: fc ? new THREE.Vector3(fc.z, 0, -fc.x) : null,
      voice: c.voice == null ? null : !!c.voice,           // perform.js: the performance's voice with it
      atMusic: c.at_music == null ? null : +c.at_music };   // on the music clock: the first frame at this song second
    // pinned in playback as while recording (the user, 2026-10-05: Sam's take played anchored by the feet, though
    // his hips were pinned to the stool): the take's own pins (meta.pins, Blender xyz), else this session's
    const pins = c.pins || meta.pins ? pinsFromMeta(c.pins || meta.pins) : anchors.get(person);
    if (pins && (pins.hips || pins.foot_l || pins.foot_r)) usePins(st, pins);
    await useStart(st, c);
    playing.set(person, st);
    live.emit('actor_play', { person, actor: who, take: c.take, seconds: +(frames[frames.length - 1].t - frames[0].t).toFixed(1), scale: +s.toFixed(2),
      ...(st.atMusic != null ? { at_music: st.atMusic, music: musicClock ? musicClock() : null } : {}) });
    return { person, actor: who, frames: frames.length, scale: +s.toFixed(2) };
  }
  // ---- follow: the person moves with the user, live, from where they stand (no take needed; a take on a person
  // turns it on while recording). Same puppet map as a played take, fed the user's frame of this moment.
  async function followNow(c) {
    const person = c.person, who = c.actor || world().actors[person];
    if (!who) throw new Error('no actor for ' + person + ' (world.json actors, or pass actor)');
    const gone = unloadedOf(person);
    if (gone) throw new Error(`${person} is in the unloaded set ${gone}: load it first (stage_set_load)`);
    if (!source) throw new Error('no live body source');
    const rig0 = await load(who, `scenes/${encodeURIComponent(c.assets || world().assets || scn())}/`);
    stop({ person });
    const rig = [...playing.values()].some((p) => p.rig.who === who) ? await cloneRig(rig0) : rig0;
    const f0 = source(), floor = world().floor || 0;
    const userH = Math.max(0.5, f0.head[1] - floor), actorH = rig.rest.head.p.y - rig.rest.foot_l.p.y + 0.08;
    const s = THREE.MathUtils.clamp(actorH / userH, 0.6, 1.4);
    const it = ed.byName.get(person);
    const w = it ? it.obj.getWorldPosition(new THREE.Vector3()) : new THREE.Vector3(f0.head[0], floor, f0.head[2]);
    const align = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rig.fwd);
    const ground = groundOf(it);
    unrest(person);
    if (it) it.obj.visible = false;
    scene.add(rig.root);
    const toW = new THREE.Vector3(w.x, ground, w.z);
    const facing = facingOf(ed, person, toW, new THREE.Vector3(f0.head[0], 0, f0.head[2]));
    const turn = turnFor(new THREE.Quaternion(f0.head[3], f0.head[4], f0.head[5], f0.head[6]), facing);
    const st = { person, rig, live: true, J: Object.fromEntries(JOINTS.map((n, i) => [n, i])), s, floor: ground,
      anchor: new THREE.Vector3(f0.head[0], floor, f0.head[2]), to: toW, turn, mode: c.mode || 'place', drift: new THREE.Vector3(),
      mirror: !!c.mirror, mirrorN: facing ? new THREE.Vector3(facing.z, 0, -facing.x) : null,
      last: new THREE.Vector3(f0.head[0], f0.head[1], f0.head[2]),
      alignInv: align.clone().invert(), feet: { l: {}, r: {} }, t0: performance.now(), it };
    const pins = anchors.get(person);
    if (pins) usePins(st, pins);
    // the setup from the body, not a button (the user, 2026-10-08: a standing man came up with his feet locked and would
    // not walk): seated (hips on a seat) stays put, a standing person walks with the user
    if (!c.mode) st.mode = st.pins && st.pins.hips ? 'place' : 'walk';
    await useStart(st, c);
    playing.set(person, st);
    lastMirror.set(person, st.mirror);
    await control.onFollow(person);
    live.emit('actor_follow', { person, actor: who, scale: +s.toFixed(2), pinned: pinnedNames(st), drives: control.state(person),
      start: st.start ? st.start.spec : null, ...setupOf(person) });
    return { person, actor: who, following: true, scale: +s.toFixed(2), ...setupOf(person) };
  }
  async function cloneRig(r) {                              // a second person on the same actor (couple 3)
    const { clone } = await import('three/addons/utils/SkeletonUtils.js');
    const root = clone(r.root), bones = {};
    root.traverse((o) => { if (o.isBone) bones[o.name] = o; });
    return { ...r, root, bones };
  }
  function stop(c) {
    const st = playing.get(c.person);
    if (!st) return { stopped: false };
    playing.delete(c.person);
    scene.remove(st.rig.root);
    if (st.it) st.it.obj.visible = true;
    live.emit('actor_stop', { person: c.person, why: c.why || 'stopped', live: !!st.live });
    rest(c.person).catch(() => {});                       // back to their resting pose, if they have one
    return { stopped: true };
  }

  // ---- resting: a person with a start pose stands in it whenever nothing plays on them, at load and after every stop,
  // instead of the statue baked into the scene (the user, 2026-10-06: the six dancers had stood with their arms out
  // since they were imported; "a pose to leave everything in"). Their own body, posed once; the statue hidden.
  // stage_actor_start(idle=False) keeps the statue for that person.
  const resting = new Map();                                // person -> { rig, spec }
  // a play or a Follow being set up claims its person until it is playing, so a rest still loading (the one its own
  // stop() begins, or a scene reveal's) stands down instead of adding a second body (the user, 2026-10-08: "double
  // Pete" while following and recording, after Pete had a start pose: the rest finished during useStart's await)
  const starting = new Map();                               // person -> plays or Follows being set up
  const claimed = (fn) => async (c) => {
    const person = c && c.person;
    starting.set(person, (starting.get(person) || 0) + 1);
    try { return await fn(c); } finally {
      const n = starting.get(person) - 1;
      if (n > 0) starting.set(person, n);
      else { starting.delete(person); if (!playing.has(person)) rest(person).catch(() => {}); }   // it failed: rest again
    }
  };
  const play = claimed(playNow), follow = claimed(followNow);
  const busy = (person) => playing.has(person) || starting.has(person);
  function unrest(person) {
    const r = resting.get(person);
    if (!r) return;
    resting.delete(person);
    scene.remove(r.rig.root);
  }
  async function rest(person) {
    if (busy(person) || !world().actors[person]) return null;
    if (unloadedOf(person)) { unrest(person); return null; }   // in an unloaded set (loadsets.js): no body at all
    const rig0 = await rigOf(person);
    await readProfile(rig0);
    const spec = rig0.profile && rig0.profile.start;
    const it = ed.byName.get(person);
    if (!spec || spec.idle === false || !it) { unrest(person); if (it) it.obj.visible = true; return null; }
    if (busy(person) || unloadedOf(person)) return null;   // a play began, or its set went, while it loaded
    const r = resting.get(person) || { rig: await cloneRig(rig0) };
    const w = it.obj.getWorldPosition(new THREE.Vector3()), floor = groundOf(it);
    const align = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), r.rig.fwd);
    const tmp = { person, rig: r.rig, s: 1, floor, to: new THREE.Vector3(w.x, floor, w.z), alignInv: align.invert(),
      feet: { l: {}, r: {} }, J: Object.fromEntries(JOINTS.map((n, i) => [n, i])) };
    await startPose(tmp, spec);
    if (busy(person) || unloadedOf(person)) return null;   // a play began, or its set was unloaded, meanwhile
    r.spec = spec;
    resting.set(person, r);
    scene.add(r.rig.root);
    it.obj.visible = false;
    live.emit('actor_rest', { person, start: spec });
    return { person, resting: true, start: spec };
  }
  async function restAll() {
    for (const person of Object.keys(world().actors || {})) await rest(person).catch((e) => console.warn('[actors] rest', person, e));
  }
  // at load and on a scene switch (the bodies of the new scene), and on an agent's word after a start pose changed
  ed.addEventListener('revealed', () => { for (const r of resting.values()) scene.remove(r.rig.root); resting.clear(); restAll(); });
  live.handlers.actor_rest = (c) => (c.person ? rest(c.person) : restAll().then(() => ({ resting: [...resting.keys()] })));
  const cur = new THREE.Vector3(), jump = new THREE.Vector3(), WALK_AWAY_M = 6;
  const shiftArr = (x, d) => (x ? [x[0] - d.x, x[1], x[2] - d.z, ...x.slice(3)] : x);
  function shifted(f, d) {                      // every position of a frame moved by -d (horizontal)
    const g = { ...f, head: shiftArr(f.head, d) };
    for (const h of Object.keys(f)) if (f[h] && Array.isArray(f[h].j)) g[h] = { ...f[h], j: f[h].j.map((x) => shiftArr(x, d)) };
    return g;
  }
  // the user's controls while someone follows (actions.js' follow panel)
  function turnBy(person, deg) {
    const st = playing.get(person);
    if (!st) return null;
    const r = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(deg));
    st.turn = st.turn ? r.multiply(st.turn) : r;
    if (st.mirrorN) st.mirrorN.applyQuaternion(r);
    return { person, turned: deg };
  }
  // how a Follow is set up, in words for the panel and the agents: seated or standing, walking or on the spot
  function setupOf(person) {
    const st = playing.get(person);
    if (!st || !st.live) return {};
    const seated = !!(st.pins && st.pins.hips);
    const says = seated ? 'Seated: the hips stay on the seat; your head leans the body, your hands move the arms.'
      : st.mode === 'walk' ? 'Standing: walks as you walk. Leaning or turning your head bends the body, the feet stay.'
        : 'On the spot: dances where they stand while you move around.';
    return { body: seated ? 'seated' : 'standing', mode: st.mode, says };
  }
  function setMode(person, mode) {
    const st = playing.get(person);
    if (!st || !st.live) return null;
    if (mode === 'walk' && st.mode === 'place') { st.anchor.add(st.drift); st.drift.set(0, 0, 0); }   // no jump when he starts walking
    st.mode = mode;
    return { person, mode, ...setupOf(person) };
  }
  function moveTo(person, at) {                // his spot moves (the stand-in too, as an undoable edit): never during a take
    const st = playing.get(person);
    if (!st) return null;
    const d = new THREE.Vector3(at.x - st.to.x, 0, at.z - st.to.z);
    st.to.add(d);
    if (st.it) {
      const o = st.it.obj, wp = o.getWorldPosition(new THREE.Vector3()).add(d);
      ed.beginEdit('move ' + person);
      o.position.copy(o.parent ? o.parent.worldToLocal(wp) : wp);
      ed.endEdit();
    }
    live.emit('actor_moved', { person, by: [+d.x.toFixed(2), +d.z.toFixed(2)] });
    return { person, moved: true };
  }
  function update() {
    for (const st of playing.values()) {
      if (st.live) {
        const f = source && source();
        if (!f) continue;
        // a teleport (the head jumps a metre in a frame) leaves the person where they are, still following (carrying them
        // along would break a take: the user, 2026-10-03; "Move him here" on the follow panel moves them on purpose)
        cur.set(f.head[0], f.head[1], f.head[2]);
        if (cur.distanceTo(st.last) > 0.8) st.anchor.add(jump.copy(cur).sub(st.last).setY(0));
        st.last.copy(cur);
        // "place": he dances on his spot while the user walks around him (the slow drift of the user's head is taken
        // out, the sway kept); "walk": he walks as the user walks. Far away ends it (and a take: actions.js).
        if (st.mode === 'place') st.drift.lerp(jump.copy(cur).sub(st.anchor).setY(0), 0.02);
        const far = st.mode === 'place' ? Math.hypot(cur.x - st.to.x, cur.z - st.to.z) : Math.hypot(cur.x - st.anchor.x, cur.z - st.anchor.z);
        if (far > WALK_AWAY_M) { stop({ person: st.person, why: 'walked_away' }); continue; }
        f.t = (performance.now() - st.t0) / 1000;
        if (st.pins && st.pins.hips) {                   // the user got up and walked off: the seat lets go of him
          const ph = st.to.clone().add(new THREE.Vector3(f.head[0], f.head[1], f.head[2]).sub(st.anchor).multiplyScalar(st.s));
          const away = Math.hypot(ph.x - st.pins.hips.x, ph.z - st.pins.hips.z) > PIN_AWAY_M * st.s;
          st.awaySince = away ? (st.awaySince || performance.now()) : 0;
          if (away && performance.now() - st.awaySince > PIN_AWAY_MS) {
            st.pins = null; st.awaySince = 0;
            live.emit('pin_released', { person: st.person, why: 'moved away from the seat' });
          }
        }
        pose(st, st.mode === 'place' ? shifted(f, st.drift) : f);
        continue;
      }
      const T = st.frames[st.frames.length - 1].t, t0 = st.frames[0].t;
      let t = t0 + (performance.now() - st.t0) / 1000 * st.rate;
      // on the music: the take's time is the song's (its first frame at song second atMusic), read every frame, so it
      // stays on the beat however late it started and wherever the song loops (no music playing: the wall clock)
      const m = st.atMusic != null && musicClock ? musicClock() : null;
      if (m && m.playing) {
        const span = Math.max(1e-3, T - t0);
        t = t0 + (m.t - st.atMusic) * st.rate;
        if (st.loop) t = t0 + ((((t - t0) % span) + span) % span);
        else if (t > T) { stop({ person: st.person }); continue; } else if (t < t0) t = t0;
        if (t < st.frames[st.i].t) { st.i = 0; st.feet = { l: {}, r: {} }; st.body = null; }   // the loop or the song wrapped
        st.t0 = performance.now() - (t - t0) / st.rate * 1000;    // the wall clock carries on from here if it stops
      }
      if (t > T) { if (!st.loop) { stop({ person: st.person }); continue; } st.t0 = performance.now(); t = t0; st.i = 0; st.feet = { l: {}, r: {} }; st.body = null; }
      while (st.i < st.frames.length - 1 && st.frames[st.i + 1].t <= t) st.i++;
      // between the two samples around t, not the last one held (interp.js mixTake); a gap over GAP_S (tracking lost,
      // a cut) is held, not swept across
      const fa = st.frames[st.i], fb = st.frames[st.i + 1];
      if (fb && fb.t - fa.t < GAP_S && t > fa.t) st.mix = mixTake(fa, fb, (t - fa.t) / (fb.t - fa.t), st.mix);
      pose(st, fb && fb.t - fa.t < GAP_S && t > fa.t ? st.mix : fa);
    }
  }
  ed.preRender.push(update);
  const canPlay = (person) => !!world().actors[person];
  const setSource = (fn) => { source = fn; };
  let unloadedOf = () => null;                              // loadsets.js: the unloaded set a person is in, or null
  const setUnloaded = (fn) => { unloadedOf = fn; };
  let musicClock = null;                                   // music.js now(): takes played on the music read it
  const setMusicClock = (fn) => { musicClock = fn; };
  // where a playing take is now (its own clock, seconds) and its span
  const at = (person) => { const st = playing.get(person); return st && st.frames ? { t: st.frames[st.i].t, t0: st.frames[0].t, t1: st.frames[st.frames.length - 1].t } : null; };
  // the mirror of each person's latest Follow (kept after it stops: the take kept from it plays the same way)
  const lastMirror = new Map();
  const mirrorOf = (person) => !!lastMirror.get(person);
  function setMirror(person, on) {
    const st = playing.get(person);
    if (!st) return null;
    st.mirror = !!on;
    if (st.live) lastMirror.set(person, st.mirror);
    live.emit('actor_mirror', { person, mirror: st.mirror });
    return { person, mirror: st.mirror };
  }
  // ---- pins: per person, for this page's session (an agent sets them before a Follow; the Follow panel's Pin too)
  const anchors = new Map();                     // person -> { hips: Vector3|null, foot_l, foot_r, legs }
  const pinnedNames = (st) => (st.pins ? Object.entries(st.pins).filter(([k, v]) => v && k !== 'legs').map(([k]) => k) : []);
  function usePins(st, pins) {
    st.pins = { hips: pins.hips && pins.hips.clone(), foot_l: pins.foot_l && pins.foot_l.clone(), foot_r: pins.foot_r && pins.foot_r.clone(), legs: pins.legs || 'keep_pose' };
    // the person's own facing (world.json facings, or where they face now); the head no longer turns the hips
    const fc = facingOf(ed, st.person, st.to, null);
    st.pinYaw = fc ? new THREE.Quaternion().setFromUnitVectors(st.rig.fwd, fc.clone().setY(0).normalize()) : (st.lastYaw ? st.lastYaw.clone() : null);
    st.awaySince = 0;
  }
  // pins as a take keeps them (Blender xyz) and back
  const t2bPin = (v) => (v ? [+v.x.toFixed(4), +(-v.z).toFixed(4), +v.y.toFixed(4)] : null);
  function pinsMeta(person) {
    const a = anchors.get(person);
    if (!a) return null;
    const out = { legs: a.legs || 'keep_pose' };
    for (const k of ['hips', 'foot_l', 'foot_r']) if (a[k]) out[k] = t2bPin(a[k]);
    return out.hips || out.foot_l || out.foot_r ? out : null;
  }
  function pinsFromMeta(m) {
    const out = { legs: m.legs || 'keep_pose' };
    for (const k of ['hips', 'foot_l', 'foot_r']) out[k] = Array.isArray(m[k]) ? b2tPos(m[k]) : null;
    return out;
  }
  // where a pin goes: an object's top (a seat; the hips sit SEAT_ABOVE over it), or a Blender xyz
  function pinPoint(to, joint) {
    if (Array.isArray(to)) return b2tPos(to);
    const it = ed.byName.get(to);
    if (!it) throw new Error('no object ' + to + ' to pin to (an object name, or [x, y, z] in Blender metres)');
    const b = new THREE.Box3().setFromObject(it.obj), c = b.getCenter(new THREE.Vector3());
    return new THREE.Vector3(c.x, b.max.y + (joint === 'hips' ? SEAT_ABOVE : 0), c.z);
  }
  // live: follow_anchor {person, joint: hips | foot_l | foot_r | feet, to: object | [x,y,z] | 'here', legs, clear}
  function anchor(c) {
    const person = c.person;
    if (!person || !world().actors[person] && !ed.byName.get(person)) throw new Error('no person ' + person);
    const cur = anchors.get(person) || { hips: null, foot_l: null, foot_r: null, legs: 'keep_pose' };
    const joints = c.joint === 'feet' ? ['foot_l', 'foot_r'] : [c.joint || 'hips'];
    for (const j of joints) if (!['hips', 'foot_l', 'foot_r'].includes(j)) throw new Error("joint is 'hips', 'foot_l', 'foot_r' or 'feet'");
    const st = playing.get(person);
    for (const j of joints) {
      if (c.clear) { cur[j] = null; continue; }
      if (c.to === 'here' || c.to == null) {                 // where that joint is now, while they follow
        if (!st || !st.live) throw new Error('to="here" pins where the person is now: start a Follow first, or give an object or [x, y, z]');
        cur[j] = j === 'hips' ? st.lastHip.clone() : st.rig.bones[j === 'foot_l' ? 'foot_l' : 'foot_r'].getWorldPosition(new THREE.Vector3());
      } else cur[j] = pinPoint(c.to, j);
    }
    if (c.legs) cur.legs = c.legs;
    if (!cur.hips && !cur.foot_l && !cur.foot_r) anchors.delete(person); else anchors.set(person, cur);
    if (st && st.live) { if (anchors.has(person)) usePins(st, cur); else st.pins = null; }
    const t2b = (v) => (v ? [+v.x.toFixed(3), +(-v.z).toFixed(3), +v.y.toFixed(3)] : null);
    const r = { person, pinned: Object.fromEntries(['hips', 'foot_l', 'foot_r'].map((k) => [k, t2b(cur[k])]).filter(([, v]) => v)), legs: cur.legs,
      following: !!(st && st.live) };
    live.emit('anchored_joint', r);
    return r;
  }
  live.handlers.follow_anchor = (c) => anchor(c);
  // ---- start poses (for the user at the bar, 2026-10-05: a Follow took his pose at the press, hands down while Sam's
  // were up). A person can start from a pose of their own (the actor profile's "start", stage_actor_start): in
  // 'relative' mode they hold it at GO and the user's motion plays as changes from the user's pose at GO (the head
  // and spine turn as the head turns, the hands move as the wrists move, scaled, the fingers turn as the user's turn,
  // the hips and legs keep the pose); 'snap' (or no start) is the user's pose, as before. A start pose is:
  //   'rest'           the body's own rest pose, standing at the person's spot, facing their way
  //   {take, frame}    a frame of a recorded take, as it plays on them
  //   {bones: {...}}   a pose from Blender (the armature the statue was baked from): per bone {rest: {head, tail, x}
  //                    in armature space, pose: {head, tail, x} in world}, Blender metres; each bone takes the world
  //                    turn from its rest frame to its posed frame, the pelvis goes where the pose has it
  const b2tV = (v) => new THREE.Vector3(v[0], v[2], -v[1]);
  function frameQ(e) {                                     // a bone's frame (Blender head, tail, x axis) as a turn
    const yv = b2tV(e.tail).sub(b2tV(e.head)).normalize(), xv = b2tV(e.x).normalize();
    xv.sub(yv.clone().multiplyScalar(xv.dot(yv))).normalize();
    const zv = new THREE.Vector3().crossVectors(xv, yv);
    return new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().makeBasis(xv, yv, zv));
  }
  const ARM = { l: ['upperarm_l', 'lowerarm_l', 'hand_l'], r: ['upperarm_r', 'lowerarm_r', 'hand_r'] };
  async function startPose(st, spec) {
    const { rig } = st, pel = rig.bones.pelvis, pose = spec.pose || 'rest';
    for (const [n, b] of Object.entries(rig.bones)) { b.quaternion.copy(rig.local[n]); b.position.copy(rig.localP[n]); }
    rig.root.updateMatrixWorld(true);
    const putPelvis = (w) => { pel.parent.updateMatrixWorld(true); pel.position.copy(pel.parent.worldToLocal(w.clone())); pel.updateMatrixWorld(true); };
    if (pose === 'rest') {
      const at = st.to || st.anchor;                          // in place (or no stand-in yet): where the take stands
      const fc = facingOf(ed, st.person, at, null);
      const yaw = fc ? new THREE.Quaternion().setFromUnitVectors(rig.fwd, fc.clone().setY(0).normalize()) : new THREE.Quaternion();
      putPelvis(new THREE.Vector3(at.x, (st.floor ?? at.y) + rig.rest.pelvis.p.y - rig.rest.foot_l.p.y, at.z));
      setWorldQ(pel, yaw.multiply(rig.rest.pelvis.q));
    } else if (pose.take) {
      const tbase = `scenes/${encodeURIComponent(pose.scene || scn())}/takes/${encodeURIComponent(pose.take)}/`;
      const [meta, txt] = await Promise.all([fetch(tbase + 'meta.json', { cache: 'no-store' }).then((r) => r.json()),
        fetch(tbase + 'frames.jsonl', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('no take ' + pose.take); return r.text(); })]);
      const frames = txt.split('\n').filter(Boolean).map((l) => JSON.parse(l)).filter((f) => f.head);
      const fr = frames[Math.max(0, Math.min(frames.length - 1, pose.frame || 0))];
      if (!fr) throw new Error('take ' + pose.take + ' has no frames');
      const anchor = new THREE.Vector3(frames[0].head[0], world().floor || 0, frames[0].head[2]);
      const actorH = rig.rest.head.p.y - rig.rest.foot_l.p.y + 0.08;            // the take's own scale, as play() has it
      const hs = frames.map((x) => x.head[1]).sort((x, y) => x - y);
      const ts = THREE.MathUtils.clamp(actorH / Math.max(0.5, hs[Math.floor(hs.length * 0.5)] - anchor.y), 0.6, 1.4);
      const facing = facingOf(ed, st.person, st.to, null);
      const tmp = { ...st, s: ts, J: Object.fromEntries((meta.joints || JOINTS).map((n, i) => [n, i])), anchor, pins: null, feet: { l: {}, r: {} },
        turn: turnFor(new THREE.Quaternion(frames[0].head[3], frames[0].head[4], frames[0].head[5], frames[0].head[6]), facing) };
      poseBase(tmp, tmp.turn ? turnFrame(fr, tmp) : fr);
    } else if (pose.bones) {
      const bs = pose.bones;
      rig.root.traverse((b) => {                            // parents first
        const e = b.isBone && bs[b.name];
        if (!e || !e.rest || !e.pose || !rig.rest[b.name]) return;
        const turn = frameQ(e.pose).multiply(frameQ(e.rest).invert());
        setWorldQ(b, turn.multiply(rig.rest[b.name].q.clone()));
      });
      if (bs.pelvis && bs.pelvis.pose) putPelvis(b2tV(bs.pelvis.pose.head));
    } else throw new Error("start pose is 'rest', {take, frame} or {bones: {...}} from Blender");
    rig.root.updateMatrixWorld(true);
    const S = { spec, locals: {}, pelvisLocal: pel.position.clone(), handW: {}, handQ: {}, elbowW: {}, fingerQ: {} };
    for (const [n, b] of Object.entries(rig.bones)) S.locals[n] = b.quaternion.clone();
    for (const sd of ['l', 'r']) {
      const [, lo, ha] = ARM[sd];
      if (!rig.bones[ha]) continue;
      S.handW[sd] = rig.bones[ha].getWorldPosition(new THREE.Vector3());
      S.handQ[sd] = rig.bones[ha].getWorldQuaternion(new THREE.Quaternion());
      S.elbowW[sd] = rig.bones[lo].getWorldPosition(new THREE.Vector3());
      for (const fg of Object.keys(FINGER_JOINTS)) for (let k = 1; k <= 3; k++) {
        const b = rig.bones[`${fg}_0${k}_${sd}`];
        if (b) S.fingerQ[b.name] = b.getWorldQuaternion(new THREE.Quaternion());
      }
    }
    return S;
  }
  const qA = (a) => new THREE.Quaternion(a[3], a[4], a[5], a[6]), vA = (a) => new THREE.Vector3(a[0], a[1], a[2]);
  function poseRelative(st, f) {
    const { rig, s } = st, S = st.start;
    for (const [n, q] of Object.entries(S.locals)) rig.bones[n].quaternion.copy(q);
    rig.bones.pelvis.position.copy(S.pelvisLocal);
    rig.root.updateMatrixWorld(true);
    if (!st.ref) st.ref = st.frames ? prep(st, st.frames[0]) : f;   // the user's pose at GO (a take: its first frame)
    const r = st.ref, I = new THREE.Quaternion();
    const D = qA(f.head).multiply(qA(r.head).invert());
    // the whole body walks and turns with the user from the start pose, as a standing body does (poseBase): the user's
    // steps since GO move the hips past HIP_SLACK, a head turn past YAW_SLACK turns the body, the feet step after it.
    // (The user, 2026-10-08: Pete, who has a start pose, stood with his feet locked and would not walk; this path kept
    // the start pose's hips and legs whatever the user did.) Pinned hips keep the seat.
    const B = moveStartBody(st, f, r, D);
    const Drel = D.clone().multiply(B.yaw.clone().invert());       // what the body's turn left for the spine and head
    const held = Object.fromEntries(SPINE.map(([n]) => [n, rig.bones[n].getWorldQuaternion(new THREE.Quaternion())]));
    for (const [n, k] of SPINE) {
      const q = new THREE.Quaternion().slerpQuaternions(I, Drel, k);
      if (B.lean) q.premultiply(new THREE.Quaternion().slerp(B.lean, Math.min(1, k * 1.25)));
      setWorldQ(rig.bones[n], q.multiply(held[n]));
    }
    for (const [sd, h] of [['l', 'left'], ['r', 'right']]) {
      const now = f[h], was = r[h];
      if (!S.handW[sd] || !now || !now.j || !now.j[0] || !was || !was.j || !was.j[0]) continue;   // untracked: the start pose
      const target = S.handW[sd].clone().add(vA(now.j[0]).sub(vA(was.j[0])).multiplyScalar(s));
      const [up, lo, ha] = ARM[sd];
      twoBone(rig, up, lo, ha, target, S.elbowW[sd].clone().add(B.off).add(new THREE.Vector3(0, -0.3, 0)));
      setWorldQ(rig.bones[ha], qA(now.j[0]).multiply(qA(was.j[0]).invert()).multiply(S.handQ[sd].clone()));
      for (const [fg, names] of Object.entries(FINGER_JOINTS)) names.forEach((jn, k) => {
        const b = rig.bones[`${fg}_0${k + 1}_${sd}`], i = st.J[jn];
        if (!b || !S.fingerQ[b.name] || !now.j[i] || !was.j[i]) return;
        setWorldQ(b, qA(now.j[i]).multiply(qA(was.j[i]).invert()).multiply(S.fingerQ[b.name].clone()));
      });
    }
  }
  // the start pose's body moved by the user's steps and turn (relative mode): hips and facing with slack, legs stepping
  // from the start pose's feet; nothing moved, the start pose's legs as they are
  function moveStartBody(st, f, r, D) {
    const { rig, s } = st, pel = rig.bones.pelvis;
    const P0 = pel.getWorldPosition(new THREE.Vector3()), Q0 = pel.getWorldQuaternion(new THREE.Quaternion());
    const b = st.body || (st.body = { yaw: new THREE.Quaternion(), off: new THREE.Vector3(), feet0: null });
    if (!b.feet0) b.feet0 = Object.fromEntries(['l', 'r'].map((sd) => [sd, {
      p: rig.bones[`foot_${sd}`].getWorldPosition(new THREE.Vector3()), q: rig.bones[`foot_${sd}`].getWorldQuaternion(new THREE.Quaternion()),
      knee: rig.bones[`calf_${sd}`].getWorldPosition(new THREE.Vector3()), thigh: rig.bones[`thigh_${sd}`].getWorldPosition(new THREE.Vector3()) }]));
    const want = vA(f.head).sub(vA(r.head)).setY(0).multiplyScalar(s);
    // a lean: the spine bends from the hips toward where the head went (what the hips did not follow), at most LEAN_MAX
    const lean = () => {
      const up = rig.bones.head.getWorldPosition(new THREE.Vector3()).sub(P0), to = up.clone().add(want.clone().sub(b.off));
      b.lean = new THREE.Quaternion().setFromUnitVectors(up.normalize(), to.normalize());
      const a = 2 * Math.acos(Math.min(1, Math.abs(b.lean.w)));
      if (a > LEAN_MAX) b.lean = new THREE.Quaternion().slerp(b.lean, LEAN_MAX / a);
    };
    if (st.pins && st.pins.hips) { b.yaw.identity(); b.off.set(0, 0, 0); lean(); return b; }
    const f0 = new THREE.Vector3(0, 0, -1).applyQuaternion(D).setY(0);
    const yawD = f0.lengthSq() > 1e-6 ? new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), f0.normalize()) : new THREE.Quaternion();
    const ang = b.yaw.angleTo(yawD);
    if (ang > YAW_SLACK) b.yaw.rotateTowards(yawD, ang - YAW_SLACK);
    const d = want.distanceTo(b.off), slack = HIP_SLACK * s;
    if (d > slack) b.off.lerp(want, (d - slack) / d);
    lean();
    const moved = b.off.lengthSq() > 1e-6 || b.yaw.angleTo(I0) > 1e-3 || ['l', 'r'].some((sd) => st.feet[sd].at && st.feet[sd].at.distanceTo(b.feet0[sd].p) > 1e-3);
    if (!moved) return b;
    pel.parent.updateMatrixWorld(true);
    pel.position.copy(pel.parent.worldToLocal(P0.clone().add(b.off)));
    setWorldQ(pel, b.yaw.clone().multiply(Q0));
    rig.root.updateMatrixWorld(true);
    const hip = P0.clone().add(b.off), now = f.t;
    for (const sd of ['l', 'r']) {
      const F = b.feet0[sd], ft = st.feet[sd], other = st.feet[sd === 'l' ? 'r' : 'l'];
      const goal = hip.clone().add(F.p.clone().sub(P0).applyQuaternion(b.yaw)); goal.y = F.p.y;
      if (!ft.at) ft.at = F.p.clone();
      if (!ft.step && !other.step && ft.at.distanceTo(goal) > STEP_AT) ft.step = { from: ft.at.clone(), to: goal.clone(), t0: now };
      let at = ft.at;
      if (ft.step) {
        const k = Math.min(1, (now - ft.step.t0) / STEP_S);
        at = ft.step.from.clone().lerp(ft.step.to, k); at.y += Math.sin(Math.PI * k) * LIFT;
        if (k >= 1) { ft.at = ft.step.to; ft.step = null; }
      }
      // the knee bends the way it bent in the start pose, turned with the body
      const bend = F.knee.clone().sub(F.thigh.clone().add(F.p).multiplyScalar(0.5)).applyQuaternion(b.yaw);
      const knee = hip.clone().add(F.knee.clone().sub(P0).applyQuaternion(b.yaw)).addScaledVector(bend.lengthSq() > 1e-8 ? bend.normalize() : rig.fwd.clone().applyQuaternion(b.yaw), 0.5);
      twoBone(rig, `thigh_${sd}`, `calf_${sd}`, `foot_${sd}`, at, knee);
      setWorldQ(rig.bones[`foot_${sd}`], b.yaw.clone().multiply(F.q));
    }
    return b;
  }
  const I0 = new THREE.Quaternion();
  async function useStart(st, c) {                          // a Follow or a playback: the person's start pose, if any
    await readProfile(st.rig);
    const spec = c.start || (st.rig.profile && st.rig.profile.start);
    st.start = spec && spec.mode !== 'snap' ? await startPose(st, spec) : null;
    st.ref = null; st.body = null;
  }
  // live: actor_pose {person, t}: where their joints are (Blender metres): now while they follow, at t seconds of
  // the take playing on them, or their start pose when nothing plays
  const READ = ['pelvis', 'spine_03', 'head', 'lowerarm_l', 'hand_l', 'lowerarm_r', 'hand_r', 'calf_l', 'foot_l', 'calf_r', 'foot_r'];
  const t2bV = (v) => [+v.x.toFixed(4), +(-v.z).toFixed(4), +v.y.toFixed(4)];
  const readJoints = (rig) => Object.fromEntries(READ.filter((n) => rig.bones[n]).map((n) => [n, t2bV(rig.bones[n].getWorldPosition(new THREE.Vector3()))]));
  // limb ends that could not reach their target in the last pose, metres short (a centimetre or more)
  const shortOf = (rig) => Object.fromEntries(Object.entries(rig.short || {}).filter(([, v]) => v >= 0.01).map(([k, v]) => [k, +v.toFixed(3)]));
  async function poseOf(c) {
    const person = c.person, st = playing.get(person);
    if (st && st.live) return { person, following: true, start: st.start ? st.start.spec : null, joints: readJoints(st.rig), short: shortOf(st.rig) };
    if (st) {
      let fr = st.frames[st.i];
      if (c.t != null) fr = st.frames.reduce((a, b) => (Math.abs(b.t - c.t) < Math.abs(a.t - c.t) ? b : a));
      pose(st, fr);
      return { person, take: st.take, t: fr.t, start: st.start ? st.start.spec : null, joints: readJoints(st.rig), short: shortOf(st.rig) };
    }
    const it = ed.byName.get(person), who = world().actors[person];
    if (!who) throw new Error('no actor for ' + person);
    let rig = await rigOf(person);
    if ([...playing.values()].some((p) => p.rig === rig)) rig = await cloneRig(rig);
    await readProfile(rig);
    const spec = c.start || rig.profile.start || { pose: 'rest' };
    const w = it ? it.obj.getWorldPosition(new THREE.Vector3()) : new THREE.Vector3();
    const tmp = { person, rig, s: 1, floor: groundOf(it), to: new THREE.Vector3(w.x, groundOf(it), w.z), J: Object.fromEntries(JOINTS.map((n, i) => [n, i])),
      alignInv: new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rig.fwd).invert(), feet: { l: {}, r: {} } };
    await startPose(tmp, spec);
    const joints = readJoints(rig);
    for (const [n, b] of Object.entries(rig.bones)) { b.quaternion.copy(rig.local[n]); b.position.copy(rig.localP[n]); }
    return { person, start: spec, joints };
  }
  live.handlers.actor_pose = (c) => poseOf(c);
  // the control map (control.js): drives per part, on top of the built-in map
  const rigOf = (person) => {
    const who = world().actors[person];
    if (!who) return Promise.reject(new Error('no actor for ' + person + ' (world.json actors)'));
    return load(who, `scenes/${encodeURIComponent(world().assets || scn())}/`);
  };
  const control = initControl(ed, live, { rigOf, readProfile, setWorldQ, twoBone, pinPoint, anchor: (c) => anchor(c), pinsOf: (p) => pinsOf(p) });
  const pinsOf = (person) => { const st = playing.get(person); return st ? pinnedNames(st) : Object.keys(anchors.get(person) || {}).filter((k) => k !== 'legs' && anchors.get(person)[k]); };
  return { play, stop, follow, setSource, playing, load, pose, canPlay, turnBy, setMode, setupOf, moveTo, at, setMirror, anchor, pinsOf, control, rigOf, pinsMeta, mirrorOf, setUnloaded, rest, unrest, setMusicClock };
}
