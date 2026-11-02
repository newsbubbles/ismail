"""The person's side of the hub, typed in a terminal (an agent never sees what is typed here).

  python -m ismail.hub token <place>              paste the bot's token (hidden as you type)
  python -m ismail.hub link <person> <account>    e.g. link nate discord:123456789012345678
  python -m ismail.hub unlink <account>
  python -m ismail.hub pair <person>              a one-time code to send to the bot as `pair <code>`
  python -m ismail.hub people
"""
import getpass
import sys

from . import config as C, people as P


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if len(a) == 2 and a[0] == 'token':
        if a[1] not in C.load_places():
            print(f"no place {a[1]} yet: an agent adds it with hub_place first")
            return 2
        tok = getpass.getpass(f"{a[1]} bot token (hidden): ").strip()
        if not tok:
            print('nothing saved')
            return 1
        C.save_keys(a[1], {'token': tok})
        print(f"saved to {C.keys_path(a[1])}; the hub picks it up within a minute")
    elif len(a) == 3 and a[0] == 'link':
        P.link(a[1], a[2])
        print(f"{a[2]} is {a[1]} now")
    elif len(a) == 2 and a[0] == 'unlink':
        print(f"unlinked from {', '.join(P.unlink(a[1])) or 'nobody'}")
    elif len(a) == 2 and a[0] == 'pair':
        print(f"send this to the bot within {P.CODE_MIN} min:  pair {P.new_code(a[1])}")
    elif a == ['people']:
        for name, p in sorted(P.load()['people'].items()):
            print(f"{name} ({p.get('role')}): {', '.join(p.get('accounts', [])) or 'no accounts'}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
