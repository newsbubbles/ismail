"""Pitch in cents (ledger:M225): analyze_melody(cents=True) gives every note's measured pitch as numbers, and
analyze_contour gives f0 per frame in bounded windows. Synthetic tones with known pitches, a quarter tone among them."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail import api

SR = 22050
OPS = api.OPS
BEAT = 0.5  # 120 bpm


@pytest.fixture
def proj():
    d = tempfile.mkdtemp(prefix='ismail_cents_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=2)
    yield d, p
    shutil.rmtree(d, ignore_errors=True)


def line(pitches, beats=1.0, sec=4.0):
    """Harmonic tones at float MIDI pitches, one per `beats` from t=0, each with a short attack and a gap."""
    y = np.zeros(int(sec * SR))
    n = int(beats * BEAT * SR * 0.9)
    t = np.arange(n) / SR
    env = np.minimum(1, t / 0.01) * np.minimum(1, (n - np.arange(n)) / (0.02 * SR))
    for i, m in enumerate(pitches):
        f = 440 * 2 ** ((m - 69) / 12)
        a = int(i * beats * BEAT * SR)
        y[a:a + n] += env * sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 5))
    return 0.4 * y / np.abs(y).max()


def wav(d, name, y):
    f = os.path.join(d, name + '.wav')
    sf.write(f, y, SR)
    return f


def rows(txt):
    return [ln.split('\t') for ln in txt.splitlines() if ln and not ln.startswith('#')]


def test_a_tone_fifty_cents_flat_reads_minus_fifty(proj):
    d, p = proj
    f = wav(d, 'flat', line([69 - 0.5], beats=3))
    out = OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', cents=True)
    r = rows(out)
    assert r and all(abs(float(x[4]) - 68.5) < 0.05 for x in r), out   # (the release can read as a short 2nd note)
    m = float(r[0][4])
    assert abs((m - 69) * 100 + 50) < 5, out                       # -50c from A4, within a few cents
    assert abs(float(r[0][5]) - 440 * 2 ** (-0.5 / 12)) < 1.5, out  # the Hz column agrees
    # the cents column is from the named note; note + cents is the float pitch
    named = int(round(m))
    assert r[0][1] == A.midi_to_name(named) and abs(int(r[0][6]) - (m - named) * 100) <= 1, out
    # a smaller offset names the note it belongs to
    f = wav(d, 'flat30', line([69 - 0.3], beats=3))
    r = rows(OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', cents=True))
    assert r[0][1] == 'A4' and abs(int(r[0][6]) + 30) <= 4, r


def test_a_quarter_tone_step_reads_both_steps(proj):
    d, p = proj
    want = [60, 62, 63.5, 65]          # C D E-half-flat F: rast's three-quarter-tone steps
    f = wav(d, 'rast', line(want))
    out = OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', cents=True)
    got = []
    for r in rows(out):     # the reader can split a note's tail off as a short note of the same pitch: one step
        if not got or abs(float(r[4]) - got[-1]) > 0.1:
            got.append(float(r[4]))
    assert len(got) == 4, out
    for g, w in zip(got, want):
        assert abs(g - w) < 0.05, out
    steps = np.diff(got) * 100
    assert abs(steps[1] - 150) < 6 and abs(steps[2] - 150) < 6, (steps, out)
    # starts and lengths are the same as the default reading's, and the default reading is unchanged
    plain = OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6')
    assert '\t' not in plain
    assert plain == A.melody(*api._load(p).source(f, [1, 1]), [1, 1], 'C3', 'C6', 0.25)[1]
    assert [ln.split()[0] for ln in plain.splitlines() if not ln.startswith('#')] == [r[0] for r in rows(out)]


def test_the_contour_reads_the_steps_and_respects_the_window(proj):
    d, p = proj
    f = wav(d, 'rast', line([60, 62, 63.5, 65]))
    # bar 1 at 10 ms is 200 frames; 60 at a time
    out = OPS['analyze_contour'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', max_rows=60)
    r = rows(out)
    assert len(r) == 60 and 'frames 0-59 of 200' in out and 'start=60' in out.splitlines()[-1], out
    # the third note (E half-flat) sounds over beats 3-3.9: frames 100-189
    out = OPS['analyze_contour'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', start=110, max_rows=60)
    r = rows(out)
    assert abs(float(r[0][0]) - 1.1) < 0.005 and r[0][1] == '1' and abs(float(r[0][2]) - 3.2) < 0.015, out
    mid = [float(x[4]) for x in r[:30]]   # 1.10-1.39 s, inside the note
    assert all(abs(m - 63.5) < 0.05 for m in mid), out
    assert 'start=170' in out.splitlines()[-1]
    last = OPS['analyze_contour'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', start=170, max_rows=60)
    assert len(rows(last)) == 30 and last.splitlines()[-1] == '# end of bars 1-1', last
    # a coarser hop gives fewer rows over the same bar; a window past the cap or the end points forward
    assert len(rows(OPS['analyze_contour'](p, source=f, bars=[1, 1], hop_ms=50, fmin='C3', fmax='C6'))) == 40
    with pytest.raises(api.OpError, match='window by window'):
        OPS['analyze_contour'](p, source=f, bars=[1, 1], max_rows=5000)
    with pytest.raises(api.OpError, match='past the end'):
        OPS['analyze_contour'](p, source=f, bars=[1, 1], start=200)


def test_ops_find_finds_the_contour():
    out = OPS['ops_find'](need='pitch contour in cents', n=3)
    assert out.splitlines()[1].split()[0] == 'analyze_contour', out


def test_a_vibrato_note_reads_as_one_note(proj):
    """A held note with a wide vibrato (+-80c at 4 Hz) centred 30c sharp reads as a semitone pair;
    vibrato_ms= averages one cycle before rounding and reads one note (ledger:M342)."""
    d, p = proj
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    m = 69.3 + 0.8 * np.sin(2 * np.pi * 4.0 * t)
    ph = 2 * np.pi * np.cumsum(440 * 2 ** ((m - 69) / 12)) / SR
    env = np.minimum(1, t / 0.01) * np.minimum(1, (n - np.arange(n)) / (0.02 * SR))
    y = np.zeros(int(4 * SR))
    y[:n] = env * sum(np.sin(k * ph) / k for k in range(1, 5))
    f = wav(d, 'vib', 0.4 * y / np.abs(y).max())
    plain = rows(OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', cents=True))
    assert len({x[1] for x in plain}) == 2, plain
    out = OPS['analyze_melody'](p, source=f, bars=[1, 1], fmin='C3', fmax='C6', cents=True, vibrato_ms=250)
    r = rows(out)      # (the release can read as a short 2nd note)
    assert r[0][1] == 'A4' and float(r[0][2]) >= 2.5 and abs(float(r[0][4]) - 69.3) < 0.1, out
    assert all(float(x[2]) <= 0.25 for x in r[1:]), out
