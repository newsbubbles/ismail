"""The toms voice: real toms retuned to the note, with where (pos) and how (stroke) each hit lands. Synthetic
samples only: a drum here is two decaying sine modes laid out in the Big Rusty folders (tom_14 .. tom_22, a folder
per stroke, a folder per mic), so nothing is downloaded and nothing real is played."""
import json
import os

import numpy as np
import pytest
import soundfile as sf

from ismail import machine, samples, sketch, voices
from ismail.voices.drums import toms

SR = 48000
FOLDERS = ('center', 'mallet', 'brush', 'rc', 'sc')
MODE2 = 1.6                                   # the overtone's ratio to the fundamental in the synthetic drums


def drum_hit(f0, amp, sr=SR, dur=0.6):
    t = np.arange(int(dur * sr)) / sr
    y = np.sin(2 * np.pi * f0 * t) * np.exp(-t / 0.25) + 0.5 * np.sin(2 * np.pi * f0 * MODE2 * t) * np.exp(-t / 0.15)
    y[:48] *= np.linspace(0, 1, 48)
    return 0.8 * amp * y / np.abs(y).max()


def make_samples(d, layers=3, mics=('cl', 'oh')):
    """A Big Rusty shaped Samples folder: every tom, every stroke folder, `layers` velocity layers, one round robin."""
    for _, (name, base, _) in toms.DRUMS.items():
        for folder in FOLDERS:
            for mic in mics:
                os.makedirs(os.path.join(d, name, folder, mic), exist_ok=True)
                for vl in range(1, layers + 1):
                    sf.write(os.path.join(d, name, folder, mic, f"{name}_{folder}_vl{vl}_rr1.flac"),
                             drum_hit(base, vl / layers), SR)
    return str(d)


@pytest.fixture(scope='module')
def kit(tmp_path_factory):
    return make_samples(tmp_path_factory.mktemp('big_rusty') / 'Samples')


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_SAMPLES', str(tmp_path / 'store'))
    monkeypatch.setenv('ISMAIL_TOMS_CACHE', str(tmp_path / 'toms_cache'))
    for v in ('TOM_SAMPLES', 'RUSTY_SAMPLES', 'RHODES_SAMPLES', 'EMILY_SAMPLES'):
        monkeypatch.delenv(v, raising=False)
    for k in [k for k in toms._cache if isinstance(k, tuple) and k[0] in ('samp', 'fix', 'disk')]:
        toms._cache.pop(k)
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))      # a render here is never held back
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [])


def band_db(y, sr, f):
    S = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    fr = np.fft.rfftfreq(len(y), 1 / sr)
    m = (fr > f * 0.94) & (fr < f * 1.06)
    return 20 * np.log10(np.sqrt((S[m] ** 2).sum()) + 1e-12)


def cents(a, b):
    return 1200 * np.log2(a / b)


def test_the_edge_moves_the_fundamental_by_edge_db_and_keeps_the_energy():
    modes = [(100.0, 0.0), (100.0 * MODE2, -6.0)]
    lv = 10 ** (np.array([db for _, db in modes]) / 10)
    assert toms.position_gains(modes, toms.POS_REF) == pytest.approx([1.0, 1.0])
    for pos in (0.0, 0.6, 1.0):
        g = np.array(toms.position_gains(modes, pos))
        assert (lv * g ** 2).sum() == pytest.approx(lv.sum())          # the stick is struck as hard
    g = toms.position_gains(modes, 1.0)
    assert 20 * np.log10(g[0] / g[1]) == pytest.approx(-toms.EDGE_DB)
    g = toms.position_gains(modes, 0.0)
    assert 20 * np.log10(g[1] / g[0]) == pytest.approx(-toms.CENTER_DB)
    # on audio: the reshaped hit's fundamental falls EDGE_DB against its overtone, the energy stays
    y = drum_hit(100.0, 1.0)
    out = toms.reshape(y, SR, modes, toms.position_gains(modes, 1.0))
    seg = slice(int(0.05 * SR), int(0.5 * SR))
    moved = (band_db(out[seg], SR, 100) - band_db(out[seg], SR, 160)) - \
        (band_db(y[seg], SR, 100) - band_db(y[seg], SR, 160))
    assert moved == pytest.approx(-toms.EDGE_DB, abs=1.5), moved
    assert 10 * np.log10((out ** 2).sum() / (y ** 2).sum()) == pytest.approx(0, abs=1.0)


