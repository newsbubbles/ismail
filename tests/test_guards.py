"""The checks ops run before they show text, ask a yes/no question or open a mic (ismail.guards). Every value here
is made up; the local files live in tmp dirs set through the same env vars the person's own files use."""
import datetime
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ismail import api, guards
from ismail import listen as LI
from ismail.phone import ops as P
from ismail.phone import server as S
from ismail.stage import link
from ismail.stage import ops as SO
from ismail.stage import ops_page as SP

GRANT = {'id': 'G1', 'date': '2026-01-01', 'topic': 'merges', 'keywords': ['merge', 'merging'],
         'says': 'merge reviewed pull requests without asking'}


class FakePhone(BaseHTTPRequestHandler):
    sent = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        FakePhone.sent.append(json.loads(self.rfile.read(n)))
        b = json.dumps({'ok': True, 'result': 'shown on the phone'}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture
def phone(tmp_path, monkeypatch):
    """A phone server that takes every op and says it was shown; the inbox is in tmp."""
    home = tmp_path / 'phone'
    home.mkdir()
    monkeypatch.setattr(S, 'HOME', home)
    FakePhone.sent = []
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), FakePhone)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    (home / 'server.json').write_text(json.dumps({'port': httpd.server_address[1], 'pid': os.getpid()}))
    yield home
    httpd.shutdown()


@pytest.fixture
def terms(tmp_path, monkeypatch):
    p = tmp_path / 'local' / 'private_terms.txt'
    p.parent.mkdir(exist_ok=True)
    p.write_text('# places and names, one per line\nExampleton\nNew Example City\n', encoding='utf8')
    monkeypatch.setenv(guards.PRIVATE_TERMS_ENV, str(p))
    return p


@pytest.fixture
def grants(tmp_path, monkeypatch):
    p = tmp_path / 'local' / 'standing_grants.json'
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps([GRANT]), encoding='utf8')
    monkeypatch.setenv(guards.GRANTS_ENV, str(p))
    return p


# ------------------------------------------------------------------ 1. nothing personal on a page

def test_a_private_term_is_refused_with_the_next_step(phone, terms):
    for text in ('back in Exampleton tonight', 'EXAMPLETON calling', 'from new  example city'):
        with pytest.raises(api.OpError) as e:
            P.phone_say(text)
        assert 'names a place or personal detail' in str(e.value) and 'Rephrase without it' in str(e.value)
    assert FakePhone.sent == []                               # nothing reached the page


def test_text_without_a_term_goes_through(phone, terms):
    assert P.phone_say('dropping the pads at bar 9') == 'shown on the phone'
    assert P.phone_say('Exampletonian is not a whole-word match') == 'shown on the phone'
    assert len(FakePhone.sent) == 2


def test_panels_buttons_and_inputs_are_scanned(phone, terms):
    with pytest.raises(api.OpError):
        P.phone_panel_show(title='Pick one', buttons=['here', 'Exampleton'])
    with pytest.raises(api.OpError):
        P.phone_panel_show(title='Pick one', inputs=[{'id': 'c', 'kind': 'choice', 'options': ['Exampleton', 'a']}])
    with pytest.raises(api.OpError):
        P.phone_now(now='live from Exampleton')
    assert FakePhone.sent == []


def test_the_git_email_is_refused_anywhere_in_the_text(phone, terms, monkeypatch):
    monkeypatch.setattr(guards, '_git_email', lambda: 'person@example.com')
    with pytest.raises(api.OpError) as e:
        P.phone_say('write to Person@Example.com.')
    assert 'person@example.com' in str(e.value)
    with pytest.raises(api.OpError):
        P.phone_say('mailto:xperson@example.comx')


def test_no_terms_file_means_no_scan_and_a_line_saying_how_to_turn_it_on(phone, tmp_path, monkeypatch):
    monkeypatch.setenv(guards.PRIVATE_TERMS_ENV, str(tmp_path / 'missing.txt'))
    out = P.phone_say('back in Exampleton tonight')
    assert out.startswith('shown on the phone') and 'personal-data scan is off' in out
    assert 'missing.txt' in out and guards.PRIVATE_TERMS_ENV in out
    monkeypatch.setattr(guards, '_git_email', lambda: 'person@example.com')
    with pytest.raises(api.OpError):                          # the email is checked even then
        P.phone_say('person@example.com')


