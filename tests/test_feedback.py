"""feedback_page / feedback_read / feedback_list / feedback_stop: a page answered by typing or by voice, kept on this
computer and brought back on reopen. The server runs in this process on a free port; the speech server and the
browser are mocked (no model, no sound, no window)."""
import json
import os
import threading
import time
import urllib.error
import urllib.request
import wave

import pytest

from ismail import api
from ismail import feedback as FB

WEBM = b'\x1a\x45\xdf\xa3' + b'\x00' * 60          # a stand-in for a recorded clip: never decoded here


def _wav(path, secs=0.05, sr=8000):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b'\x00\x00' * int(secs * sr))
    return path


def _until(cond, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=5) as r:
        return r.status, r.headers, r.read()


def _post(url, data, ctype='application/json', headers=None):
    req = urllib.request.Request(url, data=data, method='POST', headers={'Content-Type': ctype, **(headers or {})})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


class Srv:
    def __init__(self, home, words):
        self.httpd = FB.make_server(home, port=0)
        self.port = self.httpd.server_address[1]
        FB.write_json(home / 'server.json', {'port': self.port, 'pid': os.getpid()})
        self.words = words
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.store.stopped.set()
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setenv('ISMAIL_FEEDBACK_HOME', str(home))
    monkeypatch.setattr(FB, 'cpu_busy', lambda: '')
    heard = []
    monkeypatch.setattr(FB, 'transcribe', lambda p, tries=0: heard.append(str(p)) or 'the intro drags a little')
    opened = []
    monkeypatch.setattr('webbrowser.open', lambda url, new=0: opened.append(url) or True)
    song = tmp_path / 'song'
    song.mkdir()
    _wav(song / 'take.wav')
    s = {'home': home, 'song': song, 'heard': heard, 'opened': opened}
    yield s
    if s.get('srv'):
        s['srv'].close()


def _serve(env):
    env['srv'] = Srv(env['home'], env['heard'])
    return env['srv']


def _page(env, **kw):
    args = dict(items=[{'label': 'The take', 'path': str(env['song'] / 'take.wav')},
                       {'label': 'What changed', 'text': 'The bass is lower.'}],
                questions=['What do you hear first?', 'Anything else?'])
    args.update(kw)
    return api.OPS['feedback_page'](str(env['song']), 'r1', **args)


def test_page_is_written_served_and_opened_in_the_default_browser(env):
    srv = _serve(env)
    out = _page(env)
    d = env['song'] / 'work' / 'feedback' / 'r1'
    assert (d / 'manifest.json').is_file() and (d / 'answers.json').is_file() and (d / 'voice').is_dir()
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    assert url in out and 'opened in the default browser' in out and 'already running' in out
    assert env['opened'] == [url]
    code, _, html = _get(url)
    assert code == 200 and b'Record' in html
    man = json.loads(_get(url + 'manifest')[2])
    assert [q['n'] for q in man['questions']] == [1, 2] and man['voice'] is True
    assert man['items'][0] == {'n': 1, 'label': 'The take', 'kind': 'audio', 'src': 'item/1'}
    assert str(env['song']) not in json.dumps(man)          # no path on this computer reaches the page
    code, hdr, body = _get(url + 'item/1', {'Range': 'bytes=0-9'})
    assert code == 206 and len(body) == 10 and hdr['Content-Type'] == 'audio/wav'


def test_open_false_only_returns_the_link(env):
    _serve(env)
    out = _page(env, open=False)
    assert env['opened'] == [] and 'not opened (open=False)' in out


