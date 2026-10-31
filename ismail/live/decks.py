"""Decks: named groups of live tracks with a DJ channel strip, so one arrangement can play while the next is
prepared (cued, off the air) and a transition between them is queued as ramps.

A deck strip is a 3-band isolator (Linkwitz-Riley 4th-order crossovers at 250 Hz and 2.5 kHz: the bands sum flat,
and a band at KILL_DB or below is removed completely), a one-knob filter (-1 low-pass .. 0 off .. +1 high-pass)
and a fader. Songs load onto a deck from their ismail project: tracks, effects and buses come over with names
prefixed '<deck>.', and their notes play at the house tempo (re-rendered, never time-stretched).
"""
import json
import math
import os

import numpy as np
from scipy import signal

from . import dsp_blocks as dsp, fx_blocks as F
from ..dsp import SR
from ..arp import expand_project
from ..notation import placed

KILL_DB = -40.0              # an isolator band at or below this is off
SILENT_DB = -60.0            # a deck fader at or below this is off
HELD_MIN_BEATS = 1.0         # a note sounding into a deck window with this much left comes in on its first beat
LEAD_IN_BEATS = 8.0          # a performer's notes this far before its window ring into it (rendered as context)
PARAMS = {'volume_db': 0.0, 'low_db': 0.0, 'mid_db': 0.0, 'high_db': 0.0, 'filter': 0.0}
STYLES = ('blend', 'bass_swap', 'filter', 'cut')


class DeckError(ValueError):
    pass


def _lr4(f, kind):
    one = signal.butter(2, f, btype=kind, fs=SR, output='sos')
    return np.vstack([one, one])


class Strip:
    def __init__(self):
        self.lo = F._Sos(_lr4(250, 'low'), 2)
        self.hi = F._Sos(_lr4(250, 'high'), 2)
        self.mid = F._Sos(_lr4(2500, 'low'), 2)
        self.top = F._Sos(_lr4(2500, 'high'), 2)
        self.ap_lo = F._Sos(_lr4(2500, 'low'), 2)          # allpass compensation of the low band, so the
        self.ap_hi = F._Sos(_lr4(2500, 'high'), 2)         # three bands sum flat
        self.fst = [dsp.filt_state() for _ in range(4)]
        self.filter_on = False

    @staticmethod
    def gain(db):
        db = np.asarray(db, dtype=np.float64)
        g = 10 ** (db / 20)
        return np.where(db <= KILL_DB, 0.0, g) if g.ndim else (0.0 if db <= KILL_DB else float(g))

    def process(self, x, p):
        """x (2, n); p: param -> scalar or (n,) curve."""
        n = x.shape[1]
        flat = all(np.isscalar(p[k]) and p[k] == 0 for k in ('low_db', 'mid_db', 'high_db'))
        low = self.lo(x)
        rest = self.hi(x)
        mid = self.mid(rest)
        top = self.top(rest)
        low = self.ap_lo(low) + self.ap_hi(low)
        y = low + mid + top if flat else \
            low * self.gain(p['low_db']) + mid * self.gain(p['mid_db']) + top * self.gain(p['high_db'])
        k = p['filter']
        if np.any(np.abs(k) > 1e-4):
            self.filter_on = True
            kc = dsp.as_curve(k, n)
            lp = np.where(kc < 0, 20000.0 * (200.0 / 20000.0) ** (-kc), 20000.0)
            hp = np.where(kc > 0, 20.0 * (5000.0 / 20.0) ** kc, 20.0)
            y = np.stack([dsp.filt_s(np.ascontiguousarray(y[c]), 'lp24', lp, 0.15, SR, 1.0, self.fst[c])
                          for c in (0, 1)])
            y = np.stack([dsp.filt_s(np.ascontiguousarray(y[c]), 'hp24', hp, 0.15, SR, 1.0, self.fst[2 + c])
                          for c in (0, 1)])
        elif self.filter_on:
            self.filter_on = False
            self.fst = [dsp.filt_state() for _ in range(4)]
        v = p['volume_db']
        g = np.where(np.asarray(v) <= SILENT_DB, 0.0, 10 ** (np.asarray(v, dtype=np.float64) / 20))
        return y * g


