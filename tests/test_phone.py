"""The phone page (ismail/phone): a live set in the person's pocket. Nate, 2026-10-05: "make it ergonomic ... easy to
talk back just like I do in VR, and give live feedback through the phone ... while my phone screen is off", with the
agents able to drive the page. The Live DJ asked for its words stamped with the bar he actually heard."""
import json
import subprocess
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from ismail import api  # noqa: F401  (the op table loads)
from ismail.phone import ops as P
from ismail.phone import server as S

STATUS = "live 120 BPM 4/4 | heard bar 5 beat 2 (9 s) | mixed ahead 0.50 s | output none | recording rec_1.wav\n" \
         "deck a: gain 0 dB | 3 tracks\n  lead       code:grand_piano           vol +0 pan +0 level  -18.0 dBFS " \
         "(max 2 s  -12.0) | playing ch3 (pass 1/inf), next ch4 at bar 9 | renders 9x realtime"


DEVICE_CALLS = []
KEY_CALLS = []


class FakeEngine(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        req = json.loads(self.rfile.read(n))
        if req['op'] == 'device':
            DEVICE_CALLS.append(req['args'])
        res = {'ok': True, 'result': STATUS if req['op'] == 'status' else ''}
        if req['op'] == 'keys':
            KEY_CALLS.append(req['args'])
            res = {'ok': True, 'result': {'patched': True, 'what': 'preset:pluck', 'ready': 61, 'of': 61,
                                          'connected': True}}
            if any(ev[1] == 0xF0 for ev in req['args']['events']):
                res = {'ok': False, 'error': 'event [1, 240, 0, 0]: status a note-off, note-on or controller'}
        b = json.dumps(res).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self.send_response(200)
        self.send_header('X-Sample-Rate', '44100')
        self.end_headers()
        t = np.arange(4410) / 44100
        y = (0.3 * np.sin(2 * math.pi * 220 * t) * 32767).astype('<i2')
        block = np.stack([y, y], axis=1).tobytes()
        try:
            for _ in range(200):
                self.wfile.write(block)
                self.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass


def _no_speech_server(audio, name, **kw):
    raise ConnectionRefusedError('no speech server in the tests')


@pytest.fixture
def phone(tmp_path, monkeypatch):
    home, reg, proj = tmp_path / 'phone', tmp_path / 'live', tmp_path / 'song'
    reg.mkdir()
    proj.mkdir()
    monkeypatch.setattr(S, 'HOME', home)
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(reg))
    eng = ThreadingHTTPServer(('127.0.0.1', 0), FakeEngine)
    eng.daemon_threads = True
    threading.Thread(target=eng.serve_forever, daemon=True).start()
    (reg / '1.json').write_text(json.dumps({'port': eng.server_address[1], 'started': time.time(),
                                            'project': str(proj)}))
    monkeypatch.setattr(S, 'stt', _no_speech_server)       # a test that wants words patches its own in
    made, init = [], S.Phone.__init__

    def init_and_keep(self, *a, **kw):                      # the "restarted server" Phones a test makes too
        init(self, *a, **kw)
        made.append(self)
    monkeypatch.setattr(S.Phone, '__init__', init_and_keep)
    ph = S.Phone()
    monkeypatch.setattr(ph, '_cpu_busy', lambda: '')
    S.Handler.ph, S.Handler.agent = ph, S.Agent(ph)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), S.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (home / 'server.json').write_text(json.dumps({'port': httpd.server_address[1], 'pid': os.getpid()}))
    for _ in range(50):
        if ph.engine:
            break
        time.sleep(0.1)
    yield ph, f'http://127.0.0.1:{httpd.server_address[1]}', proj
    for p in made:      # left running, a Phone's transcriber retried a note left in its line inside the next test,
        p.stop()        # through that test's stt and into its inbox
    httpd.shutdown()
    eng.shutdown()
    for enc in list(ph.encoders.values()):
        enc.close()
    ph.encoders.clear()


def post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def test_status_text_is_read():
    s = S.parse_status(STATUS)
    assert s['bpm'] == 120 and s['bpb'] == 4 and s['beat'] == 17.0 and s['ahead'] == 0.5
    assert s['recording'] == 'rec_1.wav' and s['next'] == 'ch4 at bar 9' and s['playing'] == ['ch3 (lead)']
    assert S.parse_status('nonsense') is None
    assert S.bar_text(17.0, 4) == 'bar 5 beat 2'


def test_taps_and_words_reach_the_agent_with_where_they_were(phone):
    ph, base, proj = phone
    page = urllib.request.urlopen(base + '/', timeout=5).read().decode()
    assert 'Hold to talk' in page and 'Love this' in page and 'Change it up' in page
    st = get(base, '/api/state?since=0&wait=0')
    assert st['engine']['playing'] and st['engine']['next'] == 'ch4 at bar 9' and st['rec']['on']
    assert st['listening'] == []
    assert 'lines' in P.phone_listen('dj', wait=0)                  # the DJ is now listening
    assert get(base, '/api/state?since=0&wait=0')['listening'] == ['dj']
    since = json.loads(P.phone_listen('dj', wait=0))['since']
    assert post(base, '/api/tap', {'what': 'love'})['ok']
    assert post(base, '/api/tap', {'what': 'mood', 'mood': 'lift'})['ok']
    with pytest.raises(urllib.error.HTTPError):
        post(base, '/api/tap', {'what': 'explode'})
    lines = json.loads(P.phone_listen('dj', since=since, wait=0))['lines']
    assert [x['kind'] for x in lines] == ['tap', 'mood'] and lines[0]['what'] == 'love' and 'highlight' in lines[0]['means']
    routed = (proj / 'notes' / 'phone_inbox.jsonl').read_text(encoding='utf8')   # the DJ's own inbox file
    assert '"love"' in routed and '"lift"' in routed
    assert get(base, '/api/state?since=0&wait=0')['mood'] == 'lift'


def test_the_agent_drives_the_page(phone, tmp_path):
    ph, base, _ = phone
    first = get(base, '/api/state?since=0&wait=0')['cmd']
    assert 'shown on the phone' in P.phone_say('dropping the pads at bar 9', pin=True, sender='dj')
    P.phone_now(now='Canopy Psy, chapter 3', next='chapter 4 at bar 225', recording_why='set take', sender='dj')
    P.phone_buttons(['darker', {'id': 'drop', 'label': 'drop it now'}])
    st = get(base, f'/api/state?since={first}&wait=0')
    assert st['engine']['now'] == 'Canopy Psy, chapter 3' and st['rec']['why'] == 'set take'
    assert st['pinned']['text'] == 'dropping the pads at bar 9' and [b['id'] for b in st['buttons']] == ['darker', 'drop']
    assert any(c['type'] == 'caption' for c in st['cmds'])
    post(base, '/api/tap', {'what': 'button:drop'})
    assert json.loads(P.phone_listen('dj', since=0, wait=0))['lines'][-1]['label'] == 'drop it now'
    out = P.phone_ask('keep the acid line?')
    qid = get(base, '/api/state?since=0&wait=0')['panels'][-1]['id']
    assert 'answer' in out and post(base, '/api/answer', {'id': qid, 'answer': 'yes'})['ok']
    assert json.loads(P.phone_listen('dj', since=0, wait=0))['lines'][-1]['answer'] == 'yes'
    assert get(base, '/api/state?since=0&wait=0')['panels'] == []
    f = tmp_path / 'take.txt'
    f.write_text('a take')
    P.phone_offer(str(f), auto=True)
    o = get(base, '/api/state?since=0&wait=0')['offers'][-1]
    assert urllib.request.urlopen(base + o['url'], timeout=5).read() == b'a take'
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + '/files/notatoken', timeout=5)



def test_older_downloads_page_back_and_outlive_a_restart(phone, tmp_path):
    ph, base, _ = phone
    for i in range(12):
        f = tmp_path / f'take{i:02d}.txt'
        f.write_text(f'take {i}')
        P.phone_offer(str(f), label=f'take {i}')
    st = get(base, '/api/state?since=0&wait=0')
    assert [o['label'] for o in st['offers']] == ['take 8', 'take 9', 'take 10', 'take 11'] and st['offers_n'] == 12
    first = get(base, '/api/offers?skip=0&n=10')
    assert [o['label'] for o in first['offers']][:2] == ['take 11', 'take 10'] and first['total'] == 12 and first['more']
    rest = get(base, '/api/offers?skip=10&n=10')
    assert [o['label'] for o in rest['offers']] == ['take 1', 'take 0'] and not rest['more']
    assert urllib.request.urlopen(base + rest['offers'][-1]['url'], timeout=5).read() == b'take 0'
    assert S.Phone().view['offers'][0]['label'] == 'take 0'        # a restarted server still has the history

def test_a_blind_exam_on_the_phone_writes_its_answers(phone, tmp_path):
    ph, base, _ = phone
    import soundfile as sf
    a, b = tmp_path / 'a.wav', tmp_path / 'b.wav'
    t = np.arange(22050) / 44100
    sf.write(str(a), 0.1 * np.sin(2 * np.pi * 220 * t), 44100)
    sf.write(str(b), 0.1 * np.sin(2 * np.pi * 230 * t), 44100)
    ans = tmp_path / 'exam' / 'answers.jsonl'
    P.phone_exam('round 35', unmeasured='a test of the page', clips=[{'label': 'A', 'path': str(a)}, {'label': 'B', 'path': str(b)}],
                   question='which is the record?', chips=['harsh', 'thin'], choices=['A', 'B', "can't tell"],
                   answers_path=str(ans), exam_id='r35')
    p = get(base, '/api/state?since=0&wait=0')['panels'][-1]
    assert p['kind'] == 'exam' and [c['label'] for c in p['clips']] == ['A', 'B']
    assert urllib.request.urlopen(base + p['clips'][1]['url'], timeout=5).read() == b.read_bytes()
    post(base, '/api/answer', {'id': 'r35', 'answers': {'choice': "can't tell", 'clips': {'A': ['thin'], 'B': []}}})
    assert json.loads(ans.read_text(encoding='utf8').splitlines()[0])['answers']['choice'] == "can't tell"
    assert json.loads(P.phone_listen('x', since=0, wait=0))['lines'][-1]['kind'] == 'exam'


