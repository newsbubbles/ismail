"""Measurement ops: tuning and retune, swing, kit discovery, per-section loudness, fader levels from reference
stems (registered into api.OPS on import). Each exists because guessing the number cost a draft."""
import os

import numpy as np
import soundfile as sf

from . import analysis as A
from . import audiofile
from .api import op, OpError, _load
from .api_cmp import STEMS, ref_stems


def _segment(P, src, bars=None):
    """(stereo float array [2, n], sr) of a source, cut to bars [a, b] (inclusive) on the project grid."""
    path, g = P.source(src, bars)
    y, sr = sf.read(path, always_2d=True, dtype='float32')
    y = y.T if y.shape[1] > 1 else np.vstack([y.T, y.T])
    if bars:
        a, b = max(int(g.bar_time(bars[0]) * sr), 0), max(int(g.bar_time(bars[1] + 1) * sr), 0)
        y = y[:, a:b]
    return y, sr


def _db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-20)


@op(mutates=True)
def ref_retune(project: str, cents: float = None) -> str:
    """Retune the reference (and its stems) onto A440 for analysis: measures the offset (or takes `cents`), writes
    pitch-shifted copies next to the originals and points the project at them. A record tens of cents off (a sped-up
    sample) makes every note read as a pair of semitones in the pitch, chord, key and transcription tools; timing is
    unchanged. analyze_grid prints the offset and says when to run this. The originals stay in reference.original."""
    import librosa
    P = _load(project)
    ref = P.d.get('reference') or {}
    if not ref.get('file'):
        raise OpError("project has no reference; project_set(reference='path/to.wav') first")
    orig = ref.get('original') or {'file': ref['file'], 'stems_dir': ref.get('stems_dir')}
    if cents is None:
        cents, r = A.tuning(A.load(orig['file']))
        if r < 0.1:
            raise OpError(f"tuning reads {cents:+.0f} cents but with little pitch content (r {r:.2f}); pass cents=... "
                          f"yourself if you know it (from analyze_pitches on a held note)")
    if abs(cents) < 3:
        return f"reference is {cents:+.1f} cents from A440; nothing to retune"
    shift = -cents / 100.0
    tag = f"_retuned{-cents:+.0f}c".replace('+', 'p').replace('-', 'm')

    def retune(src, dst):
        y, sr = sf.read(src, always_2d=True, dtype='float32')
        out = np.stack([librosa.effects.pitch_shift(y[:, c], sr=sr, n_steps=shift) for c in range(y.shape[1])], 1)
        sf.write(dst, out, sr)
    base, _ = os.path.splitext(orig['file'])
    newf = base + tag + '.wav'
    retune(orig['file'], newf)
    new = {'file': newf, 'original': orig, 'retuned_cents': round(-cents, 1)}
    done = [os.path.basename(newf)]
    if orig.get('stems_dir') and os.path.isdir(orig['stems_dir']):
        nd = orig['stems_dir'].rstrip('/\\') + tag
        os.makedirs(nd, exist_ok=True)
        for s in STEMS:
            p = audiofile.find(os.path.join(orig['stems_dir'], s + '.wav'))
            if os.path.exists(p):
                retune(p, os.path.join(nd, s + '.wav'))
                done.append(f"{os.path.basename(nd)}/{s}.wav")
        new['stems_dir'] = nd
    P.d['reference'] = dict(ref, **new)
    P.save()
    return (f"reference was {cents:+.1f} cents from A440; shifted {-cents:+.1f} cents -> {', '.join(done)}. "
            f"ref and ref:<stem> now read the retuned copies (originals kept in reference.original). "
            f"Your song plays at A440; if the reference must be matched by ear against the original, detune the "
            f"master afterwards instead.")


@op()
def analyze_swing(project: str, source: str = None) -> str:
    """How late the swung hi-hats land, in beats and ms (16ths: the 'a' against the 'and'; 8ths: the 'and' against
    the beat). Default source: the reference drum stem if there is one, else the reference/render. Use it on a
    reference before writing swung notes, and on 'track:<hats>' to check yours. Straight = 0."""
    P = _load(project)
    if source is None:
        ref = P.d.get('reference') or {}
        source = 'ref:drums' if ref.get('stems_dir') and os.path.exists(
            audiofile.find(os.path.join(ref['stems_dir'], 'drums.wav'))) else P.auto_source(None)
    path, g = P.source(source)
    data, txt = A.swing(path, g.bpm, g.offset)
    if source in ('ref', 'render'):
        txt += ("\nNOTE this is a full mix: other highs bury the hats and can read straight. Measure a drum stem "
                "(separate(source='ref') then source='ref:drums') or 'track:<hats>'.")
    return txt