def test_typed_and_said_answers_are_kept_transcribed_and_come_back(env):
    srv = _serve(env)
    _page(env, open=False)
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    assert _post(url + 'text', json.dumps({'q': 1, 'text': 'The kick, then'}).encode())['ok']
    assert _post(url + 'text', json.dumps({'q': 1, 'text': 'The kick, then the pad.'}).encode())['ok']
    clip = _post(url + 'voice?q=2&secs=1.5', WEBM, 'audio/webm;codecs=opus')['clip']
    assert clip['file'].startswith('q2_') and clip['file'].endswith('.webm')
    d = env['song'] / 'work' / 'feedback' / 'r1'
    assert (d / 'voice' / clip['file']).read_bytes() == WEBM
    assert _until(lambda: FB.read_json(d / 'answers.json')['2']['said'][0]['state'] == 'done')
    assert (d / 'voice' / clip['file']).with_suffix('.txt').read_text(encoding='utf8') == 'the intro drags a little'
    # reopening the page: the state brings back the text, the clip and its words
    st = json.loads(_get(url + 'state')[2])['answers']
    assert st['1']['text'] == 'The kick, then the pad.'
    said = st['2']['said'][0]
    assert said['text'] == 'the intro drags a little' and said['secs'] == 1.5
    assert _get(url + 'voice/' + said['file'])[2] == WEBM
    log = (d / 'answers_log.jsonl').read_text(encoding='utf8')
    assert 'The kick, then"' in log                        # the earlier wording is kept in the log
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert 'The kick, then the pad.' in out and 'the intro drags a little' in out
    assert str(d / 'voice' / clip['file']) in out and 'latest:' in out


def test_bad_posts_are_refused(env):
    srv = _serve(env)
    _page(env, open=False)
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    for path, data in (('text', json.dumps({'q': 9, 'text': 'x'}).encode()), ('voice?q=1', b'tiny')):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(url + path, data)
        assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(f'http://127.0.0.1:{srv.port}/f/nope/')
    assert e.value.code == 404


def test_writes_from_another_site_are_refused(env):
    srv = _serve(env)
    _page(env, open=False)
    base = f'http://127.0.0.1:{srv.port}'
    url = base + '/f/song-r1/'
    body = json.dumps({'q': 1, 'text': 'from the page'}).encode()
    assert _post(url + 'text', body, headers={'Origin': base, 'Sec-Fetch-Site': 'same-origin'})['ok']
    assert _post(url + 'text', body, headers={'Origin': f'http://localhost:{srv.port}'})['ok']
    assert _post(url + 'text', json.dumps({'q': 1, 'text': 'from an op'}).encode())['ok']     # no Origin: urllib
    for path, data, h in (('text', body, {'Origin': 'http://evil.example'}),
                          ('voice?q=1', WEBM, {'Origin': 'http://127.0.0.1:1'}),
                          ('text', body, {'Sec-Fetch-Site': 'cross-site'})):
        with pytest.raises(urllib.error.HTTPError) as e:
            _post(url + path, data, headers=h)
        assert e.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(base + '/shutdown', b'{}', headers={'Origin': 'http://evil.example'})
    assert e.value.code == 403
    assert FB.running() == srv.port
    st = FB.read_json(env['song'] / 'work' / 'feedback' / 'r1' / 'answers.json')
    assert st['1']['text'] == 'from an op' and not st['1']['said']


def test_reopening_the_round_keeps_answers(env):
    srv = _serve(env)
    _page(env, open=False)
    _post(f'http://127.0.0.1:{srv.port}/f/song-r1/text', json.dumps({'q': 1, 'text': 'kept'}).encode())
    out = _page(env, open=False, questions=['What do you hear first?', 'Anything else?', 'A third?'])
    assert 'kept the 1 answer already given' in out and '3 questions' in out
    assert 'song-r1/' in out and 'song-r1-2' not in out       # the same link every time


def test_read_says_when_nothing_came_in_and_filters_since(env):
    srv = _serve(env)
    _page(env, open=False)
    assert 'nothing has come in yet' in api.OPS['feedback_read'](str(env['song']), 'r1')
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    _post(url + 'text', json.dumps({'q': 1, 'text': 'first'}).encode())
    first = api.OPS['feedback_read'](str(env['song']), 'r1')
    latest = first.split('latest: ')[1].split(' ')[0]
    assert 'nothing new since' in api.OPS['feedback_read'](str(env['song']), 'r1', since=latest)
    time.sleep(1.1)
    _post(url + 'text', json.dumps({'q': 2, 'text': 'second'}).encode())
    new = api.OPS['feedback_read'](str(env['song']), 'r1', since=latest)
    assert 'second' in new and 'first' not in new and '1 question with no answer since then' in new


