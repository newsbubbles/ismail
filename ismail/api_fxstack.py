"""Named effect stacks (ledger:M177, Nate 10-07: "a stack of effects in the engine that you can save as like a
specific effect stack and you can also store like how that runs on this device"). A stack is a named effect chain
with notes, kept in the library (~/.ismail/fx_stacks, or $ISMAIL_FX_STACKS) or in a song folder (<song>/fx_stacks);
the built-in ones come with ismail (voices/fx_stacks.json). What a stack costs is the machine's, not the song's: a
10 s test through the chain the way the live engine runs it, kept per device in the library's costs.json, so
live_track(fx='stack:<name>') and live_load can say before a set that a chain will not keep up.
"""
import json
import os
import platform
import re
import time

import numpy as np

from .api import OpError, op

BUILTIN = os.path.join(os.path.dirname(__file__), 'voices', 'fx_stacks.json')
TEST_S = 10.0
SLOW_X = 4.0       # under this many times realtime, a chain is named as a risk on a live track
NAME = re.compile(r'^[a-z0-9][a-z0-9_-]{0,47}$')


def library():
    return os.environ.get('ISMAIL_FX_STACKS') or os.path.join(os.path.expanduser('~'), '.ismail', 'fx_stacks')


def device():
    """This machine, as the costs are keyed: its name and processor."""
    cpu = platform.processor() or platform.machine()
    return f"{platform.node()} ({cpu})"


def _builtin():
    try:
        with open(BUILTIN, encoding='utf8') as f:
            return json.load(f).get('stacks', {})
    except (OSError, ValueError):
        return {}


def _dirs(project=None):
    """[(where, dir)], first match wins: the song's, then the library."""
    return ([('song', os.path.join(os.path.abspath(project), 'fx_stacks'))] if project else []) + \
        [('library', library())]


def find(name, project=None):
    """(where, stack) or raise with the names there are."""
    for where, d in _dirs(project):
        p = os.path.join(d, name + '.json')
        if os.path.isfile(p):
            with open(p, encoding='utf8') as f:
                return where, json.load(f)
    if name in _builtin():
        return 'built-in', dict(_builtin()[name], name=name)
    have = ', '.join(sorted(all_stacks(project))) or 'none'
    raise OpError(f"no effect stack {name!r}; there are: {have}. fx_stack_save(name, chain) makes one")


def all_stacks(project=None):
    out = {n: ('built-in', dict(s, name=n)) for n, s in _builtin().items()}
    for where, d in reversed(_dirs(project)):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith('.json') and f != 'costs.json':
                    try:
                        with open(os.path.join(d, f), encoding='utf8') as fh:
                            out[f[:-5]] = (where, json.load(fh))
                    except (OSError, ValueError):
                        continue
    return out


def _check_chain(chain):
    from . import fx as F
    if isinstance(chain, str):
        try:
            chain = json.loads(chain)
        except ValueError:
            raise OpError("chain: a list of effect dicts (fx_help), e.g. [{'type': 'compressor', 'threshold_db': -18}]")
    if not isinstance(chain, list) or not chain:
        raise OpError("chain: a non-empty list of effect dicts (fx_help)")
    for i, f in enumerate(chain):
        try:
            F.normalize(f)
        except F.FxError as e:
            raise OpError(f"chain[{i}]: {e}")
    return chain


# ------------------------------------------------------------------ cost on this machine

def _costs_path():
    return os.path.join(library(), 'costs.json')


