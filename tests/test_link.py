"""Ableton Link (MIDI parity step 8b): a silent engine joins a stand-in Link session on a fake clock and follows it
(alone, with peers at another tempo and phase, through a peer's tempo steps, a jump of their timeline, the
session's stop and start), leads it (live_tempo reaching the session when its bar is heard; ismail as the
session's clock, a peer's tempo set back), changes its quantum, leaves and joins again. The add-on missing says what
to install, and importing ismail never imports it. LinkSession's own thread is run against a stand-in that sends its
callbacks through the loop as aalink does, closing while they come. One test runs two real aalink peers on this
computer; it is skipped when aalink is not installed (the suite needs no aalink). No audio plays."""
import asyncio
import datetime
import importlib.util
import os
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from ismail import api
from ismail.dsp import SR
from ismail.live import graph as G
from ismail.live import link as L
from ismail.live import midi_play as MP
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.link import LinkIn, LinkSession
from ismail.live.midi_out import MidiOut
from ismail.live.midi_play import MidiPlay

import ismail.live.ops  # noqa: F401  (registers the live ops)


# ---------------------------------------------------------------------------------------------------- stand-ins
class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeLink:
    """aalink.Link's surface (beat, phase, tempo, num_peers, playing, quantum, enabled, start_stop_sync_enabled, time,
    request_beat, force_beat, add_/remove_ callbacks) over a session the test plays the peers of. The session's beat
    is a line on `clock`; this app's beat is the session's plus a shift (whole quanta once there are peers, as Link
    keeps the phase)."""
    made = []

    def __init__(self, bpm, clock):
        self.clock = clock
        self._tempo, self._t0, self._s0 = float(bpm), clock(), 0.0
        self.shift = 0.0
        self.quantum = 1.0
        self.enabled = False
        self.start_stop_sync_enabled = False
        self._playing = False
        self._peers = 0
        self.cbs = {'tempo': [], 'num_peers': [], 'start_stop': []}
        self.log = []                   # (time, what, value) this app did
        FakeLink.made.append(self)

    # aalink's surface
    def _s(self, t=None):
        t = self.clock() if t is None else t
        return self._s0 + (t - self._t0) * self._tempo / 60.0

    @property
    def beat(self):
        return self._s() + self.shift

    @property
    def phase(self):
        return self.beat % self.quantum

    @property
    def num_peers(self):
        return self._peers

    @property
    def time(self):
        return datetime.timedelta(seconds=self.clock())

    @property
    def tempo(self):
        return self._tempo

    @tempo.setter
    def tempo(self, v):
        self._commit(v)
        self.log.append((self.clock(), 'tempo', float(v)))

    @property
    def playing(self):
        return self._playing

    @playing.setter
    def playing(self, v):
        self._playing = bool(v)
        self.log.append((self.clock(), 'playing', bool(v)))

    def request_beat(self, b):
        if self._peers == 0:                    # alone: the beat is mapped to now
            self.shift = b - self._s()
        else:                                   # with peers: the phase stays the session's
            self.shift += round((b - self.beat) / self.quantum) * self.quantum
        self.log.append((self.clock(), 'request', b))

    def force_beat(self, b):
        self._s0, self._t0 = b - self.shift, self.clock()   # every peer's beat moves
        self.log.append((self.clock(), 'force', b))

    def __getattr__(self, name):
        for kind in ('tempo', 'num_peers', 'start_stop'):
            if name == f'add_{kind}_callback':
                return self.cbs[kind].append
            if name == f'remove_{kind}_callback':
                return lambda cb, k=kind: self.cbs[k].remove(cb) if cb in self.cbs[k] else None
        raise AttributeError(name)

    def _commit(self, bpm):
        now = self.clock()
        self._s0, self._t0, self._tempo = self._s(now), now, float(bpm)

    def _fire(self, kind, v):
        for cb in list(self.cbs[kind]):
            cb(v)

    # the peers (the test)
    def peer_join(self, n=1, tempo=None, beat=None):
        """n peers in the session; with tempo and beat, the session is theirs: its beat now is `beat`."""
        self._peers = n
        if tempo is not None:
            self._s0, self._t0, self._tempo = float(beat) - self.shift, self.clock(), float(tempo)
            self._fire('tempo', tempo)
        self._fire('num_peers', n)

    def peer_tempo(self, bpm):
        self._commit(bpm)
        self._fire('tempo', bpm)

    def peer_play(self, playing):
        self._playing = bool(playing)
        self._fire('start_stop', playing)

    def peer_force(self, b):
        self._s0, self._t0 = b - self.shift, self.clock()

    def arrives(self, x):
        """When this app's beat x comes, on the current line."""
        return self._t0 + (x - self.shift - self._s0) * 60.0 / self._tempo


