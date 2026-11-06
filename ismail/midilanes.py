"""Lanes as MIDI (MIDI parity step 6, Nate 10-23 "full midi like ableton"; plan in
D:/ismail-hq/proposals/midi-parity.md): a performer voice's expression lanes (automation 'inst.lane.<name>': bend in
semitones, vib in cents, level in dB ...) as pitch bend, channel pressure and controllers, in .mid files
(midi_write, midi_read) and from a played input (live_midi).

A lane map is data the agent can override: {lane: {'midi': 'bend' | 'pressure' | 'cc:<n>', 'range': [lo, hi]}}.
'range' is the lane's values at the message's ends: a controller or pressure at 0 is lo and at 127 hi; pitch bend
at its bottom is lo, its centre (lo + hi) / 2 and its top hi, so a bend range is symmetric ([-2, 2] or the number 2,
written as RPN 0 in a file). A lane mapped to None is left out.

MPE (MIDI Polyphonic Expression): each note on its own member channel, with its own pitch bend (48 semitones by
default) and channel pressure. The lower zone has channel 1 as its master and members from channel 2 up; the upper
zone has channel 16 as its master and members from channel 15 down. An MPE Configuration Message (RPN 6 on a
master channel) says how many members a zone has; with both zones they share the 14 channels between the masters.
ismail's lanes are per track, not per note, so an MPE file read in follows the newest sounding note (last-note
priority), and an MPE file written out gives each note the track's lane over its own span.
"""
import math

FILE = {'bend': {'midi': 'bend', 'range': [-2.0, 2.0]},       # GM's default bend range; RPN 0 says it in the file
        'vib': {'midi': 'cc:1', 'range': [0.0, 50.0]},        # the mod wheel: vibrato depth, cents
        'level': {'midi': 'cc:11', 'range': [-40.0, 0.0]},    # expression: 127 is the fader, as DAWs draw it
        'breath': {'midi': 'cc:2', 'range': [0.0, 1.0]}}      # the breath controller
FILE_MPE = {**FILE, 'bend': {'midi': 'bend', 'range': [-48.0, 48.0]},   # the MPE default per-note bend
            'level': {'midi': 'pressure', 'range': [-40.0, 0.0]}}       # MPE's per-note pressure is loudness
LIVE = {'bend': {'midi': 'bend', 'range': [-2.0, 2.0]},
        'vib': {'midi': 'cc:1', 'range': [0.0, 50.0]},
        'level': {'midi': 'pressure', 'range': [0.0, 6.0]}}   # a boost: a keyboard's pressure at rest is 0
LIVE_MPE = {**LIVE, 'bend': {'midi': 'bend', 'range': [-48.0, 48.0]}}
LIVE_LANES = ('bend', 'vib', 'level')       # what a key rendered ahead can still do: pitch, vibrato, gain
MPE_MASTER = 0                              # channel 1, the lower zone's master
MPE_UPPER = 15                              # channel 16, the upper zone's master
ZONES = {'lower': MPE_MASTER, 'upper': MPE_UPPER}
MPE_BEND = 48.0
# controllers that mean something else to every synth: bank select, data entry and RPN/NRPN, the pedals, the
# channel-mode messages
RESERVED_CC = {0, 6, 32, 38, 64, 65, 66, 67, 96, 97, 98, 99, 100, 101} | set(range(120, 128))
STEP = 1 / 64                               # beats between written messages along a ramp


class LaneMapError(ValueError):
    pass


