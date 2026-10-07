"""The lexicon: a two-way map between a person's own words for what they hear and see and ismail's terms.

A music culture lives in how its people talk about sound ("muddy", "the pocket", "too clean", "30% there"). The
person's words carry what their ear and eye noticed, so an agent records each one, maps it to the system's terms
(an op, a parameter and its direction, an effect, a measurement), and keeps whether the change it led to worked.
Read back, the lexicon lets an agent say things the person's way, and shows how their vocabulary grows in each
craft (from "too bright" toward "the 3 kHz bump").

Words about the work only. Never record emotion, mood, health or any inference about the person: the lexicon
measures their eloquence in the vernacular, nothing else. The file is local (songs/_user/, never committed), one
per machine with a `who` on every entry, and nothing in it leaves the machine without the person's yes.

Storage: append-only JSON lines at $ISMAIL_LEXICON, else <songs>/_user/lexicon.jsonl. An update is a new line with
the same id; the latest line of an id wins, so the history of a word is kept.
"""
import contextvars
import json
import os
import re
import time
from collections import Counter, defaultdict
from contextlib import contextmanager

from .handoffs import SONGS

CRAFTS = ('composer', 'arranger', 'performer', 'sound designer', 'recording engineer', 'mixing engineer',
          'mastering engineer', 'producer', 'dj', 'director', 'cinematographer', 'colourist', 'editor',
          'choreographer', 'listener')
OUTCOMES = ('open', 'worked', 'partly', 'missed')

# words that belong to the trade, not to everyday description: their share in what a person says is the readout
# of a vocabulary moving from description toward technique
TRADE = set("""db dbfs lufs hz khz eq equalizer equaliser compressor compression limiter limiting sidechain gate
expander attack release decay sustain adsr envelope lfo cutoff resonance filter highpass lowpass hpf lpf shelf
notch q bandwidth reverb delay chorus flanger phaser saturation distortion fuzz overdrive transient transients
stereo mono mid side panning pan bus send return stem stems headroom clipping ceiling dither midrange low-end
sub harmonics harmonic overtone overtones formant formants vibrato tremolo portamento glide legato staccato
swing quantize quantized groove velocity voicing voicings inversion modulation detune unison wavetable oscillator
sample samples round-robin bpm tempo downbeat syncopation polyrhythm cadence key mode scale chord chords
lens grade lut exposure contrast saturation white-balance key-light fill rim bokeh framing shot cut""".split())


_SCOPED = contextvars.ContextVar('ismail_lexicon_file', default=None)


def path():
    return _SCOPED.get() or os.environ.get('ISMAIL_LEXICON') or os.path.join(SONGS, '_user', 'lexicon.jsonl')


@contextmanager
def scoped(file):
    """Read and write another person's lexicon (a guest's, beside their song) for the length of a block; None
    leaves the machine's."""
    tok = _SCOPED.set(file) if file else None
    try:
        yield
    finally:
        if tok is not None:
            _SCOPED.reset(tok)


def _read():
    p = path()
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding='utf8') as f:
        for ln in f:
            ln = ln.strip()
            if ln:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue
    return out


def entries(who=None):
    """The current state of every entry (the latest line of each id), oldest first."""
    cur = {}
    for e in _read():
        cur[e['id']] = dict(cur.get(e['id'], {}), **e)
    es = sorted(cur.values(), key=lambda e: e.get('first', e.get('date', '')))
    return [e for e in es if who is None or e.get('who') == who]


@contextmanager
def _lock(timeout=10.0):
    """Several sessions write one lexicon: hold a lock file while an id is chosen and its line appended."""
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    lock = path() + '.lock'
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 30:      # a holder that died mid-write
                    os.remove(lock)
                    continue
            except OSError:
                continue
            if time.time() - t0 > timeout:
                raise ValueError(f"the lexicon {lock} stayed locked for {timeout:.0f} s; try again")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        os.remove(lock)


def _append(rec):
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'a', encoding='utf8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def _words(text):
    return [w for w in re.findall(r"[a-z0-9][a-z0-9'+-]*", (text or '').lower()) if len(w) > 1]


def trade_share(text):
    ws = _words(text)
    return (sum(1 for w in ws if w in TRADE) / len(ws)) if ws else 0.0


def _norm_craft(craft):
    if craft is None:
        return None
    c = craft.strip().lower().replace('_', ' ').replace('colorist', 'colourist')
    if c not in CRAFTS:
        raise ValueError(f"craft {craft!r}: use one of {', '.join(CRAFTS)} (the role a record used to need a person for)")
    return c