def test_a_voice_note_is_stamped_and_transcribed(phone, monkeypatch):
    ph, base, _ = phone
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: 'more acid please')
    req = urllib.request.Request(base + '/api/voice?sid=&t=', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm;codecs=opus'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    for _ in range(50):
        lines = json.loads(P.phone_listen('dj', since=0, wait=0))['lines']
        if any(x['kind'] == 'voice_text' for x in lines):
            break
        time.sleep(0.1)
    v = [x for x in lines if x.get('id') == vid]
    assert [x['kind'] for x in v] == ['voice', 'voice_text'] and v[1]['text'] == 'more acid please'
    assert os.path.exists(v[0]['file'])



def test_under_each_note_the_page_says_who_took_it(phone):
    """ledger:M219 (hq:D-179): two agents each left an untagged note, sure the other had it, and the person could not
    see from the phone whether it landed with anyone. Each note gets "taken by X" or "nobody has it yet"."""
    ph, base, _ = phone
    n1 = ph.post({'kind': 'voice_text', 'id': 'v1', 'text': 'more bass'})['n']
    n2 = ph.post({'kind': 'voice_text', 'id': 'v2', 'text': 'said on a card', 'for': 'agent a'})['n']
    st = get(base, '/api/state')
    assert st['taken'] == {'v1': [], 'v2': ['agent a']}             # nobody has v1 yet; v2 was said to agent a
    assert P.phone_claim(n1, by='agent b').startswith('yours')
    later = get(base, f"/api/state?since={st['cmd']}&wait=5")         # a claim that comes later wakes the page
    assert later['taken']['v1'] == ['agent b'] and [c['type'] for c in later['cmds']] == ['view']
    assert P.phone_claim(n1, by='agent c').startswith('taken')         # a second claimer does not change it
    P.phone_claim(n2, by='agent c')
    assert get(base, '/api/state')['taken'] == {'v1': ['agent b'], 'v2': ['agent a', 'agent c']}
    assert S.Phone().taken() == {'v1': ['agent b'], 'v2': ['agent a', 'agent c']}     # and after a restart
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    assert "'taken by ' + esc(tk[c.id].join(', ')) : 'nobody has it yet'" in js and '${took(c)}' in js

@pytest.mark.skipif(not S.ffmpeg(), reason='ffmpeg is not installed')
def test_the_stream_plays_and_knows_the_bar_heard(phone):
    ph, base, _ = phone
    r = urllib.request.urlopen(base + '/stream.mp3?sid=s1&kbps=64', timeout=15)
    assert r.headers['Content-Type'] == 'audio/mpeg'
    got, t0 = b'', time.time()
    while len(got) < 16000 and time.time() - t0 < 10:
        got += r.read1(4096)
    r.close()
    assert len(got) >= 16000 and b'\xff\xfb' in got[:4000] or b'\xff\xf3' in got[:4000]   # mp3 frames
    h = ph.heard('s1', 0.5)
    assert 'behind_s' in h and h.get('bar', 0) >= 5 and h['of'].startswith('bar ')
    st = P.phone_status()
    assert 'phone server' in st and 'engine: playing' in st


def test_a_short_note_that_is_a_command_acts_as_one(phone):
    """Nate, 10-05: the Dime 3 earbud's one button takes the voice note, and the voice does the rest out and about."""
    ph, base, _ = phone
    since = ph.seq
    assert ph.voice_command('Love this!', 'v1', {'of': 'bar 9'}) == 'love'
    assert ph.voice_command('okay, change it up please', 'v2') == 'change'
    assert ph.voice_command('stop listening', 'v3') == 'stop_listening'
    assert ph.voice_command('I love this part but change it up soon', 'v4') is None    # a real note stays a note
    lines = json.loads(P.phone_listen('dj', since=since, wait=0))['lines']
    assert [(x['kind'], x['what']) for x in lines] == [('tap', 'love'), ('tap', 'change'), ('control', 'stop_listening')]
    assert lines[0]['via'] == 'voice' and lines[0]['id'] == 'v1' and lines[0]['heard']['of'] == 'bar 9'
    assert any(c['type'] == 'stop_listening' for c in ph.cmds)
    page = urllib.request.urlopen(base + '/app.js', timeout=5).read().decode()
    assert "if (want && keysOn) return keyNote();" in page and "h('pause'" in page and 'CUES' in page


def test_an_open_page_off_the_stream_still_hears_a_spoken_answer(phone):
    """2026-10-06, Nate's walk: the page reloaded, the stream stayed off, voice notes still came in, and a spoken
    phone_say was dropped ('nobody is listening'). The open page now gets the words as a clip; status says so."""
    ph, base, _ = phone
    import io
    import numpy as np
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros(24000, dtype='float32'), 24000, format='WAV')
    ph._tts = lambda text, voice=None: buf.getvalue()
    assert 'no page is open' in P.phone_say('rain music coming', speak=True)
    get(base, '/api/state?since=0&wait=0')                       # the page is open, not on the stream
    assert 'NOT listening to the stream' in P.phone_status()
    out = P.phone_say('rain music coming', speak=True, sender='dj')
    assert 'spoken clip, 1.0 s' in out
    c = [c for c in ph.cmds if c['type'] == 'say_clip'][-1]
    assert c['text'] == 'rain music coming'
    with urllib.request.urlopen(base + c['url'], timeout=5) as r:
        assert r.status == 200 and len(r.read()) > 1000


def test_what_they_loved_is_kept_and_marks_the_piece_when_it_returns(phone):
    """Nate 10-06: keys stay pressed and nothing shows what he asked for; and a small mark beside now/next for a piece
    he loved before, one played again, or one just made."""
    ph, base, _ = phone
    P.phone_now(now='chapter 3: rain piano', next='chapter 4: dub')
    post(base, '/api/tap', {'what': 'love'})
    post(base, '/api/tap', {'what': 'mood', 'mood': 'lift'})
    s = get(base, '/api/state?since=0&wait=0')
    assert [t['what'] for t in s['taps']] == ['mood', 'love'] and s['taps'][1]['now'] == 'chapter 3: rain piano'
    assert s['tally'] == {'love': 1, 'mood': 1} and s['asked_mood']['mood'] == 'lift'
    assert s['engine']['now_mark'] == 'loved' and s['engine']['next_mark'] is None     # a heart without the DJ saying
    out = P.phone_now(now='chapter 5: new strings', now_mark='new', next_mark='replay')
    assert "(new)" in out and "(replay)" in out
    s = get(base, '/api/state?since=0&wait=0')
    assert s['engine']['now_mark'] == 'new' and s['engine']['next_mark'] == 'replay'
    P.phone_now(now='chapter 3: rain piano')
    assert get(base, '/api/state?since=0&wait=0')['engine']['now_mark'] == 'loved'   # it came back
    with pytest.raises(Exception, match='one of'):
        P.phone_now(now_mark='great')
    assert ph._recent_taps()[-1]['what'] == 'mood'                                      # a restart still has them
    for name in ('icon-192.png', 'icon-512.png', 'icon-maskable.png', 'apple-touch-icon.png'):
        with urllib.request.urlopen(base + '/' + name, timeout=10) as r:
            assert r.read()[:4] == b'\x89PNG'


