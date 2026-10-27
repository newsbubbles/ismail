"""The stream's output: the set's sound and a picture, encoded once, copied to every venue that streams.

  engine master (local http) -> feeder (on a clock; silence across engine restarts) -> encoder (ffmpeg: the picture
  drawn from the sound, text lines over it, H.264 + AAC, MPEG-TS) -> fan-out -> one pusher per venue
  (ffmpeg -c copy -> rtmp(s)://... or a local .flv), each restarted on its own when its site drops.

The encoder runs below normal priority so the live mixer keeps its cores. A change of picture (stream_visual)
restarts the encoder and every pusher: a 2-3 s blip on every site, so gather picture changes into one call. Text
lines (stream_text, and the chat line when chat_overlay is on) change live with no blip."""
import collections
import http.client
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import config as C
from .. import settings

SR = 44100
BLOCK = 2048
FLAGS = (0x08000000 | 0x00004000) if os.name == 'nt' else 0      # no console window, below-normal priority
MODES = ('cqt', 'spectrum', 'waves', 'scope', 'page', 'none')
PAGE_TITLE = 'ismail visual'
SLOTS = ('title', 'now', 'chat', 'note')
DEFAULT_VISUAL = {'mode': 'cqt', 'width': 1280, 'height': 720, 'fps': 30, 'background': '#0b0d12',
                  'colors': ['#5ec8ff', '#ff6ec7'], 'image': None, 'window': None, 'area': [0.0, 0.18, 1.0, 0.64],
                  'text_color': '#ffffff', 'font': None, 'font_size': 30, 'chat_overlay': True, 'video_kbps': 4500,
                  'audio_kbps': 160, 'encoder': 'auto', 'capture': 'auto'}
CAPTURES = ('auto', 'wgc', 'gdi')


def ffmpeg():
    return settings.get('ffmpeg')


def live_engines():
    d = Path(settings.get('live.registry'))
    out = {}
    for f in d.glob('*.json'):
        try:
            r = json.loads(f.read_text(encoding='utf8'))
            out[int(r['port'])] = r
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return out


def _esc(path):
    """A path inside an ffmpeg filter argument: forward slashes, the drive colon escaped."""
    return str(path).replace('\\', '/').replace(':', r'\:').replace("'", r"\'")


def _hex(c, default='#ffffff'):
    c = str(c or default).strip()
    if not re.fullmatch(r'#?[0-9a-fA-F]{6}', c):
        raise ValueError(f'colour {c!r}: give #rrggbb')
    return '0x' + c.lstrip('#')


