"""spatial: move a sound in 3D, rendered for stereo speakers (plain panning, or crosstalk-cancelled transaural) or
headphones (binaural), in the studio and live, with movement an agent can code.

Coordinates are metres with the listener at the origin: x to the right, y to the front, z up. Position comes from
the automatable x, y, z params, or from a `trajectory` (keyframes, orbit, line, flyby) evaluated on song time in
the studio and on the time since the trajectory started live.

Signal chain, per sample, causal (one numba kernel that both the studio and the live block twin run):
  input summed to mono
  -> air absorption (a one-pole whose cutoff falls with distance, fitted to ISO 9613-1 at 20 C, 50 % RH)
  -> occlusion on the direct path only (high cut that grows with frequency, a level drop, a little smearing)
  -> one fractional delay line with cubic read heads: propagation delay (Doppler) plus the interaural delay
     (Woodworth spherical head), so ITD and Doppler come from the same line
  -> clamped 1/r gain (reference 1 m), per-ear near-field level, plus a ground reflection (image source)
  -> head model: Brown-Duda head shadow per ear, pinna echoes that move with elevation and front/back, a rear
     high-shelf cue; or measured HRIRs (min-phase, crossfaded per 128 samples) when hrtf names a fetched set
  -> out='headphones': the two ear signals (binaural).
     out='speakers': one render, equal-power panning with every distance cue (no interaural delay, so nothing
     combs), run through a recursive 2x2 crosstalk canceller for speakers at +-30 degrees whose depth is ctc
     (0 = off: plain panning; the exact inverse of the symmetric head-shadow plant, regularised: crosstalk below
     150 Hz is left alone and the loop gain is bounded at 0.9). The near speaker stays the louder one at every
     frequency and every ctc, and the far ear hears less of the sound as ctc rises
  plus the effect's own room: early reflections and an 8-line feedback delay network fed from the un-occluded
  line, sent at a level that falls slower than the direct sound, so far sources turn reverb-dominant.

Sources: Brown and Duda 1998 (structural binaural model), Woodworth (ITD), Cooper and Bauck 1989 and Kirkeby,
Nelson, Hamada 1998 (crosstalk cancellation), ISO 9613-1 (air), Jot 1991 (FDN). See
songs/_migration/SPATIAL_RESEARCH.md in the ismail repository for the survey this follows.
"""
import math
import os

import numpy as np
from numba import njit

C_SOUND = 343.0
HEAD_A = 0.0875             # head radius, m
R_MIN = 0.25                # the 1/r gain stops growing inside this distance
EAR_H = 1.6                 # ear height above the ground, m (ground reflection)
MAX_DELAY_S = 1.0           # propagation delay stops growing past ~343 m (fixed buffer: studio and live agree)
POS_TAU_S = 0.03            # position and param smoothing: no zipper, no clicks on a jump
SLEW = 0.5                  # the propagation delay moves at most this many samples per sample (pitch 0.5x..1.5x)
CTC_SPAN_DEG = 30.0         # speakers at +-30 degrees
CTC_G = 0.9                 # crosstalk loop gain bound (regularisation)
CTC_HP_HZ = 150.0           # crosstalk below this is not cancelled (no low-frequency blow-up)
HRIR_K = 128                # measured HRIRs switch on this grid of processor samples, crossfaded over it
HRIR_TAPS = 128

OUTS = ('speakers', 'headphones')
GROUNDS = ('soft', 'hard')
HRTFS = ('model', 'sadie_d2')

DEFAULTS = {
    "spatial": {"x": 0.0, "y": 1.0, "z": 0.0, "out": "speakers", "ctc": 0.5, "doppler": 1, "occlusion": 0.0,
                "ground": "soft", "room": 0.4, "size": 0.5, "spread": 0.0, "listener_yaw": 0.0,
                "trajectory": None, "hrtf": "model"},
}
AUTOMATABLE = {"spatial": ("x", "y", "z", "occlusion", "listener_yaw", "ctc", "room", "spread")}
DOCS = {
    "spatial": (
        "move a sound in 3D. x, y, z metres (listener at 0,0,0; x right, y front, z up), automatable; the input is "
        "summed to mono, so leave the track's pan at centre. out='speakers' (default) | 'headphones' (binaural). "
        "ctc 0-1 (speakers only, experimental, default 0.5): 0 = plain stereo panning with the distance cues; above 0 "
        "a crosstalk canceller of that depth runs on the same pan, so for a listener centred between speakers at "
        "+-30 deg less of each speaker reaches the far ear and side sounds move outside the speakers (the near "
        "speaker stays the louder one); rear and height stay weak on speakers. doppler 1|0: the propagation delay "
        "(pitch moves on a flyby, a far sound arrives late); 0 for pitched parts that must stay in key. occlusion 0-1: "
        "stuff in the way (foliage), darker, quieter, smeared direct sound; the reverb is not occluded, so far + "
        "occluded turns reverb-dominant. ground 'soft' | 'hard' (strong floor reflection, sound over water). room 0-1: "
        "the effect's own reverb amount (direct and reverb are equal at about 1/room^2 m), size 0-1: room size "
        "(reflection spacing and decay, 0.4 s .. 4 s). spread 0-1: a wide source (decorrelated). listener_yaw deg, "
        "automatable: the listener turns right. trajectory: a path instead of x/y/z (x/y/z are then ignored), times in "
        "seconds from song start in the studio, from the moment it is set or inserted live: "
        "{'orbit': {'radius': 3, 'period_s': 8, 'centre': [0, 0, 0], 'elevation': 0, 'direction': 'cw', "
        "'start_az': 0}} | {'line': {'from': [-20, 5, 0], 'to': [20, 5, 0], 'duration_s': 6}} | {'flyby': {'from': "
        "..., 'to': ..., 'speed_mps': 15}} | {'keys': [[0, -5, 2, 0], [4, {'az': 90, 'el': 0, 'dist': 3}]], "
        "'interp': 'linear' | 'smooth'}; any of them takes 'loop': True and 'start_s'. A point is [x, y, z] or "
        "{'az': deg (positive = right), 'el': deg, 'dist': m}. hrtf 'model' (built-in head, no data) | 'sadie_d2' "
        "(measured KU100 dummy head, fetched with samples_fetch('sadie_d2') after the person says yes). Cues need "
        "broadband sound: a sine cannot be placed front/back or up/down."),
}


