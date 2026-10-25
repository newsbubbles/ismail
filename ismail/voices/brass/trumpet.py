"""Trumpet (Bb) as a performer: a measured additive lip-reed-and-bell voice, played a whole line at a time.

One note at a time. A note that starts before the previous one ends is SLURRED (no new tongue: the valves or the
lips move the pitch under continuous air); a gap means a new tongued note. Every technique is a lane (automation
'inst.lane.<name>', per sample) or a param with a default, so a part can be written once and played differently:

  bend      semitones added to the pitch (lip bends, smears)
  vib       vibrato depth in cents (lip/jaw vibrato; wanders in rate and depth, starts after vib_delay)
  level     dB of BLOWING on the whole part: a swell is louder and brighter together, as on the real horn
  breath    0..1 air in the tone (default param breath)
  tongue    0..1 attack hardness at each tongued note's start (0 = breath attack, 1 = hard tongue)
  scoop     semitones the note starts BELOW its pitch and rises from (read at the note's start); 0 = none
  fall      semitones the note drops at its end, fading out (read at the note's end); 0 = none
  doit      semitones the note rises at its end, fading out (read at the note's end); 0 = none
  shake     semitones of a lip shake up to the next partial and back (a minor third to a fourth); 0 = none
  flutter   0..1 flutter tongue (a rolled R behind the tone)
  growl     0..1 growl (a sung tone behind the trumpet's)
  halfvalve 0..1 half-valve (valves part way down: choked, nasal, airy, the pitch free to smear)
  plunger   0..1 plunger mute position, 0 open, 0.5 half, 1 closed against the bell ('wah' by moving it)
  jitter    cents rms of the lips' quick pitch wander (2-10 Hz), the blowing moving with it
  top       dB per octave added to the partials above 1.5 kHz (a player's brightness apart from the blowing); absent = none

Params: mute ('open', 'harmon' with the stem out, 'harmon_stem' with the stem in), and the defaults below.

The brass law (one rule for dynamics, attacks, releases and swells): the player's blowing level b (dB re ff) sets
harmonic k's level as b * (G1 + BRASS_B * log2 k): the fundamental follows the blowing, every octave of harmonic
number up follows it more steeply (the lip-reed's pulse sharpens and the bore steepens it as the air pressure
rises). So a soft note is dark, ff is brassy, the low harmonics arrive first at the attack, the top dies first at a
release, and a swell brightens as it grows. Velocity arrives 0..127; 127 is the Iowa MIS ff the tone is fitted to.

Measured from the University of Iowa MIS Bb trumpet, Jon Harnum's trumpet effects videos (CC BY), CC0 solo lines on
Freesound and lesson videos used for measuring only (INFO['sources'] lists which constant came from where). Only
numbers are kept here, no source audio. Built and judged round by round in a winds song study (blind picture
rounds, blind ear rounds on single notes and phrases), then promoted to the library (ledger:M331). The numbers are
the study's as passed by ear: the defaults render as the study's voice did, sample for sample. Window rules
(skills/ismail/references/windows.md): random draws keyed on song milliseconds, causal filters, held notes cut.
"""
import numpy as np
from scipy.signal import butter, sosfilt

INFO = {
    "summary": "Bb trumpet performer: additive lip-reed tone with one measured brass law for dynamics, attacks and "
               "swells; slurs, tonguing, scoops, falls, doits, shakes, bends, growl, flutter, half-valve, plunger and "
               "harmon mutes as lanes and params",
    "range": "concert E3-D6 (MIDI 52-86) as measured; to F6 (89) thinner; write concert pitch (the Bb trumpet "
             "reads a major second above)",
    "velocity": "blowing: 127 ff (the Iowa fit), 90 f, 55 mf, 35 mp, 22 p. Softer is darker by harmonic number "
                "(the brass law), not only quieter (the lessons' mf notes matched at 35-60)",
    "functions": {"perform": "the whole part at once: overlapping notes slur (no new tongue), a gap is a new "
                             "tongued note"},
    "lanes": {"bend": "semitones added to the pitch", "vib": "vibrato depth, cents (default param vib)",
              "level": "dB of blowing (swells brighten as they grow)", "breath": "0..1 air in the tone",
              "tongue": "0..1 attack hardness at note start",
              "scoop": "semitones below, rising into the note (read at note start)",
              "fall": "semitones dropped at the note end, fading (read at note end)",
              "doit": "semitones risen at the note end, fading (read at note end)",
              "shake": "semitones of a lip shake to the upper partial and back (0 = none)",
              "flutter": "0..1 flutter tongue", "growl": "0..1 sung growl", "halfvalve": "0..1 half-valve",
              "plunger": "0..1 plunger position, 0 open to 1 closed",
              "jitter": "cents rms of the lips' quick 2-10 Hz pitch wander (default param jitter_cents)",
              "top": "dB per octave added above 1.5 kHz, apart from the blowing (absent = none)"},
    "params": {
        "mute": "'open', 'harmon' (stem out: thin, buzzy, the cool-jazz harmon), 'harmon_stem' (stem in: nasal, "
                "'wah' with plunger)",
        "bright": "player/mic colour, dB/oct above 500 Hz (0 = the fit)", "breath": "air in the tone 0..1",
        "vib": "vibrato depth, cents (0 = straight)", "vib_rate": "vibrato rate, Hz",
        "vib_delay": "s before vibrato starts on a held note",
        "tongue": "tongue 0..1 where the tongue lane is not written",
        "slur_ms": "pitch move time of a slur, ms", "release": "air stop time at a note end, s",
        "scoop_ms": "scoop's slow time constant, ms (the first 60 % goes in 30 ms)",
        "fall_ms": "fall time for 12 semitones, ms", "doit_ms": "doit time for 12 semitones, ms",
        "shake_rate": "shakes per second (each up-and-back)", "flutter_rate": "tongue flaps per second",
        "growl_interval": "semitones the sung tone sits below the note",
        "onset_cents": "pitch blip at a tongued onset, cents",
        "drift_cents": "slow pitch wander, cents rms", "breathe_db": "slow blowing wander, dB rms",
        "noise_db": "air noise re the tone, dB", "room": "0..1 small-room reflections", "level": "output gain",
        "floor_db": "room-tone floor, dBFS (-200 = off)", "seed": "random seed",
        "jitter_cents": "the lips' quick pitch wander, cents rms (lesson players 3.6-7.5, Iowa ff 1.4)",
        "partial_wander": "each partial's own slow level wander from 1-2 kHz up, dB rms (None = 1.3, "
                          "PARTIAL_WANDER_DB; a harmon mute with the stem in 6)",
        "partial_wander_hz": "rate of that wander, Hz (None = 10, PARTIAL_WANDER_HZ)",
        "noise_wander": "slow level wander of the air noise, dB rms (0 = off; the harmon stem's floor drifts)",
        "noise_wander_hz": "rate of that wander, Hz",
        "air_lowcut": "Hz of a 4th-order high-pass on the breath band's air after its pitch-synchronous gate (0 = off; "
                      "the gate folded air 10 dB over a player's under 250 Hz)",
        "chiff_db": "dB added to the tongue's chiff burst, apart from the tongue's attack time (0 = off; a soft "
                    "player's onset has no burst over the rising tone: -30 on a passed phrase)",
        "h1_db": "dB added to the fundamental (a close mic or a room that lifts the lows; Harnum's F5 has H1 the "
                 "strongest, ours 11 dB under it at 0)"},
    "tips": "Write a slur as a small overlap (the next note starts before this one ends), a tongued note with a gap. "
            "The phrases that passed blind copied one player's take each: the bend lane from a 1 ms harmonic track of "
            "the take (a player's pitch carries a 30-35 Hz flutter of +-40-50 c on a blues opening and a few ms of "
            "travel at every note's ends, which a 93 ms pitch tracker smooths away), chiff_db -30, air_lowcut 300, "
            "the top lane per note (short notes 7-11 dB brighter at 2.5-10 kHz than long ones at the same level), "
            "and a tongued repeat with the player's gap depth (15-19 dB). Those are recreation settings, not laws: an "
            "original solo has not been judged yet.",
    "render": "about 6x realtime on one CPU core (a tongued and slurred 32-note line at 120 BPM, 44.1 kHz, "
              "2026-10-25 on a shared laptop); the cost is per phrase (harmonics up to 15 kHz, each with its own "
              "level curve). A performer: live it renders a bar at a time with context, and a deck plays it as "
              "the studio does by ear, not sample for sample (each phrase's draws are keyed on its first note).",
    "source": "measured: University of Iowa MIS Bb trumpet; Jon Harnum's trumpet effects (CC BY, credit Harnum); "
              "Freesound CC0 solo lines (emirdemirel, trumpetplayer); lesson videos measured for numbers only",
    "sources": [
        "University of Iowa Electronic Music Studios, MIS Bb trumpet 2012 "
        "(https://theremin.music.uiowa.edu/MIS-Pitches-2012/MISBbTrumpet2012.html), E3-D6, ff, vib and novib: the "
        "core tables. ENV_HZ, ENV_DB, ENV_REG, REG_MIDI, FINGER_DB (the tone per pitch at ff), G1 (the "
        "brass law's fundamental), ATT_FAST, ATT_SETTLE_S (attacks), ONSET_DIP_C, ONSET_DIP_S (onset pitch), "
        "PARTIAL_WANDER_DB, REL_DB_S, REL_TILT (releases), the vibrato. Free to use.",
        "Jon Harnum, Trumpet sound effects (1) half-valve, (4) the fall, (5) the doit, (9) the scoop "
        "(YouTube: https://www.youtube.com/watch?v=u9fnlRF56lM, qRKI-euzdfU, IhTTzb7R0zM, t8FbAtl-3sg): CC BY, "
        "credit Harnum. HALF_* (half-valve colour, steps, hiss, wobble), DOIT_* (doit curve, steps, air), SCOOP_FAST, "
        "SCOOP_FAST_S, SCOOP_AIR and scoop_ms (scoop curve, the onset buzz time), FALL_* (fall curve, partial "
        "breaks, tail air), FLOOR_ORDER, FLOOR_WHITE (the room floor), h1_db's note.",
        "Freesound CC0 solo lines by emirdemirel (417963, 417964, 416025, 416026) and trumpetplayer (651201, 651202, "
        "651203; https://freesound.org/people/trumpetplayer/sounds/651201/): tonguing and slurs, the phrase rounds' "
        "takes; TOP_HZ0 and the top lane (the blues lick's short notes), air_lowcut and chiff_db (off by default).",
        "Measured only, YouTube standard licence (cleared by the user for the defaults, 2026-10-25, credited): Bryan Davis, "
        "How to Growl and Flutter Tongue on Trumpet (the soft-note brass law: BRASS_HZ, TONE_AIR_LAW, BAND_TOP_HZ, "
        "BAND_TOP_ORDER, BAND_GAIN; BRASS_B's slope was calibrated on Davis mf F4 against Iowa ff F4; FINGER_SD, "
        "TONE_AIR, TONE_AIR_HZ, TONE_AIR_REF and TONE_AIR_TILT were fitted in the same round against Iowa ff and "
        "Davis mf; FLUTTER_DEPTH, FLUTTER_NOISE_DEPTH, FLUT_IV_SD, FLUT_DEPTH_SD, GROWL_AM, GROWL_PM_US, PW_SOFT "
        "and AIR_LAW's check); Wayne Bergeron, How to shake on the trumpet (the shake "
        "constants: SHAKE_GLIDE, SHAKE_UP, SHAKE_DIP_DB, SHAKE_CYC, SHAKE_UP_SD, SHAKE_CYC_UP, SHAKE_SAG, "
        "SHAKE_SAG_SD, SHAKE_SWOOP, SHAKE_SWOOP_SD, SHAKE_AIR_DB, SHAKE_AIR_WANDER, SHAKE_AIR_WANDER_HZ, "
        "SHAKE_PER_SD, SHAKE_REACH, SHAKE_REACH_SD, SHAKE_GHOST, SHAKE_GHOST_MS, shake_rate); Adam Meckler, How to do "
        "a Shake on the Trumpet (shake rate and width, a second player beside Bergeron); Kris Johnson, How to play a "
        "plunger mute like a pro (PLUNGER_CLOSED_DB, PLUNGER_FINE_DB, PLUNGER_HALF_DB; PW_SOFT's soft-top waver); "
        "Rufftips, Trumpet tip, harmon mute (MUTE_DB 'harmon' and 'harmon_stem'; REL_DB_S's harmon check); Brian "
        "Shook, Developing Trumpet Fundamentals: Flutter Tonguing (FLUTTER_DEPTH); the lesson players' pitch jitter "
        "(LIP_JIT_HZ, LIP_JIT_DB, jitter_cents); Louis Armstrong and his Hot Five, West End Blues (1928) opening "
        "cadenza (rips, shakes, vibrato in a real passage; its public-domain status is not assumed). Jason Bergman's "
        "commercial recording was listened to; no constant here comes from it.",
    ],
    "measured": "tone (per pitch at ff), attacks, onset pitch and vibrato from the University of Iowa MIS Bb trumpet; "
                "scoops, falls, doits and half-valve from Harnum's effects (CC BY); shakes, growl, flutter, plunger "
                "and harmon mutes and the soft-note brass law from lesson videos (measured only); tonguing and slurs "
                "from CC0 solo lines",
    "exams": "Blind picture rounds (spectrogram crops, the real recording against this voice, fresh judges, 28 pairs): "
             "the real one picked in 12, 13, 8 and 10 of 28 in rounds 7 to 10, at or under chance; the lip shake is "
             "caught every round and stays the weak spot. Blind ear round 1 (2026-10-24), five single-note pairs "
             "against a player: four not told apart, the plunger reversed (ours picked as the real one). Phrase "
             "round 1 caught; phrase round 2 (2026-10-25, the user, blind, on a phone speaker): every clip ticked "
             "'played', 'I cannot tell the difference at all'.",
    "built_in": "a winds song study, 2026-10-23/25; promoted as ledger:M331",
    "open": [
        "the shake: caught in every picture round",
        "the phrase pass was on a phone speaker",
        "the phrase settings copy one take each; an original solo is not yet judged",
        "a fall's air tail (0.3-6 kHz at 0.55 of the note's rms, about 0.8 s, no lane reaches it) is brighter "
        "above 4 kHz than the note it follows (about 5 dB on an f Bb5); a first original solo drew its falls on "
        "the bend lane instead",
        "lanes keep acting on a phrase's release: a lane that moves right after the last note (a level taper "
        "back to 0 dB, a bend back to 0) clicks or bends the tail; hold every lane at its last value for about "
        "1.2 s after each phrase",
    ],
}

