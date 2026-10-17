"""Emilyguitar (Karoryfer Samples, CC0-1.0, v1.001) as a performer: an Epiphone solidbody recorded DI (both
pickups, flatwounds), sampled every minor third with 4 velocity layers and 3 round robins. The output is the DI:
put the rig after it (amp, cab ...), as with the electric voice.
Per note: the nearest sample (<= 1.5 semitones, played faster or slower), the velocity layer from the note's
velocity, the round robin walked by the note's song position (neighbours never repeat), a short damping at the note's
end with the recorded release noise. twelve > 0 adds a 12-string's courses: the octave string under each of the
four lower courses (E2..G3 + an octave) and a unison string above, a few ms apart and a few cents off, picked in
the order an upstroke or downstroke meets them.
Articulation (the electric voice's conventions): mono=True chains notes that overlap the one before onto one string
(legato: a hammer-on, a 6 ms step; a fretted slide when the slide lane is up), the sample warped along the pitch
path; lanes bend (semitones), vib (cents, the electric voice's player vibrato) and slide (0..1); attack_cents is the
sharp start of a hard pick (the twang), settling in ~60 ms. A warped note has no 12-string courses.
Samples: not in git; samples_fetch('emilyguitar') downloads them on the person's word (ismail.samples)."""
import glob
import os
import re

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
INFO = {
    "summary": "Emilyguitar (Karoryfer, CC0) DI samples: velocity layers, round robins, release noise; optional 12-string courses",
    "range": "Db2-D6 (shifted up to 1.5 semitones from the nearest sample)",
    "velocity": "picks the layer: p <= 40, mp <= 80, mf <= 120, f above",
    "params": {"twelve": "level of the 12-string course strings, 0 = a 6-string", "release_db": "release-noise level, dB",
               "humanize_ms": "timing jitter, ms", "detune_cents": "12-string course detune, cents", "level": "output gain",
               "pan": "-1..1 (mono DI)", "mono": "legato chains (overlapping notes = hammer-on / slide)",
               "attack_cents": "pitch overshoot of a hard pick, cents", "vib_rate": "vibrato rate Hz"},
    "lanes": {"bend": "semitones on the sounding notes", "vib": "vibrato depth, cents", "slide": "0..1: legato steps become fretted slides"},
    "source": "https://github.com/sfzinstruments/karoryfer.emilyguitar (CC0-1.0)",
    "measured": "sampled: the real guitar's notes, played back (samples_fetch('emilyguitar'))",
}
NOTE = {'c': 0, 'db': 1, 'd': 2, 'eb': 3, 'e': 4, 'f': 5, 'gb': 6, 'g': 7, 'ab': 8, 'a': 9, 'bb': 10, 'b': 11}
LAYERS = [('p', 40), ('mp', 80), ('mf', 120), ('f', 128)]
_cache = {}


def _root():
    from ismail import samples
    return samples.need('emilyguitar')


def _index():
    if 'idx' in _cache:
        return _cache['idx']
    idx = {}
    for f in glob.glob(os.path.join(_root(), 'notes', '*.wav')):
        m = re.match(r'([a-g]b?)(\d)_(p|mp|mf|f)_rr(\d)\.wav$', os.path.basename(f))
        if m:
            midi = NOTE[m.group(1)] + 12 * (int(m.group(2)) + 1)
            idx.setdefault(midi, {}).setdefault(m.group(3), {})[int(m.group(4))] = f
    rel = {}
    for f in glob.glob(os.path.join(_root(), 'release', '*.wav')):
        m = re.match(r'([a-g]b?)(\d)_release_rr(\d)\.wav$', os.path.basename(f))
        if m:
            rel.setdefault(NOTE[m.group(1)] + 12 * (int(m.group(2)) + 1), {})[int(m.group(3))] = f
    _cache['idx'] = (idx, rel)
    return idx, rel


