// The construct: where the page starts, before the scene is here. A pale void with a floor grid and a horizon, a card
// that follows the eyes with how far the scene has come. VR, voice, gestures and the live link work in it from the
// first second; the scene streams in behind it (the manifest first: its sky goes up at once), and when the scene is
// built the construct fades away around the user. The user (2026-10-02): "start out in a black, grey, white space...
// like that loading scene of the matrix", so a stalled download is never a dead end.
import * as THREE from 'three';

const FADE_MS = 1800;

export function initConstruct(ed) {
  const g = new THREE.Group();
  g.name = '_construct';
  const opacity = { value: 1 }, dim = { value: 1 };     // dim: the void's brightness (a scene swap closes it dusky)

  const dome = new THREE.Mesh(new THREE.SphereGeometry(60, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, transparent: true, toneMapped: false, uniforms: { opacity, dim },
    vertexShader: 'varying vec3 vDir; void main() { vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `uniform float opacity; uniform float dim; varying vec3 vDir;
      void main() {
        float h = vDir.y;
        vec3 c = mix(vec3(0.80, 0.80, 0.82), vec3(0.93, 0.93, 0.95), smoothstep(0.0, 0.6, h));
        c = mix(c, vec3(0.66, 0.66, 0.68), smoothstep(0.0, -0.25, h));
        c -= vec3(0.06) * exp(-abs(h) * 40.0);                       // a faint horizon line
        gl_FragColor = vec4(c * dim, opacity);
      }`,
  }));
  // drawn after the night sky (editor.js: dome -10, stars -9, clouds -8; the manifest puts it up before the room comes),
  // or the clouds paint dark over the void (the user's headset, 2026-10-03: "everything's dark")
  const BACK = { dome: -7, floor: -6, card: -5 };
  dome.renderOrder = BACK.dome;
  g.add(dome);

  const floor = new THREE.Mesh(new THREE.CircleGeometry(40, 64).rotateX(-Math.PI / 2), new THREE.ShaderMaterial({
    depthWrite: false, transparent: true, toneMapped: false, uniforms: { opacity },
    vertexShader: 'varying vec3 vP; void main() { vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
    fragmentShader: `uniform float opacity; varying vec3 vP;
      void main() {
        vec2 q = abs(fract(vP.xz + 0.5) - 0.5) / fwidth(vP.xz);
        float line = 1.0 - min(min(q.x, q.y), 1.0);
        float r = length(vP.xz);
        float a = (0.10 + 0.35 * line) * exp(-r * 0.09);
        gl_FragColor = vec4(vec3(0.45, 0.46, 0.5), a * opacity);
      }`,
  }));
  floor.renderOrder = BACK.floor;
  g.add(floor);

  const hemi = new THREE.HemisphereLight(0xffffff, 0x9a9aa0, 2.2);  // the hands are lit materials: light them here
  // never removed, only dimmed: the number of lights is part of every lit shader, so a light that comes and goes makes
  // the whole room recompile (a stall in the headset at the end of every load)
  ed.scene.add(hemi);

  // the card: what is loading and how far it has come, 1.6 m ahead, easing after the head
  const cv = document.createElement('canvas'); cv.width = 1024; cv.height = 320;
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const card = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 0.25), new THREE.MeshBasicMaterial({ map: tex, transparent: true, toneMapped: false, depthWrite: false }));
  card.renderOrder = BACK.card;
  card.layers.enableAll();
  g.add(card);
  const st = { title: ed.sceneName, line: 'connecting', k: 0, hint: 'talk to me any time: the phone gesture' };
  function draw() {
    const c = cv.getContext('2d');
    c.clearRect(0, 0, 1024, 320);
    c.fillStyle = 'rgba(250,250,252,0.92)'; c.beginPath(); c.roundRect(4, 4, 1016, 312, 36); c.fill();
    c.fillStyle = '#222'; c.textAlign = 'left'; c.textBaseline = 'middle';
    c.font = 'bold 64px system-ui, sans-serif'; c.fillText(st.title, 56, 80);
    c.fillStyle = 'rgba(0,0,0,0.08)'; c.beginPath(); c.roundRect(56, 140, 912, 22, 11); c.fill();
    c.fillStyle = '#3b82f6'; c.beginPath(); c.roundRect(56, 140, Math.max(22, 912 * st.k), 22, 11); c.fill();
    c.fillStyle = '#333'; c.font = '38px system-ui, sans-serif'; c.fillText(st.line, 56, 210);
    c.fillStyle = '#777'; c.font = '30px system-ui, sans-serif'; c.fillText(st.hint, 56, 272);
    tex.needsUpdate = true;
  }
  draw();
  ed.scene.add(g);

  function layer(onTop) {                                     // on top of the room while closing over it, behind after
    dome.material.depthTest = card.material.depthTest = !onTop;
    dome.renderOrder = onTop ? 9990 : BACK.dome;
    card.renderOrder = onTop ? 9991 : BACK.card;
  }
  const head = new THREE.Vector3(), fwd = new THREE.Vector3(), want = new THREE.Vector3();
  let placed = false, fadeFrom = 0, gone = false, inFrom = 0, inDone = null;
  // a scene swap closes the void in slowly and dusky (the user, 2026-10-08: the whole room vanishing at once into the
  // white void was striking; "maybe the fade out could be a little bit longer")
  const IN_MS = 1600, SWAP_DIM = 0.32;
  ed.preRender.push(step);
  function step() {
    if (gone) return;
    ed.camera.getWorldPosition(head); ed.camera.getWorldDirection(fwd);
    fwd.y = 0; if (fwd.lengthSq() < 1e-6) fwd.set(0, 0, -1); fwd.normalize();
    want.copy(head).addScaledVector(fwd, 1.6); want.y = head.y - 0.1;
    if (!placed) { card.position.copy(want); placed = true; } else card.position.lerp(want, 0.04);
    card.lookAt(head);
    dome.position.copy(head); floor.position.set(head.x, ed.rig.position.y, head.z);   // the void is always around the user
    if (inFrom) {                                             // cover(): the void closes back in around the user
      const k = Math.min(1, (performance.now() - inFrom) / IN_MS), e = k * k * (3 - 2 * k);
      opacity.value = e; card.material.opacity = e; hemi.intensity = 2.2 * e * dim.value;
      if (k >= 1) {                                           // closed: the old room goes, the void is a backdrop again
        inFrom = 0;
        if (ed.root) ed.root.visible = false;
        layer(false);
        const d = inDone; inDone = null; if (d) d();
      }
    }
    if (fadeFrom) {
      const k = Math.min(1, (performance.now() - fadeFrom) / FADE_MS);
      opacity.value = 1 - k;
      card.material.opacity = 1 - k;
      hemi.intensity = 2.2 * (1 - k) * dim.value;
      if (k >= 1) {                                           // kept, not disposed: a scene swap brings it back
        gone = true;
        fadeFrom = 0;
        dim.value = 1;
        ed.scene.remove(g);
        ed.preRender.splice(ed.preRender.indexOf(step), 1);
      }
    }
  }

  ed.addEventListener('manifest', (e) => {
    const t = e.man && (e.man.title || e.man.scene);
    if (t) st.title = t;
    st.line = 'the room is on its way'; draw();
  });
  ed.addEventListener('reveal_progress', (e) => {             // the room assembling around the user (reveal.js)
    if (gone) return;
    st.k = e.total ? e.done / e.total : 1;
    st.line = `placing ${e.done} of ${e.total}`;
    draw();
  });
  ed.addEventListener('load_progress', (e) => {
    st.k = e.total ? e.got / e.total : 0;
    st.line = e.stalled ? `stalled at ${(e.got / 1e6).toFixed(1)} MB, asking again (try ${e.n + 1})`
      : `${(e.got / 1e6).toFixed(1)}` + (e.total ? ` of ${(e.total / 1e6).toFixed(1)} MB` : ' MB') + (e.n > 1 ? `  (try ${e.n})` : '');
    draw();
  });
  return {
    building() { st.k = 1; st.line = 'building the room'; draw(); },
    failed(msg) { st.line = msg; draw(); },
    dissolve() { if (!fadeFrom) { inFrom = 0; fadeFrom = performance.now(); } },
    // a scene swap or a reload from inside (scenes.js): the void fades back in over the old scene, resolves when it
    // has closed; dissolve() opens it on the new one
    cover(title, line) {
      st.title = title || st.title; st.line = line || ''; st.k = 0; st.hint = ''; draw();
      fadeFrom = 0;
      layer(true);                                            // over everything while it closes
      dim.value = SWAP_DIM;
      if (gone) { gone = false; ed.scene.add(g); ed.preRender.push(step); opacity.value = 0; card.material.opacity = 0; placed = false; }
      if (opacity.value >= 1) {                               // closed already: a backdrop, never over the hands
        if (ed.root) ed.root.visible = false;
        layer(false);
        return Promise.resolve();
      }
      inFrom = performance.now() - opacity.value * IN_MS;
      return new Promise((res) => { inDone = res; });
    },
    get active() { return !gone; },
  };
}