def parse_midi(text):
    """'bend' | 'pressure' | 'cc:<n>' -> ('bend', None) | ('pressure', None) | ('cc', n)."""
    s = str(text).strip().lower().replace(' ', '')
    if s in ('bend', 'pressure'):
        return s, None
    if s.startswith('cc:') and s[3:].isdigit():
        n = int(s[3:])
        if not 0 <= n <= 127:
            raise LaneMapError(f"{text!r}: controllers are cc:0 to cc:127")
        if n in RESERVED_CC:
            raise LaneMapError(f"{text!r} means something else to every synth (bank select, data entry, RPN, the "
                               f"pedals or a channel mode message); pick another, e.g. cc:1 (mod wheel), cc:2 "
                               f"(breath), cc:11 (expression), cc:74 (MPE timbre) or one of cc:20 to cc:31")
        return 'cc', n
    raise LaneMapError(f"midi {text!r}: 'bend', 'pressure' or 'cc:<n>' (cc:1 mod wheel, cc:11 expression)")


def midi_name(kind, num):
    return f"cc:{num}" if kind == 'cc' else kind


def resolve(lanes, base, allowed=None):
    """The lane map in use: `base` with the agent's `lanes` over it ({lane: spec or None}; a spec may give only
    'midi' or only 'range'). Returns {lane: (kind, num, lo, hi)}. allowed: the only lane names it may hold."""
    if lanes is not None and not isinstance(lanes, dict):
        raise LaneMapError("lanes: {lane: {'midi': 'bend' | 'pressure' | 'cc:<n>', 'range': [lo, hi]}} or "
                           "{lane: None} to leave one out, e.g. {'mute': {'midi': 'cc:20', 'range': [0, 1]}}")
    merged = {k: dict(v) for k, v in base.items()}
    for lane, spec in (lanes or {}).items():
        lane = str(lane)
        if lane.startswith('inst.lane.'):
            lane = lane[10:]
        if spec is None:
            merged.pop(lane, None)
            continue
        if isinstance(spec, str):
            spec = {'midi': spec}
        if not isinstance(spec, dict) or set(spec) - {'midi', 'range'}:
            raise LaneMapError(f"lanes[{lane!r}]: {{'midi': 'bend' | 'pressure' | 'cc:<n>', 'range': [lo, hi]}}")
        cur = merged.get(lane, {})
        if 'midi' not in spec and 'midi' not in cur:
            raise LaneMapError(f"lanes[{lane!r}] has no default message: say which, e.g. {{'midi': 'cc:20', "
                               f"'range': {spec.get('range', [0, 1])}}}")
        if 'range' not in spec and 'range' not in cur:
            raise LaneMapError(f"lanes[{lane!r}]: give the lane's values at the message's ends, 'range': [lo, hi]")
        merged[lane] = {**cur, **spec}
    out, used = {}, {}
    for lane, spec in merged.items():
        if allowed is not None and lane not in allowed:
            raise LaneMapError(f"lane {lane!r}: live keys play notes rendered ahead, so a lane can only move them "
                               f"as pitch or gain: {list(allowed)}. Lanes that change the sound itself (mute, slide, "
                               f"breath ...) need a new render: queue a clip with expr=... instead")
        kind, num = parse_midi(spec['midi'])
        rg = spec['range']
        if isinstance(rg, (int, float)):
            rg = [-abs(float(rg)), abs(float(rg))]
        try:
            lo, hi = float(rg[0]), float(rg[1])
            assert len(rg) == 2 and lo != hi and math.isfinite(lo) and math.isfinite(hi)
        except (TypeError, ValueError, IndexError, AssertionError):
            raise LaneMapError(f"lanes[{lane!r}] range {rg!r}: [lo, hi], two different numbers in the lane's unit")
        if kind == 'bend' and (abs(lo + hi) > 1e-9 or lo > hi):
            raise LaneMapError(f"lanes[{lane!r}]: pitch bend is centred, so its range is [-r, r] with r > 0 (or the "
                               f"number r); got {rg!r}")
        if kind == 'bend' and abs(hi - lo) / 2 > 127:
            raise LaneMapError(f"lanes[{lane!r}]: a bend range is at most 127 semitones")
        key = midi_name(kind, num)
        if key in used:
            raise LaneMapError(f"lanes {used[key]!r} and {lane!r} both use {key}: one message carries one lane")
        used[key] = lane
        out[lane] = (kind, num, lo, hi)
    return out


