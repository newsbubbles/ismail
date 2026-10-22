"""YouTube live chat through the Data API (a login is needed for both reading and replies).

The chat belongs to the channel's live broadcast, so while the channel is not live the adapter waits (one cheap
lookup a minute). Reading polls liveChatMessages.list: each read costs 5 units of the 10,000 a day, so it reads every
`poll_s` seconds (default 10: about 5,400 units over a 3-hour stream) and never faster than YouTube asks. A reply
costs 20. The units used today are in stream_status. (The streamed reader, streamList, is gRPC only: ledger.)"""
import json
import time

from .. import auth, config as C
from ..net import HttpError, http
from .base import Adapter, NeedsLogin

API = 'https://www.googleapis.com/youtube/v3'
COST = {'list': 5, 'insert': 20, 'broadcasts': 1}


class YouTubeAdapter(Adapter):
    platform = 'youtube'

    def __init__(self, venue, hub):
        super().__init__(venue, hub)
        self.poll_s = max(5.0, float(venue.get('poll_s') or 10))
        self.reading = f'every {int(self.poll_s)} s'
        self.chat_id = None
        self.page = None

    # ---- quota, counted per Pacific-ish day (the reset is at midnight Pacific)
    def _spend(self, what):
        p = C.HOME / 'youtube_quota.json'
        day = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 8 * 3600))
        try:
            q = json.loads(p.read_text(encoding='utf8'))
        except (OSError, ValueError):
            q = {}
        if q.get('day') != day:
            q = {'day': day, 'units': 0}
        q['units'] += COST[what]
        try:
            p.write_text(json.dumps(q), encoding='utf8')
        except OSError:
            pass
        return q['units']

    def status(self):
        d = super().status()
        try:
            q = json.loads((C.HOME / 'youtube_quota.json').read_text(encoding='utf8'))
            d['quota_today'] = f"{q['units']} of 10000 units"
        except (OSError, ValueError, KeyError):
            pass
        if self.state == 'reading' and not self.chat_id:
            d['state'] = 'waiting for the channel to go live'
        return d

    def _h(self):
        tok = auth.fresh_token(self.name, 'youtube')
        if not tok:
            raise NeedsLogin(f"YouTube chat needs the login: the person runs `python -m ismail.stream login {self.name}`")
        return {'Authorization': f'Bearer {tok}'}

    def _find_chat(self):
        self._spend('broadcasts')
        got = http('GET', f'{API}/liveBroadcasts', self._h(),
                   params={'part': 'snippet', 'broadcastStatus': 'active', 'broadcastType': 'all', 'maxResults': 5})
        for b in got.get('items') or []:
            cid = (b.get('snippet') or {}).get('liveChatId')
            if cid:
                return cid
        return None

    def session(self):
        self._h()
        self.connected()
        first = True
        while not self.stop_ev.is_set():
            if not self.chat_id:
                self.chat_id, self.page, first = self._find_chat(), None, True
                if not self.chat_id:
                    self.stop_ev.wait(60)
                    continue
                self.hub.log.add('status', self.name, platform='youtube', state='reading',
                                 text='the channel is live: reading its chat')
            try:
                self._spend('list')
                got = http('GET', f'{API}/liveChat/messages', self._h(), params={
                    'liveChatId': self.chat_id, 'part': 'id,snippet,authorDetails', 'pageToken': self.page,
                    'maxResults': 2000})
            except HttpError as e:
                if e.status == 403 and 'quota' in str(e).lower():
                    raise RuntimeError('the YouTube API quota for today is used up: chat comes back after midnight '
                                       'Pacific (or ask Google for more quota)')
                if e.status in (403, 404) and ('liveChatEnded' in str(e.body) or 'NotFound' in str(e.body)):
                    self.chat_id = None
                    continue
                if e.status == 401:
                    raise NeedsLogin(f"the YouTube login was refused: run `python -m ismail.stream login {self.name}`")
                raise
            self.page = got.get('nextPageToken')
            if not first:                           # the first page is what was said before we came: not replayed
                for it in got.get('items') or []:
                    self._on(it)
            first = False
            if got.get('offlineAt'):
                self.chat_id = None
                self.hub.log.add('status', self.name, platform='youtube', state='reading',
                                 text='the broadcast ended: waiting for the next one')
                continue
            self.stop_ev.wait(max(self.poll_s, float(got.get('pollingIntervalMillis') or 0) / 1000))

    def _on(self, it):
        s, a = it.get('snippet') or {}, it.get('authorDetails') or {}
        u = {'id': a.get('channelId', ''), 'name': (a.get('displayName') or '').lower(), 'display': a.get('displayName')}
        for flag, key in (('owner', 'isChatOwner'), ('mod', 'isChatModerator'), ('sub', 'isChatSponsor')):
            if a.get(key):
                u[flag] = True
        t = s.get('type')
        if t == 'textMessageEvent':
            self.chat(u, (s.get('textMessageDetails') or {}).get('messageText') or s.get('displayMessage', ''),
                      id=it.get('id'))
        elif t in ('superChatEvent', 'superStickerEvent'):
            d = s.get('superChatDetails') or s.get('superStickerDetails') or {}
            self.event('superchat', user=u, text=d.get('userComment') or s.get('displayMessage'), id=it.get('id'),
                       amount=d.get('amountDisplayString'))
        elif t in ('newSponsorEvent', 'memberMilestoneChatEvent'):
            self.event('member', user=u, text=s.get('displayMessage'), id=it.get('id'))
        elif t in ('membershipGiftingEvent', 'giftMembershipReceivedEvent', 'giftEvent'):
            self.event('gift', user=u, text=s.get('displayMessage'), id=it.get('id'))
        elif t == 'chatEndedEvent':
            self.chat_id = None

    def post(self, text, reply_to=None):
        if not self.chat_id:
            raise RuntimeError('the channel is not live, so there is no chat to write to')
        self._spend('insert')
        got = http('POST', f'{API}/liveChat/messages', self._h(), params={'part': 'snippet'}, json_body={
            'snippet': {'liveChatId': self.chat_id, 'type': 'textMessageEvent',
                        'textMessageDetails': {'messageText': text}}})
        return got.get('id')

    def send(self, text, reply_to=None):
        text = self.check_send(text, per_30s=10)
        mid = self.post(text, reply_to=reply_to)
        self.recent_sends.append((time.time(), text))
        self.sent += 1
        return text, mid
