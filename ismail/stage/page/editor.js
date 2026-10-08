// Core of the VR scene editor: loads a Blender export, keeps every object's state, converts to and from Blender
// world space, undo, save, selftest. desktop.js and xr.js drive it.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { meshKey } from './reveal.js';
const QUEST_UA = /OculusBrowser|Quest/i.test(navigator.userAgent);
import { RectAreaLightUniformsLib } from 'three/addons/lights/RectAreaLightUniformsLib.js';

export const GIZMO = 3;

// A night sky for exterior scenes: a dome from a dusky horizon (a faint warm glow, town lights far off) to deep blue
// overhead, and stars. Layer 0, so the headset and Blender-camera monitors both see it.
// MakeHuman wires texture alpha into every material, so the exporter marks skin, clothes, eyes and mouth BLEND; a
// blended mesh is not sorted within itself, so the inside of a jacket or a head draws over its outside (the user,
// 2026-10-03: "the normals on the people who were dancing ... seemed inverted"). A textured material at full opacity
// renders opaque with an alpha cutout (brows and lashes keep their shape); glass and bottles (opacity < 1) still blend.
// Posed people are plain meshes since vr_merge, so this goes by the material, not by skinning; actors.js uses it too.
// what the Quest can afford (2026-10-03: the room ran at a median 31 fps against 90; 16 transmissive materials made
// three render the opaque scene a second time per eye every frame glass was in view)
export function questMaterial(m) {
  if (!m || !m.isMeshPhysicalMaterial) return;
  if (m.transmission > 0) {
    m.transmission = 0;
    m.transparent = true;
    m.opacity = Math.min(m.opacity, 0.4);
    m.depthWrite = false;
  }
  m.clearcoat = 0; m.sheen = 0; m.iridescence = 0; m.anisotropy = 0;
  m.needsUpdate = true;
}
export function cutout(m) {
  if (!m || !m.transparent || !m.map || m.opacity < 0.99) return false;
  m.transparent = false;
  m.depthWrite = true;
  m.alphaTest = 0.5;
  m.needsUpdate = true;
  return true;
}

function nightSky() {
  const g = new THREE.Group();
  g.name = 'sky';
  const dome = new THREE.Mesh(new THREE.SphereGeometry(150, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: 'varying vec3 vRay; void main() { vec4 w = modelMatrix * vec4(position, 1.0); vRay = w.xyz - cameraPosition; gl_Position = projectionMatrix * viewMatrix * w; }',
    fragmentShader: `varying vec3 vRay;
      const vec3 MOON = vec3(0.656, 0.375, -0.656);
      void main() {
        vec3 vDir = normalize(vRay);
        float h = clamp(vDir.y, -0.1, 1.0);
        vec3 zenith = vec3(0.010, 0.016, 0.045), mid = vec3(0.030, 0.045, 0.100), horizon = vec3(0.110, 0.090, 0.110);
        vec3 c = mix(horizon, mid, smoothstep(0.0, 0.18, h));
        c = mix(c, zenith, smoothstep(0.18, 0.75, h));
        c += vec3(0.10, 0.05, 0.02) * exp(-max(h, 0.0) * 22.0);   // warm glow low on the horizon
        float m = dot(normalize(vDir), MOON);                      // the moon (where Blender's moon light comes from)
        c += vec3(0.16, 0.17, 0.2) * pow(max(m, 0.0), 300.0) + vec3(0.05, 0.055, 0.07) * pow(max(m, 0.0), 40.0);
        c += vec3(1.6, 1.55, 1.4) * smoothstep(0.99990, 0.99994, m);
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  }));
  dome.renderOrder = -10;
  dome.frustumCulled = false;
  const n = 1800, pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < n; i++) {
    const y = 0.08 + 0.92 * Math.sqrt(rnd()), a = rnd() * Math.PI * 2, r = Math.sqrt(1 - y * y);
    pos.set([Math.cos(a) * r * 140, y * 140, Math.sin(a) * r * 140], i * 3);
    const b = 0.25 + 0.75 * rnd() ** 3;
    col.set([b, b, b * (0.9 + 0.2 * rnd())], i * 3);
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  sg.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const stars = new THREE.Points(sg, new THREE.PointsMaterial({ size: 1.6, sizeAttenuation: false, vertexColors: true, depthWrite: false, fog: false }));
  stars.renderOrder = -9;
  stars.frustumCulled = false;
  // clouds (the user, under the stars in the headset: "maybe we could also make clouds"): a layer over the stars, the
  // same recipe as Blender's world (bf_look.py): noise on a plane above, thinner toward the horizon, moonlit edges,
  // drifting slowly; the moon shows through the gaps
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(130, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, transparent: true, fog: false, uniforms: { uTime: { value: 0 } },
    vertexShader: 'varying vec3 vRay; void main() { vec4 w = modelMatrix * vec4(position, 1.0); vRay = w.xyz - cameraPosition; gl_Position = projectionMatrix * viewMatrix * w; }',
    fragmentShader: `varying vec3 vRay; uniform float uTime;
      const vec3 MOON = vec3(0.656, 0.375, -0.656);
      float hash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }   // no sin(): it breaks into blocks on the Quest's GPU
      float vnoise(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
      float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 6; i++) { s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; } return s; }
      void main() {
        vec3 vDir = normalize(vRay);
        vec3 d = vDir;
        if (d.y < -0.02) discard;
        vec2 p = d.xz / (d.y + 0.12) * 2.4 + mod(vec2(uTime * 0.01, uTime * 0.004), 1000.0);
        float n = fbm(p + 0.6 * vec2(fbm(p * 0.7 + 3.0), fbm(p * 0.7 + 9.0)));
        float cov = smoothstep(0.47, 0.62, n) * smoothstep(-0.02, 0.1, d.y);
        float thin = 1.0 - smoothstep(0.47, 0.56, n);
        float lit = pow(max(dot(d, MOON), 0.0), 8.0);
        vec3 col = vec3(0.008, 0.010, 0.017) + vec3(0.24, 0.25, 0.3) * lit * (0.25 + 0.75 * thin)
                 + vec3(0.03, 0.018, 0.012) * exp(-max(d.y, 0.0) * 14.0);   // town glow on their bellies low down
        gl_FragColor = vec4(col, cov * 0.96);
        #include <colorspace_fragment>
      }`,
  }));
  clouds.renderOrder = -8;
  clouds.frustumCulled = false;
  clouds.onBeforeRender = () => { clouds.material.uniforms.uTime.value = performance.now() / 1000; };
  g.add(dome, stars, clouds);
  // the camera sees 200 m: the dome rides with the viewer (one frame behind, which a sky never shows)
  // read the eye's position from its matrix: getWorldPosition() recomputes the matrix of an XR eye camera mid-frame,
  // and the user saw objects and their hands vanish as they turned their head
  dome.onBeforeRender = (r, s, cam) => { g.position.setFromMatrixPosition(cam.matrixWorld); };
  return g;
}

// Daylight on demand (the user, outside at night, 2026-10-03: "could you turn it to daytime real quick so I could
// actually like inspect the trees?"): a blue dome with a sun and white clouds (the night clouds' recipe), a sun and a
// sky fill; the scene's own lights stay as they are. Live: {"type": "sky", "mode": "day" | "night" | "scene"}.
const SUN = new THREE.Vector3(0.45, 0.75, -0.48).normalize();
function daySky() {
  const g = new THREE.Group();
  g.name = 'sky';
  const vert = 'varying vec3 vRay; void main() { vec4 w = modelMatrix * vec4(position, 1.0); vRay = w.xyz - cameraPosition; gl_Position = projectionMatrix * viewMatrix * w; }';
  const dome = new THREE.Mesh(new THREE.SphereGeometry(150, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false, uniforms: { uSun: { value: SUN } }, vertexShader: vert,
    fragmentShader: `varying vec3 vRay; uniform vec3 uSun;
      void main() {
        vec3 vDir = normalize(vRay);
        vec3 d = vDir; float h = clamp(d.y, -0.1, 1.0);
        vec3 c = mix(vec3(0.40, 0.53, 0.70), vec3(0.06, 0.18, 0.55), smoothstep(0.0, 0.45, h));   // linear: sRGB pale blue to deep blue
        c = mix(c, vec3(0.45, 0.42, 0.38), smoothstep(0.0, -0.1, d.y));
        float m = max(dot(d, uSun), 0.0);
        c += vec3(1.0, 0.9, 0.7) * (0.25 * pow(m, 12.0) + 6.0 * smoothstep(0.9993, 0.9996, m));
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  }));
  dome.renderOrder = -10;
  dome.frustumCulled = false;
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(130, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, transparent: true, fog: false, uniforms: { uTime: { value: 0 }, uSun: { value: SUN } },
    vertexShader: vert,
    fragmentShader: `varying vec3 vRay; uniform float uTime; uniform vec3 uSun;
      float hash(vec2 p) { vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }   // no sin(): it breaks into blocks on the Quest's GPU
      float vnoise(vec2 p) { vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
      float fbm(vec2 p) { float a = 0.5, s = 0.0; for (int i = 0; i < 5; i++) { s += a * vnoise(p); p = p * 2.03 + 17.1; a *= 0.5; } return s; }
      void main() {
        vec3 vDir = normalize(vRay);
        vec3 d = vDir;
        if (d.y < -0.02) discard;
        vec2 p = d.xz / (d.y + 0.12) * 2.4 + mod(vec2(uTime * 0.01, uTime * 0.004), 1000.0);
        float n = fbm(p + 0.6 * vec2(fbm(p * 0.7 + 3.0), fbm(p * 0.7 + 9.0)));
        float cov = smoothstep(0.47, 0.62, n) * smoothstep(-0.02, 0.1, d.y);
        float thick = smoothstep(0.5, 0.75, n);
        vec3 col = mix(vec3(0.98, 0.97, 0.95), vec3(0.62, 0.64, 0.68), thick) + 0.15 * pow(max(dot(d, uSun), 0.0), 6.0);
        gl_FragColor = vec4(col, cov * 0.9);
        #include <colorspace_fragment>
      }`,
  }));
  clouds.renderOrder = -8;
  clouds.frustumCulled = false;
  clouds.onBeforeRender = () => { clouds.material.uniforms.uTime.value = performance.now() / 1000; };
  g.add(dome, clouds);
  dome.onBeforeRender = (r, s, cam) => { g.position.setFromMatrixPosition(cam.matrixWorld); };
  return g;
}
// the day's sun and sky fill live in the scene for good, at 0 when it is not day (editor._skyLights)
function skyLights(scene) {
  const sun = new THREE.DirectionalLight(0xfff2e0, 0);
  sun.position.copy(SUN).multiplyScalar(50);
  const hemi = new THREE.HemisphereLight(0xbcd4f0, 0x6b5a45, 0);
  scene.add(sun, sun.target, hemi);
  return { set(day) { sun.intensity = day ? 3.2 : 0; hemi.intensity = day ? 1.4 : 0; } };
}
        // layer for handles, frustums, outlines: Blender cameras never see it
