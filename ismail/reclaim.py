"""One place for everything moved out to free space: <root>/_reclaim at the root of the drive (ledger:M229).

The user's rule (voice note, 2026-10-17): all reclaim folders go in one place at the root of the drive. Nothing here
deletes: files move into the reclaim folder, keep their path, and the user deletes a whole folder when they like.

    <root>/_reclaim/<what>_<YYYYMMDD>/<group>/<path from the drive root>
    D:\\ismail\\songs\\a1\\backups\\x.wav
        -> D:\\_reclaim\\song_wavs_20261017\\rebuildable\\ismail\\songs\\a1\\backups\\x.wav

Each <what>_<date>/ folder has a manifest.json: {"runs": [{"t": "2026-10-17T11:42Z", "why": "...", "moved":
[{"from": abs, "to": abs, "bytes": n, ...}]}]}; a later run appends to "runs". <root>/_reclaim/README.txt lists the
folders (name, size, date, who, and one line on what is in it).

    from ismail import reclaim
    reclaim.move(path, 'old_takes', group='song_a', why='uncut takes, the cut is in renders/')   # one move, one run
    run = reclaim.Run('flac', why='...', group='song_a')     # many moves in one run
    run.move(path, why='...', flac='x.flac'); run.finish('WAVs of finished songs, now FLAC beside where they were')

    python -m ismail.reclaim                         # where the reclaim folder is, and its folders with sizes
    python -m ismail.reclaim move <path> <what> [--group G] [--why W]
"""
import json
import os
import re
import shutil
import sys
import time

ENV = 'ISMAIL_RECLAIM'
NAME = '_reclaim'
README = 'README.txt'
PREAMBLE = ("{root}: files moved here for the user to delete. Nothing here is used by any song or agent.\n"
            "Each folder is one kind of thing; delete a whole folder, or keep it. Inside, files keep their path.\n"
            "Space comes back only when a folder is deleted. Every folder has a manifest.json listing each file's "
            "old place.\n")
TRAILER = 'To delete a folder, in PowerShell:\n' if os.name == 'nt' else 'To delete a folder:\n'


def _songs(songs=None):
    if songs:
        return os.path.abspath(songs)
    from .handoffs import SONGS
    return os.path.abspath(SONGS)


def _writable(d):
    """Can d be written, or made? Looks only: nothing is created."""
    while d and not os.path.exists(d):
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up
    return os.path.isdir(d) and os.access(d, os.W_OK)


def default(songs=None):
    """$ISMAIL_RECLAIM, else on Windows <drive of the songs folder>\\_reclaim, else <songs folder>/_reclaim."""
    if os.environ.get(ENV):
        return os.path.abspath(os.environ[ENV])
    songs = _songs(songs)
    if os.name == 'nt':
        drive = os.path.splitdrive(songs)[0]
        if drive:
            return os.path.join(drive + os.sep, NAME)
    return os.path.join(songs, NAME)


def fallback(songs=None):
    return os.path.join(_songs(songs), NAME)


def where(songs=None):
    """-> (the reclaim root, '' or why it is the fallback <songs folder>/_reclaim). Creates nothing."""
    d = default(songs)
    if _writable(d):
        return d, ''
    fb = fallback(songs)
    return fb, f"{d} cannot be created or written, so the reclaim folder is {fb}"


def root(songs=None):
    """The reclaim root: $ISMAIL_RECLAIM if set, else on Windows the songs folder's drive root plus _reclaim
    (D:\\_reclaim), else <songs folder>/_reclaim. When that cannot be created or written: <songs folder>/_reclaim
    (where() also says why)."""
    return where(songs)[0]


def kept_path(path, songs=None, at=None):
    """The path a moved file keeps under its group: from the drive root on Windows (another drive than the reclaim
    root's, at or root(), adds its letter first), from the songs folder's parent on POSIX (the whole path when it
    is elsewhere)."""
    p = os.path.abspath(path)
    if os.name == 'nt':
        drive, rest = os.path.splitdrive(p)
        rest = rest.lstrip('\\/')
        if os.path.normcase(drive) != os.path.normcase(os.path.splitdrive(at or root(songs))[0]):
            rest = os.path.join(re.sub(r'[\\/:]+', '_', drive).strip('_') or 'drive', rest)
        return rest
    base = os.path.dirname(_songs(songs))
    rel = os.path.relpath(p, base)
    return p.lstrip('/') if rel == '..' or rel.startswith('..' + os.sep) else rel


