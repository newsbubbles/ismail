"""Listening together (ismail/listen.py, the listen_* ops): the reading path needs no sound device, so these build a
session folder from synthetic audio and read it."""
import json
import os
import types
import time

import numpy as np
import pytest
import soundfile as sf

from ismail import api, listen as LI

SR = 22050
SPB = 0.5                      # 120 BPM


def _music(segments, sr=SR, seed=0):
    """segments: [(seconds, chord root Hz, beat phase in beats)]. Kicks on the beat and hats on the off-beat follow
    the segment's phase on one global grid, so phase 0 -> 0 is a change and 0 -> 0.35 a skip. The hats are fresh
    noise each time, so the mic's lag has one answer."""
    rng = np.random.default_rng(seed)
    total = sum(s[0] for s in segments)
    n = int(total * sr)
    y = 0.003 * rng.standard_normal(n)
    t0 = 0.0
    for dur, root, ph in segments:
        a, b = int(t0 * sr), int((t0 + dur) * sr)
        tt = np.arange(b - a) / sr
        for r in (1.0, 1.26, 1.5):
            y[a:b] += 0.08 * np.sin(2 * np.pi * root * r * tt)
        k = np.ceil((t0 / SPB) - ph)
        while (k + ph) * SPB < t0 + dur:
            for off, kind in ((0.0, 'kick'), (0.5, 'hat')):
                s = (k + ph + off) * SPB
                if t0 <= s < t0 + dur:
                    i = int(s * sr)
                    m = min(int(0.15 * sr), b - i)
                    e = np.exp(-np.arange(m) / sr / (0.06 if kind == 'kick' else 0.015))
                    sig = np.sin(2 * np.pi * 60 * np.arange(m) / sr) if kind == 'kick' else rng.standard_normal(m)
                    y[i:i + m] += (0.6 if kind == 'kick' else 0.3) * e * sig
            k += 1
        t0 += dur
    return (0.5 * y / np.abs(y).max()).astype(np.float32)


