// Vertical rays against a room's meshes, fast enough for the headset (the ground check in actors.js). three's raycast
// tests every triangle of a mesh whose box the ray passes, and bluefront's merged build is one mesh of 42 000 triangles
// under the whole floor: 5 ms a ray on the PC, 50 ms a body a frame for the clip check (VR Dev, 2026-10-27, the first
// run on bluefront; the lecture hall's boxes cost 0.5). Here a mesh's triangles are put once in the world into a flat
// grid of columns (newGrid / stepGrid, in steps, so a big mesh does not stall a frame), and a vertical ray reads only
// the triangles of its column (gridHits): the faces up (tops) and down (undersides) between two heights, for
// bodycollide.js sinkOf. A mesh that moves is gridded again (the caller compares matrices: sameMatrix).
// Plain math on arrays (no three.js): pos the geometry's positions (local), idx its index or null, m the mesh's
// matrixWorld.elements (column-major).

// a cell holds about PER_CELL triangles on average, and is no smaller than CELL_MIN (metres): a guitar of 2 400
// triangles in one half-metre cell cost 0.05 ms a ray (bluefront's band); a terrain's cells grow to stay under CELLS_MAX
export const CELL_MIN = 0.1, PER_CELL = 8, CELLS_MAX = 16384;
export const FACING = 0.3;                         // |normal y| past this is a top or an underside; walls are neither

