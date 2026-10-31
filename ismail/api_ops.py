"""ops_find: find the op for a need, by purpose, at the moment of need (ledger:M210, Nate 10-12: "the agent who's in
the right role [should] actually see their tools").

Nothing here is written about an op except which shelf it sits on: the search reads each op's own docstring, so it
cannot go stale while the docstrings are right. Every op sits on exactly one shelf (CATEGORIES, by name or name
prefix); tests/test_ops_find.py fails when a new op fits none, so a new op is shelved in the pull request that adds
it. A role is an ordered list of shelves: the ones it reaches for first come first.
"""
import inspect
import math
import re

from .api import OPS, OpError, op

# (shelf, what it is for, its op names and name prefixes (ending in _)); the order is the order shown
CATEGORIES = [
    ('start', "start here: the guide, projects, undo, batches, the machine and its disk, ismail's settings",
     ('guide', 'playbook', 'project_', 'undo', 'batch', 'credits', 'machine_', 'ops_find', 'song_compress',
      'config_', 'version')),
    ('sketch', 'a quick sketch from words, kept or dropped', ('sketch', 'sketch_keep', 'sketch_wait')),
    ('write', 'tracks, notes, patterns, buses, automation, stem maps',
     ('track_add', 'track_set', 'track_remove', 'bus_add', 'notes_write', 'notes_read', 'notes_clear', 'notes_copy',
      'notes_transform', 'pattern_write', 'automation_', 'stem_map_set', 'audio_place', 'track_arp')),
    ('sounds', 'choosing and making sounds: the kit by family, voices, instruments, presets, effects, samples, speech',
     ('kit', 'voices_list', 'voice_help', 'instrument_help', 'instrument_set', 'instrument_show', 'presets_list', 'fx_',
      'samples_', 'sound_list', 'sound_make', 'sound_import', 'sound_speak', 'sound_autotune')),
    ('fit', 'matching a sound or a level to a recording, measured',
     ('instrument_fit', 'track_fit', 'track_model', 'eq_match', 'mimic_measure', 'sound_extract', 'sound_compare',
      'levels_from_ref', 'ref_retune', 'feel_from_ref')),
    ('render', 'rendering, mastering a finished cue for delivery, and playing', ('render', 'play_file', 'master_cue')),
    ('midi', 'MIDI files and ports: the song or some tracks to a .mid any DAW opens, a .mid read into tracks, the '
             'MIDI inputs and outputs, a song played to a synth or a DAW',
     ('midi_write', 'midi_read', 'midi_ports', 'midi_play')),
    ('listen', 'measuring audio as text (and pictures): notes, chords, drums, timbre, formants, structure, zooms '
               'and where two takes differ; and the arrangement checked for parts that clash',
     ('analyze_', 'spectrogram', 'stem_parts', 'separate', 'align', 'compare', 'sound_zoom', 'sound_zoom_diff',
      'region_measure', 'notes_clash')),
    ('transcribe', 'audio to notes', ('notes_from_audio', 'notes_from_audio_loop', 'melody_consensus')),
    ('compare', 'your render against the reference, stored and drilled into bar by bar, and the tells the person '
               'keeps hearing, measured every round', ('cmp_', 'ear_pass', 'tell_')),
    ('exam', 'pages the person answers: blind exams, eye exams, picture rounds they mark with pins and boxes, '
             'feedback pages they answer by voice or typing', ('exam_', 'feedback_')),
    ('lexicon', "the person's own words for what they hear and see", ('lexicon_',)),
    ('together', 'listening together: record what plays (a mic only with their yes), their words as they say them, '
                 'mark moments and their messages, read back skips, stretches and what was said on one clock', ('listen_',)),
    ('live', 'playing live: decks, transitions, the live engine, MIDI keyboards played through it, its notes and '
             'clock out to synths and other apps, tracks frozen into one a deck plays, what a deck costs',
     ('live_', 'midi_', 'track_freeze', 'deck_cost')),
    ('phone', "the person's phone page: listening, panels, questions, exams, voice notes", ('phone_',)),
    ('stream', "live streaming: the set to several sites at once, the picture and its text, callouts and panels on "
               "screen (what plays, whose request, the line, chat), every site's chat read and answered, and the "
               "request queue the MC fills and the DJ plays from",
     ('stream_', 'venue_', 'chat_', 'request_')),
    ('asks', "before you ask the person: their standing grants, and which agent owns which questions", ('grant_',)),
    ('stage', 'the browser and VR stage: scenes, objects, actors, takes, views', ('stage_',)),
]

