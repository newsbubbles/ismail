"""Audio -> text. Every function returns (data, text). Text is bounded; ranges are in 1-indexed bars on a grid
(bpm, offset_sec = time of bar 1 beat 1)."""
import os
import warnings

import numpy as np
import soundfile as sf

warnings.filterwarnings('ignore')
import librosa  # noqa: E402

from .notation import NAMES, midi_to_name, fmt_num  # noqa: E402

ASR = 22050
HOP = 256
BANDS = [('sub', 20, 60), ('bass', 60, 250), ('lowmid', 250, 500), ('mid', 500, 2000), ('himid', 2000, 6000),
         ('air', 6000, 16000)]
_CACHE = {}


class AnalysisError(ValueError):
    pass


# ------------------------------------------------------------------ loading

def load(path, sr=ASR, stereo=False):
    if not os.path.exists(path):
        raise AnalysisError(f"no such audio file {path!r}")
    key = (os.path.abspath(path), os.path.getmtime(path), sr, stereo)
    if key in _CACHE:
        return _CACHE[key]
    y, fsr = sf.read(path, dtype='float32', always_2d=True)
    y = y.T
    if not stereo:
        y = y.mean(0)
    elif y.shape[0] == 1:                # a mono file read as stereo: both channels the same (callers index [1])
        y = np.vstack([y, y])
    if fsr != sr:
        y = librosa.resample(y, orig_sr=fsr, target_sr=sr, res_type='soxr_hq')
    _CACHE.clear() if len(_CACHE) > 12 else None
    _CACHE[key] = y
    return y


class Grid:
    def __init__(self, bpm, offset_sec=0.0, beats_per_bar=4):
        self.bpm = float(bpm)
        self.offset = float(offset_sec)
        self.bpb = beats_per_bar
        self.spb = 60.0 / self.bpm

    def bar_time(self, bar):  # bar is 1-indexed, may be fractional
        return self.offset + (bar - 1) * self.bpb * self.spb

    def time_to_bar(self, t):
        return (t - self.offset) / (self.bpb * self.spb) + 1

    def time_to_beat(self, t):
        return (t - self.offset) / self.spb

    def n_bars(self, dur):
        return int(np.floor(self.time_to_bar(dur))) - 1 + 1

    def first_bar(self):  # the bar sounding at t=0 (a windowed render's grid puts bar 1 before the file)
        return max(1, int(np.floor(self.time_to_bar(0.0) + 1e-6)))


def _span(y, sr, grid, bar0, bar1):
    t0 = max(0.0, grid.bar_time(bar0))
    t1 = min(len(y) / sr, grid.bar_time(bar1))
    return y[int(t0 * sr):int(t1 * sr)], t0, t1


def _range(y, sr, grid, bars, max_bars):
    total = int(np.floor(grid.time_to_bar(len(y) / sr)))
    if bars is None:
        b0 = grid.first_bar()
        b1 = min(total, b0 + max_bars - 1) + 1
    else:
        b0, b1 = bars[0], bars[1] + 1
    if b1 - b0 > max_bars:
        raise AnalysisError(f"range too long ({b1 - b0} bars); max {max_bars} per call. Page through it.")
    return b0, b1, total


# ------------------------------------------------------------------ grid

def beat_grid(path, bpm_hint=None, beats_per_bar=4):
    """Estimate a constant tempo + the time of the first downbeat. Reports tempo stability too."""
    y = load(path)
    oenv = librosa.onset.onset_strength(y=y, sr=ASR, hop_length=HOP, aggregate=np.median)
    fps = ASR / HOP
    if bpm_hint:
        cands = [bpm_hint]
    else:
        tg = librosa.feature.tempo(onset_envelope=oenv, sr=ASR, hop_length=HOP, aggregate=None)
        cands = [float(np.median(tg))]
    best = None
    x = oenv - oenv.mean()
    def comb(bpms):
        out = None
        for bpm in bpms:
            period = fps * 60 / bpm
            k = np.arange(0, len(x) - period - 1, period)
            phs = np.linspace(0, period, 32, endpoint=False)
            sc = x[(k[:, None] + phs[None, :]).astype(int)].mean(0)
            j = int(np.argmax(sc))
            if out is None or sc[j] > out[0]:
                out = (sc[j], bpm, phs[j])
        return out
    for c in cands:
        coarse = comb(np.arange(c - 3, c + 3, 0.05))
        fine = comb(np.arange(coarse[1] - 0.06, coarse[1] + 0.06, 0.002))
        if best is None or fine[0] > best[0]:
            best = fine
    best_score, bpm, ph = best

    def alternatives(bpm, score):
        out = []
        for r, label in ((2.0, 'x2'), (1.5, 'x1.5'), (2 / 3, 'x2/3'), (0.5, '/2')):
            a = bpm * r
            if 55 <= a <= 200:
                sc_a = comb(np.arange(a - 0.3, a + 0.3, 0.01))
                out.append((label, sc_a[1], sc_a[0] / (score + 1e-9), sc_a))
        return out
    # tempo-octave alternatives: half-time genres (dubstep, DnB, trap) often read at /2 or x2/3 of the played tempo.
    # Octave doubts (x2, /2) stay a report; a x1.5 or x2/3 reading that combs clearly stronger replaces the estimate
    # (tested on 8 songs of known tempo: fixed a 150 BPM track read as 100, changed none of the others)
    alts = alternatives(bpm, best_score)
    strong = [x for x in alts if x[0] in ('x1.5', 'x2/3') and x[2] > 1.2]
    if strong:
        s0 = max(strong, key=lambda x: x[2])[3]
        best_score, bpm, ph = comb(np.arange(s0[1] - 0.06, s0[1] + 0.06, 0.002))
        alts = alternatives(bpm, best_score)
    alts = [(lab, b, r) for lab, b, r, _ in alts]
    # refine phase finely
    period = fps * 60 / bpm
    phs = np.linspace(ph - 2, ph + 2, 41)
    k = np.arange(0, len(x) - period - 3, period)
    sc = [x[np.clip((k + p).astype(int), 0, len(x) - 1)].mean() for p in phs]
    ph = phs[int(np.argmax(sc))]
    # beat vs off-beat: the full-band comb often locks onto off-beat hats; kicks sit on the beat
    S = np.abs(librosa.stft(y, n_fft=2048, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=ASR, n_fft=2048)
    low = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[freqs < 150]), sr=ASR, hop_length=HOP)
    lz = low - low.mean()

    def at(sig, p):
        return sig[np.clip((k + p).astype(int), 0, len(sig) - 1)].mean()

    def vote(a, b):
        return (a - b) / (abs(a) + abs(b) + 1e-9)
    # three votes for "ph is the beat, not the off-beat": kick-band onsets, chord changes (harmony moves on the
    # beat; a ducked bass swells on the off-beat and fools the kick band alone), and the first sound of the track
    v_low = vote(at(lz, ph), at(lz, ph + period / 2))
    chroma_f = librosa.feature.chroma_stft(S=S ** 2, sr=ASR, hop_length=HOP)
    dl = max(int(0.08 * fps), 1)
    cc = np.r_[np.zeros(dl), np.linalg.norm(chroma_f[:, 2 * dl:] - chroma_f[:, :-2 * dl], axis=0), np.zeros(dl)]
    cc = cc - cc.mean()
    v_chroma = vote(at(cc, ph), at(cc, ph + period / 2))
    first = float(np.argmax(oenv > 0.2 * oenv.max()))
    dist = ((first - ph) / period) % 1.0
    v_start = 1.0 if min(dist, 1 - dist) < 0.15 else (-1.0 if abs(dist - 0.5) < 0.15 else 0.0)
    if v_low + v_chroma + 0.75 * v_start < 0:
        ph = (ph + period / 2) % period
    beat0 = ph / fps
    # fine phase on a sharp envelope: 2048-sample frames put flux peaks ~30 ms late
    hop2 = 64
    sharp = librosa.onset.onset_strength(y=y, sr=ASR, hop_length=hop2, n_fft=512)
    sharp = sharp - sharp.mean()
    fps2 = ASR / hop2
    per2 = fps2 * 60 / bpm
    kk = np.arange(0, len(sharp) - per2 - 40, per2)
    cands = np.arange(beat0 - 0.08, beat0 + 0.02, 0.001)
    sc = [sharp[np.clip((kk + c * fps2).astype(int), 0, len(sharp) - 1)].mean() for c in cands]
    beat0 = float(cands[int(np.argmax(sc))])
    ph = beat0 * fps
    # downbeat: phase with strongest low-band onsets (kick on 1) & chroma change
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=ASR, hop_length=HOP)
    nbeats = int((len(y) / ASR - beat0) / (60 / bpm))
    bt = beat0 + np.arange(nbeats) * 60 / bpm
    bf = np.clip((bt * fps).astype(int), 0, len(low) - 1)
    cchg = np.r_[0, np.linalg.norm(np.diff(chroma[:, bf], axis=1), axis=0)]
    scores = []
    for p in range(beats_per_bar):
        scores.append(low[bf[p::beats_per_bar]].mean() / (low[bf].mean() + 1e-9)
                      + cchg[p::beats_per_bar].mean() / (cchg.mean() + 1e-9))
    bar_len = beats_per_bar * 60 / bpm
    # backbeat: in 4/4 with a drum kit the snare (1.5-5 kHz onsets, less the kick band) lands on beats 2 and 4. A
    # clear contrast (|b| >= 0.15; backbeat songs read 0.15-1.1, four-on-the-floor and drumless under 0.05) votes and
    # moves section jumps off snare beats; together they fixed a G-funk bar 1 read one beat late (snare on 1 and 3)
    # and changed none of 5 other songs with known bar 1
    backbeat = [0.0] * beats_per_bar
    if beats_per_bar == 4:
        w = max(int(0.04 * fps), 1)

        def per_beat(lo, hi):
            # linear magnitude: in dB a quiet hat rising out of silence jumps as far as the snare
            on = librosa.onset.onset_strength(S=S[(freqs >= lo) & (freqs < hi)], sr=ASR, hop_length=HOP)
            return np.array([on[max(f - w, 0):f + w + 1].max() for f in bf])
        sn, kk_ = per_beat(1500, 5000), per_beat(0, 150)
        for p in range(4):
            def contrast(v):
                on_, off_ = v[(p + 1) % 4::4][:len(v) // 4].mean() + v[(p + 3) % 4::4][:len(v) // 4].mean(), \
                    v[p::4][:len(v) // 4].mean() + v[(p + 2) % 4::4][:len(v) // 4].mean()
                return (on_ - off_) / (on_ + off_ + 1e-9)
            backbeat[p] = round(float(contrast(sn) - contrast(kk_)), 2) if len(bf) >= 16 else 0.0
    bbv = [2.0 * b if abs(b) >= 0.15 else 0.0 for b in backbeat]
    # section evidence: the biggest level jumps between beats (parts entering, drops) land on bar starts, and a
    # track usually begins on a downbeat. Each is scored per phase and added to the kick/chroma score.
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=HOP)[0]
    bl = np.array([20 * np.log10(rms[max(f - int(0.05 * fps), 0):f + int(0.25 * fps) + 1].mean() + 1e-9) for f in bf])
    jump = np.r_[0, np.diff(bl)]
    top = [int(i) for i in np.argsort(jump)[::-1][:12] if jump[i] > 3.0]
    # with a clear backbeat, a jump on a snare beat belongs to the kick beat before it: the loud snare right after a
    # section's first beat is where the beat-to-beat jump shows (G-funk: every jump read on beat 2)
    snare_ph = {q for q in range(beats_per_bar) if backbeat[q] <= -0.15}
    top = [b - 1 if b % beats_per_bar in snare_ph else b for b in top]
    sect = []
    for p in range(beats_per_bar):
        sect.append(float(np.mean([(b - p) % beats_per_bar == 0 for b in top])) if top else 0.0)
    first_onset = float(np.argmax(oenv > 0.2 * oenv.max()) / fps)
    start = []
    for p in range(beats_per_bar):
        fd = beat0 + p * 60 / bpm
        off_p = fd - np.floor(fd / bar_len) * bar_len
        # distance (in beats) from the first sound to the nearest bar start of this phase
        d = ((first_onset - off_p) / (60 / bpm)) % beats_per_bar
        start.append(1.0 if min(d, beats_per_bar - d) < 0.25 else 0.0)
    total = [scores[p] + 1.5 * sect[p] + 0.5 * start[p] + bbv[p] for p in range(beats_per_bar)]
    p = int(np.argmax(total))
    first_down = beat0 + p * 60 / bpm
    # pull offset back to the earliest bar start >= 0
    offset = first_down - np.floor(first_down / bar_len) * bar_len
    cand_offsets = []
    for q in range(beats_per_bar):
        fd = beat0 + q * 60 / bpm
        cand_offsets.append(round(float(fd - np.floor(fd / bar_len) * bar_len), 4))
    # stability: local tempo in 30 s windows
    local = []
    for w0 in range(0, int(len(y) / ASR) - 30, 30):
        seg = oenv[int(w0 * fps):int((w0 + 30) * fps)]
        tl = librosa.feature.tempo(onset_envelope=seg, sr=ASR, hop_length=HOP, start_bpm=bpm)[0]
        local.append((w0, float(tl)))
    data = {'bpm': round(float(bpm), 3), 'offset_sec': round(float(offset), 4), 'beats_per_bar': beats_per_bar,
            'downbeat_scores': [round(float(s), 2) for s in scores], 'local_tempo': local,
            'bars': int((len(y) / ASR - offset) / bar_len)}
    cents, conc = tuning(y)
    data.update({'section_scores': [round(v, 2) for v in sect], 'start_scores': start,
                 'backbeat_scores': backbeat, 'candidate_offsets': cand_offsets,
                 'tuning_cents': round(cents, 1), 'tuning_r': round(conc, 2)})
    ranked = sorted(range(beats_per_bar), key=lambda q: -total[q])
    txt = (f"tempo {bpm:.2f} BPM (constant-tempo fit); bar 1 starts at {offset:.3f}s; ~{data['bars']} bars of "
           f"{beats_per_bar}/4.\ndownbeat candidates (bar 1 at ... s: kick/chord score + section-change score + "
           f"starts-on-it + snare-on-2-and-4):\n" +
           '\n'.join(f"  {cand_offsets[q]:.4f}s  {scores[q]:.2f} + {1.5 * sect[q]:.2f} + {0.5 * start[q]:.1f} + "
                     f"{bbv[q]:.2f} = {total[q]:.2f}{'  <- chosen' if q == p else ''}" for q in ranked) +
           (f"\nbackbeat {backbeat[p]:+.2f} at the chosen bar 1 (snare on 2 and 4 reads +0.15 or more; under 0.15 "
            f"there is no kit backbeat and it did not vote)" if beats_per_bar == 4 else '') +
           f"\ntuning {cents:+.0f} cents from A440 (r {conc:.2f}" +
           (", little pitch content: unreliable)" if conc < 0.15 else ")") +
           (f": notes will read between semitones. Run ref_retune() before any transcription, key or chord reading"
            if abs(cents) >= 15 and conc >= 0.15 else '') +
           ("\n  close call: check with analyze_structure(source=...) after project_set(offset_sec=...): sections "
            "should start on bars 1, 5, 9, 17 ..., not mid-phrase" if total[ranked[0]] - total[ranked[1]] < 0.5 else '') +
           "\nlocal tempo per 30s: " +
           ', '.join(f"{w}s:{t:.1f}" for w, t in local) +
           "\nIf the local tempos disagree with the fit by >1 BPM (other than x2 or /2) the track has tempo changes." +
           ("\nother tempo readings (comb strength vs the chosen one): " +
            ', '.join(f"{lab} = {b:.2f} BPM ({r:.2f})" for lab, b, r in alts) +
            ". Half-time genres read low: if the genre usually sits at 140-175 BPM, try the x2 or x1.5 reading"
            " in project_new and check analyze_drums (kick and snare should land on the expected steps)." if alts else ''))
    return data, txt


# ------------------------------------------------------------------ per-bar features

def _band_db(S, freqs):
    out = []
    for _, lo, hi in BANDS:
        m = (freqs >= lo) & (freqs < hi)
        out.append(10 * np.log10(np.mean(S[m] ** 2, axis=0) + 1e-12))
    return np.array(out)


CHORD_TEMPLATES = {}
for _r in range(12):
    for _q, _iv in (('', (0, 4, 7)), ('m', (0, 3, 7)), ('5', (0, 7)), ('7', (0, 4, 7, 10)), ('m7', (0, 3, 7, 10)),
                    ('sus4', (0, 5, 7)), ('dim', (0, 3, 6))):
        v = np.zeros(12)
        v[[(_r + i) % 12 for i in _iv]] = 1
        CHORD_TEMPLATES[NAMES[_r] + _q] = v / np.linalg.norm(v)


def chord_label(chroma_vec):
    c = chroma_vec / (np.linalg.norm(chroma_vec) + 1e-9)
    best = max(CHORD_TEMPLATES.items(), key=lambda kv: kv[1] @ c)
    return best[0], float(best[1] @ c)


def bar_features(path, grid, sr=ASR):
    y = load(path, sr)
    S = np.abs(librosa.stft(y, n_fft=4096, hop_length=1024))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    fps = sr / 1024
    bands = _band_db(S, freqs)
    chroma = librosa.feature.chroma_stft(S=S ** 2, sr=sr, n_fft=4096, hop_length=1024)
    cent = librosa.feature.spectral_centroid(S=S, sr=sr)[0]
    rms = librosa.feature.rms(S=S, frame_length=4096)[0]
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=oenv, sr=sr, hop_length=HOP, units='time')
    nb = int(np.floor(grid.time_to_bar(len(y) / sr))) - 1
    rows = []
    for b in range(1, nb + 1):
        f0, f1 = int(grid.bar_time(b) * fps), int(grid.bar_time(b + 1) * fps)
        f0 = max(f0, 0)
        if f1 <= f0 + 1:
            continue
        t0, t1 = grid.bar_time(b), grid.bar_time(b + 1)
        ch = chroma[:, f0:f1].mean(1)
        rows.append({'bar': b, 'db': float(20 * np.log10(rms[f0:f1].mean() + 1e-9)),
                     'bands': bands[:, f0:f1].mean(1), 'centroid': float(np.median(cent[f0:f1])),
                     'chroma': ch, 'chord': chord_label(ch)[0],
                     'onsets': int(((onsets >= t0) & (onsets < t1)).sum())})
    return rows


