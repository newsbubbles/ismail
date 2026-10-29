"""What every chat adapter does: read one venue's chat into the hub, send to it, and say how it is doing."""
import collections
import threading
import time

from .. import config as C


class NeedsLogin(RuntimeError):
    """The adapter cannot go on until the person logs in (`python -m ismail.stream login <venue>`)."""


class Adapter:
    platform = ''
    reading = 'realtime'          # or 'every N s' for a polled site

    def __init__(self, venue, hub):
        self.v = venue
        self.name = venue['name']
        self.hub = hub
        self.state = 'starting'
        self.error = None
        self.since = time.time()
        self.got = 0
        self.sent = 0
        self.stop_ev = threading.Event()
        self.thread = None
        self.recent_sends = collections.deque()      # (time, text) for the rate limit and the repeat check
        self.self_ids = set()                        # our own account's user ids: its echoes are marked self

    # ---- the hub calls these
    def start(self):
        self.thread = threading.Thread(target=self._run, name=f'chat:{self.name}', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_ev.set()
        self.close()

    def status(self):
        d = {'venue': self.name, 'platform': self.platform, 'state': self.state, 'reading': self.reading,
             'got': self.got, 'sent': self.sent}
        if self.error:
            d['error'] = self.error
        return d

    # ---- each adapter writes these
    def session(self):
        """Connect and read until stop_ev is set or the connection fails (raise). Call self.chat()/self.event()."""
        raise NotImplementedError

    def close(self):
        """Unblock a session that is waiting on the network (called from another thread)."""

    def threaded_replies(self):
        """True when the site shows a reply under the line it answers; else chat_say puts @name in front."""
        return False

    def post(self, text, reply_to=None):
        """Send one chat line; return the site's message id (or None). Raise with a plain reason on failure."""
        raise RuntimeError(f'{self.platform} chat cannot be written to from here')

    # ---- helpers
    def keys(self):
        return C.load_keys(self.name)

    def chat(self, user, text, id=None, reply_to=None, **extra):
        self.got += 1
        if user and str(user.get('id')) in self.self_ids:
            extra['own'] = True
        self.hub.log.add('chat', self.name, platform=self.platform, channel=self.v.get('channel'), user=user,
                         text=text, id=id, reply_to=reply_to, **extra)

    def event(self, type, user=None, text=None, id=None, **extra):
        self.got += 1
        self.hub.log.add('event', self.name, platform=self.platform, channel=self.v.get('channel'), type=type,
                         user=user, text=text, id=id, **extra)

    def _run(self):
        backoff = 2.0
        while not self.stop_ev.is_set():
            self.state, t0 = 'connecting', time.time()
            try:
                self.session()
                if self.stop_ev.is_set():
                    break
                self.error = 'the connection ended'
            except NeedsLogin as e:
                self.state, self.error = 'needs login', str(e)
                self.hub.log.add('status', self.name, platform=self.platform, state=self.state, text=str(e))
                self.stop_ev.wait(60.0)          # the person may log in meanwhile; the keys file is read again
                continue
            except Exception as e:               # a network error, a site change: say it and come back
                self.error = f'{type(e).__name__}: {e}'
            if self.stop_ev.is_set():
                break
            if time.time() - t0 > 60:
                backoff = 2.0
            self.state = f'reconnecting in {int(backoff)} s'
            self.hub.log.add('status', self.name, platform=self.platform, state='reconnecting', text=self.error)
            self.stop_ev.wait(backoff)
            backoff = min(backoff * 2, 120.0)
        self.state = 'stopped'

    def connected(self):
        if self.state != 'reading':
            self.state, self.error, self.since = 'reading', None, time.time()
            self.hub.log.add('status', self.name, platform=self.platform, state='reading')

    def check_send(self, text, per_30s=15):
        """The limits every site shares: not empty, not too long, not the same line twice in 30 s, not a flood."""
        text = ' '.join(str(text or '').split())
        if not text:
            raise ValueError('nothing to say')
        mx = C.MAX_LEN.get(self.platform, 500)
        if len(text) > mx:
            raise ValueError(f'{len(text)} characters: {self.platform} takes at most {mx}. Say it shorter or in two '
                             f'lines')
        now = time.time()
        while self.recent_sends and now - self.recent_sends[0][0] > 30:
            self.recent_sends.popleft()
        if any(t == text for _, t in self.recent_sends):
            raise ValueError('the same line was sent here in the last 30 s (sites drop repeats): word it differently')
        if len(self.recent_sends) >= per_30s:
            raise ValueError(f'{per_30s} lines in 30 s already: wait {int(30 - (now - self.recent_sends[0][0])) + 1}'
                             f' s, or gather replies into one line')
        return text

    def send(self, text, reply_to=None):
        text = self.check_send(text)
        mid = self.post(text, reply_to=reply_to)
        self.recent_sends.append((time.time(), text))
        self.sent += 1
        return text, mid
