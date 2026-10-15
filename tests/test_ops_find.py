"""ops_find: every op sits on a shelf, every role names real shelves, and a need in plain words finds its op."""
import pytest

from ismail import api
from ismail.api_ops import CATEGORIES, ROLES, category_of


def test_every_op_sits_on_a_shelf():
    """A new op fits no shelf: put it on one in ismail/api_ops.py CATEGORIES, in the pull request that adds it."""
    assert [n for n in api.OPS if category_of(n) is None] == []


def test_every_shelf_has_ops_and_every_role_names_real_shelves():
    shelves = [c[0] for c in CATEGORIES]
    assert len(set(shelves)) == len(shelves)
    for s in shelves:
        assert any(category_of(n) == s for n in api.OPS), s
    for role, cats in ROLES.items():
        assert cats and set(cats) <= set(shelves), role


@pytest.mark.parametrize('need,first', [
    ('show me the difference between my voice and the render', 'exam_picture_round'),
    ('compare two takes by picture with the person marking', 'exam_picture_round'),
    ('formant track of a vowel', 'analyze_formants'),
    ('swing feel of the drums', 'analyze_swing'),
    ('put a yes or no question on the phone', 'phone_ask'),
    ('an on-screen keyboard for someone with no MIDI instrument', 'live_keys_page'),
    ('where does my render differ from the real takes', 'sound_zoom_diff'),
    ('zoom into one word at two resolutions', 'sound_zoom'),
    ('level of one band in one moment of a sound', 'region_measure'),
])
def test_a_need_in_plain_words_finds_its_op_first(need, first):
    out = api.OPS['ops_find'](need=need, n=3)
    assert out.splitlines()[1].split()[0] == first, out


def test_a_role_page_lists_its_shelves_first_and_their_ops():
    out = api.OPS['ops_find'](role='voice')
    shelf_lines = [l for l in out.splitlines()[1:] if not l.startswith(' ')]
    assert [l.split()[0] for l in shelf_lines] == ROLES['voice']
    assert 'exam_picture_round' in out and 'analyze_formants' in out


def test_no_arguments_shows_the_shelves_and_a_wrong_word_points_forward():
    out = api.OPS['ops_find']()
    assert all(c[0] in out for c in CATEGORIES) and 'voice' in out
    with pytest.raises(api.OpError, match='roles: song'):
        api.OPS['ops_find'](role='drummer')
    with pytest.raises(api.OpError, match='categories: start'):
        api.OPS['ops_find'](category='nope')
    assert 'nothing matches' in api.OPS['ops_find'](need='zzqx')
    with pytest.raises(api.OpError, match='ops_find'):
        api.call('no_such_op')