def test_the_phone_session_is_a_take_on_one_clock(phone):
    """Nate 10-06 08:16: 'can you see when I download stuff? where I'm scrolling? ... like a VR take, but for the
    mobile interface ... do you know if I'm on my phone?' The page reports its actions, timed; phone_timeline lays
    them over the voice notes with the bar playing in the room."""
    ph, base, _ = phone
    P.phone_now(now='chapter 3: rain piano')
    ph.room = lambda age_s=0.0: {'beat': 400.0 - age_s * 2, 'bar': 101, 'of': 'bar 101'}
    r = post(base, '/api/events', {'sid': None, 'events': [
        {'what': 'open', 'mobile': True, 'app': True, 'w': 412, 'h': 915, 'age_ms': 9000},
        {'what': 'scroll', 'to': 'downloads', 'y': 1200, 'age_ms': 4000},
        {'what': 'download', 'file': 'take3.wav', 'age_ms': 3000},
        {'what': 'Bad-Name', 'age_ms': 0},
        {'what': 'note_end', 'dur': 72.5, 'by': 'quiet', 'sent': True, 'nested': {'x': 1}}]})
    assert r['n'] == 4
    rows = json.loads(P.phone_listen('dj', since=0, wait=0, page=True))['lines']
    pg = [x for x in rows if x['kind'] == 'page']
    assert [x['what'] for x in pg] == ['open', 'scroll', 'download', 'note_end']
    assert pg[0]['mobile'] is True and 'ua' in pg[0] and pg[0]['room']['of'] == 'bar 101'
    assert pg[0]['ts'] < pg[3]['ts'] and 'nested' not in pg[3] and pg[1]['now'] == 'chapter 3: rain piano'
    assert not [x for x in json.loads(P.phone_listen('dj', since=0, wait=0))['lines'] if x['kind'] == 'page']
    req = urllib.request.Request(base + '/api/voice?sid=&t=&dur=72.5&end=quiet', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm;codecs=opus'})
    urllib.request.urlopen(req, timeout=5).read()
    tl = P.phone_timeline(minutes=5)
    assert '-- chapter 3: rain piano' in tl and 'download file=take3.wav' in tl and 'scroll to=downloads' in tl
    assert 'voice note 72.5 s, ended by quiet' in tl and 'bar 101' in tl


def test_an_open_panel_and_its_files_survive_a_restart(phone, tmp_path):
    """10-06: a server restart (a merge) dropped an unanswered panel Nate had not read yet."""
    ph, base, _ = phone
    img = tmp_path / 'cover.png'
    img.write_bytes(b'\x89PNG' + b'0' * 100)
    P.phone_panel_show(panel_id='visibility', title='What the agents can see', text='...', image=str(img))
    saved = json.loads((S.HOME / 'state.json').read_text(encoding='utf8'))
    assert [x['id'] for x in saved['panels']] == ['visibility']
    tok = saved['panels'][0]['image'].split('/')[-1]
    assert saved['files'][tok].endswith('cover.png')


def test_the_route_survives_a_restart(phone, tmp_path):
    """ledger:M157: the DJ re-routed the inbox after every phone server restart."""
    ph, base, _ = phone
    (tmp_path / 'other' / 'notes').mkdir(parents=True)
    dest = tmp_path / 'other' / 'notes' / 'phone_inbox.jsonl'
    P.phone_route(inbox=str(dest))
    assert json.loads((S.HOME / 'state.json').read_text(encoding='utf8'))['route'] == str(dest)
    assert S.Phone().route == str(dest)                                     # what a restarted server loads
    P.phone_route()
    assert S.Phone().route is None


def test_an_agent_sets_the_pages_vibe_and_it_stays_readable(phone, tmp_path):
    """Nate 10-06 08:18: 'change the colors ... how the headers look ... song covers in the background, blurred
    ... JavaScript effects ... I feel like you're there'. A vibe is data, checked by the server."""
    ph, base, _ = phone
    assert 'presets: default, rain' in P.phone_vibe(menu=True)
    out = P.phone_vibe(preset='rain')
    assert 'effect rain' in out and 'heading fraunces' in out
    v = get(base, '/api/state?since=0&wait=0')['vibe']
    assert v['css']['--ground'] == '#0b0f14' and 'Fraunces' in v['font_css'] and v['css']['--head'].startswith("'Fraunces'")
    cover = tmp_path / 'cover.jpg'
    cover.write_bytes(b'\xff\xd8' + b'0' * 200)
    P.phone_vibe(image=str(cover), blur=20, accent='#ff8844', effect='pulse')
    v = get(base, '/api/state?since=0&wait=0')['vibe']
    assert v['image'].startswith('/files/') and v['blur'] == 20 and v['effect'] == 'pulse' and v['heading'] == 'fraunces'
    with urllib.request.urlopen(base + v['image'], timeout=5) as r:
        assert r.read()[:2] == b'\xff\xd8'
    for bad, why in ((dict(ground='#f0f0f0'), 'light'), (dict(ink='#333333'), '7:1'), (dict(accent='#202020'), '3:1'),
                     (dict(heading='comic sans'), 'one of'), (dict(effect='fire'), 'one of'), (dict(preset='x'), 'one of')):
        with pytest.raises(Exception, match=why):
            P.phone_vibe(**bad)
    assert get(base, '/api/state?since=0&wait=0')['vibe']['effect'] == 'pulse'        # a refusal changes nothing
    assert 'no art' in P.phone_vibe(image='', reset=True)
    assert any(c['type'] == 'vibe' for c in ph.cmds)
    saved = json.loads((S.HOME / 'state.json').read_text(encoding='utf8'))
    assert saved['vibe']['effect'] == 'none'


def test_time_into_the_piece_rides_with_every_line(phone):
    """Nate 10-06 08:24: bars are one way; a casual listener talks in time ('2:31 into it')."""
    ph, base, _ = phone
    P.phone_now(now='chapter 7: plume house')
    ph.piece = ('chapter 7: plume house', time.time() - 151)
    post(base, '/api/tap', {'what': 'love'})
    s = get(base, '/api/state?since=0&wait=0')
    assert 150 <= s['into_s'] <= 155 and len(s['clock']) == 8
    assert 150 <= s['taps'][0]['into_s'] <= 155
    assert '2:3' in P.phone_timeline(minutes=5)


def test_a_restart_is_announced_and_the_page_can_tell_it_happened(phone):
    """10-06 08:38: a restart after a merge dropped Nate's stream mid-set ("Why'd you stop?"), and a page whose
    command count was past the new server's missed every command after it. The page is told first, and sees the
    boot and the page build change."""
    ph, base, _ = phone
    s = get(base, '/api/state?since=0&wait=0')
    assert s['boot'] == S.BOOT and len(s['build']) == 10
    assert 'told the page' in post(base, '/agent', {'op': 'restarting', 'args': {'back_in_s': 5}})['result']
    c = [c for c in ph.cmds if c['type'] == 'restarting'][-1]
    assert c['back_in_s'] == 5.0
    assert S.page_build() == S.BUILD


def test_downloads_have_names_and_mp3s_carry_ismail(phone, tmp_path):
    """Nate 10-06 09:09: a readable download name, the title in the mp3, and 'ismail with the GitHub link in the ID3
    data, very important for provenance whenever we ship mp3s'. Their own file is never changed."""
    from ismail import tags
    ph, base, _ = phone
    if not S.ffmpeg():
        pytest.skip('ffmpeg is not installed')
    src = tmp_path / 'rec_20261006_090713_hl2.mp3'
    subprocess.run([S.ffmpeg(), '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '1',
                    '-b:a', '64k', str(src)], check=True)
    before = src.read_bytes()
    P.phone_offer(str(src), label='Clair de lune / rain bed (highlight)', album='ismail live 10-06')
    o = get(base, '/api/state?since=0&wait=0')['offers'][-1]
    assert o['name'] == 'Clair de lune rain bed (highlight).mp3'
    with urllib.request.urlopen(base + o['url'] + '?dl=1', timeout=10) as r:
        assert o['name'] in r.headers['Content-Disposition']
        got = r.read()
    out = tmp_path / 'got.mp3'
    out.write_bytes(got)
    t = tags.read_tags(str(out))
    assert t['title'] == 'Clair de lune / rain bed (highlight)' and t['artist'] == 'ismail' and t['album'] == 'ismail live 10-06'
    assert t['url'] == tags.HOME_URL and tags.HOME_URL in t['comment'] and t['encoder'] == 'ismail'
    assert src.read_bytes() == before                                                   # their file is untouched


def test_the_piece_has_a_shape_and_its_start_survives_a_restart(phone):
    """Nate 10-06 09:12: where we are in the song, always on screen; and M139: a restart reset 'into_s'."""
    ph, base, _ = phone
    P.phone_now(now='chapter 9: sweet clair', length=324, into=40,
                sections=[{'at_s': 64, 'name': 'melody'}, {'at_s': 0, 'label': 'intro'}])
    s = get(base, '/api/state?since=0&wait=0')
    assert s['shape']['length_s'] == 324 and [x['label'] for x in s['shape']['sections']] == ['intro', 'melody']
    assert 40 <= s['into_s'] <= 45
    saved = json.loads((S.HOME / 'state.json').read_text(encoding='utf8'))
    assert saved['piece'][0] == 'chapter 9: sweet clair' and saved['shape']['length_s'] == 324
    P.phone_now(now='chapter 10: plume')
    assert get(base, '/api/state?since=0&wait=0')['shape'] == {}                        # a new piece clears it


def test_an_agent_gives_the_page_its_sounds_and_they_stay(phone, tmp_path):
    """Nate 10-06 14:42: "you make your sounds and then you attach it to this interface ... like a design
    philosophy". One sound per event; none means silent; they survive a restart."""
    import soundfile as sf
    ph, base, _ = phone
    ding = tmp_path / 'message.wav'
    sf.write(str(ding), np.zeros(int(0.4 * 22050)), 22050)
    long = tmp_path / 'long.wav'
    sf.write(str(long), np.zeros(int(6 * 22050)), 22050)
    assert '(none)' in P.phone_sounds(menu=True) and 'message' in P.phone_sounds()
    out = P.phone_sounds(event='message', path=str(ding), gain_db=-3)
    assert out.startswith('message: message.wav (0.40 s) at -3 dB'), out
    s = get(base, '/api/state?since=0&wait=0')['sounds']['message']
    assert s['url'].startswith('/files/') and s['gain_db'] == -3
    with pytest.raises(Exception, match="event 'ding'"):
        P.phone_sounds(event='ding', path=str(ding))
    with pytest.raises(Exception, match='at most 5 s'):
        P.phone_sounds(event='love', path=str(long))
    assert '-> message.wav (-3 dB)' in P.phone_sounds(menu=True)
    assert S.Phone().view['sounds']['message']['name'] == 'message.wav'          # a restarted server keeps it
    assert P.phone_sounds(event='note_start', path='').endswith('(the built-in tone)')
    assert P.phone_sounds(event='incoming', path=str(ding)).startswith('message: ')           # the stage's name
    assert P.phone_sounds(event='message', path='') == 'message: cleared (silent)'
    assert get(base, '/api/state?since=0&wait=0')['sounds'] == {}


def test_the_dj_layers_effects_schedules_looks_on_bars_and_keeps_scenes(phone, monkeypatch):
    """ledger:M160 (Nate 10-06 14:47: "way more creative control over the background"): up to three layers with
    their own speed, density, slant, colours and blend; a look on the bar the phone hears; named scenes."""
    ph, base, _ = phone
    out = P.phone_vibe(reset=True, layers=[{'effect': 'aurora', 'speed': 0.6},
                                           {'effect': 'rain', 'density': 0.9, 'angle': 25, 'blend': 'add'}],
                       hue_drift=20, save='storm')
    assert '1. aurora at 0.5 (speed 0.6); 2. rain at 0.5 (density 0.9, angle 25, blend add)' in out and 'storm' in out
    v = get(base, '/api/state?since=0&wait=0')['vibe']
    assert [x['effect'] for x in v['layers']] == ['aurora', 'rain'] and v['layers'][1]['op'] == 'lighter'
    assert v['hue_drift'] == 20
    for bad, why in (([{'effect': 'fire'}], "effect 'fire'"), ([{'effect': 'rain', 'wobble': 1}], 'unknown wobble'),
                     ([{'effect': 'rain'}] * 4, 'at most 3'), ([{'effect': 'rain', 'blend': 'xor'}], "blend 'xor'")):
        with pytest.raises(Exception, match=why):
            P.phone_vibe(layers=bad)
    assert 'effect pulse' in P.phone_vibe(effect='pulse')                     # one effect replaces the layers
    out = P.phone_vibe(at='bar:9', preset='peak', ramp_beats=1)
    assert out.startswith('at bar 9 (as the phone hears it), over 1 beats') and '1 scheduled' in out
    P.phone_vibe(at='bar:17', scene='storm')
    s = get(base, '/api/state?since=0&wait=0')
    assert [m['at_bar'] for m in s['vibe_moves']] == [9, 17] and s['vibe']['effect'] == 'pulse'
    assert s['vibe_moves'][1]['vibe']['layers'][1]['angle'] == 25                # the scene came back whole
    monkeypatch.setattr(S.Phone, 'room', lambda self, age_s=0.0: {'beat': 4 * 18, 'bar': 19, 'of': 'bar 19'})
    s = get(base, '/api/state?since=0&wait=0')                                 # the room is 10 bars past bar 9
    assert [m['at_bar'] for m in s['vibe_moves']] == [17] and s['vibe']['heading'] == 'bebas'
    assert 'cancelled' in P.phone_vibe(cancel_moves=True, effect='none')
    assert get(base, '/api/state?since=0&wait=0')['vibe_moves'] == []
    with pytest.raises(Exception, match='bar:N'):
        P.phone_vibe(at='soon')
    with pytest.raises(Exception, match="scene 'gone'"):
        P.phone_vibe(scene='gone')
    assert 'scenes: storm' in P.phone_vibe(menu=True)


def test_a_caption_can_be_taken_back_off_the_page(phone):
    """Nate 10-06 15:03: a caption named where he lives while he recorded the screen."""
    ph, base, _ = phone
    P.phone_say('a story set in Sometown, Somecountry')
    P.phone_say('the piano is playing')
    P.phone_say('pinned summary from Sometown', pin=True)
    out = P.phone_unsay(match='sometown|elsewhere')
    assert out.startswith('took 3 line(s) off the page'), out
    s = get(base, '/api/state?since=0&wait=0')
    assert [c['text'] for c in s['captions']] == ['the piano is playing'] and s['pinned'] is None
    assert any(c['type'] == 'unsay' for c in ph.cmds)
    assert P.phone_unsay(n=1).startswith('took 1 line(s)')
    assert 'none matched' in P.phone_unsay(match='nothing like it')
    with pytest.raises(Exception, match='say which'):
        P.phone_unsay()


def test_a_pause_tap_stops_the_set_with_no_agent_awake(phone, monkeypatch):
    """The Live DJ's HANDOFF 50 (Nate 10-06 15:08): he pressed pause and the set played 70 s more."""
    ph, base, _ = phone
    stopped = []
    monkeypatch.setattr(ph, 'stop_set', lambda project: stopped.append(project) or 'live engine stopped')
    ph.engine = dict(ph.engine or {}, project='D:/songs/x', bpm=120)
    send = lambda what: urllib.request.urlopen(urllib.request.Request(                 # noqa: E731
        base + '/api/tap', data=json.dumps({'what': what}).encode(), headers={'Content-Type': 'application/json'}),
        timeout=5).read()
    send('pause')
    t = time.time()
    while not any(c['type'] == 'caption' and c['text'].startswith('Paused') for c in ph.cmds) and time.time() - t < 5:
        time.sleep(0.05)
    assert stopped == ['D:/songs/x']
    rows = [json.loads(x) for x in open(S.HOME / 'inbox.jsonl', encoding='utf8')]
    assert rows[-1]['kind'] == 'control' and rows[-1]['what'] == 'paused' and rows[-1]['by'] == 'phone server'
    send('pause')                                                       # a second press within 15 s does nothing more
    time.sleep(0.3)
    assert stopped == ['D:/songs/x']


def test_a_hum_tells_itself_apart_from_talk():
    from ismail.phone import hum
    sr = hum.SR
    t = np.arange(int(sr * 0.5)) / sr
    held = np.concatenate([0.3 * np.sin(2 * np.pi * f * t) for f in (220.0, 246.9, 261.6, 220.0)])
    c = hum.check(held.astype(np.float32), 'Mmm.')
    assert c['is_hum'] and c['voiced'] > 0.8 and c['note'].startswith(('A', 'B', 'C'))
    tt = np.arange(int(sr * 2.0)) / sr                                  # a pitch that never holds, like speech
    glide = 0.3 * np.sin(2 * np.pi * np.cumsum(180 + 80 * np.sin(2 * np.pi * 3 * tt)) / sr)
    c = hum.check(glide.astype(np.float32), 'okay play the piano a bit louder now please')
    assert not c['is_hum'] and 'words a second' in c['why']
    assert hum.words('Mmm la la, play it') == ['play', 'it']


def test_talk_and_long_notes_skip_the_pitch_tracker(monkeypatch):
    """The pitch tracker holds the whole phone server for about half a note's length (10-11: 150 s on a 322 s note,
    and the server refused connections meanwhile); words and length rule a note out first."""
    import librosa
    from ismail.phone import hum
    monkeypatch.setattr(librosa, 'pyin', lambda *a, **k: pytest.fail('the pitch tracker ran'))
    talk = np.zeros(hum.SR * 4, dtype=np.float32)
    c = hum.check(talk, 'so this is for marketing and the paper and the other thing too')
    assert not c['is_hum'] and 'words a second' in c['why']
    long_hum = np.zeros(int(hum.SR * (hum.HUM_MAX_S + 5)), dtype=np.float32)
    c = hum.check(long_hum, 'Mmm.')
    assert not c['is_hum'] and 'too long' in c['why']


def test_every_voice_note_keeps_the_music_under_it(phone, monkeypatch):
    ph, base, _ = phone
    import soundfile as sf
    monkeypatch.setattr(S, 'KEEP_MASTER', False)                       # this test feeds the master itself
    for _ in range(50):
        if not (ph.feeder and ph.feeder.is_alive()):
            break
        time.sleep(0.1)
    ph.sids['s1'] = {'pcm0': 0.0, 'kbps': 64, 't': 0.0, 'at': time.time()}
    now = time.time()
    with ph.cond:
        ph.master.clear()
        ph.timeline.clear()
        for i in range(int(60 * S.SR / S.BLOCK)):                      # 60 s of streamed master, 2 beats a second
            p = i * S.BLOCK / S.SR
            ph.master.append((p, (np.full((S.BLOCK, 2), i % 100, dtype='<i2')).tobytes()))
            ph.timeline.append((p, now - 60 + p, p * 2, 120.0, 4, 0.0))
        ph.fed = int(60 * S.SR / S.BLOCK) * S.BLOCK
    req = urllib.request.Request(base + '/api/voice?sid=s1&t=5.0&dur=3.0&end=press', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm;codecs=opus'})
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
    meta = json.loads((S.HOME / 'voice' / f"{r['id']}_meta.json").read_text(encoding='utf8'))
    x, sr = sf.read(meta['ref'], dtype='int16')
    assert sr == S.SR and abs(len(x) / sr - 8.0) < 0.05 and meta['ref_lead_s'] == 3.0   # 3 s before, 3 s note, 2 after
    bj = json.loads(open(meta['ref_beats'], encoding='utf8').read())
    assert bj['bpm'] == 120.0 and bj['note_starts_at_s'] == 3.0 and abs(bj['beats'][0][1] - 4.0) < 0.1
    # off the stream (heard from the room speaker): the start is a guess from when the note arrived, searched wide
    sent = time.time()                         # the master ended at `now`; a slow runner gets here seconds later
    req = urllib.request.Request(base + '/api/voice?sid=&t=&dur=3.0&ago=0.5&mic=phone%20raw%20ec0%20ns0%20agc0',
                                 data=b'' * 2000,
                                 headers={'Content-Type': 'audio/webm'})
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
    meta = json.loads((S.HOME / 'voice' / f"{r['id']}_meta.json").read_text(encoding='utf8'))
    bj = json.loads(open(meta['ref_beats'], encoding='utf8').read())
    assert meta['start_is_guess'] and bj['search_s'] == S.REF_GUESS_S and meta['ref_lead_s'] == S.REF_GUESS_S
    assert abs(meta['ref_s'] - (S.REF_GUESS_S + 3.5 - (sent - now))) < 1.0     # the master runs out at "now"
    assert meta['mic'] == 'phone raw ec0 ns0 agc0'                     # which route and processing, per note


@pytest.mark.skipif(not S.ffmpeg(), reason='ffmpeg is not installed')
def test_a_hummed_note_reaches_the_inbox_as_a_hum(phone, monkeypatch):
    ph, base, _ = phone
    import io
    import soundfile as sf
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: 'Mmm.')
    t = np.arange(int(16000 * 0.5)) / 16000
    y = np.concatenate([0.3 * np.sin(2 * np.pi * f * t) for f in (196.0, 220.0, 246.9, 196.0)]).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, y, 16000, format='WAV')
    req = urllib.request.Request(base + '/api/voice?sid=&t=&dur=2.0', data=buf.getvalue(),
                                 headers={'Content-Type': 'audio/wav'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    for _ in range(100):
        lines = json.loads(P.phone_listen('dj', since=0, wait=0))['lines']
        if any(x['kind'] == 'hum' for x in lines):
            break
        time.sleep(0.1)
    h = [x for x in lines if x['kind'] == 'hum']
    assert h and h[0]['id'] == vid and h[0]['note'].startswith(('G', 'A'))
    out = P.phone_hum()
    assert f'voice note {vid}' in out and 'a HUM' in out and ('No music was saved' in out or 'a guess' in out)


def test_the_page_reacts_to_the_sets_own_notes(phone):
    """ledger:M160 phase 2: phone_vibe(react=) maps tracks to reactions; the onsets ride in the state only then."""
    ph, base, _ = phone
    out = P.phone_vibe(react={'kick': 'glow', 'qrq*': 'sparks', 'piano': {'do': 'drops', 'color': '#ffd27a'}})
    assert 'notes: kick glow, qrq* sparks, piano drops' in out
    ph.onsets = [{'t': 'kick', 'b': 12.0, 'l': -6.0}]
    s = get(base, '/api/state?since=0&wait=0')
    assert s['vibe']['react']['piano'] == {'do': 'drops', 'amount': 0.7, 'color': '#ffd27a'} and s['onsets']
    with pytest.raises(Exception, match="'explode'"):
        P.phone_vibe(react={'kick': 'explode'})
    P.phone_vibe(react={})
    assert get(base, '/api/state?since=0&wait=0')['onsets'] == []      # off: nothing sent
    assert 'react (a track' in P.phone_vibe(menu=True)


def test_the_marks_are_capped_and_phone_now_still_answers(phone):
    """phone_now's `next` argument hid the builtin next(): past 200 saved marks every call failed ('str' object is
    not callable), after it had already changed the view."""
    ph, base, _ = phone
    ph.view['marks'] = {f'piece {i}': 'new' for i in range(250)}
    out = P.phone_now(next='', now='piece 300', now_mark='loved')
    assert "now='piece 300' (loved)" in out
    assert len(ph.view['marks']) == 200 and 'piece 0' not in ph.view['marks'] and ph.view['marks']['piece 300'] == 'loved'


def test_the_background_is_still_while_no_set_plays(phone, monkeypatch):
    """The person, 10-12: the pulsing should stop when the set is paused or stopped, "more of like an indicator that
    things are playing". A pause stops the engine (live_stop), so paused and stopped both read as no engine; an
    engine left running with every track stopped is not playing either."""
    ph, base, _ = phone
    assert S.parse_status(STATUS)['sounding'] and get(base, '/api/state?since=0&wait=0')['engine']['sounding']
    quiet = STATUS.replace('| playing ch3 (pass 1/inf), next', '| silent, next')
    assert not S.parse_status(quiet)['sounding']
    assert S.parse_status(quiet + '\ndeck b: on air | fader +0 dB')['sounding']                    # a deck plays
    assert not S.parse_status(STATUS + '\nSILENT ON AIR for 12 s: nothing has sounded')['sounding']
    seen = get(base, '/api/state?since=0&wait=0')['cmd']
    monkeypatch.setattr(S, 'live_engines', lambda: {})                                       # stopped, or paused
    t = time.time()
    st = get(base, f'/api/state?since={seen}&wait=10')                                       # wakes the long poll
    assert time.time() - t < 5 and st['engine'] == dict(st['engine'], playing=False, sounding=False)
    assert any(c['type'] == 'engine' and c['sounding'] is False for c in st['cmds'])
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    assert "fx.hold(!(e.playing && e.sounding !== false));" in js
    assert "if (still.matches || rest) return;" in js and "const e = rest ? 0 : Math.exp(-ph * 5)" in js
    assert "T += dt * 1000;" in js and "hue-rotate(${(drift * T / 60000) % 360}deg)" in js     # drift on the held clock


def test_a_panel_takes_choices_checks_toggles_and_a_voice_reply(phone, monkeypatch):
    ph, base, _ = phone
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: 'more like the second one but slower')
    with pytest.raises(Exception) as e:
        P.phone_panel_show(title='Next?', inputs=[{'id': 'x', 'kind': 'slider'}])
    assert 'choice, check, toggle, text' in str(e.value)
    out = P.phone_panel_show(panel_id='dj-1', title='Next section?', sender='dj',
                             inputs=[{'id': 'tempo', 'kind': 'choice', 'label': 'Tempo', 'options': ['slower', 'same']},
                                     {'id': 'parts', 'kind': 'check', 'options': ['bass', 'pads', 'arp']},
                                     {'id': 'drop', 'kind': 'toggle', 'label': 'A drop'}])
    assert 'shown: panel dj-1' in out
    p = ph.view['panels'][-1]
    assert p['buttons'] == ['Send'] and [x['kind'] for x in p['inputs']] == ['choice', 'check', 'toggle']
    # said on the panel: it carries the panel and goes to the agent that sent it; the panel stays open
    req = urllib.request.Request(base + '/api/voice?sid=&t=&panel=dj-1', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    for _ in range(50):
        lines = json.loads(P.phone_listen('dj', since=0, wait=0))['lines']
        if any(x['kind'] == 'voice_text' and x.get('id') == vid for x in lines):
            break
        time.sleep(0.1)
    v = [x for x in lines if x.get('id') == vid]
    assert all(x.get('panel') == 'dj-1' and x.get('for') == 'dj' for x in v) and len(v) == 2
    assert any(x['id'] == 'dj-1' for x in ph.view['panels'])
    post(base, '/api/answer', {'id': 'dj-1', 'answer': 'Send',
                               'values': {'tempo': 'slower', 'parts': ['bass', 'arp'], 'drop': True}})
    a = [x for x in json.loads(P.phone_listen('dj', since=0, wait=0))['lines'] if x['kind'] == 'answer'][-1]
    assert a['values'] == {'tempo': 'slower', 'parts': ['bass', 'arp'], 'drop': True} and a['for'] == 'dj'
    assert a['typed'] == []                     # options picked, boxes ticked: none of it is their own words


def test_a_second_panel_waits_and_not_now_is_a_dismissal(phone):
    """Nate 10-07: an exam interrupted a panel he was reading; Live DJ 10-07: Send with nothing picked read as an
    answer."""
    ph, base, _ = phone
    assert 'other message' not in P.phone_panel_show(panel_id='read-1', title='A long read', text='...')
    out = P.phone_panel_show(panel_id='dj-2', title='Which?', sender='dj',
                             inputs=[{'id': 'which', 'kind': 'choice', 'options': ['a', 'b']},
                                     {'id': 'also', 'kind': 'choice', 'options': ['x'], 'optional': True}])
    assert 'never pops up' in out and '1 other message is open there too (read-1)' in out
    p = ph.view['panels'][-1]
    assert 'optional' not in p['inputs'][0] and p['inputs'][1]['optional'] is True
    post(base, '/api/answer', {'id': 'dj-2', 'dismissed': True})
    a = [x for x in json.loads(P.phone_listen('dj', since=0, wait=0))['lines'] if x['kind'] == 'answer'][-1]
    assert a['dismissed'] is True and a['answer'] is None and a['for'] == 'dj'
    assert [x['id'] for x in ph.view['panels']] == ['read-1']


@pytest.mark.skipif(not S.ffmpeg(), reason='ffmpeg is not installed')
def test_floor_pairs_join_a_phone_exam_renumbered_and_never_first(phone, tmp_path):
    ph, base, _ = phone
    import soundfile as sf
    rng = np.random.default_rng(3)
    t = np.arange(44100) / 44100
    clips, key = [], {}
    for i in (1, 2):
        for ab, cls in zip('AB', ('real', 'synth') if i == 1 else ('synth', 'real')):
            y = 0.2 * np.sin(2 * np.pi * (220 + 7 * i) * t) + 0.02 * rng.standard_normal(len(t))
            f = tmp_path / f'c{i}{ab}.wav'
            sf.write(str(f), y, 44100)
            clips.append({'label': f'{i}{ab}', 'path': str(f)})
            key[f'{i}{ab}'] = cls
    ans = tmp_path / 'exam' / 'answers.jsonl'
    out = P.phone_exam('floor round', unmeasured='a test of the page', clips=clips, question='which is real?', answers_path=str(ans), exam_id='fl1',
                       key=key, secrets=['take_r45'], floor='auto')
    rec = json.loads((tmp_path / 'exam' / 'answers.jsonl.floor.json').read_text(encoding='utf8'))
    assert sorted(r['rate'] for r in rec['floors']) == ['128k', '64k', 'same']        # the full set the first time
    assert all(r['pair'] != 1 for r in rec['floors']) and 'floor pairs: pair' in out
    assert sorted(rec['labels'].values()) == ['1A', '1B', '2A', '2B']
    p = get(base, '/api/state?since=0&wait=0')['panels'][-1]
    labels = [c['label'] for c in p['clips']]
    assert labels == [f'{i}{ab}' for i in range(1, 6) for ab in 'AB']
    same = next(r['pair'] for r in rec['floors'] if r['rate'] == 'same')
    a, b = (urllib.request.urlopen(base + c['url'], timeout=5).read() for c in p['clips'] if c['label'][:-1] == str(same))
    assert a == b                                                              # identical: the floor of floors
    out = P.phone_exam('floor round 2', unmeasured='a test of the page', clips=clips, answers_path=str(tmp_path / 'e2' / 'a.jsonl'), exam_id='fl2',
                       key=key, secrets=['take_r45'], floor='auto')
    assert out.count('pair ') == 1 and '128k' in out                           # then one rotating pair
    with pytest.raises(Exception, match='labelled by pair'):
        P.phone_exam('bad', unmeasured='a test of the page', clips=[{'label': 'A', 'path': clips[0]['path']}], floor='auto')


@pytest.mark.skipif(not S.ffmpeg(), reason='ffmpeg is not installed')
def test_floor_pairs_pass_beside_mp3_clips_and_a_refused_exam_uses_up_nothing(phone, tmp_path):
    """spanish_guitar 10-11: an exam of MP3 clips with floor pairs failed its own pre-flight four times (WAV
    floors read as "formats differ by class"), and each refused post spent the first round's full set."""
    ph, base, _ = phone
    import soundfile as sf
    rng = np.random.default_rng(5)
    t = np.arange(44100) / 44100
    clips, key = [], {}
    for i in (1, 2, 3):
        for ab in 'AB':
            cls = 'record' if (ab == 'A') == (i % 2 == 1) else 'mine'
            y = 0.2 * np.sin(2 * np.pi * (200 + 9 * i) * t) + 0.02 * rng.standard_normal(len(t))
            w = tmp_path / f'w{i}{ab}.wav'
            sf.write(str(w), y, 44100)
            f = tmp_path / f'c{i}{ab}.mp3'
            subprocess.run([S.ffmpeg(), '-y', '-loglevel', 'error', '-i', str(w), '-b:a', '192k', str(f)], check=True)
            clips.append({'label': f'{i}{ab}', 'path': str(f)})
            key[f'{i}{ab}'] = cls
    hist = S.HOME / 'floor_rounds.json'
    shutil.copyfile(clips[0]['path'], tmp_path / 'take_r45.mp3')             # a file name that gives the source away
    leaky = [dict(c, path=str(tmp_path / 'take_r45.mp3')) if c['label'] == '1A' else c for c in clips]
    with pytest.raises(Exception, match='pre-flight failed'):
        P.phone_exam('leaky', unmeasured='a test of the page', clips=leaky, answers_path=str(tmp_path / 'e0' / 'a.jsonl'), exam_id='fx0', key=key,
                     secrets=['take_r45'], floor='auto')
    assert not hist.exists()                                                   # refused: nothing used up
    out = P.phone_exam('mp3 round', unmeasured='a test of the page', clips=clips, answers_path=str(tmp_path / 'e1' / 'a.jsonl'), exam_id='fx1', key=key,
                       secrets=['take_r45'], floor='auto')
    assert out.count('pair ') == 3                                             # the full set, on the round shown
    assert len(json.loads(hist.read_text(encoding='utf8'))) == 1


def test_the_output_switch_acts_on_the_engine_without_an_agent(phone):
    """Nate 10-07: output follows Windows "the way that most applications work", a switch he controls."""
    ph, base, _ = phone
    st = get(base, '/api/state?since=0&wait=0')['engine']
    assert st['output'] == 'none' and st['follow'] is False
    DEVICE_CALLS.clear()
    assert post(base, '/api/output', {'follow': True})['ok']
    assert post(base, '/api/output', {'follow': False})['ok']
    assert DEVICE_CALLS == [{'device': 'default', 'follow': True}, {'follow': False, 'reopen': False}]
    outs = [x for x in json.loads(P.phone_listen('dj', since=0, wait=0))['lines'] if x['kind'] == 'output']
    assert [x['follow'] for x in outs] == [True, False]


def _make_video(path, size='1280x960'):
    ff = S.ffmpeg()
    subprocess.run([ff, '-y', '-loglevel', 'error', '-f', 'lavfi', '-i', f'testsrc=duration=1:size={size}:rate=10',
                    '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', str(path)],
                   check=True)


def _no_machine(monkeypatch):
    import contextlib
    calls = []

    @contextlib.contextmanager
    def slot(kind, what, **kw):
        calls.append((kind, what, kw))
        yield
    monkeypatch.setattr(P.machine, 'slot', slot)
    return calls


@pytest.mark.skipif(not S.ffmpeg(), reason='needs ffmpeg')
def test_a_video_gets_a_phone_sized_copy_once_through_the_machine_gate(phone, tmp_path, monkeypatch):
    calls = _no_machine(monkeypatch)
    src = tmp_path / 'clip.mkv'
    _make_video(src)
    out = P.phone_video_copy(str(src))
    assert out.endswith('.mp4') and os.path.dirname(out) == str(S.HOME / 'videos') and os.path.getsize(out) > 0
    assert [(c[0], c[2]['disk_gb']) for c in calls] == [('cpu', 0.2)] and 'phone video clip.mkv' in calls[0][1]
    ffprobe = shutil.which('ffprobe') or os.path.join(os.path.dirname(S.ffmpeg()), 'ffprobe')
    if os.path.isfile(ffprobe) or shutil.which('ffprobe'):
        info = subprocess.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                               'stream=height,codec_name', '-of', 'csv=p=0', out], capture_output=True, text=True).stdout
        codec, height = info.strip().split(',')
        assert codec == 'h264' and int(height) <= 720
    assert P.phone_video_copy(str(src)) == out and len(calls) == 1          # cached: not converted twice
    (S.HOME / 'videos' / 'x').write_text('')                               # the cache is just files
    shown = P.phone_panel_show(panel_id='v1', title='Watch', video=str(src), sender='dj')
    assert 'shown: panel v1' in shown and len(calls) == 1


