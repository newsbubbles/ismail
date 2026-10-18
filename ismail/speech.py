"""Speech on a rhythm: the speech engine's own word and phone timings, syllables cut where the next one starts, each
set on a written 16th. Built in a song and proven there: the timings come from Windows SAPI's word and phone events
(a speech server's word starts ran early and cut the syllable before), and each syllable is cut where the next one
starts, so a stressed word can sit on a named beat. Pure functions; sound_speak(rhythm=) uses them."""
import re

import numpy as np

# SAPI's US English phone ids (sapi.h): the vowels, and silence
VOWELS = set(range(10, 17)) | {21, 22, 23, 27, 28, 35, 36, 43, 44}
SIL = 7


class RhythmError(ValueError):
    pass


def _word(w):
    return re.sub(r"[^\w']", '', w.lower())


def parse_sapi_log(lines):
    """[(word, [(start s, phone id) ...])], end s from the event log the SAPI speaker writes: 'W<tab>ms<tab>word'
    (the next phone starts it) and 'P<tab>ms<tab>duration ms<tab>ids joined by +'. Times come from the phone
    durations, which add up to the file's length (the event positions run late at 22.05 kHz)."""
    words, t = [], 0.0
    for line in lines:
        f = line.rstrip('\r\n').split('\t')
        if f[0] == 'W' and len(f) > 2:
            words.append((_word(f[2]), []))
        elif f[0] == 'P' and len(f) > 3:
            ids = [int(c) for c in f[3].split('+') if c] or [SIL]
            if words and ids[0] != SIL:
                words[-1][1].append((t, ids[0]))
            t += float(f[2]) / 1000
    return [(w, ph) for w, ph in words if w and ph], t


def syllable_starts(phones, n):
    """n start times: the word's start, then each later syllable from the last consonant before its vowel. n == 1
    keeps the word whole."""
    starts = [phones[0][0]]
    if n == 1:
        return starts
    vi = [k for k, (_, p) in enumerate(phones) if p in VOWELS]
    for v0, v1 in zip(vi[:-1], vi[1:]):
        starts.append(phones[v1 - 1][0] if v1 - 1 > v0 else phones[v1][0])
    if len(starts) != n:
        raise RhythmError(f"{len(starts)} syllables found, {n} written")
    return starts


def parse_rhythm(spec):
    """'night 0; shift 2; engineer 12 14 16; connections 2 4 -' -> [(word, (pos, ...))]: each word then one 16th
    position per syllable (negative = before the line's beat 1; '-' skips that syllable). A word given one
    position stays whole."""
    out = []
    for item in [s for s in spec.split(';') if s.strip()]:
        parts = item.split()
        if len(parts) < 2:
            raise RhythmError(f"{item.strip()!r}: give a word then one 16th position per syllable, e.g. 'engineer 12 14 16'")
        try:
            pos = tuple(None if p == '-' else float(p) for p in parts[1:])
        except ValueError:
            raise RhythmError(f"{item.strip()!r}: positions are 16ths (numbers, '-' skips a syllable)")
        out.append((_word(parts[0]), pos))
    if not out or all(p is None for _, ps in out for p in ps):
        raise RhythmError("the rhythm places no syllable")
    return out


def place(words, y, sr, end, spec, bpm):
    """(audio, first 16th): the line with each written syllable on its 16th at bpm, the audio starting on the first
    syllable's slot. A spec word is the next one in the line with that name (from the start again when none is
    left), so a word can be named twice. A piece is cut where the next syllable starts, and chopped (faded over
    30 ms) past the next piece's slot. y is mono; words and end come from parse_sapi_log."""
    s16 = 60.0 / bpm / 4
    units, cur = [], 0                        # (source start, source end, 16th position)
    for name, pos in spec:
        hits = ([k for k in range(cur, len(words)) if words[k][0] == name] or
                [k for k in range(len(words)) if words[k][0] == name])
        if not hits:
            raise RhythmError(f"{name!r} is not in the spoken words {[w for w, _ in words]}")
        k = hits[0]
        cur = k + 1
        starts = syllable_starts(words[k][1], len(pos))
        w_end = words[k + 1][1][0][0] if k + 1 < len(words) else end
        for j, p in enumerate(pos):
            if p is not None:
                units.append((starts[j], starts[j + 1] if j + 1 < len(starts) else w_end, p))
    first = min(p for _, _, p in units)
    last_p = max(p for _, _, p in units)
    out = np.zeros(int(((last_p - first) * s16 + 1.2) * sr))
    fade = int(0.008 * sr)
    nps = sorted(set(p for _, _, p in units))
    for t, t_end, p in units:
        seg = y[int(max(0, t - 0.008) * sr):int(t_end * sr)].copy()
        later = [q for q in nps if q > p]
        if later:                             # chop at the next piece's slot, fading over 30 ms past it
            seg = seg[:int(((later[0] - p) * s16 + 0.03) * sr)]
        if not len(seg):
            continue
        f = min(fade, len(seg) // 2)
        if f:
            seg[:f] *= np.linspace(0, 1, f)
        fo = min(int(0.03 * sr), len(seg) // 2) if later else f
        if fo:
            seg[-fo:] *= np.linspace(1, 0, fo)
        a = int((p - first) * s16 * sr)
        out[a:a + len(seg)] += seg[:len(out) - a]
    return out, first
