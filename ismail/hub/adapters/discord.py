"""Discord: a bot over the gateway (websocket) and the REST API, with no Discord library.

Reads direct messages, and channel messages that mention the bot (Discord sends a server's other messages only with
the privileged message content intent, which this adapter does not ask for). Sends text, mp3s (they play in the
chat), up to 20 buttons and a form: a form is an "Answer" button that opens a pop-up form (a modal), because Discord
opens a modal only in answer to a press. Protocol facts: songs/_migration/HUB_ADAPTERS_RESEARCH.md section 2.1.

The token is the person's: `python -m ismail.hub token <place>` writes it; no op shows it."""
import json
import secrets
import threading
import time
import urllib.error
import urllib.request

from ...stream.net import HttpError, ws_connect
from .base import HubAdapter, NeedsLogin

API = 'https://discord.com/api/v10'
UA = 'DiscordBot (https://github.com/newsbubbles/ismail, 1)'
INTENTS = (1 << 0) | (1 << 9) | (1 << 12)          # GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES (no message content)
FORM_BUTTON = 'hub:form'
NO_PINGS = {'parse': []}                             # nothing we send pings @everyone, a role or a person


# ------------------------------------------------------------------ what we send, as Discord components

def components_for(r):
    """Action rows (5 buttons each) for a rendered message's buttons, plus the form's Answer button."""
    btns = [{'type': 2, 'style': 2, 'label': b['label'][:80], 'custom_id': 'hub:b:' + b['id'][:80]}
            for b in r['buttons']]
    if r.get('form'):
        btns.append({'type': 2, 'style': 1, 'label': (r['form']['title'] or 'Answer')[:80], 'custom_id': FORM_BUTTON})
    return [{'type': 1, 'components': btns[i:i + 5]} for i in range(0, len(btns), 5)]


def _select(x, many):
    opts = x['options'] if x['kind'] != 'toggle' else ['yes', 'no']
    return {'type': 3, 'custom_id': x['id'], 'required': not x.get('optional') and x['kind'] != 'toggle',
            'min_values': 0 if (x.get('optional') or x['kind'] == 'toggle') else 1,
            'max_values': min(len(opts), 25) if many else 1,
            'options': [{'label': o[:100], 'value': o[:100]} for o in opts[:25]]}


def modal_for(form, msg_id):
    """The pop-up form: one Label per input (text input, or a string select for choice, check and toggle)."""
    comps = []
    for x in form['inputs']:
        if x['kind'] == 'text':
            inner = {'type': 4, 'custom_id': x['id'], 'style': 2, 'required': not x.get('optional'),
                     'max_length': 4000}
        else:
            inner = _select(x, many=x['kind'] == 'check')
        comps.append({'type': 18, 'label': (x.get('label') or x['id'])[:45], 'component': inner})
    return {'custom_id': f'hub:f:{msg_id}', 'title': (form.get('title') or 'Your answer')[:45], 'components': comps}


def values_from_modal(data, form):
    """A modal submit's answers, typed by the form's input kinds."""
    got = {}

    def walk(c):
        if isinstance(c, dict):
            if 'custom_id' in c and ('value' in c or 'values' in c):
                got[c['custom_id']] = c.get('values', c.get('value'))
            for v in c.values():
                walk(v)
        elif isinstance(c, list):
            for v in c:
                walk(v)
    walk(data.get('components', []))
    out = {}
    for x in form['inputs']:
        if x['id'] not in got:
            continue
        v = got[x['id']]
        if x['kind'] == 'check':
            out[x['id']] = list(v or [])
        elif x['kind'] == 'toggle':
            out[x['id']] = bool(v) and (v[0] if isinstance(v, list) else v) == 'yes'
        elif x['kind'] == 'choice':
            out[x['id']] = (v[0] if v else None) if isinstance(v, list) else v
        else:
            out[x['id']] = v
    return out


def user_from(u):
    return {'id': str(u['id']), 'name': u.get('username'), 'display': u.get('global_name') or u.get('username')}


