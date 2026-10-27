"""Hum a part into a voice note (ledger:M163, the Live DJ's handoff 52). Nate, 2026-10-06: "if I hummed or sung
something into the voice notes on mobile, couldn't you also analyze that and get my rough sketch of notes plus timing
that is synchronized with the playback". No hold-to-hum button: a hum is recognised from the note itself.

This part is the plumbing: decode a note, and tell a hum from talk by how it sounds and how few words it holds. The
master under every note is saved by the server (<id>_ref.wav), so the music that bleeds into the mic can line the
note up with the beat he heard; the measuring (alignment, notes, grid, swing) is the Live DJ's and joins here when it
has proved itself on real hums.

The thresholds are first guesses, to be replaced by what the phone mic and the Dime 3 headset actually measure."""
import re
import subprocess

import numpy as np

SR = 16000
HOP = 320                         # 20 ms
FMIN, FMAX = 65.0, 1050.0         # C2 to C6: a hum or a sung line, low or high
ACTIVE_DB = -35.0                 # frames within this of the loudest count as sound
VOICED_MIN = 0.5                  # a hum is mostly pitched...
STEADY_MIN = 0.5                  # ...holds its pitch (speech glides)...
WORDS_MAX = 1.0                   # ...and has few words (per second of note)
STEADY_ST = 0.25                  # semitones per 20 ms that still count as holding a note
HUM_MAX_S = 60.0                  # longer is talk or a song, not a hum: the pitch tracker holds the whole
                                  # phone server for about half the note's length (a 322 s note: 150 s, 10-11)
NONWORDS = {'hm', 'hmm', 'hmmm', 'mm', 'mmm', 'mmmm', 'la', 'da', 'na', 'doo', 'do', 'ba', 'dum', 'dee', 'di',
            'ooh', 'oo', 'ah', 'uh', 'um', 'mhm', 'music'}


def decode(path, ffmpeg, sr=SR):
    """A voice note (webm, ogg, m4a, wav) -> mono float32 at sr."""
    out = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-i', str(path), '-ac', '1', '-ar', str(sr), '-f', 'f32le', '-'],
                         capture_output=True, timeout=120)
    if out.returncode:
        raise ValueError(f"ffmpeg could not read {path}: {out.stderr.decode('utf8', 'replace')[-200:]}")
    return np.frombuffer(out.stdout, dtype='<f4').copy()


def words(text):
    """Real words in a transcript: hums come back as 'Mmm', 'la la' or a music note sign, which are not."""
    return [w for w in re.findall(r"[a-z']+", (text or '').lower()) if w.strip("'") not in NONWORDS]


def check(y, text='', sr=SR):
    """-> {is_hum, voiced, steady, words_per_s, hz, dur_s, why}: voiced = pitched share of the frames with sound,
    steady = share of the pitched frames that hold their note, hz = the median pitch."""
    import librosa
    dur = len(y) / sr
    if dur < 0.5:
        return {'is_hum': False, 'dur_s': round(dur, 1), 'why': 'too short to tell (under half a second)'}
    wps = len(words(text)) / dur                      # words first: talk is ruled out without the pitch tracker
    if wps > WORDS_MAX:
        return {'is_hum': False, 'words_per_s': round(wps, 2), 'dur_s': round(dur, 1),
                'why': f'{wps:.1f} words a second (a hum has under {WORDS_MAX:g})'}
    if dur > HUM_MAX_S:
        return {'is_hum': False, 'words_per_s': round(wps, 2), 'dur_s': round(dur, 1),
                'why': f'too long for a hum ({dur:.0f} s; the check takes notes up to {HUM_MAX_S:g} s)'}
    f0, vflag, _ = librosa.pyin(y, fmin=FMIN, fmax=FMAX, sr=sr, frame_length=1024, hop_length=HOP)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP)[0][:len(f0)]
    db = 20 * np.log10(rms + 1e-9)
    active = db > db.max() + ACTIVE_DB
    voiced = vflag & active
    vfrac = float(voiced.sum() / max(1, active.sum()))
    midi = librosa.hz_to_midi(np.where(voiced, f0, np.nan))
    d = np.abs(np.diff(midi))
    both = voiced[1:] & voiced[:-1]
    steady = float((d[both] < STEADY_ST).sum() / max(1, both.sum()))
    hz = float(np.nanmedian(f0[voiced])) if voiced.any() else None
    fails = [m for ok, m in ((vfrac >= VOICED_MIN, f'pitched {vfrac:.0%} (a hum is over {VOICED_MIN:.0%})'),
                             (steady >= STEADY_MIN, f'holds its notes {steady:.0%} (over {STEADY_MIN:.0%})'),
                             (wps <= WORDS_MAX, f'{wps:.1f} words a second (a hum has under {WORDS_MAX:g})'))
             if not ok]
    return {'is_hum': not fails, 'voiced': round(vfrac, 2), 'steady': round(steady, 2), 'words_per_s': round(wps, 2),
            'hz': round(hz, 1) if hz else None, 'note': librosa.hz_to_note(hz) if hz else None,
            'dur_s': round(dur, 1), 'why': '; '.join(fails) or 'pitched, holds its notes, few words'}


def describe(vid, meta, chk):
    """One answer for phone_hum: what the note is, and where the music under it is."""
    h = meta.get('heard') or {}
    head = f"voice note {vid} ({chk.get('dur_s', meta.get('dur_s', '?'))} s" + \
           (f", started at {h['of']}" if h.get('of') else '') + ')'
    if chk.get('is_hum'):
        lines = [f"{head}: a HUM, around {chk['note']} ({chk['hz']} Hz). Pitched {chk['voiced']:.0%}, holds its "
                 f"notes {chk['steady']:.0%}, {chk['words_per_s']} words a second."]
    else:
        lines = [f"{head}: not a hum ({chk.get('why')})."]
    if meta.get('ref'):
        lines.append(f"The music he heard under it: {meta['ref']} (from {meta['ref_lead_s']:g} s before the note "
                     f"began; beat stamps in {meta['ref_beats']}). The music bleeding into his mic lines the two up: "
                     f"cross-correlate to find where the note sits, and the latency of his input with it." +
                     (f" Its start is a guess from when the note arrived (he was off the stream, likely on the room "
                      f"speaker): search {meta['ref_lead_s']:g} s either side." if meta.get('start_is_guess') else ''))
    else:
        lines.append(f"No music was saved under it: {meta.get('ref_why') or 'the note predates the master buffer'}.")
    lines.append("Notes, timing and swing: the Live DJ's measuring joins here once it holds on real hums (M163 phase "
                 "2); until then, read the ref and the note yourself.")
    return '\n'.join(lines)
