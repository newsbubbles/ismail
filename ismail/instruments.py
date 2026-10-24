"""Instruments: note events -> stereo audio.

Every instrument is a JSON-able dict with a "type". render_instrument() is the single entry point.
Notes arrive as (start_sec, midi, dur_sec, vel). Automation arrives as {param_path: per-sample curve}
covering the whole song, so voices can index absolute time.
"""
import copy
import math
import numpy as np

from . import dsp
from .notation import pitch_to_midi, midi_to_hz

SR = dsp.SR

# ------------------------------------------------------------------ defaults & schema

SYNTH_DEFAULT = {
    "type": "synth",
    "oscs": [{"wave": "saw", "level": 1.0}],
    "noise": 0.0, "noise_color": "white", "sub": 0.0,
    "drive": 0.0,
    "filter": {"type": "lp24", "cutoff": 20000.0, "res": 0.0, "keytrack": 0.0, "env_amount": 0.0, "vel_amount": 0.0},
    "amp_env": {"a": 0.003, "d": 0.2, "s": 1.0, "r": 0.05},
    "filter_env": {"a": 0.001, "d": 0.3, "s": 0.0, "r": 0.1},
    "pitch_env": {"amount": 0.0, "decay": 0.05},
    "lfos": [],
    "mono": False, "glide": 0.0,
    "velocity_sens": 0.5,
    "gain_db": 0.0, "pan": 0.0,
}
OSC_DEFAULT = {"wave": "saw", "level": 1.0, "octave": 0, "semi": 0, "fine": 0.0,
               "unison": 1, "detune": 15.0, "spread": 0.6, "pw": 0.5, "phase": "random",
               "fm_from": None, "fm_amount": 0.0, "pan": 0.0, "partials": None, "table": None, "fixed_hz": None}
OSC_WAVES = ('saw', 'square', 'pulse', 'triangle', 'sine', 'additive', 'table', 'noise')

DRUM_DEFAULTS = {
    "kick": {"pitch_start": 160.0, "pitch_end": 48.0, "pitch_decay": 0.045, "decay": 0.45, "click": 0.25,
             "drive": 0.0, "hold": 0.02, "gain_db": 0.0, "pan": 0.0},
    "snare": {"tone_hz": 185.0, "tone_decay": 0.10, "tone_mix": 0.45, "noise_decay": 0.17, "noise_hp": 1200.0,
              "noise_lp": 10000.0, "pitch_drop": 0.3, "drive": 0.0, "gain_db": 0.0, "pan": 0.0},
    "hat": {"decay": 0.045, "tune": 1.0, "metal": 0.6, "hp": 7000.0, "bp": 10000.0, "gain_db": 0.0, "pan": 0.0},
    "clap": {"decay": 0.22, "bp": 1300.0, "q": 0.5, "bursts": 3, "burst_gap": 0.011, "gain_db": 0.0, "pan": 0.0},
    "tom": {"pitch_start": 220.0, "pitch_end": 110.0, "pitch_decay": 0.08, "decay": 0.35, "click": 0.1,
            "drive": 0.0, "hold": 0.0, "gain_db": 0.0, "pan": 0.0},
    "noise_hit": {"decay": 0.3, "filter": "bp", "cutoff": 3000.0, "cutoff_end": 3000.0, "res": 0.3, "attack": 0.001,
                  "gain_db": 0.0, "pan": 0.0},
}
SAMPLER_DEFAULT = {"type": "sampler", "sound": None, "root": "C4", "start": 0.0, "end": None, "loop": False,
                   "loop_start": None, "loop_end": None, "one_shot": False, "pitch_track": True, "reverse": False,
                   "transpose": 0.0, "amp_env": {"a": 0.001, "d": 0.0, "s": 1.0, "r": 0.02},
                   "filter": None, "velocity_sens": 0.5, "gain_db": 0.0, "pan": 0.0}

# a sampler with several sounds ('sounds': [a, b, c] in place of 'sound') picks one per note: in turn
# (round_robin), at random, or by the note's velocity (each sound's 'vel': [lo, hi])
SAMPLER_SELECT = ('round_robin', 'random', 'velocity')
SAMPLER_LAYER_KEYS = ('sound', 'vel', 'gain_db')
RANDOM_CYCLE = 8        # a live loop of a 'random' sampler repeats its picks after this many passes

INSTRUMENT_TYPES = ('synth', 'sprite', 'sampler', 'kit', 'code', 'mimic') + tuple(DRUM_DEFAULTS)
# the two synth engines: sprite (type 'synth' or 'sprite': oscillators, filter, envelopes; right for synth sounds)
# and mimic (instruments measured from recordings)
# output calibration: one synth voice peaks near -9 dBFS, one drum hit near -6 dBFS at full velocity, so a few
# tracks at 0 dB faders sum without clipping
SYNTH_GAIN = 0.35
DRUM_GAIN = 0.5


