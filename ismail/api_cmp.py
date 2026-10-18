"""Ops for stems, structure and stored comparisons (registered into api.OPS on import)."""
import glob
import os
import shutil
import time

import numpy as np

from . import audiofile
from .api import op, OpError, _load

STEMS = ('drums', 'bass', 'other', 'vocals')


@op(mutates=True)
def stem_map_set(project: str, mapping: dict) -> str:
    """Declare which reference stem each of your tracks belongs to, e.g. {"kick": "drums", "riff": "other"}.
    cmp_run(stems='tracks') builds your stems from these groups (no separation artefacts on your side)."""
    P = _load(project)
    for t, s in mapping.items():
        P.track(t)
        if s not in STEMS:
            raise OpError(f"stem {s!r} must be one of {STEMS}")
    P.d['stem_map'] = dict(P.d.get('stem_map') or {}, **mapping)
    unmapped = [t for t in P.d['tracks'] if t not in P.d['stem_map']]
    P.save()
    return f"stem map: {P.d['stem_map']}" + (f"\nunmapped tracks (ignored in stem compares): {unmapped}" if unmapped else '')


def ref_stems(P):
    ref = P.d.get('reference') or {}
    out = {'mix': P.resolve_audio('ref')}
    sd = ref.get('stems_dir')
    if sd:
        for s in STEMS:
            p = audiofile.find(os.path.join(sd, s + '.wav'))
            if os.path.exists(p):
                out[s] = p
    return out


def render_stems(P, mode):
    """Your side: {'mix': latest.wav, stem: path}; stems from your tracks (mode 'tracks') or demucs ('demucs')."""
    import soundfile as sf
    mix = P.resolve_audio('render')
    out = {'mix': mix}
    if mode == 'none':
        return out
    if mode == 'demucs':
        from .separate import separate as sep
        od = os.path.join(P.root, 'renders', 'sep')
        sep(mix, od, 'htdemucs_ft')
        for s in STEMS:
            out[s] = os.path.join(od, s + '.wav')
        return out
    sm = P.d.get('stem_map') or {}
    if not sm:
        raise OpError("no stem map; call stem_map_set({track: stem}) or use stems='demucs' or 'none'")
    sd = os.path.join(P.root, 'renders', 'stems')
    info = sf.info(mix)
    groups = {}

    def add(stem, path):
        if P.render_window(path) != P.render_window(mix):
            raise OpError(f"{os.path.basename(path)} was rendered for a different window than latest.wav; render again"
                          " with stems=True")
        y, _ = sf.read(path, dtype='float32', always_2d=True)
        if abs(len(y) - info.frames) > info.samplerate:
            raise OpError(f"{os.path.basename(path)} length differs from latest.wav; re-render the FULL song with stems=True")
        g = groups.setdefault(stem, np.zeros((info.frames, 2), np.float32))
        m = min(len(g), len(y))
        g[:m] += y[:m]
    for t, s in sm.items():
        tr = P.d['tracks'].get(t)
        if tr is None or tr.get('mute'):
            continue
        if tr.get('output', 'master') != 'master':
            continue  # routed into a bus: the bus stem carries it (processed), don't count it twice
        p = audiofile.find(os.path.join(sd, t + '.wav'))
        if not os.path.exists(p):
            raise OpError(f"no rendered stem for {t!r}; render(stems=True) (full song) first")
        add(s, p)
    # buses: assign to the stem of the tracks feeding them (outputs count fully, sends by level)
    for bname in P.d.get('buses', {}):
        p = audiofile.find(os.path.join(sd, f"bus_{bname}.wav"))
        if not os.path.exists(p):
            continue
        weight = {}
        for t, tr in P.d['tracks'].items():
            if t not in sm:
                continue
            if tr.get('output') == bname:
                weight[sm[t]] = weight.get(sm[t], 0) + 1.0
            if bname in (tr.get('sends') or {}):
                weight[sm[t]] = weight.get(sm[t], 0) + 10 ** (tr['sends'][bname] / 20)
        add(max(weight, key=weight.get) if weight else 'other', p)
    gd = os.path.join(P.root, 'renders', 'groups')
    os.makedirs(gd, exist_ok=True)
    for s in STEMS:
        y = groups.get(s, np.zeros((info.frames, 2), np.float32))
        p = os.path.join(gd, s + '.wav')
        sf.write(p, y, info.samplerate, subtype='FLOAT')
        out[s] = p
    return out


