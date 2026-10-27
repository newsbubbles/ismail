"""grant_*: the person's standing grants and who owns which questions, kept as a checked file and tested before any ask
goes out (ledger:M209-M210; qa intent_rules 10-16 clusters 1 and 2: "do not ask again for what I already granted" and
"I should not be the one routing").

The file is local and never tracked: ~/.ismail/standing_grants.json, or the setting guards.grants (guards.py reads
it on every phone and stage ask). These ops are the only writers: an entry needs the person's own words as its
source, a keyword that would match every ask is refused, and nothing on the still-ask list (public, money, accounts,
deleting) can be granted. grant_check is for the asks no op sees: a NEEDS NATE line, a message to the person.
"""
import datetime
import json
import os
import re

from .api import OpError, op
from . import guards

SOURCE = re.compile(r'^((hq:)?D-\d+|n\d+|vr:\S+|chat: .{3,}|memory: \S+)$')
# a yes or no word, or a word every ask has, would cover any question ('merge' and 'restart' name a step: kept)
# built on first use: guards imports api, which imports this module, so guards may be half-loaded at import time
_WIDE = {'it', 'this', 'that', 'thing', 'step', 'ask', 'do', 'make', 'run', 'now'}


def _too_wide(word):
    return word in (guards.YES_WORDS - {'merge', 'restart'}) | guards.NO_WORDS | _WIDE


def _read():
    p = guards.grants_path()
    try:
        raw = p.read_text(encoding='utf8')
    except OSError:
        return p, []
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise OpError(f"{p} does not read as JSON ({e}): fix it by hand first; it is not overwritten while broken")
    if not isinstance(data, list):
        raise OpError(f"{p} is not a JSON list of grants: fix it by hand first; it is not overwritten while broken")
    return p, data


