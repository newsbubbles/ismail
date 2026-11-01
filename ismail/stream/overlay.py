"""Callouts and panels: text the agents put over the visual page, live, checked here so the page only draws.

A callout is one timed line ("drop in 8 bars", "welcome, raiders"): it comes in, stays duration_s, and goes. Callouts
share a slot (an anchor such as 'lower-third', or a name of the agent's own) one at a time, so two never overlap
there: a new one waits its turn, higher priority first, then first asked. A panel is a box bound to the stream's
own data (what plays and whose request it is, the request line, the newest chat, the credits) that keeps itself
up to date. Both draw as page elements over the canvas, from GET /visual/live (about once a second, at once on a
change).

Positions and sizes are fractions of the frame, as in scene.py: x, y the point the box is pinned to, w its widest,
size the font's height. Colours are '#rrggbb'. A wrong field comes back with what it takes."""
import hashlib
import re
import time
from pathlib import Path

from . import config as C, request_log as RL

COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')
SLOT = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')

# font names an agent may give, each a CSS stack that falls back to the system's own sans when a face is missing
FONTS = {
    'sans': 'system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    'serif': 'Georgia, "Times New Roman", serif',
    'mono': 'ui-monospace, Consolas, "Cascadia Mono", "Courier New", monospace',
    'condensed': 'Bahnschrift, "Arial Narrow", "Roboto Condensed", sans-serif',
    'display': 'Impact, Haettenschweiler, "Arial Black", sans-serif',
    'rounded': '"Segoe UI Variable Display", "Nunito", "Arial Rounded MT Bold", system-ui, sans-serif',
    'arial': 'Arial, Helvetica, sans-serif',
    'arial black': '"Arial Black", Arial, sans-serif',
    'bahnschrift': 'Bahnschrift, "Arial Narrow", sans-serif',
    'consolas': 'Consolas, "Courier New", monospace',
    'courier new': '"Courier New", monospace',
    'georgia': 'Georgia, serif',
    'impact': 'Impact, "Arial Black", sans-serif',
    'segoe ui': '"Segoe UI", system-ui, sans-serif',
    'tahoma': 'Tahoma, Verdana, sans-serif',
    'times new roman': '"Times New Roman", Times, serif',
    'trebuchet ms': '"Trebuchet MS", sans-serif',
    'verdana': 'Verdana, sans-serif',
}
FONT_TYPES = {'.ttf': 'font/ttf', '.otf': 'font/otf', '.woff': 'font/woff', '.woff2': 'font/woff2'}
FONT_FILES = {}          # id -> path of a font file a style named, served at /visual/font/<id>
REPO = Path(__file__).resolve().parents[2]

# name -> (x, y, align, valign): the point the box is pinned to, and which side of the box sits on it
ANCHORS = {
    'top': (0.5, 0.05, 'center', 'top'), 'top-left': (0.04, 0.05, 'left', 'top'),
    'top-right': (0.96, 0.05, 'right', 'top'), 'left': (0.04, 0.5, 'left', 'middle'),
    'center': (0.5, 0.5, 'center', 'middle'), 'right': (0.96, 0.5, 'right', 'middle'),
    'lower-third': (0.06, 0.8, 'left', 'bottom'), 'bottom': (0.5, 0.92, 'center', 'bottom'),
    'bottom-left': (0.04, 0.92, 'left', 'bottom'), 'bottom-right': (0.96, 0.92, 'right', 'bottom'),
}
ANIMS = ('none', 'fade', 'slide-left', 'slide-right', 'slide-up', 'slide-down', 'pop')
ALIGN = ('left', 'center', 'right')
STYLE = {'font': str, 'size': float, 'color': str, 'weight': int, 'italic': bool, 'background': str,
         'background_opacity': float, 'outline': float, 'outline_color': str, 'shadow': float, 'shadow_color': str,
         'align': str, 'padding': float, 'radius': float, 'opacity': float, 'accent': str}
RANGES = {'size': (0.01, 0.3), 'background_opacity': (0, 1), 'outline': (0, 0.3), 'shadow': (0, 1),
          'padding': (0, 2), 'radius': (0, 1), 'opacity': (0, 1)}
CALLOUT_STYLE = {'font': 'sans', 'size': 0.06, 'color': '#ffffff', 'weight': 700, 'italic': False,
                 'background': '#000000', 'background_opacity': 0.55, 'outline': 0.0, 'outline_color': '#000000',
                 'shadow': 0.0, 'shadow_color': '#000000', 'padding': 0.35, 'radius': 0.2, 'opacity': 1.0,
                 'accent': '#5ec8ff'}
PANEL_STYLE = {**CALLOUT_STYLE, 'size': 0.028, 'weight': 500, 'background_opacity': 0.45, 'padding': 0.6,
               'radius': 0.3}
