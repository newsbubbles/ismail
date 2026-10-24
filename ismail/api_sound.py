"""Sound-design ops: compare two sounds, fit an instrument's parameters to a target sound."""
import json
import math
import os

import numpy as np
import soundfile as sf

from .api import op, heavy, OpError, _load, _resolve_instrument, _write_sound
from . import sounddesign as SD
from . import instruments as inst_mod
from .notation import parse_notes, NotationError


def _audio(P, src, window=None, span=None):
    path, g = P.source(src, span=span)
    y, sr = sf.read(path, dtype='float64', always_2d=True)
    y = y.T
    if y.shape[0] == 1:
        y = np.vstack([y, y])
    if span:
        window = [g.bar_time(span[0]), g.bar_time(span[1])]
    if window:
        y = y[:, int(max(window[0], 0) * sr):int(window[1] * sr)]
    if sr != SD.SR:
        from scipy.signal import resample_poly
        y = resample_poly(y, SD.SR, sr, axis=1)
    if y.shape[1] < 256:
        raise OpError("selected audio is shorter than 6 ms; widen the window")
    return y


ZOOM_HINT = ("\nclose numbers but it still sounds different, or a voice or played instrument? zoom on the moment: "
             "spectrogram(seconds=[t0, t1], f_lo=, f_hi=, ruler=True) of both, or exam_eye_crops for a blind look "
             "(references/listening.md, When to zoom)")


def _tail_s(y, floor_db=-50.0):
    """Seconds at the end of y under floor_db against its peak (a release tail or silence past the sound)."""
    a = np.abs(y).max(axis=0)
    hop = max(1, int(SD.SR * 0.01))
    n = len(a) // hop
    if n < 2:
        return 0.0
    blk = a[:n * hop].reshape(n, hop).max(axis=1)
    loud = np.nonzero(blk > blk.max() * 10 ** (floor_db / 20))[0]
    return (n - 1 - int(loud[-1])) * hop / SD.SR if len(loud) else 0.0


def _window_hint(ya, yb, a_cut, b_cut):
    """Score the window, not the file (metal 10-22): a whole file's statistics over time count its release tail."""
    da, db = ya.shape[1] / SD.SR, yb.shape[1] / SD.SR
    ta, tb = (0.0 if a_cut else _tail_s(ya)), (0.0 if b_cut else _tail_s(yb))
    if (a_cut and b_cut) or (abs(da - db) <= 0.5 and max(ta, tb) <= 0.5):
        return ''
    return (f"\nwindow: A is {da:.1f} s" + (f" ({ta:.1f} s of tail under -50 dB)" if ta > 0.5 else '')
            + f", B {db:.1f} s" + (f" ({tb:.1f} s of tail)" if tb > 0.5 else '')
            + ". Every statistic over time here counts the whole of each, release tail included. If the person "
              "hears a window (an exam clip), pass a_window/b_window (or a_span/b_span) for exactly that window: "
              "score the window, not the file")


@op()
def sound_compare(project: str, a: str, b: str, a_window: list = None, b_window: list = None, a_span: list = None,
                  b_span: list = None, fmin: float = 25.0, fmax: float = 16000.0) -> str:
    """A/B two sounds: 1/3-octave bands side by side (with << >> where A is >6 dB off), envelope from onset,
    centroid, stereo width, and one distance number. a/b are sources ('sound:<name>', 'ref:drums', 'track:x',
    ...); window=[t0, t1] seconds or span=[start_bar, end_bar] (fractional) selects a slice. Reach for it when one
    part or one hit does not sound like the reference or the genre (a snare that sounds dated, a lead that does not
    sit like the record's): yours against theirs, on the window where it plays. Score the window, not the file:
    the envelope and every statistic over time count all of each sound, so a render or stem that runs past what
    the person hears (its release tail) reads apart from the clip they heard; the reply says when the lengths or
    tails differ and no window was given."""
    P = _load(project)
    ya, yb = _audio(P, a, a_window, a_span), _audio(P, b, b_window, b_span)
    da, db = SD.descriptor(ya), SD.descriptor(yb)
    return (SD.compare_text(da, db, a.split(':')[-1], b.split(':')[-1], fmin, fmax)[1]
            + _window_hint(ya, yb, bool(a_window or a_span), bool(b_window or b_span)) + ZOOM_HINT)


