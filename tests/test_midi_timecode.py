"""MIDI Time Code (MIDI parity step 8, part 2): the pure arithmetic (every rate's frames and labels both ways,
29.97 drop-frame at its minute boundaries, the wrap at 24 hours, quarter frames and the full frame byte by byte, the
decoder forward, backwards and across midnight), then a silent engine sending timecode out (each quarter frame on
its sample, a tempo change not moving it, a locate) and chasing a synthetic timecode in (their bar grid locked,
their speed followed, a stop, a locate, a jump, running backwards, a rate pinned or changed, clock in leading when
both are on, live_tempo moving the grid). Every port is a stand-in fed messages with controlled timestamps on a fake
clock; no MIDI device opens, no audio plays, nothing renders to disk."""
import ctypes
import random
import re
import sys

import numpy as np
import pytest

from ismail import api
from ismail import midi as M
from ismail import mtc as T
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live import midi_play as MP
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.midi_out import MidiOut
from ismail.live.midi_play import MidiPlay

import ismail.live.ops  # noqa: F401  (registers the live ops)


# ---------------------------------------------------------------------------------------------------- arithmetic
@pytest.mark.parametrize('rate, day', [(24, 2073600), (25, 2160000), (29.97, 2589408), (30, 2592000)])
def test_every_rate_counts_its_day_and_round_trips_frames_and_labels(rate, day):
    assert T.frames_per_day(rate) == day
    for n in list(range(0, 20000, 7)) + list(range(day - 4000, day)) + [day // 2, day // 3]:
        tc = T.frames_to_tc(n, rate)
        assert T.tc_to_frames(tc, rate) == n, (n, tc)
    assert T.fmt_frames(day - 1, rate)[:8] == '23:59:59' and T.frames_to_tc(day, rate) == (0, 0, 0, 0)
    assert T.frames_to_tc(-1, rate) == T.frames_to_tc(day - 1, rate)          # back from midnight
    sec = T.frames_to_seconds(day, rate)
    assert sec == pytest.approx(86400 if rate != 29.97 else 86399.9136, abs=1e-6)
    assert T.seconds_to_frames(T.frames_to_seconds(12345, rate), rate) == pytest.approx(12345, abs=1e-6)


def test_drop_frame_skips_two_numbers_each_minute_but_every_tenth():
    df = 29.97
    n = T.tc_to_frames((0, 0, 59, 29), df)
    assert T.fmt_frames(n + 1, df) == '00:01:00;02'                           # 00;00 and 00;01 are skipped
    assert T.fmt_frames(T.tc_to_frames((0, 9, 59, 29), df) + 1, df) == '00:10:00;00'   # not at a tenth minute
    assert T.fmt_frames(T.tc_to_frames((0, 19, 59, 29), df) + 1, df) == '00:20:00;00'
    assert T.fmt_frames(T.tc_to_frames((1, 0, 59, 29), df) + 1, df) == '01:01:00;02'
    assert T.tc_to_frames((0, 10, 0, 0), df) == 17982 and T.tc_to_frames((1, 0, 0, 0), df) == 107892
    for m in range(60):                                                       # every minute's first label
        first = (0, m, 0, 0 if m % 10 == 0 else 2)
        n = T.tc_to_frames(first, df)
        assert T.frames_to_tc(n, df) == first and T.frames_to_tc(n - 1, df) == (0, m - 1 if m else 59, 59, 29) \
            or m == 0
    with pytest.raises(ValueError, match=r"does not exist in drop-frame.*starts at 00:01:00;02"):
        T.tc_to_frames((0, 1, 0, 1), df)
    assert T.tc_to_frames((0, 1, 0, 0), 30) == 1800                           # 30 non-drop keeps every number
    # an hour of drop-frame labels is an hour on the wall within 4 ms; non-drop at 30000/1001 would be 3.6 s off
    assert abs(T.frames_to_seconds(T.tc_to_frames((1, 0, 0, 0), df), df) - 3600) < 0.004


def test_quarter_frames_carry_the_label_and_the_rate_nibble_by_nibble():
    tc = (23, 59, 58, 29)
    assert T.quarter_frames(tc, 30) == [0x0D, 0x11, 0x2A, 0x33, 0x4B, 0x53, 0x67, 0x77]
    for rate, code in T.CODE.items():
        last = T.quarter_frames((17, 0, 0, 0), rate)[7]
        assert last >> 4 == 7 and (last >> 1) & 3 == code and last & 1 == 1  # 17 = 0x11: hours' top bit set
    q = 4 * T.tc_to_frames((1, 0, 0, 10), 25) + 5                           # piece 5 of 01:00:00:10
    assert T.quarter_frame(q, 25) == bytes([0xF1, 0x50])                     # minutes high nibble: 0
    assert T.quarter_frame(q + T.quarters_per_day(25), 25) == T.quarter_frame(q, 25)   # wraps at 24 hours
    assert T.full_frame((1, 2, 3, 4), 29.97) == bytes.fromhex('f07f7f010141020304f7')
    assert T.read_full_frame(T.full_frame((10, 20, 30, 24), 25)) == ((10, 20, 30, 24), 25)
    assert T.read_full_frame(bytes.fromhex('f07e7f060102f7')) is None         # another SysEx
    assert T.read_full_frame(bytes.fromhex('f07f7f010101020304f7')[:-1] + b'\x00') is None


def stream(rate, q0, n, step=1):
    return [(q, T.quarter_frame(q, rate)[1]) for q in range(q0, q0 + step * n, step)]


@pytest.mark.parametrize('rate', T.RATES)
def test_the_decoder_tells_the_time_after_eight_in_order_then_every_quarter_frame(rate):
    q0 = 4 * T.tc_to_frames((0, 59, 59, 2), rate) // 8 * 8 + 3              # mid-cycle: waits for a piece 0
    dec = T.QuarterFrames()
    got = [(q, dec.feed(d)) for q, d in stream(rate, q0, 40)]
    first = next(i for i, (_, r) in enumerate(got) if r is not None)
    assert first == 12                                                       # 5 to the cycle's end, then 0 to 7
    assert got[first][1].complete and got[first][1].direction == 1 and got[first][1].rate == rate
    assert all(r.q == q % T.quarters_per_day(rate) for q, r in got[first:])
    assert sum(r.complete for _, r in got[first:]) == 4                     # one whole label each two frames


@pytest.mark.parametrize('rate', T.RATES)
def test_the_decoder_runs_backwards_and_says_so(rate):
    q0 = 4 * T.tc_to_frames((2, 0, 0, 20), rate)
    dec = T.QuarterFrames()
    got = [(q, dec.feed(d)) for q, d in stream(rate, q0, 40, step=-1)]
    seen = [(q, r) for q, r in got if r is not None]
    assert len(seen) >= 30 and all(r.direction == -1 and r.q == q for q, r in seen)
    done = [r for _, r in seen if r.complete]
    assert done and all(r.q % 8 == 0 and r.q == 4 * T.tc_to_frames(r.tc, rate) for r in done)


@pytest.mark.parametrize('rate', T.RATES)
def test_the_decoder_wraps_at_midnight(rate):
    day = T.quarters_per_day(rate)
    dec = T.QuarterFrames()
    got = [(q, dec.feed(d)) for q, d in stream(rate, day - 48, 96)]
    seen = [(q, r) for q, r in got if r is not None]
    assert all(r.q == q % day for q, r in seen)
    labels = [T.fmt_tc(r.tc, rate) for _, r in seen if r.complete]
    assert labels[0].startswith('23:59:59') and labels[-1].startswith('00:00:00')
    near = seen[0][1].q
    for q, r in seen:                                                        # one line through midnight
        u = T.unwrap(r.q, near, rate)
        assert u == q
        near = u


def test_a_lost_piece_or_a_label_that_cannot_exist_waits_for_the_next_eight():
    s = stream(25, 4 * 1000, 48)
    dec = T.QuarterFrames()
    got = [dec.feed(d) for i, (_, d) in enumerate(s) if i != 20]             # piece 4 of the third cycle lost
    after = [i for i, r in enumerate(got) if r is not None]
    assert after[0] == 7 and 19 in after and 30 in after                    # silent until the next cycle is whole
    assert not [i for i in after if 20 <= i < 30]
    dec = T.QuarterFrames()
    bad = [0x0E, 0x11, 0x20, 0x30, 0x40, 0x50, 0x60, 0x72]                   # frame 30 at 25 fps
    assert all(dec.feed(d) is None for d in bad) and dec.bad == 1


def test_rates_labels_and_offsets_from_words():
    assert [T.rate_of(x) for x in (24, '25', 29.97, '29.97df', '30df', 'df', 30, '30 fps')] == \
        [24, 25, 29.97, 29.97, 29.97, 29.97, 30, 30]
    with pytest.raises(ValueError, match=r"rate=50: 24, 25, 29.97"):
        T.rate_of(50)
    assert T.parse_tc('01:02:03;04', 29.97) == (1, 2, 3, 4)
    with pytest.raises(ValueError, match=r"frames 0 to 24"):
        T.parse_tc('00:00:00:25', 25)
    assert T.offset_frames('01:00:00:00', 25) == 90000 and T.offset_frames(2.0, 25) == 50
    with pytest.raises(ValueError, match=r"offset=-1: 0 up to 24 hours"):
        T.offset_frames(-1, 25)


def test_a_sysex_is_checked_before_it_goes_out():
    assert M._sysex(T.full_frame((0, 0, 0, 0), 25)) == T.full_frame((0, 0, 0, 0), 25)
    with pytest.raises(M.MidiError, match=r"starts with F0, ends with F7"):
        M._sysex(b'\xf0\x7f\x80\xf7')


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_sysex_out_and_in_through_a_stand_in_driver(monkeypatch):
    """The header winmm reads (MIDIHDR: 120 bytes on 64-bit Windows, 64 on 32-bit), a SysEx handed to
    midiOutLongMsg and its buffer let go once the driver marks it done, and SysEx in put back together across two
    buffers, each queued again. A stand-in driver: no MIDI device opens."""
    assert ctypes.sizeof(M._MIDIHDR) == (120 if ctypes.sizeof(ctypes.c_void_p) == 8 else 64)
    sent, unprep, added = [], [], []

    class W:
        def midiOutPrepareHeader(self, h, ph, n):
            return 0

        def midiOutLongMsg(self, h, ph, n):
            hd = ph._obj
            sent.append(ctypes.string_at(hd.lpData, hd.dwBufferLength))
            hd.dwFlags |= 1                                      # MHDR_DONE: the driver has sent it
            return 0

        def midiOutUnprepareHeader(self, h, ph, n):
            unprep.append(n)
            return 0

        def midiInAddBuffer(self, h, ph, n):
            added.append(n)
            return 0
    monkeypatch.setattr(M, '_w', W())
    out = object.__new__(M.Output)
    out.name, out._h, out._long = 'stand-in', 1, []
    ff = T.full_frame((1, 0, 0, 0), 25)
    out.send(ff)
    assert sent == [ff] and len(out._long) == 1 and not unprep
    out.send(ff)
    assert len(sent) == 2 and len(unprep) == 1                   # the first buffer, done, let go
    got = []
    inp = object.__new__(M.Input)
    inp._h, inp._sx, inp._closing, inp._part = 1, lambda t, d: got.append(d), False, b''
    bufs = []
    for part in (ff[:4], ff[4:]):
        buf = ctypes.create_string_buffer(part, 1024)
        hd = M._MIDIHDR()
        hd.lpData, hd.dwBufferLength, hd.dwBytesRecorded = ctypes.cast(buf, ctypes.c_void_p), 1024, len(part)
        bufs.append((buf, hd))
        inp._long_in(ctypes.addressof(hd))
    assert got == [ff] and len(added) == 2                       # one message from two buffers, both queued again


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


SRC = 'Test Timecode'


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 48)
    monkeypatch.setattr(MP, 'HI', 60)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    clk = Clock()
    FakePort.opened, FakePort.clock = {}, clk
    FakeInput.opened = {}
    e.midi = MidiPlay(e, sources=lambda: [SRC, 'test keys'], opener=FakeInput)
    e.mout = MidiOut(e, outputs=lambda: [SRC, 'Test Out', 'Video'], opener=FakePort, now=clk, thread=False)
    e.clk = clk
    e.feed = []                                    # (arrival time, status, d1, d2) still to deliver
    return e


