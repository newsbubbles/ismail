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
    assert 'b) version 2: solo piano' in out                          # letters and version numbers go on
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
    assert 'b) version 2: as asked' in out and 'changed from the base' in out and 'no melody' in out
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
def test_a_part_at_full_scale_is_named_and_the_tune_never_pushed_past_its_ceiling(tmp_path):
    import numpy as np
    import soundfile as sf
    p = str(tmp_path / 'hot')
    api.project_new(p, bpm=120, length_bars=1)
    api.track_add(p, 'melody', instrument={'type': 'synth', 'oscs': [{'wave': 'saw'}]}, volume_db=18.0)
    api.notes_write(p, 'melody', 1, '0 C4 4')
    out = api.render(p, stems=True)
    line = next(l for l in out.splitlines() if l.strip().startswith('melody'))
    assert 'HOT' in line
    sd = tmp_path / 'hot' / 'renders' / 'stems'
    t = np.arange(44100 * 2) / 44100
    sf.write(str(sd / 'melody.wav'), np.stack([0.8 * np.sin(2 * np.pi * 440 * t)] * 2, 1), 44100)
    api.track_add(p, 'pad', instrument={'type': 'synth'})
    sf.write(str(sd / 'pad.wav'), np.stack([0.8 * np.sin(2 * np.pi * 220 * t)] * 2, 1), 44100)
    api.track_set(p, 'melody', volume_db=0.0)
    msg = api._tune_balance(p)
    d = api._load(p)
    assert d.track('melody')['volume_db'] <= 20 * np.log10(1 / 0.8) - 1.0 + 0.1      # its peak stays under -1 dB
    assert d.track('pad')['volume_db'] < 0 and 'the parts' in msg
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
    top = os.listdir(song)                                    # ledger:M181: the pick stays, the new one joins it
    assert any(f.startswith('version 1.') for f in top) and any(f.startswith('version 2.') for f in top)
    out = api.sketch_keep(song, '1')
    assert 'kept version 1' in out and 'Play it now' in out
    assert any(f.startswith('song (version 1).') for f in os.listdir(song))
    nxt = out.split('NEXT:')[1]
    assert 'from bar' not in nxt and 'two bars' not in nxt


def test_a_musician_keeps_keys_and_tempo():
    assert SK.plain_words('faster (93 BPM), A major') == 'faster, brighter'
    assert SK.plain_mood({'bpm': 70, 'key': 'D minor'}) == 'slow and darker'


def test_a_next_round_keeps_the_tune_says_the_words_back_and_names_the_before(tmp_path):
    """ledger:M181 (dry run 2): "a bit happier" came back as two new tunes, one sparser, said as "easy-going and
    bright" with no word of happier, and as version 1 again, which replaced the pick at the top of the folder."""
    song = str(tmp_path / 'songs' / 'intro')
    api.guide(project=song, first_answer="I don't play anything")
    api.sketch(song, 'a calm piano tune', bars=4, background=False)
    out = api.sketch(song, 'a bit happier', base='3', background=False)
    say = out.split('SAY TO THE PERSON')[1].split('\nd)')[0]
    assert 'Version 4: your version 3 with the same tune and chords, happier' in say, say
    assert 'Play version 4 now' in say and 'before and after' not in say and 'BPM' not in say   # plain: new first
    assert 'Want it different? Try: ' in say and 'or tell me anything.' in say
    sd = os.path.join(song, 'sketches')
    spec = {d[0]: json.load(open(os.path.join(sd, d, 'sketch.json'), encoding='utf8')) for d in os.listdir(sd)
            if os.path.isdir(os.path.join(sd, d))}
    assert spec['d']['progression'] == spec['c']['progression']             # the same chords
    mel = {k: api._load(os.path.join(sd, d)).d['tracks']['melody'] for k, d in
           ((d[0], d) for d in os.listdir(sd) if d[0] in 'cd')}
    tune = {k: [n[:3] for n in v['notes']] for k, v in mel.items()}         # pitch and timing; touch may differ
    assert tune['c'] and tune['c'] == tune['d']                                # the same tune
    assert spec['d']['bpm'] > spec['c']['bpm'] and 'then: a bit happier' in spec['d']['objective']
    assert all(f'version {v}.' in ' '.join(os.listdir(song)) for v in (1, 2, 3, 4))
    assert 'version 4 (d-' in api.sketch_wait(song, wait=1)