@op(mutates=True)
@heavy()
def instrument_fit(project: str, target: str, instrument, params: dict, notes: str = '0 C4 1',
                   length_sec: float = None, fx: list = None, iters: int = 80, save_as: str = None,
                   apply_to_track: str = None, target_window: list = None, seed: int = 0, fmin: float = 25.0,
                   fmax: float = 16000.0) -> str:
    """Search instrument parameters so its sound matches a target (spectrum + envelope + width).
    target: a source like 'sound:refsnare' (cut one first with sound_import); instrument: dict, 'preset:x' or
    'track:<name>' (start from that track's instrument); params: {"path": [lo, hi]} e.g. {"tone_hz": [100, 250],
    "filter.cutoff": [300, 8000], "oscs.0.detune": [0, 40]}; notes: what to play (beats at project tempo; use the
    target's pitch); fmin/fmax restrict the spectral match to a range (e.g. ignore kick bleed below 70 Hz). Saves the result as sound save_as and/or sets it on apply_to_track.
    Reach for it when a drum or a sound must have the genre's or the reference's character (a snare, kick or bass
    that sounds dated or generic): fit it to a hit from analyze_kit's components or sound_extract."""
    P = _load(project)
    if isinstance(instrument, str) and instrument.startswith('track:'):
        base = P.track(instrument[6:]).get('instrument')
        if not base:
            raise OpError("that track has no instrument")
    else:
        base = _resolve_instrument(instrument, P)
    try:
        full = inst_mod.normalize(base)
    except inst_mod.InstrumentError as e:
        raise OpError(str(e))
    from . import fx as fxmod
    fx_full = [fxmod.normalize(f) for f in (fx or [])]
    for k, rng in params.items():
        try:
            SD._get({'inst': full, 'fx': fx_full}, k if k.startswith('fx.') else 'inst.' + k)
        except (KeyError, IndexError, ValueError, TypeError):
            raise OpError(f"param path {k!r} not found; instrument paths like 'filter.cutoff' (instrument_show "
                          f"full=True), fx paths like 'fx.0.depth_db' (index into the fx list you passed)")
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[1] > rng[0]):
            raise OpError(f"param {k!r} needs a [lo, hi] range with hi > lo")
    tgt = _audio(P, target, target_window)
    tdesc = SD.descriptor(tgt)
    spb = 60 / P.d['bpm']
    try:
        rel = parse_notes(notes)
    except NotationError as e:
        raise OpError(str(e))
    notes_sec = [(s * spb, p, d * spb, v) for s, p, d, v in rel]
    L = length_sec or max(tgt.shape[1] / SD.SR, max(s + d for s, _, d, _ in notes_sec) + 0.3)
    best, d1, d0, vals = SD.fit(full, params, tdesc, notes_sec, L, fx, P.d['bpm'], iters, seed, fmin=fmin, fmax=fmax,
                                root=P.root)
    y = SD.render_oneshot(best['inst'], notes_sec, L, best['fx'], P.d['bpm'], root=P.root)
    _, cmp_txt = SD.compare_text(SD.descriptor(y), tdesc, 'fitted', target.split(':')[-1], fmin, fmax)
    out = [f"fit: distance {d0:.2f} -> {d1:.2f} after {iters} renders",
           "fitted params: " + json.dumps({k: round(v, 4) for k, v in vals.items()})]
    # keep only the keys the caller gave plus type, so the stored instrument stays readable
    compact = json.loads(json.dumps(base))
    for k in params:
        if k.startswith('fx.'):
            continue
        v = SD._get(best['inst'], k)
        SD._set(compact, k, v) if _has(compact, k) else _set_deep(compact, k, v)
    fitted_fx = best['fx']
    if save_as:
        y = y / (np.max(np.abs(y)) + 1e-12) * 0.891
        _write_sound(P, save_as, y, SD.SR, note=f"fitted to {target}")
        out.append(f"saved sound {save_as!r}")
    if apply_to_track:
        tr = P.track(apply_to_track)
        tr['instrument'] = compact
        tr['model'] = {'on': target, 'by': 'instrument_fit'}
        out.append(f"applied instrument to track {apply_to_track!r} (its fx chain is unchanged; fitted fx below)")
    if save_as or apply_to_track:
        P.save()
    out.append("instrument: " + json.dumps(compact))
    if fitted_fx:
        out.append("fx: " + json.dumps(fitted_fx))
    out.append(cmp_txt)
    return '\n'.join(out)


@op(mutates=True)
def sound_extract(project: str, name: str, source: str, bars: list, step: int, steps_per_bar: int = 16,
                  length_sec: float = 0.4, which: str = 'all', pre_ms: float = 5.0, normalize: bool = True,
                  every: int = 1) -> str:
    """Build a clean one-shot of a repeated event by averaging every occurrence at grid position `step`
    (0-based within the bar, 16ths by default) over bars [a, b] (which='all'|'odd'|'even'; every=8 takes bars
    a, a+8, a+16... i.e. the same position of an 8-bar loop), aligned by
    cross-correlation. Sounds that repeat identically (drum samples, stabs) add up; everything else averages down.
    Reports how consistent the occurrences were. Reach for it when a drum in your song sounds dated or generic and
    the genre's own hit is in a reference: extract it (from analyze_kit's component wav, not the full drum stem,
    which carries the kick) and fit your sound to it. The extracted hit is a target, not a part: it plays
    in the song only when the user asked for samples."""
    P = _load(project)
    path, g = P.source(source, bars)
    y, sr = sf.read(path, dtype='float64', always_2d=True)
    y = y.T
    if y.shape[0] == 1:
        y = np.vstack([y, y])
    n = int(length_sec * sr)
    pre = int(pre_ms / 1000 * sr)
    bars_list = [b for b in range(bars[0], bars[1] + 1, max(1, every))
                 if which == 'all' or (which == 'odd' and b % 2) or (which == 'even' and not b % 2)]
    segs, used, skipped = [], [], []
    for b in bars_list:
        t = g.bar_time(b + step / steps_per_bar)
        a = int(t * sr) - pre - int(0.02 * sr)
        L = n + int(0.04 * sr)
        if a + L > y.shape[1]:
            skipped.append(b)  # runs past the end of the audio
            continue
        seg = y[:, max(a, 0):a + L]
        if a < 0:  # event right at the start of the file: pad the pre-roll with silence
            seg = np.concatenate([np.zeros((y.shape[0], -a)), seg], axis=1)
        segs.append(seg)
        used.append(b)
    if len(segs) < 2:
        raise OpError(f"fewer than 2 occurrences: bars tried {bars_list}, usable {used}"
                      f"{f', past the end of the audio {skipped}' if skipped else ''}; widen bars, lower every, or "
                      f"check step (0-based 16th within the bar)")
    ref = segs[0].mean(0)
    shift = int(0.02 * sr)
    aligned = []
    for s in segs:
        m = s.mean(0)
        best = max(range(0, 2 * shift + 1), key=lambda k: float(np.dot(ref[shift:shift + n], m[k:k + n])))
        aligned.append(s[:, best:best + n])
    A = np.stack(aligned)
    avg = A.mean(0)
    cons = float(np.mean([np.corrcoef(a.mean(0), avg.mean(0))[0, 1] for a in aligned]))
    if normalize:
        avg = avg / (np.max(np.abs(avg)) + 1e-12) * 0.891
    _write_sound(P, name, avg, sr, note=f"avg of {len(aligned)} x {source} step {step} bars {bars}")
    P.save()
    d = SD.descriptor(avg if sr == SD.SR else avg)
    return (f"sound {name!r}: average of {len(aligned)} occurrences (bars {used}"
            f"{f'; skipped {skipped}, past the end' if skipped else ''}); consistency {cons:.2f} "
            f"(1 = identical each time, <0.5 = the event varies or is buried; widen/narrow the bar set)\n"
            f"centroid {d['centroid']:.0f} Hz, width {d['width']:.2f}\n"
            f"a target to fit (instrument_fit target={name!r}), not a part: it plays in the song only when the user "
            f"asked for samples")