def run(e, until):
    """The scheduler, the mixer and the device in turn on the fake clock up to `until`, each message given to the
    input at its own time (a 'sysex' status is a full frame). Returns the audio handed to the device."""
    out = []
    while e.clk.t < until:
        end = e.clk.t + BLOCK / SR
        while e.feed and e.feed[0][0] < end:
            t, st, d1, d2 = e.feed.pop(0)
            inp = FakeInput.opened.get(SRC)
            if inp is not None:
                if st == 'sysex':
                    inp.on.sysex(t, d1)
                else:
                    inp.on(t, st, d1, d2)
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


def speaker_s(e, sample):
    return 100.0 + (sample + e.safety.la + G.LAT_BUDGET) / SR


def kick(e):
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])


def frames_of(x, rate):
    return x if isinstance(x, int) else T.tc_to_frames(T.parse_tc(x, rate), rate)


class Their:
    """Their timecode: quarter frame q arrives at t0 + (q - q0) * Tq / speed (their own clock), backwards too."""

    def __init__(self, e, rate, start, t0, t1, speed=1.0, jitter=0.0, back=False, seed=1):
        self.rate, self.t0, self.speed, self.back = rate, t0, speed, back
        self.q0 = 4 * frames_of(start, rate)
        self.tq = float(T.frame_s(rate)) / 4
        r = random.Random(seed)
        q, t = self.q0, t0
        while t < t1:
            e.feed.append((t + (r.gauss(0, jitter) if jitter else 0.0), 0xF1, T.quarter_frame(q, rate)[1], 0))
            q += -1 if back else 1
            t += self.tq / speed
        self.q1 = q
        e.feed.sort(key=lambda m: m[0])

    def arrives(self, sec):
        """When their time reads `sec` (seconds since 00:00:00:00)."""
        return self.t0 + (sec / self.tq - self.q0) * self.tq / self.speed


