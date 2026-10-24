"""Sample sets for the sampled voices (rhodes, rusty, emily): nothing downloads by itself, a missing set says what
to fetch, how big and under what licence, and a fetch or a registered folder makes the voice play."""
import io
import json
import os
import zipfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api, machine, samples


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_SAMPLES', str(tmp_path / 'store'))
    for v in ('RHODES_SAMPLES', 'RUSTY_SAMPLES', 'EMILY_SAMPLES'):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    monkeypatch.setattr(machine, 'gpu', lambda: None)
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [])
    return tmp_path / 'store'


def _rhodes_folder(d):
    d.mkdir(parents=True)
    t = np.arange(4410) / 44100
    for i, m in enumerate(range(29, 97, 4)[:16]):
        for layer in range(1, 6):
            y = 0.3 * np.sin(2 * np.pi * 440 * 2 ** ((m - 69) / 12) * t) * np.exp(-t * 3)
            sf.write(str(d / f"A_{m:03d}__X{i}_{layer}.flac"), y, 44100)
    for i in range(44):                                     # the set's size check counts files
        sf.write(str(d / f"A_{120 + i:03d}__Z_1.flac"), np.zeros(10), 44100)
    return d


def test_a_missing_set_says_what_to_fetch_and_its_licence():
    assert samples.path('jrhodes3d') is None
    with pytest.raises(FileNotFoundError) as e:
        samples.need('jrhodes3d')
    assert "samples_fetch('jrhodes3d')" in str(e.value) and '22 MB' in str(e.value) and 'CC BY-NC' in str(e.value)
    assert 'not fetched' in api.samples_list()


def test_a_repo_set_downloads_only_its_paths(store, monkeypatch):
    tree = {'truncated': False, 'tree': [
        {'type': 'blob', 'path': 'jRhodes3d-mono/A_029__F1_1.flac', 'size': 10},
        {'type': 'blob', 'path': 'jRhodes3d-wav/A_029__F1_1.wav', 'size': 10},
        {'type': 'blob', 'path': 'LICENSE', 'size': 5}]}
    got = []

    def fake_get(url, tries=3):
        got.append(url)
        return json.dumps(tree).encode() if 'api.github.com' in url else b'data'
    monkeypatch.setattr(samples, '_get', fake_get)
    monkeypatch.setitem(samples.SETS['jrhodes3d'], 'min_files', 1)
    p = samples.fetch('jrhodes3d', log=lambda m: None)
    assert p.endswith(os.path.join('jrhodes3d', 'jRhodes3d-mono'))
    assert os.listdir(p) == ['A_029__F1_1.flac'] and not any('wav' in u for u in got)
    assert 'Jeff Learman' in (store / 'jrhodes3d' / 'CREDIT.txt').read_text()