const W2LM = 683;              // the glTF exporter's watts->lumens factor; dividing it out puts lights in Blender units

// Blender world (Z-up) -> three world (Y-up): (x, y, z) -> (x, z, -y), i.e. C = -90 deg about X.
// Meshes: q_three = C q_blender C^-1. Lights and cameras ("aimed") look down local -Z in both programs, so the
// exporter turns their local axes too: q_three = C q_blender, and their scale stays in local axes.
const QC = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const QCi = QC.clone().invert();
export const b2tPos = ([x, y, z]) => new THREE.Vector3(x, z, -y);
export const t2bPos = (v) => [v.x, -v.z, v.y];
export function b2tQuat([w, x, y, z], aimed) {
  const q = QC.clone().multiply(new THREE.Quaternion(x, y, z, w));
  return aimed ? q : q.multiply(QCi);
}
export function t2bQuat(q, aimed) {
  const r = QCi.clone().multiply(q);
  if (!aimed) r.multiply(QC);
  return [r.w, r.x, r.y, r.z];
}
export const b2tScale = ([x, y, z], aimed) => (aimed ? new THREE.Vector3(x, y, z) : new THREE.Vector3(x, z, y));
export const t2bScale = (s, aimed) => (aimed ? [s.x, s.y, s.z] : [s.x, s.z, s.y]);

const r7 = (a) => a.map((x) => Math.round(x * 1e7) / 1e7);
const lin = (rgb) => new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.LinearSRGBColorSpace);
const rgbOf = (c) => [c.r, c.g, c.b];