def sections(path, grid, min_bars=4):
    """Segment by novelty over per-bar features. Returns sections with letter labels by similarity."""
    rows = bar_features(path, grid)
    if len(rows) < 4:
        raise AnalysisError("audio shorter than 4 bars on this grid")
    F = np.array([np.r_[(r['bands'] - r['db']) / 10, r['chroma'] / (r['chroma'].max() + 1e-9), r['db'] / 10]
                  for r in rows])
    F = (F - F.mean(0)) / (F.std(0) + 1e-6)
    Sm = F @ F.T / F.shape[1]
    k = 4
    nov = np.zeros(len(rows))
    kern = np.kron(np.array([[1, -1], [-1, 1]]), np.ones((k, k)))
    for i in range(k, len(rows) - k):
        nov[i] = (Sm[i - k:i + k, i - k:i + k] * kern).sum()
    # energy jumps count as novelty too
    dbs = np.array([r['db'] for r in rows])
    jump = np.r_[0, np.abs(np.diff(dbs))]
    score = nov / (nov.max() + 1e-9) + jump / (jump.max() + 1e-9) * 0.7
    bounds = [0]
    for i in np.argsort(-score):
        if score[i] < 0.25:
            break
        if all(abs(i - b) >= min_bars for b in bounds) and len(rows) - i >= min_bars // 2:
            bounds.append(int(i))
    bounds = sorted(bounds) + [len(rows)]
    segs = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        segs.append({'start_bar': rows[a]['bar'], 'end_bar': rows[b - 1]['bar'], 'vec': F[a:b].mean(0),
                     'db': float(np.mean(dbs[a:b])), 'rows': rows[a:b]})
    labels = []
    protos = []
    for s in segs:
        best = None
        for li, pv in enumerate(protos):
            d = np.linalg.norm(s['vec'] - pv) / np.sqrt(len(pv))
            if d < 0.55 and (best is None or d < best[1]):
                best = (li, d)
        if best is None:
            protos.append(s['vec'])
            labels.append(chr(65 + len(protos) - 1))
        else:
            labels.append(chr(65 + best[0]) + "'")
    lines = []
    for s, lab in zip(segs, labels):
        bands = np.mean([r['bands'] for r in s['rows']], axis=0)
        chords = _run_length([r['chord'] for r in s['rows']])
        dom = ' '.join(f"{BANDS[i][0]}" for i in np.argsort(-bands)[:2])
        lines.append(f"{lab:<3} bars {s['start_bar']:>3}-{s['end_bar']:<3} ({s['end_bar'] - s['start_bar'] + 1:>2} bars, "
                     f"{grid.bar_time(s['start_bar']):6.1f}s) level {s['db']:6.1f} dB, loudest bands: {dom}; "
                     f"onsets/bar {np.mean([r['onsets'] for r in s['rows']]):.1f}; chroma: {chords}")
        s['label'] = lab
        del s['vec']
    return segs, '\n'.join(lines)


def _run_length(seq, maxn=6):
    out = []
    for x in seq:
        if out and out[-1][0] == x:
            out[-1][1] += 1
        else:
            out.append([x, 1])
    s = ' '.join(f"{c}x{n}" if n > 1 else c for c, n in out[:maxn])
    return s + (' ...' if len(out) > maxn else '')


def bar_table(path, grid, bars=None, max_bars=32):
    """Per-bar: level, band balance, centroid, onsets, chroma chord."""
    rows = bar_features(path, grid)
    b0, b1, _ = _range(load(path), ASR, grid, bars, max_bars)
    rows = [r for r in rows if b0 <= r['bar'] < b1]
    head = "bar   level  " + ' '.join(f"{n:>6}" for n, _, _ in BANDS) + "  centroid onsets chord"
    lines = [head]
    for r in rows:
        lines.append(f"{r['bar']:>3} {r['db']:7.1f}  " + ' '.join(f"{v:6.1f}" for v in r['bands']) +
                     f"  {r['centroid']:7.0f}Hz {r['onsets']:>5}  {r['chord']}")
    lines.append("(band columns are dB energy; compare columns across bars/files, not absolute)")
    return rows, '\n'.join(lines)


# ------------------------------------------------------------------ key / chords