class FakeInput:
    def __init__(self, name, on_msg):
        pass

    def close(self):
        pass


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


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 48)
    monkeypatch.setattr(MP, 'HI', 60)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    clk = Clock()
    FakePort.opened, FakePort.clock = {}, clk
    FakeLink.made = []
    e.midi = MidiPlay(e, sources=lambda: ['Test Clock', 'test keys'], opener=FakeInput)
    e.mout = MidiOut(e, outputs=lambda: ['Test Out'], opener=FakePort, now=clk, thread=False)
    e.link = LinkIn(e, factory=lambda bpm: FakeLink(bpm, clk), threaded=False)
    e.clk = clk
    return e


def run(e, until):
    """The scheduler, the mixer and the device in turn on the fake clock up to `until`. Returns the audio handed to
    the device."""
    out = []
    while e.clk.t < until:
        end = e.clk.t + BLOCK / SR
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


def kick(e):
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])


def fake(e):
    return FakeLink.made[-1]


def phase_ms(e, b_lo, b_hi):
    """ismail's beats b_lo..b_hi against when the session's beat for each comes (on its current line), ms."""
    lk, D = fake(e), e.link.D
    return np.array([(speaker(e, b) - lk.arrives(b - D)) * 1000 for b in range(int(np.ceil(b_lo)), int(b_hi))])


def news(e):
    return '\n'.join(e.news)


def join_peers(e, tempo, beat, t_join=100.5, until=None, **kw):
    """The set joins at t_join; a peer at `tempo` is found 0.3 s later (the session's beat then is `beat`)."""
    kick(e)
    run(e, t_join)
    r = e.cmd_link(**kw)
    run(e, t_join + 0.3)
    fake(e).peer_join(1, tempo=tempo, beat=beat)
    if until:
        run(e, until)
    return r


