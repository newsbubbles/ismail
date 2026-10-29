"""Where a song's audio is on disk, now that a finished song's WAVs may be FLAC (ledger:M221).

`song_compress` turns stems and named renders into FLAC, bit for bit, and moves the WAVs to the reclaim folder.
Readers keep building the `.wav` path they always built and pass it through `find`, which returns the `.flac` beside it when the
`.wav` is gone. Writers do not change: a new render writes `<name>.wav`, and `find` prefers it to an older
`<name>.flac`, so the newest audio wins.

    from ismail import audiofile
    p = audiofile.find(os.path.join(stems_dir, 'drums.wav'))     # drums.wav, else drums.flac, else drums.wav
    for name, p in audiofile.listing(stems_dir).items(): ...      # one entry per name, from .wav and .flac
"""
import os

EXTS = ('.wav', '.flac')          # in order of preference: a WAV beside a FLAC of the same name is the newer render


def find(path):
    """The `.wav` path a reader built, if it exists; else the `.flac` beside it if that exists; else the `.wav` path
    unchanged, so the reader's own "missing" error still fires. Any other path comes back as it is."""
    path = os.fspath(path)
    if os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    if ext.lower() == '.wav':
        alt = base + '.flac'
        if os.path.exists(alt):
            return alt
    return path


def listing(folder):
    """{name: path} of the audio in a folder, sorted by name, one entry per name: `<name>.wav` when it is there,
    else `<name>.flac`. Empty when the folder does not exist."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for f in names:
        base, ext = os.path.splitext(f)
        ext = ext.lower()
        if ext not in EXTS or not os.path.isfile(os.path.join(folder, f)):
            continue
        if base not in out or EXTS.index(ext) < EXTS.index(os.path.splitext(out[base])[1].lower()):
            out[base] = os.path.join(folder, f)
    return dict(sorted(out.items()))
