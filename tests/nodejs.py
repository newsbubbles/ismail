"""Page code run in node, for the stage tests: one script per call (`node -e`), its stdout read back as JSON.

The first node launch of a run can be slow on a fresh Windows CI machine: three times (2026-10-12, 10-15 twice) the
session's first `node -e` timed out at 60 s, while the same call takes about 0.1 s and every later launch in those
runs was quick. So the first launch is a warm-up of its own (`node -e 0`, up to WARM_S), outside any check, and a
slow one says so in the warnings summary. A call that still times out is run once more (it is a pure script: the
answer is the same, and a script that hangs times out twice and fails); that is a warning too, so it stays seen.
"""
import json
import shutil
import subprocess
import time
import warnings

import pytest

WARM_S = 300                 # the first launch of the run, before any check
RUN_S = 60                   # each script (they finish in well under a second)
SLOW_START_S = 5             # a first launch slower than this is named in the warnings summary
_WARM = []


def warm():
    if _WARM:
        return
    t = time.time()
    try:
        subprocess.run(['node', '-e', '0'], capture_output=True, timeout=WARM_S)
    except subprocess.TimeoutExpired:
        pytest.fail(f'node did not start in {WARM_S} s')
    _WARM.append(time.time() - t)
    if _WARM[0] > SLOW_START_S:
        warnings.warn(f'the first node launch took {_WARM[0]:.0f} s')


def run(js):
    """Run js in node and return what it printed, parsed as JSON (skips when node is not installed)."""
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    warm()
    for attempt in (1, 2):
        t = time.time()
        try:
            r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=RUN_S)
            break
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise
            warnings.warn(f'a node script timed out after {time.time() - t:.0f} s; running it once more')
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)
