"""The phone server: python -m ismail.phone.server [--port 8870] [--inbox <file>].

Binds 127.0.0.1 only; the tailnet reaches it through `tailscale serve` (https, so the page can use the microphone and
install as an app). It never opens the speakers and never starts a set: it relays what a live engine already plays.

Audio: the newest live engine's master (`/stream?name=master`, raw PCM) is fed on the wall clock (silence while no
set plays, so a phone in a pocket keeps its stream) into one ffmpeg mp3 encoder per bitrate; the last RING_S seconds
are kept, so a listener joins live or up to RING_S back. A spoken line (phone_say speak=True) is mixed into the feed
with the music ducked under it.

Heard: every fed block is logged with the engine's beat at that moment; a page reports its stream session id and
audio.currentTime with every tap, note and poll, so the server knows the bar the person actually heard and how far
behind the room the phone is.

What the person sends lands in <home>/inbox.jsonl, in the routed inbox (phone_route, else the playing engine's
<project>/notes/phone_inbox.jsonl), and runs the hooks in <home>/hooks.json. Voice notes are transcribed by the
speech server ($ISMAIL_SPEAK, speakwright's OpenAI-style /v1/audio/transcriptions), waiting while the CPU is over
the machine governor's limit.
"""
import argparse
import bisect
import collections
import datetime
import http.client
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

try:                                       # a package module in ismail; a plain script when phone_start runs it
    from . import vibe
    from .. import tags
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import vibe
    import tags

HOME = Path(os.environ.get('ISMAIL_PHONE_HOME') or Path.home() / '.ismail' / 'phone')
PAGE = Path(__file__).resolve().parent / 'page'
# The page's sounds (Nate 10-06 14:42: "you make your sounds and then you attach it to this interface ... the same
# thing applies to everything as like a design philosophy"). An agent makes each one with ismail and attaches it with
# phone_sounds; an event with none stays silent, except the four note tones, which fall back to the built-in ones.
SOUND_EVENTS = {
    'message': 'a phone_say caption arrives (a message to them)',
    'note_start': 'a voice note starts (built-in: a rising tone)',
    'note_end': 'a voice note ends (built-in: a falling tone)',
    'note_sent': 'a voice note has arrived (built-in: a chirp)',
    'error': 'something failed: a blocked mic, a note too short (built-in: a low tone)',
    'tap': 'any key they press that sends (love, change it up, a mood, an agent button), unless it has its own',
    'love': 'they press Love this', 'change': 'they press Change it up', 'mood': 'they pick a mood',
    'offer': 'a file is offered to them', 'panel': 'a panel, a question or an exam opens',
    'chapter': 'the piece playing changes',
}
# the VR stage's earcon names (ismail/stage/page/voice.js) mean the same events here, so one set of names serves both
SOUND_ALIASES = {'incoming': 'message', 'rec_start': 'note_start', 'rec_stop': 'note_end', 'sent': 'note_sent'}
PAUSE_FADE_S = 4.0      # a pause tap fades the set out over this long and stops it (no agent needs to be awake)
MOVE_SETTLE_BARS = 8   # a scheduled vibe becomes the standing one this many bars after the room passed its bar
SOUND_EXT = ('.wav', '.ogg', '.mp3', '.m4a', '.webm', '.flac')
SOUND_MAX_S, SOUND_MAX_BYTES = 5.0, 1 << 20
SPEAK = os.environ.get('ISMAIL_SPEAK') or 'http://127.0.0.1:8765'
SR = 44100
BLOCK = 2048                       # frames fed per step (46 ms)
RING_S = 120.0                     # seconds of encoded stream kept for rewind and late joiners
MASTER_S = 150.0                   # seconds of the streamed master kept raw, for the music under a voice note
REF_LEAD_S, REF_TAIL_S = 3.0, 2.0  # how much of it before the note begins and after it ends (ledger:M163)
REF_GUESS_S = 20.0                 # off the stream (the room speaker), the note's start is a guess: this much either side
KEEP_MASTER = os.environ.get('ISMAIL_PHONE_KEEP_MASTER', '1') != '0'   # take the master while an engine plays, page or not
LEAD_S = 1.0                       # a live join starts this far back, so the phone's buffer fills at once
BOOT = secrets.token_hex(4)        # this server run: a page that sees it change knows the server restarted


def page_build():
    """The page code this server serves: an open page that sees it change reloads itself when it is idle."""
    import hashlib
    h = hashlib.sha1()
    for n in ('index.html', 'app.js', 'sw.js'):
        try:
            h.update((PAGE / n).read_bytes())
        except OSError:
            pass
    return h.hexdigest()[:10]


BUILD = page_build()
IDLE_S = 60.0                      # an encoder with no listener for this long stops
DUCK = 0.3                         # the music's gain under a spoken line
KBPS = (64, 128)
TAPS = {'love': 'loves this: cut a highlight here', 'change': 'change it up now', 'energy_up': 'more energy',
        'energy_down': 'calmer', 'louder': 'louder', 'quieter': 'quieter', 'pause': 'pause the set (gracefully)',
        'resume': 'resume the set', 'start_set': 'start a set', 'rewind': 'rewound 30 s to hear that again'}
MOODS = ('calm', 'steady', 'lift', 'peak')
# a short voice note that is only a command acts as one (earbuds give one button, so the voice does the rest);
# the words still reach the agent as voice_text, and the act carries via='voice' and the note's id
MARKS = ('loved', 'replay', 'new')   # a piece they loved before, one the DJ plays again, one just made
PAGE_OPEN_S = 60.0          # a page that asked for its state this recently is open (it long-polls every 20 s)
VOICE_CMDS = [(r'stop (?:listening|the stream|streaming)', 'stop_listening'), (r'(?:i )?love (?:this|that|it)', 'love'),
              (r'change it up', 'change'), (r'more energy', 'energy_up'), (r'calmer|calm (?:it )?down', 'energy_down'),
              (r'louder', 'louder'), (r'quieter|softer', 'quieter'), (r'pause the set', 'pause'),
              (r'resume the set', 'resume')]
STATUS = re.compile(r'live ([\d.]+) BPM (\d+)/4 \| heard bar (\d+)(?: beat ([\d.]+))? \((\d+) s\) \| '
                    r'mixed ahead ([\d.]+) s')


def readable_name(text):
    """A file name a person can read: the label's words, no slashes or odd characters (a download is named this)."""
    s = re.sub(r'[\\/:*?"<>|]+', ' ', str(text)).replace('_', ' ')
    s = re.sub(r'\s+', ' ', s).strip(' .')[:80]
    return s or 'ismail'


def note_context(raw):
    """A voice note's context from the page: `media` (each video or clip on the open panel when the note began:
    kind, panel, src, t in seconds, dur, playing, label) and `acts` (page events while it recorded, `ms` from its
    start). Bounded and typed; anything else is dropped."""
    try:
        c = json.loads(raw or '{}')
    except (TypeError, ValueError):
        return {}
    if not isinstance(c, dict):
        return {}
    def clean(d, keys):
        return {k: (v[:120] if isinstance(v, str) else v) for k, v in d.items()
                if k in keys and (v is None or isinstance(v, (str, int, float, bool)))}
    out = {}
    media = [clean(m, ('kind', 'panel', 'src', 't', 'dur', 'playing', 'label'))
             for m in (c.get('media') or [])[:8] if isinstance(m, dict)]
    acts = [clean(a, [k for k in a if re.fullmatch(r'[a-z_]{1,24}', str(k))])
            for a in (c.get('acts') or [])[:40] if isinstance(a, dict) and re.fullmatch(r'[a-z_]+', str(a.get('what', '')))]
    if media:
        out['media'] = media
    if acts:
        out['acts'] = acts
    return out


def clip_seconds(path):
    """A sound file's length in seconds, or None when it cannot be read here."""
    try:
        import soundfile as sf
        return float(sf.info(path).duration)
    except Exception:
        return None


def now_iso(at=None):
    t = datetime.datetime.fromtimestamp(at) if at else datetime.datetime.now()
    return t.isoformat(timespec='seconds')


INPUT_KINDS = ('choice', 'check', 'toggle', 'text')


def panel_link(link):
    """A panel's link: a path on this server (starts with one '/'), so it opens in the same tab and stays on the
    tailnet's https address. Anything else is refused."""
    if not isinstance(link, str) or not link.startswith('/') or link.startswith('//') or not link.isprintable() \
            or '\\' in link or ' ' in link:
        raise ValueError(f"link must be a path on the phone page, starting with one '/' (like '/eye/round1?from=phone'), "
                         f"not {link!r}")
    return link


def panel_inputs(inputs):
    """A panel's inputs (ledger:M167, Nate 10-07: "checkboxes or toggles so that I could give more of a detailed
    response"): [{id, kind: choice | check | toggle | text, label, options (choice and check), value}]."""
    out = []
    for i, x in enumerate(inputs):
        if not isinstance(x, dict) or x.get('kind') not in INPUT_KINDS:
            raise ValueError(f"inputs[{i}]: {{'id', 'kind': one of {', '.join(INPUT_KINDS)}, 'label', 'options' "
                             f"(choice and check)}}, e.g. {{'id': 'tempo', 'kind': 'choice', 'label': 'Tempo', "
                             f"'options': ['slower', 'same', 'faster']}}")
        opts = [str(o) for o in (x.get('options') or [])]
        if x['kind'] in ('choice', 'check') and not opts:
            raise ValueError(f"inputs[{i}] ({x['kind']}): give 'options', the labels to pick from")
        out.append({'id': str(x.get('id') or f'in{i + 1}'), 'kind': x['kind'], 'label': str(x.get('label') or ''),
                    **({'options': opts} if opts else {}), **({'value': x['value']} if 'value' in x else {}),
                    **({'optional': True} if x.get('optional') else {})})
    return out


def ffmpeg():
    return os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg')


