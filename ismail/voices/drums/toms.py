"""Toms that change with how they are struck: the real Big Rusty Drums toms (Karoryfer, CC0; 14", 15", 18" and a 22"
kick played as a floor tom), each hit picked from the recorded velocity layer and round robin, then reshaped for
where and how the stick lands.

- pitch: the note is the drum's sounding pitch. The nearest real drum is retuned to it (playback speed, as
  tightening or slackening the head), so a low octave is a floor tom and a high one a small rack tom. Each recorded
  layer is retuned from its own measured pitch (toms_pitch.json) to the pitch a drum at the note has at that
  velocity, interpolated between the four real drums, and its ring is bent back to that drum's decay (playing a
  sample faster also shortens its ring). Away from a real drum's own pitch, each hit is then checked as rendered
  and corrected toward the real drums as the whole voice measures them (toms_cal.json): its pitch per round robin,
  and its ring bend fitted to their level envelope (the closed loop; its corrections are cached in memory and in
  ~/.ismail/cache/toms, or $ISMAIL_TOMS_CACHE).
- velocity: picks the recorded layer: a harder hit is louder, brighter, and starts sharp and glides down (the head
  stretches; up to +1.3 semitones on the 14", +5 on the 22", measured per layer).
- pos (0 dead center, 0.25 as recorded, 1 at the edge): the fundamental gives up EDGE_DB toward the edge and its
  energy goes to the overtones (the ring keeps its energy: the stick is struck as hard), and the attack gets
  brighter. Fitted on the only real position pairs, the Big Rusty snare's center and edge hits: its edge hits keep
  4 dB less of the ring in the lowest mode than its center hits at the same loudness, and a 6 dB give-up gives
  that. The ideal membrane's mode shapes predicted 10-14 dB and lost to doing nothing. Toward the center the
  overtones give up CENTER_DB instead (a rounder thud; a mirror of the edge, not measured).
- stroke: stick, mallet, brush (the recorded articulations), rimshot (stick at the rim plus the rim's crack from the
  recorded rim click), rimclick (stick laid across the rim), shell (stick on the shell), dead (stick left on the
  head: the ring is choked after the hit).

perform() takes notes (start s, midi, dur s, velocity) and the lanes 'pos' (0..1) and 'stroke' (a number: the index
in STROKES), read at each written onset; the params pos and stroke set them for the whole track.

Samples: not in git. The voice looks for a Big Rusty Samples folder (one holding tom_14, tom_15, tom_18 and tom_22)
in $TOM_SAMPLES, then in the project's ref/ (or the ref/ beside it, for a project kept in a song's proj/ folder),
then in the engine's big_rusty set (samples_fetch('big_rusty'), which brings every tom folder and stroke)."""
import glob
import hashlib
import json
import os
import re

import numpy as np
from scipy import signal

