"""Jobs waiting in line cost little (ledger:M307): 23 `machine run --wait` waiters used 8.4% of the CPU together while
the machine was at 99%, each taking its own CPU sample and nvidia-smi reading every 5 s. Now one sample serves them
all, a waiter far back looks less often, and a job that ends wakes the line. Every test here uses a temporary board,
with the timings scaled down so the file runs in seconds."""
import os
import threading
import time

import pytest

from ismail import machine

COOL = {'temp': 60.0, 'clock': 139.0, 'max_clock': 1911.0, 'util': 0.0, 'reasons': 0x1, 'mem_used_gb': 0.5,
        'mem_total_gb': 8.0}
WAITERS = 20
WINDOW_S = 3.0


class PerThread:
    """A reading cache per thread, so each waiter thread stands for one `run --wait` process with its own."""

    def __init__(self, init):
        self.init, self.local = init, threading.local()

    def _v(self):
        if not hasattr(self.local, 'v'):
            self.local.v = list(self.init)
        return self.local.v

    def __getitem__(self, i):
        return self._v()[i]

    def __setitem__(self, i, v):
        self._v()[i] = v


class NoGpuPids:
    def tree(self, pids):
        return None

    def close(self):
        pass


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    os.makedirs(str(tmp_path / 'board'), exist_ok=True)
    counts, lock = {'cpu': 0, 'gpu': 0, 'look': 0}, threading.Lock()

    def cpu_percent(interval=None, *a, **k):
        if interval:                                      # a whole-machine sample (the one that takes CPU_SAMPLE_S)
            with lock:
                counts['cpu'] += 1
            time.sleep(interval)
        return 30.0

    def gpu_query():
        with lock:
            counts['gpu'] += 1
        time.sleep(0.01)
        return dict(COOL)
    real_check = machine.check

    def check(*a, **k):
        with lock:
            counts['look'] += 1
        return real_check(*a, **k)
    monkeypatch.setattr(machine, '_cpu_cache', PerThread([0.0, None]))
    monkeypatch.setattr(machine, '_gpu_cache', PerThread([0.0, None]))
    monkeypatch.setattr(machine.psutil, 'cpu_percent', cpu_percent)
    monkeypatch.setattr(machine.psutil, 'process_iter', lambda *a, **k: iter(()))
    monkeypatch.setattr(machine, '_gpu_query', gpu_query)
    monkeypatch.setattr(machine, 'check', check)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'disks', lambda *a: [('D:', 100.0)])
    monkeypatch.setattr(machine, 'pagefiles', lambda: [])
    monkeypatch.setattr(machine, 'disk_roots', lambda: [])
    monkeypatch.setattr(machine, '_GpuPids', NoGpuPids)
    monkeypatch.setattr(machine, '_GpuSampler', lambda: type('G', (), {'stop': lambda self: {}})())
    monkeypatch.setattr(machine, 'METER_S', 0.05)
    monkeypatch.setattr(machine, 'CPU_SAMPLE_S', 0.2)    # the 2 s sample, scaled
    monkeypatch.setattr(machine, 'CPU_FRESH_S', 0.5)     # one sample serves everyone this long
    monkeypatch.setattr(machine, 'GPU_FRESH_S', 0.5)
    depth = getattr(machine._held, 'depth', 0)
    machine._held.depth = 0                  # the suite's own slot (conftest) would make every slot pass through
    yield counts, lock
    machine._held.depth = depth


