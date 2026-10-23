// The voice channel for the headset, where there is no screen to read: earcons for every state change, voice notes
// from the mic (the phone gesture held near the head, or Claude's `listen` command) transcribed on the laptop into
// events Claude reads, Claude's replies spoken (speakwright's Kokoro), the mic recorded into each take, and snapshots
// of what one eye sees (`snap` in XR, `eyecam` for a short timelapse).
import * as THREE from 'three';

const HOLD_PHONE_MS = 350;       // the phone gesture (anywhere: tracking drops out near the face) held this long
const LET_GO_MS = 600;           // ... and it can drop out this long before the note is sent
const MAX_NOTE_S = 90, MAX_LOCKED_S = 300, SNAP_EVERY_MS = 6000;
const LOCK_MS = 10000;           // the phone gesture held this long after the note starts locks the call (hands free); the user asked for ~10 s

export function initVoice(ed, hands, live) {
  const { renderer, scene, camera } = ed;
  const qs_ = () => 'scene=' + encodeURIComponent(ed.sceneName);   // read when sent: a switch never leaves it behind
  let ac = null, stream = null, micErr = null;

  // ---- sound out: earcons and speech, all through one context
  function audio() {
    if (!ac) ac = new (window.AudioContext || window.webkitAudioContext)();
    if (ac.state === 'suspended') ac.resume();
    return ac;
  }
  function tone(seq, gain = 0.18) {          // seq: [[freq Hz, seconds], ...]
    try {
      const a = audio();
      let t = a.currentTime + 0.01;
      for (const [f, d] of seq) {
        const o = a.createOscillator(), g = a.createGain();
        o.type = 'sine'; o.frequency.setValueAtTime(f, t);
        g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(gain, t + 0.012); g.gain.exponentialRampToValueAtTime(0.0008, t + d);
        o.connect(g).connect(a.destination); o.start(t); o.stop(t + d + 0.02);
        t += d * 0.9;
      }
    } catch (_) { /* no audio yet */ }
  }
  // earcons designed in ismail (songs/crossroads/earcons, sound_make) and served from sounds/; the sine tones are the
  // fallback while a file loads or if it is missing
  const bank = {};
  async function load(name) {
    try { bank[name] = await audio().decodeAudioData(await (await fetch(`sounds/${name}.wav`)).arrayBuffer()); } catch (_) { bank[name] = null; }
  }
  function play(name, gain, fallback, rate = 1) {
    const b = bank[name];
    if (!b) { if (bank[name] === undefined && ac) load(name); return fallback(); }
    const a = audio(), src = a.createBufferSource(), g = a.createGain();
    g.gain.value = gain; src.buffer = b; src.playbackRate.value = rate; src.connect(g).connect(a.destination); src.start();
  }
  const EAR = {
    recStart: () => play('rec_start', 0.35, () => tone([[523, 0.09], [784, 0.12]])),
    recStop: () => play('rec_stop', 0.35, () => tone([[784, 0.09], [523, 0.12]])),
    sent: () => play('sent', 0.3, () => tone([[1047, 0.05], [1319, 0.07]], 0.12)),
    takeStart: () => play('take_start', 0.4, () => tone([[392, 0.1], [392, 0.1], [587, 0.2]], 0.2)),
    takeStop: () => play('take_stop', 0.4, () => tone([[587, 0.1], [392, 0.25]], 0.2)),
    incoming: () => play('incoming', 0.3, () => tone([[880, 0.06], [1175, 0.09]], 0.1)),
    error: () => play('error', 0.35, () => tone([[180, 0.25]], 0.25)),
    snap: () => play('snap', 0.3, () => tone([[1568, 0.04]], 0.1)),
    pinOpen: () => play('pin_open', 0.32, () => tone([[1760, 0.05]], 0.1)),         // a waypoint's note opens (earcons/make_pin.py)
    pinDone: () => play('pin_done', 0.32, () => tone([[1319, 0.05], [1760, 0.08]], 0.1)),
    tick: (last) => tone(last ? [[1319, 0.12]] : [[784, 0.06]], 0.14),   // a camera countdown: three ticks, the last one higher
    // the user's shot: one of 8 shutter variants (ismail), at its own pitch and level each time
    // Claude heard a voice note: a recorded "got it" (sounds/ack_k.wav, Kokoro once), played at once, not spoken live:
    // live speech waits for the CPU and for the user to stop talking, and a minute-late "got it" means nothing
    // not over a line already playing (the user: things piled up in their ears); that line is the answer anyway
    ack: () => (speaking || saying ? null : play('ack_' + Math.floor(Math.random() * 7), 0.8, () => tone([[880, 0.05], [1320, 0.07]], 0.15), 0.97 + 0.06 * Math.random())),
    shutter: () => play('shutter_' + Math.floor(Math.random() * 8), 0.3 * (0.85 + 0.3 * Math.random()),
      () => tone([[1568, 0.03], [1175, 0.04]], 0.12), 0.93 + 0.14 * Math.random()),
  };
  const loadAll = () => { for (const n of ['rec_start', 'rec_stop', 'sent', 'take_start', 'take_stop', 'incoming', 'error', 'snap', 'pin_open', 'pin_done', ...[0, 1, 2, 3, 4, 5, 6, 7].map((k) => 'shutter_' + k), ...[0, 1, 2, 3, 4, 5, 6].map((k) => 'ack_' + k)]) if (!(n in bank)) load(n); };
  // is the headset on the user's head? The XR session goes 'hidden' when it comes off (the proximity sensor) and
  // 'visible' again when it is back on; speech waits for 'visible' so nothing is said to an empty room
  const head = { on: true };
  renderer.xr.addEventListener('sessionstart', () => {
    const ss = renderer.xr.getSession();
    head.on = true;
    ss.addEventListener('visibilitychange', () => {
      head.on = ss.visibilityState !== 'hidden';             // visible-blurred: the Quest menu is over the page, still on
      live.emit('headset', { state: ss.visibilityState === 'hidden' ? 'off' : ss.visibilityState === 'visible' ? 'on' : 'menu open', xr: ss.visibilityState });
    });
  });
  renderer.xr.addEventListener('sessionend', () => { head.on = true; live.emit('headset', { state: 'left VR' }); });
  let speechAbort = null;                          // the TTS request in flight (updates.js aborts it before a reload)
  const sayQ = [];
  let speaking = false, saying = null;          // saying: the line playing now {text, voice, src}
  // the user starts a voice note: stop talking at once, and say the interrupted line again after the note
  // Where the user cut in matters: what they heard and what they missed. The cut point is estimated by time (the
  // share of the clip played maps to the share of the text), and the line is said again from the start of the sentence
  // that was cut, not from the top.
  function hush() {
    if (!saying || !saying.src) return;
    const t = saying.text, played = Math.max(0, ac.currentTime - saying.t0), dur = saying.dur || 1;
    let k = Math.min(t.length, Math.round(t.length * Math.min(1, played / dur)));
    const sentence = Math.max(0, ...['. ', '? ', '! ', ': '].map((m) => t.lastIndexOf(m, k - 1) + (t.lastIndexOf(m, k - 1) >= 0 ? m.length : 0)));
    const sp = t.lastIndexOf(' ', k); if (sp > 0 && k < t.length) k = sp;
    sayQ.unshift([t.slice(sentence), saying.voice, saying.at]);
    saying.cut = true;
    try { saying.src.stop(); } catch (_) { /* already ended */ }
    live.emit('voice_hushed', { heard: t.slice(0, k), missed: t.slice(k), at_s: +played.toFixed(1), of_s: +dur.toFixed(1),
      will_repeat_from: t.slice(sentence, sentence + 60) });
  }
  // the user is talking, or about to, or has just stopped: a phone gesture forming, a note recording, or less than
  // AFTER_NOTE_MS since the last one ended (they often send the next note at once). Speech and new panels wait for it.
  const AFTER_NOTE_MS = 3000;
  const talking = () => !!(note.rec || note.starting || phoneSince || performance.now() - (note.endedAt || -1e9) < AFTER_NOTE_MS);
  // the headset's own speech engine, for when the server's voice (speakwright) is off (the user turned it off on
  // 2026-10-05 and then heard nothing from the agents in VR). Resolves the seconds spoken; rejects when this browser
  // has no engine or no voice, or the line does not finish
  function browserSay(t) {
    const S = window.speechSynthesis;
    if (!S || !window.SpeechSynthesisUtterance) return Promise.reject(new Error('no speech engine in this browser'));
    const voices = S.getVoices();
    return new Promise((res, rej) => {
      const u = new SpeechSynthesisUtterance(t), t0 = performance.now();
      const en = voices.find((v) => /^en/i.test(v.lang) && v.localService) || voices.find((v) => /^en/i.test(v.lang));
      if (en) u.voice = en;
      u.rate = 1.0;
      const giveUp = setTimeout(() => { S.cancel(); rej(new Error('the headset speech did not finish (' + voices.length + ' voices)')); }, 8000 + t.length * 120);
      u.onend = () => { clearTimeout(giveUp); res((performance.now() - t0) / 1000); };
      u.onerror = (e) => { clearTimeout(giveUp); rej(new Error('headset speech: ' + (e.error || 'error') + ' (' + voices.length + ' voices)')); };
      S.speak(u);
    });
  }
  async function speak(text, voice) {
    sayQ.push([text, voice, Date.now()]);
    if (speaking) return;
    speaking = true;
    while (sayQ.length) {
      while ((renderer.xr.isPresenting && !head.on) || talking()) await new Promise((res) => setTimeout(res, 300));
      const [t, v, at] = sayQ.shift();
      // a line that waited (the user was talking, the headset was off) and has newer ones behind it is old news: skip it
      if (sayQ.length && Date.now() - (at || 0) > 30000) { live.emit("voice_dropped", { text: t.slice(0, 80), waited_s: Math.round((Date.now() - at) / 1000) }); continue; }
      try {
        speechAbort = new AbortController();
        const giveUp = setTimeout(() => speechAbort.abort(), 40000);          // a stuck TTS: drop the line, not the page
        const r = await fetch(`voice/say?text=${encodeURIComponent(t)}${v ? '&voice=' + encodeURIComponent(v) : ''}`, { signal: speechAbort.signal })
          .finally(() => clearTimeout(giveUp));
        if (r.status === 502 || r.status === 503) {          // the server's voice is off: the headset says it itself
          while ((renderer.xr.isPresenting && !head.on) || talking()) await new Promise((res) => setTimeout(res, 300));
          EAR.incoming();
          await new Promise((res) => setTimeout(res, 160));
          const s = await browserSay(t);
          live.emit('voice_spoken', { text: t.slice(0, 80), waited_s: Math.round((Date.now() - (at || Date.now())) / 1000), s: +s.toFixed(1), via: 'headset speech (server voice off)' });
          continue;
        }
        if (!r.ok) throw new Error('tts ' + r.status);
        const a = audio(), buf = await a.decodeAudioData(await r.arrayBuffer());
        // the user may have started talking while this line was being rendered: wait for them, check again, then play
        while ((renderer.xr.isPresenting && !head.on) || talking()) await new Promise((res) => setTimeout(res, 300));
        EAR.incoming();
        await new Promise((res) => setTimeout(res, 160));
        const src = a.createBufferSource(), g = a.createGain();
        g.gain.value = 0.9;
        src.buffer = buf; src.connect(g).connect(a.destination);
        saying = { text: t, voice: v, src, t0: a.currentTime, dur: buf.duration, at };
        await new Promise((res) => { src.onended = res; src.start(); });
        saying = null;
        live.emit('voice_spoken', { text: t.slice(0, 80), waited_s: Math.round((Date.now() - (at || Date.now())) / 1000), s: +buf.duration.toFixed(1) });
        if (note.rec || note.starting) await new Promise((res) => setTimeout(res, 300));
      } catch (e) { EAR.error(); live.emit('voice_error', { where: 'speak', error: String(e.message || e) }); }
    }
    speaking = false;
  }

  // ---- the mic: asked once on a click (entering VR is one), kept open for the session
  const micState = { s: 'not asked' };
  // the Quest can stop the mic while the headset is off: a stream whose track has ended is reopened (no prompt once
  // the user has allowed it)
  const alive = () => !!stream && stream.getAudioTracks().some((t) => t.readyState === 'live');
  let micP = null;
  async function mic() {
    if (alive()) return stream;
    if (micP) return micP;                         // one request at a time, however often it is asked
    micP = openMic().finally(() => { micP = null; });
    return micP;
  }
  async function openMic() {
    if (stream && !alive()) { live.emit('voice_mic', { state: 'reopening (the track had ended)' }); stream = null; }
    micState.s = 'asking';
    live.emit('voice_mic', { state: 'asking' });
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      micErr = null;
      micState.s = 'on';
      live.emit('voice_mic', { ok: true });
    } catch (e) {
      micErr = String(e.message || e);
      micState.s = 'not allowed';
      live.emit('voice_mic', { ok: false, error: micErr });
      throw e;
    }
    return stream;
  }
  document.addEventListener('click', () => { audio(); loadAll(); }, true);    // any click unlocks sound and loads the earcons
  const micBtn = document.getElementById('micbtn');
  if (micBtn) {
    const label = () => { micBtn.textContent = micState.s === 'on' ? 'Microphone on' : micState.s === 'asking' ? 'Microphone: answer the prompt' : 'Allow microphone (voice notes)'; micBtn.classList.toggle('on', micState.s === 'on'); };
    micBtn.addEventListener('click', () => { audio(); EAR.sent(); mic().then(label, label); label(); });
    label();
  }
  const mimeOf = () => ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg', 'audio/mp4'].find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';

  function recorder() {
    const chunks = [], mime = mimeOf(), r = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    r.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    const done = new Promise((res) => { r.onstop = () => res(new Blob(chunks, { type: (mime || 'audio/webm').split(';')[0] })); });
    r.start(1000);
    return { r, done, t0: performance.now() };
  }

  // ---- voice notes
  const note = { rec: null, via: '', timer: null, starting: false, snaps: [], snapTimer: null };
  async function noteStart(via) {
    if (note.rec || note.starting) return { already: true };
    if (!stream) { EAR.error(); live.emit('voice_error', { where: 'note', error: 'mic ' + micState.s + ': tap Allow microphone on the page before entering VR' }); return { error: 'no mic' }; }
    note.starting = true;
    hush();
    try {
      if (!alive()) await mic();
      note.rec = recorder(); note.via = via;
      // what the user sees while they talk: at the start and every SNAP_EVERY_MS (one eye render each, quiet)
      const grab = () => { if (renderer.xr.isPresenting) note.snaps.push(eyeSnapshot('call', null, true).then((r) => r.path).catch(() => null)); };
      note.snaps = []; grab();
      note.snapTimer = setInterval(grab, SNAP_EVERY_MS);
    } catch (e) {
      EAR.error();
      live.emit('voice_error', { where: 'note', error: 'could not record: ' + String(e.message || e) });
      return { error: String(e.message || e) };
    } finally { note.starting = false; }
    EAR.recStart();
    live.emit('voice_note_start', { via });
    note.timer = setTimeout(() => noteStop('time limit'), MAX_NOTE_S * 1000);
    return { recording: true };
  }
  async function noteStop(why = '') {
    if (!note.rec) return { recording: false };
    const n = note.rec; note.rec = null; note.endedAt = performance.now(); clearTimeout(note.timer);
    // what the user was looking at when they hung up goes with the note ("take a screenshot of this" means this
    // moment, not whenever Claude gets to it)
    clearInterval(note.snapTimer);
    const snapP = renderer.xr.isPresenting ? eyeSnapshot('note', null, true).then((r) => r.path).catch(() => null) : Promise.resolve(null);
    const during = note.snaps; note.snaps = [];
    if (note.locked) why = why ? 'locked, ' + why : 'locked';
    note.locked = false; note.letGo = false;
    n.r.stop();
    EAR.recStop();
    const blob = await n.done, secs = ((performance.now() - n.t0) / 1000).toFixed(1), snap = await snapP;
    const snaps = (await Promise.all(during)).filter(Boolean);
    if (blob.size < 2000 || secs < 0.4) { live.emit('voice_note_dropped', { seconds: +secs, why: 'too short' }); return { dropped: true }; }
    try {
      const r = await fetch(`voice/in?${qs_()}&kind=message&seconds=${secs}&via=${encodeURIComponent(note.via + (why ? ' / ' + why : ''))}${snap ? '&snap=' + encodeURIComponent(snap) : ''}${snaps.length ? '&snaps=' + encodeURIComponent(snaps.join('|')) : ''}`,
        { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob });
      if (!r.ok) throw new Error('upload ' + r.status);
      EAR.sent();
      return { sent: true, seconds: +secs };
    } catch (e) { EAR.error(); live.emit('voice_error', { where: 'upload', error: String(e.message || e) }); return { error: String(e) }; }
  }

  // the phone gesture (thumb and pinky out) held near the head starts a note; letting it go sends it
  // A short phone gesture is push-to-talk. Held LOCK_MS longer (the label fills up), a chime locks the call: the
  // hands are free and the note keeps recording until the phone gesture is made again, which sends it.
  let phoneSeen = 0, phoneSince = 0, refused = 0, waitRelease = false;
  function update() {
    const now = performance.now();
    // performing (perform.js): no gesture acts, so the phone, the framing and the thumbs are not read; the mic is
    // already recording the performance
    if (hands.performing) {
      if (note.rec && note.via === 'phone gesture' && !note.locked) noteStop('performing');
      phoneSince = 0; note.lockProgress = 0; hands.framing = false;
      hud(now);
      return;
    }
    let phone = false;
    if (hands.gestures.on('voice_note', { cand: true })) phone = true;   // gestures.js: a locked row (either hand phone)
    note.lockProgress = 0;
    if (phone) {
      if (!phoneSince) phoneSince = now;
      phoneSeen = now;
      if (note.locked) {                                  // a locked call: the phone gesture again hangs up and sends
        if (note.letGo && now - phoneSince >= HOLD_PHONE_MS) { waitRelease = true; noteStop('hung up'); }
      } else if (!note.rec && !waitRelease && now - phoneSince >= HOLD_PHONE_MS && now - refused > 3000) {
        noteStart('phone gesture').then((r) => { if (r && r.error) refused = performance.now(); });
      } else if (note.rec && note.via === 'phone gesture') {
        note.lockProgress = Math.min(1, (now - phoneSince - HOLD_PHONE_MS) / LOCK_MS);
        if (note.lockProgress >= 1) {
          note.locked = true; note.letGo = false;
          clearTimeout(note.timer); note.timer = setTimeout(() => noteStop('time limit'), MAX_LOCKED_S * 1000);
          EAR.incoming();
          live.emit('voice_note_locked', {});
        }
      }
    } else if (now - phoneSeen > LET_GO_MS) {
      phoneSince = 0; waitRelease = false;
      if (note.locked) note.letGo = true;
      else if (note.rec && note.via === 'phone gesture') noteStop();
    }
    frameShot(now);
    askUpdate(now);
    hud(now);
  }

  // ---- the user's screenshot: both hands as L shapes framing a picture (thumb and index out, palms toward the face,
  // one hand higher than the other), held a moment. Measured on the user (take 20261002_092829_screenshot_gesture):
  // both 'gun' at once, palms toward_you/up, left hand ~22 cm above the right, both ~33 cm in front of the face.
  // The snapshot is the left eye's view plus `frame`, the rectangle between the hands in image coordinates (0..1).
  const FRAME_HOLD_MS = 300;
  const frame = { since: 0, done: false };
  const corner = (f) => f.p['thumb-phalanx-proximal'].clone().add(f.p['index-finger-phalanx-proximal']).multiplyScalar(0.5);
  function framing() {
    const L = hands.state.left, R = hands.state.right;
    if (!L || !R || !L.f || !R.f) return null;
    const lg = (h) => h.g === 'gun' || h.cand === 'gun';
    const facing = (h) => h.palm !== 'away' && h.palm !== 'down';
    if (!lg(L) || !lg(R) || !facing(L) || !facing(R)) return null;
    // a frame, not two hands pointing: in the user's frame (take 20261002_092829) the index fingers are ~90 deg apart
    // and run across the view (|dot with gaze| <= 0.5); pointing with both hands they are parallel and forward, and that
    // fired a burst of snapshots
    const dl = L.f.indexDir, dr = R.f.indexDir, gaze = camera.getWorldDirection(new THREE.Vector3());
    if (THREE.MathUtils.radToDeg(dl.angleTo(dr)) < 55 || Math.abs(dl.dot(gaze)) > 0.65 || Math.abs(dr.dot(gaze)) > 0.65) return null;
    const a = corner(L.f), b = corner(R.f);
    const head = camera.getWorldPosition(new THREE.Vector3());
    // thresholds from the user's take: the two corners sit ~14 cm apart and ~9 cm apart in height
    if (Math.abs(a.y - b.y) < 0.03 || a.distanceTo(b) < 0.08 || a.distanceTo(head) > 0.75 || b.distanceTo(head) > 0.75) return null;
    // the frame's edges run along the index fingers and thumbs, so the rectangle spans corners and tips
    const tips = (f) => [f.p['index-finger-tip'], f.p['thumb-tip']];
    return [a, b, ...tips(L.f), ...tips(R.f)];
  }
  function frameShot(now) {
    const pts = framing();
    hands.framing = !!pts;                       // hands.js: no travel arc while the user frames a shot
    if (!pts) { frame.since = 0; frame.done = false; return; }
    if (!frame.since) frame.since = now;
    if (frame.done || now - frame.since < FRAME_HOLD_MS) return;
    frame.done = true;                           // one shot per framing: drop the hands to take another
    const g = shotHooks.gallery;
    if (g) g.shutter(); else EAR.snap();         // the shutter sounds now; the flash comes once the pixels are read
    eyeSnapshot('user', pts, true, () => g && g.captured(pts))
      .then((r) => { live.emit('user_snapshot', { path: r.path, frame: r.frame, pose: r.pose }); if (g) g.saved(r); })
      .catch((e) => { EAR.error(); live.emit('voice_error', { where: 'snapshot', error: String(e.message || e) }); });
  }

  // ---- a yes/no question from Claude: spoken, then answered with the RIGHT thumb (up = yes, down = no) held a moment
  // (the left thumbs up stays the reload card's). Resolves with the answer, or 'no answer' after the timeout.
  const ASK_HOLD_MS = 400;
  const asking = { q: null, since: 0, g: null, resolve: null, timer: null };
  async function ask(text, seconds = 60, from = null) {
    if (asking.q) return { error: 'already asking: ' + asking.q };
    if (hands.performing) return { error: 'performing: thumbs are off while someone follows the user; ask after the Follow, or show a panel' };
    speak(text);
    while (speaking || sayQ.length) await new Promise((res) => setTimeout(res, 150));
    EAR.incoming();
    asking.q = text; asking.since = 0; asking.g = null;
    thumbPrompt({ by: 'ask', q: text, yes: 'Yes', no: 'No', from });
    const t0 = performance.now();
    const answer = await new Promise((resolve) => {
      asking.resolve = resolve;
      asking.timer = setTimeout(() => resolve('no answer'), seconds * 1000);
    });
    clearTimeout(asking.timer);
    asking.q = null; asking.resolve = null;
    if (prompt.by === 'ask') thumbPrompt(null);
    if (answer === 'yes') EAR.sent(); else if (answer === 'no') EAR.recStop();
    const r = { question: text, answer, seconds: +((performance.now() - t0) / 1000).toFixed(1) };
    live.emit('answer', r);
    return r;
  }
  function askUpdate(now) {
    if (!asking.q) return;
    const G = hands.gestures;                            // the answer rows of the gesture map (gestures.js)
    const v = G.on('answer_yes') ? 'yes' : G.on('answer_no') ? 'no' : null;
    const hold = G.hold(v === 'no' ? 'answer_no' : 'answer_yes', ASK_HOLD_MS);
    if (v !== asking.g) { asking.g = v; asking.since = now; }
    if (prompt.by === 'ask') { prompt.g = v; prompt.fill = v ? Math.min(1, (now - asking.since) / hold) : 0; }
    if (v && now - asking.since >= hold && asking.resolve) asking.resolve(v);
  }

  // ---- what the user sees: the gesture over each hand, and a REC / mic badge in front of them
  function sprite(w = 256, h = 64) {
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
    const m = new THREE.Sprite(new THREE.SpriteMaterial({ map: t, depthTest: false, transparent: true, toneMapped: false }));
    m.renderOrder = 999; m.userData = { cv, t, text: null }; m.visible = false;
    scene.add(m);
    return m;
  }
  function draw(sp, text, bg, fg = '#fff', fill = 0) {
    const key = text + bg + Math.round(fill * 20);
    if (sp.userData.text === key) return;
    sp.userData.text = key;
    const { cv, t } = sp.userData, g = cv.getContext('2d');
    g.clearRect(0, 0, cv.width, cv.height);
    g.fillStyle = bg; g.beginPath(); g.roundRect(2, 2, cv.width - 4, cv.height - 4, 18); g.fill();
    if (fill > 0) { g.fillStyle = 'rgba(255,255,255,0.35)'; g.beginPath(); g.roundRect(2, 2, (cv.width - 4) * fill, cv.height - 4, 18); g.fill(); }
    g.fillStyle = fg; g.font = 'bold 34px system-ui, sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(text, cv.width / 2, cv.height / 2 + 2);
    t.needsUpdate = true;
  }
  // ---- the thumbs prompt: over the hand that answers (the right, unless the gesture map moves answer_yes), what a
  // thumbs up and a thumbs down will say and who asks (the user, 2026-10-04: show it "above the hand that answers
  // ... so I see what I'm agreeing to where I'm looking when I give the gesture"). A spoken question (ask) shows it
  // the whole time; a panel answered by thumbs shows it while the hand forms the gesture (panels.js). It fills as the
  // gesture is held.
  const prompt = { by: null, q: '', yes: 'Yes', no: 'No', from: null, g: null, fill: 0 };
  const qcard = sprite(560, 210);
  qcard.scale.set(0.17, 0.064, 1);
  function thumbPrompt(p) {
    if (!p) { prompt.by = null; qcard.visible = false; return; }
    Object.assign(prompt, { g: null, fill: 0, from: null, ...p });
  }
  function wrapTo(g, text, maxW, maxLines) {
    const out = []; let line = '';
    for (const w of String(text).split(/\s+/)) {
      const t = line ? line + ' ' + w : w;
      if (g.measureText(t).width > maxW && line) { out.push(line); line = w; } else line = t;
    }
    if (line) out.push(line);
    if (out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = out[maxLines - 1].replace(/.{0,3}$/, '...'); }
    return out;
  }
  function drawPrompt() {
    const key = JSON.stringify([prompt.q, prompt.yes, prompt.no, prompt.from, prompt.g, Math.round(prompt.fill * 20)]);
    if (qcard.userData.text === key) return;
    qcard.userData.text = key;
    const { cv, t } = qcard.userData, g = cv.getContext('2d'), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    g.fillStyle = 'rgba(16,18,22,0.94)'; g.beginPath(); g.roundRect(2, 2, W - 4, H - 4, 20); g.fill();
    g.textBaseline = 'middle'; g.textAlign = 'left';
    let y = 26;
    if (prompt.from) { g.fillStyle = '#9ca3af'; g.font = 'bold 22px system-ui, sans-serif'; g.fillText(String(prompt.from).toUpperCase() + ' ASKS', 20, y); y += 30; }
    g.fillStyle = '#f5f5f4'; g.font = 'bold 28px system-ui, sans-serif';
    for (const ln of wrapTo(g, prompt.q, W - 40, prompt.from ? 2 : 3)) { g.fillText(ln, 20, y); y += 34; }
    // the two answers, the held one filling
    const bw = (W - 52) / 2, by = H - 62;
    [['up', '\u{1F44D} ' + prompt.yes, 'thumbs_up'], ['down', '\u{1F44E} ' + prompt.no, 'thumbs_down']].forEach(([k, label, gname], i) => {
      const x = 20 + i * (bw + 12), on = prompt.g === (k === 'up' ? 'yes' : 'no') || prompt.g === gname;
      g.fillStyle = 'rgba(255,255,255,0.12)'; g.beginPath(); g.roundRect(x, by, bw, 46, 12); g.fill();
      if (on && prompt.fill > 0) { g.fillStyle = k === 'up' ? 'rgba(34,197,94,0.85)' : 'rgba(234,88,12,0.85)'; g.beginPath(); g.roundRect(x, by, bw * prompt.fill, 46, 12); g.fill(); }
      g.fillStyle = '#fff'; g.font = 'bold 24px system-ui, sans-serif'; g.textAlign = 'center';
      g.fillText(label, x + bw / 2, by + 24, bw - 16); g.textAlign = 'left';
    });
    t.needsUpdate = true;
  }
  const tags = { left: sprite(), right: sprite() }, badge = sprite(640, 72);
  tags.left.scale.set(0.09, 0.0225, 1); tags.right.scale.set(0.09, 0.0225, 1); badge.scale.set(0.32, 0.036, 1);    // wide canvas: 'REC locked: phone to send' was clipped
  const tmp = new THREE.Vector3(), fwd = new THREE.Vector3();
  function hud() {
    if (ed.cleanView) {                    // inside a person (embody.js): nothing over the view but a stop being held
      tags.left.visible = tags.right.visible = qcard.visible = false;
      const pf = self.perfHud && self.perfHud();
      badge.visible = !!(pf && pf.startsWith('■'));
      if (!badge.visible) return;
      camera.getWorldPosition(tmp); camera.getWorldDirection(fwd);
      badge.position.copy(tmp).addScaledVector(fwd, 0.6).y += 0.3;
      draw(badge, pf, 'rgba(60,60,60,0.85)');
      return;
    }
    for (const side of ['left', 'right']) {
      const h = hands.state[side], sp = tags[side];
      sp.visible = !!(h && h.f);
      if (!sp.visible) continue;
      sp.position.copy(h.f.wrist).y += 0.12;
      if (h.g === 'phone' && note.locked) draw(sp, note.letGo ? 'hang up?' : 'call locked', 'rgba(185,28,28,0.85)');
      else if (h.g === 'phone' && note.rec) draw(sp, note.lockProgress > 0.05 ? 'hold to lock' : 'phone', 'rgba(185,28,28,0.85)', '#fff', note.lockProgress);
      else draw(sp, h.g === 'none' ? '·' : h.g.replace('_', ' '), h.g === 'phone' ? 'rgba(185,28,28,0.85)' : 'rgba(20,24,30,0.7)');
    }
    const rh = hands.state[hands.gestures.handOf('answer_yes')];   // over the hand that answers (gestures.js)
    qcard.visible = !!(prompt.by && rh && rh.f && !rh.resting);
    if (qcard.visible) { qcard.position.copy(rh.f.wrist).y += 0.22; drawPrompt(); }
    const rec = !!note.rec, takeOn = !!take.rec, warn = micState.s !== 'on', pf = self.perfHud && self.perfHud();
    badge.visible = rec || takeOn || !!asking.q || !!pf || (warn && hands.state.left.f !== null);
    if (!badge.visible) return;
    camera.getWorldPosition(tmp); camera.getWorldDirection(fwd);
    badge.position.copy(tmp).addScaledVector(fwd, 0.6).y += pf ? 0.3 : 0.17;     // performing: above the view, not in it
    if (pf) draw(badge, pf, pf.startsWith('●') ? 'rgba(185,28,28,0.9)' : 'rgba(60,60,60,0.85)');
    else if (asking.q && !rec) draw(badge, 'right thumb:  up = yes   down = no', 'rgba(30,64,175,0.9)');
    else if (rec && note.locked) draw(badge, '● REC locked: phone to send', 'rgba(185,28,28,0.9)');
    else if (rec) draw(badge, '● REC  voice note', 'rgba(185,28,28,0.9)');
    else if (takeOn) draw(badge, '● TAKE recording', 'rgba(185,28,28,0.9)');
    else draw(badge, 'mic: ' + micState.s, 'rgba(120,80,10,0.85)');
  }

  // ---- take audio: the mic runs for the whole take
  const take = { rec: null, id: null };
  async function takeAudioStart(id) {
    EAR.takeStart();
    if (!stream) { live.emit('voice_error', { where: 'take', error: 'mic ' + micState.s + ': the take has no audio' }); return; }
    try {
      if (!alive()) await mic();
      take.rec = recorder(); take.id = id;
    } catch (e) { EAR.error(); live.emit('voice_error', { where: 'take', error: 'could not record: ' + String(e.message || e) }); }
  }
  async function takeAudioStop() {
    EAR.takeStop();
    if (!take.rec) return;
    const t = take.rec, id = take.id; take.rec = null;
    t.r.stop();
    const blob = await t.done;
    fetch(`voice/in?${qs_()}&kind=take&take=${encodeURIComponent(id)}&seconds=${((performance.now() - t.t0) / 1000).toFixed(2)}`,
      { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob }).catch(() => EAR.error());
  }

  // ---- what one eye sees: the left eye's camera rendered into a target and read back
  const eyeRT = new THREE.WebGLRenderTarget(1024, 1024);
  eyeRT.texture.colorSpace = THREE.SRGBColorSpace;
  // the render happens inside the next XR frame (preRender), where the eye cameras are current
  const eyeQ = [];
  ed.preRender.push(() => {
    if (!eyeQ.length || !renderer.xr.isPresenting) return;
    const job = eyeQ.shift(), xr = renderer.xr;
    if (job.clean && job.pts && job.pts.length >= 2) {
      try { job.resolve(cleanShot(xr, job.pts)); } catch (e) { job.reject(e); }
      return;
    }
    try {
      const eye = xr.getCamera().cameras[0];
      const cam = new THREE.PerspectiveCamera();
      cam.projectionMatrix.copy(eye.projectionMatrix); cam.projectionMatrixInverse.copy(eye.projectionMatrixInverse);
      eye.matrixWorld.decompose(cam.position, cam.quaternion, cam.scale);
      cam.updateMatrixWorld(true);
      cam.layers.mask = eye.layers.mask;
      const rt = renderer.getRenderTarget(), on = xr.enabled;
      xr.enabled = false;
      renderer.setRenderTarget(eyeRT);
      renderer.render(scene, cam);
      const px = new Uint8Array(1024 * 1024 * 4);
      renderer.readRenderTargetPixels(eyeRT, 0, 0, 1024, 1024, px);
      renderer.setRenderTarget(rt);
      xr.enabled = on;
      let rect = null;
      if (job.pts && job.pts.length) {
        const ndc = job.pts.map((p) => p.clone().project(cam));
        const xs = ndc.map((v) => (v.x + 1) / 2), ys = ndc.map((v) => (1 - v.y) / 2), cl = (v) => +Math.min(1, Math.max(0, v)).toFixed(3);
        rect = [cl(Math.min(...xs)), cl(Math.min(...ys)), cl(Math.max(...xs)), cl(Math.max(...ys))];
      }
      job.resolve({ px, rect });
    } catch (e) { job.reject(e); }
  });
  // the user's framed shot (the user, 2026-10-02: one eye only, hands and rays in it, and tiny): a camera between the
  // eyes looking through the middle of the hands' frame, its field of view the frame's, the hands hidden and only
  // layer 0 (no rays, panels, gizmos), rendered at 1280 px across
  const SHOT_W = 1280;
  let shotRT = null;
  function cleanShot(xr, pts) {
    const cams = xr.getCamera().cameras, eye = cams[0];
    const pos = new THREE.Vector3().setFromMatrixPosition(cams[0].matrixWorld);
    if (cams[1]) pos.lerp(new THREE.Vector3().setFromMatrixPosition(cams[1].matrixWorld), 0.5);
    const headQ = new THREE.Quaternion();
    eye.matrixWorld.decompose(new THREE.Vector3(), headQ, new THREE.Vector3());
    const c = pts.reduce((a, p) => a.add(p), new THREE.Vector3()).multiplyScalar(1 / pts.length);
    const cam = new THREE.PerspectiveCamera(50, 1, 0.03, 500);
    cam.position.copy(pos);
    cam.up.set(0, 1, 0).applyQuaternion(headQ);                  // the head's roll stays in the picture
    cam.lookAt(c);
    cam.updateMatrixWorld(true);
    const inv = cam.matrixWorld.clone().invert();
    let ax = 0, ay = 0;                                          // the frame's half angles, seen from the camera
    for (const p of pts) {
      const l = p.clone().applyMatrix4(inv);
      if (l.z < -1e-3) { ax = Math.max(ax, Math.abs(l.x / -l.z)); ay = Math.max(ay, Math.abs(l.y / -l.z)); }
    }
    ax = Math.max(ax, 0.05); ay = Math.max(ay, 0.04);
    cam.fov = THREE.MathUtils.radToDeg(2 * Math.atan(ay));
    cam.aspect = ax / ay;
    cam.updateProjectionMatrix();
    cam.layers.set(0);
    const w = SHOT_W, h = Math.max(64, Math.min(2048, Math.round(SHOT_W / cam.aspect)));
    if (!shotRT || shotRT.width !== w || shotRT.height !== h) {
      if (shotRT) shotRT.dispose();
      shotRT = new THREE.WebGLRenderTarget(w, h, { samples: 4 });
      shotRT.texture.colorSpace = THREE.SRGBColorSpace;
    }
    const hidden = [];
    for (let i = 0; i < 2; i++) for (const o of [xr.getHand(i), xr.getControllerGrip(i), xr.getController(i)]) if (o && o.visible) { o.visible = false; hidden.push(o); }
    // and every overlay drawn over the world (captions, labels, cards, the selection outline: all depthTest off)
    scene.traverseVisible((o) => { if (o.material && o.material.depthTest === false) hidden.push(o); });
    for (const o of hidden) o.visible = false;
    const rt = renderer.getRenderTarget(), on = xr.enabled;
    try {
      xr.enabled = false;
      renderer.setRenderTarget(shotRT);
      renderer.render(scene, cam);
      const px = new Uint8Array(w * h * 4);
      renderer.readRenderTargetPixels(shotRT, 0, 0, w, h, px);
      const p = cam.position, d = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion), u = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.quaternion);
      const pose = { pos: [p.x, -p.z, p.y], fwd: [d.x, -d.z, d.y], up: [u.x, -u.z, u.y], vfov: cam.fov, aspect: cam.aspect };   // Blender axes
      return { px, w, h, rect: [0, 0, 1, 1], clean: true, pose };
    } finally {
      renderer.setRenderTarget(rt);
      xr.enabled = on;
      for (const o of hidden) o.visible = true;
    }
  }
  const shotHooks = { gallery: null };              // gallery.js: shutter, flash, the picture to the wrist
  async function eyeSnapshot(tag = 'eye', pts = null, quiet = false, onRead = null) {
    if (!renderer.xr.isPresenting) throw new Error('not in VR');
    const { px, rect, w = 1024, h = 1024, clean, pose } = await new Promise((resolve, reject) => eyeQ.push({ resolve, reject, pts, clean: tag === 'user' }));
    if (onRead) onRead();
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const g = cv.getContext('2d'), img = g.createImageData(w, h), row = w * 4;
    for (let y = 0; y < h; y++) img.data.set(px.subarray((h - 1 - y) * row, (h - y) * row), y * row);
    g.putImageData(img, 0, 0);
    const blob = await new Promise((res) => cv.toBlob(res, 'image/png'));
    const r = await fetch(`snapshot?${qs_()}&tag=${encodeURIComponent(tag)}`, { method: 'POST', body: blob }).then((x) => x.json());
    if (!quiet) EAR.snap();
    return { path: r.path, eye: clean ? 'between' : 'left', frame: rect, canvas: cv, pose };
  }
  async function eyecam(fps = 1, seconds = 10) {
    const n = Math.min(600, Math.round(fps * seconds)), paths = [];
    for (let i = 0; i < n; i++) {
      if (!renderer.xr.isPresenting) break;
      paths.push((await eyeSnapshot('eyecam')).path);
      await new Promise((res) => setTimeout(res, 1000 / fps));
    }
    return { frames: paths.length, first: paths[0], last: paths[paths.length - 1] };
  }

  const abortSpeech = () => { sayQ.length = 0; if (speechAbort) speechAbort.abort(); };
  const self = { update, speak, talking, thumbPrompt, prompt, qcard, noteStart, noteStop, takeAudioStart, takeAudioStop, eyeSnapshot, eyecam, EAR, mic, note, micState, head, ask,
    shotHooks, abortSpeech, ctx: audio, perfHud: null };
  return self;
}
