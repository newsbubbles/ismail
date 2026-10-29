"""render's HOT line reads a track before the master chain (ledger:M337)."""
from ismail import api


def _line(out, name):
    return next(l for l in out.split('\n') if l.strip().startswith(name))


def test_master_gain_ahead_of_a_clipper_is_not_hot(tmp_path):
    p = str(tmp_path / 'p')
    api.project_new(p, bpm=120, length_bars=1, master_limiter=False)
    api.track_add(p, 'pad', instrument={"type": "synth", "oscs": [{"wave": "sine"}]}, volume_db=-12)
    api.notes_write(p, 'pad', 1, "0 A3 4")
    api.fx_add(p, 'master', {'type': 'gain', 'gain_db': 18})
    api.fx_add(p, 'master', {'type': 'distortion', 'mode': 'tanh', 'drive_db': 0})
    line = _line(api.render(p, stems=True), 'pad')
    assert 'before the master chain' in line and 'HOT' not in line
