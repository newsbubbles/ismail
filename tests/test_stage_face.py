"""Mouth tracks from a voice and its words (ismail/stage/face.py)."""
import json

import numpy as np
import pytest

from ismail.api import OPS, OpError
from ismail.stage import face, perform
from ismail.stage import world as W
from test_stage import FakePage, stage  # noqa: F401  (the fixture)

SR = perform.SR


def voice(spans, seconds=2.0):
    """Silence with a buzzy 180 Hz tone (a stand-in voice) over each (start, end)."""
    t = np.arange(int(seconds * SR)) / SR
    x = np.random.default_rng(0).normal(0, 1e-4, len(t))
    for a, b in spans:
        on = (t >= a) & (t < b)
        x[on] += 0.3 * np.sign(np.sin(2 * np.pi * 180 * t[on])) * np.hanning(on.sum())
    return x.astype(np.float32)


@pytest.fixture(autouse=True)
def no_rhubarb(monkeypatch, tmp_path):
    """The word tests run without Rhubarb, even on a machine that has it."""
    monkeypatch.setenv('ISMAIL_RHUBARB', str(tmp_path / 'no_rhubarb_here'))


FAKE_RHUBARB = '''
import json, sys
a = sys.argv[1:]
out = a[a.index('-o') + 1]
cues = [{'start': 0.0, 'end': 0.5, 'value': 'X'}, {'start': 0.5, 'end': 0.6, 'value': 'A'},
        {'start': 0.6, 'end': 0.8, 'value': 'D'}, {'start': 0.8, 'end': 0.9, 'value': 'D'},
        {'start': 0.9, 'end': 1.1, 'value': 'F'}, {'start': 1.1, 'end': 2.0, 'value': 'X'}]
json.dump({'mouthCues': cues}, open(out, 'w'))
'''


@pytest.fixture
def rhubarb(tmp_path, monkeypatch):
    """A stand-in Rhubarb (a .py run by this Python) answering fixed cues; returns the commands it was run with."""
    exe = tmp_path / 'rhubarb' / 'rhubarb.py'
    exe.parent.mkdir()
    exe.write_text(FAKE_RHUBARB, encoding='utf-8')
    monkeypatch.setenv('ISMAIL_RHUBARB', str(exe))
    seen, real = [], face.subprocess.run

    def run(cmd, **kw):
        seen.append(cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(face.subprocess, 'run', run)
    return seen


def at(tr, t, unit):
    return tr['frames'][int(round((t - tr['start']) * tr['fps']))][tr['units'].index(unit)]


@pytest.fixture(params=['dict', 'spelling'])
def source(request, monkeypatch):
    if request.param == 'spelling':
        monkeypatch.setattr(face, '_DICT', {})
    return request.param


def test_a_word_starts_with_closed_lips_and_opens_on_its_vowel(source):
    x = voice([(0.5, 1.1)])
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=x)
    assert tr['shapes'][0][:2] == [0.5, 'A']
    assert max(at(tr, t, 'mouthPressLeft') for t in (0.5, 0.53, 0.56)) > 0.2
    assert max(at(tr, t / 100, 'jawOpen') for t in range(55, 105)) > 0.25
    assert at(tr, 0.2, 'jawOpen') == 0 and at(tr, 1.9, 'jawOpen') < 0.02     # rest before and after
    assert tr['shapes'][-1][1] == 'X'


def test_oo_puckers_and_ee_spreads(source):
    x = voice([(0.2, 0.7), (1.0, 1.5)])
    tr = face.track(None, [{'word': 'zoo', 'start': 0.2, 'end': 0.7}, {'word': 'see', 'start': 1.0, 'end': 1.5}],
                    x=x)
    assert at(tr, 0.55, 'mouthPucker') > 0.4 and at(tr, 0.55, 'mouthStretchLeft') < 0.05
    assert at(tr, 1.35, 'mouthStretchLeft') > 0.15 and at(tr, 1.35, 'mouthPucker') < 0.1
    assert [s[1] for s in tr['shapes']].count('X') == 2                        # the pause between words rests


def test_the_mouth_leads_the_sound():
    x = voice([(0.5, 1.0)])
    w = [{'word': 'ma', 'start': 0.5, 'end': 1.0}]
    early = face.track(None, w, x=x, lead=0.1)
    late = face.track(None, w, x=x, lead=0.0)
    assert at(early, 0.45, 'mouthPressLeft') > 0.1 and at(late, 0.45, 'mouthPressLeft') == 0


