import glob
import json
import os

import pytest

from ismail import backup, reclaim


def _proj(path, cache=b'x' * 100):
    os.makedirs(os.path.join(path, 'cache'))
    os.makedirs(os.path.join(path, 'history'))
    with open(os.path.join(path, 'project.json'), 'w') as f:
        f.write('{}')
    with open(os.path.join(path, 'cache', 'c.bin'), 'wb') as f:
        f.write(cache)


def test_set_aside_and_carry_back(tmp_path):
    proj = tmp_path / 'song' / 'proj'
    _proj(str(proj))
    kept = backup.set_aside(str(proj))
    assert not proj.exists()
    assert os.path.exists(os.path.join(kept['backup'], 'project.json'))
    os.makedirs(proj / 'cache')  # project_new made an empty one
    assert backup.carry_back(kept, str(proj)) == ['cache']
    assert (proj / 'cache' / 'c.bin').exists()
    assert os.path.exists(os.path.join(kept['backup'], 'project.json'))  # the irreplaceable part stays backed up
    assert os.path.isdir(os.path.join(kept['backup'], 'cache_new_empty'))  # never deleted
    assert not os.path.exists(os.path.join(kept['backup'], 'cache'))


def test_set_aside_without_proj(tmp_path):
    assert backup.set_aside(str(tmp_path / 'nope')) is None
    assert backup.carry_back(None, str(tmp_path / 'nope')) == []


def test_carry_back_keeps_a_filled_folder(tmp_path):
    proj = tmp_path / 's' / 'proj'
    _proj(str(proj))
    kept = backup.set_aside(str(proj))
    _proj(str(proj), cache=b'new')
    assert backup.carry_back(kept, str(proj)) == []
    assert (proj / 'cache' / 'c.bin').read_bytes() == b'new'
    assert os.path.exists(os.path.join(kept['backup'], 'cache', 'c.bin'))


def test_trim_moves_old_regen_to_reclaim(tmp_path):
    song = tmp_path / 'song'
    b = song / 'backups'
    for stamp in ('20261008_010000', '20261008_010500', '20261008_011000', '20261008_011500'):
        _proj(str(b / f'proj_{stamp}'))
    os.makedirs(b / 'notes')  # not time-stamped: never touched
    dry = backup.trim(str(b), keep_full=2, dry=True)
    assert [n for n, _ in dry] == ['proj_20261008_010000', 'proj_20261008_010500']
    assert (b / 'proj_20261008_010000' / 'cache').exists()
    moved = backup.trim(str(b), keep_full=2)
    assert [s for _, s in moved] == [100, 100]
    assert not (b / 'proj_20261008_010000' / 'cache').exists()
    assert (b / 'proj_20261008_010000' / 'project.json').exists()
    assert not (song / '_reclaim').exists()                  # the one reclaim folder, not the song's (ledger:M229)
    rec, = glob.glob(os.path.join(reclaim.root(), 'backups_*', 'song'))
    src = str(b / 'proj_20261008_010000' / 'cache' / 'c.bin')
    assert os.path.exists(os.path.join(rec, reclaim.kept_path(src, songs=str(song.parent))))
    with open(os.path.join(os.path.dirname(rec), 'manifest.json'), encoding='utf8') as f:
        runs = json.load(f)['runs']
    assert len(runs) == 1 and [r['backup'] for r in runs[0]['moved']] == ['proj_20261008_010000',
                                                                          'proj_20261008_010500']
    assert (b / 'proj_20261008_011500' / 'cache').exists()
    assert backup.trim(str(b), keep_full=2) == []  # the trimmed ones are no longer full


def test_set_aside_moves_nothing_when_proj_is_held(tmp_path, monkeypatch):
    """A shell inside proj/ refused the move; the copy-then-delete fallback emptied proj/ around it and left the
    reference stems in a backup the next rebuild would trim. Now nothing moves and the error says why."""
    proj = tmp_path / 'song' / 'proj'
    _proj(str(proj))

    def held(src, dst):
        raise PermissionError(32, 'The process cannot access the file because it is being used by another process')
    monkeypatch.setattr(backup.os, 'rename', held)
    with pytest.raises(backup.BackupError, match='Nothing was moved.*cd out of proj'):
        backup.set_aside(str(proj))
    assert (proj / 'project.json').exists() and (proj / 'cache' / 'c.bin').exists()
    assert not os.listdir(tmp_path / 'song' / 'backups')


@pytest.mark.skipif(os.name != 'nt', reason='only Windows refuses to move a folder a process is working in')
def test_set_aside_with_the_working_folder_inside_proj(tmp_path, monkeypatch):
    proj = tmp_path / 'song' / 'proj'
    _proj(str(proj))
    monkeypatch.chdir(proj / 'cache')
    with pytest.raises(backup.BackupError):
        backup.set_aside(str(proj))
    assert (proj / 'project.json').exists() and (proj / 'cache' / 'c.bin').exists()


def test_set_aside_trims_older_full_backups(tmp_path):
    proj = tmp_path / 'song' / 'proj'
    for stamp in ('20261001_000000', '20261002_000000'):
        _proj(str(tmp_path / 'song' / 'backups' / f'proj_{stamp}'))
    _proj(str(proj))
    kept = backup.set_aside(str(proj), keep_full=2)
    assert os.path.exists(os.path.join(kept['backup'], 'cache'))
    assert not (tmp_path / 'song' / 'backups' / 'proj_20261001_000000' / 'cache').exists()
    assert (tmp_path / 'song' / 'backups' / 'proj_20261002_000000' / 'cache').exists()


def test_cli_dry(tmp_path, capsys):
    b = tmp_path / 'backups'
    for stamp in ('20261008_010000', '20261008_010500', '20261008_011000'):
        _proj(str(b / f'proj_{stamp}'))
    assert backup.main(['trim', str(b), '--keep', '1', '--dry']) == 0
    out = capsys.readouterr().out
    assert '2 backups trimmed' in out and 'would' in out and reclaim.root() in out
    assert not os.listdir(reclaim.root())                     # a dry run names the place and makes nothing
    assert (b / 'proj_20261008_010000' / 'cache').exists()