# ---------------------------------------------------------------------------------------------------- following
def test_alone_the_session_takes_the_sets_beat_and_nothing_is_heard_to_change(eng):
    kick(eng)
    run(eng, 100.5)
    r = eng.cmd_link()
    assert 'Link: joined (quantum 4)' in r and 'follows' in r and 'Link: alone (no peers yet)' in r
    assert 'joining: looking for peers' in eng.cmd_status()
    lk = fake(eng)
    assert lk.enabled and lk.quantum == 4 and not lk.start_stop_sync_enabled
    y = run(eng, 106.0)
    assert np.min([np.max(np.abs(y[i:i + SR // 2])) for i in range(2 * SR, len(y) - SR // 2, SR // 2)]) > 0.05
    assert 'Link: no peers yet: the session' in news(eng) and [w for _, w, _ in lk.log] == ['request']
    err = phase_ms(eng, eng.beat(eng.played) - 6, eng.beat(eng.played) - 1)
    assert np.abs(err).max() < 1.0 and eng.link.changes == 0 and eng.link.D == 0
    assert "the session's beat is the set's" in eng.cmd_status()


def test_with_a_peer_the_set_comes_in_on_the_sessions_bar_line_at_its_tempo(eng):
    join_peers(eng, 126, 37.3)
    p0 = eng.played
    y = run(eng, 115.0)
    assert 'Link: 1 peer in the session now (was 0)' in news(eng)
    assert 'joined 1 peer at 126 BPM: the set goes silent' in news(eng)
    assert "Link: locked, the session's bar line = ismail bar" in news(eng)
    xL, bL = eng.link.locked
    assert xL % 4 == pytest.approx(0, abs=1e-9) and bL % 4 == 0
    lk = fake(eng)
    assert abs(speaker(eng, bL) - lk.arrives(xL)) < 0.004                  # their bar line, ismail's bar line
    at = lambda t: int((t - 100.0) * SR) - p0                             # noqa: E731 (y's index at time t)
    t_in = speaker(eng, bL)
    assert np.max(np.abs(y[at(101.55):at(t_in)])) == 0                     # silent from the decision (101.5) on
    assert np.max(np.abs(y[at(t_in):at(t_in + 0.1)])) > 0.05              # and in on the bar line
    err = phase_ms(eng, bL + 8, eng.beat(eng.played) - 2)
    print(f"joined a 126 BPM peer: in at {t_in:.2f} s, phase max {np.abs(err).max():.2f} ms, "
          f"{eng.link.changes} tempo changes")
    assert np.abs(err).max() < 2.0 and eng.bpm == pytest.approx(126, abs=0.01)
    s = eng.cmd_status()
    assert 'Link: 1 peer | session 126 BPM, beat ' in s and ' of 4 | follows | running, the session' in s
    assert 'phase ' in s


@pytest.mark.parametrize('to', [123, 135, 100, 60, 180])
def test_a_peers_tempo_step_is_followed_onto_their_beat(eng, to):
    join_peers(eng, 120, 2.0, until=110.0)
    assert eng.link.state == 'locked'
    relocks, changes = eng.link.relocks, eng.link.changes
    fake(eng).peer_tempo(to)
    t0 = eng.clk.t
    run(eng, t0 + 16.0)
    assert eng.bpm == pytest.approx(to, abs=0.01) and eng.link.relocks == relocks       # no relock for a step
    beats = [b for b in range(int(eng.beat(eng.played)) - 1, 0, -1) if speaker(eng, b) > t0][::-1]
    err = phase_ms(eng, beats[0], beats[-1] + 1)
    off = np.nonzero(np.abs(err) >= eng.link.cfg['phase_tol_ms'])[0]
    settle = (speaker(eng, beats[off[-1] + 1]) if len(off) else speaker(eng, beats[0])) - t0
    print(f"peer 120 -> {to}: on their beat within 4 ms {settle:.1f} s after the step, "
          f"{eng.link.changes - changes} tempo changes")
    assert settle < (11.0 if to == 60 else 4.0) and np.abs(err[-4:]).max() < 1.0      # a halving: 9.6 s


def test_a_jump_of_their_beat_relocks_on_their_next_bar_line(eng):
    join_peers(eng, 120, 0.0, until=110.0)
    xL, bL = eng.link.locked
    lk = fake(eng)
    lk.peer_force(lk.beat + 1.5)                                           # a peer forces the session's beat
    run(eng, 140.0)
    assert "the session's beat jumped" in news(eng)
    xL2, bL2 = eng.link.locked
    assert (xL2, bL2) != (xL, bL) and abs(speaker(eng, bL2) - lk.arrives(xL2)) < 0.004
    assert np.abs(phase_ms(eng, bL2 + 4, eng.beat(eng.played) - 1)).max() < 2.0


def test_peers_coming_and_going_are_said(eng):
    join_peers(eng, 120, 0.0, until=106.0)
    fake(eng).peer_join(3)
    run(eng, 106.5)
    fake(eng).peer_join(0)
    run(eng, 107.0)
    said = news(eng)
    assert 'Link: 0 peers in the session now (was 3; since the news was last read: 0, 1, 3, 0)' in said
    assert said.count('peers in the session now') == 1             # one line, however often they come and go
    assert 'Link: alone (no peers yet)' in eng.cmd_status()


def longest_silence(y):
    """The longest run of all-zero audio in y, in seconds."""
    z = np.max(np.abs(y), axis=1) == 0
    best = run_ = 0
    for v in z:
        run_ = run_ + 1 if v else 0
        best = max(best, run_)
    return best / SR


def test_quantum_sets_the_sessions_bar_lines_and_one_that_keeps_them_changes_nothing_heard(eng):
    join_peers(eng, 120, 1.0, until=112.0, quantum=8)
    xL, bL = eng.link.locked
    assert fake(eng).quantum == 8 and xL % 8 == pytest.approx(0, abs=1e-9) and bL % 4 == 0
    relocks = eng.link.relocks
    r = eng.cmd_link(quantum=2)                     # every one of their old bar lines is still one of theirs
    assert 'quantum 2' in r and 'nothing heard changes' in r
    y = run(eng, 122.0)
    assert fake(eng).quantum == 2 and eng.link.relocks == relocks and eng.link.state == 'locked'
    assert longest_silence(y) < 0.3
    assert 'quantum 2: the set already plays on the session' in news(eng)
    assert np.abs(phase_ms(eng, eng.beat(eng.played) - 8, eng.beat(eng.played) - 1)).max() < 2.0
    with pytest.raises(LiveError, match=r"quantum=0: 1 to 64"):
        eng.cmd_link(quantum=0)


def test_a_quantum_that_moves_the_sessions_bar_lines_off_the_sets_relocks(eng):
    join_peers(eng, 120, 0.0, until=112.0, quantum=2)
    assert eng.link.D % 4 == 2                      # their bar lines are on the set's beat 3
    relocks = eng.link.relocks
    eng.cmd_link(quantum=4)                         # now none of theirs is on one of the set's
    run(eng, 124.0)
    assert eng.link.relocks == relocks + 1 and eng.link.state == 'locked'
    assert 'quantum 4 at 120 BPM: the set goes silent' in news(eng)
    xL, bL = eng.link.locked
    assert fake(eng).quantum == 4 and xL % 4 == pytest.approx(0, abs=1e-9) and bL % 4 == 0
    assert abs(speaker(eng, bL) - fake(eng).arrives(xL)) < 0.004


def test_alone_a_new_quantum_changes_nothing_heard(eng):
    kick(eng)
    run(eng, 100.5)
    eng.cmd_link()
    run(eng, 106.0)
    r = eng.cmd_link(quantum=8)
    assert 'quantum 8' in r
    y = run(eng, 116.0)
    assert longest_silence(y) < 0.3 and eng.link.relocks == 0
    assert 'quantum 8: no peers' in news(eng)


def test_leading_then_following_a_session_already_on_the_sets_beat_changes_nothing_heard(eng):
    join_peers(eng, 90, 13.7, follow=False, lead=True, until=110.0)
    assert eng.link.state == 'leading'
    eng.news.clear()
    r = eng.cmd_link(follow=True)
    assert 'deciding again (follows the session now)' in r
    y = run(eng, 120.0)
    assert longest_silence(y) < 0.3 and eng.link.relocks == 0 and eng.link.state == 'locked'
    said = news(eng)
    assert 'follows the session now: the set already plays on the session' in said and 'joined' not in said
    assert np.abs(phase_ms(eng, eng.beat(eng.played) - 8, eng.beat(eng.played) - 1)).max() < 2.0


def test_following_then_leading_alone_changes_nothing_heard(eng):
    join_peers(eng, 120, 1.0, until=112.0)
    eng.cmd_link(follow=False, lead=True)
    y = run(eng, 120.0)
    assert longest_silence(y) < 0.3 and eng.link.state == 'leading'
    assert 'leads alone now (follow=False): leading 1 peer' in news(eng)


def test_a_second_leader_is_not_fought_forever_and_the_news_says_so_once(eng):
    join_peers(eng, 90, 13.7, follow=False, lead=True, until=106.0)
    lk = fake(eng)
    eng.news.clear()
    sets = 0
    while eng.clk.t < 166.0:                        # a minute against an app that keeps its own tempo and beat
        run(eng, eng.clk.t + 0.5)
        if lk.tempo != 95:
            lk.peer_tempo(95)
            sets += 1
        lk.peer_force(lk.beat + 0.37)
    assert eng.link.pushed <= L.CONTEST_N and eng.link.forces <= L.CONTEST_N + 1
    said = news(eng).splitlines()
    assert len(said) <= 4, said
    contest = [x for x in said if 'another peer leads the session too' in x]
    assert len(contest) == 1 and 'the session runs at 95 BPM' in contest[0] and 'its own 120 BPM' in contest[0]
    assert 'live_link(follow=True) follows it' in contest[0] and 'live_link(lead=True) again' in contest[0]
    assert 'CONTESTED' in eng.cmd_status()
    r = eng.cmd_link(lead=True)                     # take it back: one more try, then contested again
    assert 'deciding again (takes the lead back)' in r
    run(eng, 170.0)
    assert lk.tempo == pytest.approx(120)          # the set's own tempo again
    lk.peer_tempo(95)
    eng.cmd_link(follow=True)                       # or follow it
    run(eng, 185.0)
    assert eng.link.state in ('locked', 'locking') and abs(eng.bpm - 95) < 0.5


def test_repeated_link_news_never_floods_the_news(eng):
    join_peers(eng, 120, 0.0, until=106.0)
    eng.news.clear()
    lk = fake(eng)
    for i in range(40):                             # peers come and go, and the beat jumps, again and again
        lk.peer_join(1 + i % 3)
        lk.peer_force(lk.beat + 0.5)
        run(eng, eng.clk.t + 0.2)
    eng.news.append('track drums: something else worth saying')
    said = news(eng).splitlines()
    assert 'track drums: something else worth saying' in said and len(said) <= 6, said
    assert sum('peers in the session now' in x or 'peer in the session now' in x for x in said) == 1


# ---------------------------------------------------------------------------------------------------- leading
def test_following_only_refuses_live_tempo_and_leading_sends_it_when_its_bar_is_heard(eng):
    join_peers(eng, 120, 0.0, until=110.0)
    with pytest.raises(LiveError, match=r"follows the Ableton Link session.*live_link\(lead=True\)"):
        eng.cmd_tempo(100)
    locked, relocks = eng.link.locked, eng.link.relocks
    r = eng.cmd_link(lead=True)
    assert 'leads too: live_tempo works' in r
    run(eng, 112.0)
    assert eng.link.state == 'locked' and eng.link.locked == locked and eng.link.relocks == relocks   # nothing heard
    r = eng.cmd_tempo(100)
    assert 'Link: the session goes to 100 BPM when bar ' in r and '(1 peer follows it)' in r
    beat = eng.tmap.segs[-1][1]
    run(eng, 125.0)
    lk = fake(eng)
    sent = [(t, v) for t, w, v in lk.log if w == 'tempo']
    assert sent and sent[-1][1] == 100
    print(f"led change: reached the session {(sent[-1][0] - speaker(eng, beat)) * 1000:+.1f} ms from its bar")
    assert abs(sent[-1][0] - speaker(eng, beat)) < 0.012                   # one block on the fake clock
    assert eng.bpm == pytest.approx(100, abs=0.01)
    assert np.abs(phase_ms(eng, eng.beat(eng.played) - 12, eng.beat(eng.played) - 1)).max() < 2.0


def test_as_the_sessions_clock_ismail_sets_its_tempo_and_bar_lines_and_holds_them(eng):
    r = join_peers(eng, 90, 13.7, follow=False, lead=True)
    assert 'leads: the session takes the set' in r
    y = run(eng, 106.0)
    lk = fake(eng)
    assert 'Link: leading 1 peer' in news(eng) and lk.tempo == 120
    assert [w for _, w, _ in lk.log][:2] == ['tempo', 'force']
    t = eng.clk.t
    h = eng.mout.beat_at(t)
    assert abs(lk._s(t) + lk.shift - h) * 500 < 1.0                        # their beat is the set's, within 1 ms
    assert np.max(np.abs(y[-SR:])) > 0.05 and eng.link.changes == 0        # never silent, never retimed
    lk.peer_tempo(95)
    run(eng, 108.0)
    assert lk.tempo == 120 and 'so it is set back to 120 BPM' in news(eng)
    lk.peer_tempo(120)
    lk.peer_force(lk.beat + 0.5)                                           # a peer moves the beat: forced back
    run(eng, 112.0)
    t = eng.clk.t
    assert abs(lk._s(t) + lk.shift - eng.mout.beat_at(t)) * 500 < 1.0
    assert eng.link.forces >= 2 and 'the session keeps the set' in eng.cmd_status()
    r = eng.cmd_tempo(130)
    assert 'the session goes to 130 BPM' in r
    run(eng, 122.0)
    assert lk.tempo == 130 and eng.bpm == 130 and eng.link.pushed == 1


# ---------------------------------------------------------------------------------------------------- start / stop
def test_with_start_stop_sync_their_stop_silences_the_set_and_their_start_relocks(eng):
    kick(eng)
    run(eng, 100.5)
    eng.cmd_link(start_stop=True)
    lk = fake(eng)
    assert lk.start_stop_sync_enabled
    run(eng, 100.8)
    lk.peer_join(1, tempo=120, beat=5.0)
    lk.peer_play(True)
    run(eng, 110.0)
    assert eng.link.state == 'locked'
    lk.peer_play(False)
    t0 = eng.clk.t
    y = run(eng, 113.0)
    assert 'the session stopped: the set is silent' in news(eng) and 'STOPPED (start/stop sync)' in eng.cmd_status()
    assert np.max(np.abs(y[int(0.03 * SR):])) == 0
    lk.peer_play(True)
    run(eng, 122.0)
    assert 'the session started: the set comes in' in news(eng)
    xL, bL = eng.link.locked
    assert speaker(eng, bL) > t0 + 3 and abs(speaker(eng, bL) - lk.arrives(xL)) < 0.004
    assert 'start/stop sync on, playing' in eng.cmd_status()


def test_follow_only_with_the_session_stopped_waits_silent_and_leading_starts_and_stops_it(eng):
    kick(eng)
    run(eng, 100.5)
    eng.cmd_link(start_stop=True)
    y = run(eng, 104.0)
    assert 'the session is stopped (start/stop sync)' in news(eng) and np.max(np.abs(y[-SR:])) == 0
    eng.cmd_link(on=False)
    eng.cmd_link(start_stop=True, lead=True)
    lk = fake(eng)
    run(eng, 104.5)
    lk.peer_join(1)
    y = run(eng, 110.0)
    assert ('playing', True) in [(w, v) for _, w, v in lk.log] and lk.playing
    assert np.max(np.abs(y[-SR:])) > 0.05
    r = eng.cmd_link(on=False)
    assert 'the session was stopped (start/stop sync)' in r and not lk.playing and not lk.enabled


# ---------------------------------------------------------------------------------------------------- the command
def test_leave_and_join_again(eng):
    join_peers(eng, 126, 3.0, until=112.0)
    lk = fake(eng)
    r = eng.cmd_link(on=False)
    assert 'Link: left the session' in r and 'tempo changes followed' in r and 'live_tempo is its own again' in r
    assert not lk.enabled and not any(lk.cbs.values()) and 'Link' not in eng.cmd_status()
    assert eng.cmd_link(on=False) == 'Link is off (live_link() joins the session)'
    y = run(eng, 114.0)
    assert np.max(np.abs(y[-SR:])) > 0.05 and eng.bpm == pytest.approx(126, abs=0.01)    # the tempo holds
    eng.cmd_tempo(110)                                                     # live_tempo is the set's again
    run(eng, 118.0)
    eng.cmd_link()
    assert len(FakeLink.made) == 2 and FakeLink.made[-1].enabled
    run(eng, 118.3)
    fake(eng).peer_join(1, tempo=126, beat=0.0)
    run(eng, 130.0)
    assert eng.link.state == 'locked' and eng.bpm == pytest.approx(126, abs=0.01)


def test_settings_conflicts_and_errors(eng):
    with pytest.raises(LiveError, match=r"follow=False and lead=False leave Link nothing to do"):
        eng.cmd_link(follow=False)
    with pytest.raises(LiveError, match=r"tempo_tol_bpm=99: 0.01 to 5"):
        eng.cmd_link(tempo_tol_bpm=99)
    eng.cmd_midi_clock_in(source='Test Clock')
    with pytest.raises(LiveError, match=r"MIDI clock in from 'Test Clock' leads the set's tempo now.*"
                                        r"live_midi_clock_in\(on=False\) first"):
        eng.cmd_link()
    eng.cmd_midi_clock_in(on=False)
    eng.cmd_link(phase_tol_ms=2, offset_ms=10)
    assert eng.link.cfg['phase_tol_ms'] == 2 and eng.link.cfg['offset_ms'] == 10
    with pytest.raises(LiveError, match=r"Ableton Link session, which leads its tempo; MIDI clock in.*"
                                        r"live_link\(on=False\) first"):
        eng.cmd_midi_clock_in(source='Test Clock')
    with pytest.raises(LiveError, match=r"timecode in would be a second leader"):
        eng.cmd_midi_timecode_in(source='Test Clock')
    eng.shutdown()                                                          # the set's end leaves the session
    assert not fake(eng).enabled and eng.link.session is None


def test_without_the_add_on_it_says_what_to_install_and_installs_nothing(no_aalink, monkeypatch):
    eng = no_aalink
    monkeypatch.setitem(sys.modules, 'aalink', None)                        # import aalink raises ImportError
    eng.link.factory = None
    with pytest.raises(LiveError) as ex:
        eng.cmd_link()
    msg = str(ex.value)
    assert 'pip install "ismail[link]"' in msg and '-m pip install aalink' in msg and sys.executable in msg
    assert 'GPLv3+' in msg and 'ismail stays MIT' in msg and 'Nothing was installed' in msg
    assert eng.link.session is None and 'Link' not in eng.cmd_status()


@pytest.fixture
def no_aalink(eng, monkeypatch):
    monkeypatch.setattr(L, '_LINK', None)                                   # not imported yet in this process
    eng.link.factory = None
    return eng


def test_without_the_add_on_in_a_uv_environment_it_names_the_uvx_line_and_the_restart(no_aalink, monkeypatch):
    monkeypatch.setitem(sys.modules, 'aalink', None)
    monkeypatch.setattr(L, '_has_pip', lambda: False)                       # uvx and the plugins make no pip
    monkeypatch.setattr(L, '_version', lambda: '9.8.7')
    with pytest.raises(LiveError) as ex:
        no_aalink.cmd_link()
    msg = str(ex.value)
    assert 'uvx --python 3.12 --from "ismail[link]==9.8.7" ismail mcp' in msg
    assert 'restart' in msg and 'pip install' not in msg and 'Nothing was installed' in msg


def test_an_add_on_that_fails_to_import_says_so_and_what_to_do(no_aalink, monkeypatch):
    class Broken:
        def find_spec(self, name, path=None, target=None):
            if name == 'aalink':
                raise OSError("DLL load failed while importing _aalink")
            return None
    monkeypatch.delitem(sys.modules, 'aalink', raising=False)
    monkeypatch.setattr(sys, 'meta_path', [Broken()] + sys.meta_path)
    with pytest.raises(LiveError) as ex:
        no_aalink.cmd_link()
    msg = str(ex.value)
    assert 'importing it failed: OSError: DLL load failed' in msg and 'Nothing was installed' in msg
    assert no_aalink.link.session is None


def test_two_joins_at_once_make_one_link(eng):
    made = FakeLink.made
    slow = eng.link.factory

    def factory(bpm):
        time.sleep(0.05)                                                    # wide open between check and set
        return slow(bpm)
    eng.link.factory = factory
    kick(eng)
    run(eng, 100.5)
    go, out = threading.Barrier(4), []

    def join():
        go.wait()
        try:
            out.append(eng.cmd_link())
        except LiveError as ex:
            out.append(str(ex))
    ts = [threading.Thread(target=join) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert len(out) == 4 and len(made) == 1 and made[0].enabled
    eng.cmd_link(on=False)
    assert not any(m.enabled for m in made) and eng.link.session is None


def test_clock_in_and_link_turned_on_at_once_leave_one_leader(eng):
    for _ in range(5):
        go, out = threading.Barrier(2), []

        def call(f):
            go.wait()
            try:
                out.append(f())
            except LiveError as ex:
                out.append(ex)
        ts = [threading.Thread(target=call, args=(eng.cmd_link,)),
              threading.Thread(target=call, args=(lambda: eng.cmd_midi_clock_in(source='Test Clock'),))]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert len(out) == 2 and sum(isinstance(x, LiveError) for x in out) == 1, out
        assert (eng.link.session is None) != (eng.cin.source is None)
        eng.cmd_link(on=False)
        eng.cmd_midi_clock_in(on=False)
    assert not any(m.enabled for m in FakeLink.made)


def test_importing_ismail_never_imports_aalink():
    code = ("import sys, ismail, ismail.api, ismail.live.engine, ismail.live.ops, ismail.live.link; "
            "print('aalink' in sys.modules)")
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=120,
                         cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == 'False'


def test_the_op_is_listed_and_found_by_plain_words():
    assert 'live_link' in api.OPS
    for need in ('sync the set with Ableton Link', 'play in time with Ableton Live over the network'):
        assert api.OPS['ops_find'](need=need, n=3).splitlines()[1].split()[0] == 'live_link'


# ---------------------------------------------------------------------------------------------------- the thread
class LoopLink(FakeLink):
    """FakeLink as aalink is made: inside a running loop, its callbacks sent through that loop from a thread of its
    own (Link's), and torn down on the loop's thread."""
    hold = None                         # an Event the beat waits on while set (a reading in the middle of a callback)
    instances = []

    def __init__(self, bpm):
        super().__init__(bpm, time.perf_counter)
        self.loop = asyncio.get_running_loop()
        self.torn_on = None
        self.reads_after_off = 0
        self._stop = threading.Event()
        self.pump = threading.Thread(target=self._pump, daemon=True)
        self.pump.start()
        LoopLink.instances.append(self)

    def _pump(self):
        n = 0
        while not self._stop.is_set():
            n += 1
            for kind in ('tempo', 'num_peers', 'start_stop'):
                for cb in list(self.cbs[kind]):
                    try:
                        self.loop.call_soon_threadsafe(cb, n)
                    except RuntimeError:            # loop closed: aalink drops it too
                        return
            time.sleep(0.0005)

    @property
    def beat(self):
        if not self.enabled:
            self.reads_after_off += 1
        h = LoopLink.hold
        if h is not None:
            h.wait(2.0)
        return super().beat

    @property
    def enabled(self):
        return self.__dict__.get('_en', False)

    @enabled.setter
    def enabled(self, v):
        self.__dict__['_en'] = bool(v)
        if not v and '_stop' in self.__dict__:
            self._stop.set()
            self.pump.join(1.0)
            self.torn_on = threading.get_ident()


def test_the_loop_thread_reads_the_session_and_closes_cleanly_under_a_flood_of_callbacks():
    LoopLink.instances, LoopLink.hold = [], None
    s = LinkSession(LoopLink, 120, 4, time.perf_counter, threaded=True)
    lk = LoopLink.instances[-1]
    deadline = time.time() + 2
    while s.snapshot() is None and time.time() < deadline:
        time.sleep(0.01)
    snap = s.snapshot()
    assert snap is not None and snap.enabled and snap.tempo == 120 and snap.quantum == 4
    seq = s.post('tempo', 133)
    s.post('quantum', 8)
    deadline = time.time() + 2
    while (s.snapshot().done < seq or s.snapshot().quantum != 8) and time.time() < deadline:
        time.sleep(0.005)
    assert s.snapshot().tempo == 133 and s.snapshot().quantum == 8
    t0 = time.perf_counter()
    b0 = s.snapshot().beat_at(t0)
    time.sleep(0.25)
    t1 = time.perf_counter()
    assert s.snapshot().beat_at(t1) - b0 == pytest.approx((t1 - t0) * 133 / 60, abs=0.001)   # the line holds
    thread = s.thread
    assert s.close() is None
    assert not thread.is_alive() and lk.torn_on == thread.ident and not any(lk.cbs.values())
    assert lk.reads_after_off == 0 and s.snapshot() is None and s.post('tempo', 90) == 0
    assert s.close() is None                                                # twice is fine


def test_closing_while_a_callback_is_reading_the_session_waits_for_it():
    LoopLink.instances, LoopLink.hold = [], None
    s = LinkSession(LoopLink, 120, 4, time.perf_counter, threaded=True)
    lk = LoopLink.instances[-1]
    time.sleep(0.05)
    LoopLink.hold = threading.Event()                    # the next reading blocks inside a callback
    time.sleep(0.05)
    done = []
    closer = threading.Thread(target=lambda: done.append(s.close()))
    closer.start()
    time.sleep(0.1)
    assert not done                                      # close waits for the loop
    LoopLink.hold.set()
    closer.join(5)
    LoopLink.hold = None
    assert done == [None] and not s.thread.is_alive() and lk.reads_after_off == 0
    assert lk.torn_on == s.thread.ident and not s.problems


def test_a_link_that_takes_too_long_to_start_is_disabled_and_its_thread_ends(monkeypatch):
    monkeypatch.setattr(L, 'START_TIMEOUT_S', 0.2)
    LoopLink.instances, LoopLink.hold = [], None

    def slow(bpm):
        time.sleep(0.3)                                                     # a busy machine, a network that blocks
        return LoopLink(bpm)
    before = {t.ident for t in threading.enumerate()}
    with pytest.raises(RuntimeError, match=r"did not start in 0.2 s.*It was disabled and its thread ended"):
        LinkSession(slow, 120, 4, time.perf_counter, threaded=True)
    lk = LoopLink.instances[-1]
    assert not lk.enabled and not any(lk.cbs.values()) and not lk.pump.is_alive()
    assert not [t for t in threading.enumerate() if t.name == 'ismail-link' and t.ident not in before]


def test_a_link_that_fails_to_start_says_so():
    def broken(bpm):
        raise OSError("no network")
    with pytest.raises(OSError, match="no network"):
        LinkSession(broken, 120, 4, time.perf_counter, threaded=True)


# ---------------------------------------------------------------------------------------------------- real aalink
@pytest.mark.skipif(importlib.util.find_spec('aalink') is None, reason="aalink (the Link add-on) is not installed")
def test_two_real_aalink_peers_on_this_computer_share_tempo_and_phase():
    make = L.load_aalink()
    a = LinkSession(make, 120, 4, time.perf_counter, threaded=True)
    b = LinkSession(make, 97, 4, time.perf_counter, threaded=True)
    try:
        deadline = time.time() + 8
        while time.time() < deadline and not (a.snapshot().peers >= 1 and b.snapshot().peers >= 1):
            time.sleep(0.05)
        found = time.time() - (deadline - 8)
        assert a.snapshot().peers >= 1 and b.snapshot().peers >= 1, "the two Links never found each other"
        a.post('tempo', 133)
        deadline = time.time() + 5
        while time.time() < deadline and abs(b.snapshot().tempo - 133) > 0.01:
            time.sleep(0.02)
        assert b.snapshot().tempo == pytest.approx(133, abs=0.01)
        time.sleep(0.2)
        t = time.perf_counter()
        pa, pb = a.snapshot().beat_at(t) % 4, b.snapshot().beat_at(t) % 4
        d = (pa - pb + 2) % 4 - 2
        print(f"real aalink: peers found in {found:.2f} s; tempo 133 reached the other; phase apart {d * 60 / 133 * 1000:+.2f} ms")
        assert abs(d) * 60 / 133 < 0.003
    finally:
        assert a.close() is None and b.close() is None
    assert not a.thread.is_alive() and not b.thread.is_alive()


@pytest.mark.skipif(importlib.util.find_spec('aalink') is None, reason="aalink (the Link add-on) is not installed")
def test_a_silent_engine_follows_and_leads_a_real_aalink_peer(tmp_path):
    peer = LinkSession(L.load_aalink(), 126, 4, time.perf_counter, threaded=True)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')

    def off_ms():
        t = time.perf_counter()
        d = (peer.snapshot().beat_at(t) - e.mout.beat_at(t) + 2) % 4 - 2   # their bar lines on the set's
        return d * 60 / peer.snapshot().tempo * 1000
    try:
        e.start()
        time.sleep(4.0)                                 # the silent device's clock settles first
        e.cmd_link()
        deadline = time.time() + 20
        while time.time() < deadline and not (e.link.state == 'locked' and abs(e.bpm - 126) < 0.01
                                               and abs(off_ms()) < 5):
            time.sleep(0.25)
        assert e.link.state == 'locked' and abs(e.bpm - 126) < 0.01 and abs(off_ms()) < 5, e.link.line()
        e.cmd_link(lead=True)
        e.cmd_tempo(110)
        deadline = time.time() + 10
        while time.time() < deadline and abs(peer.snapshot().tempo - 110) > 0.01:
            time.sleep(0.1)
        assert peer.snapshot().tempo == pytest.approx(110, abs=0.01)
        th = e.link.session.thread
        assert 'left the session' in e.cmd_link(on=False)
        assert not th.is_alive()
    finally:
        e.shutdown()
        peer.close()
    assert not peer.thread.is_alive()
