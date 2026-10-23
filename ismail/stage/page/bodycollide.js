// Bodies that keep their arms out of themselves and out of each other (the user, 2026-10-22, judging the body study:
// "arms clip completely through his body", and he wants "a fast way to stop that inside the VR system" that stays
// cheap on the Quest). The research helper's measure (body_study/RESEARCH2.md): a dancer's own arms are rarely inside
// his own torso; what overlaps is the couple (his hands through her head and chest, her held arms ending in his chest);
// his own arm goes through him on a straight-arm reach across, when a hand target lands inside the torso (the take
// places hands from the eye, the body is thicker), and with an elbow pointing in.
//   torso     three elliptic slabs per body, fitted once from its skinned mesh at load (pelvis and belly, chest, upper
//             chest), each riding a spine bone, so they bend and turn with it; a ball for the head. Each slab has a
//             half-width, a front and a back at its bottom, middle and top (95th percentile of the skin there)
//   limbs     the upper arm's and forearm's radius from the mesh too (the median skin distance from the bone); the hand
//             a ball at the palm
//   arm       solveArm, before the two-bone solve: (1) a hand target inside a torso or head (its own or a body near)
//             is pushed out to the surface, its turn kept; (2) an upper arm or forearm through a torso swings the elbow
//             about the shoulder-hand line to the nearest clear angle (15 degree tries, then halved twice), the hand
//             exact; (3) none clears (a straight arm has no bend to swing): the clavicle comes forward up to ALPHA_MAX;
//             only then (4) the hand is pulled out of the way. The swing and the clavicle ease home (HOME_S) once clear;
//             the swing turns at most SWING_RATE, and while the cap holds it back the hand makes way as in (4)
//   count     limbDepths: how deep each limb is inside a body, for the page's clip counter (actors.js)
//   reach     softReach / reachIn: a hand target near or past full reach is eased in, so the elbow keeps a bend
//   relax     relaxedArm: where a resting body's arms hang when they give way to someone moving near
// Plain math on arrays (no three.js), no allocation per frame: a few hundred ball tests a frame for two bodies.
// Vectors are [x, y, z] (Float64Array(3)); bone matrices are three's column-major matrixWorld.elements.

export const CLIP_M = 0.02;          // a limb this far inside counts as a clip (the page's counter, the study's measure)
export const ACT_M = 0.012;          // the solver acts past this (under it the skin is touching, not through)
export const CLEAR_M = 0.004;        // a try is clear under this
export const NEAR_M = 1.5;           // other bodies count when their hips are this near (horizontal)
const SWING_STEP = Math.PI / 12, SWING_N = 7, SWING_MAX = 2.1;   // 15 degrees, 7 a side (to 105), never past 120
export const ALPHA_MAX = 22 * Math.PI / 180;                       // the clavicle's protraction toward the hand
const ALPHA_N = 4, PULL_MAX = 0.2, PUSH_PAD = 0.003;
export const HOME_S = 0.25;          // a swing or a protraction no longer needed eases back over this
// the swing turns at most this fast (rad/s): the nearest clear angle can lie a radian or more from last frame's, and
// taken at once the elbow popped up 10 cm in one frame (the 2026-10-22 drummer); a frame or three of touching is the cost
export const SWING_RATE = 10;
// where the slabs sit: t runs from the pelvis (0) to the shoulders' middle (1); the bone each rides
export const SECTIONS = [['pelvis', -0.22, 0.3], ['spine_02', 0.3, 0.62], ['spine_03', 0.62, 0.98]];
const UP_T = [0.5, 0.75, 1.0], FORE_T = [0.25, 0.5, 0.75, 1.0], SIGNS = [1, -1];

