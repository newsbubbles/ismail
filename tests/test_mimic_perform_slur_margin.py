"""mimic perform: each slur draws its own move inside a margin (slur_vary, ledger M327).

Nate on the #358 confirm round (clarinet, 2026-10-25): on a stepping tune both the 45 ms glide and the fingered
move pass, "maybe somewhere between those two would be like the perfect fit but I think those two represent like the
margins of where we should be inside of"; on leaps he picked the fingers and called the glide "slide, too slow". So a
step draws its cross anywhere from the glide to the fingers, a leap near the fingers; slur_vary 0 renders exactly what
#358 did, and the draw is keyed on song time, so a window renders as the whole does."""
import importlib.util
import os

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

from ismail import mimic, mimic_perform
from ismail.dsp import SR

PROF = 'clarinet'
STILL = {'vib': 0.0, 'drift_cents': 0.0, 'breathe_db': 0.0}


def _before():
    """mimic_perform as it was before M327 (a saved copy in tests/), loaded inside the ismail package"""
    path = os.path.join(os.path.dirname(__file__), 'mimic_perform_before_m327.py')
    spec = importlib.util.spec_from_file_location('ismail._mimic_perform_before_m327', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def pitch_track(y, lo_m, hi_m):
    """the fundamental's pitch (MIDI) per sample, from one band around both notes, smoothed over 1 ms"""
    flo, fhi = 440 * 2 ** ((lo_m - 69) / 12), 440 * 2 ** ((hi_m - 69) / 12)
    x = sosfiltfilt(butter(4, [flo * 0.8, fhi * 1.25], 'bp', fs=SR, output='sos'), y.mean(0))
    f = np.diff(np.unwrap(np.angle(hilbert(x)))) * SR / (2 * np.pi)
    k = int(0.001 * SR)
    f = np.convolve(f, np.ones(k) / k, 'same')
    return 69 + 12 * np.log2(np.maximum(f, 1.0) / 440)


def line(lo, hi, n=41, step=0.3):
    """n notes alternating lo and hi, each overlapping the next by 50 ms: n - 1 slurs"""
    return [(0.1 + i * step, (lo, hi)[i % 2], step + 0.05, 90) for i in range(n)]


def moves_ms(lo, hi, perform=None):
    """per slur: the 10-90 % time of the pitch move, ms"""
    prof = mimic.load_profile(PROF)
    notes = line(lo, hi)
    n = int((notes[-1][0] + notes[-1][2] + 0.5) * SR)
    y = mimic_perform.perform(prof, notes, n, SR, mimic_params={'room': 0}, perform=dict(STILL, **(perform or {})))
    p = pitch_track(y, lo, hi)
    out = []
    for (_, pm, _, _), (st, m, _, _) in zip(notes, notes[1:]):
        a = int(round((st - 0.03) * SR))
        seg = (p[a:a + int(0.15 * SR)] - pm) / (m - pm)            # 0 = the old note, 1 = the new
        out.append((int(np.argmax(seg > 0.9)) - int(np.argmax(seg > 0.1))) / SR * 1000)
    return np.array(out)


def test_steps_spread_across_the_margin_and_leaps_stay_near_the_fingers():
    steps = moves_ms(62, 64)                                        # D4 E4: 2 semitones, a step
    leaps = moves_ms(62, 69)                                        # D4 A4: 7 semitones, a leap
    fixed = moves_ms(62, 64, {'slur_vary': 0.0})                    # #358: every slur fingered
    glide = moves_ms(62, 64, {'slur_cross': 0.0})                   # the glide of before
    pc = lambda x: np.percentile(x, [10, 50, 90]).round(1)
    print('10-90 % pitch move ms p10 / p50 / p90: steps', pc(steps), 'leaps', pc(leaps),
          'slur_vary 0', pc(fixed), 'slur_cross 0', pc(glide))
    assert mimic_perform.DEFAULTS['slur_vary'] == 1.0
    # steps: from about the fingers to about the glide, spread across it
    assert np.percentile(steps, 10) < np.median(fixed) + 6, (steps, fixed)
    assert np.percentile(steps, 90) > np.median(glide) - 8, (steps, glide)
    assert np.percentile(steps, 90) - np.percentile(steps, 10) > 12, steps
    assert steps.max() < glide.max() + 4, (steps, glide)
    # leaps: near the fast end, never out to the glide
    assert np.percentile(leaps, 90) < np.median(fixed) + 10, (leaps, fixed)
    assert np.median(leaps) < np.median(steps) - 5, (leaps, steps)
    # slur_vary 0: one fixed fingered move
    assert np.percentile(fixed, 90) - np.percentile(fixed, 10) < 4, fixed


def test_the_draw_is_keyed_on_song_time_and_leaps_draw_from_the_top_quarter():
    st = [mimic_perform.slur_cross_at(5, ms, 2.0, 1.0, 1.0) for ms in range(0, 40000, 37)]
    lp = [mimic_perform.slur_cross_at(5, ms, -7.0, 1.0, 1.0) for ms in range(0, 40000, 37)]
    assert min(st) >= 0.0 and max(st) <= 1.0 and min(st) < 0.05 and max(st) > 0.95
    assert min(lp) >= 0.75 and max(lp) <= 1.0 and min(lp) < 0.77
    assert mimic_perform.slur_cross_at(5, 1234, 2.0, 1.0, 1.0) == mimic_perform.slur_cross_at(5, 1234, 2.0, 1.0, 1.0)
    # a narrower margin and a lower slur_cross scale it; vary 0 or cross 0 is slur_cross itself
    assert 0.6 <= mimic_perform.slur_cross_at(5, 1234, 1.0, 0.8, 0.25) <= 0.8
    assert mimic_perform.slur_cross_at(5, 1234, 1.0, 0.7, 0.0) == 0.7
    assert mimic_perform.slur_cross_at(5, 1234, 1.0, 0.0, 1.0) == 0.0


def test_slur_vary_0_renders_what_it_did_before():
    old = _before()
    prof = mimic.load_profile(PROF)
    # steps, leaps, a tongued note, a new breath, a long last note (taper), and lanes on
    notes = [(0.1, 62, 0.45, 90), (0.5, 64, 0.4, 96), (0.85, 69, 0.3, 92), (1.1, 67, 0.5, 90),
             (2.0, 64, 0.4, 88), (2.35, 60, 0.5, 94), (2.8, 72, 1.0, 90)]
    n = int(4.2 * SR)
    lanes = {'level': np.linspace(0, -4, n), 'vib': np.full(n, 12.0), 'scoop': np.where(np.arange(n) > SR, 0.5, 0)}
    for room in (0, None):                                          # the room off, and the profile's own
        mp = {'room': room} if room is not None else None
        for pf in ({}, {'slur_cross': 0.6}):
            want = old.perform(prof, notes, n, SR, lanes=lanes, mimic_params=mp, perform=pf or True)
            got = mimic_perform.perform(prof, notes, n, SR, lanes=lanes, mimic_params=mp,
                                        perform=dict(pf, slur_vary=0.0))
            assert np.array_equal(got, want), (room, pf)
    # the default does change slurs (a new take), and leaves a phrase with no slur as it was
    assert not np.array_equal(mimic_perform.perform(prof, notes, n, SR, perform=True),
                              old.perform(prof, notes, n, SR, perform=True))
    detached = [(0.1, 62, 0.3, 90), (0.45, 65, 0.3, 90), (1.2, 60, 0.8, 90)]
    assert np.array_equal(mimic_perform.perform(prof, detached, n, SR, perform=True),
                          old.perform(prof, detached, n, SR, perform=True))


def test_same_beat0_renders_the_same_and_a_window_renders_as_the_whole():
    prof = mimic.load_profile(PROF)
    notes = line(62, 64, n=9) + [(n[0] + 4.0, n[1] + 5, n[2], n[3]) for n in line(62, 64, n=9)]
    n = int(8.0 * SR)
    mp = {'room': 0}
    whole = mimic_perform.perform(prof, notes, n, SR, bpm=120.0, mimic_params=mp, perform=True)
    assert np.array_equal(whole, mimic_perform.perform(prof, notes, n, SR, bpm=120.0, mimic_params=mp, perform=True))
    # a window from 3.6 s (beat 7.2 at 120 bpm), in the silence before the second phrase: the notes in it, shifted
    w0 = 3.6
    later = [(st - w0, m, d, v) for st, m, d, v in notes if st >= w0]
    m = n - int(w0 * SR)
    win = mimic_perform.perform(prof, later, m, SR, bpm=120.0, beat0=w0 * 2, mimic_params=mp, perform=True)
    ref = whole[:, int(w0 * SR):]
    assert np.abs(ref).max() > 0.01
    assert np.allclose(win, ref, rtol=0, atol=1e-6 * np.abs(ref).max()), np.abs(win - ref).max()
    # and it is the window's own draw, not one shared with a render from 0: a different beat0 is a different take
    other = mimic_perform.perform(prof, later, m, SR, bpm=120.0, beat0=0.0, mimic_params=mp, perform=True)
    assert not np.allclose(other, ref, rtol=0, atol=1e-3 * np.abs(ref).max())
