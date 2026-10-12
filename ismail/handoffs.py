"""Intake for the migration loop (skills/ismail/references/development.md): what is new or changed in the songs'
handoff files since the dev agent last looked.

    python -m ismail.handoffs                 # every handoff section new or changed since the last mark, by title
    python -m ismail.handoffs --full          # the same with the new text (changed sections: added lines only)
    python -m ismail.handoffs --mark          # after triage: remember what was seen
    python -m ismail.handoffs --no-prs        # handoffs only (the open pull requests are listed by default)
    python -m ismail.handoffs --no-worktrees  # without the dev copies section (listed by default)

A handoff file is HANDOFF*.md anywhere under songs/ (a song root, notes/, a video folder; not in caches, backups,
renders or a generated proj/) or under the extra folders listed in <songs>/_migration/roots.txt (one path per
line). Sections are split on '#' headings, named by their heading path, and compared by content; a new heading
whose text was already in the file (a handoff reorganized) is listed only with --all. The state lives in
<songs>/_migration/seen.json. Nothing in a song folder is written.

The open pull requests on the repository are listed after the handoffs (number, title, branch, author, checks,
whether it merges cleanly), marked new or updated since the last --mark (<songs>/_migration/prs_seen.json), so a
pull request is never seen only because its author sent word. It uses the GitHub command line (`gh`) when it is
installed and signed in, and otherwise says it could not look.

Last, the dev copies (git worktrees, `ismail.worktrees`): the next action, the main checkout when it is behind
origin/main, and only the copies that need something (no owner, merged, idle, behind), so no dev has to remember
to look. `python -m ismail.worktrees` lists every copy."""
import argparse
import difflib
import glob
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = re.compile(r'[\\/](backups?|_backups|renders|proj|cache|history|history_src|node_modules|\.git)[\\/]')


def _songs_default():
    """$ISMAIL_SONGS, else songs/ of this checkout, else of the main checkout this worktree belongs to."""
    if os.environ.get('ISMAIL_SONGS'):
        return os.environ['ISMAIL_SONGS']
    git = os.path.join(HERE, '.git')
    if os.path.isfile(git):                          # a worktree: '.git' names <main>/.git/worktrees/<name>
        with open(git, encoding='utf8') as f:
            gitdir = f.read().split(':', 1)[1].strip()
        return os.path.join(gitdir.replace(os.sep, '/').split('/.git/')[0], 'songs')
    return os.path.join(HERE, 'songs')


SONGS = os.path.normpath(_songs_default())


