"""Played keys (live_midi): a MIDI input patched to an instrument in the live engine sounds at the next device
pull, swaps sound on request, follows note-offs, the sustain pedal, channel and transpose, notices inputs coming
and going, and logs every key with the beat it sounded against; live_played reads the log back and fits a take to
the 16th grid. All silent: device='none', and a stand-in for the MIDI driver."""
import json
import os
import re

import numpy as np
import pytest

from ismail.dsp import SR
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live import midi_play as MP
from ismail.live.midi_play import MidiPlay


class FakeInput:
    opened = {}

    def __init__(self, name, on_msg):
        self.name, self.on = name, on_msg
        FakeInput.opened[name] = self

    def close(self):
        FakeInput.opened.pop(self.name, None)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(MP, 'LO', 48)              # two octaves render faster than five
    monkeypatch.setattr(MP, 'HI', 72)
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.inputs = ['flmcp Request', 'ismail keys']
    e.midi = MidiPlay(e, sources=lambda: list(e.inputs), opener=FakeInput)
    FakeInput.opened.clear()
    return e


def key(name, st, d1, d2):
    FakeInput.opened[name].on(0.0, st, d1, d2)


def pulls(e, seconds):
    """The mixer and the device in turn, as the engine's threads run them."""
    out = []
    for _ in range(int(seconds * SR / BLOCK)):
        e._fifo.append(e.mix_block().T.copy())
        e._fifo_n += BLOCK
        out.append(e._pull(BLOCK))
    return np.concatenate(out)


def test_a_key_sounds_at_the_next_pull_and_stops_after_its_release(eng):
    r = eng.cmd_midi(source='keys', instrument='preset:pluck')
    assert 'ismail keys: patched' in r and 'rendering 25 notes' in r and '25/25 notes ready' in r
    assert any('all 25 notes' in n for n in eng.drain_news())
    assert np.max(np.abs(eng._pull(BLOCK))) == 0              # nothing queued, nothing pressed: silence
    key('ismail keys', 0x90, 60, 100)
    assert np.max(np.abs(eng._pull(BLOCK))) > 0.01             # the very next block
    key('ismail keys', 0x80, 60, 0)
    pulls(eng, 0.6)
    assert eng.midi.voices == [] and np.max(np.abs(eng._pull(BLOCK))) == 0
    assert np.max(np.abs(eng.air)) > 0.01                      # live_listen hears the keys
    assert 'ismail keys: plays preset:pluck | 25/25 notes ready | 1 played' in eng.cmd_status()


def test_velocity_transpose_channel_and_the_sustain_pedal(eng):
    eng.cmd_midi(source='ismail keys', instrument='preset:pluck')
    key('ismail keys', 0x90, 60, 30)
    soft = np.max(np.abs(eng._pull(BLOCK)))
    eng.midi.voices = []
    key('ismail keys', 0x90, 60, 127)
    assert np.max(np.abs(eng._pull(BLOCK))) > 3 * soft
    eng.midi.voices = []
    eng.cmd_midi(source='keys', channel=2, transpose=12)
    key('ismail keys', 0x90, 60, 100)                          # channel 1: ignored
    assert eng.midi.voices == []
    key('ismail keys', 0x91, 60, 100)
    assert eng.midi.voices[0].y is eng.midi.patches['ismail keys']['bank'][72]
    key('ismail keys', 0xB1, 64, 127)                          # pedal down: the note-off waits for the pedal
    key('ismail keys', 0x81, 60, 0)
    pulls(eng, 0.5)
    assert len(eng.midi.voices) == 1 and eng.midi.voices[0].rel is None
    key('ismail keys', 0xB1, 64, 0)
    assert eng.midi.voices[0].rel is not None
    key('ismail keys', 0x91, 100, 100)                         # out of the rendered range: counted, silent
    assert eng.midi.patches['ismail keys']['missed'] == 1


