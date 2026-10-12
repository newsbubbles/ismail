"""One object menu at a time, and a pick made while the last menu closes is kept (page/menugate.js, run in node)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

GATE = (Path(__file__).resolve().parents[1] / 'ismail' / 'stage' / 'page' / 'menugate.js').as_uri()

# a fake menu: run(it) logs 'open <name>' and stays up until close() (a deselect or another pick) ends it a tick later,
# the way panels.show resolves after panels.close
HARNESS = """
let selected = null, closeNow = null;
const log = [];
const tick = () => new Promise((r) => setTimeout(r, 0));
const run = (it) => new Promise((res) => { log.push('open ' + it); closeNow = () => setTimeout(res, 0); });
const close = () => { const c = closeNow; closeNow = null; if (c) c(); };
"""


def _node(body):
    if not shutil.which('node'):
        pytest.skip('node is not installed')
    js = f"{HARNESS}\nimport('{GATE}').then(async (m) => {{ const g = m.menuGate(run, () => selected); {body} }});"
    r = subprocess.run(['node', '-e', js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_picking_another_thing_while_a_menu_is_up_opens_its_menu_once_the_first_closes():
    got = _node("""
      selected = 'drum_kit'; g.show('drum_kit'); await tick();
      selected = 'dell'; close(); g.show('dell');          // the select handler closes the kit's menu, then shows Dell's
      const busyThen = g.busy();
      await tick(); await tick(); await tick();
      console.log(JSON.stringify({ log, busyThen }));""")
    assert got['busyThen'] is True                         # the kit's loop had not ended yet: the old code dropped Dell here
    assert got['log'] == ['open drum_kit', 'open dell']


def test_a_pick_kept_while_busy_is_dropped_if_the_selection_moved_on():
    got = _node("""
      selected = 'drum_kit'; g.show('drum_kit'); await tick();
      selected = 'dell'; close(); g.show('dell');
      selected = null;                                     // deselected before the kit's menu finished closing
      await tick(); await tick(); await tick();
      console.log(JSON.stringify({ log }));""")
    assert got['log'] == ['open drum_kit']


def test_only_the_last_pick_made_while_busy_opens():
    got = _node("""
      selected = 'drum_kit'; g.show('drum_kit'); await tick();
      g.show('dell'); g.show('jimmy'); selected = 'jimmy'; close();
      await tick(); await tick(); await tick();
      console.log(JSON.stringify({ log }));""")
    assert got['log'] == ['open drum_kit', 'open jimmy']


def test_a_pick_of_the_thing_whose_menu_just_opened_does_not_open_it_twice():
    got = _node("""
      selected = 'dell'; g.show('dell');
      g.show('dell'); g.shown('dell');                     // picked again while opening; the loop then puts the menu up
      await tick(); close(); await tick(); await tick(); await tick();
      console.log(JSON.stringify({ log }));""")
    assert got['log'] == ['open dell']
