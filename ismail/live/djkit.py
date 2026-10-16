"""DJ kit: what a long live set keeps needing, so each request from the audience becomes one short call.

Learned in a one-hour improvised set where every section re-wrote the same helpers and each turn took 5 to 10
minutes of writing. Three layers:

Notes (pure, no engine): `steps` turns a step string into notes, `per_bar` builds a long clip bar by bar (a cycle
whose arrangement lives inside the clip: low, lift, peak, break), `notes` writes them as notation, on another
metric grid when the feel changes (`metric`: one beat of the new feel in engine beats).

Moves (pure): `sweep`, `throw`, `gap`, `pump` return live_fx moves; a list of them goes out in one call
(`Set.moves` = live_fx(moves=[...])). Effects are addressed by type ('filter', 'delay:2'), so moves survive edits
to a chain. `with_gain` puts a gain first in a chain, which `gap` and fades use.

Plumbing: `Set` wraps the live ops for one set folder and logs every call to set/setlog.md with the clip ids per
section, so a queued section can be cancelled when the audience changes direction.

    from ismail.live.djkit import Set, steps, per_bar, notes, metric, sweep, throw, gap, with_gain
    S = Set('songs/myset')
    S.start(124)
    S.track('kick', instrument={'type': 'kick'}, fx=with_gain([]))
    S.q([{'track': 'kick', 'notes': notes(steps('X...X...X...X...')), 'bars': 1}], section='s01_house')
    S.moves(gap(['kick', 'bass'], bar=33) + [throw('stab', at=32.5)])
"""
import json
import os
import random
import re
import shutil
import time

VEL = {'X': 112, 'x': 92, 'o': 64, 'g': 40}     # accent, normal, soft, ghost


# ------------------------------------------------------------------ notes

def steps(pattern, pitch='C1', step=0.25, swing=0.0, lag=0.0, dur=0.2, vel=None, seed=0, start=0.0):
    """A step string -> [(beat, pitch, dur, vel)]. Each char is one step (16ths by default): X accent, x normal,
    o soft, g ghost, '.' rest; spaces are ignored. swing pushes every second step later (beats), lag delays every
    hit (a lazy backbeat), vel fixes the velocity. Velocities vary by +-5 from a seed, so loops breathe."""
    rnd = random.Random(seed)
    out = []
    i = 0
    for c in pattern:
        if c == ' ':
            continue
        if c != '.':
            if c not in VEL:
                raise ValueError(f"step {c!r}: use X x o g or '.' (rest)")
            v = vel or max(1, min(127, VEL[c] + rnd.randint(-5, 5)))
            out.append((start + i * step + (swing if i % 2 else 0) + lag, pitch, dur, v))
        i += 1
    return out


def per_bar(n_bars, fn, beats_per_bar=4):
    """One long clip built bar by bar: fn(bar) -> tuples with beats inside that bar (bar 0 first). The clip carries
    its own arrangement (drop a part in the break bars, a fill in the last), so one queue call covers a cycle."""
    out = []
    for b in range(n_bars):
        out += [(b * beats_per_bar + t, p, d, v) for t, p, d, v in fn(b)]
    return out


def metric(feel_bpm, engine_bpm):
    """Metric modulation: one beat of a feel at feel_bpm, in beats of the running engine (the tempo is fixed per
    run). 126 over an 84 engine = 2/3: house beats on a trip-hop grid."""
    return float(engine_bpm) / float(feel_bpm)


def notes(items, ratio=1.0):
    """[(beat, pitch, dur, vel)] -> notation for live_queue. ratio maps the beats onto the engine grid (metric)."""
    return '; '.join(f"{t * ratio:.4f} {p} {max(d * ratio, 0.02):.4f} {int(v)}"
                     for t, p, d, v in sorted(items, key=lambda x: x[0]))


def clip_bars(feel_bars, ratio=1.0, beats_per_bar=4):
    """Engine bars for a clip that holds feel_bars of a feel at `ratio` (36 house bars on an 84 grid = 24)."""
    return feel_bars * beats_per_bar * ratio / beats_per_bar


# ------------------------------------------------------------------ moves

def _at(bar):
    return bar if isinstance(bar, str) else f"bar:{round(float(bar), 4):g}"


def move(target, fx, params, beats=0, at='now'):
    """One live_fx move. fx: the effect's type ('filter', 'delay:2') or a 0-based index; at: a bar number
    (16.5 = half way into bar 16) or a live_queue value."""
    return {'target': target, 'index': fx, 'params': params, 'ramp_beats': beats, 'at': _at(at)}


def sweep(target, fx, param, to, beats, at):
    """A param gliding to `to` over `beats` (a filter opening over a build)."""
    return move(target, fx, {param: to}, beats, at)


