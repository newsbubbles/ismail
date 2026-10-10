"""Takes keep what the person said while recording (their own audio, or the performance they came from), and are
found by it."""
import json
import time

import pytest

from ismail.api import OPS, OpError
from ismail.stage import perform as P
from ismail.stage import server as S
from test_stage import _get, _post, stage  # noqa: F401  (the fixture)
from test_stage_perform import _raw, _voice_wav


def _wait(port, typ, scene='room'):
    for _ in range(100):
        got = [e for e in _get(port, f'live/events?scene={scene}&limit=500')[1]['events'] if e['type'] == typ]
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f'no {typ} event')


def _take(stage, tid, **meta):
    d = stage['scenes'] / 'room' / 'takes' / tid
    d.mkdir(parents=True, exist_ok=True)
    (d / 'meta.json').write_text(json.dumps({'id': tid, **meta}), encoding='utf-8')
    return d


def test_a_takes_audio_is_transcribed_when_it_lands(stage, monkeypatch, tmp_path):
    port = stage['port']
    wav = tmp_path / 'v.wav'
    _voice_wav(wav)                       # voice from 0.8 to 1.4 s
    monkeypatch.setattr(S, 'stt_words', lambda a, n: {'text': 'bartender take three', 'words': [
        {'word': 'person_a', 'start': 0.3, 'end': 1.0}, {'word': 'take', 'start': 1.0, 'end': 1.2}, {'word': 'three', 'start': 1.2, 'end': 1.8}]})
    _take(stage, '20261005_174800_person_bartender', name='person_bartender', seconds=12.0)
    _raw(port, 'voice/in?scene=room&kind=take&take=20261005_174800_person_bartender&seconds=12', wav.read_bytes(), 'audio/wav')
    ev = _wait(port, 'take_voice')[-1]
    assert ev['take'] == '20261005_174800_person_bartender' and ev['text'] == 'bartender take three'
    assert ev['words'][0][1] == pytest.approx(0.8 - P.MARGIN_S, abs=0.03)       # snapped onto the voice
    v = json.loads((stage['scenes'] / 'room' / 'takes' / '20261005_174800_person_bartender' / 'voice.json').read_text(encoding='utf-8'))
    assert v['file'] == 'audio.wav' and v['voice'] == pytest.approx([0.8, 1.4], abs=0.03)
    out = OPS['stage_takes'](scene='room', query='three')
    assert '20261005_174800_person_bartender' in out and 'said: "bartender take three"' in out and 'three@' in out


def test_a_take_from_a_follow_carries_its_performances_words(stage):
    sd = stage['scenes'] / 'room'
    perf = sd / 'performances' / 'p1'
    perf.mkdir(parents=True)
    (perf / 'perf.json').write_text(json.dumps({'clips': [
        {'n': 1, 'at': 1.0, 'words': [{'word': 'before', 'start': 0.0, 'end': 0.3}]},
        {'n': 2, 'at': 5.0, 'words': [{'word': 'glass', 'start': 0.5, 'end': 0.9}, {'word': 'up', 'start': 1.0, 'end': 1.2}]}]}),
        encoding='utf-8')
    _take(stage, '20261005_175000_sam', name='person_b', **{'for': 'person_b'}, kept=True,
          performance='p1', perf_shift=3.0, seconds=10.0)
    out = OPS['stage_takes'](scene='room', query='glass', kept=True)
    # Follow clock 5.5 s is 2.5 s into the take (it began 3 s into the Follow); "before" fell before the take
    assert 'glass@2.5s' in out and 'said: "glass up"' in out and 'KEPT' in out
    assert 'no takes' in OPS['stage_takes'](scene='room', query='before')


