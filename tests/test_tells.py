"""The tell ledger (ledger:M305): a fault the person keeps naming is logged with its measured gap each round, and a
tell named twice whose gap holds shows NOT MOVING."""
import json

import numpy as np
import pytest

from ismail import api
from ismail import sounddesign as SD
from ismail.api import OpError
from ismail.api_sound import ZOOM_HINT

OPS = api.OPS


@pytest.fixture
def song(tmp_path):
    d = tmp_path / 'song_a'
    d.mkdir()
    return str(d)


def _two_namings(song):
    OPS['tell_add'](song, id='tone-a', what='the tone is wrong', words='sounds off', source='exam_1')
    OPS['tell_heard'](song, id='tone-a', words='still off', source='voice note n1')


def test_a_tell_named_twice_whose_gap_holds_shows_not_moving(song):
    _two_namings(song)
    show = OPS['tell_show'](song)
    assert 'NOT MOVING (never measured)' in show
    OPS['tell_measure'](song, id='tone-a', metric='band share, dB', ours=-2.0, target=-9.0, round='r1')
    out = OPS['tell_measure'](song, id='tone-a', metric='band share, dB', ours=-2.3, target=-9.0, round='r2')
    assert 'did not shrink' in out and 'NOT MOVING' in out
    show = OPS['tell_show'](song)
    assert 'tone-a  [open]  named 2x   NOT MOVING (gap did not shrink: band share, dB)' in show
    assert show.splitlines()[-1].startswith('NOT MOVING: tone-a')
    OPS['tell_measure'](song, id='tone-a', metric='band share, dB', ours=-8.0, target=-9.0, round='r3')
    assert OPS['tell_show'](song).splitlines()[-1] == 'NOT MOVING: none'


def test_a_tell_named_once_is_not_flagged(song):
    OPS['tell_add'](song, id='tone-b', what='too clean', words='too clean', source='exam_1')
    assert OPS['tell_show'](song).splitlines()[-1] == 'NOT MOVING: none'


def test_closing_without_a_passed_verdict_is_refused(song):
    _two_namings(song)
    with pytest.raises(OpError, match='no "passed" verdict after its last measure'):
        OPS['tell_status'](song, id='tone-a', status='closed')
    OPS['tell_judged'](song, id='tone-a', verdict='passed', source='exam_2')
    OPS['tell_measure'](song, id='tone-a', metric='m', ours=1.0, gap=1.0)
    with pytest.raises(OpError, match='no "passed" verdict after its last measure'):
        OPS['tell_status'](song, id='tone-a', status='closed')
    OPS['tell_judged'](song, id='tone-a', verdict='passed', source='exam_3')
    assert 'status tone-a' in OPS['tell_status'](song, id='tone-a', status='closed')
    assert 'tone-a' not in OPS['tell_show'](song)
    assert '[closed]' in OPS['tell_show'](song, open_only=False)
    assert OPS['tell_check'](song).startswith('ok:')


def _write_log(song, rows):
    import os
    os.makedirs(os.path.join(song, 'notes'), exist_ok=True)
    with open(os.path.join(song, 'notes', 'tells.jsonl'), 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


ADD = {'seq': 1, 'at': '2026-10-20T10:00:00Z', 'ev': 'add', 'id': 'tone-a', 'when': '2026-10-20T09:00:00Z',
       'what': 'the tone is wrong', 'said': 'sounds off', 'source': 'exam_1'}


def test_check_fails_on_an_unknown_id(song):
    _write_log(song, [ADD, {'seq': 2, 'at': '2026-10-20T11:00:00Z', 'ev': 'heard', 'id': 'tone-x',
                            'when': '2026-10-20T10:30:00Z', 'said': 'off again', 'source': 'n2'}])
    with pytest.raises(OpError, match=r"unknown id 'tone-x'.*Next:"):
        OPS['tell_check'](song)
    with pytest.raises(OpError, match='nothing was written'):
        OPS['tell_heard'](song, id='tone-a', words='again', source='n3')


def test_check_fails_on_times_out_of_order(song):
    _write_log(song, [ADD, {'seq': 2, 'at': '2026-10-20T09:30:00Z', 'ev': 'heard', 'id': 'tone-a',
                            'when': '2026-10-20T09:20:00Z', 'said': 'off again', 'source': 'n2'}])
    with pytest.raises(OpError, match='times out of order. Next:'):
        OPS['tell_check'](song)


def test_check_fails_on_a_malformed_row(song):
    _write_log(song, [ADD, {'seq': 2, 'at': '2026-10-20T11:00:00Z', 'ev': 'measure', 'id': 'tone-a',
                            'when': '2026-10-20T11:00:00Z', 'metric': 'm', 'ours': 'loud'}])
    with pytest.raises(OpError, match="'ours' is not a number"):
        OPS['tell_check'](song)


def _desc(level, width, cent):
    n = len(SD.THIRD_OCT)
    return {'bands': np.full(n, level, float), 'env': np.linspace(0, -30, 20), 'centroid': cent, 'width': width,
            'peak': np.full(n, 12.0)}


def test_measure_takes_its_number_from_a_sound_compare_reply(song, tmp_path):
    reply = SD.compare_text(_desc(-10.0, 0.30, 2400.0), _desc(-14.0, 0.10, 1800.0), 'ours', 'record')[1] + ZOOM_HINT
    OPS['tell_add'](song, id='tone-a', what='the tone is wrong')
    out = OPS['tell_measure'](song, id='tone-a', metric='centroid', reply=reply, round='r1')
    assert 'ours +2400.00, target +1800.00, gap +600.00' in out
    out = OPS['tell_measure'](song, id='tone-a', metric='sc distance', pull='distance', reply=reply)
    assert 'target +0.00' in out
    saved = tmp_path / 'compare_reply.txt'
    saved.write_text(reply, encoding='utf-8')
    out = OPS['tell_measure'](song, id='tone-a', metric='band 1000 Hz', pull='1000H', reply=str(saved))
    assert 'ours -10.00, target -14.00, gap +4.00' in out
    with pytest.raises(OpError, match='it has: distance, bands'):
        OPS['tell_measure'](song, id='tone-a', metric='loudness', reply=reply)
    rows = [json.loads(l) for l in (tmp_path / 'song_a' / 'notes' / 'tells.jsonl').read_text().splitlines()]
    assert rows[1]['from'] == 'sound_compare' and rows[1]['pulled'] == 'centroid' and rows[3]['source'] == str(saved)
    assert OPS['tell_check'](song).startswith('ok:')


def test_import_adopts_an_existing_ledger_only_into_an_empty_one(song, tmp_path):
    src = tmp_path / 'old' / 'tells.jsonl'
    src.parent.mkdir()
    src.write_text(json.dumps(ADD) + '\n', encoding='utf-8')
    assert 'imported 1 events, 1 tells' in OPS['tell_import'](song, path=str(src))
    with pytest.raises(OpError, match='already has events'):
        OPS['tell_import'](song, path=str(src))
