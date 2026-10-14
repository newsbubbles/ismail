"""Dev copies (ismail.worktrees) on a throwaway repository with a bare 'origin': no network, no gh."""
import json
import os
import subprocess

import pytest

from ismail import handoffs, worktrees as wt


def git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, encoding='utf8')
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as f:
        f.write(text)


def commit(cwd, name, text, msg=None):
    write(os.path.join(cwd, name), text)
    git(cwd, 'add', name)
    git(cwd, 'commit', '-q', '-m', msg or f'{name}: {text[:20]}')


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """-> (main checkout, upstream): 'proj' is the shared main checkout, 'upstream' another clone that moves origin
    the way merged pull requests do. Git sees no global or system config."""
    empty = tmp_path / 'gitconfig'
    empty.write_text('')
    for k, v in {'GIT_CONFIG_GLOBAL': str(empty), 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_AUTHOR_NAME': 't',
                 'GIT_AUTHOR_EMAIL': 't@example.com', 'GIT_COMMITTER_NAME': 't',
                 'GIT_COMMITTER_EMAIL': 't@example.com'}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv('ISMAIL_WORKTREES_NOW', raising=False)
    monkeypatch.setattr(wt, '_open_prs', lambda repo: ({}, 'not in tests'))
    origin = str(tmp_path / 'origin.git')
    git(str(tmp_path), 'init', '-q', '--bare', '-b', 'main', origin)
    up = str(tmp_path / 'upstream')
    git(str(tmp_path), 'clone', '-q', origin, up)
    commit(up, '.gitignore', '__pycache__/\n')
    commit(up, 'CHANGELOG.md', '# Changelog\n\n## Unreleased\n')
    commit(up, 'a.py', 'x = 1\n')
    git(up, 'push', '-q', 'origin', 'HEAD:main')
    main = str(tmp_path / 'proj')
    git(str(tmp_path), 'clone', '-q', origin, main)
    return main.replace('\\', '/'), up


def advance(up, name='b.py', text='y = 2\n'):
    """A pull request lands on origin/main from elsewhere."""
    git(up, 'pull', '-q', '--no-rebase', 'origin', 'main')
    commit(up, name, text)
    git(up, 'push', '-q', 'origin', 'HEAD:main')


def merge_pr(up, branch):
    """origin/main takes a pushed branch through a merge commit, as GitHub merges a pull request."""
    git(up, 'fetch', '-q', 'origin')
    git(up, 'merge', '-q', '--ff-only', 'origin/main')
    git(up, 'merge', '-q', '--no-ff', '--no-edit', f'origin/{branch}')
    git(up, 'push', '-q', 'origin', 'HEAD:main')


def copy_of(s, path):
    return next(c for c in s['copies'] if wt._key(c['path']) == wt._key(path))


def test_new_starts_from_origin_main_never_the_local_main_and_records_the_owner(repo):
    main, up = repo
    advance(up)                                          # the local main is now one behind the project
    msg = wt.new('fades', 'stage dev', main)
    folder = os.path.join(os.path.dirname(main), 'proj-wt-fades').replace('\\', '/')
    assert folder in msg and 'owner stage dev' in msg and os.path.isdir(folder)
    assert git(folder, 'rev-parse', 'HEAD') == git(main, 'rev-parse', 'origin/main') != git(main, 'rev-parse', 'HEAD')
    assert git(folder, 'branch', '--show-current') == 'fades'
    with open(os.path.join(wt.common_dir(main), wt.STORE), encoding='utf8') as f:
        rec = json.load(f)[wt._key(folder)]
    assert rec['owner'] == 'stage dev' and rec['topic'] == 'fades'
    with pytest.raises(wt.WorktreeError, match='stage dev already has the topic fades'):
        wt.new('fades', 'someone else', main)
    os.makedirs(os.path.join(os.path.dirname(main), 'proj-wt-taken'))
    with pytest.raises(wt.WorktreeError, match='already exists.*claim'):
        wt.new('taken', 'me', main)
    with pytest.raises(wt.WorktreeError, match='not a usable topic'):
        wt.new('../escape', 'me', main)


