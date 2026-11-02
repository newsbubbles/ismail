"""The hub server: places' adapters, the one log, people, open buttons and forms, and a small JSON API on 127.0.0.1.

  python -m ismail.hub.server [--port 8877]

Agents reach it through the hub_* ops (ops.py), which POST {op, args} to /agent."""
import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ..stream.chatlog import ChatLog
from . import adapters, config as C, message as M, people as P, policy

OPEN_KEEP = 500           # buttons and forms kept answerable (the newest)
QUIET_UNTIL = ('ignored', 'refused', 'paused')
PAIR_RE = re.compile(r'^\s*pair\s+([A-Za-z0-9]{6,12})\s*$', re.I)


class HubLog(ChatLog):
    KINDS = ('msg', 'press', 'form', 'said', 'edit', 'event', 'status')


class Hub:
    def __init__(self, port):
        self.port = port
        self.log = HubLog(C.HOME / 'log.jsonl')
        self.adapters, self.configs = {}, {}
        self.watchers = {}
        self.lock = threading.RLock()
        self.started = time.time()
        self.open = C._read(C.HOME / 'open.json', {})           # "place:msg id" -> who may answer, and how
        self.presence = C._read(C.HOME / 'presence.json', {})   # person -> {place: last seen}
        self.claims = C._read(C.HOME / 'claims.json', {})       # str(n) -> {by, at, why}
        self.told = {}                                          # (place, user, what) -> when a fixed reply went
        self.reload()

    # ------------------------------------------------------------ places
    def reload(self):
        places = C.load_places()
        with self.lock:
            for name in list(self.adapters):
                if places.get(name) != self.configs.get(name):
                    self.adapters.pop(name).stop()
                    self.configs.pop(name, None)
            for name, cfg in places.items():
                if name in self.adapters:
                    continue
                try:
                    a = adapters.make({**cfg, 'name': name}, self)
                except ImportError as e:
                    self.log.add('status', name, platform=cfg['platform'], state='cannot start',
                                 text=f"{e}: pip install websockets (or ismail[stream])")
                    continue
                self.adapters[name], self.configs[name] = a, dict(cfg)
                a.start()
        return places

    def shutdown(self):
        for a in list(self.adapters.values()):
            a.stop()

    def _save(self):
        if len(self.open) > OPEN_KEEP:
            for k in sorted(self.open, key=lambda k: self.open[k]['at'])[:len(self.open) - OPEN_KEEP]:
                del self.open[k]
        C._write(C.HOME / 'open.json', self.open)
        C._write(C.HOME / 'presence.json', self.presence)

    # ------------------------------------------------------------ what people send
    def inbound(self, a, kind, user, **f):
        cfg = self.configs.get(a.name, {})
        st = policy.stamp(a.name, cfg, a.platform, user)
        where = f.get('where')
        if kind == 'msg':
            m = PAIR_RE.match(f.get('text') or '')
            if m:
                person = P.redeem(m.group(1), a.platform, user.get('id'))
                self._fixed(a, user, where, f"Linked: you are {person} here now." if person else
                            "That code is not valid, or it ran out. Ask for a new one.", once=False)
                return self.log.add('event', a.name, platform=a.platform, type='paired' if person else 'pair failed',
                                    user=user, person=person or st['person'], where=where)
            if st['use'] in ('refused', 'paused'):
                self._fixed(a, user, where, policy.REFUSED_REPLY if st['use'] == 'refused' else policy.PAUSED_REPLY)
            else:
                typed = self._typed_for(a.name, where)
                got = M.parse_typed(f.get('text'), typed[1]['typed']) if typed else None
                if got:
                    kind, f = got[0], {'msg_id': typed[0], 'where': where,
                                       **({'button': got[1]['id']} if got[0] == 'press' else {'values': got[1]}),
                                       'typed': True}
        if kind in ('press', 'form'):
            o = self.open.get(f"{a.name}:{f.get('msg_id')}")
            if o is None:
                return self.log.add('event', a.name, platform=a.platform, type=f'stale {kind}', user=user,
                                    person=st['person'], msg_id=f.get('msg_id'))
            if not self._may_answer(o, user, st):
                return self.log.add(kind, a.name, platform=a.platform, user=user, **{**st, 'use': 'refused'},
                                    msg_id=f.get('msg_id'), why=f"only {o['press']} may answer this",
                                    **{'for': o['by']})
            f['for'] = o['by']
            if st['use'] == 'ignored':         # an answer to our own buttons or form is heard, as a suggestion
                st = {**st, 'use': 'suggestion'}
            if kind == 'press':
                f['label'] = next((b['label'] for b in o['buttons'] if b['id'] == f.get('button')), None)
        if st['use'] not in QUIET_UNTIL:
            self.presence.setdefault(st['person'], {})[a.name] = round(time.time(), 1)
            self._save()
        return self.log.add(kind, a.name, platform=a.platform, user=user, **st, **f)

    def _fixed(self, a, user, where, text, once=True):
        """A fixed reply (refused, paused, paired), at most once in 10 minutes per person and reply."""
        key = (a.name, user.get('id'), text)
        if once and time.time() - self.told.get(key, 0) < 600:
            return
        self.told[key] = time.time()
        try:
            r = M.render(M.check(text), a.caps, a.name)
            a.deliver(r, to=user.get('id') if str(where or '').startswith('dm') or not where else None,
                      thread=None if str(where or '').startswith('dm') else where)
        except Exception as e:
            self.log.add('status', a.name, platform=a.platform, state='fixed reply failed', text=str(e))

    def _typed_for(self, place, where):
        """The newest open message in this place and conversation with typed fallbacks: (msg id, record)."""
        best = None
        for k, o in self.open.items():
            if o['place'] == place and o['where'] == where and o.get('typed'):
                if best is None or o['at'] > best[1]['at']:
                    best = (k.split(':', 1)[1], o)
        return best

    @staticmethod
    def _may_answer(o, user, st):
        if o['press'] == 'anyone':
            return True
        if o['press'] == 'owner':
            return st['trust'] == 'owner'
        if not o.get('to_user') and not o.get('to_person'):
            return True                        # sent to a channel, not a person: anyone in that conversation
        return str(user.get('id')) == str(o.get('to_user')) or (o.get('to_person') and o['to_person'] == st['person'])

    # ------------------------------------------------------------ ops
    def run(self, op, a):
        fn = getattr(self, 'op_' + op, None)
        if fn is None:
            raise ValueError(f'no such hub op: {op}')
        return fn(**a)

    def op_health(self):
        return {'ok': True, 'pid': os.getpid(), 'port': self.port}

    def op_reload(self):
        self.reload()
        return self.op_status()

    def op_status(self):
        now = time.time()
        rows = []
        for name, cfg in sorted(C.load_places().items()):
            a = self.adapters.get(name)
            rows.append({'place': name, 'platform': cfg['platform'], 'public': cfg.get('public', False),
                         'guest_text': cfg.get('guest_text', 'ignore'), 'only': cfg.get('only'),
                         'state': a.status() if a else 'off', 'caps': a.caps if a else None,
                         'keys': C.key_summary(name) if cfg['platform'] != 'fake' else 'none needed'})
        return {'up_s': int(now - self.started), 'port': self.port, 'lines': self.log.n, 'paused': C.paused(),
                'watching': sorted(w for w, t in self.watchers.items() if now - t < 120), 'places': rows}

    def op_listen(self, who, since=None, wait=25.0, places=None, people=None, kinds=None, quiet=False, every=False):
        self.watchers[who] = time.time()
        if since is None:
            since = self.log.n
        places, people = set(places or []) or None, set(people or []) or None
        kinds = set(kinds) if kinds else {'msg', 'press', 'form', 'event'}

        def pick(d):
            if d['kind'] not in kinds or (places and d['venue'] not in places):
                return False
            if people and d.get('person') not in people:
                return False
            if not quiet and d.get('use') in QUIET_UNTIL:
                return False
            return every or d['kind'] not in ('press', 'form') or d.get('for') in (None, who)
        lines = self.log.wait(int(since), float(wait or 0), pick)
        self.watchers[who] = time.time()
        lines = [{**d, 'claimed_by': self.claims[str(d['n'])]['by']} if str(d['n']) in self.claims else d
                 for d in lines]
        return {'since': lines[-1]['n'] if lines else max(int(since), self.log.n), 'lines': lines}

    def op_read(self, since=0, place=None, person=None, kinds=None, limit=50):
        kinds = set(kinds) if kinds else {'msg', 'press', 'form', 'said', 'event'}

        def pick(d):
            return d['kind'] in kinds and (not place or d['venue'] == place) and (not person or
                                                                                    d.get('person') == person)
        lines = self.log.after(int(since or 0), pick)
        return {'lines': lines[-int(limit):], 'more_before': max(0, len(lines) - int(limit)), 'last': self.log.n}

    def _route(self, to, reply_to):
        """(adapter, user id or None, where or None, person or None) for a destination."""
        if reply_to is not None:
            d = next((x for x in self.log.lines if x['n'] == int(reply_to)), None)
            if d is None or d['kind'] not in ('msg', 'press', 'form'):
                raise ValueError(f'no message n={reply_to} in the log to answer (hub_read shows them)')
            a = self.adapters.get(d['venue'])
            if a is None:
                raise ValueError(f"{d['venue']} is not connected now (hub_status)")
            dm = d.get('dm') or str(d.get('where') or '').startswith('dm')
            return a, (d['user'] or {}).get('id') if dm else None, None if dm else d.get('where'), d.get('person')
        to = str(to or '')
        if ':' in to and to.split(':', 1)[0] in self.adapters:
            place, where = to.split(':', 1)
            return self.adapters[place], None, where, None
        if to in self.adapters:
            where = self.configs[to].get('channel')
            if not where:
                raise ValueError(f"{to} has no default channel: hub_place({to!r}, channel='<id>'), or to='{to}:<id>'")
            return self.adapters[to], None, where, None
        accs = P.accounts_of(to)
        if not accs:
            raise ValueError(f"no person or place named {to!r}: a person linked with hub_pair, a place from "
                             f"hub_status, "
                             f"or place:<channel or thread id>")
        seen = self.presence.get(to, {})
        options = [(seen.get(name, 0), name, acc.split(':', 1)[1]) for acc in accs for name, a in self.adapters.items()
                   if acc.split(':', 1)[0] == a.platform and a.state == 'reading']
        if not options:
            raise ValueError(f"{to} has no connected place now ({', '.join(accs)}); hub_status shows each place, "
                             f"and the phone (phone_say) reaches them as before")
        _, name, uid = max(options)
        return self.adapters[name], uid, None, to

    def op_say(self, to=None, text='', files=None, buttons=None, form=None, press='to', thread=None, reply_to=None,
               by=None, approved=None):
        a, uid, where, person = self._route(to, reply_to)
        if thread:
            where, uid = thread, None
        owner = person is not None and P.load()['people'].get(person, {}).get('role') == 'owner'
        cfg = self.configs.get(a.name, {})
        if not owner:
            if C.paused():
                raise ValueError(f"the hub is paused ({C.paused().get('why')}): only Nate is answered until "
                                 f"hub_pause(off)")
            if cfg.get('public') and not approved:
                raise ValueError(f"{a.name} is public: a message from an agent there is publishing. Ask Nate first "
                                 f"(phone_ask with the draft), then pass approved='<his words>'")
        out = M.check(text, files, buttons, form, press)
        r = M.render(out, a.caps, a.name)
        mid = None
        if reply_to is not None:
            d = next((x for x in self.log.lines if x['n'] == int(reply_to)), {})
            mid = d.get('id') if d.get('kind') == 'msg' else None
        got = a.deliver(r, to=uid, thread=where, reply_to=mid)
        key = f"{a.name}:{got['id']}"
        if r['buttons'] or r['form'] or r['typed']:
            self.open[key] = {'place': a.name, 'where': got.get('where'), 'by': by, 'press': press, 'at': time.time(),
                              'to_user': uid, 'to_person': person, 'buttons': out['buttons'], 'form': out['form'],
                              'typed': r['typed']}
            self._save()
        d = self.log.add('said', a.name, platform=a.platform, by=by, to=person or uid or where, id=got['id'],
                         where=got.get('where'), text=r['text'], files=[f['name'] for f in r['files']] or None,
                         buttons=out['buttons'] or None, form=out['form'], shown=r['shown'], approved=approved)
        return {'place': a.name, 'id': got['id'], 'n': d['n'], 'shown': r['shown'] or ['text']}

    def op_edit(self, n, text=None, files=None, by=None):
        d = next((x for x in self.log.lines if x['n'] == int(n) and x['kind'] == 'said'), None)
        if d is None:
            raise ValueError(f'n={n} is not a message the hub sent (hub_read kinds=["said"])')
        a = self.adapters.get(d['venue'])
        if a is None or not a.caps.get('edit'):
            raise ValueError(f"{d['venue']} cannot edit messages now: send a new one")
        r = M.render(M.check(text if text is not None else d.get('text', ''), files), a.caps, a.name)
        a.edit(d['id'], d.get('where'), r)
        self.log.add('edit', a.name, platform=a.platform, by=by, of=int(n), id=d['id'], text=r['text'])
        return {'place': a.name, 'id': d['id'], 'edited': int(n)}

    def op_claim(self, n, by, why=None):
        c = self.claims.get(str(n))
        if c and c['by'] != by:
            raise ValueError(f"n={n} is {c['by']}'s (claimed {int(time.time() - c['at'])} s ago); leave it to them")
        self.claims[str(n)] = {'by': by, 'at': time.time(), 'why': why}
        C._write(C.HOME / 'claims.json', self.claims)
        return {'n': int(n), 'by': by}

    def op_presence(self, person=None):
        now = time.time()
        rows = {p: {pl: int(now - t) for pl, t in sorted(v.items(), key=lambda x: -x[1])}
                for p, v in self.presence.items() if not person or p == person}
        return {'seconds_ago': rows}

    def op_pair(self, person, minutes=P.CODE_MIN):
        return {'person': person, 'code': P.new_code(person, minutes), 'minutes': minutes}

    # the fake place, acting as a person (tests and the /fake page)
    def op_fake(self, place, act, person='someone', text='', msg_id=None, button=None, values=None, where=None):
        a = self.adapters.get(place)
        if a is None or a.platform != 'fake':
            raise ValueError(f'{place} is not a fake place')
        if act == 'say':
            return a.person_says(person, text, where=where)
        if act == 'press':
            return a.person_presses(person, msg_id, button)
        if act == 'form':
            return a.person_answers(person, msg_id, values or {})
        if act == 'outbox':
            return a.outbox[-50:]
        raise ValueError('act: say, press, form or outbox')


