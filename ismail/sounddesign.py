"""Sound design against a target: fast one-shot rendering, sound-to-sound distance, parameter fitting."""
import copy

import numpy as np

from . import dsp, fx as fxmod, instruments

SR = 44100
THIRD_OCT = 1000 * 2 ** (np.arange(-17, 14) / 3)  # 20 Hz .. 20 kHz


class _Ctx:
    """Minimal fx context for one-shots (no automation, no sidechain sources)."""
    def __init__(self, bpm, sr=SR):
        self.sr = sr
        self.bpm = bpm
        self.offset_samples = 0

    def param(self, idx, name, default):
        return default

    def track_audio(self, name):
        raise fxmod.FxError("sidechain/vocoder sources are not available when rendering a single sound")

    track_onsets = track_audio
    modulator_audio = track_audio

    def note_gr(self, *a):
        pass


def render_oneshot(inst, notes_sec, length_s, fx=None, bpm=120.0, sr=SR, root=None):
    """notes_sec: [(start_s, midi, dur_s, vel)] -> stereo (2, n). root: project dir (for song voices)."""
    n = int(length_s * sr)
    y = instruments.render_instrument(instruments.normalize(inst), notes_sec, n, None, bpm, sr, root)
    ctx = _Ctx(bpm, sr)
    for i, f in enumerate(fx or []):
        y = fxmod.apply_fx(y, fxmod.normalize(f), ctx, i)
    return y


# ------------------------------------------------------------------ descriptors

def descriptor(y, sr=SR, env_ms=400, env_step_ms=10):
    """y mono or stereo. Returns dict: bands (1/3-oct dB rel. max), env (dB per step rel. peak, from onset),
    centroid, width."""
    stereo = y if y.ndim == 2 else np.stack([y, y])
    m = stereo.mean(0)
    # onset = first sample within 30 dB of the peak
    a = np.abs(m)
    pk = a.max() + 1e-12
    on = int(np.argmax(a > pk * 0.03))
    x = m[on:]
    nfft = 1 << int(np.ceil(np.log2(max(len(x), 4096))))
    P = np.abs(np.fft.rfft(x * np.hanning(len(x)), n=nfft)) ** 2
    f = np.fft.rfftfreq(nfft, 1 / sr)
    bands = []
    for c in THIRD_OCT:
        msk = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))
        bands.append(10 * np.log10(P[msk].sum() + 1e-15) if msk.any() else -150.0)
    bands = np.array(bands)
    bands = np.maximum(bands - bands.max(), -60)
    st = int(env_step_ms / 1000 * sr)
    ne = int(env_ms / env_step_ms)
    env = []
    for i in range(ne):
        seg = x[i * st:(i + 1) * st]
        env.append(10 * np.log10(np.mean(seg ** 2) + 1e-15) if len(seg) else -150.0)
    env = np.array(env)
    env = np.maximum(env - env.max(), -60)
    cent = float((f * P).sum() / (P.sum() + 1e-12))
    s = stereo[:, on:]
    side = (s[0] - s[1]) / 2
    mid = (s[0] + s[1]) / 2
    width = float(np.sqrt(np.mean(side ** 2) / (np.mean(mid ** 2) + 1e-12)))
    # pitch clarity: how far spectral peaks stand above the band median (dB), per 1/3 octave from 100 Hz to 5 kHz.
    # Detune smear, noise and heavy distortion lower it.
    peak = []
    for c in THIRD_OCT:
        msk = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))
        if msk.sum() >= 8:
            v = P[msk]
            peak.append(10 * np.log10(v.max() / (np.median(v) + 1e-15) + 1e-15))
        else:
            peak.append(0.0)
    return {'bands': bands, 'env': env, 'centroid': cent, 'width': width, 'peak': np.array(peak)}


FAR_DB = 20.0       # a band this far off is a different sound there, not a shade of the same one (ledger:M315)
# how instrument_fit scores (ledger:M315). distance() defaults to the score sound_compare has always given, so song
# sessions that track sound_compare numbers across rounds keep comparable numbers; a fit opts in with **FIT_SCORE.
FIT_SCORE = {'far_cost': 0.15, 'nominal_bands': True}


def band_mask(fmin=25.0, fmax=16000.0, nominal=True):
    """The 1/3-octave bands inside [fmin, fmax]. nominal=True picks them by their nominal names: the band the tables
    print as '25H' sits at 24.8 Hz and counts for fmin=25 (it fell out of the distance while the table showed it,
    ledger:M315). nominal=False is the old pick by centre frequency, which leaves it out."""
    if not nominal:
        return (THIRD_OCT >= fmin) & (THIRD_OCT <= fmax)
    q = 2 ** (1 / 12)
    return (THIRD_OCT >= fmin / q) & (THIRD_OCT <= fmax * q)


