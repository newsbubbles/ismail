"""A live set pre-programmed as one arc (synth disco, 120 BPM, A minor): intro, groove, breakdown with a filter
snap and sweep, drop, ending. 17 clips in one live_queue batch, chained with after:#k.
    python skills/ismail/examples/disco_set.py <project folder> [seconds_to_keep_playing]
"""
import re
import sys
import time

from ismail.api import OPS as O

P = None
BPM = 120

CHORDS = [  # piano (mid), strings (high), bass root pair (low, high)
    ('A3,C4,E4,G4', 'C5,E5,G5', ('A1', 'A2')),
    ('C4,E4,F#4', 'C5,E5,F#5', ('D2', 'D3')),
    ('A3,C4,E4', 'C5,E5,A5', ('F1', 'F2')),
    ('G#3,B3,D4,E4', 'B4,D5,G#5', ('E1', 'E2')),
]


def bass():
    out = []
    for b, (_, _, (lo, hi)) in enumerate(CHORDS):
        for k in range(8):
            pitch = hi if k % 2 else lo
            if b == 3 and k == 6:
                pitch = 'G#1'
            if b == 3 and k == 7:
                pitch = 'B1'
            out.append(f"{b * 4 + k * 0.5:g} {pitch} 0.35 {112 if k % 2 == 0 else 92}")
    return '; '.join(out)


def piano(sparse=False):
    out = []
    for b, (ch, _, _) in enumerate(CHORDS):
        hits = [(0, 2.0, 55)] if sparse else [(0.5, 0.2, 92), (1.5, 0.2, 72), (2.5, 0.2, 92), (3.5, 0.2, 72)]
        if not sparse and b == 3:
            hits = hits[:3] + [(3.25, 0.2, 80), (3.75, 0.2, 90)]
        out += [f"{b * 4 + s:g} {ch} {d:g} {v}" for s, d, v in hits]
    return '; '.join(out)


def strings(vel=80):
    return '; '.join(f"{b * 4} {st} 3.9 {vel}" for b, (_, st, _) in enumerate(CHORDS))


HATS = {'F#1': 'x-..x-..x-..x-..', 'A#1': '..X...X...X...X.'}
MAIN = {'F#1': HATS['F#1'] * 4, 'A#1': HATS['A#1'] * 4,
        'D#1': '....x.......x...' * 4,
        'D1': '.' * 48 + '............x-xX'}