class Deck:
    def __init__(self, name):
        self.name = name
        self.values = dict(PARAMS)      # the last value set for each param (ramps may still be moving)
        self.cue = False
        self.transpose = 0
        self.strip = Strip()
        self.ms = 0.0
        self.air = np.zeros((2, int(60 * SR)), dtype=np.float32)
        self.song = None                # what live_load put here: {'name', 'bars', 'tracks'}
        self.master = None              # the song's master chain (compressor, limiter), run on the deck's sum
        self.tp = []                    # [(beat, semitones)]: transpose from that beat on
        self.quiet = 0                  # samples since the deck last had input (dormant when long)
        self.held = None                # (bar line beat, first bar beat, 'bar N'): cued until that bar is rendered

    def describe(self, level=True, values=None, cue=None):
        """cue: the state to show when a change is already scheduled (the reply to live_deck(cue=...))."""
        v = values or self.values

        def band(b):
            db = v[b + '_db']
            return f"{b[0].upper()} " + ('KILL' if db <= KILL_DB else f"{db:+g}")
        fader = 'off' if v['volume_db'] <= SILENT_DB else f"{v['volume_db']:+g} dB"
        state = 'CUE (off air)' if (self.cue if cue is None else cue) else 'on air'
        if self.held and cue is None:
            state = f"HELD off air: on air at {self.held[2]} if that whole bar is rendered by then, else a bar later"
        s = (state + f" | fader {fader} | eq "
             + ' '.join(band(b) for b in ('low', 'mid', 'high')) + f" | filter {v['filter']:+.2f}"
             + (f" | transpose {self.transpose:+d}" if self.transpose else ''))
        if level:
            s += f" | level {10 * math.log10(self.ms + 1e-12):6.1f} dBFS"
        if self.song:
            s += f" | {self.song['name']} bars {self.song['bars'][0]}-{self.song['bars'][1]}"
        return s


# ------------------------------------------------------------------ loading a song

DRY_FX = ('reverb', 'delay', 'hall')     # effects with a dry level (studio fx._dry)


def _rename_fx(fxs, deck, names):
    out = []
    for f in fxs or []:
        f = dict(f)
        for key in ('sidechain', 'source', 'modulator'):
            v = f.get(key)
            if isinstance(v, str) and v in names:
                f[key] = f"{deck}.{v}"
            elif isinstance(v, str) and v.startswith('sound:'):
                raise DeckError(f"{f['type']} uses {v}: live effects take a track as modulator, not a sound")
        out.append(f)
    return out


def _sum_lanes(a, b):
    """Two dB lanes [[beat, value], ...] -> their sum, exact at the union of their points."""
    import numpy as np
    a, b = sorted(a), sorted(b)
    xs = sorted({p[0] for p in a} | {p[0] for p in b})
    ia = np.interp(xs, [p[0] for p in a], [p[1] for p in a])
    ib = np.interp(xs, [p[0] for p in b], [p[1] for p in b])
    return [[float(x), float(u + v)] for x, u, v in zip(xs, ia, ib)]


