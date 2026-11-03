"""The spectrogram video's playhead (ledger:M426): the line sits at the play position, and its glow grows where the
music under it is bright."""
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from ismail import settings
from ismail.video import spectro

SR = 22050
FFMPEG = settings.get('ffmpeg')
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason='ffmpeg is not installed (CI installs it on Linux only)')


@pytest.fixture
def song(tmp_path):
    """4 s: a quiet sine for 2 s, then loud noise for 2 s."""
    t = np.arange(2 * SR) / SR
    quiet = 0.01 * np.sin(2 * np.pi * 220 * t)
    loud = 0.5 * np.random.default_rng(0).standard_normal(2 * SR)
    y = np.concatenate([quiet, loud]).astype(np.float32)
    p = tmp_path / 'song.wav'
    sf.write(p, np.stack([y, y], axis=1), SR)
    return str(p)


def frame(song, tmp_path, t, **kw):
    out = str(tmp_path / f'f_{t}.png')
    spectro.render(song, out, still=t, width=320, height=180, fps=10, **kw)
    raw = subprocess.run([FFMPEG, '-v', 'error', '-i', out, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(180, 320, 3).astype(float)


def glow_width(img, x):
    """Columns around x lifted in blue over the plain picture (the halo is blue-white)."""
    blue = img[:, :, 2].mean(axis=0) - img[:, :, 0].mean(axis=0)
    return int((blue[max(0, x - 60):x + 60] > 8).sum())


@needs_ffmpeg
def test_static_line_moves_with_time(song, tmp_path):
    a, b = frame(song, tmp_path, 1.0), frame(song, tmp_path, 3.0)
    xa, xb = int(1.0 / 4 * 320), int(3.0 / 4 * 320)
    assert a[:, xa - 1:xa + 2].mean() > 200            # the core at the play position
    assert b[:, xb - 1:xb + 2].mean() > 200
    assert a[:, xb - 1:xb + 2].mean() < b[:, xb - 1:xb + 2].mean() - 40


@needs_ffmpeg
def test_glow_grows_on_bright_music(song, tmp_path):
    q, l = frame(song, tmp_path, 1.0), frame(song, tmp_path, 3.0)
    assert glow_width(l, int(3.0 / 4 * 320)) > glow_width(q, int(1.0 / 4 * 320)) + 6


@needs_ffmpeg
def test_scroll_keeps_the_line_fixed(song, tmp_path):
    a = frame(song, tmp_path, 1.0, mode='scroll', window_s=2.0)
    b = frame(song, tmp_path, 3.0, mode='scroll', window_s=2.0)
    for img in (a, b):
        assert img[:, 159:162].mean() > 200
    assert b[:, :150].mean() > a[:, :150].mean()        # the loud part has scrolled under the left half


def test_bad_options_say_what_is_known(song, tmp_path):
    with pytest.raises(ValueError, match="'static'"):
        spectro.render(song, str(tmp_path / 'x.mp4'), mode='sideways')
    with pytest.raises(ValueError, match='known'):
        spectro.render(song, str(tmp_path / 'x.mp4'), bloom=3)


@needs_ffmpeg
def test_video_has_audio_and_length(song, tmp_path):
    out = str(tmp_path / 'v.mp4')
    r = spectro.render(song, out, width=160, height=90, fps=10, start=1.0, end=3.0)
    assert '20 frames' in r
    probe = shutil.which('ffprobe') or FFMPEG.replace('ffmpeg', 'ffprobe')
    s = subprocess.run([probe, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', out],
                       capture_output=True, text=True).stdout.split()
    assert s == ['video', 'audio']
    spectro.render(song, out, width=160, height=90, fps=10, start=1.0, end=3.0, audio=False)
    s = subprocess.run([probe, '-v', 'error', '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', out],
                       capture_output=True, text=True).stdout.split()
    assert s == ['video']


def test_cli_audio_switch_keeps_the_file(song, tmp_path, monkeypatch):
    """`--audio false` turns the soundtrack off; it must not replace the audio file argument (Marketing 11-01)."""
    from ismail.video import __main__ as cli
    seen = {}
    monkeypatch.setattr(spectro, 'render', lambda src, out, **kw: seen.update(src=src, out=out, **kw) or 'ok')
    cli.cmd_spectro([song, 'o.mp4', '--start', '1', '--end', '2', '--audio', 'false'])
    assert seen['src'] == song and seen['out'] == 'o.mp4'
    assert seen['audio'] is False and seen['start'] == 1.0
