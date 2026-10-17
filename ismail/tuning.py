"""The tuning table an instrument carries (ledger:M231): cents added to the notes it plays, so a part is put in tune,
detuned on purpose, or played in another tuning system (a maqam, just intonation, 24 or 19 tones to the octave)
without editing a note.

  "tuning": {"scale": {"edo": 24, "root": "C4"},           a whole tuning system (below); optional
             "all": -3,                                   every note
             "pitch_class": {"E": -14, "B": -12},          that pitch class in every octave
             "notes": {"F#3": 12, "C1": -40},              one exact note (in a kit: the mapped pitch, one drum)
             "strings": {"6": {"all": -5, "0": 2, "3": -4}}}   a voice that puts notes on strings: string 1 is the
                                                                 highest; cents for the whole string ("all") and per
                                                                 fret ("0" = open)

The parts ADD: a note's offset = scale + all + pitch_class + notes + its string's all + its fret. Each part is read
alone, so a table written from a measurement (part 3, instrument_tune) is "minus what was measured" per note.

Two ways to a tuning system:
  * on the usual keys, 12 per octave: a pitch_class table (just intonation, a maqam whose E and B are half flat:
    {"pitch_class": {"E": -50, "B": -50}}).
  * any number of steps per period, keys in a row: "scale": {"steps": [0, 150, 300, ...], "period": 1200,
    "root": "C4"} or {"edo": 19, "period": 1200, "root": "C4"} (equal steps). Key root + k plays step k mod n in
    period k // n (Scala's linear keyboard mapping): with 24 steps each key is a quarter tone, with a 7-step maqam
    seven keys in a row span the octave. The root key keeps its 12-TET pitch.
Keys are normalised on write: note names with sharps (Eb3 -> D#3, 51 -> D#3), pitch classes without octave,
strings and frets as numbers; zero entries are dropped. Values are cents, -1200..1200, kept to 0.1 c.

A song can carry a table of the same shape (project.json 'tuning'): the tuning every instrument inherits, the target.
An instrument's own table adds on top of it (it corrects toward the song's tuning, or detunes from it on purpose);
a scale in an instrument's table replaces the song's scale. 12-TET (A4 = 440 Hz) holds only when neither is set.

intended_cents(instrument, midi, song=...) (or project_intended_cents(project, track, midi)) is the pitch a key is
meant to sound at (cents from A4 = 440 Hz), so a measurement can check a part against the tuning it was given rather
than against 12-TET.
"""
import math
import re

from .notation import NAMES, NotationError, midi_to_name, pitch_to_midi

MAX_CENTS = 1200.0
FIELDS = ('scale', 'all', 'pitch_class', 'notes', 'strings')
MAX_PERIOD = 4800.0
MAX_STEPS = 128
MAX_STRINGS = 12
MAX_FRET = 36
_PC_RE = re.compile(r'^([A-Ga-g])([#b]*)$')
_SEMI = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}


class TuningError(ValueError):
    """A table that cannot be read; the message says how to write it."""


def _cents(v, where):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise TuningError(f"{where}={v!r}: give cents as a number (+12 = 12 cents sharp, -50 = a quarter tone flat)")
    if abs(v) > MAX_CENTS:
        raise TuningError(f"{where}={v:+g} c is more than an octave: give -1200..1200 cents. To move notes by "
                          f"semitones, move the notes (notes_transform) or a sampler's root/transpose")
    return round(float(v), 1)


def pitch_class_key(k):
    """'Eb' / 'D#' / 3 -> 'D#'."""
    if isinstance(k, int) and not isinstance(k, bool) or (isinstance(k, str) and re.fullmatch(r'\d+', k.strip())):
        n = int(k)
        if 0 <= n <= 11:
            return NAMES[n]
    else:
        m = _PC_RE.match(str(k).strip())
        if m:
            acc = m.group(2).count('#') - m.group(2).count('b')
            return NAMES[(_SEMI[m.group(1).upper()] + acc) % 12]
    hint = " (for one exact note use 'notes')" if re.fullmatch(r'[A-Ga-g][#b]*-?\d+', str(k).strip()) else ''
    raise TuningError(f"pitch_class {k!r}: a pitch class is a note name without its octave (C, F#, Bb) or 0-11 "
                      f"(C = 0){hint}")