MAX_TEXT = 200
MAX_QUEUE = 12           # waiting callouts per slot


def _num(v, name, lo, hi):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= float(v) <= hi:
        raise ValueError(f'{name}: a number from {lo} to {hi}')
    return float(v)


def font_file(path, where):
    """A font file's id once it is checked: a .ttf, .otf, .woff or .woff2 under the ismail folder or the stream home."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = REPO / p
    p = p.resolve()
    roots = [REPO, Path(C.HOME).resolve()]
    if p.suffix.lower() not in FONT_TYPES:
        raise ValueError(f"{where} font {path!r}: a name ({', '.join(FONTS)}) or a font file, "
                         f"{', '.join(FONT_TYPES)}")
    if not any(p.is_relative_to(r) for r in roots):
        raise ValueError(f"{where} font {path!r}: a font file must sit under {REPO} or {C.HOME} (copy it there)")
    if not p.is_file() or p.stat().st_size > 20 << 20:
        raise ValueError(f"{where} font {path!r}: no such file, or over 20 MB")
    fid = hashlib.sha1(str(p).lower().encode()).hexdigest()[:12]
    FONT_FILES[fid] = p
    return fid


def check_style(d, where, base):
    """The style d over base (CALLOUT_STYLE or PANEL_STYLE), every field checked, plus what the page needs to draw
    the font: font_css (a CSS family stack) and font_url (for a font file)."""
    if d is None:
        d = {}
    if not isinstance(d, dict):
        raise ValueError(f"{where} style: a dict of {sorted(STYLE)}")
    bad = sorted(set(d) - set(STYLE))
    if bad:
        raise ValueError(f"{where} style: unknown {bad}; it takes {sorted(STYLE)}")
    s = {**base, **d}
    for k, v in d.items():
        t = STYLE[k]
        if t is float:
            s[k] = _num(v, f'{where} style {k}', *RANGES[k])
        elif t is int and (not isinstance(v, int) or isinstance(v, bool) or v not in range(100, 1000, 100)):
            raise ValueError(f'{where} style {k}: 100, 200, ... 900 (400 regular, 700 bold)')
        elif t is bool and not isinstance(v, bool):
            raise ValueError(f'{where} style {k}: true or false')
        elif t is str and not isinstance(v, str):
            raise ValueError(f'{where} style {k}: a string')
    for k in ('color', 'outline_color', 'shadow_color', 'accent'):
        if not COLOR.match(s[k]):
            raise ValueError(f"{where} style {k} {s[k]!r}: '#rrggbb'")
    if s['background'] != 'none' and not COLOR.match(s['background']):
        raise ValueError(f"{where} style background {s['background']!r}: '#rrggbb' or 'none' "
                         f"(background_opacity sets how solid)")
    if 'align' in s and s['align'] not in ALIGN:
        raise ValueError(f"{where} style align: one of {', '.join(ALIGN)}")
    name = s['font'].strip().lower()
    if name in FONTS:
        s['font'], s['font_css'], s['font_url'] = name, FONTS[name], None
    else:
        fid = font_file(s['font'], where)
        s['font_css'], s['font_url'] = f'"ismail-{fid}", {FONTS["sans"]}', f'/visual/font/{fid}'
    return s


def check_place(where, anchor=None, x=None, y=None, w=None, default='lower-third', w_default=0.6):
    """{anchor, x, y, w, align, valign}: an anchor's point, nudged by x / y when given."""
    if anchor is None and x is None and y is None:
        anchor = default
    if anchor is not None and anchor not in ANCHORS:
        raise ValueError(f"{where} anchor {anchor!r}: one of {', '.join(ANCHORS)}, or x= and y= (fractions of the "
                         f"frame)")
    if anchor is None and (x is None or y is None):
        raise ValueError(f"{where}: give both x= and y= (fractions of the frame), or an anchor")
    ax, ay, align, valign = ANCHORS[anchor] if anchor else (None, None, 'left', 'top')
    return {'anchor': anchor, 'x': _num(ax if x is None else x, f'{where} x', -0.5, 1.5),
            'y': _num(ay if y is None else y, f'{where} y', -0.5, 1.5),
            'w': _num(w_default if w is None else w, f'{where} w', 0.05, 1), 'align': align, 'valign': valign}


