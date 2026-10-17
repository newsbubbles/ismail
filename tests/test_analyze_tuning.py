"""analyze_tuning (ledger:M231): every part of a render against the tuning grid. Synthetic stems with known pitches:
a part on the grid passes, two drum voices a quarter tone off are flagged with their cents, a pluck's sharp attack
is kept out of the steady pitch and reported as the attack, a fast run of 16ths is read (not skipped) and its sharp
attacks flagged, noise is skipped, and the grid moves with tuning_cents. Stems are written straight into renders/stems
(no render), so the file stays light."""
import os
import re
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api

SR = 22050
OPS = api.OPS
BEAT = 0.5  # 120 bpm


def tone(m, dur, parts=(1, .5, .33, .25), decay=None, glide=0.0):
    """A tone at float MIDI pitch m: harmonic partials (amplitudes `parts`), a 5 ms attack, held or decaying, and
    an attack glide that starts `glide` cents off and reaches the pitch linearly at 90 ms."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 440 * 2 ** ((m - 69) / 12) * 2 ** (glide * np.clip(1 - t / 0.09, 0, 1) / 1200)
    ph = 2 * np.pi * np.cumsum(f) / SR
    env = np.minimum(1, t / 0.005) * (np.exp(-t / decay) if decay else np.minimum(1, (n - np.arange(n)) / (0.02 * SR)))
    return env * sum(a * np.sin(k * ph) for k, a in enumerate(parts, 1))


def seq(events, beat=BEAT):
    y = np.zeros(int((len(events) * beat + 0.5) * SR))
    for i, x in enumerate(events):
        a = int(i * beat * SR)
        y[a:a + len(x)] += x[:len(y) - a]
    return 0.4 * y / np.abs(y).max()


def hit(m, rng):
    """A hand drum: a noise strike over a decaying first mode with its second mode an octave above."""
    n = int(0.4 * SR)
    strike = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.012 * SR))
    return tone(m, 0.4, parts=(1, .6), decay=0.15) + 0.5 * strike


@pytest.fixture
def proj():
    d = tempfile.mkdtemp(prefix='ismail_tuning_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=4)
    yield p
    shutil.rmtree(d, ignore_errors=True)


def stems(p, **parts):
    """Write each part as a rendered track stem (and a latest.wav), as render(stems=True) leaves them."""
    sd = os.path.join(p, 'renders', 'stems')
    os.makedirs(sd, exist_ok=True)
    mix = 0
    for name, y in parts.items():
        api.track_add(p, name)
        sf.write(os.path.join(sd, name + '.wav'), y, SR)
        mix = y if isinstance(mix, int) else mix[:len(y)] + y[:len(mix)]
    sf.write(os.path.join(p, 'renders', 'latest.wav'), mix, SR)


def verdict(out, name):
    return next(ln for ln in out.splitlines() if ln.strip().startswith(name + ':') and 'verdict' not in ln)


def cents_of(out, note):
    """The cents printed for `note` in the worst-notes lines."""
    return [int(c) for c in re.findall(rf'\b{re.escape(note)} ([+-]\d+) c', out)]


def test_a_part_on_the_grid_passes(proj):
    stems(proj, keys=seq([tone(m, 0.45) for m in (57, 60, 64, 67, 69, 72, 45, 48)]))
    out = OPS['analyze_tuning'](proj)
    v = verdict(out.split('verdict:')[1], 'keys')
    assert 'in tune' in v, out
    row = next(ln for ln in out.splitlines() if ln.strip().startswith('keys '))
    assert int(row.split()[1]) == 8 and abs(int(row.split()[2].rstrip('c'))) <= 2, out


def test_two_drum_voices_a_quarter_tone_off_are_flagged_with_their_cents(proj):
    rng = np.random.default_rng(1)
    # tumba F#3 +46 c (190.0 Hz), conga C#4 -49 c (269.4 Hz), quinto Bb4 -7 c (in tune), in turn
    stems(proj, drums=seq([hit((54.46, 60.51, 69.93)[i % 3], rng) for i in range(12)]),
          bass=seq([tone(m, 0.45) for m in (45, 45, 48, 50, 45, 45, 43, 45)]))
    out = OPS['analyze_tuning'](proj, worst=8)
    v = verdict(out.split('verdict:')[1], 'drums')
    assert '8 of 12 pitched notes sit a quarter tone off' in v, out
    assert 'F#3 +46 c (x4)' in v and 'C#4 -49 c (x4)' in v and 'Bb4' not in v and 'split it' in v, out
    assert 'in tune' in verdict(out.split('verdict:')[1], 'bass'), out
    fs, cs = cents_of(out, 'F#3'), cents_of(out, 'C#4')
    assert fs and all(abs(c - 46) <= 3 for c in fs), out
    assert cs and all(abs(c + 49) <= 3 for c in cs), out
    assert re.search(r'bar 1 beat 1\.00\s+F#3 \+46 c\s+\(190\.0 Hz\)', out), out   # where, on the song's grid
    # a key turns the off-key pitches into information, never a flag
    out = OPS['analyze_tuning'](proj, key='C major', stems=['bass'])
    assert 'outside C major' not in out and 'in tune' in out, out
    out = OPS['analyze_tuning'](proj, key='C major', stems=['drums'])
    assert 'outside C major (information' in out and 'F#3 x4' in out, out


def test_a_sharp_attack_inside_the_first_90_ms_is_not_flagged(proj):
    stems(proj, guitar=seq([tone(m, 0.45, decay=0.4, glide=40) for m in (52, 57, 62, 67, 71, 76)]))
    out = OPS['analyze_tuning'](proj)
    v = verdict(out.split('verdict:')[1], 'guitar')
    assert v.strip().startswith('guitar: in tune'), out
    # the attack itself is read 30-80 ms in, still ~15 c sharp, and named as the attack's fault
    assert 'attacks start 16 c sharp' in v, out
    # read from the onset instead, the glide pulls the steady pitch sharp (the skip is what keeps it out) and
    # nothing is left before the read to call the attack
    out = OPS['analyze_tuning'](proj, skip_ms=0)
    row = next(ln for ln in out.splitlines() if ln.strip().startswith('guitar ')).split()
    assert int(row[2].rstrip('c')) >= 2 and row[-1] == '-', out


S16 = 60 / 104 / 4          # a 16th at 104 bpm, ~144 ms: under the 100 ms skip plus the 60 ms window
RUN = [52, 53, 55, 57, 59, 60, 62, 64, 65, 64, 62, 60, 59, 57, 55, 53] * 2     # a scale up and down, twice


def row_of(out, name):
    return next(ln for ln in out.splitlines() if ln.strip().startswith(name + ' ')).split()


def test_a_fast_run_off_the_grid_is_read_and_flagged(proj):
    # plucked 16ths 30 c sharp, each dying ~100 ms after its onset: a fixed 100 ms skip read none of them
    stems(proj, lead=seq([tone(m + 0.3, S16, decay=0.03) for m in RUN], beat=S16))
    out = OPS['analyze_tuning'](proj)
    row = row_of(out, 'lead')
    assert int(row[1]) >= 24 and abs(int(row[2].rstrip('c')) - 30) <= 5, out
    assert 'whole part sits off the grid at +30 c' in verdict(out.split('verdict:')[1], 'lead'), out
    assert 'fast notes read from 0.3 of their length' in out, out


def test_a_sharp_attack_in_a_fast_run_is_flagged_on_the_attack(proj):
    # 16ths that start 8 c sharp for 40 ms, then settle on the grid: the steady pitch is in tune, the attack is not
    def note(m):
        n = int(S16 * SR)
        t = np.arange(n) / SR
        ph = 2 * np.pi * np.cumsum(440 * 2 ** ((m - 69) / 12) * np.where(t < 0.04, 2 ** (8 / 1200), 1.0)) / SR
        env = np.minimum(1, t / 0.005) * np.exp(-t / 0.3) * np.minimum(1, (n - np.arange(n)) / (0.01 * SR))
        return env * sum(a * np.sin(k * ph) for k, a in enumerate((1, .5, .33, .25), 1))
    stems(proj, lead=seq([note(m + 5) for m in RUN], beat=S16))
    out = OPS['analyze_tuning'](proj)
    row = row_of(out, 'lead')
    assert int(row[1]) >= 24 and abs(int(row[2].rstrip('c'))) <= 2, out
    assert abs(int(row[-1].rstrip('c')) - 8) <= 2, out                      # the attack column
    v = verdict(out.split('verdict:')[1], 'lead')
    assert v.strip().startswith('lead: in tune') and 'attacks start 8 c sharp' in v and 'pitch glide' in v, out


def test_an_unpitched_part_is_skipped_not_flagged(proj):
    rng = np.random.default_rng(2)
    n = int(0.3 * SR)
    shaker = seq([rng.standard_normal(n) * np.exp(-np.arange(n) / (0.08 * SR)) for _ in range(8)])
    stems(proj, shaker=shaker, keys=seq([tone(m, 0.45) for m in (57, 60, 64, 67)]))
    out = OPS['analyze_tuning'](proj)
    v = verdict(out.split('verdict:')[1], 'shaker')
    assert 'no steady pitch' in v and 'skipped' in v, out
    assert 'in tune' in verdict(out.split('verdict:')[1], 'keys'), out


def test_the_grid_moves_with_the_tuning_offset(proj):
    # a part tuned 30 c flat throughout (an A4 = 432.5 Hz song): off on the A440 grid, in tune on its own
    stems(proj, pad=seq([tone(m - 0.3, 0.45) for m in (57, 60, 64, 67, 69)]))
    out = OPS['analyze_tuning'](proj)
    v = verdict(out.split('verdict:')[1], 'pad')
    assert 'whole part sits off the grid at -30 c' in v and 'by +30 c' in v, out
    out = OPS['analyze_tuning'](proj, tuning_cents=-30)
    assert 'A4 = 432.4 Hz' in out and 'in tune' in verdict(out.split('verdict:')[1], 'pad'), out


def test_a_reference_is_read_on_its_own_tuning_until_retuned(proj):
    # a record 30 c flat: its stems are in tune with each other, so they read in tune on the record's own grid
    d = os.path.join(os.path.dirname(proj), 'ref_stems')
    os.makedirs(d)
    y = seq([tone(m - 0.3, 0.45) for m in (57, 60, 64, 67, 69, 72)])
    sf.write(os.path.join(d, 'other.wav'), y, SR)
    sf.write(os.path.join(d, 'record.wav'), y, SR)
    api.project_set(proj, reference=os.path.join(d, 'record.wav'), reference_stems=d)
    out = OPS['analyze_tuning'](proj, source='ref')
    assert "reference's own tuning, -30 c" in out and 'in tune' in verdict(out.split('verdict:')[1], 'other'), out
    # once ref_retune has moved it (the project records the shift), the grid is A440 again
    P = api.Project(proj)
    P.d['reference']['retuned_cents'] = 30.0
    P.save()
    out = OPS['analyze_tuning'](proj, source='ref')
    assert 'A4 = 440.0 Hz' in out and 'ref_retune' in out and 'sits off the grid at -30 c' in out, out


def test_errors_point_forward(proj):
    with pytest.raises(api.OpError, match=r'render\(stems=True\) first'):
        OPS['analyze_tuning'](proj)
    os.makedirs(os.path.join(proj, 'renders'))
    sf.write(os.path.join(proj, 'renders', 'latest.wav'), np.zeros(SR), SR)
    with pytest.raises(api.OpError, match=r'no track stems: render\(stems=True\)'):
        OPS['analyze_tuning'](proj)
    with pytest.raises(api.OpError, match=r'separate\(source=.ref.\)|project_set\(reference='):
        OPS['analyze_tuning'](proj, source='ref')
    stems(proj, keys=seq([tone(m, 0.45) for m in (57, 60, 64)]))
    with pytest.raises(api.OpError, match=r"parts: \['keys'\]"):
        OPS['analyze_tuning'](proj, stems=['lead'])
    with pytest.raises(api.OpError, match='D dorian'):
        OPS['analyze_tuning'](proj, key='H sharp')
