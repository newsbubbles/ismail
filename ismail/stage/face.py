"""Mouth tracks for stage people (lip sync): a voice recording and its word times become ARKit face unit weights over
time (face.json), which the stage page plays on a person's mouth morphs (people exported with mouth shapes kept:
jawOpen, mouthFunnel, ... as glTF morph targets).

Words become phonemes with the CMU Pronouncing Dictionary when its data file is on this machine (cmudict.dict, from
$ISMAIL_CMUDICT or an installed `cmudict` package; the data is BSD licensed, nothing here imports the package), else
from the spelling. Phonemes become one of nine mouth shapes (the Preston Blair set: A closed lips for M B P, B teeth
for most consonants and EE, C open for EH, D wide for AA, E round for AO and ER, F pucker for OO and W, G lip under
teeth for F V, H for L, X rest), and each shape is a small mix of ARKit units (SHAPES). Inside a word consonants get a
short time and the vowels share the rest, so a held sung note is a held vowel. Loudness opens the jaw further on
loud vowels, and the mouth rests where nothing is said. With no words (no transcript) the track follows loudness
alone, rounding the lips when the voice is dark. The mouth leads the sound by LEAD_S, as real mouths do.

When Rhubarb Lip Sync is on this machine (rhubarb_path), it gives the shapes instead: it listens to the voice itself
(the words, when there are any, guide its recognizer), so the timing follows the sounds, not a split of each word's
span. Its output is the same nine Preston Blair shapes, so the rest of the track is made the same way.

face.json: {fps, start, seconds, units: [names], frames: [[weight per unit] per frame] (0..1, 2 decimals),
            shapes: [[t, shape, phoneme, word]], source}
start and every t are seconds on the recording's clock; frame i shows the mouth at start + i / fps.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

from . import perform

LEAD_S = 0.04              # the mouth shapes a sound this long before it is heard
CONSONANT_S = 0.06         # a consonant's share of a word (at most half the word)
GAP_REST_S = 0.12          # a pause between words longer than this goes to rest
ATTACK_S, RELEASE_S = 0.025, 0.06   # how fast the mouth moves toward a shape, and back from it

SHAPES = {
    'X': {},
    'A': {'mouthPressLeft': .45, 'mouthPressRight': .45, 'mouthRollLower': .2, 'mouthRollUpper': .15},
    'B': {'jawOpen': .12, 'mouthStretchLeft': .25, 'mouthStretchRight': .25, 'mouthSmileLeft': .1,
          'mouthSmileRight': .1},
    'C': {'jawOpen': .3, 'mouthStretchLeft': .15, 'mouthStretchRight': .15, 'mouthLowerDownLeft': .2,
          'mouthLowerDownRight': .2},
    'D': {'jawOpen': .6, 'mouthLowerDownLeft': .25, 'mouthLowerDownRight': .25},
    'E': {'jawOpen': .3, 'mouthFunnel': .35},
    'F': {'jawOpen': .1, 'mouthPucker': .7, 'mouthFunnel': .25},
    'G': {'jawOpen': .08, 'mouthRollLower': .55, 'mouthUpperUpLeft': .2, 'mouthUpperUpRight': .2},
    'H': {'jawOpen': .28, 'mouthLowerDownLeft': .1, 'mouthLowerDownRight': .1},
}
UNITS = [u for u in dict.fromkeys(k for s in SHAPES.values() for k in s)]
LOUD_UNITS = {'jawOpen', 'mouthLowerDownLeft', 'mouthLowerDownRight'}     # opened further by loudness
CLOSED = {'A', 'G'}                                                         # lips meet: never opened by loudness

# ARPAbet -> shape; a diphthong is two shapes (its vowel time split 60/40)
PHONE = {
    'AA': 'D', 'AE': 'C', 'AH': 'D', 'AO': 'E', 'AW': 'DF', 'AY': 'DB', 'EH': 'C', 'ER': 'E', 'EY': 'CB',
    'IH': 'B', 'IY': 'B', 'OW': 'EF', 'OY': 'EB', 'UH': 'F', 'UW': 'F',
    'B': 'A', 'M': 'A', 'P': 'A', 'F': 'G', 'V': 'G', 'W': 'F', 'R': 'E', 'L': 'H', 'HH': 'C', 'Y': 'B',
    'CH': 'B', 'D': 'B', 'DH': 'B', 'G': 'B', 'JH': 'B', 'K': 'B', 'N': 'B', 'NG': 'B', 'S': 'B', 'SH': 'B',
    'T': 'B', 'TH': 'B', 'Z': 'B', 'ZH': 'B',
}
VOWELS = {'AA', 'AE', 'AH', 'AO', 'AW', 'AY', 'EH', 'ER', 'EY', 'IH', 'IY', 'OW', 'OY', 'UH', 'UW'}
STRESS = {'0': .8, '1': 1.5, '2': 1.2}
# spelling -> phonemes when a word is not in the dictionary (crude, but the shapes are what matter)
LETTERS = [('oo', 'UW'), ('ee', 'IY'), ('ea', 'IY'), ('ou', 'AW'), ('ow', 'OW'), ('oa', 'OW'), ('ai', 'EY'),
           ('ay', 'EY'), ('th', 'TH'), ('sh', 'SH'), ('ch', 'CH'), ('ph', 'F'), ('ng', 'NG'), ('qu', 'K W'),
           ('a', 'AE1'), ('e', 'EH1'), ('i', 'IH1'), ('o', 'AA1'), ('u', 'AH1'), ('y', 'IY0'), ('b', 'B'),
           ('c', 'K'), ('d', 'D'), ('f', 'F'), ('g', 'G'), ('h', 'HH'), ('j', 'JH'), ('k', 'K'), ('l', 'L'),
           ('m', 'M'), ('n', 'N'), ('p', 'P'), ('r', 'R'), ('s', 'S'), ('t', 'T'), ('v', 'V'), ('w', 'W'),
           ('x', 'K S'), ('z', 'Z')]

ENGINES = ('auto', 'rhubarb', 'rhubarb_english', 'words')

_DICT = None


def _dict_path():
    p = os.environ.get('ISMAIL_CMUDICT')
    if p:
        return Path(p)
    import importlib.util
    spec = importlib.util.find_spec('cmudict')
    if spec and spec.origin:
        return Path(spec.origin).parent / 'data' / 'cmudict.dict'
    return None


def pronouncing():
    """word -> [ARPAbet phonemes] from cmudict.dict (first pronunciation), or {} when it is not here."""
    global _DICT
    if _DICT is None:
        _DICT = {}
        p = _dict_path()
        if p and p.is_file():
            for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
                parts = line.split('#')[0].split()
                if len(parts) > 1 and '(' not in parts[0]:
                    _DICT[parts[0]] = parts[1:]
    return _DICT


def phonemes(word):
    """The word's phonemes (with stress digits on vowels) and where they came from ('dict' or 'spelling')."""
    w = re.sub(r"[^a-z']", '', word.lower())
    if not w:
        return [], 'spelling'
    got = pronouncing().get(w) or pronouncing().get(w.replace("'", ''))
    if got:
        return list(got), 'dict'
    out, i, s = [], 0, w.replace("'", '')
    while i < len(s):
        for g, ph in LETTERS:
            if s.startswith(g, i):
                if s[i:] == 'e' and out:              # a silent final e
                    i += 1
                    break
                out += ph.split()
                i += len(g)
                break
        else:
            i += 1
    return [p for j, p in enumerate(out) if j == 0 or p != out[j - 1]], 'spelling'


