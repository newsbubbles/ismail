// Replay a take as input (the user, 2026-10-15: "use the take of me holding my hand over some object to pick it up,
// then dropping it somewhere as a replayable take on the virtual hands and head itself, to use it for testing"). A
// recorded take drives the page's head and hands while nobody is in VR: the camera takes the head's pose, and each
// tracked hand of the take becomes a hand on xr.ctls (a handedness, 25 joints with their radii), so hands.js reads its
// gestures, touch.js its pinches and fists and grab.js its grabs and drops from the take, through the same code as a
// real hand. What the page did comes back as events, so a test can read "grabbed 8-ball, landed on the bar" offline.
//   Only hands: a take recorded with controllers has no joints, and the runtime's pinch selects are never recorded,
//   so a select, a squeeze or a stick does not replay. The target ray (aiming, a gun click's travel) is approximated
//   from the head through the palm. Grab counts use wall time: rate 1 for a grab verdict.
//   Refused while the page is in VR (it never takes over the person's own hands), and stopped if VR starts.
//   live: replay_input {take, takes?, from?, to?, rate?, grab?, show?} -> {take, seconds, frames, hands}
//         replay_input {stop: true}
//   events: replay_start, replay_done {take, seconds, frames, events: [{t, type, ...}], recorded: [the take's own
//           grab and drop marks], held: [what a hand still held at the end]}
import * as THREE from 'three';
import { JOINTS } from './hands.js';

const SKIP = new Set(['cmd_done', 'replay_start', 'replay_done', 'page_state', 'page_load', 'actor_rest']);   // the page's own noise
const MAX_EVENTS = 500;