def note_key(k):
    """'Eb3' / 'D#3' / 51 / '51' -> 'D#3'."""
    try:
        if isinstance(k, bool) or (isinstance(k, float) and k != int(k)):
            raise NotationError(k)
        m = pitch_to_midi(k)
    except (NotationError, ValueError, TypeError):
        m = None
    if m is None or not 0 <= m <= 127:
        hint = " (for every octave of it use 'pitch_class')" if _PC_RE.match(str(k).strip()) else ''
        raise TuningError(f"notes {k!r}: not a note: write a name with its octave (C4 = 60, F#3, Bb2) or a MIDI "
                          f"number 0-127{hint}")
    return midi_to_name(m)


def string_key(k):
    s = str(k).strip()
    if re.fullmatch(r'\d+', s) and 1 <= int(s) <= MAX_STRINGS:
        return str(int(s))
    raise TuningError(f"strings {k!r}: a string number 1-{MAX_STRINGS}, 1 = the highest string (on a guitar 1 is "
                      f"high E, 6 is low E)")


def fret_key(k, string):
    s = str(k).strip().lower()
    if s in ('all', 'open'):
        return 'all' if s == 'all' else '0'
    if re.fullmatch(r'\d+', s) and int(s) <= MAX_FRET:
        return str(int(s))
    raise TuningError(f"strings.{string} {k!r}: a fret 0-{MAX_FRET} ('open' = 0), or 'all' for the whole string")


def _table(d, keyfn, where):
    if not isinstance(d, dict):
        raise TuningError(f"{where}: a table {{key: cents}}, e.g. {where}={{'E': -14}}" if where == 'pitch_class'
                          else f"{where}: a table {{key: cents}}, e.g. {where}={{'F#3': 12}}")
    out = {}
    for k, v in d.items():
        key = keyfn(k)
        c = _cents(v, f"{where}.{k}")
        if key in out and out[key] != c:
            raise TuningError(f"{where}: {key!r} given twice with different cents (as {k!r} too): keep one")
        out[key] = c
    return out


def normalize_scale(sc):
    """A scale part: {'steps': [0, ...], 'period': 1200, 'root': 'C4'} or {'edo': n, 'period': 1200, 'root': 'C4'}."""
    if not isinstance(sc, dict):
        raise TuningError("scale: a table {'edo': 24, 'root': 'C4'} or {'steps': [0, 150, 300, ...], 'period': 1200, "
                          "'root': 'C4'} (cents of each step from the root)")
    bad = set(sc) - {'steps', 'edo', 'period', 'root'}
    if bad:
        raise TuningError(f"scale: unknown keys {sorted(bad)}; keys: steps or edo, period, root")
    if ('steps' in sc) == ('edo' in sc):
        raise TuningError("scale: give 'steps' (cents of each step from the root, starting at 0) or 'edo' (that many "
                          "equal steps per period), one of them")
    per = sc.get('period', 1200.0)
    if isinstance(per, bool) or not isinstance(per, (int, float)) or not 0 < per <= MAX_PERIOD:
        raise TuningError(f"scale.period={per!r}: the interval the steps repeat at, in cents (1200 = an octave; "
                          f"1901.96 = a tritave), 0 < period <= {MAX_PERIOD:g}")
    out = {}
    if 'edo' in sc:
        e = sc['edo']
        if isinstance(e, bool) or not isinstance(e, int) or not 1 <= e <= MAX_STEPS:
            raise TuningError(f"scale.edo={e!r}: a whole number of equal steps per period, 1-{MAX_STEPS} (24 = "
                              f"quarter tones, 19, 31 ...)")
        out['edo'] = e
    else:
        st = sc['steps']
        if not isinstance(st, (list, tuple)) or not 1 <= len(st) <= MAX_STEPS:
            raise TuningError(f"scale.steps: a list of 1-{MAX_STEPS} cents from the root, starting at 0, e.g. a maqam "
                              f"[0, 200, 350, 500, 700, 900, 1050]")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in st):
            raise TuningError("scale.steps: numbers, cents from the root (0, 150, 300 ...)")
        vals = [round(float(v), 2) for v in st]
        if vals[0] != 0 or any(b <= a for a, b in zip(vals, vals[1:])) or vals[-1] >= per:
            raise TuningError(f"scale.steps={vals}: start at 0, rise step by step, and stay below the period "
                              f"({per:g} c): the period itself is the next step's 0")
        out['steps'] = vals
    out['period'] = round(float(per), 2)
    try:
        out['root'] = note_key(sc.get('root', 'C4'))
    except TuningError:
        raise TuningError(f"scale.root={sc.get('root')!r}: the key that keeps its 12-TET pitch and plays step 0, a "
                          f"note with its octave (C4, D3) or a MIDI number")
    return out


