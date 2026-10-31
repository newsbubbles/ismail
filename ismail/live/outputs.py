"""Where the live engine's sound goes, and what reaches back into it.

Streams: the master or any bus or deck as raw PCM (int16, little-endian, interleaved stereo, SR Hz) over a plain HTTP
GET on the engine's localhost port, `/stream?name=bus:lucy`. Each streamed bus passes its own safety limiter, as the
master does; a client that falls more than MAX_QUEUE_S behind loses its oldest audio instead of slowing the set. A
stage page reaches it through its own server (same origin, https), which relays the stream; the engine never listens
beyond localhost.

Controls: a named control (a knob, a switch, a slider on the VR stage) is mapped once by an agent to a live parameter
(a track, bus or deck volume, a deck EQ, an effect param). Its moves then apply in the engine in milliseconds, with no
model in the loop, and every move is logged with the bar it happened at, so a take's knob moves read back as
automation points. A MIDI controller's CC, 14-bit CC pair, pitch bend, channel pressure or a pad's note can be the
source of a map (live_map(midi=...)), in modes absolute, pickup, relative and toggle: Controls.midi_in turns its
messages into moves.

The default output: a speaker that connects after the engine started is not in this process's device list, so the
current default is asked of a fresh process.
"""
import collections
import json
import math
import os
import subprocess
import sys
import threading
import time

import numpy as np

from .safety import Safety

MAX_QUEUE_S = 1.0
FORMAT = {'format': 's16le', 'channels': 2}


class StreamHub:
    def __init__(self, sr):
        self.sr = sr
        self.subs = collections.defaultdict(list)       # name -> [Sub]
        self.limit = {}                                  # name -> Safety (buses and decks; the master has its own)
        self.lock = threading.Lock()
        self.dropped = collections.Counter()

    def wants(self, name):
        return bool(self.subs.get(name))

    def names(self):
        with self.lock:
            return {k: len(v) for k, v in self.subs.items() if v}

    def subscribe(self, name):
        s = _Sub(int(MAX_QUEUE_S * self.sr) * 4)
        with self.lock:
            self.subs[name].append(s)
            if name != 'master' and name not in self.limit:
                self.limit[name] = Safety(self.sr)
        return s

    def unsubscribe(self, name, s):
        with self.lock:
            if s in self.subs.get(name, []):
                self.subs[name].remove(s)

    def push(self, name, y, limited=False):
        """y: float (2, n) after the fader (and the fade of a stopping set). The master arrives limited already."""
        subs = self.subs.get(name)
        if not subs:
            return
        if not limited:
            y = self.limit[name].process(y)
        b = (np.clip(np.asarray(y, dtype=np.float32).T, -1.0, 1.0) * 32767.0).astype('<i2').tobytes()
        for s in list(subs):
            if s.put(b):
                self.dropped[name] += 1


class _Sub:
    def __init__(self, cap_bytes):
        self.q = collections.deque()
        self.n = 0
        self.cap = cap_bytes
        self.cv = threading.Condition()
        self.closed = False

    def put(self, b):
        dropped = False
        with self.cv:
            self.q.append(b)
            self.n += len(b)
            while self.n > self.cap and len(self.q) > 1:      # too far behind: lose the oldest, never slow the set
                self.n -= len(self.q.popleft())
                dropped = True
            self.cv.notify()
        return dropped

    def get(self, timeout=1.0):
        with self.cv:
            if not self.q:
                self.cv.wait(timeout)
            if not self.q:
                return b''
            out = b''.join(self.q)
            self.q.clear()
            self.n = 0
            return out


# ------------------------------------------------------------------ controls

CURVES = ('linear', 'log', 'switch', 'raw')
DECK_PARAMS = ('volume_db', 'low_db', 'mid_db', 'high_db', 'filter', 'transpose')


def parse_param(target, param):
    """-> ('track'|'bus'|'deck', name, kind, detail). kind: 'volume_db', 'pan', a deck param, or 'fx' with detail
    (index or 'type[:n]', param name)."""
    if target.startswith('deck:'):
        if param not in DECK_PARAMS:
            raise ValueError(f"a deck control drives one of {', '.join(DECK_PARAMS)}, not {param!r}")
        return 'deck', target[5:], param, None
    kind = 'bus' if target.startswith('bus:') else 'track'
    name = target[4:] if kind == 'bus' else target
    if param in ('volume_db', 'pan') and not (kind == 'bus' and param == 'pan'):
        return kind, name, param, None
    if param.startswith('fx:') and '.' in param:
        which, _, p = param[3:].rpartition('.')
        idx = int(which) if which.isdigit() else which
        return kind, name, 'fx', (idx, p)
    raise ValueError(f"param {param!r}: 'volume_db'" + (", 'pan'" if kind == 'track' else '') +
                     ", or an effect param as 'fx:<index or type>.<param>' (e.g. 'fx:filter.cutoff', 'fx:0.mix', "
                     "'fx:delay:2.feedback')")