def phase_ms(e, th, b_lo, b_hi, offset='00:00:00:00'):
    """ismail's beats from b_lo to b_hi against when their grid's beat for each arrives: speaker minus theirs, ms."""
    tin = e.tcin
    out = []
    for b in range(int(np.ceil(b_lo)), int(b_hi)):
        x = b - tin.D
        out.append((speaker(e, b) - th.arrives(tin._secs_of(x))) * 1000)
    return np.array(out)


def test_timecode_out_sends_each_quarter_frame_on_its_sample(eng):
    run(eng, 100.5)
    r = eng.cmd_midi_timecode(port='video', offset='01:00:00:00')
    assert "timecode out -> 'Video': 25 fps, the set's start = 01:00:00:00" in r and 'full frame' in r
    run(eng, 104.0)
    port = FakePort.opened['Video']
    full = [(t, b) for t, b in port.sent if b[0] == 0xF0]
    qf = [(t, b) for t, b in port.sent if b[0] == 0xF1]
    assert len(full) == 1 and port.sent[0] == full[0] and full[0][0] == qf[0][0]   # the locate, then piece 0
    (tc, rate), = [T.read_full_frame(full[0][1])]
    zero = T.tc_to_frames((1, 0, 0, 0), 25)
    F0 = T.tc_to_frames(tc, 25)
    assert rate == 25 and F0 % 2 == 0 and qf[0][1][1] >> 4 == 0
    dec = T.QuarterFrames()
    tq = 1 / 100
    for i, (t, b) in enumerate(qf):
        q = 4 * F0 + i
        assert b == T.quarter_frame(q, 25)
        s = int(round((q * tq - zero / 25) * SR))                            # the sample its time is
        assert t == pytest.approx(speaker_s(eng, s), abs=1e-9)
        rd = dec.feed(b[1])
        assert rd is None or rd.q == q
    assert np.allclose(np.diff([t for t, _ in qf]), tq, atol=1.5 / SR)       # on the sample grid
    assert 300 <= len(qf) <= 360                                              # 100 a second
    s = eng.cmd_status()
    assert "timecode out -> 'Video': 25 fps, 01:00:0" in s and 'quarter frames, 1 full frame' in s
    assert 'timecode out off' in eng.cmd_midi_timecode(on=False)
    n = len(port.sent)
    run(eng, 105.0)
    assert len(port.sent) == n                                                # they stop: the receiver's stop


