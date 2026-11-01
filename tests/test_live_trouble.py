"""Live trouble log (Live DJ 10-31): every rejected render is findable afterwards with its bar, track, clip, pad and
the call that fixes it; a sound imported while the engine runs plays after a swap; the op that schedules a burst
of renders says so in its reply, with an estimate measured against the renders it causes; and a burst of underruns
names what was sent just before it. Silent engines only (device='none', inline renders or a stub pool)."""
import os
import time

import numpy as np
import pytest
import soundfile as sf

from ismail import api
from ismail.dsp import SR
from ismail.live import trouble as T
from ismail.live.engine import BLOCK, BURST_NEW_SOUNDS, Engine


def run(eng, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        eng.tick()
        eng.mix_block()


class Sent(list):
    def put(self, payload):
        self.append(payload[0])


def as_pool(e, workers=1):
    """Renders go to a stub pool that keeps them: nothing renders, nothing reads a file."""
    e.n_workers = workers
    e._shared = Sent()
    e._tasks, e._procs = [Sent() for _ in range(workers)], [{'out': 0} for _ in range(workers)]
    return e._shared


def sound(root, name, f, sec=0.3):
    p = os.path.join(os.path.dirname(root), name + '.wav')
    t = np.arange(int(sec * SR)) / SR
    sf.write(p, 0.3 * np.sin(2 * np.pi * f * t) * np.exp(-t * 8), SR)
    api.sound_import(root, name, p)


def kit(names, base=36):
    return {'type': 'kit', 'map': {str(base + i): {'type': 'sampler', 'sound': n} for i, n in enumerate(names)}}


@pytest.fixture
def song(tmp_path):
    root = str(tmp_path / 'set')
    api.project_new(root, bpm=120, length_bars=4)
    sound(root, 'a', 200)
    return root


def test_a_sound_imported_after_start_plays_after_a_swap(song):
    """The DJ's 5 rejections: whisper_ghost_01 and two more were imported while the engine ran, then a kit swap on
    an existing track asked for them and the engine's bank still held its old list."""
    e = Engine(song, bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('k', instrument=kit(['a']))
    e.cmd_queue([{'track': 'k', 'notes': '0 C2 0.5; 1 C#2 0.5; 2 C2 0.5; 3 C#2 0.5', 'bars': 1, 'at': 'next_bar'}])
    run(e, 3.0)
    sound(song, 'whisper_ghost_01', 900)                            # imported while the engine runs
    out = e.cmd_track('k', instrument=kit(['a', 'whisper_ghost_01']))
    bar = int(out.split('instrument changed from bar ')[1].split(',')[0])
    run(e, 4.0)
    assert e.stats['rejected'] == 0 and not e.trouble.rejects, e.trouble.report(4)

    def pad2(b):                                                # level of beat 2 of bar b (the C#2 pad's hit)
        s0 = e.sample((b - 1) * 4 + 1)
        return float(np.sqrt(np.mean(e.air[0, s0:s0 + SR // 10] ** 2)))
    assert pad2(bar - 1) < 1e-3 and pad2(bar) > 1e-2, (pad2(bar - 1), pad2(bar))    # silent before, plays after


def test_a_rejection_says_bar_track_clip_pad_and_the_fix(song):
    e = Engine(song, bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('stab', instrument=kit(['a', 'whisper_reverb_01']))   # never imported
    e.drain_news()
    e.cmd_queue([{'track': 'stab', 'notes': '0 C2 0.5; 2 C#2 0.5', 'bars': 1, 'at': 'bar:3'}])
    run(e, 1.0)
    assert e.stats['rejected'] >= 1
    news = e.drain_news()
    line = next(n for n in news if n.startswith('rejected: '))
    assert 'bar 3 beat 3' in line and 'track stab' in line and 'clip c1' in line, line
    assert 'pad C#2 = whisper_reverb_01' in line and "is not in the bank of" in line, line
    assert "Next: sound_import the sound into" in line and "live_track('stab', instrument=<the same>)" in line, line
    status = e.cmd_status()
    assert f"rejected {e.stats['rejected']}" in status and 'rejected renders, newest' in status, status
    # a later swap clears the track's own errors; the log keeps them, and since= picks by bar
    e.cmd_track('stab', instrument=kit(['a']))
    assert not e.tracks['stab']['errors']
    rep = e.cmd_rejects()
    assert 'whisper_reverb_01' in rep and 'clip c1' in rep, rep
    assert 'rejected renders from bar 4: 0' in e.cmd_rejects(since=4)
    assert 'whisper_reverb_01' in e.cmd_rejects(since=3)
    print('\n' + line + '\n' + rep)


def test_the_log_is_bounded():
    t = T.Trouble()
    for i in range(T.REJECTS_KEPT + 20):
        t.rejects.append({'t': 0, 'beat': i, 'bar': f'bar {i}', 'deck': None, 'track': 'x', 'clips': [],
                          'notes': 1, 'what': '', 'reason': 'r', 'fix': 'f'})
    assert len(t.rejects) == T.REJECTS_KEPT and t.rejects[0]['beat'] == 20


def test_a_burst_of_underruns_names_the_op_and_the_clips_before_it(song):
    e = Engine(song, bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('k', instrument=kit(['a']))
    e.cmd_track('b', instrument='preset:pluck')
    e.cmd_queue([{'track': 'k', 'notes': '0 C2 0.5', 'bars': 1, 'at': 'bar:2'},
                 {'track': 'b', 'notes': '0 C3 1', 'bars': 1, 'at': 'bar:2'}])
    run(e, 2.1)                                                     # bar 2 has started
    e.trouble.sent('track', {'track': 'k', 'instrument': kit(['a'] * 62)}, 'bar 2')
    e.trouble.sent('status', {}, 'bar 2')                          # reads change nothing: never named
    e.played = e.pos
    for _ in range(7):
        e._pull(BLOCK)                                              # nothing mixed ahead: 7 underruns
    assert e.trouble.check(e) == ''                                 # still going: not closed yet
    line = e.trouble.check(e, now=time.time() + 2.0)
    assert line.startswith('7 underruns at bar 2'), line
    assert "after live_track(track='k', instrument=kit of 62 pads)" in line and 'status' not in line, line
    assert 'clips started on 2 tracks in the bar before (b, k)' in line, line
    assert 'last underrun burst: 7 underruns at bar 2' in e.cmd_status()
    assert 'underrun bursts: 1' in e.cmd_rejects()
    print('\n' + line)


def test_a_big_kit_swap_warns_in_its_reply(song):
    names = [f"p{i:02d}" for i in range(24)]
    for i, n in enumerate(names):
        sound(song, n, 300 + 40 * i, sec=0.1)
    e = Engine(song, bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('kit', instrument=kit(['a'] * 24))
    notes = '; '.join(f"{k * 0.25:g} {36 + k % 24} 0.25" for k in range(32))     # every pad, in 16ths
    e.cmd_queue([{'track': 'kit', 'notes': notes, 'bars': 2, 'at': 'next_bar'}])
    run(e, 0.5)
    as_pool(e, workers=3)                                           # from here nothing renders or reads a file
    out = e.cmd_track('kit', instrument=kit(names))
    burst = next(ln for ln in out.splitlines() if ln.startswith('burst: this swap'))
    assert 'on 24 kit pads' in burst and '24 bank sounds new to this set' in burst, burst
    assert 'for 3 workers' in burst and 'Expect underruns or late notes' in burst, burst
    assert "live_track('kit_warm', instrument=<the same>)" in burst and "at='bar:N'" in burst, burst
    assert 24 >= BURST_NEW_SOUNDS
    # the warm-up reads every pad on every worker: once it is back, the same swap is light
    for w in e._tasks:
        for jid in list(w):
            e._result(jid, np.zeros((2, 64), np.float32), 0.01, None)
    out2 = e.cmd_track('kit', instrument=kit(names))
    burst2 = next(ln for ln in out2.splitlines() if ln.startswith('burst: this swap'))
    assert 'new to this set' not in burst2 and 'Light: no burst expected' in burst2, burst2
    print('\n' + burst + '\n' + burst2)


def test_the_burst_estimate_matches_the_renders_it_causes(song):
    """The estimate is the engine's own est x the audio of each event it renders again; measured here against the
    seconds those renders take inline (same machine, same call)."""
    e = Engine(song, bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('p', instrument='preset:pluck')
    notes = '; '.join(f"{k * 0.5:g} {48 + (k * 5) % 24} 0.4" for k in range(16))
    e.cmd_queue([{'track': 'p', 'notes': notes, 'bars': 2, 'at': 'next_bar'}])
    run(e, 0.5)
    spent = []
    orig = e._result

    def timed(jid, y, secs, err):
        job = e.jobs.get(jid)
        if job is not None and not job['warm']:
            spent.append(secs)
        return orig(jid, y, secs, err)
    e._result = timed
    got = []
    burst = e._burst
    e._burst = lambda spans: got.append(burst(spans)) or got[-1]
    out = e.cmd_track('p', instrument='preset:pluck')        # same instrument: every event from the swap again
    assert 'burst: this swap renders' in out, out
    n, work = got[0][0], got[0][1]
    spent.clear()
    e.tick()                                                  # the scheduler sends the swap's events
    assert len(spent) == n, (len(spent), n)                   # the count is exact
    measured = sum(spent)
    assert measured / 3 <= max(work, 0.05) and work <= 3 * measured + 0.05, (work, measured)
    print(f"\nestimate {work:.2f} s, measured {measured:.2f} s over {n} renders")
