"""A Follow is a performance: its voice clips reach the server a chunk at a time and come back as words on the Follow
clock, snapped onto the measured voice; the agent's ops for it; stage_batch."""
import json
import time
import urllib.request

import numpy as np
import pytest
import soundfile as sf

from ismail.api import OPS, OpError
from ismail.stage import perform as P
from ismail.stage import server as S
from test_stage import FakePage, _get, _post, stage  # noqa: F401  (the fixture)


def _raw(port, path, data, ctype='audio/webm'):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/{path}', data=data, headers={'Content-Type': ctype}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _events(port, types, scene='room'):
    return [e for e in _get(port, f'live/events?scene={scene}&limit=500')[1]['events'] if e['type'] in types]


def _voice_wav(path, lead=0.8, voice=0.6, tail=0.4, sr=16000):
    """Room tone, then a voiced stretch (a 180 Hz buzz), then room tone again."""
    rng = np.random.default_rng(0)
    n = int(sr * (lead + voice + tail))
    x = rng.normal(0, 0.002, n)
    t = np.arange(int(sr * voice)) / sr
    x[int(sr * lead):int(sr * lead) + len(t)] += 0.3 * np.sign(np.sin(2 * np.pi * 180 * t))
    sf.write(path, x.astype(np.float32), sr)


def test_words_that_start_in_silence_move_onto_the_voice(tmp_path):
    f = tmp_path / 'clip_1.wav'
    _voice_wav(f)
    v = P.voiced(P.pcm(f))
    assert P.voice_span(v) == pytest.approx([0.8, 1.4], abs=0.03)
    # whisper's way: the first word starts 0.45 s early in the room tone, the last ends 0.3 s late
    w = P.snap_words([{'word': 'hey', 'start': 0.35, 'end': 1.0}, {'word': 'you', 'start': 1.0, 'end': 1.7},
                      {'word': 'uh', 'start': 1.75, 'end': 1.9}], v)
    assert w[0]['start'] == pytest.approx(0.8 - P.MARGIN_S, abs=0.03) and w[0]['end'] == 1.0
    assert w[1]['start'] == 1.0 and w[1]['end'] == pytest.approx(1.4 + P.MARGIN_S, abs=0.03)
    assert w[2]['unvoiced'] and w[2]['start'] == 1.75


def test_a_clip_arrives_in_chunks_and_comes_back_as_words_on_the_follow_clock(stage, monkeypatch, tmp_path):
    port, perf = stage['port'], '20261004_220000_person_b'
    wav = tmp_path / 'v.wav'
    _voice_wav(wav)
    data = wav.read_bytes()
    seen = {}

    def fake_stt(audio, filename):
        seen['bytes'], seen['name'] = len(audio), filename
        return {'text': 'hey you', 'words': [{'word': 'hey', 'start': 0.35, 'end': 1.0}, {'word': 'you', 'start': 1.0, 'end': 1.7}]}
    monkeypatch.setattr(S, 'stt_words', fake_stt)
    _post(port, f'perf/meta?scene=room&perf={perf}', {'id': perf, 'person': 'person_b', 'markers': [{'t': 2.0, 'label': 'legs', 'by': 'agent'}]})
    half = len(data) // 2
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=2&seq=0', data[:half], 'audio/wav')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=2&seq=1', data[half:], 'audio/wav')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=2&end=1&at=12.5&seconds=1.8&by=agent', b'')
    d = stage['scenes'] / 'room' / 'performances' / perf
    assert (d / 'clip_2.wav').read_bytes() == data                       # the chunks, in order
    for _ in range(100):
        got = _events(port, {'perform_clip'})
        if got:
            break
        time.sleep(0.05)
    assert _events(port, {'perform_clip_in'})[-1]['clip'] == 2
    ev = got[-1]
    assert seen['bytes'] == len(data) and ev['text'] == 'hey you'
    # on the Follow clock: the clip began at 12.5 s; "hey" starts where the voice does, not in the room tone
    assert ev['words'][0][0] == 'hey' and ev['words'][0][1] == pytest.approx(12.5 + 0.8 - P.MARGIN_S, abs=0.03)
    meta = json.loads((d / 'perf.json').read_text(encoding='utf-8'))
    assert meta['person'] == 'person_b' and meta['clips'][0]['n'] == 2 and meta['clips'][0]['voice'] == pytest.approx([0.8, 1.4], abs=0.03)
    out = OPS['stage_performance'](scene='room')
    assert perf in out and 'marker 2.0 s: legs' in out and 'clip 2 at 12.5 s' in out and 'hey@13.' in out


