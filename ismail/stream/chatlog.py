"""One numbered log for every venue's chat, events and our own replies (chat.jsonl), with waiters."""
import json
import threading
import time

KINDS = ('chat', 'event', 'said', 'status')
KEEP = 5000          # lines kept in memory; the file keeps all


class ChatLog:
    def __init__(self, path):
        self.path = path
        self.lines = []
        self.n = 0
        self.cond = threading.Condition()
        try:
            with open(path, encoding='utf8') as f:
                for raw in f:
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    self.lines.append(d)
                    self.n = max(self.n, int(d.get('n', 0)))
        except OSError:
            pass
        self.lines = self.lines[-KEEP:]

    def add(self, kind, venue, **fields):
        assert kind in KINDS, kind
        with self.cond:
            self.n += 1
            d = {'n': self.n, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime()), 'at': round(time.time(), 2),
                 'kind': kind, 'venue': venue, **{k: v for k, v in fields.items() if v is not None}}
            self.lines.append(d)
            del self.lines[:-KEEP]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, 'a', encoding='utf8') as f:
                    f.write(json.dumps(d, ensure_ascii=False) + '\n')
            except OSError:
                pass
            self.cond.notify_all()
        return d

    def after(self, since, pick=None):
        with self.cond:
            return [d for d in self.lines if d['n'] > since and (pick is None or pick(d))]

    def wait(self, since, wait, pick=None):
        """Lines after `since` that pass `pick`, waiting up to `wait` s for the first one."""
        end = time.time() + max(0.0, wait)
        with self.cond:
            while True:
                got = [d for d in self.lines if d['n'] > since and (pick is None or pick(d))]
                left = end - time.time()
                if got or left <= 0:
                    return got
                self.cond.wait(min(left, 5.0))
