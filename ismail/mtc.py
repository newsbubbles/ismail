"""MIDI Time Code (MIDI parity step 8, part 2; plan in D:/ismail-hq/proposals/midi-parity.md): pure, deterministic
helpers for timecode, its frame counts and its messages. The live engine sends it (live/midi_out.py) and chases it
(live/timecode_in.py) with these.

Rates. 24, 25 and 30 frames per second count every frame. 29.97 is drop-frame (SMPTE): it runs at 30000 / 1001
frames per second and its labels skip frame numbers 0 and 1 at the start of every minute except every tenth, so
the label stays within a frame of the clock on the wall (17982 frames in ten minutes, 2589408 in a day). A frame
count n here is the frames since 00:00:00:00 (n / fps is the real time); a label is (hours, minutes, seconds,
frames). Labels wrap at 24 hours.

Quarter frames (0xF1 0nnn dddd). Eight messages carry one label, a nibble each: frames low and high, seconds low and
high, minutes low and high, hours low, then hours' top bit with the rate code (0 = 24, 1 = 25, 2 = 29.97 drop-frame,
3 = 30) in bits 1 and 2. Four go out per frame, so the eight span two frames. Quarter frame q (counted from
midnight) is piece q % 8 of the label for frame 2 * (q // 8): piece 0 goes out as that frame starts, piece k a
quarter of a frame later each. Running backwards the same pieces come in the other order, 7 to 0, so a receiver
tells the direction from the order. Here each piece sits at q = 4 F + k in both directions, F the frame its label
carries: forward the label is whole on piece 7, at F + 7/4 frames; backwards on piece 0, at F. That is the two
frames of latency a chaser has before it knows the time; after that each piece moves it a quarter frame on.

Full frame: F0 7F <device> 01 01 hh mm ss ff F7 (hh holds the rate code in bits 5 and 6) gives a time at once, for a
locate or a stop. Device 7F is every device.
"""
import collections
import re
from fractions import Fraction

QF = 0xF1
RATES = (24, 25, 29.97, 30)
CODE = {24: 0, 25: 1, 29.97: 2, 30: 3}
BY_CODE = {v: k for k, v in CODE.items()}
FPS = {24: Fraction(24), 25: Fraction(25), 29.97: Fraction(30000, 1001), 30: Fraction(30)}
NOMINAL = {24: 24, 25: 25, 29.97: 30, 30: 30}       # frame numbers in a second as labels count them
DF_10MIN = 17982                                     # drop-frame: frames in ten minutes (18000 less 9 x 2)
DF_MIN = 1798                                        # drop-frame: frames in a minute that drops two numbers
RATE_HELP = "24, 25, 29.97 (drop-frame; also '30df' or '29.97df') or 30 frames per second"


def rate_of(x):
    """The rate x names (24, 25, 29.97 or 30; '29.97df', '30df', 'df' and '30 df' name drop-frame too), else
    ValueError."""
    if isinstance(x, str):
        s = x.strip().lower().replace(' ', '').replace('fps', '')
        if s in ('29.97', '29.97df', '2997', '30df', 'df', '29.97drop', '30drop'):
            return 29.97
        try:
            x = float(s)
        except ValueError:
            raise ValueError(f"rate={x!r}: {RATE_HELP}")
    try:
        v = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"rate={x!r}: {RATE_HELP}")
    for r in RATES:
        if abs(v - r) < 0.006:
            return r
    raise ValueError(f"rate={x!r}: {RATE_HELP}")


def is_drop(rate):
    return rate == 29.97


def fmt_rate(rate):
    return "29.97 fps drop-frame" if is_drop(rate) else f"{rate} fps"


def frames_per_day(rate):
    if is_drop(rate):
        return 144 * DF_10MIN
    return 86400 * NOMINAL[rate]


def quarters_per_day(rate):
    return 4 * frames_per_day(rate)


def frame_s(rate):
    """Seconds per frame (exact)."""
    return 1 / FPS[rate]


def check_tc(tc, rate):
    """ValueError unless tc = (h, m, s, f) is a label the rate has (drop-frame skips 0 and 1 at most minutes)."""
    h, m, s, f = (int(v) for v in tc)
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60 and 0 <= f < NOMINAL[rate]):
        raise ValueError(f"{h:02d}:{m:02d}:{s:02d}:{f:02d} is not a time at {fmt_rate(rate)}: hours 0 to 23, minutes "
                         f"and seconds 0 to 59, frames 0 to {NOMINAL[rate] - 1}")
    if is_drop(rate) and s == 0 and f < 2 and m % 10:
        raise ValueError(f"{h:02d}:{m:02d}:00;{f:02d} does not exist in drop-frame: frames 0 and 1 are skipped at "
                         f"the start of every minute but each tenth; the minute starts at {h:02d}:{m:02d}:00;02")
    return h, m, s, f


