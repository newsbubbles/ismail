"""tell_*: the tell ledger, per project (ledger:M305).

A tell is a fault the person keeps hearing in a sound, in their words ("boxy", "the distortion type is wrong"). The
metal song work found one tell named 7 times over 3 days while nothing showed it had stopped improving. The ledger
keeps one append-only event log per song, <song folder>/notes/tells.jsonl (a song's proj/ writes to its parent, as
feedback rounds do), and folds it into a table per tell: every time it was named, and the measured gap each round.

Events (one JSON object per line, the Sound Engineer's prototype shape, songs/_metal/work/tells.py):
  {seq, at, ev, id, when, ...}  seq = line number, at = when it was logged (UTC), when = when it happened (UTC)
  add      what, said, source, where     a new tell
  heard    said, source                  he named it again
  measure  metric, ours, target, gap,    one measured row; gap = ours - target unless given
           round, source, from, pulled   (prototype rows carry 'record' for target: read the same)
  judged   result, said, source, round   his verdict: passed, failed or better (prototype rows: caught, mixed)
  status   to, why                       open or closed (prototype rows: moving counts as open, parked as not)

Rules: the log is only appended to, through these ops. Closing needs a "passed" judged event after the last measure.
tell_show flags NOT MOVING on an open tell named twice or more whose gap did not shrink between its last two
measures, or that was never measured (or measured once). A NOT MOVING tell gets a different method next round.
"""
import json
import os
import re
from datetime import datetime, timezone

from .api import OpError, op

LOG_NAME = os.path.join('notes', 'tells.jsonl')
ID_RE = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
VERDICTS = ('passed', 'failed', 'better')
LEGACY_VERDICTS = ('caught', 'mixed')          # the prototype's words, read but not written
STATES = ('open', 'closed')
LEGACY_STATES = ('moving', 'parked')
OPEN_STATES = ('open', 'moving')
EVENTS = ('add', 'heard', 'measure', 'judged', 'status')
MAX_SAID_WORDS = 20
MIN_SHRINK = 0.5


# ---------------------------------------------------------------- where, when, the log

def ledger_path(project):
    """<song folder>/notes/tells.jsonl: a song's proj/ keeps its ledger in the song folder (proj/ is rebuilt)."""
    p = str(project or '').strip()
    if not p or not os.path.isdir(p):
        raise OpError(f"no folder {project!r}: pass the song's folder (or its proj/), e.g. songs/<slug>")
    from .sources import song_root
    return os.path.join(song_root(p), LOG_NAME)


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _when(s):
    t = str(s).strip()
    if t.endswith('Z'):
        t = t[:-1] + '+00:00'
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        raise OpError(f"not an ISO time: {s!r}; use UTC like 2026-10-23T19:55:36Z (a voice note's inbox time)")
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # the machine clock is UTC
    return d.astimezone(timezone.utc)


def _fmt(d):
    return d.strftime('%Y-%m-%dT%H:%M:%SZ')


def _read(path):
    """-> (events, unreadable lines as problems). A line that is not JSON is kept as a problem, not raised."""
    if not os.path.exists(path):
        return [], []
    evs, bad = [], []
    with open(path, encoding='utf-8') as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except ValueError as e:
                bad.append(f"line {i}: not JSON ({e}). Next: fix that line by hand (a row the ledger cannot read is "
                           f"the one edit the append-only rule allows), then tell_check")
                evs.append({'_line': i, '_bad': True})
                continue
            if not isinstance(ev, dict):
                bad.append(f"line {i}: not a JSON object. Next: fix that line by hand, then tell_check")
                evs.append({'_line': i, '_bad': True})
                continue
            ev['_line'] = i
            evs.append(ev)
    return evs, bad


