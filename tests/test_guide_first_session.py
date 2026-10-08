"""A new person's first session, the words around it (ledger:M151 G-1, G-2, U-1..U-4): one opening from guide's
FIRST SESSION block, the person's first answer deciding musician or plain words, and setup copy that says what is
coming (dress rehearsals 1 and 2, 2026-10-05 and 10-06)."""
import os
import re

import pytest

from ismail import api, handoffs
from ismail.guide import vocabulary

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*p):
    with open(os.path.join(ROOT, *p), encoding='utf8') as f:
        return f.read()


@pytest.fixture(autouse=True)
def new_person(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_FIRST_SESSION', str(tmp_path / 'home' / 'first_session_done'))
    monkeypatch.setattr(handoffs, 'SONGS', str(tmp_path / 'songs'))
    (tmp_path / 'songs').mkdir()


@pytest.mark.parametrize('said', [
    "Something gentle for church. Yes, I play the organ and I read music.",   # rehearsal 1, the organist
    "I can't read music but I play guitar by ear",
    "classically trained pianist",
    "I used to sing in a choir",
    "I took piano lessons as a kid",
    "I play in a band",
])
def test_a_musician_is_heard_as_one(said):
    kind, words, _ = vocabulary(said)
    assert kind == 'musician' and words and all(w.lower() in said.lower() for w in words)


@pytest.mark.parametrize('said', [
    "a birthday song for my mum with piano and strings",    # names instruments, does not play them
    "for my wedding, no I don't play",
    "I'm not a musician",
    "I don't read music, I don't play",
    "a lullaby I can play the kids at bedtime",
    "a song we can sing at the party",
    "a song about my band of friends",
    "",
])
def test_everyone_else_gets_plain_words(said):
    assert vocabulary(said)[0] == 'plain'


def test_guide_takes_the_first_answer_and_names_the_vocabulary():
    v = api.guide(first_answer="I play the organ at church and read music")
    assert v.startswith('VOCABULARY: musician') and '"play the organ"' in v and 'registrations' in v
    assert 'lexicon_note' in v
    p = api.guide(first_answer="it's for my daughter's wedding")
    assert p.startswith('VOCABULARY: plain words') and 'minutes and seconds' in p
    assert api.guide().startswith('FIRST SESSION')                    # without it, guide is unchanged


def test_one_opening_at_most_two_questions_and_a_calm_wait():
    g = api.guide()
    assert 'At most two questions before any sound' in g and 'never required' in g
    assert 'guide(first_answer=' in g
    assert 'about two minutes' in g                                  # rehearsal 2: sketch A at 1 min 43 s
    skill = read('skills', 'ismail', 'SKILL.md')
    assert 'even if they did not offer one' not in skill              # step 0 no longer asks for a recording first
    assert 'welcome, never required' in skill and 'the one opening' in skill
    for doc in (read('AGENTS.md'), read('README.md'), read('skills', 'ismail', 'references', 'setup.md')):
        assert 'one opening' in doc and 'first_answer' in doc
    assert 'first_answer' in read('skills', 'ismail', 'references', 'user-experience.md')


def test_setup_says_what_is_coming_and_the_windows_truths():
    s = read('skills', 'ismail', 'references', 'setup.md')
    assert '10 to 20 Allow boxes' in s and s.index('Allow boxes') < s.index('/plugin marketplace add')
    assert "GetFolderPath('MyDocuments')" in s and 'setx ISMAIL_SONGS "%USERPROFILE%' not in s
    assert 'Quit Claude from the tray' in s
    assert re.search(r'```\n\s*/plugin marketplace add newsbubbles/ismail\n\s*/plugin install ismail@ismail\n', s)
    for w in ('the install', 'the warm-up', 'the first sketch'):
        assert w in s


@pytest.mark.parametrize('doc', [('AGENTS.md',), ('README.md',), ('skills', 'ismail', 'SKILL.md'),
                                 ('skills', 'ismail', 'references', 'setup.md'),
                                 ('skills', 'ismail', 'references', 'user-experience.md')])
def test_no_dashes_in_the_first_session_docs(doc):
    assert not re.search('[–—]', read(*doc))


def test_a_new_person_on_an_owned_machine_never_writes_the_owners_words(tmp_path, monkeypatch):
    """ledger:M175 (Marketing's dry run 2): a guest's 'plain' became the owner's default, their words went into the
    owner's lexicon, and they were shown the owner's own words."""
    from ismail import sketch as SK
    owner = tmp_path / 'owner_lexicon.jsonl'
    monkeypatch.setenv('ISMAIL_LEXICON', str(owner))
    api.lexicon_note(said='sounds like a kids piano', craft='listener')          # the owner's word
    mark = tmp_path / 'home' / 'first_session_done'
    mark.parent.mkdir(exist_ok=True)
    mark.write_text('done')                                                       # an owned machine
    assert api.guide(new_person=True).count('kept apart') == 1
    api.guide(first_answer="it's for my daughter's wedding")
    assert not (tmp_path / 'home' / 'person.json').exists() and (tmp_path / 'home' / 'pending_person.json').exists()
    assert 'kids piano' not in api.lexicon_note(said='a bit happier', craft='listener')
    song = str(tmp_path / 'songs' / 'wedding')
    assert SK.adopt_pending(song) and SK.is_guest(song) and SK.words_for(song) == 'plain'
    assert not (tmp_path / 'home' / 'pending_person.json').exists()
    assert 'a bit happier' in api.lexicon_view(project=song)                     # their words moved with them
    assert 'kids piano' not in api.lexicon_view(project=song)
    api.lexicon_note(project=song, said='more like a music box', craft='listener')
    assert len(owner.read_text(encoding='utf8').splitlines()) == 1                # the owner's lexicon untouched
    assert SK.words_for(str(tmp_path / 'songs' / 'owners_song')) is None         # and so are their songs' words


def test_the_first_session_has_a_musician_branch_and_a_plain_words_branch():
    g = api.guide()
    assert 'MUSICIAN:' in g and 'ask which is closest or what each is missing' in g      # the musician's rounds stay
    assert '"which one?"' in g and 'change just one thing' in g
    assert 'PLAIN WORDS (guide(first_answer=' in g and 'Want it different? Try:' in g
    p = api.guide(first_answer="it's for my daughter's wedding")
    plain = p.split('PLAIN WORDS (guide(first_answer=')[1]
    for bad in ('closest', 'which one?', 'change just one thing'):
        assert bad not in plain, bad
    assert 'Play version 1 at once with one line of delight' in plain
    assert 'Never ask them to judge it, to say what is missing, or to compare three' in plain
    assert '2 or 3 playful, concrete choices' in plain and 'or tell me anything' in plain
    assert 'new version first' in plain and 'no BPM, key or chord names' in plain
    assert 'Want to keep this as your song?' in plain and 'sketch_keep' in plain
    assert 'Want me to play it live' in plain and 'do not offer them yet' not in plain  # live once they like it
    assert 'show that it changes one piece and keeps the rest' in plain                  # the generator contrast
    # Nate 10-08 (a Codex dress rehearsal ended on a setup report): the opening says why this is different, and
    # every sound is handed over as a file with a question
    assert "I'm a studio you talk to" in g and 'one piece at a time' in g and 'the sounds, the mix' in g
    assert 'not a generator' not in g                                    # no "this isn't X, it's Y" (Marketing 10-08)
    assert 'HAND IT OVER' in g and 'play_file(path)' in g and 'never a setup report' in g
    m = api.guide(first_answer="I play the organ at church and read music")
    assert 'PLAIN WORDS' not in m and 'Want it different' not in m


def test_setup_keeps_trade_words_from_the_person():
    for doc in (read('AGENTS.md'), read('skills', 'ismail', 'references', 'setup.md')):
        assert 'skill, plugin, MCP, uv or server' in doc and 'unless they' in doc
        assert 'Claude Code' in doc and 'Codex' in doc and 'in one sentence' in doc.lower()
    assert 'playful choices' in read('skills', 'ismail', 'references', 'user-experience.md')
