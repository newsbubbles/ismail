// The on-air view: the stream's picture (a live talk from the lecture hall, hq D-203). Opened as ?onair (or
// ?onair=<camera>), the page shows only what the camera on air sees, full frame, with no editor, panels, buttons or
// badges, and draws every frame at a steady rate (?fps=, default 30) instead of waking on movement, so a capture of
// its window (the stream's window mode) gets a moving picture at the stream's rate and the GPU no more frames than
// that. It follows the cuts: stage_on_air sends the page command on_air {camera}; pages that are not on air answer
// {ignored} and keep their view (the headset's page is never moved by a cut). The pointer layer (CAM_ONLY) is drawn,
// as on every camera surface. The window is titled 'ismail on air <scene>', for the capture to find.
import { CAM_ONLY } from './editor.js';

const Q = new URLSearchParams(location.search);
export const ONAIR = Q.has('onair');
export const ONAIR_FPS = Math.max(5, Math.min(60, +Q.get('fps') || 30));

export function initOnAir(ed, desktop, live) {
  if (!ONAIR) {
    live.handlers.on_air = () => ({ ignored: 'not an on-air page' });
    return null;
  }
  document.body.classList.add('onair');
  // the window's title is what the stream captures it by (stream_visual window=): 'ismail on air <scene>'
  document.title = 'ismail on air' + (Q.get('scene') ? ' ' + Q.get('scene') : '');
  const st = document.createElement('style');
  st.textContent = 'body.onair > :not(canvas) { display: none !important; } body.onair { cursor: none; background: #000; }';
  document.head.appendChild(st);
  let want = Q.get('onair') || null, on = null, timer = null;
  const firstCamera = () => (ed.items || []).find((it) => it.type === 'CAMERA');
  function cut(name) {
    const it = name ? ed.byName.get(name) : firstCamera();
    if (!it || it.type !== 'CAMERA') return false;
    it.obj.layers.enable(CAM_ONLY);
    desktop.lookThrough(it, 'on_air');
    on = it.name;
    return true;
  }
  // the room may still be loading: try again until the camera is there (a later cut replaces the wish)
  function keepTrying() {
    clearTimeout(timer);
    if (cut(want)) return;
    timer = setTimeout(keepTrying, 500);
  }
  keepTrying();
  // a scene reload or a user's Esc leaves the camera: go back to the one on air
  ed.addEventListener('mode', (e) => { if (e.mode !== 'look-through' && e.via !== 'on_air') keepTrying(); });
  live.handlers.on_air = (c) => {
    const name = c.camera || c.name;
    if (name && !ed.byName.get(name)) throw new Error(`no camera "${name}" in this scene`);
    want = name;
    keepTrying();
    return { on_air: on, fps: ONAIR_FPS };
  };
  return { camera: () => on, fps: ONAIR_FPS };
}
