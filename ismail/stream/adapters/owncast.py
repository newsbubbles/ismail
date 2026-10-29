"""Owncast (self-hosted) chat: read over its websocket as a registered chat user, reply with an integration token.

channel = the server's address (https://live.example.org). Reading: POST /api/chat/register gives a chat access
token, then wss://<server>/ws?accessToken=... carries CHAT events. Replying: POST /api/integrations/chat/send with
the person's integration token (`python -m ismail.stream login <venue>` asks for it)."""
import json

from ..net import http, ws_connect
from .base import Adapter, NeedsLogin


class OwncastAdapter(Adapter):
    platform = 'owncast'

    def __init__(self, venue, hub):
        super().__init__(venue, hub)
        self.ws = None
        self.base = str(venue.get('channel', '')).rstrip('/')

    def close(self):
        try:
            self.ws and self.ws.close()
        except Exception:
            pass

    def session(self):
        reg = http('POST', f'{self.base}/api/chat/register', json_body={'displayName': 'listener'})
        ws_url = self.base.replace('https://', 'wss://').replace('http://', 'ws://') + \
            f"/ws?accessToken={reg['accessToken']}"
        self.ws = ws_connect(ws_url)
        self.connected()
        while not self.stop_ev.is_set():
            raw = self.ws.recv(timeout=120)
            for part in str(raw).split('\n'):              # Owncast may batch events, one JSON per line
                if part.strip():
                    self._on(json.loads(part))

    def _on(self, m):
        t, u = m.get('type'), m.get('user') or {}
        user = {'id': str(u.get('id', '')), 'name': str(u.get('displayName', '')).lower(),
                'display': u.get('displayName')}
        if u.get('isBot') or 'integration' in str(u.get('scopes', '')).lower():
            user['bot'] = True
        if t == 'CHAT':
            self.chat(user, m.get('body', ''), id=m.get('id'))
        elif t == 'USER_JOINED':
            self.event('join', user=user, id=m.get('id'))
        elif t == 'FEDIVERSE_ENGAGEMENT_FOLLOW':
            self.event('follow', user={'id': '', 'name': '', 'display': m.get('title') or m.get('body')})

    def post(self, text, reply_to=None):
        tok = self.keys().get('token')
        if not tok:
            raise NeedsLogin(f"replies need the Owncast token: `python -m ismail.stream login {self.name}`")
        http('POST', f'{self.base}/api/integrations/chat/send', {'Authorization': f'Bearer {tok}'},
             json_body={'body': text})
        return None
