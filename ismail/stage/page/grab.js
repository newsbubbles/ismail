// Grab by touch, phase 1 (STAGE_DESIGN_INTERACTION.md section 7; the user, 2026-10-12). A hand takes a thing by
// holding it, not by a gesture: the palm or the finger bases on it, the hand nearly still, for hold_s (a ring fills).
// Fingertips alone stay the pinch for menus. A hand takes what it could carry: the outermost group up to HAND_M across
// (a pool ball under its table's group, never the table: the user, 2026-10-14). The hand then holds it until it opens
// past half of the grip it took, counted from at least a half-closed hand (taken with an open palm, opening flat lets
// go; the user, 2026-10-14: an open-handed take could never let go) (or pulls 6 cm off a fixed thing) for release_s (a ring empties; closing again keeps it). A release lets go
// completely (the user, 2026-10-13: a drop is often on purpose): a loose thing falls under gravity onto whatever is
// under it, carrying the hand's speed, so a toss flies. Grabs stay on during a performance (a touch, and a held count
// to let go, so a drum fill cannot drop a stick).
//   Contacts: every hand joint is a ball of its WebXR radius, smoothed (one-euro) and swept along its path from the
//   last frame (a fast hand does not pass through a thin stick); one starts within 1.0 cm of a mesh's box and ends past
//   2.5 cm; a tracking gap under 0.2 s keeps it. Boxes only (phase 1): enough for sticks and bottles.
//   Holding: a loose thing rides the wrist. Inside a person (embody.js) it is pinned to their hand bone instead (the
//   same pin as props.js, for this session), so the person keeps holding it when the take plays; the grab and the drop
//   go into the take (hands.mark) and play back with it, the drop at the moment the hand began to open. A fixed thing
//   (the bar, a rail) keeps the hand model where it took hold.
//   Never saved: like a hand anchor, the edits file keeps the thing where it stood before the first grab.
//   World: world.json grab {on, hold_s, release_s, regrab_s, objects: 'unlocked' | [names, globs], rules: {object: {grab, fixed}}}.
//   live: grab_mode {on?, hold_s?, release_s?, regrab_s?, objects?} (off lets go of everything held), grab_rule {object, grab, fixed}
//   events: grab_counting, grabbed {object, hand, joint, contacts, fixed, pinned}, grab_releasing, grab_released,
//           grab_landed {object, fell_cm, onto}
import * as THREE from 'three';
import { GIZMO } from './editor.js';
import { JOINTS } from './hands.js';
import { world } from './world.js';

const START_M = 0.010, END_M = 0.025;     // a contact starts within 1.0 cm of the surface, ends past 2.5 cm
const LOSE_S = 0.4;                       // a grab count survives a break in the touch or the stillness this short
const GAP_S = 0.2;                        // a tracking gap shorter than this keeps the contacts and the counts
const STILL_MPS = 0.25;                   // the wrist slower than this is "nearly still"
const OPEN_FRAC = 0.5, GRIP_FRAC = 0.85;  // open past half the locked grip lets go; above 85% the hand still grips
const GRIP_MIN = 120;                     // the locked grip is at least this (degrees of bend, hands.js): half of it is 60, "extended"
const HAND_M = 1.1;                       // a hand takes things up to this half-diagonal (2.2 m across: a mic stand, not a pool table)
const PULL_M = 0.06;                      // a fixed thing: the hand this far off where it took hold lets go
const SWEEP_M = 0.01;                     // the path from the last frame is tested every centimetre
const NEAR_M = 0.3;                       // only things whose box is this near the wrist are tested
const BOX_MS = 1000;                      // the list of things a hand may take is refreshed this often
const FLY_MAX_S = 4, V_MAX = 8, G = 9.81, THROW_S = 0.4;
// the joints that start a grab: the wrist, the metacarpals and the finger bases (not the tips: those pinch)
const BASE = new Set(JOINTS.filter((n) => n === 'wrist' || n.endsWith('metacarpal') || n.endsWith('phalanx-proximal')));
const ORDER = Object.fromEntries(JOINTS.map((n) => [n, n === 'wrist' ? 0 : n.endsWith('metacarpal') ? 1 : n.endsWith('proximal') ? 2
  : n.endsWith('intermediate') ? 3 : n.endsWith('distal') ? 4 : 5]));