def check_callout(text, style=None, anchor=None, x=None, y=None, w=None, slot=None, enter='fade', exit='fade',
                  anim_s=0.4, duration_s=6.0, priority=5):
    """One callout, checked: everything the page needs to draw it and the queue needs to time it."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("callout text: the words to show (stream_callout(clear=True) takes callouts down)")
    text = text.replace('\r', '').strip()
    if len(text) > MAX_TEXT or text.count('\n') > 2:
        raise ValueError(f"callout text: at most {MAX_TEXT} characters on 3 lines; it is {len(text)} on "
                         f"{text.count(chr(10)) + 1}")
    place = check_place('callout', anchor, x, y, w)
    s = check_style(style, 'callout', CALLOUT_STYLE)
    s.setdefault('align', place['align'])
    if slot is None:
        slot = place['anchor'] or 'free'
    if not isinstance(slot, str) or not SLOT.match(slot):
        raise ValueError(f"callout slot {slot!r}: 1-32 of a-z, 0-9, - and _ (callouts in one slot take turns)")
    for k, v in (('enter', enter), ('exit', exit)):
        if v not in ANIMS:
            raise ValueError(f"callout {k}: one of {', '.join(ANIMS)} (slide-<side>: in from that side, out to it)")
    dur = _num(duration_s, 'callout duration_s', 1, 600)
    anim = _num(anim_s, 'callout anim_s', 0, 3)
    if anim * 3 > dur:
        raise ValueError(f"callout anim_s {anim}: at most a third of duration_s ({dur / 3:.2f} here), so it stays "
                         f"still long enough to read")
    if not isinstance(priority, int) or isinstance(priority, bool) or not 0 <= priority <= 9:
        raise ValueError("callout priority: a whole number 0-9 (higher waits less; nothing cuts the one showing)")
    return {'text': text, 'slot': slot, 'place': place, 'style': s, 'enter': enter, 'exit': exit, 'anim_s': anim,
            'duration_s': dur, 'priority': priority}


class Callouts:
    """The callouts showing and waiting, one at a time per slot. Time moves on lazily: each read starts the next one
    at the moment the last one ended, so a queue plays back to back with no gap."""

    def __init__(self):
        self.items = []          # showing and waiting, in the order asked
        self.n = 0

    def _advance(self, now):
        for slot in {c['slot'] for c in self.items}:
            ended = None
            while True:
                cur = next((c for c in self.items if c['slot'] == slot and c['start'] is not None), None)
                if cur and cur['end'] <= now:
                    self.items.remove(cur)
                    ended, cur = cur['end'], None
                if cur:
                    break
                wait = self._waiting(slot)
                if not wait:
                    break
                nxt = wait[0]
                nxt['start'] = now if ended is None else max(ended, nxt['added'])
                nxt['end'] = nxt['start'] + nxt['duration_s']

    def _waiting(self, slot):
        return sorted((c for c in self.items if c['slot'] == slot and c['start'] is None),
                      key=lambda c: (-c['priority'], c['id']))

    def add(self, c, now, who=None):
        self._advance(now)
        same = next((d for d in self.items if d['slot'] == c['slot'] and d['text'] == c['text']), None)
        if same:
            raise ValueError(f"callout #{same['id']} says that in {c['slot']} already "
                             f"({'showing' if same['start'] is not None else 'queued'}); "
                             f"stream_callout(clear=True, id={same['id']}) first to show it again")
        if len(self._waiting(c['slot'])) >= MAX_QUEUE:
            raise ValueError(f"slot {c['slot']}: {MAX_QUEUE} callouts wait there already; clear some "
                             f"(stream_callout(clear=True, slot='{c['slot']}')) or use another slot")
        self.n += 1
        c = {**c, 'id': self.n, 'who': who, 'added': now, 'start': None, 'end': None}
        self.items.append(c)
        self._advance(now)
        return c

    def clear(self, now, id=None, slot=None):
        """Take down one callout by id, every one in a slot, or all of them; returns the ids taken down."""
        self._advance(now)
        if id is not None:
            if not any(c['id'] == id for c in self.items):
                have = ', '.join(f"#{c['id']}" for c in self.items) or 'none'
                raise ValueError(f"no callout #{id} showing or queued (there are: {have})")
            gone = [c for c in self.items if c['id'] == id]
        else:
            gone = [c for c in self.items if slot is None or c['slot'] == slot]
        for c in gone:
            self.items.remove(c)
        self._advance(now)
        return [c['id'] for c in gone]

    def schedule(self, now):
        """Every callout with its start and end (planned, for the waiting ones, if nothing changes), slot by slot."""
        self._advance(now)
        out = []
        for slot in sorted({c['slot'] for c in self.items}):
            cur = next((c for c in self.items if c['slot'] == slot and c['start'] is not None), None)
            t = cur['end'] if cur else now
            if cur:
                out.append({**cur, 'state': 'showing'})
            for c in self._waiting(slot):
                out.append({**c, 'state': 'queued', 'start': t, 'end': t + c['duration_s']})
                t += c['duration_s']
        return out


# ------------------------------------------------------------------ panels

PANELS = {        # kind -> (default anchor, default rows, default title, what it shows)
    'now': ('top-right', 1, 'Now playing', 'the request playing and who asked, or the stream\'s now line'),
    'requests': ('right', 5, 'Up next', 'the waiting requests in the order they play, with places and names'),
    'chat': ('bottom-right', 5, 'Chat', 'the newest chat lines from every venue (or venues=[...]), names and text'),
    'credits': ('left', 6, 'Thanks to', 'the viewers whose requests played, newest last'),
}
PANEL_FIELDS = ('anchor', 'x', 'y', 'w', 'rows', 'title', 'empty', 'style', 'venues')


def check_panel(kind, d):
    """A panel's settings, checked, with its defaults filled in."""
    if kind not in PANELS:
        raise ValueError(f"panel kind {kind!r}: one of {', '.join(PANELS)}")
    bad = sorted(set(d) - set(PANEL_FIELDS))
    if bad:
        raise ValueError(f"panel {kind}: unknown {bad}; it takes {list(PANEL_FIELDS)}")
    anchor, rows, title, _ = PANELS[kind]
    place = check_place(f'panel {kind}', d.get('anchor'), d.get('x'), d.get('y'), d.get('w'), anchor, 0.3)
    s = check_style(d.get('style'), f'panel {kind}', PANEL_STYLE)
    s.setdefault('align', 'right' if place['align'] == 'right' else 'left')
    n = d.get('rows', rows)
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 12:
        raise ValueError(f"panel {kind} rows: a whole number 1-12")
    for k in ('title', 'empty'):
        if d.get(k) is not None and (not isinstance(d[k], str) or len(d[k]) > 80):
            raise ValueError(f"panel {kind} {k}: a string of at most 80 characters ('' for none)")
    venues = d.get('venues')
    if venues is not None and (not isinstance(venues, list) or not all(isinstance(v, str) for v in venues)):
        raise ValueError(f"panel {kind} venues: a list of venue names (venue_list shows them), or leave it out for all")
    raw = {k: d[k] for k in PANEL_FIELDS if d.get(k) is not None}
    return {'kind': kind, 'raw': raw, 'place': place, 'style': s, 'rows': n,
            'title': title if d.get('title') is None else d['title'], 'empty': d.get('empty') or '',
            'venues': venues}


