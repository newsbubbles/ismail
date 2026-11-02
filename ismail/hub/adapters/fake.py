"""The fake place: every cap a place can have, with no network, so the whole loop runs in tests and on this machine
(http://127.0.0.1:<port>/fake shows its outbox; POST /fake/send, /fake/press, /fake/form act as a person).
A place config may narrow its caps (`caps: {buttons: 0, forms: []}`) to test the typed fallbacks."""
import itertools

from .base import HubAdapter


class FakeAdapter(HubAdapter):
    platform = 'fake'
    _ids = itertools.count(1)

    def __init__(self, place, hub):
        super().__init__(place, hub)
        self.caps.update(place.get('caps') or {})
        self.outbox = []                   # what the place showed: {id, to, where, text, files, buttons, form}

    def session(self):
        self.connected()
        self.stop_ev.wait()

    def deliver(self, r, to=None, thread=None, reply_to=None):
        mid = f'fake{next(self._ids)}'
        where = thread or (f'dm:{to}' if to else 'channel')
        self.outbox.append({'id': mid, 'to': to, 'where': where, 'reply_to': reply_to, 'text': r['text'],
                            'files': [f['name'] for f in r['files']], 'buttons': r['buttons'], 'form': r['form']})
        return {'id': mid, 'where': where}

    def edit(self, msg_id, where, r):
        for m in self.outbox:
            if m['id'] == msg_id:
                m.update(text=r['text'], edited=True, **({'files': [f['name'] for f in r['files']]}
                                                         if r['files'] else {}))
                return {'id': msg_id, 'where': where}
        raise ValueError(f'no message {msg_id} here')

    @staticmethod
    def user(name):
        name = str(name or 'someone')[:32]
        return {'id': name.lower(), 'name': name.lower(), 'display': name}

    # a person acting on the fake place (tests, the /fake page)
    def person_says(self, name, text, where=None, dm=True, files=None):
        return self.incoming(self.user(name), text, id=f'in{next(self._ids)}', where=where or f'dm:{name.lower()}',
                             dm=dm, files=files)

    def person_presses(self, name, msg_id, button_id):
        return self.pressed(self.user(name), msg_id, button_id)

    def person_answers(self, name, msg_id, values):
        return self.submitted(self.user(name), msg_id, values)