def test_swapping_to_a_tracks_sound_and_a_drum_plays_on_every_key(eng):
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    eng.cmd_track('k', instrument={'type': 'kick'})
    r = eng.cmd_midi(source='keys', track='k')
    assert "rendering 1 notes of track k's sound" in r
    key('ismail keys', 0x90, 40, 100)
    key('ismail keys', 0x90, 81, 100)
    assert len(eng.midi.voices) == 2 and eng.midi.voices[0].y is eng.midi.voices[1].y
    key('ismail keys', 0x80, 40, 0)                            # a hit rings out: note-offs do nothing
    assert all(v.rel is None for v in eng.midi.voices)
    with pytest.raises(LiveError, match="no live track 'nope'"):
        eng.cmd_midi(source='keys', track='nope')


def test_what_it_says_when_asked_wrong(eng):
    assert "MIDI inputs: 'flmcp Request', 'ismail keys'" in eng.cmd_midi()
    assert 'nothing patched' in eng.cmd_midi()
    with pytest.raises(LiveError, match="no such input; inputs: \\['flmcp Request', 'ismail keys'\\]"):
        eng.cmd_midi(source='piano', instrument='preset:pluck')
    with pytest.raises(LiveError, match='is not patched yet: say what it plays'):
        eng.cmd_midi(source='keys')
    with pytest.raises(LiveError, match='volume_db <= \\+6'):
        eng.cmd_midi(source='keys', instrument='preset:pluck', volume_db=12)
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    assert 'unpatched' in eng.cmd_midi(source='keys', remove=True) and 'ismail keys' not in FakeInput.opened


def test_inputs_plugged_in_and_out_are_noticed_and_a_patched_one_comes_back(eng):
    eng.midi.poll()
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    eng.drain_news()
    eng.inputs.append('Digital Piano')
    eng.midi.poll()
    assert any("MIDI input plugged in: 'Digital Piano'. live_midi(source='Digital Piano'" in n for n in eng.drain_news())
    key('ismail keys', 0x90, 60, 100)
    eng.inputs.remove('ismail keys')
    eng.midi.poll()
    assert any('keys ismail keys: unplugged' in n for n in eng.drain_news())
    assert 'ismail keys' not in FakeInput.opened and eng.midi.voices[0].rel is not None
    assert '(UNPLUGGED)' in eng.cmd_status()
    eng.inputs.append('ismail keys')
    eng.midi.poll()
    assert any('plugged back in' in n for n in eng.drain_news()) and 'ismail keys' in FakeInput.opened


def test_every_key_is_logged_with_the_beat_heard(eng, tmp_path):
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    pulls(eng, 2.0)
    key('ismail keys', 0x90, 64, 90)
    key('ismail keys', 0x80, 64, 0)
    eng.midi.flush()
    recs = [json.loads(x) for x in open(os.path.join(str(tmp_path), 'live', 'midi', 'ismail_keys.jsonl'))]
    assert [(r['st'], r['d1'], r['d2']) for r in recs] == [(0x90, 64, 90), (0x80, 64, 0)]
    assert 3.0 < recs[0]['beat'] < 4.0             # 2 s pulled at 120 BPM, less the path's lookahead


def test_loud_keys_on_a_loud_set_are_soft_clipped_not_wrapped(eng):
    eng.cmd_midi(source='keys', instrument='preset:pluck', volume_db=6)
    for m in range(48, 72):
        key('ismail keys', 0x90, m, 127)
    out = np.full((BLOCK, 2), 0.8, dtype=np.float32)
    eng.midi.mix(out)
    assert np.max(np.abs(out)) <= 1.0
    assert 'soft-clipped' in eng.cmd_midi()


def test_what_was_played_reads_back_as_notes_text(eng):
    assert 'no keys played yet' in eng.cmd_played()
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    pulls(eng, 2.0)
    key('ismail keys', 0x90, 60, 90)
    key('ismail keys', 0xB0, 64, 127)          # pedal down: C4's release waits for it
    key('ismail keys', 0x80, 60, 0)
    pulls(eng, 0.5)
    key('ismail keys', 0x90, 64, 70)
    key('ismail keys', 0x80, 64, 0)
    pulls(eng, 0.5)
    key('ismail keys', 0xB0, 64, 0)
    key('ismail keys', 0x90, 67, 100)          # still held when read
    r = eng.cmd_played(source='keys')
    lines = r.splitlines()
    assert lines[0].startswith('ismail_keys: 3 of 3 notes, bars 1-2 (1 still held)'), r
    c4 = next(x for x in lines if 'C4' in x)
    assert 'bar   1' in c4 and 'd=1.95' in c4 and 'v=90' in c4  # two pulls of whole blocks at 120 BPM: the pedal's hold
    assert any('E4' in x and 'v=70' in x for x in lines) and any('G4' in x for x in lines)


