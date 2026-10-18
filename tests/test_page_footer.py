"""Pages people may share end with a footer: made with ismail, the link back, and how the piece's sound was made,
by counts only (ledger:M254: a listener who is not at this computer had no way to learn what made the song)."""
import numpy as np
import pytest

from ismail import api, machine, sources
from ismail.api import OpError
from ismail.render import write_wav
from ismail.tags import SITE_URL


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [])
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=1)
    return p


def test_the_footer_alone_links_back():
    f = sources.footer()
    assert f['text'] == f"{sources.FOOTER_LEAD}: {SITE_URL}" and f['link'] == SITE_URL and f['how'] == ''
    assert f'<a href="{SITE_URL}"' in f['html'] and f['html'].startswith('<footer class="made-with">')


def test_the_footer_counts_how_the_sound_was_made_without_names(proj, tmp_path):
    api.track_add(proj, 'secret_lead', instrument={'type': 'synth'})
    api.track_add(proj, 'wobble', instrument='preset:growl')
    api.track_add(proj, 'violin', instrument={'type': 'mimic', 'profile': 'violin'})
    t = np.linspace(0, 0.3, 13230)
    write_wav(str(tmp_path / 'hit.wav'), np.vstack([np.sin(2 * np.pi * 220 * t)] * 2) * np.exp(-t * 9), 44100)
    api.sound_import(proj, 'hit', str(tmp_path / 'hit.wav'))
    api.track_add(proj, 'perc', instrument={'type': 'sampler', 'sound': 'hit'})
    out = api.credits(proj, format='footer')
    assert '4 parts: 3 synthesized (1 modeled on measured recordings, 2 designed), 1 from recorded audio.' in out
    assert 'secret_lead' not in out and str(tmp_path) not in out and 'hit.wav' not in out
    assert 'footer html: <footer' in out
    assert not (tmp_path / 'song' / 'CREDITS.md').exists()          # the footer writes nothing


def test_all_synthesized_has_no_zero_clause(proj):
    api.track_add(proj, 'pad', instrument={'type': 'synth'})
    api.track_model(proj, 'pad', on='designed')
    assert '1 part, synthesized (designed).' in api.credits(proj, format='footer')
    api.track_add(proj, 'pad2', instrument={'type': 'synth'})
    out = api.credits(proj, format='footer')
    assert '2 parts, all synthesized (all designed).' in out and ' 0 ' not in out


def test_a_wrong_format_says_which_ones_exist(proj):
    with pytest.raises(OpError, match="'footer' returns the footer"):
        api.credits(proj, format='html')
