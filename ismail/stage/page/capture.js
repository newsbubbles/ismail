// Frame-locked capture (stage_capture, capture.py): the page opened as ?capture=1 by a capture's own server draws the
// stage through a camera at song time and sends every frame back. Film, 2026-10-10: the whole video is shot on the
// stage, so frame n must show the song at t0 + n / fps (actors' loops and takes, keyed objects, lights, behaviours),
// a slow frame waits and nothing drops, and no headset is needed.
//   time    performance.now is the capture's: it stands still between frames and steps 1 / fps per frame, so every
//           module that reads it (takes, the stage clock, reveal, pops) runs on the film's time, however slow the frame
//   music   music.now() answers the song time of the frame (takes played at_music follow it); no sound is played
//   clock   the stage clock (keyed objects, tree growth) is seeked every frame: clock_at + (song t - t0)
//   camera  {object} a camera in the scene (keyed or not), {path: {keys, seconds, at}} a story path (cameras.json:
//           pos and look in Blender metres, lens in mm, keys evenly spaced over t 0..1, eased in and out), or
//           {pos, fwd, up, vfov} fixed, all in Blender coordinates; vfov overrides the lens
//   sets    every load set is loaded (the headset unloads some to stay light; the film wants everyone), unless
//           sets: false; setup can unload one ({type: load_set, name, loaded: false})
//   setup   live commands run once before the first frame (actor_play, sky, light, load_set, behaviour presses), the
//           same ones an agent sends, while the room settles in real time (a set comes in paced by the clock); then
//           the clock stops and every take playing starts again at frame 0. The capture's server answers every write
//           "not saved", so nothing reaches the scene files or the live link
// The job comes from GET capture/job; frames go to POST capture/frame?n= (PNG) and the end to POST capture/status.
import * as THREE from 'three';
import { vec, ease } from './interp.js';

export const CAPTURE = new URLSearchParams(location.search).has('capture');

const P = (v) => new THREE.Vector3(v[0], v[2], -v[1]);   // Blender (Z up) to three (Y up)
const SENSOR_MM = 36;                                     // Blender's default sensor, fitted to the width