DEFAULTS = dict(mute='open', bright=0.0, breath=0.15, vib=0.0, vib_rate=5.0, vib_delay=0.35, tongue=0.6,
                slur_ms=20.0, release=0.06, scoop_ms=180.0, fall_ms=250.0, doit_ms=500.0, shake_rate=8.0,
                flutter_rate=32.0, growl_interval=12.0, onset_cents=20.0, drift_cents=2.5, breathe_db=0.5,
                noise_db=-28.0, room=0.15, level=1.0, floor_db=-200.0, seed=7, jitter_cents=4.0,
                partial_wander=None, partial_wander_hz=None, h1_db=0.0,
                noise_wander=0.0, noise_wander_hz=3.0, air_lowcut=0.0,
                chiff_db=0.0)
INFO["params"] = {k: f"{v} (default {d if d is None or isinstance(d, str) else format(d, 'g')})"
                  for k, v, d in ((k, v, DEFAULTS[k]) for k, v in INFO["params"].items())}

# ---- tone, fitted to the Iowa MIS novib ff notes (the song study's fit): the spectral
# envelope in Hz per register (dB re the strongest harmonic) and each note's H1..H8 residual against it
ENV_HZ = np.array([140, 280, 525, 1050, 1575, 2100, 2625, 3150, 3675, 4200, 4725, 5250, 6300, 8000, 11000, 16000], float)
ENV_DB = np.array([-20.7, -20.7, -9.7, -0.9, -2.9, -6.9, -11.1, -15.8, -19.6, -24.6, -28.8, -31.9, -38.2, -49.0, -66.4, -74.2])
ENV_REG = {'low': np.array([-23.9, -19.9, -8.5, -1.8, -3.1, -9.0, -13.6, -18.7, -24.2, -29.1, -34.3, -38.2, -44.8, -53.7, -76.9, -76.9]),
           'mid': np.array([-20.7, -20.7, -9.7, -0.9, -2.9, -6.9, -11.1, -15.8, -19.6, -24.6, -28.8, -31.9, -38.2, -49.0, -66.4, -74.2]),
           'high': np.array([-10.5, -10.5, -10.5, 0.0, 0.0, -5.2, -7.4, -12.7, -16.7, -21.6, -23.8, -26.9, -33.0, -43.8, -57.4, -60.6])}
REG_MIDI = (55.0, 66.0, 77.0)
FINGER_DB = {52: [-1.0, -1.6, 0.3, -4.0, 0.6, 0.5, 1.9, 2.5], 53: [0.0, 0.7, -0.3, -2.3, 2.3, 1.8, 1.9, 2.0], 54: [0.6, 2.7, 0.3, -1.8, 0.2, 1.0, 2.4, 1.0], 55: [-0.9, 2.2, -1.0, -1.3, -0.1, 0.6, 2.6, 1.2], 56: [-0.6, 3.0, -0.8, 0.0, -0.9, 2.3, 1.6, 0.2], 57: [-1.5, 1.5, -1.9, 0.9, -0.3, 2.4, 0.8, 0.1], 58: [-3.0, 1.5, -3.6, -1.3, -0.2, 2.6, -0.6, 0.4], 59: [-0.7, 1.5, -0.9, -1.3, 2.1, -0.1, -1.0, -0.2], 60: [-2.8, -0.8, -1.3, 0.1, 2.3, 0.2, -0.4, -1.1], 61: [-1.3, -1.6, 1.0, 1.5, 2.0, 0.0, 2.5, 2.5], 62: [-1.2, -0.5, 1.5, 1.7, 2.1, 0.5, 2.4, 2.3], 63: [0.0, -1.2, -1.8, 1.9, 1.6, -0.3, 1.3, 0.5], 64: [-1.2, -3.1, -1.8, 2.1, -0.2, -1.1, 0.1, -0.7], 65: [2.1, -2.1, 0.5, 2.3, 1.3, 0.8, 1.1, 1.2], 66: [2.9, -1.5, 1.2, 1.1, 0.1, 1.1, 0.5, -0.4], 67: [2.8, 0.0, 1.3, 1.6, 1.8, 0.2, -0.7, 1.9], 68: [1.6, 0.2, 1.4, -0.4, -0.3, -0.9, -0.4, -1.2], 69: [2.4, -0.1, 1.5, 0.2, 0.4, -0.6, -1.3, -1.8], 70: [1.2, -1.2, 1.5, 1.6, 0.7, 0.5, -0.3, -1.9], 71: [2.1, -0.4, 1.4, 2.4, -0.1, -1.3, 1.1, -1.1], 72: [-0.4, -1.8, 1.3, -0.9, -1.8, 0.8, -0.6, -0.3], 73: [-1.6, 0.4, -0.6, 0.4, -0.9, -1.2, -2.9, -1.8], 74: [0.3, 0.4, 0.9, 0.3, 0.1, -0.0, -1.6, -0.8], 75: [0.1, 0.3, 0.9, -0.1, 0.5, -1.8, -4.0, -3.0], 76: [-3.6, 0.2, 1.0, -2.0, -1.7, -3.4, -1.2, 0.1], 77: [-5.5, 0.0, -2.9, -1.6, -1.6, -2.7, -3.0, -0.0], 78: [-2.2, 0.0, 0.4, -1.0, 0.3, 1.5, 2.2, 0.9], 79: [-1.5, 0.0, -0.7, 1.9, 2.0, 2.2, 4.5, 2.7], 80: [0.1, 1.0, 1.2, 1.8, 2.8, 3.5, 4.1, 4.7], 81: [1.5, 2.0, 1.4, 3.7, 4.5, 5.6, 5.3, 8.0], 82: [1.8, 2.8, 4.3, 3.9, 6.2, 7.2, 7.5, 8.8], 83: [0.2, 4.2, 2.5, 3.1, 6.8, 6.4, 6.4, 8.4], 84: [0.0, 0.7, 3.2, 3.3, 4.8, 3.9, 5.9, 8.9], 85: [0.0, 0.7, -1.0, 0.0, 1.7, 1.8, 2.3, 1.4], 86: [0.0, 0.4, -1.0, 1.7, 2.8, 3.3, 5.5, 1.3], 87: [0.0, -2.6, -2.9, -3.9, -5.4, -3.1, -2.5, -11.0]}
FINGER_SD = 3.5            # each note's own partial levels past the fitted H1..H8, dB sd (the real's partials
                           # 1-10 kHz sit 2.3-7.7 dB sd around a smooth fit, ours 1.5-2.5)
G1 = 1.2                   # the brass law: H1 follows the blowing in dB ...
BRASS_B = 0.85             # ... and each octave of harmonic number up follows it this much more steeply
BRASS_HZ = 4000.0          # ... up to this frequency; above it a soft note keeps the ff slope (Davis mf F4
                           # 3-10 kHz -28.5 dB/oct, Iowa ff F4 -29.8; the law run on to 11 kHz gave ours -36 and
                           # sank the top partials 10-16 dB under the real, into the breath noise: the 5 kHz ceiling)
                           # (Davis mf F4 vs Iowa ff F4: about -8.5 dB per octave of k; calibrated in the pairs)
ATT_FAST = 0.85            # attack: the fast rise reaches this share of the blowing ...
ATT_SETTLE_S = 0.10        # ... then the lips settle into the full note with this time constant (Iowa: H4+ late)
ONSET_DIP_C = -12.0        # after the blip, the note starts this flat and settles (Iowa C5: -6..-20 c, 100 ms)
ONSET_DIP_S = 0.045
PARTIAL_WANDER_DB = 1.3    # each partial's slow level wander from 2 kHz up, dB rms at ff (sax: 5; round 6: 2 -> 1.3
                           # with PW_SOFT, Iowa ff F4 tops 1.0 dB sd)
PARTIAL_WANDER_HZ = 10.0
PHASE_SPREAD = 0.25
NOISE_HZ = (500.0, 6000.0)
AIR_LAW = 1.0              # 1: air noise drops with the blowing as the harmonics near AIR_HZ do; 0: linear (round 2)
AIR_HZ = 2000.0
LIP_JIT_HZ = (3.0, 8.0)    # the lips' quick wander (round 3): knots at these rates, so the pitch and the blowing
LIP_JIT_DB = 0.1           # move at 2-10 Hz (lessons: 3.6-7.5 c common sd, most of it at 2-5 Hz, levels moving
                           # together, corr 0.3-0.6; Iowa ff 1.4 c). jitter_cents sets the pitch part, this the
                           # blowing part per cent of pitch jitter
