"""Where a song's sources came from: every recording, video, score or MIDI in its ref/ folders has a row in a
SOURCES file, and the credits of a public piece are written from those rows.

The skill asks for a SOURCES.md row for each source; project_info counts the files and the rows (and rows without a
licence or an author) without warning about the rest, and `credits` writes CREDITS.md
from the rows, the tracks' models and the lineage, the way the first showcase credits page was written by hand.

A file counts as listed when its name, or its name without the extension, appears in any SOURCES file of the song
(SOURCES.md / SOURCES.txt anywhere under ref/, or at the song root). Derived files are skipped: stems, separations,
renders, and analysis projects (a folder with its own project.json).
"""
import os
import re
import urllib.parse

from . import provenance

MEDIA = {'.wav', '.mp3', '.flac', '.ogg', '.oga', '.opus', '.m4a', '.aac', '.aif', '.aiff', '.wma',
         '.mid', '.midi', '.mp4', '.webm', '.mkv', '.mov', '.avi'}
SKIP_DIRS = {'stems', 'separated', 'renders', 'backups', '__pycache__', 'cache'}
SOURCE_NAMES = ('sources.md', 'sources.txt')
LICENCE_KEYS = ('licen',)
AUTHOR_KEYS = ('recordist', 'author', 'artist', 'player', 'performer', 'creator', 'uploader', 'channel', 'by',
               'credit', 'composer', 'editor')
PRIVATE_KEYS = ('approv', 'path', 'local', 'date', 'note')     # columns left out of public credits
EMPTY = {'', '-', '?', 'unknown', 'n/a', 'none'}
ROW = ("title, link, who made or played it, licence, what was measured from it, who approved the download, date")


def song_root(root):
    """The song folder: the project's parent when the project is a song's proj/ (or the parent holds ref/)."""
    root = os.path.abspath(root)
    parent = os.path.dirname(root)
    if os.path.basename(root).lower() == 'proj' or (os.path.isdir(os.path.join(parent, 'ref'))
                                                    and not os.path.isdir(os.path.join(root, 'ref'))):
        return parent
    return root


def ref_dirs(root):
    out = []
    for base in dict.fromkeys([os.path.abspath(root), song_root(root)]):
        p = os.path.join(base, 'ref')
        if os.path.isdir(p):
            out.append(p)
    return out


def _walk(top):
    for dirpath, dirs, files in os.walk(top):
        if 'project.json' in files and dirpath != top:
            dirs[:] = []
            continue
        dirs[:] = [x for x in dirs if x.lower() not in SKIP_DIRS and not x.startswith('.')]
        yield dirpath, files


def _source_files(root):
    found = []
    sr = song_root(root)
    for name in os.listdir(sr) if os.path.isdir(sr) else []:
        if name.lower() in SOURCE_NAMES:
            found.append(os.path.join(sr, name))
    for top in ref_dirs(root):
        for dirpath, files in _walk(top):
            found += [os.path.join(dirpath, f) for f in files if f.lower() in SOURCE_NAMES]
    return list(dict.fromkeys(found))


def _read(p):
    try:
        with open(p, 'rb') as f:
            raw = f.read()
    except OSError:
        return ''
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')          # a SOURCES file written by Windows PowerShell 5.1


def _cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def tables(text, headings=False):
    """Markdown tables in a SOURCES file -> [(header cells, [row cells])], or with headings=True
    [(header cells, [row cells], the heading above the table or None)]."""
    out, lines = [], text.splitlines()
    i, heading = 0, None
    while i < len(lines):
        if lines[i].startswith('#'):
            heading = lines[i].lstrip('#').strip()
        if lines[i].lstrip().startswith('|') and i + 1 < len(lines) and re.match(r'^\s*\|[\s:|-]+\|?\s*$', lines[i + 1]):
            head, rows = _cells(lines[i]), []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append(_cells(lines[i]))
                i += 1
            out.append((head, rows, heading) if headings else (head, rows))
        else:
            i += 1
    return out


