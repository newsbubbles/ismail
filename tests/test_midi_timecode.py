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
    """The header winmm reads (MIDIHDR, packed as mmsystem.h packs it: 112 bytes on 64-bit Windows, 64 on 32-bit;
    unpacked it would be 120 and the driver would read dwOffset and the reserved words 8 bytes off), a SysEx handed to
    midiOutLongMsg and its buffer let go once the driver marks it done, and SysEx in put back together across two
    buffers, each queued again. A stand-in driver: no MIDI device opens."""
    assert ctypes.sizeof(M._MIDIHDR) == (112 if ctypes.sizeof(ctypes.c_void_p) == 8 else 64)
    assert M._MIDIHDR.dwOffset.offset == (44 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
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
    inp._lk = __import__('threading').Lock()
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


# ---------------------------------------------------------------------------------------------------- review fixes
STILL = 65                                         # MIDIERR_STILLPLAYING


class StandInIn:
    """A stand-in winmm for midi.Input: it holds the queued SysEx buffers, hands them back through the callback on
    midiInReset (as winmm does), says STILLPLAYING for a buffer it still holds. No device opens."""

    def __init__(self, stick=(), miss_first_reset=0, prepare_fails_at=None):
        self.queue, self.cb, self.closed, self.log = [], None, False, []
        self.stick = set(stick)                    # indexes of buffers it never gives back
        self.miss = miss_first_reset               # buffers the first reset keeps
        self.prepare_fails_at, self.n_prep, self.addrs = prepare_fails_at, 0, []
        self.hold = None                           # (entered, release): the callback thread's AddBuffer waits

    def midiInOpen(self, ph, idx, cb, inst, flags):
        self.cb = cb
        ph._obj.value = 1
        return 0

    def midiInPrepareHeader(self, h, ph, n):
        self.n_prep += 1
        self.log.append('prepare')
        return 7 if self.n_prep == self.prepare_fails_at else 0

    def midiInAddBuffer(self, h, ph, n):
        import threading
        a = ctypes.cast(ph, ctypes.c_void_p).value
        if self.hold is not None and threading.current_thread() is not threading.main_thread():
            entered, release = self.hold
            entered.set()
            release.wait(5)
        if self.closed:
            self.log.append('add after close')
            return 5
        if a not in self.addrs:
            self.addrs.append(a)
        self.queue.append(a)
        self.log.append('add')
        return 0

    def midiInStart(self, h):
        return 0

    def midiInStop(self, h):
        self.log.append('stop')
        return 0

    def midiInReset(self, h):
        keep = [a for a in self.queue if self.addrs.index(a) in self.stick][:]
        back = [a for a in self.queue if a not in keep]
        if self.miss:
            keep, back, self.miss = keep + back[:self.miss], back[self.miss:], 0
        self.queue = keep
        for a in back:
            M._MIDIHDR.from_address(a).dwBytesRecorded = 0
            self.cb(1, M._MIM_LONGDATA, 0, a, 0)
        self.log.append(f'reset returned {len(back)}')
        return 0

    def midiInUnprepareHeader(self, h, ph, n):
        a = ctypes.cast(ph, ctypes.c_void_p).value
        r = STILL if a in self.queue else 0
        self.log.append(f'unprepare {r}')
        return r

    def midiInClose(self, h):
        r = STILL if self.queue else 0
        self.closed = not r
        self.log.append(f'close {r}')
        return r


def stand_in_input(monkeypatch, w, sysex=True):
    monkeypatch.setattr(M, '_w', w)
    monkeypatch.setattr(M, 'sources', lambda: ['X'])
    got, short = [], []

    def on(t, a, b, c):
        short.append((a, b, c))
    if sysex:
        on.sysex = lambda t, d: got.append(bytes(d))
    return M.Input('X', on), got, short


def deliver(w, a, data, kind=None):
    hd = M._MIDIHDR.from_address(a)
    ctypes.memmove(hd.lpData, data, len(data))
    hd.dwBytesRecorded = len(data)
    w.queue.remove(a)
    w.cb(1, kind or M._MIM_LONGDATA, 0, a, 0)


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_a_broken_sysex_buffer_is_queued_again_and_a_new_message_starts_clean(monkeypatch):
    """MIM_LONGERROR (a cut or broken SysEx) hands the buffer back like MIM_LONGDATA: queued again, and its bytes
    and what came before them dropped. Before, four of them and SysEx in was dead until the input reopened. A
    buffer starting F0 drops a leftover that never got its F7."""
    w = StandInIn()
    inp, got, _ = stand_in_input(monkeypatch, w)
    assert len(w.queue) == M.SYSEX_BUFS
    for _ in range(2 * M.SYSEX_BUFS):
        deliver(w, w.queue[0], b'\xf0\x7f\x7f\x01', M._MIM_LONGERROR)
    assert len(w.queue) == M.SYSEX_BUFS and inp._part == b'' and got == []
    ff = T.full_frame((1, 0, 0, 0), 25)
    deliver(w, w.queue[0], b'\xf0\x7f\x7f\x01\x01')               # a start that never ends
    deliver(w, w.queue[0], ff[:5])                                  # then a whole message over two buffers
    deliver(w, w.queue[0], ff[5:])
    assert got == [ff] and len(w.queue) == M.SYSEX_BUFS
    inp.close()
    assert w.closed and not w.queue and inp._h is None


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_close_waits_for_a_callback_queueing_a_buffer_and_gets_every_buffer_back(monkeypatch):
    """The driver's thread is inside the callback, past the closing check, about to queue its buffer again, as
    close() begins. Before: that buffer landed after midiInReset, unprepare and close said STILLPLAYING unread,
    and the handle stayed open over freed buffers. Now the check and the queueing hold the lock close() takes to
    say closing, so the reset comes after it and hands every buffer back."""
    import threading
    w = StandInIn()
    inp, got, _ = stand_in_input(monkeypatch, w)
    ff = T.full_frame((1, 0, 0, 0), 25)
    a = w.queue.pop(0)
    hd = M._MIDIHDR.from_address(a)
    ctypes.memmove(hd.lpData, ff, len(ff))
    hd.dwBytesRecorded = len(ff)
    entered, release = threading.Event(), threading.Event()
    w.hold = (entered, release)
    cb = threading.Thread(target=lambda: w.cb(1, M._MIM_LONGDATA, 0, a, 0))
    cb.start()
    assert entered.wait(5)
    errs = []

    def close():
        try:
            inp.close()
        except Exception as ex:
            errs.append(ex)
    cl = threading.Thread(target=close)
    cl.start()
    cl.join(0.2)
    assert cl.is_alive() and 'stop' not in w.log                   # close waits for the queueing to finish
    w.hold = None
    release.set()
    cb.join(5)
    cl.join(5)
    assert not errs and not cl.is_alive()
    assert w.log[-7:] == ['stop', 'reset returned 4'] + ['unprepare 0'] * 4 + ['close 0']
    assert w.closed and not w.queue and inp._h is None and inp._bufs == [] and got == [ff]


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_close_retries_after_a_reset_and_keeps_buffers_the_driver_will_not_give_back(monkeypatch):
    w = StandInIn(miss_first_reset=1)                              # the first reset keeps one: a second gets it
    inp, _, _ = stand_in_input(monkeypatch, w)
    inp.close()
    assert w.log.count('stop') == 1 and [x for x in w.log if x.startswith('reset')] == \
        ['reset returned 3', 'reset returned 1']
    assert w.closed and inp._h is None and inp._bufs == []
    w = StandInIn(stick={2})                                       # one it never gives back
    inp, _, _ = stand_in_input(monkeypatch, w)
    with pytest.raises(M.MidiError, match=r"did not close \(winmm error 65\): the driver still holds 1 SysEx "
                                          r"buffer\(s\); they are kept"):
        inp.close()
    assert not w.closed and inp._h is not None and len(inp._bufs) == 1  # kept: the driver may still write it
    assert ctypes.cast(ctypes.byref(inp._bufs[0][1]), ctypes.c_void_p).value == w.queue[0]


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_an_input_without_sysex_opens_and_closes_as_before(monkeypatch):
    """Every live input has on.sysex now; one opened without it (another caller) queues no buffer and closes as
    before: stop, reset, close, nothing else asked of the driver."""
    w = StandInIn()
    inp, got, short = stand_in_input(monkeypatch, w, sysex=False)
    assert w.log == [] and inp._bufs == []
    w.cb(1, M._MIM_DATA, 0, 0x7F3C90, 0)
    assert short == [(0x90, 0x3C, 0x7F)]
    inp.close()
    assert w.log == ['stop', 'reset returned 0', 'close 0'] and inp._h is None


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_a_sysex_buffer_the_driver_will_not_take_is_said(monkeypatch):
    w = StandInIn(prepare_fails_at=3)
    with pytest.raises(M.MidiError, match=r"a SysEx buffer was not taken \(winmm error 7\).*it is closed again"):
        stand_in_input(monkeypatch, w)
    assert w.closed and not w.queue


@pytest.mark.skipif(sys.platform != 'win32', reason='winmm is the Windows driver')
def test_winmm_output_close_checks_the_driver_let_go(monkeypatch):
    """Output.close: a STILLPLAYING close gets one more reset and close; a driver that still holds a SysEx buffer
    keeps it (and the handle) and says so, instead of a close that reads as done over memory it may still read."""
    log = []

    class W:
        """Marks the queued SysEx done on reset number `done_at` (None: never)."""

        def __init__(self, done_at):
            self.done_at, self.resets = done_at, 0

        def midiOutPrepareHeader(self, h, ph, n):
            return 0

        def midiOutLongMsg(self, h, ph, n):
            return 0                                                # queued, not done yet

        def midiOutReset(self, h):
            self.resets += 1
            log.append('reset')
            if self.resets == self.done_at:
                for _, hd in out._long:
                    hd.dwFlags |= 1

        def midiOutUnprepareHeader(self, h, ph, n):
            log.append('unprepare')
            return 0

        def midiOutClose(self, h):
            r = STILL if out._long else 0
            log.append(f'close {r}')
            return r

    def opened(done_at):
        monkeypatch.setattr(M, '_w', W(done_at))
        log.clear()
        o = object.__new__(M.Output)
        o.name, o._h, o._long = 'stand-in', 1, []
        return o
    out = opened(1)
    out.send(T.full_frame((0, 0, 0, 0), 25))
    out.close()
    assert log == ['reset', 'unprepare', 'close 0'] and out._h is None and out._long == []
    out = opened(2)                                                 # done only after a second reset
    out.send(T.full_frame((0, 0, 0, 0), 25))
    out.close()
    assert log == ['reset', 'close 65', 'reset', 'unprepare', 'close 0'] and out._h is None
    out = opened(None)                                              # never let go
    out.send(T.full_frame((0, 0, 0, 0), 25))
    with pytest.raises(M.MidiError, match=r"did not close \(winmm error 65\): the driver still holds 1 SysEx"):
        out.close()
    assert out._h == 1 and len(out._long) == 1


@pytest.mark.parametrize('piece, bit', [(1, 0x2), (3, 0x4), (5, 0x8), (7, 0x8)])
def test_the_reserved_bits_of_a_quarter_frame_are_ignored(piece, bit):
    """The MTC spec: senders put 0 in the unused bits, receivers ignore them. Before, a sender with one set had
    every label read as one that cannot exist."""
    dec = T.QuarterFrames()
    labels = []
    for q in range(4 * 1000, 4 * 1000 + 64):
        d = T.quarter_frame(q, 25)[1]
        r = dec.feed(d | bit if d >> 4 == piece else d)
        if r is not None and r.complete:
            labels.append(r.tc)
    assert dec.bad == 0 and labels == [T.frames_to_tc(1000 + 2 * i, 25) for i in range(8)]


def test_offsets_their_rate_has_no_label_for_name_one_it_has():
    assert T.offset_problem('00:01:00:00', 29.97)[1] == '00:01:00;02'
    assert T.offset_problem('00:10:00:00', 29.97) is None                 # each tenth minute keeps 00 and 01
    assert T.offset_problem('00:00:10:24', 24)[1] == '00:00:10:23'
    assert T.offset_problem('00:00:10:24', 25) is None and T.offset_problem(3.5, 24) is None
    # seconds wrap at the rate's own day: a drop-frame day is 86399.9136 s
    assert T.offset_frames(86399.95, 29.97) == 1 and T.offset_frames(86399.8, 29.97) == T.frames_per_day(29.97) - 3


def test_the_frame_period_stays_near_their_rates_own_through_bursts():
    """Quarter frames delivered twelve at a time (a sender's buffer let out at once): with their rate known, the
    filtered period holds within 10% of it and the steady line is not made from the burst. Clock in passes no
    nominal, so its own filter is as before."""
    from ismail.live.clock_in import NOMINAL_BAND, ClockFilter
    tq = 0.01
    f, free = ClockFilter(0.5, nominal=tq), ClockFilter(0.5)
    for i in range(0, 2400, 12):
        for j in range(12):
            t = 100.0 + (i + 11) * tq + j * 2e-5
            f.tick(t, k=1)
            free.tick(t, k=1)
            assert f.T is None or abs(f.T - tq) <= NOMINAL_BAND * tq + 1e-12
    st = f.steady()
    assert st is None or abs(st[0] - tq) <= NOMINAL_BAND * tq
    assert f.steps == 0
    assert free.nominal is None


@pytest.mark.parametrize('stall_ms, first_piece', [(100, 7), (150, 7), (150, 3)])
def test_a_short_delivery_stall_plays_through(eng, stall_ms, first_piece):
    """The sender's thread or USB holds the quarter frames for 100-150 ms, then lets them out at once, in order.
    Their numbering runs on, so it is no jump. Before: read as a jump, the set relocked with about 2.8 s of
    silence."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    tq, q0 = 0.01, 4 * T.tc_to_frames((0, 0, 10, 0), 25)
    feed, q, t = [], q0, 100.3
    while t < 112.0:
        feed.append([t, 0xF1, T.quarter_frame(q, 25)[1], 0])
        q, t = q + 1, t + tq
    i0 = next(i for i, m in enumerate(feed) if m[0] > 107.0 and (q0 + i) % 8 == first_piece)
    t_out = feed[i0][0] + stall_ms / 1000
    for j in range(i0, len(feed)):
        if feed[j][0] < t_out:
            feed[j][0] = t_out + (j - i0) * 1e-5
    eng.feed = [tuple(m) for m in feed]
    run(eng, 107.0)
    assert eng.tcin.state == 'locked'
    y = run(eng, 112.0)
    assert eng.tcin.jumps == 0 and not [n for n in eng.news if 'jumped' in n] and eng.tcin.state == 'locked'
    z = np.abs(y[:3 * SR, 0]) == 0
    run_max = max((len(g) for g in ''.join('1' if v else '0' for v in z).split('0')), default=0)
    assert run_max < 0.25 * SR                                     # the kicks play on through it


@pytest.mark.parametrize('n', [8, 12])
def test_quarter_frames_in_bursts_keep_the_tempo(eng, n):
    """Every n quarter frames delivered together at the last one's time. Before, bursts of 12 drove the set to 70
    or 259 BPM."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    th = Their(eng, 25, '00:00:10:00', 100.3, 125.0)
    fd = []
    for i in range(0, len(eng.feed), n):
        grp = eng.feed[i:i + n]
        fd += [(grp[-1][0] + j * 2e-5,) + m[1:] for j, m in enumerate(grp)]
    eng.feed = fd
    run(eng, 125.0)
    tc = eng.tcin
    assert tc.state == 'locked' and tc.jumps == 0 and eng.bpm == pytest.approx(120, abs=0.25)
    err = phase_ms(eng, th, tc.locked[1] + 8, eng.beat(eng.played) - 4)
    assert abs(np.median(err) - (n - 1) * 5) < 3                   # late by the burst's own lateness, no more


def test_a_small_jump_back_while_running_is_still_a_jump(eng):
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC)
    Their(eng, 25, '00:00:00:00', 100.3, 106.0)
    run(eng, 105.0)
    assert eng.tcin.state == 'locked'
    eng.feed = [m for m in eng.feed if m[0] < 105.5]
    Their(eng, 25, '00:00:04:00', 105.5, 114.0)                    # their 00:00:05:08 back to 00:00:04:00
    run(eng, 114.0)
    assert eng.tcin.jumps == 1 and eng.tcin.state == 'locked'
    assert any('they jumped from 00:00:05' in n and 'to 00:00:04:0' in n for n in eng.news)