def test_stage_captions_panels_and_markers_are_scanned(terms, monkeypatch):
    sent = []
    monkeypatch.setattr(link, 'send', lambda scene, ctype, fields, timeout=30: sent.append(ctype) or 'done')
    for call in (lambda: SP.stage_say('room', 'hello from Exampleton'),
                 lambda: SP.stage_panel_show('room', title='Exampleton', buttons=['ok']),
                 lambda: SP.stage_marker_set('room', 'm1', [0, 0, 0], label='Exampleton'),
                 lambda: link.page_cmd('room', 'cue', {'text': 'in exampleton'})):
        with pytest.raises(api.OpError) as e:
            call()
        assert 'the stage never says where the person is' in str(e.value)
    with pytest.raises(api.OpError):
        SO.stage_busy('helper', 'driving to Exampleton')
    assert sent == []
    assert SP.stage_say('room', 'hello') == 'done' and sent == ['say']


# ------------------------------------------------------------------ 2. standing grants

def test_a_granted_yes_no_panel_is_refused_with_the_grant(phone, grants):
    with pytest.raises(api.OpError) as e:
        P.phone_panel_show(title='Merge #12?', text='the tests pass', buttons=['Yes', 'No'])
    msg = str(e.value)
    assert 'a standing grant covers this (G1, 2026-01-01: merge reviewed pull requests without asking)' in msg
    assert "override_grant='<why it doesn't fit>'" in msg
    with pytest.raises(api.OpError):
        P.phone_panel_show(title='Merging #12', buttons=['Approve', 'Deny'])
    with pytest.raises(api.OpError):
        P.phone_ask('merge #12 now?')
    assert FakePhone.sent == []


def test_a_panel_that_is_not_yes_no_or_not_granted_goes_through(phone, grants):
    assert P.phone_panel_show(title='Which merge first?', buttons=['#12', '#13']) == 'shown on the phone'
    assert P.phone_panel_show(title='Merge done', text='#12 is in', buttons=['OK']) == 'shown on the phone'
    assert P.phone_panel_show(title='Restart the room?', buttons=['Yes', 'No']) == 'shown on the phone'
    assert P.phone_say('merged #12, the tests pass') == 'shown on the phone'


def test_a_caption_that_asks_a_granted_question_is_refused(phone, grants):
    with pytest.raises(api.OpError) as e:
        P.phone_say('should I merge #12?')
    assert 'G1' in str(e.value)
    with pytest.raises(api.OpError):
        P.phone_say('want me to go ahead with merging?')


def test_the_stage_ask_is_checked_too(grants, monkeypatch):
    monkeypatch.setattr(link, 'send', lambda scene, ctype, fields, timeout=30: 'done')
    with pytest.raises(api.OpError):
        SP.stage_ask('room', 'merge #12?')
    with pytest.raises(api.OpError):
        SP.stage_panel_show('room', title='Merge #12?', buttons=['yes', 'no'])


def test_an_override_needs_a_reason_and_is_logged_locally(phone, grants):
    with pytest.raises(api.OpError) as e:
        P.phone_panel_show(title='Merge #12?', buttons=['Yes', 'No'], override_grant='   ')
    assert 'needs the reason' in str(e.value)
    out = P.phone_panel_show(title='Merge #12?', buttons=['Yes', 'No'],
                             override_grant='#12 touches the release, which the grant leaves out')
    assert out.startswith('shown on the phone') and 'standing grant G1 set aside' in out
    log = grants.parent / 'grant_overrides.jsonl'
    rows = [json.loads(x) for x in log.read_text(encoding='utf8').splitlines()]
    assert len(rows) == 1 and rows[0]['grant'] == 'G1' and rows[0]['op'] == 'phone_panel_show'
    assert rows[0]['why'] == '#12 touches the release, which the grant leaves out' and 'Merge #12?' in rows[0]['question']


def test_an_override_is_not_logged_for_text_that_cannot_go(phone, grants, terms):
    with pytest.raises(api.OpError) as e:
        P.phone_panel_show(title='Merge #12 from Exampleton?', buttons=['Yes', 'No'], override_grant='a reason')
    assert 'personal detail' in str(e.value)
    assert not (grants.parent / 'grant_overrides.jsonl').exists()


def test_no_grants_file_means_no_check_and_no_noise(phone, tmp_path, monkeypatch):
    monkeypatch.setenv(guards.GRANTS_ENV, str(tmp_path / 'none' / 'standing_grants.json'))
    assert P.phone_panel_show(title='Merge #12?', buttons=['Yes', 'No']) == 'shown on the phone'
    assert P.phone_say('should I merge #12?') == 'shown on the phone'
    assert not (tmp_path / 'none').exists()


def test_a_grants_file_that_does_not_read_says_so(phone, grants):
    grants.write_text('{not json', encoding='utf8')
    out = P.phone_panel_show(title='Merge #12?', buttons=['Yes', 'No'])
    assert out.startswith('shown on the phone') and 'standing grants not checked' in out


