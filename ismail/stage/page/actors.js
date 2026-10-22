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
import { cutout, b2tPos, GIZMO } from './editor.js';
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
// how a look spreads down the body (the user, 2026-10-16: "turning his head moves the whole upper body instead of the
// head and neck"): a look aside is the neck's (a person turns the head about 70 deg on the neck alone), the chest takes
// a little of it; a nod or a tilt is mostly the neck and head too. Per bone, the share of the look's turn about the
// vertical (YAW) and of the rest of it (SWING), as fractions of the whole look in world space. The head takes it all,
// so it still looks exactly where the user looked. The turn about the vertical is the chest's own (chestFollow below):
// the spine takes CHEST of it, the neck NECK of what is left between the chest and the head.
const LOOK = { spine_01: [0.03, 0.05], spine_02: [0.06, 0.1], spine_03: [0.12, 0.2], neck_01: [0.55, 0.6], head: [1, 1] };
const CHEST = { spine_01: 0.3, spine_02: 0.65, spine_03: 1 }, NECK = 0.55;
// the chest follows the head by a limit, not a share (the user's design, 2026-10-22): within CHEST_LIMIT of the head the
// chest holds still (a look aside is the neck's); past it the chest's target is the head's turn less the limit,
// reached by a soft critically damped follow (CHEST_TAU), never in one step; a fast head turn brings the shoulders in
// sooner, the limit shrinking to CHEST_LIMIT_FAST at CHEST_FAST a second; the chest stays within CHEST_MAX of the hips
const D2R = THREE.MathUtils.degToRad;
const CHEST_LIMIT = D2R(40), CHEST_LIMIT_FAST = D2R(20), CHEST_FAST = D2R(300), CHEST_TAU = 0.25, CHEST_MAX = D2R(45), HEAD_SPEED_S = 0.08, CHEST_HOME_S = 1.5;
// the hands weigh on it (the user, 2026-10-22; handCue): their swing round the vertical through the middle of the hips,
// counted with both hands tracked, CUE_SEP apart, CUE_OUT from that vertical and the two ways of reading the swing
// agreeing within CUE_AGREE; the chest asks HAND_GAIN of the swing
const CUE_SEP = [0.25, 0.35], CUE_OUT = [0.25, 0.4], CUE_AGREE = D2R(25), HAND_GAIN = 0.7;
const STEP_AT = 0.22, STEP_S = 0.28, LIFT = 0.07;
// the forearm's roll (the user, 2026-10-22, following Jo: "his wrists are still twisting ... the entire arm just twists
// around all crazy"). No body here has forearm twist bones (all 53 joints; spreadTwist never ran), so: each arm bone is
// rolled about its own length so its elbow hinge lies across the arm's bend (it kept whatever roll the shortest turn
// from rest gave, which swung as the arm moved), and the hand's turn about the forearm is split, LOWER_ROLL of it on the
// forearm (the elbow end), the wrist keeping at most WRIST_MAX and the forearm at most LOWER_MAX
const LOWER_ROLL = 0.5, WRIST_MAX = THREE.MathUtils.degToRad(50), LOWER_MAX = THREE.MathUtils.degToRad(90);
// steps lead the body (VRIK's velocityFactor): a foot steps to where the hips will be STEP_LEAD s on (at most
// STEP_LEAD_MAX m), so it lands as the body arrives instead of trailing it; a planted foot keeps its own facing (it no
// longer spins with the body) and steps once the body turns STEP_TURN past it; a step eases in and out (smootherstep)
const STEP_LEAD = 0.25, STEP_LEAD_MAX = 0.3, STEP_TURN = THREE.MathUtils.degToRad(35);
const ease = (k) => k * k * k * (k * (k * 6 - 15) + 10);
// a standing body from a headset (the user, 2026-10-08: "when I move my head ... sometimes the rest of my body moves,
// but that's not actually what's going on"): the hips stay put while the head moves within HIP_SLACK of above them (a
// lean, a nod: the spine bends toward the head instead) and trail it beyond; the body turns only when the head is
// turned more than YAW_SLACK from it (a look aside twists the neck and spine, not the feet)
const HIP_SLACK = 0.12, YAW_SLACK = THREE.MathUtils.degToRad(35);
// balance, a first cut at gravity (the user, 2026-10-04: anchor "any part of their physical gravitational stack"), for
// bodies from a start pose (moveStartBody): the drawn hips stay within BAL_MAX of their place over the planted feet,
// easing there in BAL_S, and past that the foot further back steps under the weight, BAL_STEP past its goal. (The same
// idea in poseBase with a counter-lean (hips pushed away from the head), the 2026-10-17 study measured, made following
// worse: hip jitter 3 -> 14 m/s^2, feet sliding 4 cm/s; poseBase now does it this way too)
const BAL_MAX = 0.15, BAL_S = 0.12, BAL_STEP = 0.1, UNDER_S = 0.4;   // UNDER_S: held at the limit this long before the step under
// pins while following (the user, 2026-10-04, sitting in real life while following a man on a bar stool: "anchors his
// butt to the chair, so if I do anything with my head and hands his butt's gonna be anchored unless I move completely
// away from the chair"). Pinned hips stay on the seat, facing the person's own way; the user's head bends the spine
// toward it (direction only, so sitting lower or higher does not sink or lift him) and the hands drive the arms; the
// feet plant in front of the seat (a seated pose) or on their own pins. The head clearly away from the seat
// (PIN_AWAY_M for PIN_AWAY_MS) lets go for that follow.
// a Follow's standing height (setUp, standNow): samples a second, how often it is re-read and eased in, how many are kept
// (the median of the last 5 min), and how many of a feed's frames before it stand for the page's 2 min in VR
const STAND_HZ = 10, STAND_EVERY_S = 0.5, STAND_EASE_S = 1.5, STAND_N = 3000, RECENT_N = 1800;
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
        return { who, root, bones, rest, fwd, left, cal: handCalibration(bones, rest), hinge: elbowHinges(rest, fwd), local: Object.fromEntries(Object.entries(bones).map(([n, b]) => [n, b.quaternion.clone()])),
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

  // ---- the elbow's hinge in each arm bone's own frame: across the arm and the body's forward at rest (an arm bends
  // its hand forward), so a posed arm can be rolled to put it across the bend (twoBone's hinge)
  function elbowHinges(rest, fwd) {
    const out = {};
    for (const sd of ['l', 'r']) {
      const up = rest[`upperarm_${sd}`], lo = rest[`lowerarm_${sd}`], ha = rest[`hand_${sd}`];
      if (!up || !lo || !ha) continue;
      const n = lo.p.clone().sub(up.p).normalize().cross(fwd).normalize();
      if (n.lengthSq() < 0.5) continue;
      out[sd] = { a: n.clone().applyQuaternion(up.q.clone().invert()), b: n.clone().applyQuaternion(lo.q.clone().invert()) };
    }
    return out;
  }
  // roll a bone about its own length (axis, world) so its hinge (bone frame) lies along n (world), as near as the
  // length allows; the child's joint is on that length, so it does not move
  function rollTo(b, axis, hingeLocal, n) {
    const ax = axis.clone().normalize(), h = hingeLocal.clone().applyQuaternion(b.getWorldQuaternion(tq2));
    h.addScaledVector(ax, -h.dot(ax)); const m = n.clone().addScaledVector(ax, -n.dot(ax));
    if (h.lengthSq() < 1e-8 || m.lengthSq() < 1e-8) return;
    h.normalize(); m.normalize();
    const ang = Math.atan2(new THREE.Vector3().crossVectors(h, m).dot(ax), h.dot(m));
    setWorldQ(b, new THREE.Quaternion().setFromAxisAngle(ax, ang).multiply(b.getWorldQuaternion(tq2)));
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
  // hinge {a, b} (arms, elbowHinges): each bone is also rolled about its length so the hinge lies across the bend
  function twoBone(rig, a, bn, cn, target, pole, hinge) {  // a -> b -> c reaches target, bending toward pole
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
    const end = S.clone().addScaledVector(u, d);
    aim(A, B, E);
    // the bend's hinge is v x u (the upper arm's direction crossed with the forearm's, whatever the bend, a straight arm
    // too, as v stands across the arm)
    const n = hinge && v.lengthSq() > 0.5 ? new THREE.Vector3().crossVectors(v, u).normalize() : null;
    if (n) rollTo(A, E.clone().sub(S), hinge.a, n);
    aim(B, C, end);
    if (n) rollTo(B, end.clone().sub(E), hinge.b, n);
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
  // a look (delta: the head's world turn from rest) over the spine: the body's own facing (base) for the hips, then per
  // bone the turn about the vertical from spineYaw (the chest's follow, the neck between it and the head) and its LOOK
  // share of the rest (swing-twist), the lean from the hips on top. The head takes the whole look, so it still looks
  // exactly where the user looked.
  // q or -q (the same turn), whichever has w >= 0, so its look comes out in -180..180: the other gave about 360 minus the
  // turn, which the head did not show (the same turn) but the spine's shares of it did (the 2026-10-17 study: the chest
  // twisted 40 degrees the wrong way, in some runs only, as the tracking's quaternion sign fell)
  function near(q) { return q.w < 0 ? q.set(-q.x, -q.y, -q.z, -q.w) : q; }
  function spreadLook(st, f, base, delta, lean, P, hipAt) {
    const { rig } = st;
    const rel = near(base.clone().invert().multiply(delta));
    const tw = new THREE.Quaternion(0, rel.y, 0, rel.w).normalize();
    const swing = rel.clone().multiply(tw.clone().invert());
    const look = 2 * Math.atan2(tw.y, tw.w);
    const l = f.left && f.left.j && f.left.j[0], r = f.right && f.right.j && f.right.j[0];
    const fw = rig.fwd.clone().applyQuaternion(base).setY(0).normalize();
    const cue = handCue(l && P(l), r && P(r), hipAt, fw);
    const yaws = spineYaw(st, f, look, cue);
    const I = new THREE.Quaternion();
    for (const [n, k] of SPINE) {
      const q = base.clone().multiply(new THREE.Quaternion().slerpQuaternions(I, swing, LOOK[n][1])).multiply(new THREE.Quaternion().setFromAxisAngle(Y, yaws[n]));
      if (lean) q.premultiply(new THREE.Quaternion().slerp(lean, Math.min(1, k * 1.25)));
      setWorldQ(rig.bones[n], q.multiply(rig.rest[n].q));
    }
  }
  // the turn about the vertical per spine bone, relative to the hips: the chest's own (chestFollow), the spine below it
  // its CHEST share, the neck NECK of the way from the chest to the head, the head the whole look
  function spineYaw(st, f, look, cue) {
    const c = chestFollow(st, f, look, cue), out = {};
    for (const [n] of SPINE) out[n] = n === 'head' ? look : n === 'neck_01' ? c + NECK * (look - c) : c * (CHEST[n] ?? 0);
    return out;
  }
  const wrapPi = (a) => Math.atan2(Math.sin(a), Math.cos(a));
  const r1 = (a) => +THREE.MathUtils.radToDeg(a).toFixed(1);
  // the chest's turn about the vertical, relative to the hips (look: the head's), as the user designed it (CHEST_LIMIT
  // above): held within the limit, past it eased toward the head's turn less the limit, the limit narrower for a fast
  // head; the hands' cue pulls the target its way as sure as it is. Critically damped, exact for any frame time:
  // x'' = -(x - g) / tau^2 - 2 x' / tau. A first frame, a loop or a gap starts it where the limit puts it, at rest.
  function chestFollow(st, f, look, cue) {
    const t = f.t, hq = new THREE.Vector3(0, 0, -1).applyQuaternion(new THREE.Quaternion(f.head[3], f.head[4], f.head[5], f.head[6]));
    const hw = Math.atan2(hq.x, hq.z);                    // the head's own heading (world), for its speed
    let c = st.chest;
    const dt = c ? t - c.t : 0;
    if (!c || dt < 0 || dt > GAP_S) {
      const x = THREE.MathUtils.clamp(look - THREE.MathUtils.clamp(look, -CHEST_LIMIT, CHEST_LIMIT), -CHEST_MAX, CHEST_MAX);
      c = st.chest = { x, h: x, v: 0, t, hw, spd: 0, dbg: null };
    } else if (dt > 1e-5) {
      c.spd += (Math.abs(wrapPi(hw - c.hw)) / dt - c.spd) * (1 - Math.exp(-dt / HEAD_SPEED_S));
      c.hw = hw;
      const L = CHEST_LIMIT - (CHEST_LIMIT - CHEST_LIMIT_FAST) * THREE.MathUtils.clamp(c.spd / CHEST_FAST, 0, 1);
      // the head's ask, kept apart from the chest itself (h): past the limit the head's turn less it; within it the
      // chest does not follow the head, but it comes home to the hips' facing over CHEST_HOME_S (without that it stayed
      // turned up to the limit after a look aside and back). The hands' cue is blended with that ask, not fed back into
      // it (fed back, a weak cue held each frame outweighed the slow homing: Sam's chest stayed 10 deg turned for 7 s)
      const off = look - c.h;
      c.h = Math.abs(off) > L ? look - Math.sign(off) * L : c.h * Math.exp(-dt / CHEST_HOME_S);
      let g = c.h;
      if (cue && cue.w > 0) g += (cue.a - g) * cue.w;
      g = THREE.MathUtils.clamp(g, -CHEST_MAX, CHEST_MAX);
      const om = 1 / CHEST_TAU, y = c.x - g, e = Math.exp(-om * dt), j = (c.v + om * y) * dt;
      c.x = g + (y + j) * e; c.v = (c.v - om * j) * e; c.t = t;
      c.dbg = { L: r1(L), h: r1(c.h), g: r1(g), x: r1(c.x), look: r1(look), spd: Math.round(THREE.MathUtils.radToDeg(c.spd)), w: cue ? +cue.w.toFixed(2) : 0, a: cue ? r1(cue.a) : null };
    }
    return c.x;
  }
  // the hands' twist about the vertical through the middle of the hips (the user, 2026-10-22), yaw only (a lean does
  // not count): the hands swinging round that axis turn the chest, read two ways that must agree, the line from the
  // right hand to the left turning (seen from above) and the hands' middle bearing round the axis (each hand's bearing
  // from its own side): one hand coming forward as the other goes back, or both arcing round the same way, turns both;
  // both hands moving together to one side moves the bearing but not the line, so it does not count. Counted with both
  // hands tracked, CUE_SEP apart and CUE_OUT from the axis (hands at the sides or close in are noise). L, R: the hands
  // where the body reaches (world), axis: the hips, fw: the body's forward (flat). {a: the chest turn it asks (rad,
  // relative to the hips), w: how sure, 0..1}, or null
  function handCue(L, R, axis, fw) {
    if (!L || !R) return null;
    const left = new THREE.Vector3().crossVectors(Y, fw);
    const l = L.clone().sub(axis).setY(0), r = R.clone().sub(axis).setY(0), d = l.clone().sub(r);
    const ang = (v, ref) => Math.atan2(new THREE.Vector3().crossVectors(ref, v).y, ref.dot(v));
    const line = ang(d, left);
    const bl = ang(l, left), br = ang(r, left.clone().negate());
    const bear = Math.atan2(Math.sin(bl) + Math.sin(br), Math.cos(bl) + Math.cos(br));
    const ramp = (x, [a, b]) => THREE.MathUtils.clamp((x - a) / (b - a), 0, 1);
    const w = ramp(d.length(), CUE_SEP) * ramp(Math.min(l.length(), r.length()), CUE_OUT) * THREE.MathUtils.clamp(1 - Math.abs(wrapPi(line - bear)) / CUE_AGREE, 0, 1);
    return { a: THREE.MathUtils.clamp(HAND_GAIN * wrapPi(line + wrapPi(bear - line) / 2), -CHEST_MAX, CHEST_MAX), w };
  }
  // where an elbow points (the user, 2026-10-11: arms "clipping ... around the armpits ... into the chest"): down and a
  // little out and back for a hand at the side; a raised hand swings the elbow out to the side (hands up), a hand
  // across the body brings it out and forward, so the upper arm clears the chest; a hand pulled back toward the
  // shoulder's plane or behind it takes the elbow behind (the user, 2026-10-22, following Jo: "when I pull my hand
  // backwards ... instead of my elbow going behind me ... the elbow is going inwards"): pull runs 0 to 1 from a hand
  // PULL_FROM of the arm in front of the shoulder to PULL_TO behind it. Both halves (a start pose's arms too)
  function elbowPole(rig, sd, sh, hand, lateral, back) {
    const arm = rig.rest[`upperarm_${sd}`].p.distanceTo(rig.rest[`lowerarm_${sd}`].p) + rig.rest[`lowerarm_${sd}`].p.distanceTo(rig.rest[`hand_${sd}`].p);
    const d = hand.clone().sub(sh);
    const up = THREE.MathUtils.clamp(d.y / arm + 0.3, 0, 1), cross = THREE.MathUtils.clamp(-d.dot(lateral) / arm, 0, 1);
    const pull = THREE.MathUtils.clamp((d.dot(back) / arm + PULL_FROM) / (PULL_FROM + PULL_TO), 0, 1);
    return sh.clone().add(new THREE.Vector3(0, -(1 - 0.8 * up) * (1 - 0.5 * pull), 0)).addScaledVector(lateral, 0.5 + 0.6 * up + 0.3 * cross)
      .addScaledVector(back, 0.4 - 0.6 * cross + 1.0 * pull);
  }
  const PULL_FROM = 0.3, PULL_TO = 0.1;
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
    // balance as in moveStartBody: the drawn hips at most BAL_MAX from over the planted feet, eased in BAL_S; the steps
    // follow hipAt (where the head leads) and, held at the limit, the foot further back steps under the weight
    let hipDraw = hipAt;
    const fl = st.feet.l, fr = st.feet.r;
    if (!pin && fl.at && fr.at) {
      const b = st.body, mid = fl.at.clone().add(fr.at).multiplyScalar(0.5);
      const away = hipAt.clone().sub(mid).setY(0);
      b.held = away.length() > BAL_MAX * s ? away.clone().normalize() : null;
      b.heldT = b.held ? (b.heldT ?? f.t) : null;
      if (b.held && f.t - b.heldT < UNDER_S) b.under = null; else b.under = b.held;   // a lean held a moment, not at once
      const tgt = b.held ? mid.clone().addScaledVector(b.held, BAL_MAX * s).setY(hipAt.y) : hipAt.clone();
      const dt = f.t - (b.balT ?? f.t);
      if (!b.bal || dt < 0 || dt > GAP_S) b.bal = tgt.clone(); else b.bal.lerp(tgt, 1 - Math.exp(-dt / BAL_S));
      b.bal.y = hipAt.y; b.balT = f.t;
      hipDraw = b.bal.clone();
    } else if (st.body) st.body.held = st.body.under = null;
    st.lastHip = hipAt.clone(); st.lastYaw = base.clone();             // the body's facing, not the head's
    pel.parent.updateMatrixWorld(true);
    pel.position.copy(pel.parent.worldToLocal(hipDraw.clone()));
    setWorldQ(pel, base.clone().multiply(rig.rest.pelvis.q));
    // the spine leans from the hips (or the seat) toward where the user's head is (its length kept)
    let lean = null;
    {
      const up = rig.rest.head.p.clone().sub(rig.rest.pelvis.p).applyQuaternion(base).normalize();
      const to = head.clone().sub(hipDraw).normalize();
      lean = new THREE.Quaternion().setFromUnitVectors(up, to);
      const ang = 2 * Math.acos(Math.min(1, Math.abs(lean.w)));
      if (ang > LEAN_MAX) lean = new THREE.Quaternion().slerp(lean, LEAN_MAX / ang);
    }
    spreadLook(st, f, base, delta, lean, P, hipDraw);
    // arms and hands, fingers
    const side = (sd, h) => {
      const hd = f[h];
      const sgn = sd === 'l' ? 1 : -1;
      if (!hd || !hd.j || !hd.j[0]) {                      // the hand out of tracking: the arm hangs at the side
        const sh0 = rig.bones[`upperarm_${sd}`].getWorldPosition(new THREE.Vector3());
        const lat = rig.left.clone().applyQuaternion(base).multiplyScalar(sgn * 0.12);
        twoBone(rig, `upperarm_${sd}`, `lowerarm_${sd}`, `hand_${sd}`, sh0.clone().add(lat).add(new THREE.Vector3(0, -0.6, 0)),
          sh0.clone().add(rig.fwd.clone().applyQuaternion(base).multiplyScalar(-1)), rig.hinge[sd]);
        return;
      }
      const lateral = rig.left.clone().applyQuaternion(base).multiplyScalar(sgn);
      const back = rig.fwd.clone().applyQuaternion(base).multiplyScalar(-1);
      // the shoulder takes a share of the arm's swing away from hanging relaxed (a raised arm lifts it), as poseRelative does
      const sh0 = rig.bones[`upperarm_${sd}`].getWorldPosition(new THREE.Vector3());
      shoulderFollow(rig, sd, P(hd.j[0]), sh0.clone().add(new THREE.Vector3(0, -0.55, 0)).addScaledVector(back, -0.12));
      const sh = rig.bones[`upperarm_${sd}`].getWorldPosition(new THREE.Vector3());
      twoBone(rig, `upperarm_${sd}`, `lowerarm_${sd}`, `hand_${sd}`, P(hd.j[0]), elbowPole(rig, sd, sh, P(hd.j[0]), lateral, back), rig.hinge[sd]);
      const jq = (i) => { const a = hd.j[i]; return a ? new THREE.Quaternion(a[3], a[4], a[5], a[6]) : null; };
      const w = jq(0);
      if (w && rig.cal[`hand_${sd}`]) setWorldQ(rig.bones[`hand_${sd}`], w.multiply(rig.cal[`hand_${sd}`]));
      for (const [fg, names] of Object.entries(FINGER_JOINTS)) {
        names.forEach((jn, k) => {
          const b = rig.bones[`${fg}_0${k + 1}_${sd}`], q = jq(J[jn]);
          if (b && q && rig.cal[b.name]) setWorldQ(b, q.multiply(rig.cal[b.name]));
        });
      }
      spreadTwist(rig, sd);
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
    // the hips' velocity, smoothed: where they will be a moment on
    const mv = st.move || (st.move = { at: hipAt.clone(), t: now, v: new THREE.Vector3() });
    const dt = now - mv.t;
    if (dt > 1e-3 && dt < GAP_S) mv.v.lerp(hipAt.clone().sub(mv.at).setY(0).divideScalar(dt), Math.min(1, dt / 0.15));
    else if (dt >= GAP_S || dt < 0) mv.v.set(0, 0, 0);
    mv.at.copy(hipAt); mv.t = now;
    const lead = mv.v.clone().multiplyScalar(STEP_LEAD);
    if (lead.length() > STEP_LEAD_MAX) lead.setLength(STEP_LEAD_MAX);
    for (const sd of ['l', 'r']) {
      const ft = st.feet[sd];
      const want = hipAt.clone().add(rig.rest[`foot_${sd}`].p.clone().sub(rig.rest.pelvis.p).applyQuaternion(base));
      want.y = st.floor + rig.rest[`foot_${sd}`].p.y;
      const pinned = !!(st.pins && st.pins['foot_' + sd]);
      pinFoot(st, sd, want);
      if (!ft.at) { ft.at = want.clone(); ft.q = base.clone(); }
      if (!ft.q) ft.q = base.clone();
      const other = st.feet[sd === 'l' ? 'r' : 'l'];
      const held = !pinned && st.body && st.body.under, lagging = held && other.at && ft.at.clone().sub(other.at).dot(held) < 0;
      if (!ft.step && !other.step && (ft.at.distanceTo(want) > STEP_AT || (lagging && ft.at.distanceTo(want) > 0.05 * st.s)
          || (!pinned && ft.q.angleTo(base) > STEP_TURN))) {
        const to = pinned ? want.clone() : want.clone().add(lead);
        if (lagging) to.addScaledVector(held, BAL_STEP * st.s);
        const why = ft.at.distanceTo(want) > STEP_AT ? 'far' : lagging ? 'under' : 'turn';   // read back by the capture probe
        ft.step = { from: ft.at.clone(), to, q0: ft.q.clone(), q1: base.clone(), t0: now, why };
      }
      let at = ft.at, fq = ft.q;
      if (ft.step) {
        const k = Math.min(1, (now - ft.step.t0) / STEP_S), e = ease(k);
        at = ft.step.from.clone().lerp(ft.step.to, e); at.y += Math.sin(Math.PI * k) * LIFT;
        fq = ft.step.q0.clone().slerp(ft.step.q1, e);
        if (k >= 1) { ft.at = ft.step.to; ft.q = ft.step.q1; ft.step = null; }
      }
      const knee = rig.bones[`thigh_${sd}`].getWorldPosition(new THREE.Vector3()).addScaledVector(rig.fwd.clone().applyQuaternion(fq).add(rig.fwd.clone().applyQuaternion(base)), 0.5);
      twoBone(rig, `thigh_${sd}`, `calf_${sd}`, `foot_${sd}`, at, knee);
      setWorldQ(rig.bones[`foot_${sd}`], fq.clone().multiply(rig.rest[`foot_${sd}`].q));
    }
  }

  // ---- one setup for a played take and a Follow (the 2026-10-22 rework: a fix lands once). Where the person stands
  // and faces, the scale from the user's standing head height, the turn onto the person's facing, the pins, the
  // standing height the crouch reads and the anchor come from the same rules; only the frames differ: a take's (all of
  // them known) or the user's of each moment (a Follow: `live`, its standing height running, standNow).
  //   where     a take borrowed from someone else plays where this person stands (the user, 2026-10-03: "make the
  //             people that are inside the place who can dance, dance"); since live follow ("they stand where they
  //             are, and they follow along with my movements") every take plays where its person stands; c.in_place
  //             plays it where the user stood. A Follow with no stand-in stands where the user is
  //   facing    the person's own (facingOf); a Follow falls back on facing the user, a take on none (no turn)
  //   scale     the user's standing head height (the median of `heights`, over the floor) to the actor's
  //   pins      the take's own (meta.pins, Blender xyz: the user, 2026-10-05, Sam's take played anchored by the feet
  //             though his hips were pinned to the stool), else this session's (stage_follow_anchor)
  //   mirror    as it was made (the user, 2026-10-05: a take recorded with Follow's mirror played back the other way):
  //             the plane is the person's facing through the anchor
  //   anchor    the first frame's head over the user's floor; inside them (embody) over the person's ground
  function setUp(c, { person, rig, it, f0, heights, live = false, embodied = false, meta = null, J }) {
    const floor = world().floor || 0;                      // the user's floor while recording or following
    const placed = !!it && !c.in_place;
    const ground = placed ? groundOf(it) : floor;
    const w = placed ? it.obj.getWorldPosition(new THREE.Vector3()) : null;
    const to = placed ? new THREE.Vector3(w.x, ground, w.z) : live ? new THREE.Vector3(f0.head[0], ground, f0.head[2]) : null;
    const h0 = f0.head;
    const facing = to ? facingOf(ed, person, to, live ? new THREE.Vector3(h0[0], 0, h0[2]) : null) : null;
    const turn = to && h0.length >= 7 ? turnFor(new THREE.Quaternion(h0[3], h0[4], h0[5], h0[6]), facing) : null;
    const userH = median(heights) - floor;
    const align = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rig.fwd);
    const st = { person, rig, J, s: embodied ? 1 : scaleFor(rig, userH), anchor: new THREE.Vector3(h0[0], embodied ? ground : floor, h0[2]),
      to, floor: ground, standY: floor + userH, alignInv: align.invert(), feet: { l: {}, r: {} }, t0: performance.now(), it, turn,
      mirror: !!(c.mirror ?? (meta && meta.mirror)), mirrorN: facing ? new THREE.Vector3(facing.z, 0, -facing.x) : null };
    if (live) st.stand = { floor, embodied, hs: heights.slice(-STAND_N), h: userH, want: userH, lastT: null, readT: null, t: null };
    const pm = c.pins || (meta && meta.pins);
    const pins = pm ? pinsFromMeta(pm) : anchors.get(person);
    if (pins && (pins.hips || pins.foot_l || pins.foot_r)) usePins(st, pins);
    return st;
  }
  const median = (xs) => { const v = [...xs].sort((a, b) => a - b); return v[Math.floor(v.length * 0.5)]; };
  const scaleFor = (rig, userH) => THREE.MathUtils.clamp((rig.rest.head.p.y - rig.rest.foot_l.p.y + 0.08) / Math.max(0.5, userH), 0.6, 1.4);
  // a Follow's standing height as it goes: the user's head sampled STAND_HZ, the median re-read every STAND_EVERY_S and
  // eased in over STAND_EASE_S, so a Follow begun seated or crouched (which took that for standing and made a giant)
  // grows the right size once the user has stood a while; the scale (not inside them) and the crouch's standing height
  function standNow(st, f) {
    const k = st.stand;
    if (!k) return;
    if (k.lastT == null || f.t - k.lastT >= 1 / STAND_HZ || f.t < k.lastT) {
      k.hs.push(f.head[1]); k.lastT = f.t;
      if (k.hs.length > STAND_N) k.hs.splice(0, k.hs.length - STAND_N);
    }
    if (k.readT == null || f.t - k.readT >= STAND_EVERY_S || f.t < k.readT) { k.readT = f.t; k.want = median(k.hs) - k.floor; }
    const dt = k.t == null ? 0 : f.t - k.t;
    k.t = f.t;
    if (dt > 0 && dt < 1) k.h += (k.want - k.h) * (1 - Math.exp(-dt / STAND_EASE_S));
    st.standY = k.floor + k.h;
    if (!k.embodied) st.s = scaleFor(st.rig, k.h);
  }
  let recentHeads = () => [];                               // hands.js: the user's head heights, the last 2 min in VR
  const setRecent = (fn) => { recentHeads = fn; };

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
    const whole = frames;                                    // standing height comes from all of it (below)
    if (Array.isArray(trim) && trim.length === 2) {
      const cut = frames.filter((f) => f.t >= trim[0] && f.t <= trim[1]);
      if (cut.length > 1) frames = cut;
    }
    const rig = playing.size && [...playing.values()].some((p) => p.rig.who === who) ? await cloneRig(rig0) : rig0;
    const it = ed.byName.get(person);
    if (it) it.obj.visible = false;
    scene.add(rig.root);
    // the median of the whole take, not of the part kept: a part spent dipping took the dip for standing, so the knees
    // never bent in it (the 2026-10-17 study: head 1.70-1.90 m in a take standing at 1.87 drew 6 cm of bob)
    const st = setUp(c, { person, rig, it, f0: frames[0], heights: whole.map((f) => f.head[1]), meta,
      J: Object.fromEntries((meta.joints || []).map((n, i) => [n, i])) });
    const s = st.s;
    Object.assign(st, { frames, loop: c.loop !== false, take: c.take, i: 0, rate: c.rate || 1, meta,
      voice: c.voice == null ? null : !!c.voice,           // perform.js: the performance's voice with it
      atMusic: c.at_music == null ? null : +c.at_music });  // on the music clock: the first frame at this song second
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
    const feed = c.feed ? await loadFeed(c.feed) : null;
    if (!source && !feed) throw new Error('no live body source');
    const rig0 = await load(who, `scenes/${encodeURIComponent(c.assets || world().assets || scn())}/`);
    stop({ person });
    const rig = [...playing.values()].some((p) => p.rig.who === who) ? await cloneRig(rig0) : rig0;
    const f0 = feed ? feed.frames[0] : source();
    const it = ed.byName.get(person);
    unrest(person);
    if (it) it.obj.visible = false;
    scene.add(rig.root);
    // standing height: what the page saw of the user in VR before this Follow (hands.js keeps the last 2 min; a feed:
    // the take's seconds before it), then running (standNow); inside them (embody), from the frame at GO, as the view
    // is theirs. Stepping into them (embody.js) also keeps the body unscaled: the user's view is already at their eyes
    const seen = c.embody ? [] : feed ? feed.before.slice(-RECENT_N).map((f) => f.head[1]) : recentHeads();
    const st = setUp(c, { person, rig, it, f0, heights: [...seen, f0.head[1]], live: true, embodied: !!c.embody,
      J: Object.fromEntries(JOINTS.map((n, i) => [n, i])) });
    const s = st.s;
    Object.assign(st, { live: true, mode: c.mode || 'place', drift: new THREE.Vector3(), embodied: !!c.embody,
      last: new THREE.Vector3(f0.head[0], f0.head[1], f0.head[2]) });
    if (feed) { st.feed = feed; st.frames = feed.frames; st.i = 0; }
    // the setup from the body, not a button (the user, 2026-10-08: a standing man came up with his feet locked and would
    // not walk): seated (hips on a seat) stays put, a standing person walks with the user
    if (!c.mode) st.mode = st.pins && st.pins.hips ? 'place' : 'walk';
    if (st.embodied) st.mode = 'walk';                   // inside them, the body stays where the user is: no drift
    await useStart(st, c);
    playing.set(person, st);
    lastMirror.set(person, st.mirror);
    await control.onFollow(person);
    live.emit('actor_follow', { person, actor: who, scale: +s.toFixed(2), pinned: pinnedNames(st), drives: control.state(person),
      start: st.start ? st.start.spec : null, ...(feed ? { feed: feed.take } : {}), ...setupOf(person) });
    return { person, actor: who, following: true, scale: +s.toFixed(2), ...setupOf(person) };
  }
  // a take as the live source of a Follow (c.feed {take, takes?, from?, to?}): a Follow run offline, for captures and
  // tests (the 2026-10-17 study could only stand in for following by playing takes, a different setup); the take's
  // seconds before `from` stand for what the page saw of the user before the Follow began (feed.before)
  async function loadFeed(fd) {
    const base = `scenes/${encodeURIComponent(fd.takes || scn())}/takes/${encodeURIComponent(fd.take)}/`;
    const txt = await fetch(base + 'frames.jsonl', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('no take ' + fd.take); return r.text(); });
    const all = txt.split('\n').filter(Boolean).map((l) => JSON.parse(l)).filter((f) => f.head);
    const from = fd.from != null ? +fd.from : -Infinity, to = fd.to != null ? +fd.to : Infinity;
    const frames = all.filter((f) => f.t >= from && f.t <= to);
    if (frames.length < 2) throw new Error(`feed: take ${fd.take} has ${frames.length} frames between ${fd.from} and ${fd.to} s`);
    return { take: fd.take, frames, before: all.filter((f) => f.t < frames[0].t) };
  }
  // the feed's frame now, on the Follow's clock (held at the end), as a copy: update() writes its t
  function feedNow(st) {
    const fr = st.frames, t = fr[0].t + (performance.now() - st.t0) / 1000;
    while (st.i < fr.length - 1 && fr[st.i + 1].t <= t) st.i++;
    const fa = fr[st.i], fb = fr[st.i + 1];
    if (fb && fb.t - fa.t < GAP_S && t > fa.t) return (st.mix = mixTake(fa, fb, (t - fa.t) / (fb.t - fa.t), st.mix));
    return { ...fa };
  }
  async function cloneRig(r) {                              // a second person on the same actor (couple 3)
    const { clone } = await import('three/addons/utils/SkeletonUtils.js');
    const root = clone(r.root), bones = {};
    root.traverse((o) => { if (o.isBone) bones[o.name] = o; });
    return { ...r, root, bones };
  }
  // a person's selection box and pick follow the body they show, not the statue it stands in for (the user,
  // 2026-10-10: a person with a start pose stood about a foot in front of their box, and what stood inside the box
  // could not be picked): an unseen box round the body's bones, a child of the person's item, so the ray, the fingers
  // and the outline find the person where they are
  const PROXY_PAD = 0.1, PROXY_HEAD = 0.14;                 // metres past the bones; the head bone sits low in the head
  const proxies = new Map();                                // person -> unseen box mesh
  const pb = new THREE.Box3(), pv = new THREE.Vector3(), ps = new THREE.Vector3(), pm = new THREE.Matrix4(), pq = new THREE.Quaternion();
  function fitProxy(person, rig) {
    const it = ed.byName.get(person);
    if (!it) return;
    let m = proxies.get(person);
    if (!m) {
      m = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ visible: false }));
      m.name = `${person}:body`;
      m.userData.pickProxy = true; m.userData.bodyBox = true; m.userData.gizmo = true;
      m.matrixAutoUpdate = false;
      m.layers.set(GIZMO);
      proxies.set(person, m);
    }
    const fresh = m.parent !== it.obj;
    if (fresh) it.obj.add(m);
    rig.root.updateMatrixWorld(true);
    pb.makeEmpty();
    // the body's bones from the pelvis down the tree (the armature's Root stays at the origin whatever the pose)
    (rig.bones.pelvis || rig.root).traverse((b) => { if (b.isBone) pb.expandByPoint(pv.setFromMatrixPosition(b.matrixWorld)); });
    if (pb.isEmpty()) return;
    pb.expandByScalar(PROXY_PAD); pb.max.y += PROXY_HEAD;
    it.obj.updateWorldMatrix(true, false);
    m.matrix.copy(it.obj.matrixWorld).invert().multiply(pm.compose(pb.getCenter(pv), pq.identity(), pb.getSize(ps)));
    m.updateMatrixWorld(true);
    it.standIn = m;
    if (fresh && ed.selected === it) ed.outline.setFromObject(m);
  }
  function dropProxy(person) {
    const m = proxies.get(person), it = ed.byName.get(person);
    if (!m) return;
    proxies.delete(person);
    if (it && it.standIn === m) { delete it.standIn; if (ed.selected === it) ed.outline.setFromObject(it.obj); }
    m.removeFromParent();
    m.geometry.dispose(); m.material.dispose();
  }

  function stop(c) {
    const st = playing.get(c.person);
    if (!st) return { stopped: false };
    playing.delete(c.person);
    scene.remove(st.rig.root);
    dropProxy(c.person);
    if (st.it) st.it.obj.visible = true;
    live.emit('actor_stop', { person: c.person, why: c.why || 'stopped', live: !!st.live && !st.feed });   // a fed Follow is no performance
    rest(c.person).catch(() => {});                       // back to their resting pose, if they have one
    return { stopped: true };
  }

  // ---- resting: a person with a start pose stands in it whenever nothing plays on them, at load and after every stop,
  // instead of the statue baked into the scene (the user, 2026-10-06: the six dancers had stood with their arms out
  // since they were imported; "a pose to leave everything in"). Their own body, posed once; the statue hidden.
  // stage_actor_start(idle=False) keeps the statue for that person.
  const resting = new Map();                                // person -> { rig, spec }
  // a play or a Follow being set up claims its person until it is playing, so a rest still loading (the one its own
  // stop() begins, or a scene reveal's) stands down instead of adding a second body (the user, 2026-10-08: a person
  // with a start pose showed twice while following and recording: the rest finished during useStart's await)
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
    dropProxy(person);
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
    fitProxy(person, r.rig);
    live.emit('actor_rest', { person, start: spec });
    return { person, resting: true, start: spec };
  }
  const restDone = new Set();                               // people the room's rest pass has finished (posed or not)
  async function restAll() {
    for (const person of Object.keys(world().actors || {})) {
      await rest(person).catch((e) => console.warn('[actors] rest', person, e));
      restDone.add(person);
    }
  }
  // at load and on a scene switch (the bodies of the new scene), and on an agent's word after a start pose changed
  // restP: the room's people are in their poses (a capture waits for it before its first frame)
  let restP = Promise.resolve();
  ed.addEventListener('revealed', () => { for (const r of resting.values()) scene.remove(r.rig.root); resting.clear(); for (const p of [...proxies.keys()]) dropProxy(p); restDone.clear(); restP = restAll(); });
  const notResting = () => Object.keys(world().actors || {}).filter((p) => !restDone.has(p) && !playing.has(p));
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
    const says = st.embodied ? 'Inside them: their body is yours. Both thumbs down, held, stops and steps you back out.' : seated ? 'Seated: the hips stay on the seat; your head leans the body, your hands move the arms.'
      : st.mode === 'walk' ? 'Standing: walks as you walk. Leaning or turning your head bends the body, the feet stay.'
        : 'On the spot: dances where they stand while you move around.';
    return { body: seated ? 'seated' : 'standing', mode: st.mode, says, ...(st.embodied ? { inside: true } : {}) };
  }
  function setMode(person, mode) {
    const st = playing.get(person);
    if (!st || !st.live) return null;
    if (st.embodied) return { person, mode: st.mode, ...setupOf(person) };   // inside them: always with the user
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
        const f = st.feed ? feedNow(st) : source && source();
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
        standNow(st, f);
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
    for (const st of playing.values()) fitProxy(st.person, st.rig);
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
    // where the start pose faces (the pelvis's turn from rest, flat): the user's hands are turned onto it at GO
    S.fwdW = rig.rest.pelvis ? rig.fwd.clone().applyQuaternion(pel.getWorldQuaternion(new THREE.Quaternion()).multiply(rig.rest.pelvis.q.clone().invert())).setY(0).normalize() : null;
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
    // the start pose's own hands, read as a twist (handCue): the zero for the hands' cue, as the pose is the zero for
    // the head (a drummer's crossed hands read 42 deg, held: Dell's chest turned 44 deg and stayed)
    const c0 = handCue(S.handW.l, S.handW.r, pel.getWorldPosition(new THREE.Vector3()), S.fwdW || rig.fwd.clone().setY(0).normalize());
    S.cue0 = c0 ? c0.a : 0;
    return S;
  }
  const qA = (a) => new THREE.Quaternion(a[3], a[4], a[5], a[6]), vA = (a) => new THREE.Vector3(a[0], a[1], a[2]);
  // the user's eyes sit above and in front of the head bone; their hands are placed from there
  const EYE_UP = 0.08, EYE_FWD = 0.09;
  // shoulders (the user, 2026-10-10: "my shoulders have to kind of adjust for my hands"; arms went through the body):
  // the clavicle takes a share of the arm's swing away from where the start pose held it, up to a limit
  const SHOULDER_SHARE = 0.3, SHOULDER_MAX = THREE.MathUtils.degToRad(25);
  function shoulderFollow(rig, sd, target, rest) {
    const c = rig.bones[`clavicle_${sd}`], u = rig.bones[`upperarm_${sd}`];
    if (!c || !u || !rest) return;
    const sh = u.getWorldPosition(new THREE.Vector3()), a = rest.clone().sub(sh), b = target.clone().sub(sh);
    if (a.lengthSq() < 1e-6 || b.lengthSq() < 1e-6) return;
    const q = new THREE.Quaternion().setFromUnitVectors(a.normalize(), b.normalize());
    const ang = 2 * Math.acos(Math.min(1, Math.abs(q.w)));
    if (ang < 1e-4) return;
    const part = new THREE.Quaternion().slerp(q, Math.min(SHOULDER_SHARE * ang, SHOULDER_MAX) / ang);
    setWorldQ(c, part.multiply(c.getWorldQuaternion(new THREE.Quaternion())));
  }
  // forearm twist bones, where a body has them (lowerarm_twist_01/02_[lr]; film can export them): the hand's turn about
  // the forearm is spread over them (1/3, 2/3) instead of wringing the wrist; a body without them (all of them so far)
  // takes LOWER_ROLL of it on the forearm itself, about its own length, the wrist at most WRIST_MAX (forearmRoll)
  function spreadTwist(rig, sd) {
    const t1 = rig.bones[`lowerarm_twist_01_${sd}`], t2 = rig.bones[`lowerarm_twist_02_${sd}`];
    const lo = rig.bones[`lowerarm_${sd}`], ha = rig.bones[`hand_${sd}`];
    if (!lo || !ha || !rig.rest[lo.name] || !rig.rest[ha.name]) return;
    if (!t1) { forearmRoll(rig, sd); return; }
    const Ql = lo.getWorldQuaternion(new THREE.Quaternion()), Qh = ha.getWorldQuaternion(new THREE.Quaternion());
    const restLoInv = rig.rest[lo.name].q.clone().invert();
    const rel = Ql.clone().invert().multiply(Qh).multiply(restLoInv.clone().multiply(rig.rest[ha.name].q).invert());
    const tw = new THREE.Quaternion(0, rel.y, 0, rel.w).normalize();       // the turn about the forearm's own axis
    for (const [b, k] of [[t1, 1 / 3], [t2, 2 / 3]]) {
      if (!b || !rig.rest[b.name]) continue;
      setWorldQ(b, Ql.clone().multiply(new THREE.Quaternion().slerp(tw, k)).multiply(restLoInv.clone().multiply(rig.rest[b.name].q)));
    }
    setWorldQ(ha, Qh);                                             // the hand stays where it was put
  }
  function forearmRoll(rig, sd) {
    const lo = rig.bones[`lowerarm_${sd}`], ha = rig.bones[`hand_${sd}`];
    const Ql = lo.getWorldQuaternion(new THREE.Quaternion()), Qh = ha.getWorldQuaternion(new THREE.Quaternion());
    const rel = near(Ql.clone().invert().multiply(Qh).multiply(rig.rest[lo.name].q.clone().invert().multiply(rig.rest[ha.name].q).invert()));
    const tw = 2 * Math.atan2(rel.y, rel.w);                       // the hand's turn about the forearm (+Y on these rigs)
    let k = LOWER_ROLL * tw;
    if (Math.abs(tw - k) > WRIST_MAX) k = tw - Math.sign(tw) * WRIST_MAX;
    k = THREE.MathUtils.clamp(k, -LOWER_MAX, LOWER_MAX);
    if (Math.abs(k) < 1e-4) return;
    setWorldQ(lo, Ql.multiply(new THREE.Quaternion().setFromAxisAngle(Y, k)));
    setWorldQ(ha, Qh);                                             // the hand stays where it was put (the fingers with it)
  }
  function poseRelative(st, f) {
    const { rig, s } = st, S = st.start;
    for (const [n, q] of Object.entries(S.locals)) rig.bones[n].quaternion.copy(q);
    rig.bones.pelvis.position.copy(S.pelvisLocal);
    // pinned hips hold over a start pose too (the user, 2026-10-10: a man pinned to his stool stood up at the bar in
    // playback): the start pose keeps its shape, its hips sit on the pin
    const pin = st.pins && st.pins.hips;
    if (pin) { rig.root.updateMatrixWorld(true); rig.bones.pelvis.position.copy(rig.bones.pelvis.parent.worldToLocal(pin.clone())); }
    rig.root.updateMatrixWorld(true);
    if (!st.ref) st.ref = st.frames ? prep(st, st.frames[0]) : f;   // the user's pose at GO (a take: its first frame)
    const r = st.ref, I = new THREE.Quaternion();
    const D = qA(f.head).multiply(qA(r.head).invert());
    // the whole body walks and turns with the user from the start pose, as a standing body does (poseBase): the user's
    // steps since GO move the hips past HIP_SLACK, a head turn past YAW_SLACK turns the body, the feet step after it.
    // (The user, 2026-10-08: a person with a start pose stood with feet locked and would not walk; this path kept
    // the start pose's hips and legs whatever the user did.) Pinned hips keep the seat.
    const B = moveStartBody(st, f, r, D);
    const Drel = near(D.clone().multiply(B.yaw.clone().invert()));       // what the body's turn left for the spine and head
    const held = Object.fromEntries(SPINE.map(([n]) => [n, rig.bones[n].getWorldQuaternion(new THREE.Quaternion())]));
    const tw = new THREE.Quaternion(0, Drel.y, 0, Drel.w).normalize(), swing = Drel.clone().multiply(tw.clone().invert());
    const look = 2 * Math.atan2(tw.y, tw.w);
    // the hands' cue for the chest, from where the arms will reach (below; the eyes before the look turns the head)
    const byUser = !(S.spec && S.spec.hands === 'pose');
    if (byUser && !st.armMap) {
      const fu = new THREE.Vector3(0, 0, -1).applyQuaternion(qA(r.head)).setY(0);
      const fa = S.fwdW ? S.fwdW.clone() : rig.fwd.clone();
      st.armMap = { R: new THREE.Quaternion().setFromUnitVectors(fu.lengthSq() > 1e-6 ? fu.normalize() : fa, fa.setY(0).normalize()) };
    }
    const M = byUser ? B.yaw.clone().multiply(st.armMap.R) : null;
    const fwdNow = (S.fwdW ? S.fwdW.clone() : rig.fwd.clone()).applyQuaternion(B.yaw).setY(0).normalize();
    const reach = (eye, sd, h) => {                                    // where a hand reaches, or null (untracked)
      const now = f[h], was = r[h];
      if (!S.handW[sd] || !now || !now.j || !now.j[0] || !was || !was.j || !was.j[0]) return null;
      return byUser ? eye.clone().add(vA(now.j[0]).sub(vA(f.head)).applyQuaternion(M).multiplyScalar(s))
        : S.handW[sd].clone().add(vA(now.j[0]).sub(vA(was.j[0])).multiplyScalar(s));
    };
    const eyeAt = () => rig.bones.head.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, EYE_UP, 0)).addScaledVector(fwdNow, EYE_FWD);
    rig.root.updateMatrixWorld(true);
    const eye0 = eyeAt();
    const cue = handCue(reach(eye0, 'l', 'left'), reach(eye0, 'r', 'right'), rig.bones.pelvis.getWorldPosition(new THREE.Vector3()), fwdNow);
    if (cue) cue.a = THREE.MathUtils.clamp(wrapPi(cue.a - (S.cue0 || 0)), -CHEST_MAX, CHEST_MAX);   // from the start pose's own
    const yaws = spineYaw(st, f, look, cue);
    for (const [n, k] of SPINE) {                           // the look over the spine as in poseBase (spineYaw, LOOK)
      const q = new THREE.Quaternion().slerpQuaternions(I, swing, LOOK[n][1]).multiply(new THREE.Quaternion().setFromAxisAngle(Y, yaws[n]));
      if (B.lean) q.premultiply(new THREE.Quaternion().slerp(B.lean, Math.min(1, k * 1.25)));
      setWorldQ(rig.bones[n], q.multiply(held[n]));
    }
    // hands and fingers (the user, 2026-10-10, following a drummer posed with sticks: "the starting position of the hands
    // is not reset to what my hands' position is when we start ... that's why the fingers get all messed up"): at GO the
    // person's hands and fingers take the user's own, measured from the eyes and turned to the person's facing, so a
    // seated body reaches where the user reaches; start.hands = 'pose' keeps the start pose's grip and adds the user's
    // motion to it, as before
    rig.root.updateMatrixWorld(true);
    const eye = eyeAt();
    for (const [sd, h] of [['l', 'left'], ['r', 'right']]) {
      const now = f[h], was = r[h];
      const target = reach(eye, sd, h);
      if (!target) continue;                               // untracked: the start pose
      const [up, lo, ha] = ARM[sd];
      shoulderFollow(rig, sd, target, S.handW[sd].clone().add(B.draw || B.off));
      // the elbow: the user's own hands (byUser) point it as poseBase does, in the body's frame now (it was the start
      // pose's elbow, held where it was when the body turned, so a turned body or a hand pulled back behind it bent the
      // elbow inward: the user, 2026-10-22, following Jo); a start pose's own grip (hands: 'pose', the drummer) keeps its
      // elbow, turned with the body
      const sh = rig.bones[up].getWorldPosition(new THREE.Vector3());
      const sgn = sd === 'l' ? 1 : -1, lateral = new THREE.Vector3().crossVectors(Y, fwdNow).multiplyScalar(sgn);
      const P0 = rig.bones.pelvis.getWorldPosition(new THREE.Vector3());
      const pole = byUser ? elbowPole(rig, sd, sh, target, lateral, fwdNow.clone().negate())
        : S.elbowW[sd].clone().add(B.off).sub(P0).applyQuaternion(B.yaw).add(P0).add(new THREE.Vector3(0, -0.3, 0));
      twoBone(rig, up, lo, ha, target, pole, rig.hinge[sd]);
      if (byUser && rig.cal[ha]) setWorldQ(rig.bones[ha], M.clone().multiply(qA(now.j[0])).multiply(rig.cal[ha]));
      else setWorldQ(rig.bones[ha], qA(now.j[0]).multiply(qA(was.j[0]).invert()).multiply(S.handQ[sd].clone()));
      for (const [fg, names] of Object.entries(FINGER_JOINTS)) names.forEach((jn, k) => {
        const b = rig.bones[`${fg}_0${k + 1}_${sd}`], i = st.J[jn];
        if (!b || !now.j[i]) return;
        if (byUser && rig.cal[b.name]) setWorldQ(b, M.clone().multiply(qA(now.j[i])).multiply(rig.cal[b.name]));
        else if (S.fingerQ[b.name] && was.j[i]) setWorldQ(b, qA(now.j[i]).multiply(qA(was.j[i]).invert()).multiply(S.fingerQ[b.name].clone()));
      });
      spreadTwist(rig, sd);
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
    const lean = (o = b.off) => {
      const up = rig.bones.head.getWorldPosition(new THREE.Vector3()).sub(P0), to = up.clone().add(want.clone().sub(o).setY(0));
      b.lean = new THREE.Quaternion().setFromUnitVectors(up.normalize(), to.normalize());
      const a = 2 * Math.acos(Math.min(1, Math.abs(b.lean.w)));
      if (a > LEAN_MAX) b.lean = new THREE.Quaternion().slerp(b.lean, LEAN_MAX / a);
    };
    if (st.pins && st.pins.hips) { b.yaw.identity(); b.off.set(0, 0, 0); b.draw = null; lean(); return b; }
    const f0 = new THREE.Vector3(0, 0, -1).applyQuaternion(D).setY(0);
    const yawD = f0.lengthSq() > 1e-6 ? new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), f0.normalize()) : new THREE.Quaternion();
    const ang = b.yaw.angleTo(yawD);
    if (ang > YAW_SLACK) b.yaw.rotateTowards(yawD, ang - YAW_SLACK);
    const d = want.distanceTo(b.off), slack = HIP_SLACK * s;
    if (d > slack) b.off.lerp(want, (d - slack) / d);
    // gravity in start-pose mode (the 2026-10-17 probe: a dancer's 34 cm bob drew as 3 cm, his hips at one height and
    // up to 23 cm off his planted feet): the head going down from the start pose takes the hips down with it, so the
    // knees bend (going up never lifts them off straight legs); and the drawn hips stay within BAL_MAX of their place
    // over the planted feet, the spine leaning the rest of the way to the head, while the steps still follow the hips
    // the head leads (b.off)
    // (standing start poses only: a seated one, hips well under a leg's length up, keeps its seat)
    const leg = rig.rest.thigh_l.p.distanceTo(rig.rest.calf_l.p) + rig.rest.calf_l.p.distanceTo(rig.rest.foot_l.p);
    const standing = P0.y - Math.min(b.feet0.l.p.y, b.feet0.r.p.y) > 0.8 * leg;
    // from the start frame's head, or the take's standing height when that is lower: a part starting raised on the
    // thumbstick (2026-10-17 study: 30 cm above standing) otherwise drew four seconds of bent knees once back down
    const drop = standing ? Math.min(0, (f.head[1] - Math.min(r.head[1], st.standY ?? r.head[1])) * s) : 0;
    const S = ['l', 'r'].map((sd) => st.feet[sd].at || b.feet0[sd].p).reduce((a, v) => a.add(v), new THREE.Vector3()).multiplyScalar(0.5);
    const S0 = b.feet0.l.p.clone().add(b.feet0.r.p).multiplyScalar(0.5);
    const away = P0.clone().add(b.off).sub(S).sub(P0.clone().sub(S0)).setY(0);
    const back = standing && away.length() > BAL_MAX * s ? away.clone().setLength(away.length() - BAL_MAX * s).negate() : new THREE.Vector3();
    // eased (BAL_S): a foot landing moves the feet's middle at once, and the hips must not jump with it (the probe: hip
    // jitter 2 -> 20 m/s^2 on a walk without this)
    b.heldT = back.lengthSq() > 1e-6 ? (b.heldT ?? f.t) : null;
    const o0 = b.off.clone().add(back).setY(drop), dt = f.t - (b.drawT ?? f.t);
    if (!b.drawS || dt < 0 || dt > GAP_S) b.drawS = o0.clone(); else b.drawS.lerp(o0, 1 - Math.exp(-dt / BAL_S));
    b.drawT = f.t;
    const o = b.drawS.clone();
    b.draw = o;                                                         // where the hips are drawn (the arms' rest moves with them)
    lean(o);
    const moved = b.off.lengthSq() > 1e-6 || drop < -1e-3 || back.lengthSq() > 1e-6 || b.yaw.angleTo(I0) > 1e-3 || ['l', 'r'].some((sd) => st.feet[sd].at && st.feet[sd].at.distanceTo(b.feet0[sd].p) > 1e-3)
      || ['l', 'r'].some((sd) => st.pins && st.pins['foot_' + sd] && st.pins['foot_' + sd].distanceTo(b.feet0[sd].p) > 1e-3);
    if (!moved) return b;
    pel.parent.updateMatrixWorld(true);
    pel.position.copy(pel.parent.worldToLocal(P0.clone().add(o)));
    setWorldQ(pel, b.yaw.clone().multiply(Q0));
    rig.root.updateMatrixWorld(true);
    const hip = P0.clone().add(o), go = P0.clone().add(b.off), now = f.t;   // drawn hips; the hips the steps follow
    for (const sd of ['l', 'r']) {
      const F = b.feet0[sd], ft = st.feet[sd], other = st.feet[sd === 'l' ? 'r' : 'l'];
      const goal = go.clone().add(F.p.clone().sub(P0).applyQuaternion(b.yaw)); goal.y = F.p.y;
      pinFoot(st, sd, goal);
      if (!ft.at) ft.at = F.p.clone();
      // the weight past the feet (the hips held BAL_MAX off them): the foot further from where the head leads steps,
      // a little past its goal, as a person leaning in takes a step instead of tipping
      const lagging = back.lengthSq() > 1e-6 && f.t - b.heldT >= UNDER_S && other.at && ft.at.clone().sub(other.at).dot(away) < 0;
      if (!ft.step && !other.step && (ft.at.distanceTo(goal) > STEP_AT || (lagging && ft.at.distanceTo(goal) > 0.05 * s)))
        ft.step = { from: ft.at.clone(), to: lagging ? goal.clone().addScaledVector(away.clone().normalize(), BAL_STEP * s) : goal.clone(), t0: now,
          why: ft.at.distanceTo(goal) > STEP_AT ? 'far' : 'under' };
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
  // a standing foot with a pin of its own (stage_follow_anchor joint foot_l / foot_r) goes to the pin and stays there:
  // it never steps after the hips, the other foot does (until 2026-10-12 only the seated legs read foot pins)
  function pinFoot(st, sd, goal) {
    const own = st.pins && st.pins['foot_' + sd];
    if (!own) return;
    goal.copy(own);
    const ft = st.feet[sd];
    if (!ft.step && (!ft.at || ft.at.distanceTo(own) > 1e-4)) ft.step = { from: (ft.at || own).clone(), to: own.clone(), t0: -Infinity };
  }
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
  // a bone of the body a person shows now (playing, following or resting), for props pinned to it (props.js)
  const shownRig = (person) => { const st = playing.get(person) || resting.get(person); return st ? st.rig : null; };
  const boneOf = (person, bone) => { const r = shownRig(person); return (r && r.bones[bone]) || null; };
  // where a person's eyes are and which way they face, for stepping into them (embody.js): a body resting in a start
  // pose, its head; anyone else, above their spot at their height, where a Follow puts the head at GO
  async function eyesOf(person, user) {
    const it = ed.byName.get(person);
    if (!it) throw new Error('no such person: ' + person);
    const rig = await rigOf(person), ground = groundOf(it);
    const w = it.obj.getWorldPosition(new THREE.Vector3()), toW = new THREE.Vector3(w.x, ground, w.z);
    const facing = facingOf(ed, person, toW, user ? new THREE.Vector3(user.x, 0, user.z) : null);
    const r = resting.get(person), hb = r && r.rig.bones.head;
    let eye;
    if (hb) {
      eye = hb.getWorldPosition(new THREE.Vector3()).add(new THREE.Vector3(0, EYE_UP, 0));
      if (facing) eye.addScaledVector(facing, EYE_FWD);
    } else eye = toW.clone().add(new THREE.Vector3(0, rig.rest.head.p.y - rig.rest.foot_l.p.y + 0.08, 0));
    return { eye, facing, ground };
  }
  const bonesOf = (person) => { const r = shownRig(person); return r ? Object.keys(r.bones).sort() : null; };
  return { play, stop, follow, setSource, setRecent, playing, load, pose, canPlay, turnBy, setMode, setupOf, moveTo, at, setMirror, anchor, pinsOf, control, rigOf, pinsMeta, mirrorOf, setUnloaded, rest, unrest, setMusicClock,
    rested: () => restP, notResting, boneOf, bonesOf, shownRig, eyesOf };
}
