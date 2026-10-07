// ismail phone page: listen with the screen off, talk back, tap feedback, answer what the agents put here.
'use strict';
const $ = (id) => document.getElementById(id);
const audio = $('audio');
const store = {
  get(k, d) { try { const v = localStorage.getItem('ismail.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('ismail.' + k, JSON.stringify(v)); } catch (e) {} },
};
let kbps = store.get('kbps', 64), buzzOn = store.get('buzz', true);
let sid = null, want = false, lastT = 0, lastAdvance = Date.now(), retry = 0, state = {}, since = 0, first = true;
let outbox = store.get('outbox', []);

function toast(text) {
  const t = $('toast'); t.textContent = text; t.classList.add('show');
  clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('show'), 2600);
}
function buzz(p) { if (buzzOn && navigator.vibrate) navigator.vibrate(p || [120]); }
function heardNow() { return audio.src && !audio.paused ? audio.currentTime : null; }
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// ---- the stream
function connect(back) {
  sid = Math.random().toString(36).slice(2, 10);
  audio.src = `stream.mp3?sid=${sid}&kbps=${kbps}&back=${back || 0}`;
  lastT = 0; lastAdvance = Date.now();
  audio.play().then(() => { retry = 0; }).catch((e) => {
    if (e && e.name === 'NotAllowedError') return asleep();        // the phone wants a tap first: say so, no retry loop
    if (want) setTimeout(() => want && connect(), backoff());
  });
}
// The two big keys show their state with an icon as well as a word (Nate 10-06 09:59)
const KEYSVG = {
  play: 'M7 5 19 12 7 19Z',                                         // Listen
  stop: 'M7 7h10v10H7Z',                                            // playing: tap to stop
  wait: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4',                       // reconnecting or buffering
  resume: 'M7 5 19 12 7 19ZM3 5v14',                                // the phone wants a tap first
  mic: 'M9 3h6v10H9ZM5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8',      // hold to talk
  talking: 'M9 3h6v10H9ZM5 11v1a7 7 0 0 0 14 0v-1M12 19v3M8 22h8M1 8v6M23 8v6',
  sending: 'M12 21V6M6 12l6-6 6 6M4 3h16',                          // the note is on its way
  sent: 'M4 12l5 5L20 6',                                           // it arrived
  blocked: 'M9 3h6v10H9ZM5 11v1a7 7 0 0 0 14 0v-1M12 19v3M3 3l18 18',  // the microphone is blocked or offline
};
function keyState(id, icon, label, cls) {
  const b = $(id); if (!b) return;
  b.querySelector('svg').innerHTML = `<path d="${KEYSVG[icon]}"/>`;
  b.querySelector('.kl').textContent = label;
  b.classList.remove('wait', 'sending', 'sent');
  if (cls) b.classList.add(cls);
}
// A page that reloads (the phone dropped it while locked, or it was reopened) forgets it was listening and the set
// goes quiet with nobody told (2026-10-06, a walk). It remembers, picks the stream back up when the phone allows, and
// otherwise asks for one tap.
function remember() { store.set('listening', want ? Date.now() : 0); }
function asleep() {
  ev('resume_asked');
  want = false; sid = null;
  keyState('play', 'resume', 'Resume'); $('play').classList.remove('on');
  toast('the set is still playing: tap Resume to hear it'); buzz([80, 60, 80]);
}
function backoff() { retry = Math.min(retry + 1, 6); return 1000 * 2 ** (retry - 1); }
function setPlaying(on) {
  if (on !== want) ev(on ? 'listen' : 'stop');
  want = on;
  if (on) connect(); else { holdOff(); audio.pause(); audio.removeAttribute('src'); audio.load(); sid = null; }
  keyState('play', on ? 'wait' : 'play', on ? 'Stop' : 'Listen', on ? 'wait' : null); $('play').classList.toggle('on', on);
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = on ? 'playing' : 'paused';
  remember();
}
$('play').onclick = () => { setPlaying(!want); if (want && keysOn) armMic(); };
$('golive').onclick = () => { if (!want) return setPlaying(true); connect(0); toast('back to live'); };
$('back').onclick = () => { want = true; connect(30); send('/api/tap', { what: 'rewind' }, true); toast('30 s back'); };
// ledger:M142: after a server restart a page in a pocket never came back. A hidden page whose audio stops loses the
// media exemption and Android freezes its timers, so the retries never run. While the stream is down, a loop far
// under hearing (40 Hz at -80 dBFS) keeps the page playing, and so awake, until the stream plays again.
let keep = null;
function holdOn() {
  if (!want) return;
  try { if (!keep) { keep = new Audio(wav([[40, 1000]], 3)); keep.loop = true; } if (keep.paused) keep.play().catch(() => {}); } catch (e) {}
}
function holdOff() { if (keep && !keep.paused) keep.pause(); }
['error', 'ended'].forEach((ev) => audio.addEventListener(ev, () => { if (want) { holdOn(); setTimeout(() => want && connect(), backoff()); } }));
audio.addEventListener('playing', () => { holdOff(); if (want) keyState('play', 'stop', 'Stop'); });
['waiting', 'stalled'].forEach((x) => audio.addEventListener(x, () => { if (want) keyState('play', 'wait', 'Stop', 'wait'); }));
audio.addEventListener('timeupdate', () => { if (audio.currentTime > lastT + 0.2) { lastT = audio.currentTime; lastAdvance = Date.now(); } });
// the stream's gaps, measured (Nate 10-06 14:56: "dropouts ... is that the stream due to buffering ... or CPU ... a
// profiler"): every stall the browser reports and every freeze (sound not advancing for over 1.5 s) is logged with
// its length, whether a voice note was recording, the playback rate and the network, so phone_timeline shows each gap
const gap = { at: 0, kind: '', note: false };
function gapStart(kind) {
  if (!want || gap.at) return;
  gap.at = Date.now(); gap.kind = kind; gap.note = !!talk.rec; gap.rate = audio.playbackRate; gap.buf = +bufAhead().toFixed(1);
  if (audio.playbackRate !== 1) { audio.playbackRate = 1; catchHold = Date.now() + HOLD_MS; }   // the network can't keep up
}
function gapEnd() {
  if (!gap.at) return;
  const ms = Date.now() - gap.at, c = navigator.connection || {};
  if (ms >= 300) ev('stall', { cause: gap.kind, ms, during_note: gap.note || !!talk.rec, rate: gap.rate, buf: gap.buf,
    net: c.effectiveType || '', downlink: c.downlink, mic: talk.stream ? (micSrc === 'phone' ? 'phone' : 'earbuds') : 'closed' });
  gap.at = 0;
}
['waiting', 'stalled'].forEach((x) => audio.addEventListener(x, () => gapStart(x)));
audio.addEventListener('playing', gapEnd);
audio.addEventListener('timeupdate', () => { if (gap.at && gap.kind === 'freeze' && Date.now() - lastAdvance < 300) gapEnd(); });
setInterval(() => { if (want && !audio.paused && !clip.el && Date.now() - lastAdvance > 1500) gapStart('freeze'); }, 500);
try { navigator.mediaDevices.addEventListener('devicechange', () => ev('route', { during_note: !!talk.rec })); } catch (e) {}
setInterval(() => {                     // a stream that stops moving reconnects (wifi dropped, server restarted)
  if (want && !clip.el && Date.now() - lastAdvance > 12000) { lastAdvance = Date.now(); $('livetext').textContent = 'reconnecting'; holdOn(); connect(); }
}, 3000);
setInterval(() => { if (want) remember(); }, 30000);
window.addEventListener('online', () => { if (want) connect(); flush(); });

// ---- lock screen and earbuds: next = change it up, previous = love this
function mediaSession() {
  if (!('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession, e = state.engine || {};
  try {
    ms.metadata = new MediaMetadata({ title: e.now || 'ismail live', artist: (state.heard && state.heard.of) ? 'ismail live, ' + state.heard.of : 'ismail live',
      album: e.next ? 'next: ' + e.next : '', artwork: [{ src: 'icon.svg', sizes: '512x512', type: 'image/svg+xml' }] });
  } catch (err) {}
  if (mediaSession.done) return;
  mediaSession.done = true;
  const h = (a, f) => { try { ms.setActionHandler(a, f); } catch (err) {} };
  // earbuds: the Dime 3 sends only play/pause (double and triple presses change the volume in the bud), so while
  // the set plays a press is a voice note, and "stop listening" said in a note stops the stream
  h('play', () => { ev('earbud', { key: 'play' }); if (want && keysOn) return keyNote(); setPlaying(true); cue('start'); });
  h('pause', () => { ev('earbud', { key: 'pause' }); if (want && keysOn) return keyNote(); setPlaying(false); });
  h('nexttrack', () => { ev('earbud', { key: 'next' }); tap('change'); });
  h('previoustrack', () => tap('love'));
  h('seekbackward', () => $('back').onclick());
}

// ---- the phone's take: what happens on the page, timed, so an agent can lay it over the voice notes
// (Nate 10-06: "kind of like the same thing [as a VR take], but for the mobile interface")
const evq = [];
function ev(what, f) { evq.push(Object.assign({ what, at: Date.now(), t: heardNow() }, f || {})); if (evq.length > 40) flushEv(); }
function flushEv(beacon) {
  if (!evq.length) return;
  const events = evq.splice(0).map((e) => { const { at, ...rest } = e; return Object.assign(rest, { age_ms: Date.now() - at }); });
  const body = JSON.stringify({ sid, events });
  if (beacon && navigator.sendBeacon) { navigator.sendBeacon('api/events', new Blob([body], { type: 'application/json' })); return; }
  fetch('api/events', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body, keepalive: true })
    .catch(() => { evq.unshift(...events.map((e) => Object.assign(e, { at: Date.now() - e.age_ms }))); });
}
setInterval(() => flushEv(), 3000);
window.addEventListener('pagehide', () => { ev('close'); flushEv(true); });
document.addEventListener('visibilitychange', () => { ev(document.visibilityState === 'visible' ? 'visible' : 'hidden'); if (document.visibilityState !== 'visible') flushEv(true); });
window.addEventListener('offline', () => ev('offline'));
window.addEventListener('online', () => ev('online'));
(function opened() {
  const c = navigator.connection || {};
  const standaloneNow = matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  ev('open', { mobile: /Mobi|Android/i.test(navigator.userAgent), app: !!standaloneNow, w: screen.width, h: screen.height,
    lang: navigator.language, net: c.effectiveType || '', platform: (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '' });
})();
// where they are on the page: the section in view once scrolling settles (a timer, not scroll events: those come
// with drawn frames, and a backgrounded page draws none)
let secShown = null, lastY = -1, stillY = -1;
setInterval(() => {
  const y = Math.round(window.scrollY);
  if (y !== lastY) { lastY = y; return; }                 // still moving
  if (y === stillY) return;                               // settled where it was
  stillY = y;
  const mid = window.innerHeight * 0.4;
  const s = [...document.querySelectorAll('[data-sec]')].find((el) => { const r = el.getBoundingClientRect(); return r.top <= mid && r.bottom >= mid; });
  const name = s ? s.dataset.sec : null;
  if (name && name !== secShown) { if (secShown !== null) ev('scroll', { to: name, y }); secShown = name; }
}, 1500);

// ---- sending (queued while offline)
async function send(path, body, quiet) {
  body = Object.assign({ sid, t: heardNow() }, body);
  let r;
  try {
    r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  } catch (e) {
    r = null;
  }
  if (r) {                                   // the server answered: a refusal is final, never queued as offline
    const j = await r.json().catch(() => ({}));
    if (r.ok) return j;
    if (!quiet) toast(j.error || `refused (${r.status})`);
    return r.status === 404 ? { gone: true } : null;
  }
  {
    if (!quiet) toast('offline: kept, sends when you are back');
    outbox.push([path, body]); store.set('outbox', outbox);
    return null;
  }
}
async function flush() {
  const todo = outbox; outbox = []; store.set('outbox', outbox);
  for (const [p, b] of todo) {
    try { const r = await fetch(p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) }); if (!r.ok && r.status >= 500) throw 0; }
    catch (e) { outbox.push([p, b]); }
  }
  store.set('outbox', outbox);
}
const SAID = { love: 'love this', change: 'change it up', energy_up: 'more energy', energy_down: 'calmer', louder: 'louder',
  quieter: 'quieter', pause: 'pause the set', resume: 'resume the set', start_set: 'start a set' };
async function tap(what, extra) {
  buzz([40]);
  sound(what) || sound(String(what).split(':')[0] === 'button' ? 'tap' : (extra && extra.mood ? 'mood' : 'tap')) || (what !== 'tap' && sound('tap'));
  const j = await send('/api/tap', Object.assign({ what }, extra || {}));
  if (j) toast((SAID[what] || extra && extra.mood || what) + (j.heard && j.heard.of ? ', at ' + j.heard.of : '') + ': sent');
}
function flash(el) {                        // momentary: the key lights, says SENT, and is a plain key again
  el.blur();                                // (nothing stays pressed: a second press sends again, never "un-presses")
  const l = el.querySelector('.lbl') || el;
  if (!el.dataset.lbl) el.dataset.lbl = l.textContent;
  el.classList.add('sent'); l.textContent = 'Sent';
  clearTimeout(el.flashT); el.flashT = setTimeout(() => { el.classList.remove('sent'); l.textContent = el.dataset.lbl; }, 800);
}
$('love').onclick = () => { flash($('love')); tap('love'); };
$('startset').onclick = () => tap('start_set');
$('change').onclick = () => { flash($('change')); tap('change'); };
document.querySelectorAll('[data-tap]').forEach((b) => { b.onclick = () => { flash(b); tap(b.dataset.tap); }; });
document.querySelectorAll('[data-mood]').forEach((b) => { b.onclick = () => { flash(b); tap('mood', { mood: b.dataset.mood }); }; });
$('quality').onclick = () => { kbps = kbps === 64 ? 128 : 64; store.set('kbps', kbps); $('quality').textContent = kbps + ' kbps'; if (want) connect(); };
$('buzzset').onclick = () => { buzzOn = !buzzOn; store.set('buzz', buzzOn); $('buzzset').textContent = buzzOn ? 'Buzz on' : 'Buzz off'; };
$('quality').textContent = kbps + ' kbps'; $('buzzset').textContent = buzzOn ? 'Buzz on' : 'Buzz off';

// ---- talk: hold to talk, or tap once to talk hands-free and tap again to send
const talk = { rec: null, stream: null, chunks: [], down: 0, toggle: false, t: null, sid: null };
// Nate 10-06 14:39: with the mic open, Bluetooth earbuds (his Dime 3) switch to call mode (HFP: mono, narrowband) and
// the music sounds bad. So by default the mic opens for a note and closes after it, and the earbuds go back to music
// quality. 'Mic: kept open' is the old way (an earbud press starts a note even with the screen off, in call quality).
// 'Record: phone mic' records with the phone's own microphone, so the earbuds may never enter call mode.
let micKeep = store.get('mic_keep', false), micSrc = store.get('mic_src', 'earbuds'), micRaw = store.get('mic_raw', true);
async function openMic() {
  // raw by default (ledger:M163): the phone's echo cancelling, noise suppression and gain control strip the music that
  // bleeds into the mic, and that bleed is what lines a hummed part up with the beat heard; speech reads fine without
  const on = !micRaw, base = { echoCancellation: on, noiseSuppression: on, autoGainControl: on };
  if (micSrc === 'phone') {
    try {
      const ds = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput' && d.label);
      const own = ds.find((d) => !/bluetooth|headset|hands.?free|buds|dime|sco|wireless/i.test(d.label) &&
        !['default', 'communications'].includes(d.deviceId));
      if (own) return navigator.mediaDevices.getUserMedia({ audio: { ...base, deviceId: { exact: own.deviceId } } });
      toast('no phone microphone listed: recording with the default one');
    } catch (e) {}
  }
  return navigator.mediaDevices.getUserMedia({ audio: base });
}
function closeMic() {                     // every track stopped: the earbuds can go back to music quality
  if (talk.rec || !talk.stream) return;
  talk.stream.getTracks().forEach((t) => t.stop());
  talk.stream = null;
}
// ---- earbud button and the tones you hear in your pocket
let keysOn = store.get('keys', true), noteTimer = 0;
function wav(parts, amp = 9000) {           // [[freq, ms], ...] -> a data: URI of a short 16-bit tone sequence
  const sr = 22050, n = parts.reduce((a, [, ms]) => a + Math.round(sr * ms / 1000), 0);
  const b = new DataView(new ArrayBuffer(44 + 2 * n)); let o = 44;
  const str = (i, t) => [...t].forEach((c, k) => b.setUint8(i + k, c.charCodeAt(0)));
  str(0, 'RIFF'); b.setUint32(4, 36 + 2 * n, true); str(8, 'WAVEfmt '); b.setUint32(16, 16, true); b.setUint16(20, 1, true);
  b.setUint16(22, 1, true); b.setUint32(24, sr, true); b.setUint32(28, sr * 2, true); b.setUint16(32, 2, true); b.setUint16(34, 16, true);
  str(36, 'data'); b.setUint32(40, 2 * n, true);
  for (const [f, ms] of parts) {
    const m = Math.round(sr * ms / 1000);
    for (let i = 0; i < m; i++) { const env = Math.min(1, i / 200, (m - i) / 400); b.setInt16(o, f ? Math.sin(2 * Math.PI * f * i / sr) * amp * env : 0, true); o += 2; }
  }
  let bin = ''; new Uint8Array(b.buffer).forEach((x) => { bin += String.fromCharCode(x); });
  return 'data:audio/wav;base64,' + btoa(bin);
}
const CUES = { start: wav([[660, 90], [0, 30], [990, 120]]), end: wav([[990, 90], [0, 30], [660, 120]]),
  sent: wav([[1320, 60], [0, 50], [1320, 60]]), error: wav([[220, 260]]) };
// the tones are made with ismail (its measured grand piano: songs/_phone_cues/make_cues.py); the synthesized ones
// above stand in until the files load, or if they cannot
const MADE = {};
['start', 'end', 'sent', 'error'].forEach((n) => { const a = new Audio('cues/' + n + '.mp3'); a.preload = 'auto';
  a.addEventListener('canplaythrough', () => { MADE[n] = a.src; }, { once: true }); });
// the sounds an agent attached (phone_sounds): one per event, played on that event only; none means silent
let SOUNDS = {};
function sound(evn) {
  const s = SOUNDS[evn]; if (!s) return false;
  try { const a = new Audio(s.url); a.volume = Math.min(1, 0.8 * Math.pow(10, (s.gain_db || 0) / 20)); a.play().catch(() => {}); } catch (e) {}
  return true;
}
const CUE_EVENT = { start: 'note_start', end: 'note_end', sent: 'note_sent', error: 'error' };
function cue(name) { if (sound(CUE_EVENT[name])) return; try { const a = new Audio(MADE[name] || CUES[name]); a.volume = 0.8; a.play().catch(() => {}); } catch (e) {} }
async function armMic() {
  if (!micKeep) return false;               // the mic opens when a note starts
  if (talk.stream && talk.stream.active) return true;
  try {
    talk.stream = await openMic();
    $('talkhint').textContent = 'earbud ready: press to talk, press again to send (the mic stays open: call quality)';
    return true;
  } catch (e) { $('talkhint').textContent = 'the microphone is blocked: the earbud cannot take notes'; return false; }
}
async function keyNote() {
  if (audio.paused && want) audio.play().catch(() => {});
  if ('mediaSession' in navigator) navigator.mediaSession.playbackState = 'playing';
  if (talk.rec) { micStop(true); return; }
  if (micKeep && (!talk.stream || !talk.stream.active)) { cue('error'); buzz([300]); toast('open the page once to let the earbud take notes'); return; }
  await micStart();                         // otherwise it opens the mic now (a second or two for the earbuds to switch)
}
$('keysset').onclick = () => { keysOn = !keysOn; store.set('keys', keysOn); $('keysset').textContent = keysOn ? 'Earbud: talk' : 'Earbud: play'; if (keysOn && want) armMic(); else closeMic(); };
$('keysset').textContent = keysOn ? 'Earbud: talk' : 'Earbud: play';
$('mickeep').onclick = () => {
  micKeep = !micKeep; store.set('mic_keep', micKeep); $('mickeep').textContent = micKeep ? 'Mic: kept open' : 'Mic: per note';
  if (micKeep) { if (keysOn && want) armMic(); } else { closeMic(); $('talkhint').textContent = 'tap once for hands-free, tap again to send'; }
};
$('micsrc').onclick = () => {
  micSrc = micSrc === 'phone' ? 'earbuds' : 'phone'; store.set('mic_src', micSrc);
  $('micsrc').textContent = micSrc === 'phone' ? 'Record: phone mic' : 'Record: earbuds';
  closeMic(); if (micKeep && keysOn && want) armMic();
};
$('micraw').onclick = () => {
  micRaw = !micRaw; store.set('mic_raw', micRaw); $('micraw').textContent = micRaw ? 'Mic: raw' : 'Mic: cleaned';
  closeMic(); if (micKeep && keysOn && want) armMic();
};
$('micraw').textContent = micRaw ? 'Mic: raw' : 'Mic: cleaned';
$('mickeep').textContent = micKeep ? 'Mic: kept open' : 'Mic: per note';
$('micsrc').textContent = micSrc === 'phone' ? 'Record: phone mic' : 'Record: earbuds';
async function micStart() {
  if (talk.rec) return;
  try {
    if (!talk.stream || !talk.stream.active) { const t0 = Date.now(); talk.stream = await openMic(); talk.openMs = Date.now() - t0; }
  } catch (e) {
    cue('error'); buzz([300]);
    toast(document.hidden ? 'the phone would not open the mic with the screen off: turn it on, or set Mic: kept open' : 'the microphone is blocked: allow it for this page');
    keyState('talk', 'blocked', 'Mic blocked'); return;
  }
  const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((m) => window.MediaRecorder && MediaRecorder.isTypeSupported(m)) || '';
  talk.chunks = []; talk.t = heardNow(); talk.sid = sid; talk.started = Date.now(); talk.end = 'press';
  try {                                     // what the browser actually applied, so each route's numbers stay apart
    const g = talk.stream.getAudioTracks()[0].getSettings(), f = (k) => (g[k] === undefined ? '?' : g[k] ? 1 : 0);
    talk.mic = `${micSrc} ${micRaw ? 'raw' : 'cleaned'} ec${f('echoCancellation')} ns${f('noiseSuppression')} agc${f('autoGainControl')}`;
  } catch (e) { talk.mic = micSrc; }
  ev('note_start', { mic: micSrc, open_ms: talk.openMs || 0, kept: micKeep });
  talk.rec = new MediaRecorder(talk.stream, mime ? { mimeType: mime } : undefined);
  talk.rec.ondataavailable = (e) => { if (e.data.size) talk.chunks.push(e.data); };
  talk.rec.start(250);
  audio.volume = 0.25;
  $('talk').classList.add('on'); keyState('talk', 'talking', 'Talking'); buzz([30]);
  meterStart(talk.stream);
  cue('start');
  // Nate 10-06: "the voice recording should not cut me off" (it stopped at 60 s mid-sentence). A note now runs as long
  // as they talk: it ends on their press, after 30 s of quiet (a note left running in a pocket), or at 10 minutes
  // (a warning buzz 20 s before). The quiet check runs on a timer: animation frames stop with the screen off.
  clearTimeout(noteTimer); clearInterval(talk.quietT);
  talk.loudAt = Date.now(); const startedAt = Date.now(); let warned = false;
  talk.quietT = setInterval(() => {
    if (!talk.rec) return clearInterval(talk.quietT);
    if (meter.an) {
      const b = new Float32Array(meter.an.fftSize); meter.an.getFloatTimeDomainData(b);
      let p = 0; for (const v of b) p = Math.max(p, Math.abs(v));
      if (p > 0.02) talk.loudAt = Date.now();
    }
    const left = NOTE_MAX_MS - (Date.now() - startedAt);
    if (!warned && left < 20000) { warned = true; buzz([200, 100, 200]); cue('error'); toast('20 s left on this note'); }
    if (Date.now() - talk.loudAt > NOTE_QUIET_MS || left <= 0) { talk.end = left <= 0 ? 'max' : 'quiet'; micStop(true); }
  }, 500);
}
const NOTE_QUIET_MS = 30000, NOTE_MAX_MS = 10 * 60000;
function micStop(sendIt) {
  const r = talk.rec; if (!r) return;
  talk.rec = null; talk.toggle = false;
  const onPanel = talk.panel; talk.panel = null;
  const pb = document.getElementById('panelrec');
  if (pb) { pb.classList.remove('on'); pb.textContent = sendIt ? 'Sent. Say more' : 'Say more'; }
  $('talk').classList.remove('on'); keyState('talk', sendIt ? 'sending' : 'mic', sendIt ? 'Sending' : 'Hold to talk', sendIt ? 'sending' : null); audio.volume = 1;
  $('talkhint').textContent = 'tap once for hands-free, tap again to send';
  meterStop();
  clearTimeout(noteTimer); clearInterval(talk.quietT); cue('end');
  const dur = (Date.now() - (talk.started || Date.now())) / 1000, endBy = talk.end;
  ev('note_end', { dur: Math.round(dur * 10) / 10, by: endBy, sent: !!sendIt });
  r.onstop = () => {
    const blob = new Blob(talk.chunks, { type: r.mimeType || 'audio/webm' });
    if (sendIt && blob.size > 1500) upload(blob, talk.sid, talk.t, dur, endBy, talk.mic, Date.now(), onPanel);
    else if (sendIt) { toast('too short: hold a little longer'); keyState('talk', 'mic', 'Hold to talk'); }
    if (!micKeep) closeMic();
  };
  r.stop();
}
const METER_N = 16;
$('meter').innerHTML = '<b></b>'.repeat(METER_N);
const meter = { ctx: null, an: null, src: null, raf: 0 };
function meterStart(stream) {
  try {
    meter.ctx = meter.ctx || new (window.AudioContext || window.webkitAudioContext)();
    meter.src = meter.ctx.createMediaStreamSource(stream);
    meter.an = meter.ctx.createAnalyser(); meter.an.fftSize = 1024;
    meter.src.connect(meter.an);
    const buf = new Float32Array(meter.an.fftSize), bars = $('meter').children;
    const tick = () => {
      meter.an.getFloatTimeDomainData(buf);
      let p = 0; for (const v of buf) p = Math.max(p, Math.abs(v));
      const lit = Math.round(Math.max(0, Math.min(1, (20 * Math.log10(p + 1e-6) + 50) / 50)) * METER_N);
      for (let i = 0; i < METER_N; i++) bars[i].classList.toggle('lit', i < lit);
      meter.raf = requestAnimationFrame(tick);
    };
    tick();
  } catch (e) {}
}
function meterStop() {
  cancelAnimationFrame(meter.raf);
  try { meter.src && meter.src.disconnect(); } catch (e) {}
  [...$('meter').children].forEach((b) => b.classList.remove('lit'));
}
const pending = [];
async function upload(blob, s, t, dur, endBy, mic, ended, panel) {
  const u = `api/voice?sid=${encodeURIComponent(s || '')}&t=${t == null ? '' : t}` + (dur ? `&dur=${dur.toFixed(1)}&end=${endBy || 'press'}` : '') +
    (mic ? `&mic=${encodeURIComponent(mic)}` : '') + (ended ? `&ago=${((Date.now() - ended) / 1000).toFixed(2)}` : '') +
    (panel ? `&panel=${encodeURIComponent(panel)}` : '');
  try {
    const r = await fetch(u, { method: 'POST', headers: { 'Content-Type': blob.type }, body: blob });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error);
    addFeed({ me: true, id: j.id, ts: new Date().toTimeString().slice(0, 5), text: 'voice note' + (j.heard && j.heard.of ? ' at ' + j.heard.of : '') + ', transcribing' });
    toast('sent'); buzz([30, 60, 30]); setTimeout(() => cue('sent'), 350);
    if (!talk.rec) { keyState('talk', 'sent', 'Sent', 'sent'); setTimeout(() => { if (!talk.rec) keyState('talk', 'mic', 'Hold to talk'); }, 1400); }
  } catch (e) {
    pending.push([blob, s, t, dur, endBy, mic, ended, panel]); toast('offline: the note waits and sends when you are back'); cue('error');
    if (!talk.rec) keyState('talk', 'blocked', 'Waiting to send');
  }
}
setInterval(() => { if (navigator.onLine && pending.length) { const p = pending.splice(0); p.forEach((x) => upload(...x)); } if (outbox.length) flush(); }, 8000);
const T = $('talk');
// a still press records; a touch that moves is a scroll past the key and never starts a note (Nate 10-07 15:13:
// "getting annoying"). The press waits STILL_MS for the finger to settle; moving past STILL_PX, or the browser
// taking the touch for a scroll, lets it go
const STILL_PX = 10, STILL_MS = 150;
let arm = null;
const disarm = () => { if (arm) { clearTimeout(arm.t); arm = null; } };
T.addEventListener('pointerdown', (e) => {
  if (talk.toggle) { e.preventDefault(); micStop(true); return; }
  disarm();
  const at = Date.now();
  arm = { x: e.clientX, y: e.clientY, t: setTimeout(() => { arm = null; talk.down = at; micStart(); }, STILL_MS) };
});
T.addEventListener('pointermove', (e) => {
  if (arm && Math.hypot(e.clientX - arm.x, e.clientY - arm.y) > STILL_PX) disarm();
});
T.addEventListener('pointerup', () => {
  if (arm) {                         // a quick still tap: hands-free, as before
    disarm(); talk.toggle = true; micStart(); $('talkhint').textContent = 'hands-free: tap to send'; return;
  }
  if (!talk.rec && !talk.down) return;
  const held = Date.now() - talk.down; talk.down = 0;
  if (held < 400) { talk.toggle = true; $('talkhint').textContent = 'hands-free: tap to send'; return; }
  if (!talk.toggle) micStop(true);
});
T.addEventListener('pointercancel', () => { if (arm) { disarm(); return; } if (!talk.toggle) micStop(false); });
T.addEventListener('contextmenu', (e) => e.preventDefault());

// ---- what the agents put here
// what they loved and asked for: kept by the server (it survives a reload), newest first
const TAPWORD = { love: 'loved', change: 'change it up', energy_up: 'more energy', energy_down: 'calmer', louder: 'louder',
  quieter: 'quieter', pause: 'pause', resume: 'resume', start_set: 'start a set', rewind: '30 s back' };
const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
const MARK = { loved: svg('M12 20 4.6 12.6a4.4 4.4 0 0 1 7.4-5.1 4.4 4.4 0 0 1 7.4 5.1Z'),
  replay: svg('M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4'), new: 'NEW' };
function renderTaps() {
  const ts = state.taps || [], tl = state.tally || {};
  $('mine').hidden = !ts.length;
  $('tally').textContent = Object.keys(tl).length ? 'today: ' + Object.entries(tl).map(([k, n]) => `${TAPWORD[k] || k} ${n}`).join(', ') : '';
  $('taps').innerHTML = ts.map((x) => `<div><time>${esc((x.ts || '').slice(11, 16))}</time><span>${x.what === 'mood' ? 'mood: ' + esc(x.mood) : esc(TAPWORD[x.what] || x.what)}`
    + `${x.now ? ' <span class="me">' + (clockMode && x.into_s != null ? mmss(x.into_s) + ' into ' : 'during ') + esc(x.now) + '</span>' : x.of ? ' <span class="me">at ' + esc(x.of) + '</span>' : ''}</span></div>`).join('');
}
// The phone's player pauses when the network stalls and carries on from there, so every stall adds to the delay
// (17 s, then 40 s on a walk, 10-06). Past CATCH_S behind it plays 8 % faster (the pitch is kept) until it is close
// to the room again, and the Live key says how far behind it is.
// Faster only while the phone holds BUF_GO seconds of sound ahead of the playhead: on 4G the network often delivers
// barely faster than real time, and 1.08x then drained the buffer and stalled every 4 to 9 s (Nate 10-07 07:44,
// "cutouts on the phone that are not on the speakers": 32 stalls in 45 min, every one at 1.08x). A stall while
// catching up sets the rate back and holds it at 1 for HOLD_MS: a steady delay beats a gap every few seconds.
const CATCH_S = 15, CLOSE_S = 6, CATCH_RATE = 1.08, BUF_GO = 4, BUF_STOP = 2, HOLD_MS = 120000;
let catchHold = 0;
function bufAhead() {
  const b = audio.buffered, t = audio.currentTime;
  for (let i = 0; i < b.length; i++) if (b.start(i) <= t + 0.1 && b.end(i) >= t) return b.end(i) - t;
  return 0;
}
function catchUp(b) {
  if (!want || b == null || clip.el) { if (audio.playbackRate !== 1) audio.playbackRate = 1; return; }
  const buf = bufAhead();
  if (b > CATCH_S && audio.playbackRate === 1 && buf >= BUF_GO && Date.now() > catchHold) {
    audio.preservesPitch = true; audio.playbackRate = CATCH_RATE; ev('catch_up', { behind: Math.round(b), buf: +buf.toFixed(1) });
  } else if (audio.playbackRate !== 1 && (b < CLOSE_S || buf < BUF_STOP)) audio.playbackRate = 1;
  $('golive').textContent = b > CATCH_S ? `Live -${Math.round(b)} s` : 'Live';
}
let clockMode = store.get('clock', false), polledAt = Date.now();
const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
function showInto() {                      // how far into the piece they are hearing, ticking between polls
  const h = state.heard || {};
  if (state.into_s == null) { $('heardcap').textContent = 'Into the piece'; $('bar').innerHTML = '<span>-:--</span>'; return; }
  const s = Math.max(0, state.into_s + (Date.now() - polledAt) / 1000 - (want && h.behind_s ? h.behind_s : 0));
  $('heardcap').textContent = 'Into the piece, ' + (state.clock || '').slice(0, 5);
  $('bar').innerHTML = `${mmss(s)}<span> in</span>`;
}
setInterval(() => { if (clockMode) showInto(); showPos(); }, 1000);
// Nate 10-06 09:12: "something that's always on screen ... that shows where we are in the song". The DJ sends the
// piece's length and sections with phone_now; the line runs between polls, in time or bars like the counter.
let posKey = '';
function showPos() {
  const sh = state.shape || {}, len = sh.length_s, e = state.engine || {}, h = state.heard || {};
  const ok = len && state.into_s != null && sh.of && sh.of === e.now;
  $('pos').hidden = !ok; if (!ok) return;
  const s = Math.min(len, Math.max(0, state.into_s + (Date.now() - polledAt) / 1000 - (want && h.behind_s ? h.behind_s : 0)));
  const bars = (x) => Math.floor(x * (e.bpm || 120) / 240) + 1;
  const fmt = (x) => clockMode ? mmss(x) : 'bar ' + bars(x);
  const secs = sh.sections || [];
  const k = JSON.stringify([sh.of, len, secs]);
  if (k !== posKey) {
    posKey = k;
    $('postrack').querySelectorAll('b').forEach((b) => b.remove());
    secs.forEach((x) => { if (x.at_s > 0 && x.at_s < len) { const b = document.createElement('b'); b.style.left = (100 * x.at_s / len) + '%'; b.title = x.label; $('postrack').appendChild(b); } });
  }
  $('posfill').style.width = (100 * s / len) + '%';
  const cur = secs.filter((x) => x.at_s <= s).pop(), nx = secs.find((x) => x.at_s > s);
  $('poselapsed').textContent = `${fmt(s)} / ${fmt(len)}` + (cur && cur.label ? ` · ${cur.label}` : '');
  $('posnext').textContent = nx ? `${nx.label || 'next'} in ${mmss(nx.at_s - s)}` : `ends in ${mmss(len - s)}`;
}
function clockLabel() { $('clockset').textContent = clockMode ? 'Time' : 'Bars'; }
clockLabel();
$('clockset').onclick = () => { clockMode = !clockMode; store.set('clock', clockMode); clockLabel(); ev('setting', { clock: clockMode ? 'time' : 'bars' }); render(); };

// ---- the vibe: an agent sets the page to fit the music (phone_vibe); the server checked it, the page applies it
let vibeKey = '';
const fontsLoaded = new Set();
function applyVibe(v) {
  if (!v || !v.css) return;
  const k = JSON.stringify(v); if (k === vibeKey) return; vibeKey = k;
  const root = document.documentElement.style;
  const bpm = (state.engine && state.engine.bpm) || 120;
  const ms = v.ramp_beats ? v.ramp_beats * 60000 / bpm : (v.transition_ms || 0);
  root.setProperty('--vt', Math.round(ms) + 'ms');
  Object.entries(v.css).forEach(([n, val]) => root.setProperty(n, val));
  if (v.font_css && !fontsLoaded.has(v.font_css)) {
    fontsLoaded.add(v.font_css); const l = document.createElement('link'); l.rel = 'stylesheet'; l.href = v.font_css; document.head.appendChild(l);
  }
  const meta = document.querySelector('meta[name=theme-color]'); if (meta) meta.content = v.css['--ground'];
  const bg = $('bg');
  if (v.image) { bg.style.backgroundImage = `url("${v.image}")`; bg.style.filter = `blur(${v.blur}px)`; bg.classList.add('on'); $('bgdim').style.opacity = v.dim; }
  else { bg.classList.remove('on'); $('bgdim').style.opacity = 0; }
  fx.set(v.layers || [{ effect: v.effect, intensity: v.intensity, speed: 1, density: 0.5, size: 1, angle: 8, opacity: 1 }],
    v.css['--accent'], v.css['--ink'], ms, v.hue_drift);
}
// the background: up to three effect layers on one canvas, behind the page (ledger:M160); a new look crossfades in
// over its transition; still while hidden, and for anyone who asked for less motion
const fx = (() => {
  const cv = $('fx'), cx = cv.getContext('2d');
  let layers = [], old = [], fadeAt = 0, fadeMs = 1, drift = 0, raf = 0, last = 0, W = 0, H = 0, dpr = 1;
  let acc = '#ffffff', ink = '#ffffff';
  const still = matchMedia('(prefers-reduced-motion: reduce)');
  const COUNT = { rain: 280, particles: 140, grain: 1, aurora: 3, pulse: 1, none: 0 };
  function size() {
    dpr = Math.min(2, window.devicePixelRatio || 1); W = window.innerWidth; H = window.innerHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); cv.style.width = W + 'px'; cv.style.height = H + 'px';
  }
  function seed(L) {
    const n = L.effect === 'aurora' ? 3 : Math.round((COUNT[L.effect] || 0) * (0.1 + 0.9 * L.density) * (0.4 + 0.6 * L.intensity));
    L.parts = Array.from({ length: n }, () => ({ x: Math.random() * W, y: Math.random() * H, v: 0.4 + Math.random(), r: Math.random() }));
  }
  function draw(L, t, dt, k) {
    const a = (0.15 + L.intensity * 0.5) * L.opacity * k;
    if (a <= 0.001) return;
    const c1 = L.color || (L.effect === 'rain' || L.effect === 'grain' ? ink : acc), c2 = L.color2 || ink;
    cx.globalCompositeOperation = L.op || 'source-over';
    if (L.effect === 'rain') {
      const sl = Math.tan(L.angle * Math.PI / 180), len = (14 + 8) * L.size, sp = (500 + 500) * L.speed;
      cx.strokeStyle = c1; cx.lineWidth = Math.max(0.5, L.size); cx.globalAlpha = a * 0.5; cx.beginPath();
      for (const p of L.parts) {
        const v = (0.5 + p.v * 0.5) * sp;
        p.y += v * dt; p.x += v * sl * dt;
        if (p.y > H) { p.y = -20; p.x = Math.random() * (W + H * Math.abs(sl)) - (sl > 0 ? H * sl : 0); }
        cx.moveTo(p.x, p.y); cx.lineTo(p.x - len * sl * (0.6 + p.v * 0.4), p.y - len * (0.6 + p.v * 0.4));
      }
      cx.stroke();
    } else if (L.effect === 'particles') {
      cx.fillStyle = c1;
      for (const p of L.parts) {
        p.y -= p.v * 8 * L.speed * dt; p.x += Math.sin(t / 3000 * L.speed + p.r * 6) * 6 * L.speed * dt;
        if (p.y < -4) { p.y = H + 4; p.x = Math.random() * W; }
        cx.globalAlpha = a * (0.4 + 0.6 * Math.abs(Math.sin(t / 900 * L.speed + p.r * 9)));
        cx.beginPath(); cx.arc(p.x, p.y, (1 + p.r * 1.6) * L.size, 0, 6.283); cx.fill();
      }
    } else if (L.effect === 'pulse') {
      const bpm = (state.engine && state.engine.bpm) || 120, b = heardBeat();
      const ph = (((b != null ? b : (t / 1000) * bpm / 60) / Math.max(0.25, L.speed)) % 1 + 1) % 1;
      const e = Math.exp(-ph * 5), g = cx.createRadialGradient(W / 2, H + 40, 10, W / 2, H + 40, H * (0.55 + 0.25 * e) * L.size);
      g.addColorStop(0, c1); g.addColorStop(1, 'transparent'); cx.globalAlpha = a * (0.25 + 0.75 * e); cx.fillStyle = g; cx.fillRect(0, 0, W, H);
    } else if (L.effect === 'grain') {
      cx.fillStyle = c1; cx.globalAlpha = a * 0.35;
      const n = 1800 * (0.1 + 0.9 * L.density), sz = Math.max(1, L.size);
      for (let i = 0; i < n; i++) cx.fillRect(Math.random() * W, Math.random() * H, sz, sz);
    } else if (L.effect === 'aurora') {
      L.parts.forEach((p, i) => {
        const sp = L.speed, x = W * (0.5 + 0.4 * Math.sin(t * sp / (9000 + i * 2300) + p.r * 6)), y = H * (0.3 + 0.3 * Math.cos(t * sp / (11000 + i * 1700) + p.r * 4));
        const g = cx.createRadialGradient(x, y, 0, x, y, Math.max(W, H) * 0.6 * L.size); g.addColorStop(0, i === 1 ? c2 : c1); g.addColorStop(1, 'transparent');
        cx.globalAlpha = a * (i === 1 ? 0.12 : 0.3) * Math.min(1.6, 0.4 + 1.2 * L.density); cx.fillStyle = g; cx.fillRect(0, 0, W, H);
      });
    }
  }
  function frame(t) {
    raf = 0;
    if (document.visibilityState !== 'visible' || !(layers.length || old.length || hits.length)) return;
    raf = requestAnimationFrame(frame);
    if (t - last < 33) return;                                       // about 30 frames a second is plenty
    const dt = Math.min(0.1, (t - last) / 1000); last = t;
    cx.setTransform(dpr, 0, 0, dpr, 0, 0); cx.globalCompositeOperation = 'source-over'; cx.clearRect(0, 0, W, H);
    const k = Math.min(1, (performance.now() - fadeAt) / fadeMs);
    if (k >= 1) old = [];
    for (const L of old) draw(L, t, dt, 1 - k);
    for (const L of layers) draw(L, t, dt, k);
    hitDraw(t);
    cx.globalAlpha = 1; cx.globalCompositeOperation = 'source-over';
    cv.style.filter = drift ? `hue-rotate(${(drift * t / 60000) % 360}deg)` : '';
  }
  // the notes' reactions (ledger:M160 phase 2): short-lived marks drawn over the layers
  let hits = [];
  function hitDraw(t) {
    const now = performance.now();
    hits = hits.filter((h) => now - h.at < h.life);
    for (const h of hits) {
      const k = 1 - (now - h.at) / h.life, a = h.amount * k;
      cx.globalCompositeOperation = 'lighter';
      if (h.do === 'flash') { cx.globalAlpha = a * 0.35; cx.fillStyle = h.color; cx.fillRect(0, 0, W, H); }
      else if (h.do === 'glow') {
        const g = cx.createRadialGradient(W / 2, H + 30, 10, W / 2, H + 30, H * (0.35 + 0.35 * h.amount));
        g.addColorStop(0, h.color); g.addColorStop(1, 'transparent'); cx.globalAlpha = a * 0.8; cx.fillStyle = g; cx.fillRect(0, 0, W, H);
      } else if (h.do === 'burst' || h.do === 'sparks') {
        cx.fillStyle = h.color;
        for (const p of h.parts) { const d = (1 - k) * p.v; cx.globalAlpha = a; cx.beginPath(); cx.arc(h.x + Math.cos(p.a) * d, h.y + Math.sin(p.a) * d, p.r, 0, 6.283); cx.fill(); }
      } else if (h.do === 'drops') {
        const y = (1 - k) * H * 1.1; cx.strokeStyle = h.color; cx.globalAlpha = a; cx.lineWidth = 2;
        cx.beginPath(); cx.moveTo(h.x, y - 40); cx.lineTo(h.x, y); cx.stroke();
      } else if (h.do === 'ring') {
        cx.strokeStyle = h.color; cx.globalAlpha = a; cx.lineWidth = 2;
        cx.beginPath(); cx.arc(W / 2, H / 2, (1 - k) * Math.max(W, H) * 0.6, 0, 6.283); cx.stroke();
      }
    }
    return hits.length;
  }
  function go() { if (!raf && (layers.length || old.length || hits.length) && !still.matches && document.visibilityState === 'visible') raf = requestAnimationFrame(frame); }
  window.addEventListener('resize', () => { size(); layers.forEach(seed); });
  document.addEventListener('visibilitychange', go);
  return {
    set(ls, a, n, ms, hue) {
      acc = a || acc; ink = n || ink; drift = hue || 0; size();
      const next = (ls || []).filter((L) => L && L.effect && L.effect !== 'none').map((L) => Object.assign({}, L));
      next.forEach(seed);
      old = layers; layers = next; fadeAt = performance.now(); fadeMs = Math.max(1, ms || 1);
      if (still.matches || !(layers.length || old.length)) { if (raf) cancelAnimationFrame(raf); raf = 0; cx.clearRect(0, 0, cv.width, cv.height); return; }
      go();
    },
    hit(x) {
      if (still.matches) return;
      const life = { flash: 220, glow: 420, burst: 600, sparks: 300, drops: 900, ring: 900 }[x.do] || 400;
      const n = x.do === 'burst' ? 18 : x.do === 'sparks' ? 6 : 0;
      hits.push(Object.assign({ at: performance.now(), life, x: Math.random() * W, y: Math.random() * H * 0.8,
        parts: Array.from({ length: n }, () => ({ a: Math.random() * 6.283, v: 40 + Math.random() * 120, r: 1 + Math.random() * 2 })) }, x));
      hits.splice(0, Math.max(0, hits.length - 60)); go();
    },
    get hits() { return hits.length; },
    get kind() { return layers.map((L) => L.effect).join('+') || 'none'; },
    get layers() { return layers; },
  };
})();