TONE_AIR = 0.3             # air shaped by the tone. The partials times a slow noise (under TONE_AIR_HZ):
TONE_AIR_HZ = 200.0        # each partial gets a noise skirt at its own level, so the floor falls with the partials
                           # and rides their envelope (real floor 5-10 kHz -22..-30 dB/oct, the flat band noise
                           # gave ours -11, so ours' partials sank into it at 5-7 kHz; a picture judge's main tell)
TONE_AIR_REF = 10000.0     # the skirt is TONE_AIR at this frequency and falls TONE_AIR_TILT * 6 dB per octave
TONE_AIR_TILT = 3.0        # under it (real mf: the floor 48 dB under the partial at 1.4 kHz, 6-10 dB at 9-11 kHz)
TONE_AIR_LAW = 1.5         # the skirt grows this many dB per dB of softer blowing (Iowa ff F4: 4-8 kHz 36 dB over
                           # the floor, Davis mf F4 12)
BAND_AIR = 1.0             # share of the flat band noise (NOISE_HZ) kept beside it
BAND_TOP_HZ = 5500.0       # 0 off; else the band noise is low-passed here (order BAND_TOP_ORDER) and
BAND_TOP_ORDER = 4         # scaled by BAND_GAIN (Davis mf: the floor flat to 5.7 kHz, then 26 dB down by 11 kHz)
BAND_GAIN = 1.6
REL_DB_S = 60.0            # 0 off (the air stops in 'release' s); else at a note's end the blowing dies at
                           # this many dB per second, so by the brass law the upper partials go first (Iowa ff F4:
                           # partials under 1.5 kHz 20 dB down 180 ms after the last held frame, over 3 kHz 80 ms;
                           # Rufftips' harmon_stem E5 80 / 40 ms; ours with 'release' 0.06: 40 / 30 ms)
REL_TILT = 1.5             # in that release the partials over 1.5 kHz fall this much more per octave (the
                           # brass law alone gave lows / highs 60 / 40 ms; the real's highs go about twice as fast)
TOP_HZ0 = 1500.0           # the 'top' lane adds its dB per octave from here up (a player's own
                           # brightness at a given loudness: the lick's short notes 7-11 dB brighter at 2.5-10 kHz
                           # than the brass law gives at their level)
PW_SOFT = 0.66             # the partial wander grows this many dB per dB of softer blowing (velocity): the
                           # real tops waver more on soft notes (5-10 kHz partial level sd in 50 ms means: Iowa ff F4
                           # 1.0 dB, Davis mf F4 4.1, Johnson open Eb4 3.8; ours 1.6 / 2.7 / 2.2)
FLOOR_ORDER = 4            # room-tone low-pass order (Harnum's room: lows -36..-51 dB, 3-5 kHz under -60 re the
FLOOR_WHITE = 0.003        # the room floor's white part over its low-passed rumble (0.03 before: it filled the
                           # half-valve note's hole, Harnum 3.4-6 kHz -75 dB re 0.3-2 kHz, ours -54..-60)
                           # half-valve note; order 1 left ours at -35 there; round 5: order 2 still filled the
                           # half-valve's hole, 3-4.5 kHz -41 dB re 0.3-2 kHz, real -72; order 4: -51)

# techniques, each measured in the song study
SHAKE_GLIDE = 0.3          # share of each half-period spent crossing between partials (Bergeron: one 10 ms frame)
SHAKE_UP = 0.7             # share of each shake period on the upper partial (Bergeron: up about 0.10 s, the dips
                           # back to Bb4 about 0.04 s; 0.5 gave even halves)
SHAKE_VAR = 0.0            # 0..1 how much each shake cycle differs (up share, interval, crossing dip;
                           # picture judges: 'the dips are identical copies'). 0.3 / 0.12 tried, dist worse; not kept
SHAKE_DIP_DB = -1.0        # blowing dip while the lips cross between partials (an unpitched frame at each crossing)
SHAKE_CYC = 1.0            # 0 off; 1: each up-and-back drawn on its own as Bergeron's are (4 cycles, ours /
SHAKE_UP_SD = 0.08         # real): up share 0.53 sd 0.08 (ours 0.72 sd 0.01; SHAKE_UP is then SHAKE_CYC_UP), the
SHAKE_CYC_UP = 0.55        # upper partial sagging -0.15 st over the up, sd 0.16 (ours +0.13), the lower one swooping
SHAKE_SAG = -0.15          # 0.85 st under and back (ours 0.32), and the air between the partials 2-8 kHz rising on
SHAKE_SAG_SD = 0.16        # the upper partial (level vs up state corr 0.29, p90-p10 10 dB; ours 0.04, 3 dB)
SHAKE_SWOOP = 1.0          # 0.7 -> 1.0 and its sd 0.3 -> 0.6 (the cycles' lowest points: mean -0.47 ->
SHAKE_SWOOP_SD = 0.6       # -0.76 st, sd 0.10 -> 0.23; Bergeron -0.85, sd 0.33; 'two identical bends')
SHAKE_AIR_DB = 4.0         # (8 overshot: corr 0.88)
SHAKE_UP_DB = 0.0          # 0 off; else dB the partials over 1.2 kHz drop on the upper partial (shape
SHAKE_UP_DB_SD = 0.0       # SHAKE_UP_SHAPE), each cycle its own by this sd (share): Bergeron's upper note is darker
SHAKE_UP_SHAPE = [(1000, 0.0), (1500, 1.0), (3000, 1.0), (5000, 1.0)]
SHAKE_LO_JIT = 0.0         # 0 off; else st rms of a fast lip wobble on the lower partial (Bergeron's lower
SHAKE_LO_JIT_HZ = 30.0     # stretches are jagged, frame steps 0.4 st; ours a smooth dip 0.1 st)
SHAKE_AIR_WANDER = 4.0     # 0 off; else dB rms of the air's own wander through a shake (Bergeron's air
SHAKE_AIR_WANDER_HZ = 8.0  # between the partials follows the cycles at corr 0.29 with a 10 dB range; ours 0.72, 6.3)
SHAKE_JUMP = 0.0           # 0 off (one bent note, as before); else ms of a cross-fade between two notes, the
                           # lower and the upper partial, each with its own harmonics (Bergeron, 1 ms steps: the 2nd
                           # harmonic band of each note within 10 dB at once for 7 ms a crossing, the band half way
                           # between them 9.8 dB under; ours bent: 0 ms, -7.4 dB)
SHAKE_JUMP_BEND = 0.0      # share of the interval the incoming note starts across, settling as it fades in
SHAKE_JUMP_DIP = 0.0       # dB both notes dip half way through the cross-fade (Bergeron: -8.7 dB)
SHAKE_JUMP_AIR = 0.0       # dB the air rises half way through the cross-fade (a broken, noisy crossing)
SHAKE_PER_SD = 0.5         # 0 off; else each shake cycle its own length, log-normal sd (Bergeron, cycle to
                           # cycle: period cv 0.52, ours 0.09; up cv 0.59 / 0.17, down cv 0.88 / 0.13; mean kept).
                           # 0.5: 4-seed cv 0.11 -> 0.35, 0.20 -> 0.54, 0.16 -> 0.45; distance 8.21 -> 8.12 (seed 7 7.40 -> 6.72)
SHAKE_REACH = 0.7          # share of the way down to the lower partial each dip goes (1 = all the way, as
SHAKE_REACH_SD = 0.2       # before), each cycle its own by SHAKE_REACH_SD; the swoop under scales with it (Bergeron,
                           # per cycle: depth 2.24 sd 0.81 st of a 3.6 st interval, most dips short of Bb4; ours 4.2).
                           # With shake_rate 7 -> 8 and SHAKE_GHOST, 4 seeds: period 103 -> 100 ms (real 87; 86 at
                           # 0.6 / 0.25, which sank seed 7 to 1.1 st), period cv 0.35 -> 0.52 (0.52), depth 4.11 ->
                           # 2.56 st (2.24), its sd 0.66 -> 0.78 (0.81); quick distance mean 8.12 -> 7.65
SHAKE_GHOST = -3.0         # 0 off; else dB of the note being left, held on through each crossing while the
SHAKE_GHOST_MS = 6.0       # bent note moves, then dying with this time constant (the two notes overlap, Bergeron 7.3
                           # ms a crossing within 10 dB, ours 0.5-1); the left note's own pitch, its own harmonics.
                           # -3 dB, 6 ms, 4 seeds: overlap 0.7 -> 6.7 ms (7.3), corr top/low 0.52 -> 0.29 (0.12),
                           # on REACH 0.6: quick distance 7.46 -> 7.53 (-6 dB, 4 ms: overlap 3.6)
SHAKE_X_AIR = 0.0          # dB the air rises with the overlap at each crossing (broadband, a broken crossing);
                           # 6 dB on the ghost: gap-band noise 4.4 -> 4.9 dB (real 3.5, already there), distance up; off
SHAKE_TOP_SD = 0.0         # 0 off; else dB rms of the partials over SHAKE_TOP_HZ0 moving on their own in a
SHAKE_TOP_RATE = 9.0       # shake, a wander at about SHAKE_TOP_RATE knots a second not tied to the cycles (Bergeron
SHAKE_TOP_HZ0 = 2500.0     # 3-8 kHz vs 0.4-1.5 kHz level inside a cycle: corr 0.12 sd 0.31, ours 0.69 sd 0.17; the
                           # top's swing re the lows' 0.6 sd 2.4 dB, ours 5.4 sd 1.1), full from 1.4 x SHAKE_TOP_HZ0
SCOOP_FAST = 0.6           # scoop: this share of the distance goes with a 30 ms time constant, the rest with
SCOOP_AIR = 0.0            # extra air at a scoop's start (x the breath), dying with SCOOP_FAST_S: the lips
                           # are not yet buzzing (Harnum: 17 ms from the onset to the first pitched frame, ours 2)
SCOOP_FAST_S = 0.03        # scoop_ms (Harnum: 3.5-4 st, half gone in 30 ms, the last semitone over 0.2-0.5 s)
FALL_SHAPE = 1.2           # fall: pitch drop ~ x ** FALL_SHAPE over the fall time (Harnum: 14 st in 0.22 s, near even)
FALL_FADE_DB = -6.0        # fall: blowing fade over the fall (Harnum: the level stays within 1-10 dB of the note
                           # down 14 st, then goes unpitched; -30 had ours 40 dB down by 11 st)
DOIT_SHAPE = 1.0           # doit: near even rise (Harnum: 16 st in 0.5-0.6 s) ...
DOIT_FADE_DB = -12.0       # ... and its level only dips
DOIT_SLOT = 0.8            # 0..1 pull of the climb into steps at the partials above the note (Harnum: the
DOIT_N0 = 4.0              # climb goes 68.1-71.8, 76.0-76.6, 80.7-83.1, a step share of 0.68 where an even climb
DOIT_VAR = 0.3             # 0..1 how much each doit step differs in time and width ('identical steps'; r7 0.1 -> 0.3)
DOIT_STEP_W = 0.35         # reads 0.25); the note taken as partial DOIT_N0, each jump this share of a slot wide
DOIT_SLOT_SD = 0.6         # sd of each step's own pull (DOIT_SLOT + this * N, 0..1: some steps jump, some
                           # glide; Harnum: jump sizes cv 0.90, holds cv 0.73; ours 0.25 / 0.29)
