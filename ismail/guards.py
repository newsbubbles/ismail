"""Checks the ops run before they show text to the person, ask them something, or open a mic.

Three refusals, each saying why and what the next call is:

1. Nothing personal on a page (phone captions and panels, stage captions, panels and asks). Terms come from a local
   file that never goes in the repository: $ISMAIL_PRIVATE_TERMS, else ~/.ismail/private_terms.txt, one place or name
   per line, '#' starts a comment line. The git user.email of this checkout is always added. Words match whole and
   case-insensitive; an email matches anywhere in the text. With no file, only the email is checked and the reply
   says how to turn the rest on.
2. Do not ask for what is already granted. A yes/no question whose words a standing grant covers is refused. Grants
   live in a local file: $ISMAIL_STANDING_GRANTS, else ~/.ismail/standing_grants.json, a JSON list of
   {"id", "date", "topic", "keywords": [...], "says"}. With no file there is no check. override_grant='<why it does
   not fit>' lets the question through and appends the reason to grant_overrides.jsonl beside the grants file.
3. A mic opens only on the person's own note. listen_start (and any recorder that opens an input device) takes the
   id of a note the person sent from the phone page (a voice note, or typed words in a panel), sent within
   MIC_NOTE_MAX_H hours, that names the mic or recording and does not read as a no.

The local files are read on every call, so a change takes effect at once. Nothing they hold is written to a tracked
file: a refusal names the matched term to the agent only.
"""
import datetime
import functools
import json
import os
import re
import subprocess
from pathlib import Path

from .api import OpError

PRIVATE_TERMS_ENV = 'ISMAIL_PRIVATE_TERMS'
GRANTS_ENV = 'ISMAIL_STANDING_GRANTS'
MIC_NOTE_MAX_H = 12.0


def _local_dir():
    return Path.home() / '.ismail'


def _flat(values):
    """Every string inside values (lists, tuples and dict values, any depth)."""
    out = []
    stack = list(values)
    while stack:
        v = stack.pop(0)
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            stack.extend(v.values())
        elif isinstance(v, (list, tuple, set)):
            stack.extend(v)
        elif isinstance(v, (int, float)):
            continue
        else:
            out.append(str(v))
    return out


def _word_re(term):
    """A whole-word, case-insensitive pattern for a term; inner spaces match any run of spaces."""
    parts = [re.escape(p) for p in term.split()]
    return re.compile(r'(?<!\w)' + r'\s+'.join(parts) + r'(?!\w)', re.IGNORECASE)


# ------------------------------------------------------------------ 1. nothing personal on a page

def private_terms_path():
    return Path(os.environ.get(PRIVATE_TERMS_ENV) or _local_dir() / 'private_terms.txt').expanduser()


