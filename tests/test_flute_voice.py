"""The flute performer (ismail/voices/winds/flute.py, ledger:M321): its range, blowing that brightens, a slur with no
second attack, a gap that tongues, a long gap that breathes, a short pickup that is not swallowed by its attack, and
the same render for the same song time."""
import numpy as np
import pytest

from ismail import voices
from ismail.voices.winds import flute

SR = 44100


def play(notes, secs, bpm=120.0, beat0=0.0, **params):
    return flute.perform(notes, int(secs * SR), SR, bpm=bpm, lanes={}, beat0=beat0, **params)


def env(y, ms=10):
    """level per 10 ms frame, dB"""
    x = y.mean(0)
    w = int(SR * ms / 1000)
    e = (x[:len(x) // w * w].reshape(-1, w) ** 2).mean(1)
    return 10 * np.log10(e + 1e-20)


def level(E, a, b):
    """median 10 ms level from a to b seconds"""
    return float(np.median(E[int(round(a * 100)):int(round(b * 100))]))


def peak_hz(y, a, b):
    x = y.mean(0)[int(a * SR):int(b * SR)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), 8 * len(x)))
    f = np.fft.rfftfreq(8 * len(x), 1 / SR)
    S[f < 150] = 0
    return f[np.argmax(S)]


def centroid(y, a, b):
    x = y.mean(0)[int(a * SR):int(b * SR)]
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float((S * f).sum() / S.sum())


def test_it_is_a_library_performer_with_its_manual():
    assert voices.find('flute')[0] == 'built-in/winds'
    assert voices.entry_fns('flute')[0] == 'perform'
    info = flute.INFO
    assert info['range'].startswith('B3-Db7')
    assert set(info['params']) == set(flute.DEFAULTS)
    assert 'Scott Goff' in ' '.join(info['sources']) and 'CC BY-SA 2.0' in ' '.join(info['sources'])


@pytest.mark.parametrize('midi', [59, 97])
def test_it_plays_its_whole_range_in_tune(midi):
    y = play([(0.3, midi, 1.5, 90)], 2.5, vib=0)
    hz = peak_hz(y, 0.8, 1.6)
    cents = 1200 * np.log2(hz / (440 * 2 ** ((midi - 69) / 12)))
    assert abs(cents) < 30, cents
    assert np.sqrt(np.mean(y ** 2)) > 1e-3 and np.isfinite(y).all()


def test_harder_blowing_is_brighter():
    c = [centroid(play([(0.3, 72, 1.5, v)], 2.5, vib=0), 0.8, 1.6) for v in (40, 80, 127)]
    assert c[0] < c[1] < c[2], c
    assert c[2] > 1.3 * c[0], c


def join(gap, **params):
    """two notes, C5 then D5: an overlap (gap < 0) slurs, a gap tongues; the level around the join"""
    j = 1.3 + max(gap, 0.0)
    y = play([(0.3, 72, 1.0 + (0.04 if gap < 0 else 0.0), 90), (j, 74, 1.0, 90)], 3.5, vib=0, **params)
    E = env(y)
    held = level(E, 0.8, 1.2)
    dip = held - float(E[int((j - 0.03) * 100):int((j + 0.05) * 100)].min())
    rise = level(E, j, j + 0.04) - level(E, j + 0.3, j + 0.8)      # the new note's first 40 ms re its plateau
    return E, held, dip, rise


def test_a_slur_has_no_second_attack():
    _, _, dip, rise = join(-0.04)
    assert dip < 4.0 and rise > -3.0, (dip, rise)
    _, _, dip_t, _ = join(0.05)
    assert dip_t > dip + 4.0, (dip, dip_t)


def test_a_short_gap_tongues_under_the_same_breath():
    E, held, dip, _ = join(0.05)
    assert dip > 6.0, dip
    assert level(E, 1.3, 1.35) > held - 40, 'a tongued gap keeps the air going'


def test_a_long_gap_is_a_breath_with_an_inhale():
    quiet = None
    for inhale in (0.0, 1.0):
        E, held, _, _ = join(0.6, inhale=inhale)
        loudest = float(E[int(1.45 * 100):int(1.8 * 100)].max())    # the gap, clear of the release and the attack
        if inhale == 0.0:
            quiet = loudest
            assert loudest < held - 80, (held, loudest)             # the tone and the air stop
        else:
            assert held - 50 < loudest < held - 15, (held, loudest)  # an audible inhale, well under the tone
    assert loudest > quiet + 40


def test_a_short_pickup_is_not_swallowed_by_its_attack():
    """ledger:M321 finding 1: a phrase's 8th-note pickup (0.26 s at 136 BPM) slurred into a held note rose over most
    of its length under a breath attack and sat 7-14 dB under the note after it (the attack was capped only on notes
    under 0.2 s). The cap now scales with the note's length: 40-120 ms into the pickup it is within 7 dB of the held
    note's level on every pair (the old rule: up to 10.3 dB under, median 5.3)."""
    under = []
    for a, b in [(69, 74), (70, 77), (74, 81), (72, 79), (67, 72), (77, 81), (62, 67), (79, 84)]:
        y = play([(0.5, a, 0.26 + 0.044, 85), (0.76, b, 1.6, 85)], 3.0, bpm=136.0, entry_vel=0)
        E = env(y)
        under.append(level(E, 1.06, 1.96) - level(E, 0.54, 0.62))
    assert max(under) < 7.0 and np.median(under) < 4.0, np.round(under, 1)


def test_entry_vel_blows_only_a_short_first_note_harder():
    line = [(0.5, 72, 0.3, 80), (0.85, 74, 1.2, 80)]                 # a pickup (under a beat), then a new phrase
    a = play(line, 2.5, entry_vel=0)
    b = play(line, 2.5, entry_vel=8)
    assert not np.allclose(a[:, :int(0.8 * SR)], b[:, :int(0.8 * SR)])
    long_first = [(0.5, 72, 1.2, 80)]
    assert np.array_equal(play(long_first, 2.5, entry_vel=0), play(long_first, 2.5, entry_vel=8))


def test_the_same_song_time_renders_the_same():
    """Draws are keyed on song milliseconds (beat0 plus the note's start), so a window renders its phrases as the
    whole part does."""
    notes = [(0.5, 72, 1.04, 90), (1.5, 76, 1.0, 90), (3.5, 79, 1.0, 85)]
    a = play(notes, 5.0)
    assert np.array_equal(a, play(notes, 5.0))
    win = play([(st - 3.0, m, d, v) for st, m, d, v in notes if st >= 3.0], 2.0, beat0=6.0)   # 3 s at 120 BPM
    err = 10 * np.log10(np.mean((a[:, 3 * SR:] - win) ** 2) + 1e-30) - 10 * np.log10(np.mean(win ** 2))
    assert err < -100, err
    assert not np.allclose(win, play([(st - 3.0, m, d, v) for st, m, d, v in notes if st >= 3.0], 2.0, beat0=7.0))
