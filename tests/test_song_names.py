"""A bare song name is a folder in the songs setting, for every op and every way in (ledger:M375, Nate 10-27): never
the working folder. '.', '..' and a path with a separator are taken as given. Inside the song's folder the project
is proj/ (SKILL.md's layout, beside build.py), or the folder itself for an older song or a live set (M376)."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ismail import api, settings as S

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def songs(tmp_path, monkeypatch):
    """A settings file whose songs folder is the test's own, and a working folder that is somewhere else."""
    monkeypatch.setenv(S.CONFIG_ENV, str(tmp_path / 'cfg' / 'config.json'))
    for s in S.SETTINGS:
        monkeypatch.delenv(s.env, raising=False)
    d = tmp_path / 'songs'
    d.mkdir()
    api.OPS['config_set']('songs', str(d))
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    return d, work


def test_which_names_are_bare():
    for p in ('tune_a', 'my song', 'a.b'):
        assert S.is_bare_name(p), p
    for p in ('.', '..', './tune_a', 'sub/tune_a', 'sub\\tune_a', 'C:tune_a', 'C:/x', '/abs/x', '~/x', '~x', '', ' ',
              None, 3):
        assert not S.is_bare_name(p), p


def test_a_bare_name_is_made_in_the_songs_folder_and_found_there(songs):
    d, work = songs
    api.OPS['project_new']('tune_a', bpm=120, length_bars=4)
    assert (d / 'tune_a' / 'proj' / 'project.json').is_file() and not (work / 'tune_a').exists()
    assert api.OPS['project_info']('tune_a')                                   # a later op, a library call
    assert api.Project('tune_a').root == str(d / 'tune_a' / 'proj')            # a build.py's Project('name')
    api.OPS['project_new']('tune_b', bpm=100, length_bars=2, derived_from='tune_a')
    assert (d / 'tune_b' / 'proj' / 'project.json').is_file()


def test_a_bare_name_opens_the_songs_proj_or_an_older_songs_own_folder(songs):
    """M376: build.py makes <song>/proj (SKILL.md), so -p <song> opens it; an older song with project.json in its
    own folder still opens there, and a new live set's folder is the song's own (live.md)."""
    d, work = songs
    api.OPS['project_new'](str(d / 'skill_song' / 'proj'), bpm=94, length_bars=4)     # what a build.py does
    (d / 'skill_song' / 'build.py').write_text('# build', encoding='utf-8')
    assert api.Project('skill_song').root == str(d / 'skill_song' / 'proj')
    api.OPS['project_new'](str(d / 'old_song'), bpm=120, length_bars=4)                # an older song's layout
    assert api.Project('old_song').root == str(d / 'old_song')
    assert S.project_dir('a_set', new='') == str(d / 'a_set')
    assert S.project_dir('a_song') == str(d / 'a_song' / 'proj')
    assert api.OPS['project_info']('skill_song').startswith('skill_song:')        # its name, not 'proj'


def test_the_cli_reads_a_bare_name_from_the_songs_folder(songs):
    d, work = songs
    api.OPS['project_new']('tune_a', bpm=120, length_bars=4)
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    r = subprocess.run([sys.executable, '-m', 'ismail', '-p', 'tune_a', 'project_info'], capture_output=True,
                       text=True, cwd=str(work), env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert 'tune_a' in r.stdout and not (work / 'tune_a').exists()


def test_a_path_with_a_separator_and_dot_stay_in_the_working_folder(songs):
    d, work = songs
    api.OPS['project_new']('./tune_c', bpm=120, length_bars=4)
    api.OPS['project_new'](os.path.join('sub', 'tune_d'), bpm=120, length_bars=4)
    assert (work / 'tune_c' / 'project.json').is_file() and (work / 'sub' / 'tune_d' / 'project.json').is_file()
    assert not (d / 'tune_c').exists() and not (d / 'tune_d').exists()
    os.chdir(work / 'tune_c')
    assert api.Project('.').root == str(work / 'tune_c')
    assert 'tune_c' in api.OPS['project_info']('.')


def test_moving_the_songs_folder_moves_where_a_bare_name_lands(songs, tmp_path):
    d, work = songs
    other = tmp_path / 'other_songs'
    other.mkdir()
    api.OPS['config_set']('songs', str(other))
    api.OPS['project_new']('tune_e', bpm=90, length_bars=2)
    assert (other / 'tune_e' / 'proj' / 'project.json').is_file() and not (d / 'tune_e').exists()


def test_a_missing_bare_name_says_where_it_looked(songs):
    d, work = songs
    with pytest.raises(api.OpError) as e:
        api.Project('tune_x')
    msg = str(e.value)
    assert str(d / 'tune_x') in msg and "config_get('songs')" in msg and './tune_x' in msg
    with pytest.raises(api.OpError, match=re.escape(f"no project at {d / 'tune_y'}")):
        api.OPS['live_parity']('tune_y', [1, 2])