def free(path):
    """path, or <base>_2<ext>, <base>_3<ext>, ... whichever does not exist yet: nothing is ever overwritten."""
    if not os.path.lexists(path):
        return path
    base, ext = os.path.splitext(path)
    if os.path.isdir(path):
        base, ext = path, ''
    i = 2
    while os.path.lexists(f"{base}_{i}{ext}"):
        i += 1
    return f"{base}_{i}{ext}"


def size(path):
    """Bytes under path (a file or a folder); links and junctions are not followed."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total, stack = 0, [path]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    if getattr(st, 'st_file_attributes', 0) & 0x400 or e.is_symlink():
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    else:
                        total += st.st_size
                except OSError:
                    pass
    return total


def _inside(path, folder):
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(folder))
    except ValueError:
        return False
    return rel == '.' or not (rel == '..' or rel.startswith('..' + os.sep) or os.path.isabs(rel))


def _now():
    return time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime())


class Run:
    """Moves into <root>/<what>_<date>/<group>/ that the manifest keeps as one run. Nothing is made until the first
    move, so a dry report can show run.dir."""

    def __init__(self, what, why='', group=None, songs=None, root=None):
        if not re.fullmatch(r'[A-Za-z0-9][\w.-]*', str(what)):
            raise ValueError(f"what={what!r}: a short name of letters, digits, _ . or -")
        self.what, self.why, self.group, self.songs = str(what), why, group, songs
        self.date = time.strftime('%Y%m%d')
        self.t = _now()
        self.id = f"{os.getpid()}-{time.time_ns()}"
        if root:
            self.root, self.note = os.path.abspath(root), ''
        else:
            self.root, self.note = where(songs)
        self.moved = []

    @property
    def folder(self):
        """<root>/<what>_<date>: the folder the manifest and the README entry are for."""
        return os.path.join(self.root, f"{self.what}_{self.date}")

    @property
    def dir(self):
        return os.path.join(self.folder, self.group) if self.group else self.folder

    def target(self, path):
        return os.path.join(self.dir, kept_path(path, self.songs, self.root))

    def _fall_back(self, e):
        fb = fallback(self.songs)
        if os.path.normcase(self.root) == os.path.normcase(fb):
            raise e
        self.note = f"{self.root} cannot be written ({e}), so the reclaim folder is {fb}"
        self.root = fb

    def move(self, path, **extra):
        """Move the file or folder at path to its place in this run (a numeric suffix when the place is taken),
        add it to the manifest, and return its new path."""
        src = os.path.abspath(path)
        if not os.path.lexists(src):
            raise FileNotFoundError(src)
        if _inside(src, self.root) or _inside(self.root, src):
            raise ValueError(f"{src} is the reclaim folder or holds it: nothing to move")
        for attempt in (0, 1):
            dst = free(self.target(src))
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                break
            except OSError as e:
                if attempt:
                    raise
                self._fall_back(e)
        n = size(src)
        shutil.move(src, dst)
        row = {'from': src, 'to': dst, 'bytes': n, **extra}
        self.moved.append(row)
        self._manifest()
        return dst

    def _manifest(self):
        f = os.path.join(self.folder, 'manifest.json')
        data = {'runs': []}
        if os.path.exists(f):
            try:
                with open(f, encoding='utf8') as fh:
                    data = json.load(fh)
                if not isinstance(data.get('runs'), list):
                    raise ValueError('no runs list')
            except (OSError, ValueError, AttributeError):
                os.replace(f, free(os.path.join(self.folder, 'manifest_unreadable.json')))      # kept, not lost
                data = {'runs': []}
        mine = {'t': self.t, 'why': self.why, 'moved': self.moved}
        if self.group:
            mine['group'] = self.group
        for i, r in enumerate(data['runs']):
            if isinstance(r, dict) and r.get('id') == self.id:
                data['runs'][i] = {**mine, 'id': self.id}
                break
        else:
            data['runs'].append({**mine, 'id': self.id})
        tmp = f + '.tmp'
        with open(tmp, 'w', encoding='utf8') as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, f)

    def finish(self, line=None, who='ismail'):
        """Note this run's folder in README.txt (when it moved anything). -> the README path, or None."""
        if not self.moved:
            return None
        return note_readme(self.folder, line or self.why, who=who)


def move(path, what, group=None, why='', **extra):
    """Move the file or folder at path into <root>/<what>_<date>/<group>/<its path from the drive root>, append it to
    that folder's manifest.json as one run, note the folder in README.txt, and return the new path. Never
    overwrites (a numeric suffix), never deletes."""
    run = Run(what, why=why, group=group)
    dst = run.move(path, **extra)
    run.finish(why)
    return dst


