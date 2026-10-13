// WebXR editing: controller rays (and hand pinch, which Quest reports as the same select events), grab with a kept
// offset, grab-the-world locomotion on empty space, thumbstick move / snap turn / rise, light intensity, an in-space
// palette panel, and a director's monitor.
import * as THREE from 'three';
import { VRButton } from 'three/addons/webxr/VRButton.js';
import { XRHandModelFactory } from 'three/addons/webxr/XRHandModelFactory.js';
import { GIZMO } from './editor.js';
import { cyclePick } from './pickcycle.js';

const PW = 1024, PH = 640, PANEL_W = 0.42;        // panel canvas px and width in metres
const HUES = 12;
const NEUTRALS = [[1, 1, 1], [1, 0.72, 0.42], [1, 0.85, 0.65], [0.7, 0.8, 1], [0.5, 0.5, 0.5], [0.12, 0.12, 0.12]];
// locomotion (ported from ComfyVR's pull locomotion and stick handling, tuned for a room instead of a big space)
const PULL_GAIN = 1.0;          // grab-the-world: 1 = the room sticks to the hand
const STICK_SPEED = 1.5;        // left stick, m/s
const RISE_SPEED = 1.0;         // right stick up/down when nothing uses it, m/s
const TAP_MOVE = 0.02, TAP_MS = 350;   // a trigger/pinch on empty space this short and still is a tap, not a pull

