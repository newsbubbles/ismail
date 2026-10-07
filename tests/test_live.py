"""Live engine: queue rules (pure) and a headless engine driven block by block (inline renders, no device)."""
import numpy as np
import pytest

from ismail.dsp import SR
from ismail.live.engine import BLOCK, Engine, LiveError
from ismail.live.safety import Safety
from ismail.live.timeline import QueueError, Timeline


# ------------------------------------------------------------------ timeline

def test_quantized_launch_and_bump():
    tl = Timeline(4)
    assert tl.resolve_at('next_bar', 5.0, 5.0) == (8.0, None)
    assert tl.resolve_at('next_4', 5.0, 5.0)[0] == 16.0
    assert tl.resolve_at('next_beat', 5.2, 5.2)[0] == 6.0
    beat, note = tl.resolve_at('next_bar', 7.5, 9.0)      # render needs until beat 9: next bar is too soon
    assert beat == 12.0 and 'moved' in note
    with pytest.raises(QueueError, match='already played'):
        tl.resolve_at('bar:2', 9.0, 9.0)


def test_replace_rule_cuts_and_removes():
    tl = Timeline(4)
    a = tl.add('bass', [(0, 40, 1, 100)], 4, None, 0.0, 'next_bar')
    b = tl.add('bass', [(0, 43, 1, 100)], 4, 2, 16.0, 'bar:5')
    removed, cut = tl.claim('bass', 8.0)
    assert removed == [b.id] and cut == [a.id] and a.end == 8.0


def test_after_needs_finite_clip():
    tl = Timeline(4)
    a = tl.add('x', [(0, 60, 1, 100)], 4, None, 0.0, 'next_bar')
    with pytest.raises(QueueError, match='forever'):
        tl.resolve_at(f'after:{a.id}', 0.0, 0.0)
    b = tl.add('y', [(0, 60, 1, 100)], 4, 3, 4.0, 'next_bar')
    assert tl.resolve_at(f'after:{b.id}', 0.0, 0.0)[0] == 16.0


def test_events_stop_at_cut():
    tl = Timeline(4)
    c = tl.add('x', [(0, 60, 1, 100), (2, 62, 1, 100)], 4, None, 4.0, 'next_bar')
    c.cut = 10.0
    ev = list(tl.events(c, 0.0, 100.0, [[0], [1]]))
    assert [on for _, _, on in ev] == [4.0, 6.0, 8.0]


# ------------------------------------------------------------------ safety

def test_safety_holds_the_ceiling_and_caps_loudness():
    s = Safety(SR)
    x = np.random.default_rng(0).standard_normal((2, BLOCK * 400)) * 3.0     # far too hot
    y = np.concatenate([s.process(x[:, i:i + BLOCK]) for i in range(0, x.shape[1], BLOCK)], axis=1)
    assert np.max(np.abs(y)) <= 10 ** (s.ceiling_db / 20) + 1e-9
    tail = y[:, -SR:]
    assert 10 * np.log10(np.mean(tail ** 2)) < s.cap_db + 3
    y2 = s.process(np.full((2, BLOCK), np.nan))
    assert np.all(np.isfinite(y2)) and s.bad_blocks == 1


def test_one_huge_sample_never_silences_the_set():
    """Live DJ 10-07 12:31: one huge finite sample drove the rider to -423 dB and the set was silent for minutes."""
    s = Safety(SR)
    music = 0.1 * np.sin(2 * np.pi * 220 * np.arange(BLOCK * 200) / SR)
    x = np.stack([music, music])
    x[:, 5 * BLOCK + 7] = 1e20                                      # finite, absurd
    y = np.concatenate([s.process(x[:, i:i + BLOCK]) for i in range(0, x.shape[1], BLOCK)], axis=1)
    assert s.bad_blocks == 1 and s.rider_db >= -1.0                 # the fault went like a NaN; the rider never dove
    assert 20 * np.log10(np.sqrt(np.mean(y[:, -SR:] ** 2))) > -40   # still playing a second later
    s.process(np.full((2, BLOCK), 20.0))                            # and a loud-but-real burst cannot sink it past
    for _ in range(400):                                            # the floor
        s.process(np.full((2, BLOCK), 20.0))
    assert s.rider_db >= -40.0


# ------------------------------------------------------------------ headless engine