@functools.lru_cache(maxsize=1)
def _git_email():
    """The user.email git uses for this checkout (a worktree shares its main checkout's config), or None."""
    try:
        r = subprocess.run(['git', '-C', str(Path(__file__).resolve().parent), 'config', 'user.email'],
                           capture_output=True, text=True, timeout=10,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
    except (OSError, subprocess.SubprocessError):
        return None
    email = (r.stdout or '').strip()
    return email if '@' in email else None


def private_terms():
    """(terms, file found): the terms in the local file, plus the git email."""
    p = private_terms_path()
    terms, found = [], False
    try:
        text = p.read_text(encoding='utf8')
        found = True
    except OSError:
        text = ''
    for line in text.splitlines():
        t = line.strip()
        if t and not t.startswith('#'):
            terms.append(t)
    email = _git_email()
    if email and email.lower() not in (t.lower() for t in terms):
        terms.append(email)
    return terms, found


def private_hit(*values):
    """The first private term that appears in any of the values (strings, or lists and dicts of them), else None."""
    terms, _ = private_terms()
    texts = _flat(values)
    if not terms or not texts:
        return None
    for t in terms:
        if '@' in t:
            if any(t.lower() in s.lower() for s in texts):
                return t
        elif any(_word_re(t).search(s) for s in texts):
            return t
    return None


def scan_off_note():
    """One line for the reply while the terms file is missing, '' once it is there."""
    if private_terms()[1]:
        return ''
    return (f"(personal-data scan is off for places and names: no {private_terms_path()}; write one place or name per "
            f"line there, or point ${PRIVATE_TERMS_ENV} at such a file, to turn it on. Only the git email is checked "
            f"now.)")


def check_private(*values, where='the phone page'):
    """Refuse text that names a private term. Returns the scan-off line ('' when the scan is on)."""
    hit = private_hit(*values)
    if hit:
        raise OpError(f"text names a place or personal detail ({hit}); {where} never says where the person is or who "
                      f"they are. Rephrase without it.")
    return scan_off_note()


def with_note(reply, note):
    """The reply with the note on its own line (a reply that is not text is left alone)."""
    if note and isinstance(reply, str):
        return reply + '\n' + note
    return reply


# ------------------------------------------------------------------ 2. standing grants

YES_WORDS = {'yes', 'y', 'yep', 'yeah', 'ok', 'okay', 'sure', 'approve', 'approved', 'accept', 'allow', 'confirm',
             'go', 'go ahead', 'do it', 'proceed', 'agree', 'merge', 'restart', 'continue', 'thumbs up'}
NO_WORDS = {'no', 'n', 'nope', 'deny', 'reject', 'decline', 'cancel', 'not now', 'not yet', 'later', 'wait', 'hold',
            'skip', 'stop', 'dont', "don't", 'refuse', 'thumbs down', 'leave it'}
_YES_FIRST = {'yes', 'yep', 'yeah', 'ok', 'okay', 'sure', 'approve', 'accept', 'allow', 'confirm', 'go', 'proceed',
              'agree', 'merge', 'restart'}
_NO_FIRST = {'no', 'nope', 'deny', 'reject', 'decline', 'cancel', 'not', 'later', 'wait', 'hold', 'skip', 'stop',
             'dont', "don't", 'refuse'}
# a caption that asks a yes/no question: "should I merge #12?", "want me to restart the room?"
_ASKS = re.compile(r"\b(should i|shall i|can i|may i|could i|do you want|want me to|ok(ay)? (to|if)|is it ok(ay)?|"
                   r"are you ok(ay)? with|good to go|mind if i|go ahead)\b", re.IGNORECASE)


def grants_path():
    return Path(os.environ.get(GRANTS_ENV) or _local_dir() / 'standing_grants.json').expanduser()


def overrides_log():
    return grants_path().parent / 'grant_overrides.jsonl'


def _label(b):
    if isinstance(b, dict):
        b = b.get('label') or b.get('id') or ''
    s = re.sub(r"[^\w\s']+", ' ', str(b).lower()).strip()
    return re.sub(r'\s+', ' ', s)


def _side(b):
    s = _label(b)
    first = s.split(' ', 1)[0] if s else ''
    if s in YES_WORDS or first in _YES_FIRST:
        return 'yes'
    if s in NO_WORDS or first in _NO_FIRST:
        return 'no'
    return None


def is_yes_no(buttons=None, inputs=None, text=''):
    """A yes/no or approve/deny panel: every button (or every option of a choice input) is a yes or a no word, at
    least one is a yes, and there are two of them or the text asks (a lone 'OK' on a message is no question)."""
    groups = [list(buttons or [])]
    for x in inputs or []:
        if isinstance(x, dict) and x.get('kind') == 'choice' and x.get('options'):
            groups.append(list(x['options']))
    for g in groups:
        if not g or len(g) > 3:
            continue
        sides = [_side(b) for b in g]
        if None in sides or 'yes' not in sides:
            continue
        if len(g) >= 2 or '?' in (text or ''):
            return True
    return False


def asks_yes_no(text):
    """A caption that asks a yes/no question ("should I ...?")."""
    return '?' in (text or '') and bool(_ASKS.search(text or ''))


def load_grants():
    """(grants, problem): the grants with keywords; problem is a line to show when the file does not read."""
    p = grants_path()
    try:
        raw = p.read_text(encoding='utf8')
    except OSError:
        return [], ''
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError('it is not a JSON list')
    except ValueError as e:
        return [], (f"(standing grants not checked: {p} does not read as a JSON list of "
                    f"{{id, date, topic, keywords, says}} ({e}))")
    out = [g for g in data if isinstance(g, dict) and isinstance(g.get('keywords'), list) and g['keywords']]
    return out, ''


def grant_for(*values):
    """The first grant one of whose keywords appears, whole word, in the values; else None."""
    grants, _ = load_grants()
    texts = _flat(values)
    for g in grants:
        for k in g['keywords']:
            k = str(k).strip()
            if k and any(_word_re(k).search(s) for s in texts):
                return g
    return None


def check_grant(question, yes_no, override_grant=None, op_name=''):
    """Refuse a yes/no question a standing grant covers, unless override_grant gives a reason (then it is logged).
    question: the words asked (title, text). Returns a line for the reply, or ''."""
    if not yes_no:
        return ''
    grants, problem = load_grants()
    if problem:
        return problem
    if not grants:
        return ''
    g = grant_for(*([question] if isinstance(question, str) else question))
    if g is None:
        return ''
    why = str(override_grant or '').strip()
    if not why:
        if override_grant is not None:
            raise OpError("override_grant needs the reason the standing grant does not fit, in words (it is logged); "
                          "an empty one is no reason")
        raise OpError(f"a standing grant covers this ({g.get('id')}, {g.get('date')}: {g.get('says')}); act on it, "
                      f"then post a caption saying what you did. If the grant really doesn't fit, pass "
                      f"override_grant='<why it doesn't fit>' and the reason is logged.")
    log = overrides_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    q = ' | '.join(_flat([question]))[:300]
    with open(log, 'a', encoding='utf8') as f:
        f.write(json.dumps({'ts': datetime.datetime.now().isoformat(timespec='seconds'), 'op': op_name,
                            'grant': g.get('id'), 'question': q, 'why': why}, ensure_ascii=False) + '\n')
    return f"(standing grant {g.get('id')} set aside: {why}; logged in {log})"


# ------------------------------------------------------------------ 3. the mic needs the person's own note

MIC_WORDS = re.compile(r'\b(mic|mics|microphone|record|recording|listen|listening)\b', re.IGNORECASE)
MIC_NO = re.compile(r"\b(no|not|don'?t|do not|never|stop|delete|off|cancel)\b", re.IGNORECASE)
_TRUE = {'true', 'yes', '1', 'y', 'on'}
NEEDS_NOTE = ("mic_ok needs the id of the person's note saying yes to the mic in this session (phone_watch lists "
              "ids); ask them in words first.")


def _phone_home():
    from .phone import server as S
    return Path(S.HOME)


def _inbox_rows():
    p = _phone_home() / 'inbox.jsonl'
    rows = []
    try:
        with open(p, encoding='utf8') as f:
            for ln in f:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        pass
    return p, rows


def _note_text(r):
    k = r.get('kind')
    if k == 'voice_text':
        return str(r.get('text') or '')
    if k == 'answer':                           # their words in the panel's fields, not the button they tapped
        return ' '.join(_flat([r.get('values')]))
    if k == 'exam':
        return str((r.get('answers') or {}).get('note') or '')
    return ''


def mic_note(note):
    """The person's own note that says yes to the mic: {'id', 'n', 'ts', 'text'}; OpError with the next step if it
    is missing, not theirs, too old, silent about the mic, or a no. note: a voice note id (phone_watch's `id`) or the
    inbox line number of a typed answer (its `n`)."""
    if note is None or note is False or (isinstance(note, str) and not note.strip()):
        raise OpError(NEEDS_NOTE)
    if note is True or (isinstance(note, str) and note.strip().lower() in _TRUE):
        raise OpError(NEEDS_NOTE)
    key = str(note).strip()
    path, rows = _inbox_rows()
    by_n = None
    if re.fullmatch(r'n?\d+', key):
        n = int(key.lstrip('n'))
        by_n = next((r for r in rows if r.get('n') == n), None)
    r = by_n
    if r is None:
        r = next((x for x in reversed(rows) if x.get('id') == key and x.get('kind') == 'voice_text'), None)
    if r is None:
        r = next((x for x in reversed(rows) if x.get('id') == key and x.get('kind') in ('answer', 'exam')), None)
    if r is None:
        raise OpError(f"no note {key!r} in the phone inbox ({path}): phone_watch lists the person's notes with their "
                      f"ids; pass the id of the one where they said yes to the mic, or ask them in words first.")
    k = r.get('kind')
    own = False
    if k == 'voice_text':                       # the words of a voice note: its recording came from the page
        v = next((x for x in rows if x.get('kind') == 'voice' and x.get('id') == r.get('id')), None)
        own = bool(v and v.get('sid') and v.get('file') and Path(v['file']).is_file())
    elif k in ('answer', 'exam'):
        own = bool(r.get('sid')) and not r.get('dismissed')
    if not own:
        what = f"a {k} line" if k else 'a line'
        raise OpError(f"note {key!r} is not the person's own note ({what} the page did not send from their phone); only "
                      f"a voice note or typed words they sent count: ask them in words first, then pass that note's "
                      f"id.")
    try:
        ts = datetime.datetime.fromisoformat(str(r.get('ts')))
    except ValueError:
        ts = None
    now = datetime.datetime.now(ts.tzinfo) if ts is not None and ts.tzinfo else datetime.datetime.now()
    if ts is None or (now - ts).total_seconds() > MIC_NOTE_MAX_H * 3600:
        raise OpError(f"note {key!r} is from {r.get('ts') or 'an unknown time'}, more than {MIC_NOTE_MAX_H:g} h ago; "
                      f"the yes is for this session: ask them again in words and pass the new note's id.")
    text = _note_text(r).strip()
    if not MIC_WORDS.search(text):
        raise OpError(f"note {key!r} does not mention the mic or recording (it says: \"{text[:160]}\"); ask them "
                      f"about the mic in words and pass the note where they say yes to it.")
    if MIC_NO.search(text):
        raise OpError(f"note {key!r} reads as a no to the mic (\"{text[:160]}\"): do not record. If they meant yes, "
                      f"ask them for a plain yes and pass that note's id.")
    return {'id': r.get('id'), 'n': r.get('n'), 'ts': r.get('ts'), 'text': text}