def placed_audio(root, d, t, beat0, beat1, bpb):
    """An audio track's clips as a deck plays them -> (instrument, notes): the built-in placed_audio performer,
    which reads each clip's sound file by song time, and one gate note per bar of [beat0, beat1) that a clip sounds
    in, in beats from beat0. Each clip sits where the studio puts it: at its beat, moved by the track's offset_ms,
    from offset_sec into its file, for length_beats or to the file's end."""
    import soundfile as sf
    spb = 60.0 / float(d['bpm'])
    bank = d.get('sounds') or {}
    clips, bars = [], set()
    for c in t.get('audio') or []:
        meta = bank.get(c.get('sound'))
        if meta is None:
            raise DeckError(f"an audio clip places sound {c.get('sound')!r}, which is not in the song's sound bank "
                            f"(sound_list); have: {sorted(bank)}")
        path = os.path.join(root, meta['file'])
        try:
            info = sf.info(path)
        except (RuntimeError, OSError) as e:
            raise DeckError(f"sound {c['sound']!r}: its file {path} does not open ({e})")
        shift = float(t.get('offset_ms', 0.0)) / 1000.0
        dur = info.frames / info.samplerate - float(c.get('offset_sec') or 0.0)
        if c.get('length_beats'):
            dur = min(dur, float(c['length_beats']) * spb)
        if dur <= 0:
            continue
        sb = float(c['at_beat']) + shift / spb
        eb = sb + dur / spb
        clips.append({'file': meta['file'].replace('\\', '/'), 'at_beat': float(c['at_beat']), 'shift_sec': shift,
                      'offset_sec': float(c.get('offset_sec') or 0.0), 'length_beats': c.get('length_beats'),
                      'gain_db': float(c.get('gain_db') or 0.0)})
        j0 = max(int(math.floor(max(sb, beat0) / bpb + 1e-9)), 0)
        j1 = int(math.ceil(min(eb, beat1) / bpb - 1e-9))
        bars.update(range(j0, j1))
    notes = [(j * bpb - beat0, 60, min((j + 1) * bpb, beat1) - j * bpb, 100) for j in sorted(bars)
             if beat0 <= j * bpb < beat1]
    inst = {'type': 'code', 'voice': 'placed_audio', 'performer': True, 'tail': 0.0,
            'params': {'clips': clips, 'song_bpm': float(d['bpm'])}}
    return inst, notes


