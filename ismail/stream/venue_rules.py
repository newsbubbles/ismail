"""venue_rules and chat_commands: chat's !request and !vote handled by rule, the same way every time (ledger:M291).

Each venue has its rules (venue_rules.json in the stream home; '*' is the default for every venue). The MC hands
chat_watch's lines to chat_commands, which queues requests and counts votes in the request queue and returns the
reply for each line; the MC says them with chat_say. A chat line is acted on once, however often it is handed in."""
import difflib
import json
import os
import re
import time

from ..api import OpError, op
from . import config as C, request_queue as Q

DEFAULTS = {'request': True, 'vote': True, 'max_waiting': 1, 'cooldown_s': 600, 'duplicates': True, 'max_len': 120,
            'exempt_mods': True}
LIMITS = {'max_waiting': (1, 20), 'cooldown_s': (0, 86400), 'max_len': (10, 300)}
SAME = 0.85         # two requests this alike (after lower case and punctuation) are one


def rules_path():
    return C.HOME / 'venue_rules.json'


def _load():
    try:
        d = json.loads(rules_path().read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def rules_for(venue, saved=None):
    """The rules in effect on a venue: the defaults, then '*', then its own."""
    saved = _load() if saved is None else saved
    return {**DEFAULTS, **saved.get('*', {}), **saved.get(venue or '', {})}


@op(mutates=True)
def venue_rules(venue: str = '*', request: bool = None, vote: bool = None, max_waiting: int = None,
                cooldown_s: float = None, duplicates: bool = None, max_len: int = None, exempt_mods: bool = None,
                reset: bool = False) -> str:
    """Set and show a venue's chat command rules, which chat_commands applies to every `!request` and `!vote`. Reach
    for it when you limit requests per viewer, set a cooldown between them, or close requests or votes on a site.
    venue: a venue's name, or '*' (the default for every venue; a venue's own rules win). request / vote: the
    command is open. max_waiting: requests one viewer may have waiting (1). cooldown_s: seconds between one viewer's
    requests (600). duplicates: a request like one already waiting counts as a vote for it instead (true). max_len:
    the longest request text (120). exempt_mods: the channel owner and mods skip max_waiting and cooldown_s (true).
    reset: the venue back to the default. Call it with only venue= to see the rules."""
    venues = C.load_venues()
    if venue != '*' and venue not in venues:
        raise OpError(f"no venue {venue!r}: '*' (every venue) or one of {', '.join(sorted(venues)) or 'none yet'}")
    saved = _load()
    mine = {} if reset else dict(saved.get(venue, {}))
    for k, v in dict(request=request, vote=vote, max_waiting=max_waiting, cooldown_s=cooldown_s,
                     duplicates=duplicates, max_len=max_len, exempt_mods=exempt_mods).items():
        if v is None:
            continue
        if k in LIMITS:
            lo, hi = LIMITS[k]
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = None
            if v is None or not lo <= v <= hi:
                raise OpError(f"{k}: {lo} to {hi}" + (" (request=False closes requests)" if k == 'max_waiting' else ''))
            v = int(v) if k != 'cooldown_s' else float(v)
        mine[k] = bool(v) if k not in LIMITS else v
    changed = reset or mine != saved.get(venue, {})
    if changed:
        saved[venue] = mine
        if not mine:
            saved.pop(venue)
        C.HOME.mkdir(parents=True, exist_ok=True)
        tmp = rules_path().with_name(rules_path().name + f'.{os.getpid()}.tmp')
        tmp.write_text(json.dumps(saved, indent=1), encoding='utf8')
        os.replace(tmp, rules_path())
    out = []
    for name in ['*'] + sorted(venues):
        r = rules_for(None if name == '*' else name, saved)
        own = ' (its own)' if name in saved and name != '*' else ''
        out.append(f"{name}{own}: {_rules_line(r)}")
    return ('set. ' if changed else '') + 'rules:\n' + '\n'.join(out)


def _rules_line(r):
    return (f"!request {'open' if r['request'] else 'closed'}, !vote {'open' if r['vote'] else 'closed'}, "
            f"{r['max_waiting']} waiting per viewer, {int(r['cooldown_s'])} s between, max {r['max_len']} chars, "
            f"duplicates {'count as votes' if r['duplicates'] else 'allowed'}"
            + (', owner and mods exempt' if r['exempt_mods'] else ''))


# ------------------------------------------------------------------ the commands

def _norm(text):
    words = re.sub(r"[^\w\s]", ' ', text.lower().replace('&', ' and ')).split()
    return ' '.join('and' if w == 'n' else w for w in words)         # drum & bass, drum n bass


def _short(t, n=60):
    return repr(t if len(t) <= n else t[:n - 3].rstrip() + '...')


def _like(a, b):
    a, b = _norm(a), _norm(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= SAME


def _src(d):
    """A chat line's mark in the queue log: its n and its time, so a chat log started over is not taken as seen."""
    return f"{d['n']}@{d['at']}" if d.get('at') else d['n']


def _lines_of(lines):
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except ValueError:
            raise OpError("lines: chat_watch's JSON as it came, its 'lines' list, or a list of chat lines")
    if isinstance(lines, dict):
        lines = lines.get('lines', [])
    if not isinstance(lines, list) or not all(isinstance(d, dict) for d in lines):
        raise OpError("lines: chat_watch's JSON as it came, its 'lines' list, or a list of chat lines")
    return lines


def _request(d, who, text, rules, reqs, now):
    """(reply, result, id) for one !request; appends to the queue when it is taken (the caller holds the lock)."""
    venue, u = d.get('venue'), d.get('user') or {}
    if not rules['request']:
        return 'requests are closed right now', 'refused', None
    if not text:
        return 'say what you would like: !request <a style or a song>', 'refused', None
    if len(text) > rules['max_len']:
        return f"keep a request under {rules['max_len']} characters", 'refused', None
    key = Q.viewer_key(venue, who)
    same = next((r for r in Q.line_up(reqs) if rules['duplicates'] and _like(r['what'], text)), None)
    if same:
        if same['key'] == key:
            pos = Q.position(same['id'], reqs)
            return f"you asked for that already: #{same['id']}, #{pos} in line", 'refused', None
        if rules['vote'] and not Q.vote_refused(same, key):
            Q.vote(same['id'], key, src=_src(d))
            reqs = Q.state()
            return (f"{_short(same['what'])} is already in line as #{same['id']}: counted your vote, now "
                    f"#{Q.position(same['id'], reqs)} in line"), 'voted', same['id']
        return f"{_short(same['what'])} is already in line as #{same['id']}", 'refused', None
    mine = [r for r in reqs.values() if r['key'] == key]
    exempt = rules['exempt_mods'] and (u.get('mod') or u.get('owner'))
    waiting = [r for r in mine if r['status'] == 'waiting']
    if not exempt and len(waiting) >= rules['max_waiting']:
        w = waiting[0]
        why = (f"you have #{w['id']} waiting ({_short(w['what'])}, #{Q.position(w['id'], reqs)} in line)"
               if rules['max_waiting'] == 1 else f"you have {len(waiting)} waiting already, the most here")
        return why, 'refused', None
    last = max((r['added'] for r in mine), default=None)
    if not exempt and last is not None and rules['cooldown_s'] and now - last < rules['cooldown_s']:
        return f"your next request opens in {Q._ago(rules['cooldown_s'] - (now - last))}", 'refused', None
    rid = Q.add(who, text, venue, user_id=u.get('id') or None, src=_src(d))
    reqs = Q.state()
    pos = Q.position(rid, reqs)
    reply = f"queued {_short(text)}: #{pos} in line{Q._eta(pos, reqs, now)} (id {rid}, !vote {rid} to back it)"
    return reply, 'queued', rid


def _vote(d, who, arg, rules, reqs):
    """(reply, result, id) for one !vote; reply None when the argument is no request id (a vote of the MC's own)."""
    try:
        rid = int(arg.strip().lstrip('#'))
    except ValueError:
        return None, None, None
    if not rules['vote']:
        return 'votes are closed right now', 'refused', None
    if rid not in reqs:
        ids = ', '.join(f"#{r['id']}" for r in Q.line_up(reqs)[:5])
        return f"no request #{rid}" + (f"; waiting: {ids}" if ids else '; nothing is waiting'), 'refused', None
    key = Q.viewer_key(d.get('venue'), who)
    why = Q.vote_refused(reqs[rid], key)
    if why:
        return why.replace('they voted', 'you voted').replace('is their own', 'is your own'), 'refused', None
    Q.vote(rid, key, src=_src(d))
    reqs = Q.state()
    return (f"vote counted for #{rid} {_short(reqs[rid]['what'])} ({len(reqs[rid]['votes'])}), now "
            f"#{Q.position(rid, reqs)} in line"), 'voted', rid


@op(mutates=True)
def chat_commands(lines) -> str:
    """Handle chat's `!request <text>` and `!vote <id>` by the venue's rules (venue_rules), the same way every time:
    a request is queued for the DJ, refused (too many waiting, too soon, too long, closed) or, when one like it is
    waiting, counted as a vote for that one; a vote moves a request up. Reach for it when chat_watch returns lines
    with commands in them. lines: chat_watch's JSON as it came (or its lines). Returns JSON {replies: [{reply_to,
    text}], handled, left}: say each reply with chat_say(text, reply_to=...). A line is acted on once, however often
    it is handed in. left: commands it does not decide (`!vote a` in a vote of yours), and every other line is
    yours as before."""
    lines = _lines_of(lines)
    replies, handled, left = [], [], []
    saved = _load()
    with Q._lock():
        done = Q.handled()
        for d in lines:
            text = str(d.get('text') or '').strip()
            m = re.match(r'!(request|vote)\b\s*(.*)', text, re.I | re.S)
            if d.get('kind') != 'chat' or d.get('own') or not m or d.get('n') is None:
                continue
            if _src(d) in done:
                handled.append({'n': d['n'], 'command': m.group(1).lower(), 'result': 'handled before'})
                continue
            u = d.get('user') or {}
            who = u.get('display') or u.get('name') or 'viewer'
            rules, reqs, now = rules_for(d.get('venue'), saved), Q.state(), time.time()
            cmd, arg = m.group(1).lower(), ' '.join(m.group(2).split())
            if cmd == 'request':
                reply, result, rid = _request(d, who, arg, rules, reqs, now)
            else:
                reply, result, rid = _vote(d, who, arg, rules, reqs)
                if reply is None:
                    left.append({'n': d['n'], 'text': text, 'why': 'not a request id: a vote of yours?'})
                    continue
            if result == 'refused':
                Q._append({'ev': 'cmd', 'src': _src(d), 'command': cmd, 'refused': reply})
            done.add(_src(d))
            handled.append({'n': d['n'], 'venue': d.get('venue'), 'user': who, 'command': cmd, 'result': result,
                            'id': rid})
            replies.append({'reply_to': d['n'], 'text': reply})
    out = {'replies': replies, 'handled': handled, 'left': left}
    if replies:
        out['next'] = 'say each: chat_say(text, reply_to=<reply_to>); request_list shows the line'
    return json.dumps(out, ensure_ascii=False)
