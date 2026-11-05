"""The on-air check (ledger:M432): the silence scan, named source levels against the mix, the caller's rules
command, the verdict and the gate. Synthetic audio with known answers: a gap of known length, sources at known
levels."""
import os
import shutil
import sys
import tempfile

import numpy as np
import pytest
import soundfile as sf

from ismail import api
from ismail.api import OpError
from ismail.api_onair import OnAirRefused, onair_gate

SR = 22050
OPS = api.OPS


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp(prefix='ismail_onair_')
    yield d
    shutil.rmtree(d, ignore_errors=True)


def tone(sec, db, f=220.0, sr=SR):
    """A sine with rms `db` dBFS."""
    t = np.arange(int(sec * sr)) / sr
    return np.sin(2 * np.pi * f * t) * 10 ** (db / 20) * np.sqrt(2)


def write(path, y, sr=SR):
    sf.write(path, np.asarray(y, dtype=np.float32), sr)
    return path


def test_silence_scan_finds_the_gap_with_its_times(tmp):
    y = np.concatenate([tone(5, -20), np.zeros(int(4 * SR)), tone(3, -20), tone(2, -60), tone(4, -20)])
    f = write(os.path.join(tmp, 'mix.wav'), y)
    out = OPS['analyze_silence'](source=f)
    assert '0:05.0-0:09.0  4.0 s' in out, out
    assert 'FAIL 1 unplanned silent stretch' in out and 'planned=[[5, 9]]' in out, out
    assert '0:12.0' not in out, out                    # 2 s at -60 dBFS: under the floor but shorter than 3 s
    out = OPS['analyze_silence'](source=f, min_s=1.5)
    assert '0:12.0-0:14.0' in out, out
    out = OPS['analyze_silence'](source=f, planned=[[5, 9]])
    assert out.rstrip().endswith('PASS no unplanned silent stretch') and 'planned' in out, out
    out = OPS['analyze_silence'](source=f, min_s=1.5, floor_db=-70)
    assert '0:12.0' not in out and 'digital silence' in out, out      # -60 dBFS is over a -70 floor; zeros are not


def test_silence_scan_reads_a_long_file_in_blocks_and_gives_hours(tmp):
    sr = 8000                                          # 1 h 2 min of audio at a low rate keeps the test quick
    y = np.full(int(3720 * sr), 0.05, dtype=np.float32)
    y[int(3605 * sr):int(3611 * sr)] = 0.0
    f = write(os.path.join(tmp, 'assembly.wav'), y, sr)
    out = OPS['analyze_silence'](source=f)
    assert '1:00:05.0-1:00:11.0  6.0 s' in out and '(1:02:00.0, 8000 Hz)' in out, out


def test_silence_scan_reads_a_video_through_ffmpeg(tmp):
    from ismail import settings
    ff = settings.get('ffmpeg')
    if not ff:
        pytest.skip('ffmpeg not installed')
    import subprocess
    y = np.concatenate([tone(4, -20), np.zeros(int(4 * SR)), tone(4, -20)])
    wav = write(os.path.join(tmp, 'mix.wav'), y)
    mp4 = os.path.join(tmp, 'chapter.mp4')
    subprocess.run([ff, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=5:d=12', '-i', wav,
                    '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', mp4], check=True, timeout=60)
    out = OPS['analyze_silence'](source=mp4)
    assert 'FAIL 1 unplanned silent stretch' in out and '48000 Hz' in out, out
    assert '0:04.' in out and '0:08.' in out, out        # aac pads a few ms, so the edges are read to the 0.1 s
    novid = os.path.join(tmp, 'picture_only.mp4')
    subprocess.run([ff, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=64x64:r=5:d=2', '-c:v', 'mpeg4',
                    novid], check=True, timeout=60)
    with pytest.raises(OpError, match='ffmpeg could not read'):
        OPS['analyze_silence'](source=novid)


def test_silence_scan_on_a_project_render_gives_bars(tmp):
    p = os.path.join(tmp, 'p')
    api.project_new(p, bpm=120, length_bars=8)            # 2 s a bar
    os.makedirs(os.path.join(p, 'renders'), exist_ok=True)
    write(os.path.join(p, 'renders', 'latest.wav'), np.concatenate([tone(4, -20), np.zeros(4 * SR), tone(8, -20)]))
    out = OPS['analyze_silence'](p)
    assert 'bars 3.0-5.0' in out, out
    with pytest.raises(api.OpError, match='project source'):
        OPS['analyze_silence'](source='render')


def stems(tmp):
    """A mix of a bed at -20 dBFS rms and a theme at -26 dBFS that plays in the second half; and a silent foley."""
    bed, theme = tone(8, -20, 110), np.concatenate([np.zeros(4 * SR), tone(4, -26, 880)])
    return (write(os.path.join(tmp, 'mix.wav'), bed + theme), write(os.path.join(tmp, 'bed.wav'), bed),
            write(os.path.join(tmp, 'theme.wav'), theme), write(os.path.join(tmp, 'foley.wav'), np.zeros(8 * SR)))