def test_a_video_without_ffmpeg_or_on_a_busy_machine_says_what_to_do(phone, tmp_path, monkeypatch):
    src = tmp_path / 'clip.mkv'
    src.write_bytes(b'not really a video')
    monkeypatch.setenv('ISMAIL_FFMPEG', '')
    monkeypatch.setattr(shutil, 'which', lambda name, *a, **k: None)
    with pytest.raises(Exception) as e:
        P.phone_panel_show(title='Watch', video=str(src))
    assert 'ffmpeg' in str(e.value) and 'ISMAIL_FFMPEG' in str(e.value)
    monkeypatch.undo()
    if not S.ffmpeg():
        return
    import contextlib

    @contextlib.contextmanager
    def busy(kind, what, **kw):
        raise P.machine.MachineBusy('a render holds the cpu')
        yield
    monkeypatch.setattr(P.machine, 'slot', busy)
    with pytest.raises(Exception) as e:
        P.phone_panel_show(title='Watch', video=str(src))
    assert 'computer is busy' in str(e.value) and "video_wait='30m'" in str(e.value) and 'a render holds' in str(e.value)


def test_a_panel_carries_a_video_that_plays_and_seeks(phone, tmp_path):
    ph, base, _ = phone
    mp4 = tmp_path / 'talk.mp4'
    blob = bytes(range(256)) * 40
    mp4.write_bytes(blob)
    P._call('panel_show', panel_id='v2', title='Watch this', video=str(mp4), buttons=['Got it'])
    p = ph.view['panels'][-1]
    assert p['id'] == 'v2' and p['video'].startswith('/files/') and 'image' not in p
    assert get(base, '/api/state')['panels'][-1]['video'] == p['video']
    with urllib.request.urlopen(base + p['video'], timeout=10) as r:
        assert r.status == 200 and r.headers['Content-Type'] == 'video/mp4' and r.headers['Accept-Ranges'] == 'bytes'
        assert r.read() == blob

    def ranged(spec):
        req = urllib.request.Request(base + p['video'], headers={'Range': spec})
        return urllib.request.urlopen(req, timeout=10)
    with ranged('bytes=100-299') as r:
        assert r.status == 206 and r.headers['Content-Range'] == f'bytes 100-299/{len(blob)}'
        assert r.read() == blob[100:300]
    with ranged(f'bytes={len(blob) - 10}-') as r:
        assert r.status == 206 and r.read() == blob[-10:]
    with ranged('bytes=-50') as r:
        assert r.status == 206 and r.read() == blob[-50:]
    with pytest.raises(urllib.error.HTTPError) as e:
        ranged(f'bytes={len(blob) + 5}-')
    assert e.value.code == 416
    for ext, mime in (('.webm', 'video/webm'), ('.mov', 'video/quicktime'), ('.md', 'text/plain; charset=utf-8')):
        assert S.Handler.TYPES[ext] == mime
    big = tmp_path / 'long.mp4'                    # "the rest" comes a chunk at a time, so 4G starts at once
    data = bytes(range(256)) * (S.Handler.RANGE_CHUNK // 256 + 4096)
    big.write_bytes(data)
    P._call('panel_show', panel_id='v3', title='Long', video=str(big))
    url = base + ph.view['panels'][-1]['video']
    req = urllib.request.Request(url, headers={'Range': 'bytes=0-'})
    with urllib.request.urlopen(req, timeout=10) as r:
        n = S.Handler.RANGE_CHUNK
        assert r.status == 206 and r.headers['Content-Range'] == f'bytes 0-{n - 1}/{len(data)}' and r.read() == data[:n]
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Range': f'bytes={n}-'}), timeout=10) as r:
        assert r.read() == data[n:]


def test_priority_rides_with_panels_and_notes_and_defaults_to_normal(phone):
    ph, base, _ = phone
    P.phone_panel_show(panel_id='a', title='Plain')
    P.phone_panel_show(panel_id='b', title='Urgent', priority='needs you')
    P.phone_panel_show(panel_id='c', title='Later one', priority='Low')
    P.phone_say('just so you know')
    P.phone_say('pick a drop', priority='needs you')
    st = get(base, '/api/state')
    assert {x['id']: x['priority'] for x in st['panels']} == {'a': 'normal', 'b': 'needs you', 'c': 'low'}
    assert [(c['text'], c['priority']) for c in st['captions']] == [('just so you know', 'normal'),
                                                                    ('pick a drop', 'needs you')]
    with pytest.raises(Exception) as e:
        P.phone_say('hm', priority='urgent!!')
    assert "'needs you'" in str(e.value) and "'low'" in str(e.value)


def test_the_page_keeps_the_key_lit_after_later_and_sorts_by_priority():
    """The page is plain JS: check the rules are in it (Nate 10-07: Later greyed the key with a question still open)."""
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    assert "b.classList.toggle('new', ps.length > 0 || nn > 0)" in js
    assert "classList.toggle('urgent'" in js and "'needs you': 0" in js


def test_every_panel_picture_opens_full_screen_and_back_closes_it():
    """Nate 10-12: a picture on a panel opens full screen like a video, zooms and pans, and closes, with no setup per
    panel. The gestures were checked in a browser; here, that the page wires it up."""
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    html = (S.PAGE / 'index.html').read_text(encoding='utf8')
    assert "$('panel').addEventListener('click', (e) => { if (e.target.tagName === 'IMG') openViewer(e.target); });" in js
    assert "addEventListener('popstate', () => closeViewer(true));" in js and "history.pushState({ viewer: 1 }" in js
    assert '<div id="viewer" hidden><img id="viewimg"' in html and 'id="viewclose"' in html
    assert '#viewer{position:fixed;inset:0;z-index:10' in html and 'touch-action:none' in html



def test_a_long_path_in_the_log_wraps_instead_of_widening_the_page():
    """Nate 10-24: a DJ log note with a full file path made the whole page wider than the phone. A grid track of 1fr
    grows to its longest word; minmax(0,1fr) and overflow-wrap:anywhere let the path wrap inside its row (checked in
    a browser at 375 px: 891 px wide before, 375 after)."""
    html = (S.PAGE / 'index.html').read_text(encoding='utf8')
    assert '.log div{display:grid;grid-template-columns:42px minmax(0,1fr);' in html
    assert '.log div>*{min-width:0;overflow-wrap:anywhere}' in html

@pytest.fixture
def eye_round(tmp_path, monkeypatch):
    """A throwaway picture-round page server (ismail.exampage) on a free port, one round 'r1'; the phone server's
    proxy is pointed at it."""
    from ismail import exampage as X
    root = tmp_path / 'rounds'
    rd = root / 'r1'
    (rd / 'files').mkdir(parents=True)
    (rd / 'manifest.json').write_text(json.dumps({'round': 'r1', 'items': []}), encoding='utf8')
    (rd / 'key.json').write_text('{"1": {"real": "1"}}', encoding='utf8')
    (rd / 'files' / 'q01_word.wav').write_bytes(bytes(range(256)) * 8)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), X._handler(str(root)))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(X, 'PORT', srv.server_address[1])
    yield rd, srv
    srv.shutdown()


