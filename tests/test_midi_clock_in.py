"""MIDI clock in (MIDI parity step 8, part 1): the filter's tempo from synthetic ticks (jitter, a ramp and its lag in
ms, a step, lost ticks, a burst), then a silent engine following a synthetic clock: tempo and beat phase locked,
a ramp followed, Stop silencing the speakers and Start / Continue bringing the set in on their bar line, a Song
Position jump, a dropout holding the tempo, clock out and the arp on the followed tempo, live_tempo refused, and
clock in and clock out on one port refused. Every port is a stand-in fed messages with controlled timestamps on a
fake clock; no MIDI device opens, no audio plays, nothing renders to disk."""
import random
import re

import numpy as np
import pytest

from ismail import midisend as MS
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live import midi_play as MP
from ismail.live.clock_in import ClockFilter
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.midi_out import MidiOut
from ismail.live.midi_play import MidiPlay


def ticks(bpm, t0, t1, jitter=0.0, seed=1):
    """Tick times from t0 to t1 for a tempo bpm(t) (a number or a function), each arrival moved by Gaussian jitter."""
    f = bpm if callable(bpm) else (lambda t: bpm)
    r = random.Random(seed)
    out, t = [], t0
    while t < t1:
        out.append((t, t + (r.gauss(0, jitter) if jitter else 0.0)))
        t += 60.0 / (24 * f(t))
    return out


def run_filter(ts, B=0.5):
    f = ClockFilter(B)
    est = []
    for true, t in ts:
        f.tick(t)
        est.append((true, f.bpm() or 0.0))
    return f, np.array(est)


# ---------------------------------------------------------------------------------------------------- the filter
def test_steady_clock_with_jitter_reads_steady():
    f, est = run_filter(ticks(120, 0, 30, jitter=0.001))
    late = est[est[:, 0] > 8, 1]
    assert np.max(np.abs(late - 120)) < 0.25            # under the default tempo_tol_bpm: no tempo churn
    assert f.jit * 1000 == pytest.approx(1.0, rel=0.35)  # the jitter it reports
    _, est2 = run_filter(ticks(120, 0, 30, jitter=0.0))
    assert abs(est2[-1, 1] - 120) < 1e-6


def ramp(t):
    return 120.0 if t < 10 else 132.0 if t > 18 else 120.0 + 12.0 * (t - 10) / 8


def lag_ms(est, f, lo, hi):
    """The delay that best lines the estimate up with the true tempo, ms."""
    m = (est[:, 0] > lo) & (est[:, 0] < hi)
    lags = np.arange(0, 2.0, 0.005)
    errs = [np.max(np.abs(est[m, 1] - np.array([f(x - L) for x in est[m, 0]]))) for L in lags]
    return float(lags[int(np.argmin(errs))] * 1000), float(min(errs))


@pytest.mark.parametrize('B, lag', [(0.5, 430), (1.0, 205), (2.0, 95)])
def test_a_tempo_ramp_is_followed_with_the_documented_lag(B, lag):
    """The lag the module docstring and the op name: 430 ms at the default 0.5 Hz, 205 ms at 1 Hz, 95 ms at 2 Hz."""
    _, est = run_filter(ticks(ramp, 0, 30), B)
    got, err = lag_ms(est, ramp, 13, 17)
    print(f"bandwidth {B} Hz: ramp lag {got:.0f} ms (fit error {err:.3f} BPM)")
    assert got == pytest.approx(lag, rel=0.1) and err < 0.1
    assert abs(est[-1, 1] - 132) < 0.01                 # and it ends on the new tempo


def test_a_step_settles_within_two_seconds():
    step = lambda t: 120.0 if t < 10 else 126.0          # noqa: E731
    _, est = run_filter(ticks(step, 0, 20))
    after = est[est[:, 0] > 10]
    off = np.nonzero(np.abs(after[:, 1] - 126) >= 0.1)[0]
    settle = after[off[-1] + 1, 0] - 10 if len(off) else 0.0
    print(f"step 120 -> 126: within 0.1 BPM after {settle * 1000:.0f} ms")
    assert 1.0 < settle < 2.5


def test_lost_ticks_are_counted_and_a_burst_moves_little():
    ts = ticks(120, 0, 10)
    kept = [x for i, x in enumerate(ts) if i not in (300, 301, 302)]       # three ticks lost on the cable
    f = ClockFilter()
    n = sum(f.tick(t) for _, t in kept)
    assert n == len(ts) - 1 and abs(f.bpm() - 120) < 0.01      # periods since the first tick
    burst = [(a, b) for a, b in ts]
    for i in range(320, 330, 2):                     # pairs arriving together (a driver's buffer)
        burst[i] = (burst[i][0], burst[i + 1][1])
    f2 = ClockFilter()
    for _, t in burst:
        f2.tick(t)
    assert abs(f2.bpm() - 120) < 0.3