def test_the_stroke_lane_picks_the_folder_at_each_hit(kit, monkeypatch):
    monkeypatch.setenv('TOM_SAMPLES', kit)
    read = []
    orig = toms._read
    monkeypatch.setattr(toms, '_read', lambda p, sr, ratio: read.append(p) or orig(p, sr, ratio))
    notes = [(i * 0.5, 43, 0.25, 100) for i in range(5)]     # G2: the 14"
    total = int(3.0 * SR)
    lane = np.zeros(total)
    for i, s in enumerate(['stick', 'brush', 'rimclick', 'shell', 'mallet']):     # a step held per hit
        lane[int(i * 0.5 * SR):] = toms.STROKES.index(s)
    seen = []
    for i, (st, m, d, v) in enumerate(notes):
        read.clear()
        toms.perform([(st, m, d, v)], total, SR, lanes={'stroke': lane}, humanize_ms=0.0)
        seen.append({os.path.basename(os.path.dirname(os.path.dirname(p))) for p in read})
    assert seen == [{'center'}, {'brush'}, {'rc'}, {'sc'}, {'mallet'}], seen
    with pytest.raises(ValueError, match='one of stick'):
        toms.hit(98.0, 100, SR, stroke='flam')


def test_a_retune_lands_the_fundamental_on_the_note(kit, monkeypatch):
    monkeypatch.setenv('TOM_SAMPLES', kit)
    for midi in (38, 47, 52):                                # D2, B2, E3: away from every real drum's own pitch
        f0 = 440.0 * 2 ** ((midi - 69) / 12)
        y = toms.hit(f0, 127, SR).mean(0)
        files = toms._files(kit, toms.pick_drum(f0)[0], 'center')
        nl = max(max(v) for v in files.values())
        target = f0 * 2 ** (toms._want(f0, 'center', nl / nl, toms._cal())[0] / 1200)   # the real drums' curve
        got, _ = toms.measure(y, SR, target)
        assert abs(cents(got, target)) < 15, (midi, got, target)


def test_missing_samples_say_what_to_fetch(tmp_path, monkeypatch):
    mod = voices.load('toms')                                # importing never needs the samples
    assert mod.INFO['lanes'] and 'stroke' in mod.INFO['lanes']
    partial = tmp_path / 'partial' / 'Samples'               # a registered copy with the kit and no toms
    (partial / 'kick_24').mkdir(parents=True)
    samples.fetch('big_rusty', register=str(partial))
    with pytest.raises(FileNotFoundError) as e:
        mod.perform([(0, 43, 0.5, 100)], SR, SR)
    msg = str(e.value)
    assert "samples_fetch('big_rusty')" in msg and 'tom_14' in msg and 'TOM_SAMPLES' in msg and 'CC0' in msg
    assert str(partial) in msg                               # says which folder it found lacking
    assert not mod.samples_ready()


def test_the_fetch_list_brings_every_tom_and_stroke(tmp_path, monkeypatch):
    wanted = [f"Samples/{d}/{f}/{m}/x_vl1_rr1.flac" for d in ('tom_14', 'tom_15', 'tom_18', 'tom_22')
              for f in FOLDERS for m in ('cl', 'oh', 'sn')]
    wanted += ['Samples/tom_18/bstir/cl/x_vl1_rr1.flac', 'Samples/snare_14/rc/cl/x.flac', 'Samples/kick_24/cl/x.flac',
               'LICENSE']
    tree = {'truncated': False, 'tree': [{'type': 'blob', 'path': p, 'size': 1} for p in wanted] +
            [{'type': 'blob', 'path': 'Programs/big_rusty.sfz', 'size': 1}, {'type': 'tree', 'path': 'Samples'}]}
    got = []

    def fake_get(url, tries=3):
        got.append(url)
        return json.dumps(tree).encode() if 'api.github.com' in url else b'data'
    monkeypatch.setattr(samples, '_get', fake_get)
    p = samples.fetch('big_rusty', log=lambda m: None)
    fetched = {u.split('/main/', 1)[1] for u in got if 'raw.githubusercontent' in u}
    assert set(wanted) == fetched                            # every tom stroke folder, and not the sfz programs
    assert toms.samples_dir() == p                           # and the voice finds them in the store


