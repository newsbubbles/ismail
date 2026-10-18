"""Reply lines built from live state (ledger:M245 part B, M247).

A session does not go in a straight line, so a reply never says "at step 8": it reads what is true now (the project,
its reference, the comparisons run, the phone page, the stage, a live engine, a feedback round) and offers ONE short
suggestion chosen by a condition over that state. The suggestion is an option, never an order: whether music goes on
a page, to the phone or hands over as a file is the agent's call.

Everything here is cheap and never raises: file reads, and at most a local HTTP request to a server that has a
registry file, with a short timeout (a server that does not answer is skipped in silence).

  reference_line(root, d)      render's line: the reference and whether it has been compared since this render
  reference_next()             project_set(reference=)'s lines: what to run next with a reference
  note_compared(root, op, ..)  compare, cmp_run and sound_compare leave a receipt here (renders/compared.json)
  hear_it(root, path, d)       the "hear it" block for render and sketch
  moves_line(d)                a full render's line when the song has no moves yet (no automation, no transitions)
  balance_lines(P)             render(stems=True)'s lines: each stem's share of the mix against the reference's,
                               over the song and in its worst stretch, and whether a mastering pass comes next
"""
import json
import os
import time
from pathlib import Path

COMPARED_FILE = 'compared.json'          # <project>/renders/compared.json: the last comparison with the reference
AUDIO = {'.wav', '.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.aac', '.aif', '.aiff', '.wma'}
PROBE_S = 0.4                            # a local server that does not answer this fast is skipped
PHONE_RECENT_S = 300                     # a phone that checked in this recently is in the person's pocket
PREF_KEY = 'listen_pref'                 # project.json: the person's standing choice of how they hear drafts


# ------------------------------------------------------------------ small readers