def run(eng, seconds):
    for _ in range(int(seconds * SR / BLOCK)):
        eng.tick()
        eng.mix_block()


def onset_times(eng, thresh=0.02, gap=0.3):
    """Sample indices where the air log crosses up through `thresh` (first sample of each hit)."""
    y = np.abs(eng.air[0, :eng.pos])
    above = y > thresh
    idx = np.nonzero(above[1:] & ~above[:-1])[0] + 1
    keep = []
    for i in idx:
        if not keep or i - keep[-1] > SR * gap:
            keep.append(i)
    return keep


@pytest.fixture
def eng(tmp_path):
    return Engine(str(tmp_path), bpm=120, bpb=4, workers=0, device='none')


def test_clip_lands_on_the_grid_and_loops(eng):
    eng.cmd_track('k', instrument={'type': 'kick'})
    out = eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar'}])
    assert 'c1 k: bar 2' in out
    run(eng, 6.0)                                  # bars 1-3 at 120 BPM (2 s per bar)
    hits = onset_times(eng)
    beat = 0.5 * SR
    assert len(hits) >= 7
    assert abs(hits[0] - 4 * beat) < 0.001 * SR    # bar 2 = beat 4, to the millisecond
    assert all(abs((h - hits[0]) / beat - round((h - hits[0]) / beat)) < 0.02 for h in hits)


def test_replace_and_stop(eng):
    eng.cmd_track('k', instrument={'type': 'kick'})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    run(eng, 2.5)
    out = eng.cmd_queue([{'track': 'k', 'stop': True, 'at': 'next_bar'}])
    assert 'stop k at bar 3' in out and 'cuts c1' in out
    run(eng, 4.0)
    y = eng.air[0, :eng.pos]
    bar3 = int(4.0 * SR)
    assert np.max(np.abs(y[bar3 + int(0.5 * SR):])) < 1e-3     # bar 3 on: only the last kick's tail, then silence


def test_batch_is_atomic_and_errors_point_forward(eng):
    eng.cmd_track('k', instrument={'type': 'kick'})
    with pytest.raises(LiveError, match='live_track'):
        eng.cmd_queue([{'track': 'nope', 'notes': '0 C4 1'}])
    with pytest.raises(LiveError, match='Nothing was queued'):
        eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...'}, 'loop': 2},
                       {'track': 'k', 'lanes': {'C1': 'x.x.'}, 'at': 'after:c99'}])
    assert not eng.tl.clips
    with pytest.raises(LiveError, match='beats long'):
        eng.cmd_queue([{'track': 'k', 'notes': '5 C1 1', 'bars': 1}])


def test_arc_chain_and_runway(eng):
    eng.cmd_track('p', instrument='preset:pluck')
    out = eng.cmd_queue([{'track': 'p', 'notes': '0 C4 1; 1 E4 1; 2 G4 1', 'bars': 1, 'loop': 2},
                         {'track': 'p', 'notes': '0 A3 2', 'bars': 1, 'loop': 1, 'at': 'after:c1'},
                         {'track': 'p', 'notes': '0 F3 4', 'bars': 1, 'at': 'after:c2'}])
    assert 'c2 p: bar 4' in out and 'c3 p: bar 5' in out
    assert 'bar 5' in out.splitlines()[-1] and 'p c3 loop forever' in out
    view = eng.cmd_view(bars=6)
    assert 'c1' in view and 'c3' in view


def test_status_and_listen(eng):
    eng.cmd_track('k', instrument={'type': 'kick'}, volume_db=-3)
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    run(eng, 5.0)
    st = eng.cmd_status()
    assert 'playing c1' in st and 'limiter' in st
    d = eng.cmd_listen_dump(bars=2)
    assert d['first'] == 1 and d['last'] == 2


# ------------------------------------------------------------------ effects in the live graph

def level(eng, t0, t1):
    """RMS dBFS of the air log between song seconds t0 and t1."""
    y = eng.air[:, int(t0 * SR):int(t1 * SR)]
    return 10 * np.log10(np.mean(y ** 2) + 1e-20)


