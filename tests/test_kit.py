"""The kit page (ledger M295): every public voice and effect lands in a family, the ear state is data, the committed
page is current, --check catches a stale one, and the kit op filters by family."""
import os
import re

import pytest

from ismail import api, kit as K
from ismail.api import OpError

BRANDS = re.compile(r'\b(fender|strat|stratocaster|gibson|hammond|leslie|rhodes|wurlitzer|moog|roland|yamaha|korg|'
                    r'steinway|ludwig|precision bass|jazz bass|linn|oberheim|prophet|juno|tr-\d+|dx\d+)\b', re.I)


@pytest.fixture(scope='module')
def data():
    return K.build()


def test_every_public_voice_and_effect_lands_in_a_family(data):
    """A new preset, voice module, mimic profile or effect type needs a row in ismail/kit_tables.json; a row
    nothing plays any more is removed. The lists below name them."""
    assert data['unsorted'] == {'voices': [], 'effects': []}
    assert data['stale_rows'] == {'voices': [], 'effects': []}
    fams = data['families']
    assert {v['family'] for v in data['voices']} <= set(fams['voices'])
    assert {e['family'] for e in data['effects']} <= set(fams['effects'])
    assert len(data['voices']) >= 40 and len(data['effects']) >= 25


def test_ear_state_is_data_with_its_exam(data):
    states = set(data['ear_states'])
    assert {'passed', 'usable', 'failed', 'pending', 'untested'} <= states
    for v in data['voices']:
        assert v['ear'] in states, v['name']
        if v['ear'] != 'untested':
            assert v['exam'], f"{v['name']} is {v['ear']} with no exam or ledger row"


def test_modeled_on_names_instrument_types_not_brands(data):
    bad = {v['name']: v['modeled_on'] for v in data['voices'] if BRANDS.search(v['modeled_on'])}
    assert bad == {}


def test_every_voice_has_a_range_and_a_cost(data):
    for v in data['voices']:
        assert v['range'] and v['range'] != 'unknown', v['name']
        assert v['cost'].startswith('not measured'), v['name']


def test_the_committed_page_is_current():
    """Changed a preset, a voice, an effect or the tables: run python -m ismail.kit and commit docs/KIT.md and
    docs/kit.json."""
    assert K.stale() == []


def test_check_catches_a_stale_page(tmp_path, data, capsys):
    out = str(tmp_path)
    assert K.stale(out, data) == ['kit.json', 'KIT.md']           # missing counts as stale
    K.write(out, data)
    assert K.stale(out, data) == []
    assert K.main(['--check', '--out', out]) == 0
    p = os.path.join(out, 'KIT.md')
    with open(p, 'a', encoding='utf8') as f:
        f.write('\nan edit by hand\n')
    assert K.stale(out, data) == ['KIT.md']
    assert K.main(['--check', '--out', out]) == 1
    assert 'stale: KIT.md' in capsys.readouterr().out


def test_a_voice_with_no_row_lands_in_unsorted(monkeypatch):
    T = K.tables()
    name = next(iter(T['voices']))
    del T['voices'][name]
    T['voices']['gone_voice'] = {'family': 'keys', 'type': 'sprite', 'modeled_on': 'nothing', 'ear': 'untested'}
    monkeypatch.setattr(K, 'tables', lambda: T)
    d = K.build()
    assert d['unsorted']['voices'] == [name]
    assert d['stale_rows']['voices'] == ['gone_voice']
    assert next(v for v in d['voices'] if v['name'] == name)['family'] == 'unsorted'
    assert 'Not yet sorted' in K.render_md(d)


def test_a_bad_ear_state_is_refused(monkeypatch):
    T = K.tables()
    next(iter(T['voices'].values()))['ear'] = 'great'
    monkeypatch.setattr(K, 'tables', lambda: T)
    with pytest.raises(ValueError, match='ear'):
        K.build()


def _names(text):
    return {l.split()[0] for l in text.splitlines() if l.startswith('  ') and not l.strip().startswith('gap:')}


def test_kit_op_filters_by_family(data):
    whole = api.OPS['kit']()
    assert all(f'{fam}:' in whole for fam in data['families']['voices'])
    woodwinds = {v['name'] for v in data['voices'] if v['family'] == 'woodwinds'}
    out = api.OPS['kit'](family='woodwinds')
    assert _names(out) == woodwinds and 'gap:' in out
    assert api.OPS['kit'](family='winds') == out                    # an alias reads the same family
    dyn = api.OPS['kit'](family='dynamics')
    assert _names(dyn) == {e['name'] for e in data['effects'] if e['family'] == 'dynamics'}
    synths = api.OPS['kit'](family='synths')
    assert all(f'{f}:' in synths for f in K.SYNTHS) and 'woodwinds' not in synths
    with pytest.raises(OpError, match='families'):
        api.OPS['kit'](family='no such family')


def test_kit_op_one_voice_in_full(data):
    v = next(x for x in data['voices'] if x['ear'] != 'untested')
    out = api.OPS['kit'](voice=v['name'])
    assert out.startswith(v['name']) and f"ear: {v['ear']}" in out and 'use: instrument=' in out
    with pytest.raises(OpError, match='no public voice'):
        api.OPS['kit'](voice='no_such_voice')