def test_an_old_speech_server_still_gives_the_text(stage, monkeypatch, tmp_path):
    port, perf = stage['port'], 'p1'
    wav = tmp_path / 'v.wav'
    _voice_wav(wav)
    monkeypatch.setattr(S, 'stt_words', lambda a, n: {'text': 'hello', 'words': None})
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=1&seq=0', wav.read_bytes(), 'audio/wav')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=1&end=1&at=0&seconds=1.8', b'')
    for _ in range(100):
        got = _events(port, {'perform_clip'})
        if got:
            break
        time.sleep(0.05)
    assert got[-1]['text'] == 'hello' and 'restart speakwright' in got[-1]['words_missing']


def test_bad_names_are_refused(stage):
    with pytest.raises(urllib.error.HTTPError):
        _raw(stage['port'], 'voice/perf?scene=room&perf=../x&clip=1&seq=0', b'x')
    with pytest.raises(urllib.error.HTTPError):
        _raw(stage['port'], 'voice/perf?scene=nope&perf=p&clip=1&seq=0', b'x')


def test_perform_op_and_aloud(stage):
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_perform'](scene='room', action='next_clip')
        assert page.seen[-1]['type'] == 'perform' and page.seen[-1]['action'] == 'next_clip' and 'perform' in out
        OPS['stage_perform'](scene='room', action='mark', label='he reaches for the glass')
        assert page.seen[-1]['label'] == 'he reaches for the glass'
        OPS['stage_perform'](scene='room', action='stop')
        assert page.seen[-1]['action'] == 'stop'
        with pytest.raises(OpError, match='label'):
            OPS['stage_perform'](scene='room', action='mark')
        with pytest.raises(OpError, match="'state'"):
            OPS['stage_perform'](scene='room', action='cut')
        with pytest.raises(OpError, match='stage_perform'):
            OPS['stage_cmd'](scene='room', type='perform')
        OPS['stage_say'](scene='room', text='nice')
        assert 'aloud' not in page.seen[-1]
        OPS['stage_say'](scene='room', text='yes, keep going', aloud=True)
        assert page.seen[-1]['aloud'] is True
    finally:
        page.stop = True


class BatchPage(FakePage):
    """FakePage that runs a 'batch' command like live.js: each sub-command in order, stopping at a refused one."""

    def loop(self):
        while not self.stop:
            got = _get(self.port, f'live/cmd?scene={self.scene}&since={self.since}&wait=1')[1]
            self.since = got['last']
            for c in got['cmds']:
                self.seen.append(c)
                res = []
                for x in c.get('cmds', []):
                    if x['type'] in self.refuse:
                        res.append({'type': x['type'], 'ok': False, 'error': 'no object chair', 'ms': 1})
                        if c.get('stop_on_error', True):
                            break
                    else:
                        res.append({'type': x['type'], 'ok': True, 'ms': 1, 'result': {k: v for k, v in x.items() if k != 'type'}})
                _post(self.port, f'live/event?scene={self.scene}', {'type': 'cmd_done', 'cmd_id': c['id'], 'cmd': c['type'], 'ok': True, 'ms': 5,
                                                                    'result': res if c['type'] == 'batch' else {}})


def test_a_batch_sends_page_ops_together_and_keeps_the_order(stage):
    page = BatchPage(stage['port'], 'room')
    try:
        out = OPS['stage_batch'](scene='room', ops=[
            {'op': 'stage_follow_anchor', 'person': 'person_b', 'to': 'stool_3'},
            {'op': 'stage_perform', 'action': 'mark', 'label': 'legs now'},
            {'op': 'stage_world', 'world': {'actors': {'person_b': 'mh_man'}}},      # runs here, between
            {'op': 'stage_say', 'text': 'feet next', 'sender': 'film agent'},
        ])
        batches = [c for c in page.seen if c['type'] == 'batch']
        assert [[x['type'] for x in b['cmds']] for b in batches] == [['follow_anchor', 'perform'], ['say']]
        lines = out.splitlines()
        assert [ln.split(':')[0] for ln in lines] == ['[0] stage_follow_anchor', '[1] stage_perform', '[2] stage_world', '[3] stage_say']
        assert 'mh_man' in (stage['scenes'] / 'room' / 'world.json').read_text(encoding='utf-8')
    finally:
        page.stop = True


def test_a_failed_batch_stops_and_puts_its_files_back(stage):
    page = BatchPage(stage['port'], 'room', refuse={'say'})
    try:
        before = OPS['stage_world'](scene='room')
        with pytest.raises(OpError) as e:
            OPS['stage_batch'](scene='room', ops=[
                {'op': 'stage_world', 'world': {'actors': {'person_b': 'mh_man'}}},
                {'op': 'stage_say', 'text': 'one'},
                {'op': 'stage_perform', 'action': 'mark', 'label': 'never'},
            ])
        msg = str(e.value)
        assert '[1] stage_say: ERROR' in msg and 'not run' in msg and 'world.json' in msg
        assert OPS['stage_world'](scene='room') == before
    finally:
        page.stop = True