export class Editor extends THREE.EventDispatcher {
  constructor(sceneName) {
    super();
    this.sceneName = sceneName;
    this.items = [];            // one per Blender object
    this.pickRoots = [];        // what rays hit; empty in the construct, before the room is in
    this.manifest = { objects: {}, lights: {} };   // until the scene is here (the page starts in the construct)
    this.loaded = false;
    this.byName = new Map();
    this.materials = new Map(); // Blender material name -> {mats: [three materials], color0: Color}
    this.selected = null;
    this.unlocked = null;       // in VR only this item may be carried (actions.js: the Move button)
    this.undoStack = [];
    this.editing = false;
    this.preRender = [];        // (renderer) => void, run before the main render each frame
    this.status = '';

    const r = (this.renderer = new THREE.WebGLRenderer({ antialias: true }));
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.setSize(innerWidth, innerHeight);
    r.outputColorSpace = THREE.SRGBColorSpace;
    r.toneMapping = THREE.AgXToneMapping;          // lucy.py renders with AgX, look None
    r.shadowMap.enabled = true;
    r.shadowMap.type = THREE.PCFShadowMap;
    r.shadowMap.autoUpdate = false;              // the point light's cube shadow was six extra renders a frame (main.js)
    r.xr.enabled = true;
    document.body.appendChild(r.domElement);
    RectAreaLightUniformsLib.init();

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(50, innerHeight ? innerWidth / innerHeight : 16 / 9, 0.01, 200);
    this.camera.layers.enable(GIZMO);
    this.rig = new THREE.Group();                   // XR moves this; desktop leaves it at identity
    this.rig.add(this.camera);
    this.scene.add(this.rig);
    this.outline = new THREE.BoxHelper(undefined, 0xffd23f);
    this.outline.material.depthTest = false;
    this.outline.material.toneMapped = false;
    this.outline.renderOrder = 999;
    this.outline.layers.set(GIZMO);
    this.outline.visible = false;
    this.scene.add(this.outline);
  }

  async load() {
    const { man, gltf } = await this._fetchScene();
    this.scene.background = new THREE.Color().setRGB(0.003, 0.003, 0.0035, THREE.LinearSRGBColorSpace);
    this.emit('building');
    await new Promise((r) => setTimeout(r, 30));     // let the card say so before the install
    this._install(man, gltf);
    await this._applySaved();
    this.loaded = true;
    this.emit('loaded');                             // the user is placed first (xr.js), so the nearest pieces come first
    this.emit('staging');                            // loadsets.js hides the unloaded sets: they never reach the GPU
    // the room assembles piece by piece around the user (reveal.js): never the whole room in one frame
    const r = await this.stage(this.root);
    console.log(`[vr] room revealed: ${r.meshes} pieces in ${r.seconds} s`);
    this.emit('revealed', r);
    return this;
  }

  // v busts any cache between a re-export and the browser
  async _fetchScene(v = Date.now(), name = this.sceneName) {
    const base = `scenes/${name}/`;
    const [man, gltf] = await Promise.all([
      fetch(base + 'manifest.json?v=' + v, { cache: 'no-store' })
        .then((r) => { if (!r.ok) throw new Error('no manifest for ' + name); return r.json(); })
        .then((man) => { if (!this.loaded) { this._world(man); this.emit('manifest', { man }); } return man; }),   // the sky first
      this._fetchGlb(base + 'scene.glb?v=' + v).then((buf) => new GLTFLoader().parseAsync(buf, base)),
    ]);
    return { man, gltf };
  }

  // The scene with a watchdog. After the Quest sleeps or the user leaves the browser, it can hand a new request to a
  // dead pooled connection and wait on it for minutes ("loading scene" at 0 MB, a lottery; quitting the Browser app
  // was the only cure). A request that brings no bytes for STALL ms is aborted and asked again, each try a new URL.
  async _fetchGlb(url, STALL = 7000, TRIES = 6) {
    const el = document.getElementById('loading');
    const show = (t) => { if (el) el.textContent = t; };
    for (let n = 1; ; n++) {
      const ac = new AbortController();
      let last = performance.now(), got = 0, total = 0;
      const dog = setInterval(() => { if (performance.now() - last > STALL) ac.abort(); }, 500);
      try {
        const r = await fetch(url + '&try=' + n, { cache: 'no-store', signal: ac.signal });
        if (!r.ok) { clearInterval(dog); throw Object.assign(new Error('scene.glb: HTTP ' + r.status), { final: true }); }
        total = Number(r.headers.get('Content-Length')) || 0;
        last = performance.now();
        const out = total ? new Uint8Array(total) : null, parts = [];
        const rd = r.body.getReader();
        for (;;) {
          const { done, value } = await rd.read();
          if (done) break;
          last = performance.now();
          if (out && got + value.length <= total) out.set(value, got); else parts.push(value);
          got += value.length;
          show(`loading scene ${(got / 1e6).toFixed(1)}` + (total ? ` of ${(total / 1e6).toFixed(1)} MB` : ' MB') + (n > 1 ? ` (try ${n})` : ''));
          this.emit('load_progress', { got, total, n });
        }
        clearInterval(dog);
        if (out && !parts.length && got === total) return out.buffer;
        const all = new Uint8Array(got); let o = 0;
        if (out) { all.set(out.subarray(0, Math.min(got, total)), 0); o = Math.min(got, total); }
        for (const p of parts) { all.set(p, o); o += p.length; }
        return all.buffer;
      } catch (e) {
        clearInterval(dog);
        if (e.final) throw e;                                    // an answer (404...), not a stall
        if (n >= TRIES) throw new Error(`scene.glb: no luck after ${n} tries (${e.message || e})`);
        show(`loading scene: stalled at ${(got / 1e6).toFixed(1)} MB, asking again (try ${n + 1})`);
        this.emit('load_progress', { got, total, n, stalled: true });
        console.warn('scene.glb stalled, retrying', n, e);
        await new Promise((res) => setTimeout(res, 300));
      }
    }
  }