export async function runCapture(ed, { music, clock, construct, live, actors, sets }) {
  const post = (path, body, json) => fetch(path, { method: 'POST', body: json ? JSON.stringify(body) : body,
    headers: json ? { 'Content-Type': 'application/json' } : {} });
  const errors = [];
  const guard = (what, f) => { try { return f(); } catch (e) { if (errors.length < 20) errors.push(`${what}: ${e.message || e}`); return null; } };
  try {
    const job = await fetch('capture/job', { cache: 'no-store' }).then((r) => r.json());
    const W = job.size[0], H = job.size[1], fps = job.fps, N = Math.max(1, Math.round((job.t1 - job.t0) * fps));
    const r = ed.renderer;
    r.setAnimationLoop(null);                       // the capture draws its own frames from here on
    r.setPixelRatio(1);
    r.setSize(W, H, false);
    const cam = new THREE.PerspectiveCamera(50, W / H, 0.05, 2000);   // layer 0 only: no gizmos, panels or markers
    if (ed.selected) ed.select(null, 'capture');
    const step = () => {
      for (const f of ed.preRender) guard(f.name || 'preRender', f);
      r.shadowMap.needsUpdate = true;
      r.setRenderTarget(null);
      r.render(ed.scene, cam);
    };
    let songT = job.t0;
    music.now = () => ({ playing: true, url: job.audio || 'capture', t: +songT.toFixed(4), duration: 1e6, loop: false });
    live.handlers.music = () => ({ capture: 'no sound in a capture' });
    if (clock) clock.st.playing = false;
    // the room in, the sets loaded and the setup run, in real time (the reveal paces itself on the clock), drawing
    // all the while: every piece revealed and the construct gone
    let busy = true, failed = null;
    (async () => {
      while (ed.staging() || construct.active) await new Promise((res) => setTimeout(res, 50));
      if (sets && job.sets !== false) for (const [n, s] of Object.entries(sets.state())) if (!s.loaded) await sets.load(n);
      for (const c of job.setup || []) {
        const h = live.handlers[c.type];
        if (!h) throw new Error(`setup: no command "${c.type}"`);
        await h({ ...c });
      }
    })().catch((e) => { failed = e; }).finally(() => { busy = false; });
    const until = performance.now() + (job.settle_s || 180) * 1000;
    while ((busy || ed.staging() || construct.active) && !failed && performance.now() < until) {
      place(cam, job, job.t0, W / H, ed);
      step();
      await new Promise((res) => setTimeout(res, 15));
    }
    if (failed) throw failed;
    if (busy || ed.staging() || construct.active) throw new Error('the room did not finish coming in');
    // film time from here: performance.now stands still between frames, and the takes start at frame 0
    const real = performance.now.bind(performance), base = real();
    let vt = base;
    performance.now = () => vt;
    for (const st of actors.playing.values()) if (st.frames) { st.t0 = base; st.i = 0; st.feet = { l: {}, r: {} }; }
    await post('capture/status', { state: 'rendering', frames: N }, true);
    for (let n = 0; n < N; n++) {
      songT = job.t0 + n / fps;
      vt = base + (n * 1000) / fps;
      if (clock && job.clock !== false) guard('clock', () => clock.seek((job.clock_at ?? job.t0) + n / fps));
      place(cam, job, songT, W / H, ed);
      step();
      const blob = await new Promise((res) => r.domElement.toBlob(res, 'image/png'));   // in the task that drew it
      const res = await post(`capture/frame?n=${n}`, blob);
      if (!res.ok) throw new Error(`frame ${n}: ${(await res.json().catch(() => ({}))).error || res.status}`);
    }
    performance.now = real;
    await post('capture/status', { state: 'done', frames: N, errors }, true);
  } catch (e) {
    await post('capture/status', { state: 'failed', error: String((e && e.message) || e), errors }, true).catch(() => {});
  }
}

// the capture camera at song time t
function place(cam, job, t, aspect, ed) {
  const c = job.camera;
  cam.aspect = aspect;
  if (c.object) {
    const it = ed.byName.get(c.object);
    if (!it) throw new Error(`no camera "${c.object}"`);
    it.obj.updateMatrixWorld(true);
    it.obj.matrixWorld.decompose(cam.position, cam.quaternion, new THREE.Vector3());
    cam.fov = c.vfov || it.obj.fov || 50;
    if (it.obj.near) { cam.near = it.obj.near; cam.far = Math.max(it.obj.far || 0, 500); }
  } else if (c.path) {
    const ks = c.path.keys, n = ks.length;
    const keys = ks.map((k, i) => ({ t: n > 1 ? i / (n - 1) : 0, pos: k.pos, look: k.look, lens: [k.lens || ks[0].lens || 35] }));
    const u = ease(THREE.MathUtils.clamp((t - (c.path.at ?? job.t0)) / Math.max(1e-6, c.path.seconds), 0, 1));
    const pos = vec(keys, u, 'pos', 'smooth'), look = vec(keys, u, 'look', 'smooth'), lens = vec(keys, u, 'lens', 'smooth')[0];
    cam.position.copy(P(pos));
    cam.up.set(0, 1, 0);
    cam.lookAt(P(look));
    cam.fov = c.vfov || THREE.MathUtils.radToDeg(2 * Math.atan(SENSOR_MM / 2 / lens / aspect));
  } else {
    cam.position.copy(P(c.pos));
    cam.up.copy(P(c.up || [0, 0, 1]));
    cam.lookAt(P(c.pos).add(P(c.fwd)));
    cam.fov = c.vfov || 50;
  }
  cam.updateProjectionMatrix();
  cam.updateMatrixWorld(true);
}
