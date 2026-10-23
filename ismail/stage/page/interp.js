// How a keyed object moves between its keys (clock.js), exactly, so the Blender render can do the same (the film
// assistant, 2026-10-05: the stage and the render were 74 cm apart between keys). Per object, anim.json "interp":
//   "stop"    (default) each segment eases in and out (smoothstep): the object stops at every key
//   "smooth"  it glides through the keys: location and scale follow a cubic Hermite curve per segment whose tangent at
//             key i is (p[i+1] - p[i-1]) / (t[i+1] - t[i-1]) (one-sided at the first and last key: the segment's slope),
//             evaluated at u = (t - t[i]) / (t[i+1] - t[i]); the turn slerps at u (no easing)
// The turn always takes the short way: a key's quaternion is negated when its dot with the previous one is negative.
// Pure numbers, no three.js: k(ks, t, mode) answers which segment and how far along it to slerp; vec() the values.

export const ease = (k) => k * k * (3 - 2 * k);

// the segment index i (keys i, i + 1) and u in 0..1 for time t; null outside the keys
export function segment(ks, t) {
  if (ks.length < 2 || t <= ks[0].t || t >= ks[ks.length - 1].t) return null;
  let i = 0;
  while (ks[i + 1].t < t) i++;
  return { i, u: (t - ks[i].t) / Math.max(1e-6, ks[i + 1].t - ks[i].t) };
}

// a location or scale (key field `f`) at time t
export function vec(ks, t, f, mode = 'stop') {
  if (t <= ks[0].t) return ks[0][f].slice();
  if (t >= ks[ks.length - 1].t) return ks[ks.length - 1][f].slice();
  const { i, u } = segment(ks, t), a = ks[i], b = ks[i + 1];
  if (mode !== 'smooth') {
    const k = ease(u);
    return a[f].map((x, j) => x + (b[f][j] - x) * k);
  }
  const h = b.t - a.t;
  const tan = (n) => {
    const p = ks[Math.max(0, n - 1)], q = ks[Math.min(ks.length - 1, n + 1)];
    return p[f].map((x, j) => (q[f][j] - x) / Math.max(1e-6, q.t - p.t));
  };
  const ma = tan(i), mb = tan(i + 1);
  const u2 = u * u, u3 = u2 * u;
  const h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2;
  return a[f].map((x, j) => h00 * x + h10 * h * ma[j] + h01 * b[f][j] + h11 * h * mb[j]);
}

// how far to slerp between keys i and i + 1 at u
export const slerpK = (u, mode = 'stop') => (mode === 'smooth' ? u : ease(u));

// a Blender rotation [w, x, y, z] whose -Z looks from pos at look, world Z up (a camera's or a light's aim; a key's
// look in stage_keys_set); straight up or down, its right is +X
export function lookQuat(pos, look) {
  const norm = (v) => { const n = Math.hypot(...v) || 1; return v.map((x) => x / n); };
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const f = norm(look.map((x, i) => x - pos[i]));
  let r = cross(f, [0, 0, 1]);
  r = Math.hypot(...r) < 1e-6 ? [1, 0, 0] : norm(r);
  const u = cross(r, f);
  const m = [[r[0], u[0], -f[0]], [r[1], u[1], -f[1]], [r[2], u[2], -f[2]]];   // columns: right, up, back
  const tr = m[0][0] + m[1][1] + m[2][2];
  let q;
  if (tr > 0) { const s = Math.sqrt(tr + 1) * 2; q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]; }
  else if (m[0][0] > m[1][1] && m[0][0] > m[2][2]) { const s = Math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2; q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s]; }
  else if (m[1][1] > m[2][2]) { const s = Math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2; q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s]; }
  else { const s = Math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2; q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s]; }
  return q[0] < 0 ? q.map((x) => -x) : q;
}

// ---- take playback between recorded samples (actors.js). The user, 2026-10-06 (voice note #20002): playback "looks
// low frame rate and steppy"; a take recorded while the headset ran at 20 to 30 fps has about 20 samples a second,
// and each one was held until the next. mixFrame(a, b, u, out) is the frame u (0..1) of the way from sample a to
// sample b: numbers lerp; a pose array [x, y, z, qx, qy, qz, qw, (radius)] lerps its position and radius and nlerps
// its quaternion the short way (unit quaternions only: other arrays of 7 or 8 numbers just lerp); objects and arrays
// mix field by field; anything else (a gesture name, a hand that is missing in one sample) is the nearer sample's.
// out: the previous result, written in place when its shape matches and mixFrame made it (never a recorded sample
// passed through), so playback makes no garbage per frame on the Quest.
const OWN = new WeakSet();                       // buffers mixFrame made: the only ones it writes into
const own = (o) => { OWN.add(o); return o; };
const isQ = (a) => { const n = a[3] * a[3] + a[4] * a[4] + a[5] * a[5] + a[6] * a[6]; return n > 0.81 && n < 1.21; };

