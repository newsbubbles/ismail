"""A fader or pan move reuses the cached track render; a sidechain listener still hears the new level."""
import os
import shutil
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api


def _line(out, name):
    return next(l for l in out.split('\n') if l.strip().startswith(name))


@pytest.fixture
def proj():
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=120, length_bars=1)
    api.track_add(p, 'lead', instrument={"type": "synth", "oscs": [{"wave": "saw"}]}, volume_db=-6)
    api.notes_write(p, 'lead', 1, "0 A3 4")
    yield p
    shutil.rmtree(d, ignore_errors=True)


def test_fader_and_pan_reuse_the_cache(proj):
    api.render(proj, stems=True)
    a, _ = sf.read(os.path.join(proj, 'renders', 'stems', 'lead.wav'))
    api.track_set(proj, 'lead', volume_db=-12, pan=0.5)
    out = api.render(proj, stems=True)
    assert 'cached' in _line(out, 'lead')
    b, _ = sf.read(os.path.join(proj, 'renders', 'stems', 'lead.wav'))
    assert b[:, 0].std() < a[:, 0].std() * 0.5              # quieter and panned right
    assert b[:, 1].std() > b[:, 0].std()


def test_sound_change_still_rerenders(proj):
    api.render(proj, stems=True)
    api.notes_write(proj, 'lead', 1, "0 C4 4")
    assert 'cached' not in _line(api.render(proj, stems=True), 'lead')

