"""The melody reader keeps a held note whole: onsets fire on a note-off click and inside a held tone (a wah), and the
tail past such a split kept pYIN's smeared pitch, so one note read as two (ledger:M226). A note struck again at the
same pitch still reads as two notes. Run: python -m pytest tests/test_melody_split.py -q"""
import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A
from ismail.notation import midi_to_hz

SR = A.ASR
BPM = 120
SPB = 60 / BPM


def _tone(f, n, wah=False):
    """A 12-partial saw at 1/k; wah=True: 40 partials through a lowpass swept 300 Hz to 2.4 kHz six times a second."""
    t = np.arange(n) / SR
    if not wah:
        return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 13))
    fc = 300 * 2 ** (3 * (0.5 + 0.5 * np.sin(2 * np.pi * 6 * t)))
    return sum(np.sin(2 * np.pi * f * k * t) / k / np.sqrt(1 + (f * k / fc) ** 4) for k in range(1, 41))


def _clip(path, notes, length=1.0, release=0.0, decay=0.0, wah=False):
    """notes: (beat, midi, beats). Each note sounds for `length` of its beats, 5 ms attack; release 0 stops it dead
    (a note-off click); decay > 0 falls from full to 30% with that time constant (struck again, it rises)."""
    y = np.zeros(int(12 * SPB * SR))
    for b, m, d in notes:
        a, n = int(b * SPB * SR), int(d * length * SPB * SR)
        t = np.arange(n) / SR
        env = np.minimum(1, t / 0.005)
        if decay:
            env = env * (0.3 + 0.7 * np.exp(-t / decay))
        if release:
            env = env * np.clip((n / SR - t) / release, 0, 1)
        y[a:a + n] += _tone(midi_to_hz(m), n, wah) * env
    sf.write(path, 0.5 * y / np.abs(y).max(), SR)
    return str(path)


def _read(path):
    notes, txt = A.melody(path, A.Grid(BPM), [1, 2], fmin='C1', fmax='C6')
    return [(n[0], n[1]) for n in notes], notes, txt


@pytest.mark.parametrize('wah', [False, True])
def test_held_notes_read_whole(tmp_path, wah):
    line = [(0, 50, 4), (4, 55, 2), (6, 45, 2)]             # D3 a bar, G3 and A2 two beats each, legato
    got, notes, txt = _read(_clip(tmp_path / f'held{int(wah)}.wav', line, wah=wah))
    assert got == [(0.0, 50), (4.0, 55), (6.0, 45)], txt
    assert [n[2] for n in notes] == [4.0, 2.0, 2.0], txt


def test_note_off_click_leaves_no_tail(tmp_path):
    line = [(i * 0.5, [50, 53, 57, 53][i % 4], 0.5) for i in range(16)]   # eighths held 60%, stopped dead
    got, _, txt = _read(_clip(tmp_path / 'offs.wav', line, length=0.6))
    assert got == [(float(b), m) for b, m, _ in line], txt


@pytest.mark.parametrize('kind', ['gap', 'struck', 'stopped', 'legato'])
def test_repeated_notes_stay_separate(tmp_path, kind):
    step, kw = {'gap': (1, dict(length=0.95, release=0.01)),       # quarters with a short gap
                'struck': (0.5, dict(decay=0.08)),                 # a decaying note struck again, no gap
                'stopped': (0.5, dict(length=0.6)),                # eighths stopped dead: tail clicks, then attacks
                'legato': (1, {})}[kind]                           # flat synth note retriggered, no gap, no dip
    line = [(i * step, 50, step) for i in range(int(8 / step))]
    got, _, txt = _read(_clip(tmp_path / f'rep_{kind}.wav', line, **kw))
    assert got == [(float(b), 50) for b, _, _ in line], txt
