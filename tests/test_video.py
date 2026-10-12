"""Film renders on the shared machine: `python -m ismail.video render` takes the board's GPU slot, so the heat, disk
and on-air rules hold for it as for any Blender render (hq:D-131). No Blender runs here: the shot is a stand-in."""
import pytest

from ismail import machine
from ismail.video import __main__ as video

COOL = {'temp': 60.0, 'clock': 139.0, 'max_clock': 1911.0, 'util': 0.0, 'reasons': 0x1, 'mem_used_gb': 0.5,
        'mem_total_gb': 8.0}


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setenv('ISMAIL_MACHINE_DIR', str(tmp_path / 'board'))
    monkeypatch.setattr(machine, 'gpu', lambda: dict(COOL))
    monkeypatch.setattr(machine, 'memory', lambda: (40.0, 70.0, 30.0))
    monkeypatch.setattr(machine, 'cpu_load', lambda: (12.0, []))
    monkeypatch.setattr(machine, 'disks', lambda *a: [('D:', 100.0)])
    monkeypatch.setattr(machine, 'pagefiles', lambda: [])
    depth = getattr(machine._held, 'depth', 0)
    machine._held.depth = 0                  # the suite's own slot (conftest) would make every slot pass through
    yield tmp_path / 'board'
    machine._held.depth = depth


@pytest.fixture
def song(tmp_path, monkeypatch):
    """A song folder and a stand-in for Blender that notes what the board held while it ran."""
    d = tmp_path / 'song'
    (d / 'video' / 'shots').mkdir(parents=True)
    seen = []

    def blender(song, shot, extra, log=None):
        seen.append({'shot': shot, 'jobs': machine.jobs(), 'env': machine.child_env()})
        with open(log, 'w') as f:
            f.write(f'RENDERED {shot}.mp4\n')
        return 0

    monkeypatch.setattr(video, '_blender', blender)
    return d, seen


def test_a_film_render_holds_the_machines_gpu_slot_one_shot_at_a_time(board, song):
    d, seen = song
    code = video.cmd_render(str(d), ['shot_a.py', 'shot_b.py'], [])
    assert [s['shot'] for s in seen] == ['shot_a.py', 'shot_b.py']
    for s in seen:
        gpu = [j for j in s['jobs'] if j['kind'] == 'gpu']
        assert len(gpu) == 1 and gpu[0]['what'].startswith('video render') and s['shot'][:-3] in gpu[0]['what']
        assert s['env'].get(machine.SLOT_ENV) == gpu[0]['id']     # what the shot starts runs in the render's slot
    assert machine.jobs() == [] and code == 0                       # the slot is given back


def test_a_render_while_a_set_is_on_air_is_refused_with_why_and_the_shots_left(board, song, capsys):
    d, seen = song
    with machine.slot('live', 'live engine set', threads=None):
        machine._held.depth = 0
        assert video.cmd_render(str(d), ['shot_a.py', 'shot_b.py'], [], wait='0') == 75
        machine._held.depth = 1
    err = capsys.readouterr().err
    assert seen == []                                               # Blender never started
    assert 'a live set is on air' in err and 'not rendered: shot_a.py shot_b.py' in err and '--wait' in err
