"""The checks ops run before they show text, ask a yes/no question or open a mic (ismail.guards). Every value here
is made up; the local files live in tmp dirs set through the same env vars the person's own files use."""
import datetime
import json
import os
import threading
from pathlib import Path
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
                   'values': {'say': 'sure, use the microphone'}, 'typed': ['say']})
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
    assert 'phone_watch and stage_events list' in str(e.value)


def test_an_old_note_is_refused(phone):
    _voice(phone, 1, 'v1', 'yes, record the mic', hours_ago=guards.MIC_NOTE_MAX_H + 1)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v1')
    assert 'ask them again' in str(e.value)


def test_a_note_without_a_mic_word_or_saying_no_is_refused(phone):
    _voice(phone, 1, 'v1', 'yes, play the next one')
    _voice(phone, 3, 'v2', 'delete that recording')
    _inbox(phone, {'n': 5, 'ts': _iso(), 'kind': 'answer', 'id': 'p1', 'sid': 's1', 'answer': 'yes', 'values': {},
                   'typed': []})
    for key in ('v1', 5):
        with pytest.raises(api.OpError) as e:
            guards.mic_note(key)
        assert 'does not mention the mic' in str(e.value)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v2')
    assert 'reads as a no' in str(e.value)


def test_an_option_picked_is_not_their_words(phone):
    _inbox(phone, {'n': 1, 'ts': _iso(), 'kind': 'answer', 'id': 'p1', 'sid': 's1', 'answer': 'Send',
                   'values': {'mode': 'record the mic', 'boxes': ['record the mic'], 'on': True, 'say': 'sounds good'},
                   'typed': ['say']},
           {'n': 2, 'ts': _iso(), 'kind': 'answer', 'id': 'p2', 'sid': 's1', 'answer': 'Send',
            'values': {'mode': 'record the mic'}})
    with pytest.raises(api.OpError) as e:            # the picked option and the ticked box do not count
        guards.mic_note(1)
    assert 'does not mention the mic' in str(e.value) and 'sounds good' in str(e.value)
    with pytest.raises(api.OpError) as e:            # an answer that cannot tell typed from picked gives nothing
        guards.mic_note(2)
    assert 'which of its fields were typed' in str(e.value)


def _vr(tmp_path, monkeypatch, text, hours_ago=0.0, with_audio=True, landed=True, etype='voice_message'):
    scenes = tmp_path / 'scenes'
    (scenes / 'room' / 'live').mkdir(parents=True)
    (scenes / 'room' / 'voice').mkdir()
    rel = str(Path('voice') / 'note1.webm')
    if with_audio:
        (scenes / 'room' / rel).write_bytes(b'\0' * 16)
    evs = []
    if landed:
        evs.append({'id': 7, 'ts': _iso(hours_ago), 'type': 'voice_in', 'file': rel, 'bytes': 16})
    evs.append({'id': 8, 'ts': _iso(hours_ago), 'type': etype, 'file': rel, 'text': text})
    (scenes / 'room' / 'live' / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in evs),
                                                          encoding='utf8')
    monkeypatch.setattr(guards, '_stage_scene_dirs', lambda: [scenes])
    return scenes


def test_a_vr_voice_note_opens_the_mic(phone, tmp_path, monkeypatch):
    _vr(tmp_path, monkeypatch, 'yeah go ahead and record the mic')
    got = guards.mic_note('vr:room:8')
    assert got['from'] == 'vr' and got['key'] == 'vr:room:8' and 'record the mic' in got['text']


def test_a_vr_note_is_checked_like_a_phone_note(phone, tmp_path, monkeypatch):
    cases = [(dict(text='record the mic', with_audio=False), "not the person's own note"),
             (dict(text='record the mic', landed=False), "not the person's own note"),
             (dict(text='record the mic', etype='say'), "not the person's own note"),
             (dict(text=None), 'has no transcript'),
             (dict(text='record the mic', hours_ago=guards.MIC_NOTE_MAX_H + 1), 'ask them again'),
             (dict(text='play the next one'), 'does not mention the mic'),
             (dict(text='do not record the mic'), 'reads as a no')]
    for i, (kw, want) in enumerate(cases):
        _vr(tmp_path / str(i), monkeypatch, **kw)
        with pytest.raises(api.OpError) as e:
            guards.mic_note('vr:room:8')
        assert want in str(e.value), (kw, str(e.value))
    with pytest.raises(api.OpError) as e:
        guards.mic_note('vr:hall:8')
    assert 'no VR voice note' in str(e.value)