KS_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
KS_MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def tuning(y, sr=ASR, max_sec=120):
    """Offset of the recording from A440 in cents, from interpolated spectral peaks (60-2500 Hz) folded onto the
    semitone grid (circular mean, weighted by level). Returns (cents, r); r is the concentration, about 0.15-0.25 on
    tonal mixes; a drum loop reads 0.1, so under 0.15 means no clear pitch content. Sped-up records sit tens of cents off."""
    if len(y) > max_sec * sr:
        a = (len(y) - max_sec * sr) // 2
        y = y[a:a + max_sec * sr]
    n = 8192
    S = np.abs(librosa.stft(y, n_fft=n, hop_length=2048))
    f = np.arange(S.shape[0]) * sr / n
    band = (f[1:-1] > 60) & (f[1:-1] < 2500)
    L = 20 * np.log10(S + 1e-9)
    z = w = 0.0
    for j in range(S.shape[1]):
        c = L[:, j]
        pk = np.where(band & (c[1:-1] > c[:-2]) & (c[1:-1] >= c[2:]) & (c[1:-1] > c.max() - 40))[0] + 1
        if not len(pk):
            continue
        a, b, g = c[pk - 1], c[pk], c[pk + 1]
        m = 12 * np.log2((pk + 0.5 * (a - g) / (a - 2 * b + g - 1e-12)) * sr / n / 440.0) + 69
        z = z + (S[pk, j] * np.exp(2j * np.pi * (m - np.round(m)))).sum()
        w += S[pk, j].sum()
    if not w:
        return 0.0, 0.0
    return float(100 * np.angle(z / w) / (2 * np.pi)), float(abs(z / w))


def swing(path, bpm, offset, sr=ASR):
    """Swing from picked hi-hat onsets (> 6 kHz) folded onto the beat. 16th swing = how late the 'a' (beat + 3/4)
    lands against the 'and' (beat + 1/2); 8th swing = the 'and' against the beat. Both in beats (0.03 = 3% of a
    beat late; straight = 0). Use a drum stem: on a full mix other highs bury the hats (a G-funk mix read 0.00,
    its drum stem 0.03, the hand measurement). The 8th reading also holds kick-vs-hat timing."""
    y = load(path, sr=sr)
    hop = 64
    S = np.abs(librosa.stft(y, n_fft=512, hop_length=hop))
    f = librosa.fft_frequencies(sr=sr, n_fft=512)
    on = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[f > 6000]), sr=sr, hop_length=hop)
    fr = librosa.onset.onset_detect(onset_envelope=on, sr=sr, hop_length=hop, units='frames')
    if len(fr) < 16:
        return {}, "swing: too few hi-hat onsets to measure"
    fr = fr[on[fr] > np.percentile(on[fr], 30)]
    spb = 60 / bpm
    x = ((librosa.frames_to_time(fr, sr=sr, hop_length=hop) - offset) / spb) % 1.0
    pos = {}
    for p, lo, hi in ((0.0, -0.11, 0.11), (0.5, -0.11, 0.2), (0.75, -0.11, 0.11)):
        d = (x - p + 0.5) % 1.0 - 0.5
        m = (d > lo) & (d < hi)
        pos[p] = (float(np.median(d[m])), int(m.sum())) if m.sum() > 8 else (None, int(m.sum()))
    s8 = pos[0.5][0] - pos[0.0][0] if pos[0.5][0] is not None and pos[0.0][0] is not None else None
    s16 = pos[0.75][0] - pos[0.5][0] if pos[0.75][0] is not None and pos[0.5][0] is not None else None
    ms = spb * 1000

    def show(v, n):
        return f"{v:+.3f} beat ({v * ms:+.0f} ms, {n} hits)" if v is not None else f"not measured ({n} hits)"
    data = {'swing16': None if s16 is None else round(s16, 3), 'swing8': None if s8 is None else round(s8, 3)}
    return data, (f"swing from hi-hat onsets in {os.path.basename(path)}: 16ths {show(s16, pos[0.75][1])}, "
                  f"8ths {show(s8, pos[0.5][1])}. Write swung notes that late (straight = 0); do not guess.")


def key_estimate(path, t0=None, t1=None):
    y = load(path)
    if t0 is not None:
        y = y[int(t0 * ASR):int(t1 * ASR)]
    ch = librosa.feature.chroma_cqt(y=y, sr=ASR, hop_length=512).mean(1)
    res = []
    for r in range(12):
        res.append((np.corrcoef(np.roll(KS_MAJ, r), ch)[0, 1], NAMES[r] + ' major'))
        res.append((np.corrcoef(np.roll(KS_MIN, r), ch)[0, 1], NAMES[r] + ' minor'))
    res.sort(reverse=True)
    return res[:3], f"key: {res[0][1]} (r={res[0][0]:.2f}); runners-up: {res[1][1]} ({res[1][0]:.2f}), {res[2][1]} ({res[2][0]:.2f})"


