"""deck_cost (ledger:M293): a deck's first bar rendered offline the way the live engine renders it, timed, so the agent
sees the silent gap after a live_load coming (the Live DJ's stream set of 10-22 timed it by hand). The heavy track
here is a song voice that sleeps per note, so the ranking never rests on real render timing; the start-up warm-ups
a running engine has already paid are skipped to keep the test small."""
import contextlib
import os
import subprocess

import pytest

from ismail import api, machine
from ismail.api import OPS, OpError
from ismail.live import cost, engine as E, ops as live_ops

SLOW = '''import time
import numpy as np
INFO = {"summary": "a test voice that takes its time"}
def voice(freq, t, vel, gate, sr):
    time.sleep(0.04)
    return np.sin(2 * np.pi * freq * t) * np.exp(-t * 8) * 0.2
'''


@pytest.fixture(autouse=True)
def _no_startup_warmups(monkeypatch):
    monkeypatch.setattr(cost, 'warm_effects', lambda: None)
    monkeypatch.setattr(cost.worker, 'self_warm', lambda root: [])


def song(root):
    api.project_new(root, bpm=120, length_bars=2)
    os.makedirs(os.path.join(root, 'voices'))
    with open(os.path.join(root, 'voices', 'slowpoke.py'), 'w', encoding='utf8') as f:
        f.write(SLOW)
    api.track_add(root, 'heavy', instrument={'type': 'code', 'voice': 'slowpoke', 'tail': 0.1})
    api.notes_write(root, 'heavy', 1, '0 C4 1; 1 E4 1; 2 G4 1; 3 C5 1', repeat=2)
    api.track_add(root, 'light', instrument='preset:kick')
    api.notes_write(root, 'light', 1, '0 C1 1; 2 C1 1', repeat=2)
    return root


def test_the_report_has_each_track_the_total_and_the_mixer(tmp_path):
    r = cost.run(song(str(tmp_path / 's')), workers=2)
    assert r['bar'] == 1 and r['bpm'] == 120 and r['bar_s'] == pytest.approx(2.0)
    assert [x['track'] for x in r['tracks']] == ['heavy', 'light']          # the heavy one ranks first
    for x in r['tracks']:
        assert set(x) == {'track', 'render_s', 'rtf', 'events', 'slowest_event_s', 'first_note_s', 'fx_ms_per_block'}
        assert x['rtf'] == pytest.approx(x['render_s'] / r['bar_s']) and x['events'] >= 1
    heavy = r['tracks'][0]
    assert heavy['events'] == 4 and heavy['render_s'] >= 4 * 0.04 and heavy['first_note_s'] >= 0.04
    t = r['total']
    assert t['workers'] == 2 and t['render_s'] == pytest.approx(sum(x['render_s'] for x in r['tracks']))
    assert t['rtf'] == pytest.approx(t['render_s'] / r['bar_s']) and t['first_bar_wait_s'] >= heavy['slowest_event_s']
    m = r['mixer']
    assert m['blocks'] >= int(2.0 * 44100 / E.BLOCK) and m['block_ms'] == pytest.approx(1000 * E.BLOCK / 44100)
    assert m['load'] == pytest.approx(m['mean_ms'] / m['block_ms']) and m['peak_ms'] >= m['mean_ms']
    text = cost.report(r)
    lines = text.splitlines()
    assert lines[0].startswith('deck cost of s, bar 1 at 120 BPM') and 'render workers' in lines[1]
    assert 'mixer:' in lines[2] and lines[4].split()[0] == 'heavy' and lines[5].split()[0] == 'light'


def test_a_window_measures_its_own_first_bar_and_a_bad_window_says_the_song_s_bars(tmp_path):
    root = song(str(tmp_path / 's'))
    assert cost.run(root, bars=[2, 2], bpm=100)['bar'] == 2
    with pytest.raises(cost.CostError, match='the song has bars 1-2'):
        cost.run(root, bars=[5, 6])


def test_the_dry_run_never_starts_or_calls_a_live_engine(tmp_path, monkeypatch):
    root = song(str(tmp_path / 's'))
    made = []
    init = E.Engine.__init__

    def spy(self, root_, bpm, bpb=4, workers=2, device='default', **kw):
        made.append((workers, device))
        init(self, root_, bpm, bpb, workers=workers, device=device, **kw)

    def refuse(*a, **k):
        raise AssertionError('a dry run reached for a live engine')
    monkeypatch.setattr(E.Engine, '__init__', spy)
    monkeypatch.setattr(E.Engine, 'start', refuse)
    monkeypatch.setattr(live_ops, '_call', refuse)
    monkeypatch.setattr(subprocess, 'Popen', refuse)
    monkeypatch.setattr(machine, 'slot', lambda *a, **k: contextlib.nullcontext())
    out = OPS['deck_cost'](root, workers=2)
    assert out.startswith('deck cost of s, bar 1') and made == [(0, 'none')]
    assert not os.path.exists(os.path.join(root, 'live'))
    with pytest.raises(OpError, match=r'bars=\[first, last\]'):
        OPS['deck_cost'](root, bars=[1])