def scale(value, rng, curve):
    v = float(value)
    if curve == 'raw' or rng is None:
        return v
    lo, hi = float(rng[0]), float(rng[1])
    v = min(1.0, max(0.0, v))
    if curve == 'switch':
        return hi if v >= 0.5 else lo
    if curve == 'log':
        return lo * (hi / lo) ** v
    return lo + (hi - lo) * v


def unscale(x, rng, curve):
    """The control position 0..1 that puts the param at x (scale's inverse, clamped); None for a raw map or a value
    it cannot place."""
    if curve == 'raw' or rng is None or x is None:
        return None
    lo, hi = float(rng[0]), float(rng[1])
    x = float(x)
    if curve == 'switch':
        return 1.0 if abs(x - hi) < abs(x - lo) else 0.0
    if curve == 'log':
        if x <= 0:
            return None
        v = math.log(x / lo) / math.log(hi / lo) if hi != lo else 0.0
    else:
        v = (x - lo) / (hi - lo) if hi != lo else 0.0
    return min(1.0, max(0.0, v))


# ------------------------------------------------------------------ MIDI as a control source (live_map(midi=...))

MIDI_KINDS = "'cc:<0-127>', 'cc14:<0-31>' (MSB n with LSB n+32), 'bend', 'pressure', or 'note:<36 or C2>'"
MODES = ('absolute', 'pickup', 'relative', 'relative64', 'toggle')
PICKUP_TOL = 2 / 127        # pickup takes over when the knob comes this close to the param, or crosses it
REL_STEP = 1 / 128          # one tick of a relative encoder: 128 ticks cover the range
TOGGLE_REPEAT_S = 0.1       # a toggle button's 64-or-more repeated this long after the last, with no release, is a new press


def parse_midi(midi, channel=None, mode='absolute'):
    """live_map's midi= text -> {'kind', 'num', 'lsb', 'channel', 'mode', 'text'}, checked."""
    from ..notation import NotationError, pitch_to_midi
    kind, _, arg = str(midi).strip().partition(':')
    kind, arg = kind.lower(), arg.strip()
    num = lsb = None
    if kind in ('cc', 'cc14', 'note'):
        try:
            num = pitch_to_midi(arg) if kind == 'note' else int(arg)
        except (ValueError, NotationError):
            num = None
        top = 31 if kind == 'cc14' else 127
        if num is None or not 0 <= num <= top:
            raise ValueError(f"midi {midi!r}: {kind}:<0-{top}{' or a name like C2' if kind == 'note' else ''}>; "
                             f"sources: {MIDI_KINDS}")
        if kind == 'cc14':
            lsb = num + 32
    elif kind not in ('bend', 'pressure') or arg:
        raise ValueError(f"midi {midi!r}: one of {MIDI_KINDS}. Not sure what a knob sends? live_controls(source=<the "
                         f"input>) listens; move it, then call live_controls again")
    if mode not in MODES:
        raise ValueError(f"mode {mode!r}: one of {', '.join(MODES)}")
    if mode in ('relative', 'relative64') and kind != 'cc':
        raise ValueError(f"mode {mode!r} reads an endless encoder's steps on a 'cc:<n>'; {kind} sends positions: "
                         f"mode 'absolute' or 'pickup'")
    if mode == 'toggle' and kind not in ('cc', 'note'):
        raise ValueError("mode 'toggle' flips on a button press: a 'note:<n>' pad or a 'cc:<n>' button")
    if kind == 'note' and mode not in ('absolute', 'toggle'):
        raise ValueError(f"a 'note:<n>' pad is 'absolute' (held = 1, released = 0) or 'toggle' (each press flips), "
                         f"not {mode!r}")
    if channel is not None and not 0 <= int(channel) <= 16:
        raise ValueError("channel: 1 to 16, or 0 for every channel (the default)")
    text = {'cc': f"cc:{num}", 'cc14': f"cc14:{num}", 'note': f"note:{num}"}.get(kind, kind)
    return {'kind': kind, 'num': num, 'lsb': lsb, 'channel': int(channel) if channel else None, 'mode': mode,
            'text': text}


def midi_text(st, d1):
    """What a message is, in live_map's words: 'cc:74', 'note:36', 'bend', 'pressure'; None for the rest."""
    return {0xB0: f"cc:{d1}", 0x90: f"note:{d1}", 0x80: f"note:{d1}", 0xE0: 'bend',
            0xD0: 'pressure'}.get(st & 0xF0)