DOIT_AIR = 1.0             # extra breath (x the breath) through the doit's climb, rising over its first
DOIT_AIR_RISE = 0.3        # DOIT_AIR_RISE share (Harnum: the floor 2-8 kHz re the partials -21 dB on the note,
                           # -6..-17 through the climb; ours -28, with -9 only at each jump)
FALL_AIR = 0.55            # after a fall the lips stop but the air goes on: unpitched breath this share of
FALL_AIR_S = 0.15          # the note's rms, rising through the fall and dying with this time constant (Harnum: the
FALL_AIR_LO = 300.0        # 120 ms after the last pitched frame sit 6 dB under the note; ours were 54 dB under;
FALL_AIR_HI = 6000.0       # the band of that air (real: mid within 3-10 dB of the note, the top 24 dB under)
FALL_AIR_ORDER = 2         # the tail air's band order (the real tail is dark over 3 kHz, picture judges: ours
                           # a flat flood to 10 kHz). Tried 2500 / 3500 Hz at order 4: tail tilt -1 to -21 / -14 dB
                           # (real -34) but dist 12.24 to 13.22 / 13.78; not kept
FALL_AIR_RAMP = 1.0        # the air rises through the fall as x ** FALL_AIR_RAMP
FALL_SLOT = 1.0            # 0..1 pull of the fall into the partials under the note (Harnum's fall from F5:
FALL_N0 = 6.0              # 77.0-76.0, break, 73.7-71.7, break, 70.3-68.2, break, 64.7-63.4: the open partials 6, 5,
FALL_LIP = 0.55            # 4, 3, each lipped down about FALL_LIP of the way to the next before the lips break to it,
FALL_BREAK_W = 0.15        # the break this share of a slot, the blowing dipping FALL_BREAK_DB through it)
FALL_BREAK_DB = -8.0
FALL_AIR_TONE = 0.0        # 0..1 share of the air DURING the fall that is the falling tone times a slow
                           # noise (each partial its own skirt, as TONE_AIR) instead of the flat band (Harnum: floor
                           # beside the partials 3-9 kHz re the partials -23 dB through the fall, ours -6); the
                           # flat band air (BAND_AIR) also gives way by this share through the fall
FALL_TONE_S = 0.0          # 0 off; else after a fall the lips go on buzzing, dying with this time constant
FALL_TONE_ST = 0.0         # and sliding this many semitones further down (Harnum: after the last pitched frame
FALL_TONE_C = 0.0          # -8..-15 dB for 0.1 s, -36 by 0.3 s, unpitched: a rough pitch of FALL_TONE_C cents rms)
FLUTTER_DEPTH = 0.27       # tongue flap depth (Shook, Davis: envelope peak-to-peak 0.5-0.9 of the mean in
FLUTTER_LAW = 0.0          # share of the flap taken through the blowing (1: round 2, the brass law made 1-5 kHz
                           # dip 0.41-0.50 deep vs Davis 0.12-0.21); 0: one gain on the whole tone, all bands alike
FLUTTER_NOISE_DEPTH = 0.45 # share of the flutter air that pulses with each flap (1: all, the regular
                           # stripes 4-10 kHz main caught, autocorrelation 0.80 vs Davis 0.20)
FLUTTER_NOISE = 1.0        # 1-3 kHz, but the 10 ms level stays flat); air burst at each flap (H over floor -9 dB)
FLUT_IV_SD = 0.35          # 0 off; else each flap its own length (log sd) and depth (FLUT_DEPTH_SD): Davis's
FLUT_DEPTH_SD = 0.3        # flap intervals cv 0.39 (ours 0.10, 'evenly spaced vertical streaks'); band depths
                           # (p90-p10 of the envelope re its mean) 0.28 / 0.37 / 0.93 / 0.61 in 0.3-1 / 1-3 / 3-6 /
                           # 6-10 kHz, ours 0.45 / 0.53 / 0.96 / 1.35 (FLUTTER_DEPTH and FLUTTER_NOISE_DEPTH set those)
GROWL_AM = 0.08            # growl: the sung tone's beat (Davis: no clear beat line, mostly roughness between
GROWL_PM_US = 8.0          # the harmonics, H2-H5 over the floor 5-6 dB lower; the colour within +-4 dB)
GROWL_RASP = 0.005
# half-valve at 1 (Harnum, two notes vs his plain notes a tone away, same recording): 16 dB down, choked above
# 1 kHz, nothing harmonic above 4 kHz (the air carries it), pitch unsteady (sd 13-21 c)
HALF_DB = [(250, -16.4), (315, -16.4), (397, -16.4), (500, -16.4), (630, -16.4), (794, -17.8), (1000, -21.9), (1260, -31.1), (1587, -36.6), (2000, -36.4), (2520, -46.0), (3175, -57.5), (4000, -59.9), (5040, -60.0), (6350, -60.0), (8000, -60.0), (10079, -60.0)]
HALF_TOP = 0.0             # 0..1 of the measured top (the half-open valve rings at
HALF_DB_TOP = [(4000, -59.9), (5040, -55.8), (6350, -41.6), (8000, -25.1), (10079, -18.9)]  # 6-10 kHz: real hiss
                           # 7-10 kHz 29 dB under 0.3-2 kHz, ours 52; HALF_DB held it at -60)
HALF_AIR = 0.0             # extra air at halfvalve 1 (none: the real's top is clean to -85 dB; the air is choked too)
HALF_HOLE = 0.0            # dB added to HALF_DB at 2.5-6.3 kHz (full at 3.2-5 kHz): Harnum's H8-H13 sit
                           # 76-87 dB under his strongest, ours 53-59
HALF_AIR_ORDER = 4         # the choked air's low-pass order (4: the real's hole at 3-7 kHz, 46 dB under 0.3-2 kHz)
HALF_HISS = 1.0            # the half-open valve's hiss, 6-12 kHz, riding the air (Harnum: 7-10 kHz 29 dB
HALF_HISS_HZ = (6000.0, 12000.0)  # under 0.3-2 kHz while his quiet room sits 40 dB under there)
HALF_STEPS = 1.0           # 0 off; else the half-open valve moves in steps (about every HALF_STEP_S s, up to
HALF_STEP_S = 0.22         # HALF_STEP_OCT octaves each): the choke curve (HALF_DB) and the hiss edge move with it,
HALF_STEP_OCT = 0.25       # the hiss then a steep high band from HALF_EDGE_HZ (Harnum: the hole 3-7 kHz over a hiss
HALF_EDGE_HZ = 7000.0      # whose edge steps 4.6, 5.2, 6.5, 7.0 kHz at 0.18 / 0.25 / 0.46 s, the partials changing with
HALF_EDGE_ORDER = 8        # it; edge sd 0.79 kHz, 100 ms steps 0.44 kHz; ours a steady band: 0)
HALF_AIR_LP = 1200.0       # at halfvalve the air is low-passed here and drops with the tone
HALF_WOBBLE = 5.0          # blowing and pitch wander multiplied up to this at halfvalve 1 (real: level sd 2.5-3.5 dB,
                           # pitch sd 13-21 c)
HALF_CLIFF = 0.0           # 0 off; else dB the partials drop over HALF_CLIFF_HZ (moved by the valve's steps):
HALF_CLIFF_HZ = 2100.0     # Harnum's hole (under -40 dB re 0.3-2 kHz, 23 ms frames) starts at 2.0 kHz (sd 0.41, 50 ms
                           # steps 0.25 kHz) and bottoms at -76 dB; ours at 2.35 kHz (sd 0.25, steps 0.14), -62 dB,
                           # broken by our partials 2.3-3.5 kHz (Harnum H7 2.7 kHz -78 dB re the strongest, ours -51)
HALF_RES = 0.0             # 0 off; else dB of a resonance just under the cliff (Harnum H5 1.9 kHz -15, ours -33)
HALF_STEP_C = 45.0         # 0 off; else cents the pitch moves per unit of the valve's step position (the
                           # steps of HALF_STEPS, about +-1.5 units): Harnum's pitch steps with the valve
HALF_LEVEL_DB = 0.0        # the drop is in HALF_DB (absolute, measured)
# plunger (Johnson Eb4, same recording, absolute): closed = lows and mids 10-19 dB down, a buzz resonance
# +10..+16 dB at 3-5 kHz; half = a low-pass from 800 Hz, -20 dB at 2 kHz
PLUNGER_CLOSED_DB = [(250, -7.0), (315, -8.2), (397, -11.7), (500, -16.5), (630, -19.0), (794, -17.1), (1000, -14.7), (1260, -11.9), (1587, -9.3), (2000, -12.3), (2520, -6.6), (3175, 10.5), (4000, 16.5), (5040, 11.6), (6350, 7.1), (8000, 3.1), (10079, 1.3)]
PLUNGER_FINE = 1.0         # 0..1 share of PLUNGER_FINE_DB added to the closed plunger: Johnson's closed Eb4
# (171.9-173.3 s) partial by partial against ours (the 1/3-octave curve was too smooth: formant regions irregularity
# 7.7 dB real vs 5.1; peaks at 3.2, 6.0-6.3, 8.2 and 9.5-10 kHz, holes at 1.3, 3.8 and 6.6 kHz)
PLUNGER_FINE_DB = [(316, 0.0), (631, -2.6), (947, -8.6), (1263, -22.7), (1578, -13.7), (1894, -4.1), (2210, -6.9), (2526, -6.9), (2841, -1.2), (3157, 5.1), (3473, -1.2), (3788, -15.5), (4104, -10.5), (4420, -6.7), (4736, -13.0), (5051, -10.6), (5367, -17.9), (5683, -8.4), (5998, 9.6), (6314, 6.7), (6630, -18.7), (6945, -13.0), (7261, -1.6), (7577, -10.4), (7892, -9.1), (8208, 4.1), (8524, 0.6), (8840, -2.5), (9155, -0.1), (9471, 5.5), (9787, 10.0), (10102, 5.2)]
PLUNGER_HALF_DB = [(250, -1.3), (315, -0.9), (397, 0.2), (500, 1.6), (630, 1.0), (794, -3.6), (1000, -8.1), (1260, -8.8), (1587, -13.2), (2000, -19.9), (2520, -16.8), (3175, -9.6), (4000, -8.2), (5040, -9.0), (6350, -7.7), (8000, -6.4), (10079, -8.2)]
# harmon (Rufftips) against the Iowa note at the same pitch, both re their strongest (cross-recording: the lesson is
# close-miked and bright, so the 4-10 kHz lift is partly the mic). Level set so an F4-F5 note loses about 10 dB
# (stem out) and 8 dB (stem in) of power against the open horn (assumed, not measured).
# stem out: the lows 25-55 dB under a 4-10 kHz peak (thin, buzzy); stem in: a 1.3-1.6 kHz nasal peak over the lows
MUTE_DB = {'open': [(100, 0.0), (16000, 0.0)],
           'harmon': [(250, -3.9), (315, -6.5), (397, -12.6), (500, -17.7), (630, -22.5), (794, -29.9), (1000, -36.3), (1260, -37.0), (1587, -31.3), (2000, -21.8), (2520, -11.3), (3175, -2.2), (4000, 6.0), (5040, 13.3), (6350, 17.4), (8000, 19.0), (10079, 19.6)],
           'harmon_stem': [(250, -23.5), (315, -23.0), (397, -21.6), (500, -19.7), (630, -17.4), (794, -15.4), (1000, -12.3), (1260, -7.5), (1587, -6.5), (2000, -8.2), (2520, -10.0), (3175, -8.7), (4000, -3.0), (5040, 0.4), (6350, 5.0), (8000, 11.6), (10079, 15.3)]}


