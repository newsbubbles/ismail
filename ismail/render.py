"""Project -> audio. Tracks render in dependency order (sidechain / vocoder sources first)."""
import collections.abc
import hashlib
import json
import os
import time

import numpy as np
import soundfile as sf

from . import arp, dsp, fx as fxmod, instruments, notation, voices
from . import settings

LOG_PARAMS = ('cutoff', 'freq', '_hz')
PRE_ROLL_BEATS = 8  # rendered before a partial window so tails/sidechains settle
HELD_MIN_BEATS = 1.0  # a note sounding into a partial window with this much left still plays (drones, pads)


class RenderError(ValueError):
    pass


def automation_curve(points, name, t0_beat, n, bpm, sr, offset_sec):
    """points [[beat, value], ...] -> per-sample curve for the window starting at t0_beat (in song samples)."""
    pts = sorted(points)
    beats = np.array([p[0] for p in pts], dtype=float)
    vals = np.array([p[1] for p in pts], dtype=float)
    spb = 60.0 / bpm
    # sample times (song seconds) -> beats
    t_sec = t0_beat * spb + offset_sec + np.arange(n) / sr
    b = (t_sec - offset_sec) / spb
    if any(k in name for k in LOG_PARAMS) and np.all(vals > 0):
        return np.exp(np.interp(b, beats, np.log(vals)))
    return np.interp(b, beats, vals)


class Ctx:
    def __init__(self, renderer, track_name, fx_auto, n):
        self.r = renderer
        self.sr = renderer.sr
        self.bpm = renderer.bpm
        self.track = track_name
        self.fx_auto = fx_auto
        self.n = n
        self.offset_samples = renderer.offset_samples_in_window

    def param(self, idx, name, default):
        c = self.fx_auto.get((idx, name))
        return default if c is None else c

    def track_audio(self, name):
        if name not in self.r.post_fx:
            raise RenderError(f"sidechain source {name!r} not rendered (unknown track?)")
        return self.r.post_fx[name]

    def track_onsets(self, name):
        tr = self.r.project['tracks'].get(name)
        if tr is None:
            raise RenderError(f"duck source {name!r} is not a track; tracks: {list(self.r.project['tracks'])}")
        return [s for s, *_ in self.r.track_notes_sec(tr)]

    def track_notes(self, name):
        tr = self.r.project['tracks'].get(name)
        if tr is None:
            raise RenderError(f"autotune notes_from {name!r} is not a track; tracks: {list(self.r.project['tracks'])}")
        return self.r.track_notes_sec(tr)

    def modulator_audio(self, ref, n):
        if ref.startswith('sound:'):
            snd = self.r.load_sound(ref[6:])
            out = np.zeros(n)
            m = min(n, snd.shape[1])
            out[:m] = snd.mean(0)[:m]
            return out
        return self.track_audio(ref)

    def note_gr(self, idx, gr_db):
        key = (self.track, idx)
        self.r.gain_reduction[key] = min(self.r.gain_reduction.get(key, 0.0), gr_db)


class _Stem:
    """One track's or bus's output kept from its first to its last non-zero sample, as float32: a song of 70
    tracks that each play a few notes held ~330 MiB of float64 per track and ran out of memory."""
    __slots__ = ('a', 'y', 'n')

    def __init__(self, y):
        self.n = y.shape[1]
        nz = np.flatnonzero(np.abs(y).max(0) > 0) if self.n else []
        self.a = int(nz[0]) if len(nz) else 0
        self.y = y[:, self.a:int(nz[-1]) + 1 if len(nz) else 0].astype(np.float32)

    def scale(self, g):
        if np.ndim(g):
            self.y *= g[self.a:self.a + self.y.shape[1]]
        else:
            self.y *= g

    def crop(self, c):
        """Drop the first c samples."""
        self.n -= c
        self.a -= c
        if self.a < 0:
            self.y = self.y[:, -self.a:]
            self.a = 0

    def full(self):
        out = np.zeros((2, self.n))
        m = min(self.y.shape[1], self.n - self.a)
        if m > 0:
            out[:, self.a:self.a + m] = self.y[:, :m]
        return out


