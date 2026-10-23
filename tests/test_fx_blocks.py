"""Every live effect processor gives the same output whether the signal arrives whole or in blocks of any size."""
import numpy as np
import pytest

from ismail.live import fx_blocks as F
from ismail.dsp import SR

N = int(1.5 * SR)
BPM = 128.0


def signal_pair(seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(N) / SR
    x = np.zeros((2, N))
    for k, f in enumerate([110, 165, 220, 330]):
        on = int(k * 0.3 * SR)
        env = np.exp(-np.maximum(t - on / SR, 0) * 3) * (t >= on / SR)
        x[0] += 0.4 * env * np.sin(2 * np.pi * f * t)
        x[1] += 0.4 * env * np.sin(2 * np.pi * f * 1.003 * t + 0.3)
    x += 0.05 * rng.standard_normal((2, N)) * (np.sin(2 * np.pi * 2 * t) > 0)
    key = np.zeros((2, N))
    for on in np.arange(0, N, int(0.25 * SR)):
        key[:, on:on + 2000] += np.exp(-np.arange(min(2000, N - on)) / 300)
    return x, key


class FullBlock(F.Block):
    """Slices of whole-signal curves/sources, as the live engine would hand them over."""

    def __init__(self, pos, n, curves, key, onsets):
        self.pos, self.n = pos, n
        self.curves, self.k, self.ons = curves, key, onsets
        self.gr = []

    def param(self, name, default):
        c = self.curves.get(name)
        return default if c is None else c[self.pos:self.pos + self.n]

    def key(self, track):
        return self.k[:, self.pos:self.pos + self.n]

    def onsets(self, track, lo, hi):
        return self.ons[(self.ons >= lo) & (self.ons < hi)]

    def modulator(self, ref):
        return self.k[0, self.pos:self.pos + self.n]

    def note_gr(self, gr_db):
        self.gr.append(gr_db)


def run(fx, x, key, curves, sizes):
    fx = F.normalize(fx)
    p = F.make(fx, F.Env(SR, BPM, 1.0, 0))
    ons = np.arange(0, N, int(0.25 * SR)).astype(float) + 0.5
    out, pos, i = [], 0, 0
    while pos < x.shape[1]:
        n = min(sizes[i % len(sizes)], x.shape[1] - pos)
        out.append(p.process(np.ascontiguousarray(x[:, pos:pos + n]), FullBlock(pos, n, curves, key, ons)))
        pos += n
        i += 1
    return np.concatenate(out, axis=1)


CASES = [
    {'type': 'gain', 'gain_db': -3, 'pan': 0.3},
    {'type': 'eq', 'bands': [{'type': 'lowcut', 'freq': 120, 'slope': 24}, {'type': 'peak', 'freq': 900, 'gain_db': 5}]},
    {'type': 'filter', 'mode': 'lp24', 'cutoff': 1200, 'res': 0.5, 'lfo_rate_beats': 0.5, 'lfo_depth_oct': 1.5},
    {'type': 'filter', 'mode': 'ladder', 'cutoff': 800, 'res': 0.7},
    {'type': 'distortion', 'mode': 'tanh', 'drive_db': 18, 'mix': 0.6, 'tone_hz': 5000},
    {'type': 'distortion', 'mode': 'asym', 'drive_db': 12},
    {'type': 'distortion', 'mode': 'bitcrush', 'bits': 6, 'rate_hz': 8000},
    {'type': 'bitcrush', 'bits': 5, 'rate_hz': 5000, 'mix': 0.8},
    {'type': 'compressor', 'threshold_db': -24, 'ratio': 6, 'sc_hpf_hz': 100},
    {'type': 'compressor', 'sidechain': 'k', 'threshold_db': -30, 'ratio': 8},
    {'type': 'duck', 'every_beats': 1, 'depth_db': -10},
    {'type': 'duck', 'source': 'k', 'depth_db': -8, 'hold_ms': 20},
    {'type': 'gate', 'pattern': 'x.xxX.x-', 'step': 0.25},
    {'type': 'delay', 'time_beats': 0.75, 'feedback': 0.5, 'pingpong': True},
    {'type': 'reverb', 'size': 0.8, 'mix': 0.3, 'predelay_ms': 15},
    {'type': 'hall', 'rt60': 1.2, 'mix': 0.4},
    {'type': 'chorus', 'mix': 0.5},
    {'type': 'flanger', 'rate_beats': 2, 'feedback': 0.7},
    {'type': 'phaser', 'rate_beats': 1, 'stages': 6},
    {'type': 'tremolo', 'rate_beats': 0.5, 'depth': 0.7, 'mode': 'pan'},
    {'type': 'width', 'width': 1.6, 'mono_below_hz': 150},
    {'type': 'limiter', 'gain_db': 12, 'ceiling_db': -1},
    {'type': 'formant', 'vowel': 'a'},
    {'type': 'vocoder', 'modulator': 'k', 'bands': 12},
    {'type': 'fuzz'},
    {'type': 'fuzz', 'silicon': False, 'input_db': -6, 'tone_hz': 4000, 'mix': 0.7},
    {'type': 'univibe'},
    {'type': 'univibe', 'mode': 'vibrato', 'rate_hz': 6.0},
    {'type': 'amp'},
    {'type': 'amp', 'gain': 2.0, 'presence': 8, 'hiss_db': -75, 'hum_db': -82},
    {'type': 'cab'},
    {'type': 'cab', 'mic': 0.7},
    {'type': 'rotary'},
    {'type': 'rotary', 'speed': 1.0, 'drive': 0.4},
    {'type': 'tape'},
    {'type': 'tape', 'wow': 0.0, 'flutter': 0.0, 'drive': 0.8},
    {'type': 'wah', 'pos': 0.3},
    {'type': 'wah', 'auto': 0.6},
    {'type': 'spatial'},
    {'type': 'spatial', 'out': 'headphones', 'occlusion': 0.5, 'ground': 'hard', 'spread': 0.4, 'size': 0.9},
    {'type': 'spatial', 'ctc': 1.0, 'trajectory': {'orbit': {'radius': 3, 'period_s': 1.2, 'elevation': 20}}},
    {'type': 'spatial', 'out': 'headphones', 'trajectory': {'flyby': {'from': [-30, 5, 0], 'to': [30, 5, 0],
                                                                       'speed_mps': 40}}},
    {'type': 'spatial', 'doppler': 0, 'room': 0.0, 'trajectory': {
        'keys': [[0, -2, 1, 0], [0.4, {'az': 180, 'el': 30, 'dist': 4}], [0.9, 2, 1, 0]], 'interp': 'smooth',
        'loop': True}},
]


@pytest.mark.parametrize('fx', CASES, ids=lambda f: f['type'] + ':' + str(f.get('mode', f.get('source', ''))))
def test_blocks_match_whole(fx):
    x, key = signal_pair()
    curves = {}
    auto = F.AUTOMATABLE.get(fx['type'], ())
    if 'mix' in auto:
        curves['mix'] = np.linspace(0.2, 0.9, N)
    if 'cutoff' in auto:
        curves['cutoff'] = np.geomspace(300, 6000, N)
    if 'drive_db' in auto:
        curves['drive_db'] = np.linspace(6, 20, N)
    if fx['type'] == 'spatial':            # position and listener moving under automation
        t = np.arange(N) / SR
        curves.update(x=4 * np.sin(2 * np.pi * 0.7 * t), y=np.linspace(3, -2, N), z=np.linspace(-1, 2, N),
                      occlusion=np.linspace(0, 1, N), listener_yaw=np.linspace(0, 270, N),
                      spread=np.linspace(0, 1, N), ctc=np.linspace(1, 0, N), room=np.linspace(0.8, 0.1, N))
    whole = run(fx, x, key, curves, [N])
    blocks = run(fx, x, key, curves, [1, 37, 512, 4099, 511, 2048])
    scale = max(np.max(np.abs(whole)), 1e-9)
    assert whole.shape == blocks.shape
    assert np.max(np.abs(whole - blocks)) / scale < 1e-9


def test_offline_apply_fx_compensates_latency():
    """apply_fx of a latency effect lines up with its input (a limiter below the ceiling is transparent)."""
    x, _ = signal_pair()

    class Ctx:
        sr, bpm, offset_samples = SR, BPM, 0

        def param(self, idx, name, default):
            return default

        def note_gr(self, idx, gr):
            pass
    y = F.apply_fx(x * 0.1, F.normalize({'type': 'limiter', 'ceiling_db': -0.3}), Ctx(), 0)
    assert np.max(np.abs(y - x * 0.1)) < 1e-12
