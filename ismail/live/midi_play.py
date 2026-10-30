"""Played keys: a MIDI input (a keyboard, a pad, a loopMIDI port) patched to an instrument in the live engine, so a
person plays along with the set from the same speakers.

The engine mixes about half a second ahead of the device, so a key mixed there would sound that late. Instead each
note of the instrument is rendered once, ahead, on the engine's render workers (C2 to C7, or a kit's pads), and a
key adds its note where the audio goes to the device: it sounds one output buffer after the press. Those notes
skip the track's effects and the safety limiter; a soft clip guards the sum. live_listen hears them; a recording
started with live_record and the streams (the phone) do not yet.

Every key is logged (live/midi/<source>.jsonl) for turning a performance into a part later, on two clocks of the
music heard: 'press', the beat heard at the press, and 'beat', the beat the key sounded against (the press plus the
output buffer). A player playing by ear lines up the note they hear with the beat they hear, so 'beat' is where they
meant it; live_played reads that one (logs from before 'press' existed hold the press in 'beat'). The engine also
watches for inputs plugged in or removed and says so in its news.

One input needs no driver: 'screen keys', the on-screen keyboard page (live_keys_page), for someone with no MIDI
instrument. Its keys arrive as small HTTP messages (the engine's /api/keys, or the phone server's, which relays them)
and are fed to the patch here exactly as a driver's would be.

An input is opened once and shared: by its patch (keys) and by the control maps bound to it (live_map(midi=...)).
Each message goes to the maps first (the engine's midi_control); a message a map is bound to is a control move,
never a key. What an open input sends that nothing takes is kept (heard_lines), so an agent can find a knob's number.
"""
import collections
import json
import os
import re
import threading
import time

import numpy as np

from .. import midi as M
from . import outputs as O
from ..dsp import SR

LO, HI = 36, 96             # the notes rendered for a melodic instrument: C2 to C7
NOTE_S = 2.5                # each note's gate: a key held longer ends with the note's own release
ONESHOT_S = 0.2             # a drum's or a kit pad's gate (note-offs are ignored for those)
MAX_VOICES = 48
POLL_S = 2.0
KNEE = 0.9                  # the keys plus the set above this are soft-clipped (they skip the safety limiter)
SCREEN = 'screen keys'      # the on-screen keys page, an input that is always there (live_keys_page)
MAX_EVENTS = 64             # messages in one request from the page
HIT = 0.15                  # live_played(fit=True): onsets this close (two hands, a rolled chord) are one hit, beats
STEP = 0.25                 # the grid it fits: 16ths


class Voice:
    __slots__ = ('y', 'i', 'g', 'rel', 'left')

    def __init__(self, y, g):
        self.y, self.i, self.g, self.rel, self.left = y, 0, g, None, None

    def release(self, n):
        if self.rel is None:
            self.rel = self.left = max(1, n)


def fit_grid(t, step=STEP, min_span=4.0):
    """The grid of `step` that fits hit times t (beats, ascending) best with a free continuous offset: the offset
    with the least squared distance to the nearest line, then a straight-line drift when the hits span min_span
    beats or more. Returns {'offset': beats from the nearest line at the first hit (-step/2 to step/2), 'drift':
    beats per beat, 't0', 'k': each hit's line number, 'rms': beats}. Rounding raw times to the grid without the
    offset goes wrong when the take sits near half a step off (a played take, 2026-10-13)."""
    t = np.asarray(t, dtype=float)
    t0 = float(t[0])
    res = lambda x: x - step * np.round(x / step)                   # noqa: E731
    a = float(min(np.arange(-step / 2, step / 2, step / 250), key=lambda o: float(np.sum(res(t - o) ** 2))))
    b = 0.0
    for _ in range(4):
        k = np.round((t - a - b * (t - t0)) / step)
        r = t - k * step
        if len(t) >= 4 and t[-1] - t0 >= min_span:
            b, a = (float(c) for c in np.polyfit(t - t0, r, 1))
        else:
            a = float(np.mean(r))
    k = np.round((t - a - b * (t - t0)) / step)
    shift = round(a / step)                                          # the offset back within half a step
    a, k = a - shift * step, k + shift
    rms = float(np.sqrt(np.mean((t - a - b * (t - t0) - k * step) ** 2)))
    return {'offset': a, 'drift': b, 't0': t0, 'k': k.astype(int), 'rms': rms}


def rhythm_shape(ks, bpb=4):
    """Hits on 16th lines (ints) -> ('3-3-2', 8, 5): the groups in 16ths over the shortest cycle that repeats (4 in
    5 of the hits that have a next cycle land again a cycle later), turned to start with its longest groups (3-3-2,
    not 3-2-3), and how many 16ths after the first hit such a cycle starts. With no repeat: every gap from the
    first hit, None, 0."""
    ks = sorted(set(int(k) for k in ks))
    rel = [k - ks[0] for k in ks]
    s, span = set(rel), rel[-1]
    for P in sorted({2, 4, 8, 16, 32, 64, 2 * bpb, 4 * bpb, 8 * bpb, 16 * bpb}):
        have = [h for h in rel if h + P <= span]
        if len(have) >= 2 and sum(h + P in s for h in have) >= 0.8 * len(have):
            cyc = [h for h in rel if h < P]
            gaps = [b - a for a, b in zip(cyc, cyc[1:] + [P])]
            i = max(range(len(gaps)), key=lambda i: (gaps[i:] + gaps[:i], -i))
            return '-'.join(map(str, gaps[i:] + gaps[:i])), P, cyc[i]
    gaps = [b - a for a, b in zip(rel, rel[1:])]
    return ('-'.join(map(str, gaps[:24])) + ('-...' if len(gaps) > 24 else '')) or 'one hit', None, 0