@op()
def analyze_structure(project: str, source: str = None, stems: bool = True) -> str:
    """Macro view of a whole song: arrangement map (bands + stems, one char per bar), sections lettered by
    similar material, repeats, loop length. source='ref' uses reference stems; 'render' uses your stem groups
    (default: the reference if the project has one, else your render)."""
    from . import features as FE, structure as ST
    P = _load(project)
    source = P.auto_source(source)
    g = P.source(source)[1]
    cd = os.path.join(P.root, 'cache')
    if source == 'ref':
        srcs = ref_stems(P)
    elif source == 'render':
        try:
            srcs = render_stems(P, 'tracks' if P.d.get('stem_map') else 'none')
        except OpError:
            srcs = {'mix': P.resolve_audio('render')}
    else:
        srcs = {'mix': P.resolve_audio(source)}
    Fm = FE.extract(srcs['mix'], g, cache_dir=cd)
    Fs = {s: FE.extract(p, g, cache_dir=cd) for s, p in srcs.items() if s != 'mix'} if stems else {}
    return ST.describe(Fm, Fs)[1]


@op()
def analyze_roll(project: str, source: str = 'ref:other', bars: list = None, low: str = 'C1', high: str = 'C8',
                 rel_db: float = 18.0, floor_db: float = -40.0, as_notes: bool = False, min_steps: int = 1,
                 max_bars: int = 4) -> str:
    """Piano roll of any source from the same step features the comparisons score: one row per pitch, one char per
    16th ('#' note starts, '=' sustains). low/high limit the pitch range (split bass / chords / lead that share a
    stem); rel_db = how far under the step's loudest note still counts; floor_db = absolute floor. as_notes=True
    returns a notes_write list (beats relative to the first bar) instead - write it, then cmp to verify."""
    from . import features as FE
    from .notation import pitch_to_midi, midi_to_name, fmt_num
    P = _load(project)
    path, g = P.source(source, bars)
    F = FE.extract(path, g, cache_dir=os.path.join(P.root, 'cache'))
    spb = FE.steps_per_bar(F)
    b0, b1 = bars or [g.first_bar()] * 2
    if b1 - b0 + 1 > (16 if as_notes else max_bars):
        raise OpError(f"at most {16 if as_notes else max_bars} bars per call; page with bars=[a, b]")
    sl = slice((b0 - 1) * spb, b1 * spb)
    S = FE.note_sets(F, rel_db, floor_db)
    Aa = FE.note_attacks(F, S)
    lo, hi = pitch_to_midi(low) - FE.PITCH_BASE, pitch_to_midi(high) - FE.PITCH_BASE
    S, Aa, sal = S[sl, lo:hi + 1], Aa[sl, lo:hi + 1], F['sal'][sl, lo:hi + 1]
    n = S.shape[0]
    step_beats = 1 / (spb // P.bpb)
    if as_notes:
        notes = []
        for j in range(S.shape[1]):
            i = 0
            while i < n:
                if S[i, j]:
                    k = i + 1
                    while k < n and S[k, j] and not Aa[k, j]:
                        k += 1
                    if k - i >= min_steps:
                        vel = int(np.clip(127 + sal[i:k, j].max() * 2, 30, 127))
                        notes.append((i * step_beats, lo + j + FE.PITCH_BASE, (k - i) * step_beats, vel))
                    i = k
                else:
                    i += 1
        notes.sort()
        if not notes:
            return '(no notes in range)'
        return (f"# {len(notes)} notes, bars {b0}-{b1}, beats relative to bar {b0} (notes_write bar={b0})\n" +
                '\n'.join(f"{fmt_num(s)} {midi_to_name(p)} {fmt_num(d)} {v}" for s, p, d, v in notes))
    beat = spb // P.bpb
    L = [f"{source} bars {b0}-{b1} pitches {low}-{high}: '#' starts, '=' sustains",
         f"{'':>5}" + ''.join(str((i // beat) % P.bpb + 1) if i % beat == 0 else '.' for i in range(n))]
    for j in range(S.shape[1] - 1, -1, -1):
        if not S[:, j].any():
            continue
        L.append(f"{midi_to_name(lo + j + FE.PITCH_BASE):>5}" +
                 ''.join('#' if S[i, j] and Aa[i, j] else '=' if S[i, j] else '.' for i in range(n)))
    return '\n'.join(L)


def consensus_pattern(F, b0, b1, loop_bars, lo, hi, rel_db, floor_db, min_presence, min_steps, min_rel_db):
    """Notes of one loop cycle that are present in >= min_presence of the cycle's repetitions inside bars [b0, b1].
    Returns ([(step, pitch_index, n_steps, vel)], n_reps, stats)."""
    from . import features as FE
    spb = FE.steps_per_bar(F)
    Ls = loop_bars * spb
    S = FE.note_sets(F, rel_db, floor_db)[:, lo:hi + 1]
    A = FE.note_attacks(F, FE.note_sets(F, rel_db, floor_db))[:, lo:hi + 1]
    sal = F['sal'][:, lo:hi + 1]
    starts = [(b - 1) * spb for b in range(b0, b1 + 1, loop_bars) if b + loop_bars - 1 <= b1]
    starts = [s for s in starts if s + Ls <= len(S)]
    if len(starts) < 2:
        raise OpError(f"need at least 2 full {loop_bars}-bar repetitions inside bars {b0}-{b1}")
    Sr = np.stack([S[s:s + Ls] for s in starts]).astype(float)
    Ar = np.stack([A[s:s + Ls] for s in starts])
    At = Ar.copy()
    At[:, 1:] |= Ar[:, :-1]
    At[:, :-1] |= Ar[:, 1:]  # +-1 step tolerance when voting on attacks
    pres = Sr.mean(0)
    apres = At.astype(float).mean(0)
    salm = np.stack([sal[s:s + Ls] for s in starts]).mean(0)
    keep = pres >= min_presence
    notes = []
    for j in range(keep.shape[1]):
        i = 0
        while i < Ls:
            if not keep[i, j]:
                i += 1
                continue
            k = i + 1
            # split a run where a consensus re-attack peaks
            while k < Ls and keep[k, j] and not (apres[k, j] >= max(min_presence * 0.7, 0.34) and
                                                 apres[k, j] >= apres[max(k - 1, 0), j] and
                                                 apres[k, j] >= apres[min(k + 1, Ls - 1), j] and k - i >= 1):
                k += 1
            if k - i >= min_steps:
                notes.append([i, j, k - i, float(salm[i:k, j].max())])
            i = k
    if notes:
        top = max(n[3] for n in notes)
        notes = [n for n in notes if n[3] >= top - min_rel_db]
    out = [(s, j, d, int(np.clip(127 + v * 2, 30, 127))) for s, j, d, v in notes]
    stats = {'reps': len(starts), 'kept': len(out)}
    return sorted(out), stats


@op(mutates=True)
def notes_from_audio_loop(project: str, track: str, source: str, bars: list, loop_bars: int = 8,
                          low: str = 'C1', high: str = 'C8', min_presence: float = 0.6, rel_db: float = 15.0,
                          floor_db: float = -38.0, min_steps: int = 1, min_rel_db: float = 20.0,
                          write_bars: list = None, mode: str = 'replace', base_bars: list = None) -> str:
    """Consensus transcription: average the step-note grid of `source` over every repetition of a loop_bars-long
    cycle inside bars [a, b], keep only notes present in >= min_presence of the repetitions (artefacts, echoes and
    leakage that don't repeat drop out), drop notes quieter than min_rel_db under the loudest, then tile the clean
    cycle over write_bars (default = bars). Use for loop-based music; prefer it over notes_from_audio.
    Loop VARIANTS (the song alternates two versions of its loop): transcribe each variant from its own repetitions and
    pass base_bars = the whole span; notes the variant shares with the base consensus are written exactly as the
    base has them (same length and velocity), so variants differ only where the reference differs."""
    from . import features as FE, api
    from .notation import pitch_to_midi, midi_to_name, fmt_num
    P = _load(project)
    P.track(track)
    F = FE.extract(*P.source(source, bars), cache_dir=os.path.join(P.root, 'cache'))
    lo, hi = pitch_to_midi(low) - FE.PITCH_BASE, pitch_to_midi(high) - FE.PITCH_BASE
    pat, st = consensus_pattern(F, bars[0], bars[1], loop_bars, lo, hi, rel_db, floor_db, min_presence, min_steps,
                                min_rel_db)
    if not pat:
        return f"no notes reach presence {min_presence} over {st['reps']} repetitions; lower min_presence or check range"
    shared = 0
    if base_bars:
        base, _ = consensus_pattern(F, base_bars[0], base_bars[1], loop_bars, lo, hi, rel_db, floor_db,
                                    min_presence, min_steps, min_rel_db)
        twin = {(s, j): (s, j, d, v) for s, j, d, v in base}
        pat = [twin.get((s, j), (s, j, d, v)) for s, j, d, v in pat]
        shared = sum((s, j) in twin for s, j, _, _ in pat)
    spb = FE.steps_per_bar(F)
    sb = 1 / (spb // P.bpb)
    text = '\n'.join(f"{fmt_num(s * sb)} {midi_to_name(lo + j + FE.PITCH_BASE)} {fmt_num(d * sb)} {v}" for s, j, d, v in pat)
    wb = write_bars or bars
    n_loops = (wb[1] - wb[0] + 1) // loop_bars
    api.notes_write(project, track, wb[0], text, mode=mode, bars=loop_bars, repeat=max(n_loops, 1))
    rem = (wb[1] - wb[0] + 1) - n_loops * loop_bars
    if rem > 0:  # partial cycle at the end
        part = '\n'.join(l for l in text.split('\n') if float(l.split()[0]) < rem * P.bpb)
        if part:
            api.notes_write(project, track, wb[0] + n_loops * loop_bars, part, mode=mode, bars=rem)
    note = f"; {shared} shared with the base consensus" if base_bars else ''
    return (f"{track}: {st['kept']} consensus notes per {loop_bars}-bar cycle (from {st['reps']} repetitions of "
            f"{source} {low}-{high}), tiled over bars {wb[0]}-{wb[1]}{note}\n{text if len(pat) <= 60 else ''}").rstrip()


@op(mutates=True)
def notes_from_audio(project: str, track: str, source: str, bars: list, low: str = 'C1', high: str = 'C8',
                     rel_db: float = 18.0, floor_db: float = -40.0, min_steps: int = 1, mode: str = 'replace',
                     transpose: int = 0, max_notes_per_step: int = 8) -> str:
    """Transcribe a pitch range of an audio source into a track over bars [a, b] (any length), using the same step
    features the comparisons score (so writing them raises note_f1 by construction). mode='replace' clears that
    range of the track first. Use low/high to route registers of one stem to different tracks (bass / stabs /
    lead). Check the result with notes_read(view='roll') and cmp_run."""
    import json as _json
    from . import api
    P = _load(project)
    P.track(track)
    out, total = [], 0
    for a in range(bars[0], bars[1] + 1, 16):
        b = min(a + 15, bars[1])
        txt = analyze_roll(project, source, [a, b], low, high, rel_db, floor_db, as_notes=True, min_steps=min_steps)
        if txt.startswith('('):
            continue
        body = '\n'.join(l for l in txt.split('\n') if not l.startswith('#'))
        if transpose:
            from .notation import parse_notes, midi_to_name, fmt_num
            body = '\n'.join(f"{fmt_num(s)} {midi_to_name(p + transpose)} {fmt_num(d)} {v}" for s, p, d, v in parse_notes(body))
        # limit polyphony per start step: keep the loudest
        rows = [l.split() for l in body.split('\n') if l.strip()]
        by = {}
        for r in rows:
            by.setdefault(r[0], []).append(r)
        rows = [r for k in by for r in sorted(by[k], key=lambda r: -int(r[3]))[:max_notes_per_step]]
        total += len(rows)
        api.notes_write(project, track, a, '\n'.join(' '.join(r) for r in rows), mode=mode, bars=b - a + 1)
    return f"{track}: transcribed {total} notes from {source} ({low}-{high}) into bars {bars[0]}-{bars[1]}"


def _cmp_dir(P, cid=None):
    root = os.path.join(P.root, 'comparisons')
    if cid is None:
        ids = sorted(i for i in (os.listdir(root) if os.path.isdir(root) else [])
                     if os.path.exists(os.path.join(root, i, 'report.json')))  # complete runs only
        if not ids:
            raise OpError("no comparisons yet; run cmp_run first")
        cid = ids[-1]
    d = os.path.join(root, cid)
    if not os.path.exists(os.path.join(d, 'report.json')):
        raise OpError(f"no comparison {cid!r}; cmp_list shows them")
    return d, cid


def _rep(P, cid, stem=None):
    from . import cmp as C
    d, cid = _cmp_dir(P, cid)
    rep = C.load(d)
    if stem is not None and stem not in rep['stems']:
        raise OpError(f"stem {stem!r} not in comparison {cid}; have {list(rep['stems'])}")
    return rep, d, cid


@op()
def cmp_run(project: str, label: str = '', stems: str = 'tracks', b: str = 'ref') -> str:
    """Build and store a full comparison of your latest render (A) vs the reference (B): every stem (mix, drums,
    bass, other, vocals), every bar, every 16th step. stems: 'tracks' (your stems from stem_map; needs a full
    render(stems=True)), 'demucs' (separate your render with the reference's model: apples to apples, slower),
    'none' (mix only). Returns the summary vs ceiling/floor baselines. Drill in with cmp_sections, cmp_bars,
    cmp_worst, cmp_zoom, cmp_arrangement; cmp_list shows progress across runs."""
    from . import cmp as C
    P = _load(project)
    a = render_stems(P, stems)
    bsrc = ref_stems(P) if b == 'ref' else {'mix': P.resolve_audio(b)}
    cid = time.strftime('%Y%m%d-%H%M%S')
    d = os.path.join(P.root, 'comparisons', cid)
    try:
        rep = C.build(d, a, bsrc, P.source('render')[1], P.source(b)[1], os.path.join(P.root, 'cache'), label,
                      extra_meta={'stems_mode': stems})
        shutil.copy(P.file, os.path.join(d, 'project.json'))
    except OSError as e:
        shutil.rmtree(d, ignore_errors=True)  # never leave a half-written run behind as the 'latest'
        raise OpError(f"comparison not stored ({e}); if the disk is full, free space and cmp_run again")
    from . import replies
    if replies.is_ref(b):
        replies.note_compared(P.root, 'cmp_run', cid)
    return f"comparison id {cid}\n" + C.view_summary(rep)


@op()
def cmp_summary(project: str, id: str = None) -> str:
    """Summary of a stored comparison (default latest): per-stem metrics vs ceiling/floor baselines."""
    from . import cmp as C
    rep, d, cid = _rep(_load(project), id)
    return f"comparison id {cid}\n" + C.view_summary(rep)


@op()
def cmp_sections(project: str, stem: str = 'mix', id: str = None) -> str:
    """Metrics per reference section for one stem."""
    from . import cmp as C
    rep, _, _ = _rep(_load(project), id, stem)
    return C.view_sections(rep, stem)


@op()
def cmp_bars(project: str, stem: str = 'mix', bars: list = None, id: str = None) -> str:
    """Per-bar metrics for one stem, 32 bars per page (bars=[a, b])."""
    from . import cmp as C
    rep, _, _ = _rep(_load(project), id, stem)
    return C.view_bars(rep, stem, bars)


@op()
def cmp_worst(project: str, stem: str = 'mix', metric: str = None, n: int = 10, id: str = None) -> str:
    """The n worst bars of a stem, overall or by one metric (note_f1 pc_f1 attack_f1 hit_low hit_snare hit_hat
    shape dlevel)."""
    from . import cmp as C
    rep, _, _ = _rep(_load(project), id, stem)
    return C.view_worst(rep, stem, metric, n)


@op()
def cmp_zoom(project: str, bar: int, stem: str = 'mix', bars: int = 1, layers: list = None, id: str = None) -> str:
    """Step-level A vs B for bar..bar+bars-1: level digits, drum-lane hits, one row per pitch (b both, r reference
    only = you miss it, y yours only = extra, UPPERCASE = the note starts). layers: level bands hits notes."""
    from . import cmp as C
    P = _load(project)
    rep, d, _ = _rep(P, id, stem)
    if bars > 4:
        raise OpError("zoom at most 4 bars at a time; page with bar=")
    return C.view_zoom(rep, d, os.path.join(P.root, 'cache'), bar, stem,
                       tuple(layers or ('level', 'hits', 'notes')), bars)


@op()
def cmp_arrangement(project: str, id: str = None) -> str:
    """Arrangement maps, yours vs reference, per stem (one char per bar): the macro structure at a glance."""
    from . import cmp as C
    P = _load(project)
    rep, d, _ = _rep(P, id)
    return C.arrangement_compare(rep, d, os.path.join(P.root, 'cache'))


@op()
def cmp_list(project: str) -> str:
    """All stored comparisons with closeness per stem, oldest first: progress over time."""
    from . import cmp as C
    P = _load(project)
    root = os.path.join(P.root, 'comparisons')
    if not os.path.isdir(root):
        return '(none)'
    L = []
    for cid in sorted(os.listdir(root)):
        try:
            rep = C.load(os.path.join(root, cid))
        except (OSError, ValueError):
            continue
        per = []
        for stem, st in rep['stems'].items():
            cl = C.closeness(st['summary'], st['baselines'], st.get('consistency_a'))
            per.append(f"{stem} {C.group_closeness(cl)[1]:.2f}" if cl else f"{stem} -")
        L.append(f"{cid}  {rep.get('label', ''):<24} " + '  '.join(per))
    return '\n'.join(L)
