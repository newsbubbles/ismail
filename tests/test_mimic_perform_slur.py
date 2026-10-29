"""mimic perform: a fingered slur by default (ledger M322) and the vib_delay / vib_grow lanes (ledger M323).

Nate on the performer's ear round, 2026-10-24: the 45 ms slur glide "doesn't sound like a finger covering a hole, it
sounds like a MIDI instrument sliding up"; he liked how the note's profile changes at the join. So by default the
pitch moves in a few ms and each partial's level hands over from the old note's to the new note's; slur_cross 0 is
the glide of before, rendered exactly as before. And when the vibrato comes in is a lane now."""
import importlib.util
import os

import numpy as np
from scipy.signal import butter, hilbert, sosfiltfilt

from ismail import instruments as I
from ismail import mimic, mimic_perform
from ismail.dsp import SR

PROF = 'clarinet'
STILL = {'vib': 0.0, 'drift_cents': 0.0, 'breathe_db': 0.0}


def _before():
    """mimic_perform as it was before M322 (a saved copy in tests/), loaded inside the ismail package"""
    path = os.path.join(os.path.dirname(__file__), 'mimic_perform_before_m322.py')
    spec = importlib.util.spec_from_file_location('ismail._mimic_perform_before_m322', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render(perform, notes, dur, auto=None, room=0):
    inst = {'type': 'mimic', 'profile': PROF, 'perform': perform}
    if room is not None:
        inst['params'] = {'room': room}
    return I.render_instrument(I.normalize(inst), notes, int(dur * SR), auto, 120.0, SR, None)


def pitch_track(y, lo_m, hi_m):
    """the fundamental's pitch (MIDI) per sample, from one band around both notes, smoothed over 1 ms"""
    flo, fhi = 440 * 2 ** ((lo_m - 69) / 12), 440 * 2 ** ((hi_m - 69) / 12)
    x = sosfiltfilt(butter(4, [flo * 0.8, fhi * 1.25], 'bp', fs=SR, output='sos'), y.mean(0))
    f = np.diff(np.unwrap(np.angle(hilbert(x)))) * SR / (2 * np.pi)
    k = int(0.001 * SR)
    f = np.convolve(f, np.ones(k) / k, 'same')
    return 69 + 12 * np.log2(np.maximum(f, 1.0) / 440)


def slur_line(n=12, step=0.3):
    """n notes alternating D4 and F4, each overlapping the next by 50 ms: n - 1 slurs"""
    return [(0.1 + i * step, (62, 65)[i % 2], step + 0.05, 90) for i in range(n)]


def join_ms(perform):
    """per slur: ms from the join until the pitch is 90 % of the way to the new note, and its 10-90 % time"""
    notes = slur_line()
    end = notes[-1][0] + notes[-1][2]
    p = pitch_track(render(dict(STILL, **perform), notes, end + 0.5), 62, 65)
    arrive, move = [], []
    for (pst, pm, _, _), (st, m, _, _) in zip(notes, notes[1:]):
        a = int(round((st - 0.03) * SR))
        seg = (p[a:a + int(0.15 * SR)] - pm) / (m - pm)            # 0 = the old note, 1 = the new
        t10, t90 = int(np.argmax(seg > 0.1)), int(np.argmax(seg > 0.9))
        arrive.append((t90 / SR - 0.03) * 1000)
        move.append((t90 - t10) / SR * 1000)
    return np.array(arrive), np.array(move)


def test_a_fingered_slur_reaches_the_new_note_in_a_few_ms():
    arrive, move = join_ms({})
    arrive0, move0 = join_ms({'slur_cross': 0.0})
    print('join 10-90 % ms p50 / p90: fingered', np.percentile(move, [50, 90]).round(1),
          'glide', np.percentile(move0, [50, 90]).round(1))
    assert mimic_perform.DEFAULTS['slur_cross'] == 1.0
    assert arrive.max() < 10 and np.percentile(move, 90) < 8, (arrive, move)
    sl = mimic_perform.DEFAULTS['slur_ms']
    assert 0.5 * sl < np.median(arrive0) < 1.3 * sl, arrive0          # the glide takes about slur_ms
    assert np.median(move0) > 3 * np.median(move), (move0, move)


def test_slur_cross_0_renders_what_it_did_before():
    old = _before()
    prof = mimic.load_profile(PROF)
    # slurs, a tongued note, a new breath, a long last note (taper), and lanes on
    notes = [(0.1, 62, 0.45, 90), (0.5, 65, 0.4, 96), (0.85, 69, 0.3, 92), (1.2, 67, 0.5, 90),
             (2.0, 64, 0.4, 88), (2.35, 60, 1.2, 94)]
    n = int(4.0 * SR)
    lanes = {'level': np.linspace(0, -4, n), 'vib': np.full(n, 12.0), 'scoop': np.where(np.arange(n) > SR, 0.5, 0)}
    for room in (0, None):                                          # the room off, and the profile's own
        mp = {'room': room} if room is not None else None
        want = old.perform(prof, notes, n, SR, lanes=lanes, mimic_params=mp, perform=True)
        got = mimic_perform.perform(prof, notes, n, SR, lanes=lanes, mimic_params=mp, perform={'slur_cross': 0.0})
        assert np.array_equal(got, want), room
    # a phrase with no slur (tongued and breath onsets only) is the same at the default too
    detached = [(0.1, 62, 0.3, 90), (0.45, 65, 0.3, 90), (1.2, 60, 0.8, 90)]
    assert np.array_equal(mimic_perform.perform(prof, detached, n, SR, perform=True),
                          old.perform(prof, detached, n, SR, perform=True))


def harmonics_db(y, midi, nh=4, nfft=2048, hop=220):
    x = y.mean(0)
    f = 440 * 2 ** ((midi - 69) / 12)
    w = np.hanning(nfft)
    bins = [int(round(k * f * nfft / SR)) for k in range(1, nh + 1)]
    T, E = [], []
    for s in range(0, len(x) - nfft, hop):
        X = np.abs(np.fft.rfft(x[s:s + nfft] * w)) ** 2
        E.append(sum(X[b - 1:b + 2].max() for b in bins))
        T.append((s + nfft / 2) / SR)
    return np.array(T), 10 * np.log10(np.array(E) + 1e-20)


def test_a_fingered_slur_has_no_second_attack_and_holds_the_tone():
    slur = [(0.1, 62, 0.75, 90), (0.8, 65, 1.3, 90)]
    y = render(True, slur, 2.4)
    T, E = harmonics_db(y, 65)
    held = np.median(E[(T > 1.2) & (T < 1.6)])
    first = float(np.mean(E[(T > 0.81) & (T < 0.87)]) - held)
    assert first > -4, first                                         # the new note is there as the pitch arrives
    f = int(0.01 * SR)
    e = 10 * np.log10(np.mean(y.mean(0)[:len(y[0]) // f * f].reshape(-1, f) ** 2, axis=1) + 1e-20)
    held = np.median(e[50:75])
    assert held - e[76:86].min() < 5                                 # no gap at the join (a tongue dips 13 dB)
    assert np.sum(e[76:86] < held - 2) <= 2, e[76:86] - held         # a short dip as the fingers cross, not 40 ms


def vib_onset(y, midi, t_note, thresh=3.0):
    """s after the note's start when the pitch first wobbles more than thresh cents (std over 60 ms)"""
    p = pitch_track(y, midi - 0.5, midi + 0.5)
    c = 100 * (p - midi)
    w = int(0.06 * SR)
    for s in range(int((t_note + 0.05) * SR), len(c) - w, w // 2):
        if np.std(c[s:s + w]) > thresh:
            return s / SR - t_note
    return None


def test_the_vib_delay_lane_moves_the_vibrato_onset():
    notes = [(0.1, 70, 2.2, 90)]
    n = int(2.6 * SR)
    pf = {'vib': 30.0, 'vib_grow': 0.25, 'drift_cents': 0.0, 'breathe_db': 0.0}
    on = {}
    for d in (0.1, 0.8):
        on[d] = vib_onset(render(pf, notes, 2.6, auto={'lane.vib_delay': np.full(n, d)}), 70, 0.1)
    print('vibrato onset, s after the note start: lane 0.1 ->', on[0.1], ', lane 0.8 ->', on[0.8])
    assert on[0.1] is not None and on[0.8] is not None, on
    assert on[0.1] < 0.35 and on[0.8] > 0.55 and on[0.8] - on[0.1] > 0.4, on
    # no lane: the param, as before
    base = vib_onset(render(dict(pf, vib_delay=0.8), notes, 2.6), 70, 0.1)
    assert abs(base - on[0.8]) < 0.1, (base, on[0.8])
    assert 'vib_delay' in mimic_perform.INFO['lanes'] and 'vib_grow' in mimic_perform.INFO['lanes']