def test_an_offset_their_rate_has_no_label_for_is_said_and_not_followed(eng):
    """offset='00:01:00:00' given before their rate is known (checked at 25 fps), then they send 29.97 drop-frame,
    which has no 00:01:00;00. Before: a ValueError on the driver's thread each label, swallowed, nothing said."""
    kick(eng)
    r = eng.cmd_midi_timecode_in(source=SRC, offset='00:01:00:00')
    assert 'checked at 25 fps until their rate is known' in r and 'pin rate= first' in r
    Their(eng, 29.97, '00:01:00;02', 100.3, 116.0)
    run(eng, 106.0)
    tin = eng.tcin
    assert tin.state == 'bad offset' and not tin.running and eng.stats['timecode_in_errors'] == 0
    said = [n for n in eng.news if 'does not exist' in n]
    assert len(said) == 1 and "offset='00:01:00;02'" in said[0] and 'pin rate= first' in said[0]
    line = [x for x in eng.cmd_status().splitlines() if 'timecode in' in x][0]
    assert "BAD OFFSET: offset='00:01:00:00'" in line and "(offset='00:01:00;02' does)" in line
    assert 'timecode in' in eng.cmd_midi_timecode_in()            # describing it does not raise either
    y = run(eng, 107.0)
    assert np.abs(y).max() > 0.05                                  # not followed: the set plays on by itself
    eng.cmd_midi_timecode_in(offset='00:01:00;02')
    run(eng, 116.0)
    assert tin.state == 'locked' and tin.bad_offset is None
    with pytest.raises(ValueError, match=r"frames 0 to 23"):       # pinned: checked at their rate at once
        eng.cmd_midi_timecode_in(rate=24, offset='00:00:10:24')


