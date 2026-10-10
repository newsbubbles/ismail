"""Auto-tune: move a voice's pitch onto notes and keep the voice.

WORLD (pyworld) splits the voice into its pitch curve (f0), its spectral envelope (the shape of the voice) and its
aperiodicity (breath). Only the pitch curve is moved; the envelope and the breath are put back as they were, so the
singer's character stays. This is the analysis the Voice agent's work settled on: a WORLD copy of a sung take passed
blind exams (vox r43), so the round trip is not what gives a tuned voice away.

What does give it away is the pitch itself. Rule-made pitch curves were heard as "autotune" (vox B14); the singer's
own micro-pitch (jitter, drift, overshoot, vibrato) was heard as real (vox r15). So the correction is smoothed by
`speed_ms`, the retune speed: the slow, wide moves of the pitch are corrected and its quick moves pass through. 0
snaps every frame onto the note (the hard, robotic sound); 20 to 60 ms is a firm modern correction; 100 ms and up
leaves vibrato and scoops alone and only pulls the note's centre in.
"""
import numpy as np

FRAME_MS = 5.0
NOTE_NAMES = {'C': 0, 'C#': 1, 'DB': 1, 'D': 2, 'D#': 3, 'EB': 3, 'E': 4, 'F': 5, 'F#': 6, 'GB': 6, 'G': 7,
              'G#': 8, 'AB': 8, 'A': 9, 'A#': 10, 'BB': 10, 'B': 11}
SCALES = {
    'chromatic': tuple(range(12)),
    'major': (0, 2, 4, 5, 7, 9, 11), 'minor': (0, 2, 3, 5, 7, 8, 10),
    'harmonic_minor': (0, 2, 3, 5, 7, 8, 11), 'dorian': (0, 2, 3, 5, 7, 9, 10), 'mixolydian': (0, 2, 4, 5, 7, 9, 10),
    'phrygian': (0, 1, 3, 5, 7, 8, 10), 'pentatonic': (0, 2, 4, 7, 9), 'minor_pentatonic': (0, 3, 5, 7, 10),
    'blues': (0, 3, 5, 6, 7, 10),
}
HYSTERESIS = 0.15      # semitones past the midpoint before the target changes note (no flapping between two notes)


class TuneError(ValueError):
    pass


def world():
    try:
        import pyworld
    except ImportError:
        raise TuneError("autotune needs WORLD: pip install pyworld (or pip install 'ismail[voice]')")
    return pyworld


def scale_classes(key, scale):
    """Pitch classes (0 = C) allowed by key + scale; scale may be a name in SCALES or a list of semitones from key."""
    k = NOTE_NAMES.get(str(key).strip().upper().replace('♯', '#').replace('♭', 'B'))
    if k is None:
        raise TuneError(f"autotune key {key!r}: use a note name such as C, F#, Bb")
    if isinstance(scale, (list, tuple)):
        steps = [int(s) % 12 for s in scale]
    elif scale in SCALES:
        steps = SCALES[scale]
    else:
        raise TuneError(f"autotune scale {scale!r}: use one of {', '.join(SCALES)}, or a list of semitones from the key")
    return sorted({(k + s) % 12 for s in steps})


def _nearest(m, classes):
    """The nearest MIDI note to m whose pitch class is allowed."""
    base = np.floor(m)
    cands = [base + d for d in range(-6, 8) if int(base + d) % 12 in classes]
    return min(cands, key=lambda c: abs(c - m))


def targets(midi, voiced, times, classes, notes=None):
    """Per frame, the note each voiced frame is pulled to (NaN when unvoiced).

    notes: [(start_s, pitch, dur_s, vel), ...] of a part: while one or more sound, the target is the sounding note
    nearest the sung pitch, moved by octaves into the singer's register; between them, the scale."""
    out = np.full(len(midi), np.nan)
    prev = None
    for i in np.nonzero(voiced)[0]:
        m = midi[i]
        cand = None
        if notes:
            live = [p for s, p, d, _ in notes if s <= times[i] < s + d]
            if live:
                cand = min((p + 12 * np.round((m - p) / 12) for p in live), key=lambda c: abs(c - m))
        if cand is None:
            cand = _nearest(m, classes)
            if prev is not None and cand != prev and abs(m - prev) < 0.5 + HYSTERESIS and \
                    int(round(prev)) % 12 in classes and (i == 0 or voiced[i - 1]):
                cand = prev
        out[i] = prev = cand
    return out


def _smooth(m, win=7):
    """Median over a few frames, so vibrato and jitter do not decide the note."""
    if len(m) < win:
        return m
    pad = np.pad(m, win // 2, mode='edge')
    return np.median(np.lib.stride_tricks.sliding_window_view(pad, win), axis=1)


def retune(mono, sr, key='C', scale='chromatic', notes=None, speed_ms=20.0, amount=1.0, formant=0.0,
           lo_hz=70.0, hi_hz=1000.0, t0=0.0):
    """mono float array -> (retuned mono array of the same length, readings dict). t0 = the time of sample 0 against
    the notes' clock."""
    pw = world()
    x = np.ascontiguousarray(mono, dtype=np.float64)
    f0, tt = pw.harvest(x, sr, f0_floor=float(lo_hz), f0_ceil=float(hi_hz), frame_period=FRAME_MS)
    f0 = pw.stonemask(x, f0, tt, sr)
    sp = pw.cheaptrick(x, f0, tt, sr)
    ap = pw.d4c(x, f0, tt, sr)
    voiced = f0 > 0
    midi = np.zeros_like(f0)
    midi[voiced] = 69 + 12 * np.log2(f0[voiced] / 440.0)
    sm = midi.copy()
    sm[voiced] = _smooth(midi[voiced])
    tgt = targets(sm, voiced, tt + t0, scale_classes(key, scale), notes)
    corr = np.where(voiced, tgt - midi, 0.0)
    # the retune speed: a one-pole smoothing of the correction, restarted at each voiced stretch so a note's start
    # lands on pitch
    a = 1.0 if speed_ms <= 0 else 1.0 - np.exp(-FRAME_MS / float(speed_ms))
    y = np.zeros_like(corr)
    state = None
    for i in range(len(corr)):
        if not voiced[i]:
            state = None
            continue
        state = corr[i] if state is None else state + a * (corr[i] - state)
        y[i] = state
    new = np.where(voiced, midi + float(amount) * y, 0.0)
    f0n = np.where(voiced, 440.0 * 2 ** ((new - 69) / 12), 0.0)
    if formant:
        # the voice's shape moved up (formant > 0) or down, without moving its pitch
        k = np.arange(sp.shape[1])
        fac = 2 ** (float(formant) / 12)
        sp = np.stack([np.interp(k / fac, k, row, right=row[-1]) for row in sp])
    out = pw.synthesize(f0n, sp, ap, sr, FRAME_MS)
    out = out[:len(x)] if len(out) >= len(x) else np.pad(out, (0, len(x) - len(out)))
    moved = np.abs(new - midi)[voiced]
    readings = {'voiced_s': float(voiced.sum() * FRAME_MS / 1000),
                'median_cents_moved': float(np.median(moved) * 100) if moved.size else 0.0,
                'max_cents_moved': float(moved.max() * 100) if moved.size else 0.0}
    return out, readings
