"""Electric guitar or bass as a performer: plucked waveguide strings, played a whole part at a time.

A picked note starts a new string vibration; a note that starts before the previous one ends (overlap) on the same
string is legato: hammer-on/pull-off (a quick pitch step with a small tap), or a fretted slide when the `slide`
lane is up. Expression lanes (automation 'inst.lane.<name>'):
  bend   semitones added to every sounding string (a string bend on a mono lead part, the whammy bar on chords)
  vib    vibrato depth in cents (finger vibrato; wanders in rate and depth like a player's)
  mute   0..1 palm / fret-hand mute (short, dark)
  slide  0..1: legato steps become fretted slides instead of hammer-ons
The output is the pickup signal (DI): put the rig after it (fuzz, univibe, amp, cab ...).
"""
import numpy as np
from numba import njit

# The rigs fitted in the Pali Gap study (a late-60s Hendrix-style record, 2026-09-30): the voice is only the DI,
# so each preset is heard through one of these chains. Put the list in the track's fx (fx_add per item).
RIGS = {
    "lead": {"preset": "strat70_lead",
             "note": "wah first, fuzz, a loud amp with its noise floor: 20 of 22 single notes passed a blind exam",
             "fx": [{"type": "wah", "pos": 0.4, "mix": 0.7, "q": 6.0},
                    {"type": "fuzz", "fuzz": 0.35, "input_db": -6.0, "bias": 0.55, "tone_hz": 5000.0},
                    {"type": "amp", "gain": 3.5, "volume": 7.0, "bass": 5, "mid": 7, "treble": 5, "presence": 4,
                     "sag": 0.6, "hiss_db": -74.0, "hum_db": -84.0},
                    {"type": "cab", "mic": 0.15, "low_hz": 120, "high_hz": 4800, "presence_db": 4.0, "seed": 9},
                    {"type": "delay", "time_ms": 110.0, "feedback": 0.15, "mix": 0.12, "lp_hz": 3500.0},
                    {"type": "eq", "bands": [{"type": "peak", "freq": 700, "gain_db": 2.6, "q": 1.4},
                                             {"type": "peak", "freq": 1000, "gain_db": -1.2, "q": 1.4},
                                             {"type": "peak", "freq": 1400, "gain_db": 1.0, "q": 1.4},
                                             {"type": "peak", "freq": 2800, "gain_db": 2.0, "q": 1.2},
                                             {"type": "highshelf", "freq": 4000, "gain_db": 1.5}]}]},
    "rhythm_vibe": {"preset": "strat70_rhythm",
                    "note": "tone knob rolled back, univibe into a clean amp with everything on 10, close mic",
                    "fx": [{"type": "univibe", "rate_hz": 3.0, "intensity": 0.75, "mode": "chorus", "drive": 0.3},
                           {"type": "amp", "gain": 2.5, "volume": 4.5, "bass": 10, "mid": 9.8, "treble": 10,
                            "presence": 0, "bright": 0.6, "sag": 0.4},
                           {"type": "cab", "mic": 0.0, "high_hz": 4230, "presence_db": 6.7, "seed": 3},
                           {"type": "eq", "bands": [{"type": "peak", "freq": 1400, "gain_db": 4.0, "q": 1.0},
                                                    {"type": "peak", "freq": 460, "gain_db": -3.0, "q": 1.2}]}]},
    "rhythm_rotary": {"preset": "strat70_rotary",
                      "note": "middle pickup through a driven amp and a slow rotary speaker",
                      "fx": [{"type": "amp", "gain": 4.5, "volume": 6.0, "bass": 5, "mid": 6, "treble": 6,
                              "presence": 5, "sag": 0.5},
                             {"type": "cab", "mic": 0.2, "high_hz": 4800, "presence_db": 3.0, "seed": 5},
                             {"type": "rotary", "speed": 0.0, "mix": 1.0, "drive": 0.2}]},
    "bass": {"preset": "pbass70",
             "note": "fitted on the harmonic profile of the record's bass: a dark amp, a 550 Hz cab, compression",
             "fx": [{"type": "amp", "gain": 2.0, "volume": 4.5, "bass": 10, "mid": 0, "treble": 10, "presence": 0,
                     "bright": 0.0, "sag": 0.3},
                    {"type": "cab", "mic": 0.2, "low_hz": 82, "high_hz": 550, "presence_db": 0.0, "breakup": 0.5,
                     "seed": 12},
                    {"type": "compressor", "threshold_db": -14, "ratio": 3, "attack_ms": 15, "release_ms": 150}]},
}

