"""feedback_*: a feedback page the person answers by typing or by voice, hosted on this computer, opened in their own
browser, and kept: everything they typed or said comes back when they reopen it (hq:D-166; ismail/feedback)."""
import os
from pathlib import Path

from .api import OpError, op
from . import feedback as FB

READ_TEXT_MAX = 2000        # characters of one answer shown by feedback_read; the rest is in answers.json
READ_MAX = 9000             # characters in one feedback_read reply


def _project(project):
    p = Path(str(project or ''))
    if not str(project or '').strip() or not p.is_dir():
        raise OpError(f"no folder {project!r}: pass the song's (or film's) folder, e.g. songs/<slug>")
    return p.resolve()


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
    items: [{label, path}] for a clip (audio or video) or a picture, [{label, text}] for words; a bare path or string
    works too. questions: ['What do you hear first?', ...]. open=False only returns the link. Calling it again for
    the same round rewrites the page and keeps every answer. Read the answers with feedback_read(project, round).
    Reach for it whenever the person should answer questions by voice or by typing at the desk: notes on a cut, a
    mix, a set of shots or sketches, a round of choices, anything you would otherwise build a page and a server for."""
    proj, rnd = _project(project), _round(round)
    try:
        d, man, kept = FB.build(proj, rnd, items, questions, voice=voice, title=title)
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
         f"folder: {d} (answers.json, voice/)",
         f"server: port {p}, {'started now' if started else 'already running'}, serving every round; "
         f"read answers with feedback_read(project, round='{rnd}')"]
    return '\n'.join(L)


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
    file, its length and its transcript), each with the time it was saved. since: an ISO time (as printed here, e.g.
    the last reply's 'latest' time) shows only what came in after it. Says plainly when nothing has come in yet and
    when clips are still being transcribed (and whether the page server is running to finish them)."""
    proj, rnd = _project(project), _round(round)
    d = FB.round_dir(proj, rnd)
    man = FB.read_json(d / 'manifest.json')
    if not man:
        have = sorted(x.name for x in (proj / 'work' / 'feedback').glob('*') if (x / 'manifest.json').is_file())
        raise OpError(f"no feedback round {rnd!r} in {proj / 'work' / 'feedback'}"
                      + (f"; rounds there: {', '.join(have)}" if have else "; feedback_page(project, round, items, "
                                                                           "questions) makes one"))
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
    head = f"feedback round {rnd} ({d}), {len(man.get('questions', []))} questions"
    if not shown:
        msg = (f"{head}: nothing new since {since}" if cut else f"{head}: nothing has come in yet")
        tail = _server_line(d, waiting)
        return msg + ('.' if not tail else f". {tail}")
    out = [head + (f"; what came in after {since}" if cut else '') + ':'] + L
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
        proj = _project(project)
        dirs = sorted(x for x in (proj / 'work' / 'feedback').glob('*') if (x / 'manifest.json').is_file())
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
        L.append(f"{man.get('round', d.name)}: {answered}/{nq} questions answered, {_n(len(said), 'voice clip')}"
                 + (f" ({wait} waiting for transcription)" if wait else '') + f"; {link}; {d}")
    srv = (f"page server: running on port {port} (feedback_stop() stops it)" if port
           else "page server: not running (feedback_page starts it)")
    if not L:
        where = f"in {_project(project) / 'work' / 'feedback'}" if project else 'known to the page server'
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
