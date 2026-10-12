"""Staying responsive while the person is in the headset (presence.py receipts, busy lines, stuck; ear.py; the page's
receipts.js): a note that reaches a session shows "heard by <who>", a session can say what it is busy with, and a
note nobody answers is called out."""
import json
import shutil
import subprocess
import threading
import time
import urllib.error
from pathlib import Path

import pytest

from ismail.api import OPS
from ismail.stage import ear as EAR
from ismail.stage import presence as PR
from test_stage import _get, _post, stage  # noqa: F401  (the fixture)


def _voice(port, scene='room', file='voice/n1.webm', text=None):
    _post(port, f'live/event?scene={scene}', {'type': 'voice_in', 'file': file, 'seconds': '3.0', 'via': 'test'})
    if text:
        _post(port, f'live/event?scene={scene}', {'type': 'voice_message', 'file': file, 'text': text})


def _take(port, who, since):
    """A listener's long poll that returns once something lands; returns its thread."""
    t = threading.Thread(target=lambda: _get(port, f'live/inbox?who={who}&since={since}&wait=5'))
    t.start()
    time.sleep(0.2)
    return t


def _says(port, scene='room'):
    return [c['text'] for c in _get(port, f'live/cmd?scene={scene}&since=0')[1]['cmds'] if c['type'] == 'say']


def test_a_note_handed_to_a_listener_shows_heard_by_it_on_the_page(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 5)
    port = stage['port']
    assert _get(port, 'live/version?scene=room')[1]['receipt'] is None
    start = _get(port, 'live/inbox?who=film')[1]['last']
    t = _take(port, 'film', start)
    _voice(port)
    t.join()
    r = _get(port, 'live/version?scene=room')[1]['receipt']
    assert (r['scene'], r['by'], r['answered'], r['stuck']) == ('room', ['film'], False, [])
    _post(port, 'live/cmd?scene=room', {'type': 'say', 'text': 'on it', 'from': 'film'})
    assert _get(port, 'live/version?scene=room')[1]['receipt']['answered'] is True


def test_a_busy_line_is_set_shown_and_cleared(stage):
    port = stage['port']
    out = OPS['stage_busy'](who='film', text='rendering the drums,   in line', minutes=5)
    assert out == 'busy: film: rendering the drums, in line (for 5 min)'
    b = _get(port, 'live/version?scene=room')[1]['busy']
    assert list(b) == ['film'] and b['film']['text'] == 'rendering the drums, in line' and b['film']['left_s'] > 290
    assert 'busy: film: rendering the drums, in line' in OPS['stage_presence']()
    assert OPS['stage_busy'](who='film') == 'busy: film: cleared'
    assert _get(port, 'live/version?scene=room')[1]['busy'] == {}
    with pytest.raises(urllib.error.HTTPError) as x:
        _post(port, 'live/busy', {'text': 'x'})
    assert x.value.code == 400 and b'who' in x.value.read()


def test_a_note_nobody_answers_says_its_taker_may_be_stuck(stage, monkeypatch):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.2)
    monkeypatch.setattr(PR, 'STUCK_S', 0.8)
    port = stage['port']
    start = _get(port, 'live/inbox?who=film')[1]['last']
    t = _take(port, 'film', start)
    _voice(port, text='where are the drums')
    t.join()
    _post(port, 'live/cmd?scene=room', {'type': 'ack'})            # an automatic "got it" is not an answer
    time.sleep(1.2)
    says = _says(port)
    assert says[-1] == 'film has not answered for 0 minutes. It may be stuck.'
    assert _get(port, 'live/version?scene=room')[1]['receipt']['stuck'] == ['film']
    ev = [e for e in _get(port, f'live/inbox?who=film&since={start}')[1]['events'] if e['type'] == 'session_stuck']
    assert ev and ev[-1]['who'] == ['film'] and ev[-1]['text'] == 'where are the drums'
    assert 'looks stuck: film' in OPS['stage_presence']()