def _stream(name, path, sr, t_start, frames, loopback, **kw):
    return {'name': name, 'spec': 'speakers' if loopback else 'mic', 'device': f'test {name}', 'sr': sr, 'ch': 1,
            'file': os.path.basename(path), 'frames': frames, 'loopback': loopback, 'dropouts': [], 'anchors': [],
            't_start': t_start, **kw}


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every home in the test folder, and an empty live registry: a jam running on this machine is never picked."""
    monkeypatch.setattr(LI, 'HOME', tmp_path / 'listen')
    monkeypatch.setattr(LI, 'DEFAULT_HOME', tmp_path / 'listen')
    monkeypatch.setattr(LI, 'LEGACY_HOME', tmp_path / 'legacy')
    monkeypatch.delenv('ISMAIL_LISTEN_HOME', raising=False)
    (tmp_path / 'registry').mkdir()
    monkeypatch.setenv('ISMAIL_LIVE_REGISTRY', str(tmp_path / 'registry'))
    (tmp_path / 'listen').mkdir()
    return tmp_path / 'listen'


def _session(home, name='2026-01-01_120000', state='stopped', mic=None, events=(), music=None):
    d = home / name
    d.mkdir()
    y = _music([(30, 220, 0.0), (20, 262, 0.0), (10, 330, 0.35)]) if music is None else music
    sf.write(d / 'speakers.wav', y, SR, subtype='FLOAT')
    streams = [_stream('speakers', d / 'speakers.wav', SR, 0.5, len(y), True)]
    if mic is not None:
        sf.write(d / 'mic.wav', mic, SR, subtype='FLOAT')
        streams.append(_stream('mic', d / 'mic.wav', SR, 0.5, len(mic), False))
    wall0 = 1_000_000.0
    LI.write_json(d / 'request.json', {'streams': [s['spec'] for s in streams], 'max_s': 600})
    LI.write_json(d / 'status.json', {'state': state, 'pid': os.getpid(), 'wall0': wall0, 'date': '2026-01-01 12:00',
                                      'max_s': 600, 'mic': mic is not None, 'label': '', 'streams': streams,
                                      'stopped_at_s': len(y) / SR + 0.5, 'elapsed_s': 3.0})
    for ev in events:
        LI.add_event(d, {**ev, 'wall': wall0 + ev['wall']})
    return d


# ------------------------------------------------------------------ consent and arguments

def test_a_mic_needs_the_persons_yes():
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers', 'mic'])
    assert 'mic_ok=True' in str(e.value) and 'yes' in str(e.value)


def test_an_unknown_stream_names_the_kinds():
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['webcam'])
    assert "'speakers'" in str(e.value) and "'mic:" in str(e.value)


# ------------------------------------------------------------------ the clock

def test_a_file_maps_to_session_time_through_its_anchors():
    st = {'t_start': 1.0, 'anchors': [[10.0, 2.0]]}     # 2 s with nothing from the device at file time 10
    assert LI.to_session(st, 5.0) == 6.0
    assert LI.to_session(st, 12.0) == 15.0
    assert LI.to_file(st, 6.0) == 5.0
    assert LI.to_file(st, 15.0) == 12.0
    assert LI.to_file(st, 12.0) == 10.0                  # inside the gap: where the file resumed


# ------------------------------------------------------------------ measurements

def test_loudness_agrees_with_pyloudnorm_and_true_peak_reads_a_sine():
    import pyloudnorm as pyln
    rng = np.random.default_rng(1)
    t = np.arange(SR * 20) / SR
    y = (0.3 * np.sin(2 * np.pi * 440 * t) * (1 + 0.5 * np.sin(2 * np.pi * 0.2 * t)) +
         0.02 * rng.standard_normal(len(t))).astype(np.float32)
    m = LI.Loudness(SR, 1)
    for i in range(0, len(y), SR * 3):                   # fed in blocks, as the reading does
        m.feed(y[i:i + SR * 3, None])
    r = m.result()
    assert abs(r['lufs'] - pyln.Meter(SR).integrated_loudness(y.astype(np.float64))) < 0.3
    assert r['lra'] > 1.0                                 # the slow swell is a loudness range
    m = LI.Loudness(SR, 1)
    m.feed((0.5 * np.sin(2 * np.pi * 1000 * t[:SR * 2])).astype(np.float32))
    assert abs(m.result()['true_peak'] - (-6.0)) < 0.2


def test_cuts_tell_a_skip_from_a_change(tmp_path):
    p = tmp_path / 'music.wav'
    sf.write(p, _music([(20, 220, 0.0), (20, 262, 0.0), (20, 330, 0.35)]), SR, subtype='FLOAT')
    cs = LI.cuts(p)
    near = lambda t: [c for c in cs if abs(c['t'] - t) < 0.6]          # noqa: E731
    assert [c['kind'] for c in near(20)] == ['change'], cs
    assert [c['kind'] for c in near(40)] == ['skip'], cs
    assert not [c for c in cs if c['kind'] == 'skip' and abs(c['t'] - 40) >= 0.6], cs


def test_the_mic_lag_and_its_drift_are_measured(tmp_path):
    rng = np.random.default_rng(2)
    sr, dur = 16000, 300
    ref = rng.standard_normal(sr * dur).astype(np.float32) * 0.2
    t = np.arange(sr * dur) / sr
    lag = 0.08 + 0.00003 * t                              # 80 ms, two clocks 30 ppm apart (9 ms over 5 min)
    mic = np.interp(t - lag, t, ref, left=0).astype(np.float32) + 0.05 * rng.standard_normal(len(t)).astype(np.float32)
    sf.write(tmp_path / 'r.wav', ref, sr, subtype='FLOAT')
    sf.write(tmp_path / 'm.wav', mic, sr, subtype='FLOAT')
    rs = _stream('r', tmp_path / 'r.wav', sr, 0.0, len(ref), True)
    ms = _stream('m', tmp_path / 'm.wav', sr, 0.0, len(mic), False)
    f, info = LI.fit_residual(tmp_path / 'r.wav', rs, tmp_path / 'm.wav', ms)
    assert info['how'] == 'correlation at the start and the end'
    for s in (15, 150, 285):
        assert abs(f(s) - (0.08 + 0.00003 * s)) < 0.001, (s, f(s), info)


# ------------------------------------------------------------------ words

def test_utterances_join_across_short_pauses_and_keep_said_times():
    w = [{'t': 0.0, 'end': 0.3, 'word': 'the'}, {'t': 0.4, 'end': 0.8, 'word': 'lead'},
         {'t': 2.5, 'end': 2.9, 'word': 'comes'}, {'t': 6.0, 'end': 6.3, 'word': 'back'}]
    u = LI.utterances(w)
    assert [x['text'] for x in u] == ['the lead comes', 'back']
    assert LI.said_times('it starts at 18 and again around 38s') == [18, 38]
    assert LI.said_times('the drop at 1:20') == [80]


# ------------------------------------------------------------------ the whole reading

def test_a_session_reads_on_one_clock(home):
    y = _music([(30, 220, 0.0), (20, 262, 0.35), (10, 330, 0.0)])
    pad = int(0.05 * SR)                                   # the mic hears the speakers 50 ms late
    rng = np.random.default_rng(3)
    mic = (np.r_[np.zeros(pad, np.float32), y[:-pad]] * 0.3 + 0.01 * rng.standard_normal(len(y))).astype(np.float32)
    d = _session(home, mic=mic, music=y, events=[
        {'kind': 'mark', 'wall': 20.5, 'label': 'this!', 'back_s': 0.0},
        {'kind': 'note', 'wall': 25.0, 'text': 'love this bass', 'back_s': 3.5}])

    def stt(path):                                         # word times in the mic file
        return [{'t': 10.0, 'end': 10.3, 'word': 'the', 'p': 1}, {'t': 10.4, 'end': 10.8, 'word': 'lead', 'p': 1},
                {'t': 12.5, 'end': 12.9, 'word': 'comes', 'p': 1},
                {'t': 16.0, 'end': 16.4, 'word': 'starts', 'p': 1}, {'t': 16.5, 'end': 16.6, 'word': 'at', 'p': 1},
                {'t': 16.7, 'end': 17.0, 'word': '18', 'p': 1}]
    status = LI.read_json(d / 'status.json')
    r = LI._read(d, status, stt)
    m = r['mics'][0]
    assert m['how'].startswith('correlation') and abs(m['lag_s'] - 0.05) < 0.005, m
    u = r['utterances']
    assert [x['text'] for x in u] == ['the lead comes', 'starts at 18']
    assert abs(u[0]['t'] - (0.5 + 10.0 - 0.05)) < 0.01      # mic file time -> session time, minus the lag
    assert u[0]['window'][0] == pytest.approx(u[0]['t'] - LI.UTTER_BACK_S, abs=0.01)
    assert u[1]['said_times'] == [18] and u[0]['stretch'] == 1 and 'bar' in u[0]
    assert [s['not_read'] if s.get('not_read') else 'read' for s in r['stretches']] == ['read', 'read', 'shorter than 16 s'], r['cuts']
    assert all(s['loudness']['lufs'] is not None for s in r['stretches'] if not s.get('not_read'))
    assert r['marks'][0]['t'] == 20.5 and r['marks'][0]['label'] == 'this!'
    assert r['notes'][0]['window'] == [21.5, 25.0]

    summary = api.OPS['listen_read'](session=d.name)
    assert 'LUFS' in summary and 'true peak' in summary and 'key' not in summary.lower()
    tl = api.OPS['listen_read'](session=d.name, part='timeline')
    assert 'said: "the lead comes"' in tl and 'typed: "love this bass"' in tl and 'mark: this!' in tl
    assert '**skip**' in tl
    late = api.OPS['listen_read'](session=d.name, part='timeline', start=20, end=30)
    assert 'the lead comes' not in late and 'love this bass' in late
    assert 'not read' in api.OPS['listen_read'](session=d.name, part=f"stretch:{len(r['stretches'])}")


def test_a_missing_speech_server_still_reads_the_music(home, monkeypatch):
    import urllib.error
    y = _music([(20, 220, 0.0)])
    d = _session(home, mic=(y * 0.3).astype(np.float32), music=y)

    def down(path):
        raise urllib.error.URLError('connection refused')
    r = LI._read(d, LI.read_json(d / 'status.json'), down)
    assert 'speech server' in r['mics'][0]['stt_error'] and 'listen_stop' in r['mics'][0]['stt_error']
    assert r['stretches'][0]['loudness']['lufs'] is not None


# ------------------------------------------------------------------ the ops around a live session

def test_marks_and_typed_notes_land_while_recording(home):
    d = _session(home, state='recording')
    st = LI.read_json(d / 'status.json')
    st['wall0'] = time.time() - 30
    LI.write_json(d / 'status.json', st)
    assert 'marked' in api.OPS['listen_mark']('drop')
    out = api.OPS['listen_note']('the bass is too loud here')
    assert 'noted over' in out
    ev = LI.events(d)
    assert [e['kind'] for e in ev] == ['mark', 'note']
    assert ev[1]['back_s'] == pytest.approx(len('the bass is too loud here') / LI.TYPE_CPS, abs=0.1)
    api.OPS['listen_note']('that bit', ago_s=20)
    assert LI.events(d)[-1]['ago_s'] == 20


def test_a_stopped_session_refuses_marks(home):
    d = _session(home, state='stopped')
    with pytest.raises(api.OpError):
        api.OPS['listen_mark']('late', session=d.name)


def test_discard_deletes_unread(home):
    d = _session(home, state='stopped')
    out = api.OPS['listen_stop'](session=d.name, discard=True)
    assert 'Deleted' in out and not d.exists()


def test_list_names_sessions_newest_first(home):
    _session(home, name='2026-01-01_120000')
    _session(home, name='2026-01-02_120000')
    rows = api.OPS['listen_list']().splitlines()
    assert rows[0].startswith('2026-01-02_120000') and rows[1].startswith('2026-01-01_120000')


# ------------------------------------------------------------------ where sessions go

def _engine_note(registry, project):
    """A live engine running now, as the registry shows it (this test's own pid, so it reads as alive)."""
    (project / 'live').mkdir(parents=True, exist_ok=True)
    LI.write_json(project / 'live' / 'engine.json', {'port': 1, 'pid': os.getpid()})
    LI.write_json(registry / f'{os.getpid()}.json', {'pid': os.getpid(), 'project': str(project), 'port': 1,
                                                    'started': time.time()})


def _mk(p):
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_a_session_goes_to_the_project_then_the_live_set_then_the_home(home, tmp_path, monkeypatch):
    song = _mk(tmp_path / 'song')
    assert LI.pick_home(str(song)) == (song.resolve() / 'listen', None, 'the project folder')
    assert LI.pick_home() == (home, None, 'the listening home')
    monkeypatch.setenv('ISMAIL_LISTEN_HOME', str(tmp_path / 'elsewhere'))
    assert LI.pick_home()[0] == tmp_path / 'elsewhere'
    jam = tmp_path / 'jam'
    _engine_note(tmp_path / 'registry', jam)
    where, live, why = LI.pick_home()
    assert where == jam / 'listen' and live == str(jam) and 'live set' in why
    assert LI.pick_home(str(song))[:2] == (song.resolve() / 'listen', str(jam))     # bars from the set running
    with pytest.raises(ValueError):
        LI.pick_home(str(tmp_path / 'no such song'))


def test_listen_start_refuses_a_nearly_full_drive_and_a_missing_project(home, tmp_path, monkeypatch):
    monkeypatch.setattr(LI, 'free_gb', lambda path: 1.2)
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers'], project=str(tmp_path))
    msg = str(e.value)
    assert msg.startswith('not started: only 1.2 GB free') and 'project=' in msg and 'ISMAIL_LISTEN_HOME' in msg
    assert not (tmp_path / 'listen' / 'index.jsonl').exists()
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_start'](streams=['speakers'], project=str(tmp_path / 'nope'))
    assert 'no such folder' in str(e.value)


def test_an_id_resolves_wherever_its_session_went(home, tmp_path):
    old = _session(_mk(tmp_path / 'legacy'), name='2026-01-01_090000')
    out = api.OPS['listen_stop'](session=old.name, read=False)
    assert f'It is in {old}' in out
    d = _session(_mk(tmp_path / 'song' / 'listen'), name='2026-01-03_120000')
    with pytest.raises(api.OpError) as e:
        api.OPS['listen_read'](session=d.name)
    assert 'full path' in str(e.value)
    LI.remember(d.name, d)
    assert 'not read' in api.OPS['listen_read'](session=d.name)
    assert 'not read' in api.OPS['listen_read'](session=str(d))
    rows = api.OPS['listen_list']().splitlines()
    assert rows[0].startswith(d.name) and 'in ' + str(d.parent.resolve()) in rows[0]


def test_the_recorders_news_is_said_once(home):
    d = _session(home)
    LI.add_news(d, 'listening session x stopped by itself: only 1.9 GB free')
    first = api.OPS['listen_list']()
    assert 'NEW since your last call:\n  listening session x stopped by itself' in first
    assert 'NEW since' not in api.OPS['listen_list']()


def test_listen_stop_does_not_read_on_a_nearly_full_drive(home, monkeypatch):
    from ismail import api_listen as AL
    d = _session(home)
    monkeypatch.setattr(LI, 'free_gb', lambda path: 0.5)
    monkeypatch.setattr(AL, '_spawn', lambda *a: pytest.fail('read on a full drive'))
    out = api.OPS['listen_stop'](session=d.name)
    assert 'Not read: only 0.5 GB free' in out and f"listen_stop('{d}')" in out


def test_the_background_processes_keep_temp_files_in_the_session(tmp_path):
    from ismail import api_listen as AL
    env = AL._child_env(tmp_path)
    assert env['TEMP'] == env['TMP'] == env['TMPDIR'] == str(tmp_path / 'tmp') and (tmp_path / 'tmp').is_dir()


# ------------------------------------------------------------------ words as they are said

HSR = 16000


def _talk(d, bursts, total, sr=HSR, name='mic'):
    """A mic file: a quiet room with bursts of 'speech' (loud noise) at [(start s, end s)]."""
    rng = np.random.default_rng(5)
    y = 0.001 * rng.standard_normal(int(total * sr))
    for a, b in bursts:
        y[int(a * sr):int(b * sr)] += 0.1 * rng.standard_normal(int(b * sr) - int(a * sr))
    y = y.astype(np.float32)
    sf.write(d / f'{name}.wav', y, sr, subtype='FLOAT')
    return _stream(name, d / f'{name}.wav', sr, 0.5, 0, False), len(y)


def _onsets(path):
    """Stand-in transcriber: a word at each loud onset in the chunk (times in the chunk); it notes each call."""
    x, sr = sf.read(path, dtype='float32')
    F = int(sr * 0.05)
    lv = np.sqrt((x[:len(x) // F * F].reshape(-1, F) ** 2).mean(1)) > 0.03
    _onsets.calls.append((str(path), len(x) / sr))
    return [{'t': i * 0.05, 'end': i * 0.05 + 1.0, 'word': 'w', 'p': 1}
            for i in range(len(lv)) if lv[i] and (i == 0 or not lv[i - 1])]


def _run(h, mic, n, step_s=1.0):
    """The recorder's hearing loop over a file that grows by step_s each time."""
    for k in range(1, int(n / (HSR * step_s)) + 1):
        mic['frames'] = min(n, int(k * step_s * HSR))
        h.step()
    mic['frames'] = n
    h.step(final=True)


def _recording(home):
    d = _session(home, state='recording', mic=np.zeros(SR, np.float32))
    st = LI.read_json(d / 'status.json')
    st.update(wall0=time.time() - 30, elapsed_s=30.0)
    LI.write_json(d / 'status.json', st)
    return d, st


def test_words_come_cut_at_pauses_on_the_session_clock_with_bars(tmp_path):
    d = _mk(tmp_path / 's')
    mic, n = _talk(d, [(2.0, 4.0), (4.5, 5.5), (8.5, 9.5), (12.0, 25.0)], 30)
    _onsets.calls = []
    clock = {'wall': 1000.0, 'beat': 0.0, 'bpm': 120.0, 'bpb': 4}
    h = LI.Hearer(d, mic, wall0=1000.0, stt=_onsets, clock=lambda: (clock, None))
    _run(h, mic, n)
    assert all(p == str(d / 'heard' / 'chunk.wav') for p, _ in _onsets.calls)
    assert all(s <= LI.HEAR_MAX_S + 0.06 for _, s in _onsets.calls), _onsets.calls
    rows = LI.heard_lines(d)
    assert [r['text'] for r in rows[:3]] == ['w w', 'w', 'w'], rows
    for r, t in zip(rows, (2.5, 9.0, 12.5)):                  # burst start + the mic's t_start
        assert abs(r['t'] - t) < 0.06, rows
        assert r['bar'] == pytest.approx(r['t'] * 2 / 4 + 1, abs=0.06)
    assert len(rows) >= 4 and [r['n'] for r in rows] == list(range(1, len(rows) + 1))
    st = LI.read_json(d / 'hear_mic.json')
    assert st['failed'] == 0 and st['bars'] is None and st['heard_to_s'] == pytest.approx(30.5, abs=0.06)


def test_a_failed_chunk_is_tried_again_then_reported(home):
    import urllib.error
    d, st = _recording(home)
    calls = []

    def flaky(path):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError('u', 500, 'Internal Server Error', {}, None)
        return [{'t': 0.1, 'end': 0.4, 'word': 'louder', 'p': 1}]
    mic, n = _talk(d, [(1.0, 2.0)], 6)
    _run(LI.Hearer(d, mic, wall0=st['wall0'], stt=flaky, clock=lambda: (None, 'no live set runs'), retry_s=0), mic, n)
    assert len(calls) == 2 and [r['text'] for r in LI.heard_lines(d)] == ['louder']

    def broken(path):
        raise urllib.error.HTTPError('u', 500, 'Internal Server Error', {}, None)
    mic2, n = _talk(d, [(1.0, 2.0), (4.5, 5.5)], 8, name='mic2')
    _run(LI.Hearer(d, mic2, wall0=st['wall0'], stt=broken, clock=lambda: (None, 'no live set runs'), retry_s=0),
         mic2, n)
    out = api.OPS['listen_heard'](session=d.name)
    assert '#1 ' in out and 'louder' in out
    assert '#2-3 ' in out and 'not heard (tried twice: the speech server answered 500' in out, out
    assert 'No bars: no live set runs.' in out and 'Next: listen_heard(since=3).' in out
    assert 'louder' not in api.OPS['listen_heard'](session=d.name, since=1)
    assert 'Nothing new since #3.' in api.OPS['listen_heard'](session=d.name, since=3)


def test_a_missing_speech_server_is_said_plainly_with_what_still_works(home):
    import urllib.error
    d, st = _recording(home)

    def down(path):
        raise urllib.error.URLError('connection refused')
    mic, n = _talk(d, [(1.0, 2.0)], 5)
    _run(LI.Hearer(d, mic, wall0=st['wall0'], stt=down, clock=lambda: (None, 'x'), retry_s=0), mic, n)
    out = api.OPS['listen_heard'](session=d.name)
    assert f'the speech server at {LI.SPEAK} does not answer' in out
    assert 'listen_peek' in out and 'listen_stop reads the words' in out


def test_listen_heard_gives_the_last_lines_and_says_when_there_is_no_mic(home):
    d, _ = _recording(home)
    for k in range(25):
        LI.Hearer._add(types.SimpleNamespace(out=d, mic={'name': 'mic'}, wall0=0.0),
                       {'t': float(k), 'end': k + 0.5, 'text': f'line {k + 1}', 'bar': k + 1.0})
    out = api.OPS['listen_heard'](session=d.name)
    assert '#6 0:05.0 bar 6: line 6' in out and '#5 ' not in out and '#25 ' in out
    assert '#3 ' in api.OPS['listen_heard'](session=d.name, limit=30)
    quiet = _session(home, name='2026-01-05_120000', state='recording')
    assert 'records no mic' in api.OPS['listen_heard'](session=quiet.name)


def test_listen_peek_points_at_listen_heard_while_the_mic_is_on(home):
    import threading
    d, _ = _recording(home)

    def recorder():                                         # answers the peek as the recorder does
        for _ in range(200):
            if (d / 'peek.req').exists():
                sf.write(d / 'peek.wav', np.zeros(SR * 5, np.float32), SR)
                (d / 'peek.req').unlink()
                return
            time.sleep(0.02)
    th = threading.Thread(target=recorder)
    th.start()
    out = api.OPS['listen_peek'](session=d.name)
    th.join()
    assert 'listen_heard()' in out and 'silent' in out


def test_the_bars_come_from_the_live_engines_clock(tmp_path):
    from ismail.live.engine import Engine
    fake = types.SimpleNamespace(midi=types.SimpleNamespace(heard_beat=lambda: 33.25), stream=object(),
                                 _null_on=False, beat=lambda pos: 0.0, pos=0, bpm=124.0, bpb=4)
    c = Engine.cmd_clock(fake)
    assert c['beat'] == 33.25 and c['bpm'] == 124.0 and c['bpb'] == 4 and abs(c['wall'] - time.time()) < 1
    _mk(tmp_path / 'live')
    LI.write_json(tmp_path / 'live' / 'engine.json', {'port': 1})
    clock, why = LI.engine_clock(str(tmp_path))
    assert clock is None and 'older' in why