class InstrumentError(ValueError):
    pass


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize(inst):
    """Fill defaults and validate. Raises InstrumentError with a corrective message."""
    if not isinstance(inst, dict) or 'type' not in inst:
        raise InstrumentError(f"instrument needs a 'type': one of {', '.join(INSTRUMENT_TYPES)}")
    t = inst['type']
    if t == 'sprite':
        inst, t = dict(inst, type='synth'), 'synth'
    if t == 'synth':
        full = deep_merge(SYNTH_DEFAULT, inst)
        full['oscs'] = [deep_merge(OSC_DEFAULT, o) for o in full['oscs']]
        for i, o in enumerate(full['oscs']):
            if o['wave'] not in OSC_WAVES:
                raise InstrumentError(f"oscs[{i}].wave={o['wave']!r}: use one of {', '.join(OSC_WAVES)}")
            if o['wave'] == 'additive' and not o['partials']:
                raise InstrumentError(f"oscs[{i}] is additive: give 'partials': [amp_h1, amp_h2, ...]")
            if o['wave'] == 'table' and not o['table']:
                raise InstrumentError(f"oscs[{i}] is table: give 'table': '<sound name>' (a single-cycle sound)")
            if o['fm_from'] is not None and not (0 <= int(o['fm_from']) < i):
                raise InstrumentError(f"oscs[{i}].fm_from must index an EARLIER osc (0..{i - 1})")
        unknown = set(inst) - set(SYNTH_DEFAULT)
        if unknown:
            raise InstrumentError(f"unknown synth keys {sorted(unknown)}; valid: {sorted(SYNTH_DEFAULT)}")
        return full
    if t in DRUM_DEFAULTS:
        unknown = set(inst) - set(DRUM_DEFAULTS[t]) - {'type', 'fx'}
        if unknown:
            raise InstrumentError(f"unknown {t} keys {sorted(unknown)}; valid: {sorted(DRUM_DEFAULTS[t])}")
        return deep_merge(dict(DRUM_DEFAULTS[t], type=t), inst)
    if t == 'sampler':
        if inst.get('sounds') is not None:
            if inst.get('sound'):
                raise InstrumentError("sampler: give 'sound' (one sound) or 'sounds' (several), not both")
            select = inst.get('select', 'round_robin')
            if select not in SAMPLER_SELECT:
                raise InstrumentError(f"sampler select={select!r}: use one of {', '.join(SAMPLER_SELECT)}")
            full = deep_merge(SAMPLER_DEFAULT, {k: v for k, v in inst.items() if k != 'sounds'})
            full['sounds'] = _sampler_layers(inst['sounds'], select)
            full['select'] = select
            return full
        if 'select' in inst:
            raise InstrumentError("sampler 'select' picks among 'sounds': [a, b, ...]; with one 'sound' there is "
                                  "nothing to pick")
        full = deep_merge(SAMPLER_DEFAULT, inst)
        if not full['sound']:
            raise InstrumentError("sampler needs 'sound': '<name>' from the sound bank (see sound_list / sound_make), "
                                  "or 'sounds': [a, b, ...] with 'select'")
        return full
    if t == 'kit':
        if not isinstance(inst.get('map'), dict) or not inst['map']:
            raise InstrumentError("kit needs 'map': {'C1': {<instrument>}, 'D1': {...}} mapping pitches to instruments")
        return {"type": "kit", "map": {str(pitch_to_midi(k)): normalize(v) for k, v in inst['map'].items()},
                "gain_db": inst.get('gain_db', 0.0), "pan": inst.get('pan', 0.0)}
    if t == 'code':
        if not inst.get('code') and not inst.get('voice'):
            raise InstrumentError("code instrument needs 'voice': '<name>' (a voice module, see voices_list) or 'code' "
                                  "defining voice(freq, t, vel, gate, sr) -> array")
        return dict({"tail": 0.3, "gain_db": 0.0, "pan": 0.0, "fn": "voice", "params": {}}, **inst)
    if t == 'mimic':
        from . import mimic
        if not inst.get('profile'):
            raise InstrumentError("mimic needs 'profile': '<name>' (a <name>.mimic.json made by mimic_measure; "
                                  "voices_list shows the ones available)")
        bad = set(inst.get('params') or {}) - set(mimic.DEFAULT_PARAMS)
        if bad:
            raise InstrumentError(f"unknown mimic params {sorted(bad)}; valid: {sorted(mimic.DEFAULT_PARAMS)}")
        unknown = set(inst) - {'type', 'profile', 'params', 'tail', 'gain_db', 'pan', 'fx', 'perform', 'performer'}
        if unknown:
            raise InstrumentError(f"unknown mimic keys {sorted(unknown)}; valid: profile, params, tail, gain_db, pan, "
                                  "perform")
        _check_mimic_perform(inst)
        return dict({"tail": 1.0, "gain_db": 0.0, "pan": 0.0, "params": {}}, **inst)
    raise InstrumentError(f"unknown instrument type {t!r}; use one of {', '.join(INSTRUMENT_TYPES)}")


