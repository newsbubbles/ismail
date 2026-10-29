"""Songs are made with synthesis unless the user asks for samples: an extracted hit is a target to fit, never a part
(a session cut a reference's kick and snare into the song after "too synthetic"; sound_extract's own description
said "use it or fit to it")."""
import os

import numpy as np
import soundfile as sf

from ismail import api

SKILL = os.path.join(os.path.dirname(__file__), '..', 'skills', 'ismail')


def _clicks(path, secs=4.0, sr=44100, bpm=120):
    y = np.zeros(int(secs * sr))
    n = int(0.03 * sr)
    hit = np.exp(-np.arange(n) / (0.004 * sr)) * np.sin(2 * np.pi * 180 * np.arange(n) / sr)
    for k in range(int(secs * bpm / 60)):
        s = int(k * 60 / bpm * sr)
        y[s:s + n] += 0.5 * hit[:len(y) - s]
    sf.write(path, np.stack([y, y], 1), sr)
    return path


def test_an_extracted_hit_says_it_is_a_target_not_a_part(tmp_path):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=2)
    api.project_set(root, reference=_clicks(str(tmp_path / 'ref.wav')))
    out = api.OPS["sound_extract"](root, 'hit', 'ref', bars=[1, 2], step=0)
    assert 'a target to fit' in out and 'only when the user asked for samples' in out, out
    assert 'use it or fit' not in api.OPS["sound_extract"].__doc__
    assert 'only when the user asked for samples' in api.OPS["sound_extract"].__doc__


def test_the_skill_says_synthesis_unless_the_user_asks_for_samples():
    read = lambda *p: open(os.path.join(SKILL, *p), encoding='utf8').read()
    assert 'Make the song with synthesis unless the user asks for samples' in read('SKILL.md')
    assert 'use the slice' not in read('references', 'recreate.md')
    assert 'whenever the user wants it' not in read('SKILL.md')
