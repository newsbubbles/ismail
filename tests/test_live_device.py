"""The live output follows the device (ledger M34): with device 'default' a set moves to a speaker that becomes the
system default (a Bluetooth speaker connecting), a device that stops taking audio falls back to the default, and
live_device moves a running set by hand, its clock carrying on. Silent: device='none' and a fake stream."""
import time

import pytest

from ismail.dsp import SR
from ismail.live import outputs as O
from ismail.live.engine import BLOCK, Engine


def run(eng, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        eng.tick()
        eng.mix_block()


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    yield e
    e.running = False                                   # a silent clock thread started in a test ends with it


class _FakeStream:
    device = 0

    def stop(self):
        pass

    def close(self):
        pass


def test_the_output_follows_a_new_default_and_recovers_a_vanished_device(eng, monkeypatch):
    opened = []

    def fake_open(device, rescan=True):
        opened.append(device)
        eng.stream, eng.out_name = _FakeStream(), {'default': 'Speakers (JBL Go 3)'}.get(device, str(device))
        eng.last_pull = time.time()
    monkeypatch.setattr(eng, '_open_output', fake_open)
    eng.device, eng.stream, eng.out_name, eng.last_pull = 'default', _FakeStream(), 'Laptop speakers', time.time()
    monkeypatch.setattr(O, 'default_output_name', lambda timeout=5.0: 'Speakers (JBL Go 3)')
    eng._check_output()
    assert opened == ['default'] and eng.out_name == 'Speakers (JBL Go 3)'
    assert any('moved from Laptop speakers to Speakers (JBL Go 3)' in x for x in eng.news)
    eng._check_output()                               # nothing changed: nothing happens
    assert opened == ['default']
    # a named device that stops taking audio falls back to the default
    eng.device, eng.out_name, eng._out_tried = 'Headphones', 'Headphones', 0.0
    eng.last_pull -= 10
    eng._check_output()
    assert opened[-1] == 'default' and eng.device == 'default'
    assert 'output Speakers (JBL Go 3) (follows the default)' in eng.cmd_status()


def test_follow_off_stays_where_it_opened(eng, monkeypatch):
    monkeypatch.setattr(eng, '_open_output', lambda device, rescan=True: pytest.fail('reopened'))
    eng.device, eng.stream, eng.out_name, eng.last_pull, eng.follow = 'default', _FakeStream(), 'Laptop', \
        time.time(), False
    monkeypatch.setattr(O, 'default_output_name', lambda timeout=5.0: 'Speakers (JBL Go 3)')
    eng._check_output()
    assert eng.out_name == 'Laptop'


def test_moving_to_no_device_and_back_keeps_the_clock(eng):
    run(eng, 0.5)
    pos = eng.pos
    out = eng.cmd_device('none')
    assert 'output: none' in out and eng.stream is None
    run(eng, 0.5)
    assert eng.pos > pos


def _fake_speaker(eng, monkeypatch):
    """_open_output with a fake stream for a named device: 'none' stays real (the silent clock thread)."""
    real = eng._open_output

    def fake_open(device, rescan=True):
        if eng_none(device):
            return real(device, rescan)
        eng._null_on = False
        eng.stream, eng.out_name, eng.last_pull = _FakeStream(), f"Speakers ({device})", time.time()
    monkeypatch.setattr(eng, '_open_output', fake_open)


def eng_none(device):
    from ismail.live.engine import is_none_device
    return is_none_device(device)


def _clocks():
    import threading
    return [t for t in threading.enumerate() if t.name == 'live-null-clock' and t.is_alive()]


def test_the_room_speaker_goes_off_and_back_on_mid_set(eng, monkeypatch):
    # ledger M130: the listener moved to the phone; the room speaker goes off without a restart and comes back the same way
    _fake_speaker(eng, monkeypatch)
    t0 = time.time()
    while _clocks() and time.time() - t0 < 2:          # an earlier test's clock is still ending
        time.sleep(0.01)
    try:
        eng.cmd_device('default')
        assert eng.out_name == 'Speakers (default)' and not _clocks()
        out = eng.cmd_device('None')                   # any spelling
        assert out.startswith('output: none (was Speakers (default))') and 'nothing plays in the room' in out
        assert eng.device == 'none' and eng.stream is None
        st = eng.cmd_status()
        assert '| output none' in st.splitlines()[0] and 'output off: nothing plays in the room' in st
        # quick none -> speaker -> none: the first silent clock must not wake beside the second
        eng.cmd_device('default')
        eng.cmd_device('none')
        time.sleep(0.1)
        assert len(_clocks()) == 1
        out = eng.cmd_device('default')
        assert out == 'output: Speakers (default) (was none); follows the system default'
        time.sleep(0.1)
        assert not _clocks() and 'output off' not in eng.cmd_status()
    finally:
        eng.running = False


def test_a_set_started_silent_still_watches_its_output(eng, monkeypatch):
    # started with device 'none' and moved to a speaker later: it must still follow the default and catch a stall
    import contextlib
    import sys
    from ismail.live import engine as E
    started = []

    class NoThread:
        def __init__(self, target=None, args=(), daemon=None, name=None):
            started.append(target)

        def start(self):
            pass
    monkeypatch.setattr(E.threading, 'Thread', NoThread)
    monkeypatch.setattr(E.machine, 'slot', lambda *a, **k: contextlib.nullcontext())
    switch = sys.getswitchinterval()
    try:
        eng.start()
    finally:
        sys.setswitchinterval(switch)
        import gc
        gc.unfreeze()
    assert eng._watch_output in started and eng._null_loop in started
