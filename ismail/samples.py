"""Sample sets some built-in voices play from: recordings too big for the repo, or under a licence the repo cannot
carry (a non-commercial one). Nothing downloads by itself: `samples_fetch` fetches a set on the person's word, into
~/.ismail/samples/<set>/ ($ISMAIL_SAMPLES moves it), and a voice finds its set there, in its own environment
variable, or in a folder registered with samples_fetch(name, path=...)."""
import concurrent.futures
import io
import json
import os
import shutil
import urllib.request
import zipfile

SETS = {
    'jrhodes3d': {
        'voice': 'rhodes', 'env': 'RHODES_SAMPLES', 'sub': 'jRhodes3d-mono', 'check': '*.flac', 'min_files': 60,
        'what': '1977 Rhodes Mark I Stage 73, DI, 5 velocity layers (Jeff Learman)',
        'repo': 'sfzinstruments/jlearman.jRhodes3d', 'branch': 'master', 'paths': ['jRhodes3d-mono/', 'LICENSE',
                                                                                  'README.md'],
        'size_mb': 22,
        'licence': 'CC BY-NC 4.0 for the samples (no redistribution in a commercial product without the author); '
                   'the author grants CC0 for music made with them',
        'credit': 'jRhodes3d by Jeff Learman, https://github.com/sfzinstruments/jlearman.jRhodes3d',
    },
    'big_rusty': {
        'voice': 'rusty', 'env': 'RUSTY_SAMPLES', 'sub': 'Samples', 'check': 'kick_24', 'min_files': 1,
        'what': 'Big Rusty Drums (Karoryfer): a 1980s kit, multi-velocity round robins, close/bottom/overhead mics; '
                'the pieces the voice plays (kick, snare, hats, ride, crash, toms), and every stroke of the toms the toms voice '
                'plays (center, mallet, brush, rim click, shell)',
        'repo': 'sfzinstruments/karoryfer.big-rusty-drums', 'branch': 'main',
        'paths': ['Samples/kick_24/', 'Samples/snare_14/', 'Samples/hihat_14/', 'Samples/ride_22/', 'Samples/crash_17/',
                  'Samples/crash_sizzle_17/', 'Samples/tom_14/', 'Samples/tom_15/', 'Samples/tom_18/',
                  'Samples/tom_22/', 'LICENSE'],
        'size_mb': 520,
        'licence': 'CC0 1.0', 'credit': 'Big Rusty Drums by Karoryfer Samples, https://github.com/sfzinstruments/karoryfer.big-rusty-drums',
    },
    'emilyguitar': {
        'voice': 'emily', 'env': 'EMILY_SAMPLES', 'sub': 'Emilyguitar', 'check': 'notes', 'min_files': 1,
        'what': 'Emilyguitar (Karoryfer): a clean electric guitar, multi-sampled notes and releases',
        'zip': 'https://github.com/sfzinstruments/karoryfer.emilyguitar/releases/download/v1.001/Karoryfer.Emilyguitar.v1.001.zip',
        'zip_keep': ['notes/', 'release/', 'LICENSE', 'readme.txt'],
        'size_mb': 98,
        'licence': 'CC0 1.0', 'credit': 'Emilyguitar by D. Smolken, Karoryfer Samples, https://github.com/sfzinstruments/karoryfer.emilyguitar',
    },
    'sadie_d2': {   # URL and size not yet checked against the live site: the first fetch confirms them
        'fx': 'spatial', 'env': 'SADIE_D2_HRTF', 'sub': 'sofa', 'check': '*.sofa', 'min_files': 1,
        'what': 'SADIE II D2 head-related impulse responses: a Neumann KU100 dummy head measured from every direction '
                '(University of York), for spatial hrtf="sadie_d2"',
        'zip': 'https://www.york.ac.uk/sadie-project/Resources/SADIEIIDatabase/D2/D2_HRIR_SOFA.zip',
        'zip_keep': ['D2_44K'],
        'size_mb': 60,
        'licence': 'Apache 2.0', 'credit': 'SADIE II database, University of York, https://www.york.ac.uk/sadie-project/',
    },
}


def user(s):
    """Who plays a set: 'the rhodes voice' or 'the spatial effect'."""
    return f"the {s['fx']} effect" if 'fx' in s else f"the {s['voice']} voice"


class SampleError(RuntimeError):
    pass


def root():
    return os.environ.get('ISMAIL_SAMPLES') or os.path.join(os.path.expanduser('~'), '.ismail', 'samples')


