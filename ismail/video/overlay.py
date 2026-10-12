"""A line of text drawn on a video (a credit, a title, a link): off by default, with no text built in.

    python -m ismail.video overlay in.mp4 out.mp4 --text "made with ismail"     # any existing video
    C.overlay('made with ismail')                                               # in a cut list (ismail.video.edit)
    "overlay": {"text": "made with ismail", "opacity": 0.4}                     # or in video/config.json

Both ways draw with one ffmpeg drawtext filter built by drawtext() below, so they look the same. The look is set
for a 1080 px frame and scales with the frame's short side (its height in a landscape video, its width in a
vertical one), so the same numbers read the same at 720p, 4K or 1080x1920. It is drawn last: after the grade, the
grain and anything else in the picture.
"""
import argparse
import os
import re
import subprocess
import sys

REF = 1080                          # the look's numbers are pixels on a frame whose short side is this
POS = ('bottom', 'top', 'center')
LOOK = {'pos': 'bottom', 'size': 30.0, 'opacity': 0.5, 'shadow': 0.45, 'font': None, 'margin': 42.0, 'y': None,
        'color': 'white'}
WAIT = '30m'                        # how long the CLI stands in line for the machine's cpu slot


class OverlayError(ValueError):
    """a refusal: bad options, a missing input, an output that exists, no font."""


def find_font():
    """-> a .ttf path that exists here, or None. DejaVu Sans Mono ships with matplotlib (an ismail dependency), so
    the default looks the same on every machine; then a system mono or sans."""
    cands = []
    try:
        import matplotlib
        cands.append(os.path.join(matplotlib.get_data_path(), 'fonts', 'ttf', 'DejaVuSansMono.ttf'))
    except ImportError:
        pass
    windir = os.environ.get('WINDIR', r'C:\Windows')
    cands += [os.path.join(windir, 'Fonts', f) for f in ('consola.ttf', 'CascadiaMono.ttf', 'arial.ttf', 'segoeui.ttf')]
    cands += ['/System/Library/Fonts/Menlo.ttc', '/System/Library/Fonts/Supplemental/Arial.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', '/usr/share/fonts/TTF/DejaVuSansMono.ttf',
              '/usr/share/fonts/dejavu/DejaVuSansMono.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
              '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf']
    return next((c for c in cands if os.path.isfile(c)), None)


def _value(s):
    """one drawtext option value, escaped for both levels ffmpeg parses: the option (backslash before \\ ' :) and
    the filtergraph (the whole value in single quotes, a ' inside as '\\''). A Windows font path comes out as
    'C\\:/Windows/Fonts/x.ttf'."""
    s = re.sub(r"([\\':])", r'\\\1', str(s))
    return "'" + s.replace("'", "'\\''") + "'"


def look(**kw):
    """the look's options, checked, with the defaults filled in (the font resolved to a file that exists)."""
    bad = set(kw) - set(LOOK) - {'text'}
    if bad:
        raise OverlayError(f"unknown overlay option(s) {', '.join(sorted(bad))}; the options are text, "
                           f"{', '.join(LOOK)}")
    o = dict(LOOK)
    o.update({k: v for k, v in kw.items() if v is not None})
    if o['pos'] not in POS:
        raise OverlayError(f"pos is one of {', '.join(POS)}, not {o['pos']!r}")
    for k in ('size', 'opacity', 'shadow', 'margin'):
        try:
            o[k] = float(o[k])
        except (TypeError, ValueError):
            raise OverlayError(f'{k} is a number, not {o[k]!r}')
    if o['size'] <= 0:
        raise OverlayError(f"size is above 0, not {o['size']}")
    for k in ('opacity', 'shadow'):
        if not 0 <= o[k] <= 1:
            raise OverlayError(f'{k} is from 0 to 1, not {o[k]}')
    if o['y'] is not None:
        try:
            o['y'] = float(o['y'])
        except (TypeError, ValueError):
            raise OverlayError(f"y is a pixel row, not {o['y']!r}")
    if not re.fullmatch(r'#?[A-Za-z0-9_]+', str(o['color'])):
        raise OverlayError(f"color is an ffmpeg colour name or #rrggbb, not {o['color']!r}")
    if o['font'] is None:
        o['font'] = find_font()
        if o['font'] is None:
            raise OverlayError('no font found on this machine: pass a .ttf file (--font <path> on the command line, '
                               'font=<path> in C.overlay or "font" in the config\'s "overlay")')
    elif not os.path.isfile(o['font']):
        raise OverlayError(f"no font file {o['font']}")
    return o


def drawtext(text, w, h, **kw):
    """-> the ffmpeg drawtext filter that draws `text` on a w x h frame with the look in kw (see LOOK):
    pos bottom|top|center, size (px at 1080), opacity and shadow (0 to 1; shadow 0 draws none), font (a .ttf path;
    default find_font()), margin (px at 1080, from the edge pos names), y (a pixel row of this frame that pos
    measures from instead of the frame's edge: bottom puts the text's bottom margin px above it, top margin px
    below it, center centres it on it), color. Sizes scale with min(w, h) / 1080; y does not."""
    if not text or not str(text).strip():
        raise OverlayError('the overlay needs some text')
    o = look(**kw)
    k = min(w, h) / REF
    m = round(o['margin'] * k)
    edge = {'bottom': 'h', 'top': '0', 'center': 'h/2'}[o['pos']] if o['y'] is None else f"{round(o['y'])}"
    y = {'bottom': f'{edge}-th-{m}', 'top': f'{edge}+{m}', 'center': f'{edge}-th/2'}[o['pos']]
    f = [f"fontfile={_value(o['font'].replace(os.sep, '/'))}", f'text={_value(text)}', 'expansion=none',
         f"fontsize={max(1, round(o['size'] * k))}", f"fontcolor={o['color']}@{o['opacity']:.2f}"]
    if o['shadow'] > 0:
        d = max(1, round(2 * k))
        f += [f"shadowcolor=black@{o['shadow']:.2f}", f'shadowx={d}', f'shadowy={d}']
    f += ['x=(w-tw)/2', f'y={y}']
    return 'drawtext=' + ':'.join(f)


