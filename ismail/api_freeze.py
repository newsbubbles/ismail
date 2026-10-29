"""track_freeze: some tracks rendered into one audio track (ledger:M292, the Live DJ's stream set of 10-22). The
Crossroads slim deck needed 23 tracks down to 8 and hand-built the rest into a song voice reading a flac; a frozen
track is that bed made by the engine. It is an audio track like any placed clip, so a render plays it as it was
and `live_load` carries it onto a deck (decks.placed_audio). Registered into api.OPS on import."""
import copy
import os
import time

import numpy as np

from . import machine
from .api import op, OpError, _load, wait_seconds

FX_READS = ('sidechain', 'source', 'modulator')


@op(mutates=True)
def track_freeze(project: str, tracks: list, into: str = 'frozen', bars: list = None, originals: str = 'mute',
                 wait: str = '10m') -> str:
    """Render some tracks into one audio track a live deck can play: their mix as the studio renders it (notes,
    placed audio, each track's effects, fader, pan and automation), without the buses they send to or play
    through and without the master chain. Reach for it when a deck has too many tracks to render live, or when a
    part is placed audio (a live deck plays audio tracks, not audio placed on a track with notes). The new track
    `into` holds one clip of sounds/frozen_<into>.wav: bar `a` at 0 s of the file, the song's tempo, its tail at
    the end. A send every frozen track makes at the same level comes over as the frozen track's send, and a bus
    they all play through becomes its output; the reply names what did not. bars=[a, b]: only those bars (a deck
    section); default the whole song. originals: 'mute' (default; the song then sounds as before, with the parts
    played from the frozen file) or 'keep'. Freezing into a frozen track again replaces it: its audio, and the
    tracks the last freeze muted come back on unless frozen again; its own fader, pan and effects stay. wait:
    stand in line this long when the machine is busy (as render)."""
    from .render import Renderer, RenderError
    from . import fx as fxmod, instruments as inst_mod
    P = _load(project)
    names = [tracks] if isinstance(tracks, str) else list(tracks or [])
    if not names:
        raise OpError(f"tracks=[...]: the tracks to freeze; tracks: {list(P.d['tracks'])}")
    have = P.d['tracks']
    unknown = [n for n in names if n not in have]
    if unknown:
        raise OpError(f"no track {', '.join(map(repr, unknown))}; tracks: {list(have)}")
    if originals not in ('mute', 'keep'):
        raise OpError("originals: 'mute' (the frozen track plays them) or 'keep' (both stay on)")
    if not into or not isinstance(into, str):
        raise OpError("into: the name of the frozen track, e.g. into='bed'")
    if into in names:
        raise OpError(f"into={into!r} is one of the tracks being frozen; name the frozen track something else")
    old = have.get(into)
    if old is not None and not old.get('frozen'):
        raise OpError(f"track {into!r} exists and is not a frozen track; pick another into=, or track_remove it first")
    L = P.d['length_bars']
    a, b = (1, L) if not bars else (int(bars[0]), int(bars[-1]))
    if not 1 <= a <= b <= L:
        raise OpError(f"bars={bars}: the song has bars 1-{L}")
    # the render: the named tracks as they sound, never muted or left out by a solo, with no master chain or fade
    d = copy.deepcopy(P.d)
    d['master'] = {'fx': [], 'volume_db': 0.0}
    for n, t in d['tracks'].items():
        t['solo'] = False
        if n in names:
            t['mute'] = False
    wait_s = wait_seconds(wait)
    try:
        R = Renderer(d, P.root, None if not bars else a, None if not bars else b + 1, names, cache=True)
        with machine.slot('cpu', f"track_freeze {os.path.basename(P.root)} {into}", disk_path=P.root, wait=wait_s):
            _, stems = R.run()
    except (RenderError, fxmod.FxError, inst_mod.InstrumentError) as e:
        raise OpError(f"freeze failed while rendering: {e}")
    y = sum(stems[n] for n in names)
    if not bars:
        # a whole-song render starts at song 0 s, where bar 1 sits offset_sec later: the file starts on bar 1
        y = y[:, max(0, R.offset_samples_in_window):]
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    # the file: 32-bit float, so a sum louder than full scale (the master limiter used to catch it) is kept whole
    import soundfile as sf
    snd = f"frozen_{into}"
    rel = f"sounds/{snd}.wav"
    os.makedirs(os.path.join(P.root, 'sounds'), exist_ok=True)
    sf.write(os.path.join(P.root, rel), y.T.astype(np.float32), R.sr, subtype='FLOAT')
    P.d.setdefault('sounds', {})[snd] = {"file": rel, "note": f"track_freeze of {', '.join(names)}, bars {a}-{b}",
                                         "sec": round(y.shape[1] / R.sr, 3)}
    # routing: what every frozen track shares comes over exactly (a sum through one bus or one send level)
    srcs = [have[n] for n in names]
    outs = {t.get('output', 'master') or 'master' for t in srcs}
    output = next(iter(outs)) if len(outs) == 1 else 'master'
    lost = []
    if len(outs) > 1:
        lost.append("they play into different buses (" + ', '.join(f"{n} -> {have[n].get('output', 'master')}"
                                                                   for n in names) + "): the frozen track plays "
                    "into the master")
    sends = {}
    for bus in sorted({k for t in srcs for k in (t.get('sends') or {})}):
        lv = {(t.get('sends') or {}).get(bus) for t in srcs}
        if len(lv) == 1:
            sends[bus] = lv.pop()
        else:
            lost.append(f"sends to {bus} differ (" + ', '.join(f"{n} {(have[n].get('sends') or {}).get(bus, 'none')}"
                                                              for n in names) + "): not sent")
    prev = (old.get('frozen') or {}) if old else {}
    track = {"instrument": None, "notes": [], "fx": (old or {}).get('fx', []),
             "volume_db": (old or {}).get('volume_db', 0.0), "pan": (old or {}).get('pan', 0.0), "mute": False,
             "solo": False, "output": output, "sends": sends, "automation": (old or {}).get('automation', {}),
             "audio": [{"sound": snd, "at_beat": P.bar_to_beat(a), "gain_db": 0.0, "offset_sec": 0.0,
                        "length_beats": None}],
             "frozen": {"tracks": names, "bars": [a, b], "sound": snd, "at": time.strftime('%Y-%m-%d %H:%M'),
                        "muted": []}}
    # the last freeze's mutes: lifted unless this freeze holds that track muted again
    held = set(names) if originals == 'mute' else set()
    back = [n for n in prev.get('muted', []) if n in have and n not in held and have[n].get('mute')]
    for n in back:
        have[n]['mute'] = False
    for n in names:
        if n in held and (not have[n].get('mute') or n in prev.get('muted', [])):
            have[n]['mute'] = True
            track['frozen']['muted'].append(n)      # muted by a freeze: the next freeze may lift it
    have[into] = track                              # a replaced one keeps its place in the track list
    P.save()
    from .render import prune_cache
    prune_cache(P.root)
    sec = y.shape[1] / R.sr
    spb = 60.0 / float(P.d['bpm'])
    lines = [f"froze {', '.join(names)} into {into!r} ({'replaced' if old is not None else 'new'} audio track): bars "
             f"{a}-{b}, {sec:.1f} s ({(b - a + 1) * P.bpb * spb:.1f} s of bars + {sec - (b - a + 1) * P.bpb * spb:.1f}"
             f" s tail) in {rel}, bar {a} at 0 s, peak {20 * np.log10(peak + 1e-12):+.1f} dBFS"
             + (" (over full scale: stored as float, the master limiter catches it as before)" if peak > 1.0 else ''),
             f"  plays into {output}" + (f", sends {', '.join(f'{k} {v:+g} dB' for k, v in sends.items())}"
                                         if sends else '')]
    if originals == 'mute':
        lines.append(f"  originals muted: {', '.join(names)} (track_set(mute=False) brings one back; "
                     f"track_freeze(..., originals='keep') leaves them on)")
    if back:
        lines.append(f"  on again (the last freeze muted them, this one does not hold them): {', '.join(back)}")
    if bars and originals == 'mute':
        lines.append(f"  the originals are muted for the whole song and the frozen track holds bars {a}-{b}: a deck "
                     f"of that section (live_load bars=[{a}, {b}]) plays as before; the rest of the song lacks them")
    lines += [f"  {x}" for x in lost]
    readers = [f"{n} fx {f.get('type')} reads {f.get(k)}" for n, t in have.items() if n not in names and n != into
               for f in t.get('fx') or [] for k in FX_READS if f.get(k) in names]
    if readers and originals == 'mute':
        lines.append("  still reading a frozen track: " + '; '.join(readers) + " (a render still keys from the "
                     "muted original; a deck drops that effect: point it at the frozen track to keep it live)")
    lines.append(f"  live: live_load carries {into!r} onto a deck (one bar at a time from the file); at another "
                 f"house tempo its bars are not stretched")
    return '\n'.join(lines)