def _nmf(V, k, iters=300, seed=0):
    """KL-divergence NMF by multiplicative updates: V (freq x time) ~ W @ H."""
    rng = np.random.default_rng(seed)
    W = rng.random((V.shape[0], k)) + 1e-3
    H = rng.random((k, V.shape[1])) + 1e-3
    for _ in range(iters):
        H *= (W.T @ (V / (W @ H + 1e-12))) / (W.sum(0)[:, None] + 1e-12)
        W *= ((V / (W @ H + 1e-12)) @ H.T) / (H.sum(1)[None, :] + 1e-12)
    return W, H


@op()
def analyze_kit(project: str, source: str = 'ref:drums', bars: list = None, k: int = 6, cycle: int = 2) -> str:
    """Discover the pieces of a drum kit: NMF splits the drum source into k components (each a spectrum + when it
    hits), prints each one's pitch region, share of energy and hit pattern per 16th folded over a `cycle`-bar loop,
    and writes each component's audio to <project>/analysis/kit/comp_<n>.wav (use it as a sound_import /
    instrument_fit target, never in the render). analyze_drums shows 3 band lanes and hides pieces: on a G-funk
    record this found 6 (punch kick apart from the 808, ghost snares). Raise k until new components are only
    fragments (< 2% energy, or a copy of another's pattern)."""
    import librosa
    P = _load(project)
    path, g = P.source(source, bars)
    sr = 44100
    y = A.load(path, sr=sr)
    b0 = bars[0] if bars else g.first_bar()
    t0 = max(g.bar_time(b0), 0.0)
    t1 = g.bar_time(bars[1] + 1) if bars else len(y) / sr
    y = y[int(t0 * sr):int(t1 * sr)]
    if len(y) < sr:
        raise OpError("less than a second of audio in that range; check bars and the project grid")
    hop, nfft = 256, 2048
    S = librosa.stft(y, n_fft=nfft, hop_length=hop)
    M = np.abs(S)
    W, H = _nmf(M, k)
    freqs = librosa.fft_frequencies(sr=sr, n_fft=nfft)
    t = np.arange(M.shape[1]) * hop / sr
    step = 60 / g.bpm / 4
    nsteps = cycle * g.bpb * 4
    steps_total = int(len(y) / sr / step)
    reps = steps_total // nsteps
    if reps < 2:
        raise OpError(f"only {reps} repetition(s) of a {cycle}-bar cycle in range; give more bars or a shorter cycle")
    od = os.path.join(P.root, 'analysis', 'kit')
    os.makedirs(od, exist_ok=True)
    rows = []
    WH = W @ H + 1e-9
    for c in range(k):
        w = W[:, c] / W[:, c].sum()
        cen = float((w * freqs).sum())
        act = np.zeros(steps_total)
        for s in range(steps_total):
            m = (t >= s * step - 0.25 * step) & (t < s * step + 0.5 * step)
            act[s] = H[c, m].max() if m.any() else 0.0
        rise = np.maximum(act - 0.6 * np.roll(act, 1), 0)[:reps * nsteps].reshape(reps, nsteps)
        mean = rise.mean(0)
        cons = (rise > 0.35 * rise.max()).mean(0)
        energy = float((H[c] ** 2).sum() * (W[:, c] ** 2).sum())
        fn = os.path.join(od, f'comp_{c}.wav')
        sf.write(fn, librosa.istft(S * (np.outer(W[:, c], H[c]) / WH), hop_length=hop, length=len(y)), sr)
        rows.append((energy, c, cen, float(freqs[np.argmax(W[:, c])]), mean / (mean.max() + 1e-12), cons))
    tot = sum(r[0] for r in rows)

    def lane(v, dot):
        return ''.join((dot if x < 0.1 else str(min(9, int(x * 10)))) + ('|' if (i + 1) % 16 == 0 else
                       ' ' if (i + 1) % 4 == 0 else '') for i, x in enumerate(v))

    def guess(cen, pk):
        if pk < 150:
            return 'kick / 808' if cen < 1500 else 'kick with a click (punch kick?)'
        if pk < 400 and cen < 5000:
            return 'snare or tom body'
        if cen < 6500:
            return 'snare, clap or rim'
        return 'hat, shaker or cymbal'
    L = [f"analyze_kit {os.path.basename(path)} bars {b0}-{bars[1] if bars else '...'}: k={k} components, pattern folded "
         f"on a {cycle}-bar cycle ({reps} repetitions). Row 1 = mean hit strength per 16th (9 strongest, '.' none), "
         f"row 2 = share of repetitions with a clear hit there (9 = 90%+). Audio: analysis/kit/comp_<n>.wav"]
    for energy, c, cen, pk, mean, cons in sorted(rows, reverse=True):
        share = 100 * energy / (tot + 1e-20)
        L.append(f"comp {c}  {share:5.1f}% energy  centroid {cen:6.0f} Hz  peak {pk:6.0f} Hz  ~ {guess(cen, pk)}"
                 + ('  (fragment: lower k)' if share < 2 else ''))
        L.append('   ' + lane(mean, '.'))
        L.append('   ' + lane(np.where(cons < 0.3, 0, cons), ' '))
    return '\n'.join(L)