def test_delay_echo_lands_on_the_grid(eng):
    eng.cmd_track('k', instrument={'type': 'hat'}, fx=[{'type': 'delay', 'time_beats': 0.5, 'feedback': 0.0,
                                                                'mix': 0.5, 'hp_hz': None}])
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...............'}, 'loop': 1}])
    run(eng, 4.5)
    hits = onset_times(eng, thresh=0.005, gap=0.1)
    beat = 0.5 * SR
    assert abs(hits[0] - 4 * beat) < 0.001 * SR          # dry hit at bar 2
    assert abs(hits[1] - 4.5 * beat) < 0.001 * SR        # its echo half a beat later


def test_lookahead_tracks_stay_aligned(eng):
    eng.cmd_track('a', instrument={'type': 'kick'}, fx=[{'type': 'limiter', 'lookahead_ms': 5}, {'type': 'hall', 'mix': 0.0}])
    eng.cmd_track('b', instrument={'type': 'kick'}, pan=1.0)
    eng.cmd_track('a', pan=-1.0)
    eng.cmd_queue([{'track': 'a', 'lanes': {'C1': 'x...'}, 'loop': 1}, {'track': 'b', 'lanes': {'C1': 'x...'}, 'loop': 1}])
    run(eng, 3.0)
    left = np.nonzero(np.abs(eng.air[0, :eng.pos]) > 0.01)[0][0]
    right = np.nonzero(np.abs(eng.air[1, :eng.pos]) > 0.01)[0][0]
    assert abs(int(left) - int(right)) <= 2 and abs(left - 2 * SR) < 0.001 * SR


def test_chain_swap_lets_the_reverb_ring_out(eng):
    eng.cmd_track('p', instrument='preset:pluck', fx=[{'type': 'reverb', 'size': 0.95, 'mix': 0.6}])
    eng.cmd_queue([{'track': 'p', 'notes': '0 C4 0.25', 'loop': 1}])
    run(eng, 2.6)                                            # note at 2.0 s (bar 2)
    eng.cmd_track('p', fx=[], at='now')                      # dry from now on
    run(eng, 1.0)
    assert level(eng, 2.7, 3.0) > -60                        # the old chain's tail is still sounding


def test_send_bus_and_param_ramp(eng):
    eng.cmd_bus('verb', fx=[{'type': 'hall', 'rt60': 1.5, 'mix': 1.0}])
    eng.cmd_track('p', instrument='preset:pluck', sends={'verb': 0}, fx=[{'type': 'gain', 'gain_db': 0}])
    eng.cmd_queue([{'track': 'p', 'notes': '0 C4 0.25; 2 E4 0.25', 'bars': 1}])
    run(eng, 4.2)
    st = eng.cmd_status()
    assert 'bus verb' in st and 'hall' in st and 'fed by p' in st
    before = level(eng, 3.0, 4.0)
    out = eng.cmd_fx('p', 0, {'gain_db': -30}, ramp_beats=1)
    assert '0 -> -30' in out
    run(eng, 2.5)
    assert level(eng, 5.9, 6.4) < before - 12              # ramped down (the hall still carries a little)


def test_duck_on_source_notes(eng):
    eng.cmd_track('k', instrument={'type': 'kick'}, volume_db=-40)
    eng.cmd_track('pad', instrument='preset:pad', fx=[{'type': 'duck', 'source': 'k', 'depth_db': -24,
                                                       'release_ms': 250}])
    eng.cmd_queue([{'track': 'pad', 'notes': '0 C4 4', 'bars': 1}, {'track': 'k', 'lanes': {'C1': 'x...'}}])
    run(eng, 5.0)
    on = level(eng, 4.0 + 0.01, 4.0 + 0.06)                 # just after a kick (bar 3)
    off = level(eng, 4.0 + 0.35, 4.0 + 0.45)                # recovered
    assert off - on > 10


def test_graph_errors_point_forward(eng):
    eng.cmd_track('k', instrument={'type': 'kick'})
    with pytest.raises(LiveError, match='live_bus first'):
        eng.cmd_track('k', sends={'nope': -6})
    with pytest.raises(LiveError, match='not a live track'):
        eng.cmd_track('k', fx=[{'type': 'compressor', 'sidechain': 'ghost'}])
    with pytest.raises(LiveError, match='itself'):
        eng.cmd_track('k', fx=[{'type': 'duck', 'source': 'k'}])
    with pytest.raises(LiveError, match='budget'):
        eng.cmd_track('k', fx=[{'type': 'limiter', 'lookahead_ms': 200}])
    eng.cmd_track('s', instrument={'type': 'snare'}, fx=[{'type': 'compressor', 'sidechain': 'k'}])
    with pytest.raises(LiveError, match='loop'):
        eng.cmd_track('k', fx=[{'type': 'compressor', 'sidechain': 's'}])
    with pytest.raises(LiveError, match='valid'):
        eng.cmd_fx('s', 0, {'nonsense': 1})
    with pytest.raises(LiveError, match='out of range'):
        eng.cmd_fx('s', 3, {'mix': 1})