def _line_up(kind, n, holders, wait_s):
    """`holders` jobs hold every `kind` slot; n waiters stand in line behind them.
    -> (free, started, errors, threads)."""
    free, started, errors = threading.Event(), {}, []

    def holder(i):
        machine._held.depth = 0
        with machine.slot(kind, f'holder {i}', who=f'h{i}', force=True):
            free.wait(60)

    def waiter(i):
        machine._held.depth = 0
        try:
            with machine.slot(kind, f'waiter {i}', who=f'w{i}', wait=wait_s):
                started[i] = time.time()
        except machine.MachineBusy as e:
            errors.append(str(e))
    hs = [threading.Thread(target=holder, args=(i,), daemon=True) for i in range(holders)]
    for t in hs:
        t.start()
    t_end = time.time() + 10
    while len(machine.jobs()) < holders and time.time() < t_end:
        time.sleep(0.01)
    ws = [threading.Thread(target=waiter, args=(i,), daemon=True) for i in range(n)]
    for t in ws:
        t.start()
    t_end = time.time() + 20
    while len(machine.waiters()) < n and time.time() < t_end:
        time.sleep(0.02)
    assert len(machine.waiters()) == n
    return free, started, errors, hs + ws


def test_twenty_waiters_share_one_cpu_sample_per_interval(board, monkeypatch):
    """Before (the code of PR 338): with 23 waiters each its own process, 23 CPU samples, 91 nvidia-smi runs and 134
    looks at the board in 30 s. One sample per CPU_FRESH_S is the most it should take, however long the line."""
    counts, lock = board
    monkeypatch.setattr(machine, 'WAIT_POLL_S', 0.2)
    free, started, errors, ts = _line_up('cpu', WAITERS, holders=2, wait_s=WINDOW_S + 20)
    time.sleep(machine.CPU_FRESH_S)                       # everyone in line and settled
    with lock:
        c0 = dict(counts)
    t0 = time.time()
    time.sleep(WINDOW_S)
    with lock:
        got = {k: counts[k] - c0[k] for k in counts}
    dt = time.time() - t0
    free.set()
    for t in ts:
        t.join(30)
    intervals = dt / machine.CPU_FRESH_S
    print(f"{WAITERS} waiters, {dt:.1f} s ({intervals:.0f} sample intervals): {got['cpu']} CPU samples, "
          f"{got['gpu']} GPU readings, {got['look']} looks at the board")
    assert got['cpu'] <= intervals + 2, got               # one per interval in total, not one per waiter
    assert got['gpu'] <= dt / machine.GPU_FRESH_S + 2, got
    assert got['look'] < WAITERS * dt / machine.WAIT_POLL_S / 3, got   # the ones far back look less often
    assert errors == [] and len(started) == WAITERS       # and every one of them still ran, in turn


def test_a_job_that_ends_wakes_the_line_at_once(board, monkeypatch):
    """A waiter's own poll is 30 s here: only the wake on a job's end can start the line within the bound."""
    monkeypatch.setattr(machine, 'WAIT_POLL_S', 30.0)
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    n = 4
    free, started, errors, ts = _line_up('gpu', n, holders=1, wait_s=120)
    time.sleep(0.5)
    t_end = time.time()
    free.set()                                            # the GPU job ends
    for t in ts:
        t.join(30)
    assert errors == [] and len(started) == n, (errors, started)
    first = min(started.values()) - t_end
    last = max(started.values()) - t_end
    print(f"first waiter started {first:.2f} s after the job ended, all {n} within {last:.2f} s")
    assert first < 2.0, first                             # WAKE_TICK_S, plus the look and the board lock
    assert last < 2.0 * n + 2.0, last                     # each one's end wakes the next (its own poll is 30 s)


def test_a_dead_samplers_claim_holds_nobody_up(board, monkeypatch):
    counts, lock = board
    monkeypatch.setattr(machine, 'CLAIM_STALE_S', 0.5)
    claim = os.path.join(machine.board_dir(), 'cpu.json.claim')
    open(claim, 'w').close()
    old = time.time() - 5
    os.utime(claim, (old, old))                           # left by a sampler that died before its first sample
    t0 = time.time()
    busy, top = machine.cpu_load()
    assert busy == 30.0 and time.time() - t0 < 2.0 and counts['cpu'] == 1
    assert not os.path.exists(claim)
    machine._cpu_cache[:] = [0.0, None]
    assert machine.cpu_load() == (30.0, []) and counts['cpu'] == 1     # the next caller reads that sample