def by_message(lmap):
    """{lane: (kind, num, lo, hi)} -> {'bend' | 'pressure' | 'cc:<n>': (lane, kind, lo, hi)}."""
    return {midi_name(k, n): (lane, k, lo, hi) for lane, (k, n, lo, hi) in lmap.items()}


def to_raw(kind, lo, hi, value):
    """A lane value as the message's number (bend 0..16383, centre 8192; the rest 0..127), and whether it was
    clamped to the range."""
    if kind == 'bend':
        v = (float(value) - (lo + hi) / 2) / ((hi - lo) / 2)
        raw = 8192 + round(v * 8191)
        return max(0, min(16383, raw)), not -1 - 1e-9 <= v <= 1 + 1e-9
    v = (float(value) - lo) / (hi - lo)
    return max(0, min(127, round(v * 127))), not -1e-9 <= v <= 1 + 1e-9


def from_raw(kind, lo, hi, raw):
    """The message's number as a lane value (the inverse of to_raw; bend as live_map reads it)."""
    if kind == 'bend':
        v = min(1.0, max(-1.0, (int(raw) - 8192) / 8191.0))
        return (lo + hi) / 2 + v * (hi - lo) / 2
    return lo + (hi - lo) * int(raw) / 127.0


def tol(kind, lo, hi):
    """Half a message step in the lane's unit: what a value can move through MIDI and back."""
    return abs(hi - lo) / (16382 if kind == 'bend' else 127) / 2


def value_at(points, beat):
    """A lane [[beat, value], ...] at a beat: linear between points, held before the first and after the last."""
    if not points:
        return None
    if beat <= points[0][0]:
        return float(points[0][1])
    for (b0, v0), (b1, v1) in zip(points, points[1:]):
        if beat < b1:
            return float(v0) + (float(v1) - float(v0)) * (beat - b0) / (b1 - b0) if b1 > b0 else float(v1)
    return float(points[-1][1])


def lane_events(points, kind, lo, hi, t0, t1, step=STEP):
    """A lane's messages over [t0, t1): [(beat, raw)], the value at t0 first, then a message wherever the value
    changes along a ramp (at most one per `step` beats) and at every point. Returns (events, points clamped)."""
    pts = sorted((float(b), float(v)) for b, v in points)
    times = {t0}
    for (b0, _), (b1, _) in zip(pts, pts[1:]):
        if b1 <= t0 or b0 >= t1:
            continue
        a, z = max(b0, t0), min(b1, t1)
        n = int((z - a) / step)
        times.update(a + i * step for i in range(n + 1))
    times.update(b for b, _ in pts if t0 <= b < t1)
    out, last, clamped = [], None, 0
    for t in sorted(x for x in times if t0 <= x < t1):
        raw, c = to_raw(kind, lo, hi, value_at(pts, t))
        clamped += c
        if raw != last:
            out.append((t, raw))
            last = raw
    return out, clamped


def lane_points(events, kind, lo, hi, step=STEP, tick=1 / 960):
    """Messages [(beat, raw)] (sorted) as a lane's points [[beat, value]]: a value held for longer than a ramp's
    spacing gets a point just before the next message (MIDI holds a value; a lane is linear between points), and
    points on a straight line between their neighbours are dropped."""
    return value_points([(b, from_raw(kind, lo, hi, raw)) for b, raw in events], tol(kind, lo, hi), step, tick)


