"""A Follow is a performance (the user, 2026-10-04): the mic records from the first moment, in clips on the Follow's
clock, which an agent stops (to read it while the user goes on), starts again, or turns off. Everything of one
performance is in scenes/<name>/performances/<id>/:

  perf.json        {id, person, scene, started, ended, seconds, markers: [{t, label, by}], take, take_shift,
                    clips: [{n, file, at, seconds, by, text, words, voice}]}   (t, at: seconds on the Follow clock)
  clip_<n>.<ext>   the audio, appended chunk by chunk while it records (a crash keeps what came in)

A clip's words come from whisper with word times, snapped onto the measured voice here, in the function that
consumes them: whisper starts a phrase's first word in the silence before it, 0.25 to 0.5 s early (it cost vox twice).
"""
import json
import re
import shutil  # noqa: F401  (the ffmpeg setting finds ffmpeg through shutil.which; tests patch it here)
import subprocess
import threading

import numpy as np
from .. import settings

ID = re.compile(r'^[A-Za-z0-9_\-]{1,80}$')
LOCK = threading.Lock()
SR = 16000
FRAME_S = 0.02                 # 20 ms frames
ABOVE_FLOOR_DB = 12            # voice: this far above the recording's own floor (a fixed "30 dB under the peak"
                               # counted quiet room tone as voice)
MARGIN_S = 0.06                # quiet edge consonants


def folder(scenes, name, perf):
    return scenes / name / 'performances' / perf


def read(d):
    f = d / 'perf.json'
    try:
        return json.loads(f.read_text(encoding='utf-8')) if f.is_file() else {}
    except ValueError:
        return {}


def merge(d, patch):
    """Merge keys into perf.json (the page's meta; the server's clips go through set_clip)."""
    with LOCK:
        d.mkdir(parents=True, exist_ok=True)
        m = read(d)
        m.update({k: v for k, v in patch.items() if k != 'clips'})
        (d / 'perf.json').write_text(json.dumps(m, indent=1), encoding='utf-8')
        return m


def set_clip(d, clip):
    with LOCK:
        m = read(d)
        clips = [c for c in m.get('clips', []) if c.get('n') != clip['n']]
        clips.append(clip)
        m['clips'] = sorted(clips, key=lambda c: c['n'])
        (d / 'perf.json').write_text(json.dumps(m, indent=1), encoding='utf-8')
        return m


class NoDecoder(RuntimeError):
    """The clip's format needs ffmpeg and there is none."""


def pcm(path):
    """The clip as 16 kHz mono float32: a WAV read with soundfile, anything else (the headset's webm/ogg opus) with
    ffmpeg (on PATH, or the ffmpeg setting)."""
    if str(path).lower().endswith('.wav'):
        import soundfile as sf
        x, sr = sf.read(str(path), dtype='float32', always_2d=True)
        x = x.mean(axis=1)
        if sr != SR:                                   # linear resampling is plenty for a power envelope
            x = np.interp(np.arange(int(len(x) * SR / sr)) * sr / SR, np.arange(len(x)), x).astype(np.float32)
        return x
    exe = settings.get('ffmpeg')
    if not exe:
        raise NoDecoder("ffmpeg not found: words not snapped (install it or config_set('ffmpeg', <path>))")
    r = subprocess.run([exe, '-v', 'error', '-i', str(path), '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'],
                       capture_output=True, timeout=120)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors='replace').strip()[-300:] or f'ffmpeg exited {r.returncode}')
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


STOP_SAID = re.compile(r"\b(?:stop|end|finish|cut)\s+(?:the\s+|this\s+)?(?:performance|performing|recording)\b", re.I)


def asks_stop(text):
    """The words that ask for the performance to end ("stop the performance", "stop the recording", "end
    performance"), or None."""
    m = STOP_SAID.search(text or '')
    return m.group(0) if m else None


def voiced(x):
    """Per 20 ms frame: is it voice? (power above the recording's own floor + ABOVE_FLOOR_DB)"""
    n = int(SR * FRAME_S)
    k = len(x) // n
    if not k:
        return np.zeros(0, bool)
    db = 10 * np.log10(np.mean(x[:k * n].reshape(k, n) ** 2, axis=1) + 1e-12)
    return db > np.percentile(db, 10) + ABOVE_FLOOR_DB


