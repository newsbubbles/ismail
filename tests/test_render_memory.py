"""The render's declared memory covers what it uses (ledger:M336)."""
import tracemalloc

from ismail import api
from ismail.render import Renderer


def _peak_and_estimate(p):
    P = api._load(p)
    R = Renderer(P.d, P.root, None, None, None, False)
    tracemalloc.start()
    R.run()
    peak = tracemalloc.get_traced_memory()[1] / 2 ** 30
    tracemalloc.stop()
    return peak, api._render_gb(P.d, R.n)


def test_declared_memory_covers_the_render(tmp_path):
    p = str(tmp_path / 'p')
    api.project_new(p, bpm=120, length_bars=16)
    for i in range(3):
        api.track_add(p, f't{i}', instrument={"type": "synth", "oscs": [{"wave": "saw"}]}, volume_db=-12)
        api.notes_write(p, f't{i}', 1, "0 A3 4", repeat=16)
        api.fx_add(p, f't{i}', {'type': 'reverb'})
    peak, est = _peak_and_estimate(p)
    assert peak <= est, (peak, est)
    api.fx_set(p, 'master', 0, {'true_peak': True})
    peak, est = _peak_and_estimate(p)
    assert peak <= est, (peak, est)
