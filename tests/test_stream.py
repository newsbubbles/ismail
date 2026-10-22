"""The stream server: venues, keys kept out of sight, the chat log, the fake venue, site chat parsing, the output."""
import json
import shutil
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from ismail.api import OpError
from ismail.stream import config as C, ops, output, server
from ismail.stream.adapters.kick import KickAdapter
from ismail.stream.adapters.twitch import TwitchAdapter
from ismail.stream.adapters.youtube import YouTubeAdapter
from ismail.stream.chatlog import ChatLog


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'stream')
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(tmp_path / 'no_engines'))
    return tmp_path / 'stream'


class StubHub:
    def __init__(self, path):
        self.log = ChatLog(path / 'chat.jsonl')
        self.port = 0


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return fn()


# ------------------------------------------------------------------ venues and keys

def test_venue_set_checks_and_says_what_the_person_must_do(home):
    with pytest.raises(OpError, match='platform'):
        ops.venue_set(name='x', platform='myspace')
    with pytest.raises(OpError, match='name'):
        ops.venue_set(name='Bad Name!', platform='fake')
    with pytest.raises(OpError, match='channel'):
        ops.venue_set(name='tw', platform='twitch')
    with pytest.raises(OpError, match='stream key'):
        ops.venue_set(name='ig', platform='rtmp', ingest='rtmp://live.example.com/app/sk_live_0123456789abcdefghij')
    out = ops.venue_set(name='tw', platform='twitch', channel='somechannel')
    assert 'python -m ismail.stream login tw' in out and 'python -m ismail.stream key tw' in out
    assert ops.venue_set(name='practice', platform='fake').endswith('nothing more needed.')
    listed = ops.venue_list()
    assert 'tw: twitch somechannel' in listed and 'practice: fake' in listed
    assert 'removed venue practice' in ops.venue_remove(name='practice')


def test_keys_are_summarised_never_shown(home):
    ops.venue_set(name='tw', platform='twitch', channel='somechannel')
    C.save_keys('tw', {'stream_key': 'live_123456_secretvalue', 'access_token': 'tok_abcdefgh',
                       'expires_at': time.time() + 3600, 'login_name': 'somechannel'})
    k = C.key_summary('tw', 'twitch')
    assert k['stream_key'] is True and k['login'] is True and k['login_as'] == 'somechannel'
    assert 'secretvalue' not in json.dumps(k) and 'tok_abcdefgh' not in ops.venue_list()
    assert C.redact('rtmp://x/app/live_123456_secretvalue failed', C.secrets_of('tw')) == 'rtmp://x/app/<key> failed'


# ------------------------------------------------------------------ the chat log

def test_chat_log_numbers_waits_and_persists(tmp_path):
    log = ChatLog(tmp_path / 'chat.jsonl')
    log.add('chat', 'a', text='one')
    threading.Timer(0.2, lambda: log.add('chat', 'b', text='two')).start()
    got = log.wait(1, 3.0)
    assert [d['text'] for d in got] == ['two'] and got[0]['n'] == 2
    again = ChatLog(tmp_path / 'chat.jsonl')
    assert again.n == 2 and [d['venue'] for d in again.after(0)] == ['a', 'b']


# ------------------------------------------------------------------ the fake venue, end to end through the hub