class Controls:
    def __init__(self, log_path):
        self.maps = {}
        self.log_path = log_path
        self.last = {}
        self._lock = threading.Lock()
        self._buf = []              # log lines not yet written (flush): no file work on a MIDI driver's thread

    def set(self, control, target, param, rng=None, curve='linear', at='now', midi=None):
        """Store a map (build checks it); returns it."""
        m = self.build(target, param, rng, curve, at, midi)
        self.maps[control] = m
        return m

    @staticmethod
    def build(target, param, rng=None, curve='linear', at='now', midi=None):
        """A checked map, not stored. midi: a binding from parse_midi plus 'source' (the input's name), or None for
        a control the stage or an agent moves."""
        if curve not in CURVES:
            raise ValueError(f"curve {curve!r}: one of {', '.join(CURVES)}")
        if curve != 'raw':
            if not (isinstance(rng, (list, tuple)) and len(rng) == 2):
                raise ValueError("range: [value at 0, value at 1] (the control sends 0..1), or curve='raw' to pass "
                                 "the value through in the param's own units")
            if curve == 'log' and (float(rng[0]) <= 0 or float(rng[1]) <= 0):
                raise ValueError("a log curve needs a range above 0 (Hz, a ratio); use linear for dB")
        elif midi is not None:
            raise ValueError("a MIDI control sends positions, not the param's units: give range=[value at 0, value "
                             "at the top] with curve 'linear', 'log' or 'switch'")
        spec = parse_param(target, param)
        m = {'target': target, 'param': param, 'spec': spec, 'range': rng, 'curve': curve, 'at': at}
        if midi is not None:
            m['midi'] = dict(midi)
            # applied: the last position this map sent; seq/done: sent and applied counts (moves wait in the
            # engine's queue); pair: a cc14 pair's MSB and LSB per channel; down/t_down: a toggle button's state
            m['st'] = {'caught': False, 'last_in': None, 'applied': None, 'seq': 0, 'done': 0, 'pair': {},
                       'down': False, 't_down': 0.0}
        return m

    def bound(self, source):
        """The maps a MIDI input drives: [(control, map)]."""
        return [(c, m) for c, m in list(self.maps.items()) if (m.get('midi') or {}).get('source') == source]

    def midi_in(self, source, st, d1, d2, now):
        """A message from input `source` -> (the moves it makes [(control, map, position 0..1, via)], whether a map is
        bound to it: a bound message is a control, never a key). now(m): the map's param now as a position 0..1, or
        None when it cannot be read. While this map's own moves wait in the engine's queue, relative steps, toggles
        and pickup count from the last position it sent, not from the param (which has not moved yet)."""
        kind, ch = st & 0xF0, (st & 0x0F) + 1
        moves, hit = [], False
        for control, m in self.bound(source):
            b, s = m['midi'], m['st']
            if b['channel'] is not None and ch != b['channel']:
                continue
            v = None
            if b['kind'] == 'cc' and kind == 0xB0 and d1 == b['num']:
                hit = True
                if b['mode'] in ('relative', 'relative64'):
                    step = (d2 - 128 if d2 >= 64 else d2) if b['mode'] == 'relative' else d2 - 64
                    if step:
                        base = self._here(m, now)
                        v = min(1.0, max(0.0, (base if base is not None else 0.0) + step * REL_STEP))
                elif b['mode'] == 'toggle':
                    down, t = d2 >= 64, self.clock()
                    # a press: a value of 64 or more after a release, or after TOGGLE_REPEAT_S with no release
                    # (a trigger button that sends 127 on every press and never 0)
                    if down and (not s['down'] or t - s['t_down'] >= TOGGLE_REPEAT_S):
                        v = self._flip(m, now)
                    if down:
                        s['t_down'] = t
                    s['down'] = down
                else:
                    v = d2 / 127.0
            elif b['kind'] == 'cc14' and kind == 0xB0 and d1 in (b['num'], b['lsb']):
                hit = True
                pr = s['pair'].setdefault(ch, {'msb': None, 'lsb': 0, 'lsb_seen': False})
                if d1 == b['num']:
                    pr['msb'] = d2
                    # 7 bits until an LSB is seen; then each half moves it with the other's last value (a
                    # controller may send the LSB first)
                    v = ((d2 << 7) | pr['lsb']) / 16383.0 if pr['lsb_seen'] else d2 / 127.0
                else:
                    pr['lsb'], pr['lsb_seen'] = d2, True
                    if pr['msb'] is not None:
                        v = ((pr['msb'] << 7) | d2) / 16383.0
            elif b['kind'] == 'bend' and kind == 0xE0:
                hit = True
                v = (min(1.0, max(-1.0, (((d2 << 7) | d1) - 8192) / 8191.0)) + 1) / 2   # the centre is 0.5
            elif b['kind'] == 'pressure' and kind == 0xD0:
                hit = True
                v = d1 / 127.0
            elif b['kind'] == 'note' and kind in (0x80, 0x90) and d1 == b['num']:
                hit = True
                on = kind == 0x90 and d2 > 0
                if b['mode'] == 'toggle':
                    v = self._flip(m, now) if on else None
                else:
                    v = 1.0 if on else 0.0
            if v is None or (b['mode'] == 'pickup' and not self._caught(m, v, now)):
                continue
            s['applied'] = v
            s['seq'] += 1
            moves.append((control, m, v, f"midi {b['text']} ch{ch}"))
        return moves, hit

    clock = staticmethod(time.monotonic)

    @staticmethod
    def pending(m):
        """Has this map sent moves the engine has not applied yet?"""
        return m['st']['seq'] != m['st']['done']

    @classmethod
    def _here(cls, m, now):
        """Where this map's param is, as far as the map can tell: its last sent position while that waits in the
        queue, else the param now (else the last position sent)."""
        s = m['st']
        if cls.pending(m) and s['applied'] is not None:
            return s['applied']
        cur = now(m)
        return cur if cur is not None else s['applied']

    @classmethod
    def _flip(cls, m, now):
        cur = cls._here(m, now)
        return 0.0 if (cur or 0.0) >= 0.5 else 1.0

    @classmethod
    def _caught(cls, m, v, now):
        """Pickup (takeover): the knob moves the param only once it reaches the param's value (or passes it), so a
        knob left elsewhere never makes the param jump. Anything else moving the param (the stage, the agent, another
        map) lets go of it again: checked against the last position this map applied, never while its own moves
        still wait in the queue."""
        s = m['st']
        cur = cls._here(m, now)
        if s['caught'] and not cls.pending(m) and cur is not None and s['applied'] is not None and                 abs(cur - s['applied']) > PICKUP_TOL:
            s['caught'] = False
        prev, s['last_in'] = s['last_in'], v
        if not s['caught'] and (cur is None or m['curve'] == 'switch' or abs(v - cur) <= PICKUP_TOL
                                or (prev is not None and (prev - cur) * (v - cur) <= 0)):
            s['caught'] = True
        if s['caught']:
            s.pop('waiting', None)
        else:
            s['waiting'] = (v, cur)
        return s['caught']

    def log(self, rec):
        """Kept in memory; flush() writes it (the engine's control thread, a read, the end of the set)."""
        with self._lock:
            self._buf.append(json.dumps(rec) + '\n')

    def flush(self):
        with self._lock:
            buf, self._buf = self._buf, []
            if not buf:
                return
            try:
                os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
                with open(self.log_path, 'a', encoding='utf8') as f:
                    f.write(''.join(buf))
            except OSError:
                pass

    def read(self, control=None):
        self.flush()
        if not os.path.exists(self.log_path):
            return []
        out = []
        with open(self.log_path, encoding='utf8') as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if control is None or r.get('control') == control:
                    out.append(r)
        return out