# ---------------------------------------------------------------------------------------------------- the engine
class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeInput:
    opened = {}

    def __init__(self, name, on_msg):
        self.name, self.on = name, on_msg
        FakeInput.opened[name] = self

    def close(self):
        FakeInput.opened.pop(self.name, None)


class FakePort:
    opened = {}
    clock = None

    def __init__(self, name):
        self.name, self.sent = name, []
        FakePort.opened[name] = self

    def send(self, msg):
        self.sent.append((FakePort.clock(), bytes(msg)))

    def reset(self):
        pass

    def close(self):
        pass


SRC = 'Test Clock'


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 48)
    monkeypatch.setattr(MP, 'HI', 60)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    clk = Clock()
    FakePort.opened, FakePort.clock = {}, clk
    FakeInput.opened = {}
    e.inputs = [SRC, 'test keys']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    e.mout = MidiOut(e, outputs=lambda: [SRC, 'Test Out'], opener=FakePort, now=clk, thread=False)
    e.clk = clk
    e.feed = []                                    # (arrival time, status, d1, d2) still to deliver
    return e


def clock(e, bpm, t0, t1, jitter=0.0, start=None, seed=1):
    """Queue their ticks from t0 to t1 (a Start / Continue message just before the first when given)."""
    ts = ticks(bpm, t0, t1, jitter, seed)
    if start is not None:
        e.feed.append((ts[0][1] - 0.002, start, 0, 0))
    e.feed += [(t, 0xF8, 0, 0) for _, t in ts]
    e.feed.sort(key=lambda m: m[0])
    return ts


def run(e, until):
    """The scheduler, the mixer and the device in turn on the fake clock up to `until`, each clock message given to
    the input at its own time. Returns the audio handed to the device."""
    out = []
    while e.clk.t < until:
        end = e.clk.t + BLOCK / SR
        while e.feed and e.feed[0][0] < end:
            t, st, d1, d2 = e.feed.pop(0)
            FakeInput.opened[SRC].on(t, st, d1, d2)
        e.tick()
        e._fifo.append(e.mix_block().T.copy())
        e._fifo_n += BLOCK
        out.append(e._pull(BLOCK))
        while (n := e.mout.sched.next_time()) is not None and n <= end:
            e.clk.t = max(e.clk.t, n)
            e.mout.sched.pump(e.clk.t)
        e.clk.t = end
    return np.concatenate(out) if out else np.zeros((0, 2))


def speaker(e, beat):
    """When ismail's `beat` leaves the device in run() (the device takes each block as it is mixed, from 100 s)."""
    return 100.0 + (e.sample(beat) + e.safety.la + G.LAT_BUDGET) / SR


def phase_ms(e, ts, t_lo, t_hi, offset=0.0):
    """Their beats (every 24th tick) against ismail's beat D after them: the speaker time minus theirs, ms."""
    D = e.cin.D
    origin = 0
    errs = []
    for i, (true, _) in enumerate(ts):
        if (i + origin) % 24 or not t_lo <= true <= t_hi:
            continue
        x = (i + origin) / 24
        errs.append((speaker(e, x + D) - true - offset) * 1000)
    return np.array(errs)


def kick(e):
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])


def test_tempo_only_follows_their_tempo_and_beat(eng):
    kick(eng)
    ts = clock(eng, 126, 100.3, 130, jitter=0.001)
    r = eng.cmd_midi_clock_in(source='clock', transport='tempo')
    assert "clock in <- 'Test Clock'" in r and '0.5 Hz loop filter' in r and 'about 430 ms late' in r
    run(eng, 115)
    n = eng.cin.changes
    run(eng, 130)
    assert eng.bpm == pytest.approx(126, abs=0.25)
    err = phase_ms(eng, ts, 115, 129.5)
    print(f"126 BPM, 1 ms jitter: phase {err.mean():+.2f} ms mean, {np.abs(err).max():.2f} max; "
          f"{eng.cin.changes - n} tempo changes in the last 15 s")
    assert np.abs(err).max() < 6.0
    assert eng.cin.changes - n <= 6        # a steady clock does not keep the set re-rendering (render lead varies)
    s = eng.cmd_status()
    assert re.search(r"clock in <- 'Test Clock': theirs 12[56][.0-9]* BPM \([.0-9]+ ms jitter\) \| set 12[56]", s)
    assert 'transport ignored (tempo only)' in s and 'tempo changes' in s and 'phase ' in s
    with pytest.raises(LiveError, match=r"follows MIDI clock in .*live_midi_clock_in\(on=False\)"):
        eng.cmd_tempo(100)


