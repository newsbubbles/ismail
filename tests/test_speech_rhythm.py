"""Speech on a rhythm (ismail.speech, sound_speak(rhythm=)): SAPI event logs, syllable splits and placement on 16ths,
without a speech engine."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api, speech
from ismail.api import OPS, OpError

# 'night' (one syllable), then 'engineer': three vowels (21, 15, 28) with consonants between
LOG = ['W\t0\tNight', 'P\t0\t50\t7', 'P\t50\t100\t33', 'P\t150\t150\t13', 'P\t300\t50\t30',
       'W\t999\tengineer,', 'P\t350\t90\t21', 'P\t440\t60\t33', 'P\t500\t60\t29', 'P\t560\t40\t15', 'P\t600\t60\t33',
       'P\t660\t120\t28', 'P\t780\t70\t38', 'P\t850\t150\t7']


def test_speak_log_times_come_from_the_phone_durations():
    words, end = speech.parse_sapi_log(LOG)
    assert [w for w, _ in words] == ['night', 'engineer']
    assert words[0][1][0][0] == pytest.approx(0.05)            # the silence before it is skipped
    assert words[1][1][0][0] == pytest.approx(0.35)            # the event's own position (999 ms) is not used
    assert end == pytest.approx(1.0)


def test_speak_syllables_start_at_the_last_consonant_before_each_vowel():
    words, _ = speech.parse_sapi_log(LOG)
    assert speech.syllable_starts(words[1][1], 3) == pytest.approx([0.35, 0.50, 0.60])
    assert speech.syllable_starts(words[1][1], 1) == pytest.approx([0.35])
    with pytest.raises(speech.RhythmError):
        speech.syllable_starts(words[1][1], 2)


def test_speak_rhythm_spec_parses_and_refuses_bad_input():
    assert speech.parse_rhythm('Night 0; engineer 12 14 -') == [('night', (0.0,)), ('engineer', (12.0, 14.0, None))]
    for bad in ('night', 'night x', '', 'night -'):
        with pytest.raises(speech.RhythmError):
            speech.parse_rhythm(bad)


def test_speak_rhythm_sets_each_syllable_on_its_16th():
    sr, bpm = 8000, 120
    words, end = speech.parse_sapi_log(LOG)
    y = np.zeros(int(end * sr))
    for t in (0.05, 0.35, 0.50, 0.60):                         # a click 10 ms into each syllable
        y[int((t + 0.01) * sr)] = 1.0
    out, first = speech.place(words, y, sr, end, speech.parse_rhythm('night -2; engineer 4 6 8'), bpm)
    assert first == -2
    s16 = 60 / bpm / 4
    peaks = np.flatnonzero(out > 0.5) / sr
    want = [(p - first) * s16 + 0.018 for p in (-2, 4, 6, 8)]  # 8 ms of lead-in is kept before each cut
    assert peaks == pytest.approx(want, abs=2 / sr)


def test_speak_rhythm_through_the_op_with_a_fake_engine(monkeypatch):
    import sys
    from ismail import api_sound
    monkeypatch.setattr(api_sound, '_has_sapi', lambda: True)   # patching sys.platform breaks soundfile off Windows

    def fake_tts(text, voice, rate, base):
        sf.write(base + '.wav', np.random.default_rng(0).standard_normal(22050) * 0.3, 22050)
        with open(base + '.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG))

        class R:
            returncode, stderr, stdout = 0, '', ''
        return base + '.wav', 'fake', R
    monkeypatch.setattr(api_sound, '_tts', fake_tts)
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=120, length_bars=2)
        out = OPS['sound_speak'](p, 'line', 'night engineer', rhythm='night 0; engineer 4 6 8')
        assert 'starts on 16th 0' in out and 'line.timings.json' in out, out
        assert os.path.exists(os.path.join(p, 'sounds', 'line.timings.json'))
        with pytest.raises(OpError):
            OPS['sound_speak'](p, 'bad', 'night', rhythm='night')
        with pytest.raises(OpError, match='not in the spoken words'):
            OPS['sound_speak'](p, 'bad', 'night', rhythm='day 0')
    finally:
        shutil.rmtree(d, ignore_errors=True)