def test_the_picture_round_opens_through_the_phone_server(phone, eye_round):
    ph, base, _ = phone
    rd, srv = eye_round
    with urllib.request.urlopen(base + '/eye/r1?from=phone', timeout=10) as r:
        page = r.read().decode()
        assert r.status == 200 and r.headers['Content-Type'].startswith('text/html')
    assert 'Eye Exam' in page and 'name="viewport"' in page
    assert get(base, '/eye/r1/manifest.json')['round'] == 'r1'
    assert b'r1' in urllib.request.urlopen(base + '/eye', timeout=10).read()
    # a byte range reaches the audio, and its Content-Range and Accept-Ranges come back (a phone seeks)
    req = urllib.request.Request(base + '/eye/r1/files/q01_word.wav', headers={'Range': 'bytes=10-19'})
    with urllib.request.urlopen(req, timeout=10) as r:
        assert r.status == 206 and r.headers['Content-Range'] == 'bytes 10-19/2048'
        assert r.headers['Accept-Ranges'] == 'bytes' and r.headers['Content-Type'] == 'audio/wav'
        assert r.read() == bytes(range(10, 20))
    with urllib.request.urlopen(urllib.request.Request(base + '/eye/r1/files/q01_word.wav', method='HEAD'), timeout=10) as r:
        assert r.status == 200 and r.headers['Content-Length'] == '2048' and r.read() == b''
    # the key waits for an answer; the POST, with its body and Content-Type, lands in answers.jsonl
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + '/eye/r1/key', timeout=10)
    assert e.value.code == 403
    out = post(base, '/eye/r1/answer', {'round': 'r1', 'format': 'words', 'answers': [{'q': 1, 'pick': '1'}]})
    assert out == {'ok': True}
    rows = [json.loads(l) for l in (rd / 'answers.jsonl').read_text(encoding='utf8').splitlines()]
    assert rows[0]['answers'] == [{'q': 1, 'pick': '1'}] and 'saved_at' in rows[0]
    assert get(base, '/eye/r1/key')['1']['real'] == '1'
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + '/eye/nope', timeout=10)
    assert e.value.code == 404
    # only /eye is passed on: the page server's own / and /health are not, and the phone's still answer
    assert get(base, '/health')['inbox'] is not None


