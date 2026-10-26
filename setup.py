"""Only the build steps pyproject.toml cannot say: the playbook (skills/ismail/) goes into the package as
ismail/_playbook/, so an install without a clone reads the same skill (ismail.playbook). In a checkout only the
files git tracks go, in the sdist and the wheel: a machine's private references (references/LOCAL.md and the
pages it lists, kept out by .git/info/exclude) never ship. Everything else is in pyproject.toml."""
import ast
import glob
import os
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

HERE = os.path.dirname(os.path.abspath(__file__))


def _shipped():
    """ismail.playbook.SHIPPED, read from the file: importing ismail needs its dependencies, a build has none."""
    with open(os.path.join(HERE, 'ismail', 'playbook.py'), encoding='utf8') as f:
        return ast.literal_eval(f.read().split('SHIPPED = ', 1)[1].split('\n', 1)[0])


def _tracked():
    """The files git tracks under skills/ismail/ ('/' separated), or None outside a checkout (a build from the
    sdist, which holds only tracked files already)."""
    try:
        out = subprocess.run(['git', 'ls-files', 'skills/ismail'], cwd=HERE, capture_output=True, text=True,
                             check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return set(out.split()) or None


def _ships(rel, tracked):
    rel = rel.replace(os.sep, '/')
    return not rel.startswith('skills/') or tracked is None or rel in tracked


class SdistTracked(sdist):
    def make_release_tree(self, base_dir, files):
        tracked = _tracked()
        super().make_release_tree(base_dir, [f for f in files if _ships(f, tracked)])


class BuildWithPlaybook(build_py):
    def run(self):
        super().run()
        src = os.path.join(HERE, 'skills', 'ismail')
        if not os.path.isfile(os.path.join(src, 'SKILL.md')):
            raise SystemExit("skills/ismail/SKILL.md is missing: an sdist needs MANIFEST.in's skills lines")
        dst = os.path.join(self.build_lib, 'ismail', '_playbook')
        tracked = _tracked()
        for pat in _shipped():
            for f in glob.glob(os.path.join(src, pat)):
                if os.path.isfile(f) and _ships(os.path.relpath(f, HERE), tracked):
                    out = os.path.join(dst, os.path.relpath(f, src))
                    os.makedirs(os.path.dirname(out), exist_ok=True)
                    shutil.copyfile(f, out)


setup(cmdclass={'build_py': BuildWithPlaybook, 'sdist': SdistTracked})
