"""The CLI quick start in docs/reference.md, run as written, reads its own bass line back and renders at a normal
level (ledger:M214, songs/_qa F5: the old line's sixteenth-note E3 came back as D#3 +42c under acid_bass's 60 ms
glide, with a second-part warning, and the render was flagged QUIET at -24.2 LUFS)."""
import os
import re
import shlex
import shutil

from ismail.__main__ import main
from ismail.notation import parse_notes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def quick_start():
    """The `-p songs/demo` lines of the quick start block, as argument lists after the project."""
    with open(os.path.join(ROOT, 'docs', 'reference.md'), encoding='utf8') as f:
        doc = f.read()
    block = re.search(r'## Quick start \(CLI\).*?```bash\n(.*?)```', doc, re.S).group(1)
    steps = []
    for line in block.splitlines():
        if line.startswith('python -m ismail -p songs/demo '):
            steps.append(shlex.split(line)[5:])
    return steps


def test_the_quick_start_reads_back_what_it_wrote(tmp_path, capsys):
    steps = quick_start()
    assert [s[0] for s in steps] == ['project_new', 'track_add', 'notes_write', 'render', 'analyze_melody']
    root = str(tmp_path / 'demo')
    said = {}
    for op, *args in steps:
        if op == 'render' and not shutil.which('ffmpeg'):
            args = [a for a in args if not a.startswith('mp3=')]      # the mp3 is for listening; the wav is read
        assert main(['-p', root, op] + args) == 0, capsys.readouterr().err
        said[op] = capsys.readouterr().out
    lufs = float(re.search(r'master: (-?[\d.]+) LUFS', said['render']).group(1))
    assert 'QUIET' not in said['render'] and lufs > -20, said['render']
    written = next(s for s in steps if s[0] == 'notes_write')[1]
    block = parse_notes(re.search(r'"notes": "(.*?)"', written).group(1))
    bars = [int(b) for b in re.search(r'bars=\[(\d+),(\d+)\]', ' '.join(steps[-1])).groups()]
    want = [(s + 4 * k, p, d) for k in range(bars[1] - bars[0] + 1) for s, p, d, _ in block]
    read = '\n'.join(ln for ln in said['analyze_melody'].splitlines() if not ln.startswith('#'))
    got = [(s, p, d) for s, p, d, _ in parse_notes(read)]
    assert got == want, said['analyze_melody']
    assert 'WARNING' not in said['analyze_melody']
    assert ' # ' not in read                                      # no note read off by a cents comment
