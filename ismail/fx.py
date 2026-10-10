"""Effects. Each effect is a dict {"type": ..., params}. apply_fx(x, fx, ctx) -> stereo (2, n).

ctx.param(fx_index, name, default) returns a per-sample curve when the param is automated, else the scalar.
ctx.track_audio(name) returns another track's post-fx audio (for sidechain/vocoder).
ctx.track_onsets(name) returns note-on times (sec) of another track (for duck).
ctx.track_notes(name) returns [(start_sec, pitch, dur_sec, vel)] of another track (for autotune.notes_from).
"""
import numpy as np
from scipy import signal

from . import dsp
from .notation import parse_steps

SR = dsp.SR

FX_DEFAULTS = {
    "gain": {"gain_db": 0.0, "pan": 0.0},
    "eq": {"bands": []},
    "filter": {"mode": "lp24", "cutoff": 20000.0, "res": 0.0, "mix": 1.0, "lfo_rate_beats": None, "lfo_depth_oct": 0.0,
               "lfo_shape": "sine"},
    "distortion": {"mode": "tanh", "drive_db": 12.0, "mix": 1.0, "out_db": 0.0, "bits": 8, "rate_hz": 11025.0,
                   "tone_hz": None, "oversample": 2},
    "compressor": {"threshold_db": -18.0, "ratio": 4.0, "attack_ms": 5.0, "release_ms": 120.0, "knee_db": 6.0,
                   "makeup_db": 0.0, "sidechain": None, "sc_hpf_hz": None, "mix": 1.0},
    "duck": {"source": None, "every_beats": None, "depth_db": -12.0, "attack_ms": 2.0, "hold_ms": 0.0,
             "release_ms": 180.0, "curve": 2.0},
    "gate": {"pattern": "x.x.x.x.x.x.x.x.", "step": 0.25, "attack_ms": 2.0, "release_ms": 30.0, "depth": 1.0},
    "delay": {"time_beats": 0.75, "time_ms": None, "feedback": 0.35, "mix": 0.25, "pingpong": False,
              "lp_hz": 6000.0, "hp_hz": 150.0, "dry": None},
    "reverb": {"size": 0.75, "damp": 0.4, "mix": 0.25, "predelay_ms": 10.0, "width": 1.0, "hp_hz": 200.0,
               "lp_hz": 12000.0, "dry": None},
    "hall": {"rt60": 2.2, "low_mult": 1.3, "high_mult": 0.45, "predelay_ms": 20.0, "early": 0.5, "width": 1.0,
             "hp_hz": 80.0, "mix": 0.25, "dry": None, "seed": 1},
    "chorus": {"rate_hz": 0.8, "depth_ms": 3.0, "delay_ms": 12.0, "feedback": 0.0, "mix": 0.5},
    "flanger": {"rate_hz": 0.25, "rate_beats": None, "depth_ms": 2.0, "delay_ms": 1.0, "feedback": 0.6, "mix": 0.5},
    "phaser": {"rate_hz": 0.3, "rate_beats": None, "depth": 0.8, "center": 0.5, "stages": 6, "feedback": 0.5,
               "mix": 0.5},
    "tremolo": {"rate_beats": 0.5, "rate_hz": None, "depth": 0.5, "shape": "sine", "mode": "amp", "phase": 0.0},
    "width": {"width": 1.0, "mono_below_hz": None},
    "limiter": {"ceiling_db": -0.3, "gain_db": 0.0, "release_ms": 60.0, "lookahead_ms": 3.0},
    "vocoder": {"modulator": None, "bands": 20, "lo_hz": 90.0, "hi_hz": 9000.0, "q": 6.0, "release_ms": 25.0,
                "mix": 1.0, "unvoiced": 0.15},
    "bitcrush": {"bits": 8, "rate_hz": 11025.0, "mix": 1.0},
    "formant": {"vowel": "@", "f1": None, "f2": None, "f3": None, "q": 8.0, "gains_db": [0.0, -4.0, -10.0],
                "mix": 1.0, "shift": 1.0},
    "autotune": {"key": "C", "scale": "chromatic", "notes_from": None, "speed_ms": 20.0, "amount": 1.0,
                 "formant": 0.0, "lo_hz": 70.0, "hi_hz": 1000.0, "mix": 1.0},
}
from . import rig as _rig
FX_DEFAULTS.update(_rig.DEFAULTS)
VOWEL_FORMANTS = {'i': (280, 2250, 2900), 'e': (400, 2000, 2600), 'E': (550, 1770, 2500), 'a': (750, 1250, 2600),
                  'A': (650, 1050, 2500), 'o': (450, 850, 2400), 'u': (320, 800, 2300), '@': (500, 1450, 2500)}