HERE = os.path.dirname(os.path.abspath(__file__))
INFO = {
    "summary": "realistic acoustic toms (Big Rusty, CC0): real recorded hits retuned to the note; where the stick "
               "lands (center to rim) and how (stick, mallet, brush, rimshot, rim click, shell, dead stroke) per hit, "
               "velocity picks the real layer",
    "range": "F#1-A3 heard as real; the note is the drum's pitch (the real drums sit at 45.5, 62.3, 91 and 95.3 Hz: "
             "about F#1, B1, F#2, G2), not a GM kit slot",
    "velocity": "picks the recorded layer (louder, brighter, a harder hit starts sharp and glides down) and scales "
                "within it",
    "lanes": {"pos": "0 dead center .. 0.25 as recorded .. 1 rim, read at each written onset "
                     "(automation 'inst.lane.pos')",
              "stroke": "index into STROKES: 0 stick, 1 mallet, 2 brush, 3 rimshot, 4 rimclick, 5 shell, 6 dead, read "
                        "at each written onset ('inst.lane.stroke'). Automation is linear between points: hold a "
                        "stroke with a second point just before the next change ([[3, 3], [3.99, 3], [4, 1]])"},
    "params": {"pos": "strike position for the whole track when no lane is set, default 0.25",
               "stroke": "stick mallet brush rimshot rimclick shell dead, for the whole track when no lane is set",
               "drum": "auto (the nearest drum by pitch), or 14 15 18 22 to force one drum",
               "close_db / oh_db / btm_db": "mic levels, dB (0 / -6 / -9)", "rim_db": "rimshot crack level, dB (5)",
               "spread": "velocity-layer spread, layers (0.5)", "humanize_ms": "timing jitter, ms (2)",
               "level": "output gain"},
    "choose": "toms heard on their own in played, acoustic or live music: rock and funk fills, cinematic and tribal "
              "tom grooves, marching-style patterns, singer-songwriter and jazz kits (brushes, mallets); melodic "
              "toms, where the note is the drum's pitch; mallet swells, rimshot accents, dead-stroke staccato. Not "
              "for electronic toms (808/909, Simmons: kit70 or a synth). For the whole kit, rusty stays the kit; "
              "this voice is the toms done properly",
    "render": "about 2 s per hit at 48 kHz (an STFT reshape per hit). The first time a drum, stroke, layer and round "
              "robin is played away from the drum's own pitch it renders 3 more times to check itself (cached)",
    "source": "Big Rusty Drums, Karoryfer Samples, CC0 1.0, https://github.com/sfzinstruments/karoryfer.big-rusty-drums "
              "(samples_fetch('big_rusty'))",
    "measured": "on the Big Rusty toms, every layer and mic: the modes (toms_modes.json), each layer's pitch and ring "
                "(toms_pitch.json), each layer as the whole voice plays it (toms_cal.json); position fitted on the "
                "Big Rusty snare's center and edge hits",
    "built": "in a song's percussion study (2026-10), handed to the library after its blind exam; the song keeps its "
             "own copy",
    "exams": "2026-10-14, one listener: an open listen (21 single hits and a fill, none marked fake), then a blind "
             "exam of 5 pairs, real tom against this voice retuned from another drum (14\" up to the 15\", 15\" down "
             "to the 18\", 18\" up to the 15\" by 6.5 semitones, 18\" down to the 22\"): the real one was not found "
             "in any pair",
}
STROKES = ['stick', 'mallet', 'brush', 'rimshot', 'rimclick', 'shell', 'dead']
FOLDER = {'stick': 'center', 'mallet': 'mallet', 'brush': 'brush', 'rimshot': 'center', 'rimclick': 'rc',
          'shell': 'sc', 'dead': 'center'}
DRUMS = {'14': ('tom_14', 95.3, -0.25), '15': ('tom_15', 91.0, 0.05), '18': ('tom_18', 62.3, 0.35),
         '22': ('tom_22', 45.5, 0.5)}
NEEDS = tuple(d[0] for d in DRUMS.values())  # the Big Rusty folders a Samples folder must hold for this voice
POS_REF = 0.25                     # pos of the recorded hits
EDGE_DB = 6.0                      # the fundamental's give-up at pos 1 (fitted, see the docstring)
CENTER_DB = 4.0                    # the overtones' give-up at pos 0 (a guess)
BRIGHT_DB = 6.0                    # attack tilt above 1.5 kHz at pos 1: the edge attack centroid 1.14x (real 1.15x)
RIM_DB = 5.0                       # rimshot crack level: centroid about 1.7x the center hit (real 1.5-2x)
_cache = {}


class TomSamplesError(FileNotFoundError):
    pass


def _holds(d):
    return bool(d) and all(os.path.isdir(os.path.join(d, n)) for n in NEEDS)


def _in_ref(ref, depth=3):
    """The first folder under ref/ (itself included, `depth` levels down) that holds the Big Rusty tom folders."""
    if not os.path.isdir(ref):
        return None
    level = [ref]
    for _ in range(depth + 1):
        nxt = []
        for d in level:
            if _holds(d):
                return d
            try:
                nxt += [os.path.join(d, f) for f in sorted(os.listdir(d))
                        if not f.startswith(('.', '_')) and os.path.isdir(os.path.join(d, f))]
            except OSError:
                pass
        level = nxt
    return None


