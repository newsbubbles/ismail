"""MIDI files and ports (Nate 10-23, "full midi like ableton"; ledger:M113; plan in
D:/ismail-hq/proposals/midi-parity.md, steps 1 to 3): midi_write puts a song or some of its tracks into a type 1
.mid any DAW opens, midi_read lists a .mid and writes its parts into tracks, midi_ports lists the MIDI inputs and
outputs, midi_play plays a song's notes to an output in real time. Registered into api.OPS on import; the file
format is in midifile.py, the ports in midi.py, the timed sender in midisend.py. A live set's MIDI out and clock:
live_midi_out, live_midi_clock (live/ops.py)."""
import os
import time

from . import midi as M
from . import midifile as MF
from . import midilanes as ML
from . import midisend as MS
from .api import op, OpError, _load, _clear
from .notation import midi_to_name, pitch_to_midi, placed, fmt_num
from .arp import expand_project

DRUM_CH = 9                    # channel 10, where every DAW and GM synth looks for drums


def _is_kit(tr):
    return ((tr.get('instrument') or {}).get('type')) == 'kit'


@op()
def midi_write(project: str, out: str = None, tracks: list = None, bars: list = None, markers: dict = None,
               lanes: dict = None, mpe=None) -> str:
    """Write the song's notes to a Standard MIDI File (type 1) that Ableton, FL Studio, Logic, Reaper, MuseScore
    and any hardware sequencer open: one MIDI track per ismail track that has notes (named after it), the tempo,
    the meter, and markers. tracks: names to include (default every track with notes, muted ones too). bars:
    [a, b] writes only notes that start in those bars, moved so bar a is the file's bar 1 (default the whole song,
    bar 1 at the start). markers: {name: bar}, e.g. {'drop': 17}, shown as locators in the DAW. Nudged notes
    (@offset, a track's offset_ms) land where they sound, to the nearest tick (about 0.5 ms at 120 BPM). Kits go
    on channel 10 with ismail's own pad pitches (the reply lists which pitch is which pad); other tracks get
    channels 1 to 16 in order. The file carries notes, not sounds: in the DAW, put an instrument on each track.
    out: the file (default <project>/exports/<project name>.mid).
    A track's expression lanes (automation inst.lane.<name>) go out as MIDI on its channel: bend as pitch bend
    (+-2 semitones, said in the file with RPN 0), vib as cc:1 (mod wheel, 0..50 cents), level as cc:11 (expression,
    -40..0 dB, 127 the fader), breath as cc:2 (0..1). lanes: overrides as data, {lane: {'midi': 'bend' | 'pressure'
    | 'cc:<n>', 'range': [lo, hi]}} (the lane's values at the message's ends; a bend range is [-r, r] or r), or
    {lane: None} to leave one out; e.g. {'bend': {'range': 12}, 'mute': {'midi': 'cc:20', 'range': [0, 1]}}. The
    reply names lanes with no message and values clamped to a range. mpe: True (every melodic track) or a list of
    track names written as MPE: channel 1 the master, each note on its own member channel 2 to 16 with the track's
    lanes over its own span (bend +-48, level as per-note channel pressure), for an MPE synth. Reach for it when
    the person wants to export or write the song or a part as MIDI for their DAW, a hardware sequencer or an MPE
    synth, its lanes as pitch bend and controllers."""
    P = _load(project)
    P.d = expand_project(P.d)                    # a track's arp goes out as its steps
    names = tracks or [n for n, t in P.d['tracks'].items() if t.get('notes')]
    if not names:
        raise OpError("no track has notes; write some (notes_write) or name tracks=[...]")
    for n in names:
        P.track(n)
    try:
        lmaps = {False: ML.resolve(lanes, ML.FILE)}
        if mpe:                                  # a map that only clashes with the MPE defaults is fine without
            lmaps[True] = ML.resolve(lanes, ML.FILE_MPE)
    except ML.LaneMapError as e:
        raise OpError(str(e))
    if mpe not in (None, False, True) and not (isinstance(mpe, (list, tuple)) and all(m in names for m in mpe)):
        raise OpError(f"mpe: True (every melodic track) or a list of the tracks written: {names}")
    bpb, bpm = P.bpb, P.d['bpm']
    a, b = bars or [1, P.d['length_bars']]
    b0, b1 = P.bar_to_beat(a), P.bar_to_beat(b + 1)
    shift = b0 if bars else 0.0
    parts, chans, lines, free = [], {}, [], [c for c in range(16) if c != DRUM_CH]
    for n in names:
        tr = P.track(n)
        sel = [x for x in tr['notes'] if b0 <= x[0] < b1]
        if not sel:
            lines.append(f"  {n}: no notes in bars {a}-{b}, left out")
            continue
        notes = [(max(0.0, s - shift), p, d, v) for s, p, d, v in placed(sel, bpm, tr.get('offset_ms') or 0.0)]
        as_mpe = not _is_kit(tr) and (mpe is True or (isinstance(mpe, (list, tuple)) and n in mpe))
        if _is_kit(tr):
            ch = DRUM_CH
        elif as_mpe:
            ch = ML.MPE_MASTER
        else:
            ch = free[len(chans) % len(free)]
            chans[n] = ch
        part = {'name': n, 'channel': ch, 'notes': notes}
        lane_txt = _write_lanes(part, tr, lmaps[as_mpe], as_mpe, b0, b1, shift)
        parts.append(part)
        what = ''
        if _is_kit(tr):
            pads = {pitch_to_midi(k): v for k, v in (tr['instrument'].get('map') or {}).items()}
            used = sorted({x[1] for x in sel})
            what = '; pads ' + ', '.join(f"{p} {midi_to_name(p)}={_pad_name(pads.get(p))}" for p in used[:16]) + \
                   (f" (+{len(used) - 16} more)" if len(used) > 16 else '')
        where = "MPE (channel 1 the master, a channel of 2-16 per note)" if as_mpe else f"channel {ch + 1}"
        lines.append(f"  {n}: {len(notes)} notes, {where}" + (' (muted in the mix)' if tr.get('mute') else '')
                     + what)
        lines += lane_txt
    if not parts:
        raise OpError(f"no notes in bars {a}-{b} on {names}; widen bars")
    wrap = len(chans) > len(free)
    mk = []
    for name, bar in (markers or {}).items():
        beat = P.bar_to_beat(int(bar)) - shift
        if beat < 0 or beat >= b1 - shift:
            raise OpError(f"marker {name!r} at bar {bar} is outside the bars written ({a}-{b})")
        mk.append((beat, name))
    path = os.path.abspath(out or os.path.join(P.root, 'exports', f"{P.d.get('name') or 'song'}.mid"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    count = MF.write(path, parts, bpm, bpb, mk, name=P.d.get('name'))
    head = (f"wrote {path}: {count} notes on {len(parts)} tracks, {fmt_num(bpm)} BPM, {bpb}/4, bars {a}-{b}"
            + (f" (bar {a} is the file's bar 1)" if bars and a != 1 else '') + (f", {len(mk)} markers" if mk else ''))
    tail = ["notes only: put an instrument on each track in the DAW (stems carry ismail's sound: render(stems=True))"]
    if wrap:
        tail.append(f"WARNING {len(chans)} melodic tracks share 15 channels: some share a channel, which is fine "
                    f"in a DAW (each MIDI track keeps its own notes) but not on one hardware synth")
    return '\n'.join([head] + lines + tail)


def _lane_desc(lane, kind, num, lo, hi):
    if kind == 'bend':
        return f"{lane} -> pitch bend +-{fmt_num(hi)}"
    return f"{lane} -> {ML.midi_name(kind, num)} {fmt_num(lo)}..{fmt_num(hi)}"


def _bend_rpn(ch, lmap, lanes):
    """RPN 0 (the bend range) on channel ch when one of the lanes written goes out as pitch bend."""
    r = next((lmap[x][3] for x in lanes if lmap[x][0] == 'bend'), None)
    return [] if r is None else ML.rpn(ch, 0, 0, int(r), round((r % 1) * 100))


def _write_lanes(part, tr, lmap, as_mpe, b0, b1, shift):
    """A track's lanes (automation inst.lane.<name>, in beats) over the beats b0..b1 into part['events'] as MIDI,
    moved `shift` beats earlier as the notes are. as_mpe: the notes onto member channels 2-16 (a free one, else the
    one whose note ends soonest), each note's lanes over its own span on its own channel, after the MPE
    configuration (RPN 6 on channel 1). The reply's lines."""
    pts = {k[10:]: v for k, v in (tr.get('automation') or {}).items() if k.startswith('inst.lane.') and v}
    done = [x for x in pts if x in lmap]
    ev, L, clamped = [], [], {}
    if as_mpe:
        last, out = {c: -1.0 for c in range(1, 16)}, []
        for s, p, d, v in sorted(part['notes']):
            c = min(last, key=lambda c: (last[c] > s + 1e-9, last[c]))
            last[c] = s + d
            out.append((s, p, d, v, c))
            for lane in done:
                kind, num, lo, hi = lmap[lane]
                es, cl = ML.lane_events(pts[lane], kind, lo, hi, s + shift, s + shift + d)
                clamped[lane] = clamped.get(lane, 0) + cl
                ev += [(max(0.0, t - shift), ML.message(kind, num, c, raw)) for t, raw in es]
        part['notes'] = out
        head = ML.rpn(ML.MPE_MASTER, 0, 6, 15) + [m for c in range(1, 16) for m in _bend_rpn(c, lmap, done)]
    else:
        ch = part['channel']
        for lane in done:
            kind, num, lo, hi = lmap[lane]
            es, cl = ML.lane_events(pts[lane], kind, lo, hi, b0, b1)
            clamped[lane] = cl
            ev += [(t - shift, ML.message(kind, num, ch, raw)) for t, raw in es]
        head = _bend_rpn(ch, lmap, done)
    part['events'] = [(0.0, m) for m in head] + sorted(ev, key=lambda e: e[0])
    if done:
        L.append("    lanes: " + ', '.join(_lane_desc(x, *lmap[x]) for x in done) + f" ({len(ev)} messages)")
    for x, c in clamped.items():
        if c:
            lo, hi = lmap[x][2:]
            L.append(f"    WARNING {x}: {c} values outside {fmt_num(lo)}..{fmt_num(hi)} were clamped; lanes={{{x!r}: "
                     f"{{'range': [lo, hi]}}}} widens it")
    left = sorted(x for x in pts if x not in lmap)
    if left:
        L.append(f"    lanes not written (no MIDI message for them): {', '.join(left)}; lanes={{{left[0]!r}: "
                 f"{{'midi': 'cc:20', 'range': [lo, hi]}}}} writes one")
    return L


def _pad_name(v):
    if isinstance(v, dict):
        v = v.get('name') or v.get('sample') or v.get('file') or v.get('sound') or v.get('type')
    return os.path.splitext(os.path.basename(str(v)))[0] if v else '?'


def _file_zone(f):
    """The MPE zone the file configures (RPN 6 with members, in any track: a conductor track may hold it): its master
    channel (0 lower, 15 upper), or None."""
    return next((t['mpe_master'] for t in f['tracks'] if t['mpe']), None)


def _key(spec, f):
    """What a key in `into` names: a track's name, '#<index>', or 'ch<1-16>' (that channel in every track), as
    (notes, controller messages in file order, bend ranges {channel: [(beat, semitones)]} from RPN 0 in any track,
    the MPE zone's master channel or None)."""
    s = str(spec).strip()
    ranges = {}
    for t in f['tracks']:                        # an RPN may sit in another track than the notes (a conductor)
        for c, hist in t['bend_ranges'].items():
            ranges.setdefault(c, []).extend(hist)
    ranges = {c: sorted(h, key=lambda x: x[0]) for c, h in ranges.items()}
    if s.startswith('#') and s[1:].isdigit():
        k = int(s[1:])
        if k >= len(f['tracks']):
            raise OpError(f"{s}: the file has tracks #0-#{len(f['tracks']) - 1}")
        t = f['tracks'][k]
        return t['notes'], t['ctl'], ranges, t['mpe_master'] if t['mpe'] else _file_zone(f)
    if s.lower().startswith('ch') and s[2:].isdigit():
        ch = int(s[2:]) - 1
        if not 0 <= ch <= 15:
            raise OpError(f"{s}: channels are ch1 to ch16")
        return ([n for t in f['tracks'] for n in t['notes'] if n[4] == ch],
                sorted((c for t in f['tracks'] for c in t['ctl'] if c[1] == ch), key=lambda c: c[0]), ranges, None)
    hits = [t for t in f['tracks'] if t['name'] == s]
    if len(hits) != 1:
        raise OpError(f"{s!r} is not one track name in the file; call midi_read without into to list them, then use "
                      f"the name, '#<index>' or 'ch<n>'")
    t = hits[0]
    return t['notes'], t['ctl'], ranges, t['mpe_master'] if t['mpe'] else _file_zone(f)


def _range_at(hist, beat, default):
    """The bend range in force at a beat on one channel: the last RPN 0 at or before it, else default."""
    r = default
    for b, v in hist or ():
        if b <= beat + 1e-9:
            r = v
        else:
            break
    return r


def _is_mpe(zone, asked=None):
    """An MPE part: the file configures MPE (RPN 6 with members, zone its master channel), or the caller says so
    (mpe=True; mpe=False overrides the file). A multi-channel part with bend on several channels is not enough: a
    type 0 GM file looks the same."""
    return bool(asked) if asked is not None else zone is not None


def _ctl_counts(ctl):
    out = {}
    for _, _, w, _ in ctl:
        w = 'poly pressure' if w.startswith('poly:') else w
        out[w] = out.get(w, 0) + 1
    return out


def _read_lanes(P, tr, target, notes, ctl, ranges, zone, lanes, base, asked=None):
    """The lanes a part's controller messages carry, written into tr's automation (inst.lane.<name>, beats from
    base): (lines for the reply, warnings). A bend is read at the range in force on its channel at its time."""
    mpe = _is_mpe(zone, asked)
    master = zone if zone is not None else ML.MPE_MASTER
    try:
        lmap = ML.resolve(lanes, ML.FILE_MPE if mpe else ML.FILE)
    except ML.LaneMapError as e:
        raise OpError(str(e))
    chs = {n[4] for n in notes}
    if not mpe and chs:
        ctl = [c for c in ctl if c[1] in chs]
    given = {str(k)[10:] if str(k).startswith('inst.lane.') else str(k): v for k, v in (lanes or {}).items()}
    L, W, wrote = [], [], []
    for lane, (kind, num, lo, hi) in lmap.items():
        what = ML.midi_name(kind, num)
        fixed = kind != 'bend' or (isinstance(given.get(lane), dict) and 'range' in given[lane])
        if mpe:
            evs, over, skipped = ML.mpe_merge(notes, ctl, what, master=master, with_ch=True)
            if over and evs:
                W.append(f"WARNING {target}.{lane}: MPE gives each note its own {what}, ismail's lanes are per track: "
                         f"{over} notes overlap a newer one, and the lane follows the newest (last-note priority)")
            if skipped and evs:
                W.append(f"WARNING {target}.{lane}: {skipped} {what} messages on the MPE master channel "
                         f"({master + 1}) were left out; the lane holds the member channels' per-note values")
        else:
            evs = [(b, raw, c) for b, c, w, raw in ctl if w == what]
        if not evs:
            continue
        used_r = set()

        def val(b, raw, c):
            if fixed:
                return ML.from_raw(kind, lo, hi, raw)
            r = _range_at(ranges.get(c), b, hi)
            used_r.add(r)
            return ML.from_raw(kind, -r, r, raw)
        vals = [(b, val(b, raw, c)) for b, raw, c in evs]
        if not fixed:
            hi = max(used_r)
            lo = -hi
        pts = [[round(base + b, 6), v] for b, v in ML.value_points(vals, ML.tol(kind, lo, hi))]
        key = 'inst.lane.' + lane
        auto = tr.setdefault('automation', {})
        a, z = pts[0][0], pts[-1][0]
        auto[key] = sorted([p for p in auto.get(key, []) if not a <= p[0] <= z] + pts)
        wrote.append(lane)
        L.append(f"    lane {lane}: {len(pts)} points from {len(evs)} {what} messages"
                 + ((f" (bend range +-{'/'.join(fmt_num(r) for r in sorted(used_r))})" if not fixed else
                     f" (bend range +-{fmt_num(hi)})") if kind == 'bend' else f" ({fmt_num(lo)}..{fmt_num(hi)})")
                 + (", per note (MPE)" if mpe else ''))
    used = {ML.midi_name(k, n) for k, n, _, _ in lmap.values()}
    left = {w: c for w, c in _ctl_counts(ctl).items() if w not in used}
    if left:
        other = sorted(w for w in left if w != 'poly pressure')
        L.append("    not read: "
                 + ', '.join(f"{w} ({c} message{'s' if c != 1 else ''})" for w, c in sorted(left.items()))
                 + (f"; lanes={{'<lane>': {{'midi': '{other[0]}', 'range': [lo, hi]}}}} reads one" if other else '')
                 + ("; poly pressure (per key) is not read yet" if 'poly pressure' in left else ''))
    have = ML.voice_lanes(tr.get('instrument'), P.root)
    miss = [x for x in wrote if x not in have]
    if miss:
        W.append(f"WARNING {target} plays a sound with no {'/'.join(miss)} lane: the points are kept as automation "
                 f"(automation_read shows them) and sound once the track plays a performer voice that has "
                 f"{'it' if len(miss) == 1 else 'them'} (voice_help lists a voice's lanes)")
    return L, W


@op(mutates=True)
def midi_read(project: str, file: str, into: dict = None, bar: int = 1, mode: str = 'replace',
              quantize: float = None, lanes: dict = None, mpe: bool = None) -> str:
    """Read a Standard MIDI File (.mid, from a DAW, a score program or the web). Without `into`, it lists what the
    file holds: tempo and meter (and any changes), markers, and every track and channel with its note count, range
    and bars. With into={<file track>: <ismail track>} it writes those notes into existing tracks (track_add first),
    where <file track> is the track's name, '#<index>', or 'ch<1-16>' (that channel across the file, e.g. 'ch10' for
    drums). bar: where the file's bar 1 lands. mode='replace' clears the span the notes cover on each target first,
    'add' merges. quantize: a grid in beats (0.25 = 16ths) the starts snap to; default keeps them as played.
    Positions are read in beats, so a file at another tempo keeps its bars at this project's tempo (the reply says
    when the tempos differ).
    Pitch bend and controllers in the part become the target's expression lanes (automation inst.lane.<name>,
    which a performer voice plays): pitch bend -> bend (semitones, at the file's RPN 0 bend range, else +-2), cc:1
    -> vib (0..50 cents), cc:11 -> level (-40..0 dB), cc:2 -> breath (0..1); the reply names the messages no lane
    took. lanes: the map as data, as midi_write takes it ({lane: {'midi': 'bend' | 'pressure' | 'cc:<n>', 'range':
    [lo, hi]}}, {lane: None} skips one), e.g. {'mute': {'midi': 'cc:20', 'range': [0, 1]}}. An MPE part (the file
    configures MPE with RPN 6 in any track, lower or upper zone, or mpe=True; mpe=False reads it as one plain part,
    every channel's messages into one lane) is read per note: bend at +-48
    unless the file says, and per-note pressure -> level; a lane follows the newest note still sounding, since
    ismail's lanes are per track. Lanes replace the target's points over the span they cover; quantize moves notes
    only. Reach for it when the person brings a .mid (from their DAW, a score program or the web) to open, read or
    import into a song, its pitch bend, mod wheel, other controllers and MPE included."""
    path = os.path.abspath(file)
    if not os.path.exists(path):
        raise OpError(f"no file {path}")
    try:
        f = MF.read(path)
    except (MF.MidiFileError, IndexError) as e:
        raise OpError(f"could not read {path}: {e}")
    P = _load(project)
    bpb, bpm = P.bpb, P.d['bpm']
    tempos = f['tempos'] or [(0.0, 120.0)]
    fbpb = (f['meters'][0][1] * 4 / f['meters'][0][2]) if f['meters'] else 4
    fbar = lambda beat: int(beat // fbpb) + 1
    if not into:
        L = [f"{os.path.basename(path)}: type {f['format']}, {len(f['tracks'])} tracks, {f['ppq']} ticks per beat",
             "tempo: " + ', '.join(f"{fmt_num(round(t, 2))} BPM at bar {fbar(b)}" for b, t in tempos[:6])
             + (f" (+{len(tempos) - 6} changes)" if len(tempos) > 6 else '')]
        if f['meters']:
            L.append("meter: " + ', '.join(f"{n}/{d} at bar {fbar(b)}" for b, n, d in f['meters'][:6]))
        if f['markers']:
            L.append("markers: " + ', '.join(f"{t!r} bar {fbar(b)}" for b, t in f['markers'][:20]))
        for t in f['tracks']:
            ns = t['notes']
            cc = _ctl_counts(t['ctl'])
            mz = t['mpe_master'] if t['mpe'] else (_file_zone(f) if len({n[4] for n in ns}) >= 2 else None)
            rngs = sorted({r for h in t['bend_ranges'].values() for _, r in h})
            ctl_txt = ((f"; MPE{' (upper zone, master channel 16)' if mz == 15 else ''}, " if mz is not None
                        else "; ")
                       + "expression: " + ', '.join(f"{w} {c}" for w, c in sorted(cc.items()))
                       + (" (bend range +-" + '/'.join(fmt_num(r) for r in rngs) + ")" if rngs else '')) if cc else ''
            if not ns:
                L.append(f"  #{t['index']} {t['name'] or '(no name)'}: no notes" + ctl_txt)
                continue
            chs = sorted({n[4] + 1 for n in ns})
            lo, hi = min(n[1] for n in ns), max(n[1] for n in ns)
            L.append(f"  #{t['index']} {t['name'] or '(no name)'}: {len(ns)} notes, channel {'/'.join(map(str, chs))}"
                     f"{' (drums)' if chs == [10] else ''}, {midi_to_name(lo)}-{midi_to_name(hi)}, bars "
                     f"{fbar(ns[0][0])}-{fbar(max(n[0] for n in ns))}" + ctl_txt)
        L.append("write parts with into={'<name>' or '#<index>' or 'ch<n>': '<ismail track>'}, bar=..., quantize=...; "
                 "their bend and controllers become lanes (lanes=... maps them)")
        return '\n'.join(L)
    base = P.bar_to_beat(bar)
    out, warn = [], []
    for spec, target in into.items():
        tr = P.track(target)
        notes, ctl, ranges, zone = _key(spec, f)
        if not notes:
            out.append(f"  {spec} -> {target}: no notes")
            continue
        rows = []
        for s, p, d, v, _ in notes:
            if quantize:
                s = round(s / quantize) * quantize
                d = max(quantize, round(d / quantize) * quantize)
            rows.append([round(base + s, 6), p, round(d, 6), int(v)])
        if mode == 'replace':
            _clear(tr, min(r[0] for r in rows), max(r[0] + r[2] for r in rows))
        tr['notes'] = sorted(tr['notes'] + rows)
        out.append(f"  {spec} -> {target}: {len(rows)} notes, bars {bar + int(min(r[0] for r in rows) - base) // bpb}"
                   f"-{bar + int(max(r[0] for r in rows) - base) // bpb}")
        lt, lw = _read_lanes(P, tr, target, notes, ctl, ranges, zone, lanes, base, mpe)
        out += lt
        warn += lw
        if _is_kit(tr):
            mapped = {pitch_to_midi(k) for k in tr['instrument']['map']}
            miss = sorted({r[1] for r in rows} - mapped)
            if miss:
                warn.append(f"WARNING {target} is a kit and has no pads on {[midi_to_name(m) for m in miss]}: those "
                            f"hits are silent. A GM drum file puts the kick on C2 (36) and the snare on D2 (38); "
                            f"notes_transform or the kit's map can move them")
    P.save()
    if len(tempos) > 1:
        warn.append(f"the file changes tempo {len(tempos) - 1} times; one tempo per project, so the notes keep their "
                    f"beats at {fmt_num(bpm)} BPM")
    elif abs(tempos[0][1] - bpm) > 0.01:
        warn.append(f"the file is at {fmt_num(round(tempos[0][1], 2))} BPM, the project at {fmt_num(bpm)}: bars line up, "
                    f"the speed is the project's (project_set bpm=... to match the file)")
    if abs(fbpb - bpb) > 1e-9:
        warn.append(f"the file's meter is {fmt_num(fbpb)} beats a bar, the project's {bpb}: notes keep their beats, so "
                    f"bar numbers differ")
    return '\n'.join([f"read {os.path.basename(path)} into bar {bar} ({mode})"] + out + warn)


@op()
def midi_ports() -> str:
    """The MIDI ports this computer has now, by name: inputs (keyboards, pads, virtual ports that send to ismail)
    and outputs (synths, sound modules, loopMIDI or IAC ports into a DAW, that ismail sends to). Inputs play through
    a live set with live_midi, or lead its tempo with their clock (live_midi_clock_in); outputs take a live track's notes (live_midi_out), the set's clock (live_midi_clock),
    or a song's notes from here (midi_play). On Windows, loopMIDI makes a virtual port that is both: ismail sends to
    it, and the DAW takes it as an input."""
    ins, outs = M.sources(), M.outputs()
    miss = M.backend_missing()
    none = f"  none{' (' + miss + ')' if miss else ''}"
    L = ["MIDI outputs (ismail sends to these):"] + ([f"  {n}" for n in outs] or [none])
    L += ["MIDI inputs (these send to ismail):"] + ([f"  {n}" for n in ins] or [none])
    if any('wavetable' in n.lower() for n in outs):
        L.append("'Microsoft GS Wavetable Synth' is the General MIDI synth built into Windows: it sounds from the "
                 "speakers at once")
    if not miss and not [n for n in outs if 'wavetable' not in n.lower()]:
        L.append("no port into another app yet: on Windows, loopMIDI (tobias-erichsen.de) makes one; on a Mac, the "
                 "IAC Driver in Audio MIDI Setup")
    return '\n'.join(L)


@op()
def midi_play(project: str, port: str, tracks: list = None, bars: list = None, channel: int = None,
              clock: bool = False, max_s: float = 600) -> str:
    """Play the song's notes to a MIDI output in real time, from here, with no live set: to hear a part on a
    hardware synth, or through a loopMIDI / IAC port on an instrument in Ableton or FL Studio, or to check a port
    and a channel before a set. Blocks until the last note ends (Ctrl+C stops it; every note gets its note-off).
    port: an output's name or a unique part of it (midi_ports). tracks: names (default every unmuted track with
    notes); bars: [a, b] plays those bars, bar a first (default the whole song). Channels as midi_write: kits on 10,
    other tracks 1 to 16 in order; channel=<1-16> puts every track on that one (one synth). Notes land where they
    sound, nudges included, at the project's tempo. clock=True also sends MIDI clock (24 per beat, one beat of
    pulses before Start, Stop at the end) so a drum machine or a DAW on external sync plays along. max_s: refuses a
    longer span (choose bars). The reply says how late the messages went out."""
    P = _load(project)
    P.d = expand_project(P.d)                    # a track's arp goes out as its steps
    names = tracks or [n for n, t in P.d['tracks'].items() if t.get('notes') and not t.get('mute')]
    if not names:
        raise OpError("no unmuted track has notes; write some (notes_write) or name tracks=[...]")
    for n in names:
        P.track(n)
    if channel is not None and not 1 <= int(channel) <= 16:
        raise OpError("channel: 1 to 16 (10 is drums), or leave it out for one channel per track")
    bpm = P.d['bpm']
    spb = 60.0 / bpm
    a, b = bars or [1, P.d['length_bars']]
    b0, b1 = P.bar_to_beat(a), P.bar_to_beat(b + 1)
    parts, free, lines, melodic = [], [c for c in range(16) if c != DRUM_CH], [], 0
    for n in names:
        tr = P.track(n)
        sel = [x for x in tr['notes'] if b0 <= x[0] < b1]
        if not sel:
            continue
        if channel is not None:
            ch = int(channel) - 1
        elif _is_kit(tr):
            ch = DRUM_CH
        else:
            ch = free[melodic % len(free)]
            melodic += 1
        notes = [(max(0.0, s - b0), p, d, v) for s, p, d, v in placed(sel, bpm, tr.get('offset_ms') or 0.0)]
        parts.append((n, ch, notes))
        lines.append(f"  {n}: {len(notes)} notes, channel {ch + 1}")
    if not parts:
        raise OpError(f"no notes in bars {a}-{b} on {names}; widen bars")
    end_beat = max(s + d for _, _, ns in parts for s, _, d, _ in ns)
    if end_beat * spb > max_s:
        raise OpError(f"bars {a}-{b} play {end_beat * spb:.0f} s, over max_s={max_s:g}; choose bars=[a, b] or raise "
                      f"max_s")
    try:
        name = M.find_output(port)
        out = M.Output(name)
    except M.MidiError as e:
        raise OpError(str(e))
    sched = MS.Scheduler()
    held = MS.Notes(out)
    t0 = sched.now() + 0.3 + (spb if clock else 0.0)
    tok = 0
    for _, ch, ns in parts:
        for s, p, d, v in ns:
            tok += 1
            sched.at(t0 + s * spb, lambda c=ch, p=p, v=v, k=tok: held.on(c, p, v, k))
            sched.at(t0 + (s + max(d, 1 / 64)) * spb, lambda c=ch, p=p, k=tok: held.off(c, p, k))
    if clock:
        sched.at(t0, lambda: out.send(bytes([MS.START])))
        for k in MS.pulses(-1.0, end_beat):
            sched.at(t0 + k / MS.PPQN * spb, lambda: out.send(bytes([MS.PULSE])))
    stopped = False
    sched.start()
    try:
        while sched.pending():
            time.sleep(0.02)
    except KeyboardInterrupt:
        stopped = True
    finally:
        sched.stop()
        sched.drop()
        try:
            held.release(channels={ch for _, ch, _ in parts})
            if clock:
                out.send(bytes([MS.STOP]))
        finally:
            out.close()
    late = sched.lateness()
    head = (f"{'stopped' if stopped else 'played'} {held.ons} notes to {name!r}, bars {a}-{b} at {fmt_num(bpm)} BPM"
            f" ({end_beat * spb:.1f} s)" + (", with clock (Start, 24 per beat, Stop)" if clock else ''))
    return '\n'.join([head] + lines + [f"timing: messages went out {late['median_ms']:.2f} ms late at the median, "
                                       f"{late['p99_ms']:.2f} ms at the 99th percentile, {late['max_ms']:.2f} ms at "
                                       f"most ({late['n']} messages)"]
                     + ([f"port errors: {'; '.join(sched.errors)}"] if sched.errors else []))