def far_bands(da, db, fmin=25.0, fmax=16000.0, far_db=FAR_DB):
    """[(centre Hz, A-B dB)] for the bands inside [fmin, fmax] (nominal names) where A and B differ by more than
    far_db (either one above the floor)."""
    msk = band_mask(fmin, fmax) & ((da['bands'] > -50) | (db['bands'] > -50))
    diff = da['bands'] - db['bands']
    return [(float(c), float(x)) for c, x, m in zip(THIRD_OCT, diff, msk) if m and abs(x) > far_db]


def far_warning(da, db, fmin=25.0, fmax=16000.0, name_a='A', name_b='B', far_db=FAR_DB):
    """One line naming each band more than far_db apart, or '' (ledger:M315: a fit kept an octave-down square 35 to
    39 dB over its target at 25 to 31 Hz). Text only: it changes no distance."""
    far = far_bands(da, db, fmin, fmax, far_db)
    if not far:
        return ''
    shown = ', '.join(f"{c:.0f} Hz {x:+.0f} dB" for c, x in far)
    return (f"WARNING: {len(far)} band(s) differ by more than {far_db:.0f} dB ({name_a} - {name_b}): {shown}. A "
            f"difference that big is a part the other does not have (an extra sub octave, a missing top); check the "
            f"instrument there before trusting the distance")


def distance(da, db, w_bands=1.0, w_env=0.5, w_width=5.0, fmin=25.0, fmax=16000.0, w_peak=0.4, far_db=FAR_DB,
             far_cost=0.0, nominal_bands=False):
    """Distance between two descriptors: mean abs band dB, envelope, width and pitch clarity. far_cost: added for
    each dB a band is past far_db off, summed, not averaged (two sub bands 37 dB over then cost as much as ten bands
    3 dB off, where the mean alone spread them over the 29 bands and a fit kept them, ledger:M315). nominal_bands:
    pick the bands by nominal name (band_mask). The defaults (0.0, False) give the score sound_compare has always
    given; instrument_fit passes FIT_SCORE."""
    msk = band_mask(fmin, fmax, nominal_bands)
    # ignore bands where both are near the floor
    live = msk & ((da['bands'] > -50) | (db['bands'] > -50))
    band = float(np.mean(np.abs(da['bands'][live] - db['bands'][live]))) if live.any() else 0.0
    off = np.abs(da['bands'][live] - db['bands'][live])
    excess = float(np.sum(np.maximum(off - far_db, 0.0))) if live.any() else 0.0
    n = min(len(da['env']), len(db['env']))
    live_e = (da['env'][:n] > -50) | (db['env'][:n] > -50)
    env = float(np.mean(np.abs(da['env'][:n][live_e] - db['env'][:n][live_e]))) if live_e.any() else 0.0
    pm = msk & (THIRD_OCT >= 100) & (THIRD_OCT <= 5000) & (da['bands'] > -40) & (db['bands'] > -40)
    peak = float(np.mean(np.abs(da['peak'][pm] - db['peak'][pm]))) if pm.any() else 0.0
    d = w_bands * band + w_env * env + w_width * abs(da['width'] - db['width']) + w_peak * peak
    if far_cost:
        d += far_cost * excess
    return d, {'band_db': band, 'env_db': env, 'width': da['width'] - db['width'], 'peak_db': peak,
               'far_excess_db': excess, 'far_cost': far_cost,
               'peak_a': float(np.mean(da['peak'][pm])) if pm.any() else 0.0,
               'peak_b': float(np.mean(db['peak'][pm])) if pm.any() else 0.0}


def compare_text(da, db, name_a='A', name_b='B', fmin=25.0, fmax=16000.0, warn=True, **score):
    """(distance, text). score: distance's far_cost / nominal_bands (instrument_fit passes FIT_SCORE). warn: add the
    far-band warning line (text only)."""
    d, parts = distance(da, db, fmin=fmin, fmax=fmax, **score)
    far_note = (f"; {parts['far_excess_db']:.0f} dB past {FAR_DB:.0f} dB off in the far bands, "
                f"{parts['far_cost']:g} per dB" if parts['far_cost'] and parts['far_excess_db'] > 0 else '')
    w = far_warning(da, db, fmin, fmax, name_a, name_b) if warn else ''
    L = [f"distance {d:.2f} (bands {parts['band_db']:.1f} dB mean abs, envelope {parts['env_db']:.1f} dB mean abs, "
         f"width {da['width']:.2f} vs {db['width']:.2f}, pitch clarity {parts['peak_a']:.1f} vs {parts['peak_b']:.1f} dB"
         f" peak-over-median: lower = smeared by detune/noise/distortion{far_note})"] + ([w] if w else []) + [
         f"{'band':>8} {name_a[:8]:>8} {name_b[:8]:>8} {'A-B':>6}"]
    for c, a, b in zip(THIRD_OCT, da['bands'], db['bands']):
        if c > 20000 or (a <= -59 and b <= -59):
            continue
        flag = '  <<' if a - b < -6 else '  >>' if a - b > 6 else ''
        L.append(f"{c:>7.0f}H {a:8.1f} {b:8.1f} {a - b:+6.1f}{flag}")
    L.append("envelope (dB vs peak, 10 ms steps from onset; digit = 9 + dB/6):")
    dig = lambda e: ''.join('.' if v <= -54 else str(int(np.clip(9 + v / 6, 0, 9))) for v in e)  # noqa: E731
    L.append(f"  {name_a[:6]:>6} {dig(da['env'])}")
    L.append(f"  {name_b[:6]:>6} {dig(db['env'])}")
    L.append(f"centroid {da['centroid']:.0f} vs {db['centroid']:.0f} Hz. (<< = A too quiet in that band, >> = too loud)")
    return d, '\n'.join(L)


