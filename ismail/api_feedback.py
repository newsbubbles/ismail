"""feedback_*: a feedback page the person answers by typing or by voice, hosted on this computer, opened in their own
browser, and kept: everything they typed or said comes back when they reopen it (hq:D-166; ismail/feedback)."""
import os
import time
from pathlib import Path

from .api import OpError, op
from . import feedback as FB

READ_TEXT_MAX = 2000        # characters of one answer shown by feedback_read; the rest is in answers.json
READ_MAX = 9000             # characters in one feedback_read reply
WAIT_S = 180                # feedback_wait's default: long enough for a few answers, short for one tool call
WAIT_MAX_S = 3600
HEARD_WAIT_S = 60           # after Send, how long feedback_wait waits for clips said before it to be written down


def _project(project):
    p = Path(str(project or ''))
    if not str(project or '').strip() or not p.is_dir():
        raise OpError(f"no folder {project!r}: pass the song's (or film's) folder, e.g. songs/<slug>")
    return p.resolve()


def _base(project):
    """-> (the folder given, the folder its rounds go in). A song's proj/ (a project.json whose song folder is its
    parent) is rebuilt by the song's build, which would take the answers with it (a first-session test lost its answers that way), so its
    rounds go in the song folder: songs/<slug>/work/feedback/<round>/."""
    p = _project(project)
    if (p / 'project.json').is_file():
        from .sources import song_root
        sr = Path(song_root(str(p))).resolve()
        if sr != p:
            return p, sr
    return p, p


def _round_dir(project, rnd):
    """-> (the round's folder, the folder rounds go in). A round made in a song's proj/ before rounds moved to the
    song folder stays where it is, so its link and answers keep working."""
    p, base = _base(project)
    d, old = FB.round_dir(base, rnd), FB.round_dir(p, rnd)
    if base != p and not (d / 'manifest.json').is_file() and (old / 'manifest.json').is_file():
        return old, base
    return d, base


def _round_dirs(project):
    p, base = _base(project)
    seen, out = set(), []
    for top in dict.fromkeys([base, p]):
        for x in sorted((top / 'work' / 'feedback').glob('*')):
            key = os.path.normcase(str(x.resolve()))
            if (x / 'manifest.json').is_file() and key not in seen:
                seen.add(key)
                out.append(x)
    return base, out


def _round(rnd):
    r = str(rnd if rnd is not None else '').strip()
    if not FB.SAFE.match(r):
        raise OpError(f"round {rnd!r}: use letters, digits, '_', '-' or '.', e.g. round='r1' or round='mix2'")
    return r


def _url(port, rid):
    return f"http://127.0.0.1:{port}/f/{rid}/"


def _open(url):
    import webbrowser
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception:                   # noqa: BLE001  no browser to hand it to: the reply gives the link
        return False


