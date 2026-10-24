"""mimic perform=true plays a sustained profile as phrases (ledger M308, from the bossa_gilberto_studio flute
performer): overlapping notes slur with no second attack, a short gap tongues, a long gap is a new breath, the
vibrato waits and grows, lanes shape it, and with perform left out every render is what it was."""
import json
import os

import numpy as np
import pytest
from scipy.signal import butter, hilbert, sosfiltfilt

from ismail import instruments as I
from ismail import mimic, mimic_perform
from ismail.dsp import SR

PROF = 'clarinet'          # small and sustained; the notes are short, the room is off


def render(inst, notes, dur, auto=None, root=None):
    return I.render_instrument(I.normalize(inst), notes, int(dur * SR), auto, 120.0, SR, root)


def inst(perform=None, **params):
    d = {'type': 'mimic', 'profile': PROF, 'params': dict({'room': 0}, **params)}
    if perform is not None:
        d['perform'] = perform
    return d


def frames_db(x, ms=10):
    f = int(ms / 1000 * SR)
    n = len(x) // f
    return 10 * np.log10(np.mean(x[:n * f].reshape(n, f) ** 2, axis=1) + 1e-20)


def harmonics_db(y, midi, nh=4, nfft=2048, hop=220):
    """Energy at the first nh harmonics of a pitch over time (centre times s, dB)."""
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


def join_attack_db(y, join, midi):
    """The second attack at a join: the new note's harmonics over the first 70 ms after the join, against their
    held level (dB). A note that attacks again starts from silence and rises (well under 0); a slurred note is
    there as soon as the pitch arrives (near 0)."""
    T, E = harmonics_db(y, midi)
    held = np.median(E[(T > join + 0.4) & (T < join + 0.8)])
    return float(np.mean(E[(T > join + 0.01) & (T < join + 0.07)]) - held)


SLUR = [(0.1, 62, 0.75, 90), (0.8, 65, 1.3, 90)]       # the first note overlaps the second by 50 ms


def test_perform_left_out_renders_what_it_did():
    notes = [(0.05, 62, 0.3, 90), (0.3, 65, 0.3, 70)]
    n = int(1.2 * SR)
    prof = mimic.load_profile(PROF)
    want = np.zeros((2, n))                              # the note by note loop, as render_mimic has it
    for st, m, d, v in notes:
        s0 = int(round(st * SR))
        k = int((d + 1.0) * SR)
        y = mimic.render(prof, I.midi_to_hz(m), np.arange(k) / SR, v / 127, d, SR, room=0)
        k = min(y.shape[1], k, n - s0)
        want[:, s0:s0 + k] += y[:, :k]
    assert np.array_equal(render(inst(), notes, 1.2), want)
    assert np.array_equal(render(inst(perform=False), notes, 1.2), want)
    assert 'perform' not in I.normalize(inst())


def test_overlapping_notes_slur_without_a_second_attack():
    note = render(inst(), SLUR, 2.4)
    perf = render(inst(perform=True), SLUR, 2.4)
    a_note, a_perf = join_attack_db(note, 0.8, 65), join_attack_db(perf, 0.8, 65)
    print('second attack at the join, dB re held: note by note', round(a_note, 1), 'performed', round(a_perf, 1))
    assert a_perf > -4 and a_note < a_perf - 6, (a_note, a_perf)
    ph = mimic_perform.phrases([(s, m, d, v / 127) for s, m, d, v in SLUR], 0.16)
    assert [h for *_, h in ph[0]] == ['breath', 'slur']


def test_a_short_gap_tongues_and_a_long_gap_breathes():
    def dip(gap):
        notes = [(0.1, 62, 0.5, 90), (0.6 + gap, 65, 0.6, 90)]
        y = render(inst(perform=True), notes, 1.6 + gap)
        e = frames_db(y.mean(0))
        held = np.median(e[30:55])
        return held - e[61:int((0.6 + gap) * 100)].min(), notes
    d_tongue, notes = dip(0.06)
    d_breath, _ = dip(0.5)
    ph = mimic_perform.phrases([(s, m, d, v / 127) for s, m, d, v in notes], 0.16)
    assert [h for *_, h in ph[0]] == ['breath', 'tongue']
    assert 6 < d_tongue < 30, d_tongue              # the tongue stops the tone briefly; the air goes on
    assert d_breath > 40, d_breath                  # a new breath: silence between the phrases
    slur = render(inst(perform=True), SLUR, 2.4)
    e = frames_db(slur.mean(0))
    assert np.median(e[50:75]) - e[76:86].min() < 4   # a slur holds the tone through the join