def _sampler_layers(sounds, select):
    """'sounds' -> [{'sound', 'vel': [lo, hi] or None, 'gain_db'}]. With select='velocity' and no ranges given,
    the sounds split 0-127 evenly in the order given (softest first)."""
    if not isinstance(sounds, (list, tuple)) or not sounds:
        raise InstrumentError("sampler 'sounds' is a list: ['a', 'b'] or [{'sound': 'a', 'vel': [0, 80]}, ...]")
    out = []
    for i, x in enumerate(sounds):
        x = {'sound': x} if isinstance(x, str) else x
        if not isinstance(x, dict) or not x.get('sound'):
            raise InstrumentError(f"sounds[{i}]: a sound name, or {{'sound': '<name>', 'vel': [lo, hi], 'gain_db': 0}}")
        bad = set(x) - set(SAMPLER_LAYER_KEYS)
        if bad:
            raise InstrumentError(f"sounds[{i}]: unknown keys {sorted(bad)}; valid: {', '.join(SAMPLER_LAYER_KEYS)}")
        vel = x.get('vel')
        if vel is not None:
            if select != 'velocity':
                raise InstrumentError(f"sounds[{i}].vel: velocity ranges are for select='velocity' (it is {select!r})")
            if not (isinstance(vel, (list, tuple)) and len(vel) == 2 and 0 <= vel[0] <= vel[1] <= 127):
                raise InstrumentError(f"sounds[{i}].vel={vel!r}: [lo, hi] with 0 <= lo <= hi <= 127")
            vel = [int(vel[0]), int(vel[1])]
        out.append({'sound': str(x['sound']), 'vel': vel, 'gain_db': float(x.get('gain_db', 0.0))})
    if select == 'velocity':
        given = [l['vel'] is not None for l in out]
        if any(given) and not all(given):
            raise InstrumentError("select='velocity': give every sound a 'vel': [lo, hi], or none (then they split "
                                  "0-127 evenly, softest first)")
        if not any(given):
            n = len(out)
            edges = [int(round(i * 127 / n)) for i in range(n + 1)]
            for i, l in enumerate(out):
                l['vel'] = [0 if i == 0 else edges[i] + 1, edges[i + 1]]
    return out


def sampler_sound_names(inst):
    """Every bank sound a sampler instrument plays (raw or normalized; [] for any other type)."""
    if not inst or inst.get('type') != 'sampler':
        return []
    out = [inst['sound']] if inst.get('sound') else []
    for x in inst.get('sounds') or []:
        out.append(x if isinstance(x, str) else (x or {}).get('sound'))
    return [s for s in out if s]


def _velocity_choices(layers, v):
    """The sounds whose range holds velocity v; none: the nearest range."""
    c = [j for j, l in enumerate(layers) if l['vel'][0] <= v <= l['vel'][1]]
    if c:
        return c
    dist = [min(abs(v - l['vel'][0]), abs(v - l['vel'][1])) for l in layers]
    return [int(np.argmin(dist))]


def select_cycle(inst):
    """0 when no sampler in inst picks among several sounds; else how many passes of a live loop (whose length is
    whole bars) play before the picks repeat: round_robin repeats after len(sounds), velocity after the least common
    multiple of how many sounds share a velocity, random after RANDOM_CYCLE."""
    if not inst:
        return 0
    if inst.get('type') == 'kit':
        out = 0
        for v in (inst.get('map') or {}).values():
            c = select_cycle(v)
            if c:
                out = math.lcm(out or 1, c)
        return out
    if inst.get('type') != 'sampler' or not inst.get('sounds'):
        return 0
    try:
        full = normalize(inst)
    except InstrumentError:
        return 0
    layers, sel = full['sounds'], full['select']
    if sel == 'random':
        return RANDOM_CYCLE
    if sel == 'velocity':
        return math.lcm(*{len(_velocity_choices(layers, v)) for v in range(128)})
    return len(layers)


def sampler_picks(p, notes, beat0=0.0, bpm=120.0, bpb=4):
    """Which of p['sounds'] each note plays, keyed on where the note sits in the song (beat0: the song beat at
    sample 0), so a window render, a live deck and the whole song pick alike. round_robin: the k-th hit of a bar
    of c hits in song bar b plays sound (b * c + k) mod n (in turn, carrying on from bar to bar); random: a draw
    seeded on the note's song beat and pitch; velocity: the sound whose 'vel' range holds the velocity, in turn
    when ranges overlap."""
    layers, sel = p['sounds'], p.get('select', 'round_robin')
    n = len(layers)
    spb = 60.0 / bpm
    beats = [round(beat0 + st / spb, 6) for st, _, _, _ in notes]
    bars = [int(math.floor(b / bpb + 1e-9)) for b in beats]
    count = {}
    for b in bars:
        count[b] = count.get(b, 0) + 1
    seen, turn = {}, [0] * len(notes)
    for i in sorted(range(len(notes)), key=lambda i: (beats[i], notes[i][1], i)):
        b = bars[i]
        turn[i] = b * count[b] + seen.get(b, 0)
        seen[b] = seen.get(b, 0) + 1
    out = []
    for i, (st, m, d, v) in enumerate(notes):
        if sel == 'random':
            key = int(round(beats[i] * 1e6)) % (1 << 62)
            out.append(int(np.random.default_rng([key, int(m)]).integers(n)))
        elif sel == 'velocity':
            c = _velocity_choices(layers, v)
            out.append(c[turn[i] % len(c)])
        else:
            out.append(turn[i] % n)
    return out


# ------------------------------------------------------------------ envelopes

