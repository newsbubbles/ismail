"""The live engine: one process that plays a queue of clips forever and takes commands over local HTTP.

Threads: the mixer keeps ~0.3 s of finished audio ahead of the device; the scheduler turns clips into note
events inside a ~4 s horizon and sends each event (a note, or a mono phrase) to render worker processes; the
device callback only copies finished blocks. An event renders once per clip and is reused on every repeat.

Run: python -m ismail.live.engine --project <dir> --bpm 120 [--bpb 4] [--device default|none|<name>] [--workers 2]
Normally started by the live_start op, which also writes <project>/live/engine.json (port, pid).
"""
import argparse
import collections
import contextlib
import copy
import gc
import heapq
import json
import math
import os
import sys
import threading
import time
import traceback
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import fx_blocks as F
from .. import instruments, machine
from ..dsp import SR
from ..notation import NotationError, format_notes, parse_notes, parse_steps, pitch_to_midi, fmt_num, with_offsets
from ..presets import PRESETS
from . import decks as D
from . import graph as G
from . import worker
from .safety import BAD_ABS, Safety
from . import outputs as O
from .timeline import EPS, QueueError, Timeline, fmt_bar

BLOCK = 1024
QUIET = 1e-6            # a path whose input stops and whose output stays below this goes dormant
DORMANT_S = 0.5
SOUND_FLOOR = 1e-4      # -80 dBFS: the mix made a sound (silence on air is measured against it)
SILENT_ON_AIR_S = 10.0  # a set that has played and then sounds nothing this long says so in live_status
RUNWAY_ENDED_BARS = 8   # nothing new scheduled for this many bars: live_status says RUNWAY ENDED
THIN_TRACK_MS = 1e-5    # -50 dBFS mean square: a track counts as sounding above it
THIN_DB = 20.0          # the mix this far under the set's usual level (its fuller moments) is thin
THIN_S = 30             # ... for this many seconds in a row before live_status says THIN
THIN_HISTORY_S = 60     # seconds of sound the set needs behind it before thinness means anything
DORMANT_NOISY_S = 8.0     # a path that hisses (amp, tape) keeps hissing through rests this long, as in the studio
AHEAD_S = 0.5           # finished audio kept ahead of the device
HORIZON_S = 8.0         # how far ahead events are sent to render (slow voices need the head start)
PRELOAD_S = 60.0        # a clip queued for later renders up to this much of its first pass from the moment it is queued
PRELOAD_WITHIN_BARS = 32    # ... once it starts within this many bars: further ahead it waits (ledger:M156: 160 clips
                            # queued 21 minutes ahead made a backlog of 10,089 renders and 82 underruns in 6 bars)
FEED_PER_WORKER = 2     # render jobs handed to each worker at a time; the rest wait in the engine, earliest-needed first
AIR_S = 120.0           # output history kept for live_listen
HOLD_S = 10             # live_status also shows each track's loudest level over this many seconds
WATCH_S = 4.0           # how often the default output device is checked (a speaker connecting)
STALL_S = 2.0           # the device asking for no audio this long = a stalled output (Bluetooth sleep, a busy device)
CHUNK_PRE_S = 1.0       # a performer's bar chunk renders this much of the part before it as context
CHUNK_XF_S = 0.01       # chunks crossfade at the bar line
CHUNK_LOOK_S = 0.05     # and render the notes just after it: a pick's scrape or a pushed note sounds before its beat
CHUNK_REACH_S = 8.0     # a note held across a bar line from longer ago keeps the bars it spans in one chunk
TICK_EVENTS = 128       # events placed per scheduler pass at most, earliest first: a big queue spreads over passes
WARM_TAIL_S = 0.25      # a warm-up pays first-use costs; rendering the full tail through a rig only delays real renders
MARGIN_S = 0.5          # render estimate safety margin (quantized launches make this inaudible)
DEFAULT_RATE = {'mimic': 0.6, 'code': 0.1, 'synth': 0.15, 'sampler': 0.05}   # render s per audio s, until measured
DRUM_RATE = 0.03
DUCK_LOOKBACK = 4 * SR  # onsets this far back still shape a duck's release


class LiveError(ValueError):
    pass


class Seg:
    __slots__ = ('start', 'y', 'track', 'cid', 'on', 'dead')

    def __init__(self, start, y, track, cid, on):
        self.start, self.y, self.track, self.cid, self.on, self.dead = start, y, track, cid, on, False


def _tail_s(inst):
    """Seconds a note rings after its gate, for render estimates."""
    t = inst.get('type')
    if t in ('code', 'mimic'):
        return float(inst.get('tail', 1.0))
    if t in ('synth', 'sampler'):
        return float((inst.get('amp_env') or {}).get('r', 0.3))
    return 0.5


class _Span:
    """A performer's chunk of a clip, in clip beats: it plays [on, end); notes before it render as context."""
    __slots__ = ('on', 'end')

    def __init__(self, on, end):
        self.on, self.end = on, end


def _chunk_spans(notes, length, bpb, reach):
    """The bars of a performer's clip that sound (a note starts in them or is held into them), as chunks: one bar
    each, except that a note held across a bar line from more than `reach` beats before keeps both bars in one."""
    nb = max(1, int(math.ceil(length / bpb - EPS)))
    spans = []
    for j in range(nb):
        a, b = j * bpb, min((j + 1) * bpb, length)
        if not any(a - EPS <= s < b - EPS or (s < a - EPS and s + d > a + EPS) for s, _, d, _ in notes):
            continue
        if spans and abs(spans[-1].end - a) < EPS and \
                any(s < a - reach - EPS and s + d > a + EPS for s, _, d, _ in notes):
            spans[-1].end = b
        else:
            spans.append(_Span(a, b))
    return spans


def _mono_groups(notes, inst):
    if inst.get('_whole'):          # a deck track with instrument automation: its whole section is one event
        return [list(range(len(notes)))] if notes else []
    # a mono synth glides inside a phrase; a performer voice plays overlapping notes as one gesture (legato,
    # slides, a ringing chord): both render a group of overlapping notes as one event
    if (inst.get('type') == 'synth' and inst.get('mono')) or inst.get('performer'):
        groups = []
        for i, (st, _, d, _) in enumerate(notes):
            if groups and st < max(notes[j][0] + notes[j][2] for j in groups[-1]) - EPS:
                groups[-1].append(i)
            else:
                groups.append([i])
        return groups
    return [[i] for i in range(len(notes))]


