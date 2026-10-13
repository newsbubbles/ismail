// Stepping into a person (the user, 2026-10-12; STAGE_DESIGN_INTERACTION.md section 7): a third way to Follow,
// beside "walk with me" and "stay on the spot". At GO the user's view moves into the person: the eyes at their eyes,
// facing the way they face, so their body is exactly where the user's body is (nothing offset, turned or mirrored).
// Their head is hidden from the inside, and so are the user's own hand models, the beams, panels and labels (Marketing,
// 2026-10-13: no HUD or menus in the recorded view; the headset records what the eyes see). Both thumbs down, held,
// stops it (the performance's way out, perform.js), as does saying "stop the performance" or an agent. On stop the user
// is put back where they stood (after: 'stay' leaves them in the person's place).
// eyes: 'person' (their eye height; the floor may feel a little off: the user's pick, 2026-10-13) or 'floor' (the
// user's own height on the person's floor; the eyes may sit at their chin or forehead).
// Live: {"type": "embody", "person": ..., "when": "next"|"now"|"off", "eyes": ..., "after": ..., "countdown": 3}.
// Events: embody_armed, embody_start, embody_stop {person, why, take}, embody_skipped {person, why}.
import * as THREE from 'three';

const Y = new THREE.Vector3(0, 1, 0);
const HEAD_GONE = 1e-3;                   // the head bone's scale from the inside: its verts fold onto the neck
const TAKE_WAIT_MS = 2000;                // a take still recording at the Follow's stop gets this long to stop first

export function initEmbody(ed, live, xr, getActors, hands) {
  let armed = null;                       // {person, eyes, after}: the next Follow on that person steps in
  let inside = null;                      // {person, eyes, after, from: {p, q}, head, take}

  const how = (c) => ({ eyes: c.eyes === 'floor' ? 'floor' : 'person', after: c.after === 'stay' ? 'stay' : 'return' });
  // the next Follow on this person steps in (an agent's 'next', the person menu's Step in): taken once
  function claim(c) {
    if (c.embody) return how(c.embody === true ? {} : c.embody);
    if (armed && armed.person === c.person) { const a = armed; armed = null; return a; }
    return null;
  }

  // the view into the person, before their Follow reads the user's body at GO
  async function enter(person, h) {
    if (!ed.renderer.xr.isPresenting) { live.emit('embody_skipped', { person, why: 'not in VR' }); return false; }
    const rig = ed.rig, cam = ed.camera;
    const at = await getActors().eyesOf(person, cam.getWorldPosition(new THREE.Vector3()));
    const from = { p: rig.position.clone(), q: rig.quaternion.clone() };
    if (at.facing) {                                     // turn about the rig so the head looks the person's way
      const hy = new THREE.Euler().setFromQuaternion(cam.getWorldQuaternion(new THREE.Quaternion()), 'YXZ').y;
      rig.quaternion.premultiply(new THREE.Quaternion().setFromAxisAngle(Y, Math.atan2(-at.facing.x, -at.facing.z) - hy));
      rig.updateMatrixWorld(true);
    }
    const head = cam.getWorldPosition(new THREE.Vector3());
    rig.position.x += at.eye.x - head.x; rig.position.z += at.eye.z - head.z;
    if (h.eyes === 'person') rig.position.y += at.eye.y - head.y;
    else rig.position.y = at.ground;
    rig.updateMatrixWorld(true);
    inside = { person, ...h, from, head: null, take: null };
    ed.embodied = person;                                // xr.js: the spot is not saved in here
    clean(true);
    return true;
  }
  // after the Follow began: their head goes from the inside
  function entered(person) {
    if (!inside || inside.person !== person) return;
    const b = getActors().boneOf(person, 'head');
    if (b) { inside.head = { b, s: b.scale.clone() }; b.scale.setScalar(HEAD_GONE); }
    live.emit('embody_start', { person, eyes: inside.eyes, after: inside.after });
  }
  function exit(why) {
    if (!inside) return null;
    const was = inside;
    inside = null;
    ed.embodied = null;
    if (was.head) was.head.b.scale.copy(was.head.s);
    if (was.after === 'return' && ed.renderer.xr.isPresenting) {
      ed.rig.position.copy(was.from.p); ed.rig.quaternion.copy(was.from.q); ed.rig.updateMatrixWorld(true);
    }
    clean(false);
    live.emit('embody_stop', { person: was.person, why, take: was.take, after: was.after });
    return { person: was.person, stepped_out: true, take: was.take };
  }
  // the clean view: no hand models, beams, panels or labels (the GIZMO layer: xr.js), no HUD lines (voice.js)
  function clean(on) {
    ed.cleanView = on;
    for (const s of xr.ctls) s.hand.visible = !on;
  }

  // the Follow ended: out, once a take recording with it has stopped too (a jump in its last frames would play back)
  live.onEmit((type, d) => {
    if (!inside || !d) return;
    if (type === 'take_start' && d.name === inside.person) inside.take = d.take || null;
    else if (type === 'take_stop' && d.take) inside.take = d.take;
    else if (type === 'actor_stop' && d.live && d.person === inside.person) {
      const why = d.why || 'stopped', person = inside.person;
      if (!hands.rec.on) { setTimeout(() => exit(why), 0); return; }
      const t0 = performance.now();
      const wait = setInterval(() => {
        if (!inside || inside.person !== person) { clearInterval(wait); return; }
        if (!hands.rec.on || performance.now() - t0 > TAKE_WAIT_MS) { clearInterval(wait); exit(why); }
      }, 50);
    }
  });
  ed.renderer.xr.addEventListener('sessionend', () => { if (inside) exit('left VR'); });

  // the page command
  live.handlers.embody = async (c) => {
    const person = c.person, when = c.when || 'next';
    if (when === 'off') {
      const was = armed; armed = null;
      if (inside && (!person || inside.person === person)) { getActors().stop({ person: inside.person, why: 'stepped out' }); return { disarmed: !!was, stepping_out: true }; }
      return { disarmed: !!was, inside: null };
    }
    if (!person) throw new Error('person is needed');
    if (!getActors().canPlay(person)) throw new Error('no body for ' + person + ' (world.json actors)');
    if (when === 'next') {
      armed = { person, ...how(c) };
      live.emit('embody_armed', { ...armed });
      return { armed: true, ...armed, says: 'The next Follow or Take on them steps you inside.' };
    }
    if (when !== 'now') throw new Error("when: 'next', 'now' or 'off'");
    return live.handlers.actor_follow({ person, countdown: c.countdown == null ? 3 : c.countdown, embody: how(c) });
  };

  return { claim, enter, entered, exit, isInside: (p) => !!inside && (!p || inside.person === p), get inside() { return inside && inside.person; } };
}
