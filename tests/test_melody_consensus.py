"""melody_consensus (ledger:M314): the lead line of a repeated phrase, read over a quieter sustained backing an octave
below. Synthetic audio: 8 saw-like lead notes, one beat each, repeated 4 times (one repetition has a different 6th
note), over a held chord an octave under the lead's root plus its fifth, 6 dB down per voice."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api
from ismail.notation import parse_notes

SR = 22050
BEAT = 0.5                                   # 120 bpm
LEAD = [69, 72, 76, 76, 74, 72, 71, 69]      # A4 C5 E5 E5 D5 C5 B4 A4: the repeated E5 needs the onset test
REPS = [1, 3, 5, 7]                          # a 2-bar phrase, four times
OPS = api.OPS


def saw(f, n, k=8):
    t = np.arange(n) / SR
    return sum(np.sin(2 * np.pi * f * h * t) / h for h in range(1, k + 1))


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


@pytest.fixture(scope='module')
def song():
    d = tempfile.mkdtemp(prefix='ismail_melcons_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=8)
    n = int(BEAT * SR)
    t = np.arange(n) / SR
    env = np.minimum(1, t / 0.015) * (0.5 + 0.5 * np.exp(-t / 0.12)) * np.minimum(1, (n - np.arange(n)) / (0.03 * SR))
    total = 8 * 4 * n
    y = np.zeros(total)
    for r, bar in enumerate(REPS):
        line = list(LEAD)
        if r == 2:
            line[5] = 74                     # a variant in one repetition: the average keeps the other three
        for i, m in enumerate(line):
            a = (bar - 1) * 4 * n + i * n
            y[a:a + n] += env * saw(hz(m), n)
    back = 0.5 * (saw(hz(57), total) + saw(hz(64), total))   # A3 + E4, held, each voice 6 dB under the lead
    y = y + back + 0.01 * np.random.default_rng(0).standard_normal(total)
    f = os.path.join(d, 'lead_with_backing.wav')
    sf.write(f, 0.3 * y / np.abs(y).max(), SR)
    yield d, p, f
    shutil.rmtree(d, ignore_errors=True)


def rows(out):
    body = '\n'.join(ln for ln in out.splitlines() if not ln.startswith('#'))
    return [(round(s * 4), m) for s, m, _, _ in parse_notes(body)]


def test_recovers_the_lead_over_a_backing_without_octave_doubling(song):
    d, p, f = song
    out = OPS['melody_consensus'](p, source=f, reps=REPS, phrase_bars=2)
    got = rows(out)
    want = [(4 * i, m) for i, m in enumerate(LEAD)]
    errors = len(set(want) ^ set(got))
    assert errors <= 1, out
    octave = [(s, m) for s, m in got for ws, wm in want if s == ws and abs(m - wm) == 12]
    assert not octave, out
    assert '# notes per bar: 4 4' in out, out
    assert '# confidence:' in out and '# octave: every note beats its octave' in out, out


def test_writes_the_phrase_into_a_track_at_every_repetition(song):
    d, p, f = song
    OPS['track_add'](p, 'lead')
    out = OPS['melody_consensus'](p, source=f, reps=REPS, phrase_bars=2, track='lead', write_at=[1, 5])
    assert '# written to lead at bars [1, 5]' in out, out
    rd = OPS['notes_read'](p, 'lead', bars=[5, 6], view='rel')
    assert [m for _, m in rows(rd)] == [m for _, m in rows(out)], rd


def test_refuses_a_repetition_outside_the_audio(song):
    d, p, f = song
    with pytest.raises(api.OpError, match='outside'):
        OPS['melody_consensus'](p, source=f, reps=[1, 8], phrase_bars=2)
