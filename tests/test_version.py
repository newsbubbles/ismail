"""version: which ismail runs, a source checkout or an installed release, and where its source is."""
import re
from pathlib import Path

from ismail import api

ROOT = Path(__file__).resolve().parents[1]


def test_a_checkout_names_its_pyproject_version_and_where_it_is():
    out = api.version()
    want = re.search(r'^version = "([^"]+)"', (ROOT / 'pyproject.toml').read_text(encoding='utf-8'), re.M).group(1)
    assert out.splitlines()[0] == f'ismail {want}'
    assert 'source checkout' in out and str(ROOT) in out


def test_an_installed_release_points_at_its_tag_and_never_at_editing_the_cache(tmp_path, monkeypatch):
    import importlib.metadata as md
    monkeypatch.setattr(md, 'version', lambda name: '9.8.7')
    pkg = tmp_path / 'site-packages' / 'ismail'
    pkg.mkdir(parents=True)
    monkeypatch.setattr(api, '_install_info', lambda pkg_dir=None, _f=api._install_info: _f(pkg))
    out = api.version()
    assert out.splitlines()[0] == 'ismail 9.8.7'
    assert 'installed release' in out and 'never edit it' in out
    assert 'git clone --branch v9.8.7 https://github.com/newsbubbles/ismail' in out
    assert 'CHANGELOG.md' in out


def test_a_folder_with_no_git_is_a_release_even_beside_a_pyproject(tmp_path, monkeypatch):
    import importlib.metadata as md
    monkeypatch.setattr(md, 'version', lambda name: '1.2.3')
    (tmp_path / 'pyproject.toml').write_text('version = "0.0.1"\n', encoding='utf-8')
    (tmp_path / 'ismail').mkdir()
    ver, kind, where, git = api._install_info(tmp_path / 'ismail')
    assert (ver, kind, git) == ('1.2.3', 'release', None)
