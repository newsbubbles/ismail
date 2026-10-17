"""The tuning table every instrument takes (ledger:M231): cents per pitch class, per exact note, per string and fret,
on top of a whole-instrument offset, added up. Each instrument kind moves its notes by the table, analyze_tuning
reads the result, a corrective table written as minus the measurement brings the part back, and an instrument
without a table renders as it always did."""
import os
import re
import shutil
import tempfile

import numpy as np
import pytest

from ismail import analysis as A
from ismail import api, instruments, tuning
from ismail.voices.guitar import electric

OPS = api.OPS
SR = A.ASR
TONE = {'type': 'synth', 'oscs': [{'wave': 'triangle', 'phase': 0.0}], 'amp_env': {'a': 0.005, 'd': 0.1, 's': 1.0, 'r': 0.05}}


@pytest.fixture(autouse=True)
def calm_board(tmp_path, monkeypatch):
    """The renders here are a few notes long: they run on a board of their own, never held back by the machine's."""
    from ismail import machine
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [])


@pytest.fixture
def proj():
    d = tempfile.mkdtemp(prefix='ismail_tuning_table_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=2)
    yield p
    shutil.rmtree(d, ignore_errors=True)


def pitch(inst, notes, cents=0.0, sr=SR):
    """The median steady pitch (float MIDI) of the notes an instrument plays, as analyze_tuning reads them."""
    y = instruments.render_instrument(instruments.normalize(inst), notes, int(sr * 2.5), None, 120.0, sr,
                                      cents=cents).mean(0)
    rows, _ = A.steady_pitches(y, sr)
    assert rows, 'no steady pitch read'
    return float(np.median([r['midi'] for r in rows]))


# ------------------------------------------------------------------ the table


def test_the_table_is_written_in_one_readable_form_and_the_parts_add():
    t = tuning.normalize({'all': -3, 'pitch_class': {'Eb': 10, 4: -14}, 'notes': {'F#3': 12, 60: 0, 'Bb2': 5},
                          'strings': {6: {'open': 2, 'all': -5, 3: 4}, '1': 0}})
    assert t == {'all': -3.0, 'pitch_class': {'D#': 10.0, 'E': -14.0}, 'notes': {'A#2': 5.0, 'F#3': 12.0},
                 'strings': {'6': {'all': -5.0, '0': 2.0, '3': 4.0}}}
    assert tuning.normalize(t) == t                                  # normal form in, the same out
    assert tuning.cents(t, 64) == -3 - 14                            # all + pitch class (E4)
    assert tuning.cents(t, 54) == -3 + 12                            # all + exact note (F#3)
    assert tuning.cents(t, 51, string=6, fret=3) == -3 + 10 - 5 + 4  # + the string's all + its fret
    assert tuning.normalize(-14) == {'all': -14.0} and tuning.normalize({'notes': {'C4': 0}}) is None
    m = tuning.merge(t, {'notes': {'Gb3': None}, 'strings': {'6': {'open': None}}, 'all': None})
    assert m == {'pitch_class': {'D#': 10.0, 'E': -14.0}, 'notes': {'A#2': 5.0}, 'strings': {'6': {'all': -5.0, '3': 4.0}}}


def test_a_scale_is_a_whole_tuning_system_on_keys_in_a_row():
    q = tuning.normalize({'scale': {'edo': 24, 'root': 'C4'}})
    assert q == {'scale': {'edo': 24, 'period': 1200.0, 'root': 'C4'}}
    assert [tuning.cents(q, m) for m in (59, 60, 61, 62, 84)] == [50.0, 0.0, -50.0, -100.0, -1200.0]
    # a 7-step maqam on seven keys in a row, the root on D3; the other parts still add per key
    mq = tuning.normalize({'scale': {'steps': [0, 200, 350, 500, 700, 900, 1050], 'root': 'D3'}, 'notes': {'E3': 5}})
    assert [round(tuning.intended_cents(dict(TONE, tuning=mq), m) - tuning.intended_cents(TONE, 50), 1)
            for m in range(50, 58)] == [0, 200, 355, 500, 700, 900, 1050, 1200]
    assert tuning.normalize({'scale': {'edo': 19, 'period': 1901.96}})['scale']['period'] == 1901.96
    assert 'scale 24 equal steps per 1200 c from C4' in tuning.describe(q)[0]
    assert tuning.merge(mq, {'scale': None}) == {'notes': {'E3': 5.0}}
    for bad, says in (({'edo': 0}, 'a whole number of equal steps'), ({'edo': 12, 'steps': [0]}, 'one of them'),
                      ({'steps': [100, 200]}, 'start at 0'), ({'steps': [0, 1300]}, 'below the period'),
                      ({'edo': 19, 'root': 'X'}, 'scale.root')):
        with pytest.raises(tuning.TuningError, match=re.escape(says)):
            tuning.normalize({'scale': bad})


def test_the_intended_pitch_is_what_the_instrument_plays():
    """intended_cents is the pitch a key is meant to sound at: a measurement checks against it, not 12-TET."""
    inst = dict(TONE, tuning={'scale': {'edo': 24, 'root': 'A3'}, 'all': 7})
    for m in (57, 58, 61):
        want = tuning.intended_cents(inst, m)
        got = (pitch(inst, [(0.0, m, 0.8, 100), (1.0, m, 0.8, 100)]) - 69) * 100
        assert abs(got - want) < 3, (m, got, want)
    kit = {'type': 'kit', 'map': {'C1': {'type': 'tom', 'tuning': {'all': 5}}}, 'tuning': {'notes': {'C1': -20}}}
    assert tuning.offset_cents(kit, 24) == -15 and tuning.intended_cents(kit, 24) == 100 * (24 - 69) - 15
    assert tuning.intended_cents({'type': 'synth'}, 69) == 0 and tuning.offset_cents({'all': 4}, 60) == 4


@pytest.mark.parametrize('bad, says', [
    ({'notes': {'H3': 1}}, 'not a note: write a name with its octave'),
    ({'notes': {'E': 1}}, "use 'pitch_class'"),
    ({'pitch_class': {'E3': 1}}, "use 'notes'"),
    ({'all': 1300}, 'more than an octave: give -1200..1200'),
    ({'F#3': 3}, "put note entries under 'notes'"),
    ([40, 45, 50], 'go in params.tuning'),
    ({'strings': {'0': 1}}, '1 = the highest string'),
    ({'notes': {'C4': 'sharp'}}, 'give cents as a number'),
])
def test_a_bad_table_says_what_to_write(bad, says):
    with pytest.raises(tuning.TuningError, match=re.escape(says)):
        tuning.normalize(bad)
    with pytest.raises(instruments.InstrumentError, match='tuning'):
        instruments.normalize(dict(TONE, tuning=bad))


def test_without_a_table_nothing_changes():
    notes = [(0.0, 60, 0.4, 100), (0.5, 64, 0.4, 90)]
    plain = instruments.render_instrument(instruments.normalize(TONE), notes, SR, None, 120.0, SR)
    empty = instruments.render_instrument(instruments.normalize(dict(TONE, tuning={'all': 0})), notes, SR, None,
                                          120.0, SR)
    assert np.array_equal(plain, empty) and 'tuning' not in instruments.normalize(dict(TONE, tuning={}))


# ------------------------------------------------------------------ every kind moves its notes


KINDS = [
    ('synth', TONE),
    ('sampler', {'type': 'sampler', 'sound': 'tone', 'root': 'C4'}),
    ('one-shot sampler', {'type': 'sampler', 'sound': 'tone', 'root': 'C4', 'pitch_track': False, 'one_shot': True}),
    ('inline code', {'type': 'code', 'tail': 0.05, 'code': 'def voice(freq, t, vel, gate, sr):\n'
                     '    return np.sin(2*np.pi*freq*t) * (t < gate)'}),
    ('tom', {'type': 'tom', 'pitch_start': 220.0, 'pitch_end': 220.0, 'decay': 0.6, 'click': 0.0}),
]


@pytest.mark.parametrize('kind, inst', KINDS, ids=[k for k, _ in KINDS])
def test_every_kind_moves_its_notes_by_the_table(kind, inst):
    t = np.arange(SR * 2) / SR
    instruments.set_resolvers(lambda n: np.vstack([np.sin(2 * np.pi * 261.6256 * t)] * 2), None)
    notes = [(0.0, 57, 0.5, 100), (0.6, 57, 0.5, 100), (1.2, 57, 0.5, 100)]
    base = pitch(inst, notes)
    tuned = pitch(dict(inst, tuning={'all': 10, 'pitch_class': {'A': 30}}), notes)
    assert abs((tuned - base) * 100 - 40) < 3, (kind, base, tuned)
    # a kit entry moves the piece it maps, and adds to the piece's own table
    kit = {'type': 'kit', 'map': {'A3': dict(inst, tuning={'all': 10})}, 'tuning': {'notes': {'A3': -50}}}
    assert abs((pitch(kit, notes) - base) * 100 + 40) < 3, kind


def test_a_drum_synth_moves_its_tone_and_keeps_its_decay():
    hit = instruments.normalize({'type': 'snare'})
    a = instruments.render_drum(hit, 100, SR, seed=1)
    b = instruments.render_drum(hit, 100, SR, seed=1, cents=1200)
    assert len(a) == len(b)                                   # same length: only the tone moved
    k = instruments.normalize({'type': 'kick'})
    assert np.array_equal(instruments.render_drum(k, 100, SR, 1, cents=0.0), instruments.render_drum(k, 100, SR, 1))


def test_the_electric_guitar_reads_strings_and_frets():
    """Poly mode puts each note on a string: a table entry for string 6 fret 1 moves that note and leaves a note
    on another string sample for sample as it was."""
    tun = electric.KINDS['strat']['tuning']                   # Eb tuning, low to high: string 6 is MIDI 39
    low = [(0.0, 40, 1.0, 100)]                               # string 6, fret 1
    mid = [(0.0, 57, 1.0, 100)]                               # string 3 (MIDI 54), fret 3
    assert electric._string_for(40, tun, 22) == 0 and electric._string_for(57, tun, 22) == 3
    t = tuning.normalize({'strings': {'6': {'1': 30}, '3': {'all': -20}}})
    fn = tuning.cents_fn(t)
    seen = []

    def spy(m, string=None, fret=None):
        seen.append((m, string, fret))
        return fn(m, string, fret)
    n = int(SR * 1.5)
    kw = dict(beat0=4.0, humanize_ms=0.0)
    plain = electric.perform(low, n, SR, **kw)
    moved = electric.perform(low, n, SR, cents_of=spy, **kw)
    assert seen == [(40, 6, 1)]
    r0, _ = A.steady_pitches(plain, SR)
    r1, _ = A.steady_pitches(moved, SR)
    assert abs((r1[0]['midi'] - r0[0]['midi']) * 100 - 30) < 4, (r0, r1)
    seen.clear()
    other = electric.perform(mid, n, SR, cents_of=spy, **kw)
    assert seen == [(57, 3, 3)]
    r2, _ = A.steady_pitches(other, SR)
    r3, _ = A.steady_pitches(electric.perform(mid, n, SR, **kw), SR)
    assert abs((r2[0]['midi'] - r3[0]['midi']) * 100 + 20) < 4, (r2, r3)


@pytest.mark.parametrize('voice', ['emily', 'rhodes', 'toms'])
def test_the_sampled_performers_take_the_table(voice, tmp_path, monkeypatch):
    """Synthetic samples (a 220 Hz tone, or a tom-shaped hit), so nothing is downloaded: the note moves by the
    cents its table gives it."""
    import soundfile as sf
    from ismail import voices
    mod = voices.load(voice)
    tt = np.arange(int(1.5 * 48000)) / 48000
    f = str(tmp_path / 'a3.wav')
    sf.write(f, 0.5 * np.sin(2 * np.pi * 220.0 * tt) * np.exp(-tt / 0.8), 48000)
    if voice == 'emily':
        monkeypatch.setattr(mod, '_index', lambda: ({57: {l: {1: f} for l in ('p', 'mp', 'mf', 'f')}}, {}))
    elif voice == 'rhodes':
        monkeypatch.setattr(mod, '_index', lambda: {57: {k: f for k in range(1, 6)}})
    else:
        monkeypatch.setattr(mod, 'hit', lambda f0, v, sr, *a, **k: np.vstack(
            [0.5 * np.sin(2 * np.pi * f0 * np.arange(sr) / sr) * np.exp(-np.arange(sr) / (0.6 * sr))] * 2))
    if hasattr(mod, '_cache'):
        monkeypatch.setattr(mod, '_cache', {})
    notes = [(0.0, 57, 1.0, 100)]
    kw = dict(humanize_ms=0.0) if voice != 'rhodes' else dict(humanize_ms=0.0, detune_cents=0.0, spread=0.0)

    def read(y):
        y = np.asarray(y)
        rows, _ = A.steady_pitches(y.mean(0) if y.ndim == 2 else y, SR)
        return rows[0]['midi']
    a = read(mod.perform(notes, int(SR * 1.5), SR, **kw))
    b = read(mod.perform(notes, int(SR * 1.5), SR, cents_of=lambda m, string=None, fret=None: 35.0, **kw))
    assert abs((b - a) * 100 - 35) < 3, (voice, a, b)


def test_a_performer_that_takes_no_table_refuses_it_rather_than_playing_untuned():
    inst = instruments.normalize({'type': 'code', 'voice': 'kit70', 'tuning': {'all': 5}})
    with pytest.raises(instruments.InstrumentError, match="voice 'kit70' .* takes no tuning table"):
        instruments.render_instrument(inst, [(0.0, 36, 0.5, 100)], SR, None, 120.0, SR)


# ------------------------------------------------------------------ the op, and analyze_tuning reading it


def cents_read(out, note):
    return [int(c) for c in re.findall(rf'\b{re.escape(note)} ([+-]\d+) c', out)]


def test_a_detuned_table_is_measured_and_a_corrective_table_brings_it_back(proj):
    api.track_add(proj, 'lead', instrument=TONE)
    api.notes_write(proj, 'lead', 1, '0 E3 0.9; 1 A3 0.9; 2 E4 0.9; 3 C4 0.9; 4 E3 0.9; 5 G3 0.9; 6 E4 0.9')
    out = OPS['instrument_tuning'](proj, 'lead', set={'pitch_class': {'E': 40}})
    assert 'pitch_class E +40' in out and 'played pitches: E3 +40, G3 +0, A3 +0, C4 +0, E4 +40' in out, out
    assert 'tuning table (instrument_tuning)' in OPS['project_info'](proj)
    OPS['render'](proj, stems=True)
    out = OPS['analyze_tuning'](proj, source='track:lead', worst=8)
    got = cents_read(out, 'E3') + cents_read(out, 'E4')
    assert len(got) >= 4 and all(abs(c - 40) <= 3 for c in got), out
    assert not cents_read(out, 'A3') and not cents_read(out, 'C4'), out       # the other notes stay in tune
    # the correction is minus what was measured, per note; it adds to the pitch class entry
    OPS['instrument_tuning'](proj, 'lead', set={'notes': {'E3': -40, 'E4': -40}})
    OPS['render'](proj, stems=True)
    out = OPS['analyze_tuning'](proj, source='track:lead')
    assert re.search(r'lead: in tune \(median [+-]\d c', out), out
    # clear removes it, and the reply says how to write one
    out = OPS['instrument_tuning'](proj, 'lead', clear=True)
    assert '(no tuning table' in out and 'tuning' not in api._load(proj).track('lead')['instrument']


def test_the_song_tuning_is_every_instruments_target_and_a_track_table_adds_on_top(proj):
    api.track_add(proj, 'a', instrument=TONE)
    api.track_add(proj, 'b', instrument=TONE)
    for t in ('a', 'b'):
        api.notes_write(proj, t, 1, '0 E3 0.9; 1 A3 0.9; 2 E3 0.9; 3 A3 0.9')
    out = OPS['instrument_tuning'](proj, set={'scale': {'edo': 24, 'root': 'A3'}, 'pitch_class': {'E': 30}})
    assert out.startswith('the song tuning (changed):') and 'every instrument inherits it' in out, out
    # track b takes the song's +30 c back off E3 (five quarter tones below A3: +250 c from 12-TET)
    out = OPS['instrument_tuning'](proj, 'b', set={'notes': {'E3': -30}})
    assert 'song (inherited): scale 24 equal steps' in out and 'played pitches: E3 +250, A3 +0' in out, out
    d = api._load(proj).d
    assert tuning.project_intended_cents(d, 'a', 52) == 100 * (57 - 69) - 250 + 30
    assert tuning.project_intended_cents(d, 'b', 52) == 100 * (57 - 69) - 250
    assert tuning.project_intended_cents(d, 'nope', 57) == -1200                # the song's tuning alone
    OPS['render'](proj, stems=True)
    for t in ('a', 'b'):
        rows, _ = A.steady_pitches(A.load(os.path.join(proj, 'renders', 'stems', t + '.wav')), SR)
        assert len(rows) == 4, (t, rows)
        for r in rows:
            key = 52 if r['midi'] < 56 else 57                      # E3 sounds near 54.5, A3 at 57
            want = tuning.project_intended_cents(d, t, key)
            assert abs((r['midi'] - 69) * 100 - want) < 3, (t, r, want)
    # the song's table changes the render cache key, and clearing it brings 12-TET back
    out = OPS['instrument_tuning'](proj, clear=True)
    assert '(no tuning table' in out and 'tuning' not in api._load(proj).d


def test_a_sample_kit_and_a_drum_synth_are_tuned_per_mapped_pitch(proj):
    OPS['sound_make'](proj, 'tone', dict(TONE), notes='0 C4 1', tail_sec=0.1)
    kit = {'type': 'kit', 'map': {'D1': {'type': 'sampler', 'sound': 'tone', 'one_shot': True, 'pitch_track': False},
                                  'E1': {'type': 'tom', 'pitch_start': 220.0, 'pitch_end': 220.0, 'decay': 0.6,
                                         'click': 0.0}}}
    api.track_add(proj, 'drums', instrument=kit)
    api.notes_write(proj, 'drums', 1, '0 D1 1; 1 E1 1; 2 D1 1; 3 E1 1', repeat=2)
    out = OPS['instrument_tuning'](proj, 'drums', set={'notes': {'D1': 45, 'E1': -30, 'G1': 5}})
    assert "NOTE ['G1'] are not in the kit map" in out, out
    OPS['render'](proj, stems=True)
    out = OPS['analyze_tuning'](proj, source='track:drums', worst=8)
    c4, a3 = cents_read(out, 'C4'), cents_read(out, 'A3')
    assert c4 and all(abs(c - 45) <= 3 for c in c4), out                   # the sample: playback rate
    assert a3 and all(abs(c + 30) <= 3 for c in a3), out                   # the drum synth: its tone


def test_the_op_reports_errors_with_what_to_do(proj):
    api.track_add(proj, 'lead', instrument=TONE)
    with pytest.raises(api.OpError, match=r"tuning not changed: notes 'H3': not a note"):
        OPS['instrument_tuning'](proj, 'lead', set={'notes': {'H3': 10}})
    with pytest.raises(api.OpError, match='more than an octave'):
        OPS['instrument_tuning'](proj, 'lead', set={'all': -1500})
    out = OPS['instrument_tuning'](proj, 'lead', set={'strings': {'1': 3}})
    assert 'strings entries do nothing on this instrument' in out, out
    api.track_add(proj, 'kit', instrument='preset:kit70')
    with pytest.raises(api.OpError, match=r"voice 'kit70' plays the whole part itself.*clear=True"):
        OPS['instrument_tuning'](proj, 'kit', set={'all': 5})
    assert 'tuning' not in api._load(proj).track('kit')['instrument']
    api.track_add(proj, 'gtr', instrument='preset:strat70_lead')
    out = OPS['instrument_tuning'](proj, 'gtr', set={'strings': {'6': {'all': -5}}})
    assert 'strings included' in out and 'do nothing' not in out, out
    with pytest.raises(api.OpError, match='audio-only track'):
        api.track_add(proj, 'vox')
        OPS['instrument_tuning'](proj, 'vox')
