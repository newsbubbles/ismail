"""render's and sketch's replies read the live state (ledger:M245 part B, M247): a reference line that says whether
the reference was compared since this render, project_set(reference=)'s next steps, and a "hear it" block (the
file, what is up now to hear it on, one option chosen from that state). Every server here is a fake on this
process's own port, or a registry entry pointing at a closed port; nothing real is started or reached."""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
import soundfile as sf

from ismail import api, replies
from ismail import feedback as FB


@pytest.fixture(autouse=True)
def quiet_machine(tmp_path, monkeypatch):
    """No real phone, stage, live engine or feedback server is seen: each registry is an empty folder of our own."""
    for k, sub in (('ISMAIL_PHONE_HOME', 'phone'), ('ISMAIL_STAGE_REGISTRY', 'stage'),
                   ('ISMAIL_LIVE_REGISTRY', 'live'), ('ISMAIL_FEEDBACK_HOME', 'feedback')):
        (tmp_path / '_state' / sub).mkdir(parents=True)
        monkeypatch.setenv(k, str(tmp_path / '_state' / sub))
    return tmp_path / '_state'


class Fake:
    """A local server answering GET <path> with JSON."""
    def __init__(self, routes):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = routes.get(self.path.split('?')[0])
                data = json.dumps(body).encode()
                self.send_response(200 if body is not None else 404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _closed_port():
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wav(path, secs=4.0, sr=44100):
    t = np.arange(int(secs * sr)) / sr
    y = 0.3 * np.sin(2 * np.pi * 110 * t) * (np.sin(2 * np.pi * 2 * t) > 0)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sf.write(path, np.stack([y, y], 1), sr)
    return path


def _song(root):
    api.project_new(root, bpm=120, length_bars=2)
    api.track_add(root, 'bass', instrument={'type': 'synth'})
    api.notes_write(root, 'bass', 1, '0 A2 1; 2 A2 1', repeat=2)
    return root


# ------------------------------------------------------------------ the reference line (M245 part B)

def test_render_reply_without_a_reference_says_nothing_about_one(tmp_path):
    root = _song(str(tmp_path / 's'))
    out = api.render(root, cache=False, wait='0')
    assert 'reference' not in out and 'hear it' not in out          # a plain iteration render: no block either


def test_render_reply_names_the_comparison_not_yet_run(tmp_path):
    root = _song(str(tmp_path / 's'))
    ref = _wav(str(tmp_path / 'ref.wav'))
    out = api.project_set(root, reference=ref)
    assert 'next with a reference' in out and 'levels_from_ref' in out and 'never averaged down' in out
    out = api.render(root, cache=False, wait='0')
    assert 'compared with the reference (ref.wav): never' in out and 'sound_compare on the part the person named' in out
    api.compare(root, bars=[1, 2])
    rec = replies.last_compared(root)
    assert rec['op'] == 'compare' and 'render vs ref' in rec['what']
    out = api.render(root, cache=False, wait='0')
    line = next(ln for ln in out.splitlines() if ln.startswith('compared with the reference'))
    assert 'not since this render (last: compare render vs ref bars 1-2' in line


def test_render_reply_points_at_ref_audio_that_is_not_the_reference(tmp_path):
    song = tmp_path / 'songs' / 'tune'
    root = _song(str(song / 'proj'))
    _wav(str(song / 'ref' / 'groove.wav'), secs=0.5)
    _wav(str(song / 'ref' / 'stems' / 'drums.wav'), secs=0.5)        # a derived stem is not a reference
    out = api.render(root, cache=False, wait='0')
    line = next(ln for ln in out.splitlines() if ln.startswith('ref/ holds'))
    assert 'ref/groove.wav' in line and 'more' not in line and "project_set(reference=" in line


def test_project_new_with_a_reference_says_what_to_run_next(tmp_path):
    ref = _wav(str(tmp_path / 'ref.wav'), secs=0.5)
    out = api.project_new(str(tmp_path / 's'), bpm=120, length_bars=2, reference=ref)
    assert 'next with a reference' in out and 'analyze_structure' in out


def test_kit_help_names_the_octave_for_voice_authors():
    for t in ('kit', 'code'):
        out = api.instrument_help(type=t)
        assert 'C1 = MIDI 24' in out and '36' in out and 'by name' in out


# ------------------------------------------------------------------ hear it (M247)

def test_render_reply_hear_it_block_after_a_named_render(tmp_path):
    root = _song(str(tmp_path / 's'))
    out = api.render(root, cache=False, wait='0', out='draft1')
    block = out[out.index('hear it'):].splitlines()
    assert block[1].strip() == f"<file>: {os.path.join(os.path.abspath(root), 'renders', 'draft1.wav')}"
    assert block[-1].startswith('  one option: play_file(') and 'feedback_page(' in block[-1]
    assert 3 <= len(block) <= 7


def test_render_reply_hear_it_follows_a_standing_choice(tmp_path):
    root = _song(str(tmp_path / 's'))
    out = api.project_set(root, listen_pref='send every draft to my phone')
    assert "listen_pref='send every draft to my phone'" in out
    assert "listen_pref" in api.project_info(root)
    block = replies.hear_it(root, os.path.join(root, 'renders', 'x.mp3'), api._load(root).d)
    assert "their standing choice" in block and 'send every draft to my phone' in block and 'say you did' in block
    assert 'cleared' in api.project_set(root, listen_pref='')
    assert 'standing choice' not in replies.hear_it(root, os.path.join(root, 'x.mp3'))


def test_render_reply_hear_it_phone_seen_recently_suggests_the_phone(tmp_path, quiet_machine):
    fake = Fake({'/health': {'ok': True, 'devices': [{'kind': 'Quest', 'last_s': 5},
                                                     {'kind': 'phone', 'last_s': 150}]}})
    try:
        (quiet_machine / 'phone' / 'server.json').write_text(json.dumps({'port': fake.port, 'pid': os.getpid()}))
        block = replies.hear_it(str(tmp_path), str(tmp_path / 'a.mp3'))
        assert f'phone page: up (port {fake.port}), their phone checked in 2 min ago' in block
        assert 'one option: their phone checked in 2 min ago: phone_offer(' in block
    finally:
        fake.close()


def test_render_reply_hear_it_older_phone_server_and_a_stale_phone(tmp_path, quiet_machine):
    fake = Fake({'/health': {'ok': True}})                       # a server from before the devices field
    try:
        (quiet_machine / 'phone' / 'server.json').write_text(json.dumps({'port': fake.port, 'pid': os.getpid()}))
        block = replies.hear_it(str(tmp_path), str(tmp_path / 'a.mp3'))
        assert f'phone page: up (port {fake.port})\n' in block and 'phone_offer' not in block
        assert 'one option: play_file(' in block
    finally:
        fake.close()


def test_render_reply_hear_it_in_vr_suggests_the_stage(tmp_path, quiet_machine):
    fake = Fake({'/live/presence': {'in_vr': True, 'scene': 'room'}})
    try:
        (quiet_machine / 'stage' / f'{fake.port}.json').write_text(json.dumps(
            {'port': fake.port, 'pid': os.getpid(), 'scenes': str(tmp_path / 'scenes'), 'started': time.time()}))
        block = replies.hear_it(str(tmp_path), str(tmp_path / 'a.mp3'))
        assert 'stage: they are in the headset, scene room' in block
        assert "one option: they are in VR" in block and "stage_music(scene='room', url='scenes/room/" in block
    finally:
        fake.close()


def test_render_reply_hear_it_skips_servers_that_are_down_quickly(tmp_path, quiet_machine):
    dead = _closed_port()
    (quiet_machine / 'stage' / f'{dead}.json').write_text(json.dumps({'port': dead, 'pid': os.getpid()}))
    (quiet_machine / 'phone' / 'server.json').write_text(json.dumps({'port': dead, 'pid': os.getpid()}))
    (quiet_machine / 'feedback' / 'server.json').write_text(json.dumps({'port': dead, 'pid': os.getpid()}))
    (quiet_machine / 'stage' / 'gone.json').write_text(json.dumps({'port': 1, 'pid': 2 ** 30}))   # a dead pid
    t0 = time.time()
    block = replies.hear_it(str(tmp_path), str(tmp_path / 'a.mp3'))
    assert time.time() - t0 < 3
    assert 'stage' not in block and 'phone page' not in block and 'one option: play_file(' in block
    assert (quiet_machine / 'stage' / 'gone.json').exists()          # read only: nothing removed or started


def test_render_reply_hear_it_open_feedback_round_suggests_adding_it(tmp_path):
    root = _song(str(tmp_path / 's'))
    FB.build(root, 'r1', [], ['What do you hear first?'])
    block = replies.hear_it(root, os.path.join(root, 'renders', 'b.mp3'))
    assert 'feedback page: round r1, not sent yet, page server not running' in block
    assert "one option: round r1 is open: feedback_page(project, round='r1'" in block
    FB.write_json(FB.round_dir(root, 'r1') / 'done.json', {'sent': ['2026-10-18T10:00:00+01:00'],
                                                           'at': '2026-10-18T10:00:00+01:00', 'changed_at': None})
    block = replies.hear_it(root, os.path.join(root, 'renders', 'b.mp3'))
    assert 'round r1, sent' in block and 'one option: play_file(' in block


def test_render_reply_hear_it_live_engine_for_this_song(tmp_path, quiet_machine):
    root = _song(str(tmp_path / 's'))
    os.makedirs(os.path.join(root, 'live'))
    with open(os.path.join(root, 'live', 'engine.json'), 'w') as f:      # what a running engine leaves
        f.write('{}')
    (quiet_machine / 'live' / 'e.json').write_text(json.dumps({'project': str(root), 'port': 8999,
                                                              'pid': os.getpid(), 'started': time.time()}))
    block = replies.hear_it(root, os.path.join(root, 'renders', 'b.mp3'))
    assert 'live engine: port 8999' in block and 'one option: a live engine runs for this song: live_load(' in block