def test_the_refusal_names_both_note_sources(phone):
    with pytest.raises(api.OpError) as e:
        guards.mic_note(True)
    assert 'phone inbox' in str(e.value) and 'VR voice note' in str(e.value) and 'chat:' in str(e.value)
    with pytest.raises(api.OpError) as e:
        guards.mic_note('v404')
    assert 'phone inbox' in str(e.value) and 'VR voice notes' in str(e.value)


def test_a_chat_yes_is_checked_and_logged_locally(phone, tmp_path, monkeypatch):
    log = tmp_path / 'local' / 'mic_consent.jsonl'
    monkeypatch.setenv(guards.MIC_CONSENT_ENV, str(log))
    got = guards.mic_note('chat: "sure, record the mic while we listen"', 'listen_start')
    assert got['from'] == 'chat' and got['key'] == 'chat-log:1'
    rows = [json.loads(x) for x in log.read_text(encoding='utf8').splitlines()]
    assert rows == [{'ts': rows[0]['ts'], 'op': 'listen_start', 'quote': 'sure, record the mic while we listen'}]
    assert guards.mic_note('chat-log:1', 'listen.record')['text'] == 'sure, record the mic while we listen'
    assert len(log.read_text(encoding='utf8').splitlines()) == 1      # checking it again logs nothing
    for quote, want in (('chat: sounds good', 'does not mention the mic'),
                        ("chat: don't record the mic", 'reads as a no'), ('chat:   ', 'exact words')):
        with pytest.raises(api.OpError) as e:
            guards.mic_note(quote)
        assert want in str(e.value)
    assert len(log.read_text(encoding='utf8').splitlines()) == 1      # a refused quote is not logged
    with pytest.raises(api.OpError):
        guards.mic_note('chat-log:9')


def test_a_chat_yes_reaches_the_recorder_as_its_log_line(phone, tmp_path, monkeypatch):
    log = tmp_path / 'mic_consent.jsonl'
    monkeypatch.setenv(guards.MIC_CONSENT_ENV, str(log))
    seen = {}

    def stop_here(project):
        seen['logged'] = log.read_text(encoding='utf8')
        raise ValueError('past the mic check')
    monkeypatch.setattr(LI, 'pick_home', stop_here)
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers', 'mic'], mic_ok='chat: yes, record the mic')
    assert str(e.value) == 'past the mic check' and '"op": "listen_start"' in seen['logged']
    d = tmp_path / 'session'
    d.mkdir()
    LI.write_json(d / 'request.json', {'streams': ['mic'], 'max_s': 60, 'mic_note': 'chat-log:5'})

    def no_devices():
        raise AssertionError('the recorder reached the audio devices')
    monkeypatch.setattr(LI, '_pa', no_devices)
    LI.record(d)                                      # a log line that is not there: refused before any device
    st = LI.read_json(d / 'status.json')
    assert st['state'] == 'failed' and 'no chat yes' in st['error']


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
    assert st['state'] == 'failed' and st['error'].startswith("mic refused: mic_ok needs the person's yes")


# ------------------------------------------------------------------ owners, routes and the still-ask list (M209-M210)

def _entries(path, *rows):
    path.write_text(json.dumps([GRANT, *rows]), encoding='utf8')