def tc_to_frames(tc, rate):
    """Frames since 00:00:00:00 at the label tc = (h, m, s, f)."""
    h, m, s, f = check_tc(tc, rate)
    nom = NOMINAL[rate]
    n = ((h * 60 + m) * 60 + s) * nom + f
    if is_drop(rate):
        mins = h * 60 + m
        n -= 2 * (mins - mins // 10)
    return n


def frames_to_tc(n, rate):
    """The label (h, m, s, f) of frame n (wrapped at 24 hours, so a negative n counts back from midnight)."""
    n = int(n) % frames_per_day(rate)
    nom = NOMINAL[rate]
    if is_drop(rate):
        d, r = divmod(n, DF_10MIN)
        n += 18 * d + (2 * ((r - 2) // DF_MIN) if r >= 2 else 0)
    f = n % nom
    s = n // nom % 60
    m = n // (nom * 60) % 60
    h = n // (nom * 3600) % 24
    return h, m, s, f


def frames_to_seconds(n, rate):
    return float(n * frame_s(rate))


def seconds_to_frames(sec, rate):
    """Frames (not rounded) at real time `sec` since 00:00:00:00."""
    return float(Fraction(sec) * FPS[rate])


def fmt_tc(tc, rate=25):
    """'hh:mm:ss:ff', or 'hh:mm:ss;ff' for drop-frame."""
    h, m, s, f = tc
    return f"{h:02d}:{m:02d}:{s:02d}{';' if is_drop(rate) else ':'}{f:02d}"


def fmt_frames(n, rate):
    return fmt_tc(frames_to_tc(n, rate), rate)


def parse_tc(text, rate):
    """A label written 'hh:mm:ss:ff' (';' or '.' before the frames for drop-frame is fine) at `rate`."""
    m = re.fullmatch(r'\s*(\d{1,2})[:;.](\d{1,2})[:;.](\d{1,2})[:;.](\d{1,2})\s*', str(text))
    if not m:
        raise ValueError(f"timecode {text!r}: write it 'hh:mm:ss:ff', e.g. '01:00:00:00'")
    return check_tc(tuple(int(g) for g in m.groups()), rate)


def offset_frames(x, rate):
    """An offset as frames since 00:00:00:00: a label 'hh:mm:ss:ff', or seconds (0 up to 24 hours; a drop-frame day
    is 86399.9136 s, so seconds past it wrap to just after midnight)."""
    if x is None:
        return 0
    if isinstance(x, str) and re.search(r'[:;]', x):
        return tc_to_frames(parse_tc(x, rate), rate)
    try:
        sec = float(x)
    except (TypeError, ValueError):
        raise ValueError(f"offset={x!r}: a timecode 'hh:mm:ss:ff' (e.g. '01:00:00:00') or seconds")
    if not 0 <= sec < 86400:
        raise ValueError(f"offset={x!r}: 0 up to 24 hours (86400 s), or a timecode 'hh:mm:ss:ff'")
    return int(round(seconds_to_frames(sec, rate))) % frames_per_day(rate)


def nearest_label(tc, rate):
    """A label the rate has, nearest tc: frames held to the rate's last, a skipped drop-frame number moved on to 02."""
    h, m, s, f = (int(v) for v in tc)
    h, m, s, f = min(max(h, 0), 23), min(max(m, 0), 59), min(max(s, 0), 59), min(max(f, 0), NOMINAL[rate] - 1)
    if is_drop(rate) and s == 0 and f < 2 and m % 10:
        f = 2
    return h, m, s, f


def offset_problem(x, rate):
    """None when offset x exists at `rate`, else (what is wrong, a label that does exist there)."""
    try:
        offset_frames(x, rate)
        return None
    except ValueError as ex:
        m = re.fullmatch(r'\s*(\d{1,2})[:;.](\d{1,2})[:;.](\d{1,2})[:;.](\d{1,2})\s*', str(x))
        good = fmt_tc(nearest_label(tuple(int(g) for g in m.groups()), rate), rate) if m else None
        return str(ex), good


# ---------------------------------------------------------------------------------------------- the messages
def piece(k, tc, rate):
    """The data byte of quarter-frame piece k (0 to 7) of the label tc."""
    h, m, s, f = tc
    vals = (f & 0xF, f >> 4, s & 0xF, s >> 4, m & 0xF, m >> 4, h & 0xF, (h >> 4) | (CODE[rate] << 1))
    return (k << 4) | vals[k]


def quarter_frame(q, rate):
    """The message for quarter frame q (counted from midnight; wraps at 24 hours)."""
    q = int(q) % quarters_per_day(rate)
    return bytes([QF, piece(q % 8, frames_to_tc(q // 8 * 2, rate), rate)])


def quarter_frames(tc, rate):
    """The eight data bytes of one label, pieces 0 to 7."""
    return [piece(k, tc, rate) for k in range(8)]


def full_frame(tc, rate, device=0x7F):
    """The full-frame SysEx for label tc: F0 7F <device> 01 01 hh mm ss ff F7."""
    h, m, s, f = check_tc(tc, rate)
    return bytes([0xF0, 0x7F, device & 0x7F, 0x01, 0x01, (CODE[rate] << 5) | h, m, s, f, 0xF7])


def read_full_frame(data):
    """(label, rate) from a full-frame SysEx, or None when the bytes are not one (or carry no real time)."""
    b = bytes(data)
    if len(b) != 10 or b[0] != 0xF0 or b[1] != 0x7F or b[3:5] != b'\x01\x01' or b[9] != 0xF7:
        return None
    rate = BY_CODE[(b[5] >> 5) & 3]
    try:
        return check_tc((b[5] & 0x1F, b[6], b[7], b[8]), rate), rate
    except ValueError:
        return None


def unwrap(q, near, rate):
    """q moved by whole days to the one nearest `near` (both in quarter frames): a time read across midnight."""
    day = quarters_per_day(rate)
    return q + round((near - q) / day) * day


# ---------------------------------------------------------------------------------------------- reading them
Reading = collections.namedtuple('Reading', 'q direction rate complete tc continued')


class QuarterFrames:
    """Quarter-frame data bytes in, in arrival order; positions out. feed() returns None while it cannot tell the time
    (fewer than eight pieces in order since it started, or since the order broke), else a Reading: q, the quarter
    frame this piece sits on (4 F + k, wrapped at 24 hours); direction +1 forward or -1 backwards (from the order);
    the rate; complete True when this piece made a whole label (then tc is that label, and continued says whether
    the order held since the last whole label and this one is where counting on from it led: False after a broken
    order or a label somewhere else). A piece out of order (one
    lost, or a jump) or a turn round starts the eight again. A label that cannot exist (frame 30 at 25 fps, a skipped
    drop-frame number) is counted in `bad` and starts the eight again."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.nib = [None] * 8
        self.prev = None
        self.dir = 0
        self.run = 0
        self.q = None
        self.rate = None
        self.bad = getattr(self, 'bad', 0)

    def feed(self, data):
        k, v = (int(data) >> 4) & 7, int(data) & 0xF
        d = 0
        if self.prev is not None:
            step = (k - self.prev) % 8
            d = 1 if step == 1 else -1 if step == 7 else 0
        self.prev = k
        if d == 0 or (self.dir and d != self.dir):            # out of order or turned round: start the eight again
            self.nib = [None] * 8
            self.nib[k] = v
            self.run, self.q, self.dir = 1, None, d
            return None
        self.dir = d
        self.nib[k] = v
        self.run += 1
        if self.q is not None:
            self.q = (self.q + d) % quarters_per_day(self.rate)
        if self.run >= 8 and k == (7 if d > 0 else 0):
            n = self.nib
            rate = BY_CODE[(n[7] >> 1) & 3]
            # the bits a label does not use (frames' top three, seconds' and minutes' top two) are reserved: masked
            tc = ((n[6] | (n[7] & 1) << 4), n[4] | (n[5] & 3) << 4, n[2] | (n[3] & 3) << 4, n[0] | (n[1] & 1) << 4)
            try:
                F = tc_to_frames(tc, rate)
            except ValueError:
                self.bad += 1
                self.nib, self.run, self.q, self.dir = [None] * 8, 0, None, 0
                return None
            q = 4 * F + (7 if d > 0 else 0)
            cont = self.q is not None and self.rate == rate and self.q == q
            self.q, self.rate = q, rate
            return Reading(q, d, rate, True, tc, cont)
        if self.q is None:
            return None
        return Reading(self.q, d, self.rate, False, None, None)
