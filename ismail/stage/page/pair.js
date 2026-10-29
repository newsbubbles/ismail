// Pairing on the page (the security floor, part B2). The key itself lives in index.html's first script (VR_pair), which
// sends it on every request; this module asks for a code when the page has no key and either a code is waiting (an
// agent ran stage_pair) or the server enforces. On a desktop or a phone: a small box with a code field (a phone shows
// its number keys). In the headset: a number pad panel in front of the person; its digits never go on the live bus.
// Six digits claim at once. A revoked key is forgotten and the page asks again. Events: paired {device, kind},
// pair_failed {why} (never the code). pairFirst() runs before the stage loads: in enforce an unpaired page can load
// nothing, so it asks first (a box: no headset view exists yet, and the Quest browser types into it).
const POLL_MS = 4000, LATER_MS = 120000;     // a code lives two minutes: "Not now" waits that long
const PAD = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'Clear', '0', 'Back'];

function el(tag, css, text) { const e = document.createElement(tag); if (css) e.style.cssText = css; if (text) e.textContent = text; return e; }

function why(e) {
  return /expired|no such code/.test(e.message) ? 'That code did not work (codes last two minutes and work once).'
    : /wait|paused/.test(e.message) ? 'Too many wrong codes: wait a minute.' : e.message;
}

// the box: claim(code) -> {ok, why}; later() hides it for a while (none before the stage loads: there is no later)
function makeBox(P, enforce, claim, later) {
  const box = el('div', 'position:fixed;left:50%;top:18%;transform:translateX(-50%);z-index:60;background:var(--ground,#111);' +
    'color:var(--ink,#eee);border:1px solid var(--accent,#d97757);border-radius:8px;padding:16px 18px;font:14px system-ui,sans-serif;' +
    'width:min(340px,calc(100vw - 32px));box-shadow:0 8px 30px rgba(0,0,0,.5)');
  box.id = 'pairbox';
  const what = P.kind() === 'phone' ? 'phone' : P.kind() === 'headset' ? 'headset' : 'browser';
  box.append(el('div', 'font-weight:600;margin-bottom:6px', `Pair this ${what} with the stage`),
    el('div', 'opacity:.8;margin-bottom:10px', enforce ? 'The stage only answers paired devices. Type the six-digit code your agent gives you.'
      : 'Type the six-digit code your agent gives you.'));
  const input = el('input', 'width:100%;box-sizing:border-box;font:600 24px ui-monospace,monospace;letter-spacing:.3em;padding:8px;' +
    'background:#000;color:#fff;border:1px solid #555;border-radius:6px;text-align:center');
  Object.assign(input, { inputMode: 'numeric', maxLength: 6, autocomplete: 'one-time-code', placeholder: '000000', id: 'paircode' });
  const msg = el('div', 'min-height:1.4em;margin-top:8px;opacity:.9');
  msg.id = 'pairmsg';
  const row = el('div', 'display:flex;gap:8px;margin-top:10px;justify-content:flex-end');
  const go = el('button', 'padding:6px 12px;font-weight:600', 'Pair');
  const tryIt = async () => {
    const code = input.value.replace(/\D/g, '');
    if (code.length !== 6) { msg.textContent = 'The code has six digits.'; return; }
    msg.textContent = 'Pairing...';
    const r = await claim(code);
    if (!r.ok) { msg.textContent = r.why; input.select(); }
  };
  go.onclick = tryIt;
  input.addEventListener('input', () => { input.value = input.value.replace(/\D/g, '').slice(0, 6); if (input.value.length === 6) tryIt(); });
  input.addEventListener('keydown', (e) => { e.stopPropagation(); if (e.key === 'Enter') tryIt(); if (e.key === 'Escape' && later) later(); });
  if (later) { const b = el('button', 'padding:6px 12px', 'Not now'); b.onclick = later; row.append(b); }
  row.append(go);
  box.append(input, msg, row);
  document.body.append(box);
  setTimeout(() => input.focus(), 50);
  return box;
}