def describe(m):
    rng = '' if m['curve'] == 'raw' else f" {m['range'][0]:g}..{m['range'][1]:g}"
    out = f"{m['target']} {m['param']} ({m['curve']}{rng}" + (f", at {m['at']}" if m['at'] not in ('now', None) else '') + ')'
    b = m.get('midi')
    if b:
        out += (f" <- MIDI {b['text']}" + (f" (LSB cc:{b['lsb']})" if b['lsb'] is not None else '')
                + (f" ch{b['channel']}" if b['channel'] else ', any channel') + f", on {b['source']!r}, {b['mode']}")
        w = m['st'].get('waiting')
        if b['mode'] == 'pickup' and w:
            out += f" (waiting: the knob is at {w[0]:.2f}, the param at {w[1]:.2f}; it takes over there)"
    return out


# ------------------------------------------------------------------ the default output

def default_output_name(timeout=5.0):
    """The system's default output as PortAudio names it now, asked of a fresh process (this one's device list was
    read when it started). None when it cannot tell."""
    code = "import sounddevice as sd; print(sd.query_devices(kind='output')['name'])"
    kw = {}
    if sys.platform == 'win32':
        kw['creationflags'] = 0x08000000 | 0x00004000        # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    try:
        r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=timeout, **kw)
        name = r.stdout.strip()
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None
