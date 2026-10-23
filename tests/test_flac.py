"""Finished songs as FLAC (ledger:M221): readers find <name>.flac when <name>.wav is gone, song_compress writes FLAC
bit for bit and only moves (never deletes), and latest.wav is a hard link to the named render that a later render
never writes through. Synthetic audio in temp folders; nothing here renders a project."""
import contextlib
import json
import glob
import os
import re

import numpy as np
import pytest
import soundfile as sf

from ismail import api, api_cmp, api_compress, api_measure, audiofile, machine, reclaim as rc
from ismail.render import write_renders

SR = 22050


def tone(seconds=1.0, f=220.0, ch=2, seed=0):
    t = np.arange(int(seconds * SR)) / SR
    rng = np.random.default_rng(seed)
    y = 0.4 * np.sin(2 * np.pi * f * t) + 0.05 * rng.standard_normal(len(t))
    return np.stack([y] * ch, 1) if ch > 1 else y


def wav(path, y, subtype='PCM_24'):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sf.write(path, y, SR, subtype=subtype)
    return path


def flacify(path):
    """The FLAC song_compress would leave: same samples, the WAV gone from its folder (moved, as the op does)."""
    y, sr = sf.read(path, dtype='int32')
    sf.write(path[:-4] + '.flac', y, sr, format='FLAC', subtype=sf.info(path).subtype)
    os.replace(path, path + '.moved')


@pytest.fixture
def slots(monkeypatch):
    """The op's CPU slot, recorded instead of taken: the test machine's board is not touched."""
    taken = []

    @contextlib.contextmanager
    def slot(kind, what, **kw):
        taken.append((kind, what, kw))
        yield

    monkeypatch.setattr(machine, 'slot', slot)
    return taken


# ------------------------------------------------------------------ find and listing

def test_find_prefers_the_wav_then_the_flac_then_says_missing(tmp_path):
    d = str(tmp_path)
    w, f = os.path.join(d, 'a.wav'), os.path.join(d, 'a.flac')
    assert audiofile.find(w) == w                           # neither: the reader's own "missing" error still fires
    open(f, 'wb').close()
    assert audiofile.find(w) == f                           # flac only
    open(w, 'wb').close()
    assert audiofile.find(w) == w                           # both: the wav is the newer render
    os.replace(f, os.path.join(d, 'a.moved'))
    assert audiofile.find(w) == w                           # wav only
    assert audiofile.find(os.path.join(d, 'b.mp3')) == os.path.join(d, 'b.mp3')


def test_listing_gives_one_entry_per_name_from_both_extensions(tmp_path):
    d = str(tmp_path)
    for n in ('one.wav', 'one.flac', 'two.flac', 'three.wav', 'notes.txt'):
        open(os.path.join(d, n), 'wb').close()
    got = audiofile.listing(d)
    assert list(got) == ['one', 'three', 'two']
    assert got['one'].endswith('one.wav') and got['two'].endswith('two.flac')
    assert audiofile.listing(os.path.join(d, 'nowhere')) == {}


# ------------------------------------------------------------------ the read sites with FLAC-only stems