def _font():
    for f in ('C:/Windows/Fonts/segoeuib.ttf', 'C:/Windows/Fonts/arialbd.ttf', '/System/Library/Fonts/Helvetica.ttc',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        if os.path.exists(f):
            return f
    return None


def has_nvenc():
    try:
        out = subprocess.run([ffmpeg(), '-nostdin', '-hide_banner', '-encoders'], capture_output=True, text=True, timeout=15,
                             creationflags=FLAGS & 0x08000000).stdout
        return 'h264_nvenc' in out
    except (OSError, subprocess.SubprocessError):
        return False


_GFX = None


def has_gfxcapture():
    """ffmpeg's Windows Graphics Capture source (ffmpeg 8): a window drawn by the GPU comes through, covered or not."""
    global _GFX
    if _GFX is None:
        try:
            out = subprocess.run([ffmpeg(), '-nostdin', '-hide_banner', '-filters'], capture_output=True, text=True, timeout=15,
                                 creationflags=FLAGS & 0x08000000).stdout
            _GFX = os.name == 'nt' and ' gfxcapture ' in out
        except (OSError, subprocess.SubprocessError, TypeError):
            _GFX = False
    return _GFX


def window_content_box(title):
    """[x, y, w, h] of a Chrome or Edge window's web content inside its client area (an --app window draws its own
    title bar there), for the capture's crop. None off Windows, for another kind of window, or if it is not open."""
    if os.name != 'nt' or not title:
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass
    h = u.FindWindowW(None, title)
    if not h:
        return None
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(c, _):
        b = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(c, b, 64)
        if b.value == 'Chrome_RenderWidgetHostHWND':
            found.append(c)
        return True
    u.EnumChildWindows(h, proc(cb), 0)
    if not found:
        return None
    r = wintypes.RECT()
    u.GetWindowRect(found[0], ctypes.byref(r))
    pt = wintypes.POINT(r.left, r.top)
    u.ScreenToClient(h, ctypes.byref(pt))
    return [max(0, pt.x), max(0, pt.y), r.right - r.left, r.bottom - r.top]


def title_regex(title):
    """An exact match for a window title as gfxcapture's regex, with no character the filter graph would read. A run
    of non-ASCII characters becomes '.+': the regex sees UTF-8 bytes, and Edge's own titles hold a zero-width space
    ("Microsoft​ Edge"), which one '.' never matched."""
    t = re.sub(r'[^\x00-\x7f]+', '\x00', title)
    return '^' + re.sub(r'[^A-Za-z0-9 _\x00-]', '.', t).replace('\x00', '.+') + '$'


def fit_window(title, W, H, view, timeout=20.0):
    """Wait for the page's window, size it so the page draws exactly W x H, and return where that canvas sits in
    the window's client area, [x, y, w, h] for the capture's crop (an app window draws its own title bar inside
    its client area). view() is the page's own report of its canvas size. None off Windows or if it never shows."""
    if os.name != 'nt':
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)          # device pixels, as the capture and the page count
    except (AttributeError, OSError):
        pass
    end = time.time() + timeout
    h = 0
    while time.time() < end and not (h and view()):
        h = u.FindWindowW(None, title)
        time.sleep(0.25)
    if not h or not view():
        return None
    for _ in range(3):                                          # resize, then let the page report its new size
        wr, cr = wintypes.RECT(), wintypes.RECT()
        u.GetWindowRect(h, ctypes.byref(wr))
        u.GetClientRect(h, ctypes.byref(cr))
        pw, ph = view()
        if (pw, ph) == (W, H):
            break
        u.MoveWindow(h, wr.left, wr.top, wr.right - wr.left + W - pw, wr.bottom - wr.top + H - ph, True)
        time.sleep(0.6)
    u.GetClientRect(h, ctypes.byref(cr))
    pw, ph = view()
    return [max(0, (cr.right - pw) // 2), max(0, cr.bottom - ph), min(pw, cr.right), min(ph, cr.bottom)]


def build_encoder_args(vis, text_dir, preview, pcm_url, nvenc):
    """The encoder's ffmpeg command for a visual config (pure, so tests check it)."""
    W, H, fps = int(vis['width']), int(vis['height']), int(vis['fps'])
    ax, ay, aw, ah = (float(x) for x in vis['area'])
    vw, vh = max(16, int(W * aw) // 2 * 2), max(16, int(H * ah) // 2 * 2)
    vx, vy = int(W * ax), int(H * ay)
    c1 = _hex(vis['colors'][0]) if vis.get('colors') else '0xffffff'
    c2 = _hex(vis['colors'][1]) if len(vis.get('colors') or []) > 1 else c1
    args = [ffmpeg(), '-hide_banner', '-loglevel', 'warning', '-nostats',
            '-thread_queue_size', '1024', '-f', 's16le', '-ar', str(SR), '-ac', '2', '-i', pcm_url]
    window = PAGE_TITLE if vis['mode'] == 'page' else vis.get('window')
    if window:
        crop = 'crop={2}:{3}:{0}:{1},'.format(*vis['crop']) if vis.get('crop') else ''   # the page's own canvas
        fit = f'{crop}scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,fps={fps}[bg]'
        if vis.get('capture') == 'wgc':          # Windows Graphics Capture: GPU-drawn and covered windows come through
            bg = (f"gfxcapture=window_title='{title_regex(window)}':capture_cursor=0:max_framerate={fps},"
                  f"hwdownload,format=bgra,{fit}")
        else:
            args += ['-thread_queue_size', '64', '-f', 'gdigrab', '-framerate', str(fps), '-draw_mouse', '0',
                     '-i', f"title={window}"]
            bg = f'[1:v]{fit}'
    elif vis.get('image'):
        args += ['-loop', '1', '-framerate', str(fps), '-i', str(vis['image'])]
        bg = (f'[1:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},format=yuv420p[bg]')
    else:
        bg = f'color=c={_hex(vis["background"])}:s={W}x{H}:r={fps}[bg]'
    mode = vis['mode']
    graph = [bg, '[0:a]asplit=2[aenc][avis]']
    if mode == 'cqt':
        graph.append(f'[avis]showcqt=s={vw}x{vh}:r={fps}:bar_g=2:sono_g=4:bar_v=9:sono_v=bar_v*0.8:axis=0:'
                     f'csp=bt709,format=yuva420p,colorkey=0x000000:0.08:0.1[vis]')
    elif mode == 'spectrum':
        r, g, b = (int(c1[i:i + 2], 16) for i in (2, 4, 6))       # showfreqs fills its bars white: tint them
        graph.append(f'[avis]showfreqs=s={vw}x{vh}:rate={fps}:mode=bar:ascale=log:fscale=log:win_size=2048:'
                     f'cmode=combined:colors=0xffffff,lutrgb=r=val*{r}/255:g=val*{g}/255:b=val*{b}/255,'
                     f'format=yuva420p[vis]')
    elif mode == 'waves':
        graph.append(f'[avis]showwaves=s={vw}x{vh}:r={fps}:mode=cline:scale=sqrt:colors={c1}|{c2}:split_channels=0,'
                     f'format=yuva420p,colorkey=0x000000:0.05:0.05[vis]')
    elif mode == 'scope':
        graph.append(f'[avis]avectorscope=s={vh}x{vh}:r={fps}:zoom=1.5:draw=line:rc=255:gc=110:bc=200:rf=12:gf=12:'
                     f'bf=12,format=yuva420p,colorkey=0x000000:0.05:0.05[vis]')
        vx, vw = vx + (vw - vh) // 2, vh
    else:                                       # none, or the page (it draws its own picture from the levels)
        graph.append('[avis]anullsink')
    last = '[bg]'
    if mode not in ('none', 'page'):
        graph.append(f'[bg][vis]overlay={vx}:{vy}:shortest=0:format=auto[v0]')
        last = '[v0]'
    font = vis.get('font') or _font()
    fs, tc = int(vis['font_size']), _hex(vis['text_color'])
    spots = {'title': ('40', '36', fs + 8), 'now': ('40', f'h-{fs * 2 + 30}', fs),
             'note': ('40', f'{fs + 70}', int(fs * 0.8)), 'chat': (f'w-tw-40', f'h-{fs * 2 + 30}', int(fs * 0.8))}
    for i, slot in enumerate(SLOTS):
        x, y, size = spots[slot]
        f = f":fontfile='{_esc(font)}'" if font else ''
        graph.append(f"{last}drawtext=textfile='{_esc(Path(text_dir) / (slot + '.txt'))}':reload=1:expansion=none{f}:fontsize={size}:"
                     f"fontcolor={tc}:x={x}:y={y}:box=1:boxcolor=0x000000@0.35:boxborderw=12[t{i}]")
        last = f'[t{i}]'
    graph.append(f'{last}format=yuv420p,split=2[vout][vprev]')
    graph.append(f'[vprev]fps=0.5,scale=640:-2[vp]')
    kb = int(vis['video_kbps'])
    if nvenc:
        venc = ['-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'll', '-rc', 'cbr', '-b:v', f'{kb}k',
                '-maxrate', f'{kb}k', '-bufsize', f'{kb * 2}k', '-bf', '2', '-profile:v', 'high']
    else:
        venc = ['-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency', '-b:v', f'{kb}k', '-maxrate',
                f'{kb}k', '-bufsize', f'{kb * 2}k', '-x264-params', 'nal-hrd=cbr', '-profile:v', 'high']
    args += ['-filter_complex', ';'.join(graph),
             '-map', '[vout]', '-map', '[aenc]', *venc, '-g', str(fps * 2), '-keyint_min', str(fps * 2),
             '-sc_threshold', '0', '-pix_fmt', 'yuv420p', '-r', str(fps),
             '-c:a', 'aac', '-b:a', f"{int(vis['audio_kbps'])}k", '-ar', str(SR), '-ac', '2',
             '-f', 'mpegts', '-mpegts_flags', 'resend_headers', '-muxdelay', '0', 'pipe:1',
             '-map', '[vp]', '-q:v', '6', '-update', '1', '-y', str(preview)]
    return args


def pusher_args(url):
    out = ['-f', 'flv', url] if not url.startswith(('rtmp://', 'rtmps://')) else ['-f', 'flv', '-flvflags',
                                                                                  'no_duration_filesize', url]
    return [ffmpeg(), '-hide_banner', '-loglevel', 'warning', '-nostats', '-fflags', '+genpts+discardcorrupt',
            '-f', 'mpegts', '-i', 'pipe:0', '-map', '0', '-c', 'copy', '-bsf:a', 'aac_adtstoasc', *out]


class Pusher:
    """One venue's copy of the stream: an ffmpeg that remuxes the encoder's bytes to its site, restarted on its own."""

    def __init__(self, venue, url, secrets):
        self.venue, self.url_of, self.secrets = venue, url, secrets      # url: a callable, asked on every (re)start
        self.url = None
        self.q = collections.deque()
        self.qbytes = 0
        self.cv = threading.Condition()
        self.proc = None
        self.on = True
        self.state, self.error, self.sent, self.started, self.restarts = 'starting', None, 0, time.time(), 0
        self.tail = collections.deque(maxlen=12)
        self.align = True
        threading.Thread(target=self._run, name=f'push:{venue}', daemon=True).start()

    def feed(self, b, total):
        with self.cv:
            if self.align:                                    # start a fresh ffmpeg on a TS packet boundary
                skip = (188 - (total - len(b)) % 188) % 188
                b, self.align = b[skip:], False
            self.q.append(b)
            self.qbytes += len(b)
            while self.qbytes > 8 << 20 and len(self.q) > 1:  # 8 MB behind (~10 s): this site is stuck; drop the oldest
                self.qbytes -= len(self.q.popleft())
            self.cv.notify()

    def _run(self):
        backoff = 2.0
        while self.on:
            t0 = time.time()
            self.state = 'connecting'
            try:
                self.url = self.url_of()
                self.proc = subprocess.Popen(pusher_args(self.url), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, creationflags=FLAGS)
            except (OSError, ValueError) as e:
                self.state, self.error = 'cannot start', C.redact(str(e), self.secrets)
                return
            threading.Thread(target=self._errs, args=(self.proc,), daemon=True).start()
            with self.cv:
                self.q.clear()
                self.qbytes, self.align = 0, True
            try:
                while self.on and self.proc.poll() is None:
                    with self.cv:
                        if not self.q:
                            self.cv.wait(1.0)
                        chunks, self.q = list(self.q), collections.deque()
                        self.qbytes = 0
                    for b in chunks:
                        self.proc.stdin.write(b)
                        self.sent += len(b)
                    if chunks:
                        self.proc.stdin.flush()
                        if self.state != 'live' and time.time() - t0 > 4:
                            self.state, self.error = 'live', None
            except (BrokenPipeError, OSError, ValueError):
                pass
            self._kill()
            if not self.on:
                break
            self.restarts += 1
            self.error = C.redact(' | '.join(self.tail) or 'it stopped', self.secrets)[-400:]
            if time.time() - t0 > 60:
                backoff = 2.0
            self.state = f'reconnecting in {int(backoff)} s'
            time.sleep(backoff)
            backoff = min(backoff * 2, 60.0)
        self.state = 'off'

    def _errs(self, proc):
        for raw in proc.stderr:
            line = raw.decode('utf8', 'replace').strip()
            if line:
                self.tail.append(C.redact(line, self.secrets))

    def _kill(self):
        p = self.proc
        if p and p.poll() is None:
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()

    def stop(self):
        self.on = False
        with self.cv:
            self.cv.notify()
        self._kill()

    def status(self):
        d = {'state': self.state, 'sent_mb': round(self.sent / 1e6, 1), 'up_min': int((time.time() - self.started) // 60)}
        if self.restarts:
            d['restarts'] = self.restarts
        if self.error and self.state != 'live':
            d['error'] = self.error
        return d


class Output:
    def __init__(self, hub):
        self.hub = hub
        self.visual = dict(DEFAULT_VISUAL)
        self.page_box = None                         # [x, y, w, h] of the page's canvas inside its window
        try:
            self.visual.update(json.loads((C.HOME / 'visual.json').read_text(encoding='utf8')))
        except (OSError, ValueError):
            pass
        self.text_dir = C.HOME / 'text'
        self.text_dir.mkdir(parents=True, exist_ok=True)
        for s in SLOTS:
            p = self.text_dir / f'{s}.txt'
            if not p.exists():
                p.write_text('', encoding='utf8')
        self.pushers = {}
        self.enc = None
        self.enc_tail = collections.deque(maxlen=12)
        self.enc_started = 0.0
        self.total = 0
        self.lock = threading.RLock()
        self.pcm_clients = 0
        self.engine = None             # {'port', 'project'} of the engine being streamed
        self.levels = collections.deque(maxlen=50)
        self.nvenc = None
        self.chat_thread = None
        self.live_since = None

    # ------------------------------------------------------------ the sound: /pcm on the stream server
    def serve_pcm(self, handler):
        """Raw s16le stereo at SR, paced by the clock: the engine's master while one plays, silence between."""
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/octet-stream')
        handler.end_headers()
        q = collections.deque()
        stop = threading.Event()
        threading.Thread(target=self._source, args=(q, stop), daemon=True).start()
        t0, fed, buf = time.time(), 0, b''
        self.pcm_clients += 1
        try:
            while True:
                while q:
                    buf += q.popleft()
                due = int((time.time() - t0) * SR) - fed
                if len(buf) >= 4 and fed - int((time.time() - t0) * SR) < SR // 2:
                    n = min(len(buf) // 4, SR)
                    block, buf = buf[:n * 4], buf[n * 4:]
                elif due > BLOCK + int(0.25 * SR):            # the engine is quiet or gone: keep time with silence
                    n, block = BLOCK, bytes(BLOCK * 4)
                elif len(buf) > SR * 4 * 2:                   # far ahead after a gap: drop the excess, stay live
                    buf = buf[-SR * 4:]
                    continue
                else:
                    time.sleep(0.01)
                    continue
                handler.wfile.write(block)
                fed += n
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            stop.set()
            self.pcm_clients -= 1

    def _source(self, q, stop):
        while not stop.is_set():
            eng = live_engines()
            port = max(eng, key=lambda p: eng[p].get('started', 0), default=None)
            if port is None:
                self.engine = None
                stop.wait(1.0)
                continue
            self.engine = {'port': port, 'project': eng[port].get('project')}
            c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
            try:
                c.request('GET', '/stream?name=master')
                r = c.getresponse()
                if r.status != 200:
                    raise OSError(f'engine stream {r.status}')
                while not stop.is_set():
                    b = r.read1(16384)
                    if not b:
                        break
                    q.append(b)
            except (OSError, http.client.HTTPException):
                stop.wait(1.0)
            finally:
                c.close()
        self.engine = None

    # ------------------------------------------------------------ the encoder
    def preview_path(self):
        return C.HOME / 'preview.jpg'

    def page_window(self):
        p = getattr(self, 'browser', None)
        return {'open': bool(p and p.poll() is None), 'title': PAGE_TITLE}

    def open_page(self):
        """The visual page in its own app window (its own browser profile, never the person's), sized to the
        stream, kept rendering when covered, so the encoder can capture it by its title. It draws only: no sound."""
        if self.page_window()['open']:
            return
        exe = next((x for x in (shutil.which('msedge'), shutil.which('chrome'), shutil.which('chromium'),
                                'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
                                'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
                                'C:/Program Files/Google/Chrome/Application/chrome.exe') if x and os.path.exists(x)),
                   None)
        if not exe:
            raise ValueError("mode='page' needs Edge or Chrome to show the page (none found); use mode='cqt' etc.")
        W, H = int(self.visual['width']), int(self.visual['height'])
        gpu = [] if self.capture() == 'wgc' else ['--disable-gpu']   # gdigrab sees a GPU-composited window as blank
        self.browser = subprocess.Popen(
            [exe, f'--app=http://127.0.0.1:{self.hub.port}/visual?stream=1', f"--user-data-dir={C.HOME / 'browser'}",
             f'--window-size={W},{H}', '--window-position=40,40', '--no-first-run', '--no-default-browser-check',
             '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
             '--disable-background-timer-throttling', '--disable-features=CalculateNativeWinOcclusion',
             *gpu, '--mute-audio'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=FLAGS & 0x08000000)
        self.hub.page_view = None
        self.page_box = fit_window(PAGE_TITLE, W, H, lambda: self.hub.page_view)

    def capture(self):
        """How a window is captured: gdi when asked; wgc when asked (set_visual refuses it without gfxcapture);
        auto: wgc where ffmpeg has it, else gdi."""
        c = self.visual.get('capture') or 'auto'
        if c == 'gdi':
            return 'gdi'
        if c == 'wgc':
            if not has_gfxcapture():
                raise ValueError("capture='wgc' needs ffmpeg 8 with gfxcapture (this ffmpeg has none); use "
                                 "capture='gdi' or 'auto'")
            return 'wgc'
        return 'wgc' if has_gfxcapture() else 'gdi'

    def _visual_now(self):
        if self.visual['mode'] == 'page':
            return {**self.visual, 'crop': self.page_box, 'capture': self.capture()}
        if self.visual.get('window'):              # a browser window: its web content only, not its title bar
            return {**self.visual, 'crop': window_content_box(self.visual['window']), 'capture': self.capture()}
        return self.visual

    def _start_encoder(self):
        if not ffmpeg():
            raise ValueError("ffmpeg is not on PATH (or config_set('ffmpeg', <path>)): streaming needs it")
        if self.visual['mode'] == 'page':
            self.open_page()
        if self.nvenc is None:
            self.nvenc = self.visual['encoder'] in ('auto', 'nvenc') and has_nvenc()
        args = build_encoder_args(self._visual_now(), self.text_dir, self.preview_path(),
                                  f'http://127.0.0.1:{self.hub.port}/pcm', self.nvenc)
        self.enc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=FLAGS)
        self.enc_started = time.time()
        threading.Thread(target=self._enc_errs, args=(self.enc,), daemon=True).start()
        threading.Thread(target=self._fanout, args=(self.enc,), daemon=True).start()

    def _enc_errs(self, proc):
        for raw in proc.stderr:
            line = raw.decode('utf8', 'replace').strip()
            if line:
                self.enc_tail.append(line)

    def _fanout(self, proc):
        while True:
            b = proc.stdout.read1(65536) if hasattr(proc.stdout, 'read1') else proc.stdout.read(65536)
            if not b:
                break
            self.total += len(b)
            for p in list(self.pushers.values()):
                p.feed(b, self.total)
        with self.lock:
            if self.enc is proc and self.pushers:              # it died under us: once with NVENC off, then again
                err = ' | '.join(self.enc_tail)
                if self.nvenc and time.time() - self.enc_started < 10:
                    self.nvenc = False
                self.hub.log.add('status', 'output', state='encoder restart', text=err[-300:])
                time.sleep(2.0)
                if self.pushers:
                    self._start_encoder()

    def _stop_encoder(self):
        p, self.enc = self.enc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()

    # ------------------------------------------------------------ ops
    def _url(self, name, v, check=False):
        if v['platform'] == 'file':
            path = Path(v['channel'])
            if path.suffix.lower() != '.flv':
                path = path.with_suffix('.flv')
            path.parent.mkdir(parents=True, exist_ok=True)
            if check:
                return str(path)
            while path.exists():                       # never over an earlier take: a restart starts a new file
                path = path.with_name(f"{Path(v['channel']).with_suffix('').name}_"
                                      f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.flv")
            return str(path)
        k = C.load_keys(name)
        if not k.get('stream_key'):
            raise ValueError(f'{name} has no stream key: the person types `python -m ismail.stream key {name}` once')
        base = v.get('ingest') or C.INGEST.get(v['platform'])
        if not base:
            raise ValueError(f'{name} has no ingest address: the person sets it with `python -m ismail.stream key '
                             f'{name}` (it asks), or venue_set(..., ingest=...)')
        return base.rstrip('/') + '/' + k['stream_key']

    def go(self, venues, approved, video=None):
        all_v = C.load_venues()
        names = list(all_v) if venues in ('all', ['all']) else ([venues] if isinstance(venues, str) else list(venues))
        names = [n for n in names if all_v.get(n, {}).get('output')] if venues in ('all', ['all']) else names
        public = []
        for n in names:
            v = all_v.get(n)
            if not v:
                raise ValueError(f'no venue {n!r}: venue_list shows them')
            if not v.get('output'):
                raise ValueError(f'{n} does not stream (output off, or {v["platform"]} takes no video)')
            if v['platform'] != 'file':
                public.append(n)
        if public and len(str(approved or '').strip()) < 3:
            raise ValueError("going live on " + ', '.join(public) + " is the person's yes, each time: pass approved='<"
                             "their words, as they said them>' (a local file venue needs none)")
        if video:
            self.set_visual(video, restart=False)
        for n in names:                                             # every key checked before anything starts
            self._url(n, all_v[n], check=True)
        urls = {n: (lambda n=n: self._url(n, C.load_venues().get(n) or all_v[n])) for n in names}
        with self.lock:
            for n, url in urls.items():
                if n in self.pushers:
                    continue
                self.pushers[n] = Pusher(n, url, C.secrets_of(n))
            if self.enc is None or self.enc.poll() is not None:
                self._start_encoder()
            if self.live_since is None:
                self.live_since = time.time()
            if self.visual.get('chat_overlay') and not self.chat_thread:
                self.chat_thread = threading.Thread(target=self._chat_overlay, daemon=True)
                self.chat_thread.start()
        rec = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'venues': names, 'approved': approved}
        with open(C.HOME / 'go_log.jsonl', 'a', encoding='utf8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        self.hub.log.add('status', 'output', state='go', text=f"streaming to {', '.join(names)}")
        return self.status()

    def end(self, venues=None):
        with self.lock:
            names = list(self.pushers) if not venues or venues in ('all', ['all']) else \
                ([venues] if isinstance(venues, str) else list(venues))
            for n in names:
                p = self.pushers.pop(n, None)
                if p:
                    p.stop()
            if not self.pushers:
                self._stop_encoder()
                self.live_since = None
                if self.page_window()['open']:
                    self.browser.terminate()
        if names:
            self.hub.log.add('status', 'output', state='end', text=f"stopped streaming to {', '.join(names)}")
        return self.status()

    def set_visual(self, params, restart=True):
        params = {k: v for k, v in dict(params).items() if v is not None}
        bad = set(params) - set(DEFAULT_VISUAL)
        if bad:
            raise ValueError(f"unknown visual settings {sorted(bad)}: the settings are {sorted(DEFAULT_VISUAL)}")
        new = {**self.visual, **params}
        if new['mode'] not in MODES:
            raise ValueError(f"mode {new['mode']!r}: one of {', '.join(MODES)}")
        if new.get('capture', 'auto') not in CAPTURES:
            raise ValueError(f"capture {new['capture']!r}: one of {', '.join(CAPTURES)}")
        if new.get('capture') == 'wgc' and not has_gfxcapture():
            raise ValueError("capture='wgc' needs ffmpeg 8 with gfxcapture (this ffmpeg has none); use capture='gdi' "
                             "or 'auto'")
        for c in list(new.get('colors') or []) + [new['background'], new['text_color']]:
            _hex(c)
        if new.get('image') and not os.path.exists(str(new['image'])):
            raise ValueError(f"image {new['image']}: no such file")
        if len(new['area']) != 4 or not all(0 <= float(x) <= 1 for x in new['area']):
            raise ValueError('area: [x, y, w, h] as fractions of the frame, each 0-1')
        if not (320 <= int(new['width']) <= 1920 and 240 <= int(new['height']) <= 1080 and 10 <= int(new['fps']) <= 60):
            raise ValueError('width 320-1920, height 240-1080, fps 10-60')
        if not 500 <= int(new['video_kbps']) <= 9000:
            raise ValueError('video_kbps 500-9000 (Twitch takes at most 6000)')
        self.visual = new
        (C.HOME / 'visual.json').write_text(json.dumps(self.visual, indent=1), encoding='utf8')
        if restart:
            with self.lock:
                if self.enc is not None and self.pushers:
                    urls = {n: p.url_of for n, p in self.pushers.items()}
                    secrets = {n: p.secrets for n, p in self.pushers.items()}
                    for p in self.pushers.values():
                        p.stop()
                    self._stop_encoder()
                    self.pushers = {n: Pusher(n, u, secrets[n]) for n, u in urls.items()}
                    self._start_encoder()
        return {'visual': self.visual, 'restarted': bool(restart and self.pushers)}

    def set_text(self, slot, text):
        if slot not in SLOTS:
            raise ValueError(f"slot {slot!r}: one of {', '.join(SLOTS)}")
        text = str(text or '').replace('\r', '')[:300]
        self._write_text(slot, text)
        return {'slot': slot, 'text': text}

    def _write_text(self, slot, text):
        p = self.text_dir / f'{slot}.txt'
        tmp = p.with_name(f'{slot}.{os.getpid()}.tmp')
        tmp.write_text(text, encoding='utf8')
        for _ in range(20):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:                     # ffmpeg is reading it this instant (Windows)
                time.sleep(0.02)

    def _chat_overlay(self):
        since = self.hub.log.n
        while self.pushers and self.visual.get('chat_overlay'):
            got = self.hub.log.wait(since, 10.0, lambda d: d['kind'] in ('chat', 'event', 'said'))
            if got:
                since = got[-1]['n']
                d = got[-1]
                who = 'me' if d['kind'] == 'said' else ((d.get('user') or {}).get('display') or '')
                line = d.get('text') or d.get('type') or ''
                self._write_text('chat', f"{who}: {line}"[:90])
        self.chat_thread = None

    def look(self):
        st = self.status()
        st['preview'] = str(self.preview_path()) if self.preview_path().exists() else None
        st['preview_age_s'] = (round(time.time() - self.preview_path().stat().st_mtime, 1)
                               if self.preview_path().exists() else None)
        st['texts'] = {s: (self.text_dir / f'{s}.txt').read_text(encoding='utf8') for s in SLOTS}
        return st

    def status(self):
        on = self.enc is not None and self.enc.poll() is None
        d = {'state': 'live' if on and self.pushers else 'off',
             'venues': {n: p.status() for n, p in self.pushers.items()},
             'engine': (f"streaming the engine on :{self.engine['port']} ({os.path.basename(str(self.engine['project']))})"
                        if self.engine else 'no live engine: the stream carries silence'),
             'encoder': ('NVENC' if self.nvenc else 'x264') if on else 'off',
             'mode': self.visual['mode']}
        if self.live_since:
            d['live_min'] = int((time.time() - self.live_since) // 60)
        if self.enc_tail and not on:
            d['encoder_error'] = ' | '.join(self.enc_tail)[-300:]
        parts = [f"{n} {p['state']}" for n, p in d['venues'].items()]
        d['summary'] = (f"LIVE {d.get('live_min', 0)} min to " + ', '.join(parts) + f"; {d['engine']}; picture "
                        f"{d['mode']} via {d['encoder']}") if d['state'] == 'live' else 'off (stream_go starts it)'
        return d
