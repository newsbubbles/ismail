// Hands that touch the world. Whatever a hand is on (reached into, or under its ray) gets a box and its name; reaching
// into an object and pinching or closing the fist holds it, and it follows the hand (position and turn) until the
// hand lets go. Events Claude reads: touch (a new target held a moment), grab, release (how far it moved and turned).
import * as THREE from 'three';
import { GIZMO } from './editor.js';
import { cyclePick } from './pickcycle.js';

const REACH = 0.04;                    // metres: the pinch point or palm this close to an object's box touches it
const PINCH_ON = 0.02, PINCH_OFF = 0.04;
const FIST_ON = 150, FIST_OFF = 100;   // mean bend of the four fingers, degrees
const BOX_MS = 1000;                   // object boxes are refreshed this often (the held one every frame)
const TOUCH_LOG_MS = 400;              // a new target held this long is logged

export function initTouch(ed, xrApi, hands, emit) {
  const { scene } = ed;

  // ---- the boxes of every mesh that can be picked (not floor, walls, or the groups holding them)
  let boxes = [], boxesAt = 0;
  function refreshBoxes(now) {
    if (now - boxesAt < BOX_MS && boxes.length) return;
    boxesAt = now;
    boxes = [];
    if (!ed.root) return;
    ed.root.updateMatrixWorld(true);
    ed.root.traverse((o) => {
      if (!o.isMesh || !ed.pickable(o)) return;
      const it = ed.itemOf(o);
      if (!it || it.big) return;
      const b = new THREE.Box3().setFromObject(o);
      if (b.isEmpty()) return;
      const sz = b.getSize(new THREE.Vector3());
      boxes.push({ o, it, b, vol: Math.max(sz.x, 0.01) * Math.max(sz.y, 0.01) * Math.max(sz.z, 0.01) });
    });
  }
  // the building's fixed parts (manifest `locked`, set in Blender as vr_lock) are never taken by a hand
  const isLocked = (it) => !!(it && it.path && it.path.some((x) => x.man && x.man.locked));
  // the smallest box the point is in (or within REACH of): the most specific thing the hand is on
  // everything the point is in, smallest box first, one entry per whole thing (pickcycle.js takes the next on a
  // second pinch at the same point)
  function nearAll(pt) {
    const out = [];
    const hits = boxes.map((x) => ({ x, d: x.b.distanceToPoint(pt) })).filter((h) => h.d < REACH).sort((a, b) => a.x.vol - b.x.vol);
    for (const { x, d } of hits) {
      // a hand takes the whole thing (the person, the stool, the mic stand), never a piece inside it: the desktop's
      // click-again-to-go-deeper made a second grab take the mic stand's inner mesh, which then jumped
      const path = x.it.path ? x.it.path.filter((y) => !y.big) : [];
      const item = path[0] || x.it;
      if (!out.some((n) => n.item === item)) out.push({ item, leaf: x.it, dist: d, locked: isLocked(item) });
    }
    return out;
  }
  const nearAt = (pt) => nearAll(pt)[0] || null;

  // ---- one hand's joints, read fresh (select events can come before this frame's update)
  const ctlOf = (side) => xrApi.ctls.find((s) => s.src && s.src.handedness === side && s.src.hand && s.hand.joints && s.hand.joints.wrist);
  const jp = (s, n) => { const j = s.hand.joints[n]; return j ? j.getWorldPosition(new THREE.Vector3()) : null; };
  function points(side) {
    const s = ctlOf(side);
    if (!s) return null;
    const th = jp(s, 'thumb-tip'), ix = jp(s, 'index-finger-tip'), w = jp(s, 'wrist'), mm = jp(s, 'middle-finger-metacarpal'), mp = jp(s, 'middle-finger-phalanx-proximal');
    if (!th || !ix || !w || !mm || !mp) return null;
    return { s, pinchPt: th.clone().add(ix).multiplyScalar(0.5), palm: w.clone().add(mm).add(mp).multiplyScalar(1 / 3), pinch: th.distanceTo(ix) };
  }
  function nearFor(side) {
    const p = points(side);
    if (!p) return null;
    refreshBoxes(performance.now());
    return nearAt(p.pinchPt) || nearAt(p.palm);
  }

  // ---- what the user sees: a box around the target and its name above it, one set per hand
  function label() {
    const cv = document.createElement('canvas'); cv.width = 512; cv.height = 64;
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true, toneMapped: false }));
    sp.renderOrder = 999; sp.layers.set(GIZMO); sp.visible = false; sp.userData = { cv, t, text: '' };
    scene.add(sp);
    return sp;
  }
  function setLabel(sp, text, bg) {
    if (sp.userData.text === text + bg) return;
    sp.userData.text = text + bg;
    const { cv, t } = sp.userData, g = cv.getContext('2d');
    g.clearRect(0, 0, cv.width, cv.height);
    // a long name shrinks to fit, then ends in an ellipsis: centred text wider than the canvas lost its start (the
    // user, 2026-10-10: Dell's tag read "L, drums")
    let px = 34;
    g.font = `bold ${px}px system-ui, sans-serif`;
    while (px > 20 && g.measureText(text).width > cv.width - 40) { px -= 2; g.font = `bold ${px}px system-ui, sans-serif`; }
    while (text.length > 4 && g.measureText(text).width > cv.width - 40) text = text.slice(0, -2) + '…';
    const w = Math.min(cv.width - 4, g.measureText(text).width + 36);
    g.fillStyle = bg; g.beginPath(); g.roundRect((cv.width - w) / 2, 2, w, cv.height - 4, 16); g.fill();
    g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.fillText(text, cv.width / 2, cv.height / 2 + 2);
    t.needsUpdate = true;
  }
  const COL = { near: 0xffd23f, ray: 0xffffff, held: 0x22c55e };
  const BG = { near: 'rgba(150,110,0,0.85)', ray: 'rgba(20,24,30,0.75)', held: 'rgba(21,128,61,0.9)' };
  const show = {};
  for (const side of ['left', 'right']) {
    const box = new THREE.Box3(), helper = new THREE.Box3Helper(box, COL.near);
    helper.material.toneMapped = false; helper.material.depthTest = false; helper.renderOrder = 998;
    helper.layers.set(GIZMO); helper.visible = false;
    scene.add(helper);
    show[side] = { box, helper, sp: label() };
  }
  const tmpV = new THREE.Vector3();
  function display(side, it, how, dist) {
    const d = show[side];
    if (!it) { d.helper.visible = d.sp.visible = false; return; }
    d.box.setFromObject(it.obj);
    if (d.box.isEmpty()) { d.helper.visible = d.sp.visible = false; return; }
    d.helper.material.color.set(COL[how]);
    d.helper.visible = d.sp.visible = true;
    const nm = ed.label(it);
    const text = how === 'held' ? 'holding ' + nm : isLocked(it) ? nm + '  (locked)'
      : !ed.canMove(it) ? nm + '  (pinch: menu)' : nm + (how === 'near' ? '  (pinch or fist to hold)' : '  (hold the pinch to move)');
    setLabel(d.sp, text, BG[how]);
    // the name sits on top of the box, sized to stay readable at any distance
    d.box.getCenter(d.sp.position); d.sp.position.y = d.box.max.y + 0.05;
    const far = Math.max(0.5, ed.camera.getWorldPosition(tmpV).distanceTo(d.sp.position));
    d.sp.scale.set(0.24 * far / 1.2, 0.03 * far / 1.2, 1);
  }

  // ---- holding
  const held = { left: null, right: null };
  const st = { left: { pinch: false, fist: false, tgt: null, tgtSince: 0, logged: null }, right: { pinch: false, fist: false, tgt: null, tgtSince: 0, logged: null } };
  const wristM = (s) => { const j = s.hand.joints.wrist; j.updateMatrixWorld(true); return j.matrixWorld; };
  const sc = new THREE.Vector3();
  function grab(side, n, how, p) {
    const it = n.item, o = it.obj;
    o.updateMatrixWorld(true);
    ed.select(it, 'xr');
    ed.beginEdit('xr-hand');
    const p0 = new THREE.Vector3(), q0 = new THREE.Quaternion();
    o.matrixWorld.decompose(p0, q0, sc);
    held[side] = { it, how, offset: wristM(p.s).clone().invert().multiply(o.matrixWorld), p0, q0, t0: performance.now() };
    const a = p0.toArray().map((x) => +x.toFixed(3));
    emit('grab', { hand: side, how, item: it.name, at: a });
    const gp = p.s.src.gamepad, h = gp && gp.hapticActuators && gp.hapticActuators[0];
    if (h && h.pulse) h.pulse(0.4, 25);
  }
  function release(side) {
    const g = held[side];
    if (!g) return;
    held[side] = null;
    const p1 = new THREE.Vector3(), q1 = new THREE.Quaternion();
    g.it.obj.updateMatrixWorld(true);
    g.it.obj.matrixWorld.decompose(p1, q1, sc);
    ed.endEdit();
    boxesAt = 0;                                          // it moved: refresh the boxes
    emit('release', { hand: side, how: g.how, item: g.it.name, moved_cm: Math.round(p1.distanceTo(g.p0) * 100),
      turned_deg: Math.round(THREE.MathUtils.radToDeg(q1.angleTo(g.q0))), seconds: +((performance.now() - g.t0) / 1000).toFixed(1),
      to: p1.toArray().map((x) => +x.toFixed(3)) });
  }
  function follow(side, p) {
    const g = held[side], o = g.it.obj;
    const m = new THREE.Matrix4().multiplyMatrices(wristM(p.s), g.offset);
    if (o.parent) m.premultiply(new THREE.Matrix4().copy(o.parent.matrixWorld).invert());
    m.decompose(o.position, o.quaternion, sc);           // keep the object's own scale
    ed.emit('change');
  }

  // the ray grab in xr.js steps aside when a hand is touching or holding something
  // ... and while the hand travels: the thumb coming down for the travel click is a pinch to the Quest, which grabbed
  // whatever the ray was on (the user moved the front door twice that way)
  const travelling = (side) => hands.aim.by === side || ['gun', 'point'].includes(hands.state[side].g) || ['gun', 'point'].includes(hands.state[side].cand);
  xrApi.setNear((side) => !!held[side] || travelling(side) || !!nearFor(side));
  xrApi.setLocked(isLocked);
  // and its grabs are logged here too, so every grab reads the same in the events
  const rayHeld = { left: null, right: null };

  // drop: hold a LEFT thumbs-down for half a second while something is selected and it falls onto what is under it
  // (editor.js drop). The right thumbs-down stays the panels' and questions' "no".
  const drop = { since: 0, done: false };
  function dropUpdate(now) {
    const L = hands.state.left;
    const on = !hands.performing && !!(L && L.f && L.g === 'thumbs_down' && ed.selected && ed.canMove(ed.selected) && !held.left && !held.right);
    if (!on) { drop.since = 0; drop.done = false; return; }
    if (!drop.since) drop.since = now;
    if (!drop.done && now - drop.since > 500) {
      drop.done = true;
      const name = ed.selected.name, top = ed.drop(ed.selected, 'xr-drop');
      emit('drop', { object: name, onto: top === null ? null : +top.toFixed(3) });
    }
  }
  function update() {
    const now = performance.now();
    refreshBoxes(now);
    dropUpdate(now);
    for (const side of ['left', 'right']) {
      const p = points(side), S = st[side];
      const f = hands.state[side] && hands.state[side].f;
      if (!p || !f) {
        if (held[side]) release(side);
        S.pinch = S.fist = false;
      } else {
        const curl = (f.curl.index + f.curl.middle + f.curl.ring + f.curl.pinky) / 4;
        const pinch = p.pinch < (S.pinch ? PINCH_OFF : PINCH_ON), fist = curl > (S.fist ? FIST_OFF : FIST_ON);
        const rose = (pinch && !S.pinch) ? 'pinch' : (fist && !S.fist) ? 'fist' : null;
        S.pinch = pinch; S.fist = fist;
        if (held[side]) {
          if (!(held[side].how === 'pinch' ? pinch : fist)) release(side);
          else follow(side, p);
        } else if (rose && !hands.state[side].resting && !hands.performing) {   // perform.js: no grabs while performing
          let pt = null, all = [];
          for (const q of rose === 'pinch' ? [p.pinchPt, p.palm] : [p.palm, p.pinchPt]) {
            all = nearAll(q);
            if (all.length) { pt = q; break; }
          }
          const free = all.filter((x) => !x.locked);
          const pick = pt && cyclePick(pt, free.map((x) => x.item), 'touch');
          const n = pick ? free.find((x) => x.item === pick) : all[0];
          if (n && !n.locked && ed.canMove(n.item)) grab(side, n, rose, p);
          else if (n && !n.locked && rose === 'pinch' && ed.selected !== n.item) ed.select(n.item, 'touch');   // its menu opens
        }
      }
      // what this hand is on: held > reached into > under the ray
      const s = xrApi.ctls.find((c) => c.src && c.src.handedness === side);
      let it = null, how = null, dist = 0;
      if (held[side]) { it = held[side].it; how = 'held'; }
      else if (s && s.grab) { it = s.grab.it; how = 'held'; }
      else {
        const n = p && f ? nearAt(p.pinchPt) || nearAt(p.palm) : null;
        if (n) { it = n.item; how = 'near'; dist = n.dist; }
        else if (s && s.hit && s.hit.item) { it = s.hit.item; how = 'ray'; dist = s.hit.dist; }
      }
      display(side, it, how, dist);
      if (s && (how === 'near' || how === 'held') && s.src && s.src.hand) { s.line.visible = false; s.dot.visible = false; }
      if (s && hands.aim.by === side) { s.line.visible = false; s.dot.visible = false; display(side, null); }   // one pointer: travelling
      // log a new target once it has been held a moment (not every flicker)
      const key = it ? it.name + '|' + how : null;
      if (key !== S.tgt) { S.tgt = key; S.tgtSince = now; }
      if (key && key !== S.logged && how !== 'held' && now - S.tgtSince >= TOUCH_LOG_MS) {
        S.logged = key;
        emit('touch', { hand: side, how, item: it.name, dist_cm: Math.round(dist * 100) });
      }
      if (!key) S.logged = null;
      // ray grabs (xr.js) logged the same way
      const rg = s && s.grab ? s.grab : null;
      if (rg && !rayHeld[side]) {
        const o = rg.it.obj, p0 = new THREE.Vector3(), q0 = new THREE.Quaternion();
        o.updateMatrixWorld(true); o.matrixWorld.decompose(p0, q0, sc);
        rayHeld[side] = { it: rg.it, p0, q0, t0: now };
        emit('grab', { hand: side, how: 'ray', item: rg.it.name, at: p0.toArray().map((x) => +x.toFixed(3)) });
      } else if (!rg && rayHeld[side]) {
        const g = rayHeld[side], p1 = new THREE.Vector3(), q1 = new THREE.Quaternion();
        rayHeld[side] = null;
        g.it.obj.updateMatrixWorld(true); g.it.obj.matrixWorld.decompose(p1, q1, sc);
        boxesAt = 0;
        emit('release', { hand: side, how: 'ray', item: g.it.name, moved_cm: Math.round(p1.distanceTo(g.p0) * 100),
          turned_deg: Math.round(THREE.MathUtils.radToDeg(q1.angleTo(g.q0))), seconds: +((now - g.t0) / 1000).toFixed(1),
          to: p1.toArray().map((x) => +x.toFixed(3)) });
      }
    }
  }
  ed.renderer.xr.addEventListener('sessionend', () => { for (const side of ['left', 'right']) { release(side); display(side, null); } });

  return { update, held, nearFor };
}