def project_with_stems(tmp_path, flac=True):
    """songs/song_a/proj with a reference and its four stems, rendered stems for two tracks and a melody, and a
    latest.wav; with flac=True every stem is FLAC only."""
    root = str(tmp_path / 'songs' / 'song_a' / 'proj')
    api.project_new(root, bpm=120, length_bars=2)
    ref = wav(os.path.join(root, 'ref', 'ref_a.wav'), tone(2.0))
    sd = os.path.join(root, 'stems', 'ref_a')
    for i, s in enumerate(api_cmp.STEMS):
        wav(os.path.join(sd, s + '.wav'), tone(2.0, 110 * (i + 1), seed=i))
    rs = os.path.join(root, 'renders', 'stems')
    wav(os.path.join(rs, 'part_a.wav'), tone(2.0, 330, seed=5))
    wav(os.path.join(rs, 'part_b.wav'), tone(2.0, 165, seed=6))
    wav(os.path.join(rs, 'melody.wav'), tone(2.0, 440, seed=7))
    wav(os.path.join(root, 'renders', 'latest.wav'), tone(2.0, 220, seed=8))
    with open(os.path.join(root, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    d['reference'] = {'file': ref, 'stems_dir': sd}
    with open(os.path.join(root, 'project.json'), 'w', encoding='utf8') as f:
        json.dump(d, f)
    if flac:
        for folder in (sd, rs):
            for n in os.listdir(folder):
                if n.endswith('.wav'):
                    flacify(os.path.join(folder, n))
    return root, sd, rs


def test_sources_resolve_to_flac_stems(tmp_path):
    root, sd, rs = project_with_stems(tmp_path)
    P = api._load(root)
    assert P.resolve_audio('ref:drums') == os.path.join(sd, 'drums.flac')
    assert P.resolve_audio('track:part_a') == os.path.join(rs, 'part_a.flac')
    assert os.path.normpath(P.resolve_audio('renders/stems/melody.wav')) == os.path.join(rs, 'melody.flac')
    with pytest.raises(api.OpError) as e:
        P.resolve_audio('ref:keys')
    assert "have: ['bass', 'drums', 'other', 'vocals']" in str(e.value)


def test_compare_reads_flac_stems_on_both_sides(tmp_path):
    root, sd, rs = project_with_stems(tmp_path)
    P = api._load(root)
    ref = api_cmp.ref_stems(P)
    assert all(ref[s].endswith('.flac') for s in api_cmp.STEMS)
    P.d['stem_map'] = {'part_a': 'other', 'part_b': 'bass'}
    P.d['tracks'] = {'part_a': {}, 'part_b': {}}
    mine = api_cmp.render_stems(P, 'tracks')
    y, _ = sf.read(mine['other'])
    assert np.abs(y).max() > 0.1                           # the FLAC stem was read into its group


def test_swing_and_grid_read_the_flac_drum_stem(tmp_path, monkeypatch):
    root, sd, rs = project_with_stems(tmp_path)
    read = []
    monkeypatch.setattr(api_measure.A, 'swing', lambda path, bpm, off: (read.append(path) or ({}, 'swing: 0')))
    api_measure.analyze_swing(root)
    assert read[-1] == os.path.join(sd, 'drums.flac')
    monkeypatch.setattr(api.A, 'beat_grid', lambda path, hint, bpb: ({'bpm': 120.0, 'offset_sec': 0.0}, 'grid'))
    txt = api.analyze_grid(root, source='ref:drums')
    assert 'swing: 0' in txt and read[-1] == os.path.join(sd, 'drums.flac')


def test_melody_balance_reads_flac_stems(tmp_path, monkeypatch):
    root, sd, rs = project_with_stems(tmp_path)
    gap = api._tune_gap(root)
    assert not isinstance(gap, str), gap                     # weighed, not 'no tune and parts to weigh'
    monkeypatch.setattr(api, 'HOT_DB', -100.0)               # any tune it can read counts as hot
    assert api._tune_hot(root) != 0.0


def test_retune_reads_flac_stems_and_writes_wav(tmp_path):
    root, sd, rs = project_with_stems(tmp_path)
    txt = api_measure.ref_retune(root, cents=30)
    nd = api._load(root).d['reference']['stems_dir']
    assert nd != sd and 'drums.wav' in txt
    assert sorted(f for f in os.listdir(nd)) == sorted(s + '.wav' for s in api_cmp.STEMS)


def test_a_flac_made_from_a_windowed_render_keeps_its_window(tmp_path):
    root, sd, rs = project_with_stems(tmp_path)
    w = {'bars': [2, 2], 'start_sec': 2.0}
    with open(os.path.join(root, 'renders', api.WINDOWS_FILE), 'w', encoding='utf8') as f:
        json.dump({'stems/part_a.wav': w}, f)
    assert api._load(root).render_window(os.path.join(rs, 'part_a.flac')) == w


# ------------------------------------------------------------------ latest.wav as a hard link

def test_latest_links_to_the_named_render_and_a_later_render_never_writes_through(tmp_path):
    rd = str(tmp_path / 'renders')
    a, b, c = (tone(1.0, f, seed=k).T for k, f in enumerate((220, 330, 440)))
    assert write_renders(rd, a, SR, 'v1') == {'v1.wav': 'written', 'latest.wav': 'linked'}
    v1, latest = os.path.join(rd, 'v1.wav'), os.path.join(rd, 'latest.wav')
    assert os.path.samefile(v1, latest)
    before = sf.read(v1, dtype='int32')[0]
    write_renders(rd, b, SR)                                 # an unnamed render: latest.wav only
    assert not os.path.samefile(v1, latest)
    assert np.array_equal(sf.read(v1, dtype='int32')[0], before)
    write_renders(rd, c, SR, 'v2')                           # another named render
    assert os.path.samefile(latest, os.path.join(rd, 'v2.wav'))
    assert np.array_equal(sf.read(v1, dtype='int32')[0], before)
    write_renders(rd, a, SR, 'v2')                           # the same name again: v2 changes, v1 does not
    assert os.path.samefile(latest, os.path.join(rd, 'v2.wav'))
    assert np.array_equal(sf.read(os.path.join(rd, 'v2.wav'), dtype='int32')[0], before)
    assert np.array_equal(sf.read(v1, dtype='int32')[0], before)
    assert not [f for f in os.listdir(rd) if f.endswith(('.writing', '.link'))]


# ------------------------------------------------------------------ song_compress

def song(tmp_path):
    """songs/song_b/proj: 16- and 24-bit stems, a float stem, a named render with latest.wav a copy of it, a
    reference stem, and a song script that opens a .wav."""
    songs = tmp_path / 'songs'
    root = str(songs / 'song_b' / 'proj')
    api.project_new(root, bpm=120, length_bars=2)
    rd, rs = os.path.join(root, 'renders'), os.path.join(root, 'renders', 'stems')
    data = {
        os.path.join(rs, 'part_16.wav'): (tone(3.0, 220, seed=1), 'PCM_16'),
        os.path.join(rs, 'part_24.wav'): (tone(3.0, 330, seed=2), 'PCM_24'),
        os.path.join(rs, 'part_mono.wav'): (tone(3.0, 110, ch=1, seed=3), 'PCM_24'),
        os.path.join(rd, 'take_a.wav'): (tone(3.0, 440, seed=4), 'PCM_24'),
        os.path.join(root, 'stems', 'ref_b', 'drums.wav'): (tone(3.0, 60, seed=5), 'PCM_16'),
    }
    for p, (y, sub) in data.items():
        wav(p, y, sub)
    wav(os.path.join(rs, 'part_float.wav'), tone(1.0, 550, seed=6), 'FLOAT')
    with open(os.path.join(rd, 'take_a.wav'), 'rb') as f, open(os.path.join(rd, 'latest.wav'), 'wb') as g:
        g.write(f.read())
    with open(os.path.join(root, 'project.json'), encoding='utf8') as f:
        d = json.load(f)
    d['reference'] = {'file': os.path.join(root, 'ref.wav'), 'stems_dir': os.path.join(root, 'stems', 'ref_b')}
    with open(os.path.join(root, 'project.json'), 'w', encoding='utf8') as f:
        json.dump(d, f)
    os.makedirs(songs / 'song_b' / 'work')
    (songs / 'song_b' / 'work' / 'build.py').write_text("import soundfile as sf\ny = sf.read('mix.wav')\n",
                                                        encoding='utf8')
    return str(songs), root, data


def reclaim(songs):
    """<reclaim root>/flac_<date>/song_b: the WAVs go to the one reclaim folder, never under songs/ (ledger:M229)."""
    assert not os.path.exists(os.path.join(songs, '_reclaim'))
    found = glob.glob(os.path.join(rc.root(), 'flac_*', 'song_b'))
    assert len(found) == 1, found
    return found[0]


def tree(folder):
    return sorted(os.path.relpath(os.path.join(r, f), folder) for r, _, fs in os.walk(folder) for f in fs)


def test_dry_by_default_reports_and_touches_nothing(tmp_path, slots):
    songs, root, data = song(tmp_path)
    before = tree(songs)
    txt = api.OPS['song_compress'](root)
    assert tree(songs) == before and not slots               # nothing written, moved or queued
    assert re.search(r'files: 4 WAV to FLAC, [\d.]+ MB now, about [\d.]+ MB after \(estimated from 3 files', txt)
    assert 'float WAV (FLAC has no float)' in txt and 'part_float.wav' in txt
    assert 'latest.wav: a copy of renders/take_a.wav' in txt
    assert 'work/build.py: line 2' in txt
    assert os.path.join(rc.root(), 'flac_') in txt and not os.listdir(rc.root())   # named, not made


def test_compress_writes_exact_flac_moves_the_wavs_and_links_latest(tmp_path, slots):
    songs, root, data = song(tmp_path)
    txt = api.OPS['song_compress'](root, dry=False)
    assert [s[0] for s in slots] == ['cpu']                  # one CPU slot through the governor
    assert '4 WAV to FLAC' in txt and 'NOT done' not in txt
    rec = reclaim(songs)
    for p, (y, sub) in data.items():
        if p.endswith('take_a.wav'):
            continue
        flac = p[:-4] + '.flac'
        assert not os.path.exists(p) and sf.info(flac).subtype == sub
        assert np.array_equal(sf.read(flac, dtype='int32')[0], sf.read(os.path.join(
            rec, rc.kept_path(p, songs=songs)), dtype='int32')[0])                   # the path from the drive root is kept
    rd = os.path.join(root, 'renders')
    assert os.path.samefile(os.path.join(rd, 'latest.wav'), os.path.join(rd, 'take_a.wav'))
    assert os.path.exists(os.path.join(rd, 'stems', 'part_float.wav'))     # skipped, still there
    with open(os.path.join(os.path.dirname(rec), 'manifest.json'), encoding='utf8') as f:
        runs = json.load(f)['runs']
    rows = runs[-1]['moved']
    assert len(runs) == 1 and runs[0]['t'].endswith('Z') and runs[0]['group'] == 'song_b'
    assert len(rows) == 5 and {r['from'] for r in rows} >= {os.path.join(rd, 'latest.wav'),
                                                             os.path.join(rd, 'stems', 'part_16.wav')}
    assert all(os.path.exists(r['to']) and r['why'] and r['at'] for r in rows)
    assert all(r['flac_bytes'] < r['bytes'] and os.path.exists(r['flac']) for r in rows if r.get('flac'))
    assert 'Nothing was deleted' in txt
    with open(os.path.join(rc.root(), 'README.txt'), encoding='utf8') as f:
        readme = f.read()
    assert os.path.basename(os.path.dirname(rec)) + os.sep in readme and 'ismail song_compress' in readme
    # a source still reads, now from the FLAC
    assert api._load(root).resolve_audio('ref:drums').endswith('drums.flac')


def test_a_new_render_beside_an_old_flac_wins_and_the_old_flac_goes_to_reclaim(tmp_path, slots):
    songs, root, data = song(tmp_path)
    api.OPS['song_compress'](root, dry=False)
    p = os.path.join(root, 'renders', 'stems', 'part_24.wav')
    fresh = tone(3.0, 660, seed=9)
    wav(p, fresh)
    assert audiofile.find(p) == p
    assert 'part_24.flac (older than part_24.wav' in api.OPS['song_compress'](root)
    api.OPS['song_compress'](root, dry=False)
    assert np.array_equal(sf.read(p[:-4] + '.flac', dtype='int32')[0],
                          sf.read(wav(str(tmp_path / 'x.wav'), fresh), dtype='int32')[0])
    rec = os.path.join(reclaim(songs), rc.kept_path(os.path.join(root, 'renders', 'stems'), songs=songs))
    assert {'part_24.flac', 'part_24.wav', 'part_24_2.wav'} <= set(os.listdir(rec))
    with open(os.path.join(os.path.dirname(reclaim(songs)), 'manifest.json'), encoding='utf8') as f:
        assert len(json.load(f)['runs']) == 2                            # the second run appended


def test_never_touched_folders(tmp_path, slots):
    songs = str(tmp_path / 'songs')
    for seq in api_compress.NEVER_PATHS:
        assert api_compress.never(os.path.join(songs, *seq, 'a.wav'), songs)
    for d in ('live', 'sounds', 'exam', 'exam_clips', 'takes'):
        assert api_compress.never(os.path.join(songs, 'song_c', d, 'renders', 'a.wav'), songs)
    assert api_compress.never(os.path.join(songs, 'song_c', 'proj', 'renders', 'a.wav'), songs) is None
    root = os.path.join(songs, 'song_c', 'live', 'proj')     # a project inside live/: nothing moves
    api.project_new(root, bpm=120, length_bars=2)
    stem = wav(os.path.join(root, 'renders', 'stems', 'part.wav'), tone(1.0))
    before = tree(songs)
    txt = api.OPS['song_compress'](root, dry=False)
    assert tree(songs) == before and os.path.exists(stem)
    assert 'nothing to compress' in txt and 'under live/' in txt