def value_points(vals, half_step, step=STEP, tick=1 / 960):
    """lane_points on values already in the lane's unit [(beat, value)] (sorted), half_step the tolerance (half a
    message step, tol()): for bends read at a range that changes during the file."""
    if not vals:
        return []
    pts = []
    for b, v in vals:
        if pts and b - pts[-1][0] > step * 1.5 and v != pts[-1][1]:
            pts.append([max(pts[-1][0], b - tick), pts[-1][1]])
        if pts and abs(b - pts[-1][0]) < 1e-12:
            pts[-1][1] = v
        else:
            pts.append([b, v])
    e = half_step * 2.02                 # one message step: a slow ramp read back is a staircase
    keep, a = [pts[0]], 0
    for j in range(2, len(pts)):
        A, C = pts[a], pts[j]
        if any(abs(pts[k][1] - (A[1] + (C[1] - A[1]) * (pts[k][0] - A[0]) / (C[0] - A[0]))) > e
               for k in range(a + 1, j)):
            a = j - 1
            keep.append(pts[a])
    if len(pts) > 1:
        keep.append(pts[-1])
    return [[round(b, 6), round(v, 6)] for b, v in keep]


def rpn(ch, msb, lsb, value, frac=0):
    """An RPN as four controller messages (with the null RPN after, so later data entry changes nothing)."""
    c = 0xB0 | (ch & 0x0F)
    return [bytes([c, 101, msb]), bytes([c, 100, lsb]), bytes([c, 6, int(value) & 0x7F]),
            bytes([c, 38, int(frac) & 0x7F]), bytes([c, 101, 127]), bytes([c, 100, 127])]


def message(kind, num, ch, raw):
    if kind == 'bend':
        return bytes([0xE0 | ch, raw & 0x7F, (raw >> 7) & 0x7F])
    if kind == 'pressure':
        return bytes([0xD0 | ch, raw & 0x7F])
    return bytes([0xB0 | ch, num & 0x7F, raw & 0x7F])


def voice_lanes(inst, root=None):
    """The lanes the instrument's voice takes (its INFO), as a set; empty for a sound with none (a synth, a
    sampler, a code voice that is not a performer)."""
    inst = inst or {}
    t = inst.get('type')
    if t == 'code' and inst.get('voice'):
        from . import voices
        from .instruments import performer_fn
        try:
            mod = voices.load(inst['voice'], root)
        except Exception:
            return set()
        if performer_fn(mod, inst.get('fn')) is None:
            return set()
        return set(((getattr(mod, 'INFO', None) or {}).get('lanes') or {}))
    if t == 'mimic' and inst.get('perform'):
        from . import mimic_perform
        return set(mimic_perform.INFO['lanes'])
    return set()


def is_true(mpe):
    """mpe=True (or 1): MPE, in the zones the controller or the file configures, else the lower zone."""
    return mpe is True or (type(mpe) is int and mpe == 1)


def mpe_zone(mpe):
    """mpe= as the ops take it -> None (not given), {} (off) or {master channel: member count} (channels from 0):
    True or 'lower' the lower zone (master channel 1, members 2 to 16), 'upper' the upper zone (master channel 16,
    members 15 down to 1). A LaneMapError says what it takes."""
    if mpe is None:
        return None
    if mpe is False or (type(mpe) is int and mpe == 0):
        return {}
    if is_true(mpe):
        return {MPE_MASTER: 15}
    s = str(mpe).strip().lower()
    if s in ZONES:
        return {ZONES[s]: 15}
    raise LaneMapError(f"mpe {mpe!r}: True or 'lower' (the lower zone: channel 1 the master, 2-16 a note each), "
                       f"'upper' (the upper zone: channel 16 the master, 15 down to 1 a note each), or False")


def zone_members(master, n):
    """A zone's member channels (from 0), in the order a controller hands them out: the lower zone's from channel 2
    up, the upper zone's from channel 15 down."""
    n = max(0, min(15, int(n)))
    return list(range(1, 1 + n)) if master == MPE_MASTER else list(range(14, 14 - n, -1))