def _load(f, sr, semis):
    key = (f, sr, round(semis, 4))
    if key in _cache:
        return _cache[key]
    import soundfile as sf
    from fractions import Fraction
    from scipy import signal
    y, fsr = sf.read(f, dtype='float64', always_2d=True)
    y = y.mean(1)
    ratio = fsr / sr * 2 ** (semis / 12.0)
    if abs(ratio - 1) > 1e-6:
        fr = Fraction(ratio).limit_denominator(1000)
        y = signal.resample_poly(y, fr.denominator, fr.numerator)
    _cache[key] = y
    return y


def _rng(beat, m, salt):
    return np.random.default_rng([int(round(beat * 960)) % (2 ** 31), int(m), int(salt)])


def _one(out, m, st, dur, v, beat, sr, gain, release_db, humanize_ms, cents=0.0, salt=0):
    idx, rel = _index()
    base = min(idx, key=lambda k: (abs(k - m), k))
    if abs(base - m) > 2:
        return
    layer = next(n for n, top in LAYERS if v <= top)
    takes = idx[base].get(layer) or next(iter(idx[base].values()))
    rr = sorted(takes)[(int(round(beat * 4)) + m + salt) % len(takes)]
    r = _rng(beat, m, 7 + salt)
    y = _load(takes[rr], sr, (m - base) + cents / 100.0)
    i0 = max(0, int(round((st + r.normal(0, humanize_ms / 1000.0)) * sr)))
    n_note = int(dur * sr)
    if i0 >= len(out):
        return
    y = y.copy()
    damp = int(0.07 * sr)                                    # the string is stopped: 70 ms to silence
    if n_note + damp < len(y):
        y[n_note:n_note + damp] *= np.linspace(1, 0, damp) ** 2
        y[n_note + damp:] = 0
        if release_db > -90 and rel:
            rb = min(rel, key=lambda k: abs(k - m))
            rf = rel[rb][sorted(rel[rb])[(int(round(beat * 4)) + salt) % len(rel[rb])]]
            ry = _load(rf, sr, 0.0) * 10 ** (release_db / 20)
            j = n_note
            k = min(len(ry), len(y) - j)
            y[j:j + k] += ry[:k]
    k = min(len(y), len(out) - i0)
    out[i0:i0 + k] += gain * y[:k]


def _lane(lanes, name, a, b):
    c = lanes.get(name)
    if c is None:
        return np.zeros(b - a)
    c = np.asarray(c, dtype=np.float64)
    seg = c[a:b]
    if len(seg) < b - a:
        seg = np.concatenate([seg, np.full(b - a - len(seg), c[-1] if len(c) else 0.0)])
    return seg