def _has_sapi():
    """Windows SAPI is the engine that gives word and phone timings (tests patch this, never sys.platform)."""
    import sys
    return sys.platform == 'win32'


def _tts(text, voice, rate, tmp_base):
    """Speak text into an audio file with the OS's own engine: Windows SAPI, macOS `say`, Linux espeak-ng/espeak.
    A voice the engine doesn't have falls back to its default voice. Returns (path, voice actually used)."""
    import shutil
    import subprocess
    import sys
    if sys.platform == 'win32':
        tmp = tmp_base + '.wav'
        v = voice or 'David'
        safe = text.replace("'", "''")
        log = tmp_base + '.txt'        # the engine's word and phone events: true timings (ismail.speech)
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$v = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Name -like '*{v}*' }} | Select-Object -First 1; "
              "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $global:L = New-Object System.Collections.ArrayList; "
              "$s.add_SpeakProgress({ param($o, $e) [void]$global:L.Add(\"W`t$($e.AudioPosition.TotalMilliseconds)"
              "`t$($e.Text)\") }); "
              "$s.add_PhonemeReached({ param($o, $e) [void]$global:L.Add(\"P`t$($e.AudioPosition.TotalMilliseconds)"
              "`t$($e.Duration.TotalMilliseconds)`t$((@($e.Phoneme.ToCharArray() | ForEach-Object { [int]$_ })) "
              "-join '+')\") }); "
              f"$s.Rate = {int(rate)}; $s.SetOutputToWaveFile('{tmp}'); $s.Speak('{safe}'); $s.Dispose(); "
              f"[IO.File]::WriteAllLines('{log}', [string[]]$global:L, (New-Object Text.UTF8Encoding $false))")
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=60)
        return tmp, v, r
    wpm = int(round(175 * 1.1 ** max(-10, min(10, rate))))  # SAPI-style rate -10..10 -> words per minute
    if sys.platform == 'darwin' and shutil.which('say'):
        tmp = tmp_base + '.aiff'
        names = subprocess.run(['say', '-v', '?'], capture_output=True, text=True).stdout.split('\n')
        match = next((ln.split()[0] for ln in names if voice and ln.lower().startswith(voice.lower())), None)
        cmd = ['say', '-o', tmp, '-r', str(wpm)] + (['-v', match] if match else []) + [text]
        return tmp, match or 'default', subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    eng = shutil.which('espeak-ng') or shutil.which('espeak')
    if eng:
        tmp = tmp_base + '.wav'
        r = subprocess.run([eng, '-w', tmp, '-s', str(wpm)] + (['-v', voice] if voice else []) + [text],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 and voice:  # unknown voice name: use the default one
            r = subprocess.run([eng, '-w', tmp, '-s', str(wpm), text], capture_output=True, text=True, timeout=60)
            voice = None
        return tmp, voice or 'default', r
    raise OpError("no text-to-speech engine found: sound_speak uses Windows SAPI, macOS `say`, or espeak-ng on Linux "
                  "(apt install espeak-ng); or record the words and bring them in with sound_import")


@op(mutates=True)
def sound_speak(project: str, name: str, text: str, voice: str = None, rate: int = 0, normalize: bool = True,
                rhythm: str = None, bpm: float = None) -> str:
    """Text-to-speech into the sound bank with the OS's own engine: Windows SAPI (voices like David, Zira; the
    default is David), macOS `say` (Alex, Samantha ...), Linux espeak-ng (en-us, en-gb ...). A voice the engine
    doesn't have falls back to its default. rate -10..10. Use as a vocoder modulator (vocoder fx,
    modulator='sound:<name>' or a muted track holding it as audio clips), a sampler source, or an audio clip.
    On Windows the engine's own word and phone timings are saved next to the sound (sounds/<name>.timings.json);
    other systems give no timings. Reach for rhythm= when spoken words must land on the beat (a narrator, a chopped
    vocal line): rhythm='night 0; shift 2; engineer 12 14 16' gives each word one 16th position per syllable
    (counted from the line's beat 1, negative before it; '-' skips a syllable; one position keeps the word whole),
    and each syllable is cut where the next one starts and set on its 16th at bpm (default the project's). The
    sound then starts on its first syllable's slot; the reply says where to place it. Windows SAPI only (the
    timings come from its events; built in a song and proven there)."""
    import tempfile
    import os
    import sys
    from . import speech
    spec = None
    if rhythm:
        if not _has_sapi():
            raise OpError("rhythm= needs the speech engine's word and phone timings, which only Windows SAPI gives; "
                          "on this system speak the line without rhythm= and cut it by ear (sound_zoom)")
        try:
            spec = speech.parse_rhythm(rhythm)
        except speech.RhythmError as e:
            raise OpError(f"rhythm: {e}")
    P = _load(project)
    base = os.path.join(tempfile.gettempdir(), f"ismail_tts_{os.getpid()}")
    tmp, used, r = _tts(text, voice, rate, base)
    if r.returncode != 0 or not os.path.exists(tmp):
        raise OpError(f"TTS failed: {(r.stderr or r.stdout).strip()[:300]}")
    voice = used
    words, end = [], 0.0
    if os.path.exists(base + '.txt'):
        with open(base + '.txt', encoding='utf-8-sig') as f:
            words, end = speech.parse_sapi_log(f.read().splitlines())
        os.remove(base + '.txt')
    y, sr = sf.read(tmp, dtype='float64', always_2d=True)
    os.remove(tmp)
    y = y.T
    if y.shape[0] == 1:
        y = np.vstack([y, y])
    if spec:
        if not words:
            raise OpError("the speech engine gave no word timings for this line; try again, or another voice")
        m = y.mean(0)
        loud = np.flatnonzero(np.abs(m) > 0.05 * (np.abs(m).max() + 1e-12))
        bpm = bpm or P.d['bpm']
        try:
            m, first = speech.place(words, m, sr, (loud[-1] + 1) / sr if len(loud) else end, spec, bpm)
        except speech.RhythmError as e:
            raise OpError(f"rhythm: {e}")
        y = np.vstack([m, m])
        nz = np.nonzero(np.abs(m) > 1e-4)[0]
        y = y[:, :nz[-1] + 1] if len(nz) else y
        shift = 0.0
    else:
        a = np.max(np.abs(y), axis=0)
        nz = np.nonzero(a > 1e-3)[0]
        shift = nz[0] / sr if len(nz) else 0.0
        if len(nz):
            y = y[:, nz[0]:nz[-1] + 1]
    if normalize:
        y = y / (np.max(np.abs(y)) + 1e-12) * 0.891
    _write_sound(P, name, y, sr, note=f"tts '{text}' ({voice})" + (f" on rhythm '{rhythm}' at {bpm:g} BPM" if spec else ''))
    P.save()
    out = f"sound {name!r}: {y.shape[1] / sr:.2f}s of speech '{text}' ({voice}, {sr} Hz)"
    if words:
        import json
        with open(os.path.join(P.root, 'sounds', f"{name}.timings.json"), 'w', encoding='utf8') as f:
            json.dump({"engine": "sapi", "text": text, "note": "seconds in the spoken line" +
                       (" before rhythm= placed it" if spec else " from the sound's start") +
                       "; phones are SAPI US English phone ids",
                       "words": [{"word": w, "start": round(ph[0][0] - shift, 4),
                                  "phones": [[round(t - shift, 4), i] for t, i in ph]} for w, ph in words]},
                      f, indent=1)
        out += f"; word and phone timings in sounds/{name}.timings.json ({len(words)} words)"
    else:
        out += "; word timings are not available on this OS (Windows SAPI gives them)"
    if spec:
        bar16 = 4 * P.bpb
        out += (f"\nset on 16ths at {bpm:g} BPM: the sound starts on 16th {first:g} of the line, so place it at "
                f"<the line's bar> {'+' if first >= 0 else '-'} {abs(first) / bar16:g} bars "
                f"({abs(first):g}/{bar16} of a bar)")
    return out


def _has(d, path):
    try:
        SD._get(d, path)
        return True
    except (KeyError, IndexError, ValueError, TypeError):
        return False


def _set_deep(d, path, v):
    ks = path.split('.')
    for k in ks[:-1]:
        if isinstance(d, list):
            d = d[int(k)]
        else:
            d = d.setdefault(k, {})
    d[ks[-1]] = v


@op(mutates=True)
@heavy()
def track_fit(project: str, track: str, params: dict, bars: list, iters: int = 30, apply: bool = False,
              seed: int = 0) -> str:
    """Tune a track IN CONTEXT: render the track's whole stem group (stem_map) over bars [a, b] and score it against
    the same window of the reference stem with the perceptual (CLAP) metric plus band levels. params:
    {path: [lo, hi]} with 'inst.<param path>' (inst.filter.cutoff, inst.oscs.0.detune ...), 'fx.<i>.<param>',
    'volume_db' or 'pan'. 8-16 bars and 20-40 iterations is a good budget (each iteration renders the window).
    apply=True writes the best values into the track."""
    from . import trackfit as TF
    from .api_cmp import ref_stems
    P = _load(project)
    tr = P.track(track)
    sm = P.d.get('stem_map') or {}
    if track not in sm:
        raise OpError(f"{track!r} is not in the stem map; stem_map_set({{'{track}': '<stem>'}}) first")
    stem = sm[track]
    refs = ref_stems(P)
    if stem not in refs:
        raise OpError(f"no reference stem {stem!r}; separate(source='ref') first")
    group = [t for t, s in sm.items() if s == stem and t in P.d['tracks'] and not P.d['tracks'][t].get('mute')]
    for k in params:
        if not (k.startswith('inst.') or k.startswith('fx.') or k in ('volume_db', 'pan')):
            raise OpError(f"param {k!r}: use 'inst.<path>', 'fx.<index>.<param>', 'volume_db' or 'pan'")
    if bars[1] - bars[0] + 1 > 24:
        raise OpError("use at most 24 bars per fit (each iteration renders the window)")
    vals, s1, s0, p1, p0 = TF.fit_track(P.d, P.root, P.grid(), track, params, bars, group, refs[stem], iters, seed)
    out = [f"track_fit {track} in stem group {stem} {group}, bars {bars[0]}-{bars[1]}: score {s0:.2f} -> {s1:.2f}",
           f"  perceptual {p0['perceptual']:.3f} -> {p1['perceptual']:.3f}; band error {p0['band_db']:.1f} -> "
           f"{p1['band_db']:.1f} dB; width diff {p0['width_diff']:+.2f} -> {p1['width_diff']:+.2f}",
           "  best: " + json.dumps({k: round(v, 4) for k, v in vals.items()})]
    if apply and s1 < s0:
        from .trackfit import _path_set
        for k, v in vals.items():
            _path_set(tr, k, v)
        if any(k.startswith('inst.') for k in vals) and not tr.get('model'):
            tr['model'] = {'on': f'ref:{stem}', 'by': f'track_fit bars {bars[0]}-{bars[1]}'}
        P.save()
        out.append("  applied (render + cmp_run to confirm on the whole song)")
    elif apply:
        out.append("  not applied: no improvement over the current settings")
    return '\n'.join(out)


@op(mutates=True)
def eq_match(project: str, track: str, bars: list, max_db: float = 12.0, apply: bool = True,
             level: bool = True) -> str:
    """Match a track's long-term spectrum to the reference stem over bars [a, b]: renders the track's stem group
    (stem_map), compares 1/3-octave levels with the same window of the reference stem, and writes a peaking EQ
    at octave centres (63 Hz .. 16 kHz, gains clipped to +-max_db) as the track's fx 0 (replacing an earlier
    eq_match EQ), plus the level difference on the fader when level=True. Best where that track dominates its
    stem in the window (e.g. a pad in an intro)."""
    from .api_cmp import ref_stems
    from .render import Renderer
    P = _load(project)
    tr = P.track(track)
    sm = P.d.get('stem_map') or {}
    if track not in sm:
        raise OpError(f"{track!r} is not in the stem map; stem_map_set first")
    refs = ref_stems(P)
    stem = sm[track]
    if stem not in refs:
        raise OpError(f"no reference stem {stem!r}; separate(source='ref') first")
    group = [t for t, s in sm.items() if s == stem and t in P.d['tracks'] and not P.d['tracks'][t].get('mute')]
    d = json.loads(json.dumps(P.d))
    fxl = d['tracks'][track].get('fx', [])
    if fxl and fxl[0].get('name') == 'eq_match':
        fxl.pop(0)  # measure without the previous match
    R = Renderer(d, P.root, bars[0], bars[1] + 1, group, cache=True)
    y, _ = R.run()
    g = P.grid()
    n_win = int((g.bar_time(bars[1] + 1) - g.bar_time(bars[0])) * R.sr)
    y = y[:, :n_win]
    yr, srr = sf.read(refs[stem], always_2d=True, dtype='float64')
    a = int(max(g.bar_time(bars[0]), 0) * srr)
    yr = yr[a:a + n_win].T

    def third_oct(x, sr):
        m = x.mean(0)
        nfft = 1 << int(np.ceil(np.log2(len(m))))
        Pw = np.abs(np.fft.rfft(m * np.hanning(len(m)), n=nfft)) ** 2
        f = np.fft.rfftfreq(nfft, 1 / sr)
        out = []
        for c in SD.THIRD_OCT:
            msk = (f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))
            out.append(10 * np.log10(Pw[msk].sum() + 1e-15) if msk.any() else -150)
        return np.array(out)
    mine, ref = third_oct(y, R.sr), third_oct(yr, srr)
    live = (mine > mine.max() - 60) & (ref > ref.max() - 60) & (SD.THIRD_OCT >= 40) & (SD.THIRD_OCT <= 16000)
    # level: energy difference; shape: per-band difference after removing the level offset
    lvl = 10 * np.log10(np.sum(10 ** (ref[live] / 10)) / np.sum(10 ** (mine[live] / 10)))
    diff = np.where(live, (ref - mine) - lvl, 0.0)
    centers = [63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    bands = []
    for c in centers:
        msk = live & (SD.THIRD_OCT >= c / 2 ** 0.5) & (SD.THIRD_OCT < c * 2 ** 0.5)
        if msk.any():
            gdb = float(np.clip(np.mean(diff[msk]), -max_db, max_db))
            if abs(gdb) >= 1.0:
                bands.append({"type": "peak", "freq": c, "gain_db": round(gdb, 1), "q": 1.2})
    lines = [f"eq_match {track} (stem group {group}) bars {bars[0]}-{bars[1]}: level {lvl:+.1f} dB",
             "  band gains: " + ', '.join(f"{b['freq']}Hz {b['gain_db']:+.1f}" for b in bands)]
    if apply:
        tfx = tr.setdefault('fx', [])
        if tfx and tfx[0].get('name') == 'eq_match':
            tfx.pop(0)
        if bands:
            tfx.insert(0, {"type": "eq", "name": "eq_match", "bands": bands})
            # automation indices on this track's fx shift by one
            auto = tr.get('automation', {})
            tr['automation'] = {(f"fx.{int(k.split('.')[1]) + 1}.{k.split('.', 2)[2]}" if k.startswith('fx.') else k): v
                                for k, v in auto.items()}
        if level:
            tr['volume_db'] = round(tr.get('volume_db', 0.0) + float(np.clip(lvl, -24, 24)), 2)
        P.save()
        lines.append(f"  applied: eq as fx 0, fader now {tr.get('volume_db')} dB (render + cmp_run to confirm)")
    return '\n'.join(lines)


def _pitch_of(name):
    import librosa
    base = os.path.basename(name).rsplit('.', 1)[0]
    base = base.replace('s', '#') if len(base) <= 4 else base
    return int(round(librosa.note_to_midi(base)))


@op(mutates=True)
@heavy()
def mimic_measure(project: str, name: str, notes: list = None, folder: str = None, kind: str = 'auto',
                  vel: float = 0.7, check: bool = True, defaults: dict = None, no_top: bool = False) -> str:
    """Measure an instrument from recorded notes into a mimic profile (<project>/voices/<name>.mimic.json), then
    use it as {"type": "mimic", "profile": "<name>"}. notes: [[source, pitch], ...] or [[source, pitch, vel,
    [t0, t1]]] where source is 'sound:<name>', 'ref', 'ref:<stem>' or a path, pitch like 'A4', vel 0..1 (how hard
    it was played; default `vel`), [t0, t1] seconds to cut one note out of a longer file. folder: a directory of
    one-note files named by pitch (A4.wav, Fs3.mp3, C#5.flac) instead of notes. One note per file or window,
    isolated, a few across the range; two dynamics of the same pitch teach velocity. kind: auto | sustained
    (bowed, blown, sung) | decaying (plucked, struck). check=True rebuilds every measured note from the OTHER
    notes and reports how close it lands (leave-one-out), the honest estimate for pitches you did not record.
    defaults: mimic params stored in the profile and used unless a track overrides them, e.g. the open strings of a
    bowed instrument {"strings": ["G3", "D4", "A4", "E5"]} (instrument_help(type='mimic') lists them). Each recording is
    checked first for capture faults that would be learned as the instrument's sound (peaks flattened by a browser
    mic or a limiter, nothing above 8 kHz from a Bluetooth mic); no_top=True says the instrument has no top, so a
    missing top is not a fault."""
    import glob as _glob
    import librosa
    from . import mimic
    P = _load(project)
    items = []
    if folder:
        fd = folder if os.path.isabs(folder) else os.path.join(P.root, folder)
        files = sorted(f for ext in ('wav', 'flac', 'mp3', 'ogg', 'aif', 'aiff')
                       for f in _glob.glob(os.path.join(fd, '*.' + ext)))
        if not files:
            raise OpError(f"no audio files in {fd}; name them by pitch (A4.wav, Fs3.mp3) or pass notes=[[source, pitch]]")
        for f in files:
            try:
                items.append((f, _pitch_of(f), vel, None))
            except Exception:
                raise OpError(f"can't read a pitch from file name {os.path.basename(f)!r}; name files like A4.wav, "
                              f"Fs3.wav, C#5.wav, or pass notes=[[source, pitch]]")
    for it in notes or []:
        if not isinstance(it, (list, tuple)) or len(it) < 2:
            raise OpError("each note is [source, pitch] or [source, pitch, vel, [t0, t1]], e.g. ['sound:vln_a4', 'A4']")
        src, pitch = it[0], it[1]
        items.append((src, int(round(librosa.note_to_midi(pitch))) if isinstance(pitch, str) else int(pitch),
                      float(it[2]) if len(it) > 2 and it[2] is not None else vel, it[3] if len(it) > 3 else None))
    if not items:
        raise OpError("give notes=[[source, pitch], ...] or folder=<directory of pitch-named files>")
    ys = []
    for src, midi, v, win in items:
        if isinstance(src, str) and os.path.isfile(src) and not win:
            ys.append((mimic._load(src), midi, v))
        else:
            ys.append((_audio(P, src, win).mean(axis=0), midi, v))
    from . import capture
    from .voices import _note_name
    faults = {}
    for (y, _, _), (src, midi, _, win) in zip(ys, items):
        w = capture.check(y, mimic.SR, no_top=no_top)['warnings']
        if w:
            faults[f"{os.path.basename(str(src))}{f' {win}' if win else ''} ({_note_name(midi)})"] = w
    try:
        measured = mimic.measure_notes(ys, kind=kind)
    except Exception as e:
        raise OpError(f"measuring failed: {e}")
    for n, (src, _, _, _) in zip(measured, items):
        n['file'] = os.path.basename(src) if isinstance(src, str) else None
    source = folder or ', '.join(sorted({str(i[0]).split(':')[0] if str(i[0]).startswith('sound:') else
                                         os.path.basename(str(i[0])) for i in items}))[:200]
    # leave-one-out: rebuild each note from the others; also picks how sharp the body curve can be for this data
    loo = {}
    if check and len(measured) >= 3:
        for smooth in (1, 3, 5, 9):
            rows = []
            for i, n in enumerate(sorted(measured, key=lambda m: m['midi'])):
                rest = mimic.profile_from_notes([m for m in measured if m is not n], name, body_smooth=smooth)
                y = _audio_note(ys, measured, n)
                gate = min(max(n['dur'] - 0.3, 0.3), 3.0) if n['kind'] == 'sustained' else 3.0
                z = mimic.render(rest, mimic._hz(n['midi']), np.arange(int((gate + 1.0) * SD.SR)) / SD.SR,
                                 n['vel'], gate).mean(axis=0)
                y = y[:len(z)]
                z = z * np.sqrt(np.mean(y ** 2) / (np.mean(z ** 2) + 1e-15))       # compare timbre, not level
                d, _ = SD.distance(SD.descriptor(np.stack([y, y])), SD.descriptor(np.stack([z, z])))
                rows.append((n, d))
            loo[smooth] = rows
    smooth = min(loo, key=lambda k: np.mean([d for _, d in loo[k]])) if loo else 1
    prof = mimic.profile_from_notes(measured, name, source, body_smooth=smooth)
    prof['body_smooth'] = smooth
    if defaults:
        bad = set(defaults) - set(mimic.DEFAULT_PARAMS)
        if bad:
            raise OpError(f"unknown mimic params in defaults {sorted(bad)}; valid: {sorted(mimic.DEFAULT_PARAMS)}")
        prof['defaults'] = defaults
    if faults:
        prof['capture'] = faults
    if no_top:
        prof['no_top'] = True
    vd = os.path.join(P.root, 'voices')
    os.makedirs(vd, exist_ok=True)
    out = os.path.join(vd, name + '.mimic.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(prof, f)
    from .voices import _note_name
    L = [f"mimic profile {name!r} -> {out}", f"kind {prof['kind']}, {len(measured)} notes"]
    if faults:
        L.append(f"RECORDING WARNINGS ({len(faults)} of {len(items)} recordings; kept in the profile as 'capture'):")
        L += [f"  {k}: {w}" for k, ws in faults.items() for w in ws]
    L.append(f"{'note':>5} {'partials':>8} {'B':>8} {'vib Hz':>6} {'cents':>5} {'attack':>6}  noise (sustain, dB under harmonics)")
    for n in prof['notes']:
        ns = np.array(n['noise_sus'])
        L.append(f"{_note_name(n['midi']):>5} {n['partials']:>8} {n['B']:8.1e} {n['vib_rate']:6.1f} {n['vib_depth']:5.1f} "
                 f"{n['attack'][0]:6.3f}  {np.median(ns[ns > -140]):.0f}")
    body = np.array(prof['body_db'])
    hz = np.array(prof['body_hz'])
    pk = [i for i in range(1, len(body) - 1) if body[i] >= body[i - 1] and body[i] >= body[i + 1] and body[i] > 3]
    pk = sorted(pk, key=lambda i: -body[i])[:5]
    if pk:
        L.append("body resonances: " + ', '.join(f"{hz[i]:.0f} Hz +{body[i]:.0f} dB" for i in sorted(pk)))
    if loo:
        L.append("leave-one-out (each note rebuilt from the others, loudness matched; distance as sound_compare, lower "
                 "is closer; about 5-10 is close, 20+ means that region needs its own recording):")
        L.append(f"  body sharpness tried (bins of 1/12 octave smoothed): " +
                 ', '.join(f"{k}: {np.mean([d for _, d in v]):.1f}" for k, v in loo.items()) + f" -> kept {smooth}")
        L.append("  " + ', '.join(f"{_note_name(n['midi'])} {d:.1f}" for n, d in loo[smooth]))
    L.append(f"use: instrument={{'type': 'mimic', 'profile': '{name}', 'params': {{}}, 'tail': 1.0}} "
             f"(instrument_help(type='mimic') for params)")
    return '\n'.join(L)


def _audio_note(ys, measured, n):
    for (y, midi, v), m in zip(ys, measured):
        if m is n or (abs(m['midi'] - n['midi']) < 0.01 and m['vel'] == n['vel']):
            env = np.sqrt(np.convolve(y ** 2, np.ones(220) / 220, 'same'))
            on = int(np.argmax(env > 0.02 * env.max()))
            return y[max(0, on - 220):]
    return ys[0][0]


@op()
def sound_autotune(path: str, key: str = 'C', scale: str = 'chromatic', notes: list = None, speed_ms: float = 20.0,
                   amount: float = 1.0, formant: float = 0.0, out: str = None) -> str:
    """Auto-tune a recorded voice file (a phone voice note, a mic take, a stem): its pitch moved onto key + scale, or
    onto notes = [[start_s, midi_pitch, dur_s], ...] (times from the file's start), its character kept (WORLD; needs
    pyworld). speed_ms is the retune speed: 0 = the hard robotic snap, 20-60 firm, 100+ keeps vibrato and scoops.
    formant = semitones to move the voice's character without its pitch. Writes a new wav next to the take (or
    out=) and never overwrites; the same as the autotune effect on a track (fx_help type='autotune')."""
    from . import tune
    if not os.path.exists(path):
        raise OpError(f"no such file {path!r}")
    try:
        y, sr = sf.read(path, dtype='float64', always_2d=True)
        y = y.mean(1)
    except RuntimeError:                         # a phone note (webm, ogg, m4a): decode with ffmpeg
        import shutil
        from .phone.hum import decode
        ff = shutil.which('ffmpeg')
        if not ff:
            raise OpError(f"{path!r} is not a wav/flac and ffmpeg is not installed to decode it")
        sr = SD.SR
        y = decode(path, ff, sr).astype(np.float64)
    if sr != SD.SR:
        from scipy.signal import resample_poly
        y, sr = resample_poly(y, SD.SR, sr), SD.SR
    nl = None
    if notes:
        try:
            nl = [(float(s), int(p), float(d), 100) for s, p, d in notes]
        except (TypeError, ValueError):
            raise OpError("notes = [[start_s, midi_pitch, dur_s], ...], e.g. [[0, 62, 0.5], [0.5, 64, 1]]")
    try:
        tuned, r = tune.retune(y, sr, key, scale, nl, speed_ms, amount, formant)
    except tune.TuneError as e:
        raise OpError(str(e))
    if out is None:
        base = os.path.splitext(path)[0] + '_tuned'
        out, k = base + '.wav', 2
        while os.path.exists(out):
            out, k = f"{base}_{k}.wav", k + 1
    elif os.path.exists(out):
        raise OpError(f"{out!r} exists; give a new name (takes are never overwritten)")
    peak = float(np.max(np.abs(tuned))) or 1.0
    sf.write(out, (tuned / max(peak, 1.0)).astype(np.float32), sr)
    return (f"tuned -> {out}\n{r['voiced_s']:.1f} s voiced; moved {r['median_cents_moved']:.0f} cents typical, "
            f"{r['max_cents_moved']:.0f} at most ({'notes given' if nl else f'{key} {scale}'}, speed {speed_ms:g} ms)\n"
            "next: listen against the music it was sung over; a phone note keeps that music beside it "
            "(<id>_ref.wav, beats in <id>_ref.json) so the take can be placed on the beat it was sung to")


@op(mutates=True)
def master_cue(source: str, start_s: float = 0.0, end_s: float = None, lufs: float = -16.0, tp: float = -1.0,
               sr: int = 48000, out: str = None, fade_in_s: float = 0.0, fade_out_s: float = 0.4,
               mp3_margin_db: float = 1.2, tp_catch: bool = True) -> str:
    """Master a finished cue (a teaser, a stinger, a song for posting) for delivery: cut source to start_s..end_s
    (seconds; end_s=None keeps the rest), fade the ends (cos^2; 0.4 s out by default), resample to sr, then ONE fixed
    gain to `lufs` integrated (BS.1770). Only if that gain puts the true peak (4x oversampled) over the ceiling does a
    lookahead peak limiter take the excess, and its gain reduction is reported; then a true-peak catch (4x
    oversampled, +-1.5 ms lookahead, 25 ms release, no re-gain after it) takes whatever the limiter left, so the wav
    ends at or under the ceiling. The ceiling is tp - mp3_margin_db: a 320k mp3 of a limited master peaks up to about
    1 dB over its wav, so the default 1.2 keeps the mp3 under `tp` too; mp3_margin_db=0 when the wav itself is the
    delivery. With the catch the limiter gets up to 6 rounds, each aiming lower by what the last re-gain put over;
    tp_catch=False with mp3_margin_db=0 is the master from before (3 plain rounds, no catch). Never a dynamic
    loudness ride (ffmpeg loudnorm falls back to one on some files). Writes 24-bit wav to out= (default
    <source>_master.wav beside it); a file already there is moved to old/ beside it first, never overwritten. -16
    LUFS / -1 dBTP suits most platforms; -14 / -1 for a louder stream. Read the report before anyone listens: it is
    the measurement line for the round."""
    import shutil
    import time
    from . import master as M
    if not os.path.exists(source):
        raise OpError(f"no such file {source!r}: give the full path of the rendered cue (a wav or flac)")
    try:
        y, in_sr = sf.read(source, dtype='float64', always_2d=True)
    except RuntimeError as e:
        raise OpError(f"cannot read {source!r} ({e}): render or convert it to wav first")
    if not -40 <= lufs <= -5:
        raise OpError(f"lufs={lufs} is outside -40..-5: delivery targets are about -16 (posts) or -14 (streams)")
    if tp > 0:
        raise OpError(f"tp={tp} dBTP would allow clipping: keep the ceiling at or under 0, -1 is usual")
    if not 0 <= mp3_margin_db <= 3:
        raise OpError(f"mp3_margin_db={mp3_margin_db} is outside 0..3: a 320k mp3 peaks up to about 1 dB over its wav, "
                      f"so {M.MP3_MARGIN_DB:g} keeps it under tp; 0 aims the wav itself at tp")
    try:
        y, out_sr, r = M.master(y, in_sr, start_s, end_s, lufs, tp, int(sr), fade_in_s, fade_out_s,
                                mp3_margin_db=float(mp3_margin_db), tp_catch=bool(tp_catch))
    except ValueError as e:
        raise OpError(str(e))
    if out is None:
        out = os.path.splitext(source)[0] + '_master.wav'
    if os.path.abspath(out) == os.path.abspath(source):
        raise OpError("out= is the source itself: give a new name (the source is never overwritten)")
    moved = None
    if os.path.exists(out):
        old = os.path.join(os.path.dirname(os.path.abspath(out)), 'old')
        os.makedirs(old, exist_ok=True)
        stem = os.path.splitext(os.path.basename(out))[0]
        moved = os.path.join(old, f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.wav")
        shutil.move(out, moved)
    sf.write(out, y, out_sr, subtype='PCM_24')
    lim = (f"limiter took {-r['limiter_gr_db']:.1f} dB at most (the gain alone would peak at "
           f"{r['peak_after_gain_dbtp']:.1f} dBTP)" if r['limiter_gr_db'] < 0 else
           f"no limiting (peak {r['peak_after_gain_dbtp']:.1f} dBTP after the gain)")
    ceil = r['ceiling_dbtp']
    aim = f"{tp:g}" if ceil == tp else f"{tp:g}, the wav aimed at {ceil:g} so its mp3 stays under {tp:g}"
    lines = [f"mastered -> {out}" + (f" (the old one is now {moved})" if moved else ''),
             f"{r['length_s']:.2f} s, {r['sr']} Hz, {r['channels']} ch, 24-bit; cut {r['trimmed'][0]:.2f}-"
             f"{r['trimmed'][1]:.2f} s of the source",
             f"measured: {r['lufs']:.1f} LUFS integrated, true peak {r['true_peak_dbtp']:.2f} dBTP (target {lufs:g} / "
             f"{aim}); source was {r['source_lufs']:.1f} LUFS, gain {r['gain_db']:+.1f} dB; {lim}"]
    if r['catch_gr_db'] < 0:
        lines.append(f"true-peak catch took {-r['catch_gr_db']:.2f} dB at most (the limiter's rounds left peaks over "
                     f"{ceil:g}); no re-gain after it, so the loudness lands {r['lufs'] - lufs:+.2f} LU from the "
                     f"target")
    if r['over_ceiling']:
        lines.append(f"WARNING: still {r['true_peak_dbtp'] - ceil:.1f} dB over the ceiling after "
                     f"{M.TP_ROUNDS if tp_catch else M.ROUNDS} rounds: "
                     f"the target is too loud for this mix; try lufs={lufs - 2:g}, or tp_catch=True")
    elif r['limiter_gr_db'] < -3:
        lines.append(f"note: over 3 dB of limiting can be heard on transients; lufs={lufs - 2:g} keeps them")
    lines.append("next: these lines are the cue's measurement; then the person listens to the file")
    return '\n'.join(lines)
