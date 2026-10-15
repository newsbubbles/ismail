// Hands in XR: what each tracked hand is doing (finger curls, pinch, palm direction -> a named gesture, logged as an
// event Claude can read; 'phone' = thumb and pinky out), point-to-travel (finger gun to aim an arc, drop the thumb to go; or the right stick), and
// takes: head and both hands, every joint, recorded at 30 Hz to scenes/<scene>/takes/<id>/ on the server.
import * as THREE from 'three';
import { GIZMO } from './editor.js';

export const JOINTS = ['wrist',
  'thumb-metacarpal', 'thumb-phalanx-proximal', 'thumb-phalanx-distal', 'thumb-tip',
  'index-finger-metacarpal', 'index-finger-phalanx-proximal', 'index-finger-phalanx-intermediate', 'index-finger-phalanx-distal', 'index-finger-tip',
  'middle-finger-metacarpal', 'middle-finger-phalanx-proximal', 'middle-finger-phalanx-intermediate', 'middle-finger-phalanx-distal', 'middle-finger-tip',
  'ring-finger-metacarpal', 'ring-finger-phalanx-proximal', 'ring-finger-phalanx-intermediate', 'ring-finger-phalanx-distal', 'ring-finger-tip',
  'pinky-finger-metacarpal', 'pinky-finger-phalanx-proximal', 'pinky-finger-phalanx-intermediate', 'pinky-finger-phalanx-distal', 'pinky-finger-tip'];
const FINGERS = ['index', 'middle', 'ring', 'pinky'];
const EXT = 60, CURL = 140;              // degrees of total bend: under EXT a finger is extended, over CURL curled
const PINCH_ON = 0.02, PINCH_OFF = 0.035; // thumb tip to finger tip, metres (hysteresis)
const HOLD_MS = 150;                      // a gesture must hold this long before it counts
const ARC_SPEED = 7.0, G = 9.8, ARC_N = 48, ARC_DT = 0.035;
const REC_HZ = 30;
const Y = new THREE.Vector3(0, 1, 0);

