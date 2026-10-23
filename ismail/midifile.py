"""Standard MIDI Files (.mid), written and read with no library: the format is small (ledger:M113, Nate 10-23: "full
midi like ableton"; plan in D:/ismail-hq/proposals/midi-parity.md).

write(path, tracks, bpm, beats_per_bar, markers, name) makes a type 1 file: a conductor track (name, tempo, meter,
markers) and one track per part (name, channel, notes). read(path) gives every track's notes in beats (quarter
notes), with the tempo map, meters, markers and names, from type 0 or 1 files.
"""
import struct

PPQ = 960                      # ticks per quarter note: a tick is about 0.5 ms at 120 BPM


def _vlq(n):
    """A variable-length quantity, as MIDI files store delta times and lengths."""
    n = int(n)
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def _meta(kind, data):
    return b'\xff' + bytes([kind]) + _vlq(len(data)) + data


def _chunk(events):
    """events: [(tick, order, bytes)] -> an MTrk chunk, sorted, with delta times and an end-of-track."""
    body, last = bytearray(), 0
    for tick, _, data in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - last) + data
        last = tick
    body += _vlq(0) + _meta(0x2F, b'')
    return b'MTrk' + struct.pack('>I', len(body)) + bytes(body)


def _text(s):
    return str(s).encode('latin-1', 'replace')


def write(path, tracks, bpm, beats_per_bar=4, markers=None, name=None):
    """tracks: [{'name': str, 'channel': 0-15, 'notes': [(start_beat, pitch, dur_beats, vel)]}]. markers:
    [(beat, text)]. Same-pitch notes on one channel that overlap are cut where the next one starts, so every
    note-off belongs to its own note. Returns the number of notes written."""
    tempo = round(60_000_000 / float(bpm))
    num = int(beats_per_bar)
    head = [(0, 0, _meta(0x03, _text(name or 'ismail'))), (0, 1, _meta(0x51, tempo.to_bytes(3, 'big'))),
            (0, 2, _meta(0x58, bytes([num, 2, 24, 8])))]
    head += [(round(b * PPQ), 3, _meta(0x06, _text(t))) for b, t in (markers or [])]
    chunks, count = [_chunk(head)], 0
    for tr in tracks:
        ch = int(tr.get('channel', 0)) & 0x0F
        ev = [(0, 0, _meta(0x03, _text(tr.get('name', ''))))]
        if tr.get('program') is not None:
            ev.append((0, 1, bytes([0xC0 | ch, int(tr['program']) & 0x7F])))
        notes = sorted((round(s * PPQ), int(p), max(1, round(d * PPQ)), v) for s, p, d, v in tr['notes'])
        nxt = {}
        for i in range(len(notes) - 1, -1, -1):          # the next start of the same pitch, to cut overlaps
            t, p, d, v = notes[i]
            end = t + d
            if p in nxt and nxt[p] < end:
                end = max(t + 1, nxt[p])
            nxt[p] = t
            vel = max(1, min(127, int(round(v))))
            ev.append((end, 2, bytes([0x80 | ch, p & 0x7F, 0])))        # offs before ons at the same tick
            ev.append((t, 3, bytes([0x90 | ch, p & 0x7F, vel])))
            count += 1
        chunks.append(_chunk(ev))
    with open(path, 'wb') as f:
        f.write(b'MThd' + struct.pack('>IHHH', 6, 1, len(chunks), PPQ))
        for c in chunks:
            f.write(c)
    return count


class MidiFileError(ValueError):
    pass


def _read_vlq(b, i):
    n = 0
    while True:
        c = b[i]
        i += 1
        n = (n << 7) | (c & 0x7F)
        if not c & 0x80:
            return n, i


def read(path):
    """{'ppq', 'format', 'tempos': [(beat, bpm)], 'meters': [(beat, num, den)], 'markers': [(beat, text)],
    'tracks': [{'index', 'name', 'channels': set, 'programs': {ch: prog}, 'notes': [(beat, pitch, dur, vel, ch)]}]}.
    Beats are quarter notes from the start of the file. A note-on with no note-off is held to the track's end."""
    with open(path, 'rb') as f:
        b = f.read()
    if b[:4] != b'MThd':
        raise MidiFileError(f"{path} is not a MIDI file (no MThd header)")
    hl, fmt, ntr, div = struct.unpack('>IHHH', b[4:14])
    if div & 0x8000:
        raise MidiFileError("this file counts time in SMPTE frames, not beats; open it in a DAW and save it again "
                            "with a beats-based resolution")
    out = {'ppq': div, 'format': fmt, 'tempos': [], 'meters': [], 'markers': [], 'tracks': []}
    i = 8 + hl
    for k in range(ntr):
        while i < len(b) and b[i:i + 4] != b'MTrk':          # skip unknown chunks
            i += 8 + struct.unpack('>I', b[i + 4:i + 8])[0]
        if i >= len(b):
            break
        ln = struct.unpack('>I', b[i + 4:i + 8])[0]
        j, end = i + 8, i + 8 + ln
        i = end
        tick, status, held = 0, 0, {}
        tr = {'index': k, 'name': '', 'channels': set(), 'programs': {}, 'notes': []}
        while j < end:
            dt, j = _read_vlq(b, j)
            tick += dt
            c = b[j]
            if c == 0xFF:
                kind = b[j + 1]
                n, j = _read_vlq(b, j + 2)
                data, j = b[j:j + n], j + n
                beat = tick / div
                if kind == 0x03 and not tr['name']:
                    tr['name'] = data.decode('latin-1')
                elif kind == 0x51 and n == 3:
                    out['tempos'].append((beat, 60_000_000 / int.from_bytes(data, 'big')))
                elif kind == 0x58 and n >= 2:
                    out['meters'].append((beat, data[0], 2 ** data[1]))
                elif kind == 0x06:
                    out['markers'].append((beat, data.decode('latin-1')))
                elif kind == 0x2F:
                    break
                continue
            if c in (0xF0, 0xF7):
                n, j = _read_vlq(b, j + 1)
                j += n
                continue
            if c & 0x80:
                status, j = c, j + 1
            elif not status:
                raise MidiFileError(f"track {k}: data byte with no status at byte {j}")
            typ, ch = status & 0xF0, status & 0x0F
            size = 1 if typ in (0xC0, 0xD0) else 2
            d = b[j:j + size]
            j += size
            if typ == 0x90 and d[1] > 0:
                held.setdefault((ch, d[0]), []).append((tick, d[1]))
                tr['channels'].add(ch)
            elif typ in (0x80, 0x90):
                ons = held.get((ch, d[0]))
                if ons:
                    t0, v = ons.pop(0)
                    tr['notes'].append((t0 / div, d[0], max(tick - t0, 1) / div, v, ch))
            elif typ == 0xC0:
                tr['programs'].setdefault(ch, d[0])
        for (ch, p), ons in held.items():                    # never released: held to the track's end
            for t0, v in ons:
                tr['notes'].append((t0 / div, p, max(tick - t0, 1) / div, v, ch))
        tr['notes'].sort()
        out['tracks'].append(tr)
    return out