def scale_cents(sc, midi):
    """The cents a scale moves key `midi` from its 12-TET pitch."""
    root = pitch_to_midi(sc['root'])
    k = int(round(midi)) - root
    steps = sc.get('steps')
    n = len(steps) if steps else sc['edo']
    q, r = divmod(k, n)
    step = steps[r] if steps else r * sc['period'] / n
    return q * sc['period'] + step - 100.0 * k


def _clean(t):
    """Drop zero entries and empty parts, order keys for reading; {} -> None."""
    out = {}
    if t.get('scale'):
        out['scale'] = t['scale']
    if t.get('all'):
        out['all'] = t['all']
    pc = {k: t['pitch_class'][k] for k in NAMES if t.get('pitch_class', {}).get(k)}
    if pc:
        out['pitch_class'] = pc
    ns = {k: v for k, v in sorted((t.get('notes') or {}).items(), key=lambda kv: pitch_to_midi(kv[0])) if v}
    if ns:
        out['notes'] = ns
    st = {}
    for s in sorted(t.get('strings') or {}, key=int):
        fr = t['strings'][s]
        row = {k: fr[k] for k in sorted(fr, key=lambda x: -1 if x == 'all' else int(x)) if fr[k]}
        if row:
            st[s] = row
    if st:
        out['strings'] = st
    return out or None


def normalize(t):
    """Validate a tuning table and write it in its normal form (see the module doc); None when it holds nothing.
    A bare number is the whole-instrument offset."""
    if t is None:
        return None
    if isinstance(t, (list, tuple)):
        raise TuningError("tuning is a table {'all': c, 'pitch_class': {...}, 'notes': {...}, 'strings': {...}}; "
                          "a performer voice's open strings (a list of MIDI numbers) go in params.tuning")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        t = {'all': t}
    if not isinstance(t, dict):
        raise TuningError(f"tuning={t!r}: a table {{'all': c, 'pitch_class': {{'E': -14}}, 'notes': {{'F#3': 12}}}} "
                          f"in cents")
    bad = [k for k in t if k not in FIELDS]
    if bad:
        notes = [k for k in bad if _looks_note(k)]
        hint = f" (put note entries under 'notes', e.g. {{'notes': {{{notes[0]!r}: c}}}})" if notes else ''
        raise TuningError(f"tuning: unknown part {bad}; parts: {', '.join(FIELDS)}{hint}")
    out = {}
    if t.get('scale') is not None:
        out['scale'] = normalize_scale(t['scale'])
    if t.get('all') is not None:
        out['all'] = _cents(t['all'], 'all')
    if t.get('pitch_class') is not None:
        out['pitch_class'] = _table(t['pitch_class'], pitch_class_key, 'pitch_class')
    if t.get('notes') is not None:
        out['notes'] = _table(t['notes'], note_key, 'notes')
    if t.get('strings') is not None:
        if not isinstance(t['strings'], dict):
            raise TuningError("strings: a table {string: {fret: cents}}, string 1 = the highest, e.g. "
                              "{'6': {'all': -5, '3': 4}}")
        st = {}
        for s, frets in t['strings'].items():
            sk = string_key(s)
            if isinstance(frets, (int, float)) and not isinstance(frets, bool):
                frets = {'all': frets}
            st[sk] = _table(frets, lambda f, s=sk: fret_key(f, s), f"strings.{sk}")
        out['strings'] = st
    return _clean(out)


