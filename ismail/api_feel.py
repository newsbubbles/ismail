"""Per-note feel against a reference (ledger:M364): every note's timing and level against its own beat, read on a
beat line that follows the record, so a drifting or rubato take stays locked (the project plays one tempo, M258;
real records drift, M287b). analyze_feel measures it, feel_from_ref writes it into a track's notes.

What makes a draft "sound MIDI" is measurable here: 8ths placed by the grid (the record's offbeat sits off the middle
of its beat), every note at one velocity or accented by a rule (beat 1 loudest), timing that never varies from note
to note. Ported from a song's own scripts (a baroque concerto, draft 3, 2026-10-26): written from the record, the
tutti's offbeat 8th moved from 0.532 to 0.510 of the beat (record 0.507), its beat 1 / beat 2 levels from +2.4 / +1.6
to +0.1 / +3.3 dB (record -0.9 / +2.9), the solo's timing spread from 33 to 43 ms (record 46).
"""
import math
import os
import re

import numpy as np

from . import analysis as A
from .api import op, OpError, _load, _voice_root
from .notation import midi_to_name

SR = A.ASR
HOP = 64                 # onset curve frames, ~2.9 ms at 22050 Hz
BEAT_WIN = 0.15          # a followed beat snaps to the strongest onset within this share of a beat of its prediction
CLEAR = 1.5              # an onset counts when it stands this many times over the local median of the onset curve
FLOOR_SHARE = 0.4        # ... and reaches this share of the median onset on the beats
CTX_S = 0.5              # the local median is read over +-this many seconds
LEVEL_SPAN = 0.6         # a slot's level is its peak over its first 60 %
LEVEL_CLIP = 6.0         # a slot's level against its bar's mean is clipped to +-6 dB
FRAME_S = 0.01           # level frames
FRAME_HOP_S = 0.0025     # level frames start this often, so a fast decay reads the same wherever a note starts
ON_SLOT = 0.2            # a note falls on a slot when its start is within this share of a slot of it
DEFAULT_BAND = (600.0, 4000.0)
SUBS = {2: ['&'], 4: ['e', '&', 'a']}