def _short(s, n=140):
    s = ' '.join(str(s or '').split())
    return s if len(s) <= n else s[:n - 1] + '…'


def panel_rows(p, log, home=None):
    """What a panel shows now: a list of {text, by, venue, place} rows, newest or next first as the kind reads."""
    kind, n = p['kind'], p['rows']
    if kind == 'chat':
        want = set(p['venues']) if p['venues'] else None
        rows = []
        for d in reversed(log.lines):
            if d['kind'] not in ('chat', 'event') or d.get('own') or (want and d['venue'] not in want):
                continue
            u = d.get('user') or {}
            text = d.get('text') or ' '.join(str(x) for x in (d.get('type'), d.get('amount')) if x)
            rows.append({'by': _short(u.get('display') or u.get('name') or 'viewer', 30), 'text': _short(text),
                         'venue': d.get('platform') or d['venue']})
            if len(rows) == n:
                break
        return rows[::-1]                   # oldest at the top, as chat reads
    reqs = RL.state()
    venues = C.load_venues()

    def where(r):
        return (venues.get(r.get('venue')) or {}).get('platform') or r.get('venue')
    if kind == 'now':
        playing = [r for r in reqs.values() if r['status'] == 'playing']
        if playing:
            r = playing[-1]
            return [{'text': _short(r['what']), 'by': _short(r['who'], 30), 'venue': where(r)}]
        try:
            line = (Path(home or C.HOME) / 'text' / 'now.txt').read_text(encoding='utf8').strip()
        except OSError:
            line = ''
        if line.startswith('request from @'):      # request_mark's line for a request that has stopped playing
            line = ''
        return [{'text': _short(line)}] if line else []
    if kind == 'requests':
        return [{'place': i, 'text': _short(r['what'], 80), 'by': _short(r['who'], 30), 'venue': where(r),
                 'votes': len(r['votes'])} for i, r in enumerate(RL.line_up(reqs)[:n], 1)]
    played = sorted((r for r in reqs.values() if r['status'] in ('playing', 'done')),
                    key=lambda r: r['started'] or r['added'])
    seen, rows = set(), []
    for r in reversed(played):              # each viewer once, by their newest request
        if r['key'] in seen:
            continue
        seen.add(r['key'])
        rows.append({'by': _short(r['who'], 30), 'text': _short(r['what'], 80), 'venue': where(r)})
        if len(rows) == n:
            break
    return rows[::-1]


def page_panel(p):
    """The fields the page draws a panel from (its settings without the raw ones the agent gave)."""
    return {k: p[k] for k in ('kind', 'place', 'style', 'rows', 'title', 'empty')}


def now():
    return time.time()
