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
    ('what version of ismail is this', 'version'),
    ('show me the difference between my voice and the render', 'exam_picture_round'),
    ('compare two takes by picture with the person marking', 'exam_picture_round'),
    ('formant track of a vowel', 'analyze_formants'),
    ('swing feel of the drums', 'analyze_swing'),
    ('put a yes or no question on the phone', 'phone_ask'),
    ('an on-screen keyboard for someone with no MIDI instrument', 'live_keys_page'),
    ('where does my render differ from the real takes', 'sound_zoom_diff'),
    ('zoom into one word at two resolutions', 'sound_zoom'),
    ('reply to a viewer in the twitch chat', 'chat_say'),
    ('read the stream chat', 'chat_watch'),
    ('go live on youtube and twitch at once', 'stream_go'),
    ('queue a song request from chat', 'request_add'),
    ('the next request for the DJ to play', 'request_next'),
    ('how long until my request plays', 'request_where'),
    ('handle the !request and !vote commands in chat', 'chat_commands'),
    ('limit requests per viewer with a cooldown', 'venue_rules'),
    ('credit the viewers who asked for a song', 'request_credits'),
    ('level of one band in one moment of a sound', 'region_measure'),
    ('is every part of my render in tune', 'analyze_tuning'),
    ('two parts clash a semitone apart', 'notes_clash'),
    ('a weird leftover melody, notes out of key', 'notes_clash'),
    ('can the narrator be heard over the mix', 'analyze_voice_heard'),
    ('speech on the beat, each syllable on a 16th', 'sound_speak'),
    ('what instruments exist', 'kit'),
    ('send the notes to my hardware synth', 'live_midi_out'),
    ('ableton follows the tempo of the set with midi clock', 'live_midi_clock'),
    ('record what I played on my keyboard into a track', 'live_played'),
    ('the knobs on my midi controller move the filter cutoff', 'live_map'),
    ('write the bend lane out as midi pitch bend', 'midi_write'),
    ('arpeggiator on the keys I play live', 'live_arp'),
    ('arpeggiate the chords of a track at render', 'track_arp'),
    ('the lead line when the stem also has a backing part', 'melody_consensus'),
    ('my draft sounds MIDI, mechanical against the reference', 'analyze_feel'),
    ('write the reference timing and velocity into my notes', 'feel_from_ref'),
    ('where are my songs saved', 'config_get'),
    ('change the songs folder', 'config_set'),
    ('set the ffmpeg path', 'config_set'),
    ('point ismail at blender', 'config_set'),
])
def test_a_need_in_plain_words_finds_its_op_first(need, first):
    out = api.OPS['ops_find'](need=need, n=3)
    assert out.splitlines()[1].split()[0] == first, out


def _top(need, n):
    return [l.split()[0] for l in api.OPS['ops_find'](need=need, n=n).splitlines()[1:]]


def test_a_dated_drum_finds_the_kit_and_fit_ops():
    """ledger:M245: the query found phone_sounds and sound_speak (the word 'sound' in their names), and missed the
    ops that give a drum the genre's character. Their own 'Reach for it when' sentences now say it."""
    top = _top('the snare sounds dated, make it sound like the genre', 4)
    assert set(top) == {'analyze_kit', 'sound_extract', 'instrument_fit', 'sound_compare'}, top


def test_a_reference_as_inspiration_finds_where_to_set_it_and_compare():
    top = _top('a reference arrived, use it as inspiration', 3)
    assert top[0] == 'project_set' and 'compare' in top, top


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