def _chain(out, ch, beat, sr, release_db, humanize_ms, lanes, attack_cents, vib_rate, cents_of=None):
    """A legato chain (or one note with expression) on one string: the first note's sample warped along the pitch
    path (steps or fretted slides at the legato notes, pick overshoot, bend and vibrato lanes)."""
    from ismail.voices.guitar.electric import _vibrato
    idx, rel = _index()
    st0, m0, _, v0 = ch[0]
    end = max(c[0] + c[2] for c in ch)
    base = min(idx, key=lambda k: (abs(k - m0), k))
    if abs(base - m0) > 2:
        return
    layer = next(n for n, top in LAYERS if v0 <= top)
    takes = idx[base].get(layer) or next(iter(idx[base].values()))
    rr = sorted(takes)[(int(round(beat * 4)) + m0) % len(takes)]
    r = _rng(beat, m0, 7)
    y = _load(takes[rr], sr, 0.0)
    i0 = max(0, int(round((st0 + r.normal(0, humanize_ms / 1000.0)) * sr)))
    if i0 >= len(out):
        return
    n_note = int((end - st0) * sr)
    damp = int(0.07 * sr)
    n = min(len(out) - i0, n_note + damp + int(0.3 * sr))
    t = np.arange(n) / sr
    path = np.full(n, float(m0))
    slide = _lane(lanes, 'slide', i0, i0 + n)
    for j in range(1, len(ch)):
        k0 = int((ch[j][0] - st0) * sr)
        if k0 >= n:
            break
        prev, new = ch[j - 1][1], ch[j][1]
        if slide[min(k0, n - 1)] > 0.5:                       # a fretted slide: arrives on the new note's time
            dur = min(0.12, max(0.03, abs(new - prev) * 0.018))
            ka = max(0, k0 - int(dur * sr))
            p = prev + (new - prev) * np.linspace(0, 1, k0 - ka) ** 1.3
            fl = np.floor(p)
            path[ka:k0] = fl + np.clip((p - fl - 0.75) / 0.25, 0, 1)
            path[k0:] = new
        else:                                                   # hammer-on / pull-off
            k1 = min(n, k0 + int(0.006 * sr))
            path[k0:k1] = np.linspace(prev, new, k1 - k0)
            path[k1:] = new
    semis = path - base + (attack_cents * (v0 / 127.0) ** 2 / 100.0) * np.exp(-t / 0.06)
    if cents_of is not None:                                    # the tuning table, a step at each legato note
        tc = np.full(n, cents_of(m0) / 100.0)
        for c in ch[1:]:
            k0 = int((c[0] - st0) * sr)
            if k0 < n:
                tc[k0:] = cents_of(c[1]) / 100.0
        semis = semis + tc
    semis += _lane(lanes, 'bend', i0, i0 + n)
    vd = _lane(lanes, 'vib', i0, i0 + n)
    if np.any(vd > 0):
        semis += vd / 100.0 * _vibrato(n, sr, vib_rate, r)
    pos = np.cumsum(2.0 ** (semis / 12.0)) - 1.0
    ok = pos < len(y) - 2
    w = np.zeros(n)
    w[ok] = np.interp(pos[ok], np.arange(len(y)), y)
    if n_note + damp < n:                                       # the string is stopped at the chain's end
        w[n_note:n_note + damp] *= np.linspace(1, 0, damp) ** 2
        w[n_note + damp:] = 0
        if release_db > -90 and rel:
            rb = min(rel, key=lambda k: abs(k - ch[-1][1]))
            rf = rel[rb][sorted(rel[rb])[int(round(beat * 4)) % len(rel[rb])]]
            ry = _load(rf, sr, 0.0) * 10 ** (release_db / 20)
            k = min(len(ry), n - n_note)
            w[n_note:n_note + k] += ry[:k]
    out[i0:i0 + n] += w


def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, twelve=0.0, release_db=-18.0, humanize_ms=3.0,
            detune_cents=4.0, level=1.0, pan=0.0, mono=False, attack_cents=0.0, vib_rate=5.4, cents_of=None, **_):
    """cents_of(midi): the instrument's tuning table, cents added to each note."""
    out = np.zeros(total_n)
    lanes = lanes or {}
    expr = attack_cents > 0 or any(np.any(np.asarray(lanes.get(k, [0])) != 0) for k in ('bend', 'vib'))
    ns = sorted(((float(st), int(m), float(d), v) for st, m, d, v in notes), key=lambda z: (z[0], -z[1]))
    chains = []
    for z in ns:
        if mono and chains and chains[-1][-1][0] + 1e-3 < z[0] < chains[-1][-1][0] + chains[-1][-1][2] - 1e-3:
            chains[-1].append(z)
        else:
            chains.append([z])
    for ch in chains:
        st, m, dur, v = ch[0]
        beat = beat0 + st * bpm / 60.0
        if len(ch) > 1 or expr:
            _chain(out, ch, beat, sr, release_db, humanize_ms, lanes, attack_cents, vib_rate, cents_of)
            continue
        c = cents_of(m) if cents_of else 0.0
        _one(out, m, st, dur, v, beat, sr, 1.0, release_db, humanize_ms, c)
        if twelve > 0:
            # 12-string courses: an octave string under the four lower courses (to G3), a unison string above
            up = (int(round(beat * 2)) % 2) == 1                 # off-beat 8ths: upstroke meets the course string first
            dt = (-0.004 if up else 0.004)
            if m <= 55:
                _one(out, m + 12, st + dt, dur, max(1, v - 10), beat, sr, twelve, -90.0, humanize_ms, detune_cents + c,
                     salt=1)
            else:
                _one(out, m, st + dt, dur, max(1, v - 6), beat, sr, twelve, -90.0, humanize_ms, -detune_cents + c,
                     salt=2)
    gl, gr = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    return np.stack([out * gl, out * gr]) * 0.8 * level