def chords(path, grid, bars=None, per_bar=2, max_bars=32):
    """Chord + bass-note per 1/per_bar bar, from CQT chroma (bass from < 200 Hz)."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, t1 = _span(y, ASR, grid, b0, b1)
    hop = 512
    C = np.abs(librosa.cqt(seg, sr=ASR, hop_length=hop, fmin=librosa.note_to_hz('C1'), n_bins=84, bins_per_octave=12))
    fps = ASR / hop
    lines = []
    data = []
    step = grid.bpb / per_bar
    for b in range(b0, b1):
        cells = []
        for k in range(per_bar):
            ta = grid.bar_time(b) + k * step * grid.spb - t0
            tb = ta + step * grid.spb
            fa, fb = int(ta * fps), int(tb * fps)
            if fb <= fa:
                continue
            blk = C[:, fa:fb].mean(1)
            chroma = np.zeros(12)
            for i, v in enumerate(blk[24:]):  # from C3 up for harmony
                chroma[(i + 24) % 12] += v
            bass = blk[:36]  # C1..B3
            bn = int(np.argmax(bass))
            lab, conf = chord_label(chroma)
            cells.append(f"{lab}/{midi_to_name(bn + 24)}" + ('?' if conf < 0.7 else ''))
            data.append({'bar': b, 'part': k, 'chord': lab, 'bass': midi_to_name(bn + 24), 'conf': round(conf, 2)})
        lines.append(f"bar {b:>3}: " + '  '.join(f"{c:<12}" for c in cells))
    lines.append("(chord/bass-note; '?' = weak template match: likely a riff or single-note line, not a chord)")
    return data, '\n'.join(lines)


# ------------------------------------------------------------------ melody / notes

def melody(path, grid, bars=None, fmin='C1', fmax='C7', quant=0.25, max_bars=16, min_conf=0.0):
    """Monophonic line -> quantized notes (notes_write format). Notes are segmented at onsets (and at pitch changes
    inside legato phrases); each segment's pitch is the median pYIN f0 of its voiced frames."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    _, t0, t1 = _span(y, ASR, grid, b0, b1)
    pre = min(t0, 0.3)
    pad = int(0.3 * ASR) - int(pre * ASR)  # zero pre-roll so a note at the very start still has an onset
    seg = np.concatenate([np.zeros(pad), y[int((t0 - pre) * ASR):int(t1 * ASR)]])
    ts = t0 - 0.3
    hop = 128
    f0, voiced, prob = librosa.pyin(seg, fmin=librosa.note_to_hz(fmin), fmax=librosa.note_to_hz(fmax), sr=ASR,
                                    hop_length=hop, frame_length=2048, center=True)
    nfr = len(f0)
    times = librosa.times_like(f0, sr=ASR, hop_length=hop) + ts
    rms = librosa.feature.rms(y=seg, hop_length=hop, frame_length=1024)[0][:nfr]
    rdb = 20 * np.log10(rms + 1e-9)
    floor = rdb.max() - 30
    midi = np.where(voiced & (prob > min_conf), librosa.hz_to_midi(np.nan_to_num(f0, nan=1.0)), np.nan)
    oenv = librosa.onset.onset_strength(y=seg, sr=ASR, hop_length=hop)
    ons = librosa.onset.onset_detect(onset_envelope=oenv, sr=ASR, hop_length=hop, backtrack=True).tolist()
    # split legato phrases where the rounded pitch changes and stays changed for >= 100 ms
    # (shorter changes are glides or pYIN window smear at note boundaries)
    r = np.where(np.isnan(midi), -1, np.round(midi)).astype(int)
    hold = int(0.1 * ASR / hop)
    runs = []  # stable runs: (start_frame, pitch) lasting >= hold frames
    i = 0
    while i < nfr:
        j = i
        while j < nfr and r[j] == r[i]:
            j += 1
        if r[i] >= 0 and j - i >= hold:
            runs.append((i, j, r[i]))
        i = j
    for (i0, e0, p0), (i1, e1, p1) in zip(runs[:-1], runs[1:]):
        # the new note starts where the old pitch stopped being stable (pYIN smears ~half a window late)
        cut = e0 - int(0.02 * ASR / hop)
        if p1 != p0 and all(abs(cut - o) > hold for o in ons) and not any(i0 < o < i1 for o in ons):
            ons.append(cut)
    ons = sorted(set(ons))
    bounds = ons + [nfr]
    base_beat = grid.time_to_beat(grid.bar_time(b0))
    out = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if times[a] < t0 - 0.03:
            continue
        seg_m = midi[a:b]
        # note ends where energy falls below floor or at next onset
        e = a
        while e < b and rdb[e] > floor:
            e += 1
        n_ = max(e - a, 1)
        vm = seg_m[n_ // 4: max(n_ * 3 // 4, n_ // 4 + 1)] if n_ >= 8 else seg_m[:n_]  # middle half: skip glide/smear
        vm = vm[~np.isnan(vm)]
        if len(vm) < max(3, 0.25 * (e - a)):
            continue
        p = _refine_pitch(seg, times[a] - ts, times[min(e, nfr - 1)] - ts, float(np.median(vm)))
        p = _octave_up(seg, times[a] - ts, times[min(e, nfr - 1)] - ts, p, librosa.note_to_midi(fmax))
        sb = grid.time_to_beat(times[a]) - base_beat
        eb = grid.time_to_beat(times[min(e, nfr - 1)]) - base_beat
        qs = round(sb / quant) * quant
        qd = max(quant, round((eb - sb) / quant) * quant)
        vel = int(np.clip(127 + (rdb[a:max(e, a + 1)].max() - rdb.max()) * 2, 1, 127))
        out.append([qs, int(round(p)), qd, vel, (p - round(p)) * 100])
    merged = []
    for nt in out:
        if merged and nt[0] <= merged[-1][0]:
            continue
        if merged and nt[0] < merged[-1][0] + merged[-1][2]:
            merged[-1][2] = nt[0] - merged[-1][0]
        merged.append(nt)
    lines = [f"# melody bars {b0}-{b1 - 1}: start beats relative to bar {b0} beat 1 (paste into notes_write at bar {b0})"]
    for st, p, d, v, c in merged:
        lines.append(f"{fmt_num(st)} {midi_to_name(p)} {fmt_num(d)} {v}" + (f"   # {c:+.0f}c" if abs(c) > 25 else ''))
    if not merged:
        lines.append("# no confident pitched notes (unpitched/noisy or polyphonic material?)")
    else:
        lines += _melody_confidence(seg, ts, merged, grid, base_beat, prob, times, fmin, fmax)
    return merged, '\n'.join(lines)


def _melody_confidence(seg, ts, notes, grid, base_beat, prob, times, fmin, fmax):
    """How far to trust a melody reading: pYIN's voicing probability over the notes, and how many notes had another
    pitch within 6 dB of the one read (a second part in the source: the reader can follow the backing instead).
    The other pitch is looked for over the whole range, so a backing outside fmin/fmax still shows."""
    hop = 256
    E = _salience(seg, 'C1', hop=hop)
    base = int(round(librosa.note_to_midi('C1')))
    fps = ASR / hop
    pv, rivals, n = [], [], 0
    for st, p, d, _, _ in notes:
        ta = grid.offset + (base_beat + st) * grid.spb - ts
        tb = grid.offset + (base_beat + st + d) * grid.spb - ts
        sel = (times >= ta + ts) & (times < tb + ts)
        if sel.any() and not np.all(np.isnan(prob[sel])):
            pv.append(float(np.nanmean(prob[sel])))
        fa = int(ta * fps)
        fb = max(int(tb * fps), fa + 1)
        i = p - base
        if fb > E.shape[1] or i < 0 or i >= E.shape[0]:
            continue
        v = np.percentile(E[:, fa:fb], 90, axis=1)          # the louder frames: a plucked rival peaks at its attack
        own = v[max(i - 1, 0):i + 2].max()
        rem = v.copy()
        rem[max(i - 1, 0):i + 2] = 0
        for h in (2, 3, 4, 5, 6):                          # the read note's partials are not a rival
            j = int(round(i + 12 * np.log2(h)))
            if j < len(rem):
                rem[j] = 0
        k = int(np.argmax(rem))
        n += 1
        if rem[k] >= own * 10 ** (-6 / 20):
            rivals.append(base + k)
    L = [f"# confidence: voicing {np.mean(pv):.2f} (mean pYIN probability over the notes; under about 0.5 the "
         f"line is breathy, noisy or not one voice)" if pv else "# confidence: no voiced frames inside the notes"]
    if n and len(rivals) >= max(2, 0.25 * n):
        line = float(np.median([nt[1] for nt in notes]))
        other = float(np.median(rivals))
        where = 'under' if other < line else 'over'
        L.append(f"# WARNING {len(rivals)} of {n} notes had another pitch within 6 dB (median "
                 f"{midi_to_name(int(round(other)))}, {where} the line read at median "
                 f"{midi_to_name(int(round(line)))}): a second part shares this source, and the reading may follow "
                 f"it. Restrict fmin/fmax to the lead's register (now {fmin}-{fmax}), and score the reader on a known "
                 f"line with a similar backing first.")
    elif pv and np.mean(pv) < 0.4:
        L.append(f"# WARNING voicing {np.mean(pv):.2f}: the line read is not one clear voice (a louder second part, "
                 f"noise or chords). Score the reader on a known line with a similar backing before trusting it.")
    elif n:
        L.append(f"# {len(rivals)} of {n} notes had another pitch within 6 dB: one line, by this reading")
    return L


# ------------------------------------------------------------------ one part or several

def stem_parts(path, grid, bars=None, max_bars=16):
    """Does a source (a stem, a 'solo' stretch) hold one part or several? Per band: its level, the rate its
    envelope moves at most, and the share of its movement at the source's note rate; the strongest low pitch per
    beat; and how many onsets are strums (three or more pitches at once). A band that is loud but barely moves at
    the note rate is played by something else: another player, or bleed from another stem."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, t1 = _span(y, ASR, grid, b0, b1)
    hop = 256
    fps = ASR / hop
    oenv = librosa.onset.onset_strength(y=seg, sr=ASR, hop_length=hop)
    ons = librosa.onset.onset_detect(onset_envelope=oenv, sr=ASR, hop_length=hop, units='frames')
    if len(ons) < 4:
        return {}, f"stem parts bars {b0}-{b1 - 1}: only {len(ons)} onsets; nothing to read (a drone or silence?)"
    rate = float(fps / np.median(np.diff(ons)))           # notes per second, from the median gap
    S = np.abs(librosa.stft(seg, n_fft=2048, hop_length=hop)) ** 2
    freqs = librosa.fft_frequencies(sr=ASR, n_fft=2048)
    data = {'note_rate': round(rate, 2), 'bands': {}}
    for name, lo, hi in BANDS:
        e = np.sqrt(S[(freqs >= lo) & (freqs < hi)].sum(0))
        nfft = max(len(e), int(fps * 8))
        P = np.abs(np.fft.rfft((e - e.mean()) * np.hanning(len(e)), n=nfft)) ** 2
        f = np.fft.rfftfreq(nfft, 1 / fps)
        band = (f >= 0.5) & (f <= min(30, fps / 2))
        at = (f >= rate / 1.25) & (f <= rate * 1.25)
        data['bands'][name] = {'db': round(float(20 * np.log10(e.mean() + 1e-12)), 1),
                               'share': round(float(P[at].sum() / (P[band].sum() + 1e-20)), 3),
                               'moves_at': round(float(f[band][np.argmax(P[band])]), 2)}
    top = max(b['db'] for b in data['bands'].values())
    live = {k for k, b in data['bands'].items() if b['db'] > top - 24}
    best = max(data['bands'][k]['share'] for k in live)
    lines = [f"stem parts bars {b0}-{b1 - 1}: note rate {rate:.1f}/s (median onset gap). Per band: level, share of "
             f"its envelope's movement at the note rate, the rate it moves at most"]
    apart = []
    for name, b in data['bands'].items():
        own = (b['db'] > top - 15 and b['share'] < 0.35 * best
               and abs(np.log2(max(b['moves_at'], 0.1) / rate)) > 0.32)
        if own:
            apart.append(name)
        lines.append(f"  {name:<7}{b['db'] - top:6.1f} dB  {100 * b['share']:4.0f}% at the note rate  moves at "
                     f"{b['moves_at']:4.1f}/s" + ('   <- moves on its own' if own else
                                                  ('' if name in live else '   (quiet)')))
    E = _salience(seg, 'C1', hop=hop)
    base = int(round(librosa.note_to_midi('C1')))
    lo_top = int(round(librosa.hz_to_midi(250))) - base
    beats = [] if data['bands']['bass']['db'] > top - 24 else ['  (nothing loud under 250 Hz)']
    for b in (range(b0, b1) if not beats else []):
        cells = []
        for k in range(grid.bpb):
            fa = int((grid.bar_time(b + k / grid.bpb) - t0) * fps)
            fb = int((grid.bar_time(b + (k + 1) / grid.bpb) - t0) * fps)
            if fb <= fa or fa >= E.shape[1]:
                continue
            cells.append(midi_to_name(base + int(np.argmax(E[:lo_top, fa:fb].mean(1)))))
        beats.append(f"  bar {b:>3}: " + ' '.join(cells))
    strums = 0
    for o in ons:
        v = E[:, o:o + max(1, int(0.06 * fps))].mean(1)
        rem, found = v.copy(), 0
        for _ in range(8):
            i = int(np.argmax(rem))
            if rem[i] < v.max() * 10 ** (-10 / 20):
                break
            found += 1
            for h in (1, 2, 3, 4, 5, 6):
                j = int(round(i + 12 * np.log2(h)))
                rem[max(j - 1, 0):j + 2] = 0
        strums += found >= 3
    data.update(strums=int(strums), onsets=int(len(ons)), apart=apart)
    lines.append(f"strums: {strums} of {len(ons)} onsets have three or more pitches at once")
    lines.append("strongest pitch under 250 Hz per beat:")
    lines += beats
    if apart:
        lines.append(f"WARNING {', '.join(apart)} {'is' if len(apart) == 1 else 'are'} loud but barely "
                     f"{'moves' if len(apart) == 1 else 'move'} at the note rate: another part plays there (a second "
                     f"player, or bleed from another stem). Before matching tone to this source, give yours an "
                     f"under-part at that level, or match only the bands that move with the notes; fit a voice on the "
                     f"raw render and tone-match after.")
    else:
        lines.append("one part, by this reading: every loud band moves with the notes")
    return data, '\n'.join(lines)


def _refine_pitch(y, ta, tb, est_midi):
    """Refine a pitch estimate with one long FFT over the note's middle (harmonic sum, +-1 semitone search)."""
    d = tb - ta
    a, b = int((ta + d * 0.2) * ASR), int((tb - d * 0.2) * ASR)
    x = y[a:b]
    if len(x) < 1024:
        return est_midi
    n = 1 << 16
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=n))
    fr = np.fft.rfftfreq(n, 1 / ASR)
    cands = est_midi + np.linspace(-1, 1, 81)
    best, bm = -1, est_midi
    for m in cands:
        f = 440 * 2 ** ((m - 69) / 12)
        sc = sum(S[min(int(round(f * h * n / ASR)), len(S) - 1)] / h ** 0.5 for h in range(1, 5))
        if sc > best:
            best, bm = sc, m
    return bm


def _octave_up(y, ta, tb, est_midi, max_midi):
    """Undo a sub-octave reading. pYIN's smoothing can hold the previous pitch through a quick jump an octave up
    (the lower note's period is also a period of the higher one), so a note read at f is checked against its own
    spectrum: if its odd partials (f, 3f, 5f) are near silent while its even ones (2f, 4f, 6f) carry the sound,
    the note is at 2f. Any real tone at f has energy at f or 3f, so a true low note keeps its octave."""
    d = tb - ta
    a, b = int((ta + d * 0.2) * ASR), int((tb - d * 0.2) * ASR)
    x = y[max(a, 0):b]
    if len(x) < 1024:
        return est_midi
    n = 1 << 16
    S = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=n))

    def part(f):                                           # strongest bin within 30 cents of f
        if f >= ASR / 2 * 0.95:
            return 0.0
        lo, hi = int(f * 2 ** (-0.3 / 12) * n / ASR), int(np.ceil(f * 2 ** (0.3 / 12) * n / ASR)) + 1
        return float(S[lo:hi].max())

    m = est_midi
    while m + 12 <= max_midi + 0.5:
        f = 440 * 2 ** ((m - 69) / 12)
        odd = sum(part(f * h) for h in (1, 3, 5))
        even = sum(part(f * h) for h in (2, 4, 6))
        if even <= 0 or odd >= 0.1 * even:                 # odd partials within 20 dB: the read octave holds
            break
        m += 12
    return m