def test_read_says_transcription_waits_when_the_server_is_down(env, monkeypatch):
    srv = _serve(env)
    monkeypatch.setattr(FB, 'cpu_busy', lambda: 'the CPU is 95% busy')
    _page(env, open=False)
    _post(f'http://127.0.0.1:{srv.port}/f/song-r1/voice?q=1&secs=2', WEBM, 'audio/ogg')
    d = env['song'] / 'work' / 'feedback' / 'r1'
    assert _until(lambda: 'CPU' in (FB.read_json(d / 'answers.json')['1']['said'][0]['state']))
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert 'Transcription is still running for 1 clip' in out and 'CPU is 95% busy' in out
    srv.close()
    env['srv'] = None
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert 'the page server is not running' in out and 'feedback_page(' in out


def test_clips_left_waiting_are_transcribed_after_a_restart(env):
    srv = _serve(env)
    _page(env, open=False)
    d = env['song'] / 'work' / 'feedback' / 'r1'
    srv.httpd.store.stt = False                                # the speech server "down": the clip stays waiting
    _post(f'http://127.0.0.1:{srv.port}/f/song-r1/voice?q=1&secs=1', WEBM, 'audio/webm')
    time.sleep(0.3)
    assert FB.read_json(d / 'answers.json')['1']['said'][0]['state'] != 'done'
    srv.close()
    _serve(env)
    assert _until(lambda: FB.read_json(d / 'answers.json')['1']['said'][0]['state'] == 'done')


def test_list_and_stop(env):
    srv = _serve(env)
    _page(env, open=False)
    out = api.OPS['feedback_list'](str(env['song']))
    assert out.startswith('r1: 0/2 questions answered') and f'running on port {srv.port}' in out
    assert 'r1:' in api.OPS['feedback_list']()
    assert f'stopped the feedback page server on port {srv.port}' in api.OPS['feedback_stop']()
    assert _until(lambda: FB.running() is None)
    assert 'no feedback page server is running' in api.OPS['feedback_stop']()
    assert 'not running' in api.OPS['feedback_list'](str(env['song']))


def test_errors_say_what_to_do(env, tmp_path):
    song = str(env['song'])
    with pytest.raises(api.OpError, match="round='r1'"):
        api.OPS['feedback_page'](song, 'r 1', questions=['q'])
    with pytest.raises(api.OpError, match='no file'):
        api.OPS['feedback_page'](song, 'r1', items=[{'path': str(tmp_path / 'missing.wav')}], questions=['q'])
    with pytest.raises(api.OpError, match='no questions'):
        api.OPS['feedback_page'](song, 'r1', questions=[])
    with pytest.raises(api.OpError, match='songs/<slug>'):
        api.OPS['feedback_page'](str(tmp_path / 'nowhere'), 'r1', questions=['q'])
    with pytest.raises(api.OpError, match='feedback_page'):
        api.OPS['feedback_read'](song, 'r9')
    with pytest.raises(api.OpError, match='another ismail server'):
        api.OPS['feedback_page'](song, 'r1', questions=['q'], port=8886, open=False)


def test_ports_skip_the_reserved_ones(env, monkeypatch):
    monkeypatch.setattr(FB, 'PORT0', 8886)
    monkeypatch.setattr(FB, 'port_free', lambda p: True)
    assert FB.pick_port(env['home']) == 8888


# ------------------------------------------------------------------ Send, SENT / STILL WRITING, feedback_wait (M246)

def _send(srv, rid='song-r1'):
    return _post(f'http://127.0.0.1:{srv.port}/f/{rid}/done', b'{}')