def mcm(zones, master, n):
    """An MPE Configuration Message (RPN 6 on a master channel, n members) applied to zones {master: members}: 0
    members turns that zone off; a zone that now overlaps the other shrinks the other (the two share the 14
    channels between the masters), and one shrunk to nothing is off. A new dict."""
    out = dict(zones or {})
    if master not in (MPE_MASTER, MPE_UPPER):
        return out
    n = max(0, min(15, int(n)))
    out.pop(master, None)
    if n:
        out[master] = n
        other = MPE_UPPER if master == MPE_MASTER else MPE_MASTER
        if other in out and out[other] + n > 14:
            if n >= 14:
                del out[other]
            else:
                out[other] = 14 - n
    return out


def zone_of(zones, ch):
    """The master channel of the zone that holds channel ch (as its master or a member), or None."""
    for master, n in (zones or {}).items():
        if ch == master or ch in zone_members(master, n):
            return master
    return None


def pick_zone(zones, notes):
    """The zone a part is read in: the one whose member channels hold most of its notes ([(beat, pitch, dur, vel,
    ch)]), (master, member channels); None when no note is on a member channel (a part on a zone's master channel, or
    outside every zone, is a plain part)."""
    if not zones:
        return None
    count = {m: sum(1 for n in notes if n[4] in zone_members(m, zones[m])) for m in sorted(zones)}
    best = max(count, key=count.get)
    return (best, zone_members(best, zones[best])) if count[best] else None


def zones_at(history, beat):
    """The zones in force at a beat: every configuration [(beat, master, members)] at or before it, in time order
    (a format 1 file may keep them in several tracks)."""
    z = {}
    for b, master, n in sorted(history, key=lambda h: h[0]):
        if b <= beat + 1e-9:
            z = mcm(z, master, n)
    return z


def _span(chs):
    lo, hi = min(chs) + 1, max(chs) + 1
    return f"{lo}" if lo == hi else f"{lo}-{hi}"


def zone_text(zones):
    """Zones in plain words, channels from 1: 'channel 1 the master, 2-16 a note each' for the usual lower zone,
    'upper zone, channel 16 the master, 1-15 a note each'."""
    parts = []
    for master in sorted(zones or {}):
        mem = zone_members(master, zones[master])
        plain = master == MPE_MASTER and len(zones) == 1 and len(mem) == 15     # the usual lower zone
        parts.append(('' if plain else 'lower zone, ' if master == MPE_MASTER else 'upper zone, ')
                     + f"channel {master + 1} the master, {_span(mem)} a note each")
    return '; '.join(parts)


def mpe_merge(notes, ctl, what, master=MPE_MASTER, with_ch=False, rest=None, members=None):
    """MPE: each note's own channel stream of one message ('bend', 'pressure', 'cc:<n>') joined into one lane by
    last-note priority. notes: [(beat, pitch, dur, vel, ch)]; ctl: [(beat, ch, what, raw)]. At any time the lane
    belongs to the newest-started note still sounding; when that note ends it goes back to the newest older note
    still held, and while nothing sounds the last owner keeps it. In each owner's stretch the lane takes that
    note's channel's value. master: the zone's master channel (0 lower, 15 upper). rest: a value each owner's
    stretch starts at unless that note's channel sent one since the note began (poly pressure: a key that sends
    none has none, and a key struck again starts from nothing); None keeps the channel's last value. members: the
    zone's member channels, when only those count (notes and messages on other channels are another part's);
    None counts every channel but the master. Returns (events [(beat, raw)], or [(beat, raw, channel)] with
    with_ch, notes overlapped by a newer one, master-channel messages left out)."""
    by_ch = {}
    skipped = 0
    inz = (lambda c: c != master) if members is None else (lambda c: c in members)
    for b, ch, w, raw in ctl:
        if w != what:
            continue
        if ch == master:
            skipped += 1
            continue
        if inz(ch):
            by_ch.setdefault(ch, []).append((b, raw))
    ns = sorted(n for n in notes if inz(n[4]))
    overlapped = sum(1 for i, n in enumerate(ns) if any(m[0] < n[0] + n[2] - 1e-9 for m in ns[i + 1:]))
    cuts = sorted({n[0] for n in ns} | {n[0] + n[2] for n in ns})
    stretches, owner = [], None                        # [(from, to, channel, the owner note's start)]
    for a, z in zip(cuts, cuts[1:] + [math.inf]):
        sounding = [n for n in ns if n[0] <= a + 1e-9 and a < n[0] + n[2] - 1e-9]
        if sounding:
            owner = max(sounding, key=lambda n: n[0])
        if owner is None:
            continue
        if stretches and stretches[-1][2] == owner[4] and (rest is None or stretches[-1][3] == owner[0]):
            stretches[-1] = (stretches[-1][0], z, owner[4], stretches[-1][3])
        else:
            stretches.append((a, z, owner[4], owner[0]))
    out = []
    for a, z, ch, n0 in stretches:
        evs = by_ch.get(ch, [])
        before = [r for b, r in evs if b <= a + 1e-9 and (rest is None or b >= n0 - 1e-9)]
        if before:
            out.append((a, before[-1], ch))
        elif rest is not None:
            out.append((a, rest, ch))
        out += [(b, r, ch) for b, r in evs if a + 1e-9 < b < z - 1e-9]
    out.sort(key=lambda e: e[0])
    dedup = []
    for b, r, ch in out:
        if dedup and dedup[-1][1:] == (r, ch):
            continue
        dedup.append((b, r, ch))
    return (dedup if with_ch else [(b, r) for b, r, _ in dedup]), overlapped, skipped