class _Read:
    """One source read on its own followed beat line over bars [a, b]: the slots with their onsets and levels."""

    def __init__(self, P, source, bars, per_beat, band, window_ms):
        import librosa
        from scipy import signal
        self.source, self.per, self.band = source, per_beat, band
        path, g = P.source(source, bars)
        self.g, self.bpb = g, P.bpb
        a, b = bars
        self.kb0, self.kb1 = (a - 1) * P.bpb, b * P.bpb          # project beats of the bars, end exclusive
        y = A.load(path)
        t_end = g.bar_time(b + 2) * 1.12 + 2.0                    # room for a record slower than the grid
        y = y[:max(0, min(len(y), int(t_end * SR)))]
        if len(y) < SR:
            raise OpError(f"{source}: under a second of audio up to bar {b + 1}; check bars and the project grid "
                          f"(analyze_grid)")
        full = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, lag=2)
        lo, hi = band
        self.on = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP, lag=2, fmin=lo, fmax=hi)
        self.t_on = librosa.times_like(full, sr=SR, hop_length=HOP)
        bp = signal.sosfiltfilt(signal.butter(4, [lo, hi], btype='band', fs=SR, output='sos'), y)
        h, step = int(FRAME_S * SR), int(FRAME_HOP_S * SR)       # 10 ms frames every 2.5 ms
        cs = np.r_[0.0, np.cumsum(bp.astype(np.float64) ** 2)]
        i = np.arange(0, max(len(bp) - h, 0) + 1, step)
        self.lev = 10 * np.log10((cs[i + h] - cs[i]) / h + 1e-12)
        self.t_lev = (i + 0.5 * h) / SR
        self._follow(full, self.kb1)
        self.S = self._smooth()
        self._slots(window_ms / 1000.0)

    def _smooth(self):
        """The tempo line: each beat's place on a curve fitted to the followed beats within 4 beats of it (a
        9-beat window, quadratic; lost beats' predictions do not bend it)."""
        n = len(self.B)
        idx = np.arange(n)
        ok = np.array([k not in self.lost for k in range(self.k0, self.k0 + n)])
        S = self.B.astype(float).copy()
        for i in range(n):
            m = ok & (np.abs(idx - i) <= 4)
            if m.sum() >= 3:
                S[i] = np.polyval(np.polyfit(idx[m] - i, self.B[m], 2 if m.sum() >= 5 else 1), 0)
        return S

    # ---------------------------------------------------------------- the beat line
    def _follow(self, full, k_end):
        """Follow every beat from bar 1 (the project grid and its bar 1 offset) to beat k_end: predict a period on,
        snap to the strongest onset within BEAT_WIN of a beat when it stands clear, let the period follow slowly.
        A beat with no clear onset keeps its prediction and is counted lost."""
        g, t = self.g, self.t_on
        spb = g.spb
        k0 = max(0, math.ceil(-g.offset / spb - 1e-9))
        floor = 0.1 * float(np.percentile(full, 99))
        per, B, lost = spb, [], set()
        last, last_ok, missed = None, False, 0
        for k in range(k0, k_end + 1):
            pred = g.offset + k * spb if last is None else last + per
            # the first beats, and a beat after two lost ones, look wider (a rough bar 1, a pause, a held chord)
            w = (0.25 if k - k0 < 2 or missed >= 2 else BEAT_WIN) * per
            i0, i1 = np.searchsorted(t, [pred - w, pred + w])
            ok = False
            if i1 > i0:
                i = i0 + int(np.argmax(full[i0:i1]))
                c0, c1 = np.searchsorted(t, [pred - 2 * CTX_S, pred + 2 * CTX_S])
                if full[i] > CLEAR * np.median(full[c0:c1]) and full[i] > floor:
                    bt, ok = float(t[i]), True
                    if last is not None and last_ok:
                        per = float(np.clip(0.85 * per + 0.15 * (bt - last), 0.9 * spb, 1.1 * spb))
            if not ok:
                bt = pred
                lost.add(k)
            missed = 0 if ok else missed + 1
            B.append(bt)
            last, last_ok = bt, ok
        if len(B) < 2:
            raise OpError(f"{self.source}: the audio starts after the bars asked for; pick bars inside it")
        self.k0, self.k1, self.B, self.lost = k0, k_end, np.array(B), lost

    def beat(self, k):
        return self.B[k - self.k0], self.S[k - self.k0]

    # ---------------------------------------------------------------- the slots
    def _onset_near(self, e, win):
        """(time, height) of the strongest onset-curve peak within win of e when it stands CLEAR times over the
        local median, else (None, height)."""
        t, on = self.t_on, self.on
        i0, i1 = np.searchsorted(t, [e - win, e + win])
        if i1 <= i0:
            return None, 0.0
        i = i0 + int(np.argmax(on[i0:i1]))
        c0, c1 = np.searchsorted(t, [e - CTX_S, e + CTX_S])
        return (float(t[i]) if on[i] > CLEAR * np.median(on[c0:c1]) else None), float(on[i])

    def _slots(self, win):
        """Every slot of the bars: its beat k, place j in the beat, even place e between the followed beats, the
        record's onset t (None: no clear onset), its slot length, its place s on the smoothed line and that beat's
        length there, its peak level in the band and that against its bar's mean."""
        per = self.per
        t_last = self.t_on[-1] if len(self.t_on) else 0.0
        i0, i1 = np.searchsorted(self.t_on, [self.g.bar_time(self.kb0 / self.bpb + 1) - 1,
                                             self.g.bar_time(self.kb1 / self.bpb + 1) + 1])
        seg = self.on[i0:i1] if i1 > i0 else self.on
        floor = 0.05 * float(np.percentile(seg, 99)) if len(seg) else 0.0
        raw = []
        self.slots = []
        self.unread = []                                   # beats of the bars outside the file or not followed
        for k in range(self.kb0, self.kb1):
            if k < self.k0 or k + 1 > self.k1 or self.beat(k + 1)[0] > t_last:
                self.unread.append(k)
                continue
            (bk, sk), (bk1, sk1) = self.beat(k), self.beat(k + 1)
            if k in self.lost:
                self.unread.append(k)
                continue
            for j in range(per):
                e = bk + (bk1 - bk) * j / per
                t, hgt = self._onset_near(e, win)
                raw.append(hgt if t is not None and j == 0 else None)
                self.slots.append({'k': k, 'j': j, 'e': e, 't': t, 'h': hgt,
                                   'dur': (bk1 - bk) / per, 's': sk + (sk1 - sk) * j / per, 'beat_s': sk1 - sk,
                                   'B': bk, 'B1': bk1})
        # where the curve sits near zero between notes (a sparse part, a quiet room) the median says little: an
        # onset also has to reach FLOOR_SHARE of the typical onset on the beats
        beats = [h for h in raw if h is not None]
        floor = max(floor, FLOOR_SHARE * float(np.median(beats))) if beats else floor
        for sl in self.slots:
            if sl['t'] is not None and sl['h'] <= floor:
                sl['t'] = None
        # in order and at least half a slot apart: an onset next to its neighbour's is that neighbour's note
        for i, sl in enumerate(self.slots):
            if sl['t'] is None:
                continue
            prev = self.slots[i - 1] if i else None
            nxt = self.slots[i + 1] if i + 1 < len(self.slots) else None
            if prev is not None and prev['k'] * per + prev['j'] == sl['k'] * per + sl['j'] - 1:
                pt = prev['t'] if prev['t'] is not None else prev['e']
                if sl['t'] - pt < 0.5 * sl['dur']:
                    sl['t'] = None
                    continue
            if nxt is not None and nxt['k'] * per + nxt['j'] == sl['k'] * per + sl['j'] + 1:
                nt = nxt['t'] if nxt['t'] is not None else nxt['e']
                if nt - sl['t'] < 0.5 * sl['dur']:
                    sl['t'] = None
        for sl in self.slots:
            st = sl['t'] if sl['t'] is not None else sl['e']
            m = (self.t_lev >= st) & (self.t_lev < st + LEVEL_SPAN * sl['dur'])
            sl['peak'] = float(self.lev[m].max()) if m.any() else None
        bars = {}
        for sl in self.slots:
            if sl['t'] is not None and sl['peak'] is not None:
                bars.setdefault(sl['k'] // self.bpb, []).append(sl['peak'])
        for sl in self.slots:
            mu = bars.get(sl['k'] // self.bpb)
            sl['rel'] = (float(np.clip(sl['peak'] - np.mean(mu), -LEVEL_CLIP, LEVEL_CLIP))
                         if mu and sl['peak'] is not None else None)
        # the part's own lag against the beat line (band onsets against the whole mix's beats): taken out, so
        # only each note's place against its own beat is written
        lag = [sl['t'] - sl['s'] for sl in self.slots if sl['j'] == 0 and sl['t'] is not None]
        self.lag = float(np.median(lag)) if lag else 0.0
        for sl in self.slots:
            sl['dev_beats'] = (None if sl['t'] is None else (sl['t'] - self.lag - sl['s']) / sl['beat_s'])
        self.by = {(sl['k'], sl['j']): sl for sl in self.slots}

    # ---------------------------------------------------------------- the measures
    def label(self, sl):
        return str(sl['k'] % self.bpb + 1) if sl['j'] == 0 else SUBS[self.per][sl['j'] - 1]

    def labels(self):
        return [str(i + 1) for i in range(self.bpb)] + SUBS[self.per]

    def tempo(self):
        """(min, max) BPM of the followed line over the bars, 4-beat median."""
        i0, i1 = max(self.kb0, self.k0) - self.k0, min(self.kb1, self.k1) - self.k0
        loc = 60 / np.diff(self.S[i0:i1 + 1]) if i1 > i0 else np.array([self.g.bpm])
        roll = [np.median(loc[max(0, i - 2):i + 2]) for i in range(len(loc))]
        return float(min(roll)), float(max(roll))

    def measures(self):
        per, mid = self.per, self.per // 2
        between, share = [], []
        for k in sorted({sl['k'] for sl in self.slots}):
            s0 = self.by.get((k, 0))
            d0 = (s0['t'] - s0['e']) if s0 and s0['t'] is not None else 0.0
            for j in range(1, per):
                sl = self.by.get((k, j))
                if sl and sl['t'] is not None:
                    between.append((sl['t'] - sl['e'] - d0) * 1000)
            sm = self.by.get((k, mid))
            if s0 and sm and s0['t'] is not None and sm['t'] is not None:
                share.append((sm['t'] - s0['t']) / (s0['B1'] - s0['B']))
        on = [sl for sl in self.slots if sl['t'] is not None and sl['rel'] is not None]
        pos = {}
        for sl in on:
            pos.setdefault(self.label(sl), []).append(sl['rel'])
        notes = [sl for sl in self.slots if sl['t'] is not None and sl['peak'] is not None]
        steps = [b['peak'] - a['peak'] for a, b in zip(notes, notes[1:])]       # note to note, rests skipped
        return {'timing_sd': float(np.std(between)) if len(between) >= 3 else None,
                'offbeat': float(np.mean(share)) if share else None,
                'by_pos': {p: float(np.mean(v)) for p, v in pos.items()},
                'step_sd': float(np.std(steps)) if len(steps) >= 3 else None,
                'notes': len(on), 'slots': len(self.slots), 'between': len(between)}

    def lost_text(self):
        """'' or one line naming the beats of the bars it could not follow, and where it lost the beat."""
        bad = sorted(set(self.unread) | {k for k in self.lost if self.kb0 <= k < self.kb1})
        if not bad:
            return ''
        nb = self.kb1 - self.kb0
        names = [f"{k // self.bpb + 1}.{k % self.bpb + 1}" for k in bad]
        txt = (f"  beats it could not follow ({len(bad)} of {nb}, bar.beat): {' '.join(names[:16])}"
               + (f" ... {len(names) - 16} more" if len(names) > 16 else '') +
               "; their notes are left alone")
        run = 0
        for k in range(self.kb0, self.kb1):
            run = run + 1 if k in bad else 0
            if run == 4:
                s = k - 3
                txt += (f"\n  LOST THE BEAT from bar {s // self.bpb + 1} beat {s % self.bpb + 1} (4 beats in a row "
                        f"with no clear onset): check bpm and offset_sec with analyze_grid, or read a stem with a clear "
                        f"pulse (source='ref:<stem>')")
                break
        return txt


def _band(band):
    if band is None:
        return DEFAULT_BAND
    try:
        lo, hi = float(band[0]), float(band[1])
    except (TypeError, ValueError, IndexError):
        raise OpError(f"band is [lo, hi] in Hz, got {band!r}; default [600, 4000]")
    if len(band) != 2 or not 20 <= lo < hi <= 0.48 * SR:
        raise OpError(f"band is [lo, hi] in Hz with 20 <= lo < hi <= {0.48 * SR:.0f}, got {band!r}; "
                      f"default [600, 4000] (a violin or a lead), [6000, 10000] for hats")
    return lo, hi


def _check(P, bars, per_beat):
    if per_beat not in (2, 4):
        raise OpError(f"per_beat is 2 (8ths) or 4 (16ths), got {per_beat!r}")
    if not (isinstance(bars, (list, tuple)) and len(bars) == 2 and 1 <= int(bars[0]) <= int(bars[1])):
        raise OpError(f"bars is [a, b] (inclusive, from 1), got {bars!r}")
    return [int(bars[0]), int(bars[1])]


def _short(source):
    """A source as a column head: a file path by its name."""
    return os.path.basename(source) if ('/' in source or os.sep in source) else source


def _fmt(v, f):
    return '-' if v is None else format(v, f)


def _pos_line(by, labels):
    return '  '.join(f"{p} {by[p]:+.1f}" for p in labels if p in by)


@op()
def analyze_feel(project: str, source: str, bars: list, per_beat: int = 2, band: list = None,
                 against: str = None, window_ms: float = 50.0) -> str:
    """The per-note feel of a part: what makes a draft "sound MIDI" or "mechanical", measured on a beat line that
    follows the record (a drifting or rubato take stays locked). For every slot of bars [a, b] (the beats, and the
    8ths (per_beat=2) or 16ths (per_beat=4) spread evenly between the followed beats) it reads the onset within
    window_ms in `band` ([lo, hi] Hz, default [600, 4000]) and the slot's peak level there, then prints: the timing
    spread (sd, ms) of the notes between the beats with each beat's own offset taken out; where the offbeat 8th sits
    as a share of the beat (0.500 = even); the mean level by metric position against its bar's mean (beat 1, 2, ...,
    and the e, &, a); the sd of note-to-note level steps; the beats it could not follow. against='ref' (with
    source='render' or 'track:<name>') prints both columns and the differences, and names the ones that read as
    MIDI-like. Reach for it when a draft sounds MIDI, mechanical, robotic, stiff or quantized against a reference,
    or to check feel_from_ref's result. Read a stem (ref:<stem>, track:<name>) when the part shares its band with
    others."""
    P = _load(project)
    bars = _check(P, bars, per_beat)
    band = _band(band)
    reads = [(source, _Read(P, source, bars, per_beat, band, window_ms))]
    if against:
        reads.append((against, _Read(P, against, bars, per_beat, band, window_ms)))
    ms = [(_short(s), r, r.measures()) for s, r in reads]
    r0 = reads[0][1]
    L = [f"feel of bars {bars[0]}-{bars[1]}, {per_beat} slots a beat, onsets and levels in {band[0]:.0f}-"
         f"{band[1]:.0f} Hz, each source on its own followed beat line:"]
    for s, r, m in ms:
        lo, hi = r.tempo()
        L.append(f"  {s}: {m['notes']} notes on {m['slots']} slots; beat line {lo:.1f}-{hi:.1f} BPM")
        lt = r.lost_text()
        if lt:
            L.append(lt.replace('; their notes are left alone', ''))
    labels = r0.labels()
    if len(ms) == 1:
        m = ms[0][2]
        L += [f"  timing spread between the beats: {_fmt(m['timing_sd'], '.1f')} ms sd ({m['between']} notes)",
              f"  offbeat 8th at {_fmt(m['offbeat'], '.3f')} of the beat (0.500 = even)",
              f"  level by position, dB against the bar's mean: {_pos_line(m['by_pos'], labels) or '-'}",
              f"  note-to-note level steps: {_fmt(m['step_sd'], '.1f')} dB sd",
              "Next: analyze_feel(against='ref') beside the reference, or feel_from_ref(track, bars) to write the "
              "reference's feel into a part."]
        return '\n'.join(L)
    (sa, _, a), (sb, _, b) = ms

    def d(x, y, f):
        return '-' if x is None or y is None else format(x - y, f)
    w = max(12, len(sa), len(sb))
    L.append(f"  {'':<26}{sa:>{w}}{sb:>{w}}{'difference':>{w}}")
    L.append(f"  {'timing sd between beats ms':<26}{_fmt(a['timing_sd'], '.1f'):>{w}}{_fmt(b['timing_sd'], '.1f'):>{w}}"
             f"{d(a['timing_sd'], b['timing_sd'], '+.1f'):>{w}}")
    L.append(f"  {'offbeat 8th, share of beat':<26}{_fmt(a['offbeat'], '.3f'):>{w}}{_fmt(b['offbeat'], '.3f'):>{w}}"
             f"{d(a['offbeat'], b['offbeat'], '+.3f'):>{w}}")
    for p in labels:
        x, y = a['by_pos'].get(p), b['by_pos'].get(p)
        if x is None and y is None:
            continue
        L.append(f"  {'level on ' + p + ', dB vs bar':<26}{_fmt(x, '+.1f'):>{w}}{_fmt(y, '+.1f'):>{w}}"
                 f"{d(x, y, '+.1f'):>{w}}")
    L.append(f"  {'level step sd dB':<26}{_fmt(a['step_sd'], '.1f'):>{w}}{_fmt(b['step_sd'], '.1f'):>{w}}"
             f"{d(a['step_sd'], b['step_sd'], '+.1f'):>{w}}")
    midi = []
    if a['timing_sd'] is not None and b['timing_sd'] is not None and b['timing_sd'] > 8 and \
            a['timing_sd'] < 0.6 * b['timing_sd']:
        midi.append(f"timing too even: {a['timing_sd']:.0f} ms sd between the beats against {b['timing_sd']:.0f} "
                    f"(notes on the grid)")
    if a['offbeat'] is not None and b['offbeat'] is not None and abs(a['offbeat'] - b['offbeat']) >= 0.01:
        what = ('placed by the grid' if abs(a['offbeat'] - 0.5) < abs(b['offbeat'] - 0.5) else
                'pushed off the middle by something else (an attack or a delay?)')
        midi.append(f"offbeat 8th at {a['offbeat']:.3f} of the beat against {b['offbeat']:.3f}: {what}")
    if a['step_sd'] is not None and b['step_sd'] is not None and a['step_sd'] < 0.7 * b['step_sd'] - 0.3:
        midi.append(f"levels too even: steps {a['step_sd']:.1f} dB sd against {b['step_sd']:.1f} (one velocity)")
    off = [(p, a['by_pos'][p] - b['by_pos'][p]) for p in labels if p in a['by_pos'] and p in b['by_pos']
           and abs(a['by_pos'][p] - b['by_pos'][p]) >= 1.5]
    if off:
        midi.append("accents by rule, not the record's: " + ', '.join(f"{p} {x:+.1f} dB" for p, x in off))
    if midi:
        L.append(f"reads as MIDI-like in {sa}:")
        L += [f"  - {x}" for x in midi]
        L.append(f"Next: feel_from_ref(track=<the part>, bars={bars}, source='{against}', per_beat={per_beat}) writes "
                 f"{sb}'s timing and level into its notes (dry_run=True first), then render and run this again.")
    else:
        L.append("no difference reads as MIDI-like (timing within 40 %, offbeat within 0.01, levels within 1.5 dB)")
    return '\n'.join(L)


def _vel_law(project, tr):
    """(scale velocities?, a line on how this track's instrument reads velocity, or '')."""
    inst = tr.get('instrument') or {}
    t = inst.get('type')
    from .instruments import DRUM_DEFAULTS

    def sens(vs):
        part = vs * 100 / 127 / ((1 - vs) + vs * 100 / 127)
        return (f"velocity_sens {vs:g}: level follows (1 - {vs:g}) + {vs:g} x velocity, so at velocity 100 a "
                f"velocity change moves the level about {part:.0%} of its dB; raise level= to make up, and check with "
                f"analyze_feel")
    if t == 'mimic' or t is None:
        return True, ''
    if t in ('synth', 'sprite'):
        return True, f"{t}: " + sens(float(inst.get('velocity_sens', 0.5)))
    if t == 'sampler':
        line = 'sampler: ' + sens(float(inst.get('velocity_sens', 0.5)))
        if inst.get('select') == 'velocity':
            line += "; velocity also picks the sound by its range, so a changed velocity can switch the sample"
        return True, line
    if t in DRUM_DEFAULTS:
        return True, f"{t}: " + sens(0.7)
    if t == 'kit':
        return True, "kit: every piece has its own velocity law; check the result with analyze_feel"
    if t == 'code' and inst.get('voice'):
        from . import voices
        name = inst['voice']
        try:
            meta = voices.info(name, _voice_root(project))[2]
        except Exception:
            return True, f"voice {name}: not found to read its velocity law; voice_help(name='{name}')"
        txt = str(meta.get('velocity', '') or '')
        if re.search(r'\bcode\b|ignored|picks the effect', txt):
            return False, (f"voice {name}: velocity is {txt[:90]!r}, not a level: velocities left alone, timing "
                           f"written")
        if txt and txt.strip().lower() != 'level':
            return True, (f"voice {name}: velocity is {txt[:90]!r}, not only level; voice_help(name='{name}') prints "
                          f"its level per velocity")
    return True, ''


def _share(pos, per, mid):
    """The offbeat 8th's mean place as a share of the beat, from {(k, j): position in beats}."""
    sh = [pos[(k, mid)] - pos.get((k, 0), k) for (k, j) in pos if j == mid]
    return float(np.mean(sh)) if sh else None


@op(mutates=True)
def feel_from_ref(project: str, track: str, bars: list, source: str = 'ref', per_beat: int = 2, band: list = None,
                  time: float = 1.0, level: float = 0.7, window_ms: float = 50.0, dry_run: bool = False) -> str:
    """Write a reference's per-note timing and level into a track's notes. It follows the record's beats over bars
    [a, b] from the project grid and its bar 1 (each beat snaps to the strongest nearby onset, so a drifting or
    rubato take stays locked, and it says where it lost the beat), then for every note of `track` on a slot (a beat,
    or the 8ths (per_beat=2) or 16ths (per_beat=4) spread evenly between the followed beats) it reads the record's
    onset within window_ms of the slot in `band` ([lo, hi] Hz, default [600, 4000]: the band the part sounds in),
    and the slot's peak level there over its first 60 % against the mean of its bar (the bar's own loudness stays
    with the score and the faders), clipped to +-6 dB. Each note moves to its slot plus time x the record's offset
    against its own beat (time=1.0: the record's place; written as the note's @offset nudge, so it stays on its
    beat in notes_read) and its velocity scales by 10**(level x dB / 20). The project plays one tempo: the record's
    drift is not written, only each note's place against its beat. Notes on slots with no clear onset, off the
    slots, or on beats it could not follow are left alone. dry_run=True prints what it would change. undo() takes
    it back; run it once per draft (velocities scale again on a second run). Reach for it when a draft sounds MIDI,
    mechanical or quantized against a reference: analyze_feel(source='render', against='ref') first, then this."""
    P = _load(project)
    tr = P.track(track)
    bars = _check(P, bars, per_beat)
    band = _band(band)
    if not 0 <= time <= 2 or not 0 <= level <= 2:
        raise OpError(f"time and level are shares of the record's offset and level: 0 (none) to 1 (all), up to 2; "
                      f"got time={time!r}, level={level!r}")
    b0, b1 = P.bar_to_beat(bars[0]), P.bar_to_beat(bars[1] + 1)
    sel = [n for n in tr['notes'] if b0 <= n[0] < b1]
    if not sel:
        raise OpError(f"no notes of {track} in bars {bars[0]}-{bars[1]}; notes_read(track='{track}') shows where "
                      f"they are")
    R = _Read(P, source, bars, per_beat, band, window_ms)
    scale_vel, law = _vel_law(project, tr)
    spb_ms = 60000.0 / float(P.d['bpm'])
    per, mid = per_beat, per_beat // 2
    off_slot = no_onset = unread = 0
    moved, changes, shifts, vel_changed = 0, [], [], 0
    old_pos, new_pos = {}, {}
    applied = {}
    for n in sel:
        q = n[0] * per
        qi = int(round(q))
        if abs(q - qi) > ON_SLOT:
            off_slot += 1
            continue
        k, j = divmod(qi, per)
        sl = R.by.get((k, j))
        if sl is None:
            unread += 1
            continue
        if sl['t'] is None or sl['dev_beats'] is None:
            no_onset += 1
            continue
        old_off = float(n[4]) if len(n) > 4 and n[4] else 0.0
        old = n[0] + old_off / spb_ms
        new = qi / per + time * sl['dev_beats']
        new_off = round((new - n[0]) * spb_ms, 1)
        old_pos.setdefault((k, j), old)
        new_pos.setdefault((k, j), n[0] + new_off / spb_ms)
        shift = new_off - old_off
        v = n[3]
        if scale_vel and sl['rel'] is not None:
            v = int(np.clip(round(n[3] * 10 ** (level * sl['rel'] / 20)), 1, 127))
            applied.setdefault(R.label(sl), []).append(20 * np.log10(v / max(n[3], 1)))
        if abs(shift) >= 0.05:
            moved += 1
            shifts.append(abs(shift))
        if v != n[3]:
            vel_changed += 1
        if abs(shift) >= 0.05 or v != n[3]:
            changes.append((n[0], n[1], shift, n[3], v))
        if not dry_run:
            del n[4:]
            if abs(new_off) >= 0.05:
                n.append(new_off)
            n[3] = v
    lo, hi = R.tempo()
    m = R.measures()
    verb = 'would move' if dry_run else 'moved'
    L = [f"{track}: feel from {source} over bars {bars[0]}-{bars[1]} ({per} slots a beat, onsets and levels in "
         f"{band[0]:.0f}-{band[1]:.0f} Hz, time={time:g}, level={level:g}{', dry run' if dry_run else ''})",
         f"  beat line: {source}'s beats followed from bar 1, {lo:.1f}-{hi:.1f} BPM over these bars; the project plays "
         f"one tempo ({P.d['bpm']:g}), so the drift is not written, only each note's place against its own beat",
         f"  notes in the band sit {int(round(R.lag * 1000)):+d} ms from the beat line on average; "
         f"taken out (a whole-part lag is align or track_set(offset_ms=))"]
    lt = R.lost_text()
    if lt:
        L.append(lt)
    L.append(f"  {verb} {moved} of {len(sel)} notes, mean |shift| "
             f"{(np.mean(shifts) if shifts else 0.0):.1f} ms (max {(max(shifts) if shifts else 0.0):.0f}); "
             f"{vel_changed} velocities {'would change' if dry_run else 'changed'}")
    sb, sa, sr = _share(old_pos, per, mid), _share(new_pos, per, mid), m['offbeat']
    if sb is not None or sr is not None:
        L.append(f"  offbeat 8th as a share of the beat, in the notes: {_fmt(sb, '.3f')} -> {_fmt(sa, '.3f')} "
                 f"(record {_fmt(sr, '.3f')})")
    labels = R.labels()
    L.append(f"  record's level by position, dB against its bar's mean: {_pos_line(m['by_pos'], labels) or '-'}")
    if applied:
        L.append("  velocity change by position, mean dB: " +
                 _pos_line({p: float(np.mean(v)) for p, v in applied.items()}, labels))
    left = []
    if no_onset:
        left.append(f"{no_onset} on slots with no clear onset")
    if off_slot:
        left.append(f"{off_slot} off the {per}-a-beat slots (per_beat={4 if per == 2 else 2}?)")
    if unread:
        left.append(f"{unread} on beats not followed")
    if left:
        L.append("  left alone: " + ', '.join(left))
    if law:
        L.append(f"  velocity: {law}")
    if dry_run:
        for st, p, sh, v0, v1 in changes[:16]:
            bar, beat = divmod(st, P.bpb)
            L.append(f"    bar {int(bar) + 1} beat {beat + 1:g} {midi_to_name(p)}: {sh:+.1f} ms, vel {v0} -> {v1}")
        if len(changes) > 16:
            L.append(f"    ... {len(changes) - 16} more")
        L.append("dry run: nothing written. Without dry_run=True it writes these; undo() takes them back.")
        return '\n'.join(L)
    tr['notes'].sort()
    P.save()
    L.append(f"written (undo() takes it back; a second run scales velocities again). Next: render, then "
             f"analyze_feel(source='render', against='{source}', bars={bars}, per_beat={per}).")
    return '\n'.join(L)
