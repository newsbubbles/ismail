"""Measurement ops: tuning and retune, every part against the tuning grid, swing, kit discovery, per-section
loudness, fader levels from reference stems (registered into api.OPS on import). Each exists because guessing the
number cost a draft."""
import json
import os
import re

import numpy as np
import soundfile as sf

from . import analysis as A
from . import audiofile
from .api import op, OpError, _load
from .api_cmp import STEMS, ref_stems
from .notation import midi_to_name


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
    fragments (< 2% energy, or a copy of another's pattern). Reach for it when a snare, kick or hat sounds dated,
    generic or not the genre's: the reference's own pieces, each as a clean target."""
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
    path, g = P.source(source)
    total = P.d.get('length_bars') or int((y.shape[1] / sr - g.offset) / (g.bpb * g.spb)) + 1
    win = P.render_window(path)            # a bars=[a, b] render: its file starts at song bar a (windows.json)
    w0, w1 = (int(win['bars'][0]), int(win['bars'][1])) if win else (1, total)
    if sections:
        secs = [(n, int(a), int(b)) for n, (a, b) in sections.items()]
        out = [f"{n} [{a}, {b}]" for n, a, b in secs if a < w0 or b > w1]
        if win and out:
            raise OpError(f"{source!r} is a window render: its file starts at song bar {w0} and holds bars {w0}-{w1} "
                          f"(renders/windows.json). Sections take song bars, so {', '.join(out[:3])} "
                          f"{'falls' if len(out) == 1 else 'fall'} outside it: bars 1-4 of this file are "
                          f"[{w0}, {w0 + 3}]. Shift the sections by {w0 - 1} bars, or render the full song")
    else:
        secs = [(f"{a}-{min(a + block - 1, w1)}", a, min(a + block - 1, w1)) for a in range(w0, w1 + 1, block)]
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
        raise OpError("no section had 400 ms of audio; check sections against the render length"
                      + (f" ({source!r} holds song bars {w0}-{w1})" if win else ''))
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
    after applying: compressors and the limiter move levels again. Reach for it when a part sounds too loud or too quiet,
    or render(stems=True)'s balance lines name a stem and a stretch (bars=[a, b] for that stretch)."""
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


# ------------------------------------------------------------------ every part against the tuning grid (ledger:M231)

_SCALE_WORDS = {'': 'major', 'maj': 'major', 'm': 'minor', 'min': 'minor', 'aeolian': 'minor', 'ionian': 'major'}


def _song_key(P, key):
    """(label, pitch classes) of the key to check against: `key` ('A minor', 'Am', 'F# major', 'D dorian'), else the
    project's ('key' in project.json, or the sketch's in sketch.json); (None, None) when there is none."""
    from . import tune
    if not key:
        key = P.d.get('key')
    if not key:
        try:
            with open(os.path.join(P.root, 'sketch.json'), encoding='utf8') as f:
                key = json.load(f).get('key')
        except (OSError, ValueError):
            key = None
    if not key:
        return None, None
    m = re.fullmatch(r'\s*([A-Ga-g][#b]?)\s*([A-Za-z_ ]*?)\s*', str(key))
    if not m:
        raise OpError(f"key {key!r}: write it like 'A minor', 'Am', 'F# major' or 'D dorian'")
    scale = m.group(2).strip().lower().replace(' ', '_')
    scale = _SCALE_WORDS.get(scale, scale)
    try:
        return f"{m.group(1)[0].upper()}{m.group(1)[1:]} {scale}", set(tune.scale_classes(m.group(1), scale))
    except tune.TuneError:
        raise OpError(f"key {key!r}: the scale must be one of {', '.join(n for n in tune.SCALES if n != 'chromatic')}")


def _tuning_parts(P, source, stems):
    """[(name, source string)] of the parts to read, and a note on their age (or '')."""
    note = ''
    if source == 'render':
        rd = os.path.join(P.root, 'renders')
        latest = audiofile.find(os.path.join(rd, 'latest.wav'))
        if not os.path.exists(latest):
            raise OpError("nothing rendered yet: render(stems=True) first (bars=[a, b] is enough to check a part), "
                          "then call analyze_tuning again")
        have = {n: p for n, p in audiofile.listing(os.path.join(rd, 'stems')).items() if n in P.d['tracks']}
        if not have:
            raise OpError("the last render wrote no track stems: render(stems=True) (bars=[a, b] is enough), then call "
                          "again. To read the whole mix as one part pass source='render:mix' (less precise: every "
                          "part's notes at once)")
        if any(os.path.getmtime(p) < os.path.getmtime(latest) - 60 for p in have.values()):
            note = ("NOTE some stems are older than renders/latest.wav (a render without stems=True came after): "
                    "render(stems=True) to read the parts as they are now")
        parts = [(n, f'track:{n}') for n in have]
    elif source == 'ref':
        have = {s: p for s, p in ref_stems(P).items() if s != 'mix'}
        if not have:
            raise OpError("the reference has no stems yet: separate(source='ref') first (or project_set("
                          "reference_stems=<folder>)), then call again; source='ref:mix' reads the whole record as "
                          "one part")
        parts = [(s, f'ref:{s}') for s in have]
    elif source in ('render:mix', 'ref:mix'):
        parts = [('mix', source.split(':')[0])]
    else:
        parts = [(source.split(':', 1)[1] if source.startswith(('track:', 'ref:', 'sound:')) else
                  os.path.splitext(os.path.basename(source))[0], source)]
    if stems:
        names = [n for n, _ in parts]
        bad = [s for s in stems if s not in names]
        if bad:
            raise OpError(f"no part named {bad} in {source!r}; parts: {names}")
        parts = [p for p in parts if p[0] in stems]
    return parts, note


def _grid_cents(P, source, tuning_cents):
    """(cents of the grid from A440, where that came from). Renders play at A440; a reference that ref_retune has not
    moved is read on its own grid, measured as analyze_grid does."""
    if tuning_cents is not None:
        return float(tuning_cents), f"tuning_cents={tuning_cents:+g} as given"
    ref = P.d.get('reference') or {}
    if source.startswith('ref') and ref.get('file'):
        if 'retuned_cents' in ref:
            return 0.0, f"the reference was moved onto A440 by ref_retune ({ref['retuned_cents']:+g} c)"
        cents, r = A.tuning(A.load(ref['file']))
        if r >= 0.1 and abs(cents) >= 3:
            return cents, f"the reference's own tuning, {cents:+.0f} c from A440 (ref_retune() moves it onto A440)"
    return 0.0, "ismail plays at A440"


def _cents_off(m, gc):
    """(nearest grid note as a MIDI number, cents from it) for a float MIDI pitch on a grid gc cents from A440."""
    x = m - gc / 100.0
    k = int(round(x))
    return k, 100.0 * (x - k)


def _spread_stats(offs):
    """(median, spread) of cents offsets that wrap at +-50: read around their circular mean, so a part sitting at a
    quarter tone (+49 and -49 on either side) is one part at ~50, not two halves averaging 0. Spread = the middle
    half (75th - 25th percentile)."""
    o = np.asarray(offs)
    c = 100 * np.angle(np.exp(2j * np.pi * o / 100).mean()) / (2 * np.pi)
    r = (o - c + 50) % 100 - 50 + c
    med = (np.median(r) + 50) % 100 - 50
    return float(med), float(np.percentile(r, 75) - np.percentile(r, 25))


def _clusters(rows):
    """Readings grouped into the pitches a part plays (gaps of more than a third of a semitone split them)."""
    rows = sorted(rows, key=lambda r: r['midi'])
    out = []
    for r in rows:
        if out and r['midi'] - out[-1][-1]['midi'] <= 0.33:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def _tuning_verdict(name, rows, tol, gc, skip_ms):
    """One line an agent can act on, for one part's readings."""
    med, spread = _spread_stats([r['off'] for r in rows])
    off = [r for r in rows if abs(r['off']) > tol]
    n = len(rows)
    if not off:
        return f"{name}: in tune (median {med:+.0f} c, spread {spread:.0f} c)"
    bad = []                                            # the pitches that sit off: (count, label, a quarter tone?)
    for cl in _clusters(rows):
        cm, _ = _spread_stats([r['off'] for r in cl])
        if abs(cm) > tol:
            k, _ = _cents_off(float(np.median([r['midi'] for r in cl])), gc)
            bad.append((len(cl), f"~{midi_to_name(k)} {cm:+.0f} c (x{len(cl)})", abs(cm) >= 40))
    quarter = ' a quarter tone' if any(q for *_, q in bad) else ''
    named = ', '.join(s for _, s, _ in sorted(bad, reverse=True)[:3]) + (' ...' if len(bad) > 3 else '')
    in_bad = sum(c for c, *_ in bad)
    if abs(med) > tol and spread < tol:
        return (f"{name}: the whole part sits{quarter} off the grid at {med:+.0f} c, spread {spread:.0f} c "
                f"({named}): retune its voice by {-med:+.0f} c (its tune/tone param, or the sample's root pitch)")
    if bad and len(bad) <= 3 and in_bad >= 0.7 * len(off):
        return (f"{name}: {in_bad} of {n} pitched notes sit{quarter} off the grid ({named}): retune that voice "
                f"(params.tone or tune) or split it onto its own track to retune it alone")
    if len(off) <= max(1, 0.1 * n):
        return (f"{name}: mostly in tune (median {med:+.0f} c, spread {spread:.0f} c); {len(off)} of {n} notes beyond "
                f"{tol:g} c (worst above)")
    return (f"{name}: {len(off)} of {n} notes beyond {tol:g} c, over {len({r['k'] for r in off})} pitches (median "
            f"{med:+.0f} c, spread {spread:.0f} c): the tuning drifts note to note. Look for a random detune or "
            f"intonation drawn per note or per string, unison or chorus detune, or an attack glide lasting past "
            f"{skip_ms:g} ms")


@op()
def analyze_tuning(project: str, source: str = 'render', stems: list = None, tol_cents: float = 15.0,
                   bars: list = None, key: str = None, tuning_cents: float = None, skip_ms: float = 100.0,
                   worst: int = 5) -> str:
    """Check every part of a render against the song's tuning grid (12-TET) and key, and flag notes more than
    tol_cents off (ledger:M231). A pitched part off the grid goes unnoticed until someone hears it: drums measured
    from records keep the records' tuning (a tumba a quarter tone sharp). Run it after the first render of a song
    with pitched percussion or a voice playing in a new register, and before any exam.
    Reads: source='render' (default) every track stem of the last render(stems=True); 'ref' the reference's stems;
    'track:<name>', 'ref:<stem>', 'sound:<name>' or a file path one part; 'render:mix' / 'ref:mix' a whole mix as one
    part. stems=[names] keeps some parts; bars=[a, b] (inclusive) cuts the range.
    Per note: the steady pitch from skip_ms (100) after its onset (plucks start sharp for ~90 ms, a drum's strike is
    noise), read from spectral lines, so a chord reads one pitch per note and a drum its sustained mode; gliding
    notes and noise are counted, not read. skip_ms is the longest skip: a note shorter than skip_ms / 0.3 (a fast
    run: a 16th at 104 bpm is ~144 ms) is read from 0.3 of its length, so a picado scale is read, not skipped. The
    attack is read too (30-80 ms after the onset, less on a short note); a note too short for a steady part is read
    from its attack on and marked 'attack only' (it still counts against the grid), and one too short to resolve
    its pitch (under 4 periods of it) is counted, not read. Grid: A4 = 440 Hz moved by tuning_cents; by default 0
    for renders, and for a reference ref_retune has not moved, its own measured tuning. key ('A minor', 'D dorian'; default the
    project's or the sketch's) lists out-of-key pitches as information (they can be intended).
    Returns a table per part (pitched notes, median cents off the grid, spread = middle half, count beyond
    tol_cents, attack = median cents of the attack over the steady pitch), up to `worst` worst notes per part
    (seconds in the file, bar and beat, note, cents, Hz), and one verdict per part: in tune; the whole part off
    (retune its voice by N c); a few pitches off (retune that voice or split it onto its own track); or drifting note
    to note (per-note or per-string detune). When most attacks sit more than 5 c off their steady pitch, the
    verdict adds "attacks start N c sharp: shorten the voice's pitch glide" (in fast runs that attack is what is
    heard). A part with no steady pitch (hats, noise, a sliding vocal) says so and is skipped, never flagged.
    Errors: nothing rendered or no stems -> render(stems=True) first; no reference stems -> separate(source='ref');
    an unknown part in stems= -> the reply lists the parts."""
    if not 0 < tol_cents < 50:
        raise OpError(f"tol_cents={tol_cents}: give 1-49 cents (15 is the usual line; wider is heard as out of tune)")
    if not 0 <= skip_ms <= 1000:
        raise OpError(f"skip_ms={skip_ms}: give 0-1000 ms (100 skips a pluck's sharp start and a drum's strike)")
    P = _load(project)
    parts, note = _tuning_parts(P, source, stems)
    gc, why = _grid_cents(P, source, tuning_cents)
    klabel, kpcs = _song_key(P, key)
    L = [f"analyze_tuning {source}{f' bars {bars[0]}-{bars[1]}' if bars else ''}: 12-TET at A4 = "
         f"{440.0 * 2 ** (gc / 1200):.1f} Hz ({why}); flag beyond {tol_cents:g} c; each note read from {skip_ms:g} ms "
         f"after its onset (0.3 of a shorter note), its attack before that" +
         (f"; key {klabel}" if klabel else "; no key known (key='A minor' adds out-of-key pitches)")]
    if note:
        L.append(note)
    L.append(f"  {'part':<14} {'notes':>5} {'median':>7} {'spread':>7} {'beyond':>10} {'attack':>7}")
    verdicts, details = [], []
    for name, src in parts:
        path, g = P.source(src, bars)
        y = A.load(path)
        t0 = 0.0
        if bars:
            t0 = max(g.bar_time(bars[0]), 0.0)
            y = y[int(t0 * A.ASR):max(int(g.bar_time(bars[1] + 1) * A.ASR), 0)]
        rows, cnt = A.steady_pitches(y, A.ASR, skip_ms=skip_ms)
        if len(rows) < 3 or cnt['pitched'] < 0.1 * cnt['events']:
            L.append(f"  {name:<14} {len(rows):>5} {'-':>7} {'-':>7} {'-':>10} {'-':>7}")
            short = f", {cnt['short']} too short to resolve" if cnt['short'] else ''
            verdicts.append(f"{name}: no steady pitch ({cnt['pitched']} of {cnt['events']} notes or hits pitched, "
                            f"{cnt['noisy']} noise, {cnt['gliding']} gliding{short}): unpitched, too noisy or too "
                            f"short, skipped")
            continue
        for r in rows:
            r['t'] += t0
            r['k'], r['off'] = _cents_off(r['midi'], gc)
        med, spread = _spread_stats([r['off'] for r in rows])
        off = sorted([r for r in rows if abs(r['off']) > tol_cents], key=lambda r: -abs(r['off']))
        att = [100 * (r['attack'] - r['midi']) for r in rows if r['attack'] is not None]
        am = float(np.median(att)) if att else None
        L.append(f"  {name:<14} {len(rows):>5} {med:>+6.0f}c {spread:>6.0f}c {len(off):>4} "
                 f"({100 * len(off) / len(rows):3.0f}%) " + (f"{am:>+6.0f}c" if att else f"{'-':>7}"))
        v = _tuning_verdict(name, rows, tol_cents, gc, skip_ms)
        if len(att) >= 3 and sum(abs(d) > 5 and d * am > 0 for d in att) > len(att) / 2:
            v += (f"; attacks start {abs(am):.0f} c {'sharp' if am > 0 else 'flat'} (median of {len(att)}): "
                  f"shorten the voice's pitch glide")
        verdicts.append(v)
        extra = []
        if cnt['sampled']:
            extra.append(f"read an even sample of {cnt['events']} of {cnt['sampled']} onsets")
        if cnt['fast']:
            extra.append(f"{cnt['fast']} fast notes read from 0.3 of their length, not {skip_ms:g} ms")
        if cnt['attack_only']:
            extra.append(f"{cnt['attack_only']} too short for a steady part: read from the attack on, marked "
                         f"'attack only'")
        if cnt['short']:
            extra.append(f"{cnt['short']} notes too short to resolve their pitch (under 4 periods or 25 ms), not read")
        if cnt['gliding']:
            extra.append(f"{cnt['gliding']} gliding notes not read")
        if kpcs:
            out_key = {}
            for r in rows:
                if r['k'] % 12 not in kpcs:
                    out_key[midi_to_name(r['k'])] = out_key.get(midi_to_name(r['k']), 0) + 1
            if out_key:
                top = sorted(out_key.items(), key=lambda kv: -kv[1])
                extra.append(f"outside {klabel} (information; may be intended): " +
                             ', '.join(f"{n} x{c}" for n, c in top[:6]) + (' ...' if len(top) > 6 else ''))
        for r in off[:max(worst, 0)]:
            bt = round(g.time_to_beat(r['t']), 2)          # song beats from bar 1, rounded before splitting
            extra.append(f"{r['t']:7.2f} s  bar {int(bt // g.bpb) + 1} beat {bt % g.bpb + 1:.2f}  "
                         f"{midi_to_name(r['k'])} {r['off']:+.0f} c  ({440 * 2 ** ((r['midi'] - 69) / 12):.1f} Hz)"
                         + ("  attack only" if r['attack_only'] else ''))
        if extra:
            details.append(f"  {name}:\n" + '\n'.join('    ' + x for x in extra))
    L += details
    L.append("verdict:")
    L += ['  ' + v for v in verdicts]
    return '\n'.join(L)


HEARD_BANDS = [(300, 1000, 'body'), (1000, 2000, 'words'), (2000, 4000, 'consonants'), (4000, 8000, 'air')]


def voice_over_rest(voice, parts, sr, hop_s=0.02, gate_db=-30.0):
    """(fraction of frames where the voice speaks, [(lo, hi, what, voice over the rest dB, loudest other part,
    that part over the voice dB)]): mono arrays, the rest is the sum of `parts` ({name: array}), each band's energy
    averaged over the 20 ms frames where the voice is within gate_db of its loudest frame."""
    import scipy.signal as ss
    n = min([len(voice)] + [len(p) for p in parts.values()])
    hop = int(hop_s * sr)
    voice = voice[:n]
    parts = {k: p[:n] for k, p in parts.items()}

    def frames(x):
        k = (len(x) // hop) * hop
        return np.mean(x[:k].reshape(-1, hop) ** 2, axis=1)

    e = frames(voice)
    if not len(e) or e.max() <= 1e-12:
        return 0.0, []
    on = e > e.max() * 10 ** (gate_db / 10)
    rows = []
    for lo, hi, what in HEARD_BANDS:
        if hi >= sr / 2:
            continue
        b = ss.butter(4, [lo / (sr / 2), hi / (sr / 2)], 'band', output='sos')
        ve = frames(ss.sosfilt(b, voice))[on].mean() + 1e-20
        pe = {k: frames(ss.sosfilt(b, p))[on].mean() + 1e-20 for k, p in parts.items()}
        re_ = frames(ss.sosfilt(b, sum(parts.values())))[on].mean() + 1e-20
        top = max(pe, key=pe.get) if pe else '-'
        rows.append((lo, hi, what, 10 * np.log10(ve / re_), top, 10 * np.log10(pe[top] / ve) if pe else 0.0))
    return float(on.mean()), rows


@op()
def analyze_voice_heard(project: str, voice: str, bars: list = None) -> str:
    """Is the voice heard over the mix? Reach for it when a spoken or sung line may be buried (a narrator under a
    drop, a vocal fighting a lead), before asking the person whether they can make out the words. Reads the last
    render(stems=True): `voice` is the voice's track stem, the rest is the sum of the other track stems that go to
    the master plus the bus stems (a bus the voice goes through counts without the voice). bars=[a, b] (inclusive)
    cuts the range. Per band (300-1000 body, 1-2k words, 2-4k consonants, 4-8k air): the voice over the rest in dB
    over the 20 ms frames where the voice speaks (within 30 dB of its loudest), and the loudest other part there.
    Ends in a one-line verdict naming the band that masks it most. No transcription (no speech server is assumed).
    Built in a song and proven there."""
    P = _load(project)
    tracks = P.d['tracks']
    if voice not in tracks:
        raise OpError(f"no track {voice!r}; tracks: {list(tracks)}")
    v, sr = _segment(P, f'track:{voice}', bars)
    rd = os.path.join(P.root, 'renders', 'stems')
    have = audiofile.listing(rd)
    parts = {}
    for n, tr in tracks.items():
        if n != voice and n in have and not tr.get('mute') and tr.get('output', 'master') == 'master':
            parts[n] = _segment(P, f'track:{n}', bars)[0].mean(0)
    vm = v.mean(0)
    for b in P.d.get('buses', {}):
        if 'bus_' + b in have:
            y = _segment(P, f'track:bus_{b}', bars)[0].mean(0)
            if tracks[voice].get('output') == b:      # the voice went through this bus: count the bus without it
                k = min(len(y), len(vm))
                y = y[:k] - vm[:k]
            parts['bus:' + b] = y
    if not parts:
        raise OpError("no other stems to measure the voice against: render(stems=True) with the other parts "
                      "playing, then call again")
    frac, rows = voice_over_rest(vm, parts, sr)
    where = f" bars {bars[0]}-{bars[1]}" if bars else ''
    if not rows:
        raise OpError(f"the {voice!r} stem is silent{where}: pick bars where it speaks")
    L = [f"analyze_voice_heard {voice}{where}: the voice speaks in {frac * 100:.0f}% of the 20 ms frames; "
         f"rest = {', '.join(parts)}",
         f"  {'band Hz':<11} {'':<10} {'voice/rest':>10}  loudest other part there"]
    for lo, hi, what, db, top, tdb in rows:
        L.append(f"  {f'{lo}-{hi}':<11} {what:<10} {db:>+8.1f} dB  {top} ({tdb:+.1f} dB over the voice)")
    lo, hi, what, db, top, tdb = min(rows, key=lambda r: r[3])
    band = f"{lo}-{hi} Hz ({what})"
    if db >= 0:
        L.append(f"verdict: heard: the voice is over the rest in every band while it speaks (closest: {band} at "
                 f"{db:+.1f} dB)")
    elif all(r[3] >= 0 for r in rows if r[0] in (1000, 2000)):
        L.append(f"verdict: heard, the words are clear (1-4 kHz over the rest), but {band} masks it ({db:+.1f} dB), "
                 f"mostly {top}")
    else:
        L.append(f"verdict: masked: {band} buries it ({-db:.1f} dB under the rest), mostly {top}: cut that band in "
                 f"{top} while the voice speaks, or lift the voice there")
    return '\n'.join(L)