def test_a_grant_with_an_owner_is_acted_on_by_it_and_sent_to_it_by_anyone_else(phone, grants):
    _entries(grants, {'id': 'G2', 'date': '2026-01-02', 'keywords': ['room restart'], 'says': 'restart the room when '
                      'the person is out of it', 'owner': 'Room Dev'})
    with pytest.raises(api.OpError, match="this is Room Dev's, not the person's"):
        P.phone_ask('ok to do a room restart?', sender='Song Agent')
    with pytest.raises(api.OpError, match='a standing grant covers this'):
        P.phone_ask('ok to do a room restart?', sender='room dev')
    out = P.phone_ask('ok to do a room restart?', sender='Song Agent', override_grant='the person asked to choose')
    assert 'set aside' in out
    row = json.loads((grants.parent / 'grant_overrides.jsonl').read_text().splitlines()[-1])
    assert row['verdict'] == 'send' and row['sender'] == 'Song Agent'


def test_a_route_sends_any_question_to_its_owner_but_lets_the_owner_ask(phone, grants):
    _entries(grants, {'id': 'G2', 'date': '2026-01-02', 'keywords': ['views', 'how many plays'], 'kind': 'route',
                      'says': 'numbers on the posts', 'owner': 'Numbers Agent'})
    with pytest.raises(api.OpError, match='answers this kind of question'):
        P.phone_say('how many plays did the teaser get?', sender='Song Agent')
    assert P.phone_say('how many plays did the teaser get?', sender='Numbers Agent') == 'shown on the phone'
    assert P.phone_say('the teaser has 40 views') == 'shown on the phone'        # no question: nothing to route


def test_public_money_accounts_and_deleting_are_always_asked(phone, grants):
    out = P.phone_ask('merge #12 and delete the old branch?')
    assert out.startswith('shown on the phone') and "never covers 'delete'" in out
    assert api.OPS['grant_check'](ask='should I merge and publish the release?').startswith('ask:')


def test_grant_check_answers_act_send_or_ask(grants):
    _entries(grants, {'id': 'G2', 'date': '2026-01-02', 'keywords': ['room restart'], 'says': 'restart the room',
                      'owner': 'Room Dev'})
    gc = api.OPS['grant_check']
    assert gc(ask='NEEDS NATE: merge #12?').startswith('act:')
    assert gc(ask='room restart now?', sender='Song Agent').startswith("send: this is Room Dev's")
    assert gc(ask='room restart now?', sender='Room Dev').startswith('act:')
    assert gc(ask='which take do you like?').startswith('ask: no standing grant')
    with pytest.raises(api.OpError, match='the words'):
        gc(ask=' ')


def test_grant_add_needs_the_persons_words_and_narrow_keywords(tmp_path, monkeypatch):
    p = tmp_path / 'local' / 'standing_grants.json'
    monkeypatch.setenv(guards.GRANTS_ENV, str(p))
    add = api.OPS['grant_add']
    assert 'none yet' in api.OPS['grant_list']()
    with pytest.raises(api.OpError, match='source'):
        add(says='merge on green', keywords=['merge'], source='the lead said so')
    with pytest.raises(api.OpError, match='match almost any ask'):
        add(says='merge on green', keywords=['merge', 'yes'], source='D-1')
    with pytest.raises(api.OpError, match='always asked'):
        add(says='post the clips', keywords=['clips'], source='n12')
    with pytest.raises(api.OpError, match='owner'):
        add(says='numbers', keywords=['views'], source='n12', kind='route')
    out = add(says='merge reviewed pull requests on green', keywords=['merge', 'merging'], source='hq:D-7',
              owner='Maintainer')
    assert out.startswith("added G1 (") and "Maintainer's, from hq:D-7" in out
    assert add(says='numbers on posts', keywords=['views'], source='chat: ask the numbers agent', kind='route',
               owner='Numbers Agent').startswith('added G2')
    assert [g['id'] for g in json.loads(p.read_text())] == ['G1', 'G2']
    assert api.OPS['grant_check'](ask='merge #3?', sender='Maintainer').startswith('act:')
    with pytest.raises(api.OpError, match='why'):
        api.OPS['grant_drop'](id='G1', why='')
    assert 'dropped G1' in api.OPS['grant_drop'](id='G1', why='the person took it back')
    assert [g['id'] for g in json.loads(p.read_text())] == ['G2']
    assert 'G1' in (p.parent / 'grants_dropped.jsonl').read_text()
    p.write_text('{broken', encoding='utf8')
    with pytest.raises(api.OpError, match='not overwritten'):
        add(says='x y', keywords=['stage restart'], source='D-2')
    assert p.read_text() == '{broken'