def test_without_words_the_jaw_follows_loudness():
    x = voice([(0.6, 1.2)])
    tr = face.track(None, None, x=x)
    assert at(tr, 0.3, 'jawOpen') < 0.05 and at(tr, 0.9, 'jawOpen') > 0.2 and at(tr, 1.7, 'jawOpen') < 0.05
    assert tr['shapes'] == [] and tr['source']['words'] == 0


def test_a_stretch_of_a_recording_keeps_its_clock_and_writes_small_json(tmp_path):
    x = voice([(1.2, 1.6)], seconds=3.0)
    tr = face.track(None, [{'word': 'no', 'start': 1.2, 'end': 1.6}, {'word': 'later', 'start': 2.6, 'end': 2.9}],
                    start=1.0, end=2.0, x=x)
    assert tr['start'] == 1.0 and tr['seconds'] == 1.0 and len(tr['frames']) == 30
    assert all(s[0] < 2.0 for s in tr['shapes']) and tr['source']['words'] == 1
    p = face.write(tr, tmp_path / 'face.json')
    back = json.loads(open(p, encoding='utf-8').read())
    assert back['units'] == tr['units'] and all(0 <= v <= 1 for row in back['frames'] for v in row)
    lines = face.readout(tr, 0.1).splitlines()
    assert len(lines) == 3 and lines[1].startswith('shape') and len(lines[1]) == 6 + 10
    with pytest.raises(ValueError):
        face.track(None, None, start=5.0, x=x)


def test_unknown_words_are_read_from_their_spelling(monkeypatch):
    monkeypatch.setattr(face, '_DICT', {})
    phs, src = face.phonemes('Blorf!')
    assert src == 'spelling' and phs[0] == 'B' and 'F' in phs
    assert list(face.word_shapes('blorf', 0, 1)[0][2:]) == ['A', 'B']                # B: closed lips
    assert face.phonemes('...') == ([], 'spelling') and face.word_shapes('', 0, 1) == []


def test_a_voice_goes_on_a_persons_mouth_at_a_song_time(stage, tmp_path):
    import soundfile as sf
    sc = stage['scenes']
    OPS['stage_world'](scene='room', world={'actors': {'singer': 'body_c'}})
    wav = tmp_path / 'vocal.wav'
    sf.write(wav, voice([(1.0, 1.6)], seconds=3.0), SR)
    with pytest.raises(OpError, match="'nobody' is not a person of room"):
        OPS['stage_face'](scene='room', person='nobody', at=10, audio=str(wav))
    with pytest.raises(OpError, match='one of them'):
        OPS['stage_face'](scene='room', person='singer', at=10)
    page = FakePage(stage['port'], 'room')
    try:
        out = OPS['stage_face'](scene='room', person='singer', at=12.0, audio=str(wav), start=0.5, end=2.5,
                                words=[{'word': 'hello', 'start': 1.0, 'end': 1.6}])
        assert "singer's mouth: 2.0 s of vocal.wav from 0.5 s, at song 12.0 s, from 1 words" in out
        assert 'said: hello' in out and 'the page has it' in out and '\nshape ' in out
        assert page.seen[-1]['type'] == 'face_reload' and page.seen[-1]['faces']['singer'][0]['file'] == 'singer_12000.json'
        tr = json.loads((sc / 'room' / 'faces' / 'singer_12000.json').read_text(encoding='utf-8'))
        assert tr['start'] == 0.5 and len(tr['frames']) == 60 and tr['source']['audio'] == 'vocal.wav'
        f = W.load_world(sc, 'room')['faces']['singer']
        assert [(e['file'], e['at']) for e in f] == [('singer_12000.json', 12.0)]
        out = OPS['stage_face'](scene='room', person='singer', at=13.0, audio=str(wav))   # overlaps: replaces it
        assert 'replaced singer_12000.json' in out and 'from loudness only' in out
        OPS['stage_face'](scene='room', person='singer', at=30.0, audio=str(wav))         # later: beside it
        assert [e['file'] for e in W.load_world(sc, 'room')['faces']['singer']] == ['singer_13000.json', 'singer_30000.json']
        assert OPS['stage_face'](scene='room', person='singer', clear=True, file='singer_13000.json').startswith(
            'removed 1 mouth track(s) of singer')
        OPS['stage_face'](scene='room', person='singer', clear=True)
        assert 'singer' not in W.load_world(sc, 'room')['faces']
        assert (sc / 'room' / 'faces' / 'singer_13000.json').is_file()                     # the files stay
        with pytest.raises(OpError, match='singer has no mouth tracks'):
            OPS['stage_face'](scene='room', person='singer', clear=True)
    finally:
        page.stop = True
        page.t.join(5)