def test_a_rate_change_that_keeps_an_earlier_offset_names_it(eng):
    eng.cmd_midi_timecode_in(source=SRC, rate=30, offset='00:01:00:00')
    with pytest.raises(ValueError, match=r"rate=29.97 keeps the offset given before, '00:01:00:00', and that does "
                                         r"not exist at 29.97 fps drop-frame .*pass offset=\.\.\. with it, e\.g\. "
                                         r"offset='00:01:00;02'"):
        eng.cmd_midi_timecode_in(rate=29.97)
    assert eng.tcin.pin == 30
    eng.cmd_midi_timecode(port='Video', rate=30, offset='00:01:00:00')
    with pytest.raises(ValueError, match=r"rate=29.97 keeps the offset given before, '00:01:00:00', and that does "
                                         r"not exist at 29.97 fps drop-frame .*pass offset=\.\.\. with it"):
        eng.cmd_midi_timecode(rate=29.97)
    assert eng.mout.tc['rate'] == 30


@pytest.mark.parametrize('their', ['stopped', 'located'])
def test_clock_in_turned_off_while_their_time_stands_still_keeps_the_set_silent(eng, their):
    """Clock in leads while on (timecode only reads); turned off while their time is stopped or located, the set
    goes back to silent. Before, it played on by itself until their time ran."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC, dropout_ms=200)
    Their(eng, 25, '00:00:10:00', 100.3, 106.0)
    if their == 'located':
        eng.feed.append((106.5, 'sysex', T.full_frame((0, 0, 30, 0), 25), 0))
    run(eng, 107.0)
    assert eng.tcin.state == their
    eng.cmd_midi_clock_in(source='test keys', transport='tempo')
    y = run(eng, 108.0)
    assert eng.tcin.passive and np.abs(y).max() > 0.05             # clock in leads: the set plays
    eng.cmd_midi_clock_in(on=False)
    y = run(eng, 110.0)
    assert not eng.tcin.passive and eng.tcin.state == their
    assert np.abs(y[int(0.3 * SR):]).max() == 0.0
    assert any(f'their time is {their}, so the set is silent until it runs' in n for n in eng.news)


def test_their_grid_moved_by_live_tempo_holds_through_their_stop_and_play(eng):
    """live_tempo moves their bar grid from the change on; their stop and play from the same place keeps it.
    Before, the relock went back to the grid from offset at the new tempo: bar lines 21.6, 24.0, ... not 20.0,
    22.4, ... A new offset starts it again from the offset."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC, dropout_ms=200)
    Their(eng, 25, '00:00:00:00', 100.3, 116.0)
    run(eng, 107.0)
    eng.cmd_tempo(100)
    run(eng, 116.0)
    tc = eng.tcin

    def bars():
        return [round(tc._secs_of(x), 3) for x in range(0, 80, 4) if 20 <= tc._secs_of(x) <= 30]
    before = bars()
    assert before == [20.0, 22.4, 24.8, 27.2, 29.6]
    run(eng, 117.0)
    assert tc.state == 'stopped'
    eng.feed = []
    Their(eng, 25, '00:00:20:00', 117.5, 130.0)
    run(eng, 130.0)
    assert tc.state == 'locked' and bars() == before
    eng.cmd_midi_timecode_in(offset='00:00:00:00')
    assert tc.anchor is None


