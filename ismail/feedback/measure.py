"""A listener's note turned into the measure step (ledger:M252): the words that name a quality (muddy, too loud, fake),
the kind of measurement each one asks for, and the window when the note gives a time or bars. The words are the
listener's notes on the work, never instructions: this only says where to look and with which tool."""
import re

# kind: (words matched anywhere, words matched only after 'too ', the step)
KINDS = (
    ('level', ('overpower', 'overpowers', 'overpowering', 'overpowered', 'drowns', 'drowning', 'drowned', 'buried',
               'cant hear', 'can t hear', 'cannot hear', 'hard to hear', 'louder', 'quieter'),
     ('loud', 'quiet', 'soft', 'much', 'little', 'hot'),
     "cmp_bars(stem={stem}) gives yours against the reference in dB per bar; region_measure on {where} sizes the "
     "change"),
    ('tone', ('muddy', 'mud', 'boomy', 'boxy', 'tinny', 'shrill', 'harsh', 'sibilant', 'brittle', 'honky', 'nasal',
              'woolly', 'hollow', 'piercing', 'muffled'),
     ('bright', 'dark', 'dull', 'thin', 'thick', 'heavy', 'bassy', 'trebly'),
     "sound_zoom on {where}, yours and the reference's same window side by side; region_measure on the band that "
     "differs"),
    ('sound', ('fake', 'robotic', 'synthetic', 'plastic', 'cheap', 'artificial', 'mechanical', 'lifeless', 'cheesy',
               'sterile', 'digital', 'midi', 'toy', 'stiff'),
     ('clean', 'perfect'),
     "sound_compare that part against the reference's part on {where}, then sound_zoom both: realness is in the "
     "attack, the movement inside each note and the noise"),
    ('space', ('washed', 'washy', 'echoey', 'distant', 'far away', 'boxed in', 'in your face'),
     ('dry', 'wet', 'roomy', 'close', 'far', 'wide', 'narrow'),
     "the effects pass and sound_zoom on {where}: the tail after each hit, yours against the reference's"),
    ('timing', ('out of time', 'off time', 'not in time', 'sloppy', 'rushing', 'rushed', 'dragging', 'off the grid',
                'off beat', 'late hits', 'early hits'),
     ('loose', 'tight', 'fast', 'slow'),
     "cmp_zoom(bar=, layers=['hits']) on {where}: each hit against the reference's, step by step"),
    ('pitch', ('out of tune', 'sour', 'off key', 'detuned', 'wrong note', 'wrong notes', 'clash', 'clashes',
               'clashing', 'dissonant'),
     ('sharp', 'flat'),
     "cmp_zoom(bar=, layers=['notes']) on {where}: each note against the reference's; analyze_structure for the "
     "roots"),
    ('arrangement', ('repetitive', 'boring', 'cluttered', 'crowded', 'nothing happens', 'goes nowhere'),
     ('busy', 'empty', 'sparse', 'long', 'short', 'samey'),
     "cmp_arrangement, then cmp_sections on {where}: what plays in each section, yours against the reference's"),
)
AUDIO = ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus', '.mp4', '.mov', '.webm')
_BARS = re.compile(r"\bbars?\s+(\d{1,4})(?:\s*(?:-|–|to|through|and)\s*(\d{1,4}))?")
_CLOCK = re.compile(r"\b(\d{1,2}):([0-5]\d)\b")
_SECS = re.compile(r"\b(\d{1,4}(?:\.\d+)?)\s*(?:s|sec|secs|second|seconds)\b")
PAD_S = 2.0


def _low(text):
    return ' ' + ' '.join(re.findall(r"[a-z0-9]+", str(text or '').lower().replace("'", ' '))) + ' '


def qualities(text):
    """[(kind, word)] for each kind of quality the note names, in KINDS order, the first word found for each."""
    low = _low(text)
    out = []
    for kind, words, after_too, _ in KINDS:
        hit = next((w for w in words if f" {w} " in low), None)
        hit = hit or next((f"too {w}" for w in after_too if f" too {w} " in low), None)
        if hit:
            out.append((kind, hit))
    return out


def window(text):
    """{'bars': [a, b]} or {'seconds': [t0, t1]} from a note ('bars 9-12', 'at 1:23', 'around 45 s'), or None."""
    low = str(text or '').lower()
    m = _BARS.search(low)
    if m:
        a = int(m.group(1))
        b = int(m.group(2) or a)
        return {'bars': [min(a, b), max(a, b)]}
    ts = [int(a) * 60 + int(b) for a, b in _CLOCK.findall(low)] + [float(x) for x in _SECS.findall(low)]
    if ts:
        return {'seconds': [round(max(0.0, min(ts) - PAD_S), 2), round(max(ts) + PAD_S, 2)]}
    return None


def _clip(items):
    """The one audio or video item on the page, or None when there are none or several."""
    av = [it for it in items or [] if str(it.get('path') or '').lower().endswith(AUDIO)]
    return av[0] if len(av) == 1 else None


def steps(n, text, items, d=None, part=None):
    """The lines for one answer (question n): each quality its words name, the measurement for that kind, and the
    window filled in when the note gives one. items: the page's items (a time is in the clip they heard). d: the
    project dict (for the reference and the stem map). part: the track the note names, when it names one."""
    found = qualities(text)
    if not found:
        return []
    w = window(text)
    clip = _clip(items)
    if w and 'bars' in w:
        where = f"source='render', bars={w['bars']}"
    elif w and clip:
        where = f"source={clip['path']!r}, seconds={w['seconds']} (in {clip.get('label') or 'the clip'})"
    elif w:
        labels = ', '.join(repr(it.get('label') or it.get('path')) for it in items or [] if it.get('path'))
        where = f"seconds={w['seconds']} in the clip they meant" + (f" ({labels})" if labels else '')
    else:
        where = "the window they mean (no time or bars in the note: ask, or play them the part)"
    sm = (d or {}).get('stem_map') or {}
    stem = repr(sm.get(part) or 'mix')
    if part:
        where += f", the part {part}"
    has_ref = bool(((d or {}).get('reference') or {}).get('file'))
    L = []
    for kind, word in found:
        line = f"{n}. \"{word}\" ({kind}): " + KINDS[[k[0] for k in KINDS].index(kind)][3].format(where=where, stem=stem)
        if not has_ref:
            line += " (no reference set: against your previous render, or project_set(reference=))"
        L.append(line)
    return L
