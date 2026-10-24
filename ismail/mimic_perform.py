"""A phrase performer for sustained mimic profiles: {"type": "mimic", "profile": ..., "perform": true}.

Why: the mimic instrument plays every note on its own (its own attack, release and vibrato from its first moment),
so a slurred line comes out as separate notes cross-fading. Nate on a mimic flute, 2026-10-23: "the flute sounds a
little bit midi". Here one breath (or one bow stroke) carries a phrase: the tone is one continuous oscillator bank
whose pitch glides between slurred notes, with onsets, air noise, a delayed vibrato that grows on long notes,
swells and a taper at the phrase end. The timbre (each partial's level per note, the body curve, the air or bow
noise spectrum and the attack map) is read from the same mimic profile, so the colour stays the profile's.

The design is the bossa_gilberto_studio flute performer (songs/bossa_gilberto_studio/voices/flute_perf.py, ledger
M308), made general: any sustained (blown or bowed) profile, the profile's own measured vibrato by default, chords
split into lines, and the room, ring and sympathetic strings of mimic's own post stage.

How notes are read (per line; a chord splits into lines, each phrased on its own):
  overlap         SLUR: no new attack, the pitch moves under continuous air
  gap < breath_gap TONGUE: the air keeps going, the tone dips and speaks again with the measured chiff
  longer gap      a new BREATH: a new phrase, with its onset

Lanes (automation 'inst.lane.<name>', per-sample curves, as for every performer voice; the names are the ones
songs/kit_winds' tenor sax uses where they overlap):
  level   dB on the tone (swells, tapers); +1 dB also brightens by 0.15 dB per octave of partial number
  vib     vibrato depth, cents peak, read at each note's start (0 = straight); grows in after vib_delay
  scoop   semitones the note starts below its pitch and rises from (read at the note's start)
  tongue  0..1 hardness of a tongued or breath onset (0 = a pure breath attack)
  breath  dB on the air noise
  bend    semitones added to the pitch (smears, lip bends)
"""
import numpy as np

from . import mimic

INFO = {
    "summary": "phrase performer over any sustained mimic profile: slurs, tonguing, breath onsets and air, "
               "delayed vibrato that grows (pitch plus breath pulse), scoops, bends, swells and phrase-end tapers",
    "use": "legato winds and bowed strings (a slurred melody, a sustained line) that sound like separate notes "
           "note by note; decaying profiles (piano, harp, plucked) refuse it",
    "lanes": {"level": "dB on the tone", "vib": "cents peak, read at note start",
              "scoop": "semitones below, read at note start", "tongue": "0..1 onset hardness",
              "breath": "dB on the air noise", "bend": "semitones added to the pitch"},
    "params": {"bright": "dB/octave of partial number, added", "vel_bright": "dB/octave per velocity unit",
               "vib": "vibrato cents peak (null = the profile's: the median of its notes with vibrato, times "
                      "0.5 + 0.5 x the share of notes that have it)",
               "vib_rate": "Hz (null = the profile's measured median, else 5.5)",
               "vib_delay": "s into a note before vibrato starts", "vib_grow": "s for it to reach full depth",
               "vib_am_db": "breath pulse with the vibrato, dB at full depth",
               "slur_ms": "pitch glide of a slur", "scoop_ms": "time to rise out of a scoop",
               "attack_ms": "breath onset rise", "tongue": "default onset hardness 0..1",
               "release": "s, the air stop at a phrase end",
               "breath_gap": "s: a gap shorter than this is tongued, longer is a new breath",
               "noise": "dB on the measured air", "lead_ms": "air before the tone speaks at a breath onset",
               "drift_cents": "slow pitch wander", "breathe_db": "slow level wander",
               "taper_db": "dB the phrase end falls by (read after the room), over the last 35 % of the last note "
                           "or the phrase's last 0.3 s, whichever is longer",
               "end_sag_cents": "pitch sag at that taper", "chord_ms": "notes starting this close are a chord",
               "room": "room RT60 s (null = the profile's)", "room_mix": "dB", "width": "0..1",
               "seed": "seed"},
}