def test_the_picture_round_says_so_when_none_is_open(phone, monkeypatch):
    import socket
    from ismail import exampage as X
    ph, base, _ = phone
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    monkeypatch.setattr(X, 'PORT', s.getsockname()[1])
    s.close()                                                     # a port nothing listens on
    for method, data in (('GET', None), ('POST', b'{}')):
        req = urllib.request.Request(base + '/eye/r1', data=data, method=method)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 503 and b'No picture round is open right now.' in e.value.read()
    assert S.Handler.is_eye('/eye') and S.Handler.is_eye('/eye/r1/x')
    assert not S.Handler.is_eye('/eyelid') and not S.Handler.is_eye('/api/eye') and not S.Handler.is_eye('/health')


def test_a_panel_link_is_a_same_origin_path_and_rides_with_the_panel(phone):
    ph, base, _ = phone
    for bad in ('https://evil.example/x', '//evil.example/x', 'eye/r1', 'javascript:alert(1)', '/a b', '/a\b', '/a\nb', ''):
        with pytest.raises(Exception) as e:
            P.phone_panel_show(title='Open', link=bad)
        assert 'link must be a path' in str(e.value)
    assert not any(x['title'] == 'Open' for x in ph.view['panels'])
    out = P.phone_panel_show(panel_id='eye-1', title='A picture round', text='Look at the sound.',
                             link='/eye/r1?from=phone', link_label='Open the picture round')
    assert 'shown: panel eye-1' in out
    p = next(x for x in get(base, '/api/state')['panels'] if x['id'] == 'eye-1')
    assert p['link'] == '/eye/r1?from=phone' and p['link_label'] == 'Open the picture round'
    P.phone_panel_show(panel_id='plain-1', title='No link')
    assert 'link' not in next(x for x in ph.view['panels'] if x['id'] == 'plain-1')
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    assert 'class="linkbtn" href="${esc(p.link)}"' in js and 'target=' not in js[js.index('linkbtn') - 80:js.index('linkbtn') + 120]
    words = (S.PAGE.parent.parent / 'exampage' / 'words.html').read_text(encoding='utf8')
    assert 'name="viewport"' in words and 'from' in words and 'Back to the phone' in words


def _exam_rounds(ph):
    return [(r['state'], r['id'], r.get('for'), r.get('title')) for r in ph.read_inbox(0) if r['kind'] == 'exam_round']


def _make_exam(tmp_path, exam_id, who='music'):
    import soundfile as sf
    a = tmp_path / f'{exam_id}.wav'
    sf.write(str(a), 0.1 * np.sin(2 * np.pi * 220 * np.arange(4410) / 44100), 44100)
    return P._call('exam', title='round 1', clips=[{'label': 'A', 'path': str(a)}], exam_id=exam_id, who=who)


def _page(base, what, pid):
    post(base, '/api/events', {'sid': None, 'events': [{'what': what, 'id': pid, 'title': ''}]})


def test_an_exam_round_is_marked_started_and_finished_once(phone, tmp_path):
    ph, base, _ = phone
    _make_exam(tmp_path, 'r1')
    assert _exam_rounds(ph) == []                          # sent to the phone, not on screen yet
    _page(base, 'panel_open', 'r1')
    _page(base, 'panel_open', 'r1')                        # opened twice: still one started
    assert _exam_rounds(ph) == [('started', 'r1', 'music', 'round 1')]
    post(base, '/api/answer', {'id': 'r1', 'answers': {'choice': 'A'}})
    assert _exam_rounds(ph) == [('started', 'r1', 'music', 'round 1'), ('finished', 'r1', 'music', 'round 1')]
    _page(base, 'panel_close', 'r1')                       # already finished: nothing more
    assert len(_exam_rounds(ph)) == 2


def test_an_exam_round_ends_when_dismissed_or_closed_and_plain_panels_are_not_rounds(phone, tmp_path):
    ph, base, _ = phone
    P.phone_panel_show(panel_id='plain', title='Just a question', sender='dj')
    _page(base, 'panel_open', 'plain')
    assert _exam_rounds(ph) == []
    _make_exam(tmp_path, 'r2')
    _page(base, 'panel_open', 'r2')
    post(base, '/api/answer', {'id': 'r2', 'dismissed': True})
    assert [x[0] for x in _exam_rounds(ph)] == ['started', 'finished']
    _make_exam(tmp_path, 'r3')
    _page(base, 'panel_open', 'r3')
    _page(base, 'panel_close', 'r3')
    assert [x[0] for x in _exam_rounds(ph)] == ['started', 'finished', 'started', 'finished']
    # the picture round: a panel linking under /eye/
    P.phone_panel_show(panel_id='eye-9', title='Picture round', sender='music', link='/eye/round9?from=phone')
    _page(base, 'panel_open', 'eye-9')
    assert _exam_rounds(ph)[-1] == ('started', 'eye-9', 'music', 'Picture round')
    P.phone_panel_close('eye-9')
    assert _exam_rounds(ph)[-1][:2] == ('finished', 'eye-9')


def test_phone_status_shows_the_exam_in_progress_and_the_open_panels(phone, tmp_path):
    ph, base, _ = phone
    st = P.phone_status()
    assert 'exam in progress: none' in st and 'open panels: none' in st and 'page shows: no panel' in st
    P.phone_panel_show(panel_id='dj-5', title='Which?', sender='dj', priority='needs you')
    _make_exam(tmp_path, 'r4', who='music')
    _page(base, 'panel_open', 'r4')
    st = P.phone_status()
    assert 'dj-5 (for dj, priority needs you, kind panel)' in st
    assert 'r4 (for music, priority normal, kind exam)' in st
    assert 'page shows: panel r4 since ' in st and ' UTC' in st
    assert 'exam in progress: r4 for music since ' in st
    assert f'last inbox line: {ph.seq}' in st
    post(base, '/api/answer', {'id': 'r4', 'answers': {}})
    st = P.phone_status()
    assert 'exam in progress: none' in st and 'page shows: no panel' in st