def _col(head, keys, avoid=('approv',)):
    for j, h in enumerate(head):
        hl = h.lower()
        words = set(re.findall(r'[a-z]+', hl))
        if any(a in hl for a in avoid):
            continue
        if any((k in words) if len(k) <= 3 else (k in hl) for k in keys):
            return j
    return None


def _label(head, row):
    for k in ('file', 'title', 'recording', 'name', 'work', 'species'):
        for j, h in enumerate(head):
            if k in h.lower() and j < len(row) and row[j].strip():
                return row[j].strip()
    return next((c for c in row if c.strip()), '?')


def scan(root, d=None):
    """-> {'files': [rel paths of source media], 'unlisted': [...], 'sources': [SOURCES paths],
    'incomplete': [(SOURCES rel path, row label, [missing fields])], 'reference': path or None,
    'reference_listed': bool}"""
    sr = song_root(root)
    srcs = _source_files(root)
    blob = '\n'.join(_read(p) for p in srcs)
    blob = (blob + '\n' + urllib.parse.unquote(blob)).lower()
    files, unlisted = [], []
    for top in ref_dirs(root):
        for dirpath, names in _walk(top):
            for f in sorted(names):
                stem, ext = os.path.splitext(f)
                if ext.lower() not in MEDIA:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, f), sr).replace(os.sep, '/')
                files.append(rel)
                if f.lower() not in blob and stem.lower() not in blob:
                    unlisted.append(rel)
    incomplete = []
    for p in srcs:
        for head, rows in tables(_read(p)):
            lic, who = _col(head, LICENCE_KEYS), _col(head, AUTHOR_KEYS)
            for r in rows:
                miss = [name for name, j in (('licence', lic), ('author', who))
                        if j is not None and (j >= len(r) or r[j].strip().lower() in EMPTY)]
                if miss:
                    incomplete.append((os.path.relpath(p, sr).replace(os.sep, '/'), _label(head, r), miss))
    ref = ((d or {}).get('reference') or {}).get('file')
    ref_listed = True
    if ref:
        b = os.path.basename(ref)
        ref_listed = b.lower() in blob or os.path.splitext(b)[0].lower() in blob
    return {'files': files, 'unlisted': unlisted, 'sources': srcs, 'incomplete': incomplete,
            'reference': ref, 'reference_listed': ref_listed}


def _few(xs, n=4):
    return ', '.join(xs[:n]) + (f" and {len(xs) - n} more" if len(xs) > n else '')


def summary(root, d=None):
    """-> (lines for project_info, '' for render). A ref file without a row is not warned about: the row is a
    habit for credits, and `credits` lists what it could not credit when a piece goes public."""
    s = scan(root, d)
    if not s['files'] and not s['reference']:
        return [], ''
    L = []
    n, un = len(s['files']), s['unlisted']
    if n:
        L.append(f"sources: {n} files in ref/, {n - len(un)} with a row in a SOURCES file "
                 f"({len(s['sources'])} SOURCES file{'s' if len(s['sources']) != 1 else ''})")
    if s['incomplete']:
        L.append(f"  {len(s['incomplete'])} rows without a licence or an author: " +
                 _few([f"{lbl} ({'/'.join(m)})" for _, lbl, m in s['incomplete']], 3))
    return L, ''


# ------------------------------------------------------------------ credits

_LOCAL = re.compile(r'^(?:[A-Za-z]:[\\/]|/(?!/))|\\')


def _public(text):
    """A local path becomes its file name; links stay whole."""
    out = []
    for tok in re.split(r'(\s+)', text):
        core = tok.strip('(),;\'"')
        segs = [x for x in re.split(r'[\\/]', core) if x]
        if len(segs) >= 2 and '://' not in core and _LOCAL.search(core):
            tok = tok.replace(core, segs[-1])
        out.append(tok)
    return ''.join(out)


