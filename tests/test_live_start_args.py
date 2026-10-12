"""live_track's output, live_start's set loudness and engine priority (hq:D-131): the op passes a track's output bus
to the engine, live_start takes the trim and cap as arguments that win over the environment, and live_start sets
the engine's priority in code. No engine process and no audio device: the ops talk to an in-process Engine with
device='none', or stop at the point where they would start one."""
import os
import subprocess
import sys

import pytest

from ismail.api import OPS, OpError
from ismail.live import ops
from ismail.live.engine import PRIORITIES, Engine, LiveError, set_priority
from ismail.live.safety import CAP_RANGE, TRIM_RANGE, Safety


@pytest.fixture
def eng(tmp_path, monkeypatch):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    cmds = {'track': e.cmd_track, 'bus': e.cmd_bus}
    monkeypatch.setattr(ops, '_call', lambda project, name, timeout=30, **args: cmds[name](**args))
    return e


def test_live_track_routes_a_new_track_to_its_output_bus(eng, tmp_path):
    OPS['live_bus'](str(tmp_path), 'objbus')
    OPS['live_track'](str(tmp_path), 'p', instrument='preset:pluck', output='objbus')
    assert eng.tracks['p']['output'] == 'objbus'
    OPS['live_track'](str(tmp_path), 'p', volume_db=-3, output='objbus')         # the same output again is fine
    with pytest.raises(LiveError, match='set when it is created'):
        OPS['live_track'](str(tmp_path), 'p', output='otherbus')
    with pytest.raises(LiveError, match='does not exist'):
        OPS['live_track'](str(tmp_path), 'q', instrument='preset:pluck', output='nobus')
    with pytest.raises(OpError, match='name of a live bus'):
        OPS['live_track'](str(tmp_path), 'q', instrument='preset:pluck', output=3)


def test_set_loudness_arguments_win_over_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_LIVE_TRIM_DB', '-9')
    monkeypatch.setenv('ISMAIL_LIVE_CAP_DB', '-20')
    e = Engine(str(tmp_path), bpm=120, workers=0, device='none', trim_db=4, cap_db=-12)
    assert (e.safety.trim_db, e.safety.cap_db) == (4.0, -12.0)
    assert 'trim 4 dB' in e.cmd_status() and 'cap -12 dBFS' in e.cmd_status()
    e = Engine(str(tmp_path), bpm=120, workers=0, device='none')                  # left out: the environment
    assert (e.safety.trim_db, e.safety.cap_db) == (-9.0, -20.0)
    s = Safety(trim_db=50, cap_db=0)                                                # the engine holds the range
    assert (s.trim_db, s.cap_db) == (TRIM_RANGE[1], CAP_RANGE[1])


class _Started(Exception):
    pass


def test_live_start_passes_loudness_and_priority_to_the_engine(tmp_path, monkeypatch):
    seen = {}

    def popen(argv, **kw):
        seen['argv'] = argv
        raise _Started

    monkeypatch.setattr(ops, '_alive', lambda project: False)
    monkeypatch.setattr(ops, '_other_engines', lambda project: [])
    monkeypatch.setattr(ops.subprocess, 'Popen', popen)
    with pytest.raises(_Started):
        OPS['live_start'](str(tmp_path), bpm=120, device='none', trim_db=4, cap_db=-12)
    argv = seen['argv']
    assert argv[argv.index('--trim-db') + 1] == '4.0' and argv[argv.index('--cap-db') + 1] == '-12.0'
    assert argv[argv.index('--priority') + 1] == 'above_normal'
    with pytest.raises(_Started):
        OPS['live_start'](str(tmp_path), bpm=120, device='none', priority='high')
    assert '--trim-db' not in seen['argv'] and seen['argv'][seen['argv'].index('--priority') + 1] == 'high'
    for bad in ({'trim_db': 40}, {'cap_db': -3}, {'cap_db': 'loud'}, {'priority': 'realtime'}):
        with pytest.raises(OpError):
            OPS['live_start'](str(tmp_path), bpm=120, device='none', **bad)


def _priority_class(pid):
    import ctypes
    k = ctypes.WinDLL('kernel32')
    k.OpenProcess.restype = ctypes.c_void_p
    k.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.OpenProcess(0x1000, False, pid)
    try:
        return k.GetPriorityClass(h)
    finally:
        k.CloseHandle(h)


def test_set_priority_sets_the_named_process():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        out = set_priority('above_normal', child.pid)
        assert out.startswith('priority above_normal')
        if os.name == 'nt':
            assert _priority_class(child.pid) == PRIORITIES['above_normal'][0]
            set_priority('normal', child.pid)
            assert _priority_class(child.pid) == PRIORITIES['normal'][0]
        with pytest.raises(ValueError):
            set_priority('realtime', child.pid)
    finally:
        child.kill()
        child.wait()
