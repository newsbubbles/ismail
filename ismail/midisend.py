"""Timed MIDI out (ledger:M113 step 3, Nate 10-23: "full midi like ableton"; plan in
D:/ismail-hq/proposals/midi-parity.md): a sender thread that puts each message on its port at its time, the notes
held on a port (so every note-off ends its own note, and a stop turns off what sounds), and MIDI clock.

Scheduler(now) keeps a heap of (time, action) on the `now` clock (time.perf_counter by default). Its thread sleeps
until about SPIN_S before the next message, then spins (yielding the GIL) to its time: a sleep alone wakes up to a
few ms late on Windows. pump(now) fires what is due, so tests drive it with a fake clock and no thread. Each fired
message records how late it went out; lateness() sums it up.

Notes(out) is one port's held notes: on() and off() carry a token from the plan, so a note-off that belongs to a
note already struck again is skipped (the newer note keeps sounding) and a note struck while still held is turned
off first. release() sends note-offs for what is held and All Notes Off (CC 123) per channel.

Clock: 24 pulses per quarter note (PPQN). pulses(b0, b1) are the pulse numbers in a span of beats; pulse k sits on
beat k / 24, so a clock placed on the beat clock follows any tempo the beats follow.
"""
import collections
import heapq
import itertools
import math
import sys
import threading
import time

PPQN = 24                   # MIDI clock pulses per quarter note
SPIN_S = 0.0015             # the sender spins this close to a message's time instead of sleeping
IDLE_S = 0.004              # the longest the sender sleeps at once, so a message pushed in for sooner is not missed
PRIORITY = 15               # Windows: THREAD_PRIORITY_TIME_CRITICAL for the sender (it sleeps between messages)
PULSE, START, CONTINUE, STOP = 0xF8, 0xFA, 0xFB, 0xFC


def note_on(ch, pitch, vel):
    return bytes([0x90 | (ch & 0x0F), int(pitch) & 0x7F, max(1, min(127, int(round(vel))))])


def note_off(ch, pitch):
    return bytes([0x80 | (ch & 0x0F), int(pitch) & 0x7F, 0])


def control(ch, num, val):
    return bytes([0xB0 | (ch & 0x0F), int(num) & 0x7F, int(val) & 0x7F])


def song_position(beat):
    """Song Position Pointer for a beat: counted in 16ths (MIDI beats) from the song's start, at most 16383."""
    v = max(0, min(16383, int(round(beat * 4))))
    return bytes([0xF2, v & 0x7F, v >> 7])


def pulses(b0, b1):
    """Clock pulse numbers k with k / PPQN in [b0, b1)."""
    return range(math.ceil(b0 * PPQN - 1e-9), math.ceil(b1 * PPQN - 1e-9))