def test_an_entry_with_a_source_matches_before_one_without(grants):
    # an older entry with no source and a wide keyword must not shadow the sourced one that names an owner
    grants.write_text(json.dumps([
        {'id': 'G1', 'date': '2026-01-01', 'keywords': ['merge'], 'says': 'merge anything'},
        {'id': 'G7', 'date': '2026-01-02', 'keywords': ['business main'], 'says': 'merges into business main',
         'kind': 'grant', 'source': 'D-9', 'owner': 'Lead Agent'}]), encoding='utf8')
    assert api.OPS['grant_check'](ask='merge into business main?', sender='Song Agent').startswith(
        "send: this is Lead Agent's")
    assert api.OPS['grant_check'](ask='merge #4?', sender='Song Agent').startswith('act:')
    assert 'G1 (2026-01-01, grant, no source' in api.OPS['grant_list']()


def test_a_route_still_stops_at_the_still_ask_list_and_his_senses(grants):
    _entries(grants, {'id': 'G2', 'date': '2026-01-02', 'keywords': ['the teaser'], 'kind': 'route',
                      'says': 'the teaser is the clips agent\'s', 'owner': 'Clips Agent', 'source': 'D-3'})
    gc = api.OPS['grant_check']
    assert gc(ask='when does the teaser go out?', sender='Song Agent').startswith("send: this is Clips Agent's")
    assert gc(ask='should I publish the teaser?', sender='Song Agent').startswith('ask: G2 matched')
    assert gc(ask='can you listen to the teaser mix?', sender='Song Agent').startswith('ask: G2 matched')
    assert gc(ask='is the teaser exam ready for you?', sender='Song Agent').startswith('ask: G2 matched')


def test_a_context_keeps_a_grant_to_its_own_kind_of_step(grants):
    # a room-edit grant shares "typed yes" with a site deploy ask; its context keeps it to the room (qa 10-30 G5)
    _entries(grants, {'id': 'G5', 'date': '2026-01-05', 'keywords': ['typed yes', 'room rebuild'],
                      'says': 'his spoken word in the headset is his yes for room edits', 'kind': 'grant',
                      'source': 'D-5', 'owner': 'Room Dev', 'context': ['room', 'stage', 'scene']})
    gc = api.OPS['grant_check']
    assert gc(ask='NEEDS NATE: a typed yes to deploy site-fixes-1030', sender='Site Agent').startswith(
        'ask: no standing grant')
    assert gc(ask='do I need a typed yes for the room rebuild?', sender='Song Agent').startswith(
        "send: this is Room Dev's")
    assert gc(ask='a typed yes for the stage room rebuild?', sender='Room Dev').startswith('act:')
    assert guards.grant_for('a typed yes to deploy the site') is None
    assert guards.grant_for('a typed yes for the room')['id'] == 'G5'


def test_grant_add_takes_a_context(tmp_path, monkeypatch):
    p = tmp_path / 'local' / 'standing_grants.json'
    monkeypatch.setenv(guards.GRANTS_ENV, str(p))
    add = api.OPS['grant_add']
    with pytest.raises(api.OpError, match='context'):
        add(says='room edits by voice', keywords=['room rebuild'], source='D-5', context=[' '])
    out = add(says='room edits by voice', keywords=['typed yes'], source='D-5', context=['room', 'stage'])
    assert 'only with: room, stage' in out
    assert json.loads(p.read_text())[0]['context'] == ['room', 'stage']
    assert api.OPS['grant_check'](ask='a typed yes to deploy the site?').startswith('ask:')
    assert api.OPS['grant_check'](ask='a typed yes for the room?').startswith('act:')
