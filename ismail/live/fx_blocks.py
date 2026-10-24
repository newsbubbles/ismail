"""Live effect processors: every studio effect (ismail/fx.py) as a processor that keeps state between blocks
(make(fx, env).process(block)), so the live engine can run it on ~23 ms blocks. The studio functions stay the source
of truth; tests/test_live_parity.py holds each processor to its studio twin. An effect type with no processor here
is baked instead: the render workers run the studio function on each rendered note or phrase (graph.split_chain).
apply_fx(x, fx, ctx, idx) runs a processor over a whole window (the parity tests use it).
"""
import numpy as np
from scipy import signal

from . import dsp_blocks as dsp
from ..fx import (AUTOMATABLE, EQ_BAND_TYPES, FX_DEFAULTS, FX_TYPES, SR, VOWEL_FORMANTS, FxError,  # noqa: F401
                  _dry, _lfo_phase, _mix, _shape_env, eq_sos, hall_ir, normalize, rbj_sos)
from ..notation import parse_steps  # noqa: F401

# ------------------------------------------------------------------ processors
#
# Every effect is a processor: built once, then fed consecutive blocks; it keeps its own state (filter memories,
# delay lines, envelopes, reverb tails), so the output does not depend on how the signal is cut into blocks.
# Offline rendering feeds the whole window as one block (apply_fx); the live engine feeds ~12 ms blocks. A
# processor that needs to look ahead or buffer (limiter, oversampled distortion, hall) reports `latency` in
# samples: its output is its input delayed by that much. apply_fx compensates it, the live engine aligns tracks.

class Env:
    """What a processor is built with. offset_samples: processor-time sample of song beat 0 (grid effects). sync0:
    the same origin for the beat-synced LFOs (filter, phaser, flanger); 0 in the studio, where they never had one,
    and moved with offset_samples when the live engine changes tempo (Proc.retime). hz0: the origin of LFOs in Hz,
    which never moves (a tempo change does not jump their phase)."""

    def __init__(self, sr=SR, bpm=120.0, dry=1.0, offset_samples=0):
        self.sr, self.bpm, self.dry, self.offset_samples = sr, float(bpm), float(dry), int(offset_samples)
        self.sync0, self.hz0 = 0, self.offset_samples


class Block:
    """One block of processing. pos: processor-time sample of the block's first sample. The live engine and
    apply_fx subclass it; param() gives a scalar or an (n,) curve, key() another track's post-fx audio for this
    block, onsets() another track's note onsets (processor-time samples) in [lo, hi)."""
    pos = 0
    n = 0

    def param(self, name, default):
        return default

    def key(self, track):
        raise FxError(f"sidechain source {track!r} is not available here")

    def onsets(self, track, lo, hi):
        raise FxError(f"duck source {track!r} is not available here")

    def modulator(self, ref):
        raise FxError(f"vocoder modulator {ref!r} is not available here")

    def note_gr(self, gr_db):
        pass


class _Lag:
    """Delays a signal (history starts silent) or a param (history starts at its first value) by `L` samples
    across blocks, for latency alignment; scalar params become curves."""

    def __init__(self, L, signal=False):
        self.L = L
        self.signal = signal
        self.h = None

    def __call__(self, v, n):
        if self.L == 0:
            return v
        v = np.asarray(v, dtype=np.float64)
        if v.ndim == 0:
            v = np.full(n, float(v))
        elif v.shape[-1] < n:
            v = dsp.as_curve(v, n)
        if self.h is None:
            first = v[..., :1] * (0.0 if self.signal else 1.0)
            self.h = np.repeat(first, self.L, axis=-1)
        cat = np.concatenate([self.h, v], axis=-1)
        self.h = cat[..., -self.L:]
        return cat[..., :n]