def test_versions_say_how_they_differ_in_plain_words():
    a = {'key': 'C major', 'bpm': 90, 'bars': 8, 'parts': {'melody': {'voice': 'grand_piano', 'notes': [1] * 20}}}
    b = dict(a, key='F major', parts={'melody': {'voice': 'grand_piano', 'notes': [1] * 12}})
    assert SK.contrast(a, b, plainw=True) == ['pitched higher', 'fewer notes']      # never "brighter" for F major
    assert SK.contrast(a, dict(a, key='A minor'), plainw=True) == ['darker']


def test_a_saloon_piano_is_named_as_missing():
    s = SK.read_brief("an intro for a video; like an old saloon player piano")
    assert s['parts']['keys'] == 'grand_piano' and SK.read_brief('upright bass')['parts']['bass'] == 'contrabass'
    assert any("old saloon player piano; that sound isn't here yet" in x for x in SK.say_plain(s))
    assert any('saloon' in g for g in SK.showcase()['gaps'])


def test_a_busy_machine_says_so_in_plain_words_first(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from ismail import machine

    @contextmanager
    def busy(*a, **kw):
        assert kw.get('wait') == 0.0 or kw.get('wait') is None
        raise machine.MachineBusy("not starting cpu job 'sketch x': the CPU is 90% busy (limit 80%)")
        yield
    monkeypatch.setattr(machine, 'slot', busy)
    with pytest.raises(OpError) as e:
        api.sketch(str(tmp_path / 'x'), 'a calm piano tune', wait='0')
    assert str(e.value).startswith('BUSY: the computer is busy') and '90% busy' in str(e.value)


def test_a_musician_still_gets_the_comparison_rounds(tmp_path):
    song = str(tmp_path / 'songs' / 'organ')
    api.guide(project=song, first_answer="I play the organ and read music")
    assert SK.words_for(song) == 'musician'
    out = api.sketch(song, 'a calm piano tune', styles=['piano'], bars=4)
    assert 'ask which is closest or what each is missing' in out.split('NEXT:')[1]
    assert 'Want it different' not in out and 'have a listen' not in out
    out = api.sketch(song, 'a bit happier', base='1', bars=4, background=False)
    say = out.split('SAY TO THE PERSON')[1].split('\nc)')[0]
    assert 'before and after' in say and 'Want it different' not in say


def _specs_for_many_briefs():
    briefs = ['a calm piano tune', 'lo-fi beat with flute', 'rock band', 'house track', 'a sad hymn in D minor',
              'trip-hop at 80 BPM', 'jazz with a rhodes', 'ambient strings', 'a lullaby', 'funk with bass and guitar',
              'a happy 130 BPM dance track', 'solo violin in A minor', 'a 60 BPM ballad', 'something for a party',
              'cinematic film score with drums', 'upright bass and piano', 'no drums, just piano and cello']
    for b in briefs:
        specs, _ = SK.specs_for(b)
        for label, spec in specs:
            yield b, label, spec, SK.plan_spec(spec, b)


_NOISE = ('said', 'subs', 'unmodelled', 'changed')


def test_every_playful_choice_changes_the_sketch_when_it_comes_back_as_words():
    """hq:D-82: a choice offered to someone on plain words must do something when they say it."""
    seen = set()
    n = 0
    for b, label, spec, pl in _specs_for_many_briefs():
        choices = SK.playful_choices(spec, pl)
        assert 2 <= len(choices) <= 3 and len(set(choices)) == len(choices), (b, choices)
        spec['tune'] = pl['tune']
        for c in choices:
            assert not any(w in c for w in ('BPM', 'major', 'minor', 'key')), c         # everyday words only
            specs, said = SK.specs_for(c, None, None, 2, spec)                          # as the next round runs it
            assert 'named nothing to change' not in ' '.join(said), (b, c)
            assert specs[0][1].get('changed'), (b, c)
            eff = dict(spec, key=spec.get('key') or pl['key'], bpm=spec.get('bpm') or pl['bpm'])
            new, changed = SK.apply_words(eff, c)
            assert changed and {k: v for k, v in new.items() if k not in _NOISE} != \
                {k: v for k, v in eff.items() if k not in _NOISE}, (b, c)
            seen.add(c)
            n += 1
    assert n > 40 and {'add a beat you can nod to', 'sunnier', 'a bit spookier', 'faster and bouncier',
                       'add a cello underneath'} <= seen, seen


def test_choices_come_from_this_sketch():
    quiet = SK.read_brief('a calm solo piano tune')
    quiet['key'], quiet['bpm'] = 'C major', 70
    assert SK.playful_choices(quiet)[:3] == ['add a beat you can nod to', 'faster and bouncier', 'a bit spookier']
    band = SK.read_brief('a rock band in A minor at 120 bpm with a cello')
    got = SK.playful_choices(band)
    assert 'sunnier' in got and 'slower and dreamier' in got and 'add a beat you can nod to' not in got
    assert 'add a cello underneath' not in got                                      # it is already there
    assert SK.choices_line(['a', 'b', 'c']) == 'Want it different? Try: a, b, or c, or tell me anything.'
    assert SK.choices_line(['a', 'b']) == 'Want it different? Try: a or b, or tell me anything.'


def test_plain_words_first_round_plays_version_1_with_delight_and_offers_choices(tmp_path):
    song = str(tmp_path / 'songs' / 'party')
    api.guide(project=song, first_answer="it's for my sister's birthday, I don't play anything")
    assert SK.words_for(song) == 'plain'
    out = api.sketch(song, 'a calm piano tune', n=2, bars=4, background=False)
    say = out.split('SAY TO THE PERSON (read it out as it is):')[1].split('\na)')[0]
    nxt = out.split('NEXT:')[1]
    for text in (say, nxt):
        for bad in ('closest', 'missing', 'compare three'):
            assert bad not in text, (bad, text)
    assert 'BPM' not in say
    assert 'Version 1 is ready now; the other is a spare' in say
    assert say.index("Here's the first one, have a listen.") > say.index('Version 2:')           # after the versions
    assert say.rstrip().endswith('or tell me anything.') and 'Want it different? Try: ' in say
    line = [x for x in say.splitlines() if 'Want it different?' in x][0]
    choices = [c.strip() for c in line.split('Try: ')[1].replace(', or tell me anything.', '').replace(' or ', ', ')
               .split(',') if c.strip()]
    assert 2 <= len(choices) <= 3
    assert 'play version 1 at once' in nxt and 'or tell me anything' in nxt and 'Want to keep this as your song?' in nxt
    # the choice, said back as the next round, plays at once and offers the next choices from the new spec
    out2 = api.sketch(song, choices[0], base='1', bars=4, background=False)
    say2 = out2.split('SAY TO THE PERSON')[1].split('\nc)')[0]
    assert "Here's the new one, have a listen." in say2 and 'Want it different? Try: ' in say2
    assert 'named nothing to change' not in say2
    keep = api.sketch_keep(song, '1')
    nk = keep.split('NEXT:')[1]
    assert 'playful choices' in nk and 'or tell me anything' in nk
    assert 'live changes come later; do not offer them yet' in nk.lower()
    assert 'closest' not in nk and 'before and after' not in nk
