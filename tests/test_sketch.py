"""A new person's first session (M110): guide notices someone who has made nothing yet, sketch gives them sound
in minutes on the measured voices, and keeping a sketch makes it the song and ends the first session (2026-10-05:
a creative-AI founder found ismail "good at doing covers but hard to make a new song")."""
import json
import os

import pytest

from ismail import api, handoffs, machine
from ismail import sketch as SK
from ismail.api import OpError


@pytest.fixture(autouse=True)
def cool(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [], raising=False)
    monkeypatch.setenv('ISMAIL_FIRST_SESSION', str(tmp_path / 'home' / 'first_session_done'))
    monkeypatch.setenv('ISMAIL_SAMPLES', str(tmp_path / 'samples'))     # no sample set is on this machine
    for v in ('RHODES_SAMPLES', 'RUSTY_SAMPLES', 'EMILY_SAMPLES'):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(handoffs, 'SONGS', str(tmp_path / 'songs'))
    (tmp_path / 'songs').mkdir()


def test_a_sketch_is_a_tune_not_block_chords():
    pl = SK.plan('a quiet song for a rainy morning', 'piano')
    assert pl['key'] == 'A minor' and pl['bars'] % 4 == 0
    mel = pl['parts']['melody']['notes']
    assert sum(1 for n in mel if n[1] != 0) > len(mel) / 2          # most notes off beat 1
    assert mel[-1][0] == pl['bars'] - 1 and mel[-1][2] % 12 == 9 and mel[-1][3] == 4   # home, held
    lo, hi = SK.REG['melody']
    assert all(lo <= n[2] <= hi for n in mel)
    first = [n[2] for n in mel if n[0] < 4]
    second = [n[2] for n in mel if 4 <= n[0] < 8]
    assert first != second                                            # the second phrase moves
    assert SK.plan('bright and happy', 'band')['key'] == 'C major'
    assert SK.chord('V', 9, 'minor')[1] == [4, 8, 11]                 # E G# B in A minor
    assert SK.chord('F#m', 0, 'major')[1] == [6, 9, 1]
    assert SK.parse_key('Eb major') == (3, 'major') and SK.parse_key('c#m') == (1, 'minor')
    with pytest.raises(SK.SketchError):
        SK.parse_key('H dorian')


def test_guide_opens_with_the_first_session_only_for_someone_new(tmp_path):
    g = api.guide()
    assert g.startswith('FIRST SESSION') and 'sketch(project' in g and 'violin' in g and 'never required' in g
    r = tmp_path / 'songs' / 'old' / 'renders'
    r.mkdir(parents=True)
    (r / 'latest.wav').write_bytes(b'')
    assert not api.guide().startswith('FIRST SESSION')
    (r / 'latest.wav').unlink()
    SK.mark_done()
    assert not api.guide().startswith('FIRST SESSION')
    assert '*violin' in api.voices_list() and '*kit70' in api.voices_list() and '* showcase' in api.voices_list()