def test_feedback_page_has_a_send_button_and_its_reply_points_to_feedback_wait(env):
    srv = _serve(env)
    out = _page(env, open=False)
    assert "feedback_wait(project, round='r1')" in out and "never ask them to type 'done'" in out
    assert 'Send' in out and 'older one' not in out
    html = _get(f'http://127.0.0.1:{srv.port}/f/song-r1/')[2]
    assert b'sendbtn' in html and b"fetch('done'" in html
    assert FB.health(srv.port)['send'] is True


def test_feedback_read_says_still_writing_until_send_then_sent(env):
    srv = _serve(env)
    _page(env, open=False)
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert out.splitlines()[0] == 'STILL WRITING (last save none yet)'
    assert "not sent yet: don't act on these answers" in out
    _post(url + 'text', json.dumps({'q': 1, 'text': 'The kick, then'}).encode())
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert out.startswith('STILL WRITING (last save 20') and "don't act on these answers" in out
    at = _send(srv)['at']
    d = env['song'] / 'work' / 'feedback' / 'r1'
    assert FB.read_json(d / 'done.json')['sent'] == [at]
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert out.splitlines()[0] == f'SENT {at}' and "don't act" not in out and 'The kick, then' in out
    assert json.loads(_get(url + 'state')[2])['sent']['state'] == 'sent'
    assert f'SENT {at}' in api.OPS['feedback_list'](str(env['song']))
    # a late addition: the old send time stays, the round reads changed until Send again
    time.sleep(1.1)
    _post(url + 'text', json.dumps({'q': 2, 'text': 'and the pad is late'}).encode())
    st = FB.sent_state(d)
    assert st['state'] == 'changed' and st['at'] == at and st['changed_at']
    out = api.OPS['feedback_read'](str(env['song']), 'r1')
    assert out.startswith(f'SENT {at}, then CHANGED') and 'not sent again yet' in out
    assert json.loads(_get(url + 'state')[2])['sent']['state'] == 'changed'
    time.sleep(1.1)
    at2 = _send(srv)['at']
    assert FB.read_json(d / 'done.json')['sent'] == [at, at2]
    assert api.OPS['feedback_read'](str(env['song']), 'r1').splitlines()[0] == f'SENT {at2} (sent 2 times; this is the last)'


def test_a_voice_clip_after_send_counts_as_a_change(env):
    srv = _serve(env)
    _page(env, open=False)
    _send(srv)
    _post(f'http://127.0.0.1:{srv.port}/f/song-r1/voice?q=1&secs=1', WEBM, 'audio/webm')
    assert FB.sent_state(env['song'] / 'work' / 'feedback' / 'r1')['state'] == 'changed'


def test_feedback_wait_returns_on_send_and_at_timeout(env):
    srv = _serve(env)
    _page(env, open=False)
    url = f'http://127.0.0.1:{srv.port}/f/song-r1/'
    _post(url + 'text', json.dumps({'q': 1, 'text': 'half a tho'}).encode())
    t0 = time.time()
    out = api.OPS['feedback_wait'](str(env['song']), 'r1', timeout_s=1)
    assert 0.9 <= time.time() - t0 < 5
    assert out.startswith('STILL WRITING') and 'half a tho' in out and "don't act on these answers" in out
    assert "feedback_wait(project, round='r1') again keeps waiting" in out
    got = {}
    th = threading.Thread(target=lambda: got.setdefault('out', api.OPS['feedback_wait'](str(env['song']), 'r1',
                                                                                         timeout_s=20)))
    th.start()
    time.sleep(0.8)
    _post(url + 'text', json.dumps({'q': 1, 'text': 'half a thought, now whole'}).encode())
    at = _send(srv)['at']
    th.join(15)
    out = got['out']
    assert out.startswith(f'SENT {at}') and 'now whole' in out and f"after='{at}'" in out
    # already sent: at once; after= that send waits for a later one
    t0 = time.time()
    assert api.OPS['feedback_wait'](str(env['song']), 'r1', timeout_s=20).startswith('SENT')
    assert time.time() - t0 < 2
    out = api.OPS['feedback_wait'](str(env['song']), 'r1', timeout_s=0.6, after=at)
    assert f"again after {at}" in out and f"after='{at}'" in out
    with pytest.raises(api.OpError, match='not a time'):
        api.OPS['feedback_wait'](str(env['song']), 'r1', after='soon')
    with pytest.raises(api.OpError, match='rounds there: r1'):
        api.OPS['feedback_wait'](str(env['song']), 'r9', timeout_s=0)


