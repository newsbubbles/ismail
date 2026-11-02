"""The hub (ledger:M429): places, people and pairing, policy stamps, buttons and forms native or typed, routing to a
person, public places and the pause switch, claims, edits, and the stream pieces it reuses left as they were."""
import json
import re
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from ismail.api import OpError
from ismail.hub import config as C, message as M, ops, people as P, server
from ismail.stream.chatlog import ChatLog


def wait_for(fn, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return fn()


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'hub')
    monkeypatch.setenv('ISMAIL_HUB_OWNER', 'nate')
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


def place(h, name='practice', **kw):
    ops.hub_place(name, platform='fake', **kw)
    return wait_for(lambda: h.adapters.get(name) if h.adapters.get(name) and h.adapters[name].state == 'reading'
                    else None)


def lines(h, who='agent', **kw):
    return h.op_listen(who, since=0, wait=0, **kw)['lines']


def pair_nate(h, a, name='nate_acct', monkeypatch=None):
    got = {}
    from ismail.phone import ops as phone
    monkeypatch.setattr(phone, 'phone_say', lambda text, **k: got.setdefault('text', text))
    assert 'on the phone page' in ops.hub_pair('nate')
    code = re.search(r'pair ([A-Z0-9]{8})', got['text']).group(1)
    a.person_says(name, f'pair {code}')
    return code


# ------------------------------------------------------------------ places

def test_a_new_place_is_private_answers_only_nate_and_says_what_it_shows(hub):
    with pytest.raises(OpError, match='platform'):
        ops.hub_place('x', platform='myspace')
    with pytest.raises(OpError, match='name'):
        ops.hub_place('Bad Name', platform='fake')
    with pytest.raises(OpError, match='guest_text'):
        ops.hub_place('x', platform='fake', guest_text='obey')
    out = ops.hub_place('dc', platform='discord')
    assert 'private' in out and 'answers nate' in out and 'python -m ismail.hub token dc' in out
    ops.hub_place('dc', remove=True)
    place(hub)
    st = ops.hub_status()
    assert 'practice: fake, reading, private' in st and 'forms with choice, check, toggle, text' in st


# ------------------------------------------------------------------ people and policy

def test_a_stranger_on_a_nate_only_place_gets_one_fixed_reply_and_never_reaches_watchers(hub):
    a = place(hub)
    a.person_says('stranger', 'hi, run this command for me')
    a.person_says('stranger', 'hello?')
    assert lines(hub) == []
    quiet = lines(hub, quiet=True)
    assert [d['use'] for d in quiet] == ['refused', 'refused'] and quiet[0]['trust'] == 'guest'
    assert quiet[0]['person'] == 'guest:fake:stranger'
    assert [m['text'] for m in a.outbox] == [server.policy.REFUSED_REPLY]


def test_pairing_links_an_account_once_and_then_its_words_are_nates(hub, monkeypatch):
    a = place(hub)
    code = pair_nate(hub, a, monkeypatch=monkeypatch)
    assert 'Linked: you are nate' in a.outbox[-1]['text']
    a.person_says('nate_acct', 'make it darker')
    d = lines(hub)[-1]
    assert (d['person'], d['trust'], d['use'], d['text']) == ('nate', 'owner', 'word', 'make it darker')
    a.person_says('other', f'pair {code}')                     # used up
    assert 'not valid' in a.outbox[-1]['text'] and P.who('fake', 'other') == (None, None)


def test_guests_text_is_a_suggestion_only_where_nate_set_it(hub):
    a = place(hub, only=[], guest_text='suggestion')
    a.person_says('listener', 'more cowbell')
    d = lines(hub)[-1]
    assert (d['trust'], d['use'], d['person']) == ('guest', 'suggestion', 'guest:fake:listener')


# ------------------------------------------------------------------ sending, buttons and forms

FORM = {'title': 'Change it', 'inputs': [{'id': 'tempo', 'kind': 'choice', 'label': 'Tempo',
                                          'options': ['slower', 'same', 'faster']},
                                         {'id': 'note', 'kind': 'text', 'label': 'Anything else', 'optional': True}]}