def transcribe(path, grid, bars=None, quant=0.25, max_bars=16, fmin='C1', n_octaves=7, threshold=0.35,
               max_poly=6, fund_db=24):
    """Polyphonic note estimate from a harmonic-sum CQT salience. Good on synth riffs/chords, rough on dense mixes.
    Run it on a separated stem when possible."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, t1 = _span(y, ASR, grid, b0, b1)
    hop = 256
    bpo = 36
    n_octaves = _max_octaves(fmin, n_octaves)
    C = np.abs(librosa.cqt(seg, sr=ASR, hop_length=hop, fmin=librosa.note_to_hz(fmin), n_bins=bpo * n_octaves,
                           bins_per_octave=bpo))
    L = librosa.amplitude_to_db(C, ref=np.max)
    L = np.maximum(L, -60) + 60  # 0..60
    # harmonic sum salience on semitone grid
    nsemi = 12 * n_octaves
    sal = np.zeros((nsemi, C.shape[1]))
    for s in range(nsemi):
        acc = 0
        for h, w in ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.3), (5, 0.2)):
            idx = int(round(s * 3 + bpo * np.log2(h)))
            if idx + 1 < C.shape[0]:
                acc = acc + w * L[max(idx - 1, 0):idx + 2].max(0)
        sal[s] = acc
    # suppress each pitch's own harmonics (a louder note one octave/fifth below explains it)
    sal = sal / (sal.max() + 1e-9)
    act = np.zeros_like(sal, dtype=bool)
    for f in range(sal.shape[1]):
        col = sal[:, f].copy()
        for _ in range(max_poly * 3):
            if act[:, f].sum() >= max_poly:
                break
            s = int(np.argmax(col))
            if col[s] < threshold:
                break
            h1 = s * 3
            if h1 + 1 >= C.shape[0] or L[max(h1 - 1, 0):h1 + 2, f].max() < 60 - fund_db:
                col[s] = 0  # no energy at the fundamental: a sub-octave ghost of real notes' harmonics
                continue
            act[s, f] = True
            for h in (1, 2, 3, 4, 5, 6):
                j = int(round(s + 12 * np.log2(h)))
                lo, hi = max(j - 1, 0), min(j + 2, nsemi)
                col[lo:hi] *= 0.25 if h > 1 else 0.0
            col[max(s - 1, 0):s + 2] = 0
    fps = ASR / hop
    base_beat = grid.time_to_beat(grid.bar_time(b0))
    notes = []
    fmin_m = int(round(librosa.note_to_midi(fmin)))
    min_frames = int(0.06 * fps)
    for s in range(nsemi):
        row = act[s]
        f = 0
        while f < len(row):
            if row[f]:
                g = f
                while g < len(row) and (row[g] or (g + 2 < len(row) and row[g + 1:g + 3].any())):
                    g += 1
                if g - f >= min_frames:
                    sb = grid.time_to_beat(t0 + f / fps) - base_beat
                    eb = grid.time_to_beat(t0 + g / fps) - base_beat
                    qs = round(sb / quant) * quant
                    qd = max(quant, round((eb - sb) / quant) * quant)
                    vel = int(np.clip(30 + 97 * sal[s, f:g].mean(), 1, 127))
                    notes.append([qs, fmin_m + s, qd, vel])
                f = g
            f += 1
    notes.sort()
    lines = [f"# polyphonic estimate bars {b0}-{b1 - 1}, beats rel. to bar {b0} (paste into notes_write at bar {b0});"
             f" {len(notes)} notes"]
    for st, p, d, v in notes[:400]:
        lines.append(f"{fmt_num(st)} {midi_to_name(p)} {fmt_num(d)} {v}")
    if len(notes) > 400:
        lines.append(f"# ... {len(notes) - 400} more; narrow the bar range")
    return notes, '\n'.join(lines)


def _max_octaves(fmin, n_octaves):
    return int(max(1, min(n_octaves, np.floor(np.log2(ASR / 2 * 0.9 / librosa.note_to_hz(fmin))))))


def _salience(seg, fmin='C1', n_octaves=7, hop=256):
    n_octaves = _max_octaves(fmin, n_octaves)
    bpo = 36
    C = np.abs(librosa.cqt(seg, sr=ASR, hop_length=hop, fmin=librosa.note_to_hz(fmin), n_bins=bpo * n_octaves,
                           bins_per_octave=bpo))
    # semitone energies (max of 3 bins)
    nsemi = 12 * n_octaves
    E = np.stack([C[max(3 * s - 1, 0):3 * s + 2].max(0) for s in range(nsemi)])
    return E


def pitches(path, grid, bars=None, per_bar=4, max_notes=6, fmin='C1', floor_db=-30, max_bars=16):
    """Which pitches sound in each window (default: each beat). Energy per semitone from a CQT, with each note's
    harmonics explained away (a partial at 2x/3x/4x/5x of a louder lower note is not reported)."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, t1 = _span(y, ASR, grid, b0, b1)
    hop = 256
    E = _salience(seg, fmin, hop=hop)
    fps = ASR / hop
    base = int(round(librosa.note_to_midi(fmin)))
    lines = [f"pitches per 1/{per_bar} bar (loudest first, dB rel. loudest in range; harmonics explained away)"]
    data = []
    gmax = 20 * np.log10(E.max() + 1e-9)
    for b in range(b0, b1):
        cells = []
        for k in range(per_bar):
            ta = grid.bar_time(b + k / per_bar) - t0
            tb = grid.bar_time(b + (k + 1) / per_bar) - t0
            fa, fb = int(ta * fps), int(tb * fps)
            if fb <= fa:
                continue
            v = E[:, fa:fb].mean(1)
            vd = 20 * np.log10(v + 1e-9) - gmax
            rem = v.copy()
            found = []
            for _ in range(max_notes * 3):
                if len(found) >= max_notes:
                    break
                i = int(np.argmax(rem))
                d = 20 * np.log10(rem[i] + 1e-9) - gmax
                if d < floor_db:
                    break
                found.append((i, d))
                for h in (1, 2, 3, 4, 5, 6):
                    j = int(round(i + 12 * np.log2(h)))
                    if j < len(rem):
                        rem[j] = min(rem[j], max(0.0, rem[j] - v[i] * (0.6 / h if h > 1 else 1.0)))
                    for jj in (j - 1, j + 1):
                        if h > 1 and 0 <= jj < len(rem):
                            rem[jj] *= 0.7
            data.append({'bar': b, 'part': k, 'notes': [(base + i, round(float(d), 1)) for i, d in found]})
            cells.append(' '.join(f"{midi_to_name(base + i)}{d:.0f}" for i, d in found))
        lines.append(f"bar {b:>3}: " + ' | '.join(cells))
    return data, '\n'.join(lines)


# ------------------------------------------------------------------ drums

DRUM_LANES = [('low', 30, 120), ('snare', 1000, 5000), ('hat', 7000, 16000)]


def drums(path, grid, bars=None, steps_per_beat=4, max_bars=16, sens=1.0):
    """Band-split onset lanes quantized to a step grid. Output per bar: step strings (X loud, x med, o soft)."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, t1 = _span(y, ASR, grid, b0, b1)
    pre = min(t0, 0.25)  # pre-roll so a hit right at the window start is detectable
    t0 -= pre
    seg = np.concatenate([np.zeros(int(0.25 * ASR) - int(pre * ASR)), y[int(t0 * ASR):int(t1 * ASR)]])
    t0 -= 0.25 - pre
    hop = 128
    S = np.abs(librosa.stft(seg, n_fft=1024, hop_length=hop))
    freqs = librosa.fft_frequencies(sr=ASR, n_fft=1024)
    fps = ASR / hop
    lanes = {}
    for name, lo, hi in DRUM_LANES:
        m = (freqs >= lo) & (freqs < hi)
        env = librosa.onset.onset_strength(S=librosa.amplitude_to_db(S[m], ref=np.max), sr=ASR, hop_length=hop,
                                           lag=1, max_size=3)
        lvl = librosa.amplitude_to_db(np.sqrt((S[m] ** 2).mean(0)) + 1e-9, ref=np.max(S) + 1e-9)
        pk = librosa.util.peak_pick(env, pre_max=3, post_max=3, pre_avg=10, post_avg=10,
                                    delta=float(np.std(env)) * 0.8 / sens, wait=int(fps * 0.06))
        lanes[name] = (env, lvl, pk)
    nsteps = grid.bpb * steps_per_beat
    step_t = grid.spb / steps_per_beat
    # reference level per lane: loud hits in the whole analysed window (dB)
    lane_ref = {}
    for name, (env, lvl, pk) in lanes.items():
        peaks = [lvl[p:p + int(0.03 * fps)].max() for p in pk] or [0.0]
        lane_ref[name] = float(np.percentile(peaks, 90))
    out = {}
    lines = [f"steps: {steps_per_beat}/beat, {nsteps}/bar. lanes: low=kick-ish(30-120Hz) snare=noise 1-5kHz "
             f"hat=7kHz+. X within 4 dB of the lane's loud hits, x within 9, o within 16, - within 26"]
    # level-rise detector: peak_pick alone misses a downbeat after silence (a noise peak just before it takes
    # the 'wait' slot) and depends on the analysis window; a >= 9 dB rise into a step counts as a hit too
    win = int(0.03 * fps)
    back = max(int(0.04 * fps), 1)
    rise = {}
    for name, (env, lvl, pk) in lanes.items():
        swv = np.lib.stride_tricks.sliding_window_view
        fwd_max = swv(np.concatenate([lvl, np.full(win - 1, lvl[-1])]), win).max(1)    # max over [k, k+win)
        bwd_min = swv(np.concatenate([np.full(back, lvl[0]), lvl[:-1]]), back).min(1)  # min over [k-back, k)
        rise[name] = (fwd_max - bwd_min, fwd_max)
    for b in range(b0, b1):
        bar_t0 = grid.bar_time(b) - t0
        lane_strs = {}
        for name, (env, lvl, pk) in lanes.items():
            cells = ['.'] * nsteps
            strength = np.full(nsteps, -np.inf)
            for p in pk:
                tt = p / fps - bar_t0
                st = int(round(tt / step_t))
                if 0 <= st < nsteps and abs(tt - st * step_t) < step_t * 0.45:
                    strength[st] = max(strength[st], lvl[p:p + win].max())
            r, fm = rise[name]
            for st in range(nsteps):
                c = (bar_t0 + st * step_t) * fps
                k0, k1 = max(int(c - 0.45 * step_t * fps), 0), min(int(c + 0.45 * step_t * fps) + 1, len(r))
                if k1 > k0:
                    k = k0 + int(np.argmax(r[k0:k1]))
                    if r[k] >= 9.0:
                        strength[st] = max(strength[st], fm[k])
            ref = lane_ref[name]
            for i, s in enumerate(strength):
                if s > ref - 4:
                    cells[i] = 'X'
                elif s > ref - 9:
                    cells[i] = 'x'
                elif s > ref - 16:
                    cells[i] = 'o'
                elif s > ref - 26:
                    cells[i] = '-'
            lane_strs[name] = ''.join(cells)
        out[b] = lane_strs
        lines.append(f"bar {b:>3}  " + '  '.join(f"{k:<5} {_group(v, steps_per_beat)}" for k, v in lane_strs.items()))
    return out, '\n'.join(lines)


def _group(s, n):
    return ' '.join(s[i:i + n] for i in range(0, len(s), n))


# ------------------------------------------------------------------ envelope / rhythm of level

def envelope(path, grid, bars=None, steps_per_beat=4, max_bars=8, band=None):
    """Level per step (dB), one line per bar, as digits 0-9 (9 = loudest in range). Shows pumping/gating/rhythm."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    seg, t0, _ = _span(y, ASR, grid, b0, b1)
    if band:
        lo, hi = dict((n, (a, b)) for n, a, b in BANDS)[band]
        from scipy.signal import butter, sosfilt
        seg = sosfilt(butter(4, [max(lo, 20), min(hi, ASR / 2 - 100)], btype='band', fs=ASR, output='sos'), seg)
    step_t = grid.spb / steps_per_beat
    nsteps = grid.bpb * steps_per_beat
    vals = []
    for b in range(b0, b1):
        row = []
        for s in range(nsteps):
            a = int((grid.bar_time(b) + s * step_t - t0) * ASR)
            e = int(a + step_t * ASR)
            x = seg[max(a, 0):max(e, 0)]
            row.append(10 * np.log10(np.mean(x ** 2) + 1e-12) if len(x) else -120)
        vals.append(row)
    v = np.array(vals)
    top = v.max()
    digits = np.clip(np.round((v - (top - 36)) / 4), 0, 9).astype(int)
    lines = [f"level per 1/{steps_per_beat}-beat step{' in ' + band if band else ''}; 9 = {top:.1f} dB, each digit = 4 dB"]
    for b, row in zip(range(b0, b1), digits):
        lines.append(f"bar {b:>3} " + _group(''.join(map(str, row)), steps_per_beat))
    return v, '\n'.join(lines)