class Scheduler:
    def __init__(self, now=time.perf_counter, spin_s=SPIN_S):
        self.now = now
        self.spin_s = spin_s
        self.heap = []
        self.lock = threading.Lock()
        self._seq = itertools.count()
        self.late = collections.deque(maxlen=20000)      # seconds each message went out after its time
        self.errors = collections.deque(maxlen=5)
        self.running = False
        self.thread = None

    # ------------------------------------------------------------------ the queue
    def at(self, t, act, tag=None):
        """Run act() (it sends one message, or a few) at time t on the `now` clock."""
        with self.lock:
            heapq.heappush(self.heap, (float(t), next(self._seq), tag, act))

    def drop(self, tag=None, after=None, keep=None):
        """Take back queued messages: those with `tag` (None: all), at or after `after` when given; keep(tag) True
        spares one. Returns how many went."""
        with self.lock:
            stay, n = [], 0
            for ev in self.heap:
                hit = (tag is None or ev[2] == tag) and (after is None or ev[0] >= after) and \
                    not (keep is not None and keep(ev[2]))
                if hit:
                    n += 1
                else:
                    stay.append(ev)
            heapq.heapify(stay)
            self.heap = stay
        return n

    def pending(self, tag=None):
        with self.lock:
            return sum(1 for ev in self.heap if tag is None or ev[2] == tag)

    def next_time(self):
        with self.lock:
            return self.heap[0][0] if self.heap else None

    def pump(self, now=None):
        """Fire everything due at `now` (default: the clock), in time order; returns how many fired."""
        now = self.now() if now is None else now
        n = 0
        while True:
            with self.lock:
                if not self.heap or self.heap[0][0] > now:
                    return n
                t, _, _, act = heapq.heappop(self.heap)
            try:
                act()
            except Exception as e:                         # a port gone mid-set: say it, keep the rest going
                self.errors.append(f"{type(e).__name__}: {e}")
            self.late.append(max(0.0, self.now() - t))
            n += 1

    # ------------------------------------------------------------------ the thread
    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name='midi-out')
        self.thread.start()

    def stop(self, join_s=1.0):
        self.running = False
        t = self.thread
        if t is not None and t is not threading.current_thread():
            t.join(join_s)
        self.thread = None

    def _run(self):
        _fine_timer(True)
        try:
            while self.running:
                self.pump()
                nxt = self.next_time()
                dt = (nxt - self.now()) if nxt is not None else IDLE_S
                if dt > self.spin_s:
                    time.sleep(min(dt - self.spin_s, IDLE_S))
                elif dt > 0:
                    while self.now() < nxt:                 # spin to it, letting the mixer have the GIL
                        time.sleep(0)
        finally:
            _fine_timer(False)

    def lateness(self):
        """{'n', 'median_ms', 'p99_ms', 'max_ms'} over the messages sent so far (the last 20000)."""
        x = sorted(self.late)
        if not x:
            return {'n': 0, 'median_ms': 0.0, 'p99_ms': 0.0, 'max_ms': 0.0}
        q = lambda f: x[min(len(x) - 1, int(f * (len(x) - 1) + 0.5))] * 1000     # noqa: E731
        return {'n': len(x), 'median_ms': round(q(0.5), 3), 'p99_ms': round(q(0.99), 3),
                'max_ms': round(x[-1] * 1000, 3)}


def _fine_timer(on):
    """Windows: ask for the 1 ms system timer while the sender runs (Python before 3.11 sleeps in 15.6 ms steps),
    and the sender thread's priority raised (it sleeps between messages, so it takes little; on a loaded machine
    a normal thread woke 10 to 40 ms late). Elsewhere nothing."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes
        if on:
            ctypes.windll.winmm.timeBeginPeriod(1)
            k = ctypes.WinDLL('kernel32')                     # its own handle: argtypes set here stay here
            k.GetCurrentThread.restype = wintypes.HANDLE
            k.SetThreadPriority.argtypes = [wintypes.HANDLE, ctypes.c_int]
            k.SetThreadPriority(k.GetCurrentThread(), PRIORITY)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass


class Notes:
    """The notes held on one port: (channel, pitch) -> (token, owner). Thread-safe."""

    def __init__(self, out):
        self.out = out
        self.held = {}
        self.lock = threading.Lock()
        self.ons = 0

    def on(self, ch, pitch, vel, token, owner=None):
        with self.lock:
            k = (ch, pitch)
            if k in self.held:                  # struck again while it sounds: end the old one first
                self.out.send(note_off(ch, pitch))
            self.held[k] = (token, owner)
            self.out.send(note_on(ch, pitch, vel))
            self.ons += 1

    def off(self, ch, pitch, token):
        with self.lock:
            k = (ch, pitch)
            h = self.held.get(k)
            if h is not None and h[0] == token:
                del self.held[k]
                self.out.send(note_off(ch, pitch))

    def release(self, owner=None, channels=(), all_off=True):
        """Note-offs for the held notes (only `owner`'s when given), then All Notes Off (CC 123) on those notes'
        channels and on `channels`. Returns how many notes were held."""
        with self.lock:
            ks = [k for k, (_, o) in self.held.items() if owner is None or o == owner]
            for ch, p in ks:
                del self.held[(ch, p)]
                self.out.send(note_off(ch, p))
            if all_off:
                for ch in sorted({k[0] for k in ks} | set(channels)):
                    self.out.send(control(ch, 123, 0))
            return len(ks)