def word_shapes(word, a, b):
    """[(t0, t1, shape, phoneme)] across the word's span [a, b]."""
    phs, _ = phonemes(word)
    if not phs or b <= a:
        return []
    span = b - a
    base = [re.sub(r'\d', '', p) for p in phs]
    vow = [i for i, p in enumerate(base) if p in VOWELS]
    cons = len(base) - len(vow)
    c_each = (min(cons * CONSONANT_S, span * .5) / cons if cons else 0) if vow else span / len(base)
    v_room = span - c_each * cons
    weights = {i: STRESS.get(phs[i][-1], 1.0) for i in vow}
    total = sum(weights.values()) or 1.0
    out, t = [], a
    for i, (p, raw) in enumerate(zip(base, phs)):
        d = v_room * weights[i] / total if i in weights else c_each
        shp = PHONE.get(p, 'B')
        if p == 'AH' and raw.endswith('0'):
            shp = 'C'                                 # an unstressed schwa opens less
        if len(shp) == 2:
            out += [(t, t + d * .6, shp[0], raw), (t + d * .6, t + d, shp[1], raw)]
        else:
            out.append((t, t + d, shp, raw))
        t += d
    return out


def _features(x, hop):
    """Per hop: loudness 0..1 against the recording's own range, and darkness 0..1 (low spectral centroid)."""
    n = max(1, len(x) // hop)
    x = np.pad(x, (0, n * hop + hop - len(x)))
    fr = np.lib.stride_tricks.sliding_window_view(x, 2 * hop)[::hop][:n] * np.hanning(2 * hop)
    db = 10 * np.log10(np.mean(fr ** 2, axis=1) + 1e-12)
    floor, top = np.percentile(db, 10), np.percentile(db, 98)
    loud = np.clip((db - floor - perform.ABOVE_FLOOR_DB) / max(top - floor - perform.ABOVE_FLOOR_DB, 6.0), 0, 1)
    mag = np.abs(np.fft.rfft(fr, axis=1))
    f = np.fft.rfftfreq(2 * hop, 1 / perform.SR)
    cen = (mag * f).sum(1) / (mag.sum(1) + 1e-9)
    dark = np.clip((1800 - cen) / 1200, 0, 1)
    return loud, dark


def _smooth(m, fps):
    out = np.zeros_like(m)
    up, down = 1 - np.exp(-1 / (fps * ATTACK_S)), 1 - np.exp(-1 / (fps * RELEASE_S))
    cur = np.zeros(m.shape[1])
    for i, row in enumerate(m):
        k = np.where(row > cur, up, down)
        cur = cur + (row - cur) * k
        out[i] = cur
    return out


def rhubarb_path():
    """The Rhubarb Lip Sync program when it is on this machine: $ISMAIL_RHUBARB (the program or its folder), else
    ~/.ismail/tools/rhubarb/ with the release unpacked in it at any depth; None when it is not here."""
    env = os.environ.get('ISMAIL_RHUBARB')
    root = Path(env) if env else Path.home() / '.ismail' / 'tools' / 'rhubarb'
    if root.is_file():
        return root
    if root.is_dir():
        for name in ('rhubarb.exe', 'rhubarb'):
            hit = sorted(q for q in root.rglob(name) if q.is_file())
            if hit:
                return hit[0]
    return None


def rhubarb_shapes(x, start, end, words=None, exe=None, english=False, timeout=600):
    """[[t, shape, '', word]] from Rhubarb Lip Sync for x (16 kHz mono) between start and end, t on x's clock; the
    shapes keep the word they fall in. Its phonetic recognizer by default: any language, singing too, about 4 times
    faster than real time. english=True uses its English speech recognizer, guided by the words as dialog text
    (about 2 times slower than real time, 2026-10-13 on this laptop)."""
    import soundfile as sf
    exe = exe or rhubarb_path()
    if not exe or not Path(exe).is_file():
        raise ValueError('Rhubarb Lip Sync is not on this machine (set ISMAIL_RHUBARB, or unpack it in '
                         '~/.ismail/tools/rhubarb/)')
    exe, sr = Path(exe), perform.SR
    ws = sorted((w for w in words or [] if float(w['end']) > start and float(w['start']) < end),
                key=lambda w: float(w['start']))
    with tempfile.TemporaryDirectory(prefix='ismail_rhubarb_') as td:
        wav, out, dialog = Path(td) / 'voice.wav', Path(td) / 'cues.json', Path(td) / 'dialog.txt'
        sf.write(wav, x[int(start * sr):int(end * sr)], sr, subtype='PCM_16')
        cmd = [sys.executable, str(exe)] if exe.suffix == '.py' else [str(exe)]   # a .py stands in for it in tests
        cmd += ['-f', 'json', '--extendedShapes', 'GHX', '-r', 'pocketSphinx' if english else 'phonetic', '-o', str(out)]
        if ws and english:
            dialog.write_text(' '.join(str(w['word']).strip() for w in ws), encoding='utf-8')
            cmd += ['-d', str(dialog)]
        r = subprocess.run(cmd + [str(wav)], capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not out.is_file():
            raise ValueError(f'Rhubarb Lip Sync failed ({r.returncode}): {(r.stderr or r.stdout).strip()[-300:]}')
        cues = json.loads(out.read_text(encoding='utf-8')).get('mouthCues') or []
    shapes = []
    for c in cues:
        t, shp = start + float(c['start']), str(c['value'])
        if shp not in SHAPES or (shapes and shapes[-1][1] == shp):
            continue
        word = next((str(w['word']).strip() for w in ws if float(w['start']) <= t < float(w['end'])), '')
        shapes.append([round(t, 3), shp, '', '' if shp == 'X' else word])
    return shapes


def _word_shapes_of(ws):
    """[[t, shape, phoneme, word]] for words [{word, start, end}], with rests in the pauses."""
    segs = []
    for w in sorted(ws, key=lambda w: float(w['start'])):
        for s in word_shapes(str(w['word']), float(w['start']), float(w['end'])):
            segs.append(s + (str(w['word']).strip(),))
    shapes, prev_end = [], None
    for (t0, t1, shp, ph, word) in segs:
        if prev_end is not None and t0 - prev_end > GAP_REST_S:
            shapes.append([round(prev_end, 3), 'X', '', ''])
        shapes.append([round(t0, 3), shp, ph, word])
        prev_end = t1
    if prev_end is not None:
        shapes.append([round(prev_end, 3), 'X', '', ''])
    return shapes


def track(audio, words=None, start=0.0, end=None, fps=30, lead=LEAD_S, x=None, engine='auto'):
    """The mouth track for audio (a path, or x: 16 kHz mono samples) between start and end (seconds on its clock),
    from words [{word, start, end}] on the same clock (snapped words from a take are best), or loudness alone.
    engine: 'auto' (Rhubarb when it is here, else the words), 'rhubarb' (an error when it is not here),
    'rhubarb_english' (its slower English recognizer, guided by the words) or 'words'."""
    if engine not in ENGINES:
        raise ValueError(f"engine: one of {', '.join(ENGINES)}, not {engine!r}")
    if x is None:
        x = perform.pcm(audio)
    sr = perform.SR
    end = len(x) / sr if end is None else min(end, len(x) / sr)
    if end <= start:
        raise ValueError(f'nothing between {start} and {end} s (the recording is {len(x) / sr:.2f} s)')
    hop = int(sr / fps)
    loud, dark = _features(x, hop)                    # against the whole recording's floor, not the stretch's
    n, i0 = int(round((end - start) * fps)), int(round(start * sr / hop))
    loud, dark = np.resize(loud[i0:i0 + n], n), np.resize(dark[i0:i0 + n], n)
    idx = {u: j for j, u in enumerate(UNITS)}
    m = np.zeros((n, len(UNITS)))
    ws = [w for w in (words or []) if float(w['end']) > start and float(w['start']) < end and str(w.get('word', '')).strip()]
    shapes, used, why = [], 'words' if ws else 'loudness', None
    if engine.startswith('rhubarb') or (engine == 'auto' and rhubarb_path()):
        try:
            shapes = rhubarb_shapes(x, start, end, ws, english=engine == 'rhubarb_english')
            if shapes:
                used = 'rhubarb_english' if engine == 'rhubarb_english' else 'rhubarb'
            else:
                why = 'Rhubarb heard no mouth shapes'     # the words (or loudness) make the track instead
        except (ValueError, OSError, subprocess.TimeoutExpired) as e:
            if engine != 'auto':
                raise ValueError(str(e)) from None
            why = str(e)                              # auto: say why, and fall back to the words
    if not used.startswith('rhubarb') and ws:
        shapes = _word_shapes_of(ws)
    if shapes:
        times = np.array([s[0] for s in shapes])
        for i in range(n):
            t = start + i / fps + lead
            k = np.searchsorted(times, t, side='right') - 1
            if k < 0:
                continue
            shp = shapes[k][1]
            for u, wgt in SHAPES[shp].items():
                if u in LOUD_UNITS and shp not in CLOSED:
                    wgt = min(1.0, wgt * (.6 + .7 * loud[i]))
                m[i, idx[u]] = wgt
    else:
        for i in range(n):
            o = loud[i] ** .8
            m[i, idx['jawOpen']] = .55 * o
            m[i, idx['mouthLowerDownLeft']] = m[i, idx['mouthLowerDownRight']] = .2 * o
            m[i, idx['mouthFunnel']] = .35 * o * dark[i]
            m[i, idx['mouthPucker']] = .25 * o * dark[i]
    m = np.clip(_smooth(m, fps), 0, 1)
    return {'fps': fps, 'start': round(start, 3), 'seconds': round(n / fps, 3), 'units': UNITS,
            'frames': np.round(m, 2).tolist(), 'shapes': [s for s in shapes if start <= s[0] < end],
            'source': {'audio': str(audio) if audio is not None else None, 'words': len(ws),
                       'phonemes': 'dict' if pronouncing() else 'spelling', 'lead_s': lead, 'engine': used,
                       **({'rhubarb_error': why} if why else {})}}


def write(tr, path):
    Path(path).write_text(json.dumps(tr, separators=(',', ':')), encoding='utf-8')
    return str(path)


def readout(tr, step=0.1):
    """The track as text: one column per step, a shape letter row and a jaw row (0-9), with a time ruler."""
    fps, units = tr['fps'], tr['units']
    j = units.index('jawOpen')
    n = int(tr['seconds'] / step)
    times = [s[0] for s in tr['shapes']]
    letters, jaw = [], []
    for c in range(n):
        t = tr['start'] + c * step
        k = int(np.searchsorted(times, t, side='right')) - 1
        letters.append(tr['shapes'][k][1] if k >= 0 else '.')
        f = tr['frames'][min(len(tr['frames']) - 1, int(c * step * fps))]
        jaw.append(str(min(9, int(f[j] * 10))))
    per = max(1, round(1 / step))
    ruler = ''.join('|' if c % per == 0 else ' ' for c in range(n))
    return '\n'.join([f'{"time":6}{ruler}  (| every second from {tr["start"]} s, one column {step} s)',
                      f'{"shape":6}{"".join(letters)}', f'{"jaw":6}{"".join(jaw)}'])