def test_takes_are_named_noted_and_filtered(stage, monkeypatch, tmp_path):
    _take(stage, '20261005_175100_a', name='player', seconds=4.0)
    _take(stage, '20261005_175200_b', name='lucy', seconds=6.0, kept=True)
    out = OPS['stage_take_note'](scene='room', take='20261005_175100_a', label='player nod', note='too fast at the end', at=3.2, sender='film agent')
    assert "label 'player nod', 1 notes" in out
    lst = OPS['stage_takes'](scene='room')
    assert lst.index('20261005_175200_b') < lst.index('20261005_175100_a')          # newest first
    assert '[player nod]' in lst and 'note @3.2s (film agent): too fast at the end' in lst
    assert '20261005_175100_a' in OPS['stage_takes'](scene='room', query='too fast')
    assert '20261005_175200_b' not in OPS['stage_takes'](scene='room', person='player')
    assert '20261005_175100_a' not in OPS['stage_takes'](scene='room', kept=True)
    with pytest.raises(OpError, match='no take'):
        OPS['stage_take_note'](scene='room', take='nope', note='x')
    with pytest.raises(OpError, match='label= and/or note='):
        OPS['stage_take_note'](scene='room', take='20261005_175100_a')
    # an older take with audio and no words yet
    d = _take(stage, '20261005_170000_old', name='player', seconds=2.0)
    wav = tmp_path / 'v.wav'
    _voice_wav(wav)
    (d / 'audio.wav').write_bytes(wav.read_bytes())
    monkeypatch.setattr(S, 'stt_words', lambda a, n: {'text': 'old one', 'words': [{'word': 'old', 'start': 0.8, 'end': 1.0}, {'word': 'one', 'start': 1.0, 'end': 1.4}]})
    assert '"old one" (2 words)' in OPS['stage_take_transcribe'](scene='room', take='20261005_170000_old')
    with pytest.raises(OpError, match='no audio'):
        OPS['stage_take_transcribe'](scene='room', take='20261005_175200_b')


def _dance(d, seconds=8.0, hz=30.0, bounce_hz=2.0):
    """A dancer bouncing at bounce_hz (2 Hz: on the beat at 120 BPM), hands swinging with it, a sharp hit each bounce."""
    import math
    with open(d / 'frames.jsonl', 'w', encoding='utf-8') as fh:
        for i in range(int(seconds * hz)):
            t = i / hz
            ph = 2 * math.pi * bounce_hz * t
            y = 1.6 + 0.04 * abs(math.sin(ph / 2)) ** 0.5
            hand = lambda s: {'g': 'none', 'palm': 'down', 'j': [[s * 0.3, 1.1 + 0.1 * math.sin(ph), -0.2, 0, 0, 0, 1, 0.02]]}
            fh.write(json.dumps({'t': round(t, 4), 'head': [0, y, 0, 0, 0, 0, 1], 'left': hand(-1), 'right': hand(1),
                                 'body': {'hips': [0, 1.0, 0, 0, 0, 0, 1]}}) + '\n')


def test_takes_keep_time_loop_and_warp_onto_the_beat(stage):
    d = _take(stage, '20261006_120000_dance', name='person_c', performance='p1', trim=[0, 8], label='dance')
    _dance(d)
    out = OPS['stage_take_sync'](scene='room', takes=['20261006_120000_dance'], bpm=120)
    import re
    assert abs(float(re.search(r'own_bpm ([\d.]+)', out).group(1)) - 120) < 1 and 'pulse_beats 1,' in out, out
    assert abs(float(re.search(r'rate ([\d.]+)', out).group(1)) - 1) < 0.01
    loops = OPS['stage_take_sync'](scene='room', takes=['20261006_120000_dance'], bpm=120, loops=True, bars=[2])
    assert '(2 bars)' in loops and 'pulse 1 beat 0.0% off' in loops, loops
    with pytest.raises(OpError, match='no take'):
        OPS['stage_take_loop'](scene='room', take='nope', name='x', start=0, end=1)
    out = OPS['stage_take_loop'](scene='room', take='20261006_120000_dance', name='loop_a', bpm=120, bars=[2])
    tid = out.split(':')[0]
    m = json.loads((stage['scenes'] / 'room' / 'takes' / tid / 'meta.json').read_text(encoding='utf-8'))
    assert 'best window: 2 bars' in out and abs(m['seconds'] - 4.0) < 0.1
    assert not {'performance', 'trim', 'label'} & set(m) and m['from_take'] == '20261006_120000_dance'   # no borrowed voice
    with pytest.raises(OpError, match='name'):
        OPS['stage_take_loop'](scene='room', take='20261006_120000_dance', name='a b', start=0, end=2)
    out = OPS['stage_take_warp'](scene='room', take=tid, name='warp_a', bpm=126, bars=2)
    wid = out.split(':')[0]
    w = json.loads((stage['scenes'] / 'room' / 'takes' / wid / 'meta.json').read_text(encoding='utf-8'))
    assert w['seconds'] == round(8 * 60 / 126, 4) and w['frames'] == round(w['seconds'] * 30) and w['beat_warp']['bpm'] == 126
    assert all(abs(g - round(g)) < 0.01 for g in w['beat_warp']['hits_on_grid_units'][1:-1])   # hits on beats
    with pytest.raises(OpError, match='more than 2x'):
        OPS['stage_take_warp'](scene='room', take=tid, name='warp_b', bpm=120, bars=8)
    assert wid in OPS['stage_takes'](scene='room')
