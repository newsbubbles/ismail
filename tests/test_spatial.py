"""The spatial effect: registration, trajectories (parse, evaluation, errors that say what to do), the optional
measured HRTF loader on a synthetic SOFA-like set (nothing is downloaded), and measurements on the output: each
cue points the right way and has about the right size, it does not click, and live equals studio."""
import inspect
import math

import numpy as np
import pytest
from scipy.signal import butter, lfilter, sosfiltfilt

from ismail import fx as F
from ismail import render
from ismail import spatial as S
from ismail.dsp import SR
from ismail.live import fx_blocks as L

RNG = np.random.default_rng(7)
NOISE = RNG.standard_normal(SR // 2)
XN = np.stack([NOISE, NOISE]) * 0.1


def run(sig, curves=None, offset=0, **kw):
    p = F.normalize(dict(type='spatial', **kw))
    curves = curves or {}
    return S.spatial(sig, p, SR, lambda k: curves.get(k, p[k]), offset)


def db(a):
    return 10 * np.log10(np.mean(np.asarray(a) ** 2) + 1e-30)


# ------------------------------------------------------------------ registration

def test_spatial_is_registered_everywhere():
    from ismail import api
    assert 'spatial' in F.FX_DEFAULTS and 'spatial' in F.AUTOMATABLE and 'spatial' in L.PROCS
    assert set(S.AUTOMATABLE['spatial']) <= set(S.DEFAULTS['spatial'])
    h = api.fx_help(type='spatial')
    assert 'metres' in h and 'trajectory' in h and 'doppler' in h


def test_code_hash_covers_rig_and_spatial():
    """A change to rig.py or spatial.py must invalidate cached track renders."""
    src = inspect.getsource(render._code_hash)
    assert "'rig.py'" in src and "'spatial.py'" in src


def test_defaults_build_and_run():
    y = run(XN[:, :2000])
    assert y.shape == (2, 2000) and np.all(np.isfinite(y))


# ------------------------------------------------------------------ trajectories

def test_point_forms_and_angle_convention():
    assert S.point([1, 2, 3]) == (1.0, 2.0, 3.0)
    assert S.point({'x': 1, 'z': 2}) == (1.0, 0.0, 2.0)
    x, y, z = S.point({'az': 90, 'el': 0, 'dist': 2})          # azimuth positive = right
    assert x == pytest.approx(2) and abs(y) < 1e-12 and abs(z) < 1e-12
    x, y, z = S.point({'az': 180, 'dist': 3})                  # behind
    assert y == pytest.approx(-3) and abs(x) < 1e-9
    x, y, z = S.point({'az': 0, 'el': 90, 'dist': 1})          # above
    assert z == pytest.approx(1)


def test_orbit_is_analytic_and_cw_goes_front_right_back():
    tr = S.parse_trajectory({'orbit': {'radius': 3, 'period_s': 8}})
    x, y, z = tr.eval(np.array([0.0, 2.0, 4.0, 6.0]))
    assert np.allclose(x, [0, 3, 0, -3], atol=1e-9) and np.allclose(y, [3, 0, -3, 0], atol=1e-9)
    assert np.allclose(np.hypot(*tr.eval(np.linspace(0, 20, 999))[:2]), 3)
    x, _, _ = S.parse_trajectory({'orbit': {'radius': 3, 'period_s': 8, 'direction': 'ccw'}}).eval(np.array([2.0]))
    assert x[0] == pytest.approx(-3)
    _, _, z = S.parse_trajectory({'orbit': {'radius': 2, 'period_s': 4, 'elevation': 30,
                                            'centre': [0, 0, 1]}}).eval(np.array([1.0]))
    assert z[0] == pytest.approx(1 + 2 * math.sin(math.radians(30)))


def test_line_flyby_hold_and_loop():
    tr = S.parse_trajectory({'line': {'from': [-10, 5, 0], 'to': [10, 5, 0], 'duration_s': 4}, 'start_s': 1})
    x, y, _ = tr.eval(np.array([0.0, 1.0, 3.0, 5.0, 9.0]))
    assert np.allclose(x, [-10, -10, 0, 10, 10]) and np.allclose(y, 5)    # holds its start, then its end
    tr = S.parse_trajectory({'line': {'from': [-10, 5, 0], 'to': [10, 5, 0], 'duration_s': 4}, 'loop': True})
    assert tr.eval(np.array([5.0]))[0][0] == pytest.approx(-5)
    fb = S.parse_trajectory({'flyby': {'from': [-20, 0, 0], 'to': [20, 0, 0], 'speed_mps': 10}})
    assert fb.duration == pytest.approx(4)


def test_keys_linear_smooth_angles_and_loop():
    spec = {'keys': [[0, -2, 1, 0], [2, {'az': 90, 'el': 0, 'dist': 2}], {'t': 4, 'x': -2, 'y': 1, 'z': 0}]}
    tr = S.parse_trajectory(spec)
    x, y, _ = tr.eval(np.array([0.0, 1.0, 2.0, 10.0]))
    assert np.allclose(x, [-2, 0, 2, -2]) and np.allclose(y, [1, 0.5, 0, 1], atol=1e-9)
    sm = S.parse_trajectory(dict(spec, interp='smooth'))
    xs, _, _ = sm.eval(np.array([0.0, 2.0, 4.0]))
    assert np.allclose(xs, [-2, 2, -2])                      # passes through every key
    lo = S.parse_trajectory(dict(spec, loop=True))
    assert np.allclose(np.array(lo.eval(np.array([5.0]))), np.array(tr.eval(np.array([1.0]))))


def test_evaluation_does_not_depend_on_block_cuts():
    tr = S.parse_trajectory({'keys': [[0, -2, 1, 0], [0.3, 2, 2, 1], [0.7, 0, -3, 0]], 'interp': 'smooth',
                             'loop': True})
    t = np.arange(40000) / SR
    whole = np.array(tr.eval(t))
    parts = np.concatenate([np.array(tr.eval(t[a:a + 777])) for a in range(0, len(t), 777)], axis=1)
    assert np.array_equal(whole, parts)


@pytest.mark.parametrize('spec, says', [
    ('orbit', 'a dict such as'),
    ({'orbit': {'radius': 3, 'period_s': 8}, 'line': {}}, 'exactly one of'),
    ({'spiral': {}}, 'exactly one of'),
    ({'orbit': {'period_s': 8}}, 'needs radius'),
    ({'orbit': {'radius': -1, 'period_s': 8}}, 'must be above 0'),
    ({'orbit': {'radius': 1, 'period_s': 8, 'speed': 2}}, "unknown keys ['speed']"),
    ({'orbit': {'radius': 1, 'period_s': 8, 'direction': 'left'}}, "'cw'"),
    ({'keys': [[0, 0, 1, 0], [0, 1, 1, 0]]}, 'times must rise'),
    ({'keys': [[0, 0, 1]]}, '[t_s, x, y, z]'),
    ({'keys': [[0, 'far', 1, 0]]}, 'needs a number'),
    ({'keys': []}, 'a list of keys'),
    ({'keys': [[0, 0, 1, 0]], 'interp': 'cubic'}, "'linear' or 'smooth'"),
    ({'line': {'from': [0, 1, 0], 'to': [1, 1, 0]}}, 'duration_s'),
    ({'line': {'from': [0, 1], 'to': [1, 1, 0], 'duration_s': 2}}, '[x, y, z]'),
    ({'line': {'from': {'az': 10, 'range': 3}, 'to': [1, 1, 0], 'duration_s': 2}}, "unknown keys ['range']"),
    ({'flyby': {'from': [0, 1, 0], 'to': [0, 1, 0], 'speed_mps': 3}}, 'same point'),
    ({'orbit': {'radius': 1, 'period_s': 2}, 'loop': 'yes'}, 'True or False'),
])
def test_trajectory_errors_say_what_to_do(spec, says):
    with pytest.raises(S.SpatialError) as e:
        S.parse_trajectory(spec)
    assert says in str(e.value)
    with pytest.raises(F.FxError) as e:                     # and the effect's own validation passes them on
        F.normalize({'type': 'spatial', 'trajectory': spec})
    assert says in str(e.value)


@pytest.mark.parametrize('bad, says', [
    ({'out': 'surround'}, "'speakers' | 'headphones'"),
    ({'ground': 'grass'}, "'soft' | 'hard'"),
    ({'ctc': 2}, 'between 0 and 1'),
    ({'occlusion': -0.1}, 'between 0 and 1'),
    ({'doppler': 2}, '1 (on) or 0'),
    ({'x': 'left'}, 'automation_set'),
    ({'height': 2}, 'unknown spatial params'),
])
def test_param_errors(bad, says):
    with pytest.raises(F.FxError) as e:
        F.normalize(dict(type='spatial', **bad))
    assert says in str(e.value)


# ------------------------------------------------------------------ measured HRTF (synthetic set, no download)

def _fake_sofa(fs=44100.0):
    """A SOFA-like mapping: 24 directions on the horizon plus up and down; each IR is louder in the ear facing it
    (SOFA azimuth is positive to the LEFT)."""
    az = np.concatenate([np.arange(0, 360, 15), [0, 0]]).astype(float)
    el = np.concatenate([np.zeros(24), [90, -90]])
    ir = np.zeros((26, 2, 256))
    for m in range(26):
        left = math.sin(math.radians(az[m])) * math.cos(math.radians(el[m]))   # +1 hard left
        ir[m, 0, 10] = 1.0 + 0.8 * left
        ir[m, 1, 10] = 1.0 - 0.8 * left
        ir[m, :, 11:40] = 0.05 * RNG.standard_normal((2, 29)) * np.exp(-np.arange(29) / 6)
    return {'Data.IR': ir, 'SourcePosition': np.stack([az, el, np.full(26, 1.5)], 1),
            'Data.SamplingRate': np.array([fs])}


def test_convert_sofa_turns_left_azimuth_into_our_frame():
    dirs, hrir = S.convert_sofa(_fake_sofa(), SR)
    assert hrir.shape == (26, 2, S.HRIR_TAPS) and np.allclose(np.linalg.norm(dirs, axis=1), 1)
    m = 6                                                # SOFA az 90 = left
    assert dirs[m] == pytest.approx([-1, 0, 0], abs=1e-9)
    assert np.sum(hrir[m, 0] ** 2) > 4 * np.sum(hrir[m, 1] ** 2)
    assert dirs[24] == pytest.approx([0, 0, 1], abs=1e-9)
    with pytest.raises(S.SpatialError, match='48000 Hz'):
        S.convert_sofa(_fake_sofa(48000.0), SR)
    with pytest.raises(S.SpatialError, match=r'Data.IR \(M, 2, N\)'):
        S.convert_sofa({'Data.IR': np.zeros((3, 256)), 'SourcePosition': np.zeros((3, 3)),
                        'Data.SamplingRate': [SR]}, SR)


def test_measured_set_from_cache_places_the_source(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_HRTF', str(tmp_path))
    S._HRTF.clear()
    dirs, hrir = S.convert_sofa(_fake_sofa(), SR)
    np.savez(tmp_path / f'sadie_d2_{SR}.npz', dirs=dirs, hrir=hrir)
    try:
        y = run(XN, x=2.0, y=0.0, room=0.0, out='headphones', hrtf='sadie_d2')
        assert np.all(np.isfinite(y)) and db(y[1]) - db(y[0]) > 6
        t = np.arange(SR // 2) / SR
        s = np.sin(2 * np.pi * 150 * t) * 0.3
        moving = run(np.stack([s, s]), {'x': np.linspace(-3, 3, len(t))}, room=0.0, out='headphones',
                     hrtf='sadie_d2')
        d = np.abs(np.diff(moving[:, 2000:], axis=1)).max()
        assert d < 1.6 * (2 * np.pi * 150 / SR) * np.abs(moving[:, 2000:]).max()   # the direction switch fades
    finally:
        S._HRTF.clear()


def test_missing_measured_set_says_how_to_get_it(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_HRTF', str(tmp_path / 'hrtf'))
    monkeypatch.setenv('ISMAIL_SAMPLES', str(tmp_path / 'samples'))
    monkeypatch.delenv('SADIE_D2_HRTF', raising=False)
    with pytest.raises(F.FxError) as e:
        F.normalize({'type': 'spatial', 'hrtf': 'sadie_d2'})
    assert "samples_fetch('sadie_d2')" in str(e.value) and "hrtf='model'" in str(e.value)


# ------------------------------------------------------------------ measurements on the output

@pytest.mark.parametrize('out', ['headphones', 'speakers'])
def test_a_source_on_the_right_is_louder_and_earlier_on_the_right(out):
    y = run(XN, x=2.0, y=0.0, room=0.0, out=out)
    ild = db(y[1]) - db(y[0])
    c = np.correlate(y[0, 2000:8000], y[1, 2000:8000], 'full')
    lag = np.argmax(c) - 5999                              # > 0: the left channel lags
    assert ild > (10 if out == 'headphones' else 6)
    assert 0.5e-3 < lag / SR < 0.8e-3                       # Woodworth, 8.75 cm head: ~0.66 ms at 90 degrees
    yl = run(XN, x=-2.0, y=0.0, room=0.0, out=out)
    assert db(yl[0]) - db(yl[1]) == pytest.approx(ild, abs=0.5)


def _bands(y):
    Y = np.abs(np.fft.rfft(y.sum(0) * np.hanning(y.shape[1]))) ** 2
    f = np.fft.rfftfreq(y.shape[1], 1 / SR)
    edges = np.geomspace(200, 16000, 25)
    return np.array([10 * np.log10(Y[(f >= a) & (f < b)].mean()) for a, b in zip(edges[:-1], edges[1:])]), edges


@pytest.mark.parametrize('out', ['headphones', 'speakers'])
def test_front_and_back_differ_spectrally(out):
    front, e = _bands(run(XN, x=0.0, y=2.0, room=0.0, out=out))
    back, _ = _bands(run(XN, x=0.0, y=-2.0, room=0.0, out=out))
    hi = e[:-1] > 4000
    assert np.mean((front - back)[hi]) > 3                 # behind is darker (pinna and rear shelf)
    assert np.max(np.abs(front - back)) > 4


def test_doubling_distance_drops_direct_6db_and_raises_wet_dry():
    imp = np.zeros((2, SR))
    imp[:, 100] = 1
    sos = butter(4, 4000, fs=SR, output='sos')            # the cue, not the interpolator's top octave
    res = {}
    for r in (2.0, 4.0):
        ir = run(imp, y=r, room=0.4, out='headphones')
        dry = run(imp, y=r, room=0.0, out='headphones')
        pk = int(np.argmax(np.abs(dry[0])))
        lp = sosfiltfilt(sos, dry, axis=1)
        direct = np.sum(lp[:, pk - 40:pk + 60] ** 2)
        wet = np.sum((ir - dry) ** 2)
        res[r] = (10 * np.log10(direct), 10 * np.log10(wet / np.sum(dry[:, pk - 40:pk + 60] ** 2)))
    assert res[2][0] - res[4][0] == pytest.approx(6.0, abs=1.0)
    assert 2.0 < res[4][1] - res[2][1] < 4.5               # reverb level falls as 1/sqrt(r): wet/dry +3 dB


def test_doppler_flyby_shifts_a_tone_up_then_down():
    t = np.arange(int(SR * 12)) / SR
    tone = np.sin(2 * np.pi * 1000 * t) * 0.2
    v = 20.0
    y = run(np.stack([tone, tone]), room=0.0, out='headphones',
            trajectory={'flyby': {'from': [-120, 3, 0], 'to': [120, 3, 0], 'speed_mps': v}})

    def freq(seg):
        n = len(seg)
        Y = np.abs(np.fft.rfft(seg * np.hanning(n), 8 * n))
        return np.argmax(Y) * SR / (8 * n)
    up, down = freq(y.sum(0)[SR:2 * SR]), freq(y.sum(0)[10 * SR:11 * SR])
    assert up / down == pytest.approx((343 + v) / (343 - v), rel=0.005)
    assert up > 1050 and down < 950
    still = run(np.stack([tone, tone]), room=0.0, out='headphones', doppler=0,
                trajectory={'flyby': {'from': [-120, 3, 0], 'to': [120, 3, 0], 'speed_mps': v}})
    assert freq(still.sum(0)[SR:2 * SR]) == pytest.approx(1000, abs=3)


def _speakers_to_ears(y, span=30.0):
    """The listening room the canceller assumes: speakers at +-span, a spherical head (Brown-Duda shadow)."""
    w0, K, th = S.C_SOUND / S.HEAD_A, 2 * SR, math.radians(span)

    def shadow(cos_t, x):
        al = 1.05 + 0.95 * np.cos(np.arccos(cos_t) / (5 * np.pi / 6) * np.pi)
        return lfilter([(2 * w0 + al * K) / (2 * w0 + K), (2 * w0 - al * K) / (2 * w0 + K)],
                       [1, (2 * w0 - K) / (2 * w0 + K)], x)
    d = int(round(S.HEAD_A / S.C_SOUND * (th + math.sin(th)) * SR))

    def late(x):
        return np.concatenate([np.zeros(d), x[:-d]])
    return np.stack([shadow(math.sin(th), y[0]) + late(shadow(-math.sin(th), y[1])),
                     shadow(math.sin(th), y[1]) + late(shadow(-math.sin(th), y[0]))])


def test_crosstalk_cancellation_restores_the_binaural_level_difference():
    sos = butter(4, [300, 3000], 'band', fs=SR, output='sos')
    band_ild = lambda e: db(sosfiltfilt(sos, e[1])) - db(sosfiltfilt(sos, e[0]))   # noqa: E731
    target = band_ild(run(XN, x=3.0, y=0.0, room=0.0, out='headphones'))
    plain = band_ild(_speakers_to_ears(run(XN, x=3.0, y=0.0, room=0.0, ctc=0.0)))
    full = band_ild(_speakers_to_ears(run(XN, x=3.0, y=0.0, room=0.0, ctc=1.0)))
    assert full > plain + 2.5                              # less sound of the right speaker reaches the left ear
    assert full == pytest.approx(target, abs=1.5)


def test_ctc_zero_is_plain_equal_power_panning():
    y = run(XN, x=1.0, y=1.0, room=0.0, ctc=0.0)
    pp = (1 / math.sqrt(2) + 1) * math.pi / 4
    assert np.allclose(y[0] * math.sin(pp), y[1] * math.cos(pp), atol=1e-12)


def test_occlusion_darkens_the_direct_sound_but_not_the_reverb():
    open_, e = _bands(run(XN, y=6.0, room=0.0, out='headphones'))
    hid, _ = _bands(run(XN, y=6.0, room=0.0, out='headphones', occlusion=1.0))
    assert np.mean((open_ - hid)[e[:-1] > 4000]) > np.mean((open_ - hid)[e[:-1] < 500]) + 10
    wet_open = run(XN, y=6.0, out='headphones') - run(XN, y=6.0, room=0.0, out='headphones')
    wet_hid = run(XN, y=6.0, out='headphones', occlusion=1.0) - run(XN, y=6.0, room=0.0, out='headphones',
                                                                     occlusion=1.0)
    assert np.allclose(wet_open, wet_hid)


@pytest.mark.parametrize('out', ['speakers', 'headphones'])
def test_a_jump_in_position_does_not_click(out):
    t = np.arange(SR) / SR
    s = np.sin(2 * np.pi * 200 * t) * 0.3
    xc = np.where(t < 0.5, -3.0, 3.0)
    y = run(np.stack([s, s]), {'x': xc, 'occlusion': np.where(t < 0.5, 0.0, 1.0)}, room=0.0, out=out)
    w = int(0.01 * SR)
    for a in range(int(0.45 * SR), int(0.7 * SR), w):
        seg = y[:, a:a + w]
        amp = np.abs(y[:, max(0, a - w):a + 2 * w]).max()
        assert np.abs(np.diff(seg, axis=1)).max() <= 1.6 * (2 * np.pi * 200 / SR) * amp + 1e-6


# ------------------------------------------------------------------ live

class _Ctx:
    sr, bpm, track = SR, 120.0, 'pad'

    def __init__(self, offset):
        self.offset_samples = offset

    def param(self, idx, name, default):
        return default


def test_studio_and_offline_live_agree_on_song_time():
    fx = F.normalize({'type': 'spatial', 'trajectory': {'orbit': {'radius': 2, 'period_s': 0.4}}, 'out': 'headphones'})
    for off in (0, 3000, -5000):
        a = F.apply_fx(XN.copy(), fx, _Ctx(off), 0)
        b = L.apply_fx(XN.copy(), fx, _Ctx(off), 0)
        assert np.max(np.abs(a - b)) < 1e-12


def test_live_trajectory_starts_when_the_effect_starts():
    """Live, the first block the processor sees is trajectory time 0, whatever the engine's clock says."""
    fx = F.normalize({'type': 'spatial', 'trajectory': {'line': {'from': [-5, 1, 0], 'to': [5, 1, 0],
                                                                 'duration_s': 0.3}}})
    p = L.make(fx, L.Env(SR, 120.0, 1.0, 0))
    out, pos = [], 7 * SR
    for n in (256, 512, 1000) * 8:
        b = L.Block()
        b.pos, b.n = pos, n
        out.append(p.process(np.ascontiguousarray(XN[:, pos - 7 * SR:pos - 7 * SR + n]), b))
        pos += n
    live = np.concatenate(out, axis=1)
    studio = run(XN[:, :live.shape[1]], trajectory=fx['trajectory'])
    assert np.max(np.abs(live - studio)) < 1e-12


def test_a_gap_in_the_blocks_takes_the_new_position():
    fx = F.normalize({'type': 'spatial', 'trajectory': {'orbit': {'radius': 3, 'period_s': 2}}})
    p = L.make(fx, L.Env(SR, 120.0, 1.0, 0))
    for pos in (0, 512, 1024, 50000, 50512):
        b = L.Block()
        b.pos, b.n = pos, 512
        y = p.process(np.ascontiguousarray(XN[:, :512]), b)
        assert np.all(np.isfinite(y))
    assert p.sp.st[S.S_BASE] == pytest.approx(3 / 343 * SR + 3 + math.ceil(0.5 * S.HEAD_A / S.C_SOUND
                                                                          * (math.pi / 2 + 1) * SR), abs=1)
