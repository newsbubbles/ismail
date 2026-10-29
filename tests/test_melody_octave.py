"""The melody reader keeps a quick octave jump: pYIN's smoothing held the lower pitch through it, so the upper notes
of an eighth-note octave bounce read an octave low (ledger:M220). Run: python -m pytest tests/test_melody_octave.py -q"""
import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail.notation import midi_to_hz

SR = A.ASR
BPM = 124


def _tone(f, n, parts):
    """Partials 1..parts at 1/k (parts=1: a plain sine), 5 ms attack, held for 95% of the note, 10 ms release."""
    t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, parts + 1))
    return x * np.minimum(1, t / 0.005) * np.clip((0.95 * n / SR - t) / 0.01 + 1, 0, 1)


def _clip(path, line, parts, bars=3):
    step = SR * 60 / BPM / 2                                # eighth notes
    y = np.zeros(int(step * 8 * bars))
    for i in range(int(len(y) // step)):
        a, b = int(i * step), int((i + 1) * step)
        y[a:b] += _tone(midi_to_hz(line[i % len(line)]), b - a, parts)
    sf.write(path, 0.5 * y / np.abs(y).max(), SR)
    return str(path)


@pytest.mark.parametrize('parts', [1, 6])
def test_octave_bounce_reads_both_octaves(tmp_path, parts):
    line = [45, 45, 57, 45]                                 # A2 A2 A3 A2: one jump up an octave and straight back
    path = _clip(tmp_path / f'bounce{parts}.wav', line, parts)
    notes, txt = A.melody(path, A.Grid(BPM), [2, 2], fmin='C1', fmax='C5')
    got = {round(n[0] * 2): n[1] for n in notes}
    assert [got.get(i) for i in range(8)] == [line[i % 4] for i in range(8)], txt


def test_octave_check_keeps_a_true_low_note():
    n = SR // 4
    t = np.arange(n) / SR
    f = midi_to_hz(40)
    saw = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 8))
    no_fund = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(2, 8))   # fundamental gone, 3f still there
    sine_up = np.sin(2 * np.pi * 2 * f * t)
    assert A._octave_up(saw, 0, 0.25, 40.0, 72) == 40.0
    assert A._octave_up(no_fund, 0, 0.25, 40.0, 72) == 40.0
    assert A._octave_up(sine_up, 0, 0.25, 40.0, 72) == 52.0
    assert A._octave_up(sine_up, 0, 0.25, 40.0, 48) == 40.0                    # never above fmax
