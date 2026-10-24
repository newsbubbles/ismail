"""Placed audio on a live deck: the carrier `live_load` gives a track whose sound is audio clips placed on the
song's timeline (`audio_place`, a `track_freeze`), so it plays on a deck as the studio renders it.

A performer that reads the song's own sound files by song time: each note is a gate, and where it sounds the
voice plays every clip that covers that song beat, from the right place in its file, at its gain, at 1x speed.
Pitch and velocity are ignored (a deck transpose leaves it alone, as it leaves drums). The deck loader writes one
note per bar the clips sound in, so the engine renders it in bar chunks like any performer; only those bars are
read from disk, never the whole file. At a house tempo other than the song's, each bar starts at its own place in
the file and plays at its own speed (not stretched): a faster house cuts each bar short, a slower one runs it on
into the start of the next."""
import os

import numpy as np

INFO = {
    "summary": "placed audio clips on a deck: plays the song's own sound files by song time (live_load uses it)",
    "range": "any pitch (ignored)",
    "velocity": "ignored",
    "params": {"clips": "[{file, at_beat, offset_sec, length_beats, gain_db, shift_sec}] from the song's project",
               "song_bpm": "the tempo the clips were placed at"},
    "source": "the song's own sound files",
}


def _read(path, start, n, sr):
    """n samples (2, n) of the file from frame `start` (at the file's own rate), at rate sr; zeros past its end."""
    import soundfile as sf
    out = np.zeros((2, n))
    if n <= 0:
        return out
    with sf.SoundFile(path) as f:
        fsr, frames = f.samplerate, f.frames
        if fsr == sr:
            a = max(0, start)
            if a >= frames:
                return out
            f.seek(a)
            x = f.read(min(n - (a - start), frames - a), dtype='float64', always_2d=True).T
            out[:, a - start:a - start + x.shape[1]] = x[:2] if x.shape[0] > 1 else np.vstack([x, x])
            return out
        # another rate: read the span it covers and interpolate (a frozen file is written at the project's rate)
        pos = start + np.arange(n) * (fsr / sr)
        a, b = max(0, int(np.floor(pos[0]))), min(frames, int(np.ceil(pos[-1])) + 2)
        if a >= b:
            return out
        f.seek(a)
        x = f.read(b - a, dtype='float64', always_2d=True).T
        x = x[:2] if x.shape[0] > 1 else np.vstack([x, x])
        ok = (pos >= a) & (pos <= b - 1)
        for c in (0, 1):
            out[c, ok] = np.interp(pos[ok] - a, np.arange(x.shape[1]), x[c])
    return out


def perform(notes, total_n, sr, bpm=120.0, lanes=None, beat0=0.0, project_root=None, clips=(), song_bpm=None, **_):
    out = np.zeros((2, total_n))
    if not notes or not clips:
        return out
    spb_house, spb_song = 60.0 / bpm, 60.0 / float(song_bpm or bpm)
    notes = sorted(notes)
    rates = {}
    for i, (st, _, d, _) in enumerate(notes):
        a = max(0, int(round(st * sr)))
        # a note sounds until the next one starts (they are bars of one recording); the last one reads on to the
        # event's end, which carries the crossfade into the next chunk and the file's own tail
        z = total_n if i + 1 == len(notes) else min(int(round((st + d) * sr)), int(round(notes[i + 1][0] * sr)))
        z = min(z, total_n)
        if z <= a:
            continue
        # where this note sits in the song, in seconds from beat 0 at the song's own tempo
        t_song = (beat0 + st / spb_house) * spb_song
        for c in clips:
            path = c['file'] if os.path.isabs(c['file']) else os.path.join(project_root or '', c['file'])
            u0 = t_song - (float(c.get('at_beat', 0.0)) * spb_song + float(c.get('shift_sec', 0.0)))
            n = z - a
            j0 = max(0, int(np.ceil(-u0 * sr - 1e-9)))          # first sample at or after the clip's start
            j1 = n
            if c.get('length_beats'):
                j1 = min(n, int(np.floor((float(c['length_beats']) * spb_song - u0) * sr + 1e-9)))
            if j1 <= j0:
                continue
            if path not in rates:
                import soundfile as sf
                rates[path] = sf.info(path).samplerate
            fsr = rates[path]
            start = int(round((float(c.get('offset_sec', 0.0)) + u0 + j0 / sr) * fsr))
            y = _read(path, start, j1 - j0, sr)
            g = 10 ** (float(c.get('gain_db', 0.0)) / 20)
            out[:, a + j0:a + j1] += y * g
    return out