# a role -> its shelves, the ones to reach for first first
ROLES = {
    'song': ['start', 'sketch', 'write', 'sounds', 'render', 'midi', 'listen', 'compare', 'transcribe', 'fit', 'exam',
             'lexicon', 'together', 'asks'],
    'voice': ['listen', 'exam', 'fit', 'sounds', 'compare', 'render', 'phone', 'lexicon', 'asks'],
    'sound': ['sounds', 'fit', 'listen', 'compare', 'exam', 'render', 'asks'],
    'dj': ['live', 'phone', 'stream', 'listen', 'together', 'sounds', 'exam', 'lexicon', 'asks'],
    'mc': ['stream', 'phone', 'live', 'asks'],
    'stage': ['stage', 'phone', 'exam', 'listen', 'asks'],
    'film': ['stage', 'render', 'listen', 'exam', 'asks'],
    'dev': [c[0] for c in CATEGORIES],
}

# the ops a filtered MCP server always serves, so an agent with a few shelves can still find and read what it lacks,
# and see and change ismail's settings (a DJ's live trim, a stage agent's browser: ledger:M368)
DISCOVERY = ('guide', 'playbook', 'ops_find', 'config_get', 'config_set', 'config_unset', 'version')

# what the running MCP server serves: None (every op) or (set of op names, how it was chosen); set by mcp_server
_SERVED = None


def ops_for(roles=(), shelves=()):
    """The ops an MCP server filtered to these roles and shelves serves: every op on their shelves, plus DISCOVERY,
    in OPS order. An unknown role or shelf raises OpError naming the valid ones."""
    known = [c[0] for c in CATEGORIES]
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise OpError(f"unknown role {', '.join(bad)}; roles: {', '.join(ROLES)}")
    bad = [s for s in shelves if s not in known]
    if bad:
        raise OpError(f"unknown shelf {', '.join(bad)}; shelves: {', '.join(known)}")
    want = set(shelves).union(*(ROLES[r] for r in roles))
    return [n for n in OPS if n in DISCOVERY or category_of(n) in want]


def serve_only(names, how=''):
    """Tell ops_find what the running MCP server serves: these op names, chosen by `how` (names None: every op)."""
    global _SERVED
    _SERVED = None if names is None else (set(names), how)


def _here(name):
    return '' if _SERVED is None or name in _SERVED[0] else '  (not on this server)'


def _served_note():
    if _SERVED is None:
        return []
    return [f"note: this MCP server is filtered to {_SERVED[1]} and serves {len(_SERVED[0])} of {len(OPS)} ops. An op "
            "marked (not on this server) needs the server started with its role or shelf (--roles or --shelves, or "
            "config_set('mcp.roles', [...]) and a restart), or the CLI: python -m ismail -p <project> <op>"]


_STOP = set('a an the and or of to in on for with by at from as is are be it its this that my our your their i we '
            'you he she they me them do does how what which when where who can could should would want need make '
            'get see show let use using into out up between vs versus just some any all there here whether'.split())


def category_of(name):
    """The shelf an op sits on, or None (the test fails on None). An exact name beats a prefix."""
    for cat, _, members in CATEGORIES:
        if name in members:
            return cat
    for cat, _, members in CATEGORIES:
        if any(name.startswith(m) for m in members if m.endswith('_')):
            return cat
    return None


def _stem(w):
    """Crude, on purpose: tune/tuning/tuned and mark/marking/marks meet on one stem."""
    for suf in ('ings', 'ing', 'ies', 'ed', 'es', 's'):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            w = w[:-len(suf)]
            break
    return w[:-1] if len(w) > 4 and w.endswith('e') else w


def _words(text):
    return [_stem(w) for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in _STOP]


def _first_line(name):
    doc = inspect.getdoc(OPS[name]) or ''
    s = doc.split('\n\n')[0].replace('\n', ' ')
    m = re.match(r'(.+?[.!?])(\s|$)', s)
    return (m.group(1) if m else s)[:200]


def _reach(doc):
    """An op's 'Reach for it when ...' sentence: the asks it answers, in the words people use. Weighted like the first
    line, so an op author steers the search from the description itself."""
    m = re.search(r'Reach for it (?:when|whenever|for)\b(.+?)(?:\.\s|\.$|$)', doc.replace('\n', ' '))
    return m.group(1) if m else ''