DEFAULTS = dict(bright=0.0, vel_bright=6.0, vib=None, vib_rate=None, vib_delay=0.45, vib_grow=0.6, vib_am_db=1.6,
                slur_ms=45.0, scoop_ms=110.0, attack_ms=75.0, tongue=0.35, release=0.16, breath_gap=0.16,
                noise=0.0, lead_ms=28.0, drift_cents=3.0, breathe_db=0.7, taper_db=5.0, end_sag_cents=14.0,
                chord_ms=30.0, room=None, room_mix=-10.0, width=0.35, seed=5)

# mimic params that mean the same with perform on (the rest of mimic.DEFAULT_PARAMS shape a single note)
SHARED = ('bright', 'vel_bright', 'vib', 'vib_rate', 'vib_delay', 'width', 'noise', 'room', 'room_mix', 'seed')
POST = ('ring', 'ring_q', 'strings', 'sympathy', 'string_t60', 'mono')   # mimic's post stage, used as is
MIMIC_OK = SHARED + POST

CR = 64
MAX_PARTIALS = 200


class PerformError(ValueError):
    pass


def check_profile(prof, name=None):
    if prof.get('kind') != 'sustained':
        raise PerformError(
            f"mimic perform=true phrases sustained (blown or bowed) profiles; {name or prof.get('name')!r} is "
            f"{prof.get('kind')!r} (struck or plucked, like piano or harp), which rings on its own and has no "
            "breath or bow to carry a phrase. Drop 'perform', or play the line with a performer voice "
            "(voices_list shows them)")


def settings(prof, mimic_params=None, perform=None):
    """Performer params: DEFAULTS < the profile's own defaults (shared names) < mimic 'params' < 'perform' dict.
    Returns (P, post) with post the mimic params for mimic's post stage (room, ring, strings, mono)."""
    P = dict(DEFAULTS)
    post = dict(mimic.DEFAULT_PARAMS)
    for src in (prof.get('defaults') or {}, mimic_params or {}):
        post.update({k: v for k, v in src.items() if k in post})
        P.update({k: v for k, v in src.items() if k in SHARED and v is not None})
    if isinstance(perform, dict):
        P.update(perform)
    return P, post


def _smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _rng(seed, ms, salt):
    return np.random.default_rng([int(seed) & 0xFFFFFFFF, int(ms) + (1 << 40), int(salt) & 0xFFFF])


def _wander(rng, n, rate_hz, sr_c):
    x = rng.standard_normal(n + 64)
    k = max(1, int(sr_c / max(rate_hz, 0.1)))
    h = np.hanning(2 * k + 1)
    x = np.convolve(x, h, 'same')[32:32 + n]
    # unit variance by the filter's own gain, not by this stretch's std: a phrase shorter than the window holds a
    # nearly flat curve, and dividing that by its tiny std blew a +-0.7 dB wander up to -12 dB on whole phrases
    # (bossa_gilberto_studio v2 render 4)
    return x / np.sqrt(np.sum(h ** 2))


def _source_db(prof, midi, vel, kk, P):
    """mimic's static source level per partial number (without the body), blended between the two nearest notes"""
    a, b, w = mimic._neighbours(prof['notes'], midi)
    ra, rb = np.asarray(a['resid']), np.asarray(b['resid'])
    det = np.interp(kk, np.arange(1, len(ra) + 1), ra) * (1 - w) + np.interp(kk, np.arange(1, len(rb) + 1), rb) * w
    det = np.where(kk <= min(len(ra), len(rb)), det, 0.0)
    slope = a['slope'] * (1 - w) + b['slope'] * w
    gain = a['gain'] * (1 - w) + b['gain'] * w
    vref = a['vel'] * (1 - w) + b['vel'] * w
    lk = np.log2(kk)
    return (slope * lk + gain + det + (P['bright'] + P['vel_bright'] * (vel - vref)) * lk
            + 20 * np.log10(max(vel, 1e-3) / max(vref, 1e-3)) + prof.get('norm_db', 0.0)), (a, b, w)


VIB_FLOOR_C = 2.0     # mimic's analysis writes 0 for a note whose vibrato is 2 c or less: that note has none


