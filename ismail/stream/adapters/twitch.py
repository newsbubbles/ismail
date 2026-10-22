"""Twitch chat: EventSub over a websocket when the venue is logged in (chat, follows, subs, cheers, raids, channel
points; replies through the Helix API), else read-only anonymous IRC (chat only, no replies).

EventSub: wss://eventsub.wss.twitch.tv/ws sends session_welcome; the subscriptions must be made within 10 s; a
session_reconnect gives a new url to open before the old socket closes. The token is checked at start and hourly
(/oauth2/validate), as Twitch asks."""
import json
import random
import time

from .. import auth
from ..net import HttpError, http, ws_connect
from .base import Adapter, NeedsLogin

EVENTSUB = 'wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30'
IRC = 'wss://irc-ws.chat.twitch.tv:443'
HELIX = 'https://api.twitch.tv/helix'

# (type, version, condition builder): chat first; the rest need the login to be the channel's owner (or a mod)
SUBS = [
    ('channel.chat.message', '1', lambda b, u: {'broadcaster_user_id': b, 'user_id': u}),
    ('channel.chat.notification', '1', lambda b, u: {'broadcaster_user_id': b, 'user_id': u}),
    ('channel.follow', '2', lambda b, u: {'broadcaster_user_id': b, 'moderator_user_id': u}),
    ('channel.cheer', '1', lambda b, u: {'broadcaster_user_id': b}),
    ('channel.raid', '1', lambda b, u: {'to_broadcaster_user_id': b}),
    ('channel.channel_points_custom_reward_redemption.add', '1', lambda b, u: {'broadcaster_user_id': b}),
]


def _user(uid, login, name, badges=()):
    sets = {b.get('set_id') for b in badges or ()}
    d = {'id': str(uid or ''), 'name': (login or '').lower(), 'display': name or login}
    for flag, keys in (('owner', ('broadcaster',)), ('mod', ('moderator',)), ('sub', ('subscriber', 'founder')),
                       ('vip', ('vip',))):
        if sets & set(keys):
            d[flag] = True
    return d