FX_TYPES = tuple(FX_DEFAULTS)
EQ_BAND_TYPES = ('peak', 'lowshelf', 'highshelf', 'lowcut', 'highcut', 'notch', 'bandpass')

# params that accept automation (per-sample curves)
AUTOMATABLE = {
    "gain": ("gain_db", "pan"), "filter": ("cutoff", "res", "mix"), "distortion": ("drive_db", "mix"),
    "delay": ("mix",), "reverb": ("mix",), "hall": ("mix",), "chorus": ("mix",), "flanger": ("mix", "feedback"),
    "phaser": ("mix",), "tremolo": ("depth",), "formant": ("mix",), "width": ("width",), "compressor": ("threshold_db",),
    "duck": ("depth_db",), "gate": ("depth",), "vocoder": ("mix",), "bitcrush": ("mix",), "autotune": ("mix",),
}
AUTOMATABLE.update(_rig.AUTOMATABLE)


class FxError(ValueError):
    pass


def normalize(fx):
    if not isinstance(fx, dict) or fx.get('type') not in FX_DEFAULTS:
        raise FxError(f"effect needs 'type' in {', '.join(FX_TYPES)}")
    unknown = set(fx) - set(FX_DEFAULTS[fx['type']]) - {'type', 'name', 'bypass'}
    if unknown:
        raise FxError(f"unknown {fx['type']} params {sorted(unknown)}; valid: {sorted(FX_DEFAULTS[fx['type']])}")
    full = dict(FX_DEFAULTS[fx['type']], **fx)
    if fx['type'] == 'autotune':
        from . import tune
        try:
            tune.scale_classes(full['key'], full['scale'])
        except tune.TuneError as e:
            raise FxError(str(e))
    if fx['type'] == 'eq':
        for b in full['bands']:
            if b.get('type', 'peak') not in EQ_BAND_TYPES:
                raise FxError(f"eq band type {b.get('type')!r}: use {', '.join(EQ_BAND_TYPES)}")
    return full


# ------------------------------------------------------------------ helpers

def _mix(dry, wet, m):
    return dry * (1 - m) + wet * m