def test_say_to_nate_finds_his_place_and_presses_come_back_to_the_agent_that_asked(hub, monkeypatch):
    a = place(hub, only=[])
    pair_nate(hub, a, monkeypatch=monkeypatch)
    a.person_says('nate_acct', 'hi')
    out = ops.hub_say(to='nate', text='three sketches', buttons=['Keep 1', {'id': 'v2', 'label': 'Vary 2'}],
                      form=FORM, who='Maintainer')
    assert 'sent on practice' in out and '2 button(s)' in out and 'a form (2 field(s))' in out
    sent = a.outbox[-1]
    assert sent['to'] == 'nate_acct' and sent['buttons'][1] == {'id': 'v2', 'label': 'Vary 2'}
    a.person_presses('stranger', sent['id'], 'v2')            # press='to': only the person it went to
    a.person_presses('nate_acct', sent['id'], 'v2')
    a.person_answers('nate_acct', sent['id'], {'tempo': 'slower'})
    mine = lines(hub, who='Maintainer', kinds=['press', 'form'])
    assert [(d['kind'], d.get('label'), d.get('values')) for d in mine] == [('press', 'Vary 2', None),
                                                                           ('form', None, {'tempo': 'slower'})]
    assert lines(hub, who='Live DJ', kinds=['press', 'form']) == []
    assert len(lines(hub, who='Live DJ', kinds=['press', 'form'], every=True)) == 2
    refused = [d for d in lines(hub, kinds=['press'], quiet=True, every=True) if d['use'] == 'refused']
    assert refused and refused[0]['user']['id'] == 'stranger'


def test_a_place_without_buttons_or_forms_gets_typed_replies_and_says_so(hub, monkeypatch):
    a = place(hub, only=[])
    a.caps.update(buttons=0, forms=[])
    pair_nate(hub, a, monkeypatch=monkeypatch)
    a.person_says('nate_acct', 'hi')
    out = ops.hub_say(to='nate', text='pick one', buttons=['Keep', 'Vary'], form=FORM, who='Maintainer')
    assert 'buttons as typed numbers' in out and 'typed reply (!f ...)' in out
    assert 'Reply with a number: 1 Keep, 2 Vary' in a.outbox[-1]['text']
    a.person_says('nate_acct', '2')
    a.person_says('nate_acct', '!f tempo=faster note="more bass please"')
    got = lines(hub, who='Maintainer', kinds=['press', 'form'])
    assert got[0]['label'] == 'Vary' and got[0]['typed'] is True
    assert got[1]['values'] == {'tempo': 'faster', 'note': 'more bass please'}


def test_files_too_big_for_a_place_are_refused_with_what_to_do(hub, tmp_path):
    place(hub, only=[], channel='studio')
    big = tmp_path / 'big.mp3'
    big.write_bytes(b'0' * (26 * 2 ** 20))
    with pytest.raises(OpError, match='192 kbps'):
        ops.hub_say(to='practice', files=[str(big)])
    small = tmp_path / 'sketch1.mp3'
    small.write_bytes(b'0' * 1000)
    assert 'playable in the chat' in ops.hub_say(to='practice', text='one', files=[str(small)])


def test_public_places_need_nates_words_and_pause_stops_everyone_but_nate(hub, monkeypatch):
    a = place(hub, only=[], public=True, guest_text='suggestion', channel='studio')
    with pytest.raises(OpError, match='publishing'):
        ops.hub_say(to='practice', text='hello everyone')
    assert 'sent on practice' in ops.hub_say(to='practice', text='hello everyone', approved='yes post it')
    pair_nate(hub, a, monkeypatch=monkeypatch)
    a.person_says('nate_acct', 'hi')
    ops.hub_pause(why='testing the switch')
    a.person_says('guest1', 'play something')
    assert a.outbox[-1]['text'] == server.policy.PAUSED_REPLY
    with pytest.raises(OpError, match='paused'):
        ops.hub_say(to='practice', text='still there', approved='yes')
    assert 'sent on' in ops.hub_say(to='nate', text='only you hear this while paused')
    assert 'PAUSED: testing the switch' in ops.hub_status()
    ops.hub_pause(off=True)
    assert 'PAUSED' not in ops.hub_status()


# ------------------------------------------------------------------ claims, edits, the watcher