def adsr(n, gate_s, a, d, s, r, sr=SR):
    t = np.arange(n) / sr
    a = max(a, 1e-4)
    d = max(d, 1e-4)
    r = max(r, 1e-4)
    pre = np.where(t < a, t / a, s + (1 - s) * np.exp(-(t - a) * 5.0 / d))
    ta = np.clip(gate_s, 0, None)
    lg = (ta / a) if ta < a else s + (1 - s) * np.exp(-(ta - a) * 5.0 / d)
    post = lg * np.exp(-(t - ta) * 5.0 / r)
    return np.where(t < ta, pre, post)


def env_tail(env):
    return max(env.get('r', 0.05), 1e-3)


# ------------------------------------------------------------------ synth

def _curve(auto, key, s0, n, default):
    c = auto.get(key) if auto else None
    if c is None:
        return default
    seg = c[s0:s0 + n]
    if len(seg) < n:
        seg = np.concatenate([seg, np.full(n - len(seg), c[-1])])
    return seg


def _lfo_curve(lfo, n, s0, bpm, sr):
    rate = lfo.get('rate', 5.0)
    if 'rate_beats' in lfo:  # period in beats, tempo-synced, phase locked to song time
        rate = bpm / 60.0 / float(lfo['rate_beats'])
    start = 0 if lfo.get('retrigger', True) else s0
    phase = (start + np.arange(n)) * rate / sr + lfo.get('phase', 0.0)
    w = dsp.lfo_wave(lfo.get('shape', 'sine'), phase) * lfo.get('depth', 1.0)
    delay = lfo.get('delay', 0.0)
    if delay > 0:
        w *= np.clip(np.arange(n) / (delay * sr), 0, 1)
    return w


def _synth_voice(p, pitch_curve, gate_s, vel, s0, n, auto, bpm, sr, seed):
    """Render one voice. pitch_curve: per-sample midi pitch. Returns (L, R)."""
    rng = np.random.default_rng(seed)
    lfo_sum = {}
    for lfo in p['lfos']:
        tgt = lfo.get('target', 'pitch')
        lfo_sum[tgt] = lfo_sum.get(tgt, 0.0) + _lfo_curve(lfo, n, s0, bpm, sr)
    pc = pitch_curve + lfo_sum.get('pitch', 0.0) + _curve(auto, 'pitch', s0, n, 0.0)
    pe = p['pitch_env']
    if pe['amount']:
        pc = pc + pe['amount'] * np.exp(-np.arange(n) / (max(pe['decay'], 1e-4) * sr))
    base_hz = 440.0 * 2 ** ((pc - 69) / 12)

    L = np.zeros(n)
    R = np.zeros(n)
    outs = []
    for i, o in enumerate(p['oscs']):
        level = _curve(auto, f'oscs.{i}.level', s0, n, o['level'])
        if np.isscalar(level) and level == 0 and not any(
                oo.get('fm_from') == i for oo in p['oscs']):
            outs.append(np.zeros(n))
            continue
        ratio = 2 ** (o['octave'] + o['semi'] / 12 + o['fine'] / 1200)
        hz = np.full(n, float(o['fixed_hz'])) if o['fixed_hz'] else base_hz * ratio
        uni = max(1, int(o['unison']))
        mono_sum = np.zeros(n)
        pw = _curve(auto, f'oscs.{i}.pw', s0, n, o['pw']) + lfo_sum.get(f'oscs.{i}.pw', lfo_sum.get('pw', 0.0)) * 0.4
        fm = None
        if o['fm_from'] is not None:
            amt = _curve(auto, f'oscs.{i}.fm_amount', s0, n, o['fm_amount']) + lfo_sum.get('fm', 0.0)
            fm = outs[int(o['fm_from'])] * amt
        for u in range(uni):
            off = 0.0 if uni == 1 else (u / (uni - 1) - 0.5) * 2 * o['detune']
            upan = o['pan'] + (0.0 if uni == 1 else (u / (uni - 1) - 0.5) * 2 * o['spread'])
            dt = hz * 2 ** (off / 1200) / sr
            ph0 = rng.random() if o['phase'] == 'random' else float(o['phase'])
            phase = np.cumsum(dt) + ph0
            if fm is not None:
                phase = phase + fm
            w = o['wave']
            if w == 'additive':
                y = dsp.additive(phase, dt, o['partials'])
            elif w == 'table':
                y = dsp.table_osc(phase, _TABLE_RESOLVER(o['table']))
            elif w == 'noise':
                y = rng.standard_normal(n) * 0.5
            else:
                y = dsp.osc(w, phase, dt, pw)
            y = y / np.sqrt(uni)
            if uni == 1:
                mono_sum += y
            else:
                gl, gr = dsp.pan_gains(upan)
                L += y * gl * level
                R += y * gr * level
        outs.append(mono_sum)
        if uni == 1:
            gl, gr = dsp.pan_gains(o['pan'])
            L += mono_sum * gl * level
            R += mono_sum * gr * level
    if p['sub']:
        sub = np.sin(2 * np.pi * np.cumsum(base_hz / 2 / sr)) * p['sub']
        L += sub
        R += sub
    nz = _curve(auto, 'noise', s0, n, p['noise'])
    if np.any(nz):
        noise = dsp.pink_noise(n, seed) if p['noise_color'] == 'pink' else rng.standard_normal(n) * 0.5
        L += noise * nz
        R += noise * nz
    drive = _curve(auto, 'drive', s0, n, p['drive'])
    if np.any(drive):
        g = dsp.undb(drive)
        L = np.tanh(L * g) / np.sqrt(g)
        R = np.tanh(R * g) / np.sqrt(g)

    f = p['filter']
    fe = p['filter_env']
    cutoff = _curve(auto, 'filter.cutoff', s0, n, f['cutoff'])
    res = _curve(auto, 'filter.res', s0, n, f['res'])
    need_filter = not (np.isscalar(cutoff) and cutoff >= 19999 and not f['env_amount'] and 'cutoff' not in lfo_sum) \
        or f['type'].startswith(('hp', 'bp', 'notch'))
    if need_filter:
        octs = np.zeros(n)
        if f['env_amount']:
            octs += f['env_amount'] * adsr(n, gate_s, fe['a'], fe['d'], fe['s'], fe['r'], sr)
        if f['keytrack']:
            octs += f['keytrack'] * (pc - 60) / 12
        if f['vel_amount']:
            octs += f['vel_amount'] * (vel / 127 - 0.5) * 2
        octs += lfo_sum.get('cutoff', 0.0)
        c = cutoff * 2 ** octs
        L = dsp.filt(L, f['type'], c, res, sr, f.get('drive', 1.0))
        R = dsp.filt(R, f['type'], c, res, sr, f.get('drive', 1.0))
    ae = p['amp_env']
    env = adsr(n, gate_s, ae['a'], ae['d'], ae['s'], ae['r'], sr)
    vs = p['velocity_sens']
    amp = env * ((1 - vs) + vs * vel / 127)
    if 'amp' in lfo_sum:
        amp = amp * (1 + lfo_sum['amp'])
    return L * amp, R * amp