class SpatialError(ValueError):
    pass


# ------------------------------------------------------------------ trajectory

TRAJ_KINDS = ('keys', 'orbit', 'line', 'flyby')
_EXAMPLE = "{'orbit': {'radius': 3, 'period_s': 8}} or {'keys': [[0, -5, 2, 0], [4, 5, 2, 0]]}"


def _num(v, where, lo=None, hi=None, pos=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise SpatialError(f"spatial trajectory {where}: needs a number, got {v!r}")
    v = float(v)
    if pos and v <= 0:
        raise SpatialError(f"spatial trajectory {where}: must be above 0, got {v}")
    if lo is not None and v < lo:
        raise SpatialError(f"spatial trajectory {where}: must be at least {lo}, got {v}")
    return v


def point(v, where='point'):
    """[x, y, z] metres, or {'x','y','z'}, or {'az': deg (positive right), 'el': deg, 'dist': m} -> (x, y, z)."""
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(_num(c, f"{where}[{i}]") for i, c in enumerate(v))
    if isinstance(v, dict):
        if set(v) <= {'x', 'y', 'z'} and v:
            return tuple(_num(v.get(k, 0.0), f"{where}.{k}") for k in 'xyz')
        if 'az' in v or 'dist' in v:
            bad = set(v) - {'az', 'el', 'dist'}
            if bad:
                raise SpatialError(f"spatial trajectory {where}: unknown keys {sorted(bad)}; an angle point is "
                                   "{'az': deg, 'el': deg, 'dist': m}")
            az = math.radians(_num(v.get('az', 0.0), f"{where}.az"))
            el = math.radians(_num(v.get('el', 0.0), f"{where}.el"))
            d = _num(v.get('dist', 1.0), f"{where}.dist", lo=0.0)
            return (d * math.cos(el) * math.sin(az), d * math.cos(el) * math.cos(az), d * math.sin(el))
    raise SpatialError(f"spatial trajectory {where}: a point is [x, y, z] in metres (x right, y front, z up) or "
                       f"{{'az': deg, 'el': deg, 'dist': m}} (az positive = right); got {v!r}")


def _key(k, i):
    w = f"keys[{i}]"
    if isinstance(k, (list, tuple)):
        if len(k) == 4:
            return _num(k[0], w + '.t', lo=0.0), point(list(k[1:]), w)
        if len(k) == 2:
            return _num(k[0], w + '.t', lo=0.0), point(k[1], w)
    if isinstance(k, dict) and 't' in k:
        rest = {a: b for a, b in k.items() if a != 't'}
        if 'pos' in rest and len(rest) == 1:
            rest = rest['pos']
        return _num(k['t'], w + '.t', lo=0.0), point(rest, w)
    raise SpatialError(f"spatial trajectory {w}: a key is [t_s, x, y, z], [t_s, {{'az': deg, 'el': deg, 'dist': m}}] "
                       f"or {{'t': t_s, 'x': .., 'y': .., 'z': ..}}; got {k!r}")


class Trajectory:
    """A parsed trajectory: eval(t) -> (x, y, z) arrays for times t in seconds (elementwise, so any cut of the
    time axis gives the same positions)."""

    def __init__(self, kind, start_s, loop, **kw):
        self.kind, self.start_s, self.loop = kind, start_s, loop
        self.__dict__.update(kw)

    def eval(self, t):
        t = np.asarray(t, dtype=np.float64) - self.start_s
        t = np.maximum(t, 0.0)
        if self.kind == 'orbit':
            az = self.az0 + self.sign * 2 * np.pi * t / self.period
            ce, se = math.cos(self.el), math.sin(self.el)
            cx, cy, cz = self.centre
            return (cx + self.radius * ce * np.sin(az), cy + self.radius * ce * np.cos(az),
                    np.full(t.shape, cz + self.radius * se))
        if self.kind == 'line':
            if self.loop:
                t = np.mod(t, self.duration)
            u = np.clip(t / self.duration, 0.0, 1.0)
            a, b = self.p0, self.p1
            return tuple(a[k] + (b[k] - a[k]) * u for k in range(3))
        # keys
        T, Pk = self.times, self.pts
        if self.loop and T[-1] > 0:
            t = np.mod(t, T[-1])
        if len(T) == 1:
            return tuple(np.full(t.shape, Pk[0, k]) for k in range(3))
        if self.interp == 'linear':
            return tuple(np.interp(t, T, Pk[:, k]) for k in range(3))
        # smooth: cubic Hermite through every key, tangents from the neighbours (Catmull-Rom on uneven times)
        tc = np.clip(t, T[0], T[-1])
        i = np.clip(np.searchsorted(T, tc, side='right') - 1, 0, len(T) - 2)
        h = T[i + 1] - T[i]
        s = (tc - T[i]) / h
        h00, h10, h01, h11 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s, -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
        return tuple(h00 * Pk[i, k] + h10 * h * self.tan[i, k] + h01 * Pk[i + 1, k] + h11 * h * self.tan[i + 1, k]
                     for k in range(3))


def parse_trajectory(spec):
    """Validate and compile a trajectory spec once. None or {} -> None. Errors say how to write it."""
    if spec is None or spec == {}:
        return None
    if not isinstance(spec, dict):
        raise SpatialError(f"spatial trajectory: a dict such as {_EXAMPLE}; got {spec!r}")
    kinds = [k for k in TRAJ_KINDS if k in spec]
    extra = set(spec) - set(TRAJ_KINDS) - {'loop', 'start_s', 'interp'}
    if len(kinds) != 1 or extra:
        raise SpatialError(f"spatial trajectory: give exactly one of {', '.join(TRAJ_KINDS)} (plus 'loop', 'start_s', "
                           f"'interp' for keys); got {sorted(spec)}. Example: {_EXAMPLE}")
    kind = kinds[0]
    loop = spec.get('loop', False)
    if not isinstance(loop, bool):
        raise SpatialError(f"spatial trajectory loop: True or False, got {loop!r}")
    start = _num(spec.get('start_s', 0.0), 'start_s', lo=0.0)
    body = spec[kind]
    if kind == 'keys':
        interp = spec.get('interp', 'linear')
        if interp not in ('linear', 'smooth'):
            raise SpatialError(f"spatial trajectory interp: 'linear' or 'smooth' (a curve through the keys), "
                               f"got {interp!r}")
        if not isinstance(body, (list, tuple)) or not body:
            raise SpatialError("spatial trajectory keys: a list of keys [t_s, x, y, z], times rising from 0, e.g. "
                               "[[0, -5, 2, 0], [4, 5, 2, 0]]")
        ks = [_key(k, i) for i, k in enumerate(body)]
        T = np.array([k[0] for k in ks])
        for i in range(1, len(T)):
            if T[i] <= T[i - 1]:
                raise SpatialError(f"spatial trajectory keys[{i}]: times must rise ({T[i]:g} s after {T[i - 1]:g} s); "
                                   "give each key a later t in seconds")
        if loop and T[-1] <= 0:
            raise SpatialError("spatial trajectory: a looping keys path needs a last key after t=0 (its length)")
        Pk = np.array([k[1] for k in ks], dtype=np.float64)
        tan = np.zeros_like(Pk)
        if len(T) > 1:
            tan[0] = (Pk[1] - Pk[0]) / (T[1] - T[0])
            tan[-1] = (Pk[-1] - Pk[-2]) / (T[-1] - T[-2])
            for i in range(1, len(T) - 1):
                tan[i] = (Pk[i + 1] - Pk[i - 1]) / (T[i + 1] - T[i - 1])
        return Trajectory('keys', start, loop, times=T, pts=Pk, tan=tan, interp=interp)
    if not isinstance(body, dict):
        raise SpatialError(f"spatial trajectory {kind}: a dict of its settings, e.g. {_EXAMPLE}")
    if kind == 'orbit':
        bad = set(body) - {'radius', 'period_s', 'centre', 'center', 'elevation', 'direction', 'start_az'}
        if bad:
            raise SpatialError(f"spatial trajectory orbit: unknown keys {sorted(bad)}; it takes radius (m), period_s, "
                               "centre [x, y, z], elevation (deg), direction 'cw' | 'ccw', start_az (deg)")
        for need in ('radius', 'period_s'):
            if need not in body:
                raise SpatialError(f"spatial trajectory orbit: needs {need}, e.g. {{'radius': 3, 'period_s': 8}}")
        d = body.get('direction', 'cw')
        if d not in ('cw', 'ccw'):
            raise SpatialError(f"spatial trajectory orbit.direction: 'cw' (front, right, back, left seen from above) "
                               f"or 'ccw'; got {d!r}")
        return Trajectory('orbit', start, loop, radius=_num(body['radius'], 'orbit.radius', pos=True),
                          period=_num(body['period_s'], 'orbit.period_s', pos=True),
                          centre=point(body.get('centre', body.get('center', [0.0, 0.0, 0.0])), 'orbit.centre'),
                          el=math.radians(_num(body.get('elevation', 0.0), 'orbit.elevation')),
                          sign=1.0 if d == 'cw' else -1.0,
                          az0=math.radians(_num(body.get('start_az', 0.0), 'orbit.start_az')))
    # line / flyby
    bad = set(body) - {'from', 'to', 'duration_s', 'speed_mps'}
    if bad:
        raise SpatialError(f"spatial trajectory {kind}: unknown keys {sorted(bad)}; it takes from, to and duration_s "
                           "(or speed_mps)")
    if 'from' not in body or 'to' not in body:
        raise SpatialError(f"spatial trajectory {kind}: needs from and to, e.g. {{'from': [-20, 5, 0], 'to': [20, 5, 0], "
                           "'duration_s': 6}")
    p0, p1 = point(body['from'], kind + '.from'), point(body['to'], kind + '.to')
    if ('duration_s' in body) == ('speed_mps' in body):
        raise SpatialError(f"spatial trajectory {kind}: give duration_s (seconds from start to end) or speed_mps "
                           "(metres per second), one of them")
    if 'duration_s' in body:
        dur = _num(body['duration_s'], kind + '.duration_s', pos=True)
    else:
        L = math.dist(p0, p1)
        if L <= 0:
            raise SpatialError(f"spatial trajectory {kind}: from and to are the same point; move one of them")
        dur = L / _num(body['speed_mps'], kind + '.speed_mps', pos=True)
    return Trajectory('line', start, loop, p0=p0, p1=p1, duration=dur)


def validate(full):
    """Check a normalized spatial dict; errors say what to use instead."""
    for k, allowed in (('out', OUTS), ('ground', GROUNDS), ('hrtf', HRTFS)):
        if full[k] not in allowed:
            raise SpatialError(f"spatial {k}={full[k]!r}: use {' | '.join(repr(a) for a in allowed)}")
    if full['doppler'] not in (0, 1, True, False):
        raise SpatialError(f"spatial doppler={full['doppler']!r}: 1 (on) or 0 (off: no pitch shift, no arrival delay)")
    for k in ('x', 'y', 'z', 'ctc', 'occlusion', 'room', 'size', 'spread', 'listener_yaw'):
        v = full[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise SpatialError(f"spatial {k}: a number, got {v!r}" + (" (automate it with automation_set "
                                                                      f"fx.<i>.{k})" if k in AUTOMATABLE['spatial'] else ''))
    for k in ('ctc', 'occlusion', 'room', 'size', 'spread'):
        if not 0.0 <= float(full[k]) <= 1.0:
            raise SpatialError(f"spatial {k}={full[k]}: must be between 0 and 1")
    parse_trajectory(full['trajectory'])
    if full['hrtf'] != 'model':
        hrtf_ready(full['hrtf'])


# ------------------------------------------------------------------ measured HRTF (optional)

def hrtf_cache_dir():
    return os.environ.get('ISMAIL_HRTF') or os.path.join(os.path.expanduser('~'), '.ismail', 'hrtf')


def _min_phase(h, nfft=4096):
    """Minimum-phase version of an impulse response with the same magnitude (homomorphic, folded cepstrum)."""
    H = np.abs(np.fft.fft(h, nfft))
    c = np.fft.ifft(np.log(np.maximum(H, 1e-8))).real
    w = np.zeros(nfft)
    w[0] = 1.0
    w[1:nfft // 2] = 2.0
    w[nfft // 2] = 1.0
    return np.fft.ifft(np.exp(np.fft.fft(c * w))).real


def convert_sofa(sofa, sr, taps=HRIR_TAPS):
    """A SOFA-like mapping (an open h5py File, or a dict holding 'Data.IR' (M, 2, N), 'SourcePosition' (M, 3) as
    azimuth deg (positive = LEFT, the SOFA convention), elevation deg, distance, and 'Data.SamplingRate') ->
    (dirs (M, 3) unit vectors in ismail's frame: x right, y front, z up; hrir (M, 2, taps) minimum-phase, the
    interaural delay removed: the effect adds its own from the read heads)."""
    ir = np.asarray(sofa['Data.IR'], dtype=np.float64)
    pos = np.asarray(sofa['SourcePosition'], dtype=np.float64)
    fs = float(np.asarray(sofa['Data.SamplingRate']).ravel()[0])
    if ir.ndim != 3 or ir.shape[1] != 2 or pos.shape != (ir.shape[0], 3):
        raise SpatialError(f"HRTF file: expected Data.IR (M, 2, N) and SourcePosition (M, 3); got {ir.shape} and "
                           f"{pos.shape}")
    if abs(fs - sr) > 0.5:
        raise SpatialError(f"HRTF file is at {fs:g} Hz, the engine runs at {sr} Hz: use the set's {sr // 1000}k file "
                           "(SADIE II ships 44.1 kHz), or hrtf='model'")
    az, el = np.radians(pos[:, 0]), np.radians(pos[:, 1])
    dirs = np.stack([-np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)], axis=1)   # SOFA az is left
    fade = np.ones(taps)
    q = taps // 4
    fade[-q:] = 0.5 * (1 + np.cos(np.pi * np.arange(1, q + 1) / q))
    out = np.empty((ir.shape[0], 2, taps))
    for m in range(ir.shape[0]):
        for e in range(2):
            out[m, e] = _min_phase(ir[m, e])[:taps] * fade
    return np.ascontiguousarray(dirs), out


def _cache_path(name, sr):
    return os.path.join(hrtf_cache_dir(), f"{name}_{int(sr)}.npz")


def hrtf_ready(name, sr=44100):
    """Raise, saying how to get it, unless the HRIR set is converted in the cache or fetched (cheap: files only)."""
    if os.path.exists(_cache_path(name, sr)):
        return
    from . import samples
    if not samples.path(name):
        s = samples.SETS[name]
        raise SpatialError(f"spatial hrtf={name!r}: the {s['what']} is not on this machine. Ask the person, then "
                           f"samples_fetch('{name}') (about {s['size_mb']} MB, {s['licence']}); or use hrtf='model', "
                           "the built-in head, which needs no data")


def load_hrtf(name, sr):
    """The converted HRIR set for hrtf=name, from the ~/.ismail/hrtf cache, converting the fetched SOFA file on
    first use. Raises with the next step when the set is not on this machine."""
    if name == 'model':
        return None
    cache = _cache_path(name, sr)
    if os.path.exists(cache):
        d = np.load(cache)
        return d['dirs'], d['hrir']
    hrtf_ready(name, sr)
    from . import samples
    folder = samples.path(name)
    sofas = sorted(f for f in os.listdir(folder) if f.lower().endswith('.sofa'))
    pick = [f for f in sofas if '44' in f] or sofas
    try:
        import h5py
    except ImportError:
        raise SpatialError("reading a SOFA HRTF file needs h5py once (pip install h5py); the converted set is then "
                           f"cached in {hrtf_cache_dir()}. Or use hrtf='model'")
    with h5py.File(os.path.join(folder, pick[0]), 'r') as f:
        dirs, hrir = convert_sofa(f, sr)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    np.savez(cache, dirs=dirs, hrir=hrir)
    return dirs, hrir


_HRTF = {}


def _hrtf(name, sr):
    k = (name, int(sr))
    if k not in _HRTF:
        _HRTF[k] = load_hrtf(name, sr)
    return _HRTF[k]


# ------------------------------------------------------------------ kernel

# pinna echoes (Brown and Duda 1998): gain, A (samples), B (samples), D, at 44.1 kHz
PINNA_RHO = np.array([0.5, -1.0, 0.5, -0.25, 0.25])
PINNA_A = np.array([1.0, 5.0, 5.0, 5.0, 5.0])
PINNA_B = np.array([2.0, 4.0, 7.0, 11.0, 13.0])
PINNA_D = np.array([1.0, 0.5, 0.5, 0.5, 0.5])
PINNA_AMT = 0.5
REAR_HZ = 3000.0
REAR_CUT = 0.45             # high band down by this fraction directly behind (-5 dB)
ER_MS = np.array([5.3, 8.9, 12.7, 17.1, 23.3, 29.9])
ER_G = np.array([0.7, 0.6, 0.55, 0.45, 0.4, 0.33])
ER_PAN = np.array([[1.0, 0.3], [0.3, 1.0], [0.9, 0.5], [0.5, 0.9], [1.0, 0.2], [0.2, 1.0]])
FDN_LEN = np.array([1031, 1327, 1523, 1789, 2053, 2311, 2671, 2999], dtype=np.float64)
FDN_SIGN_L = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
FDN_SIGN_R = np.array([1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0])
FDN_DAMP = 0.35
WET_NORM = 0.8             # direct (1/r) and reverb are equal near r = 1/room^2 m
AP_LEN = np.array([113, 337, 127, 353], dtype=np.int64)       # spread: two allpasses per side
AP_G = 0.6
SMEAR_LEN = 191             # occlusion smearing allpass, ~4.3 ms
GROUND = {'soft': (0.35, 2000.0), 'hard': (0.85, 0.0)}        # reflection gain, lowpass Hz (0 = none)

# float state slots
S_INIT, S_X, S_Y, S_Z, S_BASE, S_BASEG, S_AIR, S_OCC, S_GLP = 0, 1, 2, 3, 4, 5, 6, 7, 8
S_SHX, S_SHY = 9, 11        # head shadow x1 / y1, two ears each
S_REAR = 13                 # rear lp: L, R, centre
S_CHP = 16                  # ctc highpass x1, y1 per channel (4)
S_CSH = 20                  # ctc shelf x1, y1 per channel (4)
S_ERLP = 24                 # early reflection lowpass L, R
S_FDNLP = 26                # 8 damping states
                            # 34: spare
S_PAR = 35                  # smoothed occlusion, spread, ctc, room
N_STATE = 39


@njit(cache=True)
def _cubic(buf, mask, p):
    i = int(math.floor(p))
    f = p - i
    xm1 = buf[(i - 1) & mask]
    x0 = buf[i & mask]
    x1 = buf[(i + 1) & mask]
    x2 = buf[(i + 2) & mask]
    c1 = 0.5 * (x1 - xm1)
    c2 = xm1 - 2.5 * x0 + 2.0 * x1 - 0.5 * x2
    c3 = 0.5 * (x2 - xm1) + 1.5 * (x0 - x1)
    return ((c3 * f + c2) * f + c1) * f + x0


@njit(cache=True)
def _lin(buf, mask, p):
    i = int(math.floor(p))
    f = p - i
    return buf[i & mask] * (1.0 - f) + buf[(i + 1) & mask] * f


@njit(cache=True)
def _alpha(cos_t):
    # Brown-Duda head shadow: alpha(theta) with alpha_min 0.1, theta_min 150 deg; theta from the ear's axis
    th = math.acos(min(1.0, max(-1.0, cos_t)))
    return 1.05 + 0.95 * math.cos(th / (150.0 / 180.0 * math.pi) * math.pi)


@njit(cache=True)
def _kernel(mono, cur, sr, doppler, headphones, g_ground, a_ground,
            er_off, fdn_len, fdn_g, st, ist, lineA, lineB, smear, pin, xh, fdn, ap, use_hrir, hdirs, hrir, hh, out):
    n = len(mono)
    maskA = len(lineA) - 1
    mask_s = len(smear) - 1
    mask_p = pin.shape[1] - 1
    mask_x = xh.shape[1] - 1
    mask_f = fdn.shape[1] - 1
    mask_a = ap.shape[1] - 1
    mask_h = hh.shape[1] - 1
    taps = hrir.shape[2]
    a_pos = math.exp(-1.0 / (POS_TAU_S * sr))
    K2 = 2.0 * sr
    w0 = C_SOUND / HEAD_A
    a1_sh = (2 * w0 - K2) / (2 * w0 + K2)
    itd_max = HEAD_A / C_SOUND * (math.pi / 2 + 1.0) * sr
    D0 = 3.0 + math.ceil(0.5 * itd_max)      # whole samples: a still source with doppler=0 reads unfiltered
    max_d = MAX_DELAY_S * sr
    a_rear = math.exp(-2 * math.pi * REAR_HZ / sr)
    a_er = math.exp(-2 * math.pi * 6000.0 / sr)
    sr_k = sr / 44100.0
    # crosstalk path for speakers at +-span: delay, relative shelf (contra / ipsi head shadow), highpass
    span = CTC_SPAN_DEG * math.pi / 180.0
    dx = HEAD_A / C_SOUND * (span + math.sin(span)) * sr
    al_i = _alpha(math.sin(span))
    al_c = _alpha(-math.sin(span))
    den = 2 * w0 + al_i * K2
    cb0, cb1, ca1 = (2 * w0 + al_c * K2) / den, (2 * w0 - al_c * K2) / den, (2 * w0 - al_i * K2) / den
    wh = math.tan(math.pi * CTC_HP_HZ / sr)
    hb0 = 1.0 / (1.0 + wh)
    ha1 = (wh - 1.0) / (wh + 1.0)
    inv8 = 1.0 / math.sqrt(8.0)
    tmp = np.empty(8)
    dec = np.empty(2)
    for i in range(n):
        cnt = ist[0]
        m = mono[i]
        # --- position: world -> head (listener turned right by yaw), smoothed
        cy = math.cos(cur[3, i])
        sy = math.sin(cur[3, i])
        hx = cur[0, i] * cy - cur[1, i] * sy
        hy = cur[1, i] * cy + cur[0, i] * sy
        hz = cur[2, i]
        if st[S_INIT] == 0.0:
            st[S_X] = hx
            st[S_Y] = hy
            st[S_Z] = hz
        else:
            st[S_X] = hx + a_pos * (st[S_X] - hx)
            st[S_Y] = hy + a_pos * (st[S_Y] - hy)
            st[S_Z] = hz + a_pos * (st[S_Z] - hz)
        # the other automatable params glide the same way (a step in automation must not click)
        for k in range(4):
            v = cur[4, i] if k == 0 else (cur[7, i] if k == 1 else (cur[5, i] if k == 2 else cur[6, i]))
            v = min(1.0, max(0.0, v))
            st[S_PAR + k] = v if st[S_INIT] == 0.0 else v + a_pos * (st[S_PAR + k] - v)
        px, py, pz = st[S_X], st[S_Y], st[S_Z]
        r = math.sqrt(px * px + py * py + pz * pz)
        rd = max(r, 0.3)            # inside 0.3 m the direction fades to the centre of the head
        ux, uy, uz = px / rd, py / rd, pz / rd
        g_dir = 1.0 / max(r, R_MIN)
        # --- mono path: air absorption, occlusion (direct only), write both lines
        a_air = math.exp(-2 * math.pi * 52700.0 / math.sqrt(max(r, R_MIN)) / sr)
        st[S_AIR] = (1 - a_air) * m + a_air * st[S_AIR]
        oc = st[S_PAR]
        a_oc = math.exp(-2 * math.pi * 40000.0 * (600.0 / 40000.0) ** oc / sr)
        st[S_OCC] = (1 - a_oc) * st[S_AIR] + a_oc * st[S_OCC]
        v = st[S_OCC] + AP_G * smear[(cnt - SMEAR_LEN) & mask_s]
        smear[cnt & mask_s] = v
        ap_out = -AP_G * v + smear[(cnt - SMEAR_LEN) & mask_s]
        w_sm = 0.5 * oc
        d_in = ((1 - w_sm) * st[S_OCC] + w_sm * ap_out) * 10 ** (-10.0 * oc / 20.0)
        lineA[cnt & maskA] = d_in
        lineB[cnt & maskA] = m
        # --- read heads: propagation (Doppler) + ITD, ground image
        rgz = max(pz, -EAR_H) + 2 * EAR_H
        rg = math.sqrt(px * px + py * py + rgz * rgz)
        if doppler:
            bt = min(r / C_SOUND * sr, max_d) + D0
            bg = min(rg / C_SOUND * sr, max_d) + D0
        else:
            bt = D0
            bg = D0 + (rg - r) / C_SOUND * sr
        if st[S_INIT] == 0.0:
            st[S_BASE] = bt
            st[S_BASEG] = bg
            st[S_INIT] = 1.0
        else:
            st[S_BASE] = min(max(bt, st[S_BASE] - SLEW), st[S_BASE] + SLEW)
            st[S_BASEG] = min(max(bg, st[S_BASEG] - SLEW), st[S_BASEG] + SLEW)
        base = st[S_BASE]
        lat = math.asin(min(1.0, max(-1.0, ux)))
        itd = HEAD_A / C_SOUND * (lat + math.sin(lat)) * sr
        sC = _cubic(lineA, maskA, cnt - base)
        gnd = _cubic(lineA, maskA, cnt - st[S_BASEG])
        if a_ground > 0:
            st[S_GLP] = (1 - a_ground) * gnd + a_ground * st[S_GLP]
            gnd = st[S_GLP]
        gnd *= g_ground / max(rg, R_MIN)
        eL = 0.0
        eR = 0.0
        if headphones:              # the ear reads (ITD) and per-ear near-field level: the head model's input
            sL = _cubic(lineA, maskA, cnt - (base + 0.5 * itd))
            sR = _cubic(lineA, maskA, cnt - (base - 0.5 * itd))
            rl = math.sqrt((px + HEAD_A) ** 2 + py * py + pz * pz)
            rr = math.sqrt((px - HEAD_A) ** 2 + py * py + pz * pz)
            rc = max(r, R_MIN)
            eL = g_dir * rc / max(rl, R_MIN) * sL + gnd
            eR = g_dir * rc / max(rr, R_MIN) * sR + gnd
        pc = g_dir * sC + gnd
        rear = max(0.0, -uy)
        # --- spread: decorrelated copies of the centre direct
        sp = st[S_PAR + 1]
        for side in range(2):
            u = pc
            for k in range(2):
                j = 2 * side + k
                L = ap[j, (cnt - AP_LEN[j]) & mask_a]
                vv = u + AP_G * L
                ap[j, cnt & mask_a] = vv
                u = -AP_G * vv + L
            dec[side] = u
        # --- head (headphones only): Brown-Duda shadow + rear cue + pinna, or measured HRIRs
        bL = 0.0
        bR = 0.0
        if not headphones:
            pass
        elif use_hrir:
            hh[0, cnt & mask_h] = eL
            hh[1, cnt & mask_h] = eR
            ph = cnt % HRIR_K
            if ph == 0 or ist[2] < 0:
                best = 0
                bd = -2.0
                nrm = math.sqrt(ux * ux + uy * uy + uz * uz)
                qx = 0.0
                qy = 1.0
                qz = 0.0
                if nrm > 1e-6:
                    qx = ux / nrm
                    qy = uy / nrm
                    qz = uz / nrm
                for k in range(hdirs.shape[0]):
                    dd = hdirs[k, 0] * qx + hdirs[k, 1] * qy + hdirs[k, 2] * qz
                    if dd > bd:
                        bd = dd
                        best = k
                ist[3] = ist[2] if ist[2] >= 0 else best
                ist[2] = best
            fx_ = ph / HRIR_K
            hcur, hprv = ist[2], ist[3]
            for e in range(2):
                yc = 0.0
                yp = 0.0
                for j in range(taps):
                    hv = hh[e, (cnt - j) & mask_h]
                    yc += hrir[hcur, e, j] * hv
                    if hprv != hcur:
                        yp += hrir[hprv, e, j] * hv
                if hprv == hcur:
                    yp = yc
                if e == 0:
                    bL = yp + (yc - yp) * fx_
                else:
                    bR = yp + (yc - yp) * fx_
        else:
            for e in range(2):
                x_in = eL if e == 0 else eR
                cos_t = ux if e == 1 else -ux
                al = _alpha(cos_t)
                b0 = (2 * w0 + al * K2) / (2 * w0 + K2)
                b1 = (2 * w0 - al * K2) / (2 * w0 + K2)
                y = b0 * x_in + b1 * st[S_SHX + e] - a1_sh * st[S_SHY + e]
                st[S_SHX + e] = x_in
                st[S_SHY + e] = y
                st[S_REAR + e] = (1 - a_rear) * y + a_rear * st[S_REAR + e]
                y = y - REAR_CUT * rear * (y - st[S_REAR + e])
                pin[e, cnt & mask_p] = y
                if e == 0:
                    bL = y
                else:
                    bR = y
            # pinna echoes: delays move with elevation and front/back (median-plane angle phi, -90 .. 270 deg)
            phi = math.atan2(uz, uy)
            if phi < -0.5 * math.pi:
                phi += 2 * math.pi
            cl = math.cos(0.5 * lat)
            accL = 0.0
            accR = 0.0
            for k in range(5):
                tau = (PINNA_A[k] * cl * math.sin(PINNA_D[k] * (0.5 * math.pi - phi)) + PINNA_B[k]) * sr_k
                accL += PINNA_RHO[k] * _lin(pin[0], mask_p, cnt - tau)
                accR += PINNA_RHO[k] * _lin(pin[1], mask_p, cnt - tau)
            bL += PINNA_AMT * accL
            bR += PINNA_AMT * accR
        # --- spread on the direct sound
        keep = 1.0 - 0.5 * sp
        bL = keep * bL + 0.7 * sp * dec[0]
        bR = keep * bR + 0.7 * sp * dec[1]
        if headphones:
            oL, oR = bL, bR
        else:
            # Speakers: ONE render, the equal-power pan of the centre read (every distance cue, rear darkening,
            # no interaural delay), and ctc sets only how deep a crosstalk canceller runs on it. There is no
            # second render to blend, so nothing can comb at any ctc, and ctc=0 is the plain pan sample for sample.
            # Why not the binaural feed: the exact transaural inverse of a source beyond the speakers drives the
            # far speaker LOUDER than the near one around 0.8-2 kHz (the target's interaural delay is longer than
            # the crosstalk delay, so there the cancelling term adds to the far feed instead of taking away), and
            # a partly cancelled binaural feed sends a low note to the wrong ear. With a pan (spread 0) the target
            # ratio pL/pR = k is real (one signal, 0 <= k <= 1), and with a crosstalk path |h| <= CTC_G < 1 the
            # speakers are S_L/S_R = (k - h) / (1 - k h), whose size is below 1 at every frequency
            # (|1 - k h|^2 - |k - h|^2 = (1 - k^2)(1 - |h|^2) > 0): the near speaker is always the louder one,
            # and the far ear's share falls steadily as ctc rises.
            c = st[S_PAR + 2]
            pp = (min(1.0, max(-1.0, ux)) + 1) * math.pi / 4
            st[S_REAR + 2] = (1 - a_rear) * pc + a_rear * st[S_REAR + 2]
            v = keep * (pc - REAR_CUT * rear * (pc - st[S_REAR + 2])) * math.sqrt(2.0)
            pL = v * math.cos(pp) + 0.7 * sp * dec[0]
            pR = v * math.sin(pp) + 0.7 * sp * dec[1]
            # recursive crosstalk canceller (Atal-Schroeder form, the exact inverse of the symmetric plant for
            # speakers at +-CTC_SPAN_DEG): each feed subtracts the OTHER feed as it reaches the far ear relative to
            # the near one, i.e. delayed by the crosstalk path (dx samples) and shaped by the contra/ipsi head
            # shadow ratio, high-passed at CTC_HP_HZ, with its gain bounded by CTC_G. ctc scales that gain only.
            xr = _lin(xh[1], mask_x, cnt - dx)
            xl = _lin(xh[0], mask_x, cnt - dx)
            fL = pL - c * CTC_G * xr
            fR = pR - c * CTC_G * xl
            for e in range(2):
                f = fL if e == 0 else fR
                hp = hb0 * (f - st[S_CHP + 2 * e]) - ha1 * st[S_CHP + 2 * e + 1]
                st[S_CHP + 2 * e] = f
                st[S_CHP + 2 * e + 1] = hp
                y = cb0 * hp + cb1 * st[S_CSH + 2 * e] - ca1 * st[S_CSH + 2 * e + 1]
                st[S_CSH + 2 * e] = hp
                st[S_CSH + 2 * e + 1] = y
                xh[e, cnt & mask_x] = y
            oL = fL
            oR = fR
        # --- the effect's own room: early reflections + FDN tail, fed un-occluded, level falls as 1/sqrt(r)
        wg = st[S_PAR + 3] * WET_NORM / math.sqrt(max(r, 1.0))
        wL = 0.0
        wR = 0.0
        ers = 0.0
        for k in range(len(er_off)):
            e_ = _lin(lineB, maskA, cnt - base - er_off[k]) * ER_G[k] * wg
            wL += ER_PAN[k, 0] * e_
            wR += ER_PAN[k, 1] * e_
            ers += e_
        st[S_ERLP] = (1 - a_er) * wL + a_er * st[S_ERLP]
        st[S_ERLP + 1] = (1 - a_er) * wR + a_er * st[S_ERLP + 1]
        f_in = wg * _lin(lineB, maskA, cnt - base) + 0.3 * ers
        for k in range(8):
            tmp[k] = fdn[k, (cnt - int(fdn_len[k])) & mask_f]
        # fast Walsh-Hadamard, normalised
        h_ = 1
        while h_ < 8:
            for a in range(0, 8, 2 * h_):
                for b in range(a, a + h_):
                    u0 = tmp[b]
                    u1 = tmp[b + h_]
                    tmp[b] = u0 + u1
                    tmp[b + h_] = u0 - u1
            h_ *= 2
        tL = 0.0
        tR = 0.0
        for k in range(8):
            o = fdn[k, (cnt - int(fdn_len[k])) & mask_f]
            tL += FDN_SIGN_L[k] * o
            tR += FDN_SIGN_R[k] * o
            st[S_FDNLP + k] = (1 - FDN_DAMP) * tmp[k] * inv8 + FDN_DAMP * st[S_FDNLP + k]
            fdn[k, cnt & mask_f] = fdn_g[k] * st[S_FDNLP + k] + f_in
        out[0, i] = oL + st[S_ERLP] + 0.25 * tL
        out[1, i] = oR + st[S_ERLP + 1] + 0.25 * tR
        ist[0] = cnt + 1


def _pow2(n):
    return 1 << int(math.ceil(math.log2(max(int(n), 2))))


class Spatializer:
    """One spatial effect's state; process() is called on consecutive blocks (the studio sends the whole window
    once). t0: the trajectory time (in samples) of the block's first sample."""

    def __init__(self, p, sr):
        self.p, self.sr = p, sr
        self.traj = parse_trajectory(p.get('trajectory'))
        self.st = np.zeros(N_STATE)
        self.ist = np.array([0, 0, -1, -1], dtype=np.int64)
        NB = _pow2(MAX_DELAY_S * sr + 0.05 * 2 * sr + 64)
        self.lineA, self.lineB = np.zeros(NB), np.zeros(NB)
        self.smear = np.zeros(_pow2(SMEAR_LEN + 2))
        self.pin = np.zeros((2, 32))
        self.xh = np.zeros((2, 32))
        scale = (0.5 + 1.5 * float(p['size'])) * sr / 44100.0
        self.er_off = ER_MS / 1000.0 * sr * scale
        self.fdn_len = np.round(FDN_LEN * scale)
        rt60 = 0.4 + 3.6 * float(p['size']) ** 2
        self.fdn_g = 10 ** (-3.0 * self.fdn_len / (sr * rt60))
        self.fdn = np.zeros((8, _pow2(self.fdn_len.max() + 2)))
        self.ap = np.zeros((4, _pow2(AP_LEN.max() + 2)))
        g, hz = GROUND[p['ground']]
        self.g_ground, self.a_ground = g, (math.exp(-2 * math.pi * hz / sr) if hz else 0.0)
        h = _hrtf(p['hrtf'], sr) if p.get('hrtf', 'model') != 'model' else None
        if h is None:
            self.use_hrir, self.hdirs, self.hrir = 0, np.zeros((1, 3)), np.zeros((1, 2, 1))
        else:
            self.use_hrir, (self.hdirs, self.hrir) = 1, h
        self.hh = np.zeros((2, _pow2(self.hrir.shape[2] + 1)))

    def snap(self):
        """Next sample takes the position as it is, without gliding (after a gap in the blocks)."""
        self.st[S_INIT] = 0.0

    def process(self, x, P, t0):
        n = x.shape[1]
        mono = np.ascontiguousarray(x.mean(0), dtype=np.float64)
        cur = np.empty((8, n))          # x, y, z, yaw (rad), occlusion, ctc, room, spread: one row per sample

        def put(row, v):
            v = np.asarray(v, dtype=np.float64)
            cur[row] = v if v.ndim == 0 else v[:n]
        if self.traj is not None:
            cur[:3] = self.traj.eval((t0 + np.arange(n)) / self.sr)
        else:
            for row, name in enumerate(('x', 'y', 'z')):
                put(row, P(name))
        for row, name in enumerate(('listener_yaw', 'occlusion', 'ctc', 'room', 'spread'), 3):
            put(row, P(name))
        cur[3] *= np.pi / 180.0
        out = np.empty((2, n))
        p = self.p
        _kernel(mono, cur, float(self.sr), bool(p['doppler']), p['out'] == 'headphones', self.g_ground,
                self.a_ground, self.er_off, self.fdn_len, self.fdn_g, self.st, self.ist, self.lineA, self.lineB,
                self.smear, self.pin, self.xh, self.fdn, self.ap, self.use_hrir, self.hdirs, self.hrir, self.hh, out)
        return out


def spatial(x, p, sr, P, offset_samples=0):
    """Studio: the whole window at once. Song time 0 is buffer sample offset_samples (the window rule)."""
    return Spatializer(p, sr).process(x, P, -int(offset_samples))


APPLY = {"spatial": spatial}
