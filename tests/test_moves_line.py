"""A full render of a song with no moves says so (Nate 10-18: two sessions handed over whole drafts without an
effects pass until the person asked for one)."""
from ismail import api


def _song(p):
    api.project_new(p, bpm=120, length_bars=2)
    for t in ('kick', 'bass', 'pad'):
        api.track_add(p, t, instrument={'type': 'synth'})
        api.notes_write(p, t, 1, '0 C3 1; 2 C3 1', repeat=2)
    return p


def test_a_full_render_without_moves_names_the_effects_pass(tmp_path):
    p = _song(str(tmp_path / 's'))
    out = api.render(p, cache=False, wait='0')
    assert 'moves: no automation, no transitions' in out and 'effects pass' in out, out
    assert 'moves:' not in api.render(p, bars=[1, 1], cache=False, wait='0')       # a window is not a draft
    api.automation_set(p, 'pad', 'volume_db', [[1, 0], [2.75, -12], [3, 0]])
    api.track_add(p, 'fx', instrument={'type': 'code', 'voice': 'transitions'})
    assert 'moves:' not in api.render(p, cache=False, wait='0')
