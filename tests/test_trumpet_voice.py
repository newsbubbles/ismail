"""The trumpet performer (ismail/voices/brass/trumpet.py, ledger:M331): its manual, its range in tune, each mute, a
slur with no new tongue and a gap that tongues, the params that are off by default leaving the render untouched, the
same render for the same song time, and the same render as the song study's voice it was promoted from.

tests/fixtures/trumpet_kit_winds_6fe6db1.py is a saved copy of the study's voice as the user passed it blind (the
winds song study at commit 6fe6db1): the library voice keeps its numbers, so every render here matches it sample for
sample."""
import importlib.util
import os

import numpy as np
import pytest

from ismail import voices
from ismail.voices.brass import trumpet

SR = 44100


def _study():
    """the song study's trumpet at 6fe6db1 (a saved copy in tests/fixtures/), loaded inside the ismail package"""
    path = os.path.join(os.path.dirname(__file__), 'fixtures', 'trumpet_kit_winds_6fe6db1.py')
    spec = importlib.util.spec_from_file_location('ismail._trumpet_kit_winds_6fe6db1', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def play(notes, secs, lanes=None, beat0=0.0, voice=trumpet, **params):
    return voice.perform(notes, int(secs * SR), SR, bpm=120.0, lanes=lanes or {}, beat0=beat0, **params)


def lane(secs, value):
    return np.full(int(secs * SR), float(value))


def env(y, ms=10):
    """level per 10 ms frame, dB"""
    x = y.mean(0)
    w = int(SR * ms / 1000)
    e = (x[:len(x) // w * w].reshape(-1, w) ** 2).mean(1)
    return 10 * np.log10(e + 1e-20)


def db(y, a, b):
    seg = y[:, int(a * SR):int(b * SR)]
    return 10 * np.log10(np.mean(seg ** 2) + 1e-30)


def centroid(y, a, b):
    x = y.mean(0)[int(a * SR):int(b * SR)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float((S * f).sum() / S.sum())


def cents_off(y, midi, a, b):
    """the strongest partial, read as the harmonic of the written note nearest to it: cents off that harmonic"""
    x = y.mean(0)[int(a * SR):int(b * SR)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), 8 * len(x)))
    f = np.fft.rfftfreq(8 * len(x), 1 / SR)
    S[f < 100] = 0
    pk = f[np.argmax(S)]
    f0 = 440 * 2 ** ((midi - 69) / 12)
    return 1200 * np.log2(pk / max(1, round(pk / f0)) / f0)


def sane(y):
    assert np.isfinite(y).all()
    assert np.abs(y).max() <= 1.0
    assert np.sqrt(np.mean(y ** 2)) > 1e-3


def test_it_is_a_library_performer_with_its_manual():
    assert voices.find('trumpet')[0] == 'built-in/brass'
    assert voices.entry_fns('trumpet')[0] == 'perform'
    info = trumpet.INFO
    assert info['range'].startswith('concert E3-D6')
    assert set(info['params']) == set(trumpet.DEFAULTS)
    assert set(info['lanes']) >= {'bend', 'vib', 'level', 'tongue', 'shake', 'fall', 'doit', 'plunger', 'top'}
    src = ' '.join(info['sources'])
    assert 'Harnum' in src and 'CC BY' in src


@pytest.mark.parametrize('midi', [52, 86])
def test_it_plays_its_whole_range_in_tune(midi):
    y = play([(0.3, midi, 1.5, 100)], 2.3)
    sane(y)
    assert abs(cents_off(y, midi, 0.7, 1.6)) < 30


def test_harder_blowing_is_louder_and_brighter():
    ys = [play([(0.3, 67, 1.5, v)], 2.3) for v in (22, 55, 127)]
    lv = [db(y, 0.6, 1.6) for y in ys]
    c = [centroid(y, 0.6, 1.6) for y in ys]
    assert lv[0] < lv[1] < lv[2] and c[0] < c[1] < c[2], (lv, c)
    assert c[2] > 2 * c[0], c


@pytest.mark.parametrize('mute', ['open', 'harmon', 'harmon_stem'])
def test_each_mute_renders(mute):
    y = play([(0.3, 67, 1.5, 100)], 2.3, mute=mute)
    sane(y)
    if mute != 'open':
        open_ = play([(0.3, 67, 1.5, 100)], 2.3)
        assert db(y, 0.6, 1.6) < db(open_, 0.6, 1.6) - 4          # a mute takes power out
        assert centroid(y, 0.6, 1.6) > centroid(open_, 0.6, 1.6)   # and the lows with it


@pytest.mark.parametrize('pos', [0.5, 1.0])
def test_the_plunger_lane_renders(pos):
    y = play([(0.3, 67, 1.5, 100)], 2.3, lanes={'plunger': lane(2.3, pos)})
    sane(y)
    assert db(y, 0.6, 1.6) < db(play([(0.3, 67, 1.5, 100)], 2.3), 0.6, 1.6) - 3


def join(gap):
    """two notes, G4 then A4: an overlap (gap < 0) slurs, a gap tongues; the deepest dip at the join re the held
    level"""
    j = 1.3 + max(gap, 0.0)
    y = play([(0.3, 67, 1.0 + (0.04 if gap < 0 else 0.0), 90), (j, 69, 1.0, 90)], 3.0)
    E = env(y)
    held = float(np.median(E[80:120]))
    return y, held - float(E[int((j - 0.03) * 100):int((j + 0.05) * 100)].min())


def test_a_phrase_slurs_on_an_overlap_and_tongues_on_a_gap():
    y_s, slur = join(-0.04)
    y_t, tongue = join(0.05)
    sane(y_s)
    sane(y_t)
    assert tongue > slur + 3.0, (slur, tongue)


def test_the_params_off_by_default_leave_the_render_untouched():
    """air_lowcut, chiff_db, noise_wander, h1_db at 0 and the top lane absent or 0 are the render without them;
    set, each one changes it."""
    notes = [(0.2, 67, 0.5, 90), (0.75, 69, 0.4, 70), (1.1, 72, 0.8, 100)]
    base = play(notes, 2.5)
    assert np.array_equal(base, play(notes, 2.5, air_lowcut=0.0, chiff_db=0.0, noise_wander=0.0, h1_db=0.0))
    assert np.array_equal(base, play(notes, 2.5, lanes={'top': lane(2.5, 0.0)}))
    for kw in ({'air_lowcut': 300.0}, {'chiff_db': -30.0}, {'noise_wander': 3.0}, {'h1_db': 3.0}):
        assert not np.array_equal(base, play(notes, 2.5, **kw)), kw
    assert not np.array_equal(base, play(notes, 2.5, lanes={'top': lane(2.5, 6.0)}))


def test_the_same_song_time_renders_the_same():
    """Draws are keyed on song milliseconds (beat0 plus the note's start), so a window renders its phrases as the
    whole part does."""
    notes = [(0.5, 67, 1.04, 90), (1.5, 71, 1.0, 90), (4.5, 74, 1.0, 85)]   # the first phrase's release is over by 4 s
    a = play(notes, 6.5)
    assert np.array_equal(a, play(notes, 6.5))
    later = [(st - 4.0, m, d, v) for st, m, d, v in notes if st >= 4.0]
    win = play(later, 2.5, beat0=8.0)                              # 4 s at 120 BPM
    err = 10 * np.log10(np.mean((a[:, 4 * SR:] - win) ** 2) + 1e-30) - 10 * np.log10(np.mean(win ** 2))
    assert err < -100, err
    assert not np.allclose(win, play(later, 2.5, beat0=9.0))


TECHNIQUES = {'scoop': 3.0, 'fall': 7.0, 'doit': 6.0, 'shake': 3.5, 'flutter': 1.0, 'growl': 0.8,
              'halfvalve': 1.0, 'plunger': 1.0, 'bend': 0.5, 'vib': 20.0, 'jitter': 6.0, 'top': 4.0}


@pytest.mark.parametrize('case', ['phrase', 'harmon', 'harmon_stem', 'techniques', 'params'])
def test_it_renders_as_the_song_study_voice_did(case):
    """The library voice keeps the study's numbers: its defaults, each mute, every technique lane and the params
    render sample for sample as the saved copy of the study's voice."""
    study = _study()
    phrase = [(0.2, 64, 0.45, 100), (0.6, 67, 0.3, 80), (0.95, 72, 0.6, 90), (1.6, 72, 0.4, 60), (2.1, 76, 0.9, 110)]
    args, kw = (phrase, 3.5), {'beat0': 2.0}
    if case in ('harmon', 'harmon_stem'):
        kw['mute'] = case
    elif case == 'techniques':
        n = int(4.0 * SR)
        lanes = {}
        for i, (name, v) in enumerate(TECHNIQUES.items()):
            c = np.zeros(n)
            c[int((0.2 + 0.3 * i) * SR):int((0.5 + 0.3 * i) * SR)] = v
            lanes[name] = c
        args, kw = ([(0.2 + 0.3 * i, 67 + i % 5, 0.32, 95) for i in range(len(TECHNIQUES))], 4.0), {'lanes': lanes}
    elif case == 'params':
        kw.update(air_lowcut=300.0, chiff_db=-30.0, noise_wander=3.0, h1_db=2.0, floor_db=-90.0, partial_wander=6.0,
                  vib=15.0, breath=0.4, room=0.4, tongue=0.2)
    ours = play(*args, **kw)
    sane(ours)
    assert np.array_equal(ours, play(*args, voice=study, **kw))