// regrab_s: a thing let go is not taken again for this long (the user's hand, set down on it, took it straight back)
const DEFAULTS = { on: false, hold_s: 1.5, release_s: 1.0, regrab_s: 0.5, objects: 'unlocked', rules: {} };

// the one-euro filter (Casiez et al. 2012): little smoothing when slow (no jitter), little lag when fast
class OneEuro {
  constructor(minCut = 2.0, beta = 1.0, dCut = 1.0) { Object.assign(this, { minCut, beta, dCut, x: null, dx: new THREE.Vector3(), t: 0 }); }
  static a(cut, dt) { const tau = 1 / (2 * Math.PI * cut); return 1 / (1 + tau / dt); }
  filter(v, t) {
    if (!this.x || t - this.t > GAP_S || t <= this.t) { this.x = v.clone(); this.dx.set(0, 0, 0); this.t = t; return this.x; }
    const dt = t - this.t;
    this.t = t;
    const d = v.clone().sub(this.x).divideScalar(dt);
    this.dx.lerp(d, OneEuro.a(this.dCut, dt));
    this.x.lerp(v, OneEuro.a(this.minCut + this.beta * this.dx.length(), dt));
    return this.x;
  }
}

const globRe = (g) => new RegExp('^' + g.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.') + '$');

export function initGrab(ed, live, xr, hands, getActors, props) {
  const cfg = () => ({ ...DEFAULTS, ...(world().grab || {}), rules: { ...((world().grab || {}).rules || {}) } });
  const ruleOf = (name) => cfg().rules[name] || null;
  const isLocked = (it) => !!(it && it.path && it.path.some((x) => x.man && x.man.locked));
  const isPerson = (it) => !!(world().actors || {})[it.name];
  function takeable(it) {
    const c = cfg(), r = ruleOf(it.name);
    if (!c.on || !it || it.light || isPerson(it)) return false;
    if (r && r.grab === false) return false;
    if (r && r.grab) return true;
    if (it.big) return false;
    if (Array.isArray(c.objects)) return c.objects.some((g) => g === it.name || (/[*?]/.test(g) && globRe(g).test(it.name)));
    return !isLocked(it);
  }
  const fixedOf = (it) => !!(ruleOf(it.name) || {}).fixed;
  // small enough for a hand, unless a rule allows it (grab: true takes a big thing too)
  const carriable = (it) => (ruleOf(it.name) || {}).grab === true || !it.box || it.box.isEmpty()
    || it.box.getSize(new THREE.Vector3()).length() / 2 <= HAND_M;

  // ---- the things a hand may take, whole (the mic stand, not its inner mesh), with their meshes' boxes
  let things = [], thingsAt = 0;
  function refresh(now, force) {
    if (!force && now - thingsAt < BOX_MS) return;
    thingsAt = now;
    const by = new Map();
    if (!ed.root || !cfg().on) { things = []; return; }
    ed.root.updateMatrixWorld(true);
    ed.root.traverse((o) => {
      if (!o.isMesh || o.isSkinnedMesh || !o.geometry || !ed.pickable(o)) return;
      const leaf = ed.itemOf(o);
      if (!leaf) return;
      const path = leaf.path ? leaf.path.filter((y) => !y.big) : [leaf];
      const it = path.find((y) => takeable(y) && carriable(y));        // outermost first: the mic stand, not its pole
      if (!it) return;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      if (!by.has(it)) by.set(it, { it, meshes: [] });
      by.get(it).meshes.push(o);
    });
    things = [...by.values()];
  }
  ed.addEventListener('revealed', () => { thingsAt = 0; });

  // the distance from a point to a mesh's box (0 inside): the point in the mesh's frame, clamped to its box, back out
  const tl = new THREE.Vector3(), tq = new THREE.Vector3(), inv = new THREE.Matrix4();
  function boxDist(p, mesh) {
    inv.copy(mesh.matrixWorld).invert();
    tl.copy(p).applyMatrix4(inv);
    const b = mesh.geometry.boundingBox;
    tq.copy(tl).clamp(b.min, b.max);
    if (tq.equals(tl)) return 0;
    return tq.applyMatrix4(mesh.matrixWorld).distanceTo(p);
  }
  const wb = new THREE.Box3();
  function near(thing, w) {
    wb.makeEmpty();
    for (const m of thing.meshes) wb.expandByObject(m);
    return wb.distanceToPoint(w) < NEAR_M;
  }

  // ---- per hand
  const mk = () => ({ f: {}, prev: {}, seen: 0, hist: [], speed: 0, contacts: new Map(), count: null, held: null, rel: null });
  const H = { left: mk(), right: mk() };
  const ctlOf = (side) => xr.ctls.find((s) => s.src && s.src.handedness === side && s.src.hand && s.hand.joints && s.hand.joints.wrist);
  const curlOf = (side) => { const f = hands.state[side] && hands.state[side].f; return f ? (f.curl.index + f.curl.middle + f.curl.ring + f.curl.pinky) / 4 : null; };

  // the joints this frame, smoothed, with where each was last frame
  function joints(side, s, t) {
    const h = H[side], out = [];
    for (const n of JOINTS) {
      const j = s.hand.joints[n];
      if (!j || j.visible === false) continue;
      const raw = j.getWorldPosition(new THREE.Vector3());
      const fl = h.f[n] || (h.f[n] = new OneEuro());
      const p = fl.filter(raw, t).clone();
      const prev = h.prev[n] && t - h.seen < GAP_S ? h.prev[n] : null;
      out.push({ n, p, prev, r: j.jointRadius || 0.008 });
      h.prev[n] = p;
    }
    return out;
  }
  // how far each joint's ball came from a thing's surface along its path this frame (the nearest point of the path)
  function sweep(js, thing) {
    let best = Infinity, joint = null;
    const touching = [];
    for (const j of js) {
      let d = Infinity;
      const steps = j.prev ? Math.min(8, Math.ceil(j.prev.distanceTo(j.p) / SWEEP_M)) : 0;
      for (let i = 0; i <= steps; i++) {
        const q = steps ? j.prev.clone().lerp(j.p, i / steps) : j.p;
        for (const m of thing.meshes) d = Math.min(d, boxDist(q, m));
      }
      d -= j.r;
      if (d < END_M) touching.push({ n: j.n, d });
      if (d < best) { best = d; joint = j.n; }
    }
    return { best, joint, touching };
  }

  // ---- what the user sees: a ring at the palm, filling while a grab counts, emptying while a release counts
  const rings = {};
  for (const side of ['left', 'right']) {
    const mat = new THREE.MeshBasicMaterial({ color: 0xffd23f, transparent: true, opacity: 0.9, depthTest: false, side: THREE.DoubleSide, toneMapped: false });
    const mesh = new THREE.Mesh(new THREE.RingGeometry(0.03, 0.038, 48, 1, Math.PI / 2, 0.001), mat);
    mesh.renderOrder = 999; mesh.layers.set(GIZMO); mesh.visible = false; mesh.userData.gizmo = true; mesh.userData.k = -1;
    ed.scene.add(mesh);
    rings[side] = mesh;
  }
  function ring(side, at, k, color) {
    const r = rings[side];
    if (k == null || !at) { r.visible = false; return; }
    if (Math.abs(k - r.userData.k) > 0.01) {
      r.geometry.dispose();
      r.geometry = new THREE.RingGeometry(0.03, 0.038, 48, 1, Math.PI / 2, -Math.max(0.001, k) * Math.PI * 2);
      r.userData.k = k;
    }
    r.material.color.set(color);
    const cam = ed.camera.getWorldPosition(new THREE.Vector3());
    r.position.copy(at).add(cam.clone().sub(at).setLength(0.05));
    r.lookAt(cam);
    r.visible = true;
  }

  // ---- holding
  const sc = new THREE.Vector3();
  const wristM = (s) => { const j = s.hand.joints.wrist; j.updateMatrixWorld(true); return j.matrixWorld.clone(); };
  const handModel = (s) => s.hand.children.find((c) => 'motionController' in c) || null;
  const boneFor = (side) => (side === 'left' ? 'hand_l' : 'hand_r');
  function home(it) {
    const u = it.obj.userData;
    if (!u.grabHome && !u.anchorHome && !u.propHome) u.grabHome = { changed: ed.changed(it), t: ed.blenderTransform(it) };
  }
  const heldBy = (it) => ['left', 'right'].find((sd) => H[sd].held && H[sd].held.it === it) || null;

  function grab(side, s, thing, joint, contacts, now) {
    const h = H[side], it = thing.it, o = it.obj;
    flights.delete(it.name);
    home(it);
    let from = null;
    const had = props.pins.get(it.name);
    if (had) { from = had.person; props.letGo(it.name); }   // taken from a person who held it
    o.updateMatrixWorld(true);
    const fromM = o.matrixWorld.clone();
    const fixed = fixedOf(it), inside = ed.embodied || null;
    let pinned = null;
    if (!fixed && inside) {                                  // inside a person: their hand holds it, now and in the take
      const bone = boneFor(side), b = getActors().boneOf(inside, bone);
      if (b) {
        const p = props.hold(it.name, inside, bone);
        if (p) {
          pinned = { person: inside, bone };
          hands.mark({ grab: it.name, person: inside, bone, hand: side, rel: p.rel.elements.map((x) => +x.toFixed(6)),
            from: fromM.elements.map((x) => +x.toFixed(6)), at: now });
        }
      }
    }
    h.held = { it, fixed, pinned, joint, t0: now, grip: Math.max(curlOf(side) || 0, GRIP_MIN), gripAt: now,
      offset: wristM(s).invert().multiply(o.matrixWorld), holdW: h.prev.wrist ? h.prev.wrist.clone() : null, nearAt: now };
    h.count = null;
    live.emit('grabbed', { object: it.name, hand: side, joint, contacts, fixed, pinned: pinned ? `${pinned.person} ${pinned.bone}` : null,
      ...(from ? { from_person: from } : {}) });
  }
  function follow(side, s) {
    const g = H[side].held;
    if (g.fixed || g.pinned) return;
    const o = g.it.obj, m = wristM(s).multiply(g.offset);
    if (o.parent) { o.parent.updateMatrixWorld(true); m.premultiply(new THREE.Matrix4().copy(o.parent.matrixWorld).invert()); }
    m.decompose(o.position, o.quaternion, sc);
    o.updateMatrixWorld(true);
    ed.emit('change');
  }
  // a fixed thing: the hand model stays where it took hold (the tracked hand moves on; 6 cm off lets go)
  function glue(side, s) {
    const g = H[side].held, model = handModel(s);
    if (!model) return;
    if (!g || !g.fixed || !g.holdW) { model.position.set(0, 0, 0); return; }
    const at = s.hand.worldToLocal(g.holdW.clone());
    model.position.copy(at.sub(s.hand.joints.wrist.position));
  }
  // the wrist's speed around a moment, from the last second of the hand's path
  function speedAt(side, t) {
    const hist = H[side].hist;
    const a = hist.filter((x) => x.t >= t - 0.05 && x.t <= t + 0.15);
    if (a.length < 2) return new THREE.Vector3();
    const v = a[a.length - 1].p.clone().sub(a[0].p).divideScalar(Math.max(1e-3, a[a.length - 1].t - a[0].t));
    return v.length() > V_MAX ? v.setLength(V_MAX) : v;
  }
  // the fastest the wrist moved in the last moments before a let-go (0.1 s steps over THROW_S): a throw is the swing
  // before the fingers open. Measured at "began to open" alone, a thing held by a resting open palm (never closed, so
  // "began" is the grab itself) let go of a swung throw with 0.01 m/s (Nate, 10-25: "it just drops").
  function swingBefore(side, t) {
    const hist = H[side].hist.filter((x) => x.t >= t - THROW_S && x.t <= t);
    let best = new THREE.Vector3();
    for (let i = 0, j = 0; i < hist.length; i++) {
      while (hist[i].t - hist[j].t > 0.1) j++;
      if (i === j || hist[i].t - hist[j].t < 0.04) continue;
      const v = hist[i].p.clone().sub(hist[j].p).divideScalar(hist[i].t - hist[j].t);
      if (v.lengthSq() > best.lengthSq()) best = v;
    }
    return best.length() > V_MAX ? best.setLength(V_MAX) : best;
  }
  const letGoAt = new Map();                                 // thing name -> when a hand last let go of it (regrab_s)
  function release(side, why, began, now) {
    const h = H[side], g = h.held;
    if (!g) return null;
    h.held = null; h.rel = null;
    letGoAt.set(g.it.name, now);
    const s = ctlOf(side);
    if (s) { const model = handModel(s); if (model) model.position.set(0, 0, 0); }
    const it = g.it, v0 = speedAt(side, began / 1000), v1 = swingBefore(side, now / 1000);
    const v = v1.lengthSq() > v0.lengthSq() ? v1 : v0;
    let rel = null;
    if (g.pinned) { const p = props.letGo(it.name); rel = p ? p.rel : null; }
    if (g.pinned && rel) hands.mark({ drop: it.name, person: g.pinned.person, bone: g.pinned.bone, hand: side,
      rel: rel.elements.map((x) => +x.toFixed(6)), v: v.toArray().map((x) => +x.toFixed(3)), at: began });
    if (!g.fixed) fly(it, v);
    thingsAt = 0;
    const out = { object: it.name, hand: side, why, held_s: +((now - g.t0) / 1000).toFixed(1), opened_s_before: +((now - began) / 1000).toFixed(2),
      speed_mps: +v.length().toFixed(2), fixed: g.fixed };
    live.emit('grab_released', out);
    return out;
  }

  // ---- falling: under gravity from where it was let go, with the hand's speed, onto whatever is under it
  const flights = new Map();                                 // name -> { it, v, t, y0, solid }
  function solids(it) {
    const own = new Set();
    it.obj.traverse((x) => own.add(x));
    const out = new Set();
    for (const top of [ed.scene, ed.root]) top && top.traverse((x) => {   // the room's root too, while it is still being revealed
      if (x.isMesh && !x.isSkinnedMesh && x.visible && !own.has(x) && !x.userData.gizmo && x.layers.isEnabled(0)
        && !(x.material && x.material.transparent && x.material.opacity < 0.5)) out.add(x);
    });
    return [...out];
  }
  function fly(it, v) {
    it.obj.updateMatrixWorld(true);
    flights.set(it.name, { it, v: v.clone(), t: performance.now(), y0: new THREE.Box3().setFromObject(it.obj).min.y, solid: solids(it) });
  }
  const rc = new THREE.Raycaster(), DOWN = new THREE.Vector3(0, -1, 0), fb = new THREE.Box3();
  // the highest surface under the footprint (centre and four corners), below fromY: the thing's bottom before this
  // step, so a fast fall (20 cm a frame) cannot pass through a table top between two frames
  function surfaceUnder(f, box, fromY) {
    let top = null, onto = null;
    const c = box.getCenter(new THREE.Vector3()), hx = (box.max.x - box.min.x) * 0.3, hz = (box.max.z - box.min.z) * 0.3;
    for (const [dx, dz] of [[0, 0], [hx, hz], [-hx, hz], [hx, -hz], [-hx, -hz]]) {
      rc.set(new THREE.Vector3(c.x + dx, fromY + 0.02, c.z + dz), DOWN); rc.far = 80;
      const hit = rc.intersectObjects(f.solid, false)[0];
      if (hit && (top === null || hit.point.y > top)) { top = hit.point.y; onto = hit.object; }
    }
    return { top, onto };
  }
  function flightsUpdate(dt) {
    for (const [name, f] of flights) {
      const o = f.it.obj, fromY = fb.setFromObject(o).min.y;
      f.v.y -= G * dt;
      const w = o.getWorldPosition(new THREE.Vector3()).addScaledVector(f.v, dt);
      if (o.parent) o.parent.worldToLocal(w);
      o.position.copy(w);
      o.updateMatrixWorld(true);
      fb.setFromObject(o);
      const { top, onto } = surfaceUnder(f, fb, fromY);
      const long = (performance.now() - f.t) / 1000 > FLY_MAX_S;
      if ((top !== null && fb.min.y <= top && f.v.y <= 0) || long) {
        if (top !== null) {                                  // set down on it
          const up = top - fb.min.y, wp = o.getWorldPosition(new THREE.Vector3());
          wp.y += up;
          if (o.parent) o.parent.worldToLocal(wp);
          o.position.copy(wp);
          o.updateMatrixWorld(true);
        }
        flights.delete(name);
        const it2 = onto ? ed.itemOf(onto) : null;
        live.emit('grab_landed', { object: name, fell_cm: Math.round((f.y0 - (top ?? fb.min.y)) * 100), onto: it2 ? it2.name : top === null ? null : 'the floor' });
      }
      ed.emit('change');
    }
  }

  // ---- a take made inside a person plays its grabs and drops back on that person
  function replay() {
    const actors = getActors();
    for (const st of actors.playing.values()) {
      if (!st.frames) continue;
      if (!st.grabEv) st.grabEv = st.frames.flatMap((f) => f.ev || []).filter((e) => e.grab || e.drop).sort((a, b) => a.t - b.t);
      if (!st.grabEv.length) continue;
      const t = st.frames[st.i].t;
      if (st.grabT == null || t < st.grabT - 1e-3) {         // the start, or the take looped: as it was when it began
        const first = new Map();
        for (const e of st.grabEv) { const n = e.grab || e.drop; if (!first.has(n)) first.set(n, e); }
        for (const [n, e] of first) {
          const it = ed.byName.get(n);
          if (!it || heldBy(it)) continue;
          flights.delete(n);
          home(it);
          if (e.drop) props.hold(n, st.person, e.bone, e.rel);
          else {
            props.letGo(n);
            const m = new THREE.Matrix4().fromArray(e.from), o = it.obj;
            if (o.parent) { o.parent.updateMatrixWorld(true); m.premultiply(new THREE.Matrix4().copy(o.parent.matrixWorld).invert()); }
            m.decompose(o.position, o.quaternion, sc);
            o.updateMatrixWorld(true);
          }
        }
        st.grabT = st.frames[0].t - 1e-3;
      }
      for (const e of st.grabEv) {
        if (e.t <= st.grabT || e.t > t) continue;
        const n = e.grab || e.drop, it = ed.byName.get(n);
        if (!it || heldBy(it)) continue;
        if (e.grab) { flights.delete(n); props.hold(n, st.person, e.bone, e.rel); }
        else { props.letGo(n); fly(it, new THREE.Vector3().fromArray(e.v || [0, 0, 0])); }
      }
      st.grabT = t;
    }
  }
  let lastT = performance.now();
  ed.preRender.push(function grabWorld() {
    const now = performance.now(), dt = Math.min(0.05, (now - lastT) / 1000);
    lastT = now;
    if (getActors().playing.size) replay();
    if (flights.size) flightsUpdate(dt);
  });

  // ---- per frame in VR (main.js, after hands.js has read the gestures)
  function update() {
    const now = performance.now(), t = now / 1000, c = cfg();
    const anyHeld = H.left.held || H.right.held;
    if (!c.on && !anyHeld) { ring('left', null); ring('right', null); return; }
    refresh(now);
    for (const side of ['left', 'right']) {
      const h = H[side], s = ctlOf(side);
      if (!s) {                                              // tracking lost: a short gap keeps everything, a hold is kept
        if (t - h.seen > GAP_S) { h.contacts.clear(); h.count = null; h.rel = null; }
        ring(side, null);
        continue;
      }
      const js = joints(side, s, t);
      h.seen = t;
      const wrist = js.find((j) => j.n === 'wrist');
      if (!wrist) { ring(side, null); continue; }
      h.hist.push({ t, p: wrist.p.clone() });
      while (h.hist.length && h.hist[0].t < t - 3) h.hist.shift();   // 3 s: a release counts up to a second or two after the hand began to open
      const sp = h.hist.length > 3 ? h.hist[h.hist.length - 1].p.distanceTo(h.hist[h.hist.length - 4].p) / Math.max(1e-3, t - h.hist[h.hist.length - 4].t) : 0;
      h.speed = sp;
      const palm = js.filter((j) => ['wrist', 'middle-finger-metacarpal', 'middle-finger-phalanx-proximal'].includes(j.n))
        .reduce((a, j) => a.add(j.p), new THREE.Vector3()).divideScalar(3);

      if (h.held) {                                          // holding: follow, and count a release
        follow(side, s);
        glue(side, s);
        const g = h.held, curl = curlOf(side);
        if (curl != null && curl >= g.grip * GRIP_FRAC) g.gripAt = now;
        const pulled = g.fixed && g.holdW && wrist.p.distanceTo(g.holdW) > PULL_M;
        if (g.fixed && g.holdW && wrist.p.distanceTo(g.holdW) < 0.02) g.nearAt = now;
        const open = curl != null && curl < g.grip * OPEN_FRAC;
        if (open || pulled) {
          if (!h.rel) {
            h.rel = { t0: now, why: open ? 'opened' : 'pulled', began: open ? g.gripAt : g.nearAt };
            live.emit('grab_releasing', { object: g.it.name, hand: side, why: h.rel.why, release_s: c.release_s });
          }
          const k = (now - h.rel.t0) / (c.release_s * 1000);
          if (k >= 1) { release(side, h.rel.why, h.rel.began, now); ring(side, null); }
          else ring(side, palm, 1 - k, 0xf97316);
        } else {
          if (h.rel) live.emit('grab_releasing', { object: g.it.name, hand: side, cancelled: true });
          h.rel = null;
          ring(side, null);
        }
        continue;
      }
      if (!c.on) { ring(side, null); continue; }

      // not holding: contacts with every thing near the wrist
      const touchingNow = new Map();
      for (const th of things) {
        if (heldBy(th.it) || flights.has(th.it.name) || !near(th, wrist.p)) continue;
        if (now - (letGoAt.get(th.it.name) ?? -Infinity) < (c.regrab_s ?? 0.5) * 1000) continue;   // just let go of
        const r = sweep(js, th);
        const was = h.contacts.get(th.it.name);
        const on = was ? r.best < END_M : r.best < START_M;
        if (on) touchingNow.set(th.it.name, { th, r, since: was ? was.since : now });
      }
      h.contacts = touchingNow;
      // a grab counts while a base joint touches and the hand is nearly still
      const resting = hands.state[side] && hands.state[side].resting;
      let cand = null;
      for (const x of touchingNow.values()) {
        const base = x.r.touching.filter((j) => BASE.has(j.n) && j.d < END_M);
        if (!base.length) continue;
        if (!cand || x.r.best < cand.x.r.best) cand = { x, joint: base.sort((a, b) => ORDER[a.n] - ORDER[b.n])[0].n };
      }
      if (!cand || resting || h.speed > STILL_MPS) {
        // a flicker (a joint off the box for a frame, a twitch over the speed line) pauses the count; only a
        // break longer than LOSE_S starts it over (the user, 2026-10-14: a bottle's count began again 12 times in 9 s)
        if (h.count && !h.count.lost) h.count.lost = now;
        if (!h.count || now - h.count.lost > LOSE_S * 1000) { h.count = null; ring(side, null); }
        continue;
      }
      const name = cand.x.th.it.name;
      if (h.count && h.count.lost) {                   // back on: the paused time does not count
        if (h.count.name === name) h.count.t0 += now - h.count.lost;
        h.count.lost = null;
      }
      if (!h.count || h.count.name !== name) {
        h.count = { name, t0: now, lost: null };
        live.emit('grab_counting', { object: name, hand: side, joint: cand.joint, hold_s: c.hold_s });
      }
      const k = (now - h.count.t0) / (c.hold_s * 1000);
      if (k >= 1) {
        grab(side, s, cand.x.th, cand.joint, cand.x.r.touching.sort((a, b) => ORDER[a.n] - ORDER[b.n]).map((j) => j.n), now);
        ring(side, null);
      } else ring(side, palm, k, 0xffd23f);
    }
  }
  function letGoAll(why) {
    const now = performance.now();
    return ['left', 'right'].map((sd) => (H[sd].held ? release(sd, why, now, now) : null)).filter(Boolean);
  }
  ed.renderer.xr.addEventListener('sessionend', () => { letGoAll('left VR'); ring('left', null); ring('right', null); });

  // ---- the page commands
  live.handlers.grab_mode = (cmd) => {
    const w = world(), g = { ...DEFAULTS, ...(w.grab || {}) };
    for (const k of ['on', 'hold_s', 'release_s', 'regrab_s', 'objects']) if (cmd[k] != null) g[k] = cmd[k];
    w.grab = g;
    thingsAt = 0;
    const dropped = g.on ? [] : letGoAll('grab off');
    refresh(performance.now(), true);
    return { ...g, takeable: things.map((x) => x.it.name).sort(), ...(dropped.length ? { let_go: dropped.map((x) => x.object) } : {}) };
  };
  live.handlers.grab_rule = (cmd) => {
    const w = world(), g = { ...DEFAULTS, ...(w.grab || {}) };
    if (cmd.clear) {
      const { [cmd.object]: _, ...rest } = g.rules || {};
      w.grab = { ...g, rules: rest };
      thingsAt = 0;
      return { object: cmd.object, cleared: true };
    }
    const it = ed.byName.get(cmd.object);
    if (!it) throw new Error('no object named ' + cmd.object);
    g.rules = { ...(g.rules || {}), [it.name]: { grab: cmd.grab !== false, fixed: !!cmd.fixed } };
    w.grab = g;
    thingsAt = 0;
    const sd = heldBy(it);
    if (sd && cmd.grab === false) release(sd, 'rule', performance.now(), performance.now());
    return { object: it.name, ...g.rules[it.name], on: g.on };
  };

  return { update, held: (side) => (H[side].held ? H[side].held.it : null), heldBy, letGoAll, takeable, things: () => things.map((x) => x.it.name),
    fly: (name, v = [0, 0, 0]) => { const it = ed.byName.get(name); if (it) fly(it, new THREE.Vector3().fromArray(v)); return !!it; } };
}
