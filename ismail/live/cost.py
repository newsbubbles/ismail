"""What a deck costs before it plays (ledger:M293, the Live DJ's stream set of 10-22): the silent gap after a
live_load is mostly the first bar's render, and the agent could not see it coming. A dry run loads the song's first
bar on a silent engine of its own in this process (no live_start, no workers, no device, nothing is played: the
same path live_parity uses) and times what the live engine does with it: each track's render of that bar (and the
first note of each instrument, which pays its first-use cost), and the mixer's work per block while the bar plays.
Wall seconds per bar against the bar's own length is the real-time factor: above 1, that track alone renders
slower than the music plays."""
import json
import math
import os
import tempfile
import time

from ..dsp import SR
from . import worker
from .engine import BLOCK, Engine, warm_effects


class CostError(ValueError):
    pass


def default_workers():
    """live_start's default render workers (cores - 4, from 2 to 4)."""
    return min(4, max(2, (os.cpu_count() or 4) - 4))


def run(song, bars=None, bpm=None, workers=None, performers=()):
    """-> report dict: {'song', 'bar', 'bpm', 'bar_s', 'tracks': [{'track', 'render_s', 'rtf', 'events',
    'slowest_event_s', 'first_note_s', 'fx_ms_per_block'}] heaviest first, 'total': {'render_s', 'rtf', 'workers',
    'first_bar_wait_s', 'first_note_s'}, 'mixer': {'block_ms', 'mean_ms', 'peak_ms', 'load', 'peak_load'},
    'load_reply'}. bars: the deck window (its first bar is measured); bpm: the house tempo (default the song's)."""
    song = os.path.abspath(song)
    fn = os.path.join(song, 'project.json')
    if not os.path.isfile(fn):
        raise CostError(f"no ismail project at {song} (a song folder holds project.json)")
    with open(fn, encoding='utf8') as f:
        d = json.load(f)
    bpb = int(d.get('beats_per_bar', 4))
    length = int(d.get('length_bars', 0)) or 1
    a = 1 if not bars else int(bars[0])
    if not 1 <= a <= length:
        raise CostError(f"bars {bars}: the song has bars 1-{length}")
    house = float(bpm or d['bpm'])
    workers = int(workers or default_workers())
    bar_s = bpb * 60.0 / house
    per = {}

    def row(track):
        return per.setdefault(track, {'render_s': 0.0, 'events': 0, 'slowest_event_s': 0.0, 'first_note_s': 0.0,
                                      'fx_s': 0.0, 'blocks': 0})

    # a started engine has paid these already: its effect kernels compile before the audio starts and each render
    # worker warms the built-in instruments (and the mimic profiles it can see) before it takes a job
    warm_effects()
    worker.self_warm(song)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        eng = Engine(tmp, bpm=house, bpb=bpb, workers=0, device='none')
        submit = eng._submit

        def timed(key, track, notes_s, lead_s, warm=False, **kw):
            # workers=0: the engine renders the job right here, exactly as a worker would
            t0 = time.perf_counter()
            submit(key, track, notes_s, lead_s, warm=warm, **kw)
            dt = time.perf_counter() - t0
            r = row(track)
            if warm:
                r['first_note_s'] += dt
            else:
                r['render_s'] += dt
                r['events'] += 1
                r['slowest_event_s'] = max(r['slowest_event_s'], dt)
        eng._submit = timed
        try:
            head = eng.cmd_load('C', song, bars=[a, a], at='next_bar', loop=False, performers=list(performers))
        except Exception as e:
            eng.shutdown()
            raise CostError(str(e))
        # each track's effect chain, timed per block while the bar mixes
        for name, t in eng.tracks.items():
            ch = t['path'].chain
            proc = ch.process

            def chain_timed(x, blocks, proc=proc, r=row(name)):
                t0 = time.perf_counter()
                y = proc(x, blocks)
                r['fx_s'] += time.perf_counter() - t0
                r['blocks'] += 1
                return y
            ch.process = chain_timed
        start = eng.sample(min(c.start for c in eng.tl.clips.values())) if eng.tl.clips else 0
        end = start + int(round(bar_s * SR))
        blocks = []
        try:
            while eng.pos < end:
                eng.tick()
                timing = eng.pos >= start
                t0 = time.perf_counter()
                eng.mix_block()
                if timing:
                    blocks.append(time.perf_counter() - t0)
        finally:
            eng.shutdown()
    rows = []
    for name, r in per.items():
        if name is None:
            continue
        rows.append({'track': name.split('.', 1)[1] if name.startswith('C.') else name,
                     'render_s': r['render_s'], 'rtf': r['render_s'] / bar_s, 'events': r['events'],
                     'slowest_event_s': r['slowest_event_s'], 'first_note_s': r['first_note_s'],
                     'fx_ms_per_block': 1000.0 * r['fx_s'] / r['blocks'] if r['blocks'] else 0.0})
    rows.sort(key=lambda x: (-x['render_s'], x['track']))
    total = sum(x['render_s'] for x in rows)
    first = sum(x['first_note_s'] for x in rows)
    slowest = max((x['slowest_event_s'] for x in rows), default=0.0)
    block_s = BLOCK / SR
    mean = sum(blocks) / len(blocks) if blocks else 0.0
    peak = max(blocks, default=0.0)
    return {'song': d.get('name') or os.path.basename(song), 'bar': a, 'bpm': house, 'bar_s': bar_s, 'tracks': rows,
            'total': {'render_s': total, 'rtf': total / bar_s, 'workers': workers, 'first_note_s': first,
                      # the bar's events spread over the workers; one slow event cannot be split
                      'first_bar_wait_s': max(total / workers, slowest) + first / workers},
            'mixer': {'block_ms': 1000 * block_s, 'mean_ms': 1000 * mean, 'peak_ms': 1000 * peak,
                      'load': mean / block_s, 'peak_load': peak / block_s, 'blocks': len(blocks)},
            'load_reply': head}


