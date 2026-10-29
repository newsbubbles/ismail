"""Per-note feel from a reference (ledger:M364): analyze_feel reads planted timing and levels on a beat line that
follows a drifting take, and feel_from_ref writes them into a track's notes (dry run, undo, a slot with no onset)."""
import json
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api

SR = 22050
OPS = api.OPS
BPM = 120.0
OFFSET = 0.5                       # bar 1 starts here, in the project and in the take
BARS = 16
SHARE = 0.54                       # the take's offbeat 8th, as a share of its beat
PATTERN = [12.0, -12.0, 6.0, -6.0]  # ms on the offbeat 8th of beats 1-4, on top of SHARE (mean 0)
POS_DB = {0: 3.0, 1: -2.0, 2: 1.0, 3: -1.0}   # beat 1-4 level, dB
OFF_DB = -3.0                      # every offbeat 8th, dB
BAR_DB = [0.0, -6.0]               # alternate bars quieter: the bar's own loudness stays out of the reading
MISSING = (8, 2)                   # bar 9 (0-based 8), beat 3: no offbeat note in the take


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp(prefix='ismail_feel_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


def tone(f, gain_db, sr=SR):
    t = np.arange(int(0.12 * sr)) / sr
    return 10 ** (gain_db / 20) * 0.3 * np.sin(2 * np.pi * f * t) * np.minimum(1, t / 0.002) * np.exp(-t * 35)


def beat_times(drift=True):
    """The take's beats from bar 1: tempo 120 BPM +-3 % over a slow sine (48 beats a cycle), or steady."""
    n = BARS * 4 + 2
    k = np.arange(n)
    tempo = BPM * (1 + 0.03 * np.sin(2 * np.pi * k / 48) * drift)
    return OFFSET + np.r_[0, np.cumsum(60 / tempo)]


def take(drift=True, share=SHARE, pattern=PATTERN, pos_db=POS_DB, off_db=OFF_DB, bar_db=BAR_DB, missing=MISSING,
         gap=()):
    """An 8th-note train on a (drifting) beat line: beats on the beat, the offbeat 8th at `share` of its beat plus
    `pattern` ms, levels by position and bar."""
    B = beat_times(drift)
    rng = np.random.default_rng(1)
    y = rng.normal(0, 1e-4, int((B[-1] + 1) * SR))

    def put(h, t):
        a = int(round(t * SR))
        y[a:a + len(h)] += h[:len(y) - a]
    for k in range(BARS * 4):
        bar, beat = divmod(k, 4)
        if bar + 1 in gap:
            continue
        g = bar_db[bar % len(bar_db)]
        put(tone(1500, pos_db[beat] + g), B[k])
        if (bar, beat) != missing:
            span = B[k + 1] - B[k]
            put(tone(1800, off_db + g), B[k] + share * span + pattern[beat] / 1000)
    return y.astype(np.float32)


def make_song(tmp, **kw):
    ref = os.path.join(tmp, 'take.wav')
    sf.write(ref, take(**kw), SR)
    proj = os.path.join(tmp, 'song')
    api.project_new(proj, bpm=BPM, length_bars=BARS, offset_sec=OFFSET, reference=ref)
    OPS['track_add'](proj, 'lead', instrument={'type': 'synth'})
    OPS['notes_write'](proj, 'lead', 1, '; '.join(f"{b / 2:g} A5 0.5 60" for b in range(8)), bars=1, repeat=BARS)
    return proj


def notes(proj):
    with open(os.path.join(proj, 'project.json'), encoding='utf8') as f:
        return json.load(f)['tracks']['lead']['notes']


def planted_rel():
    """dB against the bar's mean, by position (beats 0-3, offbeat 'o'), for a bar with every note."""
    vals = [POS_DB[b] for b in range(4)] + [OFF_DB] * 4
    mu = np.mean(vals)
    out = {str(b + 1): POS_DB[b] - mu for b in range(4)}
    out['&'] = OFF_DB - mu
    return out


def test_analyze_feel_reads_the_planted_feel_on_a_drifting_take(tmp):
    proj = make_song(tmp)
    from ismail.api_feel import _Read
    P = api._load(proj)
    r = _Read(P, 'ref', [2, 15], 2, (600.0, 4000.0), 50.0)
    m = r.measures()
    lo, hi = r.tempo()
    assert lo < 118 and hi > 122, (lo, hi)                   # it followed the drift
    assert not r.lost and not r.unread, (r.lost, r.unread)
    assert abs(m['offbeat'] - SHARE) < 0.006, m
    assert abs(m['timing_sd'] - np.std(PATTERN)) < 2.0, m
    want = planted_rel()
    for p, v in want.items():
        assert abs(m['by_pos'][p] - v) < 1.0, (p, m['by_pos'])
    # read as 16ths: the empty e and a slots hold no note, the & is the same
    m4 = _Read(P, 'ref', [2, 15], 4, (600.0, 4000.0), 50.0).measures()
    assert m4['notes'] == m['notes'] == 14 * 8 - 1 and abs(m4['offbeat'] - SHARE) < 0.006, m4
    out = OPS['analyze_feel'](proj, source='ref', bars=[2, 15])
    assert 'offbeat 8th at 0.54' in out and 'timing spread between the beats' in out, out
    assert 'could not follow' not in out, out


def test_analyze_feel_against_names_what_reads_as_midi(tmp):
    proj = make_song(tmp)
    flat = os.path.join(tmp, 'flat.wav')
    sf.write(flat, take(drift=False, share=0.5, pattern=[0, 0, 0, 0], pos_db={b: 0.0 for b in range(4)}, off_db=0.0,
                        bar_db=[0.0], missing=None), SR)
    out = OPS['analyze_feel'](proj, source=flat, bars=[2, 15], against='ref')
    assert 'difference' in out and 'reads as MIDI-like' in out, out
    assert 'timing too even' in out and 'levels too even' in out and 'placed by the grid' in out, out
    assert 'accents by rule' in out and 'feel_from_ref(' in out, out
    # the take against itself: nothing reads as MIDI
    out = OPS['analyze_feel'](proj, source='ref', bars=[2, 15], against='ref')
    assert 'no difference reads as MIDI-like' in out, out


def test_feel_from_ref_writes_the_takes_offsets_and_levels(tmp):
    proj = make_song(tmp)
    out = OPS['feel_from_ref'](proj, 'lead', bars=[2, 15], level=1.0)
    assert 'moved' in out and 'undo()' in out and 'record 0.54' in out, out
    assert '1 on slots with no clear onset' in out, out
    assert 'velocity_sens' in out, out                      # a synth: velocity is not linear amplitude
    want = planted_rel()
    errs, lev = [], {p: [] for p in want}
    for n in notes(proj):
        bar, beat = divmod(n[0], 4)
        if not 1 <= bar <= 14:
            assert len(n) == 4 and n[3] == 60, n              # outside the bars: untouched
            continue
        k, half = int(beat), beat % 1 > 0
        if half and (bar, k) == MISSING:
            assert len(n) == 4 and n[3] == 60, n              # no onset in the take: left alone
            continue
        off = n[4] if len(n) > 4 else 0.0
        target = (SHARE - 0.5) * 500 + PATTERN[k] if half else 0.0
        errs.append(off - target)
        if bar != MISSING[0]:
            lev['&' if half else str(k + 1)].append(20 * np.log10(n[3] / 60))
    errs = np.abs(errs)
    assert np.median(errs) < 3 and errs.max() < 8, (np.median(errs), errs.max())
    for p, v in want.items():
        assert abs(np.mean(lev[p]) - v) < 1.0, (p, np.mean(lev[p]), v)
    # read back: the notes keep their beats, the nudge is the offset
    assert '@' in OPS['notes_read'](proj, 'lead', bars=[3, 3]), out


def test_dry_run_writes_nothing_and_undo_takes_it_back(tmp):
    proj = make_song(tmp)
    before = notes(proj)
    out = OPS['feel_from_ref'](proj, 'lead', bars=[2, 15], dry_run=True)
    assert 'would move' in out and 'dry run: nothing written' in out and ' ms, vel 60 -> ' in out, out
    assert notes(proj) == before
    OPS['feel_from_ref'](proj, 'lead', bars=[2, 15])
    assert notes(proj) != before
    OPS['undo'](proj)
    assert notes(proj) == before


def test_bad_arguments_point_forward(tmp):
    proj = make_song(tmp)
    with pytest.raises(api.OpError, match='per_beat is 2'):
        OPS['feel_from_ref'](proj, 'lead', bars=[2, 3], per_beat=3)
    with pytest.raises(api.OpError, match='band is'):
        OPS['analyze_feel'](proj, source='ref', bars=[2, 3], band=[4000, 600])
    OPS['notes_clear'](proj, 'lead', bars=[16, 16])
    with pytest.raises(api.OpError, match='notes_read'):
        OPS['feel_from_ref'](proj, 'lead', bars=[16, 16])


def test_a_silent_stretch_is_named_and_the_beat_is_found_again(tmp):
    proj = make_song(tmp, gap=(6, 7))
    out = OPS['feel_from_ref'](proj, 'lead', bars=[2, 15])
    assert 'beats it could not follow (8 of 56' in out and 'LOST THE BEAT from bar 6 beat 1' in out, out
    assert '16 on beats not followed' in out, out
    errs = []
    for n in notes(proj):
        bar, beat = divmod(n[0], 4)
        if 7 <= bar <= 14 and bar != MISSING[0] and beat % 1:      # from the first bar after the gap
            errs.append(abs((n[4] if len(n) > 4 else 0.0) - ((SHARE - 0.5) * 500 + PATTERN[int(beat)])))
    assert len(errs) == 28 and np.median(errs) < 3 and max(errs) < 8, errs
