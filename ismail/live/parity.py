"""Studio against live: the same bars rendered by the studio and played by a silent live engine deck, compared per
track, per bus and as a mix. Offline and deterministic: the engine renders inline (no workers, nothing is late) and
its master safety chain is bypassed, so any difference is the live path itself. The studio side is the song with its
master effects left out, because a deck has none (it says so). The live mix is the deck's input: its strip is an
allpass at flat settings (the isolator's bands sum flat in level, not in phase), which is the DJ's, not the song's."""
import copy
import json
import os
import shutil
import tempfile

import numpy as np
from scipy import signal as sg

from ..dsp import SR
from . import graph as G
from .engine import Engine, TempoMap
from .ops import _song_performers

BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
# a pass: what a listener cannot tell apart on a level meter or a spectrum, and the envelope follows the studio
LEVEL_DB = 0.5
CORR = 0.99
BAND_DB = 1.0
# a take that lines up within this many ms is the same take, offset (a deck's notes round to its own mix samples);
# the fit is looked for this far either way, so a take further off is still found and DIFFERS
LAG_MS = 3.0
LAG_SEARCH_MS = 100.0


class _Unity:
    """Stands in for the master safety chain: trim, rider and limiter would change the take, not the engine."""
    la = 0

    def process(self, x):
        return x


def _studio(d, root, bars, tracks):
    from ..render import Renderer
    d = copy.deepcopy(d)
    d.setdefault('master', {})['fx'] = []
    R = Renderer(d, root, bars[0], bars[1] + 1, tracks, cache=False)
    y, stems = R.run()
    out = {('track:' + k if not k.startswith('bus:') else k): v for k, v in stems.items()}
    out['mix'] = y
    return out


def _live(song, d, bars, n):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        bpm, bpb = float(d['bpm']), d.get('beats_per_bar', 4)
        eng = Engine(tmp, bpm=bpm, bpb=bpb, workers=0, device='none')
        # each event lands on the nearest mix sample. When a beat is not a whole number of samples, the engine's bar
        # line and the song's sit at different fractions of a sample, so some notes land one sample apart: a deck
        # in a set does that (it cannot hear it). Here the engine's clock starts so its next bar line has the
        # song window's fraction, and the take is sample for sample (ledger:M285(a))
        spb = 60.0 / bpm
        t_s = (float(d.get('offset_sec', 0.0)) + (bars[0] - 1) * bpb * spb) * SR
        frac = t_s - np.floor(t_s)
        eng.tmap = TempoMap([(0, bpb - (int(0.75 * bpb * spb * SR) + frac) / (spb * SR), bpm)])
        eng.safety = _Unity()
        got = {}

        def tap(key, p0, y, lag):
            got.setdefault(key, []).append((p0 - lag, y.copy()))
        eng.tap = tap
        # each track's render speed: seconds of audio its renders gave, and the seconds they took (the engine keeps
        # only a running estimate, and that goes with it at shutdown, ledger:M285(e))
        spent = {}
        result = eng._result

        def timed(jid, y, secs, err):
            job = eng.jobs.get(jid)
            if job is not None and not job['warm'] and not job.get('midi'):
                a = spent.setdefault('track:' + job['track'].split('.', 1)[-1], [0.0, 0.0])
                a[0] += job['audio_s']
                a[1] += secs
            return result(jid, y, secs, err)
        eng._result = timed
        head = eng.cmd_load('P', song, bars=list(bars), at='bar:2', loop=False, performers=_song_performers(song))
        start = eng.sample(min(c.start for c in eng.tl.clips.values()))
        while eng.pos < start + n + G.LAT_BUDGET:
            eng.tick()
            eng.mix_block()
        eng.shutdown()
    got['mix'] = got.pop('deck:P', [])
    out = {}
    for key, blocks in got.items():
        y = np.zeros((2, n))
        for p, b in blocks:
            a, z = max(p, start), min(p + b.shape[1], start + n)
            if z > a:
                y[:, a - start:z - start] += b[:, a - p:z - p]
        out[key.replace('track:P.', 'track:').replace('bus:P.', 'bus:')] = y
    return out, head, {k: {'audio_s': a, 'render_s': r} for k, (a, r) in spent.items()}