const v3 = () => new Float64Array(3);
const set = (o, x, y, z) => { o[0] = x; o[1] = y; o[2] = z; return o; };
const copy = (o, a) => { o[0] = a[0]; o[1] = a[1]; o[2] = a[2]; return o; };
const sub = (o, a, b) => { o[0] = a[0] - b[0]; o[1] = a[1] - b[1]; o[2] = a[2] - b[2]; return o; };
const axpy = (o, a, s, b) => { o[0] = a[0] + s * b[0]; o[1] = a[1] + s * b[1]; o[2] = a[2] + s * b[2]; return o; };
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const cross = (o, a, b) => { const x = a[1] * b[2] - a[2] * b[1], y = a[2] * b[0] - a[0] * b[2], z = a[0] * b[1] - a[1] * b[0]; o[0] = x; o[1] = y; o[2] = z; return o; };
const normalize = (o) => { const l = len(o); if (l > 1e-12) { o[0] /= l; o[1] /= l; o[2] /= l; } return o; };
const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
// a point and a direction through a column-major 4x4 (three's matrixWorld.elements); a direction comes out unit
function xfPoint(o, m, p) {
  const x = p[0], y = p[1], z = p[2];
  o[0] = m[0] * x + m[4] * y + m[8] * z + m[12]; o[1] = m[1] * x + m[5] * y + m[9] * z + m[13]; o[2] = m[2] * x + m[6] * y + m[10] * z + m[14];
  return o;
}
function xfDir(o, m, d) {
  const x = d[0], y = d[1], z = d[2];
  o[0] = m[0] * x + m[4] * y + m[8] * z; o[1] = m[1] * x + m[5] * y + m[9] * z; o[2] = m[2] * x + m[6] * y + m[10] * z;
  return normalize(o);
}
// the inverse of a bone's rest matrix applied (rotation and uniform scale, as these rigs have): world -> bone frame
function invPoint(m, p) {
  const s2 = m[0] * m[0] + m[1] * m[1] + m[2] * m[2], x = p[0] - m[12], y = p[1] - m[13], z = p[2] - m[14];
  return [(m[0] * x + m[1] * y + m[2] * z) / s2, (m[4] * x + m[5] * y + m[6] * z) / s2, (m[8] * x + m[9] * y + m[10] * z) / s2];
}
function invDir(m, d) {
  const o = [m[0] * d[0] + m[1] * d[1] + m[2] * d[2], m[4] * d[0] + m[5] * d[1] + m[6] * d[2], m[8] * d[0] + m[9] * d[1] + m[10] * d[2]];
  const l = Math.hypot(o[0], o[1], o[2]) || 1;
  return [o[0] / l, o[1] / l, o[2] / l];
}
const pct = (xs, q) => { if (!xs.length) return 0; const s = Float64Array.from(xs).sort(); return s[Math.min(s.length - 1, Math.floor(q * s.length))]; };

