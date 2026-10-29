"""The playbook (the ismail skill, skills/ismail/) for an agent that has ismail without a clone: pip, uvx or a
server-only client. A clone reads skills/ismail/ itself; a build copies the same files into the package as
ismail/_playbook/ (setup.py), so both read one text.

    python -m ismail.playbook                    # SKILL.md
    python -m ismail.playbook live               # one reference page (references/live.md)
    python -m ismail.playbook export <folder>    # the skill as a folder an agent loads: <folder>/ismail/SKILL.md ...

The files a build copies: SKILL.md, references/*.md, examples/*, hooks/* (not evals/, which tests the skill)."""
import os
import shutil
import sys

PKG = os.path.dirname(os.path.abspath(__file__))
SHIPPED = ('SKILL.md', 'references/*.md', 'examples/*', 'hooks/*')


def root():
    """The skill folder: skills/ismail/ of a checkout, else ismail/_playbook/ of an installed package, else None."""
    from .handoffs import HERE, is_checkout
    for d, ok in ((os.path.join(HERE, 'skills', 'ismail'), is_checkout(HERE)), (os.path.join(PKG, '_playbook'), True)):
        if ok and os.path.isfile(os.path.join(d, 'SKILL.md')):
            return d
    return None


def pages(d=None):
    """The reference pages by name (references/<name>.md), sorted."""
    d = d or root()
    ref = os.path.join(d, 'references') if d else None
    return sorted(f[:-3] for f in os.listdir(ref) if f.endswith('.md')) if ref and os.path.isdir(ref) else []


def read(page=None):
    """SKILL.md, or one reference page by name ('live', 'references/live.md' and 'live.md' all work). Raises
    LookupError naming the pages when there is no such page, and when this install has no playbook."""
    d = root()
    if d is None:
        raise LookupError("this ismail has no playbook: a build copies skills/ismail/ into the package, and this one "
                          "was built without it. Read it on GitHub: https://github.com/newsbubbles/ismail/blob/main/"
                          "skills/ismail/SKILL.md")
    if not page:
        path = os.path.join(d, 'SKILL.md')
    else:
        name = os.path.basename(str(page).replace('\\', '/'))
        name = name[:-3] if name.endswith('.md') else name
        path = os.path.join(d, 'references', name + '.md')
        if not os.path.isfile(path):
            raise LookupError(f"no playbook page {page!r}. The pages: {', '.join(pages(d))}")
    with open(path, encoding='utf8') as f:
        return f.read()


def export(folder):
    """Copy the playbook to <folder>/ismail/ (SKILL.md and its subfolders), the shape an agent loads a skill from
    (.claude/skills, .agents/skills, ...). Files already there are replaced; nothing else in the folder is touched.
    Returns the skill folder."""
    import glob
    src = root()
    if src is None:
        raise LookupError("this ismail has no playbook to export")
    dst = os.path.join(os.path.abspath(os.path.expanduser(folder)), 'ismail')
    for pat in SHIPPED:
        for f in glob.glob(os.path.join(src, pat)):
            if os.path.isfile(f):
                out = os.path.join(dst, os.path.relpath(f, src))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copyfile(f, out)
    return dst


def main(argv=None):
    a = sys.argv[1:] if argv is None else argv
    try:
        if a[:1] == ['export']:
            if len(a) != 2:
                print("usage: python -m ismail.playbook export <folder>", file=sys.stderr)
                return 2
            print(f"the playbook is in {export(a[1])}")
            return 0
        text = read(a[0] if a else None)
    except LookupError as e:
        print(e, file=sys.stderr)
        return 1
    sys.stdout.reconfigure(encoding='utf-8')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