def candidates(project_root=None):
    """[(where, folder)] in the order the voice tries them: $TOM_SAMPLES, the project's ref/ (and the ref/ beside a
    project kept in a song's proj/), then every folder the engine's big_rusty set has (its env var, a registered
    folder, the store)."""
    out = [('$TOM_SAMPLES', os.environ.get('TOM_SAMPLES') or None)]
    if project_root:
        root = os.path.abspath(project_root)
        for ref in (os.path.join(root, 'ref'), os.path.join(os.path.dirname(root), 'ref')):
            out.append((ref, _in_ref(ref) or ref))
    try:
        from ismail import samples
        out += [("big_rusty set", p) for p in samples.paths('big_rusty')]
    except Exception:
        pass
    return out


def samples_dir(project_root=None):
    """The Big Rusty Samples folder this voice plays, or an error that says what to fetch or set."""
    key = ('samp', os.path.abspath(project_root) if project_root else None, os.environ.get('TOM_SAMPLES'))
    if key in _cache:
        return _cache[key]
    tried = candidates(project_root)
    for _, d in tried:
        if _holds(d):
            _cache[key] = os.path.abspath(d)
            return _cache[key]
    seen = []
    for _, d in tried:
        if d and os.path.isdir(d):
            seen.append(f"{d} has no {', '.join(n for n in NEEDS if not os.path.isdir(os.path.join(d, n)))}")
    size, lic = 520, 'CC0 1.0'
    try:
        from ismail import samples
        size, lic = samples.SETS['big_rusty']['size_mb'], samples.SETS['big_rusty']['licence']
    except Exception:
        pass
    raise TomSamplesError(
        "the toms voice plays the Big Rusty toms (Karoryfer, CC0), and no folder here holds "
        f"{', '.join(NEEDS)}" + (f" ({'; '.join(seen)})" if seen else '') + ". Ask the person, then "
        f"samples_fetch('big_rusty'): about {size} MB, {lic}; it brings every tom folder and stroke and skips files "
        "already there. Or set $TOM_SAMPLES to a Big Rusty Samples folder, or put one in the project's ref/.")


def samples_ready(project_root=None):
    """True when a Samples folder with the toms is on this machine (sketch asks before it reaches for the voice)."""
    try:
        samples_dir(project_root)
        return True
    except TomSamplesError:
        return False


def _modes(drum):
    """The drum's ringing modes: (Hz at its own pitch, level dB re the fundamental), every overtone found in at
    least half the stick layers (toms_modes.json)."""
    if ('modes', drum) not in _cache:
        with open(os.path.join(HERE, 'toms_modes.json'), encoding='utf8') as f:
            _cache[('modes', drum)] = [tuple(m) for m in json.load(f)[drum]]
    return _cache[('modes', drum)]


def position_gains(modes, pos):
    """Per-mode gains for a hit at pos (0 center, 0.25 as recorded, 1 edge), the ring's energy kept."""
    lv = 10 ** (np.array([db for _, db in modes]) / 10)
    g = np.ones(len(modes))
    if pos >= POS_REF:
        g[0] = 10 ** (-EDGE_DB * (pos - POS_REF) / (1 - POS_REF) / 20)
    else:
        g[1:] = 10 ** (-CENTER_DB * (POS_REF - pos) / POS_REF / 20)
    return list(g / np.sqrt((lv * g ** 2).sum() / lv.sum()))


def _files(samp, drum, folder):
    """{mic: {layer: [files by round robin]}} of one drum's stroke folder."""
    key = ('files', samp, drum, folder)
    if key not in _cache:
        out = {}
        for p in glob.glob(os.path.join(samp, drum, folder, '*', '*.flac')):
            mic = os.path.basename(os.path.dirname(p))
            mm = re.search(r'_vl(\d+)_rr(\d+)', os.path.basename(p))
            if mm:
                out.setdefault(mic, {}).setdefault(int(mm.group(1)), {})[int(mm.group(2))] = p
        if not out:
            raise TomSamplesError(f"{os.path.join(samp, drum, folder)} holds no samples: this copy of Big Rusty is "
                                  f"partial. samples_fetch('big_rusty') brings every tom stroke folder (center, "
                                  f"mallet, brush, rc, sc) and skips files already there")
        _cache[key] = {mic: {vl: [d[k] for k in sorted(d)] for vl, d in v.items()} for mic, v in out.items()}
    return _cache[key]


