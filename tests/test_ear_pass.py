"""ear_pass (ledger:M252): one call compares the latest render with the reference, ranks the worst bars, zooms each
and crops yours and the reference's side by side; it is the receipt the render reply asks for, and a sound_compare
alone is not."""
import os

import numpy as np
import pytest
import soundfile as sf

from ismail import api, replies


@pytest.fixture(autouse=True)
def quiet(tmp_path, monkeypatch):
    for k, sub in (('ISMAIL_PHONE_HOME', 'phone'), ('ISMAIL_STAGE_REGISTRY', 'stage'),
                   ('ISMAIL_LIVE_REGISTRY', 'live'), ('ISMAIL_FEEDBACK_HOME', 'feedback')):
        (tmp_path / '_state' / sub).mkdir(parents=True)
        monkeypatch.setenv(k, str(tmp_path / '_state' / sub))
    from ismail import perceptual
    monkeypatch.setattr(perceptual, 'available', lambda: False)      # no model load in a unit test


def _song(root, ref):
    api.project_new(root, bpm=120, length_bars=4)
    api.track_add(root, 'bass', instrument={'type': 'synth'})
    api.notes_write(root, 'bass', 1, '0 A2 1; 2 A2 1', repeat=4)
    sr = 44100
    t = np.arange(8 * sr) / sr
    y = 0.3 * np.sin(2 * np.pi * 110 * t) * (np.sin(2 * np.pi * 2 * t) > 0) + 0.05 * np.sin(2 * np.pi * 3000 * t)
    os.makedirs(os.path.dirname(ref), exist_ok=True)
    sf.write(ref, np.stack([y, y], 1), sr)
    api.project_set(root, reference=ref)
    return root


def test_ear_pass_needs_a_reference_and_says_how(tmp_path):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=2)
    with pytest.raises(api.OpError, match=r"project_set\(reference=<file>\)"):
        api.OPS['ear_pass'](root)


def test_ear_pass_ranks_zooms_crops_and_counts_as_the_comparison(tmp_path):
    root = _song(str(tmp_path / 's'), str(tmp_path / 'ref' / 'ref.wav'))
    out = api.render(root, cache=False, wait='0')
    assert 'compared with the reference (ref.wav): never' in out and 'Next: ear_pass' in out
    api.OPS['sound_compare'](root, 'render', 'ref')
    assert replies.last_compared(root) is None                       # a sound_compare alone is not the comparison
    out = api.OPS['ear_pass'](root, top=2)
    assert out.startswith("ear pass: comparison ") and "stems 'none'" in out, out[:300]
    assert 'the 2 worst bars of mix, worst first:' in out and 'most different band:' in out
    assert "region_measure(source='render', bars=[" in out and 'zoom bars' in out
    pngs = [ln.strip() for ln in out.splitlines() if ln.strip().endswith('.png')]
    assert len(pngs) == 4 and all(os.path.isfile(p) for p in pngs), pngs
    assert pngs[0].endswith('_yours.png') and pngs[1].endswith('_ref.png')
    assert replies.last_compared(root)['op'] == 'ear_pass'
    with pytest.raises(api.OpError, match='1 to 6'):
        api.OPS['ear_pass'](root, top='many')