def test_feedback_wait_says_when_the_page_server_is_too_old_for_send(env, monkeypatch):
    srv = _serve(env)
    _page(env, open=False)
    real = FB.health
    monkeypatch.setattr(FB, 'health', lambda port, timeout=1.5: {k: v for k, v in (real(port, timeout) or {}).items()
                                                                if k != 'send'} or None)
    out = api.OPS['feedback_wait'](str(env['song']), 'r1', timeout_s=0)
    assert 'older one' in out and 'feedback_stop()' in out
    assert 'older one' in _page(env, open=False)


def test_a_songs_proj_writes_its_rounds_in_the_song_folder(env):
    """A first-session test: proj/ is regenerated by the song's build, which took the answers with it."""
    _serve(env)
    song = env['song']
    proj = song / 'proj'
    api.project_new(str(proj), bpm=120, length_bars=2)
    (song / 'ref').mkdir()
    out = api.OPS['feedback_page'](str(proj), 'r2', items=[str(song / 'take.wav')], questions=['How is it?'],
                                   open=False)
    d = song / 'work' / 'feedback' / 'r2'
    assert (d / 'manifest.json').is_file() and not (proj / 'work' / 'feedback' / 'r2').exists()
    assert 'song folder, which a rebuild of proj/ leaves alone' in out and '/f/song-r2/' in out
    assert 'nothing has come in yet' in api.OPS['feedback_read'](str(proj), 'r2')
    assert 'r2:' in api.OPS['feedback_list'](str(proj))
    # a round made in proj/ before the move stays where it is
    FB.build(proj, 'old', [], ['Still there?'])
    assert FB.round_dir(proj, 'old').is_dir()
    assert 'nothing has come in yet' in api.OPS['feedback_read'](str(proj), 'old')
    out = api.OPS['feedback_page'](str(proj), 'old', questions=['Still there?'], open=False)
    assert 'in proj/, which a rebuild replaces' in out and not (song / 'work' / 'feedback' / 'old').exists()


def test_the_page_ends_with_the_made_with_footer(env):
    """ledger:M254: a page a person may pass on says what made the song, with no names or paths."""
    srv = _serve(env)
    song = env['song']
    proj = song / 'proj'
    api.project_new(str(proj), bpm=120, length_bars=2)
    api.track_add(str(proj), 'my_private_name', instrument={'type': 'synth'})
    api.OPS['feedback_page'](str(proj), 'r3', items=[str(song / 'take.wav')], questions=['How is it?'], open=False)
    url = f'http://127.0.0.1:{srv.port}/f/song-r3/'
    man = json.loads(_get(url + 'manifest')[2])
    assert man['footer']['link'].startswith('https://') and 'ismail' in man['footer']['lead']
    assert man['footer']['how'] == '1 part, synthesized (designed).'
    assert 'my_private_name' not in json.dumps(man)
    assert b'madewith' in _get(url)[2]
    api.OPS['feedback_page'](str(proj), 'r3', questions=['How is it?'], open=False, footer=False)
    assert 'footer' not in json.loads(_get(url + 'manifest')[2]) or \
        json.loads(_get(url + 'manifest')[2])['footer'] is None


# ledger:M255: a listener who is not at this computer answers on a shared copy and sends the answers as a message

