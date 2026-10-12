"""python -m ismail.video <command> --song <song dir> ...

  init                      scaffold <song>/video (config, plan, cut list, an example shot, rigs/, assets/)
  rip <path>...             extract .dae models (files or folders) into video/build/x for the Blender kit
  sync                      song project -> video/build/events.json + features.npz (run after every song change)
  still <shot.py> <frame>   one frame -> video/build/look/<shot>_<frame>.png  (extra args after --, e.g. -- --top 0,60,300,20)
  posesheet <shot.py> [f,f] every character alone from four sides at its key frames + the clipping check -> build/look/posesheet_<shot>.png
  render <shot.py>...       full shot renders, one at a time, each in the machine's GPU slot (ismail.machine: it waits
                            in line, --wait 4h by default, while the GPU is busy or hot or a live set is on air);
                            refuses on clipping unless -- --allowclip
  contact <video> [n]       contact sheet of n frames of any video -> video/build/look/contact_<name>.png
  edit [-- --sheet a b n | -- --range a b]   runs video/cut.py (sheet = contact sheet of bars a..b-1)
  overlay <in.mp4> <out.mp4> --text "..."    a line of text on a copy of any video (no --song; `overlay -h` for the look)
"""
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time

from . import blender_exe, config, video_dir
from .. import machine

KIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'blender')
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
RENDER_WAIT = '4h'                 # a render queue is walk-away work: it outlasts a live set rather than give up


def _blender(song, shot, extra, log=None):
    vd = video_dir(song)
    # inside a render's slot the shot carries it ($ISMAIL_SLOT), so anything heavy it starts runs in that slot
    env = machine.child_env(env=dict(os.environ, ISMAIL_VIDEO_KIT=KIT, ISMAIL_VIDEO_DIR=vd))
    shot = shot if os.path.isabs(shot) else os.path.join(vd, 'shots', os.path.basename(shot))
    cmd = [blender_exe(song), '-b', '-P', shot, '--'] + list(extra)
    if log:
        with open(log, 'w') as f:
            return subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT).returncode
    r = subprocess.run(cmd, env=env, capture_output=True, text=True)
    noise = ('BlenderMCP', 'Server thread', 'socket', 'Blender quit', 'Fra:', 'Saved:', 'Time:', 'Deprecation', 'use_nodes', 'OBJ import')
    lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip() and not any(n in l for n in noise)]
    for line in lines:
        print(line)
    _blender.lines = lines
    return r.returncode


def cmd_posesheet(song, shot, frames, extra):
    """every character of a shot alone, from four sides, at its key frames (or the given ones), with the clipping
    check's hits written under the frame they happen on."""
    from PIL import Image, ImageDraw
    vd = video_dir(song)
    name = os.path.splitext(os.path.basename(shot))[0]
    pd = os.path.join(vd, 'build', 'look', 'pose')
    for f in glob.glob(os.path.join(pd, f'{name}__*.png')):
        os.replace(f, f + '.old')        # keep nothing stale in the sheet without deleting anything
    _blender(song, shot, ['--posesheet', frames or ''] + extra if frames else ['--posesheet'] + extra)
    clips = [l for l in _blender.lines if l.startswith('CLIP') and ' f' in l]
    files = sorted(glob.glob(os.path.join(pd, f'{name}__*.png')))
    rows = sorted({tuple(os.path.basename(f)[:-4].split('__')[1:2]) + (os.path.basename(f)[:-4].split('__')[2][:4],) for f in files})
    if not rows:
        print('no pose frames rendered')
        return
    S, H = 300, 44
    sheet = Image.new('RGB', (S * 4, (S + H) * len(rows)), (20, 20, 22))
    d = ImageDraw.Draw(sheet)
    for r, (who, fr) in enumerate(rows):
        for i in range(4):
            fp = os.path.join(pd, f'{name}__{who}__{fr}_{i}.png')
            if os.path.exists(fp):
                sheet.paste(Image.open(fp).convert('RGB').resize((S, S)), (i * S, r * (S + H)))
        hit = [l.split(':', 1)[1].strip() for l in clips if l.startswith(f'CLIP {who} f{int(fr)}:')]
        y = r * (S + H) + S + 4
        d.text((6, y), f'{who} frame {int(fr)}', fill=(230, 230, 230))
        d.text((6, y + 16), ('CLIP: ' + '; '.join(hit))[:190] if hit else 'clean', fill=(255, 90, 90) if hit else (120, 220, 120))
    out = os.path.join(vd, 'build', 'look', f'posesheet_{name}.png')
    sheet.save(out)
    print(f'posesheet -> {out}  ({len(clips)} clipping hits)')


def cmd_init(song):
    vd = video_dir(song)
    for d in ('shots', 'rigs', 'assets', 'build', 'renders/shots'):
        os.makedirs(os.path.join(vd, d), exist_ok=True)
    for root, _, files in os.walk(TEMPLATES):
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.join(vd, os.path.relpath(src, TEMPLATES))
            if not os.path.exists(dst):          # never overwrite the song's own files
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
    print('video project:', vd)
    for root, _, files in os.walk(vd):
        for f in files:
            print('  ', os.path.relpath(os.path.join(root, f), vd))


