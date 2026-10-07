"""Named effect stacks (ledger:M177): save and load by name, in the library or a song, and what each costs on this
machine, measured once and named again where a chain is used live."""
import json

import pytest

from ismail import api_fxstack as X
from ismail.api import OpError

REAL_MEASURE = X.measure


@pytest.fixture(autouse=True)
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_FX_STACKS', str(tmp_path / 'lib'))
    monkeypatch.setattr(X, 'measure', lambda chain, **k: {'x_rt': 2.5 if any(f['type'] == 'univibe' for f in chain)
                                                          else 12.0, 'live_s': 0.1, 'baked_s': 0.0})
    return tmp_path / 'lib'


def test_save_load_list_and_the_cost_on_this_machine(tmp_path, lib):
    chain = [{'type': 'compressor', 'threshold_db': -18, 'ratio': 3}, {'type': 'tape', 'drive': 0.25}]
    out = X.fx_stack_save('my_glue', chain, notes='kit glue')
    assert 'saved effect stack' in out and '12x realtime' in out and 'RISK' not in out
    assert json.loads((lib / 'my_glue.json').read_text(encoding='utf8'))['chain'] == chain
    assert X.device() in json.loads((lib / 'costs.json').read_text(encoding='utf8'))['my_glue']
    got = X.fx_stack_load('my_glue')
    assert json.loads(got.split('chain: ', 1)[1]) == chain and 'kit glue' in got
    song = tmp_path / 'song'
    song.mkdir()
    X.fx_stack_save('my_glue', chain[:1], project=str(song), measure_cost=False)    # the song's own wins there
    assert json.loads(X.fx_stack_load('my_glue', project=str(song)).split('chain: ', 1)[1]) == chain[:1]
    listed = X.fx_stack_list()
    assert 'my_glue [library]' in listed and 'afrobeat_chank [built-in]' in listed


def test_bad_names_and_chains_say_what_to_send():
    with pytest.raises(OpError, match='lowercase'):
        X.fx_stack_save('My Glue', [{'type': 'tape'}])
    with pytest.raises(OpError, match=r"chain\[0\]"):
        X.fx_stack_save('x', [{'type': 'nope'}])
    with pytest.raises(OpError, match='there are: .*kit70_glue'):
        X.fx_stack_load('missing')


def test_a_slow_chain_is_named_where_it_is_used():
    X.fx_stack_measure('afrobeat_chank')
    chank = X.find('afrobeat_chank')[1]['chain']
    lines = X.chain_costs([chank, 'afrobeat_chank', [{'type': 'gain'}]])       # by content or by name, once
    assert len(lines) == 1 and 'RISK' in lines[0] and '2.5x realtime' in lines[0]


def test_the_built_in_chains_are_valid_and_a_real_measure_runs():
    for name, (_, s) in X.all_stacks().items():
        X._check_chain(s['chain'])
    m = REAL_MEASURE([{'type': 'eq', 'bands': [{'type': 'lowcut', 'freq': 35}]}, {'type': 'cab'}], seconds=1.0)
    assert m['x_rt'] > 1 and m['live_s'] > 0
