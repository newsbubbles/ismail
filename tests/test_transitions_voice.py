"""The transitions voice (fx family): the note picks the effect, sweeps last the note, levels sane."""
import numpy as np
import pytest

from ismail import voices

SR = 44100


def _hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _play(m, gate, tail=1.0):
    v = voices.load('transitions')
    t = np.arange(int((gate + tail) * SR)) / SR
    return v.voice(_hz(m), t, 1.0, gate, SR)


@pytest.mark.parametrize('m', [24, 26, 28, 29])        # C1 impact, D1 uplifter, E1 downlifter, F1 reverse crash
def test_transitions_voice_renders_each_effect_at_a_sane_level(m):
    y = _play(m, 1.0)
    assert y.shape[0] == 2 and np.all(np.isfinite(y))
    assert 0.05 < np.max(np.abs(y)) < 2.5        # the sweeps peak near 1.7 at full velocity (INFO says so)


def test_transitions_voice_notes_pick_different_effects():
    ys = [_play(m, 1.0).mean(0) for m in (24, 26, 28, 29)]
    for i in range(4):
        for j in range(i + 1, 4):
            assert abs(np.corrcoef(ys[i], ys[j])[0, 1]) < 0.5


def _env(y, hop=441):
    m = y.mean(0)
    k = len(m) // hop * hop
    return np.sqrt(np.mean(m[:k].reshape(-1, hop) ** 2, axis=1))


@pytest.mark.parametrize('gate', [0.5, 2.0])
def test_transitions_voice_sweeps_last_the_note(gate):
    up = _env(_play(26, gate))
    assert abs(np.argmax(up) * 0.01 - gate) < 0.1              # the uplifter peaks where the note ends
    for m in (28, 29):                                         # downlifter and reverse crash stop with the note
        e = _env(_play(m, gate))
        assert e[int((gate + 0.05) / 0.01):].max() < 0.01 * e.max()
    crash = _env(_play(29, gate))
    assert abs(np.argmax(crash) * 0.01 - gate) < 0.1           # the reverse crash swells into the note's end