INFO = {
    "measured": "strings, pickups and rigs fitted to a late-60s record's guitar and bass stems; strat70 lead notes passed the user's blind exam 20 of 22",
    "summary": "electric guitar / bass performer: waveguide strings with pick, pickup comb and LC resonance; "
               "legato, slides, bends, whammy, vibrato and mutes as lanes",
    "range": "guitar Eb2-Eb6 (half-step-down tuning by default), bass Eb1-G3",
    "velocity": "pick strength: louder, brighter, sharper attack pitch transient",
    "lanes": {"bend": "semitones on all sounding strings", "vib": "vibrato depth, cents", "mute": "0..1 palm mute",
              "slide": "0..1 legato as fretted slides", "level": "dB on the whole part (finger pressure, swells, dips)"},
    "rigs": RIGS,
    "params": {
        "kind": "'strat' or 'pbass' (sets the defaults below)",
        "mode": "'mono' (a lead line: one note at a time, overlaps are legato) or 'poly' (chords on 6 strings)",
        "tuning": "open strings, MIDI numbers low to high",
        "pickup": "pickup position(s) as fractions of the string from the bridge, list; mixed equally",
        "pluck": "pick position, fraction from the bridge", "bright": "pick brightness 0-1",
        "t60": "sustain of a mid string at the fundamental, s", "damp": "extra loop low-pass 0-1 (0 = only t60_hi)",
        "t60_hi": "sustain of partials near hf_hz, s (strings: tops die in a fraction of a second)",
        "hf_hz": "frequency where t60_hi applies", "pol2": "level of the second string polarization (0-0.6)",
        "pol2_cents": "its detune in cents (beating, a string that moves)", "click": "pick click level",
        "drift_cents": "slow random pitch wander of the fretting finger, cents rms",
        "air": "string/pick noise riding on the note (follows its level), 0-0.2", "air_hz": "its high-pass, Hz",
        "scrape": "pick scrape just before the attack, 0-1", "pick_ms": "pick contact time at full velocity (soft = longer)",
        "pick_var": "note-to-note variation of the pick (click, scrape, contact time, position), 0-1",
        "rattle": "fret/string rattle on hard picks (a short buzz while the string is loud), 0-1",
        "bright_track": "brightness vs pitch: pick brightness x (f0/350 Hz)^bright_track (low notes darker)",
        "res_hz": "pickup + cable resonance", "res_q": "its peak", "release": "string stop time after note end, s",
        "velocity": "0..1: a magnetic pickup senses string VELOCITY (+6 dB/oct over displacement); 1 = physical",
        "vel_ref_hz": "frequency where the velocity pickup's level equals the displacement signal's",
        "strum_ms": "chord spread (poly)", "vib_rate": "vibrato rate Hz", "attack_cents": "pitch overshoot of a hard pick",
        "humanize_ms": "timing jitter", "level": "output gain", "seed": "random seed",
    },
    "tuning": "the instrument's tuning table, strings included: poly mode puts each note on a string (string 1 = the "
              "highest, fret = note - open string); mono mode reads the string the same rule picks",
}