def cents_wobble(y, midi, t0, t1):
    f = 440 * 2 ** ((midi - 69) / 12)
    x = sosfiltfilt(butter(3, [f * 0.85, f * 1.15], 'bp', fs=SR, output='sos'), y.mean(0))
    ph = np.unwrap(np.angle(hilbert(x)))
    inst_f = np.diff(ph) * SR / (2 * np.pi)
    k = int(0.01 * SR)
    inst_f = np.convolve(inst_f, np.ones(k) / k, 'same')
    c = 1200 * np.log2(np.maximum(inst_f[int(t0 * SR):int(t1 * SR)], 1.0) / f)
    return float(np.std(c))


def test_vibrato_waits_then_grows():
    notes = [(0.1, 70, 1.6, 90)]
    y = render(inst(perform={'vib': 30.0, 'drift_cents': 0.0, 'breathe_db': 0.0}), notes, 2.0)
    early = cents_wobble(y, 70, 0.2, 0.4)
    late = cents_wobble(y, 70, 1.1, 1.5)
    assert early < 4 and late > 12, (early, late)
    flat = render(inst(perform={'vib': 0.0, 'drift_cents': 0.0, 'breathe_db': 0.0}), notes, 2.0)
    assert cents_wobble(flat, 70, 1.1, 1.5) < 4


def test_the_level_lane_changes_the_level():
    notes = [(0.1, 65, 1.4, 90)]
    n = int(1.8 * SR)
    lane = np.zeros(n)
    lane[int(0.8 * SR):] = -10.0
    plain = render(inst(perform=True), notes, 1.8)
    laned = render(inst(perform=True), notes, 1.8, auto={'lane.level': lane})

    def rms_db(y, a, b):
        return 20 * np.log10(np.sqrt(np.mean(y[:, int(a * SR):int(b * SR)] ** 2)))
    assert abs(rms_db(laned, 0.3, 0.7) - rms_db(plain, 0.3, 0.7)) < 0.5
    drop = rms_db(plain, 0.9, 1.3) - rms_db(laned, 0.9, 1.3)
    assert 7 < drop < 13, drop


def test_a_chord_splits_into_lines():
    ns = [(0.0, 60, 1.0, 0.7), (0.0, 64, 1.0, 0.7), (0.9, 61, 1.0, 0.7)]
    ls = mimic_perform.lines(ns, 0.03)
    assert sorted(len(x) for x in ls) == [1, 2]
    assert [m for _, m, _, _ in max(ls, key=len)] == [60, 61]     # the nearest line takes the next note
    y = render(inst(perform=True), [(s, m, d, 90) for s, m, d, _ in ns[:2]], 1.5)
    assert np.sqrt(np.mean(y ** 2)) > 1e-3


def test_a_decaying_profile_refuses_perform(tmp_path):
    prof = json.load(open(os.path.join(os.path.dirname(I.__file__), 'voices', 'winds', 'clarinet.mimic.json')))
    prof['kind'] = 'decaying'
    os.makedirs(tmp_path / 'voices')
    with open(tmp_path / 'voices' / 'plucky.mimic.json', 'w') as f:
        json.dump(prof, f)
    with pytest.raises(I.InstrumentError, match='sustained'):
        render({'type': 'mimic', 'profile': 'plucky', 'perform': True}, [(0.0, 60, 0.2, 90)], 0.5,
               root=str(tmp_path))


def test_perform_settings_are_checked():
    with pytest.raises(I.InstrumentError, match='perform params'):
        I.normalize({'type': 'mimic', 'profile': PROF, 'perform': {'vibe': 3}})
    with pytest.raises(I.InstrumentError, match='do nothing with perform'):
        I.normalize({'type': 'mimic', 'profile': PROF, 'perform': True, 'params': {'players': 3}})
    with pytest.raises(I.InstrumentError, match='true'):
        I.normalize({'type': 'mimic', 'profile': PROF, 'perform': 'yes'})
    ok = I.normalize({'type': 'mimic', 'profile': PROF, 'perform': {'vib': 8}, 'params': {'bright': -2}})
    assert ok['perform'] == {'vib': 8}


def test_live_plays_a_performed_mimic_as_a_performer():
    from ismail.live import ops, worker
    marked = ops._mark_performer({'type': 'mimic', 'profile': PROF, 'perform': True}, '.')
    assert marked.get('performer') and I.normalize(marked)['performer']
    assert not ops._mark_performer({'type': 'mimic', 'profile': PROF}, '.').get('performer')
    y = worker._perform(I.normalize(dict(marked, params={'room': 0})), SLUR, int(2.2 * SR), 120.0, None,
                        {'level': [[0.0, 0.0], [1.0, 0.0]]})
    assert y.shape == (2, int(2.2 * SR)) and np.sqrt(np.mean(y ** 2)) > 1e-3