def test_after_batch_index(eng):
    eng.cmd_track('p', instrument='preset:pluck')
    out = eng.cmd_queue([{'track': 'p', 'notes': '0 C4 1', 'bars': 1, 'loop': 2},
                         {'track': 'p', 'notes': '0 E4 1', 'bars': 1, 'loop': 1, 'at': 'after:#0'},
                         {'track': 'p', 'notes': '0 G4 1', 'bars': 1, 'at': 'after:#1'}])
    assert 'c2 p: bar 4' in out and 'c3 p: bar 5' in out
    with pytest.raises(LiveError, match='earlier clip'):
        eng.cmd_queue([{'track': 'p', 'notes': '0 C4 1', 'at': 'after:#0'}])


def test_ramp_schedule_keeps_earlier_ramps(eng):
    eng.cmd_track('p', instrument='preset:pad', fx=[{'type': 'gain', 'gain_db': 0}])
    eng.cmd_fx('p', 0, {'gain_db': -20}, ramp_beats=4, at='bar:2')      # beats 4-8
    eng.cmd_fx('p', 0, {'gain_db': 0}, ramp_beats=4, at='bar:4')        # beats 12-16
    sch = eng.ramps[('track:p', 0, 'gain_db')]
    s = eng.sample
    assert sch.value(s(2)) == 0 and abs(sch.value(s(6)) + 10) < 0.1 and sch.value(s(10)) == -20
    assert abs(sch.value(s(14)) + 10) < 0.1 and sch.value(s(20)) == 0
    c = sch.curve(s(7), 4 * 22050)                                      # a block spanning ramp end and hold
    assert c[0] > -20 and abs(c[-1] + 20) < 1e-9


def test_recording_starts_on_a_downbeat(eng, tmp_path):
    import json
    import soundfile as sf
    eng.cmd_track('k', instrument={'type': 'kick'})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}}])
    run(eng, 2.3)                                              # mid bar 2
    out = eng.cmd_record(True)
    assert 'from bar 3' in out
    run(eng, 3.0)
    eng.cmd_record(False)
    y, sr = sf.read(eng.rec_path)
    meta = json.load(open(eng.rec_path[:-4] + '.json'))
    assert meta['first_bar'] == 3
    first = np.nonzero(np.abs(y[:, 0]) > 0.02)[0][0]
    assert first < 0.001 * SR                                  # the bar-3 kick is at t=0


# ------------------------------------------------------------------ decks

def test_isolator_sums_flat_and_kills():
    from ismail.live.decks import Strip, PARAMS
    st = Strip()
    x = np.zeros((2, SR))
    x[:, 100] = 1.0
    y = st.process(x, dict(PARAMS))
    H = np.abs(np.fft.rfft(y[0]))
    fr = np.fft.rfftfreq(SR, 1 / SR)
    band = (fr > 30) & (fr < 16000)
    assert np.max(np.abs(20 * np.log10(H[band]))) < 0.1          # flat to 0.1 dB at 0 dB gains
    st = Strip()
    y = st.process(x, dict(PARAMS, low_db=-40))
    H = np.abs(np.fft.rfft(y[0]))
    assert 20 * np.log10(np.mean(H[(fr > 30) & (fr < 80)])) < -30  # low band killed
    assert abs(20 * np.log10(np.mean(H[(fr > 5000) & (fr < 10000)]))) < 0.5


def make_song(root, name, pitch, bpm=100):
    from ismail.api import OPS
    OPS['project_new'](root, bpm=bpm, length_bars=4, name=name)
    OPS['track_add'](root, 'kick', instrument='preset:kick')
    OPS['notes_write'](root, 'kick', 1, '0 C1 0.5; 1 C1 0.5; 2 C1 0.5; 3 C1 0.5', repeat=4)
    OPS['track_add'](root, 'lead', instrument='preset:pluck')
    OPS['notes_write'](root, 'lead', 1, f'0 {pitch} 1; 2 {pitch} 1', repeat=4)
    return root