// ---- the fit, once at load. joints: name -> [x, y, z] at rest (world); mats: name -> the bone's rest matrixWorld
// elements; fwd: the body's forward at rest; torso, head: flat [x, y, z, ...] of the skin each bone group carries
// (dominant weight); limbs: {upperarm_l: flat, lowerarm_l: ..., _r}. null when the rig lacks a spine or arms.
export function fitBody({ joints, mats, fwd, torso, head, limbs }) {
  const need = ['pelvis', 'spine_02', 'spine_03', 'head', 'upperarm_l', 'upperarm_r', 'lowerarm_l', 'lowerarm_r', 'hand_l', 'hand_r'];
  if (need.some((n) => !joints[n] || !mats[n]) || torso.length < 60) return null;
  const P0 = joints.pelvis, S0 = joints.upperarm_l.map((x, i) => (x + joints.upperarm_r[i]) / 2);
  const U = sub(v3(), S0, P0), L = len(U);
  normalize(U);
  const Z = axpy(v3(), fwd, -dot(fwd, U), U); normalize(Z);
  const X = cross(v3(), U, Z);
  const n = torso.length / 3, T = new Float64Array(n), XS = new Float64Array(n), ZS = new Float64Array(n), d = v3(), q = v3();
  for (let i = 0; i < n; i++) {
    set(q, torso[3 * i], torso[3 * i + 1], torso[3 * i + 2]);
    sub(d, q, P0);
    const h = dot(d, U);
    T[i] = h / L;
    axpy(d, d, -h, U);
    XS[i] = dot(d, X); ZS[i] = dot(d, Z);
  }
  const ring = (t0, hw) => {
    for (let w = hw; ; w *= 1.5) {
      const ax = [], fz = [], bz = [];
      for (let i = 0; i < n; i++) if (Math.abs(T[i] - t0) <= w) { ax.push(Math.abs(XS[i])); if (ZS[i] > 0) fz.push(ZS[i]); else bz.push(-ZS[i]); }
      if (ax.length >= 12 || w > 0.5) {
        const a = pct(ax, 0.95);
        return [a, fz.length >= 4 ? pct(fz, 0.95) : 0.7 * a, bz.length >= 4 ? pct(bz, 0.95) : 0.6 * a];
      }
    }
  };
  const secs = SECTIONS.map(([bone, ta, tb]) => {
    const hw = Math.max(0.06, (tb - ta) / 2), rs = [ring(ta, hw), ring((ta + tb) / 2, hw), ring(tb, hw)];
    const O = axpy(v3(), P0, ta * L, U);
    return { bone, len: (tb - ta) * L, a: rs.map((r) => r[0]), f: rs.map((r) => r[1]), b: rs.map((r) => r[2]),
      Ol: invPoint(mats[bone], O), Ul: invDir(mats[bone], U), Xl: invDir(mats[bone], X), Zl: invDir(mats[bone], Z) };
  });
  // the head: the middle of its skin and the 85th percentile from there (hair and nose stick out past a ball)
  let hc = joints.head.slice(), hr = 0.1;
  if (head.length >= 30) {
    const m = head.length / 3; hc = [0, 0, 0];
    for (let i = 0; i < m; i++) for (let k = 0; k < 3; k++) hc[k] += head[3 * i + k] / m;
    const ds = []; for (let i = 0; i < m; i++) ds.push(Math.hypot(head[3 * i] - hc[0], head[3 * i + 1] - hc[1], head[3 * i + 2] - hc[2]));
    hr = pct(ds, 0.85);
  }
  // limb radii: the median skin distance from the bone, away from the joints
  const rad = {};
  for (const sd of ['l', 'r']) for (const [seg, a, b] of [['upper', 'upperarm', 'lowerarm'], ['fore', 'lowerarm', 'hand']]) {
    const A = joints[`${a}_${sd}`], B = joints[`${b}_${sd}`], vs = limbs[`${a}_${sd}`] || [], ab = sub(v3(), B, A), l = len(ab), ds = [];
    normalize(ab);
    for (let i = 0; i < vs.length / 3; i++) {
      set(q, vs[3 * i], vs[3 * i + 1], vs[3 * i + 2]); sub(d, q, A);
      const t = dot(d, ab) / l;
      if (t > 0.2 && t < 0.9) { axpy(d, d, -t * l, ab); ds.push(len(d)); }
    }
    rad[`${seg}_${sd}`] = ds.length > 10 ? pct(ds, 0.5) : seg === 'upper' ? 0.05 : 0.035;
  }
  const la = { l: Math.hypot(...sub(v3(), joints.lowerarm_l, joints.upperarm_l)), r: Math.hypot(...sub(v3(), joints.lowerarm_r, joints.upperarm_r)) };
  const lb = { l: Math.hypot(...sub(v3(), joints.hand_l, joints.lowerarm_l)), r: Math.hypot(...sub(v3(), joints.hand_r, joints.lowerarm_r)) };
  return { L, secs, headL: invPoint(mats.head, hc), headR: hr, rad, hand: 0.035, la, lb };
}

// ---- a body as it stands this frame (after its spine is posed): each slab's frame and the head ball in the world
export function newState(fit, name = '') {
  return { fit, name, on: false, secs: fit.secs.map(() => ({ O: v3(), U: v3(), X: v3(), Z: v3() })), head: v3(), pel: v3() };
}
export function bodyNow(st, matOf) {
  const { fit } = st;
  for (let i = 0; i < fit.secs.length; i++) {
    const f = fit.secs[i], s = st.secs[i], m = matOf(f.bone);
    xfPoint(s.O, m, f.Ol); xfDir(s.U, m, f.Ul); xfDir(s.X, m, f.Xl); xfDir(s.Z, m, f.Zl);
  }
  xfPoint(st.head, matOf('head'), fit.headL);
  const mp = matOf('pelvis');
  set(st.pel, mp[12], mp[13], mp[14]);
  st.on = true;
  return st;
}