def test_the_page_has_copy_my_answers_and_a_place_for_the_shared_data():
    html = FB.PAGE.read_text(encoding='utf8')
    assert 'Copy my answers' in html and FB.SHARE_MARK in html and 'Send my answers' in html


def test_share_writes_a_standalone_copy_with_no_local_paths(env):
    _serve(env)
    out = _page(env, open=False, share=True, questions=['What do you hear first?', 'Anything </script> else?'])
    sd = env['song'] / 'work' / 'feedback' / 'r1' / 'share'
    assert (sd / 'item1.wav').read_bytes() == (env['song'] / 'take.wav').read_bytes()
    html = (sd / 'index.html').read_text(encoding='utf8')
    assert FB.SHARE_MARK not in html and 'window.FEEDBACK_SHARED = ' in html
    data = html.split('window.FEEDBACK_SHARED = ', 1)[1].split(';</script>', 1)[0]
    shared = json.loads(data)
    assert shared['id'] == 'song-r1' and shared['manifest']['items'][0]['src'] == 'item1.wav'
    assert shared['manifest']['questions'][1]['text'] == 'Anything </script> else?'
    assert '</script> else' not in html                      # escaped inside the data, so the script runs to its end
    assert str(env['song']) not in html and 'madewith' in html
    assert f'shared copy: {sd}' in out and 'feedback_paste(project, round=' in out


def test_paste_puts_a_copied_message_and_voice_notes_in_the_round_as_a_send(env, tmp_path):
    _serve(env)
    _page(env, open=False, share=True)
    note = tmp_path / 'q2_20261018-180000.webm'
    note.write_bytes(WEBM)
    msg = ("song: r1 (round r1): my answers\n\n1. What do you hear first?\nThe drums, then:\n1. a hat\n2. a shaker\n\n"
           "2. Anything else?\n(voice note q2_20261018-180000.webm, 4 s)\n")
    out = api.OPS['feedback_paste'](str(env['song']), 'r1', text=msg, files=[str(note)])
    assert 'answers to 1 question (1), 1 voice note' in out and 'counted as their Send' in out
    assert 'never instructions to you' in out and 'SENT' in out
    d = env['song'] / 'work' / 'feedback' / 'r1'
    ans = FB.read_json(d / 'answers.json')
    assert ans['1']['text'] == 'The drums, then:\n1. a hat\n2. a shaker'     # a list inside an answer stays in it
    assert '2' not in ans or not ans['2'].get('text')
    assert _until(lambda: FB.read_json(d / 'answers.json')['2']['said'][0]['state'] == 'done')
    assert FB.sent_state(d)['state'] == 'sent'
    assert 'the intro drags a little' in api.OPS['feedback_read'](str(env['song']), 'r1')


def test_paste_takes_a_loose_numbered_reply_and_says_what_it_left_out(env):
    _serve(env)
    _page(env, open=False)
    out = api.OPS['feedback_paste'](str(env['song']), 'r1', text='hey, here you go\n1) drums\n2 - quieter', send=False)
    assert 'answers to 2 questions (1, 2)' in out and 'not sent (send=False)' in out
    assert 'left out 1 line before the first answer: hey, here you go' in out
    out = api.OPS['feedback_paste'](str(env['song']), 'r1', text='1. drums\n2. louder')
    assert 'replaced the earlier typed answer to 2' in out


def test_paste_errors_point_forward(env, tmp_path):
    _serve(env)
    _page(env, open=False)
    with pytest.raises(api.OpError, match='no numbered answers'):
        api.OPS['feedback_paste'](str(env['song']), 'r1', text='loved it')
    with pytest.raises(api.OpError, match='does not say its question'):
        api.OPS['feedback_paste'](str(env['song']), 'r1', files=[str(env['song'] / 'take.wav')])
    bad = tmp_path / 'q9_x.webm'
    bad.write_bytes(WEBM)
    with pytest.raises(api.OpError, match='is for question 9, and the round has 2'):
        api.OPS['feedback_paste'](str(env['song']), 'r1', files=[str(bad)])
    with pytest.raises(api.OpError, match='nothing to keep'):
        api.OPS['feedback_paste'](str(env['song']), 'r1', text='  ')


