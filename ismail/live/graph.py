"""Live mixing graph: effect chains on tracks and send buses, latency compensation, parameter ramps.

Every path from a track to the output is delayed to the same total (LAT_BUDGET samples), so tracks stay aligned
whatever lookahead their effects need. A chain that is replaced keeps running on silence until its tail has
died away (reverbs ring out instead of being cut).
"""
import numpy as np

from . import dsp_blocks as dsp, fx_blocks as F
from ..dsp import SR

LAT_BUDGET = 4096           # ~93 ms: the most lookahead any track path + bus may add up to
LOG_PARAMS = ('cutoff', 'freq', '_hz')
MIN_RAMP_S = 0.02           # even an instant change glides this long (no zipper clicks)
QUIET = 1e-5                # a retiring chain below this for RETIRE_S is dropped
RETIRE_S = 0.5
RETIRE_MAX_S = 12.0


class GraphError(ValueError):
    pass


class Ramp:
    """A param moving from v0 at sample p0 to v1 at p1 (log-space for frequencies), held after."""

    def __init__(self, p0, v0, p1, v1, log, gen=0, section=None):
        self.p0, self.v0, self.p1, self.v1 = int(p0), float(v0), int(max(p1, p0 + 1)), float(v1)
        self.log = log and v0 > 0 and v1 > 0
        self.gen = gen              # the effect chain this ramp was written for (a chain swap drops the others)
        self.section = section      # the set section it was scheduled for (live_queue(cancel_section=) drops it)

    def curve(self, pos, n):
        if pos >= self.p1:
            return self.v1
        t = np.clip((pos + np.arange(n) - self.p0) / (self.p1 - self.p0), 0.0, 1.0)
        if self.log:
            return np.exp(np.log(self.v0) + (np.log(self.v1) - np.log(self.v0)) * t)
        return self.v0 + (self.v1 - self.v0) * t

    def value(self, pos):
        c = self.curve(pos, 1)
        return float(c if np.isscalar(c) else c[0])


class Schedule:
    """Ramps of one param in time order. Like clips: a ramp added at p0 replaces those starting at or after p0
    (for the same effect chain: ramps written for a chain that is scheduled to replace this one are kept)."""

    def __init__(self):
        self.r = []

    def add(self, ramp):
        self.r = sorted([x for x in self.r if x.p0 < ramp.p0 or x.gen != ramp.gen] + [ramp], key=lambda x: x.p0)

    def keep_gen(self, gen):
        """Drop the ramps written for other chains (at a chain swap). Returns whether any are left."""
        self.r = [x for x in self.r if x.gen == gen]
        return bool(self.r)

    def hold(self, pos, gen):
        """Cancel every move of chain `gen` from `pos` on: the param stays where it is then."""
        v = self.value(pos)
        self.r = [x for x in self.r if x.p0 < pos or x.gen != gen] + [Ramp(pos, v, pos + 1, v, False, gen)]
        self.r.sort(key=lambda x: x.p0)
        return v

    def drop_section(self, section, pos):
        """Take back the moves of `section` from `pos` on: the ones starting then or later go, one gliding through
        `pos` stops where it is then (same curve up to there). Returns (dropped, held); a param left with nothing
        scheduled holds the value it had at `pos`."""
        mine = [x for x in self.r if x.section == section]
        if not mine:
            return 0, 0
        v = self.value(pos)
        gen = mine[-1].gen
        gone = [x for x in mine if x.p0 >= pos]
        held = 0
        for x in mine:
            if x.p0 < pos < x.p1:
                x.v1, x.p1 = x.value(pos), int(pos)       # a glide cut short follows the same line up to there
                held += 1
        self.r = [x for x in self.r if x not in gone]
        if not self.r:
            self.r = [Ramp(pos, v, pos + 1, v, False, gen)]
        return len(gone), held

    def value(self, pos):
        cur = [x for x in self.r if x.p0 <= pos]
        return cur[-1].value(pos) if cur else self.r[0].v0

    def curve(self, pos, n):
        while len(self.r) > 1 and self.r[1].p0 <= pos:      # fully superseded
            self.r.pop(0)
        first = self.r[0]
        if len(self.r) == 1 or self.r[1].p0 >= pos + n:
            return first.curve(pos, n) if pos + n > first.p0 or pos >= first.p0 else first.v0
        t = pos + np.arange(n)
        out = np.full(n, first.v0)
        for x in self.r:
            m = t >= x.p0
            if m.any():
                out[m] = np.broadcast_to(x.curve(pos, n), (n,))[m]
        return out

    @property
    def target(self):
        return self.r[-1]


def deps(fxs):
    """Tracks whose audio or notes an effect chain reads."""
    out = set()
    for f in fxs:
        if f['type'] == 'compressor' and f.get('sidechain'):
            out.add(f['sidechain'])
        if f['type'] == 'vocoder' and f.get('modulator'):
            out.add(f['modulator'])
        if f['type'] == 'duck' and f.get('source'):
            out.add(f['source'])
    return out


def audio_deps(fxs):
    return {f['sidechain'] for f in fxs if f['type'] == 'compressor' and f.get('sidechain')} | \
           {f['modulator'] for f in fxs if f['type'] == 'vocoder' and f.get('modulator')}