export async function pairFirst() {
  const P = window.VR_pair;
  if (!P || P.paired() || new URLSearchParams(location.search).has('capture')) return;
  const h = await fetch('health', { cache: 'no-store' }).then((r) => r.json()).catch(() => null);
  if (!h || !h.auth || h.auth.mode !== 'enforce') return;
  await new Promise((done) => {
    let busy = false;
    const box = makeBox(P, true, async (code) => {
      if (busy) return { ok: false, why: 'one moment' };
      busy = true;
      try { await P.claim(code); box.remove(); done(); return { ok: true }; }
      catch (e) { return { ok: false, why: why(e) }; }
      finally { busy = false; }
    }, null);
  });
}

export function initPairing(ed, panels, live) {
  const P = window.VR_pair;
  if (!P) return { state: () => 'no pairing' };
  const xr = () => ed.renderer.xr.isPresenting;
  let box = null, pad = false, laterUntil = 0, timer = null, busy = false, last = null;

  function showBox(enforce) {
    if (box) return;
    box = makeBox(P, enforce, async (code) => { const r = await claim(code); if (r.ok) hideBox(); return r; },
      () => { laterUntil = performance.now() + LATER_MS; hideBox(); });
  }
  function hideBox() { if (box) { box.remove(); box = null; } }

  async function claim(code) {
    if (busy) return { ok: false, why: 'one moment' };
    busy = true;
    try {
      const r = await P.claim(code);
      live.emit('paired', { device: r.device, kind: r.kind });
      return { ok: true, device: r.device };
    } catch (e) {
      const w = why(e);
      live.emit('pair_failed', { why: w });
      return { ok: false, why: w };
    } finally { busy = false; }
  }

  // the number pad in the headset: a panel that stays, rewritten as digits come in
  async function showPad(enforce) {
    if (pad) return;
    pad = true;
    hideBox();
    let digits = '', note = enforce ? 'The stage only answers paired devices.' : '';
    const text = () => `${note ? note + '\n' : ''}Type the code from your agent:\n${(digits + '______').slice(0, 6).split('').join(' ')}`;
    try {
      let res = await panels.show({ panel_id: 'pair_pad', title: 'Pair this headset', text: text(), buttons: PAD, stay: true,
        private: true, from: 'stage', quiet: true });
      while (res && res.answer != null) {
        const a = res.answer;
        if (/^\d$/.test(a) && digits.length < 6) digits += a;
        else if (a === 'Back') digits = digits.slice(0, -1);
        else if (a === 'Clear') digits = '';
        note = '';
        if (digits.length === 6) {
          panels.rewrite('pair_pad', { text: `Pairing...\n${digits.split('').join(' ')}` });   // (its promise is the next press)
          const r = await claim(digits);
          if (r.ok) { panels.close('pair_pad', 'paired'); break; }
          digits = ''; note = r.why;
        }
        res = await panels.rewrite('pair_pad', { text: text() });
      }
      if (res && res.answer == null && res.why && res.why !== 'paired' && res.why !== 'replaced') laterUntil = performance.now() + LATER_MS;
    } finally { pad = false; }
  }

  async function check() {
    if (P.paired()) { hideBox(); if (pad) panels.close('pair_pad', 'paired'); return; }
    if (performance.now() < laterUntil) return;
    const h = await fetch('health', { cache: 'no-store' }).then((r) => r.json()).catch(() => null);
    const a = h && h.auth;
    if (!a) return;
    last = a;
    const want = a.mode === 'enforce' || a.pending_codes > 0;
    if (!want) { hideBox(); return; }
    if (xr()) showPad(a.mode === 'enforce');
    else if (!pad) showBox(a.mode === 'enforce');
  }
  P.on((what) => {
    if (what === 'paired') { hideBox(); return; }
    laterUntil = 0;                                   // refused, or the key was revoked: ask at once
    check();
  });
  const loop = () => { check().finally(() => { timer = setTimeout(loop, POLL_MS); }); };
  if (!new URLSearchParams(location.search).has('capture')) loop();   // a capture page never asks (it pairs by link)
  return {
    state: () => ({ paired: P.paired(), asking: box ? 'field' : pad ? 'pad' : null, mode: last && last.mode }),
    check,
    stop: () => clearTimeout(timer),
  };
}
