"""CLI: python -m ismail -p <project> <op> [json-args | @file.json | key=value ...]

  python -m ismail guide                     read this first: workflow, conventions, which tool for which question
  python -m ismail ops                       list ops
  python -m ismail help <op>                 op signature + description
  python -m ismail -p song notes_read '{"track": "bass", "bars": [1, 4]}'
  python -m ismail -p song render bars=[1,8] stems=true
  python -m ismail -p song batch @edits.json      (file holds {"ops": [...]} or a bare list)
  python -m ismail mcp                       run the MCP server (stdio)

Installed from PyPI, `ismail` is the same command: `ismail ops`, `ismail -p song render`, `ismail mcp`.
"""
import codecs
import inspect
import json
import os
import sys

from .api import OPS, OpError


def _json_escape(err):
    """A character the console can't take goes out as a JSON escape (\\u266f), so a JSON answer still parses."""
    out = []
    for c in err.object[err.start:err.end]:
        n = ord(c)
        units = [n] if n < 0x10000 else [0xd800 + ((n - 0x10000) >> 10), 0xdc00 + ((n - 0x10000) & 0x3ff)]
        out += [f'\\u{u:04x}' for u in units]
    return ''.join(out), err.end


codecs.register_error('ismail_json_escape', _json_escape)


def console_safe(*streams):
    """On a console that isn't UTF-8 (Windows' cp1252), write plain ASCII with JSON escapes: one sharp sign in an
    inbox row used to turn a whole answer into a 'charmap' ERROR, and every reader can decode ASCII."""
    for s in streams:
        enc = (getattr(s, 'encoding', None) or '').lower().replace('-', '')
        if enc != 'utf8' and hasattr(s, 'reconfigure'):
            s.reconfigure(encoding='ascii', errors='ismail_json_escape')


def parse_args(args):
    if not args:
        return {}
    if len(args) == 1 and args[0].startswith('@'):
        with open(args[0][1:], encoding='utf8') as f:
            v = json.load(f)
        return {'ops': v} if isinstance(v, list) else v
    if len(args) == 1 and args[0].lstrip().startswith('{'):
        return json.loads(args[0])
    if any(a.lstrip().startswith('{') for a in args):
        raise ValueError("pass either one JSON object or key=value pairs, not both; e.g. "
                         "instrument_fit '{\"target\": \"sound:x\", \"params\": {...}}' or target=sound:x params='{...}'")
    kw = {}
    for a in args:
        k, _, v = a.partition('=')
        try:
            kw[k] = json.loads(v)
        except json.JSONDecodeError:
            kw[k] = v
    return kw


def main(argv):
    project = os.environ.get('ISMAIL_PROJECT', '.')
    if len(argv) >= 2 and argv[0] == '-p':
        project, argv = argv[1], argv[2:]
    if not argv or argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    if argv[0] == 'mcp':
        from .mcp_server import main as serve
        serve()
        return 0
    console_safe(sys.stdout, sys.stderr)                 # after mcp: its stdio is the protocol's own
    if argv[0] == 'ops':
        for name, fn in OPS.items():
            print(f"{name:<18} {(fn.__doc__ or '').strip().splitlines()[0]}")
        return 0
    if argv[0] == 'help':
        if len(argv) < 2 or argv[1] not in OPS:
            print(f"ERROR help needs an op name; run 'python -m ismail ops' (start with 'python -m ismail guide')",
                  file=sys.stderr)
            return 2
        fn = OPS[argv[1]]
        print(f"{argv[1]}{inspect.signature(fn)}\n{inspect.getdoc(fn)}")
        return 0
    name = argv[0]
    try:
        kw = parse_args(argv[1:])
    except (ValueError, OSError) as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    if name not in OPS:
        print(f"ERROR unknown op {name!r}; run 'python -m ismail ops'", file=sys.stderr)
        return 2
    project = kw.pop('project', project)
    takes_project = next(iter(inspect.signature(OPS[name]).parameters), None) == 'project'   # stage_* take scene
    try:
        print(OPS[name](project, **kw) if takes_project else OPS[name](**kw))
        return 0
    except (OpError, ValueError) as e:  # analysis/instrument/fx errors are ValueErrors with guidance
        print(f"ERROR {e}", file=sys.stderr)
        return 1
    except TypeError as e:
        print(f"ERROR bad arguments for {name}: {e}\n{name}{inspect.signature(OPS[name])}", file=sys.stderr)
        return 1


def cli():
    sys.exit(main(sys.argv[1:]))


if __name__ == '__main__':
    cli()