def test_the_screen_keys_play_into_the_engine_with_no_port(eng, tmp_path):
    """The on-screen keys page (live_keys_page): its messages reach the 'screen keys' patch as a driver's would."""
    assert eng.cmd_keys(events=[[1, 0x90, 60, 100]], sid='a') == {'patched': False, 'connected': True}
    assert eng.midi.voices == []                                # not patched: the key is dropped
    assert "'screen keys', the on-screen keys page (live_keys_page)" in eng.cmd_midi()
    r = eng.cmd_midi(source='screen keys', instrument='preset:pluck')
    assert 'screen keys: patched' in r and 'screen keys' not in FakeInput.opened   # no MIDI port opened
    st = eng.cmd_keys(sid='a')
    assert st['patched'] and st['what'] == 'preset:pluck' and st['ready'] == st['of'] == 25
    assert st['keys'] == list(range(48, 73)) and st['sustain'] is False and st['connected']
    eng.cmd_keys(events=[[2, 0x90, 60, 30]], sid='a')
    soft = np.max(np.abs(eng._pull(BLOCK)))
    assert soft > 0.001
    eng.midi.voices = []
    eng.cmd_keys(events=[[3, 0x80, 60, 0], [4, 0x90, 62, 127]], sid='a')
    assert np.max(np.abs(eng._pull(BLOCK))) > 3 * soft          # near the bottom of the key is loud
    assert eng.cmd_keys(events=[[5, 0xB0, 64, 127], [6, 0x80, 62, 0]], sid='a')['sustain'] is True
    assert len(eng.midi.voices) == 1 and eng.midi.voices[0].rel is None   # the pedal holds it
    eng.cmd_keys(events=[[7, 0xB0, 64, 0]], sid='a')
    assert eng.midi.voices[0].rel is not None
    eng.midi.flush()
    recs = [json.loads(x) for x in open(os.path.join(str(tmp_path), 'live', 'midi', 'screen_keys.jsonl'))]
    assert [(r['st'], r['d1'], r['d2']) for r in recs][:2] == [(0x90, 60, 30), (0x80, 60, 0)]
    assert eng.cmd_played(source='screen keys').startswith('screen_keys: 2 of 2 notes')


def test_a_screen_key_that_arrives_late_never_undoes_a_newer_one(eng):
    eng.cmd_midi(source='screen keys', instrument='preset:pluck')
    eng.cmd_keys(events=[[10, 0x80, 64, 0]], sid='a')           # the release overtook its press
    eng.cmd_keys(events=[[9, 0x90, 64, 100]], sid='a')
    assert len(eng.midi.voices) == 1 and eng.midi.voices[0].rel is not None   # a short tap, not a stuck note
    eng.cmd_keys(events=[[12, 0x90, 65, 90]], sid='a')
    eng.cmd_keys(events=[[14, 0xB0, 64, 0]], sid='a')
    eng.cmd_keys(events=[[11, 0x90, 65, 40], [13, 0xB0, 64, 127]], sid='a')   # older than what it played: dropped
    assert len(eng.midi.voices) == 2 and eng.midi.patches['screen keys']['sustain'] is False
    eng.cmd_keys(events=[[1, 0x90, 67, 90]], sid='b')           # another page counts on its own
    assert len(eng.midi.voices) == 3


def test_the_screen_keys_say_what_is_wrong_and_partial_names_still_find_real_inputs(eng):
    with pytest.raises(LiveError, match='status a note-off, note-on or controller'):
        eng.cmd_keys(events=[[1, 0xF0, 0, 0]])
    with pytest.raises(LiveError, match='at most 64'):
        eng.cmd_keys(events=[[i, 0x90, 60, 100] for i in range(65)])
    with pytest.raises(LiveError, match=r'\[seq, status, data1, data2\]'):
        eng.cmd_keys(events=[[1, 0x90, 60]])
    assert eng.midi.find('keys') == 'ismail keys' and eng.midi.find('screen') == 'screen keys'
    with pytest.raises(ValueError, match=r"'screen keys' \(the on-screen keys page, live_keys_page\)"):
        eng.midi.find('piano')


