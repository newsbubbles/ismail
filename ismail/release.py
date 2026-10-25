"""One release step for the harness plugin stores (hq:D-213, D-214).

Every target is built from the same metadata (packaging/listing.json, version from pyproject.toml) and points at
the same MCP server (ismail.mcp_server, run as `ismail mcp`). A target makes:
- dist files: what a person uploads or shares, written to dist/<target>/ (gitignored);
- repo files: manifests committed at the repo root, which clients read straight from GitHub.

    python -m ismail.release --all                 build every target into dist/
    python -m ismail.release --target openai       one target
    python -m ismail.release --all --sync          also rewrite the committed repo files
    python -m ismail.release --all --check         fail when a committed repo file differs from the build

Builds are deterministic: fixed file order, fixed zip timestamps, LF line endings in text files, so two builds of
the same checkout give identical bytes. Each target validates its own output against its store's documented rules
and refuses to write when one breaks. To add a target: one entry in TARGETS (a build and a validate function) and
one test in tests/test_store_plugins.py.
"""
import argparse
import io
import json
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
TEXT_SUFFIXES = {'.md', '.json', '.py', '.txt', '.svg', '.yaml', '.yml', ''}
SEMVER = re.compile(r'^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$')


# ---------------------------------------------------------------- shared

def project_version(root=ROOT):
    """The [project] version in pyproject.toml (a regex, so Python 3.10 needs no tomllib)."""
    text = (root / 'pyproject.toml').read_text(encoding='utf-8')
    section = text.split('[project]', 1)[1].split('\n[', 1)[0]
    m = re.search(r'^version\s*=\s*"([^"]+)"', section, re.M)
    if not m:
        raise ValueError('no version in [project] of pyproject.toml')
    return m.group(1)


def load_meta(root=ROOT):
    meta = json.loads((root / 'packaging' / 'listing.json').read_text(encoding='utf-8'))
    meta['version'] = project_version(root)
    return meta


