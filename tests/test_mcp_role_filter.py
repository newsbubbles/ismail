"""The MCP server filtered to some roles or shelves: their ops plus the discovery ops, counts that agree with the
shelves, and a refusal that names the valid choices."""
import asyncio

import pytest

from ismail import api
from ismail.api_ops import CATEGORIES, DISCOVERY, ROLES, category_of, ops_for, serve_only


@pytest.fixture
def served_reset():
    yield
    serve_only(None)


def _tool_names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_a_filtered_server_lists_the_roles_ops_plus_discovery():
    from ismail.mcp_server import build
    names = ops_for(roles=['mc'])
    want = {n for n in api.OPS if category_of(n) in ROLES['mc']} | set(DISCOVERY)
    assert set(names) == want
    assert _tool_names(build(names)) == want


def test_roles_and_shelves_together_are_their_union():
    names = set(ops_for(roles=['mc'], shelves=['listen']))
    assert names == set(ops_for(roles=['mc'])) | set(ops_for(shelves=['listen']))


@pytest.mark.parametrize('kw,valid', [({'roles': ['nobody']}, 'mc'), ({'shelves': ['nowhere']}, 'stream')])
def test_an_unknown_role_or_shelf_is_refused_with_the_valid_ones(kw, valid):
    from ismail.mcp_server import choose
    with pytest.raises(ValueError, match=valid):
        choose(**kw)


def test_the_counts_agree_with_the_shelves():
    from ismail.mcp_server import sizes
    roles, shelves = sizes()
    on = {c[0]: sum(category_of(n) == c[0] for n in api.OPS) for c in CATEGORIES}
    extra = lambda cats: sum(category_of(d) not in cats for d in DISCOVERY)   # noqa: E731
    for role, cats in ROLES.items():
        assert roles[role] == sum(on[c] for c in cats) + extra(cats), role
    for s, n in shelves.items():
        assert n == on[s] + extra([s]), s
    assert roles['dev'] == len(api.OPS)


def test_max_tools_refuses_a_set_too_big_and_names_the_sizes():
    from ismail.mcp_server import choose
    n = len(ops_for(roles=['mc']))
    assert len(choose(roles=['mc'], max_tools=n)[0]) == n
    with pytest.raises(ValueError, match=r'role sizes: .*mc \d+'):
        choose(roles=['mc'], max_tools=n - 1)


def test_no_filter_serves_every_op():
    from ismail.mcp_server import choose, mcp
    assert choose() == (None, 'every op')
    assert _tool_names(mcp) == set(api.OPS)


def test_ops_find_says_the_server_is_filtered(served_reset):
    find = api.OPS['ops_find']
    assert 'not on this server' not in find(role='dj')
    serve_only(ops_for(roles=['mc']), 'roles mc')
    out = find(need='formant track of a vowel', n=3)
    assert 'analyze_formants' in out and '(not on this server)' in out and 'filtered to roles mc' in out
    assert 'not on this server' not in find(category='stream').split('\nnote:')[0]