def test_fake_venue_script_reply_and_limits(home):
    ops.venue_set(name='practice', platform='fake', script=[{'after_s': 0.1, 'user': 'Ana', 'text': 'hello'},
                                                            {'after_s': 0.1, 'user': 'Bo', 'event': 'follow'}])
    hub = server.Hub(port=0)
    try:
        lines = wait_for(lambda: [d for d in hub.log.after(0) if d['kind'] in ('chat', 'event')] if
                         len([d for d in hub.log.after(0) if d['kind'] in ('chat', 'event')]) >= 2 else None)
        chat = next(d for d in lines if d['kind'] == 'chat')
        assert chat['user']['display'] == 'Ana' and chat['venue'] == 'practice' and chat['platform'] == 'fake'
        assert any(d['kind'] == 'event' and d['type'] == 'follow' for d in lines)
        res = hub.op_say('welcome in', reply_to=chat['n'], who='MC')
        assert res[0]['ok']
        said = hub.log.after(0, lambda d: d['kind'] == 'said')[-1]
        assert said['text'] == '@Ana welcome in' and said['by'] == 'MC' and said['reply_to'] == chat['n']
        again = hub.op_say('welcome in', reply_to=chat['n'])            # the same line inside 30 s: refused
        assert not again[0]['ok'] and 'same line' in again[0]['error']
        long = hub.op_say('x' * 600, venue='practice')
        assert not long[0]['ok'] and 'at most 500' in long[0]['error']
        with pytest.raises(ValueError, match='reply_to'):
            hub.op_say('hi', reply_to=99999)
        hub.op_viewer('typed on the page', user='Cy')
        lis = hub.op_listen('MC', since=said['n'], wait=1)
        assert [d['text'] for d in lis['lines'] if d['kind'] == 'chat'] == ['typed on the page']
    finally:
        hub.shutdown()


# ------------------------------------------------------------------ site chat parsing

def test_twitch_irc_and_eventsub_lines(tmp_path):
    hub = StubHub(tmp_path)
    a = TwitchAdapter({'name': 'tw', 'platform': 'twitch', 'channel': 'somechannel'}, hub)
    a._irc_line('@badges=moderator/1,subscriber/12;display-name=Ana_B;id=abc-1;user-id=42 '
                ':ana_b!ana_b@ana_b.tmi.twitch.tv PRIVMSG #somechannel :play something faster')
    a._irc_line('@display-name=Bo;login=bo;msg-id=raid;system-msg=5\\sraiders\\sfrom\\sBo;user-id=7;id=r1 '
                ':tmi.twitch.tv USERNOTICE #somechannel')
    a.self_ids = {'99'}
    a._on('channel.chat.message', {'chatter_user_id': '99', 'chatter_user_login': 'me', 'chatter_user_name': 'Me',
                                   'message_id': 'm2', 'message': {'text': 'my own echo', 'fragments': [
                                       {'type': 'gif', 'text': ''}]}, 'badges': [{'set_id': 'broadcaster'}]})
    a._on('channel.cheer', {'user_id': '8', 'user_login': 'cy', 'user_name': 'Cy', 'bits': 100, 'message': 'go'})
    a._on('channel.chat.notification', {'notice_type': 'unknown', 'chatter_user_id': '1'})     # tolerated, skipped
    a._irc_line(':bo!bo@bo.tmi.twitch.tv PRIVMSG #somechannel :ACTION waves')
    assert hub.log.lines.pop()['text'] == 'waves'
    lines = hub.log.after(0)
    assert lines[0]['user'] == {'id': '42', 'name': 'ana_b', 'display': 'Ana_B', 'mod': True, 'sub': True}
    assert lines[0]['text'] == 'play something faster' and lines[0]['id'] == 'abc-1'
    assert lines[1]['type'] == 'raid' and lines[1]['text'] == '5 raiders from Bo'
    assert lines[2]['own'] is True and lines[2]['user']['owner'] is True
    assert lines[3]['type'] == 'cheer' and lines[3]['amount'] == 100
    assert len(lines) == 4


def test_youtube_items(tmp_path):
    hub = StubHub(tmp_path)
    a = YouTubeAdapter({'name': 'yt', 'platform': 'youtube', 'channel': '@someone', 'reader': 'poll'}, hub)
    a._on({'id': 'y1', 'snippet': {'type': 'textMessageEvent', 'textMessageDetails': {'messageText': 'hi there'}},
           'authorDetails': {'channelId': 'UC1', 'displayName': 'Dee', 'isChatModerator': True}})
    a._on({'id': 'y2', 'snippet': {'type': 'superChatEvent', 'superChatDetails': {'amountDisplayString': '$5.00',
                                                                                  'userComment': 'more bass'}},
           'authorDetails': {'channelId': 'UC2', 'displayName': 'Eli'}})
    a._on({'id': 'y3', 'snippet': {'type': 'pollEvent'}, 'authorDetails': {}})                  # unknown: skipped
    lines = hub.log.after(0)
    assert lines[0]['text'] == 'hi there' and lines[0]['user']['mod'] is True
    assert lines[1]['type'] == 'superchat' and lines[1]['amount'] == '$5.00' and lines[1]['text'] == 'more bass'
    assert len(lines) == 2 and a.reading == 'every 10 s'


