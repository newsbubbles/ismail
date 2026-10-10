// The user's shots: what happens when both hands frame a picture, and where the pictures go.
// The moment the frame holds: a camera shutter (made in ismail, songs/crossroads/earcons/make_shutter.py: 8 variants,
// each play at its own pitch and level, so it never sounds the same twice). Once the eye's pixels are read: a white
// flash inside the frame (after the capture, so it is not in the picture), then the cropped picture appears where the
// hands were and flies to the left wrist. Turn the left palm toward you to see the last shot on the wrist; poke it to
// open the gallery (a panel: back, next, close) over every shot taken in this scene. Shots are saved as
// scenes/<scene>/snapshots/shot_<time>.png beside the full eye view (user_<time>.png), and listed by GET /snapshots.
import * as THREE from 'three';
import { GIZMO } from './editor.js';
import { OVER_WORLD } from './panels.js';

export function initGallery(ed, hands, voice, panels, live) {
  const { scene, camera } = ed;
  const scn = () => ed.sceneName;                      // live: scenes.js can switch it
  // the user's shots and Claude's review renders (snapshots/render_<time>_<what>.png, `live.py cmd {"type":
  // "gallery_add", "url": ...}`), oldest first by the time in the name
  const shots = [];
  const timeOf = (u) => (u.match(/_(\d{8}_\d{6})/) || ['', ''])[1];
  const sortShots = () => shots.sort((a, b) => timeOf(a).localeCompare(timeOf(b)));
  const loadShots = () => Promise.all(['shot', 'render'].map((t) => fetch(`snapshots?scene=${encodeURIComponent(scn())}&tag=${t}`).then((r) => r.json()).catch(() => [])))
    .then((ls) => {
      shots.length = 0;                                     // a scene switch brings that scene's shots
      for (const l of ls) if (Array.isArray(l)) shots.push(...l.filter((u) => !shots.includes(u)));
      sortShots();
      if (shots.length) setThumb(shots[shots.length - 1]);
    });
  loadShots();
  ed.addEventListener('switched', () => loadShots());
  const titleOf = (u) => { const m = u.match(/render_\d{8}_\d{6}_(.+)\.png$/); return m ? 'Claude: ' + m[1].replace(/_/g, ' ') : 'Your shot'; };
  function addShot(url) {
    if (!shots.includes(url)) { shots.push(url); sortShots(); }
    setThumb(url);
    return { n: shots.length };
  }

  const mat = (o) => new THREE.MeshBasicMaterial(Object.assign({ transparent: true, toneMapped: false, ...OVER_WORLD,
    side: THREE.DoubleSide }, o));
  const unit = new THREE.PlaneGeometry(1, 1);
  const add = (m) => { m.layers.set(GIZMO); m.renderOrder = 995; m.visible = false; scene.add(m); return m; };
  const flash = add(new THREE.Mesh(unit, mat({ color: 0xffffff, opacity: 0 })));
  const fly = add(new THREE.Mesh(unit, mat({ opacity: 1 })));
  const thumb = add(new THREE.Mesh(unit, mat({ opacity: 1 })));
  const ring = add(new THREE.Mesh(new THREE.PlaneGeometry(1.08, 1.08), mat({ color: 0xffffff, opacity: 0.5 })));
  ring.renderOrder = 994;
  const anim = { flashAt: 0, flyAt: 0, from: new THREE.Vector3(), q: new THREE.Quaternion(), w: 0.2, h: 0.15 };
  const FLASH_MS = 220, FLY_MS = 650, THUMB_W = 0.07;

  // ---- the frame: where the hands were, facing the eye
  const head = new THREE.Vector3(), tmp = new THREE.Vector3();
  function frameOf(pts) {
    head.setFromMatrixPosition(camera.matrixWorld);
    const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
    const q = new THREE.Quaternion().setFromRotationMatrix(new THREE.Matrix4().lookAt(c, head, new THREE.Vector3(0, 1, 0)));
    const inv = q.clone().invert();
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
    for (const p of pts) {
      tmp.copy(p).sub(c).applyQuaternion(inv);
      x0 = Math.min(x0, tmp.x); x1 = Math.max(x1, tmp.x); y0 = Math.min(y0, tmp.y); y1 = Math.max(y1, tmp.y);
    }
    return { c, q, w: Math.max(0.06, x1 - x0), h: Math.max(0.05, y1 - y0) };
  }

  // ---- the moment of the shot (voice.js frameShot calls these)
  function shutter() { voice.EAR.shutter(); }
  function captured(pts) {                                      // the pixels are read: flash in the frame
    const f = frameOf(pts);
    flash.position.copy(f.c); flash.quaternion.copy(f.q); flash.scale.set(f.w, f.h, 1);
    anim.from.copy(f.c); anim.q.copy(f.q); anim.w = f.w; anim.h = f.h;
    anim.flashAt = performance.now(); flash.visible = true;
  }
  function crop(canvas, rect) {
    const [x0, y0, x1, y1] = rect || [0, 0, 1, 1];
    const W = canvas.width, H = canvas.height;
    const cw = Math.max(8, Math.round((x1 - x0) * W)), ch = Math.max(8, Math.round((y1 - y0) * H));
    const out = document.createElement('canvas'); out.width = cw; out.height = ch;
    out.getContext('2d').drawImage(canvas, Math.round(x0 * W), Math.round(y0 * H), cw, ch, 0, 0, cw, ch);
    return out;
  }
  async function saved(r) {                                      // the full view is saved: crop, fly, keep
    if (!r || !r.canvas) return;
    const cv = crop(r.canvas, r.frame);
    const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
    fly.material.map = tex; fly.material.needsUpdate = true;
    anim.aspect = cv.width / cv.height;
    anim.flyAt = performance.now(); fly.visible = true;
    thumb.material.map = tex; thumb.material.needsUpdate = true; thumb.userData.aspect = anim.aspect;
    try {
      const blob = await new Promise((res) => cv.toBlob(res, 'image/png'));
      const j = await fetch(`snapshot?scene=${encodeURIComponent(scn())}&tag=shot`, { method: 'POST', body: blob }).then((x) => x.json());
      const url = 'scenes/' + scn() + '/snapshots/' + j.path.split(/[\\/]/).pop();
      shots.push(url);
      live.emit('user_shot', { path: j.path, of: r.path, n: shots.length });
    } catch (e) { live.emit('voice_error', { where: 'shot save', error: String(e.message || e) }); }
  }
  function setThumb(url) {
    new THREE.TextureLoader().load(url, (t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      thumb.material.map = t; thumb.material.needsUpdate = true;
      thumb.userData.aspect = t.image.width / t.image.height;
    });
  }

  // ---- the gallery: a panel over all the shots, newest first
  let galleryOpen = false;
  async function open(i = shots.length - 1) {
    if (galleryOpen || !shots.length) return;
    galleryOpen = true;
    const id = 'gallery_' + Date.now();
    const shown = () => ({ title: `${titleOf(shots[i])}  (${i + 1} of ${shots.length})`, image: shots[i] });
    try {
      i = (i + shots.length) % shots.length;
      let a = await panels.show({ panel_id: id, ...shown(), buttons: ['◀ back', 'next ▶', 'close'], stay: true, wait: true });
      while (true) {                                // one panel, where it is: back and next change its picture
        const ans = a && a.answer;
        if (ans === '◀ back') i -= 1;
        else if (ans === 'next ▶') i += 1;
        else break;
        i = (i + shots.length) % shots.length;
        a = await panels.rewrite(id, shown());
      }
    } finally { panels.close(id, 'gallery closed'); galleryOpen = false; }
  }
  panels.registerPokeable(thumb, () => open(), () => {});

  // ---- per XR frame
  const wristAt = new THREE.Vector3(), up = new THREE.Vector3(0, 1, 0);
  function update() {
    const now = performance.now();
    if (flash.visible) {
      const k = (now - anim.flashAt) / FLASH_MS;
      flash.material.opacity = k < 0.15 ? 0.85 : 0.85 * Math.max(0, 1 - (k - 0.15) / 0.85);
      if (k >= 1) flash.visible = false;
    }
    const L = hands.state.left;
    const haveWrist = !!(L && L.f);
    if (haveWrist) wristAt.copy(L.f.wrist).addScaledVector(up, 0.06);
    if (fly.visible) {
      const k = Math.min(1, (now - anim.flyAt) / FLY_MS), e = k * k * (3 - 2 * k);
      const hold = Math.min(1, k / 0.25);                         // a beat where the hands were, then away
      const t = Math.max(0, (k - 0.25) / 0.75), te = t * t * (3 - 2 * t);
      const to = haveWrist ? wristAt : anim.from;
      fly.position.lerpVectors(anim.from, to, te);
      fly.quaternion.copy(anim.q);
      const w = THREE.MathUtils.lerp(anim.w, THUMB_W, te);
      fly.scale.set(w, w / (anim.aspect || 1.33), 1);
      fly.material.opacity = hold < 1 ? 1 : 1 - Math.max(0, (k - 0.9) / 0.1) * 0.2;
      if (k >= 1) fly.visible = false;
      void e;
    }
    // the wrist thumbnail shows while the left palm is turned toward the face (look at your wrist)
    const show = haveWrist && L.palm === 'toward_you' && !!thumb.material.map && !fly.visible && !galleryOpen;
    thumb.visible = ring.visible = show;
    if (show) {
      head.setFromMatrixPosition(camera.matrixWorld);
      thumb.position.copy(wristAt); thumb.lookAt(head);
      const a = thumb.userData.aspect || 1.33;
      thumb.scale.set(THUMB_W, THUMB_W / a, 1);
      ring.position.copy(wristAt).addScaledVector(tmp.copy(head).sub(wristAt).normalize(), -0.001);
      ring.quaternion.copy(thumb.quaternion); ring.scale.copy(thumb.scale);
    }
  }

  return { shutter, captured, saved, open, update, shots, add: addShot };
}