// where the phone is in the set, in beats, ticking between polls: what they hear when on the stream, else the room
function heardBeat() {
  const h = state.heard || {}, r = state.room || {}, bpm = (state.engine && state.engine.bpm) || 0;
  const dt = ((Date.now() - polledAt) / 1000) * bpm / 60;
  if (want && h.beat != null) return h.beat + dt;
  if (r.beat != null) return r.beat + dt;
  return null;
}
// scheduled looks (phone_vibe at='bar:N'): each lands on its bar as this phone hears it; the server folds a move
// into the standing vibe a few bars later, so this only has to be right at the moment
let movesLocal = null;
function vibeNow() {
  const moves = movesLocal || state.vibe_moves || [], b = heardBeat(), bpb = (state.engine && state.engine.bpb) || 4;
  let v = state.vibe;
  if (b != null) for (const m of moves) if (b >= (m.at_bar - 1) * bpb - 0.02) v = Object.assign({}, m.vibe, { ramp_beats: m.ramp_beats });
  return v;
}
setInterval(() => { if (state.vibe && document.visibilityState === 'visible') applyVibe(vibeNow()); }, 50);
// the set's notes on the beat this phone hears (phone_vibe react=): each onset fires once, as it reaches the ear
const fired = new Set();
function reactFor(track, map) {
  if (map[track]) return map[track];
  const k = Object.keys(map).find((p) => p.endsWith('*') && track.startsWith(p.slice(0, -1)));
  return k ? map[k] : null;
}
setInterval(() => {
  const v = vibeNow(), map = v && v.react, b = heardBeat();
  if (!map || b == null || document.visibilityState !== 'visible') return;
  const acc = (v.css && v.css['--accent']) || '#ffffff';
  for (const o of state.onsets || []) {
    const key = o.t + '@' + o.b;
    if (o.b > b || o.b < b - 0.5 || fired.has(key)) continue;
    fired.add(key);
    const r = reactFor(o.t, map);
    if (r) fx.hit({ do: r.do, color: r.color || acc, amount: r.amount * Math.min(1, Math.max(0.3, (o.l + 40) / 34)) });
  }
  if (fired.size > 2000) fired.clear();
}, 30);

