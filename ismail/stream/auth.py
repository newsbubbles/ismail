"""Logins and stream keys: run by the person in a terminal, never by an agent, and never printed.

  python -m ismail.stream login <venue>     sign the venue's account in (chat replies, events)
  python -m ismail.stream key <venue>       paste the venue's stream key (hidden while typed)

Twitch and YouTube use the device flow: the terminal shows a short code and an address; the person opens it on any
device, signs in and types the code. Kick opens the browser and catches the answer on 127.0.0.1. Each site wants an
app of the person's own first (a client id): the login says where to make it, once."""
import base64
import getpass
import hashlib
import http.server
import secrets
import threading
import time
import urllib.parse
import webbrowser

from . import config as C
from .net import HttpError, http

TWITCH_SCOPES = ['user:read:chat', 'user:write:chat', 'moderator:read:followers', 'channel:read:subscriptions',
                 'bits:read', 'channel:read:redemptions']
YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube']
KICK_SCOPES = ['user:read', 'channel:read', 'chat:write', 'events:subscribe']
KICK_PORT = 8876

APP_HELP = {
    'twitch': "Make a Twitch app once: https://dev.twitch.tv/console/apps -> Register Your Application; OAuth "
              "Redirect URL http://localhost; Client Type: Public. Copy its Client ID.",
    'youtube': "Make a Google OAuth client once: https://console.cloud.google.com -> a project -> enable \"YouTube "
               "Data API v3\" -> OAuth consent screen (External; publish it, or logins expire every 7 days) -> "
               "Credentials -> Create OAuth client ID -> type \"TVs and Limited Input devices\". Copy its Client ID "
               "and Client secret.",
    'kick': f"Make a Kick app once: https://kick.com/settings/developer -> Create new app; Redirect URL "
            f"http://localhost:{KICK_PORT}/callback; tick the scopes it lists. Copy its Client ID and Client secret.",
    'owncast': "In the Owncast admin: Integrations -> Access Tokens -> Create, with \"Can send chat messages\". Copy "
               "the token.",
}


def _ask(prompt, secret=False):
    v = (getpass.getpass if secret else input)(prompt).strip()
    if not v:
        raise SystemExit('nothing typed: stopped, nothing saved')
    return v


def _device_flow(start_url, token_url, form, poll_form, show):
    d = http('POST', start_url, form=form)
    print(f"\nOpen {d.get('verification_uri') or d.get('verification_url')} on any device, sign in to the account "
          f"that owns the channel, and type this code:\n\n    {d['user_code']}\n\n(waiting up to "
          f"{int(d.get('expires_in', 600)) // 60} minutes)", flush=True)
    every = float(d.get('interval', 5))
    end = time.time() + float(d.get('expires_in', 600))
    while time.time() < end:
        time.sleep(every)
        try:
            return http('POST', token_url, form={**poll_form, 'device_code': d['device_code']})
        except HttpError as e:
            err = e.body.get('error') if isinstance(e.body, dict) else ''
            msg = e.body.get('message', '') if isinstance(e.body, dict) else ''
            if err == 'authorization_pending' or 'pending' in str(msg):
                continue
            if err == 'slow_down':
                every += 5
                continue
            raise SystemExit(f'the login did not finish: {e}')
    raise SystemExit('the code ran out before it was typed: run the login again')


def login(venue):
    v = C.load_venues().get(venue)
    if not v:
        raise SystemExit(f"no venue {venue!r}: an agent adds it with venue_set first")
    k = C.load_keys(venue)
    p = v['platform']
    if p == 'twitch':
        if not k.get('client_id'):
            print(APP_HELP['twitch'])
            k['client_id'] = _ask('Twitch Client ID: ')
        tok = _device_flow('https://id.twitch.tv/oauth2/device', 'https://id.twitch.tv/oauth2/token',
                           {'client_id': k['client_id'], 'scopes': ' '.join(TWITCH_SCOPES)},
                           {'client_id': k['client_id'], 'scopes': ' '.join(TWITCH_SCOPES),
                            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}, print)
        _take(k, tok)
        who = http('GET', 'https://id.twitch.tv/oauth2/validate', {'Authorization': f"OAuth {k['access_token']}"})
        k['login_name'], k['user_id'] = who.get('login'), who.get('user_id')
    elif p == 'youtube':
        if not k.get('client_id') or not k.get('client_secret'):
            print(APP_HELP['youtube'])
            k['client_id'] = _ask('Google Client ID: ')
            k['client_secret'] = _ask('Google Client secret (hidden): ', secret=True)
        tok = _device_flow('https://oauth2.googleapis.com/device/code', 'https://oauth2.googleapis.com/token',
                           {'client_id': k['client_id'], 'scope': ' '.join(YOUTUBE_SCOPES)},
                           {'client_id': k['client_id'], 'client_secret': k['client_secret'],
                            'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'}, print)
        _take(k, tok)
        me = http('GET', 'https://www.googleapis.com/youtube/v3/channels', {'Authorization': f"Bearer {k['access_token']}"},
                  params={'part': 'snippet', 'mine': 'true'})
        it = (me.get('items') or [{}])[0]
        k['login_name'], k['user_id'] = (it.get('snippet') or {}).get('title'), it.get('id')
    elif p == 'kick':
        if not k.get('client_id') or not k.get('client_secret'):
            print(APP_HELP['kick'])
            k['client_id'] = _ask('Kick Client ID: ')
            k['client_secret'] = _ask('Kick Client secret (hidden): ', secret=True)
        _take(k, _kick_pkce(k))
        try:
            me = http('GET', 'https://api.kick.com/public/v1/users', {'Authorization': f"Bearer {k['access_token']}"})
            u = (me.get('data') or [{}])[0]
            k['login_name'], k['user_id'] = u.get('name'), u.get('user_id')
        except HttpError:
            pass
    elif p == 'owncast':
        print(APP_HELP['owncast'])
        k['token'] = _ask('Owncast access token (hidden): ', secret=True)
    else:
        raise SystemExit(f"{p} has no login here (it has no chat this tool can answer)")
    C.save_keys(venue, k)
    print(f"\nlogged in{(' as ' + k['login_name']) if k.get('login_name') else ''}. Saved to {C.keys_path(venue)} "
          f"(this computer only). The stream server picks it up within a minute.")