export function mixFrame(a, b, u, out) {
  if (typeof a === 'number') return typeof b === 'number' ? a + (b - a) * u : (u < 0.5 ? a : b);
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || b.length !== a.length) return u < 0.5 ? a : b;
    const o = Array.isArray(out) && out.length === a.length && OWN.has(out) ? out : own(new Array(a.length));
    if (a.length && typeof a[0] === 'number') {
      for (let k = 0; k < a.length; k++) o[k] = a[k] + (b[k] - a[k]) * u;
      if ((a.length === 7 || a.length === 8) && isQ(a) && isQ(b)) {
        const s = a[3] * b[3] + a[4] * b[4] + a[5] * b[5] + a[6] * b[6] < 0 ? -1 : 1;
        let n = 0;
        for (let k = 3; k < 7; k++) { o[k] = a[k] + (s * b[k] - a[k]) * u; n += o[k] * o[k]; }
        n = Math.sqrt(n) || 1;
        for (let k = 3; k < 7; k++) o[k] /= n;
      }
      return o;
    }
    for (let k = 0; k < a.length; k++) o[k] = mixFrame(a[k], b[k], u, o[k]);
    return o;
  }
  if (a && typeof a === 'object') {
    if (!b || typeof b !== 'object' || Array.isArray(b)) return u < 0.5 ? a : b;
    const o = out && typeof out === 'object' && !Array.isArray(out) && OWN.has(out) ? out : own({});
    for (const k in a) o[k] = k in b ? mixFrame(a[k], b[k], u, o[k]) : a[k];
    return o;
  }
  return u < 0.5 ? a : b;
}

// a take frame for playback: only what posing reads (t, head, the hands) is mixed; the rest (the 83-joint body) is
// sample a's. A whole frame took 0.1 ms on the desktop, the body most of it.
const POSED = ['t', 'head', 'left', 'right'];
export function mixTake(a, b, u, out) {
  const o = out && OWN.has(out) ? out : own({});
  for (const k in a) if (!POSED.includes(k)) o[k] = a[k];
  for (const k of POSED) if (k in a) o[k] = k in b ? mixFrame(a[k], b[k], u, o[k]) : a[k];
  return o;
}

// ---- a recorded move made into keys (camrec.js; Nate, 2026-10-14: hold a camera while the song plays and the move
// becomes its keys). Samples [{t, location, quaternion}] (Blender, quaternion [w, x, y, z]) are first eased over
// smooth_s (a hand's tremor is not a camera move), then thinned: keys start at the first and last sample and the
// sample the keys miss by most is added, until every sample is within pos metres and ang degrees of what the keys
// play back with `mode` (the same vec and slerp the clock uses), or max_keys is reached. Returns {keys, err_m, err_deg}.
export function quatAt(ks, t, mode = 'stop') {
  if (t <= ks[0].t) return ks[0].quaternion.slice();
  if (t >= ks[ks.length - 1].t) return ks[ks.length - 1].quaternion.slice();
  const { i, u } = segment(ks, t);
  return slerpQ(ks[i].quaternion, ks[i + 1].quaternion, slerpK(u, mode));
}
export function slerpQ(a, b, k) {
  let d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const s = d < 0 ? -1 : 1;
  d = Math.min(1, Math.abs(d));
  if (d > 0.9995) { const q = a.map((x, j) => x + (s * b[j] - x) * k), n = Math.hypot(...q); return q.map((x) => x / n); }
  const th = Math.acos(d), sn = Math.sin(th), wa = Math.sin((1 - k) * th) / sn, wb = Math.sin(k * th) / sn;
  return a.map((x, j) => wa * x + wb * s * b[j]);
}
export const quatAngleDeg = (a, b) => 2 * Math.acos(Math.min(1, Math.abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]))) * 180 / Math.PI;