def test_load_cue_transition(eng, tmp_path):
    a = make_song(str(tmp_path / 'songA'), 'Song A', 'C4')
    b = make_song(str(tmp_path / 'songB'), 'Song B', 'G4')
    out = eng.cmd_load('A', a, at='next_bar')
    assert 'ON AIR' in out and '100 BPM plays at the house 120' in out
    run(eng, 3.0)
    out = eng.cmd_load('B', b, at='next_bar')
    assert 'CUED' in out
    run(eng, 4.5)
    st = eng.cmd_status()
    assert 'deck A: on air' in st and 'deck B: CUE' in st and 'on decks' in st
    d = eng.cmd_listen_dump(bars=1, deck='B')                    # the cued deck is audible to the agent only
    import soundfile as sf
    yb, _ = sf.read(d['path'])
    assert np.max(np.abs(yb)) > 0.01
    with pytest.raises(LiveError, match='on air and playing'):
        eng.cmd_load('A', b)
    out = eng.cmd_transition('B', at='next_bar', bars=2, style='blend')
    assert 'from deck A to deck B' in out and 'goes on air' in out and 'stop' in out
    run(eng, 7.0)
    st = eng.cmd_status()
    assert 'deck B: on air' in st and 'deck A: CUE (off air) | fader off' in st     # M57: off air when it ends
    assert not any(eng.tl.playing(k, eng.beat(eng.pos)) for k, t in eng.tracks.items() if t['deck'] == 'A')
    assert 'loaded Song A' in eng.cmd_load('A', a, at='next_bar')    # the old deck takes the next song (was refused)
    out = eng.cmd_deck('A', cue=False)
    assert out.splitlines()[0].endswith('on air') and out.splitlines()[1].strip().startswith('on air')


def test_deck_keeps_the_songs_mix(eng, tmp_path):
    """A loaded song sounds like its render: a drum group bus with a reverb insert keeps the dry drums, and
    automation comes over (fx params and volume as ramps, instrument params rendered with the notes, the master
    fade on everything that feeds the master)."""
    from ismail.api import OPS
    root = make_song(str(tmp_path / 'song'), 'Song', 'C4')
    OPS['bus_add'](root, 'drums', fx=[{'type': 'reverb', 'size': 0.3, 'mix': 0.1}])
    OPS['bus_add'](root, 'verb', fx=[{'type': 'reverb', 'mix': 1.0}])
    OPS['track_set'](root, 'kick', output='drums')
    OPS['track_set'](root, 'lead', sends={'verb': -12})
    OPS['track_add'](root, 'bass', instrument={'type': 'synth', 'oscs': [{'wave': 'saw'}],
                                               'filter': {'type': 'lp24', 'cutoff': 300}})
    OPS['notes_write'](root, 'bass', 1, '0 C2 4', repeat=4)
    OPS['fx_add'](root, 'lead', {'type': 'filter', 'mode': 'lp24', 'cutoff': 500})
    OPS['automation_set'](root, 'lead', 'fx.0.cutoff', [[1, 500], [3, 8000]])
    OPS['automation_set'](root, 'lead', 'volume_db', [[1, -6], [2, 0]])
    OPS['automation_set'](root, 'bass', 'inst.filter.cutoff', [[1, 200], [4, 4000]])
    OPS['automation_set'](root, 'master', 'volume_db', [[3, 0], [5, -60]])
    out = eng.cmd_load('A', root, at='bar:3', loop=False)
    assert 'automation:' not in out                           # every lane came over
    drums = eng.buses['A.drums']['path'].chain.procs[0]
    verb = eng.buses['A.verb']['path'].chain.procs[0]
    assert drums.env.dry == 1.0 and verb.env.dry == 0.0      # insert on a group bus, wet-only on a send bus
    keys = set(eng.ramps)
    assert ('track:A.lead', 0, 'cutoff') in keys and ('track:A.lead', -1, 'volume_db') in keys
    assert ('bus:A.drums', -1, 'volume_db') in keys and ('track:A.bass', -1, 'volume_db') in keys  # master fade
    bass = eng.tracks['A.bass']
    assert bass['inst'].get('_whole') and bass['inst_auto']['filter.cutoff'][0] == (0.0, 200.0)
    lead_cut = eng.ramps[('track:A.lead', 0, 'cutoff')]
    start = eng.sample(8)                                     # bar 3 of the house = beat 8
    assert abs(lead_cut.value(start) - 500) < 1 and abs(lead_cut.value(start + eng.sample(8) - 1) - 8000) < 50
    run(eng, 16.0)
    assert not [k for k, v in eng.cache.items() if isinstance(v, str) and v == 'error']
    assert np.max(np.abs(eng.air[:, :eng.pos])) > 0.01