def _read(p, sr, ratio):
    """A sample at sr, played `ratio` times faster (retuned)."""
    key = ('y', p, sr, round(ratio, 5))
    if key not in _cache:
        import soundfile as sf
        from fractions import Fraction
        y, fsr = sf.read(p, dtype='float64')
        y = y if y.ndim == 1 else y.mean(1)
        fr = Fraction(fsr / sr * ratio).limit_denominator(600)
        if fr != 1:
            y = signal.resample_poly(y, fr.denominator, fr.numerator)
        _cache[key] = y
    return _cache[key]


def _onset(y):
    e = np.abs(y)
    return int(np.argmax(e > 0.1 * e.max()))


def reshape(y, sr, modes, gains, choke=None, bright_db=0.0):
    """Scale each ringing mode of a recorded hit by its gain, following the mode's own pitch as it glides
    (tracked frame by frame from the recording), choke the ring after the hit (a dead stroke: (start s, tau s)),
    and tilt the first 20 ms brighter (bright_db above 1.5 kHz)."""
    if all(abs(g - 1) < 1e-3 for g in gains) and choke is None and abs(bright_db) < 1e-3:
        return y
    N, H = 4096, 128
    f, t, Z = signal.stft(y, sr, window='hann', nperseg=N, noverlap=N - H, boundary='even', padded=True)
    A = np.abs(Z)
    i0 = _onset(y) / sr
    M = np.ones_like(A)
    fs = np.array([m[0] for m in modes])
    for k, (fk, g) in enumerate(zip(fs, gains)):
        if abs(g - 1) < 1e-3:
            continue
        hi = min(1.35, (fs[k + 1] / fk) * 0.97) if k + 1 < len(fs) else 1.35
        lo = max(0.97, (fs[k - 1] / fk) * 1.03) if k > 0 else 0.97
        band = np.where((f >= fk * lo) & (f <= fk * hi))[0]
        if len(band) == 0:
            continue
        w = max(0.03 * fk, 1.5 * (f[1] - f[0]))
        fc = np.where(t - i0 < 0.6, f[band[np.argmax(A[band], 0)]], fk)          # the mode's pitch in each frame
        M *= 1 + (g - 1) * np.exp(-0.5 * ((f[:, None] - fc[None, :]) / w) ** 2)
    if choke is not None:
        s, tau = choke
        env = np.where(t - i0 > s, np.exp(-(t - i0 - s) / tau), 1.0)
        M *= env[None, :] ** (f < 4000)[:, None]
    if abs(bright_db) > 1e-3:
        tilt = 10 ** (bright_db / 20 * np.clip(np.log2(np.maximum(f, 1) / 1500) + 1, 0, 1))
        early = np.clip(1 - (t - i0 - 0.02) / 0.03, 0, 1)
        M *= 1 + (tilt[:, None] - 1) * early[None, :]
    _, out = signal.istft(Z * M, sr, window='hann', nperseg=N, noverlap=N - H, boundary=True)
    return out[:len(y)]


def _pitch():
    """toms_pitch.json: each drum, folder and velocity layer's pitch (cents re the drum's base) and ring (tau, s),
    with misreads replaced by the folder's median (a pitch 60 c or more off it, a ring off by 2x or more: the 18"
    mallet's softest layer read +66 c among -27..-90)."""
    if 'pitch' not in _cache:
        with open(os.path.join(HERE, 'toms_pitch.json'), encoding='utf8') as f:
            raw = json.load(f)
        out = {}
        for drum, folders in raw.items():
            for folder, layers in folders.items():
                ks = sorted(layers, key=int)
                c = np.array([layers[k]['cents'] for k in ks], float)
                t = np.array([layers[k]['tau'] or np.nan for k in ks], float)
                mc, mt = np.median(c), np.nanmedian(t)
                c = np.where(np.abs(c - mc) >= 60, mc, c)
                t = np.where(np.isnan(t) | (np.abs(np.log(t / mt)) >= np.log(2)), mt, t)
                out[(drum, folder)] = (c, t)
        _cache['pitch'] = out
    return _cache['pitch']


