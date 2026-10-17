// Live surfaces: an image, a video, text or a live camera drawn on every mesh of a named material, now, by an agent
// (live `surface`, stage_surface). For a talk given from the stage (the user, 2026-10-17): slides and a video backdrop
// on a whiteboard, a teleprompter that scrolls, a monitor showing the camera that is on air, screens on the desks.
// The meshes get a flat, unlit material (screens glow; no tone mapping or fog) and get their own back with clear; nothing
// is saved to the scene. A surface keeps the shape of its mesh: images, video and text are drawn into a canvas of the
// surface's own aspect (measured from its UVs), fitted inside it (contain), over it (cover) or stretched; a camera
// renders at that aspect, with the camera's horizontal field of view. A re-exported room gets its surfaces back; another
// scene starts with none. In a capture (capture.js) a video is seeked to the film's time every frame and a camera
// renders every frame, so the edit sees what the room showed.
import * as THREE from 'three';
import { CAPTURE } from './capture.js';

const FITS = new Set(['contain', 'cover', 'stretch']);

// metres along u and v of a mesh's UV square: its first triangle's tangent and bitangent in world space, times the
// span of its UVs. A plane from Blender reads its own width / height, whatever its scale or rotation
export function surfaceAspect(mesh) {
  const g = mesh.geometry, pos = g.attributes.position, uv = g.attributes.uv;
  if (!pos || !uv || pos.count < 3) return 16 / 9;
  const ix = g.index ? [g.index.getX(0), g.index.getX(1), g.index.getX(2)] : [0, 1, 2];
  mesh.updateMatrixWorld(true);
  const p = ix.map((i) => new THREE.Vector3().fromBufferAttribute(pos, i).applyMatrix4(mesh.matrixWorld));
  const t = ix.map((i) => new THREE.Vector2().fromBufferAttribute(uv, i));
  const e1 = p[1].clone().sub(p[0]), e2 = p[2].clone().sub(p[0]);
  const du1 = t[1].x - t[0].x, dv1 = t[1].y - t[0].y, du2 = t[2].x - t[0].x, dv2 = t[2].y - t[0].y;
  const det = du1 * dv2 - du2 * dv1;
  if (Math.abs(det) < 1e-12) return 16 / 9;
  const T = e1.clone().multiplyScalar(dv2).sub(e2.clone().multiplyScalar(dv1)).divideScalar(det);
  const B = e2.clone().multiplyScalar(du1).sub(e1.clone().multiplyScalar(du2)).divideScalar(det);
  let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
  for (let i = 0; i < uv.count; i++) { const u = uv.getX(i), v = uv.getY(i); u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
  const a = (T.length() * (u1 - u0)) / Math.max(1e-9, B.length() * (v1 - v0));
  return Number.isFinite(a) && a > 0.05 && a < 20 ? a : 16 / 9;
}

// where a w x h picture goes on a W x H canvas
function fitRect(w, h, W, H, fit) {
  if (fit === 'stretch') return [0, 0, W, H];
  const s = fit === 'cover' ? Math.max(W / w, H / h) : Math.min(W / w, H / h);
  return [(W - w * s) / 2, (H - h * s) / 2, w * s, h * s];
}

// text wrapped to the canvas width; a blank line between paragraphs stays
function wrap(ctx, text, width) {
  const out = [];
  for (const para of String(text).split('\n')) {
    if (!para.trim()) { out.push(''); continue; }
    let line = '';
    for (const word of para.split(/\s+/)) {
      const next = line ? line + ' ' + word : word;
      if (line && ctx.measureText(next).width > width) { out.push(line); line = word; } else line = next;
    }
    out.push(line);
  }
  return out;
}

export function initSurfaces(ed, live, songNow) {
  const on = new Map();          // material name -> surface
  const renderer = ed.renderer;
  const scratch = new THREE.PerspectiveCamera(50, 16 / 9, 0.05, 2000);   // layer 0 only, like a capture

  function materialName(c) {
    let name = c.material || null;
    if (!name && c.object) {
      const it = ed.byName.get(c.object);
      if (!it) throw new Error(`no object "${c.object}"`);
      const ms = ed.materialsOf(it);
      if (ms.length !== 1) throw new Error(`"${c.object}" has ${ms.length} materials (${ms.join(', ') || 'none'}): pass material`);
      name = ms[0];
    }
    if (!name) throw new Error('surface needs material (or object)');
    if (!ed.materials.has(name)) name = [...ed.materials.keys()].find((n) => n.toLowerCase() === String(name).toLowerCase()) || name;
    if (!ed.materials.has(name)) throw new Error(`no material "${name}"`);
    return name;
  }

  function meshesOf(name) {          // the material's meshes, those showing a surface now included
    const mine = new Set(ed.materials.get(name).mats), out = [];
    if (on.has(name)) mine.add(on.get(name).mat);
    ed.scene.traverse((o) => { if (o.isMesh && !Array.isArray(o.material) && mine.has(o.material)) out.push(o); });
    return out;
  }

  function release(s) {
    for (const [mesh, m] of s.kept) mesh.material = m;
    s.kept.clear();
    if (s.video) { s.video.pause(); s.video.removeAttribute('src'); s.video.load(); }
    if (s.blobUrl) URL.revokeObjectURL(s.blobUrl);
    if (s.rt) s.rt.dispose();
    if (s.tex) s.tex.dispose();
    s.mat.dispose();
  }

  const url = (p) => `scenes/${encodeURIComponent(ed.sceneName)}/` + String(p).split(/[\\/]/).map(encodeURIComponent).join('/');

  async function build(name, c, old) {
    const meshes = meshesOf(name);
    if (!meshes.length) throw new Error(`material "${name}" is on no mesh`);
    const aspect = c.aspect || surfaceAspect(meshes[0]);
    const px = Math.max(64, Math.min(4096, Math.round(c.px || 1024)));
    const W = aspect >= 1 ? px : Math.round(px * aspect), H = aspect >= 1 ? Math.round(px / aspect) : px;
    const fit = c.fit || 'contain';
    if (!FITS.has(fit)) throw new Error(`fit is ${[...FITS].join(', ')}`);
    const glow = c.glow ?? 1;
    const s = { name, c: { ...c }, kept: new Map(), W, H, fit, source: null, meshes };
    if (c.camera) {
      const it = ed.byName.get(c.camera);
      if (!it || it.type !== 'CAMERA') throw new Error(`no camera "${c.camera}"`);
      s.source = 'camera';
      s.cam = it.obj;
      s.fps = c.fps || 15;
      s.last = -Infinity;
      s.rt = new THREE.WebGLRenderTarget(W, H);
      s.rt.texture.colorSpace = THREE.SRGBColorSpace;
      s.tex = s.rt.texture;
      s.tex.repeat.set(1, -1);    // a render target's first row is its bottom; glTF UVs put v = 0 at the top
      s.tex.offset.set(0, 1);
    } else {
      s.canvas = document.createElement('canvas');
      s.canvas.width = W; s.canvas.height = H;
      s.ctx = s.canvas.getContext('2d');
      s.tex = new THREE.CanvasTexture(s.canvas);
      s.tex.colorSpace = THREE.SRGBColorSpace;
      s.tex.flipY = false;        // glTF UVs: v = 0 is the top row
      s.bg = c.background || '#000000';
      if (c.image) {
        s.source = 'image';
        const img = new Image();
        img.src = url(c.image);
        try { await img.decode(); } catch (_) { throw new Error(`cannot load image "${c.image}"`); }
        s.ctx.fillStyle = s.bg; s.ctx.fillRect(0, 0, W, H);
        s.ctx.drawImage(img, ...fitRect(img.naturalWidth, img.naturalHeight, W, H, fit));
        s.size = [img.naturalWidth, img.naturalHeight];
      } else if (c.video) {
        s.source = 'video';
        // the whole file as a blob: seekable without range requests (a capture seeks every frame)
        const r = await fetch(url(c.video));
        if (!r.ok) throw new Error(`cannot load video "${c.video}" (${r.status})`);
        s.blobUrl = URL.createObjectURL(await r.blob());
        const v = s.video = document.createElement('video');
        v.muted = !c.sound; v.loop = c.loop !== false; v.playsInline = true; v.crossOrigin = 'anonymous';
        v.src = s.blobUrl;
        await new Promise((res, rej) => { v.onloadeddata = res; v.onerror = () => rej(new Error(`cannot play video "${c.video}"`)); });
        s.size = [v.videoWidth, v.videoHeight];
        s.from = c.from || 0;
        s.song0 = songNow();
        s.vt = -1;
        if (CAPTURE) await seek(s, s.song0);
        else { v.currentTime = s.from; v.play().catch(() => {}); }
      } else if (c.text != null) {
        s.source = 'text';
        s.lines = Math.max(1, c.lines || 8);
        s.font = c.font || 'sans-serif';
        s.fg = c.color || '#ffffff';
        s.align = c.align || 'left';
        const fontPx = Math.round(H / s.lines / 1.25);
        s.ctx.font = `${fontPx}px ${s.font}`;
        s.fontPx = fontPx;
        s.wrapped = wrap(s.ctx, c.text, W - 2 * Math.round(W * 0.04));
        s.scroll = c.scroll || 0;            // lines a second
        s.top0 = c.line || 0;
        s.t0 = performance.now();
        s.drawnTop = null;
      } else throw new Error('surface needs image, video, text, camera or clear');
    }
    s.mat = new THREE.MeshBasicMaterial({ map: s.tex, color: new THREE.Color(glow, glow, glow), toneMapped: false, fog: false, side: THREE.DoubleSide });
    s.mat.name = 'surface:' + name;
    for (const m of meshes) { s.kept.set(m, (old && old.kept.get(m)) || m.material); m.material = s.mat; }
    return s;
  }

  function drawText(s) {
    const top = Math.max(0, s.top0 + s.scroll * (performance.now() - s.t0) / 1000);
    if (top === s.drawnTop) return;
    s.drawnTop = top;
    const { ctx, W, H, fontPx } = s, lh = fontPx * 1.25, pad = Math.round(W * 0.04);
    ctx.fillStyle = s.bg; ctx.fillRect(0, 0, W, H);
    ctx.font = `${fontPx}px ${s.font}`;
    ctx.fillStyle = s.fg;
    ctx.textBaseline = 'top';
    ctx.textAlign = s.align;
    const x = s.align === 'center' ? W / 2 : s.align === 'right' ? W - pad : pad;
    const i0 = Math.floor(top), off = (top - i0) * lh;
    for (let i = i0; i < s.wrapped.length && (i - i0) * lh - off < H; i++) ctx.fillText(s.wrapped[i], x, (i - i0) * lh - off + lh * 0.1);
    s.tex.needsUpdate = true;
  }

  function drawVideo(s) {
    const v = s.video;
    if (v.readyState < 2 || v.currentTime === s.vt) return;
    s.vt = v.currentTime;
    s.ctx.fillStyle = s.bg; s.ctx.fillRect(0, 0, s.W, s.H);
    s.ctx.drawImage(v, ...fitRect(v.videoWidth, v.videoHeight, s.W, s.H, s.fit));
    s.tex.needsUpdate = true;
  }

  async function seek(s, songT) {
    const v = s.video, d = v.duration || 0;
    let t = s.from + Math.max(0, songT - s.song0);
    if (d > 0) t = v.loop ? t % d : Math.min(t, d - 0.001);
    if (Math.abs(v.currentTime - t) < 1e-4 && v.readyState >= 2) return;
    await new Promise((res) => {
      const done = () => { clearTimeout(k); v.removeEventListener('seeked', done); res(); };
      const k = setTimeout(done, 3000);
      v.addEventListener('seeked', done);
      v.currentTime = t;
    });
  }

  function drawCamera(s) {
    const now = performance.now();
    if (!CAPTURE && now - s.last < 1000 / s.fps) return;
    s.last = now;
    const cam = s.cam, a = s.W / s.H;
    cam.updateMatrixWorld(true);
    scratch.matrixWorld.copy(cam.matrixWorld);
    scratch.matrixWorld.decompose(scratch.position, scratch.quaternion, scratch.scale);
    const hfov = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(cam.fov || 50) / 2) * (cam.aspect || 16 / 9));
    scratch.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(hfov / 2) / a));
    scratch.aspect = a;
    scratch.near = cam.near || 0.05; scratch.far = Math.max(cam.far || 0, 500);
    scratch.updateProjectionMatrix();
    scratch.updateMatrixWorld(true);
    const xr = renderer.xr, rt = renderer.getRenderTarget(), xrOn = xr.enabled, auto = renderer.shadowMap.autoUpdate;
    for (const m of s.meshes) m.visible = false;       // never itself (a texture cannot be drawn into while it is read)
    xr.enabled = false;
    renderer.shadowMap.autoUpdate = false;
    renderer.setRenderTarget(s.rt);
    renderer.render(ed.scene, scratch);
    renderer.setRenderTarget(rt);
    xr.enabled = xrOn;
    renderer.shadowMap.autoUpdate = auto;
    for (const m of s.meshes) m.visible = true;
  }

  ed.preRender.push(function surfaces() {
    for (const s of on.values()) {
      if (s.source === 'text' && s.scroll) drawText(s);
      else if (s.source === 'video') drawVideo(s);
      else if (s.source === 'camera') drawCamera(s);
    }
  });

  const summary = () => [...on.values()].map((s) => ({ material: s.name, source: s.source }));

  async function set(c) {
    if (c.clear) {
      const names = c.material || c.object ? [materialName(c)] : [...on.keys()];
      for (const n of names) { const s = on.get(n); if (s) { release(s); on.delete(n); } }
      live.emit('surface_cleared', { materials: names });
      return { cleared: names, on: summary() };
    }
    const name = materialName(c);
    const old = on.get(name);
    const s = await build(name, c, old);
    if (old) { old.kept.clear(); release(old); }     // its meshes are the new one's now, with their own material kept
    on.set(name, s);
    if (s.source === 'text') drawText(s);
    if (s.source === 'video') drawVideo(s);
    if (s.source === 'camera') drawCamera(s);
    const out = { surface: name, source: s.source, meshes: s.meshes.length, px: [s.W, s.H], aspect: +(s.W / s.H).toFixed(3) };
    if (s.size) out.size = s.size;
    if (s.wrapped) out.text_lines = s.wrapped.length;
    live.emit('surface_set', { material: name, source: s.source });
    return { ...out, on: summary() };
  }
  live.handlers.surface = set;

  // a re-exported room: the old meshes are gone, put every surface on the new ones; another scene: none
  live.onEmit((type) => {
    if (type !== 'scene_reload' || !on.size) return;
    const was = [...on.values()].map((s) => s.c);
    for (const s of on.values()) { s.kept.clear(); release(s); }
    on.clear();
    (async () => { for (const c of was) { try { await set(c); } catch (e) { live.emit('surface_lost', { material: c.material || c.object, error: e.message }); } } })();
  });
  ed.addEventListener('switched', () => { for (const s of on.values()) { s.kept.clear(); release(s); } on.clear(); });

  // a capture's frame: every video at the film's time, drawn before the frame is
  async function sync(songT) {
    for (const s of on.values()) if (s.source === 'video') { await seek(s, songT); drawVideo(s); }
  }
  return { set, sync, on };
}
