"""The zoom and the zoom diff (ledger:M193, moved from vox's handoff section 7). Synthetic audio only.

Parity with vox's own spectrogram_diff, checked at the move on the fixture seeds 0-9, every ref as the anchor, the
test clip and the clip without the box: every map and every spot the same, cell for cell (the table is in the pull
request). With ISMAIL_VOX_WORK set to vox's work folder, test_matches_the_vox_reference_cell_for_cell checks it again.
The numbers vox measured at build (10-08) and reran (10-15, seed 0) are pinned here: the box found at the strokes and
plumes scales, region_measure +5.9 dB for the 6 dB box, and at seed 4 one darker strokes cell beside the box (3-6 kHz,
in its quarter), the same stray as vox's, not a cleaner result. ISMAIL_ZOOM_ALL_SEEDS=1 runs all ten seeds (about
half a minute each)."""
import json
import os
import sys

import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail import api
from ismail import zoom as Z

OPS = api.OPS
SEEDS_ALL = bool(os.environ.get('ISMAIL_ZOOM_ALL_SEEDS'))


def _run(inp, clip='y', anchor=0, **kw):
    return Z.spectrogram_diff(inp[clip], inp['sr'], inp['t0'], inp['t1'], refs=inp['refs'], anchor=anchor,
                              word='fixture', **kw)


def _in_box(s, exp, tol):
    B = exp['box']
    return (B['t0_s'] - tol['box_margin_s'] <= s['t_peak_s'] <= B['t1_s'] + tol['box_margin_s'] and
            B['f_lo_hz'] - tol['box_margin_hz'] <= s['f_peak_hz'] <= B['f_hi_hz'] + tol['box_margin_hz'] and
            s['sign'] == B['sign'])


def cell_rates(diffs):
    """{(scale, sign, quarter, band_lo, band_hi): margin} over runs of one clip with every ref as the anchor: the share
    of runs with a spot in the cell minus the share of control pairs with one (vox's spot_clusters for one group,
    which stays in vox; only the fixture's stray check needs it). Cells the runs never hit are left out."""
    hit, chit, n_c = {}, {}, {}
    for D in diffs:
        for sc, S in D['scales'].items():
            for x in S['spots']:
                hit.setdefault((sc,) + Z._cell(x), set()).add(D['anchor_id'])
            for kid, cs in S['control_spots'].items():
                n_c.setdefault(sc, set()).add((D['anchor_id'], kid))
                for x in cs:
                    chit.setdefault((sc,) + Z._cell(x), set()).add((D['anchor_id'], kid))
    return {k: len(v) / len(diffs) - len(chit.get(k, ())) / max(len(n_c.get(k[0], ())), 1) for k, v in hit.items()}


def strays(inp, exp, tol):
    """Cells outside the box whose margin rose by stray_margin or more over the same clip without the box (vox's
    parity check), and the best margin in the box at the strokes and plumes scales."""
    n = len(inp['refs'])
    with_box = cell_rates([_run(inp, 'y', a) for a in range(n)])
    without = cell_rates([_run(inp, 'after', a) for a in range(n)])
    B, pad = exp['box'], Z.DEFAULTS['pad_s']
    a, b = inp['t0'] - pad, inp['t1'] + pad
    q0, q1 = int((B['t0_s'] - a) / (b - a) * 4) + 1, int((B['t1_s'] - a) / (b - a) * 4) + 1

    def in_box(k):
        sc, sign, q, lo, hi = k
        return q0 <= q <= q1 and lo < B['f_hi_hz'] and hi > B['f_lo_hz'] and sign == B['sign']
    box = max((m for k, m in with_box.items() if in_box(k) and k[0] in exp['scales_must_find']), default=0.0)
    out = sorted(k for k, m in with_box.items() if not in_box(k) and m - without.get(k, 0.0) >= tol['stray_margin'])
    return out, box, (q0, q1)


