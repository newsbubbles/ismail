"""The owner's phone page and VR stage, read into the hub log (phase 2 of HUB_PLAN.md): read only.

One thread per source tails a file the place already writes, from a byte offset kept in <hub home>/bridges/:

- phone: the phone page's inbox (<phone.home>/inbox.jsonl, lines {n, ts, kind, text, id, panel, for, ...}).
- stage: each running stage server's scenes/<scene>/live/events.jsonl (the stage registry names the servers).

A bridge never claims, marks, rewrites, moves or deletes anything there, and never polls their servers: listening on
the stage's /live/inbox as a named listener would show "heard by <who>" in the headset and quiet its "nobody is
listening" warning, and phone_listen counts its caller among the phone's watchers. Reading the files changes nothing
either place shows. The hub.bridges setting names the sources (both by default; [] for none).

Sending goes to the phone only (PhonePlace, through the public phone_* ops). VR is read only in this phase."""
import datetime
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .. import settings
from . import config as C

SOURCES = ('phone', 'stage')
POLL_S = 1.0               # how often a file is looked at
DOWN_S = 5.0               # how often a source that cannot be read is tried again

# phone inbox kinds: what the person says or types (a message), what is not theirs or not worth a line
PHONE_WORDS = ('voice_text', 'text')
PHONE_NOT_THEIRS = ('alarm', 'exam_round')                 # watcher alarms and exam bookkeeping: the server's own
PHONE_PRESENCE_ONLY = ('page', 'voice')                    # page telemetry; 'voice' is followed by its voice_text
PHONE_DROP = ('n', 'ts', 'kind', 'sid', 'room', 'now', 'into_s')

# stage event types: the person's words, the headset's comings and goings, and what only says they are there
STAGE_WORDS = ('voice_message', 'perform_clip')
STAGE_EVENTS = ('headset', 'vr_exit', 'scene_switched')
STAGE_PRESENCE_ONLY = ('voice_in',)
STAGE_DROP = ('id', 'ts', 'type', 'page', 'words', 'snap', 'snaps', 'during')


def enabled():
    try:
        got = settings.get('hub.bridges')
    except KeyError:
        got = list(SOURCES)
    return [s for s in got or [] if s in SOURCES]


class Tail:
    """One append-only JSONL file read from a byte offset: whole new lines only. offset None starts at its end; a
    file that shrank (replaced) is read again from its start."""

    def __init__(self, path, offset=None):
        self.path, self.offset, self.reset, self.missing = Path(path), offset, False, False

    def read(self):
        """New dict lines, or None when the file is not there."""
        try:
            size = os.path.getsize(self.path)
        except OSError:
            self.missing = True
            return None
        self.reset = False
        if self.offset is None:                    # a file that was missing at the first look is new: all of it
            self.offset = 0 if self.missing else size
            if not self.missing:
                return []
        if size < self.offset:
            self.offset, self.reset = 0, True
        if size == self.offset:
            return []
        with open(self.path, 'rb') as f:
            f.seek(self.offset)
            data = f.read(size - self.offset)
        end = data.rfind(b'\n') + 1                # a line still being written waits for the next look
        self.offset += end
        out = []
        for raw in data[:end].splitlines():
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if isinstance(d, dict):
                out.append(d)
        return out