def _group_mono(notes):
    """Mono mode: notes that overlap/touch form one legato phrase (single envelope, gliding pitch)."""
    phrases = []
    for nt in sorted(notes):
        if phrases and nt[0] < phrases[-1][-1][0] + phrases[-1][-1][2] - 1e-6:
            phrases[-1].append(nt)
        else:
            phrases.append([nt])
    return phrases


def _bar_key(st, beat0, bpm, bpb, sr):
    """Where a note starts in its bar, in samples: the seed of its random phase and noise, so a note sounds the same
    in a full render, in a window and live (where each bar's events render on their own)."""
    spb = 60.0 / bpm
    return int(round((round(beat0 + st / spb, 6) % bpb) * spb * sr))


def render_synth(p, notes, total_n, auto, bpm, sr, beat0=0.0, bpb=4):
    out = np.zeros((2, total_n))
    tail = env_tail(p['amp_env'])
    if p['mono']:
        groups = _group_mono(notes)
    else:
        groups = [[nt] for nt in notes]
    prev_pitch = None
    for gi, g in enumerate(groups):
        st = g[0][0]
        end = max(x[0] + x[2] for x in g)
        # mono: a new phrase cuts at the next phrase start
        if p['mono'] and gi + 1 < len(groups):
            end = min(end, groups[gi + 1][0][0])
        s0 = int(round(st * sr))
        gate = end - st
        n = int((gate + tail) * sr) + 1
        if p['mono'] and gi + 1 < len(groups):
            n = min(n, int(round(groups[gi + 1][0][0] * sr)) - s0 + int(0.004 * sr))
        n = min(n, total_n - s0)
        if n <= 0:
            continue
        steps = np.zeros(n)
        for x in g:
            steps[max(int(round((x[0] - st) * sr)), 0):] = x[1]
        glide = p['glide']
        if glide > 0 and (len(g) > 1 or prev_pitch is not None):
            first = prev_pitch if (prev_pitch is not None and p['mono']) else g[0][1]
            steps_in = steps.copy()
            a = np.exp(-1.0 / (glide * sr / 3))
            from scipy.signal import lfilter
            zi = [first * a]
            steps = lfilter([1 - a], [1, -a], steps_in, zi=zi)[0]
        prev_pitch = g[-1][1]
        vel = g[0][3]
        l, r = _synth_voice(p, steps, gate, vel, s0, n, auto, bpm, sr, seed=_bar_key(st, beat0, bpm, bpb, sr) * 31 + g[0][1])
        if p['mono'] and gi + 1 < len(groups):
            fade = min(int(0.004 * sr), n)
            ramp = np.linspace(1, 0, fade)
            l[-fade:] *= ramp
            r[-fade:] *= ramp
        out[0, s0:s0 + n] += l
        out[1, s0:s0 + n] += r
    return out


# ------------------------------------------------------------------ drums

def _hit_env(n, decay, sr, hold=0.0):
    t = np.arange(n) / sr
    e = np.exp(-np.maximum(t - hold, 0) * 5.0 / max(decay, 1e-3))
    atk = min(int(0.0015 * sr), n)
    e[:atk] *= np.linspace(0, 1, atk)
    return e