def test_a_take_gives_its_own_voice_and_words(stage):
    import soundfile as sf
    sc = stage['scenes']
    OPS['stage_world'](scene='room', world={'actors': {'singer': 'body_c'}})
    td = sc / 'room' / 'takes' / 'take_a'
    td.mkdir(parents=True)
    sf.write(td / 'audio.wav', voice([(0.4, 0.9)], seconds=1.5), SR)
    (td / 'voice.json').write_text(json.dumps({'file': 'audio.wav', 'text': 'Moo',
                                               'words': [{'word': 'Moo', 'start': 0.4, 'end': 0.9}]}), encoding='utf-8')
    out = OPS['stage_face'](scene='room', person='singer', at=2.0, take='take_a')
    assert 'of audio.wav' in out and 'said: Moo' in out and 'the page takes it at its next load' in out
    assert '\nshape ....AF' in out                                     # closed lips for the M, then the pucker
    with pytest.raises(OpError, match="take 'nope' in room has no audio"):
        OPS['stage_face'](scene='room', person='singer', at=2.0, take='nope')


def test_rhubarb_gives_the_shapes_when_it_is_here(rhubarb):
    x = voice([(0.5, 1.1)])
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=x)
    assert tr['source']['engine'] == 'rhubarb'
    assert [s[:2] for s in tr['shapes']] == [[0.0, 'X'], [0.5, 'A'], [0.6, 'D'], [0.9, 'F'], [1.1, 'X']]
    assert tr['shapes'][1][3] == 'mama' and tr['shapes'][0][3] == ''
    assert max(at(tr, t, 'mouthPressLeft') for t in (0.5, 0.53, 0.56)) > 0.2 and at(tr, 1.05, 'mouthPucker') > 0.3
    cmd = rhubarb[-1]
    assert cmd[cmd.index('-r') + 1] == 'phonetic' and '-d' not in cmd
    assert cmd[cmd.index('--extendedShapes') + 1] == 'GHX'


def test_the_english_recognizer_is_guided_by_the_words(rhubarb):
    x = voice([(0.5, 1.1)])
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=x, engine='rhubarb_english')
    cmd = rhubarb[-1]
    assert tr['source']['engine'] == 'rhubarb_english' and cmd[cmd.index('-r') + 1] == 'pocketSphinx'
    assert '-d' in cmd


def test_a_stretch_keeps_the_recordings_clock_with_rhubarb(rhubarb):
    x = voice([(1.5, 2.1)], seconds=3.0)
    tr = face.track(None, None, start=1.0, end=3.0, x=x)
    assert tr['start'] == 1.0 and [s[:2] for s in tr['shapes'][:2]] == [[1.0, 'X'], [1.5, 'A']]


def test_without_rhubarb_auto_uses_the_words_and_asking_for_it_is_an_error():
    x = voice([(0.5, 1.1)])
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=x)
    assert tr['source']['engine'] == 'words' and 'rhubarb_error' not in tr['source']
    with pytest.raises(ValueError, match='not on this machine'):
        face.track(None, None, x=x, engine='rhubarb')
    with pytest.raises(ValueError, match='engine'):
        face.track(None, None, x=x, engine='lips')


def test_a_failing_rhubarb_falls_back_to_the_words_and_says_why(tmp_path, monkeypatch):
    exe = tmp_path / 'rhubarb.py'
    exe.write_text('import sys; sys.stderr.write("bad audio"); sys.exit(3)', encoding='utf-8')
    monkeypatch.setenv('ISMAIL_RHUBARB', str(exe))
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=voice([(0.5, 1.1)]))
    assert tr['source']['engine'] == 'words' and 'bad audio' in tr['source']['rhubarb_error']


def test_a_rhubarb_that_hears_nothing_leaves_it_to_the_words(tmp_path, monkeypatch):
    exe = tmp_path / 'rhubarb.py'
    exe.write_text("import json, sys; a = sys.argv; json.dump({'mouthCues': []}, open(a[a.index('-o') + 1], 'w'))",
                   encoding='utf-8')
    monkeypatch.setenv('ISMAIL_RHUBARB', str(exe))
    tr = face.track(None, [{'word': 'mama', 'start': 0.5, 'end': 1.1}], x=voice([(0.5, 1.1)]))
    assert tr['source']['engine'] == 'words' and 'no mouth shapes' in tr['source']['rhubarb_error']
    assert any(s[1] != 'X' for s in tr['shapes'])