def test_deck_transpose_and_errors(eng, tmp_path):
    a = make_song(str(tmp_path / 'songA'), 'Song A', 'A4')
    eng.cmd_load('A', a, at='next_bar')
    out = eng.cmd_deck('A', transpose=12, at='next_bar')
    assert 'transpose +12' in out
    run(eng, 6.0)
    keys = [k for k in eng.cache if k[0] in {c.id for c in eng.tl.clips.values() if c.track == 'A.lead'}]
    assert any(k[2] == 12 for k in keys)                          # events after the change render transposed
    with pytest.raises(LiveError, match='no deck'):
        eng.cmd_transition('Z')
    eng.cmd_deck('C')
    with pytest.raises(LiveError, match='nothing queued'):
        eng.cmd_transition('C', from_deck='A')
    with pytest.raises(LiveError, match='between'):
        eng.cmd_deck('A', filter=2)


# ------------------------------------------------------------------ studio / live split

def test_studio_only_effect_is_baked(eng, monkeypatch):
    """An effect with no live processor runs the studio function on each rendered note, in chain order."""
    from ismail.live import fx_blocks
    monkeypatch.delitem(fx_blocks.PROCS, 'bitcrush')
    out = eng.cmd_track('h', instrument='preset:pluck', fx=[{'type': 'bitcrush', 'bits': 3, 'rate_hz': 4000},
                                                          {'type': 'gain', 'gain_db': -3}])
    assert out.startswith('h: new track')
    assert 'bitcrush (baked) > gain' in eng.cmd_status()
    eng.cmd_queue([{'track': 'h', 'notes': '0 C4 1', 'loop': 1}])
    run(eng, 3.0)
    assert level(eng, 2.0, 2.5) > -40
    eng.cmd_bus('b')
    with pytest.raises(LiveError, match='bus cannot bake'):
        eng.cmd_bus('b', fx=[{'type': 'bitcrush'}])
    eng.cmd_track('k', instrument={'type': 'kick'})
    with pytest.raises(LiveError, match='move it after'):
        eng.cmd_track('h', fx=[{'type': 'duck', 'source': 'k'}, {'type': 'bitcrush'}])


PERFORMER = '''
import numpy as np

def perform(notes, total_n, sr, bpm=120.0, lanes=None, **params):
    """one sine that slides between overlapping notes; lanes['bend'] adds semitones"""
    y = np.zeros(total_n)
    if not notes:
        return y
    f = np.zeros(total_n)
    for st, m, d, v in sorted(notes):
        f[int(st * sr):] = 440.0 * 2 ** ((m - 69) / 12)
    end = int(max(st + d for st, _, d, _ in notes) * sr)
    bend = (lanes or {}).get('bend')
    if bend is not None:
        f = f * 2 ** (bend / 12)
    ph = np.cumsum(2 * np.pi * f / sr)
    y[:end] = 0.3 * np.sin(ph[:end])
    return y
'''