def test_timecode_out_keeps_time_through_a_tempo_change_and_a_drop_frame_minute(eng):
    run(eng, 100.2)
    eng.cmd_midi_timecode(port='Video', rate='29.97df', offset='00:00:58;00')
    eng.cmd_tempo(150, at='bar:2')                                            # beats move, the time does not
    run(eng, 104.5)
    qf = [(t, b) for t, b in FakePort.opened['Video'].sent if b[0] == 0xF1]
    assert np.allclose(np.diff([t for t, _ in qf]), 1001 / 120000, atol=1.5 / SR)
    dec = T.QuarterFrames()
    labels = [T.fmt_tc(r.tc, 29.97) for r in (dec.feed(b[1]) for _, b in qf) if r is not None and r.complete]
    i = labels.index('00:00:59;28')
    assert labels[i + 1] == '00:01:00;02'                                    # the minute starts at ;02
    assert '29.97 fps drop-frame' in eng.cmd_status()


def test_a_new_offset_is_a_locate(eng):
    run(eng, 100.2)
    eng.cmd_midi_timecode(port='Video')
    run(eng, 101.5)
    r = eng.cmd_midi_timecode(offset='10:00:00:00')
    assert 'a full frame of 10:00:0' in r
    run(eng, 103.0)
    port = FakePort.opened['Video']
    full = [T.read_full_frame(b) for _, b in port.sent if b[0] == 0xF0]
    assert len(full) == 2 and full[0][0][0] == 0 and full[1][0][0] == 10
    i = max(j for j, (_, b) in enumerate(port.sent) if b[0] == 0xF0)
    assert port.sent[i + 1][1] == T.quarter_frame(4 * T.tc_to_frames(full[1][0], 25), 25)