def _rng(seed, ms, salt):
    return np.random.default_rng([int(seed) & 0xFFFFFFFF, int(ms) + (1 << 40), int(salt) & 0xFFFF])


def _song_ms(st, beat0, bpm):
    return int(round((st + beat0 * 60.0 / bpm) * 1000))


def _normal(rng, size, n_draw=None):
    """rng.normal(0, 1, size), but only n_draw values come from rng (the rest from a child stream), so a phrase made
    longer by a release tail draws the same values as before and leaves the later draws of rng where they were"""
    if n_draw is None or size <= n_draw:
        return rng.normal(0, 1, size)
    x = rng.normal(0, 1, n_draw)
    return np.concatenate([x, np.random.default_rng([int(n_draw), int(size), 977]).normal(0, 1, size - n_draw)])


def _wander(n_ctrl, cr, rate_hz, rng, n_draw=None):
    """smooth random curve (zero mean, unit rms-ish) with knots about every 1/rate_hz s."""
    step = max(2, int(cr / rate_hz))
    knots = _normal(rng, n_ctrl // step + 3, None if n_draw is None else n_draw // step + 3)
    x = np.arange(n_ctrl) / step
    i = np.floor(x).astype(int)
    f = x - i
    f = f * f * (3 - 2 * f)
    return knots[i] * (1 - f) + knots[i + 1] * f


def _smooth(f):
    f = np.clip(f, 0, 1)
    return f * f * (3 - 2 * f)


def _curve(points, fk):
    """dB at frequencies fk from (Hz, dB) points, linear in log frequency, held flat outside."""
    h = np.log2([p[0] for p in points])
    d = np.array([p[1] for p in points], float)
    return np.interp(np.log2(np.maximum(fk, 20.0)), h, d)


def _half_db(fk):
    """the half-valve filter: HALF_DB, its top above 4 kHz blended toward HALF_DB_TOP by HALF_TOP"""
    d = _curve(HALF_DB, fk)
    if HALF_HOLE != 0:
        d = d + HALF_HOLE * _curve([(2000, 0.0), (3175, 1.0), (5040, 1.0), (8000, 0.0)], fk)
    if HALF_CLIFF != 0:                                # a cliff over the valve's moving edge (fk comes in
        e = HALF_CLIFF_HZ                              # shifted by the valve position, so the edge steps with it)
        d = d - HALF_CLIFF * _curve([(e, 0.0), (e * 2 ** 0.25, 1.0), (9000, 1.0), (12000, 0.0)], fk)
    if HALF_RES != 0:                                  # the last open resonance just under that edge
        e = HALF_CLIFF_HZ * 2 ** -0.2
        d = d + HALF_RES * _curve([(e * 2 ** -0.3, 0.0), (e, 1.0), (e * 2 ** 0.2, 0.0)], fk)
    if HALF_TOP > 0:
        d = np.where(fk > 4000, (1 - HALF_TOP) * d + HALF_TOP * _curve(HALF_DB_TOP, fk), d)
    return d


def _env_db(fk, midi):
    """the bell-and-bore envelope at fk for a note of pitch midi (register-interpolated when fitted)."""
    lf = np.log2(np.maximum(fk, 20.0))
    hz = np.log2(ENV_HZ)
    if ENV_REG is None:
        return np.interp(lf, hz, ENV_DB)
    lo, mi, hi = (np.interp(lf, hz, ENV_REG[r]) for r in ('low', 'mid', 'high'))
    a = np.clip((midi - REG_MIDI[0]) / (REG_MIDI[1] - REG_MIDI[0]), 0, 1)
    b = np.clip((midi - REG_MIDI[1]) / (REG_MIDI[2] - REG_MIDI[1]), 0, 1)
    return np.where(midi < REG_MIDI[1], lo * (1 - a) + mi * a, mi * (1 - b) + hi * b)


def _finger(midi, k):
    m = int(round(midi))
    if m in FINGER_DB and k <= len(FINGER_DB[m]):
        return FINGER_DB[m][k - 1]
    if k == 1:
        return 0.0
    r = np.random.default_rng([31337, m]).normal(0, 1, 128)
    w = np.clip((np.arange(1, 129) - 3) / 6.0, 0.1, 1.0)
    return float(np.clip(FINGER_SD * r[k - 1] * w[k - 1], -2 * FINGER_SD, 2 * FINGER_SD))


def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, **params):
    P = dict(DEFAULTS)
    P.update({k: v for k, v in params.items() if v is not None and k in DEFAULTS})
    lanes = lanes or {}
    out = np.zeros(total_n)
    if not notes:
        return np.zeros((2, total_n))
    CR = 64
    seed = int(P['seed'])
    mute = MUTE_DB.get(str(P['mute']), MUTE_DB['open'])

    def lane_at(name, i, default):
        c = lanes.get(name)
        if c is None or len(c) == 0:
            return float(default)
        return float(c[int(np.clip(i, 0, len(c) - 1))])

    ns = sorted([(float(n[0]), float(n[1]), float(n[2]), float(n[3]) / 127.0) for n in notes], key=lambda z: (z[0], -z[1]))
    phrases = []
    for st, m, d, v in ns:
        if d <= 0:
            continue
        if phrases and st < phrases[-1][-1][0] + phrases[-1][-1][2] - 1e-3 and st > phrases[-1][-1][0] + 1e-3:
            phrases[-1].append((st, m, d, v))
        else:
            phrases.append([(st, m, d, v)])

    rel = float(P['release'])
    for ph in phrases:
        p0 = ph[0][0]
        p_end = ph[-1][0] + ph[-1][2]
        a = int(round(p0 * sr))
        # a fall's air tail needs room after the release (only then: a longer phrase draws its wanders anew)
        tail_s = 4 * FALL_AIR_S if FALL_AIR > 0 and lane_at('fall', int(round(p_end * sr)) - 1, 0.0) > 0 else 0.0
        rel_s = 50.0 / REL_DB_S if REL_DB_S > 0 else rel  # the tapered release runs to -50 dB of blowing
        b = int(round((p_end + rel_s + 0.03 + tail_s) * sr))
        if b <= 0 or a >= total_n:
            continue
        n = b - a
        nc = n // CR + 2
        n_dr = int(round((p_end + rel + 0.03 + tail_s) * sr)) - a     # the draws as without the tapered release
        nc_dr = n_dr // CR + 2

        def _wd(n_ctrl, cr, rate_hz, rng):
            return _wander(n_ctrl, cr, rate_hz, rng, nc_dr)
        tc = (np.arange(nc) * CR) / sr
        crate = sr / CR
        ms0 = _song_ms(p0, beat0, bpm)
        prng = _rng(seed, ms0, 1)

        midi = np.zeros(nc)
        vel = np.zeros(nc)
        amp_env = np.zeros(nc)
        note_age = np.zeros(nc)
        blow_db = np.zeros(nc)                             # dB of blowing from slurs, falls, doits, scoops
        bend_extra = np.zeros(nc)
        chiff = []
        fall_tail = None
        fall_rough = None
        air_boost = np.zeros(nc)                           # extra air share at a scoop's start
        fall_bx = np.zeros(nc)                             # 0..1 progress through a fall (FALL_AIR_TONE)
        rel_db = np.zeros(nc)                              # dB of the tapered release (0 while held)
        att_t = 0.03
        for j, (st, m, d, v) in enumerate(ph):
            i0 = int(round((st - p0) * sr / CR))
            i1 = max(int(round((st + d - p0) * sr / CR)), i0 + 1)
            nrng = _rng(seed, _song_ms(st, beat0, bpm), 2)
            midi[i0:] = m + nrng.normal(0, 0.04)
            vel[i0:] = v
            note_age[i0:] = tc[i0:] - tc[i0]
            s_glob = a + i0 * CR
            e_glob = a + i1 * CR
            t_rel = tc[i0:] - tc[i0]
            if j == 0:
                tg = float(np.clip(lane_at('tongue', s_glob, P['tongue']), 0, 1))
                # attack time: hard tongue at ff about 15 ms to the fast plateau, a breath attack about 60 ms
                att_t = (0.015 + 0.045 * (1 - tg)) * (1.4 - 0.5 * v) * np.exp(nrng.normal(0, 0.15))
                chiff.append((i0, tg * (0.3 + 0.7 * v)))
                oc = float(P['onset_cents']) * (0.4 + tg) / 100.0
                bend_extra[i0:] += oc * np.exp(-t_rel / 0.008) + ONSET_DIP_C / 100.0 * (0.5 + 0.5 * tg) * \
                    (1 - np.exp(-t_rel / 0.006)) * np.exp(-t_rel / ONSET_DIP_S)
            else:
                pm = ph[j - 1][1]
                sl = max(0.008, float(P['slur_ms']) / 1000 * (1 + 0.25 * nrng.normal()))
                x = t_rel / sl
                bend_extra[i0:] += (pm - m) * (1 - _smooth(x))
                blow_db[i0:] += -3.0 * np.exp(-((t_rel - sl * 0.5) / (sl * 0.7)) ** 2)
            sc = lane_at('scoop', s_glob, 0.0)
            if sc > 0:
                stime = float(P['scoop_ms']) / 1000 * (0.6 + 0.4 * min(sc, 4) / 4) * np.exp(nrng.normal(0, 0.12))
                curve = SCOOP_FAST * np.exp(-t_rel / SCOOP_FAST_S) + (1 - SCOOP_FAST) * np.exp(-t_rel / stime)
                curve *= 1 - _smooth(t_rel / (4 * stime))
                bend_extra[i0:] += -sc * curve
                blow_db[i0:] += -4.0 * curve
                air_boost[i0:] += SCOOP_AIR * min(sc, 4) / 4 * np.exp(-t_rel / SCOOP_FAST_S)
            fl = lane_at('fall', e_glob - 1, 0.0)
            dt = lane_at('doit', e_glob - 1, 0.0)
            for amt, sign, key, shp, fade in ((fl, -1.0, 'fall_ms', FALL_SHAPE, FALL_FADE_DB),
                                              (dt, 1.0, 'doit_ms', DOIT_SHAPE, DOIT_FADE_DB)):
                if amt <= 0:
                    continue
                ftime = float(P[key]) / 1000 * (0.4 + 0.6 * amt / 12.0) ** 0.7 * np.exp(nrng.normal(0, 0.1))
                ftime = min(ftime, (i1 - i0) * CR / sr * 0.85)
                f0i = i1 - int(ftime * sr / CR)
                x = np.clip((tc[f0i:i1] - tc[f0i]) / max(ftime, 1e-3), 0, 1)
                shape = x ** shp
                if sign > 0 and DOIT_SLOT > 0:
                    # the lips jump across the fingering's partials: the even climb is pulled into steps at the
                    # harmonic series above the note (taken as partial DOIT_N0)
                    slots = 12 * np.log2(np.arange(DOIT_N0, DOIT_N0 + 40) / DOIT_N0)
                    v = amt * shape
                    ia = np.clip(np.searchsorted(slots, v, side='right') - 1, 0, len(slots) - 2)
                    sa, sb = slots[ia], slots[ia + 1]
                    u = np.clip((v - sa) / (sb - sa), 0, 1)
                    pull = DOIT_SLOT
                    if DOIT_SLOT_SD > 0:                   # each step its own pull (jump or glide)
                        pull = np.clip(DOIT_SLOT + DOIT_SLOT_SD * _rng(seed, _song_ms(st, beat0, bpm), 20).normal(
                            0, 1, len(slots))[ia], 0, 1)
                    if DOIT_VAR > 0:                       # each step its own time and width
                        dv = nrng.normal(0, 1, (2, len(slots)))
                        c0 = np.clip(0.5 + DOIT_VAR * dv[0][ia], 0.25, 0.75)
                        w_s = DOIT_STEP_W * np.clip(1 + 2 * DOIT_VAR * dv[1][ia], 0.4, 2.0)
                        w_s = np.minimum(w_s, 2 * np.minimum(c0, 1 - c0))
                        us = _smooth((u - c0) / w_s + 0.5)
                    else:
                        us = _smooth((u - 0.5) / DOIT_STEP_W + 0.5)
                    shape = ((1 - pull) * v + pull * (sa + us * (sb - sa))) / max(amt, 1e-6)
                if sign > 0 and DOIT_AIR > 0:              # breath through the climb
                    air_boost[f0i:i1] += DOIT_AIR * _smooth(x / DOIT_AIR_RISE)
                    if j == len(ph) - 1:
                        air_boost[i1:] += DOIT_AIR
                if sign < 0 and FALL_SLOT > 0:
                    # the lips drop through the partials under the note (taken as partial FALL_N0), lipping
                    # each one down FALL_LIP of the way, then breaking to the next with a dip in the blowing
                    ks = np.arange(FALL_N0, 0.5, -1.0)
                    slots = -12 * np.log2(ks / FALL_N0)               # 0, 3.2, 7.0, 12.0, ... semitones down
                    v = amt * shape
                    ia = np.clip(np.searchsorted(slots, v, side='right') - 1, 0, len(slots) - 2)
                    sa, sb = slots[ia], slots[ia + 1]
                    u = np.clip((v - sa) / (sb - sa), 0, 1)
                    w = FALL_BREAK_W
                    us = np.where(u < 1 - w, FALL_LIP * u / (1 - w), FALL_LIP + (1 - FALL_LIP) * _smooth((u - 1 + w) / w))
                    shape = ((1 - FALL_SLOT) * v + FALL_SLOT * (sa + us * (sb - sa))) / max(amt, 1e-6)
                    brk = np.exp(-((u - 1 + w / 2) / (w * 0.6)) ** 2) * (v < slots[-1])
                    blow_db[f0i:i1] += FALL_BREAK_DB * FALL_SLOT * brk
                if sign < 0 and FALL_AIR_TONE > 0:         # the flat band air gives way through the fall
                    fall_bx[f0i:i1] = np.maximum(fall_bx[f0i:i1], x)
                    if j == len(ph) - 1:
                        fall_bx[i1:] = 1.0
                if sign < 0 and FALL_AIR > 0 and j == len(ph) - 1:
                    fall_tail = (f0i, i1)
                bend_extra[f0i:i1] += sign * amt * shape
                fdb = fade * x ** 2.0
                blow_db[f0i:i1] += fdb
                if j == len(ph) - 1 and len(x):
                    bend_extra[i1:] += sign * amt * shape[-1]
                    blow_db[i1:] += fdb[-1]
            if j == len(ph) - 1:
                ir = max(1, int(rel * sr / CR))
                tail = np.arange(nc - i1)
                amp_env[i1:] = np.clip(1 - tail / ir, 0, 1) ** 1.5
                if REL_DB_S > 0 and fl <= 0 and dt <= 0:  # the blowing dies in dB, the top partials first (a fall
                    # or a doit keeps its own end)
                    tt = tail * CR / sr
                    amp_env[i1:] = 10 ** (-REL_DB_S * tt / 20) * np.clip(1 - tt / rel_s, 0, 1)
                    rel_db[i1:] = 20 * np.log10(np.maximum(amp_env[i1:], 1e-4))
                if fl > 0 and FALL_TONE_S > 0:            # the lips go on buzzing after the fall
                    tt = tail * CR / sr
                    amp_env[i1:] = np.exp(-tt / FALL_TONE_S) * np.clip(1 - tt / (5 * FALL_TONE_S), 0, 1)
                    bend_extra[i1:] += -FALL_TONE_ST * (1 - np.exp(-tt / FALL_TONE_S))
                    fall_rough = (i1, tt)
        # the phrase's air: a fast rise to ATT_FAST of the blowing, then the lips settle into the note
        held = int(round((p_end - p0) * sr / CR)) + 1
        amp_env[:held] = 1.0
        rise = _smooth(tc / max(att_t, 1e-3))
        settle = ATT_FAST + (1 - ATT_FAST) * (1 - np.exp(-tc / ATT_SETTLE_S))
        amp_env *= rise * settle
        vel = np.maximum(vel, 1e-3)

        gidx_c = np.clip(a + np.arange(nc) * CR, 0, total_n - 1)

        def lc(name, default):
            c = lanes.get(name)
            if c is None or len(c) == 0:
                return np.full(nc, float(default))
            c = np.asarray(c, dtype=np.float64)
            return c[np.clip(gidx_c, 0, len(c) - 1)]

        bend = lc('bend', 0.0)
        vibd = lc('vib', P['vib'])
        lvl = lc('level', 0.0)
        air = np.clip(lc('breath', P['breath']), 0, 1) * (1 + air_boost)
        shake = np.maximum(lc('shake', 0.0), 0)
        flut = np.clip(lc('flutter', 0.0), 0, 1)
        growl = np.clip(lc('growl', 0.0), 0, 1)
        half = np.clip(lc('halfvalve', 0.0), 0, 1)
        plg = np.clip(lc('plunger', 0.0), 0, 1)
        hv_oct = np.zeros(nc)                              # the half-open valve's position, octaves
        if HALF_STEPS > 0 and half.max() > 0:
            hrng = _rng(seed, ms0, 14)
            i, pos = 0, hrng.uniform(-0.5, 0.5)
            while i < nc:
                L_ = max(int(0.08 * crate), int(hrng.exponential(HALF_STEP_S) * crate))
                hv_oct[i:i + L_] = pos
                pos = float(np.clip(pos + hrng.choice([-1, 1]) * hrng.uniform(0.3, 1.0), -1.5, 1.5))
                i += L_
            k_ = max(1, int(0.015 * crate))
            hv_oct = np.convolve(np.pad(hv_oct, k_, mode='edge'), np.ones(2 * k_ + 1) / (2 * k_ + 1), 'same')[k_:-k_]
            hv_oct = HALF_STEPS * HALF_STEP_OCT * hv_oct

        # ---- pitch
        vr = float(P['vib_rate']) * (1 + 0.07 * _wd(nc, crate, 1.2, prng))
        vph = 2 * np.pi * np.cumsum(vr / crate) + prng.uniform(0, 2 * np.pi)
        vdepth = vibd * (1 + 0.25 * _wd(nc, crate, 0.8, prng))
        vgate = _smooth((note_age - float(P['vib_delay'])) / 0.3)
        vib_c = vdepth * vgate * np.sin(vph) / 100.0
        drift = float(P['drift_cents']) / 100.0 * _wd(nc, crate, 0.7, prng) * (1 + HALF_WOBBLE * half)
        hi = np.clip((midi - 84) / 6.0, 0, 1)                 # above C6: less stable
        drift = drift * (1 + 2.0 * hi)
        # the lips' quick wander: one curve for the pitch and (partly) the blowing, 2-10 Hz
        jrng = _rng(seed, ms0, 9)
        jit_c = lc('jitter', P['jitter_cents'])
        if np.max(jit_c) > 0:
            jw = (0.6 * _wd(nc, crate, LIP_JIT_HZ[0], jrng) + 0.8 * _wd(nc, crate, LIP_JIT_HZ[1], jrng)) / 0.8
            jw2 = _wd(nc, crate, LIP_JIT_HZ[1], jrng) / 0.8
            drift = drift + jit_c / 100.0 * jw
            jit_db = LIP_JIT_DB * jit_c * (0.7 * jw + 0.7 * jw2)
        else:
            jit_db = 0.0
        # shake: the lips flip to the upper partial and back; the rate wanders, the crossings dip the air
        srng = _rng(seed, ms0, 6)
        shk_dark = None
        shk_top = None
        shk_jump = None
        shk_ghost = None
        if shake.max() > 0:
            sr_hz = float(P['shake_rate']) * (1 + 0.12 * _wd(nc, crate, 2.0, srng))
            sph = np.cumsum(sr_hz / crate) + srng.uniform(0, 0.2)
            if SHAKE_PER_SD > 0:                           # each cycle its own length
                c0 = np.floor(sph).astype(int)
                c0 -= c0.min()
                pm = np.exp(SHAKE_PER_SD * _rng(seed, ms0, 26).normal(0, 1, c0.max() + 4) - 0.5 * SHAKE_PER_SD ** 2)
                sph = np.cumsum(sr_hz / pm[c0] / crate) + (sph[0] - sr_hz[0] / crate)
            frac = sph - np.floor(sph)                     # 0..1 per up-and-back
            g = SHAKE_GLIDE / 2
            if SHAKE_VAR > 0:                              # each up-and-back drawn on its own
                cyc = np.floor(sph).astype(int)
                cyc -= cyc.min()
                cv = srng.normal(0, 1, (3, cyc.max() + 2))
                s_up = np.clip(SHAKE_UP * (1 + 0.5 * SHAKE_VAR * cv[0][cyc]), 0.4, 0.85)
                s_amt = 1 + SHAKE_VAR * np.clip(cv[1][cyc], -2, 2)
                s_dip = np.clip(1 + 2 * SHAKE_VAR * cv[2][cyc], 0.2, 2.5)
            else:
                s_up, s_amt, s_dip = SHAKE_UP, 1.0, 1.0
            if SHAKE_CYC > 0:                              # each cycle its own share, sag and swoop
                cyc = np.floor(sph).astype(int)
                cyc -= cyc.min()
                cv = _rng(seed, ms0, 15).normal(0, 1, (cyc.max() + 2, 3)).T
                s_up = np.clip(SHAKE_CYC_UP + SHAKE_UP_SD * cv[0][cyc], 0.35, 0.8)
            up = _smooth(frac / g) * (1 - _smooth((frac - s_up) / g))
            sh_c = shake * up * s_amt
            if SHAKE_CYC > 0:
                u_up = np.clip((frac - g) / np.maximum(s_up - g, 1e-3), 0, 1)
                u_lo = np.clip((frac - s_up) / np.maximum(1 - s_up, 1e-3), 0, 1)
                sag = SHAKE_SAG + SHAKE_SAG_SD * cv[1][cyc]
                swp = SHAKE_SWOOP * np.clip(1 + SHAKE_SWOOP_SD * cv[2][cyc], 0, 2)
                gate = shake > 0
                sh_c = sh_c + SHAKE_CYC * gate * (up * sag * u_up - (1 - up) * swp * np.sin(np.pi * u_lo))
                air = air * 10 ** (SHAKE_CYC * SHAKE_AIR_DB * gate * (up - 0.5) / 20)
                if SHAKE_AIR_WANDER > 0:                   # the air wanders on its own as well
                    air = air * 10 ** (SHAKE_AIR_WANDER * gate * _wander(nc, crate, SHAKE_AIR_WANDER_HZ,
                                                                          _rng(seed, ms0, 25)) / 20)
            if SHAKE_REACH < 1 or SHAKE_REACH_SD > 0:      # the dips stop short of the lower partial
                cyr = np.floor(sph).astype(int)
                cyr -= cyr.min()
                rch = np.clip(SHAKE_REACH + SHAKE_REACH_SD * _rng(seed, ms0, 28).normal(0, 1, cyr.max() + 2), 0.1, 1.2)
                top_ = shake * s_amt
                sh_c = top_ - rch[cyr] * (top_ - sh_c) * (shake > 0)
            if SHAKE_GHOST != 0 or SHAKE_X_AIR > 0:        # the left note holds on through a crossing
                gate_g = (shake > 0).astype(float)
                rate_e = np.maximum(np.gradient(sph) * crate, 1.0)
                rising = frac < s_up
                t_x = np.where(rising, frac, frac - s_up) / rate_e
                gd = g / rate_e
                env_g = np.clip(t_x / np.maximum(gd, 1e-4), 0, 1) * np.exp(-np.maximum(t_x - gd, 0) / (SHAKE_GHOST_MS / 1000.0))
                env_g = env_g * gate_g
                top_g = shake * s_amt
                if SHAKE_REACH < 1 or SHAKE_REACH_SD > 0:
                    low_g = top_g * (1 - rch[cyr])
                else:
                    low_g = np.zeros(nc)
                left = np.where(rising, low_g, top_g)
                if SHAKE_GHOST != 0:
                    shk_ghost = (left, 10 ** (SHAKE_GHOST / 20) * env_g)
                if SHAKE_X_AIR > 0:
                    air = air * 10 ** (SHAKE_X_AIR * env_g / 20)
            if SHAKE_TOP_SD > 0:                           # the top moves on its own
                shk_top = SHAKE_TOP_SD * (shake > 0) * _wander(nc, crate, SHAKE_TOP_RATE, _rng(seed, ms0, 27))
            if SHAKE_UP_DB > 0:                            # the upper note darker, each cycle its own
                cyc_ = np.floor(sph).astype(int)
                cyc_ -= cyc_.min()
                dm = np.clip(1 + SHAKE_UP_DB_SD * _rng(seed, ms0, 23).normal(0, 1, cyc_.max() + 2), 0, 2.5)
                shk_dark = -SHAKE_UP_DB * dm[cyc_] * up * (shake > 0)
            if SHAKE_LO_JIT > 0:                           # the lower partial wobbles fast
                sh_c = sh_c + SHAKE_LO_JIT * (shake > 0) * (1 - up) * _wander(nc, crate, SHAKE_LO_JIT_HZ,
                                                                                 _rng(seed, ms0, 24))
            if SHAKE_JUMP > 0:                             # two notes cross-faded, not one bent note
                gate_ = (shake > 0).astype(float)
                gj = np.clip(SHAKE_JUMP / 1000.0 * sr_hz, 1e-3, 0.4)
                upj = _smooth(frac / gj) * (1 - _smooth((frac - s_up) / gj)) * gate_
                sh_hi = shake * s_amt
                sh_lo = np.zeros(nc)
                if SHAKE_CYC > 0:
                    sh_hi = sh_hi + SHAKE_CYC * gate_ * sag * u_up
                    sh_lo = sh_lo - SHAKE_CYC * gate_ * swp * np.sin(np.pi * u_lo) * (1 - up)
                if SHAKE_LO_JIT > 0:
                    sh_lo = sh_lo + SHAKE_LO_JIT * gate_ * (1 - up) * _wander(nc, crate, SHAKE_LO_JIT_HZ,
                                                                              _rng(seed, ms0, 24))
                xw = 4 * upj * (1 - upj)                   # 1 half way through each cross-fade
                if SHAKE_JUMP_BEND > 0:                    # the incoming note slots in from part way across
                    rising = frac < 0.5 * s_up
                    sh_hi = sh_hi - SHAKE_JUMP_BEND * shake * s_amt * (1 - upj) * rising
                    sh_lo = sh_lo + SHAKE_JUMP_BEND * shake * s_amt * upj * ~rising
                blow_db = blow_db + SHAKE_JUMP_DIP * xw    # the lips break: both notes weaker in the crossing
                air = air * 10 ** (SHAKE_JUMP_AIR * xw / 20)   # and the crossing is noisy
                shk_jump = (np.sqrt(1 - upj), 2 ** ((sh_hi - sh_lo) / 12), np.sqrt(upj))
                sh_c = sh_lo
            cross = np.exp(-((frac - g / 2) / (g * 0.6)) ** 2) + np.exp(-((frac - s_up - g / 2) / (g * 0.6)) ** 2)
            blow_db = blow_db + SHAKE_DIP_DB * (shake > 0) * cross * s_dip
        else:
            sh_c = 0.0
        if fall_rough is not None and FALL_TONE_C > 0:     # the dying tone's pitch goes rough (unpitched)
            ri, rt = fall_rough
            rw = _wd(nc, crate, 30.0, _rng(seed, ms0, 13))
            drift = drift + np.concatenate([np.zeros(ri), FALL_TONE_C / 100.0 * rw[ri:] * (1 - np.exp(-rt / 0.03))])
        rel_sag = -0.15 * (1 - amp_env) * (amp_env > 0)
        if HALF_STEP_C != 0 and HALF_STEPS > 0 and half.max() > 0:   # the pitch steps with the valve
            drift = drift + HALF_STEP_C / 100.0 * half * hv_oct / (HALF_STEPS * HALF_STEP_OCT)
        m_c = midi + bend + bend_extra + vib_c + drift + rel_sag + sh_c
        f0_c = 440.0 * 2 ** ((m_c - 69) / 12)

        # ---- blowing (dB re ff): velocity, the level lane, slur/fall dips, flutter flaps, half-valve, wander
        frng = _rng(seed, ms0, 8)
        if flut.max() > 0:
            fr_hz = float(P['flutter_rate']) * (1 + 0.1 * _wd(nc, crate, 4.0, frng))
            fph = 2 * np.pi * np.cumsum(fr_hz / crate)
            flap = (0.5 + 0.5 * np.cos(fph)) ** 2              # 1 at each tongue closure
            if FLUT_IV_SD > 0:                             # each flap its own length and depth
                cyc = np.cumsum(fr_hz / crate)
                vr_ = _rng(seed, ms0, 16)
                nfl = int(cyc[-1]) + 4
                dv_ = vr_.normal(0, 1, (nfl, 2))
                ivs = np.exp(FLUT_IV_SD * dv_[:, 0])
                B = np.concatenate([[0.0], np.cumsum(ivs / ivs.mean())]) - 0.5
                jf = np.clip(np.searchsorted(B, cyc, side='right') - 1, 0, nfl - 1)
                uf = (cyc - B[jf]) / (B[jf + 1] - B[jf])
                dmul = np.clip(1 + FLUT_DEPTH_SD * dv_[:, 1], 0.2, 2.0)
                flap = (0.5 + 0.5 * np.cos(2 * np.pi * uf)) ** 2 * dmul[jf]
            flut_gain = 1 - FLUTTER_DEPTH * flut * flap
        else:
            flap = np.zeros(nc)
            flut_gain = np.ones(nc)
        breathe = float(P['breathe_db']) * _wd(nc, crate, 0.9, prng) * (1 + HALF_WOBBLE * half) + \
            2.0 * half * _wd(nc, crate, 6.0, prng)
        b_db = 20 * np.log10(vel) + lvl + blow_db + breathe + jit_db + HALF_LEVEL_DB * half + \
            20 * np.log10(np.maximum(amp_env * flut_gain ** FLUTTER_LAW, 1e-4))
        b_db = np.minimum(b_db, 6.0)
        tilt = float(P['bright'])
        top_l = lc('top', 0.0) if lanes.get('top') is not None else None   # the 'top' lane

        # ---- harmonics
        upi = np.arange(n)
        ci = upi / CR
        i_lo = np.minimum(np.floor(ci).astype(int), nc - 2)
        fr_ = ci - i_lo

        def upc(x):
            return x[i_lo] * (1 - fr_) + x[i_lo + 1] * fr_

        sig = np.zeros(n)
        TG = 4                                             # partial groups, each with its own slow air noise
        sig_g = np.zeros((TG, n)) if TONE_AIR > 0 else None
        on = amp_env > 0
        f0_med = float(np.median(f0_c[on])) if on.any() else 440.0
        kmax = int(min(80, 15000.0 / max(80.0, np.min(f0_c[on]) if on.any() else 200.0)))
        phases = prng.uniform(-PHASE_SPREAD, PHASE_SPREAD, kmax + 1)
        note_ids = np.round(midi).astype(int)
        fin = {u: np.array([_finger(u, k) for k in range(1, kmax + 1)]) for u in np.unique(note_ids)}
        fin_c = np.stack([fin[u] for u in note_ids], axis=1)
        pw_db = PARTIAL_WANDER_DB if P['partial_wander'] is None else float(P['partial_wander'])
        pw_hz = PARTIAL_WANDER_HZ if P['partial_wander_hz'] is None else float(P['partial_wander_hz'])
        f0_c_main = f0_c
        # SHAKE_JUMP plays the shake as two notes, the lower and the upper partial, each with its own
        # harmonics, cross-faded at each crossing (the notes overlap there), in place of one bent note
        passes = [(f0_c, None)] if shk_jump is None else [(f0_c, shk_jump[0]), (f0_c * shk_jump[1], shk_jump[2])]
        if shk_ghost is not None and shk_jump is None:     # the left note, a second pass at its own pitch
            passes.append((f0_c * 2 ** ((shk_ghost[0] - sh_c) / 12), shk_ghost[1]))
        for p_i, (f0_c, w_pass) in enumerate(passes):
            f0 = upc(f0_c)
            gr = growl.max() > 0
            if gr:
                grng = _rng(seed, ms0, 4)
                g_u = upc(growl)
                fh = f0 * 2 ** (-float(P['growl_interval']) / 12) * \
                    np.maximum(0.3, 1 + 0.1 * upc(_wd(nc, crate, 25.0, grng)))
                phh = 2 * np.pi * np.cumsum(fh) / sr
                pm = 2 * np.pi * f0 * GROWL_PM_US * 1e-6 * g_u * np.sin(phh + 0.5)
                beat = GROWL_AM * g_u * np.sin(phh)
                ph1 = 2 * np.pi * np.cumsum(f0) / sr + pm
            else:
                ph1 = 2 * np.pi * np.cumsum(f0) / sr
            for k in range(1, kmax + 1):
                fk = k * f0_c
                adb = _env_db(fk, midi) + fin_c[k - 1]
                kb = np.clip(BRASS_HZ / np.maximum(f0_c, 1.0), 1.0, k)   # the steepening stops at BRASS_HZ
                adb = adb + b_db * (G1 + BRASS_B * np.log2(kb))
                octs = np.log2(np.maximum(fk, 1) / 500.0)
                adb = adb + tilt * np.where(octs > 0, octs, 0.0)
                if top_l is not None:
                    adb = adb + top_l * np.clip(np.log2(np.maximum(fk, 1) / TOP_HZ0), 0, None)
                adb = adb + _curve(mute, fk) + half * _half_db(fk * 2 ** -hv_oct)
                if k == 1:
                    adb = adb + float(P['h1_db'])
                if shk_dark is not None:
                    adb = adb + shk_dark * _curve(SHAKE_UP_SHAPE, fk)
                if shk_top is not None:
                    adb = adb + shk_top * np.clip((fk / SHAKE_TOP_HZ0 - 1) / 0.4, 0, 1)
                if REL_TILT > 0 and REL_DB_S > 0:
                    adb = adb + REL_TILT * rel_db * np.clip(np.log2(np.maximum(fk, 1) / 1500.0), 0, None)
                if plg.max() > 0:
                    pc = np.clip(plg * 2, 0, 1)
                    pc2 = np.clip(plg * 2 - 1, 0, 1)
                    adb = adb + pc * (1 - pc2) * _curve(PLUNGER_HALF_DB, fk) + pc2 * _curve(PLUNGER_CLOSED_DB, fk)
                    if PLUNGER_FINE > 0:
                        adb = adb + PLUNGER_FINE * pc2 * _curve(PLUNGER_FINE_DB, fk)
                if pw_db > 0:
                    pw = np.clip(np.log2(np.maximum(fk, 1) / 1000.0), 0, 1)
                    if pw.max() > 0:
                        pwg = pw_db * pw
                        if PW_SOFT > 0:                        # softer notes waver more at the top
                            pwg = pwg * 10 ** (-PW_SOFT * np.clip(20 * np.log10(vel), -30, 0) / 20)
                        adb = adb + pwg * _wd(nc, crate, pw_hz, _rng(seed, ms0, 300 + k)) - 0.06 * pwg ** 2
                amp_k = 10 ** (adb / 20)
                if w_pass is not None:
                    amp_k = amp_k * w_pass
                amp_k[fk > 15000] = 0.0
                amp_k *= np.clip((16000 - fk) / 3000, 0, 1)
                part = upc(amp_k) * np.sin(k * ph1 + phases[k])
                if gr:
                    wk = min(1.0, 0.2 + 0.8 * (k - 1) / 3.0)
                    part = part * (1 + wk * beat)
                sig += part
                if sig_g is not None:
                    sig_g[k % TG] += part * (k * f0_med / TONE_AIR_REF) ** TONE_AIR_TILT
            if p_i == 0:
                ph1_main = ph1
        f0_c, ph1 = f0_c_main, ph1_main

        if FLUTTER_LAW < 1 and flut.max() > 0:
            sig *= upc(flut_gain ** (1 - FLUTTER_LAW))     # the flap gates the sound as a whole
        # ---- air: breath noise riding the tone, flutter bursts, half-valve hiss, the tongue's chiff
        nrng = _rng(seed, ms0, 3)
        wn = _normal(nrng, n, n_dr)
        bn = sosfilt(butter(2, NOISE_HZ, btype='band', fs=sr, output='sos'), wn)
        if BAND_TOP_HZ > 0:                                # the breath band's top falls steeply
            bn = BAND_GAIN * sosfilt(butter(int(BAND_TOP_ORDER), BAND_TOP_HZ, btype='low', fs=sr, output='sos'), bn)
        blow_lin = upc(10 ** (np.minimum(b_db, 0) / 20))
        sync = 0.5 + 0.5 * (0.5 + 0.5 * np.cos(ph1)) ** 2
        mute_air = 10 ** (np.mean(_curve(mute, np.array([1500.0, 2500.0, 4000.0]))) / 20)
        # the flutter's air: FLUTTER_NOISE_DEPTH of it pulses with the flaps, the rest rides at the flaps' mean
        air_amt = upc(air * (1 + HALF_AIR * half) +
                      FLUTTER_NOISE * flut * ((1 - FLUTTER_NOISE_DEPTH) * 0.375 + FLUTTER_NOISE_DEPTH * flap))
        if half.max() > 0:
            h_u = upc(half)
            bl = sosfilt(butter(int(HALF_AIR_ORDER), HALF_AIR_LP, btype='low', fs=sr, output='sos'), bn)
            hg = 10 ** (float(_curve(HALF_DB, np.array([400.0]))[0]) * h_u / 20)
            bn = (bn * (1 - h_u) + bl * h_u * 1.5) * hg
            if HALF_HISS > 0 and HALF_STEPS > 0:
                # a steep high band whose edge follows the valve; five edges a quarter octave apart, mixed
                w_h = _normal(_rng(seed, ms0, 11), n, n_dr)
                qs = np.arange(-2, 3) * 0.25
                lp = butter(4, HALF_HISS_HZ[1], btype='low', fs=sr, output='sos')
                bands = []
                for q in qs:                               # the high-pass run twice: the real edge is a wall
                    hp = butter(int(HALF_EDGE_ORDER), HALF_EDGE_HZ * 2 ** q, btype='high', fs=sr, output='sos')
                    bands.append(sosfilt(lp, sosfilt(hp, sosfilt(hp, w_h))))
                pos = upc(np.clip(hv_oct / 0.25 + 2, 0, 4))
                hs = np.zeros(n)
                for qi in range(5):
                    hs += bands[qi] * np.clip(1 - np.abs(pos - qi), 0, 1)
                bn = bn + HALF_HISS * h_u * hs
            elif HALF_HISS > 0:
                hs = sosfilt(butter(2, HALF_HISS_HZ, btype='band', fs=sr, output='sos'), _normal(_rng(seed, ms0, 11), n, n_dr))
                bn = bn + HALF_HISS * h_u * hs
        # the breath noise follows the blowing at the brass law of the harmonics in its band (about 2 kHz), so
        # softer notes keep the real's harmonic-over-floor (Davis mf 50 dB, Iowa ff 51 dB; linear left mf at 36)
        k_air = np.log2(np.maximum(AIR_HZ / np.maximum(f0_c, 50.0), 1.0))
        air_law = 1.0 + AIR_LAW * (G1 + BRASS_B * k_air - 1.0)
        air_lin = upc(10 ** (np.minimum(b_db, 0) * air_law / 20))
        if TONE_AIR > 0:
            sos_t = butter(2, TONE_AIR_HZ, btype='low', fs=sr, output='sos')
            imp = np.zeros(sr)
            imp[0] = 1.0
            t_norm = np.sqrt(np.sum(sosfilt(sos_t, imp) ** 2))      # unit-rms slow noise
            if FLUTTER_LAW < 1 and flut.max() > 0:
                sig_g *= upc(flut_gain ** (1 - FLUTTER_LAW))[None, :]
            trng = _rng(seed, ms0, 12)
            sig_t = sum(sig_g[g] * sosfilt(sos_t, _normal(trng, n, n_dr)) for g in range(TG)) / t_norm
            sig_t = sig_t * upc(air / DEFAULTS['breath'] * 10 ** (-TONE_AIR_LAW * np.minimum(b_db, 0) / 20)) * TONE_AIR
        else:
            sig_t = 0.0
        if FALL_AIR_TONE > 0 and fall_bx.max() > 0:
            bn = bn * upc(1 - FALL_AIR_TONE * fall_bx)
        if float(P['noise_wander']) > 0:                  # the air floor (band and tone-shaped) drifts
            nw = upc(10 ** (float(P['noise_wander']) * _wander(nc, crate, float(P['noise_wander_hz']),
                                                              _rng(seed, ms0, 22)) / 20))
            bn = bn * nw
            sig_t = sig_t * nw
        air_band = BAND_AIR * bn * sync * air_amt * air_lin * 10 ** (float(P['noise_db']) / 20) * 8.0 * mute_air
        if float(P['air_lowcut']) > 0:                     # the breath band's low end cut steeply,
            # after the pitch-synchronous gate (the gate at f0 folds the band's air down under the fundamental)
            air_band = sosfilt(butter(4, float(P['air_lowcut']), btype='high', fs=sr, output='sos'), air_band)
        sig += air_band
        sig += sig_t
        if gr:
            rasp = sosfilt(butter(2, (800, 6000), btype='band', fs=sr, output='sos'), _normal(grng, n, n_dr))
            sig += rasp * g_u * GROWL_RASP * blow_lin * (0.5 + 0.5 * np.sin(phh)) ** 3
        sosh = butter(2, 1500.0, btype='high', fs=sr, output='sos')
        for i0, strength in chiff:
            if strength <= 0:
                continue
            L = int(0.015 * sr)
            s0 = i0 * CR
            if s0 >= n:
                continue
            e = min(n, s0 + L)
            w = sosfilt(sosh, nrng.normal(0, 1, e - s0))
            envc = np.exp(-np.arange(e - s0) / (0.004 * sr)) * (1 - np.exp(-np.arange(e - s0) / (0.001 * sr)))
            g_ref = blow_lin[min(n - 1, s0 + int(0.04 * sr))]
            if float(P['chiff_db']) != 0:
                strength = strength * 10 ** (float(P['chiff_db']) / 20)
            sig[s0:e] += w * envc * strength * 0.12 * g_ref * mute_air
        if fall_tail is not None:
            fa, fb = fall_tail[0] * CR, min(n, fall_tail[1] * CR)
            ref = sig[max(0, fa - int(0.1 * sr)):fa]
            if fb > fa and len(ref):
                trng = _rng(seed, ms0, 10)
                tn = sosfilt(butter(int(FALL_AIR_ORDER), (FALL_AIR_LO, FALL_AIR_HI), btype='band', fs=sr, output='sos'), trng.normal(0, 1, n - fa))
                tn /= np.sqrt(np.mean(tn ** 2)) + 1e-12
                tt = np.arange(n - fa) / sr
                tf = (fb - fa) / sr
                env = np.where(tt < tf, (tt / tf) ** FALL_AIR_RAMP, np.exp(-(tt - tf) / FALL_AIR_S))
                if FALL_AIR_TONE > 0:                      # during the fall the air follows the partials
                    sos_t = butter(2, TONE_AIR_HZ, btype='low', fs=sr, output='sos')
                    sn = sosfilt(sos_t, _rng(seed, ms0, 21).normal(0, 1, fb - fa))
                    tone = sig[fa:fb] * sn / (np.sqrt(np.mean(sn ** 2)) + 1e-12)
                    tone /= np.sqrt(np.mean(sig[fa:fb] ** 2)) + 1e-12
                    xf = np.clip(tt / max(tf, 1e-6), 0, 1)
                    mix = FALL_AIR_TONE * (1 - _smooth((xf - 0.9) / 0.1))     # the band air takes over at the end
                    tn[:fb - fa] = (1 - mix[:fb - fa]) * tn[:fb - fa] + mix[:fb - fa] * tone
                sig[fa:] += tn * env * FALL_AIR * np.sqrt(np.mean(ref ** 2))

        s0, e0 = max(0, a), min(total_n, b)
        out[s0:e0] += sig[s0 - a:e0 - a]

    out *= 0.14 * float(P['level'])
    room = float(P['room'])
    L = out.copy()
    R = out.copy()
    if room > 0:
        for d_ms, g, side in ((6.3, 0.3, 0), (10.1, 0.24, 1), (15.7, 0.19, 0), (22.9, 0.15, 1), (29.5, 0.1, 0),
                              (35.2, 0.08, 1)):
            dn = int(d_ms / 1000 * sr)
            tgt = L if side == 0 else R
            tgt[dn:] += room * g * out[:-dn]
    if float(P['floor_db']) > -150:
        t_start = beat0 * 60.0 / bpm
        i0 = int(round(t_start * sr))
        b_first, b_last = i0 // sr, (i0 + total_n - 1) // sr
        sos_f = butter(int(FLOOR_ORDER), 1500.0, btype='low', fs=sr, output='sos')
        fl = np.zeros(total_n)
        for blk in range(b_first, b_last + 1):
            w = np.concatenate([_rng(seed, blk - 1, 7).normal(0, 1, sr), _rng(seed, blk, 7).normal(0, 1, sr)])
            w = (sosfilt(sos_f, w) * 2.5 + FLOOR_WHITE * w)[sr:]
            a = blk * sr - i0
            s0, e0 = max(0, a), min(total_n, a + sr)
            if e0 > s0:
                fl[s0:e0] = w[s0 - a:e0 - a]
        fl *= 10 ** (float(P['floor_db']) / 20) / (0.788 * np.sqrt(44100.0 / sr))
        L += fl
        R += fl
    return np.vstack([L, R])
