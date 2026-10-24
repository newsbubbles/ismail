"""The request queue: the MC adds, the DJ reads and marks, both see one line kept in an append-only log."""
import json
import subprocess
import sys

import pytest

from ismail import api
from ismail.api import OpError
from ismail.stream import config as C, request_queue as R


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(C, 'HOME', tmp_path / 'stream')
    return tmp_path / 'stream'


def test_add_list_and_positions(home):
    out = R.request_add(who='ViewerA', what='deep house', venue='practice')
    assert 'queued #1' in out and 'position 1 in line (0 ahead' in out
    out = R.request_add(who='ViewerB', what='  a   sad cello over rain ', venue='practice', note='slow')
    assert "#2 'a sad cello over rain'" in out and 'position 2 in line (1 ahead' in out
    listed = R.request_list()
    assert 'playing: nothing' in listed and 'waiting: 2' in listed
    assert listed.index('1. #1') < listed.index('2. #2') and '; slow' in listed
    assert 'no done requests' == R.request_list(status='done')


def test_next_mark_and_the_line_moves(home):
    for who, what in (('ViewerA', 'one'), ('ViewerB', 'two'), ('ViewerC', 'three')):
        R.request_add(who=who, what=what)
    nxt = R.request_next()
    assert nxt.startswith("next: #1 'one' for ViewerA") and '2 more behind it' in nxt
    assert "request_mark(1, 'playing')" in nxt
    assert 'next: #2' in R.request_mark(id=1, status='playing')
    assert R.request_next().startswith('next: #2')
    out = R.request_mark(id='#2', status='playing')
    assert '#1 marked done' in out                      # one plays at a time
    assert R.state()[1]['status'] == 'done' and R.state()[2]['status'] == 'playing'
    assert 'already playing' in R.request_mark(id=2, status='playing')
    R.request_mark(id=3, status='skipped', note='not in the set tonight')
    assert R.request_next().startswith('no requests waiting')
    R.request_mark(id=3, status='waiting')
    assert R.request_next().startswith('next: #3')
    every = R.request_list(status='all').splitlines()
    assert [l.split(':')[1].split(',')[0].strip() for l in every] == ['done', 'playing', 'waiting']


def test_where_answers_when_does_mine_play(home):
    assert "no request from 'Nobody'; nobody is waiting" == R.request_where(who='Nobody')
    R.request_add(who='ViewerA', what='one', venue='site1')
    R.request_add(who='ViewerB', what='two', venue='site1')
    R.request_add(who='ViewerB', what='three', venue='site2')
    out = R.request_where(who='viewerb')
    assert '#2' in out and 'position 2 in line, 1 ahead' in out and 'position 3 in line, 2 ahead' in out
    assert 'about' not in out                            # nothing has played yet: no timing to guess from
    assert '#3' in R.request_where(who='ViewerB', venue='site2') and '#2' not in R.request_where(
        who='ViewerB', venue='site2')
    assert 'waiting now: ViewerA, ViewerB' in R.request_where(who='ViewerZ')
    R.request_mark(id=1, status='playing')
    assert 'playing now' in R.request_where(who='ViewerA')


def test_timing_comes_from_what_played(home, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(R.time, 'time', lambda: clock[0])
    for n in range(4):
        R.request_add(who=f'Viewer{n}', what=f'piece {n}')
    R.request_mark(id=1, status='playing')
    clock[0] += 240
    R.request_mark(id=2, status='playing')               # #1 played 4 min
    clock[0] += 60
    out = R.request_where(who='Viewer3')
    assert 'position 2 in line, 1 ahead, about 7 min' in out     # 3 min left of #2, then 4 for #3


def test_errors_point_forward(home):
    with pytest.raises(OpError, match='queue is empty'):
        R.request_mark(id=4, status='done')
    R.request_add(who='ViewerA', what='one')
    R.request_add(who='ViewerB', what='two')
    R.request_mark(id=1, status='done')
    with pytest.raises(OpError, match=r"no request 9; the ids are 1 to 2, waiting: 2"):
        R.request_mark(id=9, status='done')
    with pytest.raises(OpError, match='one of waiting, playing, done, skipped'):
        R.request_mark(id=2, status='played')
    with pytest.raises(OpError, match='one of waiting, playing, done, skipped, all'):
        R.request_list(status='finished')
    with pytest.raises(OpError, match='who='):
        R.request_add(who=' ', what='x')
    with pytest.raises(OpError, match='under 300'):
        R.request_add(who='ViewerA', what='x' * 400)


def test_the_log_is_append_only_and_shared_across_processes(home):
    R.request_add(who='ViewerA', what='one')
    R.request_mark(id=1, status='playing')
    with open(R.log_path(), 'a', encoding='utf8') as f:
        f.write('{"ev": "add", "id": 7, "who"')             # a line cut by a crash is skipped
    code = ("import sys; from ismail.stream import config as C, request_queue as R; from pathlib import Path; "
            "C.HOME = Path(sys.argv[1]); print(R.request_add(who='ViewerB', what='two'))")
    out = subprocess.run([sys.executable, '-c', code, str(home)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert 'queued #2' in out.stdout
    evs = [json.loads(l) for l in R.log_path().read_text(encoding='utf8').splitlines() if l.endswith('}')]
    assert [e['ev'] for e in evs] == ['add', 'mark', 'add'] and all('at' in e for e in evs)
    assert R.request_next().startswith("next: #2 'two' for ViewerB")
    assert not (home / 'requests.jsonl.lock').exists()


def test_ops_are_registered_and_shelved_for_the_dj_and_mc():
    from ismail.api_ops import ROLES, category_of
    for name in ('request_add', 'request_list', 'request_mark', 'request_next', 'request_where'):
        assert name in api.OPS and category_of(name) == 'stream'
    assert 'stream' in ROLES['dj'] and 'stream' in ROLES['mc']


def test_each_state_keeps_its_utc_time(home):
    R.request_add(who='ViewerA', what='one')
    R.request_mark(id=1, status='playing', on_screen=False)
    R.request_mark(id=1, status='done')
    r = R.state()[1]
    assert all(r[k] and r[k].endswith('Z') and r[k][10] == 'T' for k in ('queued_at', 'playing_at', 'done_at'))
    assert r['queued_at'] <= r['playing_at'] <= r['done_at'] and 'skipped_at' not in r
    shown = R.request_list(status='all')
    assert f"[queued {r['queued_at'][11:]}, playing {r['playing_at'][11:]}, done {r['done_at'][11:]}]" in shown
    assert '[queued ' in R.request_where(who='ViewerA')


def test_playing_puts_the_viewer_on_screen(home, monkeypatch):
    said = []
    monkeypatch.setattr(R.S, '_call', lambda name, **a: said.append((name, a)) or {'slot': a['slot'],
                                                                                   'text': a['text']})
    R.request_add(who='ViewerA', what='deep house')
    R.request_add(who='ViewerB', what='two')
    out = R.request_mark(id=1, status='playing')
    assert said == [('text', {'slot': 'now', 'text': 'request from @ViewerA: now playing deep house'})]
    assert "on screen: 'request from @ViewerA: now playing deep house'" in out
    R.request_mark(id=2, status='playing', on_screen=False)
    R.request_mark(id=2, status='done')
    assert len(said) == 1


def test_no_stream_server_still_marks(home):
    R.request_add(who='ViewerA', what='one')
    out = R.request_mark(id=1, status='playing')
    assert 'not on screen (the stream server is not running' in out and R.state()[1]['status'] == 'playing'