def _files(songs):
    roots = [songs]
    extra = os.path.join(songs, '_migration', 'roots.txt')
    if os.path.exists(extra):
        with open(extra, encoding='utf8') as f:
            roots += [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    out = []
    for r in roots:
        for p in glob.glob(os.path.join(r, '**', 'HANDOFF*.md'), recursive=True):
            if not SKIP.search(p):
                out.append(os.path.normpath(p))
    return sorted(set(out))


def _sections(text):
    """[(title, body)]: split on markdown headings. A title is its heading path below the file's top heading
    ('Night-cam grade > Evidence'), so one subheading under two elements stays two sections; text before the first
    heading is '(top)'."""
    out, path, buf, title = [], [], [], '(top)'
    for ln in text.splitlines():
        m = re.match(r'(#{1,4}) (.*)', ln)
        if m:
            if buf and any(x.strip() for x in buf):
                out.append((title, '\n'.join(buf)))
            level = len(m.group(1))
            path = [h for h in path if h[0] < level] + [(level, m.group(2).strip())]
            title, buf = ' > '.join(h for lv, h in path if lv > 1) or path[-1][1], []
        else:
            buf.append(ln)
    if buf and any(x.strip() for x in buf):
        out.append((title, '\n'.join(buf)))
    return out


def _words(body):
    return re.findall(r"[a-z0-9]+(?:['-][a-z0-9]+)*", body.lower())


def _moved(body, known):
    """A section whose words (90% or more) were already in the file: reorganized, not new."""
    w = _words(body)
    return bool(w) and sum(x in known for x in w) >= 0.9 * len(w)


def _digest(s):
    return hashlib.sha1(s.encode('utf8')).hexdigest()[:12]


def scan(songs=SONGS):
    """-> (report rows, new state, state file). Row: (path, title, 'new'|'changed'|'moved', body, old body or None);
    'moved' is a new heading whose words were nearly all in the file already."""
    state_file = os.path.join(songs, '_migration', 'seen.json')
    try:
        with open(state_file, encoding='utf8') as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    rows, state = [], {}
    for p in _files(songs):
        with open(p, encoding='utf8', errors='replace') as f:
            secs = _sections(f.read())
        key = os.path.relpath(p, songs) if p.startswith(os.path.normpath(songs)) else p
        old = seen.get(key, {})
        known = set().union(*(_words(t + ' ' + v['body']) for t, v in old.items())) if old else set()
        state[key] = {}
        for title, body in secs:
            d = _digest(body)
            state[key][title] = {'hash': d, 'body': body}
            if title not in old:
                rows.append((key, title, 'moved' if old and _moved(title + ' ' + body, known) else 'new', body, None))
            elif old[title]['hash'] != d:
                rows.append((key, title, 'changed', body, old[title]['body']))
    return rows, state, state_file


PR_FIELDS = 'number,title,headRefName,headRefOid,author,isDraft,mergeable,statusCheckRollup'


def _gh(args, cwd):
    """Run the GitHub command line; -> (stdout, None) or (None, why it could not)."""
    try:
        r = subprocess.run(['gh'] + args, cwd=cwd, capture_output=True, text=True, encoding='utf8', timeout=60)
    except FileNotFoundError:
        return None, 'the GitHub command line (gh) is not installed'
    except subprocess.TimeoutExpired:
        return None, 'gh did not answer in 60 s'
    if r.returncode:
        msg = (r.stderr or r.stdout or '').strip()
        return None, msg.splitlines()[-1] if msg else f'gh exit {r.returncode}'
    return r.stdout, None


def _checks(rollup):
    """'pass', 'FAIL (names)', 'pending' or 'none' from a pull request's check list."""
    if not rollup:
        return 'none'
    state = [(c.get('conclusion') or c.get('state') or '').upper() for c in rollup]
    bad = [c.get('name') or c.get('context') or '?' for c, st in zip(rollup, state)
           if st in ('FAILURE', 'ERROR', 'CANCELLED', 'TIMED_OUT')]
    if bad:
        return f"FAIL ({', '.join(bad)})"
    return 'pass' if all(st in ('SUCCESS', 'NEUTRAL', 'SKIPPED') for st in state) else 'pending'


def open_prs(songs=SONGS, repo=HERE):
    """-> (rows, state, state file, None or the reason it could not look). Row: (number, 'new'|'updated'|'seen',
    one-line description); a pull request is 'updated' when its branch moved since the last mark."""
    state_file = os.path.join(songs, '_migration', 'prs_seen.json')
    out, why = _gh(['pr', 'list', '--state', 'open', '--limit', '50', '--json', PR_FIELDS], repo)
    if out is None:
        return [], None, state_file, why
    try:
        prs = json.loads(out or '[]')
    except ValueError:
        return [], None, state_file, 'gh answered something that is not JSON'
    try:
        with open(state_file, encoding='utf8') as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    rows, state = [], {}
    for p in sorted(prs, key=lambda x: x['number']):
        n = str(p['number'])
        state[n] = p.get('headRefOid')
        kind = 'new' if n not in seen else ('updated' if seen[n] != p.get('headRefOid') else 'seen')
        who = (p.get('author') or {}).get('login', '?')
        merge = {'MERGEABLE': 'merges cleanly', 'CONFLICTING': 'CONFLICTS with main'}.get(p.get('mergeable'),
                                                                                          'merge state unknown')
        rows.append((p['number'], kind, f"#{n} {p['title']} ({p.get('headRefName')}, by {who}"
                                        f"{', draft' if p.get('isDraft') else ''}): checks "
                                        f"{_checks(p.get('statusCheckRollup'))}, {merge}"))
    return rows, state, state_file, None


def _report_prs(songs, mark):
    rows, state, state_file, why = open_prs(songs)
    if why:
        print(f"\nopen pull requests: could not look ({why}); check the repository's pull requests by hand")
        return
    if mark:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, 'w', encoding='utf8') as f:
            json.dump(state, f, indent=1)
        print(f"marked {len(state)} open pull requests as seen")
        return
    if not rows:
        print("\nopen pull requests: none")
        return
    fresh = sum(r[1] != 'seen' for r in rows)
    print(f"\n== open pull requests ({len(rows)}, {fresh} new or updated since the last mark): review each and say "
          "what it needs; merges are the user's")
    for _, kind, line in rows:
        print(f"  [{kind}] {line}")


