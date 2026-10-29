"""The fake venue: a local chat page and a scripted test viewer, so the whole loop runs with no site at all."""
import itertools
import secrets

from .base import Adapter


class FakeAdapter(Adapter):
    """Viewers type on http://127.0.0.1:<port>/fake (hub.fake_say). A venue's `script` plays as a test viewer:
    [{"after_s": 3, "user": "viewer1", "text": "hi"}, {"after_s": 5, "event": "follow", "user": "viewer2"}];
    `loop: true` plays it again from the top."""
    platform = 'fake'

    def session(self):
        self.connected()
        script = list(self.v.get('script') or [])
        if not script:
            self.stop_ev.wait()
            return
        for step in (itertools.cycle(script) if self.v.get('loop') else script):
            if self.stop_ev.wait(float(step.get('after_s', 3))):
                return
            user = self.user(step.get('user', 'viewer'))
            if step.get('event'):
                self.event(step['event'], user=user, text=step.get('text'), id=self.mid(),
                           **({'amount': step['amount']} if 'amount' in step else {}))
            else:
                self.chat(user, step.get('text', ''), id=self.mid())
        self.stop_ev.wait()

    def viewer(self, user, text):
        self.chat(self.user(user), text, id=self.mid())

    def post(self, text, reply_to=None):
        return self.mid()              # the hub logs it as 'said'; the page shows said lines as the streamer's

    @staticmethod
    def user(name):
        name = str(name or 'viewer')[:25]
        return {'id': 'fake:' + name.lower(), 'name': name.lower(), 'display': name}

    @staticmethod
    def mid():
        return secrets.token_hex(6)
