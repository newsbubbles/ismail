"""melody_consensus: the lead line of a phrase that repeats, read where the stem also carries a backing part
(ledger:M314, celestial_resort_1024 HANDOFF 4).

On a lead stem that also held a sustained backing, analyze_melody warned of a second part within 6 dB on half the
notes and notes_from_audio_loop doubled the lead at the octave. What worked (the song's work/lead_consensus.py):
a harmonic-sum salience over a constant-Q spectrum (each candidate pitch scores its first five harmonics, weighted
1, 0.6, 0.45, 0.3, 0.2, so a note an octave below the lead scores less than the lead), averaged over every
repetition of the phrase (what changes between repetitions averages down), then one pitch per step, a new note
where the pitch changes or where the start of a step is louder than the step before (a re-attack).
"""
import numpy as np

from .api import OpError, _load, op

HOP = 64                  # CQT hop at 22.05 kHz: 2.9 ms. Seven octaves need a hop divisible by 2**6
BASE = 36                 # lowest CQT bin, C2
BPO = 36                  # three bins per semitone
SEMIS = 84                # C2 up to C9 (the top harmonics of a high note fall off the end and are skipped)
HARM = ((1, 1.0), (2, 0.6), (3, 0.45), (4, 0.3), (5, 0.2))
EDGE = 0.008              # seconds trimmed off each end of a step before averaging
EARLY = 0.35              # the share of a step the onset test reads
# a note whose octave above or below scores within this is listed as a close call. With HARM a clean note beats its
# own octave below by 2.2 dB (a sine) to 2.6 dB (a saw) by construction, so a call is close under that
OCTAVE_CLOSE_DB = 1.5
SHOW_CLOSE = 8


