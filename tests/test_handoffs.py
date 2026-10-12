"""The migration loop's intake: handoff sections new or changed since the last mark, nothing written in songs."""
import json
import os

import pytest

from ismail import handoffs


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """The intake also lists open pull requests through the GitHub command line; tests never reach the network."""
    monkeypatch.setattr(handoffs, '_gh', lambda args, cwd: (None, 'not in tests'))
    monkeypatch.setattr(handoffs, '_report_worktrees', lambda: None)    # tests/test_worktrees.py covers it


def write(p, text):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf8') as f:
        f.write(text)


def test_intake_reports_new_and_changed_sections_until_marked(tmp_path, capsys):
    songs = str(tmp_path / 'songs')
    h = os.path.join(songs, 'birds', 'HANDOFF.md')
    write(h, '# HANDOFF: birds\nintro\n## Call mode\nfirst version\n## Bug\nmono crash\n')
    write(os.path.join(songs, 'birds', 'proj', 'HANDOFF.md'), '# generated, ignored\n')
    rows, _, _ = handoffs.scan(songs)
    assert [(r[1], r[2]) for r in rows] == [('HANDOFF: birds', 'new'), ('Call mode', 'new'), ('Bug', 'new')]
    handoffs.main(['--songs', songs, '--mark'])
    assert handoffs.scan(songs)[0] == []
    write(h, '# HANDOFF: birds\nintro\n## Call mode\nfirst version\nnow with knocks\n## Bug\nmono crash\n## Offsets\nx\n')
    rows, _, _ = handoffs.scan(songs)
    assert [(r[1], r[2]) for r in rows] == [('Call mode', 'changed'), ('Offsets', 'new')]
    handoffs.main(['--songs', songs, '--full'])
    out = capsys.readouterr().out
    assert 'now with knocks' in out and 'first version' not in out     # a changed section shows its added lines
    assert sorted(os.listdir(os.path.join(songs, 'birds'))) == ['HANDOFF.md', 'proj']


def test_one_subheading_under_two_elements_stays_two_sections_and_a_reorganized_file_is_not_new(tmp_path):
    songs = str(tmp_path / 'songs')
    h = os.path.join(songs, 'film', 'HANDOFF.md')
    write(os.path.join(songs, 'film', 'work', 'cache', 'HANDOFF.copy.md'), '# a backup, ignored\n')
    write(h, '# HANDOFF\n## Grade\n### What it is\nnight cam\n## Thumb\n### What it is\nmatte\n'
             '## Lessons\n- **Fade the edges.** A cut mid-sound clicks.\n')
    rows, _, _ = handoffs.scan(songs)
    assert [r[1] for r in rows] == ['Grade > What it is', 'Thumb > What it is', 'Lessons']
    handoffs.main(['--songs', songs, '--mark'])
    write(h, '# HANDOFF\n## Grade\n### What it is\nnight cam\n## Thumb\n### What it is\nmatte\n'
             '## Lessons\n### Fade the edges\nA cut mid-sound clicks.\n### New\nsomething never said before\n')
    kinds = {r[1]: r[2] for r in handoffs.scan(songs)[0]}
    assert kinds['Lessons > Fade the edges'] == 'moved' and kinds['Lessons > New'] == 'new'


def test_open_pull_requests_are_listed_and_marked_by_their_branch(tmp_path, monkeypatch, capsys):
    songs = str(tmp_path / 'songs')
    os.makedirs(os.path.join(songs, '_migration'))
    prs = [{'number': 36, 'title': 'presence', 'headRefName': 'stage-presence', 'headRefOid': 'aaa',
            'author': {'login': 'dev'}, 'isDraft': False, 'mergeable': 'CONFLICTING',
            'statusCheckRollup': [{'name': 'test', 'conclusion': 'SUCCESS'}, {'name': 'lint', 'conclusion': 'FAILURE'}]},
           {'number': 35, 'title': 'wheel', 'headRefName': 'stage-desk', 'headRefOid': 'bbb',
            'author': {'login': 'dev'}, 'isDraft': False, 'mergeable': 'MERGEABLE',
            'statusCheckRollup': [{'name': 'test', 'status': 'IN_PROGRESS'}]}]
    monkeypatch.setattr(handoffs, '_gh', lambda args, cwd: (json.dumps(prs), None))
    handoffs.main(['--songs', songs])
    out = capsys.readouterr().out
    assert '[new] #35 wheel (stage-desk, by dev): checks pending, merges cleanly' in out
    assert '[new] #36 presence (stage-presence, by dev): checks FAIL (lint), CONFLICTS with main' in out
    handoffs.main(['--songs', songs, '--mark'])
    assert 'marked 2 open pull requests as seen' in capsys.readouterr().out
    prs[0]['headRefOid'] = 'ccc'                         # a new commit on #36
    handoffs.main(['--songs', songs])
    out = capsys.readouterr().out
    assert '[updated] #36' in out and '[seen] #35' in out and '1 new or updated' in out


def test_the_intake_says_when_it_could_not_look_at_pull_requests(tmp_path, capsys):
    songs = str(tmp_path / 'songs')
    os.makedirs(songs)
    handoffs.main(['--songs', songs])
    assert "open pull requests: could not look (not in tests); check the repository's pull requests by hand"         in capsys.readouterr().out
