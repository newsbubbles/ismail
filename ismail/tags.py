"""ID3 tags on every mp3 ismail writes: what it is, who made it, when, and that ismail made it, with the link back
(Nate, 2026-10-06: "make sure that we put ismail with the ismail GitHub link reference in the MP3 ID3 data, because
that's very important for provenance whenever we're shipping out MP3s"). Tags only: the audio is never re-encoded.

Each mp3 also says WHICH ismail made it (hq:D-128): the version, the short git commit (with '+local' when the
package code has uncommitted changes) and the skill's version, read from the build itself at the moment of writing,
so a cold-run ear exam can say "v0.3.0 against v0.3.4". `python -m ismail.tags <file.mp3>` prints them.
"""
import datetime
import os
import re
import subprocess

HOME_URL = 'https://github.com/newsbubbles/ismail'
MADE_WITH = f'Made with ismail ({HOME_URL})'
PKG = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PKG)


def _version():
    """pyproject.toml beside the package in a checkout (an editable install's metadata can lag it), else the
    installed package's metadata."""
    try:
        with open(os.path.join(REPO, 'pyproject.toml'), encoding='utf8') as f:
            m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read())
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        from importlib.metadata import version
        return version('ismail')
    except Exception:          # noqa: BLE001 (not installed as a package: no version to claim)
        return None


def _git(*args):
    try:
        r = subprocess.run(['git', '-C', REPO, *args], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _commit():
    """The short commit of a git checkout, '+local' when the package's own code differs from it; None for a pip
    install (no .git beside the package)."""
    if not os.path.exists(os.path.join(REPO, '.git')):
        return None
    sha = _git('rev-parse', '--short=7', 'HEAD')
    if sha and _git('status', '--porcelain', '--', 'ismail'):
        sha += '+local'
    return sha


def _skill_version():
    try:
        with open(os.path.join(REPO, 'skills', 'ismail', 'SKILL.md'), encoding='utf8') as f:
            head = f.read(4000).split('\n---', 1)[0]
        m = re.search(r'(?m)^\s+version:\s*"?([^"\s]+)"?', head)
        return m.group(1) if m else None
    except OSError:
        return None


def build():
    """Which ismail this is: {version, commit, skill}, each None when the build cannot say."""
    return {'version': _version(), 'commit': _commit(), 'skill': _skill_version()}


def build_line(b=None):
    """'ismail 0.3.0 (1a2b3c4, skill 0.1.0)', leaving out what the build cannot say."""
    b = b or build()
    extra = ', '.join(x for x in (b['commit'], b['skill'] and f"skill {b['skill']}") if x)
    return 'ismail' + (f" {b['version']}" if b['version'] else '') + (f' ({extra})' if extra else '')


def tag_mp3(path, title=None, artist=None, album=None, date=None, comment=None):
    """Write the tags into `path` (an mp3), keeping any other tags it has. title: what it is (default: the file
    name); artist: who made it (default 'ismail'); album: the song or set it belongs to; date: YYYY-MM-DD (default
    today); comment: added after the made-with line. -> the tags written, as a dict."""
    from mutagen.id3 import COMM, ID3, ID3NoHeaderError, TALB, TDRC, TENC, TIT2, TPE1, TSSE, TXXX, WOAS, WXXX
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    title = title or os.path.splitext(os.path.basename(path))[0].replace('_', ' ')
    vals = {'title': title, 'artist': artist or 'ismail', 'album': album, 'date': date or datetime.date.today().isoformat(),
            'comment': MADE_WITH + (f'. {comment}' if comment else '')}
    tags.setall('TIT2', [TIT2(encoding=3, text=vals['title'])])
    tags.setall('TPE1', [TPE1(encoding=3, text=vals['artist'])])
    if album:
        tags.setall('TALB', [TALB(encoding=3, text=album)])
    tags.setall('TDRC', [TDRC(encoding=3, text=vals['date'])])
    tags.setall('TENC', [TENC(encoding=3, text='ismail')])
    b = build()
    vals.update(b)
    tags.setall('TSSE', [TSSE(encoding=3, text=f'{build_line(b)} {HOME_URL}')])
    for k in ('version', 'commit', 'skill'):       # one machine-readable frame each, only what the build can say
        tags.delall(f'TXXX:ismail_{k}')
        if b[k]:
            tags.add(TXXX(encoding=3, desc=f'ismail_{k}', text=b[k]))
    tags.setall('WOAS', [WOAS(url=HOME_URL)])
    tags.setall('WXXX', [WXXX(encoding=3, desc='ismail', url=HOME_URL)])
    tags.setall('COMM', [COMM(encoding=3, lang='eng', desc='', text=vals['comment'])])
    tags.save(path, v2_version=3)                  # ID3v2.3: what phones, players and SoundCloud read best
    return vals


def read_tags(path):
    """The tags ismail cares about, for checks: {title, artist, album, date, encoder, software, url, comment,
    version, commit, skill} (the last three None on an mp3 from before they were written, or a build that could
    not say)."""
    from mutagen.id3 import ID3, ID3NoHeaderError
    try:
        t = ID3(path)
    except ID3NoHeaderError:
        return {}
    g = lambda k: str(t[k].text[0]) if k in t and getattr(t[k], 'text', None) else None   # noqa: E731
    url = t.getall('WOAS')
    comm = t.getall('COMM')
    return {'title': g('TIT2'), 'artist': g('TPE1'), 'album': g('TALB'), 'date': g('TDRC'), 'encoder': g('TENC'),
            'software': g('TSSE'), 'url': url[0].url if url else None,
            'comment': str(comm[0].text[0]) if comm else None,
            **{k: g(f'TXXX:ismail_{k}') for k in ('version', 'commit', 'skill')}}


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print(f'which ismail this is: {build_line()}\nusage: python -m ismail.tags <file.mp3> ...: which ismail made each')
    for p in sys.argv[1:]:
        t = read_tags(p)
        print(f"{p}: {t.get('software') or 'no ismail tags'}" + (f"  [{t['title']}, {t['date']}]" if t else ''))
