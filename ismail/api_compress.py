"""song_compress: a finished song's stems and named renders as FLAC, bit for bit (ledger:M221, hq:D-183).

FLAC keeps every sample of a 16- or 24-bit WAV at about a fifth of the size. Each FLAC is read back and compared
with its WAV sample for sample; only an exact match moves on. The op never deletes: the WAVs move to
songs/_reclaim/flac_<date>/<song>/ with a manifest, and the user empties _reclaim when they like. Readers find the
FLAC through ismail.audiofile.find; writers keep writing WAV.

Scope: renders/stems/, the reference's stems folder and renders/*.wav except latest.wav. Never touched, by rule
(NEVER_PATHS, NEVER_DIRS, EXAM below): takes, rec, caches and exam clips of the songs that keep their own audio
there, anything under live/ (sets in progress), sounds/ (the render cache keys on their mtime), exam folders.
"""
import filecmp
import io
import json
import os
import re
import shutil
import time

import numpy as np

from . import machine
from .api import _load, op
from .sources import song_root

# relative to the songs folder: the folders Voice keeps its own audio in (Voice, 10-16)
NEVER_PATHS = (('boom_trance', 'takes'), ('vox', 'exam', 'clips'), ('vox', 'work', 'cache'), ('vox', 'rec'))
NEVER_DIRS = {
    'live': 'under live/ (a set in progress)',
    'sounds': 'under sounds/ (the render cache keys on their mtime)',
    'takes': 'a takes folder (phone, stage and recorded takes)',
    'phone': 'a phone folder (phone takes)',
}
EXAM = re.compile(r'(^|[_\-. ])exams?($|[_\-. ])', re.I)      # exam, exams, exam_clips, spatial_exam
FLAC_OK = {'PCM_16': 'PCM_16', 'PCM_24': 'PCM_24', 'PCM_S8': 'PCM_S8'}
WAV_FORMATS = ('WAV', 'WAVEX', 'RF64')
BLOCK = 1 << 18                       # frames per read: a stem is streamed, never held whole
SAMPLE_FILES, SAMPLE_S = 3, 30.0      # the dry estimate encodes this many files, this many seconds from each middle
SCRIPT_EXTS = ('.py', '.ps1', '.sh', '.bat', '.cmd', '.js', '.mjs')
SCRIPT_SKIP = {'_reclaim', 'backups', '_backups', 'renders', 'stems', 'cache', 'history', 'history_src',
               'node_modules', '.git', '__pycache__', '.venv', 'venv', 'sounds'}
G = 1e9


def _gb(n):
    return f"{n / G:.2f} GB" if n >= 0.1 * G else f"{n / 1e6:.1f} MB"


def never(path, songs):
    """Why `path` is never touched, or None. The rules read the path below the songs folder (the whole path when
    it is not below it)."""
    rel = os.path.abspath(path)
    if _inside(path, songs):
        rel = os.path.relpath(rel, os.path.abspath(songs))
    parts = [p.lower() for p in rel.replace('\\', '/').split('/') if p]
    for seq in NEVER_PATHS:
        if tuple(parts[:len(seq)]) == seq:
            return f"never touched: songs/{'/'.join(seq)}/"
    for p in parts[:-1]:
        if p in NEVER_DIRS:
            return 'never touched: ' + NEVER_DIRS[p]
        if EXAM.search(p):
            return f"never touched: an exam folder ({p}/)"
    return None