@pytest.mark.parametrize('how', ['answered', 'busy', 'answered by another'])
def test_no_stuck_warning_for_an_answer_or_a_busy_line(stage, monkeypatch, how):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 0.2)
    monkeypatch.setattr(PR, 'STUCK_S', 0.8)
    port = stage['port']
    start = _get(port, 'live/inbox?who=film')[1]['last']
    ts = [_take(port, 'film', start), _take(port, 'hq', start)]
    if how == 'busy':
        OPS['stage_busy'](who='film', text='rendering, about 5 min')
    _voice(port)
    for t in ts:
        t.join()
    if how != 'busy':                                               # the note was for one of them; the other only listens
        _post(port, 'live/cmd?scene=room', {'type': 'say', 'text': 'on it', 'from': 'hq' if 'another' in how else 'film'})
    time.sleep(1.2)
    assert not [s for s in _says(port) if 'stuck' in s]
    assert _get(port, 'live/version?scene=room')[1]['receipt']['stuck'] == []


def test_the_ear_prints_notes_logs_the_rest_and_survives_a_restart(stage, monkeypatch, tmp_path):
    monkeypatch.setattr(PR, 'HEARD_WAIT_S', 5)
    monkeypatch.setattr(EAR, 'WAIT_S', 0.3)
    port = stage['port']
    out, folder = [], tmp_path / 'ear'
    EAR.listen('film', port=port, folder=folder, out=out.append, polls=1)   # starts at now: nothing old is printed
    assert out == [] and (folder / 'mark').read_text().split()[0] == str(PR.SEQ[0])
    _voice(port, text='hello from the room')
    EAR.listen('film', port=port, folder=folder, out=out.append, polls=2)   # a watcher started again resumes from the mark
    assert out == [f'[room] VOICE #{PR.INBOX[-1]["id"]}: hello from the room']
    assert 'voice_in' in (folder / 'ear.log').read_text(encoding='utf-8')
    assert 'film' in _get(port, 'live/version?scene=room')[1]['listening']
    PR.reset()                                                      # a server restart: the inbox counts from 0 again,
    monkeypatch.setattr(PR, 'STARTED', PR.STARTED + 5)              # here to the same count as before
    _voice(port, file='voice/n2.webm', text='after the restart')
    EAR.listen('film', port=port, folder=folder, out=out.append, polls=2)
    assert out[-1].endswith('after the restart')


def test_the_ear_once_exits_after_the_first_printed_line(stage, monkeypatch, tmp_path):
    monkeypatch.setattr(EAR, 'WAIT_S', 0.3)
    port = stage['port']
    out = []
    EAR.listen('film', port=port, folder=tmp_path, out=out.append, polls=1)
    _voice(port, text='one')
    _voice(port, file='voice/n2.webm', text='two')
    assert EAR.listen('film', port=port, folder=tmp_path, out=out.append, once=True, polls=50) == 0
    assert [o.split(': ')[-1] for o in out] == ['one', 'two']


RECEIPTS = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'receipts.js').as_uri()


def _note_text(receipt, busy):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    js = (f"import('{RECEIPTS}').then((m) => console.log(JSON.stringify(m.noteText({json.dumps(receipt)}, "
          f"{json.dumps(busy)}))));")
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_card_says_heard_by_busy_and_stuck():
    note = {'scene': 'room', 'note_id': 4, 'by': ['film', 'unnamed'], 'answered': False, 'stuck': []}
    film_busy = {'film': {'text': 'rendering the drums', 'age_s': 3, 'left_s': 300}}
    assert _note_text(None, {}) is None
    assert _note_text(note, {}) == ['heard by film, an unnamed agent', '', False]
    assert _note_text(note, film_busy) == ['heard by film, an unnamed agent', 'film: rendering the drums', False]
    assert _note_text(None, film_busy) == ['film is busy', 'rendering the drums', False]
    assert _note_text({**note, 'stuck': ['film']}, {}) == ['film looks stuck', 'no answer for 2 minutes', True]
    # an answered note does not borrow another session's busy line
    assert _note_text({**note, 'by': ['hq'], 'answered': True}, film_busy) == ['heard by hq', '', False]
