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


def test_a_new_watcher_starts_minutes_back(home):
    hub = server.Hub(port=0)
    try:
        for i, ago in enumerate((900, 600, 120, 30)):
            hub.log.add('chat', 'practice', user={'name': f'v{i}'}, text=f'line {i}')
            hub.log.lines[-1]['at'] = time.time() - ago
        hub.log.add('said', 'practice', text='refused', ok=False)
        got = hub.op_listen('MC', since=None, wait=0, back_s=300)
        assert [d['text'] for d in got['lines']] == ['line 2', 'line 3']        # the room's opening, not the past
        assert hub.op_listen('MC2', since=None, wait=0)['lines'] == []          # no back_s: from the end, as before
    finally:
        hub.shutdown()


# ------------------------------------------------------------------ the visual page (scene, levels)

def test_scene_checks_layers_and_points_forward(home):
    from ismail.stream import scene as SC
    assert SC.check_scene(SC.DEFAULT)['layers'][0]['kind'] == 'bars'
    for bad, says in [({'kind': 'lasers'}, 'kind is one of'),
                      ({'kind': 'bars', 'glow': 1}, "unknown \\['glow'\\]"),
                      ({'kind': 'bars', 'color': 'red'}, '#rrggbb'),
                      ({'kind': 'bars', 'bands': [3, 20]}, 'band indices 0-15'),
                      ({'kind': 'pulse', 'drive': 'loudness'}, "'level', 'beat'"),
                      ({'kind': 'ring', 'x': 3}, 'x: a number from -0.5 to 1.5'),
                      ({'kind': 'hits', 'tracks': {'kick': 'pink'}}, '<track name>')]:
        with pytest.raises(ValueError, match=says):
            SC.check_layer(0, bad)
    with pytest.raises(ValueError, match='background'):
        SC.check_scene({'background': ['#000000'] * 4})
    hub = server.Hub(port=0)
    try:
        v = hub.scene_v
        hub.op_scene(layers=[{'kind': 'pulse', 'drive': 'track:kick', 'color': '#ff0000'}], trails=0.5)
        hub.op_scene(layers=[{'kind': 'text', 'text': 'hello', 'x': 0.1, 'y': 0.1}], replace=False)
        assert [d['kind'] for d in hub.scene['layers']] == ['pulse', 'text'] and hub.scene['trails'] == 0.5
        assert hub.scene_v == v + 2 and json.loads((home / 'scene.json').read_text())['layers'][1]['text'] == 'hello'
        with pytest.raises(ValueError):
            hub.op_scene(layers=[{'kind': 'bars', 'opacity': 2}])
        assert len(hub.scene['layers']) == 2                       # a bad scene leaves the last good one up
    finally:
        hub.shutdown()


def test_bands_put_a_tone_in_its_band_at_its_level():
    import numpy as np
    from ismail.stream import levels as L
    t = np.arange(L.N_FFT) / L.SR
    edges = L.band_edges()
    for f in (60, 440, 3000, 12000):
        b = L.analyse(0.5 * np.sin(2 * np.pi * f * t))['bands']
        k = int(np.argmax(b))
        assert edges[k] <= f < edges[k + 1] and abs(b[k] - 0.9) < 0.05    # -6 dBFS on a 60 dB scale, within 3 dB
    quiet = L.analyse(np.zeros(L.N_FFT))
    assert quiet['level'] == 0 and max(quiet['bands']) == 0