  async _applySaved() {
    const ed = await fetch(`edits?scene=${encodeURIComponent(this.sceneName)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    this.loadedEdits = ed;
    if (ed) this.applyEdits(ed);
    this.savedJSON = JSON.stringify(this.computeEdits());
  }

  // the edits that differ from what edits.json holds (edits made since the last save or load)
  unsavedEdits() {
    const cur = this.computeEdits(), saved = JSON.parse(this.savedJSON || '{}'), out = {};
    for (const k of ['objects', 'lights', 'materials']) {
      out[k] = {};
      for (const [n, v] of Object.entries(cur[k])) {
        if (JSON.stringify(v) !== JSON.stringify((saved[k] || {})[n])) out[k][n] = v;
      }
    }
    return out;
  }

  // Hot reload after a re-export: swap the glb and manifest in place. The editor camera, rig, markers and helpers are
  // not part of the glb, so the view stays put. edits.json is applied as on a page load, then the unsaved edits go
  // back on top by name; names that no longer exist are dropped and reported. Undo history cannot survive (it
  // indexes the old objects) and is cleared.
  async reload(v) {
    const unsaved = this.unsavedEdits(), selName = this.selected ? this.selected.name : null;
    const { man, gltf } = await this._fetchScene(v);       // a failed fetch leaves the old scene untouched
    return this._swapIn(man, gltf, unsaved, selName, true);
  }
  // Another scene, in place (scenes.js; the user, 2026-10-03: control scene swaps "just like how you do in-experience
  // editing"): its manifest, sky, objects and saved edits replace this one's; the rig, the hands, the live link and
  // every module stay. A failed fetch leaves the current scene and its name untouched.
  async switchTo(name, v = Date.now()) {
    const prev = this.sceneName;
    const got = await this._fetchScene(v, name);
    this.emit('switching', { from: prev, to: name });      // still the old scene's name: xr.js saves where the user stood
    this.sceneName = name;
    this.skyMode = undefined;                               // each scene keeps its own remembered sky
    this._world(got.man);
    this.emit('manifest', { man: got.man });
    // the new scene's world.json before its room is staged: its unloaded sets (loadsets.js) and its resting people
    // (actors.js, on 'revealed') read it; main.js sets loadWorld
    if (this.loadWorld) await this.loadWorld(name).catch((e) => console.warn('[vr] world', e));
    const info = await this._swapIn(got.man, got.gltf, {}, null);
    this.emit('switched', { from: prev, to: name, ...info });
    return { from: prev, to: name, ...info };
  }
  async _swapIn(man, gltf, unsaved, selName, sameScene = false) {
    const old = this.root;
    const drop = (r) => {
      this.scene.remove(r);
      r.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        for (const m of [o.material].flat()) if (m) {
          for (const val of Object.values(m)) if (val && val.isTexture) val.dispose();
          m.dispose();
        }
        if (o.shadow && o.shadow.map) o.shadow.map.dispose();
      });
    };
    // a re-export of the same scene: the old room stays up and each new piece replaces its twin as it appears (no
    // blink, no stall); another scene comes in behind the construct, so the old one can go at once
    let replace = null;
    // (only while the old room is still on show: under the construct, scenes.js, it is hidden, its lights off, and the
    // new room simply assembles in the void with its own lights from the start)
    if (sameScene && old.visible) {
      replace = new Map();
      old.traverse((o) => { if ((o.isMesh || o.isPoints || o.isLine) && o.visible) replace.set(meshKey(o), o); });
    } else drop(old);
    this.selected = null;
    this.outline.visible = false;
    this.items = [];
    this.byName = new Map();
    this.materials = new Map();
    this.undoStack = [];
    this.editing = false;
    this._install(man, gltf);
    await this._applySaved();
    const res = this.applyEdits(unsaved, true);
    const sel = selName ? this.byName.get(selName) || null : null;
    this.select(sel, 'reload');
    this.emit('staging');
    const r = await this.stage(this.root, replace ? { replace, oldRoot: old, minS: QUEST_UA ? 4 : 1 } : {});
    if (replace) drop(old);
    this.emit('revealed', r);
    const info = { objects: this.items.length, kept_edits: res.applied, dropped: res.missing,
      selection: sel ? sel.name : null, lost_selection: selName && !sel ? selName : null };
    this.setStatus(`scene updated: ${info.objects} objects, ${res.applied.length} unsaved edits kept` +
      (res.missing.length ? `, ${res.missing.length} dropped (${res.missing.join(', ')})` : ''));
    this.emit('reloaded', info);
    this.emit('change');
    return info;
  }

  // The Blender world as fill light, and a sky dome when the scene asks for one (manifest.sky)
  setSky(mode) {                // 'day' | 'night' | 'scene' (the manifest's own sky); remembered per scene on this headset
    this.skyMode = mode;
    // remembered for a while only: the scene's own sky (night) is the default (the user, 10-03: "night should be the default")
    try { localStorage.setItem('vr_sky:' + this.sceneName, JSON.stringify({ mode, until: Date.now() + 20 * 60e3 })); } catch (_) { /* private mode */ }
    if (this.manifest) this._world(this.manifest);
    return { sky: mode === 'scene' ? this.manifest && this.manifest.sky : mode };
  }

  _world(man) {
    if (!this._fill) { this._fill = new THREE.AmbientLight(0xffffff, 0); this.scene.add(this._fill); }
    const w = man.world;
    if (w) { this._fill.color.setRGB(...w.color, THREE.LinearSRGBColorSpace); this._fill.intensity = w.strength * Math.PI * 0.5; }
    else this._fill.intensity = 0;
    if (this._sky) { this.scene.remove(this._sky); this._sky.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); }); this._sky = null; }
    let mode = this.skyMode;
    if (mode === undefined) {
      try { const s = JSON.parse(localStorage.getItem('vr_sky:' + this.sceneName) || 'null'); mode = s && s.until > Date.now() ? s.mode : 'scene'; } catch (_) { mode = 'scene'; }
    }
    const sky = mode && mode !== 'scene' ? mode : man.sky;
    if (sky === 'night') this._sky = nightSky();
    else if (sky === 'day') this._sky = daySky();
    if (!this._skyLights) this._skyLights = skyLights(this.scene);
    this._skyLights.set(sky === 'day');
    if (this._sky) this.scene.add(this._sky);
    if (this.scene.background && this.scene.background.isColor) this.scene.background.setRGB(...(sky === 'day' ? [0.3, 0.45, 0.7] : [0.003, 0.003, 0.0035]), THREE.LinearSRGBColorSpace);
  }

  _install(man, gltf) {                                // the root is set up here, then added piece by piece (stage)
    this.manifest = man;
    this._world(man);
    gltf.scene.updateMatrixWorld(true);

    gltf.scene.traverse((o) => {
      if (o.isMesh) {
        o.castShadow = o.receiveShadow = true;
        for (const m of [o.material].flat()) {
          cutout(m);
          if (QUEST_UA) questMaterial(m);
          if (!this.materials.has(m.name)) this.materials.set(m.name, { mats: [], color0: m.color.clone() });
          const e = this.materials.get(m.name);
          if (!e.mats.includes(m)) e.mats.push(m);
        }
      }
    });

    // every glTF node that is a Blender object becomes an item, at any depth (groups: an empty with children).
    // items are in depth-first order, so a parent always comes before its children.
    const assoc = gltf.parser.associations;
    const collect = (o, parent) => {
      for (const obj of [...o.children]) {
        const a = assoc.get(obj), name = obj.userData.name || obj.name;
        let it = null;
        if (!obj.isBone && a && a.nodes !== undefined && name) {
          const mo = man.objects[name];
          if (!mo) console.warn('not in manifest:', name);
          else if (this.byName.has(name)) console.warn('duplicate node name:', name);
          else {
            it = { name, type: mo.type, aimed: mo.type === 'LIGHT' || mo.type === 'CAMERA', obj, man: mo,
              parent, children: [], depth: parent ? parent.depth + 1 : 0,
              base: { pos: obj.position.clone(), quat: obj.quaternion.clone(), scale: obj.scale.clone() } };
            obj.userData.item = it;
            if (parent) parent.children.push(it);
            this.items.push(it);
            this.byName.set(name, it);
          }
        }
        collect(obj, it || parent);
      }
    };
    collect(gltf.scene, null);
    for (const it of this.items) {
      it.top = it.parent ? it.parent.top : it;
      it.path = it.parent ? [...it.parent.path, it] : [it];
    }
    // "big" = floor, walls, or a group holding them: never picked by a click (the object list still has them)
    const own = new Map(this.items.map((it) => [it, new THREE.Box3()]));
    const bb = new THREE.Box3();
    gltf.scene.traverse((o) => {
      if (!o.isMesh) return;
      const it = this.itemOf(o);
      if (!it) return;
      if (!o.geometry.boundingBox) o.geometry.computeBoundingBox();
      own.get(it).union(bb.copy(o.geometry.boundingBox).applyMatrix4(o.matrixWorld));
    });
    const half = (b) => (b.isEmpty() ? 0 : b.getSize(new THREE.Vector3()).length() / 2);
    for (const it of [...this.items].reverse()) {       // children first
      it.box = own.get(it).clone();
      for (const c of it.children) it.box.union(c.box);
      // over 4 m across is building (walls, floors); a pool table (2.6 m long) is furniture
      it.big = half(own.get(it)) > 2.0 || it.children.some((c) => c.big) || (it.children.length > 0 && half(it.box) > 2.5);
    }
    for (const it of this.items) {
      if (it.type === 'LIGHT') this._setupLight(it, man.lights[it.name]);
      if (it.type === 'CAMERA') this._setupCamera(it);
    }
    // shadows from the two strongest point/spot lights only: each casts six shadow renders a frame, too many for a Quest
    const casters = this.items.filter((it) => it.light && it.light.castShadow && (it.light.isPointLight || it.light.isSpotLight))
      .sort((a, b) => b.light.intensity - a.light.intensity);
    // on the headset (the user, the built club: "a bit laggy") one caster at 512 px, and small things (bottles,
    // glasses, caps) cast none; the desktop keeps two at 1024
    const QUEST = QUEST_UA;
    for (const it of casters.slice(QUEST ? 1 : 2)) it.light.castShadow = false;
    if (QUEST) {
      for (const it of casters.slice(0, 1)) it.light.shadow.mapSize.set(512, 512);
      const sz = new THREE.Vector3();
      gltf.scene.traverse((o) => {
        if (o.isMesh && new THREE.Box3().setFromObject(o).getSize(sz).length() < 0.6) o.castShadow = false;
      });
    }
    this.root = gltf.scene;
    this.pickRoots = [gltf.scene];
  }

  _setupLight(it, ml) {
    let light = it.obj.isLight ? it.obj : null;
    if (ml.kind === 'AREA') {                       // glTF has no area lights: rebuild it from the manifest
      const w = ml.size || 0.1, h = ml.size_y || w;
      light = new THREE.RectAreaLight(lin(ml.color), 1, w, h);
      light.power = ml.energy;                      // Blender W, same units as the punctual lights below
      it.obj.add(light);
    } else if (light) {
      light.intensity /= W2LM;
      if (ml.energy >= 10 && (light.isPointLight || light.isSpotLight)) {
        light.castShadow = true;
        light.shadow.mapSize.set(1024, 1024);
        light.shadow.camera.near = 0.05;
        light.shadow.bias = -0.0004;
        light.shadow.normalBias = 0.004;
      }
    }
    if (!light) return;
    it.light = light;
    it.kind = ml.kind;
    it.intensity0 = light.intensity;
    it.color0 = light.color.clone();

    const g = new THREE.Group();
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.025, 16, 12),
      new THREE.MeshBasicMaterial({ color: light.color, toneMapped: false }));
    g.add(ball);
    if (ml.kind !== 'POINT') {
      g.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -0.25)]),
        new THREE.LineBasicMaterial({ color: 0xffffff, toneMapped: false })));
    }
    if (ml.kind === 'AREA') {
      const w = light.width / 2, h = light.height / 2;
      g.add(new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(
        [[-w, -h], [w, -h], [w, h], [-w, h]].map(([x, y]) => new THREE.Vector3(x, y, 0))),
        new THREE.LineBasicMaterial({ color: 0xffffff, toneMapped: false })));
    }
    it.handle = ball;
    this._gizmo(it, g);
  }

  _setupCamera(it) {
    const cam = it.obj;
    cam.aspect = 16 / 9;
    cam.updateProjectionMatrix();
    const d = 0.15, hh = d * Math.tan(THREE.MathUtils.degToRad(cam.fov) / 2), hw = hh * cam.aspect;
    const c = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]].map(([x, y]) => new THREE.Vector3(x, y, -d));
    const o = new THREE.Vector3();
    const pts = [o, c[0], o, c[1], o, c[2], o, c[3], c[0], c[1], c[1], c[2], c[2], c[3], c[3], c[0],
      new THREE.Vector3(-hw * 0.4, hh * 1.1, -d), new THREE.Vector3(0, hh * 1.5, -d),
      new THREE.Vector3(0, hh * 1.5, -d), new THREE.Vector3(hw * 0.4, hh * 1.1, -d)];
    const g = new THREE.Group();
    g.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: 0x66ccff, toneMapped: false })));
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.05, 0.04, 0.07),
      new THREE.MeshBasicMaterial({ color: 0x1d4f66, toneMapped: false }));
    body.position.z = 0.035;
    g.add(body);
    // what the VR ray and hands catch: an unseen box round the whole frustum (the 7 cm body alone was too small to
    // point at; the user, 2026-10-02: "can't really interact with cameras... by pointing at them or pinching them")
    const grab = new THREE.Mesh(new THREE.BoxGeometry(2 * hw + 0.08, 2 * hh * 1.5 + 0.08, d + 0.12), new THREE.MeshBasicMaterial({ visible: false }));
    grab.position.z = -d / 2 + 0.03;
    grab.userData.pickProxy = true;
    g.add(grab);
    it.handle = body;
    this._gizmo(it, g);
  }

  _gizmo(it, g) {
    g.traverse((o) => { o.layers.set(GIZMO); o.castShadow = o.receiveShadow = false; });
    g.userData.gizmo = true;
    it.gizmo = g;
    it.obj.add(g);
  }

  itemOf(o) {
    while (o && !o.userData.item) o = o.parent;
    return o ? o.userData.item : null;
  }

  // ---- picking with groups. The first hit on a mesh that is not big gives the leaf item; a click selects the
  // highest non-big ancestor (the group), a second click on the selected group drills one level down toward the
  // leaf, a click on a sibling of a drilled-in selection selects that sibling, and deep (Alt) goes to the leaf.
  pickLeaf(hits) {
    for (const h of hits) {
      if (!h.object.isMesh || !h.object.visible) continue;
      const it = this.itemOf(h.object);
      if (it && !it.big) return { it, hit: h };
    }
    return null;
  }
  resolvePick(leaf, deep) {
    if (!leaf || deep) return leaf;
    const path = leaf.path.filter((x) => !x.big), s = this.selected;
    if (s && path.length) {
      const sp = s.path.filter((x) => !x.big), d = sp.length - 1;
      if (d >= 0 && d < path.length && (d === 0 || sp[d - 1] === path[d - 1])) {
        if (path[d] === s) return path[Math.min(d + 1, path.length - 1)];
        if (d > 0) return path[d];
      }
    }
    return path[0] || leaf;
  }

  emit(type, extra = {}) { this.dispatchEvent({ type, ...extra }); }
  canMove(it) { return !this.renderer.xr.isPresenting || it === this.unlocked; }
  // the names the user and Claude call things by (scenes/<scene>/names.json): a shared lexicon, shown in menus and labels
  label(it) { const n = it && (this.names || {})[it.name]; return n ? n : (it ? it.name : ''); }
  setStatus(s) { this.status = s; this.emit('status'); console.log('[vr]', s); }

  select(it, via = 'user') {
    if (typeof it === 'string') it = this.byName.get(it) || null;
    const prev = this.selected;
    this.selected = it;
    if (this.unlocked && this.unlocked !== it) this.unlocked = null;
    this.outline.visible = !!it;
    if (it) this.outline.setFromObject(it.obj);
    this.emit('select', { prev, via });
  }

  // ---- lights and materials
  // watts to the light's intensity: its own ratio, or for a light saved off (0 W, no ratio of its own), an area light's
  // size, else the ratio of the other lights of its kind in the scene (the user, 2026-10-08: two house lights saved at
  // 0 W, set to 300 and 900 W, divided by zero, and the whole room went black)
  perW(it) {
    if (it.perW) return it.perW;
    const ratio = (o) => { const e = this.manifest.lights[o.name].energy; return e > 0 && o.intensity0 > 0 ? o.intensity0 / e : null; };
    let k = ratio(it);
    if (k == null && it.light.isRectAreaLight) k = 1 / (it.light.width * it.light.height * Math.PI);
    if (k == null) {
      const ks = this.items.filter((o) => o !== it && o.light && o.kind === it.kind).map(ratio).filter((x) => x).sort((x, y) => x - y);
      k = ks.length ? ks[ks.length >> 1] : null;
    }
    return (it.perW = k || 1);
  }
  energy(it) { return it.light.intensity / this.perW(it); }
  setEnergy(it, w) {
    if (!Number.isFinite(+w) || +w < 0) throw new Error('energy: watts, a number 0 or more');
    it.light.intensity = this.perW(it) * +w; this.emit('change');
    if (it.lightHome) Object.assign(it.lightHome, { energy: +w, changed: true });   // an edit is what the build keeps
  }
  setLightColor(it, color) {
    it.light.color.copy(color);
    it.handle.material.color.copy(color);
    this.emit('change');
    if (it.lightHome) Object.assign(it.lightHome, { color: rgbOf(color), changed: true });
  }
  // ---- run time (behaviours.js): what a thing does when pressed is not an edit. The first run-time change keeps the
  // edited state (it.lightHome, userData.behaviourHome) and computeEdits saves that, never the switch of the moment
  runLight(it, w, color) {
    if (!it.lightHome) it.lightHome = { changed: this.lightChanged(it), energy: this.energy(it), color: rgbOf(it.light.color) };
    if (w !== undefined && w !== null) it.light.intensity = this.perW(it) * Math.max(0, +w);
    if (color) { it.light.color.copy(lin(color)); it.handle.material.color.copy(it.light.color); }
    this.emit('change');
  }
  runHome(it) {
    if (!it.obj.userData.behaviourHome) it.obj.userData.behaviourHome = { changed: this.changed(it), t: this.blenderTransform(it) };
  }
  materialsOf(it) {
    const names = new Set();
    it.obj.traverse((o) => { if (o.isMesh && !o.layers.isEnabled(GIZMO)) [o.material].flat().forEach((m) => names.add(m.name)); });
    return [...names].filter((n) => this.materials.has(n));
  }
  setMaterialColor(name, color) {
    for (const m of this.materials.get(name).mats) m.color.copy(color);
    this.emit('change');
  }

  // ---- undo: whole-scene snapshots; cheap at this size and impossible to get out of sync
  snapshot() {
    return {
      items: this.items.map((it) => [it.obj.position.clone(), it.obj.quaternion.clone(), it.obj.scale.clone(),
        it.light ? it.light.intensity : 0, it.light ? it.light.color.clone() : null]),
      mats: [...this.materials.values()].map((e) => e.mats[0].color.clone()),
    };
  }
  restore(s) {
    this.items.forEach((it, i) => {
      const [p, q, sc, inten, col] = s.items[i];
      it.obj.position.copy(p); it.obj.quaternion.copy(q); it.obj.scale.copy(sc);
      if (it.light) { it.light.intensity = inten; it.light.color.copy(col); it.handle.material.color.copy(col); }
    });
    [...this.materials.values()].forEach((e, i) => e.mats.forEach((m) => m.color.copy(s.mats[i])));
    this.emit('change');
  }
  // what differs between two snapshots, in Blender world space: moved items (local transform changed; their
  // children are carried along and not listed), lights, materials
  diff(a, b, worldA) {
    const moved = [], lights = [], materials = [];
    const mats = [...this.materials.entries()];
    this.items.forEach((it, i) => {
      const [p, q, s, li, lc] = a.items[i], [p2, q2, s2, li2, lc2] = b.items[i];
      const qa = q.toArray(), qb = q2.toArray();
      const dq = Math.min(Math.max(...qa.map((v, k) => Math.abs(v - qb[k]))), Math.max(...qa.map((v, k) => Math.abs(v + qb[k]))));
      if (p.distanceTo(p2) > 1e-7 || dq > 1e-7 || s.distanceTo(s2) > 1e-7) moved.push({ it, before: worldA ? worldA.get(it) : null });
      if (it.light && (Math.abs(li - li2) > 1e-9 * Math.max(1, li) || !lc.equals(lc2))) {
        lights.push({ it, before: { energy: li / this.perW(it), color: rgbOf(lc) } });
      }
    });
    mats.forEach(([name], i) => { if (!a.mats[i].equals(b.mats[i])) materials.push({ name, before: rgbOf(a.mats[i]) }); });
    return { moved, lights, materials };
  }
  worldAll() { return new Map(this.items.map((it) => [it, this.blenderTransform(it)])); }

  beginEdit(via) {
    if (this.editing) return;
    this.editing = true;
    this.editVia = via || (this.renderer.xr.isPresenting ? 'xr' : 'user');
    this.undoStack.push(this.snapshot());
    this._editWorld = this.worldAll();
    if (this.undoStack.length > 200) this.undoStack.shift();
  }
  endEdit() {
    if (!this.editing) return this.emit('change');
    this.editing = false;
    const before = this.undoStack[this.undoStack.length - 1];
    if (before) {
      const d = this.diff(before, this.snapshot(), this._editWorld);
      if (d.moved.length || d.lights.length || d.materials.length) this.emit('edited', { ...d, via: this.editVia });
    }
    this.emit('change');
  }
  // ---- drop: not a physics engine, the one thing physics was wanted for. The item stands back up (its tilt goes back
  // to how Blender had it, its heading stays), then falls under gravity onto whatever is under it (the highest surface
  // under its footprint) and lands there. One undoable edit. Returns the landing height, or null if nothing is below.
  drop(it, via) {
    if (typeof it === 'string') it = this.byName.get(it) || null;
    if (!it || it.light || it.aimed || it.big) return null;
    const o = it.obj;
    o.updateWorldMatrix(true, true);
    const qCur = new THREE.Quaternion(), pCur = new THREE.Vector3(), sCur = new THREE.Vector3();
    o.matrixWorld.decompose(pCur, qCur, sCur);
    const qParent = new THREE.Quaternion();
    if (o.parent) o.parent.getWorldQuaternion(qParent);
    const qBase = qParent.clone().multiply(it.base.quat);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(qCur.clone().multiply(qBase.clone().invert()));
    const qUp = new THREE.Quaternion().setFromUnitVectors(up, new THREE.Vector3(0, 1, 0)).multiply(qCur);
    this.beginEdit(via || 'drop');
    o.quaternion.copy(qParent.clone().invert().multiply(qUp));
    o.updateWorldMatrix(true, true);
    const box = new THREE.Box3().setFromObject(o, true);
    const own = new Set();
    o.traverse((x) => own.add(x));
    const solid = [];
    this.scene.traverse((x) => {
      if (x.isMesh && x.visible && !own.has(x) && !x.userData.gizmo && x.layers.isEnabled(0) && !(x.material && x.material.transparent && x.material.opacity < 0.5)) solid.push(x);
    });
    // a rigid fall: over a grid of columns through the footprint, find the item's own lowest surface in that column (a
    // ray up into it) and what is under that (a ray down, from 10 cm up so a sunk item comes back out). It moves by the
    // smallest gap, so a stool lands on its base, not on the foot rail under the edge of its seat.
    const rc = new THREE.Raycaster(), down = new THREE.Vector3(0, -1, 0), upv = new THREE.Vector3(0, 1, 0);
    const ownMeshes = [...own].filter((x) => x.isMesh);
    let fall = Infinity, top = null;
    const N = 8;
    for (let i = 0; i <= N; i++) {
      for (let j = 0; j <= N; j++) {
        const x = THREE.MathUtils.lerp(box.min.x, box.max.x, (i + 0.5) / (N + 1)), z = THREE.MathUtils.lerp(box.min.z, box.max.z, (j + 0.5) / (N + 1));
        rc.set(new THREE.Vector3(x, box.min.y - 0.01, z), upv); rc.far = box.max.y - box.min.y + 0.02;
        const mine = rc.intersectObjects(ownMeshes, false)[0];
        if (!mine) continue;
        const bottom = mine.point.y;
        rc.set(new THREE.Vector3(x, bottom + 0.1, z), down); rc.far = 60;
        const under = rc.intersectObjects(solid, false)[0];
        if (under && bottom - under.point.y < fall) { fall = bottom - under.point.y; top = under.point.y; }
      }
    }
    if (top === null) {                                  // one-sided faces the up-rays cannot see: the box's bottom
      const c = box.getCenter(new THREE.Vector3());
      rc.set(new THREE.Vector3(c.x, box.min.y + 0.1, c.z), down); rc.far = 60;
      const under = rc.intersectObjects(solid, false)[0];
      if (under) { top = under.point.y; fall = box.min.y - top; }
    }
    if (top === null) { this.endEdit(); this.setStatus(`drop: nothing under ${it.name}`); return null; }
    const t0 = performance.now();
    const T = fall > 0 ? Math.sqrt(2 * fall / 9.81) * 1000 : 150;
    const step = () => {
      const k = Math.min(1, (performance.now() - t0) / T);
      const d = fall > 0 ? fall * k * k : fall * k;        // gravity: distance grows with t squared
      const wp = new THREE.Vector3(pCur.x, pCur.y - d, pCur.z);
      if (o.parent) o.parent.worldToLocal(wp);
      o.position.copy(wp);
      o.updateMatrixWorld(true);
      if (this.selected === it) this.outline.setFromObject(o);
      if (k >= 1) {
        this.preRender.splice(this.preRender.indexOf(step), 1);
        this.endEdit();
        this.emit('dropped', { it, fall });
        this.setStatus(`dropped ${it.name} ${fall >= 0 ? 'down' : 'up'} ${Math.abs(fall * 100).toFixed(0)} cm`);
      } else this.emit('change');
    };
    this.preRender.push(step);
    return top;
  }
  undo() {
    if (!this.undoStack.length) return this.setStatus('nothing to undo');
    this.editing = false;
    const now = this.snapshot(), world = this.worldAll(), s = this.undoStack.pop();
    this.restore(s);
    const d = this.diff(now, s, world);
    this.emit('undone', { ...d, depth: this.undoStack.length });
    this.setStatus('undone');
  }
  dirty() { return this.loaded && JSON.stringify(this.computeEdits()) !== this.savedJSON; }   // (never save a half-loaded page)

  // ---- edits.json in Blender world space
  changed(it) {      // component-wise: Quaternion.angleTo is acos-noisy (~7e-4 rad) for identical inputs
    const o = it.obj, b = it.base, q = o.quaternion.toArray(), q0 = b.quat.toArray();
    const dq = Math.min(Math.max(...q.map((v, i) => Math.abs(v - q0[i]))), Math.max(...q.map((v, i) => Math.abs(v + q0[i]))));
    return o.position.distanceTo(b.pos) > 1e-7 || dq > 1e-7 || o.scale.distanceTo(b.scale) > 1e-7;
  }
  lightChanged(it) {
    return Math.abs(it.light.intensity - it.intensity0) > 1e-6 * Math.max(1e-6, it.intensity0) ||
      Math.max(...['r', 'g', 'b'].map((k) => Math.abs(it.light.color[k] - it.color0[k]))) > 1e-6;
  }
  blenderTransform(it) {
    const o = it.obj;
    o.updateWorldMatrix(true, false);              // parents too: a group may have moved this frame
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    o.matrixWorld.decompose(p, q, s);
    return { location: t2bPos(p), quaternion: t2bQuat(q, it.aimed), scale: t2bScale(s, it.aimed) };
  }
  // set an object's WORLD transform from Blender-space values (missing parts keep their current world value)
  setBlenderWorld(it, t) {
    const cur = this.blenderTransform(it), o = it.obj;
    const m = new THREE.Matrix4().compose(b2tPos(t.location || cur.location), b2tQuat(t.quaternion || cur.quaternion, it.aimed),
      b2tScale(t.scale || cur.scale, it.aimed));
    if (o.parent) {
      o.parent.updateWorldMatrix(true, false);
      m.premultiply(new THREE.Matrix4().copy(o.parent.matrixWorld).invert());
    }
    m.decompose(o.position, o.quaternion, o.scale);
    o.updateMatrixWorld(true);
  }
  computeEdits() {
    const out = { objects: {}, lights: {}, materials: {} };
    for (const it of this.items) {
      // pinned to a hand (anchor.js), or moved on trial by an agent (live.js set trial): saved as it was before
      const home = it.obj.userData.anchorHome || it.obj.userData.trialHome || it.obj.userData.behaviourHome;
      if (home ? home.changed : this.changed(it)) {
        const t = home ? home.t : this.blenderTransform(it);
        out.objects[it.name] = { location: r7(t.location), quaternion: r7(t.quaternion) };
        if (!it.aimed) out.objects[it.name].scale = r7(t.scale);
      }
      const lh = it.light && it.lightHome;                       // a light a behaviour has set: as it was edited
      if (lh ? lh.changed : it.light && this.lightChanged(it)) {
        out.lights[it.name] = lh ? { energy: r7([lh.energy])[0], color: r7(lh.color) } : { energy: r7([this.energy(it)])[0], color: r7(rgbOf(it.light.color)) };
      }
    }
    for (const [name, e] of this.materials) {
      const c = e.mats[0].color;
      if (Math.max(...['r', 'g', 'b'].map((k) => Math.abs(c[k] - e.color0[k]))) > 1e-6) {
        out.materials[name] = { base_color: r7(rgbOf(c)) };
      }
    }
    return out;
  }
  // returns {applied, missing}: the names it set and the names this scene does not have
  applyEdits(ed, quiet = false) {
    const objs = ed.objects || {}, applied = [], missing = [];
    for (const name of Object.keys(objs)) if (!this.byName.has(name)) { console.warn('edits: no object', name); missing.push(name); }
    for (const it of this.items) {                 // parents before children: values are world transforms
      const t = objs[it.name];
      if (!t) continue;
      if (!it.parent) {                            // top level: exact, as in v0
        if (t.location) it.obj.position.copy(b2tPos(t.location));
        if (t.quaternion) it.obj.quaternion.copy(b2tQuat(t.quaternion, it.aimed));
        if (t.scale) it.obj.scale.copy(b2tScale(t.scale, it.aimed));
      } else this.setBlenderWorld(it, t);
      applied.push(it.name);
    }
    for (const [name, t] of Object.entries(ed.lights || {})) {
      const it = this.byName.get(name);
      if (!it || !it.light) { missing.push(name); continue; }
      if (t.energy !== undefined) this.setEnergy(it, t.energy);
      if (t.color) this.setLightColor(it, lin(t.color));
      applied.push(name);
    }
    for (const [name, t] of Object.entries(ed.materials || {})) {
      if (this.materials.has(name) && t.base_color) { this.setMaterialColor(name, lin(t.base_color)); applied.push(name); }
      else missing.push(name);
    }
    const n = Object.values(ed).reduce((a, v) => a + Object.keys(v || {}).length, 0);
    if (!quiet) this.setStatus(`applied ${n} saved edits from edits.json`);
    return { applied: [...new Set(applied)], missing: [...new Set(missing)] };
  }
  async save() {
    if (!this.loaded) throw new Error('the scene is still loading: nothing to save');
    const edits = this.computeEdits();
    // what was edited at the last save or load and is back to the export now: the server drops it from edits.json
    const before = JSON.parse(this.savedJSON || '{}'), reverted = {};
    for (const k of ['objects', 'lights', 'materials']) {
      const gone = Object.keys(before[k] || {}).filter((n) => !(n in (edits[k] || {})));
      if (gone.length) reverted[k] = gone;
    }
    try {
      const r = await fetch(`save?scene=${encodeURIComponent(this.sceneName)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...edits, reverted }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || r.status);
      const c = j.counts;
      this.savedJSON = JSON.stringify(edits);
      this.setStatus(`saved ${c.objects} objects, ${c.lights} lights, ${c.materials} materials` + (j.previous ? ' (previous kept in history/)' : '') +
        (j.server_stale ? '. WARNING: the server runs old code, restart server.py' : ''));
      this.emit('saved', { edits, result: j });
      return { edits, result: j };
    } catch (e) {
      this.setStatus('SAVE FAILED: ' + e.message);
      throw e;
    }
  }

  // Every unchanged object's three.js world transform, taken back to Blender space, must equal the manifest.
  selftest() {
    const fails = [], byType = {};
    let checked = 0;
    for (const it of this.items) {
      if (it.path.some((x) => this.changed(x))) continue;     // moved itself, or carried by a moved group
      const t = this.blenderTransform(it), m = it.man;
      const dl = Math.max(...t.location.map((v, i) => Math.abs(v - m.location[i])));
      const dq = Math.min(Math.max(...t.quaternion.map((v, i) => Math.abs(v - m.quaternion[i]))),
        Math.max(...t.quaternion.map((v, i) => Math.abs(v + m.quaternion[i]))));
      const ok = dl < 1e-4 && dq < 1e-3;
      checked++;
      byType[it.type] = byType[it.type] || { ok: 0, fail: 0 };
      byType[it.type][ok ? 'ok' : 'fail']++;
      if (!ok) fails.push({ name: it.name, type: it.type, dLoc: dl, dQuat: dq, got: t, want: m });
    }
    const res = { pass: fails.length === 0 && checked > 0, checked, byType, fails };
    console.log('[vr] selftest', res.pass ? 'PASS' : 'FAIL', JSON.stringify({ checked, byType }), fails);
    return res;
  }
}