class Bridge:
    """A thread that polls one source, says its state in the hub log when it changes, and keeps going when the
    place is down."""
    name = ''

    def __init__(self, hub):
        self.hub = hub
        self.state, self.why, self.got = 'starting', '', 0
        self.stop_ev = threading.Event()
        self.thread = None
        self.saved = C._read(self._cursor_path(), {})

    def _cursor_path(self):
        return C.HOME / 'bridges' / f'{self.name}.json'

    def save(self, d):
        self.saved = d
        C._write(self._cursor_path(), d)

    def start(self):
        self.thread = threading.Thread(target=self._run, name=f'hub-bridge:{self.name}', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()

    def status(self):
        return {'source': self.name, 'state': self.state, 'why': self.why, 'got': self.got, 'read_only': True}

    def _set(self, state, why=''):
        if (state, why) != (self.state, self.why):
            self.state, self.why = state, why
            self.hub.log.add('status', self.name, platform=self.name, state=state, text=why or None, bridge=True)

    def _run(self):
        while not self.stop_ev.is_set():
            try:
                ok = self.poll()
            except Exception as e:                 # a bad line or a file in use: say so, try again
                self._set('cannot read', f'{type(e).__name__}: {e}')
                ok = False
            self.stop_ev.wait(POLL_S if ok else DOWN_S)

    def poll(self):
        raise NotImplementedError


def phone_inbox():
    """The running phone server's inbox file (its folder may have moved with config_set('phone.home'))."""
    from ..phone import ops as phone
    return phone._running()[0] / 'inbox.jsonl'


class PhoneBridge(Bridge):
    name = 'phone'

    def __init__(self, hub, inbox=phone_inbox):
        super().__init__(hub)
        self.inbox = inbox
        self.tail = None
        self.n = int(self.saved.get('n') or 0)

    def poll(self):
        path = Path(self.inbox())
        if self.tail is None or self.tail.path != path:
            same = self.saved.get('path') == str(path)
            self.tail = Tail(path, self.saved.get('offset') if same else None)
            if not same:
                self.n = 0
        rows = self.tail.read()
        if rows is None:
            self._set('waiting', f'no phone inbox yet at {path}')
            return False
        self._set('reading', str(path))
        if self.tail.reset:
            self.n = 0
        for r in rows:
            n = int(r.get('n') or 0)
            if n <= self.n:
                continue
            self.n = n
            self.got += 1
            self.mirror(r)
        if rows or self.saved.get('offset') != self.tail.offset:
            self.save({'path': str(path), 'offset': self.tail.offset, 'n': self.n})
        return True

    def mirror(self, r):
        k = r.get('kind')
        if k in PHONE_NOT_THEIRS or (k == 'control' and r.get('by')):
            return
        if k in PHONE_PRESENCE_ONLY:
            return self.hub.seen_owner('phone')
        f = {x: v for x, v in r.items() if x not in PHONE_DROP}
        f.update(src_n=r.get('n'), src_kind=k, src_ts=r.get('ts'))
        heard = r.get('heard')
        if isinstance(heard, dict) and heard.get('of'):
            f['heard'] = heard.get('of')
        if k in PHONE_WORDS:
            return self.hub.bridged('phone', 'msg', f)
        if k in ('answer', 'exam') and r.get('id'):
            if self.hub.panel_answer(r, f):
                return
        self.hub.bridged('phone', 'event', {**f, 'type': k})


def stage_dirs(registry=None):
    """The scenes folders of the running stage servers, read from the registry without changing it (stage's own
    link.servers() removes dead servers' notes; this leaves them). A test copy under a hidden folder is left out."""
    from ..live.ops import _pid_alive
    d = Path(registry or settings.get('stage.registry'))
    out = []
    for f in sorted(d.glob('*.json')) if d.is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        sc = rec.get('scenes')
        if not sc or not _pid_alive(rec.get('pid', -1)):
            continue
        if any(p.startswith('.') and p not in ('.', '..') for p in Path(sc).parts):
            continue
        out.append(Path(sc))
    return out


def _epoch(ts):
    try:
        return datetime.datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError):
        return None


