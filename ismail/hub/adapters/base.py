"""What every hub adapter does, on top of the stream chat adapter (reconnect with backoff, NeedsLogin, send limits):
say what it can show (caps), deliver one rendered message, edit it, and hand the hub what people send."""
from ...stream.adapters.base import Adapter, NeedsLogin  # noqa: F401
from .. import config as C


class HubAdapter(Adapter):
    def __init__(self, place, hub):
        super().__init__(place, hub)
        self.caps = dict(C.PLATFORMS[self.platform]['caps'])

    # ---- each adapter writes these (plus session/close from Adapter)
    def deliver(self, r, to=None, thread=None, reply_to=None):
        """Show one rendered message (message.render's dict) to a user id (`to`, a direct message) or in a thread or
        channel. Return {'id': message id, 'where': thread or channel id}. Raise with a plain reason on failure."""
        raise NotImplementedError

    def edit(self, msg_id, where, r):
        raise ValueError(f'{self.platform} messages cannot be edited from here')

    # ---- the stream base reads these
    def text_max(self):
        return self.caps.get('text_max', 2000)

    def keys(self):
        return C.load_keys(self.name)

    # ---- what people send: the hub stamps it (policy) and logs it
    def incoming(self, user, text='', id=None, where=None, dm=False, files=None, reply_to=None):
        self.got += 1
        return self.hub.inbound(self, 'msg', user, text=text, id=id, where=where, dm=dm, files=files,
                                reply_to=reply_to)

    def pressed(self, user, msg_id, button_id, where=None):
        self.got += 1
        return self.hub.inbound(self, 'press', user, msg_id=msg_id, button=button_id, where=where)

    def submitted(self, user, msg_id, values, where=None):
        self.got += 1
        return self.hub.inbound(self, 'form', user, msg_id=msg_id, values=values, where=where)