KINDS = {
    "strat": dict(tuning=[39, 44, 49, 54, 58, 63], pickup=[0.25], pluck=0.16, bright=0.6, t60=6.0, damp=0.0,
                  t60_hi=0.45, hf_hz=4000.0, pol2=0.35, pol2_cents=1.2, click=0.12, drift_cents=3.0, air=0.05, air_hz=2500.0, scrape=0.3,
                  pick_ms=1.2, pick_var=0.5, rattle=0.3, bright_track=0.25,
                  res_hz=3600.0, res_q=1.8, release=0.25, strum_ms=14.0, vib_rate=5.6, attack_cents=6.0,
                  max_fret=22, level=1.0, velocity=0.0, vel_ref_hz=400.0),
    "pbass": dict(tuning=[27, 32, 37, 42], pickup=[0.19], pluck=0.22, bright=0.3, t60=4.0, damp=0.0,
                  t60_hi=0.25, hf_hz=2000.0, pol2=0.25, pol2_cents=0.8, click=0.1, drift_cents=1.0, air=0.01, air_hz=1500.0, scrape=0.0,
                  pick_ms=2.5, pick_var=0.3, rattle=0.1, bright_track=0.0,
                  res_hz=2200.0, res_q=1.2, release=0.12, strum_ms=0.0, vib_rate=5.0, attack_cents=4.0,
                  max_fret=20, level=1.0, velocity=1.0, vel_ref_hz=150.0),
}


@njit(cache=False)
def _string(n, semis, ref_hz, exc, loopgain, lpcoef, pickups, npk, sr):
    """One string vibration. semis: pitch in semitones re ref_hz per sample; exc: input per sample;
    loopgain/lpcoef: per-sample loop loss and low-pass. Output: sum of pickup combs."""
    size = int(sr / 18.0) + 8
    buf = np.zeros(size)
    w = 0
    lp = 0.0
    out = np.zeros(n)
    for i in range(n):
        f = ref_hz * 2.0 ** (semis[i] / 12.0)
        L = sr / f
        # the loop's total delay is one period: the read delay plus the low-pass's group delay (a/(1-a))
        gd = lpcoef[i] / (1.0 - lpcoef[i] + 1e-9)
        D = L - gd
        if D < 3.0:
            D = 3.0
        # 3rd-order Lagrange read at D samples back
        p = w - D
        while p < 0:
            p += size
        j = int(p)
        fr = p - j
        x0 = buf[(j - 1) % size]
        x1 = buf[j % size]
        x2 = buf[(j + 1) % size]
        x3 = buf[(j + 2) % size]
        # reading "back" means older samples are at smaller p; Lagrange on x0..x3 around fr
        d = 1.0 + fr
        y = (-(d - 1) * (d - 2) * (d - 3) / 6.0 * x0 + d * (d - 2) * (d - 3) / 2.0 * x1
             - d * (d - 1) * (d - 3) / 2.0 * x2 + d * (d - 1) * (d - 2) / 6.0 * x3)
        lp = lp + (1.0 - lpcoef[i]) * (y - lp)
        v = lp * loopgain[i] + exc[i]
        buf[w] = v
        # pickups: difference of the wave and its copy p*L samples older (the spatial comb)
        acc = 0.0
        for k in range(npk):
            q = w - pickups[k] * L
            while q < 0:
                q += size
            jq = int(q)
            fq = q - jq
            a = buf[jq % size]
            b = buf[(jq + 1) % size]
            acc += v - (a + (b - a) * fq)
        out[i] = acc / npk
        w += 1
        if w >= size:
            w = 0
    return out


def _resonance(x, f0, q, sr):
    from scipy import signal
    w0 = 2 * np.pi * f0 / sr
    alpha = np.sin(w0) / (2 * q)
    b = np.array([(1 - np.cos(w0)) / 2, 1 - np.cos(w0), (1 - np.cos(w0)) / 2])
    a = np.array([1 + alpha, -2 * np.cos(w0), 1 - alpha])
    return signal.lfilter(b / a[0], a / a[0], x)