# ------------------------------------------------------------------ spectrum / timbre

def spectrum(path, t0, t1, n_peaks=12):
    y = load(path)
    t0 = max(t0, 0.0)
    seg = y[int(t0 * ASR):int(t1 * ASR)]
    if len(seg) < 256:
        raise AnalysisError("window too short; need >= 12 ms")
    if len(seg) < 8192:  # short one-shots: zero-pad to one analysis frame
        seg = np.concatenate([seg, np.zeros(8192 - len(seg))])
    S = np.abs(librosa.stft(seg, n_fft=8192, hop_length=2048, center=False)) ** 2
    P = S.mean(1)
    freqs = librosa.fft_frequencies(sr=ASR, n_fft=8192)
    # 1/3-octave bands
    centers = 1000 * 2 ** (np.arange(-17, 14) / 3)
    lines = ["1/3-octave levels (dB, relative to loudest band):"]
    lv = []
    for c in centers:
        m = (freqs >= c / 2 ** (1 / 6)) & (freqs < c * 2 ** (1 / 6))
        lv.append(10 * np.log10(P[m].sum() + 1e-15) if m.any() else -150)
    lv = np.array(lv) - max(lv)
    for c, l in zip(centers, lv):
        if c > ASR / 2:
            break
        bar = '#' * int(max(0, 60 + l) / 2)
        lines.append(f"{c:>7.0f}Hz {l:6.1f} {bar}")
    Pd = 10 * np.log10(P + 1e-15)
    pk = librosa.util.peak_pick(Pd, pre_max=6, post_max=6, pre_avg=30, post_avg=30, delta=6, wait=6)
    pk = sorted(pk, key=lambda i: -Pd[i])[:n_peaks]
    lines.append("strongest peaks: " + ', '.join(
        f"{freqs[i]:.0f}Hz({midi_to_name(librosa.hz_to_midi(freqs[i]))} {Pd[i] - Pd.max():.0f}dB)" for i in
        sorted(pk) if freqs[i] > 20))
    return {'centers': centers.tolist(), 'levels': lv.tolist()}, '\n'.join(lines)