// a recorded move without the shake of a hand or a head and the frame-to-frame jitter of the samples' timing. The
// place: a local quadratic fit over smooth_s either side of each sample (tricube weights), which follows a curve
// without cutting inside it and needs no one-sided window at the ends (a moving average pulled the first and last
// samples back along the move, and its leftover noise near the 2 cm tolerance made the thinning chase the shake:
// 56 keys for a 4 s arc, 9 without it). The turn: the average of the quaternions inside the same window.
export function easeSamples(samples, smooth_s = 0.15) {
  const n = samples.length;
  if (!(smooth_s > 0) || n < 5) return samples.map((s) => ({ t: s.t, location: s.location.slice(), quaternion: s.quaternion.slice() }));
  const h = smooth_s, T0 = samples[0].t, T1 = samples[n - 1].t;
  let lo = 0, hi = 0;
  return samples.map((s) => {
    const a = Math.max(T0, Math.min(s.t - h, T1 - 2 * h)), b = Math.min(T1, Math.max(s.t + h, T0 + 2 * h));
    while (lo < n - 1 && samples[lo].t < a - 1e-9) lo++;
    while (lo > 0 && samples[lo - 1].t >= a - 1e-9) lo--;
    while (hi < n - 1 && samples[hi + 1].t <= b + 1e-9) hi++;
    while (hi > lo && samples[hi].t > b + 1e-9) hi--;
    const R = Math.max(s.t - a, b - s.t, 1e-6);
    // weighted sums for the normal equations of x(τ) = c0 + c1 τ + c2 τ², τ = t_i - t
    const S = [0, 0, 0, 0, 0], Y = [[0, 0, 0], [0, 0, 0], [0, 0, 0]], q = [0, 0, 0, 0], q0 = s.quaternion;
    for (let i = lo; i <= hi; i++) {
      const p = samples[i], tau = p.t - s.t, u = Math.min(1, Math.abs(tau) / (R * 1.0001)), w = (1 - u ** 3) ** 3;
      let tp = w;
      for (let k = 0; k < 5; k++) { S[k] += tp; if (k < 3) for (let d = 0; d < 3; d++) Y[k][d] += tp * p.location[d]; tp *= tau; }
      const sg = p.quaternion[0] * q0[0] + p.quaternion[1] * q0[1] + p.quaternion[2] * q0[2] + p.quaternion[3] * q0[3] < 0 ? -1 : 1;
      for (let k = 0; k < 4; k++) q[k] += w * sg * p.quaternion[k];
    }
    const M = [[S[0], S[1], S[2]], [S[1], S[2], S[3]], [S[2], S[3], S[4]]];
    const det3 = (m) => m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
      + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    const D = det3(M), qn = Math.hypot(...q) || 1;
    const location = [0, 1, 2].map((d) => {
      if (hi - lo < 4 || !(Math.abs(D) > 1e-18)) return s.location[d];
      return det3([[Y[0][d], M[0][1], M[0][2]], [Y[1][d], M[1][1], M[1][2]], [Y[2][d], M[2][1], M[2][2]]]) / D;   // Cramer: c0
    });
    return { t: s.t, location, quaternion: q.map((x) => x / qn) };
  });
}

export function thinKeys(samples, { pos = 0.02, ang = 2, mode = 'smooth', smooth_s = 0.15, max_keys = 300 } = {}) {
  const sm = easeSamples(samples, smooth_s);
  if (sm.length <= 2) return { keys: sm, err_m: 0, err_deg: 0 };
  const idx = [0, sm.length - 1];
  let errM = 0, errD = 0;
  for (;;) {
    const ks = idx.map((i) => sm[i]);
    let worst = -1, wErr = 0;
    errM = 0; errD = 0;
    for (let j = 0; j < sm.length; j++) {
      const s = sm[j], p = vec(ks, s.t, 'location', mode);
      const dm = Math.hypot(p[0] - s.location[0], p[1] - s.location[1], p[2] - s.location[2]);
      const dd = quatAngleDeg(quatAt(ks, s.t, mode), s.quaternion);
      errM = Math.max(errM, dm); errD = Math.max(errD, dd);
      const e = Math.max(dm / pos, dd / ang);
      if (e > wErr) { wErr = e; worst = j; }
    }
    if (wErr <= 1 || idx.length >= max_keys || idx.includes(worst)) break;
    idx.push(worst); idx.sort((a, b) => a - b);
  }
  return { keys: idx.map((i) => sm[i]), err_m: errM, err_deg: errD };
}