def _db(x):
    return 10 * np.log10(np.mean(x ** 2) + 1e-20)


def _env_db(x, floor_db=50):
    f = int(0.05 * SR)
    m = x.mean(0)
    e = np.array([np.sqrt(np.mean(m[i:i + f] ** 2)) for i in range(0, len(m) - f + 1, f)])
    d = 20 * np.log10(e + 1e-9)
    return np.maximum(d, d.max() - floor_db)


def _bands(x):
    # Hann-framed: one unwindowed FFT over a take cut mid-note leaks a broadband top 50-60 dB under a loud note
    fr, S = sg.welch(x.mean(0), SR, nperseg=min(8192, x.shape[1]))
    return np.array([10 * np.log10(S[(fr >= c / 2 ** .5) & (fr < c * 2 ** .5)].sum() + 1e-20) for c in BANDS])


def _lag(st, lv, reach):
    """The lag (samples, + = live late) where live lines up best with the studio within +-reach, from the first 4 s;
    0 when the two never line up at all (noise or a free phase)."""
    m = min(st.shape[1], 4 * SR)
    a, b = lv.mean(0)[:m], st.mean(0)[:m]
    xc = sg.correlate(a, b, mode='full', method='fft') / (np.sqrt(np.sum(a * a) * np.sum(b * b)) + 1e-30)
    r = min(reach, m - 1)
    w = np.abs(xc[m - 1 - r:m + r])
    return int(np.argmax(w)) - r if w.max() > 0.5 else 0


def _shift(st, lv, lag):
    """The two takes lined up at `lag` (live `lag` samples late): the overlap only."""
    if lag > 0:
        return st[:, :-lag], lv[:, lag:]
    if lag < 0:
        return st[:, -lag:], lv[:, :lag]
    return st, lv


def compare(studio, live):
    """Numbers for one pair of (2, n) takes: the studio level (dBFS rms), live minus studio level, 50 ms envelope
    correlation (dB, 50 dB floor), the residual (live - studio) under the studio level, octave bands 63..8k live
    minus studio (bands under 60 dB below the loudest are left out, as None), and 'lag': the samples live is late
    (+) or early (-) at its best fit within +-LAG_SEARCH_MS. A lag within +-LAG_MS is an offset, not a difference:
    the other numbers are then judged with live moved back by it. A lag beyond it is judged where the take sits."""
    n = min(studio.shape[1], live.shape[1])
    st, lv = studio[:, :n], live[:, :n]
    if _db(st) < -100 and _db(lv) < -100:
        return {'silent': True}
    lag = _lag(st, lv, int(LAG_SEARCH_MS * SR / 1000))
    if abs(lag) <= LAG_MS * SR / 1000:
        st, lv = _shift(st, lv, lag)
    es, el = _env_db(st), _env_db(lv)
    corr = float(np.corrcoef(es, el)[0, 1]) if es.std() > 1e-6 and el.std() > 1e-6 else float(np.allclose(es, el, atol=0.5))
    bs, bl = _bands(st), _bands(lv)
    keep = bs > bs.max() - 60
    return {'silent': False, 'studio_db': _db(st), 'level_db': _db(lv) - _db(st), 'corr': corr, 'resid_db': _db(lv - st) - _db(st),
            'lag': lag,
            'bands': [float(b) if k else None for b, k in zip(bl - bs, keep)]}


def _samples(lag):
    return f"{abs(lag)} sample{'s' if abs(lag) != 1 else ''} {'late' if lag > 0 else 'early'}"


def verdict(m):
    """'ok', 'ok (same, offset N samples ...)' or the reasons it is not."""
    if m.get('silent'):
        return 'ok (silent in both)'
    why = []
    # a take further off than an offset is said first: the envelope and the bands then differ by that alone
    small = abs(m['lag']) <= LAG_MS * SR / 1000
    if m['lag'] and not small:
        why.append(f"{_samples(m['lag'])} ({abs(m['lag']) * 1000 / SR:.1f} ms, past +-{LAG_MS:g} ms)")
    if abs(m['level_db']) > LEVEL_DB:
        why.append(f"level {m['level_db']:+.1f} dB")
    if m['corr'] < CORR:
        why.append(f"envelope corr {m['corr']:.3f}")
    worst = max((abs(b) for b in m['bands'] if b is not None), default=0.0)
    if worst > BAND_DB:
        why.append(f"a band off by {worst:.1f} dB")
    if why:
        return 'DIFFERS: ' + ', '.join(why)
    return f"ok (same, offset {_samples(m['lag'])})" if m['lag'] else 'ok'


