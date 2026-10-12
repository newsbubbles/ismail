// Cues: a menu Claude attaches ahead of time to something the user will do, opened at the spot where it happens.
// The user, 2026-10-03: "pre-attach an ad-hoc menu to like some action of mine depending on whatever context you are
// literally in at the time ... cache something like a menu coming up when some action or event happens, at the spot
// of that event". A cue waits for one event of the page (anything live.emit sends: touch, menu, dropped, teleport,
// take_kept, waypoint_done, gesture ...) whose fields match, or for the user to walk near a point, then shows its
// panel there; the answer goes back as `cue_answer`, and a button can run a live command on the spot (`then`).
//   live: cue {cue?: its id, on, match?, near?: {position, radius}, title, text?, buttons, then?: {button: command}, once?, ttl_s?}
//         cue_remove {cue} | cues_clear | cues_list
//   on: an event type, or "near" (then `near` is required). match: {field: value} (strings match by substring).
//   Kept per scene in scenes/<scene>/cues.json, so a cue waits across reloads.
import * as THREE from 'three';
import { b2tPos } from './editor.js';

export function initCues(ed, live, panels) {
  const scn = () => ed.sceneName;
  let cues = [];
  let busy = false, saveT = null;

  const save = () => {
    clearTimeout(saveT);
    // the scene and the list as they are now: a switch inside the wait never writes one scene's cues into another
    const url = `cues?scene=${encodeURIComponent(scn())}`, body = JSON.stringify(cues);
    saveT = setTimeout(() => fetch(url, { method: 'POST', body }).catch(() => {}), 300);
  };
  async function load() {
    const j = await fetch(`cues?scene=${encodeURIComponent(scn())}`, { cache: 'no-store' }).then((r) => r.json()).catch(() => []);
    const now = Date.now();
    cues = (Array.isArray(j) ? j : []).filter((c) => !c.until || c.until > now);
  }
  const matches = (c, data) => Object.entries(c.match || {}).every(([k, v]) => {
    const d = data[k];
    return typeof v === 'string' && typeof d === 'string' ? d.includes(v) : JSON.stringify(d) === JSON.stringify(v);
  });

  // where the menu opens: beside the thing the event names, at a point it carries, else in front of the user
  function whereOf(c, data) {
    const name = data.item || data.object || data.person || data.it;
    const it = name && ed.byName.get(name);
    if (it) {
      const b = new THREE.Box3().setFromObject(it.obj), top = b.getCenter(new THREE.Vector3());
      top.y = Math.min(b.max.y + 0.15, ed.camera.getWorldPosition(new THREE.Vector3()).y);
      return top;
    }
    const p = data.to || data.position || (c.near && c.near.position);
    if (Array.isArray(p) && p.length === 3) return data.to ? new THREE.Vector3(p[0], p[1] + 1.3, p[2]) : b2tPos(p).add(new THREE.Vector3(0, 1.3, 0));
    return null;
  }

  async function fire(c, data) {
    if (busy) return;                                   // one cue at a time; the next event fires the next
    busy = true;
    if (c.once !== false) { cues = cues.filter((x) => x !== c); save(); }
    live.emit('cue_fired', { id: c.id, on: c.on });
    try {
      const near = whereOf(c, data);
      const a = await panels.show({ panel_id: 'cue_' + c.id + '_' + Date.now(), title: c.title || c.id, text: c.text || '',
        buttons: c.buttons && c.buttons.length ? c.buttons : ['OK'], width: c.width || 0.4, wait: true, ...(near ? { near } : {}) });
      const ans = a && (a.answer || a);
      const brief = Object.fromEntries(Object.entries(data).filter(([k, v]) => typeof v !== 'object' || Array.isArray(v)).slice(0, 8));
      live.emit('cue_answer', { id: c.id, answer: ans || null, on: c.on, event: brief });
      const cmd = ans && c.then && c.then[ans];
      if (cmd && live.handlers[cmd.type]) await live.handlers[cmd.type](cmd);
    } catch (e) {
      live.emit('voice_error', { where: 'cue ' + c.id, error: String(e.message || e) });
    } finally { busy = false; }
  }

  live.onEmit((type, data) => {
    if (type.startsWith('cue_') || !cues.length) return;
    const c = cues.find((x) => x.on === type && matches(x, data || {}));
    if (c) setTimeout(() => fire(c, data || {}), 0);
  });

  // "near": the user walks within `radius` of a point
  const eye = new THREE.Vector3();
  ed.preRender.push(function cuesNear() {
    if (!cues.length || busy) return;
    ed.camera.getWorldPosition(eye);
    for (const c of cues) {
      if (c.on !== 'near' || !c.near) continue;
      const p = b2tPos(c.near.position);
      if (Math.hypot(eye.x - p.x, eye.z - p.z) < (c.near.radius || 1.5)) { fire(c, { position: c.near.position }); break; }
    }
  });

  live.handlers.cue = (c) => {
    if (!c.on) throw new Error('cue needs `on` (an event type, or "near")');
    if (c.on === 'near' && !(c.near && c.near.position)) throw new Error('a "near" cue needs near.position');
    const cid = c.cue != null ? String(c.cue) : c.name != null ? String(c.name) : null;   // `id` is the server's command number
    const cue = { ...c, id: cid || 'cue_' + Date.now().toString(36) };
    delete cue.type; delete cue.ts; delete cue.cue;
    if (c.ttl_s) cue.until = Date.now() + c.ttl_s * 1000;
    cues = cues.filter((x) => x.id !== cue.id).concat(cue);
    save();
    return { id: cue.id, waiting: cues.length };
  };
  live.handlers.cue_remove = (c) => { const n = cues.length, cid = String(c.cue ?? c.name); cues = cues.filter((x) => x.id !== cid); save(); return { removed: n - cues.length }; };
  live.handlers.cues_clear = () => { const n = cues.length; cues = []; save(); return { cleared: n }; };
  live.handlers.cues_list = () => cues.map((c) => ({ id: c.id, on: c.on, title: c.title, match: c.match }));
  ed.addEventListener('switched', () => load());
  load();
  return { list: () => cues };
}
