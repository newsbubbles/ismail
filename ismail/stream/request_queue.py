"""request_* ops: one queue of chat's song requests that the MC fills and the DJ plays from (ledger:M290), with votes
that move a request up and credit lines for the viewers who asked (ledger:M291).

The queue is an append-only event log, requests.jsonl in the stream home: {ev: add | mark | vote | love | cmd, id, at,
...}, replayed on every read. So it needs no server, survives a restart, and an MC and a DJ in separate processes see
the same line. Writes hold a lock file while an id is chosen and its line appended."""
import json
import os
import statistics
import time
from contextlib import contextmanager
from pathlib import Path

from ..api import OpError, op
from . import config as C, ops as S
from .request_log import _events, line_up, log_path, state, viewer_key  # noqa: F401 (the replay, kept light)

STATUSES = ('waiting', 'playing', 'done', 'skipped')
MAX_WHAT = 300


@contextmanager
def _lock(timeout=10.0):
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = str(p) + '.lock'
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 30:      # a holder that died mid-write
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.time() - t0 > timeout:
                raise OpError(f"the request queue {lock} stayed locked for {timeout:.0f} s; try again")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        os.remove(lock)


def _append(ev):
    ev = {'at': round(time.time(), 2), 'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
          **{k: v for k, v in ev.items() if v is not None}}
    p = log_path()
    try:
        with open(p, 'rb') as f:
            f.seek(-1, os.SEEK_END)
            cut = f.read(1) != b'\n'            # a crash mid-write left half a line: start a fresh one
    except OSError:
        cut = False
    with open(p, 'a', encoding='utf8') as f:
        f.write(('\n' if cut else '') + json.dumps(ev, ensure_ascii=False) + '\n')
    return ev


def _playing(reqs):
    return [r for r in reqs.values() if r['status'] == 'playing']


def _ago(s):
    s = max(0, int(s))
    return f'{s} s' if s < 60 else f'{s // 60} min' if s < 3600 else f'{s // 3600} h {s % 3600 // 60} min'


def _desc(r):
    n = len(r['votes'])
    return (f"#{r['id']} {r['what']!r} for {r['who']}" + (f" ({r['venue']})" if r.get('venue') else '')
            + (f", {n} vote{'s' * (n != 1)}" if n else ''))


def handled():
    """The chat line numbers already acted on (chat_commands), so a line is never handled twice."""
    return {e['src'] for e in _events() if e.get('src') is not None}


def add(who, what, venue=None, note=None, user_id=None, src=None):
    """Append a request (the caller holds _lock) and return its id."""
    rid = max(state(), default=0) + 1
    _append({'ev': 'add', 'id': rid, 'who': str(who)[:80], 'what': what, 'venue': venue, 'note': note,
             'user_id': user_id, 'src': src})
    return rid


def position(rid, reqs):
    return [r['id'] for r in line_up(reqs)].index(rid) + 1


def _id(given, reqs):
    try:
        rid = int(str(given).strip().lstrip('#'))
    except ValueError:
        rid = None
    if rid not in reqs:
        wait = [str(r['id']) for r in line_up(reqs)]
        raise OpError(f"no request {given!r}" + (f"; the ids are {min(reqs)} to {max(reqs)}, waiting: "
                                              f"{', '.join(wait) or 'none'}" if reqs else '; the queue is empty')
                      + " (request_list(status='all') shows every one)")
    return rid


def _piece_s(reqs):
    """The median time a request played, from the last 5 that played to the end, or None."""
    took = sorted((r for r in reqs.values() if r['status'] == 'done' and r['started'] and r['ended']),
                  key=lambda r: r['ended'])[-5:]
    return statistics.median(r['ended'] - r['started'] for r in took) if took else None


def _eta(pos, reqs, now):
    """'about N min' until the request at position pos (1 = next) plays, or '' with no timing yet."""
    per = _piece_s(reqs)
    if per is None:
        return ''
    left = sum(max(0.0, per - (now - r['started'])) for r in _playing(reqs))
    return f", about {max(1, round((left + (pos - 1) * per) / 60))} min"


def _times(r):
    """' [queued 12:00:01Z, playing 12:04:10Z]': the UTC time of each state it went through."""
    got = [f"{k[:-3]} {r[k][11:]}" for k in ('queued_at', 'playing_at', 'done_at', 'skipped_at') if r.get(k)]
    return f" [{', '.join(got)}]" if got else ''


def _now_line(reqs, now):
    p = _playing(reqs)
    return 'playing: ' + ('; '.join(f"{_desc(r)}, for {_ago(now - r['started'])}"
                                    + (f", {r['loves']} !love" if r['loves'] else '') for r in p) if p else 'nothing')