def normalize_chain(fxs, where):
    if fxs is None:
        return []
    if not isinstance(fxs, list):
        raise GraphError(f"{where}: fx is a list of effect dicts (fx_help lists types and params)")
    out = []
    for i, f in enumerate(fxs):
        try:
            out.append(F.normalize(f))
        except F.FxError as e:
            raise GraphError(f"{where} fx[{i}]: {e}")
        if out[-1]['type'] == 'vocoder' and str(out[-1].get('modulator') or '').startswith('sound:'):
            raise GraphError(f"{where} fx[{i}]: live vocoders take a live track as modulator, not a sound")
    return out


def split_chain(fxs):
    """(bake, run): effects up to and including the last one without a live processor are baked (the render
    workers run the studio function on each note or phrase), the rest run live. Order is never changed."""
    k = 0
    for i, f in enumerate(fxs):
        if f['type'] not in F.PROCS:
            k = i + 1
    return fxs[:k], fxs[k:]


_DN = {}


def _anti_denormal(n):
    """1e-18 of DC plus Nyquist, added at every processor's input: filter and envelope states decaying in silence
    reach denormal floats otherwise, which run 10-30x slower on x86 (a fuzz left in silence took a guitar chain
    to 100% of real time). -360 dB; it survives a 280 dB cut either way (high-pass or low-pass)."""
    a = _DN.get(n)
    if a is None:
        a = _DN[n] = np.tile(1e-18 * (1 + (-1.0) ** np.arange(n)), (2, 1))
    return a


class Chain:
    def __init__(self, fxs, bpm, dry_default, bake=None):
        self.fx = fxs
        self.bake = list(bake or [])
        self.procs = []
        for f in fxs:
            dry = float(f['dry']) if f.get('dry') is not None else dry_default
            self.procs.append(F.make(f, F.Env(SR, bpm, dry, 0)))
        self.latency = sum(p.latency for p in self.procs)
        self.noise = sum(p.noise for p in self.procs)      # self-noise of the chain (amp hiss, tape hiss)
        self.hold_s = sum(p.hold_s for p in self.procs)    # sound still in flight after the output goes quiet
        self.gr = {}
        self.gen = 0                # set when the chain is scheduled to replace another (ramps carry it)

    def process(self, x, block_for):
        dn = _anti_denormal(x.shape[1])
        for i, p in enumerate(self.procs):
            x = p.process(x + dn, block_for(i))
        return x

    def retime(self, bpm, origin):
        """A live tempo change: beat-synced effects follow `bpm`, with song beat 0 at sample `origin`."""
        for p in self.procs:
            p.retime(bpm, origin)

    def describe(self, gr=True):
        parts = [f['type'] + ' (baked)' for f in self.bake]
        for i, f in enumerate(self.fx):
            s = f['type'] + (':' + f['mode'] if f['type'] in ('filter', 'distortion') else '')
            if gr and self.gr.get(i, 0.0) < -0.05:
                s += f" ({self.gr[i]:.1f} dB)"
            parts.append(s)
        return ' > '.join(parts) if parts else 'none'


class Path:
    """A track or bus in the graph: its chain, the delay that brings it to LAT_BUDGET, and retiring chains."""

    def __init__(self, chain, extra_lat=0):
        self.chain = chain
        self.lag = F._Lag(LAT_BUDGET - chain.latency - extra_lat, signal=True)
        self.retiring = []          # [chain, lag, quiet samples, age samples]

    def retire_to(self, chain, extra_lat=0):
        self.retiring.append([self.chain, self.lag, 0, 0])
        self.chain = chain
        self.lag = F._Lag(LAT_BUDGET - chain.latency - extra_lat, signal=True)

    def run_retiring(self, n, blocks_for):
        """Outputs of the old chains (fed silence), already delayed by their own lags; drops the ones gone quiet.
        blocks_for(chain) -> (processor index -> Block)."""
        if not self.retiring:
            return None
        acc = np.zeros((2, n))
        keep = []
        for r in self.retiring:
            y = r[0].process(np.zeros((2, n)), blocks_for(r[0]))
            y = r[1](y, n)
            acc += y
            r[2] = r[2] + n if np.max(np.abs(y)) < QUIET else 0
            r[3] += n
            if r[2] < RETIRE_S * SR and r[3] < RETIRE_MAX_S * SR:
                keep.append(r)
        self.retiring = keep
        return acc


class LiveBlock(F.Block):
    """One processor's view of one block in the live engine."""

    def __init__(self, eng, target, chain, idx, pos, n, post, onsets):
        self.eng, self.target, self.chain, self.idx = eng, target, chain, idx
        self.pos, self.n, self.post, self._ons = pos, n, post, onsets

    def param(self, name, default):
        sch = self.eng.ramps.get((self.target, self.idx, name))
        return default if sch is None else sch.curve(self.pos, self.n)

    def key(self, track):
        y = self.post.get(track)
        return np.zeros((2, self.n)) if y is None else y

    def onsets(self, track, lo, hi):
        o = self._ons.get(track)
        if o is None or not len(o):
            return np.zeros(0)
        return o[(o >= lo) & (o < hi)]

    def modulator(self, ref):
        y = self.post.get(ref)
        return np.zeros(self.n) if y is None else y.mean(0)

    def note_gr(self, gr_db):
        self.chain.gr[self.idx] = min(self.chain.gr.get(self.idx, 0.0), gr_db)


def fader_gains(volume_db, pan):
    g = 10 ** (volume_db / 20)
    gl, gr = dsp.pan_gains(pan)
    return g * float(gl), g * float(gr)
