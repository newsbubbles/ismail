"""Named-ops guard: a Claude Code PreToolUse hook that turns the off-path stage and live commands an agent reaches
for into the named op that does the same thing, before a permission prompt can throw the person out of VR.

It reads the hook's JSON on stdin ({"tool_name": ..., "tool_input": {...}, "cwd": ...}). A refusal exits 2 with
the reason on stderr (the agent reads it and uses the op); everything else exits 0 and prints nothing. It never
refuses `python -m ismail ...`, and any problem reading the input lets the command through. Stdlib only.

THE RULES (blocked form -> the named op to use instead):

  python -c / py -c / a python heredoc with ismail code -> python -m ismail <op> key=value (python -m ismail ops)
  a copy or move into a stage scenes folder              -> stage_asset_install(scene=, file=, kind=)
      (cp, mv, rsync, copy, move, xcopy, robocopy, Copy-Item, Move-Item, shutil in python -c; also any copy from
       AppData/Local/Temp/claude into a scenes folder)
  killing the stage or a live engine                     -> stage_restart / stage_stop / live_stop(project=)
      (Stop-Process, taskkill, kill, pkill aimed at python, port 8862, or a pid or port in the stage or live
       registry)
  writing world.json, scene.glb or manifest.json in a scenes folder (Write, Edit, MultiEdit, > redirect,
  Set-Content, Out-File, Add-Content, tee, sed -i)       -> stage_world(update=) / stage_asset_install
"""
import json
import os
import re
import shlex
import sys

GUARDED_FILES = {'world.json': 'world', 'scene.glb': 'asset', 'manifest.json': 'asset'}
COPY_VERBS = {'cp', 'mv', 'rsync', 'copy', 'move', 'xcopy', 'robocopy', 'copy-item', 'move-item', 'cpi', 'mi'}
KILL_VERBS = {'stop-process', 'spps', 'taskkill', 'kill', 'pkill', 'killall'}
WRITE_VERBS = {'set-content', 'out-file', 'add-content', 'sc', 'ac', 'tee', 'tee-object'}
PYTHONS = re.compile(r'^(python[0-9.]*|py|pythonw)(\.exe)?$')
SHELLS = {'powershell', 'powershell.exe', 'pwsh', 'pwsh.exe', 'cmd', 'cmd.exe', 'bash', 'bash.exe', 'sh', 'zsh'}
STAGE_PORT = 8862
TEMP_CLAUDE = 'appdata/local/temp/claude'

MSG = {
    'python_c': ('python -c with ismail code is refused: use the named op, `python -m ismail <op> key=value` '
                 '(for example `python -m ismail stage_status`; `python -m ismail ops` lists them, '
                 '`python -m ismail help <op>` explains one).'),
    'asset': ('copying a file into a stage scenes folder is refused: use stage_asset_install, for example '
              '`python -m ismail stage_asset_install scene=room file=D:/work/body.glb kind=actor` '
              '(kind is scene, manifest, actor or tree; the old file is kept in history/).'),
    'world': ('editing world.json by hand is refused: use stage_world with update=, for example '
              '`python -m ismail stage_world scene=room \'update={"floor": 0.1}\'` (only the keys given change; '
              'the old file is kept in history/).'),
    'kill_stage': ('stopping the stage server by process is refused: use `python -m ismail stage_restart` (it waits '
                   'while someone is in VR) or `python -m ismail stage_stop port=8862`.'),
    'kill_live': ('stopping a live engine by process is refused: use `python -m ismail live_stop project=<song>` '
                  '(it fades out first).'),
    'kill_python': ('killing python processes is refused (a stage server or a live engine may be one of them): use '
                    '`python -m ismail stage_restart`, `python -m ismail stage_stop port=<port>` or '
                    '`python -m ismail live_stop project=<song>`.'),
}


# ---- paths -------------------------------------------------------------------------------------------------------

def unify(p):
    """One spelling for a path, case kept: forward slashes, d:/ for /d/, /mnt/d/ and /cygdrive/d/."""
    p = (p or '').strip().strip('"\'').replace('\\', '/')
    m = re.match(r'^/(?:mnt/|cygdrive/)?([a-zA-Z])(/|$)', p)
    if m:
        p = f'{m.group(1)}:/' + p[m.end():]
    return p