def test_the_chase_lands_its_bar_line_on_their_grid_and_keeps_its_beats_there(eng):
    kick(eng)
    eng.cmd_midi_out(track='k', port='Test Out')
    r = eng.cmd_midi_timecode_in(source='timecode', offset='01:00:00:00')
    assert "timecode in <- 'Test Timecode'" in r and 'MTC carries no tempo' in r and 'their bar 1 at 01:00:00:00' in r
    assert 'waiting for timecode' in eng.cmd_status()
    y0 = run(eng, 103.0)
    assert np.max(np.abs(y0[-SR:])) > 0.05                                   # nothing heard yet: the set plays
    th = Their(eng, 25, '01:00:00:00', 103.0, 120.0, jitter=0.001)
    t1 = eng.clk.t
    y = run(eng, 120.0)
    news = '\n'.join(eng.news)
    assert 'their time runs from 01:00:00:00 (25 fps)' in news and 'timecode in: locked, their 01:00:0' in news
    xL, bL = eng.tcin.locked
    assert xL % 4 == 0 and bL % 4 == 0                                       # their bar line, ismail's bar line
    t_in = speaker(eng, bL)
    assert abs(t_in - th.arrives(3600 + xL * 0.5)) < 0.004                   # their 01:00:00:00 + xL beats at 120
    first = t1 + 0.2 + np.argmax(np.abs(y[int(0.2 * SR):, 0]) > 1e-4) / SR
    assert first == pytest.approx(t_in, abs=0.006)                            # silent until the lock bar
    off = (t_in - th.arrives(3600 + xL * 0.5)) * 1000
    print(f"their time from {t1:.1f} s: heard again at {first:.2f} s, the lock bar {off:+.2f} ms off their grid")
    ons = [t for t, b in FakePort.opened['Test Out'].sent if b[0] == 0x99]
    assert not [t for t in ons if 103.2 < t < t_in - 0.001]                  # MIDI out held back too
    err = phase_ms(eng, th, bL + 8, eng.beat(eng.played) - 4)
    print(f"chase, 1 ms jitter: phase max {np.abs(err).max():.2f} ms, {eng.tcin.changes} tempo changes")
    assert np.abs(err).max() < 6.0 and eng.bpm == pytest.approx(120, abs=0.25)
    s = eng.cmd_status()
    assert "timecode in <- 'Test Timecode': their 01:00:1" in s and '(25 fps)' in s and \
        f"running, their {eng.tcin._label(eng.tcin._secs_of(xL))} = ismail bar {bL // 4 + 1}" in s
    assert re.search(r"their speed (1\.000|0\.999)\d*x", s) and 'phase ' in s