class _StreamList:
    """A local stand-in for YouTube's StreamList: plays `pages` (lists of response kwargs), or aborts with `abort`."""
    def __init__(self, pages=None, abort=None):
        import grpc
        from concurrent import futures
        from ismail.stream.adapters import youtube_stream as YS
        self.seen = []

        def stream_list(req, ctx):
            self.seen.append((req.live_chat_id, req.page_token, dict(ctx.invocation_metadata()).get('authorization')))
            if abort:
                ctx.abort(abort, 'not for this project')
            for kw in pages or []:
                yield YS.Response(**kw)
                time.sleep(0.05)
            time.sleep(30)                                   # held open, as YouTube does, until cancelled

        svc, meth = YS.METHOD.strip('/').split('/')
        h = grpc.method_handlers_generic_handler(svc, {meth: grpc.unary_stream_rpc_method_handler(
            stream_list, request_deserializer=YS.Request.FromString,
            response_serializer=YS.Response.SerializeToString)})
        self.srv = grpc.server(futures.ThreadPoolExecutor(4))
        self.srv.add_generic_rpc_handlers((h,))
        self.target = f'127.0.0.1:{self.srv.add_insecure_port("127.0.0.1:0")}'
        self.srv.start()


def _yt_msg(mid, kind, name, **snip):
    from ismail.stream.adapters import youtube_stream as YS
    return YS.C['LiveChatMessage'](id=mid, snippet=YS.C['LiveChatMessageSnippet'](type=kind, **snip),
                                   author_details=YS.C['LiveChatMessageAuthorDetails'](channel_id='UC' + mid,
                                                                                       display_name=name))


def test_youtube_streamed_reader(home, tmp_path):
    pytest.importorskip('grpc')
    from ismail.stream.adapters import youtube_stream as YS
    from ismail.stream.adapters.youtube import _Refused
    said = YS.C['LiveChatTextMessageDetails']
    sl = _StreamList(pages=[
        {'next_page_token': 'p1', 'items': [_yt_msg('old', 1, 'Old', text_message_details=said(message_text='before'))]},
        {'next_page_token': 'p2', 'items': [
            _yt_msg('m1', 1, 'Dee', text_message_details=said(message_text='hi there')),
            _yt_msg('m2', 15, 'Eli', super_chat_details=YS.C['LiveChatSuperChatDetails'](
                amount_display_string='$5.00', user_comment='more bass'))]},
        {'next_page_token': 'p3', 'offline_at': '2026-01-01T00:00:00Z'}])
    hub = StubHub(tmp_path)
    a = YouTubeAdapter({'name': 'yt', 'platform': 'youtube', 'channel': '@someone', '_grpc_target': sl.target}, hub)
    a._h = lambda: {'Authorization': 'Bearer test-token'}
    assert a.reader == 'stream' and a.reading == 'streamed (realtime)'
    a.chat_id = 'chat1'
    try:
        th = threading.Thread(target=a._stream, daemon=True)
        th.start()
        th.join(10)
        assert not th.is_alive() and a.chat_id is None and a.page == 'p3'       # the broadcast ended: it let go
        chat = [d for d in hub.log.after(0) if d['kind'] != 'status']
        assert [d.get('text') for d in chat] == ['hi there', 'more bass']           # the first page is history
        assert chat[1]['type'] == 'superchat' and chat[1]['amount'] == '$5.00'
        assert sl.seen == [('chat1', '', 'Bearer test-token')]
    finally:
        sl.srv.stop(0)
    refused = _StreamList(abort=__import__('grpc').StatusCode.PERMISSION_DENIED)
    b = YouTubeAdapter({'name': 'yt2', 'platform': 'youtube', '_grpc_target': refused.target}, hub)
    b._h, b.chat_id = (lambda: {'Authorization': 'Bearer t'}), 'chat2'
    try:
        with pytest.raises(_Refused, match='PERMISSION_DENIED'):
            b._stream()
    finally:
        refused.srv.stop(0)


