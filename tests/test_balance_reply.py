"""render(stems=True)'s balance lines (Nate 10-18, a recreation's second bass was far too loud and no reply said so):
each stem's share of the mix against the reference's per 8-bar stretch, a layer the reference has that yours lacks,
and whether a mastering pass comes next."""
import os

import numpy as np
import soundfile as sf

from ismail import api

OPS = api.OPS


def _song(p):
    api.project_new(p, bpm=120, length_bars=16)
    api.track_add(p, 'drums', instrument='preset:kit_basic')
    api.track_add(p, 'bass', instrument={"type": "synth", "oscs": [{"wave": "saw"}], "mono": True}, volume_db=-6)
    api.track_add(p, 'lead', instrument={"type": "synth", "oscs": [{"wave": "square"}]}, volume_db=-12)
    api.pattern_write(p, 'drums', 1, {"C1": "x...x...x...x...", "D1": "....x.......x..."}, repeat=16)
    api.notes_write(p, 'bass', 1, "0 E2 1; 1 G2 1; 2 A2 1; 3 D3 1", repeat=16)
    api.notes_write(p, 'lead', 1, "0 E4 2; 2 G4 2", repeat=8)          # bars 1-8 only
    return p


def test_render_names_the_hot_stretch_the_missing_layer_and_the_master_pass(tmp_path):
    p = _song(str(tmp_path / 'p'))
    api.render(p, stems=True, cache=False)
    sd = str(tmp_path / 'ref_stems')
    os.makedirs(sd)
    st = os.path.join(p, 'renders', 'stems')
    y, sr = sf.read(os.path.join(st, 'drums.wav'))
    sf.write(os.path.join(sd, 'drums.wav'), y, sr)
    y, sr = sf.read(os.path.join(st, 'bass.wav'))
    y = y.copy()
    y[int(16.0 * sr):] *= 10 ** (-9 / 20)                               # the reference's bass sits 9 dB lower from bar 9
    sf.write(os.path.join(sd, 'bass.wav'), y, sr)
    sf.write(os.path.join(sd, 'other.wav'), y * 0.5, sr)               # and it has a layer in 'other' all the way
    ref = str(tmp_path / 'ref.wav')
    sf.write(ref, y, sr)
    api.project_set(p, reference=ref, reference_stems=sd)
    OPS['stem_map_set'](p, {'drums': 'drums', 'bass': 'bass', 'lead': 'other'})
    out = api.render(p, stems=True, cache=False)
    assert 'balance against the reference' in out, out
    line = next(ln for ln in out.splitlines() if ln.strip().startswith('bass:'))
    assert 'worst bars 9-16' in line and 'louder' in line, line
    assert any(ln.strip().startswith('other: silent in bars 9-16') for ln in out.splitlines()), out
    assert 'master: a limiter only, no mastering pass yet. Balance first' in out, out


def test_no_stem_map_no_balance_lines(tmp_path):
    p = _song(str(tmp_path / 'p'))
    out = api.render(p, stems=True, cache=False)
    assert 'balance against the reference' not in out and 'mastering pass' not in out