def test_their_speed_is_followed(eng):
    """A tape running 1% fast: the set runs 1% fast and its beats stay on their grid."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    th = Their(eng, 30, '00:10:00:00', 100.3, 125.0, speed=1.01)
    run(eng, 125.0)
    assert eng.bpm == pytest.approx(121.2, abs=0.25)
    xL, bL = eng.tcin.locked
    err = phase_ms(eng, th, bL + 16, eng.beat(eng.played) - 4)
    print(f"their speed 1.01: set {eng.bpm:.2f} BPM, phase max {np.abs(err).max():.2f} ms after the lock bar")
    assert np.abs(err).max() < 6.0


def test_their_stop_silences_the_set_and_running_again_relocks(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC, dropout_ms=200)
    Their(eng, 25, '00:00:10:00', 100.3, 108.0)
    run(eng, 108.1)
    assert eng.tcin.state == 'locked'
    y = run(eng, 109.5)                                                       # they stopped at 108
    assert 'no timecode from' in '\n'.join(eng.news) and 'STOPPED' in eng.cmd_status()
    assert np.max(np.abs(y[int(0.3 * SR):])) == 0                            # silent from 200 ms after their stop
    eng.feed.append((109.6, 'sysex', T.full_frame((0, 0, 20, 0), 25), 0))     # they locate, then play
    run(eng, 109.8)
    assert 'they located to 00:00:20:00' in '\n'.join(eng.news) and 'LOCATED to 00:00:20:00' in eng.cmd_status()
    th = Their(eng, 25, '00:00:20:00', 110.0, 120.0)
    run(eng, 120.0)
    xL, bL = eng.tcin.locked
    assert eng.tcin.state == 'locked' and xL * 0.5 >= 20.0
    assert abs(speaker(eng, bL) - th.arrives(xL * 0.5)) < 0.004


def test_a_jump_while_running_relocks(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    Their(eng, 25, '00:00:00:00', 100.3, 106.0)
    run(eng, 105.0)
    first = eng.tcin.locked
    eng.feed = [m for m in eng.feed if m[0] < 105.5]
    th = Their(eng, 25, '00:05:00:00', 105.5, 116.0)                          # their locate while running
    run(eng, 116.0)
    assert 'they jumped from 00:00:05' in '\n'.join(eng.news) and 'to 00:05:00:00' in '\n'.join(eng.news)
    xL, bL = eng.tcin.locked
    assert eng.tcin.locked != first and xL * 0.5 >= 300
    assert abs(speaker(eng, bL) - th.arrives(xL * 0.5)) < 0.004


def test_running_backwards_silences_the_set(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    Their(eng, 24, '00:01:00:00', 100.3, 106.0)
    run(eng, 106.0)
    Their(eng, 24, '00:01:05:12', 106.0, 108.0, back=True)
    y = run(eng, 108.0)
    assert 'their time runs backwards' in '\n'.join(eng.news) and 'RUNNING BACKWARDS' in eng.cmd_status()
    assert np.max(np.abs(y[int(0.2 * SR):])) == 0


def test_a_rate_pinned_is_held_and_a_rate_change_relocks(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC, rate=25)
    Their(eng, 30, '00:00:00:00', 100.3, 103.0)
    y = run(eng, 103.0)
    assert "sends 30 fps and rate=25 was asked: not following" in '\n'.join(eng.news)
    assert 'WRONG RATE' in eng.cmd_status() and eng.tcin.locked is None
    assert np.max(np.abs(y[-SR // 2:])) > 0.05                               # not followed: the set plays on
    eng.cmd_midi_timecode_in(rate='any')
    eng.feed = []
    Their(eng, 30, '00:00:03:00', 103.0, 110.0)
    run(eng, 110.0)
    assert eng.tcin.state == 'locked'
    eng.feed = []
    th = Their(eng, 25, '00:00:10:00', 110.0, 118.0)
    run(eng, 118.0)
    assert 'their rate changed from 30 fps to 25 fps' in '\n'.join(eng.news)
    xL, bL = eng.tcin.locked
    assert eng.tcin.rate == 25 and abs(speaker(eng, bL) - th.arrives(xL * 0.5)) < 0.004


def test_clock_in_leads_when_both_are_on(eng):
    """One DAW sending MIDI clock and timecode on one port: the clock leads, timecode only reads."""
    kick(eng)
    t = 100.3
    while t < 110:
        eng.feed.append((t, 0xF8, 0, 0))
        t += 60 / (24 * 126)
    Their(eng, 25, '00:00:00:00', 100.3, 110.0)
    eng.cmd_midi_clock_in(source=SRC, transport='tempo')
    r = eng.cmd_midi_timecode_in(source=SRC)
    assert 'MIDI clock in' in r and 'leads (tempo, beat and transport)' in r
    y = run(eng, 110.0)
    assert eng.tcin.changes == 0 and eng.bpm == pytest.approx(126, abs=0.25)
    assert 'reading only: MIDI clock in' in eng.cmd_status() and np.max(np.abs(y[-SR:])) > 0.05


def test_live_tempo_still_works_and_their_grid_follows_it(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    th = Their(eng, 25, '00:00:00:00', 100.3, 125.0)
    run(eng, 108.0)
    assert eng.tcin.state == 'locked'
    r = eng.cmd_tempo(100)
    assert 'timecode in: their bar lines run at 100 BPM from bar' in r
    run(eng, 125.0)
    assert eng.bpm == pytest.approx(100, abs=0.25)
    err = phase_ms(eng, th, eng.beat(eng.played) - 20, eng.beat(eng.played) - 4)
    print(f"live_tempo 120 -> 100 while chasing: phase max {np.abs(err).max():.2f} ms, 16 beats from 13 s on")
    assert np.abs(err).max() < 6.0


def test_one_port_both_ways_settings_errors_and_off(eng):
    eng.cmd_midi_timecode(port=SRC)
    with pytest.raises(LiveError, match=r"timecode out already goes to 'Test .*live_midi_timecode\(on=False\)"):
        eng.cmd_midi_timecode_in(source=SRC)
    eng.cmd_midi_timecode(on=False)
    eng.cmd_midi_timecode_in(source=SRC)
    with pytest.raises(LiveError, match=r"timecode in already chases .*live_midi_timecode_in\(on=False\)"):
        eng.cmd_midi_timecode(port=SRC)
    with pytest.raises(LiveError, match=r"dropout_ms=10: 100 to 5000"):
        eng.cmd_midi_timecode_in(dropout_ms=10)
    with pytest.raises(LiveError, match=r"rate=48: 24, 25, 29.97"):
        eng.cmd_midi_timecode_in(rate=48)
    with pytest.raises(LiveError, match=r"timecode '1:00': write it 'hh:mm:ss:ff'"):
        eng.cmd_midi_timecode_in(offset='1:00')
    with pytest.raises(LiveError, match=r"sends no timecode"):
        eng.cmd_midi_timecode_in(source='screen keys')
    assert 'timecode in off' in eng.cmd_midi_timecode_in(on=False) and SRC not in FakeInput.opened
    with pytest.raises(LiveError, match=r"timecode out is off: name the port"):
        eng.cmd_midi_timecode(rate=30)
    assert eng.cmd_midi_timecode_in(on=False) == 'timecode in is off'


def test_the_ops_are_listed_and_found_by_plain_words():
    for name in ('live_midi_timecode', 'live_midi_timecode_in'):
        assert name in api.OPS
    assert api.OPS['ops_find'](need='sync the set to video timecode', n=3).splitlines()[1].split()[0] == \
        'live_midi_timecode_in'