def test_seed_0_reproduces_the_vox_numbers():
    """vox's rerun on 10-15 (seed 0): box z 8.5 at strokes, 6.3 at plumes, region_measure +5.91 dB."""
    inp, exp, tol = Z.parity_fixture(0)
    D = _run(inp)
    found = {sc: [s for s in D['scales'][sc]['spots'] if _in_box(s, exp, tol)] for sc in exp['scales_must_find']}
    assert round(found['strokes'][0]['peak_z_spread'], 1) == 8.5 and round(found['plumes'][0]['peak_z_spread'], 1) == 6.3
    assert all(s[0]['control_cell_rate'] == 0.0 and s[0]['beyond_control'] for s in found.values())
    B = exp['box']
    box = (B['t0_s'], B['t1_s'], B['f_lo_hz'], B['f_hi_hz'])
    gain = (Z.region_measure(inp['y'], inp['sr'], *box)['box_power_db'] -
            Z.region_measure(inp['after'], inp['sr'], *box)['box_power_db'])
    assert round(gain, 2) == 5.91


@pytest.mark.parametrize('seed', range(10) if SEEDS_ALL else [0, 4])
def test_the_box_is_found_and_only_seed_4_has_a_stray_cell(seed):
    inp, exp, tol = Z.parity_fixture(seed)
    D = _run(inp)
    for sc in exp['scales_must_find']:
        assert any(_in_box(s, exp, tol) for s in D['scales'][sc]['spots']), (seed, sc)
    out, box, (q0, q1) = strays(inp, exp, tol)
    assert box >= tol['stray_margin'], (seed, box)
    if seed == 4:     # vox: one darker strokes cell beside the box, 3-6 kHz, in its quarter
        assert len(out) == 1, out
        sc, sign, q, lo, hi = out[0]
        assert (sc, sign, lo, hi) == ('strokes', 'darker', 3000.0, 6000.0) and q0 <= q <= q1, out
    else:
        assert out == [], (seed, out)


def test_matches_the_vox_reference_cell_for_cell():
    work = os.environ.get('ISMAIL_VOX_WORK')
    if not work or not os.path.isfile(os.path.join(work, 'spectrogram_diff.py')):
        pytest.skip('ISMAIL_VOX_WORK is not set to a folder with the vox reference (spectrogram_diff.py)')
    sys.path.insert(0, work)
    import spectrogram_diff as V
    for seed in (0, 4):
        inp, _, _ = Z.parity_fixture(seed)
        vi, _, _ = V.parity_fixture(seed)
        assert np.array_equal(inp['y'], vi['y']) and all(np.array_equal(a, b) for a, b in zip(inp['refs'], vi['refs']))
        for anchor in (0, 3):
            m = _run(inp, anchor=anchor)
            v = V.spectrogram_diff(inp['y'], inp['sr'], inp['t0'], inp['t1'], refs=inp['refs'], anchor=anchor,
                                   word='fixture')
            for sc in Z.SCALES:
                M, W = m['scales'][sc], v['scales'][sc]
                assert np.allclose(M['z_spread'], W['z_spread'], atol=1e-9, rtol=0)
                assert all(np.allclose(M['control_z_spread'][k], W['control_z_spread'][k], atol=1e-9, rtol=0)
                           for k in W['control_z_spread'])
                assert [Z._cell(s) for s in M['spots']] == [Z._cell(s) for s in W['spots']]
                assert np.allclose([s['peak_z_spread'] for s in M['spots']], [s['peak_z_spread'] for s in W['spots']])


def test_too_few_takes_refuse_and_say_what_to_do_next(tmp_path):
    inp, _, _ = Z.parity_fixture(1, n_refs=4)
    with pytest.raises(Z.ZoomError, match='at least 3 takes.*Record or cut more takes'):
        Z.spectrogram_diff(inp['y'], inp['sr'], inp['t0'], inp['t1'], refs=inp['refs'][:2])
    D = Z.spectrogram_diff(inp['y'], inp['sr'], inp['t0'], inp['t1'], refs=inp['refs'][:3])
    assert D['n_controls'] == 0 and all(s['control_cell_rate'] is None for S in D['scales'].values() for s in S['spots'])
    proj = _project(tmp_path, inp, n=3)
    with pytest.raises(api.OpError, match='at least 4 takes.*own-source control.*then call again'):
        OPS['sound_zoom_diff'](proj, 'test.wav', [f'take{i}.wav' for i in range(3)], seconds=[0.1, 0.7])


