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


def test_camera_path_eases_and_clamps():
    """Camera keys (ledger:M430): full, zoom to 12 s, roll, back out. The line rides in from the left edge, rolls at
    `at`, and drifts to the right edge at the end; the view never leaves the clip and the line never jumps."""
    keys = spectro.parse_camera('0:full,4:12,200:12,210:full')
    dur = 220.0
    w, left, x = spectro.camera_path(keys, [0, 2, 4, 10, 100, 200, 210], dur)
    assert np.isclose(w[0], dur) and x[0] == 0                                   # the whole clip, the line at its start
    assert np.isclose(w[1], (dur * 12) ** 0.5)                     # halfway through the ease, in log size
    assert np.isclose(w[2], 12) and left[2] == 0 and np.isclose(x[2], 4 / 12)   # zoomed, the line still riding in
    assert np.isclose(left[3], 4) and np.allclose(x[3:5], 0.5)                # rolling under the line at `at`
    assert np.isclose(w[5], 12) and np.isclose(w[6], dur) and np.isclose(x[6], 210 / dur)
    t = np.arange(0, dur, 1 / 30)
    w, left, x = spectro.camera_path(keys, t, dur)
    assert (left >= 0).all() and (left + w <= dur + 1e-9).all()
    assert np.abs(np.diff(x)).max() < 0.01                            # under 1% of the width a frame
    w, left, x = spectro.camera_path([(0, 10)], [2, 15, 28, 30], 30)     # the edges while zoomed
    assert np.allclose(left, [0, 10, 20, 20]) and np.allclose(x, [0.2, 0.5, 0.8, 1.0])


def test_camera_ease_s_holds_then_eases():
    keys = spectro.parse_camera([(0, 'full'), (10, 5)])
    w = spectro.camera_path(keys, [5, 8, 9, 10, 12], 40, ease_s=2)[0]
    assert np.allclose(w[[0, 1, 3, 4]], [40, 40, 5, 5]) and 5 < w[2] < 40


def test_defaults_unchanged_and_extras_need_a_camera(song, tmp_path):
    d = spectro.DEFAULTS
    assert d['mode'] == 'static' and d['camera'] is None and d['preset'] == 'medium'
    assert d['minimap'] == 0 and d['zoom_follow'] == 0 and d['kick_pulse'] == 0
    with pytest.raises(ValueError, match='camera'):
        spectro.render(song, str(tmp_path / 'x.mp4'), minimap=40)
    with pytest.raises(ValueError, match='0:full'):
        spectro.parse_camera('0-full')


@needs_ffmpeg
def test_camera_full_is_static(song, tmp_path):
    assert (frame(song, tmp_path, 1.5) == frame(song, tmp_path, 1.5, camera='0:full')).all()


@needs_ffmpeg
def test_camera_line_rides_in_rolls_and_drifts_out(song, tmp_path):
    for t, x in ((0.5, 80), (2.0, 160), (3.7, 272)):                  # 2 s in view: 0.25, 0.5, 0.85 of 320
        img = frame(song, tmp_path, t, camera='0:2')
        assert img[:, x - 1:x + 2].mean() > 200, t


@needs_ffmpeg
def test_extras_off_by_default_and_draw_when_on(song, tmp_path):
    plain = frame(song, tmp_path, 2.1, camera='0:2')
    mm = frame(song, tmp_path, 2.1, camera='0:2', minimap=30)
    assert (mm[:150] == plain[:150]).all() and np.abs(mm[150:] - plain[150:]).mean() > 5
    kick = frame(song, tmp_path, 2.1, camera='0:2', kick_pulse=1.0)   # the noise comes in at 2 s: a low onset
    assert kick.mean() > plain.mean() + 2
    breathe = frame(song, tmp_path, 3.0, camera='0:full,1:2', zoom_follow=1.0)
    assert breathe.shape == plain.shape
    out = str(tmp_path / 'c.mp4')
    r = spectro.render(song, out, width=160, height=90, fps=10, end=2.0, audio=False, camera='0:full,1:1',
                       minimap=20, zoom_follow=0.5, kick_pulse=0.5)
    assert '20 frames' in r and 'camera 2 keys' in r


def test_kick_env_peaks_on_low_onsets():
    B8 = np.zeros((10, 400), np.uint8)
    B8[8:, 100:140] = 220                                             # a low hit at column 100 (1 s at 100 px/s)
    env = spectro._kick_env(B8, np.arange(8, 10), 100.0, 40, 10, 0.15)
    assert env[:9].max() == 0 and env[10] > 0.9 and env[13] < 0.3