def test_source_levels_against_the_mix_where_they_play(tmp):
    mix, bed, theme, foley = stems(tmp)
    out = OPS['onair_check'](mix=mix, sources={'bed': bed, 'theme': theme})
    assert out.startswith(f'on-air check of {mix}: PASS (2 sources over the floor'), out
    row = next(ln for ln in out.splitlines() if ln.strip().startswith('theme'))
    own, peak, plays, in_mix, share = row.split()[1:6]
    assert abs(float(own) - (-29.0)) < 0.3, row         # -26 dBFS over half the file: -29 over the whole
    assert plays == '50%', row
    assert abs(float(in_mix) - (-7.0)) < 0.3, row       # -26 against the half's mix (-20 and -26 summed: -19.0)
    assert abs(float(share.rstrip('%')) - 11.2) < 0.5, row
    out = OPS['onair_check'](mix=mix, sources={'theme': theme}, floor_db=-6)
    assert ': FAIL (1 reason)' in out and 'theme sits 7.0 dB under the mix where it plays (the floor is 6 dB under it)' in out, out
    assert 'raise it 1 dB or more' in out and 'LOW 1.0 dB under the floor' in out, out


def test_a_silent_source_fails_and_says_what_to_check(tmp):
    mix, bed, theme, foley = stems(tmp)
    out = OPS['onair_check'](mix=mix, sources=[bed, foley])
    assert ': FAIL (1 reason)' in out and f'source {foley} is silent' in out and 'SILENT' in out, out


def test_rules_command_exit_code_is_the_verdict(tmp):
    mix, bed, theme, foley = stems(tmp)
    good = os.path.join(tmp, 'rules_ok.py')
    bad = os.path.join(tmp, 'rules_bad.py')
    with open(good, 'w') as f:
        f.write("import os, sys\nprint('checked', os.path.basename(os.environ['ISMAIL_CHECK_MIX']))\n")
    with open(bad, 'w') as f:
        f.write("import sys\nprint('lead: a square wave on part 3')\nsys.exit(2)\n")
    out = OPS['onair_check'](mix=mix, rules=good)
    assert 'PASS' in out.splitlines()[0] and 'rules exit 0' in out and '| checked mix.wav' in out, out
    out = OPS['onair_check'](mix=mix, rules=[bad, 'part3'])
    assert ': FAIL (1 reason)' in out and 'exited 2' in out and '| lead: a square wave on part 3' in out, out


def test_rules_runs_only_a_python_file(tmp):
    mix, bed, theme, foley = stems(tmp)
    for rules in ('echo hi', [sys.executable, '-c', 'print(1)'], os.path.join(tmp, 'missing.py')):
        with pytest.raises(OpError) as e:
            OPS['onair_check'](mix=mix, rules=rules)
        assert 'give a Python file that exists' in str(e.value)


def test_the_gate_refuses_on_a_fail_unless_forced(tmp):
    mix, bed, theme, foley = stems(tmp)
    assert 'PASS' in onair_gate(mix=mix, sources=[bed])
    gap = write(os.path.join(tmp, 'gap.wav'), np.concatenate([tone(2, -20), np.zeros(5 * SR), tone(2, -20)]))
    with pytest.raises(OnAirRefused) as e:
        onair_gate(mix=gap, sources=[foley])
    assert len(e.value.reasons) == 2 and str(e.value).startswith('REFUSED on-air check'), str(e.value)
    assert isinstance(e.value, api.OpError)
    out = onair_gate(mix=gap, sources=[foley], force=True)
    assert out.rstrip().endswith('FORCED: aired with 2 fails (force=True)'), out


def test_track_sources_read_the_render_stems(tmp):
    p = os.path.join(tmp, 'p')
    api.project_new(p, bpm=240, length_bars=4)
    api.track_add(p, 'pad', instrument={"type": "synth", "oscs": [{"wave": "saw"}]})
    api.track_add(p, 'quiet', instrument={"type": "synth", "oscs": [{"wave": "sine"}]}, volume_db=-40)
    api.notes_write(p, 'pad', 1, "0 E3 4", repeat=4)
    api.notes_write(p, 'quiet', 1, "0 A4 4", repeat=4)
    api.render(p, stems=True, cache=False)
    out = OPS['onair_check'](p, sources=['track:pad', 'track:quiet'])
    assert 'FAIL' in out.splitlines()[0] and 'source track:quiet sits' in out and 'track:pad' not in out.split('---')[0]
    with pytest.raises(api.OpError, match='no rendered stem'):
        OPS['onair_check'](p, sources=['track:none'])