def norm(p):
    """unify, in lower case (for comparing)."""
    return unify(p).lower()


def _registry(env, default):
    d = os.environ.get(env) or os.path.join(os.path.expanduser('~'), '.ismail', default)
    out = []
    try:
        names = [n for n in os.listdir(d) if n.endswith('.json')]
    except OSError:
        return out
    for n in names:
        try:
            with open(os.path.join(d, n), encoding='utf-8') as fh:
                out.append(json.load(fh))
        except (OSError, ValueError):
            pass
    return [r for r in out if isinstance(r, dict)]


_CACHE = {}


def stage_records():
    if 'stage' not in _CACHE:
        _CACHE['stage'] = _registry('ISMAIL_STAGE_REGISTRY', 'stage')
    return _CACHE['stage']


def live_records():
    if 'live' not in _CACHE:
        _CACHE['live'] = _registry('ISMAIL_LIVE_REGISTRY', 'live')
    return _CACHE['live']


def scene_dir_of(path, cwd='', src=''):
    """The scene a path is in, as a normalised folder, or None. A folder named scenes/<name> counts when it is a
    vr/scenes folder, under a running server's scenes, has a scene.glb on disk, or the source is a claude temp file."""
    raw = unify(path)
    n = raw.lower()
    for r in stage_records():                              # a running server's scenes folder, whatever its name
        root = norm(str(r.get('scenes', ''))).rstrip('/')
        mr = re.match(re.escape(root) + r'/([^/._][^/]*)', n) if root else None
        if mr:
            return n[:mr.end()]
    m = re.search(r'(^|/)scenes/([^/._][^/]*)', n)
    if not m:
        return None
    folder = n[:m.end()]
    if '/vr/scenes/' in '/' + folder:
        return folder
    try:
        p = raw[:m.end()]
        if cwd and not os.path.isabs(p):
            p = os.path.join(cwd, p)
        if os.path.isfile(os.path.join(p, 'scene.glb')):
            return folder
    except (OSError, ValueError):
        pass
    if TEMP_CLAUDE in norm(src):
        return folder
    return None


def guarded_file(path, cwd=''):
    base = norm(path).rstrip('/').rsplit('/', 1)[-1]
    if base in GUARDED_FILES and scene_dir_of(path, cwd):
        return GUARDED_FILES[base]
    return None


# ---- splitting a command line ------------------------------------------------------------------------------------

def split_statements(cmd):
    """Statements (on ; && || newline) of pipelines (on |), quote aware: [[part, part], ...]."""
    stmts, pipe, cur, q, i = [], [], [], None, 0
    while i < len(cmd):
        c = cmd[i]
        if q:
            cur.append(c)
            if c == q:
                q = None
        elif c in '"\'':
            q = c
            cur.append(c)
        elif c in ';\n' or cmd.startswith('&&', i) or cmd.startswith('||', i):
            pipe.append(''.join(cur))
            stmts.append(pipe)
            pipe, cur = [], []
            if c not in ';\n':
                i += 1
        elif c == '|':
            pipe.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    pipe.append(''.join(cur))
    stmts.append(pipe)
    return [[p.strip() for p in s if p.strip()] for s in stmts if any(p.strip() for p in s)]


def tokens(part):
    try:
        lx = shlex.shlex(part, posix=False)
        lx.whitespace_split = True
        lx.commenters = ''
        toks = list(lx)
    except ValueError:
        toks = part.split()
    out = []
    for t in toks:
        if len(t) >= 2 and t[0] == t[-1] and t[0] in '"\'':
            t = t[1:-1]
        out.append(t)
    return out


def verb(t):
    t = norm(t).rsplit('/', 1)[-1]
    return t[:-4] if t.endswith('.exe') else t


def strip_prefix(toks):
    """Drop env assignments, `&`, sudo and the like in front of the command."""
    i = 0
    while i < len(toks) and (re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', toks[i]) or toks[i] in ('&', '.', 'sudo', 'env',
                                                                                            'exec', 'time', '{', '(')):
        i += 1
    return toks[i:]