def timbre(path, t0, t1, f0_hint=None):
    """Describe a sound: pitch, harmonic profile (-> waveform guess), brightness, noisiness, envelope, width."""
    ys = load(path, stereo=True)
    y = ys.mean(0)
    t0 = max(t0, 0.0)
    a, b = int(t0 * ASR), int(t1 * ASR)
    seg = y[a:b]
    if len(seg) < 1024:
        raise AnalysisError("window too short; need >= 50 ms")
    lines = []
    # envelope
    hop = 64
    env = librosa.feature.rms(y=seg, frame_length=256, hop_length=hop)[0]
    et = np.arange(len(env)) * hop / ASR
    pk = int(np.argmax(env))
    pdb = 20 * np.log10(env[pk] + 1e-9)
    edb = 20 * np.log10(env + 1e-9)
    after = edb[pk:]
    d6 = next((et[pk + i] - et[pk] for i, v in enumerate(after) if v < pdb - 6), None)
    d20 = next((et[pk + i] - et[pk] for i, v in enumerate(after) if v < pdb - 20), None)
    start_i = next((i for i, v in enumerate(edb) if v > pdb - 20), 0)
    attack = et[pk] - et[start_i]
    tail = edb[int(len(edb) * 0.6):int(len(edb) * 0.9)]
    sustain = (np.mean(tail) - pdb) if len(tail) else 0
    lines.append(f"envelope: attack ~{attack * 1000:.0f} ms, peak at {et[pk]:.3f}s into window, -6 dB after "
                 f"{'%.0f ms' % (d6 * 1000) if d6 else '>window'}, -20 dB after {'%.0f ms' % (d20 * 1000) if d20 else '>window'}; "
                 f"late-window level {sustain:+.1f} dB vs peak ({'sustained' if sustain > -6 else 'decaying'})")
    # pitch
    body = seg[int(len(seg) * 0.1):] if len(seg) > 4096 else seg
    f0s, vf, _ = librosa.pyin(body, fmin=30, fmax=2000, sr=ASR, frame_length=4096)
    vratio = float(np.mean(vf)) if len(vf) else 0.0
    f0 = f0_hint or (float(np.nanmedian(f0s)) if np.any(vf) else None)
    if f0 and not f0_hint:
        wob = np.nanstd(librosa.hz_to_midi(f0s[vf])) * 100 if np.any(vf) else 0
        if vratio < 0.4 or wob > 150:
            lines.append(f"pitch: no single stable pitch (voiced {vratio:.0%}, spread {wob:.0f} c) - chord, glide or "
                         f"noise; analyze a single note, or use analyze_spectrum peaks")
            f0 = None
    S = np.abs(np.fft.rfft(body * np.hanning(len(body)), n=max(len(body), 1 << 16)))
    freqs = np.fft.rfftfreq(max(len(body), 1 << 16), 1 / ASR)
    P = S ** 2
    cent = float((freqs * P).sum() / (P.sum() + 1e-12))
    cum = np.cumsum(P) / (P.sum() + 1e-12)
    roll = float(freqs[np.searchsorted(cum, 0.85)])
    flat = float(np.exp(np.mean(np.log(S[1:] + 1e-12))) / (np.mean(S[1:]) + 1e-12))
    if f0 and np.isfinite(f0):
        spread = np.nanstd(librosa.hz_to_midi(f0s[vf])) * 100 if np.any(vf) else 0
        lines.append(f"pitch: {f0:.1f} Hz = {midi_to_name(librosa.hz_to_midi(f0))} "
                     f"({(librosa.hz_to_midi(f0) - round(librosa.hz_to_midi(f0))) * 100:+.0f} c); pitch wobble sd {spread:.0f} c"
                     f"{' (glide/vibrato/multiple notes)' if spread > 40 else ''}")
        harm = []
        widths = []
        for h in range(1, 17):
            fh = f0 * h
            if fh > ASR / 2 - 200:
                break
            m = (freqs > fh * 0.97) & (freqs < fh * 1.03)
            if not m.any():
                break
            harm.append(S[m].max())
            above = S[m] > S[m].max() * 0.5
            widths.append(above.sum() * (freqs[1] - freqs[0]))
        hdb = 20 * np.log10(np.array(harm) / (harm[0] + 1e-12) + 1e-9)
        lines.append("harmonics dB rel. h1: " + ' '.join(f"h{i + 1}:{v:.0f}" for i, v in enumerate(hdb)))
        odd = np.mean([harm[i] for i in range(0, len(harm), 2)])
        even = np.mean([harm[i] for i in range(1, len(harm), 2)]) if len(harm) > 1 else 0
        slope = np.polyfit(np.log2(np.arange(1, len(hdb) + 1)), hdb, 1)[0] if len(hdb) > 2 else 0
        guess = ('sine-like (few harmonics)' if len(hdb) > 3 and np.all(hdb[1:4] < -25) else
                 'square/pulse-like (odd harmonics dominate)' if even < odd * 0.35 else
                 'saw-like (all harmonics, ~-6 dB/oct)' if -8.5 < slope < -3.5 else
                 'bright/distorted (flat harmonic slope)' if slope >= -3.5 else 'filtered/soft (steep slope)')
        lines.append(f"harmonic slope {slope:.1f} dB/octave, even/odd ratio {even / (odd + 1e-9):.2f} -> {guess}")
        wmed = np.median(widths[:8]) if widths else 0
        lines.append(f"partial width ~{wmed:.1f} Hz at -6 dB (>~{3 * ASR / len(body):.1f} Hz resolution: wider = detune/unison/chorus)")
    elif not any(l.startswith('pitch:') for l in lines):
        lines.append("pitch: none detected (unpitched / noise / dense chord)")
    lines.append(f"brightness: centroid {cent:.0f} Hz, 85% rolloff {roll:.0f} Hz (a lowpass cutoff is usually near/below "
                 f"rolloff); spectral flatness {flat:.3f} ({'noisy' if flat > 0.3 else 'tonal' if flat < 0.1 else 'mixed'})")
    # brightness trajectory -> filter envelope
    if len(seg) > ASR * 0.2:
        cent_t = librosa.feature.spectral_centroid(y=seg, sr=ASR, hop_length=512)[0]
        k = max(1, len(cent_t) // 6)
        traj = [np.median(cent_t[i:i + k]) for i in range(0, len(cent_t), k)][:6]
        lines.append("centroid over time: " + ' -> '.join(f"{c:.0f}" for c in traj) + " Hz "
                     f"({'falling: filter/decay envelope' if traj[-1] < traj[0] * 0.7 else 'rising: opening filter' if traj[-1] > traj[0] * 1.4 else 'steady'})")
    L, R = ys[0, a:b], ys[1, a:b]
    mid, side = (L + R) / 2, (L - R) / 2
    w = np.sqrt(np.mean(side ** 2) / (np.mean(mid ** 2) + 1e-12))
    corr = np.corrcoef(L, R)[0, 1] if np.std(L) > 0 and np.std(R) > 0 else 1.0
    lines.append(f"stereo: side/mid {w:.2f} ({'mono' if w < 0.05 else 'narrow' if w < 0.3 else 'wide'}), L/R corr {corr:.2f}")
    return {'centroid': cent, 'rolloff': roll, 'flatness': flat, 'f0': f0}, '\n'.join(lines)


# ------------------------------------------------------------------ loudness / overview

def loudness(path):
    import pyloudnorm as pyln
    y = load(path, sr=44100, stereo=True)
    meter = pyln.Meter(44100)
    lufs = meter.integrated_loudness(y.T)
    peak = 20 * np.log10(np.max(np.abs(y)) + 1e-12)
    return lufs, peak


def overview(path, grid=None):
    y = load(path)
    dur = len(y) / ASR
    lufs, peak = loudness(path)
    lines = [f"file: {os.path.basename(path)}  duration {dur:.1f}s  integrated {lufs:.1f} LUFS  peak {peak:.1f} dBFS"]
    if grid is None:
        g, txt = beat_grid(path)
        grid = Grid(g['bpm'], g['offset_sec'])
        lines.append(txt)
    else:
        b = grid.first_bar()
        lines.append(f"grid: {grid.bpm} BPM, bar {b} at {grid.bar_time(b):.3f}s")
    k, ktxt = key_estimate(path)
    lines.append(ktxt)
    segs, stxt = sections(path, grid)
    lines.append("sections:\n" + stxt)
    return {'duration': dur, 'lufs': lufs, 'peak': peak, 'sections': [
        {k2: v for k2, v in s.items() if k2 != 'rows'} for s in segs]}, '\n'.join(lines)


# ------------------------------------------------------------------ compare

def compare(path_a, path_b, grid, bars=None, offset_b=0.0, max_bars=200, detail=8):
    """Compare A (your render) against B (reference) bar by bar on the same grid.
    offset_b shifts B (seconds) if B's bar 1 is at a different time. Reports per-band dB deltas (A-B),
    chroma similarity, onset-rhythm correlation and the worst bars."""
    fa = bar_features(path_a, grid)
    gb = Grid(grid.bpm, grid.offset + offset_b, grid.bpb)
    fb = bar_features(path_b, gb)
    ya = load(path_a)
    b0, b1, _ = _range(ya, ASR, grid, bars, max_bars)
    A = {r['bar']: r for r in fa}
    B = {r['bar']: r for r in fb}
    common = [b for b in range(b0, b1) if b in A and b in B]
    if not common:
        raise AnalysisError("no overlapping bars; check grid/offset and lengths")
    rows = []
    for b in common:
        ra, rb = A[b], B[b]
        dband = ra['bands'] - rb['bands']
        ca = ra['chroma'] / (np.linalg.norm(ra['chroma']) + 1e-9)
        cb = rb['chroma'] / (np.linalg.norm(rb['chroma']) + 1e-9)
        rows.append({'bar': b, 'dlevel': ra['db'] - rb['db'], 'dband': dband, 'chroma_sim': float(ca @ cb),
                     'dcent': ra['centroid'] / (rb['centroid'] + 1e-9), 'chord_a': ra['chord'], 'chord_b': rb['chord']})
    # rhythm: onset envelope correlation per bar
    ob = load(path_b)
    oa_env = librosa.onset.onset_strength(y=ya, sr=ASR, hop_length=HOP)
    ob_env = librosa.onset.onset_strength(y=ob, sr=ASR, hop_length=HOP)
    fps = ASR / HOP
    for r in rows:
        a0, a1 = max(0, int(grid.bar_time(r['bar']) * fps)), max(0, int(grid.bar_time(r['bar'] + 1) * fps))
        c0, c1 = max(0, int(gb.bar_time(r['bar']) * fps)), max(0, int(gb.bar_time(r['bar'] + 1) * fps))
        xa, xb = oa_env[a0:a1], ob_env[c0:c1]
        m = min(len(xa), len(xb))
        r['rhythm'] = float(np.corrcoef(xa[:m], xb[:m])[0, 1]) if m > 4 and xa[:m].std() > 0 and xb[:m].std() > 0 else 0.0
    # log-mel distance overall
    sa = slice(max(0, int(grid.bar_time(b0) * ASR)), max(0, int(grid.bar_time(b1) * ASR)))
    sb = slice(max(0, int(gb.bar_time(b0) * ASR)), max(0, int(gb.bar_time(b1) * ASR)))
    Ma = librosa.power_to_db(librosa.feature.melspectrogram(y=ya[sa], sr=ASR, n_mels=64))
    Mb = librosa.power_to_db(librosa.feature.melspectrogram(y=ob[sb], sr=ASR, n_mels=64))
    m = min(Ma.shape[1], Mb.shape[1])
    mel_l1 = float(np.mean(np.abs(Ma[:, :m] - Mb[:, :m])))
    dband = np.array([r['dband'] for r in rows])
    summary = {
        'bars': [common[0], common[-1]],
        'mean_abs_band_db': float(np.mean(np.abs(dband))),
        'band_bias_db': dict(zip([n for n, _, _ in BANDS], np.round(dband.mean(0), 1).tolist())),
        'level_bias_db': float(np.mean([r['dlevel'] for r in rows])),
        'chroma_sim': float(np.mean([r['chroma_sim'] for r in rows])),
        'rhythm_corr': float(np.mean([r['rhythm'] for r in rows])),
        'mel_l1_db': mel_l1,
    }
    lines = [f"compare A={os.path.basename(path_a)} vs B={os.path.basename(path_b)}, bars {common[0]}-{common[-1]}",
             f"SCORES  chroma_sim {summary['chroma_sim']:.3f} (1=same harmony)  rhythm_corr {summary['rhythm_corr']:.3f} "
             f"(1=same onsets)  mean|band dB| {summary['mean_abs_band_db']:.1f}  log-mel L1 {mel_l1:.1f} dB",
             f"level bias A-B {summary['level_bias_db']:+.1f} dB; band bias A-B: " +
             ', '.join(f"{k} {v:+.1f}" for k, v in summary['band_bias_db'].items())]
    worst = sorted(rows, key=lambda r: -(np.mean(np.abs(r['dband'])) + 8 * (1 - r['chroma_sim']) + 4 * (1 - r['rhythm'])))[:detail]
    lines.append(f"worst {len(worst)} bars (band deltas A-B dB: " + ' '.join(n for n, _, _ in BANDS) + "):")
    for r in sorted(worst, key=lambda r: r['bar']):
        lines.append(f"  bar {r['bar']:>3} level {r['dlevel']:+5.1f} | " + ' '.join(f"{v:+5.1f}" for v in r['dband']) +
                     f" | chroma {r['chroma_sim']:.2f} ({r['chord_a']} vs {r['chord_b']}) rhythm {r['rhythm']:.2f}")
    al = align(path_a, path_b, max(0.0, grid.bar_time(b0)), gb.bar_time(b1))
    summary['lag_ms'] = al['lag_ms']
    lines.insert(3, f"timing: B is {al['lag_ms']:+.0f} ms relative to A (onset xcorr {al['corr']:.2f} at best lag vs "
                    f"{al['corr_at_0']:.2f} at 0)")
    adv = []
    if abs(al['lag_ms']) > 12 and al['corr'] - al['corr_at_0'] > 0.03:
        adv.append(f"timing offset: shift A by {al['lag_ms']:+.0f} ms (project_set offset_sec += {al['lag_ms'] / 1000:+.3f}) "
                   f"before judging rhythm")
    for n, v in summary['band_bias_db'].items():
        if abs(v) > 3:
            adv.append(f"{n} is {abs(v):.0f} dB {'too loud' if v > 0 else 'too quiet'} on average")
    if summary['chroma_sim'] < 0.8:
        adv.append("harmony differs: check notes/key with chords or transcribe on the worst bars")
    if summary['rhythm_corr'] < 0.4:
        adv.append("onset pattern differs: check drums/gating with drums or envelope on the worst bars")
    if adv:
        lines.append("advice: " + '; '.join(adv))
    return {'summary': summary, 'rows': rows}, '\n'.join(lines)


def align(path_a, path_b, t0=None, t1=None, band=None, max_ms=300, shift_b=0.0):
    """Lag of B relative to A from onset-envelope cross-correlation (optionally inside one band).
    Negative lag = B's events come EARLIER than A's. t0/t1 are A's seconds; shift_b = B's time minus A's time for
    the same song moment (non-zero when one of them is a windowed render)."""
    hop = 128
    fps = ASR / hop

    def env(p):
        y = load(p)
        S = np.abs(librosa.stft(y, n_fft=1024, hop_length=hop))
        if band:
            lo, hi = dict((n, (a, b)) for n, a, b in BANDS)[band]
            f = librosa.fft_frequencies(sr=ASR, n_fft=1024)
            S = S[(f >= lo) & (f < hi)]
        return librosa.onset.onset_strength(S=librosa.amplitude_to_db(S), sr=ASR, hop_length=hop)
    ea, eb = env(path_a), env(path_b)
    s = int(round(shift_b * fps))
    a0 = int((t0 or 0) * fps)
    a1 = int(t1 * fps) if t1 else len(ea)
    L = int(max_ms / 1000 * fps)
    a0, a1 = max(a0, L, L - s), min(a1, len(ea) - L, len(eb) - L - s)
    if a1 - a0 < 2:
        raise AnalysisError("the two sources don't overlap in time; check bars / render window")
    x = ea[a0:a1]
    res = []
    for lag in range(-L, L + 1):
        yb = eb[a0 + s + lag:a1 + s + lag]
        res.append((float(np.corrcoef(x, yb)[0, 1]), lag))
    res.sort(reverse=True)
    c, lag = res[0]
    zero = [r for r in res if r[1] == 0][0][0]
    return {'lag_ms': lag / fps * 1000, 'corr': c, 'corr_at_0': zero}


VOWELS = {'i': (280, 2250), 'e': (400, 2000), 'E': (550, 1770), 'a': (750, 1250), 'o': (450, 850), 'u': (320, 800),
          'A': (650, 1050), '@': (500, 1400)}


def formants(path, grid, bars, steps_per_beat=2, order=12, fmax=5000, max_bars=8):
    """LPC formant tracks (F1, F2, F3) per step with a nearest-vowel guess. Voiced frames only."""
    y = load(path)
    b0, b1, _ = _range(y, ASR, grid, bars, max_bars)
    sr2 = 11025
    y2 = librosa.resample(y, orig_sr=ASR, target_sr=sr2)
    step_t = grid.spb / steps_per_beat
    lines = [f"formants per 1/{steps_per_beat} beat: F1/F2/F3 Hz + nearest vowel (i e E a A o u @); '-' = unvoiced/silent",
             "vowel map: i beet, e bait, E bet, a father, A law, o boat, u boot, @ schwa"]
    out = []
    lvl_ref = 20 * np.log10(np.max(np.abs(y2[int(max(grid.bar_time(b0), 0) * sr2):int(grid.bar_time(b1) * sr2)])) + 1e-9)
    for b in range(b0, b1):
        cells = []
        for k in range(grid.bpb * steps_per_beat):
            t0 = grid.bar_time(b) + k * step_t
            seg = y2[int(max(t0, 0) * sr2):int((t0 + step_t) * sr2)]
            if len(seg) < 256 or 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-9) < lvl_ref - 35:
                cells.append('-')
                continue
            x = seg * np.hamming(len(seg))
            x = np.append(x[0], x[1:] - 0.63 * x[:-1])  # pre-emphasis
            try:
                a = librosa.lpc(x, order=order)
            except Exception:
                cells.append('-')
                continue
            roots = [r for r in np.roots(a) if np.imag(r) > 0]
            fr = sorted(np.angle(roots) * sr2 / (2 * np.pi))
            bw = [-0.5 * sr2 / np.pi * np.log(abs(r)) for r in roots]
            fs = [f for f, w in sorted(zip(np.angle(roots) * sr2 / (2 * np.pi), bw)) if 150 < f < fmax and w < 400]
            if len(fs) < 2:
                cells.append('-')
                continue
            f1, f2 = fs[0], fs[1]
            f3 = fs[2] if len(fs) > 2 else 0
            v = min(VOWELS, key=lambda k2: (np.log(f1 / VOWELS[k2][0])) ** 2 + (np.log(f2 / VOWELS[k2][1])) ** 2)
            cells.append(f"{v}{f1:.0f}/{f2:.0f}")
            out.append({'bar': b, 'step': k, 'f1': f1, 'f2': f2, 'f3': f3, 'vowel': v})
        lines.append(f"bar {b:>3}: " + ' '.join(f"{c:<10}" for c in cells))
    return out, '\n'.join(lines)


EYE_BOX = (0.08, 0.11, 0.9, 0.86)        # the plot box in figure fractions (left, bottom, width, height): fixed, so
                                         # two pictures of one window line up pixel for pixel and a page maps clicks
EYE_BANDS = ((1000, 'body'), (4000, 'vowel bands'), (8000, 'hiss'), (16001, 'air'))   # the names both sides use