def test_performer_plays_phrases_with_expression(eng, tmp_path):
    from ismail.live.ops import _mark_performer
    (tmp_path / 'voices').mkdir(exist_ok=True)
    (tmp_path / 'voices' / 'slide.py').write_text(PERFORMER)
    inst = _mark_performer({'type': 'code', 'voice': 'slide', 'tail': 0.1}, str(tmp_path))
    assert inst.get('performer')
    assert not _mark_performer({'type': 'code', 'voice': 'grand_piano'}, str(tmp_path)).get('performer')
    eng.cmd_track('g', instrument=inst)
    with pytest.raises(LiveError, match='performer'):
        eng.cmd_track('p', instrument='preset:pluck')
        eng.cmd_queue([{'track': 'p', 'notes': '0 C4 1', 'expr': {'bend': [[0, 0]]}}])
    out = eng.cmd_queue([{'track': 'g', 'notes': '0 A4 1.5; 1 A4 1', 'loop': 1,
                          'expr': {'bend': [[0, 0], [1.0, 0], [1.01, 12]]}}])
    cid = out.split()[0]
    assert [(g.on, g.end) for g in eng.meta[cid]['groups']] == [(0, 4)]   # a performer renders in bar chunks
    run(eng, 4.0)
    a = eng.air[0, int(2.1 * SR):int(2.4 * SR)]            # before the bend: 440 Hz
    b = eng.air[0, int(2.6 * SR):int(2.9 * SR)]            # after: an octave up

    def hz(x):
        return np.sum(np.diff(np.signbit(x).astype(int)) != 0) / 2 / (len(x) / SR)
    assert abs(hz(a) - 440) < 15 and abs(hz(b) - 880) < 25


def test_a_deck_runs_the_songs_master_chain(tmp_path):
    # M56: live_load dropped the master chain, so a deck played about 4 dB quieter and unlimited
    from ismail.api import OPS

    def level(with_master):
        root = make_song(str(tmp_path / f'song{with_master}'), 'Song', 'C4')
        if with_master:
            OPS['fx_add'](root, 'master', {'type': 'gain', 'gain_db': -12})
        e = Engine(str(tmp_path / f'eng{with_master}'), bpm=120, bpb=4, workers=0, device='none')
        e.safety = type('Unity', (), {'la': 0, 'process': staticmethod(lambda x: x), 'report': lambda self: 'off'})()
        out = e.cmd_load('A', root, at='next_bar', loop=False)
        run(e, 4.0)
        st = e.cmd_status()
        e.shutdown()
        return out, st, 10 * np.log10(np.mean(e.air[:, :e.pos] ** 2) + 1e-20)

    _, st0, db0 = level(False)
    out, st1, db1 = level(True)
    assert 'master limiter' in st0                           # a new project's own limiter comes over
    assert 'master limiter > gain' in st1                    # then the song's gain after it
    assert abs((db1 - db0) + 12) < 0.5


def test_silence_on_air_is_said(eng):
    # M57: a crashed helper left a set silent for 9 minutes and nothing in live_status said so
    eng.cmd_track('k', instrument={'type': 'kick'})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar', 'loop': 1}])
    run(eng, 4.0)
    assert 'SILENT ON AIR' not in eng.cmd_status()
    run(eng, 12.0)
    st = eng.cmd_status()
    assert 'SILENT ON AIR for' in st and 'nothing has sounded since bar' in st


def test_runway_ended_and_a_thin_mix_are_said(eng, monkeypatch):
    # M59: a set ran 10 min on one hat loop after its runway ran out; SILENT ON AIR never fired on it
    import ismail.live.engine as E
    monkeypatch.setattr(E, 'RUNWAY_ENDED_BARS', 2)
    monkeypatch.setattr(E, 'THIN_HISTORY_S', 4)
    monkeypatch.setattr(E, 'THIN_S', 3)
    for k, p in (('k', 'C1'), ('s', 'D1'), ('h', 'F#1')):
        eng.cmd_track(k, instrument={'type': {'k': 'kick', 's': 'snare', 'h': 'hat'}[k]})
    eng.cmd_queue([{'track': 'k', 'lanes': {'C1': 'x...x...x...x...'}, 'at': 'next_bar', 'loop': 3},
                   {'track': 's', 'lanes': {'D1': '....x.......x...'}, 'at': 'next_bar', 'loop': 3},
                   {'track': 'h', 'lanes': {'F#1': 'x.x.x.x.x.x.x.x.'}, 'at': 'next_bar'}])        # the hat loops on
    run(eng, 6.0)
    st = eng.cmd_status()
    assert 'RUNWAY ENDED' not in st and 'THIN' not in st
    run(eng, 10.0)                                   # kick and snare are done; the hat plays on alone
    st = eng.cmd_status()
    assert 'RUNWAY ENDED' in st and 'h loop on unchanged' in st
    assert 'THIN for' in st and 'only h sounding' in st and 'SILENT ON AIR' not in st