class Handler(BaseHTTPRequestHandler):
    hub = None

    def log_message(self, *a):
        pass

    def _json(self, code, d):
        b = json.dumps(d, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == '/health':
            return self._json(200, self.hub.op_health())
        return self._json(404, {'error': 'no such page'})

    def do_POST(self):
        if self.headers.get('Origin') or self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            return self._json(403, {'ok': False, 'error': 'agents only: no web pages'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            req = json.loads(self.rfile.read(n) or b'{}')
            if self.path == '/agent':
                return self._json(200, {'ok': True, 'result': self.hub.run(req['op'], dict(req.get('args') or {}))})
        except (ValueError, TypeError, KeyError) as e:
            return self._json(400, {'ok': False, 'error': str(e)})
        except Exception as e:
            return self._json(500, {'ok': False, 'error': f'{type(e).__name__}: {e}'})
        return self._json(404, {'error': 'no such page'})


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m ismail.hub.server')
    ap.add_argument('--port', type=int, default=C.PORT)
    a = ap.parse_args(argv)
    C.HOME.mkdir(parents=True, exist_ok=True)
    hub = Hub(a.port)
    Handler.hub = hub
    srv = ThreadingHTTPServer(('127.0.0.1', a.port), Handler)
    srv.daemon_threads = True
    (C.HOME / 'server.json').write_text(json.dumps({'port': a.port, 'pid': os.getpid(), 'started': time.time()}),
                                        encoding='utf8')
    print(f'[hub] up on 127.0.0.1:{a.port}, home {C.HOME}', flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        hub.shutdown()
    return 0


if __name__ == '__main__':
    sys.exit(main())
