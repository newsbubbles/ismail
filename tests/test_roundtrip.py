"""Write known material, render it, read it back through the analysis tools. Run: python -m pytest tests -q"""
import os
import shutil
import tempfile

import numpy as np
import pytest

from ismail import api, notation

BPM = 120


@pytest.fixture(scope='module')
def proj():
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    api.project_new(p, bpm=BPM, length_bars=4)
    api.track_add(p, 'drums', instrument='preset:kit_basic')
    api.track_add(p, 'bass', instrument={"type": "synth", "oscs": [{"wave": "saw"}], "mono": True,
                                         "filter": {"type": "lp24", "cutoff": 900}}, volume_db=-6)
    api.track_add(p, 'chords', instrument={"type": "synth", "oscs": [{"wave": "saw", "unison": 2, "detune": 6}],
                                           "filter": {"type": "lp24", "cutoff": 3000}}, volume_db=-10)
    api.pattern_write(p, 'drums', 1, {"C1": "x...x...x...x...", "D1": "....x.......x..."}, repeat=4)
    api.notes_write(p, 'bass', 1, "0 E2 1; 1 G2 1; 2 A2 1; 3 D3 1", repeat=4)
    api.notes_write(p, 'chords', 1, "0 E4,G4,B4 4; 4 D4,F#4,A4 4", repeat=2)
    api.render(p, stems=True)
    yield p
    shutil.rmtree(d, ignore_errors=True)


def test_notation():
    assert notation.pitch_to_midi('C4') == 60
    assert notation.pitch_to_midi('F#2') == 42
    assert notation.parse_notes("0 F#4 1 90 # comment")[0] == (0.0, 66, 1.0, 90)
    hits, span = notation.parse_steps("x..X")
    assert span == 1.0 and hits[1] == (0.75, 0.25, 127)


def test_render_levels(proj):
    out = api.render(proj)
    assert 'SILENT' not in out and 'CLIPPING' not in out


def test_drums_readback(proj):
    txt = api.analyze_drums(proj, source='track:drums', bars=[2, 2])
    row = [l for l in txt.split('\n') if l.startswith('bar   2')][0]
    low = row.split('low')[1].split('snare')[0].replace(' ', '')
    snare = row.split('snare')[1].split('hat')[0].replace(' ', '')
    assert [i for i, c in enumerate(low) if c != '.'] == [0, 4, 8, 12]
    assert [i for i, c in enumerate(snare) if c in 'Xx'] == [4, 12]


def test_melody_readback(proj):
    txt = api.analyze_melody(proj, source='track:bass', bars=[2, 2], fmin='C1', fmax='C5')
    notes = [l.split()[:2] for l in txt.split('\n') if l and not l.startswith('#')]
    assert notes == [['0', 'E2'], ['1', 'G2'], ['2', 'A2'], ['3', 'D3']]


def test_polyphonic_readback(proj):
    txt = api.analyze_pitches(proj, source='track:chords', bars=[1, 2], per_bar=1, max_notes=3)
    assert set(txt.split('\n')[1].split(':')[1].replace('-', ' ').split()[::2]) == {'E4', 'G4', 'B4'}
    assert set(txt.split('\n')[2].split(':')[1].replace('-', ' ').split()[::2]) == {'D4', 'F#4', 'A4'}


def test_self_alignment(proj):
    txt = api.align(proj, a='track:drums', b='track:drums')
    assert '+0.0 ms' in txt or '-0.0 ms' in txt


def test_undo(proj):
    api.notes_clear(proj, 'bass', [1, 4])
    assert api.notes_read(proj, 'bass', [1, 1]) == '(no notes in range)'
    api.undo(proj)
    assert 'E2' in api.notes_read(proj, 'bass', [1, 1])


def test_errors_point_forward(proj):
    with pytest.raises(api.OpError, match='track_add'):
        api.notes_write(proj, 'nope', 1, '0 C4 1')
    with pytest.raises(api.OpError, match='duration'):
        api.notes_write(proj, 'bass', 1, '0 C4')


def test_batch_is_atomic(proj):
    before = api.notes_read(proj, 'bass', [1, 4])
    with pytest.raises(api.OpError, match='rolled back'):
        api.batch(proj, [{"op": "notes_clear", "track": "bass", "bars": [1, 4]},
                         {"op": "notes_write", "track": "missing", "bar": 1, "notes": "0 C4 1"}])
    assert api.notes_read(proj, 'bass', [1, 4]) == before


