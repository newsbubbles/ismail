import os

from ismail import backup


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
    assert (song / '_reclaim' / 'backups' / 'proj_20261008_010000' / 'cache' / 'c.bin').exists()
    assert (b / 'proj_20261008_011500' / 'cache').exists()
    assert backup.trim(str(b), keep_full=2) == []  # the trimmed ones are no longer full


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
    assert '2 backups trimmed' in out and 'would' in out
    assert (b / 'proj_20261008_010000' / 'cache').exists()