def render_drum(p, vel, sr, seed):
    t = p['type']
    rng = np.random.default_rng(seed)
    if t in ('kick', 'tom'):
        n = int((p['decay'] + p['hold'] + 0.05) * sr)
        tt = np.arange(n) / sr
        f = p['pitch_end'] + (p['pitch_start'] - p['pitch_end']) * np.exp(-tt / max(p['pitch_decay'], 1e-4))
        y = np.sin(2 * np.pi * np.cumsum(f) / sr) * _hit_env(n, p['decay'], sr, p['hold'])
        if p['click']:
            cn = int(0.004 * sr)
            click = rng.standard_normal(cn) * np.exp(-np.arange(cn) / (0.0008 * sr))
            click = dsp.filt(click, 'hp12', 800.0, 0.0, sr)
            y[:cn] += click * p['click']
        if p['drive']:
            g = dsp.undb(p['drive'])
            y = np.tanh(y * g) / np.tanh(g)
    elif t == 'snare':
        n = int((max(p['tone_decay'], p['noise_decay']) + 0.05) * sr)
        tt = np.arange(n) / sr
        f = p['tone_hz'] * (1 + p['pitch_drop'] * np.exp(-tt / 0.01))
        tone = np.sin(2 * np.pi * np.cumsum(f) / sr) * _hit_env(n, p['tone_decay'], sr)
        noise = rng.standard_normal(n)
        noise = dsp.filt(noise, 'hp12', p['noise_hp'], 0.1, sr)
        noise = dsp.filt(noise, 'lp12', p['noise_lp'], 0.0, sr) * _hit_env(n, p['noise_decay'], sr)
        y = tone * p['tone_mix'] + noise * (1 - p['tone_mix']) * 0.8
        if p['drive']:
            g = dsp.undb(p['drive'])
            y = np.tanh(y * g) / np.tanh(g)
    elif t == 'hat':
        n = int((p['decay'] + 0.03) * sr)
        tt = np.arange(n) / sr
        freqs = np.array([205.3, 304.4, 369.6, 522.7, 540.0, 800.0]) * p['tune']
        metal = sum(np.sign(np.sin(2 * np.pi * fr * tt + rng.random() * 6.28)) for fr in freqs) / 6
        y = metal * p['metal'] + rng.standard_normal(n) * (1 - p['metal'])
        y = dsp.filt(y, 'bp', p['bp'], 0.3, sr)
        y = dsp.filt(y, 'hp24', p['hp'], 0.0, sr) * _hit_env(n, p['decay'], sr) * 2.0
    elif t == 'clap':
        n = int((p['decay'] + p['bursts'] * p['burst_gap'] + 0.05) * sr)
        env = np.zeros(n)
        for b in range(int(p['bursts'])):
            o = int(b * p['burst_gap'] * sr)
            m = int(0.01 * sr)
            env[o:o + m] += np.exp(-np.arange(min(m, n - o)) / (0.003 * sr))
        o = int((p['bursts'] - 1) * p['burst_gap'] * sr)
        env[o:] += _hit_env(n - o, p['decay'], sr) * 0.7
        y = dsp.filt(rng.standard_normal(n), 'bp', p['bp'], p['q'], sr) * env * 1.5
    elif t == 'noise_hit':
        n = int((p['decay'] + 0.05) * sr)
        tt = np.arange(n) / sr
        c = p['cutoff'] * (p['cutoff_end'] / p['cutoff']) ** np.clip(tt / p['decay'], 0, 1)
        y = dsp.filt(rng.standard_normal(n), p['filter'], c, p['res'], sr)
        e = _hit_env(n, p['decay'], sr)
        at = min(int(p['attack'] * sr), n)
        if at > 1:
            e[:at] *= np.linspace(0, 1, at)
        y = y * e
    else:
        raise InstrumentError(f"not a drum type: {t}")
    return y * (0.3 + 0.7 * vel / 127)


# ------------------------------------------------------------------ sampler

_SOUND_RESOLVER = None  # set by render: name -> (stereo array (2,n), sr)
_TABLE_RESOLVER = None


def set_resolvers(sound_fn, table_fn):
    global _SOUND_RESOLVER, _TABLE_RESOLVER
    _SOUND_RESOLVER = sound_fn
    _TABLE_RESOLVER = table_fn


def _resample_play(sample, ratio, n_out, start_s, end_s, loop, ls, le):
    """Read sample (2, N) at playback-rate ratio for n_out samples. Linear interpolation."""
    N = sample.shape[1]
    pos = start_s + np.arange(n_out) * ratio
    if loop and le > ls:
        span = le - ls
        over = pos >= le
        pos[over] = ls + np.mod(pos[over] - ls, span)
    valid = pos < min(end_s, N - 1)
    pos = np.where(valid, pos, 0)
    i0 = pos.astype(np.int64)
    fr = pos - i0
    out = sample[:, i0] * (1 - fr) + sample[:, np.minimum(i0 + 1, N - 1)] * fr
    return out * valid


def _sampler_source(p, name, sr):
    """One bank sound as the sampler plays it: (array (2, N), start, end, loop start, loop end) in samples."""
    snd = _SOUND_RESOLVER(name)
    if p['reverse']:
        snd = snd[:, ::-1]
    N = snd.shape[1]
    start_s = p['start'] * sr
    end_s = N if p['end'] is None else min(N, p['end'] * sr)
    ls = (p['loop_start'] if p['loop_start'] is not None else p['start']) * sr
    le = (p['loop_end'] * sr) if p['loop_end'] is not None else end_s
    return snd, start_s, end_s, ls, le