def test_a_release_zip_keeps_only_what_the_voice_reads(store, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('Karoryfer.Emilyguitar.v1.001/Emilyguitar/notes/a.wav', b'x')
        z.writestr('Karoryfer.Emilyguitar.v1.001/Emilyguitar/noises/n.wav', b'x')
        z.writestr('Karoryfer.Emilyguitar.v1.001/Emilyguitar/LICENSE', b'CC0')
    monkeypatch.setattr(samples, '_get', lambda url, tries=3: buf.getvalue())
    p = samples.fetch('emilyguitar', log=lambda m: None)
    assert sorted(os.listdir(p)) == ['LICENSE', 'notes']


def test_a_registered_folder_plays_the_rhodes(tmp_path):
    d = _rhodes_folder(tmp_path / 'mine' / 'jRhodes3d-mono')
    with pytest.raises(api.OpError):
        api.samples_fetch('jrhodes3d', path=str(tmp_path))       # not the set's folder
    assert 'registered' in api.samples_fetch('jrhodes3d', path=str(d))
    p = str(tmp_path / 'song')
    api.project_new(p, bpm=120, length_bars=1)
    api.track_add(p, 'ep', instrument={'type': 'code', 'voice': 'rhodes'})
    api.track_add(p, 'cr', instrument={'type': 'code', 'voice': 'crackle'})
    api.notes_write(p, 'ep', 1, '0 57,60,64 2 80')
    api.notes_write(p, 'cr', 1, '0 60 4 90')
    out = api.render(p, stems=True)
    assert 'SILENT' not in out


def _rms_db(perform, m, vels, dur=0.15, sr=44100):
    """The probe voice_help runs: one short note per velocity, RMS dB against the last velocity."""
    r = {v: float(np.sqrt(np.mean(perform([(0.0, m, dur, v * 127)], int((dur + 0.25) * sr), sr) ** 2))) for v in vels}
    return {v: 20 * np.log10(x / r[vels[-1]]) for v, x in r.items()}


def _centroid(y, sr=44100):
    y = y.mean(0)
    P = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2
    return float(np.sum(np.fft.rfftfreq(len(y), 1 / sr) * P) / np.sum(P))


def test_rhodes_level_rises_with_velocity_and_a_layer_is_timbre_only(tmp_path, monkeypatch):
    """M310 (voice_help's level per velocity): rhodes read louder at velocity 0.2 than at 1.0 on C4. The jRhodes3d
    samples are each normalised near full scale, so a soft layer reads 2 to 7 dB hotter than the hard one, and the
    voice had only a 3 dB slope inside a layer. Before, on the real set (C4, a 0.15 s note, dB against 1.0): 0.2 +1.4,
    0.4 -1.0, 0.6 -1.8, 0.8 -1.1. After: 0.1 -30.7, 0.2 -21.7, 0.4 -12.6, 0.6 -7.2, 0.8 -3.2."""
    from ismail.voices.keys import rhodes
    d = tmp_path / 'jRhodes3d-mono'
    d.mkdir()
    t = np.arange(22050) / 44100
    for i, m in enumerate(range(29, 97, 4)[:16]):
        f0 = 440 * 2 ** ((m - 69) / 12)
        for layer in range(1, 6):                       # 1 the hardest (bright, quiet), 5 the softest (mellow, hot)
            bark = (5 - layer) / 4
            y = np.sin(2 * np.pi * f0 * t) + bark * sum(np.sin(2 * np.pi * k * f0 * t) / k for k in range(2, 8))
            y = (0.2 + 0.1 * layer) * y / np.abs(y).max() * np.exp(-t * 3)
            sf.write(str(d / f"A_{m:03d}__X{i}_{layer}.flac"), y, 44100)
    monkeypatch.setenv('RHODES_SAMPLES', str(d))
    monkeypatch.setitem(samples.SETS['jrhodes3d'], 'min_files', 1)
    monkeypatch.setattr(rhodes, '_cache', {})
    vels = (0.2, 0.4, 0.6, 0.8, 1.0)
    for m in (48, 60, 72):
        lv = _rms_db(rhodes.perform, m, vels)
        steps = [lv[b] - lv[a] for a, b in zip(vels, vels[1:])]
        assert all(s > 0.5 for s in steps), (m, lv)
        assert 15 < -lv[0.2] < 30, (m, lv)               # an electric piano's range, not a grand's 40
    soft, hard = (rhodes.perform([(0.0, 60, 0.3, v)], 22050, 44100) for v in (20, 127))
    assert _centroid(hard) > 1.3 * _centroid(soft)        # the layers still bark at the top
    flat = _rms_db(lambda *a: rhodes.perform(*a, vel_db=0.0), 60, vels)
    assert max(abs(x) for x in flat.values()) < 1.0, flat  # vel_db 0: the layers matched, one level


def test_emily_level_moves_inside_a_recorded_layer(tmp_path, monkeypatch):
    """M310: emily read the same level at 0.4 and 0.6, both in the mp layer (41-80) of its 4 recorded layers, with no
    level inside a layer. A layer now plays as recorded at its top and falls toward the layer below at its bottom."""
    from ismail.voices.guitar import emily
    d = tmp_path / 'Emilyguitar' / 'notes'
    d.mkdir(parents=True)
    t = np.arange(22050) / 44100
    for amp, layer in ((0.05, 'p'), (0.1, 'mp'), (0.2, 'mf'), (0.4, 'f')):
        for rr in (1, 2, 3):
            sf.write(str(d / f"c4_{layer}_rr{rr}.wav"), amp * np.sin(2 * np.pi * 261.6 * t) * np.exp(-t * 4), 44100)
    monkeypatch.setenv('EMILY_SAMPLES', str(d.parent))
    monkeypatch.setattr(emily, '_cache', {})
    vels = (0.2, 0.4, 0.6, 0.8, 1.0)
    lv = _rms_db(emily.perform, 60, vels)
    assert all(lv[b] - lv[a] > 1.0 for a, b in zip(vels, vels[1:])), lv
    top = _rms_db(emily.perform, 60, (80 / 127, 1.0))      # the top of mp: the mp sample as recorded, 12 dB under f
    assert abs(top[80 / 127] + 12.04) < 0.3, top
