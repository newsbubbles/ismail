// What can be done with the selected thing, in VR: a small menu beside it (the user, 2026-10-02: selecting is free,
// but moving or acting on something takes an unlock, "some menu that pops up next to it so that we can freestyle on
// what can be done without having to map it all out in gestures"). Selecting a movable thing opens it:
//   Move   unlocks that one thing: hands and ray can then carry it, until it is deselected
//   Drop   it stands up and falls onto what is under it (editor.js drop)
//   Take   records a take for it (head, both hands, the mic) named after it; the button turns into Stop
//   x      deselects
// A selection lets go by itself after idle seconds x metres away > 25 (25 s at arm's length, 8 s from 3 m), so
// nothing stays selected (and unlocked) while the user walks off. Events: menu {object, action}, deselect_idle.
import * as THREE from 'three';

const IDLE_METRE_SECONDS = 25;
const FOLLOW_COUNTDOWN_S = 3;          // a Follow from the menu counts down first (the user, 2026-10-05: see below)
const IDLE_MENU_METRE_SECONDS = 60;   // while its menu is up the user may be reading it (14 s at 1.7 m was too quick)

export function initActions(ed, hands, panels, live, takes) {
  const { camera } = ed;
  let open = null;                        // { it, id }
  let busy = false;
  let lastUse = performance.now();
  const touchUse = () => { lastUse = performance.now(); };

  const movable = (it) => it && !it.big && !it.light && (!it.aimed || it.type === 'CAMERA') && !(it.path && it.path.some((x) => x.man && x.man.locked));
  function besideOf(it) {
    // in front of the user, turned a little toward the thing, a hand below the eyes: beside the thing it was out of
    // view when the user stood close (the menu for a dancer was open and never seen)
    const b = new THREE.Box3().setFromObject(it.obj), c = b.getCenter(new THREE.Vector3());
    const head = camera.getWorldPosition(new THREE.Vector3());
    const look = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    const to = c.clone().sub(head).setY(0);
    const dir = to.lengthSq() > 1e-4 ? look.clone().lerp(to.normalize(), 0.35).normalize() : look;
    const p = head.clone().addScaledVector(dir, 0.55);
    p.y = head.y - 0.18;
    return p;
  }
  // ---- a countdown before a Follow starts (the user, 2026-10-05, in a kept take: the Follow took its offset from
  // his pose the moment he pressed it, hands down on the bar, while Sam's were up and to the right: "kind of makes it
  // impossible"). The person stays as they are while the numbers count, so the user can take their pose; at GO the
  // Follow starts from where the user is then (and the performance clock with it), with a higher tick
  const cdCanvas = document.createElement('canvas');
  cdCanvas.width = 256; cdCanvas.height = 256;
  const cdTex = new THREE.CanvasTexture(cdCanvas);
  cdTex.colorSpace = THREE.SRGBColorSpace;
  const cdSprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: cdTex, depthTest: false, transparent: true, toneMapped: false }));
  cdSprite.renderOrder = 999; cdSprite.visible = false; cdSprite.scale.set(0.16, 0.16, 1);
  ed.scene.add(cdSprite);
  function cdDraw(text, go) {
    const g = cdCanvas.getContext('2d');
    g.clearRect(0, 0, 256, 256);
    g.fillStyle = go ? 'rgba(22,163,74,0.9)' : 'rgba(16,18,22,0.85)';
    g.beginPath(); g.arc(128, 128, 120, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#fff'; g.font = `bold ${go ? 92 : 150}px system-ui, sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(text, 128, 136);
    cdTex.needsUpdate = true;
  }
  function cdPlace() {                       // in front of the user, a little below the eyes, where they look
    const h = camera.getWorldPosition(new THREE.Vector3()), f = camera.getWorldDirection(new THREE.Vector3());
    cdSprite.position.copy(h).addScaledVector(f, 0.7).y -= 0.08;
  }
  async function countdown(seconds = FOLLOW_COUNTDOWN_S, person = '') {
    const n = Math.max(0, Math.round(seconds));
    if (!n) return;
    live.emit('follow_countdown', { person, seconds: n });
    cdSprite.visible = true;
    const follow = setInterval(cdPlace, 30);
    try {
      for (let k = n; k > 0; k--) {
        cdDraw(String(k), false); cdPlace();
        if (takes.ear && takes.ear.tick) takes.ear.tick(false);
        await new Promise((res) => setTimeout(res, 1000));
      }
      cdDraw('GO', true); cdPlace();
      if (takes.ear && takes.ear.tick) takes.ear.tick(true);
      setTimeout(() => { cdSprite.visible = false; }, 600);
    } finally { clearInterval(follow); }
  }

  // takes kept for a thing (name -> take id): the newest take named after it whose meta says kept
  const kept = new Map();
  const scn = () => ed.sceneName;                      // live: scenes.js can switch it
  const scanKept = () => { kept.clear(); return fetch(`takes?scene=${encodeURIComponent(scn())}`).then((r) => r.json()).then(async (l) => {
    for (const t of (Array.isArray(l) ? l : []).sort((a, b) => String(a.id).localeCompare(String(b.id)))) {
      const m = await fetch(`scenes/${encodeURIComponent(scn())}/takes/${encodeURIComponent(t.id)}/meta.json`, { cache: 'no-store' })
        .then((r) => r.json()).catch(() => null);
      if (m && m.kept && m.for) kept.set(m.for, t.id);
    }
  }).catch(() => {}); };
  scanKept();
  ed.addEventListener('switched', () => scanKept());   // each scene has its own takes
  const canAct = (it) => !!(takes.actors && takes.actors.canPlay(it.name));
  function buttons(it) {
    const moving = ed.unlocked === it, rec = takes.recording(), playing = takes.actors && takes.actors.playing.has(it.name);
    const b = [moving ? '✓ Done' : '✋ Move'];
    if (it.type === 'CAMERA') return ['📷 My eyes', ...b, '◆ Key', moved(it) ? '✕ Cancel' : '✕'];   // a camera only moves and keys (no take, no drop)
    const following = playing && takes.actors.playing.get(it.name).live;
    if (kept.has(it.name) && !following) b.push(playing ? '■ Stop' : '▶ Play');
    if (canAct(it) && !rec) b.push(following ? '■ Unfollow' : '◎ Follow');   // they move with you, live
    b.push(rec ? '■ Stop take' : '● Take', '◆ Key', '📌 Pin', '⬇ Drop', moved(it) ? '✕ Cancel' : '✕');
    return b;
  }
  // after a take: the 4D view in front of the user, and keep or redo (a right thumbs up / down answers too)
  async function review(it, r) {
    const id = r && (r.take || r.id);
    if (!id) return;
    // the take plays on the person while the user decides (the user, 2026-10-03: "while looking at the take preview
    // whether or not to keep... we can see the take played on the thing in question"); the 4D view is a button now
    const onBody = canAct(it) && takes.actors;
    let view4d = !onBody, slow = false, a = null, trim = null;    // trim: [t0, t1] in the take's own seconds
    const playIt = () => onBody && takes.actors.play({ person: it.name, take: id, loop: true, rate: slow ? 0.5 : 1, trim: trim || null })
      .catch((e) => live.emit('voice_error', { where: 'review play', error: String(e.message || e) }));
    const showView = async () => {
      if (!takes.view) return;
      if (view4d) { try { await takes.view.show({ id, layout: 'strip', n: 8 }); } catch (e) { live.emit('voice_error', { where: 'take view', error: String(e.message || e) }); } }
      else takes.view.clear();
    };
    await playIt();
    await showView();
    for (;;) {
      // trimming (the user, 2026-10-03: "you should be able to clip the take ... sometimes I'm reaching for a menu"):
      // while it loops, Start here / End here mark the kept part at the moment playing; the loop then plays only that
      const span = trim ? `Kept part: ${trim[0].toFixed(1)} s to ${trim[1].toFixed(1)} s. ` : '';
      a = await panels.show({ panel_id: 'review_' + Date.now(), title: `Keep this take for ${ed.label(it)}?`,
        text: span + (onBody ? 'Playing on them now. Mark Start here and End here while it loops to keep only that part.' : 'Kept with their name (no body to play it on yet).'),
        buttons: ['👍 Keep', '👎 Redo', ...(onBody ? [slow ? '▶ Normal' : '🐢 Slow', '⟦ Start here', 'End here ⟧', ...(trim ? ['↺ Whole take'] : [])] : []),
          view4d ? '4D off' : '4D'], width: 0.5 });
      const x = a && a.answer;
      if (x === '⟦ Start here' || x === 'End here ⟧') {
        const now = takes.actors.at(it.name);
        if (now) {
          const full = trim || [now.t0, now.t1];
          trim = x === '⟦ Start here' ? [now.t, Math.max(now.t + 0.5, full[1])] : [full[0], Math.max(full[0] + 0.5, now.t)];
          live.emit('take_trim', { take: id, trim });
          await playIt();
        }
        continue;
      }
      if (x === '↺ Whole take') { trim = null; await playIt(); continue; }
      if (x === '4D' || x === '4D off') { view4d = !view4d; await showView(); continue; }
      if (x === '🐢 Slow' || x === '▶ Normal') { slow = !slow; await playIt(); continue; }
      break;
    }
    if (takes.view) takes.view.clear();
    if (onBody) takes.actors.stop({ person: it.name });
    if (a && a.answer === '👍 Keep') {
      kept.set(it.name, id);
      fetch(`take/meta?scene=${encodeURIComponent(scn())}&take=${encodeURIComponent(id)}`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ for: it.name, kept: true, trim }) }).catch(() => {});
      live.emit('take_kept', { object: it.name, take: id });
    }
  }
  // while a person follows the user: a small panel where the user stood when it began (it stays there; the menu that
  // opened Follow goes away and the person may be out of reach: the user, 2026-10-03, "the follow menu disappeared ...
  // I can't find it"). Stop ends the follow (and a take recording); Bring along / Leave him sets whether a teleport
  // carries the person too. A walk-away stop (actors.js) closes it and stops the take with a review.
  const followPanels = new Map();
  async function followPanel(it) {
    if (!takes.actors || followPanels.has(it.name)) return;
    const head = camera.getWorldPosition(new THREE.Vector3()), look = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
    const near = head.clone().addScaledVector(look, 0.55).add(new THREE.Vector3(look.z, 0, -look.x).multiplyScalar(0.35));
    near.y = head.y - 0.35;
    const id = 'follow_' + it.name + '_' + Date.now();
    let round = 0, cur = id, more = false;
    followPanels.set(it.name, id);
    try {
      while (takes.actors.playing.get(it.name)?.live) {
        const st = takes.actors.playing.get(it.name), rec = takes.recording();
        // four buttons, the rest under More (the user, 2026-10-08: "there was like a lot of different buttons on the menu.
        // I don't really know what each of them did"): the setup comes from the body (actors.setupOf) and the panel says
        // it in a sentence; Stop, walk or stay, turn, More. More: mirror, pin the hips, the mic, move the spot (not while
        // a take records: a jump would be in the take), Back.
        // a fresh panel id each round: re-showing the one that was closing made the panel vanish after Turn (the user,
        // 2026-10-03: "when I clicked turn him the menu disappeared")
        cur = id + '_' + (++round);
        followPanels.set(it.name, cur);
        const pinned = takes.actors.pinsOf ? takes.actors.pinsOf(it.name) : [];
        const setup = takes.actors.setupOf ? takes.actors.setupOf(it.name) : {};
        // a Follow is a performance (perform.js): the voice records with it and gestures are off, so the buttons are poked
        const pf = takes.perform ? takes.perform.state() : { performing: false };
        const ctl = takes.actors.control ? takes.actors.control.summary(it.name) : '';   // control.js drives
        const mic = pf.performing ? (pf.mic ? '🎙 Your voice records with it.' : '🎙 Mic off.') : '';
        const feet = pinned.filter((p) => p !== 'hips');
        const text = [setup.says || '', feet.length ? '📌 pinned: ' + feet.join(', ') + '.' : '', ctl ? '🎛 ' + ctl + '.' : '', mic].filter(Boolean).join(' ');
        const buttons = more
          ? [st.mirror ? '⇄ Mirror: on' : '⇄ Mirror: off', pinned.includes('hips') ? '📌 Unpin hips' : '📌 Pin hips',
            ...(pf.performing ? [pf.mic ? '🎙 Mic off' : '🎙 Mic on'] : []), ...(rec ? [] : ['⇲ Move him here']), '← Back']
          : [rec ? '■ Stop take' : '■ Stop', st.mode === 'walk' ? '📍 Stay on the spot' : '🚶 Walk with me', '⟲ Turn him', '⋯ More'];
        const r = await panels.show({ panel_id: cur, title: (rec ? '● Recording: ' : 'Following you: ') + ed.label(it),
          text, buttons, near, width: 0.46, quiet: true, wait: true });
        const ans = r && r.answer;
        if (!ans) break;
        if (ans === '⋯ More' || ans === '← Back') { more = ans === '⋯ More'; continue; }
        if (ans === '🎙 Mic off' || ans === '🎙 Mic on') {
          try { await (ans === '🎙 Mic off' ? takes.perform.micOff('user') : takes.perform.clipStart('user')); }
          catch (e) { live.emit('voice_error', { where: 'performance mic', error: String(e.message || e) }); }
          continue;
        }
        if (ans === '📌 Pin hips' || ans === '📌 Unpin hips') {
          try { takes.actors.anchor({ person: it.name, joint: 'hips', ...(ans === '📌 Pin hips' ? { to: 'here' } : { clear: true }) }); }
          catch (e) { live.emit('voice_error', { where: 'pin', error: String(e.message || e) }); }
          continue;
        }
        if (ans === '⟲ Turn him') { takes.actors.turnBy(it.name, 45); continue; }
        if (ans.startsWith('⇄ Mirror')) { takes.actors.setMirror(it.name, !st.mirror); continue; }
        if (ans === '🚶 Walk with me' || ans === '📍 Stay on the spot') { takes.actors.setMode(it.name, ans === '🚶 Walk with me' ? 'walk' : 'place'); continue; }
        if (ans === '⇲ Move him here') {                          // a metre in front of the user, on the floor
          const h = camera.getWorldPosition(new THREE.Vector3()), l = camera.getWorldDirection(new THREE.Vector3()).setY(0).normalize();
          takes.actors.moveTo(it.name, h.addScaledVector(l, 1.0));
          more = false;
          continue;
        }
        if (ans === '■ Stop take') { takes.actors.stop({ person: it.name }); const t = await takes.stop(); await review(it, t); }
        else takes.actors.stop({ person: it.name });
        break;
      }
    } finally { followPanels.delete(it.name); }
  }
  // after a plain Follow: keep it as a take? (hands.js buffered it; the user, 2026-10-04, lost a liked follow)
  let offerId = null;
  live.onEmit((type) => { if (type === 'follow_kept' && offerId) panels.close(offerId, 'kept by an agent'); });   // stage_take_keep_last
  async function offerKeep(person) {
    const lf = takes.lastFollow && takes.lastFollow();
    const it = ed.byName.get(person);
    if (!lf || !it || lf.person !== person) return;
    const pid = 'keep_follow_' + Date.now();
    offerId = pid;
    // it plays back first, with the voice (the user, 2026-10-04: after a Follow the performance plays back before
    // anything else), and loops while the user decides
    const lfd = takes.lastFollowData && takes.lastFollowData();
    const back = !!(lfd && canAct(it)) && await takes.actors.play({ person, frames: lfd.frames, meta: { ...(lfd.perf || {}), mirror: takes.actors.mirrorOf(person) }, take: 'last follow', loop: true })
      .then(() => true).catch((e) => { live.emit('voice_error', { where: 'follow playback', error: String(e.message || e) }); return false; });
    const a = await panels.show({ panel_id: pid, title: `Keep that follow of ${ed.label(it)}?`,
      text: `${lf.seconds.toFixed(0)} s${back ? ', playing back on them now' : ''}. Keep it as a take to trim it, or discard it.`,
      buttons: ['💾 Keep as take', '🗑 Discard'], width: 0.5, quiet: true, seconds: 120 });
    if (back && takes.actors.playing.get(person)?.take === 'last follow') takes.actors.stop({ person });
    const x = a && a.answer;
    if (x === '💾 Keep as take') {
      try { const t = await takes.keepLast(person); await review(it, t); } catch (e) { live.emit('voice_error', { where: 'keep follow', error: String(e.message || e) }); }
    } else if (x === '🗑 Discard') takes.discardLast();
  }
  live.onEmit((type, d) => {
    if (type !== 'actor_stop' || !d || !d.live) return;
    const id = followPanels.get(d.person);
    if (id) panels.close(id, 'follow ended');
    if (!takes.recording()) setTimeout(() => offerKeep(d.person), 0);   // after hands.js has buffered it (main.js order)
    if (d.why === 'walked_away' && takes.recording()) {
      const it = ed.byName.get(d.person);
      setTimeout(async () => { const t = await takes.stop(); if (it) await review(it, t); }, 0);
    }
  });

  async function show(it) {
    if (!ed.renderer.xr.isPresenting || !movable(it) || busy) return;
    busy = true;
    start = { it, p: it.obj.position.clone(), q: it.obj.quaternion.clone(), s: it.obj.scale.clone() };
    try {
      while (ed.selected === it) {
        const id = 'actions_' + Date.now();
        open = { it, id };
        const bs = buttons(it);
        const r = await panels.show({ panel_id: id, title: ed.label(it) + (ed.unlocked === it ? '  (unlocked)' : ''), buttons: bs,
          near: besideOf(it), width: bs.length > 4 ? 0.42 : 0.34, quiet: true });
        open = null;
        const a = r && r.answer;
        if (!a) break;                                            // closed (deselected, replaced, timed out)
        touchUse();
        live.emit('menu', { object: it.name, action: a });
        if (a === '📷 My eyes') { fromEyes(it); break; }
        if (a === '✋ Move') { ed.unlocked = it; ed.setStatus(it.name + ' unlocked: grab to move it'); }
        else if (a === '✓ Done') { ed.unlocked = null; ed.select(null, 'menu'); break; }
        else if (a === '⬇ Drop') ed.drop(it, 'menu');
        else if (a === '📌 Pin' && window.VR_waypoints) { window.VR_waypoints.pinObject(it); ed.setStatus('pinned ' + ed.label(it) + ': say the note'); break; }   // the next voice note is its note
        else if (a === '◆ Key' && takes.clock) { takes.clock.show(true); takes.clock.key(it.name); }   // clock.js: a key at the playhead
        else if (a === '● Take') {                              // a person follows the user while the take records
          if (canAct(it)) { ed.select(null, 'menu'); await countdown(FOLLOW_COUNTDOWN_S, it.name); }
          if (canAct(it)) await takes.actors.follow({ person: it.name }).catch(() => null);
          takes.start(it.name);
          if (canAct(it)) { followPanel(it); ed.select(null, 'menu'); break; }
        }
        else if (a === '■ Stop take') {
          if (takes.actors && takes.actors.playing.get(it.name)?.live) takes.actors.stop({ person: it.name });
          const r = await takes.stop(); await review(it, r);
        }
        else if (a === '◎ Follow' && takes.actors) {
          ed.select(null, 'menu');                                // the menu goes; the numbers count; GO starts it
          await countdown(FOLLOW_COUNTDOWN_S, it.name);
          const ok = await takes.actors.follow({ person: it.name }).then(() => true).catch((e) => {
            live.emit('voice_error', { where: 'actor follow', error: String(e.message || e) }); return false; });
          if (ok) { followPanel(it); ed.select(null, 'menu'); break; }
        }
        else if (a === '■ Unfollow' && takes.actors) takes.actors.stop({ person: it.name });
        else if (a === '▶ Play' && takes.actors) await takes.actors.play({ person: it.name, take: kept.get(it.name) }).catch((e) =>
          live.emit('voice_error', { where: 'actor play', error: String(e.message || e) }));
        else if (a === '■ Stop' && takes.actors) takes.actors.stop({ person: it.name });
        else if (a === '✕' || a === '✕ Cancel') {               // ✕ undoes whatever was done since this menu opened
          if (moved(it)) {
            ed.beginEdit('menu cancel');
            it.obj.position.copy(start.p); it.obj.quaternion.copy(start.q); it.obj.scale.copy(start.s);
            ed.endEdit();
            ed.setStatus(ed.label(it) + ': put back');
          }
          ed.unlocked = null; ed.select(null, 'menu'); break;
        }
        await new Promise((res) => setTimeout(res, 650));         // the panel's own close, then the next state
      }
    } finally { busy = false; }
  }
  // a camera framed by the user's own eyes: the menu closes, 3 s to look at the shot, a shutter, and the camera stands
  // where the eyes are, aimed where they look (one undoable edit; the menu's Cancel puts it back). The user, 2026-10-03:
  // camera angles are easy on the desktop and not in VR.
  function fromEyes(it) {
    ed.select(null, 'menu');
    live.emit('menu', { object: it.name, action: 'my eyes: 3 s' });
    // a countdown you can hear (the user: everything designed in VR should also be designed as sound)
    if (takes.ear && takes.ear.tick) for (const k of [0, 1, 2]) setTimeout(() => takes.ear.tick(k === 2), k * 1000);
    setTimeout(() => {
      const p = new THREE.Vector3(), q = new THREE.Quaternion(), pq = new THREE.Quaternion();
      ed.camera.getWorldPosition(p); ed.camera.getWorldQuaternion(q);
      const o = it.obj;
      o.parent.updateMatrixWorld(true);
      ed.beginEdit('camera from eyes');
      o.position.copy(o.parent.worldToLocal(p.clone()));
      o.quaternion.copy(o.parent.getWorldQuaternion(pq).invert().multiply(q));
      ed.endEdit();
      if (takes.ear) (takes.ear.shutter || takes.ear.snap)();
      const b = ed.blenderTransform(it);
      live.emit('camera_set', { camera: it.name, via: 'my eyes', location: b.location, quaternion: b.quaternion });
    }, 3000);
  }
  let start = null;                                               // the selected thing as it was when its menu opened
  function moved(it) {
    if (!start || start.it !== it) return false;
    const o = it.obj;
    return o.position.distanceTo(start.p) > 1e-5 || o.quaternion.angleTo(start.q) > 1e-3 || o.scale.distanceTo(start.s) > 1e-5;
  }
  ed.addEventListener('select', (e) => {
    touchUse();
    if (ed.unlocked && ed.unlocked !== ed.selected) ed.unlocked = null;
    if (open && open.it !== ed.selected) panels.close(open.id, 'deselected');
    if (ed.selected && window.VR_behaviours && window.VR_behaviours.onSelect(ed.selected, e.via)) return;   // it does its own thing (behaviours.js)
    if (ed.selected && ed.selected !== e.prev) show(ed.selected);
  });
  ed.addEventListener('edited', touchUse);

  const head = new THREE.Vector3(), box = new THREE.Box3(), c = new THREE.Vector3();
  function update() {
    const it = ed.selected;
    if (!it || ed.editing || !ed.renderer.xr.isPresenting) return;
    camera.getWorldPosition(head);
    box.setFromObject(it.obj).getCenter(c);
    const d = Math.max(0.7, c.distanceTo(head));
    if ((performance.now() - lastUse) / 1000 * d > (open ? IDLE_MENU_METRE_SECONDS : IDLE_METRE_SECONDS)) {
      live.emit('deselect_idle', { object: it.name, metres: +d.toFixed(1), seconds: Math.round((performance.now() - lastUse) / 1000) });
      ed.select(null, 'idle');
    }
  }
  return { update, show, touchUse, countdown, followPanel };
}