export function newGrid(pos, idx, m) {
  const n = idx ? (idx.length / 3) | 0 : (pos.length / 9) | 0;
  // a mirrored mesh (a negative scale) winds its faces the other way round in the world: its outward normal is the
  // winding's turned over
  const det = m[0] * (m[5] * m[10] - m[9] * m[6]) - m[4] * (m[1] * m[10] - m[9] * m[2]) + m[8] * (m[1] * m[6] - m[5] * m[2]);
  return { pos, idx, n, m: Float64Array.from(m), flip: det < 0 ? -1 : 1, W: new Float32Array(n * 9), i: 0, phase: 0, x0: Infinity, z0: Infinity,
    x1: -Infinity, z1: -Infinity, y0: Infinity, y1: -Infinity, cell: CELL_MIN, nx: 0, nz: 0, cells: null, ylo: null, yhi: null, done: n === 0 };
}
export function sameMatrix(g, m) {
  const a = g.m;
  for (let k = 0; k < 16; k++) if (a[k] !== m[k]) return false;
  return true;
}
// up to budget triangles more of the build (Infinity: all); how many it took (g.done once the grid is ready)
export function stepGrid(g, budget = Infinity) {
  if (g.done) return 0;
  const { pos, idx, n, m, W } = g;
  let left = budget, used = 0;
  if (g.phase === 0) {                       // the corners into the world, and the grid's extent
    for (; g.i < n && left > 0; g.i++, left--, used++) {
      for (let c = 0; c < 3; c++) {
        const v = idx ? idx[3 * g.i + c] : 3 * g.i + c, x = pos[3 * v], y = pos[3 * v + 1], z = pos[3 * v + 2], o = 9 * g.i + 3 * c;
        const wx = m[0] * x + m[4] * y + m[8] * z + m[12], wy = m[1] * x + m[5] * y + m[9] * z + m[13], wz = m[2] * x + m[6] * y + m[10] * z + m[14];
        W[o] = wx; W[o + 1] = wy; W[o + 2] = wz;
        if (wx < g.x0) g.x0 = wx; if (wx > g.x1) g.x1 = wx; if (wz < g.z0) g.z0 = wz; if (wz > g.z1) g.z1 = wz;
        if (wy < g.y0) g.y0 = wy; if (wy > g.y1) g.y1 = wy;
      }
    }
    if (g.i < n) return used;
    const w = Math.max(1e-3, g.x1 - g.x0), d = Math.max(1e-3, g.z1 - g.z0);
    g.cell = Math.max(CELL_MIN, Math.sqrt((w * d * PER_CELL) / n), Math.sqrt((w * d) / CELLS_MAX));
    g.nx = Math.max(1, Math.ceil(w / g.cell)); g.nz = Math.max(1, Math.ceil(d / g.cell));
    g.cells = new Array(g.nx * g.nz).fill(null);
    g.ylo = new Float32Array(g.nx * g.nz).fill(Infinity); g.yhi = new Float32Array(g.nx * g.nz).fill(-Infinity);
    g.phase = 1; g.i = 0;
  }
  const { cells, cell, nx, nz, x0, z0, ylo, yhi } = g;
  for (; g.i < n && left > 0; g.i++, left--, used++) {     // each triangle into the columns its flat box covers
    const o = 9 * g.i;
    const ax = W[o], az = W[o + 2], bx = W[o + 3], bz = W[o + 5], cx = W[o + 6], cz = W[o + 8];
    const lo = Math.min(W[o + 1], W[o + 4], W[o + 7]), hi = Math.max(W[o + 1], W[o + 4], W[o + 7]);
    // a wall (no area seen from above) is never met by a vertical ray
    if (Math.abs((bx - ax) * (cz - az) - (cx - ax) * (bz - az)) < 1e-10) continue;
    const i0 = Math.max(0, Math.floor((Math.min(ax, bx, cx) - x0) / cell)), i1 = Math.min(nx - 1, Math.floor((Math.max(ax, bx, cx) - x0) / cell));
    const k0 = Math.max(0, Math.floor((Math.min(az, bz, cz) - z0) / cell)), k1 = Math.min(nz - 1, Math.floor((Math.max(az, bz, cz) - z0) / cell));
    for (let i = i0; i <= i1; i++) for (let k = k0; k <= k1; k++) {
      const j = i * nz + k;
      (cells[j] || (cells[j] = [])).push(g.i);
      if (lo < ylo[j]) ylo[j] = lo;
      if (hi > yhi[j]) yhi[j] = hi;
    }
  }
  if (g.i < n) return used;
  g.done = true; g.pos = g.idx = null;
  return used;
}
// a vertical line at (x, z): the tops (faces up) it crosses between y0 and y1 and the undersides (faces down) between
// u0 and y1, appended to out.tops / out.unders (Float64Array) up to their length; out.nt, out.nu count them
export function gridHits(g, x, z, y0, y1, u0, out) {
  const lo = Math.min(y0, u0);
  if (!g.done || !g.cells || g.y0 > y1 || g.y1 < lo) return;
  const i = Math.floor((x - g.x0) / g.cell), k = Math.floor((z - g.z0) / g.cell);
  if (i < 0 || k < 0 || i >= g.nx || k >= g.nz) return;
  const j = i * g.nz + k, list = g.cells[j];
  if (!list || g.ylo[j] > y1 || g.yhi[j] < lo) return;          // the column's triangles all above or all below
  const W = g.W;
  for (let q = 0; q < list.length; q++) {
    const o = 9 * list[q];
    const ax = W[o], ay = W[o + 1], az = W[o + 2], bx = W[o + 3], by = W[o + 4], bz = W[o + 5], cx = W[o + 6], cy = W[o + 7], cz = W[o + 8];
    const ex = bx - ax, ez = bz - az, fx = cx - ax, fz = cz - az, d = ex * fz - fx * ez;
    if (d === 0) continue;
    const u = ((x - ax) * fz - fx * (z - az)) / d, v = (ex * (z - az) - (x - ax) * ez) / d;
    if (u < 0 || v < 0 || u + v > 1) continue;
    const y = ay + u * (by - ay) + v * (cy - ay);
    if (y > y1 || y < lo) continue;
    // the face's normal (the winding's, as three's face.normal turned into the world): its y is -d over its length
    const ey = by - ay, fy = cy - ay, nx = ey * fz - ez * fy, nz = ex * fy - ey * fx, ny = -g.flip * d / Math.hypot(nx, d, nz);
    if (ny > FACING) { if (y >= y0 && out.nt < out.tops.length) out.tops[out.nt++] = y; }
    else if (ny < -FACING && y >= u0 && out.nu < out.unders.length) out.unders[out.nu++] = y;
  }
}
// tops highest first, undersides lowest first (as sinkOf reads them), a height met twice (a ray down an edge two
// triangles share) kept once
export function sortHits(out) {
  out.nt = sortDedupe(out.tops, out.nt, -1);
  out.nu = sortDedupe(out.unders, out.nu, 1);
}
function sortDedupe(a, n, sg) {
  for (let i = 1; i < n; i++) { const v = a[i]; let j = i - 1; while (j >= 0 && sg * (a[j] - v) > 0) { a[j + 1] = a[j]; j--; } a[j + 1] = v; }
  let m = 0;
  for (let i = 0; i < n; i++) if (m === 0 || Math.abs(a[i] - a[m - 1]) > 5e-4) a[m++] = a[i];
  return m;
}
