"""How a keyed object moves between its keys: the same curves as the stage page (ismail/stage/page/interp.js), for
whatever turns anim.json into a render, so the stage and the render never drift apart (they were 74 cm apart between
camera keys once). tests/test_stage_interp.py samples both and holds them within a millimetre.

anim.json: {"objects": {name: [{"t", "location", "quaternion" [w, x, y, z], "scale"}, ...]}, "interp": {name: mode}}
  "stop"    (default) each segment eases in and out (smoothstep): the object stops at every key
  "smooth"  location and scale follow a cubic Hermite curve per segment, tangent at key i
            (p[i+1] - p[i-1]) / (t[i+1] - t[i-1]) (one-sided at the ends); the turn slerps at u, no easing
The turn takes the short way (a quaternion is negated when its dot with the previous key's is negative).
"""
import math


def ease(k):
    return k * k * (3 - 2 * k)


def segment(ks, t):
    """(i, u): keys i and i + 1 and how far between them; None outside the keys."""
    if len(ks) < 2 or t <= ks[0]['t'] or t >= ks[-1]['t']:
        return None
    i = 0
    while ks[i + 1]['t'] < t:
        i += 1
    return i, (t - ks[i]['t']) / max(1e-6, ks[i + 1]['t'] - ks[i]['t'])


def vec(ks, t, f, mode='stop'):
    """A location or scale (key field f) at time t."""
    if t <= ks[0]['t']:
        return list(ks[0][f])
    if t >= ks[-1]['t']:
        return list(ks[-1][f])
    i, u = segment(ks, t)
    a, b = ks[i], ks[i + 1]
    if mode != 'smooth':
        k = ease(u)
        return [x + (y - x) * k for x, y in zip(a[f], b[f])]
    h = b['t'] - a['t']

    def tan(n):
        p, q = ks[max(0, n - 1)], ks[min(len(ks) - 1, n + 1)]
        return [(y - x) / max(1e-6, q['t'] - p['t']) for x, y in zip(p[f], q[f])]
    ma, mb = tan(i), tan(i + 1)
    u2, u3 = u * u, u * u * u
    h00, h10, h01, h11 = 2 * u3 - 3 * u2 + 1, u3 - 2 * u2 + u, -2 * u3 + 3 * u2, u3 - u2
    return [h00 * x + h10 * h * m0 + h01 * y + h11 * h * m1 for x, y, m0, m1 in zip(a[f], b[f], ma, mb)]


def slerp_k(u, mode='stop'):
    return u if mode == 'smooth' else ease(u)


def look_quat(pos, look):
    """A Blender rotation (w, x, y, z) whose -Z looks from pos at look, world Z up (interp.js lookQuat)."""
    def norm(v):
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    f = norm([look[i] - pos[i] for i in range(3)])
    r = cross(f, [0, 0, 1])
    r = [1.0, 0.0, 0.0] if math.sqrt(sum(x * x for x in r)) < 1e-6 else norm(r)
    u = cross(r, f)
    m = [[r[0], u[0], -f[0]], [r[1], u[1], -f[1]], [r[2], u[2], -f[2]]]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1) * 2
        q = [0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s]
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = [(m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s]
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = [(m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s]
    else:
        s = math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = [(m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s]
    return [-x for x in q] if q[0] < 0 else q


def _slerp(qa, qb, k):
    """[w, x, y, z] quaternions, the short way."""
    d = sum(x * y for x, y in zip(qa, qb))
    if d < 0:
        qb, d = [-x for x in qb], -d
    if d > 0.9995:
        q = [x + (y - x) * k for x, y in zip(qa, qb)]
    else:
        th = math.acos(min(1.0, d))
        s = math.sin(th)
        wa, wb = math.sin((1 - k) * th) / s, math.sin(k * th) / s
        q = [wa * x + wb * y for x, y in zip(qa, qb)]
    n = math.sqrt(sum(x * x for x in q))
    return [x / n for x in q]


def sample(ks, t, mode='stop'):
    """{location, quaternion [w, x, y, z], scale} of a keyed object at time t (the page's clock.js sample)."""
    if t <= ks[0]['t']:
        return {k: list(ks[0][k]) for k in ('location', 'quaternion', 'scale') if k in ks[0]}
    if t >= ks[-1]['t']:
        return {k: list(ks[-1][k]) for k in ('location', 'quaternion', 'scale') if k in ks[-1]}
    i, u = segment(ks, t)
    out = {'location': vec(ks, t, 'location', mode)}
    if 'quaternion' in ks[i]:
        out['quaternion'] = _slerp(ks[i]['quaternion'], ks[i + 1]['quaternion'], slerp_k(u, mode))
    if 'scale' in ks[i]:
        out['scale'] = vec(ks, t, 'scale', mode)
    return out


def sample_anim(anim, name, t):
    """An object of an anim.json dict at time t, in its own interp mode."""
    return sample(anim['objects'][name], t, (anim.get('interp') or {}).get(name, 'stop'))
