"""venue_rules and chat_commands: !request and !vote handled by each venue's rules, votes that move the line, and the
credit lines for the viewers who asked."""
import json

import pytest

from ismail import api
from ismail.api import OpError
from ismail.stream import config as C, ops, request_queue as Q, venue_rules as V


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'stream')
    ops.venue_set(name='site1', platform='fake')
    ops.venue_set(name='site2', platform='fake')
    return tmp_path / 'stream'


class Chat:
    """Chat lines as chat_watch returns them, numbered like the chat log."""
    def __init__(self):
        self.n = 0

    def __call__(self, user, text, venue='site1', **flags):
        self.n += 1
        return {'n': self.n, 'at': 1000.0 + self.n, 'kind': 'chat', 'venue': venue, 'platform': 'fake',
                'text': text,
                'user': {'id': f'fake:{user.lower()}', 'name': user.lower(), 'display': user, **flags}}


def run(*lines):
    return json.loads(V.chat_commands(json.dumps({'since': 9, 'lines': list(lines)})))


def test_request_queues_and_replies(home):
    chat = Chat()
    out = run(chat('ViewerA', '!request deep house'), chat('ViewerB', 'hello all'),
              chat('ViewerB', '!REQUEST   a sad cello  over rain'))
    assert [h['result'] for h in out['handled']] == ['queued', 'queued']
    assert [r['reply_to'] for r in out['replies']] == [1, 3]
    first = out['replies'][0]['text']
    assert first.startswith("queued 'deep house': #1 in line") and '!vote 1' in first
    assert "'a sad cello over rain': #2 in line" in out['replies'][1]['text']
    r = Q.state()[1]
    assert (r['who'], r['venue'], r['what']) == ('ViewerA', 'site1', 'deep house')


