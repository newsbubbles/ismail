"""Places (no secrets), their tokens (the person's, never shown to an agent), and what each platform can show."""
import json
import os
import re
import time
from pathlib import Path

from .. import settings

HOME = Path(settings.get('hub.home'))
PORT = 8877

# what each platform can show; an adapter's caps start here. forms: the input kinds it shows natively (the phone
# panel's kinds: choice, check, toggle, text); the rest fall back to typed answers.
PLATFORMS = {
    'fake': {'about': "a local test place (http://127.0.0.1:<port>/fake): type as any person, press buttons, answer "
                      "forms; for practice and tests",
             'caps': {'text_max': 4000, 'files_out': True, 'file_max_mb': 25, 'audio_inline': True, 'buttons': 25,
                      'forms': ['choice', 'check', 'toggle', 'text'], 'form_max': 10, 'edit': True, 'threads': True,
                      'voice_in': True, 'dm': True}},
    'discord': {'about': "a Discord bot: direct messages and @mentions in channels, buttons, pop-up forms, mp3s "
                         "that play in the chat; needs `python -m ismail.hub token <place>`",
                # 20 MiB a file since 2026-09-03; 20 buttons (a row stays free for a form's Answer button); a form
                # is a modal of up to 5 fields (research 2.1)
                'caps': {'text_max': 2000, 'files_out': True, 'file_max_mb': 20, 'audio_inline': True, 'buttons': 20,
                         'forms': ['text', 'choice', 'check', 'toggle'], 'form_max': 5, 'edit': True,
                         'threads': True, 'voice_in': True, 'dm': True}},
}

GUEST_TEXT = ('ignore', 'suggestion', 'request')
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def _read(p, default):
    try:
        d = json.loads(Path(p).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return default
    return d if isinstance(d, type(default)) else default


def _write(p, d):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding='utf8')
    os.replace(tmp, p)


# ------------------------------------------------------------------ places

def places_path():
    return HOME / 'places.json'


def load_places():
    return _read(places_path(), {})


def save_places(d):
    _write(places_path(), d)


def place_defaults(platform):
    """A new place answers only its owner (only: ['nate']), is not public, and ignores guests' text."""
    return {'public': False, 'guest_text': 'ignore', 'only': ['nate']}


# ------------------------------------------------------------------ keys: written by the person, read by adapters

def keys_path(place):
    return HOME / 'keys' / f'{place}.json'


def load_keys(place):
    return _read(keys_path(place), {})


def save_keys(place, d):
    _write(keys_path(place), {**load_keys(place), **d, 'saved': time.time()})


def key_summary(place):
    k = load_keys(place)
    return 'token in place' if k.get('token') else f'no token: the person runs `python -m ismail.hub token {place}`'


# ------------------------------------------------------------------ the kill switch

def paused():
    return _read(HOME / 'state.json', {}).get('paused')


def set_paused(why):
    st = _read(HOME / 'state.json', {})
    st['paused'] = {'why': why, 'at': time.time()} if why else None
    _write(HOME / 'state.json', st)