def _free(path):
    """path, or <base>_2<ext>, <base>_3<ext>, ... whichever does not exist yet."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.lexists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


def _wavs(folder):
    try:
        return sorted(os.path.join(folder, f) for f in os.listdir(folder)
                      if f.lower().endswith('.wav') and os.path.isfile(os.path.join(folder, f)))
    except OSError:
        return []


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _verdict(path):
    """(FLAC subtype, None) when the WAV can go to FLAC bit for bit, else (None, why not)."""
    import soundfile as sf
    try:
        info = sf.info(path)
    except Exception as e:                       # noqa: BLE001  (a broken file is listed, never touched)
        return None, f"unreadable ({type(e).__name__})"
    if info.format not in WAV_FORMATS:
        return None, f"not a plain WAV ({info.format})"
    if info.subtype in ('FLOAT', 'DOUBLE'):
        return None, 'float WAV (FLAC has no float)'
    if info.subtype not in FLAC_OK:
        return None, f"{info.subtype} WAV (FLAC here keeps 8, 16 or 24 bits)"
    if not 1 <= info.channels <= 8:
        return None, f"{info.channels} channels (FLAC holds 1 to 8)"
    return FLAC_OK[info.subtype], None


def _sample_ratio(paths):
    """FLAC bytes over WAV bytes on up to SAMPLE_S seconds from the middle of each file (encoded in memory)."""
    import soundfile as sf
    wav_b = flac_b = 0
    for p in paths:
        with sf.SoundFile(p) as r:
            n = min(r.frames, int(SAMPLE_S * r.samplerate))
            r.seek(max(0, (r.frames - n) // 2))
            y = r.read(n, dtype='int32', always_2d=True)
            sub, sr = FLAC_OK[r.subtype], r.samplerate
        if not len(y):
            continue
        buf = io.BytesIO()
        sf.write(buf, y, sr, format='FLAC', subtype=sub)
        flac_b += len(buf.getvalue())             # not tell(): the encoder seeks back to write its header
        wav_b += y.shape[0] * y.shape[1] * {'PCM_16': 2, 'PCM_24': 3, 'PCM_S8': 1}[sub]
    return flac_b / wav_b if wav_b else None


def _encode_and_verify(wav, part, sub):
    """Write `part` (FLAC at the WAV's bit depth) and compare every sample with the WAV. -> True on an exact match."""
    import soundfile as sf
    with sf.SoundFile(wav) as r, sf.SoundFile(part, 'w', samplerate=r.samplerate, channels=r.channels,
                                              subtype=sub, format='FLAC') as w:
        for block in r.blocks(blocksize=BLOCK, dtype='int32', always_2d=True):
            w.write(block)
    with sf.SoundFile(wav) as a, sf.SoundFile(part) as b:
        if (a.frames, a.samplerate, a.channels) != (b.frames, b.samplerate, b.channels):
            return False
        while True:
            x = a.read(BLOCK, dtype='int32', always_2d=True)
            y = b.read(BLOCK, dtype='int32', always_2d=True)
            if not np.array_equal(x, y):
                return False
            if len(x) < BLOCK:
                return True


def song_scripts(song):
    """[(path relative to the song, line numbers)] of the song's own scripts that name a .wav: they are their owners'
    code and read only WAV unless changed."""
    out = []
    for root, dirs, files in os.walk(song):
        dirs[:] = sorted(d for d in dirs if d.lower() not in SCRIPT_SKIP)
        for f in sorted(files):
            if not f.lower().endswith(SCRIPT_EXTS):
                continue
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) > 2_000_000:
                    continue
                with open(p, encoding='utf8', errors='replace') as fh:
                    lines = [i for i, ln in enumerate(fh, 1) if '.wav' in ln.lower()]
            except OSError:
                continue
            if lines:
                out.append((os.path.relpath(p, song).replace('\\', '/'), lines))
    return out


class _Plan:
    """What one run would do, found without writing anything."""

    def __init__(self, P):
        self.root = P.root
        self.song = song_root(P.root)
        self.songs = os.path.dirname(self.song)
        self.todo = []            # [(wav, flac subtype)]
        self.skipped = []         # [(path, why)]
        self.stale = []           # [(flac, why)]: an older FLAC beside a newer WAV of the same name
        self.strays = []          # [(path, why)]: a link name left by a failed latest.wav swap
        self.dup = None           # (named render) when latest.wav is a separate copy of it
        rd = os.path.join(self.root, 'renders')
        ref = P.d.get('reference') or {}
        dirs = [os.path.join(rd, 'stems')]
        for sd in (ref.get('stems_dir'), (ref.get('original') or {}).get('stems_dir')):
            if sd:
                sd = sd if os.path.isabs(sd) else os.path.join(self.root, sd)
                if os.path.abspath(sd) not in map(os.path.abspath, dirs):
                    dirs.append(sd)
        latest = os.path.join(rd, 'latest.wav')
        named = [p for p in _wavs(rd) if os.path.basename(p).lower() != 'latest.wav']
        here = never(latest, self.songs)          # a project inside a never-touched folder: nothing moves at all
        if os.path.isfile(latest) and not here:
            for p in named:
                if _same_file(p, latest):
                    self.skipped.append((p, 'latest.wav is a link to it: it stays WAV and costs nothing extra'))
                elif self.dup is None and os.path.getsize(p) == os.path.getsize(latest) and \
                        filecmp.cmp(p, latest, shallow=False):
                    self.dup = p
                    self.skipped.append((p, 'latest.wav is a copy of it: latest.wav becomes a link to it instead'))
        stray = latest + '.link'
        if os.path.lexists(stray) and not here:
            self.strays.append((stray, 'a second name for an older render, left by a latest.wav swap that failed'))
        taken = {os.path.abspath(p) for p, _ in self.skipped}
        for p in [*named, *(w for d in dirs for w in _wavs(d))]:
            if os.path.abspath(p) in taken:
                continue
            taken.add(os.path.abspath(p))
            if not _inside(p, self.song):
                self.skipped.append((p, 'outside the song folder (its owner compresses it)'))
                continue
            why = never(p, self.songs)
            if not why:
                sub, why = _verdict(p)
            if why:
                self.skipped.append((p, why))
                continue
            self.todo.append((p, sub))
            flac = os.path.splitext(p)[0] + '.flac'
            if os.path.exists(flac):
                self.stale.append((flac, f"older than {os.path.basename(p)}, rendered after it"))

    def wav_bytes(self):
        return sum(os.path.getsize(p) for p, _ in self.todo)


