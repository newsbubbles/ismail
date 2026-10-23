// Recording a camera move like a take (Nate, 2026-10-14, the v2 shot session): hold a camera, or be it ("camera = my
// head"), while the clock plays; the move is sampled every frame, thinned into smooth keys (interp.js thinKeys: every
// sample within 2 cm and 2 degrees of what the keys play back), and written on that camera between the first and last
// sample, its keys outside that window kept. Then Keep (saved to anim.json), Redo (the old keys back, record again)
// or Undo (the old keys back). Unsaved until Keep, like any key.
//   source 'head': the camera sits at the user's eyes and looks where they look (hidden while it does, so it never
//     blocks the view); 'hold': the camera is wherever it is put (carried on the ray, grabbed, or moved by an agent);
//     'follow': a camera that follows something (behaviours.js s.follow, or stage_follow) bakes that follow. The
//     follow is snapped onto its target at t0, and stopped when the take ends so the new keys play; Undo and Redo
//     start it again, Keep leaves it off (the default source for a following camera).
//   The clock plays from t0 (default: where it is) to t1 (default: the end of its span); a stop, the span's end or a
//   loop back ends the take. A countdown (default 3 s) first, the clock held at t0.
//   live: cam_record {action: start|stop|keep|redo|undo|state, name, source?, t0?, t1?, countdown?, wait?}
//   events: cam_record {state: counting|recording|review|kept|undone, name, ...}
import * as THREE from 'three';
import { t2bPos, t2bQuat } from './editor.js';
import { thinKeys } from './interp.js';

const r6 = (a) => a.map((v) => +v.toFixed(6));