// how deep a ball (q, r) is inside a body (metres, > 0 inside; 0 outside), the way out in n (unit) when given
const _d = v3();
export function ballDepth(st, q, r, n) {
  let best = 0;
  const { fit } = st;
  for (let i = 0; i < fit.secs.length; i++) {
    const f = fit.secs[i], s = st.secs[i];
    sub(_d, q, s.O);
    const h = dot(_d, s.U);
    if (h < -r || h > f.len + r) continue;
    const k = clamp(h / f.len, 0, 1) * 2, i0 = k >= 1 ? 1 : 0, w = k - i0;
    const x = dot(_d, s.X), z = dot(_d, s.Z);
    const A = f.a[i0] + (f.a[i0 + 1] - f.a[i0]) * w + r;
    const F = (z >= 0 ? f.f[i0] + (f.f[i0 + 1] - f.f[i0]) * w : f.b[i0] + (f.b[i0 + 1] - f.b[i0]) * w) + r;
    const rho = Math.hypot(x / A, z / F);
    if (rho >= 1) continue;
    const dist = Math.hypot(x, z), dep = rho > 1e-6 ? dist / rho - dist : Math.min(A, F);
    if (dep > best) {
      best = dep;
      if (n) { if (dist > 1e-6) { n[0] = (s.X[0] * x + s.Z[0] * z) / dist; n[1] = (s.X[1] * x + s.Z[1] * z) / dist; n[2] = (s.X[2] * x + s.Z[2] * z) / dist; } else copy(n, s.Z); }
    }
  }
  sub(_d, q, st.head);
  const dh = len(_d), deph = fit.headR + r - dh;
  if (deph > best) { best = deph; if (n) { if (dh > 1e-6) set(n, _d[0] / dh, _d[1] / dh, _d[2] / dh); else copy(n, st.secs[2].Z); } }
  return best;
}
// the deepest over a body and the bodies near it (self may be null); which: -1 the body itself, i the i-th other
const _n = v3();
let which = -1;
function allDepth(self, others, q, r, n) {
  let best = self ? ballDepth(self, q, r, n) : 0;
  which = -1;
  for (let i = 0; i < others.length; i++) {
    const d = ballDepth(others[i], q, r, n ? _n : null);
    if (d > best) { best = d; which = i; if (n) copy(n, _n); }
  }
  return best;
}