def _at(name, folder, layer, nl):
    """(cents, tau) of a drum's recorded layer, or None for a folder not measured."""
    v = _pitch().get((name, folder))
    if v is None:
        return None
    c, t = v
    x = np.arange(1, len(c) + 1) / len(c)
    return float(np.interp(layer / nl, x, c)), float(np.interp(layer / nl, x, t))


def _want(f0, folder, frac, table=None):
    """(cents, tau) a drum tuned to f0 has at this velocity (layer fraction): the four real drums' curves,
    interpolated in log pitch (held past the lowest and highest drum). At a real drum's own pitch it is that drum.
    table: _cal() for (cents, ring) as the whole voice measures them."""
    d = sorted(DRUMS.values(), key=lambda d: d[1])
    cs, ts, fs = [], [], []
    for name, base, _ in d:
        v = (table if table is not None else _pitch()).get((name, folder))
        if v is None:
            return None
        c, t = v
        x = np.arange(1, len(c) + 1) / len(c)
        cs.append(np.interp(frac, x, c))
        ts.append(np.interp(frac, x, np.log(t)))
        fs.append(np.log(base))
    lf = np.log(f0)
    return float(np.interp(lf, fs, cs)), float(np.exp(np.interp(lf, fs, ts)))


def _ring(y, sr, slope, after=0.03, max_db=30.0):
    """Bend a hit's ring by slope dB/s from `after` s past the onset, at most max_db up or down: playing a sample
    faster to tune it up also shortens its ring (an 18" tuned up to the 15" rang 0.19 s to -30 dB, the real 15"
    0.44 s)."""
    t = np.maximum((np.arange(len(y)) - _onset(y)) / sr - after, 0)
    return y * 10 ** (np.clip(slope * t, -max_db, max_db) / 20)


def _cal():
    """toms_cal.json: every real drum, folder and layer as this voice plays it at the drum's own pitch (all mics,
    median over round robins): the fundamental (cents re the drum's base, 50-400 ms after the onset), the ring (s to
    30 dB under the peak) and the level envelope (dB re peak, 10 ms frames from the onset), misreads cleaned as in
    _pitch (a misread layer's envelope is the folder's median)."""
    if 'cal' not in _cache:
        with open(os.path.join(HERE, 'toms_cal.json'), encoding='utf8') as f:
            raw = json.load(f)
        out = {}
        for drum, folders in raw.items():
            for folder, layers in folders.items():
                ks = sorted(layers, key=int)
                c = np.array([layers[k]['cents'] for k in ks], float)
                r = np.array([layers[k]['ring'] for k in ks], float)
                e = np.array([layers[k]['env'] for k in ks], float)
                mc, mr = np.median(c), np.median(r)
                c = np.where(np.abs(c - mc) >= 60, mc, c)
                bad = np.abs(np.log(r / mr)) >= np.log(2)
                r = np.where(bad, mr, r)
                e[bad] = np.median(e, 0)
                out[(drum, folder)] = (c, r)
                out[('env', drum, folder)] = e
        _cache['cal'] = out
    return _cache['cal']


def envelope(y, sr, n=150):
    """Level in 10 ms frames from the onset, dB re the loudest frame, n frames (floor -80)."""
    a, h = _onset(y), int(0.01 * sr)
    x = np.zeros(n * h)
    x[:min(n * h, len(y) - a)] = y[a:a + n * h]
    e = 10 * np.log10(np.mean(x.reshape(n, h) ** 2, 1) + 1e-20)
    return np.maximum(e - e.max(), -80.0)


