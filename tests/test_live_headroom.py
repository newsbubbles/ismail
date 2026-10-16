"""How near the live mixer comes to a dropout (ledger M128): the mixer % is wall time per block, so it climbs with
other programs' CPU while the sound stays clean. The real deadline is the mixed audio still waiting when the
device pulls; live_status reports it at its thinnest since the last status. Silent: no device, no threads."""
import re

import pytest

from ismail.dsp import SR
from ismail.live.engine import AHEAD_S, BLOCK, Engine


@pytest.fixture
def eng(tmp_path):
    e = Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')
    e.cmd_track('k', instrument={'type': 'kick'})
    e.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    return e


def fill(eng, blocks):
    """What the mixer thread does: mix blocks into the fifo the device plays from."""
    for _ in range(blocks):
        eng.tick()
        y = eng.mix_block()
        eng._fifo.append(y.T.copy())
        eng._fifo_n += y.shape[1]


def headroom_ms(status):
    return float(re.search(r'^headroom: (\d+) ms of (\d+)', status, re.M).group(1))


def test_headroom_is_the_audio_left_when_the_device_pulls(eng):
    fill(eng, 20)
    eng._primed = True
    eng._pull(BLOCK)
    eng._pull(BLOCK)
    st = eng.cmd_status()
    assert headroom_ms(st) == pytest.approx(18 * BLOCK / SR * 1000, abs=1)
    assert f"of {AHEAD_S * 1000:.0f} at the tightest" in st and 'clean' in st
    # the DJ's guards read this prefix: it stays
    assert re.search(r'mixer: (\d+)%', st)
    # each status starts a new window
    assert 'headroom: not measured yet' in eng.cmd_status()


def test_a_thin_cushion_and_a_dropout_say_so(eng):
    fill(eng, 4)
    eng._primed = True
    eng._pull(BLOCK)
    assert 'THIN' in eng.cmd_status()
    eng._pull(BLOCK)
    eng._pull(BLOCK)
    eng._pull(2 * BLOCK)                              # asks for more than is left: the device found it empty
    st = eng.cmd_status()
    assert headroom_ms(st) == 0 and 'DROPOUT' in st


def test_a_slow_block_is_named_against_its_own_length(eng):
    fill(eng, 20)
    eng._primed = True
    eng._pull(BLOCK)
    eng.mix_peak = 4.89                                # one block at 489% of real time, the cushion absorbing it
    st = eng.cmd_status()
    blk = BLOCK / SR * 1000
    assert f"the slowest block took {4.89 * blk:.0f} ms for its {blk:.0f} ms of audio" in st
    assert 'clean' in st


def test_the_filling_fifo_at_start_is_not_counted(eng):
    fill(eng, 2)
    eng._pull(BLOCK)                                   # the mixer has not filled the cushion once yet
    assert eng.head_min is None


def test_a_new_track_resolves_its_instrument_outside_the_mixer_lock(eng, monkeypatch):
    # a first mimic instrument imports its module (39 ms measured) and 'track:' reads project.json: the mixer takes
    # this lock every block, so none of it may happen under it
    import threading
    from ismail.live.engine import LiveError
    real = eng._resolve_instrument
    free = []

    def resolve(spec):
        got = []
        t = threading.Thread(target=lambda: got.append(eng.lock.acquire(timeout=0) and (eng.lock.release() or True)))
        t.start()
        t.join()
        free.append(got[0])
        return real(spec)
    monkeypatch.setattr(eng, '_resolve_instrument', resolve)
    eng.cmd_track('k2', instrument={'type': 'kick'})
    assert free == [True]
    with pytest.raises(LiveError, match='unknown preset'):
        eng.cmd_track('bad', instrument='preset:no_such_preset')
    assert 'bad' not in eng.tracks                     # a bad instrument no longer leaves a half-made track