// ---- the arm. job (filled by the caller, reused every frame): S shoulder, C the clavicle's pivot, fwd the chest's
// forward (flat), lat out from the body on this side, T the wrist's target, palm the wrist-to-palm offset (world), la
// lb the bones, v0 toward the pole (from S), rU rF rH radii, self the body's state, others the bodies near, prev
// {phi, alpha} last frame's, dt. out: T (pushed or pulled), S (after the clavicle), v (the bend's direction from S, for
// the pole), alpha (signed, about the vertical), phi, cost0 (the plain solve), cost, pushed, pulled (m)
export function newArm() {
  return { job: { S: v3(), C: v3(), fwd: v3(), lat: v3(), T: v3(), palm: v3(), la: 0.3, lb: 0.3, v0: v3(), rU: 0.05, rF: 0.035, rH: 0.035, self: null, others: [], dt: 0 },
    out: { T: v3(), S: v3(), v: v3(), alpha: 0, phi: 0, cost0: 0, cost: 0, pushed: 0, pulled: 0 }, prev: { phi: 0, alpha: 0 } };
}
const _u = v3(), _v = v3(), _w = v3(), _E = v3(), _p = v3(), _q = v3(), _S = v3(), _bn = v3(), _bn2 = v3(), _CS = v3();
let geo = { d: 0, ca: 0, sa: 0 };
// the elbow's circle for S -> T: u along, v (from v0) and w across
function circle(job, S, T) {
  sub(_u, T, S);
  const want = len(_u), d = Math.min(want, job.la + job.lb - 1e-4);
  normalize(_u);
  axpy(_v, job.v0, -dot(job.v0, _u), _u);
  if (len(_v) < 1e-6) { set(_v, 0, -1, 0); axpy(_v, _v, -dot(_v, _u), _u); }
  normalize(_v);
  cross(_w, _u, _v);
  const ca = clamp((job.la * job.la + d * d - job.lb * job.lb) / (2 * job.la * Math.max(d, 1e-6)), -1, 1);
  geo.d = d; geo.ca = ca; geo.sa = Math.sqrt(1 - ca * ca);
}
function elbowAt(o, job, S, phi) {
  const c = Math.cos(phi), s = Math.sin(phi), k = job.la * geo.sa;
  for (let i = 0; i < 3; i++) o[i] = S[i] + _u[i] * job.la * geo.ca + (_v[i] * c + _w[i] * s) * k;
  return o;
}
// how deep the upper arm and forearm are (the hand is the push's): the worst sample; with n: its way out and where
// along the arm it is (fraction from the shoulder, in deepK)
let deepK = 0;
function armCost(job, S, E, T, n) {
  let worst = 0;
  const L = job.la + job.lb;
  for (const t of UP_T) {
    for (let i = 0; i < 3; i++) _p[i] = S[i] + t * (E[i] - S[i]);
    const d = allDepth(job.self, job.others, _p, job.rU, n ? _bn2 : null);
    if (d > worst) { worst = d; if (n) { copy(n, _bn2); deepK = (t * job.la) / L; } }
  }
  for (const t of FORE_T) {
    for (let i = 0; i < 3; i++) _p[i] = E[i] + t * (T[i] - E[i]);
    const d = allDepth(job.self, job.others, _p, job.rF, n ? _bn2 : null);
    if (d > worst) { worst = d; if (n) { copy(n, _bn2); deepK = (job.la + t * job.lb) / L; } }
  }
  return worst;
}
const costAt = (job, S, T, phi) => armCost(job, S, elbowAt(_E, job, S, phi), T, null);
// how natural an elbow is: out to this side and down rather than up or in
const naturalAt = (job, S, phi) => { elbowAt(_E, job, S, phi); sub(_q, _E, S); return dot(_q, job.lat) - 0.5 * _q[1]; };
// the swing about the shoulder-hand line from phi0: the nearest clear angle (halved twice toward phi0), else the least
// deep tried; {phi, cost}
const swingOut = { phi: 0, cost: 0 };
function swing(job, S, T, phi0) {
  circle(job, S, T);
  let c0 = costAt(job, S, T, phi0);
  swingOut.phi = phi0; swingOut.cost = c0;
  if (c0 < ACT_M || geo.sa < 0.08) return swingOut;        // clear enough, or a straight arm: nothing to swing
  let best = phi0, bestC = c0;
  for (let k = 1; k <= SWING_N; k++) {
    let pick = null, pickN = -Infinity;
    for (const sg of SIGNS) {
      const ph = phi0 + sg * k * SWING_STEP;
      if (Math.abs(ph) > SWING_MAX) continue;
      const c = costAt(job, S, T, ph);
      if (c < bestC) { bestC = c; best = ph; }
      if (c < CLEAR_M) {
        // halve toward the last try on that side, twice: the swing grows smoothly as the arm comes in
        let lo = phi0 + sg * (k - 1) * SWING_STEP, hi = ph;
        for (let j = 0; j < 2; j++) { const mid = (lo + hi) / 2; if (costAt(job, S, T, mid) < CLEAR_M) hi = mid; else lo = mid; }
        const nat = naturalAt(job, S, hi);
        if (nat > pickN) { pickN = nat; pick = hi; }
      }
    }
    if (pick !== null) { swingOut.phi = pick; swingOut.cost = costAt(job, S, T, pick); return swingOut; }
  }
  swingOut.phi = best; swingOut.cost = bestC;
  return swingOut;
}
// the shoulder with the clavicle turned alpha about the vertical through its pivot (signed: + turns it toward the
// chest's front), into o; returns the signed angle used
function shoulderAt(o, job, alpha) {
  sub(_CS, job.S, job.C);
  // which way about +Y moves the shoulder forward: (Y x CS) . fwd
  const sg = (_CS[2] * job.fwd[0] - _CS[0] * job.fwd[2]) >= 0 ? 1 : -1, a = sg * alpha, c = Math.cos(a), s = Math.sin(a);
  set(o, job.C[0] + c * _CS[0] + s * _CS[2], job.C[1] + _CS[1], job.C[2] - s * _CS[0] + c * _CS[2]);
  return a;
}
export function solveArm(arm) {
  const { job, out, prev } = arm;
  circle(job, job.S, job.T);
  out.cost0 = costAt(job, job.S, job.T, 0);
  const T = copy(out.T, job.T);
  // (1) the hand out of any body: the wrist and the palm, along the way out, a few times (a slab, then the next)
  let pushed = 0;
  for (let it = 0; it < 3; it++) {
    const dw = allDepth(job.self, job.others, T, job.rH, _bn);
    axpy(_p, T, 1, job.palm);
    const dp = allDepth(job.self, job.others, _p, job.rH, _q);
    const d = Math.max(dw, dp);
    if (d < ACT_M) break;
    axpy(T, T, d + PUSH_PAD, dp > dw ? _q : _bn);
    pushed += d;
  }
  // (2) the swing and the clavicle as last frame left them, easing home
  const home = Math.exp(-Math.max(0, job.dt) / HOME_S);
  let alpha = prev.alpha * home, phi0 = prev.phi * home;
  let aS = shoulderAt(_S, job, alpha);
  let r = swing(job, _S, T, phi0), phi = r.phi, cost = r.cost;
  // (3) not clear: the clavicle forward, a step at a time, swinging again at each
  if (cost >= ACT_M && alpha < ALPHA_MAX - 1e-6) {
    let bA = alpha, bPhi = phi, bC = cost;
    for (let k = 1; k <= ALPHA_N; k++) {
      const a = alpha + (ALPHA_MAX - alpha) * k / ALPHA_N;
      shoulderAt(_S, job, a);
      r = swing(job, _S, T, phi0);
      if (r.cost < bC) { bA = a; bPhi = r.phi; bC = r.cost; }
      if (r.cost < CLEAR_M) break;
    }
    alpha = bA; phi = bPhi; cost = bC;
    aS = shoulderAt(_S, job, alpha);
  }
  // (4) still through: the hand pulled out of the way (by the deepest point's way out, more for a point near the
  // shoulder, as the hand moves the whole line), swinging again
  let pulled = 0;
  for (let it = 0; it < 2 && cost >= ACT_M; it++) {
    circle(job, _S, T);
    elbowAt(_E, job, _S, phi);
    const d = armCost(job, _S, _E, T, _bn);
    if (d < ACT_M) { cost = d; break; }
    const m = Math.min(PULL_MAX, (d + PUSH_PAD) / Math.max(deepK, 0.35));
    axpy(T, T, m, _bn);
    pulled += m;
    r = swing(job, _S, T, phi);
    phi = r.phi; cost = r.cost;
  }
  circle(job, _S, T);
  if (job.dt > 0 && Math.abs(phi - prev.phi) > SWING_RATE * job.dt) {
    phi = prev.phi + Math.sign(phi - prev.phi) * SWING_RATE * job.dt;
    cost = costAt(job, _S, T, phi);
    // held back by the cap and still through a body: the hand and forearm make way this frame instead, as in (4) but
    // at the capped angle, and the swing catches up over the next frames (the 2026-10-22 couple: Jo's forearm 5.8 cm
    // into Mae for one frame, the clear angle 0.34 rad past the cap)
    for (let it = 0; it < 2 && cost >= ACT_M; it++) {
      elbowAt(_E, job, _S, phi);
      const d = armCost(job, _S, _E, T, _bn);
      if (d < ACT_M) { cost = d; break; }
      const m = Math.min(PULL_MAX, (d + PUSH_PAD) / Math.max(deepK, 0.35));
      axpy(T, T, m, _bn);
      pulled += m;
      circle(job, _S, T);
      cost = costAt(job, _S, T, phi);
    }
  }
  const c = Math.cos(phi), s = Math.sin(phi);
  for (let i = 0; i < 3; i++) out.v[i] = _v[i] * c + _w[i] * s;
  copy(out.S, _S);
  out.alpha = aS; out.phi = phi; out.cost = cost; out.pushed = pushed; out.pulled = pulled;
  prev.alpha = Math.abs(alpha) < 0.005 ? 0 : alpha; prev.phi = Math.abs(phi) < 0.01 ? 0 : phi;   // home: exactly 0, so a resting arm is let be
  return out;
}