def live_engines():
    d = Path(os.environ.get('ISMAIL_LIVE_REGISTRY') or Path.home() / '.ismail' / 'live')
    out = {}
    for f in d.glob('*.json'):
        try:
            r = json.loads(f.read_text(encoding='utf8'))
            out[int(r['port'])] = r
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def engine_call(port, op, timeout=3, **args):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=json.dumps({'op': op, 'args': args}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        res = json.loads(r.read())
    if not res.get('ok'):
        raise RuntimeError(res.get('error', 'engine error'))
    return res['result']


def parse_status(text):
    """The engine's status text -> {bpm, bpb, beat (heard, 0-based), ahead, recording, playing, next}."""
    m = STATUS.search(text or '')
    if not m:
        return None
    bpm, bpb = float(m.group(1)), int(m.group(2))
    beat = (int(m.group(3)) - 1) * bpb + (float(m.group(4)) - 1 if m.group(4) else 0.0)
    rec = re.search(r'\| recording (\S+)', text)
    playing = re.findall(r'^\s+([\w.-]+)\s.*?\| playing (\S+)', text, re.M)
    nxt = re.search(r'next (\S+) at (bar \d+(?: beat [\d.]+)?)', text)
    out = re.search(r'\| output (.+?)( \(follows the default\))?(?: \||$)', (text or '').splitlines()[0])
    return {'bpm': bpm, 'bpb': bpb, 'beat': beat, 'ahead': float(m.group(6)),
            'output': out.group(1) if out else None, 'follow': bool(out and out.group(2)),
            'recording': rec.group(1) if rec else None,
            'playing': [f"{c} ({t})" for t, c in playing[:3]],
            'next': f"{nxt.group(1)} at {nxt.group(2)}" if nxt else None}


def bar_text(beat, bpb):
    bar = int(beat // bpb) + 1
    b = beat - (bar - 1) * bpb + 1
    return f"bar {bar}" + (f" beat {b:.0f}" if abs(b - round(b)) < 0.25 else f" beat {b:.1f}")


class Encoder:
    """One ffmpeg mp3 encoder (constant bitrate, so a byte offset is a time) and the ring of what it made."""

    def __init__(self, kbps, pcm0):
        self.kbps, self.rate, self.pcm0 = kbps, kbps * 1000 / 8, pcm0
        self.chunks, self.total, self.cond = collections.deque(), 0, threading.Condition()
        self.listeners, self.last = 0, time.time()
        flags = 0x08000000 if os.name == 'nt' else 0          # no console window
        self.proc = subprocess.Popen(
            [ffmpeg(), '-hide_banner', '-loglevel', 'error', '-f', 's16le', '-ar', str(SR), '-ac', '2', '-i', 'pipe:0',
             '-c:a', 'libmp3lame', '-b:a', f'{kbps}k', '-write_xing', '0', '-id3v2_version', '0', '-flush_packets', '1',
             '-f', 'mp3', 'pipe:1'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            b = self.proc.stdout.read1(8192)
            if not b:
                break
            with self.cond:
                self.chunks.append((self.total, b))
                self.total += len(b)
                while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) < self.total - self.rate * RING_S:
                    self.chunks.popleft()
                self.cond.notify_all()
        with self.cond:
            self.proc = None
            self.cond.notify_all()

    def write(self, pcm):
        try:
            self.proc.stdin.write(pcm)
            self.proc.stdin.flush()
            return True
        except (OSError, AttributeError, ValueError):
            return False

    def start(self, back=0.0):
        with self.cond:
            first = self.chunks[0][0] if self.chunks else self.total
            return int(max(first, self.total - self.rate * (LEAD_S + max(0.0, back))))

    def read(self, off, timeout=5.0):
        """-> (bytes from off, new offset); b'' when nothing came in time; None when the encoder ended."""
        with self.cond:
            if self.total <= off:
                self.cond.wait(timeout)
            if self.proc is None and self.total <= off:
                return None, off
            first = self.chunks[0][0] if self.chunks else self.total
            off = max(off, first)
            out = b''.join(b[max(0, off - o):] for o, b in self.chunks if o + len(b) > off)
            return out, off + len(out)

    def close(self):
        try:
            self.proc.stdin.close()
        except (OSError, AttributeError):
            pass


class Phone:
    def __init__(self, inbox=None):
        HOME.mkdir(parents=True, exist_ok=True)
        (HOME / 'voice').mkdir(exist_ok=True)
        self.cond = threading.Condition()
        self.route = inbox
        self.paused_at = 0.0
        self.onsets = []                         # the engine's placed events (cmd_onsets), when the look reacts to them
        self.seq = self._last_seq()
        self.cmd_id = 0
        self.cmds = []
        self.view = {'now': None, 'next': None, 'rec_why': None, 'mood': None, 'captions': [], 'pinned': None,
                     'buttons': [], 'panels': [], 'offers': [], 'sounds': {}, 'vibe_moves': [], 'scenes': {}}
        saved = {}
        try:
            saved = json.loads((HOME / 'state.json').read_text(encoding='utf8'))
            self.view.update({k: saved[k] for k in ('now', 'next', 'rec_why', 'mood', 'buttons', 'pinned', 'panels',
                                                    'shape', 'sounds', 'vibe_moves', 'scenes')
                              if k in saved and saved[k] is not None})
        except (OSError, ValueError):
            pass
        if not self.route and saved.get('route') and Path(saved['route']).parent.is_dir():
            self.route = saved['route']               # phone_route survives a restart (ledger:M157)
        self.view.setdefault('marks', {})         # piece text -> 'loved' | 'replay' | 'new' (the DJ's, phone_now)
        try:
            self.view['marks'] = dict(saved.get('marks') or {})
        except NameError:
            pass
        self.taps = collections.deque(self._recent_taps(), maxlen=60)   # what they loved and asked for, newest last
        self.piece = tuple(saved.get('piece') or (None, time.time()))   # when the playing piece began (M139)
        try:
            self.view['vibe'] = vibe.resolve(saved.get('vibe') or {})
            if saved.get('vibe', {}).get('image'):
                self.view['vibe'].update({k: saved['vibe'][k] for k in ('image', 'image_name') if k in saved['vibe']})
        except (ValueError, TypeError):
            self.view['vibe'] = vibe.resolve({})
        self.files = {k: Path(v) for k, v in (saved.get('files') or {}).items()}   # token -> path (only what an op offered is served); a panel
        # exam id -> its answers file, kept after the panel closes: a voice note said on the card lands there too,
        # late ones included (Voice 10-08: 30 notes missed because they went only to the inbox)
        self.answer_files = dict(saved.get('answer_files') or {})
                                                      # or exam left open survives a restart with its clips
        self.page_seen = 0.0                       # the last time an open page asked for its state
        self.engine = None                         # the playing engine's parsed status
        self.agents = {}                           # who -> last time an agent called
        self.encoders = {}
        self.fed = 0                               # frames fed since the server started
        self.timeline = collections.deque()        # (pcm_s, wall, beat or None, bpm, bpb, ahead)
        self.master = collections.deque()          # (pcm_s, int16 stereo block): the last MASTER_S of the stream
        self.hums = {}                             # voice id -> the hum check (hum.check)
        self.overlay = collections.deque()         # spoken lines waiting to be mixed in (float32 stereo)
        self.sids = {}                             # stream session -> {pcm0, kbps, t, at}
        self.voice_q = collections.deque()
        self.voice_panel = {}                      # voice id -> {panel, for}: a reply said on a panel
        self.voice_state = {}
        self.rounds = {}                           # panel id -> {for, title, since}: exam rounds the page has open now
        self.showing = None                        # {id, since}: the panel the page shows now (its panel_open/close)
        self._rounds_lock = threading.Lock()
        self._recent_rounds()
        self.feeder = None
        threading.Thread(target=self._poll_engine, daemon=True).start()
        threading.Thread(target=self._transcriber, daemon=True).start()

    # ---- exam rounds and the panel on screen (the page's panel_open/close, answers, dismissals)
    @staticmethod
    def _ts_epoch(ts):
        try:
            return datetime.datetime.fromisoformat(ts).timestamp()
        except (TypeError, ValueError):
            return time.time()

    def _recent_rounds(self):
        """After a restart: the exam round and the panel on screen, read back from the last inbox lines."""
        try:
            with open(HOME / 'inbox.jsonl', 'rb') as f:
                f.seek(max(0, os.path.getsize(f.name) - 200_000))
                lines = f.read().decode('utf8', 'replace').splitlines()[1:]
        except OSError:
            return
        open_ids = {x['id'] for x in self.view['panels']}
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            at = self._ts_epoch(r.get('ts'))
            if r.get('kind') == 'exam_round':
                if r.get('state') == 'started':
                    self.rounds[r.get('id')] = {'for': r.get('for'), 'title': r.get('title') or '', 'since': at}
                else:
                    self.rounds.pop(r.get('id'), None)
            elif r.get('kind') == 'page' and r.get('what') == 'panel_open' and r.get('id'):
                self.showing = {'id': r['id'], 'since': at}
            elif r.get('kind') == 'page' and r.get('what') in ('panel_close', 'panel_later'):
                self.showing = None
            elif r.get('kind') in ('answer', 'exam') and self.showing and self.showing['id'] == r.get('id'):
                self.showing = None
        self.rounds = {k: v for k, v in self.rounds.items() if k in open_ids}
        if self.showing and self.showing['id'] not in open_ids:
            self.showing = None

    @staticmethod
    def is_round(p):
        """An exam (phone_exam) or the picture round (a panel linking under /eye/)."""
        return bool(p) and (p.get('kind') == 'exam' or str(p.get('link') or '').startswith('/eye/'))

    def page_panel_event(self, what, pid=None):
        """The page opened or left a panel. Opening a round is 'started'; leaving it (Later, closed the sheet, or
        another panel opened in its place) is 'finished'. One line per state change, never twice."""
        now = time.time()
        if what == 'panel_open' and pid:
            self.showing = {'id': pid, 'since': now}
            p = next((x for x in self.view['panels'] if x['id'] == pid), None)
            for other in [k for k in self.rounds if k != pid]:      # the page shows one panel at a time
                self.round_mark(other, 'finished')
            if self.is_round(p):
                self.round_mark(pid, 'started', p)
        elif what in ('panel_close', 'panel_later'):
            gone = pid or (self.showing or {}).get('id')
            self.showing = None
            for k in [k for k in self.rounds if gone is None or k == gone]:
                self.round_mark(k, 'finished')

    def round_mark(self, pid, state, p=None):
        """Append the exam_round line for a state change, once (a round already started is not started again)."""
        with self._rounds_lock:
            if (state == 'started') == (pid in self.rounds):
                return
            if state == 'started':
                self.rounds[pid] = {'for': p.get('who'), 'title': p.get('title') or '', 'since': time.time()}
                info = self.rounds[pid]
            else:
                info = self.rounds.pop(pid)
        self.post({'kind': 'exam_round', 'state': state, 'id': pid, 'for': info['for'], 'title': info['title']})

    def panel_gone(self, pid):
        """A panel was answered, dismissed or closed by an agent: it is no longer on screen."""
        if self.showing and self.showing['id'] == pid:
            self.showing = None
        self.round_mark(pid, 'finished')

    # ---- the inbox
    def _last_seq(self):
        try:
            with open(HOME / 'inbox.jsonl', 'rb') as f:
                f.seek(max(0, os.path.getsize(f.name) - 4096))
                lines = f.read().decode('utf8', 'replace').strip().splitlines()
            return json.loads(lines[-1])['n'] if lines else 0
        except (OSError, ValueError, KeyError, IndexError):
            return 0

    def _recent_taps(self):
        """The last taps and moods from the inbox, so a restarted server still shows what they asked for."""
        try:
            with open(HOME / 'inbox.jsonl', 'rb') as f:
                f.seek(max(0, os.path.getsize(f.name) - 200_000))
                lines = f.read().decode('utf8', 'replace').splitlines()[1:]
        except OSError:
            return []
        out = []
        for line in lines:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get('kind') in ('tap', 'mood'):
                out.append(self._tap_item(r))
        return out[-60:]

    @staticmethod
    def _tap_item(r):
        return {'ts': r.get('ts'), 'what': r.get('what') or 'mood', 'mood': r.get('mood'),
                'of': (r.get('heard') or {}).get('of') or (r.get('room') or {}).get('of'), 'now': r.get('now'),
                'into_s': r.get('into_s')}

    def now_text(self):
        e = self.engine
        t = self.view['now'] or (', '.join(e['playing']) if e and e['playing'] else None)
        if t != self.piece[0]:
            self.piece = (t, time.time())          # when this piece began (for "2:31 into it", Nate 10-06 08:24)
            if self.view.get('shape', {}).get('of') != t:
                self.view['shape'] = {}            # a new piece: its length and sections come with its phone_now
            self.save()
        return t

    def into_s(self, age_s=0.0):
        """Seconds into the piece playing now (or age_s ago), from when it began; None when nothing names it."""
        self.now_text()
        if not self.piece[0]:
            return None
        return round(max(0.0, time.time() - age_s - self.piece[1]), 1)

    def mark(self, text):
        """How a piece stands with them: the DJ's mark (phone_now now_mark/next_mark), else 'loved' when they tapped
        Love this while it played."""
        if not text:
            return None
        m = self.view['marks'].get(text)
        if m:
            return m
        return 'loved' if any(t['what'] == 'love' and t.get('now') == text for t in self.taps) else None

    def routes(self):
        out = [HOME / 'inbox.jsonl']
        if self.route:
            out.append(Path(self.route))
        elif self.engine and self.engine.get('project'):
            out.append(Path(self.engine['project']) / 'notes' / 'phone_inbox.jsonl')
        return out

    def vibe_now(self):
        """The vibe in force: moves whose bar the room passed MOVE_SETTLE_BARS ago become the vibe (the page applied
        each on the bar it heard; this keeps a page that opens later, or one off the stream, in step)."""
        moves, r = self.view.get('vibe_moves') or [], self.room()
        if moves and r.get('bar'):
            due = [m for m in moves if m['at_bar'] <= r['bar'] - MOVE_SETTLE_BARS]
            if due:
                self.view['vibe'] = due[-1]['vibe']
                self.view['vibe_moves'] = [m for m in moves if m not in due]
                self.save()
        return self.view['vibe']

    def room(self, age_s=0.0):
        """The bar playing in the room age_s seconds ago, from the engine's clock (heard is what the phone's stream
        played; this is there even when the page is off the stream, on the room speakers or a JBL)."""
        b, e = self.beat_now(), self.engine
        if b is None or not e or not e.get('bpm'):
            return {}
        b -= max(0.0, age_s) * e['bpm'] / 60
        bpb = e.get('bpb') or 4
        return {'beat': round(b, 2), 'bar': int(b // bpb) + 1, 'of': bar_text(b, bpb)}

    def post(self, rec):
        age = float(rec.pop('_age', 0) or 0)
        if 'heard' in rec:                                   # from the page: where the set was, on one clock
            rec.setdefault('room', self.room(age))
            rec.setdefault('now', self.now_text())
            rec.setdefault('into_s', self.into_s(age))       # and how far into that piece, in seconds
        with self.cond:
            self.seq += 1
            rec = {'n': self.seq, 'ts': now_iso(time.time() - age), **rec}
            line = json.dumps(rec, ensure_ascii=False) + '\n'
            for p in self.routes():
                try:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    with open(p, 'a', encoding='utf8') as f:
                        f.write(line)
                except OSError as e:
                    print(f'[phone] inbox {p}: {e}', flush=True)
            self.cond.notify_all()
            if rec.get('kind') in ('tap', 'mood'):
                self.taps.append(self._tap_item(rec))
        self._hooks(rec)
        if rec.get('kind') == 'tap' and rec.get('what') == 'pause':
            self.pause_set(rec)
        return rec

    def pause_set(self, rec):
        """A pause tap (the key, or "pause the set" said) stops the set here and now (Nate 10-06 15:08: "Did you not
        see my pause button press? I would definitely stop"; the DJ was mid-task for 70 s). The server fades the
        engine out over PAUSE_FADE_S and stops it, then says so on the page and in the inbox; starting again is the
        DJ's, on their word."""
        e = self.engine
        if not e or not e.get('project') or time.time() - self.paused_at < 15:
            return
        self.paused_at = time.time()
        project = e['project']

        def go():
            try:
                msg = self.stop_set(project)
            except Exception as ex:                       # say it failed; the DJ still sees the tap
                msg = f"could not stop it: {type(ex).__name__}: {ex}"
            ok = not msg.startswith('could not')
            self.post({'kind': 'control', 'what': 'paused' if ok else 'pause_failed', 'by': 'phone server',
                       'ref': rec.get('n'), 'result': str(msg)[:300]})
            text = ('Paused: the set faded out. Say or tap Resume when you want it back.' if ok else
                    'Pause did not reach the set: ' + str(msg)[:120])
            cap = {'text': text, 'ts': now_iso(), 'who': 'phone'}
            self.view['captions'] = (self.view['captions'] + [cap])[-20:]
            self.cmd('caption', text=text, who='phone', buzz=True)
        threading.Thread(target=go, daemon=True).start()

    def stop_set(self, project):
        """live_stop on the playing engine, in a child process (the op table is not loaded here)."""
        code = ("import sys; from ismail.live import ops; "
                f"print(ops.live_stop(sys.argv[1], fade_sec={PAUSE_FADE_S}))")
        r = subprocess.run([sys.executable, '-c', code, str(project)], capture_output=True, text=True, timeout=60,
                           cwd=str(Path(__file__).resolve().parents[2]))
        out = (r.stdout or '').strip().splitlines()
        if r.returncode:
            raise RuntimeError(((r.stderr or '').strip().splitlines() or ['failed'])[-1])
        return out[-1] if out else 'stopped'

    def _hooks(self, rec):
        try:
            hooks = json.loads((HOME / 'hooks.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            return
        env = {**os.environ, 'PHONE_EVENT': rec.get('kind', ''), 'PHONE_TEXT': rec.get('text') or rec.get('what') or '',
               'PHONE_INBOX': str(self.routes()[-1]), 'PHONE_LINE': json.dumps(rec, ensure_ascii=False)}
        for cmd in (hooks.get(rec.get('kind')) or []) + (hooks.get('any') or []):
            try:
                with open(HOME / 'hooks.log', 'a', encoding='utf8') as log:
                    subprocess.Popen(cmd, shell=True, env=env, stdout=log, stderr=log,
                                     creationflags=0x08000000 if os.name == 'nt' else 0)
            except OSError as e:
                print(f'[phone] hook {cmd!r}: {e}', flush=True)

    def read_inbox(self, since):
        out = []
        try:
            with open(HOME / 'inbox.jsonl', encoding='utf8') as f:
                for ln in f:
                    try:
                        r = json.loads(ln)
                    except ValueError:
                        continue
                    if r.get('n', 0) > since:
                        out.append(r)
        except OSError:
            pass
        return out

    # ---- the page's command queue
    def cmd(self, type_, **kw):
        with self.cond:
            self.cmd_id += 1
            c = {**kw, 'id': self.cmd_id, 'type': type_, 'ts': now_iso()}
            self.cmds.append(c)
            del self.cmds[:-200]
            self.cond.notify_all()
            return c

    def save(self):
        try:
            st = {k: self.view.get(k) for k in ('now', 'next', 'rec_why', 'mood', 'buttons', 'pinned', 'marks',
                                                'panels', 'vibe', 'shape', 'sounds', 'vibe_moves', 'scenes')}
            st['piece'] = list(self.piece)
            st['route'] = str(self.route) if self.route else None
            st['files'] = {k: str(v) for k, v in self.files.items()}
            st['answer_files'] = dict(list(self.answer_files.items())[-300:])
            (HOME / 'state.json').write_text(json.dumps(st), encoding='utf8')
        except OSError:
            pass

    # ---- the engine
    def _poll_engine(self):
        while True:
            eng = live_engines()
            port = max(eng, key=lambda p: eng[p].get('started', 0), default=None)
            got = None
            if port is not None:
                try:
                    got = parse_status(engine_call(port, 'status'))
                    if got:
                        got.update(port=port, project=eng[port].get('project'), at=time.time())
                except Exception:
                    got = None
                if got and (self.view.get('vibe') or {}).get('react'):
                    try:                                  # only while the look reacts to the notes
                        self.onsets = engine_call(port, 'onsets').get('onsets') or []
                    except Exception:
                        pass
            self.engine = got
            if got and got.get('playing') and KEEP_MASTER:  # the music under a note heard from the room speaker too
                with self.cond:
                    if self.feeder is None or not self.feeder.is_alive():
                        self.feeder = threading.Thread(target=self._feed, daemon=True)
                        self.feeder.start()
            time.sleep(1.0)

    def _want_feed(self):
        e = self.engine
        return bool(self.encoders) or (KEEP_MASTER and bool(e and e.get('playing')))

    def beat_now(self):
        e = self.engine
        if not e or time.time() - e['at'] > 5:
            return None
        return e['beat'] + (time.time() - e['at'] + e['ahead']) * e['bpm'] / 60

    # ---- audio
    def encoder(self, kbps):
        kbps = min(KBPS, key=lambda k: abs(k - int(kbps)))
        with self.cond:
            enc = self.encoders.get(kbps)
            if enc is None or enc.proc is None:
                if not ffmpeg():
                    raise RuntimeError('ffmpeg is not on PATH (or $ISMAIL_FFMPEG): the phone stream needs it')
                enc = self.encoders[kbps] = Encoder(kbps, self.fed / SR)
            if self.feeder is None or not self.feeder.is_alive():
                self.feeder = threading.Thread(target=self._feed, daemon=True)
                self.feeder.start()
            return enc

    def _source(self, q):
        """Pump the engine's master into q while an engine plays; reconnect when it changes."""
        import http.client
        while self._want_feed():
            e = self.engine
            if not e:
                time.sleep(1.0)
                continue
            c = http.client.HTTPConnection('127.0.0.1', e['port'], timeout=10)
            try:
                c.request('GET', '/stream?name=master')
                r = c.getresponse()
                if r.status != 200:
                    raise OSError(f'engine stream {r.status}')
                print(f'[phone] master from :{e["port"]}', flush=True)
                while self._want_feed():
                    b = r.read1(16384)
                    if not b:
                        break
                    q.append(b)
            except (OSError, http.client.HTTPException) as ex:
                print(f'[phone] engine stream: {ex}', flush=True)
                time.sleep(2.0)
            finally:
                c.close()

    def _feed(self):
        q = collections.deque()
        threading.Thread(target=self._source, args=(q,), daemon=True).start()
        t0, start, buf = time.time(), self.fed, b''
        while True:
            with self.cond:
                for k, enc in list(self.encoders.items()):
                    if enc.proc is None or (enc.listeners == 0 and time.time() - enc.last > IDLE_S):
                        enc.close()
                        del self.encoders[k]
                if not self._want_feed():
                    self.feeder = None
                    return
            while q:
                buf += q.popleft()
            due = int((time.time() - t0) * SR) - (self.fed - start)
            if len(buf) >= 4:
                n = len(buf) // 4
                block, buf = buf[:n * 4], buf[n * 4:]
                live = True
            elif due > BLOCK + int(0.15 * SR):
                n, block, live = BLOCK, bytes(BLOCK * 4), False
            else:
                time.sleep(0.01)
                continue
            if self.fed - start - int((time.time() - t0) * SR) > 3 * SR:
                continue                                       # a burst after a gap: never run seconds ahead
            block = self._mix(block, n)
            e = self.engine
            beat = self.beat_now() if live else None
            with self.cond:
                self.timeline.append((self.fed / SR, time.time(), beat, e['bpm'] if e else None,
                                      e['bpb'] if e else None, e['ahead'] if e else 0.0))
                while self.timeline and self.timeline[0][0] < self.fed / SR - RING_S - 30:
                    self.timeline.popleft()
                self.master.append((self.fed / SR, block))     # what the page plays, overlay and all (M163)
                while self.master and self.master[0][0] < self.fed / SR - MASTER_S:
                    self.master.popleft()
                self.fed += n
                encs = list(self.encoders.values())
            for enc in encs:
                enc.write(block)

    def _mix(self, block, n):
        if not self.overlay:
            return block
        x = np.frombuffer(block, dtype='<i2').astype(np.float32).reshape(-1, 2) / 32768.0
        v, take = np.zeros_like(x), 0
        while self.overlay and take < n:
            seg = self.overlay[0]
            k = min(n - take, len(seg))
            v[take:take + k] = seg[:k]
            take += k
            if k == len(seg):
                self.overlay.popleft()
            else:
                self.overlay[0] = seg[k:]
        y = np.clip(x * DUCK + v, -1.0, 1.0)
        return (y * 32767).astype('<i2').tobytes()

    def _tts(self, text, voice=None):
        req = urllib.request.Request(SPEAK + '/v1/audio/speech', method='POST',
                                     headers={'Content-Type': 'application/json'},
                                     data=json.dumps({'input': text, 'voice': voice or 'af_heart'}).encode())
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def speak_clip(self, text, voice=None):
        """The words as a clip the open page plays itself, for when nobody listens to the stream (2026-10-06: the
        page reloaded on a walk, the stream stayed off and a spoken answer was dropped). -> (url, seconds)."""
        wav = self._tts(text, voice)
        import soundfile as sf
        info = sf.info(io.BytesIO(wav))
        d = HOME / 'said'
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}.wav"
        p.write_bytes(wav)
        return '/files/' + self.offer_file(str(p)), info.frames / info.samplerate

    def speak(self, text, voice=None):
        wav = self._tts(text, voice)
        import soundfile as sf
        y, sr = sf.read(io.BytesIO(wav), dtype='float32', always_2d=True)
        y = y.mean(axis=1)
        if sr != SR:
            t = np.arange(int(len(y) * SR / sr)) * sr / SR
            y = np.interp(t, np.arange(len(y)), y).astype(np.float32)
        pad = np.zeros(int(0.25 * SR), dtype=np.float32)
        y = np.concatenate([pad, y * 0.9, pad])
        self.overlay.append(np.stack([y, y], axis=1))
        return len(y) / SR

    # ---- what the person heard
    def heard(self, sid, t):
        s = self.sids.get(sid)
        try:
            t = float(t)
        except (TypeError, ValueError):
            return {}
        if not s:
            return {}
        s['t'], s['at'] = t, time.time()
        pcm = s['pcm0'] + t
        with self.cond:
            tl = list(self.timeline)
        if not tl:
            return {}
        i = max(0, bisect.bisect_right([x[0] for x in tl], pcm) - 1)
        p, wall, beat, bpm, bpb, ahead = tl[i]
        wall_at = wall + (pcm - p)
        out = {'behind_s': round(max(0.0, time.time() - wall_at - (ahead or 0.0)), 1)}
        if beat is not None and bpm:
            b = beat + (pcm - p) * bpm / 60
            out.update(beat=round(b, 2), bar=int(b // bpb) + 1, of=bar_text(b, bpb))
        return out

    # ---- voice notes
    def add_voice(self, data, mime, sid, t, extra=None):
        vid = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_') + secrets.token_hex(2)
        ext = 'webm' if 'webm' in (mime or '') else 'ogg' if 'ogg' in (mime or '') else 'm4a' if 'mp4' in (mime or '') else             'wav' if 'wav' in (mime or '') else 'bin'
        f = HOME / 'voice' / f'{vid}.{ext}'
        f.write_bytes(data)
        heard = self.heard(sid, t)
        extra = dict(extra or {})
        ago = extra.pop('_ago', None)
        meta = {'heard': heard, 'dur_s': extra.get('dur_s'), 'mic': extra.get('mic')}
        try:
            meta.update(self._save_ref(vid, sid, t, meta['dur_s'], ago))
        except Exception as e:                               # never lose the note over its reference
            meta['ref_why'] = f'saving it failed ({type(e).__name__}: {e})'
        (HOME / 'voice' / f'{vid}_meta.json').write_text(json.dumps(meta), encoding='utf8')
        rec = self.post({'kind': 'voice', 'id': vid, 'file': str(f), 'state': 'transcribing', 'sid': sid,
                         'heard': heard, **({'ref': meta['ref']} if meta.get('ref') else {}), **(extra or {})})
        self.voice_state[vid] = 'queued'
        if extra.get('panel') or extra.get('media') or extra.get('acts'):
            self.voice_panel[vid] = {k: extra[k] for k in ('panel', 'for', 'field', 'media', 'acts') if k in extra}
        self.voice_q.append((vid, f, rec['heard'], sid))
        return rec

    def _save_ref(self, vid, sid, t, dur, ago=None):
        """The master the page played under a voice note, from REF_LEAD_S before it began to REF_TAIL_S after it
        ended, as <id>_ref.wav beside it, with the beat at points through it (<id>_ref.json). The music bleeding
        into the mic lines the note up with it (ledger:M163). -> meta keys."""
        s = self.sids.get(sid)
        try:
            t = float(t)
        except (TypeError, ValueError):
            t = None
        guess = not s or t is None
        if guess:                                # off the stream (the room speaker): from when the note arrived
            with self.cond:
                tl = list(self.timeline)
            if not tl:
                return {'ref_why': 'no engine was playing (the server keeps the master only while one plays)'}
            began = time.time() - float(dur or 0) - (ago if ago is not None else 1.0)   # the page says how long ago
            p, wall = min(tl, key=lambda x: abs(x[1] - began))[:2]
            start = p + (began - wall)
            lead, tail = REF_GUESS_S, REF_GUESS_S
        else:
            start, lead, tail = s['pcm0'] + t, REF_LEAD_S, REF_TAIL_S
        a = start - lead
        with self.cond:
            blocks = [(p, b) for p, b in self.master if p + len(b) / 4 / SR > a]
            tl = [x for x in self.timeline if x[0] >= a - 1]
            end = self.fed / SR
        if dur:
            end = min(end, start + float(dur) + tail)
        if not blocks or (not guess and blocks[0][0] > a + 1.0):
            return {'ref_why': 'the stream had not been playing long enough before the note'}
        a = max(a, blocks[0][0])
        x = np.frombuffer(b''.join(b for _, b in blocks), dtype='<i2').reshape(-1, 2)
        i0 = int(round((a - blocks[0][0]) * SR))
        x = x[i0:i0 + int((end - a) * SR)]
        import soundfile as sf
        ref = HOME / 'voice' / f'{vid}_ref.wav'
        sf.write(str(ref), x, SR, subtype='PCM_16')
        beats, bpm, bpb, last = [], None, None, -9.0
        for p, _, beat, bp, bb, _ in tl:
            if beat is not None and a <= p <= end and p - last >= 0.5:
                beats.append([round(p - a, 3), round(beat, 3)])
                bpm, bpb, last = bp or bpm, bb or bpb, p
        bj = HOME / 'voice' / f'{vid}_ref.json'
        bj.write_text(json.dumps({'sr': SR, 'note_starts_at_s': round(start - a, 3), 'bpm': bpm, 'bpb': bpb,
                                  'beats': beats, 'start_is_guess': guess,
                                  'search_s': REF_GUESS_S if guess else REF_LEAD_S}), encoding='utf8')
        return {'ref': str(ref), 'ref_beats': str(bj), 'ref_lead_s': round(start - a, 2),
                'ref_s': round(len(x) / SR, 2), 'start_is_guess': guess}

    def hum_check(self, vid, f=None, text=None):
        """The hum check on one voice note (hum.check), kept beside it as <id>_hum.json. -> (meta, check)."""
        from . import hum
        d = HOME / 'voice'
        if f is None:
            f = next((p for p in sorted(d.glob(f'{vid}.*')) if p.suffix != '.json'), None)
            if f is None:
                raise ValueError(f"no voice note {vid!r} in {d}")
        try:
            meta = json.loads((d / f'{vid}_meta.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            meta = {}
        try:
            chk = json.loads((d / f'{vid}_hum.json').read_text(encoding='utf8'))
        except (OSError, ValueError):
            if text is None:
                text = next((r.get('text') for r in self._voice_texts() if r.get('id') == vid), '')
            if not ffmpeg():
                raise ValueError('ffmpeg is needed to read a voice note (set ISMAIL_FFMPEG)')
            chk = hum.check(hum.decode(f, ffmpeg()), text)
            try:                                      # capture faults, at full rate (ledger:M172)
                from .. import capture
                cap = capture.check(hum.decode(f, ffmpeg(), sr=44100), 44100)
                chk['capture'] = cap['warnings']
                chk['air_db'] = cap['air_db']
            except Exception as e:
                chk['capture_why'] = f'{type(e).__name__}: {e}'
            (d / f'{vid}_hum.json').write_text(json.dumps(chk), encoding='utf8')
        self.hums[vid] = chk
        return meta, chk

    def note_to_answers(self, vid, f, text, vp):
        """A voice note said on an exam card goes into that exam's answers file as a kind 'voice_note' row, before or
        after the answer, so a reader of that one file misses nothing (the inbox still gets voice_text)."""
        pid = vp.get('panel')
        ap = self.answer_files.get(pid) if pid else None
        if not ap:
            return
        try:
            with open(ap, 'a', encoding='utf8') as fh:
                fh.write(json.dumps({'ts': now_iso(), 'exam': pid, 'kind': 'voice_note', 'id': vid, 'text': text,
                                     'audio_path': str(f), 'field': vp.get('field') or 'card',
                                     **{k: vp[k] for k in ('media', 'acts') if vp.get(k)}},
                                    ensure_ascii=False) + '\n')
        except OSError as e:
            print(f'[phone] voice note {vid} to {ap}: {e}', flush=True)

    def voice_pending(self, pid):
        """Voice notes said on panel pid that are still being transcribed."""
        return [q[0] for q in self.voice_q if self.voice_panel.get(q[0], {}).get('panel') == pid]

    def voice_files(self, vids):
        """The audio files of these voice notes."""
        out = []
        for v in vids or []:
            v = str(v)
            if not re.fullmatch(r'[\w-]+', v):
                continue
            out += [str(p) for p in sorted((HOME / 'voice').glob(f'{v}.*')) if p.suffix not in ('.json', '.wav')]
        return out

    def _voice_texts(self):
        try:
            with open(HOME / 'inbox.jsonl', 'rb') as fh:
                fh.seek(max(0, os.path.getsize(fh.name) - 400_000))
                lines = fh.read().decode('utf8', 'replace').splitlines()[1:]
        except OSError:
            return []
        out = []
        for l in lines:
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if r.get('kind') == 'voice_text':
                out.append(r)
        return out

    def _transcriber(self):
        while True:
            if not self.voice_q:
                time.sleep(0.3)
                continue
            vid, f, heard, sid = self.voice_q[0]
            why = self._cpu_busy()
            if why:
                self.voice_state[vid] = 'waiting: ' + why
                time.sleep(10)
                continue
            try:
                text = stt(f.read_bytes(), f.name)
            except Exception as e:
                self.voice_state[vid] = f'waiting: the speech server does not answer ({type(e).__name__})'
                time.sleep(20)
                continue
            self.voice_q.popleft()
            self.voice_state.pop(vid, None)
            vp = self.voice_panel.pop(vid, {})
            self.post({'kind': 'voice_text', 'id': vid, 'text': text, **vp})
            self.note_to_answers(vid, f, text, vp)
            self.cmd('heard', ref=vid, text=text)
            if self.voice_command(text, vid, heard, sid) is None and ffmpeg():
                try:                                         # a hum tells itself apart: no button (ledger:M163)
                    meta, chk = self.hum_check(vid, f, text)
                    if chk.get('is_hum'):
                        self.post({'kind': 'hum', 'id': vid, 'file': str(f), 'heard': heard, 'ref': meta.get('ref'),
                                   'ref_beats': meta.get('ref_beats'), 'ref_lead_s': meta.get('ref_lead_s'),
                                   **{k: chk[k] for k in ('hz', 'note', 'voiced', 'steady', 'dur_s')},
                                   **({'capture': chk['capture']} if chk.get('capture') else {}),
                                   '_age': chk.get('dur_s') or 0})
                except Exception as e:
                    print(f'[phone] hum check {vid}: {type(e).__name__}: {e}', flush=True)

    def voice_command(self, text, vid=None, heard=None, sid=None):
        """A note of at most six words that is a command (VOICE_CMDS) acts as one. -> the act or None."""
        words = re.sub(r'[^\w\s]', ' ', (text or '').lower()).split()
        if not words or len(words) > 6:
            return None
        said = ' '.join(w for w in words if w not in ('please', 'okay', 'ok', 'hey', 'dj', 'now', 'just'))
        for pat, act in VOICE_CMDS:
            if re.fullmatch(pat, said):
                if act == 'stop_listening':
                    self.cmd('stop_listening', ref=vid)
                    self.post({'kind': 'control', 'what': act, 'via': 'voice', 'id': vid, 'sid': sid})
                else:
                    self.post({'kind': 'tap', 'what': act, 'means': TAPS[act], 'via': 'voice', 'id': vid, 'sid': sid,
                               'heard': heard or {}})
                return act
        return None

    def _cpu_busy(self):
        try:
            from .. import machine
            busy, _ = machine.cpu_load()
            return f'the CPU is {busy:.0f}% busy' if busy >= machine.CPU_BUSY else ''
        except Exception:
            return ''

    # ---- files the agents offer
    def offer_file(self, path):
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            raise ValueError(f'no file {path}')
        for k, v in self.files.items():
            if v == p:
                return k
        k = secrets.token_urlsafe(9)
        self.files[k] = p
        self.save()
        return k

    # ---- state for the page
    def state(self, sid=None, t=None):
        e = self.engine
        now = time.time()
        agents = sorted(w for w, at in self.agents.items() if now - at < 90)
        rec = {'on': bool(e and e.get('recording')), 'file': e.get('recording') if e else None,
               'why': self.view['rec_why']}
        now_t, next_t = self.now_text(), self.view['next'] or (e['next'] if e else None)
        today = time.strftime('%Y-%m-%d')
        tally = collections.Counter(x['what'] for x in self.taps if (x['ts'] or '').startswith(today))
        moods = [x for x in self.taps if x['what'] == 'mood']
        return {'engine': {'playing': bool(e), 'bpm': e['bpm'] if e else None, 'bpb': (e.get('bpb') or 4) if e else None,
                           'now': now_t, 'next': next_t,
                           'output': e.get('output') if e else None, 'follow': bool(e and e.get('follow')),
                           'now_mark': self.mark(now_t), 'next_mark': self.mark(next_t)},
                'taps': list(self.taps)[-8:][::-1], 'tally': dict(tally),
                'asked_mood': moods[-1] if moods else None,
                'rec': rec, 'mood': self.view['mood'], 'captions': self.view['captions'][-6:],
                'pinned': self.view['pinned'], 'buttons': self.view['buttons'], 'panels': self.view['panels'],
                'offers': self.view['offers'][-4:], 'listening': agents,
                'voice': [{'id': k, 'state': v} for k, v in self.voice_state.items()],
                'heard': self.heard(sid, t) if sid else {}, 'cmd': self.cmd_id,
                'vibe': self.vibe_now(), 'vibe_moves': self.view.get('vibe_moves') or [], 'room': self.room(),
                'onsets': self.onsets if (self.view.get('vibe') or {}).get('react') else [],
                'sounds': self.view.get('sounds') or {}, 'into_s': self.into_s(), 'clock': time.strftime('%H:%M:%S'),
                'shape': self.view.get('shape') or {},
                'boot': BOOT, 'build': BUILD}


def stt(audio, filename):
    b = '----phone' + str(int(time.time() * 1000))
    body = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + audio + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request(SPEAK + '/v1/audio/transcriptions', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={b}'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return (json.loads(r.read()).get('text') or '').strip()


# ------------------------------------------------------------------ what agents can do (the phone_* ops call these)

class Agent:
    def __init__(self, ph):
        self.ph = ph

    def run(self, op, args):
        fn = getattr(self, 'op_' + op, None)
        if fn is None:
            raise ValueError(f"no phone op {op!r}")
        who = args.pop('sender', None) or args.get('who')
        if who:
            self.ph.agents[who] = time.time()
        return fn(**args)

    def op_status(self):
        ph, e = self.ph, self.ph.engine
        L = [f"phone server: {sum(x.listeners for x in ph.encoders.values())} listening"
             + (f" ({', '.join(f'{k} kbps: {v.listeners}' for k, v in ph.encoders.items())})" if ph.encoders else '')]
        for sid, s in list(ph.sids.items())[-4:]:
            if time.time() - s.get('at', 0) < 60:
                h = ph.heard(sid, s.get('t'))
                L.append(f"  a phone hears {h.get('of', 'a gap')}, {h.get('behind_s', '?')} s behind the room")
        ago = time.time() - ph.page_seen
        listening = sum(x.listeners for x in ph.encoders.values())
        L.append('page: ' + ('never opened since the server started' if not ph.page_seen else
                             f"open (seen {ago:.0f} s ago)" + ('' if listening else
                                                              ', NOT listening to the stream: it asks to be resumed; a '
                                                              'spoken phone_say goes to the page as a clip')
                             if ago < PAGE_OPEN_S else f"closed or asleep (last seen {ago / 60:.0f} min ago)"))
        L.append(f"engine: {'playing, ' + str(e['bpm']) + ' BPM, recording ' + str(e['recording']) if e else 'none playing'}")
        L.append(f"inbox: {ph.seq} lines; routed to {', '.join(str(p) for p in ph.routes())}")
        if ph.voice_state:
            L.append('voice notes waiting: ' + '; '.join(f"{k} ({v})" for k, v in ph.voice_state.items()))
        L.append(f"view: now={ph.view['now']!r} next={ph.view['next']!r} mood={ph.view['mood']!r} "
                 f"buttons={[b['id'] for b in ph.view['buttons']]} panels={[p['id'] for p in ph.view['panels']]}")
        L.append('open panels: ' + ('; '.join(f"{p['id']} (for {p.get('who') or 'nobody'}, priority "
                                              f"{p.get('priority') or 'normal'}, kind {p.get('kind')})"
                                              for p in ph.view['panels']) or 'none'))
        utc = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%H:%M:%S UTC')
        sh = ph.showing
        L.append(f"page shows: panel {sh['id']} since {utc(sh['since'])}" if sh else 'page shows: no panel')
        if ph.rounds:
            L.append('exam in progress: ' + '; '.join(f"{k} for {v['for'] or 'nobody'} since {utc(v['since'])}"
                                                      for k, v in ph.rounds.items()))
        else:
            L.append('exam in progress: none')
        L.append(f"last inbox line: {ph.seq} (pass it as `since` to phone_listen)")
        return '\n'.join(L)

    def op_route(self, inbox=None):
        self.ph.route = inbox or None
        self.ph.save()
        return f"inbox routed to {', '.join(str(p) for p in self.ph.routes())}"

    def op_say(self, text, speak=False, pin=False, buzz=False, voice=None, who=None, priority=None):
        ph = self.ph
        cap = {'text': text, 'ts': now_iso(), 'who': who, 'priority': norm_priority(priority)}
        ph.view['captions'] = (ph.view['captions'] + [cap])[-20:]
        if pin:
            ph.view['pinned'] = cap
            ph.save()
        ph.cmd('caption', text=text, who=who, buzz=bool(buzz))
        out = f"shown on the phone: {text}"
        if speak:
            if not ph.encoders:
                ago = time.time() - ph.page_seen
                if ago > PAGE_OPEN_S:
                    return out + (" (not spoken: nobody is listening to the stream and no page is open"
                                  + (f", last seen {ago / 60:.0f} min ago" if ph.page_seen else '') +
                                  "; the caption waits on the page)")
                try:
                    url, s = ph.speak_clip(text, voice)
                except Exception as e:
                    return out + f" (not spoken: the speech server does not answer: {e})"
                ph.cmd('say_clip', url=url, text=text, who=who)
                return out + (f" (nobody is listening to the stream, so it went to the open page as a spoken clip, "
                              f"{s:.1f} s: it plays if the phone lets the page make sound; the caption shows either "
                              f"way)")
            try:
                s = ph.speak(text, voice)
                out += f" (and spoken into the stream, {s:.1f} s, the music ducked under it)"
            except Exception as e:
                out += f" (not spoken: the speech server does not answer: {e})"
        return out

    def op_now(self, now=None, next=None, recording_why=None, mood=None, now_mark=None, next_mark=None, length=None,
               sections=None, into=None, who=None):
        v, ph = self.ph.view, self.ph
        for k, val in (('now', now), ('next', next), ('rec_why', recording_why), ('mood', mood)):
            if val is not None:
                v[k] = val or None
        for text, m in ((ph.now_text(), now_mark), (v['next'], next_mark)):
            if m is None or not text:
                continue
            if m and m not in MARKS:
                raise ValueError(f"now_mark/next_mark: one of {MARKS}, or '' to clear")
            if m:
                v['marks'][text] = m
            else:
                v['marks'].pop(text, None)
        while len(v['marks']) > 200:
            v['marks'].pop(next(iter(v['marks'])))
        t = ph.now_text()
        if into is not None and t:                  # the piece did not start when its name went up: say where it is
            ph.piece = (t, time.time() - max(0.0, float(into)))
        if length is not None or sections is not None:
            sh = v['shape'] if v.get('shape', {}).get('of') == t else {'of': t}
            if length is not None:
                sh['length_s'] = round(max(0.0, float(length)), 1)
            if sections is not None:
                sh['sections'] = sorted(({'at_s': round(float(s['at_s']), 1), 'label': str(s.get('label') or s.get('name') or '')[:40]}
                                         for s in sections), key=lambda s: s['at_s'])[:24]
            v['shape'] = sh
        ph.save()
        ph.cmd('view')
        return (f"now={v['now']!r} ({ph.mark(ph.now_text()) or 'no mark'}) next={v['next']!r} "
                f"({ph.mark(v['next']) or 'no mark'}) recording_why={v['rec_why']!r} mood={v['mood']!r}")

    def _wait_answer(self, pid, wait):
        if not wait:
            return None
        end = time.time() + float(wait)
        with self.ph.cond:
            while time.time() < end:
                for r in self.ph.read_inbox(max(0, self.ph.seq - 50)):
                    if r.get('id') == pid and r.get('kind') in ('answer', 'exam'):
                        return r
                self.ph.cond.wait(min(1.0, end - time.time()))
        return None

    def _panel(self, p, wait):
        v = self.ph.view
        ahead = [x['id'] for x in v['panels'] if x['id'] != p['id']]
        v['panels'] = [x for x in v['panels'] if x['id'] != p['id']] + [p]
        self.ph.save()
        self.ph.cmd('panel', panel=p)
        got = self._wait_answer(p['id'], wait)
        if got and got.get('dismissed'):
            return f"set aside: they tapped Not now on {p['id']} (not an answer; ask again later or let it go)"
        if got:
            return f"answered: {json.dumps(got.get('answers') or got.get('answer'), ensure_ascii=False)} (heard {got.get('heard', {}).get('of', '?')})"
        queued = (f"; {len(ahead)} other message{'s are' if len(ahead) > 1 else ' is'} open there too "
                  f"({', '.join(ahead)})" if ahead else '')
        return (f"shown: panel {p['id']}, as a message on the phone's corner key: it never pops up, he opens it when "
                f"he chooses{queued}. Send a panel only for something to decide; say the rest in a caption"
                + (" (no answer yet; it arrives in the inbox as kind 'answer')" if wait else
                   "; the answer arrives in the inbox as kind 'answer'"))

    def op_panel_show(self, panel_id=None, title='', text='', image=None, buttons=None, inputs=None, wait=0,
                      who=None, video=None, priority=None, link=None, link_label='Open'):
        p = {'id': panel_id or 'p' + secrets.token_hex(3), 'kind': 'panel', 'title': title, 'text': text,
             'priority': norm_priority(priority),
             'buttons': [b if isinstance(b, str) else str(b) for b in (buttons or ['Send' if inputs else 'OK'])],
             'who': who}
        if inputs:
            p['inputs'] = panel_inputs(inputs)
        if image:
            p['image'] = '/files/' + self.ph.offer_file(image)
        if video:
            p['video'] = '/files/' + self.ph.offer_file(video)
        if link is not None:
            p['link'] = panel_link(link)
            p['link_label'] = str(link_label or 'Open')[:60]
        return self._panel(p, wait)

    def op_ask(self, text, wait=0, who=None):
        return self._panel({'id': 'q' + secrets.token_hex(3), 'kind': 'ask', 'title': text, 'text': '',
                            'buttons': ['yes', 'no'], 'who': who}, wait)

    def op_panel_close(self, panel_id):
        v = self.ph.view
        had = any(x['id'] == panel_id for x in v['panels'])
        v['panels'] = [x for x in v['panels'] if x['id'] != panel_id]
        self.ph.save()
        self.ph.panel_gone(panel_id)
        self.ph.cmd('close', ref=panel_id)
        return f"closed {panel_id}" if had else f"no open panel {panel_id}"

    def op_exam(self, title, clips, question='', chips=None, choices=None, answers_path=None, exam_id=None,
                wait=0, who=None):
        if not clips:
            raise ValueError("clips: [{'label': 'A', 'path': '<file>'}, ...]")
        cl = []
        for i, c in enumerate(clips):
            c = c if isinstance(c, dict) else {'path': c}
            cl.append({'label': c.get('label') or chr(65 + i), 'url': '/files/' + self.ph.offer_file(c['path']),
                       'note': c.get('note', '')})
        p = {'id': exam_id or 'x' + secrets.token_hex(3), 'kind': 'exam', 'title': title, 'text': question,
             'clips': cl, 'chips': list(chips or []), 'choices': list(choices or []), 'answers_path': answers_path,
             'who': who}
        if answers_path:
            self.ph.answer_files[p['id']] = str(Path(answers_path).expanduser().resolve())
            self.ph.save()
        out = self._panel(p, wait)
        man = []
        for c, src in zip(cl, clips):
            path = str(Path((src if isinstance(src, dict) else {'path': src})['path']).expanduser().resolve())
            sec = clip_seconds(path)
            man.append(f"  {c['label']}: {c['url']}  {path}" + (f"  {sec:.1f} s" if sec else ''))
        where = (f"everything lands in {answers_path}: the answer (kind 'answer'), and every voice note said on the "
                 f"card (kind 'voice_note' with text, audio_path and field), before or after the answer; an answer "
                 f"row with voice_notes_pending has more rows coming" if answers_path else
                 "no answers_path: the answer and the card's voice notes arrive only in the inbox (kind 'exam', and "
                 "'voice_text' with panel=<id>); give answers_path to get them in one file")
        return out + '\nclips:\n' + '\n'.join(man) + '\n' + where

    def op_offer(self, path, label=None, auto=False, title=None, album=None, artist=None, who=None):
        src = Path(path).expanduser().resolve()
        if not src.is_file():
            raise ValueError(f'no file {path}')
        nice = readable_name(label or src.stem) + src.suffix.lower()
        if src.suffix.lower() == '.mp3':              # a tagged copy with a readable name; their file stays as it is
            d = HOME / 'offers'
            d.mkdir(parents=True, exist_ok=True)
            out = d / nice
            shutil.copy2(src, out)
            try:
                tags.tag_mp3(str(out), title=title or label or src.stem.replace('_', ' '), artist=artist, album=album)
            except Exception as e:                    # a broken mp3 still downloads; say the tags failed
                print(f'[phone] tags for {out.name}: {e}', flush=True)
            src = out
        k = self.ph.offer_file(str(src))
        o = {'url': '/files/' + k, 'name': src.name if src.parent == HOME / 'offers' else nice,
             'label': label or Path(path).name, 'auto': bool(auto), 'ts': now_iso()}
        self.ph.view['offers'].append(o)
        self.ph.cmd('offer', offer=o)
        return f"offered {o['name']} on the phone" + (" (starts downloading if the page is open)" if auto else '')

    def op_buttons(self, buttons=None, who=None):
        bs = []
        for b in buttons or []:
            b = b if isinstance(b, dict) else {'id': str(b), 'label': str(b)}
            bs.append({'id': str(b.get('id') or b.get('label')), 'label': str(b.get('label') or b.get('id'))})
        self.ph.view['buttons'] = bs
        self.ph.save()
        self.ph.cmd('view')
        return f"buttons on the phone: {[b['label'] for b in bs] or 'none'}; a tap arrives as kind 'button'"

    def op_vibe(self, preset=None, ground=None, ink=None, accent=None, heading=None, image=None, blur=None, dim=None,
                effect=None, intensity=None, transition_ms=None, reset=False, menu=False, layers=None,
                hue_drift=None, at=None, ramp_beats=None, save=None, scene=None, cancel_moves=False, react=None,
                who=None):
        ph = self.ph
        moves = ph.view.setdefault('vibe_moves', [])
        scenes = ph.view.setdefault('scenes', {})
        if menu:
            out = vibe.menu() + '\n' + vibe.describe(self.ph.vibe_now())
            if scenes:
                out += '\nscenes: ' + ', '.join(scenes)
            if moves:
                out += '\nscheduled: ' + '; '.join(f"bar {m['at_bar']}: {vibe.describe(m['vibe'])}" for m in moves)
            return out
        if cancel_moves:
            n = len(moves)
            ph.view['vibe_moves'] = moves = []
        if preset is not None and preset not in vibe.PRESETS:
            raise ValueError(f"preset {preset!r}: one of {sorted(vibe.PRESETS)} (or set the parts yourself)")
        if scene is not None and scene not in scenes:
            raise ValueError(f"scene {scene!r}: " + (f"saved ones: {', '.join(scenes)}" if scenes else
                                                     "none saved yet (phone_vibe(save='name') keeps the current look)"))
        at_bar = None
        if at not in (None, '', 'now'):
            m = re.fullmatch(r'(?:bar:?\s*)?(\d+)', str(at).strip().lower())
            if not m:
                raise ValueError(f"at {at!r}: 'bar:N' (the bar the phone hears it on), or leave it out for now")
            at_bar = int(m.group(1))
        # a move builds on the last scheduled look, so a list of moves reads like a score
        base = moves[-1]['vibe'] if moves and at_bar is not None else ph.vibe_now()
        cur = {} if reset else {k: v for k, v in base.items() if k in vibe.DEFAULT}
        if scene is not None:
            cur = {k: v for k, v in scenes[scene].items() if k in vibe.DEFAULT}
        if preset:
            cur.update({k: v for k, v in vibe.DEFAULT.items() if k not in ('image', 'blur', 'dim')})
            cur.update(vibe.PRESETS[preset])
        if (effect is not None or intensity is not None) and layers is None:
            cur['layers'] = None                       # the one-effect shortcut replaces the layers
        for k, v in (('ground', ground), ('ink', ink), ('accent', accent), ('heading', heading), ('blur', blur),
                     ('dim', dim), ('effect', effect), ('intensity', intensity), ('transition_ms', transition_ms),
                     ('layers', layers), ('hue_drift', hue_drift), ('react', react)):
            if v is not None:
                cur[k] = v
        if preset and layers is None and effect is None:
            cur['layers'] = None
        name = ph.view['vibe'].get('image_name')
        if image is not None:
            cur['image'] = '/files/' + ph.offer_file(image) if image else None
            name = Path(image).name if image else None
        v = vibe.resolve(cur)
        v['image_name'] = name if v.get('image') else None
        if save:
            scenes[str(save)] = v
        ramp = None if ramp_beats is None else round(max(0.0, min(64.0, float(ramp_beats))), 2)
        if at_bar is not None:
            moves.append({'at_bar': at_bar, 'vibe': v, 'ramp_beats': ramp})
            moves.sort(key=lambda m: m['at_bar'])
            ph.save()
            ph.cmd('vibe_moves', moves=moves)
            return (f"at bar {at_bar} (as the phone hears it)" + (f", over {ramp:g} beats" if ramp else '') + ": "
                    + vibe.describe(v) + (f" (saved as scene {save!r})" if save else '')
                    + f"; {len(moves)} scheduled")
        if ramp:
            v['ramp_beats'] = ramp
        ph.view['vibe'] = v
        ph.save()
        ph.cmd('vibe', vibe=v)
        if cancel_moves:
            ph.cmd('vibe_moves', moves=[])
        return (vibe.describe(v) + (f" (saved as scene {save!r})" if save else '') + " (on the page now, "
                + (f"over {ramp:g} beats" if ramp else f"fading over {v['transition_ms']} ms") + ")"
                + (f"; {n} scheduled moves cancelled" if cancel_moves and n else ''))

    def op_sounds(self, event=None, path=None, gain_db=0.0, menu=False, who=None):
        ph = self.ph
        snd = ph.view.setdefault('sounds', {})

        def one(k):
            if k not in snd:
                return '(none)'
            g = snd[k].get('gain_db') or 0
            return '-> ' + snd[k]['name'] + (f" ({g:+g} dB)" if g else '')

        def listing():
            return '\n'.join(f"  {k:<10} {one(k):<34} {v}" for k, v in SOUND_EVENTS.items())
        if menu or event is None:
            return 'the page\'s sounds (phone_sounds(event, path) attaches one; path="" clears it):\n' + listing()
        event = SOUND_ALIASES.get(event, event)
        if event not in SOUND_EVENTS:
            raise ValueError(f"event {event!r}: one of {', '.join(SOUND_EVENTS)}")
        if not path:
            snd.pop(event, None)
            ph.save()
            ph.cmd('sounds', sounds=snd)
            return f"{event}: cleared (" + ('the built-in tone' if event in ('note_start', 'note_end', 'note_sent', 'error')
                                           else 'silent') + ")"
        p = Path(path).expanduser()
        if not p.is_file():
            raise ValueError(f"no file {path}")
        if p.suffix.lower() not in SOUND_EXT:
            raise ValueError(f"{p.name}: a sound is {', '.join(SOUND_EXT)} (render it with mp3='also', or an ogg)")
        if p.stat().st_size > SOUND_MAX_BYTES:
            raise ValueError(f"{p.name} is {p.stat().st_size / 2 ** 20:.1f} MB: a page sound is at most 1 MB (a short "
                             f"mp3 or ogg)")
        try:
            import soundfile as sf
            dur = sf.info(str(p)).duration
        except Exception:
            dur = None
        if dur is not None and dur > SOUND_MAX_S:
            raise ValueError(f"{p.name} is {dur:.1f} s: a page sound is at most {SOUND_MAX_S:g} s (it plays over the "
                             f"set; trim it)")
        g = max(-30.0, min(6.0, float(gain_db or 0.0)))
        snd[event] = {'url': '/files/' + ph.offer_file(str(p)), 'name': p.name, 'gain_db': g}
        ph.save()
        ph.cmd('sounds', sounds=snd)
        return (f"{event}: {p.name}" + (f" ({dur:.2f} s)" if dur else '') + (f" at {g:+g} dB" if g else '') +
                " (the page plays it from now on; it never plays on its own, only on its event)")

    def op_unsay(self, match=None, since=None, n=None, who=None):
        """Take captions back off the page (Nate 10-06 15:03: a caption named where he lives while he recorded the
        screen): every caption whose text contains `match` (any of several, '|' between them, case ignored), every
        one since `since` ('HH:MM' today or an ISO time), or the last `n`. The pinned line too, when it matches."""
        ph = self.ph
        caps = ph.view['captions']
        if not (match or since or n):
            raise ValueError("say which: match='text' (or 'a|b'), since='HH:MM', or n=3 (the last three)")
        words = [w.strip().lower() for w in str(match).split('|') if w.strip()] if match else []
        if since:
            t = str(since).strip()
            if re.fullmatch(r'\d{1,2}:\d{2}', t):
                t = datetime.date.today().isoformat() + 'T' + t.zfill(5)
        gone = []
        for i, c in enumerate(caps):
            hit = (words and any(w in c['text'].lower() for w in words)) or (since and c['ts'] >= t) or \
                  (n and i >= len(caps) - int(n))
            if hit:
                gone.append(c)
        ph.view['captions'] = [c for c in caps if c not in gone]
        pin = ph.view.get('pinned')
        if pin and ((words and any(w in pin['text'].lower() for w in words)) or pin in gone):
            ph.view['pinned'] = None
            gone.append(pin)
        ph.save()
        ph.cmd('unsay', texts=[c['text'] for c in gone])
        return (f"took {len(gone)} line(s) off the page" + (": " + '; '.join(c['text'][:50] for c in gone[:5]) if gone
                                                            else " (none matched)")
                + ". A screenshot or recording already made keeps them; the page's notification is closed too")

    def op_restarting(self, back_in_s=5, why='updating'):
        """Tell the open page the server is about to restart: it says so, then reconnects the stream at once."""
        self.ph.cmd('restarting', back_in_s=float(back_in_s), why=str(why)[:80])
        n = sum(x.listeners for x in self.ph.encoders.values())
        return f"told the page ({n} on the stream)"

    def op_hum(self, voice_id=None, who=None):
        """Is a voice note a hum, and where is the music he heard under it (ledger:M163). Any note, the latest when
        voice_id is not given."""
        from . import hum
        if not voice_id:
            notes = sorted(p.stem for p in (HOME / 'voice').glob('*') if p.suffix != '.json'
                           and not p.stem.endswith('_ref'))
            if not notes:
                raise ValueError('no voice notes yet')
            voice_id = notes[-1]
        meta, chk = self.ph.hum_check(voice_id)
        return hum.describe(voice_id, meta, chk)

    def op_buzz(self, pattern=None, who=None):
        self.ph.cmd('buzz', pattern=pattern or [200, 100, 200])
        return 'buzzed (if the page is open)'

    def op_listen(self, who, since=None, wait=25, page=False):
        ph = self.ph
        ph.agents[who] = time.time()
        since = ph.seq if since is None else int(since)
        end = time.time() + float(wait or 0)
        keep = (lambda r: True) if page else (lambda r: r.get('kind') != 'page')
        rows = [r for r in ph.read_inbox(since) if keep(r)]
        while not rows and time.time() < end:          # the page's own actions (scrolls, opens) do not wake you
            seen = ph.seq
            with ph.cond:
                while ph.seq <= seen and time.time() < end:
                    ph.cond.wait(min(5.0, end - time.time()))
                    ph.agents[who] = time.time()
            rows = [r for r in ph.read_inbox(since) if keep(r)]
        return json.dumps({'since': ph.seq, 'lines': rows}, ensure_ascii=False)

    def op_timeline(self, minutes=15, kinds=None, limit=200):
        """Everything from the phone in the last `minutes` on one clock, oldest first, one line each: time, the bar
        in the room (and what the phone heard), the piece, then what happened. A take of the phone session."""
        ph = self.ph
        start = datetime.datetime.now() - datetime.timedelta(minutes=float(minutes))
        want = set(kinds.split(',') if isinstance(kinds, str) else kinds or [])
        rows = [r for r in ph.read_inbox(max(0, ph.seq - 5000))
                if (r.get('ts') or '') >= start.strftime('%Y-%m-%dT%H:%M:%S') and (not want or r.get('kind') in want)]
        L, piece = [], None
        for r in rows[-int(limit):]:
            k = r.get('kind')
            if r.get('now') and r['now'] != piece:
                piece = r['now']
                L.append(f"            -- {piece}")
            where = (r.get('room') or {}).get('of') or ''
            if r.get('into_s') is not None:
                where += f" {int(r['into_s'] // 60)}:{int(r['into_s'] % 60):02d} in"
            h = (r.get('heard') or {}).get('of')
            if h and h != where:
                where += f" (phone heard {h})"
            if k == 'page':
                skip = ('n', 'ts', 'kind', 'what', 'sid', 'heard', 'room', 'now')
                what = r['what'] + ''.join(f" {a}={v}" for a, v in r.items() if a not in skip)
            elif k == 'voice_text':
                what = f"said: \"{r.get('text', '')}\""
            elif k == 'voice':
                what = f"voice note {r.get('dur_s', '?')} s" + (f", ended by {r['ended_by']}" if r.get('ended_by') else '')
            elif k == 'tap':
                what = 'tap ' + str(r.get('what'))
            elif k == 'mood':
                what = 'mood ' + str(r.get('mood'))
            else:
                what = k + ' ' + json.dumps({a: v for a, v in r.items() if a in ('id', 'answer', 'label', 'state')},
                                            ensure_ascii=False)
            L.append(f"{(r.get('ts') or '')[11:19]}  {where:<22} {what}")
        return '\n'.join(L) if L else f"nothing from the phone in the last {minutes} min"


# ------------------------------------------------------------------ http

PRIORITIES = ('needs you', 'normal', 'low')


def norm_priority(x):
    """The sender's priority for a panel or note: 'needs you', 'normal' (the default) or 'low'."""
    if x is None or str(x).strip() == '':
        return 'normal'
    t = str(x).strip().lower().replace('_', ' ')
    if t not in PRIORITIES:
        raise ValueError(f"priority is one of {', '.join(repr(p) for p in PRIORITIES)}, not {x!r}")
    return t


class Handler(BaseHTTPRequestHandler):
    server_version = 'ismail-phone'
    ph: Phone = None
    agent: Agent = None
    TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css',
             '.json': 'application/json', '.webmanifest': 'application/manifest+json', '.svg': 'image/svg+xml',
             '.png': 'image/png', '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.ogg': 'audio/ogg',
             '.flac': 'audio/flac', '.mp4': 'video/mp4', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.pdf':
             'application/pdf', '.webm': 'video/webm', '.mov': 'video/quicktime', '.mid': 'audio/midi', '.zip': 'application/zip', '.txt': 'text/plain; charset=utf-8'}

    def log_message(self, fmt, *a):
        if '/api/state' not in (a[0] if a else ''):
            sys.stderr.write('[phone] ' + (fmt % a) + '\n')

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self, limit=25 * 1024 * 1024):
        n = int(self.headers.get('Content-Length') or 0)
        if n > limit:
            raise ValueError('too big')
        return self.rfile.read(n) if n else b''

    RANGE_CHUNK = 2 * 1024 * 1024

    def _range(self, size):
        """The (start, end) a single `Range: bytes=a-b` header asks for, None for no header or one that is not
        understood (the whole file is sent), or False when it lies past the end (416)."""
        m = re.fullmatch(r'bytes=(\d*)-(\d*)', (self.headers.get('Range') or '').strip())
        if not m or not (m.group(1) or m.group(2)):
            return None
        if m.group(1):
            a, b = int(m.group(1)), int(m.group(2) or size - 1)
        else:                                   # "-N": the last N bytes
            a, b = max(0, size - int(m.group(2))), size - 1
        b = min(b, size - 1)
        return (a, b) if a <= b else False

    def _file(self, p, download=False):
        """A file, with HTTP Range (206) so a phone browser can play and seek a video or a long clip."""
        try:
            size = p.stat().st_size
            rng = self._range(size)
            if rng is False:
                self.send_response(416)
                self.send_header('Content-Range', f'bytes */{size}')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            a, b = rng if rng else (0, size - 1)
            if rng:
                # a player asking for "the rest" gets RANGE_CHUNK at a time and asks again: on 4G the first frames
                # come at once instead of after one long 30 MB answer (Nate 10-07 15:39, a video "not loading")
                b = min(b, a + self.RANGE_CHUNK - 1)
            fh = open(p, 'rb')
            fh.seek(a)
        except OSError:
            return self._json(404, {'error': 'gone'})
        self.send_response(206 if rng else 200)
        self.send_header('Content-Type', self.TYPES.get(p.suffix.lower(), 'application/octet-stream'))
        self.send_header('Accept-Ranges', 'bytes')
        if rng:
            self.send_header('Content-Range', f'bytes {a}-{b}/{size}')
        self.send_header('Content-Length', str(b - a + 1 if size else 0))
        self.send_header('Cache-Control', 'no-cache')
        if download:
            name = next((o['name'] for o in self.ph.view['offers'] if o['url'].endswith('/' + self.path.split('/')[2]
                                                                                     .split('?')[0])), p.name)
            self.send_header('Content-Disposition', f'attachment; filename="{name}"')
        self.end_headers()
        left, sent = (b - a + 1 if size else 0), 0
        try:
            with fh:
                while left > 0:
                    buf = fh.read(min(256 * 1024, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    sent += len(buf)
                    left -= len(buf)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError) as e:
            if sent and size > self.RANGE_CHUNK:  # a player that seeks drops what it was reading; said, so a stall shows
                print(f"[phone] {p.name}: dropped after {sent} of {b - a + 1} bytes ({type(e).__name__})", flush=True)

    # The picture round (ismail/exampage) listens on 127.0.0.1 only. Its page and files are all under /eye/ (and /eye,
    # the list of rounds); nothing else it serves (/ and /health) is passed on, so no phone route is ever shadowed.
    EYE_PASS = ('content-type', 'content-length', 'content-range', 'accept-ranges')

    @staticmethod
    def is_eye(path):
        return path == '/eye' or path.startswith('/eye/')

    def _eye(self):
        """Forward this GET, HEAD or POST to the picture-round page server, streaming the answer back (the phone
        reaches the exam page through this server's https address). 503 when no round is open."""
        from .. import exampage
        up = None
        try:
            up = http.client.HTTPConnection('127.0.0.1', exampage.PORT, timeout=30)
            hdrs = {k: self.headers[k] for k in ('Content-Type', 'Range') if self.headers.get(k)}
            body = None
            if self.command == 'POST':
                body = self._body()
                hdrs['Content-Length'] = str(len(body))
            up.request(self.command, self.path, body=body, headers=hdrs)
            r = up.getresponse()
        except (OSError, http.client.HTTPException):
            if up:
                up.close()
            b = (b'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                 b'<title>Picture round</title><body style="font:18px system-ui;padding:24px">'
                 b'No picture round is open right now.</body>')
            self.send_response(503)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(b)
            return
        try:
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() in self.EYE_PASS:
                    self.send_header(k, v)
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    buf = r.read(256 * 1024)
                    if not buf:
                        break
                    self.wfile.write(buf)
        except (OSError, http.client.HTTPException):
            pass                                  # the phone dropped, or the page server did: nothing more to say
        finally:
            up.close()

    def do_HEAD(self):
        if self.is_eye(urllib.parse.urlparse(self.path).path):
            return self._eye()
        return self._json(404, {'error': 'no such page'})

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        g = lambda k, d=None: q.get(k, [d])[0]
        path = u.path
        if self.is_eye(path):
            return self._eye()
        if path in ('/', '/index.html'):
            return self._file(PAGE / 'index.html')
        if re.fullmatch(r'/cues/(start|end|sent|error)\.mp3', path):
            return self._file(PAGE / path[1:])
        if path in ('/app.js', '/sw.js', '/icon.svg', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png',
                    '/icon-maskable.png', '/apple-touch-icon.png'):
            return self._file(PAGE / path[1:])
        if path == '/health':
            return self._json(200, {'ok': True, 'engine': bool(self.ph.engine), 'inbox': self.ph.seq})
        if path == '/api/state':
            self.ph.page_seen = time.time()
            since, wait = int(g('since', '0') or 0), min(25.0, float(g('wait', '0') or 0))
            end = time.time() + wait
            with self.ph.cond:
                while self.ph.cmd_id <= since and time.time() < end:
                    self.ph.cond.wait(min(2.0, end - time.time()))
                cmds = [c for c in self.ph.cmds if c['id'] > since]
            return self._json(200, {**self.ph.state(g('sid'), g('t')), 'cmds': cmds})
        if path.startswith('/files/'):
            p = self.ph.files.get(path[7:])
            return self._file(p, download=g('dl') == '1') if p else self._json(404, {'error': 'not offered'})
        if path == '/stream.mp3':
            return self._stream(g('sid') or secrets.token_hex(4), g('kbps', '64'), float(g('back', '0') or 0))
        return self._json(404, {'error': 'no such page'})

    def _stream(self, sid, kbps, back):
        try:
            enc = self.ph.encoder(kbps)
        except RuntimeError as e:
            return self._json(503, {'error': str(e)})
        off = enc.start(back)
        self.ph.sids[sid] = {'pcm0': enc.pcm0 + off / enc.rate, 'kbps': enc.kbps, 't': 0.0, 'at': time.time()}
        while len(self.ph.sids) > 50:
            self.ph.sids.pop(next(iter(self.ph.sids)))
        self.send_response(200)
        self.send_header('Content-Type', 'audio/mpeg')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        with enc.cond:
            enc.listeners += 1
        try:
            while True:
                b, off = enc.read(off)
                if b is None:
                    break
                if b:
                    self.wfile.write(b)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            with enc.cond:
                enc.listeners -= 1
                enc.last = time.time()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        g = lambda k, d=None: q.get(k, [d])[0]
        ph = self.ph
        if self.is_eye(u.path):
            return self._eye()
        try:
            if u.path == '/agent':
                req = json.loads(self._body() or b'{}')
                return self._json(200, {'ok': True, 'result': self.agent.run(req['op'], dict(req.get('args') or {}))})
            if u.path == '/api/voice':
                data = self._body()
                if len(data) < 200:
                    return self._json(400, {'error': 'no audio'})
                extra = {}
                try:
                    if g('dur'):
                        extra['dur_s'] = round(float(g('dur')), 1)        # how long they talked
                        extra['_age'] = extra['dur_s']                    # the line is stamped when they started
                except ValueError:
                    pass
                if g('end') in ('press', 'quiet', 'max'):
                    extra['ended_by'] = g('end')                          # their press, 30 s of quiet, or 10 min
                pp = next((x for x in ph.view['panels'] if x['id'] == g('panel')), None) if g('panel') else None
                if pp:                                                    # said on a panel: a reply to whoever sent it
                    extra.update(panel=pp['id'], panel_title=pp.get('title') or '', **({'for': pp['who']}
                                                                                        if pp.get('who') else {}))
                if g('field') and pp:
                    extra['field'] = re.sub(r'[^\w .-]', '', g('field'))[:60] or 'card'   # the box it was said into
                if g('mic'):
                    extra['mic'] = g('mic')[:80]                          # route and processing: 'phone raw ec0 ns0 agc0'
                extra.update(note_context(g('ctx')))                      # what played, and what they did, while they spoke
                try:
                    if g('ago'):
                        extra['_ago'] = min(3600.0, max(0.0, float(g('ago'))))   # how long since it ended (an upload
                except ValueError:                                                # that waited offline included)
                    pass
                rec = ph.add_voice(data, self.headers.get('Content-Type'), g('sid'), g('t'), extra)
                return self._json(200, {'ok': True, 'id': rec['id'], 'heard': rec['heard']})
            body = json.loads(self._body() or b'{}')
            sid, t = body.get('sid'), body.get('t')
            if u.path == '/api/events':
                n = 0
                for ev in (body.get('events') or [])[:60]:
                    what = str(ev.get('what', ''))[:40]
                    if not re.fullmatch(r'[a-z_]+', what):
                        continue
                    fields = {k[:24]: (v[:300] if isinstance(v, str) else v) for k, v in ev.items()
                              if k not in ('what', 'age_ms', 't', 'kind', 'n', 'ts', 'sid', 'heard', 'room', 'now')
                              and isinstance(v, (str, int, float, bool)) and v is not None}
                    if what == 'open':
                        fields['ua'] = self.headers.get('User-Agent', '')[:300]
                    age = max(0.0, min(3600.0, float(ev.get('age_ms') or 0) / 1000))
                    ph.post({'kind': 'page', 'what': what, **fields, 'sid': sid, 'heard': ph.heard(sid, ev.get('t')),
                             '_age': age})
                    if what in ('panel_open', 'panel_close', 'panel_later'):
                        ph.page_panel_event(what, fields.get('id'))
                    n += 1
                return self._json(200, {'ok': True, 'n': n})
            if u.path == '/api/tap':
                what = str(body.get('what', ''))
                if what == 'mood':
                    m = body.get('mood')
                    if m not in MOODS:
                        return self._json(400, {'error': f'mood: one of {MOODS}'})
                    ph.view['mood'] = m
                    ph.save()
                    rec = ph.post({'kind': 'mood', 'mood': m, 'sid': sid, 'heard': ph.heard(sid, t),
                                   'now': ph.now_text()})
                elif what.startswith('button:'):
                    bid = what[7:]
                    lab = next((b['label'] for b in ph.view['buttons'] if b['id'] == bid), bid)
                    rec = ph.post({'kind': 'button', 'id': bid, 'label': lab, 'sid': sid, 'heard': ph.heard(sid, t)})
                elif what in TAPS:
                    rec = ph.post({'kind': 'tap', 'what': what, 'means': TAPS[what], 'sid': sid,
                                   'heard': ph.heard(sid, t), 'now': ph.now_text()})
                else:
                    return self._json(400, {'error': f'what: one of {sorted(TAPS)}, mood, button:<id>'})
                ph.cmd('view')
                return self._json(200, {'ok': True, 'n': rec['n'], 'heard': rec['heard']})
            if u.path == '/api/output':                       # ledger:M179: he sets it, no agent needed
                e = ph.engine
                if not e:
                    return self._json(409, {'error': 'no set is playing'})
                follow = bool(body.get('follow'))
                try:
                    if follow:                                     # onto the Windows default now, and follow it
                        msg = engine_call(e['port'], 'device', timeout=20, device='default', follow=True)
                    else:
                        try:                                       # stay where it is, no gap
                            msg = engine_call(e['port'], 'device', timeout=20, follow=False, reopen=False)
                        except Exception:                          # an engine from before reopen=: reopen it there
                            msg = engine_call(e['port'], 'device', timeout=20, device=e.get('output') or 'default',
                                              follow=False)
                except Exception as ex:
                    return self._json(502, {'ok': False, 'error': str(ex)})
                text = msg.get('result') if isinstance(msg, dict) else msg
                ph.post({'kind': 'output', 'follow': follow, 'result': text, 'sid': sid, 'heard': ph.heard(sid, t)})
                e['follow'] = follow
                ph.cmd('view')
                return self._json(200, {'ok': True, 'result': text})
            if u.path == '/api/answer':
                pid = str(body.get('id'))
                p = next((x for x in ph.view['panels'] if x['id'] == pid), None)
                if p is None:
                    return self._json(404, {'error': 'that question is closed'})
                rec = {'kind': 'exam' if p['kind'] == 'exam' else 'answer', 'id': pid, 'title': p['title'],
                       'sid': sid, 'heard': ph.heard(sid, t)}
                if body.get('dismissed'):                                 # Not now: set aside, not answered
                    rec['dismissed'] = True
                    rec['answer'] = None
                elif p['kind'] == 'exam':
                    rec['answers'] = body.get('answers') or {}
                    nv = rec['answers'].pop('note_voice', None)
                    if nv:                                                # the note was said (and maybe edited)
                        rec['answers']['note_audio'] = ph.voice_files(nv)
                        rec['answers']['note_source'] = 'voice'
                    elif rec['answers'].get('note'):
                        rec['answers']['note_source'] = 'typed'
                else:
                    rec['answer'] = body.get('answer')
                    if p.get('inputs'):
                        rec['values'] = body.get('values') or {}
                        vv = {k: ph.voice_files(v) for k, v in (body.get('values_voice') or {}).items() if v}
                        if vv:
                            rec['values_audio'] = vv
                pend = ph.voice_pending(pid)
                if pend and not rec.get('dismissed'):
                    rec['voice_notes_pending'] = pend                     # their rows follow when transcribed
                if p.get('who'):
                    rec['for'] = p['who']                                 # the agent that sent the panel
                if p.get('answers_path') and not rec.get('dismissed'):
                    try:
                        ap = Path(p['answers_path'])
                        ap.parent.mkdir(parents=True, exist_ok=True)
                        with open(ap, 'a', encoding='utf8') as f:
                            f.write(json.dumps({'ts': now_iso(), 'exam': pid, 'kind': 'answer', 'answers': rec['answers'],
                                                **({'voice_notes_pending': rec['voice_notes_pending']}
                                                   if rec.get('voice_notes_pending') else {})},
                                               ensure_ascii=False) + '\n')
                    except OSError as e:
                        rec['answers_path_error'] = str(e)
                ph.view['panels'] = [x for x in ph.view['panels'] if x['id'] != pid]
                ph.save()
                rec = ph.post(rec)
                ph.panel_gone(pid)
                ph.cmd('close', ref=pid)
                return self._json(200, {'ok': True, 'n': rec['n']})
            return self._json(404, {'error': 'no such action'})
        except (ValueError, KeyError, TypeError) as e:
            return self._json(400, {'ok': False, 'error': str(e)})
        except Exception as e:
            return self._json(500, {'ok': False, 'error': f'{type(e).__name__}: {e}'})


class Stamped:
    """A log stream whose every line starts with the UTC time (the Director, 10-07: drops in server.log could not be
    dated)."""

    def __init__(self, f):
        self.f, self.bol = f, True

    def write(self, s):
        out = []
        for part in s.splitlines(True):
            if self.bol:
                out.append(time.strftime('%H:%M:%SZ ', time.gmtime()))
            out.append(part)
            self.bol = part.endswith('\n')
        return self.f.write(''.join(out))

    def __getattr__(self, k):
        return getattr(self.f, k)


class PhoneServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """A phone that drops off (4G, the screen off, a page closed) mid long-poll is one line, not a traceback."""
        e = sys.exc_info()[1]
        if isinstance(e, (ConnectionError, TimeoutError)):
            sys.stderr.write(f"[phone] {client_address[0]} dropped: {type(e).__name__}\n")
            return
        super().handle_error(request, client_address)


def serve(port=8870, inbox=None, host='127.0.0.1'):
    ph = Phone(inbox)
    Handler.ph, Handler.agent = ph, Agent(ph)
    httpd = PhoneServer((host, port), Handler)
    httpd.daemon_threads = True
    rec = {'port': httpd.server_address[1], 'pid': os.getpid(), 'started': time.time()}
    (HOME / 'server.json').write_text(json.dumps(rec), encoding='utf8')
    print(f"[phone] http://{host}:{rec['port']}/ (pid {rec['pid']})", flush=True)
    try:
        httpd.serve_forever()
    finally:
        try:
            if json.loads((HOME / 'server.json').read_text(encoding='utf8')).get('pid') == os.getpid():
                (HOME / 'server.json').unlink()
        except (OSError, ValueError):
            pass
    return httpd


def main(argv=None):
    a = argparse.ArgumentParser(description='the ismail phone page server')
    a.add_argument('--port', type=int, default=8870)
    a.add_argument('--inbox', default=None, help='also write what the person sends here')
    args = a.parse_args(argv)
    sys.stdout, sys.stderr = Stamped(sys.stdout), Stamped(sys.stderr)
    serve(args.port, args.inbox)


if __name__ == '__main__':
    main()