def throw(target, at, fx='delay', param='mix', peak=0.5, back=0.15, hold_beats=2, release_beats=4):
    """A dub throw: the send jumps up at `at` (the end of a phrase), holds, and eases back."""
    a = float(at) if not isinstance(at, str) else None
    if a is None:
        raise ValueError("throw: at is a bar number (the release is placed after it)")
    return [move(target, fx, {param: peak}, 0, a),
            move(target, fx, {param: back}, release_beats, a + hold_beats / 4)]


def gap(targets, bar, length=1 / 3, depth_db=-60, fx='gain', level_db=0.0):
    """Silence before a drop: every target's gain cut for the last `length` of the bar before `bar`, back on the
    downbeat. Measured: a drop lands when the bar before it is empty (-27 dB, no sub) and the drop is 8 dB up.
    level_db: the gain each target comes back to, a number or {target: dB}. A track run above 0 dB on this gain
    must come back to that level, or the drop lands under its build (`Set.gap` passes the levels it has set)."""
    out = []
    for t in targets:
        back = level_db.get(t, 0.0) if isinstance(level_db, dict) else level_db
        out += [move(t, fx, {'gain_db': depth_db}, 0, bar - length), move(t, fx, {'gain_db': back}, 0, bar)]
    return out


def pump(target, at, depth_db=-8, fx='duck'):
    """Set a duck's depth (the kick pumping a pad or bass) from `at`; 0 lets go."""
    return move(target, fx, {'depth_db': depth_db}, 0, at)


def with_gain(chain):
    """The chain with a gain first: fades and gaps then work on any track by type ('gain')."""
    return [{'type': 'gain', 'gain_db': 0}] + list(chain)


# ------------------------------------------------------------------ plumbing

def _no_sections(err):
    """The engine is older than section tags (it names 'section' an unknown key)."""
    return 'unknown keys' in str(err) and "'section'" in str(err)