def _costs():
    try:
        with open(_costs_path(), encoding='utf8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cost_here(name):
    """The last measured cost of a stack on this machine, or None."""
    return _costs().get(name, {}).get(device())


def _cpu_busy():
    try:
        import psutil
        return round(psutil.cpu_percent(interval=0.3))
    except Exception:
        return None


def measure(chain, sr=44100, bpm=120.0, seconds=TEST_S):
    """Run `seconds` of a test signal (plucked notes over a quiet bed, stereo) through the chain the way the live
    engine does: effects with a live processor in 1024-sample blocks, the rest (baked) over the whole window as the
    render workers would. Returns {'x_rt', 'live_s', 'baked_s'}: how many times faster than realtime it ran."""
    from . import fx as F
    from .live import fx_blocks as FB
    from .live import graph as G
    n = int(seconds * sr)
    rng = np.random.default_rng(7)
    t = np.arange(n) / sr
    env = np.exp(-((t * bpm / 60) % 1.0) * 6)                   # a note every beat
    x = 0.3 * env * np.sin(2 * np.pi * 110 * t * (1 + 0.5 * (np.floor(t * bpm / 60) % 3)))
    x = np.stack([x, x]) + 0.003 * rng.standard_normal((2, n))
    norm = [F.normalize(f) for f in chain]
    bake, run = G.split_chain(norm)

    class _Ctx:
        def __init__(self):
            self.sr, self.bpm, self.track = sr, bpm, ''

        def param(self, idx, name, default):
            return default

    t0 = time.perf_counter()
    y = x
    for i, f in enumerate(bake):
        y = F.apply_fx(y, f, _Ctx(), i)
    baked = time.perf_counter() - t0
    procs = [FB.make(f, FB.Env(sr, bpm)) for f in run]
    blk = FB.Block()
    t0 = time.perf_counter()
    for p0 in range(0, n, 1024):
        b = y[:, p0:p0 + 1024]
        blk.pos, blk.n = p0, b.shape[1]
        for p in procs:
            b = p.process(b, blk)
    live = time.perf_counter() - t0
    return {'x_rt': round(seconds / max(baked + live, 1e-6), 1), 'live_s': round(live, 3),
            'baked_s': round(baked, 3)}


def _record_cost(name, m):
    m = dict(m, at=time.strftime('%Y-%m-%d %H:%M'), cpu_busy=_cpu_busy())
    c = _costs()
    c.setdefault(name, {})[device()] = m
    os.makedirs(library(), exist_ok=True)
    with open(_costs_path(), 'w', encoding='utf8') as f:
        json.dump(c, f, indent=1)
    return m


def cost_text(name, m):
    if not m:
        return f"{name}: not measured on this machine yet (fx_stack_measure('{name}'))"
    busy = f", CPU {m['cpu_busy']}% busy then" if m.get('cpu_busy') is not None else ''
    risk = (f" RISK: under {SLOW_X:g}x, a few tracks with this chain can fall behind live; bake it into the "
            f"song or use it on one track" if m['x_rt'] < SLOW_X else '')
    return f"{name}: runs {m['x_rt']:g}x realtime on this machine (measured {m['at']}{busy}).{risk}"


def chain_costs(chains):
    """For live_track and live_load: a cost line for each chain that is a saved stack (by name or by equal
    content), or [] when none is."""
    if not chains:
        return []
    known = {json.dumps(s.get('chain'), sort_keys=True): n for n, (_, s) in all_stacks().items()}
    out = []
    for c in chains:
        name = c if isinstance(c, str) else known.get(json.dumps(c, sort_keys=True))
        if name:
            out.append(cost_text(name, cost_here(name)))
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ ops

@op()
def fx_stack_save(name: str, chain, notes: str = '', project: str = None, measure_cost: bool = True) -> str:
    """Save an effect chain under a name, so any set or song can use it: live_track(fx='stack:<name>'), or
    fx_stack_load for the chain itself. chain: a list of effect dicts (fx_help). notes: what it is for and what the
    person said about it ("dirty P-bass: 'it's dirty'"). project=<song folder> keeps it in that song
    (<song>/fx_stacks); without it, in the library for every song. A 10 s test then measures how fast it runs on
    this machine (measure_cost=False skips it); live_track and live_load name a chain that may not keep up."""
    if not NAME.match(name or ''):
        raise OpError("name: lowercase letters, digits, - and _ (e.g. 'dirty_pbass')")
    chain = _check_chain(chain)
    where, d = _dirs(project)[0]
    os.makedirs(d, exist_ok=True)
    rec = {'name': name, 'chain': chain, 'notes': notes, 'saved': time.strftime('%Y-%m-%d %H:%M')}
    with open(os.path.join(d, name + '.json'), 'w', encoding='utf8') as f:
        json.dump(rec, f, indent=1)
    L = [f"saved effect stack {name!r} in the {where} ({d}): {len(chain)} effects "
         f"({', '.join(f['type'] for f in chain)})"]
    if measure_cost:
        L.append(cost_text(name, _record_cost(name, measure(chain))))
    L.append(f"use it: live_track(project, track, fx='stack:{name}'), or fx_stack_load('{name}') for the chain")
    return '\n'.join(L)


@op()
def fx_stack_load(name: str, project: str = None) -> str:
    """A saved effect stack: its chain (JSON, ready for live_track fx= or fx_add one by one), its notes, and what
    it costs on this machine. project: look in that song's stacks first."""
    where, s = find(name, project)
    return '\n'.join([f"{name} ({where}){': ' + s['notes'] if s.get('notes') else ''}",
                      cost_text(name, cost_here(name)),
                      'chain: ' + json.dumps(s['chain'])])


@op()
def fx_stack_list(project: str = None) -> str:
    """Every saved effect stack (built-in, library, and the song's when project is given), with its notes and its
    cost on this machine."""
    st = all_stacks(project)
    if not st:
        return "no effect stacks yet: fx_stack_save(name, chain, notes) saves one"
    L = [f"effect stacks ({device()}):"]
    for n, (where, s) in sorted(st.items()):
        m = cost_here(n)
        L.append(f"- {n} [{where}] {', '.join(f['type'] for f in s.get('chain', []))}"
                 + (f": {s['notes']}" if s.get('notes') else '')
                 + (f" ({m['x_rt']:g}x realtime here)" if m else " (not measured here)"))
    return '\n'.join(L)


@op()
def fx_stack_measure(name: str = None, project: str = None) -> str:
    """Measure a stack's cost on this machine again (10 s through the chain the way the live engine runs it), or
    every stack with name=None: after a machine change, or when a set runs behind."""
    names = [name] if name else sorted(all_stacks(project))
    return '\n'.join(cost_text(n, _record_cost(n, measure(find(n, project)[1]['chain']))) for n in names)