@op()
def analyze_contour(project: str, source: str = 'ref:other', bars: list = None, hop_ms: float = 10.0,
                    fmin: str = 'C1', fmax: str = 'C7', start: int = 0, max_rows: int = 200) -> str:
    """Pitch contour of a monophonic line: one row per hop_ms frame with time, bar, beat, f0 in Hz, the pitch as
    a float MIDI number, the nearest note and its cents offset, and pYIN's voicing probability ('-' = unvoiced).
    For slides, vibrato, ornaments and intonation (maqam steps a quarter tone apart) inside or between notes; for a
    pitch per note use analyze_melody(cents=True). Output is one window of at most max_rows frames (cap 1000) from
    frame `start`; the last line names the start of the next window. bars up to 64; only the window is analysed.
    Narrow fmin/fmax around the line when a backing or an octave error pulls the reading."""
    P = _load(project)
    try:
        return A.pitch_contour(*P.source(source, bars), bars, hop_ms, fmin, fmax, start, max_rows)[1]
    except A.AnalysisError as e:
        raise OpError(str(e))


@op()
def analyze_sections(project: str, source: str = 'render', sections: dict = None, block: int = 4) -> str:
    """Loudness per section: rms, peak, loudest and quietest 400 ms, and the song's dynamic range. sections =
    {"intro": [1, 4], "climax": [25, 28], ...} (bars inclusive); omitted = blocks of `block` bars. Flags the
    section right before the loudest when it is 2 dB or more quieter on average but peaks within 3 dB of it (a build
    as loud as its climax: the climax will not arrive, and this only showed up here) and sections so quiet they vanish at normal volume."""
    P = _load(project)
    y, sr = _segment(P, source)
    _, g = P.source(source)
    total = P.d.get('length_bars') or int((y.shape[1] / sr - g.offset) / (g.bpb * g.spb)) + 1
    if sections:
        secs = [(n, int(a), int(b)) for n, (a, b) in sections.items()]
    else:
        secs = [(f"{a}-{min(a + block - 1, total)}", a, min(a + block - 1, total)) for a in range(1, total + 1, block)]
    w = int(0.4 * sr)
    rows = []
    for n, a, b in secs:
        s = y[:, max(int(g.bar_time(a) * sr), 0):max(int(g.bar_time(b + 1) * sr), 0)]
        if s.shape[1] < w:
            continue
        m = (s ** 2).mean(0)
        win = 10 * np.log10(np.add.reduceat(m, np.arange(0, len(m) - w + 1, w))[:len(m) // w] / w + 1e-20)
        rows.append((n, a, b, _db(s), 20 * np.log10(np.abs(s).max() + 1e-12), win.max(), win.min()))
    if not rows:
        raise OpError("no section had 400 ms of audio; check sections against the render length")
    top = max(rows, key=lambda r: r[3])
    L = [f"sections of {source} (dB; loudest/quietest = 400 ms windows):",
         f"  {'section':<12} {'bars':>7} {'rms':>6} {'peak':>6} {'loudest':>8} {'quietest':>9}"]
    for n, a, b, rms, pk, hi, lo in rows:
        L.append(f"  {n:<12} {f'{a}-{b}':>7} {rms:6.1f} {pk:6.1f} {hi:8.1f} {lo:9.1f}")
    live = [r for r in rows if r[3] > -70]
    low = min(live, key=lambda r: r[3])
    L.append(f"dynamic range: {top[3] - low[3]:.1f} dB between the loudest section ({top[0]}, rms {top[3]:.1f}) and the "
             f"quietest with sound ({low[0]}, {low[3]:.1f})")
    i = rows.index(top)
    # a build: 2 dB or more under the loudest section on average, yet its loudest moment is as loud. Two loud
    # sections in a row (a drop that goes on) are one section and are not flagged
    if i > 0 and rows[i - 1][5] > top[5] - 3 and rows[i - 1][3] < top[3] - 2:
        p = rows[i - 1]
        L.append(f"WARNING {p[0]} (bars {p[1]}-{p[2]}) peaks {top[5] - p[5]:.1f} dB under {top[0]}, the loudest: the "
                 f"arrival will not land. Keep what comes before the climax 3 dB or more under it (faders, automation "
                 f"or velocities there), not by raising the climax into the limiter.")
    for r in live:
        if r is not rows[-1] and r[3] < top[3] - 35:
            L.append(f"WARNING {r[0]} sits {top[3] - r[3]:.0f} dB under the loudest section: inaudible at normal "
                     f"volume unless it is a deliberate near-silence")
    return '\n'.join(L)


@op(mutates=True)
def levels_from_ref(project: str, bars: list = None, apply: bool = False) -> str:
    """Set faders from the reference's stem balance instead of by feel: for every stem in the stem map, compares
    its level against the sum of the mapped stems in the reference (demucs stems) and in your render (track stems),
    and proposes a fader change per track (the same change for every track of one stem). apply=True writes them.
    Needs stem_map_set, separate(source='ref') and render(stems=True) over the same bars. Re-render and re-check
    after applying: compressors and the limiter move levels again."""
    P = _load(project)
    sm = P.d.get('stem_map') or {}
    if not sm:
        raise OpError("no stem map; stem_map_set({track: stem}) first (drums, bass, other, vocals)")
    have = ref_stems(P)
    groups = {}
    for t, s in sm.items():
        if t in P.d['tracks']:
            groups.setdefault(s, []).append(t)
    missing = [s for s in groups if s not in have]
    if missing:
        raise OpError(f"reference stems {missing} not found; run separate(source='ref') first")
    ref_lv, own_lv = {}, {}
    ref_sum = own_sum = None
    for s, trs in groups.items():
        r, _ = _segment(P, f'ref:{s}', bars)
        mine = None
        for t in trs:
            x, _ = _segment(P, f'track:{t}', bars)
            mine =x if mine is None else mine[:, :x.shape[1]] + x[:, :mine.shape[1]]
        ref_lv[s], own_lv[s] = _db(r), _db(mine)
        ref_sum = r if ref_sum is None else ref_sum[:, :r.shape[1]] + r[:, :ref_sum.shape[1]]
        own_sum = mine if own_sum is None else own_sum[:, :mine.shape[1]] + mine[:, :own_sum.shape[1]]
    rs, os_ = _db(ref_sum), _db(own_sum)
    L = [f"stem balance (dB against the sum of mapped stems{', bars ' + str(bars) if bars else ''}):",
         f"  {'stem':<7} {'reference':>9} {'yours':>7} {'change':>7}  tracks"]
    changes = {}
    for s, trs in sorted(groups.items()):
        d = (ref_lv[s] - rs) - (own_lv[s] - os_)
        if ref_lv[s] < -70:
            L.append(f"  {s:<7} {'silent':>9} {own_lv[s] - os_:7.1f} {'-':>7}  {', '.join(trs)} (reference stem empty: "
                     f"its content sits in another stem; not changed)")
            continue
        L.append(f"  {s:<7} {ref_lv[s] - rs:9.1f} {own_lv[s] - os_:7.1f} {d:+7.1f}  {', '.join(trs)}")
        for t in trs:
            changes[t] = d
    unmapped = [t for t in P.d['tracks'] if t not in sm]
    if unmapped:
        L.append(f"  unmapped tracks (not compared, not changed): {', '.join(unmapped)}")
    L.append("  demucs stems carry bleed ('other' catches synths, pads and leftovers): trust drums/bass/vocals first")
    if apply:
        for t, d in changes.items():
            tr = P.track(t)
            tr['volume_db'] = round(float(tr.get('volume_db', 0.0) + d), 1)
        P.save()
        L.append("applied: " + ', '.join(f"{t} {P.track(t)['volume_db']:+.1f} dB" for t in changes) +
                 ". Re-render and run levels_from_ref again; stop when every change is within 1 dB.")
    else:
        L.append("apply=True writes these fader changes.")
    return '\n'.join(L)