def run(song, bars, tracks=None):
    """-> ({key: metrics}, takes {key: (studio, live)}, the deck's load message). Keys: 'mix', 'track:<name>',
    'bus:<name>', judged from the window's second bar, and 'mix, bar <a> (edge)' for the first. A track's metrics
    carry 'speed_x', its live render speed (x realtime) when it rendered anything. tracks limits both sides to those tracks (the mix is then theirs alone)."""
    song = os.path.abspath(song)
    with open(os.path.join(song, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    if tracks:
        d = copy.deepcopy(d)
        d['tracks'] = {k: v for k, v in d['tracks'].items() if k in tracks}
        missing = set(tracks) - set(d['tracks'])
        if missing:
            raise ValueError(f"no track {', '.join(sorted(missing))} in {song}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            # the deck reads a song folder: a copy of the project file next to the song's own voices and sounds
            sub = os.path.join(tmp, os.path.basename(song))
            os.makedirs(sub)
            for name in os.listdir(song):
                if name not in ('project.json', 'renders', 'cache', 'history', 'live'):
                    src = os.path.join(song, name)
                    try:
                        os.symlink(src, os.path.join(sub, name), target_is_directory=os.path.isdir(src))
                    except OSError:
                        (shutil.copytree if os.path.isdir(src) else shutil.copy)(src, os.path.join(sub, name))
            with open(os.path.join(sub, 'project.json'), 'w', encoding='utf8') as f:
                json.dump(d, f)
            return _run(sub, song, d, bars)
    return _run(song, song, d, bars)


def _run(deck_song, root, d, bars):
    st = _studio(d, root, bars, None)
    n = st['mix'].shape[1]
    lv, head, spent = _live(deck_song, d, bars, n)
    # a window's first bar is its edge: the studio rings in what was played before it, a deck starts clean (only
    # notes still held come in). Judged from the second bar on; the edge is reported as a line of its own.
    edge = int(round(d.get('beats_per_bar', 4) * 60.0 / float(d['bpm']) * SR)) if bars[0] > 1 and bars[1] > bars[0] else 0
    keys = ['mix'] + sorted(k for k in st if k != 'mix')
    res, takes = {}, {}
    for k in keys:
        s = st[k]
        v = lv.get(k, np.zeros_like(s))
        res[k] = compare(s[:, edge:], v[:, edge:])
        if k in spent and spent[k]['render_s'] > 0:
            # x realtime: seconds of audio its renders gave per second they took, in this process (one worker's pace)
            res[k]['speed_x'] = spent[k]['audio_s'] / spent[k]['render_s']
        takes[k] = (s, v)
    if edge:
        res[f'mix, bar {bars[0]} (edge)'] = compare(takes['mix'][0][:, :edge], takes['mix'][1][:, :edge])
    return res, takes, head


def report(res, head=''):
    L = []
    for k, m in res.items():
        if m.get('silent'):
            L.append(f"  {k:<22} silent in both")
            continue
        bands = ' '.join('  . ' if b is None else f"{b:+4.1f}" for b in m['bands'])
        speed = f"  renders {m['speed_x']:.1f}x realtime" if 'speed_x' in m else ''
        L.append(f"  {k:<22} {m['studio_db']:6.1f} dB rms  live {m['level_db']:+5.2f} dB  corr {m['corr']:.3f}  residual {m['resid_db']:+6.1f} dB  "
                 f"bands {bands}  {verdict(m)}{speed}")
    notes = [ln.strip() for ln in head.splitlines() if 'not live' in ln]
    return '\n'.join(L + [f"  deck: {x}" for x in notes])

