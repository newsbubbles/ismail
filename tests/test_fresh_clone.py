"""What a fresh clone sees first (QA rehearsal 10-16, F3 F6 F7): the help line, the MCP handshake, the guide's steps."""
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent


def _version():
    return re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(), re.M).group(1)


def test_the_mcp_handshake_reports_ismails_version_not_fastmcps():
    from ismail.mcp_server import mcp
    assert mcp.version == _version()


def test_the_help_line_does_not_send_people_to_pypi_before_a_release():
    import ismail.__main__ as cli
    assert 'Installed from PyPI' not in cli.__doc__
    assert 'git+https://github.com/newsbubbles/ismail' in cli.__doc__


def test_the_plain_words_steps_are_numbered_once_and_in_order():
    from ismail.guide import PLAIN_STEPS
    steps = [int(n) for n in re.findall(r'^(\d+)p\.', PLAIN_STEPS, re.M)]
    assert steps == list(range(3, 3 + len(steps)))


def test_the_plain_words_steps_do_not_forbid_what_the_first_sketch_says():
    from ismail.guide import PLAIN_STEPS
    assert 'say what is missing' not in PLAIN_STEPS    # the 10-08 first-try line asks what they would change
    assert 'what would you change?" on its own' in PLAIN_STEPS


def test_guards_imports_on_its_own():
    """guards imports api, and api imports api_guards, which read guards' word sets at import: a script or test that
    imported guards first failed with a circular import (found 10-16 running one test file)."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, '-W', 'ignore', '-c', 'import ismail.guards'], cwd=ROOT, capture_output=True,
                       text=True)
    assert r.returncode == 0, r.stderr[-400:]