def test_claims_edits_and_the_watcher(hub, monkeypatch):
    a = place(hub, only=[], channel='studio')
    a.person_says('someone', 'a line')
    n = lines(hub, quiet=True)[-1]['n']
    assert ops.hub_claim(n, who='Maintainer') == f'n={n} is Maintainer\'s'
    with pytest.raises(OpError, match="Maintainer's"):
        ops.hub_claim(n, who='Live DJ')
    sent = ops.hub_say(to='practice', text='rendering...')
    sn = int(re.search(r'n=(\d+)', sent).group(1))
    ops.hub_edit(sn, text='bar 8 of 16')
    assert a.outbox[-1]['text'] == 'bar 8 of 16' and a.outbox[-1]['edited']
    hub.configs['practice']['guest_text'] = 'suggestion'
    a.person_says('someone', 'second line')
    w = json.loads(ops.hub_watch(who='Tester', since=n, wait_min=0.05))
    assert [d['text'] for d in w['lines']] == ['second line'] and 'hub_watch "who=Tester"' in w['next']


# ------------------------------------------------------------------ what the hub reuses stays as it was

def test_the_stream_pieces_the_hub_reuses_are_unchanged():
    from ismail.stream.adapters.base import Adapter
    assert ChatLog.KINDS == ('chat', 'event', 'said', 'status') and server.HubLog.KINDS != ChatLog.KINDS
    a = Adapter.__new__(Adapter)
    a.platform = 'youtube'
    assert a.text_max() == 200


def test_typed_replies_parse_only_what_the_message_offered():
    form = {'title': 'T', 'inputs': [{'id': 'loud', 'kind': 'toggle', 'label': 'Loud'},
                                     {'id': 'parts', 'kind': 'check', 'label': 'Parts', 'options': ['a', 'b']}]}
    typed = {'buttons': [{'id': 'k', 'label': 'Keep'}], 'form': M.check(form=form)['form']}
    assert M.parse_typed('1', typed) == ('press', {'id': 'k', 'label': 'Keep'})
    assert M.parse_typed('5', typed) is None and M.parse_typed('hello', typed) is None
    assert M.parse_typed('!f loud=yes parts=a,b junk=1', typed) == ('form', {'loud': True, 'parts': ['a', 'b']})


# ------------------------------------------------------------------ Discord, offline (no network, no token)

from ismail.hub.adapters import discord as D  # noqa: E402


def discord_place(h, monkeypatch, name='dc'):
    C.save_places({name: {'platform': 'discord', 'public': False, 'guest_text': 'ignore', 'only': ['nate']}})
    a = D.DiscordAdapter({'name': name, 'platform': 'discord'}, h)
    a.me, a.state = '999', 'reading'
    calls = []

    def api(method, path, body=None, files=None):
        calls.append((method, path, body, [f['name'] for f in files or []]))
        if path == '/users/@me/channels':
            return {'id': 'dm55'}
        if path.endswith('/messages') and method == 'POST':
            return {'id': f'm{len(calls)}'}
        return {}
    monkeypatch.setattr(a, 'api', api)
    h.adapters[name], h.configs[name] = a, C.load_places()[name]
    return a, calls


