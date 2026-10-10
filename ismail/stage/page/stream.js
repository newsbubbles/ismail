// Live sound in the room: ismail live's buses streamed from the PC (server.py relays the engine's /stream), each one
// played from an object's place through an HRTF panner, so the guitar comes out of its amp and the drums from the kit
// (the user, 2026-10-03: "making the track or playing something live originate from ... the amp"). Only ever on a
// command (live `stream`); the HUD sounds (earcons, Claude's voice) stay in the ears as before.
import * as THREE from 'three';

// the audio thread: int16 stereo blocks in, a jitter buffer `lead` seconds deep, resampled to the context's rate;
// mono by default (an amp is a point), stereo on request. Underruns re-prime; a buffer three leads deep skips ahead.
const WORKLET = `
class IsmailFeed extends AudioWorkletProcessor {
  constructor(o) {
    super();
    const p = o.processorOptions;
    this.ratio = p.srcRate / sampleRate; this.cap = Math.ceil(p.srcRate * 4); this.mono = p.mono;
    this.L = new Float32Array(this.cap); this.R = new Float32Array(this.cap);
    this.w = 0; this.r = 0; this.n = 0; this.lead = p.srcRate * p.lead; this.primed = false;
    this.under = 0; this.skips = 0; this.t = 0;
    this.port.onmessage = (e) => this.push(e.data);
  }
  push(buf) {
    const s = new Int16Array(buf), k = s.length >> 1, cap = this.cap;
    for (let i = 0; i < k; i++) { this.L[this.w] = s[2 * i] / 32768; this.R[this.w] = s[2 * i + 1] / 32768; this.w = (this.w + 1) % cap; }
    this.n += k;
    if (this.n > Math.min(cap - 2, this.lead * 3)) { const d = this.n - this.lead; this.r = (this.r + d) % cap; this.n = this.lead; this.skips++; }
  }
  process(_, outs) {
    const o = outs[0], l = o[0], r = o[1] || o[0], m = l.length, cap = this.cap;
    if ((this.t += m) > sampleRate * 2) { this.t = 0; this.port.postMessage({ under: this.under, skips: this.skips, lead_s: this.n / (this.ratio * sampleRate) }); }
    if (!this.primed) {
      if (this.n < this.lead) { l.fill(0); if (r !== l) r.fill(0); return true; }
      this.primed = true;
    }
    for (let i = 0; i < m; i++) {
      if (this.n < 2) { this.primed = false; this.under++; l.fill(0, i); if (r !== l) r.fill(0, i); break; }
      const a = Math.floor(this.r), f = this.r - a, b = (a + 1) % cap;
      const x = this.L[a] + (this.L[b] - this.L[a]) * f, y = this.R[a] + (this.R[b] - this.R[a]) * f;
      if (this.mono) l[i] = 0.5 * (x + y); else { l[i] = x; r[i] = y; }
      this.r += this.ratio; if (this.r >= cap) this.r -= cap; this.n -= this.ratio;
    }
    return true;
  }
}
registerProcessor('ismail-feed', IsmailFeed);
`;

export function listenerOf(ed) {          // one listener on the head, shared with music.js
  let l = ed.camera.children.find((o) => o.type === 'AudioListener');
  if (!l) { l = new THREE.AudioListener(); ed.camera.add(l); }
  return l;
}

