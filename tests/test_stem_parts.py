"""A source with two parts in it: stem_parts says which band moves on its own, and analyze_melody warns that a
second pitch shares the source. Run: python -m pytest tests/test_stem_parts.py -q"""
import numpy as np
import pytest
import soundfile as sf

from ismail import analysis as A

SR = 22050
BPM = 120


def _tone(f, n, decay, parts=5):
    t = np.arange(n) / SR
    return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, parts + 1)) * np.exp(-decay * t)


def _hz(name):
    import librosa
    return librosa.note_to_hz(name)


def _clips(tmp_path):
    """4 bars at 120 bpm: a plucked lead in 16ths (8 notes a second) alone, and over a held bass that changes
    once a beat (2 a second), about as loud as the lead: the melody reader follows the bass there, as it did on a
    record."""
    beat = int(SR * 60 / BPM)
    step = beat // 4
    n = beat * 16
    lead = np.zeros(n)
    line = ['E4', 'F#4', 'G4', 'A4', 'B4', 'A4', 'G4', 'F#4']
    for i in range(n // step):
        lead[i * step:(i + 1) * step] += _tone(_hz(line[i % len(line)]), step, 9)
    bass = np.zeros(n)
    roots = ['B2', 'A2', 'G2', 'F#2']
    ramp = np.minimum(1, np.arange(beat) / (0.03 * SR))
    for i in range(16):
        bass[i * beat:(i + 1) * beat] += _tone(_hz(roots[i % 4]), beat, 0.3, parts=3) * ramp
    lead /= np.abs(lead).max()
    bass *= 0.7 / np.abs(bass).max()
    alone, both = tmp_path / 'alone.wav', tmp_path / 'both.wav'
    sf.write(alone, 0.5 * lead, SR)
    sf.write(both, 0.3 * (lead + bass), SR)
    return str(alone), str(both)


@pytest.fixture(scope='module')
def clips(tmp_path_factory):
    return _clips(tmp_path_factory.mktemp('parts'))


def test_stem_parts_finds_the_band_a_second_part_plays_in(clips):
    alone, both = clips
    g = A.Grid(BPM)
    d1, t1 = A.stem_parts(alone, g, [1, 4])
    assert d1['apart'] == [] and 'one part' in t1
    assert 7 <= d1['note_rate'] <= 9
    d2, t2 = A.stem_parts(both, g, [1, 4])
    assert 'bass' in d2['apart'] and 'mid' not in d2['apart'] and 'WARNING' in t2
    assert d2['bands']['bass']['share'] < d2['bands']['mid']['share']
    assert 'bar   1: B2 A2 G2 F#2' in t2                    # the low part's pitch, beat by beat


def test_melody_reports_its_confidence_and_warns_of_a_second_pitch(clips):
    alone, both = clips
    g = A.Grid(BPM)
    _, t1 = A.melody(alone, g, [1, 2], fmin='C3', fmax='C6')
    assert '# confidence: voicing' in t1 and 'WARNING' not in t1
    _, t2 = A.melody(both, g, [1, 2], fmin='C1', fmax='C6')
    assert 'WARNING' in t2 and 'second part shares this source' in t2 and 'over the line read' in t2