def _report_worktrees():
    """The dev copies that need something, from ismail.worktrees (no fetch, no gh: the pull requests are above)."""
    from . import worktrees
    try:
        lines = worktrees.intake_lines(HERE)
    except Exception as e:                           # the intake goes on without it: say so, never fail
        print(f"\ndev copies (worktrees): could not look ({e}); run python -m ismail.worktrees")
        return
    print(f"\n== dev copies (worktrees): {lines[0]}")
    for ln in lines[1:]:
        print(ln if ln.startswith('  ') else f"  {ln}")
    print("  (python -m ismail.worktrees lists every copy; skills/ismail/references/worktrees.md says what to do)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--songs', default=SONGS)
    ap.add_argument('--full', action='store_true', help='print the new text, not only the titles')
    ap.add_argument('--all', action='store_true', help='also list sections whose text only moved')
    ap.add_argument('--mark', action='store_true', help='remember everything as seen (after triage)')
    ap.add_argument('--no-prs', action='store_true', help='skip the open pull requests')
    ap.add_argument('--no-worktrees', action='store_true', help='skip the dev copies (git worktrees)')
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')     # handoffs quote symbols a Windows console code page lacks
    rows, state, state_file = scan(a.songs)
    if a.mark:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, 'w', encoding='utf8') as f:
            json.dump(state, f, indent=1)
        print(f"marked {sum(len(v) for v in state.values())} sections in {len(state)} handoff files as seen")
        if not a.no_prs:
            _report_prs(a.songs, mark=True)
        return
    moved = [r for r in rows if r[2] == 'moved']
    if not a.all:
        rows = [r for r in rows if r[2] != 'moved']
    note = f" ({len(moved)} more only moved: their text was already in the file; --all lists them)" \
        if moved and not a.all else ''
    if not rows:
        print(f"nothing new in {len(state)} handoff files since the last mark{note}")
        if not a.no_prs:
            _report_prs(a.songs, mark=False)
        if not a.no_worktrees:
            _report_worktrees()
        return
    last = None
    for path, title, kind, body, old in rows:
        if path != last:
            print(f"\n== {path}")
            last = path
        n = len([x for x in body.splitlines() if x.strip()])
        print(f"  [{kind}] {title} ({n} lines)")
        if a.full:
            if old is None:
                text = body
            else:
                text = '\n'.join(x[2:] for x in difflib.ndiff(old.splitlines(), body.splitlines()) if x.startswith('+ '))
            print('\n'.join('      ' + x for x in text.splitlines() if x.strip()))
    print(f"\n{len(rows)} sections new or changed{note}. Triage them into "
          f"{os.path.join(a.songs, '_migration', 'LEDGER.md')}, then run with --mark.")
    if not a.no_prs:
        _report_prs(a.songs, mark=False)
    if not a.no_worktrees:
        _report_worktrees()


if __name__ == '__main__':
    sys.exit(main())