def files_from(msg):
    return [{'name': a.get('filename'), 'url': a.get('url'), 'type': a.get('content_type'), 'size': a.get('size'),
             **({'seconds': a['duration_secs']} if a.get('duration_secs') else {})}
            for a in msg.get('attachments') or []] or None


def _channel(where):
    return str(where).split(':')[-1]


# ------------------------------------------------------------------ the adapter

class DiscordAdapter(HubAdapter):
    platform = 'discord'
    reading = 'realtime (gateway)'

    def __init__(self, place, hub):
        super().__init__(place, hub)
        self.ws = None
        self.me = None
        self.dms = {}                      # user id -> DM channel id
        self.send_lock = threading.Lock()

    def close(self):
        try:
            self.ws and self.ws.close()
        except Exception:
            pass

    # ---- REST
    def _token(self):
        tok = self.keys().get('token')
        if not tok:
            raise NeedsLogin(f"no bot token: the person runs `python -m ismail.hub token {self.name}`")
        return tok

    def api(self, method, path, body=None, files=None):
        h = {'Authorization': f'Bot {self._token()}', 'User-Agent': UA}
        if files:
            boundary = 'hub' + secrets.token_hex(12)
            parts = [(f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\n'
                      f'Content-Type: application/json\r\n\r\n').encode() + json.dumps(body).encode() + b'\r\n']
            for i, f in enumerate(files):
                with open(f['path'], 'rb') as fh:
                    data = fh.read()
                parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="files[{i}]"; '
                              f'filename="{f["name"]}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                             + data + b'\r\n')
            data = b''.join(parts) + f'--{boundary}--\r\n'.encode()
            h['Content-Type'] = f'multipart/form-data; boundary={boundary}'
        else:
            data = json.dumps(body).encode() if body is not None else None
            if data is not None:
                h['Content-Type'] = 'application/json'
        for attempt in range(3):
            req = urllib.request.Request(API + path, data=data, headers=h, method=method)
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    return json.loads(raw) if raw.strip() else {}
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    b = json.loads(raw)
                except ValueError:
                    b = raw.decode('utf8', 'replace')
                if e.code == 429 and attempt < 2 and isinstance(b, dict):
                    time.sleep(min(float(b.get('retry_after', 1)), 10))
                    continue
                if e.code == 401:
                    raise NeedsLogin(f"Discord refused the token: the person runs `python -m ismail.hub token "
                                     f"{self.name}` with the bot's current token")
                raise HttpError(e.code, b, API + path) from None

    def _dm(self, uid):
        if uid not in self.dms:
            self.dms[uid] = self.api('POST', '/users/@me/channels', {'recipient_id': str(uid)})['id']
        return self.dms[uid]

    def deliver(self, r, to=None, thread=None, reply_to=None):
        cid = self._dm(to) if to else _channel(thread)
        where = f'dm:{cid}' if to or str(thread).startswith('dm:') else cid
        body = {'content': r['text'], 'allowed_mentions': NO_PINGS, 'components': components_for(r)}
        if reply_to:
            body['message_reference'] = {'message_id': str(reply_to), 'fail_if_not_exists': False}
        if r['files']:
            body['attachments'] = [{'id': i, 'filename': f['name']} for i, f in enumerate(r['files'])]
        with self.send_lock:
            m = self.api('POST', f'/channels/{cid}/messages', body, files=r['files'] or None)
        self.sent += 1
        return {'id': m['id'], 'where': where}

    def edit(self, msg_id, where, r):
        body = {'content': r['text'], 'allowed_mentions': NO_PINGS}
        if r['files']:
            body['attachments'] = [{'id': i, 'filename': f['name']} for i, f in enumerate(r['files'])]
        self.api('PATCH', f'/channels/{_channel(where)}/messages/{msg_id}', body, files=r['files'] or None)
        return {'id': msg_id, 'where': where}

    # ---- the gateway
    def session(self):
        self._token()
        url = self.api('GET', '/gateway/bot')['url']
        self.ws = ws_connect(f'{url}/?v=10&encoding=json')
        hello = json.loads(self.ws.recv(timeout=20))
        every = hello['d']['heartbeat_interval'] / 1000.0
        seq, acked, next_beat = None, True, time.time() + every * 0.5
        props = {'os': 'windows', 'browser': 'ismail', 'device': 'ismail'}
        self.ws.send(json.dumps({'op': 2, 'd': {'token': self._token(), 'intents': INTENTS, 'properties': props}}))
        while not self.stop_ev.is_set():
            if time.time() >= next_beat:
                if not acked:
                    raise RuntimeError('Discord stopped answering heartbeats')
                self.ws.send(json.dumps({'op': 1, 'd': seq}))
                acked, next_beat = False, time.time() + every
            try:
                raw = self.ws.recv(timeout=max(0.5, next_beat - time.time()))
            except TimeoutError:
                continue
            except Exception as e:
                code = getattr(getattr(e, 'rcvd', None), 'code', None)
                if code == 4004:
                    raise NeedsLogin(f"Discord refused the token: `python -m ismail.hub token {self.name}` again")
                if code in (4013, 4014):
                    raise NeedsLogin('Discord refused the intents: in the developer page, the bot needs no '
                                     'privileged intents; check the app is a bot')
                raise
            m = json.loads(raw)
            if m.get('s') is not None:
                seq = m['s']
            op = m.get('op')
            if op == 11:
                acked = True
            elif op == 1:
                self.ws.send(json.dumps({'op': 1, 'd': seq}))
            elif op in (7, 9):
                raise RuntimeError('Discord asked for a new connection')
            elif op == 0:
                self.dispatch(m['t'], m['d'])

    def dispatch(self, t, d):
        if t == 'READY':
            self.me = d['user']['id']
            self.self_ids = {str(self.me)}
            self.connected()
        elif t == 'MESSAGE_CREATE':
            a = d.get('author') or {}
            if a.get('bot') or str(a.get('id')) == str(self.me):
                return
            dm = not d.get('guild_id')
            text = d.get('content') or ''
            if not dm:
                if not any(str(u.get('id')) == str(self.me) for u in d.get('mentions') or []):
                    return
                text = text.replace(f'<@{self.me}>', '').replace(f'<@!{self.me}>', '').strip()
            self.incoming(user_from(a), text, id=d['id'], where=(f"dm:{d['channel_id']}" if dm else d['channel_id']),
                          dm=dm, files=files_from(d),
                          reply_to=(d.get('message_reference') or {}).get('message_id'))
        elif t == 'INTERACTION_CREATE':
            self.interaction(d)

    def _answer(self, d, body):
        self.api('POST', f"/interactions/{d['id']}/{d['token']}/callback", body)

    def interaction(self, d):
        u = user_from((d.get('member') or {}).get('user') or d.get('user') or {})
        where = f"dm:{d['channel_id']}" if not d.get('guild_id') else d.get('channel_id')
        data = d.get('data') or {}
        mid = (d.get('message') or {}).get('id')
        if d['type'] == 3:                                      # a button
            cid = data.get('custom_id', '')
            if cid == FORM_BUTTON:
                o = self.hub.open.get(f'{self.name}:{mid}')
                if not o or not o.get('form'):
                    return self._answer(d, {'type': 4, 'data': {'content': 'This form has closed.', 'flags': 64}})
                return self._answer(d, {'type': 9, 'data': modal_for(o['form'], mid)})
            self._answer(d, {'type': 6})
            if cid.startswith('hub:b:'):
                self.pressed(u, mid, cid[6:], where=where)
        elif d['type'] == 5:                                    # a form sent
            mid = data.get('custom_id', '')[len('hub:f:'):]
            self._answer(d, {'type': 6})
            o = self.hub.open.get(f'{self.name}:{mid}')
            if o and o.get('form'):
                self.submitted(u, mid, values_from_modal(data, o['form']), where=where)
        else:
            self._answer(d, {'type': 4, 'data': {'content': 'Not yet: write to me instead.', 'flags': 64}})