class StageBridge(Bridge):
    name = 'stage'

    def __init__(self, hub, dirs=stage_dirs):
        super().__init__(hub)
        self.dirs = dirs
        self.tails = {}                            # events file -> Tail
        self.scanned = None                        # when the last look began (None: not yet)

    def poll(self):
        dirs = self.dirs()
        if not dirs:
            self._set('waiting', 'no stage server is running')
            self.scanned = self.scanned or time.time()
            return False
        self._set('reading', ', '.join(str(d) for d in dirs))
        began, changed = time.time(), False
        for d in dirs:
            for ev in sorted(d.glob('*/live/events.jsonl')):
                key = str(ev)
                t = self.tails.get(key)
                since = None
                if t is None:
                    if key in self.saved:                      # where the hub stopped reading it
                        t = Tail(ev, self.saved[key])
                    elif self.scanned is None:                 # the first look: from now on
                        t = Tail(ev)
                    else:                                      # a scene or server that came since: its new lines
                        t, since = Tail(ev, 0), self.scanned - 2
                    self.tails[key] = t
                rows = t.read() or []
                for e in rows:
                    if since is not None and (_epoch(e.get('ts')) or 0) < since:
                        continue
                    self.got += 1
                    self.mirror(ev.parent.parent.name, e)
                if self.saved.get(key) != t.offset:
                    self.saved[key] = t.offset
                    changed = True
        if changed:
            self.save(dict(self.saved))
        self.scanned = began
        return True

    def mirror(self, scene, e):
        t = e.get('type')
        if t in STAGE_PRESENCE_ONLY:
            return self.hub.seen_owner('stage')
        if t == 'perform_clip' and not (e.get('text') or '').strip():
            return
        if t not in STAGE_WORDS + STAGE_EVENTS:
            return
        f = {x: v for x, v in e.items() if x not in STAGE_DROP}
        f.update(scene=scene, id=e.get('id'), src_n=e.get('id'), src_kind=t, src_ts=e.get('ts'))
        if t in STAGE_WORDS:
            return self.hub.bridged('stage', 'msg', f)
        self.hub.bridged('stage', 'event', {**f, 'type': t})


BRIDGES = {'phone': PhoneBridge, 'stage': StageBridge}


# ------------------------------------------------------------------ sending to the phone

class PhonePlace:
    """The phone page as a place hub_say can send to (the owner's only): a caption (phone_say), a panel with
    buttons and a form (phone_panel_show, the same schema), files as download cards (phone_offer)."""
    name = platform = 'phone'
    state = 'reading'
    caps = {'text_max': 4000, 'files_out': True, 'file_max_mb': 500, 'audio_inline': False, 'buttons': 12,
            'forms': ['choice', 'check', 'toggle', 'text'], 'form_max': 12, 'edit': False, 'threads': False,
            'voice_in': True, 'dm': True}

    @staticmethod
    def up():
        """The phone server answers its /health."""
        from ..phone import ops as phone
        import urllib.request
        port = phone._port()
        if not port:
            return False
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2) as r:
                return bool(json.loads(r.read()).get('ok'))
        except (OSError, ValueError):
            return False

    def deliver(self, r, to=None, thread=None, reply_to=None, by=None):
        from ..api import OpError
        from ..phone import ops as phone
        how, mid = [], None
        try:
            if r['buttons'] or r['form']:
                mid = 'h' + secrets.token_hex(3)
                form = r['form'] or {}
                lines = r['text'].split('\n', 1)
                title = form.get('title') or lines[0][:80]
                text = r['text'] if form.get('title') else (lines[1] if len(lines) > 1 else '')
                phone.phone_panel_show(panel_id=mid, title=title, text=text,
                                       buttons=[b['label'] for b in r['buttons']] or None,
                                       inputs=form.get('inputs'), sender=by)
                how.append(f'a panel ({mid}) on the corner key')
            elif r['text']:
                phone.phone_say(r['text'], sender=by)
                how.append('a caption')
            for f in r['files']:
                phone.phone_offer(f['path'], label=f['name'], sender=by)
            if r['files']:
                how.append(f"{len(r['files'])} download card(s)")
        except OpError as e:
            raise ValueError(f'the phone refused it: {e}') from None
        return {'id': mid or 'p' + secrets.token_hex(3), 'where': 'phone', 'how': how}