def inner_shell(toks):
    """The command a shell wrapper runs (powershell -Command x, cmd /c x, bash -c x), or None."""
    if not toks or verb(toks[0]) not in SHELLS:
        return None
    for i, t in enumerate(toks[1:], 1):
        if t.lower() in ('-c', '/c', '/k', '-command', '-lc', '-ic') or (t.startswith('-') and len(t) > 2
                                                                           and '-command'.startswith(t.lower())):
            return ' '.join(toks[i + 1:]) if len(toks) > i + 2 else (toks[i + 1] if i + 1 < len(toks) else '')
    return None


# ---- the checks --------------------------------------------------------------------------------------------------

def check_python(toks, cwd):
    if not toks or not PYTHONS.match(verb(toks[0])):
        return None
    args = toks[1:]
    # python -m ismail ... is the named way: always let it through
    skip = False
    for i, t in enumerate(args):
        if skip:
            skip = False
            continue
        if t in ('-X', '-W'):
            skip = True
            continue
        if t == '-m':
            return None
        if t == '-c' and i + 1 < len(args):
            code = ' '.join(args[i + 1:])
            return check_code(code, cwd)
        if not t.startswith('-'):
            return None
    return None


def check_code(code, cwd):
    if re.search(r'\bismail\b', code):
        return 'python_c'
    if re.search(r'shutil|copy|replace|rename|write|open\(', code):
        for lit in re.findall(r'''r?["']([^"']+)["']''', code):
            if scene_dir_of(lit, cwd, code):
                base = norm(lit).rstrip('/').rsplit('/', 1)[-1]
                return 'world' if base == 'world.json' else 'asset'
    return None


def positional(args, skip_value=()):
    out, i = [], 0
    while i < len(args):
        t = args[i]
        if t.lower() in skip_value:
            i += 2
            continue
        if t.startswith('-') or re.match(r'^/[a-zA-Z]{1,6}(:\S*)?$', t):  # cmd style /Y /E switches
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def check_copy(toks, cwd):
    if not toks:
        return None
    v = verb(toks[0])
    if v not in COPY_VERBS:
        return None
    args = toks[1:]
    dests, srcs = [], []
    low = [a.lower() for a in args]
    if v in ('copy-item', 'move-item', 'cpi', 'mi'):
        for i, a in enumerate(low):
            if a in ('-destination', '-dest', '-d') or (a.startswith('-dest') and len(a) > 2):
                if i + 1 < len(args):
                    dests.append(args[i + 1])
            if a in ('-path', '-literalpath', '-lp') and i + 1 < len(args):
                srcs.append(args[i + 1])
        pos = positional(args, ('-destination', '-dest', '-path', '-literalpath', '-lp', '-filter', '-include',
                                '-exclude'))
        if not dests and pos:
            dests.append(pos[-1])
            pos = pos[:-1]
        srcs += pos
    elif v == 'robocopy':
        pos = positional(args)
        if len(pos) >= 2:
            srcs, dests = [pos[0]], [pos[1]]
    else:
        for i, a in enumerate(low):
            if a in ('-t', '--target-directory') and i + 1 < len(args):
                dests.append(args[i + 1])
            elif a.startswith('--target-directory='):
                dests.append(args[i][len('--target-directory='):])
        pos = positional(args, ('-t', '--target-directory'))
        if not dests and len(pos) >= 2:
            dests.append(pos[-1])
            srcs = pos[:-1]
        else:
            srcs = pos
    src = ' '.join(srcs)
    for d in dests:
        if scene_dir_of(d, cwd, src):
            return 'world' if norm(d).rstrip('/').endswith('world.json') else 'asset'
    return None


def check_write(toks, part, cwd):
    """A shell write into a guarded scene file: > / >> redirects, Set-Content and friends, tee, sed -i."""
    for m in re.finditer(r'>>?\s*("[^"]+"|\'[^\']+\'|\S+)', part):
        k = guarded_file(m.group(1), cwd)
        if k:
            return k
    if not toks:
        return None
    v = verb(toks[0])
    if v in WRITE_VERBS or (v == 'sed' and any(t.startswith('-i') for t in toks[1:])) or v in ('truncate', 'touch'):
        for t in toks[1:]:
            k = guarded_file(t, cwd)
            if k:
                return k
    return None