def _measured_vibrato(prof):
    """The profile's typical vibrato (cents peak, Hz): the median depth of the notes that have one, times
    (0.5 + 0.5 x the share of notes that have one); the median rate of those notes.

    The median over all notes gave 0 c whenever most samples were played straight: the clarinet's has vibrato on 4
    of its 11 notes (5.0-6.3 c), so a performed line had none and the bloom never showed (the Sound Engineer's
    #346 ear round build). The player's own depth, leaned down for a mostly straight player but never below half
    of it, keeps the bloom audible wherever the profile shows real vibrato: 3.7 c on the clarinet, 4.5 c on the
    bassoon (12 of 19), within 0.8 c of the measured depth where nearly every note has it (horn, strings)."""
    notes = prof['notes']
    ds = [float(n.get('vib_depth') or 0.0) for n in notes]
    on = [d for d in ds if d > VIB_FLOOR_C]
    rs = [float(n.get('vib_rate') or 0.0) for n, d in zip(notes, ds)
          if d > VIB_FLOOR_C and (n.get('vib_rate') or 0) > 0]
    depth = float(np.median(on)) * (0.5 + 0.5 * len(on) / len(ds)) if on else 0.0
    return depth, float(np.median(rs)) if rs else 5.5


def _median_db(y, a, b, sr):
    """median level of 10 ms frames in y[:, a:b], dB (a stretch shorter than a frame is one frame)"""
    f = max(1, int(0.01 * sr))
    m = max(1, (b - a) // f)
    x = (y[:, a:a + m * f] ** 2).sum(axis=0)
    return float(np.median(10 * np.log10(x[:len(x) // m * m].reshape(m, -1).mean(axis=1) + 1e-20)))


def _hold_tapers(y, tapers, starts, sr):
    """Hold each phrase end to its taper after the room, in place on one line's output y (2, n).

    Why: a steady tone (no vibrato yet) meets one fixed complex gain of the room per partial, so dry and wet sum
    partly in or out of phase. The end's pitch sag moves the partials across the room's response, the sum changes,
    and that can undo the taper: a clarinet D5 phrase end fell 0.4 dB against the 5 dB setting (3.4 dB with the
    room off, 3.3 dB with the room on and no sag; the Sound Engineer's #346 ear round build). Here the level after
    the room is read (median of 10 ms frames) over the held stretch before the taper and over the end, where the
    fall is full; when it fell less than it should, the rest is taken off with the taper's own shape, held through
    the release and the room tail, and eased back over 50 ms before the line's next phrase. It only ever lowers."""
    n = y.shape[1]
    for tp in tapers:
        h0, t0, e0, end = (int(np.clip(tp[k], 0, n)) for k in ('h0', 't0', 'e0', 'end'))
        if not (h0 < t0 < e0 < end) or tp['want'] <= 0:
            continue
        fell = _median_db(y, h0, t0, sr) - _median_db(y, e0, end, sr)
        short = min(float(tp['want'] - fell), 12.0)
        if short <= 0.05:
            continue
        s1 = min([s for s in starts if s > end] + [n])
        x = np.clip(np.arange(s1 - t0) / (0.85 * tp['span'] * sr), 0, 1)
        g = -short * x ** 1.3
        ease = min(int(0.05 * sr), s1 - end)
        if s1 < n and ease > 0:
            g[len(g) - ease:] *= np.linspace(1.0, 0.0, ease)
        y[:, t0:s1] *= 10 ** (g / 20)


def lines(ns, chord_s):
    """Split notes [(st, m, d, v)] into monophonic lines: notes starting within chord_s of each other are a chord
    and go to different lines; each note joins the free line whose last pitch is nearest."""
    out = []
    i = 0
    while i < len(ns):
        j = i
        while j < len(ns) and ns[j][0] - ns[i][0] <= chord_s:
            j += 1
        used = set()
        for nt in sorted(ns[i:j], key=lambda z: -z[1]):
            best, cost = None, None
            for li, ln in enumerate(out):
                if li in used or ln[-1][0] > ns[i][0] - chord_s:
                    continue
                c = abs(ln[-1][1] - nt[1]) + (0.0 if ln[-1][0] + ln[-1][2] >= nt[0] - 1.0 else 24.0)
                if cost is None or c < cost:
                    best, cost = li, c
            if best is None:
                out.append([nt])
                used.add(len(out) - 1)
            else:
                out[best].append(nt)
                used.add(best)
        i = j
    return out


def phrases(line, breath_gap):
    """One line -> phrases [[(st, m, d, v, how)]], how = 'breath' | 'slur' | 'tongue'."""
    out = []
    for st, m, d, v in line:
        if out:
            pst, pm, pd, pv, _ = out[-1][-1]
            pend = pst + pd
            if pst + 1e-3 < st < pend - 1e-3:
                out[-1].append((st, m, d, v, 'slur'))
                continue
            if st >= pend - 1e-3 and st - pend < breath_gap:
                out[-1].append((st, m, d, v, 'tongue'))
                continue
        out.append([(st, m, d, v, 'breath')])
    return out


def perform(prof, notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, mimic_params=None, perform=None):
    """notes [(start s, midi, dur s, vel 0..127)] -> (2, total_n). mimic_params: the instrument's 'params';
    perform: True or a dict of DEFAULTS overrides."""
    check_profile(prof)
    P, post = settings(prof, mimic_params, perform)
    lanes = lanes or {}
    out = np.zeros((2, total_n))
    ns = sorted([(float(n[0]), float(n[1]), float(n[2]), float(n[3]) / 127.0) for n in notes if n[2] > 0],
                key=lambda z: (z[0], -z[1]))
    if not ns:
        return out
    seed = int(P['seed'])
    t_song0 = beat0 * 60.0 / bpm
    vib_med, rate_med = _measured_vibrato(prof)
    vib_default = float(P['vib']) if P['vib'] is not None else vib_med
    vib_rate = float(P['vib_rate']) if P['vib_rate'] is not None else rate_med

    def lane_vec(name, idx, default):
        c = lanes.get(name)
        if c is None or len(c) == 0:
            return np.full(len(idx), float(default))
        c = np.asarray(c, dtype=np.float64)
        return c[np.clip(idx, 0, len(c) - 1)]

    def lane_at(name, i, default):
        return float(lane_vec(name, np.array([int(i)]), default)[0])

    rel = float(P['release'])
    lead = float(P['lead_ms']) / 1000.0
    nyq = min(sr / 2 * 0.95, 20000)
    sr_c = sr / CR
    all_ph = []                                            # (line, phrase), a line's phrases in time order
    for li, ln in enumerate(lines(ns, float(P['chord_ms']) / 1000)):
        all_ph += [(li, ph) for ph in phrases(ln, float(P['breath_gap']))]
    # the room, ring and sympathetic strings, as mimic's own post stage has them, run per line, so that each
    # phrase end can be held to its taper after the room (_hold_tapers)
    post['room'], post['room_mix'] = P['room'], float(P['room_mix'])
    buf = np.zeros((2, total_n))
    tapers, starts = [], []

    def flush():
        y = mimic._post(buf, prof, post, sr, seed)
        _hold_tapers(y, tapers, starts, sr)
        out[:] += y
        buf[:] = 0.0
        tapers.clear()
        starts.clear()

    for k, (li, ph) in enumerate(all_ph):
        if k and li != all_ph[k - 1][0]:
            flush()
        p0 = ph[0][0] - lead                               # the air starts a little before the tone speaks
        p_end = max(st + d for st, _, d, _, _ in ph)
        a0 = int(round(p0 * sr))
        starts.append(a0)
        b0 = int(round((p_end + rel + 0.05) * sr))
        if b0 <= 0 or a0 >= total_n:
            continue
        n = b0 - a0
        nc = n // CR + 2
        tc = np.arange(nc) * CR / sr                       # s from the phrase's air start
        ms0 = int(round((ph[0][0] + t_song0) * 1000))
        prng = _rng(seed, ms0, 1)
        gidx = a0 + np.arange(nc) * CR                     # sample index in the buffer, for the lanes

        midi = np.zeros(nc)
        vel = np.zeros(nc)
        bend = np.zeros(nc)                                # semitones
        art_db = np.zeros(nc)                              # articulation dips (tongue, slur, taper)
        vib_amt = np.zeros(nc)                             # cents peak, per note, growing in
        note_of = np.zeros(nc, int)
        onsets = []                                        # (control index, hardness)
        for j, (st, m, d, v, how) in enumerate(ph):
            i0 = min(nc - 1, max(0, int(round((st - p0) * sr_c))))
            nrng = _rng(seed, int(round((st + t_song0) * 1000)), 2)
            midi[i0:] = m + nrng.normal(0, 0.03)            # +-3 c intonation per note
            vel[i0:] = v
            note_of[i0:] = j
            age = tc[i0:] - tc[i0]
            if how == 'slur':
                pm = ph[j - 1][1]
                sl = max(0.015, float(P['slur_ms']) / 1000 * (1 + 0.2 * nrng.normal()))
                bend[i0:] += (pm - m) * (1 - _smooth(age / sl))
                art_db[i0:] += -0.8 * np.exp(-((age - sl * 0.5) / (sl * 0.7)) ** 2)
            elif how == 'tongue':
                tg = float(np.clip(lane_at('tongue', a0 + i0 * CR, P['tongue']), 0, 1))
                gap = st - (ph[j - 1][0] + ph[j - 1][2])
                dip = -(10 + 10 * tg)
                # the tone stops for the gap (the tongue touches), then speaks again in ~25-45 ms
                ig = max(0, i0 - int(round(max(gap, 0.02) * sr_c)))
                art_db[ig:i0] += dip * _smooth((tc[ig:i0] - tc[ig]) / max(tc[i0] - tc[ig], 1e-3))
                ts = 0.025 + 0.02 * (1 - tg)
                art_db[i0:] += dip * (1 - _smooth(age / ts))
                onsets.append((i0, tg))
            else:
                onsets.append((i0, float(np.clip(lane_at('tongue', a0 + i0 * CR, P['tongue']), 0, 1))))
            sc = lane_at('scoop', a0 + i0 * CR, 0.0)
            if sc > 0:
                stime = float(P['scoop_ms']) / 1000 * (0.7 + 0.6 * sc) * np.exp(nrng.normal(0, 0.12))
                x = np.clip(age / stime, 0, 1)
                bend[i0:] += -sc * (1 - x) ** 2
                art_db[i0:] += -2.0 * (1 - x) ** 2
            # vibrato: delayed, then growing; only notes long enough reach it
            dep = lane_at('vib', a0 + i0 * CR, vib_default) * np.exp(nrng.normal(0, 0.12))
            delay = float(P['vib_delay']) * np.exp(nrng.normal(0, 0.2))
            grow = max(0.05, float(P['vib_grow']) * np.exp(nrng.normal(0, 0.2)))
            curve = max(dep, 0.0) * np.clip((age - delay) / grow, 0, 1) ** 1.3
            if how == 'slur' and i0 > 0:
                # the vibrato carries through a slur and eases back (no step in pitch at the join)
                curve = np.maximum(curve, vib_amt[i0 - 1] * np.exp(-age / 0.12))
            vib_amt[i0:] = curve
        # pre-roll (the air lead) belongs to the first note
        first_i = min(nc - 1, int(round(lead * sr_c)))
        midi[:first_i] = ph[0][1]
        vel[:first_i] = ph[0][3]

        # breath envelope: onset rise (after the lead), sustain, taper and stop at the phrase end
        att = float(P['attack_ms']) / 1000
        t_tone = tc - lead
        tg0 = onsets[0][1] if onsets else float(P['tongue'])
        att_eff = max(att * (1.25 - 0.6 * tg0), 0.005)
        env = _smooth(t_tone / att_eff)
        i_end = min(nc - 1, int(round((p_end - p0) * sr_c)))
        tail = tc[i_end:] - tc[i_end]
        env[i_end:] *= np.clip(1 - tail / max(rel, 1e-3), 0, 1) ** 1.6
        # the taper before the stop: a phrase of 0.5 s or more falls over the last 35 % of its last note, or over
        # its last 0.3 s when that note is short (the fall then starts in the notes before it; a short last note
        # had no taper at all), and the pitch sags a little. The fall is full by 85 % of the span and holds there.
        plen = p_end - ph[0][0]
        span = max(0.35 * (p_end - ph[-1][0]), min(0.3, 0.35 * plen))
        if plen >= 0.5:
            ti0 = max(0, int(round((p_end - span - p0) * sr_c)))
            x = np.clip((tc[ti0:] - tc[ti0]) / (0.85 * span), 0, 1)
            art_db[ti0:] += -float(P['taper_db']) * x ** 1.3
            bend[ti0:] += -float(P['end_sag_cents']) / 100 * x ** 2
        lvl_lane = lane_vec('level', gidx, 0.0)
        if plen >= 0.5:
            # what the end must fall by, read after the room: the taper plus what the level lane falls there
            # the held level: the last note's own sustain before the taper, or the 0.5 s before it when that is
            # longer (a short last note)
            i_last = int(round((ph[-1][0] - p0) * sr_c))
            h0 = max(first_i + int(0.12 * sr_c), min(ti0 - int(0.5 * sr_c), i_last + int(0.12 * sr_c)))
            e0 = max(ti0 + 1, int(round((p_end - 0.15 * span - p0) * sr_c)))
            if h0 < ti0 < e0 < i_end:
                want = float(P['taper_db']) + float(np.mean(lvl_lane[h0:ti0]) - np.mean(lvl_lane[e0:i_end]))
                tapers.append(dict(h0=a0 + h0 * CR, t0=a0 + ti0 * CR, e0=a0 + e0 * CR, end=a0 + i_end * CR,
                                   span=span, want=want))
        breath_lane = lane_vec('breath', gidx, 0.0)
        bend += lane_vec('bend', gidx, 0.0)
        wander_db = float(P['breathe_db']) * _wander(prng, nc, 0.7, sr_c)
        drift = float(P['drift_cents']) / 100 * _wander(prng, nc, 1.2, sr_c)

        # vibrato oscillator: one continuous phase over the phrase, rate and depth wandering slowly
        rate = vib_rate * (1 + 0.07 * _wander(prng, nc, 0.9, sr_c))
        vph = 2 * np.pi * np.cumsum(rate) / sr_c + prng.uniform(0, 2 * np.pi)
        vdep = vib_amt * (1 + 0.15 * _wander(prng, nc, 0.7, sr_c))
        vib_c = vdep / 100 * np.sin(vph)                   # semitones
        vib_am = float(P['vib_am_db']) * (vdep / max(vib_default, 1.0)) * np.sin(vph - 0.6)   # the breath pulse

        pitch = midi + bend + vib_c + drift                # MIDI at control rate
        f0c = 440.0 * 2 ** ((pitch - 69) / 12)
        fmin = float(f0c[first_i:max(first_i + 1, i_end)].min())
        K = int(max(1, min(MAX_PARTIALS, nyq / fmin)))
        kk = np.arange(1, K + 1, dtype=float)
        lk = np.log2(kk)

        # each note's source levels; a slur moves the colour with the pitch, no step
        srcs, nbr = [], []
        for st, m, d, v, how in ph:
            s, ab = _source_db(prof, m, v, kk, P)
            srcs.append(s + prng.normal(0, 0.5, K))
            nbr.append(ab)
        src_c = np.array(srcs)[note_of]                    # (nc, K)
        for j in range(1, len(ph)):
            if ph[j][4] != 'slur':
                continue
            i0 = max(0, int(round((ph[j][0] - p0) * sr_c)))
            sl = max(0.015, float(P['slur_ms']) / 1000)
            i1 = min(nc, i0 + int(np.ceil(2 * sl * sr_c)) + 1)
            x = _smooth((tc[i0:i1] - tc[i0]) / (2 * sl))[:, None]
            src_c[i0:i1] = srcs[j - 1][None, :] * (1 - x) + srcs[j][None, :] * x
        a, b, w = nbr[0]
        B = a['B'] * (1 - w) + b['B'] * w
        stretch = np.sqrt(1 + B * kk ** 2)
        # level per partial at control rate: source, the body at the partial's current frequency, the lanes
        body_hz, body_db = np.log2(np.array(prof['body_hz'])), np.array(prof['body_db'])
        fk = f0c[:, None] * (kk * stretch)[None, :]
        body = np.interp(np.log2(np.maximum(fk, 1.0)), body_hz, body_db)
        common = (art_db + lvl_lane + wander_db + vib_am)[:, None]
        tilt = (0.15 * lvl_lane + 0.05 * vib_am)[:, None] * lk[None, :]
        # per-partial onset: the upper partials speak later (the profile's attack times at the first note)
        atk_k = np.maximum(mimic._blend(a, b, w, 'attack', kk * stretch * 440.0 * 2 ** ((ph[0][1] - 69) / 12)), 0.01)
        atk_k = np.minimum(atk_k * 0.6, 0.18)
        up = _smooth(np.maximum(t_tone, 0)[:, None] / atk_k[None, :])
        lvl_db = src_c + body + common + tilt + 20 * np.log10(np.maximum(env[:, None] * up, 1e-5))
        amp_c = 10 ** (lvl_db / 20)
        amp_c[fk >= nyq] = 0.0

        # synthesis
        t = np.arange(n) / sr
        f0s = np.interp(t, tc, f0c)
        phase = 2 * np.pi * np.cumsum(f0s) / sr
        yl = np.zeros(n)
        yr = np.zeros(n)
        wid = float(P['width'])
        for i, k in enumerate(kk):
            if amp_c[:, i].max() < 1e-6:
                continue
            amp = np.interp(t, tc, amp_c[:, i])
            th = k * stretch[i] * phase + prng.uniform(0, 2 * np.pi)
            dphi = wid * prng.uniform(-np.pi / 2, np.pi / 2) if k > 1 else 0.0
            dl = 1 + wid * prng.uniform(-0.1, 0.1)
            yl += amp * np.sin(th) * dl
            yr += amp * np.sin(th + dphi) / dl
        harm_pow_c = (amp_c ** 2).sum(axis=1)

        # air: the measured sustained noise spectrum, riding the tone's envelope, led by the breath at each onset
        aa, bb, ww = nbr[len(nbr) // 2]
        nz_hz = np.log2(np.array(prof['noise_hz']))
        ns_db = np.array(aa['noise_sus']) * (1 - ww) + np.array(bb['noise_sus']) * ww + float(P['noise'])
        ref_db = 10 * np.log10(harm_pow_c.max() + 1e-15)
        fr = np.fft.rfftfreq(n, 1 / sr)
        g = 10 ** ((np.interp(np.log2(np.maximum(fr, 1.0)), nz_hz, ns_db, left=-150, right=-150) + ref_db) / 20)
        g[fr < 20] = 0
        sus = np.sqrt(harm_pow_c / (harm_pow_c.max() + 1e-15))
        air = sus * 10 ** (breath_lane / 20)
        pre = np.clip(tc / max(lead, 1e-3), 0, 1) * (tc < lead + att_eff) * 0.6
        air = np.maximum(air, pre * np.clip(1 - t_tone / att_eff, 0, 1))
        for i0, hard in onsets:
            x = tc[i0:] - tc[i0]
            air[i0:] += (0.35 + 0.9 * hard) * np.exp(-x / 0.035)
        for y in (yl, yr):
            wn = prng.standard_normal(n)
            y += np.fft.irfft(np.fft.rfft(wn) * g, n) * np.interp(t, tc, air)
        y2 = np.stack([yl, yr])
        # the measured attack map (chiff, edge tone, bow scrape) at each onset, scaled to this phrase's level
        seg = y2.mean(axis=0)[int((lead + 0.25) * sr):int((lead + 1.5) * sr)]
        rms_now = float(np.sqrt(np.mean(seg ** 2))) if len(seg) > sr // 10 else float(np.sqrt(np.mean(y2.mean(0) ** 2)))
        for i0, hard in onsets:
            a_, b_, w_ = nbr[min(int(note_of[i0]), len(nbr) - 1)]
            if not ('atk_tf' in a_ and 'atk_tf' in b_):
                continue
            s0 = i0 * CR
            m_ = min(n - s0, int(0.2 * sr))
            if m_ <= 0:
                continue
            an = mimic._attack_noise(a_, b_, w_, prng, m_, sr, 0.5 + 0.8 * hard, rms_now)
            y2[:, s0:s0 + m_] += an[:, :m_]
        s_a, s_b = max(0, a0), min(total_n, b0)
        if s_b > s_a:
            buf[:, s_a:s_b] += y2[:, s_a - a0:s_b - a0]
    flush()
    return out
