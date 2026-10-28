"""The event a hook runs for, as a JSON file the hook can open (stage hooks and phone hooks).

A hook command (a shell string or an argv list) names the file with {event_file}; ismail writes the event there
before it starts the command and puts the file's path in place of {event_file}. A command without it runs exactly
as it did. The files are kept in <folder>/hook_events, the newest KEEP of them.

The STAGE_* / PHONE_* environment variables the hooks got before 0.4.3 are still set until 0.5 (ledger:M383).
"""
import json
import os
import shlex
import subprocess
import time
from pathlib import Path

KEEP = 200
TOKEN = '{event_file}'


def write(folder, event):
    """Write one event to <folder>/hook_events/<time>_<pid>_<n>.json and return the path; older files past KEEP go."""
    d = Path(folder) / 'hook_events'
    d.mkdir(parents=True, exist_ok=True)
    write.n = getattr(write, 'n', 0) + 1
    p = d / f'{time.strftime("%Y%m%d_%H%M%S")}_{os.getpid()}_{write.n:06d}.json'
    p.write_text(json.dumps(event, ensure_ascii=False, indent=1), encoding='utf-8')
    for old in sorted(d.glob("*.json"), key=lambda f: (f.stat().st_mtime, f.name))[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return p


def expand(cmd, path):
    """The command with {event_file} replaced by the event file's path (quoted in a shell string)."""
    if isinstance(cmd, str):
        quoted = subprocess.list2cmdline([str(path)]) if os.name == 'nt' else shlex.quote(str(path))
        return cmd.replace(TOKEN, quoted)
    return [str(a).replace(TOKEN, str(path)) for a in cmd]


def wants(cmds):
    """True when a command names {event_file} (so the event file is only written when a hook reads it)."""
    return any(TOKEN in (c if isinstance(c, str) else ' '.join(map(str, c))) for c in cmds)
