"""Back up a song's project before a rebuild, without copying what the engine makes again.

A song's build.py rebuilds proj/ from scratch and never deletes the old one: the old proj/ moves to backups/. Moved
whole, every backup also carries cache/, renders/ and stems/, which the engine regenerates: 13 rebuilds in 15
minutes wrote 3.2 GB (Machine Steward, 2026-10-08). Here the old proj/ still moves to backups/, the regenerable
folders come back into the new proj/, and older backups that still hold them hand them to the song's _reclaim/
(never deleted: the user clears _reclaim). What cannot be made again (project.json, history/, sounds/, voices/,
notes) stays in every backup.

    from ismail import backup
    kept = backup.set_aside(PROJ)        # old proj/ -> backups/proj_<time>; older full backups trimmed
    ... project_new(PROJ) ...
    backup.carry_back(kept, PROJ)        # cache/, renders/, stems/ move into the new proj/

    python -m ismail.backup trim songs/<slug>/backups [--keep 2] [--dry]   # tidy backups made the old way
"""
import os
import re
import shutil
import sys
import time

REGEN = ('cache', 'renders', 'stems')
_STAMP = re.compile(r'_(\d{8}_\d{6})(?:_\d+)?$')


def _free(path):
    """path, or path_2, path_3, ... whichever does not exist yet."""
    if not os.path.exists(path):
        return path
    i = 2
    while os.path.exists(f"{path}_{i}"):
        i += 1
    return f"{path}_{i}"


def _size(path):
    n = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                n += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return n


def set_aside(proj, backups=None, regen=REGEN, keep_full=2):
    """Move proj/ to backups/<name>_<time> and trim older full backups (see trim). Returns what carry_back needs,
    or None when there is no proj/ yet."""
    proj = os.path.abspath(proj)
    if not os.path.isdir(proj):
        return None
    song = os.path.dirname(proj)
    backups = backups or os.path.join(song, 'backups')
    os.makedirs(backups, exist_ok=True)
    dst = _free(os.path.join(backups, os.path.basename(proj) + '_' + time.strftime('%Y%m%d_%H%M%S')))
    shutil.move(proj, dst)
    trim(backups, regen=regen, keep_full=keep_full, reclaim=os.path.join(song, '_reclaim', 'backups'))
    return {'backup': dst, 'carry': {d: os.path.join(dst, d) for d in regen if os.path.isdir(os.path.join(dst, d))}}


def carry_back(kept, proj):
    """Move the regenerable folders from the backup set_aside made into the new proj/. A folder the new project
    already filled stays in the backup; an empty one it made goes into the backup as <name>_new_empty (never
    deleted). Returns the folder names moved."""
    if not kept:
        return []
    proj = os.path.abspath(proj)
    moved = []
    for d, src in kept['carry'].items():
        if not os.path.isdir(src):
            continue
        tgt = os.path.join(proj, d)
        if os.path.isdir(tgt):
            if os.listdir(tgt):
                continue
            shutil.move(tgt, _free(os.path.join(kept['backup'], d + '_new_empty')))
        os.makedirs(proj, exist_ok=True)
        shutil.move(src, tgt)
        moved.append(d)
    return moved


def trim(backups, regen=REGEN, keep_full=2, reclaim=None, dry=False):
    """The newest keep_full backups that still hold regenerable folders keep them; older ones move those folders
    to reclaim/<backup name>/ (default: the song's _reclaim/backups). Only time-stamped folders (<name>_YYYYmmdd_HHMMSS)
    count. Returns [(backup name, bytes moved)]."""
    backups = os.path.abspath(backups)
    if not os.path.isdir(backups):
        return []
    reclaim = reclaim or os.path.join(os.path.dirname(backups), '_reclaim', 'backups')
    stamped = [n for n in os.listdir(backups) if _STAMP.search(n) and os.path.isdir(os.path.join(backups, n))]
    stamped.sort(key=lambda n: (_STAMP.search(n).group(1), n))
    full = [n for n in stamped if any(os.path.isdir(os.path.join(backups, n, d)) for d in regen)]
    out = []
    for n in full[:max(0, len(full) - keep_full)]:
        size = 0
        for d in regen:
            src = os.path.join(backups, n, d)
            if not os.path.isdir(src):
                continue
            size += _size(src)
            if not dry:
                os.makedirs(os.path.join(reclaim, n), exist_ok=True)
                shutil.move(src, _free(os.path.join(reclaim, n, d)))
        out.append((n, size))
    return out


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) < 2 or a[0] != 'trim':
        print(__doc__.strip().splitlines()[-1].strip())
        return 2
    keep = int(a[a.index('--keep') + 1]) if '--keep' in a else 2
    dry = '--dry' in a
    moved = trim(a[1], keep_full=keep, dry=dry)
    total = sum(s for _, s in moved)
    for n, s in moved:
        print(f"{'would move' if dry else 'moved'} {n}: {s / 1e6:.0f} MB")
    print(f"{len(moved)} backups trimmed, {total / 1e9:.2f} GB {'would go' if dry else 'went'} to _reclaim"
          f" (never deleted: the user clears _reclaim)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