const feedItems = [];
function addFeed(it) { feedItems.unshift(it); feedItems.splice(12); renderFeed(); }
function renderFeed() {
  const caps = (state.captions || []).slice().reverse().map((c) => ({ text: c.text, who: c.who, ts: (c.ts || '').slice(11, 16) }));
  const mine = feedItems.filter((x) => x.me);
  const all = mine.concat(caps).slice(0, 10);
  $('feed').innerHTML = all.length ? all.map((c) => `<div><time>${esc(c.ts || '')}</time><span class="${c.me ? 'me' : ''}">${c.me ? 'you: ' : ''}${esc(c.text)}</span></div>`).join('')
    : '<div><time></time><span class="me">nothing yet</span></div>';
}
const clip = { el: null, resume: false };
function stopClip() {
  if (clip.el) { clip.el.pause(); clip.el = null; document.querySelectorAll('.clip').forEach((c) => c.classList.remove('playing')); }
  if (clip.resume) { clip.resume = false; setPlaying(true); }
}
function playClip(url, box) {
  ev('clip', { url: String(url).split('?')[0].slice(-60) });
  const again = clip.el && clip.el.dataset.url === url;
  if (clip.el) { clip.el.pause(); clip.el = null; document.querySelectorAll('.clip').forEach((c) => c.classList.remove('playing')); }
  if (again) return stopClip();
  if (want) { clip.resume = true; setPlaying(false); }
  const a = new Audio(url); a.dataset.url = url; clip.el = a; box.classList.add('playing');
  a.onended = () => { box.classList.remove('playing'); clip.el = null; };
  a.play().catch(() => toast('could not play that clip'));
}
// Messages never pop up (Nate 10-07 11:17: "these are modals that block me ... it should let me know, in the top
// right corner ... and then I can click on it to switch to that message"): a new one buzzes once and shows on the
// corner key; he opens it when he chooses, switches between open ones on the tabs, and Later puts it back
let shown = null;
const notified = new Set(), readPanels = new Set(); const answered = new Set();   // answered here: gone at once, whatever a stale state says (Nate 10-07 12:01)
// priority (the sender's: 'needs you', 'normal', 'low'): the most pressing first, then the newest
const PRI = { 'needs you': 0, normal: 1, low: 2 };
const rank = (x) => (x && x.priority in PRI ? PRI[x.priority] : 1);
const byPriority = (a, b) => rank(a.x) - rank(b.x) || b.i - a.i;
const sortPri = (xs) => xs.map((x, i) => ({ x, i })).sort(byPriority).map((o) => o.x);
const openPanels = () => sortPri((state.panels || []).filter((x) => !answered.has(x.id)));
function answeredHere(id) { answered.add(id); if (shown === id) closeSheet(); else renderBadge(); }
function closeSheet() { $('sheet').classList.remove('show'); stopClip(); $('panel').querySelectorAll('video').forEach((v) => v.pause()); shown = null; renderBadge(); }
// notes (the DJ's and the Director's captions) are only a toast and a chime when they land; the corner key keeps
// count of the ones not looked at yet, so one that came in during a voice note is still one tap away (Nate 10-07
// 12:20: "I heard a message ... but I was recording")
const notesSeen = () => store.get('notes_seen', '');
const newNotes = () => (state.captions || []).filter((c) => (c.ts || '') > notesSeen());
function renderBadge() {
  const ps = openPanels(), nn = newNotes().length;
  const b = $('msgs');
  b.hidden = !ps.length && !(state.captions || []).length && !state.pinned;
  // lit while anything waits: a panel put off with Later is still open and still counted (Nate 10-07)
  b.classList.toggle('new', ps.length > 0 || nn > 0);
  b.classList.toggle('urgent', ps.some((x) => x.priority === 'needs you') || newNotes().some((c) => c.priority === 'needs you'));
  b.textContent = [ps.length ? `${ps.length} ${ps.length === 1 ? 'question' : 'questions'}` : '', nn ? `${nn} new` : '']
    .filter(Boolean).join(' · ') || 'Notes';
}
function showNotes() {
  shown = '__notes';
  const caps = (state.captions || []).slice(-12).reverse(), seen = notesSeen();    // newest first
  let h = tabsHtml(openPanels(), '__notes') + '<h2>Notes</h2><div class="notes">';
  if (state.pinned) h += `<div class="item"><span class="cap">Since you left</span>${esc(state.pinned.text)}</div>`;
  h += sortPri(caps.slice().reverse()).map((c) => `<div class="item${(c.ts || '') > seen ? ' new' : ''}${c.priority === 'needs you' ? ' urgent' : ''}${c.priority === 'low' ? ' low' : ''}"><span class="cap">${esc(c.who || 'DJ')} ${esc((c.ts || '').slice(11, 16))}</span>${esc(c.text)}</div>`).join('')
    || '<div class="item">nothing yet</div>';
  $('panel').innerHTML = h + '</div>';
  if (caps.length) store.set('notes_seen', caps[0].ts || '');
  $('sheet').classList.add('show');
  bindTabs(); renderBadge(); ev('notes_open', { n: caps.length });
}
function renderPanel() {
  const ps = openPanels();
  const fresh = ps.filter((x) => !notified.has(x.id));
  if (fresh.length) { fresh.forEach((x) => notified.add(x.id)); if (!talk.rec) buzz([60]); }   // a nudge, never a sound or a sheet
  renderBadge();
  if (!shown) return;
  if (shown === '__notes') return;
  if (!ps.some((x) => x.id === shown)) { closeSheet(); return; }       // answered or closed by the agent
  const tabs = $('panel').querySelector('.ptabs');
  if (tabs && tabs.dataset.n !== String(ps.length)) { tabs.outerHTML = tabsHtml(ps, shown); bindTabs(); }
}
function tabsHtml(ps, id) {
  return `<div class="ptabs" data-n="${ps.length}">` + (ps.length > 1 || (ps.length && id === '__notes') ? ps.map((x) => `<button data-tab="${esc(x.id)}" class="${x.id === id ? 'sel' : ''}${readPanels.has(x.id) ? '' : ' new'}">${esc((x.title || 'message').slice(0, 28))}</button>`).join('') : '')
    + `<button data-tab="__notes" class="${id === '__notes' ? 'sel' : ''}${newNotes().length ? ' new' : ''}">Notes</button>`
    + '<button data-later class="later">Later</button></div>';
}
function bindTabs() {
  const box = $('panel');
  box.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => { if (b.dataset.tab === '__notes') { stopClip(); showNotes(); return; } const x = openPanels().find((q) => q.id === b.dataset.tab); if (x) { stopClip(); showPanel(x); } }; });
  const l = box.querySelector('[data-later]');
  if (l) l.onclick = () => { ev('panel_later', { id: shown }); closeSheet(); };
}
function openMessages() {
  const ps = openPanels(), unread = ps.find((x) => !readPanels.has(x.id));
  if (unread) showPanel(unread);
  else if (newNotes().length || !ps.length) showNotes();
  else showPanel(ps[0]);
}
function showPanel(p) {
  shown = p.id; readPanels.add(p.id); ev('panel_open', { id: p.id, title: p.title || '' });
  const box = $('panel');
  let h = tabsHtml(openPanels(), p.id) + `<h2>${esc(p.title)}</h2>` + (p.text ? `<p>${esc(p.text)}</p>` : '') + (p.image ? `<img src="${esc(p.image)}">` : '')
    + (p.video ? `<video src="${esc(p.video)}" controls playsinline preload="metadata"></video>` : '');
  if (p.kind === 'exam') {
    h += (p.clips || []).map((c, i) => `<div class="clip" data-i="${i}"><button class="playclip" data-url="${esc(c.url)}">Play ${esc(c.label)}</button>`
      + (c.note ? `<div class="hint">${esc(c.note)}</div>` : '')
      + (p.chips && p.chips.length ? `<div class="chips">${p.chips.map((w) => `<button data-chip="${esc(w)}">${esc(w)}</button>`).join('')}</div>` : '') + '</div>').join('');
    if (p.choices && p.choices.length) h += `<div class="btns">${p.choices.map((c) => `<button class="choice" data-choice="${esc(c)}">${esc(c)}</button>`).join('')}</div>`;
    const dev = store.get('listen_on', '');
    h += `<div class="hint">Listening on</div><div class="chips" id="listenon">${['Earbuds', 'Headphones', 'Phone speaker', 'Speaker'].map((w) => `<button data-on="${w}" class="${w === dev ? 'sel' : ''}">${w}</button>`).join('')}</div>`;
    h += `<textarea id="examnote" placeholder="a note (optional)"></textarea><div class="btns"><button id="submit" style="font-weight:700">Submit</button><button data-notnow>Not now</button></div>`;
  } else {
    h += (p.inputs || []).map((x) => {
      const lab = x.label ? `<div class="inlab">${esc(x.label)}</div>` : '';
      if (x.kind === 'text') return lab + `<textarea data-in="${esc(x.id)}" data-kind="text">${esc(x.value || '')}</textarea>`;
      if (x.kind === 'toggle') return `<div class="chips"><button data-in="${esc(x.id)}" data-kind="toggle" class="${x.value ? 'sel' : ''}">${esc(x.label || x.id)}: ${x.value ? 'on' : 'off'}</button></div>`;
      const val = [].concat(x.value || []);
      return lab + `<div class="chips" data-in="${esc(x.id)}" data-kind="${x.kind}">${(x.options || []).map((o) => `<button data-opt="${esc(o)}" class="${val.includes(o) ? 'sel' : ''}">${esc(o)}</button>`).join('')}</div>`;
    }).join('');
    h += `<div class="btns">${(p.buttons || ['OK']).map((b) => `<button data-answer="${esc(b)}">${esc(b)}</button>`).join('')}`
      + `<button data-notnow>Not now</button></div>`;
  }
  // every panel takes a voice reply too (Nate 10-07 08:52: "I felt like I should be able to say more"): tap to
  // record, tap again to send; it reaches the agent that sent the panel, and the panel stays open
  h += `<div class="btns"><button id="panelrec" class="rec">Say more</button></div>`;
  box.innerHTML = h;
  box.querySelectorAll('[data-kind="toggle"]').forEach((b) => { b.onclick = () => {
    const on = !b.classList.contains('sel'); b.classList.toggle('sel', on);
    const x = (p.inputs || []).find((i) => i.id === b.dataset.in); b.textContent = `${(x && x.label) || b.dataset.in}: ${on ? 'on' : 'off'}`; }; });
  box.querySelectorAll('.chips[data-kind="choice"], .chips[data-kind="check"]').forEach((g) => {
    g.querySelectorAll('[data-opt]').forEach((b) => { b.onclick = () => {
      if (g.dataset.kind === 'choice') g.querySelectorAll('[data-opt]').forEach((o) => { if (o !== b) o.classList.remove('sel'); });
      b.classList.toggle('sel'); }; }); });
  const values = () => {
    const v = {};
    box.querySelectorAll('[data-kind="text"]').forEach((t) => { v[t.dataset.in] = t.value; });
    box.querySelectorAll('[data-kind="toggle"]').forEach((b) => { v[b.dataset.in] = b.classList.contains('sel'); });
    box.querySelectorAll('.chips[data-kind="choice"]').forEach((g) => { const s = g.querySelector('.sel'); v[g.dataset.in] = s ? s.dataset.opt : null; });
    box.querySelectorAll('.chips[data-kind="check"]').forEach((g) => { v[g.dataset.in] = [...g.querySelectorAll('.sel')].map((s) => s.dataset.opt); });
    return v;
  };
  $('panelrec').onclick = async () => {
    if (talk.rec && talk.panel === p.id) { micStop(true); return; }
    if (talk.rec) { toast('finish the note you are recording first'); return; }
    box.querySelectorAll('video').forEach((v) => v.pause());       // the note is not said over the video
    talk.panel = p.id; await micStart();
    if (talk.rec) { talk.toggle = true; $('panelrec').classList.add('on'); $('panelrec').textContent = 'Recording: tap to send'; }
    else talk.panel = null;
  };
  $('sheet').classList.add('show');
  bindTabs(); renderBadge();
  box.querySelectorAll('[data-answer]').forEach((b) => { b.onclick = async () => {
    // a pick is part of the answer (Live DJ 10-07: Send with nothing picked came in as {"which": null})
    const unpicked = (p.inputs || []).find((x) => x.kind === 'choice' && !x.optional
      && !box.querySelector(`.chips[data-in="${CSS.escape(x.id)}"] .sel`));
    if (unpicked) { toast(`${unpicked.label ? unpicked.label + ': ' : ''}pick one first, or tap Not now`); return; }
    if (talk.rec && talk.panel === p.id) micStop(true);        // what they were saying goes too
    const j = await send('/api/answer', { id: p.id, answer: b.dataset.answer, ...(p.inputs ? { values: values() } : {}) });
    if (j) { if (!j.gone) toast('sent: ' + b.dataset.answer); answeredHere(p.id); } } });
  const nn = box.querySelector('[data-notnow]');
  if (nn) nn.onclick = async () => {                          // set aside: the agent reads a dismissal, not an answer
    if (talk.rec && talk.panel === p.id) micStop(true);
    const j = await send('/api/answer', { id: p.id, dismissed: true });
    if (j) { if (!j.gone) toast('set aside'); answeredHere(p.id); }
  };
  box.querySelectorAll('.playclip').forEach((b) => { b.onclick = () => playClip(b.dataset.url, b.closest('.clip')); });
  box.querySelectorAll('[data-chip]').forEach((b) => { b.onclick = () => b.classList.toggle('sel'); });
  box.querySelectorAll('[data-on]').forEach((b) => { b.onclick = () => { box.querySelectorAll('[data-on]').forEach((x) => x.classList.remove('sel')); b.classList.add('sel'); store.set('listen_on', b.dataset.on); }; });
  box.querySelectorAll('[data-choice]').forEach((b) => { b.onclick = () => { box.querySelectorAll('[data-choice]').forEach((x) => x.classList.remove('sel')); b.classList.add('sel'); }; });
  const sub = box.querySelector('#submit');
  if (sub) sub.onclick = async () => {
    const answers = { clips: {}, choice: (box.querySelector('.choice.sel') || {}).dataset ? (box.querySelector('.choice.sel') || { dataset: {} }).dataset.choice || null : null,
      note: (box.querySelector('#examnote') || {}).value || '' };
    box.querySelectorAll('.clip').forEach((c) => { const lab = p.clips[+c.dataset.i].label; answers.clips[lab] = [...c.querySelectorAll('[data-chip].sel')].map((x) => x.dataset.chip); });
    if (p.choices && p.choices.length && !answers.choice) { toast('pick one answer first'); return; }
    answers.device = await deviceInfo((box.querySelector('[data-on].sel') || { dataset: {} }).dataset.on || '');
    const j = await send('/api/answer', { id: p.id, answers });
    if (j) { if (!j.gone) toast('submitted, thank you'); stopClip(); answeredHere(p.id); }
  };
}
// What the person listened on, for an exam's answers: their pick, and what the browser can name (Chrome on a computer
// lists outputs; a phone usually does not, so the pick matters there). Labels need the page's mic permission.
async function deviceInfo(pick) {
  const out = { listening_on: pick || null, mobile: /Mobi|Android/i.test(navigator.userAgent) };
  try {
    const ds = await navigator.mediaDevices.enumerateDevices();
    const o = ds.filter((d) => d.kind === 'audiooutput' && d.label), i = ds.filter((d) => d.kind === 'audioinput' && d.label);
    if (o.length) out.output = (o.find((d) => d.deviceId === 'default') || o[0]).label;
    if (talk.stream && talk.stream.getAudioTracks()[0]) out.mic = talk.stream.getAudioTracks()[0].label;
    else if (i.length) out.mic = (i.find((d) => d.deviceId === 'default') || i[0]).label;
  } catch (e) {}
  return out;
}
function renderOffers() {
  const os = state.offers || [];
  $('offerbox').hidden = !os.length;
  $('offers').innerHTML = os.slice().reverse().map((o) => `<div class="offer"><span>${esc(o.label)}</span><a href="${esc(o.url)}?dl=1" download="${esc(o.name)}">Download</a></div>`).join('');
  $('offers').querySelectorAll('a[download]').forEach((a) => { a.onclick = () => ev('download', { file: a.getAttribute('download') }); });
}
function render() {
  const e = state.engine || {}, h = state.heard || {}, r = state.rec || {};
  $('top').classList.toggle('live', !!e.playing);
  $('livetext').textContent = !navigator.onLine ? 'offline' : e.playing ? (want ? 'live' : 'live, not listening') : 'no set playing';
  $('rec').classList.toggle('on', !!r.on);
  $('rectext').innerHTML = (r.on ? 'ON AIR' : 'REC OFF') + (r.why ? ` <small>${esc(r.why)}</small>` : '');
  const m = /bar (\d+)(?: beat ([\d.]+))?/.exec(h.of || '');
  polledAt = Date.now();
  if (clockMode) showInto();
  else { $('heardcap').textContent = 'Heard'; $('bar').innerHTML = m ? `BAR ${m[1]}<span>.${esc(Math.floor(+(m[2] || 1)))}</span>` : 'BAR <span>---</span>'; }
  movesLocal = null; applyVibe(vibeNow()); showPos();
  SOUNDS = state.sounds || {};
  if (e.now && render.now !== undefined && e.now !== render.now) sound('chapter');   // not on the first look
  render.now = e.now || null;
  $('behind').textContent = want && h.behind_s != null ? '+' + h.behind_s.toFixed(1) + ' s' : '--';
  catchUp(h.behind_s);
  $('now').textContent = e.now || (e.playing ? 'playing' : 'nothing playing');
  $('next').textContent = e.next || '--';
  $('outrow').hidden = !e.playing || !e.output;
  if (e.output) {
    $('outname').textContent = e.output;
    $('follow').classList.toggle('on', !!e.follow);
    $('follow').textContent = 'Follow Windows: ' + (e.follow ? 'on' : 'off');
  }
  $('pinned').hidden = !state.pinned;
  if (state.pinned) {
    $('pinned').innerHTML = `<span class="cap">Since you left</span><span class="body">${esc(state.pinned.text)}</span>`;
    if (!$('pinned').dataset.open) $('pinned').classList.add('clamp');
  }
  const caps = state.captions || [], lastc = caps[caps.length - 1];
  const fresh = lastc && (!state.pinned || lastc.text !== state.pinned.text);
  $('last').hidden = !fresh;
  if (fresh) $('last').innerHTML = `<span class="cap">${esc(lastc.who || 'DJ')} ${esc((lastc.ts || '').slice(11, 16))}</span>${esc(lastc.text)}`;
  $('startset').hidden = !!e.playing;
  const am = state.asked_mood;
  $('askedmood').textContent = am ? `: you asked ${am.mood}, ${(am.ts || '').slice(11, 16)}` : '';
  $('nowmark').className = 'mark ' + (e.now_mark || ''); $('nowmark').innerHTML = MARK[e.now_mark] || '';
  $('nextmark').className = 'mark ' + (e.next_mark || ''); $('nextmark').innerHTML = MARK[e.next_mark] || '';
  renderTaps();
  $('agentbtns').innerHTML = (state.buttons || []).map((b) => `<button data-btn="${esc(b.id)}">${esc(b.label)}</button>`).join('');
  $('agentbtns').querySelectorAll('[data-btn]').forEach((b) => { b.onclick = () => tap('button:' + b.dataset.btn); });
  $('agentwrap').hidden = !(state.buttons || []).length;
  const ls = state.listening || [];
  $('listening').innerHTML = ls.length ? 'listening: ' + esc(ls.join(', ')) : '<span class="warn">nobody listening: notes wait in the inbox</span>';
  (state.voice || []).forEach((v) => { const it = feedItems.find((x) => x.id === v.id); if (it && v.state.startsWith('waiting')) it.text = 'voice note: ' + v.state; });
  renderFeed(); renderPanel(); renderOffers(); mediaSession();
}
function onCmd(c) {
  if (c.type === 'sounds') SOUNDS = c.sounds || {};
  else if (c.type === 'unsay') {
    const gone = new Set(c.texts || []);
    state.captions = (state.captions || []).filter((x) => !gone.has(x.text));
    if (state.pinned && gone.has(state.pinned.text)) state.pinned = null;
    if ([...gone].some((g) => $('toast').textContent.includes(g))) { $('toast').classList.remove('show'); $('toast').textContent = ''; }
    navigator.serviceWorker && navigator.serviceWorker.ready.then((r) => r.getNotifications()).then((ns) => ns.forEach((x) => { if (gone.has(x.body)) x.close(); })).catch(() => {});
    render();
  }
  else if (c.type === 'vibe_moves') { movesLocal = c.moves || []; applyVibe(vibeNow()); }
  else if (c.type === 'vibe') { state.vibe = c.vibe; applyVibe(vibeNow()); }
  else if (c.type === 'caption') { sound('message'); toast(c.text); if (c.buzz) buzz([150, 80, 150]); notifyBg(c.who || 'ismail live', c.text); }
  else if (c.type === 'buzz') buzz(c.pattern);
  else if (c.type === 'restarting') { restarting = Date.now(); holdOn(); toast('updating, back in a few seconds'); }
  else if (c.type === 'say_clip') {                // spoken to the page itself: nobody was on the stream
    const a = new Audio(c.url); a.play().catch(() => { toast((c.who || 'DJ') + ': ' + c.text); buzz([150, 80, 150]); });
  }
  else if (c.type === 'stop_listening') { if (want) { setPlaying(false); cue('end'); toast('stopped listening: press the earbud or Listen to start again'); } }
  else if (c.type === 'heard') { const it = feedItems.find((x) => x.id === c.ref); if (it) it.text = '“' + c.text + '”'; else addFeed({ me: true, id: c.ref, ts: new Date().toTimeString().slice(0, 5), text: '“' + c.text + '”' }); }
  if (c.type === 'offer') sound('offer');
  if (c.type === 'offer' && c.offer && c.offer.auto && document.visibilityState === 'visible') {
    const a = document.createElement('a'); a.href = c.offer.url + '?dl=1'; a.download = c.offer.name; document.body.appendChild(a); a.click(); a.remove(); toast('downloading ' + c.offer.name);
  }
}
// a server restart (an update): told first, the page retries every second, reconnects the stream, and loads the new
// page code the next time it is on screen and idle (the stream position is remembered, so it picks back up)
let boot = null, build = null, restarting = 0, reloadDue = false;
function maybeReload(b) {
  if (b && build && b !== build) reloadDue = true;
  if (!reloadDue || document.visibilityState !== 'visible' || talk.rec || shown) return;
  ev('reload', { why: 'new page code' }); flushEv(true); remember();
  setTimeout(() => location.reload(), 300);
}
document.addEventListener('visibilitychange', () => maybeReload());
async function poll() {
  const was = store.get('listening', 0);            // listening when the page went away: pick it back up
  if (was && Date.now() - was < 30 * 60000 && !want) setPlaying(true);
  for (;;) {
    try {
      const t = heardNow();
      const r = await fetch(`api/state?since=${since}&wait=${first ? 0 : 20}&sid=${sid || ''}&t=${t == null ? '' : t}`, { cache: 'no-store' });
      const j = await r.json();
      if (boot && j.boot !== boot) {             // the server restarted: its commands count from 0 again
        since = 0; first = true; boot = j.boot; restarting = 0;
        if (want) { retry = 0; connect(); }      // and the stream comes back at once, at the live edge
        toast('back');
        continue;
      }
      boot = j.boot; build = build || j.build;
      state = j;
      if (!first) (j.cmds || []).forEach(onCmd);
      since = j.cmd; first = false; poll.wait = 0;
      render();
      maybeReload(j.build);
    } catch (e) {
      const soon = restarting && Date.now() - restarting < 60000;
      $('livetext').textContent = soon ? 'updating, back in a few seconds' : navigator.onLine ? 'the server does not answer, retrying' : 'offline';
      poll.wait = soon ? 1000 : Math.min(30000, (poll.wait || 2000) * 2);   // back off: a phone in a pocket keeps its battery
      await new Promise((ok) => setTimeout(ok, poll.wait));
      continue;
    }
  }
}
$('sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') { ev('panel_close', { id: shown }); closeSheet(); } });
$('msgs').onclick = openMessages;
$('pinned').onclick = () => { $('pinned').dataset.open = $('pinned').classList.toggle('clamp') ? '' : '1'; };
// the set's sound follows the Windows default output, or stays where it is (Nate 10-07: "the way that most
// applications work", switched by him, not by an agent)
$('follow').onclick = async () => {
  const on = !$('follow').classList.contains('on');
  const j = await send('/api/output', { follow: on });
  if (j) toast(on ? 'following Windows output' : 'staying on this output');
};
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
// Install: Chrome offers it once the page qualifies (PNG icons, a service worker); the button appears only then
let installEvt = null;
const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone;
window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); installEvt = e; $('install').hidden = standalone(); });
window.addEventListener('appinstalled', () => { $('install').hidden = true; installEvt = null; toast('installed: open ismail from your home screen'); });
$('install').onclick = async () => { if (!installEvt) return; installEvt.prompt(); await installEvt.userChoice.catch(() => {}); installEvt = null; $('install').hidden = true; };
// Notify: when the page is in the background, what the DJ says or asks also arrives as a phone notification
let notifyOn = store.get('notify', false) && 'Notification' in window && Notification.permission === 'granted';
function notifyLabel() { $('notify').textContent = notifyOn ? 'Notify on' : 'Notify off'; }
if ('Notification' in window && 'serviceWorker' in navigator) { $('notify').hidden = false; notifyLabel(); }
$('notify').onclick = async () => {
  if (!notifyOn && Notification.permission !== 'granted') {
    const p = await Notification.requestPermission().catch(() => 'denied');
    if (p !== 'granted') { toast('notifications are blocked for this page: allow them in Chrome site settings'); return; }
  }
  notifyOn = !notifyOn; store.set('notify', notifyOn); notifyLabel();
};
async function notifyBg(title, body) {
  if (!notifyOn || document.visibilityState === 'visible') return;
  try { const r = await navigator.serviceWorker.ready; r.showNotification(title, { body, tag: 'ismail', renotify: true, icon: 'icon-192.png', badge: 'icon-192.png' }); } catch (e) {}
}
keyState('play', 'play', 'Listen'); keyState('talk', 'mic', 'Hold to talk');
poll();