def test_yes_no_detection():
    assert guards.is_yes_no(['Yes', 'No']) and guards.is_yes_no(['Approve', 'Deny'])
    assert guards.is_yes_no(['OK'], text='go on?') and not guards.is_yes_no(['OK'], text='done')
    assert not guards.is_yes_no(['Tea', 'Coffee']) and not guards.is_yes_no(['No', 'Later'])
    assert guards.is_yes_no(None, [{'id': 'a', 'kind': 'choice', 'options': ['yes', 'no']}])
    assert guards.asks_yes_no('Should I merge #12?') and not guards.asks_yes_no('Merged #12.')


# ------------------------------------------------------------------ 3. the mic needs the person's own note

def _iso(hours_ago=0.0):
    return (datetime.datetime.now() - datetime.timedelta(hours=hours_ago)).isoformat(timespec='seconds')


def _inbox(home, *rows):
    with open(home / 'inbox.jsonl', 'a', encoding='utf8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


def _voice(home, n, vid, text, hours_ago=0.0):
    f = home / f'{vid}.webm'
    f.write_bytes(b'\0' * 16)
    _inbox(home, {'n': n, 'ts': _iso(hours_ago), 'kind': 'voice', 'id': vid, 'file': str(f), 'sid': 's1'},
           {'n': n + 1, 'ts': _iso(hours_ago), 'kind': 'voice_text', 'id': vid, 'text': text})


def test_mic_ok_true_or_missing_is_refused_with_the_next_step(phone):
    for v in (True, 'true', None, ''):
        with pytest.raises(api.OpError) as e:
            guards.mic_note(v)
        assert str(e.value) == guards.NEEDS_NOTE
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers', 'mic'], mic_ok=True)
    assert 'phone_watch lists ids' in str(e.value)


def test_a_good_note_opens_the_mic(phone):
    _voice(phone, 1, 'v1', 'yes, you can record the mic while we listen')
    got = guards.mic_note('v1')
    assert got['id'] == 'v1' and 'record the mic' in got['text']
    _inbox(phone, {'n': 3, 'ts': _iso(), 'kind': 'answer', 'id': 'p1', 'sid': 's1', 'answer': 'Send',
                   'values': {'say': 'sure, use the microphone'}})
    assert guards.mic_note(3)['id'] == 'p1' and guards.mic_note('n3')['text'] == 'sure, use the microphone'


def test_a_note_the_person_did_not_send_is_refused(phone):
    _inbox(phone, {'n': 1, 'ts': _iso(), 'kind': 'voice_text', 'id': 'v9', 'text': 'yes record the mic'},
           {'n': 2, 'ts': _iso(), 'kind': 'control', 'id': 'c1', 'text': 'yes record the mic'},
           {'n': 3, 'ts': _iso(), 'kind': 'answer', 'id': 'p2', 'answer': 'yes',
            'values': {'say': 'yes record the mic'}})
    for key in ('v9', 2, 'p2'):
        with pytest.raises(api.OpError) as e:
            guards.mic_note(key)
        assert "not the person's own note" in str(e.value)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v404')
    assert 'phone_watch lists' in str(e.value)


def test_an_old_note_is_refused(phone):
    _voice(phone, 1, 'v1', 'yes, record the mic', hours_ago=guards.MIC_NOTE_MAX_H + 1)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v1')
    assert 'ask them again' in str(e.value)


def test_a_note_without_a_mic_word_or_saying_no_is_refused(phone):
    _voice(phone, 1, 'v1', 'yes, play the next one')
    _voice(phone, 3, 'v2', 'delete that recording')
    _inbox(phone, {'n': 5, 'ts': _iso(), 'kind': 'answer', 'id': 'p1', 'sid': 's1', 'answer': 'yes', 'values': {}})
    for key in ('v1', 5):
        with pytest.raises(api.OpError) as e:
            guards.mic_note(key)
        assert 'does not mention the mic' in str(e.value)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v2')
    assert 'reads as a no' in str(e.value)


def test_listen_start_passes_a_good_note_on_to_the_recorder(phone, monkeypatch):
    _voice(phone, 1, 'v1', 'yes, record the mic')

    def stop_here(project):
        raise ValueError('past the mic check')
    monkeypatch.setattr(LI, 'pick_home', stop_here)
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers', 'mic'], mic_ok='v1')
    assert str(e.value) == 'past the mic check'


def test_the_recorder_checks_the_note_again_before_any_device(phone, tmp_path, monkeypatch):
    d = tmp_path / 'session'
    d.mkdir()
    LI.write_json(d / 'request.json', {'streams': ['speakers', 'mic'], 'max_s': 60, 'mic_note': None})

    def no_devices():
        raise AssertionError('the recorder reached the audio devices')
    monkeypatch.setattr(LI, '_pa', no_devices)
    LI.record(d)
    st = LI.read_json(d / 'status.json')
    assert st['state'] == 'failed' and st['error'].startswith('mic refused: mic_ok needs the id')