def _take(k, tok):
    k['access_token'] = tok['access_token']
    if tok.get('refresh_token'):
        k['refresh_token'] = tok['refresh_token']
    k['expires_at'] = time.time() + float(tok.get('expires_in') or 3600) - 60


def _kick_pkce(k):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    state = secrets.token_urlsafe(16)
    redirect = f'http://localhost:{KICK_PORT}/callback'
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get('state', [''])[0] == state:
                got.update({x: y[0] for x, y in q.items()})
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'Done: you can close this tab and go back to the terminal.')

    srv = http.server.HTTPServer(('127.0.0.1', KICK_PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = 'https://id.kick.com/oauth/authorize?' + urllib.parse.urlencode({
        'response_type': 'code', 'client_id': k['client_id'], 'redirect_uri': redirect, 'scope': ' '.join(KICK_SCOPES),
        'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state})
    print(f"\nOpening the Kick sign-in in your browser. If it does not open, paste this address into it:\n{url}\n",
          flush=True)
    webbrowser.open(url)
    end = time.time() + 300
    while not got and time.time() < end:
        time.sleep(0.5)
    srv.shutdown()
    if not got.get('code'):
        raise SystemExit('no answer from Kick within 5 minutes: run the login again')
    return http('POST', 'https://id.kick.com/oauth/token', form={
        'grant_type': 'authorization_code', 'client_id': k['client_id'], 'client_secret': k['client_secret'],
        'redirect_uri': redirect, 'code_verifier': verifier, 'code': got['code']})


def fresh_token(venue, platform):
    """The venue's access token, renewed first when it is about to run out. None with no login."""
    k = C.load_keys(venue)
    if not k.get('access_token'):
        return None
    if k.get('expires_at', 0) > time.time() + 120 or not k.get('refresh_token'):
        return k['access_token']
    form = {'grant_type': 'refresh_token', 'refresh_token': k['refresh_token'], 'client_id': k.get('client_id')}
    url = {'twitch': 'https://id.twitch.tv/oauth2/token', 'youtube': 'https://oauth2.googleapis.com/token',
           'kick': 'https://id.kick.com/oauth/token'}[platform]
    if platform in ('youtube', 'kick'):
        form['client_secret'] = k.get('client_secret')
    tok = http('POST', url, form=form)
    _take(k, tok)
    C.save_keys(venue, k)
    return k['access_token']


def set_key(venue):
    v = C.load_venues().get(venue)
    if not v:
        raise SystemExit(f"no venue {venue!r}: an agent adds it with venue_set first")
    if not C.PLATFORMS[v['platform']]['output'] or v['platform'] == 'file':
        raise SystemExit(f"{v['platform']} takes no stream key")
    where = {'twitch': 'Twitch: Creator Dashboard -> Settings -> Stream -> Primary Stream key',
             'youtube': 'YouTube Studio -> Create -> Go live -> Stream -> Stream key',
             'kick': 'Kick: Creator Dashboard -> Settings -> Stream URL & Key (copy both)',
             'owncast': 'the Owncast admin -> Configuration -> Server Setup -> Stream Keys'}.get(v['platform'], '')
    if where:
        print(f"Where to find it: {where}")
    if not v.get('ingest') and not C.INGEST.get(v['platform']):
        addr = _ask('The RTMP(S) server address (rtmp://... or rtmps://..., without the key): ')
        if not addr.startswith(('rtmp://', 'rtmps://')):
            raise SystemExit('that is not an rtmp:// or rtmps:// address: nothing saved')
        venues = C.load_venues()
        venues[venue]['ingest'] = addr.rstrip('/')
        C.save_venues(venues)
    k = C.load_keys(venue)
    k['stream_key'] = _ask('Stream key (hidden while you type or paste): ', secret=True)
    C.save_keys(venue, k)
    print(f"saved to {C.keys_path(venue)} (this computer only).")
