"""Agent-facing operations. Every op takes the project directory first and returns text.

Conventions (repeated in every tool description so an agent never has to guess):
  * bars are 1-indexed; bar ranges [a, b] are inclusive.
  * note times inside a write are beats (quarter notes) relative to the target bar's beat 1, e.g. "0 E2 0.5 100".
  * pitches are names (C4 = 60, F#2, Bb3) or MIDI numbers.
  * audio sources for analysis: 'render' (last render), 'ref' (reference), 'ref:drums|bass|other|vocals' (reference
    stems), 'track:<name>' (last rendered stem of a track), 'sound:<name>' (sound bank), or a file path.
"""
import copy
import glob
import json
import math
import os
import re
import shutil
import time

import numpy as np

from . import analysis as A
from . import machine
from . import provenance
from . import sources as sourcesmod
from . import fx as fxmod
from . import rig as rigmod
from . import instruments as inst_mod
from .notation import (fmt_offset, parse_notes, parse_steps, pitch_to_midi, midi_to_name, format_notes, piano_roll, fmt_num,
                       NotationError)
from .presets import PRESETS

OPS = {}
MUTATING = set()
WINDOWS_FILE = 'windows.json'  # renders/: {file relative to renders/: window} for files written by a bars=[a, b] render


class OpError(ValueError):
    """Raised with a message that says what to do next."""


ARG_ALIASES = {'name': 'track', 'track': 'name', 'map': 'mapping', 'stem_map': 'mapping'}


def _alias_args(sig, kw):
    """Accept the names agents reach for: name/track for each other, map for mapping, and bars=[a, b] (inclusive)
    where an op takes span=[a, b) (fractional bars, end exclusive)."""
    params = sig.parameters
    for k in list(kw):
        if k in params:
            continue
        alt = ARG_ALIASES.get(k)
        if alt and alt in params and alt not in kw:
            kw[alt] = kw.pop(k)
        elif k == 'bars' and 'span' in params and 'span' not in kw and isinstance(kw[k], (list, tuple)):
            b = kw.pop(k)
            kw['span'] = [b[0], b[-1] + 1]
    return kw


def op(mutates=False):
    def deco(fn):
        import functools
        import inspect
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapped(*a, **kw):
            kw = _alias_args(sig, kw)
            try:
                sig.bind(*a, **kw)
            except TypeError as e:
                raise OpError(f"bad arguments for {fn.__name__}: {e}. Signature: {fn.__name__}{sig}")
            try:
                return fn(*a, **kw)
            except machine.MachineBusy as e:
                raise OpError(str(e))
        OPS[fn.__name__] = wrapped
        if mutates:
            MUTATING.add(fn.__name__)
        return wrapped
    return deco


def heavy(kind='cpu'):
    """An op that runs in one of the machine's heavy-job slots (ismail.machine) for its whole length; it refuses
    with the reason when the machine is busy or hot. Goes under @op()."""
    def deco(fn):
        import functools

        @functools.wraps(fn)
        def wrapped(*a, **kw):
            proj = str(kw.get('project', a[0] if a else '')).replace(os.sep, '/').rstrip('/')
            with machine.slot(kind, f"{fn.__name__} {os.path.basename(proj)}", disk_path=proj or None):
                return fn(*a, **kw)
        return wrapped
    return deco


# ------------------------------------------------------------------ project state

class Project:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.file = os.path.join(self.root, 'project.json')
        if not os.path.exists(self.file):
            raise OpError(f"no project at {self.root}; create one with project_new(project='{root}', bpm=..., length_bars=...)")
        with open(self.file, encoding='utf8') as f:
            self.d = json.load(f)

    @property
    def bpb(self):
        return self.d.get('beats_per_bar', 4)

    def bar_to_beat(self, bar):
        return (bar - 1) * self.bpb

    def track(self, name):
        if name not in self.d['tracks']:
            raise OpError(f"no track {name!r}; tracks: {list(self.d['tracks']) or 'none'} (track_add to create)")
        return self.d['tracks'][name]

    def save(self, snapshot=True):
        if snapshot and os.path.exists(self.file):
            hd = os.path.join(self.root, 'history')
            os.makedirs(hd, exist_ok=True)
            shutil.copy(self.file, os.path.join(hd, f"{time.time():.3f}.json"))
            old = sorted(glob.glob(os.path.join(hd, '*.json')))
            for f in old[:-100]:
                os.remove(f)
        tmp = self.file + '.tmp'
        with open(tmp, 'w', encoding='utf8') as f:
            json.dump(self.d, f, indent=1)
        os.replace(tmp, self.file)

    def grid(self, bpm=None, offset_sec=None):
        return A.Grid(bpm or self.d['bpm'], self.d.get('offset_sec', 0.0) if offset_sec is None else offset_sec, self.bpb)

    def render_window(self, path):
        """None if `path` is not a windowed render, else {'bars': [a, b], 'start_sec': song time of the file's t=0}."""
        rd = os.path.join(self.root, 'renders')
        try:
            rel = os.path.relpath(os.path.abspath(path), rd).replace(os.sep, '/')
            with open(os.path.join(rd, WINDOWS_FILE), encoding='utf8') as f:
                return json.load(f).get(rel)
        except (OSError, ValueError):
            return None

    def source(self, src, bars=None, span=None):
        """(path, grid) for an analysis source. A windowed render gets a grid shifted by where its file starts, so
        song bar numbers land on the right samples; bars [a, b] (inclusive) or span [a, b) (fractional) outside the
        window raise."""
        path = self.resolve_audio(src)
        g = self.grid()
        w = self.render_window(path)
        if not w:
            return path, g
        w0, w1 = w['bars']
        asked = ([(bars[0], bars[1] + 1, f"bars {list(bars)}")] if bars else []) +                 ([(span[0], span[1], f"span {list(span)}")] if span else [])
        for lo, hi, shown in asked:
            if lo < w0 or hi > w1 + 1:
                raise OpError(f"{shown} is outside {src!r}, which holds only bars {w0}-{w1} (render(bars=[{w0}, {w1}]));"
                              f" pick bars inside that window, or render those bars / the full song first")
        return path, A.Grid(g.bpm, g.offset - w['start_sec'], g.bpb)

    def auto_source(self, src):
        """None means: the reference if the project has one, else your latest render."""
        if src:
            return src
        return 'ref' if (self.d.get('reference') or {}).get('file') else 'render'

    def resolve_audio(self, src):
        src = self.auto_source(src)
        ref = self.d.get('reference') or {}
        if src == 'render':
            p = os.path.join(self.root, 'renders', 'latest.wav')
            if not os.path.exists(p):
                raise OpError("nothing rendered yet; call render first")
            return p
        if src == 'ref':
            if not ref.get('file'):
                raise OpError("project has no reference track. To analyse your own audio pass source='render' (the mix) "
                              "or source='track:<name>' (after render(stems=True)); to compare against a recording, "
                              "set one with project_set(reference='path/to.wav')")
            return ref['file']
        if src.startswith('ref:'):
            if not ref.get('file'):
                raise OpError(f"project has no reference track, so there is no {src!r}. For your own parts pass "
                              f"source='track:<name>' (after render(stems=True)) or 'render' (the mix)")
            stem = src[4:]
            sd = ref.get('stems_dir')
            if not sd:
                raise OpError("reference has no stems yet; run separate(source='ref') first")
            p = os.path.join(sd, stem + '.wav')
            if not os.path.exists(p):
                raise OpError(f"no reference stem {stem!r}; have: {[os.path.basename(x)[:-4] for x in glob.glob(sd + '/*.wav')]}")
            return p
        if src.startswith('track:'):
            p = os.path.join(self.root, 'renders', 'stems', src[6:] + '.wav')
            if not os.path.exists(p):
                raise OpError(f"no rendered stem for track {src[6:]!r}; render(stems=True) first")
            return p
        if src.startswith('sound:'):
            meta = self.d.get('sounds', {}).get(src[6:])
            if not meta:
                raise OpError(f"no sound {src[6:]!r}; sound_list to see the bank")
            return os.path.join(self.root, meta['file'])
        p = src if os.path.isabs(src) else os.path.join(self.root, src)
        if not os.path.exists(p):
            raise OpError(f"audio source {src!r} not found. Use render | ref | ref:<stem> | track:<name> | sound:<name> | a path")
        return p


def _load(project):
    return Project(project)


def _span_bars(notes_rel, bpb):
    end = max((n[0] + n[2] for n in notes_rel), default=bpb)
    return max(1, math.ceil(end / bpb - 1e-9))


def _clear(tr, b0, b1):
    """Remove notes starting in [b0, b1) beats."""
    before = len(tr['notes'])
    tr['notes'] = [n for n in tr['notes'] if not (b0 <= n[0] < b1)]
    return before - len(tr['notes'])


def _resolve_instrument(spec, P=None):
    """dict | 'preset:<name>' | 'track:<name>' (a copy of that track's instrument; needs the project)."""
    if isinstance(spec, str) and spec.startswith('track:'):
        if P is None:
            raise OpError("'track:<name>' instruments need a project context")
        inst = P.track(spec[6:]).get('instrument')
        if not inst:
            raise OpError(f"track {spec[6:]!r} has no instrument")
        return copy.deepcopy(inst)
    if isinstance(spec, str):
        if spec.startswith('preset:'):
            spec = spec[7:]
        if spec not in PRESETS:
            raise OpError(f"unknown preset {spec!r}; presets: {', '.join(PRESETS)}")
        return copy.deepcopy(PRESETS[spec])
    try:
        inst_mod.normalize(spec)
    except inst_mod.InstrumentError as e:
        raise OpError(f"instrument invalid: {e}")
    _check_voices(spec, P)
    return spec


def _check_voices(spec, P):
    """Fail at edit time, not render time, when a code instrument names a voice that does not exist."""
    from . import voices
    if not isinstance(spec, dict):
        return
    if spec.get('type') == 'code' and spec.get('voice'):
        try:
            mod = voices.load(spec['voice'], P.root if P else None)
            # fn 'voice' (the old help text's default) on a performer voice plays it with perform, as a missing fn does
            if spec.get('fn') not in (None, 'voice') or not callable(getattr(mod, 'perform', None)):
                voices.function(spec['voice'], spec.get('fn', 'voice'), P.root if P else None)
        except voices.VoiceError as e:
            raise OpError(f"instrument invalid: {e}")
    for v in (spec.get('map') or {}).values():
        _check_voices(v, P)


# ------------------------------------------------------------------ project ops

@op()
def project_new(project: str, bpm: float, length_bars: int, name: str = None, beats_per_bar: int = 4,
                offset_sec: float = 0.0, reference: str = None, objective: str = None,
                derived_from: str = None) -> str:
    """Create a new project directory. offset_sec = time of bar 1 (match a reference's grid with analyze_grid).
    objective: what this piece is for, in the person's words ("keep a listener asleep for 3 hours"); see project_set.
    derived_from: the project this one is a version of; its objectives and lineage are carried over, so a derivative
    keeps the intent it came from (intent provenance)."""
    root = os.path.abspath(project)
    if os.path.exists(os.path.join(root, 'project.json')):
        raise OpError(f"project already exists at {root}; use project_info / project_set")
    os.makedirs(os.path.join(root, 'sounds'), exist_ok=True)
    d = {"version": 1, "name": name or os.path.basename(root), "bpm": bpm, "beats_per_bar": beats_per_bar,
         "sr": 44100, "offset_sec": offset_sec, "length_bars": length_bars, "tail_sec": 2.0,
         "tracks": {}, "buses": {}, "master": {"fx": [{"type": "limiter", "ceiling_db": -0.3, "gain_db": 0.0}], "volume_db": 0.0},
         "sounds": {}, "reference": None}
    if reference:
        d['reference'] = {"file": os.path.abspath(reference)}
    if derived_from:
        src = os.path.join(os.path.abspath(derived_from), 'project.json')
        if not os.path.exists(src):
            raise OpError(f"derived_from {derived_from!r} has no project.json; give the folder of the project this "
                          f"one is a version of")
        with open(src, encoding='utf8') as f:
            parent = json.load(f)
        d['lineage'] = [{"project": os.path.abspath(derived_from), "name": parent.get('name'),
                         "objectives": parent.get('objectives', [])}] + parent.get('lineage', [])
    if objective:
        d['objectives'] = [_objective(objective, 'user')]
    with open(os.path.join(root, 'project.json'), 'w', encoding='utf8') as f:
        json.dump(d, f, indent=1)
    return f"created project {d['name']} at {root}: {bpm} BPM, {length_bars} bars, bar 1 at {offset_sec}s"