def read_song(path, deck, bars, house_bpb, performers=()):
    """-> (song summary, buses [(name, fx, volume_db)], tracks [dict], clip specs, notes on what was skipped)."""
    root = os.path.abspath(path)
    fn = os.path.join(root, 'project.json')
    if not os.path.isfile(fn):
        raise DeckError(f"no ismail project at {root} (a song folder holds project.json; songs/<name>/proj is common)")
    with open(fn, encoding='utf8') as f:
        d = json.load(f)
    d = expand_project(d)            # a track's arp (track_arp) plays as its steps, as in the studio
    bpb = d.get('beats_per_bar', 4)
    if bpb != house_bpb:
        raise DeckError(f"the song is in {bpb}/4 and the live set in {house_bpb}/4; decks share the house meter")
    length = int(d.get('length_bars', 0)) or max(1, math.ceil(max(
        (n[0] + n[2] for t in d['tracks'].values() for n in t.get('notes', [])), default=bpb) / bpb))
    b0, b1 = (1, length) if not bars else (int(bars[0]), int(bars[1]))
    if not 1 <= b0 <= b1 <= length:
        raise DeckError(f"bars {bars}: the song has bars 1-{length}")
    beat0, beat1 = (b0 - 1) * bpb, b1 * bpb
    tnames = set(d['tracks'])
    skipped = {'muted': [], 'no instrument': [], 'automation': [], 'placed audio': 0, 'silent in range': []}
    # a bus that tracks play through (a drum group) keeps the studio's insert rule: its reverb, delay or hall passes
    # the dry signal (dry 1); only a pure send bus is wet-only. Live buses default to wet-only, which took the kick
    # and snare out of a song whose drum group had a reverb on it
    grouped = {t.get('output') for t in d['tracks'].values()}
    buses = [(f"{deck}.{b}", [dict(f, dry=1.0) if b in grouped and f['type'] in DRY_FX and f.get('dry') is None
                              else f for f in _rename_fx(v.get('fx'), deck, tnames)], float(v.get('volume_db', 0.0)))
             for b, v in (d.get('buses') or {}).items()]
    # automation comes over: {'track:<deck>.<name>' or 'bus:<deck>.<name>': {'fx.0.cutoff' | 'volume_db' | 'inst.<param>':
    # [[beat, value], ...]}}, beats from the song's bar 1 (the studio's format). fx and volume become ramps; inst lanes
    # render with the notes (the track's section is one render event)
    auto = {}
    for b, v in (d.get('buses') or {}).items():
        for key, pts in (v.get('automation') or {}).items():
            if key == 'volume_db' or key.startswith('fx.'):
                auto.setdefault(f"bus:{deck}.{b}", {})[key] = pts
            else:
                skipped['automation'].append(f"bus {b} {key}")
    master_fade = ((d.get('master') or {}).get('automation') or {}).get('volume_db')
    tracks, clips, audio_tracks = [], [], []
    solo = any(t.get('solo') for t in d['tracks'].values())
    for name, t in d['tracks'].items():
        if t.get('mute') or (solo and not t.get('solo')):
            skipped['muted'].append(name)
            continue
        audio_track = bool(t.get('audio')) and not t.get('instrument')
        if not audio_track:
            # placed audio beside notes stays behind (track_freeze puts it on an audio track of its own)
            skipped['placed audio'] += len(t.get('audio') or [])
        if not t.get('instrument') and not audio_track:
            skipped['no instrument'].append(name)
            continue
        for key, pts in (t.get('automation') or {}).items():
            if key == 'volume_db' or key.startswith(('fx.', 'inst.')):
                auto.setdefault(f"track:{deck}.{name}", {})[key] = pts
            else:
                skipped['automation'].append(f"{name} {key}")
        context = []
        if audio_track:
            # an audio track (placed clips, a track_freeze) plays through the placed_audio voice: it reads the song's
            # own files by song time, one gate note per bar the clips sound in
            inst, notes = placed_audio(root, d, t, beat0, beat1, bpb)
            audio_tracks.append(name)
        else:
            inst = t['instrument']
            # where each note sounds: its beat moved by the track's and its own offset, as the studio places it
            tn = placed(t.get('notes', []), float(d['bpm']), t.get('offset_ms', 0.0))
            notes = [(n[0] - beat0, n[1], n[2], n[3]) for n in tn if beat0 <= n[0] < beat1]
            if name in performers and notes:
                # a performer renders what came before as context: held notes keep their real start, short ones
                # ring in
                context = [(n[0] - beat0, n[1], n[2], n[3]) for n in tn
                           if beat0 - LEAD_IN_BEATS <= n[0] < beat0 or (n[0] < beat0 < n[0] + n[2])]
            else:
                # a note still sounding at the window's start (a drone, a pad, a held string) comes in on its first
                # beat with what is left of it, as in a studio render of the same window; one with less than a beat
                # left is a tail
                notes += [(0.0, n[1], min(n[0] + n[2], beat1) - beat0, n[3]) for n in tn
                          if n[0] < beat0 and n[0] + n[2] - beat0 >= HELD_MIN_BEATS]
        full = f"{deck}.{name}"
        out = t.get('output', 'master')
        tracks.append({'track': full, 'instrument': inst, 'fx': _rename_fx(t.get('fx'), deck, tnames),
                       'volume_db': min(6.0, float(t.get('volume_db', 0.0))), 'pan': float(t.get('pan', 0.0)),
                       'sends': {f"{deck}.{b}": float(v) for b, v in (t.get('sends') or {}).items()},
                       'output': None if out in (None, 'master') else f"{deck}.{out}", 'root': root})
        if notes:
            clips.append({'track': full, 'notes': '; '.join(f"{s:.10g} {m} {du:.10g} {v}" for s, m, du, v in notes),
                          'beats': beat1 - beat0, **({'context': sorted(context)} if context else {})})
        else:
            skipped['silent in range'].append(name)
    tracks, dropped = _order_tracks(tracks)
    if dropped:
        skipped['effects without their source'] = dropped
    clips = [c for c in clips if c['track'] in {t['track'] for t in tracks}]
    if master_fade:
        # the song's master fade rides on everything that feeds its master directly (tracks without a bus, and its
        # buses), added in dB to any volume automation already there: both are piecewise linear, so summing them at
        # the union of their points is exact
        heads = [t['track'] for t in tracks if t['output'] is None] + [b[0] for b in buses]
        for h in heads:
            key = ('track:' if not any(h == b[0] for b in buses) else 'bus:') + h
            own = auto.setdefault(key, {}).get('volume_db')
            auto[key]['volume_db'] = _sum_lanes(own, master_fade) if own else master_fade
    kept = {t['track'] for t in tracks} | {b[0] for b in buses}
    song = {'name': d.get('name') or os.path.basename(root.rstrip('/\\')), 'bars': [b0, b1],
            'bpm': d.get('bpm'), 'root': root, 'tracks': [t['track'] for t in tracks],
            'master_fx': _rename_fx((d.get('master') or {}).get('fx'), deck, tnames),
            'automation': {k: v for k, v in auto.items() if k.split(':', 1)[1] in kept}, 'beats': [beat0, beat1],
            'audio_tracks': [n for n in audio_tracks if f"{deck}.{n}" in kept]}
    return song, buses, tracks, clips, skipped