def rbj_sos(btype, f, gain_db, q, sr):
    """RBJ cookbook biquad as an sos row."""
    A = 10 ** (gain_db / 40)
    w0 = 2 * np.pi * min(f, sr * 0.49) / sr
    cw, sw = np.cos(w0), np.sin(w0)
    alpha = sw / (2 * q)
    if btype == 'peak':
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    elif btype == 'lowshelf':
        s = 2 * np.sqrt(A) * alpha
        b = [A * ((A + 1) - (A - 1) * cw + s), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - s)]
        a = [(A + 1) + (A - 1) * cw + s, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - s]
    elif btype == 'highshelf':
        s = 2 * np.sqrt(A) * alpha
        b = [A * ((A + 1) + (A - 1) * cw + s), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - s)]
        a = [(A + 1) - (A - 1) * cw + s, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - s]
    elif btype == 'lowcut':
        b = [(1 + cw) / 2, -(1 + cw), (1 + cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif btype == 'highcut':
        b = [(1 - cw) / 2, 1 - cw, (1 - cw) / 2]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif btype == 'notch':
        b = [1, -2 * cw, 1]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    elif btype == 'bandpass':
        b = [alpha, 0, -alpha]
        a = [1 + alpha, -2 * cw, 1 - alpha]
    else:
        raise FxError(f"bad eq band {btype}")
    b = np.array(b) / a[0]
    a = np.array(a) / a[0]
    return np.concatenate([b, a])


def eq_sos(bands, sr):
    rows = []
    for bd in bands:
        t = bd.get('type', 'peak')
        q = bd.get('q', 0.707)
        rows.append(rbj_sos(t, bd['freq'], bd.get('gain_db', 0.0), q, sr))
        if t in ('lowcut', 'highcut') and bd.get('slope', 12) >= 24:
            rows.append(rbj_sos(t, bd['freq'], 0, q, sr))
    return np.array(rows)


def _lfo_phase(n, rate_hz, rate_beats, bpm, sr, start_sample):
    if rate_beats:
        rate_hz = bpm / 60 / rate_beats
    return (start_sample + np.arange(n)) * rate_hz / sr


def _shape_env(n, onsets_samp, attack, hold, release, curve):
    """Duck shape in [0,1]: rises to 1 at each onset (attack), holds, falls back to 0 (release)."""
    a, h, r = int(attack), int(hold), max(int(release), 1)
    i = np.arange(a + h + r + 1)
    shape = np.where(i < a, i / max(a, 1), np.where(i < a + h, 1.0, np.clip(1 - (i - a - h) / r, 0, 1) ** curve))
    out = np.zeros(n)
    for o in onsets_samp:
        o = int(o)
        if 0 <= o < n:
            m = min(len(shape), n - o)
            np.maximum(out[o:o + m], shape[:m], out=out[o:o + m])
    return out


# ------------------------------------------------------------------ main

def _dry(fx, ctx):
    """delay/reverb dry level: default 1 (insert), but 0 on a pure send-return bus (no track outputs into it):
    the dry signal already reaches the mix through the sending track."""
    if fx.get('dry') is not None:
        return float(fx['dry'])
    name = str(getattr(ctx, 'track', ''))
    if name.startswith('bus:'):
        tracks = getattr(getattr(ctx, 'r', None), 'project', {}).get('tracks', {})
        if not any(t.get('output') == name[4:] for t in tracks.values()):
            return 0.0
    return 1.0


def apply_fx(x, fx, ctx, idx):
    P = lambda name: ctx.param(idx, name, fx[name])  # noqa: E731
    t = fx['type']
    sr = ctx.sr
    n = x.shape[1]
    if fx.get('bypass'):
        return x
    if t in _rig.APPLY:
        return _rig.APPLY[t](x, fx, sr, P)
    if t == 'gain':
        g = dsp.undb(P('gain_db'))
        gl, gr = dsp.pan_gains(P('pan'))
        return np.stack([x[0] * g * gl, x[1] * g * gr])
    if t == 'eq':
        if not fx['bands']:
            return x
        return signal.sosfilt(eq_sos(fx['bands'], sr), x, axis=1)
    if t == 'filter':
        cut = dsp.as_curve(P('cutoff'), n)
        if fx['lfo_rate_beats'] and fx['lfo_depth_oct']:
            ph = _lfo_phase(n, None, fx['lfo_rate_beats'], ctx.bpm, sr, 0)
            cut = cut * 2 ** (fx['lfo_depth_oct'] * dsp.lfo_wave(fx['lfo_shape'], ph))
        wet = np.stack([dsp.filt(ch, fx['mode'], cut, P('res'), sr) for ch in x])
        return _mix(x, wet, P('mix'))
    if t == 'distortion':
        g = dsp.undb(P('drive_db'))
        mode = fx['mode']
        os_ = int(fx['oversample']) if mode in ('tanh', 'hard', 'fold', 'asym', 'rectify') else 1
        xi = signal.resample_poly(x, os_, 1, axis=1) if os_ > 1 else x
        gi = np.repeat(g, os_)[:xi.shape[1]] if not np.isscalar(g) else g
        if mode == 'tanh':
            y = np.tanh(xi * gi)
        elif mode == 'hard':
            y = np.clip(xi * gi, -1, 1)
        elif mode == 'fold':
            y = np.sin(xi * gi * np.pi / 2)
        elif mode == 'asym':
            z = xi * gi
            y = np.where(z > 0, np.tanh(z), np.tanh(z * 0.5) * 1.2)
            y = y - np.mean(y, axis=1, keepdims=True)
        elif mode == 'rectify':
            y = np.abs(np.tanh(xi * gi)) * 2 - 0.5
        elif mode in ('bitcrush', 'downsample'):
            y = _crush(x * g, fx['bits'] if mode == 'bitcrush' else 24, fx['rate_hz'], sr)
        else:
            raise FxError(f"distortion mode {mode!r}: tanh hard fold asym rectify bitcrush downsample")
        if os_ > 1:
            y = signal.resample_poly(y, 1, os_, axis=1)[:, :n]
        if fx['tone_hz']:
            y = signal.sosfilt(eq_sos([{"type": "highcut", "freq": fx['tone_hz']}], sr), y, axis=1)
        y = y * dsp.undb(fx['out_db'])
        return _mix(x, y, P('mix'))
    if t == 'bitcrush':
        return _mix(x, _crush(x, fx['bits'], fx['rate_hz'], sr), P('mix'))
    if t == 'compressor':
        key = x if not fx['sidechain'] else ctx.track_audio(fx['sidechain'])
        key = key.mean(0) if key.ndim == 2 else key
        if fx['sc_hpf_hz']:
            key = signal.sosfilt(eq_sos([{"type": "lowcut", "freq": fx['sc_hpf_hz']}], sr), key)
        lvl = dsp.db(dsp.env_follow(key, 0.0005, 0.01, sr))
        thr = P('threshold_db')
        thr_arr = dsp.as_curve(thr, n)
        gr = dsp.gain_computer(lvl - thr_arr, 0.0, float(fx['ratio']), float(fx['knee_db']))
        gr = dsp.smooth_gain(gr, fx['attack_ms'] / 1000, fx['release_ms'] / 1000, sr)
        y = x * dsp.undb(gr + fx['makeup_db'])
        ctx.note_gr(idx, float(np.min(gr)) if len(gr) else 0.0)
        return _mix(x, y, fx['mix'])
    if t == 'duck':
        if fx['source']:
            onsets = np.array(ctx.track_onsets(fx['source'])) * sr
        elif fx['every_beats']:
            step = fx['every_beats'] * 60 / ctx.bpm * sr
            onsets = np.arange(ctx.offset_samples % step, n, step)
        else:
            raise FxError("duck needs 'source': '<track>' (duck on its notes) or 'every_beats': 1")
        ms = sr / 1000
        shape = _shape_env(n, onsets, fx['attack_ms'] * ms, fx['hold_ms'] * ms, fx['release_ms'] * ms, fx['curve'])
        return x * dsp.undb(shape * -np.abs(dsp.as_curve(P('depth_db'), n)))
    if t == 'gate':
        steps, total = parse_steps(fx['pattern'], fx['step'])
        spb = 60 / ctx.bpm * sr
        tgt = np.zeros(n)
        cycle = total * spb
        k0 = int(np.floor(-ctx.offset_samples / cycle)) - 1
        for k in range(k0, k0 + int(n / cycle) + 3):
            for st, d, v in steps:
                a = int(ctx.offset_samples + (k * total + st) * spb)
                b = int(a + d * spb)
                if a < n and b > 0:     # b <= 0 would slice from the end of tgt
                    tgt[max(a, 0):min(b, n)] = v / 127
        env = dsp.env_follow(tgt, fx['attack_ms'] / 1000, fx['release_ms'] / 1000, sr)
        depth = P('depth')
        return x * (1 - depth + depth * env)
    if t == 'delay':
        ds = int((fx['time_ms'] / 1000 if fx['time_ms'] else fx['time_beats'] * 60 / ctx.bpm) * sr)
        src = x
        if fx['hp_hz']:
            src = signal.sosfilt(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], sr), src, axis=1)
        coef = 1 - np.exp(-2 * np.pi * fx['lp_hz'] / sr)
        wl, wr = dsp.feedback_delay(src[0].copy(), src[1].copy(), max(ds, 1), float(fx['feedback']), bool(fx['pingpong']), coef)
        wet = np.stack([wl, wr])
        m = P('mix')
        return x * _dry(fx, ctx) + wet * m
    if t == 'reverb':
        pre = int(fx['predelay_ms'] / 1000 * sr)
        mono = x.mean(0)
        if fx['hp_hz']:
            mono = signal.sosfilt(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], sr), mono)
        if fx['lp_hz']:
            mono = signal.sosfilt(eq_sos([{"type": "highcut", "freq": fx['lp_hz']}], sr), mono)
        mono = np.concatenate([np.zeros(pre), mono])[:n]
        wl, wr = dsp.freeverb(mono, fx['size'], fx['damp'], sr)
        mid, side = (wl + wr) / 2, (wl - wr) / 2 * fx['width']
        wet = np.stack([mid + side, mid - side])
        return x * _dry(fx, ctx) + wet * P('mix') * 3.0
    if t == 'hall':
        ir = hall_ir(fx['rt60'], fx['low_mult'], fx['high_mult'], fx['predelay_ms'], fx['early'], sr, int(fx['seed']))
        mono = x.mean(0)
        if fx['hp_hz']:
            mono = signal.sosfilt(eq_sos([{"type": "lowcut", "freq": fx['hp_hz']}], sr), mono)
        wl, wr = (signal.oaconvolve(mono, ir[c])[:n] for c in (0, 1))
        mid, side = (wl + wr) / 2, (wl - wr) / 2 * fx['width']
        return x * _dry(fx, ctx) + np.stack([mid + side, mid - side]) * P('mix')
    if t in ('chorus', 'flanger'):
        rate = fx['rate_hz'] if t == 'chorus' or not fx.get('rate_beats') else ctx.bpm / 60 / fx['rate_beats']
        ph = np.arange(n) * rate / sr
        outs = []
        for ch, off in ((0, 0.0), (1, 0.25)):
            d = (fx['delay_ms'] + fx['depth_ms'] * (0.5 + 0.5 * np.sin(2 * np.pi * (ph + off)))) / 1000 * sr
            outs.append(dsp.mod_delay(x[ch], d, fx['feedback'], 1.0))
        wet = np.stack(outs)
        return _mix(x, wet, P('mix'))
    if t == 'phaser':
        ph = _lfo_phase(n, fx['rate_hz'], fx['rate_beats'], ctx.bpm, sr, 0)
        outs = []
        for ch, off in ((0, 0.0), (1, 0.25)):
            m = fx['center'] + 0.5 * fx['depth'] * np.sin(2 * np.pi * (ph + off))
            fc = 200 * 2 ** (m * 6)  # 200 Hz .. 12.8 kHz
            wc = np.tan(np.pi * np.minimum(fc, sr * 0.45) / sr)
            coef = (wc - 1) / (wc + 1)
            outs.append(dsp.allpass1_chain(x[ch], coef, int(fx['stages']), fx['feedback']))
        wet = np.stack(outs)
        return _mix(x, (x + wet) / 2, P('mix'))
    if t == 'tremolo':
        ph = _lfo_phase(n, fx['rate_hz'], fx['rate_beats'], ctx.bpm, sr, -ctx.offset_samples) + fx['phase']
        w = dsp.lfo_wave(fx['shape'], ph)
        d = P('depth')
        if fx['mode'] == 'pan':
            gl, gr = dsp.pan_gains(w * d)
            return np.stack([x[0] * gl, x[1] * gr])
        g = 1 - d * (0.5 - 0.5 * w)
        return x * g
    if t == 'width':
        mid = (x[0] + x[1]) / 2
        side = (x[0] - x[1]) / 2
        if fx['mono_below_hz']:
            side = signal.sosfilt(eq_sos([{"type": "lowcut", "freq": fx['mono_below_hz'], "slope": 24}], sr), side)
        side = side * P('width')
        return np.stack([mid + side, mid - side])
    if t == 'limiter':
        xg = x * dsp.undb(fx['gain_db'])
        ceil = dsp.undb(fx['ceiling_db'])
        la = int(fx['lookahead_ms'] / 1000 * sr)
        peak = np.max(np.abs(xg), axis=0)
        # lookahead: max over the next la samples
        from scipy.ndimage import maximum_filter1d
        pk = maximum_filter1d(peak, size=2 * la + 1, origin=0)
        need = np.minimum(1.0, ceil / np.maximum(pk, 1e-9))
        gdb = dsp.smooth_gain(dsp.db(need), 0.0001, fx['release_ms'] / 1000, sr)
        gdb = np.minimum(gdb, dsp.db(need))
        ctx.note_gr(idx, float(np.min(gdb)))
        return np.clip(xg * dsp.undb(gdb), -ceil, ceil)
    if t == 'formant':
        if fx['vowel'] not in VOWEL_FORMANTS and not (fx['f1'] and fx['f2']):
            raise FxError(f"formant vowel must be one of {list(VOWEL_FORMANTS)} or give f1/f2(/f3) in Hz")
        base = VOWEL_FORMANTS.get(fx['vowel'], (500, 1450, 2500))
        fs = [fx['f1'] or base[0], fx['f2'] or base[1], fx['f3'] or base[2]]
        wet = np.zeros_like(x)
        for f, g in zip(fs, fx['gains_db']):
            f = min(f * fx['shift'], sr * 0.45)
            bw = f / fx['q']
            sos = signal.butter(2, [max(f - bw / 2, 20), f + bw / 2], btype='band', fs=sr, output='sos')
            wet += signal.sosfilt(sos, x, axis=1) * dsp.undb(g)
        wet *= 4.0
        return _mix(x, wet, P('mix'))
    if t == 'vocoder':
        if not fx['modulator']:
            raise FxError("vocoder needs 'modulator': '<track name>' or 'sound:<name>' (the voice)")
        mod = ctx.modulator_audio(fx['modulator'], n)
        mod = mod.mean(0) if mod.ndim == 2 else mod
        car = x
        edges = np.geomspace(fx['lo_hz'], fx['hi_hz'], int(fx['bands']) + 1)
        centers = np.sqrt(edges[:-1] * edges[1:])
        wet = np.zeros_like(x)
        rel = fx['release_ms'] / 1000
        soss = [signal.butter(2, [fc / 2 ** (0.5 / fx['q'] * 3), min(fc * 2 ** (0.5 / fx['q'] * 3), sr * 0.45)],
                              btype='band', fs=sr, output='sos') for fc in centers]
        hp = signal.butter(4, 5000, btype='high', fs=sr, output='sos')
        # only process where the modulator speaks (+ release tail); elsewhere the output is silent anyway
        for a, b in _active_spans(mod, sr, pad_s=0.05, tail_s=0.3, gap_s=1.0):
            m = mod[a:b]
            c = np.ascontiguousarray(car[:, a:b])
            for sos in soss:
                env = dsp.env_follow(signal.sosfilt(sos, m), 0.002, rel, sr)
                wet[:, a:b] += signal.sosfilt(sos, c, axis=1) * env
            if fx['unvoiced']:
                wet[:, a:b] += signal.sosfilt(hp, m) * fx['unvoiced']
        wet *= 8.0
        return _mix(x, wet, P('mix'))
    if t == 'autotune':
        return _autotune(x, fx, ctx, sr, P)
    raise FxError(f"unknown fx {t}")