def cmd_rip(song, paths):
    from .dae import main as extract
    out = os.path.join(video_dir(song), 'build', 'x')
    os.makedirs(out, exist_ok=True)
    files = []
    for p in paths:
        files += glob.glob(os.path.join(p, '**', '*.dae'), recursive=True) if os.path.isdir(p) else [p]
    for f in files:
        stem = ''.join(ch for ch in os.path.splitext(os.path.basename(f))[0] if ch.isalnum())
        extract(f, os.path.join(out, stem))
    print(f'{len(files)} models -> {out}  (load with S.room(stem) / S.dae(stem, name); .obj files load directly with S.obj)')


def cmd_render(song, shots, extra, wait=RENDER_WAIT):
    """Each shot renders in the machine's GPU slot (ismail.machine), so the board's rules hold for it as for any
    Blender render: one GPU job machine-wide, the heat and disk floors, and no render while a live set is on air.
    It stands in line up to `wait` ('4h'; '0' refuses at once). A refusal stops the queue, says why
    and names the shots not rendered. -> 0, or 75 (try again later) when the machine refused."""
    vd = video_dir(song)
    os.makedirs(os.path.join(vd, 'build', 'logs'), exist_ok=True)
    wait_s = machine.duration_s(wait) or None          # '0': no line, refused at once if the slot is not free
    for i, s in enumerate(shots):
        name = os.path.splitext(os.path.basename(s))[0] + ''.join('_' + x.strip('-') for x in extra)
        log = os.path.join(vd, 'build', 'logs', name + '.log')
        try:
            with machine.slot('gpu', f'video render {os.path.basename(song)} {name}', wait=wait_s, disk_path=vd,
                              threads=None, cmd=[blender_exe(song), '-b', '-P', s]):
                t = time.time()
                code = _blender(song, s, extra, log)
        except machine.MachineBusy as e:
            left = [os.path.basename(x) for x in shots[i:]]
            print(f'{name}: not rendered, the machine refused the GPU slot: {e}', file=sys.stderr)
            print(f'not rendered: {" ".join(left)}. Run `python -m ismail.machine` to see the board, then render them '
                  f'again (--wait 12h stands in line longer; only the user lifts the on-air rule).', file=sys.stderr)
            return 75                                            # EX_TEMPFAIL, as `machine run`
        text = open(log, errors='replace').read().splitlines()
        out = next((l.split('RENDERED', 1)[1].strip() for l in text if l.startswith('RENDERED')), None)
        for l in text:
            if l.startswith(('CLIP', 'NOT RENDERED')):
                print('  ' + l)
        print(f'{name}: exit {code}, {time.time() - t:.0f}s -> {out or "NO OUTPUT, see " + log}', flush=True)
    return 0


def cmd_contact(song, video, n):
    out = os.path.join(video_dir(song), 'build', 'look', f'contact_{os.path.splitext(os.path.basename(video))[0]}.png')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = subprocess.run(['ffprobe', '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries',
                        'stream=nb_read_packets', '-of', 'csv=p=0', video], capture_output=True, text=True)
    total = int(r.stdout.strip() or 0)
    step = max(1, total // n)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', video, '-vf',
                    f'select=not(mod(n\\,{step})),scale=480:-1,tile=4x{(n + 3) // 4}', '-frames:v', '1', out])
    print(f'{video}: {total} frames -> {out}')


def main():
    ap = argparse.ArgumentParser(prog='python -m ismail.video', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command')
    ap.add_argument('args', nargs='*')
    ap.add_argument('--song', '-s', default='.', help='song folder (holds proj/ and the master wav)')
    ap.add_argument('--wait', default=RENDER_WAIT, help="render: how long each shot stands in line for the machine's "
                    "GPU slot (4h by default, 12h; 0 refuses at once)")
    argv = sys.argv[1:]
    if argv[:1] == ['overlay']:                    # its own options (the look), and no song
        from .overlay import main as overlay
        sys.exit(overlay(argv[1:]))
    extra = []
    if '--' in argv:
        i = argv.index('--')
        argv, extra = argv[:i], argv[i + 1:]
    a = ap.parse_intermixed_args(argv)
    song = os.path.abspath(a.song)
    c = a.command
    if c == 'init':
        cmd_init(song)
    elif c == 'rip':
        cmd_rip(song, a.args)
    elif c == 'sync':
        from .sync import run
        run(song)
    elif c == 'still':          # no slot: a still is seconds, fine beside a queued render
        _blender(song, a.args[0], ['--still', a.args[1]] + extra)
    elif c == 'posesheet':
        cmd_posesheet(song, a.args[0], a.args[1] if len(a.args) > 1 else None, extra)
    elif c == 'render':
        try:
            machine.duration_s(a.wait)
        except ValueError as e:
            ap.error(f'--wait: {e}')
        sys.exit(cmd_render(song, a.args, extra, a.wait))
    elif c == 'contact':
        cmd_contact(song, a.args[0], int(a.args[1]) if len(a.args) > 1 else 12)
    elif c == 'edit':
        subprocess.run([sys.executable, os.path.join(video_dir(song), 'cut.py')] + a.args + extra)
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == '__main__':
    main()