def _vibrato(n, sr, rate, rng):
    # a player's vibrato: rate and depth wander over about a second; one-sided (a bend-and-release) like a
    # guitarist's, so it pushes the pitch up from the note rather than around it
    t = np.arange(n) / sr
    k = max(2, int(n / sr * 1.5) + 2)
    wr = np.interp(t, np.linspace(0, t[-1] + 1e-9, k), rng.normal(0, 0.12, k))
    ph = np.cumsum(rate * (1 + wr)) / sr
    wd = np.interp(t, np.linspace(0, t[-1] + 1e-9, k), rng.uniform(0.7, 1.2, k))
    return (0.5 - 0.5 * np.cos(2 * np.pi * ph)) * wd


def _pick(L, vel, bright, pluck, rng, sr, strength=1.0, click_lvl=0.3, pick_ms=0.6):
    """Excitation for one period: a pick-shaped displacement (triangle toward the pluck point) through a
    velocity-dependent low-pass, plus a short pick click."""
    m = max(8, int(L))
    x = np.linspace(0, 1, m)
    pk = max(0.02, min(0.98, pluck))
    tri = np.where(x < pk, x / pk, (1 - x) / (1 - pk))
    tri -= tri.mean()
    cut = 900 + 9000 * (bright * 0.6 + 0.4 * vel) ** 2
    a = np.exp(-2 * np.pi * cut / sr)
    y = np.empty_like(tri)
    s = 0.0
    for i in range(len(tri)):
        s = a * s + (1 - a) * tri[i]
        y[i] = s
    nc = int(0.003 * sr)
    click = rng.standard_normal(nc) * np.hanning(nc) * 0.15 * bright * click_lvl
    # the pick is a soft contact, not an impulse: smooth the displacement's corner
    wlen = max(3, int(pick_ms / 1000 * (1.8 - 0.8 * vel) * sr))
    y = np.convolve(y, np.hanning(wlen) / np.hanning(wlen).sum(), 'same')
    e = np.zeros(max(len(y), len(click)))
    e[:len(y)] += y
    e[:len(click)] += click
    return e * vel * strength


def _rng(seed, beat0, t, bpm, salt, extra=0):
    """A generator for one note: keyed on the seed, where the note sits in the song (beat0 = the song beat at
    sample 0, t = seconds from there) and a salt, never on how many notes came before it in this render."""
    tick = int(round((beat0 + t * bpm / 60.0) * 960)) + (1 << 40)
    return np.random.default_rng([int(seed) & 0xFFFFFFFF, tick, int(salt) & 0xFFFF, int(extra) & 0xFFFF])


def _string_for(m, tuning, max_fret, used=()):
    """The string (index into tuning, 0 = the lowest) a note goes on: the highest free string that plays it within
    12 frets, else any free string that reaches it."""
    cands = [s for s in range(len(tuning)) if tuning[s] <= m <= tuning[s] + max_fret and s not in used]
    if not cands:
        cands = [s for s in range(len(tuning)) if s not in used] or [0]
    s = max(cands) if m - tuning[max(cands)] >= 0 else min(cands)
    # prefer lower frets: the highest string that can play it, unless the fret is very high
    for c in sorted(cands, reverse=True):
        if 0 <= m - tuning[c] <= 12:
            s = c
            break
    return s


