// Mouth tracks on stage people (lip sync). world.json faces: {person: [{file, at}]}: file a mouth track in the scene's
// faces/ folder (face.py: ARKit face unit weights per frame), at the song second where the track's own first frame
// plays. Every frame, on the music clock, the body actors.js shows for the person (a take, a Follow, a rest pose) gets
// the track's weights on its morph targets by name (jawOpen, mouthFunnel, ...: people exported with mouth shapes kept),
// on every mesh that has them, so the teeth and tongue move with the lips. Outside every track, or with no music, the
// mouth rests. A body with no mouth shapes is reported once (face_unsupported) and left alone. A capture moves the
// music clock frame by frame (capture.js), so a film shows the same mouth.
//   live: face_reload {faces} -> {people, tracks}   (the op changed world.json faces or a track file)
//         face_now {person} -> {person, t, file, weights: {unit: w}} | {person, resting}
//   events: face_unsupported {person, actor}, face_error {file, error}
import { world } from './world.js';

export function initFaces(ed, live, actors, musicNow) {
  const tracks = new Map();                   // file -> track | {error} | null (loading)
  const morphs = new WeakMap();               // rig -> [{ mesh, idx: {unit: morph index} }]
  const told = new Set();
  const shown = new Map();                    // person -> the rig whose mouth was last set (to rest it when it changes)

  function get(file) {
    if (!tracks.has(file)) {
      tracks.set(file, null);
      fetch(`scenes/${encodeURIComponent(ed.sceneName)}/faces/${encodeURIComponent(file)}`, { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status} for faces/${file}`))))
        .then((tr) => { tr.n = tr.frames.length; tracks.set(file, tr); })
        .catch((e) => { tracks.set(file, { error: String(e.message || e) }); live.emit('face_error', { file, error: String(e.message || e) }); });
    }
    return tracks.get(file);
  }
  function reload() { tracks.clear(); told.clear(); }
  ed.addEventListener('revealed', reload);

  function morphsOf(rig) {
    if (!morphs.has(rig)) {
      const out = [];
      rig.root.traverse((o) => { if (o.isMesh && o.morphTargetDictionary && o.morphTargetInfluences) out.push({ mesh: o, idx: o.morphTargetDictionary }); });
      morphs.set(rig, out);
    }
    return morphs.get(rig);
  }
  function setMouth(rig, units, w) {           // w: weights in units' order, or null to rest
    for (const { mesh, idx } of morphsOf(rig)) {
      const inf = mesh.morphTargetInfluences;
      if (!w) { for (const k in idx) inf[idx[k]] = 0; continue; }
      for (let j = 0; j < units.length; j++) { const k = idx[units[j]]; if (k !== undefined) inf[k] = w[j]; }
    }
  }

  // the weights of person's track at song second t (between two frames), or null outside every track
  function at(person, t) {
    for (const e of (world().faces || {})[person] || []) {
      const tr = get(e.file);
      if (!tr || tr.error) continue;
      const x = (t - e.at) * tr.fps;
      if (x < 0 || x > tr.n - 1) continue;
      const i = Math.floor(x), f = x - i, a = tr.frames[i], b = tr.frames[Math.min(i + 1, tr.n - 1)];
      return { file: e.file, units: tr.units, w: a.map((v, j) => v + (b[j] - v) * f) };
    }
    return null;
  }

  function update() {
    const faces = world().faces;
    if (!faces) return;
    const m = musicNow();
    for (const person of Object.keys(faces)) {
      const rig = actors.shownRig(person);
      const was = shown.get(person);
      if (was && was !== rig) setMouth(was, null, null);
      if (!rig) { shown.delete(person); continue; }
      shown.set(person, rig);
      if (!morphsOf(rig).length) {
        if (!told.has(person)) { told.add(person); live.emit('face_unsupported', { person, actor: rig.who }); }
        continue;
      }
      const got = m && m.playing ? at(person, m.t) : null;
      setMouth(rig, got && got.units, got && got.w);
    }
  }
  ed.preRender.push(function faces() { update(); });      // after actors.js has chosen and posed the bodies

  live.handlers.face_reload = (c) => {
    if (c && c.faces) world().faces = c.faces;
    reload();
    const f = world().faces || {};
    for (const list of Object.values(f)) for (const e of list) get(e.file);
    return { people: Object.keys(f), tracks: Object.values(f).reduce((n, l) => n + l.length, 0) };
  };
  live.handlers.face_now = (c) => {
    const m = musicNow(), got = m && m.playing ? at(c.person, m.t) : null;
    const rig = actors.shownRig(c.person);
    if (!got) return { person: c.person, resting: true, music: !!(m && m.playing), body: rig ? rig.who : null };
    return { person: c.person, t: m.t, file: got.file, body: rig ? rig.who : null, mouth_shapes: rig ? morphsOf(rig).length > 0 : false,
      weights: Object.fromEntries(got.units.map((u, j) => [u, +got.w[j].toFixed(2)]).filter(([, v]) => v > 0)) };
  };
  return { at, reload, update };
}
