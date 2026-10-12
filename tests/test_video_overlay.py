"""The text overlay (ismail.video.overlay): one drawtext filter for the edit and for any existing video. The filter
is checked as a string; one tiny real run (a 1 s 320x180 clip) only when ffmpeg is on PATH. No Blender, no GPU."""
import os
import shutil
import subprocess

import pytest

from ismail.video import overlay as ov
from ismail.video.edit import Cut


@pytest.fixture
def font(tmp_path):
    f = tmp_path / 'Fonts' / 'mono.ttf'
    f.parent.mkdir()
    f.write_bytes(b'not read by the filter builder')
    return str(f)


def test_the_default_look_at_1080_is_bottom_centre_half_white_with_a_soft_shadow(font):
    f = ov.drawtext('made with ismail', 1920, 1080, font=font)
    assert f.startswith('drawtext=fontfile=') and "text='made with ismail'" in f and 'expansion=none' in f
    for part in ('fontsize=30', 'fontcolor=white@0.50', 'shadowcolor=black@0.45', 'shadowx=2', 'shadowy=2',
                 'x=(w-tw)/2', 'y=h-th-42'):
        assert part in f


def test_sizes_scale_with_the_short_side_and_y_is_a_row_of_the_frame(font):
    f = ov.drawtext('example.com', 3840, 2160, font=font, size=30, margin=42)
    assert 'fontsize=60' in f and 'y=h-th-84' in f and 'shadowx=4' in f
    f = ov.drawtext('example.com', 1280, 720, font=font, pos='top')
    assert 'fontsize=20' in f and 'y=0+28' in f
    v = ov.drawtext('example.com', 1080, 1920, font=font, size=26, opacity=0.55, shadow=0.5, y=1260, margin=14)
    assert 'fontsize=26' in v and 'fontcolor=white@0.55' in v and 'y=1260-th-14' in v     # inside a band
    c = ov.drawtext('example.com', 1920, 1080, font=font, pos='center', shadow=0)
    assert 'y=h/2-th/2' in c and 'shadow' not in c


def test_a_windows_font_path_and_odd_text_are_escaped_for_ffmpeg():
    assert ov._value('C:/Windows/Fonts/x.ttf') == r"'C\:/Windows/Fonts/x.ttf'"
    assert ov._value("it's: 100%") == r"'it\'\''s\: 100%'"
    assert ov._value('a\\b') == r"'a\\b'"


def test_bad_looks_are_refused_before_any_work(font):
    for kw in ({'pos': 'left'}, {'opacity': 2}, {'shadow': -1}, {'size': 0}, {'color': 'red;x'}, {'blur': 1},
               {'font': font + '.missing'}):
        with pytest.raises(ov.OverlayError):
            ov.drawtext('x', 1920, 1080, **dict({'font': font}, **kw))
    with pytest.raises(ov.OverlayError):
        ov.drawtext('  ', 1920, 1080, font=font)


def test_no_font_on_the_machine_says_what_to_pass(monkeypatch):
    monkeypatch.setattr(ov, 'find_font', lambda: None)
    with pytest.raises(ov.OverlayError, match='--font'):
        ov.drawtext('x', 1920, 1080)


def test_the_cli_refuses_a_missing_input_an_existing_output_and_the_input_as_output(tmp_path, capsys):
    src, out = tmp_path / 'in.mp4', tmp_path / 'out.mp4'
    assert ov.main([str(src), str(out), '--text', 'x']) == 2
    assert 'no input video' in capsys.readouterr().err
    src.write_bytes(b'v')
    out.write_bytes(b'kept')
    assert ov.main([str(src), str(out), '--text', 'x']) == 2
    assert '--force' in capsys.readouterr().err and out.read_bytes() == b'kept'
    for o in (src, tmp_path / '.' / 'in.mp4'):
        assert ov.main([str(src), str(o), '--text', 'x', '--force']) == 2
        assert 'never overwritten' in capsys.readouterr().err
    assert src.read_bytes() == b'v'


def test_the_edit_draws_the_overlay_last_on_the_1080p_frame_and_only_when_asked(font):
    C = Cut.__new__(Cut)
    C.overlay_text = None
    assert C.vf() == 'scale=1920:1080:flags=lanczos'                       # off by default
    C.overlay('made with ismail', font=font, opacity=0.4)
    vf = C.vf()
    assert vf.startswith('scale=1920:1080:flags=lanczos,drawtext=') and 'fontcolor=white@0.40' in vf
    assert vf.split(',', 1)[1] == ov.drawtext('made with ismail', 1920, 1080, font=font, opacity=0.4)
    with pytest.raises(ov.OverlayError):
        C.overlay('x', pos='left')
    C.overlay(None)
    assert C.vf() == 'scale=1920:1080:flags=lanczos'


@pytest.mark.skipif(not (shutil.which('ffmpeg') and shutil.which('ffprobe')), reason='needs ffmpeg')
def test_a_real_run_on_a_tiny_clip_keeps_the_audio(tmp_path):
    src, out = str(tmp_path / 'clip.mp4'), str(tmp_path / 'clip_marked.mp4')
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=10:duration=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-c:a', 'aac', '-shortest', src], check=True, capture_output=True)
    ov.overlay_file(src, out, "made with ismail: it's 100%")
    assert os.path.getsize(out) > 0 and not os.path.exists(out[:-4] + '.part.mp4')
    r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', out],
                       capture_output=True, text=True)
    assert set(r.stdout.split()) == {'video', 'audio'}
    assert ov.probe_size(out) == (320, 180)