def _inside(path, folder):
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(folder))
    except ValueError:                             # another drive
        return False
    return not rel.startswith('..') and not os.path.isabs(rel)


def _rel(path, base):
    return os.path.relpath(path, base).replace('\\', '/')


class _Reclaim:
    """songs/_reclaim/flac_<date>/<song>/: moved files keep their path inside the song; manifest.jsonl lists each."""

    def __init__(self, plan):
        self.song = plan.song
        self.dir = os.path.join(plan.songs, '_reclaim', 'flac_' + time.strftime('%Y%m%d'), os.path.basename(plan.song))
        self.moved = []

    def move(self, path, why, **extra):
        dst = _free(os.path.join(self.dir, _rel(path, self.song)))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        size = os.path.getsize(path)
        shutil.move(path, dst)
        row = {'at': time.strftime('%Y-%m-%d %H:%M:%S'), 'from': _rel(path, self.song), 'to': _rel(dst, self.dir),
               'bytes': size, 'why': why, **extra}
        with open(os.path.join(self.dir, 'manifest.jsonl'), 'a', encoding='utf8') as f:
            f.write(json.dumps(row) + '\n')
        self.moved.append(row)
        return dst


def _few(paths, base, n=6):
    names = [_rel(p, base) for p in paths]
    return ', '.join(names[:n]) + (f", +{len(names) - n} more" if len(names) > n else '')


def _report_common(plan, L):
    if plan.skipped:
        by = {}
        for p, why in plan.skipped:
            by.setdefault(why, []).append(p)
        L.append(f"skipped {len(plan.skipped)}:")
        L += [f"  {len(ps)} {why}: {_few(ps, plan.song)}" for why, ps in by.items()]
    sc = song_scripts(plan.song)
    if sc:
        L.append(f"song scripts that name a .wav ({len(sc)}; they are their owners' code and read WAV only unless "
                 f"they pass the path through ismail.audiofile.find):")
        L += [f"  {p}: line{'s' if len(ln) > 1 else ''} {', '.join(map(str, ln[:8]))}{' ...' if len(ln) > 8 else ''}"
              for p, ln in sc[:30]]
        if len(sc) > 30:
            L.append(f"  +{len(sc) - 30} more")
    else:
        L.append('song scripts that name a .wav: none')


