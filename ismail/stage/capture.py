"""Frame-locked capture of the stage on the PC: a scene, a camera and a song time span in, a video (and PNGs if
asked) out, every frame at t0 + n / fps however long it takes to draw (page/capture.js has the page side).

Film, 2026-10-10: the whole video is shot in the stage look, so the stage is the renderer; captures run while the
person is out of the headset and never touch the stage they use. A capture runs in its own process (stage_capture
starts `python -m ismail.stage.capture <job.json>`):
  - its own stage server, in this process, on a free port and not in the registry (agents never find it); it serves
    the same scenes read-only: every write the page makes is answered "not saved" and no live command reaches it
  - a headless browser (Edge or Chrome) on ?scene=<scene>&capture=1, muted, at the capture size
  - ffmpeg fed the frames as they arrive (no PNGs on disk unless keep_frames), with the song's audio when given
  - the GPU slot from the machine governor for the whole run
Files: <scene>/captures/<job>/job.json, status.json (progress, read by stage_capture_status), log.txt, capture.mp4,
frames/f_00000.png (keep_frames).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

BROWSERS = [r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/microsoft-edge',
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
STALL_S = 300                         # no frame for this long: the page is stuck, the capture stops


def browser():
    """A Chromium browser to run headless: $ISMAIL_BROWSER, else Edge or Chrome where they install, else on PATH."""
    for p in [os.environ.get('ISMAIL_BROWSER'), *BROWSERS]:
        if p and Path(p).is_file():
            return p
    for n in ('msedge', 'chrome', 'google-chrome', 'chromium'):
        if shutil.which(n):
            return shutil.which(n)
    return None


def ffmpeg():
    return os.environ.get('ISMAIL_FFMPEG') or shutil.which('ffmpeg')


def write_json(path, obj):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(obj, indent=1), encoding='utf-8')
    tmp.replace(path)


class Capture:
    """What the capture's server calls (server.CAPTURE): the job for the page, each frame, the page's status and log."""

    def __init__(self, job, d, encoder):
        self.job, self.d, self.encoder = job, Path(d), encoder
        self.n = 0                                  # the next frame expected
        self.frames = int(round((job['t1'] - job['t0']) * job['fps']))
        self.last = time.time()
        self.page = {}                              # the page's last status: rendering, done, failed
        self.errors = []
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.frames_dir = self.d / 'frames' if job.get('keep_frames') else None
        if self.frames_dir:
            self.frames_dir.mkdir(parents=True, exist_ok=True)

    def state(self, **kw):
        s = {'job': self.job['id'], 'scene': self.job['scene'], 'frame': self.n, 'frames': self.frames,
             'elapsed_s': round(time.time() - self.t0, 1), **kw}
        if self.n and self.frames:
            per = (time.time() - self.t0) / self.n
            s['fps_drawn'] = round(1 / per, 2) if per else None
            s['eta_s'] = round(per * (self.frames - self.n))
        if self.errors:
            s['page_errors'] = self.errors[:10]
        write_json(self.d / 'status.json', s)
        return s

    def frame(self, n, png):
        with self.lock:
            if n != self.n:
                raise ValueError(f'frame {n} came where frame {self.n} was due')
            if not png.startswith(b'\x89PNG'):
                raise ValueError(f'frame {n} is not a PNG')
            if self.encoder:
                self.encoder.stdin.write(png)
            if self.frames_dir:
                (self.frames_dir / f'f_{n:05d}.png').write_bytes(png)
            self.n += 1
            self.last = time.time()
            if self.n % 15 == 0 or self.n == self.frames:
                self.state(state='rendering')
        return {'ok': True, 'n': n}

    def status(self, j):
        self.page = j if isinstance(j, dict) else {}
        self.last = time.time()
        for e in self.page.get('errors') or []:
            self.errors.append(str(e)[:300])
        return {'ok': True}

    def log(self, j):
        ents = [e for e in (j.get('entries') or []) if isinstance(e, dict)][:200]
        with open(self.d / 'log.txt', 'a', encoding='utf-8') as fh:
            for e in ents:
                if e.get('level') != 'beat':
                    fh.write(f"{time.strftime('%H:%M:%S')} {e.get('level')}: {str(e.get('msg'))[:500]}\n")
                if e.get('level') == 'error' and len(self.errors) < 50:
                    self.errors.append(str(e.get('msg'))[:300])
        return {'ok': True}


def _encoder(job, out):
    ff = ffmpeg()
    if not ff:
        raise RuntimeError('no ffmpeg (put it on PATH or set ISMAIL_FFMPEG), or capture with video=False and keep_frames')
    args = [ff, '-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', str(job['fps']), '-c:v', 'png', '-i', '-']
    if job.get('audio'):
        args += ['-ss', f"{job['t0']:.4f}", '-t', f"{job['t1'] - job['t0']:.4f}", '-i', job['audio']]
    args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', str(job.get('crf', 16)), '-preset', 'medium',
             '-r', str(job['fps'])]
    if job.get('audio'):
        args += ['-c:a', 'aac', '-b:a', '256k', '-shortest']
    args.append(str(out))
    return subprocess.Popen(args, stdin=subprocess.PIPE, stderr=open(Path(out).with_suffix('.ffmpeg.txt'), 'w'))


def _kill(p):
    if p is None or p.poll() is not None:
        return
    if os.name == 'nt':                       # the browser's helper processes go with it
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True)
    else:
        p.kill()


def run(job_path):
    from . import server
    from .. import machine
    job_path = Path(job_path)
    job = json.loads(job_path.read_text(encoding='utf-8'))
    d = job_path.parent
    W, H = job['size']
    out = d / 'capture.mp4' if job.get('video', True) else None
    cap = Capture(job, d, None)
    cap.state(state='waiting for the GPU')
    tmp = Path(tempfile.mkdtemp(prefix='ismail_capture_'))
    srv = page = enc = None
    try:
        with machine.slot('gpu', f"stage capture {job['scene']} {job['id']}", wait=job.get('wait_s', 1800),
                          disk_path=str(d), threads=None):
            enc = cap.encoder = _encoder(job, out) if out else None
            os.environ['ISMAIL_STAGE_REGISTRY'] = str(tmp / 'registry')   # never registered: agents cannot find it
            server.configure(job['scenes'], state=str(tmp / 'state'))
            server.CAPTURE = cap
            srv = server.Server(('127.0.0.1', 0), server.Handler)
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            b = browser()
            if not b:
                raise RuntimeError('no Edge or Chrome to draw with (set ISMAIL_BROWSER to one)')
            url = f"http://127.0.0.1:{port}/?scene={job['scene']}&capture=1"
            page = subprocess.Popen([b, '--headless=new', '--mute-audio', f'--window-size={W},{H}',
                                     f'--user-data-dir={tmp / "browser"}', '--no-first-run', '--no-default-browser-check',
                                     '--disable-extensions', '--ignore-gpu-blocklist', '--enable-gpu-rasterization',
                                     '--disable-background-timer-throttling', '--disable-renderer-backgrounding',
                                     url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            cap.last = time.time()
            cap.state(state='loading the room', url=url)
            while True:
                time.sleep(0.5)
                ps = cap.page.get('state')
                if ps in ('done', 'failed'):
                    break
                if page.poll() is not None:
                    raise RuntimeError(f'the browser closed (exit {page.returncode}) at frame {cap.n}')
                if time.time() - cap.last > max(STALL_S, job.get('settle_s', 180) + 30):
                    raise RuntimeError(f'no frame for {STALL_S} s at frame {cap.n}: the page is stuck (log.txt)')
            if ps == 'failed':
                raise RuntimeError('the page: ' + str(cap.page.get('error')))
            if cap.n != cap.frames:
                raise RuntimeError(f'{cap.n} of {cap.frames} frames came')
            if enc:
                enc.stdin.close()
                if enc.wait(timeout=600) != 0:
                    raise RuntimeError(f'ffmpeg failed: {Path(out).with_suffix(".ffmpeg.txt")}')
            cap.state(state='done', video=str(out) if out else None,
                      frames_dir=str(cap.frames_dir) if cap.frames_dir else None,
                      seconds=round(cap.frames / job['fps'], 3), fps=job['fps'], size=job['size'])
    except Exception as e:                               # noqa: BLE001  (the reason goes to status.json)
        cap.state(state='failed', error=str(e))
        if enc and enc.poll() is None:
            try:
                enc.stdin.close()
            except OSError:
                pass
            enc.kill()
    finally:
        _kill(page)
        if srv:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)          # the run's own temp folder (browser profile, bundle)


if __name__ == '__main__':
    run(sys.argv[1])