def _autotune(x, fx, ctx, sr, P):
    """The voice's pitch moved onto the key's scale, or onto the notes of fx['notes_from'] (tune.py)."""
    from . import tune
    notes = None
    if fx['notes_from']:
        get = getattr(ctx, 'track_notes', None)
        if get is None:
            raise FxError("autotune.notes_from works in a render; here, tune to key and scale instead")
        notes = get(fx['notes_from'])
    mono = x.mean(0)
    wet = x.copy()
    try:
        for a, b in _active_spans(mono, sr, pad_s=0.1, tail_s=0.1, gap_s=0.5):
            y, _ = tune.retune(mono[a:b], sr, fx['key'], fx['scale'], notes, fx['speed_ms'], fx['amount'],
                               fx['formant'], fx['lo_hz'], fx['hi_hz'], t0=a / sr)
            wet[:, a:b] = y
    except tune.TuneError as e:
        raise FxError(str(e))
    return _mix(x, wet, P('mix'))


_IR_CACHE = {}


def hall_ir(rt60, low_mult, high_mult, predelay_ms, early, sr, seed=1):
    """Synthetic hall impulse response (2, n), unit energy: decorrelated noise tail whose decay time falls with
    frequency (octave bands, rt60 at 1 kHz, low_mult below ~250 Hz, high_mult above ~6 kHz), a density build-up
    over the first ~80 ms, and sparse early reflections."""
    key = (rt60, low_mult, high_mult, predelay_ms, early, sr, seed)
    if key in _IR_CACHE:
        return _IR_CACHE[key]
    rng = np.random.default_rng(seed)
    n = int((rt60 * max(low_mult, 1.0) * 1.1 + predelay_ms / 1000) * sr)
    t = np.arange(n) / sr
    pre = int(predelay_ms / 1000 * sr)
    noise = rng.standard_normal((2, n))
    tail = np.zeros((2, n))
    centers = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    lc = np.log2(np.array(centers) / 1000.0)
    for fc, l in zip(centers, lc):
        mult = np.interp(l, [-4, -2, 0, 2.6, 4], [low_mult, low_mult, 1.0, high_mult, high_mult * 0.7])
        lo, hi = fc / np.sqrt(2), min(fc * np.sqrt(2), sr * 0.45)
        if lo >= hi:
            continue
        sos = signal.butter(2, [lo, hi], btype='band', fs=sr, output='sos')
        band = signal.sosfilt(sos, noise, axis=1)
        tail += band * np.exp(-6.91 * t / (rt60 * mult))
    build = 1 - np.exp(-np.maximum(t - 0.01, 0) / 0.04)
    tail *= build
    tail = np.concatenate([np.zeros((2, pre)), tail[:, :n - pre]], axis=1)
    er = np.zeros((2, n))
    for i in range(14):
        d = pre + int(rng.uniform(0.004, 0.075) * sr)
        g = early * rng.uniform(0.4, 1.0) * np.exp(-(d - pre) / sr / 0.05)
        er[i % 2, d] += g * 3.0
        er[(i + 1) % 2, min(n - 1, d + int(rng.uniform(0.0005, 0.003) * sr))] += g * 1.5
    ir = tail / np.sqrt(np.sum(tail ** 2) / 2) + er / max(1.0, np.sqrt(np.sum(er ** 2) / 2) / max(early, 1e-6))
    ir /= np.sqrt(np.sum(ir ** 2) / 2)
    _IR_CACHE[key] = ir
    return ir


def _active_spans(x, sr, pad_s=0.05, tail_s=0.3, gap_s=1.0, thresh=1e-4):
    """[(start, end)] sample spans where |x| exceeds thresh, padded, merging gaps shorter than gap_s."""
    idx = np.nonzero(np.abs(x) > thresh)[0]
    if len(idx) == 0:
        return []
    spans = []
    gap = int(gap_s * sr)
    breaks = np.nonzero(np.diff(idx) > gap)[0]
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    n = len(x)
    for a, b in zip(starts, ends):
        spans.append((max(0, int(a - pad_s * sr)), min(n, int(b + tail_s * sr))))
    return spans


def _crush(x, bits, rate_hz, sr):
    q = 2 ** (bits - 1)
    y = np.round(x * q) / q
    hold = max(1, int(round(sr / rate_hz)))
    if hold > 1:
        idx = (np.arange(x.shape[-1]) // hold) * hold
        y = y[..., idx]
    return y