export function initHands(ed, xrApi, emit) {
  const { renderer, scene, camera, rig } = ed;
  const xr = renderer.xr;
  const P = (h, n) => { const j = h.joints && h.joints[n]; return j && j.visible !== false ? j.getWorldPosition(new THREE.Vector3()) : null; };
  const bend = (a, b, c) => { const u = b.clone().sub(a), v = c.clone().sub(b); return THREE.MathUtils.radToDeg(u.angleTo(v)); };

  // ---- features and the gesture of one hand
  function features(hand, side) {
    const p = {};
    for (const n of JOINTS) { p[n] = P(hand, n); if (!p[n]) return null; }
    const f = { curl: {}, ext: {}, tip: {} };
    for (const fn of FINGERS) {
      const k = (s) => p[`${fn}-finger-${s}`];
      const b = bend(k('metacarpal'), k('phalanx-proximal'), k('phalanx-intermediate'))
        + bend(k('phalanx-proximal'), k('phalanx-intermediate'), k('phalanx-distal'))
        + bend(k('phalanx-intermediate'), k('phalanx-distal'), k('tip'));
      f.curl[fn] = b;
      f.ext[fn] = b < EXT ? 1 : b > CURL ? -1 : 0;
    }
    const tb = bend(p['thumb-metacarpal'], p['thumb-phalanx-proximal'], p['thumb-phalanx-distal'])
      + bend(p['thumb-phalanx-proximal'], p['thumb-phalanx-distal'], p['thumb-tip']);
    f.curl.thumb = tb;
    const thumbAway = p['thumb-tip'].distanceTo(p['index-finger-phalanx-proximal']);
    f.ext.thumb = tb < 50 && thumbAway > 0.045 ? 1 : thumbAway < 0.03 ? -1 : 0;
    f.thumbAway = thumbAway;
    f.thumbOnMiddle = Math.min(p['thumb-tip'].distanceTo(p['middle-finger-phalanx-proximal']),
      p['thumb-tip'].distanceTo(p['middle-finger-phalanx-intermediate'])) < 0.03;
    f.thumbTucked = p['thumb-tip'].distanceTo(p['middle-finger-phalanx-intermediate']) < 0.035 ||
      p['thumb-tip'].distanceTo(p['index-finger-phalanx-intermediate']) < 0.03;
    f.pinch = p['thumb-tip'].distanceTo(p['index-finger-tip']);
    f.pinchMid = p['thumb-tip'].distanceTo(p['middle-finger-tip']);
    const w = p.wrist, a = p['index-finger-metacarpal'].clone().sub(w), b = p['pinky-finger-metacarpal'].clone().sub(w);
    f.palm = new THREE.Vector3().crossVectors(a, b).normalize().multiplyScalar(side === 'left' ? 1 : -1);
    f.thumbDir = p['thumb-tip'].clone().sub(p['thumb-phalanx-proximal']).normalize();
    f.indexDir = p['index-finger-tip'].clone().sub(p['index-finger-phalanx-proximal']).normalize();
    f.indexTip = p['index-finger-tip'];
    f.wrist = w;
    f.p = p;
    return f;
  }
  function classify(f, prev) {
    const e = f.ext, ext4 = FINGERS.filter((n) => e[n] === 1), curl4 = FINGERS.filter((n) => e[n] === -1);
    const pinching = f.pinch < (prev === 'pinch' || prev === 'ok' ? PINCH_OFF : PINCH_ON);
    if (pinching && ['middle', 'ring', 'pinky'].every((n) => e[n] === 1)) return 'ok';
    if (pinching) return 'pinch';
    if (f.pinchMid < PINCH_ON && e.index === 1) return 'pinch_middle';
    if (curl4.length === 4) {
      if (e.thumb === 1 && f.thumbDir.dot(Y) > 0.6) return 'thumbs_up';
      if (e.thumb === 1 && f.thumbDir.dot(Y) < -0.6) return 'thumbs_down';
      return 'fist';
    }
    if (ext4.length === 4) return 'open';
    if (e.index === 1 && ['middle', 'ring', 'pinky'].every((n) => e[n] === -1)) return e.thumb === 1 ? 'gun' : 'point';
    if (e.thumb === 1 && f.curl.pinky < 95 && e.index === -1 && e.middle === -1 && e.ring === -1) return 'phone';   // a half-bent pinky still counts
    if (e.index === 1 && e.middle === 1 && e.ring === -1 && e.pinky === -1) return 'peace';
    if (e.index === 1 && e.pinky === 1 && e.middle === -1 && e.ring === -1) return 'rock';
    return 'none';
  }
  function palmWord(f) {
    const head = camera.getWorldPosition(new THREE.Vector3()), toHead = head.sub(f.wrist).normalize();
    if (f.palm.dot(Y) > 0.7) return 'up';
    if (f.palm.dot(Y) < -0.7) return 'down';
    if (f.palm.dot(toHead) > 0.6) return 'toward_you';
    if (f.palm.dot(toHead) < -0.6) return 'away';
    return 'side';
  }

  const H = { left: { g: 'none', cand: 'none', since: 0, f: null }, right: { g: 'none', cand: 'none', since: 0, f: null } };

  // ---- point to travel: an arc and a landing ring; the finger gun aims, dropping the thumb goes
  const arcGeo = new THREE.BufferGeometry().setFromPoints(Array.from({ length: ARC_N }, () => new THREE.Vector3()));
  const arc = new THREE.Line(arcGeo, new THREE.LineBasicMaterial({ color: 0x7dd3fc, toneMapped: false, transparent: true, opacity: 0.85 }));
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.18, 0.24, 40).rotateX(-Math.PI / 2),
    new THREE.MeshBasicMaterial({ color: 0x7dd3fc, toneMapped: false, transparent: true, opacity: 0.8, side: THREE.DoubleSide }));
  // the travel beam: a 4 mm rod from the fingertip to the landing (a 1 px line was hard to see in the headset)
  const tbeam = new THREE.Mesh(new THREE.CylinderGeometry(0.002, 0.002, 1, 6, 1, true).rotateX(Math.PI / 2).translate(0, 0, 0.5),
    new THREE.MeshBasicMaterial({ color: 0x7dd3fc, toneMapped: false, transparent: true, opacity: 0.8, depthWrite: false }));
  arc.layers.set(GIZMO); ring.layers.set(GIZMO); tbeam.layers.set(GIZMO);
  arc.visible = ring.visible = tbeam.visible = false; arc.frustumCulled = false; tbeam.frustumCulled = false;
  for (const o of [arc, ring, tbeam]) { o.material.depthTest = false; o.renderOrder = o === ring ? 998 : 997; }   // on top, like the pointer
  scene.add(arc, ring, tbeam);
  const rc = new THREE.Raycaster();
  rc.layers.enableAll();
  const aim = { by: null, target: null };
  function castArc(origin, dir) {
    const pts = arcGeo.attributes.position, v = dir.clone().multiplyScalar(ARC_SPEED), p = origin.clone();
    let hit = null, n = 0;
    for (let i = 0; i < ARC_N; i++) {
      pts.setXYZ(i, p.x, p.y, p.z); n = i + 1;
      const q = p.clone().addScaledVector(v, ARC_DT);
      v.y -= G * ARC_DT;
      if (!hit) {
        rc.set(p, q.clone().sub(p).normalize());
        rc.far = p.distanceTo(q);
        // walls, doors and windows do not stop the arc: it lands on the first floor-like surface (the user could not
        // jump into the club through its closed front)
        const h = rc.intersectObjects(ed.pickRoots, true).find((x) => x.object.isMesh && ed.pickable(x.object) && !x.object.userData.pickProxy && x.face &&
          x.face.normal.clone().transformDirection(x.object.matrixWorld).y > 0.9);   // 0.9: the 40 deg roof is not a floor
        if (h) {
          hit = { point: h.point, ok: true };
          pts.setXYZ(i + 1 < ARC_N ? i + 1 : i, h.point.x, h.point.y, h.point.z); n = Math.min(ARC_N, i + 2);
          break;
        }
      }
      p.copy(q);
    }
    arcGeo.setDrawRange(0, n);
    pts.needsUpdate = true;
    return hit;
  }
  // A straight beam, not an arc: the user found an arc beside the straight selection beam confusing (2026-10-02).
  // It lands on the first floor-like surface along the line, through walls, up to TRAVEL_FAR metres.
  const TRAVEL_FAR = 40;
  function castStraight(origin, dir) {
    rc.set(origin, dir); rc.far = TRAVEL_FAR;
    const hits = rc.intersectObjects(ed.pickRoots, true).filter((x) => x.object.isMesh && ed.pickable(x.object) && !x.object.userData.pickProxy && x.face);
    const floorish = (x) => x.face.normal.clone().transformDirection(x.object.matrixWorld).y > 0.9;
    let h = hits.find(floorish);
    // pointing at a door or a wall means "go through there": land on the floor half a metre past the first surface
    // (the user could not get into the club through its door; the beam rarely meets a floor when aimed level)
    if (hits.length && !floorish(hits[0]) && (!h || h.distance > hits[0].distance + 0.6)) {
      const past = hits[0].point.clone().addScaledVector(dir, 0.5);
      rc.set(past.clone().setY(past.y + 0.05), new THREE.Vector3(0, -1, 0)); rc.far = 4;
      const down = rc.intersectObjects(ed.pickRoots, true).find((x) => x.object.isMesh && ed.pickable(x.object) && !x.object.userData.pickProxy && x.face && floorish(x));
      if (down) h = down;
    }
    const end = h ? h.point : origin.clone().addScaledVector(dir, TRAVEL_FAR);
    tbeam.position.copy(origin); tbeam.lookAt(end); tbeam.scale.set(1, 1, origin.distanceTo(end));
    const pts = arcGeo.attributes.position;
    pts.setXYZ(0, origin.x, origin.y, origin.z); pts.setXYZ(1, end.x, end.y, end.z);
    arcGeo.setDrawRange(0, 2); pts.needsUpdate = true;
    return h ? { point: h.point, ok: true } : null;
  }
  function showAim(by, origin, dir) {
    const hit = castStraight(origin, dir);
    aim.by = by; aim.target = hit && hit.ok ? hit.point.clone() : null;
    tbeam.visible = true;
    tbeam.material.color.set(aim.target ? 0x7dd3fc : 0xf87171);
    ring.visible = !!aim.target;
    if (aim.target) ring.position.copy(aim.target).y += 0.01;
  }
  function hideAim() { arc.visible = ring.visible = tbeam.visible = false; aim.by = null; aim.target = null; }
  function travel(how) {
    if (!aim.target) return hideAim();
    // no travel while a take records or someone follows the user (the user, 2026-10-03, landed on the roof during a
    // Follow at 21:13; the follow then ended as "walked away")
    const following = window.VR_actors && [...window.VR_actors.playing.values()].some((s) => s.live);
    if (rec.on || following) {
      emit('teleport_blocked', { how, why: rec.on ? 'take recording' : 'someone is following you' });
      return hideAim();
    }
    const head = camera.getWorldPosition(new THREE.Vector3()), t = aim.target;
    rig.position.x += t.x - head.x; rig.position.z += t.z - head.z; rig.position.y = t.y;
    rig.updateMatrixWorld(true);
    emit('teleport', { how, to: [t.x, t.y, t.z].map((x) => +x.toFixed(3)) });
    hideAim();
  }

  // ---- takes
  const rec = { on: false, id: null, t0: 0, last: 0, buf: [], frames: 0, name: '', sending: false };
  const scene_ = () => ed.sceneName || '';
  async function post(path, body, id = rec.id) {
    const r = await fetch(`${path}?scene=${encodeURIComponent(scene_())}&take=${encodeURIComponent(id)}`,
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error('take upload ' + r.status);
    return r.json();
  }
  async function flushTake() {
    if (rec.sending || !rec.buf.length) return;
    rec.sending = true;
    const batch = rec.buf.splice(0, rec.buf.length);
    try { await post('take/frames', batch); } catch (e) { rec.buf.unshift(...batch); }
    rec.sending = false;
  }
  const pad = (x) => String(x).padStart(2, '0');
  const takeId = (d, name) => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}` +
    (name ? '_' + name.replace(/[^A-Za-z0-9_-]+/g, '_').slice(0, 40) : '');
  function startTake(name = '') {
    if (rec.on) return { id: rec.id, already: true };
    const d = new Date();
    rec.id = takeId(d, name);
    Object.assign(rec, { on: true, t0: performance.now(), last: 0, buf: [], frames: 0, name });
    post('take/meta', { id: rec.id, name, scene: scene_(), started: d.toISOString(), ...TAKE_FORMAT, presenting: xr.isPresenting })
      .catch(() => {});
    emit('take_start', { take: rec.id, name });
    return { id: rec.id };
  }
  async function stopTake() {
    if (!rec.on) return { stopped: false };
    rec.on = false;
    const id = rec.id, frames = rec.frames, secs = (performance.now() - rec.t0) / 1000;
    for (let i = 0; i < 20 && (rec.buf.length || rec.sending); i++) { await flushTake(); if (rec.buf.length || rec.sending) await new Promise((r) => setTimeout(r, 100)); }
    await post('take/meta', { ended: new Date().toISOString(), frames, seconds: +secs.toFixed(2) }).catch(() => {});
    emit('take_stop', { take: id, frames, seconds: +secs.toFixed(2) });
    return { id, frames, seconds: +secs.toFixed(2) };
  }
  const TAKE_FORMAT = { hz: REC_HZ, joints: JOINTS, space: 'three.js world, Y up, metres (Blender: x, -z, y)',
    frame: 'frames.jsonl: one line per sample {t, head:[px,py,pz,qx,qy,qz,qw], left/right:{g, palm, j:[[px,py,pz,qx,qy,qz,qw,r] x 25]} or null, ctl}' };

  // ---- every Follow is recorded too, in memory: a good performance happens when the user is not "recording" (the
  // user, 2026-10-04: a 61 s follow of the bartender he liked was gone, because a plain Follow kept nothing). The last
  // one (up to SHADOW_MAX_S, the newest part) waits until the next Follow or a reload; keepLast saves it as a take.
  const SHADOW_MAX_S = 180;
  const shadow = { on: false, t0: 0, last: 0, frames: [], person: null, started: null, perf: null };
  let lastFollow = null;
  // t0 and perf: the performance's clock (perform.js), so its voice clips line up with these frames
  function shadowStart(person, t0 = null, perf = null) {
    if (rec.on) return;
    const now = performance.now();
    Object.assign(shadow, { on: true, t0: t0 || now, last: 0, frames: [], person, started: new Date(Date.now() - (now - (t0 || now))), perf });
  }
  function shadowStop() {
    if (!shadow.on) return null;
    shadow.on = false;
    const fr = shadow.frames;
    shadow.frames = [];
    if (fr.length < REC_HZ) return null;                        // under a second: nothing worth keeping
    const t0 = fr[0].t;                                         // the buffer may have dropped its oldest part
    lastFollow = { person: shadow.person, frames: t0 ? fr.map((f) => ({ ...f, t: r4(f.t - t0), ...(f.ev ? { ev: f.ev.map((e) => ({ ...e, t: r4(e.t - t0) })) } : {}) })) : fr,
      started: new Date(shadow.started.getTime() + t0 * 1000), seconds: r4(fr[fr.length - 1].t - t0),
      perf: shadow.perf ? { performance: shadow.perf.id, perf_shift: r4(t0), perf_scene: shadow.perf.scene } : null };
    emit('follow_buffered', { person: lastFollow.person, frames: fr.length, seconds: lastFollow.seconds });
    return lastFollowInfo();
  }
  const lastFollowInfo = () => (lastFollow ? { person: lastFollow.person, frames: lastFollow.frames.length, seconds: lastFollow.seconds,
    started: lastFollow.started.toISOString() } : null);
  async function keepLast(name) {
    if (!lastFollow) throw new Error('no follow to keep: the last Follow is kept in memory until the next one or a reload');
    const lf = lastFollow, label = name || lf.person || 'follow', id = takeId(lf.started, label);
    await post('take/meta', { id, name: label, scene: scene_(), started: lf.started.toISOString(), ...TAKE_FORMAT, presenting: true,
      from: 'follow', ...(lf.perf || {}) }, id);
    for (let i = 0; i < lf.frames.length; i += 300) await post('take/frames', lf.frames.slice(i, i + 300), id);
    await post('take/meta', { ended: new Date(lf.started.getTime() + lf.seconds * 1000).toISOString(), frames: lf.frames.length,
      seconds: lf.seconds }, id);
    lastFollow = null;
    const r = { id, take: id, person: lf.person, frames: lf.frames.length, seconds: lf.seconds };
    emit('follow_kept', r);
    return r;
  }
  function discardLast() { const had = !!lastFollow; lastFollow = null; return { discarded: had }; }

  // ---- the last RECENT_S seconds in VR, always, in memory (the user, 2026-10-15: a moment worth testing with happens
  // when nothing records). keepRecent saves the newest part as a take ("keep that"). Half the take rate: a replay
  // interpolates, and the buffer runs all session. No audio. Kept until a reload, across VR sessions.
  const RECENT_S = 120, RECENT_HZ = 15;
  const recent = { t0: performance.now(), last: 0, frames: [], marks: [] };
  function sampleRecent(now) {
    recent.last = now;
    const fr = makeFrame(now, recent.t0);
    if (recent.marks.length) { fr.ev = recent.marks.map(({ at, ...e }) => ({ ...e, t: r4((at - recent.t0) / 1000) })); recent.marks = []; }
    recent.frames.push(fr);
    const max = RECENT_S * RECENT_HZ;
    if (recent.frames.length > max * 1.1) recent.frames.splice(0, recent.frames.length - max);
  }
  const recentInfo = () => {
    const fr = recent.frames;
    return { frames: fr.length, seconds: fr.length ? r4(fr[fr.length - 1].t - fr[0].t) : 0 };
  };
  async function keepRecent(seconds = 30, name = '') {
    const fr = recent.frames;
    if (fr.length < 2) throw new Error('nothing in the recent buffer: it fills only while someone is in VR on this page');
    const tEnd = fr[fr.length - 1].t, from = tEnd - Math.max(1, Math.min(+seconds || 30, RECENT_S));
    const keep = fr.filter((f) => f.t >= from);
    const t0 = keep[0].t, secs = r4(tEnd - t0);
    const started = new Date(Date.now() - (performance.now() - recent.t0) + t0 * 1000);
    const label = name || 'recent', id = takeId(started, label);
    const out = keep.map((f) => ({ ...f, t: r4(f.t - t0), ...(f.ev ? { ev: f.ev.map((e) => ({ ...e, t: r4(e.t - t0) })) } : {}) }));
    await post('take/meta', { id, name: label, scene: scene_(), started: started.toISOString(), ...TAKE_FORMAT, hz: RECENT_HZ,
      presenting: true, from: 'recent' }, id);
    for (let i = 0; i < out.length; i += 300) await post('take/frames', out.slice(i, i + 300), id);
    await post('take/meta', { ended: new Date(started.getTime() + secs * 1000).toISOString(), frames: out.length, seconds: secs }, id);
    const r = { id, take: id, frames: out.length, seconds: secs };
    emit('recent_kept', r);
    return r;
  }
  const r4 = (x) => Math.round(x * 1e4) / 1e4;
  const pose = (o) => {
    const p = new THREE.Vector3(), q = new THREE.Quaternion();
    o.updateMatrixWorld(true); o.matrixWorld.decompose(p, q, new THREE.Vector3());
    return [p.x, p.y, p.z, q.x, q.y, q.z, q.w].map(r4);
  };
  // the WebXR body (body-tracking), world space like the hands: {joint: [x, y, z, qx, qy, qz, qw]}, or null
  const bodyM = new THREE.Matrix4(), bodyP = new THREE.Vector3(), bodyQ = new THREE.Quaternion(), bodyS = new THREE.Vector3();
  let bodySeen = false;
  function bodyPose() {
    const frame = renderer.xr.getFrame && renderer.xr.getFrame(), ref = renderer.xr.getReferenceSpace();
    const body = frame && frame.body;
    if (!body || !ref) return null;
    const rigM = rig ? rig.matrixWorld : new THREE.Matrix4();
    const out = {};
    for (const [name, space] of body) {
      const p = frame.getPose(space, ref);
      if (!p) continue;
      bodyM.fromArray(p.transform.matrix).premultiply(rigM).decompose(bodyP, bodyQ, bodyS);
      out[name] = [r4(bodyP.x), r4(bodyP.y), r4(bodyP.z), r4(bodyQ.x), r4(bodyQ.y), r4(bodyQ.z), r4(bodyQ.w)];
    }
    if (!bodySeen) { bodySeen = true; emit('body_tracking', { joints: Object.keys(out).length, legs: 'left-upper-leg' in out }); }
    return out;
  }
  function makeFrame(now, t0) {                              // one sample: the head, both hands (or controllers), the body
    const fr = { t: r4((now - t0) / 1000), head: pose(camera) };
    for (const s of xrApi.ctls) {
      const side = s.src ? s.src.handedness : null;
      if (!side || side === 'none') continue;
      if (s.src.hand && s.hand.joints && s.hand.joints.wrist) {
        fr[side] = { g: H[side].g, palm: H[side].palm || null,
          j: JOINTS.map((n) => { const j = s.hand.joints[n]; return j ? [...pose(j), r4(j.jointRadius || 0)] : null; }) };
      } else {
        (fr.ctl = fr.ctl || {})[side] = pose(s.c);
      }
    }
    const body = bodyPose();
    if (body) fr.body = body;
    return fr;
  }
  // grab.js: a grab or a drop inside a person goes into the take being made (a take or a Follow's shadow), on the next
  // frame, with its own time (a drop is marked when the hand began to open, up to a second or two back)
  let marks = [];
  function mark(ev) { if (rec.on || shadow.on) marks.push(ev); if (xr.isPresenting) recent.marks.push(ev); }
  function withMarks(fr, t0) {
    if (marks.length) { fr.ev = marks.map(({ at, ...e }) => ({ ...e, t: r4((at - t0) / 1000) })); marks = []; }
    return fr;
  }
  function sample(now) {
    const fr = withMarks(makeFrame(now, rec.t0), rec.t0);
    rec.buf.push(fr);
    rec.frames++;
    if (rec.buf.length >= REC_HZ) flushTake();
  }

  // ---- a poke at UI wins over travel (the user, 2026-10-04: poking menu buttons with the same finger gun that aims
  // travel teleported him twice). A guard answers, for a hand and its index tip, why travel must wait (a panel or
  // button within 10 cm, or one just poked) or null; while one does, that hand shows no aim and its click is ignored.
  const uiGuards = [];
  function uiHold(side, f) {
    for (const g of uiGuards) { const why = g(side, f.indexTip); if (why) return why; }
    return null;
  }

  // ---- per frame (called from xr.update while presenting)
  function update() {
    const now = performance.now();
    let gunAim = null;
    for (const s of xrApi.ctls) {
      const side = s.src && s.src.handedness;
      if (!side || !H[side]) continue;
      const h = H[side];
      if (!s.src.hand || !s.hand.joints || !s.hand.joints.wrist) { h.f = null; continue; }
      const f = features(s.hand, side);
      if (!f) { h.f = null; continue; }     // tracking lost: no stale hand (the update card floated where the hand was last seen)
      h.f = f;
      // resting: the wrist half a metre or more under the eyes and the fingers hanging down. No gesture, no ray, no
      // grab, no poke for that hand until it comes up (it held a cigarette on the dance floor and selected a dancer)
      const eyeY = camera.getWorldPosition(new THREE.Vector3()).y;
      const rest = eyeY - f.wrist.y > (h.resting ? 0.45 : 0.5) && f.indexDir.y < (h.resting ? -0.35 : -0.5);
      if (rest !== !!h.resting) { h.resting = rest; emit('hand_rest', { hand: side, resting: rest }); }
      if (rest) { h.g = h.cand = 'none'; h.armed = false; continue; }
      const g = classify(f, h.g);
      if (g !== h.cand) { h.cand = g; h.since = now; }
      if (h.cand !== h.g && now - h.since >= HOLD_MS) {
        const prev = h.g;
        h.g = h.cand; h.palm = palmWord(f);
        const hd = camera.getWorldPosition(new THREE.Vector3()), rel = f.wrist.clone().sub(hd)
          .applyQuaternion(camera.getWorldQuaternion(new THREE.Quaternion()).invert());
        emit('gesture', { hand: side, gesture: h.g, prev, palm: h.palm,
          from_head_cm: { right: Math.round(rel.x * 100), up: Math.round(rel.y * 100), forward: Math.round(-rel.z * 100) },
          curl: Object.fromEntries(Object.entries(f.curl).map(([k, v]) => [k, Math.round(v)])), pinch_cm: +(f.pinch * 100).toFixed(1) });
      }
      const held = uiHold(side, f);
      if (held) {
        if (h.g === 'gun' && h.heldFor !== h.since) { h.heldFor = h.since; emit('travel_held', { hand: side, ...held }); }
        h.armed = false; h.clickN = 0; h.restUntil = Math.max(h.restUntil || 0, now + 500);   // and a moment after leaving
        if (aim.by === side) hideAim();
        continue;
      }
      // the click: while aiming, the thumb coming down onto the side of the index or the middle finger travels at
      // once, whatever the hand classifies as on the way (a half-curled index used to read as 'none' and the click was lost)
      if (h.g === 'gun') h.gunSeen = now;
      const aimingHere = aim.by === side;
      // measured on the user's hand (2026-10-02): thumb bend 28-50 deg in the gun, 55-71 deg once it comes down; the
      // distance alone hovered at the threshold and re-armed every frame (five jumps in a second)
      if (aimingHere && f.thumbAway > 0.05 && f.curl.thumb < 48) h.armed = true;
      if (aimingHere && h.armed && (f.thumbAway < 0.035 || f.thumbOnMiddle || f.curl.thumb > 58)) {
        h.clickN = (h.clickN || 0) + 1;
        if (h.clickN >= 2) { h.armed = false; h.clickN = 0; h.restUntil = now + 600; travel('hand ' + side); continue; }
      } else h.clickN = 0;
      if (now < (h.restUntil || 0) || api.framing) { /* just travelled (one click, one jump), or framing a snapshot */ }
      else if (h.g === 'gun' || (aimingHere && now - (h.gunSeen || 0) < 400 && f.curl.index < 110)) gunAim = { side, f };
      else if (!aimingHere) h.armed = false;
    }
    // controllers: right stick forward aims, letting go travels
    let stickAim = null;
    for (const s of xrApi.ctls) {
      if (!s.src || s.src.hand || s.src.handedness !== 'right' || !s.src.gamepad) continue;
      const a = s.src.gamepad.axes, y = a.length >= 4 ? a[3] : a[1] || 0;
      if (y < -0.6 && !s.grab) stickAim = s;
      else if (aim.by === 'stick' && y > -0.3) travel('stick');
    }
    // travel aims along the same ray as selecting (the system's shoulder-through-hand target ray), not the index
    // finger: two directions made the beam jump whenever the pose changed between point and gun
    if (api.performing) { gunAim = stickAim = null; if (aim.by) hideAim(); }   // perform.js: no gesture acts
    if (gunAim) {
      const c = xrApi.ctls.find((x) => x.src && x.src.handedness === gunAim.side);
      if (c) {
        c.c.updateMatrixWorld(true);
        showAim(gunAim.side, new THREE.Vector3().setFromMatrixPosition(c.c.matrixWorld), new THREE.Vector3(0, 0, -1).transformDirection(c.c.matrixWorld));
      } else showAim(gunAim.side, gunAim.f.indexTip, gunAim.f.indexDir);
    }
    else if (stickAim) {
      stickAim.c.updateMatrixWorld(true);
      const o = new THREE.Vector3().setFromMatrixPosition(stickAim.c.matrixWorld);
      const d = new THREE.Vector3(0, 0, -1).transformDirection(stickAim.c.matrixWorld);
      showAim('stick', o, d);
    } else if (aim.by && aim.by !== 'stick') hideAim();
    if (rec.on && now - rec.last >= 1000 / REC_HZ - 1) { rec.last = now; sample(now); }
    if (shadow.on && rec.on) { shadow.on = false; shadow.frames = []; }   // a real take started: it has the frames
    else if (shadow.on && now - shadow.last >= 1000 / REC_HZ - 1) {
      shadow.last = now;
      shadow.frames.push(withMarks(makeFrame(now, shadow.t0), shadow.t0));
      if (shadow.frames.length > SHADOW_MAX_S * REC_HZ) shadow.frames.shift();
    }
    if (xr.isPresenting && now - recent.last >= 1000 / RECENT_HZ - 1) sampleRecent(now);
  }
  xr.addEventListener('sessionend', () => { hideAim(); if (rec.on) stopTake(); });

  // actors.js follow: the frame a take would record right now (a person moves with the user, live)
  const frameNow = () => makeFrame(performance.now(), 0);
  const api = { update, startTake, stopTake, rec, state: H, aim, frameNow, stickAiming: () => aim.by === 'stick', framing: false,
    shadowStart, shadowStop, mark, keepLast, discardLast, lastFollowInfo, lastFollowData: () => lastFollow, performing: false,
    keepRecent, recentInfo,
    addUIGuard: (fn) => uiGuards.push(fn) };
  return api;
}
