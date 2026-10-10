// Props pinned to a person's bone (the user, 2026-10-10: sticks, a guitar, a bass that stay in a player's hands, the way
// a hand anchor holds one to the user's own hand). Saved with the scene (world.json props: object -> {person, bone,
// rel}, rel the object's matrix in the bone's frame, 16 numbers), so the prop rides whichever body the person shows:
// resting in a start pose, playing a take, following the user. With no body shown (the statue, an unloaded set) it
// stands where it was. Like a hand anchor, editor.computeEdits saves it where it stood before the pin
// (userData.propHome), and unpinning puts it back there.
//   live: prop_pin {object, person, bone} -> {object, person, bone, rel}   (measured from where it stands now)
//         prop_pin {object, clear: true}   -> {object, unpinned}
//   events: prop_pinned, prop_unpinned
import * as THREE from 'three';
import { world } from './world.js';

export function initProps(ed, live, actors) {
  const pins = new Map();                                    // object name -> { it, person, bone, rel, on }
  const m = new THREE.Matrix4(), inv = new THREE.Matrix4();

  function home(it) {
    const o = it.obj;
    if (!o.userData.propHome) {
      o.userData.propHome = { changed: ed.changed(it), t: ed.blenderTransform(it),
        pos: o.position.clone(), quat: o.quaternion.clone(), scale: o.scale.clone() };
    }
  }
  function goHome(it) {
    const o = it.obj, h = o.userData.propHome;
    if (h) { o.position.copy(h.pos); o.quaternion.copy(h.quat); o.scale.copy(h.scale); o.updateMatrixWorld(true); }
  }

  function set(name, spec) {
    const it = ed.byName.get(name);
    if (!it || !spec || !Array.isArray(spec.rel) || spec.rel.length !== 16) return null;
    home(it);
    const p = { it, person: spec.person, bone: spec.bone, rel: new THREE.Matrix4().fromArray(spec.rel), on: false };
    pins.set(name, p);
    return p;
  }

  function unpin(name) {
    const p = pins.get(name);
    if (!p) return false;
    goHome(p.it);
    delete p.it.obj.userData.propHome;
    pins.delete(name);
    return true;
  }

  // the scene's pins, at every staging (load, hot reload, scene switch)
  function fromWorld() {
    for (const n of [...pins.keys()]) unpin(n);
    for (const [name, spec] of Object.entries(world().props || {})) set(name, spec);
  }
  ed.addEventListener('revealed', fromWorld);

  function update() {
    for (const p of pins.values()) {
      const b = actors.boneOf(p.person, p.bone);
      if (!b) { if (p.on) { goHome(p.it); p.on = false; } continue; }
      b.updateWorldMatrix(true, false);
      const o = p.it.obj;
      m.multiplyMatrices(b.matrixWorld, p.rel);
      if (o.parent) { o.parent.updateWorldMatrix(true, false); m.premultiply(inv.copy(o.parent.matrixWorld).invert()); }
      m.decompose(o.position, o.quaternion, o.scale);
      o.updateMatrixWorld(true);
      p.on = true;
    }
  }
  ed.preRender.push(function props() { if (pins.size) update(); });   // after actors.js has posed the bodies

  live.handlers.prop_pin = (c) => {
    if (c.clear) {
      const was = unpin(c.object);
      if (world().props) { const { [c.object]: _, ...rest } = world().props; world().props = rest; }
      if (was) live.emit('prop_unpinned', { object: c.object });
      return { object: c.object, unpinned: was };
    }
    const it = ed.byName.get(c.object);
    if (!it) throw new Error('no object named ' + c.object);
    if (!ed.byName.get(c.person) && !(world().actors || {})[c.person]) throw new Error('no person named ' + c.person);
    const b = actors.boneOf(c.person, c.bone);
    if (!b) {
      const names = actors.bonesOf(c.person);
      throw new Error(names ? `${c.person} has no bone ${c.bone} (bones: ${names.join(', ')})`
        : `${c.person} shows no body now (a statue, or unloaded): give them a start pose or play a take, then pin`);
    }
    pins.delete(c.object);                                   // pinned before: measured again from where it stands now
    it.obj.updateWorldMatrix(true, false);
    b.updateWorldMatrix(true, false);
    const rel = new THREE.Matrix4().copy(b.matrixWorld).invert().multiply(it.obj.matrixWorld);
    const spec = { person: c.person, bone: c.bone, rel: rel.elements.map((x) => Math.round(x * 1e6) / 1e6) };
    set(c.object, spec);
    world().props = { ...(world().props || {}), [c.object]: spec };
    live.emit('prop_pinned', { object: c.object, person: c.person, bone: c.bone });
    return { object: c.object, ...spec };
  };

  return { pins, unpin, reload: fromWorld };
}