def main(hold_s):
    print(O['live_start'](P, bpm=BPM).splitlines()[0])
    print(O['live_bus'](P, 'hall', fx=[{'type': 'hall', 'rt60': 2.2, 'mix': 1.0}], volume_db=-6))
    print(O['live_track'](P, 'kick', instrument='preset:kick', volume_db=-2))
    print(O['live_track'](P, 'perc', instrument='preset:kit_basic', volume_db=-9,
                          fx=[{'type': 'eq', 'bands': [{'type': 'lowcut', 'freq': 250}]}]))
    print(O['live_track'](P, 'bass', volume_db=-9, instrument={
        'type': 'synth', 'oscs': [{'wave': 'saw'}, {'wave': 'square', 'octave': -1, 'level': 0.45}],
        'filter': {'type': 'lp24', 'cutoff': 900, 'res': 0.2, 'env_amount': 2.0},
        'filter_env': {'a': 0.001, 'd': 0.12, 's': 0.25, 'r': 0.05},
        'amp_env': {'a': 0.002, 'd': 0.15, 's': 0.7, 'r': 0.05}},
        fx=[{'type': 'duck', 'source': 'kick', 'depth_db': -6, 'release_ms': 120}]))
    print(O['live_track'](P, 'piano', instrument='preset:grand_piano', volume_db=-7, sends={'hall': -14},
                          fx=[{'type': 'eq', 'bands': [{'type': 'lowcut', 'freq': 200}]}]))
    print(O['live_track'](P, 'strings', instrument='preset:pad', volume_db=-15, sends={'hall': -6},
                          fx=[{'type': 'filter', 'mode': 'lp24', 'cutoff': 600, 'res': 0.2},
                              {'type': 'duck', 'source': 'kick', 'depth_db': -8, 'release_ms': 200}]))
    time.sleep(4)
    print(O['live_record'](P, True))
    reply = O['live_queue'](P, [
        {'track': 'kick', 'lanes': {'C1': 'x...x...x...x...'}, 'loop': 16, 'at': 'next_4'},           # 0
        {'track': 'perc', 'lanes': HATS, 'loop': 4, 'at': 'next_4'},                                  # 1 intro
        {'track': 'strings', 'notes': strings(70), 'bars': 4, 'loop': 1, 'at': 'next_4'},              # 2
        {'track': 'perc', 'lanes': MAIN, 'loop': 3, 'at': 'after:#1'},                                # 3 groove
        {'track': 'bass', 'notes': bass(), 'bars': 4, 'loop': 3, 'at': 'after:#1'},                   # 4
        {'track': 'piano', 'notes': piano(), 'bars': 4, 'loop': 3, 'at': 'after:#1'},                 # 5
        {'track': 'strings', 'notes': strings(), 'bars': 4, 'loop': 3, 'at': 'after:#2'},             # 6
        {'track': 'strings', 'notes': strings(90), 'bars': 4, 'loop': 1, 'at': 'after:#6'},           # 7 break
        {'track': 'piano', 'notes': piano(sparse=True), 'bars': 4, 'loop': 1, 'at': 'after:#5'},      # 8
        {'track': 'kick', 'lanes': {'C1': 'x...x...x...x...'}, 'loop': 16, 'at': 'after:#7'},         # 9 drop
        {'track': 'perc', 'lanes': MAIN, 'loop': 4, 'at': 'after:#7'},                                # 10
        {'track': 'bass', 'notes': bass(), 'bars': 4, 'loop': 4, 'at': 'after:#7'},                   # 11
        {'track': 'piano', 'notes': piano(), 'bars': 4, 'loop': 4, 'at': 'after:#7'},                 # 12
        {'track': 'strings', 'notes': strings(), 'bars': 4, 'loop': 4, 'at': 'after:#7'},             # 13
        {'track': 'strings', 'notes': '0 C5,E5,G5,B5 8 85', 'bars': 2, 'loop': 1, 'at': 'after:#13'},  # 14 end
        {'track': 'piano', 'notes': '0 A2,E3,A3,C4,E4 6 80', 'bars': 2, 'loop': 1, 'at': 'after:#12'},
        {'track': 'bass', 'notes': '0 A1 2 110', 'bars': 2, 'loop': 1, 'at': 'after:#11'},
    ])
    print(reply)
    s = int(re.search(r'c1 kick: bar (\d+)', reply).group(1))
    # filter arc on the strings: open over the intro, snap shut at the break, sweep open into the drop
    print(O['live_fx'](P, 'strings', 0, {'cutoff': 3500}, ramp_beats=16, at=f'bar:{s}'))
    print(O['live_fx'](P, 'strings', 0, {'cutoff': 450}, ramp_beats=0, at=f'bar:{s + 16}'))
    print(O['live_fx'](P, 'strings', 0, {'cutoff': 9000}, ramp_beats=15, at=f'bar:{s + 16.25}'))
    t_end = time.time() + hold_s
    while time.time() < t_end:
        time.sleep(min(10, max(0.1, t_end - time.time())))
        st = O['live_status'](P)
        print('\n'.join(l for l in st.splitlines() if l.startswith(('live ', 'safety', '  ', 'ramps', 'render'))))
        print('-' * 60)
    print(O['live_record'](P, False))
    print(O['live_stop'](P, fade_sec=2))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit("usage: disco_set.py <project folder> [seconds]")
    P = sys.argv[1]
    main(float(sys.argv[2]) if len(sys.argv) > 2 else 90)