USED_KEYS = ('in the song', 'in song', 'used', 'in the piece')
YES = ('yes', 'used', 'true', 'in the song', 'in song', 'in the piece')
NO = ('no', 'not', 'unused', 'false', 'consulted')


def _says(cell):
    """An 'in the song' cell -> True, False or None. It may be prose: '**in the song**: the kit ...',
    'not yet (measured, unused)'."""
    c = re.sub(r'[*_`]', '', cell).strip().lower()
    if c in ('x', 'y', '✓', '✔'):
        return True
    if c.startswith(YES):
        return True
    if c.startswith(NO) or c in ('', '-'):
        return False
    return None


def _model_ons(tr):
    m = tr.get('model') or {}
    on = m.get('on')
    return [] if not on or on == 'designed' else ([on] if isinstance(on, str) else list(on))


def used_refs(root, d):
    """What the piece is made from, as lowercase strings to match sources against: every track's model (each
    path, folder or note), mimic profile sources, sample notes, the reference file."""
    out = []
    for tr in d.get('tracks', {}).values():
        out += _model_ons(tr)
        inst = tr.get('instrument') or {}
        insts = [inst] + list((inst.get('map') or {}).values()) if inst.get('type') == 'kit' else [inst]
        for i in insts:
            if i.get('type') == 'mimic':
                out.append(provenance._mimic_source(root, i.get('profile', '')))
            if i.get('type') == 'sampler':
                out += [((d.get('sounds') or {}).get(s, {}) or {}).get('note') or ''
                        for s in provenance.sampler_sounds(i)]
    ref = (d.get('reference') or {}).get('file')
    if ref:
        out.append(ref)
    return [urllib.parse.unquote(x).replace('\\', '/').lower() for x in out if x]


def _used_file(rel, refs):
    """A file in ref/ (path from the song root) is used when a model names it, a folder holding it, or its
    name without the extension (a video id that names its stems folder)."""
    rel = rel.lower()
    stem = os.path.splitext(os.path.basename(rel))[0]
    for r in refs:
        path = r.split(' (')[0].strip().rstrip('/')
        if rel == path or rel.startswith(path + '/') or path.endswith('/' + rel) or rel.endswith('/' + path):
            return True
        if len(stem) >= 6 and stem in r:
            return True
    return False


def _row_used(head, row, refs, used_names):
    """-> True, False, or None when nothing says."""
    for j, h in enumerate(head):
        if any(k in h.lower() for k in USED_KEYS) and j < len(row):
            said = _says(row[j])
            if said is not None:
                return said
    text = ' '.join(row).lower()
    if any(n in text for n in used_names):
        return True
    for tok in re.findall(r'[\w.%-]{6,}', text):
        if '.' in tok and os.path.splitext(tok)[1] in MEDIA and any(tok in r for r in refs):
            return True
    return None


def plays_source_audio(d):
    """[(track, why)] for tracks whose sound is audio from a recording (a sample imported or averaged from one,
    or placed audio clips), as opposed to a measurement rebuilt by synthesis."""
    out = []
    for name, tr in d.get('tracks', {}).items():
        if tr.get('audio'):
            out.append((name, f"{len(tr['audio'])} audio clips"))
        inst = tr.get('instrument') or {}
        insts = [inst] + list((inst.get('map') or {}).values()) if inst.get('type') == 'kit' else [inst]
        for i in insts:
            if i.get('type') == 'sampler':
                for s in provenance.sampler_sounds(i):
                    note = ((d.get('sounds') or {}).get(s, {}) or {}).get('note') or ''
                    if note.startswith(('imported from', 'avg of')):
                        out.append((name, f"sample {s}, {_public(note)}"))
    return out


