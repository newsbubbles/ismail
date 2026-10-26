"""What a package install gets (pip, uvx, the plugins) is what a clone has: every engine file ships, the songs
folder is never inside the install, and the playbook comes along (ledger:M351)."""
import glob
import os
import subprocess

import pytest

from ismail import handoffs, playbook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
tomllib = pytest.importorskip('tomllib')


def _tracked(prefix):
    try:
        out = subprocess.run(['git', 'ls-files', prefix], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip('needs git and a checkout')
    return {f for f in out.split() if os.path.isfile(os.path.join(ROOT, f))}


def _setuptools():
    with open(os.path.join(ROOT, 'pyproject.toml'), 'rb') as f:
        return tomllib.load(f)['tool']['setuptools']


def test_every_tracked_engine_file_ships():
    st = _setuptools()
    ships = set()
    for pkg in st['packages']:
        d = os.path.join(ROOT, *pkg.split('.'))
        ships |= set(glob.glob(os.path.join(d, '*.py')))
        for pat in st.get('package-data', {}).get(pkg, []):
            ships |= set(glob.glob(os.path.join(d, pat), recursive=True))
    ships = {os.path.relpath(f, ROOT).replace(os.sep, '/') for f in ships if os.path.isfile(f)}
    missing = sorted(_tracked('ismail') - ships)
    assert not missing, ("these files are in the repository but not in the package (add the folder to packages or "
                         f"a pattern to package-data in pyproject.toml): {missing}")


def test_every_subpackage_is_listed():
    listed = set(_setuptools()['packages'])
    found = {os.path.dirname(f).replace('/', '.') for f in _tracked('ismail') if f.endswith('/__init__.py')}
    assert not found - listed, f"add to [tool.setuptools] packages: {sorted(found - listed)}"


def test_installed_package_keeps_songs_outside_the_install(tmp_path, monkeypatch):
    monkeypatch.delenv('ISMAIL_SONGS', raising=False)
    site = tmp_path / 'site-packages'
    (site / 'ismail').mkdir(parents=True)
    songs = handoffs._songs_default(str(site))
    assert not os.path.normcase(os.path.abspath(songs)).startswith(os.path.normcase(str(site)))
    assert os.path.basename(songs) == 'ismail'


def test_checkout_keeps_its_songs_folder(tmp_path, monkeypatch):
    monkeypatch.delenv('ISMAIL_SONGS', raising=False)
    (tmp_path / 'pyproject.toml').write_text('[project]\nname = "ismail"\n')
    assert handoffs._songs_default(str(tmp_path)) == os.path.join(str(tmp_path), 'songs')
    monkeypatch.setenv('ISMAIL_SONGS', str(tmp_path / 'mine'))
    assert handoffs._songs_default(str(tmp_path)) == str(tmp_path / 'mine')


def test_old_songs_beside_an_install_are_found(tmp_path):
    site = tmp_path / 'site-packages'
    (site / 'songs').mkdir(parents=True)
    assert handoffs.old_songs(str(site)) is None                    # empty: nothing to move
    (site / 'songs' / '_machine').mkdir()
    assert handoffs.old_songs(str(site)) == os.path.normpath(str(site / 'songs'))
    (site / 'pyproject.toml').write_text('')
    assert handoffs.old_songs(str(site)) is None                    # a checkout's songs/ is its home


def test_playbook_reads_the_skill():
    skill = os.path.join(ROOT, 'skills', 'ismail')
    with open(os.path.join(skill, 'SKILL.md'), encoding='utf8') as f:
        assert playbook.read() == f.read()
    with open(os.path.join(skill, 'references', 'live.md'), encoding='utf8') as f:
        text = f.read()
    assert playbook.read('live') == playbook.read('references/live.md') == playbook.read('live.md') == text
    with pytest.raises(LookupError, match='mastering'):
        playbook.read('no_such_page')


def test_playbook_op_and_guide():
    from ismail import api
    assert api.OPS['playbook']().startswith('---')
    g = api.OPS['guide']()
    assert 'Songs folder: ' + handoffs.SONGS in g and 'playbook' in g


def test_playbook_export(tmp_path):
    out = playbook.export(str(tmp_path))
    assert out == os.path.join(str(tmp_path), 'ismail')
    assert os.path.isfile(os.path.join(out, 'SKILL.md'))
    assert set(playbook.pages(out)) == set(playbook.pages())
    assert not os.path.exists(os.path.join(out, 'evals'))


def test_build_and_sdist_ship_the_same_playbook():
    """setup.py reads SHIPPED from playbook.py's text (a build cannot import ismail); MANIFEST.in puts the same
    files in the sdist, so a wheel built from it has them too."""
    import ast
    with open(os.path.join(ROOT, 'ismail', 'playbook.py'), encoding='utf8') as f:
        assert ast.literal_eval(f.read().split('SHIPPED = ', 1)[1].split('\n', 1)[0]) == playbook.SHIPPED
    with open(os.path.join(ROOT, 'MANIFEST.in'), encoding='utf8') as f:
        manifest = f.read()
    for pat in playbook.SHIPPED:
        folder = os.path.dirname(pat)
        assert (f'include skills/ismail/{pat}' in manifest if not folder
                else f'recursive-include skills/ismail/{folder} ' in manifest), pat