export function initCamRec(ed, live, clock, panels, { countdown, trails, follows = null } = {}) {
  let rec = null;            // { name, it, source, t0, t1, prev, prevMode, samples, phase, lastT, hid }
  const REVIEW = 'cam_record_review', STOP = 'cam_record_stop';

  function headPose() {
    const p = new THREE.Vector3(), q = new THREE.Quaternion(), s = new THREE.Vector3();
    ed.camera.updateMatrixWorld(true);
    ed.camera.matrixWorld.decompose(p, q, s);
    return { location: t2bPos(p), quaternion: t2bQuat(q, true) };        // a camera is aimed: its frame is Blender's
  }
  function state() {
    if (!rec) return { state: 'idle' };
    return { state: rec.phase, name: rec.name, source: rec.source, t0: rec.t0, t1: rec.t1, samples: rec.samples.length,
      ...(rec.result ? { keys: rec.result.keys, err_m: rec.result.err_m, err_deg: rec.result.err_deg, window: rec.result.window } : {}) };
  }
  const tell = () => { const s = state(); live.emit('cam_record', s); return s; };

  async function start(c) {
    if (rec && (rec.phase === 'counting' || rec.phase === 'recording')) throw new Error(`already recording ${rec.name}`);
    if (rec && rec.phase === 'review') { panels.close(REVIEW, 'replaced'); rec = null; }
    const it = ed.byName.get(c.name);
    if (!it) throw new Error('no object ' + c.name);
    const following = follows ? follows.of(it.name) : null;
    const source = c.source || (following ? 'follow' : it.type === 'CAMERA' ? 'head' : 'hold');
    if (!['head', 'hold', 'follow'].includes(source)) throw new Error("source is 'head', 'hold' or 'follow'");
    if (source === 'follow' && !following) throw new Error(`nothing makes ${it.name} follow: start one first (s.follow in its behaviour, or stage_follow)`);
    const span = clock.anim.span;
    const t0 = c.t0 != null ? +c.t0 : clock.st.t, t1 = c.t1 != null ? +c.t1 : span[1];
    if (!(t1 > t0 + 0.2)) throw new Error(`t1 (${t1}) must be after t0 (${t0})`);
    const ks = clock.anim.objects[it.name];
    rec = { name: it.name, it, source, t0, t1, prev: ks ? ks.map((k) => ({ ...k })) : null,
      prevMode: (clock.anim.interp || {})[it.name] || null, samples: [], phase: 'counting', lastT: null, hid: null,
      opts: { source, t0, t1, countdown: c.countdown }, follow: source === 'follow' ? following : null };
    clock.hold(it.name);
    clock.st.playing = false;
    clock.seek(t0);
    if (source === 'head') { rec.hid = it.obj.visible; it.obj.visible = false; }
    tell();
    const n = c.countdown != null ? +c.countdown : 3;
    if (countdown && n > 0) await countdown(n, ed.label(it));
    if (!rec || rec.phase !== 'counting') return state();          // stopped during the countdown
    rec.phase = 'recording';
    clock.seek(t0);
    if (rec.follow) follows.snap(rec.name);   // on its mark at t0, not easing in from wherever the countdown left it
    sampleNow(t0);                    // the take starts at t0 exactly: an old key just before the first frame would whip
    clock.st.playing = true;
    stopPanel();
    return tell();
  }
  async function stopPanel() {
    const r = await panels.show({ panel_id: STOP, title: `● Recording the move: ${ed.label(rec.it)}`,
      text: `${rec.t0.toFixed(1)} s to ${rec.t1.toFixed(1)} s on the clock`, buttons: ['■ Stop'], anchor: 'body', stay: true, quiet: true });
    if (r && r.answer === '■ Stop' && rec && rec.phase === 'recording') finish('stopped');
  }

  ed.preRender.push(() => {
    if (!rec || (rec.phase !== 'recording' && rec.phase !== 'counting')) return;
    if (rec.source === 'head') ed.setBlenderWorld(rec.it, headPose());
    if (rec.phase !== 'recording') return;
    const t = clock.st.t;
    if (!clock.st.playing) return finish('the clock stopped');
    if (t < rec.lastT - 1e-3) return finish('the clock looped');
    if (t > rec.lastT + 1e-4) sampleNow(t);
    if (t >= rec.t1) finish('the end');
  });
  function sampleNow(t) {
    if (rec.source === 'head') ed.setBlenderWorld(rec.it, headPose());
    const tr = ed.blenderTransform(rec.it);
    rec.samples.push({ t, location: tr.location, quaternion: tr.quaternion, scale: tr.scale });
    rec.lastT = t;
  }

  function finish(why) {
    if (!rec || rec.phase !== 'recording') return state();
    clock.st.playing = false;
    panels.close(STOP, 'done');
    if (rec.hid != null) { rec.it.obj.visible = rec.hid; rec.hid = null; }
    if (rec.follow) follows.stop(rec.name, 'baked');                 // the keys play now, not the follow
    const ss = rec.samples;
    if (ss.length < 2) {                                            // nothing to keep: as it was
      restore();
      live.emit('cam_record', { state: 'empty', name: rec.name, why });
      rec = null;
      return { state: 'empty', why };
    }
    const { keys, err_m, err_deg } = thinKeys(ss, { mode: 'smooth' });
    const scale = ss[0].scale, a = ss[0].t, b = ss[ss.length - 1].t;
    const outside = (rec.prev || []).filter((k) => k.t < a - 1e-3 || k.t > b + 1e-3);   // a is t0: the punch-in
    const mine = keys.map((k) => ({ t: +k.t.toFixed(3), location: r6(k.location), quaternion: r6(k.quaternion), scale: r6(scale) }));
    clock.setKeys(rec.name, [...outside, ...mine].sort((x, y) => x.t - y.t), 'smooth');
    clock.unhold(rec.name);
    clock.seek(a);
    rec.phase = 'review';
    rec.result = { keys: mine.length, err_m: +err_m.toFixed(3), err_deg: +err_deg.toFixed(2), window: [+a.toFixed(2), +b.toFixed(2)], why,
      ...(rec.follow ? { baked: rec.follow.target } : {}) };
    if (trails) trails.show(true, [rec.name]);
    review();
    return tell();
  }
  async function review() {
    const name = rec.name, res = rec.result;
    const r = await panels.show({ panel_id: REVIEW, title: `Keep this move for ${ed.label(rec.it)}?`,
      text: `${res.keys} keys, ${res.window[0]} s to ${res.window[1]} s. The path is drawn; play the clock to watch it.` +
        (rec.follow ? ' The follow is off so the keys play; Undo turns it back on.' : ''),
      buttons: ['👍 Keep', '↻ Redo', '↶ Undo'], width: 0.5 });
    if (!rec || rec.name !== name || rec.phase !== 'review' || !r) return;
    if (r.answer === '👍 Keep') await keep();
    else if (r.answer === '↻ Redo') await redo();
    else if (r.answer === '↶ Undo') undo();
  }
  function restore() {
    clock.setKeys(rec.name, rec.prev, rec.prevMode || null);
    clock.unhold(rec.name);
    if (rec.follow && follows) {                                     // following again, as before the take
      try { follows.start(rec.name, rec.follow); } catch (e) { live.emit('cam_record', { state: 'follow_lost', name: rec.name, error: String(e.message || e) }); }
    }
  }
  async function keep() {
    if (!rec || rec.phase !== 'review') throw new Error('no recorded move to keep');
    const name = rec.name, res = rec.result;
    panels.close(REVIEW, 'kept');
    rec = null;
    await clock.save();
    const extra = res.baked ? { follow: 'off: the keys play now' } : {};
    live.emit('cam_record', { state: 'kept', name, ...res, ...extra });
    return { state: 'kept', name, ...res, ...extra };
  }
  function undo() {
    if (!rec) throw new Error('nothing recorded to undo');
    if (rec.phase === 'recording' || rec.phase === 'counting') { rec.phase = 'stopping'; clock.st.playing = false; panels.close(STOP, 'undone'); }
    if (rec.hid != null) { rec.it.obj.visible = rec.hid; rec.hid = null; }
    panels.close(REVIEW, 'undone');
    restore();
    const name = rec.name;
    rec = null;
    live.emit('cam_record', { state: 'undone', name });
    return { state: 'undone', name };
  }
  async function redo() {
    if (!rec || rec.phase !== 'review') throw new Error('no recorded move to redo');
    const { name, opts } = rec;
    undo();
    return start({ name, ...opts });
  }
  function stop() {
    if (!rec) throw new Error('not recording');
    if (rec.phase === 'counting') return undo();
    return finish('stopped');
  }

  live.handlers.cam_record = async (c) => {
    const a = c.action || 'start';
    if (a === 'start') {
      const p = start(c);
      if (c.wait === false) { p.catch(() => {}); return { state: 'counting', name: c.name }; }
      return p;
    }
    if (a === 'stop') return stop();
    if (a === 'keep') return keep();
    if (a === 'redo') { const p = redo(); p.catch(() => {}); return { state: 'counting', name: rec && rec.name }; }
    if (a === 'undo') return undo();
    if (a === 'state') return state();
    throw new Error('action is start, stop, keep, redo, undo or state');
  };
  ed.addEventListener('switched', () => { if (rec) { try { undo(); } catch { rec = null; } } });
  return { start, stop, keep, redo, undo, state };
}