def spectrogram_png(path, out_png, t0=0.0, t1=None, grid=None, n_mels=128, f_lo=None, f_hi=None, words=None,
                    target=None, ruler=False, box=None):
    """A spectrogram PNG of [t0, t1] s. By default a mel view with bar lines (grid). With any of f_lo, f_hi,
    words, ruler or box: the picture for eyes (ledger:M165 step 1, vox's eyeword.crop_png2): a linear STFT on a
    fixed plot box (EYE_BOX), the band f_lo..f_hi Hz, a ruler in ms from t0, each word's start drawn and named
    (words: [{'w', 't0', 't1'}] in s; target: the one word whose bounds are bright), and out_png + '.json', the map
    from a pixel to (s, Hz) that a page or eye_address reads."""
    if f_lo is not None or f_hi is not None or words or ruler or box:
        return _eye_png(path, out_png, t0, t1, f_lo or 0.0, f_hi or 16000.0, words or [], target, ruler,
                        tuple(box or EYE_BOX))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    y = load(path)
    t1 = t1 or len(y) / ASR
    seg = y[int(t0 * ASR):int(t1 * ASR)]
    M = librosa.power_to_db(librosa.feature.melspectrogram(y=seg, sr=ASR, n_mels=n_mels, hop_length=256), ref=np.max)
    fig, ax = plt.subplots(figsize=(14, 5), dpi=90)
    ax.imshow(M, origin='lower', aspect='auto', extent=[t0, t1, 0, n_mels], cmap='magma', vmin=-80, vmax=0)
    mel_f = librosa.mel_frequencies(n_mels=n_mels, fmax=ASR / 2)
    ticks = [50, 100, 200, 500, 1000, 2000, 5000, 10000]
    ax.set_yticks([np.searchsorted(mel_f, f) for f in ticks])
    ax.set_yticklabels([f"{f}" for f in ticks])
    if grid:
        b = int(np.ceil(grid.time_to_bar(t0)))
        while grid.bar_time(b) < t1:
            ax.axvline(grid.bar_time(b), color='cyan', lw=0.5, alpha=0.6)
            ax.text(grid.bar_time(b), n_mels - 4, str(b), color='cyan', fontsize=7)
            b += 1
    ax.set_xlabel('s')
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
    return out_png


EYE_PX_MAX = 1600                        # no eye picture is taller or wider than this (a page and an agent read it whole)
ZOOM_VIEWS = (('time-sharp', 256, 32), ('frequency-sharp', 2048, 128))   # (name, window, hop) in samples at 44.1 kHz,
# scaled to the file's rate: pulses, onsets and stripes; harmonics and formant ridges (vox eye_zoom, ledger:M193)
MARK_COLORS = {'brighter': '#4fc3f7', 'darker': '#b2ff59'}


def _mono(path):
    import soundfile as sf
    y, sr = sf.read(str(path), dtype='float64')
    return (y.mean(axis=1) if y.ndim > 1 else y), sr


def _eye_png(path, out_png, t0, t1, f_lo, f_hi, words, target, ruler, box, size=(6.4, 4.2), dpi=100):
    y, sr = _mono(path)
    return _eye_stack(out_png, [dict(y=y, sr=sr, t0=t0, t1=t1, words=words, target=target, name=str(path))],
                      f_lo, f_hi, ruler, box, size, dpi)


def zoom_png(path, out_png, t0=0.0, t1=None, f_lo=0.0, f_hi=16000.0, words=None, target=None, ruler=True,
             views=ZOOM_VIEWS):
    """One time x frequency box of a sound drawn at two resolutions, stacked: the time-sharp view (256-point
    window, 32-sample hop at 44.1 kHz: pulses, onsets, a boundary 50 ms early) above the frequency-sharp one
    (2048-point: harmonics, formant ridges). Each panel is an eye picture (spectrogram_png's plot box, words, ruler),
    so a box has the same pixel box in both panels (one panel lower) and a spot the same eye_address; out_png +
    '.json' lists the panels. Windows scale with the file's rate, so a view keeps its ms and Hz resolution."""
    y, sr = _mono(path)
    k = sr / 44100.0
    panels = [dict(y=y, sr=sr, t0=t0, t1=t1, words=words or [], target=target, name=str(path), label=name,
                   nfft=max(16, int(round(n * k))), hop=max(1, int(round(h * k)))) for name, n, h in views]
    return _eye_stack(out_png, panels, f_lo, f_hi, ruler, EYE_BOX)


def _eye_stack(out_png, panels, f_lo, f_hi, ruler, box, size=(6.4, 4.2), dpi=100):
    """Eye pictures stacked top to bottom in one PNG, each on its own copy of the plot box `box` (so every panel has
    the pixel geometry of a lone eye picture, one panel height lower). A panel: y, sr, t0, t1 (s), words, target,
    name, and optional nfft/hop (samples; default 1024/64), label (drawn in the corner), marks [{t0, t1, f_lo, f_hi,
    sign, label}] (outlined boxes in s and Hz of that panel's file). Writes out_png + '.json': the single-picture
    keys of the first panel, plus 'panels' when there are several."""
    import json
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.mlab as mlab
    from matplotlib.patches import Rectangle
    if not 0 <= f_lo < f_hi:
        raise ValueError(f"f_lo {f_lo} Hz must be under f_hi {f_hi} Hz")
    n = len(panels)
    if n * size[1] * dpi > EYE_PX_MAX:
        raise ValueError(f"{n} panels make a picture over {EYE_PX_MAX} px tall; draw at most "
                         f"{int(EYE_PX_MAX // (size[1] * dpi))}")
    fig = plt.figure(figsize=(size[0], size[1] * n), dpi=dpi)
    views = []
    for i, pn in enumerate(panels):
        y, sr, t0 = pn['y'], pn['sr'], pn['t0']
        t1 = len(y) / sr if pn.get('t1') is None else pn['t1']
        nfft, hop = pn.get('nfft', 1024), pn.get('hop', 64)
        seg = y[max(0, int((t0 - 0.03) * sr)):int((t1 + 0.03) * sr)]
        S, f, t = mlab.specgram(seg, NFFT=nfft, Fs=sr, noverlap=nfft - hop)
        t = t + max(0.0, t0 - 0.03)
        m = (t >= t0) & (t <= t1)
        if not m.any():
            plt.close(fig)
            raise ValueError(f"window {t0:.3f}-{t1:.3f} s holds no frame of {pn['name']} ({len(y) / sr:.2f} s)")
        d = t1 - t0
        pbox = [box[0], (n - 1 - i + box[1]) / n, box[2], box[3] / n]
        ax = fig.add_axes(pbox)
        ax.imshow(10 * np.log10(S[:, m] + 1e-20), origin='lower', aspect='auto', cmap='magma', vmin=-150, vmax=-40,
                  extent=[0, d * 1000, 0, f[-1]], interpolation='nearest')
        ax.set_ylim(f_lo, f_hi)
        ax.set_xlim(0, d * 1000)
        ticks = [x for x in (0, 2000, 4000, 8000, 12000, 16000, 20000) if f_lo <= x <= f_hi]
        ax.set_yticks(ticks)
        ax.set_yticklabels([f"{x // 1000}k" if x else '0' for x in ticks], fontsize=7)
        if ruler:
            step = 50 if d < 0.6 else 100 if d < 2 else 500 if d < 10 else 1000
            ax.set_xticks(np.arange(0, d * 1000 + 1, step))
            ax.tick_params(axis='x', labelsize=7)
            ax.set_xlabel('ms', fontsize=7, labelpad=1)
        else:
            ax.set_xticks([])
        top = f_lo + (f_hi - f_lo) * 15300 / 16000
        words, target = pn.get('words') or [], pn.get('target')
        for w in words:
            if w['t1'] < t0 or w['t0'] > t1:
                continue
            hot = bool(target) and w['w'] == target['w'] and abs(w['t0'] - target['t0']) < 1e-6
            for e in (w['t0'], w['t1']) if hot else (w['t0'],):
                if t0 <= e <= t1:
                    ax.axvline((e - t0) * 1000, color='w', lw=1.2 if hot else 0.6, alpha=0.9 if hot else 0.5)
            ax.text(max(0.0, (w['t0'] - t0) * 1000) + 3, top, w['w'], color='w', fontsize=10 if hot else 8,
                    fontweight='bold' if hot else 'normal', va='top')
        for mk in pn.get('marks') or []:
            c = MARK_COLORS.get(mk.get('sign'), 'w')
            x0, x1 = (max(mk['t0'], t0) - t0) * 1000, (min(mk['t1'], t1) - t0) * 1000
            ax.add_patch(Rectangle((x0, mk['f_lo']), max(x1 - x0, 1.0), max(mk['f_hi'] - mk['f_lo'], 1.0), fill=False,
                                   edgecolor=c, lw=1.4))
            if mk.get('label'):
                ax.text(x0 + 2, min(mk['f_hi'], f_hi), mk['label'], color=c, fontsize=8, fontweight='bold', va='bottom')
        if pn.get('label'):
            ax.text(0.01, 0.02, pn['label'], transform=ax.transAxes, color='w', fontsize=7, va='bottom')
        views.append({'axes': pbox, 't0': round(t0, 4), 't1': round(t1, 4), 'nfft': nfft, 'hop': hop,
                      'label': pn.get('label'), 'words': [{'w': w['w'], 't0': w['t0'], 't1': w.get('t1')}
                                                           for w in words if w['t1'] >= t0 and w['t0'] <= t1]})
    fig.savefig(out_png)
    plt.close(fig)
    v0 = views[0]
    meta = {'png': os.path.basename(str(out_png)), 'axes': v0['axes'] if n > 1 else list(box),
            'px': [int(size[0] * dpi), int(size[1] * dpi * n)], 't0': v0['t0'], 't1': v0['t1'], 'f_lo': f_lo,
            'f_hi': f_hi, 'words': v0['words']}
    if n > 1:
        meta['panels'] = views
    with open(str(out_png) + '.json', 'w', encoding='utf8') as fh:
        json.dump(meta, fh)
    return out_png


def _eye_panel_at(view, fx, fy):
    """(panel, u, v): the panel of an eye picture under a spot (the view itself for a lone picture) and the spot as
    fractions of its plot box, or None off every plot box."""
    for pn in view.get('panels') or [view]:
        l, b, w, h = pn['axes']
        u, v = (fx - l) / w, (1 - fy - b) / h
        if 0 <= u <= 1 and 0 <= v <= 1:
            return pn, u, v
    return None


def eye_point(view, fx, fy):
    """A spot on an eye picture as fractions of the image (fx from the left, fy from the top, as a page reads a
    click) -> (s from the start of the file, Hz), or None off the plot box. view: the picture's .json (a stacked
    picture from zoom_png: the panel under the spot)."""
    hit = _eye_panel_at(view, fx, fy)
    if hit is None:
        return None
    pn, u, v = hit
    return pn['t0'] + u * (pn['t1'] - pn['t0']), view['f_lo'] + v * (view['f_hi'] - view['f_lo'])


def eye_address(view, fx, fy, label=''):
    """The shared address of a spot, as the person reads it off a screenshot and the agent finds it again:
    '<label> 0.19 s 7.9 kHz "this" hiss' (time from the window start; the word under it or 'gap'; the band name).
    On a stacked picture the same moment gives the same address in every panel."""
    hit = _eye_panel_at(view, fx, fy)
    if hit is None:
        return None
    pn = hit[0]
    t, f = eye_point(view, fx, fy)
    w = [x for x in pn.get('words') or [] if x['t0'] <= t]
    word = w[-1]['w'] if w and (w[-1].get('t1') is None or t <= w[-1]['t1'] + 0.02) else 'gap'
    band = next(n for top, n in EYE_BANDS if f < top) if f < EYE_BANDS[-1][0] else EYE_BANDS[-1][1]
    return (f"{label} " if label else '') + f'{t - pn["t0"]:.2f} s {f / 1000:.1f} kHz "{word}" {band}'
