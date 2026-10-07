"""Capture faults in a recording, found before it is measured (ledger:M172).

Two faults get learned as the instrument's own sound if nothing says so:
- soft-limited peaks: the browser microphone chain (the phone page, Chrome) flattens loud peaks into a plateau near
  0.894-0.899 instead of clipping at 1.0, so a "peak > 0.98" clip test never fires. A limiter on a mastered file
  does the same.
- band-limited capture: a Bluetooth headset microphone sends 16 kHz audio, nothing above 7 to 8 kHz. A normal mic
  reads -26 to -30 dB of 8-12 kHz against 0.5-2 kHz; the headset reads about -120.
"""
import numpy as np

PLATEAU_FLOOR = 0.88       # only peaks above this count
PLATEAU_NEAR = 0.006       # within this of the file's own maximum
PLATEAU_PER_S = 20         # more than this many such samples per second of audio is a plateau
CLIP = 0.98                # at or over this it is plain clipping, which other checks report
AIR_MIN_DB = -60.0         # 8-12 kHz under 0.5-2 kHz by more than this: band-limited
FULL_MAX = 1 << 22         # up to this many samples (95 s at 44.1 kHz) air_db is one FFT over the whole file


def plateau(y, sr):
    """-> (level, samples per second) of a flattened peak, or None. y: mono or (channels, n)."""
    x = np.abs(np.asarray(y, dtype=np.float64)).reshape(-1) if np.ndim(y) == 1 else \
        np.abs(np.asarray(y, dtype=np.float64)).max(axis=0)
    if not len(x):
        return None
    top = float(x.max())
    if top >= CLIP or top <= PLATEAU_FLOOR:
        return None
    n = int(np.count_nonzero(x >= max(PLATEAU_FLOOR, top - PLATEAU_NEAR)))
    per_s = n / (len(x) / sr)
    return (round(top, 3), round(per_s, 1)) if per_s > PLATEAU_PER_S else None


def air_db(y, sr, seg=16384):
    """Level of 8-12 kHz against 0.5-2 kHz, dB, as vox rec/server.py air_db measures it (a file over FULL_MAX
    samples is averaged over Hann frames instead). None when the file is silent or too short; below sr 24 kHz the band is missing by definition."""
    x = np.asarray(y, dtype=np.float64)
    x = x if x.ndim == 1 else x.mean(axis=0)
    if sr < 24000:
        return -200.0
    if len(x) < 2048:
        return None
    if len(x) <= FULL_MAX:                       # one Hann FFT over the whole file, exactly as vox measures it
        n, S, k = len(x), np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2, 1
    else:                                        # a long file: the mean of Hann frames, a few dB from the above
        n, hop, k = seg, seg // 2, 0
        w, S = np.hanning(n), np.zeros(n // 2 + 1)
        for a in range(0, len(x) - n + 1, hop):
            S += np.abs(np.fft.rfft(x[a:a + n] * w)) ** 2
            k += 1
    f = np.fft.rfftfreq(n, 1 / sr)
    air, mid = S[(f >= 8000) & (f < 12000)], S[(f >= 500) & (f < 2000)]
    if mid.mean() < 1e-12 * n * k:
        return None
    return round(float(10 * np.log10(air.mean() + 1e-20) - 10 * np.log10(mid.mean() + 1e-20)), 1)


def check(y, sr, no_top=False):
    """-> {'warnings': [plain sentences], 'plateau': (level, per_s) or None, 'air_db': dB or None}.
    no_top: the instrument is known to have nothing above 8 kHz, so a missing top is not a fault."""
    p, a = plateau(y, sr), air_db(y, sr)
    W = []
    if p:
        W.append(f"peaks flattened at {p[0]:.3f} ({p[1]:.0f} samples a second sit there): a browser mic or a "
                 f"limiter squashed them, so attacks and loudness measure soft. Record quieter or with the mic's "
                 f"processing off.")
    if a is not None and a < AIR_MIN_DB and not no_top:
        W.append(f"nothing above about 8 kHz (8-12 kHz is {a:.0f} dB under 0.5-2 kHz; a normal mic reads -26 to "
                 f"-30): a Bluetooth headset or a phone call line. Brightness and breath measure as missing. Record "
                 f"with the phone's or computer's own mic, or say no_top=True if the instrument has no top.")
    return {'warnings': W, 'plateau': p, 'air_db': a}