export function initStream(ed, live) {
  const on = new Map();                   // id -> {name, at, pa, node, holder, abort, stats}
  let loaded = null;

  function stop(id) {
    const s = on.get(id);
    if (!s) return false;
    on.delete(id);
    s.abort.abort();
    try { s.pa.disconnect(); } catch (_) { /* not connected */ }
    s.node.port.onmessage = null;
    s.node.disconnect();
    s.holder.removeFromParent();
    return true;
  }

  function place(holder, c) {             // at an object (its world position, lifted), or where the user stands
    const it = c.at ? ed.byName.get(c.at) : null;
    if (c.at && !it) throw new Error(`no object "${c.at}" to play from`);
    if (it) { it.obj.updateMatrixWorld(true); holder.position.copy(it.obj.getWorldPosition(new THREE.Vector3())); holder.position.y += c.lift ?? 0.5; }
    return it ? it.name : null;
  }

  async function play(c) {
    const name = c.name || 'master', id = c.stream_id || c.at || name;
    if (!/^(master|(bus|deck):[\w.-]{1,40})$/.test(name)) throw new Error('name: master, bus:<name> or deck:<name>');
    stop(id);
    const listener = listenerOf(ed), ctx = listener.context;
    if (ctx.state !== 'running') await ctx.resume().catch(() => {});
    if (!ctx.audioWorklet) throw new Error('this browser has no AudioWorklet');
    if (!loaded) loaded = ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([WORKLET], { type: 'text/javascript' })));
    await loaded;
    const abort = new AbortController();
    const res = await fetch(`livestream?name=${encodeURIComponent(name)}${c.port ? `&port=${+c.port}` : ''}`,
      { signal: abort.signal, cache: 'no-store' });
    if (!res.ok) throw new Error('stream: ' + (await res.text()).slice(0, 300));
    const rate = +res.headers.get('X-Sample-Rate') || 44100, mono = !c.stereo;
    const node = new AudioWorkletNode(ctx, 'ismail-feed', { numberOfInputs: 0, outputChannelCount: [mono ? 1 : 2],
      processorOptions: { srcRate: rate, lead: c.lead ?? 0.3, mono } });
    const pa = new THREE.PositionalAudio(listener);
    pa.panner.panningModel = 'HRTF';
    pa.setRefDistance(c.ref ?? 1.5);
    pa.setRolloffFactor(c.rolloff ?? 1);
    pa.setVolume(c.volume ?? 0.7);
    pa.setNodeSource(node);
    const holder = new THREE.Object3D();
    const at = place(holder, c);
    ed.scene.add(holder);
    holder.add(pa);
    const s = { name, at, pa, node, holder, abort, stats: null, rate, bytes: 0 };
    on.set(id, s);
    node.port.onmessage = (e) => { const was = s.stats; s.stats = e.data;
      if (was && e.data.under > was.under + 2) live.emit('stream_underrun', { stream: id, ...e.data }); };
    (async () => {                        // network -> audio thread, in whole frames (4 bytes)
      const reader = res.body.getReader();
      let carry = new Uint8Array(0);
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          const u = new Uint8Array(carry.length + value.length);
          u.set(carry); u.set(value, carry.length);
          const whole = u.length - (u.length % 4);
          const out = u.slice(0, whole);
          carry = u.slice(whole);
          s.bytes += whole;
          node.port.postMessage(out.buffer, [out.buffer]);
        }
      } catch (_) { /* stopped */ }
      if (on.get(id) === s) { stop(id); live.emit('stream_end', { stream: id, name, seconds: +(s.bytes / 4 / rate).toFixed(1) }); }
    })();
    live.emit('stream_play', { stream: id, name, at, rate, mono });
    return { stream: id, name, at, rate, mono, lead: c.lead ?? 0.3 };
  }

  live.handlers.stream = async (c) => {
    const id = c.stream_id || c.at || c.name;
    if (c.action === 'stop') {
      if (!id) { const n = on.size; [...on.keys()].forEach(stop); return { stopped: n }; }
      return { stopped: stop(id) };
    }
    if (c.action === 'volume') { const s = on.get(id); if (!s) throw new Error(`no stream ${id}`); s.pa.setVolume(c.volume); return { volume: c.volume }; }
    if (c.action === 'move') { const s = on.get(id); if (!s) throw new Error(`no stream ${id}`); s.at = place(s.holder, c); return { at: s.at }; }
    if (c.action === 'status') return [...on.entries()].map(([k, s]) => ({ stream: k, name: s.name, at: s.at, seconds: +(s.bytes / 4 / s.rate).toFixed(1), ...s.stats }));
    return play(c);
  };
  return { play, stop, on };
}