class _Sos:
    """sosfilt that remembers its state (mono (n,) or stereo (2, n) input)."""

    def __init__(self, sos, channels=None):
        self.sos = np.ascontiguousarray(sos, dtype=np.float64)
        self.mono = channels is None
        self.zi = np.zeros((self.sos.shape[0], 1 if self.mono else channels, 2))

    def __call__(self, x):
        x = np.asarray(x, dtype=np.float64)
        if self.mono:
            return dsp.sos_s(np.ascontiguousarray(x[None, :]), self.sos, self.zi)[0]
        return dsp.sos_s(np.ascontiguousarray(x), self.sos, self.zi)


class _Lfilter:
    """lfilter(b, a) along the last axis of (2, n), remembering its state."""

    def __init__(self, b, a, channels=2):
        m = max(len(b), len(a))
        self.b = np.zeros(m)
        self.b[:len(b)] = b
        self.a = np.zeros(m)
        self.a[:len(a)] = a
        self.b /= self.a[0]
        self.a /= self.a[0]
        self.zi = np.zeros((channels, m - 1))

    def __call__(self, x):
        return dsp.lfilter_s(np.ascontiguousarray(x, dtype=np.float64), self.b, self.a, self.zi)


class Proc:
    latency = 0
    noise = 0.0         # RMS of the noise the processor makes by itself (an amp's hiss and hum), for dormancy
    hold_s = 0.0        # seconds of silent output while sound is still in flight inside it (a far source), for dormancy

    def __init__(self, fx, env):
        self.fx = fx
        self.env = env
        self.sr = env.sr

    def process(self, x, blk):
        raise NotImplementedError

    def retime(self, bpm, origin):
        """A live tempo change: from now on a beat lasts 60/bpm s and song beat 0 sits at processor sample
        `origin` (extrapolated back at the new tempo), so beat-synced effects stay on the grid."""
        self.env.bpm, self.env.offset_samples, self.env.sync0 = float(bpm), origin, origin


class _Bypass(Proc):
    def process(self, x, blk):
        return x


class _Gain(Proc):
    def process(self, x, blk):
        g = dsp.undb(blk.param('gain_db', self.fx['gain_db']))
        gl, gr = dsp.pan_gains(blk.param('pan', self.fx['pan']))
        return np.stack([x[0] * g * gl, x[1] * g * gr])