def _want_env(f0, folder, frac):
    """The level envelope a drum at f0 has at this velocity: _want's interpolation, frame by frame in dB."""
    d = sorted(DRUMS.values(), key=lambda d: d[1])
    es, fs = [], []
    for name, base, _ in d:
        e = _cal()[('env', name, folder)]
        x = np.arange(1, len(e) + 1) / len(e)
        es.append([np.interp(frac, x, e[:, j]) for j in range(e.shape[1])])
        fs.append(np.log(base))
    es = np.array(es)
    return np.array([np.interp(np.log(f0), fs, es[:, j]) for j in range(es.shape[1])])


def measure(y, sr, f_guess):
    """(fundamental Hz near f_guess +-25%, 50-400 ms after the onset; s from the onset until 10 ms frames stay 30 dB
    under their peak): how the closed loop, and the blind exam's gate, read a hit."""
    a = _onset(y)
    x = y[a + int(0.05 * sr):a + int(0.4 * sr)]
    n = 1 << 18
    F = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    fr = np.fft.rfftfreq(n, 1 / sr)
    m = (fr > f_guess * 0.75) & (fr < f_guess * 1.25)
    h = int(0.01 * sr)
    k = (len(y) - a) // h
    e = 10 * np.log10(np.mean(y[a:a + k * h].reshape(k, h) ** 2, 1) + 1e-20)
    i = int(np.argmax(e))
    below = np.where(e[i:] < e[i] - 30)[0]
    return float(fr[m][np.argmax(F[m])]), (i + below[0]) * 0.01 if len(below) else k * 0.01


def pick_drum(f0, drum='auto'):
    if drum != 'auto':
        return DRUMS[str(drum)]
    return min(DRUMS.values(), key=lambda d: abs(np.log(f0 / d[1])))


# ------------------------------------------------------------------ the closed loop's corrections, kept on disk

def _disk_dir():
    return os.environ.get('ISMAIL_TOMS_CACHE') or os.path.join(os.path.expanduser('~'), '.ismail', 'cache', 'toms')


def _disk_file():
    """One JSON file per version of this voice's code and data: a change to either starts a new file, so an old
    correction is never applied to a new voice."""
    if 'disk_file' not in _cache:
        h = hashlib.sha1()
        for f in ('toms.py', 'toms_modes.json', 'toms_pitch.json', 'toms_cal.json'):
            with open(os.path.join(HERE, f), 'rb') as fh:
                h.update(fh.read())
        _cache['disk_file'] = f"fix_{h.hexdigest()[:12]}.json"
    return os.path.join(_disk_dir(), _cache['disk_file'])


def _disk_load():
    k = ('disk', _disk_file())
    if k not in _cache:
        try:
            with open(k[1], encoding='utf8') as f:
                d = json.load(f)
            _cache[k] = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _cache[k] = {}
    return _cache[k]


def _disk_key(sample, *rest):
    """The sample file (its name and size: the same take in any folder) and every argument the correction reads."""
    try:
        size = os.path.getsize(sample)
    except OSError:
        size = -1
    return '|'.join([os.path.basename(sample), str(size)] + [repr(x) for x in rest])


def _disk_put(k, v):
    """Add one correction; a failed write only means it is measured again next time."""
    d = _disk_load()
    d[k] = v
    try:
        os.makedirs(_disk_dir(), exist_ok=True)
        try:                                                     # another process may have added its own meanwhile
            with open(_disk_file(), encoding='utf8') as f:
                d = {**json.load(f), **d}
        except (OSError, ValueError, TypeError):
            pass
        tmp = f"{_disk_file()}.{os.getpid()}.tmp"
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump(d, f)
        os.replace(tmp, _disk_file())
        _cache[('disk', _disk_file())] = d
    except OSError:
        pass


# ------------------------------------------------------------------ hits

