"""phone_watch: the standing phone watcher with a cursor on disk. A watcher hand-rolled after a context reset kept no
cursor and missed six of the person's notes in 36 minutes; this one resumes where its last return left off.
No server here: phone_listen is a fake inbox."""
import json

import pytest

from ismail import api  # noqa: F401  (the op table loads)
from ismail.api import OpError
from ismail.phone import ops as P
from ismail.phone import server as S


class FakeInbox:
    """phone_listen over a list of rows. `arrive` holds rows that land one batch per call (a person typing)."""

    def __init__(self, rows=()):
        self.rows, self.arrive, self.calls, self.fail = [], [], [], 0
        for r in rows:
            self.add(r)

    def add(self, r):
        self.rows.append({'n': len(self.rows) + 1, **r})

    def __call__(self, who, since=None, wait=25, page=False):
        self.calls.append({'who': who, 'since': since, 'wait': wait, 'page': page})
        if self.fail:
            self.fail -= 1
            raise OpError('the phone server does not answer')
        start = len(self.rows) if since is None else since
        if self.arrive:
            for r in self.arrive.pop(0):
                self.add(r)
        return json.dumps({'since': len(self.rows), 'lines': [r for r in self.rows if r['n'] > start]})


@pytest.fixture
def inbox(monkeypatch, tmp_path):
    monkeypatch.setattr(S, 'HOME', tmp_path)
    monkeypatch.setattr(P, '_RETRY_S', 0)
    f = FakeInbox()
    monkeypatch.setattr(P, 'phone_listen', f)
    return f


def watch(**kw):
    return json.loads(P.phone_watch(**kw))


def test_no_cursor_starts_at_the_end_and_never_replays(inbox):
    for i in range(5):
        inbox.add({'kind': 'voice_text', 'text': f'old {i}'})
    inbox.arrive = [[], [{'kind': 'voice_text', 'text': 'new'}]]
    out = watch(who='Live DJ')
    assert [d['text'] for d in out['lines']] == ['new']
    assert out['since'] == 6 and 'no saved cursor' in out['note']
    assert 'phone_watch "who=Live DJ"' in out['next'] and out['next'].startswith('re-arm FIRST')


def test_cursor_persists_across_calls(inbox):
    inbox.add({'kind': 'tap', 'what': 'love'})
    first = watch(who='Live DJ', since=0)
    assert [d['what'] for d in first['lines']] == ['love']
    assert json.loads((S.HOME / 'cursors' / 'live_dj.json').read_text())['since'] == 1
    inbox.add({'kind': 'voice_text', 'text': 'while the watcher was down'})
    second = watch(who='Live DJ')                        # a re-arm with no since resumes at the saved line
    assert [d['text'] for d in second['lines']] == ['while the watcher was down']
    assert 'note' not in second
    assert inbox.calls[-2]['since'] == 1                 # (the last call is the burst look)


def test_cursor_is_per_agent(inbox):
    inbox.add({'kind': 'tap', 'what': 'change'})
    watch(who='Live DJ', since=0)
    inbox.add({'kind': 'tap', 'what': 'louder'})
    assert [d['what'] for d in watch(who='Director', since=0)['lines']] == ['change', 'louder']
    assert [d['what'] for d in watch(who='Live DJ')['lines']] == ['louder']


def test_filter_drops_only_telemetry_and_the_transcribing_line(inbox):
    for r in [{'kind': 'page', 'what': 'scroll'}, {'kind': 'page', 'what': 'panel_open'},
              {'kind': 'page', 'what': 'panel_close'}, {'kind': 'voice', 'id': 'v1'},
              {'kind': 'voice_text', 'id': 'v1', 'text': 'hi'},
              {'kind': 'exam', 'for': 'Voice', 'id': 'x1'},            # another agent's: kept (phone.md step 1)
              {'kind': 'exam_round', 'for': 'Voice', 'state': 'started'},
              {'kind': 'answer', 'for': 'Director', 'answer': 'yes'}]:
        inbox.add(r)
    kinds = [(d['kind'], d.get('what')) for d in watch(who='Live DJ', since=0)['lines']]
    assert kinds == [('page', 'panel_close'), ('voice_text', None), ('exam', None), ('exam_round', None),
                     ('answer', None)]
    assert inbox.calls[0]['page'] is True


def test_burst_is_gathered_with_one_short_look(inbox):
    inbox.arrive = [[{'kind': 'answer', 'answer': 'open'}], [{'kind': 'answer', 'answer': 'and this'}]]
    out = watch(who='dj', since=0)
    assert [d['answer'] for d in out['lines']] == ['open', 'and this']
    assert [c['wait'] for c in inbox.calls] == [25, 5]
    assert out['since'] == 2


def test_alive_check_returns_no_lines_and_saves_the_cursor(inbox):
    inbox.add({'kind': 'page', 'what': 'scroll'})        # telemetry only: nothing wakes, the cursor still moves
    out = watch(who='dj', since=0, wait_min=0)
    assert out['alive'] is True and out['lines'] == [] and out['since'] == 1
    assert 'phone_status' in out['next']
    assert P._cursor_load('dj') == 1


def test_waits_out_a_server_restart(inbox):
    inbox.fail = 2
    inbox.arrive = [[{'kind': 'voice_text', 'text': 'back'}]]
    out = watch(who='dj', since=0)
    assert [d['text'] for d in out['lines']] == ['back'] and len(inbox.calls) >= 3


def test_server_down_for_the_whole_wait_says_so(inbox):
    inbox.fail = 99
    out = watch(who='dj', wait_min=0)
    assert out['alive'] is True and out['since'] is None and 'phone_start' in out['next']
    assert P._cursor_load('dj') is None


def test_long_lines_are_cut_and_odd_characters_pass(inbox):
    inbox.add({'kind': 'voice_text', 'text': 'x' * 5000})
    inbox.add({'kind': 'hum', 'notes': 'F♯4 G♯4'})
    out = P.phone_watch(who='dj', since=0)
    lines = json.loads(out)['lines']
    assert len(json.dumps(lines[0], ensure_ascii=False)) <= 1600 and 'inbox line 1' in lines[0]['text']
    assert lines[1]['notes'] == 'F♯4 G♯4'


def test_round_lines_are_held_until_something_else_or_quiet(inbox, monkeypatch):
    monkeypatch.setattr(P, '_ROUND_QUIET_S', -1)
    inbox.arrive = [[{'kind': 'exam', 'id': 's5p_1', 'for': 'Voice'}, {'kind': 'exam', 'id': 's5p_2', 'for': 'Voice'}]]
    out = watch(who='dj', since=0, round='s5p')
    assert out['lines'] == [] and out['held'] == {'round': 's5p', 'lines': 2, 'answered': 2}
    assert 'round s5p' in out['round_over'] and 'round=' not in out['next']
    inbox.arrive = [[{'kind': 'exam', 'id': 's5p_3'}, {'kind': 'tap', 'what': 'change'}]]
    monkeypatch.setattr(P, '_ROUND_QUIET_S', 999)
    out = watch(who='dj', round='s5p')
    assert [d['kind'] for d in out['lines']] == ['tap'] and 'round=s5p' in out['next']
