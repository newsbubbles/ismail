"""Keyed motion between keys (page/interp.js, run in node) and trial moves (the ops' side)."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from ismail.api import OPS, OpError
from ismail.stage import interp as PI
from test_stage import FakePage, stage  # noqa: F401,F811  (the fixture)

INTERP = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'interp.js').as_uri()


def _node(expr):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    js = f"import('{INTERP}').then((m) => {{ console.log(JSON.stringify({expr})); }});"
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


KEYS = [{'t': 0, 'location': [0, 0, 0]}, {'t': 5, 'location': [10, 0, 0]}, {'t': 10, 'location': [10, 10, 0]}]


def test_smooth_glides_through_a_key_and_stop_halts_there():
    ks = json.dumps(KEYS)
    got = _node(f"""(() => {{ const ks = {ks}, v = (t, m) => m.vec(ks, t, 'location', m.mode);
      const at = (t, mode) => m.vec(ks, t, 'location', mode);
      const speed = (mode) => Math.hypot(...at(5.01, mode).map((x, j) => (x - at(4.99, mode)[j]) / 0.02));
      return {{ keys: [0, 5, 10].map((t) => at(t, 'smooth')), stopSpeed: speed('stop'), smoothSpeed: speed('smooth'),
               mid: at(2.5, 'smooth'), midStop: at(2.5, 'stop') }}; }})()""")
    assert got['keys'] == [[0, 0, 0], [10, 0, 0], [10, 10, 0]]          # both pass through every key
    assert got['stopSpeed'] < 0.05                                     # stop: at rest on the middle key
    # smooth: the tangent at the middle key is (p2 - p0) / (t2 - t0) = (1, 1, 0) m/s, length sqrt(2)
    assert got['smoothSpeed'] == pytest.approx(2 ** 0.5, rel=0.01)
    assert got['midStop'] == [5, 0, 0] and got['mid'][1] < 0             # Hermite overshoots a little before the turn


def test_slerp_share_follows_the_mode():
    got = _node("[m.slerpK(0.25, 'stop'), m.slerpK(0.25, 'smooth'), m.segment([{t: 0}, {t: 4}], 1)]")
    assert got[0] == pytest.approx(0.15625) and got[1] == 0.25 and got[2] == {'i': 0, 'u': 0.25}


def test_key_interp_and_trial_set_reach_the_page(stage):
    page = FakePage(stage['port'], 'room')
    try:
        OPS['stage_key_interp'](scene='room', object='cf_hall_wide_cam', mode='smooth')
        assert (page.seen[-1]['type'], page.seen[-1]['name'], page.seen[-1]['mode']) == ('key_interp', 'cf_hall_wide_cam', 'smooth')
        with pytest.raises(OpError, match="'stop' or 'smooth'"):
            OPS['stage_key_interp'](scene='room', object='x', mode='bezier')
        OPS['stage_object_set'](scene='room', object='cf_hall_wide_cam', offset=[0, 0, 0.5], trial=True)
        assert page.seen[-1]['trial'] is True
        OPS['stage_object_set'](scene='room', object='cf_hall_wide_cam', offset=[0, 0, 0.5])
        assert 'trial' not in page.seen[-1]
        with pytest.raises(OpError, match='stage_key_interp'):
            OPS['stage_cmd'](scene='room', type='key_interp')
    finally:
        page.stop = True


ROT = [{'t': 0, 'location': [0, 0, 0], 'quaternion': [1, 0, 0, 0], 'scale': [1, 1, 1]},
       {'t': 2.5, 'location': [3, 1, 0.5], 'quaternion': [0.9239, 0, 0, 0.3827], 'scale': [1, 1, 1.2]},
       {'t': 7, 'location': [3, 6, 1.5], 'quaternion': [-0.7071, 0, 0, -0.7071], 'scale': [1.1, 1, 1]},
       {'t': 10, 'location': [-2, 8, 1.6], 'quaternion': [0.5, 0, 0, 0.866], 'scale': [1, 1, 1]}]


def test_the_page_and_python_sample_the_same_curves():
    """The stage (interp.js) and a render (interp.py) within a millimetre, in both modes, everywhere along the keys."""
    ts = [round(0.1 * k, 2) for k in range(-5, 106)]
    got = _node(f"""(() => {{ const ks = {json.dumps(ROT)}, ts = {json.dumps(ts)};
      return ['stop', 'smooth'].map((mode) => ts.map((t) => [m.vec(ks, t, 'location', mode), m.vec(ks, t, 'scale', mode),
        m.segment(ks, t) ? m.slerpK(m.segment(ks, t).u, mode) : null])); }})()""")
    worst = 0.0
    for mode, rows in zip(('stop', 'smooth'), got):
        for t, (loc, scale, k) in zip(ts, rows):
            py = PI.sample(ROT, t, mode)
            worst = max(worst, *(abs(a - b) for a, b in zip(loc, py['location'])), *(abs(a - b) for a, b in zip(scale, py['scale'])))
            seg = PI.segment(ROT, t)
            assert (k is None) == (seg is None) and (k is None or abs(k - PI.slerp_k(seg[1], mode)) < 1e-9)
    assert worst < 1e-3


def test_python_turns_take_the_short_way_and_read_the_mode_from_anim():
    anim = {'objects': {'cam': ROT}, 'interp': {'cam': 'smooth'}}
    q = PI.sample_anim(anim, 'cam', 5.0)['quaternion']
    assert abs(sum(x * x for x in q) - 1) < 1e-9
    # keys 2.5 -> 7: 45 deg about z, then the 90 deg turn written with negated signs; the short way passes 67.5 deg
    # halfway (smooth: u = 0.5 at t = 4.75), not the long way round through 247.5
    w, x, y, z = PI.sample(ROT, 4.75, 'smooth')['quaternion']
    assert 2 * math.degrees(math.acos(min(1, abs(w)))) == pytest.approx(67.5, abs=0.2) and abs(x) + abs(y) < 1e-9
    assert PI.sample_anim(anim, 'cam', 2.5)['location'] == [3, 1, 0.5]
    assert PI.sample_anim({'objects': {'cam': ROT}}, 'cam', 1.25)['location'] == PI.sample(ROT, 1.25, 'stop')['location']


def test_take_playback_mixes_between_samples_without_touching_them():
    """Playback (actors.js) poses the frame between the two samples around t: positions lerp, quaternions nlerp the
    short way and stay unit, names and a hand missing in one sample come from the nearer one, and the buffer it
    writes into is never a recorded sample."""
    s = math.sqrt(0.5)
    a = {'t': 0, 'head': [0, 1.6, 0, 0, 0, 0, 1], 'left': {'g': 'none', 'j': [[0, 1, 0, 0, 0, 0, 1, 0.01]]}, 'right': None,
         'body': {'hips': [0, 1, 0, 0, 0, 0, 1]}}
    b = {'t': 0.05, 'head': [1, 1.6, 0, 0, -s, 0, -s], 'left': {'g': 'fist', 'j': [[1, 1, 0, 0, 0, 0, 1, 0.03]]},
         'right': {'g': 'none', 'j': []}, 'body': {'hips': [2, 1, 0, 0, 0, 0, 1]}}
    got = _node(f"""(() => {{ const a = {json.dumps(a)}, b = {json.dumps(b)}, a0 = JSON.stringify(a), b0 = JSON.stringify(b);
        const m1 = m.mixFrame(a, b, 0.25), n1 = Math.hypot(...m1.head.slice(3, 7)), keep = m1;
        const m2 = m.mixFrame(b, a, 0.5, m1);                       // the next frame reuses the buffer
        const m3 = m.mixFrame(a, b, 0.75, m2);
        return {{ m1head: JSON.parse(JSON.stringify(m.mixFrame(a, b, 0.25).head)), n1, reused: m2 === keep && m3 === keep,
          m3: {{ t: m3.t, x: m3.head[0], g: m3.left.g, r: m3.left.j[0][7], hips: m3.body.hips[0], right: m3.right }},
          untouched: JSON.stringify(a) === a0 && JSON.stringify(b) === b0 }}; }})()""")
    assert got['untouched'] and got['reused']
    assert abs(got['n1'] - 1) < 1e-9
    h = got['m1head']
    assert abs(h[0] - 0.25) < 1e-9 and h[6] > 0.9          # the short way: b's quaternion was given negated
    assert 0.15 < h[4] < 0.25                                # a quarter of the way into the 90 degree turn (sin 11.25 deg = 0.195)
    m3 = got['m3']
    assert abs(m3['t'] - 0.0375) < 1e-9 and abs(m3['x'] - 0.75) < 1e-9 and abs(m3['hips'] - 1.5) < 1e-9
    assert m3['g'] == 'fist' and abs(m3['r'] - 0.025) < 1e-9 and m3['right'] == {'g': 'none', 'j': []}


def test_take_playback_mixes_only_what_posing_reads():
    a = {'t': 0, 'head': [0, 1.6, 0, 0, 0, 0, 1], 'left': None, 'right': None, 'body': {'hips': [0, 1, 0, 0, 0, 0, 1]}}
    b = {'t': 0.1, 'head': [1, 1.6, 0, 0, 0, 0, 1], 'left': None, 'right': None, 'body': {'hips': [2, 1, 0, 0, 0, 0, 1]}}
    got = _node(f"""(() => {{ const a = {json.dumps(a)}, b = {json.dumps(b)}, o = m.mixTake(a, b, 0.5);
        return {{ x: o.head[0], t: o.t, bodySame: o.body === a.body, again: m.mixTake(b, a, 0.5, o) === o }}; }})()""")
    assert got == {'x': 0.5, 't': 0.05, 'bodySame': True, 'again': True}


LOOKS = [([-9, -10, 1.3], [-4.5, -4.5, 1.6]), ([7.3, 10.5, 1.6], [12, 12, 1.2]), ([0, 0, 5], [1, 0, 0]),
         ([0, 0, 5], [0, 0, 0]), ([1, 2, 3], [1, 2, 9])]


def _turn(q, v):                                     # rotate v by the unit quaternion q (w, x, y, z)
    w, x, y, z = q
    u = (x, y, z)
    c = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    c2 = (u[1] * c[2] - u[2] * c[1], u[2] * c[0] - u[0] * c[2], u[0] * c[1] - u[1] * c[0])
    return [v[i] + 2 * (w * c[i] + c2[i]) for i in range(3)]


def test_a_look_key_aims_minus_z_at_the_point_the_same_on_page_and_in_python():
    page = _node(f"{json.dumps(LOOKS)}.map(([p, l]) => m.lookQuat(p, l))")
    for (pos, look), q in zip(LOOKS, page):
        assert q == pytest.approx(PI.look_quat(pos, look), abs=1e-9)
        f = [look[i] - pos[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in f))
        assert _turn(q, (0, 0, -1)) == pytest.approx([x / n for x in f], abs=1e-9)   # -Z looks at the point
        if abs(f[2]) < n * 0.99:
            assert _turn(q, (0, 1, 0))[2] > 0                                        # up stays up


def test_many_keys_go_to_the_page_as_one_command(stage):
    page = FakePage(stage['port'], 'room')
    try:
        keys = [{'name': 'cam_a', 't': 0, 'location': [0, -5, 1.5], 'look': [0, 0, 1.5]},
                {'name': 'cam_a', 't': 4, 'location': [0, -2, 1.5], 'look': [0, 0, 1.5]},
                {'name': 'door', 't': 2, 'quaternion': [1, 0, 0, 0]}]
        OPS['stage_keys_set'](scene='room', keys=keys, replace=['cam_a'], interp={'cam_a': 'smooth'})
        c = page.seen[-1]
        assert (c['type'], c['keys'], c['replace'], c['interp']) == ('key', keys, ['cam_a'], {'cam_a': 'smooth'})
        n = len(page.seen)
        with pytest.raises(OpError, match='needs "name" and "t"'):
            OPS['stage_keys_set'](scene='room', keys=[{'name': 'cam_a'}])
        with pytest.raises(OpError, match='unknown fields'):
            OPS['stage_keys_set'](scene='room', keys=[{'name': 'cam_a', 't': 0, 'rotation': [0, 0, 0]}])
        with pytest.raises(OpError, match="'stop' or 'smooth'"):
            OPS['stage_keys_set'](scene='room', keys=keys, interp={'cam_a': 'bezier'})
        assert len(page.seen) == n                                                   # nothing sent
    finally:
        page.stop = True