def test_sketch_then_keep_makes_the_song_and_ends_the_first_session(tmp_path):
    song = str(tmp_path / 'songs' / 'rain')
    out = api.sketch(song, 'a quiet song for a rainy morning', styles=['piano'], bars=4)
    assert 'a) version 1: solo piano' in out and 'sketch_keep' in out
    sp = os.path.join(song, 'sketches', 'a-piano')
    rendered = os.listdir(os.path.join(sp, 'renders'))
    assert any(f.startswith('sketch_a.') for f in rendered)
    assert api.guide().startswith('FIRST SESSION')                   # a sketch is not a finished song
    out = api.sketch(song, 'slower, with strings', styles='piano', bars=4)
    assert 'b) version 1: solo piano' in out                          # letters go on; versions restart
    with pytest.raises(OpError) as e:
        api.sketch_keep(song, 'z')
    assert 'a-piano' in str(e.value)
    out = api.sketch_keep(song, 'a')
    assert 'First session marked done' in out and 'change just one thing' in out
    with open(os.path.join(song, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    assert set(d['tracks']) == {'melody', 'harmony'} and d['name'] == 'rain' and d['lineage'][0]['project'] == sp
    assert d['objectives'][0]['text'] == 'a quiet song for a rainy morning'
    assert not api.guide().startswith('FIRST SESSION')
    with pytest.raises(OpError) as e:
        api.sketch_keep(song, 'b')
    assert 'replace=True' in str(e.value)
    assert 'b-piano' in api.sketch_keep(song, 'b', replace=True)
    with pytest.raises(OpError):
        api.sketch(song, 'x', styles=['opera'])


TRIP = ('trip-hop, 90 BPM, A minor; dusty breakbeat, crisp hats, ride, deep sub, warm Rhodes chords, clean bluesy '
        'guitar melody; intro, groove, Rhodes-and-ride breakdown, return, fade')


def test_the_brief_is_read_and_what_has_no_voice_is_said():
    """M021 run 1: a trip-hop brief got three fixed styles that matched none of it, and nothing said so."""
    s = SK.read_brief(TRIP)
    assert s['bpm'] == 90 and s['key'] == 'A minor' and s['feel'] == 'break' and s['ride'] and s['crisp']
    assert s['parts'] == {'drums': 'kit70', 'sub': 'sub_bass', 'keys': 'grand_piano', 'melody': 'strat70_clean',
                          'fx': 'crackle'}
    assert s['form'] == ['intro', 'groove', 'breakdown', 'groove', 'outro'] and s['blues'] and s['sevenths']
    assert len(s['said']) == 3 and "samples_fetch('jrhodes3d')" in s['said'][0] and 'grand_piano plays it' in s['said'][0]
    pl = SK.plan_spec(s, TRIP)
    assert pl['bars'] == 20 and pl['bpm'] == 90 and pl['form'] == s['form']
    by_bar = lambda role: {n[0] for n in pl['parts'][role]['notes']}
    assert not by_bar('melody') & set(range(8, 12)) and not by_bar('sub') & set(range(8, 12))   # breakdown
    assert {n[2] for n in pl['parts']['drums']['notes'] if 8 <= n[0] < 12} <= {51, 37, 38, 42, 49, 48, 45, 41}
    assert 51 in {n[2] for n in pl['parts']['drums']['notes']} and 36 not in {
        n[2] for n in pl['parts']['drums']['notes'] if n[0] < 4}                                 # no kick in the intro
    assert all(len(n[2]) == 4 for n in pl['parts']['keys']['notes'])                         # sevenths
    lo = SK.read_brief('lo-fi beat with flute and vocals')
    assert lo['parts']['melody'] == 'violin' and any('vocals left out' in x for x in lo['said'])
    assert 'drums' not in SK.read_brief('house track with piano and strings, no drums')['parts']
    assert SK.read_brief('a quiet song for a rainy morning')['parts'] == {}


def test_the_next_round_takes_the_persons_words():
    base = SK.read_brief(TRIP)
    s, changed = SK.apply_words(base, 'slower, no guitar, add a pad')
    assert s['bpm'] < 90 and 'strat70_clean' not in s['parts'].values() and s['parts']['pad'] == 'cello'
    assert 'no melody' in changed and any(c.startswith('slower') for c in changed)
    assert any('no pad voice' in x for x in s['said'])
    s, changed = SK.apply_words(base, 'in D minor at 100 bpm')
    assert s['key'] == 'D minor' and s['bpm'] == 100 and s['parts'] == base['parts']
    todo, said = SK.specs_for('hmm', base=base)
    assert len(todo) == 3 and 'named nothing to change' in said[-1]


def test_a_brief_sketch_and_a_next_round_from_it(tmp_path):
    song = str(tmp_path / 'songs' / 'trip')
    out = api.sketch(song, 'trip-hop with rhodes and a guitar melody, A minor, 88 BPM', n=1, bars=4)
    assert "FOR YOU: the rhodes voice plays real samples" in out and "SAY TO THE PERSON" in out and 'a) version 1: as asked' in out and 'LUFS' in out
    out = api.sketch(song, 'no guitar, slower', base='a', n=1, bars=4)
    assert 'b) version 1: as asked' in out and 'changed from the base' in out and 'no melody' in out
    with open(os.path.join(song, 'sketches', 'b-as-asked', 'sketch.json'), encoding='utf8') as f:
        assert json.load(f)['bpm'] < 88
    with pytest.raises(OpError):
        api.sketch(song, 'x', base='q')


def test_with_its_samples_here_the_brief_gets_the_real_voices(monkeypatch):
    from ismail import samples
    monkeypatch.setattr(samples, 'path', lambda name: '/somewhere/' + name)
    s = SK.read_brief(TRIP)
    assert s['parts'] == {'drums': 'rusty', 'sub': 'sub_bass', 'keys': 'rhodes', 'melody': 'emily', 'fx': 'crackle'}
    assert s['said'] == []


def test_run_1b_words_are_honoured():
    """M021 run 1b: no hats in the groove, a snare in 'just Rhodes and ride', 'fades out' dropped, 'sparser' the
    same notes and in G major."""
    b = ('trip-hop, 90 BPM, A minor. Dusty breakbeat with crisp hats and a ride, a deep sub, warm Rhodes chords and '
         'a clean guitar bluesy melody. Intro, groove, a breakdown with just Rhodes and ride, the return, then it '
         'fades out.')
    todo, said = SK.specs_for(b)
    a, sp = [SK.plan_spec(s, b, 0, i) for i, (_, s) in enumerate(todo[:2])]
    assert a['form'] == ['intro', 'groove', 'breakdown', 'groove', 'outro']
    drums = a['parts']['drums']['notes']
    assert any(n[2] == 42 for n in drums if 4 <= n[0] < 8)                          # hats in the groove
    assert {n[2] for n in drums if 8 <= n[0] < 12} == {51}                          # the breakdown: ride alone
    assert a['parts']['melody']['voice'] == 'strat70_clean' and 'fx' in a['parts']  # the guitar has the tune; dust
    assert len(sp['parts']['melody']['notes']) < len(a['parts']['melody']['notes'])
    assert all(p[0] in ('i', 'I') for p in (x['progression'] for x in (a, sp)))



def test_a_gentle_church_prelude_is_classical_soft_and_closes_home():
    """M118, the organist's dress rehearsal: 'strings' became one cello unsaid, 'gentle', 'quiet', 'prelude' and
    'church' set no tempo, dynamics or form, the cello sat 17 to 22 dB over the piano tune, and the pop loops ended
    on Em in C."""
    b = 'something gentle for piano and strings, like a quiet prelude I might play in church'
    todo, said = SK.specs_for(b)
    s = todo[0][1]
    assert s['feel'] == 'classical' and s['soft']
    assert s['parts'] == {'harmony': 'grand_piano', 'pad': 'violin', 'counter': 'cello', 'bass': 'contrabass',
                          'melody': 'grand_piano'}
    assert any('violin and cello' in x for x in said) and any('melody (grand_piano)' in x for x in said)
    pls = [SK.plan_spec(sp, b, 0, i, label=label) for i, (label, sp) in enumerate(todo)]
    assert all(pl['bpm'] <= 76 for pl in pls) and pls[1]['bpm'] < pls[0]['bpm'] < pls[2]['bpm']   # sparser slowest
    assert all(pl['form'] == ['groove', 'outro'] for pl in pls)
    assert [pl['cadence'] for pl in pls] == ['V I', 'IV I', 'V I']
    for pl in pls:
        assert pl['progression'][0] == 'I'
        mel = pl['parts']['melody']
        assert all(p['level'] <= mel['level'] - 9 for r, p in pl['parts'].items() if r != 'melody')
        assert max(n[4] for n in mel['notes']) < 76                                  # played softly
    assert SK.read_brief('a hymn in D minor')['feel'] == 'classical'
    assert SK.read_brief('rock band with strings')['parts']['bass'] == 'pbass70'        # a named bass stays


def test_the_first_sketch_comes_first_says_it_plainly_and_is_balanced(tmp_path, monkeypatch):
    """ledger:M151 (first-session spec S-2..S-5) and M150: A was ready at 1:43 but the reply came at ~5 min; the
    three sounded alike; the reply said voice IDs; the tune sat 1 to 13 LU over the parts."""
    import threading
    song = str(tmp_path / 'songs' / 'walk')
    spawned = []
    monkeypatch.setattr(api, '_sketch_spawn', lambda jobs, sd: spawned.append(
        threading.Thread(target=api._sketch_finish, args=(jobs,))) or spawned[-1].start())
    out = api.sketch(song, 'lo-fi beat with flute and gritty vocals', n=2, bars=4)
    say = out.split('SAY TO THE PERSON (read it out as it is):')[1].split('\nFOR YOU')[0]
    assert 'the first is ready now' in say and 'Version 1: a violin plays the tune' in say and 'Version 2: ' in say
    assert 'unlike version 1:' in say and 'flute; that isn' in say and "isn't something I can make yet" in say
    assert 'grand_piano' not in say and 'kit70' not in say and 'voice' not in say.replace('singing voice', '')
    assert 'PLAY VERSION 1 NOW' in out and 'rendering in the background' in out
    spawned[0].join(300)
    got = api.sketch_wait(song, wait=5)
    assert got.count(': ready') == 2 and 'the tune' in got, got
    import json as _j
    for d in os.listdir(os.path.join(song, 'sketches')):
        if d == 'round.json':
            continue
        r = _j.load(open(os.path.join(song, 'sketches', d, 'sketch_ready.json'), encoding='utf8'))
        assert 'sits' in r['balance'] or 'moved it' in r['balance']


@pytest.mark.parametrize('words, bpm, key, dense, soft', [
    ('a bit happier', 93, 'A major', 1, False),          # Marketing's dry run: this changed nothing before
    ('sadder', 81, 'A minor', -1, True),
    ('much more exciting', 106, 'A minor', 1, False),
    ('darker', 86, 'A minor', 0, True),
    ('faster but happier', 101, 'A major', 1, False),     # a named tempo wins over the feeling's
])
def test_feeling_words_move_tempo_mode_density_and_tone(words, bpm, key, dense, soft):
    base = SK.style_spec('piano')
    base.update(bpm=90, key='A minor')
    spec, changed = SK.apply_words(base, words)
    assert (spec['bpm'], spec['key'], spec['dense'], spec['soft']) == (bpm, key, dense, soft)
    assert changed
def test_a_new_person_gets_the_first_session_on_a_machine_with_songs_and_marks_nobody(tmp_path):
    r = tmp_path / 'songs' / 'owners_song' / 'renders'
    r.mkdir(parents=True)
    (r / 'latest.wav').write_bytes(b'')
    g = api.guide()
    assert not g.startswith('FIRST SESSION') and 'guide(new_person=True)' in g.split('\n')[0]
    assert api.guide(new_person=True).startswith('FIRST SESSION')
    song = str(tmp_path / 'songs' / 'guest')
    api.sketch(song, 'a calm piano tune', styles=['piano'], bars=4)
    out = api.sketch_keep(song, 'a')
    assert 'nothing marked' in out and not os.path.exists(SK.marker_path())


def test_plain_words_are_kept_and_versions_play_from_the_song_folder(tmp_path):
    song = str(tmp_path / 'songs' / 'walk')
    api.guide(project=song, first_answer="I just like listening to music on walks")
    assert SK.words_for(song) == 'plain'
    out = api.sketch(song, 'a calm piano tune', styles=['piano'], bars=4)
    say = out.split('SAY TO THE PERSON')[1].split('\na)')[0]
    assert 'Version 1:' in say and 'BPM' not in say and ' major' not in say and ' minor' not in say
    assert any(f.startswith('version 1.') for f in os.listdir(song))           # playable at the song's top
    api.sketch(song, 'a bit happier', base='1', bars=4, n=1, background=False)
    assert any(f.startswith('version 1.') for f in os.listdir(song))           # the new round's version 1
    out = api.sketch_keep(song, '1')
    assert 'Play it now' in out and any(f.startswith('walk.') for f in os.listdir(song))
    nxt = out.split('NEXT:')[1]
    assert 'from bar' not in nxt and 'two bars' not in nxt


def test_a_musician_keeps_keys_and_tempo():
    assert SK.plain_words('faster (93 BPM), A major') == 'faster, brighter'
    assert SK.plain_mood({'bpm': 70, 'key': 'D minor'}) == 'slow and darker'
