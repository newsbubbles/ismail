// Desktop editing: orbit, click to select (groups first), TransformControls, the HTML panel, camera look-through and
// preview, walk mode, and smooth camera flights (used by F and by the live link).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

const $ = (id) => document.getElementById(id);
const EYE = 1.6;                 // walk eye height above the floor, metres
const MAX_MAT_INPUTS = 12;

export function initDesktop(ed) {
  const { renderer, camera, scene } = ed;
  const canvas = renderer.domElement;
  const orbit = new OrbitControls(camera, canvas);
  // the wheel flies the camera and the pivot together (below): zoom only shrank the orbit until the wheel stalled at
  // the pivot (the user: "I can't move forward with mousewheel... it zooms, messing up my orbit")
  orbit.enableZoom = false;
  const tc = new TransformControls(camera, canvas);
  tc.setSize(0.8);
  const tch = tc.getHelper();
  scene.add(tch);
  tc.addEventListener('dragging-changed', (e) => {
    orbit.enabled = !e.value && !walk.on;
    if (e.value) ed.beginEdit('gizmo'); else ed.endEdit();
  });
  tc.addEventListener('objectChange', () => ed.emit('change'));

  const view = { through: null, rect: null };

  // in look-through the mouse moves the Blender camera itself, one undo step per drag: a plain drag pans and tilts it
  // about its own centre (a camera operator's head, like walk mode; the user: "it orbits around some point instead of
  // the camera rotating around its center"); Alt+drag orbits it around what is in the middle of the view (pilot)
  const pilot = new OrbitControls(camera, canvas);
  pilot.enabled = false;
  pilot.enableZoom = false;       // the wheel dollies the camera itself (below), never just its orbit distance
  pilot.addEventListener('start', () => ed.beginEdit('camera-fly'));
  pilot.addEventListener('end', () => { ed.endEdit(); pilot.enabled = false; });
  pilot.addEventListener('change', () => ed.emit('change'));

  // ---- initial view: the manifest's start camera (its position and look, orbit target 2 m ahead); without one,
  // from the hero camera (or the first one) toward the middle of the props, never framing huge backdrops (> 10 m)
  const props = new THREE.Box3();
  const huge = (b) => { const s = b.getSize(new THREE.Vector3()); return Math.max(s.x, s.y, s.z) > 10; };
  const measureProps = () => {
    props.makeEmpty();
    ed.items.filter((it) => it.type === 'MESH' && !it.big && !it.box.isEmpty() && !huge(it.box)).forEach((it) => props.union(it.box));
  };
  function initialView() {
    measureProps();
    const start = ed.manifest.start && ed.byName.get(ed.manifest.start);
    if (start && start.type === 'CAMERA') {
      const o = start.obj;
      o.updateWorldMatrix(true, false);
      camera.position.copy(o.getWorldPosition(new THREE.Vector3()));
      orbit.target.copy(camera.position).addScaledVector(o.getWorldDirection(new THREE.Vector3()), 2);
    } else {
      if (ed.manifest.start) console.warn('manifest start is not a camera in this scene:', ed.manifest.start);
      const cam0 = ed.items.find((it) => it.name === 'hero') || ed.items.find((it) => it.type === 'CAMERA');
      orbit.target.copy(props.isEmpty() ? new THREE.Vector3() : props.getCenter(new THREE.Vector3()));
      if (cam0) camera.position.copy(cam0.obj.getWorldPosition(new THREE.Vector3())).lerp(orbit.target, -0.9).add(new THREE.Vector3(0, 0.5, 0));
      else camera.position.set(0, 1, 2);
    }
    orbit.update();
  }
  initialView();
  ed.addEventListener('loaded', () => { if (!ed.renderer.xr.isPresenting) initialView(); });

  // ---- modes: orbit, walk, look-through. 'mode' fires on every change.
  let lastMode = '';
  const mode = () => (view.through ? 'look-through' : walk.on ? 'walk' : 'orbit');
  function emitMode(via = 'user') {
    const m = mode(), key = m + (view.through ? ':' + view.through.name : '');
    if (key === lastMode) return;
    lastMode = key;
    ed.emit('mode', { mode: m, camera: view.through ? view.through.name : null, via });
    refresh();
  }
  const gizmoVisible = () => !view.through && !walk.on;

  // ---- picking
  const ray = new THREE.Raycaster();
  ray.layers.enableAll();
  const ndc = (x, y) => { const r = canvas.getBoundingClientRect(); return new THREE.Vector2(((x - r.left) / r.width) * 2 - 1, -((y - r.top) / r.height) * 2 + 1); };
  function pickAt(v2, deep, via) {
    ray.setFromCamera(v2, camera);
    const leaf = ed.pickLeaf(ray.intersectObjects(ed.pickRoots, true));
    ed.select(leaf ? ed.resolvePick(leaf.it, deep) : null, via);
  }
  let down = null;
  const camLook = { on: false, last: null };
  const Y_UP = new THREE.Vector3(0, 1, 0);
  canvas.addEventListener('pointerdown', (e) => {   // capture: decide before the pilot's own handler sees the press
    if (!view.through || flight || e.button !== 0) return;
    const c = view.through.obj;
    if (e.altKey) {
      const h = lookingAt(), p = c.getWorldPosition(new THREE.Vector3());
      pilot.target.copy(h ? h.point : p.addScaledVector(c.getWorldDirection(new THREE.Vector3()), 3));
      pilot.enabled = true;
      return;
    }
    pilot.enabled = false;
    camLook.on = true; camLook.last = [e.clientX, e.clientY];
    ed.beginEdit('camera-look');
  }, { capture: true });
  function camLookMove(e) {
    const c = view.through.obj;
    const dx = e.clientX - camLook.last[0], dy = e.clientY - camLook.last[1];
    camLook.last = [e.clientX, e.clientY];
    const qw = c.getWorldQuaternion(new THREE.Quaternion());
    const yaw = new THREE.Quaternion().setFromAxisAngle(Y_UP, -dx * 0.0025);
    const pitch = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -dy * 0.0025);
    const next = yaw.multiply(qw).multiply(pitch);
    const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(next);
    if (Math.abs(fwd.y) > 0.985) next.copy(new THREE.Quaternion().setFromAxisAngle(Y_UP, -dx * 0.0025).multiply(qw));   // no flip over the top
    if (c.parent) next.premultiply(c.parent.getWorldQuaternion(new THREE.Quaternion()).invert());
    c.quaternion.copy(next);
    c.updateMatrixWorld(true);
    ed.emit('change');
  }
  canvas.addEventListener('pointerdown', (e) => { down = [e.clientX, e.clientY]; if (walk.on) walkPointerDown(e); });
  canvas.addEventListener('pointermove', (e) => { if (walk.on) walkPointerMove(e); else if (camLook.on && view.through) camLookMove(e); });
  addEventListener('pointerup', () => { if (camLook.on) { camLook.on = false; ed.endEdit(); } });
  canvas.addEventListener('pointerup', (e) => {
    const click = down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) <= 4;
    down = null;
    if (walk.on) return walkPointerUp(e, click);
    if (!click || tc.dragging || tc.axis || view.through || flight) return;
    pickAt(ndc(e.clientX, e.clientY), e.altKey, e.altKey ? 'alt-click' : 'click');
  });

  // what is under the middle of the screen (any mesh, walls included)
  const lookRay = new THREE.Raycaster();
  function lookingAt() {
    const cam = view.through ? view.through.obj : camera;
    cam.updateMatrixWorld(true);
    lookRay.setFromCamera(new THREE.Vector2(0, 0), cam);
    const h = lookRay.intersectObject(ed.root, true).find((x) => x.object.isMesh && ed.pickable(x.object) && !x.object.userData.pickProxy && ed.itemOf(x.object));
    return h ? { it: ed.itemOf(h.object), distance: h.distance, point: h.point } : null;
  }

  // ---- panel
  // the outliner: top-level items by kind; the selected item's ancestors open to show their parts; a search
  // finds anything by name. One action per row: a click selects.
  const outl = $('outliner'), search = $('search');
  let outlKey = null;
  function row(it, indent, label) {
    const d = document.createElement('div');
    d.className = 'it' + (ed.selected === it ? ' sel' : '');
    d.style.paddingLeft = 6 + indent * 12 + 'px';
    d.textContent = label || it.name;
    if (it.children.length) {
      const n = document.createElement('span');
      n.className = 'n';
      n.textContent = `  ${it.children.length}`;
      d.appendChild(n);
    }
    d.title = it.path.map((x) => x.name).join(' > ');
    d.addEventListener('click', () => ed.select(it.name, 'list'));
    outl.appendChild(d);
    return d;
  }
  function renderOutliner(force) {
    const q = search.value.trim().toLowerCase(), sel = ed.selected;
    const key = q + '|' + (sel ? sel.name : '');
    if (!force && key === outlKey) return;
    outlKey = key;
    outl.textContent = '';
    if (q) {
      const hits = ed.items.filter((it) => it.name.toLowerCase().includes(q));
      hits.slice(0, 200).forEach((it) => row(it, 0, it.path.map((x) => x.name).join(' > ')));
      if (!hits.length) outl.textContent = 'nothing matches';
      return;
    }
    const open = new Set(sel ? sel.path : []);
    const tree = (it, depth) => {
      const r = row(it, depth);
      if (it === sel) r.scrollIntoView({ block: 'nearest' });
      if (open.has(it)) it.children.forEach((c) => tree(c, depth + 1));
    };
    for (const [label, test] of [['Cameras', (it) => it.type === 'CAMERA'], ['Lights', (it) => it.type === 'LIGHT'],
      ['Objects', (it) => it.type !== 'CAMERA' && it.type !== 'LIGHT']]) {
      const top = ed.items.filter((it) => it.depth === 0 && test(it));
      if (!top.length) continue;
      const g = document.createElement('div');
      g.className = 'grp';
      g.textContent = `${label}  ${top.length}`;
      outl.appendChild(g);
      top.forEach((it) => tree(it, 0));
    }
  }
  const buildList = () => renderOutliner(true);
  buildList();
  search.addEventListener('input', () => renderOutliner());
  search.addEventListener('keydown', (e) => { if (e.key === 'Escape' || e.key === 'Enter') search.blur(); });

  // panel and help toggles, remembered per viewer
  const uiPref = (k, v) => {
    try {
      if (v === undefined) return localStorage.getItem('vr.' + k);
      localStorage.setItem('vr.' + k, v);
    } catch (e) { /* storage off: defaults */ }
    return null;
  };
  function showPanel(on) {
    $('panel').classList.toggle('hidden', !on);
    $('togglepanel').classList.toggle('on', on);
    uiPref('panel', on ? '1' : '0');
  }
  function showHelp(on) {
    $('help').classList.toggle('hidden', !on);
    $('togglehelp').classList.toggle('on', on);
  }
  $('togglepanel').addEventListener('click', () => showPanel($('panel').classList.contains('hidden')));
  $('togglehelp').addEventListener('click', () => showHelp($('help').classList.contains('hidden')));
  showPanel(uiPref('panel') !== '0');
  for (const id of ['d_out', 'd_sel', 'd_view']) {
    const d = $(id), v = uiPref(id);
    if (v !== null) d.open = v === '1';
    d.addEventListener('toggle', () => uiPref(id, d.open ? '1' : '0'));
  }

  const setMode = (m) => {
    if (ed.selected && ed.selected.aimed && m === 'scale') m = 'translate';
    tc.setMode(m);
    for (const b of document.querySelectorAll('[data-mode]')) b.classList.toggle('on', b.dataset.mode === tc.mode);
  };
  document.querySelectorAll('[data-mode]').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
  $('space').addEventListener('click', () => { tc.setSpace(tc.space === 'local' ? 'world' : 'local'); refresh(); });

  const inten = $('intensity'), lcol = $('lightcolor');
  inten.addEventListener('input', () => {
    const it = ed.selected;
    ed.beginEdit('panel');
    ed.setEnergy(it, ed.manifest.lights[it.name].energy * 2 ** +inten.value);
  });
  inten.addEventListener('change', () => ed.endEdit());
  lcol.addEventListener('input', () => { ed.beginEdit('panel'); ed.setLightColor(ed.selected, new THREE.Color(lcol.value)); });
  lcol.addEventListener('change', () => ed.endEdit());

  $('through').addEventListener('click', () => lookThrough(view.through ? null : ed.selected));
  function lookThrough(it, via = 'user') {
    if (it && it.type === 'CAMERA' && walk.on) setWalk(false, via, true);
    view.through = it && it.type === 'CAMERA' ? it : null;
    orbit.enabled = !view.through && !walk.on;
    tch.visible = gizmoVisible();
    pilot.enabled = false;                          // armed per drag (Alt), see the pointerdown above
    if (view.through) {
      const c = view.through.obj, p = c.getWorldPosition(new THREE.Vector3());
      const d = Math.max(0.3, p.distanceTo(orbit.target));
      pilot.object = c;
      pilot.target.copy(p).addScaledVector(c.getWorldDirection(new THREE.Vector3()), d);
    }
    emitMode(via);
    refresh();
  }

  $('exposure').addEventListener('input', (e) => {
    renderer.toneMappingExposure = 2 ** +e.target.value;
    $('expval').textContent = (+e.target.value).toFixed(1) + ' EV';
  });
  $('shadows').addEventListener('change', (e) => {
    renderer.shadowMap.enabled = e.target.checked;
    scene.traverse((o) => { if (o.material) [o.material].flat().forEach((m) => { m.needsUpdate = true; }); });
  });
  $('save').addEventListener('click', () => ed.save().catch(() => {}));
  $('undo').addEventListener('click', () => ed.undo());
  $('walk').addEventListener('click', () => setWalk(!walk.on));
  $('selftest').addEventListener('click', () => {
    const r = ed.selftest();
    ed.setStatus(`selftest ${r.pass ? 'PASS' : 'FAIL'}: ${r.checked} unchanged objects checked ` +
      Object.entries(r.byType).map(([k, v]) => `${k} ${v.ok}/${v.ok + v.fail}`).join(', '));
  });

  function refresh() {
    const it = ed.selected, act = document.activeElement;
    let label = 'nothing selected';
    if (it) {
      label = `${it.name}  (${it.children.length ? `group of ${it.children.length}` : it.kind || it.type})`;
      if (it.parent) label += `  in ${it.path.slice(0, -1).map((x) => x.name).join(' > ')}`;
    }
    $('selname').textContent = label;
    $('selhint').hidden = !(it && it.children.length);
    renderOutliner();
    $('space').textContent = (tc.space === 'local' ? 'Local' : 'World') + ' L';
    $('walk').classList.toggle('on', walk.on);
    $('lightbox').hidden = !(it && it.light);
    if (it && it.light) {
      const e = ed.energy(it);
      if (act !== inten) inten.value = Math.log2(e / ed.manifest.lights[it.name].energy);
      if (act !== lcol) lcol.value = '#' + it.light.color.getHexString();
      $('energy').textContent = e.toPrecision(4) + ' W';
    }
    $('cambox').hidden = !(it && it.type === 'CAMERA') && !view.through;
    $('through').textContent = view.through ? 'Back to editor view (C)' : 'Look through camera (C)';
    $('through').classList.toggle('on', !!view.through);
    const mats = it && !it.light && it.type !== 'CAMERA' ? ed.materialsOf(it) : [];
    const mb = $('matbox');
    mb.hidden = !mats.length;
    if (mb.dataset.for !== (it ? it.name : '')) {
      mb.dataset.for = it ? it.name : '';
      mb.querySelectorAll('label, .more').forEach((l) => l.remove());
      for (const name of mats.slice(0, MAX_MAT_INPUTS)) {
        const l = document.createElement('label');
        const inp = document.createElement('input');
        inp.type = 'color';
        inp.dataset.mat = name;
        inp.addEventListener('input', () => { ed.beginEdit('panel'); ed.setMaterialColor(name, new THREE.Color(inp.value)); });
        inp.addEventListener('change', () => ed.endEdit());
        l.append(inp, ' ' + name);
        mb.appendChild(l);
      }
      if (mats.length > MAX_MAT_INPUTS) {
        const m = document.createElement('div');
        m.className = 'more muted';
        m.textContent = `+${mats.length - MAX_MAT_INPUTS} more (select a child to see its materials)`;
        mb.appendChild(m);
      }
    }
    mb.querySelectorAll('input[data-mat]').forEach((inp) => {
      if (inp !== act) inp.value = '#' + ed.materials.get(inp.dataset.mat).mats[0].color.getHexString();
    });
    const nEdits = Object.values(ed.unsavedEdits()).reduce((a, v) => a + Object.keys(v).length, 0);   // since the last save
    $('dirty').textContent = nEdits ? nEdits + ' unsaved' : 'saved';
    $('dirty').classList.toggle('on', nEdits > 0);
  }

  ed.addEventListener('select', () => {
    const it = ed.selected;
    if (it) tc.attach(it.obj); else tc.detach();
    if (it && it.aimed && tc.mode === 'scale') setMode('translate');
    if (view.through && it && it.type === 'CAMERA' && it !== view.through) lookThrough(it);
    refresh();
  });
  ed.addEventListener('change', refresh);
  ed.addEventListener('saved', refresh);
  // hot reload: new items replaced the old ones; the editor camera never moved
  ed.addEventListener('reloaded', () => {
    buildList();
    measureProps();
    $('matbox').dataset.for = '#reloaded';             // force the material inputs to rebuild
    if (view.through) lookThrough(ed.byName.get(view.through.name) || null, 'reload');
    refresh();
  });
  ed.addEventListener('status', () => { $('status').textContent = ed.status; });

  // ---- camera flights: ease in/out position + orientation; resolves when the camera arrives
  let flight = null;
  const ease = (k) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2);
  function lookQuat(p, target) {
    const m = new THREE.Matrix4().lookAt(p, target, camera.up);
    return new THREE.Quaternion().setFromRotationMatrix(m);
  }
  // position, target: three.js world; quaternion optional (else look at target)
  function flyTo({ position, target, quaternion = null, seconds = 1.2 }) {
    if (view.through) lookThrough(null, 'fly');
    if (flight) flight.resolve(false);
    const p1 = position.clone(), q1 = quaternion ? quaternion.clone() : lookQuat(p1, target);
    if (![...p1.toArray(), ...q1.toArray(), ...target.toArray()].every(Number.isFinite)) {
      return Promise.reject(new Error('flight target is not a finite pose'));
    }
    return new Promise((resolve) => {
      flight = { p0: camera.position.clone(), q0: camera.quaternion.clone(), p1, q1, target: target.clone(),
        t0: performance.now(), dur: Math.max(0, seconds) * 1000, resolve };
      orbit.enabled = false;
    });
  }
  function tickFlight() {
    if (!flight) return;
    const f = flight, k = f.dur ? Math.min(1, (performance.now() - f.t0) / f.dur) : 1, e = ease(k);
    camera.position.lerpVectors(f.p0, f.p1, e);
    camera.quaternion.slerpQuaternions(f.q0, f.q1, e);
    if (k < 1) return;
    flight = null;
    orbit.target.copy(f.target);
    if (walk.on) syncWalk();
    else { orbit.enabled = !view.through; camera.quaternion.copy(f.q1); orbit.update(); }
    f.resolve(true);
  }
  ed.preRender.push(tickFlight);

  // ---- framing (F, focus): fit the object's bounding sphere, keep the current viewing direction
  function boxOf(it) {
    const box = new THREE.Box3(), bb = new THREE.Box3();
    it.obj.updateMatrixWorld(true);
    it.obj.traverse((o) => {
      if (!(o.isMesh || o.isLine) || !o.layers.isEnabled(0) || !o.geometry) return;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      box.union(bb.copy(o.geometry.boundingBox).applyMatrix4(o.matrixWorld));
    });
    if (box.isEmpty()) {                            // lights, cameras, empty groups
      const p = it.obj.getWorldPosition(new THREE.Vector3());
      box.setFromCenterAndSize(p, new THREE.Vector3(0.4, 0.4, 0.4));
    }
    return box;
  }
  function frameItem(it, seconds = 1.0, via = 'user') {
    const s = boxOf(it).getBoundingSphere(new THREE.Sphere());
    const vfov = THREE.MathUtils.degToRad(camera.fov) / 2, hfov = Math.atan(Math.tan(vfov) * camera.aspect);
    const dist = Math.max(0.35, (s.radius / Math.sin(Math.min(vfov, hfov))) * 1.15);
    const from = view.through ? view.through.obj.getWorldPosition(new THREE.Vector3()) : camera.position;
    const dir = from.clone().sub(s.center);
    if (dir.lengthSq() < 1e-6) dir.set(0, 0.4, 1);
    dir.normalize();
    if (dir.y < -0.2) { dir.y = 0.25; dir.normalize(); }  // never frame from below the floor
    ed.emit('focus', { item: it, via });
    return flyTo({ position: s.center.clone().addScaledVector(dir, dist), target: s.center, seconds });
  }

  // ---- walk: pointer-lock mouse look, WASD (physical keys, so ZQSD on AZERTY), Q/E down/up, Shift faster
  const walk = { on: false, yaw: 0, pitch: 0, keys: new Set(), locked: false, drag: null, last: 0, floorY: 0 };
  const downRay = new THREE.Raycaster();
  function floorBelow(p) {
    downRay.set(new THREE.Vector3(p.x, p.y + 0.05, p.z), new THREE.Vector3(0, -1, 0));
    const n = new THREE.Vector3();
    for (const h of downRay.intersectObject(ed.root, true)) {
      if (!h.object.isMesh || !ed.pickable(h.object) || !h.face) continue;
      n.copy(h.face.normal).transformDirection(h.object.matrixWorld);
      const it = ed.itemOf(h.object);
      if (n.y > 0.7 && it && it.big) return h.point.y;
    }
    return null;
  }
  function syncWalk() {
    const e = new THREE.Euler().setFromQuaternion(camera.quaternion, 'YXZ');
    walk.yaw = e.y;
    walk.pitch = THREE.MathUtils.clamp(e.x, -1.5, 1.5);
  }
  function setWalk(on, via = 'user', quiet = false) {
    if (on === walk.on) return Promise.resolve(true);
    let arrive = Promise.resolve(true);
    if (on) {
      if (view.through) lookThrough(null, via);
      walk.on = true;
      orbit.enabled = false;
      tc.enabled = false;
      walk.keys.clear();
      syncWalk();
      let fy = floorBelow(camera.position);
      if (fy === null) fy = floorBelow(orbit.target);
      if (fy === null) fy = props.isEmpty() ? 0 : props.min.y;
      walk.floorY = fy;
      const p = camera.position.clone();
      p.y = fy + EYE;
      walk.pitch = THREE.MathUtils.clamp(walk.pitch, -0.6, 0.6);
      const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(walk.pitch, walk.yaw, 0, 'YXZ'));
      arrive = flyTo({ position: p, target: orbit.target, quaternion: q, seconds: 0.6 });
    } else {
      walk.on = false;
      if (document.pointerLockElement === canvas) document.exitPointerLock();
      walk.keys.clear();
      tc.enabled = true;
      const la = lookingAt();
      const d = la ? THREE.MathUtils.clamp(la.distance, 0.5, 6) : 2;
      orbit.target.copy(camera.position).addScaledVector(camera.getWorldDirection(new THREE.Vector3()), d);
      orbit.enabled = !view.through;
      if (!view.through) orbit.update();
    }
    tch.visible = gizmoVisible();
    $('crosshair').hidden = !walk.on;
    $('walkhint').hidden = !walk.on;
    if (!quiet) emitMode(via);
    refresh();
    return arrive;
  }
  function look(dx, dy) {
    walk.yaw -= dx * 0.0022;
    walk.pitch = THREE.MathUtils.clamp(walk.pitch - dy * 0.0022, -1.5, 1.5);
  }
  function walkPointerDown(e) { if (!walk.locked) walk.drag = [e.clientX, e.clientY]; }
  function walkPointerMove(e) {
    if (walk.locked || !walk.drag || !(e.buttons & 1)) return;
    look(e.clientX - walk.drag[0], e.clientY - walk.drag[1]);
    walk.drag = [e.clientX, e.clientY];
  }
  function walkPointerUp(e, click) {
    walk.drag = null;
    if (!click || flight) return;
    if (walk.locked) return pickAt(new THREE.Vector2(0, 0), e.altKey, 'walk-click');   // the crosshair
    // the first click captures the mouse; where pointer lock is not available it selects under the cursor instead
    const sel = () => pickAt(ndc(e.clientX, e.clientY), e.altKey, 'walk-click');
    try { const r = canvas.requestPointerLock(); if (r && r.catch) r.catch(sel); } catch (_) { sel(); }
  }
  document.addEventListener('mousemove', (e) => { if (walk.on && walk.locked && !flight) look(e.movementX, e.movementY); });
  document.addEventListener('pointerlockchange', () => {
    const had = walk.locked;
    walk.locked = document.pointerLockElement === canvas;
    // Esc while locked: the browser drops the lock and swallows the key. Leave walk then, but not on alt-tab.
    if (had && !walk.locked && walk.on && document.hasFocus()) setWalk(false, 'esc');
  });
  // ---- keyboard movement, always on (orbit and walk): W/S along the view flattened to the floor, A/D strafe, Q/E
  // down/up, Shift faster, arrows too. In orbit the target travels with the camera, so the orbit pivot comes along.
  const MOVE_KEYS = ['KeyW', 'KeyA', 'KeyS', 'KeyD', 'KeyQ', 'KeyE', 'ShiftLeft', 'ShiftRight', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'];
  // m/s, scaled to the scene: 1.2 crosses a 3 x 4 m room in a few seconds but took a minute across the 50 m club
  // block (the user: "walking mode really walks soooo slow"); Shift is 3x
  let SPEED = 1.2, FAST = 3.6;
  const pace = () => {
    const d = props.isEmpty() ? 0 : props.getSize(new THREE.Vector3()).length();
    SPEED = THREE.MathUtils.clamp(d / 8, 1.5, 8);
    FAST = SPEED * 3;
  };
  pace();
  ed.addEventListener('loaded', pace);
  function tickWalk() {
    const now = performance.now(), dt = Math.min(0.1, (now - (walk.last || now)) / 1000);
    walk.last = now;
    if (flight || renderer.xr.isPresenting) return;
    if (walk.on) camera.quaternion.setFromEuler(new THREE.Euler(walk.pitch, walk.yaw, 0, 'YXZ'));
    else if (view.through) return void moveThrough(dt);
    else if (tc.dragging) return;
    const k = walk.keys, v = new THREE.Vector3();
    let fwd;
    if (walk.on) fwd = new THREE.Vector3(-Math.sin(walk.yaw), 0, -Math.cos(walk.yaw));
    else {
      fwd = camera.getWorldDirection(new THREE.Vector3()).setY(0);
      if (fwd.lengthSq() < 1e-6) fwd.set(0, 1, 0).applyQuaternion(camera.quaternion).setY(0);   // looking straight down
      fwd.normalize();
    }
    const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
    if (k.has('KeyW') || k.has('ArrowUp')) v.add(fwd);
    if (k.has('KeyS') || k.has('ArrowDown')) v.sub(fwd);
    if (k.has('KeyD') || k.has('ArrowRight')) v.add(right);
    if (k.has('KeyA') || k.has('ArrowLeft')) v.sub(right);
    if (v.lengthSq()) v.normalize();
    if (k.has('KeyE')) v.y += 1;
    if (k.has('KeyQ')) v.y -= 1;
    if (!v.lengthSq()) return;
    v.multiplyScalar(dt * (k.has('ShiftLeft') || k.has('ShiftRight') ? FAST : SPEED));
    camera.position.add(v);
    if (!walk.on) orbit.target.add(v);
  }
  ed.preRender.push(tickWalk);

  // ---- look-through: the keys and the wheel move the Blender camera you are looking through (a camera operator's
  // dolly): W/S forward and back along its view flattened to the floor, A/D sideways, Q/E down and up, Shift faster;
  // the wheel dollies along the exact view direction. Each key press or wheel burst is one undo step.
  const camMove = { keys: false, wheel: 0 };
  function shiftCam(c, v) {
    const p = c.getWorldPosition(new THREE.Vector3()).add(v);
    if (c.parent) c.parent.worldToLocal(p);
    c.position.copy(p);
    c.updateMatrixWorld(true);
    pilot.target.add(v);
    ed.emit('change');
  }
  function moveThrough(dt) {
    const k = walk.keys, c = view.through.obj, v = new THREE.Vector3();
    const fwd = c.getWorldDirection(new THREE.Vector3()).setY(0);
    if (fwd.lengthSq() < 1e-6) fwd.set(0, 1, 0).applyQuaternion(c.quaternion).setY(0);
    fwd.normalize();
    const right = new THREE.Vector3(-fwd.z, 0, fwd.x);
    if (k.has('KeyW') || k.has('ArrowUp')) v.add(fwd);
    if (k.has('KeyS') || k.has('ArrowDown')) v.sub(fwd);
    if (k.has('KeyD') || k.has('ArrowRight')) v.add(right);
    if (k.has('KeyA') || k.has('ArrowLeft')) v.sub(right);
    if (v.lengthSq()) v.normalize();
    if (k.has('KeyE')) v.y += 1;
    if (k.has('KeyQ')) v.y -= 1;
    if (!v.lengthSq()) return;
    shiftCam(c, v.multiplyScalar(dt * (k.has('ShiftLeft') || k.has('ShiftRight') ? FAST : SPEED) * 0.5));
  }
  // the wheel in orbit and walk: a step forward where you look (walk: along the floor), the orbit pivot travelling
  // with the camera so the orbit stays as it was; Shift steps 4x. Trackpads send many small deltas: scale by them.
  canvas.addEventListener('wheel', (e) => {
    if (flight || view.through) return;
    e.preventDefault();
    const notches = Math.min(3, Math.abs(e.deltaMode === 1 ? e.deltaY / 3 : e.deltaY / 100));
    const step = SPEED * 0.4 * notches * (e.shiftKey ? 4 : 1) * Math.sign(-e.deltaY);
    const dir = walk.on ? new THREE.Vector3(-Math.sin(walk.yaw), 0, -Math.cos(walk.yaw))
      : camera.getWorldDirection(new THREE.Vector3());
    const v = dir.multiplyScalar(step);
    camera.position.add(v);
    if (!walk.on) { orbit.target.add(v); orbit.update(); }
  }, { passive: false });
  canvas.addEventListener('wheel', (e) => {
    if (!view.through || flight) return;
    e.preventDefault();
    if (!camMove.wheel) ed.beginEdit('camera-dolly');
    clearTimeout(camMove.wheel);
    camMove.wheel = setTimeout(() => { camMove.wheel = 0; ed.endEdit(); }, 350);
    const c = view.through.obj, step = (e.shiftKey ? 0.25 : 0.06) * Math.sign(-e.deltaY);
    shiftCam(c, c.getWorldDirection(new THREE.Vector3()).multiplyScalar(step));
  }, { passive: false });
  addEventListener('keyup', (e) => {
    walk.keys.delete(e.code);
    if (camMove.keys && ![...walk.keys].some((c) => !c.startsWith('Shift'))) { camMove.keys = false; ed.endEdit(); }
  });
  addEventListener('blur', () => walk.keys.clear());

  addEventListener('keydown', (e) => {
    const t = e.target, tag = t.tagName;
    if (tag === 'TEXTAREA' || (tag === 'INPUT' && !['range', 'checkbox', 'color'].includes(t.type))) return;
    const k = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') { e.preventDefault(); ed.undo(); return; }
    if ((e.ctrlKey || e.metaKey) && k === 's') { e.preventDefault(); ed.save().catch(() => {}); return; }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    // arrows stay with a focused slider or list; letters always move (and never jump the object list)
    const formArrow = e.code.startsWith('Arrow') && (tag === 'SELECT' || (tag === 'INPUT' && t.type === 'range'));
    if (MOVE_KEYS.includes(e.code) && !formArrow) {
      if (view.through && !e.code.startsWith('Shift') && !camMove.keys) { ed.beginEdit('camera-move'); camMove.keys = true; }
      walk.keys.add(e.code);
      e.preventDefault();
      return;
    }
    if (e.code === 'KeyV') return void setWalk(!walk.on);
    if (e.code === 'KeyH') return void showPanel($('panel').classList.contains('hidden'));
    if (e.key === '?') return void showHelp($('help').classList.contains('hidden'));
    if (walk.on && k === 'escape') return void setWalk(false, 'esc');
    if (e.code === 'Digit1' || e.code === 'Numpad1') setMode('translate');
    else if (e.code === 'Digit2' || e.code === 'Numpad2') setMode('rotate');
    else if (e.code === 'Digit3' || e.code === 'Numpad3') setMode('scale');
    else if (k === 'l') { tc.setSpace(tc.space === 'local' ? 'world' : 'local'); refresh(); }
    else if (k === 'c') lookThrough(view.through ? null : ed.selected);
    else if (k === 'p') snapshot();
    else if (k === 'escape') { lookThrough(null); ed.select(null, 'esc'); }
    else if (k === 'f' && ed.selected) frameItem(ed.selected);
    else if (e.code === 'End' && ed.selected) ed.drop(ed.selected, 'drop');   // Unreal's key: down onto what is below
  });

  // keep canvas and camera fitted to the window. Checked every frame too: a page loaded in a hidden tab or pane
  // starts at 0 x 0 (aspect NaN, which turns every camera flight into NaN) and may resize before this listener exists.
  let fitW = -1, fitH = -1;
  function fit() {
    if (renderer.xr.isPresenting || (innerWidth === fitW && innerHeight === fitH)) return;
    fitW = innerWidth; fitH = innerHeight;
    if (!fitW || !fitH) return;
    renderer.setSize(fitW, fitH);
    camera.aspect = fitW / fitH;
    camera.updateProjectionMatrix();
  }
  addEventListener('resize', fit);
  fit();

  // ---- rendering: editor view with a camera preview inset, or full look-through at 16:9
  const size = new THREE.Vector2();
  function renderInset(cam, x, y, w, h) {
    tch.visible = false;
    renderer.setScissorTest(true);
    renderer.setViewport(x, y, w, h);
    renderer.setScissor(x, y, w, h);
    renderer.render(scene, cam);
    renderer.setScissorTest(false);
    renderer.getSize(size);
    renderer.setViewport(0, 0, size.x, size.y);
    tch.visible = gizmoVisible();
  }
  function render() {
    fit();
    renderer.getSize(size);
    const W = size.x, H = size.y, pip = $('pip'), it = ed.selected;
    if (view.through) {
      const w = Math.min(W, H * 16 / 9), h = w * 9 / 16;
      renderer.setClearColor(0x000000, 1);
      renderer.clear();
      view.rect = [(W - w) / 2, (H - h) / 2, w, h];
      renderInset(view.through.obj, (W - w) / 2, (H - h) / 2, w, h);
      pip.hidden = false;
      Object.assign(pip.style, { left: (W - w) / 2 + 'px', bottom: (H - h) / 2 + 'px', width: w + 'px', height: h + 'px' });
      pip.dataset.label = view.through.name + '  16:9';
      return;
    }
    renderer.render(scene, camera);
    if (it && it.type === 'CAMERA') {
      const w = Math.min(W * 0.34, 520), h = w * 9 / 16, x = 12, y = 12;
      renderInset(it.obj, x, y, w, h);
      pip.hidden = false;
      Object.assign(pip.style, { left: x + 'px', bottom: y + 'px', width: w + 'px', height: h + 'px' });
      pip.dataset.label = it.name;
    } else pip.hidden = true;
  }

  // P: write what is on screen (the 16:9 frame when looking through a camera) to scenes/<name>/snapshots/
  async function snapshot() {
    render();
    if (!size.x || !size.y) throw new Error('the view has no size (is the tab hidden or minimised?)');
    const pr = renderer.getPixelRatio(), r = view.through ? view.rect : [0, 0, size.x, size.y];
    const c2 = document.createElement('canvas');
    c2.width = Math.round(r[2] * pr); c2.height = Math.round(r[3] * pr);
    c2.getContext('2d').drawImage(canvas, r[0] * pr, canvas.height - (r[1] + r[3]) * pr, c2.width, c2.height, 0, 0, c2.width, c2.height);
    const blob = await new Promise((res) => c2.toBlob(res, 'image/png'));
    const tag = view.through ? view.through.name : 'editor';
    const j = await fetch(`snapshot?scene=${encodeURIComponent(ed.sceneName)}&tag=${encodeURIComponent(tag)}`, { method: 'POST', body: blob }).then((x) => x.json());
    ed.setStatus(j.path ? 'snapshot ' + j.path : 'snapshot failed: ' + j.error);
    return j;
  }

  const api = {
    render, snapshot, pilot, orbit, tc, view, walk, lookThrough, refresh, mode, setWalk, flyTo, frameItem, lookingAt, boxOf, floorBelow,
    flying: () => !!flight,
    viewCamera: () => (view.through ? view.through.obj : camera),
    saveView: () => ({ p: camera.position.clone(), q: camera.quaternion.clone(), t: orbit.target.clone() }),
    restoreView: (v) => { camera.position.copy(v.p); camera.quaternion.copy(v.q); orbit.target.copy(v.t); orbit.update(); },
  };
  refresh();
  lastMode = mode();
  return api;
}