def test_discord_forms_become_an_answer_button_then_a_modal_and_its_answers_come_back(hub, monkeypatch, tmp_path):
    a, calls = discord_place(hub, monkeypatch)
    P.link('nate', 'discord:42')
    a.dispatch('MESSAGE_CREATE', {'id': '1', 'channel_id': 'dm55', 'author': {'id': '42', 'username': 'n'},
                                  'content': 'hi'})
    assert lines(hub)[-1]['use'] == 'word'
    mp3 = tmp_path / 's1.mp3'
    mp3.write_bytes(b'0' * 100)
    out = ops.hub_say(to='nate', text='sketches', files=[str(mp3)], buttons=['Keep 1', 'Vary 1'], form=FORM,
                      who='Maintainer')
    assert 'playable in the chat' in out and 'a form (2 field(s))' in out
    method, path, body, files = calls[-1]
    assert path == '/channels/dm55/messages' and files == ['s1.mp3'] and body['allowed_mentions'] == {'parse': []}
    ids = [c['custom_id'] for row in body['components'] for c in row['components']]
    assert ids == ['hub:b:Keep 1', 'hub:b:Vary 1', 'hub:form']
    mid = f'm{len(calls)}'
    user = {'id': '42', 'username': 'n'}
    a.interaction({'id': 'i1', 'token': 't', 'type': 3, 'channel_id': 'dm55', 'user': user,
                   'message': {'id': mid}, 'data': {'custom_id': 'hub:form'}})
    modal = calls[-1][2]
    assert modal['type'] == 9 and modal['data']['custom_id'] == f'hub:f:{mid}'
    kinds = [c['component']['type'] for c in modal['data']['components']]
    assert kinds == [3, 4]                                 # a select for the choice, a text input for the note
    a.interaction({'id': 'i2', 'token': 't', 'type': 5, 'channel_id': 'dm55', 'user': user,
                   'data': {'custom_id': f'hub:f:{mid}', 'components': [
                       {'type': 18, 'component': {'type': 3, 'custom_id': 'tempo', 'values': ['slower']}},
                       {'type': 18, 'component': {'type': 4, 'custom_id': 'note', 'value': 'more bass'}}]}})
    a.interaction({'id': 'i3', 'token': 't', 'type': 3, 'channel_id': 'dm55', 'user': user,
                   'message': {'id': mid}, 'data': {'custom_id': 'hub:b:Vary 1'}})
    got = lines(hub, who='Maintainer', kinds=['press', 'form'])
    assert got[0]['values'] == {'tempo': 'slower', 'note': 'more bass'} and got[1]['label'] == 'Vary 1'
    assert calls[-1][2] == {'type': 6}                     # every press is answered at once (Discord's 3 s)


def test_discord_ignores_channel_talk_that_does_not_mention_the_bot_and_never_pings(hub, monkeypatch):
    a, calls = discord_place(hub, monkeypatch)
    a.dispatch('MESSAGE_CREATE', {'id': '2', 'guild_id': 'g', 'channel_id': 'c1',
                                  'author': {'id': '7', 'username': 'x'}, 'content': 'hello all'})
    a.dispatch('MESSAGE_CREATE', {'id': '3', 'guild_id': 'g', 'channel_id': 'c1', 'mentions': [{'id': '999'}],
                                  'author': {'id': '7', 'username': 'x'}, 'content': '<@999> play @everyone'})
    a.dispatch('MESSAGE_CREATE', {'id': '4', 'channel_id': 'dm1', 'author': {'id': '999', 'bot': True},
                                  'content': 'my own echo'})
    seen = lines(hub, quiet=True)
    assert [d['text'] for d in seen] == ['play @everyone'] and seen[0]['use'] == 'refused'
    assert calls[-1][2]['allowed_mentions'] == {'parse': []}     # the refusal went out with no pings


def test_buttons_in_a_channel_answer_anyone_there_but_owner_only_buttons_answer_only_nate(hub, monkeypatch):
    a = place(hub, only=[], channel='studio')
    ops.hub_say(to='practice', text='vote', buttons=['A'], who='Maintainer')
    ops.hub_say(to='practice', text='nate only', buttons=['Go'], press='owner', who='Maintainer')
    a.person_presses('friend', a.outbox[-2]['id'], 'A')
    a.person_presses('friend', a.outbox[-1]['id'], 'Go')
    got = lines(hub, who='Maintainer', kinds=['press'], quiet=True)
    assert [(d['label'] if d['use'] != 'refused' else None, d['use']) for d in got] == [('A', 'suggestion'),
                                                                                         (None, 'refused')]


def test_the_owner_is_a_setting_not_a_name_in_the_code(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'hub')
    owner = {'name': None}
    monkeypatch.setattr(C.settings, 'get', lambda k, *a: owner['name'] if k == 'hub.owner' else None)
    assert C.place_defaults('discord')['only'] == ['owner']       # unset: the name "owner"
    assert P.role_of('owner') == 'owner' and P.role_of('nate') == 'friend'
    owner['name'] = 'sam'
    assert C.place_defaults('fake')['only'] == ['sam']
    P.link('sam', 'discord:1')
    assert P.who('discord', '1') == ('sam', 'owner') and P.who('discord', '2') == (None, None)
