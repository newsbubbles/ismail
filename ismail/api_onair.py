"""The on-air check (ledger:M432, Nate 11-04): measure a render before it airs or goes to review. Registered into
api.OPS on import.

  analyze_silence  silent stretches in audio of any length (a render, a file, a whole assembly), read in blocks
  onair_check      the silence scan, each named source's level in the mix against a floor, and the caller's own
                   rules file (a Python file; a nonzero exit is a fail), with one verdict and what to do next on a fail
  onair_gate       the same check for a set's or a video's own code: raises OnAirRefused on a fail unless force=True

Nothing here knows what a set, a world or a chapter is. A set's own rules (its norms file) plug in as a Python file;
which sources a piece names, and what its plan calls silent, are the caller's arguments. Five live set parts once
aired with their named source too quiet to hear (a string theme at -39 dBFS) or silent foley, while the set's
norms check only warned; an album video went to review with a 3-minute silent chapter.
"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

from .api import op, OpError, _load
from .api_measure import _cwd_first, _KEYWORD_SOURCES, _KEYWORD_PREFIXES

SILENCE_WIN_S = 0.1         # the silence scan reads rms per 100 ms
LEVEL_WIN_S = 0.4           # source levels per 400 ms, as analyze_sections
ACTIVE_RANGE_DB = 30.0      # a source "plays" in a window within this many dB of its own loudest window
GONE_DB = -70.0             # a source whose loudest window is under this is silent
RULES_TAIL = 15             # lines of the rules file's output kept in the reply


class OnAirRefused(OpError):
    """onair_gate on a fail without force=True. str() is the whole report; .reasons lists one line per fail."""

    def __init__(self, text, reasons):
        super().__init__(text)
        self.reasons = reasons


# ------------------------------------------------------------------ reading audio in blocks

PIPE_SR = 48000             # a file libsndfile cannot open (mp4, m4a, mp3) is decoded through ffmpeg at this rate


def _pipe_blocks(path, frames_per_block, ch=2):
    """float32 blocks (frames x ch) of any file ffmpeg reads, streamed from a pipe: a video of hours never sits in
    memory whole."""
    from . import settings
    exe = settings.get('ffmpeg')
    if not exe:
        raise OpError(f"{path!r} is not a file libsndfile reads (wav, flac, ogg) and ffmpeg is not found to decode it: "
                      f"install ffmpeg or config_set('ffmpeg', <its path>), or check an audio copy of it")
    proc = subprocess.Popen([exe, '-nostdin', '-v', 'error', '-i', str(path), '-vn', '-ac', str(ch), '-ar', str(PIPE_SR),
                             '-f', 'f32le', '-'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    step = frames_per_block * ch * 4
    try:
        while True:
            buf = proc.stdout.read(step)
            if not buf:
                break
            buf = buf[:len(buf) - len(buf) % (ch * 4)]
            if buf:
                yield np.frombuffer(buf, dtype='<f4').reshape(-1, ch)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode('utf8', 'replace').strip()
        proc.stderr.close()
        code = proc.wait()
    if code:
        raise OpError(f"ffmpeg could not read {path!r}: {err[-300:] or 'it has no audio stream?'}")


def _blocks(path, frames_per_block):
    """(sample rate, an iterator of float32 blocks, frames x channels). A file libsndfile reads (wav, flac, ogg) is
    read in place; any other (a video, m4a, mp3) goes through ffmpeg."""
    try:
        f = sf.SoundFile(path)
    except (sf.LibsndfileError, RuntimeError):
        return PIPE_SR, _pipe_blocks(path, frames_per_block)

    def read():
        with f:
            yield from f.blocks(blocksize=frames_per_block, dtype='float32', always_2d=True)
    return f.samplerate, read()


def _powers(path, win_s):
    """(mean square per window across channels, sample rate, seconds, peak dBFS), read in blocks so a file of hours
    never sits in memory whole. The last part window counts when it holds a tenth of a window or more."""
    sr, it = _blocks(path, 1 << 16)
    n = max(1, int(round(win_s * sr)))
    out, peak, frames, carry = [], 0.0, 0, np.zeros(0)
    for blk in it:
        if not len(blk):
            continue
        frames += len(blk)
        peak = max(peak, float(np.abs(blk).max()))
        sq = np.concatenate([carry, (blk.astype(np.float64) ** 2).mean(axis=1)])
        full = len(sq) // n
        if full:
            out.append(sq[:full * n].reshape(full, n).mean(axis=1))
        carry = sq[full * n:]
    if len(carry) >= max(1, n // 10):
        out.append(np.array([carry.mean()]))
    p = np.concatenate(out) if out else np.zeros(0)
    return p, sr, frames / sr, 20 * np.log10(peak + 1e-12)


def _db(p):
    return 10 * np.log10(np.asarray(p, dtype=np.float64) + 1e-20)


def _clock(t):
    """Seconds as m:ss.s, or h:mm:ss.s from an hour."""
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if s >= 59.95:                       # 59.96 s rounds to the next minute, not to "0:60.0"
        m, s = m + 1, 0.0
        if m == 60:
            h, m = h + 1, 0
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m)}:{s:04.1f}"


def _is_keyword(src):
    return src in _KEYWORD_SOURCES or src.startswith(_KEYWORD_PREFIXES)


def _open(project, srcs):
    """The Project, or None when every source is a file path that exists without one (a video assembly, a set's
    render saved elsewhere). A keyword source (render, track:<name> ...) needs the project and raises without it."""
    need = [s for s in srcs if _is_keyword(s)]
    if project is None:
        if need:
            raise OpError(f"{need[0]!r} is a project source: pass project=<the project folder>, or give a file path")
        return None
    try:
        return _load(project)
    except OpError:
        if need:
            raise
        return None


def _path(P, src):
    """An absolute path for an analysis source: render | ref | ref:<stem> | track:<name> | sound:<name> | a file
    (the working directory first, then the project)."""
    if not isinstance(src, str) or not src.strip():
        raise OpError(f"source {src!r}: give render | ref | ref:<stem> | track:<name> | sound:<name> | a file path")
    if P is None:
        p = os.path.abspath(src)
        if not os.path.isfile(p):
            raise OpError(f"audio file {src!r} not found (looked at {p}); pass an absolute path, or project=<folder> "
                          f"for render | track:<name> | sound:<name>")
        return p
    return P.resolve_audio(_cwd_first(P, src))


def _grid(P, src):
    """The project grid for a keyword source (bar numbers in the reply), else None (a file has no bars)."""
    if P is None or not _is_keyword(src):
        return None
    try:
        return P.source(src)[1]
    except (OpError, KeyError, ValueError):
        return None


# ------------------------------------------------------------------ silence

def _planned(planned):
    out = []
    for w in planned or []:
        try:
            a, b = float(w[0]), float(w[1])
        except (TypeError, ValueError, IndexError):
            raise OpError(f"planned={planned!r}: give a list of [start_s, end_s] windows, e.g. [[56.0, 60.0]]")
        if b <= a:
            raise OpError(f"planned window {list(w)}: the end must come after the start (seconds)")
        out.append((a, b))
    return out


def silences(path, min_s=3.0, floor_db=-50.0, planned=None, grid=None, label=None):
    """(data, text): every stretch of min_s seconds or more whose rms per 100 ms stays under floor_db. A stretch
    inside a planned [start_s, end_s] window (0.5 s of slack at each edge) is listed as planned and does not fail.
    data = {'ok', 'duration', 'stretches': [{'start', 'end', 'planned', 'quietest_db'}]}."""
    min_s, floor_db = float(min_s), float(floor_db)
    if min_s <= 0:
        raise OpError(f"min_s={min_s}: give the shortest silence that counts, in seconds (3 is the default)")
    plan = _planned(planned)
    p, sr, dur, _ = _powers(path, SILENCE_WIN_S)
    db = _db(p)
    quiet = np.concatenate([[False], db < floor_db, [False]])
    edges = np.flatnonzero(np.diff(quiet.astype(np.int8)))
    rows = []
    for i0, i1 in zip(edges[::2], edges[1::2]):
        a, b = i0 * SILENCE_WIN_S, min(i1 * SILENCE_WIN_S, dur)
        if b - a + 1e-9 < min_s:
            continue
        ok = any(pa - 0.5 <= a and b <= pb + 0.5 for pa, pb in plan)
        rows.append({'start': round(a, 2), 'end': round(b, 2), 'planned': ok, 'quietest_db': float(db[i0:i1].min())})
    bad = [r for r in rows if not r['planned']]
    name = label or os.path.basename(path)
    L = [f"silence scan of {name} ({_clock(dur)}, {sr} Hz): stretches of {min_s:g} s or more under {floor_db:g} dBFS "
         f"(rms per {SILENCE_WIN_S * 1000:.0f} ms)"]
    if not rows:
        L.append("  none")
    for r in rows[:40]:
        a, b = r['start'], r['end']
        where = ''
        if grid is not None:
            where = f"  bars {grid.time_to_bar(a):.1f}-{grid.time_to_bar(b):.1f}"
        edge = ' (the start)' if a <= 0 else ' (the end)' if b >= dur - SILENCE_WIN_S else ''
        quietest = 'digital silence' if r['quietest_db'] < -150 else f"quietest {r['quietest_db']:.0f} dBFS"
        L.append(f"  {_clock(a)}-{_clock(b)}  {b - a:.1f} s{where}  {quietest}{edge}"
                 + ('  planned' if r['planned'] else ''))
    if len(rows) > 40:
        L.append(f"  ... and {len(rows) - 40} more")
    if bad:
        total = sum(r['end'] - r['start'] for r in bad)
        r0 = bad[0]
        L.append(f"FAIL {len(bad)} unplanned silent stretch{'es' if len(bad) > 1 else ''} ({total:.1f} s in all). "
                 f"Fill or cut {'it' if len(bad) == 1 else 'them'}; if the plan calls for one, name it in "
                 f"planned=[[{r0['start']:g}, {r0['end']:g}]]")
    else:
        L.append("PASS no unplanned silent stretch")
    return {'ok': not bad, 'duration': dur, 'stretches': rows}, '\n'.join(L)


@op()
def analyze_silence(project: str = None, source: str = 'render', min_s: float = 3.0, floor_db: float = -50.0,
                    planned: list = None) -> str:
    """Silent stretches in audio of any length: every stretch of min_s seconds or more (default 3) whose rms per
    100 ms stays under floor_db (default -50 dBFS), with start and end times (and bars for a project source).
    source: render | ref | track:<name> | sound:<name> | a file path; a file needs no project (a video's rendered
    chapter, a whole assembly of hours, read in blocks). planned=[[start_s, end_s], ...]: silences the plan calls
    for, listed as planned and not failed. Ends with PASS, or FAIL and what to do. Run it on every rendered chapter
    and on a whole assembly before anyone reviews it; onair_check runs it with the source levels and a rules check.
    analyze_bars and analyze_envelope read levels on the bar grid (32 and 8 bars at most)."""
    P = _open(project, [source])
    path = _path(P, source)
    return silences(path, min_s, floor_db, planned, grid=_grid(P, source), label=source)[1]


# ------------------------------------------------------------------ named sources in the mix

def _named(sources):
    """[(label, source)] from a list of sources or a {label: source} dict."""
    if sources is None:
        return []
    if isinstance(sources, str):
        sources = [sources]
    if isinstance(sources, dict):
        return [(str(k), v) for k, v in sources.items()]
    if isinstance(sources, (list, tuple)):
        return [(str(s), s) for s in sources]
    raise OpError(f"sources={sources!r}: give a list (['track:strings', 'stems/foley.wav']) or a dict "
                  f"({{'theme': 'track:strings'}})")


def source_levels(P, mix_path, named, floor_db=-20.0, mix_src='render'):
    """(data, text): each named source's own rms and peak, how much of the mix it plays in, its level against the
    mix where it plays (rms over its own windows against the mix's in the same windows), and its share of the mix's
    energy. A source fails when it is silent or sits more than floor_db under the mix where it plays. Sources line
    up with the mix from its first sample (stems of the same render, or files cut to the same start)."""
    floor_db = float(floor_db)
    pm, _, mdur, _ = _powers(mix_path, LEVEL_WIN_S)
    mix_rms = float(_db(pm.mean())) if len(pm) else -200.0
    rows = []
    for label, src in named:
        path = _path(P, src)
        ps, _, sdur, peak = _powers(path, LEVEL_WIN_S)
        r = {'label': label, 'source': src, 'rms_db': float(_db(ps.mean())) if len(ps) else -200.0, 'peak_db': peak,
             'plays': 0.0, 'in_mix_db': None, 'share': 0.0, 'verdict': 'ok', 'note': ''}
        stale = (isinstance(src, str) and src.startswith('track:') and mix_src == 'render'
                 and not (os.path.getmtime(mix_path) - 60 <= os.path.getmtime(path) <= os.path.getmtime(mix_path) + 600))
        n = min(len(ps), len(pm))
        top = float(_db(ps.max())) if len(ps) else -200.0
        if top < GONE_DB or n == 0:
            r['verdict'] = 'silent'
        else:
            s, m = ps[:n], pm[:n]
            act = _db(s) > max(top - ACTIVE_RANGE_DB, GONE_DB)
            r['plays'] = float(act.mean())
            r['in_mix_db'] = float(_db(s[act].sum()) - _db(m[act].sum()))
            r['share'] = float(s.sum() / max(pm.sum(), 1e-20))
            if not act.any():
                r['verdict'] = 'silent'
            elif r['in_mix_db'] < floor_db:
                r['verdict'] = 'low'
        if stale:
            r['verdict'] = 'stale' if r['in_mix_db'] is not None else 'silent'
            r['note'] = 'its stem is from another render than the mix'
        elif abs(sdur - mdur) > 1.0:
            r['note'] = f"{_clock(sdur)} long against the mix's {_clock(mdur)}: read over the overlap"
        rows.append(r)
    L = [f"named sources in {mix_src} (mix rms {mix_rms:.1f} dBFS, {_clock(mdur)}; floor {floor_db:g} dB against the "
         f"mix where a source plays):",
         f"  {'source':<22} {'own rms':>7} {'peak':>6} {'plays':>6} {'in mix':>8} {'share':>6}  verdict"]
    for r in rows:
        if r['verdict'] == 'silent':
            pk = f"{r['peak_db']:6.1f}" if r['peak_db'] > -150 else f"{'-':>6}"
            L.append(f"  {r['label'][:22]:<22} {'silent':>7} {pk} {'-':>6} {'-':>8} {'-':>6}  SILENT"
                     + (f" ({r['note']})" if r['note'] else ''))
            continue
        v = {'ok': 'ok', 'low': f"LOW {floor_db - r['in_mix_db']:.1f} dB under the floor",
             'stale': 'STALE'}[r['verdict']]
        L.append(f"  {r['label'][:22]:<22} {r['rms_db']:7.1f} {r['peak_db']:6.1f} {r['plays'] * 100:5.0f}% "
                 f"{r['in_mix_db']:+7.1f} {r['share'] * 100:5.1f}%  {v}" + (f" ({r['note']})" if r['note'] else ''))
    return {'mix_rms_db': mix_rms, 'rows': rows, 'ok': all(r['verdict'] == 'ok' for r in rows)}, '\n'.join(L)


# ------------------------------------------------------------------ the caller's own rules

def _command(rules):
    """The rules check is a Python file (and its arguments), run with this Python: never a shell or another program,
    since any agent connected to ismail can call this op."""
    args = [str(c) for c in rules] if isinstance(rules, (list, tuple)) else [rules] if isinstance(rules, str) else []
    path = args[0] if args else ''
    if not (path.lower().endswith('.py') and os.path.isfile(path)):
        raise OpError(f"rules={rules!r}: give a Python file that exists ('set/norms_check.py'), or a list of it and its "
                      f"arguments (['set/norms_check.py', 'part3']); it runs with this Python and exits nonzero on a "
                      f"fail. Other programs and command lines are not run")
    return [sys.executable] + args


def run_rules(rules, mix_path, timeout_s=300.0):
    """(data, text): run the caller's rules file in the working directory, with ISMAIL_CHECK_MIX set to the mix's
    path. Exit 0 passes; any other exit or a timeout fails. The output's last lines
    are kept."""
    cmd = _command(rules)
    shown = ' '.join(cmd[1:] if cmd[0] == sys.executable else cmd)
    env = dict(os.environ, ISMAIL_CHECK_MIX=mix_path)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=float(timeout_s), env=env,
                             encoding='utf-8', errors='replace')
        code, out = res.returncode, (res.stdout or '') + (res.stderr or '')
    except subprocess.TimeoutExpired:
        code, out = None, f"(ran over {timeout_s:g} s and was stopped)"
    except OSError as e:
        code, out = None, f"(could not start: {e})"
    lines = [ln for ln in out.splitlines() if ln.strip()]
    tail = lines[-RULES_TAIL:]
    said = 'exit 0' if code == 0 else (f'exit {code}' if code is not None else 'did not run')
    L = [f"rules: {shown}: {said}"]
    if len(lines) > RULES_TAIL:
        L.append(f"  ... {len(lines) - RULES_TAIL} earlier lines")
    L += [f"  | {ln}" for ln in tail]
    return {'ok': code == 0, 'code': code, 'command': shown, 'tail': tail}, '\n'.join(L)


# ------------------------------------------------------------------ the verdict

def check(project=None, mix='render', sources=None, rules=None, floor_db=-20.0, min_s=3.0, silence_db=-50.0,
          planned=None, rules_timeout_s=300.0):
    """(ok, reasons, text) of the whole on-air check; onair_check and onair_gate both read it."""
    named = _named(sources)
    P = _open(project, [mix] + [s for _, s in named if isinstance(s, str)])
    mix_path = _path(P, mix)
    reasons, blocks = [], []
    sil, stxt = silences(mix_path, min_s, silence_db, planned, grid=_grid(P, mix), label=mix)
    blocks.append(stxt)
    for r in [r for r in sil['stretches'] if not r['planned']][:5]:
        reasons.append(f"silence {_clock(r['start'])}-{_clock(r['end'])} ({r['end'] - r['start']:.1f} s under "
                       f"{float(silence_db):g} dBFS). Next: fill it or cut it; if the plan calls for it, pass "
                       f"planned=[[{r['start']:g}, {r['end']:g}]]")
    if named:
        lv, ltxt = source_levels(P, mix_path, named, floor_db, mix_src=mix)
        blocks.append(ltxt)
        for r in lv['rows']:
            if r['verdict'] == 'silent':
                reasons.append(f"source {r['label']} is silent. Next: check that it is placed and plays (its track "
                               f"unmuted, its file or sample loaded), render again with stems=True, check again")
            elif r['verdict'] == 'low':
                need = floor_db - r['in_mix_db']
                reasons.append(f"source {r['label']} sits {-r['in_mix_db']:.1f} dB under the mix where it plays "
                               f"(the floor is {-floor_db:g} dB under it). Next: raise it {np.ceil(need):.0f} dB or more (its fader, "
                               f"track_set volume_db) or pull down what covers it, then check again")
            elif r['verdict'] == 'stale':
                reasons.append(f"source {r['label']}: its stem is from another render than the mix. Next: "
                               f"render(stems=True), then check again")
    else:
        blocks.append("named sources: none given (sources=['track:<name>', 'a/stem.wav'] measures each named part "
                      "in the mix)")
    if rules is not None:
        rr, rtxt = run_rules(rules, mix_path, rules_timeout_s)
        blocks.append(rtxt)
        if not rr['ok']:
            reasons.append(f"rules `{rr['command']}` {'exited ' + str(rr['code']) if rr['code'] is not None else 'did not run'}. "
                           f"Next: fix what it names (its output is below), then check again")
    ok = not reasons
    if ok:
        said = [f"{len(named)} source{'s' if len(named) != 1 else ''} over the floor" if named else 'no sources named',
                'no unplanned silence'] + (['rules exit 0'] if rules is not None else [])
        head = [f"on-air check of {mix}: PASS ({', '.join(said)})"]
    else:
        head = [f"on-air check of {mix}: FAIL ({len(reasons)} reason{'s' if len(reasons) > 1 else ''})"]
        head += [f"  {i}. {r}" for i, r in enumerate(reasons, 1)]
        head.append("It does not go on air (or to review) until this passes; onair_gate(..., force=True) airs it "
                    "anyway, by the person's choice.")
    return ok, reasons, '\n'.join(head + ['---'] + blocks)


@op()
def onair_check(project: str = None, mix: str = 'render', sources=None, rules=None, floor_db: float = -20.0,
                min_s: float = 3.0, silence_db: float = -50.0, planned: list = None,
                rules_timeout_s: float = 300.0) -> str:
    """Measure a render before it goes on air or to review, in one verdict: PASS, or FAIL with each reason and
    what to do next. Three readings:
    (1) silence: no stretch of min_s s (3) or more under silence_db (-50 dBFS) in the mix, except the planned
    [[start_s, end_s], ...] windows (analyze_silence);
    (2) sources: each named source (track:<name> after render(stems=True), or a file lined up with the mix, as a
    list or a {label: source} dict): its own rms and peak, how much of the mix it plays in, its level against the
    mix where it plays, and its share of the mix's energy. It fails when silent, or more than floor_db (-20 dB)
    under the mix where it plays;
    (3) rules: the caller's own check, a Python file, or a list of it and its arguments (run with this Python, never a shell or another program), run in the working
    directory with ISMAIL_CHECK_MIX set to the mix's path; a nonzero exit fails, and its last lines are shown.
    mix: render | a file path (no project needed for files). A set's or a video's code calls onair_gate, which
    raises on a fail unless force=True."""
    return check(project, mix, sources, rules, floor_db, min_s, silence_db, planned, rules_timeout_s)[2]


def onair_gate(project=None, mix='render', sources=None, rules=None, force=False, **kw):
    """The on-air check as a gate for a set's or a video's own code (not an op): returns the report on a pass,
    raises OnAirRefused (an OpError; str() is the report, .reasons the fails) on a fail. force=True returns the
    report with a FORCED line instead of raising: the person chose to air it anyway. kw: floor_db, min_s,
    silence_db, planned, rules_timeout_s, as onair_check.

        from ismail.api_onair import onair_gate, OnAirRefused
        onair_gate('proj', sources={'theme': 'track:strings'}, rules='set/rules_check.py')
    """
    ok, reasons, text = check(project, mix, sources, rules, **kw)
    if ok:
        return text
    if force:
        return text + f"\nFORCED: aired with {len(reasons)} fail{'s' if len(reasons) > 1 else ''} (force=True)"
    raise OnAirRefused("REFUSED " + text, reasons)
