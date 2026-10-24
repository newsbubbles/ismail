"""ledger:M313 and M315 (celestial_resort_1024 HANDOFF items 3 and 5, 10-24).

M313: instrument_fit refused BUSY and said to pass wait='10m', then rejected the keyword. A heavy op takes `wait`
the way render does, and one without it is told the shell work-around instead.
M315: a bass fit kept an octave-down square 35 to 39 dB over the target at 25 to 31 Hz: the mean over 29 bands spread
two far-off sub bands thin (and the band the table prints as 25 Hz, at 24.8 Hz, fell out of the distance)."""
import contextlib

import pytest

from ismail import api, machine
from ismail import sounddesign as SD

NOTE = [(0.0, 33, 1.0, 0.8)]          # A1, 55 Hz: the target has nothing under 40 Hz


def _bass(cutoff, sub=0.0):
    oscs = [{"wave": "saw", "level": 1.0, "unison": 1, "phase": 0}]
    if sub:
        oscs.append({"wave": "square", "level": sub, "octave": -1, "unison": 1, "phase": 0})
    return {"type": "synth", "oscs": oscs, "filter": {"type": "lp24", "cutoff": cutoff}}


def _desc(inst):
    return SD.descriptor(SD.render_oneshot(inst, NOTE, 1.3))


def test_a_strong_sub_octave_the_target_lacks_now_scores_worse_than_a_clean_bass():
    tgt = _desc(_bass(900))
    clean = _desc(_bass(500))                 # the right parts, a darker filter
    sub = _desc(_bass(900, sub=0.15))         # the target's tone plus an octave-down square
    d_clean, _ = SD.distance(clean, tgt)
    d_sub, parts = SD.distance(sub, tgt)
    assert d_sub > d_clean
    assert parts['far_db'] > 10
    far = dict(SD.far_bands(sub, tgt))
    assert any(c < 40 and x > 20 for c, x in far.items())
    w = SD.far_warning(sub, tgt)
    assert 'WARNING' in w and '25 Hz' in w and 'more than 20 dB' in w
    assert SD.far_warning(clean, tgt) == ''


def test_the_band_the_table_calls_25_hz_counts_for_fmin_25():
    m = SD.band_mask(25.0, 16000.0)
    assert m[1] and not m[0]                                     # 24.8 Hz in, 19.7 Hz out
    assert not SD.band_mask(70.0)[5] and SD.band_mask(70.0)[6]   # fmin=70 still leaves 63 Hz out, keeps 79 Hz


@pytest.fixture
def proj(tmp_path):
    p = str(tmp_path / 'fit')
    api.project_new(p, bpm=120, length_bars=1)
    api.sound_make(p, 'ref_bass', _bass(900), notes='0 A1 2', describe=False)
    return p


def test_the_fit_reply_warns_when_a_band_is_more_than_20_db_off(proj):
    out = api.OPS['instrument_fit'](proj, target='sound:ref_bass', instrument=_bass(900, sub=0.5),
                                    params={"oscs.1.level": [0.4, 0.5]}, notes='0 A1 2', iters=8)
    assert 'WARNING' in out and 'more than 20 dB' in out and 'fitted - target' in out


class _Busy:
    def __init__(self):
        self.waits = []

    @contextlib.contextmanager
    def slot(self, kind, what, wait=None, **kw):
        self.waits.append(wait)
        raise machine.MachineBusy(f"not starting {kind} job '{what}': cpu slots are full")
        yield                                                   # noqa: unreachable, a generator for contextmanager


def test_instrument_fit_takes_wait_like_render_and_the_busy_reply_says_so(proj, monkeypatch):
    b = _Busy()
    monkeypatch.setattr(machine, 'slot', b.slot)
    for w, s in (('0', None), ('2m', 120.0), ('10', 600.0)):
        with pytest.raises(api.OpError) as e:
            api.OPS['instrument_fit'](proj, target='sound:ref_bass', instrument=_bass(900),
                                      params={"filter.cutoff": [400, 2000]}, notes='0 A1 2', iters=8, wait=w)
        assert b.waits[-1] == s
        assert 'BUSY' in str(e.value) and "wait='10m'" in str(e.value) and 'unexpected keyword' not in str(e.value)
    with pytest.raises(api.OpError, match='wait'):
        api.OPS['instrument_fit'](proj, target='sound:ref_bass', instrument=_bass(900),
                                  params={"filter.cutoff": [400, 2000]}, wait='soon')


def test_a_heavy_op_without_wait_is_not_told_to_pass_one(proj, monkeypatch):
    monkeypatch.setattr(machine, 'slot', _Busy().slot)
    with pytest.raises(api.OpError) as e:
        api.OPS['mimic_measure'](proj, name='x', notes=[['sound:ref_bass', 'A1']])
    msg = str(e.value)
    assert 'BUSY' in msg and "wait='10m'" not in msg
    assert 'mimic_measure has no wait argument' in msg and 'python -m ismail.machine run --cpu --wait 10m' in msg