def _order_tracks(tracks):
    """Sources (sidechain keys, duck sources, vocoder modulators) before the tracks that read them; effects whose
    source did not come over are dropped (reported)."""
    have = {t['track'] for t in tracks}
    dropped = []
    for t in tracks:
        keep = []
        for f in t['fx']:
            src = [f.get(k) for k in ('sidechain', 'source', 'modulator') if f.get(k)]
            if any(x not in have for x in src):
                dropped.append(f"{t['track']} {f['type']} (reads {src[0]})")
            else:
                keep.append(f)
        t['fx'] = keep
    by = {t['track']: t for t in tracks}
    out, seen = [], set()

    def visit(name, stack=()):
        if name in seen or name in stack:
            return
        for f in by[name]['fx']:
            for k in ('sidechain', 'source', 'modulator'):
                if f.get(k) in by:
                    visit(f[k], stack + (name,))
        seen.add(name)
        out.append(by[name])
    for t in tracks:
        visit(t['track'])
    return out, dropped


# ------------------------------------------------------------------ transitions

def plan(style, bars, bpb):
    """Ramps for a transition of `bars` bars: [(deck 'to'|'from', param, from value or None (= where it is),
    to value, start beat from the transition start, ramp beats)]."""
    L = bars * bpb
    half = L / 2
    if style == 'blend':        # bring the new deck up without its bass, swap the bass mid-way, fade the old out
        return [('to', 'low_db', KILL_DB, KILL_DB, 0, 0), ('to', 'volume_db', -30.0, 0.0, 0, half),
                ('from', 'low_db', None, KILL_DB, half, 1), ('to', 'low_db', KILL_DB, 0.0, half, 1),
                ('from', 'volume_db', None, SILENT_DB, half, half)]
    if style == 'bass_swap':    # both full, one bass at a time, the old deck leaves in the last quarter
        return [('to', 'volume_db', 0.0, 0.0, 0, 0), ('to', 'low_db', KILL_DB, KILL_DB, 0, 0),
                ('from', 'low_db', None, KILL_DB, half, 1), ('to', 'low_db', KILL_DB, 0.0, half, 1),
                ('from', 'volume_db', None, SILENT_DB, L * 0.75, L * 0.25)]
    if style == 'filter':       # the old deck thins out through a rising high-pass while the new one opens up
        return [('to', 'volume_db', 0.0, 0.0, 0, 0), ('to', 'filter', -0.85, 0.0, 0, L),
                ('from', 'filter', None, 0.85, 0, L), ('from', 'volume_db', None, SILENT_DB, L - 2, 2)]
    if style == 'cut':
        return [('to', 'volume_db', 0.0, 0.0, 0, 0), ('from', 'volume_db', None, SILENT_DB, 0, 0)]
    raise DeckError(f"style={style!r}: use one of {', '.join(STYLES)}")