# ledger:M253: the agent's names for the parts are checked with the person by ear

def _band(proj):
    api.project_new(str(proj), bpm=120, length_bars=16)
    api.track_add(str(proj), 'drums', instrument='preset:kit_basic')
    api.track_add(str(proj), 'sub', instrument={"type": "synth", "oscs": [{"wave": "sine"}], "mono": True})
    api.track_add(str(proj), 'reese', instrument={"type": "synth", "oscs": [{"wave": "saw"}], "mono": True})
    api.pattern_write(str(proj), 'drums', 1, {"C1": "x...x...x...x..."}, repeat=16)
    api.notes_write(str(proj), 'sub', 1, "0 E1 4", repeat=16)
    api.notes_write(str(proj), 'reese', 9, "0 E2 2; 2 G2 2", repeat=4)          # bars 9-12 only
    api.OPS['stem_map_set'](str(proj), {'drums': 'drums', 'sub': 'bass', 'reese': 'bass'})
    api.render(str(proj), stems=True, cache=False)


def test_parts_block_names_come_back_and_a_note_that_could_mean_two_parts_is_flagged(env, tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_LEXICON', str(tmp_path / 'lexicon.jsonl'))
    srv = _serve(env)
    proj = env['song'] / 'proj'
    _band(proj)
    out = api.OPS['feedback_page'](str(proj), 'r5', parts=True, questions=['Which part is too loud?'], open=False,
                                   share=True)
    assert 'parts block: 3 parts' in out and 'reese bars 9-12' in out, out
    d = env['song'] / 'work' / 'feedback' / 'r5'
    man = FB.read_json(d / 'manifest.json')
    assert [p['track'] for p in man['parts']] == ['drums', 'sub', 'reese']
    assert all(os.path.isfile(p['solo']) and os.path.isfile(p['mix']) for p in man['parts'])
    assert (d / 'share' / 'part3-solo.wav').is_file()
    url = f'http://127.0.0.1:{srv.port}/f/song-r5/'
    pub = json.loads(_get(url + 'manifest')[2])
    assert pub['parts'][2] == {'n': 3, 'track': 'reese', 'bars': [9, 12], 'solo': 'part/3/solo', 'mix': 'part/3/mix'}
    assert _get(url + 'part/3/solo')[1]['Content-Type'] == 'audio/wav'
    assert _post(url + 'name', json.dumps({'track': 'reese', 'text': 'the answer bass'}).encode())['ok']
    with pytest.raises(urllib.error.HTTPError):
        _post(url + 'name', json.dumps({'track': 'nope', 'text': 'x'}).encode())
    _post(url + 'text', json.dumps({'q': 1, 'text': 'the bass is way too loud'}).encode())
    read = api.OPS['feedback_read'](str(proj), 'r5')
    assert 'reese -> "the answer bass"' in read and 'not named yet: drums, sub' in read
    assert '1. says "bass", and 2 parts could be it (reese, sub): ask which' in read, read
    _post(url + 'text', json.dumps({'q': 1, 'text': 'the answer bass is way too loud'}).encode())
    assert 'parts could be it' not in api.OPS['feedback_read'](str(proj), 'r5')
    # the name is confirmed only by what they typed there
    ok = api.lexicon_note(str(env['song']), said='the answer bass', track='reese', heard='r5')
    assert 'the part reese, confirmed by ear (feedback r5)' in ok
    with pytest.raises(api.OpError, match="they called 'reese' 'the answer bass', not 'the talker'"):
        api.lexicon_note(str(env['song']), said='the talker', track='reese', heard='r5')
    with pytest.raises(api.OpError, match="has no name for 'sub' from them"):
        api.lexicon_note(str(env['song']), said='the sub', track='sub', heard='r5')
    assert 'not confirmed by them yet' in api.lexicon_note(str(env['song']), said='the low one', track='sub')


def test_paste_brings_names_for_the_parts(env):
    _serve(env)
    proj = env['song'] / 'proj'
    _band(proj)
    api.OPS['feedback_page'](str(proj), 'r6', parts=['sub', 'reese'], questions=['Anything?'], open=False)
    msg = ("song: r6 (round r6): my answers\n\n1. Anything?\nmore drums\n\nNames for the parts\n- sub: the deep one\n"
           "- reese: (no name)\n")
    out = api.OPS['feedback_paste'](str(proj), 'r6', text=msg)
    assert 'answers to 1 question (1), names for 1 part' in out
    d = env['song'] / 'work' / 'feedback' / 'r6'
    assert FB.names(d) == {'sub': 'the deep one'} and FB.read_json(d / 'answers.json')['1']['text'] == 'more drums'


def test_parts_errors_point_forward(env):
    _serve(env)
    with pytest.raises(api.OpError, match='needs a song'):
        _page(env, open=False, parts=True)
    proj = env['song'] / 'proj'
    api.project_new(str(proj), bpm=120, length_bars=2)
    api.track_add(str(proj), 'pad', instrument={'type': 'synth'})
    with pytest.raises(api.OpError, match="no track 'nope'; the song has pad"):
        api.OPS['feedback_page'](str(proj), 'r7', parts=['nope'], questions=['?'], open=False)
    with pytest.raises(api.OpError, match='latest render'):
        api.OPS['feedback_page'](str(proj), 'r7', parts=True, questions=['?'], open=False)


def test_a_note_that_names_a_quality_comes_back_as_its_measure_step(env):
    """ledger:M252: 'muddy at bars 9-12' reads back with the window filled in and the tool for that kind of word."""
    _serve(env)
    proj = env['song'] / 'proj'
    _band(proj)
    api.OPS['feedback_page'](str(proj), 'r8', questions=['What bothers you?', 'Anything else?'], open=False)
    msg = ("song: r8 (round r8): my answers\n\n1. What bothers you?\nthe reese gets muddy in bars 9-12\n\n"
           "2. Anything else?\nlove it\n")
    out = api.OPS['feedback_paste'](str(proj), 'r8', text=msg)
    assert 'measure before you change anything' in out, out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith('1. "muddy"'))
    assert "(tone): sound_zoom on source='render', bars=[9, 12], the part reese" in line, line
    assert not any(ln.strip().startswith('2. "') for ln in out.splitlines())
    assert 'ear_pass' not in out                                      # no reference on this song