def _ports_pids():
    stage_ports = {STAGE_PORT} | {int(r['port']) for r in stage_records() if str(r.get('port', '')).isdigit()}
    stage_pids = {int(r['pid']) for r in stage_records() if str(r.get('pid', '')).isdigit()}
    live_ports = {int(r['port']) for r in live_records() if str(r.get('port', '')).isdigit()}
    live_pids = {int(r['pid']) for r in live_records() if str(r.get('pid', '')).isdigit()}
    return stage_ports, stage_pids, live_ports, live_pids


def check_kill(stmt):
    """A kill verb in command position, aimed at python, ismail, a stage or live port, or a registry pid."""
    flat = []
    for part in stmt:
        toks = strip_prefix(tokens(part))
        flat.append(toks)
    hit = False
    for toks in flat:
        for i, t in enumerate(toks):
            if verb(t) in KILL_VERBS and (i == 0 or toks[i - 1].lower() in ('xargs', 'foreach-object', '%', 'foreach',
                                                                                 'sudo', '&', '{', '(')):
                hit = True
    if not hit:
        return None
    text = ' '.join(' '.join(t) for t in flat)
    words = [w.strip('"\'(){},;') for w in re.split(r'[\s,]+', text)]
    lw = [w.lower() for w in words]
    stage_ports, stage_pids, live_ports, live_pids = _ports_pids()
    nums = {int(w) for w in words if w.isdigit()}
    if nums & stage_pids or nums & stage_ports:
        return 'kill_stage'
    if nums & live_pids or nums & live_ports:
        return 'kill_live'
    if any(PYTHONS.match(w.rsplit('/', 1)[-1]) or w.startswith('python') for w in lw if w) or 'ismail' in text.lower():
        return 'kill_python'
    return None


def check_command(cmd, cwd, depth=0):
    if depth > 3:
        return None
    # a python heredoc or here-string piped to python with ismail code in it
    if re.search(r'\b(python[0-9.]*|py)(\.exe)?\b[^\n|;&]*(<<|-\s*$|-\s*<)', cmd, re.M) or \
            re.search(r"@['\"][\s\S]*?['\"]@\s*\|\s*(python[0-9.]*|py)\b", cmd):
        if re.search(r'^\s*(import\s+ismail|from\s+ismail)', cmd, re.M):
            return 'python_c'
    for stmt in split_statements(cmd):
        k = check_kill(stmt)
        if k:
            return k
        for part in stmt:
            toks = strip_prefix(tokens(part))
            if not toks:
                continue
            inner = inner_shell(toks)
            if inner is not None:
                k = check_command(inner, cwd, depth + 1)
                if k:
                    return k
                continue
            for check in (lambda: check_python(toks, cwd), lambda: check_copy(toks, cwd),
                          lambda: check_write(toks, part, cwd)):
                k = check()
                if k:
                    return k
    return None


def decide(data):
    """The MSG key of the rule a tool call breaks, or None."""
    tool = data.get('tool_name') or ''
    ti = data.get('tool_input') or {}
    cwd = data.get('cwd') or ''
    if tool in ('Bash', 'PowerShell'):
        cmd = ti.get('command') or ''
        if re.match(r'^\s*(python[0-9.]*|py)(\.exe)?\s+-m\s+ismail(\s|$)', cmd) and not re.search(r'[;&|\n]', cmd):
            return None
        return check_command(cmd, cwd)
    if tool in ('Write', 'Edit', 'MultiEdit'):
        return guarded_file(ti.get('file_path') or '', cwd)
    return None


def main():
    try:
        data = json.loads(sys.stdin.read() or '{}')
        key = decide(data) if isinstance(data, dict) else None
        msg = MSG[key] if key else None
    except Exception:                                      # noqa: BLE001  (never block on a guard bug)
        return 0
    if not msg:
        return 0
    sys.stderr.write('ismail named-ops guard: ' + msg + '\n')
    return 2


if __name__ == '__main__':
    try:
        code = main()
    except Exception:                                      # noqa: BLE001  (a guard bug lets the call through)
        code = 0
    sys.exit(code)