def _looks_note(k):
    try:
        note_key(k)
        return True
    except TuningError:
        return bool(_PC_RE.match(str(k).strip()))


def merge(old, new):
    """`new` (a partial table) over `old`: entries are set or replaced one by one, and None removes one (a part, a
    note, a pitch class, a string or a fret). -> the normal form, or None when nothing is left."""
    if isinstance(new, (int, float)) and not isinstance(new, bool):
        new = {'all': new}
    if not isinstance(new, dict):
        normalize(new)                       # raises the same advice
        raise TuningError("tuning: give a table")
    out = normalize(old) or {}
    bad = [k for k in new if k not in FIELDS]
    if bad:
        normalize({k: new[k] for k in bad})  # raises with the advice for those keys
    for part, val in new.items():
        if val is None:
            out.pop(part, None)
        elif part == 'scale':                # a scale is replaced whole
            out['scale'] = normalize_scale(val)
        elif part == 'all':
            out['all'] = _cents(val, 'all')
        elif part in ('pitch_class', 'notes'):
            keyfn = pitch_class_key if part == 'pitch_class' else note_key
            if not isinstance(val, dict):
                _table(val, keyfn, part)
            cur = dict(out.get(part) or {})
            for k, v in val.items():
                if v is None:
                    cur.pop(keyfn(k), None)
                else:
                    cur[keyfn(k)] = _cents(v, f"{part}.{k}")
            out[part] = cur
        else:
            if not isinstance(val, dict):
                normalize({'strings': val})
            cur = {s: dict(f) for s, f in (out.get('strings') or {}).items()}
            for s, frets in val.items():
                sk = string_key(s)
                if frets is None:
                    cur.pop(sk, None)
                    continue
                if isinstance(frets, (int, float)) and not isinstance(frets, bool):
                    frets = {'all': frets}
                if not isinstance(frets, dict):
                    _table(frets, None, f"strings.{sk}")
                row = cur.setdefault(sk, {})
                for f, v in frets.items():
                    fk = fret_key(f, sk)
                    if v is None:
                        row.pop(fk, None)
                    else:
                        row[fk] = _cents(v, f"strings.{sk}.{f}")
            out['strings'] = cur
    return _clean(out)


def cents(t, midi, string=None, fret=None):
    """The offset in cents a (normalised) table gives one note; string (1 = highest) and fret when the voice knows
    where the note is played."""
    if not t:
        return 0.0
    m = int(round(midi))
    c = t.get('all', 0.0)
    if 'scale' in t:
        c += scale_cents(t['scale'], m)
    if 'pitch_class' in t:
        c += t['pitch_class'].get(NAMES[m % 12], 0.0)
    if 'notes' in t:
        c += t['notes'].get(midi_to_name(m), 0.0)
    if string is not None and 'strings' in t:
        row = t['strings'].get(str(int(string)))
        if row:
            c += row.get('all', 0.0)
            if fret is not None and fret >= 0:
                c += row.get(str(int(fret)), 0.0)
    return c


def cents_fn(t, extra=0.0):
    """A performer's cents_of(midi, string=None, fret=None) for table t plus `extra` cents (a kit's entry for the
    piece); None when there is nothing to apply."""
    if not t and not extra:
        return None

    def cents_of(midi, string=None, fret=None):
        return extra + cents(t, midi, string, fret)
    return cents_of