def poly_to_channel(notes, ctl):
    """Polyphonic aftertouch (one key's own pressure) onto its channel's pressure, since ismail's lanes are per track,
    not per note: on each channel that sends poly pressure and no channel pressure, the channel's pressure follows
    the newest key still sounding (last-note priority, as an MPE part is read; when it lets go, the newest older key
    held). A key that has sent no pressure since its own note-on is at 0 while it holds the lane. notes: [(beat, pitch, dur, vel, ch)]; ctl: [(beat, ch, what, raw)] with 'poly:<pitch>' messages. Returns
    (ctl with those channels' poly pressure as 'pressure', poly messages read that way, poly messages left as they
    were because their channel sends channel pressure too)."""
    poly = {}
    for b, ch, w, raw in ctl:
        if w.startswith('poly:'):
            poly.setdefault(ch, []).append((b, int(w[5:]), 'pressure', raw))
    chans = {ch for _, ch, w, _ in ctl if w == 'pressure'}
    out = [c for c in ctl if not (c[2].startswith('poly:') and c[1] in poly and c[1] not in chans)]
    read = kept = 0
    for ch, msgs in poly.items():
        if ch in chans:
            kept += len(msgs)
            continue
        keys = [(n[0], n[1], n[2], n[3], n[1]) for n in notes if n[4] == ch]    # each key as its own "channel"
        evs, _, _ = mpe_merge(keys, msgs, 'pressure', master=-1, rest=0)     # a key with no pressure has none
        out += [(b, ch, 'pressure', raw) for b, raw in evs]
        read += len(msgs)
    out.sort(key=lambda c: c[0])
    return out, read, kept


def splice(old, new, a, z):
    """A lane's points [[beat, value]] with `new` written over the beats [a, z): the old points there go, an old
    point on z itself stays (the song's own value from there on) and a new point on that beat gives way to it.
    When the song has a point from z on, the new last value is held to z rather than ramped toward it."""
    keep = [q for q in old if not a - 1e-9 <= q[0] < z - 1e-9]
    at = {round(q[0], 6) for q in keep}
    new = [q for q in new if round(q[0], 6) not in at]
    if new and any(q[0] >= z - 1e-9 for q in keep) and new[-1][0] < z - 2 / 960:
        new = new + [[round(z - 1 / 960, 6), new[-1][1]]]
    return sorted(keep + new)
