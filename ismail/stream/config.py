"""Venues (no secrets) and their keys (the person's, never shown to an agent)."""
import json
import os
import re
import time
from pathlib import Path
from .. import settings

HOME = Path(settings.get('stream.home'))
PORT = 8875

# what each platform can do here: chat read, chat send, output (push video to it)
PLATFORMS = {
    'fake': {'chat': True, 'send': True, 'output': False,
             'about': "a local chat page (http://127.0.0.1:<port>/fake) where anyone on this machine types as a "
                      "viewer, and a scripted test viewer; for practice and tests"},
    'twitch': {'chat': True, 'send': True, 'output': True,
               'about': "chat in realtime over EventSub (or read-only, anonymous, with no login); replies need "
                        "`python -m ismail.stream login <venue>`"},
    'youtube': {'chat': True, 'send': True, 'output': True,
                'about': "chat read by the Data API (it costs quota, so it is read every few seconds, not in "
                         "realtime); needs `python -m ismail.stream login <venue>`"},
    'kick': {'chat': True, 'send': True, 'output': True,
             'about': "chat read from Kick's public websocket (unofficial: it can break), replies through Kick's "
                      "official API after `python -m ismail.stream login <venue>`"},
    'owncast': {'chat': True, 'send': True, 'output': True,
                'about': "a self-hosted Owncast server: chat over its websocket, replies with its integration "
                         "token"},
    'rtmp': {'chat': False, 'send': False, 'output': True,
             'about': "any other site that takes RTMP(S) with a stream key (Facebook, X, TikTok with access, a "
                      "restreaming service): output only, no chat"},
    'file': {'chat': False, 'send': False, 'output': True,
             'about': "a recording of the stream to a local .flv file (`channel` is the path); for tests and an "
                      "archive of every set"},
}

# default ingest addresses; a venue's own `ingest` wins. The stream key is appended from its keys file.
INGEST = {
    'twitch': 'rtmps://ingest.global-contribute.live-video.net:443/app',
    'youtube': 'rtmps://a.rtmps.youtube.com:443/live2',
    'kick': None,          # each Kick channel has its own RTMPS address: set it with `stream key` (it asks for both)
    'owncast': None,       # rtmp://<your server>:1935/live
    'rtmp': None,
}

# longest chat line each site takes; chat_say refuses longer ones rather than letting the site cut them
MAX_LEN = {'twitch': 500, 'youtube': 200, 'kick': 500, 'owncast': 500, 'fake': 500}

NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')


def venues_path():
    return HOME / 'venues.json'


def load_venues():
    try:
        d = json.loads(venues_path().read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}
    return {v['name']: v for v in d.get('venues', []) if isinstance(v, dict) and v.get('name')}


def save_venues(venues):
    HOME.mkdir(parents=True, exist_ok=True)
    p = venues_path()
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps({'venues': sorted(venues.values(), key=lambda v: v['name'])}, indent=1),
                   encoding='utf8')
    os.replace(tmp, p)


# ------------------------------------------------------------------ keys: written by the person, read by the server

def keys_path(venue):
    return HOME / 'keys' / f'{venue}.json'


def load_keys(venue):
    try:
        return json.loads(keys_path(venue).read_text(encoding='utf8'))
    except (OSError, ValueError):
        return {}


def save_keys(venue, keys):
    p = keys_path(venue)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f'.{os.getpid()}.tmp')
    tmp.write_text(json.dumps(keys, indent=1), encoding='utf8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def key_summary(venue, platform):
    """What an agent may know about a venue's keys: which are there and when the login runs out. Never a value."""
    if platform in ('fake', 'file'):
        return {}
    k = load_keys(venue)
    out = {}
    if PLATFORMS.get(platform, {}).get('output'):
        out['stream_key'] = bool(k.get('stream_key'))
    if PLATFORMS.get(platform, {}).get('send'):
        out['login'] = bool(k.get('access_token') or k.get('token'))
        if k.get('expires_at'):
            left = k['expires_at'] - time.time()
            out['login_expires'] = ('expired; it renews itself' if left < 0 and k.get('refresh_token') else
                                    'expired: run the login again' if left < 0 else f'in {int(left // 60)} min')
        if k.get('login_name'):
            out['login_as'] = k['login_name']
    return out


def secrets_of(venue):
    """Every secret value of a venue, so logs can blank them."""
    k = load_keys(venue)
    return [str(v) for f, v in k.items() if v and f in ('stream_key', 'access_token', 'refresh_token', 'token',
                                                         'client_secret') and len(str(v)) >= 6]


def redact(text, secrets):
    for s in secrets:
        text = text.replace(s, '<key>')
    return text
