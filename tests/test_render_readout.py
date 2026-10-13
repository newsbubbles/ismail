"""render's per-track readout is post-fader and prints each fader, and says when the master limiter is easing
every peak (songs/partisan_club/HANDOFF.md: a bus at -2 dB read the same as at 0 behind a working limiter)."""
import json
import os

from ismail import api


def test_the_readout_prints_each_fader_and_says_post_fader(tmp_path):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=2)
    api.track_add(root, 'kick', instrument='preset:kick')
    api.notes_write(root, 'kick', 1, '0 C2 1 110; 2 C2 1 110', repeat=2)
    api.bus_add(root, 'drums', volume_db=-2.0)
    api.track_set(root, 'kick', output='drums', volume_db=-4.5)
    out = api.render(root, cache=False, wait='0')
    assert 'post-fader' in out
    line = {ln.split()[0]: ln for ln in out.splitlines() if ln.startswith('  kick') or ln.startswith('  bus:drums')}
    assert 'fader -4.5' in line['kick'] and 'fader -2.0' in line['bus:drums']


def test_project_set_artist_keeps_an_empty_artist(tmp_path):
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=2)
    api.project_set(root, artist='')
    with open(os.path.join(root, 'project.json'), encoding='utf8') as f:
        assert json.load(f)['artist'] == ''