def test_power_not_mean_of_db():
    """A box half at +12 dB and half at the floor: on power it reads near +9 dB, a mean of dB would say +6."""
    sr, rng = 44100, np.random.default_rng(3)
    y = 0.01 * rng.standard_normal(sr)
    y[:sr // 2] *= 4.0                                          # +12 dB over the first half
    r = Z.region_measure(y, sr, 0.0, 1.0, 1000, 8000, 1000, 8000)
    half = Z.region_measure(y, sr, 0.5, 1.0, 1000, 8000)['box_power_db']
    assert abs(r['box_power_db'] - half - 10 * np.log10((16 + 1) / 2)) < 0.3


def test_debias_absorbs_a_band_wide_deficit_and_debias_false_shows_it():
    """A take with a dull top end over the whole word (-6 dB above 4.5 kHz, ramping in from 2 kHz): the de-bias takes
    it as the band offsets, so no darker spot covers the band; with debias=False it is one spot over thousands of
    cells; region_measure reads the level."""
    from scipy.signal import istft, stft
    inp, _, _ = Z.parity_fixture(2)
    ff, _, S = stft(inp['after'], inp['sr'], nperseg=512)
    S *= 10 ** (np.interp(ff, [0, 2000, 4500, ff[-1]], [0, 0, -6, -6]) / 20)[:, None]
    dull = istft(S, inp['sr'], nperseg=512)[1][:len(inp['after'])]
    inp = dict(inp, y=dull)

    def widest_darker(D):
        return max((s['area_cells'] for S in D['scales'].values() for s in S['spots'] if s['sign'] == 'darker'), default=0)
    assert widest_darker(_run(inp)) < 1000
    assert widest_darker(_run(inp, debias=False)) > 10000
    box = (0.2, 0.6, 6000, 10000)
    d = Z.region_measure(dull, inp['sr'], *box)['box_power_db'] - Z.region_measure(inp['after'], inp['sr'], *box)['box_power_db']
    assert abs(d + 6.0) < 0.5


def test_a_spot_counts_only_above_the_control_rate_in_its_own_cell():
    from ismail.api_zoom import hot_spots
    base = dict(scale='strokes', sign='brighter', quarter=2, band_lo_hz=1000.0, band_hi_hz=3000.0, beyond_control=True)
    D = {'scales': {'strokes': {'spots': [dict(base, peak_z_spread=9.0, control_cell_rate=1.0),
                                          dict(base, peak_z_spread=3.0, control_cell_rate=0.2),
                                          dict(base, peak_z_spread=4.0, control_cell_rate=0.0)]}}}
    counted, rest = hot_spots(D)
    assert [s['peak_z_spread'] for s in counted] == [4.0, 3.0] and [s['peak_z_spread'] for s in rest] == [9.0]
    many = {'scales': {'grain': {'spots': [dict(base, peak_z_spread=float(i), control_cell_rate=0.0) for i in range(40)]}}}
    assert len(hot_spots(many, top=99)[0]) == 12 and len(hot_spots(many, top=3)[0]) == 3


def test_level_match_sets_minus_25_lufs():
    sr, rng = 44100, np.random.default_rng(5)
    a, b = 0.3 * rng.standard_normal(sr), 0.01 * rng.standard_normal(sr)
    la, lb = (Z.loudness_db(Z.level_match(x, sr), sr)[0] for x in (a, b))
    assert abs(la + 25) < 0.1 and abs(lb + 25) < 0.1


def _project(tmp_path, inp, n=None):
    proj = str(tmp_path / 'proj')
    OPS['project_new'](proj, bpm=120, length_bars=1, name='Zoom')
    sf.write(os.path.join(proj, 'test.wav'), inp['y'], inp['sr'])
    for i, r in enumerate(inp['refs'][:n]):
        sf.write(os.path.join(proj, f'take{i}.wav'), r, inp['sr'])
    return proj


def test_the_op_reads_hot_spots_first_and_draws_a_bounded_picture(tmp_path):
    inp, exp, tol = Z.parity_fixture(0)
    proj = _project(tmp_path, inp)
    takes = [os.path.join(proj, f'take{i}.wav') for i in range(len(inp['refs']))]
    out = OPS['sound_zoom_diff'](proj, os.path.join(proj, 'test.wav'), takes, seconds=[0.1, 0.7], word='fixture',
                                 level_lufs=None)
    lines = out.splitlines()
    assert lines[1].startswith('HOT SPOTS (counted')
    first = lines[2]
    assert 'brighter' in first and "of 'fixture'" in first and 'control 0.00' in first, out
    assert first.split('. ')[1].split(',')[0] in ('strokes', 'plumes') and '1-3 kHz' in first or '3-6 kHz' in first
    png = lines[-1].split('picture: ')[1].split(' (')[0]
    v = json.load(open(png + '.json'))
    assert max(v['px']) <= A.EYE_PX_MAX and len(v['panels']) == 2
    with pytest.raises(api.OpError, match='unknown params'):
        OPS['sound_zoom_diff'](proj, 'test.wav', takes, params={'z_threshold': 2})
    r = OPS['region_measure'](proj, os.path.join(proj, 'test.wav'), seconds=[0.35, 0.5], f_lo=2000, f_hi=4000)
    assert r.startswith('box 0.350-0.500 s, 2.00-4.00 kHz') and 'share' in r


def test_sound_zoom_same_box_same_pixels_and_address_at_both_resolutions(tmp_path):
    """One box of one file: the two panels hold it at the same pixel box (one panel lower) and give it one
    address; the time-sharp view resolves a click train the frequency-sharp one smears."""
    sr = 44100
    y = np.zeros(sr)
    y[np.arange(int(0.3 * sr), int(0.5 * sr), int(sr / 100))] = 0.8           # 100 Hz clicks, 0.3-0.5 s
    t = np.arange(sr) / sr
    y += 0.1 * np.sin(2 * np.pi * 3000 * t)
    proj = str(tmp_path / 'proj')
    OPS['project_new'](proj, bpm=120, length_bars=1, name='Zoom')
    sf.write(os.path.join(proj, 'clicks.wav'), y, sr)
    words = [{'w': 'click', 't0': 0.3, 't1': 0.5}]
    out = OPS['sound_zoom'](proj, os.path.join(proj, 'clicks.wav'), seconds=[0.25, 0.55], words=words)
    png = out.splitlines()[0]
    v = json.load(open(png + '.json'))
    top, bot = v['panels']
    assert top['label'] == 'time-sharp' and (top['nfft'], top['hop']) == (256, 32) and bot['nfft'] == 2048
    assert v['px'] == [640, 840] and max(v['px']) <= A.EYE_PX_MAX
    assert top['axes'][0] == bot['axes'][0] and top['axes'][2:] == bot['axes'][2:]
    assert abs(top['axes'][1] - bot['axes'][1] - 0.5) < 1e-9                  # one panel lower, same pixel box
    l, b, w, h = top['axes']
    for u, f in ((0.2, 3000.0), (0.5, 9000.0)):
        fx = l + w * u
        fy_top = 1 - (b + h * f / 16000)
        fy_bot = fy_top + 0.5
        at = A.eye_address(v, fx, fy_top)
        assert at == A.eye_address(v, fx, fy_bot) and at is not None
        assert np.allclose(A.eye_point(v, fx, fy_top), A.eye_point(v, fx, fy_bot))
    assert A.eye_address(v, l + w * 0.5, 1 - (b + h * 3000 / 16000)) == '0.15 s 3.0 kHz "click" vowel bands'
    import matplotlib.image as mpimg
    im = mpimg.imread(png)[..., :3].sum(axis=2)
    H, Wd = im.shape
    row = lambda pn: int((1 - (pn['axes'][1] + pn['axes'][3] * 12000 / 16000)) * H)    # noqa: E731  12 kHz, clicks only
    cols = slice(int((l + w * 0.3) * Wd), int((l + w * 0.75) * Wd))
    assert np.std(im[row(top), cols]) > 2 * np.std(im[row(bot), cols])        # the pulses as stripes on top only
