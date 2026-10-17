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
//           {pos, fwd, up, vfov} fixed, all in Blender coordinates; vfov overrides the lens;
//           {track: {person, bone, dist, height, angle, facing, smooth, vfov}} follows a person playing a take: it looks at
//           their bone (default spine_03) from dist metres (3), height metres above it (0.1), angle degrees round from
//           their front at frame 0 (0 = in front, 90 = their left side), the bearing then held in the room so their
//           turns show; it trails their moves by smooth seconds (0.4). {eyes: {person, vfov}}: through their eyes as
//           the head turns, their head hidden (the embodied view: their own body when they look down)
//   sets    every load set is loaded (the headset unloads some to stay light; the film wants everyone), unless
//           sets: false; setup can unload one ({type: load_set, name, loaded: false})
//   setup   live commands run once before the first frame (actor_play, sky, light, load_set, behaviour presses), the
//           same ones an agent sends, while the room settles in real time (a set comes in paced by the clock); then
//           the clock stops and every take playing starts again at frame 0. The capture's server answers every write
//           "not saved", so nothing reaches the scene files or the live link. A command with at_song (song seconds)
//           waits for that frame instead and runs on film time before it is drawn (film, 2026-10-16: a light switch at
//           255.0 s; the house stays club-coloured until then)
//   screen_key  a setup command for captures only: {type: screen_key, material: 'screen' (default) | object, color:
//           '#00ff00' | 'off'} draws that material flat and unlit in the key colour (no tone mapping, no fog, no
//           shadows), so every keyed pixel in a frame is the visible screen and a comp in post finds occluders exactly;
//           'off' puts the scene's material back
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
    const handlers = { ...live.handlers, screen_key: screenKey(ed) };
    const run = async (c) => {
      const { at_song, ...cmd } = c;
      const h = handlers[cmd.type];
      if (!h) throw new Error(`setup: no command "${cmd.type}"`);
      await h(cmd);
    };
    const first = (job.setup || []).filter((c) => c.at_song == null);
    const timed = (job.setup || []).filter((c) => c.at_song != null).sort((a, b) => a.at_song - b.at_song);
    for (const c of timed) if (!handlers[c.type]) throw new Error(`setup: no command "${c.type}"`);
    // the room in, the sets loaded and the setup run, in real time (the reveal paces itself on the clock), drawing
    // all the while: every piece revealed and the construct gone
    let busy = true, failed = null;
    (async () => {
      while (ed.staging() || construct.active) await new Promise((res) => setTimeout(res, 50));
      if (sets && job.sets !== false) for (const [n, s] of Object.entries(sets.state())) if (!s.loaded) await sets.load(n);
      for (const c of first) await run(c);
    })().catch((e) => { failed = e; }).finally(() => { busy = false; });
    const until = performance.now() + (job.settle_s || 180) * 1000;
    while ((busy || ed.staging() || construct.active) && !failed && performance.now() < until) {
      try { place(cam, job, job.t0, W / H, ed, actors); } catch (e) { if (!busy) throw e; }   // a tracked person still loading
      step();
      await new Promise((res) => setTimeout(res, 15));
    }
    if (failed) throw failed;
    if (busy || ed.staging() || construct.active) throw new Error('the room did not finish coming in');
    // everyone in their pose before frame 0 (film, 2026-10-10: the first seconds showed statues and arms-out people)
    if (actors.rested) {
      await Promise.race([actors.rested(), new Promise((res) => setTimeout(res, (job.rest_s || 60) * 1000))]);
      const late = actors.notResting ? actors.notResting() : [];
      if (late.length) errors.push(`not in their pose at frame 0: ${late.join(', ')}`);
    }
    // film time from here: performance.now stands still between frames, and the takes start at frame 0
    const real = performance.now.bind(performance), base = real();
    let vt = base;
    performance.now = () => vt;
    // the body too, as at a loop: its facing and balance from the wait would otherwise carry in, and vary run to run
    // (the 2026-10-17 study: 0-12 degrees of facing at frame 0, 2 steps or 5 in the same clip)
    for (const st of actors.playing.values()) if (st.frames) { st.t0 = base; st.i = 0; st.feet = { l: {}, r: {} }; st.body = null; st.chestHands = undefined; }
    await post('capture/status', { state: 'rendering', frames: N }, true);
    for (let n = 0; n < N; n++) {
      songT = job.t0 + n / fps;
      vt = base + (n * 1000) / fps;
      if (clock && job.clock !== false) guard('clock', () => clock.seek((job.clock_at ?? job.t0) + n / fps));
      while (timed.length && timed[0].at_song <= songT + 1e-6) {
        const c = timed.shift();
        try { await run(c); } catch (e) { throw new Error(`setup ${c.type} at_song ${c.at_song}: ${e.message || e}`); }
      }
      place(cam, job, songT, W / H, ed, actors);
      step();
      const pr = job.probe ? JSON.stringify(probe(actors, job.probe, songT)) : null;
      const blob = await new Promise((res) => r.domElement.toBlob(res, 'image/png'));   // in the task that drew it
      const res = await post(`capture/frame?n=${n}`, blob);
      if (!res.ok) throw new Error(`frame ${n}: ${(await res.json().catch(() => ({}))).error || res.status}`);
      if (pr) await post('clientlog', { entries: [{ level: 'probe', msg: pr }] }, true);   // after the frame: an await
      // between drawing and toBlob hands back a cleared canvas (black frames)
    }
    performance.now = real;
    await post('capture/status', { state: 'done', frames: N, errors }, true);
  } catch (e) {
    await post('capture/status', { state: 'failed', error: String((e && e.message) || e), errors }, true).catch(() => {});
  }
}