class Stems(collections.abc.Mapping):
    """Each track's and bus's output by name ('bus:<name>' for buses), built full length when read: going through
    them one at a time holds one at a time."""

    def __init__(self, stems):
        self._s = stems

    def __getitem__(self, k):
        return self._s[k].full()

    def __iter__(self):
        return iter(self._s)

    def __len__(self):
        return len(self._s)


class Renderer:
    def __init__(self, project, root, start_bar=None, end_bar=None, only=None, cache=True):
        project = arp.expand_project(project)     # a track's arp (track_arp) plays as its steps
        self.project = project
        self.root = root
        self.sr = project.get('sr', 44100)
        self.bpm = float(project['bpm'])
        self.bpb = project.get('beats_per_bar', 4)
        self.offset = float(project.get('offset_sec', 0.0))
        self.spb = 60.0 / self.bpm
        length_beats = project['length_bars'] * self.bpb
        sb = 1 if start_bar is None else start_bar
        eb = project['length_bars'] + 1 if end_bar is None else end_bar
        self.win_b0 = (sb - 1) * self.bpb                    # beat where output starts
        self.win_b1 = min((eb - 1) * self.bpb, length_beats)
        self.full = start_bar is None
        self.pre_beats = 0 if self.full else min(PRE_ROLL_BEATS, self.win_b0)
        self.r_b0 = self.win_b0 - self.pre_beats             # beat where rendering starts
        tail = project.get('tail_sec', 2.0)
        # a window's buffer starts on a whole song sample: every note then rounds to the sample it has in the whole
        # song's render (a pre-roll that is a fraction of a sample long moved some notes by one, ledger:M285(a))
        t0 = 0.0 if self.full else np.floor((self.offset + self.r_b0 * self.spb) * self.sr + 1e-6) / self.sr
        t1 = self.offset + self.win_b1 * self.spb + tail
        self.t0 = t0
        self.b_t0 = (t0 - self.offset) / self.spb             # the song beat at the buffer's first sample
        self.n = int((t1 - t0) * self.sr)
        # sample index (may be negative) where song beat 0 falls inside the render buffer
        self.offset_samples_in_window = int(round((self.offset - t0) * self.sr))
        self.only = only
        self.post_fx = {}
        self.pre_master_peak = {}   # each audible track's peak (dB) after its fader, before the master chain
        self.gain_reduction = {}
        self.sounds = {}
        self.use_cache = cache
        self.cache_dir = os.path.join(root, 'cache')
        self.stats = {}
        self.early = {}          # track -> notes nudged before the song's first sample (moved to it)
        self.notes_report = {}   # track -> summary lines about single notes (a mimic take far over its neighbours)
        instruments.set_resolvers(self.load_sound, self.load_table)

    # ------------------------------------------------------------------ sounds
    def load_sound(self, name):
        if name in self.sounds:
            return self.sounds[name]
        meta = self.project.get('sounds', {}).get(name)
        if meta is None:
            raise RenderError(f"sound {name!r} not in bank; have: {sorted(self.project.get('sounds', {}))}")
        path = os.path.join(self.root, meta['file'])
        y, sr = sf.read(path, dtype='float64', always_2d=True)
        y = y.T
        if y.shape[0] == 1:
            y = np.vstack([y, y])
        if sr != self.sr:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(sr, self.sr)
            y = resample_poly(y, self.sr // g, sr // g, axis=1)
        self.sounds[name] = y
        return y

    def load_table(self, name):
        y = self.load_sound(name).mean(0)
        return y / (np.max(np.abs(y)) + 1e-12)

    def sound_mtime(self, name):
        meta = self.project.get('sounds', {}).get(name)
        if not meta:
            return 0
        p = os.path.join(self.root, meta['file'])
        return os.path.getmtime(p) if os.path.exists(p) else 0

    # ------------------------------------------------------------------ timing
    def beat_to_win_sec(self, b):
        return self.offset + b * self.spb - self.t0

    def track_notes_sec(self, tr, name=None):
        out = []
        lo = self.r_b0 - 16 if not self.full else -1e9  # notes starting a bit before the window still ring
        # each note where it sounds: its beat moved by the track's and its own offset (notation.placed), as on a deck
        for st, p, d, v in notation.placed(tr.get('notes', []), self.bpm, tr.get('offset_ms', 0.0)):
            if st + d < lo or st >= self.win_b1:
                continue
            s = self.beat_to_win_sec(st)
            if s < 0 and self.full:
                # nudged before the song's first sample: it starts there instead (project_set offset_sec makes room)
                self.early[name] = self.early.get(name, 0) + 1
                out.append((0.0, int(p), d * self.spb, int(v)))
                continue
            if s < 0:
                # started before the render window: a note still sounding there with at least HELD_MIN_BEATS left
                # (a drone, a pad, a held string) comes in at the window's first sample with what is left of it; the
                # re-attack falls in the pre-roll, before the output starts
                left = d - (self.r_b0 - st)
                if left < HELD_MIN_BEATS:
                    continue
                s, d = 0.0, left
            out.append((s, int(p), d * self.spb, int(v)))
        return out

    # ------------------------------------------------------------------ deps
    def deps(self, tr):
        d = set()
        for f in tr.get('fx', []):
            if f['type'] == 'compressor' and f.get('sidechain'):
                d.add(f['sidechain'])
            if f['type'] == 'vocoder' and f.get('modulator') and not f['modulator'].startswith('sound:'):
                d.add(f['modulator'])
        return d

    def order(self):
        tracks = self.project['tracks']
        needed = set(tracks) if self.only is None else set(self.only)
        stack = list(needed)
        while stack:
            t = stack.pop()
            for dep in self.deps(tracks[t]):
                if dep not in tracks:
                    raise RenderError(f"track {t!r} references unknown track {dep!r}")
                if dep not in needed:
                    needed.add(dep)
                    stack.append(dep)
        order, seen, temp = [], set(), set()

        def visit(t):
            if t in seen:
                return
            if t in temp:
                raise RenderError(f"sidechain cycle through {t!r}")
            temp.add(t)
            for dep in self.deps(tracks[t]):
                visit(dep)
            temp.discard(t)
            seen.add(t)
            order.append(t)
        for t in tracks:
            if t in needed:
                visit(t)
        return order

    # ------------------------------------------------------------------ cache
    def cache_key(self, name, tr, dep_keys):
        # the cached file holds the track before its fader and pan, so a mix move reuses it
        sounds = sorted(_sounds_used(tr))
        blob = json.dumps([_dry(tr), self.bpm, self.sr, self.offset, self.t0, self.n, self.r_b0, self.win_b1,
                           [(s, self.sound_mtime(s)) for s in sounds], dep_keys, _CODE_VERSION,
                           voices.fingerprint(tr.get('instrument'), self.root)],
                          sort_keys=True, default=str)
        return hashlib.sha1(blob.encode()).hexdigest()[:20]

    # ------------------------------------------------------------------ render
    def render_track(self, name, tr):
        n = self.n
        inst = instruments.normalize(tr['instrument']) if tr.get('instrument') else None
        auto = tr.get('automation', {})
        inst_auto, fx_auto = {}, {}
        for key, pts in auto.items():
            if not pts or key in _MIX_AUTO:     # fader and pan curves: mix_track
                continue
            curve = automation_curve(pts, key, self.b_t0,
                                     n, self.bpm, self.sr, self.offset)
            if key.startswith('inst.'):
                inst_auto[key[5:]] = curve
            elif key.startswith('fx.'):
                _, i, pname = key.split('.', 2)
                fx_auto[(int(i), pname)] = curve
        y = np.zeros((2, n))
        if inst is not None:
            notes = self.track_notes_sec(tr, name)
            if notes:
                rep = []
                y += instruments.render_instrument(inst, notes, n, inst_auto, self.bpm, self.sr, self.root,
                                                  self.b_t0, self.bpb, rep)
                if rep:
                    self.notes_report[name] = rep
        for clip in tr.get('audio', []):
            snd = self.load_sound(clip['sound'])
            s0 = int(round((self.beat_to_win_sec(clip['at_beat']) + tr.get('offset_ms', 0.0) / 1000.0) * self.sr))
            a = int(clip.get('offset_sec', 0.0) * self.sr)
            seg = snd[:, a:]
            if clip.get('length_beats'):
                seg = seg[:, :int(clip['length_beats'] * self.spb * self.sr)]
            if s0 < 0:
                seg = seg[:, -s0:]
                s0 = 0
            m = min(seg.shape[1], n - s0)
            if m > 0:
                y[:, s0:s0 + m] += seg[:, :m] * dsp.undb(clip.get('gain_db', 0.0))
        ctx = Ctx(self, name, fx_auto, n)
        for i, f in enumerate(tr.get('fx', [])):
            y = fxmod.apply_fx(y, fxmod.normalize(f), ctx, i)
        return y

    def mix_track(self, tr, y):
        """The track's fader and pan, applied after the cached render."""
        auto = tr.get('automation', {})
        mix_auto = {k: automation_curve(auto[k], k, self.b_t0, self.n, self.bpm, self.sr, self.offset)
                    for k in _MIX_AUTO if auto.get(k)}
        # volume automation is an offset on top of the track's volume_db fader (0 = unchanged)
        vol = tr.get('volume_db', 0.0) + mix_auto.get('volume_db', 0.0)
        pan = mix_auto.get('pan', tr.get('pan', 0.0))
        y = y * dsp.undb(vol)
        if np.any(pan):
            gl, gr = dsp.pan_gains(pan)
            y = np.stack([y[0] * gl, y[1] * gr])
        return y

    def run(self):
        t_start = time.time()
        tracks = self.project['tracks']
        order = self.order()
        keys = {}
        any_solo = any(t.get('solo') for t in tracks.values())
        buses = {b: np.zeros((2, self.n)) for b in self.project.get('buses', {})}
        master = np.zeros((2, self.n))
        stems = {}
        # a track's full output stays in memory only while an effect may read it (a sidechain or vocoder source)
        sources = set().union(*(self.deps(t) for t in tracks.values()),
                              *(self.deps(b) for b in self.project.get('buses', {}).values()),
                              self.deps(self.project.get('master', {})))
        for name in order:
            tr = tracks[name]
            dry = self.cache_key(name, tr, [keys[d] for d in sorted(self.deps(tr))])
            # tracks that read this one (sidechain, vocoder) hear it after the fader, so their key holds the mix
            keys[name] = hashlib.sha1(json.dumps([dry, _mix_of(tr)], sort_keys=True, default=str)
                                      .encode()).hexdigest()[:20]
            path = os.path.join(self.cache_dir, dry + '.npy')
            t0 = time.time()
            rpath = path[:-4] + '.notes.json'
            if self.use_cache and os.path.exists(path):
                y = np.load(path).astype(np.float64)
                cached = True
                if os.path.exists(rpath):        # a cached track repeats its note lines
                    with open(rpath, encoding='utf-8') as fh:
                        self.notes_report[name] = json.load(fh)
            else:
                y = self.render_track(name, tr)
                cached = False
                if self.use_cache:
                    os.makedirs(self.cache_dir, exist_ok=True)
                    np.save(path, y.astype(np.float32))
                    if self.notes_report.get(name):
                        with open(rpath, 'w', encoding='utf-8') as fh:
                            json.dump(self.notes_report[name], fh)
            y = self.mix_track(tr, y)
            if name in sources:
                self.post_fx[name] = y
            self.stats[name] = {'sec': round(time.time() - t0, 2), 'cached': cached}
            audible = not tr.get('mute') and (not any_solo or tr.get('solo'))
            if self.only is not None and name not in self.only:
                audible = False
            if not audible:
                continue
            stems[name] = _Stem(y)
            self.pre_master_peak[name] = float(20 * np.log10(np.max(np.abs(y)) + 1e-12))
            out = tr.get('output', 'master')
            if out != 'master' and out not in buses:
                raise RenderError(f"track {name!r} outputs to unknown bus {out!r}; buses: {list(buses)}")
            (master if out == 'master' else buses[out]).__iadd__(y)
            for bus, lvl in tr.get('sends', {}).items():
                if bus not in buses:
                    raise RenderError(f"track {name!r} sends to unknown bus {bus!r}; buses: {list(buses)}")
                buses[bus] += y * dsp.undb(lvl)
        for bname, bus in self.project.get('buses', {}).items():
            y = buses[bname]
            fx_auto, vol = {}, bus.get('volume_db', 0.0)
            for key, pts in (bus.get('automation') or {}).items():
                if not pts:
                    continue
                curve = automation_curve(pts, key, self.b_t0,
                                         self.n, self.bpm, self.sr, self.offset)
                if key.startswith('fx.'):
                    _, i, pname = key.split('.', 2)
                    fx_auto[(int(i), pname)] = curve
                elif key == 'volume_db':
                    vol = vol + curve
            ctx = Ctx(self, 'bus:' + bname, fx_auto, self.n)
            for i, f in enumerate(bus.get('fx', [])):
                y = fxmod.apply_fx(y, fxmod.normalize(f), ctx, i)
            y = y * dsp.undb(vol)
            stems['bus:' + bname] = _Stem(y)
            master += y
        m = self.project.get('master', {})
        ctx = Ctx(self, 'master', {}, self.n)
        pre = master
        for i, f in enumerate(m.get('fx', [])):
            master = fxmod.apply_fx(master, fxmod.normalize(f), ctx, i)
        if m.get('fx'):
            # stems carry the master chain's broadband gain (limiter drive and reduction, compression), so the
            # stems sum to the mix; exact for gain-type fx, a level approximation for eq
            w = max(int(0.005 * self.sr), 1)
            k = np.ones(w) / w
            e_pre = np.convolve(np.sum(pre ** 2, 0), k, 'same')
            e_post = np.convolve(np.sum(master ** 2, 0), k, 'same')
            mg = np.sqrt((e_post + 1e-12) / (e_pre + 1e-12))
            mg = np.where(e_pre > 1e-10, mg, 1.0)
            for v in stems.values():
                v.scale(mg)
        vol = m.get('volume_db', 0.0)
        pts = (m.get('automation') or {}).get('volume_db')
        if pts:  # master fade: dB offset curve, like track volume automation
            vol = vol + automation_curve(pts, 'volume_db', self.b_t0,
                                         self.n, self.bpm, self.sr, self.offset)
        master = master * dsp.undb(vol)
        # stems carry the master gain curve (fades), so they line up with stems separated from a finished master
        g = dsp.undb(vol)
        for v in stems.values():
            v.scale(g)
        # crop pre-roll for partial renders
        if not self.full:
            c = int(round(self.beat_to_win_sec(self.win_b0) * self.sr))
            master = master[:, c:]
            for v in stems.values():
                v.crop(c)
        self.elapsed = time.time() - t_start
        return master, Stems(stems)


def _code_hash():
    # any change to the synthesis/effect code invalidates cached track renders
    h = hashlib.sha1()
    here = os.path.dirname(os.path.abspath(__file__))
    for f in ('dsp.py', 'instruments.py', 'fx.py', 'render.py', 'rig.py', 'spatial.py', 'mimic.py'):
        with open(os.path.join(here, f), 'rb') as fh:
            h.update(fh.read())
    return h.hexdigest()[:12]


_CODE_VERSION = _code_hash()


# a track's mix settings: applied after the cached render, so they stay out of its cache key
_MIX = ('volume_db', 'pan', 'sends', 'output', 'mute', 'solo')
_MIX_AUTO = ('volume_db', 'pan')


def _dry(tr):
    out = {k: v for k, v in tr.items() if k not in _MIX}
    auto = {k: v for k, v in (tr.get('automation') or {}).items() if k not in _MIX_AUTO}
    if 'automation' in tr:
        out['automation'] = auto
    return out


def _mix_of(tr):
    return [tr.get('volume_db', 0.0), tr.get('pan', 0.0),
            {k: v for k, v in (tr.get('automation') or {}).items() if k in _MIX_AUTO}]


def _sounds_used(tr):
    out = set()

    def walk(inst):
        if not inst:
            return
        out.update(instruments.sampler_sound_names(inst))
        if inst.get('type') == 'kit':
            for v in inst['map'].values():
                walk(v)
        for o in inst.get('oscs', []) or []:
            if o.get('table'):
                out.add(o['table'])
    walk(tr.get('instrument'))
    for c in tr.get('audio', []):
        out.add(c['sound'])
    for f in tr.get('fx', []):
        if f.get('type') == 'vocoder' and str(f.get('modulator', '')).startswith('sound:'):
            out.add(f['modulator'][6:])
    return out


def prune_cache(root, max_bytes=1024 ** 3):
    d = os.path.join(root, 'cache')
    if not os.path.isdir(d):
        return
    files = sorted((os.path.join(d, f) for f in os.listdir(d)), key=os.path.getatime, reverse=True)
    total = 0
    for f in files:
        total += os.path.getsize(f)
        if total > max_bytes:
            os.remove(f)


def write_mp3(wav_path, mp3_path, bitrate='320k'):
    """Encode a rendered wav to mp3 with ffmpeg (on PATH, or the program the ffmpeg setting names)."""
    import subprocess
    exe = settings.get('ffmpeg')
    if not exe:
        raise RuntimeError("ffmpeg not found; install it or config_set('ffmpeg', <its path>)")
    r = subprocess.run([exe, '-nostdin', '-y', '-loglevel', 'error', '-i', wav_path, '-codec:a', 'libmp3lame', '-b:a', bitrate,
                        mp3_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-300:] or f"ffmpeg exited {r.returncode}")
    return mp3_path


def _nlinks(path):
    try:
        return os.stat(path).st_nlink
    except OSError:
        return 0


def _replace(tmp, path, tries=20):
    """os.replace, retried for about two seconds: on Windows a reader holding `path` open makes it fail a moment."""
    for i in range(tries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.1)


def write_wav(path, y, sr):
    """24-bit WAV. A name that shares its file with another (latest.wav is a hard link to the named render,
    ledger:M221) is never written through: the new audio goes to `<path>.writing`, which then replaces the name, so
    the other name keeps its own sound. A file with one name is written in place, as always."""
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    data = np.clip(y.T, -1, 1).astype(np.float32)
    if _nlinks(path) <= 1:
        sf.write(path, data, sr, subtype='PCM_24')
        return path
    tmp = path + '.writing'
    sf.write(tmp, data, sr, subtype='PCM_24', format='WAV')
    try:
        _replace(tmp, path)
    except OSError as e:
        raise RuntimeError(f"{path} is open in another program, so the new audio is in {tmp}; close it and render "
                           f"again ({e})")
    return path


def link_wav(src, dst, y, sr):
    """Make `dst` a second name for `src`, the WAV just written (renders/latest.wav and renders/<out>.wav: one copy
    on disk, ledger:M221). A later write to either name replaces that name only (write_wav). Where no link can be
    made (a drive without hard links, `dst` open in another program), `dst` is written as its own file, as before.
    -> 'linked' or 'written'."""
    try:
        if os.path.exists(dst) and os.path.samefile(src, dst):
            return 'linked'
    except OSError:
        pass
    tmp = dst + '.link'
    if not os.path.lexists(tmp):       # one left by a failed swap is a second name for an older render: song_compress
        try:                           # moves it to _reclaim; until then dst is written as its own file
            os.link(src, tmp)
        except OSError:
            pass
        else:
            try:
                _replace(tmp, dst)
                return 'linked'
            except OSError:
                pass
    write_wav(dst, y, sr)
    return 'written'


def write_renders(rd, y, sr, out=None):
    """A render's files in renders/: `<out>.wav` when the render is named, with latest.wav a hard link to it (one
    copy on disk, ledger:M221); latest.wav alone when it is not. -> {name: 'written' | 'linked'}."""
    latest = os.path.join(rd, 'latest.wav')
    named = os.path.join(rd, out + '.wav') if out else None
    if not named or os.path.abspath(named) == os.path.abspath(latest):
        write_wav(latest, y, sr)
        return {'latest.wav': 'written'}
    write_wav(named, y, sr)
    return {out + '.wav': 'written', 'latest.wav': link_wav(named, latest, y, sr)}