@op(mutates=True)
def request_add(who: str, what: str, venue: str = None, note: str = None) -> str:
    """Put a viewer's song request in the queue the DJ plays from. Reach for it when chat asks for a song, a style or
    a change for the set. who: the viewer's name as chat shows it. what: the request in a few words ("deep house",
    "a sad cello over rain"). venue: where they asked (credit and replies). note: anything the DJ should know. Returns
    its id and its place in line; the DJ reads the line with request_next at a phrase end."""
    who, what = str(who or '').strip(), ' '.join(str(what or '').split())
    if not who or not what:
        raise OpError("request_add needs who= (the viewer's name) and what= (the request in a few words)")
    if len(what) > MAX_WHAT:
        raise OpError(f"what is {len(what)} characters; keep it under {MAX_WHAT}: the request, not the chat line")
    with _lock():
        rid = add(who, what, venue, note)
    reqs = state()
    pos = position(rid, reqs)
    now = time.time()
    return (f"queued {_desc(reqs[rid])}: position {pos} in line ({pos - 1} ahead{_eta(pos, reqs, now)}). "
            f"{_now_line(reqs, now)}")


@op()
def request_list(status: str = 'waiting') -> str:
    """The request queue: what plays now, then the waiting requests in the order they play (most votes first, then
    first asked), with their positions. status: waiting (the default), playing, done, skipped, or all (every
    request, newest last)."""
    if status not in STATUSES + ('all',):
        raise OpError(f"status {status!r}: one of {', '.join(STATUSES + ('all',))}")
    reqs, now = state(), time.time()
    if status == 'waiting':
        line = line_up(reqs)
        out = [_now_line(reqs, now), f"waiting: {len(line)}"]
        out += [f"  {i}. {_desc(r)}, asked {_ago(now - r['added'])} ago" + (f"; {r['note']}" if r.get('note') else '')
                + _times(r) for i, r in enumerate(line, 1)]
        return '\n'.join(out)
    pick = [r for r in sorted(reqs.values(), key=lambda r: r['id']) if status in ('all', r['status'])]
    if not pick:
        return f"no {status} requests" if status != 'all' else 'no requests yet'
    return '\n'.join(f"{_desc(r)}: {r['status']}, asked {_ago(now - r['added'])} ago"
                     + (f"; {r['note']}" if r.get('note') else '') + _times(r) for r in pick)


@op(mutates=True)
def request_mark(id: int, status: str, note: str = None, on_screen: bool = True) -> str:
    """Move a request along: playing (the DJ started it; one that was playing is marked done), done, skipped, or
    waiting (back in line). note: why, for the MC to tell the viewer ("played as a dnb mix"). on_screen: when it
    starts playing, the stream's `now` line says whose request it is ("request from @name: now playing ..."); the
    stream server must be up for that, and the mark counts either way. Each state's UTC time is kept
    (request_list(status='all') shows them)."""
    if status not in STATUSES:
        raise OpError(f"status {status!r}: one of {', '.join(STATUSES)}")
    with _lock():
        reqs = state()
        rid = _id(id, reqs)
        said = []
        if reqs[rid]['status'] == status:
            return f"{_desc(reqs[rid])} is already {status}"
        if status == 'playing':
            for r in _playing(reqs):
                _append({'ev': 'mark', 'id': r['id'], 'status': 'done', 'note': f"#{rid} started after it"})
                said.append(f"#{r['id']} marked done")
        _append({'ev': 'mark', 'id': rid, 'status': status, 'note': note})
    reqs = state()
    nxt = line_up(reqs)
    if status == 'playing' and on_screen:
        said.append(_on_screen(reqs[rid]))
    return '; '.join([f"{_desc(reqs[rid])}: {status}"] + said + [
        f"next: {_desc(nxt[0])}" if nxt else 'nobody waiting'])


def _on_screen(r):
    text = f"request from @{r['who']}: now playing {r['what']}"[:120]
    try:
        S.stream_text(slot='now', text=text)
    except OpError as e:
        return f"not on screen ({e})"
    return f"on screen: {text!r}"


@op()
def request_next() -> str:
    """The first waiting request, for the DJ to read at a phrase end: what, who, how long it waited, and how many are
    behind it. It changes nothing; request_mark(id, 'playing') when it starts."""
    reqs, now = state(), time.time()
    line = line_up(reqs)
    if not line:
        return f"no requests waiting. {_now_line(reqs, now)}"
    r = line[0]
    return (f"next: {_desc(r)}, waiting {_ago(now - r['added'])}" + (f"; note: {r['note']}" if r.get('note') else '')
            + f". {len(line) - 1} more behind it. {_now_line(reqs, now)}. When it starts: "
              f"request_mark({r['id']}, 'playing')")


@op()
def request_where(who: str, venue: str = None) -> str:
    """When a viewer's request plays: its place in line and about how long until it plays (timed from the requests
    played so far), or that it is playing or played. Reach for it when a viewer asks when theirs plays or how long
    until their request. who: their name as chat shows it (any case). venue: only theirs from that venue, when two
    sites have the same name."""
    reqs, now = state(), time.time()
    key = str(who or '').strip().lower()
    mine = [r for r in reqs.values() if str(r['who']).lower() == key and (venue is None or r.get('venue') == venue)]
    if not mine:
        names = sorted({r['who'] for r in line_up(reqs)})
        return f"no request from {who!r}" + (f"; waiting now: {', '.join(names)}" if names else '; nobody is waiting')
    line = [r['id'] for r in line_up(reqs)]
    out = []
    for r in sorted(mine, key=lambda r: r['id']):
        if r['status'] == 'waiting':
            pos = line.index(r['id']) + 1
            where = f"position {pos} in line, {pos - 1} ahead{_eta(pos, reqs, now)}"
        elif r['status'] == 'playing':
            where = f"playing now, for {_ago(now - r['started'])}"
        else:
            where = (f"{r['status']} {_ago(now - (r['ended'] or r['added']))} ago"
                     + (f" ({r['note']})" if r.get('note') else ''))
        out.append(f"{_desc(r)}: {where}{_times(r)}")
    return '\n'.join(out)