def test_cli_camera_keys(song, monkeypatch):
    from ismail.video import __main__ as cli
    seen = {}
    monkeypatch.setattr(spectro, 'render', lambda src, out, **kw: seen.update(kw) or 'ok')
    cli.cmd_spectro([song, 'o.mp4', '--camera', '0:full,4:12,200:12,210:full', '--minimap', '72',
                     '--kick_pulse', '0.6'])
    assert seen['minimap'] == 72 and spectro.keyed(seen['kick_pulse'], [0.0])[0] == 0.6
    assert spectro.parse_camera(seen['camera']) == [(0, None), (4, 12), (200, 12), (210, None)]


def test_keyed_depths_say_when():
    t = np.array([0, 100, 181, 190, 201, 250.0])
    np.testing.assert_allclose(spectro.keyed('0:0,180:0,182:0.6,200:0.6,202:0', t), [0, 0, 0.3, 0.6, 0.3, 0])
    np.testing.assert_allclose(spectro.keyed(0.5, t), 0.5)
    np.testing.assert_allclose(spectro.keyed('0.25', t), 0.25)
    np.testing.assert_allclose(spectro.keyed([(10, 1), (20, 0)], [0, 10, 30], t0=5), [1, 0.5, 0])  # file seconds
    with pytest.raises(ValueError, match='keys like'):
        spectro.keyed('0:x', t)


@needs_ffmpeg
def test_kick_pulse_keys_only_where_on(song, tmp_path):
    plain = frame(song, tmp_path, 2.1, camera='0:2')
    off = frame(song, tmp_path, 2.1, camera='0:2', kick_pulse='0:0,3:0,3.1:1')    # on only after 3 s
    on = frame(song, tmp_path, 2.1, camera='0:2', kick_pulse='0:1,3:1')
    assert (off == plain).all() and on.mean() > plain.mean() + 2


def test_needle_held_back_on_a_build_and_snapped_in_on_the_drop():
    at = spectro.needle('0:0.5,60:0.35,64:0.5:snap', [0, 30, 60, 63.8, 63.94, 64, 80])
    assert np.isclose(at[0], 0.5) and 0.35 < at[1] < 0.5 and np.isclose(at[2], 0.35)
    assert np.isclose(at[3], 0.35) and 0.35 < at[4] < 0.5 and np.allclose(at[5:], 0.5)   # the snap: the last 0.12 s
    assert np.allclose(spectro.needle(0.4, [0, 9]), 0.4) and np.allclose(spectro.needle('0.4', [0, 9]), 0.4)
    assert np.isclose(spectro.needle('10:0.5,20:0.3', [15], t0=10)[0], 0.3)             # file seconds
    with pytest.raises(ValueError, match='0 to 1'):
        spectro.needle('0:0.5,4:1.5', [0])
    with pytest.raises(ValueError, match='keys like'):
        spectro.needle('0:0.5:bounce', [0])


def test_camera_snap_key_and_needle_array():
    keys = spectro.parse_camera('0:12,64:4:snap')
    assert keys == [(0, 12), (64, 4, 'snap')]
    w = spectro.camera_path(keys, [10, 63, 63.94, 64, 80], 100)[0]
    assert np.allclose(w[[0, 1]], 12) and 4 < w[2] < 12 and np.allclose(w[3:], 4)
    t = np.array([20, 30, 40.0])
    at = spectro.needle('0:0.5,30:0.3,40:0.5', t)
    w, left, x = spectro.camera_path([(0, 10)], t, 100, at=at)
    assert np.allclose(x, at)                                          # the line sits where the needle says


@needs_ffmpeg
def test_needle_keys_need_a_camera_and_draw(song, tmp_path):
    with pytest.raises(ValueError, match='timed at keys work with a camera'):
        frame(song, tmp_path, 1.0, mode='scroll', at='0:0.5,2:0.3')
    a = frame(song, tmp_path, 2.5, camera='0:2', at='0:0.5,2.5:0.3')
    b = frame(song, tmp_path, 2.5, camera='0:2', at=0.5)
    assert a.shape == b.shape and np.abs(a.astype(int) - b).mean() > 1


def test_cli_needle_keys(song, monkeypatch):
    from ismail.video import __main__ as cli
    seen = {}
    monkeypatch.setattr(spectro, 'render', lambda src, out, **kw: seen.update(kw) or 'ok')
    cli.cmd_spectro([song, 'o.mp4', '--camera', '0:full,4:12,64:4:snap', '--at', '0:0.5,60:0.35,64:0.5:snap',
                     '--snap_s', '0.08'])
    assert seen['snap_s'] == 0.08 and np.isclose(spectro.needle(seen['at'], [64.0])[0], 0.5)
