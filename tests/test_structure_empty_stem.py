"""analyze_structure on a render whose stem map has no track for a group (ledger M296): the empty group is
written as an all-zero wav, and its arrangement-map row must read '.' (silent), not '9' at its own top, and it
must stay out of every section's "loud stems"."""
import numpy as np
import soundfile as sf

from ismail import features as FE, structure as ST
from ismail.analysis import Grid

SR = 22050
BPM = 240.0  # 1 s bars: 8 bars in 8 s keeps the features pass small


def _write(path, y):
    sf.write(str(path), np.stack([y, y], 1).astype(np.float32), SR, subtype='FLOAT')
    return str(path)


def test_empty_stem_group_is_silent_and_not_loud(tmp_path):
    n = SR * 8
    t = np.arange(n) / SR
    other = 0.3 * np.sin(2 * np.pi * 220 * t) * (t < 6)          # a pad in bars 1-6, then 2 silent bars
    drums = np.zeros(n)
    rng = np.random.default_rng(0)
    for b in range(8 * 4):                                       # a noise hit on every beat
        i = int(b * SR * 60 / BPM)
        drums[i:i + 2000] += 0.5 * rng.standard_normal(2000) * np.exp(-np.arange(2000) / 400)
    vocals = np.zeros(n)                                         # the group no track feeds
    g = Grid(BPM)
    Fm = FE.extract(_write(tmp_path / 'mix.wav', other + drums), g)
    Fs = {s: FE.extract(_write(tmp_path / f'{s}.wav', y), g)
          for s, y in (('drums', drums), ('other', other), ('vocals', vocals))}
    _, text = ST.describe(Fm, Fs)
    rows = {ln[:9].strip(): ln[10:] for ln in text.splitlines() if len(ln) > 10}
    assert set(rows['vocals']) == {'.'}, rows['vocals']
    assert '9' in rows['other'] and '9' in rows['drums']
    for ln in text.splitlines():
        if 'loud stems:' in ln:
            assert 'vocals' not in ln.split('loud stems:')[1], ln


def test_level_row_absolute_floor():
    assert ST.level_row(np.full(4, -117.0)) == '....'
    assert ST.level_row(np.array([-20.0, -24.0, -117.0])) == '98.'