def note(said=None, means=None, craft=None, song=None, where=None, outcome=None, why=None, who='user', id=None):
    """Add an entry, or update entry `id` (means, craft, outcome, why). -> the entry as stored."""
    now = time.strftime('%Y-%m-%dT%H:%M:%S')
    if outcome is not None and outcome not in OUTCOMES:
        raise ValueError(f"outcome {outcome!r}: use one of {', '.join(OUTCOMES)}")
    if means is not None and isinstance(means, str):
        means = [m.strip() for m in means.split(';') if m.strip()]
    craft = _norm_craft(craft)
    with _lock():
        return _note(said, means, craft, song, where, outcome, why, who, id, now)


def _note(said, means, craft, song, where, outcome, why, who, id, now):
    if id:
        cur = {e['id']: e for e in entries()}
        if id not in cur:
            raise ValueError(f"no entry {id!r}; lexicon_find to look one up")
        rec = {'id': id, 'date': now}
        for k, v in (('means', means), ('craft', craft), ('outcome', outcome), ('why', why), ('where', where)):
            if v is not None:
                rec[k] = v
        if said:
            raise ValueError("an entry's words are what the person said and never change; note a new entry instead")
        _append(rec)
        return dict(cur[id], **rec)
    if not said or not said.strip():
        raise ValueError("said: the person's own words, verbatim (a quote, not your paraphrase)")
    n = len({e['id'] for e in _read()}) + 1
    rec = {'id': f'L{n:04d}', 'said': said.strip(), 'who': who, 'first': now, 'date': now, 'song': song,
           'where': where, 'means': means or [], 'craft': craft, 'outcome': outcome or 'open', 'why': why,
           'trade': round(trade_share(said), 3)}
    _append(rec)
    return rec


def find(text, who='user', limit=8):
    """Entries whose words or system terms share words with `text`, best first: [(score, entry, 'said'|'means')]."""
    q = set(_words(text))
    if not q:
        return []
    hits = []
    for e in entries(who):
        s_said = len(q & set(_words(e.get('said'))))
        s_means = len(q & set(_words(' '.join(e.get('means') or []))))
        if s_said or s_means:
            hits.append((max(s_said, s_means) + 0.5 * min(s_said, s_means), e,
                         'said' if s_said >= s_means else 'means'))
    hits.sort(key=lambda h: (-h[0], h[1].get('first', '')))
    return hits[:limit]


def line(e):
    m = '; '.join(e.get('means') or []) or '(not mapped yet)'
    tail = ' '.join(x for x in (e.get('craft') or '', e.get('song') or '', e.get('where') or '') if x)
    return (f"{e['id']} \"{e['said']}\" -> {m} | {e.get('outcome', 'open')}" + (f": {e['why']}" if e.get('why') else '')
            + (f" | {tail}" if tail else ''))


def view(who='user', craft=None, since=None, last=10):
    """A bounded readout: counts by craft and outcome, the trade-word share over time, the newest entries."""
    es = [e for e in entries(who) if (not craft or e.get('craft') == _norm_craft(craft))
          and (not since or e.get('first', '') >= since)]
    if not es:
        return [f"lexicon ({path()}): no entries" + (f" for {who}" if who else '') + ". lexicon_note records one."]
    L = [f"lexicon of {who}: {len(es)} entries, {es[0]['first'][:10]} to {es[-1]['first'][:10]} ({path()})"]
    cr = Counter(e.get('craft') or 'unassigned' for e in es)
    L.append("  by craft: " + ', '.join(f"{k} {v}" for k, v in cr.most_common()))
    oc = Counter(e.get('outcome', 'open') for e in es)
    L.append("  outcomes: " + ', '.join(f"{k} {oc[k]}" for k in OUTCOMES if oc[k]))
    by_month = defaultdict(list)
    for e in es:
        by_month[e.get('first', '')[:7]].append(e.get('trade', trade_share(e.get('said'))))
    L.append("  trade words in what was said, by month (description -> technique): "
             + ', '.join(f"{m} {100 * sum(v) / len(v):.0f}% of {len(v)}" for m, v in sorted(by_month.items())))
    weeks = Counter(time.strftime('%G-W%V', time.strptime(e['first'][:10], '%Y-%m-%d')) for e in es if e.get('first'))
    L.append("  new entries per week: " + ', '.join(f"{w} {n}" for w, n in sorted(weeks.items())[-8:]))
    unmapped = [e['id'] for e in es if not e.get('means')]
    if unmapped:
        L.append(f"  not mapped yet: {', '.join(unmapped[:12])}" + (' ...' if len(unmapped) > 12 else '')
                 + " (lexicon_note(id=..., means=...) once you know what it meant)")
    L.append(f"  newest {min(last, len(es))}:")
    L += ['    ' + line(e) for e in es[-last:]]
    return L