def _registry():
    try:
        with open(os.path.join(root(), 'registered.json'), encoding='utf8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ok(path, s):
    if not path or not os.path.isdir(path):
        return False
    if s['check'].startswith('*'):
        return sum(1 for f in os.listdir(path) if f.endswith(s['check'][1:])) >= s['min_files']
    return os.path.exists(os.path.join(path, s['check']))


def path(name):
    """The folder a set's voice reads, or None: its environment variable, a registered folder, or the store."""
    s = SETS[name]
    for c in (os.environ.get(s['env'], ''), _registry().get(name, ''), os.path.join(root(), name, s['sub'])):
        if _ok(c, s):
            return os.path.abspath(c)
    return None


def paths(name):
    """Every folder on this machine that passes the set's check, in path()'s order (a voice that needs more of a set
    than the check, such as toms' tom folders, looks through them all: a registered copy may be partial)."""
    s, out = SETS[name], []
    for c in (os.environ.get(s['env'], ''), _registry().get(name, ''), os.path.join(root(), name, s['sub'])):
        if _ok(c, s) and os.path.abspath(c) not in out:
            out.append(os.path.abspath(c))
    return out


def need(name):
    """For a voice: its set's folder, or an error that says what to fetch, how big and under what licence."""
    p = path(name)
    if p:
        return p
    s = SETS[name]
    raise FileNotFoundError(
        f"{user(s)} plays the {name} samples ({s['what']}), which are not on this machine. Ask the person, "
        f"then samples_fetch('{name}'): about {s['size_mb']} MB, {s['licence']}. A folder they already have: "
        f"samples_fetch('{name}', path=<its {s['sub']} folder>) or ${s['env']}.")


def status():
    L = []
    for name, s in SETS.items():
        p = path(name)
        L.append(f"{name:<12} {'ready' if p else 'not fetched':<12} {user(s)[4:]:<14} ~{s['size_mb']} MB, "
                 f"{s['licence']}" + (f"  ({p})" if p else ''))
    return '\n'.join(L)


def _get(url, tries=3):
    last = None
    for _ in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'ismail'}), timeout=60) as r:
                return r.read()
        except OSError as e:
            last = e
    raise SampleError(f"download failed: {url}: {last}")


def _tree(repo, branch):
    d = json.loads(_get(f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"))
    if d.get('truncated'):
        raise SampleError(f"{repo}: the file list came back truncated")
    return [(t['path'], t.get('size', 0)) for t in d['tree'] if t['type'] == 'blob']


def fetch(name, register=None, log=print):
    """Download a set into the store (or register a folder that already holds it). -> its folder."""
    if name not in SETS:
        raise SampleError(f"no sample set {name!r}; sets: {', '.join(SETS)}")
    s = SETS[name]
    if register:
        p = os.path.abspath(register)
        if not _ok(p, s):
            raise SampleError(f"{p} does not look like the {name} {s['sub']} folder (it should hold {s['check']})")
        reg = _registry()
        reg[name] = p
        os.makedirs(root(), exist_ok=True)
        with open(os.path.join(root(), 'registered.json'), 'w', encoding='utf8') as f:
            json.dump(reg, f, indent=1)
        return p
    dest = os.path.join(root(), name)
    os.makedirs(dest, exist_ok=True)
    if 'zip' in s:
        log(f"downloading {s['zip']} (~{s['size_mb']} MB)")
        z = zipfile.ZipFile(io.BytesIO(_get(s['zip'])))
        for m in z.namelist():
            parts = m.split('/')
            rel = next(('/'.join(parts[i:]) for i in range(len(parts))
                        if any('/'.join(parts[i:]).startswith(k) for k in s['zip_keep'])), None)
            if m.endswith('/') or rel is None:
                continue
            out = os.path.join(dest, s['sub'], rel)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with z.open(m) as src, open(out, 'wb') as f:
                shutil.copyfileobj(src, f)
    else:
        files = [(p, n) for p, n in _tree(s['repo'], s['branch']) if any(p == k or p.startswith(k) for k in s['paths'])]
        log(f"downloading {len(files)} files from {s['repo']} (~{sum(n for _, n in files) / 2 ** 20:.0f} MB)")

        def one(item):
            p, _ = item
            out = os.path.join(dest, p)
            if os.path.exists(out):
                return
            os.makedirs(os.path.dirname(out), exist_ok=True)
            data = _get(f"https://raw.githubusercontent.com/{s['repo']}/{s['branch']}/{urllib.request.quote(p)}")
            with open(out + '.part', 'wb') as f:
                f.write(data)
            os.replace(out + '.part', out)
        with concurrent.futures.ThreadPoolExecutor(8) as ex:
            list(ex.map(one, files))
    with open(os.path.join(dest, 'CREDIT.txt'), 'w', encoding='utf8') as f:
        f.write(f"{s['credit']}\nLicence: {s['licence']}\n")
    p = path(name)
    if not p:
        raise SampleError(f"{name}: downloaded to {dest} but the voice cannot find {s['check']} under {s['sub']}")
    return p
