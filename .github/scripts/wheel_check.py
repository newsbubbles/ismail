"""An installed ismail works like a clone (ledger:M351). Run with the Python ismail is installed in, from a folder
outside the repository, with ISMAIL_SONGS unset:

    cd /tmp && /tmp/v/bin/python $GITHUB_WORKSPACE/.github/scripts/wheel_check.py

Checks: ismail is imported from the install, not a checkout; its data files are there; the songs folder is not
inside the install; the playbook and every reference page read; guide names the songs folder."""
import os
import sys
import sysconfig

import ismail
from ismail import api, handoffs, playbook

problems = []
pkg = os.path.dirname(os.path.abspath(ismail.__file__))
site = os.path.normcase(os.path.abspath(sysconfig.get_paths()['purelib']))

if handoffs.is_checkout():
    problems.append(f"ismail is imported from a checkout ({pkg}), not the install: run this outside the repository")
for rel in ('kit_tables.json', 'voices/drums/toms_modes.json', 'voices/drums/toms_pitch.json',
            'voices/drums/toms_cal.json', '_playbook/SKILL.md'):
    if not os.path.isfile(os.path.join(pkg, *rel.split('/'))):
        problems.append(f"missing from the package: ismail/{rel}")
if os.path.exists(os.path.join(pkg, '_playbook', 'references', 'LOCAL.md')):
    problems.append("a machine's private references shipped (ismail/_playbook/references/LOCAL.md): build from a "
                    "clean checkout")
if os.environ.get('ISMAIL_SONGS'):
    problems.append("ISMAIL_SONGS is set: unset it, this checks the default")
songs = os.path.normcase(os.path.abspath(handoffs.SONGS))
if songs.startswith(site + os.sep) or songs.startswith(os.path.normcase(pkg)):
    problems.append(f"the songs folder is inside the install: {handoffs.SONGS}")
try:
    if not playbook.read().startswith('---'):
        problems.append("the playbook does not start with the skill's front matter")
    names = playbook.pages()
    if len(names) < 10:
        problems.append(f"only {len(names)} playbook pages: {names}")
    for n in names:
        playbook.read(n)
except LookupError as e:
    problems.append(f"playbook: {e}")
if 'Songs folder: ' + handoffs.SONGS not in api.OPS['guide']():
    problems.append("guide does not name the songs folder")

if problems:
    print('\n'.join(['wheel check FAILED:'] + ['- ' + p for p in problems]))
    sys.exit(1)
print(f"wheel check ok: ismail {pkg}, songs {handoffs.SONGS}, playbook {len(playbook.pages())} pages")