def test_list_flags_each_copy_and_the_first_line_names_the_next_action(repo, capsys):
    main, up = repo
    wt.new('merged-one', 'dev a', main)
    wt.new('open-one', 'dev b', main)
    m1 = os.path.join(os.path.dirname(main), 'proj-wt-merged-one')
    o1 = os.path.join(os.path.dirname(main), 'proj-wt-open-one')
    commit(m1, 'm.py', 'm = 1\n')
    git(m1, 'push', '-q', 'origin', 'merged-one')
    commit(o1, 'o.py', 'o = 1\n')
    merge_pr(up, 'merged-one')
    git(main, 'fetch', '-q', 'origin')
    git(main, 'worktree', 'add', '-q', '-b', 'stray', os.path.join(os.path.dirname(main), 'stray'), 'origin/main')
    s = wt.survey(main)
    assert copy_of(s, m1)['merged'] and copy_of(s, m1)['flags'] == ['MERGED']
    assert copy_of(s, o1)['flags'] == ['BEHIND'] and (copy_of(s, o1)['ahead'], copy_of(s, o1)['behind']) == (1, 2)
    stray = copy_of(s, os.path.join(os.path.dirname(main), 'stray'))
    assert stray['flags'] == ['UNASSIGNED'] and stray['empty'] and not stray['merged']   # fresh, not "merged"
    lines = wt.report(s)
    assert lines[0].startswith('1 copy to remove: python -m ismail.worktrees done ') and '1 unassigned' in lines[0]
    assert lines[1].startswith('main checkout is 2 commits behind origin/main (1 merges)')
    assert sum(ln.startswith('  ') for ln in lines) == 3
    # four days on, the unmerged copies are idle (the age comes from a parameter, never a sleep)
    later = wt.survey(main, now=wt._now() + 4 * 86400)
    assert 'IDLE' in copy_of(later, o1)['flags'] and 'IDLE' not in copy_of(later, m1)['flags']
    # --unassigned shows only the copy with no owner; list is a report and exits 0
    assert wt.main(['--repo', main, 'list', '--unassigned']) == 0
    out = capsys.readouterr().out.splitlines()
    assert [ln for ln in out if ln.startswith('  ')] == [ln for ln in out if 'stray' in ln and ln.startswith('  ')]
    assert wt.main(['--repo', os.path.dirname(main), 'list']) == 0       # not a repository: says so, still 0
    assert 'could not look' in capsys.readouterr().out


def test_records_of_vanished_copies_are_pruned_on_list(repo):
    main, _ = repo
    owners = {wt._key('/nowhere/proj-wt-gone'): {'owner': 'ghost'}}
    wt.save_owners(owners, main)
    wt.main(['--repo', main, 'list'])
    assert wt.load_owners(main) == {}


def test_claim_records_an_owner_and_will_not_take_one_over_unasked(repo):
    main, _ = repo
    path = os.path.join(os.path.dirname(main), 'old-copy')
    git(main, 'worktree', 'add', '-q', '-b', 'old', path, 'origin/main')
    assert 'owned by dev a' in wt.claim(path, 'dev a', main)
    assert copy_of(wt.survey(main), path)['owner'] == 'dev a'
    with pytest.raises(wt.WorktreeError, match='owned by dev a.*--reassign'):
        wt.claim(path, 'dev b', main)
    assert '(was dev a)' in wt.claim(path, 'dev b', main, reassign=True)
    with pytest.raises(wt.WorktreeError, match='main checkout'):
        wt.claim(main, 'dev a', main)


def test_done_removes_only_a_merged_clean_copy(repo):
    main, up = repo
    wt.new('feature', 'dev a', main)
    path = os.path.join(os.path.dirname(main), 'proj-wt-feature')
    commit(path, 'f.py', 'f = 1\n')
    with pytest.raises(wt.WorktreeError, match='not merged.*open or finish its pull request, or ask the person'):
        wt.done(path, main)
    git(path, 'push', '-q', 'origin', 'feature')
    merge_pr(up, 'feature')
    write(os.path.join(path, 'notes.txt'), 'mine\n')
    with pytest.raises(wt.WorktreeError, match='notes.txt.*_reclaim/.*never delete'):
        wt.done(path, main)
    assert os.path.exists(os.path.join(path, 'notes.txt'))
    os.replace(os.path.join(path, 'notes.txt'), os.path.join(main, 'notes.txt'))
    write(os.path.join(path, 'a.py'), 'x = 2\n')
    with pytest.raises(wt.WorktreeError, match='uncommitted changes'):
        wt.done(path, main)
    git(path, 'checkout', '-q', '--', 'a.py')
    write(os.path.join(path, '__pycache__', 'f.cpython-311.pyc'), 'cache')     # a cache is not work: it may go
    msg = wt.done(path, main)
    assert 'removed' in msg and 'feature' in msg and not os.path.exists(path)
    assert git(main, 'branch', '--list', 'feature') == ''
    assert wt._key(path) not in wt.load_owners(main)


