"""ledger:M229: every reclaim move goes to one folder at the root of the drive, <root>/_reclaim/<what>_<date>/<group>/
<path from the drive root>, with a manifest.json of runs and a README.txt entry. Never a delete, never an overwrite.
conftest points ISMAIL_RECLAIM at a temp folder for every test: nothing here touches the person's D:\\_reclaim."""
import json
import os
import time

import pytest

from ismail import reclaim


def _file(path, data=b'abc'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    return path


def _manifest(folder):
    with open(os.path.join(folder, 'manifest.json'), encoding='utf8') as f:
        return json.load(f)


def test_root_follows_the_env_then_the_drive_root(monkeypatch, tmp_path):
    assert reclaim.root() == os.environ[reclaim.ENV]
    monkeypatch.delenv(reclaim.ENV)
    songs = str(tmp_path / 'songs')
    if os.name == 'nt':
        assert reclaim.default(songs) == os.path.splitdrive(songs)[0] + '\\_reclaim'
    else:
        assert reclaim.default(songs) == os.path.join(songs, '_reclaim')


def test_move_keeps_the_path_from_the_drive_root(tmp_path):
    src = _file(str(tmp_path / 'songs' / 'a1' / 'backups' / 'x.wav'), b'wave')
    dst = reclaim.move(src, 'song_wavs', group='rebuildable', why='old WAVs', song='a1')
    folder = os.path.join(reclaim.root(), 'song_wavs_' + time.strftime('%Y%m%d'))
    assert dst == os.path.join(folder, 'rebuildable', reclaim.kept_path(src))
    if os.name == 'nt':                                       # D:\ismail\songs\... -> ...\rebuildable\ismail\songs\...
        assert reclaim.kept_path(src) == os.path.splitdrive(src)[1].lstrip('\\')
    assert not os.path.exists(src) and open(dst, 'rb').read() == b'wave'
    run, = _manifest(folder)['runs']
    assert run['why'] == 'old WAVs' and run['t'].endswith('Z') and len(run['t']) == len('2026-10-17T11:42Z')
    assert run['moved'] == [{'from': src, 'to': dst, 'bytes': 4, 'song': 'a1'}]


def test_a_folder_moves_whole(tmp_path):
    d = tmp_path / 'song' / 'proj' / 'cache'
    _file(str(d / 'a.bin'), b'1' * 10)
    _file(str(d / 'sub' / 'b.bin'), b'2' * 5)
    run = reclaim.Run('old_caches', why='caches')
    dst = run.move(str(d))
    assert dst == os.path.join(run.folder, reclaim.kept_path(str(d)))  # no group: straight under the folder
    assert not d.exists() and os.path.isfile(os.path.join(dst, 'sub', 'b.bin'))
    assert _manifest(run.folder)['runs'][0]['moved'][0]['bytes'] == 15


def test_runs_append_and_one_run_holds_many_moves(tmp_path):
    a = _file(str(tmp_path / 's' / 'a.wav'))
    b = _file(str(tmp_path / 's' / 'b.wav'))
    c = _file(str(tmp_path / 's' / 'c.wav'))
    reclaim.move(a, 'takes', group='s', why='first')
    run = reclaim.Run('takes', why='second', group='s')
    run.move(b, note='b')
    run.move(c, note='c')
    runs = _manifest(run.folder)['runs']
    assert [r['why'] for r in runs] == ['first', 'second']
    assert [m['note'] for m in runs[1]['moved']] == ['b', 'c']
    assert [m['from'] for m in runs[0]['moved']] == [a]


def test_nothing_is_ever_overwritten(tmp_path):
    src = str(tmp_path / 's' / 'x.wav')
    first = reclaim.move(_file(src, b'one'), 'takes')
    second = reclaim.move(_file(src, b'two'), 'takes')
    third = reclaim.move(_file(src, b'six'), 'takes')
    assert open(first, 'rb').read() == b'one' and open(second, 'rb').read() == b'two'
    assert second == first[:-4] + '_2.wav' and third == first[:-4] + '_3.wav'
    d = str(tmp_path / 's' / 'cache')
    _file(os.path.join(d, 'a'), b'1')
    one = reclaim.move(d, 'caches')
    _file(os.path.join(d, 'a'), b'2')
    two = reclaim.move(d, 'caches')
    assert two == one + '_2' and open(os.path.join(one, 'a'), 'rb').read() == b'1'
    assert len(_manifest(os.path.join(reclaim.root(), 'takes_' + time.strftime('%Y%m%d')))['runs']) == 3


def test_never_moves_the_reclaim_folder_or_what_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        reclaim.move(str(tmp_path / 'nope.wav'), 'takes')
    inside = _file(os.path.join(reclaim.root(), 'x', 'a.wav'))
    with pytest.raises(ValueError):
        reclaim.move(inside, 'takes')
    assert os.path.exists(inside)
    with pytest.raises(ValueError):
        reclaim.Run('../up')


def test_an_unwritable_root_falls_back_to_the_songs_folder_and_says_so(tmp_path, monkeypatch):
    songs = str(tmp_path / 'songs')
    blocker = _file(str(tmp_path / 'a_file'))                  # a "folder" under a file can never be made
    monkeypatch.setenv(reclaim.ENV, os.path.join(blocker, '_reclaim'))
    at, note = reclaim.where(songs)
    assert at == os.path.join(songs, '_reclaim') and 'cannot be created or written' in note
    run = reclaim.Run('takes', why='t', songs=songs)
    dst = run.move(_file(os.path.join(songs, 'song', 'take.wav')))
    assert dst.startswith(os.path.join(songs, '_reclaim', 'takes_')) and os.path.exists(dst)
    assert run.note == note


def test_a_root_that_fails_at_the_move_falls_back_too(tmp_path, monkeypatch):
    songs = str(tmp_path / 'songs')
    blocker = _file(str(tmp_path / 'a_file'))
    monkeypatch.setenv(reclaim.ENV, os.path.join(blocker, '_reclaim'))
    monkeypatch.setattr(reclaim, '_writable', lambda d: True)  # it looked writable, the write says otherwise
    run = reclaim.Run('takes', why='t', songs=songs)
    assert run.root == os.path.join(blocker, '_reclaim') and not run.note
    dst = run.move(_file(os.path.join(songs, 'song', 'take.wav')))
    assert dst.startswith(os.path.join(songs, '_reclaim')) and os.path.exists(dst)
    assert 'cannot be written' in run.note and os.path.join(songs, '_reclaim') in run.note


STEWARD = """{root}: files moved here for Nate to delete. Nothing here is used by any song or agent.
Each folder is one kind of thing; delete a whole folder, or keep it. Inside, files keep their path.

song_wavs_20261017\\                    20.17 GB  (2026-10-17, Machine Steward, notes)
  Old song WAVs from 36 songs untouched for a week.
  rebuildable\\       17.97 GB  the song has a build script

To delete a folder, in PowerShell:
  Remove-Item -Recurse -Force {root}\\song_wavs_20261017\\rebuildable
"""


def test_readme_adds_and_updates_one_entry_and_leaves_the_others(tmp_path):
    rr = reclaim.root()
    before = STEWARD.format(root=rr)
    with open(os.path.join(rr, 'README.txt'), 'w', encoding='utf8') as f:
        f.write(before)
    run = reclaim.Run('flac', group='g')
    run.move(_file(str(tmp_path / 'x.wav'), b'1' * 2000))
    folder = run.folder
    reclaim.note_readme(folder, 'WAVs now FLAC', who='ismail song_compress')
    reclaim.note_readme(folder, 'WAVs of finished songs, now FLAC', who='ismail song_compress')
    with open(os.path.join(rr, 'README.txt'), encoding='utf8') as f:
        after = f.read()
    name = os.path.basename(folder)
    for line in before.splitlines():                          # every line of the other entries is still there
        assert line in after.splitlines()
    assert after.count(name + os.sep) == 1 and 'WAVs of finished songs, now FLAC' in after
    assert 'WAVs now FLAC\n' not in after
    assert after.index(name) < after.index('To delete a folder')
    assert after.index('song_wavs_20261017') < after.index(name)
    head = next(ln for ln in after.splitlines() if ln.startswith(name))
    day = time.strftime('%Y-%m-%d')
    assert head.endswith(f"GB  ({day}, ismail song_compress)")


def test_a_new_readme_has_the_preamble(tmp_path):
    reclaim.move(_file(str(tmp_path / 'y.wav')), 'takes', why='uncut takes')
    with open(os.path.join(reclaim.root(), 'README.txt'), encoding='utf8') as f:
        text = f.read()
    assert text.startswith(reclaim.root() + ': files moved here') and 'uncut takes' in text
    assert 'To delete a folder' in text


def test_folders_and_cli(tmp_path, capsys):
    src = _file(str(tmp_path / 'z.wav'), b'0' * 100)
    assert reclaim.main(['move', src, 'takes', '--group', 'g', '--why', 'cli']) == 0
    out = capsys.readouterr().out
    assert 'moved' in out and 'never deleted' in out and not os.path.exists(src)
    assert [n for n, _ in reclaim.folders()] == ['takes_' + time.strftime('%Y%m%d')]
    assert reclaim.main([]) == 0
    assert 'takes_' in capsys.readouterr().out