@op()
def song_compress(project: str, dry: bool = True) -> str:
    """A finished song's stems and named renders as FLAC, bit for bit: about a fifth of the space. One song per
    call, run by its owner or the user (no sweep over songs). dry=True (the default) only reports: the files, GB
    now, GB after (estimated from a few files), what is skipped and why, and the song's own scripts that name a .wav
    (their owners' code: they read WAV only). dry=False writes each FLAC at the WAV's bit depth, reads it back and
    compares every sample, and only then moves the WAV to songs/_reclaim/flac_<date>/<song>/ (manifest.jsonl lists
    every move). It never deletes: the space comes back when the user empties _reclaim. Scope: renders/stems/, the
    reference's stems folder, renders/*.wav except latest.wav (a latest.wav that copies a named render becomes a
    hard link to it). Never touched: float WAVs (FLAC has no float), anything under live/, sounds/, takes, exam
    folders, and the folders Voice keeps (boom_trance/takes, vox/exam/clips, vox/work/cache, vox/rec). Every reader
    in ismail finds <name>.flac when <name>.wav is gone; a new render writes WAV again and wins. dry=False runs in a
    CPU slot on the shared machine."""
    P = _load(project)
    plan = _Plan(P)
    now = plan.wav_bytes()
    dup_b = os.path.getsize(plan.dup) if plan.dup else 0
    head = f"song_compress {_rel(plan.root, plan.songs)}" + (' (dry: nothing written or moved)' if dry else '')
    if dry:
        ratio = _sample_ratio([p for p, _ in plan.todo[:SAMPLE_FILES]]) if plan.todo else None
        L = [head, f"files: {len(plan.todo)} WAV to FLAC, {_gb(now)} now"]
        if plan.todo:
            L[-1] += (f", about {_gb(now * ratio)} after (estimated from {min(len(plan.todo), SAMPLE_FILES)} "
                      f"files, {ratio:.0%} of their size)" if ratio else '')
            L.append(f"  {_few([p for p, _ in plan.todo], plan.song)}")
        if plan.dup:
            L.append(f"latest.wav: a copy of renders/{os.path.basename(plan.dup)}; it would become a hard link to it "
                     f"({_gb(dup_b)} to _reclaim)")
        for p, why in plan.stale + plan.strays:
            L.append(f"to _reclaim: {_rel(p, plan.song)} ({why})")
        _report_common(plan, L)
        L.append(f"WAVs would go to {_Reclaim(plan).dir} (never deleted: the user empties _reclaim). "
                 f"Run song_compress(project, dry=False) to do it.")
        return '\n'.join(L)
    if not plan.todo and not plan.dup and not plan.stale and not plan.strays:
        L = [head, 'nothing to compress']
        _report_common(plan, L)
        return '\n'.join(L)
    R = _Reclaim(plan)
    done, failed, flac_b = [], [], 0
    with machine.slot('cpu', f"song_compress {os.path.basename(plan.song)}", mem_gb=0.5, threads=1,
                      disk_gb=now * 0.35 / G, disk_path=plan.root,
                      disk_hint='free some space first: the FLACs are written before the WAVs move'):
        for p, why in plan.stale + plan.strays:
            R.move(p, why)
        if plan.dup:
            latest = os.path.join(plan.root, 'renders', 'latest.wav')
            tmp = latest + '.link'
            try:
                os.link(plan.dup, tmp)
            except OSError as e:
                failed.append((latest, f"no hard link on this drive ({e}); latest.wav stays a copy"))
            else:
                R.move(latest, f"copy of renders/{os.path.basename(plan.dup)}; latest.wav is now a hard link to it")
                os.replace(tmp, latest)
        for wav, sub in plan.todo:
            base = os.path.splitext(wav)[0]
            flac, part = base + '.flac', base + '.flac.part'
            if os.path.lexists(part):
                R.move(part, 'left by an earlier run that stopped')
            if os.path.lexists(flac):
                R.move(flac, 'an older FLAC of this name')
            try:
                ok = _encode_and_verify(wav, part, sub)
            except Exception as e:                       # noqa: BLE001  (the WAV stays; the next file goes on)
                ok = False
                failed.append((wav, f"{type(e).__name__}: {e}"))
            if not ok:
                if os.path.lexists(part):
                    R.move(part, f"FLAC of {os.path.basename(wav)} did not read back identical; the WAV stayed")
                if not failed or failed[-1][0] != wav:
                    failed.append((wav, 'the FLAC did not read back identical; the WAV stayed'))
                continue
            os.replace(part, flac)
            shutil.copystat(wav, flac)
            fb = os.path.getsize(flac)
            R.move(wav, 'FLAC read back sample for sample identical', flac=_rel(flac, plan.song), flac_bytes=fb)
            done.append(wav)
            flac_b += fb
    wav_b = sum(r['bytes'] for r in R.moved if r.get('flac'))
    L = [head, f"{len(done)} WAV to FLAC, each read back sample for sample identical: {_gb(wav_b)} of WAV is "
               f"now {_gb(flac_b)} of FLAC"]
    if plan.dup and not any(p.endswith('latest.wav') for p, _ in failed):
        L.append(f"latest.wav is now a hard link to renders/{os.path.basename(plan.dup)}")
    if failed:
        L.append(f"NOT done {len(failed)}:")
        L += [f"  {_rel(p, plan.song)}: {why}" for p, why in failed]
    moved_b = sum(r['bytes'] for r in R.moved)
    L.append(f"moved {len(R.moved)} files ({_gb(moved_b)}) to {R.dir} (manifest.jsonl lists each). Nothing was "
             f"deleted: the {_gb(moved_b)} come back when the user empties _reclaim.")
    _report_common(plan, L)
    return '\n'.join(L)