def _index():
    """{op: (name words, first-line and reach-for words, all words)} and each word's idf, rebuilt per call (cheap,
    never stale)."""
    ix = {}
    for name in OPS:
        doc = inspect.getdoc(OPS[name]) or ''
        nm = set(_words(name.replace('_', ' ')))
        ix[name] = (nm, set(_words(_first_line(name) + ' ' + _reach(doc))), nm | set(_words(doc)))
    df = {}
    for _, _, allw in ix.values():
        for w in allw:
            df[w] = df.get(w, 0) + 1
    avg = sum(len(e[2]) for e in ix.values()) / len(ix)
    return ix, {w: math.log(1 + len(ix) / d) for w, d in df.items()}, avg


def _score(need, entry, idf, avg):
    """A rare word counts more than a common one, a word in the name or first line more than one further down, and
    a word further down less in a long description than in a short one (a long one says many things in passing)."""
    nm, first, allw = entry
    rest = 1.0 / (0.5 + 0.5 * len(allw) / avg)
    return sum(idf.get(w, 0) * (3 if w in nm else 2 if w in first else rest if w in allw else 0) for w in set(need))


@op()
def ops_find(need: str = None, role: str = None, category: str = None, n: int = 8) -> str:
    """Find the op for what you need, by purpose, from each op's own description (so it is never out of date).
    need: in plain words ("let the person mark the difference between two pictures", "formants of a vowel",
    "match a level to the reference"): the n best ops with their first line and shelf. role: song, voice, sound,
    dj, mc, stage, film or dev: the ops that role reaches for first, shelf by shelf (with need: the search, the role's
    shelves first). category: every op on one shelf. No arguments: the shelves and the roles. Then read an op's full
    description through your tool list, or call it. Writing an op: a sentence 'Reach for it when ...' in its
    description, in the words a person or agent would ask with, counts as much as its first line here."""
    if role is not None and role not in ROLES:
        raise OpError(f"unknown role {role!r}; roles: {', '.join(ROLES)}. Or search by need without a role")
    shelves = {c[0]: c for c in CATEGORIES}
    if category is not None and category not in shelves:
        raise OpError(f"unknown category {category!r}; categories: {', '.join(shelves)}")
    by_cat = {}
    for name in sorted(OPS):
        by_cat.setdefault(category_of(name), []).append(name)
    if need:
        words = _words(need)
        if not words:
            raise OpError("need has no words to search by; say what you want to do, e.g. 'compare two takes by ear'")
        mine = set(ROLES[role]) if role else set()
        pool = [x for x in (by_cat.get(category, []) if category else OPS) if x != 'ops_find']
        ix, idf, avg = _index()
        ranked = sorted(((_score(words, ix[x], idf, avg) * (1.3 if category_of(x) in mine else 1.0), x) for x in pool),
                        key=lambda t: (-t[0], t[1]))
        hits = [(s, x) for s, x in ranked if s > 0][:n]
        if not hits:
            return (f"nothing matches {need!r}. Try other words, or browse: ops_find(role=...) or "
                    f"ops_find(category=...); categories: {', '.join(shelves)}")
        return '\n'.join([f"ops for: {need}"] + [f"  {x}  [{category_of(x)}]  {_first_line(x)}{_here(x)}"
                                                 for _, x in hits] + _served_note())
    if category:
        c = shelves[category]
        return '\n'.join([f"{category}: {c[1]}"] + [f"  {x}  {_first_line(x)}{_here(x)}"
                                                     for x in by_cat.get(category, [])] + _served_note())
    if role:
        L = [f"role {role}: the shelves to reach for first come first (ops_find(need=...) searches them all)"]
        for cat in ROLES[role]:
            names = by_cat.get(cat, [])
            L.append(f"{cat} ({len(names)}): {shelves[cat][1]}")
            if len(names) > 24:          # the stage and live shelves: names only, search for the rest
                L.append('  ' + ' '.join(x + ('*' if _here(x) else '') for x in names))
            else:
                L += [f"  {x}  {_first_line(x)}{_here(x)}" for x in names]
        if any(_here(x) for c in ROLES[role] for x in by_cat.get(c, [])):
            L.append('(* = not on this server)')
        return '\n'.join(L + _served_note())
    L = ['shelves (ops_find(category=...) lists one; ops_find(need="...") searches every description):']
    L += [f"  {c} ({len(by_cat.get(c, []))}): {d}" for c, d, _ in CATEGORIES]
    L.append('roles (ops_find(role=...) shows the shelves a role reaches for first): ' + ', '.join(ROLES))
    return '\n'.join(L + _served_note())
