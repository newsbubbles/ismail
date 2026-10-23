// "The stage was updated": when the page's code changes on the laptop, a quiet notice offers a reload instead of the
// user wondering which version they are running. Desktop: a small bar at the top with Reload. VR: a chime, a card
// over the left wrist, and a thumbs up with the left hand (held a moment) reloads. Unsaved edits are saved first.
// The reload leaves VR (a browser cannot re-enter VR without a tap), so the card says so.
import * as THREE from 'three';
import { GIZMO } from './editor.js';
import { noteText } from './receipts.js';

const THUMB_HOLD_MS = 700;

export function initUpdates(ed, live, hands, voice) {
  const { renderer, scene } = ed;
  const st = { ready: false, thumbSince: 0, going: false };

  async function reload(via) {
    if (st.going) return;
    st.going = true;
    live.emit('code_update', { state: 'reloading', via });
    try { if (ed.dirty && ed.dirty()) await ed.save(); } catch (_) { /* reload anyway: autosave history has it */ }
    try { if (voice && voice.abortSpeech) voice.abortSpeech(); } catch (_) { /* nothing in flight */ }
    try { const s = renderer.xr.getSession(); if (s) await s.end(); } catch (_) { /* not in VR */ }
    // a fresh URL, not reload(): twice the Quest's reload from VR hung on a blank page until the tab was closed
    const u = new URL(location.href);
    // the plain address: on the Quest a fresh ?r=<time> address hung on "loading" while ?scene=<name> loaded (the user,
    // 2026-10-03). The page and its modules are served no-store, so the plain address still brings the new code.
    u.searchParams.delete('r');
    setTimeout(() => location.replace(u.toString()), 250);   // the event above gets out first
  }

  // ---- desktop: a bar under the toolbar
  const bar = document.createElement('div');
  bar.id = 'updbar';
  bar.innerHTML = '<span>The stage was updated.</span><button type="button">Reload</button><button type="button" class="later">Later</button>';
  const css = document.createElement('style');
  css.textContent = `#updbar { position: fixed; left: 50%; top: 52px; transform: translate(-50%, -260%); z-index: 50; display: flex;
    gap: 10px; align-items: center; padding: 6px 8px 6px 14px; border-radius: 10px; background: #14532d; color: #ecfdf5;
    font: 13px system-ui, sans-serif; box-shadow: 0 6px 20px rgba(0,0,0,.35); transition: transform .35s ease; }
  #updbar.show { transform: translate(-50%, 0); }
  #updbar button { font: inherit; border: 0; border-radius: 7px; padding: 4px 12px; background: #22c55e; color: #052e16; cursor: pointer; }
  #updbar button.later { background: transparent; color: #bbf7d0; padding: 4px 6px; }`;
  document.head.appendChild(css);
  document.body.appendChild(bar);
  bar.querySelector('button').addEventListener('click', () => reload('button'));
  bar.querySelector('.later').addEventListener('click', () => bar.classList.remove('show'));

  // ---- what is waiting: updates.json, one entry per change landed ({t_ms, title, level}), written by update_note.py.
  // The card counts entries newer than the code this page runs and lists them, the most important first (up to
  // SHOW, then "+N more"; the user, q44: panels "listing what changed in each update"); the colour is the highest
  // level: green normal, amber important, orange critical (never red: red means recording).
  const LEVELS = { normal: 0, important: 1, critical: 2 };
  const COLOURS = [['rgba(20,83,45,0.92)', 'rgba(34,197,94,0.9)', '#14532d'], ['rgba(161,98,7,0.94)', 'rgba(250,204,21,0.9)', '#a16207'],
    ['rgba(194,65,12,0.94)', 'rgba(251,146,60,0.9)', '#c2410c']];
  const waiting = { n: 1, level: 0, top: '', titles: [] };
  const SHOW = 5, ROW = 38, HEAD = 76, FOOT = 50;
  async function readWaiting() {
    let list = [];
    try { const r = await fetch('updates.json', { cache: 'no-store' }); if (r.ok) list = (await r.json()).updates || []; } catch (_) { /* no log: one plain update */ }
    const since = Number(live.codeV.loaded) / 1e6;
    const fresh = list.filter((u) => u.t_ms > since);
    const rank = (u) => LEVELS[u.level] || 0;
    fresh.sort((a, b) => rank(b) - rank(a) || b.t_ms - a.t_ms);
    waiting.n = Math.max(1, fresh.length);
    waiting.level = fresh.length ? rank(fresh[0]) : 0;
    waiting.top = fresh.length ? fresh[0].title : '';
    waiting.titles = fresh.map((u) => u.title);
  }

  // ---- VR: a card over the left wrist
  const cv = document.createElement('canvas'); cv.width = 512; cv.height = HEAD + SHOW * ROW + ROW + FOOT;
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const card = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, toneMapped: false }));
  card.renderOrder = 999; card.layers.set(GIZMO); card.visible = false; card.scale.set(0.16, 0.055, 1);
  scene.add(card);
  function drawCard(progress) {
    const g = cv.getContext('2d'), [bg, fill] = COLOURS[waiting.level];
    const shown = waiting.titles.slice(0, SHOW), more = waiting.titles.length - shown.length;
    const h = HEAD + shown.length * ROW + (more > 0 ? ROW : 0) + FOOT;   // the card is as tall as its list
    card.scale.set(0.16, 0.16 * h / 512, 1);
    card.material.map.repeat.set(1, h / cv.height); card.material.map.offset.set(0, 1 - h / cv.height);
    g.clearRect(0, 0, 512, cv.height);
    g.fillStyle = bg; g.beginPath(); g.roundRect(2, 2, 508, h - 4, 22); g.fill();
    if (progress > 0) { g.fillStyle = fill; g.beginPath(); g.roundRect(2, 2, 508 * progress, h - 4, 22); g.fill(); }
    g.fillStyle = '#fff'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.font = 'bold 40px system-ui, sans-serif'; g.fillText(waiting.n > 1 ? waiting.n + ' updates ready' : 'Update ready', 256, 40);
    g.textAlign = 'left'; g.font = '26px system-ui, sans-serif';
    shown.forEach((t, i) => g.fillText('• ' + t, 22, HEAD + ROW * i + ROW / 2, 468));
    if (more > 0) { g.fillStyle = 'rgba(255,255,255,0.75)'; g.fillText(`+${more} more`, 22, HEAD + ROW * shown.length + ROW / 2, 468); }
    g.textAlign = 'center'; g.fillStyle = 'rgba(255,255,255,0.75)'; g.font = '24px system-ui, sans-serif';
    g.fillText((hands.gestures ? hands.gestures.words('reload') : 'left thumbs up') + ' to reload (leaves VR)', 256, h - FOOT / 2 - 2);
    tex.needsUpdate = true;
  }

  // the version this page runs: the time of the newest page file, e.g. "v1002.1623" (Oct 2, 16:23). Shown on the
  // desktop LIVE badge, in front of the user for a few seconds after entering VR, and sent to Claude as page_version.
  const vname = (ns) => { const d = new Date(Number(ns) / 1e6), p = (x) => String(x).padStart(2, '0');
    return `v${p(d.getMonth() + 1)}${p(d.getDate())}.${p(d.getHours())}${p(d.getMinutes())}`; };
  st.version = null;
  live.codeV.first.push((v) => {
    st.version = live.codeV.name || vname(v);      // the laptop's clock names it (the Quest's may be in another zone)
    live.emit('page_version', { version: st.version, code: String(v), in_vr: renderer.xr.isPresenting });
    const b = document.getElementById('livebadge') || document.querySelector('.live, #live');
    if (b) b.title = 'stage ' + st.version;
    const tag = document.createElement('div');
    tag.id = 'vertag'; tag.textContent = st.version;
    tag.style.cssText = 'position:fixed;left:12px;bottom:10px;font:11px ui-monospace,monospace;color:#9ca3af;opacity:.8;z-index:40;pointer-events:none';
    document.body.appendChild(tag);
  });
  // the card in front of the user on entering VR: the version, and who is listening (or that nobody is). It shows
  // again for a few seconds whenever that changes while they are in.
  const vcv = document.createElement('canvas'); vcv.width = 512; vcv.height = 150;
  const vtex = new THREE.CanvasTexture(vcv); vtex.colorSpace = THREE.SRGBColorSpace;
  const vcard = new THREE.Sprite(new THREE.SpriteMaterial({ map: vtex, depthTest: false, transparent: true, toneMapped: false }));
  vcard.renderOrder = 999; vcard.layers.set(GIZMO); vcard.visible = false; vcard.scale.set(0.2, 0.0586, 1);
  scene.add(vcard);
  let vUntil = 0, listenLine = '', nobody = false, serverNote = '', wasNobody = null;
  let noteTop = '', noteLine = '', noteWarn = false, noteUntil = 0;    // a receipt, a busy line or a stuck warning
  // a listener that gave no name reads as someone, not as a glitch (the user, 2026-10-04: "unnamed" looked suspicious)
  const listenText = (ls) => (ls.length ? 'listening: ' + ls.map((w) => (w === 'unnamed' ? 'an unnamed agent' : w)).join(', ') : 'nobody is listening');
  function drawV() {
    const g = vcv.getContext('2d');
    g.clearRect(0, 0, 512, 150);
    g.fillStyle = 'rgba(20,24,30,0.85)'; g.beginPath(); g.roundRect(2, 2, 508, 146, 20); g.fill();
    g.textAlign = 'center'; g.textBaseline = 'middle';
    const note = performance.now() < noteUntil;
    g.fillStyle = note && noteWarn ? '#fbbf24' : note ? '#86efac' : '#fff'; g.font = 'bold 40px system-ui, sans-serif';
    g.fillText(note ? noteTop : serverNote || 'stage ' + (st.version || '?'), 256, 46, 480);
    g.fillStyle = note ? '#e5e7eb' : nobody ? '#fbbf24' : '#86efac'; g.font = '30px system-ui, sans-serif';
    g.fillText(note ? noteLine : listenLine || 'checking who is listening', 256, 108, 480);
    vtex.needsUpdate = true;
  }
  renderer.xr.addEventListener('sessionstart', () => {
    drawV();
    vUntil = performance.now() + 5000;
    live.emit('page_version', { version: st.version, in_vr: true });
  });
  live.heard.on.push((ch) => {
    listenLine = listenText(ch.listening); nobody = !ch.listening.length;
    if (ch.server_changed) serverNote = 'the stage server restarted';
    const tag = document.getElementById('vertag');
    if (tag) { tag.textContent = (st.version || '') + ' \u00b7 ' + listenLine; tag.style.color = nobody ? '#fbbf24' : '#9ca3af'; }
    drawV();
    // in front of them again only when it matters: nobody listening now, or somebody again, or the server restarted;
    // a listener coming and going while others stay was showing it every half minute (the user, 2026-10-04, #4164)
    const flipped = wasNobody !== null && wasNobody !== nobody;
    wasNobody = nobody;
    if (!ch.first && renderer.xr.isPresenting && (flipped || ch.server_changed)) vUntil = performance.now() + 4000;
    if (ch.server_changed) { live.emit('server_changed', { server: live.heard.server }); setTimeout(() => { serverNote = ''; drawV(); }, 6000); }
  });

  // a note landed with a session ("heard by crossroads film"), a session said what it is busy with, or one looks
  // stuck: shown on this card for a few seconds (the user, 2026-10-11: four notes went unanswered while a session sat on
  // a render, and he could not tell landed from lost)
  live.heard.noteOn.push(({ receipt, busy, first }) => {
    const t = noteText(receipt, busy);
    if (!t) return;
    // shown again only for news: another top line, or a busy line that is new (not one that just ended)
    const news = t[0] !== noteTop || (t[1] && t[1] !== noteLine);
    [noteTop, noteLine, noteWarn] = t;
    if (!news) return;
    const tag = document.getElementById('vertag');
    if (tag) tag.title = noteTop + (noteLine ? ' \u00b7 ' + noteLine : '');
    if (first || !renderer.xr.isPresenting) return;
    noteUntil = performance.now() + (noteWarn ? 8000 : 4500);
    vUntil = Math.max(vUntil, noteUntil);
    drawV();
    setTimeout(drawV, noteUntil - performance.now() + 50);    // back to the version and who is listening
  });

  live.codeV.on.push(async () => {
    const was = st.ready ? waiting.level : -1;
    await readWaiting();
    st.ready = true;
    bar.querySelector('span').textContent = (waiting.n > 1 ? waiting.n + ' updates ready' : 'The stage was updated.') + (waiting.top ? ' ' + waiting.top : '');
    bar.style.background = COLOURS[waiting.level][2];
    bar.classList.add('show');
    drawCard(0);
    live.emit('updates_waiting', { count: waiting.n, level: Object.keys(LEVELS)[waiting.level], top: waiting.top, titles: waiting.titles });
    if (renderer.xr.isPresenting && waiting.level > was) voice.EAR.incoming();   // a chime when it first shows or gets more important
  });

  // per XR frame
  const up = new THREE.Vector3(0, 1, 0);
  const tmpV = new THREE.Vector3(), fwd = new THREE.Vector3();
  function update() {
    vcard.visible = performance.now() < vUntil;
    if (vcard.visible) {
      ed.camera.getWorldPosition(tmpV); ed.camera.getWorldDirection(fwd);
      vcard.position.copy(tmpV).addScaledVector(fwd, 0.7).y -= 0.12;
    }
    const G = hands.gestures, h = hands.state[G.handOf('reload')];   // the card sits over the reload's hand (gestures.js)
    card.visible = st.ready && !!(h && h.f);
    if (!card.visible) { st.thumbSince = 0; return; }
    card.position.copy(h.f.wrist).addScaledVector(up, 0.2);
    const now = performance.now();
    if (G.on('reload') && !hands.performing) {          // perform.js: gestures off while performing
      if (!st.thumbSince) st.thumbSince = now;
      const k = Math.min(1, (now - st.thumbSince) / G.hold('reload', THUMB_HOLD_MS));
      drawCard(k);
      if (k >= 1) reload(G.words('reload'));
    } else if (st.thumbSince) { st.thumbSince = 0; drawCard(0); }
  }

  return { update, reload, st };
}