@op()
def feedback_page(project: str, round: str, items: list = None, questions: list = None, voice: bool = True,
                  title: str = None, open: bool = True, port: int = None) -> str:
    """A feedback page for the person, on this computer: the items to look at or listen to, then numbered questions,
    each with a text box and (voice=True) a Record button. Everything they type is saved as it changes and every
    recording is saved as a file and transcribed in the background; when they reopen the page, their text, their
    recordings with players and the transcripts all come back. Written to <project>/work/feedback/<round>/
    (manifest.json, answers.json, voice/), served by one shared local server on 127.0.0.1 (found or started here; it
    serves every round), and opened in their default browser, never an app's browser pane, which has no microphone.
    A song's proj/ (rebuilt by the song's build) writes its rounds in the song folder instead,
    songs/<slug>/work/feedback/<round>/, so a rebuild never takes the answers.
    items: [{label, path}] for a clip (audio or video) or a picture, [{label, text}] for words; a bare path or string
    works too. questions: ['What do you hear first?', ...]. open=False only returns the link. Calling it again for
    the same round rewrites the page and keeps every answer. The page ends in a Send button: the person presses it
    when they have said everything (done.json in the round), and pressing it again after a late addition counts as a
    new send. feedback_wait(project, round) waits for that Send and returns the answers; feedback_read reads them
    at any time and says SENT or STILL WRITING first. Never ask the person to type "done".
    Reach for it whenever the person should answer questions by voice or by typing at the desk: notes on a cut, a
    mix, a set of shots or sketches, a round of choices, anything you would otherwise build a page and a server for."""
    proj, rnd = _project(project), _round(round)
    folder, base = _round_dir(project, rnd)
    try:
        d, man, kept = FB.build(proj, rnd, items, questions, voice=voice, title=title,
                                folder=folder if folder != FB.round_dir(proj, rnd) else None)
    except (OSError, ValueError) as e:
        raise OpError(f"feedback_page: {e}")
    rid = FB.register(d)
    try:
        p, started = FB.ensure(port)
    except RuntimeError as e:
        raise OpError(f"feedback_page: the page is written in {d}, but {e}")
    url = _url(p, rid)
    opened = _open(url) if open else False
    kinds = {}
    for it in man['items']:
        kinds[it['kind']] = kinds.get(it['kind'], 0) + 1
    L = [f"feedback round {rnd}: {len(man['questions'])} questions, "
         + (', '.join(f"{n} {k}" for k, n in kinds.items()) or 'no items') + (', voice on' if voice else ', typing only')
         + (f"; kept the {_n(kept, 'answer')} already given" if kept else ''),
         f"link: {url}",
         (f"opened in the default browser (an app's browser pane has no microphone: do not open it there)" if opened
          else f"not opened: ask the person to open the link in their own browser (Chrome, Edge, Firefox), not an app "
               f"pane, which has no microphone" if open else
          "not opened (open=False): give the person the link to open in their own browser, not an app pane"),
         f"folder: {d} (answers.json, voice/, done.json when they press Send)"
         + (" in the song folder, which a rebuild of proj/ leaves alone" if base != proj and FB._same(d.parents[2], base)
            else " in proj/, which a rebuild replaces: a new round goes in the song folder" if base != proj else ''),
         f"server: port {p}, {'started now' if started else 'already running'}, serving every round",
         f"the person presses Send at the bottom when they are finished: feedback_wait(project, round='{rnd}') waits "
         f"for it and returns the answers (feedback_read shows them any time, headed SENT or STILL WRITING); never "
         f"ask them to type 'done'"]
    old = _old_server(p)
    if old:
        L.append(old)
    return '\n'.join(L)


def _old_server(port):
    """A page server started before the Send button has none on its page: say how to get it."""
    h = FB.health(port, timeout=1.0) if port else None
    if h and not h.get('send'):
        return (f"the page server on port {port} is an older one: its pages have no Send button, so feedback_wait can "
                f"only time out. feedback_stop() then feedback_page(...) again starts the new one (answers are kept; "
                f"other open rounds' pages reconnect)")
    return ''


def _clip_line(c, d):
    path = d / 'voice' / c['file']
    words = ((c.get('text') or '(no words heard)') if c.get('state') == 'done' else
             f"(not transcribed yet: {c.get('state') or 'waiting'})")
    return f"   said at {c['at']}, {c.get('secs') or '?'} s, {path}:\n      {_cap(words)}"