def _read_json(path, default=None):
    try:
        with open(path, encoding='utf8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _ago(seconds):
    s = max(0, int(seconds))
    if s < 90:
        return f"{s} s ago"
    if s < 5400:
        return f"{round(s / 60)} min ago"
    if s < 172800:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


def _alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except (ImportError, TypeError, ValueError):
        return pid is not None


def _get(port, path, timeout=PROBE_S):
    """GET http://127.0.0.1:<port>/<path> -> JSON or None, never raising."""
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/{path.lstrip("/")}', timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:                    # noqa: BLE001  down, slow, or not ours: skipped
        return None


def _song_root(root):
    from .sources import song_root
    return song_root(root)


def _same(a, b):
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


# ------------------------------------------------------------------ the reference (M245 part B)

def note_compared(root, op, what=''):
    """A comparison with the reference ran: keep its receipt, so render can say whether it ran since."""
    rd = os.path.join(str(root), 'renders')
    try:
        os.makedirs(rd, exist_ok=True)
        tmp = os.path.join(rd, f"{COMPARED_FILE}.{os.getpid()}.tmp")
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump({'t': time.time(), 'at': time.strftime('%Y-%m-%dT%H:%M:%S'), 'op': op, 'what': what}, f)
        os.replace(tmp, os.path.join(rd, COMPARED_FILE))
    except OSError:
        pass


def last_compared(root):
    return _read_json(os.path.join(str(root), 'renders', COMPARED_FILE))


def is_ref(src):
    return str(src or '').split(':')[0] == 'ref'


def ref_audio(root):
    """Audio in the song's ref/ folders (stems, separations and analysis projects left out), paths from the song."""
    from .sources import ref_dirs, _walk
    sr = _song_root(root)
    out = []
    for top in ref_dirs(root):
        for dirpath, files in _walk(top):
            out += [os.path.relpath(os.path.join(dirpath, f), sr).replace(os.sep, '/') for f in sorted(files)
                    if os.path.splitext(f)[1].lower() in AUDIO]
    return out


def reference_line(root, d):
    """One line for render's reply: whether the reference was compared since this render, or that ref/ holds audio
    the project does not use as its reference. '' when there is no reference at all."""
    try:
        ref = ((d or {}).get('reference') or {}).get('file')
        if ref:
            last = last_compared(root)
            when = ('never' if not last else
                    f"not since this render (last: {last.get('op')}{' ' + last['what'] if last.get('what') else ''}, "
                    f"{_ago(time.time() - float(last.get('t') or 0))})")
            return (f"compared with the reference ({os.path.basename(ref)}): {when}. Next: compare(bars=), then "
                    f"sound_compare on the part the person named; cmp_run stores a full run")
        files = ref_audio(root)
        if files:
            sr = _song_root(root)
            more = f" (and {len(files) - 1} more)" if len(files) > 1 else ''
            return (f"ref/ holds {files[0]}{more}, but it is not this project's reference: project_set(reference="
                    f"'{os.path.join(sr, files[0])}') makes compare, cmp_run, levels_from_ref and eq_match reachable")
    except Exception:                    # noqa: BLE001  a reply line never breaks the render that made it
        return ''
    return ''


def reference_next():
    return ["next with a reference: analyze_grid (tempo, bar 1, tuning), separate(source='ref') for its stems, "
            "analyze_structure(source='ref') for its roots (one root throughout is a pedal, not a progression); "
            "before each draft goes out, compare or cmp_run, and sound_compare on the part the person named",
            "levels_from_ref sets each stem's average level from it; the part that carries the reference's identity "
            "(the riff, the hook) is checked on top in its own window (sound_compare or region_measure), never "
            "averaged down to its stem"]


# ------------------------------------------------------------------ live state for "hear it" (M247)

def phone_state():
    """{'port', 'kind', 'last_s'} for the phone page server, or None when it is not running. 'last_s' is when the
    person's freshest page (not the headset's) last asked for its state; None when the server does not say."""
    home = Path(os.environ.get('ISMAIL_PHONE_HOME') or Path.home() / '.ismail' / 'phone')
    rec = _read_json(home / 'server.json') or {}
    port = rec.get('port')
    if not port or not _alive(rec.get('pid')):
        return None
    h = _get(port, 'health')
    if not h or not h.get('ok'):
        return None
    devs = [x for x in h.get('devices') or [] if x.get('kind') != 'Quest' and x.get('last_s') is not None]
    best = min(devs, key=lambda x: x['last_s'], default=None)
    return {'port': port, 'kind': best and best['kind'], 'last_s': best and best['last_s'],
            'says': 'devices' in h}


def stage_state():
    """{'port', 'scene', 'scenes'} when the person is in the headset on a running stage, else None."""
    reg = Path(os.environ.get('ISMAIL_STAGE_REGISTRY') or Path.home() / '.ismail' / 'stage')
    try:
        recs = [_read_json(f) or {} for f in reg.glob('*.json')]
    except OSError:
        return None
    for r in sorted(recs, key=lambda r: -float(r.get('started') or 0)):
        if not r.get('port') or not _alive(r.get('pid')):
            continue
        p = _get(r['port'], 'live/presence')
        if p and p.get('in_vr'):
            return {'port': r['port'], 'scene': p.get('scene'), 'scenes': r.get('scenes')}
    return None


def live_state(root):
    """A running live engine for this project or another project in the same song folder, else None."""
    try:
        from .listen import live_engines
        engines = live_engines()
    except Exception:                    # noqa: BLE001
        return None
    song = _song_root(root)
    for r in engines:
        p = r.get('project')
        if p and (_same(p, root) or _same(_song_root(p), song)):
            return r
    return None


def feedback_state(root):
    """The newest feedback round of this project (in the project or its song folder): {'round', 'state', 'url'};
    'url' only while the page server answers. None when it has none."""
    from . import feedback as FB
    dirs = []
    for top in dict.fromkeys([os.path.abspath(str(root)), os.path.abspath(_song_root(root))]):
        fb = Path(top) / 'work' / 'feedback'
        dirs += [x for x in (fb.glob('*') if fb.is_dir() else []) if (x / 'manifest.json').is_file()]
    if not dirs:
        return None
    d = max(dirs, key=lambda x: (x / 'manifest.json').stat().st_mtime)
    st = FB.sent_state(d)
    url = None
    srv = _read_json(FB.home() / 'server.json') or {}
    if srv.get('port') and _alive(srv.get('pid')) and _get(srv['port'], 'health'):
        rid = next((k for k, v in FB.rounds().items() if _same(v, d)), None)
        url = rid and f"http://127.0.0.1:{srv['port']}/f/{rid}/"
    return {'round': d.name, 'state': st['state'], 'url': url}


def _pref(root, d):
    p = (d or {}).get(PREF_KEY)
    if p is None:
        p = (_read_json(os.path.join(str(root), 'project.json')) or {}).get(PREF_KEY)
    return str(p).strip() if p else ''


def hear_it(root, path, d=None, what='render'):
    """The "hear it" block: the file, what is up right now to hear it on, and one option chosen from that state.
    root: the project (or song) folder; path: the file just written; d: its project.json, if loaded."""
    try:
        return _hear_it(root, path, d, what)
    except Exception:                    # noqa: BLE001  a reply block never breaks the render that made it
        return f"hear it: {os.path.abspath(str(path))}"


def _hear_it(root, path, d, what):
    path = os.path.abspath(str(path))
    root = os.path.abspath(str(root))
    q = lambda s: f"'{s}'"                # noqa: E731  a name or folder as the agent would pass it
    pref, stage, fb, phone, live = _pref(root, d), stage_state(), feedback_state(root), phone_state(), live_state(root)
    L = ["hear it (live state, for you; not to read out):", f"  <file>: {path}"]
    if stage:
        L.append(f"  stage: they are in the headset" + (f", scene {stage['scene']}" if stage.get('scene') else '')
                 + f" (port {stage['port']})")
    if phone:
        L.append(f"  phone page: up (port {phone['port']})"
                 + (f", their {phone['kind']} checked in {_ago(phone['last_s'])}" if phone['last_s'] is not None else
                    ", no phone has checked in since it started" if phone['says'] else ''))
    if live:
        L.append(f"  live engine: port {live.get('port')} for {live.get('project')}")
    if fb:
        L.append(f"  feedback page: round {fb['round']}, "
                 + {'sent': 'sent', 'changed': 'changed after sending', 'writing': 'not sent yet'}[fb['state']]
                 + (f", {fb['url']}" if fb['url'] else ', page server not running'))
    if pref:
        opt = f"their standing choice ({PREF_KEY} in project.json): {pref!r}. Follow it, and say you did"
    elif stage:
        scene = stage.get('scene') or '<scene>'
        where = os.path.join(stage['scenes'], scene) if stage.get('scenes') else 'the scene folder'
        opt = (f"they are in VR: a copy in {where} played with stage_music(scene={q(scene)}, "
               f"url='scenes/{scene}/<file name>') would play it in the room where they stand")
    elif fb and fb['state'] != 'sent':
        opt = (f"round {fb['round']} is open: feedback_page(project, round={q(fb['round'])}, items=[..., <file>]) "
               f"would add it to that page (their answers stay)")
    elif phone and phone['last_s'] is not None and phone['last_s'] <= PHONE_RECENT_S:
        opt = (f"their {phone['kind']} checked in {_ago(phone['last_s'])}: phone_offer(<file>, label=...) would "
               f"put it in their pocket")
    elif live:
        opt = (f"a live engine runs for this song: live_load(project={q(live.get('project'))}, deck=..., "
               f"song={q(root)}) would cue it on a deck")
    else:
        opt = ("play_file(<file>) opens it in their own player (say first that it plays out loud), or "
               "feedback_page(project, round=..., items=[<file>], questions=[...]) would put it on a page with "
               "questions and a Send button")
    L.append(f"  one option: {opt}")
    return '\n'.join(L)


# ------------------------------------------------------------------ balance against the reference (render, stems)

BALANCE_WIN = 8                          # bars per stretch: a part too loud in the drop only shows per stretch
BALANCE_SHOW_DB = 1.5                    # a stem whose share over the song is off by this much gets a line
BALANCE_WORST_DB = 3.0                   # ...or one stretch off by this much
ABSENT_DB = -35.0                        # a stem this far under the stretch's mix is not playing there
PRESENT_DB = -20.0                       # ...and this close to it is a layer you hear
QUIET_DB = -10.0                         # stretches this far under the reference's loudest one are left out


def _mono(path):
    import soundfile as sf
    y, sr = sf.read(path, always_2d=True, dtype='float32')
    return y.mean(axis=1), sr


def _ms(y, sr, g, a, b):
    i, j = max(int(g.bar_time(a) * sr), 0), max(int(g.bar_time(b) * sr), 0)
    seg = y[i:j]
    return float((seg.astype('float64') ** 2).mean()) if len(seg) else 0.0


def balance_lines(P, win=BALANCE_WIN):
    """Each mapped stem's share of the mix in your render against its share in the reference, per `win`-bar stretch
    and over the song: the line a too-loud part shows up in (a bass that is right in the intro and 9 dB hot in the
    drop averages out in a whole-song number). Names the loudest track of the stem in its worst stretch. Then one
    line on the master: a limiter alone is not a mastering pass. [] when there is no stem map, no reference stems
    or no rendered track stems. Never raises."""
    import math
    try:
        sm = P.d.get('stem_map') or {}
        ref = P.d.get('reference') or {}
        if not sm or not ref.get('stems_dir'):
            return []
        groups = {}
        for t, s in sm.items():
            if t in P.d.get('tracks', {}):
                try:
                    groups.setdefault(s, []).append((t,) + P.source(f'track:{t}')[:2])
                except Exception:        # noqa: BLE001  a track with no rendered stem is left out
                    pass
        rsrc = {}
        for s in list(groups):
            try:
                rsrc[s] = P.source(f'ref:{s}')[:2]
            except Exception:            # noqa: BLE001
                groups.pop(s)
        if len(groups) < 2:
            return []
        first = next(iter(groups.values()))[0]
        w = P.render_window(first[1])
        y0, sr0 = _mono(first[1])
        a = w['bars'][0] if w else 1
        b = w['bars'][1] + 1 if w else int(first[2].time_to_bar(len(y0) / sr0))
        stretches = [(x, min(x + win, b)) for x in range(a, b, win) if min(x + win, b) - x >= 2]
        if not stretches:
            return []
        own, ref_e, loud = {}, {}, {}
        for s, trs in groups.items():
            acc = None
            for t, path, g in trs:
                y, sr = _mono(path)
                loud[t] = [_ms(y, sr, g, x, z) for x, z in stretches]
                acc = y if acc is None else acc[:len(y)] + y[:len(acc)]
            own[s] = [_ms(acc, sr, trs[0][2], x, z) for x, z in stretches]
            y, sr = _mono(rsrc[s][0])
            ref_e[s] = [_ms(y, sr, rsrc[s][1], x, z) for x, z in stretches]
        db = lambda e: 10 * math.log10(e + 1e-20)
        tot_o = [sum(own[q][k] for q in groups) + 1e-20 for k in range(len(stretches))]
        tot_r = [sum(ref_e[q][k] for q in groups) + 1e-20 for k in range(len(stretches))]
        top_r = max(tot_r)
        loud_k = [k for k in range(len(stretches)) if db(tot_r[k] / top_r) > QUIET_DB]   # intros and outros out
        span = lambda ks: ', '.join(f"{stretches[k][0]}-{stretches[k][1] - 1}" for k in ks)
        rows, gone, extra = [], [], []
        for s in sorted(groups):
            diffs, wsum_o, wsum_r, miss, more = [], 0.0, 0.0, [], []
            for k in loud_k:
                so, sr_ = db(own[s][k] / tot_o[k]), db(ref_e[s][k] / tot_r[k])
                if sr_ > PRESENT_DB and so < ABSENT_DB:
                    miss.append(k)
                elif so > PRESENT_DB and sr_ < ABSENT_DB:
                    more.append(k)
                if so < ABSENT_DB or sr_ < ABSENT_DB:
                    continue
                diffs.append((so - sr_, k))
                wsum_o += own[s][k] / tot_o[k]
                wsum_r += ref_e[s][k] / tot_r[k]
            names = [t for t, _, _ in groups[s]]
            if miss:
                gone.append(f"  {s}: silent in bars {span(miss)}, where the reference's {s} plays (" +
                            f"{db(ref_e[s][miss[0]] / tot_r[miss[0]]):.0f} dB of the mix): a missing layer, or a part "
                            f"of yours filed under another stem (stem_map_set)")
            if more:
                extra.append(f"  {s}: plays in bars {span(more)}, where the reference's {s} is silent")
            if not diffs:
                continue
            whole = db(wsum_o) - db(wsum_r)
            worst, k = max(diffs, key=lambda d: abs(d[0]))
            if abs(whole) < BALANCE_SHOW_DB and abs(worst) < BALANCE_WORST_DB:
                continue
            x, z = stretches[k]
            top = max(names, key=lambda t: loud[t][k])
            word = lambda v: f"{abs(v):.1f} dB {'louder' if v > 0 else 'quieter'}"
            rows.append((abs(worst), f"  {s}: {word(whole)} than in the reference over its loud stretches; worst bars "
                                     f"{x}-{z - 1}: {word(worst)}" + (f" (loudest there: {top}, of {', '.join(names)})"
                                                                      if len(names) > 1 else f" ({top})")))
        L = []
        if rows or gone or extra:
            L.append(f"balance against the reference (each stem's share of the mix, {win}-bar stretches, "
                     f"quiet stretches left out):")
            L += [r for _, r in sorted(rows, reverse=True)] + gone + extra
            L.append("  levels_from_ref(bars=[a, b]) proposes the faders for a stretch; solo the named track before "
                     "moving a whole stem, then re-render with stems")
        else:
            L.append(f"balance against the reference: every stem within {BALANCE_SHOW_DB} dB over the song and "
                     f"{BALANCE_WORST_DB} dB in every loud {win}-bar stretch")
        mfx = [f.get('type') for f in (P.d.get('master') or {}).get('fx', [])]
        if not w and set(mfx) <= {'limiter'}:
            L.append("master: a limiter only, no mastering pass yet. " + (
                "Balance first (the lines above), then " if gone or (rows and max(rows)[0] >= BALANCE_WORST_DB) else "Next: ") +
                "the mastering pass, references/mastering.md (glue, mono low end, loudness for the reference)")
        return L
    except Exception:                    # noqa: BLE001  a reply line never breaks the render that made it
        return []


# ------------------------------------------------------------------ the effects pass (full renders)

def moves_line(d):
    """One line for a full render whose song has no moves yet: no automation lane anywhere (tracks, buses, master)
    or no transitions part. Two sessions handed over whole drafts with neither until the person asked for effects.
    '' once both are there. Never raises."""
    try:
        lanes = sum(len((x or {}).get('automation') or {}) for x in
                    list((d.get('tracks') or {}).values()) + list((d.get('buses') or {}).values()) + [d.get('master')])
        trans = any('transitions' in json.dumps(t.get('instrument') or '') for t in (d.get('tracks') or {}).values())
        if lanes and trans:
            return ''
        have = ', '.join(x for x in ((f"{lanes} automation lanes" if lanes else 'no automation'),
                                     ('a transitions part' if trans else 'no transitions')))
        return (f"moves: {have}. A full draft without moves sounds static; the effects pass (SKILL.md loop, step 5): "
                f"a transition into each section (voice 'transitions'), what changes at phrase ends (automation_set on "
                f"a filter, a send or a volume; a fill; a drop-out), the ducks under the kick, and the space (width, a reverb "
                f"send for depth, fx 'spatial' to place or fly a part, with doppler)")
    except Exception:                    # noqa: BLE001
        return ''