def test_a_ramp_is_followed_and_the_beat_stays_close(eng):
    kick(eng)
    f = lambda t: 120.0 if t < 108 else 130.0 if t > 124 else 120.0 + 10.0 * (t - 108) / 16    # noqa: E731
    ts = clock(eng, f, 100.2, 140)
    eng.cmd_midi_clock_in(source=SRC, transport='tempo')
    run(eng, 140)
    assert eng.bpm == pytest.approx(130, abs=0.25)
    during = phase_ms(eng, ts, 110, 124)
    after = phase_ms(eng, ts, 132, 139.5)
    print(f"ramp 120 -> 130 over 16 s: phase max {np.abs(during).max():.1f} ms during, {np.abs(after).max():.1f} "
          f"after; {eng.cin.changes} tempo changes")
    assert np.abs(during).max() < 40 and np.abs(after).max() < 6.0


def test_stop_silences_the_set_and_start_brings_it_in_on_their_bar_line(eng):
    kick(eng)
    eng.cmd_midi_out(track='k', port='Test Out')           # its notes to a synth too
    eng.cmd_midi_clock_in(source=SRC)                      # transport follow is the default
    clock(eng, 120, 100.1, 103)
    y1 = run(eng, 103)
    assert np.max(np.abs(y1[-SR:])) > 0.05                 # playing: ticks without a Start keep it playing
    eng.feed.append((103.01, 0xFC, 0, 0))                  # Stop: their clock stops too
    y2 = run(eng, 105)
    assert np.max(np.abs(y2[int(0.05 * SR):])) == 0        # silent from the next device buffer on
    assert 'STOPPED' in eng.cmd_status()
    ts = clock(eng, 124, 105.5, 118, start=0xFA)
    t3 = eng.clk.t
    y3 = run(eng, 118)
    news = '\n'.join(eng.news)
    assert 'Start from' in news and 'locked, their bar' in news
    xL, bL = eng.cin.locked
    assert xL % 4 == 0 and bL % 4 == 0
    t_in = speaker(eng, bL)
    t_their = ts[int(xL * 24)][0]
    assert abs(t_in - t_their) < 0.004                     # ismail's bar line on their bar line
    ons = [t for t, b in FakePort.opened['Test Out'].sent if b[0] == 0x99]
    assert ons and not [t for t in ons if 103.06 < t < t_in - 0.001]   # no MIDI notes while they are stopped
    assert min(t for t in ons if t > 103.06) == pytest.approx(t_in, abs=1e-6)
    first = t3 + np.argmax(np.abs(y3[:, 0]) > 1e-4) / SR
    assert first == pytest.approx(t_in, abs=0.006)         # and that is where the set is heard again
    assert np.abs(phase_ms(eng, ts, t_in + 2, 117.5)).max() < 6.0
    assert eng.bpm == pytest.approx(124, abs=0.25)
    s = eng.cmd_status()
    assert f"running, their bar {xL // 4 + 1} = ismail bar {bL // 4 + 1}" in s


def test_continue_lands_on_their_song_position(eng):
    kick(eng)
    eng.cmd_midi_clock_in(source=SRC)
    eng.feed.append((100.4, 0xFC, 0, 0))
    eng.feed.append((100.5, 0xF2, 64 & 0x7F, 64 >> 7))     # song position 64 16ths: their bar 5
    clock(eng, 120, 101.0, 112, start=0xFB)
    ts = [x for x in ticks(120, 101.0, 112)]
    run(eng, 112)
    xL, bL = eng.cin.locked
    assert xL >= 16 and xL % 4 == 0                         # their bar 5 or later
    t_their = ts[int((xL - 16) * 24)][0]
    assert abs(speaker(eng, bL) - t_their) < 0.004


def test_a_song_position_jump_while_running_relocks(eng):
    kick(eng)
    eng.cmd_midi_clock_in(source=SRC)
    clock(eng, 120, 100.2, 115, start=0xFA)
    run(eng, 106)
    first = eng.cin.locked
    eng.feed.append((106.001, 0xF2, 32, 0))                # they jump to bar 3 while playing
    eng.feed.sort(key=lambda m: m[0])
    run(eng, 115)
    assert 'they jumped to song position 32' in '\n'.join(eng.news)
    xL, bL = eng.cin.locked
    assert eng.cin.locked != first and not eng.cin.lock_req and xL >= 8 and xL % 4 == 0