// ---- reach (the user, 2026-10-22, at the drummer's clip: "still pretty bad"): a hand target at or past the arm's full
// length straightens the arm, and a straight arm has no bend to steer, so it reads stiff and robotic. A seated drummer
// played from a standing take had his right hand target past reach in about half the frames. softReach eases the
// target's distance from the shoulder in past REACH_KNEE of the arm toward REACH_MAX, never past it, so the elbow
// always keeps a bend (about 45 degrees at REACH_MAX); under the knee nothing changes. e: the distance over the arm's
// length; returns the eased one
export const REACH_KNEE = 0.85, REACH_MAX = 0.95;
export function softReach(e, knee = REACH_KNEE, max = REACH_MAX) {
  if (!(e > knee)) return e;
  const w = max - knee;
  return knee + w * (1 - Math.exp(-(e - knee) / w));
}
// a job's target T brought in along the shoulder line (S) by softReach; out: [the raw reach, the eased one], both over
// the arm's length (la + lb)
export function reachIn(job, out = [0, 0]) {
  sub(_d, job.T, job.S);
  const L = job.la + job.lb, dist = len(_d), e = dist / L, k = softReach(e);
  if (k < e && dist > 1e-9) axpy(job.T, job.S, k / e, _d);
  out[0] = e; out[1] = k;
  return out;
}
// how deep a ball (q, r) is inside any of the bodies given (0 outside): a resting body asks whether its held arms come
// near someone moving (r taken large, as a margin)
export function depthIn(bodies, q, r) {
  let best = 0;
  for (let i = 0; i < bodies.length; i++) { const d = ballDepth(bodies[i], q, r, null); if (d > best) best = d; }
  return best;
}
// a relaxed arm for a body that has to give way (the user, 2026-10-22, on the partner holding her arms out stiffly
// toward the dancer: "You can just soften it of course"): the elbow hangs low under the shoulder, a little forward and
// out, and the forearm comes forward and in, a little down, so the hand rests in front of the hips. st: the body this
// frame (its upper-chest slab gives up, forward and left), S the shoulder, sgn +1 left, -1 right; E and H filled
export const RELAX_ELBOW = [0.15, 0.12], RELAX_FORE = [0.7, -0.3, -0.25];   // elbow forward, out; forearm forward, up, out
export function relaxedArm(st, S, la, lb, sgn, E, H) {
  const s = st.secs[st.secs.length - 1], U = s.U, Z = s.Z, X = s.X;
  const [ef, eo] = RELAX_ELBOW, [ff, fu, fo] = RELAX_FORE;
  for (let i = 0; i < 3; i++) _q[i] = -U[i] + ef * Z[i] + eo * sgn * X[i];
  normalize(_q);
  axpy(E, S, la, _q);
  for (let i = 0; i < 3; i++) _q[i] = ff * Z[i] + fu * U[i] + fo * sgn * X[i];
  normalize(_q);
  axpy(H, E, lb, _q);
  return H;
}