export function initReplay(ed, live, xr, hands, grab, desktop) {
  const camera = ed.camera, rig = ed.rig;
  let R = null;
  const ball = new THREE.SphereGeometry(1, 8, 6);
  const mats = { left: new THREE.MeshBasicMaterial({ color: 0x38bdf8 }), right: new THREE.MeshBasicMaterial({ color: 0xf97316 }) };

  // the page's own events while a take plays, kept for replay_done
  live.onEmit((type, data) => {
    if (!R || SKIP.has(type) || R.events.length >= MAX_EVENTS) return;
    const { page, ...rest } = data || {};
    R.events.push({ t: +R.at.toFixed(2), type, ...rest });
  });

  function parse(txt) {
    const frames = [];
    for (const line of txt.split('\n')) { if (line.trim()) frames.push(JSON.parse(line)); }
    frames.sort((a, b) => a.t - b.t);
    return frames;
  }

  // a synthetic hand on one of xr's controller slots: three's XRHand group gets joint objects like the runtime's
  function fakeHand(s, side, show) {
    const saved = { src: s.src, cp: s.c.position.clone(), cq: s.c.quaternion.clone(), cauto: s.c.matrixAutoUpdate, hvis: s.hand.visible, joints: { ...s.hand.joints } };
    s.src = { handedness: side, hand: { size: JOINTS.length }, gamepad: null, targetRayMode: 'tracked-pointer', profiles: [], replay: true };
    s.c.matrixAutoUpdate = true;
    s.hand.visible = !!show;                       // three keeps a hand group hidden until a session shows it
    const made = {};
    for (const n of JOINTS) {
      const j = new THREE.Group();
      j.name = 'replay_' + n;
      j.jointRadius = 0.008;
      j.visible = false;
      if (show) { const m = new THREE.Mesh(ball, mats[side]); m.name = 'replay_ball'; j.add(m); }
      s.hand.add(j);
      s.hand.joints[n] = made[n] = j;
    }
    return { s, side, saved, made };
  }
  function unfake(f) {
    for (const j of Object.values(f.made)) j.removeFromParent();
    for (const n of Object.keys(f.s.hand.joints)) delete f.s.hand.joints[n];
    Object.assign(f.s.hand.joints, f.saved.joints);
    f.s.src = f.saved.src;
    f.s.hand.visible = f.saved.hvis;
    f.s.c.position.copy(f.saved.cp); f.s.c.quaternion.copy(f.saved.cq); f.s.c.matrixAutoUpdate = f.saved.cauto;
    f.s.c.updateMatrixWorld(true);
  }

  // the take at time t: positions lerped, rotations slerped between the two frames around it
  const pa = new THREE.Vector3(), pb = new THREE.Vector3(), qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
  const wm = new THREE.Matrix4(), inv = new THREE.Matrix4(), one = new THREE.Vector3(1, 1, 1);
  function lerpPose(a, b, k, outP, outQ) {
    pa.fromArray(a, 0); qa.fromArray(a, 3);
    if (b) { pb.fromArray(b, 0); qb.fromArray(b, 3); pa.lerp(pb, k); qa.slerp(qb, k); }
    outP.copy(pa); outQ.copy(qa);
  }
  // put a child of `parent` at a world pose
  function placeWorld(o, parent, p, q) {
    parent.updateMatrixWorld(true);
    inv.copy(parent.matrixWorld).invert();
    wm.compose(p, q, one).premultiply(inv);
    wm.decompose(o.position, o.quaternion, new THREE.Vector3());
    o.updateMatrixWorld(true);
  }

  const hp = new THREE.Vector3(), hq = new THREE.Quaternion(), jp = new THREE.Vector3(), jq = new THREE.Quaternion();
  function pose(t) {
    const fr = R.frames;
    while (R.i < fr.length - 2 && fr[R.i + 1].t <= t) R.i++;
    const a = fr[R.i], b = fr[R.i + 1] && fr[R.i + 1].t > a.t ? fr[R.i + 1] : null;
    const k = b ? Math.min(1, Math.max(0, (t - a.t) / (b.t - a.t))) : 0;
    if (a.head) {
      lerpPose(a.head, b && b.head, k, hp, hq);
      placeWorld(camera, camera.parent || rig, hp, hq);
    }
    const head = camera.getWorldPosition(new THREE.Vector3()), hQ = camera.getWorldQuaternion(new THREE.Quaternion());
    for (const f of R.fakes) {
      const ha = a[f.side], hb = b && b[f.side];
      const on = !!(ha && ha.j);
      for (let i = 0; i < JOINTS.length; i++) {
        const j = f.made[JOINTS[i]], ja = on && ha.j[i], jb = hb && hb.j && hb.j[i];
        if (!ja) { j.visible = false; continue; }
        lerpPose(ja, jb, k, jp, jq);
        placeWorld(j, f.s.hand, jp, jq);
        j.jointRadius = (jb ? ja[7] + (jb[7] - ja[7]) * k : ja[7]) || 0.008;
        j.scale.setScalar(1);
        const m = j.children[0];
        if (m) m.scale.setScalar(j.jointRadius);
        j.visible = true;
      }
      // the target ray, roughly as a runtime makes it for a tracked hand: from the shoulder through the palm
      if (on && f.made.wrist.visible) {
        const palm = f.made.wrist.getWorldPosition(new THREE.Vector3())
          .add(f.made['middle-finger-metacarpal'].getWorldPosition(new THREE.Vector3())).multiplyScalar(0.5);
        const shoulder = new THREE.Vector3(f.side === 'left' ? -0.18 : 0.18, -0.15, 0).applyQuaternion(hQ).add(head);
        const dir = palm.clone().sub(shoulder).normalize();
        const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), dir);
        placeWorld(f.s.c, f.s.c.parent || rig, palm, q);
      }
    }
  }

  async function start(c) {
    if (ed.renderer.xr.isPresenting) throw new Error('someone is in VR on this page: a replay never takes over their hands; run it on a page nobody wears');
    if (R) throw new Error(`a replay is already running (${R.take}); replay_input {stop: true} ends it`);
    if (!c.take) throw new Error('no take given (stage_takes lists them)');
    const scn = c.takes || ed.sceneName;
    const base = `scenes/${encodeURIComponent(scn)}/takes/${encodeURIComponent(c.take)}/`;
    const r = await fetch(base + 'frames.jsonl', { cache: 'no-store' });
    if (!r.ok) throw new Error(`no take ${c.take} in ${scn}`);
    let frames = parse(await r.text());
    const from = c.from != null ? +c.from : 0, to = c.to != null ? +c.to : Infinity;
    frames = frames.filter((f) => f.t >= from - 0.05 && f.t <= to + 0.05);
    if (frames.length < 2) throw new Error(`take ${c.take} has ${frames.length} frames between ${from} and ${to} s`);
    const sides = ['left', 'right'].filter((sd) => frames.some((f) => f[sd] && f[sd].j));
    if (!sides.length) throw new Error(`take ${c.take} has no tracked hands (controllers only): a replay drives hands`);
    const recorded = [];
    for (const f of frames) for (const e of f.ev || []) recorded.push(e);
    const fakes = sides.map((sd, i) => fakeHand(xr.ctls[i], sd, c.show !== false));
    const orbitWas = desktop.orbit.enabled;
    desktop.orbit.enabled = false;
    const grabWas = c.grab != null ? !!live.handlers.grab_mode({}).on : null;   // grab_mode with nothing set reads it
    if (c.grab != null) live.handlers.grab_mode({ on: !!c.grab });
    const t0 = frames[0].t, t1 = frames[frames.length - 1].t, rate = Math.max(0.05, +c.rate || 1);
    R = { take: c.take, frames, fakes, i: 0, t0, t1, rate, wall0: performance.now(), at: t0, events: [], recorded,
      view: desktop.saveView(), orbitWas, grabWas, grabSet: c.grab != null };
    pose(t0);
    live.emit('replay_start', { take: c.take, from: t0, to: t1, rate, hands: sides });
    return { take: c.take, from: +t0.toFixed(2), to: +t1.toFixed(2), seconds: +((t1 - t0) / rate).toFixed(2), frames: frames.length, hands: sides };
  }

  function finish(why) {
    const r = R;
    R = null;
    for (const f of r.fakes) unfake(f);
    const held = ['left', 'right'].map((sd) => grab.held(sd)).filter(Boolean).map((it) => it.name);
    if (held.length) grab.letGoAll('replay ended');   // no hand is left holding a thing once the take's hands are gone
    if (r.grabSet) live.handlers.grab_mode({ on: r.grabWas });   // back off lets go of what the replay still held
    desktop.restoreView(r.view);
    desktop.orbit.enabled = r.orbitWas;
    const out = { take: r.take, why, seconds: +((performance.now() - r.wall0) / 1000).toFixed(2), frames: r.frames.length,
      events: r.events, recorded: r.recorded, held };
    live.emit('replay_done', out);
    return out;
  }

  // per frame, before hands, touch and grab read the hands (main.js); true while a take plays
  function update() {
    if (!R) return false;
    if (ed.renderer.xr.isPresenting) { finish('VR started'); return false; }
    const t = R.t0 + ((performance.now() - R.wall0) / 1000) * R.rate;
    R.at = Math.min(t, R.t1);
    pose(R.at);
    if (t > R.t1) { setTimeout(() => R && R.ended && finish('end'), 0); R.ended = true; }   // one more frame reads the last pose
    return true;
  }

  live.handlers.replay_input = (c) => {
    if (c.stop) return R ? finish('stopped') : { stopped: false };
    return start(c);
  };
  return { update, start, stop: () => (R ? finish('stopped') : null), get active() { return !!R; } };
}