def test_kick_feed_events(tmp_path):
    hub = StubHub(tmp_path)
    a = KickAdapter({'name': 'k', 'platform': 'kick', 'channel': 'somechannel'}, hub)
    a._on('ChatMessageEvent', {'id': 'u-1', 'content': 'nice [emote:37226:KEKW] set',
                               'sender': {'id': 5, 'username': 'Fay', 'slug': 'fay',
                                          'identity': {'badges': [{'type': 'subscriber'}, {'type': 'moderator'}]}},
                               'metadata': {'original_message': {'id': 'u-0'}}})
    a._on('GiftedSubscriptionsEvent', {'gifter_username': 'Gus', 'gifted_usernames': ['a', 'b', 'c']})
    a._on('PinnedMessageCreatedEvent', {})                                                 # unknown: skipped
    lines = hub.log.after(0)
    assert lines[0]['text'] == 'nice :KEKW: set' and lines[0]['reply_to'] == 'u-0'
    assert lines[0]['user'] == {'id': '5', 'name': 'fay', 'display': 'Fay', 'mod': True, 'sub': True}
    assert lines[1]['type'] == 'gift' and lines[1]['amount'] == 3 and len(lines) == 2
    assert a.threaded_replies()


# ------------------------------------------------------------------ the output

def test_encoder_args_per_mode(tmp_path):
    for mode in output.MODES:
        vis = {**output.DEFAULT_VISUAL, 'mode': mode}
        a = output.build_encoder_args(vis, tmp_path, tmp_path / 'p.jpg', 'http://127.0.0.1:1/pcm', nvenc=False)
        g = a[a.index('-filter_complex') + 1]
        assert 'drawtext' in g and 'expansion=none' in g and '[vout]' in g and 'libx264' in a and 'mpegts' in a
        assert a[a.index('-g') + 1] == '60'                           # a keyframe every 2 s
    a = output.build_encoder_args({**output.DEFAULT_VISUAL, 'window': 'My Page'}, tmp_path, tmp_path / 'p.jpg',
                                  'http://x/pcm', nvenc=True)
    assert 'gdigrab' in a and 'title=My Page' in a and 'h264_nvenc' in a


def test_going_live_needs_the_persons_words_and_a_key(home):
    ops.venue_set(name='tw', platform='twitch', channel='somechannel')
    out = output.Output(StubHub(home))
    with pytest.raises(ValueError, match="person's yes"):
        out.go(['tw'], approved=None)
    with pytest.raises(ValueError, match='python -m ismail.stream key tw'):
        out.go(['tw'], approved='go live on twitch')
    with pytest.raises(ValueError, match='unknown visual'):
        out.set_visual({'glitter': 1})
    with pytest.raises(ValueError, match='mode'):
        out.set_visual({'mode': 'lasers'})
    assert not out.pushers and out.enc is None


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='needs ffmpeg')
def test_a_file_venue_records_the_stream(home, tmp_path):
    rec = tmp_path / 'rec' / 'take.flv'
    ops.venue_set(name='rec', platform='file', channel=str(rec))
    hub = server.Hub(port=0)
    server.Handler.hub = hub
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    hub.port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hub.op_visual(encoder='x264', mode='waves', width=640, height=360, fps=15, video_kbps=800)
        hub.op_text('title', 'test 100% %{pts}')
        hub.op_go(['rec'])
        assert wait_for(lambda: hub.output.pushers['rec'].sent > 20000, timeout=20)
        st = hub.op_look()
        assert st['state'] == 'live' and 'silence' in st['engine'] and st['texts']['title'] == 'test 100% %{pts}'
        hub.op_end()
        assert rec.exists() and rec.stat().st_size > 20000
        assert not hub.output.pushers and hub.output.enc is None
    finally:
        hub.shutdown()
        srv.shutdown()