// screen_key: a material drawn flat in a key colour for the capture, and back again with 'off'
function screenKey(ed) {
  const kept = new Map();    // mesh -> its own material (or array of them)
  return (c) => {
    let name = c.material || null;
    if (!name && c.object) {
      const it = ed.byName.get(c.object);
      if (!it) throw new Error(`no object "${c.object}"`);
      const ms = ed.materialsOf(it);
      if (ms.length !== 1) throw new Error(`"${c.object}" has ${ms.length} materials (${ms.join(', ') || 'none'}): pass material`);
      name = ms[0];
    }
    name = name || 'screen';
    if (!ed.materials.has(name)) name = [...ed.materials.keys()].find((n) => n.toLowerCase() === String(name).toLowerCase()) || name;
    const e = ed.materials.get(name);
    if (!e) throw new Error(`no material "${name}"`);
    const mine = new Set(e.mats);
    if (c.color === 'off') {
      let n = 0;
      for (const [mesh, m] of kept) if (mesh.userData.screenKey === name) { mesh.material = m; delete mesh.userData.screenKey; kept.delete(mesh); n++; }
      return { screen_key: 'off', material: name, meshes: n };
    }
    const key = new THREE.MeshBasicMaterial({ color: new THREE.Color(c.color || '#00ff00'), toneMapped: false, fog: false });
    let n = 0;
    ed.scene.traverse((o) => {
      if (!o.isMesh) return;
      const orig = kept.has(o) ? kept.get(o) : o.material;
      const list = Array.isArray(orig) ? orig : [orig];
      if (!list.some((m) => mine.has(m))) return;
      if (!kept.has(o)) kept.set(o, orig);
      o.material = Array.isArray(orig) ? orig.map((m) => (mine.has(m) ? key : m)) : key;
      o.userData.screenKey = name;
      n++;
    });
    if (!n) throw new Error(`no mesh draws the material "${name}"`);
    return { screen_key: c.color || '#00ff00', material: name, meshes: n };
  };
}