def test_the_engine_serves_the_keys_page_and_a_key_leaves_the_agents_news(eng):
    import urllib.request
    from ismail.live.engine import serve
    eng.cmd_midi(source='screen keys', instrument='preset:pluck')
    eng.drain_news()
    httpd = serve(eng, 0)
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        with urllib.request.urlopen(base + '/keys', timeout=10) as r:
            assert r.headers['Content-Type'].startswith('text/html') and b"'/api/keys'" in r.read()
        eng.news.append('a chapter changed')
        req = urllib.request.Request(base + '/api/keys', data=json.dumps({'sid': 'p', 'events': [[1, 0x90, 60, 90]]})
                                     .encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=10) as r:
            j = json.loads(r.read())
        assert j['ok'] and j['result']['patched'] and j['result']['connected'] and len(eng.midi.voices) == 1
        assert list(eng.news) == ['a chapter changed']               # still there for the agent's next call
    finally:
        httpd.shutdown()


def test_live_keys_page_gives_the_addresses_and_puts_a_panel_on_the_phone(eng, tmp_path, monkeypatch):
    from ismail.api import OpError
    from ismail.live import ops as LO
    from ismail.live.engine import serve
    from ismail.phone import ops as PO
    httpd = serve(eng, 0)
    port = httpd.server_address[1]
    os.makedirs(os.path.join(str(tmp_path), 'live'), exist_ok=True)
    with open(os.path.join(str(tmp_path), 'live', 'engine.json'), 'w', encoding='utf8') as f:
        json.dump({'port': port}, f)
    panels = []
    try:
        monkeypatch.setattr(PO, '_port', lambda: None)
        r = LO.live_keys_page(str(tmp_path))
        assert f'http://127.0.0.1:{port}/keys' in r and 'phone_start(), then live_keys_page(phone=True)' in r
        assert "live_midi(project=" in r and "source='screen keys'" in r            # nothing patched yet
        with pytest.raises(OpError, match=r'phone_start\(\) first'):
            LO.live_keys_page(str(tmp_path), phone=True)
        eng.cmd_midi(source='screen keys', instrument='preset:pluck')
        monkeypatch.setattr(PO, '_port', lambda: 8870)
        monkeypatch.setattr(PO, '_tailnet', lambda p: ('https://box.tail.ts.net:8870/', ''))
        monkeypatch.setattr(PO, '_call', lambda op, **kw: panels.append((op, kw)))
        r = LO.live_keys_page(str(tmp_path), phone=True, sender='jam')
        assert 'on the phone: https://box.tail.ts.net:8870/keys' in r and 'the keys play preset:pluck' in r
        assert panels[0][0] == 'panel_show' and panels[0][1]['link'] == '/keys?from=phone'
        assert panels[0][1]['who'] == 'jam'
    finally:
        httpd.shutdown()


# ------------------------------------------------------------------ the clock and the fit (live_played)