def _harm_index(lo, hi):
    """(candidates, harmonics) bin indices into the CQT and a mask of the ones inside it."""
    m = np.arange(lo, hi + 1)[:, None]
    k = np.rint((m + 12 * np.log2([h for h, _ in HARM])[None, :] - BASE) * (BPO // 12)).astype(int)
    ok = k + 1 < BPO // 12 * SEMIS
    return np.where(ok, k, 0), ok


def _salience(seg, K, ok, w):
    """Harmonic-sum salience of every candidate for one averaged power spectrum (the loudest bin within a third of
    a semitone of each harmonic)."""
    m3 = seg.copy()
    m3[1:] = np.maximum(m3[1:], seg[:-1])
    m3[:-1] = np.maximum(m3[:-1], seg[1:])
    return (m3[K] * ok * w).sum(1)


def _db(x):
    return 10 * np.log10(np.asarray(x) + 1e-12)


@op(mutates=True)
def melody_consensus(project: str, source: str, reps: list, phrase_bars: int, steps_per_beat: int = 4,
                     low: str = 'C3', high: str = 'C7', track: str = None, write_at: list = None,
                     mode: str = 'replace', held_pct: float = 10.0, rest_db: float = -20.0, onset_db: float = 2.5,
                     short_db: float = -15.0, legato_steps: int = 1) -> str:
    """The lead line of a phrase that repeats, when its stem also carries a backing part (a pad, a chord, a second
    voice): analyze_melody warns of a second part within 6 dB, notes_from_audio_loop doubles the lead at the octave.
    reps = the bar where each repetition of the phrase starts ([17, 25, 49, 57]); phrase_bars = its length. Every
    candidate pitch in [low, high] scores its first five harmonics on a constant-Q spectrum (an octave below the lead
    scores less than the lead), the scores are averaged over the repetitions, and each step takes the best pitch.
    Before scoring, what sounds through nearly all of the phrase comes off: each bin loses its held_pct percentile
    over the phrase's steps, so a held pad or chord stops winning (a lead holding one pitch through more than
    100 - held_pct percent of the phrase would go with it: pass held_pct=None). A new note starts where the pitch changes or where the start of a step is onset_db louder than the step before.
    Steps rest_db or more under the loudest are rests; one-step notes quieter than short_db drop (None keeps them);
    gaps of up to legato_steps close up (0 keeps them). Returns notes_write rows with beats from the phrase's first
    bar, then a summary: notes per bar, confidence (how many repetitions picked the same pitch on their own, and
    the margin over the runner-up) and the notes where the octave choice was close. track= writes the phrase into
    that track at every bar of write_at (default: reps) with notes_write(mode=mode). Repetitions that differ
    (variants, fills) pull the average apart: pass only the bars where the phrase really repeats."""
    import librosa
    from . import analysis as A, api
    from .notation import pitch_to_midi, midi_to_name, fmt_num, NotationError
    P = _load(project)
    if track:
        P.track(track)
    reps = [int(r) for r in (reps or [])]
    if not reps:
        raise OpError("reps: the bars where the phrase starts, e.g. reps=[17, 25, 49, 57]")
    phrase_bars, spb = int(phrase_bars), int(steps_per_beat)
    if phrase_bars < 1 or spb < 1:
        raise OpError("phrase_bars and steps_per_beat must be 1 or more")
    try:
        lo, hi = pitch_to_midi(low), pitch_to_midi(high)
    except NotationError as e:
        raise OpError(str(e))
    if lo < BASE or hi <= lo:
        raise OpError(f"low {low!r} to high {high!r}: give a range from C2 up, with high above low")
    path, g = P.source(source, [min(reps), max(reps) + phrase_bars - 1])
    y = A.load(path)
    sr = A.ASR
    nsteps = phrase_bars * P.bpb * spb
    step = g.spb / spb
    K, ok = _harm_index(lo, hi)
    w = np.array([wt for _, wt in HARM])[None, :]
    nb = BPO // 12 * SEMIS
    segF, segE = np.zeros((len(reps), nsteps, nb)), np.zeros((len(reps), nsteps, nb))  # power per rep, step, bin
    pad = 1.0                 # seconds of context each side: the low CQT filters are long (C2 at 36 bins per octave: 0.8 s)
    for i, r in enumerate(reps):
        t0, t1 = g.bar_time(r), g.bar_time(r + phrase_bars)
        if t0 < 0 or t1 > len(y) / sr + 1e-3:
            raise OpError(f"repetition at bar {r} ({t0:.2f}-{t1:.2f} s) runs outside {source!r} "
                          f"({len(y) / sr:.2f} s); check reps, phrase_bars and the project's bpm and offset_sec")
        a = max(0, int((t0 - pad) * sr))
        C = np.abs(librosa.cqt(y[a:int((t1 + pad) * sr)], sr=sr, hop_length=HOP, fmin=librosa.midi_to_hz(BASE),
                               n_bins=nb, bins_per_octave=BPO)) ** 2
        for s in range(nsteps):
            t = t0 + s * step - a / sr
            f0 = int((t + EDGE) * sr / HOP)
            f1 = max(f0 + 1, int((t + step - EDGE) * sr / HOP))
            fm = max(f0 + 1, int((t + EARLY * step) * sr / HOP))
            segF[i, s], segE[i, s] = C[:, f0:f1].mean(1), C[:, f0:fm].mean(1)
        if held_pct is not None:  # what sounds through nearly all of the phrase (a pad, a drone, a chord) comes off
            floor = np.percentile(segF[i], held_pct, axis=0)
            segF[i] = np.maximum(segF[i] - floor, 0)
            segE[i] = np.maximum(segE[i] - floor, 0)
    sal_rep = np.stack([[_salience(segF[i, s], K, ok, w) for s in range(nsteps)] for i in range(len(reps))])
    mF, mE = segF.mean(0), segE.mean(0)
    full = np.stack([_salience(mF[s], K, ok, w) for s in range(nsteps)])
    early = np.stack([_salience(mE[s], K, ok, w) for s in range(nsteps)])
    best = full.argmax(1)
    pitch = lo + best
    lev = _db(full[np.arange(nsteps), best])
    early_lev = _db(early[np.arange(nsteps), best])
    ref = np.percentile(lev, 98)
    agree = (sal_rep.argmax(2) == best[None, :]).mean(0)            # share of repetitions that pick it alone

    notes = []  # [start step, midi, steps, level dB under the loudest]
    cur = None
    for s in range(nsteps):
        if lev[s] - ref <= rest_db:
            cur = None
            continue
        if cur is None or pitch[s] != notes[cur][1] or early_lev[s] - lev[s - 1] > onset_db:
            notes.append([s, int(pitch[s]), 1, float(lev[s] - ref)])
            cur = len(notes) - 1
        else:
            notes[cur][2] += 1
    if short_db is not None:
        notes = [n for n in notes if n[3] >= short_db or n[2] >= 2]
    for j, n in enumerate(notes):
        nxt = notes[j + 1][0] if j + 1 < len(notes) else nsteps
        if 0 < nxt - (n[0] + n[2]) <= legato_steps:
            n[2] = nxt - n[0]
    if not notes:
        return (f"no notes: every step is {-rest_db:g} dB or more under the loudest, or none falls in {low}-{high}; "
                f"check reps against the song's form and the range")

    def vel(lv):
        return int(np.clip(112 + 2 * lv, 50, 127))

    text = '\n'.join(f"{fmt_num(s / spb)} {midi_to_name(m)} {fmt_num(d / spb)} {vel(lv)}" for s, m, d, lv in notes)

    # confidence and the close octave calls, per note over its steps
    conf, close = [], []
    for s, m, d, lv in notes:
        sl = full[s:s + d].mean(0)
        j = m - lo
        rest = np.delete(sl, j)
        margin = float(_db(sl[j]) - _db(rest.max())) if rest.size else 99.0
        ag = float(agree[s:s + d].mean())
        conf.append((ag, margin))
        octs = [(o, sl[j + o]) for o in (-12, 12) if 0 <= j + o < len(sl)]
        if octs:
            o, v = max(octs, key=lambda x: x[1])
            od = float(_db(sl[j]) - _db(v))
            if od < OCTAVE_CLOSE_DB:
                close.append((s, m, o, od))
    steps_bar = P.bpb * spb
    per_bar = [sum(1 for n in notes if b * steps_bar <= n[0] < (b + 1) * steps_bar) for b in range(phrase_bars)]
    ag_all = float(np.mean([c[0] for c in conf]))
    weak = sorted(range(len(notes)), key=lambda i: conf[i][0])[:3]
    lines = [text, '#',
             f"# {len(notes)} notes over {phrase_bars} bar(s), averaged over {len(reps)} repetitions at bars {reps} "
             f"of {source} ({low}-{high}, {spb} steps per beat)",
             f"# notes per bar: {' '.join(str(c) for c in per_bar)}",
             f"# confidence: {ag_all:.0%} of the repetitions pick each note's pitch on their own (mean); margin over "
             f"the runner-up pitch: median {np.median([c[1] for c in conf]):.1f} dB"]
    if len(reps) > 1 and conf[weak[0]][0] < 1:
        lines.append('# least sure: ' + ', '.join(
            f"beat {fmt_num(notes[i][0] / spb)} {midi_to_name(notes[i][1])} ({conf[i][0]:.0%}, {conf[i][1]:.1f} dB)"
            for i in weak if conf[i][0] < 1))
    if close:
        shown = sorted(close, key=lambda c: c[3])[:SHOW_CLOSE]
        lines.append(f"# octave close (its octave above or below within {OCTAVE_CLOSE_DB:g} dB) on {len(close)} "
                     f"note(s): " + ', '.join(
                         f"beat {fmt_num(s / spb)} {midi_to_name(m)} vs {midi_to_name(m + o)} {od:+.1f} dB"
                         for s, m, o, od in sorted(shown)) + "; check these by ear or narrow low/high")
    else:
        lines.append(f"# octave: every note beats its octave above and below by {OCTAVE_CLOSE_DB:g} dB or more")
    if track:
        at = [int(b) for b in (write_at or reps)]
        for b in at:
            api.notes_write(project, track, b, text, mode=mode, bars=phrase_bars)
        lines.append(f"# written to {track} at bars {at} (mode={mode})")
    return '\n'.join(lines)