def test_sync_merges_origin_main_and_keeps_a_conflict_for_its_owner(repo):
    main, up = repo
    wt.new('clean', 'dev a', main)
    wt.new('clash', 'dev b', main)
    clean = os.path.join(os.path.dirname(main), 'proj-wt-clean')
    clash = os.path.join(os.path.dirname(main), 'proj-wt-clash')
    commit(clash, 'a.py', 'x = "mine"\n')
    advance(up, 'a.py', 'x = "theirs"\n')
    code, msg = wt.sync(clean, main)
    assert code == 0 and 'merged origin/main (1 commits)' in msg
    assert open(os.path.join(clean, 'a.py'), encoding='utf8').read() == 'x = "theirs"\n'
    assert wt.sync(clean, main)[1].endswith('already has everything in origin/main')
    write(os.path.join(clash, 'local.txt'), 'untracked is fine\n')
    code, msg = wt.sync(clash, main)
    assert code == 1 and '1 conflicted files' in msg and '\n  a.py\n' in msg
    assert 'explain each conflict to the person' in msg and 'CHANGELOG.md conflicts usually keep both sides' in msg
    assert subprocess.run(['git', 'rev-parse', '--verify', '-q', 'MERGE_HEAD'], cwd=clash).returncode == 0
    with pytest.raises(wt.WorktreeError, match='merge in progress'):
        wt.sync(clash, main)
    git(clash, 'merge', '--abort')
    write(os.path.join(clash, 'a.py'), 'x = "edited"\n')
    with pytest.raises(wt.WorktreeError, match='commit them on clash first.*Never stash'):
        wt.sync(clash, main)


def test_the_intake_shows_the_copies_that_need_something(repo, monkeypatch, capsys, tmp_path):
    main, up = repo
    wt.new('quiet', 'dev a', main)
    git(main, 'worktree', 'add', '-q', '-b', 'stray', os.path.join(os.path.dirname(main), 'stray'), 'origin/main')
    advance(up)
    git(main, 'fetch', '-q', 'origin')
    monkeypatch.setattr(handoffs, 'HERE', main)
    monkeypatch.setattr(handoffs, '_gh', lambda args, cwd: (None, 'not in tests'))
    (tmp_path / 'songs').mkdir()
    handoffs.main(['--songs', str(tmp_path / 'songs')])
    out = capsys.readouterr().out
    section = out[out.index('== dev copies'):]
    assert 'main checkout is 1 commits behind' in section
    assert 'stray' in section and 'UNASSIGNED' in section and 'proj-wt-quiet' in section     # behind: flagged
    wt.claim(os.path.join(os.path.dirname(main), 'stray'), 'dev b', main)
    for p in ('stray', 'proj-wt-quiet'):
        assert wt.sync(os.path.join(os.path.dirname(main), p), main)[0] == 0
    handoffs.main(['--songs', str(tmp_path / 'songs'), '--no-prs'])
    section = capsys.readouterr().out.split('== dev copies')[1]
    assert section.startswith(' (worktrees): all copies owned and current') and 'stray' not in section


def _held(path):
    raise PermissionError(13, 'Permission denied', path)