def test_limits_per_viewer_cooldown_and_mods(home, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(V.time, 'time', lambda: clock[0])
    monkeypatch.setattr(Q.time, 'time', lambda: clock[0])
    chat = Chat()
    run(chat('ViewerA', '!request one'))
    out = run(chat('ViewerA', '!request two'))
    assert out['handled'][0]['result'] == 'refused'
    assert out['replies'][0]['text'] == "you have #1 waiting ('one', #1 in line)"
    Q.request_mark(id=1, status='playing')
    clock[0] += 120
    out = run(chat('ViewerA', '!request two'))
    assert out['replies'][0]['text'] == 'your next request opens in 8 min'
    clock[0] += 500
    assert run(chat('ViewerA', '!request two'))['handled'][0]['result'] == 'queued'
    for what in ('more bass', 'faster', 'slower'):     # the owner and mods skip both limits
        assert run(chat('Host', f'!request {what}', owner=True))['handled'][0]['result'] == 'queued'
    assert run(chat('Helper', '!request deeper', mod=True))['handled'][0]['result'] == 'queued'
    # the same name on another venue is another viewer
    assert run(chat('ViewerA', '!request three', venue='site2'))['handled'][0]['result'] == 'queued'


def test_duplicates_count_as_votes_and_votes_move_the_line(home):
    chat = Chat()
    run(chat('ViewerA', '!request deep house'), chat('ViewerB', '!request drum and bass'))
    out = run(chat('ViewerC', '!request Drum & Bass!'))
    assert out['handled'][0] == {'n': 3, 'venue': 'site1', 'user': 'ViewerC', 'command': 'request', 'result': 'voted',
                                 'id': 2}
    assert 'already in line as #2: counted your vote, now #1 in line' in out['replies'][0]['text']
    assert Q.line_up(Q.state())[0]['id'] == 2 and ', 1 vote' in Q.request_list()
    out = run(chat('ViewerB', '!request drum n bass'))
    assert out['replies'][0]['text'].startswith('you asked for that already: #2')
    out = run(chat('ViewerD', '!vote 1'), chat('ViewerE', '!vote #1'), chat('ViewerD', '!vote 1'),
              chat('ViewerA', '!vote 1'), chat('ViewerD', '!vote 9'), chat('ViewerD', '!vote b'))
    assert [h['result'] for h in out['handled']] == ['voted', 'voted', 'refused', 'refused', 'refused']
    texts = [r['text'] for r in out['replies']]
    assert "vote counted for #1 'deep house' (2), now #1 in line" == texts[1]
    assert texts[2] == 'you voted for #1 already' and texts[3] == '#1 is your own request'
    assert texts[4] == 'no request #9; waiting: #1, #2'
    assert out['left'] == [{'n': 10, 'text': '!vote b', 'why': 'not a request id: a vote of yours?'}]
    assert [r['id'] for r in Q.line_up(Q.state())] == [1, 2]
    Q.request_mark(id=1, status='playing')
    assert run(chat('ViewerF', '!vote 1'))['replies'][0]['text'] == '#1 is not waiting (it is playing)'


def test_a_line_is_acted_on_once(home):
    chat = Chat()
    lines = [chat('ViewerA', '!request one'), chat('ViewerA', '!request two'), chat('ViewerB', '!vote 1')]
    first = run(*lines)
    again = run(*lines)
    assert len(first['replies']) == 3 and again['replies'] == []
    assert [h['result'] for h in again['handled']] == ['handled before'] * 3
    assert len(Q.state()) == 1 and len(Q.state()[1]['votes']) == 1
    restarted = {**lines[0], 'at': 5000.0}               # a chat log started over: n 1 again, a new line
    assert run(restarted)['handled'][0]['result'] == 'refused'


def test_rules_per_venue_and_errors_point_forward(home):
    out = V.venue_rules()
    assert out.startswith('rules:') and '*: !request open' in out and 'site1: !request open' in out
    out = V.venue_rules(venue='site2', request=False, max_len=20)
    assert out.startswith('set.') and 'site2 (its own): !request closed' in out and 'max 20 chars' in out
    chat = Chat()
    out = run(chat('ViewerA', '!request one', venue='site2'), chat('ViewerA', '!request one'),
              chat('ViewerB', '!request', venue='site1'))
    assert [r['text'] for r in out['replies']][0] == 'requests are closed right now'
    assert out['replies'][2]['text'].startswith('say what you would like')
    V.venue_rules(venue='*', max_waiting=2, cooldown_s=0, duplicates=False)
    assert run(chat('ViewerA', '!request one more'))['handled'][0]['result'] == 'queued'
    assert 'site2: !request open' in V.venue_rules(venue='site2', reset=True)
    with pytest.raises(OpError, match=r"no venue 'nowhere': '\*' \(every venue\) or one of site1, site2"):
        V.venue_rules(venue='nowhere')
    with pytest.raises(OpError, match='max_waiting: 1 to 20'):
        V.venue_rules(max_waiting=0)
    with pytest.raises(OpError, match="chat_watch's JSON"):
        V.chat_commands('not json')


def test_request_vote_op_and_credits(home, tmp_path):
    Q.request_add(who='ViewerA', what='deep house', venue='site1')
    Q.request_add(who='ViewerB', what='a sad cello over rain', venue='site2')
    assert 'now position 1 in line' in Q.request_vote(id=2, who='ViewerC', venue='site1')
    with pytest.raises(OpError, match='their own request'):
        Q.request_vote(id=2, who='viewerb', venue='site2')
    assert Q.request_credits().startswith('no request has played yet')
    Q.request_mark(id=2, status='playing')
    Q.request_mark(id=1, status='playing')
    assert Q.request_credits() == ('- ViewerA (site1 on fake): asked for "deep house"\n'
                                   '- ViewerB (site2 on fake): asked for "a sad cello over rain"')
    song = tmp_path / 'song'
    song.mkdir()
    (song / 'CREDITS.md').write_text('# Credits\n\n## Sounds\n\n- a kit\n', encoding='utf8')
    out = Q.request_credits(ids=[2], path=str(song))
    assert 'added 1' in out
    out = Q.request_credits(path=str(song / 'CREDITS.md'))
    assert 'added 1' in out and 'ViewerA' in out
    assert 'every line is there already' in Q.request_credits(path=str(song))
    text = (song / 'CREDITS.md').read_text(encoding='utf8')
    assert text == ('# Credits\n\n## Sounds\n\n- a kit\n\n## Requests from chat\n\n'
                    '- ViewerB (site2 on fake): asked for "a sad cello over rain"\n'
                    '- ViewerA (site1 on fake): asked for "deep house"\n')
    with pytest.raises(OpError, match='no request 7'):
        Q.request_credits(ids=[7])


def test_ops_are_shelved_for_the_dj_and_mc():
    from ismail.api_ops import category_of
    for name in ('venue_rules', 'chat_commands', 'request_vote', 'request_credits'):
        assert name in api.OPS and category_of(name) == 'stream'


def test_one_vote_or_love_per_viewer_per_window(home, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(V.time, 'time', lambda: clock[0])
    monkeypatch.setattr(Q.time, 'time', lambda: clock[0])
    chat = Chat()
    run(chat('ViewerA', '!request one'), chat('ViewerB', '!request two'), chat('ViewerC', '!request three'))
    Q.request_mark(id=1, status='playing', on_screen=False)
    out = run(chat('ViewerD', '!love'), chat('ViewerD', '!love'), chat('ViewerD', '!vote 2'),
              chat('ViewerE', '!LOVE'))
    assert [h['result'] for h in out['handled']] == ['loved', 'refused', 'refused', 'loved']
    assert out['handled'][0]['id'] == 1
    assert [r['text'] for r in out['replies']] == ['one vote or love per 60 s: yours counts again in 1 min']
    assert '2 !love' in Q.request_next()
    clock[0] += 61
    out = run(chat('ViewerD', '!vote 2'), chat('ViewerD', '!vote 3'), chat('ViewerD', '!request three'))
    assert [h['result'] for h in out['handled']] == ['voted', 'refused', 'refused']
    assert out['replies'][2]['text'] == "'three' is already in line as #3"       # too soon to count as a vote
    V.venue_rules(venue='site2', reward_s=0, love=False)
    out = run(chat('ViewerD', '!vote 3', venue='site2'), chat('ViewerD', '!vote 2', venue='site2'),
              chat('ViewerD', '!love', venue='site2'))
    assert [h['result'] for h in out['handled']] == ['voted', 'voted', 'refused']
    assert 'one !vote or !love per 0 s' in V.venue_rules(venue='site2') and '!love closed' in V.venue_rules()
    with pytest.raises(OpError, match='reward_s: 0 to 3600'):
        V.venue_rules(reward_s=-1)
