"""MCP server: every ismail op as an agent tool (stdio).

Run:  python -m ismail.mcp_server   (or `ismail mcp`, or `uvx ismail mcp` without installing)
Claude Code config (.mcp.json):
  {"mcpServers": {"ismail": {"command": "python", "args": ["-m", "ismail.mcp_server"]}}}   (after pip install -e .)
All tools take `project` (a project directory) first, except the stage_* tools, which take `scene` (or `scenes`, a
song's scenes folder); see the `guide` tool for the workflow.

Fewer tools, for a harness that caps them (many stop near 128): serve only some roles' or shelves' ops, always with
guide, ops_find and the config_* ops so the agent can still find the rest and see its settings (the shelves and
roles are api_ops.CATEGORIES and ROLES):
  python -m ismail.mcp_server --roles dj,mc            or  --shelves live,stream,phone
  python -m ismail.mcp_server --list-roles             each role's and shelf's tool count, without starting
  python -m ismail.mcp_server --roles dj --max-tools 128   refuses to start when the set is bigger
The settings mcp.roles, mcp.shelves and mcp.max_tools (config_set, then restart the server) do the same for a
harness that can't pass arguments; an argument wins over its setting (their old variables ISMAIL_MCP_ROLES,
ISMAIL_MCP_SHELVES and ISMAIL_MCP_MAX_TOOLS still work).
"""
import argparse
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('PYTHONWARNINGS', 'ignore')

import warnings  # noqa: E402
warnings.filterwarnings('ignore')

from fastmcp import FastMCP  # noqa: E402
from fastmcp.exceptions import ToolError  # noqa: E402

from ismail.api import OPS, OpError  # noqa: E402
from ismail.api_ops import CATEGORIES, DISCOVERY, ROLES, ops_for, serve_only  # noqa: E402
from ismail.guide import GUIDE  # noqa: E402
from ismail import settings  # noqa: E402


def _version():
    """ismail's own version for the MCP handshake; without it FastMCP reports its own (QA F7: 4.1.0)."""
    from importlib.metadata import PackageNotFoundError, version
    try:
        return version('ismail')
    except PackageNotFoundError:                       # run from a checkout that was never installed
        import re
        from pathlib import Path
        m = re.search(r'^version = "([^"]+)"', (Path(__file__).parent.parent / 'pyproject.toml').read_text(), re.M)
        return m.group(1) if m else None


def _wrap(name, fn):
    def tool(**kw):
        try:
            return fn(**kw)
        except (OpError, ValueError) as e:
            raise ToolError(str(e))
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}. Signature: {name}{inspect.signature(fn)}")
    tool.__name__ = name
    tool.__doc__ = inspect.getdoc(fn)
    tool.__signature__ = inspect.signature(fn)
    tool.__annotations__ = {k: (v.annotation if v.annotation is not inspect.Parameter.empty else object)
                            for k, v in inspect.signature(fn).parameters.items()}
    tool.__annotations__['return'] = str
    return tool


def build(names=None):
    """A server with these ops as its tools, in OPS order as given (None: every op)."""
    server = FastMCP('ismail', instructions=GUIDE, version=_version())
    for name in (OPS if names is None else names):
        server.tool(_wrap(name, OPS[name]), name=name)
    return server


mcp = build()


def _list(v):
    return [x.strip() for x in (v or '').split(',') if x.strip()]


def sizes():
    """({role: tool count}, {shelf: tool count}) as served, each count with the discovery ops."""
    return ({r: len(ops_for(roles=[r])) for r in ROLES},
            {c[0]: len(ops_for(shelves=[c[0]])) for c in CATEGORIES})


def _sizes_line():
    roles, shelves = sizes()
    return ('role sizes: ' + ', '.join(f'{r} {n}' for r, n in roles.items()) +
            '; shelf sizes: ' + ', '.join(f'{s} {n}' for s, n in shelves.items()))


def list_roles():
    """The --list-roles text: each role's and shelf's tool count, and a role's shelves."""
    roles, shelves = sizes()
    L = [f'ismail MCP tool counts ({len(OPS)} ops in all; each count includes the ops every server serves: '
         f'{", ".join(DISCOVERY)})', 'roles (--roles):']
    L += [f'  {r:<7} {n:>4}  {" ".join(ROLES[r])}' for r, n in roles.items()]
    L.append('shelves (--shelves):')
    L += [f'  {s:<10} {n:>4}  {d}' for (s, n), (_, d, _) in zip(shelves.items(), CATEGORIES)]
    return '\n'.join(L)


def choose(roles=(), shelves=(), max_tools=None):
    """(op names, how they were chosen) for these roles and shelves; (None, 'every op') for neither. Raises
    ValueError for an unknown role or shelf, or a set bigger than max_tools."""
    if not roles and not shelves:
        names, how = None, 'every op'
    else:
        try:
            names = ops_for(roles, shelves)
        except OpError as e:
            raise ValueError(str(e)) from None
        how = ' and '.join(x for x in (f"roles {','.join(roles)}" if roles else '',
                                       f"shelves {','.join(shelves)}" if shelves else '') if x)
    n = len(OPS) if names is None else len(names)
    if max_tools is not None and n > max_tools:
        raise ValueError(f'{n} tools for {how} is more than --max-tools {max_tools}. {_sizes_line()}. '
                         'Pick fewer roles, or name only the shelves you need with --shelves')
    return names, how


def _setting(key, ap):
    """A tool-list setting. A bad value in the settings file never stops the server: its unknown names are skipped
    (or the value is), the rest is served, and stderr says what was skipped. An old variable that does not read
    stops it, as it always did (a flag on the command line is checked the same way)."""
    s = settings.setting(key)
    v, src, problems = settings.resolve(key)
    for p in problems:
        if p.startswith(s.env + '='):
            ap.error(p)
        print(f'ismail MCP: {key}: {p}. config_get({key!r}) shows it; config_set fixes it', file=sys.stderr,
              flush=True)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m ismail.mcp_server',
                                 description='ismail MCP server (stdio). With no options it serves every op.')
    ap.add_argument('--roles', help=f"comma-separated roles whose shelves to serve: {', '.join(ROLES)} "
                                    '(setting mcp.roles)')
    ap.add_argument('--shelves', help=f"comma-separated shelves to serve: {', '.join(c[0] for c in CATEGORIES)} "
                                      '(setting mcp.shelves)')
    ap.add_argument('--max-tools', type=int, help='refuse to start when more tools than this would be served '
                                                  '(setting mcp.max_tools)')
    ap.add_argument('--list-roles', action='store_true', help="print each role's and shelf's tool count and exit")
    a = ap.parse_args(argv)
    if a.list_roles:
        print(list_roles())
        return
    roles = _list(a.roles) if a.roles is not None else _setting('mcp.roles', ap)
    shelves = _list(a.shelves) if a.shelves is not None else _setting('mcp.shelves', ap)
    cap = a.max_tools if a.max_tools is not None else _setting('mcp.max_tools', ap)
    try:
        names, how = choose(roles, shelves, cap)
    except ValueError as e:
        ap.error(str(e))
    serve_only(names, how)
    server = mcp if names is None else build(names)
    print(f'ismail MCP: serving {len(OPS) if names is None else len(names)} of {len(OPS)} tools ({how})',
          file=sys.stderr, flush=True)
    server.run(show_banner=False)


if __name__ == '__main__':
    main()
