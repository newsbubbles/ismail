"""The clip check before a scene ships (stage_clip_check): a clip-check capture's 'probe:' lines read back into one line
per person (ismail/stage/clipcheck.py), and the op's arguments, its job and its reading of a finished capture."""
import json

import pytest

from ismail.api import OpError
from ismail.stage import capture as C
from ismail.stage import clipcheck as K
from ismail.stage import ops as O


def _line(t, people):
    return f'12:00:00 probe: {json.dumps({"t": t, "clipcheck": people, "ms": {"update_ms": 0.4}})}'


# three frames of a room: Jo's forearm in Mae on the second, his own upper arm in him on the third; the speaker's left
# foot 45 cm in the stage throughout; Mae clear; the bartender a statue
LOG = '\n'.join([
    '12:00:00 info: the room came in',
    _line(0.0, {'jo': {'clip': [], 'sink': {'l': 0, 'r': 0.4}, 'gms': 0.1, 'playing': True},
                'mae': {'clip': [], 'sink': {'l': 0, 'r': 0}, 'gms': 0.1},
                'speaker': {'clip': [['foot_l', 'ground', 45.2]], 'sink': {'l': 45.2, 'r': 1.5}, 'gms': 0.2},
                'bartender': {'none': 'shows no body (a statue: no start pose and nothing playing)'}}),
    _line(0.033, {'jo': {'clip': [['fore_r', 'mae', 5.8], ['hand_r', 'mae', 1.2]], 'sink': {'l': 0, 'r': 0}, 'gms': 0.1},
                  'mae': {'clip': [], 'sink': {'l': 0, 'r': 0}, 'gms': 0.1},
                  'speaker': {'clip': [['foot_l', 'ground', 45.3], ['foot_r', 'ground', 2.1]], 'sink': {'l': 45.3, 'r': 2.1}, 'gms': 0.2},
                  'bartender': {'none': 'shows no body (a statue: no start pose and nothing playing)'}}),
    '12:00:00 probe: {"t": 0.05, "cut short',                                  # a line cut short is skipped
    _line(0.067, {'jo': {'clip': [['upper_l', 'self', 3.0]], 'sink': {'l': 0, 'r': 0}, 'gms': 0.1},
                  'mae': {'clip': [], 'sink': {'l': 0, 'r': 0}, 'gms': 0.1},
                  'speaker': {'clip': [['foot_l', 'ground', 45.0]], 'sink': {'l': 45.0, 'r': 0}, 'gms': 0.2},
                  'bartender': {'none': 'shows no body (a statue: no start pose and nothing playing)'}}),
    '12:00:00 probe: {"t": 0.1, "pel": [0, 1, 0]}',                            # a plain probe line is not a clip check
])


def test_the_lines_become_one_row_per_person_with_frames_and_the_deepest():
    frames = K.probe_lines(LOG)
    assert [f['t'] for f in frames] == [0.0, 0.033, 0.067]
    rows = {r['person']: r for r in K.summarize(frames, ['speaker', 'jo', 'mae', 'bartender'])}
    jo, sp = rows['jo'], rows['speaker']
    assert jo['frames'] == 3 and jo['self'][:2] == [1, 3.0] and jo['other'][:2] == [1, 5.8] and jo['foot'][0] == 0
    assert jo['other'][2] == 'fore_r in mae at 0.033 s' and jo['verdict'] == 'FAIL'
    assert sp['foot'][:3] == [3, 45.3, 'foot_l at 0.033 s'] and sp['verdict'] == 'FAIL'
    assert rows['mae']['verdict'] == 'PASS' and rows['bartender']['verdict'] == 'NO BODY'
    assert list(rows) == ['speaker', 'jo', 'mae', 'bartender']


def test_the_table_says_pass_or_fail_per_line_and_why():
    t = K.table(K.summarize(K.probe_lines(LOG), ['speaker', 'jo', 'mae', 'bartender']), 'clip check room: ')
    lines = t.splitlines()
    assert lines[0] == 'clip check room: FAIL (2 of 4)'
    assert lines[1].split() == ['person', 'frames', 'arm>=2cm', 'self', 'deepest', 'cm', 'arm>=2cm', 'other', 'deepest',
                                'cm', 'foot', 'sunk>=2cm', 'deepest', 'cm', 'result']
    assert lines[2].split() == ['speaker', '3', '0', '-', '0', '-', '3', '45.3', 'FAIL']
    assert lines[3].split() == ['jo', '3', '1', '3.0', '1', '5.8', '0', '-', 'FAIL']
    assert lines[4].split() == ['mae', '3', '0', '-', '0', '-', '0', '-', 'PASS']
    assert lines[5].split()[-2:] == ['NO', 'BODY']
    assert 'speaker: deepest foot in the ground: foot_l at 0.033 s, 45.3 cm' in t
    assert 'jo: deepest arm in own body: upper_l at 0.067 s, 3.0 cm; arm in another: fore_r in mae at 0.033 s, 5.8 cm' in t
    assert 'bartender: shows no body' in t and 'ms a body a frame' in t
    assert K.table(K.summarize(K.probe_lines(LOG), ['mae'])).splitlines()[0] == 'PASS'