def hit(f0, vel, sr, pos=POS_REF, stroke='stick', drum='auto', rr=0, close_db=0.0, oh_db=-6.0, btm_db=-9.0,
        rim_db=RIM_DB, layer_spread=0.0, seed=0, project_root=None):
    """One tom hit: (L, R) at sr. f0 Hz, vel 1..127, pos 0 center .. 1 rim, stroke one of STROKES."""
    if stroke not in FOLDER:
        raise ValueError(f"stroke {stroke!r}: one of {', '.join(STROKES)}")
    samp = samples_dir(project_root)
    name, base, pan = pick_drum(f0, drum)
    if stroke == 'rimshot':
        pos = max(pos, 0.9)
    files = _files(samp, name, FOLDER[stroke])
    nl = max(max(v) for v in files.values())
    rng = np.random.default_rng(seed)
    layer = int(np.clip(round(vel / 127 * nl + rng.normal(0, layer_spread)), 1, nl))
    g = float(np.clip(vel / (layer / nl * 127), 0.6, 1.15))
    # retuned from this layer's own measured pitch to the pitch a drum at f0 has at this velocity (the layers of one
    # drum sit up to 75 c apart, and a mallet up to 60 c under the stick: one base pitch per drum put hits 45 c off)
    have, want = _at(name, FOLDER[stroke], layer, nl), _want(f0, FOLDER[stroke], layer / nl)
    ratio = f0 / base if have is None or want is None else (
        f0 * 2 ** (want[0] / 1200) / (base * 2 ** (have[0] / 1200)))
    slope = 8.686 * (ratio / have[1] - 1 / want[1]) if have and want else 0.0
    bright = BRIGHT_DB * float(np.clip((pos - POS_REF) / (1 - POS_REF), -0.5, 1))
    choke = (0.03, 0.07) if stroke == 'dead' else None

    def play(rr, ratio, slope):
        modes = [(fm * ratio * 2 ** ((have[0] if have else 0) / 1200), db) for fm, db in _modes(name)]
        gains = position_gains(modes, pos) if stroke in ('stick', 'mallet', 'brush', 'rimshot', 'dead') else \
            [1.0] * len(modes)
        L = R = None
        for mic, layers in files.items():
            lay = layers.get(layer) or layers[min(layers, key=lambda k: abs(k - layer))]
            y = reshape(_read(lay[rr % len(lay)], sr, ratio), sr, modes, gains, choke, bright)
            if slope:
                y = _ring(y, sr, slope)
            mg = {'cl': close_db, 'oh': oh_db, 'sn': btm_db}.get(mic, -12.0)
            a = g * 10 ** (mg / 20)
            p = pan if mic != 'oh' else pan * 1.4
            gl, gr = np.cos((p + 1) * np.pi / 4), np.sin((p + 1) * np.pi / 4)
            L = a * gl * y if L is None else _add(L, a * gl * y)
            R = a * gr * y if R is None else _add(R, a * gr * y)
        return L, R

    # away from a real drum's own pitch, the retune and ring bend are checked on the hit itself and corrected toward
    # the real drums' curves as the whole voice measures them (toms_cal.json): the close mic's pitch and the
    # fundamental's ring, bent and read in a 350 ms window, put the 18" 28 c sharp and 0.25-0.7 s long
    cal = _want(f0, FOLDER[stroke], layer / nl, _cal()) if have and want and abs(np.log(f0 / base)) > 1e-3 else None
    if cal is not None:
        key = ('fix', samp, name, stroke, layer, rr, round(f0, 3), round(pos, 3), sr, close_db, oh_db, btm_db)
        if key not in _cache:
            lays = {mic: ls.get(layer) or ls[min(ls, key=lambda k: abs(k - layer))] for mic, ls in files.items()}
            first = lays[sorted(lays)[0]]
            dk = _disk_key(first[rr % len(first)], name, stroke, layer, round(f0, 3), round(pos, 3), sr, close_db,
                           oh_db, btm_db, [(mic, rr % len(lays[mic])) for mic in sorted(lays)])
            got = _disk_load().get(dk)
            if got is not None:
                _cache[key] = tuple(got)
            else:
                # pitch: each round robin on its own (the 14" mallet's layers hold one stroke 21 c off its
                # siblings; a real drum at one tension and strength sits within 3 c); ring: the bend slope that best
                # fits the real drums' level envelope, least squares over frames 50 ms on, above -40 dB in the target
                # and -50 in mine (matching the -30 dB crossing swung 0.26-0.97 s: it is a threshold)
                ft = f0 * 2 ** (cal[0] / 1200)
                for _ in range(2):
                    fm, _r = measure(np.mean(play(rr, ratio, 0.0), 0), sr, ft)
                    ratio *= ft / fm
                e = envelope(np.mean(play(rr, ratio, 0.0), 0), sr)
                et = _want_env(f0, FOLDER[stroke], layer / nl)
                t = np.arange(len(e)) * 0.01 - 0.03
                m = (t >= 0.02) & (et > -40) & (e > -50)
                slope = float(np.sum((et - e)[m] * t[m]) / np.sum(t[m] ** 2)) if m.sum() > 3 else slope
                _cache[key] = (float(ratio), float(slope))
                _disk_put(dk, list(_cache[key]))
        ratio, slope = _cache[key]
    L, R = play(rr, ratio, slope)
    if stroke == 'rimshot':
        crack = _crack(samp, name, vel, sr, ratio, rr) * g * 10 ** (rim_db / 20)
        gl, gr = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        L, R = _add(L, gl * crack), _add(R, gr * crack)
    return np.stack([L, R])


