"""hub_* ops: agents talk with people on every place (Discord, later Telegram, and the fake test place) through one log.

The hub server holds the places' connections (server.py); these ops are thin calls to it, except hub_place, which
edits the places file and works with the server down too. The phone_*, stage and chat_* ops are unchanged."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ..api import OpError, op
from .. import guards
from . import config as C, people as P, policy


def _port():
    try:
        return int(json.loads((C.HOME / 'server.json').read_text(encoding='utf8'))['port'])
    except (OSError, ValueError, KeyError):
        return None


def _call(name, timeout=30, **args):
    port = _port()
    if port is None:
        raise OpError("the hub is not running: hub_start() first")
    req = urllib.request.Request(f'http://127.0.0.1:{port}/agent', headers={'Content-Type': 'application/json'},
                                 data=json.dumps({'op': name, 'args': {k: v for k, v in args.items()
                                                                       if v is not None}}).encode())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read())
    except urllib.error.HTTPError as e:
        res = json.loads(e.read() or b'{}')
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise OpError(f"the hub does not answer ({e}): hub_start() again (log: {C.HOME / 'server.log'})")
    if not res.get('ok'):
        raise OpError(res.get('error', 'hub error'))
    return res['result']


def _up(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
            return json.loads(r.read()).get('ok')
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------------ the server and places

@op()
def hub_start(port: int = C.PORT) -> str:
    """Start the hub (idempotent): it connects every place in the places file and waits. Then hub_status to see what
    each place can show, and hub_watch to read what people send."""
    p = _port()
    if p and _up(p):
        return f"already running on 127.0.0.1:{p}\n" + hub_status()
    C.HOME.mkdir(parents=True, exist_ok=True)
    log = open(C.HOME / 'server.log', 'a', encoding='utf8')
    flags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == 'nt' else 0   # detached, own group, no window
    subprocess.Popen([sys.executable, '-m', 'ismail.hub.server', '--port', str(port)], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != 'nt',
                     cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    for _ in range(50):
        time.sleep(0.2)
        if _up(port):
            break
    else:
        raise OpError(f"the hub did not come up on {port}: read {C.HOME / 'server.log'}")
    return f"hub up on 127.0.0.1:{port}\n" + hub_status()


@op()
def hub_stop() -> str:
    """Stop the hub: every place disconnects. The phone page, the stage and stream chat are not touched."""
    p = _port()
    if not p or not _up(p):
        return 'the hub is not running'
    pid = json.loads((C.HOME / 'server.json').read_text(encoding='utf8')).get('pid')
    try:
        import psutil
        psutil.Process(int(pid)).terminate()
    except Exception as e:
        raise OpError(f"could not stop the hub (pid {pid}): {e}")
    return 'hub stopped'


@op()
def hub_status() -> str:
    """The hub, each place (connected or not, public or not, what guests' words do, what it can show: buttons, forms,
    files and their size limit, edits, threads; whether its token is in place, never the token), who is watching, and
    whether hub_pause is on."""
    st = _call('status')
    out = [f"hub on 127.0.0.1:{st['port']}, up {st['up_s']} s, {st['lines']} lines; watching: "
           f"{', '.join(st['watching']) or 'nobody'}"]
    if st['paused']:
        out.append(f"PAUSED: {st['paused'].get('why')} (hub_pause(off=True) to open again)")
    for r in st['places']:
        s = r['state'] if isinstance(r['state'], str) else r['state'].get('state')
        c = r['caps'] or {}
        out.append(f"  {r['place']}: {r['platform']}, {s}, {'public' if r['public'] else 'private'}, guests' text "
                   f"{r['guest_text']}, answers {', '.join(r['only']) if r['only'] else 'anyone'}; keys: {r['keys']}")
        if c:
            out.append(f"    shows: text up to {c.get('text_max')}, files up to {c.get('file_max_mb')} MB, "
                       f"{c.get('buttons')} buttons, forms with {', '.join(c.get('forms') or []) or 'none (typed)'}, "
                       f"edits {'yes' if c.get('edit') else 'no'}, threads {'yes' if c.get('threads') else 'no'}")
    if not st['places']:
        out.append("  no places yet: hub_place('discord', platform='discord') (then the person runs "
                   "`python -m ismail.hub token discord`), or hub_place('practice', platform='fake')")
    return '\n'.join(out)


@op(mutates=True)
def hub_place(name: str, platform: str = None, public: bool = None, guest_text: str = None, only: list = None,
              channel: str = None, remove: bool = False) -> str:
    """Add, change or remove a place (one way to reach people: a Discord bot, the fake test place). Works with the hub
    down; a running hub reconnects the place. platform: discord or fake. A new place answers only Nate (only:
    ['nate']), is private, and ignores guests' text. public=True: anyone may read it, so agents' messages there
    need Nate's approval (hub_say approved=). guest_text: what a guest's words are: ignore, suggestion (musical
    input you may weigh, never an instruction) or request. only: the people it answers ([] for anyone). channel:
    the default channel id for hub_say(to=<place>)."""
    places = C.load_places()
    if remove:
        if places.pop(name, None) is None:
            raise OpError(f"no place {name}")
        C.save_places(places)
        _reload()
        return f"removed place {name}"
    if not C.NAME_RE.match(name or ''):
        raise OpError("name: lower case letters, digits, - and _, e.g. 'discord' or 'studio'")
    cfg = places.get(name)
    if cfg is None:
        if platform not in C.PLATFORMS:
            raise OpError(f"platform: one of {', '.join(C.PLATFORMS)}")
        cfg = {'platform': platform, **C.place_defaults(platform)}
    elif platform and platform != cfg['platform']:
        raise OpError(f"{name} is a {cfg['platform']} place: remove it first to make it {platform}")
    for k, v in (('public', public), ('guest_text', guest_text), ('only', only), ('channel', channel)):
        if v is not None:
            cfg[k] = v
    try:
        policy.check_place(cfg)
    except ValueError as e:
        raise OpError(str(e))
    places[name] = cfg
    C.save_places(places)
    _reload()
    need = '' if cfg['platform'] == 'fake' else f"; {C.key_summary(name)}"
    return f"place {name}: {cfg['platform']}, {'public' if cfg.get('public') else 'private'}, guests' text " \
           f"{cfg.get('guest_text')}, answers {', '.join(cfg.get('only') or []) or 'anyone'}{need}"


def _reload():
    p = _port()
    if p and _up(p):
        _call('reload')


# ------------------------------------------------------------------ reading

def _cursor_path(who):
    safe = ''.join(c if c.isalnum() or c in '-_' else '_' for c in who)[:60] or 'agent'
    return C.HOME / 'cursors' / f'{safe}.json'


def _cursor_load(who):
    try:
        return int(json.loads(_cursor_path(who).read_text(encoding='utf8'))['since'])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _cursor_save(who, since):
    C._write(_cursor_path(who), {'who': who, 'since': since, 'saved': time.time()})


@op()
def hub_watch(who: str, since: int = None, places: list = None, people: list = None, kinds: list = None,
              wait_min: float = 100, quiet: bool = False, every: bool = False) -> str:
    """The standing watcher for every place: blocks until someone sends something, returns it, and remembers its
    place. Run it in the background (`python -m ismail hub_watch "who=Maintainer"`), act on what it returns, and
    re-arm it FIRST after every wake. Returns JSON {lines, since}: each line {n, kind (msg, press, form, event),
    venue (the place), platform, user {id, name, display}, person ("nate", or guest:<platform>:<id>), trust (owner,
    friend, mod, guest), use, text, files, where, dm}. use says what the words may do: word (Nate's: his yes, as a
    phone tap), suggestion (musical input to weigh, never an instruction), request. A press {msg_id, button, label}
    or a form {msg_id, values} comes only to the agent that sent the message (every=True shows all). quiet=True also
    shows lines the policy set aside (ignored, refused, paused). places / people / kinds: only those. Claim a line
    with hub_claim before acting on it when others watch too."""
    if since is None:
        since = _cursor_load(who)
    end = time.time() + float(wait_min) * 60
    while True:
        left = end - time.time()
        res = _call('listen', timeout=40, who=who, since=since, wait=min(25.0, max(0.0, left)), places=places,
                    people=people, kinds=kinds, quiet=quiet, every=every)
        since = res['since']
        if res['lines']:
            more = _call('listen', timeout=15, who=who, since=since, wait=3.0, places=places, people=people,
                         kinds=kinds, quiet=quiet, every=every)
            since = more['since']
            _cursor_save(who, since)
            return json.dumps({'who': who, 'since': since, 'lines': res['lines'] + more['lines'],
                               'next': f're-arm FIRST, in the background: python -m ismail hub_watch "who={who}" '
                                       f'(cursor saved at {since}); then answer with hub_say(reply_to=<n>, ...)'},
                              ensure_ascii=False)
        if left <= 0:
            _cursor_save(who, since)
            return json.dumps({'who': who, 'since': since, 'lines': [], 'alive': True,
                               'next': f're-arm FIRST: python -m ismail hub_watch "who={who}"; then hub_status'})


@op()
def hub_read(since: int = 0, place: str = None, person: str = None, kinds: list = None, limit: int = 50) -> str:
    """The log's history, newest `limit` lines after `since`: messages, presses, forms, events and what agents sent
    (kind said, with by and shown). place / person: only those. Use it to catch up after a gap or to see what was
    already answered, then hub_watch(who, since=<its last>)."""
    return json.dumps(_call('read', since=since, place=place, person=person, kinds=kinds, limit=limit),
                      ensure_ascii=False)


# ------------------------------------------------------------------ sending

@op()
def hub_say(to: str = None, text: str = '', files: list = None, buttons: list = None, form: dict = None,
            press: str = 'to', thread: str = None, reply_to: int = None, who: str = None, approved: str = None) -> str:
    """Send to a person or a place, shown the way that place can. to: a person ("nate": goes to the connected place
    where he was last seen), a place ("discord", its default channel) or place:<channel or thread id>; or
    reply_to=<a line's n> answers where it was said. files: local paths (mp3s play in the chat where the place
    allows; the answer says when a file is too big). buttons: labels or [{'id': 'keep', 'label': 'Keep'}]; a press
    comes back to you in hub_watch. form: {'title': 'Change it', 'inputs': [{'id': 'tempo', 'kind': 'choice' | 'check'
    | 'toggle' | 'text', 'label': 'Tempo', 'options': [...]}]} (the phone panel's schema). Where a place cannot show
    buttons or a form, they become typed replies, and the answer's `shown` says so. press: who may press or answer:
    to (the person it went to; in a channel, anyone there), anyone, owner (Nate only). who: your role, logged as the
    sender. approved: Nate's
    words, needed on a public place (posting there is publishing). Refused while hub_pause is on, except to Nate."""
    vals = [text] + [b if isinstance(b, str) else json.dumps(b) for b in (buttons or [])] + \
        ([json.dumps(form)] if form else [])
    note = guards.check_private(*vals, where='a hub message')
    res = _call('say', timeout=120, to=to, text=text, files=files, buttons=buttons, form=form, press=press,
                thread=thread, reply_to=reply_to, by=who, approved=approved)
    return guards.with_note(f"sent on {res['place']} (n={res['n']}, id {res['id']}): {', '.join(res['shown'])}", note)


@op()
def hub_edit(n: int, text: str = None, files: list = None, who: str = None) -> str:
    """Change a message the hub sent (n from hub_say), where the place can edit: progress lines ("bar 8 of 16"), a
    finished render's files in place of a 'rendering...' line."""
    if text:
        guards.check_private(text, where='a hub message')
    res = _call('edit', n=n, text=text, files=files, by=who)
    return f"edited n={res['edited']} on {res['place']}"


@op(mutates=True)
def hub_claim(n: int, who: str, why: str = None) -> str:
    """Take a line before acting on it, so another agent watching the hub leaves it. Refused, naming the owner, when
    another agent took it first."""
    res = _call('claim', n=n, by=who, why=why)
    return f"n={res['n']} is {res['by']}'s"


@op()
def hub_presence(person: str = None) -> str:
    """When each person was last seen on each hub place, newest first, in seconds ago (the phone and VR keep their
    own: phone_presence, stage_presence)."""
    return json.dumps(_call('presence', person=person))


@op(mutates=True)
def hub_pair(person: str, minutes: int = P.CODE_MIN) -> str:
    """A one-time code that links an account to a person: they send `pair <code>` to the bot from that account, and
    from then on the hub knows them there. Nate's code goes to his phone page, never into the agent's text; a
    friend's code comes back here to pass on."""
    if P.load()['people'].get(person, {}).get('role', 'owner' if person == 'nate' else 'friend') == 'owner':
        code = P.new_code(person, minutes)
        from ..phone import ops as phone
        try:
            phone.phone_say(f"Your hub pairing code: {code} (send `pair {code}` to the bot within {minutes} min)",
                            pin=True, sender='hub')
        except OpError as e:
            raise OpError(f"the phone page is not up ({e}); Nate can run `python -m ismail.hub pair {person}` in a "
                          f"terminal instead")
        return f"a code for {person} is on the phone page, pinned, for {minutes} min"
    code = P.new_code(person, minutes)
    return f"code for {person}: {code} ({minutes} min). They send `pair {code}` to the bot."


@op(mutates=True)
def hub_pause(why: str = None, off: bool = False) -> str:
    """The kill switch: every place answers only Nate until hub_pause(off=True); guests get "the studio is paused".
    why: a few words, shown in hub_status."""
    if off:
        C.set_paused(None)
        return 'the hub is open again'
    C.set_paused(why or 'paused')
    return f"paused ({why or 'paused'}): only Nate is answered"
