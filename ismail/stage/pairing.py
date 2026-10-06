"""Pairing and principals: who is talking to the stage (the security floor part B1, research/multiplayer/
security-floor.md). Part A refuses other origins and server-only events; it cannot tell a program on the tailnet or on
this PC from the person's headset. Here each device pairs once with a short code and gets a key, each agent on this PC
reads a token, and the server stamps every event and command with a principal:

    {"person": P, "device": D}   a paired device (X-Stage-Key)
    {"agent": who, "for": P}     an agent with the token (X-Stage-Agent, X-Stage-Who)
    {"server": true}             the server itself
    {"unpaired": true}           anyone else

Keys travel in a header, never a cookie (no cross-site sending) or the URL (no logs); the device file keeps a hash
of each key, beside the registry (~/.ismail/stage_devices.json), never in the scenes folder (it travels). The switch in
~/.ismail/stage.json "auth": "report" (the default: everything is accepted, unpaired requests are stamped and counted)
or "enforce" (unpaired requests are refused). Enforce comes only after the person has paired the headset and the phone
in the headset, so the switch can never lock them out in the middle of a session.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

CODE_S = 120                                   # a pairing code is good for two minutes, once
WRONG_MAX, WRONG_WINDOW_S, BLOCK_S = 5, 60, 60  # five wrong codes in a minute from one address: a minute's pause
WRONG_ALL = 20                                  # over twenty a minute from everyone: every claim pauses a minute
KINDS = ('headset', 'phone', 'desktop')


def _h(key):
    return hashlib.sha256(key.encode()).hexdigest()


class Auth:
    """The server's pairing state: pending codes, paired devices (from the device file), the agent token."""

    def __init__(self, registry):
        self.registry = Path(registry)         # ~/.ismail/stage: the agent token lives beside the server records
        self.home = self.registry.parent       # ~/.ismail: stage.json and the device file
        self.lock = threading.Lock()
        self.codes = {}                        # code -> {name, kind, person, until}
        self.wrong = {}                        # address -> [times of wrong codes]
        self.blocked = {}                      # address -> until
        self.wrong_all, self.paused_until = [], 0.0   # wrong codes from every address; the global pause
        self.counts = {'unpaired': 0, 'paired': 0, 'agent': 0, 'bad_key': 0}
        self._devices = (None, {})             # (file mtime, {hash: record})
        self._conf = (0.0, {})
        self.token = self._ensure_token()

    # ---- files
    @property
    def devices_file(self):
        return self.home / 'stage_devices.json'

    @property
    def token_file(self):
        return self.registry / 'agent_token'

    def _ensure_token(self):
        f = self.token_file
        try:
            t = f.read_text(encoding='utf-8').strip()
            if len(t) >= 32:
                return t
        except OSError:
            pass
        f.parent.mkdir(parents=True, exist_ok=True)
        t = secrets.token_urlsafe(32)
        f.write_text(t, encoding='utf-8')
        try:
            os.chmod(f, 0o600)                 # readable by the user only (Windows: the profile folder's own ACL)
        except OSError:
            pass
        return t

    def conf(self):
        """~/.ismail/stage.json, re-read every 5 s: the auth mode and the person this stage belongs to."""
        t, c = self._conf
        if time.time() - t < 5:
            return c
        try:
            c = json.loads((self.home / 'stage.json').read_text(encoding='utf-8'))
            c = c if isinstance(c, dict) else {}
        except (OSError, ValueError):
            c = {}
        self._conf = (time.time(), c)
        return c

    def mode(self):
        return 'enforce' if self.conf().get('auth') == 'enforce' else 'report'

    def person(self):
        return str(self.conf().get('person') or 'owner')

    def devices(self):
        """{hash: record}, re-read when the file changes (an unpair from another process shows at once)."""
        f = self.devices_file
        try:
            mt = f.stat().st_mtime_ns
        except OSError:
            mt = None
        if mt != self._devices[0]:
            recs = {}
            if mt is not None:
                try:
                    for r in json.loads(f.read_text(encoding='utf-8')).get('devices', []):
                        recs[r['hash']] = r
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    recs = {}
            self._devices = (mt, recs)
        return self._devices[1]

    def _save(self, recs):
        f = self.devices_file
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix('.tmp')
        tmp.write_text(json.dumps({'devices': list(recs.values())}, indent=1), encoding='utf-8')
        tmp.replace(f)
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        self._devices = (f.stat().st_mtime_ns, dict(recs))

    # ---- pairing
    def start(self, name, kind, person=None):
        if kind not in KINDS:
            raise ValueError(f'kind: one of {", ".join(KINDS)}')
        name = str(name or kind)[:40]
        with self.lock:
            now = time.time()
            self.codes = {c: v for c, v in self.codes.items() if v['until'] > now}
            code = f'{secrets.randbelow(10 ** 6):06d}'
            while code in self.codes:
                code = f'{secrets.randbelow(10 ** 6):06d}'
            self.codes[code] = {'name': name, 'kind': kind, 'person': person or self.person(), 'until': now + CODE_S}
        return {'code': code, 'expires_s': CODE_S, 'name': name, 'kind': kind}

    def claim(self, code, addr, name=None, kind=None):
        """The device's page claims a code: (record, key) once; ValueError with the reason otherwise."""
        now = time.time()
        with self.lock:
            if self.paused_until > now:
                raise PermissionError('pairing is paused for a minute (too many wrong codes); start a new code after')
            if self.blocked.get(addr, 0) > now:
                raise PermissionError('too many wrong codes: wait a minute')
            v = self.codes.pop(str(code or ''), None)
            if v is None or v['until'] < now:
                w = [t for t in self.wrong.get(addr, []) if now - t < WRONG_WINDOW_S] + [now]
                self.wrong[addr] = w
                if len(w) >= WRONG_MAX:
                    self.blocked[addr], self.wrong[addr] = now + BLOCK_S, []
                # a guesser that looks like a new address every try still meets this: pairing is rare and done at
                # the PC, so a pause for everyone costs nothing
                self.wrong_all = [t for t in self.wrong_all if now - t < WRONG_WINDOW_S] + [now]
                if len(self.wrong_all) > WRONG_ALL:
                    self.paused_until, self.wrong_all, self.codes = now + BLOCK_S, [], {}
                    self.counts['pauses'] = self.counts.get('pauses', 0) + 1
                    print(f'PAIRING PAUSED for {BLOCK_S} s: over {WRONG_ALL} wrong codes in a minute; pending codes cancelled', flush=True)
                raise ValueError('no such code, or it expired (codes last two minutes and work once)')
            key = secrets.token_urlsafe(32)
            rec = {'hash': _h(key), 'device': (str(name)[:40] if name else v['name']), 'kind': kind if kind in KINDS else v['kind'],
                   'person': v['person'], 'paired': time.strftime('%Y-%m-%dT%H:%M:%S')}
            recs = dict(self.devices())
            n, base = 2, rec['device']
            while any(r['device'] == rec['device'] for r in recs.values()):    # device names are unique per stage
                rec['device'], n = f'{base} {n}', n + 1
            recs[rec['hash']] = rec
            self._save(recs)
        return {k: v for k, v in rec.items() if k != 'hash'}, key

    def unpair(self, device):
        with self.lock:
            recs = dict(self.devices())
            gone = [h for h, r in recs.items() if r['device'] == device]
            for h in gone:
                del recs[h]
            if gone:
                self._save(recs)
        return len(gone)

    def listing(self):
        return [{k: v for k, v in r.items() if k != 'hash'} for r in self.devices().values()]

    def valid(self, key_hash):
        return key_hash in self.devices()

    # ---- who sent a request
    def principal(self, key=None, agent=None, who=None):
        """(principal, key hash or None, problem or None)."""
        if key:
            h = _h(key)
            r = self.devices().get(h)
            if r:
                self.counts['paired'] += 1
                return {'person': r['person'], 'device': r['device']}, h, None
            self.counts['bad_key'] += 1
            return {'unpaired': True}, None, 'unknown device key (unpaired, or revoked)'
        if agent:
            if hmac.compare_digest(str(agent), self.token):
                self.counts['agent'] += 1
                return {'agent': str(who or 'agent')[:60], 'for': self.person()}, None, None
            self.counts['bad_key'] += 1
            return {'unpaired': True}, None, 'wrong agent token'
        self.counts['unpaired'] += 1
        return {'unpaired': True}, None, None

    def health(self):
        paused = max(0, round(self.paused_until - time.time()))
        return {'mode': self.mode(), 'devices': len(self.devices()), 'pending_codes': len(self.codes),
                **({'pairing_paused_s': paused} if paused else {}), **self.counts}


def stamp(obj, principal):
    """The server's principal on an event or command; what the client said it was is kept only as display text."""
    out = {k: v for k, v in obj.items() if k != 'principal'}
    if 'principal' in obj:
        out['said_by'] = obj['principal'] if isinstance(obj['principal'], str) else json.dumps(obj['principal'])[:200]
    out['principal'] = principal
    return out
