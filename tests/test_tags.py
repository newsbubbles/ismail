"""Every mp3 ismail writes says which ismail made it (hq:D-128): version, commit and skill version, read from the
build itself, and nothing made up when the build cannot say (a pip install has no git and no skill folder)."""
import re

from ismail import tags


def _mp3(tmp_path):
    p = tmp_path / 'x.mp3'
    p.write_bytes(b'\xff\xfb\x90\x00' + bytes(400))      # tags only: mutagen never reads the audio
    return str(p)


def test_a_checkout_stamps_its_version_commit_and_skill(tmp_path):
    with open(tags.REPO + '/pyproject.toml', encoding='utf8') as f:
        v = re.search(r'(?m)^version\s*=\s*"([^"]+)"', f.read()).group(1)
    b = tags.build()
    assert b['version'] == v and re.fullmatch(r'[0-9a-f]{7}(\+local)?', b['commit']) and b['skill']
    p = _mp3(tmp_path)
    tags.tag_mp3(p, title='t')
    t = tags.read_tags(p)
    assert (t['version'], t['commit'], t['skill']) == (b['version'], b['commit'], b['skill'])
    assert t['software'] == f"ismail {v} ({b['commit']}, skill {b['skill']}) {tags.HOME_URL}"
    assert t['url'] == tags.HOME_URL and 'Made with ismail' in t['comment']


def test_a_build_without_git_or_skill_says_only_what_it_knows(tmp_path, monkeypatch):
    monkeypatch.setattr(tags, 'REPO', str(tmp_path / 'site-packages'))      # a pip install: no .git, no skills/
    b = tags.build()
    assert b['commit'] is None and b['skill'] is None
    line = tags.build_line(b)
    assert '()' not in line and 'None' not in line and line.startswith('ismail')
    p = _mp3(tmp_path)
    tags.tag_mp3(p)
    t = tags.read_tags(p)
    assert t['commit'] is None and t['skill'] is None and t['software'].startswith(line)


def test_retagging_replaces_the_stamp_and_an_old_mp3_reads_none(tmp_path, monkeypatch):
    p = _mp3(tmp_path)
    tags.tag_mp3(p)
    monkeypatch.setattr(tags, 'build', lambda: {'version': '9.9.9', 'commit': None, 'skill': None})
    tags.tag_mp3(p)
    from mutagen.id3 import ID3
    assert len(ID3(p).getall('TXXX:ismail_version')) == 1 and ID3(p).getall('TXXX:ismail_commit') == []
    assert tags.read_tags(p)['version'] == '9.9.9'
    old = tmp_path / 'old.mp3'
    old.write_bytes(b'\xff\xfb\x90\x00' + bytes(400))
    assert tags.read_tags(str(old)) == {}


def test_an_empty_artist_writes_no_artist_tag_and_none_means_ismail(tmp_path):
    p = _mp3(tmp_path)
    assert tags.tag_mp3(p)['artist'] == 'ismail' and tags.read_tags(p)['artist'] == 'ismail'
    tags.tag_mp3(p, artist='')                    # a copy whose artist is added by hand later: none, and the old one gone
    from mutagen.id3 import ID3
    assert ID3(p).getall('TPE1') == [] and not tags.read_tags(p).get('artist')
    tags.tag_mp3(p, artist='Someone')
    assert tags.read_tags(p)['artist'] == 'Someone'