def perform(notes, total_n, sr, bpm=120.0, lanes=None, kind='strat', mode='poly', seed=1, humanize_ms=4.0,
            beat0=0.0, cents_of=None, **params):
    """beat0: the song beat at sample 0. Each note's randomness is keyed on where it sits in the song, so a slice
    of the part (the live engine renders bar by bar) plays exactly as it does in the whole part.
    cents_of(midi, string, fret): the instrument's tuning table (string 1 = the highest), added to each note."""
    P = dict(KINDS[kind])
    P.update({k: v for k, v in params.items() if v is not None})
    lanes = lanes or {}
    tuning = list(P['tuning'])
    out = np.zeros(total_n)

    def lane(name, a, b, default=0.0):
        c = lanes.get(name)
        if c is None:
            return np.full(b - a, default)
        c = np.asarray(c, dtype=np.float64)
        seg = c[a:b]
        if len(seg) < b - a:
            seg = np.concatenate([seg, np.full(b - a - len(seg), c[-1] if len(c) else default)])
        return seg

    # ---- assign notes to strings, group legato chains
    ns = sorted(notes, key=lambda z: (z[0], -z[1]))
    if mode == 'mono':
        chains = []
        for st, m, d, v in ns:
            if chains and st < chains[-1][-1][0] + chains[-1][-1][2] - 1e-3 and st > chains[-1][-1][0] + 1e-3:
                chains[-1].append((st, m, d, v))
            else:
                chains.append([(st, m, d, v)])
        string_of = [None] * len(chains)
    else:
        busy = {}          # string -> end time
        chains, string_of = [], []
        last_chain_on = {}
        groups = []
        for nt in ns:       # chord groups: starts within 30 ms
            if groups and nt[0] - groups[-1][0][0] < 0.03:
                groups[-1].append(nt)
            else:
                groups.append([nt])
        for g in groups:
            used = set()
            g = sorted(g, key=lambda z: -z[1])
            beat = g[0][0] * bpm / 60.0
            up = abs((beat * 2) % 2 - 1) < 0.25          # off-beat eighths strum up
            order = sorted(range(len(g)), key=lambda i: g[i][1], reverse=up)
            for rank, i in enumerate(order):
                st, m, d, v = g[i]
                s = _string_for(m, tuning, P['max_fret'], used)
                used.add(s)
                st2 = st + rank * P['strum_ms'] / 1000.0 * (0.8 + 0.4 * _rng(seed, beat0, st, bpm, m, 1).random())
                ci = last_chain_on.get(s)
                if ci is not None:
                    pst, pm, pd, pv = chains[ci][-1]
                    # legato on the same string: the old note still sounds and the move is a few frets
                    # (a single note, or every note of a double-stop moving together)
                    if pst + 1e-3 < st2 < pst + pd - 1e-3 and (len(g) == 1 or abs(m - pm) <= 5):
                        chains[ci].append((st2, m, d, v))
                        continue
                chains.append([(st2, m, d, v)])
                string_of.append(s)
                last_chain_on[s] = len(chains) - 1

    # the time each chain is cut: the next picked chain on its string (mono: the next chain at all)
    cut = []
    for k, ch in enumerate(chains):
        nxt = None
        for k2 in range(k + 1, len(chains)):
            if mode == 'mono' or string_of[k2] == string_of[k]:
                nxt = chains[k2][0][0]
                break
        cut.append(nxt)

    t60_0, damp = float(P['t60']), float(P['damp'])
    pickups = np.array(P['pickup'], dtype=np.float64)
    for k, ch in enumerate(chains):
        st0 = ch[0][0]
        rng = _rng(seed, beat0, st0, bpm, ch[0][1], 2)
        jitter = rng.normal(0, humanize_ms / 1000.0)
        st0 = max(0.0, st0 + jitter)
        end = ch[-1][0] + ch[-1][2] + jitter
        if cut[k] is not None:
            end = min(end, cut[k] + jitter)
        rel = float(P['release'])
        a = int(st0 * sr)
        b = min(total_n, int((end + rel * 8 + 0.02) * sr))
        if b - a < 16 or a >= total_n:
            continue
        n = b - a
        t = np.arange(n) / sr
        # pitch path: steps at legato notes (hammer ~6 ms glide) or fretted slides
        base = np.full(n, float(ch[0][1]))
        slide = lane('slide', a, b)
        for j in range(1, len(ch)):
            ts = ch[j][0] - ch[0][0]
            i0 = int(ts * sr)
            if i0 >= n:
                break
            prev, new = ch[j - 1][1], ch[j][1]
            if slide[min(i0, n - 1)] > 0.5:
                dur = min(0.12, max(0.03, abs(new - prev) * 0.018))
                i1 = min(n, i0 + int(dur * sr))
                path = prev + (new - prev) * np.linspace(0, 1, i1 - i0) ** 1.3
                fl = np.floor(path)
                frac = path - fl
                path = fl + np.clip((frac - 0.75) / 0.25, 0, 1)    # frets: steps, not a glide
                base[i0:i1] = path
                base[i1:] = new
            else:
                i1 = min(n, i0 + int(0.006 * sr))
                base[i0:i1] = np.linspace(prev, new, i1 - i0)
                base[i1:] = new
        v0 = ch[0][3] / 127.0
        # pick transient: a hard pick starts sharp (tension), settling in ~60 ms
        semis = base - 69.0 + (P['attack_cents'] * v0 ** 2 / 100.0) * np.exp(-t / 0.06)
        semis += lane('bend', a, b)
        if cents_of is not None:
            # the tuning table: each note of the chain on its string and fret, a step where the note starts
            si = string_of[k] if string_of[k] is not None else _string_for(ch[0][1], tuning, P['max_fret'])
            tc = np.zeros(n)
            for j, nt in enumerate(ch):
                fret = nt[1] - tuning[si]
                i0 = 0 if j == 0 else int((nt[0] - ch[0][0]) * sr)
                if i0 < n:
                    tc[i0:] = cents_of(nt[1], len(tuning) - si, fret if fret >= 0 else None)
            semis += tc / 100.0
        if P.get('drift_cents', 0) > 0:
            k = max(2, int(n / sr * 2.5) + 2)                   # a wander with ~0.4 s correlation
            w = np.interp(np.arange(n), np.linspace(0, n - 1, k), rng.normal(0, 1, k))
            semis += float(P['drift_cents']) / 100.0 * w
        vd = lane('vib', a, b)
        if np.any(vd > 0):
            semis += vd / 100.0 * _vibrato(n, sr, P['vib_rate'], rng)
        # loop loss designed from two decay times: T60 at the fundamental and at hf_hz (a one-pole loss
        # filter g(1-a)/(1-a z^-1) meets both); mutes and releases shorten both
        f_ref = 440.0 * 2 ** ((base - 69) / 12)
        T60 = t60_0 * (196.0 / np.maximum(f_ref, 30)) ** 0.35
        T60h = np.full(n, float(P['t60_hi']))
        mute = np.clip(lane('mute', a, b), 0, 1)
        T60 = T60 * (1 - 0.93 * mute)
        T60h = T60h * (1 - 0.8 * mute)
        end_i = int((end - st0) * sr)
        if end_i < n:
            T60[max(0, end_i):] = np.minimum(T60[max(0, end_i):], rel)
            T60h[max(0, end_i):] = np.minimum(T60h[max(0, end_i):], rel * 0.5)
        T60h = np.minimum(T60h, T60 * 0.95)
        per = 1.0 / f_ref
        loopgain = 10 ** (-3 * per / T60)
        r = 10 ** (-3 * per / T60h) / loopgain                 # wanted |H(w_h)| / |H(0)|
        wh = 2 * np.pi * min(float(P['hf_hz']), sr * 0.4) / sr
        c = np.cos(wh)
        A = 1 - r * r
        B = -2 * (1 - r * r * c)
        disc = np.maximum(B * B - 4 * A * A, 0)
        lpc = np.where(A > 1e-9, (-B - np.sqrt(disc)) / (2 * np.maximum(A, 1e-9)), 0.0)
        lpc = np.clip(lpc + damp * (1 - lpc) * 0.5, 0.0, 0.95)
        # excitation: the pick, plus small taps at hammer-ons / pull-offs
        exc = np.zeros(n)
        L0 = sr / (440.0 * 2 ** ((ch[0][1] - 69) / 12))
        pv = float(P.get('pick_var', 0.0))
        k_click, k_scr, k_ms = np.exp(rng.normal(0, 0.4 * pv, 3))
        pluck_n = float(np.clip(P['pluck'] + rng.normal(0, 0.03 * pv), 0.05, 0.4))
        bright_n = P['bright'] * (440.0 * 2 ** ((ch[0][1] - 69) / 12) / 350.0) ** float(P.get('bright_track', 0.0))
        e = _pick(L0, 0.35 + 0.65 * v0, float(np.clip(bright_n, 0.05, 1.0)) * (1 - 0.6 * mute[0]), pluck_n, rng, sr,
                  click_lvl=float(P['click']) * k_click, pick_ms=float(P.get('pick_ms', 0.6)) * k_ms)
        exc[:min(n, len(e))] += e[:n]
        for j in range(1, len(ch)):
            i0 = int((ch[j][0] - ch[0][0]) * sr)
            if i0 >= n:
                break
            if slide[min(i0, n - 1)] <= 0.5:
                Lj = sr / (440.0 * 2 ** ((ch[j][1] - 69) / 12))
                e = _pick(Lj, 0.35 + 0.65 * ch[j][3] / 127.0, 0.25, 0.5, rng, sr, strength=0.25)
                m2 = min(n - i0, len(e))
                exc[i0:i0 + m2] += e[:m2]
        y = _string(n, semis, 440.0, exc, loopgain, lpc, pickups, len(pickups), float(sr))
        if P['pol2'] > 0:
            # the string's other polarization: a hair sharper, decaying a little faster; the two beat
            det = float(P['pol2_cents']) * (0.7 + 0.6 * rng.random()) / 100.0
            lg2 = 10 ** (np.log10(loopgain) * 1.25)
            y = y + float(P['pol2']) * _string(n, semis + det, 440.0, exc, lg2, lpc, pickups, len(pickups), float(sr))
        if P.get('scrape', 0) > 0 and a > int(0.03 * sr):
            ns_ = int((0.008 + 0.012 * rng.random()) * sr)
            z = rng.standard_normal(ns_) * np.linspace(0, 1, ns_) ** 2
            from scipy.signal import butter, lfilter
            z = lfilter(*butter(2, [1800 / (sr / 2), 7000 / (sr / 2)], 'band'), z)
            out[a - ns_:a] += z * float(P['scrape']) * 0.01 * v0 * k_scr
        if P.get('rattle', 0) > 0 and v0 > 0.55:
            from scipy.signal import butter, lfilter
            nr = min(n, int((0.06 + 0.08 * rng.random()) * sr))
            buzz = lfilter(*butter(2, [1200 / (sr / 2), 5000 / (sr / 2)], 'band'), rng.standard_normal(nr))
            envr = np.abs(y[:nr]) / (np.abs(y[:nr]).max() + 1e-9)
            y[:nr] += buzz * envr * np.exp(-np.arange(nr) / (0.03 * sr)) * float(P['rattle']) * (v0 - 0.5) *                 np.abs(y[:nr]).max() * 0.6
        if P.get('air', 0) > 0:
            from scipy.signal import butter, lfilter
            # the noise lives and dies with the note's top (pick and string noise), not its fundamental
            hi = lfilter(*butter(6, float(P['air_hz']) / (sr / 2), 'high'), y)
            env = np.sqrt(lfilter([0.002], [1, -0.998], hi ** 2))
            z = lfilter(*butter(4, float(P['air_hz']) / (sr / 2), 'high'), rng.standard_normal(n))
            y = y + z * env * float(P['air']) * 12.0
        out[a:b] += y[:n]
    a = float(P.get('velocity', 0.0))
    if a > 0:
        vel = np.diff(out, prepend=0.0) * sr / (2 * np.pi * float(P.get('vel_ref_hz', 200.0)))
        out = (1 - a) * out + a * vel
    out = _resonance(out, P['res_hz'], P['res_q'], sr)
    lv = lanes.get('level')
    if lv is not None:
        lv = np.asarray(lv, dtype=np.float64)[:total_n]
        out[:len(lv)] *= 10 ** (lv / 20)
    return out * 0.6 * float(P['level'])