class _Engine:
    """A stand-in live engine: a tone on its master stream, and the clock and onsets ops."""
    def __init__(self, registry):
        import numpy as np
        from http.server import BaseHTTPRequestHandler
        from ismail.stream.levels import SR
        t0, tone = time.time(), (0.5 * np.sin(2 * np.pi * 440 * np.arange(SR // 10) / SR) * 32767).astype('<i2')
        pcm = np.repeat(tone, 2).tobytes()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(pcm)
                        time.sleep(0.1)
                except OSError:
                    pass

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                beat = (time.time() - t0) * 2.0                           # 120 bpm
                res = {'wall': time.time(), 'beat': beat, 'bpm': 120.0, 'bpb': 4}
                if req['op'] == 'onsets':
                    res['onsets'] = [{'t': 'kick', 'b': float(k), 'l': -6.0} for k in range(int(beat), int(beat) + 9)]
                b = json.dumps({'ok': True, 'result': res}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        registry.mkdir(parents=True, exist_ok=True)
        (registry / 'demo.json').write_text(json.dumps({'port': self.port, 'project': 'demo', 'started': t0}))


def test_levels_read_the_engines_master_beat_and_notes(home, tmp_path, monkeypatch):
    import numpy as np
    from ismail.stream.levels import Levels, band_edges
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(tmp_path / 'engines'))
    eng = _Engine(tmp_path / 'engines')
    lv = Levels()
    lv.want(8.0)
    seen = []

    def got():
        seen.append(lv.wait(-1, 0.5)[1])
        return seen[-1]['engine'] == eng.port and seen[-1]['beat'] is not None and any(x['hits'] for x in seen)
    try:
        assert wait_for(got, timeout=7)
        f = seen[-1]
        assert f['level'] > 0.8 and max(range(16), key=lambda k: f['bands'][k]) == int(np.searchsorted(band_edges(), 440)) - 1 and f['bpm'] == 120.0
        assert {h['track'] for x in seen for h in x['hits']} == {'kick'}
    finally:
        lv.until = 0
        eng.srv.shutdown()


def test_the_page_its_scene_its_size_and_its_frames(home):
    import urllib.request
    hub = server.Hub(port=0)
    server.Handler.hub = hub
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    hub.port = srv.server_address[1]
    base = f'http://127.0.0.1:{hub.port}'
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def get(p, timeout=5):
        with urllib.request.urlopen(base + p, timeout=timeout) as r:
            return r.read()
    try:
        assert b'<title>ismail visual</title>' in get('/visual')
        v = json.loads(get('/visual/scene?v=0'))['v']
        got = {}
        th = threading.Thread(target=lambda: got.update(json.loads(get(f'/visual/scene?v={v}&wait=10', 15))))
        t0 = time.time()
        th.start()
        time.sleep(0.3)
        hub.op_scene(layers=[{'kind': 'pulse'}])
        th.join(5)
        assert got['v'] == v + 1 and got['scene']['layers'][0]['kind'] == 'pulse' and time.time() - t0 < 3
        get('/visual/size?w=1280&h=720')
        assert hub.page_view == (1280, 720)
        with urllib.request.urlopen(base + '/levels', timeout=5) as r:
            line = r.readline()
        f = json.loads(line[len(b'data: '):])
        assert line.startswith(b'data: ') and f['engine'] is None and len(f['bands']) == 16
        p = hub.op_page()
        assert p['url'].endswith('/visual') and p['scene']['layers'][0]['kind'] == 'pulse'
    finally:
        hub.shutdown()
        srv.shutdown()


def test_wgc_captures_a_gpu_window_by_title_cropped_to_its_content(tmp_path, home):
    vis = {**output.DEFAULT_VISUAL, 'window': 'hall (on air)', 'capture': 'wgc', 'crop': [0, 31, 1280, 720]}
    a = output.build_encoder_args(vis, tmp_path, tmp_path / 'p.jpg', 'http://x/pcm', nvenc=True)
    g = a[a.index('-filter_complex') + 1]
    assert 'gdigrab' not in a and a.count('-i') == 1                   # the window is a source filter, not an input
    assert "gfxcapture=window_title='^hall .on air.$':capture_cursor=0:max_framerate=30,hwdownload,format=bgra," in g
    assert 'crop=1280:720:0:31,' in g and '[0:a]asplit' in g
    assert output.title_regex('a:b,c[d]') == '^a.b.c.d.$'
    assert output.title_regex('hall - Microsoft​ Edge') == '^hall - Microsoft.+ Edge$'   # UTF-8 bytes, not one '.'
    with pytest.raises(ValueError, match='capture'):
        output.Output(StubHub(home)).set_visual({'capture': 'screen'})


def test_wgc_asked_for_without_gfxcapture_is_refused_not_downgraded(home, monkeypatch):
    monkeypatch.setattr(output, '_GFX', False)
    with pytest.raises(ValueError, match="needs ffmpeg 8 with gfxcapture"):
        output.Output(StubHub(home)).set_visual({'capture': 'wgc'})


def test_page_mode_captures_the_page_window_cropped(tmp_path):
    vis = {**output.DEFAULT_VISUAL, 'mode': 'page', 'crop': [8, 31, 1280, 720]}
    a = output.build_encoder_args(vis, tmp_path, tmp_path / 'p.jpg', 'http://x/pcm', nvenc=False)
    g = a[a.index('-filter_complex') + 1]
    assert 'gdigrab' in a and f'title={output.PAGE_TITLE}' in a and 'crop=1280:720:8:31,' in g
    assert 'showcqt' not in g and 'overlay' not in g.split('drawtext')[0]


def test_an_encoder_failure_names_its_cause_and_the_next_call():
    page = {**output.DEFAULT_VISUAL, 'mode': 'page', 'capture': 'wgc'}
    crop = ('[Parsed_crop_3 @ 0] Failed to configure input pad on Parsed_crop_3 | [fc#0 @ 0] Terminating thread with '
            'return code -22 (Invalid argument) | [out#0/mpegts @ 0] Nothing was written into output file')
    cause, nxt = output.encoder_failure(crop, page)
    assert 'crop' in cause and 'changed size' in cause
    assert "stream_visual(mode='cqt')" in nxt and "capture='gdi'" in nxt and nxt.endswith('stream_go')
    gone = ('[Parsed_gfxcapture_0 @ 0] Failed to configure output pad on Parsed_gfxcapture_0 | Error configuring '
            'filter graph: No such file or directory')
    cause, nxt = output.encoder_failure(gone, {**output.DEFAULT_VISUAL, 'window': 'hall (on air)', 'capture': 'gdi'})
    assert "no window titled 'hall (on air)'" in cause and 'window=None' in nxt and "capture='wgc'" in nxt
    cause, nxt = output.encoder_failure('x | Unknown encoder h264_qsv', output.DEFAULT_VISUAL)
    assert cause == 'Unknown encoder h264_qsv' and 'stream_look' in nxt


class FakePusher:
    url_of, secrets, sent = None, (), 0

    def feed(self, b, total):
        pass

    def stop(self):
        pass

    def status(self):
        return {'state': 'live'}


def test_an_encoder_that_never_gives_a_picture_stops_after_three_tries(home, monkeypatch):
    """2026-10-31: a page capture that gave no packets restarted every 5 s for as long as the stream ran, and each
    restart brought the page window up again. Now three starts in a row with nothing written stop the output."""
    import sys
    script = ('import sys; sys.stderr.write("[Parsed_crop_3 @ 0] Failed to configure input pad on Parsed_crop_3\\n'
              '[out#0/mpegts @ 0] Nothing was written into output file\\n"); sys.exit(1)')
    monkeypatch.setattr(output, 'ffmpeg', lambda: sys.executable)
    monkeypatch.setattr(output, 'build_encoder_args', lambda *a: [sys.executable, '-c', script])
    hub = StubHub(home)
    out = output.Output(hub)
    out.visual['window'], out.visual['capture'], out.nvenc = 'hall (on air)', 'gdi', False
    monkeypatch.setattr(output, 'window_content_box', lambda title: None)
    with out.lock:
        out.pushers = {'rec': FakePusher()}
        out._start_encoder()
    def states():
        return [d['state'] for d in hub.log.after(0) if d['kind'] == 'status']
    assert wait_for(lambda: 'end' in states(), timeout=20)        # 'stopped' is set a moment before end() logs
    states = states()
    assert states == ['encoder restart', 'encoder restart', 'encoder stopped', 'end']
    assert out.enc_fails == 3 and not out.pushers and out.enc is None
    st = out.status()
    assert st['state'] == 'off' and st['summary'].startswith('off: the encoder failed 3 starts in a row')
    assert "crop of the 'hall (on air)' window" in st['stopped'] and 'Next: stream_visual(window=None)' in st['stopped']
    time.sleep(2.5)
    assert len([d for d in hub.log.after(0) if d['kind'] == 'status']) == 4       # and nothing starts it again


def test_a_page_window_already_open_is_fitted_again_never_opened_twice(home, monkeypatch):
    """Edge hands a launch on a profile it already runs to that browser and exits, so the process we started says
    nothing about the window: an open page window is found by its title, kept, and its crop measured again."""
    closed, fitted = [], []
    monkeypatch.setattr(output, 'windows_titled', lambda title: [11, 22] if title == output.PAGE_TITLE else [])
    monkeypatch.setattr(output, 'close_windows', closed.extend)
    monkeypatch.setattr(output, 'fit_window', lambda title, W, H, view, **k: fitted.append((W, H)) or [0, 31, W, H])

    def no_launch(*a, **k):
        raise AssertionError('a second page window was launched')
    monkeypatch.setattr(output.subprocess, 'Popen', no_launch)
    hub = StubHub(home)
    hub.page_view = (900, 500)                        # an old report: measured again, never used as it was
    out = output.Output(hub)
    out.visual.update(mode='page', width=1280, height=720)
    out.open_page()
    assert closed == [22] and fitted == [(1280, 720)] and out.page_box == [0, 31, 1280, 720]
    assert hub.page_view is None and out.page_window()['open']
    out._visual_used = {**out.visual}
    out.end()                                         # nothing streaming: the page window is closed with the take
    assert closed == [22, 11, 22]


def test_holding_the_page_window_never_restarts_a_live_encoder(home, monkeypatch):
    """2026-10-31: a page that drew a few pixels off the stream's size (display scaling) made the hold loop restart
    the live encoder every few seconds, before its capture ever gave a frame, so YouTube got nothing. Now it refits
    the window and keeps the crop for the next start, and the running encoder is left alone."""
    hub = StubHub(home)
    hub.page_view = (1200, 676)
    out = output.Output(hub)
    out.visual['mode'] = 'page'
    monkeypatch.setattr(output, 'windows_titled', lambda title: [1])
    monkeypatch.setattr(output, 'fit_window', lambda *a, **k: [0, 40, 1280, 720])
    restarts = []
    monkeypatch.setattr(out, '_restart_all', lambda: restarts.append(1))

    class Running:
        def poll(self):
            return None
    enc = out.enc = Running()
    out.pushers = {'rec': FakePusher()}
    t = threading.Thread(target=out._hold_page, daemon=True)
    t.start()
    assert wait_for(lambda: out.page_box == [0, 40, 1280, 720], timeout=5)
    time.sleep(1.5)
    assert out.enc is enc and not restarts
    out.pushers = {}
    t.join(5)
    assert not t.is_alive()