@pytest.fixture()
def room(tmp_path, monkeypatch):
    scenes = tmp_path / 'scenes'
    d = scenes / 'room'
    (d / 'takes' / 'dance').mkdir(parents=True)
    (d / 'scene.glb').write_bytes(b'glTF')
    (d / 'world.json').write_text(json.dumps({'actors': {'jo': 'bf_jo', 'mae': 'bf_mae'}}), encoding='utf-8')
    (d / 'takes' / 'dance' / 'frames.jsonl').write_text(''.join(json.dumps({'t': 1.0 + k / 10}) + '\n' for k in range(81)), encoding='utf-8')
    started = []
    monkeypatch.setattr(C, 'browser', lambda: 'browser.exe')
    monkeypatch.setattr(O.subprocess, 'Popen', lambda args, **kw: started.append(args))
    return scenes, started


def _job(scenes, name):
    return json.loads((scenes / 'room' / 'captures' / name / 'job.json').read_text())


def test_the_op_checks_everyone_still_for_a_few_frames_with_no_picture_kept(room):
    scenes, started = room
    r = O.stage_clip_check('room', scenes=str(scenes), name='c1', wait_s=0)
    assert 'still starting' in r and "job='c1'" in r
    job = _job(scenes, 'c1')
    assert job['clip_check'] == ['jo', 'mae'] and job['t0'] == 0 and job['t1'] == 0.5 and job['fps'] == 30
    assert job['video'] is False and job['keep_frames'] is False and 'probe' not in job
    assert started and started[0][-1].endswith('job.json')


def test_a_take_played_in_setup_sets_how_long_it_runs(room):
    scenes, _ = room
    setup = [{'type': 'actor_play', 'person': 'jo', 'take': 'dance', 'at_music': 0}]
    O.stage_clip_check('room', people=['jo'], setup=setup, scenes=str(scenes), name='c2', wait_s=0)
    job = _job(scenes, 'c2')
    assert job['clip_check'] == ['jo'] and job['t1'] == pytest.approx(8.0) and job['setup'] == setup
    O.stage_clip_check('room', people='jo, mae', t0=2, t1=3, scenes=str(scenes), name='c3', wait_s=0)
    assert _job(scenes, 'c3')['clip_check'] == ['jo', 'mae'] and _job(scenes, 'c3')['t1'] == 3


def test_the_op_refuses_what_it_cannot_check(room):
    scenes, _ = room
    with pytest.raises(OpError, match='not people in room: bob'):
        O.stage_clip_check('room', people=['bob'], scenes=str(scenes), wait_s=0)
    with pytest.raises(OpError, match='after t0'):
        O.stage_clip_check('room', t0=2, t1=1, scenes=str(scenes), wait_s=0)
    with pytest.raises(OpError, match='live commands'):
        O.stage_clip_check('room', setup=[{'person': 'jo'}], scenes=str(scenes), wait_s=0)
    with pytest.raises(OpError, match='no scene'):
        O.stage_clip_check('hall', scenes=str(scenes), wait_s=0)
    (scenes / 'room' / 'world.json').write_text('{"actors": {}}', encoding='utf-8')
    with pytest.raises(OpError, match='no people'):
        O.stage_clip_check('room', scenes=str(scenes), wait_s=0)
    assert not (scenes / 'room' / 'captures').exists()
    with pytest.raises(OpError, match='bad arguments'):
        O.stage_clip_check('room', scenes=str(scenes), colour='red')


def test_job_reads_a_finished_check_and_refuses_a_plain_capture(room):
    scenes, _ = room
    O.stage_clip_check('room', scenes=str(scenes), name='c4', wait_s=0)
    jd = scenes / 'room' / 'captures' / 'c4'
    (jd / 'log.txt').write_text(LOG, encoding='utf-8')
    C.write_json(jd / 'status.json', {'job': 'c4', 'state': 'done', 'frames': 15})
    t = O.stage_clip_check('room', job='c4', scenes=str(scenes))
    assert t.startswith('clip check room (c4, song 0 to 0.5 s, 3 frames): FAIL (1 of 2)')
    assert any(ln.split()[:2] == ['jo', '3'] and ln.endswith('FAIL') for ln in t.splitlines())
    C.write_json(jd / 'status.json', {'job': 'c4', 'state': 'failed', 'error': 'the room did not finish coming in'})
    with pytest.raises(OpError, match='did not finish coming in'):
        O.stage_clip_check('room', job='c4', scenes=str(scenes))
    (scenes / 'room' / 'captures' / 'shot').mkdir()
    C.write_json(scenes / 'room' / 'captures' / 'shot' / 'job.json', {'id': 'shot', 'probe': 'jo'})
    with pytest.raises(OpError, match='not a clip check'):
        O.stage_clip_check('room', job='shot', scenes=str(scenes))
    with pytest.raises(OpError, match='no capture'):
        O.stage_clip_check('room', job='nope', scenes=str(scenes))