def render_sampler(p, notes, total_n, auto, sr, beat0=0.0, bpm=120.0, bpb=4):
    """One sound ('sound'), or one of several per note ('sounds' + 'select', see sampler_picks)."""
    if p.get('sounds'):
        picks = sampler_picks(p, notes, beat0, bpm, bpb)
        names = [l['sound'] for l in p['sounds']]
        gains = [dsp.undb(l['gain_db']) for l in p['sounds']]
    else:
        picks, names, gains = [0] * len(notes), [p['sound']], [1.0]
    srcs = {}
    root = pitch_to_midi(p['root'])
    out = np.zeros((2, total_n))
    ae = p['amp_env']
    for (st, m, d, v), j in zip(notes, picks):
        if j not in srcs:
            srcs[j] = _sampler_source(p, names[j], sr)
        snd, start_s, end_s, ls, le = srcs[j]
        semis = ((m - root) if p['pitch_track'] else 0) + p['transpose']
        ratio = 2 ** (semis / 12) * p.get('_sr_ratio', 1.0)
        s0 = int(round(st * sr))
        if p['one_shot']:
            n = int((end_s - start_s) / ratio) + 1
            gate = n / sr
        else:
            gate = d
            n = int((d + env_tail(ae)) * sr)
        n = min(n, total_n - s0)
        if n <= 0:
            continue
        y = _resample_play(snd, ratio, n, start_s, end_s, p['loop'], ls, le)
        env = adsr(n, gate, ae['a'], ae['d'], ae['s'], ae['r'], sr)
        vs = p['velocity_sens']
        y = y * env * ((1 - vs) + vs * v / 127)
        if gains[j] != 1.0:
            y = y * gains[j]
        if p['filter']:
            f = p['filter']
            cut = _curve(auto, 'filter.cutoff', s0, n, f.get('cutoff', 20000))
            y = np.stack([dsp.filt(ch, f.get('type', 'lp12'), cut, f.get('res', 0.0), sr) for ch in y])
        out[:, s0:s0 + n] += y
    return out


# ------------------------------------------------------------------ code instrument

def takes_arg(perform, name):
    """True when a performer names the argument in its signature (a **kw catch-all does not count)."""
    import inspect
    try:
        return name in inspect.signature(perform).parameters
    except (TypeError, ValueError):
        return False


def takes_beat0(perform):
    """A performer that keys its randomness on the song beat (see voices/guitar/electric.py) takes beat0."""
    return takes_arg(perform, 'beat0')


def performer_kw(perform, params, beat0, root):
    """A performer's keyword arguments: its params, plus beat0 and project_root (the project folder, for a voice
    that finds files in the project's ref/, such as toms) when it names them."""
    kw = dict(params or {})
    if takes_beat0(perform):
        kw['beat0'] = beat0
    if root and takes_arg(perform, 'project_root'):
        kw.setdefault('project_root', root)
    return kw


def render_code(p, notes, total_n, sr, bpm=120.0, root=None, auto=None, beat0=0.0):
    from . import voices
    if p.get('voice'):
        try:
            mod = voices.load(p['voice'], root)
        except voices.VoiceError as e:
            raise InstrumentError(str(e))
        # fn defaults to 'voice'; a module without voice() but with perform() is a performer
        want = p.get('fn') or 'voice'
        perform = getattr(mod, 'perform', None) if (want == 'perform' or not callable(getattr(mod, want, None))) else None
        if callable(perform):
            # a performer voice renders the whole part at once (strings that ring on, legato, whammy):
            # it gets every note and its expression lanes (automation 'inst.lane.<name>', per-sample curves)
            lanes = {k[5:]: v for k, v in (auto or {}).items() if k.startswith('lane.')}
            kw = performer_kw(perform, p.get('params'), beat0, root)
            y = np.asarray(perform(notes, total_n, sr, bpm=bpm, lanes=lanes, **kw), dtype=np.float64)
            if y.ndim == 1:
                y = np.stack([y, y])
            out = np.zeros((2, total_n))
            out[:, :min(total_n, y.shape[1])] = y[:, :total_n]
            return out
        try:
            fn = voices.function(p['voice'], p.get('fn', 'voice'), root)
        except voices.VoiceError as e:
            raise InstrumentError(str(e))
    else:
        ns = {"np": np, "dsp": dsp, "sr": sr}
        exec(p['code'], ns)
        fn = ns.get(p.get('fn') or 'voice')
        if fn is None:
            raise InstrumentError("code instrument must define voice(freq, t, vel, gate, sr)")
    out = np.zeros((2, total_n))
    for st, m, d, v in notes:
        s0 = int(round(st * sr))
        # the whole note even past the buffer's end: a voice's noise draws and filters depend on the length it is
        # given, so a note cut by a render window or a live event would sound different from the song's
        n = int((d + p['tail']) * sr)
        if n <= 0 or s0 >= total_n:
            continue
        try:
            y = voices.call(fn, midi_to_hz(m), np.arange(n) / sr, v / 127, d, sr, bpm, p.get('params'))
        except voices.VoiceError as e:
            raise InstrumentError(str(e))
        y = np.asarray(y, dtype=np.float64)
        if y.ndim == 1:
            y = np.stack([y, y])
        k = min(y.shape[1], n, total_n - s0)
        out[:, s0:s0 + k] += y[:, :k]
    return out