def test_measure_words_and_windows():
    from ismail.feedback import measure as M
    assert M.qualities('Too loud, and the lead sounds fake.') == [('level', 'too loud'), ('sound', 'fake')]
    assert M.qualities('dark and moody, I love it') == []            # 'dark' alone is a taste, not a fault
    assert M.qualities('a bit too dark') == [('tone', 'too dark')]
    assert M.window('around 1:20 to 1:30') == {'seconds': [78.0, 92.0]}
    assert M.window('bar 5') == {'bars': [5, 5]} and M.window('at 45 seconds') == {'seconds': [43.0, 47.0]}
    assert M.window('no time here') is None
    items = [{'label': 'draft 3', 'path': '/x/draft3.mp3'}]
    step = M.steps(2, 'harsh at 0:45', items, {'reference': {'file': 'r.wav'}})
    assert step == ["2. \"harsh\" (tone): sound_zoom on source='/x/draft3.mp3', seconds=[43.0, 47.0] (in draft 3), "
                    "yours and the reference's same window side by side; region_measure on the band that differs"]
    two = items + [{'label': 'draft 2', 'path': '/x/draft2.mp3'}]
    assert "seconds=[43.0, 47.0] in the clip they meant ('draft 3', 'draft 2')" in M.steps(1, 'harsh at 0:45', two)[0]