def _crack(samp, name, vel, sr, ratio, rr):
    """The rim's crack: the close mic of the recorded rim click at the same strength, high-passed above the head's
    modes and cut to its first 60 ms."""
    files = _files(samp, name, 'rc')['cl']
    nl = max(files)
    layer = int(np.clip(round(vel / 127 * nl), 1, nl))
    y = _read(files[layer][rr % len(files[layer])], sr, 1.0).copy()
    i0 = _onset(y)
    sos = signal.butter(4, 900, 'hp', fs=sr, output='sos')
    y = signal.sosfilt(sos, y)
    n = int(0.06 * sr)
    env = np.zeros(len(y))
    env[:i0 + n] = 1.0
    env[i0 + n // 2:i0 + n] = np.linspace(1, 0, n - n // 2)
    return y * env


def _add(a, b):
    n = max(len(a), len(b))
    out = np.zeros(n)
    out[:len(a)] += a
    out[:len(b)] += b
    return out


def _lane(lanes, name, i, default):
    c = (lanes or {}).get(name)
    if c is None or len(c) == 0:
        return default
    return float(c[min(i, len(c) - 1)])


def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, pos=POS_REF, stroke='stick', drum='auto',
            close_db=0.0, oh_db=-6.0, btm_db=-9.0, rim_db=RIM_DB, spread=0.5, humanize_ms=2.0, level=1.0,
            project_root=None, cents_of=None, **_):
    """cents_of(midi): the instrument's tuning table, cents added to each hit's pitch."""
    out = np.zeros((2, total_n))
    for st, m, d, v in notes:
        beat = beat0 + st * bpm / 60.0
        seed = [int(round(beat * 960)) % (2 ** 31), int(m)]
        rng = np.random.default_rng(seed)
        i0 = max(0, int(round((st + rng.normal(0, humanize_ms / 1000)) * sr)))
        if i0 >= total_n:
            continue
        j = min(int(round(st * sr)), total_n - 1)          # lanes read on the written onset, before the jitter
        p = float(np.clip(_lane(lanes, 'pos', j, pos), 0, 1))
        s = stroke if (lanes or {}).get('stroke') is None else STROKES[int(np.clip(round(_lane(lanes, 'stroke', j, 0)), 0, len(STROKES) - 1))]
        f0 = 440.0 * 2 ** ((m - 69) / 12 + (cents_of(m) / 1200.0 if cents_of else 0.0))
        y = hit(f0, v, sr, p, s, drum, rr=int(round(beat * 4)) + int(m), close_db=close_db, oh_db=oh_db,
                btm_db=btm_db, rim_db=rim_db, layer_spread=spread, seed=seed, project_root=project_root)
        n = min(y.shape[1], total_n - i0)
        out[:, i0:i0 + n] += y[:, :n]
    return out * 0.7 * level