def _check_mimic_perform(inst):
    """'perform': true, or a dict of phrase performer params (ismail/mimic_perform.py, ledger M308). With it on,
    only the mimic params that mean the same for a phrase are allowed."""
    pf = inst.get('perform')
    if pf is None or pf is False:
        return
    from . import mimic_perform as mp
    if not (pf is True or isinstance(pf, dict)):
        raise InstrumentError("mimic 'perform' is true (play the track as phrases) or a dict of performer params, "
                              f"e.g. {{\"vib\": 8, \"breath_gap\": 0.2}}; params: {sorted(mp.DEFAULTS)}")
    if isinstance(pf, dict):
        bad = set(pf) - set(mp.DEFAULTS)
        if bad:
            raise InstrumentError(f"unknown mimic perform params {sorted(bad)}; valid: {sorted(mp.DEFAULTS)}")
    bad = set(inst.get('params') or {}) - set(mp.MIMIC_OK)
    if bad:
        raise InstrumentError(f"mimic params {sorted(bad)} shape single notes and do nothing with perform on; "
                              f"with perform the mimic params are {sorted(mp.MIMIC_OK)} and the performer's own go "
                              f"in 'perform': {sorted(mp.DEFAULTS)}")


def render_mimic(p, notes, total_n, sr, root=None, auto=None, bpm=120.0, beat0=0.0):
    from . import mimic
    try:
        prof = mimic.load_profile(p['profile'], root)
    except ValueError as e:
        raise InstrumentError(str(e))
    if p.get('perform'):
        # the whole track as phrases: slurs, tonguing, breath, delayed vibrato, lanes (ismail/mimic_perform.py)
        from . import mimic_perform
        try:
            mimic_perform.check_profile(prof, p['profile'])
        except mimic_perform.PerformError as e:
            raise InstrumentError(str(e))
        lanes = {k[5:]: v for k, v in (auto or {}).items() if k.startswith('lane.')}
        return mimic_perform.perform(prof, notes, total_n, sr, bpm=bpm, lanes=lanes, beat0=beat0,
                                     mimic_params=p.get('params'), perform=p['perform'])
    out = np.zeros((2, total_n))
    for st, m, d, v in notes:
        s0 = int(round(st * sr))
        n = int((d + p['tail']) * sr)             # the whole note, as in render_code
        if n <= 0 or s0 >= total_n:
            continue
        y = mimic.render(prof, midi_to_hz(m), np.arange(n) / sr, v / 127, d, sr, **(p.get('params') or {}))
        k = min(y.shape[1], n, total_n - s0)
        out[:, s0:s0 + k] += y[:, :k]
    return out


# ------------------------------------------------------------------ entry point

def render_instrument(inst, notes, total_n, auto=None, bpm=120.0, sr=SR, root=None, beat0=0.0, bpb=4):
    """inst: normalized instrument; notes: [(start_sec, midi, dur_sec, vel)]; -> (2, total_n). root = project
    directory (song voices in <root>/voices/). beat0: the song beat at sample 0 (performers key notes on it; synths
    and drums seed on where a note sits in its bar of bpb beats)."""
    auto = auto or {}
    t = inst['type']
    if t == 'synth':
        out = render_synth(inst, notes, total_n, auto, bpm, sr, beat0, bpb) * SYNTH_GAIN
    elif t == 'sampler':
        out = render_sampler(inst, notes, total_n, auto, sr, beat0, bpm, bpb)
    elif t == 'code':
        out = render_code(inst, notes, total_n, sr, bpm, root, auto, beat0)
    elif t == 'mimic':
        out = render_mimic(inst, notes, total_n, sr, root, auto, bpm, beat0)
    elif t == 'kit':
        out = np.zeros((2, total_n))
        by = {}
        for nt in notes:
            by.setdefault(str(nt[1]), []).append(nt)
        for key, nts in by.items():
            if key not in inst['map']:
                continue  # unmapped pitches are silent; notes_read flags them
            out += render_instrument(inst['map'][key], nts, total_n, None, bpm, sr, root, beat0, bpb)
    elif t in DRUM_DEFAULTS:
        out = np.zeros((2, total_n))
        for st, m, d, v in notes:
            s0 = int(round(st * sr))
            y = render_drum(inst, v, sr, seed=_bar_key(st, beat0, bpm, bpb, sr)) * DRUM_GAIN
            n = min(len(y), total_n - s0)
            if n > 0:
                out[0, s0:s0 + n] += y[:n]
                out[1, s0:s0 + n] += y[:n]
    else:
        raise InstrumentError(f"unknown instrument type {t}")
    g = dsp.undb(_curve(auto, 'gain_db', 0, total_n, inst.get('gain_db', 0.0)))
    pan = inst.get('pan', 0.0)
    if pan:
        gl, gr = dsp.pan_gains(pan)
        out = out * np.array([[gl], [gr]])
    return out * g