def credits_md(root, d, consulted=False):
    """-> (CREDITS.md text, warnings). Only the sources the piece uses, unless consulted=True."""
    s = scan(root, d)
    refs = used_refs(root, d)
    used_files = [f for f in s['files'] if _used_file(f, refs)]
    # names that mark a row as used: each used file's name and its name without the extension (a video id)
    used_names = set()
    for f in used_files:
        b = os.path.basename(f).lower()
        used_names |= {b} | ({os.path.splitext(b)[0]} if len(os.path.splitext(b)[0]) >= 6 else set())
    for r in refs:                                  # a stems folder named by a video id: ref/x/stems/<id>/drums.wav
        m = re.search(r'stems/([^/]{6,})/', r)
        if m:
            used_names.add(m.group(1))
    decided = any(_model_ons(t) for t in d.get('tracks', {}).values()) or bool(used_files)
    sr = song_root(root)
    title = d.get('name') if (d.get('name') or '').lower() not in ('', 'proj') else os.path.basename(sr)
    warn = []
    if s['unlisted']:
        warn.append(f"{len(s['unlisted'])} files in ref/ have no SOURCES row and are not credited: {_few(s['unlisted'])}")
    if s['reference'] and not s['reference_listed']:
        warn.append(f"the reference {os.path.basename(s['reference'])} has no SOURCES row and is not credited")
    if s['incomplete']:
        warn.append(f"{len(s['incomplete'])} rows lack a licence or an author")
    L = [f"# {title}: credits", '']
    audio = plays_source_audio(d)
    if audio:
        L += ["Parts of this piece play recorded audio: " + '; '.join(f"{t} ({why})" for t, why in audio) + ". "
              "The other sources below were measured, and their sound is rebuilt by synthesis.", '']
    elif s['files'] or s['sources']:
        L += ["This piece contains none of the audio below. ismail measured each source (a sound's pitch, "
              "harmonics and noise, an instrument's response, a player's timing and phrasing) and the piece "
              "rebuilds it with synthesis. These recordings, and the people who made and played them, are where "
              "its sounds come from.", '']
    table_text = ' '.join(' '.join(r) for p in s['sources'] for _, rows in tables(_read(p)) for r in rows).lower()
    main, also, n_used, n_other, n_dup = [], [], 0, 0, 0
    has_column = False
    for p in s['sources']:
        text = _read(p)
        head = next((ln.lstrip('#').strip() for ln in text.splitlines() if ln.startswith('#')), None)
        title = f"## {head or os.path.relpath(os.path.dirname(p), sr).replace(os.sep, '/')}"
        tb = tables(text, headings=True)
        sec_used, sec_other = [], []
        if tb:
            for h, rows, sub in tb:
                if sub and sub != head:                 # a table under its own heading in the same file
                    hdr0 = [f"### {sub}", '']
                else:
                    hdr0 = []
                has_column = has_column or any(any(k in c.lower() for k in USED_KEYS) for c in h)
                keep = [j for j, c in enumerate(h) if not any(k in c.lower() for k in PRIVATE_KEYS + USED_KEYS)]
                hdr = hdr0 + ['| ' + ' | '.join(h[j] for j in keep) + ' |', '|' + '---|' * len(keep)]
                u, o = [], []
                for r in rows:
                    line = '| ' + ' | '.join(_public(r[j]) if j < len(r) else '' for j in keep) + ' |'
                    is_used = _row_used(h, r, refs, used_names)
                    (u if is_used or (is_used is None and not decided) else o).append(line)
                if u:
                    sec_used += hdr + u + ['']
                if o:
                    sec_other += hdr + o + ['']
                n_used += len(u)
                n_other += len(o)
        else:
            for ln in text.splitlines():
                if not ln.strip() or ln.startswith('#'):
                    continue
                first = re.split(r'[\s|]+', ln.strip())[0].lower()
                if len(first) >= 6 and first in table_text:
                    n_dup += 1                      # a plain list repeating a table's row (a SOURCES.txt of ids)
                    continue
                is_used = any(n in ln.lower() for n in used_names) or (len(first) >= 6 and any(first in r for r in refs))
                ((sec_used if is_used or not decided else sec_other)).append(f"- {_public(ln.strip())}")
                n_used, n_other = n_used + (is_used or not decided), n_other + (decided and not is_used)
            for sec in (sec_used, sec_other):
                if sec and sec[-1] != '':
                    sec.append('')
        if sec_used:
            main += [title, ''] + sec_used
        if sec_other:
            also += [title.replace('## ', '### '), ''] + sec_other
    L += main
    if consulted and also:
        L += ['## Also consulted', '', 'Measured or listened to while the piece was made; not in the piece.', ''] + also
    if not decided:
        warn.append("no part names its sources (track_model) and no table has an 'in the song' column: every row "
                    "is credited as used")
    if n_other and not consulted:
        warn.append(f"credited {n_used} sources the piece uses; {n_other} consulted ones left out (consulted=True "
                    f"lists them)")
    if n_dup:
        warn.append(f"credited each source once: {n_dup} lines of a plain list repeat a table's rows")
    groups, open_ = {}, []
    for name, tr in d.get('tracks', {}).items():
        st, txt = provenance.of_track(tr, d, root)
        if st == provenance.UNSTATED:
            open_.append(name)
        else:
            groups.setdefault((st, _public(txt)), []).append(name)
    if groups:
        L += ['## How each part was made', '', '| how | from | parts |', '|---|---|---|']
        L += [f"| {st} | {txt} | {', '.join(names)} |" for (st, txt), names in groups.items()]
        L.append('')
    if open_:
        warn.append(f"{len(open_)} parts have no stated model and are left out of 'How each part was made' "
                    f"({_few(open_)}): track_model(track, on=<the source>) or on='designed'")
    lin = [a.get('name') or os.path.basename(a.get('project', '')) for a in d.get('lineage', [])]
    if lin:
        L += ['## Made from', '', 'A version of ' + ', itself a version of '.join(lin) + '.', '']
    L.append('Made with ismail (https://github.com/newsbubbles/ismail).')
    return '\n'.join(L) + '\n', warn