def snap_words(words, v):
    """Each word's start and end moved onto the voice inside its own span (a start in silence moves to where the
    voice begins, an end in silence to where it stops), with MARGIN_S for quiet edges. A word with no voice in its
    span is kept as whisper placed it and marked unvoiced."""
    out = []
    for w in words or []:
        a, b = float(w['start']), float(w['end'])
        i0, i1 = int(a / FRAME_S), int(np.ceil(b / FRAME_S))
        on = np.nonzero(v[max(0, i0):max(0, i1)])[0]
        if len(on):
            a2 = max(a, (i0 + on[0]) * FRAME_S - MARGIN_S)
            b2 = min(b, (i0 + on[-1] + 1) * FRAME_S + MARGIN_S)
            out.append({'word': w['word'], 'start': round(a2, 3), 'end': round(max(b2, a2 + FRAME_S), 3)})
        else:
            out.append({'word': w['word'], 'start': round(a, 3), 'end': round(b, 3), 'unvoiced': True})
    return out


def voice_span(v):
    on = np.nonzero(v)[0]
    return [round(on[0] * FRAME_S, 3), round((on[-1] + 1) * FRAME_S, 3)] if len(on) else None


def transcribe_clip(d, n, at, seconds, by, stt_words, emit):
    """Transcribe clip n with word times, snap them onto the voice, store them in perf.json and emit perform_clip
    (words on the Follow clock: [word, start, end])."""
    files = sorted(d.glob(f'clip_{n}.*'))
    clip = {'n': n, 'at': at, 'seconds': seconds, 'by': by, 'file': files[0].name if files else None}
    if not files:
        clip['error'] = 'no audio arrived'
    else:
        try:
            got = stt_words(files[0].read_bytes(), files[0].name)
            clip['text'] = got.get('text', '')
            if got.get('words') is None:
                clip['words'] = None
                clip['words_missing'] = 'the speech server gave no word times (restart speakwright for verbose_json)'
            else:
                try:
                    v = voiced(pcm(files[0]))
                except NoDecoder as e:                    # the text still arrives; raw times never do
                    clip['words'], clip['words_missing'] = None, str(e)
                else:
                    clip['voice'] = voice_span(v)
                    clip['words'] = snap_words(got['words'], v)
        except Exception as e:                                    # noqa: BLE001
            clip['error'] = str(e)
    set_clip(d, clip)
    ev = {'type': 'perform_clip', 'perf': d.name, 'clip': n, 'at': at, 'seconds': seconds, 'by': by,
          'text': clip.get('text'), **({'error': clip['error']} if 'error' in clip else {})}
    if clip.get('words'):
        ev['words'] = [[w['word'], round(at + w['start'], 2), round(at + w['end'], 2)] for w in clip['words']]
    elif 'words_missing' in clip:
        ev['words_missing'] = clip['words_missing']
    emit(ev)
    return clip


def summary(scenes, name, perf=None):
    """One performance (the newest when perf is None) as text an agent reads: who, how long, markers, and each clip
    with its words on the Follow clock."""
    root = scenes / name / 'performances'
    ds = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name) if root.is_dir() else []
    if perf:
        ds = [p for p in ds if p.name == perf]
    if not ds:
        return None
    d = ds[-1]
    m = read(d)
    lines = [f"performance {d.name}: {m.get('person')} in {name}, {m.get('seconds', '?')} s"
             + (' (still going)' if not m.get('ended') else '') + (f", take {m['take']} (shift {m.get('take_shift')} s)" if m.get('take') else '')]
    for k in m.get('markers', []):
        lines.append(f"  marker {k.get('t')} s: {k.get('label')} ({k.get('by')})")
    for c in m.get('clips', []):
        head = f"  clip {c['n']} at {c['at']} s, {c.get('seconds')} s ({c.get('by')})"
        if c.get('error'):
            lines.append(head + ': ERROR ' + c['error'])
            continue
        lines.append(head + ': ' + (c.get('text') or '(no words)'))
        if c.get('words'):
            lines.append('    ' + ' '.join(f"{w['word']}@{c['at'] + w['start']:.2f}" for w in c['words']))
    others = [p.name for p in ds[:-1]][-5:] if not perf else []
    if others:
        lines.append('earlier: ' + ', '.join(others))
    return '\n'.join(lines)
