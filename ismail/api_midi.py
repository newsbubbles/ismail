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
from . import midisend as MS
from .api import op, OpError, _load, _clear
from .notation import midi_to_name, pitch_to_midi, placed, fmt_num

DRUM_CH = 9                    # channel 10, where every DAW and GM synth looks for drums


def _is_kit(tr):
    return ((tr.get('instrument') or {}).get('type')) == 'kit'


@op()
def midi_write(project: str, out: str = None, tracks: list = None, bars: list = None, markers: dict = None) -> str:
    """Write the song's notes to a Standard MIDI File (type 1) that Ableton, FL Studio, Logic, Reaper, MuseScore
    and any hardware sequencer open: one MIDI track per ismail track that has notes (named after it), the tempo,
    the meter, and markers. tracks: names to include (default every track with notes, muted ones too). bars:
    [a, b] writes only notes that start in those bars, moved so bar a is the file's bar 1 (default the whole song,
    bar 1 at the start). markers: {name: bar}, e.g. {'drop': 17}, shown as locators in the DAW. Nudged notes
    (@offset, a track's offset_ms) land where they sound, to the nearest tick (about 0.5 ms at 120 BPM). Kits go
    on channel 10 with ismail's own pad pitches (the reply lists which pitch is which pad); other tracks get
    channels 1 to 16 in order. The file carries notes, not sounds: in the DAW, put an instrument on each track.
    out: the file (default <project>/exports/<project name>.mid)."""
    P = _load(project)
    names = tracks or [n for n, t in P.d['tracks'].items() if t.get('notes')]
    if not names:
        raise OpError("no track has notes; write some (notes_write) or name tracks=[...]")
    for n in names:
        P.track(n)
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
        if _is_kit(tr):
            ch = DRUM_CH
        else:
            ch = free[len(chans) % len(free)]
            chans[n] = ch
        parts.append({'name': n, 'channel': ch, 'notes': notes})
        what = ''
        if _is_kit(tr):
            pads = {pitch_to_midi(k): v for k, v in (tr['instrument'].get('map') or {}).items()}
            used = sorted({x[1] for x in sel})
            what = '; pads ' + ', '.join(f"{p} {midi_to_name(p)}={_pad_name(pads.get(p))}" for p in used[:16]) + \
                   (f" (+{len(used) - 16} more)" if len(used) > 16 else '')
        lines.append(f"  {n}: {len(notes)} notes, channel {ch + 1}" + (' (muted in the mix)' if tr.get('mute') else '')
                     + what)
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


def _pad_name(v):
    if isinstance(v, dict):
        v = v.get('name') or v.get('sample') or v.get('file') or v.get('sound') or v.get('type')
    return os.path.splitext(os.path.basename(str(v)))[0] if v else '?'


def _key(spec, f):
    """The notes a key in `into` names: a track's name, '#<index>', or 'ch<1-16>' (that channel in every track)."""
    s = str(spec).strip()
    if s.startswith('#') and s[1:].isdigit():
        k = int(s[1:])
        if k >= len(f['tracks']):
            raise OpError(f"{s}: the file has tracks #0-#{len(f['tracks']) - 1}")
        return f['tracks'][k]['notes']
    if s.lower().startswith('ch') and s[2:].isdigit():
        ch = int(s[2:]) - 1
        if not 0 <= ch <= 15:
            raise OpError(f"{s}: channels are ch1 to ch16")
        return [n for t in f['tracks'] for n in t['notes'] if n[4] == ch]
    hits = [t for t in f['tracks'] if t['name'] == s]
    if len(hits) != 1:
        raise OpError(f"{s!r} is not one track name in the file; call midi_read without into to list them, then use "
                      f"the name, '#<index>' or 'ch<n>'")
    return hits[0]['notes']


@op(mutates=True)
def midi_read(project: str, file: str, into: dict = None, bar: int = 1, mode: str = 'replace',
              quantize: float = None) -> str:
    """Read a Standard MIDI File (.mid, from a DAW, a score program or the web). Without `into`, it lists what the
    file holds: tempo and meter (and any changes), markers, and every track and channel with its note count, range
    and bars. With into={<file track>: <ismail track>} it writes those notes into existing tracks (track_add first),
    where <file track> is the track's name, '#<index>', or 'ch<1-16>' (that channel across the file, e.g. 'ch10' for
    drums). bar: where the file's bar 1 lands. mode='replace' clears the span the notes cover on each target first,
    'add' merges. quantize: a grid in beats (0.25 = 16ths) the starts snap to; default keeps them as played.
    Positions are read in beats, so a file at another tempo keeps its bars at this project's tempo (the reply says
    when the tempos differ)."""
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
            if not ns:
                L.append(f"  #{t['index']} {t['name'] or '(no name)'}: no notes")
                continue
            chs = sorted({n[4] + 1 for n in ns})
            lo, hi = min(n[1] for n in ns), max(n[1] for n in ns)
            L.append(f"  #{t['index']} {t['name'] or '(no name)'}: {len(ns)} notes, channel {'/'.join(map(str, chs))}"
                     f"{' (drums)' if chs == [10] else ''}, {midi_to_name(lo)}-{midi_to_name(hi)}, bars "
                     f"{fbar(ns[0][0])}-{fbar(max(n[0] for n in ns))}")
        L.append("write parts with into={'<name>' or '#<index>' or 'ch<n>': '<ismail track>'}, bar=..., quantize=...")
        return '\n'.join(L)
    base = P.bar_to_beat(bar)
    out, warn = [], []
    for spec, target in into.items():
        tr = P.track(target)
        notes = _key(spec, f)
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
    a live set with live_midi; outputs take a live track's notes (live_midi_out), the set's clock (live_midi_clock),
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