// ---- the counter: how deep each limb of an arm is, in itself and in the bodies near. S E H: shoulder, elbow, wrist;
// palm: the palm's centre. out: {self: [upper, fore, hand], other: [...], who: [...] (index into others or -1)}
export function newDepths() { return { self: new Float64Array(3), other: new Float64Array(3), who: new Int8Array(3) }; }
function oneLimb(self, others, out, k, q, r) {
  const a = self ? ballDepth(self, q, r, null) : 0;
  if (a > out.self[k]) out.self[k] = a;
  for (let i = 0; i < others.length; i++) { const b = ballDepth(others[i], q, r, null); if (b > out.other[k]) { out.other[k] = b; out.who[k] = i; } }
}
export function limbDepths(self, others, S, E, H, palm, rU, rF, rH, out) {
  out.self.fill(0); out.other.fill(0); out.who.fill(-1);
  // the upper arm from its middle down (its top half is the armpit), as the study measured
  for (const t of UP_T) { for (let i = 0; i < 3; i++) _p[i] = S[i] + t * (E[i] - S[i]); oneLimb(self, others, out, 0, _p, rU); }
  for (const t of FORE_T) { for (let i = 0; i < 3; i++) _p[i] = E[i] + t * (H[i] - E[i]); oneLimb(self, others, out, 1, _p, rF); }
  oneLimb(self, others, out, 2, H, rH); oneLimb(self, others, out, 2, palm, rH);
  return out;
}

// ---- two bodies too close to stand apart (a placement for the scene, not something to fix by moving feet): the
// hips' distance (flat) and the least the two belly slabs allow along the line between them; null when they fit
export function overlapOf(a, b) {
  sub(_d, b.pel, a.pel); _d[1] = 0;
  const dist = len(_d);
  if (dist < 1e-6) return { dist: 0, need: 0.2 };
  normalize(_d);
  const reach = (st, sign) => {
    const f = st.fit.secs[0], s = st.secs[0], x = sign * dot(_d, s.X), z = sign * dot(_d, s.Z);
    const r = Math.hypot(x / f.a[1], z / (z >= 0 ? f.f[1] : f.b[1]));
    return r > 1e-6 ? 1 / r : f.a[1];
  };
  const need = reach(a, 1) + reach(b, -1);
  return dist < need ? { dist, need } : null;
}
