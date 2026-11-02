"""Who is who across places: one people file maps a person's accounts ("discord:123") to one name, and a one-time
pairing code links a new account. Everyone not in it is a guest, named by the platform's own id."""
import hashlib
import secrets
import time

from . import config as C

ROLES = ('owner', 'friend')
CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'      # no 0/O or 1/I to misread
CODE_MIN = 10


def path():
    return C.HOME / 'people.json'


def load():
    d = C._read(path(), {})
    d.setdefault('people', {})
    d.setdefault('codes', {})
    return d


def account(platform, user_id):
    return f'{platform}:{user_id}'


def who(platform, user_id):
    """(person, role) for a platform account, or (None, None) for a guest."""
    acc = account(platform, user_id)
    for name, p in load()['people'].items():
        if acc in p.get('accounts', []):
            return name, p.get('role', 'friend')
    return None, None


def role_of(person):
    """A person's role: as stored, else owner for the hub.owner name, else friend."""
    stored = load()['people'].get(person, {}).get('role')
    return stored or ('owner' if person == C.owner_name() else 'friend')


def accounts_of(person):
    return list(load()['people'].get(person, {}).get('accounts', []))


def link(person, acc, role=None):
    """Link an account to a person (the person's own act: `python -m ismail.hub link`, or a pairing code)."""
    if ':' not in acc:
        raise ValueError(f"an account is platform:id, e.g. discord:123456789012345678 (got {acc!r})")
    d = load()
    for name, p in d['people'].items():
        if acc in p.get('accounts', []) and name != person:
            raise ValueError(f"{acc} is already {name}'s: unlink it first (python -m ismail.hub unlink {acc})")
    p = d['people'].setdefault(person, {'accounts': [], 'role': role or role_of(person)})
    if role:
        p['role'] = role
    if acc not in p['accounts']:
        p['accounts'].append(acc)
    C._write(path(), d)
    return p


def unlink(acc):
    d = load()
    gone = [n for n, p in d['people'].items() if acc in p.get('accounts', [])]
    for n in gone:
        d['people'][n]['accounts'].remove(acc)
    C._write(path(), d)
    return gone


def _h(code):
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def new_code(person, minutes=CODE_MIN):
    """A one-time code that links whichever account sends it (`pair <code>`) to this person, for `minutes`."""
    d = load()
    now = time.time()
    d['codes'] = {h: c for h, c in d['codes'].items() if c['until'] > now}
    code = ''.join(secrets.choice(CODE_CHARS) for _ in range(8))
    d['codes'][_h(code)] = {'person': person, 'until': now + minutes * 60}
    C._write(path(), d)
    return code


def redeem(code, platform, user_id):
    """The person a valid code names, now linked to this account (the code is used up), or None."""
    d = load()
    c = d['codes'].pop(_h(code), None)
    C._write(path(), d)
    if not c or c['until'] < time.time():
        return None
    link(c['person'], account(platform, user_id))
    return c['person']
