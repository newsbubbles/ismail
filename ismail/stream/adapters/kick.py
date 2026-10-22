"""Kick chat. Reading is UNOFFICIAL: Kick's official events come only as webhooks to a public server, so this reads
the public chat feed kick.com's own page uses (a Pusher websocket, credentials asked of Kick at each connect, never
hardcoded). It can break when Kick changes its site; the error then says so. Replies use the official API
(POST /public/v1/chat) after `python -m ismail.stream login <venue>`.

The channel's ids come from kick.com/api/v2/channels/<slug> (behind Cloudflare, so a browser User-Agent); a venue
can set `chatroom_id` and `user_id` itself if that page is blocked."""
import json
import re
import uuid

from .. import auth
from ..net import HttpError, http, ws_connect
from .base import Adapter

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/141.0 Safari/537.36', 'Accept': 'application/json'}
EMOTE = re.compile(r'\[emote:\d+:([^\]]+)\]')
FALLBACK_PUSHER = ('34bf7a0ff419a2a775b9', 'us2')      # what Kick's realtime endpoint gave on 2026-10-22


def _user(sender):
    sender = sender or {}
    badges = {b.get('type') for b in (sender.get('identity') or {}).get('badges') or []}
    d = {'id': str(sender.get('id', '')), 'name': str(sender.get('slug') or sender.get('username') or '').lower(),
         'display': sender.get('username')}
    for flag, keys in (('owner', {'broadcaster'}), ('mod', {'moderator'}), ('sub', {'subscriber', 'founder'}),
                       ('vip', {'vip', 'og'})):
        if badges & keys:
            d[flag] = True
    return d


class KickAdapter(Adapter):
    platform = 'kick'
    reading = 'realtime (unofficial feed)'

    def __init__(self, venue, hub):
        super().__init__(venue, hub)
        self.ws = None
        self.chatroom_id = venue.get('chatroom_id')
        self.user_id = venue.get('user_id')

    def threaded_replies(self):
        return True

    def close(self):
        try:
            self.ws and self.ws.close()
        except Exception:
            pass

    def _ids(self):
        if self.chatroom_id and self.user_id:
            return
        try:
            ch = http('GET', f"https://kick.com/api/v2/channels/{self.v['channel']}", UA)
        except HttpError as e:
            raise RuntimeError(f"kick.com did not give the channel's ids ({e}); it retries. If it keeps failing, "
                               f"Kick's site blocks the lookup: the venue file can carry chatroom_id and user_id") from None
        self.chatroom_id = self.chatroom_id or (ch.get('chatroom') or {}).get('id')
        self.user_id = self.user_id or ch.get('user_id')
        if not self.chatroom_id:
            raise RuntimeError(f"no Kick channel {self.v['channel']!r}: fix the venue's channel (the slug in "
                               f"kick.com/<slug>)")

    def _pusher_key(self):
        try:
            got = http('POST', f'https://web.kick.com/api/v1/realtime/channels/{self.user_id}/chat/connection', UA,
                       json_body={'client': {'id': str(uuid.uuid4()), 'type': 'web'},
                                  'capabilities': {'accepted_providers': [{'provider': 'pusher'}]}})
            c = got.get('credentials') or {}
            if got.get('provider') == 'pusher' and c.get('app_key'):
                return c['app_key'], c.get('cluster', 'us2')
        except (HttpError, OSError, ValueError):
            pass
        return FALLBACK_PUSHER

    def session(self):
        self._ids()
        key, cluster = self._pusher_key()
        self.ws = ws_connect(f'wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=8.4.0&flash=false')
        self.ws.send(json.dumps({'event': 'pusher:subscribe',
                                 'data': {'auth': '', 'channel': f'chatrooms.{self.chatroom_id}.v2'}}))
        if self.keys().get('user_id'):
            self.self_ids = {str(self.keys()['user_id'])}
        while not self.stop_ev.is_set():
            m = json.loads(self.ws.recv(timeout=150))
            ev = m.get('event', '')
            if ev == 'pusher:ping':
                self.ws.send(json.dumps({'event': 'pusher:pong', 'data': {}}))
            elif ev == 'pusher_internal:subscription_succeeded':
                self.connected()
            elif ev == 'pusher:error':
                raise RuntimeError(f"Kick's chat feed refused us ({(m.get('data') or {})}): Kick may have changed "
                                   f"its site; replies still work")
            elif ev.startswith('App\\Events\\'):
                d = m.get('data')
                self._on(ev.split('\\')[-1], json.loads(d) if isinstance(d, str) else (d or {}))

    def _on(self, ev, d):
        if ev == 'ChatMessageEvent':
            reply = ((d.get('metadata') or {}).get('original_message') or {}).get('id')
            text = EMOTE.sub(lambda m: f':{m.group(1)}:', d.get('content', ''))     # [emote:123:name] -> :name:
            self.chat(_user(d.get('sender')), text, id=d.get('id'), reply_to=reply)
        elif ev == 'SubscriptionEvent':
            self.event('sub', user={'id': '', 'name': str(d.get('username', '')).lower(),
                                    'display': d.get('username')}, amount=d.get('months'))
        elif ev == 'GiftedSubscriptionsEvent':
            self.event('gift', user={'id': '', 'name': str(d.get('gifter_username', '')).lower(),
                                     'display': d.get('gifter_username')},
                       amount=len(d.get('gifted_usernames') or []))
        elif ev == 'StreamHostEvent':
            self.event('raid', user={'id': '', 'name': str(d.get('host_username', '')).lower(),
                                     'display': d.get('host_username')}, viewers=d.get('number_viewers'))

    def post(self, text, reply_to=None):
        tok = auth.fresh_token(self.name, 'kick')
        if not tok:
            raise RuntimeError(f"read-only: replies need the login (`python -m ismail.stream login {self.name}`)")
        self._ids()
        body = {'type': 'user', 'broadcaster_user_id': int(self.user_id), 'content': text}
        if reply_to:
            body['reply_to_message_id'] = reply_to
        got = http('POST', 'https://api.kick.com/public/v1/chat', {'Authorization': f'Bearer {tok}'}, json_body=body)
        d = got.get('data') or {}
        if d.get('is_sent') is False:
            raise RuntimeError('Kick did not send it (chat rules dropped it)')
        return d.get('message_id')