def vote_refused(r, key):
    """Why this viewer's vote for r does not count, or None."""
    if r['status'] != 'waiting':
        return f"#{r['id']} is not waiting (it is {r['status']})"
    if key == r['key']:
        return f"#{r['id']} is their own request"
    if key in r['votes']:
        return f"they voted for #{r['id']} already"
    return None


def vote(rid, key, src=None):
    """Append a vote (the caller holds _lock and checked vote_refused)."""
    _append({'ev': 'vote', 'id': rid, 'key': key, 'src': src})


def love(key, src=None):
    """Append a !love for what plays now (or for the set, when no request plays); returns that request's id."""
    rid = next((r['id'] for r in _playing(state())), None)
    _append({'ev': 'love', 'id': rid, 'key': key, 'src': src})
    return rid


def last_reward(key):
    """When this viewer last voted or loved (chat_commands' window), or None."""
    return max((e['at'] for e in _events() if e.get('ev') in ('vote', 'love') and e.get('key') == key), default=None)


@op(mutates=True)
def request_vote(id: int, who: str, venue: str = None) -> str:
    """Count a viewer's vote for a waiting request: more votes play sooner (ties go to the first asked). One vote
    per viewer per request, never for their own. chat_commands counts `!vote <id>` lines by itself; this is for a
    vote that came another way. who / venue: the voter, as chat shows them."""
    if not str(who or '').strip():
        raise OpError("request_vote needs who= (the voter's name as chat shows it)")
    with _lock():
        reqs = state()
        rid = _id(id, reqs)
        why = vote_refused(reqs[rid], viewer_key(venue, who))
        if why:
            raise OpError(f"vote not counted: {why}. request_list shows the waiting ones")
        vote(rid, viewer_key(venue, who))
    reqs = state()
    return f"counted: {_desc(reqs[rid])}, now position {position(rid, reqs)} in line"


CREDITS_HEAD = '## Requests from chat'


def credit_line(r, venues):
    plat = (venues.get(r.get('venue')) or {}).get('platform')
    where = (f"{r['venue']} on {plat}" if plat and plat != r['venue'] else r.get('venue')) or 'chat'
    return f"- {r['who']} ({where}): asked for \"{r['what']}\""


def _write_credits(p, lines):
    """Add the lines missing from p under CREDITS_HEAD (made at the end when it is not there); returns the added."""
    have = p.read_text(encoding='utf8').splitlines() if p.exists() else ['# Credits']
    new = [l for l in lines if l not in have]
    if not new:
        return []
    if CREDITS_HEAD not in have:
        have += ([''] if have and have[-1].strip() else []) + [CREDITS_HEAD, ''] + new
    else:
        end = next((i for i in range(have.index(CREDITS_HEAD) + 1, len(have)) if have[i].startswith('#')), len(have))
        while not have[end - 1].strip():
            end -= 1
        have[end:end] = new
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('\n'.join(have) + '\n', encoding='utf8')
    return new


@op(mutates=True)
def request_credits(ids: list = None, path: str = None) -> str:
    """Credit lines for the viewers whose requests shaped a song: their name, where they asked and what they asked
    for, for the song's CREDITS.md. Reach for it when a song made from chat's requests is finished. ids: those
    requests (default: every one that played). path: a CREDITS.md or a song folder (its CREDITS.md): the lines go
    under "## Requests from chat", each one once; without path they come back to paste. credits(overwrite=True)
    rewrites the file from SOURCES, so run this again after it. Name a viewer in a song only with their OK: ask them
    in chat first."""
    reqs = state()
    if ids is None:
        pick = [r for r in sorted(reqs.values(), key=lambda r: r['id']) if r['status'] in ('playing', 'done')]
        if not pick:
            return "no request has played yet: credit lines come from played requests, or name them with ids=[...]"
    else:
        pick = [reqs[_id(i, reqs)] for i in ([ids] if isinstance(ids, (int, str)) else ids)]
    venues = C.load_venues()
    lines = list(dict.fromkeys(credit_line(r, venues) for r in pick))
    if not path:
        return '\n'.join(lines)
    p = Path(path)
    if p.is_dir() or not p.suffix:
        p = p / 'CREDITS.md'
    new = _write_credits(p, lines)
    if not new:
        return f"{p}: every line is there already"
    return f"{p}: added {len(new)} under {CREDITS_HEAD!r}\n" + '\n'.join(new)