class _Eq(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.f = _Sos(eq_sos(fx['bands'], self.sr), 2) if fx['bands'] else None

    def process(self, x, blk):
        return x if self.f is None else self.f(x)


class _Filter(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.st = [dsp.filt_state(), dsp.filt_state()]

    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        cut = dsp.as_curve(blk.param('cutoff', fx['cutoff']), n)
        if fx['lfo_rate_beats'] and fx['lfo_depth_oct']:
            ph = _lfo_phase(n, None, fx['lfo_rate_beats'], self.env.bpm, self.sr, blk.pos - self.env.sync0)
            cut = cut * 2 ** (fx['lfo_depth_oct'] * dsp.lfo_wave(fx['lfo_shape'], ph))
        res = blk.param('res', fx['res'])
        wet = np.stack([dsp.filt_s(np.ascontiguousarray(x[c]), fx['mode'], cut, res, self.sr, 1.0, self.st[c])
                        for c in (0, 1)])
        return _mix(x, wet, blk.param('mix', fx['mix']))


class _Crush:
    """Bit depth + sample-and-hold decimation, holds counted from processor time 0 (block-independent)."""

    def __init__(self, bits, rate_hz, sr):
        self.q = 2 ** (bits - 1)
        self.hold = max(1, int(round(sr / rate_hz)))
        self.last = np.zeros(2)

    def __call__(self, x, pos):
        y = np.round(x * self.q) / self.q
        if self.hold == 1:
            return y
        n = x.shape[1]
        src = ((pos + np.arange(n)) // self.hold) * self.hold - pos
        out = y[:, np.maximum(src, 0)]
        if src[0] < 0:
            out[:, src < 0] = self.last[:, None]
        tail = ((pos + n - 1) // self.hold) * self.hold - pos
        if tail >= 0:
            self.last = y[:, tail].copy()
        return out


OS_MODES = ('tanh', 'hard', 'fold', 'asym', 'rectify')


class _Distortion(Proc):
    """Oversampled waveshaper. The resampling filters are the ones scipy's resample_poly designs; run causally
    they delay by 10 base samples each way, so latency = 20."""

    def __init__(self, fx, env):
        super().__init__(fx, env)
        mode = fx['mode']
        if mode not in OS_MODES + ('bitcrush', 'downsample'):
            raise FxError(f"distortion mode {mode!r}: tanh hard fold asym rectify bitcrush downsample")
        self.os = int(fx['oversample']) if mode in OS_MODES else 1
        if self.os > 1:
            half = 10 * self.os
            h = signal.firwin(2 * half + 1, 1.0 / self.os, window=('kaiser', 5.0))
            self.up = _Lfilter(h * self.os, [1.0])
            self.down = _Lfilter(h, [1.0])
            self.latency = 20
            self.g_lag = _Lag(10)
        if mode == 'asym':      # DC from the asymmetric curve: a 10 Hz blocker at the oversampled rate
            R = 1 - 2 * np.pi * 10 / (self.sr * self.os)
            self.dc = _Lfilter([1.0, -1.0], [1.0, -R])
        if mode in ('bitcrush', 'downsample'):
            self.crush = _Crush(fx['bits'] if mode == 'bitcrush' else 24, fx['rate_hz'], self.sr)
        self.tone = _Sos(eq_sos([{"type": "highcut", "freq": fx['tone_hz']}], self.sr), 2) if fx['tone_hz'] else None
        self.dry_lag = _Lag(self.latency, signal=True)
        self.mix_lag = _Lag(self.latency)

    def process(self, x, blk):
        fx, n, os_ = self.fx, x.shape[1], self.os
        g = dsp.undb(blk.param('drive_db', fx['drive_db']))
        mode = fx['mode']
        if mode in ('bitcrush', 'downsample'):
            y = self.crush(x * g, blk.pos)
        else:
            if os_ > 1:
                z = np.zeros((2, n * os_))
                z[:, ::os_] = x
                xi = self.up(z)
                gl = self.g_lag(g, n) if not np.isscalar(g) else g
                gi = np.repeat(gl, os_) if not np.isscalar(gl) else gl
            else:
                xi, gi = x, g
            if mode == 'tanh':
                y = np.tanh(xi * gi)
            elif mode == 'hard':
                y = np.clip(xi * gi, -1, 1)
            elif mode == 'fold':
                y = np.sin(xi * gi * np.pi / 2)
            elif mode == 'asym':
                zz = xi * gi
                y = np.where(zz > 0, np.tanh(zz), np.tanh(zz * 0.5) * 1.2)
                y = self.dc(y)
            else:
                y = np.abs(np.tanh(xi * gi)) * 2 - 0.5
            if os_ > 1:
                y = self.down(y)[:, ::os_]
        if self.tone is not None:
            y = self.tone(y)
        y = y * dsp.undb(fx['out_db'])
        return _mix(self.dry_lag(x, n), y, self.mix_lag(blk.param('mix', fx['mix']), n))


class _Bitcrush(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.crush = _Crush(fx['bits'], fx['rate_hz'], self.sr)

    def process(self, x, blk):
        return _mix(x, self.crush(x, blk.pos), blk.param('mix', self.fx['mix']))


class _Compressor(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.hpf = _Sos(eq_sos([{"type": "lowcut", "freq": fx['sc_hpf_hz']}], self.sr)) if fx['sc_hpf_hz'] else None
        self.e = np.zeros(1)
        self.g = np.zeros(1)

    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        key = x if not fx['sidechain'] else blk.key(fx['sidechain'])
        key = key.mean(0) if key.ndim == 2 else key
        if self.hpf is not None:
            key = self.hpf(key)
        lvl = dsp.db(dsp.env_follow_s(np.ascontiguousarray(key, dtype=np.float64), 0.0005, 0.01, self.sr, self.e))
        thr = dsp.as_curve(blk.param('threshold_db', fx['threshold_db']), n)
        gr = dsp.gain_computer(lvl - thr, 0.0, float(fx['ratio']), float(fx['knee_db']))
        gr = dsp.smooth_gain_s(gr, fx['attack_ms'] / 1000, fx['release_ms'] / 1000, self.sr, self.g)
        y = x * dsp.undb(gr + fx['makeup_db'])
        blk.note_gr(float(np.min(gr)) if len(gr) else 0.0)
        return _mix(x, y, fx['mix'])


class _Duck(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        if not fx['source'] and not fx['every_beats']:
            raise FxError("duck needs 'source': '<track>' (duck on its notes) or 'every_beats': 1")
        ms = self.sr / 1000
        a, h, r = int(fx['attack_ms'] * ms), int(fx['hold_ms'] * ms), max(int(fx['release_ms'] * ms), 1)
        i = np.arange(a + h + r + 1)
        self.shape = np.where(i < a, i / max(a, 1), np.where(i < a + h, 1.0,
                                                             np.clip(1 - (i - a - h) / r, 0, 1) ** fx['curve']))

    def process(self, x, blk):
        fx, n, pos = self.fx, x.shape[1], blk.pos
        L = len(self.shape)
        if fx['source']:
            ons = blk.onsets(fx['source'], pos - L, pos + n)
        else:
            step = fx['every_beats'] * 60 / self.env.bpm * self.sr
            base = self.env.offset_samples % step
            k0 = max(0, int(np.ceil((pos - L - base) / step)))
            k1 = int(np.floor((pos + n - base) / step))
            ons = base + np.arange(k0, k1 + 1) * step
        out = np.zeros(n)
        for o in ons:
            oi = int(o) - pos
            a, b = max(0, -oi), min(L, n - oi)
            if b > a:
                np.maximum(out[oi + a:oi + b], self.shape[a:b], out=out[oi + a:oi + b])
        return x * dsp.undb(out * -np.abs(dsp.as_curve(blk.param('depth_db', fx['depth_db']), n)))


class _Gate(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.steps, self.total = parse_steps(fx['pattern'], fx['step'])
        self.e = np.zeros(1)

    def process(self, x, blk):
        fx, n, pos = self.fx, x.shape[1], blk.pos
        spb = 60 / self.env.bpm * self.sr
        off = self.env.offset_samples
        cycle = self.total * spb
        tgt = np.zeros(n)
        k0 = int(np.floor((pos - off) / cycle)) - 1
        k1 = int(np.floor((pos + n - off) / cycle)) + 1
        for k in range(k0, k1 + 1):
            for st, d, v in self.steps:
                a = int(off + (k * self.total + st) * spb)
                b = int(a + d * spb)
                lo, hi = max(a, pos), min(b, pos + n)
                if hi > lo:
                    tgt[lo - pos:hi - pos] = v / 127
        env = dsp.env_follow_s(tgt, fx['attack_ms'] / 1000, fx['release_ms'] / 1000, self.sr, self.e)
        depth = blk.param('depth', fx['depth'])
        return x * (1 - depth + depth * env)


class _Delay(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.ds = max(int((fx['time_ms'] / 1000 if fx['time_ms'] else fx['time_beats'] * 60 / env.bpm) * self.sr), 1)
        self.hp = _Sos(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], self.sr), 2) if fx['hp_hz'] else None
        self.coef = 1 - np.exp(-2 * np.pi * fx['lp_hz'] / self.sr)
        self.bl, self.br, self.st = np.zeros(self.ds + 1), np.zeros(self.ds + 1), np.zeros(3)

    def retime(self, bpm, origin):
        super().retime(bpm, origin)
        if self.fx['time_ms']:
            return
        ds = max(int(self.fx['time_beats'] * 60 / self.env.bpm * self.sr), 1)
        if ds == self.ds:
            return
        # the echoes in flight keep their order: oldest first, padded with silence (longer) or cut (shorter)
        w, size = int(self.st[0]), ds + 1
        for k in ('bl', 'br'):
            old = np.roll(getattr(self, k), -w)
            new = np.zeros(size)
            m = min(size, len(old))
            new[size - m:] = old[len(old) - m:]
            setattr(self, k, new)
        self.ds, self.st[0] = ds, 0

    def process(self, x, blk):
        src = x if self.hp is None else self.hp(x)
        wl, wr = dsp.feedback_delay_s(src[0].copy(), src[1].copy(), self.ds, float(self.fx['feedback']),
                                      bool(self.fx['pingpong']), self.coef, self.bl, self.br, self.st)
        return x * self.env.dry + np.stack([wl, wr]) * blk.param('mix', self.fx['mix'])


class _Reverb(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        pre = int(fx['predelay_ms'] / 1000 * self.sr)
        self.pre = _Lag(pre, signal=True) if pre else None
        self.hp = _Sos(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], self.sr)) if fx['hp_hz'] else None
        self.lp = _Sos(eq_sos([{"type": "highcut", "freq": fx['lp_hz']}], self.sr)) if fx['lp_hz'] else None
        self.verb = dsp.Freeverb(fx['size'], fx['damp'], self.sr)

    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        mono = x.mean(0)
        if self.hp is not None:
            mono = self.hp(mono)
        if self.lp is not None:
            mono = self.lp(mono)
        if self.pre is not None:
            mono = self.pre(mono, n)
        wl, wr = self.verb.process(np.ascontiguousarray(mono))
        mid, side = (wl + wr) / 2, (wl - wr) / 2 * fx['width']
        return x * self.env.dry + np.stack([mid + side, mid - side]) * blk.param('mix', fx['mix']) * 3.0


class PartConv:
    """Uniformly partitioned FFT convolution (overlap-save) of a mono input with k impulse responses.
    Output = the linear convolution delayed by P samples, whatever the block sizes. A first block of 16+
    partitions (offline: the whole window) is convolved in one FFT pass instead; the processor then refuses
    further blocks (offline runs never send any)."""

    def __init__(self, irs, P=1024):
        self.P = P
        self.irs = irs
        k, m = irs.shape
        self.nparts = max(1, int(np.ceil(m / P)))
        pad = np.zeros((k, self.nparts * P))
        pad[:, :m] = irs
        parts = pad.reshape(k, self.nparts, P)
        self.H = np.fft.rfft(np.concatenate([parts, np.zeros_like(parts)], axis=2), axis=2)
        self.fdl = np.zeros((self.nparts, P + 1), dtype=complex)
        self.inbuf = np.zeros(2 * P)
        self.pending = np.zeros(0)
        self.out = np.zeros((k, P))
        self.fed = 0
        self.oneshot = False

    def process(self, x):
        n = len(x)
        if self.oneshot:
            raise RuntimeError("PartConv used one-shot (offline); build a new one for block processing")
        if self.fed == 0 and n >= 16 * self.P:
            self.oneshot = True
            y = np.stack([signal.oaconvolve(x, ir)[:n] for ir in self.irs])
            return np.concatenate([np.zeros((len(self.irs), self.P)), y], axis=1)[:, :n]
        self.fed += n
        self.pending = np.concatenate([self.pending, x])
        outs = [self.out]
        P = self.P
        while len(self.pending) >= P:
            blk, self.pending = self.pending[:P], self.pending[P:]
            self.inbuf = np.concatenate([self.inbuf[P:], blk])
            self.fdl = np.roll(self.fdl, 1, axis=0)
            self.fdl[0] = np.fft.rfft(self.inbuf)
            Y = np.einsum('kpf,pf->kf', self.H, self.fdl)
            outs.append(np.fft.irfft(Y, 2 * P, axis=1)[:, P:])
        allout = np.concatenate(outs, axis=1)
        self.out = allout[:, n:]
        return allout[:, :n]


class _Hall(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        ir = hall_ir(fx['rt60'], fx['low_mult'], fx['high_mult'], fx['predelay_ms'], fx['early'], self.sr,
                     int(fx['seed']))
        self.conv = PartConv(ir, 1024)
        self.latency = self.conv.P
        self.hp = _Sos(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], self.sr)) if fx['hp_hz'] else None
        self.dry_lag = _Lag(self.latency, signal=True)
        self.mix_lag = _Lag(self.latency)

    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        mono = x.mean(0)
        if self.hp is not None:
            mono = self.hp(mono)
        wl, wr = self.conv.process(mono)
        mid, side = (wl + wr) / 2, (wl - wr) / 2 * fx['width']
        return self.dry_lag(x, n) * self.env.dry + np.stack([mid + side, mid - side]) * \
            self.mix_lag(blk.param('mix', fx['mix']), n)


class _Chorus(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        size = int((fx['delay_ms'] + fx['depth_ms']) / 1000 * self.sr) + 4
        self.bufs = [np.zeros(size), np.zeros(size)]
        self.st = [np.zeros(1), np.zeros(1)]

    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        is_flanger = fx['type'] == 'flanger'
        rate = fx['rate_hz'] if not is_flanger or not fx.get('rate_beats') else self.env.bpm / 60 / fx['rate_beats']
        ph = (blk.pos - (self.env.sync0 if is_flanger and fx.get('rate_beats') else 0) + np.arange(n)) * rate / self.sr
        outs = []
        for ch, off in ((0, 0.0), (1, 0.25)):
            d = (fx['delay_ms'] + fx['depth_ms'] * (0.5 + 0.5 * np.sin(2 * np.pi * (ph + off)))) / 1000 * self.sr
            outs.append(dsp.mod_delay_s(np.ascontiguousarray(x[ch]), d, fx['feedback'], 1.0, self.bufs[ch], self.st[ch]))
        return _mix(x, np.stack(outs), blk.param('mix', fx['mix']))


class _Phaser(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.z = [np.zeros(int(fx['stages'])), np.zeros(int(fx['stages']))]
        self.st = [np.zeros(1), np.zeros(1)]

    def process(self, x, blk):
        fx, n, sr = self.fx, x.shape[1], self.sr
        ph = _lfo_phase(n, fx['rate_hz'], fx['rate_beats'], self.env.bpm, sr,
                        blk.pos - (self.env.sync0 if fx['rate_beats'] else 0))
        outs = []
        for ch, off in ((0, 0.0), (1, 0.25)):
            m = fx['center'] + 0.5 * fx['depth'] * np.sin(2 * np.pi * (ph + off))
            fc = 200 * 2 ** (m * 6)  # 200 Hz .. 12.8 kHz
            wc = np.tan(np.pi * np.minimum(fc, sr * 0.45) / sr)
            coef = (wc - 1) / (wc + 1)
            outs.append(dsp.allpass1_chain_s(np.ascontiguousarray(x[ch]), coef, int(fx['stages']), fx['feedback'],
                                             self.z[ch], self.st[ch]))
        return _mix(x, (x + np.stack(outs)) / 2, blk.param('mix', fx['mix']))


class _Tremolo(Proc):
    def process(self, x, blk):
        fx, n = self.fx, x.shape[1]
        ph = _lfo_phase(n, fx['rate_hz'], fx['rate_beats'], self.env.bpm, self.sr,
                        blk.pos - (self.env.offset_samples if fx['rate_beats'] else self.env.hz0)) + fx['phase']
        w = dsp.lfo_wave(fx['shape'], ph)
        d = blk.param('depth', fx['depth'])
        if fx['mode'] == 'pan':
            gl, gr = dsp.pan_gains(w * d)
            return np.stack([x[0] * gl, x[1] * gr])
        return x * (1 - d * (0.5 - 0.5 * w))


class _Width(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.hp = _Sos(eq_sos([{"type": "lowcut", "freq": fx['mono_below_hz'], "slope": 24}], self.sr)) \
            if fx['mono_below_hz'] else None

    def process(self, x, blk):
        mid = (x[0] + x[1]) / 2
        side = (x[0] - x[1]) / 2
        if self.hp is not None:
            side = self.hp(side)
        side = side * blk.param('width', self.fx['width'])
        return np.stack([mid + side, mid - side])


class _Limiter(Proc):
    """Peak limiter: the gain at sample i is set by the peaks within +-lookahead of i, so the output is delayed
    by the lookahead (latency)."""

    def __init__(self, fx, env):
        super().__init__(fx, env)
        self.la = int(fx['lookahead_ms'] / 1000 * self.sr)
        self.latency = self.la
        self.ceil = dsp.undb(fx['ceiling_db'])
        self.xh = np.zeros((2, self.la))
        self.ph = np.zeros(2 * self.la)
        self.g = np.zeros(1)

    def process(self, x, blk):
        from scipy.ndimage import maximum_filter1d
        fx, n, la = self.fx, x.shape[1], self.la
        xg = x * dsp.undb(fx['gain_db'])
        ext = np.concatenate([self.ph, np.max(np.abs(xg), axis=0)])
        self.ph = ext[len(ext) - 2 * la:] if la else self.ph
        pk = maximum_filter1d(ext, size=2 * la + 1)[la:la + n]
        xd = np.concatenate([self.xh, xg], axis=1)
        self.xh = xd[:, xd.shape[1] - la:] if la else self.xh
        xd = xd[:, :n]
        need = np.minimum(1.0, self.ceil / np.maximum(pk, 1e-9))
        gdb = dsp.smooth_gain_s(dsp.db(need), 0.0001, fx['release_ms'] / 1000, self.sr, self.g)
        gdb = np.minimum(gdb, dsp.db(need))
        blk.note_gr(float(np.min(gdb)) if n else 0.0)
        return np.clip(xd * dsp.undb(gdb), -self.ceil, self.ceil)


class _Formant(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        if fx['vowel'] not in VOWEL_FORMANTS and not (fx['f1'] and fx['f2']):
            raise FxError(f"formant vowel must be one of {list(VOWEL_FORMANTS)} or give f1/f2(/f3) in Hz")
        base = VOWEL_FORMANTS.get(fx['vowel'], (500, 1450, 2500))
        fs = [fx['f1'] or base[0], fx['f2'] or base[1], fx['f3'] or base[2]]
        self.bands = []
        for f, g in zip(fs, fx['gains_db']):
            f = min(f * fx['shift'], self.sr * 0.45)
            bw = f / fx['q']
            sos = signal.butter(2, [max(f - bw / 2, 20), f + bw / 2], btype='band', fs=self.sr, output='sos')
            self.bands.append((_Sos(sos, 2), dsp.undb(g)))

    def process(self, x, blk):
        wet = np.zeros_like(x)
        for f, g in self.bands:
            wet += f(x) * g
        return _mix(x, wet * 4.0, blk.param('mix', self.fx['mix']))


class _Vocoder(Proc):
    def __init__(self, fx, env):
        super().__init__(fx, env)
        if not fx['modulator']:
            raise FxError("vocoder needs 'modulator': '<track name>' or 'sound:<name>' (the voice)")
        sr = self.sr
        edges = np.geomspace(fx['lo_hz'], fx['hi_hz'], int(fx['bands']) + 1)
        centers = np.sqrt(edges[:-1] * edges[1:])
        self.bands = []
        for fc in centers:
            sos = signal.butter(2, [fc / 2 ** (0.5 / fx['q'] * 3), min(fc * 2 ** (0.5 / fx['q'] * 3), sr * 0.45)],
                                btype='band', fs=sr, output='sos')
            self.bands.append((_Sos(sos), _Sos(sos, 2), np.zeros(1)))
        self.hp = _Sos(signal.butter(4, 5000, btype='high', fs=sr, output='sos'))

    def process(self, x, blk):
        fx = self.fx
        mod = blk.modulator(fx['modulator'])
        mod = mod.mean(0) if mod.ndim == 2 else mod
        rel = fx['release_ms'] / 1000
        wet = np.zeros_like(x)
        for fm, fc, e in self.bands:
            env = dsp.env_follow_s(np.ascontiguousarray(fm(mod)), 0.002, rel, self.sr, e)
            wet += fc(x) * env
        hp = self.hp(mod)
        if fx['unvoiced']:
            wet += hp * fx['unvoiced']
        return _mix(x, wet * 8.0, blk.param('mix', fx['mix']))


PROCS = {'gain': _Gain, 'eq': _Eq, 'filter': _Filter, 'distortion': _Distortion, 'bitcrush': _Bitcrush,
         'compressor': _Compressor, 'duck': _Duck, 'gate': _Gate, 'delay': _Delay, 'reverb': _Reverb, 'hall': _Hall,
         'chorus': _Chorus, 'flanger': _Chorus, 'phaser': _Phaser, 'tremolo': _Tremolo, 'width': _Width,
         'limiter': _Limiter, 'formant': _Formant, 'vocoder': _Vocoder}

from . import rig_blocks  # noqa: E402,F401  the rig's processors (they build on the classes above) join PROCS
from . import spatial_blocks  # noqa: E402,F401  the spatial effect joins PROCS


def make(fx, env):
    """A processor for a normalized effect dict."""
    if fx.get('bypass'):
        return _Bypass(fx, env)
    return PROCS[fx['type']](fx, env)


class _OfflineBlock(Block):
    """The whole render window as one block, reading automation / sources from a render Ctx."""

    def __init__(self, ctx, idx, n, n0):
        self.ctx, self.idx, self.n, self.n0 = ctx, idx, n, n0

    def param(self, name, default):
        v = self.ctx.param(self.idx, name, default)
        return v if np.isscalar(v) else dsp.as_curve(v, self.n)

    def _pad(self, a):
        a = np.asarray(a, dtype=np.float64)
        if a.shape[-1] >= self.n:
            return a[..., :self.n]
        return np.concatenate([a, np.zeros(a.shape[:-1] + (self.n - a.shape[-1],))], axis=-1)

    def key(self, track):
        return self._pad(self.ctx.track_audio(track))

    def onsets(self, track, lo, hi):
        o = np.asarray(self.ctx.track_onsets(track), dtype=np.float64) * self.ctx.sr
        return o[(o >= lo) & (o < hi)]

    def modulator(self, ref):
        return self._pad(self.ctx.modulator_audio(ref, self.n0))

    def note_gr(self, gr_db):
        self.ctx.note_gr(self.idx, gr_db)


def apply_fx(x, fx, ctx, idx):
    """Offline: run effect `fx` over the whole window x (2, n) as one block, latency compensated."""
    if fx.get('bypass'):
        return x
    env = Env(ctx.sr, ctx.bpm, _dry(fx, ctx), getattr(ctx, 'offset_samples', 0))
    p = make(fx, env)
    n = x.shape[1]
    L = p.latency
    xin = x if L == 0 else np.concatenate([x, np.zeros((2, L))], axis=1)
    y = p.process(xin, _OfflineBlock(ctx, idx, n + L, n))
    return y[:, L:] if L else y