class TwitchAdapter(Adapter):
    platform = 'twitch'

    def __init__(self, venue, hub):
        super().__init__(venue, hub)
        self.ws = None
        self.broadcaster_id = None
        self.validated = 0.0
        self.skipped = []

    def threaded_replies(self):
        return True

    def close(self):
        try:
            self.ws and self.ws.close()
        except Exception:
            pass

    def _headers(self):
        tok = auth.fresh_token(self.name, 'twitch')
        return {'Authorization': f'Bearer {tok}', 'Client-Id': self.keys().get('client_id', '')}

    def _validate(self):
        k = self.keys()
        try:
            who = http('GET', 'https://id.twitch.tv/oauth2/validate',
                       {'Authorization': f"OAuth {auth.fresh_token(self.name, 'twitch')}"})
        except HttpError as e:
            if e.status == 401:
                raise NeedsLogin("the Twitch login has run out: the person runs `python -m ismail.stream login "
                                 f"{self.name}` again")
            raise
        self.validated = time.time()
        self.self_ids = {str(who.get('user_id') or k.get('user_id'))}
        return str(who.get('user_id'))

    def session(self):
        if self.keys().get('access_token'):
            self.reading = 'realtime (EventSub)'
            return self._eventsub()
        self.reading = 'realtime, read-only (no login: replies need `python -m ismail.stream login`)'
        return self._irc()

    # ---------------------------------------------------------------- EventSub (logged in)
    def _eventsub(self, url=EVENTSUB, old=None):
        uid = self._validate()
        if not self.broadcaster_id:
            got = http('GET', f'{HELIX}/users', self._headers(), params={'login': self.v['channel']})
            if not got.get('data'):
                raise RuntimeError(f"no Twitch channel {self.v['channel']!r}: fix the venue's channel")
            self.broadcaster_id = got['data'][0]['id']
        self.ws = ws_connect(url)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        while not self.stop_ev.is_set():
            raw = self.ws.recv(timeout=75)          # keepalives come every 30 s; silence past that means a dead socket
            m = json.loads(raw)
            mt = m['metadata']['message_type']
            if mt == 'session_welcome':
                if old is None:                     # a reconnect keeps its subscriptions
                    self._subscribe(m['payload']['session']['id'], uid)
                self.connected()
            elif mt == 'session_reconnect':
                return self._eventsub(m['payload']['session']['reconnect_url'], old=self.ws)
            elif mt == 'notification':
                self._on(m['payload']['subscription']['type'], m['payload']['event'])
            elif mt == 'revocation':
                self.hub.log.add('status', self.name, platform='twitch', state='revoked',
                                 text=f"Twitch stopped {m['payload']['subscription']['type']}: "
                                      f"{m['payload']['subscription'].get('status')}")
            if time.time() - self.validated > 3600:
                self._validate()

    def _subscribe(self, session_id, uid):
        self.skipped = []
        for typ, ver, cond in SUBS:
            try:
                http('POST', f'{HELIX}/eventsub/subscriptions', self._headers(), json_body={
                    'type': typ, 'version': ver, 'condition': cond(self.broadcaster_id, uid),
                    'transport': {'method': 'websocket', 'session_id': session_id}})
            except HttpError as e:
                if typ == 'channel.chat.message':
                    if e.status == 401:
                        raise NeedsLogin(f"Twitch refused the login ({e}): run `python -m ismail.stream login "
                                         f"{self.name}` again")
                    raise
                self.skipped.append(typ.split('.', 1)[1])  # follows etc. need the channel's own login: say, go on
        if self.skipped:
            self.hub.log.add('status', self.name, platform='twitch', state='reading',
                             text=f"no {', '.join(self.skipped)} events: they need the channel owner's login")

    def _on(self, typ, e):
        if typ == 'channel.chat.message':
            u = _user(e.get('chatter_user_id'), e.get('chatter_user_login'), e.get('chatter_user_name'),
                      e.get('badges'))
            extra = {}
            if e.get('cheer'):
                extra['bits'] = e['cheer'].get('bits')
            self.chat(u, (e.get('message') or {}).get('text', ''), id=e.get('message_id'),
                      reply_to=(e.get('reply') or {}).get('parent_message_id'), **extra)
        elif typ == 'channel.chat.notification':
            nt = e.get('notice_type') or 'unknown'
            kind = {'sub': 'sub', 'resub': 'sub', 'sub_gift': 'gift', 'community_sub_gift': 'gift',
                    'raid': 'raid', 'announcement': 'announcement'}.get(nt)
            if kind:
                u = _user(e.get('chatter_user_id'), e.get('chatter_user_login'), e.get('chatter_user_name'),
                          e.get('badges'))
                extra = {'viewers': (e.get('raid') or {}).get('viewer_count')} if nt == 'raid' else {}
                self.event(kind, user=u, text=e.get('system_message') or (e.get('message') or {}).get('text'),
                           id=e.get('message_id'), notice=nt, **extra)
        elif typ == 'channel.follow':
            self.event('follow', user=_user(e.get('user_id'), e.get('user_login'), e.get('user_name')))
        elif typ == 'channel.cheer':
            self.event('cheer', user=_user(e.get('user_id'), e.get('user_login'), e.get('user_name')),
                       text=e.get('message'), amount=e.get('bits'))
        elif typ == 'channel.raid':
            if 'chat.notification' in ' '.join(self.skipped):      # else the notification already said it
                self.event('raid', user=_user(e.get('from_broadcaster_user_id'), e.get('from_broadcaster_user_login'),
                                              e.get('from_broadcaster_user_name')), viewers=e.get('viewers'))
        elif typ == 'channel.channel_points_custom_reward_redemption.add':
            self.event('redeem', user=_user(e.get('user_id'), e.get('user_login'), e.get('user_name')),
                       text=e.get('user_input'), reward=(e.get('reward') or {}).get('title'),
                       amount=(e.get('reward') or {}).get('cost'), id=e.get('id'))

    def post(self, text, reply_to=None):
        k = self.keys()
        if not k.get('access_token'):
            raise RuntimeError(f"read-only: replies need the login (`python -m ismail.stream login {self.name}`)")
        body = {'broadcaster_id': self.broadcaster_id, 'sender_id': k.get('user_id'), 'message': text}
        if reply_to:
            body['reply_parent_message_id'] = reply_to
        res = http('POST', f'{HELIX}/chat/messages', self._headers(), json_body=body)
        d = (res.get('data') or [{}])[0]
        if not d.get('is_sent', True):
            dr = d.get('drop_reason') or {}
            raise RuntimeError(f"Twitch dropped it ({dr.get('code', '?')}): {dr.get('message', '')}")
        return d.get('message_id')

    def send(self, text, reply_to=None):
        owner = self.broadcaster_id in self.self_ids             # the channel's own login: 100 per 30 s, else 20
        text = self.check_send(text, per_30s=90 if owner else 18)
        mid = self.post(text, reply_to=reply_to)
        self.recent_sends.append((time.time(), text))
        self.sent += 1
        return text, mid

    # ---------------------------------------------------------------- anonymous IRC (read-only)
    def _irc(self):
        self.ws = ws_connect(IRC)
        nick = f'justinfan{random.randint(10000, 99999)}'
        self.ws.send('CAP REQ :twitch.tv/tags twitch.tv/commands')
        self.ws.send(f'NICK {nick}')
        self.ws.send(f"JOIN #{self.v['channel'].lower()}")
        while not self.stop_ev.is_set():
            raw = self.ws.recv(timeout=360)
            for line in raw.split('\r\n'):
                if not line:
                    continue
                if line.startswith('PING'):
                    self.ws.send('PONG' + line[4:])
                    continue
                self._irc_line(line)

    def _irc_line(self, line):
        tags = {}
        if line.startswith('@'):
            t, line = line[1:].split(' ', 1)
            tags = dict(x.split('=', 1) if '=' in x else (x, '') for x in t.split(';'))
        parts = line.split(' ', 3)
        if len(parts) < 3:
            return
        cmd = parts[1]
        if cmd == '366' or (cmd == 'ROOMSTATE' and self.state != 'reading'):
            self.connected()
        elif cmd == 'PRIVMSG' and len(parts) == 4:
            login = parts[0][1:].split('!', 1)[0]
            badges = [{'set_id': b.split('/')[0]} for b in tags.get('badges', '').split(',') if b]
            u = _user(tags.get('user-id'), login, tags.get('display-name') or login, badges)
            extra = {'bits': int(tags['bits'])} if tags.get('bits') else {}
            self.chat(u, parts[3][1:] if parts[3].startswith(':') else parts[3], id=tags.get('id'),
                      reply_to=tags.get('reply-parent-msg-id') or None, **extra)
        elif cmd == 'USERNOTICE':
            nt = tags.get('msg-id', '')
            kind = {'sub': 'sub', 'resub': 'sub', 'subgift': 'gift', 'submysterygift': 'gift', 'raid': 'raid',
                    'announcement': 'announcement'}.get(nt)
            if kind:
                login = tags.get('login', '')
                u = _user(tags.get('user-id'), login, tags.get('display-name') or login)
                sysmsg = tags.get('system-msg', '').replace('\\s', ' ')
                self.event(kind, user=u, text=sysmsg, id=tags.get('id'), notice=nt)
        elif cmd == 'RECONNECT':
            raise RuntimeError('Twitch asked for a reconnect')
