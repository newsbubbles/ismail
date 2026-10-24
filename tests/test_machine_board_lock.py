"""The board lock serves callers in arrival order (ledger:M303): with about 23 jobs waiting, a new `machine run` lost
the race for the lock again and again and exited 75 after 10 s. Every test here uses a temporary board."""
import json
import os
import random
import subprocess
import sys
import threading
import time

import psutil
import pytest

from ismail import machine

POLLERS = 30          # callers that take the lock over and over, as waiters looking at the board did
ENQUEUERS = 10        # callers that each put one job on the board once
BOUND_S = 5.0         # each enqueue gets the lock within this, however busy the line (the lock gives up at 30)


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    os.makedirs(str(tmp_path / 'board'), exist_ok=True)
    return tmp_path / 'board'


def _rewrite(path, fn):
    with open(path, encoding='utf8') as f:
        data = json.load(f)
    data = fn(data)
    with open(path, 'w', encoding='utf8') as f:
        json.dump(data, f)


def test_every_enqueue_gets_the_lock_in_bounded_time_with_30_pollers_and_no_job_is_lost(board):
    path = os.path.join(str(board), 'board.json')
    with open(path, 'w', encoding='utf8') as f:
        json.dump({'jobs': [], 'polls': 0}, f)
    stop, errors, waits, inside, overlaps = threading.Event(), [], [], [0], [0]

    def poller():
        try:
            while not stop.is_set():
                with machine._board_lock():
                    inside[0] += 1
                    overlaps[0] += inside[0] > 1
                    _rewrite(path, lambda d: dict(d, polls=d['polls'] + 1))
                    time.sleep(0.002)                     # a short look at the board under the lock
                    inside[0] -= 1
                time.sleep(random.uniform(0.0, 0.01))     # and straight back for another
        except Exception as e:                            # noqa: BLE001
            errors.append(repr(e))

    def enqueuer(n):
        try:
            time.sleep(random.uniform(0.2, 0.8))          # arrive while the pollers hold the line
            t0 = time.time()
            with machine._board_lock():
                waits.append(time.time() - t0)
                inside[0] += 1
                overlaps[0] += inside[0] > 1
                _rewrite(path, lambda d: dict(d, jobs=d['jobs'] + [f'job-{n}']))
                inside[0] -= 1
        except Exception as e:                            # noqa: BLE001
            errors.append(repr(e))
    ps = [threading.Thread(target=poller) for _ in range(POLLERS)]
    es = [threading.Thread(target=enqueuer, args=(n,)) for n in range(ENQUEUERS)]
    for t in ps + es:
        t.start()
    for t in es:
        t.join(30)
    stop.set()
    for t in ps:
        t.join(30)
    assert errors == [] and overlaps[0] == 0
    print(f"worst enqueue wait with {POLLERS} pollers: {max(waits):.3f} s")
    assert len(waits) == ENQUEUERS and max(waits) < BOUND_S, waits
    with open(path, encoding='utf8') as f:
        data = json.load(f)                               # still valid JSON
    assert sorted(data['jobs']) == sorted(f'job-{n}' for n in range(ENQUEUERS)) and data['polls'] > 0
    assert not os.path.exists(os.path.join(str(board), 'lock'))
    assert os.listdir(os.path.join(str(board), machine.LOCK_LINE)) == []     # every ticket left the line


def test_callers_are_served_in_the_order_they_arrived(board):
    """Before the line, whoever happened to look at the right moment won: a caller could lose every time."""
    line = os.path.join(str(board), machine.LOCK_LINE)
    order, errors = [], []

    def caller(n):
        try:
            with machine._board_lock(timeout=20):
                order.append(n)
        except Exception as e:                            # noqa: BLE001
            errors.append(repr(e))
    ts = []
    with machine._board_lock():                           # the lock is taken while the callers line up
        for n in range(6):
            t = threading.Thread(target=caller, args=(n,))
            t.start()
            ts.append(t)
            t_end = time.time() + 10
            while len(os.listdir(line)) < n + 1 and time.time() < t_end:    # its ticket is in the line
                time.sleep(0.005)
    for t in ts:
        t.join(30)
    assert errors == [] and order == list(range(6))


def _dead_pid():
    p = subprocess.Popen([sys.executable, '-c', 'pass'])
    p.wait()
    assert not psutil.pid_exists(p.pid)
    return p.pid


def test_a_dead_callers_ticket_and_a_dead_holders_lock_hold_nobody_up(board):
    pid = _dead_pid()
    line = os.path.join(str(board), machine.LOCK_LINE)
    os.makedirs(line, exist_ok=True)
    old = time.time() - 5
    until = time.time() + 600                             # first in line with a long wait ahead, then died
    open(os.path.join(line, f"{int(old * 1e9):020d}_{int(until * 1e9):020d}_{pid}_1_aaaaaa"), 'w').close()
    lock = os.path.join(str(board), 'lock')
    with open(lock, 'w', encoding='utf8') as f:
        f.write(f"{pid} 1 {old!r}")                       # died holding the lock, younger than STALE_LOCK_S
    os.utime(lock, (old, old))
    t0 = time.time()
    with machine._board_lock(timeout=10):
        took = time.time() - t0
        assert open(lock, encoding='utf8').read().split()[0] == str(os.getpid())
    assert took < 2.0, took                               # not the 30 s a stale lock used to need
    assert os.listdir(line) == [] and not os.path.exists(lock)


def test_a_genuine_timeout_says_what_to_do_and_leaves_the_line(board):
    lock = os.path.join(str(board), 'lock')
    with open(lock, 'w', encoding='utf8') as f:
        f.write(f"{os.getpid()} 1 {time.time()!r}")       # a live holder that keeps it
    with pytest.raises(machine.MachineBusy, match=r'run --wait 30m') as e:
        with machine._board_lock(timeout=0.3):
            pass
    assert 'nothing was registered' in str(e.value)
    assert os.listdir(os.path.join(str(board), machine.LOCK_LINE)) == []
    assert open(lock, encoding='utf8').read().startswith(str(os.getpid()))   # a live holder's lock is left alone
