"""Bodies that keep their arms out of themselves and each other (page/bodycollide.js, run in node on a made-up body),
the op that turns it on and off (stage_body_collide), and the grab's re-take wait (stage_grab_mode regrab_s)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from ismail.stage import world as W
from test_stage import FakePage, stage  # noqa: F401  (the fixture)

MOD = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'bodycollide.js').as_uri()

# a made-up body: an elliptic torso 0.34 m wide, 0.13 m to the front and 0.10 to the back over 0.49 m from the pelvis
# to the shoulders, a head ball, arms of 0.28 and 0.26 m; every bone frame is a turn about the vertical and a place
BODY = """
const M = await import('%s');
const J = { pelvis: [0, 1, 0], spine_02: [0, 1.17, 0], spine_03: [0, 1.23, 0], head: [0, 1.7, 0],
  upperarm_l: [0.19, 1.49, 0], lowerarm_l: [0.21, 1.21, 0], hand_l: [0.22, 0.95, 0],
  upperarm_r: [-0.19, 1.49, 0], lowerarm_r: [-0.21, 1.21, 0], hand_r: [-0.22, 0.95, 0] };
const mat = (p, yaw = 0) => { const c = Math.cos(yaw), s = Math.sin(yaw); return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, p[0], p[1], p[2], 1]; };
const mats = Object.fromEntries(Object.entries(J).map(([n, p]) => [n, mat(p)]));
const torso = [], head = [];
for (let t = -0.2; t <= 1.0; t += 0.02) for (let a = 0; a < 2 * Math.PI; a += 0.1) torso.push(0.17 * Math.cos(a), 1 + t * 0.49, Math.sin(a) * (Math.sin(a) > 0 ? 0.13 : 0.10));
for (let a = 0; a < Math.PI; a += 0.2) for (let b = 0; b < 2 * Math.PI; b += 0.2) head.push(0.1 * Math.sin(a) * Math.cos(b), 1.78 + 0.1 * Math.cos(a), 0.02 + 0.1 * Math.sin(a) * Math.sin(b));
const fit = M.fitBody({ joints: J, mats, fwd: [0, 0, 1], torso, head, limbs: {} });
const at = (dz = 0, yaw = 0) => { const st = M.newState(fit); M.bodyNow(st, (n) => mat([J[n][0], J[n][1], J[n][2] + dz], yaw)); return st; };
const v = (x, y, z) => Float64Array.from([x, y, z]);
const solve = (T, pole, self, others = [], frames = 1) => {    // frames: the same target held that many frames (1/30 s)
  const arm = M.newArm(), j = arm.job, S = [0.19, 1.49, 0], phis = [];
  let o;
  for (let k = 0; k < frames; k++) {
    j.S.set(S); j.C.set([0.03, 1.47, 0.02]); j.fwd.set([0, 0, 1]); j.lat.set([1, 0, 0]); j.T.set(T); j.palm.set([0, -0.05, 0.03]);
    j.la = 0.28; j.lb = 0.26; j.v0.set([pole[0] - S[0], pole[1] - S[1], pole[2] - S[2]]); j.self = self; j.others = others; j.dt = 1 / 30;
    o = M.solveArm(arm); phis.push(o.phi);
  }
  const body = self || others[0];
  return { T: Array.from(o.T), alpha: o.alpha, phi: o.phi, phis, cost0: o.cost0, cost: o.cost, pushed: o.pushed, pulled: o.pulled,
    hand: M.ballDepth(body, o.T, 0.035, null) };
};
"""


def _node(expr):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    js = BODY % MOD + f'console.log(JSON.stringify({expr}));'
    r = subprocess.run(['node', '--input-type=module', '-e', js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_slabs_are_fitted_from_the_skin_and_follow_their_bones():
    got = _node("""{ a: fit.secs.map((s) => s.a[1]), f: fit.secs.map((s) => s.f[1]), b: fit.secs.map((s) => s.b[1]), headR: fit.headR,
      inside: M.ballDepth(at(), v(0, 1.3, 0), 0.03, null), front: M.ballDepth(at(), v(0, 1.3, 0.5), 0.03, null),
      side: M.ballDepth(at(), v(0.14, 1.3, 0), 0, null), sideTurned: M.ballDepth(at(0, Math.PI / 2), v(0.14, 1.3, 0), 0, null),
      headIn: M.ballDepth(at(), v(0, 1.78, 0.05), 0.03, null) }""")
    assert all(0.16 < a < 0.18 for a in got['a']) and all(0.12 < f < 0.14 for f in got['f']) and all(0.09 < b < 0.11 for b in got['b'])
    assert 0.09 < got['headR'] < 0.11
    assert got['inside'] > 0.15 and got['front'] == 0 and got['headIn'] > 0.05
    # 14 cm to the side is inside a 17 cm half-width; turned a quarter, that way is the 10 cm back: outside
    assert got['side'] > 0.02 and got['sideTurned'] == 0


def test_a_hand_target_inside_the_torso_is_pushed_to_its_surface():
    got = _node("solve([0.05, 1.3, 0.05], [0.6, 1.2, -0.3], at())")
    assert got['cost0'] > 0.05 and got['pushed'] > 0.05
    assert got['hand'] < 0.012 and got['cost'] < 0.012 and got['pulled'] == 0


def test_an_elbow_through_the_chest_swings_clear_and_the_hand_stays_where_it_was():
    got = _node("solve([-0.12, 1.25, 0.22], [-0.3, 1.3, 0], at(), [], 8)")   # the pole inward, across the chest
    assert got['cost0'] > 0.05
    assert got['cost'] < 0.012 and got['pushed'] == 0 and got['pulled'] == 0
    assert got['T'] == pytest.approx([-0.12, 1.25, 0.22])
    assert abs(got['phi']) > 0.3


def test_the_elbow_swings_clear_over_a_few_frames_not_in_one_jump():
    got = _node("[solve([-0.12, 1.25, 0.22], [-0.3, 1.3, 0], at(), [], 8).phis, M.SWING_RATE]")
    phis, rate = got
    steps = [abs(b - a) for a, b in zip([0] + phis, phis)]
    assert max(steps) <= rate / 30 + 1e-9                                      # at most SWING_RATE a second
    assert abs(phis[-1]) > 0.3 and steps[-1] < 0.05                            # there, and settled


def test_a_straight_arm_through_the_chest_brings_the_clavicle_forward_before_the_hand_moves():
    got = _node("solve([-0.33, 1.32, 0.02], [0.6, 1.2, -0.3], at())")
    assert got['cost0'] > 0.05
    assert got['alpha'] == pytest.approx(-22 * 3.14159265 / 180, abs=0.01)     # the left shoulder turned forward
    assert got['pulled'] > 0 and got['cost'] < got['cost0'] / 3


def test_a_hand_through_a_partner_is_pushed_out_of_her():
    got = _node("solve([0.02, 1.32, 0.42], [0.6, 1.2, -0.3], null, [at(0.5, Math.PI)])")
    assert got['pushed'] > 0.05 and got['hand'] < 0.012
    assert got['T'][2] < 0.34                                                  # her front is at 0.5 - 0.13 - the hand


def test_hips_closer_than_two_bodies_allow_are_told_not_moved():
    got = _node("[M.overlapOf(at(), at(0.15, Math.PI)), M.overlapOf(at(), at(0.6, Math.PI))]")
    assert got[0]['dist'] == pytest.approx(0.15) and got[0]['need'] > 0.2 and got[1] is None


def test_the_counter_measures_each_limb():
    got = _node("""(() => { const D = M.newDepths();
      M.limbDepths(at(), [at(0.5, Math.PI)], v(0.19, 1.49, 0), v(0.05, 1.3, 0.1), v(0.02, 1.3, 0.42), v(0.02, 1.3, 0.45), 0.05, 0.035, 0.035, D);
      return { self: Array.from(D.self), other: Array.from(D.other), who: Array.from(D.who) }; })()""")
    assert got['self'][0] > 0.02 and got['self'][2] == 0                       # the upper arm in himself, the hand not
    assert got['other'][2] > 0.02 and got['who'][2] == 0                       # the hand in her


def test_a_hand_sent_past_reach_is_eased_in_so_the_elbow_keeps_a_bend():
    got = _node("""{ k: [0.5, 0.85, 0.9, 1.0, 1.2, 1.6].map((e) => M.softReach(e)), knee: M.REACH_KNEE, max: M.REACH_MAX,
      job: (() => { const j = M.newArm().job; j.S.set([0, 1.5, 0]); j.T.set([0, 1.5, 0.81]); j.la = 0.28; j.lb = 0.26;
        const r = M.reachIn(j); return { r, T: Array.from(j.T) }; })() }""")
    k = got['k']
    assert k[0] == 0.5 and k[1] == 0.85                                         # under the knee: as sent
    assert all(a < b for a, b in zip(k[1:], k[2:]))                            # further still reaches further
    assert 0.92 < k[3] < got['max'] and k[5] < got['max']                     # full reach: 92% or so; never past 95%
    r, T = got['job']['r'], got['job']['T']
    assert r[0] == pytest.approx(1.5) and r[1] == pytest.approx(_soft(1.5))
    assert T[0] == 0 and T[1] == 1.5 and T[2] == pytest.approx(r[1] * 0.54)  # along the shoulder's line


def _soft(e, knee=0.85, top=0.95):
    import math
    return e if e <= knee else knee + (top - knee) * (1 - math.exp(-(e - knee) / (top - knee)))


def test_a_resting_arm_that_gives_way_hangs_relaxed_beside_the_body():
    got = _node("""(() => { const st = at(), S = v(0.19, 1.49, 0), E = v(0, 0, 0), H = v(0, 0, 0);
      M.relaxedArm(st, S, 0.28, 0.26, 1, E, H);
      return { E: Array.from(E), H: Array.from(H), inside: M.ballDepth(st, H, 0.035, null),
        near: M.depthIn([at(0.5, Math.PI)], v(0.02, 1.3, 0.3), 0.1), far: M.depthIn([at(0.5, Math.PI)], v(0.02, 1.3, 0.1), 0.1) }; })()""")
    import math
    S, E, H = [0.19, 1.49, 0], got['E'], got['H']
    up = [E[i] - S[i] for i in range(3)]
    fore = [H[i] - E[i] for i in range(3)]
    assert math.dist(S, E) == pytest.approx(0.28) and math.dist(E, H) == pytest.approx(0.26)
    assert S[1] - E[1] > 0.25                                                  # the elbow hangs low under the shoulder
    bend = math.degrees(math.acos(sum(a * b for a, b in zip(up, fore)) / (0.28 * 0.26)))
    assert 50 < bend < 90                                                      # bent, not held out straight
    assert H[1] < E[1] + 0.05 and 0.15 < H[2] < 0.3                            # the hand low, in front of the hips
    assert abs(H[0]) < abs(S[0])                                               # and in toward the body, not out
    assert got['inside'] == 0
    assert got['near'] > 0 and got['far'] == 0                                 # 10 cm of margin finds her, 30 does not


def test_a_standing_body_keeps_the_eyes_height_and_a_start_pose_elbow_stays_off_the_arm_line():
    js = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'actors.js').read_text(encoding='utf-8')
    # the headset's offset to the head bone is level only: the body hangs at the eyes' height, set back from them
    assert 'new THREE.Vector3(0, EYE_UP, -EYE_FWD).applyQuaternion(qc).setY(0)' in js
    assert 'addScaledVector(lateral, POSE_POLE_OUT)' in js


def test_body_collide_reaches_the_page(stage):
    page = FakePage(stage['port'], 'room', answers={'body_collide': {'on': False, 'update_ms': 0.4, 'body_ms': 0.05}})
    try:
        out = OPS['stage_body_collide'](scene='room', on=False)
        assert (page.seen[-1]['type'], page.seen[-1]['on']) == ('body_collide', False) and 'body_ms' in out
        OPS['stage_body_collide'](scene='room')
        assert not {'on', 'reach', 'relax'} & set(page.seen[-1])                # all omitted: only reads
        OPS['stage_body_collide'](scene='room', reach=False)
        assert page.seen[-1]['reach'] is False and 'on' not in page.seen[-1] and 'relax' not in page.seen[-1]
        with pytest.raises(OpError, match='stage_body_collide'):
            OPS['stage_cmd'](scene='room', type='body_collide')
    finally:
        page.stop = True


def test_a_thing_let_go_waits_regrab_s_before_a_hand_takes_it_again(stage):
    sc = stage['scenes']
    page = FakePage(stage['port'], 'room', answers={'grab_mode': {'takeable': ['bottle']}})
    try:
        out = OPS['stage_grab_mode'](scene='room', objects='bottle', regrab_s=0.8)
        assert 'not taken again for 0.8 s after' in out
        assert (page.seen[-1]['type'], page.seen[-1]['regrab_s']) == ('grab_mode', 0.8)
        assert W.load_world(sc, 'room')['grab']['regrab_s'] == 0.8
        with pytest.raises(OpError, match='grab.regrab_s must be seconds from 0 to 5'):
            OPS['stage_grab_mode'](scene='room', regrab_s=9)
        assert W.load_world(sc, 'room')['grab']['regrab_s'] == 0.8               # refused: nothing written
    finally:
        page.stop = True
    js = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'grab.js').read_text(encoding='utf-8')
    assert "regrab_s: 0.5" in js and 'letGoAt' in js                          # the page's default and its wait
