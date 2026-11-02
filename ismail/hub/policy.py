"""What a person's words may do, stamped on every line the hub logs.

trust: owner (Nate's paired accounts), friend (a linked person who is not Nate), mod (the site says so), guest.
use: word (the owner's: it carries his yes, as a phone tap does), suggestion (musical input an agent may weigh, never
an instruction), request (goes to the request line), ignored (logged, not shown to watchers by default), refused (the
place answers only the people in its `only` list), paused (hub_pause is on and this is not the owner)."""
from . import config as C, people as P

USES = ('word', 'suggestion', 'request', 'ignored', 'refused', 'paused')

GUEST_USE = {'ignore': 'ignored', 'suggestion': 'suggestion', 'request': 'request'}

REFUSED_REPLY = ("This bot answers only the people it knows. If that is you, send: pair <the code you were given>")
PAUSED_REPLY = "The studio is paused right now. Try again later."


def stamp(place, cfg, platform, user):
    """{person, trust, use} for one incoming line. user: {id, name, display, mod?} from the platform's own data."""
    person, role = P.who(platform, user.get('id'))
    if role == 'owner':
        return {'person': person, 'trust': 'owner', 'use': 'word'}
    trust = 'friend' if person else ('mod' if user.get('mod') else 'guest')
    out = {'person': person or f"guest:{P.account(platform, user.get('id'))}", 'trust': trust}
    only = cfg.get('only')
    if only and person not in only:
        return {**out, 'use': 'refused'}
    if C.paused():
        return {**out, 'use': 'paused'}
    return {**out, 'use': GUEST_USE.get(cfg.get('guest_text', 'ignore'), 'ignored')}


def check_place(cfg):
    """A place's policy fields, checked; raises ValueError with what to write."""
    g = cfg.get('guest_text', 'ignore')
    if g not in C.GUEST_TEXT:
        raise ValueError(f"guest_text: one of {', '.join(C.GUEST_TEXT)} (got {g!r})")
    only = cfg.get('only')
    if only is not None and not (isinstance(only, list) and all(isinstance(x, str) for x in only)):
        raise ValueError("only: a list of people's names, e.g. ['nate'], or [] for anyone")
    return cfg