def test_timecode_out_after_the_set_ends_says_nothing_would_go_and_a_move_says_the_old_port_stopped(eng):
    run(eng, 100.2)
    eng.cmd_midi_timecode(port='Video')
    run(eng, 101.0)
    r = eng.cmd_midi_timecode(port='Test Out')
    assert r.startswith("timecode out to 'Video' stopped (") and "timecode out -> 'Test Out'" in r
    eng.mout.end(0.5)
    with pytest.raises(ValueError, match=r"the set has ended \(its end is planned\): timecode out would send "
                                         r"nothing"):
        eng.cmd_midi_timecode(offset='01:00:00:00')
    run(eng, 102.0)
    assert eng.mout.ended
    with pytest.raises(ValueError, match=r"the set has ended: timecode out would send nothing"):
        eng.cmd_midi_timecode(port='Video')
    assert 'timecode out' in eng.cmd_midi_timecode(on=False)


def test_their_rate_changing_to_one_without_the_offset_label_stops_following(eng):
    """Locked at 25 fps on offset 00:01:00:00, then they switch to 29.97 drop-frame, which has no such label: not
    followed, said once, and live_tempo still works (no grid is read from an offset that does not exist)."""
    kick(eng)
    eng.cmd_midi_timecode_in(source=SRC, offset='00:01:00:00')
    Their(eng, 25, '00:01:00:00', 100.3, 106.0)
    run(eng, 105.5)
    assert eng.tcin.state == 'locked'
    eng.feed = [m for m in eng.feed if m[0] < 105.5]
    Their(eng, 29.97, '00:01:05;10', 105.5, 110.0)
    run(eng, 110.0)
    assert eng.tcin.state == 'bad offset' and eng.stats['timecode_in_errors'] == 0
    assert len([n for n in eng.news if 'does not exist at their 29.97' in n]) == 1
    assert 'BPM' in eng.cmd_tempo(110)