# ------------------------------------------------------------------ fitting

def _get(d, path):
    for k in path.split('.'):
        d = d[int(k)] if isinstance(d, list) else d[k]
    return d


def _set(d, path, v):
    ks = path.split('.')
    for k in ks[:-1]:
        d = d[int(k)] if isinstance(d, list) else d[k]
    last = ks[-1]
    if isinstance(d, list):
        d[int(last)] = v
    else:
        d[last] = v


def fit(inst, params, target_desc, notes_sec, length_s, fx=None, bpm=120.0, iters=80, seed=0, log_keys=None,
        progress=None, fmin=25.0, fmax=16000.0, root=None):
    """(1+lambda) evolution strategy over params {path: [lo, hi]} minimising distance to target_desc.
    Paths address the instrument ('filter.cutoff') or the fx chain ('fx.0.depth_db'). Parameters whose range spans
    > 8x (and lo > 0) are searched in log space. Returns (best_state {'inst', 'fx'}, best_d, start_d, values)."""
    rng = np.random.default_rng(seed)
    full = {'inst': instruments.normalize(copy.deepcopy(inst)),
            'fx': [fxmod.normalize(f) for f in (fx or [])]}
    keys = list(params)
    paths = [k if k.startswith('fx.') else 'inst.' + k for k in keys]
    lo = np.array([params[k][0] for k in keys], float)
    hi = np.array([params[k][1] for k in keys], float)
    logk = np.array([(lo[i] > 0 and hi[i] / lo[i] > 8) or (log_keys and keys[i] in log_keys) for i in range(len(keys))])

    def to_u(v):
        return np.where(logk, (np.log(np.maximum(v, 1e-12)) - np.log(np.maximum(lo, 1e-12))) /
                        (np.log(hi) - np.log(np.maximum(lo, 1e-12)) + 1e-12), (v - lo) / (hi - lo + 1e-12))

    def from_u(u):
        u = np.clip(u, 0, 1)
        return np.where(logk, np.exp(np.log(np.maximum(lo, 1e-12)) + u * (np.log(hi) - np.log(np.maximum(lo, 1e-12)))),
                        lo + u * (hi - lo))

    def evaluate(v):
        cand = copy.deepcopy(full)
        for k, x in zip(paths, v):
            orig = _get(full, k)
            _set(cand, k, int(round(x)) if isinstance(orig, (bool, int)) and not isinstance(orig, bool) else float(x))
        try:
            y = render_oneshot(cand['inst'], notes_sec, length_s, cand['fx'], bpm, root=root)
        except Exception:
            return 1e9, cand
        if np.max(np.abs(y)) < 1e-6:
            return 1e9, cand
        return distance(descriptor(y), target_desc, fmin=fmin, fmax=fmax, **FIT_SCORE)[0], cand

    start_v = np.array([float(_get(full, k)) for k in paths])
    start_v = np.clip(start_v, lo, hi)
    best_d, best_inst = evaluate(start_v)
    best_u = to_u(start_v)
    hist = [best_d]
    d0 = best_d
    # global random probes, then local ES with shrinking step
    n_global = max(8, iters // 4)
    for _ in range(n_global):
        u = rng.random(len(keys))
        d, cand = evaluate(from_u(u))
        if d < best_d:
            best_d, best_inst, best_u = d, cand, u
        hist.append(best_d)
    sigma = 0.2
    for it in range(iters - n_global):
        u = best_u + rng.normal(0, sigma, len(keys))
        d, cand = evaluate(from_u(u))
        if d < best_d:
            best_d, best_inst, best_u = d, cand, np.clip(u, 0, 1)
            sigma = min(sigma * 1.3, 0.3)
        else:
            sigma = max(sigma * 0.93, 0.02)
        hist.append(best_d)
        if progress:
            progress(it, best_d)
    return best_inst, best_d, d0, {k: float(v) for k, v in zip(keys, from_u(best_u))}