def probe_size(path):
    """-> (width, height) of a video's first video stream."""
    r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                        '-of', 'csv=p=0:s=x', path], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().splitlines()[0].split('x')[:2]
        return int(w), int(h)
    except (IndexError, ValueError):
        raise OverlayError(f'ffprobe found no video stream in {path}: {r.stderr.strip()[-300:]}')


def check_paths(src, out, force=False):
    """refuse before any work: a missing input, an output that is the input, an output that exists (unless force)."""
    if not os.path.isfile(src):
        raise OverlayError(f'no input video {src}')
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(out)) or \
            (os.path.exists(out) and os.path.samefile(src, out)):
        raise OverlayError('the output is the input: the input is never overwritten, name another output file')
    if os.path.exists(out) and not force:
        raise OverlayError(f'{out} exists: name another output file, or pass --force to replace it')


def overlay_file(src, out, text, force=False, **kw):
    """draw `text` on the video src -> out (re-encoded H.264 crf 18, the audio copied as it is). Never writes the
    input; refuses an output that exists unless force. Writes <out>.part first, so a failed run leaves out as it
    was. Takes no machine slot (the CLI does). -> out"""
    check_paths(src, out, force)
    w, h = probe_size(src)
    vf = drawtext(text, w, h, **kw)
    root, ext = os.path.splitext(out)
    tmp = root + '.part' + (ext or '.mp4')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cmd = ['ffmpeg', '-v', 'error', '-y', '-i', src, '-map', '0:v:0', '-map', '0:a?', '-vf', vf, '-c:v', 'libx264',
           '-preset', 'slow', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-map_metadata', '0',
           '-movflags', '+faststart', tmp]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise OverlayError('ffmpeg is not on PATH: install ffmpeg (with drawtext, which most builds have)')
    if r.returncode != 0 or not os.path.isfile(tmp):
        if os.path.isfile(tmp):
            os.remove(tmp)               # this run's own partial file, never the output or the input
        raise RuntimeError(f'ffmpeg failed on {src}: {r.stderr.strip()[-600:]}')
    os.replace(tmp, out)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='python -m ismail.video overlay',
        description='Draw a line of text on a copy of a video (a credit, a title, a link), centred, with a soft '
                    'shadow. Sizes are pixels on a 1080 px frame and scale with the short side of the video. The '
                    'video is re-encoded (H.264 crf 18) and the audio copied as it is. The input is never '
                    'overwritten. Runs in the machine\'s cpu slot.')
    ap.add_argument('src', metavar='in.mp4', help='the video to draw on (never changed)')
    ap.add_argument('out', metavar='out.mp4', help='the new video (refused if it exists, unless --force)')
    ap.add_argument('--text', required=True, help='the text, drawn as written (no %%{} expansion)')
    ap.add_argument('--pos', choices=POS, default=LOOK['pos'], help='where it sits (default bottom)')
    ap.add_argument('--size', type=float, default=LOOK['size'], help='font size, px at 1080 (default 30)')
    ap.add_argument('--opacity', type=float, default=LOOK['opacity'], help='text opacity, 0 to 1 (default 0.5)')
    ap.add_argument('--shadow', type=float, default=LOOK['shadow'],
                    help='shadow opacity, 0 to 1; 0 draws none (default 0.45)')
    ap.add_argument('--font', help='a .ttf or .otf file (default: DejaVu Sans Mono from matplotlib, else a system '
                                   'mono or sans)')
    ap.add_argument('--margin', type=float, default=LOOK['margin'],
                    help='px at 1080 from the edge (or from --y) to the text (default 42)')
    ap.add_argument('--y', type=float, help='a pixel row of the video that --pos measures from instead of the '
                                            'frame edge, e.g. the bottom of a footage band in a vertical layout '
                                            '(not scaled)')
    ap.add_argument('--color', default=LOOK['color'], help='an ffmpeg colour name or #rrggbb (default white)')
    ap.add_argument('--force', action='store_true', help='replace out.mp4 if it exists (never the input)')
    ap.add_argument('--wait', default=WAIT, help="how long to stand in line for the machine's cpu slot "
                                                 "(default 30m; 0 refuses at once when it is busy)")
    a = ap.parse_args(argv)
    from .. import machine
    try:
        wait_s = machine.duration_s(a.wait) or None
    except ValueError as e:
        ap.error(f'--wait: {e}')
    kw = dict(pos=a.pos, size=a.size, opacity=a.opacity, shadow=a.shadow, font=a.font, margin=a.margin, y=a.y,
              color=a.color)
    try:
        check_paths(a.src, a.out, a.force)
        drawtext(a.text, REF, REF, **kw)           # every option checked before standing in line
        with machine.slot('cpu', f'video overlay {os.path.basename(a.out)}', wait=wait_s, mem_gb=1.0,
                          disk_path=os.path.dirname(os.path.abspath(a.out))):
            overlay_file(a.src, a.out, a.text, force=a.force, **kw)
    except OverlayError as e:
        print(f'overlay: {e}', file=sys.stderr)
        return 2
    except machine.MachineBusy as e:
        print(f'overlay: not run, the machine refused the cpu slot: {e}', file=sys.stderr)
        return 75
    except RuntimeError as e:
        print(f'overlay: {e}', file=sys.stderr)
        return 1
    print(f'overlay -> {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