def test_sweep_removes_only_empty_folders_git_does_not_list(repo, monkeypatch, tmp_path, capsys):
    main, _ = repo
    side = os.path.dirname(main)
    wt.new('kept', 'dev a', main)
    reg = os.path.join(side, 'proj-wt-reg')                  # a registered copy, emptied: git still lists it
    git(main, 'worktree', 'add', '-q', '-b', 'reg', reg, 'origin/main')
    moved = tmp_path / 'moved'
    moved.mkdir()
    for name in os.listdir(reg):
        os.replace(os.path.join(reg, name), str(moved / name))
    assert os.listdir(reg) == []
    for name in ('proj-wt-old', 'proj-other', 'proj-held'):
        os.makedirs(os.path.join(side, name))                # empty leftovers of removed copies
    agent = os.path.join(main, '.claude', 'worktrees', 'agent-1')
    os.makedirs(agent)
    write(os.path.join(side, 'proj-wt-stray', '.hidden'), 'x')   # a hidden file is still a file
    write(os.path.join(side, 'proj-hq', 'notes.md'), 'another project\n')
    wt.save_owners({**wt.load_owners(main), wt._key(os.path.join(side, 'proj-wt-gone')):
                    {'owner': 'ghost', 'path': os.path.join(side, 'proj-wt-gone')}}, main)
    lines = wt.intake_lines(main)
    assert lines[-1] == '4 empty leftover folders next to the main checkout: python -m ismail.worktrees sweep'

    dry = wt.sweep(main, dry_run=True)
    out = wt.sweep_lines(dry)
    assert sorted(wt._key(f['path']) for f in dry['folders'] if f['action'] == 'removed') == sorted(
        wt._key(p) for p in (agent, *(os.path.join(side, n) for n in ('proj-wt-old', 'proj-other', 'proj-held'))))
    assert out[-1] == ('dry run, nothing changed: 4 would be removed, 1 left alone, '
                       '1 owner record would be dropped')
    assert all(os.path.isdir(os.path.join(side, n)) for n in ('proj-wt-old', 'proj-other', 'proj-held'))
    assert len(wt.load_owners(main)) == 2

    real = os.rmdir
    monkeypatch.setattr(wt.os, 'rmdir', lambda p: _held(p) if p.replace(os.sep, '/').endswith('proj-held') else real(p))
    assert wt.main(['--repo', main, 'sweep']) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == 'swept: 3 removed, 1 held, 1 left alone, 1 owner record dropped'
    held = next(ln for ln in out if 'proj-held' in ln)
    assert held.startswith('  HELD') and "often a shell that cd'd there" in held and 'next sweep removes it' in held
    stray = next(ln for ln in out if 'proj-wt-stray' in ln)
    assert 'not a dev copy, left alone' in stray and not any('proj-hq' in ln for ln in out)
    for name in ('proj-wt-old', 'proj-other'):
        assert not os.path.exists(os.path.join(side, name))
    assert not os.path.exists(agent) and os.path.isdir(os.path.join(main, '.claude', 'worktrees'))
    assert os.path.isdir(reg) and os.path.isdir(os.path.join(side, 'proj-held'))
    assert os.listdir(os.path.join(side, 'proj-wt-stray')) == ['.hidden']
    assert os.listdir(os.path.join(side, 'proj-hq')) == ['notes.md']
    assert os.path.isfile(os.path.join(main, 'a.py')) and os.path.isfile(os.path.join(side, 'proj-wt-kept', '.git'))
    assert list(wt.load_owners(main)) == [wt._key(os.path.join(side, 'proj-wt-kept'))]
    monkeypatch.setattr(wt.os, 'rmdir', real)               # the shell left: the next sweep takes it
    res = wt.sweep(main)
    assert [f['action'] for f in res['folders']] == ['removed', 'left']
    assert not os.path.exists(os.path.join(side, 'proj-held'))
    assert wt.main(['--repo', main, 'sweep', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['folders'][0]['path'].endswith('proj-wt-stray')


def test_done_says_held_when_the_copys_empty_folder_stays(repo, monkeypatch):
    main, up = repo
    wt.new('feature', 'dev a', main)
    path = os.path.dirname(main) + '/proj-wt-feature'
    commit(path, 'f.py', 'f = 1\n')
    git(path, 'push', '-q', 'origin', 'feature')
    merge_pr(up, 'feature')
    real_git, real_rmdir = wt._git, os.rmdir

    def windows_git(args, cwd, check=True):
        # as on Windows with a shell inside the copy: git removes the files and its record, not the folder, exit 1
        r = real_git(args, cwd, check)
        if args[:2] == ['worktree', 'remove']:
            os.makedirs(path)
            return subprocess.CompletedProcess(args, 1, '', f"error: failed to delete '{path}': Permission denied\n")
        return r
    monkeypatch.setattr(wt, '_git', windows_git)
    monkeypatch.setattr(wt.os, 'rmdir', _held)
    msg = wt.done(path, main)
    assert msg.startswith(f'removed {path}') and 'local branch feature' in msg
    assert "empty folder is HELD: a program's working folder is inside it" in msg and 'next sweep removes it' in msg
    assert os.path.isdir(path) and git(main, 'branch', '--list', 'feature') == ''
    assert wt._key(path) not in wt.load_owners(main)
    monkeypatch.setattr(wt, '_git', real_git)                # the shell left: the next sweep takes it
    monkeypatch.setattr(wt.os, 'rmdir', real_rmdir)
    assert [f['action'] for f in wt.sweep(main)['folders']] == ['removed'] and not os.path.exists(path)