def describe(t):
    """The table as lines of text, one per part."""
    if not t:
        return ["(no tuning table: every note plays at the 12-TET pitch, A4 = 440 Hz)"]
    L = []
    sc = t.get('scale')
    if sc:
        what = (f"{sc['edo']} equal steps" if 'edo' in sc else
                f"{len(sc['steps'])} steps " + ', '.join(f"{v:g}" for v in sc['steps']))
        L.append(f"scale {what} per {sc['period']:g} c from {sc['root']} (keys in a row play the steps)")
    if 'all' in t:
        L.append(f"all {t['all']:+g} c")
    if 'pitch_class' in t:
        L.append("pitch_class " + ', '.join(f"{k} {v:+g}" for k, v in t['pitch_class'].items()))
    if 'notes' in t:
        L.append("notes " + ', '.join(f"{k} {v:+g}" for k, v in t['notes'].items()))
    for s, row in (t.get('strings') or {}).items():
        L.append(f"string {s} " + ', '.join(f"{'all' if k == 'all' else 'fret ' + k} {v:+g}" for k, v in row.items()))
    return L


def combine(song, own):
    """The table an instrument plays: the song's tuning (project.json 'tuning', the target every instrument
    inherits) with the instrument's own table on top. Cents add part by part (the instrument corrects toward the
    song's tuning, or detunes from it); a scale in the instrument's table replaces the song's (one instrument in
    another tuning system). Both normalised -> the normal form, or None."""
    if not song:
        return own or None
    if not own:
        return song
    out = {'scale': own.get('scale') or song.get('scale'), 'all': song.get('all', 0.0) + own.get('all', 0.0)}
    for part in ('pitch_class', 'notes'):
        d = dict(song.get(part) or {})
        for k, v in (own.get(part) or {}).items():
            d[k] = round(d.get(k, 0.0) + v, 1)
        out[part] = d
    st = {s: dict(f) for s, f in (song.get('strings') or {}).items()}
    for s, frets in (own.get('strings') or {}).items():
        row = st.setdefault(s, {})
        for f, v in frets.items():
            row[f] = round(row.get(f, 0.0) + v, 1)
    out['strings'] = st
    return _clean({k: v for k, v in out.items() if v})


def with_song(inst, song):
    """inst (an instrument dict) with the song's tuning folded into its 'tuning' (combine); inst itself when the song
    has none. The renderers and the live decks play this."""
    song = normalize(song) if song else None
    if not song or not inst:
        return inst
    t = combine(song, normalize(inst.get('tuning')))
    out = {k: v for k, v in inst.items() if k != 'tuning'}
    if t:
        out['tuning'] = t
    return out


def offset_cents(inst, midi, string=None, fret=None, song=None):
    """The cents key `midi` sounds away from its 12-TET pitch: the song's tuning (song: project.json's 'tuning')
    combined with the instrument's table, and in a kit the kit's entry plus the piece's own table. inst: an
    instrument dict (raw or normalised), a bare table, or None (the song's tuning alone)."""
    if inst and 'type' not in inst:
        inst = {'type': 'synth', 'tuning': inst}
    inst = with_song(inst or {'type': 'synth'}, song)
    c = cents(normalize(inst.get('tuning')), midi, string, fret)
    if inst.get('type') == 'kit':
        m = int(round(midi))
        for k, piece in (inst.get('map') or {}).items():
            try:
                if pitch_to_midi(k) == m:
                    c += cents(normalize((piece or {}).get('tuning')), m)
                    break
            except NotationError:
                continue
    return c


def intended_cents(inst, midi, string=None, fret=None, song=None):
    """The pitch key `midi` is meant to sound at, in cents from A4 = 440 Hz (A4 = 0, C4 = -900): its 12-TET pitch
    moved by the song's tuning (song: project.json's 'tuning') and the instrument's table (a scale, deliberate
    microtones and detunes included); 12-TET only when neither is set. A measurement compares what it reads with
    this, not with the 12-TET grid. For a piece that plays the same sound whatever the key (a drum synth, a one-shot
    sample) this is the key's pitch moved by the tables; that piece's own pitch is measured, not known."""
    return 100.0 * (int(round(midi)) - 69) + offset_cents(inst, midi, string, fret, song)


def project_intended_cents(project, track, midi, string=None, fret=None):
    """intended_cents for a note on a track of a loaded project dict (project.json): the song's tuning and the
    track's instrument; a track with no instrument reads the song's tuning alone."""
    tr = (project.get('tracks') or {}).get(track) or {}
    return intended_cents(tr.get('instrument'), midi, string, fret, project.get('tuning'))