def _append(path, ev):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    clean = {k: v for k, v in ev.items() if not k.startswith('_')}
    with open(path, 'a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(clean, ensure_ascii=False) + '\n')


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x


# ---------------------------------------------------------------- folding

def _key(e):
    return (_when(e['when']), e.get('seq', 0))


def _target(m):
    t = m.get('target', m.get('record'))
    return t if _num(t) else None


def gap_of(m):
    """The measured gap: the row's own gap, else ours minus target (prototype rows: ours minus record)."""
    if _num(m.get('gap')):
        return float(m['gap'])
    t = _target(m)
    return None if t is None or not _num(m.get('ours')) else float(m['ours']) - float(t)


def fold(events):
    """Tells by id, each with its events in 'when' order (seq breaks ties), and its state."""
    tells = {}
    for ev in events:
        if ev.get('_bad') or ev.get('ev') not in EVENTS:
            continue
        tid = ev.get('id')
        if ev['ev'] == 'add' and tid not in tells:
            tells[tid] = {'id': tid, 'add': ev, 'events': [], 'state': 'open'}
        if tid in tells:
            tells[tid]['events'].append(ev)
    for t in tells.values():
        try:
            t['events'].sort(key=_key)
        except (OpError, KeyError):
            pass
        st = [e for e in t['events'] if e['ev'] == 'status']
        if st:
            t['state'] = max(st, key=lambda e: e.get('seq', 0)).get('to')
    return tells


def _named(t):
    return [e for e in t['events'] if e['ev'] in ('add', 'heard')]


def _measures(t):
    return [e for e in t['events'] if e['ev'] == 'measure']


def can_close(t):
    evs = t['events']
    last_m = max((i for i, e in enumerate(evs) if e['ev'] == 'measure'), default=-1)
    return any(e['ev'] == 'judged' and e.get('result') == 'passed' for e in evs[last_m + 1:])


def movement(t, min_shrink=MIN_SHRINK):
    """(moving, why). Per metric of the latest measured round (or the latest measure when rounds are not named),
    the last two gaps: it moved when |gap| fell by more than min_shrink on any of them."""
    ms = _measures(t)
    if not ms:
        return False, 'never measured'
    last = ms[-1]
    latest = [m for m in ms if m.get('round') == last.get('round')] if last.get('round') else [last]
    shrank, stuck, single = [], [], []
    for metric in dict.fromkeys(m.get('metric') for m in latest):
        rows = [m for m in ms if m.get('metric') == metric and gap_of(m) is not None]
        if len(rows) < 2:
            single.append(metric)
        elif abs(gap_of(rows[-2])) - abs(gap_of(rows[-1])) > min_shrink:
            shrank.append(metric)
        else:
            stuck.append(metric)
    if shrank:
        return True, 'shrank: ' + '; '.join(shrank)
    if stuck:
        return False, 'gap did not shrink: ' + '; '.join(stuck)
    return False, 'only one measure on: ' + '; '.join(single)


def not_moving(t, min_shrink=MIN_SHRINK):
    moving, why = movement(t, min_shrink)
    return (t['state'] in OPEN_STATES and len(_named(t)) >= 2 and not moving), why


# ---------------------------------------------------------------- validation

def _said_problem(said):
    if not str(said or '').strip():
        return "his words are empty: quote what he said"
    n = len(str(said).split())
    if n > MAX_SAID_WORDS:
        return f"his words run {n} words: quote the {MAX_SAID_WORDS} or fewer that name the tell, exact"
    return None


def validate(events):
    """Problems in the log, each ending in what to do next. An empty list means the log is sound."""
    problems = []
    known = {}
    prev_at = None
    for i, ev in enumerate(events, 1):
        if ev.get('_bad'):
            continue
        line = ev.get('_line', i)
        where = f"line {line} (seq {ev.get('seq', '?')})"
        if ev.get('seq') != i:
            problems.append(f"{where}: seq should be {i}. Next: renumber seq to the line order by hand, then "
                            f"tell_check")
        kind = ev.get('ev')
        if kind not in EVENTS:
            problems.append(f"{where}: unknown event {kind!r}; events are {', '.join(EVENTS)}. Next: write it "
                            f"through the tell_{kind if kind in EVENTS else '<event>'} op instead, and fix the line")
            continue
        tid = ev.get('id', '')
        try:
            at = _when(ev['at'])
            when = _when(ev['when'])
        except (KeyError, OpError) as e:
            problems.append(f"{where}: a missing or bad time ({e}). Next: give the line 'at' (when logged) and "
                            f"'when' (when it happened), both UTC ISO")
            continue
        if prev_at and at < prev_at:
            problems.append(f"{where}: logged at {_fmt(at)}, before the line above ({_fmt(prev_at)}): times out of "
                            f"order. Next: the ops write in order; a line pasted in by hand goes at the end, with "
                            f"its real time in 'when'")
        prev_at = max(prev_at, at) if prev_at else at
        if when > at:
            problems.append(f"{where}: when {_fmt(when)} is after it was logged ({_fmt(at)}). Next: 'when' is when it "
                            f"happened, at or before 'at'")
        if kind == 'add':
            if not ID_RE.match(str(tid or '')):
                problems.append(f"{where}: id {tid!r} is not short kebab-case. Next: an id like dist-type")
            if tid in known:
                problems.append(f"{where}: {tid!r} added twice. Next: a repeat is tell_heard, not tell_add")
                continue
            known[tid] = ev
        elif tid not in known:
            near = ', '.join(sorted(known)) or 'none yet'
            problems.append(f"{where}: unknown id {tid!r}: no tell_add before it (known: {near}). Next: if it is a "
                            f"typo, correct the id on that line; if it is a new tell, its add line goes above it")
            continue
        elif when < _when(known[tid]['when']):
            problems.append(f"{where}: when {_fmt(when)} is before {tid} was first named ({known[tid]['when']}): "
                            f"times out of order. Next: an earlier naming belongs in the add")
        if kind in ('add', 'heard', 'judged') and (kind == 'heard' or ev.get('said')):
            p = _said_problem(ev.get('said'))
            if p:
                problems.append(f"{where}: {p}")
        if kind == 'heard' and not ev.get('source'):
            problems.append(f"{where}: heard without a source. Next: name where he said it (a voice note id, an "
                            f"exam, a picture round)")
        if kind == 'measure':
            if not ev.get('metric'):
                problems.append(f"{where}: measure without a metric. Next: name it, the same string every round")
            if not _num(ev.get('ours')):
                problems.append(f"{where}: measure 'ours' is not a number")
            if gap_of(ev) is None:
                problems.append(f"{where}: measure with neither a target nor a gap. Next: the row needs target= (the "
                                f"reference's number) or gap=")
        if kind == 'judged' and ev.get('result') not in VERDICTS + LEGACY_VERDICTS:
            problems.append(f"{where}: verdict {ev.get('result')!r}; verdicts are {', '.join(VERDICTS)}")
        if kind == 'status' and ev.get('to') not in STATES + LEGACY_STATES:
            problems.append(f"{where}: status {ev.get('to')!r}; a tell is {' or '.join(STATES)}")
    if not problems:
        for tid, t in fold(events).items():
            if t['state'] == 'closed' and not can_close(t):
                problems.append(f"{tid}: closed without a \"passed\" verdict after its last measure. Next: "
                                f"tell_status(id={tid!r}, status='open'), then a blind round and tell_judged")
    return problems


def _load(project, writing=False):
    path = ledger_path(project)
    events, bad = _read(path)
    if writing:
        problems = bad + validate(events)
        if problems:
            raise OpError(f"the tell ledger {path} has problems; nothing was written. Fix them first (tell_check):\n  "
                          + '\n  '.join(problems))
    return path, events


def _need(events, tid):
    tells = fold(events)
    if tid not in tells:
        raise OpError(f"no tell {tid!r} in this song (known: {', '.join(sorted(tells)) or 'none'}). Next: "
                      f"tell_add(id={tid!r}, what=...) if it is new, or use one of the known ids")
    return tells[tid]


def _write(path, events, kind, tid, when, **fields):
    at = _now()
    if events:
        last = [e for e in events if not e.get('_bad')]
        if last and at < _when(last[-1]['at']):
            raise OpError(f"the clock ({_fmt(at)}) is behind the ledger's last line ({last[-1]['at']}); check the "
                          f"machine clock, then call again")
    w = _when(when) if when else at
    if w > at:
        raise OpError(f"when {_fmt(w)} is in the future (now {_fmt(at)} UTC); give when it happened, in UTC")
    ev = {'seq': len(events) + 1, 'at': _fmt(at), 'ev': kind, 'id': tid, 'when': _fmt(w)}
    ev.update({k: v for k, v in fields.items() if v is not None})
    problems = validate(events + [ev])
    if problems:
        raise OpError('refused, the row would break the ledger: ' + '; '.join(problems))
    _append(path, ev)
    return ev


def _logged(ev, path):
    return f"logged #{ev['seq']} {ev['ev']} {ev['id']} ({ev['when']}) in {path}"


# ---------------------------------------------------------------- measuring ops' replies

_NUM = r'([-+]?\d+(?:\.\d+)?|nan)'


def _f(s):
    return float(s)


def parse_reply(text):
    """A measuring op's reply -> (op name, {key: (ours, target or None)}). Reads sound_compare (a = ours, b = the
    target), region_measure and sound_zoom_diff."""
    t = str(text or '')
    out = {}
    m = re.search(r'distance ' + _NUM + r' \(bands ' + _NUM + r' dB mean abs, envelope ' + _NUM +
                  r' dB mean abs, width ' + _NUM + r' vs ' + _NUM + r', pitch clarity ' + _NUM + r' vs ' + _NUM, t)
    if m:
        g = [_f(x) for x in m.groups()]
        out.update({'distance': (g[0], 0.0), 'bands': (g[1], 0.0), 'envelope': (g[2], 0.0),
                    'width': (g[3], g[4]), 'pitch clarity': (g[5], g[6])})
        for b in re.finditer(r'^\s*(\d+)H\s+' + _NUM + r'\s+' + _NUM + r'\s+[-+]\d', t, re.M):
            out[f'band {int(b.group(1))}'] = (_f(b.group(2)), _f(b.group(3)))
        c = re.search(r'centroid ' + _NUM + r' vs ' + _NUM + r' Hz', t)
        if c:
            out['centroid'] = (_f(c.group(1)), _f(c.group(2)))
        return 'sound_compare', out
    m = re.search(r'box [^:]*: ' + _NUM + r' dB; reference band [^:]*: ' + _NUM + r' dB; share ' + _NUM + r' dB', t)
    if m:
        out.update({'box': (_f(m.group(1)), None), 'reference band': (_f(m.group(2)), None),
                    'share': (_f(m.group(3)), None)})
        return 'region_measure', out
    for z in re.finditer(r'^\s*(grain|strokes|plumes): mean \|z\| ' + _NUM + r' \((?:no controls|controls ' + _NUM +
                         r')\)', t, re.M):
        out[z.group(1)] = (_f(z.group(2)), _f(z.group(3)) if z.group(3) else 0.0)
    if out:
        return 'sound_zoom_diff', out
    return None, {}


def _norm_key(k):
    k = re.sub(r'\s+', ' ', str(k or '').strip().lower())
    b = re.match(r'^(?:band ?)?(\d+)(?: ?(?:h|hz))?$', k)
    return f'band {int(b.group(1))}' if b else k


def _from_json(data, key):
    cur = data
    for part in key.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    if _num(cur):
        return float(cur), None
    if isinstance(cur, (list, tuple)) and len(cur) == 2 and all(_num(x) for x in cur):
        return float(cur[0]), float(cur[1])
    if isinstance(cur, dict) and _num(cur.get('ours')):
        return float(cur['ours']), (float(cur['target']) if _num(cur.get('target')) else None)
    return None


def pull_number(project, reply, key):
    """(ours, target or None, op name, where from) from a reply's text or a saved report's path."""
    text, origin = str(reply), None
    if '\n' not in text and len(text) < 500:
        p = text if os.path.isabs(text) else os.path.join(str(project), text)
        if os.path.isfile(p):
            origin = p
            with open(p, encoding='utf-8') as f:
                text = f.read()
            if p.lower().endswith('.json'):
                try:
                    data = json.loads(text)
                except ValueError as e:
                    raise OpError(f"{p} does not read as JSON ({e}); pass the op's reply text instead")
                got = _from_json(data, key)
                if got is None:
                    raise OpError(f"{p} has no number at {key!r}; name the key with pull= (a dotted path)")
                return got[0], got[1], 'report', origin
    name, vals = parse_reply(text)
    if name is None:
        raise OpError("reply: not a reply I can read (sound_compare, region_measure, sound_zoom_diff, or a saved "
                      ".json report). Next: pass ours= (and target=) as numbers, or the op's whole reply")
    k = _norm_key(key)
    if k not in vals:
        raise OpError(f"{name}'s reply has no {key!r}; it has: {', '.join(vals)}. Next: pull= one of those "
                      f"(metric stays your name for the row)")
    ours, target = vals[k]
    if ours != ours:
        raise OpError(f"{name}'s {k} is nan (silence or too short a window); measure a window where it plays")
    return ours, target, name, origin


# ---------------------------------------------------------------- ops

@op(mutates=True)
def tell_add(project: str, id: str, what: str, words: str = None, source: str = None, when: str = None,
             where: str = None) -> str:
    """Log a new tell: a fault the person hears in a sound and names ("boxy", "the distortion type is wrong"), in
    the song's tell ledger (<song>/notes/tells.jsonl, append-only). id: short kebab-case (dist-type); what: the
    fault in your words; words: theirs, verbatim, 20 words or fewer; source: where they said it (a voice note id,
    an exam, a picture round); when: UTC ISO of when they said it (default now); where: the song, clip or window.
    Reach for it the first time the person names a fault in how something sounds; a repeat is tell_heard."""
    tid = str(id or '').strip()
    if not ID_RE.match(tid) or len(tid) > 32:
        raise OpError(f"id {id!r}: use short kebab-case, e.g. dist-type")
    if not str(what or '').strip():
        raise OpError("what: the fault in a few words, e.g. 'the distortion type is wrong'")
    if words is not None:
        p = _said_problem(words)
        if p:
            raise OpError(f"words: {p}")
    path, events = _load(project, writing=True)
    if tid in fold(events):
        raise OpError(f"{tid} is already in the ledger. Next: tell_heard(id={tid!r}, words=..., source=...) logs "
                      f"that it was named again")
    return _logged(_write(path, events, 'add', tid, when, what=what, said=words, source=source, where=where), path)


@op(mutates=True)
def tell_heard(project: str, id: str, words: str, source: str, when: str = None) -> str:
    """The person named a tell again: their words (verbatim, 20 words or fewer), where (a voice note id, an exam,
    a picture round) and when (UTC ISO, default now). A tell named twice whose gap does not shrink shows NOT MOVING
    in tell_show."""
    p = _said_problem(words)
    if p:
        raise OpError(f"words: {p}")
    if not str(source or '').strip():
        raise OpError("source: where they said it (a voice note id, an exam, a picture round)")
    path, events = _load(project, writing=True)
    _need(events, id)
    ev = _write(path, events, 'heard', id, when, said=words, source=source)
    t = fold(events + [ev])[id]
    stuck, why = not_moving(t)
    return _logged(ev, path) + (f"\nNOT MOVING: named {len(_named(t))}x, {why}. Next round tries a different method, "
                                f"not the same one again" if stuck else '')


@op(mutates=True)
def tell_measure(project: str, id: str, metric: str, ours: float = None, target: float = None, gap: float = None,
                 source: str = None, reply: str = None, pull: str = None, target_reply: str = None,
                 round: str = None, when: str = None) -> str:
    """One measured row for a tell: ours against the target on one metric (the same metric name every round), so
    the ledger sees whether the gap shrinks. gap = ours - target unless given (a distance is its own gap: target=0).
    Instead of typing numbers, pass a measuring op's reply (or the path of a saved reply or .json report) as
    reply=: sound_compare(a=ours, b=the reference) gives distance, bands, envelope, width, pitch clarity, centroid
    and 'band <Hz>'; region_measure gives share, box, reference band (its target from target_reply=, the same
    measure on the reference, or target=); sound_zoom_diff gives grain, strokes, plumes (mean |z|, target the
    controls'). pull: which number (default the metric). round: the round's name; source: the files or run.
    Reach for it every round, once per open tell."""
    if not str(metric or '').strip():
        raise OpError("metric: the measure's name with its unit, the same string every round")
    pulled = origin = op_name = None
    if reply is not None:
        if ours is not None:
            raise OpError("pass ours= or reply=, not both")
        ours, t_r, op_name, origin = pull_number(project, reply, pull or metric)
        pulled = _norm_key(pull or metric)
        if target is None and t_r is not None:
            target = t_r
    if target_reply is not None:
        if target is not None and reply is None:
            raise OpError("pass target= or target_reply=, not both")
        target = pull_number(project, target_reply, pull or metric)[0]
    if not _num(ours):
        raise OpError("ours: your number, or reply= with a measuring op's reply")
    if target is None and gap is None:
        raise OpError("the row needs target= (the reference's number) or gap=; from region_measure pass "
                      "target_reply= with the same measure on the reference")
    if target is not None and not _num(target) or gap is not None and not _num(gap):
        raise OpError("target and gap are numbers")
    path, events = _load(project, writing=True)
    _need(events, id)
    g = float(gap) if gap is not None else float(ours) - float(target)
    ev = _write(path, events, 'measure', id, when, round=round, metric=metric, ours=float(ours),
                target=None if target is None else float(target), gap=g, source=source or origin,
                **{'from': op_name, 'pulled': pulled})
    t = fold(events + [ev])[id]
    rows = [m for m in _measures(t) if m.get('metric') == metric and gap_of(m) is not None]
    trend = ''
    if len(rows) >= 2:
        a, b = abs(gap_of(rows[-2])), abs(gap_of(rows[-1]))
        trend = f"; |gap| {a:.2f} -> {b:.2f}, {'shrank' if a - b > MIN_SHRINK else 'did not shrink'}"
    stuck, why = not_moving(t)
    return (_logged(ev, path) + f"\n{metric}: ours {float(ours):+.2f}"
            + ('' if target is None else f", target {float(target):+.2f}") + f", gap {g:+.2f}{trend}"
            + (f"\nNOT MOVING: named {len(_named(t))}x, {why}. Next round tries a different method" if stuck else ''))


@op(mutates=True)
def tell_judged(project: str, id: str, verdict: str, source: str, words: str = None, round: str = None,
                when: str = None) -> str:
    """The person's verdict on a tell after a blind round: passed (they could not tell it), failed (they caught it)
    or better (still there, less). source: the exam, panel or voice note; words: theirs, 20 words or fewer; when:
    UTC ISO (default now). Closing a tell needs a passed verdict after its last measure."""
    v = str(verdict or '').strip().lower()
    if v not in VERDICTS:
        raise OpError(f"verdict {verdict!r}: one of {', '.join(VERDICTS)}")
    if not str(source or '').strip():
        raise OpError("source: the exam, panel or voice note the verdict came from")
    if words is not None:
        p = _said_problem(words)
        if p:
            raise OpError(f"words: {p}")
    path, events = _load(project, writing=True)
    _need(events, id)
    ev = _write(path, events, 'judged', id, when, round=round, result=v, said=words, source=source)
    hint = f"\nNext: tell_status(id={id!r}, status='closed') if it is done" if v == 'passed' else ''
    return _logged(ev, path) + hint


@op(mutates=True)
def tell_status(project: str, id: str, status: str, why: str = None, when: str = None) -> str:
    """Open or close a tell. Closing is refused without a "passed" tell_judged verdict after its last measure (a
    new measure after the pass needs a new blind round)."""
    s = str(status or '').strip().lower()
    if s not in STATES:
        raise OpError(f"status {status!r}: {' or '.join(STATES)}")
    path, events = _load(project, writing=True)
    t = _need(events, id)
    if s == 'closed' and not can_close(t):
        raise OpError(f"{id} stays {t['state']}: no \"passed\" verdict after its last measure. Next: a blind round "
                      f"on it, then tell_judged(id={id!r}, verdict='passed', source=...) if they cannot tell it")
    return _logged(_write(path, events, 'status', id, when, to=s, why=why), path)


def _n(x):
    return '' if x is None else f'{x:+.2f}'


def _render(t, min_shrink):
    stuck, why = not_moving(t, min_shrink)
    add = t['add']
    named = _named(t)
    L = [f"== {t['id']}  [{t['state']}]  named {len(named)}x" + (f"   NOT MOVING ({why})" if stuck else '')]
    for k in ('what', 'where'):
        if add.get(k):
            L.append(f"   {k}: {add[k]}")
    for e in named:
        said = f"\"{e['said']}\"" if e.get('said') else '(no words)'
        L.append(f"   {e['when']}  {said}" + (f"  ({e['source']})" if e.get('source') else ''))
    ms = _measures(t)
    if not ms:
        L.append('   no measures yet')
    for metric in dict.fromkeys(m.get('metric') for m in ms):
        rows = [m for m in ms if m.get('metric') == metric][-4:]
        L.append(f"   metric: {metric}" + (f" (last {len(rows)})" if len(rows) == 4 else ''))
        L.append(f"     {'round':<18} {'when':<20} {'target':>8} {'ours':>8} {'gap':>8}  shrank")
        prev = None
        allrows = [m for m in ms if m.get('metric') == metric]
        start = len(allrows) - len(rows)
        if start:
            prev = gap_of(allrows[start - 1])
        for m in rows:
            g = gap_of(m)
            sh = '-' if prev is None or g is None else ('yes' if abs(prev) - abs(g) > min_shrink else 'no')
            L.append(f"     {str(m.get('round') or ''):<18} {m['when']:<20} {_n(_target(m)):>8} {_n(m.get('ours')):>8} "
                     f"{_n(g):>8}  {sh}")
            prev = g
    js = [e for e in t['events'] if e['ev'] == 'judged']
    if js:
        last = js[-1]
        L.append('   verdicts: ' + ', '.join(f"{e.get('round') or e['when'][:10]} {e.get('result')}" for e in js))
        L.append(f"   last verdict: {last.get('result')} {last['when']}"
                 + (f" \"{last['said']}\"" if last.get('said') else ''))
    else:
        L.append('   last verdict: none')
    L.append(f"   movement: {why}")
    return L, stuck


@op()
def tell_show(project: str, open_only: bool = True, id: str = None, min_shrink: float = MIN_SHRINK) -> str:
    """The song's tell ledger as a table per tell: every time the person named it, its last measures (target, ours,
    gap, shrank) and verdicts. Flags NOT MOVING on an open tell named twice or more whose gap did not shrink (by more
    than min_shrink) between its last two measures, or that was never measured. Run it before every round; a NOT
    MOVING tell gets a different method, not another try of the same one. open_only=False shows closed ones too."""
    path, events = _load(project)
    tells = fold(events)
    if id:
        if id not in tells:
            raise OpError(f"no tell {id!r} (known: {', '.join(sorted(tells)) or 'none'})")
        sel = [tells[id]]
    else:
        sel = [t for t in tells.values() if not open_only or t['state'] in OPEN_STATES]
        sel.sort(key=lambda t: (t['state'] not in OPEN_STATES, t['add'].get('seq', 0)))
    if not tells:
        return (f"no tells yet in {path}. When the person names a fault in a sound: tell_add(id=, what=, words=, "
                f"source=)")
    L = [f"Tell ledger {path}: {len(sel)} of {len(tells)} tell(s){' (open)' if open_only and not id else ''}; "
         f"gap = ours - target; shrank = |gap| fell by more than {min_shrink}"]
    flagged = []
    for t in sel:
        block, stuck = _render(t, min_shrink)
        L += [''] + block
        if stuck:
            flagged.append(t['id'])
    L += ['', 'NOT MOVING: ' + (', '.join(flagged) + '. Each gets a different method next round, not another try of '
                                'the same one' if flagged else 'none')]
    bad = _read(path)[1] + validate(events)
    if bad:
        L.append(f"the log has {len(bad)} problem(s): tell_check")
    return '\n'.join(L)


@op()
def tell_check(project: str) -> str:
    """Validate the song's tell ledger: unknown ids, times out of order, rows that are not JSON or miss a field, and
    closed tells without a pass. Each problem says what to do next. The write ops refuse while it has problems."""
    path, events = _load(project)
    problems = _read(path)[1] + validate(events)
    if problems:
        raise OpError(f"{len(problems)} problem(s) in {path}:\n  " + '\n  '.join(problems))
    return (f"ok: {path}: {len(events)} events, {len(fold(events))} tells; ids known, times in order, closed tells "
            f"have a pass")


@op(mutates=True)
def tell_import(project: str, path: str) -> str:
    """Adopt an existing tells.jsonl (the prototype's shape, e.g. one kept by hand in a song's notes) as this song's
    tell ledger. It is checked first, and goes only into an empty ledger; the source file is left as it is. A song
    whose ledger is already at <song>/notes/tells.jsonl needs no import."""
    src = str(path or '')
    if not os.path.isabs(src) and not os.path.isfile(src):
        src = os.path.join(str(project), src)
    if not os.path.isfile(src):
        raise OpError(f"no file {path!r}")
    dest = ledger_path(project)
    if os.path.exists(dest) and os.path.samefile(src, dest):
        return f"already in place: {dest} is this song's tell ledger; tell_check and tell_show read it as it is"
    events, bad = _read(src)
    problems = bad + validate(events)
    if problems:
        raise OpError(f"{src} has {len(problems)} problem(s); nothing was imported. Fix them in a copy, then "
                      f"import the copy:\n  " + '\n  '.join(problems))
    if _read(dest)[0]:
        raise OpError(f"{dest} already has events; the ledger is append-only, so an import goes only into an empty one")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(src, encoding='utf-8') as f, open(dest, 'w', encoding='utf-8', newline='\n') as g:
        for line in f:
            if line.strip():
                g.write(line.rstrip('\r\n') + '\n')
    return f"imported {len(events)} events, {len(fold(events))} tells from {src} into {dest}\n" + tell_show(project)
