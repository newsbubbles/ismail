"""The exam pre-flight: what every agent runs before an exam page goes to the person (Nate, 2026-10-05: self-checks
live in tools). A blind exam that leaks its answer, a clip that does not play, a clip quieter than the rest, or a
Submit that lands nowhere wastes a round of the one instrument no metric replaces: the person's ear.

run(...) -> (ready, lines). Lines start with FAIL (never show the page), WARN (a likely tell: fix it or say why it
cannot be) or ok. The checks:
- clips: each exists, decodes, is not silent, and how near full scale it peaks;
- loudness: integrated loudness within lufs_tol of each other, and no class louder on average than another;
- the same audio: two clips of a pair (1A and 1B; every two clips when the labels name no pairs) that differ by less
  than SAME_DB once aligned and level-matched are one sound twice, and the ear cannot tell them (ledger:M212). A
  deliberate same-against-same control is a floor pair (phone_exam floor=), not a pair of the round;
- blind leaks, given the key ({clip label, file name or path: its class}) and any secret strings: the key's classes
  or the secrets in clip file names and URLs, in their metadata tags, in the page source (and the scripts and styles
  it loads) next to a clip or in a label-to-class mapping, a key/answer file the page loads, formats (sample rate,
  channels, encoding) that differ by class, durations or leading silence that separate the classes, and a clip
  order that follows the key;
- the overall choice: on an exam of pairs (1A, 1B, 2A ...) whose real side moves between pairs, one single-choice
  question that asks which side is real ('A is the real band' / 'B is the real band') fails, and one that names
  sides about anything else warns (Nate, metal round 3: ask "real or fake?" per clip instead);
- submit: a marked test answer posted to submit_url lands in answers_path (where the asking agent reads it).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request

import numpy as np

AUDIO = r'(?:wav|mp3|ogg|oga|flac|m4a|aac|webm|opus)'
SAME_DB = -40.0        # a residual under this, against the clip's own level, is the same sound to the ear
PAIR = re.compile(r'^(\d+)\s*([A-Za-z])$')
KEYISH = re.compile(r'(key|answer|truth|solution|reveal)[\w.-]*\.(json|txt|csv|js)', re.I)


def _decode(path):
    import soundfile as sf
    try:
        info = sf.info(path)
        y, sr = sf.read(path, dtype='float32', always_2d=True)
        return y, sr, {'samplerate': info.samplerate, 'channels': info.channels, 'subtype': info.subtype,
                       'format': info.format}
    except Exception as first:
        ff = os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg')
        if not ff:
            raise first
        r = subprocess.run([ff, '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '2', '-ar', '44100', 'pipe:1'],
                           capture_output=True, timeout=120)
        if r.returncode or not r.stdout:
            raise RuntimeError(r.stderr.decode('utf8', 'replace').strip()[:200] or str(first))
        y = np.frombuffer(r.stdout, dtype='<f4').reshape(-1, 2)
        return y, 44100, {'samplerate': None, 'channels': None, 'subtype': os.path.splitext(path)[1].lower(),
                          'format': 'ffmpeg'}


def _tags(path):
    """Metadata tags (title, artist, comment, encoder ...) from ffprobe, or {} without it."""
    fp = shutil.which('ffprobe') or (os.path.join(os.path.dirname(os.environ['ISMAIL_FFMPEG']), 'ffprobe')
                                     if os.environ.get('ISMAIL_FFMPEG') else None)
    if not fp:
        return {}
    try:
        r = subprocess.run([fp, '-v', 'quiet', '-print_format', 'json', '-show_format', path], capture_output=True,
                           text=True, timeout=30)
        return {k.lower(): str(v) for k, v in ((json.loads(r.stdout or '{}').get('format') or {}).get('tags') or {}).items()}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _loudness(y, sr):
    mono = y.mean(axis=1)
    try:
        import pyloudnorm as pyln
        if len(mono) >= int(0.45 * sr):
            v = float(pyln.Meter(sr).integrated_loudness(y if y.shape[1] <= 2 else mono))
            if np.isfinite(v):
                return v, 'LUFS'
    except Exception:
        pass
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    return 20 * np.log10(rms) - 0.691, 'dB RMS'


def _air(y, sr):
    """The clip's top end against its middle: (air dB = 8-12 kHz vs 0.5-2 kHz, the 1/3-octave levels from 6 kHz up,
    each against 0.5-2 kHz). A take through a Bluetooth headset microphone is cut at about 7 kHz (16 kHz audio): its
    air is near -120 dB where a studio mic gives -25 to -30 (vox:r39)."""
    m = y.mean(axis=1) if y.ndim > 1 else y
    if len(m) < 2048:
        return None, []
    f = np.fft.rfftfreq(len(m), 1 / sr)
    p = np.abs(np.fft.rfft(m * np.hanning(len(m)))) ** 2
    mid = p[(f >= 500) & (f < 2000)].sum() + 1e-30
    lvl = lambda a, b: 10 * np.log10(p[(f >= a) & (f < b)].sum() / mid + 1e-15)   # noqa: E731
    centres = [c for c in (6300, 8000, 10000, 12500, 16000) if c * 2 ** (1 / 6) < sr / 2]
    return lvl(8000, min(12000, sr / 2)), [lvl(c * 2 ** (-1 / 6), c * 2 ** (1 / 6)) for c in centres]


def _lead(y, sr):
    a = np.abs(y).max(axis=1)
    i = np.argmax(a > 10 ** (-50 / 20))
    return i / sr if a.max() > 10 ** (-50 / 20) else None


def _mono(y):
    return y.mean(axis=1) if y.ndim > 1 else y


def _difference(y1, sr1, y2, sr2, max_s=30.0, lag_s=0.25):
    """How far apart two clips are as sound: (residual dB against the first, lag ms, gain dB) after the best
    alignment within lag_s and one least-squares gain. None when they share under 0.2 s."""
    from scipy.signal import resample_poly
    a, b = _mono(np.asarray(y1, float)), _mono(np.asarray(y2, float))
    if sr1 != sr2:
        sr = min(sr1, sr2)
        a = resample_poly(a, sr, sr1) if sr1 != sr else a
        b = resample_poly(b, sr, sr2) if sr2 != sr else b
    else:
        sr = sr1
    a, b = a[:int(max_s * sr)], b[:int(max_s * sr)]
    n = min(len(a), len(b))
    if n < 0.2 * sr:
        return None
    m = int(lag_s * sr)
    size = 1 << int(np.ceil(np.log2(len(a) + len(b))))
    xc = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    xc = np.concatenate([xc[-m:], xc[:m + 1]])                      # lags -m .. m
    lag = int(np.argmax(np.abs(xc))) - m
    a2, b2 = (a[lag:], b) if lag >= 0 else (a, b[-lag:])
    k = min(len(a2), len(b2))
    a2, b2 = a2[:k], b2[:k]
    eb, ea = float(b2 @ b2), float(a2 @ a2)
    if eb <= 0 or ea <= 0:
        return None
    g = float(a2 @ b2) / eb
    res = float(np.sum((a2 - g * b2) ** 2)) / ea
    return 10 * np.log10(res + 1e-20), 1000.0 * lag / sr, 20 * np.log10(abs(g) + 1e-12)


def _pairs(cl):
    """The pairs a round compares: by label (1A with 1B), else every two clips (up to 8 clips)."""
    groups = {}
    for c in cl:
        m = PAIR.match(str(c['label']))
        if m:
            groups.setdefault(m.group(1), []).append(c)
    if groups:
        return [(g[i], g[j]) for g in groups.values() for i in range(len(g)) for j in range(i + 1, len(g))]
    return [(cl[i], cl[j]) for i in range(len(cl)) for j in range(i + 1, len(cl))] if len(cl) <= 8 else []


def _words(terms):
    return [t for t in dict.fromkeys(str(t).strip() for t in terms if t is not None) if len(t) >= 3]


def _hit(text, term):
    return re.search(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', text, re.I)


def _fetch(url, dest_dir):
    with urllib.request.urlopen(url, timeout=20) as r:
        data = r.read()
    name = os.path.basename(urllib.parse.urlparse(url).path) or 'index.html'
    p = os.path.join(dest_dir, f"{len(os.listdir(dest_dir))}_{name}")
    with open(p, 'wb') as f:
        f.write(data)
    return p, data


def _page(page, tmp):
    """-> (texts {name: source}, clips [{label, path, ref}]) from an exam page file or URL and what it loads."""
    is_url = re.match(r'https?://', page or '')
    texts, clips, seen = {}, [], set()

    def load(ref, base):
        loc = urllib.parse.urljoin(base, ref) if is_url else os.path.normpath(os.path.join(base, ref))
        if is_url:
            p, data = _fetch(loc, tmp)
            return loc, p, data
        with open(loc, 'rb') as f:
            return loc, loc, f.read()

    if is_url:
        loc, _, data = load(page, page)
        base = loc
    else:
        with open(page, 'rb') as f:
            data = f.read()
        loc, base = page, os.path.dirname(os.path.abspath(page))
    html = data.decode('utf8', 'replace')
    texts[os.path.basename(urllib.parse.urlparse(loc).path) or loc] = html
    for ref in re.findall(r'(?:src|href)\s*=\s*["\']([^"\'#?]+\.(?:js|mjs|css|json))', html, re.I):
        try:
            _, _, d = load(ref, base)
            texts[ref] = d.decode('utf8', 'replace')
        except (OSError, ValueError):
            texts[ref] = ''
    for name, src in list(texts.items()):
        for ref in re.findall(r'["\'(`]([^"\'()`\s]+\.' + AUDIO + r')(?:[?#][^"\'()`\s]*)?["\')`]', src, re.I):
            if ref in seen:
                continue
            seen.add(ref)
            try:
                loc2, p, _ = load(ref, base)
                clips.append({'label': ref, 'path': p, 'ref': ref})
            except (OSError, ValueError) as e:
                clips.append({'label': ref, 'path': None, 'ref': ref, 'error': str(e)})
    return texts, clips


def run(clips=None, page=None, key=None, secrets=None, answers_path=None, submit_url=None, submit_body=None,
        lufs_tol=1.0, texts=None, choices=None, question=None):
    fails, warns, oks = [], [], []
    tmp = tempfile.mkdtemp(prefix='exam_check_')
    try:
        texts = dict(texts or {})
        found = []
        if page:
            try:
                t, found = _page(page, tmp)
                texts.update(t)
            except (OSError, ValueError) as e:
                fails.append(f"the page does not load: {page}: {e}")
        cl = []
        for i, c in enumerate(clips or found):
            c = dict(c) if isinstance(c, dict) else {'path': c}
            c.setdefault('label', c.get('ref') or (os.path.basename(c['path']) if c.get('path') else f'clip {i + 1}'))
            c.setdefault('ref', c.get('path') or '')
            cl.append(c)
        if not cl:
            fails.append("no clips: pass clips=[...] or a page whose audio can be found")
            return False, _report(fails, warns, oks, 0)

        # ---- the clips play
        for c in cl:
            p = c.get('path')
            if c.get('error') or not p or not os.path.isfile(p):
                fails.append(f"{c['label']}: missing ({c.get('error') or p})")
                continue
            try:
                y, sr, fmt = _decode(p)
            except Exception as e:
                fails.append(f"{c['label']}: does not decode ({type(e).__name__}: {str(e)[:120]})")
                continue
            peak = float(np.abs(y).max()) if len(y) else 0.0
            c['_y'], c['_sr'] = y, sr
            c.update(dur=len(y) / sr, fmt=fmt, peak_db=20 * np.log10(peak + 1e-12), lead=_lead(y, sr),
                     tags=_tags(p))
            c['air'], c['hf'] = _air(y, sr)
            if c['air'] is not None and c['air'] < -60:
                warns.append(f"{c['label']}: nothing above 8 kHz (air {c['air']:.0f} dB against 0.5-2 kHz): a band-"
                             f"limited take (a Bluetooth headset microphone records 16 kHz audio) or a heavy low-pass")
            c['loud'], c['unit'] = _loudness(y, sr) if peak > 0 else (-120.0, 'dB RMS')
            if peak < 10 ** (-60 / 20):
                fails.append(f"{c['label']}: silent (peak {c['peak_db']:.0f} dBFS)")
            elif c['peak_db'] > -0.1:
                warns.append(f"{c['label']}: peaks at {c['peak_db']:.2f} dBFS (it may clip on the person's player)")
        good = [c for c in cl if 'dur' in c]
        if len(good) == len(cl):
            oks.append(f"all {len(cl)} clips exist and decode (" + ', '.join(f"{c['label']} {c['dur']:.1f} s" for c in cl[:8])
                       + (' ...' if len(cl) > 8 else '') + ")")

        # ---- one listening level
        if len(good) >= 2:
            ls = [c['loud'] for c in good]
            spread = max(ls) - min(ls)
            line = ', '.join(f"{c['label']} {c['loud']:.1f}" for c in good[:10]) + f" {good[0]['unit']}"
            (fails if spread > lufs_tol else oks).append(
                f"loudness spread {spread:.1f} {'over' if spread > lufs_tol else 'within'} {lufs_tol:g}: {line}"
                + (": normalise every clip to one loudness" if spread > lufs_tol else ''))

        # ---- one sound twice: the ear cannot tell a pair that is the same audio
        same, apart = [], []
        for c1, c2 in _pairs(good):
            d = _difference(c1['_y'], c1['_sr'], c2['_y'], c2['_sr'])
            if d is None:
                continue
            (same if d[0] < SAME_DB else apart).append((c1['label'], c2['label'], d))
        for l1, l2, (res, lag, gain) in same:
            fails.append(f"{l1} and {l2} are the same audio: {l2} is {l1} {-lag:+.0f} ms later and {-gain:+.1f} dB "
                         f"louder, and past that they differ by {res:.0f} dB, under {SAME_DB:.0f}, so the ear cannot tell them and the pair "
                         f"spends a slot of the person's ear on nothing: check the render or the file you meant to put "
                         f"there (a same-against-same control is a floor pair: phone_exam floor='auto')")
        if apart:
            oks.append("every pair differs as sound (" + ', '.join(f"{a}/{b} {round(d[0]) + 0:.0f} dB" for a, b, d in apart[:6])
                       + (' ...' if len(apart) > 6 else '') + f" residual; the same audio is under {SAME_DB:.0f})")
        for c in cl:
            c.pop('_y', None)

        # ---- the key and what could leak it
        key = {str(k): str(v) for k, v in (key or {}).items()}

        def cls(c):
            for k in (c['label'], os.path.basename(c.get('path') or ''), c.get('path') or '', c.get('ref') or ''):
                if k and k in key:
                    return key[k]
            return None
        for c in cl:
            c['cls'] = cls(c)
        classes = sorted({c['cls'] for c in cl if c['cls']})
        terms = _words(list(classes) + list(secrets or []))
        if key and not classes:
            warns.append("the key matches no clip (key by the clip's label, file name or path): the leak checks "
                         "that need it were skipped")
        if not key and not secrets:
            warns.append("no key and no secrets given: the blind-leak checks are limited; pass key={clip: class} "
                         "(and secrets=[source names]) for the full check")

        for c in cl:                                            # names and urls
            for term in terms:
                for what in ('ref', 'path'):
                    v = os.path.basename(str(c.get(what) or ''))
                    if v and _hit(v, term):
                        fails.append(f"{c['label']}: its file name {v!r} says {term!r}")
                        break
            for k, v in (c.get('tags') or {}).items():
                for term in terms:
                    if _hit(v, term):
                        fails.append(f"{c['label']}: its metadata tag {k}={v!r} says {term!r}")
        for name, src in texts.items():                         # the page source
            for m in KEYISH.finditer(src):
                fails.append(f"the page ({name}) loads {m.group(0)!r}: an answer key the person's browser can read")
            refs = [os.path.basename(str(c.get('ref') or c.get('path') or '')) for c in cl]
            for term in terms:
                for m in re.finditer(r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])', src, re.I):
                    # a leak ties the word to one clip: the same tag or statement (no tag edge, ';' or line between),
                    # or a "label": "class" mapping; a question that names the classes for every clip is fine
                    tied = False
                    for r in filter(None, refs):
                        for rm in re.finditer(re.escape(r), src):
                            lo, hi = sorted((rm.end(), m.start())) if rm.start() < m.start() else (m.end(), rm.start())
                            if hi - lo <= 80 and not re.search(r'[<>;\n]', src[lo:hi]):
                                tied = True
                                break
                        if tied:
                            break
                    tied = tied or bool(re.search(r'["\']\s*:\s*["\']?$', src[max(0, m.start() - 8):m.start()]))
                    if tied:
                        near = src[max(0, m.start() - 70):m.end() + 70]
                        fails.append(f"the page ({name}) ties {term!r} to a clip: ...{' '.join(near.split())[:140]}...")
                        break
        if classes and len(classes) >= 2:
            by = {k: [c for c in good if c['cls'] == k] for k in classes}
            fmts = {k: {(c['fmt']['samplerate'], c['fmt']['channels'], c['fmt']['subtype'],
                         os.path.splitext(c.get('path') or '')[1].lower()) for c in v} for k, v in by.items()}
            if all(fmts.values()) and not set.intersection(*fmts.values()):
                fails.append("formats differ by class (sample rate, channels, encoding or file type): "
                             + '; '.join(f"{k}: {sorted(map(str, v))}" for k, v in fmts.items()))
            durs = {k: [c['dur'] for c in v] for k, v in by.items() if v}
            ks = sorted(durs, key=lambda k: min(durs[k]))
            for a, b in zip(ks, ks[1:]):
                lo_gap = min(durs[b]) - max(durs[a])
                if len(durs[a]) >= 2 and len(durs[b]) >= 2 and lo_gap > 0.05:
                    fails.append(f"durations separate the classes: {a} {min(durs[a]):.2f}-{max(durs[a]):.2f} s, "
                                 f"{b} {min(durs[b]):.2f}-{max(durs[b]):.2f} s (cut every clip the same way)")
                elif (len(durs[a]) == 1 or len(durs[b]) == 1) and lo_gap > 0.25:
                    warns.append(f"{a} and {b} differ in length by {lo_gap:.2f} s or more (a tell if the person notices)")
            leads = {k: [c['lead'] for c in v if c['lead'] is not None] for k, v in by.items()}
            means = {k: float(np.mean(v)) for k, v in leads.items() if v}
            if len(means) >= 2 and max(means.values()) - min(means.values()) > 0.04:
                warns.append("leading silence differs by class: " + ', '.join(f"{k} {v * 1000:.0f} ms" for k, v in
                                                                              means.items()))
            # the top end by class: a class recorded or made differently there is heard as that class (vox:r39:
            # the real takes went through an earbud mic, cut at 7 kHz; the synth filled the band 45-50 dB above them)
            hfs = {k: np.array([c['hf'] for c in v if c.get('hf')]) for k, v in by.items()}
            hfs = {k: v for k, v in hfs.items() if len(v) and v.ndim == 2}
            if len(hfs) >= 2:
                means = {k: v.mean(axis=0) for k, v in hfs.items()}
                n = min(len(v) for v in means.values())     # classes at other sample rates: the shared bands
                means = {k: v[:n] for k, v in means.items()}
                spread = max(float(np.max(v.max(axis=0) - v.min(axis=0))) if len(v) > 1 else 0.0 for v in hfs.values())
                ks = sorted(means)
                gap = max(float(np.max(np.abs(means[a] - means[b]))) for i, a in enumerate(ks) for b in ks[i + 1:])
                if gap > max(10.0, 2 * spread):
                    fails.append("the classes differ above 6 kHz by up to " + f"{gap:.0f} dB (" + ', '.join(
                        f"{k} {float(np.mean(v)):+.0f} dB" for k, v in means.items()) + " against 0.5-2 kHz; the "
                        f"clips within a class spread {spread:.0f} dB): a tell, and often a recording fault (a band-"
                        f"limited mic on one side). Record every class the same way, or match the top end")
                elif gap > max(6.0, spread):
                    warns.append(f"the classes differ above 6 kHz by up to {gap:.0f} dB (within a class {spread:.0f}"
                                 f" dB): listen for it before you show the exam")
            louds = {k: float(np.mean([c['loud'] for c in v])) for k, v in by.items() if v}
            if len(louds) >= 2 and max(louds.values()) - min(louds.values()) > 0.5:
                warns.append("one class is louder on average: " + ', '.join(f"{k} {v:.1f}" for k, v in louds.items()))
            seq = [c['cls'] for c in cl if c['cls']]
            pairs = [seq[i:i + len(classes)] for i in range(0, len(seq) - len(classes) + 1, len(classes))]
            trial = [re.match(r'^(\d+)[A-Za-z]$', str(c['label'])) for c in cl if c['cls']]
            if seq and all(trial):                      # clips labelled by trial (1A, 1B, 2A ...): the trials are
                by_t = {}                               # those, however many classes the key has (spanish_guitar
                for m, k in zip(trial, seq):            # 10-11: 3 classes over 2-clip pairs read as triples)
                    by_t.setdefault(m.group(1), []).append(k)
                pairs = [v for v in by_t.values() if len(v) >= 2]
            if len(pairs) >= 2 and len({p[0] for p in pairs}) == 1:
                fails.append(f"the order follows the key: every trial starts with {pairs[0][0]!r} (shuffle per trial)")
            elif len(pairs) >= 6:                      # each class first in about its share of the trials (vox:r41)
                for k in classes:
                    first = sum(1 for p in pairs if p[0] == k)
                    if first / len(pairs) >= 0.8:
                        warns.append(f"{k!r} comes first in {first} of {len(pairs)} trials: balance the sides")
            elif len(seq) >= 4 and seq == sorted(seq):
                fails.append("the clips are in key order (all of one class, then the next): shuffle them")
            if not any(f.startswith(('formats', 'durations', 'the order', 'the clips are')) for f in fails):
                oks.append(f"no tell by format, length or order across {len(classes)} classes")
        side = side_choice(choices, question, [(c['label'], c['cls']) for c in cl])
        if side:
            (fails if side[0] == 'FAIL' else warns).append(side[1])
        elif choices and classes:
            oks.append("the overall choice asks nothing a moving side would break")
        if terms and not any('says' in f or 'ties' in f or 'loads' in f for f in fails):
            oks.append(f"no leak of {', '.join(repr(t) for t in terms[:6])} in names, tags or the page")

        # ---- Submit lands where the agent reads
        if submit_url:
            before = os.path.getsize(answers_path) if answers_path and os.path.exists(answers_path) else 0
            body = submit_body or {'preflight': True, 'note': 'exam_check round trip: ignore this line'}
            try:
                req = urllib.request.Request(submit_url, data=json.dumps(body).encode(), method='POST',
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=15) as r:
                    status = r.status
            except Exception as e:
                status = None
                fails.append(f"Submit does not go through: POST {submit_url}: {type(e).__name__}: {e}")
            if status and answers_path:
                got = ''
                if os.path.exists(answers_path):
                    with open(answers_path, 'rb') as f:
                        f.seek(before)
                        got = f.read().decode('utf8', 'replace')
                if 'preflight' in got:
                    oks.append(f"Submit lands in {answers_path} (a test line marked preflight was added: skip it "
                               f"when scoring)")
                else:
                    fails.append(f"Submit answered {status} but nothing new reached {answers_path}: the person's "
                                 f"answers would be lost")
            elif status:
                warns.append(f"Submit answered {status}; pass answers_path to check it lands where you read it")
        elif answers_path:
            d = os.path.dirname(os.path.abspath(answers_path))
            up = d
            while not os.path.isdir(up) and os.path.dirname(up) != up:
                up = os.path.dirname(up)
            (oks if os.access(up, os.W_OK) else fails).append(
                f"answers go to {answers_path}" + ('' if os.path.isdir(d) else ' (its folder will be made)')
                + ('' if os.access(up, os.W_OK) else ': not writable'))
        else:
            warns.append("no submit_url or answers_path: the Submit round trip was not checked")
        return not fails, _report(fails, warns, oks, len(cl))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ one overall "A or B" choice across pairs
# Nate, metal round 3 (2026-10-22 voice note): "the last question of A being the real band or B being the real band,
# or can't tell, I think that's redundant and it also doesn't match what you're doing here because you don't have
# A or B be the real band every time." The real side moves between pairs, so one A-or-B answer has nothing to name.
REALISH = ('real', 'fake', 'record', 'recording', 'original', 'reference', 'ref', 'genuine', 'human', 'synth',
           'synthetic', 'ai', 'machine', 'ours', 'mine', 'made', 'render', 'rendered')
REAL_CLASSES = ('real', 'record', 'recording', 'original', 'reference', 'ref', 'genuine', 'human')
SIDE_WORD = r'(?i:clip|side|sound|option|take|version)'


def _side_of(choice, sides):
    """The side a choice names: 'A is the record', 'B', 'B)', 'clip A', 'the real one is B'; not 'A lot'."""
    s = str(choice).strip()
    for x in sides:
        e = re.escape(x)
        if (re.match(rf'^{e}(?:\s*$|\s*[).:,=]|\s+(?:is|was|sounds|seems|feels|has)\b)', s)
                or re.search(rf'(?<![A-Za-z0-9]){SIDE_WORD}\s+{e}(?![A-Za-z0-9])', s)
                or re.search(rf'\b(?:is|was)\s+{e}\s*[.!?]?$', s)):
            return x
    return None


def side_choice(choices, question, clips):
    """The overall single-choice question on a multi-pair exam. clips: [(label, class)], labelled by trial (1A, 1B,
    2A ...). -> None, or (level, text): FAIL when the choices ask which side holds a class (the real one) and that
    class changes side between the trials; WARN when they name sides that mean a different class in each trial. A
    choice that names no side ('pair 1', "can't tell", 'yes'), a single trial, or classes that keep their side are
    never flagged."""
    trials = {}
    for lab, k in clips:
        m = PAIR.match(str(lab))
        if m and k:
            trials.setdefault(m.group(1), {})[m.group(2).upper()] = str(k)
    trials = {t: v for t, v in trials.items() if len(v) >= 2}
    if len(trials) < 2 or not choices:
        return None
    sides = sorted({s for v in trials.values() for s in v})
    named = [(str(c), _side_of(c, sides)) for c in choices]
    named = [(c, s) for c, s in named if s]
    if len({s for _, s in named}) < 2:
        return None
    classes = sorted({k for v in trials.values() for k in v.values()})
    where = {k: {} for k in classes}
    for t in sorted(trials, key=int):
        for s, k in sorted(trials[t].items()):
            where[k].setdefault(s, []).append(t)
    moving = [k for k in classes if len(where[k]) > 1]
    if not moving:
        return None
    said = ' '.join(c for c, _ in named)
    bare = all(not re.sub(rf'{SIDE_WORD}|\b[A-Z]\b|\W', '', c) for c, _ in named)
    words = _words(classes) + list(REALISH)
    about = [w for w in words if _hit(said, w)] or ([w for w in words if _hit(question or '', w)] if bare else [])
    target = (next((k for k in moving if any(k.lower() == w.lower() for w in about)), None)
              or next((k for k in moving if k.lower() in REAL_CLASSES), None) or moving[0])
    at = '; '.join(f"{s} in pair{'s' * (len(ts) > 1)} {', '.join(ts)}" for s, ts in sorted(where[target].items()))
    shown = ' / '.join(repr(c) for c, _ in named)
    if about:
        return 'FAIL', (f"one overall choice ({shown}) asks which side is {target!r}, but {target!r} moves between "
                        f"pairs ({at}): that answer names nothing. Ask \"real or fake?\" for each clip of a pair (a "
                        f"chip or a choice per clip) and drop the overall side choice (Nate, metal round 3)")
    return 'WARN', (f"one overall choice names sides ({shown}), but a side is a different class in each pair "
                    f"({target!r}: {at}): an answer about one side means a different clip per pair. Ask per pair "
                    f"or per clip")


def _report(fails, warns, oks, n):
    head = (f"NOT READY: {len(fails)} problem{'s' * (len(fails) != 1)}; do not show this exam until they are fixed"
            if fails else f"READY: {n} clips" + (f", {len(warns)} warning{'s' * (len(warns) != 1)} to read" if warns else ''))
    return [head] + [f"FAIL {x}" for x in dict.fromkeys(fails)] + [f"WARN {x}" for x in dict.fromkeys(warns)] + \
        [f"ok   {x}" for x in oks]


# ------------------------------------------------------------------ the blind crop check (a pre-exam gate)
# Nate's "90 degree rotation" (https://newsbubbles.github.io/rotation/): when one sense plateaus, rotate which one
# measures. A spectrogram is an image an eye reads well; after vox:r41 he saw in one look a tell 40 rounds of 1D
# numbers missed, and vox's work/eye_crops.py made it a gate: the same window cut from the real and the made clip of
# each pair, side by side in a random order, the key hidden. If the agent can pick the real side from the picture,
# the person will hear it too: the round is not ready (Voice picked 15 of 16 on r44-r45; chance is about 3 in 10,000).

def _crop_image(ax, y, sr, t0, t1, top_hz=16000):
    import matplotlib.mlab as mlab
    m = y.mean(axis=1) if y.ndim > 1 else y
    S, f, t = mlab.specgram(m, NFFT=1024, Fs=sr, noverlap=896)
    S = 10 * np.log10(S + 1e-20)
    k = (t >= t0) & (t <= t1)
    if not k.any():
        raise ValueError(f"window {t0:.2f}-{t1:.2f} s is outside the clip ({len(m) / sr:.2f} s)")
    ax.imshow(S[:, k], origin='lower', aspect='auto', cmap='magma', vmin=-150, vmax=-40,
              extent=[t[k][0], t[k][-1], 0, f[-1]])
    ax.set_ylim(0, min(top_hz, sr / 2))
    ax.set_xticks([])
    ticks = [x for x in (0, 4000, 8000, 12000, 16000) if x <= min(top_hz, sr / 2)]
    ax.set_yticks(ticks)
    ax.set_yticklabels([f"{x // 1000}k" if x else '0' for x in ticks], fontsize=7)


def eye_crops(pairs, out, windows=None, n_windows=3, width_s=0.35, seed=0):
    """pairs: [[real, made], ...] (paths); windows: per pair, [[t0, t1], ...] in seconds (a word +-60 ms), else
    n_windows evenly through the shorter clip. Writes out/q01.png ... ("1" and "2" in a random order),
    out/questions.json (no side) and out/key.json (hidden: do not open it before answers.json is written).
    -> the number of crop pairs."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import soundfile as sf
    os.makedirs(out, exist_ok=True)
    rng = np.random.default_rng(seed)
    key, qs, n = [], [], 0
    for i, pr in enumerate(pairs):
        ys = [sf.read(str(p), dtype='float64') for p in pr]
        dur = min(len(y) / sr for y, sr in ys)
        ws = (windows[i] if windows and i < len(windows) and windows[i] else
              [[c - width_s / 2, c + width_s / 2] for c in np.linspace(dur * 0.15, dur * 0.85, n_windows)])
        for t0, t1 in ws:
            t0, t1 = max(0.0, float(t0)), min(dur, float(t1))
            order = [0, 1] if rng.random() < 0.5 else [1, 0]
            fig, axs = plt.subplots(1, 2, figsize=(6, 2.6), dpi=100)
            for ax, side, lab in zip(axs, order, ('1', '2')):
                y, sr = ys[side]
                _crop_image(ax, y, sr, t0, t1)
                ax.set_title(lab, fontsize=10)
            fig.tight_layout()
            n += 1
            fig.savefig(os.path.join(out, f"q{n:02d}.png"))
            plt.close(fig)
            key.append({'q': n, 'pair': i + 1, 't0': round(t0, 3), 't1': round(t1, 3),
                        'real': '1' if order[0] == 0 else '2'})
            qs.append({'q': n, 'pair': i + 1, 't0': round(t0, 3), 't1': round(t1, 3)})
    with open(os.path.join(out, 'key.json'), 'w', encoding='utf8') as f:
        json.dump(key, f, indent=1)
    with open(os.path.join(out, 'questions.json'), 'w', encoding='utf8') as f:
        json.dump(qs, f, indent=1)
    return n


