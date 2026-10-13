"""Every ismail.stage module imports on its own, in a fresh interpreter. A tool that starts at a stage module
(python -m ismail.stage.ear) loads it before ismail.api; an import cycle through api only fails that way, never in a
test process that already imported api."""
import subprocess
import sys
from pathlib import Path

import pytest

STAGE = Path(__file__).resolve().parents[1] / 'ismail' / 'stage'
MODULES = sorted(f'ismail.stage.{p.stem}' for p in STAGE.glob('*.py') if p.stem != '__init__')


@pytest.mark.parametrize('module', MODULES)
def test_a_stage_module_imports_first_in_a_fresh_interpreter(module):
    r = subprocess.run([sys.executable, '-c', f'import {module}'], cwd=STAGE.parents[1],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f'{module}: {r.stderr.strip().splitlines()[-1:]}'


def test_the_list_covers_the_stage_modules():
    assert {'ismail.stage.link', 'ismail.stage.ear', 'ismail.stage.ops'} <= set(MODULES)