def test_a_dropout_holds_the_tempo_and_says_so(eng):
    kick(eng)
    eng.cmd_midi_clock_in(source=SRC, transport='tempo')
    clock(eng, 126, 100.2, 108)
    run(eng, 109)
    held = eng.bpm
    assert held == pytest.approx(126, abs=0.25)
    s = eng.cmd_status()
    assert 'NO CLOCK for' in s and 'holding the last tempo' in s
    assert any('no clock from' in n and 'holding' in n for n in eng.news)
    y = run(eng, 111)
    assert eng.bpm == held and np.max(np.abs(y)) > 0.05    # the set plays on at the last tempo
    clock(eng, 126, 111.0, 116, seed=2)
    run(eng, 116)
    assert any('is back after' in n for n in eng.news) and 'NO CLOCK' not in eng.cmd_status()


def test_clock_out_and_the_arp_follow_the_followed_tempo(eng):
    eng.cmd_midi(source='test keys', instrument='preset:pluck')
    eng.cmd_arp(source='test keys', at='now', rate=1, gate=0.25)
    FakeInput.opened['test keys'].on(0.0, 0x90, 55, 100)
    eng.cmd_midi_clock(port='Test Out')
    eng.cmd_midi_clock_in(source=SRC, transport='tempo')
    ts = clock(eng, 132, 100.2, 118)
    y = run(eng, 118)
    out = FakePort.opened['Test Out']
    pulses = [t for t, b in out.sent if b == bytes([MS.PULSE]) and t > 112]
    gaps = np.diff(pulses)
    assert np.median(gaps) == pytest.approx(60 / (132 * 24), rel=0.002)
    steps = [r[0] for r in eng.arp.arps['test keys']['recent']]
    late = [b for b in steps if speaker(eng, b) > 112]
    assert len(late) >= 4
    D = eng.cin.D
    for b in late:                                         # each step on one of their beats
        x = b - D
        assert abs(speaker(eng, b) - ts[int(round(x * 24))][0]) < 0.006
    lag = eng.safety.la + G.LAT_BUDGET
    b = late[-1]
    seg = np.abs(y[eng.sample(b) + lag - 64:eng.sample(b) + lag + int(0.05 * SR), 0])
    assert np.max(seg[:64]) < np.max(seg) * 0.1 and np.max(seg) > 1e-3   # the step's sound starts on its beat


def test_one_port_for_clock_in_and_out_is_refused_both_ways(eng):
    eng.cmd_midi_clock(port='Test Clock')                  # an output with the clock input's name: one port
    with pytest.raises(LiveError, match=r"chase itself.*live_midi_clock\(on=False\)"):
        eng.cmd_midi_clock_in(source=SRC)
    eng.cmd_midi_clock(on=False)
    eng.cmd_midi_clock_in(source=SRC)
    with pytest.raises(LiveError, match=r"chase its own tempo.*live_midi_clock_in\(on=False\)"):
        eng.cmd_midi_clock(port='Test Clock')
    assert 'clock out ->' in eng.cmd_midi_clock(port='Test Out')


def test_settings_errors_and_off(eng):
    with pytest.raises(LiveError, match=r"bandwidth_hz=9: 0.05 to 5"):
        eng.cmd_midi_clock_in(source=SRC, bandwidth_hz=9)
    with pytest.raises(LiveError, match=r"transport='bars'"):
        eng.cmd_midi_clock_in(source=SRC, transport='bars')
    with pytest.raises(LiveError, match=r"sends no clock"):
        eng.cmd_midi_clock_in(source='screen keys')
    with pytest.raises(LiveError, match=r"name the input"):
        eng.cmd_midi_clock_in(phase_tol_ms=2)
    assert 'clock in: off' in eng.cmd_midi_clock_in()
    r = eng.cmd_midi_clock_in(source=SRC, step_beats=2, bandwidth_hz=1)
    assert 'about 215 ms late' in r and 'at most every 2 beats' in r and 'waiting for clock' in r
    assert SRC in FakeInput.opened
    r = eng.cmd_midi_clock_in(on=False)
    assert 'clock in off' in r and 'live_tempo works again' in r and SRC not in FakeInput.opened
    assert 'tempo 120 -> 100' in eng.cmd_tempo(100)
