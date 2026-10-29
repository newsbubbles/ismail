"""analyze_grid's harmony vote between bar 1 candidates two beats apart (M312): a four-on-the-floor house track read
bar 1 two beats late, since a snare on 2 and 4 reads the same under a two-beat shift."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail import api

SR = 22050
BPM = 124
LEAD = 0.5
BARS = 24
SPB = 60 / BPM
BAR = 4 * SPB


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp(prefix='ismail_downbeat_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


def hit(kind, rng=np.random.default_rng(3)):
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    if kind == 'kick':
        return np.sin(2 * np.pi * (50 + 80 * np.exp(-t * 40)) * t) * np.exp(-t * 25)
    noise = rng.normal(0, 1, n)
    if kind == 'hat':
        return 0.3 * np.diff(np.r_[0, noise]) * np.exp(-t * 90)
    return 0.6 * (noise * np.exp(-t * 30) + np.sin(2 * np.pi * 190 * t) * np.exp(-t * 40))


def house(changes=True):
    """Kick on every beat, snare on 2 and 4, an off-beat hat, and a bass whose root moves on beat 1 of every bar
    (A1 F1 C2 G1), or holds A1 throughout. Returns (mix, drums, bass)."""
    n = int((LEAD + BARS * BAR + 1) * SR)
    drums, bass = np.zeros(n), np.zeros(n)

    def put(kind, beat, gain=1.0):
        h = hit(kind) * gain
        a = int((LEAD + beat * SPB) * SR)
        drums[a:a + len(h)] += h[:n - a]
    roots = [33, 29, 36, 31] if changes else [33]
    for b in range(BARS):
        for q in range(4):
            put('kick', b * 4 + q)
            put('hat', b * 4 + q + 0.5, 0.4)
        put('snare', b * 4 + 1)
        put('snare', b * 4 + 3)
        f = 440 * 2 ** ((roots[b % len(roots)] - 69) / 12)
        a, e = int((LEAD + b * BAR) * SR), int((LEAD + (b + 1) * BAR) * SR)
        t = np.arange(e - a) / SR
        env = np.minimum(1, t / 0.01) * np.minimum(1, (t[-1] - t) / 0.02)
        bass[a:e] += env * sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 7))
    drums *= 0.5 / np.abs(drums).max()
    bass *= 0.4 / np.abs(bass).max()
    return drums + bass, drums, bass


def write(tmp, changes, cut=0.0):
    """The track's three files; cut s off the start (the true bar 1 then sits at (LEAD - cut) mod BAR)."""
    mix, drums, bass = (y[int(cut * SR):] for y in house(changes))
    paths = {}
    for name, y in (('mix', mix), ('drums', drums), ('bass', bass)):
        paths[name] = os.path.join(tmp, f'{name}.wav')
        sf.write(paths[name], y, SR)
    return paths


def near(a, b):
    d = (a - b) % BAR
    return min(d, BAR - d) < 0.03


def two_beats_off(g):
    """The grid's bar 1 candidate two beats from the true one."""
    c = g['candidate_offsets']
    q = int(np.argmin([min((x - LEAD) % BAR, BAR - (x - LEAD) % BAR) for x in c]))
    assert near(c[q], LEAD), c        # the beat itself reads right; only bar 1 is in question
    return c[(q + 2) % 4]


def test_harmony_moves_bar_1_two_beats_to_the_bass_change(tmp):
    p = write(tmp, changes=True)
    g, txt = A.beat_grid(p['mix'], BPM, 4)
    wrong = two_beats_off(g)
    for bass in (p['bass'], None):          # the bass stem, then the low band of the mix
        # today's pick, whatever it was, and a pick two beats late both end on the bass change
        for start in (g['offset_sec'], wrong):
            g2, txt2 = A.downbeat_by_harmony(p['mix'], dict(g, offset_sec=start), txt, bass)
            line = txt2.splitlines()[-1]
            assert near(g2['offset_sec'], LEAD), (bass, start, line)
            assert line.startswith(f"downbeat: drums say {start:.4f} s"), line
            assert f"using {g2['offset_sec']:.4f} s" in line, line
            if start == wrong:
                assert f"harmony says {g2['offset_sec']:.4f} s (changes on beat 1 in" in line, line
    # an override marks the grid text: bar 1 moved, the drums' candidate and the chosen one named
    g3, txt3 = A.downbeat_by_harmony(p['mix'], dict(g, offset_sec=wrong), txt.replace(
        f"bar 1 starts at {g['offset_sec']:.3f}s", f"bar 1 starts at {wrong:.3f}s"), p['bass'])
    assert f"bar 1 starts at {g3['offset_sec']:.3f}s (by harmony" in txt3, txt3
    assert g3['harmony_changes_per_beat'][2] > 3 * g3['harmony_changes_per_beat'][0], g3


def test_no_harmony_changes_keeps_the_drums_pick(tmp):
    p = write(tmp, changes=False)
    g, txt = A.beat_grid(p['mix'], BPM, 4)
    for bass in (p['bass'], None):
        for start in (g['offset_sec'], two_beats_off(g)):
            g2, txt2 = A.downbeat_by_harmony(p['mix'], dict(g, offset_sec=start), txt, bass)
            assert g2['offset_sec'] == start, txt2.splitlines()[-1]
            assert 'harmony did not vote' in txt2 or 'harmony is not clear' in txt2, txt2.splitlines()[-1]


def test_analyze_grid_prints_the_vote_from_the_ref_bass_stem(tmp):
    # the track opens on beat 3 (the first two beats cut): the drum stem alone reads bar 1 there, two beats late,
    # as on celestial_resort_1024 (drum stem 0.916 s, bass changes on beat 1 at 0.0687 s)
    cut = LEAD + 2 * SPB
    true = (LEAD - cut) % BAR
    p = write(tmp, changes=True, cut=cut)
    today = A.beat_grid(p['drums'], BPM, 4)[0]['offset_sec']
    sd = os.path.join(tmp, 'stems')
    os.makedirs(sd)
    for s in ('drums', 'bass'):
        shutil.copy(p[s], os.path.join(sd, s + '.wav'))
    proj = os.path.join(tmp, 'p')
    api.project_new(proj, bpm=BPM, length_bars=BARS)
    api.project_set(proj, reference=p['mix'], reference_stems=sd)
    out = api.OPS['analyze_grid'](proj, source='ref:drums', bpm_hint=BPM)
    line = next(ln for ln in out.splitlines() if ln.startswith('downbeat: drums say'))
    assert 'from the bass stem' in line, line
    got = float(out.split('bar 1 starts at ')[1].split('s')[0])
    assert abs(got - true) < 0.03, (today, out)
    if abs(today - true) > 0.03:        # today's pick was off: the harmony vote moved it
        assert f"drums say {today:.4f} s, harmony says {got:.4f} s" in line and '<- chosen (harmony)' in out, out