class Set:
    """The live ops for one set folder, logged. Every call appends to set/setlog.md (time, what, the reply);
    queue calls record the clip ids under a section name, so `cancel(section)` can take a queued section back."""

    def __init__(self, folder, ops=None, log='set/setlog.md'):
        if ops is None:
            from ..api import OPS as ops
        self.P = os.path.abspath(folder)
        self.O = ops
        self.log_path = os.path.join(self.P, log)
        self.sections = {}
        self.gains = {}             # {target: gain_db} last set on its 'gain' effect through this Set (for gap)

    def log(self, txt):
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, 'a', encoding='utf8') as f:
            f.write(time.strftime('%H:%M:%S ') + txt + '\n')

    def start(self, bpm, device='default', trim_db=None, cap_db=None, min_free_gb=2.0, priority='above_normal'):
        """live_start at set loudness (trim 4, cap -12, about -16 LUFS, unless given here or set in the environment),
        with the pre-flight in the reply: other engines still holding a device, and free disk for a recording."""
        if trim_db is None and 'ISMAIL_LIVE_TRIM_DB' not in os.environ:      # set there: the engine reads it
            trim_db = 4
        if cap_db is None and 'ISMAIL_LIVE_CAP_DB' not in os.environ:
            cap_db = -12
        r = self.O['live_start'](self.P, bpm=bpm, device=device, trim_db=trim_db, cap_db=cap_db, priority=priority)
        free = shutil.disk_usage(self.P).free / 1e9
        if free < min_free_gb:
            r = f"LOW DISK: {free:.1f} GB free (a recording takes ~1 GB an hour): skip live_record\n" + r
        self.log(f"start {bpm} BPM device {device}: {r.splitlines()[0]}")
        return r

    def q(self, clips, section=None, cut=None, cut_at=None):
        """Queue clips for a section: each clip carries the section's name in the engine too, so `cut` (or a later
        `cut(section)`) can take back what it still has to do. cut: the section this batch cuts over from (its clips
        still to come or playing, stops and effect moves go from where this batch lands, or from cut_at)."""
        if section:
            clips = [c if 'section' in c else dict(c, section=section) for c in clips]
        kw = {k: v for k, v in (('cancel_section', cut), ('cancel_at', cut_at)) if v is not None}
        try:
            r = self.O['live_queue'](self.P, clips, **kw)
        except Exception as e:
            if not (section and not kw and _no_sections(e)):
                raise
            # an engine started before sections (a set already on air): queue untagged, as before
            clips = [{k: v for k, v in c.items() if k != 'section'} for c in clips]
            r = self.O['live_queue'](self.P, clips)
        ids = re.findall(r'\b(c\d+) \S+: bar', r)
        if section:
            self.sections.setdefault(section, []).extend(ids)
        self.log(f"queue [{section or '-'}]" + (f" cutting [{cut}]" if cut else '') + f" {ids}: " +
                 json.dumps([{k: v for k, v in c.items() if k not in ('notes', 'lanes')} for c in clips]) + '\n' + r)
        return r

    def cut(self, section, at='next_bar'):
        """Cut a section off mid-chapter: from `at` its clips go (playing ones are cut there), and its stops and
        its moves sent with section= are dropped. Nothing else is queued: follow with q() or queue in the same call
        with q(clips, cut=section)."""
        r = self.O['live_queue'](self.P, [], cancel_section=section, cancel_at=_at(at))
        self.log(f"cut [{section}] at {at}: {r.splitlines()[0]}")
        return r

    def cancel(self, section):
        """Take back a section's clips that have not started (a playing one needs a stop or a replacement; `cut`
        does both, and takes its moves too)."""
        ids = self.sections.get(section, [])
        if not ids:
            return f"no clips logged for section {section!r}"
        try:
            r = self.O['live_cancel'](self.P, ids)
        except Exception as e:                          # some ids already played: cancel the rest one by one
            done = []
            for i in ids:
                try:
                    self.O['live_cancel'](self.P, [i])
                    done.append(i)
                except Exception:
                    pass
            r = f"cancelled {done or 'none'} ({e})"
        self.log(f"cancel [{section}]: {r}")
        return r

    def _note_gain(self, target, fx, params):
        if fx in ('gain', 'gain:0') and isinstance(params, dict) and 'gain_db' in params:
            self.gains[target] = float(params['gain_db'])

    def track(self, name, **kw):
        for f in kw.get('fx') or []:
            if isinstance(f, dict) and f.get('type') == 'gain':
                self.gains[name] = float(f.get('gain_db', 0.0))
                break
        r = self.O['live_track'](self.P, name, **kw)
        self.log(f"track {name} {({k: v for k, v in kw.items() if k != 'instrument'})}: {r}")
        return r

    def fx(self, target, fx, params, beats=0, at='now'):
        self._note_gain(target, fx, params)
        r = self.O['live_fx'](self.P, target, fx, params, ramp_beats=beats, at=_at(at))
        self.log(f"fx {target}[{fx}] {params} over {beats} at {at}: {r}")
        return r

    def moves(self, moves, section=None):
        """A whole choreography in one call (flattens nested lists from throw/gap). section: the moves belong to it
        (`cut` takes them back)."""
        flat = []
        for m in moves:
            flat += m if isinstance(m, list) else [m]
        if section:
            flat = [m if 'section' in m else dict(m, section=section) for m in flat]
        for m in flat:
            self._note_gain(m.get('target'), m.get('index'), m.get('params'))
        try:
            r = self.O['live_fx'](self.P, moves=flat)
        except Exception as e:
            if not (section and _no_sections(e)):
                raise
            # an engine from before sections rejects the first move, so none were scheduled: send them untagged
            r = self.O['live_fx'](self.P, moves=[{k: v for k, v in m.items() if k != 'section'} for m in flat])
        self.log(f"moves ({len(flat)}): {r.splitlines()[0]}")
        return r

    def gap(self, targets, bar, **kw):
        """`gap` sent now, each target coming back to the gain this Set last gave it (0 dB if none)."""
        return self.moves(gap(targets, bar, level_db={t: self.gains.get(t, 0.0) for t in targets}, **kw))

    def clear(self, target, fx=None, at='now'):
        r = self.O['live_fx'](self.P, target, fx, clear=True, at=_at(at))
        self.log(f"clear {target} {fx if fx is not None else ''} at {at}: {r}")
        return r

    def status(self, short=True):
        st = self.O['live_status'](self.P)
        if short:
            st = '\n'.join(ln for ln in st.splitlines()
                           if ln.startswith(('live ', 'STALLED', 'safety', '  ', 'render', 'mixer', 'headroom',
                                             'runway', 'NEW', 'output off', 'sections')))
        return st

    def heard_bar(self):
        return int(re.search(r'heard bar (\d+)', self.O['live_status'](self.P)).group(1))

    def levels(self):
        """{track or 'bus <name>': (level now, loudest over the last 10 s)}: judge a sparse part by the second."""
        out = {}
        for ln in self.O['live_status'](self.P).splitlines():
            m = re.match(r'\s+(bus \S+|\S+)\s.*?level\s+(-?[\d.]+) dBFS \(max \d+ s\s+(-?[\d.]+)\)', ln)
            if m:
                out[m.group(1)] = (float(m.group(2)), float(m.group(3)))
        return out

    def next_boundary(self, start, every, margin=2):
        """The first bar start + k*every at least `margin` bars after what is heard now: where a cycle-aligned
        change can still land in time."""
        heard = self.heard_bar()
        k = max(0, -(-(heard + margin - start) // every))
        return start + k * every