def _write(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf8')
    os.replace(tmp, p)


def _line(g):
    kind = g.get('kind') or 'grant'
    who = f", {g['owner']}'s" if g.get('owner') else ''
    src = (f", from {g['source']}" if g.get('source') else
           ", no source: matched after the sourced ones; grant_drop it and grant_add it with its source")
    return (f"{g.get('id')} ({g.get('date')}, {kind}{who}{src}): {g.get('says')}; keywords: "
            f"{', '.join(map(str, g.get('keywords') or []))}")


@op()
def grant_check(ask: str, sender: str = None) -> str:
    """Before an ask reaches the person (a NEEDS NATE line, a phone or VR question, a message), test it against
    their standing grants and routes. Returns one of:
    'act: ...'   a grant covers it and it is yours: do it, then say what you did; leave it off NEEDS NATE;
    'send: ...'  it belongs to another agent (it acts on it, or answers that kind of question): send it there;
    'ask: ...'   nothing covers it, or it names something always asked (public, money, accounts, deleting, or
                 what only their senses answer: an exam, a listen, a headset test).
    sender: your name as the team knows you (an entry with an owner is yours or someone else's). Phone and stage
    asks run the same test themselves."""
    if not str(ask or '').strip():
        raise OpError("ask: the words you would put to the person")
    a = guards.assess(ask, True, sender)
    if a['problem']:
        return 'ask: ' + a['problem']
    g = a['entry']
    if a['verdict'] == 'act':
        return (f"act: a standing grant covers this ({g.get('id')}, {g.get('date')}: {g.get('says')}). Do it, then say "
                f"what you did; it does not go on NEEDS NATE")
    if a['verdict'] == 'send':
        what = 'answers this kind of question' if g.get('kind') == 'route' else 'acts on this by the grant'
        return (f"send: this is {g['owner']}'s ({g.get('id')}: {g.get('says')}); {g['owner']} {what}. Send it to "
                f"{g['owner']}, not to the person" + ('' if sender else f" (if you are {g['owner']}, pass sender)"))
    if a['verdict'] == 'still':
        return (f"ask: {g.get('id')} matched, but '{a['word']}' is always asked (public, money, accounts, deleting, "
                f"exams and listening): ask the person, in plain words, with your suggestion")
    return (f"ask: no standing grant or route covers it ({a['checked']} checked in {guards.grants_path()}): ask "
            f"the person, in plain words, with your suggestion")


@op()
def grant_list() -> str:
    """The person's standing grants and routes, one per line, with where the file is and what is always asked."""
    p, data = _read()
    rows = [g for g in data if isinstance(g, dict)]
    head = f"{len(rows)} in {p}" if rows else f"none yet ({p} is missing or empty): grant_add writes one"
    return '\n'.join([head] + ['  ' + _line(g) for g in rows] +
                     ["always asked, whatever a grant or route says: anything public, money, accounts and passwords, "
                      "deleting, and what only their senses answer (exams, listening, headset tests)"])


@op(mutates=True)
def grant_add(says: str, keywords: list, source: str, owner: str = None, kind: str = 'grant', topic: str = None,
              id: str = None, date: str = None) -> str:
    """Record a standing grant (the person said yes to a kind of step: kind='grant', its owner acts without asking)
    or a route (a kind of question belongs to an agent: kind='route', owner required, asks go to it, not to the
    person). says: the grant in a few plain words. keywords: whole words or phrases that mark an ask it covers
    (['merge', 'merging']); a word that would match any ask ('yes', 'go') is refused. source: where the person
    said it: a decision id ('D-150' or 'hq:D-150'), a phone note ('n5737'), a VR note ('vr:<scene>:<id>'), their
    words ('chat: <exact words>') or a memory file ('memory: <name>'). Public, money, accounts and deleting cannot
    be granted. id: default the next G<n>."""
    says = str(says or '').strip()
    if not says:
        raise OpError("says: the grant in a few plain words, e.g. 'merge reviewed pull requests on green'")
    if not isinstance(keywords, (list, tuple)) or not keywords:
        raise OpError("keywords: a list of whole words or phrases, e.g. ['merge', 'merging']")
    kws = [str(k).strip() for k in keywords if str(k).strip()]
    wide = [k for k in kws if _too_wide(k.lower()) or len(k) < 3]
    if wide:
        raise OpError(f"keywords {wide} would match almost any ask: use the words that name the step "
                      f"('merge', 'stage restart')")
    # a grant approves a step, so it can name nothing always asked; a route only says who answers a question, so its
    # words may mention posts or accounts, but its keywords may not (they would carry a "should I post?" away)
    still = [k for k in kws + ([says] if kind == 'grant' else []) if guards.STILL_ASK.search(k)]
    if still:
        raise OpError(f"{still} names something always asked (public, money, accounts, deleting): no grant covers it, "
                      f"so the person is asked each time")
    source = str(source or '').strip()
    if not SOURCE.match(source):
        raise OpError("source: where the person said it, as 'D-150' (or 'hq:D-150'), 'n5737' (a phone note), "
                      "'vr:<scene>:<id>', 'chat: <their exact words>' or 'memory: <file name>'. A grant without "
                      "the person's own words behind it is not recorded")
    if kind not in guards.GRANT_KINDS:
        raise OpError(f"kind is 'grant' (the owner acts without asking) or 'route' (the owner answers instead of the "
                      f"person), got {kind!r}")
    if kind == 'route' and not owner:
        raise OpError("a route needs owner=: the agent that answers this kind of question")
    p, data = _read()
    ids = {str(g.get('id')) for g in data if isinstance(g, dict)}
    if id is None:
        n = max([int(m.group(1)) for i in ids for m in [re.match(r'^G(\d+)$', i)] if m] or [0]) + 1
        id = f'G{n}'
    elif id in ids:
        raise OpError(f"{id} is already in {p}: grant_list shows it; grant_drop it first to replace it")
    g = {'id': id, 'date': date or datetime.date.today().isoformat(), 'topic': topic or says[:40], 'keywords': kws,
         'says': says, 'kind': kind, 'source': source}
    if owner:
        g['owner'] = owner
    data.append(g)
    _write(p, data)
    return f"added {_line(g)}\nphone and stage asks it covers are refused from now on; grant_check tests any other ask"


@op(mutates=True)
def grant_drop(id: str, why: str) -> str:
    """Take a grant or route out (the person withdrew it, or it was wrong). why is required; the entry and the
    reason are kept in grants_dropped.jsonl beside the file."""
    if not str(why or '').strip():
        raise OpError("why: the reason it goes (the person's words, or what was wrong); it is logged with the entry")
    p, data = _read()
    keep = [g for g in data if not (isinstance(g, dict) and str(g.get('id')) == str(id))]
    if len(keep) == len(data):
        raise OpError(f"no {id!r} in {p}: grant_list shows the ids")
    gone = [g for g in data if g not in keep]
    _write(p, keep)
    with open(p.parent / 'grants_dropped.jsonl', 'a', encoding='utf8') as f:
        f.write(json.dumps({'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'why': why,
                            'entry': gone[0]}, ensure_ascii=False) + '\n')
    return f"dropped {_line(gone[0])} (kept in {p.parent / 'grants_dropped.jsonl'})"