def test_phone_listen_returns_exam_round_lines_without_page_true(phone, tmp_path):
    ph, base, _ = phone
    _make_exam(tmp_path, 'r5')
    _page(base, 'panel_open', 'r5')
    lines = json.loads(P.phone_listen('watcher', since=0, wait=0))['lines']
    assert [x['kind'] for x in lines] == ['exam_round']    # the page's own panel_open line is not shown
    assert lines[0]['state'] == 'started' and lines[0]['for'] == 'music' and lines[0]['id'] == 'r5'
    assert any(x['kind'] == 'page' for x in json.loads(P.phone_listen('watcher', since=0, wait=0, page=True))['lines'])


def test_an_exams_voice_notes_land_in_its_answers_file_even_after_the_answer(phone, monkeypatch, tmp_path):
    """Voice 10-08: 30 voice notes said on exam cards went only to the inbox, the answer row's empty note read as "no
    comment", and a note lands seconds after the answer. Now one answers file holds the answer and every note."""
    import soundfile as sf
    ph, base, _ = phone
    gate = threading.Event()
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: (gate.wait(60), 'lisp buzz on the S')[1])
    a, b = tmp_path / 'a.wav', tmp_path / 'b.wav'
    t = np.arange(22050) / 44100
    sf.write(str(a), 0.1 * np.sin(2 * np.pi * 220 * t), 44100)
    sf.write(str(b), 0.1 * np.sin(2 * np.pi * 230 * t), 44100)
    ans = tmp_path / 'exam' / 'answers.jsonl'
    out = P.phone_exam('round 50', unmeasured='a test of the page', clips=[{'label': 'A', 'path': str(a)}, {'label': 'B', 'path': str(b)}],
                       choices=['A', 'B'], answers_path=str(ans), exam_id='r50')
    assert 'clips:' in out and f'{a.resolve()}  0.5 s' in out and "kind 'voice_note'" in out   # the manifest
    assert json.loads((S.HOME / 'state.json').read_text(encoding='utf8'))['answer_files']['r50'] == str(ans.resolve())
    # said into the note box with its mic, then submitted before the words are written down
    req = urllib.request.Request(base + '/api/voice?sid=&t=&panel=r50&field=note', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    post(base, '/api/answer', {'id': 'r50', 'answers': {'choice': 'A', 'note': '', 'note_voice': [vid]}})
    row = json.loads(ans.read_text(encoding='utf8').splitlines()[0])
    assert row['kind'] == 'answer' and row['voice_notes_pending'] == [vid]
    assert row['answers']['note_source'] == 'voice' and row['answers']['note_audio'][0].endswith(f'{vid}.webm')
    assert 'note_voice' not in row['answers']
    gate.set()
    deadline = time.time() + 60                                                 # slow under a full run
    while True:
        rows = [json.loads(x) for x in ans.read_text(encoding='utf8').split('\n')[:-1]]   # whole lines
        if len(rows) > 1 or time.time() > deadline:
            break
        time.sleep(0.1)
    assert len(rows) > 1, (rows, ph.voice_state, list(ph.voice_q))
    note = rows[1]
    assert (note['kind'], note['exam'], note['id'], note['field']) == ('voice_note', 'r50', vid, 'note')
    assert note['text'] == 'lisp buzz on the S' and os.path.exists(note['audio_path'])
    lines = json.loads(P.phone_listen('x', since=0, wait=0))['lines']
    vt = [x for x in lines if x['kind'] == 'voice_text' and x.get('id') == vid]
    assert len(vt) == 1 and vt[0].get('panel') == 'r50' and vt[0].get('field') == 'note', lines   # the inbox has it


def test_a_note_the_speech_server_is_slow_on_waits_longer_and_lets_the_next_through(phone, monkeypatch):
    """10-11: a long note timed out at 120 s while the speech server went on with it; every retry added a copy and
    the short note behind it waited for hours."""
    ph, base, _ = phone
    tries = []

    def slow_then_fine(audio, name, timeout=S.STT_TIMEOUT_S):
        long = audio.startswith(b'the long note')
        tries.append((name, long, timeout))
        if long and sum(1 for _, lg, _ in tries if lg) == 1:
            deadline = time.time() + 60              # fail only once the short note is in line behind it
            while len(ph.voice_q) < 2 and time.time() < deadline:
                time.sleep(0.05)
            raise TimeoutError('timed out')
        return 'the long one' if long else 'the short one'
    monkeypatch.setattr(S, 'STT_RETRY_S', 0.05)
    monkeypatch.setattr(S, 'stt', slow_then_fine)
    ids = []
    for b in (b'the long note ', b'the short note'):
        req = urllib.request.Request(base + '/api/voice?sid=&t=', data=b * 150, headers={'Content-Type': 'audio/webm'})
        ids.append(json.loads(urllib.request.urlopen(req, timeout=5).read())['id'])
    deadline = time.time() + 60                                                 # slow under a full run
    while True:
        lines = json.loads(P.phone_listen('x', since=0, wait=0))['lines']
        vt = [x for x in lines if x['kind'] == 'voice_text' and x.get('id') in ids]
        if len(vt) == 2 or time.time() > deadline:
            break
        time.sleep(0.1)
    state = (ids, tries, ph.voice_state, list(ph.voice_q), lines)
    assert [(x['id'], x['text']) for x in vt] == [(ids[1], 'the short one'), (ids[0], 'the long one')], state
    assert [t for n, _, t in tries if n.startswith(ids[0])] == [S.STT_TIMEOUT_S, 2 * S.STT_TIMEOUT_S], state
    assert [n for n, _, _ in tries if n.startswith(ids[1])] and all(n.startswith(tuple(ids)) for n, _, _ in tries), state


def test_words_written_beside_a_note_are_used_instead_of_the_speech_server(phone, monkeypatch):
    ph, base, _ = phone
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: (_ for _ in ()).throw(TimeoutError('busy')))
    monkeypatch.setattr(S, 'STT_RETRY_S', 0.05)
    req = urllib.request.Request(base + '/api/voice?sid=&t=', data=b'' * 2000, headers={'Content-Type': 'audio/webm'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    (S.HOME / 'voice' / f'{vid}.txt').write_text('transcribed elsewhere', encoding='utf8')
    for _ in range(100):
        vt = [x for x in json.loads(P.phone_listen('x', since=0, wait=0))['lines']
              if x['kind'] == 'voice_text' and x.get('id') == vid]
        if vt:
            break
        time.sleep(0.1)
    assert vt and vt[-1]['text'] == 'transcribed elsewhere'


def test_after_a_restart_notes_still_without_words_go_back_in_line(tmp_path, monkeypatch):
    home = tmp_path / 'phone'
    (home / 'voice').mkdir(parents=True)
    monkeypatch.setattr(S, 'HOME', home)
    monkeypatch.setattr(S.Phone, '_transcriber', lambda self: None)
    monkeypatch.setattr(S.Phone, '_poll_engine', lambda self: None)
    rows, now = [{'kind': 'boot'}], time.time()
    for vid, age, extra in (('waiting', 60, {'panel': 'p-1', 'for': 'film'}), ('done', 60, {}), ('old', 2 * 86400, {})):
        f = home / 'voice' / f'{vid}.webm'
        f.write_bytes(b'' * 10)
        rows.append({'kind': 'voice', 'id': vid, 'file': str(f), 'state': 'transcribing', 'ts': S.now_iso(now - age),
                     **extra})
    rows.append({'kind': 'voice_text', 'id': 'done', 'text': 'said'})
    (home / 'inbox.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf8')
    ph = S.Phone()
    assert [q[0] for q in ph.voice_q] == ['waiting'] and ph.voice_panel['waiting'] == {'panel': 'p-1', 'for': 'film'}


def test_a_panel_text_box_said_by_voice_carries_its_audio(phone, monkeypatch):
    ph, base, _ = phone
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: 'warmer')
    P.phone_panel_show(panel_id='vb-1', title='Which?', sender='dj', inputs=[{'id': 'why', 'kind': 'text'}])
    req = urllib.request.Request(base + '/api/voice?sid=&t=&panel=vb-1&field=why', data=b'\x1a' * 2000,
                                 headers={'Content-Type': 'audio/webm'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    post(base, '/api/answer', {'id': 'vb-1', 'answer': 'Send', 'values': {'why': 'warmer'},
                               'values_voice': {'why': [vid]}})
    a = [x for x in json.loads(P.phone_listen('dj', since=0, wait=0))['lines'] if x['kind'] == 'answer'][-1]
    assert a['values'] == {'why': 'warmer'} and a['values_audio']['why'][0].endswith(f'{vid}.webm')
    assert a['typed'] == ['why']                # a text box: their own words


def test_a_voice_note_keeps_what_played_and_what_they_did_while_speaking(phone, monkeypatch, tmp_path):
    """Nate 10-10: a note said over a panel's video should say where in the video they were when it began, and what
    they did on the page while it recorded."""
    ph, base, _ = phone
    monkeypatch.setattr(S, 'stt', lambda audio, name, **kw: 'this bit, the turn')
    ans = tmp_path / 'answers.jsonl'
    P.phone_panel_show(panel_id='cx-1', title='The cut', sender='film', text='watch it', inputs=[])
    ph.answer_files['cx-1'] = str(ans)
    ctx = {'media': [{'kind': 'video', 'panel': 'cx-1', 'src': 'cut3.mp4', 't': 42.37, 'dur': 90.0, 'playing': True,
                      'junk': {'x': 1}}],
           'acts': [{'what': 'video_seek', 'ms': 1800, 'id': 'cx-1', 'pos': 38.0}, {'what': 'Bad What', 'ms': 1}]}
    q = urllib.parse.urlencode({'sid': '', 't': '', 'panel': 'cx-1', 'ctx': json.dumps(ctx)})
    req = urllib.request.Request(base + '/api/voice?' + q, data=b'\x1a' * 2000, headers={'Content-Type': 'audio/webm'})
    vid = json.loads(urllib.request.urlopen(req, timeout=5).read())['id']
    for _ in range(100):                     # its own row: a slow runner can transcribe an earlier test's note late
        vt = [x for x in json.loads(P.phone_listen('film', since=0, wait=0))['lines']
              if x['kind'] == 'voice_text' and x.get('id') == vid]
        if vt:
            break
        time.sleep(0.1)
    m = vt[-1]['media'][0]
    assert (m['src'], m['t'], m['playing']) == ('cut3.mp4', 42.37, True) and 'junk' not in m
    assert vt[-1]['acts'] == [{'what': 'video_seek', 'ms': 1800, 'id': 'cx-1', 'pos': 38.0}]
    for _ in range(100):                     # the answers file is written just after the inbox row
        rows = [json.loads(x) for x in ans.read_text(encoding='utf8').splitlines()] if ans.exists() else []
        rows = [r for r in rows if r.get('id') == vid]
        if rows:
            break
        time.sleep(0.1)
    assert rows and rows[-1]['media'][0]['t'] == 42.37                          # the answers file has it too


def test_the_keys_page_is_served_and_its_keys_reach_the_playing_engine(phone, monkeypatch):
    """live_keys_page: the phone server serves the on-screen keys and relays each key, one small message, to the
    engine; no engine, and the page says so instead of failing."""
    ph, base, _ = phone
    with urllib.request.urlopen(base + '/keys', timeout=10) as r:
        page = r.read().decode('utf8')
    assert "fetch('/api/keys'" in page and 'name="viewport"' in page and '--ground:#0e0d0b' in page
    KEY_CALLS.clear()
    j = post(base, '/api/keys', {'sid': 'k1', 'events': [[1, 0x90, 60, 90], [2, 0x80, 60, 0]]})
    assert j == {'ok': True, 'result': {'patched': True, 'what': 'preset:pluck', 'ready': 61, 'of': 61,
                                        'connected': True}}
    assert KEY_CALLS == [{'events': [[1, 0x90, 60, 90], [2, 0x80, 60, 0]], 'sid': 'k1'}]
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base, '/api/keys', {'sid': 'k1', 'events': [[3, 0xF0, 0, 0]]})
    assert e.value.code == 400 and 'note-off, note-on or controller' in json.loads(e.value.read())['error']
    monkeypatch.setattr(S, 'live_engines', lambda: {})                   # the set stopped
    for _ in range(50):
        if not ph.engine:
            break
        time.sleep(0.1)
    assert post(base, '/api/keys', {'sid': 'k1', 'events': []}) == {'ok': True, 'result': {'connected': False}}
    assert not any(r.get('kind') == 'keys' for r in ph.read_inbox(0))     # keys are not messages for an agent


def _state(base, dev, ua, sid=''):
    req = urllib.request.Request(base + f'/api/state?since=0&wait=0&dev={dev}&sid={sid}', headers={'User-Agent': ua})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


QUEST_UA = 'Mozilla/5.0 (X11; Linux x86_64; Quest 3) AppleWebKit/537.36 OculusBrowser/35.0 Chrome/126.0 Safari/537.36'
IPHONE_UA = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile Safari/604.1'


def test_phone_claim_the_first_caller_wins_and_every_line_carries_it(phone):
    """The person, 10-16 (n5737, n5776): untagged notes reached nobody or six agents at once; the first agent to say
    it passes a note on owns it, decided by the server, and the others see it taken."""
    ph, base, _ = phone
    n = ph.post({'kind': 'voice_text', 'id': 'v1', 'text': 'for whoever is listening'})['n']
    got, start = {}, threading.Barrier(8)

    def claim(who):
        start.wait()
        got[who] = P.phone_claim(n, by=who)
    ts = [threading.Thread(target=claim, args=(f'agent {i}',)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    won = [w for w, r in got.items() if r.startswith('yours:')]
    assert len(won) == 1 and len(got) == 8
    assert all(r.startswith(f'taken: {won[0]} claimed note {n} at ') for w, r in got.items() if w != won[0])
    assert P.phone_claim(n, by=won[0]).startswith('yours: you claimed')
    line = [x for x in json.loads(P.phone_listen('agent 9', since=n - 1, wait=0))['lines'] if x['n'] == n][0]
    assert line['claimed']['by'] == won[0]
    assert S.Phone().claims[n]['by'] == won[0]          # a restarted server keeps it
    with pytest.raises(api.OpError, match='no inbox line'):
        P.phone_claim(n + 50, by='x')


def test_phone_presence_shows_agents_and_devices_many_to_many(phone):
    ph, base, _ = phone
    t = threading.Thread(target=P.phone_listen, args=('watcher b',), kwargs={'wait': 3})
    t.start()
    P.phone_say('hello', sender='busy agent')
    _state(base, 'quest1', QUEST_UA)
    st = _state(base, 'phone1', IPHONE_UA, sid='s1')
    assert {d['kind'] for d in st['devices']} == {'Quest', 'phone'}
    ph.post({'kind': 'voice_text', 'text': 'old untagged', '_age': 400})
    ph.post({'kind': 'voice_text', 'text': 'new untagged'})
    ph.post({'kind': 'voice_text', 'text': 'tagged one', 'for': 'dj'})
    time.sleep(0.3)
    pr = P.phone_presence()
    assert 'watcher b: watching' in pr and 'busy agent: not watching' in pr
    assert 'Quest (Quest Browser): open' in pr and 'not on the stream' in pr
    assert 'phone (Safari): open' in pr and 'on the stream' in pr
    assert "LEAD: 'old untagged'" in pr and "'new untagged'" in pr and 'tagged one' not in pr
    assert ' LEAD' not in pr.split("'old untagged'")[1]
    st = P.phone_status()
    assert 'agents listening: ' in st and 'watcher b' in st and 'devices open: phone (Safari, stream), Quest' in st
    t.join(10)


def _alarms(ph, what='watcher_down'):
    return [r for r in ph.read_inbox(0) if r.get('kind') == 'alarm' and r.get('what') == what]


def test_a_watcher_that_stops_while_the_person_is_in_vr_raises_one_alarm(phone):
    """ledger:M211 (qa intent_rules 10-16 cluster 3): a DJ whose watcher died left the person asking "you watching
    phone?" while live. In VR, an agent that watched and went quiet for 2 min is named once to the lead and on the
    page, and its return is said once."""
    ph, base, _ = phone
    P.phone_listen('lead a', wait=0, lead=True)
    P.phone_listen('dj', wait=0)
    assert _state(base, 'quest1', QUEST_UA)['deaf'] == [] and _alarms(ph) == []
    ph.listened['dj'] = time.time() - 200                                   # its phone_watch stopped polling
    ph.listened['lead a'] = time.time()
    st = _state(base, 'quest1', QUEST_UA)
    assert st['deaf'] == [{'who': 'dj', 'min': 3}]
    a = _alarms(ph)
    assert len(a) == 1 and a[0]['agent'] == 'dj' and a[0]['for'] == 'lead a' and 'in VR' in a[0]['text']
    assert abs(a[0]['down_since'] - (time.time() - 200)) < 5                # a number, not HH:MM in the text
    _state(base, 'quest1', QUEST_UA)
    P.phone_listen('lead a', wait=0, lead=True)
    assert len(_alarms(ph)) == 1                                            # one per stretch down
    assert 'WATCHER DOWN: dj, stopped 3 min ago' in P.phone_status() and 'lead: lead a' in P.phone_presence()
    P.phone_listen('dj', wait=0)
    assert [r['agent'] for r in _alarms(ph, 'watcher_back')] == ['dj'] and _state(base, 'quest1', QUEST_UA)['deaf'] == []


def test_signoff_and_not_live_raise_nothing_and_expect_holds_without_vr(phone):
    ph, base, _ = phone
    P.phone_listen('dj', wait=0)
    P.phone_listen('voice', wait=0)
    ph.listened['dj'] = ph.listened['voice'] = time.time() - 200
    assert ph.heartbeat() == []                                             # nobody in VR or on the stream
    assert 'NOT watching now' in P.phone_expect('dj', why='a set', minutes=60)
    assert [d['who'] for d in ph.heartbeat()] == ['dj'] and _alarms(ph)[0]['for'] is None   # no lead: everyone's
    assert 'expected: dj until' in P.phone_presence() and 'a set' in P.phone_presence()
    _state(base, 'quest1', QUEST_UA)
    assert 'phone_expect (a set) ended' in P.phone_signoff('dj', why='set over')
    assert [d['who'] for d in ph.heartbeat()] == ['voice']                  # in VR now: voice watched lately
    ph.listened['voice'] = time.time() - S.RECENT_S - 5
    assert ph.heartbeat() == []                                             # too long ago to be expected
    with pytest.raises(api.OpError, match='1 to 1440'):
        P.phone_expect('dj', why='a set', minutes=0)
    with pytest.raises(api.OpError, match='why'):
        P.phone_expect('dj', why=' ')


def test_a_listen_with_lines_names_the_other_watchers(phone):
    ph, base, _ = phone
    t = threading.Thread(target=P.phone_listen, args=('watcher b',), kwargs={'wait': 3, 'since': 10 ** 6})
    t.start()
    time.sleep(0.3)
    ph.post({'kind': 'voice_text', 'text': 'hi'})
    out = json.loads(P.phone_listen('watcher a', since=ph.seq - 1, wait=0))
    assert out['watching'] == ['watcher b']
    t.join(10)


def _until(cond, s=5.0):
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


def test_a_now_label_expires_when_what_it_named_stops(phone, monkeypatch):
    """ledger:M218 (the Live DJ, 10-12): a label a set script put up with phone_now stayed over a later loop that
    never called it, and the page named the wrong piece. The label is tied to what played under it."""
    ph, base, _ = phone
    status = {'text': STATUS}
    monkeypatch.setattr(S, 'engine_call', lambda port, op, timeout=3, **a: status['text'] if op == 'status' else {})
    monkeypatch.setattr(S, 'LABEL_GRACE_BARS', 0)
    assert _until(lambda: ph.engine and ph.engine['clips'] == [['lead', 'ch3']])
    out = P.phone_now(now='Piece one')
    assert 'the now label stays while ch3 (lead) plays' in out
    ph.track_label(S.parse_status(STATUS))
    assert ph.now_text() == 'Piece one'                                         # still playing: it holds
    status['text'] = STATUS.replace('lead       code', 'beats      code').replace('playing ch3', 'playing loop1')
    assert _until(lambda: ph.view['now'] is None)                               # a later loop: it expires
    assert ph.now_text() == 'loop1 (beats)' and ph.view['now_of'] is None
    # named by deck: it holds while the deck's tracks play
    status['text'] = STATUS.replace('lead       code', 'a.lead     code')
    assert _until(lambda: ph.engine['clips'] == [['a.lead', 'ch3']])
    assert 'stays while a plays' in P.phone_now(now='Deck song', now_of=['a'])
    time.sleep(1.5)
    assert ph.now_text() == 'Deck song'
    status['text'] = STATUS.replace('lead       code', 'b.lead     code')
    assert _until(lambda: ph.view['now'] is None)
    # now_of=[]: up until replaced or cleared, whatever plays
    P.phone_now(now='Sticky', now_of=[])
    status['text'] = STATUS
    time.sleep(1.5)
    assert ph.now_text() == 'Sticky' and ph.view['now_of'] is None
    P.phone_now(now='')
    assert ph.now_text() == 'ch3 (lead)'


def test_a_panel_left_unsent_keeps_what_was_typed_said_or_picked():
    """Nate 10-17 (n6203): he typed and dictated into a panel, left it, came back, and the boxes were empty; Send then
    sent nothing (ledger:M227). Leaving by Later, another tab or a tap outside keeps the inputs; reopening puts them
    back; an answer or Not now drops them."""
    js = (S.PAGE / 'app.js').read_text(encoding='utf8')
    assert "const panelKept = store.get('panel_kept', {}) || {};" in js
    assert "function closeSheet() { keepPanel();" in js
    assert "function showPanel(p) {\n  keepPanel();" in js and "function showNotes() {\n  keepPanel();" in js
    assert "if (panelKept[p.id] && p.kind !== 'exam') putBack(box, p, panelKept[p.id]);" in js
    assert js.index('putBack(box, p, panelKept[p.id])') < js.index('(fieldDrafts[p.id] || []).forEach')   # late voice text adds to it
    assert "function answeredHere(id) { answered.add(id); forgetPanel(id);" in js
    assert "if (!shown || shown === '__notes' || answered.has(shown)) return;" in js
