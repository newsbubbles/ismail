"""The master chain: the default safety limiter can be left out, and a second limiter is named."""
from ismail import api


def test_master_chain_without_default_limiter(tmp_path):
    p = str(tmp_path / 'p')
    api.project_new(p, bpm=120, length_bars=1, master_limiter=False)
    assert 'fx chain now [0:eq]' in api.fx_add(p, 'master', {'type': 'eq'})


def test_second_master_limiter_is_named(tmp_path):
    p = str(tmp_path / 'p')
    api.project_new(p, bpm=120, length_bars=1)
    out = api.fx_add(p, 'master', {'type': 'limiter', 'ceiling_db': -1.0})
    assert '2 limiters (#0, #1)' in out and 'index=0' in out