def _slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').lower() or 'midi'


def _bars_span(bars):
    """bars=[a, b] as (a, b), checked; None for a count of the last bars played."""
    if not isinstance(bars, (list, tuple)):
        return None
    if len(bars) != 2 or int(bars[0]) < 1 or int(bars[1]) < int(bars[0]):
        raise ValueError(f"bars=[a, b]: the first and last bar, a <= b, from 1 (got {list(bars)})")
    return int(bars[0]), int(bars[1])


class ScreenInput:
    """The on-screen keys page as an input: open while it is patched, fed by MidiPlay.screen instead of a driver."""

    def __init__(self, play, name, on_msg):
        self.play, self.name = play, name
        play.feeds[name] = on_msg

    def close(self):
        self.play.feeds.pop(self.name, None)


class MidiPlay:
    def __init__(self, engine, sources=None, opener=None):
        """sources, opener: stand-ins for midi.sources and midi.Input (tests)."""
        self.e = engine
        self.sources = sources or M.sources
        self.opener = opener or M.Input
        self.lock = threading.Lock()
        self.voices = []
        self.patches = {}
        self.known = None
        self.clipped = 0
        self._log = []
        self.feeds = {}                     # an open screen input -> its message handler
        self.seqs = collections.OrderedDict()   # page id -> {key: (last seq, was a note-off)}
        self.ins = {}                       # input name -> its open handle, shared by a patch and control maps
        self.lost = set()                   # inputs in use that were unplugged (they reopen when they come back)
        self.listening = set()              # inputs opened by live_controls(source=) to hear what they send
        self.heard = collections.OrderedDict()  # (input, 'cc:74', channel) -> [last value, count], not mapped

    # ------------------------------------------------------------------ inputs: one handle per input
    def _open(self, name):
        if name not in self.ins and name not in self.lost:
            self.ins[name] = (self._screen if name == SCREEN else self.opener)(name, self._on(name))
        return self.ins.get(name)

    def _shut(self, name):
        inp = self.ins.pop(name, None)
        if inp is not None:
            try:
                inp.close()
            except Exception:
                pass

    def _wanted(self, name):
        return name in self.patches or name in self.listening or bool(self.e.controls.bound(name))

    def release(self, name):
        """Close an input nothing uses any more (no patch, no control map, not listening)."""
        if not self._wanted(name):
            self._shut(name)
            self.lost.discard(name)

    def input_for(self, source=None):
        """The input a control map or a listen names: an input's name or a unique part of it; with none, the one
        input there is."""
        if source is None:
            names = self.sources()
            if len(names) == 1:
                return names[0]
            raise ValueError("source: which MIDI input the controller is, by name or a unique part of it; inputs: "
                             f"{names or 'none (plug the controller in; midi_sources lists them)'}")
        if source in self.ins or source in self.lost:
            return source
        return self.find(source)

    def open_for_controls(self, name):
        self._open(name)

    def listen(self, name):
        """Open an input only to hear what it sends (live_controls(source=)): its unmapped messages are listed."""
        self.listening.add(name)
        self._open(name)

    def _hear(self, name, st, d1, d2):
        what = O.midi_text(st, d1)
        if what is None or (st & 0xF0) == 0x80 or ((st & 0xF0) == 0x90 and not d2):
            return
        k = (name, what, (st & 0x0F) + 1)
        h = self.heard.pop(k, [0, 0])
        val = ((d2 << 7) | d1) - 8192 if (st & 0xF0) == 0xE0 else d1 if (st & 0xF0) == 0xD0 else d2
        self.heard[k] = [val, h[1] + 1]
        while len(self.heard) > 32:
            self.heard.popitem(last=False)

    def heard_lines(self, source=None):
        """What the open inputs sent that no map takes, newest last: the numbers live_map(midi=...) needs."""
        rows = [(k, v) for k, v in self.heard.items() if source is None or k[0] == source]
        L = []
        if source is not None and not rows:
            L.append(f"listening on {source!r}: nothing heard yet that no map takes. Move a knob or press a pad, then "
                     f"call live_controls(source={source!r}) again")
        elif rows:
            L.append("MIDI heard, not mapped (live_map(control=..., midi=..., source=..., channel=...) binds one):")
            L += [f"  {src!r} {what} ch{ch}: last {val}, {n} messages" for (src, what, ch), (val, n) in rows]
        return L

    # ------------------------------------------------------------------ the command
    def find(self, source):
        names = self.sources()
        if source in names or source == SCREEN:
            return source
        hits = [n for n in names if source.lower() in n.lower()] or ([SCREEN] if source.lower() in SCREEN else [])
        if len(hits) == 1:
            return hits[0]
        raise ValueError(f"MIDI input {source!r}: " + (f"matches {hits}, name one" if hits else
                         f"no such input; inputs: {names or 'none (plug a keyboard in, or add a loopMIDI port)'}, "
                         f"and {SCREEN!r} (the on-screen keys page, live_keys_page)"))

    def cmd(self, source=None, track=None, instrument=None, volume_db=None, transpose=None, channel=None,
            remove=False):
        e = self.e
        if source is None:
            return self.describe()
        name = source if source in self.patches else None
        if name is None and remove:
            name = next((n for n in self.patches if source.lower() in n.lower()), None)
        if name is None:
            name = self.find(source)
        p = self.patches.get(name)
        if remove:
            if p is None:
                raise ValueError(f"{name!r} is not patched; patched: {list(self.patches) or 'none'}")
            self.release_all(p)
            del self.patches[name]
            self.release(name)
            return f"{name}: unpatched ({p['played']} keys played)"
        if p is None and track is None and instrument is None:
            raise ValueError(f"{name!r} is not patched yet: say what it plays, track='<live track>' (that track's "
                             f"sound) or instrument=... (as live_track takes it)")
        if volume_db is not None and float(volume_db) > 6:
            raise ValueError("volume_db <= +6")
        if channel is not None and not 0 <= int(channel) <= 16:
            raise ValueError("channel: 1 to 16, or 0 for every channel")
        new = p is None
        if new:
            p = {'source': name, 'what': None, 'gen': 0, 'bank': {}, 'prev': {}, 'want': [],
                 'oneshot': False, 'vol': -6.0, 'transpose': 0, 'channel': None, 'held': {}, 'sustain': False,
                 'sustained': [], 'played': 0, 'missed': 0, 'errors': [], 'lost': False, 'rel_s': 0.1,
                 'log': os.path.join(e.root, 'live', 'midi', _slug(name) + '.jsonl')}
        if volume_db is not None:
            p['vol'] = float(volume_db)
        if transpose is not None:
            p['transpose'] = int(transpose)
        if channel is not None:
            p['channel'] = int(channel) or None
        msg = []
        if new:
            self._open(name)
            p['lost'] = name in self.lost
            self.patches[name] = p
            msg.append(f"{name}: patched")
        if track is not None or instrument is not None:
            try:
                msg.append(self._render(p, track, instrument))
                if volume_db is None and track is not None:      # a track's sound comes at its fader
                    p['vol'] = float(e.tracks[track]['volume_db'])
            except Exception:
                if new:
                    del self.patches[name]
                    self.release(name)
                raise
        return '\n'.join(msg + [self.line(p)])

    def _render(self, p, track, instrument):
        e = self.e
        from .engine import LiveError, _tail_s
        if track is not None:
            if track not in e.tracks or e.tracks[track]['inst'] is None:
                raise LiveError(f"no live track {track!r} with an instrument; tracks: {list(e.tracks) or 'none'}")
            tr = e.tracks[track]
            inst, root, what = e._job_inst(tr), tr['root'] or e.root, f"track {track}'s sound"
        else:
            inst, root = e._resolve_instrument(instrument), e.root
            what = instrument if isinstance(instrument, str) else inst.get('voice') or inst.get('profile') or inst['type']
        t = inst.get('type')
        oneshot = t == 'kit' or t not in ('synth', 'sprite', 'sampler', 'code', 'mimic')
        if t == 'kit':
            want = sorted(int(k) for k in inst['map'])
        elif oneshot:
            want = [60]                      # a drum: every key plays the same hit
        else:
            want = sorted(range(LO, HI + 1), key=lambda m: (abs(m - 60), m))
        gate = ONESHOT_S if oneshot else NOTE_S
        inst = dict(inst, _tail=min(2.0, _tail_s(inst) + 0.3))
        p['prev'] = {**p['prev'], **p['bank']} if p['bank'] else p['prev']
        p['gen'] += 1
        p.update(bank={}, want=want, oneshot=oneshot, what=what, errors=[],
                 rel_s=min(0.5, max(0.06, float((inst.get('amp_env') or {}).get('r', 0.1)))))
        with e.lock:
            for m in want:
                e._submit_midi(('midi', p['source'], p['gen'], m), [(0.0, m, gate, 100)], inst, root)
        return f"rendering {len(want)} notes of {what} on the render workers; keys play as their notes land"

    def rendered(self, key, y, err):
        _, name, gen, m = key
        p = self.patches.get(name)
        if p is None or gen != p['gen']:
            return
        if err:
            p['errors'] = (p['errors'] + [f"{M.note_name(m)}: {err}"])[-3:]
            if len(p['errors']) == 1:
                self.e.news.append(f"keys {name}: a note did not render: {err}")
            return
        p['bank'][m] = np.ascontiguousarray(y, dtype=np.float32)
        if len(p['bank']) == len(p['want']):
            p['prev'] = {}
            self.e.news.append(f"keys {name}: all {len(p['want'])} notes of {p['what']} are ready")

    def _screen(self, name, on_msg):
        return ScreenInput(self, name, on_msg)

    # ------------------------------------------------------------------ the on-screen keys page
    def screen(self, events=(), sid=None):
        """Messages from the on-screen keys page: events [[seq, status, data1, data2], ...], numbered by the page
        `sid` in the order it made them, fed to the 'screen keys' patch as a driver would. Each key is its own
        request and requests travel side by side, so one can overtake another: an event older than the last one
        for its key (or controller) from that page is dropped, except a note-on overtaken by its own note-off,
        which plays as a short tap rather than not at all. With no events, only says what the page shows."""
        if not isinstance(events, (list, tuple)) or len(events) > MAX_EVENTS:
            raise ValueError(f"events: a list of at most {MAX_EVENTS} [seq, status, data1, data2]")
        ok = []
        for ev in events:
            if not (isinstance(ev, (list, tuple)) and len(ev) == 4 and all(isinstance(x, int) for x in ev)
                    and 0x80 <= ev[1] <= 0xBF and 0 <= ev[2] <= 127 and 0 <= ev[3] <= 127):
                raise ValueError(f"event {ev!r}: [seq, status, data1, data2], status a note-off, note-on or "
                                 f"controller (0x80 to 0xBF), data 0 to 127")
            ok.append(ev)
        on = self.feeds.get(SCREEN)
        if on is not None and ok:
            last = self.seqs.setdefault(str(sid), {})
            self.seqs.move_to_end(str(sid))
            while len(self.seqs) > 8:
                self.seqs.popitem(last=False)
            for seq, st, d1, d2 in ok:
                kind = st & 0xF0
                is_off = kind == 0x80 or (kind == 0x90 and not d2)
                k = ('c' if kind == 0xB0 else 'n', st & 0x0F, d1)
                prev = last.get(k)
                if prev is not None and seq <= prev[0]:
                    if kind == 0x90 and d2 and prev[1]:      # its note-off came first: a quick tap
                        on(time.time(), st, d1, d2)
                        on(time.time(), 0x80 | (st & 0x0F), d1, 0)
                    continue
                last[k] = (seq, is_off)
                on(time.time(), st, d1, d2)
        p = self.patches.get(SCREEN)
        if p is None:
            return {'patched': False}
        keys = None if p['oneshot'] and len(p['want']) == 1 else [m - p['transpose'] for m in p['want']]
        return {'patched': True, 'what': p['what'], 'ready': len(p['bank']), 'of': len(p['want']),
                'keys': sorted(keys) if keys is not None else None, 'sustain': p['sustain'],
                'out_ms': self.out_latency() * 1000 // SR}

    # ------------------------------------------------------------------ keys (the driver's thread)
    def _on(self, name):
        def on(t, st, d1, d2):
            if self.e.midi_control(name, st, d1, d2):        # bound by live_map: a control move, not a key
                return
            p = self.patches.get(name)
            if p is None:
                self._hear(name, st, d1, d2)
                return
            kind, ch = st & 0xF0, (st & 0x0F) + 1
            if p['channel'] is not None and ch != p['channel']:
                self._hear(name, st, d1, d2)
                return
            if kind == 0x90 and d2:
                self.note_on(p, d1, d2)
            elif kind == 0x80 or kind == 0x90:
                self.note_off(p, d1)
            elif kind == 0xB0 and d1 == 64:
                p['sustain'] = d2 >= 64
                if not p['sustain']:
                    with self.lock:
                        for v in p['sustained']:
                            v.release(int(p['rel_s'] * SR))
                    p['sustained'] = []
            elif kind == 0xB0 and d1 in (120, 123):
                self.release_all(p)
            else:
                self._hear(name, st, d1, d2)
                return
            self._log.append((p['log'], {'wall': round(time.time(), 4), 'beat': round(self.sound_beat(), 4),
                                         'press': round(self.heard_beat(), 4), 'st': st, 'd1': d1, 'd2': d2}))
        return on

    def heard_beat(self):
        """The beat of the music the person hears now: what the device was handed, less the output buffer and the
        path lookahead the mix carries (as live_listen aligns its bars)."""
        from . import graph as G
        e = self.e
        return e.beat(e.played - self.out_latency() - e.safety.la - G.LAT_BUDGET)

    def sound_beat(self):
        """The beat a key pressed now sounds against: it joins the next device buffer, which starts at `played`, and
        that buffer carries the music of beat(played - path lookahead). The output buffer delays the key and the
        music alike, so this is heard_beat() plus the key's whole delay, where a player who plays by ear meant it."""
        from . import graph as G
        e = self.e
        return e.beat(e.played - e.safety.la - G.LAT_BUDGET)

    def out_latency(self):
        lat = getattr(self.e.stream, 'latency', 0) if self.e.stream is not None else 0
        return int(float(lat or 0) * SR)

    def note_on(self, p, note, vel):
        m = note + p['transpose']
        if p['oneshot'] and len(p['want']) == 1:
            m = p['want'][0]
        y = p['bank'].get(m)
        if y is None:
            y = p['prev'].get(m)
        if y is None:
            p['missed'] += 1
            return
        v = Voice(y, 10 ** (p['vol'] / 20) * (vel / 100.0) ** 1.3)
        with self.lock:
            old = p['held'].pop(note, None)
            if old is not None:
                old.release(int(0.02 * SR))
            if len(self.voices) >= MAX_VOICES:
                self.voices[0].release(int(0.02 * SR))
            self.voices.append(v)
            if not p['oneshot']:
                p['held'][note] = v
        p['played'] += 1

    def note_off(self, p, note):
        with self.lock:
            v = p['held'].pop(note, None)
            if v is None:
                return
            if p['sustain']:
                p['sustained'].append(v)
            else:
                v.release(int(p['rel_s'] * SR))

    def release_all(self, p):
        with self.lock:
            for v in list(p['held'].values()) + p['sustained']:
                v.release(int(0.05 * SR))
            p['held'], p['sustained'] = {}, []

    # ------------------------------------------------------------------ audio (the device's thread)
    def mix(self, out):
        """Adds the sounding keys to out (frames, 2) in place; returns what was added, or None."""
        if not self.voices:
            return None
        n = out.shape[0]
        acc = np.zeros((n, 2), dtype=np.float32)
        with self.lock:
            keep = []
            for v in self.voices:
                k = min(n, v.y.shape[1] - v.i)
                if v.left is not None:
                    k = min(k, v.left)
                if k > 0:
                    seg = v.y[:, v.i:v.i + k].T * v.g
                    if v.left is not None:
                        seg = seg * (np.arange(v.left, v.left - k, -1, dtype=np.float32) / v.rel)[:, None]
                        v.left -= k
                    acc[:k] += seg
                    v.i += k
                if v.i < v.y.shape[1] and (v.left is None or v.left > 0):
                    keep.append(v)
            self.voices = keep
        out += acc
        a = np.abs(out)
        over = a > KNEE
        if over.any():                  # above the knee only: the music under it is untouched
            out[over] = np.sign(out[over]) * (KNEE + (1 - KNEE) * np.tanh((a[over] - KNEE) / (1 - KNEE)))
            if float(a.max()) > 1.0:
                self.clipped += 1
        return acc

    # ------------------------------------------------------------------ inputs coming and going, the log
    def poll(self):
        """Once every POLL_S: news when an input appears or goes, reopen a patched one that came back, write the
        key log."""
        try:
            names = set(self.sources())
        except Exception:
            names = set()
        if self.known is not None:
            for n in sorted(names - self.known):
                p = self.patches.get(n)
                if n in self.lost:
                    self.lost.discard(n)
                    try:
                        self._open(n)
                        if p is not None:
                            p['lost'] = False
                        self.e.news.append(f"keys {n}: plugged back in, playing {p['what']} again" if p is not None
                                           else f"controls on {n}: plugged back in, its maps work again")
                    except Exception as ex:
                        self.lost.add(n)
                        self.e.news.append(f"{'keys' if p is not None else 'controls on'} {n}: back, but it did "
                                           f"not open: {ex}")
                elif p is None and not self._wanted(n):
                    self.e.news.append(f"MIDI input plugged in: {n!r}. live_midi(source={n!r}, instrument=... or "
                                       f"track=...) plays it through the set; live_map(..., midi=..., source={n!r}) "
                                       f"maps its knobs")
            for n in sorted(self.known - names):
                p = self.patches.get(n)
                if n in self.ins:
                    self._shut(n)
                    self.lost.add(n)
                    if p is not None:
                        self.release_all(p)
                        p['lost'] = True
                        self.e.news.append(f"keys {n}: unplugged; it plays again when it comes back")
                    else:
                        self.e.news.append(f"controls on {n}: unplugged; its maps work again when it comes back")
                elif n not in self.lost:
                    self.e.news.append(f"MIDI input removed: {n!r}")
        self.known = names
        self.flush()

    def flush(self):
        log, self._log = self._log, []
        by = {}
        for path, rec in log:
            by.setdefault(path, []).append(rec)
        for path, recs in by.items():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'a', encoding='utf8') as f:
                f.write(''.join(json.dumps(r) + '\n' for r in recs))

    def _logs(self, source):
        """The key logs to read: (folder, sorted file names), one input's when source names it (an input's name, a
        unique part of one, or the log's own name, which still reads after the input is unplugged)."""
        self.flush()
        d = os.path.join(self.e.root, 'live', 'midi')
        names = [n for n in (os.listdir(d) if os.path.isdir(d) else []) if n.endswith('.jsonl')]
        if source is not None:
            direct = _slug(source) + '.jsonl'
            names = [direct] if direct in names else [n for n in names if n == _slug(self.find(source)) + '.jsonl']
        return d, sorted(names)

    def _read(self, path):
        """One input's log as notes (start, midi, dur, vel) on the heard clock, note-offs held while the sustain pedal
        was down; a key still down lasts until now. Returns (notes, keys still down, key delays in beats, how many
        notes came from a log written before the heard clock)."""
        notes, down, held, ped, old, lag = [], {}, set(), False, 0, []
        for r in (json.loads(x) for x in open(path, encoding='utf8') if x.strip()):
            kind, b = r['st'] & 0xF0, r['beat']
            if kind == 0x90 and r['d2']:
                if 'press' in r:
                    lag.append(b - r['press'])
                else:
                    old += 1
                if r['d1'] in down:
                    st, v = down.pop(r['d1'])
                    notes.append((st, r['d1'], b - st, v))
                down[r['d1']] = (b, r['d2'])
                held.discard(r['d1'])
            elif kind in (0x80, 0x90):
                if ped and r['d1'] in down:
                    held.add(r['d1'])
                elif r['d1'] in down:
                    st, v = down.pop(r['d1'])
                    notes.append((st, r['d1'], b - st, v))
            elif kind == 0xB0 and (r['d1'] in (120, 123) or (r['d1'] == 64 and r['d2'] < 64)):
                for m in list(held if r['d1'] == 64 else down):
                    st, v = down.pop(m)
                    notes.append((st, m, b - st, v))
                held.clear()
                ped = False if r['d1'] == 64 else ped
            elif kind == 0xB0 and r['d1'] == 64:
                ped = True
        now = self.sound_beat()
        notes += [(st, m, max(now - st, 0.0), v) for m, (st, v) in down.items()]
        return notes, down, lag, old

    def _choose(self, notes, bars, span):
        """The notes in the bars asked for: the last `bars` bars that had keys, or span (a, b), inclusive. Rounded as
        the lines print them; [] when span has none."""
        bpb = self.e.bpb
        if span is None:
            last_bar = int(max(n[0] for n in notes) // bpb)
            keep = [n for n in notes if n[0] // bpb > last_bar - int(bars)]
        else:
            keep = [n for n in notes if span[0] <= n[0] // bpb + 1 <= span[1]]
        return [(round(st, 3), m, round(du, 3), v) for st, m, du, v in keep]

    def _none_in(self, fn, notes, span):
        bpb = self.e.bpb
        first, last = int(min(n[0] for n in notes) // bpb) + 1, int(max(n[0] for n in notes) // bpb) + 1
        return f"{fn[:-6]}: no notes in bars {span[0]}-{span[1]}; it has notes in bars {first}-{last}"

    def _head(self, fn, keep, notes, down):
        bpb = self.e.bpb
        b0, b1 = int(min(k[0] for k in keep) // bpb) + 1, int(max(k[0] for k in keep) // bpb) + 1
        return (f"{fn[:-6]}: {len(keep)} of {len(notes)} notes, bars {b0}-{b1}"
                + (f" ({len(down)} still held)" if down else ''))

    def played(self, source=None, bars=8, fit=False, from_bar=None):
        """The keys played, as notes text in this engine's bars: 'bar | +beat | pitch | d= | v=' lines (the format
        notes take everywhere), note-offs held by the pedal while it was down. bars: the last `bars` bars that had
        keys, or [a, b] (those bars, inclusive). fit=True fits a 16th grid to the hits and prints two placements as
        live_queue notes (fitted())."""
        from ..notation import format_notes
        if from_bar is not None and not fit:
            raise ValueError("from_bar says which bar the fitted notes count as bar 1; use it with fit=True")
        span = _bars_span(bars)
        d, names = self._logs(source)
        if not names:
            return "no keys played yet" + (f" on {source!r}" if source else '')
        bpb, L = self.e.bpb, []
        for fn in names:
            notes, down, lag, old = self._read(os.path.join(d, fn))
            if not notes:
                continue
            keep = self._choose(notes, bars, span)
            if not keep:
                L.append(self._none_in(fn, notes, span))
                continue
            L.append(self._head(fn, keep, notes, down))
            L.append(self._clock_line(lag, old))
            L.append(self.fitted(keep, from_bar) if fit else format_notes(keep, bpb))
            vels = {k[3] for k in keep}
            if len(vels) == 1 and len(keep) >= 4:
                L.append(f"velocity: every key came at v={vels.pop()} (this keyboard sends one velocity; the lines "
                         f"keep it). Looping it, write an accent shape and say so: the bar's downbeat loudest, then "
                         f"the beats, then pushed 16ths, offbeat 8ths softest; in a chord the top note leads and the "
                         f"lower notes sit well under it (about 30 under sounded right in one jam).")
        return '\n'.join(L) or "no notes played yet (only pedal or controls)"

    def take(self, source=None, bars=8, fit=False, from_bar=None, placement='A'):
        """One input's keys, ready to write into a song's track (live_played(into=...)): {'source', 'notes': [[beat
        from the start of the take's bar 1, midi, dur, vel]], 'first_bar': the engine's bar that is the take's bar 1
        (from_bar, else the first bar with a note), 'bpm', 'bpb', 'lines': what to say about it}. Without fit the
        notes are as they sounded (off the grid where they were played, the pedal's holds, the velocities); with fit,
        the snapped placement A (as played) or B (the groups from beat 1) that fit=True prints."""
        span = _bars_span(bars)
        place = str(placement or 'A').strip().upper()
        if place not in ('A', 'B'):
            raise ValueError(f"placement {placement!r}: 'A' (each hit on the 16th it was played nearest) or 'B' (the "
                             f"groups from beat 1), the two that live_played(fit=True) prints")
        if place == 'B' and not fit:
            raise ValueError("placement='B' is one of the two snapped placements; use it with fit=True")
        d, names = self._logs(source)
        if not names:
            raise ValueError("no keys played yet" + (f" on {source!r}" if source else ''))
        if len(names) > 1:
            raise ValueError(f"keys from {len(names)} inputs ({', '.join(repr(n[:-6]) for n in names)}): name the "
                             f"one to write with source=...")
        fn = names[0]
        notes, down, lag, old = self._read(os.path.join(d, fn))
        if not notes:
            raise ValueError(f"{fn[:-6]}: no notes played yet (only pedal or controls)")
        keep = self._choose(notes, bars, span)
        if not keep:
            raise ValueError(self._none_in(fn, notes, span))
        bpb = self.e.bpb
        L = [self._head(fn, keep, notes, down)]
        if fit:
            f = self._fit(keep)
            if isinstance(f, str):
                raise ValueError(f + ", or write the take as it sounded (leave fit out)")
            lines, placings = f
            L += lines[:2]
            pick = next((p for p in placings if p[0].startswith(place)), None)
            if pick is None:
                L.append("B is the same as A here (the groups already start on the beat): wrote A")
                pick = placings[0]
            L.append(f"wrote {pick[0]} ({pick[1]})")
            out = pick[2]
        else:
            L.append("as it sounded: each note where it was played (off the grid by the player's own timing), its "
                     "velocity and length, note-offs held while the pedal was down. fit=True writes it snapped to "
                     "16ths instead")
            out = keep
        origin = (int(from_bar) - 1) * bpb if from_bar is not None else (min(o[0] for o in out) // bpb) * bpb
        early = [o for o in out if o[0] < origin - 1e-9]
        out = [o for o in out if o[0] >= origin - 1e-9]
        if not out:
            raise ValueError(f"every note is before bar {int(origin // bpb) + 1}; lower from_bar")
        if early:
            L.append(f"{len(early)} notes before bar {int(origin // bpb) + 1} left out (from_bar)")
        vels = {k[3] for k in keep}
        if len(vels) == 1 and len(keep) >= 4:
            L.append(f"velocity: every key came at v={vels.pop()} (this keyboard sends one velocity); written as "
                     f"played. An accent shape (downbeats loudest, offbeat 8ths softest) is a later edit")
        return {'source': fn[:-6], 'first_bar': int(origin // bpb) + 1, 'bpm': float(self.e.bpm), 'bpb': bpb,
                'notes': [[round(st - origin, 6), int(m), round(du, 6), int(v)] for st, m, du, v in sorted(out)],
                'lines': L}

    def _clock_line(self, lag, old):
        """Which clock the times are on, in plain words, and the correction the log applied."""
        ms = float(np.median(lag)) * self.e.spb * 1000 if lag else 0.0
        if lag and ms >= 1:
            s = (f"clock: each note sits where it sounded against the beat the player heard, which is where someone "
                 f"playing by ear meant it: the key press plus the {ms:.0f} ms the keys take to reach the speakers "
                 f"(the output buffer). The press times are in the log too ('press').")
        elif lag:
            s = ("clock: each note sits where it sounded against the beat the player heard (no output delay was "
                 "measured, so that is the press).")
        else:
            return ("clock: this log was written before the keys' delay was counted: each note is at the press, "
                    "about one key delay early against what the player heard (fit=True finds the 16ths either way).")
        if old:
            s += (f" {old} notes come from a log written before this clock: those are at the press, about one key "
                  f"delay early (fit=True finds the 16ths either way).")
        return s

    def _fit(self, notes):
        """The fit behind fitted() and take(): the text when there are under 3 hits, else (lines: the fit, the rhythm
        and the placement advice, placings: [(label, why, snapped notes (start, midi, dur, vel) on the engine's
        clock)], A as played first, then B when it differs)."""
        from ..notation import fmt_num
        bpb, ms = self.e.bpb, self.e.spb * 1000
        notes = sorted(notes)
        hits = []                                   # [onset of the hit's first note, [note indices]]
        for i, n in enumerate(notes):
            if hits and n[0] - hits[-1][0] < HIT:
                hits[-1][1].append(i)
            else:
                hits.append([n[0], [i]])
        if len(hits) < 3:
            return (f"fit: {len(hits)} hits in these bars; a grid needs at least 3. Choose more bars (bars=[a, b]) "
                    f"or read the lines without fit")
        g = fit_grid([h[0] for h in hits], min_span=bpb)
        grid = lambda t: (t - g['offset'] - g['drift'] * (t - g['t0'])) / STEP     # noqa: E731
        ks = [int(k) for k in g['k']]
        shape, period, lead = rhythm_shape(ks, bpb)
        drift_ms = g['drift'] * bpb * ms
        L = [f"fit: {len(hits)} hits (keys within {HIT:g} beat are one hit) against a 16th grid with a free offset: "
             f"they sit {abs(g['offset']) * ms:.0f} ms {'after' if g['offset'] >= 0 else 'before'} the 16ths, "
             f"{g['rms'] * ms:.0f} ms rms off them, drifting {abs(drift_ms):.0f} ms per bar "
             + ("(steady)" if abs(drift_ms) < 15 else "later (dragging; taken out before snapping)" if drift_ms > 0
                else "earlier (rushing; taken out before snapping)")
             + ("" if g['rms'] < 0.06 else ". A loose fit: some hits may land on the wrong 16th, check by ear")]
        L.append(f"rhythm: {shape} in 16ths" +
                 (f", repeating every {period} 16ths ({fmt_num(period * STEP)} beats)" if period else
                  " from the first hit (no repeat found in these bars)")
                 + (f"; the take starts {lead} 16ths before a {shape} cycle" if lead else ''))
        # B: a cycle's first group on beat 1 (and on every cycle line within the bar: beats 1 and 3 for 2 beats)
        x = (ks[0] + lead) * STEP
        c = period * STEP if period and (bpb * 4) % period == 0 else bpb
        move = round(x / c) * c - x
        on = "beat 1" + (" and beat 3" if c * 2 == bpb else '') if c >= bpb / 2 else "the beat"
        placings = [('A, as played', 0.0, "each hit on the 16th it was played nearest")]
        if abs(move) > 1e-9:
            placings.append(('B, groups from beat 1', move, f"everything {fmt_num(abs(move))} beat "
                             f"{'later' if move > 0 else 'earlier'}, so the {shape} groups start on {on}"))
        L.append("placement against the beat is a musical choice the data cannot make: play "
                 + ("both and let the player pick by ear (one word: A or B)" if len(placings) > 1 else
                    f"it to the player (as played, the {shape} groups already start on {on})"))
        out_all = []
        for label, mv, why in placings:
            out = []
            for (t, idx), k in zip(hits, ks):
                st = k * STEP + mv
                for i in idx:
                    _, m, du, v = notes[i]
                    end = round(grid(notes[i][0] + du)) * STEP + mv
                    out.append((st, m, max(STEP, end - st), v))
            out_all.append((label, why, sorted(out)))
        return L, out_all

    def fitted(self, notes, from_bar=None):
        """notes: (start, midi, dur, vel) on the heard clock. The hits (onsets within HIT beat are one), the 16th
        grid that fits them with a free offset, its error and drift, the rhythm's shape, and the notes snapped two
        ways as live_queue notes: as played (each hit on its nearest 16th) and with the first hit on a downbeat."""
        from ..notation import fmt_num
        f = self._fit(notes)
        if isinstance(f, str):
            return f
        L, placings = f
        bpb = self.e.bpb
        for label, why, out in placings:
            origin = (int(from_bar) - 1) * bpb if from_bar is not None else (min(o[0] for o in out) // bpb) * bpb
            early = [o for o in out if o[0] < origin - 1e-9]
            out = [o for o in out if o[0] >= origin - 1e-9]
            if not out:
                L.append(f"{label}: every note is before bar {int(origin // bpb) + 1}; lower from_bar")
                continue
            n_bars = int((max(o[0] for o in out) - origin) // bpb) + 1
            by_bar = {}
            for st, m, du, v in out:
                by_bar.setdefault(int((st - origin) // bpb), []).append(
                    f"{fmt_num(st - origin)} {M.note_name(m)} {fmt_num(du)} {v}")
            first = min(o[0] for o in out)
            L.append(f"{label} ({why}): first hit at bar {int(first // bpb) + 1} +{fmt_num(first % bpb)}; as a "
                     f"live_queue clip from the start of bar {int(origin // bpb) + 1}, bars={n_bars}"
                     + (f" ({len(early)} notes before that bar left out)" if early else '') + ", notes:")
            L += ['; '.join(by_bar[b]) for b in sorted(by_bar)]
        return '\n'.join(L)

    def loop(self):
        while self.e.running:
            self.poll()
            time.sleep(POLL_S)

    def close(self):
        for n in list(self.ins):
            self._shut(n)
        self.flush()

    # ------------------------------------------------------------------ what it says
    def line(self, p):
        ready = f"{len(p['bank'])}/{len(p['want'])} notes ready" if p['want'] else "no instrument"
        extra = [f"volume {p['vol']:+.0f} dB"] if p['vol'] else []
        if p['transpose']:
            extra.append(f"transpose {p['transpose']:+d}")
        if p['channel']:
            extra.append(f"channel {p['channel']}")
        if p['missed']:
            extra.append(f"{p['missed']} keys came before their note was ready")
        if p['errors']:
            extra.append("render errors: " + '; '.join(p['errors']))
        lat = self.out_latency() * 1000 // SR
        return (f"keys {p['source']}" + (" (UNPLUGGED)" if p['lost'] else '') + f": plays {p['what']} | {ready} | "
                f"{p['played']} played" + (f" | {', '.join(extra)}" if extra else '')
                + (f" | a key sounds about {lat} ms after it is pressed (the output's buffer)" if lat else ''))

    def describe(self):
        names = self.sources()
        lines = ["MIDI inputs: " + (', '.join(repr(n) for n in names) if names else
                                    "none (plug a keyboard in, or add a loopMIDI port; this notices it)"),
                 f"and {SCREEN!r}, the on-screen keys page (live_keys_page), for someone with no MIDI instrument"]
        lines += [self.line(p) for p in self.patches.values()] or \
            ["nothing patched: live_midi(source=..., instrument=... or track=...) plays one through the set"]
        if self.clipped:
            lines.append(f"the keys pushed the output over 0 dBFS {self.clipped} times (soft-clipped): lower "
                         f"their volume_db")
        return '\n'.join(lines)