def report(r, top=12):
    """The report as text, heaviest track first."""
    t, m = r['total'], r['mixer']
    L = [f"deck cost of {r['song']}, bar {r['bar']} at {r['bpm']:g} BPM (a bar is {r['bar_s']:.2f} s), measured "
         f"offline on this machine, nothing played:",
         f"  render: {t['render_s']:.2f} s of work for the bar = {t['rtf']:.2f}x real time; with {t['workers']} "
         f"render workers the first bar is ready about {t['first_bar_wait_s']:.1f} s after live_load"
         + (f" (first notes of each instrument: {t['first_note_s']:.2f} s)" if t['first_note_s'] >= 0.01 else ''),
         f"  mixer: {m['mean_ms']:.2f} ms per {m['block_ms']:.1f} ms block = {100 * m['load']:.0f}% of real time "
         f"(peak block {100 * m['peak_load']:.0f}%) for this deck alone",
         "  per track (heaviest first): render s for the bar, x real time, events, slowest event, first note, "
         "effects ms per block"]
    for x in r['tracks'][:top]:
        L.append(f"    {x['track']:<16} {x['render_s']:6.3f} s  {x['rtf']:5.2f}x  {x['events']:3d} ev  "
                 f"slowest {x['slowest_event_s']:.3f} s  first {x['first_note_s']:.3f} s  fx {x['fx_ms_per_block']:.2f} ms")
    if len(r['tracks']) > top:
        rest = r['tracks'][top:]
        L.append(f"    ... {len(rest)} more, {sum(x['render_s'] for x in rest):.3f} s together")
    heavy = [x['track'] for x in r['tracks'] if x['rtf'] >= 1.0]
    if heavy:
        L.append(f"  slower than real time on its own: {', '.join(heavy)} (load the deck bars ahead, keep it cued "
                 f"until it is rendered, or track_freeze what does not need to move live)")
    if t['first_bar_wait_s'] > r['bar_s']:
        bars = math.ceil(t['first_bar_wait_s'] / r['bar_s'])
        L.append(f"  a load straight on air waits about {bars} bar{'s' if bars > 1 else ''} before this deck sounds: "
                 f"load it at least {bars + 1} bars before the transition")
    if m['load'] > 0.5:
        L.append("  the mixer alone uses over half of real time for this deck: two decks at once may drop out")
    return '\n'.join(L)
