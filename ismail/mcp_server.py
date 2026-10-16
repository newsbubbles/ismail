"""MCP server: every ismail op as an agent tool (stdio).

Run:  python -m ismail.mcp_server   (or `ismail mcp`, or `uvx ismail mcp` without installing)
Claude Code config (.mcp.json):
  {"mcpServers": {"ismail": {"command": "python", "args": ["-m", "ismail.mcp_server"]}}}   (after pip install -e .)
All tools take `project` (a project directory) first, except the stage_* tools, which take `scene` (or `scenes`, a
song's scenes folder); see the `guide` tool for the workflow.
"""
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
from ismail.guide import GUIDE  # noqa: E402



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


mcp = FastMCP('ismail', instructions=GUIDE, version=_version())


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


for _name, _fn in OPS.items():
    mcp.tool(_wrap(_name, _fn), name=_name)


def main():
    mcp.run(show_banner=False)


if __name__ == '__main__':
    main()
