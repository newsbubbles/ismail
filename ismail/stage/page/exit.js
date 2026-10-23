// leave VR without reloading: both thumbs up, held. A card fills in front of the eyes; letting go cancels.
// The page stays as it is (scene, edits, the live link), so the user can record or talk outside, then enter again.
import * as THREE from 'three';

const HOLD_MS = 1500;

export function initExit(ed, hands, live) {
  const cv = document.createElement('canvas'); cv.width = 512; cv.height = 128;
  const tex = new THREE.CanvasTexture(cv);
  const card = new THREE.Mesh(new THREE.PlaneGeometry(0.32, 0.08),
    new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthTest: false }));
  card.renderOrder = 999; card.visible = false; card.layers.enableAll();
  ed.scene.add(card);
  let since = 0, done = false;
  const pos = new THREE.Vector3(), fwd = new THREE.Vector3();

  function draw(k) {
    const g = cv.getContext('2d');
    g.clearRect(0, 0, 512, 128);
    g.fillStyle = 'rgba(30,30,40,0.9)'; g.beginPath(); g.roundRect(2, 2, 508, 124, 22); g.fill();
    g.fillStyle = 'rgba(96,165,250,0.9)'; g.beginPath(); g.roundRect(2, 2, 508 * k, 124, 22); g.fill();
    g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.font = 'bold 40px system-ui, sans-serif'; g.fillText('Exit VR', 256, 44);
    g.font = '28px system-ui, sans-serif'; g.fillText('keep both thumbs up', 256, 92);
    tex.needsUpdate = true;
  }

  function update() {
    const G = hands.gestures;                           // gestures.js: vr_exit is a locked row (both thumbs up)
    const both = !hands.performing && !!G.on('vr_exit');   // perform.js: gestures off
    if (!both) { since = 0; card.visible = false; return false; }
    const now = performance.now();
    if (!since) since = now;
    const k = Math.min(1, (now - since) / G.hold('vr_exit', HOLD_MS));
    ed.camera.getWorldPosition(pos); ed.camera.getWorldDirection(fwd);
    card.position.copy(pos).addScaledVector(fwd, 0.6); card.lookAt(pos);
    card.visible = true; draw(k);
    if (k >= 1 && !done) {
      done = true; card.visible = false;
      live.emit('vr_exit', { via: 'both thumbs up' });
      const s = ed.renderer.xr.getSession();
      if (s) s.end().catch(() => {});
      setTimeout(() => { done = false; since = 0; }, 2000);
    }
    return true;   // both thumbs up: the one-handed gestures (reload, panel answers) wait
  }
  return { update, get active() { return since > 0; } };
}