FOOTER_LEAD = 'Made with ismail, a music studio for AI agents'   # wording reviewed by Marketing (ledger:M254)


def _how_made(d, root):
    """One sentence on how the piece's sound was made, by counts only (no track names, no paths, no people)."""
    tracks = d.get('tracks') or {}
    if not tracks:
        return ''
    audio = {t for t, _ in plays_source_audio(d)}
    measured = designed = 0
    for name, tr in tracks.items():
        if name in audio:
            continue
        st, _ = provenance.of_track(tr, d, root)
        if st == provenance.MEASURED:
            measured += 1
        else:
            designed += 1
    n, synth = len(tracks), measured + designed

    def parts(k):
        return f"{k} part{'' if k == 1 else 's'}"
    how = (f"{measured} modeled on measured recordings, {designed} designed" if measured and designed else
           'all modeled on measured recordings' if measured else 'all designed')
    if not audio:
        return f"{parts(n)}, every sound synthesized ({how}); no recorded audio in the piece."
    if not synth:
        return f"{parts(n)}, all playing recorded audio."
    return (f"{parts(n)}: {len(audio)} play{'s' if len(audio) == 1 else ''} recorded audio, {synth} synthesized "
            f"({how}).")


def footer(root=None, d=None):
    """-> {'text', 'html', 'link', 'how'}: the line a page people may share ends with (ledger:M254): made with
    ismail, the link back, and how the piece's sound was made (counts of parts measured, designed, playing recorded
    audio). No names and no paths, so it is safe on a public page. With no project, the first sentence only."""
    import html as H
    from .tags import SITE_URL
    how = _how_made(d or {}, root) if d else ''
    text = f"{FOOTER_LEAD}: {SITE_URL}" + (f". {how}" if how else '')
    lead = H.escape(FOOTER_LEAD).replace('ismail', f'<a href="{SITE_URL}" target="_blank" rel="noopener">ismail</a>', 1)
    markup = f'<footer class="made-with"><p>{lead}.' + (f' {H.escape(how)}' if how else '') + '</p></footer>'
    return {'text': text, 'html': markup, 'link': SITE_URL, 'how': how}
