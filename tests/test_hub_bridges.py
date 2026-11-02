"""The hub, phase 2 (ledger:M429): the owner's phone page and VR stage read into the hub log, read only; presence
across places; hub_say to the owner choosing the phone when that is where they were last; and a phone panel the hub
opened answering as a press or form for the agent that sent it. No network: a temp phone inbox, temp scene event
files, and a fake stage server that must never be asked anything."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ismail.api import OpError
from ismail.hub import bridges as B, config as C, ops, people as P, server


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.03)
    return fn()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'hub')
    monkeypatch.setenv('ISMAIL_HUB_OWNER', 'nate')
    monkeypatch.setattr(B, 'POLL_S', 0.03)
    monkeypatch.setattr(B, 'DOWN_S', 0.05)
    C.HOME.mkdir(parents=True)
    h = server.Hub(0)
    server.Handler.hub = h
    srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    srv.daemon_threads = True
    h.port = srv.server_address[1]
    (C.HOME / 'server.json').write_text(json.dumps({'port': h.port, 'pid': 0}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield h
    srv.shutdown()
    h.shutdown()


def append(path, *rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


def logged(h, venue, kinds=('msg', 'press', 'form', 'event')):
    return [d for d in h.log.lines if d['venue'] == venue and d['kind'] in kinds]


def phone_line(n, kind, **kw):
    return {'n': n, 'ts': '2026-11-02T10:00:00', 'kind': kind, **kw}


def stage_ev(i, type_, **kw):
    return {'id': i, 'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'type': type_, **kw}


# ------------------------------------------------------------------ the phone inbox, read only

def test_the_phone_inbox_is_copied_into_the_log_as_the_owners_word_and_left_as_it_was(hub, tmp_path):
    inbox = tmp_path / 'phone' / 'inbox.jsonl'
    append(inbox, phone_line(1, 'voice_text', text='from before the hub started'))
    hub.start_bridges(['phone'], phone=lambda: inbox)
    wait_for(lambda: hub.bridges['phone'].state == 'reading')
    append(inbox, phone_line(2, 'voice_text', id='v9', text='make it darker', **{'for': 'Live DJ'}, panel='p1'),
           phone_line(3, 'tap', what='skip', means='next piece', sid='s1', heard={'of': 'bar 9 of 16'}),
           phone_line(4, 'page', what='open'), phone_line(5, 'voice', id='v10', state='transcribing'),
           phone_line(6, 'alarm', what='watcher_down', agent='Maintainer'),
           phone_line(7, 'answer', id='q77', answer='Keep', **{'for': 'Film'}))
    before = inbox.read_bytes()
    got = wait_for(lambda: len(logged(hub, 'phone')) >= 3 and logged(hub, 'phone'))
    msg, tap, ans = got
    assert (msg['kind'], msg['text'], msg['person'], msg['trust'], msg['use']) == ('msg', 'make it darker', 'nate',
                                                                                   'owner', 'word')
    assert (msg['src_n'], msg['src_kind'], msg['id'], msg['for'], msg['panel']) == (2, 'voice_text', 'v9', 'Live DJ',
                                                                                    'p1')
    assert (tap['kind'], tap['type'], tap['what'], tap['heard'], tap['src_n']) == ('event', 'tap', 'skip',
                                                                                   'bar 9 of 16', 3)
    assert 'sid' not in tap
    assert (ans['kind'], ans['src_kind'], ans['answer'], ans['for']) == ('event', 'answer', 'Keep', 'Film')
    assert inbox.read_bytes() == before                     # nothing written, claimed or marked there
    assert set(os.listdir(inbox.parent)) == {'inbox.jsonl'}
    assert 'phone' in json.loads(ops.hub_presence('nate'))['seconds_ago']['nate']
    # watchers see them; mine=True leaves out what was addressed to another agent
    seen = [d['text'] for d in hub.op_listen('Maintainer', since=0, wait=0, places=['phone'])['lines']
            if d['kind'] == 'msg']
    assert seen == ['make it darker']
    assert hub.op_listen('Maintainer', since=0, wait=0, places=['phone'], mine=True)['lines'] == [tap]
    assert len(hub.op_listen('Live DJ', since=0, wait=0, places=['phone'], mine=True)['lines']) == 2


def test_the_phone_bridge_waits_for_a_missing_inbox_and_resumes_where_it_stopped(hub, tmp_path):
    inbox = tmp_path / 'phone' / 'inbox.jsonl'
    hub.start_bridges(['phone'], phone=lambda: inbox)
    assert wait_for(lambda: hub.bridges['phone'].state == 'waiting')
    status = [d for d in hub.log.lines if d['kind'] == 'status' and d['venue'] == 'phone']
    assert status and 'no phone inbox yet' in status[-1]['text']
    assert 'phone: read only, waiting' in ops.hub_status()
    append(inbox, phone_line(1, 'text', text='first ever line'))
    assert wait_for(lambda: [d['text'] for d in logged(hub, 'phone')] == ['first ever line'])
    hub.bridges.pop('phone').stop()
    time.sleep(0.1)
    with open(inbox, 'a', encoding='utf8') as f:                # while the hub is down, and half a line
        f.write(json.dumps(phone_line(2, 'text', text='said while the hub was down')) + '\n{"n": 3, "ki')
    hub.start_bridges(['phone'], phone=lambda: inbox)
    assert wait_for(lambda: len(logged(hub, 'phone')) == 2)
    with open(inbox, 'a', encoding='utf8') as f:
        f.write('nd": "text", "text": "the rest of it"}\n')
    assert wait_for(lambda: [d['text'] for d in logged(hub, 'phone')][-1] == 'the rest of it')
    assert [d['src_n'] for d in logged(hub, 'phone')] == [1, 2, 3]


# ------------------------------------------------------------------ the stage's event files, read only

class NeverAsk(BaseHTTPRequestHandler):
    asked = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        NeverAsk.asked.append(self.path)
        self.send_response(500)
        self.end_headers()

    do_POST = do_GET


def test_vr_voice_notes_come_in_from_the_scene_files_and_the_stage_server_is_never_asked(hub, tmp_path,
                                                                                          monkeypatch):
    fake = ThreadingHTTPServer(('127.0.0.1', 0), NeverAsk)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    reg, scenes = tmp_path / 'registry', tmp_path / 'song' / 'scenes'
    reg.mkdir()
    (reg / f'{fake.server_address[1]}.json').write_text(json.dumps({'port': fake.server_address[1],
                                                                    'pid': os.getpid(), 'scenes': str(scenes)}))
    (reg / '1.json').write_text(json.dumps({'port': 1, 'pid': 2 ** 30, 'scenes': str(tmp_path / 'gone')}))
    (reg / '2.json').write_text(json.dumps({'port': 2, 'pid': os.getpid(), 'scenes': str(tmp_path / '.scratch')}))
    monkeypatch.setenv('ISMAIL_STAGE_REGISTRY', str(reg))
    assert B.stage_dirs() == [scenes]                       # the dead one's note stays: the stage cleans its own
    assert sorted(os.listdir(reg)) == sorted(['1.json', '2.json', f'{fake.server_address[1]}.json'])
    room = scenes / 'club' / 'live' / 'events.jsonl'
    append(room, stage_ev(1, 'voice_message', text='an old note'))
    hub.start_bridges(['stage'])
    assert wait_for(lambda: hub.bridges['stage'].state == 'reading')
    append(room, stage_ev(2, 'voice_in', file='a.webm', seconds=3, page=1),
           stage_ev(3, 'voice_message', text='louder kick here', file='a.webm'),
           stage_ev(4, 'behaviour_error', error='x'), stage_ev(5, 'perform_clip', text='', words=[]),
           stage_ev(6, 'headset', state='entered VR', page=1))
    got = wait_for(lambda: len(logged(hub, 'stage')) >= 2 and logged(hub, 'stage'))
    note, head = got
    assert (note['kind'], note['text'], note['scene'], note['src_n'], note['src_kind']) == (
        'msg', 'louder kick here', 'club', 3, 'voice_message')
    assert (note['person'], note['trust'], note['use']) == ('nate', 'owner', 'word')
    assert (head['kind'], head['type'], head['state']) == ('event', 'headset', 'entered VR') and 'page' not in head
    # a scene that starts after the hub: its new lines come in too
    append(scenes / 'stage2' / 'live' / 'events.jsonl', stage_ev(1, 'voice_message', text='in the new room'))
    assert wait_for(lambda: logged(hub, 'stage')[-1].get('text') == 'in the new room')
    assert 'stage' in json.loads(ops.hub_presence('nate'))['seconds_ago']['nate']
    assert NeverAsk.asked == []
    fake.shutdown()


# ------------------------------------------------------------------ sending to the owner where they are

@pytest.fixture
def phone_out(monkeypatch):
    from ismail.phone import ops as phone
    sent = []
    monkeypatch.setattr(B.PhonePlace, 'up', staticmethod(lambda: True))
    monkeypatch.setattr(phone, 'phone_say', lambda text, **k: sent.append(('say', text, k)) or 'ok')
    monkeypatch.setattr(phone, 'phone_panel_show', lambda **k: sent.append(('panel', k)) or 'ok')
    monkeypatch.setattr(phone, 'phone_offer', lambda path, **k: sent.append(('offer', path, k)) or 'ok')
    return sent


FORM = {'title': 'Change it', 'inputs': [{'id': 'tempo', 'kind': 'choice', 'label': 'Tempo',
                                          'options': ['slower', 'same', 'faster']}]}


def test_say_to_the_owner_goes_where_they_were_last_and_the_panel_answer_comes_back(hub, tmp_path, phone_out):
    ops.hub_place('practice', platform='fake', only=[])
    a = wait_for(lambda: hub.adapters.get('practice') if hub.adapters.get('practice') and
                 hub.adapters['practice'].state == 'reading' else None)
    P.link('nate', 'fake:nate_acct')
    inbox = tmp_path / 'phone' / 'inbox.jsonl'
    append(inbox, phone_line(1, 'text', text='old'))
    scenes = tmp_path / 'scenes'
    hub.start_bridges(['phone', 'stage'], phone=lambda: inbox, stage=lambda: [scenes])
    wait_for(lambda: hub.bridges['phone'].state == 'reading')
    a.person_says('nate_acct', 'hi from the bot')
    assert 'sent on practice' in ops.hub_say(to='nate', text='on the bot', who='Maintainer')
    time.sleep(0.05)
    append(inbox, phone_line(2, 'voice_text', text='now on the phone'))
    wait_for(lambda: logged(hub, 'phone'))
    out = ops.hub_say(to='nate', text='Two takes\nwhich one?', buttons=['Keep 1', {'id': 'v2', 'label': 'Vary 2'}],
                      form=FORM, who='Maintainer')
    assert 'sent on phone' in out and 'the phone page: a panel (h' in out and 'VR' not in out
    kind, k = phone_out[-1]
    assert kind == 'panel' and k['title'] == 'Change it' and k['buttons'] == ['Keep 1', 'Vary 2']
    assert k['inputs'][0]['id'] == 'tempo' and k['sender'] == 'Maintainer'
    pid = k['panel_id']
    append(inbox, phone_line(3, 'answer', id=pid, answer='Vary 2', values={'tempo': 'slower'},
                             **{'for': 'Maintainer'}))
    mine = wait_for(lambda: hub.op_listen('Maintainer', since=0, wait=0, kinds=['press', 'form'])['lines'])
    assert [(d['kind'], d['button'], d['label'], d['values'], d['venue']) for d in mine] == [
        ('form', 'v2', 'Vary 2', {'tempo': 'slower'}, 'phone')]
    assert hub.op_listen('Film', since=0, wait=0, kinds=['press', 'form'])['lines'] == []
    # a caption and files; a reply to a phone line goes to the phone
    mp3 = tmp_path / 'take.mp3'
    mp3.write_bytes(b'0' * 100)
    n = logged(hub, 'phone')[0]['n']
    out = ops.hub_say(reply_to=n, text='here it is', files=[str(mp3)], who='Maintainer')
    assert 'a caption, 1 download card(s)' in out
    assert [x[0] for x in phone_out[-2:]] == ['say', 'offer'] and phone_out[-1][2]['label'] == 'take.mp3'
    # in VR now: the phone still gets it, and the answer says VR is read only
    append(scenes / 'club' / 'live' / 'events.jsonl', stage_ev(1, 'voice_message', text='in the headset'))
    vr = wait_for(lambda: logged(hub, 'stage'))
    out = ops.hub_say(to='nate', text='still here', who='Maintainer')
    assert 'VR: read only in phase 2' in out
    with pytest.raises(OpError, match='VR: read only in phase 2'):
        ops.hub_say(reply_to=vr[0]['n'], text='hello in there')
    with pytest.raises(OpError, match='VR: read only'):
        ops.hub_say(to='stage', text='hello')
    seen = json.loads(ops.hub_presence('nate'))['seconds_ago']['nate']
    assert set(seen) == {'practice', 'phone', 'stage'}
    # back on the bot: it wins again
    a.person_says('nate_acct', 'back on the bot')
    assert 'sent on practice' in ops.hub_say(to='nate', text='there you are')


def test_with_the_phone_down_the_owner_is_reached_on_the_bot_and_never_on_the_phone(hub, tmp_path, phone_out,
                                                                                      monkeypatch):
    monkeypatch.setattr(B.PhonePlace, 'up', staticmethod(lambda: False))
    inbox = tmp_path / 'inbox.jsonl'
    hub.start_bridges(['phone'], phone=lambda: inbox)
    append(inbox, phone_line(1, 'text', text='hi'))
    wait_for(lambda: logged(hub, 'phone'))
    with pytest.raises(OpError, match='no connected place'):
        ops.hub_say(to='nate', text='anyone?')
    assert phone_out == []


def test_a_not_now_on_a_hub_panel_comes_back_as_a_dismissed_press(hub, tmp_path, phone_out):
    inbox = tmp_path / 'inbox.jsonl'
    hub.start_bridges(['phone'], phone=lambda: inbox)
    ops.hub_say(to='phone', text='Pick', buttons=['A', 'B'], who='Film')
    pid = phone_out[-1][1]['panel_id']
    append(inbox, phone_line(1, 'answer', id=pid, answer=None, dismissed=True, **{'for': 'Film'}))
    got = wait_for(lambda: hub.op_listen('Film', since=0, wait=0, kinds=['press'])['lines'])
    assert got[0]['dismissed'] is True and got[0].get('button') is None


def test_bridges_follow_the_setting(monkeypatch):
    monkeypatch.setenv('ISMAIL_HUB_BRIDGES', 'phone')
    assert B.enabled() == ['phone']
    monkeypatch.delenv('ISMAIL_HUB_BRIDGES')
    assert B.enabled() == ['phone', 'stage']