def _n(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _cap(text, n=READ_TEXT_MAX):
    text = str(text)
    t = text if len(text) <= n else text[:n] + f" ... ({len(text) - n} more characters in answers.json)"
    return t.replace('\n', '\n      ')


def _after(t, since):
    return since is None or (t and FB.parse_time(t) > since)


@op()
def feedback_read(project: str, round: str, since: str = None) -> str:
    """The answers given on a feedback_page round, verbatim, per question: the typed text and each voice clip (its
    file, its length and its transcript), each with the time it was saved. The first line says SENT (the time the
    person pressed Send) or STILL WRITING (the last save): answers not sent yet may be half written, so do not act on
    them; feedback_wait(project, round) waits for the Send. since: an ISO time (as printed here, e.g. the last reply's
    'latest' time) shows only what came in after it. Says plainly when nothing has come in yet and when clips are
    still being transcribed (and whether the page server is running to finish them)."""
    rnd = _round(round)
    d, _ = _round_dir(project, rnd)
    man = FB.read_json(d / 'manifest.json')
    if not man:
        raise _no_round(project, rnd)
    try:
        cut = FB.parse_time(since) if since else None
    except ValueError:
        raise OpError(f"since={since!r} is not a time: pass an ISO time such as 2026-10-15T14:02:11+01:00 (each "
                      f"reply prints its latest)")
    ans = FB.read_json(d / 'answers.json', {}) or {}
    L, latest, waiting, quiet, shown = [], [], [], 0, 0
    for q in man.get('questions', []):
        a = ans.get(str(q['n'])) or {}
        typed = (a.get('text') or '').strip()
        said = a.get('said') or []
        latest += [a.get('typed_at')] if typed else []
        latest += [c.get('heard_at') or c['at'] for c in said]
        waiting += [c for c in said if c.get('state') != 'done']
        new_typed = typed and _after(a.get('typed_at'), cut)
        new_said = [c for c in said if _after(c['at'], cut) or _after(c.get('heard_at'), cut)]
        if not new_typed and not new_said:
            quiet += 1
            continue
        L.append(f"{q['n']}. {q['text']}")
        if new_typed:
            L.append(f"   typed (last change {a.get('typed_at')}):\n      {_cap(typed)}")
            shown += 1
        for c in new_said:
            L.append(_clip_line(c, d))
            shown += 1
    state = _state_lines(d, rnd)
    head = f"feedback round {rnd} ({d}), {len(man.get('questions', []))} questions"
    if not shown:
        msg = (f"{head}: nothing new since {since}" if cut else f"{head}: nothing has come in yet")
        tail = _server_line(d, waiting)
        return '\n'.join(state + [msg + ('.' if not tail else f". {tail}")])
    out = state + [head + (f"; what came in after {since}" if cut else '') + ':'] + L
    if quiet:
        out.append(f"({_n(quiet, 'question')} with no answer" + (' since then' if cut else '') + ')')
    tail = _server_line(d, waiting)
    if tail:
        out.append(tail)
    stamps = sorted(FB.parse_time(t) for t in latest if t)
    if stamps:
        out.append(f"latest: {stamps[-1].isoformat(timespec='seconds')} (pass it as since= to see only what is new)")
    text = '\n'.join(out)
    if len(text) > READ_MAX:
        text = text[:READ_MAX] + f"\n... (cut at {READ_MAX} characters: everything is in {d / 'answers.json'}; " \
                                 f"since= shows only what is new)"
    return text


def _no_round(project, rnd):
    base, dirs = _round_dirs(project)
    have = [x.name for x in dirs]
    return OpError(f"no feedback round {rnd!r} in {base / 'work' / 'feedback'}"
                   + (f"; rounds there: {', '.join(have)}" if have else "; feedback_page(project, round, items, "
                                                                        "questions) makes one"))


def _state_lines(d, rnd):
    """The first line of a read: SENT or STILL WRITING, and, when not sent, the line that says not to act yet."""
    st = FB.sent_state(d)
    wait = f"feedback_wait(project, round='{rnd}') waits for their Send"
    if st['state'] == 'sent':
        return [f"SENT {st['at']}" + (f" (sent {len(st['sent'])} times; this is the last)" if len(st['sent']) > 1
                                     else '')]
    if st['state'] == 'changed':
        return [f"SENT {st['at']}, then CHANGED {st['changed_at']}: STILL WRITING a late addition (last save "
                f"{st['last_save']})",
                f"not sent again yet: act on what was sent, and treat what came after {st['at']} as unfinished; {wait}"]
    return [f"STILL WRITING (last save {st['last_save'] or 'none yet'})",
            f"not sent yet: don't act on these answers; {wait}"]


def _server_line(d, waiting):
    if not waiting:
        return ''
    rid = next((k for k, v in FB.rounds().items() if FB._same(v, d)), None)
    port = FB.running()
    n = len(waiting)
    clips = _n(n, 'clip')
    if port and rid:
        states = sorted({c.get('state') or 'waiting' for c in waiting})
        return (f"Transcription is still running for {clips} ({'; '.join(states)}): the page server finishes them on "
                f"its own; read again in a minute.")
    return (f"{clips} still wait{'s' if n == 1 else ''} for transcription, but the page server is not running: "
            f"feedback_page(project, round, ...) again (open=False if the person is not at the desk) starts it, and "
            f"it picks them up.")


@op()
def feedback_list(project: str = None) -> str:
    """The feedback rounds of a project (or, with no project, every round the page server knows): each one's link,
    how many questions have an answer, its voice clips and how many still wait for transcription; then whether the
    page server runs and on which port."""
    port = FB.running()
    if project:
        base, dirs = _round_dirs(project)
    else:
        dirs = [Path(v) for v in FB.rounds().values() if (Path(v) / 'manifest.json').is_file()]
    reg = {os.path.normcase(str(Path(v).resolve())): k for k, v in FB.rounds().items()}
    L = []
    for d in dirs:
        man = FB.read_json(d / 'manifest.json', {}) or {}
        ans = FB.read_json(d / 'answers.json', {}) or {}
        nq = len(man.get('questions', []))
        answered = sum(1 for a in ans.values() if (a.get('text') or '').strip() or a.get('said'))
        said = [c for a in ans.values() for c in a.get('said') or []]
        wait = sum(1 for c in said if c.get('state') != 'done')
        rid = reg.get(os.path.normcase(str(d.resolve())))
        link = _url(port, rid) if port and rid else '(not served now: feedback_page again serves it)'
        st = FB.sent_state(d)
        sent = {'sent': f"SENT {st['at']}", 'changed': f"changed after sending {st['at']}"}.get(st['state'],
                                                                                              'not sent yet')
        L.append(f"{man.get('round', d.name)}: {answered}/{nq} questions answered, {_n(len(said), 'voice clip')}"
                 + (f" ({wait} waiting for transcription)" if wait else '') + f", {sent}; {link}; {d}")
    srv = (f"page server: running on port {port} (feedback_stop() stops it)" if port
           else "page server: not running (feedback_page starts it)")
    if not L:
        where = f"in {base / 'work' / 'feedback'}" if project else 'known to the page server'
        return f"no feedback rounds {where}; feedback_page(project, round, items, questions) makes one.\n{srv}"
    return '\n'.join(L + [srv])


@op()
def feedback_stop() -> str:
    """Stop the shared feedback page server (every round's page stops answering; nothing saved is lost, and clips
    still waiting for transcription are picked up when feedback_page starts it again)."""
    waiting = 0
    for v in FB.rounds().values():
        ans = FB.read_json(Path(v) / 'answers.json', {}) or {}
        waiting += sum(1 for a in ans.values() for c in a.get('said') or [] if c.get('state') != 'done')
    port = FB.stop()
    if not port:
        return "no feedback page server is running"
    return (f"stopped the feedback page server on port {port}"
            + (f"; {_n(waiting, 'clip')} still waiting for transcription, picked up when feedback_page starts it again"
               if waiting else ''))


@op()
def feedback_wait(project: str, round: str, timeout_s: float = WAIT_S, after: str = None) -> str:
    """Wait for the person to press Send on a feedback_page round, then return their answers (feedback_read's reply,
    headed SENT), so you never write a polling loop or answer a note they are still typing. Returns at once when the
    round is already sent and not changed since. At timeout_s (default 180, at most 3600) it returns what is typed so
    far, headed STILL WRITING, and how to call it again. after: an ISO time (a SENT time it printed): wait for a Send
    later than that one, e.g. for a late addition after you read the first. Clips said before Send get up to a minute
    more to be written down. Reach for it when a feedback page is out and you are waiting for the person's answers."""
    rnd = _round(round)
    d, _ = _round_dir(project, rnd)
    if not (d / 'manifest.json').is_file():
        raise _no_round(project, rnd)
    try:
        cut = FB.parse_time(after) if after else None
    except ValueError:
        raise OpError(f"after={after!r} is not a time: pass the SENT time a reply printed, e.g. "
                      f"2026-10-15T14:02:11+01:00")
    try:
        limit = min(max(float(timeout_s), 0.0), WAIT_MAX_S)
    except (TypeError, ValueError):
        raise OpError(f"timeout_s={timeout_s!r}: seconds to wait, e.g. 180 (at most {WAIT_MAX_S})")
    t0 = time.time()

    def ready():
        st = FB.sent_state(d)
        return st if st['state'] == 'sent' and (cut is None or FB.parse_time(st['at']) > cut) else None

    st = ready()
    while st is None and time.time() - t0 < limit:
        time.sleep(0.5)
        st = ready()
    if st is None:
        out = [feedback_read(project, rnd),
               f"waited {time.time() - t0:.0f} s and they have not pressed Send" + (f" again after {after}" if cut else '')
               + f": feedback_wait(project, round='{rnd}'" + (f", after='{after}'" if cut else '')
               + ") again keeps waiting (what they type is saved as they go)"]
        old = _old_server(FB.running())
        if old:
            out.append(old)
        return '\n'.join(out)

    def unheard():
        ans = FB.read_json(d / 'answers.json', {}) or {}
        return any(c.get('state') != 'done' for a in ans.values() for c in a.get('said') or [])

    end = min(t0 + limit, time.time() + HEARD_WAIT_S)
    while time.time() < end and unheard():
        time.sleep(1.0)
    return (feedback_read(project, rnd) + f"\nto wait for a later Send (a late addition), feedback_wait(project, "
                                          f"round='{rnd}', after='{st['at']}')")