# ------------------------------------------------------------------ README.txt

def _read_text(f):
    with open(f, 'rb') as fh:
        raw = fh.read()
    for enc in ('utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8', 'cp1252'):
        try:
            return raw.decode(enc), enc          # written back in the same encoding, a BOM only if it had one
        except UnicodeDecodeError:
            pass
    return raw.decode('utf8', errors='replace'), 'utf8'


def _entry(name, folder, line, who):
    m = re.search(r'_(\d{4})(\d\d)(\d\d)$', name)
    day = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else time.strftime('%Y-%m-%d')
    head = f"{name}{os.sep}".ljust(38) + f"{size(folder) / 1e9:6.2f} GB  ({day}, {who})"
    return head, '  ' + ' '.join(str(line).split())


def note_readme(what_dir, line, who='ismail'):
    """Add or update the entry for <what>_<date>/ in <root>/README.txt: its header (name, size, date, who) and
    first line (what is in it). Other entries, and any further lines of this one, stay as they are. A new README
    starts with the same preamble the Machine Steward's has. -> the README path."""
    what_dir = os.path.abspath(what_dir)
    rd, name = os.path.dirname(what_dir), os.path.basename(what_dir)
    f = os.path.join(rd, README)
    head, desc = _entry(name, what_dir, line, who)
    if os.path.exists(f):
        text, enc = _read_text(f)
    else:
        text, enc = PREAMBLE.format(root=rd) + '\n' + TRAILER, 'utf8'
    nl = '\r\n' if '\r\n' in text else '\n'
    lines = text.replace('\r\n', '\n').split('\n')
    at = next((i for i, ln in enumerate(lines)
               if re.match(re.escape(name) + r'[\\/]?(\s|$)', ln)), None)
    if at is not None:
        lines[at] = head
        if at + 1 < len(lines) and lines[at + 1].startswith((' ', '\t')) and lines[at + 1].strip():
            lines[at + 1] = desc
        else:
            lines.insert(at + 1, desc)
    else:
        tail = next((i for i, ln in enumerate(lines) if ln.startswith('To delete')), None)
        while lines and lines[-1] == '' and tail is None:
            lines.pop()
        block = [head, desc, '']
        if tail is None:
            lines += [''] + block
        else:
            lines[tail:tail] = block
            cmd = f"  Remove-Item -Recurse -Force {what_dir}" if os.name == 'nt' else f"  rm -r '{what_dir}'"
            j = tail + len(block) + 1
            while j < len(lines) and lines[j].startswith(' '):
                j += 1
            if cmd not in lines:
                lines.insert(j, cmd)
    os.makedirs(rd, exist_ok=True)
    out = nl.join(lines)
    if not out.endswith(nl):
        out += nl
    tmp = f + '.tmp'
    with open(tmp, 'w', encoding=enc, newline='') as fh:
        fh.write(out)
    os.replace(tmp, f)
    return f


# ------------------------------------------------------------------ looking

def folders(at=None):
    """[(name, bytes)] of the folders in the reclaim root (at: another root), biggest first."""
    at = at or root()
    try:
        names = [e.name for e in os.scandir(at) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []
    return sorted(((n, size(os.path.join(at, n))) for n in names), key=lambda kv: -kv[1])


def text(at=None):
    at, note = (at, '') if at else where()
    fs = folders(at)
    L = [f"reclaim folder {at}: {sum(b for _, b in fs) / 1e9:.2f} GB in {len(fs)} folders (moved out, waiting for "
         f"the user to delete; nothing here is used)"]
    L += [f"  {b / 1e9:7.2f} GB  {n}" for n, b in fs]
    if note:
        L.append('note: ' + note)
    return '\n'.join(L)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] in ('list', 'root'):
        print(text())
        return 0
    if a[0] == 'move' and len(a) >= 3:
        def opt(k):
            return a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else None
        run = Run(a[2], why=opt('--why') or '', group=opt('--group'))
        dst = run.move(a[1])
        run.finish()
        print(f"moved {a[1]} to {dst} ({run.moved[-1]['bytes'] / 1e6:.1f} MB; never deleted: the user deletes the "
              f"folder {run.folder} when they like)" + (f"\nnote: {run.note}" if run.note else ''))
        return 0
    print(__doc__.strip().splitlines()[-1].strip())
    return 2


if __name__ == '__main__':
    sys.exit(main())
