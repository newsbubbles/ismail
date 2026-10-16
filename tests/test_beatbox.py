"""The beatbox classifier (ismail.phone.beatbox, ledger:M222) on a generic stand-in: dark mouth thumps, bright hard
bursts and quiet airy ticks, as an earbud mic hears a beatbox (the kick has little under 250 Hz), at levels that
move from hit to hit and take to take, so only a rule that ranks the hits against each other gets them right."""
import numpy as np
from scipy import signal

from ismail.phone import beatbox as B

SR = B.SR
STEP = 0.125                                     # a 16th at 120 BPM
BAR = 'K..hS.hKK.hhS.h.'                         # one bar: K kick, S snare, h hat


def _burst(rng, lo_hz, hi_hz, decay_s, n=int(0.12 * SR)):
    z = rng.normal(0, 1, n)
    sos = signal.butter(4, [lo_hz, hi_hz], btype='bandpass', fs=SR, output='sos')
    z = signal.sosfilt(sos, z) * np.exp(-np.arange(n) / (decay_s * SR))
    return z / (np.abs(z).max() + 1e-12)


def take(bars=4, gain=1.0, seed=0):
    """(audio, [(t, class)]): the bar pattern played `bars` times. Kicks are a 150-700 Hz thump with a 4 ms
    700-2500 Hz attack (centroid about 500 Hz, under 35% of the energy below 250 Hz: an absolute 'lo > 0.35' rule
    misses them), each at its own level over 18 dB; snares a hard 2.5-9 kHz burst; hats a 6-10 kHz tick 12 to 16 dB
    under the snares."""
    rng = np.random.default_rng(seed)
    y = np.zeros(int((bars * 16 * STEP + 0.5) * SR))
    truth = []
    for b in range(bars):
        for j, c in enumerate(BAR):
            if c == '.':
                continue
            t = 0.1 + (b * 16 + j) * STEP
            if c == 'K':
                x = _burst(rng, 150, 700, 0.035) + _burst(rng, 700, 2500, 0.004)
                x, k = 2.0 * x * 10 ** (rng.uniform(-18, 0) / 20), 'kick'
            elif c == 'S':
                x, k = _burst(rng, 2500, 9000, 0.04) * 10 ** (rng.uniform(-3, 0) / 20), 'snare'
            else:
                x, k = _burst(rng, 6000, 10000, 0.012) * 10 ** (rng.uniform(-16, -12) / 20), 'hat'
            a = int(t * SR)
            y[a:a + len(x)] += x
            truth.append((t, k))
    y += rng.normal(0, 1e-4, len(y))                     # a little room noise
    return (gain * 0.5 * y).astype(np.float32), truth


def _match(hits, truth, tol=0.03):
    """[(true class, read class or None)] for every true hit, and the hits that match no true hit"""
    used, pairs = set(), []
    for t, k in truth:
        j = min(range(len(hits)), key=lambda i: abs(hits[i]['t'] - t)) if hits else None
        if j is not None and abs(hits[j]['t'] - t) <= tol and j not in used:
            used.add(j)
            pairs.append((k, hits[j]['class']))
        else:
            pairs.append((k, None))
    return pairs, [h for i, h in enumerate(hits) if i not in used]


def test_dark_is_kick_bright_hard_is_snare_quiet_bright_is_hat():
    y, truth = take()
    hits = B.read(y, SR)
    pairs, extra = _match(hits, truth)
    assert not extra, extra
    wrong = [(k, r) for k, r in pairs if k != r]
    assert not wrong, wrong
    assert sum(k == 'kick' for k, _ in pairs) == 12                  # 3 a bar
    kicks = [h for h in hits if h['class'] == 'kick']
    assert all(h['lo'] < 0.35 for h in kicks)                          # dark, but not much under 250 Hz
    assert all(h['margin'] is not None and h['margin'] > 0 for h in hits)


def test_the_whole_take_quieter_or_louder_reads_the_same():
    """The level of a phone take depends on where the phone was: 26 dB down or 6 dB up changes nothing."""
    base = [h['class'] for h in B.read(*take(seed=3)[:1], SR)]
    for g in (0.05, 2.0):
        y, _ = take(gain=g, seed=3)
        assert [h['class'] for h in B.read(y, SR)] == base


def test_the_absolute_band_rule_misses_these_kicks():
    """The rule that drifted (hi > 0.3 hat, lo > 0.35 kick, else snare) reads the stand-in's kicks as snares; the
    relative rule reads them all."""
    y, truth = take(seed=5)
    hits = B.read(y, SR)
    old = ['hat' if h['hi'] > 0.3 else ('kick' if h['lo'] > 0.35 else 'snare') for h in hits]
    n_true = sum(k == 'kick' for _, k in truth)
    assert old.count('kick') < n_true // 2
    assert [h['class'] for h in hits].count('kick') == n_true


def test_another_rate_reads_the_same_hits():
    y, truth = take(seed=7)
    y44 = signal.resample_poly(y, 2, 1).astype(np.float32)
    a, b = B.read(y, SR), B.read(y44, 2 * SR)
    assert [h['class'] for h in a] == [h['class'] for h in b]
    assert max(abs(p['t'] - q['t']) for p, q in zip(a, b)) < 0.015


def test_the_speakers_beat_under_the_take_is_not_read_as_hits():
    """A four-on-the-floor from the room speaker bleeds into the mic; the music under the take (ref) takes it off."""
    y, truth = take(bars=2, seed=9)
    rng = np.random.default_rng(11)
    spk = np.zeros_like(y)
    for i in range(16):                                           # quarter notes, off the person's 16ths
        a = int((0.1 + 0.0625 + i * 4 * STEP / 2) * SR)
        x = _burst(rng, 60, 3000, 0.05) * 0.5
        spk[a:a + len(x)] += x[:len(spk) - a]
    hits = B.read(y + 0.3 * spk, SR, ref=spk)
    pairs, extra = _match(hits, truth)
    assert not extra
    assert sum(k == r for k, r in pairs) >= len(truth) - 1


def test_classify_on_its_own():
    h = lambda c, lo, hi, w: dict(centroid_hz=c, lo=lo, hi=hi, strength=w)       # noqa: E731
    # dark vs bright an octave or more apart; among the bright, the hard ones are snares
    hits = [h(300, .2, .02, .5), h(350, .25, .03, .6), h(5000, 0, .6, .9), h(6000, 0, .7, .3), h(5500, 0, .65, .95),
            h(6500, 0, .7, .25)]
    assert B.classify(hits) == ['kick', 'kick', 'snare', 'hat', 'snare', 'hat']
    # a bright hit with more body than air is a kick with a click on it
    assert B.classify(hits + [h(3000, .4, .2, .8)])[-1] == 'kick'
    # one kind of sound (no octave between them): the full-range band rule
    same = [h(2000, .5, .1, .5), h(2100, .1, .5, .9), h(2200, .1, .5, .3), h(2050, .1, .5, .85)]
    assert B.classify(same)[0] == 'kick'
    # under 4 bright hits: all snares
    assert B.classify([h(300, .2, 0, .5), h(320, .2, 0, .5), h(5000, 0, .6, .9), h(6000, 0, .6, .2)])[2:] == \
        ['snare', 'snare']
    assert B.classify([]) == []
    assert B.split([1, 1.1, 1.2, 1.15], 1.0) is None