// probe: a person's body as numbers each frame (the 2026-10-16 body study: judge feet, hips and balance by measure,
// not by eye): world positions of the drawn hips, feet and head, the hips the steps follow, the balance point, the
// planted feet and which foot is stepping; written to the capture's log.txt as 'probe:' lines
function probe(actors, person, t) {
  const st = actors && actors.playing.get(person), rig = st && st.rig;
  if (!rig) return { t, none: true };
  const r = (v) => v && [+v.x.toFixed(3), +v.y.toFixed(3), +v.z.toFixed(3)];
  const at = (n) => rig.bones[n] && r(rig.bones[n].getWorldPosition(new THREE.Vector3()));
  const b = st.body || {}, fl = (st.feet && st.feet.l) || {}, fr = (st.feet && st.feet.r) || {};
  return { t: +t.toFixed(3), pel: at('pelvis'), hd: at('head'), fl: at('foot_l'), fr: at('foot_r'), hip: r(st.lastHip),
    bal: r(b.bal), over: b.over ? r(b.over.dir) : null, al: r(fl.at), ar: r(fr.at), sl: !!fl.step, sr: !!fr.step,
    mode: st.start ? 'start pose' : 'base', pinned: Object.keys(st.pins || {}).filter((k) => st.pins[k]),
    short: Object.keys(rig.short || {}).filter((k) => rig.short[k]), hy: yawOf(rig, 'head'), cy: yawOf(rig, 'spine_03'), py: yawOf(rig, 'pelvis'),
    hl: at('hand_l'), hr: at('hand_r'), ch: st.chestHands === undefined ? null : +THREE.MathUtils.radToDeg(st.chestHands).toFixed(1) };
}
// a bone's heading in degrees: its rest forward (the rig's) turned by its turn from rest, about the vertical
function yawOf(rig, n) {
  const b = rig.bones[n];
  if (!b || !rig.rest[n]) return null;
  const q = b.getWorldQuaternion(new THREE.Quaternion()).multiply(rig.rest[n].q.clone().invert());
  const f = rig.fwd.clone().applyQuaternion(q);
  return +THREE.MathUtils.radToDeg(Math.atan2(f.x, f.z)).toFixed(1);
}

// the capture camera at song time t
const Y = new THREE.Vector3(0, 1, 0);
function place(cam, job, t, aspect, ed, actors) {
  const c = job.camera;
  cam.aspect = aspect;
  if (c.track || c.eyes) {
    const k = c.track || c.eyes, st = actors && actors.playing.get(k.person), rig = st && st.rig;
    if (!rig) throw new Error(`${c.track ? 'track' : 'eyes'}: ${k.person} is not playing a take (setup: actor_play)`);
    rig.root.updateMatrixWorld(true);
    const yaw = st.lastYaw || new THREE.Quaternion();
    if (c.eyes) {
      const hb = rig.bones.head;
      hb.scale.setScalar(1e-3);                              // their head folds away, as from the inside (embody.js)
      const q = hb.getWorldQuaternion(new THREE.Quaternion()).multiply(rig.rest.head.q.clone().invert());
      const fwd = rig.fwd.clone().applyQuaternion(q);
      cam.position.copy(hb.getWorldPosition(new THREE.Vector3())).addScaledVector(Y, 0.08).addScaledVector(fwd, 0.09);
      cam.quaternion.copy(q).multiply(new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 0, -1), rig.fwd));
      cam.near = 0.03;
      cam.fov = k.vfov || 90;
    } else {
      const at = (rig.bones[k.bone || 'spine_03'] || rig.bones.pelvis).getWorldPosition(new THREE.Vector3());
      const tr = cam.userData.track;
      if (!tr || t <= job.t0) {                              // frame 0 (and the settle before it): the bearing is set
        // their front: where their toes point now (the feet are what the body faces; a turned head or a stale facing
        // put the first study shots behind a seated man)
        const toes = new THREE.Vector3();
        for (const sd of ['l', 'r']) if (rig.bones[`ball_${sd}`] && rig.bones[`foot_${sd}`]) toes.add(rig.bones[`ball_${sd}`].getWorldPosition(new THREE.Vector3()).sub(rig.bones[`foot_${sd}`].getWorldPosition(new THREE.Vector3())));
        toes.setY(0);
        // facing: [x, z] fixes the front in the room, so two solvers of the same take are filmed from the same place
        if (k.facing) toes.set(k.facing[0], 0, k.facing[1]);
        const dir = (toes.lengthSq() > 1e-6 ? toes : rig.fwd.clone().applyQuaternion(yaw).setY(0)).normalize()
          .applyAxisAngle(Y, THREE.MathUtils.degToRad(k.angle ?? 0));
        cam.userData.track = { dir, at: at.clone() };
      } else tr.at.lerp(at, 1 - Math.exp(-1 / (job.fps * Math.max(1e-3, k.smooth ?? 0.4))));
      const { dir, at: a } = cam.userData.track;
      cam.position.copy(a).addScaledVector(dir, k.dist ?? 3).addScaledVector(Y, k.height ?? 0.1);
      cam.up.set(0, 1, 0);
      cam.lookAt(a);
      cam.fov = k.vfov || 40;
    }
  } else if (c.object) {
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