def test_a_batch_refuses_what_cannot_be_in_it(stage):
    with pytest.raises(OpError, match='not a stage op'):
        OPS['stage_batch'](scene='room', ops=[{'op': 'render'}])
    with pytest.raises(OpError, match='on its own'):
        OPS['stage_batch'](scene='room', ops=[{'op': 'stage_scene_go', 'name': 'attic'}])
    with pytest.raises(OpError, match='one scene'):
        OPS['stage_batch'](scene='room', ops=[{'op': 'stage_say', 'scene': 'attic', 'text': 'x'}])


def test_any_error_in_a_batch_rolls_back(stage, monkeypatch):
    from ismail.stage import world as W
    real = W.save_world

    def half_then_fail(d, scene, world):
        real(d, scene, world)
        raise KeyError('boom')
    page = BatchPage(stage['port'], 'room')
    try:
        before = OPS['stage_world'](scene='room')
        monkeypatch.setattr(W, 'save_world', half_then_fail)
        with pytest.raises(OpError, match=r"\[0\] stage_world: ERROR KeyError: 'boom'"):
            OPS['stage_batch'](scene='room', ops=[{'op': 'stage_world', 'world': {'actors': {'person_b': 'mh_man'}}}])
        monkeypatch.setattr(W, 'save_world', real)
        assert OPS['stage_world'](scene='room') == before
    finally:
        page.stop = True


def test_without_ffmpeg_a_webm_clip_keeps_its_text(stage, monkeypatch):
    port, perf = stage['port'], 'p2'
    monkeypatch.delenv('ISMAIL_FFMPEG', raising=False)
    monkeypatch.setattr(P.shutil, 'which', lambda name: None)
    monkeypatch.setattr(S, 'stt_words', lambda a, n: {'text': 'cut', 'words': [{'word': 'cut', 'start': 0.1, 'end': 0.4}]})
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=1&seq=0', b'not really opus', 'audio/webm')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=1&end=1&at=3&seconds=1', b'')
    for _ in range(100):
        got = _events(port, {'perform_clip'})
        if got:
            break
        time.sleep(0.05)
    ev = got[-1]
    assert ev['text'] == 'cut' and 'words' not in ev and ev['words_missing'].startswith('ffmpeg not found')


def test_saying_stop_ends_the_performance_and_its_words_reach_the_listeners(stage, monkeypatch, tmp_path):
    port, perf = stage['port'], '20261005_190000_person_couple_3_m'
    wav = tmp_path / 'v.wav'
    _voice_wav(wav)
    asked = []

    def fake_stt(audio, filename):
        asked.append(filename)
        return {'text': 'yo, you need to stop the recording', 'words': [{'word': 'yo,', 'start': 0.8, 'end': 1.0}]}
    monkeypatch.setattr(S, 'stt_words', fake_stt)
    since = _get(port, 'live/inbox?who=film')[1]['last']
    cmd0 = _get(port, 'live/cmd?scene=room&since=0')[1]['last']
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=3&seq=0', wav.read_bytes(), 'audio/wav')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=3&end=1&at=40&seconds=1.8&by=auto:%20pause', b'')
    for _ in range(100):
        cmds = [c for c in _get(port, f'live/cmd?scene=room&since={cmd0}')[1]['cmds'] if c['type'] == 'perform']
        if cmds:
            break
        time.sleep(0.05)
    assert cmds[-1]['action'] == 'stop' and cmds[-1]['by'] == 'voice: "stop the recording"' and cmds[-1]['perf'] == perf
    got = [e for e in _get(port, f'live/inbox?who=film&since={since}')[1]['events'] if e['type'] == 'perform_clip']
    assert got and got[-1]['text'] == 'yo, you need to stop the recording' and 'words' not in got[-1]
    # a clip the page heard nothing in is not sent to the speech server, and is not in the inbox
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=4&seq=0', wav.read_bytes(), 'audio/wav')
    _raw(port, f'voice/perf?scene=room&perf={perf}&clip=4&end=1&at=42&seconds=25&by=auto:%2025%20s&quiet=1', b'')
    for _ in range(100):
        if [e for e in _events(port, {'perform_clip'}) if e['clip'] == 4]:
            break
        time.sleep(0.05)
    assert asked == ['clip_3.wav']
    last = _get(port, f'live/inbox?who=film&since={since}')[1]['events']
    assert not [e for e in last if e['type'] == 'perform_clip' and e['clip'] == 4]


def test_stop_words():
    assert P.asks_stop('Okay. Stop the performance now') == 'Stop the performance'
    assert P.asks_stop('end performance') == 'end performance'
    assert P.asks_stop("all right, I'm done. I'm done") is None
    assert P.asks_stop('stop it, stop') is None and P.asks_stop(None) is None