class Engine:
    def __init__(self, root, bpm, bpb=4, workers=2, device='default', follow=True):
        self.root = os.path.abspath(root)
        self.bpm = float(bpm)
        self.bpb = int(bpb)
        self.spb = 60.0 / self.bpm
        self.device = device
        self.follow = bool(follow)      # device 'default': move to a new system default (a speaker connecting)
        self.out_name = None            # the device the stream opened on, by name
        self.stream = None
        self._null_on = False
        self._out_lock = threading.Lock()
        self._out_tried = 0.0
        self.hub = O.StreamHub(SR)
        self.controls = O.Controls(os.path.join(self.root, 'live', 'controls.jsonl'))
        self.lock = threading.RLock()
        self.tl = Timeline(self.bpb)
        self.tracks = {}
        self.buses = {}
        self.decks = {}
        self.order = []                 # tracks, sidechain sources first
        self.duck_sources = set()
        self.ramps = {}                 # (target, fx index, param) -> Ramp
        self.swaps = []                 # heap of (sample, seq, fn): chain changes due at a bar line
        self.chain_gen = 0              # a chain scheduled to replace another gets the next number; ramps carry it
        self.meta = {}                  # clip id -> {'groups', 'placed'}
        self.cache = {}                 # (clip id, event) -> array | 'pending' | 'error'
        self.renders = {}               # content key -> array | 'pending': identical notes render once
        self.rwait = {}                 # content key -> [(clip id, event)] waiting for that render
        self._rbytes = 0
        self.waiting = collections.defaultdict(list)    # (clip id, event) -> [onset beat]
        self.jobs = {}                  # job id -> (clip id, event, track, submitted time)
        self.pending = []               # heap of (start, seq, Seg)
        self.active = []
        self.seq = 0
        self.pos = 0                    # samples mixed
        self.played = 0                 # samples handed to the device
        self.sounded = None             # the mix position the set last made a sound at (silence on air)
        self.levels = collections.deque(maxlen=600)   # per second: (pos, mix dB, tracks sounding, one of them)
        self._lvl = [0.0, 0]            # the second being summed: energy, samples
        self.runway_out = None          # the mix position the runway ran out (nothing new scheduled) at
        self.safety = Safety(SR)
        self.air = np.zeros((2, int(AIR_S * SR)), dtype=np.float32)
        self.fade = None                # (start gain, samples left, total) when stopping
        self.gain = 1.0
        self.rec = None
        self.rec_path = None
        self.tap = None                 # tap(key, p0, y, lag): track and bus outputs, deck inputs (live.parity)
        self.stats = collections.Counter()
        self.last_late = None
        self.mix_load = None            # mixer time / audio time, smoothed
        self.mix_peak = 0.0
        self.last_underrun = None
        self.last_cmd = time.time()
        self.running = True
        self.stopped = threading.Event()
        self.n_workers = int(workers)
        self.ready = {}                 # worker id -> seconds it took to warm itself
        self._jid = 0
        self._procs = []
        self._fifo = collections.deque()
        self._backlog = []              # heap of (sample the notes are needed at, seq, job id, payload) not yet sent
        self._inflight = 0              # render jobs handed to the shared worker queue and not back yet
        self.last_pull = None           # wall time the device last asked for audio
        self.news = collections.deque(maxlen=20)    # problems found between calls: the next reply of any op says them
        self._lost_since = {}           # track -> beats of notes whose render came back after they had passed
        self._fifo_n = 0
        self._fifo_lock = threading.Lock()
        self._head = 0
        if self.n_workers > 0:
            import multiprocessing as mp
            ctx = mp.get_context('spawn')
            self._results = ctx.Queue()
            self._shared = ctx.Queue()          # render jobs: whichever worker is free takes the next one
            self._tasks = []                    # per-worker queues, for warm-ups every worker must run
            for wi in range(self.n_workers):
                q = ctx.Queue()
                p = ctx.Process(target=worker.main, args=(q, self._shared, self._results, self.root, wi), daemon=True)
                p.start()
                self._tasks.append(q)
                self._procs.append({'p': p, 'out': 0})
        else:
            self._banks = {self.root: worker.SoundBank(self.root)}     # per song, as each worker keeps them

    # ------------------------------------------------------------------ time
    def beat(self, pos):
        return pos / SR / self.spb

    def sample(self, beat):
        return int(round(beat * self.spb * SR))

    # ------------------------------------------------------------------ rendering
    def _submit(self, key, track, notes_s, lead_s, warm=False, expr=None, iauto=None, need=0, extra=None):
        """need: the sample where the first of these notes must sound (renders go out earliest-needed first);
        extra: more keys for the worker ('_chunk', '_beat0')."""
        tr = self.tracks[track]
        inst = self._job_inst(tr, expr, iauto)
        if extra:
            inst = dict(inst, **extra)
        self._jid += 1
        jid = self._jid
        audio_s = max(s + d for s, _, d, _ in notes_s) + _tail_s(inst)
        self.jobs[jid] = {'key': key, 'track': track, 'warm': warm, 'wi': None, 'gen': tr['gen'], 'audio_s': audio_s,
                          'est_s': self._rate(tr) * audio_s}
        if self.n_workers == 0:
            t0 = time.time()
            root = tr['root'] or self.root
            if root not in self._banks:
                self._banks[root] = worker.SoundBank(root)
            instruments.set_resolvers(self._banks[root].sound, self._banks[root].table)
            try:
                y = worker.render_event(inst, notes_s, lead_s, self.bpm, root)
                err = worker.check(y)
            except Exception as e:
                y, err = None, f"{type(e).__name__}: {e}"
            self._result(jid, None if err else y, time.time() - t0, err)
            return
        self.seq += 1
        heapq.heappush(self._backlog, (int(need), self.seq, jid, (jid, inst, notes_s, lead_s, self.bpm,
                                                                   tr['root'] or self.root)))
        self._feed()

    def _feed(self):
        """Hand the workers the earliest-needed renders, a few at a time. A render whose notes no queued clip wants
        any more (cancelled, replaced, ended) is dropped here, so old clips cannot clog the workers."""
        while self._backlog and self._inflight < FEED_PER_WORKER * max(1, self.n_workers):
            _, _, jid, payload = heapq.heappop(self._backlog)
            job = self.jobs.get(jid)
            if job is None:
                continue
            if not job['warm'] and not any(k[0] in self.tl.clips for k in self.rwait.get(job['key'], [])):
                self.jobs.pop(jid, None)
                self.renders.pop(job['key'], None)
                self.rwait.pop(job['key'], None)
                self.stats['skipped'] += 1
                continue
            job['sent'] = True
            self._inflight += 1
            self._shared.put(payload)

    def _warm(self, track):
        """A short throwaway note on every worker (first-use compilation happens off the air), then one 1 s note
        on whichever worker is free to measure what a second of this instrument costs to render."""
        tr = self.tracks[track]
        inst = self._job_inst(tr)
        pitch = int(next(iter(inst['map']))) if inst.get('type') == 'kit' else 60
        short, full = [(0.0, pitch, 0.1, 100)], [(0.0, pitch, 1.0, 100)]
        guess = self._rate(tr)
        if self.n_workers == 0:
            tr['warming'] += 1
            self._submit(('warm', track), track, full, 0.0, warm=True)
            return
        tr['warming'] += self.n_workers + 1
        winst = dict(inst, _tail=WARM_TAIL_S)
        for i in range(self.n_workers):
            self._jid += 1
            self.jobs[self._jid] = {'key': ('warm', track), 'track': track, 'warm': True, 'wi': i, 'gen': tr['gen'],
                                    'audio_s': 0.1 + WARM_TAIL_S, 'est_s': guess * (0.1 + WARM_TAIL_S)}
            self._procs[i]['out'] += 1
            self._tasks[i].put((self._jid, winst, short, 0.0, self.bpm, tr['root'] or self.root))
        self._submit(('warm', track), track, full, 0.0, warm='measure')

    def _result(self, jid, y, secs, err):
        with self.lock:
            job = self.jobs.pop(jid, None)
            if job is None:
                return
            key, track, warm = job['key'], job['track'], job['warm']
            if job['wi'] is not None:
                self._procs[job['wi']]['out'] -= 1
            if job.get('sent'):
                self._inflight -= 1
                self._feed()
            tr = self.tracks.get(track)
            if tr is None or job['gen'] != tr['gen']:      # rendered with an instrument the track no longer has
                if not warm:
                    self.renders.pop(key, None)
                    self.rwait.pop(key, None)
                return
            if warm:
                if tr:
                    tr['warming'] = max(0, tr['warming'] - 1)
                    tr['warm_s'] = round(secs, 2)
                    if warm == 'measure' or self.n_workers == 0:
                        tr['est'] = secs / job['audio_s']
                    if err:
                        tr['errors'].append(f"warm-up: {err}")
                        self.news.append(f"track {track}: warm-up failed: {err}")
                return
            if tr is not None:
                r = secs / max(job['audio_s'], 0.05)
                tr['est'] = r if tr['est'] is None else 0.7 * tr['est'] + 0.3 * r
            users = self.rwait.pop(key, [])
            if err:
                self.renders.pop(key, None)
                self.stats['rejected'] += 1
                if tr is not None:
                    tr['errors'] = (tr['errors'] + [err])[-3:]
                    self.news.append(f"track {track}: a note was not played: {err}")
                for k in users:
                    if k in self.cache:
                        self.cache[k] = 'error'
                    self.waiting.pop(k, None)
                return
            self._keep_render(key, y)
            for k in users:
                if k not in self.cache:        # clip was replaced or its instrument changed meanwhile
                    self.waiting.pop(k, None)
                    continue
                self.cache[k] = y
                for on in self.waiting.pop(k, []):
                    self._place(k[0], track, on, y)

    def _keep_render(self, key, y, cap=600 * 2 ** 20):
        self.renders[key] = y
        self._rbytes += y.nbytes
        for k in list(self.renders):
            if self._rbytes <= cap:
                break
            v = self.renders[k]
            if isinstance(v, np.ndarray) and k != key:
                self._rbytes -= v.nbytes
                del self.renders[k]

    def _collect(self):
        while self.running:
            try:
                msg = self._results.get(timeout=0.2)
            except Exception:
                continue
            if msg[0] == 'ready':
                self.ready[msg[1]] = round(msg[2], 1)
                continue
            self._result(*msg)

    def _place(self, cid, track, on, y):
        c = self.tl.clips.get(cid)
        if c is None or (c.end is not None and on >= c.end - EPS):
            return
        start = self.sample(on)
        if start < self.pos:
            self.stats['late'] += 1
            self.last_late = f"{track} {fmt_bar(on, self.bpb)} by {(self.pos - start) / SR * 1000:.0f} ms"
            if start + y.shape[1] <= self.pos:
                self.stats['lost'] += 1
                self._lost_since.setdefault(track, []).append(on)
                return
        self.seq += 1
        heapq.heappush(self.pending, (start, self.seq, Seg(start, y, track, cid, on)))

    # ------------------------------------------------------------------ scheduler
    def tick(self):
        with self.lock:
            now = self.beat(self.pos)
            horizon = self.beat(self.pos + int(HORIZON_S * SR))
            gone = {c.id: c.track for c in self.tl.clips.values()}
            self.tl.prune(now)
            gone = {cid: t for cid, t in gone.items() if cid not in self.tl.clips}
            for cid, track in gone.items():
                self.meta.pop(cid, None)
                lost = []
                for key in [k for k in self.cache if k[0] == cid]:
                    lost += self.waiting.pop(key, [])
                    del self.cache[key]
                if lost:
                    # the clip ended before these notes' renders came back: they never sounded
                    self.stats['late'] += len(lost)
                    self.stats['lost'] += len(lost)
                    self.last_late = f"{track} from {fmt_bar(min(lost), self.bpb)}: never rendered in time"
                    self.news.append(self._lost_line(track, lost))
            todo = []
            for c in list(self.tl.clips.values()):
                m = self.meta[c.id]
                b0 = m['placed'] if m['placed'] is not None else c.start
                h = horizon
                if c.start > now + EPS:
                    if c.start - now > PRELOAD_WITHIN_BARS * self.bpb:
                        continue                       # far ahead: nothing renders until it comes within range
                    # queued for later: render its first pass now, so loading ahead buys render time
                    h = max(h, min(c.start + c.length, c.start + PRELOAD_S / self.spb,
                                   c.end if c.end is not None else c.start + c.length))
                tr = self.tracks.get(c.track)
                if tr and tr['inst'] and tr['inst'].get('_whole'):
                    # a whole-section event can be minutes of audio: send it one pass ahead, not HORIZON_S ahead
                    h = max(horizon, min(b0 + c.length, c.end if c.end is not None else b0 + c.length))
                if h <= b0:
                    continue
                for k, ei, on in self.tl.events(c, b0, h, m['groups']):
                    todo.append((on, c, ei, k))
                m['placed'] = h
            todo.sort(key=lambda x: x[0])
            if len(todo) > TICK_EVENTS:
                # a big batch spreads over passes (20 ms apart): each clip resumes at its first event not placed.
                # The cut falls between onsets, so the next pass never yields an event this one placed.
                cut = todo[TICK_EVENTS][0]
                n = sum(1 for x in todo if x[0] < cut - EPS) or sum(1 for x in todo if x[0] <= cut + EPS)
                for on, c, _, _ in todo[n:]:
                    m = self.meta[c.id]
                    m['placed'] = min(m['placed'], on)
                todo = todo[:n]
            for on, c, ei, k in todo:     # render in the order they will sound
                tp = self._transpose(c.track, on)
                key = (c.id, ei, tp) + self._variant(c, ei, k)
                have = self.cache.get(key)
                if isinstance(have, np.ndarray):
                    self._place(c.id, c.track, on, have)
                elif have != 'error':
                    self.waiting[key].append(on)
                    if have is None:
                        self.cache[key] = 'pending'
                        g = self.meta[c.id]['groups'][ei]
                        tr = self.tracks[c.track]
                        extra = {'_bpb': self.bpb}
                        wrapped = False
                        if isinstance(g, _Span):
                            # a performer's bar chunk: the notes before it render as context and are dropped
                            t0, ns, chunk = self._chunk(c, ei, k)
                            span = max(s + d for s, _, d, _ in ns) - t0
                            extra['_chunk'] = chunk
                            wrapped = self._variant(c, ei, k)[0]
                        else:
                            ns = [c.notes[j] for j in g]
                            t0 = ns[0][0]
                            span = max(s + d for s, _, d, _ in ns) - t0
                        notes_s = [((s - t0) * self.spb, m + tp, d * self.spb, v) for s, m, d, v in ns]
                        # code and mimic voices do not depend on where the note sits in the bar
                        lead = 0.0 if tr['inst']['type'] in ('code', 'mimic') else (t0 % self.bpb) * self.spb
                        expr = None
                        if tr['inst'].get('performer'):
                            expr = self._event_expr(c, t0, span)
                            # where it sits in the song: keyed variation. A wrapped chunk's context is the previous
                            # pass, at negative clip beats: key it as pass 2, so a voice never sees a negative song
                            # time and every pass after the first shares one render
                            extra['_beat0'] = c.beat0 + t0 + (c.length if wrapped else 0.0)
                        iauto = self._event_iauto(tr, t0)
                        ck = (c.track, tr['gen'], round(lead, 6),
                              tuple((round(a, 6), m, round(d, 6), v) for a, m, d, v in notes_s),
                              repr(sorted(expr.items())) if expr else '', repr(sorted(iauto.items())) if iauto else '',
                              repr(sorted(extra.items())))
                        r = self.renders.get(ck)
                        if isinstance(r, np.ndarray):
                            self.cache[key] = r
                            for o in self.waiting.pop(key, []):
                                self._place(c.id, c.track, o, r)
                        elif r == 'pending':
                            self.rwait[ck].append(key)
                        else:
                            self.renders[ck] = 'pending'
                            self.rwait[ck] = [key]
                            self._submit(ck, c.track, notes_s, lead, expr=expr, iauto=iauto, need=self.sample(on),
                                         extra=extra)
            self._feed()
            for dn, dk in self.decks.items():
                if dk.held:
                    self._release(dn, dk, now)

    def _unrendered(self, deck, b0, b1):
        """Tracks of `deck` with events in [b0, b1) still waiting for their render."""
        out = set()
        for key, ons in self.waiting.items():
            c = self.tl.clips.get(key[0])
            if c is not None and self.tracks.get(c.track, {}).get('deck') == deck and \
                    any(b0 - EPS <= o < b1 - EPS for o in ons):
                out.add(c.track)
        return out

    def _release(self, dn, dk, now):
        """A held deck goes on air on the first bar line whose whole bar is rendered; it is decided in the bar
        before, so a bar never starts with some tracks and not others."""
        b, first, _ = dk.held
        if now < b - self.bpb:
            return
        late = self._unrendered(dn, b, b + self.bpb)
        if not late:
            def on_air(dk=dk):
                dk.cue = False
            self._swap_at(self.sample(b) + G.LAT_BUDGET, on_air)     # deck audio runs the path budget behind
            dk.held = None
            if b > first + EPS:
                self.news.append(f"deck {dn}: on air from {fmt_bar(b, self.bpb)}, {(b - first) / self.bpb:g} bars "
                                 f"after it started (held off air until a whole bar was rendered; its first bars "
                                 f"played cued). Load further ahead next time")
        elif now >= b - 0.5:
            if b <= first + EPS:
                self.news.append(f"deck {dn}: held off air at {fmt_bar(b, self.bpb)}: not rendered yet "
                                 f"({', '.join(sorted(late))}). It goes on air on the first bar line that is")
            dk.held = (b + self.bpb, first, fmt_bar(b + self.bpb, self.bpb))

    def _transpose(self, track, beat):
        """Semitones the track's deck transposes by at `beat` (drums never transpose)."""
        t = self.tracks.get(track)
        dk = self.decks.get(t['deck']) if t else None
        if dk is None or not dk.tp or t['inst'].get('type') in instruments.DRUM_DEFAULTS or \
                t['inst'].get('type') == 'kit':
            return 0
        v = 0
        for b, x in dk.tp:
            if b <= beat + EPS:
                v = x
        return v

    def _rerender_from(self, track, beat, inst=None):
        """Drop the track's events from `beat` on and render them again (new instrument or transpose)."""
        for s in self.active + [p[2] for p in self.pending]:
            if s.track == track and s.on >= beat - EPS:
                s.dead = True
        for c in self.tl.track_clips(track):
            for key in [k for k in self.cache if k[0] == c.id]:
                del self.cache[key]
                self.waiting.pop(key, None)
            m = self.meta[c.id]
            if inst is not None:
                m['groups'] = self._groups(c, inst)
                m.pop('chunks', None)
            if m['placed'] is not None:
                m['placed'] = min(m['placed'], max(beat, c.start))

    def _schedule_loop(self):
        while self.running:
            try:
                self.tick()
            except Exception:
                traceback.print_exc()
            time.sleep(0.02)

    # ------------------------------------------------------------------ mixer
    def _blocks(self, target, chain, p0, n, post, onsets):
        return lambda i: G.LiveBlock(self, target, chain, i, p0, n, post, onsets)

    def mix_block(self, n=BLOCK):
        t_mix = time.perf_counter()
        p0, p1 = self.pos, self.pos + n
        with self.lock:
            while self.pending and self.pending[0][0] < p1:
                self.active.append(heapq.heappop(self.pending)[2])
            while self.swaps and self.swaps[0][0] <= p0:
                heapq.heappop(self.swaps)[2]()
            tracks = dict(self.tracks)
            buses = list(self.buses.items())
            order = list(self.order)
            onsets = {}
            for name in self.duck_sources:
                o = [s.start for s in self.active if s.track == name and not s.dead and s.start >= p0 - DUCK_LOOKBACK] + \
                    [q[0] for q in self.pending if q[2].track == name and not q[2].dead and q[0] < p1]
                onsets[name] = np.array(sorted(o), dtype=float)
        bufs = {}
        keep = []
        for s in self.active:
            if s.dead or s.track not in tracks:
                continue
            a, b = max(s.start, p0), min(s.start + s.y.shape[1], p1)
            if b > a:
                buf = bufs.get(s.track)
                if buf is None:
                    buf = bufs[s.track] = np.zeros((2, n))
                buf[:, a - p0:b - p0] += s.y[:, a - s.start:b - s.start]
            if s.start + s.y.shape[1] > p1:
                keep.append(s)
        self.active = keep
        master = np.zeros((2, n))
        post = {}
        bus_in = {b: np.zeros((2, n)) for b, _ in buses}
        decks = list(self.decks.items())
        streamed = {}                       # bus and deck outputs a stream listens to
        deck_in = {dn: np.zeros((2, n)) for dn, _ in decks}
        bus_fed, deck_fed = set(), set()
        ramp = np.linspace(0, 1, n)
        a = math.exp(-n / SR / 0.3)
        retired = lambda ch: (lambda i: G.LiveBlock(self, 'retired', ch, i, p0, n, post, onsets))  # noqa: E731
        dormant_after = int(DORMANT_S * SR) + G.LAT_BUDGET
        noisy_after = int(DORMANT_NOISY_S * SR) + G.LAT_BUDGET
        for name in order:
            t = tracks.get(name)
            if t is None:
                continue
            path = t['path']
            x = bufs.get(name)
            if x is None:
                if t.get('quiet', 0) > (noisy_after if path.chain.noise else dormant_after) and not path.retiring \
                        and t['cur'] == (t['gl'], t['gr']):
                    t['ms'] *= a               # dormant: no notes, tails died away; costs nothing until a note
                    self._hold(t, p0)
                    continue
                x = np.zeros((2, n))
            fed = name in bufs
            if path.chain.procs:
                x = path.chain.process(x, self._blocks('track:' + name, path.chain, p0, n, post, onsets))
                pk = float(np.max(np.abs(x))) if x.size else 0.0
                if not np.isfinite(pk) or pk > BAD_ABS:          # an effect blew up: silence this block, name it
                    x = np.zeros_like(x)
                    if t.get('fault_at', -1e9) < p0 - SR:            # once a second at most
                        self.news.append(f"track {name}: its effects produced a fault ("
                                         + (f"peak {20 * np.log10(pk):+.0f} dBFS" if np.isfinite(pk) else "NaN/inf")
                                         + "); that block was silenced. Check the chain (live_status, fx_help)")
                    t['fault_at'] = p0
            post[name] = x
            gl0, gr0 = t['cur']
            gl1, gr1 = t['gl'], t['gr']
            t['cur'] = (gl1, gr1)
            if gl0 == gl1 and gr0 == gr1:
                g = np.array([[gl1], [gr1]])
            else:
                g = np.stack([gl0 + (gl1 - gl0) * ramp, gr0 + (gr1 - gr0) * ramp])
            vsch = self.ramps.get(('track:' + name, -1, 'volume_db'))
            if vsch is not None:                       # a loaded song's volume automation: dB offset on the fader
                g = g * 10 ** (np.asarray(vsch.curve(p0, n)) / 20)
            y = x * g
            if self.tap is not None:
                self.tap('track:' + name, p0, y, path.chain.latency)
            ret = path.run_retiring(n, retired)
            dest = deck_in.get(t['deck'], master)
            if t['output'] in bus_in:
                bus_in[t['output']] += t['out_lag'](y, n) + (ret * g if ret is not None else 0)
            elif ret is not None:
                dest += path.lag(y, n) + ret * g
            else:
                dest += path.lag(y, n)
            for bus, lag in t['send_lags'].items():
                if bus in bus_in:
                    bus_in[bus] += lag(y * (10 ** (t['sends'][bus] / 20)), n)
                    bus_fed.add(bus)
            if t['output'] in bus_in:
                bus_fed.add(t['output'])
            ms = float(np.mean(y ** 2))
            t['ms'] = t['ms'] * a + ms * (1 - a)
            self._hold(t, p0)
            floor = max(QUIET, 4 * path.chain.noise * max(abs(gl1), abs(gr1)))     # an amp's own hiss is not music
            t['quiet'] = 0 if fed or ms > floor * floor else t.get('quiet', 0) + n
            if not t['quiet']:
                deck_fed.add(t['deck'])
        for bname, b in buses:
            path = b['path']
            if bname not in bus_fed and b.get('quiet', 0) > (noisy_after if path.chain.noise else dormant_after) \
                    and not path.retiring:
                b['ms'] *= a
                self._hold(b, p0)
                if self.hub.wants('bus:' + bname):     # a listener hears silence, not a gap (streams stay in step)
                    streamed['bus:' + bname] = np.zeros((2, n), dtype=np.float32)
                continue
            x = bus_in[bname]
            if path.chain.procs:
                x = path.chain.process(x, self._blocks('bus:' + bname, path.chain, p0, n, post, onsets))
            ret = path.run_retiring(n, retired)
            if ret is not None:
                x = x + ret
            gl0, gr0 = b['cur']
            gl1, gr1 = b['gl'], b['gr']
            b['cur'] = (gl1, gr1)
            y = x * np.stack([gl0 + (gl1 - gl0) * ramp, gr0 + (gr1 - gr0) * ramp])
            vsch = self.ramps.get(('bus:' + bname, -1, 'volume_db'))
            if vsch is not None:                       # a loaded song's bus volume automation
                y = y * 10 ** (np.asarray(vsch.curve(p0, n)) / 20)
            if self.tap is not None:
                self.tap('bus:' + bname, p0, y, G.LAT_BUDGET)
            if self.hub.wants('bus:' + bname):
                streamed['bus:' + bname] = y.copy()
            deck_in.get(b['deck'], master).__iadd__(y)
            ms = float(np.mean(y ** 2))
            b['ms'] = b['ms'] * a + ms * (1 - a)
            self._hold(b, p0)
            floor = max(QUIET, 4 * path.chain.noise * max(abs(gl1), abs(gr1)))
            b['quiet'] = 0 if bname in bus_fed or ms > floor * floor else b.get('quiet', 0) + n
            if not b['quiet']:
                deck_fed.add(b['deck'])
        for dn, dk in decks:
            i = (p0 - G.LAT_BUDGET) % dk.air.shape[1]
            if dn not in deck_fed and dk.quiet > dormant_after:
                dk.ms *= a
                j = min(i + n, dk.air.shape[1])
                dk.air[:, i:j] = 0
                if j - i < n:
                    dk.air[:, :n - (j - i)] = 0
                if self.hub.wants('deck:' + dn):
                    streamed['deck:' + dn] = np.zeros((2, n), dtype=np.float32)
                continue
            if self.tap is not None:
                self.tap('deck:' + dn, p0, deck_in[dn], G.LAT_BUDGET)
            x = deck_in[dn]
            if dk.master is not None:
                # the song's own master chain, as in its studio render (live_parity compares the sum before it)
                x = dk.master.process(x, self._blocks('master:' + dn, dk.master, p0, n, post, onsets))
            y = dk.strip.process(x, {k: self._deck_param(dn, k, p0, n) for k in D.PARAMS})
            if self.hub.wants('deck:' + dn):
                streamed['deck:' + dn] = y.copy()
            dk.quiet = 0 if dn in deck_fed else dk.quiet + n
            dk.ms = dk.ms * a + float(np.mean(y ** 2)) * (1 - a)
            j = min(i + n, dk.air.shape[1])
            dk.air[:, i:j] = y[:, :j - i]
            if j - i < n:
                dk.air[:, :n - (j - i)] = y[:, j - i:]
            if not dk.cue:
                master += y
        fade = None
        if self.fade is not None:
            left, total = self.fade
            g0 = left / total
            g1 = max(0.0, (left - n) / total)
            fade = np.linspace(g0, g1, n)
            master *= fade
            self.fade = (max(0, left - n), total)
        if np.max(np.abs(master)) > SOUND_FLOOR:
            self.sounded = p1
        self._lvl[0] += float(np.sum(master * master))
        self._lvl[1] += n
        if self._lvl[1] >= SR:
            self._second(p1, tracks)
        y = self.safety.process(master).astype(np.float32)
        self.hub.push('master', y, limited=True)
        for name, s in streamed.items():
            self.hub.push(name, s * fade if fade is not None else s)
        # the output lags the mix by the path budget + the safety limiter's lookahead: keep live_listen's bars aligned
        i = (p0 - self.safety.la - G.LAT_BUDGET) % self.air.shape[1]
        j = min(i + n, self.air.shape[1])
        self.air[:, i:j] = y[:, :j - i]
        if j - i < n:
            self.air[:, :n - (j - i)] = y[:, j - i:]
        if self.rec is not None:
            # file t=0 is exactly the downbeat of the first recorded bar (output trails the mix by lat samples)
            m0 = p0 - self.safety.la - G.LAT_BUDGET
            if m0 + n > self.rec_start:
                self.rec.write(y[:, max(0, self.rec_start - m0):].T)
                self.rec_n += n - max(0, self.rec_start - m0)
        self.pos = p1
        load = (time.perf_counter() - t_mix) / (n / SR)
        self.mix_load = load if self.mix_load is None else 0.97 * self.mix_load + 0.03 * load
        self.mix_peak = max(self.mix_peak, load)
        return y

    def _mix_loop(self):
        ahead = int(AHEAD_S * SR)
        while self.running:
            if self._fifo_n < ahead:
                y = self.mix_block()
                with self._fifo_lock:
                    self._fifo.append(y.T.copy())
                    self._fifo_n += y.shape[1]
            else:
                time.sleep(0.003)
            if self.fade is not None and self.fade[0] == 0 and self._fifo_n == 0:
                self.running = False

    @staticmethod
    def _hold(node, p0):
        """Loudest smoothed level per second for the last HOLD_S seconds: a sparse part (a vocal call, a stab)
        reads silent in a snapshot taken in its rests."""
        sec = p0 // SR
        h = node.setdefault('hold', {})
        h[sec] = max(h.get(sec, 0.0), node['ms'])
        if len(h) > HOLD_S + 1:
            for k in [k for k in h if k < sec - HOLD_S]:
                del h[k]

    def _pull(self, frames):
        self.last_pull = time.time()
        out = np.zeros((frames, 2), dtype=np.float32)
        got = 0
        with self._fifo_lock:
            while got < frames and self._fifo:
                blk = self._fifo[0]
                take = min(frames - got, blk.shape[0] - self._head)
                out[got:got + take] = blk[self._head:self._head + take]
                got += take
                self._head += take
                if self._head >= blk.shape[0]:
                    self._fifo.popleft()
                    self._head = 0
            self._fifo_n -= got
        if got < frames and self.pos > 0 and self.fade is None:
            self.stats['underruns'] += 1
            self.last_underrun = fmt_bar(self.beat(self.played), self.bpb)
        self.played += got
        return out

    def _null_loop(self):
        t0 = time.time()
        done = 0
        while self.running and self._null_on:
            want = int((time.time() - t0) * SR) - done
            if want >= BLOCK:
                self._pull(want)
                done += want
            time.sleep(0.005)

    def wait_ready(self, timeout=120.0):
        """Block until every render worker has warmed itself (or timeout); returns the seconds waited."""
        t0 = time.time()
        while self.n_workers and len(self.ready) < self.n_workers and time.time() - t0 < timeout:
            time.sleep(0.1)
        return time.time() - t0

    def start(self):
        # everything alive now (imports, compiled kernels, the engine) leaves the collector's view: a full collection
        # scanned ~170k objects and stopped every thread, the mixer's included, for ~100 ms after a big queue
        gc.collect()
        gc.freeze()
        # the mixer's numpy calls each take the GIL back; while another thread runs Python (a big queue being
        # parsed) each could wait out the default 5 ms switch interval
        sys.setswitchinterval(0.001)
        # on the machine's job board as a live engine: it holds a CPU slot and heavy jobs elsewhere make room
        self._on_board = contextlib.ExitStack()
        try:
            self._on_board.enter_context(machine.slot('live', f"live engine {os.path.basename(self.root)}", threads=None))
        except Exception:                      # the board is advice for others; the set plays regardless
            pass
        threads = [self._schedule_loop, self._mix_loop]
        if self.n_workers:
            threads.append(self._collect)
        for fn in threads:
            threading.Thread(target=fn, daemon=True).start()
        with self._out_lock:
            self._open_output(self.device, rescan=False)
        if self.device not in (None, 'none', 'null'):
            threading.Thread(target=self._watch_output, daemon=True).start()

    # ------------------------------------------------------------------ output device
    def _open_output(self, device, rescan=True):
        """Open the output on `device` ('default', a name or index, or 'none'). rescan re-reads the system's device
        list first (a speaker that connected after this process started is not in it)."""
        if device in (None, 'none', 'null'):
            self.stream, self.out_name = None, 'none'
            if not self._null_on:
                self._null_on = True
                threading.Thread(target=self._null_loop, daemon=True).start()
            return
        import sounddevice as sd
        self._null_on = False
        if rescan:
            sd._terminate()
            sd._initialize()

        def cb(outdata, frames, t, status):
            outdata[:] = self._pull(frames)
        dev = None if device in ('default', '') else (int(device) if str(device).isdigit() else device)
        st = sd.OutputStream(samplerate=SR, channels=2, dtype='float32', callback=cb, device=dev, latency='high')
        st.start()
        self.stream = st
        try:
            self.out_name = sd.query_devices(st.device, 'output')['name'] if dev is not None else \
                sd.query_devices(kind='output')['name']
        except Exception:
            self.out_name = str(device)
        self.last_pull = time.time()

    def _close_output(self):
        st, self.stream = self.stream, None
        if st is None:
            return True

        def close():
            try:
                st.stop()
                st.close()
            except Exception:
                pass
        t = threading.Thread(target=close, daemon=True)
        t.start()
        t.join(3.0)                     # a dead device (a Bluetooth speaker gone) can block stop() forever
        return not t.is_alive()

    def cmd_device(self, device='default', follow=None, reopen=True):
        """Move the sound to another output mid-set: the timeline, the queue and the audio mixed ahead carry on (a
        gap of about a second). device: 'default' (whatever the system's default is now), a name or part of one, an
        index, or 'none'. reopen=False only sets follow and stays on the output it has (no gap)."""
        if follow is not None:
            self.follow = bool(follow)
        if not reopen:
            return f"output: {self.out_name or self.device}" + (
                "; follows the system default" if self.device == 'default' and self.follow else
                "; stays on it (does not follow the system default)")
        old = self.out_name or self.device
        with self._out_lock:
            if not self._close_output():
                self.news.append("the previous output did not close (a vanished device); it was left behind")
            try:
                self._open_output(device)
            except Exception as e:
                try:
                    self._open_output(self.device)
                    back = f"; still on {self.out_name}"
                except Exception:
                    self._open_output('none')
                    back = "; nothing could be opened, so the set plays silently (live_device to try again)"
                names = _output_names()
                raise LiveError(f"could not open {device!r}: {e}{back}. Outputs now: {', '.join(names) or 'none'}")
            self.device = device
        return f"output: {self.out_name} (was {old})" + (
            "; follows the system default" if device == 'default' and self.follow else '')

    def _watch_output(self):
        """Device 'default' follows the system's default output (a Bluetooth speaker connecting moves the set
        there), and a device that stopped asking for audio (gone, asleep) is reopened."""
        while self.running:
            time.sleep(WATCH_S)
            if self.running:
                self._check_output()

    def _check_output(self):
        """One look at the output: moves it when the default changed or the device stopped taking audio."""
        if self.fade is not None:
            return
        stalled = self.stream is not None and self.last_pull is not None and \
            time.time() - self.last_pull > STALL_S
        moved = None
        if self.device == 'default' and self.follow and self.stream is not None:
            now = O.default_output_name()
            if now and self.out_name and now != self.out_name:
                moved = now
        if not (moved or stalled) or time.time() - self._out_tried < 2 * WATCH_S:
            return
        self._out_tried = time.time()
        was = self.out_name
        try:
            self.cmd_device('default' if stalled else self.device)      # a vanished device falls back to the default
            self.news.append(f"output moved from {was} to {self.out_name} "
                             + ("(the system's default changed)" if moved else "(the device stopped taking audio)"))
        except LiveError as e:
            self.news.append(f"output: {e}")

    def shutdown(self):
        self.running = False
        if getattr(self, '_on_board', None) is not None:
            self._on_board.close()
        if getattr(self, 'stream', None) is not None:
            self.device_hung = not self._close_output()
        if self.rec is not None:
            self.rec.close()
            self.rec = None
        for q in getattr(self, '_tasks', []):
            q.put(None)
        self.stopped.set()

    # ------------------------------------------------------------------ commands
    def _resolve_instrument(self, spec):
        if isinstance(spec, str) and spec.startswith('track:'):
            try:
                with open(os.path.join(self.root, 'project.json'), encoding='utf8') as f:
                    tracks = json.load(f)['tracks']
            except (OSError, ValueError, KeyError):
                raise LiveError(f"instrument {spec!r}: the live folder {self.root} has no project.json with tracks")
            if spec[6:] not in tracks or not tracks[spec[6:]].get('instrument'):
                raise LiveError(f"instrument {spec!r}: project tracks with instruments: "
                                f"{[k for k, v in tracks.items() if v.get('instrument')]}")
            spec = copy.deepcopy(tracks[spec[6:]]['instrument'])
        elif isinstance(spec, str):
            name = spec[7:] if spec.startswith('preset:') else spec
            if name not in PRESETS:
                raise LiveError(f"unknown preset {name!r}; presets: {', '.join(PRESETS)} (or pass an instrument dict)")
            spec = copy.deepcopy(PRESETS[name])
        if not isinstance(spec, dict):
            raise LiveError("instrument: a dict, 'preset:<name>' or 'track:<project track>'")
        if spec.get('fx'):
            raise LiveError("effects belong to the track, not the instrument: live_track(track, fx=[...])")
        try:
            # voice modules are checked by the op before they get here: importing one in this process could hold
            # the GIL for seconds and starve the audio
            inst = instruments.normalize(spec)
        except (instruments.InstrumentError, ValueError) as e:
            raise LiveError(f"instrument invalid: {e}")
        return inst

    # ------------------------------------------------------------------ graph (effects, buses)
    def _chain(self, fxs, where, dry_default, own=None):
        try:
            fxs = G.normalize_chain(fxs, where)
        except G.GraphError as e:
            raise LiveError(str(e))
        bake, fxs = G.split_chain(fxs)
        if bake and own is None:
            raise LiveError(f"{where}: {', '.join(sorted({f['type'] for f in bake if f['type'] not in F.PROCS}))} has "
                            f"no live version and a bus cannot bake it (a bus has no notes to render): put it on "
                            f"the tracks instead")
        for f in bake:
            src = [f.get(k) for k in ('sidechain', 'source', 'modulator') if f.get(k)]
            if src:
                raise LiveError(f"{where}: {f['type']} reads {src[0]!r} but sits before a studio-only effect, so it "
                                f"would be baked per note, where other tracks are not available: move it after the "
                                f"studio-only effects")
        for d in G.deps(fxs):
            if d == own:
                raise LiveError(f"{where}: a track cannot sidechain, duck or vocode from itself")
            if d not in self.tracks:
                raise LiveError(f"{where}: an effect reads track {d!r}, which is not a live track; tracks: "
                                f"{list(self.tracks) or 'none'}. Create {d!r} with live_track first, then this chain (or add "
                                f"the effect to this track afterwards with live_track(fx=...))")
        try:
            return G.Chain(fxs, self.bpm, dry_default, bake)
        except (F.FxError, ValueError) as e:
            raise LiveError(f"{where}: {e}")

    def _job_inst(self, tr, expr=None, iauto=None):
        """The instrument as a render job sees it: baked effects, expression lanes and instrument automation
        ride along."""
        inst = tr['inst']
        bake = tr.get('bake') or []
        if not bake and not expr and not iauto:
            return inst
        return dict(inst, _bake=bake, _expr=expr or {}, _auto=iauto or {})

    def _groups(self, c, inst):
        """A clip's render events: bar chunks for a performer voice (it plays a whole part, so each chunk renders
        with the part before it as context), else single notes or mono phrases."""
        if inst.get('performer') and not inst.get('_whole'):
            spans = _chunk_spans(c.notes, c.length, self.bpb, CHUNK_REACH_S / self.spb)
            if c.context and (not spans or spans[0].on > EPS):
                spans.insert(0, _Span(0.0, min(float(self.bpb), c.length)))     # what came before rings in
            return spans
        return _mono_groups(c.notes, inst)

    def _variant(self, c, ei, k):
        """For a performer's chunk on pass k of its clip: (context from the previous pass, cut at its end, context
        from before the clip)."""
        spans = self.meta[c.id]['groups']
        if not spans or not isinstance(spans[0], _Span):
            return ()
        loops = c.loop is None or c.loop > 1
        more = c.loop is None or k < c.loop - 1
        seam = loops and spans[0].on < EPS and spans[-1].end > c.length - EPS
        cut = (ei + 1 < len(spans) and abs(spans[ei + 1].on - spans[ei].end) < EPS) or \
            (ei == len(spans) - 1 and more and seam)
        return (k >= 1 and ei == 0 and seam, cut, k == 0 and ei == 0 and bool(c.context))

    def _chunk(self, c, ei, k):
        """(render origin in clip beats, notes from there to the chunk's end, the worker's '_chunk') for chunk ei
        on pass k: context is the last CHUNK_PRE_S of the part, reaching back to the start of any note still held
        at the chunk's start; a chunk cut at its end crossfades into the next one, which plays what still rings."""
        wrapped, cut, lead_in = self._variant(c, ei, k)
        memo = self.meta[c.id].setdefault('chunks', {})
        if (ei, wrapped, cut, lead_in) in memo:
            return memo[(ei, wrapped, cut, lead_in)]
        spans = self.meta[c.id]['groups']
        sp = spans[ei]
        notes = list(c.notes)
        if wrapped:
            notes = [(s - c.length, m, d, v) for s, m, d, v in c.notes] + notes
        elif lead_in:
            notes = list(c.context) + notes
        pre = CHUNK_PRE_S / self.spb
        lo = min([sp.on] + [s for s, _, d, _ in notes if s < sp.on - EPS and (s + d > sp.on + EPS or s >= sp.on - pre)])
        ns = [n for n in notes if lo - EPS <= n[0] < sp.end + CHUNK_LOOK_S / self.spb - EPS]
        fade_in = (ei > 0 and abs(spans[ei - 1].end - sp.on) < EPS) or wrapped
        out = memo[(ei, wrapped, cut, lead_in)] = (lo, ns, {'pre': (sp.on - lo) * self.spb, 'len': (sp.end - sp.on) * self.spb,
                                                   'xf': CHUNK_XF_S, 'fade_in': fade_in, 'cut': cut})
        return out

    def _event_iauto(self, tr, t0):
        """A deck track's instrument automation for the event starting at clip beat t0: {param: [(seconds from
        the onset, value)]}."""
        lanes = tr.get('inst_auto')
        if not lanes:
            return None
        return {k: [((b - t0) * self.spb, v) for b, v in pts] for k, pts in lanes.items()}

    def _event_expr(self, c, t0, span):
        """A clip's expression lanes cut to one event: [(seconds from the event onset, value)], starting with
        the value in force at the onset."""
        if not c.expr:
            return None
        out = {}
        for name, pts in c.expr.items():
            before = [v for b, v in pts if b <= t0 + EPS]
            seg = [((b - t0) * self.spb, v) for b, v in pts if t0 + EPS < b <= t0 + span]
            out[name] = [(0.0, before[-1] if before else pts[0][1])] + seg
        return out

    def _order(self, override=None):
        """Tracks in an order where every sidechain/vocoder source comes before the tracks that read it."""
        chains = {k: t['path'].chain.fx for k, t in self.tracks.items()}
        chains.update(override or {})
        order, seen, busy = [], set(), set()

        def visit(k):
            if k in seen:
                return
            if k in busy:
                raise LiveError(f"sidechain loop through track {k!r}: two tracks cannot key each other")
            busy.add(k)
            for d in G.audio_deps(chains.get(k, [])):
                if d in chains:
                    visit(d)
            busy.discard(k)
            seen.add(k)
            order.append(k)
        for k in chains:
            visit(k)
        return order

    def _refresh_graph(self):
        self.order = self._order()
        src = set()
        for t in self.tracks.values():
            src |= {f['source'] for f in t['path'].chain.fx if f['type'] == 'duck' and f.get('source')}
        for b in self.buses.values():
            src |= {f['source'] for f in b['path'].chain.fx if f['type'] == 'duck' and f.get('source')}
        self.duck_sources = src

    def _send_lags(self, t):
        lat = t['path'].chain.latency
        t['send_lags'] = {b: F._Lag(G.LAT_BUDGET - lat - self.buses[b]['path'].chain.latency, signal=True)
                          for b in t['sends'] if b in self.buses}
        out = t.get('output')
        t['out_lag'] = F._Lag(G.LAT_BUDGET - lat - self.buses[out]['path'].chain.latency, signal=True) \
            if out in self.buses else None

    def _deck_param(self, deck, k, p0, n):
        sch = self.ramps.get(('deck:' + deck, -1, k))
        return self.decks[deck].values[k] if sch is None else sch.curve(p0, n)

    def _check_latency(self, track_lat, sends, where, bus_override=None):
        for b in sends:
            bl = bus_override[1] if bus_override and bus_override[0] == b else self.buses[b]['path'].chain.latency
            if track_lat + bl > G.LAT_BUDGET:
                raise LiveError(f"{where}: this path needs {track_lat + bl} samples of lookahead (track {track_lat} + "
                                f"bus {b} {bl}); the live budget is {G.LAT_BUDGET}. Drop a hall/limiter/oversampled "
                                f"distortion from one of them")
        if track_lat > G.LAT_BUDGET:
            raise LiveError(f"{where}: the chain needs {track_lat} samples of lookahead; the live budget is "
                            f"{G.LAT_BUDGET}")

    def _at_sample(self, at):
        if at in (None, 'now', 'asap'):
            return self.pos
        now = self.beat(self.pos)
        try:
            beat, _ = self.tl.resolve_at(at, now, now)
        except QueueError as e:
            raise LiveError(str(e))
        return self.sample(beat)

    def _swap_at(self, sample, fn):
        self.seq += 1
        heapq.heappush(self.swaps, (sample, self.seq, fn))

    def _keep_ramps(self, key, gen):
        """A chain swap: effect ramps written for other chains stop (volume ramps stay)."""
        for k in [k for k in self.ramps if k[0] == key and k[1] >= 0]:
            if not self.ramps[k].keep_gen(gen):
                del self.ramps[k]

    @staticmethod
    def _chain_at(node, s0):
        """The chain that will be playing at sample s0: the current one or a scheduled replacement."""
        due = [p for p in node.get('pending', []) if p[0] <= s0]
        return max(due, key=lambda p: p[0])[1] if due else node['path'].chain

    def _target(self, target):
        if target.startswith('bus:'):
            b = self.buses.get(target[4:])
            if b is None:
                raise LiveError(f"no live bus {target[4:]!r}; buses: {list(self.buses) or 'none'} (live_bus to add)")
            return b
        t = self.tracks.get(target)
        if t is None:
            raise LiveError(f"no live track {target!r}; tracks: {list(self.tracks) or 'none'}; buses are 'bus:<name>'")
        return t

    def cmd_track(self, track, instrument=None, volume_db=None, pan=None, remove=False, at='next_bar', fx=None,
                  sends=None, deck=None, output=None, root=None, warm=True):
        new_chain = None
        if fx is not None:
            new_chain = self._chain(fx, f"track {track!r}", 1.0, own=track)      # built outside the lock
        with self.lock:
            now = self.beat(self.pos)
            if remove:
                if track not in self.tracks:
                    raise LiveError(f"no live track {track!r}; tracks: {list(self.tracks) or 'none'}")
                users = [k for k, t in self.tracks.items() if track in G.deps(t['path'].chain.fx)]
                if users:
                    raise LiveError(f"tracks {users} read {track!r} (sidechain/duck/vocoder); change their fx first")
                self.tl.claim(track, now)
                for s in self.active + [p[2] for p in self.pending]:
                    if s.track == track:
                        s.dead = True
                del self.tracks[track]
                self._refresh_graph()
                return f"removed track {track}"
            msg = []
            sends = None if sends is None else {str(k): float(v) for k, v in sends.items()}
            if sends:
                bad = [b for b in sends if b not in self.buses]
                if bad:
                    raise LiveError(f"sends to unknown buses {bad}; buses: {list(self.buses) or 'none'} (live_bus first)")
                loud = [b for b, v in sends.items() if v > 6]
                if loud:
                    raise LiveError(f"send levels above +6 dB: {loud}")
            if deck is not None and track in self.tracks and self.tracks[track]['deck'] != deck:
                raise LiveError(f"track {track!r} is on deck {self.tracks[track]['deck']!r}; a track stays on the deck "
                                f"it was created on (remove it and create it again)")
            if track not in self.tracks:
                if instrument is None:
                    raise LiveError(f"new track {track!r} needs an instrument: a dict, 'preset:<name>' (presets_list) "
                                    f"or {{'type': 'code', 'voice': '<name>'}} (voices_list)")
                if deck is not None and deck not in self.decks:
                    self.decks[deck] = D.Deck(deck)
                    msg.append(f"new deck {deck}")
                if output is not None and output not in self.buses:
                    raise LiveError(f"output bus {output!r} does not exist")
                chain = new_chain or G.Chain([], self.bpm, 1.0)
                self._check_latency(chain.latency, dict(sends or {}, **({output: 0} if output else {})),
                                    f"track {track!r}")
                if new_chain is not None:
                    self._order({track: new_chain.fx})
                self.tracks[track] = {'inst': None, 'volume_db': 0.0, 'pan': 0.0, 'gl': 0.0, 'gr': 0.0,
                                      'cur': (0.0, 0.0), 'ms': 0.0, 'est': None, 'warming': 0, 'warm_s': None,
                                      'errors': [], 'gen': 0, 'path': G.Path(chain), 'sends': sends or {},
                                      'send_lags': {}, 'deck': deck, 'output': output, 'out_lag': None,
                                      'root': root, 'bake': list(chain.bake)}
                self._send_lags(self.tracks[track])
                self._refresh_graph()
                new_chain = None
                msg.append(f"new track {track}")
            tr = self.tracks[track]
            if new_chain is not None:
                self._check_latency(new_chain.latency, sends if sends is not None else tr['sends'], f"track {track!r}")
                self._order({track: new_chain.fx})
                s0 = self._at_sample(at)

                if new_chain.bake != tr.get('bake'):
                    # baked effects live in the rendered notes: render them again from the swap on
                    tr['bake'] = list(new_chain.bake)
                    tr['gen'] += 1
                    if tr['inst'] is not None:
                        self._rerender_from(track, self.beat(s0), tr['inst'])

                self.chain_gen += 1
                new_chain.gen = self.chain_gen
                tr.setdefault('pending', []).append((s0, new_chain))

                def swap(tr=tr, ch=new_chain, name=track):
                    tr['path'].retire_to(ch)
                    tr['pending'] = [p for p in tr.get('pending', []) if p[1] is not ch]
                    self._keep_ramps('track:' + name, ch.gen)
                    self._send_lags(tr)
                    self._refresh_graph()
                self._swap_at(s0, swap)
                msg.append(f"fx {new_chain.describe(False)} from {fmt_bar(self.beat(s0), self.bpb)} (old tail rings out)")
            if sends is not None:
                self._check_latency((new_chain or tr['path'].chain).latency, sends, f"track {track!r}")
                tr['sends'] = sends
                self._send_lags(tr)
                msg.append("sends " + (', '.join(f"{b} {v:+g} dB" for b, v in sends.items()) or 'none'))
            if instrument is not None:
                inst = self._resolve_instrument(instrument)
                old = tr['inst']
                tr['inst'] = inst
                tr['errors'] = []
                tr['gen'] += 1
                tr['warming'] = 0
                if old is not None:
                    # the new sound takes over at `at`: drop events from there on and render them again
                    evs = sorted((e for c in self.tl.track_clips(track) for e in self._events_of(c.notes, inst)),
                                 key=lambda e: e[0])
                    beat, _ = self.tl.resolve_at(at, now, now + self._lead_beats(track, evs[:8]))
                    self._rerender_from(track, beat, inst)
                    msg.append(f"instrument changed from {fmt_bar(beat, self.bpb)}")
                if warm:
                    self._warm(track)
                msg.append(f"{inst['type']}" + (f" voice {inst['voice']}" if inst.get('voice') else '') + ", warming up")
            if volume_db is not None:
                if volume_db > 6:
                    raise LiveError(f"volume_db {volume_db:g} is above the +6 dB fader limit; lower the others instead")
                tr['volume_db'] = float(volume_db)
            if pan is not None:
                tr['pan'] = max(-1.0, min(1.0, float(pan)))
            tr['gl'], tr['gr'] = G.fader_gains(tr['volume_db'], tr['pan'])
            msg.append(f"vol {tr['volume_db']:g} dB, pan {tr['pan']:g}")
            return f"{track}: " + ', '.join(msg)

    def cmd_bus(self, bus, fx=None, volume_db=None, remove=False, at='now', deck=None):
        chain = self._chain(fx, f"bus {bus!r}", 0.0) if fx is not None else None
        with self.lock:
            if remove:
                if bus not in self.buses:
                    raise LiveError(f"no live bus {bus!r}; buses: {list(self.buses) or 'none'}")
                dropped = []
                for k, t in self.tracks.items():
                    if bus in t['sends'] or t['output'] == bus:
                        t['sends'].pop(bus, None)
                        if t['output'] == bus:
                            t['output'] = None
                        self._send_lags(t)
                        dropped.append(k)
                del self.buses[bus]
                self._refresh_graph()
                return f"removed bus {bus}" + (f"; dropped the sends from {dropped}" if dropped else '')
            msg = []
            if bus not in self.buses:
                ch = chain or G.Chain([], self.bpm, 0.0)
                if deck is not None and deck not in self.decks:
                    self.decks[deck] = D.Deck(deck)
                self.buses[bus] = {'path': G.Path(ch, G.LAT_BUDGET - ch.latency), 'volume_db': 0.0, 'gl': 1.0,
                                   'gr': 1.0, 'cur': (1.0, 1.0), 'ms': 0.0, 'deck': deck}
                chain = None
                msg.append(f"new bus {bus} ({self.buses[bus]['path'].chain.describe(False)}); send to it with "
                           f"live_track(track, sends={{'{bus}': -6}})")
            b = self.buses[bus]
            if chain is not None:
                for k, t in self.tracks.items():
                    if bus in t['sends']:
                        self._check_latency(t['path'].chain.latency, t['sends'], f"track {k!r}", (bus, chain.latency))
                s0 = self._at_sample(at)

                self.chain_gen += 1
                chain.gen = self.chain_gen
                b.setdefault('pending', []).append((s0, chain))

                def swap(b=b, ch=chain, name=bus):
                    b['path'].retire_to(ch, G.LAT_BUDGET - ch.latency)
                    b['pending'] = [p for p in b.get('pending', []) if p[1] is not ch]
                    self._keep_ramps('bus:' + name, ch.gen)
                    for t in self.tracks.values():
                        if name in t['sends'] or t['output'] == name:
                            self._send_lags(t)
                    self._refresh_graph()
                self._swap_at(s0, swap)
                msg.append(f"fx {chain.describe(False)} from {fmt_bar(self.beat(s0), self.bpb)} (old tail rings out)")
            if volume_db is not None:
                if volume_db > 6:
                    raise LiveError(f"volume_db {volume_db:g} is above the +6 dB fader limit")
                b['volume_db'] = float(volume_db)
                b['gl'], b['gr'] = G.fader_gains(b['volume_db'], 0.0)
            msg.append(f"vol {b['volume_db']:g} dB")
            return f"bus {bus}: " + ', '.join(msg)

    def cmd_fx(self, target, index=None, params=None, ramp_beats=0, at='now', clear=False):
        with self.lock:
            node = self._target(target)
            key = ('bus:' + target[4:]) if target.startswith('bus:') else 'track:' + target
            s0 = self._at_sample(at)
            chain = self._chain_at(node, s0)
            fxs = chain.fx
            later = (f"; from {fmt_bar(self.beat(s0), self.bpb)} the chain scheduled then"
                     if chain is not node['path'].chain else '')
            if clear and index is None:
                return self._clear_ramps(node, key, None, s0, at)
            if isinstance(index, str):                 # by type: 'filter', or 'filter:2' for the second filter
                kind, _, nth = index.partition(':')
                hits = [i for i, x in enumerate(fxs) if x['type'] == kind]
                k = int(nth) - 1 if nth.isdigit() else 0
                if not 0 <= k < len(hits):
                    raise LiveError(f"{target} has no {index!r} effect ({chain.describe(False)}{later}); use a type "
                                    f"in the chain, 'type:2' for the second of a type, or a 0-based index")
                index = hits[k]
            if not isinstance(index, int) or not 0 <= index < len(fxs):
                raise LiveError(f"{target} has {len(fxs)} effects ({chain.describe(False)}{later}); index "
                                f"{index!r} is out of range (0-based)")
            if clear:
                return self._clear_ramps(node, key, index, s0, at)
            f = fxs[index]
            if not isinstance(params, dict) or not params:
                raise LiveError(f"params: a dict of {f['type']} params, e.g. {{'mix': 0.4}}; valid: "
                                f"{sorted(F.FX_DEFAULTS[f['type']])}")
            bad = set(params) - set(F.FX_DEFAULTS[f['type']])
            if bad:
                raise LiveError(f"{f['type']} has no params {sorted(bad)}; valid: {sorted(F.FX_DEFAULTS[f['type']])}")
            auto = set(F.AUTOMATABLE.get(f['type'], ()))
            s1 = s0 + max(int(float(ramp_beats) * self.spb * SR), int(G.MIN_RAMP_S * SR))
            msg = []
            for name, v in params.items():
                if name not in auto:
                    continue
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    raise LiveError(f"{f['type']}.{name} = {v!r}: a number")
                sch = self.ramps.get((key, index, name))
                if sch is None:
                    sch = self.ramps[(key, index, name)] = G.Schedule()
                own = [x for x in sch.r if x.gen == chain.gen]
                before = [x for x in own if x.p0 <= s0]
                # where this chain's param is at s0: its last move so far, else held before its first move
                v0 = before[-1].value(s0) if before else own[0].v0 if own else float(f[name])
                log = any(k in name for k in G.LOG_PARAMS)
                sch.add(G.Ramp(s0, v0, s1, v, log, chain.gen))
                f[name] = v
                msg.append(f"{name} {v0:g} -> {v:g}" + (f" over {float(ramp_beats):g} beats" if ramp_beats else ''))
            rebuild = {k: v for k, v in params.items() if k not in auto}
        if rebuild:
            new = copy.deepcopy(fxs)
            new[index].update(rebuild)
            ch = self._chain(new, target, 1.0 if not target.startswith('bus:') else 0.0,
                             own=None if target.startswith('bus:') else target)
            with self.lock:
                if target.startswith('bus:'):
                    self.cmd_bus(target[4:], fx=new, at=at)
                else:
                    self._check_latency(ch.latency, node['sends'], target)
                    self.cmd_track(target, fx=new, at=at)
            msg.append(f"rebuilt {f['type']} with {rebuild} (not automatable: the effect restarts; its old tail "
                       f"rings out)")
        start = 'now' if at in (None, 'now', 'asap') else fmt_bar(self.beat(s0), self.bpb)
        return f"{target} fx[{index}] {f['type']} ({start}): " + '; '.join(msg) + later

    def cmd_moves(self, moves):
        """Many live_fx moves in one call (a whole cycle of choreography): applied in order; a bad one stops the
        batch and says which, after the ones before it."""
        if not isinstance(moves, list) or not moves:
            raise LiveError("moves: a list of {target, index, params, ramp_beats, at} (live_fx arguments)")
        out = []
        for i, m in enumerate(moves):
            if not isinstance(m, dict) or 'target' not in m:
                raise LiveError(f"move [{i}]: a dict with target, index, params (ramp_beats, at, clear optional); "
                                f"{len(out)} moves before it were scheduled")
            bad = set(m) - {'target', 'index', 'params', 'ramp_beats', 'at', 'clear'}
            if bad:
                raise LiveError(f"move [{i}]: unknown keys {sorted(bad)}; {len(out)} moves before it were scheduled")
            try:
                out.append(self.cmd_fx(**m))
            except LiveError as e:
                raise LiveError(f"move [{i}] ({m.get('target')} {m.get('index')!r}): {e}. {len(out)} moves before it "
                                f"were scheduled; fix it and send the rest")
        return f"{len(out)} moves scheduled\n" + '\n'.join(out)

    def _clear_ramps(self, node, key, index, s0, at):
        """Cancel the scheduled moves on a track or bus from s0 on (every effect, or one index, plus the volume
        when index is None): each param holds the value it has then."""
        held = []
        for k in [k for k in self.ramps if k[0] == key and (index is None or k[1] == index)]:
            sch = self.ramps[k]
            if not any(x.p1 > s0 for x in sch.r):
                continue
            gen = self._chain_at(node, s0).gen if k[1] >= 0 else 0
            v = sch.hold(s0, gen)
            held.append(f"{'volume' if k[1] < 0 else f'fx[{k[1]}].'}{'' if k[1] < 0 else k[2]} holds {v:g}")
        when = 'now' if at in (None, 'now', 'asap') else fmt_bar(self.beat(s0), self.bpb)
        if not held:
            return f"{key[key.index(':') + 1:]}: nothing scheduled to clear from {when}"
        return f"{key[key.index(':') + 1:]}: cleared the scheduled moves from {when}: " + '; '.join(held)

    # ------------------------------------------------------------------ decks
    def _deck(self, name, create=False):
        if name not in self.decks:
            if not create:
                raise LiveError(f"no deck {name!r}; decks: {list(self.decks) or 'none'} (live_deck or live_load "
                                f"creates one)")
            self.decks[name] = D.Deck(name)
        return self.decks[name]

    def _deck_ramp(self, deck, k, v, s0, ramp_beats, v0=None):
        key = ('deck:' + deck, -1, k)
        sch = self.ramps.get(key)
        if sch is None:
            sch = self.ramps[key] = G.Schedule()
            held = self.decks[deck].values[k]
            sch.add(G.Ramp(self.pos, held, self.pos + 1, held, False))      # holds until the first move starts
        cur = sch.value(s0)
        s1 = s0 + max(int(float(ramp_beats) * self.spb * SR), int(G.MIN_RAMP_S * SR))
        sch.add(G.Ramp(s0, cur if v0 is None else v0, s1, v, False))
        self.decks[deck].values[k] = v
        return cur

    def cmd_deck(self, deck, volume_db=None, low_db=None, mid_db=None, high_db=None, filter=None, transpose=None,
                 cue=None, ramp_beats=0, at='now', remove=False):
        with self.lock:
            if remove:
                self._deck(deck)
                names = [k for k, t in self.tracks.items() if t['deck'] == deck]
                for k in names:
                    self.tl.claim(k, self.beat(self.pos))
                    for s in self.active + [p[2] for p in self.pending]:
                        if s.track == k:
                            s.dead = True
                    del self.tracks[k]
                for b in [b for b, v in self.buses.items() if v['deck'] == deck]:
                    del self.buses[b]
                del self.decks[deck]
                for k in [k for k in self.ramps if k[0] == 'deck:' + deck]:
                    del self.ramps[k]
                self._refresh_graph()
                return f"removed deck {deck} ({len(names)} tracks)"
            new = deck not in self.decks
            dk = self._deck(deck, create=True)
            s0 = self._at_sample(at)
            when = 'now' if at in (None, 'now', 'asap') else fmt_bar(self.beat(s0), self.bpb)
            msg = [f"new deck {deck}"] if new else []
            for k, v, lo, hi in (('volume_db', volume_db, -120, 6), ('low_db', low_db, -120, 6),
                                 ('mid_db', mid_db, -120, 6), ('high_db', high_db, -120, 6), ('filter', filter, -1, 1)):
                if v is None:
                    continue
                if not lo <= float(v) <= hi:
                    raise LiveError(f"{k}={v}: between {lo} and {hi} (eq {D.KILL_DB:g} or less kills the band; "
                                    f"fader {D.SILENT_DB:g} or less is off)")
                cur = self._deck_ramp(deck, k, float(v), s0, ramp_beats)
                msg.append(f"{k} {cur:g} -> {float(v):g}" + (f" over {float(ramp_beats):g} beats" if ramp_beats else ''))
            if transpose is not None:
                tp = int(transpose)
                if not -24 <= tp <= 24:
                    raise LiveError("transpose: -24 to +24 semitones")
                beat = self.beat(s0)
                dk.tp = [x for x in getattr(dk, 'tp', []) if x[0] < beat - EPS] + [(beat, tp)]
                dk.transpose = tp
                for k, t in self.tracks.items():
                    if t['deck'] == deck:
                        self._rerender_from(k, beat)
                msg.append(f"transpose {tp:+d} (drums stay)")
            if cue is not None:
                dk.held = None

                def flip(dk=dk, c=bool(cue)):
                    dk.cue = c
                self._swap_at(s0 + G.LAT_BUDGET, flip)              # deck audio runs the path budget behind
                msg.append('cued (off air)' if cue else 'on air')
            return f"deck {deck} ({when}): " + (', '.join(msg) or 'unchanged') + \
                f"\n  {dk.describe(False, cue=None if cue is None else bool(cue))}"

    def _deck_automation(self, info, start, loop):
        """A loaded song's automation as ramps from `start` (house beats): fx params on the effect's schedule, track
        volume as a dB offset on the fader, both shaped like the studio render (linear, log for frequencies, held
        before and after). A looping deck repeats it every pass for the next 10 minutes. -> what could not come."""
        b0, b1 = info['beats']
        span = b1 - b0
        passes = max(1, math.ceil(600 / (span * self.spb))) if loop else 1
        bad = []
        for target, lanes in info.get('automation', {}).items():
            kind, name = target.split(':', 1)
            node = (self.tracks if kind == 'track' else self.buses).get(name)
            if node is None:
                continue
            for key, pts in lanes.items():
                if key.startswith('inst.'):
                    continue                           # rendered with the notes (inst_auto), not a ramp
                if key == 'volume_db':
                    idx, param = -1, 'volume_db'
                else:
                    try:
                        _, i, param = key.split('.', 2)
                        idx = int(i)
                        f = node['path'].chain.fx[idx]
                    except (ValueError, IndexError):
                        bad.append(f"{name} {key} (no such effect)")
                        continue
                    if param not in F.AUTOMATABLE.get(f['type'], ()):
                        bad.append(f"{name} {key} (not automatable live)")
                        continue
                pts = sorted((float(p[0]), float(p[1])) for p in pts)
                beats = np.array([p[0] for p in pts])
                vals = np.array([p[1] for p in pts])
                log = any(k in param for k in G.LOG_PARAMS) and bool(np.all(vals > 0))

                def at(b):
                    return float(np.exp(np.interp(b, beats, np.log(vals))) if log else np.interp(b, beats, vals))
                inner = [b for b in beats if b0 < b < b1]
                knots = [(0.0, at(b0))] + [(b - b0, at(b)) for b in inner] + [(span, at(b1))]
                sch = self.ramps[(target, idx, param)] = G.Schedule()
                for p in range(passes):
                    base = start + p * span
                    for (x0, v0), (x1, v1) in zip(knots, knots[1:]):
                        s0, s1 = self.sample(base + x0), self.sample(base + x1)
                        if s1 > s0:
                            sch.add(G.Ramp(s0, v0, s1, v1, log, node['path'].chain.gen if idx >= 0 else 0))
                if idx >= 0:
                    node['path'].chain.fx[idx][param] = knots[0][1]
        return bad

    def cmd_load(self, deck, song, bars=None, at='next_bar', loop=True, cue=None, performers=None):
        try:
            info, buses, tracks, clips, skipped = D.read_song(song, deck, bars, self.bpb, performers or ())
            for t in tracks:                   # performer voices, found by live_load in the op process
                if t['track'].split('.', 1)[1] in (performers or ()):
                    t['instrument'] = dict(t['instrument'], performer=True)
        except D.DeckError as e:
            raise LiveError(str(e))
        if not clips:
            raise LiveError(f"{info['name']} has no notes in bars {info['bars']}")
        with self.lock:
            now = self.beat(self.pos)
            if deck in self.decks and any(t['deck'] == deck for t in self.tracks.values()):
                if not self.decks[deck].cue and any(self.tl.playing(k, now) for k, t in self.tracks.items()
                                                    if t['deck'] == deck):
                    raise LiveError(f"deck {deck} is on air and playing; load into another deck, or take this one "
                                    f"off air first (live_transition away from it, or live_deck(cue=True))")
                self.cmd_deck(deck, remove=True)
            dk = self._deck(deck, create=True)
            others = [d for n, d in self.decks.items() if n != deck and (not d.cue or d.held)]
            dk.cue = bool(others) if cue is None else bool(cue)
            dk.song = info
        for name, fxs, vol in buses:
            self.cmd_bus(name, fx=fxs, volume_db=min(6.0, vol), deck=deck)
        for t in tracks:
            self.cmd_track(t['track'], instrument=t['instrument'], volume_db=t['volume_db'], pan=t['pan'],
                           fx=t['fx'] or None, sends=t['sends'] or None, deck=deck, output=t['output'],
                           root=t['root'], warm=False)
        master = None
        if info.get('master_fx'):
            try:
                master = self._chain(info['master_fx'], f"deck {deck}'s master", 1.0)
            except LiveError as e:
                skipped['master chain'] = [str(e)]
        with self.lock:
            dk.master = master
        with self.lock:
            b0 = info['beats'][0]
            lane_note, deck_expr = [], {}
            for target, lanes in info.get('automation', {}).items():
                tr = self.tracks.get(target.split(':', 1)[1]) if target.startswith('track:') else None
                if tr is None or not tr['inst']:
                    continue
                pts_of = lambda pts: sorted((float(b) - b0, float(v)) for b, v in pts)  # noqa: E731
                il = {k[5:]: pts_of(p) for k, p in lanes.items() if k.startswith('inst.') and not k.startswith('inst.lane.')}
                ll = {k[10:]: pts_of(p) for k, p in lanes.items() if k.startswith('inst.lane.')}
                if tr['inst'].get('performer'):
                    # a performer's studio lanes (automation inst.lane.<name>) are its live clip's expr lanes
                    if ll:
                        deck_expr[target.split(':', 1)[1]] = {k: [list(x) for x in v] for k, v in ll.items()}
                    if il:
                        lane_note.append(f"{target.split(':', 1)[1]} {', '.join(il)} (a performer takes lanes)")
                else:
                    if ll:
                        lane_note.append(f"{target.split(':', 1)[1]} lanes {', '.join(ll)} (not a performer voice)")
                    if il:
                        tr['inst'] = dict(tr['inst'], _whole=True)
                        tr['inst_auto'] = il
            for t in tracks:                               # one warm-up per distinct instrument, not per track
                self.tracks[t['track']]['warming'] = 0
            seen = {}
            for t in tracks:
                k = json.dumps(self.tracks[t['track']]['inst'], sort_keys=True) + (t['root'] or '')
                if k not in seen:
                    seen[k] = t['track']
                    self._warm(t['track'])
            # every clip starts on the same bar: the latest any of them needs
            now = self.beat(self.pos)
            need, extra = 0.0, 0.0
            for cspec in clips:
                inst = self.tracks[cspec['track']]['inst']
                evs = self._events_of(self._clip_notes(cspec['notes']), inst)
                need = max(need, self._lead_beats(cspec['track'], evs, extra))
                extra += sum(self._event_s(cspec['track'], dd) for _, dd in evs[:32] if dd is not None)
            try:
                beat, note = self.tl.resolve_at(at, now, now + need)
            except QueueError as e:
                raise LiveError(str(e))
        bar = beat / self.bpb + 1
        out = self.cmd_queue([dict(c, loop=None if loop else 1, at=f'bar:{bar:g}', beat0=info['beats'][0],
                                   **({'expr': deck_expr[c['track']]} if c['track'] in deck_expr else {}))
                              for c in clips])
        with self.lock:
            auto_note = self._deck_automation(info, beat, loop)
            if not dk.cue:
                dk.cue, dk.held = True, (beat, beat, fmt_bar(beat, self.bpb))
        auto_note = auto_note + lane_note
        if auto_note:
            skipped.setdefault('automation', [])
            skipped['automation'] = skipped['automation'] + auto_note
        sk = [f"{k}: {v if isinstance(v, int) else ', '.join(v)}" for k, v in skipped.items() if v]
        head = (f"deck {deck}: loaded {info['name']} bars {info['bars'][0]}-{info['bars'][1]} "
                f"({info['bars'][1] - info['bars'][0] + 1} bars, {len(tracks)} tracks, {len(buses)} buses), starts "
                f"{fmt_bar(beat, self.bpb)}, {'loops' if loop else 'plays once'}, "
                f"{'ON AIR once its first bar is rendered (until then held cued; it says so if it slips)' if dk.held else 'CUED: off air, live_listen(deck=...) hears it'}")
        if info.get('bpm') and abs(float(info['bpm']) - self.bpm) > 0.01:
            head += f"\n  song tempo {info['bpm']:g} BPM plays at the house {self.bpm:g} (re-rendered, not stretched)"
        if note:
            head += f"\n  note: {note}" + ("; the deck stays off air until a whole bar is rendered, then goes on air on "
                                         "that bar line" if dk.held else '')
        if sk:
            head += "\n  not live: " + '; '.join(sk)
        return head + '\n' + '\n'.join(ln for ln in out.splitlines() if ln.startswith('runway'))

    def cmd_transition(self, to, from_deck=None, at='next_8', bars=16, style='blend', stop_from=True):
        with self.lock:
            now = self.beat(self.pos)
            self._deck(to)
            if from_deck is None:
                live = [n for n, d in self.decks.items() if n != to and (not d.cue or d.held) and
                        any(self.tl.playing(k, now) for k, t in self.tracks.items() if t['deck'] == n)]
                if len(live) != 1:
                    raise LiveError(f"from_deck: {'no other deck is on air' if not live else f'several decks are on air {live}'}"
                                    f"; name the deck to leave")
                from_deck = live[0]
            self._deck(from_deck)
            if from_deck == to:
                raise LiveError("to and from_deck are the same deck")
            to_tracks = [k for k, t in self.tracks.items() if t['deck'] == to]
            starts = [c.start for k in to_tracks for c in self.tl.track_clips(k)]
            if not starts:
                raise LiveError(f"deck {to} has nothing queued: live_load a song onto it or live_queue its tracks "
                                f"first (cued)")
            ready = max(now, min(starts))       # counted from when the incoming deck is playing
            try:
                beat, _ = self.tl.resolve_at(at, ready, ready)
            except QueueError as e:
                raise LiveError(str(e))
            L = int(bars) * self.bpb
            if not any(self.tl.playing(k, beat) for k in to_tracks):
                raise LiveError(f"deck {to} plays nothing at {fmt_bar(beat, self.bpb)}: live_load a song onto it or "
                                f"live_queue its tracks first, starting by then")
            try:
                steps = D.plan(style, int(bars), self.bpb)
            except D.DeckError as e:
                raise LiveError(str(e))
            names = {'to': to, 'from': from_deck}
            lines = [f"{style} from deck {from_deck} to deck {to}: {fmt_bar(beat, self.bpb)} to "
                     f"{fmt_bar(beat + L, self.bpb)} ({bars} bars)"]
            for who, k, v0, v1, st, dur in steps:
                s0 = self.sample(beat + st)
                self._deck_ramp(names[who], k, v1, s0, dur, v0)
                lines.append(f"  {fmt_bar(beat + st, self.bpb)}: deck {names[who]} {k} "
                             + (f"{v0:g} -> " if v0 is not None else '') + f"{v1:g}"
                             + (f" over {dur:g} beats" if dur else ''))
            dk = self.decks[to]
            dk.held = None

            def on_air(dk=dk):
                dk.cue = False
            self._swap_at(self.sample(beat) + G.LAT_BUDGET, on_air)
            lines.insert(1, f"  {fmt_bar(beat, self.bpb)}: deck {to} goes on air")
        if stop_from:
            stops = [{'track': k, 'stop': True, 'at': f'bar:{(beat + L) / self.bpb + 1:g}'}
                     for k, t in self.tracks.items() if t['deck'] == from_deck]
            if stops:
                self.cmd_queue(stops)
                lines.append(f"  {fmt_bar(beat + L, self.bpb)}: deck {from_deck}'s tracks stop (tails ring out) "
                             f"and it goes off air (cued): load the next song onto it")
            with self.lock:
                old = self.decks[from_deck]

                def off_air(dk=old):
                    dk.cue = True
                self._swap_at(self.sample(beat + L) + G.LAT_BUDGET, off_air)
        return '\n'.join(lines)

    def _event_s(self, track, dur_beats):
        """Estimated render seconds for one event of `dur_beats` on `track` (cost grows with the audio length)."""
        tr = self.tracks[track]
        return self._rate(tr) * (dur_beats * self.spb + _tail_s(tr['inst']))

    @staticmethod
    def _rate(tr):
        if tr['est'] is not None:
            return tr['est']
        return DEFAULT_RATE.get(tr['inst'].get('type'), DRUM_RATE)

    def _lead_beats(self, track, events, extra_s=0.0, queued=None):
        """Beats a new clip on `track` must start after now so that each event ((onset, dur) in beats from the clip
        start, sorted) is rendered before it sounds; events render in onset order across the workers, behind the
        render seconds already queued (extra_s: earlier clips of the same batch; queued: the jobs' sum, if known)."""
        tr = self.tracks[track]
        w = max(1, self.n_workers)
        if queued is None:
            queued = sum(j['est_s'] for j in self.jobs.values())                 # warm-ups occupy workers too
        queued += extra_s
        warm = 1.0 if tr['warming'] else 0.0                                # compile time not in est_s
        need, acc = 0.0, 0.0
        for on, d in events[:64]:
            if d is None:                 # a repeat of an earlier event: rendered once, costs nothing more
                continue
            acc += self._event_s(track, d)
            done_s = MARGIN_S + warm + (queued + acc) / w
            need = max(need, done_s / self.spb - on)
        return need

    def _clip_notes(self, text):
        """A clip's note text -> [(start, midi, dur, vel)], each start moved by its @offset as the studio does. A note
        nudged before the clip's first beat starts on it: begin the clip a beat earlier to keep its lead-in."""
        return [(max(0.0, s), m, d, v) for s, m, d, v in with_offsets(parse_notes(text, offsets=True), self.bpm)]

    def _events_of(self, notes, inst):
        """(onset, duration) in beats of each render event (a note, or a mono phrase); duration None for an event
        identical to an earlier one (same pitches, lengths and velocities), which renders only once."""
        notes = sorted(notes)
        if inst.get('performer') and not inst.get('_whole'):
            end = max(s + d for s, _, d, _ in notes) if notes else 0.0
            pre = CHUNK_PRE_S / self.spb
            return [(sp.on, sp.end - sp.on + pre) for sp in
                    _chunk_spans(notes, math.ceil(end / self.bpb) * self.bpb, self.bpb, CHUNK_REACH_S / self.spb)]
        out, seen = [], set()
        for g in _mono_groups(notes, inst):
            t0 = notes[g[0]][0]
            sig = tuple((round(notes[j][0] - t0, 4), notes[j][1], round(notes[j][2], 4), notes[j][3]) for j in g)
            d = max(notes[j][0] + notes[j][2] for j in g) - t0
            out.append((t0, None if sig in seen else d))
            seen.add(sig)
        return out

    def _parse_clip(self, i, it):
        if not isinstance(it, dict) or 'track' not in it:
            raise LiveError(f"clip [{i}]: each item is a dict with 'track' and 'notes' and/or 'lanes' (or 'stop': true)")
        track = it['track']
        if track not in self.tracks:
            raise LiveError(f"clip [{i}]: no live track {track!r}; create it with live_track(project, track="
                            f"'{track}', instrument=...). Live tracks: {list(self.tracks) or 'none'}")
        known = {'track', 'notes', 'lanes', 'step', 'bars', 'beats', 'loop', 'at', 'stop', 'expr', 'beat0', 'context'}
        extra = set(it) - known
        if extra:
            raise LiveError(f"clip [{i}]: unknown keys {sorted(extra)}; valid: {sorted(known)}")
        if it.get('stop'):
            return track, None, None, None, None
        expr = None
        if it.get('expr'):
            if not self.tracks[track]['inst'] or not self.tracks[track]['inst'].get('performer'):
                raise LiveError(f"clip [{i}]: expr lanes drive performer voices (a voice module with perform()); "
                                f"track {track!r} is not one. For effect params use live_fx ramps")
            try:
                expr = {str(k): sorted((float(b), float(v)) for b, v in pts) for k, pts in it['expr'].items()}
                assert all(expr.values())
            except (TypeError, ValueError, AssertionError, AttributeError):
                raise LiveError(f"clip [{i}]: expr is {{lane: [[beat, value], ...]}} with beats from the clip start, "
                                f"e.g. {{'bend': [[0, 0], [1.5, 2], [2, 0]]}}")
        notes = []
        try:
            if it.get('notes'):
                notes += self._clip_notes(it['notes'])
            span = max((s + d for s, _, d, _ in notes), default=0.0)
            for pitch, pat in (it.get('lanes') or {}).items():
                m = pitch_to_midi(pitch)
                hits, length = parse_steps(pat, float(it.get('step', 0.25)))
                notes += [(s, m, d, v) for s, d, v in hits]
                span = max(span, length)
        except NotationError as e:
            raise LiveError(f"clip [{i}]: {e}")
        if not notes:
            raise LiveError(f"clip [{i}]: no notes. Give notes='<beat> <pitch> <dur> [vel]; ...' or lanes={{'C1': "
                            f"'x...x...'}}, or 'stop': true to silence the track")
        if it.get('beats') is not None:
            length = float(it['beats'])
        elif it.get('bars') is not None:
            length = float(it['bars']) * self.bpb
        else:
            length = max(1, math.ceil(span / self.bpb - 1e-9)) * self.bpb
        late = [s for s, _, _, _ in notes if s >= length - EPS]
        if late:
            raise LiveError(f"clip [{i}]: notes start at beat {fmt_num(max(late))} but the clip is {fmt_num(length)} "
                            f"beats long; raise bars/beats or drop those notes")
        loop = it.get('loop')
        if loop in (None, 'forever', 0):
            loop = None
        else:
            try:
                loop = int(loop)
                assert loop >= 1
            except (ValueError, AssertionError):
                raise LiveError(f"clip [{i}]: loop={it.get('loop')!r}; use a count >= 1 or 'forever'")
        return track, notes, length, loop, expr

    def cmd_queue(self, clips):
        if not isinstance(clips, list) or not clips:
            raise LiveError("clips: a list of {track, notes | lanes, bars, loop, at} (or {track, stop: true, at})")
        parsed = [self._parse_clip(i, it) + (it.get('at') or 'next_bar', float(it.get('beat0') or 0.0),
                                             [tuple(float(x) for x in n) for n in it.get('context') or []])
                  for i, it in enumerate(clips)]
        # the slow part of planning a batch (render estimates, event groups) runs before the engine lock is taken:
        # the mixer takes that lock every block, and a 4000-note batch once held it for 150 ms
        plans = []
        for track, notes, length, loop, expr, at, beat0, context in parsed:
            inst = self.tracks[track]['inst']
            if not notes:
                plans.append(([], [], None))
                continue
            evs = self._events_of(notes, inst)
            shape = types.SimpleNamespace(notes=sorted(notes), length=length,
                                          context=[n for n in context if n[0] < 0])
            plans.append((evs, [self._event_s(track, d) for _, d in evs if d is not None], self._groups(shape, inst)))
        with self.lock:
            now = self.beat(self.pos)
            queued = sum(j['est_s'] for j in self.jobs.values())
            tl = self.tl.copy()
            claims, lines, added = [], [], []
            extra = 0.0                      # render seconds of earlier clips in this batch
            batch_ids = {}                   # batch index -> clip id, for at='after:#<index>'
            for i, (track, notes, length, loop, expr, at, beat0, context) in enumerate(parsed):
                if at.startswith('after:#'):
                    try:
                        k = int(at[7:])
                    except ValueError:
                        raise LiveError(f"clip [{i}] at={at!r}: after:#<index of an earlier clip in this batch>")
                    if k not in batch_ids:
                        raise LiveError(f"clip [{i}] at={at!r}: item [{k}] is not an earlier clip of this batch "
                                        f"(stops have no end). Nothing was queued.")
                    at = 'after:' + batch_ids[k]
                evs, costs, _ = plans[i]
                ready = now + self._lead_beats(track, evs, extra, queued)
                extra += sum(costs)
                try:
                    beat, note = tl.resolve_at(at, now, ready if notes else now)
                except QueueError as e:
                    raise LiveError(f"clip [{i}] ({track}): {e}. Nothing was queued.")
                removed, cut = tl.claim(track, beat)
                claims.append((track, beat, set(removed) | set(cut)))
                what = []
                if cut:
                    what.append(f"cuts {', '.join(cut)} there")
                if removed:
                    what.append(f"replaces queued {', '.join(removed)}")
                if notes is None:
                    tl.stop(track, beat)
                    lines.append(f"stop {track} at {fmt_bar(beat, self.bpb)}" + (f"; {'; '.join(what)}" if what else '')
                                 + (f"\n  note: {note}" if note else ''))
                    continue
                c = tl.add(track, notes, length, loop, beat, at)
                c._groups = plans[i][2]
                c.expr = expr
                c.beat0 = beat0
                c.context = [(s, int(m), d, int(v)) for s, m, d, v in context if s < 0]
                batch_ids[i] = c.id
                added.append(c)
                bars = length / self.bpb
                lines.append(f"{c.id} {track}: {fmt_bar(beat, self.bpb)} ({at}), {fmt_num(bars)} bars x "
                             f"{'forever' if loop is None else loop}, {len(notes)} notes"
                             + (f"; ends {fmt_bar(c.end, self.bpb)}" if c.end is not None else '')
                             + (f"; {'; '.join(what)}" if what else '') + (f"\n  note: {note}" if note else ''))
            self.tl = tl
            for track, beat, ids in claims:
                for s in self.active + [p[2] for p in self.pending]:
                    if s.cid in ids and s.on >= beat - EPS:
                        s.dead = True
            for c in added:
                g = c.__dict__.pop('_groups', None)
                self.meta[c.id] = {'groups': g if g is not None else self._groups(c, self.tracks[c.track]['inst']),
                                   'placed': None}
            for cid in list(self.meta):
                if cid not in self.tl.clips:
                    self.meta.pop(cid)
        self.tick()
        return '\n'.join(lines) + '\n' + self._runway()

    def cmd_cancel(self, clips):
        out = []
        with self.lock:
            now = self.beat(self.pos)
            for cid in clips:
                try:
                    c = self.tl.cancel(cid, now)
                except QueueError as e:
                    raise LiveError(str(e))
                self.meta.pop(cid, None)
                for s in [p[2] for p in self.pending]:
                    if s.cid == cid:
                        s.dead = True
                out.append(f"cancelled {cid} ({c.track}, was due {fmt_bar(c.start, self.bpb)})")
        return '\n'.join(out) + '\n' + self._runway()

    def _runway(self):
        now = self.beat(self.pos)
        last = self.tl.last_change(now)
        loops = []
        for t in self.tracks:
            cs = self.tl.track_clips(t)
            if cs and cs[-1].end is None:
                loops.append(f"{t} {cs[-1].id}")
        tail = f"after that: {', '.join(loops)} loop forever" if loops else "after that: silence"
        if last is None:
            return f"runway: nothing scheduled to change; {tail}"
        return f"runway: last scheduled change at {fmt_bar(last, self.bpb)} (in {(last - now) * self.spb:.1f} s); {tail}"

    @staticmethod
    def _held_db(node):
        return 10 * math.log10(max(node.get('hold', {}).values(), default=node['ms']) + 1e-12)

    def _stalled(self):
        """A device that stopped asking for audio: the playhead freezes while everything else looks fine."""
        if getattr(self, 'stream', None) is None or self.last_pull is None or not self.running:
            return None
        quiet = time.time() - self.last_pull
        if quiet < STALL_S:
            return None
        return (f"STALLED: the audio device has asked for no audio for {quiet:.0f} s, so nothing is heard and the "
                f"playhead is frozen (a Bluetooth speaker asleep, or another program or engine holding the device). "
                f"live_stop, then live_start again (another device: device='<name>')")

    def _lost_line(self, track, beats):
        t = self.tracks.get(track)
        speed = f"; it renders {1 / max(t['est'], 1e-3):.1f}x realtime" if t and t.get('est') else ''
        return (f"track {track}: {len(beats)} notes from {fmt_bar(min(beats), self.bpb)} never sounded: their "
                f"renders came back too late{speed}. Queue or load further ahead, or lighten the track (fewer studio-"
                f"only effects, shorter notes)")

    def _second(self, p1, tracks):
        """Once a second of mix: its level and how many tracks sound (for THIN), and whether the runway is out."""
        e, n = self._lvl
        self._lvl = [0.0, 0]
        db = 10 * math.log10(e / (2 * n) + 1e-20)
        on = [k for k, t in tracks.items() if t.get('ms', 0.0) > THIN_TRACK_MS]
        self.levels.append((p1, db, len(on), on[0] if on else ''))
        with self.lock:                                  # the timeline changes under the lock (queue, cancel)
            out = self.tl.last_change(self.beat(p1)) is None
        if out:
            if self.runway_out is None:
                self.runway_out = p1
        else:
            self.runway_out = None

    def _runway_ended(self):
        """A line when a set that has played has had nothing new scheduled for RUNWAY_ENDED_BARS (a runway ran out
        and one hat loop played on for 10 minutes; silence checks never fire on that)."""
        if self.runway_out is None or self.sounded is None:
            return ''
        bars = (self.pos - self.runway_out) / SR / (self.spb * self.bpb)
        if bars < RUNWAY_ENDED_BARS:
            return ''
        loops = [t for t in self.tracks if (cs := self.tl.track_clips(t)) and cs[-1].end is None]
        what = f"{', '.join(loops[:4])}{' ...' if len(loops) > 4 else ''} loop on unchanged" if loops else "what is left rings out"
        return (f"RUNWAY ENDED {bars:.0f} bars ago ({fmt_bar(self.beat(self.runway_out), self.bpb)}): nothing new is "
                f"queued and {what}. Queue the next section, or an ending")

    def _thin(self):
        """A line when the mix has thinned out against the set so far: one track left where several played, or the
        level THIN_DB under the set's usual level, for THIN_S seconds (silence is SILENT ON AIR's)."""
        hist = [x for x in self.levels if x[1] > 20 * math.log10(SOUND_FLOOR)]
        if len(hist) < THIN_HISTORY_S + THIN_S:
            return ''
        # the set at its fuller moments, before the window being judged (a long thin stretch must not become the
        # set's normal)
        base = hist[:-THIN_S]
        med_db = float(np.percentile([x[1] for x in base], 75))
        med_n = float(np.percentile([x[2] for x in base], 75))

        def thin(x):
            return x[1] > 20 * math.log10(SOUND_FLOOR) and ((x[2] <= 1 and med_n >= 3) or x[1] < med_db - THIN_DB)
        run = 0
        for x in reversed(self.levels):
            if not thin(x):
                break
            run += 1
        if run < THIN_S:
            return ''
        x = self.levels[-1]
        why = (f"only {x[3] or 'one track'} sounding (the set has had {med_n:.0f} at its fuller moments)" if x[2] <= 1 and med_n >= 3
               else f"the mix {med_db - x[1]:.0f} dB under the set's usual level")
        return f"THIN for {run} s: {why}. An outro left running? Bring the set back or end it"

    def _silent_on_air(self):
        """A line when the set has played and then nothing has sounded for SILENT_ON_AIR_S (a crashed helper left
        a set silent for 9 minutes and nothing said so)."""
        if self.sounded is None or self.pos - self.sounded < SILENT_ON_AIR_S * SR:
            return ''
        s = (self.pos - self.sounded) / SR
        return (f"SILENT ON AIR for {s:.0f} s: nothing has sounded since {fmt_bar(self.beat(self.sounded), self.bpb)}. "
                f"Queue or load something (a deck that finished a transition is off air: load onto it), or live_stop")

    def drain_news(self):
        """Problems that arrived since the last reply, said once."""
        with self.lock:                                 # the mixer and scheduler write these
            out = [self._lost_line(track, beats) for track, beats in self._lost_since.items()]
            self._lost_since.clear()
        stall = self._stalled()
        if stall and not getattr(self, '_stall_said', False):
            out.append(stall)
        self._stall_said = bool(stall)
        while self.news:
            out.append(self.news.popleft())
        return out

    # ------------------------------------------------------------------ streams and controls
    def _stream_ok(self, name):
        if name == 'master':
            return True
        if name.startswith('bus:') and name[4:] in self.buses:
            return True
        if name.startswith('deck:') and name[5:] in self.decks:
            return True
        raise LiveError(f"no {name!r} to stream; streams: 'master', " + ', '.join(
            [f"'bus:{b}'" for b in self.buses] + [f"'deck:{d}'" for d in self.decks]))

    def cmd_stream(self, name='master'):
        self._stream_ok(name)
        return {'path': f"/stream?name={name}", 'rate': SR, **O.FORMAT, 'listening': self.hub.names().get(name, 0)}

    def cmd_map(self, control, target=None, param='volume_db', range=None, curve='linear', at='now', remove=False):
        if remove:
            if self.controls.maps.pop(control, None) is None:
                raise LiveError(f"no control {control!r}; mapped: {sorted(self.controls.maps) or 'none'}")
            return f"{control}: unmapped"
        if not target:
            raise LiveError("target: the track, 'bus:<name>' or 'deck:<name>' the control drives")
        try:
            kind, name, _, _ = O.parse_param(target, param)
            m = self.controls.set(control, target, param, range, curve, at)
        except ValueError as e:
            raise LiveError(str(e))
        with self.lock:
            if kind == 'track':
                self._target(name)
            elif kind == 'bus' and name not in self.buses:
                raise LiveError(f"no bus {name!r}; buses: {list(self.buses) or 'none'}")
            elif kind == 'deck':
                self._deck(name)
        return f"{control} -> {O.describe(m)}: its moves apply in the engine at once and are logged by bar"

    def cmd_control(self, control, value):
        """A control moved (a knob turned on the stage): apply its mapping now and log it by bar."""
        m = self.controls.maps.get(control)
        if m is None:
            raise LiveError(f"no control {control!r}; live_map it first. Mapped: {sorted(self.controls.maps) or 'none'}")
        x = O.scale(value, m['range'], m['curve'])
        kind, name, what, detail = m['spec']
        if what == 'volume_db':
            x = min(6.0, x)
        if kind == 'deck':
            self.cmd_deck(name, **{what: x}, at=m['at'])
        elif what == 'fx':
            self.cmd_fx(name if kind == 'track' else 'bus:' + name, detail[0], {detail[1]: x}, at=m['at'])
        elif kind == 'bus':
            self.cmd_bus(name, volume_db=x)
        else:
            self.cmd_track(name, **{what: x})
        # the bar being heard; an engine with no output running (tests, a harness) has only its mixed position
        beat = self.beat(self.played if (self.stream is not None or self._null_on) else self.pos)
        self.controls.log({'t': round(time.time(), 3), 'beat': round(beat, 4), 'control': control,
                           'value': float(value), 'target': m['target'], 'param': m['param'], 'applied': x})
        return {'applied': x, 'bar': fmt_bar(beat, self.bpb)}

    def cmd_controls(self, control=None, last=20):
        """What the controls did, as automation: per control and target, [bar, value] points in this engine's bars."""
        rec = self.controls.read(control)
        if not rec:
            return "no control moves logged" + (f" for {control!r}" if control else '') + \
                (f"; mapped: {sorted(self.controls.maps)}" if self.controls.maps else '; live_map one first')
        out = collections.OrderedDict()
        for r in rec:
            out.setdefault((r['control'], r['target'], r['param']), []).append(
                [round(r['beat'] / self.bpb + 1, 4), round(r['applied'], 4)])
        L = []
        for (c, t, p), pts in out.items():
            L.append(f"{c} -> {t} {p}: {len(pts)} moves, bars {pts[0][0]:g}-{pts[-1][0]:g}")
            L.append(f"  automation points [bar, value]: {json.dumps(pts[-int(last):])}")
        return '\n'.join(L)

    def cmd_onsets(self, ahead_s=12.0, behind_s=2.0, limit=400):
        """The events placed to sound from behind_s ago to ahead_s ahead, one per rendered note group: {'t': track,
        'b': its beat, 'l': its peak in dB}, earliest first, plus the clock. A visualizer plays them against its own
        playback position (ledger:M160 phase 2, Nate 10-06 14:48: "representing every element of the song to the
        rhythm ... synchronized with what's playing now on the stream")."""
        with self.lock:
            lo, hi = self.pos - int(float(behind_s) * SR), self.pos + int(float(ahead_s) * SR)
            segs = [s for s in self.active if not s.dead and lo <= s.start <= hi] + \
                   [q[2] for q in self.pending if not q[2].dead and lo <= q[0] <= hi]
            now = self.beat(self.pos)
        segs.sort(key=lambda s: s.start)
        out = []
        for s in segs[:int(limit)]:
            head = s.y[:, :min(s.y.shape[1], SR // 10)]
            pk = float(np.max(np.abs(head))) if head.size else 0.0
            out.append({'t': s.track, 'b': round(self.beat(s.start), 3), 'l': round(20 * math.log10(pk + 1e-9), 1)})
        return {'beat': round(now, 3), 'bpm': self.bpm, 'bpb': self.bpb, 'onsets': out}

    def cmd_status(self, deck=None):
        if deck is not None and deck not in self.decks:
            raise LiveError(f"no deck {deck!r}; decks: {list(self.decks) or 'none'}")
        with self.lock:
            now = self.beat(self.pos)
            heard = self.beat(self.played)
            lines = [f"live {fmt_num(self.bpm)} BPM {self.bpb}/4 | heard {fmt_bar(math.floor(heard), self.bpb)} "
                     f"({self.played / SR:.0f} s) | mixed ahead {max(0.0, (self.pos - self.played) / SR):.2f} s | "
                     f"output {self.out_name or self.device}" + (" (follows the default)" if self.device == 'default' and self.follow and self.stream is not None else '')
                     + (f" | recording {os.path.basename(self.rec_path)}" if self.rec else '')]
            sn = self.hub.names()
            if sn:
                lines.append("streams: " + ', '.join(f"{k} ({v} listening" + (f", {self.hub.dropped[k]} blocks dropped" if self.hub.dropped[k] else '') + ")" for k, v in sn.items()))
            if self.controls.maps:
                lines.append("controls: " + '; '.join(f"{c} -> {O.describe(m)}" for c, m in self.controls.maps.items()))
            stall = self._stalled()
            if stall:
                lines.append(stall)
            for flag in (self._silent_on_air(), self._runway_ended(), self._thin()):
                if flag:
                    lines.append(flag)
            lines.append("safety: " + self.safety.report())
            for dn, dk in self.decks.items():
                n_tr = sum(1 for t in self.tracks.values() if t['deck'] == dn)
                errs = sum(len(t['errors']) for t in self.tracks.values() if t['deck'] == dn)
                warming = sum(1 for t in self.tracks.values() if t['deck'] == dn and t['warming'])
                now_vals = {k: (lambda v: float(v if np.isscalar(v) else v[0]))(self._deck_param(dn, k, self.pos, 1))
                            for k in D.PARAMS}
                lines.append(f"deck {dn}: {dk.describe(values=now_vals)} | {n_tr} tracks"
                             + (f" | master {dk.master.describe()}" if dk.master is not None else '')
                             + (f", {warming} WARMING" if warming else '')
                             + (f", {errs} ERRORS (live_status(deck='{dn}'))" if errs else ''))
            shown = [(k, t) for k, t in self.tracks.items() if t['deck'] == deck]
            hidden = len(self.tracks) - len(shown)
            lines.append(("tracks" + (f" on deck {deck}:" if deck else ':')) if shown else
                         "tracks: none (live_track to add one)" if not self.tracks else
                         f"tracks: {hidden} on decks (live_status(deck=...) lists them)")
            for name, t in shown:
                lvl = 10 * math.log10(t['ms'] + 1e-12)
                inst = t['inst']
                what = inst['type'] + (f":{inst['voice']}" if inst.get('voice') else '')
                cs = self.tl.track_clips(name)
                play = self.tl.playing(name, now)
                nxt = [c for c in cs if c.start > now + EPS]
                seg = f"playing {play.id}" if play else "silent"
                if play:
                    k = int((now - play.start) // play.length) + 1
                    seg += f" (pass {k}/{'inf' if play.loop is None else play.loop})"
                if nxt:
                    seg += f", next {nxt[0].id} at {fmt_bar(nxt[0].start, self.bpb)}"
                stops = self.tl.stops.get(name)
                if stops:
                    seg += f", stop at {fmt_bar(stops[0], self.bpb)}"
                est = f"renders {1 / max(t['est'], 1e-3):.0f}x realtime" if t['est'] is not None else "no renders yet"
                lines.append(f"  {name:<10} {what:<22} vol {t['volume_db']:+g} pan {t['pan']:+g} "
                             f"level {lvl:6.1f} dBFS (max {HOLD_S} s {self._held_db(t):6.1f}) | {seg} | {est}" +
                             (" | WARMING" if t['warming'] else ''))
                ch = t['path'].chain
                if ch.procs or t['sends'] or t['path'].retiring:
                    fxl = f"      fx {ch.describe()}"
                    if t['sends']:
                        fxl += " | sends " + ', '.join(f"{k} {v:+g} dB" for k, v in t['sends'].items())
                    if t['path'].retiring:
                        fxl += f" | {len(t['path'].retiring)} old chain(s) ringing out"
                    lines.append(fxl)
                    ch.gr = {}
                for e in t['errors']:
                    lines.append(f"      ERROR {e}")
            for name, b in self.buses.items():
                if b['deck'] != deck:
                    continue
                lvl = 10 * math.log10(b['ms'] + 1e-12)
                users = [k for k, t in self.tracks.items() if name in t['sends']]
                lines.append(f"  bus {name:<6} fx {b['path'].chain.describe()} | vol {b['volume_db']:+g} | level "
                             f"{lvl:6.1f} dBFS (max {HOLD_S} s {self._held_db(b):6.1f}) | fed by {', '.join(users) or 'nothing yet'}")
                b['path'].chain.gr = {}
            def ahead(sch):                            # a loaded song's automation can hold hundreds: show two
                fut = [r for r in sch.r if r.p1 > self.pos]
                return ', '.join(f"{r.v1:g} by {fmt_bar(self.beat(r.p1), self.bpb)}" for r in fut[:2]) + \
                    (f" (+{len(fut) - 2} more)" if len(fut) > 2 else '')
            moving = [f"{k[0]} {'' if k[1] < 0 else f'fx[{k[1]}].'}{k[2]} -> " + ahead(sch)
                      for k, sch in self.ramps.items() if sch.target.p1 > self.pos]
            if moving:
                lines.append("ramps: " + '; '.join(moving))
            if self.swaps:
                lines.append("pending fx changes at: " + ', '.join(fmt_bar(self.beat(s[0]), self.bpb) for s in sorted(self.swaps)))
            lines.append(self._runway())
            backlog = sum(1 for j in self.jobs.values() if not j['warm'])
            if self.mix_load is not None:
                lines.append(f"mixer: {self.mix_load * 100:.0f}% of real time (peak {self.mix_peak * 100:.0f}% since the "
                             f"last status; above ~70% risks dropouts: fewer tracks or effects)")
                self.mix_peak = 0.0
            if self.n_workers and len(self.ready) < self.n_workers:
                lines.append(f"workers warming up: {len(self.ready)}/{self.n_workers} ready")
            starving = self._starving()
            if starving:
                lines.append(starving)
            lines.append(f"render: {self.n_workers or 'inline'} workers, backlog {backlog}, late events "
                         f"{self.stats['late']} (never sounded {self.stats['lost']}; dropped unneeded renders "
                         f"{self.stats['skipped']})" + (f" (last: {self.last_late})" if self.last_late else '') +
                         f", rejected {self.stats['rejected']}, underruns {self.stats['underruns']}" +
                         (f" (last at {self.last_underrun})" if self.last_underrun else ''))
            return '\n'.join(lines)

    def _starving(self):
        """STARVING when the render line, earliest-needed first, cannot finish a render before its notes sound:
        the jobs in the workers plus every job queued before it, at the measured render rates, spread over the
        workers (ledger:M156: the DJ sees it before the underruns). '' when the line keeps up."""
        if not self.n_workers or not self._backlog:
            return ''
        w = max(1, self.n_workers)
        t = sum(j['est_s'] for j in self.jobs.values() if j.get('sent') and not j['warm']) / w
        for need, _, jid, _ in sorted(self._backlog):
            job = self.jobs.get(jid)
            if job is None or job['warm']:
                continue
            t += job['est_s'] / w
            to_s = (need - self.pos) / SR
            if t > to_s + MARGIN_S:
                return (f"STARVING: the render line needs about {t:.0f} s of work before "
                        f"{fmt_bar(self.beat(need), self.bpb)} ({job['track']}), which sounds in {max(0.0, to_s):.0f} "
                        f"s: queue less far ahead in one call (stream it), mute a heavy track, or let the machine "
                        f"cool (machine_status)")
        return ''

    def cmd_view(self, bars=8, clip=None):
        with self.lock:
            if clip:
                c = self.tl.clips.get(clip)
                if c is None:
                    raise LiveError(f"no clip {clip!r} (finished clips are forgotten); clips: {sorted(self.tl.clips)}")
                head = (f"{c.id} on {c.track}: starts {fmt_bar(c.start, self.bpb)}, {fmt_num(c.length / self.bpb)} bars x "
                        f"{'forever' if c.loop is None else c.loop}" +
                        (f", ends {fmt_bar(c.end, self.bpb)}" if c.end is not None else '') + f" (at={c.at})")
                return head + '\nnotes (bar = bar within the clip):\n' + format_notes(c.notes, self.bpb)
            now = self.beat(self.pos)
            b0 = int(now // self.bpb) + 1
            n = max(1, min(int(bars), 64))
            lines = [f"bars {b0}-{b0 + n - 1} (now {fmt_bar(math.floor(now), self.bpb)}); a cell = the clip sounding at the bar's "
                     f"downbeat, '.' silent, '|' marks every 4 bars"]
            head = ''.join(f"{b:<5}" + ('| ' if (b % 4 == 0) else '') for b in range(b0, b0 + n))
            lines.append(f"{'':<10} {head}")
            for name in self.tracks:
                cells = []
                for b in range(b0, b0 + n):
                    c = self.tl.playing(name, (b - 1) * self.bpb)
                    cells.append(f"{(c.id if c else '.'):<5}" + ('| ' if (b % 4 == 0) else ''))
                lines.append(f"{name:<10} {''.join(cells)}")
            lines.append(self._runway())
            return '\n'.join(lines)

    def cmd_listen_dump(self, bars=4, deck=None):
        if deck is not None and deck not in self.decks:
            raise LiveError(f"no deck {deck!r}; decks: {list(self.decks) or 'none'}")
        with self.lock:
            air = self.air if deck is None else self.decks[deck].air
            lag = G.LAT_BUDGET + (self.safety.la if deck is None else 0)
            done = int(self.beat(self.pos - lag) // self.bpb)    # bars 1..done are fully written to the air log
            if done < 1:
                raise LiveError("no complete bar has played yet; wait one bar and call again")
            keep = int(air.shape[1] / SR / (self.bpb * self.spb))
            n = max(1, min(int(bars), done, keep, 32))
            first = done - n + 1
            s0 = self.sample((first - 1) * self.bpb)
            s1 = self.sample(done * self.bpb)
            N = air.shape[1]
            idx = np.arange(s0, s1) % N
            y = air[:, idx]
        import soundfile as sf
        os.makedirs(os.path.join(self.root, 'live'), exist_ok=True)
        path = os.path.join(self.root, 'live', 'listen.wav' if deck is None else f'listen_{deck}.wav')
        sf.write(path, y.T, SR)
        return {'path': path, 'first': first, 'last': done, 'bpm': self.bpm, 'bpb': self.bpb}

    def cmd_record(self, on=True):
        import soundfile as sf
        with self.lock:
            if on and self.rec is None:
                d = os.path.join(self.root, 'live')
                os.makedirs(d, exist_ok=True)
                self.rec_path = os.path.join(d, time.strftime('rec_%Y%m%d_%H%M%S.wav'))
                bar = int(self.beat(self.pos) // self.bpb) + 2            # next full bar not yet mixed
                self.rec_start = self.sample((bar - 1) * self.bpb)
                self.rec_n = 0
                with open(self.rec_path[:-4] + '.json', 'w', encoding='utf8') as f:
                    json.dump({'bpm': self.bpm, 'beats_per_bar': self.bpb, 'first_bar': bar}, f)
                self.rec = sf.SoundFile(self.rec_path, 'w', SR, 2, 'PCM_24')
                return (f"recording to {self.rec_path} from bar {bar} (file starts on that downbeat; "
                        f"{os.path.basename(self.rec_path[:-4])}.json has bpm and first_bar for analysis)")
            if not on and self.rec is not None:
                self.rec.close()
                self.rec = None
                return f"stopped recording: {self.rec_path} ({self.rec_n / SR:.1f} s)"
            return "recording already " + ("on: " + self.rec_path if self.rec else "off")

    def cmd_stop(self, fade_s=1.0):
        with self.lock:
            n = max(1, int(float(fade_s) * SR))
            self.fade = (n, n)
        threading.Thread(target=self._finish, daemon=True).start()
        return f"fading out over {fade_s:g} s and stopping"

    def _finish(self):
        # the fade advances only while the device takes audio: a stalled device would never finish it
        deadline = time.time() + (self.fade[1] / SR if self.fade else 1.0) + 3.0
        while self.running and not (self.fade and self.fade[0] == 0) and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(AHEAD_S + 0.3)
        self.shutdown()


def _output_names():
    try:
        import sounddevice as sd
        return sorted({d['name'] for d in sd.query_devices() if d['max_output_channels'] > 0})
    except Exception:
        return []


def registry_path(pid=None):
    """Every running engine leaves a note here (any project folder), so live_start can name the ones still
    holding a device."""
    d = os.environ.get('ISMAIL_LIVE_REGISTRY') or os.path.join(os.path.expanduser('~'), '.ismail', 'live')
    return d if pid is None else os.path.join(d, f"{pid}.json")


def warm_effects():
    """Compile/load every effect kernel once before audio starts: a first numba compile mid-set would hold the GIL
    long enough to starve the device."""
    for t in F.FX_DEFAULTS:
        if t not in F.PROCS:    # studio-only effects bake in the workers
            continue
        spec = {'type': t}
        if t == 'duck':
            spec['every_beats'] = 1
        if t == 'vocoder':
            spec['modulator'] = 'w'
        for extra in ([{}] if t not in ('distortion', 'filter') else
                      [{'mode': m} for m in (('tanh', 'asym', 'bitcrush') if t == 'distortion' else ('lp24', 'ladder'))]):
            p = F.make(F.normalize(dict(spec, **extra)), F.Env(SR, 120.0, 1.0, 0))
            b = F.Block()
            b.pos, b.n = 0, 64
            b.modulator = lambda ref: np.zeros(64)
            p.process(np.zeros((2, 64)), b)


# ------------------------------------------------------------------ control server

def serve(engine, port=0, idle_min=None):
    idle_min = float(os.environ.get('ISMAIL_LIVE_IDLE_MIN', 60)) if idle_min is None else idle_min
    ops = {'status': engine.cmd_status, 'track': engine.cmd_track, 'queue': engine.cmd_queue,
           'bus': engine.cmd_bus, 'fx': engine.cmd_fx, 'deck': engine.cmd_deck, 'load': engine.cmd_load,
           'transition': engine.cmd_transition, 'moves': engine.cmd_moves,
           'cancel': engine.cmd_cancel, 'view': engine.cmd_view, 'listen': engine.cmd_listen_dump,
           'record': engine.cmd_record, 'stop': engine.cmd_stop, 'device': engine.cmd_device,
           'stream': engine.cmd_stream, 'map': engine.cmd_map, 'control': engine.cmd_control,
           'controls': engine.cmd_controls, 'onsets': engine.cmd_onsets}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            # /stream?name=master|bus:<b>|deck:<d>: raw PCM, int16 LE stereo at SR, until the client leaves
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            if u.path != '/stream':
                self.send_error(404, "GET /stream?name=master (or bus:<name>, deck:<name>); everything else is POST")
                return
            name = (parse_qs(u.query).get('name') or ['master'])[0]
            try:
                engine._stream_ok(name)
            except LiveError as e:
                self.send_error(404, str(e))
                return
            sub = engine.hub.subscribe(name)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('X-Sample-Rate', str(SR))
                self.send_header('X-Channels', '2')
                self.send_header('X-Format', 's16le')
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                while engine.running:
                    b = sub.get(1.0)
                    if b:
                        self.wfile.write(b)
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                engine.hub.unsubscribe(name, sub)

        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
                engine.last_cmd = time.time()
                fn = ops.get(req.get('op'))
                if fn is None:
                    res = {'ok': False, 'error': f"unknown op {req.get('op')!r}; ops: {sorted(ops)}"}
                else:
                    res = {'ok': True, 'result': fn(**(req.get('args') or {}))}
            except (LiveError, QueueError) as e:
                res = {'ok': False, 'error': str(e)}
            except TypeError as e:
                res = {'ok': False, 'error': f"bad arguments: {e}"}
            except Exception as e:
                traceback.print_exc()
                res = {'ok': False, 'error': f"engine error {type(e).__name__}: {e}"}
            news = engine.drain_news()
            if news:
                k = 'result' if res['ok'] else 'error'
                res[k] = f"{res[k]}\nNEW since your last call:\n" + '\n'.join('  ' + x for x in news)
            body = json.dumps(res).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def idle():
        while not engine.stopped.is_set():
            if time.time() - engine.last_cmd > idle_min * 60 and engine.fade is None:
                print(f"no commands for {idle_min:g} min: stopping", flush=True)
                engine.cmd_stop(3.0)
            time.sleep(5)
    threading.Thread(target=idle, daemon=True).start()
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--project', required=True)
    ap.add_argument('--bpm', type=float, required=True)
    ap.add_argument('--bpb', type=int, default=4)
    ap.add_argument('--device', default='default')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--port', type=int, default=0)
    ap.add_argument('--no-follow', action='store_true', help="device 'default' stays where it opened")
    a = ap.parse_args()
    import scipy.signal  # noqa: F401  (seconds to import: do it before audio starts, never mid-set)
    from .. import mimic  # noqa: F401  (normalize() imports it for mimic tracks)
    warm_effects()
    eng = Engine(a.project, a.bpm, a.bpb, a.workers, a.device, follow=not a.no_follow)
    httpd = serve(eng, a.port)
    eng.start()
    waited = eng.wait_ready()           # engine.json appears (live_start returns) once the workers can render
    print(f"workers ready after {waited:.1f} s: {eng.ready}", flush=True)
    d = os.path.join(eng.root, 'live')
    os.makedirs(d, exist_ok=True)
    info = os.path.join(d, 'engine.json')
    rec = {'port': httpd.server_address[1], 'pid': os.getpid(), 'bpm': a.bpm, 'bpb': a.bpb,
           'device': a.device, 'started': time.time(), 'project': eng.root}
    with open(info, 'w', encoding='utf8') as f:
        json.dump(rec, f)
    reg = registry_path(os.getpid())
    try:
        os.makedirs(os.path.dirname(reg), exist_ok=True)
        with open(reg, 'w', encoding='utf8') as f:
            json.dump(rec, f)
    except OSError:
        reg = None
    print(f"live engine on 127.0.0.1:{httpd.server_address[1]} ({a.bpm:g} BPM)", flush=True)
    try:
        eng.stopped.wait()
    except KeyboardInterrupt:
        eng.shutdown()
    finally:
        httpd.shutdown()
        for p in (info, reg):
            try:
                if p:
                    os.remove(p)
            except OSError:
                pass
    if getattr(eng, 'device_hung', False):
        print("the audio device did not close; exiting without waiting for it", flush=True)
        os._exit(0)


if __name__ == '__main__':
    main()
