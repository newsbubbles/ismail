"""Played keys: a MIDI input (a keyboard, a pad, a loopMIDI port) patched to an instrument in the live engine, so a
person plays along with the set from the same speakers.

The engine mixes about half a second ahead of the device, so a key mixed there would sound that late. Instead each
note of the instrument is rendered once, ahead, on the engine's render workers (C2 to C7, or a kit's pads), and a
key adds its note where the audio goes to the device: it sounds one output buffer after the press. Those notes
skip the track's effects and the safety limiter; a soft clip guards the sum. live_listen hears them; a recording
started with live_record and the streams (the phone) do not yet.

Every key is logged with the bar of the music heard when it was pressed (live/midi/<source>.jsonl), for turning a
performance into a part later. The engine also watches for inputs plugged in or removed and says so in its news.

One input needs no driver: 'screen keys', the on-screen keyboard page (live_keys_page), for someone with no MIDI
instrument. Its keys arrive as small HTTP messages (the engine's /api/keys, or the phone server's, which relays them)
and are fed to the patch here exactly as a driver's would be.
"""
import collections
import json
import os
import re
import threading
import time

import numpy as np

from .. import midi as M
from ..dsp import SR

LO, HI = 36, 96             # the notes rendered for a melodic instrument: C2 to C7
NOTE_S = 2.5                # each note's gate: a key held longer ends with the note's own release
ONESHOT_S = 0.2             # a drum's or a kit pad's gate (note-offs are ignored for those)
MAX_VOICES = 48
POLL_S = 2.0
KNEE = 0.9                  # the keys plus the set above this are soft-clipped (they skip the safety limiter)
SCREEN = 'screen keys'      # the on-screen keys page, an input that is always there (live_keys_page)
MAX_EVENTS = 64             # messages in one request from the page


class Voice:
    __slots__ = ('y', 'i', 'g', 'rel', 'left')

    def __init__(self, y, g):
        self.y, self.i, self.g, self.rel, self.left = y, 0, g, None, None

    def release(self, n):
        if self.rel is None:
            self.rel = self.left = max(1, n)


def _slug(name):
    return re.sub(r'[^A-Za-z0-9]+', '_', name).strip('_').lower() or 'midi'


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
            self._close(p)
            self.release_all(p)
            del self.patches[name]
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
            p = {'source': name, 'input': None, 'what': None, 'gen': 0, 'bank': {}, 'prev': {}, 'want': [],
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
            p['input'] = (self._screen if name == SCREEN else self.opener)(name, self._on(name))
            self.patches[name] = p
            msg.append(f"{name}: patched")
        if track is not None or instrument is not None:
            try:
                msg.append(self._render(p, track, instrument))
                if volume_db is None and track is not None:      # a track's sound comes at its fader
                    p['vol'] = float(e.tracks[track]['volume_db'])
            except Exception:
                if new:
                    self._close(p)
                    del self.patches[name]
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
            p = self.patches.get(name)
            if p is None:
                return
            kind, ch = st & 0xF0, (st & 0x0F) + 1
            if p['channel'] is not None and ch != p['channel']:
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
                return
            self._log.append((p['log'], {'wall': round(time.time(), 4), 'beat': round(self.heard_beat(), 4),
                                         'st': st, 'd1': d1, 'd2': d2}))
        return on

    def heard_beat(self):
        """The beat of the music the person hears now: what the device was handed, less the output buffer and the
        path lookahead the mix carries (as live_listen aligns its bars)."""
        from . import graph as G
        e = self.e
        return e.beat(e.played - self.out_latency() - e.safety.la - G.LAT_BUDGET)

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
                if p is not None and p['lost']:
                    try:
                        p['input'] = self.opener(n, self._on(n))
                        p['lost'] = False
                        self.e.news.append(f"keys {n}: plugged back in, playing {p['what']} again")
                    except Exception as ex:
                        self.e.news.append(f"keys {n}: back, but it did not open: {ex}")
                elif p is None:
                    self.e.news.append(f"MIDI input plugged in: {n!r}. live_midi(source={n!r}, instrument=... or "
                                       f"track=...) plays it through the set")
            for n in sorted(self.known - names):
                p = self.patches.get(n)
                if p is not None and not p['lost']:
                    self._close(p)
                    self.release_all(p)
                    p['lost'] = True
                    self.e.news.append(f"keys {n}: unplugged; it plays again when it comes back")
                else:
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

    def played(self, source=None, bars=8):
        """The keys played, as notes text in this engine's bars: 'bar | +beat | pitch | d= | v=' lines (the format
        notes take everywhere), note-offs held by the pedal while it was down. The last `bars` bars that had keys."""
        from ..notation import format_notes
        self.flush()
        d = os.path.join(self.e.root, 'live', 'midi')
        names = [n for n in (os.listdir(d) if os.path.isdir(d) else []) if n.endswith('.jsonl')]
        if source is not None:
            names = [n for n in names if n == _slug(self.find(source)) + '.jsonl']
        if not names:
            return "no keys played yet" + (f" on {source!r}" if source else '')
        bpb, L = self.e.bpb, []
        for fn in sorted(names):
            notes, down, held, ped = [], {}, set(), False
            for r in (json.loads(x) for x in open(os.path.join(d, fn), encoding='utf8') if x.strip()):
                kind, b = r['st'] & 0xF0, r['beat']
                if kind == 0x90 and r['d2']:
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
            now = self.heard_beat()
            notes += [(st, m, max(now - st, 0.0), v) for m, (st, v) in down.items()]
            if not notes:
                continue
            last_bar = int(max(n[0] for n in notes) // bpb)
            keep = [(round(st, 3), m, round(du, 3), v) for st, m, du, v in notes if st // bpb > last_bar - int(bars)]
            L.append(f"{fn[:-6]}: {len(keep)} of {len(notes)} notes, bars {int(min(k[0] for k in keep) // bpb) + 1}"
                     f"-{last_bar + 1}" + (f" ({len(down)} still held)" if down else ''))
            L.append(format_notes(keep, bpb))
        return '\n'.join(L) or "no notes played yet (only pedal or controls)"

    def loop(self):
        while self.e.running:
            self.poll()
            time.sleep(POLL_S)

    def _close(self, p):
        if p['input'] is not None:
            try:
                p['input'].close()
            except Exception:
                pass
            p['input'] = None

    def close(self):
        for p in self.patches.values():
            self._close(p)
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