def test_sound_fit_improves(proj):
    api.sound_make(proj, 'target', {"type": "snare", "tone_hz": 150, "noise_hp": 2000}, notes='0 C1 0.5',
                   describe=False)
    out = api.OPS['instrument_fit'](proj, target='sound:target', instrument={"type": "snare"},
                                    params={"tone_hz": [80, 300], "noise_hp": [300, 6000]}, notes='0 C1 0.5', iters=40)
    d0, d1 = [float(x) for x in out.split('\n')[0].split('distance ')[1].split(' after')[0].split(' -> ')]
    assert d1 < d0 * 0.6


def _bar_db(txt, bar):
    return float([l for l in txt.split('\n') if l.split()[:1] == [str(bar)]][0].split()[1])


def test_windowed_render_uses_song_bars():
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=BPM, length_bars=8)
        api.track_add(p, 'bass', instrument={"type": "synth", "oscs": [{"wave": "saw"}]}, volume_db=-6)
        api.notes_write(p, 'bass', 6, "0 A2 4")  # bar 6 only: bar 5 silent, bar 6 loud
        api.render(p, bars=[5, 8], stems=True)
        for src in ('render', 'track:bass'):
            txt = api.analyze_bars(p, source=src, bars=[5, 6])
            assert _bar_db(txt, 6) > -40 and _bar_db(txt, 6) - _bar_db(txt, 5) > 30
        assert 'A' in api.analyze_spectrum(p, source='render', span=[6, 6.5])
        with pytest.raises(api.OpError, match='bars 5-8'):
            api.analyze_drums(p, source='render', bars=[2, 3])
        api.render(p)  # a full render drops the window: bar 6 is still bar 6
        assert _bar_db(api.analyze_bars(p, source='render', bars=[6, 6]), 6) > -40
        assert _bar_db(api.analyze_bars(p, source='track:bass', bars=[6, 6]), 6) > -40  # stems keep their window
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_downbeat_after_silence_is_read():
    # a kick returning on beat 1 after a silent bar must read as a hit, whatever the analysis window
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=BPM, length_bars=6)
        api.track_add(p, 'drums', instrument='preset:kit_basic')
        api.pattern_write(p, 'drums', 1, {"C1": "x...x...x...x..."}, repeat=2)
        api.pattern_write(p, 'drums', 4, {"C1": "x...x...x...x..."}, repeat=2)
        api.render(p)
        for bars in ([1, 5], [3, 4], [4, 4]):
            txt = api.analyze_drums(p, source='render', bars=bars)
            line = [ln for ln in txt.splitlines() if ln.startswith('bar   4')][0]
            assert line.split('low')[1].split()[0][0] in 'Xx', (bars, line)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_render_mp3(proj):
    if not (os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg')):
        pytest.skip('ffmpeg not installed')
    txt = api.render(proj, bars=[1, 1], out='preview', mp3='only')
    rd = os.path.join(proj, 'renders')
    assert 'preview.mp3' in txt and os.path.getsize(os.path.join(rd, 'preview.mp3')) > 1000
    assert not os.path.exists(os.path.join(rd, 'preview.wav'))
    from ismail import tags                       # every mp3 ismail writes says what it is and that ismail made it
    t = tags.read_tags(os.path.join(rd, 'preview.mp3'))
    assert t['title'].endswith('(preview)') and t['url'] == tags.HOME_URL and 'Made with ismail' in t['comment']


@pytest.mark.parametrize('name, vel', [('grand_piano', 90), ('additive_piano', 90), ('growl', 21), ('sfx', 10)])
def test_builtin_voices_render(name, vel):
    from ismail import instruments, voices
    assert name in [n for n, _, _ in voices.available()]
    inst = instruments.normalize({"type": "code", "voice": name, "tail": 0.5})
    y = instruments.render_instrument(inst, [(0.0, 45, 0.5, vel)], 48000)
    assert y.shape == (2, 48000) and np.max(np.abs(y)) > 1e-3 and np.all(np.isfinite(y))


def test_song_voice_overrides_builtin_and_invalidates_cache():
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=120, length_bars=1)
        os.makedirs(os.path.join(p, 'voices'))
        src = os.path.join(p, 'voices', 'sfx.py')
        with open(src, 'w') as f:
            f.write("import numpy as np\nINFO = {'summary': 'song sine'}\n"
                    "def voice(freq, t, vel, gate, sr, level=0.5):\n    return level * np.sin(2 * np.pi * freq * t)\n")
        assert 'song sine' in api.voices_list(p)
        api.track_add(p, 'fx', instrument={"type": "code", "voice": "sfx", "params": {"level": 0.2}})
        api.notes_write(p, 'fx', 1, "0 A4 1")
        api.render(p, stems=True)
        a = api.analyze_timbre(p, source='track:fx', span=[1, 1.25])
        with open(src, 'a') as f:
            f.write("# edited\n")
        api.track_set(p, 'fx', volume_db=0.0)
        assert 'cached' not in api.render(p, stems=True).split('fx')[1].split('\n')[0]
        with pytest.raises(api.OpError):
            api.track_add(p, 'bad', instrument={"type": "code", "voice": "nope"})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_gate_closes_on_rests():
    from ismail import fx as fxmod

    class Ctx:
        sr, bpm = 48000, 120

        def __init__(self, off):
            self.offset_samples = off

        def param(self, i, name, default):
            return default

    g = fxmod.normalize({"type": "gate", "pattern": "x.x.", "step": 0.25})
    st = 6000                       # one 16th at 120 BPM
    for off in (0, -48000 * 3):     # song start, and a window starting mid-song
        y = fxmod.apply_fx(np.ones((2, 48000)), g, Ctx(off), 0)[0]
        lv = [y[i * st + st // 2:(i + 1) * st].mean() for i in range(8)]
        assert all(lv[i] > 0.7 for i in (0, 2, 4, 6)) and all(lv[i] < 0.1 for i in (1, 3, 5, 7))


def test_sound_speak_on_this_os():
    import sys
    if not (sys.platform == 'win32' or shutil.which('say') or shutil.which('espeak-ng') or shutil.which('espeak')):
        pytest.skip('no text-to-speech engine on this machine')
    d = tempfile.mkdtemp(prefix='ismail_test_')
    p = os.path.join(d, 'p')
    try:
        api.project_new(p, bpm=120, length_bars=1)
        from ismail.api import OPS
        out = OPS['sound_speak'](p, 'hi', 'hello there', voice='no such voice')
        assert "sound 'hi'" in out and 'hi' in OPS['sound_list'](p)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_mimic_measures_and_rebuilds_a_known_tone():
    from ismail import mimic, instruments
    sr = 44100
    t = np.arange(int(2.5 * sr)) / sr
    rng = np.random.default_rng(0)
    notes = []
    for midi in (57, 64, 69, 76):
        f = 440.0 * 2 ** ((midi - 69) / 12)
        y = sum(a * np.sin(2 * np.pi * k * f * t) for k, a in ((1, 0.3), (2, 0.15), (3, 0.08)) if k * f < 16000)
        y = y + 0.002 * rng.standard_normal(len(t))
        y[:441] *= np.linspace(0, 1, 441)
        y[-4410:] *= np.linspace(1, 0, 4410)
        notes.append((y, midi, 0.7))
    prof = mimic.measure(notes, name='test')
    assert prof['kind'] == 'sustained'
    n = prof['notes'][2]
    assert abs(n['level'][0] - 20 * np.log10(0.3)) < 1.0 and abs(n['level'][1] - 20 * np.log10(0.15)) < 1.0
    z = mimic.render(prof, 440.0, t, 0.7, 2.0, variation=0.0, width=0.0, room=0.0)[0]
    back = mimic.measure_note(z, 69, kind='sustained')
    # partial balance survives the round trip (absolute level is normalized by the profile)
    assert abs((back['level'][0] - back['level'][1]) - (n['level'][0] - n['level'][1])) < 1.5
    inst = instruments.normalize({'type': 'mimic', 'profile': 'x'})
    assert inst['tail'] == 1.0
    with pytest.raises(instruments.InstrumentError):
        instruments.normalize({'type': 'mimic', 'profile': 'x', 'params': {'nope': 1}})


def test_sprite_is_the_synth_and_mimic_profiles_carry_defaults():
    from ismail import mimic, instruments
    a = instruments.normalize({'type': 'sprite', 'oscs': [{'wave': 'saw'}]})
    b = instruments.normalize({'type': 'synth', 'oscs': [{'wave': 'saw'}]})
    assert a == b and a['type'] == 'synth'
    sr = 44100
    t = np.arange(int(1.5 * sr)) / sr
    notes = [(0.3 * np.sin(2 * np.pi * 440.0 * 2 ** ((m - 69) / 12) * t) * np.minimum(1, t / 0.01), m, 0.7)
             for m in (60, 67, 74)]
    prof = mimic.profile_from_notes(mimic.measure_notes(notes, calibrate=False))
    plain = mimic.render(prof, 440.0, t, 0.7, 1.0, variation=0.0, seed=1)
    prof['defaults'] = {'bright': 12.0}
    bright = mimic.render(prof, 440.0, t, 0.7, 1.0, variation=0.0, seed=1)
    assert not np.allclose(plain, bright)
    assert np.allclose(mimic.render(prof, 440.0, t, 0.7, 1.0, variation=0.0, seed=1, bright=0.0), plain)


def test_voice_families_and_builtin_mimic_strings():
    from ismail import voices, mimic
    import ismail.voices.growl as g_old
    import ismail.voices.bass.growl as g_new
    assert g_old is g_new                                   # flat imports from older songs still work
    fam = {n: o for n, o, _ in voices.available()}
    assert fam['grand_piano'] == 'built-in/keys' and fam['growl'] == 'built-in/bass'
    profs = {n: o for n, o, _ in voices.mimic_profiles()}
    for name in ('violin', 'cello', 'contrabass'):
        assert profs[name] == 'built-in/strings'
        y = mimic.render(mimic.load_profile(name), 220.0, np.arange(4410) / 44100, 0.7, 0.08, room=0.0)
        assert y.shape == (2, 4410) and np.isfinite(y).all() and np.abs(y).max() > 1e-4


def test_voices_list_names_each_voices_function_and_fn_voice_plays_a_performer(tmp_path):
    """e002 pilots: voices_list said fn 'voice' for every code voice; electric and emily have only perform, so the
    agents' first track_add failed."""
    out = api.voices_list()
    assert 'electric' in out and [l for l in out.splitlines() if l[1:].startswith('electric')][0].split()[2] == 'fn=perform'
    assert [l for l in out.splitlines() if l[1:].startswith('grand_piano')][0].split()[2] == 'fn=voice'
    p = str(tmp_path / 'g')
    api.project_new(p, 120, 1)
    api.track_add(p, 'gtr', instrument={'type': 'code', 'voice': 'electric', 'fn': 'voice'})


def test_ffmpeg_never_reads_the_servers_stdin():
    """Under the MCP server, stdin is the JSON-RPC pipe: an ffmpeg that reads it hangs the render (ledger:M372)."""
    import re
    root = os.path.dirname(api.__file__)
    bad = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            if f.endswith('.py'):
                src = open(os.path.join(dp, f), encoding='utf8').read()
                for m in re.finditer(r"subprocess\.(?:run|Popen)\(\[\s*(?:'ffmpeg'|ffmpeg\(\)|ffmpeg|ff|exe)\s*,([^\]]*)\]", src):
                    if "'-nostdin'" not in m.group(1) and "'pipe:0'" not in m.group(1):
                        bad.append(f"{f}: {m.group(0)[:80]}")
    assert not bad, bad


def test_a_failed_mp3_keeps_the_stems(proj, monkeypatch):
    from ismail import render as R
    def boom(*a, **k):
        raise RuntimeError('ffmpeg exited 1')
    monkeypatch.setattr(R, 'write_mp3', boom)
    sd = os.path.join(proj, 'renders', 'stems')
    shutil.rmtree(sd, ignore_errors=True)                                           # the test's own temp project
    with pytest.raises(api.OpError, match='stems/'):
        api.render(proj, bars=[1, 1], stems=True, mp3='also', out='m373')            # ledger:M373
    assert os.path.isdir(sd) and any(f.endswith('.wav') for f in os.listdir(sd))