export function initXR(ed, desktop) {
  const { renderer, scene, camera, rig } = ed;
  const xr = renderer.xr;
  // three's WebXR eye cameras keep only layers 0-2 (left = mask & 0b011, right = mask & 0b101, every frame), so the GIZMO
  // layer (the beam, the travel arc and ring, boxes and names on targets, the panel, the update card) never reached the
  // headset; the user could not see what they pointed at. Put the layer back after three sets the eye cameras.
  const updateCamera = xr.updateCamera.bind(xr);
  // Inside a person (embody.js) the view is clean: the layer stays off (Marketing: no HUD or menus in the recorded view)
  xr.updateCamera = (cam) => { updateCamera(cam); for (const c of xr.getCamera().cameras) c.layers[ed.cleanView ? 'disable' : 'enable'](GIZMO); };
  // body-tracking: Quest Browser's WebXR body (83 joints, legs from Meta's generated legs) when the user has turned on
  // chrome://flags "WebXR Experiments"; without the flag it is simply not granted and nothing changes
  const btn = VRButton.createButton(renderer, { optionalFeatures: ['hand-tracking', 'body-tracking'] });
  document.body.appendChild(btn);

  const ray = new THREE.Raycaster();
  ray.layers.enableAll();
  const tmpM = new THREE.Matrix4(), tmpV = new THREE.Vector3(), tmpQ = new THREE.Quaternion();
  const Y = new THREE.Vector3(0, 1, 0);

  // ---- panel: one canvas, buttons are rectangles on it
  const cvs = document.createElement('canvas');
  cvs.width = PW; cvs.height = PH;
  const g2 = cvs.getContext('2d');
  const tex = new THREE.CanvasTexture(cvs);
  tex.colorSpace = THREE.SRGBColorSpace;
  const panel = new THREE.Group();
  const panelMesh = new THREE.Mesh(new THREE.PlaneGeometry(PANEL_W, PANEL_W * PH / PW),
    new THREE.MeshBasicMaterial({ map: tex, toneMapped: false, transparent: true, opacity: 0.96 }));
  panel.add(panelMesh);
  const monRT = new THREE.WebGLRenderTarget(768, 432);
  monRT.texture.colorSpace = THREE.SRGBColorSpace;
  const monitor = new THREE.Mesh(new THREE.PlaneGeometry(PANEL_W, PANEL_W * 9 / 16),
    new THREE.MeshBasicMaterial({ map: monRT.texture, toneMapped: false }));
  monitor.position.y = PANEL_W * PH / PW / 2 + PANEL_W * 9 / 32 + 0.01;
  panel.add(monitor);
  panel.traverse((o) => o.layers.set(GIZMO));
  panel.visible = false;
  scene.add(panel);

  const st = { monitorOn: true, hover: null, msg: '', placed: false, liteMats: [] };
  const buttons = [];
  function layout() {
    buttons.length = 0;
    const sw = 70, sh = 56, x0 = 32, y0 = 120, gap = 8;
    for (let row = 0; row < 3; row++) {
      for (let h = 0; h < HUES; h++) {
        const c = new THREE.Color().setHSL(h / HUES, [1, 0.75, 0.45][row], [0.5, 0.62, 0.4][row], THREE.SRGBColorSpace);
        buttons.push({ id: 'color', color: c, x: x0 + h * (sw + gap) + 30, y: y0 + row * (sh + gap), w: sw, h: sh });
      }
    }
    NEUTRALS.forEach((rgb, i) => buttons.push({ id: 'color', color: new THREE.Color().setRGB(...rgb, THREE.LinearSRGBColorSpace),
      x: x0 + 30 + i * (sw * 2 + gap * 2 + 3), y: y0 + 3 * (sh + gap), w: sw * 2 + gap, h: sh }));
    const by = y0 + 4 * (sh + gap) + 24, bw = 180, bh = 84;
    [['dimmer', 'Dimmer'], ['brighter', 'Brighter'], ['monitor', 'Monitor'], ['shadows', 'Shadows'], ['deselect', 'Deselect']]
      .forEach(([id, label], i) => buttons.push({ id, label, x: x0 + i * (bw + 12), y: by, w: bw, h: bh }));
    [['save', 'SAVE'], ['undo', 'Undo'], ['rec', 'REC']]
      .forEach(([id, label], i) => buttons.push({ id, label, x: x0 + i * (bw + 12) * 1.67, y: by + bh + 16, w: bw * 1.6, h: bh }));
  }
  layout();

  function drawPanel() {
    g2.fillStyle = '#15171c'; g2.fillRect(0, 0, PW, PH);
    g2.strokeStyle = '#3a3f4a'; g2.lineWidth = 4; g2.strokeRect(2, 2, PW - 4, PH - 4);
    const it = ed.selected;
    g2.fillStyle = '#f2f2f2'; g2.font = 'bold 40px system-ui, sans-serif';
    g2.fillText(it ? it.name : 'nothing selected', 32, 58);
    g2.font = '28px system-ui, sans-serif'; g2.fillStyle = '#9aa3b2';
    let sub = it ? (it.kind || it.type) : 'point and pull the trigger (or pinch) to pick';
    if (it && it.light) sub += `   ${ed.energy(it).toPrecision(4)} W`;
    if (it && it.type === 'MESH') sub += '   colour: ' + ed.materialsOf(it).join(', ');
    g2.fillText(sub, 32, 98);
    for (const b of buttons) {
      const hov = st.hover === b;
      if (b.id === 'color') {
        g2.fillStyle = '#' + b.color.getHexString();
        g2.fillRect(b.x, b.y, b.w, b.h);
        if (hov) { g2.strokeStyle = '#fff'; g2.lineWidth = 5; g2.strokeRect(b.x - 3, b.y - 3, b.w + 6, b.h + 6); }
        continue;
      }
      const on = (b.id === 'monitor' && st.monitorOn) || (b.id === 'shadows' && renderer.shadowMap.enabled);
      const recOn = b.id === 'rec' && st.recording && st.recording();
      g2.fillStyle = recOn ? '#b91c1c' : hov ? '#4a5568' : b.id === 'save' ? '#1f5f3a' : on ? '#2d4a6b' : '#272b33';
      g2.fillRect(b.x, b.y, b.w, b.h);
      g2.fillStyle = '#fff'; g2.font = 'bold 34px system-ui, sans-serif'; g2.textAlign = 'center';
      g2.fillText(b.label, b.x + b.w / 2, b.y + b.h / 2 + 12);
      g2.textAlign = 'left';
    }
    g2.fillStyle = '#cfd6e0'; g2.font = '26px system-ui, sans-serif';
    g2.fillText((st.msg || ed.status).slice(0, 70), 32, PH - 28);
    tex.needsUpdate = true;
  }
  let dirtyPanel = true;
  ['select', 'change', 'status'].forEach((t) => ed.addEventListener(t, () => { dirtyPanel = true; }));

  function press(b) {
    const it = ed.selected;
    if (b.id === 'color') {
      if (!it) return flash('select a light or an object first');
      ed.beginEdit();
      if (it.light) ed.setLightColor(it, b.color);
      else if (it.type !== 'CAMERA') ed.materialsOf(it).slice(0, 1).forEach((m) => ed.setMaterialColor(m, b.color));
      ed.endEdit();
    } else if (b.id === 'dimmer' || b.id === 'brighter') {
      if (!it || !it.light) return flash('select a light first');
      ed.beginEdit();
      ed.setEnergy(it, ed.energy(it) * (b.id === 'brighter' ? 1.25 : 0.8));
      ed.endEdit();
    } else if (b.id === 'monitor') st.monitorOn = !st.monitorOn;
    else if (b.id === 'shadows') {
      renderer.shadowMap.enabled = !renderer.shadowMap.enabled;
      scene.traverse((o) => { if (o.material) [o.material].flat().forEach((m) => { m.needsUpdate = true; }); });
    } else if (b.id === 'deselect') ed.select(null);
    else if (b.id === 'save') ed.save().catch(() => {});
    else if (b.id === 'undo') ed.undo();
    else if (b.id === 'rec' && st.onRec) st.onRec();
    dirtyPanel = true;
  }
  function flash(m) { st.msg = m; dirtyPanel = true; setTimeout(() => { st.msg = ''; dirtyPanel = true; }, 2500); }

  function placePanel() {
    const head = camera.getWorldPosition(new THREE.Vector3());
    const fwd = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    const left = new THREE.Vector3().crossVectors(Y, fwd).normalize();
    panel.position.copy(head).addScaledVector(fwd, 0.55).addScaledVector(left, 0.18).add(new THREE.Vector3(0, -0.22, 0));
    panel.lookAt(head.x, panel.position.y, head.z);
    panel.rotateX(-0.35);
    panel.visible = true;
    dirtyPanel = true;
  }

  // the hands draw after the panels and test against their depth: in front of a panel they show, behind it they do not
  function handOverPanels(model) {
    model.traverse((o) => {
      if (!o.isMesh) return;
      o.renderOrder = 996;
      for (const m of [].concat(o.material)) { m.transparent = true; m.needsUpdate = true; }   // drawn in the later pass
    });
  }

  // ---- controllers and hands
  const hands = new XRHandModelFactory();
  const ctls = [0, 1].map((i) => {
    const c = xr.getController(i);
    // a beam, not a 1 px line (the user could not see what the hand pointed at): 3 mm thick, 1 m long, scaled in z
    const line = new THREE.Mesh(new THREE.CylinderGeometry(0.0015, 0.0015, 1, 6, 1, true).rotateX(Math.PI / 2).translate(0, 0, -0.5),
      new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false, transparent: true, opacity: 0.55, depthTest: false, depthWrite: false }));
    line.scale.z = 3;
    line.renderOrder = 997;             // over the room and the panels, like its dot (the user, 2026-10-10: the beam vanished behind the speakers)
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.009, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffd23f, toneMapped: false, depthTest: false }));
    dot.renderOrder = 998;
    line.layers.set(GIZMO); dot.layers.set(GIZMO);
    c.add(line);
    scene.add(dot);
    const grip = xr.getControllerGrip(i);
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.03, 0.025, 0.1), new THREE.MeshStandardMaterial({ color: 0x333840, roughness: 0.6 }));
    body.layers.set(GIZMO);
    grip.add(body);
    const hand = xr.getHand(i);         // note: with hands tracked, three stops updating the grip space (it freezes)
    const model = hands.createHandModel(hand, 'spheres');
    hand.add(model);
    hand.addEventListener('connected', () => handOverPanels(model));   // after the factory's own listener built it
    rig.add(c, grip, hand);
    const s = { c, grip, hand, line, dot, body, src: null, grab: null, pull: null, pending: null, stickActive: false, turned: false, hit: null };
    c.addEventListener('connected', (e) => { s.src = e.data; body.visible = !e.data.hand; });
    c.addEventListener('disconnected', () => {
      if (s.grab && s.grab.started) ed.endEdit();
      s.src = null; s.grab = null; s.pull = null; s.pending = null; two = null;
    });
    c.addEventListener('selectstart', () => onPress(s, 'select'));
    c.addEventListener('squeezestart', () => onPress(s, 'squeeze'));
    c.addEventListener('selectend', () => onRelease(s, 'select'));
    c.addEventListener('squeezeend', () => onRelease(s, 'squeeze'));
    return s;
  });

  function castFrom(s) {
    s.c.updateMatrixWorld(true);
    tmpM.identity().extractRotation(s.c.matrixWorld);
    ray.ray.origin.setFromMatrixPosition(s.c.matrixWorld);
    ray.ray.direction.set(0, 0, -1).applyMatrix4(tmpM);
    const targets = [...(panel.visible ? [panelMesh] : []), ...extras.filter((m) => m.parent && m.visible), ...ed.pickRoots];
    // the first surface stops the beam: a wall or the floor ends it there and picks nothing (the user: the beams
    // "literally go through all the walls" and picked things they could not see)
    // (panels live on the GIZMO layer like every helper; they are hit on purpose, the other helpers never)
    const ok = (h) => h.object.isMesh && ed.pickable(h.object) && h.object !== monitor &&
      (h.object === panelMesh || h.object.userData.panelApi || ((!h.object.layers.isEnabled(GIZMO) || h.object.userData.pickProxy) && ed.itemOf(h.object)));
    const hits = ed.insideFirst(ray.intersectObjects(targets, true).filter(ok)), hit = hits[0];
    if (!hit) return null;
    if (hit.object.userData.panelApi) return { extra: hit.object, uv: hit.uv, point: hit.point, dist: hit.distance };
    if (hit.object !== panelMesh && ed.itemOf(hit.object).big) return { wall: true, point: hit.point, dist: hit.distance };
    if (hit.object === panelMesh) {
      const px = hit.uv.x * PW, py = (1 - hit.uv.y) * PH;
      return { panel: true, point: hit.point, dist: hit.distance,
        button: buttons.find((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) || null };
    }
    const whole = (o) => { const leaf = ed.itemOf(o), path = leaf && leaf.path ? leaf.path.filter((x) => !x.big) : []; return path[0] || leaf; };
    // everything under the beam up to the first wall, nearest first: a pinch again at the same point takes the next
    const items = [];
    for (const h of hits) {
      if (h.object === panelMesh || h.object.userData.panelApi) break;
      const it = whole(h.object);
      if (!it || it.big || (st.locked && st.locked(it))) break;      // a wall, the bar counter: the beam ends there
      if (!items.includes(it)) items.push(it);
    }
    return { item: whole(hit.object), items, point: hit.point, dist: hit.distance };   // the whole thing, never a piece of it
  }

  function pulse(s, k = 0.4, ms = 25) {
    const a = s.src && s.src.gamepad && s.src.gamepad.hapticActuators && s.src.gamepad.hapticActuators[0];
    if (a && a.pulse) a.pulse(k, ms);
  }

  // Press on an object = grab it. Press on the panel = its button. Press on empty space = grab the world: the rig moves
  // against the hand so the room stays stuck to it (both hands: move and turn the room about the midpoint). A quick
  // trigger or pinch tap on empty space keeps its old meaning: left brings the panel, right deselects.
  const resting = (s) => !!(s.src && s.src.hand && st.handState && st.handState[s.src.handedness] && st.handState[s.src.handedness].resting);
  function onPress(s, kind) {
    if (s.grab || s.pull) return;
    if (resting(s)) return;                                             // a limp hand at the side presses nothing
    const acting = !!(s.src && s.src.hand && st.performing && st.performing());   // perform.js: a hand only presses panels
    if (s.src && s.src.hand && st.near && st.near(s.src.handedness)) return;   // touch.js: the hand is on something
    if (s.src && s.src.hand && st.nearChecks.some((f) => f(s.src.handedness))) return;   // panels.js: the hand is at a panel
    const h = castFrom(s);
    if (h && h.panel) { if (h.button && kind === 'select') { press(h.button); pulse(s, 0.3, 15); } return; }
    if (acting && !(h && h.extra)) return;
    if (h && h.extra) {
      if (kind === 'select') {
        const api = h.extra.userData.panelApi;
        if (!api.press(h.uv, 'ray') && api.dragTo) s.panelDrag = { api, kind, dist: h.dist };   // the frame: carry it on the ray
        pulse(s, 0.3, 15);
      }
      return;
    }
    // the building (locked: walls, floor, ceiling) is not picked at all: a pinch on it is a pinch on empty space, so it
    // moves you (the user, 2026-10-03: "I want to select the furniture, but the walls... I don't care to move them")
    if (h && h.item && !(st.locked && st.locked(h.item))) {
      h.item = cyclePick(h.point, h.items.length ? h.items : [h.item], 'ray', h.dist) || h.item;   // pickcycle.js
      // a tracked hand grabs from afar only after holding the pinch on the same object (a quick pinch selects it):
      // quick pinches from a relaxed hand grabbed the front door from five metres and swung it a metre
      if (!ed.canMove(h.item)) { ed.select(h.item, 'xr'); return; }   // selecting is free; moving needs Move (actions.js)
      if (s.src && s.src.hand && kind === 'select') { s.pending = { kind, it: h.item, t0: performance.now() }; ed.select(h.item, 'xr'); return; }
      startGrab(s, kind, h.item);
      return;
    }
    s.pull = { kind, start: s.c.position.clone(), last: s.c.position.clone(), t0: performance.now() };
    two = null;
    pulse(s, 0.15, 12);
  }
  function startGrab(s, kind, it) {
    ed.select(it, 'xr');
    s.c.updateMatrixWorld(true);
    const o = it.obj;
    o.updateMatrixWorld(true);
    s.grab = { kind, it, offset: s.c.matrixWorld.clone().invert().multiply(o.matrixWorld),
      start: s.c.matrixWorld.clone(), started: false };
    pulse(s);
  }
  const HOLD_TO_GRAB_MS = 350;
  function onRelease(s, kind) {
    if (s.panelDrag && s.panelDrag.kind === kind) s.panelDrag = null;
    if (s.pending && s.pending.kind === kind) s.pending = null;          // a quick pinch: it only selected
    if (s.grab && s.grab.kind === kind) {
      if (s.grab.started) ed.endEdit();
      s.grab = null;
    }
    if (s.pull && s.pull.kind === kind) {
      const tap = kind === 'select' && s.pull.start.distanceTo(s.c.position) < TAP_MOVE && performance.now() - s.pull.t0 < TAP_MS;
      s.pull = null;
      two = null;
      if (tap) { if (s.src && s.src.handedness === 'left') { if (!s.src.hand) placePanel(); } else ed.select(null, 'xr'); }
    }
  }

  const sP = new THREE.Vector3(), sQ = new THREE.Quaternion(), cP = new THREE.Vector3(), cQ = new THREE.Quaternion(), sc = new THREE.Vector3();
  function updateGrab(s, dt, pushPull) {
    const g = s.grab, o = g.it.obj;
    if (!g.started) {        // dead zone: a click that barely moves must not count as an edit
      g.start.decompose(sP, sQ, sc);
      s.c.matrixWorld.decompose(cP, cQ, sc);
      if (sP.distanceTo(cP) < 0.01 && sQ.angleTo(cQ) < 0.035 && !pushPull) return;
      g.started = true;
      ed.beginEdit();
    }
    if (pushPull) g.offset.premultiply(tmpM.makeTranslation(0, 0, pushPull * dt * 1.5));
    const m = new THREE.Matrix4().multiplyMatrices(s.c.matrixWorld, g.offset);
    if (o.parent) m.premultiply(tmpM.copy(o.parent.matrixWorld).invert());
    m.decompose(o.position, o.quaternion, sc);   // keep the object's own scale
    ed.emit('change');
  }

  // ---- grab-the-world locomotion. Controller positions are in rig space (they are children of the rig), so moving
  // the rig never feeds back into them. One hand: the rig moves by -(hand delta) * PULL_GAIN, in 3D. Two hands: the
  // midpoint does the same and the yaw of the hand-to-hand line turns the room about the midpoint.
  let two = null;
  const mid = new THREE.Vector3(), dv = new THREE.Vector3(), wm = new THREE.Vector3();
  function updatePull() {
    rig.updateMatrix();
    const ps = ctls.filter((s) => s.pull && s.src);
    if (ps.length === 2) {
      const a = ps[0].c.position, b = ps[1].c.position;
      mid.addVectors(a, b).multiplyScalar(0.5);
      const ang = Math.atan2(b.x - a.x, b.z - a.z);
      if (two) {
        let da = ang - two.ang;
        da = Math.atan2(Math.sin(da), Math.cos(da));
        wm.copy(mid).applyMatrix4(rig.matrix);                          // the midpoint in world space
        rig.position.sub(wm).applyAxisAngle(Y, -da).add(wm);           // the room turns with the hands
        rig.quaternion.premultiply(tmpQ.setFromAxisAngle(Y, -da));
        rig.updateMatrix();
        dv.subVectors(two.mid, mid).applyQuaternion(rig.quaternion);
        rig.position.addScaledVector(dv, PULL_GAIN);
      }
      two = { mid: mid.clone(), ang };
    } else if (ps.length === 1) {
      const s = ps[0];
      dv.subVectors(s.pull.last, s.c.position).applyQuaternion(rig.quaternion);
      rig.position.addScaledVector(dv, PULL_GAIN);
    }
    for (const s of ps) s.pull.last.copy(s.c.position);
    rig.updateMatrix();
  }

  // xr-standard puts the thumbstick at axes 2,3, but not every runtime does: take the pair that carries signal
  function stick(gp) {
    const A = (i) => (Number.isFinite(gp.axes[i]) ? gp.axes[i] : 0);
    const hi = gp.axes.length >= 4 && Math.abs(A(2)) + Math.abs(A(3)) >= Math.abs(A(0)) + Math.abs(A(1));
    return hi ? [A(2), A(3)] : [A(0), A(1)];
  }

  // ---- per frame
  const DEAD = 0.15;
  // the desktop's move gizmo (the arrows) is not a VR handle: the user took its green arrow for the grab target
  const tcHelper = desktop.tc && desktop.tc.getHelper ? desktop.tc.getHelper() : null;
  xr.addEventListener('sessionend', () => { if (tcHelper) tcHelper.visible = !!desktop.tc.object; });
  function update(dt) {
    if (!xr.isPresenting) return;
    if (tcHelper) tcHelper.visible = false;
    // the colour palette no longer opens by itself on entering (the user, 10-03: "why does the color palette always
    // show up every time I walk in here?"), nor on a quick left pinch of a tracked hand (rolling a cigarette opened it)
    updatePull();
    let hover = null;
    for (const s of ctls) {
      if (!s.src || resting(s)) { s.line.visible = s.dot.visible = false; continue; }
      s.line.visible = true;
      const gp = s.src.gamepad && !s.src.hand ? s.src.gamepad : null;
      const ax = gp && gp.axes.length >= 2 ? stick(gp) : [0, 0];
      const right = s.src.handedness !== 'left';

      if (!right && gp && Math.hypot(ax[0], ax[1]) > DEAD) {          // left stick: smooth move where the head looks
        const f = camera.getWorldDirection(tmpV).setY(0).normalize();
        const r = new THREE.Vector3().crossVectors(f, Y);
        rig.position.addScaledVector(f, -ax[1] * dt * STICK_SPEED).addScaledVector(r, ax[0] * dt * STICK_SPEED);
      }
      let pushPull = 0;
      if (right && gp) {
        if (Math.abs(ax[0]) > 0.7 && !s.turned) {                        // right stick X: snap turn 30 degrees
          const a = -Math.sign(ax[0]) * Math.PI / 6, head = camera.getWorldPosition(new THREE.Vector3());
          rig.position.sub(head).applyAxisAngle(Y, a).add(head);
          rig.quaternion.premultiply(tmpQ.setFromAxisAngle(Y, a));
          s.turned = true;
          pulse(s, 0.2, 10);
        } else if (Math.abs(ax[0]) < 0.3) s.turned = false;
        const y = Math.abs(ax[1]) > DEAD && Math.abs(ax[1]) > Math.abs(ax[0]) ? ax[1] : 0;
        if (s.grab) pushPull = y;                                          // right stick Y while grabbing: push / pull
        else if (ed.selected && ed.selected.light) {                       // right stick Y on a light: intensity
          if (y) {
            if (!s.stickActive) { ed.beginEdit(); s.stickActive = true; }
            ed.setEnergy(ed.selected, ed.energy(ed.selected) * 2 ** (-y * dt * 2));
          } else if (s.stickActive) { ed.endEdit(); s.stickActive = false; }
        } else if (y && gp.buttons[1] && gp.buttons[1].pressed) rig.position.y -= y * dt * RISE_SPEED;   // grip + stick: up / down
        // (stick forward alone aims a teleport arc: hands.js)
      }
      if (s.pending) {                                                   // a hand holding a pinch on an object
        const ph = castFrom(s);
        if (!ph || ph.item !== s.pending.it) s.pending = null;           // the ray left it: no grab
        else if (performance.now() - s.pending.t0 >= HOLD_TO_GRAB_MS) { const p = s.pending; s.pending = null; startGrab(s, p.kind, p.it); }
      }
      if (s.panelDrag) {                                                // a panel carried on the ray, at the distance it was taken
        s.c.updateMatrixWorld(true);
        const o = new THREE.Vector3().setFromMatrixPosition(s.c.matrixWorld), d = new THREE.Vector3(0, 0, -1).transformDirection(s.c.matrixWorld);
        s.panelDrag.api.dragTo(o.addScaledVector(d, s.panelDrag.dist));
        s.line.scale.z = s.panelDrag.dist; s.dot.visible = false;
        continue;
      }
      if (s.grab) { updateGrab(s, dt, pushPull); s.dot.visible = false; s.line.scale.z = 1; continue; }
      if (s.pull) { s.dot.visible = false; s.line.scale.z = 0.05; continue; }

      const h = castFrom(s);
      s.hit = h;
      s.line.scale.z = h ? h.dist : 3;
      s.dot.visible = !!h;
      if (h) { s.dot.position.copy(h.point); s.dot.scale.setScalar(Math.max(1, h.dist * 1.5)); }   // readable far away
      s.line.material.color.set(h && (h.item || (h.panel && h.button)) ? 0xffd23f : 0xffffff);
      if (h && h.panel && h.button) hover = h.button;
      if (s.extraHover && (!h || h.extra !== s.extraHover)) { s.extraHover.userData.panelApi.hover(null); s.extraHover = null; }
      if (h && h.extra) { h.extra.userData.panelApi.hover(h.uv); s.extraHover = h.extra; s.line.material.color.set(0xffd23f); }
    }
    if (hover !== st.hover) { st.hover = hover; dirtyPanel = true; }
    if (dirtyPanel && panel.visible) { drawPanel(); dirtyPanel = false; }
    const cam = ed.selected && ed.selected.type === 'CAMERA' ? ed.selected.obj : null;
    monitor.visible = panel.visible && st.monitorOn && !!cam;
  }

  // the director's monitor: render the selected Blender camera into a texture before the headset frame
  ed.preRender.push(() => {
    if (!xr.isPresenting || !monitor.visible) return;
    const cam = ed.selected && ed.selected.obj;             // deselected since update(): nothing to show
    if (!cam) { monitor.visible = false; return; }
    const rt = renderer.getRenderTarget(), xrOn = xr.enabled, auto = renderer.shadowMap.autoUpdate;
    xr.enabled = false;
    renderer.shadowMap.autoUpdate = false;
    renderer.setRenderTarget(monRT);
    renderer.render(scene, cam);
    xr.enabled = xrOn;
    renderer.shadowMap.autoUpdate = auto;
    renderer.setRenderTarget(rt);
  });

  // ---- session start / end: start in front of the props, cheaper materials, restore the desktop view after
  let saved = null;
  if (/OculusBrowser|Quest/i.test(navigator.userAgent)) {   // fewer pixels per eye and full foveation on the Quest
    try { ed.renderer.xr.setFramebufferScaleFactor(0.85); ed.renderer.xr.setFoveation(1.0); } catch (_) { /* older three */ }
  }
  xr.addEventListener('sessionstart', () => {
    if (desktop.walk.on) desktop.setWalk(false, 'xr');
    saved = desktop.saveView();
    desktop.tc.getHelper().visible = false;
    desktop.lookThrough(null);
    if (ed.loaded) enterScene(true);
    else { rig.position.set(0, 0, 0); rig.quaternion.identity(); }   // in the construct until the scene is here
  });
  // the scene arrived while the user was already inside (construct.js): put them where they would have started
  ed.addEventListener('loaded', () => { if (xr.isPresenting) enterScene(false); });
  function enterScene(fromDesktop) {
    // stand where the desktop view was (or at the start camera), on the floor under it, facing the same way
    const startCam = ed.manifest.start && ed.byName.get(ed.manifest.start);
    const spots = fromDesktop ? [camera.getWorldPosition(new THREE.Vector3())] : [];
    if (startCam) spots.push(startCam.obj.getWorldPosition(new THREE.Vector3()));
    let spot = null, floorY = 0;
    for (const p of spots) { const f = desktop.floorBelow(p); if (f !== null) { spot = p; floorY = f; break; } }
    const yaw = new THREE.Euler().setFromQuaternion((fromDesktop || !startCam ? camera : startCam.obj).getWorldQuaternion(new THREE.Quaternion()), 'YXZ').y;
    if (spot) rig.position.set(spot.x, floorY, spot.z); else rig.position.set(0, 0, 1.1);
    rig.quaternion.setFromAxisAngle(Y, spot ? yaw : 0);
    // ...unless this headset was in this scene before: back where the user last stood (they asked not to walk in from
    // the overview every time)
    const last = spotLoad();
    if (last) { rig.position.set(last.x, last.y, last.z); rig.quaternion.setFromAxisAngle(Y, last.yaw); }
    st.placed = false;
    st.liteMats = [];
    scene.traverse((o) => {
      if (o.material) [o.material].flat().forEach((m) => {
        if (m.transmission > 0) { st.liteMats.push([m, m.transmission]); m.transmission = 0; }
      });
    });
    setShadowSize(512);
  }
  // where the user stands, per scene, kept in this browser (localStorage): saved every 2 s in VR and on leaving
  const spotKey = () => 'vr_spot_' + ed.sceneName;
  function spotLoad() { try { const j = JSON.parse(localStorage.getItem(spotKey()) || 'null'); return j && Number.isFinite(j.x) ? j : null; } catch (_) { return null; } }
  function spotSave() {
    try {
      const e = new THREE.Euler().setFromQuaternion(rig.quaternion, 'YXZ');
      localStorage.setItem(spotKey(), JSON.stringify({ x: rig.position.x, y: rig.position.y, z: rig.position.z, yaw: e.y }));
    } catch (_) { /* storage blocked: start as before */ }
  }
  setInterval(() => { if (xr.isPresenting && !ed.embodied) spotSave(); }, 2000);   // inside a person is not a spot
  ed.addEventListener('switching', () => { if (xr.isPresenting) spotSave(); });   // the old scene keeps its spot
  xr.addEventListener('sessionend', () => {
    if (!ed.embodied) spotSave();
    rig.position.set(0, 0, 0);
    rig.quaternion.identity();
    panel.visible = false;
    st.liteMats.forEach(([m, t]) => { m.transmission = t; });
    setShadowSize(1024);
    desktop.tc.getHelper().visible = true;
    if (saved) desktop.restoreView(saved);
    camera.aspect = innerHeight ? innerWidth / innerHeight : camera.aspect;
    camera.updateProjectionMatrix();
  });

  function setShadowSize(n) {
    for (const it of ed.items) {
      const l = it.light;
      if (!l || !l.castShadow) continue;
      l.shadow.mapSize.set(n, n);
      if (l.shadow.map) { l.shadow.map.dispose(); l.shadow.map = null; }
    }
  }

  // hands.js plugs in here: the REC button and its lit state
  function setRec(onRec, recording) { st.onRec = onRec; st.recording = recording; dirtyPanel = true; }
  function redraw() { dirtyPanel = true; }
  function setNear(fn) { st.near = fn; }
  function setLocked(fn) { st.locked = fn; }
  function setPerforming(f) { st.performing = f; }                       // perform.js: is the user performing
  function setHandState(H) { st.handState = H; }                       // hands.js state: which hand is resting     // touch.js: the building's fixed parts
  st.nearChecks = [];
  function addNearCheck(fn) { st.nearChecks.push(fn); }
  // panels.js: other surfaces the ray can point at and press (agent panels)
  const extras = [];
  function addTarget(m) { if (!extras.includes(m)) extras.push(m); }
  function removeTarget(m) { const i = extras.indexOf(m); if (i >= 0) extras.splice(i, 1); }
  // the colour panel by a uv on it (a fingertip poke from panels.js)
  function buttonAt(uv) { const px = uv.x * PW, py = (1 - uv.y) * PH; return buttons.find((b) => px >= b.x && px <= b.x + b.w && py >= b.y && py <= b.y + b.h) || null; }
  function pressUV(uv) { const b = buttonAt(uv); if (b) press(b); }
  function hoverUV(uv) { const b = uv ? buttonAt(uv) : null; if (b !== st.hover) { st.hover = b; dirtyPanel = true; } }       // touch.js: is this hand reaching into something (then it holds, not the ray)
  // scenes.js: after a scene switch, stand where this headset last stood in that scene (or at its start camera)
  const placeInScene = () => { if (xr.isPresenting) enterScene(false); };
  return { update, panel, panelMesh, placePanel, press, buttons, ctls, drawPanel, setRec, redraw, setNear, setLocked, setHandState, setPerforming, addTarget, removeTarget, pressUV, hoverUV, addNearCheck, placeInScene };
}
