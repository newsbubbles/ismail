"""First sketches: a brief in, two or three short pieces on the showcase voices out, so a person hears something
within minutes and picks a direction by ear (the pick becomes the song's example). The brief is read: a named
tempo, key, genre, instruments and form shape the sketch, and whatever has no voice yet is named in the reply
(never played as if it answered). A vague brief gets three contrasting styles. Each sketch is a normal project the
agent can edit: a motif that comes back and answers itself, chords that move, parts in their own registers and
rhythms, never block chords on every beat 1."""
import copy
import json
import os
import random
import re

HERE = os.path.dirname(os.path.abspath(__file__))
SHOWCASE = os.path.join(HERE, 'voices', 'showcase.json')

NAMES = {'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11}
SCALES = {'major': [0, 2, 4, 5, 7, 9, 11], 'minor': [0, 2, 3, 5, 7, 8, 10],
          'major_pent': [0, 2, 4, 7, 9], 'minor_pent': [0, 3, 5, 7, 10]}
ROMAN = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii']
PROGRESSIONS = {'major': [['I', 'V', 'vi', 'IV'], ['I', 'vi', 'IV', 'V'], ['I', 'IV', 'vi', 'V']],
                'minor': [['i', 'VI', 'III', 'VII'], ['i', 'VI', 'VII', 'i'], ['i', 'iv', 'VI', 'V']]}
# a classical or sacred piece moves by function (tonic, predominant, dominant) and its phrases close on cadences;
# the last two bars are V I (authentic) or, for the second reading, IV I (the plagal "amen")
CLASSICAL = {'major': [['I', 'IV', 'V', 'I'], ['I', 'vi', 'ii', 'V'], ['I', 'ii', 'V', 'I']],
             'minor': [['i', 'iv', 'V', 'i'], ['i', 'VI', 'iv', 'V'], ['i', 'iv', 'VII', 'III']]}
SOFT = re.compile(r'\b(gentle|gently|soft\w*|quiet\w*|calm\w*|peaceful|tender\w*|hushed|prayerful|meditative|'
                  r'slow\w*|reverent\w*|contemplative|serene)\b', re.I)
# every loop starts on the tonic and the second and third avoid the relative key's cadence, so a sketch reads in
# the key asked for (i iv VII III read as G major in a cold-start run)
DARK = re.compile(r'\b(sad|dark|melanchol\w*|night\w*|lonely|grief|rain\w*|minor|moody|haunt\w*|tense|cold|loss)\b', re.I)
MOTIF_RHYTHMS = [   # (start beat, length) over two bars of 4/4; none of them only lands on beat 1
    [(0, 1.5), (1.5, 0.5), (2, 1), (3, 1), (4, 3), (7, 1)],
    [(0.5, 0.5), (1, 1), (2, 0.5), (2.5, 1.5), (4.5, 0.5), (5, 1), (6, 2)],
    [(1, 1), (2, 1), (3, 0.5), (3.5, 0.5), (4, 2), (6, 1), (7, 1)],
    [(0.5, 1), (1.5, 0.5), (2, 2), (4.5, 0.5), (5, 0.5), (5.5, 2.5)],          # sparse, laid back
]

# (role, voice) -> register and fader; a role with no entry for its voice uses the role's default
REG = {'melody': (62, 84), 'keys': (55, 72), 'harmony': (36, 60), 'counter': (48, 64), 'pad': (45, 62),
       'bass': (28, 50), 'sub': (26, 40), 'chords': (52, 67)}
REG_VOICE = {('melody', 'strat70_clean'): (57, 79), ('melody', 'emily'): (57, 81), ('bass', 'contrabass'): (31, 48),
             ('melody', 'violin'): (62, 84), ('keys', 'rhodes'): (52, 72), ('pad', 'violin'): (62, 79)}
LEVEL = {('drums', 'kit70'): 7.0, ('bass', 'pbass70'): -9.0, ('bass', 'contrabass'): -6.0, ('sub', 'sub_bass'): -11.0,
         ('keys', 'grand_piano'): -7.0, ('melody', 'grand_piano'): 0.0, ('melody', 'violin'): -1.0,
         ('melody', 'strat70_clean'): 1.0, ('counter', 'cello'): 1.0, ('pad', 'cello'): -3.0,
         ('chords', 'strat70_rhythm'): -3.0, ('harmony', 'grand_piano'): -10.0, ('keys', 'rhodes'): -12.0,
         ('drums', 'rusty'): 5.0, ('melody', 'emily'): 0.0, ('fx', 'crackle'): -6.0}
# a classical or soft sketch: the tune on top, every other part under it (the dress rehearsal's cello read 17 to
# 22 dB over the piano melody, and the chord readout named its notes instead of the chords)
LEVEL_UNDER = {'counter': -9.0, 'pad': -13.0, 'bass': -13.0, 'harmony': -11.0, 'keys': -11.0}
# 'strings' with no other word: a section, one player a part (there is no ensemble strings voice yet)
SECTION = {'pad': 'violin', 'counter': 'cello', 'bass': 'contrabass'}

# the three styles a vague brief gets, as specs
STYLES = {
    'piano': {'what': 'solo piano: a melody over a broken-chord left hand', 'bpm': (72, 88), 'feel': None,
              'parts': {'melody': 'grand_piano', 'harmony': 'grand_piano'}},
    'chamber': {'what': 'string trio and piano: violin melody, cello counterline, contrabass, soft piano',
                'bpm': (68, 84), 'feel': None,
                'parts': {'melody': 'violin', 'counter': 'cello', 'bass': 'contrabass', 'keys': 'grand_piano'}},
    'band': {'what': 'a small band: drums, bass guitar, rhythm guitar, piano melody', 'bpm': (92, 112),
             'feel': 'rock', 'parts': {'drums': 'kit70', 'bass': 'pbass70', 'chords': 'strat70_rhythm',
                                       'melody': 'grand_piano'}},
}
ORDER = ['piano', 'chamber', 'band']

# words in a brief -> (role, showcase voice, what to say when the voice stands in for what was asked)
INSTRUMENTS = [
    (r'rhodes|electric piano|e-piano|epiano', 'keys', 'rhodes', None),
    (r'wurli\w*', 'keys', 'rhodes', 'no Wurlitzer voice yet: the Rhodes plays its part'),
    (r'organ|hammond', 'keys', 'grand_piano', 'no organ voice yet: grand_piano plays its part'),
    (r'(?:clean |bluesy |blues |lead |melodic |jazz )+guitar(?: melody| lead| solo| line)?|guitar (?:melody|lead|solo|line)',
     'melody', 'emily', None),
    (r'(?:rhythm )?guitars?', 'chords', 'strat70_rhythm', None),
    (r'(?:deep |big |heavy )?sub(?:[- ]?bass)?|808s?', 'sub', 'sub_bass', None),
    (r'upright(?: bass)?|double bass|contrabass|acoustic bass', 'bass', 'contrabass', None),
    (r'bass(?: guitar)?|electric bass', 'bass', 'pbass70', None),
    (r'piano|keys', 'keys', 'grand_piano', None),
    (r'violins?|fiddle', 'melody', 'violin', None),
    (r'cellos?', 'counter', 'cello', None),
    (r'strings|string section|string quartet|orchestra\w*', 'section', None,
     'a string section here is violin and cello (and contrabass when no other bass plays), one player each (no ensemble strings voice yet)'),
    (r'(?:synth |ambient |warm )?pads?', 'pad', 'cello', 'no pad voice yet (a known gap): cello holds the long notes'),
    (r'flute|sax\w*|trumpet|horns?|brass|clarinet|oboe|synth lead|lead synth', 'melody', 'violin',
     'no {word} voice yet: violin plays the line'),
    (r'vocals?|singer|singing|voice|rap\w*|choir', None, None,
     'no singing voice in the showcase yet: {word} left out (say it to the person)'),
    (r'vinyl(?: crackle)?|crackle|record noise|surface noise|dusty', 'fx', 'crackle', None),
    (r'breakbeats?|breaks|boom[- ]?bap|live drums|acoustic drums|sampled (?:kit|drums)', 'drums', 'rusty', None),
    (r'drums?|kit|beat|percussion|hats|hi-?hats?|ride', 'drums', 'kit70', None),
]
GENRES = [   # words -> feel, tempo range, the parts a genre brings when the brief names none
    (r'trip[- ]?hop|downtempo', 'break', (84, 94), ['drums', 'bass', 'keys', 'melody'], True),
    (r'hip[- ]?hop|boom[- ]?bap|lo-?fi|chillhop', 'break', (84, 94), ['drums', 'bass', 'keys', 'melody'], True),
    (r'house|techno|disco|dance|edm|garage', 'four', (120, 126), ['drums', 'bass', 'keys', 'melody'], False),
    (r'jazz\w*|swing', 'jazz', (110, 140), ['drums', 'bass', 'keys', 'melody'], True),
    (r'rock|indie|funk\w*|soul|blues|r&b|band|pop', 'rock', (92, 116), ['drums', 'bass', 'chords', 'melody'], False),
    (r'prelude|postlude|hymn\w*|chorale|church|sacred|chapel|psalm|anthem|requiem|liturg\w*|baroque|classical|'
     r'chamber|adagio|largo|andante|nocturne|elegy', 'classical', (56, 72), ['melody', 'harmony', 'counter', 'bass'],
     False),
    (r'ambient|cinematic|film|score|orchestral|string', None, (66, 80),
     ['melody', 'counter', 'bass', 'keys'], False),
    (r'ballad|solo piano|lullaby', None, (68, 84), ['melody', 'harmony'], False),
]
DEFAULT_VOICE = {'drums': 'kit70', 'bass': 'pbass70', 'keys': 'grand_piano', 'melody': 'grand_piano',
                 'chords': 'strat70_rhythm', 'counter': 'cello', 'harmony': 'grand_piano', 'sub': 'sub_bass'}
CLASSICAL_VOICE = {'bass': 'contrabass'}
SECTIONS = {'intro': 'intro', 'verse': 'groove', 'groove': 'groove', 'main': 'groove', 'chorus': 'groove',
            'hook': 'groove', 'drop': 'groove', 'breakdown': 'breakdown', 'break down': 'breakdown',
            'bridge': 'breakdown', 'build': 'build', 'buildup': 'build', 'return': 'groove', 'reprise': 'groove',
            'outro': 'outro', 'fade': 'outro', 'fades': 'outro', 'fade out': 'outro', 'fade-out': 'outro',
            'fades out': 'outro', 'fading out': 'outro', 'ending': 'outro'}
# what a musician would call each showcase voice (first-session spec S-4: no voice IDs, no engine words)
PLAIN = {'grand_piano': 'a grand piano', 'violin': 'a violin', 'cello': 'a cello', 'contrabass': 'a double bass',
         'kit70': 'a drum kit', 'rusty': 'a real drum kit', 'strat70_rhythm': 'a rhythm guitar',
         'strat70_clean': 'an electric guitar', 'emily': 'a clean electric guitar', 'pbass70': 'a bass guitar',
         'sub_bass': 'a deep sub bass', 'growl': 'a growling synth bass', 'rhodes': 'a Rhodes electric piano',
         'crackle': 'record crackle'}
PLAIN_ROLE = {'melody': 'the tune', 'keys': 'the chords', 'harmony': 'the chords', 'chords': 'the chords',
              'counter': 'a second line', 'pad': 'the long notes', 'bass': 'the bass', 'sub': 'the low end',
              'drums': 'the drums', 'fx': 'the texture'}
# brief words read as a wish for a sound that no voice or setting models yet: said, not silently dropped
UNMODELLED = r'gritty|lush|airy|punchy|glitch\w*|distorted|fuzz\w*|swirl\w*|shimmer\w*|wobbl\w*|tape[- ]?saturat\w*|' \
             r'chopped|vocal chops|sidechain\w*|reverse\w*|stutter\w*|bitcrush\w*|lo[- ]?fi tape'


def plain(voice):
    return PLAIN.get(voice, voice.replace('_', ' '))


def plain_parts(parts):
    """{role: voice} -> 'a violin plays the tune over a cello, a double bass and a grand piano'."""
    lead = parts.get('melody')
    rest = [plain(v) for r, v in ((r, parts[r]) for r in ROLE_ORDER if r in parts) if r != 'melody']
    rest = list(dict.fromkeys(rest))                    # the piano's two hands are one piano
    if lead and plain(lead) in rest:
        rest.remove(plain(lead))
    tail = (', '.join(rest[:-1]) + ' and ' + rest[-1]) if len(rest) > 1 else (rest[0] if rest else '')
    if not lead:
        return tail
    return f"{plain(lead)} plays the tune" + (f" over {tail}" if tail else ' alone')


def contrast(a, b):
    """How sketch b differs from sketch a, in plain words: key or mode, tempo, length, lead, density, form."""
    out = []
    if a['key'] != b['key']:
        out.append(f"in {b['key']}" if a['key'].split()[1] == b['key'].split()[1] else f"{b['key'].split()[1]} ({b['key']})")
    if abs(a['bpm'] - b['bpm']) >= 0.06 * a['bpm']:
        out.append(('slower' if b['bpm'] < a['bpm'] else 'faster') + f" ({b['bpm']:g} BPM)")
    if a['bars'] != b['bars']:
        out.append('longer' if b['bars'] > a['bars'] else 'shorter')
    la, lb = a['parts'].get('melody', {}).get('voice'), b['parts'].get('melody', {}).get('voice')
    if la != lb and lb:
        out.append(f"{plain(lb)} on the tune")
    na, nb = (sum(len(p['notes']) for p in x['parts'].values()) for x in (a, b))
    if na and abs(nb - na) >= 0.15 * na:
        out.append('sparser' if nb < na else 'busier')
    if (a.get('form') or []) != (b.get('form') or []):
        out.append('with an intro and an ending' if b.get('form') else 'one groove throughout')
    return out


ROLE_ORDER = ['drums', 'sub', 'bass', 'chords', 'keys', 'harmony', 'pad', 'counter', 'melody', 'fx']


# A sketch is a first impression, so it is mastered and placed in a room (moves:M021 exam r1, Nate 10-06: "is this
# mastered?"; "sounds like it's on a basic midi piano ... not enough effects engineering"). Per feel: the loudness the
# genre is mastered to (references/mastering.md), the room's length, and whether the keys get a little tape warmth.
MASTER = {   # feel -> (target LUFS, room rt60 s, keys warmth)
    'break': (-12.0, 1.2, True), 'four': (-9.5, 0.9, False), 'jazz': (-14.0, 1.4, True), 'rock': (-11.0, 1.0, True),
    'classical': (-17.0, 2.4, False), None: (-16.0, 1.8, False)}
ROOM_SEND = {'keys': -12.0, 'chords': -14.0, 'harmony': -12.0, 'pad': -10.0, 'counter': -12.0, 'melody': -13.0,
             'drums': -24.0}


def production(spec, roles):
    """The mix and master a sketch gets: {lufs, master (fx chain), room (the bus's reverb), sends {role: dB},
    track_fx {role: [fx]}, why}."""
    feel = spec.get('feel')
    lufs, rt60, warm = MASTER.get(feel, MASTER[None])
    if spec.get('soft'):
        lufs -= 2.0
    master = [{'type': 'eq', 'bands': [{'type': 'lowcut', 'freq': 28}]},
              {'type': 'compressor', 'threshold_db': -20.0, 'ratio': 1.6, 'attack_ms': 25.0, 'release_ms': 150.0,
               'knee_db': 6.0},                                          # glue: 1-3 dB on the loud bars
              {'type': 'width', 'width': 1.05, 'mono_below_hz': 130},    # the low end mono (phones, clubs)
              {'type': 'limiter', 'ceiling_db': -1.0, 'gain_db': 0.0}]   # -1 dB: mp3 encoding overshoots
    room = {'type': 'hall', 'rt60': rt60, 'predelay_ms': 18.0, 'mix': 1.0, 'hp_hz': 180.0, 'high_mult': 0.5}
    sends = {r: db for r, db in ROOM_SEND.items() if r in roles}
    track_fx = {}
    if warm:
        for r in ('keys', 'chords', 'harmony'):
            if r in roles:
                track_fx[r] = [{'type': 'distortion', 'mode': 'tanh', 'drive_db': 5.0, 'mix': 0.22}]
    why = (f"mastered to {lufs:g} LUFS for {feel or 'its style'} (glue, mono below 130 Hz, limiter at -1 dB), "
           f"a {rt60:g} s room on " + ', '.join(sends) + (', tape warmth on the keys' if track_fx else ''))
    return {'lufs': lufs, 'master': master, 'room': room, 'sends': sends, 'track_fx': track_fx, 'why': why}


class SketchError(ValueError):
    pass


def showcase():
    with open(SHOWCASE, encoding='utf8') as f:
        return json.load(f)


def showcase_text():
    sc = showcase()
    L = [f"  {v['name']:<15} {v['family']:<8} {v['range']:<12} {v['why']}" for v in sc['voices']]
    L.append(f"  never in a first sketch: {sc['never_first']}")
    L.append(f"  not covered yet: {'; '.join(sc['gaps'])}")
    return '\n'.join(L)


def parse_key(key, brief=''):
    """'A minor', 'Am', 'Eb', 'F# major' -> (tonic pitch class, mode). Empty: from the brief's words (minor for
    dark words, else major), tonic A for minor and C for major."""
    if not key:
        return (9, 'minor') if DARK.search(brief or '') else (0, 'major')
    m = re.fullmatch(r'\s*([A-Ga-g])([#b]?)\s*(m|min|minor|maj|major)?\s*', key)
    if not m:
        raise SketchError(f"key {key!r}: write it like 'A minor', 'Am', 'Eb major' or 'F#'")
    pc = (NAMES[m.group(1).upper()] + {'#': 1, 'b': -1, '': 0}[m.group(2)]) % 12
    return pc, ('minor' if m.group(3) in ('m', 'min', 'minor') else 'major')


def chord(sym, tonic, mode):
    """'vi', 'V', 'bVII' or a chord name ('Am', 'F', 'C#m') -> (root pitch class, [pitch classes of the triad])."""
    m = re.fullmatch(r'([A-G])([#b]?)(m?)', sym)
    if m:
        root = (NAMES[m.group(1)] + {'#': 1, 'b': -1, '': 0}[m.group(2)]) % 12
        return root, [root, (root + (3 if m.group(3) else 4)) % 12, (root + 7) % 12]
    m = re.fullmatch(r'([b#]?)([ivIV]+)', sym)
    if not m or m.group(2).lower() not in ROMAN:
        raise SketchError(f"chord {sym!r}: a roman numeral (I, vi, bVII) or a chord name (Am, F, C#m)")
    deg = ROMAN.index(m.group(2).lower())
    sc = SCALES[mode]
    root = (tonic + sc[deg] + {'b': -1, '#': 1, '': 0}[m.group(1)]) % 12
    if m.group(1):
        third = 3 if m.group(2).islower() else 4
    else:
        third = (sc[(deg + 2) % 7] - sc[deg]) % 12
        if mode == 'minor' and m.group(2) == 'V':
            third = 4                                        # the dominant of a minor key is major
    return root, [root, (root + third) % 12, (root + 7) % 12]


def seventh(ch):
    """A triad with its seventh: m7 on a minor chord, maj7 on a major one, 7 on a major chord a fourth below the
    next (left to the caller); here minor -> b7, major -> maj7."""
    root, pcs = ch
    minor = (pcs[1] - root) % 12 == 3
    return root, pcs + [(root + (10 if minor else 11)) % 12]


def near(pc, target, lo, hi):
    """The pitch of class pc nearest to target inside [lo, hi]."""
    best = None
    for p in range(lo, hi + 1):
        if p % 12 == pc and (best is None or abs(p - target) < abs(best - target)):
            best = p
    return best if best is not None else target


def scale_pitches(tonic, mode, lo, hi):
    return [p for p in range(lo, hi + 1) if (p - tonic) % 12 in SCALES[mode]]


# ------------------------------------------------------------------ reading a brief

def _find(pattern, text):
    return re.search(r'(?<!\w)(?:' + pattern + r')(?!\w)', text, re.I)


def read_brief(brief):
    """What the brief names -> a spec: bpm, key, feel, parts {role: voice}, form, density, blues, and `said`: one
    line per thing asked for that a voice only stands in for, or that is left out."""
    text = ' ' + (brief or '') + ' '
    spec = {'bpm': None, 'key': None, 'feel': None, 'bpm_range': None, 'parts': {}, 'form': None, 'dense': 0,
            'blues': False, 'sevenths': False, 'ride': False, 'crisp': False, 'said': [], 'named': False,
            'removed': [], 'soft': False, 'genre_word': None, 'subs': [], 'unmodelled': []}
    m = re.search(r'(\d{2,3})\s*bpm', text, re.I)
    if m:
        spec['bpm'] = float(m.group(1))
    m = re.search(r'(?<![\w#])([A-G])([#b]?)\s*(minor|major|min|maj|m)(?![a-z])', text)
    if m:
        spec['key'] = m.group(1) + m.group(2) + ' ' + ('minor' if m.group(3) in ('m', 'min', 'minor') else 'major')
    for pat, feel, rng, parts, sev in GENRES:
        if _find(pat, text):
            spec['genre_word'] = _find(pat, text).group(0).strip().lower()
            spec['feel'], spec['bpm_range'], spec['sevenths'] = feel, rng, sev
            spec['genre_parts'] = parts
            spec['named'] = True
            break
    for m in re.finditer(r'\b(?:no|without|drop|lose)\s+(?:the\s+)?([a-z][a-z -]{1,20}?)(?=[,.;]|\s+(?:and|but|or)\b|$)',
                         text, re.I):
        spec['removed'].append(m.group(1).strip().lower())
    rest = text
    for pat, role, voice, note in INSTRUMENTS:
        for m in list(re.finditer(r'(?<!\w)(?:' + pat + r')(?!\w)', rest, re.I)):
            word = m.group(0).strip()
            if any(word.lower() in r or r in word.lower() for r in spec['removed']):
                continue
            spec['named'] = True
            line = f"asked for {word}: " + note.format(word=word) if note else None
            if line and not any(x.lower().split(':')[1:] == line.lower().split(':')[1:] for x in spec['said']):
                spec['said'].append(line)
                spec['subs'].append({'asked': word, 'role': role, 'plays': voice})
            if role == 'section':
                for r, v in SECTION.items():               # in a band, its own bass stays the bass
                    if r != 'bass' or spec['feel'] in (None, 'classical'):
                        spec['parts'].setdefault(r, v)
            elif role and role not in spec['parts']:
                spec['parts'][role] = voice
            rest = rest[:m.start()] + ' ' * (m.end() - m.start()) + rest[m.end():]   # each word counts once
    spec['unmodelled'] = list(dict.fromkeys(m.group(0).lower() for m in re.finditer(
        r'(?<!\w)(?:' + UNMODELLED + r')(?!\w)', text, re.I)))
    spec['ride'] = bool(_find(r'ride', text))
    spec['crisp'] = bool(_find(r'crisp|bright|tight', text))
    spec['blues'] = bool(_find(r'blues\w*|bluesy', text))
    spec['soft'] = bool(SOFT.search(text))
    if _find(r'sparse|minimal|sparser|simple|less|quiet', text):
        spec['dense'] = -1
    if _find(r'busy|busier|dense|more energy|driving', text):
        spec['dense'] = 1
    form = []
    for m in re.finditer(r'(?<!\w)(' + '|'.join(sorted(map(re.escape, SECTIONS), key=len, reverse=True)) +
                         r')(?!\w)', text, re.I):
        form.append(SECTIONS[m.group(1).lower()])
    if len(form) >= 2:
        spec['form'] = form[:6]
    if spec['feel'] == 'classical' and spec['parts'].get('keys') == 'grand_piano' and 'harmony' not in spec['parts']:
        spec['parts']['harmony'] = spec['parts'].pop('keys')   # a classical piano plays broken chords, not comping
    if spec['named'] and spec['feel'] and 'genre_parts' in spec:
        filled = []
        for role in spec['genre_parts']:
            if role == 'bass' and 'sub' in spec['parts']:
                continue
            if role not in spec['parts']:
                spec['parts'][role] = CLASSICAL_VOICE.get(role, DEFAULT_VOICE[role]) if spec['feel'] == 'classical' \
                    else DEFAULT_VOICE[role]
                filled.append(role)
        if spec['feel'] == 'classical' and filled:
            spec['said'].append(f"filled in for {spec['genre_word']}: " + ', '.join(
                f"{r} ({spec['parts'][r]})" for r in ROLE_ORDER if r in filled))
    _stand_ins(spec)
    spec['asked'] = sorted(spec['parts'])                    # what the words named, before any default fills in
    if spec['parts'] and 'melody' not in spec['parts']:
        spec['parts']['melody'] = 'grand_piano'               # every sketch carries a tune
        if spec['named'] and len(spec['parts']) > 2 and 'grand_piano' not in [spec['parts'][r] for r in spec['asked']]:
            spec['said'].append("no instrument was named for the melody: grand_piano plays it")
            spec['subs'].append({'asked': None, 'role': 'melody', 'plays': 'grand_piano'})
    if spec['parts'] and 'drums' not in spec['parts'] and spec['feel'] is None and spec['ride']:
        spec['parts']['drums'] = 'kit70'
    if 'drums' in spec['parts'] and not spec['feel']:
        spec['feel'] = 'rock'
    for r in spec['removed']:
        for role in _roles_named(r, spec['parts']):
            spec['parts'].pop(role)
    spec['genre'] = bool(spec.pop('genre_parts', None))
    return spec


FAMILY_WORDS = {'drums': r'drums?|beat|percussion|kit|breakbeats?|hats|ride', 'guitar': r'guitars?',
                'bass': r'bass\w*|sub\w*|808s?', 'keys': r'piano|keys|rhodes|organ', 'strings': r'strings?|violin|cello'}


def _roles_named(word, parts):
    """The roles a removed word means ('guitar' -> every guitar part), by the showcase voice's family."""
    fam = {v['name']: v['family'] for v in showcase()['voices']}
    out = [r for r in parts if r == word]
    for family, pat in FAMILY_WORDS.items():
        if re.fullmatch(pat, word.split()[-1], re.I):
            out += [r for r, v in parts.items() if fam.get(v) == family or (family == 'drums' and r == 'drums')]
    return list(dict.fromkeys(out))


def ready(voice):
    """A showcase voice can play here: it needs no samples, or its sample set is on this machine."""
    v = next((x for x in showcase()['voices'] if x['name'] == voice), None)
    if not v or not v.get('needs'):
        return True
    from . import samples
    return samples.path(v['needs']) is not None


def _stand_ins(spec):
    """Swap a sampled voice whose samples are not here for its stand-in, and say how to get the real one."""
    from . import samples
    sc = {v['name']: v for v in showcase()['voices']}
    for role, voice in list(spec['parts'].items()):
        v = sc.get(voice, {})
        if v.get('needs') and not ready(voice):
            st = samples.SETS[v['needs']]
            spec['parts'][role] = v['standin']
            line = (f"the {voice} voice plays real samples not on this machine yet ({st['what']}): ask the person, "
                    f"then samples_fetch('{v['needs']}') (~{st['size_mb']} MB, {st['licence']}); {v['standin']} "
                    f"plays it for now")
            if line not in spec['said']:
                spec['said'].append(line)
                spec.setdefault('subs', []).append({'asked': voice, 'role': role, 'plays': v['standin'],
                                                    'download_mb': st['size_mb']})


def say_plain(spec):
    """The SAY TO THE PERSON lines for what was swapped or not honoured, in a musician's words (spec S-4)."""
    out = []
    for s in spec.get('subs') or []:
        who = plain(s['plays']) if s.get('plays') else None
        if s.get('download_mb'):
            out.append(f"the real {plain(s['asked']).split(' ', 1)[-1]} needs a download of about {s['download_mb']} "
                       f"MB first; {who} plays its part until you say yes to it")
        elif s.get('asked') is None:
            out.append(f"you didn't name an instrument for the tune, so {who} plays it")
        elif s['role'] == 'section':
            out.append(f"for '{s['asked']}' you hear one violin, one cello and a double bass, one player each; a "
                       f"full string section isn't here yet")
        elif who is None:
            out.append(f"you asked for {s['asked']}; there's no singing voice yet, so it's left out")
        else:
            what = PLAIN_ROLE.get(s['role'], 'its part')
            out.append(f"you asked for {s['asked']}; that isn't here yet, so {who} plays {what}")
    for w in spec.get('unmodelled') or []:
        out.append(f"'{w}' isn't something I can make yet, so these don't try")
    return out


# A non-musician's change is a feeling word (ledger:M170 S-6: "a bit happier" got "the words named nothing to change"
# and two slower takes). Each moves the concrete things in its direction: (words, tempo factor, mode, density step,
# soft, crisp, the name said back). "a bit" halves the move, "much" makes it half again as big.
FEELINGS = [
    (r'happier|happy|cheerful|joyful|joyous|uplifting|sunnier|more fun', 1.06, 'major', 1, False, True, 'happier'),
    (r'sadder|sad|melanchol\w*|gloomier|more mournful|more wistful', 0.9, 'minor', -1, True, False, 'sadder'),
    (r'calmer|calm|gentler|more relaxed|relaxing|more peaceful|peaceful|quieter|more soothing', 0.9, None, -1, True,
     False, 'calmer'),
    (r'more exciting|exciting|energetic|more energetic|livelier|more lively|upbeat|more intense|more epic', 1.12, None,
     1, False, True, 'more exciting'),
    (r'darker|moodier|ominous|spookier|scarier|more mysterious|mysterious', 0.96, 'minor', 0, True, False, 'darker'),
    (r'dreamier|dreamy|floatier|floaty|spacier|spacey|hazier', 0.92, None, -1, True, False, 'dreamier'),
]
_LITTLE = r'a (?:little|bit|touch|tad)(?: bit)?|slightly|somewhat|a little more|just a bit'
_MUCH = r'much|a lot|way|far|very|really|lots'


def feelings(words):
    """The feeling words in a change request -> [(name, tempo factor, mode, density step, soft, crisp)], scaled by
    "a bit" (half) and "much" (one and a half)."""
    scale = 0.5 if _find(_LITTLE, words) else 1.5 if _find(_MUCH, words) else 1.0
    out = []
    for pat, tempo, mode, dense, soft, crisp, name in FEELINGS:
        if _find(pat, words):
            out.append((name, 1 + (tempo - 1) * scale, mode, dense, soft, crisp))
    return out


def apply_words(base, words):
    """A sketch's spec changed by the person's next words ("more like a Rhodes, slower drums, no guitar"): what the
    words name replaces, the rest stays. -> (new spec, [what changed])."""
    spec = copy.deepcopy(base)
    new = read_brief(words)
    changed = []
    if new['bpm']:
        spec['bpm'] = new['bpm']
        changed.append(f"tempo {new['bpm']:g}")
    elif _find(r'slower|slow(?:er)? down|half[- ]time|chill(?:er)?', words):
        spec['bpm'] = round((spec.get('bpm') or 90) * 0.88)
        changed.append(f"slower ({spec['bpm']:g} BPM)")
    elif _find(r'faster|speed up|more energy|uptempo', words):
        spec['bpm'] = round((spec.get('bpm') or 90) * 1.12)
        changed.append(f"faster ({spec['bpm']:g} BPM)")
    if new['key']:
        spec['key'] = new['key']
        changed.append(f"key {new['key']}")
    elif _find(r'darker|sadder|minor', words) and spec.get('key') and 'major' in spec['key']:
        spec['key'] = spec['key'].split()[0] + ' minor'
        changed.append('minor')
    elif _find(r'brighter|happier|major', words) and spec.get('key') and 'minor' in spec['key']:
        spec['key'] = spec['key'].split()[0] + ' major'
        changed.append('major')
    if new['genre'] and new['feel'] != spec.get('feel'):
        spec['feel'] = new['feel']
        changed.append(f"feel {new['feel']}")
    for role in new['asked']:
        voice = new['parts'].get(role)
        if voice and spec['parts'].get(role) != voice:
            spec['parts'][role] = voice
            changed.append(f"{role}: {voice}")
    for r in new['removed']:
        for role in _roles_named(r, spec['parts']):
            spec['parts'].pop(role)
            changed.append(f"no {role}")
    if 'melody' not in spec['parts'] and spec['parts']:
        spec['parts']['melody'] = 'grand_piano'
        changed.append('melody: grand_piano')
    if new['form']:
        spec['form'] = new['form']
        changed.append('form ' + ' '.join(new['form']))
    if new['dense']:
        spec['dense'] = new['dense']
        changed.append('sparser' if new['dense'] < 0 else 'busier')
    for k in ('ride', 'crisp', 'blues', 'soft'):
        if new[k] and not spec.get(k):
            spec[k] = True
            changed.append(k)
    tempo_named = bool(new['bpm']) or any(c.startswith(('slower', 'faster')) for c in changed)
    mode_named = bool(new['key']) or any(c in ('minor', 'major') for c in changed)
    for name, tempo, mode, dense, soft, crisp in feelings(words):
        did = []
        if not tempo_named and abs(tempo - 1) > 0.005:
            lo, hi = spec.get('bpm_range') or (60, 140)
            was = spec.get('bpm') or round((lo + hi) / 2)
            spec['bpm'] = round(was * tempo)
            did.append(f"{'faster' if tempo > 1 else 'slower'} ({spec['bpm']:g} BPM)")
            tempo_named = True
        if mode and not mode_named and spec.get('key') and mode not in spec['key']:
            spec['key'] = spec['key'].split()[0] + ' ' + mode
            did.append(mode)
            mode_named = True
        if dense and not new['dense'] and spec.get('dense', 0) != max(-1, min(1, spec.get('dense', 0) + dense)):
            spec['dense'] = max(-1, min(1, spec.get('dense', 0) + dense))
            did.append('busier' if dense > 0 else 'sparser')
        if soft and not spec.get('soft'):
            spec['soft'], spec['crisp'] = True, False
            did.append('softer')
        if crisp and not spec.get('crisp'):
            spec['crisp'], spec['soft'] = True, False
            did.append('brighter')
        if did:
            changed.append(f"{name}: " + ', '.join(did))
    spec['said'] = new['said']
    spec['subs'], spec['unmodelled'] = new.get('subs', []), new.get('unmodelled', [])
    return spec, changed


def style_spec(style):
    st = STYLES[style]
    return {'bpm': None, 'key': None, 'feel': st['feel'], 'bpm_range': st['bpm'], 'parts': dict(st['parts']),
            'form': None, 'dense': 0, 'blues': False, 'sevenths': False, 'ride': False, 'crisp': False, 'said': [],
            'soft': False, 'what': st['what'], 'style': style}


# ------------------------------------------------------------------ parts

def melody(chords, tonic, mode, reg, rng, rhythm, alt):
    """A 2-bar motif and its answer make a 4-bar phrase. Each later phrase keeps the motif's steps (that is what
    makes it a tune) but moves: the second lifts and answers in another rhythm, the third lifts further, the last
    falls home. Strong-beat and long notes land on chord tones; the last bar holds the tonic."""
    lo, hi = reg
    sp = scale_pitches(tonic, mode, lo, hi)
    n_steps = max(len(rhythm), len(alt))
    steps = [rng.choice([1, 1, 2, -1]) if i < n_steps // 2 else rng.choice([-1, -1, -2, 1]) for i in range(n_steps)]
    out = []
    home = lo + int((hi - lo) * 0.35)                        # phrases start near here, so a tune never drifts to an edge
    last = home
    end = len(chords) - 1
    for c in range((len(chords) + 1) // 2):
        b0, ph = c * 2, c // 2
        rh = alt if ph % 2 == 1 and c % 2 == 1 else rhythm
        lift = [0, 2, 3, 1][ph % 4] if b0 + 2 <= end - 3 or ph == 0 else 0
        start = near(chords[b0][1][(c % 2) * 2], home if c % 2 == 0 else last, lo, hi)   # motif: the root; answer: the fifth
        idx = min(range(len(sp)), key=lambda i: abs(sp[i] - start)) + lift
        for n, (beat, dur) in enumerate(rh):
            bar = b0 + int(beat // 4)
            if bar >= end:
                break
            if n:
                idx += steps[n - 1] * (-1 if c % 2 and n > len(rh) // 2 else 1)
            idx = max(0, min(len(sp) - 1, idx))
            p = sp[idx]
            if (beat % 1 == 0 and beat % 2 == 0) or dur >= 2:
                pcs = chords[bar][1]
                q = min(pcs, key=lambda q: abs(near(q, p, lo, hi) - p))
                p = near(q, p, lo, hi)
                idx = min(range(len(sp)), key=lambda i: abs(sp[i] - p))
            vel = 76 + (8 if beat % 4 == 0 else 0) - (10 if beat % 1 else 0) + 3 * min(ph, 2) + rng.randint(-4, 4)
            out.append((bar, beat % 4, p, dur, vel))
            last = p
    out.append((end, 0, near(tonic, home, lo, hi), 4, 80))
    return out


def broken_chord(chords, reg, rng, pattern='8ths'):
    """Left-hand broken chords held into the bar (a pedal): root, fifth, octave, tenth."""
    lo, hi = reg
    out = []
    for bar, (root, pcs) in enumerate(chords):
        r = near(root, lo + 7, lo, hi)
        seq = [r, r + 7, r + 12, near(pcs[1], r + 16, lo, hi + 12)]
        beats = [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5] if pattern == '8ths' else [0, 1, 2, 3]
        for i, b in enumerate(beats):
            p = seq[[0, 1, 2, 3, 2, 1, 2, 3][i % 8]] if pattern == '8ths' else seq[[0, 1, 3, 2][i % 4]] + 12
            out.append((bar, b, p, 4 - b, 52 + (6 if b == 0 else 0) + rng.randint(-3, 3)))
    return out


def comp(chords, reg, rng, feel, dense, sevenths):
    """Chords voiced close and led (each voicing near the last), in the feel's rhythm: laid back and pushed for a
    break, offbeat for four on the floor, two per bar otherwise."""
    lo, hi = reg
    rhythms = {'break': [[(0, 1.5), (1.5, 1), (2.75, 1.25)], [(0, 2.5), (2.75, 1.25)], [(0, 1), (1.5, 0.5), (2, 1.5), (3.5, 0.5)]],
               'four': [[(0.5, 0.5), (1.5, 0.5), (2.5, 0.5), (3.5, 0.5)], [(0.5, 1), (2.5, 1)], [(0.5, 0.5), (1.5, 0.5), (2.5, 0.5), (3, 0.5), (3.5, 0.5)]],
               'jazz': [[(0, 1.5), (2.5, 1.5)], [(1.5, 2.5)], [(0, 0.5), (1.5, 1), (3, 1)]]}
    pat = rhythms.get(feel, [[(0, 2), (2, 2)], [(0, 4)], [(0, 1), (1.5, 1), (3, 1)]])[{0: 0, -1: 1, 1: 2}[dense]]
    out, last = [], None
    for bar, ch in enumerate(chords):
        root, pcs = seventh(ch) if sevenths else ch
        centre = (lo + hi) // 2 if last is None else sum(last) // len(last)
        v = sorted(near(pc, centre, lo, hi) for pc in pcs[1:] + pcs[:1])
        last = v
        for i, (b, d) in enumerate(pat):
            out.append((bar, b, v, d, 60 + (6 if i == 0 else 0) + rng.randint(-4, 4)))
    return out


def counterline(chords, reg, rng):
    """Half notes on chord tones, each the nearest to the one before (voice leading), moving on beat 3."""
    lo, hi = reg
    out, last = [], (lo + hi) // 2
    for bar, (root, pcs) in enumerate(chords):
        for b, pc in ((0, pcs[1]), (2, pcs[2] if rng.random() < 0.5 else pcs[0])):
            p = near(pc, last, lo, hi)
            out.append((bar, b, p, 2, 64 + rng.randint(-4, 4)))
            last = p
    return out


def pad(chords, reg, rng):
    """Two held voices a bar long: the third and the fifth, led."""
    lo, hi = reg
    out, last = [], (lo + hi) // 2
    for bar, (root, pcs) in enumerate(chords):
        a = near(pcs[1], last, lo, hi)
        out.append((bar, 0, [a, near(pcs[2], a + 4, lo, hi + 5)], 4, 58 + rng.randint(-3, 3)))
        last = a
    return out


def bassline(chords, reg, rng, feel):
    lo, hi = reg
    out = []
    for bar, (root, pcs) in enumerate(chords):
        r = near(root, lo + 5, lo, hi)
        nxt = chords[(bar + 1) % len(chords)][0]
        approach = near(nxt, r, lo, hi)
        approach += -1 if approach > r else 1                 # a half step into the next root
        fifth = near(pcs[2], r, lo, hi)
        if feel == 'rock':
            out += [(bar, 0, r, 1.5, 92), (bar, 1.5, r, 0.5, 70), (bar, 2, fifth, 1.5, 84), (bar, 3.5, approach, 0.5, 74)]
        elif feel == 'break':
            out += [(bar, 0, r, 1.5, 94), (bar, 1.75, r, 0.25, 72), (bar, 2.5, fifth if bar % 2 else r, 1, 84),
                    (bar, 3.5, approach, 0.5, 76)]
        elif feel == 'four':
            out += [(bar, b, r if b < 3 else fifth, 0.45, 88) for b in (0.5, 1.5, 2.5, 3.5)]
        elif feel == 'jazz':
            walk = [r, near(pcs[1], r + 3, lo, hi), fifth, approach]
            out += [(bar, b, walk[b], 1, 82 + rng.randint(-4, 4)) for b in range(4)]
        else:
            out.append((bar, 0, r, 4, 70 + rng.randint(-3, 3)))
    return out


def subline(chords, reg, rng, feel):
    lo, hi = reg
    out = []
    for bar, (root, pcs) in enumerate(chords):
        r = near(root, lo + 5, lo, hi)
        if feel == 'break':
            out += [(bar, 0, r, 2.25, 100), (bar, 2.5, r, 1.25, 92)]
        elif feel == 'four':
            out += [(bar, b, r, 0.45, 96) for b in (0.5, 1.5, 2.5, 3.5)]
        else:
            out.append((bar, 0, r, 4, 96))
    return out


def stabs(chords, reg, rng):
    lo, hi = reg
    out = []
    for bar, (root, pcs) in enumerate(chords):
        v = sorted(near(pc, (lo + hi) // 2, lo, hi) for pc in pcs)
        for b in (1.5, 3.5) if bar % 4 != 3 else (1.5, 2.5, 3.5):
            out.append((bar, b, v, 0.5, 82 + rng.randint(-5, 5)))
    return out


def drum_bar(feel, bar, sec, rng, spec, fill, crash):
    """One bar of kit70 for a feel and a section: intro is cymbals only, a breakdown is the ride and a rim, an
    outro thins out; a fill leads into the next section."""
    ride, dense = spec.get('ride'), spec.get('dense', 0)
    out = []
    cym = 51 if ride and sec == 'breakdown' else 42
    kick = sec not in ('intro', 'breakdown')
    if ride and sec in ('groove', 'outro') and bar % 8 >= 4:     # the ride rides over the hats in the second phrase
        out += [(b, 51, 1, 70 + rng.randint(-4, 4)) for b in range(4)]
    if feel == 'four':
        if kick:
            out += [(b, 36, 0.5, 104) for b in range(4)] + [(b, 38, 0.5, 80) for b in (1, 3)]
        out += [(b + 0.5, 46, 0.5, 70) for b in range(4)]
        if dense >= 0:
            out += [(i / 4, 42, 0.25, 44 + 10 * (i % 2 == 0)) for i in range(16) if i % 4 != 2]
    elif feel == 'jazz':
        out += [(b, 51, 1, 76 if b % 2 else 68) for b in range(4)] + [(b + 2 / 3, 51, 1 / 3, 60) for b in (1, 3)]
        out += [(b, 44, 0.5, 60) for b in (1, 3)]
        if kick:
            out += [(0, 36, 0.5, 70)] + [(rng.choice([1.66, 2.66, 3.66]), 38, 0.3, 52)]
    else:
        if feel == 'break':
            if kick:
                out += [(0, 36, 0.5, 104), (1.75, 36, 0.25, 84), (2.5, 36, 0.5, 96)]
                if dense > 0 or bar % 2:
                    out.append((3.25, 36, 0.25, 70))
            if sec not in ('intro', 'breakdown'):
                out += [(1, 38, 0.5, 100), (3, 38, 0.5, 102)]
                out += [(2.75, 37, 0.25, 42), (3.75, 37, 0.25, 38)] if dense >= 0 else []
        else:
            if kick:
                out += [(0, 36, 0.5, 100), (2.5, 36, 0.5, 86)] + ([(1.75, 36, 0.25, 72)] if bar % 2 else [])
            if sec not in ('intro', 'breakdown'):
                out += [(1, 38, 0.5, 96), (3, 38, 0.5, 98)]
        steps = 16 if (spec.get('crisp') and dense >= 0) or dense > 0 else 8
        for i in range(steps):
            t = i * 4 / steps
            if fill and t >= 2:
                break
            if cym == 51 and i % (steps // 4) and i % (steps // 4) != steps // 8:
                continue                                       # the ride plays quarters and the skip
            v = (72 if i % (steps // 4) == 0 else 52 if i % 2 == 0 else 40) + rng.randint(-4, 4)
            if dense < 0 and i % 2:
                continue
            out.append((t, cym, 4 / steps, v + (6 if cym == 51 else 0)))
    if fill:
        out += [(2 + i * 0.25, t, 0.25, 78 + i * 2) for i, t in enumerate((48, 48, 45, 45, 41, 41, 41, 38))]
    if crash:
        out.append((0, 49, 2, 92))
    return [(bar, b, p, d, v) for b, p, d, v in out]


def note_text(notes, bar):
    """The notes of one bar as notes_write text."""
    L = []
    for b, beat, p, dur, vel in notes:
        if b != bar:
            continue
        pitch = ','.join(str(x) for x in p) if isinstance(p, list) else str(p)
        L.append(f"{beat:g} {pitch} {dur:g} {max(1, min(127, int(vel)))}")
    return '; '.join(L)


# ------------------------------------------------------------------ a whole sketch

ACTIVE = {   # section -> roles that play in it (drums play their own section pattern)
    'intro': {'drums', 'keys', 'harmony', 'pad', 'chords', 'counter'},
    'groove': set(ROLE_ORDER),
    'breakdown': {'drums', 'keys', 'harmony', 'pad', 'counter'},
    'build': set(ROLE_ORDER) - {'melody'},
    'outro': set(ROLE_ORDER),
}


def plan_spec(spec, brief='', seed=0, variant=0, bars=None, progression=None, label=None):
    """-> a dict: what it is, key, bpm, bars, form, chords, every part's notes as (bar, beat, pitch, dur, vel)."""
    rng = random.Random(f"{seed}:{variant}:{brief}:{sorted(spec['parts'].items())}")
    tonic, mode = parse_key(spec.get('key'), brief)
    if not spec.get('key') and spec.get('key_shift'):        # a reading in another key, the mode the words chose
        tonic = (tonic + spec['key_shift']) % 12
    lo_b, hi_b = spec.get('bpm_range') or (80, 100)
    soft, classical = spec.get('soft'), spec.get('feel') == 'classical'
    if soft:                                                 # gentle, quiet, slow: never a fast reading
        lo_b, hi_b = min(lo_b, 60), min(hi_b, 76)
    # the readings sit in order inside the range: sparser the slowest, busier the fastest (a cold run had the
    # sparser reading fastest)
    at = {'as asked': 0.5, 'sparser': 0.15, 'busier': 0.85}.get((label or '').split(',')[0])
    bpm = spec.get('bpm') or (round(lo_b + at * (hi_b - lo_b)) if at is not None else rng.randint(lo_b, hi_b))
    form = spec.get('form') or (['groove', 'groove', 'outro'] if classical else None)
    if form:
        bars = bars or 4 * len(form)
    elif not bars:
        bars = max(8, min(16, int(round(30 * bpm / 240 / 4)) * 4))   # about 30 seconds, whole 4-bar phrases
    if form:
        per = max(1, bars // len(form))
        secs = [form[min(len(form) - 1, b // per)] for b in range(bars)]
    else:
        secs = ['groove'] * bars
    table = CLASSICAL if classical else PROGRESSIONS
    prog = progression or table[mode][variant % len(table[mode])]
    if isinstance(prog, str):
        prog = [x for x in re.split(r'[\s,|-]+', prog) if x]
    loop = [chord(s, tonic, mode) for s in prog]
    chords = [loop[i % len(loop)] for i in range(bars - 1)] + [chord('i' if mode == 'minor' else 'I', tonic, mode)]
    cadence = None
    if classical and not progression and bars >= 2:          # close on a cadence: V I, or IV I (plagal) for reading b
        plagal = variant % 3 == 1
        chords[-2] = chord('iv' if mode == 'minor' else 'IV', tonic, mode) if plagal else chord('V', tonic, mode)
        cadence = ('iv i' if mode == 'minor' else 'IV I') if plagal else ('V i' if mode == 'minor' else 'V I')
    mmode = ('minor_pent' if mode == 'minor' else 'major_pent') if spec.get('blues') else mode
    k = (variant + len(spec['parts'])) % 3 if spec.get('dense', 0) >= 0 else 3
    rhythm, alt = MOTIF_RHYTHMS[k], MOTIF_RHYTHMS[(k + 1) % 3]
    feel, dense = spec.get('feel'), spec.get('dense', 0)
    parts = {}
    for role in [r for r in ROLE_ORDER if r in spec['parts']]:
        voice = spec['parts'][role]
        reg = REG_VOICE.get((role, voice), REG.get(role))
        if role == 'melody':
            if voice == 'grand_piano' and len(spec['parts']) > 2:
                reg = (64, 86)
            notes = melody(chords, tonic, mmode, reg, rng, rhythm, alt)
            if dense < 0:                                    # sparser: the tune's long notes, the passing ones out
                notes = [n for n in notes if n[3] >= 1 or n[1] % 1 == 0 and n[1] % 2 == 0]
        elif role == 'harmony':
            notes = broken_chord(chords, reg, rng, '8ths' if len(spec['parts']) <= 2 else 'quarters')
        elif role == 'keys':
            notes = comp(chords, reg, rng, feel, dense, spec.get('sevenths'))
        elif role == 'counter':
            notes = counterline(chords, reg, rng)
        elif role == 'pad':
            notes = pad(chords, reg, rng)
        elif role == 'bass':
            notes = bassline(chords, reg, rng, feel)
        elif role == 'sub':
            notes = subline(chords, reg, rng, feel)
        elif role == 'chords':
            notes = stabs(chords, reg, rng)
        elif role == 'fx':
            notes = [(0, 0, 60, bars * 4, 72)]
        else:
            notes = []
            for b in range(bars):
                nxt = secs[b + 1] if b + 1 < bars else None
                fill = (nxt is not None and nxt != secs[b] and nxt in ('groove', 'outro')
                        and secs[b] not in ('breakdown', 'intro'))   # a breakdown is the ride alone; the crash marks the return
                crash = secs[b] in ('groove', 'build', 'outro') and (b == 0 or secs[b - 1] != secs[b])
                notes += drum_bar(feel or 'rock', b, secs[b], rng, spec, fill or (not form and b == bars - 2), crash)
            if not form:
                notes.append((bars - 1, 0, 49, 4, 96))
        keep = []
        for n in notes:
            b = n[0]
            if role not in ('drums', 'fx') and role not in ACTIVE[secs[b]]:
                continue
            if secs[b] == 'outro':                           # the outro fades: velocity falls across it
                first = secs.index('outro')
                n = n[:4] + (int(n[4] * (1 - 0.45 * (b - first) / max(1, bars - first))),)
            keep.append(n)
        level = LEVEL.get((role, voice), -4.0)
        if (classical or soft) and 'melody' in spec['parts'] and role in LEVEL_UNDER:
            level = min(level, LEVEL_UNDER[role])
        if soft and role not in ('drums', 'fx'):              # soft dynamics: everything played lighter
            keep = [n[:4] + (max(24, int(n[4] * 0.8)),) for n in keep]
        parts[role] = {'voice': voice, 'level': level, 'notes': keep}
    names = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B']
    what = spec.get('what') or (label or 'as asked') + ': ' + ', '.join(
        f"{r} ({v})" for r, v in ((r, spec['parts'][r]) for r in ROLE_ORDER if r in spec['parts']))
    return {'what': what, 'key': f"{names[tonic]} {mode}", 'bpm': bpm, 'bars': bars, 'progression': prog,
            'form': [s for i, s in enumerate(secs) if i == 0 or secs[i - 1] != s] if form else None,
            'feel': feel, 'soft': bool(soft), 'cadence': cadence, 'parts': parts}


def plan(brief, style, key=None, bpm=None, bars=None, progression=None, seed=0, variant=0):
    """One of the three styles a vague brief gets (piano, chamber, band), with the brief's tempo, key and form."""
    if style not in STYLES:
        raise SketchError(f"style {style!r}: one of {', '.join(STYLES)}")
    spec = style_spec(style)
    got = read_brief(brief)
    spec.update({k: got[k] for k in ('form', 'blues') if got[k]})
    spec['key'] = key or got['key']
    spec['bpm'] = bpm or got['bpm']
    return plan_spec(spec, brief, seed, variant, bars, progression)


VARIANTS = [('as asked', 0), ('sparser, other chords', -1), ('busier, other chords', 1)]


def specs_for(brief, key=None, bpm=None, n=3, base=None):
    """-> [(label, spec)], and the lines to say: a brief that names a genre or instruments gets n readings of it
    (as asked, sparser, busier); a vague one gets the three styles; base= a sketch's spec changed by the words."""
    if base is not None:
        spec, changed = apply_words(base, brief)
        said = spec['said'] + ([f"changed from the base: {', '.join(changed)}"] if changed else
                               ["the words named nothing to change: say what to change (an instrument, tempo, "
                                "sparser or busier, a section)"])
    else:
        spec = read_brief(brief)
        said = list(spec['said'])
        if not spec['parts']:
            out = []
            for st in ORDER[:n]:
                s = style_spec(st)
                s.update({'key': key or spec['key'], 'bpm': bpm or spec['bpm'], 'form': spec['form'],
                          'soft': spec['soft']})
                out.append((st, s))
            return out, said + ["the brief names no genre or instrument: three contrasting styles (piano, chamber, "
                                "band); sketch again with what the person wants to hear"]
    if key:
        spec['key'] = key
    if bpm:
        spec['bpm'] = bpm
    out = []
    for label, d in VARIANTS[:n]:
        s = copy.deepcopy(spec)
        s['dense'] = d if label != 'as asked' else s.get('dense', 0)
        # spec S-3: real contrast. Within what the words fixed: the sparser reading moves key (up a fourth, same
        # mode), the busier one takes a form with an intro and an ending (so a different length too)
        if d < 0 and not spec.get('key'):
            s['key_shift'] = 5
            label = 'sparser, other chords, other key'
        if d > 0 and not spec.get('form') and spec.get('feel') != 'classical':
            s['form'] = ['intro', 'groove', 'groove', 'outro']
            label = 'busier, other chords, intro and ending'
        out.append((label, s))
    return out, said


# ------------------------------------------------------------------ the first session

def marker_path():
    return os.environ.get('ISMAIL_FIRST_SESSION') or os.path.join(os.path.expanduser('~'), '.ismail',
                                                                  'first_session_done')


def mark_done():
    p = marker_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf8') as f:
        f.write('the person kept a first sketch; guide no longer opens with the first session\n')


def _has_render(root, depth=4):
    """A finished render somewhere under root: a renders/ folder holding a wav or mp3 (sketches do not count)."""
    stack = [(root, 0)]
    while stack:
        d, k = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if not e.is_dir(follow_symlinks=False) or e.name in ('sketches', 'cache', '.git', 'node_modules'):
                continue
            if e.name == 'renders':
                try:
                    if any(f.endswith(('.wav', '.mp3')) for f in os.listdir(e.path)):
                        return True
                except OSError:
                    pass
            elif k < depth:
                stack.append((e.path, k + 1))
    return False


def is_new(project=None):
    """A person who has made nothing with ismail yet: no first-session mark and no finished render in the songs
    folder or next to the given project."""
    if os.path.exists(marker_path()):
        return False
    from .handoffs import SONGS
    roots = [SONGS] + ([os.path.dirname(os.path.abspath(project))] if project else [])
    return not any(os.path.isdir(r) and _has_render(r) for r in roots)