@op()
def project_info(project: str) -> str:
    """Summary of the whole project: grid, tracks (instrument, note count, bars used, fx), buses, master, sounds."""
    P = _load(project)
    d = P.d
    L = [f"{d['name']}: {d['bpm']} BPM, {d['beats_per_bar']}/4, {d['length_bars']} bars, bar 1 at {d['offset_sec']}s, "
         f"song {(d['length_bars'] * d['beats_per_bar'] * 60 / d['bpm'] + d['offset_sec']):.1f}s"]
    if d.get('objectives'):
        o = d['objectives'][-1]
        L.append(f"objective: {o['text']!r} (by {o['by']}, {o['date'][:10]})"
                 + (f"; {len(d['objectives']) - 1} earlier" if len(d['objectives']) > 1 else ''))
    else:
        L.append("objective: none stated (project_set(objective=...) in the person's words: what is this piece for?)")
    for anc in d.get('lineage', [])[:3]:
        last = (anc.get('objectives') or [{}])[-1].get('text')
        L.append(f"derived from {anc.get('name')}" + (f", whose objective was {last!r}" if last else ''))
    if d.get('reference'):
        L.append(f"reference: {d['reference'].get('file')}" + (f" (stems: {d['reference']['stems_dir']})" if d['reference'].get('stems_dir') else ''))
    L.append(f"tracks ({len(d['tracks'])}):")
    for name, tr in d['tracks'].items():
        ns = tr.get('notes', [])
        inst = tr.get('instrument') or {}
        bars_used = sorted({int(n[0] // P.bpb) + 1 for n in ns})
        flags = ' '.join(f for f, on in (('MUTE', tr.get('mute')), ('SOLO', tr.get('solo'))) if on)
        fxs = ', '.join(f"{i}:{f['type']}" for i, f in enumerate(tr.get('fx', [])))
        L.append(f"  {name:<14} {inst.get('type', 'audio'):<8} {len(ns):>4} notes bars {_ranges(bars_used) or '-'}"
                 f" | vol {tr.get('volume_db', 0):+.1f} pan {tr.get('pan', 0):+.2f} -> {tr.get('output', 'master')}"
                 + (f" sends {tr['sends']}" if tr.get('sends') else '') + (f" | fx [{fxs}]" if fxs else '')
                 + (f" | auto {list(tr['automation'])}" if tr.get('automation') else '')
                 + (f" | offset {tr['offset_ms']:+g} ms" if tr.get('offset_ms') else '')
                 + (f" | {sum(1 for n in ns if len(n) > 4 and n[4])} notes nudged" if any(len(n) > 4 and n[4] for n in ns) else '')
                 + (f" | {len(tr['audio'])} audio clips" if tr.get('audio') else '') + (f" {flags}" if flags else ''))
    for b, bus in d.get('buses', {}).items():
        L.append(f"bus {b}: fx [{', '.join(f['type'] for f in bus.get('fx', []))}] vol {bus.get('volume_db', 0):+.1f}")
    L.append(f"master: fx [{', '.join(f['type'] for f in d['master'].get('fx', []))}] vol {d['master'].get('volume_db', 0):+.1f}")
    if d.get('sounds'):
        L.append(f"sounds: {', '.join(d['sounds'])}")
    L += provenance.summary(d, P.root)[0]
    L += sourcesmod.summary(P.root, d)[0]
    return '\n'.join(L)


def _objective(text, by):
    return {"text": text.strip(), "by": by, "date": time.strftime('%Y-%m-%dT%H:%M:%S')}


def _ranges(xs):
    if not xs:
        return ''
    out = []
    a = b = xs[0]
    for x in xs[1:]:
        if x == b + 1:
            b = x
        else:
            out.append(f"{a}-{b}" if a != b else str(a))
            a = b = x
    out.append(f"{a}-{b}" if a != b else str(a))
    return ','.join(out)


@op(mutates=True)
def project_set(project: str, bpm: float = None, length_bars: int = None, offset_sec: float = None,
                reference: str = None, tail_sec: float = None, name: str = None, reference_stems: str = None,
                objective: str = None, objective_by: str = 'user') -> str:
    """Change project settings. Changing bpm keeps notes on the same beats (the song gets faster/slower).
    reference_stems = a folder of already-separated stems (drums.wav, bass.wav, ...) for the reference.
    objective: what the piece is for, in the words of whoever set it (objective_by, default 'user'): a sleep set's
    might be "stay asleep", a cover's "sound like the 1970 record". Earlier objectives are kept as history;
    project_info shows the current one, and every version made from this one carries them (derived_from)."""
    P = _load(project)
    ch = []
    for k, v in (('bpm', bpm), ('length_bars', length_bars), ('offset_sec', offset_sec), ('tail_sec', tail_sec),
                 ('name', name)):
        if v is not None:
            P.d[k] = v
            ch.append(f"{k}={v}")
    if reference is not None:
        p = os.path.abspath(reference)
        if not os.path.exists(p):
            raise OpError(f"reference file {reference!r} not found")
        P.d['reference'] = dict(P.d.get('reference') or {}, file=p)
        ch.append(f"reference={p}")
    if objective is not None:
        if not objective.strip():
            raise OpError("objective: say what the piece is for, in the person's words")
        P.d.setdefault('objectives', []).append(_objective(objective, objective_by))
        ch.append(f"objective={objective!r} (by {objective_by})")
    if reference_stems is not None:
        if not P.d.get('reference'):
            raise OpError("set reference first")
        P.d['reference']['stems_dir'] = os.path.abspath(reference_stems)
        ch.append(f"reference_stems={reference_stems}")
    P.save()
    return "set " + (', '.join(ch) if ch else 'nothing')


@op(mutates=True)
def undo(project: str, steps: int = 1) -> str:
    """Restore the project to its state `steps` mutations ago (every mutating op snapshots first)."""
    P = _load(project)
    hd = os.path.join(P.root, 'history')
    snaps = sorted(glob.glob(os.path.join(hd, '*.json')))
    if len(snaps) < steps:
        raise OpError(f"only {len(snaps)} snapshots available")
    target = snaps[-steps]
    shutil.copy(target, P.file)
    for s in snaps[-steps:]:
        os.remove(s)
    return f"restored snapshot from {time.ctime(float(os.path.basename(target)[:-5]))}"


# ------------------------------------------------------------------ tracks

@op(mutates=True)
def track_add(project: str, name: str, instrument=None, volume_db: float = 0.0, pan: float = 0.0,
              output: str = 'master') -> str:
    """Add a track. instrument: a dict (see instrument_help) or 'preset:<name>' (see presets_list). None = audio-only track."""
    P = _load(project)
    if name in P.d['tracks']:
        raise OpError(f"track {name!r} exists; use track_set / instrument_set")
    inst = _resolve_instrument(instrument, P) if instrument is not None else None
    P.d['tracks'][name] = {"instrument": inst, "notes": [], "fx": [], "volume_db": volume_db, "pan": pan,
                           "mute": False, "solo": False, "output": output, "sends": {}, "automation": {}, "audio": []}
    P.save()
    return f"added track {name!r} ({inst['type'] if inst else 'audio'})"


@op(mutates=True)
def track_set(project: str, track: str, volume_db: float = None, pan: float = None, mute: bool = None,
              solo: bool = None, output: str = None, sends: dict = None, rename: str = None,
              offset_ms: float = None) -> str:
    """Mixer settings for a track. sends = {bus: level_db} (replaces all sends; {} removes). offset_ms moves every
    sound of the track (notes and audio clips) off its beat, negative = earlier, e.g. -35 for a part whose attacks
    come late; 0 removes it. Notes keep their beats (notes_read shows them on the grid); automation stays on the
    song's time."""
    P = _load(project)
    tr = P.track(track)
    if offset_ms is not None:
        from .notation import MAX_OFFSET_MS
        if abs(offset_ms) > MAX_OFFSET_MS:
            raise OpError(f"offset_ms {offset_ms:g}: at most {MAX_OFFSET_MS:g} ms either way; a larger move is a "
                          f"different start (notes_transform shift_beats)")
        if offset_ms:
            tr['offset_ms'] = float(offset_ms)
        else:
            tr.pop('offset_ms', None)
    for k, v in (('volume_db', volume_db), ('pan', pan), ('mute', mute), ('solo', solo), ('output', output),
                 ('sends', sends)):
        if v is not None:
            tr[k] = v
    if output and output != 'master' and output not in P.d['buses']:
        raise OpError(f"no bus {output!r}; bus_add first. buses: {list(P.d['buses'])}")
    for b in (sends or {}):
        if b not in P.d['buses']:
            raise OpError(f"no bus {b!r}; bus_add first. buses: {list(P.d['buses'])}")
    if rename:
        if rename in P.d['tracks']:
            raise OpError(f"a track named {rename!r} already exists")
        P.d['tracks'] = {(rename if k == track else k): v for k, v in P.d['tracks'].items()}
        # keep every reference to the old name valid
        sm = P.d.get('stem_map') or {}
        if track in sm:
            sm[rename] = sm.pop(track)
        for tr in P.d['tracks'].values():
            for f in tr.get('fx', []):
                for key in ('sidechain', 'source', 'modulator'):
                    if f.get(key) == track:
                        f[key] = rename
    P.save()
    return f"updated track {track!r}"


@op(mutates=True)
def track_remove(project: str, track: str) -> str:
    """Delete a track (undo restores it)."""
    P = _load(project)
    P.track(track)
    del P.d['tracks'][track]
    P.save()
    return f"removed track {track!r}"


@op(mutates=True)
def bus_add(project: str, name: str, fx: list = None, volume_db: float = 0.0) -> str:
    """Add an effect-return bus (e.g. reverb). Route with track_set(sends={name: -12}) or track_set(output=name)."""
    P = _load(project)
    if name in P.d['buses']:
        raise OpError(f"bus {name!r} exists; use fx_add/fx_set with target='bus:{name}'")
    for f in fx or []:
        try:
            fxmod.normalize(f)
        except fxmod.FxError as e:
            raise OpError(str(e))
    P.d['buses'][name] = {"fx": list(fx or []), "volume_db": volume_db}
    P.save()
    return f"added bus {name!r}"


# ------------------------------------------------------------------ instruments

@op()
def guide(project: str = None, first_answer: str = None) -> str:
    """Read this first: how to use this DAW as an agent (workflow, conventions, which tool for which question). For
    a person who has made nothing with ismail yet it opens with how to run their first session. first_answer: the
    person's first answer, verbatim; the reply is then only which words to use with them from now on (musician,
    when they name an instrument they play, a style they trained in or reading music; otherwise plain words)."""
    from .guide import GUIDE, FIRST_SESSION, vocabulary_text
    from . import sketch as SK
    if first_answer is not None:
        return vocabulary_text(first_answer)
    if SK.is_new(project):
        return FIRST_SESSION.format(marker=SK.marker_path(), showcase=SK.showcase_text()) + '\n\n' + GUIDE
    return GUIDE


@op()
@heavy()
def sketch(project: str, brief: str, base: str = None, n: int = None, styles: list = None, key: str = None,
           bpm: float = None, bars: int = None, progression: str | list = None, seed: int = 0,
           background: bool = True) -> str:
    """First sound for a new song, in one call: short sketches on the showcase voices, each a project in
    <project>/sketches/<letter>-<label>/, rendered to mp3 (wav without ffmpeg). brief: the person's words. The brief
    is read: a tempo ('90 BPM'), a key ('A minor'), a genre (trip-hop, house, jazz, rock, ambient, a church prelude or
    hymn ...), gentle or soft words, instruments and a form (intro, groove, breakdown, return, fade) shape the
    sketch; three readings come back (as asked, sparser, busier). Whatever has no voice yet is named first in the reply ("asked for Rhodes: ... grand_piano plays
    its part"): tell the person. A brief naming no genre or instrument gets three contrasting styles (piano,
    chamber, band). base='<letter>': the next round, that sketch changed by the brief's words ("slower, no guitar,
    add a pad"); n: how many (default 3, or 2 with base). styles: force the fixed styles. key, bpm, bars,
    progression ('i VI III VII' or chord names) override. Play each to the person, ask which is closest or what
    each is missing, then sketch_keep(project, '<letter>').
    The first sketch comes back as soon as it is rendered and the others render in the background (background=
    False waits for all): play the first while they land, and sketch_wait(project) says when they are ready. The
    reply opens with SAY TO THE PERSON, written for them: read it out as it is. Every sketch is mixed (the tune
    sits 4-6 LU over the rest) and mastered for its style."""
    import sys
    from . import sketch as SK
    from . import voices as V
    root = os.path.abspath(project)
    sd = os.path.join(root, 'sketches')
    have = sorted(os.listdir(sd)) if os.path.isdir(sd) else []
    n = n or (2 if base else 3)
    if not 1 <= n <= 4:
        raise OpError("n: 1 to 4 sketches")
    try:
        if styles:
            if isinstance(styles, str):
                styles = [x for x in re.split(r'[\s,]+', styles) if x]
            bad = [x for x in styles if x not in SK.STYLES]
            if bad or not 1 <= len(styles) <= 4:
                raise OpError(f"styles: one to four of {', '.join(SK.STYLES)} (got {styles})")
            todo, said = [], []
            for st in styles:
                spec = SK.style_spec(st)
                got = SK.read_brief(brief)
                spec.update({'key': key or got['key'], 'bpm': bpm or got['bpm'], 'form': got['form']})
                todo.append((st, spec))
        else:
            base_spec = None
            if base:
                hits = [f for f in have if f == base or f.split('-')[0] == base]
                if len(hits) != 1 or not os.path.exists(os.path.join(sd, hits[0], 'sketch.json')):
                    raise OpError(f"base={base!r}: no single sketch with that letter in {sd} (have: "
                                  f"{', '.join(have) or 'none'})")
                with open(os.path.join(sd, hits[0], 'sketch.json'), encoding='utf8') as f:
                    base_spec = json.load(f)['spec']
            todo, said = SK.specs_for(brief, key, bpm, n, base_spec)
    except SK.SketchError as e:
        raise OpError(str(e))
    sc = {v['name']: v for v in SK.showcase()['voices']}
    used = {f.split('-')[0] for f in have}
    letters = [c for c in 'abcdefghijklmnopqrstuvwxyz' if c not in used]
    mp3 = 'also' if _ffmpeg_ok() else 'none'
    L = [f"sketches for: {brief}" + (f" (from sketch {base})" if base else '')]
    L += [f"FOR YOU: {x}" for x in said]
    t0 = time.time()
    built, plans = [], []
    for i, (label, spec) in enumerate(todo):
        for role, voice in list(spec['parts'].items()):     # spec S-5: only the showcase plays a first sketch
            if voice not in sc:
                spec['parts'].pop(role)
                spec.setdefault('subs', []).append({'asked': role, 'role': role, 'plays': None})
        try:
            pl = SK.plan_spec(spec, brief, seed, i + len(used), bars, progression, label)
        except SK.SketchError as e:
            raise OpError(str(e))
        letter = letters[i]
        slug = re.sub(r'[^a-z]+', '-', label.split(',')[0].lower()).strip('-')
        sp = os.path.join(sd, f"{letter}-{slug}")
        project_new(sp, pl['bpm'], pl['bars'], name=f"sketch {letter} ({label})", objective=brief)
        ops = []
        for role, part in pl['parts'].items():
            v = sc[part['voice']]
            ops.append({'op': 'track_add', 'name': role, 'instrument': v['instrument'], 'volume_db': part['level']})
            if v.get('rig'):
                rig = V.info(v['voice'], None)[2].get('rigs', {}).get(v['rig'])
                if rig:
                    ops += [{'op': 'fx_add', 'target': role, 'fx': fx} for fx in rig['fx']]
            ops += [{'op': 'fx_add', 'target': role, 'fx': fx} for fx in v.get('fx', [])]
            for b in range(pl['bars']):
                t = SK.note_text(part['notes'], b)
                if t:
                    ops.append({'op': 'notes_write', 'track': role, 'bar': b + 1, 'notes': t, 'mode': 'add'})
        batch(sp, ops)
        for role, part in pl['parts'].items():                # a sketch states its sounds: no "unstated" nag
            v = sc[part['voice']]
            track_model(sp, role, on='designed' if not v.get('needs') and v['name'] in ('sub_bass', 'crackle')
                        else f"showcase voice {v['name']} ({v['why']})", by='sketch')
        with open(os.path.join(sp, 'sketch.json'), 'w', encoding='utf8') as f:
            json.dump({'brief': brief, 'label': label, 'spec': spec, 'key': pl['key'], 'bpm': pl['bpm'],
                       'form': pl['form'], 'progression': pl['progression']}, f, indent=1)
        prod = SK.production(spec, list(pl['parts']))             # a first impression is mixed and mastered
        P = _load(sp)
        P.d['master']['fx'] = prod['master']
        P.save()
        bus_add(sp, 'room', fx=[prod['room']])
        for role, db in prod['sends'].items():
            track_set(sp, role, sends={'room': db})
        for role, fxs in prod['track_fx'].items():
            for fx in fxs:
                fx_add(sp, role, fx)
        for f_ in ('sketch_ready.json',):
            try:
                os.remove(os.path.join(sp, f_))
            except OSError:
                pass
        built.append({'sp': sp, 'letter': letter, 'lufs': prod['lufs'], 'mp3': mp3, 'why': prod['why']})
        plans.append((letter, pl, spec))
    # spec S-2: the first is played as soon as it lands; the others render behind it
    rest = built[1:] if background and len(built) > 1 else []
    for job in (built[:1] if rest else built):
        got = _sketch_render(job)
        print(f"[sketch] {job['letter']} ready ({time.time() - t0:.0f} s): {got['file']}", file=sys.stderr, flush=True)
    if rest:
        _sketch_spawn(rest, sd)
    first = plans[0][1]
    say = [f"Here {'are' if len(plans) > 1 else 'is'} {len(plans)} short sketch{'es' if len(plans) > 1 else ''}"
           + (f"; the first is ready now and the other{'s land' if len(rest) > 1 else ' lands'} in a minute or two"
              if rest else '') + '.']
    for letter, pl, spec in plans:
        sec = pl['bars'] * 4 * 60 / pl['bpm']
        diff = SK.contrast(first, pl) if pl is not first else []
        say.append(f"{letter.upper()}: {SK.plain_parts({r: p['voice'] for r, p in pl['parts'].items()})}, in "
                   f"{pl['key']} at {pl['bpm']:g} BPM, about {sec:.0f} seconds"
                   + (f"; unlike A: {', '.join(diff)}" if diff else '') + '.')
    seen = []
    for _, _, spec in plans:
        for x in SK.say_plain(spec):
            if x not in seen:
                seen.append(x)
    say += [x[0].upper() + x[1:] + '.' for x in seen]
    L[1:1] = ['SAY TO THE PERSON (read it out as it is):'] + ['  ' + x for x in say]
    for (letter, pl, spec), job in zip(plans, built):
        sec = pl['bars'] * 4 * 60 / pl['bpm']
        form = f"; form {' > '.join(pl['form'])} (4 bars each)" if pl['form'] else ''
        L += [f"{letter}) {pl['what']}",
              f"   {pl['key']}, {pl['bpm']:g} BPM, {pl['bars']} bars (~{sec:.0f} s){form}; chords "
              f"{' '.join(pl['progression'])}" + (f", closing {pl['cadence']}" if pl.get('cadence') else '') +
              (f"; feel {pl['feel']}" if pl['feel'] else '') + ('; soft' if pl.get('soft') else '')]
        if job in rest:
            L.append(f"   rendering in the background: sketch_wait(project) says when it is ready")
        else:
            r = job['done']
            L.append(f"   {r['lufs']:.1f} LUFS, peak {r['peak']:.1f} dBFS; {r['balance']}; {job['why']}; listen: "
                     f"{r['file']}")
    if rest:
        L.append(f"PLAY {plans[0][0].upper()} NOW (open its file); while it plays, sketch_wait(project) waits for "
                 f"the others.")
    L.append("NEXT: play them to the person one at a time (open each file), ask which is closest or what each is "
             "missing. Their correction is the next round: sketch(project, '<their words>', base='<letter>'). "
             f"sketch_keep(project, '<letter>') makes the pick the song (it is the song's example); until then "
             f"{root} holds only sketches/, each its own project. These are sketches: do not polish one (no "
             f"Listening Report, no section fixes) before the person picks.")
    return '\n'.join(L)


@op()
def samples_list() -> str:
    """The sample sets some built-in voices play from (a sampled Rhodes, a sampled kit, a clean guitar): whether each
    is on this machine, its size and its licence. A voice whose set is missing raises an error that says what to
    fetch; sketch uses a stand-in and says so."""
    from . import samples
    return samples.status() + f"\nstore: {samples.root()}"


@op()
def samples_fetch(name: str, path: str = None) -> str:
    """Download a sample set into the store (~/.ismail/samples, or $ISMAIL_SAMPLES), only after the person says
    yes: tell them its size and licence first (samples_list shows both). path=<a folder that already holds the
    set>: register it instead of downloading (nothing is copied)."""
    from . import samples
    try:
        p = samples.fetch(name, register=path, log=lambda m: None)
    except samples.SampleError as e:
        raise OpError(str(e))
    s = samples.SETS[name]
    return (f"{name} {'registered' if path else 'fetched'}: {p}\nthe {s['voice']} voice plays it now. Credit: "
            f"{s['credit']} ({s['licence']})")


def _lufs_peak(path):
    try:
        import pyloudnorm as pyln
        import soundfile as sf
        y, sr = sf.read(path)
        return float(pyln.Meter(sr).integrated_loudness(y)), float(20 * np.log10(np.max(np.abs(y)) + 1e-12))
    except Exception:
        return float('nan'), float('nan')


@op(mutates=True)
def sketch_keep(project: str, sketch: str, replace: bool = False) -> str:
    """Make a sketch the song: its project (tempo, tracks, notes, fx) is copied to <project>, with the sketch as its
    lineage, and the first session is marked done for this person (guide stops opening with it). sketch: its letter
    ('b') or folder name. replace=True only when the person says to drop what <project> already holds."""
    from . import sketch as SK
    root = os.path.abspath(project)
    sd = os.path.join(root, 'sketches')
    hits = [f for f in (os.listdir(sd) if os.path.isdir(sd) else []) if f == sketch or f.split('-')[0] == sketch]
    if len(hits) != 1:
        have = ', '.join(sorted(os.listdir(sd))) if os.path.isdir(sd) else 'none: run sketch first'
        raise OpError(f"no single sketch {sketch!r} in {sd} (have: {have})")
    src = os.path.join(sd, hits[0])
    dst = os.path.join(root, 'project.json')
    if os.path.exists(dst) and not replace:
        with open(dst, encoding='utf8') as f:
            if json.load(f).get('tracks'):
                raise OpError(f"{root} already holds a song with tracks; sketch_keep(..., replace=True) only if the "
                              f"person says to drop it, or keep the sketch into a new folder")
    with open(os.path.join(src, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    d['lineage'] = [{"project": src, "name": d.get('name'), "objectives": d.get('objectives', []),
                     "kept": "the person picked this sketch"}] + d.get('lineage', [])
    d['name'] = os.path.basename(root)
    for sub in ('sounds', 'voices'):
        if os.path.isdir(os.path.join(src, sub)):
            shutil.copytree(os.path.join(src, sub), os.path.join(root, sub), dirs_exist_ok=True)
    os.makedirs(os.path.join(root, 'sounds'), exist_ok=True)
    with open(dst, 'w', encoding='utf8') as f:
        json.dump(d, f, indent=1)
    SK.mark_done()
    return (f"kept {hits[0]} as the song in {root} ({d['bpm']} BPM, {d['length_bars']} bars, tracks: "
            f"{', '.join(d['tracks'])}). First session marked done ({SK.marker_path()}).\n"
            f"NEXT: offer one deliberate change ('change just one thing': a warmer bass from bar 5, drums out for two "
            f"bars), make only that, render a window, play before and after. Then the normal loop: extend the form "
            f"in a Session Sheet, a part at a time.")


def _ffmpeg_ok():
    return bool(os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg'))


def _tune_balance(project, target=5.0):
    """ledger:M150: the tune sat +1 LU over a contrabass in one sketch and 7-13 LU over the others, because the
    faders are fixed. From a stems render: move the melody's fader so it sits `target` LU (4-6) over the other
    parts together. -> a line saying where it sits."""
    try:
        import pyloudnorm as pyln
        import soundfile as sf
    except ImportError:
        return 'balance not measured (no pyloudnorm)'
    sd = os.path.join(os.path.abspath(project), 'renders', 'stems')
    tune = os.path.join(sd, 'melody.wav')
    others = [os.path.join(sd, f) for f in os.listdir(sd) if f.endswith('.wav') and f != 'melody.wav'
              and not f.startswith('bus_')] if os.path.isdir(sd) else []
    if not os.path.exists(tune) or not others:
        return 'balance: no tune and parts to weigh'
    y, sr = sf.read(tune)
    rest = None
    for f in others:
        z, _ = sf.read(f)
        rest = z if rest is None else rest[:len(z)] + z[:len(rest)]
    m = pyln.Meter(sr)
    lt, lr = m.integrated_loudness(y), m.integrated_loudness(rest)
    if not (np.isfinite(lt) and np.isfinite(lr)):
        return 'balance: a part is silent'
    gap = lt - lr
    if target - 1.0 <= gap <= target + 1.0:
        return f"the tune sits {gap:+.1f} LU over the parts"
    move = float(np.clip(target - gap, -8.0, 8.0))
    P = _load(project)
    v = P.track('melody').get('volume_db', 0.0) + move
    track_set(project, 'melody', volume_db=round(v, 1))
    return f"the tune sat {gap:+.1f} LU over the parts; moved it {move:+.1f} dB to about {gap + move:+.1f}"


def _sketch_render(job):
    """One sketch, mixed and mastered: a stems render to weigh the tune, the balance, the loudness trim, the
    final render. Writes sketch_ready.json in the sketch's folder (sketch_wait reads it)."""
    sp, letter, mp3 = job['sp'], job['letter'], job['mp3']
    ready = os.path.join(sp, 'sketch_ready.json')
    try:
        render(sp, stems=True)
        bal = _tune_balance(sp)
        if 'moved it' in bal:
            render(sp)
        _loudness_trim(sp, job['lufs'])
        render(sp, out=f"sketch_{letter}", mp3=mp3)
        lufs, peak = _lufs_peak(os.path.join(sp, 'renders', 'latest.wav'))
        f = os.path.join(sp, 'renders', f"sketch_{letter}.{'mp3' if mp3 == 'also' else 'wav'}")
        got = {'letter': letter, 'file': f, 'lufs': lufs, 'peak': peak, 'balance': bal, 'at': time.time()}
    except Exception as e:                        # said by sketch_wait, never lost
        got = {'letter': letter, 'error': f"{type(e).__name__}: {e}", 'at': time.time()}
    with open(ready, 'w', encoding='utf8') as fh:
        json.dump(got, fh)
    job['done'] = got
    if 'error' in got:
        raise OpError(f"sketch {letter} did not render: {got['error']}")
    return got


def _sketch_finish(jobs):
    """The background child: render the sketches after the first, one by one (each waits its turn on the machine)."""
    for job in jobs:
        try:
            _sketch_render(job)
        except OpError:
            pass


def _sketch_spawn(jobs, sd):
    """Start the background child that renders `jobs`; its output goes to <sketches>/render.log."""
    import subprocess
    import sys
    os.makedirs(sd, exist_ok=True)
    log = open(os.path.join(sd, 'render.log'), 'a', encoding='utf8')
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008) if os.name == 'nt' else 0   # detached on Windows
    code = 'import json, sys; from ismail import api; api._sketch_finish(json.loads(sys.argv[1]))'
    subprocess.Popen([sys.executable, '-c', code, json.dumps(jobs)], cwd=os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
        start_new_session=os.name != 'nt')


@op()
def sketch_wait(project: str, wait: float = 180) -> str:
    """Wait for the sketches still rendering in the background (sketch returns the first as soon as it lands),
    up to `wait` seconds. -> each sketch: its file to play, or still rendering, or what went wrong."""
    sd = os.path.join(os.path.abspath(project), 'sketches')
    if not os.path.isdir(sd):
        raise OpError(f"no sketches in {project}: sketch(project, '<their words>') makes them")
    end = time.time() + max(0.0, float(wait))
    while True:
        rows = []
        for d in sorted(os.listdir(sd)):
            if not os.path.exists(os.path.join(sd, d, 'sketch.json')):
                continue
            r = os.path.join(sd, d, 'sketch_ready.json')
            rows.append((d, json.load(open(r, encoding='utf8')) if os.path.exists(r) else None))
        if all(x for _, x in rows) or time.time() >= end:
            break
        time.sleep(1.0)
    L = []
    for d, r in rows:
        if r is None:
            L.append(f"{d}: still rendering (the machine may be busy; sketch_wait again, or read {sd}/render.log)")
        elif r.get('error'):
            L.append(f"{d}: did not render: {r['error']}")
        else:
            L.append(f"{d}: ready, {r['lufs']:.1f} LUFS; {r['balance']}; play: {r['file']}")
    return '\n'.join(L) or 'no sketches yet'


def _loudness_trim(project, target):
    """Set the master limiter's gain so the render sits near `target` LUFS (a first listen should not be quiet);
    -> the gain change in dB (0 when already close)."""
    try:
        import pyloudnorm as pyln
        import soundfile as sf
        y, sr = sf.read(os.path.join(os.path.abspath(project), 'renders', 'latest.wav'))
        lufs = pyln.Meter(sr).integrated_loudness(y)
    except Exception:
        return 0.0
    if not np.isfinite(lufs) or abs(lufs - target) < 1.0:
        return 0.0
    P = _load(project)
    lim = [fx for fx in P.d['master']['fx'] if fx.get('type') == 'limiter']
    if not lim:
        return 0.0
    g = float(np.clip(target - lufs, -6.0, 15.0))
    lim[0]['gain_db'] = round(lim[0].get('gain_db', 0.0) + g, 1)
    P.save()
    return g


@op()
def lexicon_note(project: str = None, said: str = None, means=None, craft: str = None, where: str = None,
                 outcome: str = None, why: str = None, who: str = 'user', id: str = None) -> str:
    """Record what the person called something, and what it means in ismail. said: their words, verbatim ("too
    clean", "the snare is boxy"). means: the system terms it maps to, a list or ';'-separated ("fx eq peak 400 Hz -3
    dB on snare; analyze_timbre centroid"). craft: the role the word belongs to (composer, arranger, performer, sound
    designer, recording/mixing/mastering engineer, producer, dj, director, cinematographer, colourist, editor,
    choreographer, listener). outcome: open | worked | partly | missed, with why. Update an entry with id=... (means,
    craft, outcome, why); the words themselves never change. Words about the work only: never record emotion, mood
    or health. The file is local and shared by every session (lexicon_view shows where). Returns the entry and what
    the same words meant before."""
    from . import lexicon as LX
    song = os.path.basename(os.path.abspath(project)) if project else None
    before = LX.find(said, who) if said and not id else []
    try:
        e = LX.note(said, means, craft, song, where, outcome, why, who, id)
    except ValueError as ex:
        raise OpError(str(ex))
    L = [("updated " if id else "noted ") + LX.line(e)]
    if before:
        L.append("the same words before:")
        L += ['  ' + LX.line(b) for _, b, _ in before[:5]]
    if not e.get('means'):
        L.append(f"not mapped yet: when you know what it meant, lexicon_note(id={e['id']!r}, means=[...])")
    return '\n'.join(L)


@op()
def lexicon_find(project: str = None, text: str = None, who: str = 'user') -> str:
    """Look up the person's words in both directions: from their word to the system ("what did 'crisp' mean last
    time?") and from a system term to their word (text='high shelf' finds what they call it, so you can say it their
    way). Best matches first, with whether the change worked."""
    from . import lexicon as LX
    if not text:
        raise OpError("text: the person's words, or a system term (an op, param or effect name)")
    hits = LX.find(text, who)
    if not hits:
        return (f"no entries share words with {text!r}. If the person just used it, lexicon_note it; "
                f"lexicon_view lists everything")
    return '\n'.join(f"[{side}] " + LX.line(e) for _, e, side in hits)


@op()
def lexicon_view(project: str = None, who: str = 'user', craft: str = None, since: str = None) -> str:
    """The person's vocabulary as a readout: entries by craft and outcome, the share of trade words in what they
    say by month (description moving toward technique), new entries per week, entries not mapped yet, the newest
    ten. since='2026-10-01' limits it. Read it at the start of a session to speak the person's language."""
    from . import lexicon as LX
    try:
        return '\n'.join(LX.view(who, craft, since))
    except ValueError as ex:
        raise OpError(str(ex))


@op()
def presets_list(project: str = None) -> str:
    """List instrument presets with their type (voice presets name their voice module)."""
    return '\n'.join(f"{k:<16} {v['type']}" + (f" voice={v['voice']}" + (f" fn={v['fn']}" if v.get('fn') else '')
                                                 if v.get('voice') else '') for k, v in PRESETS.items())


def _voice_root(project):
    if project and os.path.isfile(os.path.join(project, 'project.json')):
        return os.path.abspath(project)
    return None


@op()
def voices_list(project: str = None) -> str:
    """List voice modules (code instruments kept as Python files) and mimic profiles (instruments measured from
    recordings): the song's own (<project>/voices/), those on $ISMAIL_VOICES, and the built-ins. Use one as {"type": "code", "voice": "<name>"} or its preset; voice_help(name)
    shows its velocity mapping, functions and parameters."""
    from . import voices
    rows = voices.available(_voice_root(project))
    prof = voices.mimic_profiles(_voice_root(project))
    if not rows and not prof:
        return '(no voices)'
    from . import sketch as SK
    sc = {v.get('voice') or v['name'] for v in SK.showcase()['voices']}
    mark = lambda n: '*' if n in sc else ' '
    def fn(n):
        try:
            return (voices.entry_fns(n, _voice_root(project)) or ['?'])[0]
        except Exception:
            return '?'
    L = [f"{mark(n)}{n:<16} {o:<9} fn={fn(n):<8} {s}" for n, o, s in rows]
    L.append("use: instrument={'type': 'code', 'voice': '<name>', 'fn': '<its fn= above>', 'params': {}, "
             "'tail': <seconds>} (voice_help(name) lists every function and its params)")
    if prof:
        L += [''] + [f"{mark(n)}{n:<16} {o:<9} {s}" for n, o, s in prof]
        L.append("use: instrument={'type': 'mimic', 'profile': '<name>', 'params': {}, 'tail': <seconds>} "
                 "(instrument_help(type='mimic') lists the params)")
    L.append("* showcase: measured voices a first sketch uses (ismail/voices/showcase.json; sketch builds from them)")
    return '\n'.join(L)


@op()
def voice_help(project: str = None, name: str = 'grand_piano') -> str:
    """Everything about one voice module: where it comes from, what velocity does, its functions and parameters."""
    from . import voices
    try:
        origin, path, meta, doc = voices.info(name, _voice_root(project))
    except voices.VoiceError as e:
        raise OpError(str(e))
    L = [f"{name} ({origin}: {path})", meta.pop('summary', '')]
    rigs = meta.pop('rigs', None)
    for k, v in meta.items():
        L.append(f"{k}: " + (json.dumps(v, indent=1) if isinstance(v, dict) else str(v)))
    if rigs:
        # the fx chains the voice was fitted with, one line each, ready to paste into fx
        L.append("rigs (instrument='preset:<preset>', then these fx in order):")
        for rn, r in rigs.items():
            L.append(f"  {rn} [preset:{r.get('preset', '')}] {r.get('note', '')}\n    fx: {json.dumps(r['fx'])}")
    if doc and doc.split('\n')[0] != L[1]:
        L.append(doc)
    return '\n'.join(L)


@op()
def instrument_help(project: str = None, type: str = 'synth') -> str:
    """Full parameter reference (with defaults) for an instrument type: synth (= sprite), sampler, kit, code, mimic, kick, snare, hat, clap, tom, noise_hit."""
    if type in ('synth', 'sprite'):
        return ("sprite, the oscillator synth (type 'synth' or 'sprite'; right for synth sounds, not for acoustic "
                "instruments: use mimic or a voice for those). params (defaults):\n" +
                json.dumps(inst_mod.SYNTH_DEFAULT, indent=1) +
                "\nosc params (defaults):\n" + json.dumps(inst_mod.OSC_DEFAULT, indent=1) +
                "\nnotes: waves " + ', '.join(inst_mod.OSC_WAVES) +
                "; fine/detune in cents; filter.type lp12 lp24 hp12 hp24 bp notch ladder; env_amount/keytrack/vel_amount"
                " in octaves; drive dB pre-filter; lfo = {target: pitch|cutoff|amp|pw|fm|oscs.N.pw, rate (Hz) or"
                " rate_beats (period in beats), shape sine|triangle|saw|ramp_down|square|sh, depth (semitones for pitch,"
                " octaves for cutoff, 0..1 for amp), delay s, retrigger}; fm_from=earlier osc index (phase mod, amount ~0.1-3);"
                " mono+glide(s) = legato slides on overlapping notes; additive needs partials [h1,h2,...]; table needs a"
                " single-cycle sound name. Automatable (automation_set param 'inst.X'): filter.cutoff filter.res drive"
                " gain_db noise pitch oscs.N.level oscs.N.pw oscs.N.fm_amount")
    if type == 'sampler':
        return "sampler params (defaults):\n" + json.dumps(inst_mod.SAMPLER_DEFAULT, indent=1) + \
            "\nroot = pitch at which the sound plays unshifted; one_shot plays the whole sample regardless of note length;" \
            " start/end/loop_* in seconds; filter = {type, cutoff, res} (cutoff automatable as inst.filter.cutoff)"
    if type == 'kit':
        return 'kit: {"type":"kit","map":{"C1":{"type":"kick",...},"D1":{"type":"sampler","sound":"snare1","one_shot":true}}}' \
               " - maps pitches to any instrument. GM-ish names: kick C1, snare D1, clap D#1, hat F#1, open hat A#1."
    if type == 'code':
        return ('code: {"type":"code","voice":"<name>","fn":"voice","params":{},"tail":1.0} plays a voice module '
                "(voices_list, voice_help; a song's own voices go in <project>/voices/<name>.py). Inline form: "
                '{"type":"code","code":"def voice(freq, t, vel, gate, sr):\\n    return np.sin(2*np.pi*freq*t)*np.exp(-t*4)",'
                ' "tail":0.3}. t is a time array (s) covering gate+tail, vel 0..1; return mono or (2,n); np and dsp '
                'are available inline. Prefer a voice module for anything you will reuse.')
    if type == 'mimic':
        from . import mimic
        return ('mimic: {"type":"mimic","profile":"<name>","params":{},"tail":1.0} plays an instrument measured from '
                'recordings (mimic_measure makes <project>/voices/<name>.mimic.json; voices_list shows the profiles). '
                'Partials with their own envelopes read a body curve at their current frequency, plus measured noise; '
                'unmeasured pitches blend the two nearest measured notes. params (defaults): ' +
                json.dumps(mimic.DEFAULT_PARAMS) + '. Velocity 0..1 against the measured velocity: louder and brighter '
                '(vel_bright dB per octave of partial number). For sustained kinds the note length is the bow/breath '
                'length; decaying kinds ring and are damped `damp` s after the note ends (null = let ring).')
    if type in inst_mod.DRUM_DEFAULTS:
        return f"{type} params (defaults): " + json.dumps(inst_mod.DRUM_DEFAULTS[type]) + \
            " (times in s, freqs in Hz, drive in dB). A drum synth ignores note pitch (any pitch triggers it; put" \
            " several in a kit to map pitches). Velocity scales level gently: 127 = 0 dB, 100 = -1.4, 70 = -3.2," \
            " 45 = -5.2, 1 = -10.5 dB, so analyze_drums reads pattern o/- hits as x or X; for audible ghost notes" \
            " use a separate quieter kit entry or gain_db."
    raise OpError(f"unknown type {type!r}; types: synth sampler kit code " + ' '.join(inst_mod.DRUM_DEFAULTS))


@op(mutates=True)
def instrument_set(project: str, track: str, instrument, merge: bool = True) -> str:
    """Set a track's instrument. merge=True deep-merges the given keys into the current instrument (oscs list is
    replaced whole); merge=False replaces it. instrument may be 'preset:<name>'."""
    P = _load(project)
    tr = P.track(track)
    if isinstance(instrument, str):
        new = _resolve_instrument(instrument, P)
    elif merge and tr.get('instrument') and instrument.get('type', tr['instrument']['type']) == tr['instrument']['type']:
        new = inst_mod.deep_merge(tr['instrument'], instrument)
        if 'oscs' in instrument:
            new['oscs'] = instrument['oscs']
        if 'map' in instrument:
            new['map'] = inst_mod.deep_merge(tr['instrument'].get('map', {}), instrument['map'])
        new = _resolve_instrument(new, P)
    else:
        new = _resolve_instrument(instrument, P)
    old = tr.get('instrument') or {}
    dropped = tr.get('model') and old.get('type') != new['type']
    if dropped:
        tr.pop('model')
    tr['instrument'] = new
    P.save()
    return f"instrument of {track!r} is now {new['type']}" + (
        f" (its model record no longer applies and was cleared: {provenance.of_track(tr, P.d, P.root)[1]})"
        if dropped else '')


@op(mutates=True)
def track_model(project: str, track: str, on=None, by: str = None) -> str:
    """Record what a track's sound is modeled on, shown by project_info and render, and read by `credits` to tell
    the sources a piece uses from the ones only consulted. on: the example it was measured from ('ref',
    'ref:<stem>', 'sound:<name>', a path (a file, or a folder of takes such as 'ref/birds/potoo'), or a note like
    'kit comp_2 of the ref drums'), a list of them when the sound comes from several sources (['ref/a.ogg (quiet
    stretches)', 'ref/b.ogg']), 'designed' for a sound made on purpose (most electronic music), or '' to clear. A
    note after a path goes in brackets. by: how (an op, a song script, 'ear exam'). Fits record it themselves
    (instrument_fit apply_to_track, track_fit apply=True); mimic profiles, measured library voices and imported
    samples need nothing."""
    P = _load(project)
    tr = P.track(track)
    ons = [str(x).strip() for x in on if str(x).strip()] if isinstance(on, (list, tuple)) else [(on or '').strip()]
    ons = [x for x in ons if x]
    if not ons:
        tr.pop('model', None)
        P.save()
        return f"cleared; {track!r} is now {' '.join(provenance.of_track(tr, P.d, P.root))}"
    if 'designed' in ons and len(ons) > 1:
        raise OpError("on='designed' stands alone: a designed sound has no sources (or list the sources it was "
                      "measured from, without 'designed')")
    for o in ons:
        if o.startswith('sound:') and o[6:] not in (P.d.get('sounds') or {}):
            raise OpError(f"no sound {o[6:]!r} in the bank (sound_list); sound_import the example first")
        is_ref = o == 'ref' or o.startswith('ref:')
        if is_ref and not P.d.get('reference'):
            raise OpError("the project has no reference; project_set(reference=<file>) or give the example's path")
        path = o.split(' (')[0].strip()                 # 'ref/birds/x.mp3 (a call profile)': the path, then a note
        looks_path = os.sep in path or '/' in path or os.path.splitext(path)[1].lower() in sourcesmod.MEDIA
        if looks_path and not is_ref and not o.startswith('sound:') and not any(
                os.path.exists(os.path.join(base, path)) for base in (P.root, sourcesmod.song_root(P.root))) \
                and not os.path.exists(path):
            raise OpError(f"{path!r} not found (looked in the project and the song folder); give an existing file "
                          f"or folder, 'ref', 'ref:<stem>', 'sound:<name>' or 'designed'")
    tr['model'] = {'on': ons[0] if len(ons) == 1 else ons, **({'by': by} if by else {})}
    P.save()
    return f"{track!r}: {' '.join(provenance.of_track(tr, P.d, P.root))}"


@op()
def credits(project: str, out: str = None, overwrite: bool = False, consulted: bool = False) -> str:
    """Write the piece's credits (CREDITS.md at the song root) from its SOURCES files (the recording, who made
    or played it, the licence, the link; columns about approval, local paths, dates and notes are left out), each
    track's model (measured from what, or designed) and its lineage (derived_from). Only the sources the piece uses
    are credited: a row is used when a track's model names its file, its folder or its id (track_model), a mimic
    profile or a sample came from it, or its table has an 'in the song' column saying yes. consulted=True adds the
    rest under 'Also consulted'. A plain list that repeats a table's rows (a SOURCES.txt of video ids) is left out.
    It says whether the piece holds any recorded audio (imported samples, audio clips) or only measurements rebuilt
    by synthesis. Files in ref/ with no SOURCES row are not credited: the reply lists them. Review the file before
    it goes public. out: another path; overwrite: replace an existing file (it may have been edited by hand)."""
    P = _load(project)
    path = os.path.abspath(out) if out else os.path.join(sourcesmod.song_root(P.root), 'CREDITS.md')
    if os.path.exists(path) and not overwrite:
        raise OpError(f"{path} exists (it may have been edited by hand): pass overwrite=True to replace it, or "
                      f"out=<another path> to compare")
    text, warn = sourcesmod.credits_md(P.root, P.d, consulted=consulted)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8', newline='\n') as f:
        f.write(text)
    s = sourcesmod.scan(P.root, P.d)
    L = [f"wrote {path}: {len(s['sources'])} SOURCES files, {len(s['files'])} files in ref/, "
         f"{len(P.d.get('tracks', {}))} parts" + (f", lineage {len(P.d.get('lineage', []))}" if P.d.get('lineage') else '')]
    audio = sourcesmod.plays_source_audio(P.d)
    L.append("  recorded audio in the piece: " + ('; '.join(f"{t} ({w})" for t, w in audio) if audio else
                                                 "none (it says the piece holds only measurements)"))
    L += [f"  {'NOT CREDITED' if not w.startswith('credited') else 'note'}: {w}" for w in warn]
    L.append("  review it before it goes public: credits name people and licences (CC BY and BY-SA require them)")
    return '\n'.join(L)


@op()
def instrument_show(project: str, track: str, full: bool = False) -> str:
    """Show a track's instrument JSON (full=True includes all defaults)."""
    P = _load(project)
    inst = P.track(track).get('instrument')
    if inst is None:
        return "(audio-only track, no instrument)"
    return json.dumps(inst_mod.normalize(inst) if full else inst, indent=1)


# ------------------------------------------------------------------ notes

@op(mutates=True)
def notes_write(project: str, track: str, bar: int, notes: str, mode: str = 'replace', bars: int = None,
                repeat: int = 1) -> str:
    """Write notes starting at `bar`. notes: one per line or ';'-separated: '<beat> <pitch> <dur_beats> [vel]
    [@offset]' with beat relative to the bar's beat 1 (0, 0.5, 1/3 ...). Chords: 'C4,E4,G4'. @offset nudges the
    sound off its beat in milliseconds ('@-40ms' = 40 ms early, so a late attack lands on the beat); the note still
    belongs to its beat. mode='replace' first clears the written span (length `bars`, default = bars the notes
    cover); mode='add' merges. repeat=N tiles the block N times."""
    P = _load(project)
    tr = P.track(track)
    try:
        rel = parse_notes(notes, offsets=True)
    except NotationError as e:
        raise OpError(str(e))
    if not rel:
        raise OpError("no notes parsed; format '<beat> <pitch> <dur> [vel]' e.g. '0 E2 0.5 110; 0.5 E3 0.5'")
    span = bars or _span_bars(rel, P.bpb)
    base = P.bar_to_beat(bar)
    removed = 0
    written = 0
    for r in range(repeat):
        b0 = base + r * span * P.bpb
        if mode == 'replace':
            removed += _clear(tr, b0, b0 + span * P.bpb)
        for s, p, d, v, off in rel:
            tr['notes'].append([round(b0 + s, 6), p, d, v] + ([off] if off else []))
            written += 1
    tr['notes'].sort()
    P.save()
    msg = f"{track}: wrote {written} notes in bars {bar}-{bar + span * repeat - 1}" + (f", replaced {removed}" if removed else '')
    return msg + _kit_warning(tr, rel)


def _kit_warning(tr, rel):
    inst = tr.get('instrument') or {}
    if inst.get('type') == 'kit':
        mapped = {pitch_to_midi(k) for k in inst['map']}
        miss = sorted({n[1] for n in rel if n[1] not in mapped})
        if miss:
            return f"\nWARNING: pitches {[midi_to_name(m) for m in miss]} are not in the kit map (silent). mapped: " \
                   f"{[midi_to_name(m) for m in sorted(mapped)]}"
    return ''


@op(mutates=True)
def pattern_write(project: str, track: str, bar: int, lanes: dict, step: float = 0.25, repeat: int = 1,
                  mode: str = 'replace', dur: float = None) -> str:
    """Step-sequence: lanes = {pitch: 'x...x...X...x..o'} ('.' rest, X accent 127, x 100, o 70, - 45, '_' tie).
    step = beats per character (0.25 = 16ths). Pattern length (chars*step) sets the span; repeat tiles it."""
    P = _load(project)
    tr = P.track(track)
    rel = []
    span_beats = 0
    for pitch, pat in lanes.items():
        try:
            m = pitch_to_midi(pitch)
            hits, length = parse_steps(pat, step, dur)
        except NotationError as e:
            raise OpError(str(e))
        span_beats = max(span_beats, length)
        rel += [(s, m, d, v) for s, d, v in hits]
    span = max(1, math.ceil(span_beats / P.bpb - 1e-9))
    base = P.bar_to_beat(bar)
    removed = 0
    pitches = {pitch_to_midi(p) for p in lanes}
    for r in range(repeat):
        b0 = base + r * span_beats
        if mode == 'replace':
            before = len(tr['notes'])
            tr['notes'] = [n for n in tr['notes'] if not (b0 <= n[0] < b0 + span_beats and n[1] in pitches)]
            removed += before - len(tr['notes'])
        for s, p, d, v in rel:
            tr['notes'].append([round(b0 + s, 6), p, d, v])
    tr['notes'].sort()
    P.save()
    end_bar = bar + math.ceil(span_beats * repeat / P.bpb - 1e-9) - 1
    odd = abs(span_beats / P.bpb - round(span_beats / P.bpb)) > 1e-6
    return f"{track}: {len(rel) * repeat} hits in bars {bar}-{end_bar} (lanes {list(lanes)})" + \
        (f", replaced {removed} on those pitches" if removed else '') + _kit_warning(tr, rel) + \
        (f"\nWARNING pattern is {span_beats:g} beats, not a whole number of bars ({P.bpb} beats each): it spills "
         f"into bar {end_bar} and a later write there will overwrite it" if odd else '')


@op()
def notes_read(project: str, track: str, bars: list = None, view: str = 'list', step: float = 0.25) -> str:
    """Read a track's notes in bars [a, b] (inclusive). view='list' (bar, beat in the bar, pitch, dur, vel, and an
    @offset when a note is nudged), 'roll' (ASCII piano roll, max 8 bars, on the beats), or 'rel' (notes_write
    format relative to bar a - copy/edit/write back, offsets included). A track offset is named on the first line."""
    P = _load(project)
    tr = P.track(track)
    a, b = bars or [1, P.d['length_bars']]
    b0, b1 = P.bar_to_beat(a), P.bar_to_beat(b + 1)
    sel = [n for n in tr['notes'] if b0 <= n[0] < b1]
    toff = (f"(track offset {tr['offset_ms']:+g} ms: every sound of {track} lands that far from its beat)\n"
            if tr.get('offset_ms') else '')
    if view == 'roll':
        if b - a + 1 > 8:
            raise OpError("roll view is limited to 8 bars; narrow `bars`")
        head = f"{track} bars {a}-{b} (one char per {step:g} beat; # = note start, = = held)"
        return toff + head + '\n' + piano_roll(sel, b0, b1 - b0, step, P.bpb)
    if view == 'rel':
        return toff + ('\n'.join(f"{fmt_num(n[0] - b0)} {midi_to_name(n[1])} {fmt_num(n[2])} {n[3]}"
                                 + (f" {fmt_offset(n[4])}" if len(n) > 4 and n[4] else '') for n in sel) or '(empty)')
    if len(sel) > 300:
        return toff + format_notes(sel[:300], P.bpb) + f"\n... {len(sel) - 300} more; narrow `bars`"
    return toff + (format_notes(sel, P.bpb) or '(no notes in range)')


@op(mutates=True)
def notes_clear(project: str, track: str, bars: list, pitches: list = None) -> str:
    """Delete notes starting in bars [a, b]; optionally only the given pitches."""
    P = _load(project)
    tr = P.track(track)
    b0, b1 = P.bar_to_beat(bars[0]), P.bar_to_beat(bars[1] + 1)
    ps = {pitch_to_midi(p) for p in pitches} if pitches else None
    before = len(tr['notes'])
    tr['notes'] = [n for n in tr['notes'] if not (b0 <= n[0] < b1 and (ps is None or n[1] in ps))]
    P.save()
    return f"{track}: removed {before - len(tr['notes'])} notes from bars {bars[0]}-{bars[1]}"


@op(mutates=True)
def notes_copy(project: str, track: str, from_bars: list, to_bar: int, times: int = 1, to_track: str = None,
               transpose: int = 0, mode: str = 'replace') -> str:
    """Copy notes from bars [a, b] of `track` to start at `to_bar` (of to_track, default same), tiled `times`."""
    P = _load(project)
    src = P.track(track)
    dst = P.track(to_track or track)
    a, b = from_bars
    b0, b1 = P.bar_to_beat(a), P.bar_to_beat(b + 1)
    block = [n for n in src['notes'] if b0 <= n[0] < b1]
    span = b1 - b0
    for r in range(times):
        d0 = P.bar_to_beat(to_bar) + r * span
        if mode == 'replace':
            _clear(dst, d0, d0 + span)
        for n in block:
            dst['notes'].append([round(d0 + n[0] - b0, 6), n[1] + transpose, n[2], n[3]] + list(n[4:5]))
    dst['notes'].sort()
    P.save()
    return f"copied {len(block)} notes x{times} from {track} bars {a}-{b} to {to_track or track} bar {to_bar}" \
           f"-{to_bar + (b - a + 1) * times - 1}"


@op(mutates=True)
def notes_transform(project: str, track: str, bars: list, transpose: int = 0, velocity: int = None,
                    vel_scale: float = None, shift_beats: float = 0.0, quantize: float = None, dur_scale: float = None,
                    dur_set: float = None, legato: bool = False, pitches: list = None,
                    offset_ms: float = None) -> str:
    """Edit notes in bars [a, b]: transpose (semitones), velocity (set) / vel_scale, shift_beats, quantize (grid in
    beats; a note's @offset is kept, so a nudge survives quantizing), dur_scale / dur_set, legato (extend each note to
    the next onset), offset_ms (set each note's nudge in ms, negative = earlier; 0 removes it). pitches limits to
    those pitches."""
    P = _load(project)
    tr = P.track(track)
    b0, b1 = P.bar_to_beat(bars[0]), P.bar_to_beat(bars[1] + 1)
    ps = {pitch_to_midi(p) for p in pitches} if pitches else None
    sel = [n for n in tr['notes'] if b0 <= n[0] < b1 and (ps is None or n[1] in ps)]
    for n in sel:
        n[1] += transpose
        if quantize:
            n[0] = round(round(n[0] / quantize) * quantize, 6)
        n[0] = round(n[0] + shift_beats, 6)
        if velocity is not None:
            n[3] = velocity
        if vel_scale:
            n[3] = int(np.clip(round(n[3] * vel_scale), 1, 127))
        if dur_scale:
            n[2] *= dur_scale
        if dur_set:
            n[2] = dur_set
        if offset_ms is not None:
            from .notation import parse_offset
            try:
                parse_offset(f"@{offset_ms}")
            except NotationError as e:
                raise OpError(str(e))
            del n[4:]
            if offset_ms:
                n.append(float(offset_ms))
    if legato:
        starts = sorted({n[0] for n in sel})
        for n in sel:
            nxt = [s for s in starts if s > n[0]]
            if nxt:
                n[2] = nxt[0] - n[0]
    tr['notes'].sort()
    P.save()
    return f"{track}: transformed {len(sel)} notes in bars {bars[0]}-{bars[1]}"


# ------------------------------------------------------------------ fx & automation

def _fx_list(P, target):
    if target == 'master':
        return P.d['master'].setdefault('fx', [])
    if target.startswith('bus:'):
        b = target[4:]
        if b not in P.d['buses']:
            raise OpError(f"no bus {b!r}; buses: {list(P.d['buses'])}")
        return P.d['buses'][b].setdefault('fx', [])
    return P.track(target).setdefault('fx', [])


@op(mutates=True)
def fx_add(project: str, target: str, fx: dict, index: int = None) -> str:
    """Add an effect to target ('<track>', 'bus:<name>' or 'master'). fx = {'type': ..., params}; see fx_help.
    index = position in chain (default end)."""
    P = _load(project)
    lst = _fx_list(P, target)
    try:
        fxmod.normalize(fx)
    except fxmod.FxError as e:
        raise OpError(str(e))
    if fx['type'] == 'compressor' and fx.get('sidechain'):
        P.track(fx['sidechain'])
    if fx['type'] == 'duck' and fx.get('source'):
        P.track(fx['source'])
    i = len(lst) if index is None else index
    lst.insert(i, fx)
    P.save()
    chain = ', '.join(f"{j}:{f['type']}" for j, f in enumerate(lst))
    return f"{target}: fx chain now [{chain}]"


@op(mutates=True)
def fx_set(project: str, target: str, index: int, params: dict) -> str:
    """Update params of effect #index on target (merge)."""
    P = _load(project)
    lst = _fx_list(P, target)
    if not 0 <= index < len(lst):
        raise OpError(f"{target} has fx indices 0..{len(lst) - 1}: {[f['type'] for f in lst]}")
    new = dict(lst[index], **params)
    try:
        fxmod.normalize(new)
    except fxmod.FxError as e:
        raise OpError(str(e))
    lst[index] = new
    P.save()
    return f"{target} fx {index} ({new['type']}) updated"


@op(mutates=True)
def fx_remove(project: str, target: str, index: int) -> str:
    """Remove effect #index from target. Automation on later fx indices is re-pointed automatically."""
    P = _load(project)
    lst = _fx_list(P, target)
    if not 0 <= index < len(lst):
        raise OpError(f"{target} has fx indices 0..{len(lst) - 1}")
    removed = lst.pop(index)
    if target in P.d['tracks']:
        auto = P.d['tracks'][target].get('automation', {})
        newauto = {}
        for k, v in auto.items():
            if k.startswith('fx.'):
                _, i, p = k.split('.', 2)
                i = int(i)
                if i == index:
                    continue
                k = f"fx.{i - 1 if i > index else i}.{p}"
            newauto[k] = v
        P.d['tracks'][target]['automation'] = newauto
    P.save()
    return f"removed {removed['type']} from {target}"


@op()
def fx_help(project: str = None, type: str = None) -> str:
    """Effect types with default params; type=<name> for one. Automatable params listed per type."""
    types = [type] if type else list(fxmod.FX_DEFAULTS)
    out = []
    for t in types:
        if t not in fxmod.FX_DEFAULTS:
            raise OpError(f"unknown fx {t!r}; types: {', '.join(fxmod.FX_DEFAULTS)}")
        out.append(f"{t}: {json.dumps(fxmod.FX_DEFAULTS[t])}  automatable: {list(fxmod.AUTOMATABLE.get(t, ()))}")
        if t in rigmod.DOCS:
            out.append(f"    {rigmod.DOCS[t]}")
    out.append("notes: compressor.sidechain = track name; duck.source = track name (ducks on its note-ons) or every_beats;"
               " gate.pattern uses step chars; vocoder.modulator = track name or 'sound:<name>'; eq.bands = "
               "[{type: peak|lowshelf|highshelf|lowcut|highcut|notch|bandpass, freq, gain_db, q, slope 12|24}]")
    return '\n'.join(out)


INST_CONTINUOUS = ('filter.cutoff', 'filter.res', 'drive', 'gain_db', 'noise', 'pitch')


@op(mutates=True)
def automation_set(project: str, track: str, param: str, points: list, mode: str = 'replace') -> str:
    """Automate a parameter over time (track='master' for a master volume fade, track='bus:<name>' for a bus's
    volume_db or fx.<index>.<param>, e.g. a filter sweep on a drum bus). points = [[bar, value], ...] with fractional bars allowed (17.5 = beat 3 of
    bar 17); linear between points (log for Hz params), held before/after. param: 'volume_db' (dB OFFSET added to
    the track fader: 0 = unchanged, -30 = fade out), 'pan',
    'inst.<path>' (inst.filter.cutoff, inst.oscs.0.level ... see instrument_help), 'inst.lane.<name>' (an expression
    lane of a performer voice, e.g. a guitar's bend in semitones: voice_help lists its lanes), 'fx.<index>.<param>'.
    mode='merge' keeps existing points outside the new points' span."""
    P = _load(project)
    if track == 'master':
        if param != 'volume_db':
            raise OpError("the master accepts only 'volume_db' automation (a dB offset, e.g. a fade out)")
        tr = P.d['master']
    elif track.startswith('bus:'):
        tr = _bus(P, track)
        if not (param == 'volume_db' or param.startswith('fx.')):
            raise OpError("a bus accepts 'volume_db' or 'fx.<index>.<param>' automation")
    else:
        tr = P.track(track)
    if param in ('volume_db', 'pan'):
        pass
    elif param.startswith('inst.'):
        pp = param[5:]
        if pp.startswith('lane.') and len(pp) > 5:
            pass  # expression lane of a performer voice (bend, vib, mute ...): see voice_help(<voice>)
        elif not (pp in INST_CONTINUOUS or (pp.startswith('oscs.') and pp.split('.')[-1] in ('level', 'pw', 'fm_amount'))):
            raise OpError(f"inst param {pp!r} is not automatable; use one of {INST_CONTINUOUS} or oscs.N.level|pw|fm_amount")
    elif param.startswith('fx.'):
        try:
            _, i, pname = param.split('.', 2)
            f = tr['fx'][int(i)]
        except (ValueError, IndexError):
            raise OpError(f"bad fx param {param!r}; track fx: {[(j, f['type']) for j, f in enumerate(tr.get('fx', []))]}")
        if pname not in fxmod.AUTOMATABLE.get(f['type'], ()):
            raise OpError(f"{f['type']}.{pname} not automatable; automatable: {fxmod.AUTOMATABLE.get(f['type'], ())}")
    else:
        raise OpError("param must be volume_db, pan, inst.<path> or fx.<index>.<param>")
    beats = [[round(P.bar_to_beat(b), 6), float(v)] for b, v in points]
    auto = tr.setdefault('automation', {})
    if mode == 'merge' and param in auto:
        lo, hi = min(b for b, _ in beats), max(b for b, _ in beats)
        beats = sorted([p for p in auto[param] if not lo <= p[0] <= hi] + beats)
    auto[param] = sorted(beats)
    P.save()
    return f"{track}.{param}: {len(beats)} points from bar {points[0][0]} to {points[-1][0]}"


def _bus(P, track):
    b = track[4:]
    if b not in P.d.get('buses', {}):
        raise OpError(f"no bus {b!r}; buses: {list(P.d.get('buses', {})) or 'none'} (bus_add to create)")
    return P.d['buses'][b]


@op(mutates=True)
def automation_clear(project: str, track: str, param: str = None) -> str:
    """Remove automation for one param (or all when param is None)."""
    P = _load(project)
    tr = P.d['master'] if track == 'master' else _bus(P, track) if track.startswith('bus:') else P.track(track)
    if param:
        tr.get('automation', {}).pop(param, None)
    else:
        tr['automation'] = {}
    P.save()
    return f"cleared automation {param or '(all)'} on {track}"


@op()
def automation_read(project: str, track: str) -> str:
    """List automation lanes on a track ('master' and 'bus:<name>' too) as [bar, value] points."""
    P = _load(project)
    tr = P.d['master'] if track == 'master' else _bus(P, track) if track.startswith('bus:') else P.track(track)
    auto = tr.get('automation', {})
    return '\n'.join(f"{k}: " + ' '.join(f"[{fmt_num(b / P.bpb + 1)}, {fmt_num(v)}]" for b, v in pts)
                     for k, pts in auto.items()) or '(none)'


# ------------------------------------------------------------------ sounds

def _write_sound(P, name, y, sr, note=''):
    from .render import write_wav
    rel = os.path.join('sounds', f"{name}.wav")
    write_wav(os.path.join(P.root, rel), y, sr)
    P.d.setdefault('sounds', {})[name] = {"file": rel.replace('\\', '/'), "note": note,
                                          "sec": round(y.shape[1] / sr, 3)}


@op(mutates=True)
def sound_make(project: str, name: str, instrument, notes: str = '0 C4 1', fx: list = None, tail_sec: float = 1.0,
               normalize: bool = True, describe: bool = True) -> str:
    """Synthesize a sound into the bank: play `notes` (notes_write format, beats at project tempo) on `instrument`
    through `fx`. Use it as a sampler source, a wavetable, a vocoder modulator or an audio clip. Returns a timbre
    description of the result."""
    from .render import Renderer
    P = _load(project)
    inst = _resolve_instrument(instrument, P)
    tmp = {"bpm": P.d['bpm'], "beats_per_bar": P.bpb, "sr": 44100, "offset_sec": 0.0, "tail_sec": tail_sec,
           "tracks": {"x": {"instrument": inst, "notes": [], "fx": fx or []}}, "buses": {}, "master": {"fx": []},
           "sounds": P.d.get('sounds', {})}
    try:
        rel = parse_notes(notes)
    except NotationError as e:
        raise OpError(str(e))
    tmp['tracks']['x']['notes'] = [[s, p, d, v] for s, p, d, v in rel]
    end = max(s + d for s, _, d, _ in rel)
    tmp['length_bars'] = max(1, math.ceil(end / P.bpb))
    R = Renderer(tmp, P.root, cache=False)
    y, _ = R.run()
    # trim to notes + tail and strip trailing silence
    n = int((end * 60 / P.d['bpm'] + tail_sec) * 44100)
    y = y[:, :n]
    a = np.max(np.abs(y), axis=0)
    nz = np.nonzero(a > 1e-4)[0]
    if len(nz) == 0:
        raise OpError("the sound rendered silent; check instrument levels/notes")
    y = y[:, :nz[-1] + 1]
    if normalize:
        y = y / (np.max(np.abs(y)) + 1e-12) * 0.891
    _write_sound(P, name, y, 44100, note=f"made from {inst['type']}")
    P.save()
    msg = f"sound {name!r}: {y.shape[1] / 44100:.2f}s"
    if describe:
        _, txt = A.timbre(os.path.join(P.root, P.d['sounds'][name]['file']), 0, y.shape[1] / 44100)
        msg += '\n' + txt
    return msg


@op(mutates=True)
def sound_import(project: str, name: str, source: str, start_sec: float = None, end_sec: float = None,
                 bars: list = None, normalize: bool = False) -> str:
    """Import audio into the bank from a file or any analysis source (ref, ref:vocals, track:x, render...), optionally
    a time slice (seconds) or bars [a, b] on the project grid. Warns on capture faults (peaks flattened by a browser
    mic or a limiter, nothing above 8 kHz from a Bluetooth mic) before the sound is measured or played as a sample."""
    import soundfile as sf
    P = _load(project)
    path, g = P.source(source, bars)
    y, sr = sf.read(path, dtype='float64', always_2d=True)
    y = y.T
    if bars:
        start_sec, end_sec = g.bar_time(bars[0]), g.bar_time(bars[1] + 1)
    a = int((start_sec or 0) * sr)
    b = int(end_sec * sr) if end_sec else y.shape[1]
    y = y[:, a:b]
    if y.shape[0] == 1:
        y = np.vstack([y, y])
    from . import capture
    warn = capture.check(y, sr)['warnings']                 # before normalizing, which would move the plateau
    if normalize:
        y = y / (np.max(np.abs(y)) + 1e-12) * 0.891
    _write_sound(P, name, y, sr, note=f"imported from {source}")
    P.save()
    return f"sound {name!r}: {y.shape[1] / sr:.2f}s from {source}" + ''.join(f"\nRECORDING WARNING: {w}" for w in warn)


@op()
def sound_list(project: str) -> str:
    """List the sound bank."""
    P = _load(project)
    s = P.d.get('sounds', {})
    return '\n'.join(f"{k:<20} {v.get('sec', '?')}s  {v.get('note', '')}" for k, v in s.items()) or '(empty bank)'


@op(mutates=True)
def audio_place(project: str, track: str, sound: str, bar: float, gain_db: float = 0.0, offset_sec: float = 0.0,
                length_beats: float = None) -> str:
    """Place a bank sound on a track's timeline at `bar` (fractional ok) as an audio clip."""
    P = _load(project)
    tr = P.track(track)
    if sound not in P.d.get('sounds', {}):
        raise OpError(f"no sound {sound!r}; sound_list")
    tr.setdefault('audio', []).append({"sound": sound, "at_beat": P.bar_to_beat(bar), "gain_db": gain_db,
                                       "offset_sec": offset_sec, "length_beats": length_beats})
    P.save()
    return f"{track}: placed {sound} at bar {bar}"


# ------------------------------------------------------------------ render

@op()
def render(project: str, bars: list = None, tracks: list = None, stems: bool = False, out: str = None,
           cache: bool = True, mp3: str = 'none', wait: str = '10m') -> str:
    """Render to renders/latest.wav (and renders/<out>.wav). bars=[a,b] renders a window (fast iteration); tracks
    limits to those tracks (+ their sidechain sources, which stay silent); stems=True writes per-track files for
    analysis as 'track:<name>'. Analysis of a windowed render and its stems still uses song bar numbers (bars outside
    the window raise). mp3='also' writes renders/<out or latest>.mp3 next to the wav, mp3='only' writes the named
    render as mp3 only (latest.wav is always written: analysis reads it); needs ffmpeg on PATH or $ISMAIL_FFMPEG.
    wait: when the machine is busy, stand in line this long ('10m' default, '0' refuses at once).
    Returns levels, clipping and timing."""
    if mp3 not in ('none', 'also', 'only'):
        raise OpError("mp3 must be 'none', 'also' (wav + mp3) or 'only' (the named render as mp3 only)")
    from .render import Renderer, write_wav, prune_cache, RenderError
    try:
        wait_s = machine.duration_s(wait) if str(wait).strip() not in ('', '0', 'none', 'None') else None
    except ValueError as e:
        raise OpError(f"wait: {e}")
    P = _load(project)
    if tracks:
        for t in tracks:
            P.track(t)
    try:
        R = Renderer(P.d, P.root, bars[0] if bars else None, (bars[1] + 1) if bars else None, tracks, cache)
        cache_gb, out_gb = _render_disk_gb(P.d, R.n, tracks, cache, stems, out and mp3 != 'only')
        t_line = time.time()
        with machine.slot('cpu', f"render {os.path.basename(P.root)}" + (f" bars {bars[0]}-{bars[1]}" if bars else ''),
                          mem_gb=_render_gb(P.d, R.n, tracks), disk_gb=cache_gb + out_gb, disk_path=P.root,
                          disk_hint=(f"cache=False skips the track cache ({cache_gb:.1f} GB of it)" if cache_gb else
                                     '') + (", a shorter window (bars=) writes less" if not bars else ''),
                          wait=wait_s):     # a newcomer's agent stands in line, as sketch's renders do (ledger:M152)
            waited = time.time() - t_line
            y, st = R.run()
    except (RenderError, fxmod.FxError, inst_mod.InstrumentError) as e:
        raise OpError(f"render failed: {e}")
    rd = os.path.join(P.root, 'renders')
    written = ['latest.wav'] + ([out + '.wav'] if out and mp3 != 'only' else [])
    write_wav(os.path.join(rd, 'latest.wav'), y, R.sr)
    if out and mp3 != 'only':
        write_wav(os.path.join(rd, out + '.wav'), y, R.sr)
    mp3_note = ''
    if mp3 != 'none':
        from .render import write_mp3
        name = (out or 'latest') + '.mp3'
        try:
            write_mp3(os.path.join(rd, 'latest.wav'), os.path.join(rd, name))
        except RuntimeError as e:
            raise OpError(f"render finished (renders/latest.wav) but the mp3 failed: {e}")
        from .tags import tag_mp3
        song = P.d.get('name') or os.path.basename(P.root)
        tag_mp3(os.path.join(rd, name), title=song if not out or out == 'latest' else f"{song} ({out})",
                artist=P.d.get('artist'), album=P.d.get('album') or song)
        mp3_note = f" + renders/{name} (tagged: {song}, made with ismail)"
    if stems:
        sd = os.path.join(rd, 'stems')
        os.makedirs(sd, exist_ok=True)
        for k, v in st.items():
            write_wav(os.path.join(sd, k.replace(':', '_') + '.wav'), v, R.sr)
            written.append('stems/' + k.replace(':', '_') + '.wav')
    _record_window(rd, written, None if R.full else
                   {'bars': [bars[0], min(bars[1], P.d['length_bars'])], 'start_sec': R.offset + R.win_b0 * R.spb})
    prune_cache(P.root)
    peak = 20 * np.log10(np.max(np.abs(y)) + 1e-12)
    clip = float(np.mean(np.abs(y) >= 0.999)) * 100
    try:
        import pyloudnorm as pyln
        lufs = pyln.Meter(R.sr).integrated_loudness(y.T)
    except Exception:
        lufs = float('nan')
    L = [f"rendered {y.shape[1] / R.sr:.1f}s" + (f" (bars {bars[0]}-{bars[1]})" if bars else '') +
         f" in {R.elapsed:.1f}s -> renders/latest.wav{' + renders/' + out + '.wav' if out and mp3 != 'only' else ''}{mp3_note}",
         f"master: {lufs:.1f} LUFS, peak {peak:.1f} dBFS" + (f", CLIPPING {clip:.2f}% of samples (lower levels or add limiter)" if clip > 0.001 else '')]
    if waited >= 5:
        L.append(f"  waited {waited / 60:.1f} min in line for the machine (wait='0' refuses at once instead)")
    if R.full and lufs < -20:
        L.append(f"  QUIET: {lufs:.1f} LUFS is under every genre target (classical and ambient sit at -18 to -16): raise "
                 f"the master (limiter gain_db) or the faders; at low playback volume this reads as nothing")
    L.append("  per track (after its fx and fader, scaled by the master chain's gain, so tracks sum to the mix;"
             " buses listed as bus:<name>):")
    for k, v in st.items():
        pk = 20 * np.log10(np.max(np.abs(v)) + 1e-12)
        rms = 10 * np.log10(np.mean(v ** 2) + 1e-12)
        s = R.stats.get(k, {})
        L.append(f"  {k:<14} peak {pk:6.1f} rms {rms:6.1f} dB" + (f"  ({'cached' if s.get('cached') else 'rendered in ' + str(s['sec']) + 's'})" if s else '')
                 + ('  SILENT - check notes/instrument/mute' if pk < -90 else ''))
    for (trk, i), gr in R.gain_reduction.items():
        if gr < -0.5:
            L.append(f"  {trk} fx {i}: max gain reduction {gr:.1f} dB")
    for trk, k in R.early.items():
        L.append(f"  {trk}: {k} nudged notes would sound before 0 s and start at 0 s instead; "
                 f"project_set(offset_sec=...) a little later than now gives their lead-in room")
    mfx = [f.get('type') for f in P.d.get('master', {}).get('fx', [])]
    if 'limiter' not in mfx:
        L.append("  master: no limiter (fx_add target='master' fx={'type': 'limiter', 'ceiling_db': -0.3} catches peaks)")
    elif not any(t == 'master' and gr < -0.5 for (t, _), gr in R.gain_reduction.items()):
        L.append("  master limiter: idle (gain reduction under 0.5 dB)")
    short = provenance.summary(P.d, P.root, tracks)[1]
    if short:
        L.append(short)
    short = sourcesmod.summary(P.root, P.d)[1]
    if short:
        L.append(short)
    return '\n'.join(L)


def _render_gb(d, n, only=None):
    """Peak memory estimate of one render of n samples (ismail.machine.render_memory_gb)."""
    from .render import Renderer
    tracks = [t for k, t in d['tracks'].items() if only is None or k in only]
    deps = set().union(*(Renderer.deps(None, t) for t in d['tracks'].values()))
    return machine.render_memory_gb(n, len(tracks), len(d.get('buses', {})), len(deps))


def _render_disk_gb(d, n, only, cache, stems, named):
    """-> (GB the track cache may write: float32 per track, at most every track; GB of wav: 24-bit latest.wav, the
    named copy, the stems)."""
    k = len([t for t in d['tracks'] if only is None or t in only])
    cache_gb = k * n * 2 * 4 / 2 ** 30 if cache else 0.0
    wavs = 1 + bool(named) + ((k + len(d.get('buses', {}))) if stems else 0)
    return cache_gb, wavs * n * 2 * 3 / 2 ** 30


def _record_window(rd, files, window):
    """Remember where each written file starts in the song (full renders start at song t=0 and are not listed)."""
    wf = os.path.join(rd, WINDOWS_FILE)
    try:
        with open(wf, encoding='utf8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    for k in files:
        d.pop(k, None)
        if window:
            d[k] = window
    with open(wf, 'w', encoding='utf8') as f:
        json.dump(d, f, indent=1)


# ------------------------------------------------------------------ analysis

def _grid(P, bpm, offset_sec):
    return P.grid(bpm, offset_sec)


@op()
def analyze_grid(project: str, source: str = None, bpm_hint: float = None) -> str:
    """Estimate tempo, the time of bar 1 (kick, harmony, section changes and snare on 2 and 4 vote), the tuning
    offset from A440 and, with a reference drum stem, the swing. Use the result in project_new/project_set."""
    P = _load(project)
    g, txt = A.beat_grid(P.resolve_audio(source), bpm_hint, P.bpb)
    ref = P.d.get('reference') or {}
    drums = os.path.join(ref.get('stems_dir') or '', 'drums.wav')
    if P.auto_source(source) in ('ref', 'ref:drums') and ref.get('stems_dir') and os.path.exists(drums):
        txt += '\n' + A.swing(drums, g['bpm'], g['offset_sec'])[1]
    else:
        txt += "\nswing: not measured (needs a drum stem: separate(source='ref'), then analyze_swing)"
    return txt


@op()
def analyze_overview(project: str, source: str = None) -> str:
    """Loudness, key and a section map (bars, level, dominant bands, chroma) of an audio source on the project grid."""
    P = _load(project)
    return A.overview(*P.source(source))[1]


@op()
def analyze_bars(project: str, source: str = None, bars: list = None) -> str:
    """Per-bar table (max 32 bars): level dB, 6 band energies, spectral centroid, onset count, chroma chord."""
    P = _load(project)
    return A.bar_table(*P.source(source, bars), bars)[1]


@op()
def analyze_chords(project: str, source: str = None, bars: list = None, per_bar: int = 2) -> str:
    """Chord + bass note per 1/per_bar of a bar (max 32 bars)."""
    P = _load(project)
    return A.chords(*P.source(source, bars), bars, per_bar)[1]


@op()
def analyze_melody(project: str, source: str = 'ref:other', bars: list = None, fmin: str = 'C1', fmax: str = 'C7',
                   quant: float = 0.25, min_conf: float = 0.0) -> str:
    """Monophonic pitch -> note list in notes_write format (max 16 bars). Best on a stem or a soloed line."""
    P = _load(project)
    return A.melody(*P.source(source, bars), bars, fmin, fmax, quant, min_conf=min_conf)[1]


@op()
def analyze_notes(project: str, source: str = 'ref:other', bars: list = None, quant: float = 0.25,
                  threshold: float = 0.35, fmin: str = 'C1', max_poly: int = 6) -> str:
    """Polyphonic note estimate -> notes_write format (max 16 bars). Raise threshold to drop weak/ghost notes."""
    P = _load(project)
    return A.transcribe(*P.source(source, bars), bars, quant, threshold=threshold, fmin=fmin,
                        max_poly=max_poly)[1]


@op()
def analyze_pitches(project: str, source: str = 'ref:other', bars: list = None, per_bar: int = 4,
                    max_notes: int = 6, fmin: str = 'C1', floor_db: float = -30) -> str:
    """Pitches sounding in each 1/per_bar of a bar, loudest first with dB (max 16 bars). Best view for pads,
    drones, sustained basses and chords; use analyze_melody for fast monophonic lines."""
    P = _load(project)
    return A.pitches(*P.source(source, bars), bars, per_bar, max_notes, fmin, floor_db)[1]


@op()
def analyze_drums(project: str, source: str = 'ref:drums', bars: list = None, steps_per_beat: int = 4,
                  sens: float = 1.0) -> str:
    """Drum hits as step strings per bar in 3 lanes (low/snare/hat bands), max 16 bars. Paste into pattern_write.
    Best on an isolated drum source: 'track:<drum track>' after render(stems=True), or 'ref:drums'. On a full mix
    ('render', 'ref') the lanes are band activity, so bass, pads and leads also register as hits."""
    P = _load(project)
    txt = A.drums(*P.source(source, bars), bars, steps_per_beat, sens=sens)[1]
    if source in ('render', 'ref'):
        txt = (f"NOTE {source!r} is a full mix: lanes show band activity, not just drums (other parts leak in); "
               f"use source='track:<drum track>' for your drums\n") + txt
    return txt


@op()
def analyze_envelope(project: str, source: str = None, bars: list = None, steps_per_beat: int = 4,
                     band: str = None) -> str:
    """Level per step as digits 0-9 (max 8 bars); band = sub|bass|lowmid|mid|himid|air to isolate a range.
    Reveals sidechain pumping, gating, note rhythm."""
    P = _load(project)
    return A.envelope(*P.source(source, bars), bars, steps_per_beat, band=band)[1]


def _window(g, span, t0, t1):
    if span:
        return g.bar_time(span[0]), g.bar_time(span[1])
    if t0 is None or t1 is None:
        raise OpError("give span=[start_bar, end_bar] (fractional bars, end exclusive, e.g. [17, 17.25] = first beat"
                      " of bar 17) or t0/t1 in seconds")
    return t0, t1


@op()
def analyze_spectrum(project: str, source: str = None, span: list = None, t0: float = None, t1: float = None) -> str:
    """1/3-octave levels + strongest peaks (with note names) over a window. Window: span=[a, b] in fractional bars, end exclusive (span=[5, 6] = all of bar 5, [5, 5.25] = its first beat), or bars=[a, b] inclusive like the other analysis tools, or t0/t1 in seconds."""
    P = _load(project)
    path, g = P.source(source, span=span)
    a, b = _window(g, span, t0, t1)
    return A.spectrum(path, a, b)[1]


@op()
def analyze_timbre(project: str, source: str = None, span: list = None, t0: float = None, t1: float = None) -> str:
    """Describe the sound in a window: envelope, pitch, harmonic profile -> waveform guess, brightness/filter,
    noisiness, stereo width. Use on isolated sounds (stems, sound bank, soloed tracks) for sound design. Window: span=[a, b] in fractional bars, end exclusive (span=[5, 6] = all of bar 5, [5, 5.25] = its first beat), or bars=[a, b] inclusive like the other analysis tools, or t0/t1 in seconds."""
    P = _load(project)
    path, g = P.source(source, span=span)
    a, b = _window(g, span, t0, t1)
    return A.timbre(path, a, b)[1]


@op()
def analyze_formants(project: str, source: str = 'ref:vocals', bars: list = None, steps_per_beat: int = 2) -> str:
    """Vowel view of a voice: LPC formants F1/F2/F3 per step and the nearest vowel (max 8 bars). Use it to see what
    a vocal part is saying/singing and to match a vocoder / formant sound to it."""
    P = _load(project)
    return A.formants(*P.source(source, bars), bars, steps_per_beat)[1]


@op()
def analyze_key(project: str, source: str = None, bars: list = None) -> str:
    """Key estimate (Krumhansl) over the whole source or bars [a, b]."""
    P = _load(project)
    path, g = P.source(source, bars)
    if bars:
        return A.key_estimate(path, g.bar_time(bars[0]), g.bar_time(bars[1] + 1))[1]
    return A.key_estimate(path)[1]


@op()
def compare(project: str, a: str = 'render', b: str = 'ref', bars: list = None, offset_b: float = 0.0,
            detail: int = 8) -> str:
    """Compare two sources bar by bar on the project grid (default: your render vs the reference). Scores:
    chroma_sim, rhythm_corr, band dB deltas, log-mel L1, plus the worst bars and advice. Compare stems too:
    a='track:bass', b='ref:bass'."""
    P = _load(project)
    (pa, ga), (pb, gb) = P.source(a, bars), P.source(b, bars)
    return A.compare(pa, pb, ga, bars, offset_b + gb.offset - ga.offset, detail=detail)[1]


@op()
def align(project: str, a: str = 'render', b: str = 'ref', bars: list = None, band: str = None) -> str:
    """Timing lag between two sources (onset cross-correlation, optionally in one band e.g. band='sub' for kicks).
    Use it to line a render up with the reference: project_set(offset_sec = offset_sec + lag)."""
    P = _load(project)
    (pa, g), (pb, gb) = P.source(a, bars), P.source(b, bars)
    t0, t1 = (g.bar_time(bars[0]), g.bar_time(bars[1] + 1)) if bars else (None, None)
    r = A.align(pa, pb, t0, t1, band, shift_b=gb.offset - g.offset)
    return (f"B is {r['lag_ms']:+.1f} ms relative to A (negative = B earlier); xcorr {r['corr']:.3f} at best lag, "
            f"{r['corr_at_0']:.3f} at 0. To match B, set offset_sec to {P.d.get('offset_sec', 0) + r['lag_ms'] / 1000:.4f}"
            " if A is your render.")


@op()
def spectrogram(project: str, source: str = None, bars: list = None, out: str = None, seconds: list = None,
                f_lo: float = None, f_hi: float = None, words: list = None, ruler: bool = False) -> str:
    """Write a mel spectrogram PNG with bar lines (the one non-text view). Returns the PNG path.
    For eyes (zoomed on one sound, to compare two by picture): seconds=[t0, t1] inside bars (or the source), the band
    f_lo..f_hi Hz, ruler=True (ms from the window start), words=[{'w', 't0', 't1'}] in s. That picture sits on a fixed
    plot box, so two of the same window line up pixel for pixel, and a .json beside it maps a pixel to (s, Hz)."""
    P = _load(project)
    path, g = P.source(source, bars)
    a, b = (g.bar_time(bars[0]), g.bar_time(bars[1] + 1)) if bars else (0.0, None)
    if seconds:
        a, b = a + float(seconds[0]), a + float(seconds[1])
    outp = out or os.path.join('renders', f"spec_{source.replace(':', '_').replace('/', '_')}_{bars[0] if bars else 'all'}.png")
    if not os.path.isabs(outp):
        outp = os.path.join(P.root, outp)  # relative paths are relative to the project
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    try:
        return A.spectrogram_png(path, outp, a, b, g, f_lo=f_lo, f_hi=f_hi, words=words, ruler=ruler)
    except ValueError as e:
        raise OpError(f"spectrogram: {e}")


@op(mutates=True)
def separate(project: str, source: str = 'ref', model: str = 'htdemucs_ft') -> str:
    """Split a source into drums/bass/other/vocals stems with demucs (GPU if available). For 'ref' the stems become
    'ref:drums' etc."""
    from .separate import separate as sep, uses_gpu
    P = _load(project)
    path = P.resolve_audio(source)
    outdir = os.path.join(P.root, 'stems', os.path.splitext(os.path.basename(path))[0])
    with machine.slot('gpu' if uses_gpu() else 'cpu', f"separate {os.path.basename(path)}", threads=4):  # demucs on 4
        names = sep(path, outdir, model)
    if source == 'ref':
        P.d['reference']['stems_dir'] = outdir
        P.save()
    return f"stems {names} in {outdir}" + (" (available as ref:<stem>)" if source == 'ref' else '')


# ------------------------------------------------------------------ the shared machine

@op()
def machine_status(project: str = None) -> str:
    """The shared machine before anything heavy (a render over a minute, separate, mimic_measure, a fit, Blender,
    whisper): GPU heat and throttling, CPU, free memory, every heavy job running now in any session, and whether a new
    GPU or CPU job may start, free commit and disk (each running job's memory, OVER when past what it declared).
    Heavy ops check it themselves and refuse with the reason; run commands outside ismail through
    `python -m ismail.machine run --gpu|--cpu [--mem GB] [--disk GB] -- <command>` so they take a slot too."""
    return machine.board()


@op()
def machine_disk(top: int = 12) -> str:
    """Where the disk went, when the board says disk LOW or a heavy job waits on disk: each drive's free space and
    pagefile, the biggest folders under songs/ with their growth since the last daily snapshot, and every _reclaim
    folder. To free space, move a project's finished intermediates (caches, old renders, uncut takes) into its
    _reclaim/ folder and tell the user; never delete: the user clears _reclaim."""
    return machine.disk_text(top)


# ------------------------------------------------------------------ batch

@op()
def batch(project: str, ops: list, stop_on_error: bool = True) -> str:
    """Run many ops in order: ops = [{"op": "notes_write", "track": ..., ...}, ...] (project is implied).
    Mutations are atomic: if one fails the project is restored to its state before the batch. Returns each op's output."""
    P = _load(project)
    backup = json.dumps(P.d)
    out = []
    for i, spec in enumerate(ops):
        spec = dict(spec)
        name = spec.pop('op', None)
        if name not in OPS or name == 'batch':
            msg = f"op #{i}: unknown op {name!r}"
            if stop_on_error:
                _restore(P, backup)
                raise OpError(msg + f"; batch rolled back. valid ops: {', '.join(sorted(OPS))}")
            out.append(msg)
            continue
        try:
            out.append(f"[{i}] {name}: " + OPS[name](project, **spec))
        except (OpError, TypeError, ValueError, KeyError) as e:
            if stop_on_error:
                _restore(P, backup)
                done = '\n'.join(out)
                raise OpError(f"{done}\nop #{i} ({name}) failed: {e}\nbatch rolled back; fix op #{i} and resend the whole batch")
            out.append(f"[{i}] {name}: ERROR {e}")
    return '\n'.join(out)


def _restore(P, backup):
    with open(P.file, 'w', encoding='utf8') as f:
        f.write(json.dumps(json.loads(backup), indent=1))


def call(name, /, **kw):
    if name not in OPS:
        raise OpError(f"unknown op {name!r}; ops: {', '.join(sorted(OPS))}")
    return OPS[name](**kw)


from . import api_cmp  # noqa: E402,F401  (registers stem/structure/comparison ops)
from . import api_sound  # noqa: E402,F401  (registers sound_compare / instrument_fit)
from . import api_measure  # noqa: E402,F401  (registers tuning, swing, kit, section and level ops)
from . import api_exam  # noqa: E402,F401  (registers exam_check, the exam pre-flight)
from .live import ops as _live_ops  # noqa: E402,F401  (registers the live_* ops)
from .stage import ops as _stage_ops  # noqa: E402,F401  (registers the stage_* ops)
from .phone import ops as _phone_ops  # noqa: E402,F401  (registers the phone_* ops)