def test_the_projects_ref_folder_is_found_and_a_song_renders(kit, tmp_path):
    import shutil
    from ismail import api
    root = str(tmp_path / 'song')
    api.project_new(root, bpm=120, length_bars=1)
    shutil.copytree(kit, os.path.join(root, 'ref', 'big_rusty', 'Samples'))
    assert toms.samples_dir(root) == os.path.join(root, 'ref', 'big_rusty', 'Samples')
    api.track_add(root, 't', instrument={'type': 'code', 'voice': 'toms', 'tail': 1.0})
    api.notes_write(root, 't', 1, '0 G2 0.5 100; 1 D2 0.5 90; 2 A1 0.5 110')
    api.automation_set(root, 't', 'inst.lane.stroke', [[1, 0], [2.99, 0], [3, 6]])
    api.render(root)
    y, sr = sf.read(os.path.join(root, 'renders', 'latest.wav'))
    assert np.abs(y).max() > 0.05


def test_corrections_are_kept_on_disk(kit, tmp_path, monkeypatch):
    monkeypatch.setenv('TOM_SAMPLES', kit)
    f0 = 440.0 * 2 ** ((40 - 69) / 12)
    a = toms.hit(f0, 90, SR)
    files = os.listdir(tmp_path / 'toms_cache')
    assert len(files) == 1 and files[0].startswith('fix_')
    for k in [k for k in toms._cache if isinstance(k, tuple) and k[0] in ('fix', 'disk')]:
        toms._cache.pop(k)

    def no(*a, **k):
        raise AssertionError('measured again')
    monkeypatch.setattr(toms, 'measure', no)
    b = toms.hit(f0, 90, SR)                                 # from the file, not measured again
    assert np.array_equal(a, b)


def test_sketch_reaches_for_toms_and_says_when_they_are_missing(kit, monkeypatch):
    spec = sketch.read_brief('a tribal tom groove in D minor with cello')
    assert 'toms' not in spec['parts']                       # not on this machine: left out, and said
    assert any("samples_fetch('big_rusty')" in s and 'left out' in s for s in spec['said'])
    assert any('left out' in s for s in sketch.say_plain(spec))
    monkeypatch.setenv('TOM_SAMPLES', kit)
    spec = sketch.read_brief('a tribal tom groove in D minor with cello')
    assert spec['parts'].get('toms') == 'toms'
    pl = sketch.plan_spec(spec, 'a tribal tom groove in D minor with cello', bars=8)
    notes = pl['parts']['toms']['notes']
    assert notes and all(30 <= n[2] <= 57 for n in notes)    # F#1-A3: heard as real
    assert {n[2] % 12 for n in notes} <= {2, 9}               # D minor: the tonic and its fifth
    alone = len(notes)
    spec = sketch.read_brief('rock with live drums and tom fills')
    assert spec['parts'].get('toms') == 'toms' and 'drums' in spec['parts']
    pl = sketch.plan_spec(spec, 'rock with live drums and tom fills', bars=8)
    assert 0 < len(pl['parts']['toms']['notes']) < alone / 2    # over a kit: the fills only


def test_toms_play_live_as_in_the_studio_by_ear(kit, tmp_path, monkeypatch):
    from ismail import api
    from ismail.live import parity
    monkeypatch.setenv('TOM_SAMPLES', kit)
    root = str(tmp_path / 's')
    api.project_new(root, bpm=120, length_bars=4)
    api.track_add(root, 'p', instrument={'type': 'code', 'voice': 'toms', 'tail': 1.0})
    api.notes_write(root, 'p', 2, '0 G2 0.5 100; 0.75 G2 0.25 70; 1.5 D3 0.5 88; 2 A1 0.5 110; 3.5 D3 0.5 90',
                    repeat=3)
    res, _, _ = parity.run(root, [2, 3])
    assert parity.verdict(res['track:p']) == 'ok', res['track:p']
