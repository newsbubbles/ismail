"""Render workers: one note (or mono phrase) per job, in separate processes so rendering never holds the audio
thread's GIL. A job renders exactly what the offline renderer would for those notes (same instrument code);
the result is trimmed to where it falls silent."""
import json
import os
import time

import numpy as np

from .. import instruments
from ..dsp import SR

MAX_TAIL_S = 8.0
MAX_PEAK = 4.0          # +12 dBFS: a single event louder than this is a broken voice, not music


class SoundBank:
    """Sampler sounds of the live project (its project.json sound bank), for instruments.set_resolvers."""

    def __init__(self, root):
        self.root = root
        self.cache = {}
        try:
            with open(os.path.join(root, 'project.json'), encoding='utf8') as f:
                self.meta = json.load(f).get('sounds', {})
        except (OSError, ValueError):
            self.meta = {}

    def sound(self, name):
        if name not in self.cache:
            import soundfile as sf
            m = self.meta.get(name)
            if m is None:
                raise instruments.InstrumentError(f"sound {name!r} not in the live project's bank; have: {sorted(self.meta)}")
            y, sr = sf.read(os.path.join(self.root, m['file']), dtype='float64', always_2d=True)
            y = y.T if y.shape[1] > 1 else np.vstack([y.T, y.T])
            if sr != SR:
                from math import gcd
                from scipy.signal import resample_poly
                g = gcd(sr, SR)
                y = resample_poly(y, SR // g, sr // g, axis=1)
            self.cache[name] = y
        return self.cache[name]

    def table(self, name):
        y = self.sound(name).mean(0)
        return y / (np.max(np.abs(y)) + 1e-12)


class _BakeCtx:
    """What a studio effect sees when it is baked into one rendered event: no automation (live_fx moves only
    live effects), no other tracks (split_chain keeps effects that read them out of the bake)."""
    sr, offset_samples, track = SR, 0, ''

    def __init__(self, bpm):
        self.bpm = bpm

    def param(self, idx, name, default):
        return default

    def note_gr(self, idx, gr):
        pass


def _lane_curve(points, n):
    t = np.array([p[0] for p in points]) * SR
    v = np.array([p[1] for p in points], dtype=np.float64)
    return np.interp(np.arange(n), t, v)


def _perform(inst, notes, total, bpm, root, expr, beat0=0.0):
    """A performer voice (module with perform(notes, total_n, sr, bpm, lanes, **params)) plays the whole event.
    beat0: the song beat at sample 0, for a voice that keys its randomness on it."""
    from .. import dsp, voices
    mod = voices.load(inst['voice'], root)
    lanes = {k: _lane_curve(p, total) for k, p in (expr or {}).items()}
    kw = instruments.performer_kw(mod.perform, inst.get('params'), beat0, root, inst.get('tuning'),
                                  voice=inst['voice'])
    y = np.asarray(mod.perform(notes, total, SR, bpm=bpm, lanes=lanes, **kw), dtype=np.float64)
    y = np.stack([y, y]) if y.ndim == 1 else y
    out = np.zeros((2, total))
    out[:, :min(total, y.shape[1])] = y[:, :total]
    out *= dsp.undb(inst.get('gain_db', 0.0))
    if inst.get('pan'):
        gl, gr = dsp.pan_gains(inst['pan'])
        out *= np.array([[gl], [gr]])
    return out


def _auto_curves(lanes, lead_s, total):
    """Instrument automation {param: [(seconds from the onset, value)]} -> per-sample curves over the event, shaped
    like the studio's (render.automation_curve: linear, log for frequencies, held before and after)."""
    from ..render import LOG_PARAMS
    out = {}
    t = np.arange(total) / SR - lead_s
    for k, pts in lanes.items():
        xs = np.array([p[0] for p in pts], dtype=float)
        vs = np.array([p[1] for p in pts], dtype=float)
        if any(x in k for x in LOG_PARAMS) and np.all(vs > 0):
            out[k] = np.exp(np.interp(t, xs, np.log(vs)))
        else:
            out[k] = np.interp(t, xs, vs)
    return out


def render_event(inst, notes, lead_s, bpm, root):
    """notes: [(start_s, midi, dur_s, vel)] relative to the event onset; lead_s: where the onset sits inside the
    bar of '_bpb' beats (keeps synth and drum seeds and bar-locked LFO phase as in the studio). Returns float32 (2, n) from the onset.
    inst may carry '_bake' (studio effects run on this event), '_expr' (lanes for a performer voice), '_tail'
    (seconds rendered after the last note ends, MAX_TAIL_S if absent), '_beat0' (the song beat at the onset, for
    a performer) and '_chunk' (a slice of a performer's part: {'pre': seconds of earlier notes rendered as context
    and dropped, 'len': seconds kept, 'xf': crossfade seconds, 'fade_in', 'cut'}; a cut chunk stops at len + xf,
    fading out, because the next chunk plays what still rings)."""
    bake, expr, iauto = inst.get('_bake') or [], inst.get('_expr'), inst.get('_auto') or {}
    tail = float(inst.get('_tail', MAX_TAIL_S))
    beat0, chunk, bpb = float(inst.get('_beat0', 0.0)), inst.get('_chunk'), inst.get('_bpb', 4)
    keyed = '_beat0' in inst            # a sampler picking among sounds: where the bar sits in the song
    inst = {k: v for k, v in inst.items()
            if k not in ('_bake', '_expr', '_auto', '_whole', '_tail', '_beat0', '_chunk', '_bpb')}
    lead = int(round(lead_s * SR))
    span = max(s + d for s, _, d, _ in notes)
    total = lead + int((span + tail) * SR)
    if chunk and chunk.get('cut'):
        total = lead + int(round((chunk['pre'] + chunk['len'] + chunk['xf']) * SR))
    shifted = [(s + lead_s, m, d, v) for s, m, d, v in notes]
    if inst.get('performer'):
        y = _perform(inst, shifted, total, bpm, root, expr, beat0 - lead_s * bpm / 60.0)
    else:
        # onset at lead_s into its bar: synth and drum seeds land where the studio puts them
        y = instruments.render_instrument(inst, shifted, total, _auto_curves(iauto, lead_s, total) or None, bpm,
                                          SR, root, beat0 - lead_s * bpm / 60.0 if keyed else 0.0, bpb)
    if bake:
        from .. import fx as studio_fx
        ctx = _BakeCtx(bpm)
        for i, f in enumerate(bake):
            y = studio_fx.apply_fx(y, f, ctx, i)
    y = y[:, lead:]
    if chunk:
        y = y[:, int(round(chunk['pre'] * SR)):].copy()
        x = int(round(chunk['xf'] * SR))
        if chunk.get('cut'):
            y = y[:, :int(round((chunk['len'] + chunk['xf']) * SR))]
            if x:
                y[:, -x:] *= np.linspace(1.0, 0.0, x)
        if chunk.get('fade_in') and x:
            y[:, :x] *= np.linspace(0.0, 1.0, x)
        if chunk.get('cut'):            # its length is the bar: the next chunk takes over at the line
            return np.ascontiguousarray(y, dtype=np.float32)
    lvl = np.max(np.abs(y), axis=0)
    idx = np.nonzero(lvl > 1e-5)[0]
    y = y[:, :idx[-1] + 64] if len(idx) else y[:, :64]
    return np.ascontiguousarray(y, dtype=np.float32)


def check(y):
    if not np.all(np.isfinite(y)):
        return "rendered NaN/inf"
    pk = float(np.max(np.abs(y))) if y.size else 0.0
    if pk > MAX_PEAK:
        return f"one event peaks at {20 * np.log10(pk):+.1f} dBFS (limit {20 * np.log10(MAX_PEAK):+.0f}): the voice is broken or its gain is far too high"
    return None


def self_warm(root):
    """Pay first-use costs before taking jobs: one note of each built-in instrument kind and of every mimic
    profile the project can see (imports, numba loads, profile parsing)."""
    import glob
    from .. import voices
    specs = [{'type': 'synth'}, {'type': 'kick'}, {'type': 'snare'}, {'type': 'hat'}, {'type': 'clap'},
             {'type': 'code', 'voice': 'grand_piano'}]
    for _, d in voices.search_path(root):
        for f in sorted(glob.glob(os.path.join(d, '*.mimic.json'))):
            specs.append({'type': 'mimic', 'profile': os.path.basename(f)[:-len('.mimic.json')]})
    done = []
    for spec in specs:
        try:
            render_event(instruments.normalize(spec), [(0.0, 60, 0.1, 100)], 0.0, 120.0, root)
            done.append(spec.get('profile') or spec.get('voice') or spec['type'])
        except Exception:
            pass
    return done


def main(own, shared, results, root, wid=0):
    """own: jobs for this worker only (warm-ups, the stop signal); shared: render jobs any free worker takes."""
    import queue
    bank = SoundBank(root)
    instruments.set_resolvers(bank.sound, bank.table)
    banks = {root: bank}
    t0 = time.time()
    warmed = self_warm(root)
    results.put(('ready', wid, time.time() - t0, warmed))
    while True:
        try:
            job = own.get_nowait()
        except queue.Empty:
            try:
                job = shared.get(timeout=0.05)
            except queue.Empty:
                continue
        if job is None:
            return
        jid, inst, notes, lead_s, bpm, jroot = job
        t0 = time.time()
        try:
            if jroot not in banks:
                banks[jroot] = SoundBank(jroot)
            instruments.set_resolvers(banks[jroot].sound, banks[jroot].table)
            y = render_event(inst, notes, lead_s, bpm, jroot)
            err = check(y)
            if err:
                y = None
        except Exception as e:  # a voice bug must not kill the worker; the engine reports it on the track
            y, err = None, f"{type(e).__name__}: {e}"
        results.put((jid, y, time.time() - t0, err))