def test_a_player_by_ear_lands_on_the_beat_he_heard(eng):
    """The whole path, silent: a kick on every beat goes through the mixer, the fifo and a device with a 180 ms
    output buffer. A perfect player presses each key one key delay before the kick reaches the speaker, so the note
    he hears sounds with the kick he hears. live_played must put every key on its beat: logged at the press on the
    heard clock (as before 2026-10-14) they read 180 ms (0.36 beat) early."""
    import types
    from ismail.live import graph as G
    from ismail.live.engine import AHEAD_S
    lat = 0.18
    eng.stream = types.SimpleNamespace(latency=lat)          # only its latency is read
    eng.cmd_track('k', instrument={'type': 'kick'})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    dev, presses = [], {}

    def step(press_at=()):
        while eng._fifo_n < int(AHEAD_S * SR):                  # the mixer keeps its half second ahead
            eng.tick()
            eng._fifo.append(eng.mix_block().T.copy())
            eng._fifo_n += BLOCK
        for d in press_at:                                        # the key joins the next device buffer
            if abs(eng.played - d) <= BLOCK // 2:
                key('ismail keys', 0x90, 60, 100)
                presses[d] = eng.played
        for d, at in list(presses.items()):
            if eng.played - at == 4 * BLOCK:
                key('ismail keys', 0x80, 60, 0)
        dev.append(eng._pull(BLOCK))

    for _ in range(int(3.0 * SR / BLOCK)):                        # no keys yet: where does a kick leave the device?
        step()
    y = np.abs(np.concatenate(dev)[:, 0])
    above = y > 0.02
    hits = np.nonzero(above[1:] & ~above[:-1])[0] + 1
    beat = eng.sample(1)
    first = int(hits[0])
    # the device is handed beat b's music at sample(b) + the path budget + the safety lookahead (the bar 2 kick)
    assert abs(first - (eng.sample(4) + G.LAT_BUDGET + eng.safety.la)) < 0.001 * SR
    # it reaches the speaker `lat` later, and a key pressed when the device stands at d reaches it `lat` after d,
    # so the perfect player's key enters the device at each kick's own position
    want = [first + (b - 4) * beat for b in range(8, 16)]
    while eng.played < want[-1] + 8 * BLOCK:
        step(want)
    assert len(presses) == 8
    r = eng.cmd_played(source='keys')
    starts = [(int(b) - 1) * 4 + float(x) for b, x in re.findall(r'bar\s+(\d+) \+([\d.]+)\s+C4', r)]
    assert [round(s) for s in starts] == list(range(8, 16)), r
    assert all(abs(s - round(s)) < 0.03 for s in starts), r     # half a device buffer at most
    assert 'the key press plus the 180 ms the keys take to reach the speakers' in r
    recs = [json.loads(x) for x in open(eng.midi.patches['ismail keys']['log']) if x.strip()]
    on = [x for x in recs if x['st'] == 0x90]
    assert all(abs(x['beat'] - x['press'] - lat / 0.5) < 0.001 for x in on)    # the press is kept, 0.36 beat before