def eye_score(out, answers):
    """answers: {q: '1' | '2'} (which side looks real), written before key.json is opened. -> (ready, lines): not
    ready when the picks beat chance at p < 0.05 (one-sided binomial)."""
    import math
    with open(os.path.join(out, 'answers.json'), 'w', encoding='utf8') as f:
        json.dump({str(k): str(v) for k, v in answers.items()}, f, indent=1)
    with open(os.path.join(out, 'key.json'), encoding='utf8') as f:
        key = {str(k['q']): k for k in json.load(f)}
    picked = {str(k): str(v) for k, v in answers.items() if str(k) in key}
    n = len(picked)
    if not n:
        raise ValueError(f"no answers for the questions in {out} (keys are question numbers, values '1' or '2')")
    hits = sum(1 for q, v in picked.items() if key[q]['real'] == v)
    p = sum(math.comb(n, k) for k in range(hits, n + 1)) / 2 ** n
    missing = len(key) - n
    lines = [f"blind crop check: picked the real side in {hits} of {n} (chance would give {n / 2:g}; p = {p:.4f})"
             + (f"; {missing} unanswered" if missing else '')]
    tells = sorted({key[q]['pair'] for q, v in picked.items() if key[q]['real'] == v})
    if p < 0.05:
        lines[0] = 'NOT READY: ' + lines[0]
        lines.append(f"the picture gives the real side away (pairs {', '.join(map(str, tells))}): the person will "
                     f"likely hear it too. Look at the crops again for what you read (a smooth top end, a missing "
                     f"band, a boundary early or late), zoom two ways (time-sharp and frequency-sharp) to find where, "
                     f"and fix that before the round")
    else:
        lines[0] = 'READY: ' + lines[0]
    return p >= 0.05, lines