def dumps(obj):
    return (json.dumps(obj, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def read_bytes(path):
    data = path.read_bytes()
    if path.suffix.lower() in TEXT_SUFFIXES:
        data = data.replace(b'\r\n', b'\n')
    return data


def tree_files(root, rel, exclude=()):
    """{archive path: bytes} for every file under root/rel, skipping caches and the named top-level subfolders."""
    base = root / rel
    out = {}
    for p in sorted(base.rglob('*')):
        if not p.is_file():
            continue
        parts = p.relative_to(base).parts
        if parts[0] in exclude or '__pycache__' in parts or p.suffix == '.pyc':
            continue
        out[p.relative_to(root).as_posix()] = read_bytes(p)
    return out


def make_zip(files):
    """Zip bytes with a fixed entry order and timestamp, so equal inputs give equal bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, files[name], compresslevel=9)
    return buf.getvalue()


def read_zip(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {n: z.read(n) for n in z.namelist()}


def image_size(data):
    """(width, height, format) of a PNG, JPEG, WebP or SVG, or None when it cannot be read."""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        w, h = struct.unpack('>II', data[16:24])
        return w, h, 'png'
    if data.lstrip()[:4] in (b'<svg', b'<?xm'):
        m = re.search(rb'viewBox="\s*([-\d.]+)[ ,]+([-\d.]+)[ ,]+([\d.]+)[ ,]+([\d.]+)', data)
        return (float(m.group(3)), float(m.group(4)), 'svg') if m else None
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        return im.size[0], im.size[1], (im.format or '').lower()
    except Exception:
        return None


def check_icon(errors, label, path, files, lo=48, hi=4096, max_bytes=5 * 1024 * 1024):
    """OpenAI's icon rule: square, 48 to 4096 px a side, PNG/JPEG/WebP/SVG, at most 5 MiB, ./-prefixed."""
    if not isinstance(path, str) or not path.startswith('./'):
        errors.append(f'{label}: path must start with ./ ({path!r})')
        return
    data = files.get(path[2:])
    if data is None:
        errors.append(f'{label}: {path} is not in the package')
        return
    size = image_size(data)
    if size is None:
        errors.append(f'{label}: {path} is not a readable PNG, JPEG, WebP or SVG')
        return
    w, h, fmt = size
    if fmt not in ('png', 'jpeg', 'webp', 'svg'):
        errors.append(f'{label}: {path} is {fmt}, not PNG, JPEG, WebP or SVG')
    if w != h or w < lo or (fmt != 'svg' and w > hi):
        errors.append(f'{label}: {path} is {w}x{h}; it must be square, {lo} to {hi} px')
    if len(data) > max_bytes:
        errors.append(f'{label}: {path} is over 5 MiB')


def check_len(errors, label, value, limit, required=True):
    if value is None or value == '':
        if required:
            errors.append(f'{label} is required')
        return
    if not isinstance(value, str):
        errors.append(f'{label} must be a string')
    elif len(value) > limit:
        errors.append(f'{label} is {len(value)} characters; the limit is {limit}')


def skill_frontmatter(text):
    m = re.match(r'^---\n(.*?)\n---\n', text.replace('\r\n', '\n'), re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        k, sep, v = line.partition(':')
        if sep and not line.startswith((' ', '\t')):
            out[k.strip()] = v.strip()
    return out


def check_skills(errors, warnings, files):
    """Agent Skills rules (agentskills.io/specification): name matches its folder, description 1 to 1024."""
    skills = sorted(n for n in files if re.fullmatch(r'skills/[^/]+/SKILL\.md', n))
    if not skills:
        errors.append('no skills/<name>/SKILL.md in the package')
    for n in skills:
        fm = skill_frontmatter(files[n].decode('utf-8'))
        folder = n.split('/')[1]
        if fm.get('name') != folder:
            errors.append(f'{n}: frontmatter name {fm.get("name")!r} must match its folder {folder!r}')
        desc = fm.get('description', '')
        if not desc:
            errors.append(f'{n}: frontmatter description is required')
        elif len(desc) > 1024:
            errors.append(f'{n}: description is {len(desc)} characters; the Agent Skills spec caps it at 1024')


def server_entry(meta, **extra):
    s = meta['server']
    return {**extra, 'command': s['command'], 'args': list(s['args']), 'env': dict(s['env'])}


def local_command(server):
    """The command a client would run for a declared stdio server, with this interpreter in place of uvx.

    uvx resolves the package (a download) and then runs its `ismail` entry point; offline, the same entry point is
    `python -m ismail`. Anything else is returned as declared."""
    cmd, args = server['command'], list(server.get('args', []))
    if cmd == 'uvx' and 'ismail' in args:
        return [sys.executable, '-m', 'ismail', *args[args.index('ismail') + 1:]]
    if cmd == 'python':
        return [sys.executable, *args]
    return [cmd, *args]


# ---------------------------------------------------------------- targets

@dataclass
class Output:
    dist: dict = field(default_factory=dict)     # path under dist/<target>/ -> bytes
    repo: dict = field(default_factory=dict)     # path under the repo root -> bytes
    warnings: list = field(default_factory=list)


@dataclass
class Target:
    name: str
    summary: str
    launch: str
    build: Callable
    validate: Callable


# ---- OpenAI: ChatGPT and Codex share one plugin directory

OPENAI_SCHEMA = 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json'
OPENAI_TOP_KEYS = {'$schema', 'name', 'version', 'description', 'author', 'homepage', 'repository', 'license',
                   'keywords', 'extensions'}
OPENAI_FORBIDDEN_FILES = {'mcp.json', '.mcp.json', '.app.json', 'hooks.json'}
OPENAI_FORBIDDEN_KEYS = {'test_credentials', 'reviewer_instructions', 'clientSecret', 'apps', 'hooks'}
CODEX_INSTALL = {'AVAILABLE', 'INSTALLED_BY_DEFAULT', 'NOT_AVAILABLE'}
CODEX_AUTH = {'ON_INSTALL', 'ON_USE'}


def openai_zip_name(meta):
    return f"{meta['name']}-openai-skills-{meta['version']}.zip"


def openai_interface(meta, long_description=None):
    L = meta['openai']['listing']
    return {
        'displayName': L['displayName'],
        'shortDescription': L['shortDescription'],
        'longDescription': long_description or L['longDescription'],
        'developerName': L['developerName'],
        'category': L['category'],
        'capabilities': list(L['capabilities']),
        'websiteURL': L['websiteURL'],
        'defaultPrompt': list(L['defaultPrompt']),
        'logo': './assets/logo.png',
        'composerIcon': './assets/logo.png',
    }


def build_openai(meta, root=ROOT):
    o = meta['openai']
    out = Output()
    # A: the skills-only package for the OpenAI plugin directory (portal: Create plugin > Skills only > Upload ZIP).
    manifest = {
        '$schema': OPENAI_SCHEMA,
        'name': meta['name'],
        'version': meta['version'],
        'description': o['listing']['description'],
        'author': meta['author'],
        'homepage': meta['homepage'],
        'repository': meta['repository'],
        'license': meta['license'],
        'keywords': o['keywords'],
        'extensions': {'com.openai': {'interface': openai_interface(meta)}},
    }
    files = {'plugin.json': dumps(manifest), 'LICENSE': read_bytes(root / 'LICENSE'),
             'assets/logo.png': (root / meta['icon']).read_bytes()}
    files.update(tree_files(root, meta['skill'], exclude=set(o['skill_exclude'])))
    out.dist[openai_zip_name(meta)] = make_zip(files)

    # B: the Codex marketplace in this repo (`codex plugin marketplace add newsbubbles/ismail`), no review.
    cx = o['codex']
    icon = './' + meta['icon']
    codex_manifest = {
        'name': meta['name'],
        'version': meta['version'],
        'description': meta['marketplace_description'],
        'author': meta['author'],
        'homepage': meta['homepage'],
        'repository': meta['repository'],
        'license': meta['license'],
        'keywords': meta['keywords'],
        'skills': './skills/',
        'mcpServers': './.codex-plugin/mcp.json',
        'interface': {**openai_interface(meta, long_description=meta['marketplace_description']),
                      'logo': icon, 'composerIcon': icon},
    }
    mcp = {'mcpServers': {meta['server']['name']: server_entry(
        meta, type='stdio') | {'startup_timeout_sec': cx['startup_timeout_sec']}}}
    mk = cx['marketplace']
    marketplace = {
        'name': mk['name'],
        'interface': {'displayName': mk['displayName']},
        'plugins': [{'name': meta['name'], 'source': {'source': 'local', 'path': './'},
                     'policy': dict(mk['policy']), 'category': o['listing']['category']}],
    }
    out.repo['.codex-plugin/plugin.json'] = dumps(codex_manifest)
    out.repo['.codex-plugin/mcp.json'] = dumps(mcp)
    out.repo['.agents/plugins/marketplace.json'] = dumps(marketplace)
    return out


def find_keys(obj, keys, where=''):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys:
                hits.append(f'{where}{k}')
            hits += find_keys(v, keys, f'{where}{k}.')
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += find_keys(v, keys, f'{where}{i}.')
    return hits


def check_openai_interface(errors, I, label):
    """Listing limits from developers.openai.com/plugins/deploy/submission (read 2026-10-25)."""
    check_len(errors, f'{label}.displayName', I.get('displayName'), 30)
    check_len(errors, f'{label}.shortDescription', I.get('shortDescription'), 30)
    check_len(errors, f'{label}.longDescription', I.get('longDescription'), 4000)
    check_len(errors, f'{label}.developerName', I.get('developerName'), 80)
    if not I.get('category'):
        errors.append(f'{label}.category is required (a dashboard category)')
    caps = I.get('capabilities')
    if not isinstance(caps, list) or len(caps) > 20 or any(not isinstance(c, str) or len(c) > 120 for c in caps):
        errors.append(f'{label}.capabilities must be a list of at most 20 labels of at most 120 characters')
    prompts = I.get('defaultPrompt', [])
    if not isinstance(prompts, list) or len(prompts) > 3 or len(set(prompts)) != len(prompts):
        errors.append(f'{label}.defaultPrompt: at most 3, all different')
    for p in prompts if isinstance(prompts, list) else []:
        if len(p) > 128 or '@' in p:
            errors.append(f'{label}.defaultPrompt {p!r}: at most 128 characters and no @mentions')
    for k in ('websiteURL', 'privacyPolicyURL', 'termsOfServiceURL', 'supportURL'):
        v = I.get(k)
        if v is not None and (not v.startswith('https://') or '@' in v.split('/')[2]):
            errors.append(f'{label}.{k} must be an https URL without credentials')
    for k in ('brandColor', 'brandColorDark'):
        if k in I and not re.fullmatch(r'#[0-9A-Fa-f]{6}', I[k]):
            errors.append(f'{label}.{k} must be #RRGGBB')


def check_openai_name(errors, name):
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        errors.append(f'name {name!r}: at most 64 characters, lowercase letters, numbers and single hyphens')


def validate_openai(out, meta, root=ROOT):
    errors = []
    # A: the directory zip
    files = read_zip(out.dist[openai_zip_name(meta)])
    for n in files:
        parts = n.split('/')
        if n.startswith('/') or '..' in parts or '\\' in n:
            errors.append(f'zip path {n!r} is not a plain relative path')
        if parts[-1] in OPENAI_FORBIDDEN_FILES or 'hooks' in parts or parts[0] == '.codex-plugin':
            errors.append(f'zip has {n}: a skills-only package carries no MCP config, apps, hooks or overlay')
    m = json.loads(files['plugin.json'])
    extra = set(m) - OPENAI_TOP_KEYS
    if extra:
        errors.append(f'plugin.json keys outside the Agent Plugins 1.0.0 schema: {sorted(extra)}')
    if m.get('$schema') != OPENAI_SCHEMA:
        errors.append('plugin.json $schema must be ' + OPENAI_SCHEMA)
    check_openai_name(errors, m.get('name'))
    if not SEMVER.match(str(m.get('version', ''))):
        errors.append(f'version {m.get("version")!r} is not semver')
    check_len(errors, 'description', m.get('description'), 4000)
    author = m.get('author', {})
    if set(author) - {'name', 'email', 'url'}:
        errors.append('author takes only name, email and url')
    check_len(errors, 'author.name', author.get('name'), 120)
    ext = m.get('extensions', {}).get('com.openai')
    if not isinstance(ext, dict) or not isinstance(ext.get('interface'), dict):
        errors.append('plugin.json needs extensions["com.openai"].interface')
    else:
        I = ext['interface']
        check_openai_interface(errors, I, 'interface')
        check_icon(errors, 'interface.logo', I.get('logo'), files)
        check_icon(errors, 'interface.composerIcon', I.get('composerIcon'), files)
    for n, data in files.items():
        if n.endswith('.json'):
            for hit in find_keys(json.loads(data), OPENAI_FORBIDDEN_KEYS):
                errors.append(f'{n}: {hit} is not allowed in a submitted package')
    check_skills(errors, out.warnings, files)

    # B: the Codex marketplace files
    repo = {**out.repo}
    cm = json.loads(repo['.codex-plugin/plugin.json'])
    check_openai_name(errors, cm.get('name'))
    if not SEMVER.match(str(cm.get('version', ''))):
        errors.append('.codex-plugin version is not semver')
    check_len(errors, '.codex-plugin description', cm.get('description'), 4000)
    I = cm.get('interface', {})
    check_openai_interface(errors, I, '.codex-plugin interface')
    on_disk = {p.relative_to(root).as_posix(): p.read_bytes() for p in (root / 'assets').rglob('*') if p.is_file()}
    check_icon(errors, '.codex-plugin interface.logo', I.get('logo'), on_disk)
    check_icon(errors, '.codex-plugin interface.composerIcon', I.get('composerIcon'), on_disk)
    for k in ('skills', 'mcpServers'):
        v = cm.get(k, '')
        if not v.startswith('./') or not ((root / v[2:]).exists() or v[2:] in repo):
            errors.append(f'.codex-plugin {k} {v!r} must be a ./ path inside the repo')
    servers = json.loads(repo['.codex-plugin/mcp.json']).get('mcpServers', {})
    if len(servers) != 1:
        errors.append('.codex-plugin/mcp.json must declare exactly one server')
    for name, s in servers.items():
        if s.get('type') != 'stdio' or not isinstance(s.get('command'), str) or not isinstance(s.get('args'), list):
            errors.append(f'mcp server {name}: a stdio server needs type, command and args')
        if not isinstance(s.get('startup_timeout_sec'), (int, float)):
            errors.append(f'mcp server {name}: startup_timeout_sec must be a number')
    mk = json.loads(repo['.agents/plugins/marketplace.json'])
    if not mk.get('name') or not mk.get('interface', {}).get('displayName') or not mk.get('plugins'):
        errors.append('marketplace.json needs name, interface.displayName and plugins')
    for e in mk.get('plugins', []):
        src = e.get('source', {})
        if e.get('name') != cm.get('name'):
            errors.append('marketplace entry name must match the plugin name')
        if src.get('source') != 'local' or not str(src.get('path', '')).startswith('./') or '..' in src['path']:
            errors.append('marketplace entry source must be local and ./-relative')
        if e.get('policy', {}).get('installation') not in CODEX_INSTALL:
            errors.append(f'policy.installation must be one of {sorted(CODEX_INSTALL)}')
        if e.get('policy', {}).get('authentication') not in CODEX_AUTH:
            errors.append(f'policy.authentication must be one of {sorted(CODEX_AUTH)}')
        if not e.get('category'):
            errors.append('marketplace entry category is required')
    return errors


# ---- Claude Code: the plugin and its marketplace in this repo

CLAUDE_KNOWN_KEYS = {'$schema', 'name', 'displayName', 'version', 'description', 'author', 'homepage', 'repository',
                     'license', 'keywords', 'metadata', 'icon', 'documentationUrl', 'supportUrl',
                     'privacyPolicyUrl', 'termsOfServiceUrl', 'defaultEnabled', 'dependencies', 'settings',
                     'userConfig', 'types', 'channels', 'skills', 'commands', 'agents', 'hooks', 'mcpServers',
                     'lspServers', 'outputStyles', 'workflows', 'experimental'}
CLAUDE_RESERVED = {'claude', 'anthropic', 'anthropics', 'claude-code', 'claude-mods'}
CLAUDE_RESERVED_MARKETS = {
    'claude-code-marketplace', 'claude-code-plugins', 'claude-plugins-official', 'anthropic-marketplace',
    'anthropic-plugins', 'agent-skills', 'anthropic-agent-skills', 'life-sciences', 'knowledge-work-plugins',
    'claude-for-legal', 'claude-for-financial-services', 'financial-services-plugins', 'first-party-plugins',
    'claude-tag-plugins', 'claude-community', 'claude-plugins-community', 'healthcare',
    'anthropic-plugin-directory', 'claude-plugin-directory', 'inline', 'builtin', 'skills-dir', 'synced',
    'claude-plugin-test', 'npm', 'pip', 'uv', 'cargo', 'github', 'gh'}
CLAUDE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


def claude_zip_name(meta):
    return f"{meta['name']}-claude-code-{meta['version']}.zip"


def build_claude_code(meta, root=ROOT):
    c = meta['claude-code']
    out = Output()
    plugin = {
        'name': meta['name'],
        'displayName': c['displayName'],
        'version': meta['version'],
        'description': meta['marketplace_description'],
        'author': meta['author'],
        'homepage': meta['homepage'],
        'repository': meta['repository'],
        'license': meta['license'],
        'keywords': meta['keywords'],
        'mcpServers': {meta['server']['name']: server_entry(meta)},
    }
    mk = c['marketplace']
    marketplace = {
        'name': mk['name'],
        'description': mk['description'],
        'owner': mk['owner'],
        'plugins': [{'name': meta['name'], 'source': './', 'description': meta['short_description'],
                     'category': mk['category'], 'tags': mk['tags']}],
    }
    out.repo['.claude-plugin/plugin.json'] = dumps(plugin)
    out.repo['.claude-plugin/marketplace.json'] = dumps(marketplace)
    files = {'.claude-plugin/plugin.json': out.repo['.claude-plugin/plugin.json'],
             'LICENSE': read_bytes(root / 'LICENSE')}
    files.update(tree_files(root, meta['skill']))
    out.dist[claude_zip_name(meta)] = make_zip(files)
    return out


def validate_claude_code(out, meta, root=ROOT):
    """Rules from code.claude.com/docs/en/plugins-reference and plugins/marketplace-reference (read 2026-10-25)."""
    errors = []
    files = read_zip(out.dist[claude_zip_name(meta)])
    p = json.loads(out.repo['.claude-plugin/plugin.json'])
    if json.loads(files['.claude-plugin/plugin.json']) != p:
        errors.append('the zip and the repo carry different plugin.json files')
    name = p.get('name')
    if not isinstance(name, str) or not name or re.search(r'[\s@:/\\]', name):
        errors.append(f'plugin name {name!r}: non-empty, no spaces, @, : or path separators')
    elif not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        out.warnings.append(f'plugin name {name!r} is not kebab-case')
    low = (name or '').lower()
    if low in CLAUDE_RESERVED or low.startswith(('claude-', 'anthropic-', 'anthropics-', 'cc-plugin-')):
        errors.append(f'plugin name {name!r} is reserved')
    unknown = set(p) - CLAUDE_KNOWN_KEYS
    if unknown:
        out.warnings.append(f'plugin.json fields Claude Code strips: {sorted(unknown)}')
    for k in ('version', 'description'):
        if not p.get(k):
            out.warnings.append(f'plugin.json has no {k}')
    if not p.get('author', {}).get('name'):
        errors.append('author.name is required when author is set')
    if not str(p.get('homepage', '')).startswith('https://'):
        errors.append('homepage must parse as a URL')
    servers = p.get('mcpServers')
    if not isinstance(servers, dict) or not servers:
        errors.append('plugin.json declares no MCP server')
    for sname, s in (servers or {}).items():
        if not isinstance(s.get('command'), str) or not all(isinstance(a, str) for a in s.get('args', [])):
            errors.append(f'mcp server {sname}: stdio needs a command string and string args')
    m = json.loads(out.repo['.claude-plugin/marketplace.json'])
    mname = m.get('name', '')
    if not CLAUDE_ID.match(mname) or '..' in mname or mname.lower() in CLAUDE_RESERVED_MARKETS \
            or mname.lower().startswith('claudeai-'):
        errors.append(f'marketplace name {mname!r} is invalid or reserved')
    if not m.get('owner', {}).get('name'):
        errors.append('marketplace owner.name is required')
    if not m.get('description'):
        out.warnings.append('marketplace has no description')
    entries = m.get('plugins') or []
    if not entries:
        errors.append('marketplace has no plugins')
    for e in entries:
        src = e.get('source')
        if not CLAUDE_ID.match(e.get('name', '')):
            errors.append(f'marketplace entry name {e.get("name")!r} is invalid')
        if e.get('name') != name:
            errors.append('marketplace entry name must match plugin.json name')
        if not isinstance(src, str) or not (src == '.' or src.startswith('./')) or '..' in src:
            errors.append(f'marketplace entry source {src!r} must be a ./ path without ..')
        elif not (root / src).is_dir():
            errors.append(f'marketplace entry source {src!r} is not a folder')
    check_skills(errors, out.warnings, files)
    return errors


TARGETS = {t.name: t for t in [
    Target('openai',
           'ChatGPT and Codex (one OpenAI plugin directory): a skills-only zip for the directory, and a Codex '
           'marketplace in this repo that bundles the local server',
           'Codex launches `uvx ... ismail mcp` over stdio from .codex-plugin/mcp.json; the directory zip carries '
           'no server (public MCP servers there must be remote HTTPS)',
           build_openai, validate_openai),
    Target('claude-code',
           'Claude Code: the plugin and its marketplace in this repo, plus a zip of the plugin to share',
           'Claude Code launches `uvx ... ismail mcp` over stdio from .claude-plugin/plugin.json',
           build_claude_code, validate_claude_code),
]}


def build(name, root=ROOT, meta=None):
    """Build and validate one target. Returns (Output, errors)."""
    meta = meta or load_meta(root)
    t = TARGETS[name]
    out = t.build(meta, root)
    return out, t.validate(out, meta, root)


def repo_drift(out, root=ROOT):
    """Repo files whose committed bytes differ from the build."""
    return [p for p, data in sorted(out.repo.items())
            if not (root / p).is_file() or read_bytes(root / p) != data]


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m ismail.release', description=__doc__.split('\n\n')[0])
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument('--all', action='store_true', help='every target')
    which.add_argument('--target', choices=sorted(TARGETS), help='one target')
    which.add_argument('--list', action='store_true', help='list the targets and exit')
    ap.add_argument('--sync', action='store_true', help='rewrite the committed repo files from the build')
    ap.add_argument('--check', action='store_true', help='fail when a committed repo file differs from the build')
    ap.add_argument('--dist', default=str(ROOT / 'dist'), help='output folder (default: dist/ in the repo)')
    a = ap.parse_args(argv)
    if a.list:
        for t in TARGETS.values():
            print(f'{t.name}: {t.summary}\n  server: {t.launch}')
        return 0
    meta = load_meta()
    status = 0
    for name in (sorted(TARGETS) if a.all else [a.target]):
        out, errors = build(name, meta=meta)
        for w in out.warnings:
            print(f'{name}: warning: {w}', file=sys.stderr)
        if errors:
            for e in errors:
                print(f'{name}: error: {e}', file=sys.stderr)
            status = 1
            continue
        dest = Path(a.dist) / name
        dest.mkdir(parents=True, exist_ok=True)
        for rel, data in sorted(out.dist.items()):
            (dest / rel).write_bytes(data)
            print(f'{name}: wrote {dest / rel} ({len(data)} bytes)')
        if a.sync:
            for rel, data in sorted(out.repo.items()):
                (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
                (ROOT / rel).write_bytes(data)
                print(f'{name}: synced {rel}')
        elif a.check:
            for rel in repo_drift(out):
                print(f'{name}: {rel} differs from the build (run with --sync)', file=sys.stderr)
                status = 1
    return status


if __name__ == '__main__':
    sys.exit(main())