def _log(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf8') as f:
        f.write(''.join(json.dumps(r) + '\n' for r in recs))


def test_fit_finds_the_grid_the_shape_and_offers_two_placements(eng, tmp_path):
    """A take in bars 3-5 played 3-3-2 from the 'and' of beat 1, 45 ms behind the 16ths, two hands per hit 0.04 beat
    apart, a keyboard that sends v=80. Rounding the raw times would put hits on the wrong 16th; the fit does not."""
    rng = np.random.default_rng(1)
    half = [0, 3, 6]                                              # 16ths of the shape in each half bar
    true = [8.5 + 2 * h + s * 0.25 for h in range(4) for s in half]
    recs = [{'wall': 0, 'beat': 2.0, 'st': 0x90, 'd1': 50, 'd2': 80},      # an older log line, bar 1
            {'wall': 0, 'beat': 2.5, 'st': 0x80, 'd1': 50, 'd2': 0}]
    for t in true:
        b = t + 0.09 + rng.uniform(-0.015, 0.015)
        for m, dt in ((57, 0.0), (72, 0.04)):
            recs.append({'wall': 0, 'beat': b + dt, 'press': b + dt - 0.36, 'st': 0x90, 'd1': m, 'd2': 80})
            recs.append({'wall': 0, 'beat': b + dt + 0.2, 'press': b + dt - 0.16, 'st': 0x80, 'd1': m, 'd2': 0})
    recs.append({'wall': 0, 'beat': 30.0, 'press': 29.64, 'st': 0x90, 'd1': 60, 'd2': 80})   # bar 8: not chosen
    recs.append({'wall': 0, 'beat': 30.5, 'press': 30.14, 'st': 0x80, 'd1': 60, 'd2': 0})
    _log(os.path.join(str(tmp_path), 'live', 'midi', 'ismail_keys.jsonl'), sorted(recs, key=lambda r: r['beat']))

    r = eng.cmd_played(source='keys', bars=[3, 4], fit=True)
    assert 'ismail_keys: 22 of 26 notes, bars 3-4' in r, r
    assert 'the key press plus the 180 ms' in r and '1 notes come from a log written before this clock' in r
    m = re.search(r'fit: 11 hits .*? they sit (\d+) ms after the 16ths, (\d+) ms rms off them, drifting (\d+) ms per '
                  r'bar \(steady\)', r)
    assert m and 38 <= int(m[1]) <= 52 and int(m[2]) <= 12, r
    assert 'rhythm: 3-3-2 in 16ths, repeating every 8 16ths (2 beats)\n' in r
    assert 'play both and let the player pick by ear' in r
    lines = r.splitlines()
    a = lines.index(next(x for x in lines if x.startswith('A, as played')))
    b = lines.index(next(x for x in lines if x.startswith('B, groups from beat 1')))
    assert 'first hit at bar 3 +0.5; as a live_queue clip from the start of bar 3, bars=2' in lines[a]
    assert lines[a + 1] == ('0.5 A3 0.25 80; 0.5 C5 0.25 80; 1.25 A3 0.25 80; 1.25 C5 0.25 80; 2 A3 0.25 80; '
                            '2 C5 0.25 80; 2.5 A3 0.25 80; 2.5 C5 0.25 80; 3.25 A3 0.25 80; 3.25 C5 0.25 80')
    assert 'everything 0.5 beat earlier, so the 3-3-2 groups start on beat 1 and beat 3' in lines[b]
    assert 'bar 3 +0;' in lines[b]
    assert lines[b + 1].startswith('0 A3 0.25 80; 0 C5 0.25 80; 0.75 A3 0.25 80')
    assert 'every key came at v=80' in r and 'accent shape' in r
    # the lines are live_queue notes as they stand
    notes = ' ; '.join(lines[a + 1:b])
    eng.cmd_track('p', instrument='preset:pluck')
    assert 'c1 p:' in eng.cmd_queue([{'track': 'p', 'notes': notes, 'bars': 2}])

    r = eng.cmd_played(source='keys', bars=[3, 4], fit=True, from_bar=2)
    assert 'clip from the start of bar 2, bars=3' in r and '\n4.5 A3 0.25 80; 4.5 C5' in r

    # the raw times rounded to 16ths: the 45 ms (0.09 beat) offset pushes 0.75 + 0.09 + jitter toward the wrong line
    raw = sorted({round(x['beat'], 3) for x in recs if x['st'] == 0x90 and x['d1'] == 57})
    assert MP.fit_grid(raw)['k'].tolist() == [round(t * 4) for t in true]


def test_shape_and_grid_on_their_own():
    assert MP.rhythm_shape([0, 3, 6, 8, 11, 14, 16]) == ('3-3-2', 8, 0)
    # 3-2-3 from the first hit is a 3-3-2 cycle that starts 5 16ths in (a take that began on its last 3)
    assert MP.rhythm_shape([0, 3, 5, 8, 11, 13, 16]) == ('3-3-2', 8, 5)
    assert MP.rhythm_shape([0, 2, 4, 6, 8]) == ('2', 2, 0)
    assert MP.rhythm_shape([0, 5, 7]) == ('5-2', None, 0)
    # half a 16th off: rounding is a coin toss, the free offset is not
    g = MP.fit_grid([0.125 + 0.25 * k for k in (0, 3, 6, 8, 11, 14)])
    assert abs(abs(g['offset']) - 0.125) < 0.002 and np.diff(g['k']).tolist() == [3, 3, 2, 3, 3]
    # a player who drags 0.01 beat per beat over two bars (0.01 / 1.01 of the time played): measured and taken out
    t = [s + 0.01 * s for s in np.arange(0, 8, 0.75)]
    g = MP.fit_grid(t)
    assert abs(g['drift'] - 0.01 / 1.01) < 1e-6 and g['rms'] < 1e-6 and (g['k'] == np.arange(0, 32, 3)).all()


def test_played_bars_and_from_bar_say_what_to_do(eng):
    eng.cmd_midi(source='keys', instrument='preset:pluck')
    pulls(eng, 1.0)
    key('ismail keys', 0x90, 60, 90)
    key('ismail keys', 0x80, 60, 0)
    assert 'no notes in bars 5-6; it has notes in bars 1-1' in eng.cmd_played(source='keys', bars=[5, 6])
    with pytest.raises(LiveError, match='bars=\\[a, b\\]'):
        eng.cmd_played(source='keys', bars=[4, 2])
    with pytest.raises(LiveError, match='use it with fit=True'):
        eng.cmd_played(source='keys', from_bar=1)
    assert 'a grid needs at least 3' in eng.cmd_played(source='keys', fit=True)
    assert 'no output delay was measured' in eng.cmd_played(source='keys')
