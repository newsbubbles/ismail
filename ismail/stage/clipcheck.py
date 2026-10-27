"""The clip check's table (stage_clip_check): a clip-check capture's 'probe:' lines (page/capture.js clipProbe) read
back into one line per person, PASS or FAIL.

Why (the 2026-10-26 patterns review): clipping reached the user two windows running, arms through bodies on 10-22 and
on 10-23 "main character is clipping through the stage to about his knees" (a speaker marked by an empty stood on the
hall floor, 0.45 m under the stage top). Each line of the capture's log.txt holds every person's clip list that frame:
[limb, with, cm] for an arm limb (upper_l, fore_r, hand_l ...) in a body, with 'self' or the other person, and
[foot_l, 'ground', cm] for a foot inside what it stands on; sink {l, r} cm and gms (the ground check's cost, ms).
"""
import json

CLIP_CM = 2.0                     # the page's CLIP_M: 2 cm or more inside counts
ARM = ('upper_', 'fore_', 'hand_')
LABEL = {'self': 'arm in own body:', 'other': 'arm in another:', 'foot': 'foot in the ground:'}


def probe_lines(text):
    """The clip-check frames of a log.txt: each 'probe:' line holding 'clipcheck', parsed (others are skipped)."""
    out = []
    for ln in text.splitlines():
        i = ln.find(' probe: ')
        if i < 0:
            continue
        try:
            j = json.loads(ln[i + 8:])
        except ValueError:
            continue
        if isinstance(j, dict) and isinstance(j.get('clipcheck'), dict):
            out.append(j)
    return out


def summarize(frames, people=None):
    """Per person: frames with a body, frames with an arm limb in their own body and in another's (deepest cm, which
    limb, when), frames with a foot sunk (deepest cm, which foot, when), the ground check's mean cost, and PASS or FAIL.
    people: who, in that order (default: everyone, as they appear)."""
    rows = {}
    for p in people or []:
        rows[p] = None
    for fi, fr in enumerate(frames):
        t = fr.get('t')
        for p, c in fr['clipcheck'].items():
            if people and p not in rows:
                continue
            r = rows.get(p) or {'person': p, 'frames': 0, 'none': None, 'self': [0, 0.0, None], 'other': [0, 0.0, None],
                                'foot': [0, 0.0, None], 'gms': [], 'playing': False}
            rows[p] = r
            if not isinstance(c, dict) or c.get('none'):
                r['none'] = (c or {}).get('none') if isinstance(c, dict) else 'no reading'
                continue
            r['frames'] += 1
            r['playing'] = r['playing'] or bool(c.get('playing'))
            if c.get('gms') is not None:
                r['gms'].append((fi, float(c['gms'])))
            got = {'self': 0.0, 'other': 0.0, 'foot': 0.0}
            what = {}
            for e in c.get('clip') or []:
                if not isinstance(e, (list, tuple)) or len(e) < 3:
                    continue
                limb, with_, cm = str(e[0]), str(e[1]), float(e[2])
                if cm < CLIP_CM:
                    continue
                k = 'foot' if with_ == 'ground' else 'self' if with_ == 'self' else 'other' if limb.startswith(ARM) else None
                if k and cm > got[k]:
                    got[k] = cm
                    what[k] = limb if k != 'other' else f'{limb} in {with_}'
            for k, cm in got.items():
                if cm <= 0:
                    continue
                acc = r[k]
                acc[0] += 1
                if cm > acc[1]:
                    acc[1], acc[2] = cm, f'{what[k]} at {t:g} s' if t is not None else what[k]
    out = []
    for p, r in rows.items():
        if r is None:
            r = {'person': p, 'frames': 0, 'none': 'not in the capture', 'self': [0, 0.0, None], 'other': [0, 0.0, None],
                 'foot': [0, 0.0, None], 'gms': [], 'playing': False}
        bad = r['self'][0] or r['other'][0] or r['foot'][0]
        r['verdict'] = 'FAIL' if bad else 'PASS' if r['frames'] else 'NO BODY'
        out.append(r)
    return out


def _cm(acc):
    return f'{acc[1]:.1f}' if acc[0] else '-'


def table(rows, head=''):
    """The rows as plain text: a head line, one line per person, the worst of each failing line, the check's cost."""
    cols = ['person', 'frames', 'arm>=2cm self', 'deepest cm', 'arm>=2cm other', 'deepest cm', 'foot sunk>=2cm',
            'deepest cm', 'result']
    lines = [[r['person'], str(r['frames']), str(r['self'][0]), _cm(r['self']), str(r['other'][0]), _cm(r['other']),
              str(r['foot'][0]), _cm(r['foot']), r['verdict']] for r in rows]
    w = [max(len(c), *(len(x[i]) for x in lines)) if lines else len(c) for i, c in enumerate(cols)]
    fmt = lambda xs: '  '.join(x.ljust(w[i]) for i, x in enumerate(xs)).rstrip()   # noqa: E731
    fails = sum(r['verdict'] == 'FAIL' for r in rows)
    verdict = f'FAIL ({fails} of {len(rows)})' if fails else 'PASS' if any(r['verdict'] == 'PASS' for r in rows) else 'nothing measured'
    out = [f'{head}{verdict}', fmt(cols), *(fmt(x) for x in lines)]
    for r in rows:
        worst = [f'{LABEL[k]} {r[k][2]}, {r[k][1]:.1f} cm' for k in ('self', 'other', 'foot') if r[k][0]]
        if worst:
            out.append(f'  {r["person"]}: deepest ' + '; '.join(worst))
        if r['none']:
            out.append(f'  {r["person"]}: {r["none"]}')
    # the first frame builds each mesh's column grid (page/groundgrid.js), so it is told apart; the capture's clock
    # counts in 0.1 ms steps, so a frame reads 0 or 0.1 and the mean says more than the median
    ms = [x for r in rows for fi, x in r['gms'] if fi > 0]
    first = [x for r in rows for fi, x in r['gms'] if fi == 0]
    if ms:
        out.append(f'the ground check: {sum(ms) / len(ms):.3f} ms a body a frame (mean), {max(ms):.3f} at most'
                   + (f'; the first frame {max(first):.1f} ms at most (it builds the grids)' if first else '') + ', on this PC')
    elif first:
        out.append(f'the ground check: the first frame {max(first):.1f} ms at most (it builds the grids), on this PC')
    return '\n'.join(out)
