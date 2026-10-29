"""The person's side: logins and stream keys, typed in a terminal.

  python -m ismail.stream login <venue>
  python -m ismail.stream key <venue>
  python -m ismail.stream venues
"""
import sys

from . import auth, config as C


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) == 2 and a[0] == 'login':
        auth.login(a[1])
    elif len(a) == 2 and a[0] == 'key':
        auth.set_key(a[1])
    elif a == ['venues']:
        for name, v in sorted(C.load_venues().items()):
            print(f"{name}: {v['platform']} {v.get('channel') or ''}  keys: {C.key_summary(name, v['platform'])}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
