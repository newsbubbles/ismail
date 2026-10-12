"""The edit engine: a cut list written in bars, glitches driven by the song's own notes, a VHS layer, the master mixed in.

A song's video/cut.py:

    from ismail.video.edit import Cut
    C = Cut(__file__)
    C.shot('EXT', 's01_exterior', 192, rain=True)
    C.seg(C.B(1), C.B(5), 'EXT')                       # bars 1-4
    for e in C.ev('growl', 12, 17, fam='talk'):        # an insert for exactly one note
        C.seg(e['f'], e['f'] + e['len'], 'INSERT', 20)
    C.fx_map.update({'name': 'talk'})                  # named families -> effects
    C.main()

    python video/cut.py                     full render -> video/renders/<Title> vN.mp4
    python video/cut.py --sheet 33 41 16    contact sheet of 16 frames across bars 33-40 (the review tool)
    python video/cut.py --range 33 41       a preview mp4 of bars 33-40

Frames are processed at 960x540 (shots render there on a slow GPU) and lanczos-scaled to 1920x1080 on encode.
"""
import json
import os
import subprocess
import sys
import time

import numpy as np

W, H = 960, 540

# growl family (and named family) -> effect
FX_MAP = {'wub': 'wub', 'yoi': 'yoi', 'screech': 'screech', 'metal': 'metal', 'zap': 'zap', 'dive': 'dive',
          'grind': 'grind', 'robot': 'robot', 'howl': 'howl', 'talk': 'talk', 'chop': 'stutter',
          'bang': 'bang', 'reload': 'reload'}
EFFECTS = ('wub', 'yoi', 'screech', 'metal', 'zap', 'dive', 'grind', 'robot', 'howl', 'kick', 'snare', 'flash',
           'tear', 'talk', 'ghost', 'palette')


def _nframes(path):
    r = subprocess.run(['ffprobe', '-v', 'error', '-count_packets', '-select_streams', 'v:0', '-show_entries',
                        'stream=nb_read_packets', '-of', 'csv=p=0', path], capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return 0


class _Src:
    """one shot's frames, decoded on demand: reads forward while the cut plays forward (it mostly does), seeks on a
    jump, and keeps the last frames in memory for stutters and flickers. No disk cache: a full-song source at
    960x540 would be ~10 GB."""

    def __init__(self, key, stem, n, shotdir, keep=96):
        import cv2
        self.key, self.n, self.cap, self.pos, self.keep = key, n, None, 0, keep
        self.lru = {}
        vs = sorted((int(f[len(stem) + 2:-4]), f) for f in os.listdir(shotdir) if f.startswith(stem + '_v')
                    and f.endswith('.mp4') and f[len(stem) + 2:-4].isdigit()) if os.path.isdir(shotdir) else []
        path = next((os.path.join(shotdir, f) for v, f in reversed(vs) if _nframes(os.path.join(shotdir, f)) >= n), None)
        if path is None:
            print(f'  [missing] {key}: {stem} (a labelled placeholder is used)')
            return
        self.path, self._cv2 = path, cv2
        self.cap = cv2.VideoCapture(path)

    def _read(self):
        ok, im = self.cap.read()
        self.pos += 1
        if not ok:
            return np.zeros((H, W, 3), np.uint8)
        if im.shape[0] != H or im.shape[1] != W:
            im = self._cv2.resize(im, (W, H), interpolation=self._cv2.INTER_AREA)
        return im

    def get(self, i):
        if self.cap is None:
            import cv2
            im = np.zeros((H, W, 3), np.uint8)
            cv2.putText(im, self.key, (40, 80), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 255), 3)
            return im
        i = int(np.clip(i, 0, self.n - 1))
        if i in self.lru:
            return self.lru[i]
        if not (self.pos <= i < self.pos + 90):        # a jump: seek (forward runs just read on)
            self.cap.set(self._cv2.CAP_PROP_POS_FRAMES, i)
            self.pos = i
        while self.pos < i:
            self._read()
        im = self._read()
        self.lru[i] = im
        if len(self.lru) > self.keep:
            self.lru.pop(next(iter(self.lru)))
        return im


class Cut:
    def __init__(self, cut_file, font=r'C:\Windows\Fonts\OCRAEXT.TTF'):
        from . import config
        self.vd = os.path.dirname(os.path.abspath(cut_file))
        self.song = os.path.dirname(self.vd)
        self.cfg = config(self.song)
        ev = json.load(open(os.path.join(self.vd, 'build', 'events.json')))
        self.EV, self.fps, self.fpb, self.bpb = ev['tracks'], ev['fps'], ev['frames_per_beat'], ev['beats_per_bar']
        self.off, self.total, self.master = ev['offset_frames'], ev['total_frames'], ev['master']
        self.FT = dict(np.load(os.path.join(self.vd, 'build', 'features.npz')))
        self.title = self.cfg.get('title') or json.load(open(os.path.join(self.song, 'proj', 'project.json'))).get('name', 'video')
        self.font = self.cfg.get('font') or font
        self.shots, self.keys = {}, []
        self.rain, self.gain, self.crop = set(), {}, {}
        n = self.total
        self.p_shot = np.full(n, -1, np.int16)
        self.p_src = np.zeros(n, np.float32)
        self.p_flip = np.zeros(n, bool)
        self.stutter = np.full(n, -1, np.int32)
        self.env = {k: np.zeros(n, np.float32) for k in EFFECTS}
        self.level = np.full(n, 0.5, np.float32)
        self.fx_map = dict(FX_MAP)
        self.texts = []        # (t0, t1, text, size, cx, cy)
        self.crt_off = None    # frame where the picture collapses
        self.end_title = None  # (t0, text)
        self.ghost_key = None  # the shot double-exposed on 'howl'/'ghost'
        self.grade_gamma = 0.85
        self.palette = [(20, 20, 20), (90, 90, 90), (170, 170, 170), (235, 235, 235)]   # RGB, dark to light
        self.palette_gain = 1.25                        # brightens night renders before they are mapped

    # ---- time
    def B(self, bar, beat=0.0):
        """song position -> frame (bar 1-based, beat 0-based)."""
        return int(round(((bar - 1) * self.bpb + beat) * self.fpb + self.off))

    def ev(self, track, a=1, b=10 ** 6, **kw):
        lo, hi = self.B(a), self.B(b) if b < 10 ** 6 else 10 ** 9
        return [e for e in self.EV.get(track, []) if lo <= e['f'] < hi and all(e.get(k) == v for k, v in kw.items())]

    # ---- sources
    def shot(self, key, stem, frames, rain=False, gain=1.0, crop=None):
        """register a rendered shot (video/renders/shots/<stem>_vN.mp4, newest complete version)."""
        self.shots[key] = (stem, frames)
        self.keys.append(key)
        if rain:
            self.rain.add(key)
        if gain != 1.0:
            self.gain[key] = gain
        if crop:
            self.crop[key] = crop       # (zoom, centre x fraction, centre y fraction)

    # ---- the cut list
    def seg(self, t0, t1, key, s0=0, speed=1.0, flip=False, wrap=True):
        n = self.shots[key][1]
        k = self.keys.index(key)
        for t in range(max(0, t0), min(self.total, t1)):
            s = s0 + (t - t0) * speed
            if wrap:
                s = s % n
            self.p_shot[t], self.p_src[t], self.p_flip[t] = k, s, flip

    def black(self, t0, t1):
        self.p_shot[max(0, t0):min(self.total, t1)] = -1

    def cycle_cuts(self, t0, t1, cuts, keys, heads, speed=1.0):
        """cut at each frame of `cuts` inside [t0, t1), cycling through keys; each key keeps its own playhead."""
        b = [t0] + [c for c in cuts if t0 < c < t1] + [t1]
        for i in range(len(b) - 1):
            k = keys[i % len(keys)]
            self.seg(b[i], b[i + 1], k, heads.get(k, 0), speed)
            heads[k] = heads.get(k, 0) + (b[i + 1] - b[i]) * speed

    def tape_stop(self, t0, t_stop, key, s0):
        """the picture decelerates to a halt between t0 and t_stop (follows a pitch sag into a tape stop)."""
        pos, k = float(s0), self.keys.index(key)
        for t in range(t0, self.total):
            x = (t - t0) / max(1, t_stop - t0)
            pos += max(0.0, 1.0 - x ** 0.7) if x < 1 else 0.0
            self.p_shot[t], self.p_src[t], self.p_flip[t] = k, pos, False

    def sections(self, spec):
        """spec: [(bar_a, bar_b, level)]; level scales punch, shake, chroma, grain (intro 0.25, drop 1.0, drop 2 1.2)."""
        for a, b, lv in spec:
            self.level[self.B(a):min(self.total, self.B(b))] = lv

    def text(self, t0, t1, s, size=96, cx=W / 2, cy=H / 2):
        self.texts.append((t0, t1, s, size, cx, cy))

    # ---- effects
    def hold(self, key, t0, t1, amp=1.0):
        """an effect held on for a stretch (e.g. hold('palette', ...) draws a whole section in C.palette)."""
        e = self.env[key]
        e[max(0, t0):min(self.total, t1)] = np.maximum(e[max(0, t0):min(self.total, t1)], amp)

    def pulse(self, key, f0, length, tau=None, amp=1.0):
        tau = tau or max(2.0, length * 0.45)
        e = self.env[key]
        for t in range(max(0, f0), min(self.total, f0 + length + int(tau * 2))):
            e[t] = max(e[t], amp * np.exp(-(t - f0) / tau))

    def auto_fx(self, fam_tracks=None):
        """schedule the glitches from the notes: family effects from every track with `fam`, plus kick, snare,
        crash/boom and glitch tracks by name. Call after the cut list is written."""
        for name, evs in self.EV.items():
            if fam_tracks and name not in fam_tracks:
                continue
            for e in evs:
                fx = self.fx_map.get(e.get('fam'))
                if fx is None:
                    continue
                if fx == 'stutter':
                    for t in range(e['f'], min(self.total, e['f'] + e['len'])):
                        self.stutter[t] = e['f'] + (t - e['f']) % 3
                    self.pulse('tear', e['f'], 3, amp=0.5)
                elif fx == 'bang':
                    self.pulse('flash', e['f'], 2, tau=4, amp=1.6)
                elif fx == 'reload':
                    self.pulse('snare', e['f'], 2, tau=1.5, amp=1.0)
                else:
                    self.pulse(fx, e['f'], e['len'])
        for e in self.EV.get('kick', []):
            self.pulse('kick', e['f'], 3, tau=2.5, amp=e['vel'] / 127)
        for e in self.EV.get('snare', []):
            self.pulse('snare', e['f'], 2, tau=1.8, amp=e['vel'] / 127)
        for e in self.EV.get('glitch', []):
            self.pulse('tear', e['f'], 3); self.pulse('grind', e['f'], 3)
        for e in self.EV.get('crash', []) + self.EV.get('boom', []):
            self.pulse('flash', e['f'], 2, tau=3, amp=0.9)

    # ---- per frame
    def _text_img(self, s, size, cache={}):
        from PIL import Image, ImageDraw, ImageFont
        k = (self.font, s, size)
        if k not in cache:
            try:
                font = ImageFont.truetype(self.font, size)
            except OSError:     # no OCR-A on this machine: set cfg 'font' or pass Cut(font=...)
                font = ImageFont.load_default(size)
            l, t_, r, b = font.getbbox(s)
            im = Image.new('L', (r - l + 8, b - t_ + 8), 0)
            ImageDraw.Draw(im).text((4 - l, 4 - t_), s, 255, font=font)
            cache[k] = np.asarray(im, np.float32) / 255
        return cache[k]

    def _put_text(self, x, s, size, cx, cy, alpha=1.0, split=0):
        m = self._text_img(s, size)
        h, w = m.shape
        x0, y0 = int(cx - w / 2), int(cy - h / 2)
        for ch, dx in ((2, -split), (1, 0), (0, split)):
            xa, ya, xb, yb = max(0, x0 + dx), max(0, y0), min(W, x0 + dx + w), min(H, y0 + h)
            if xb > xa and yb > ya:
                mm = m[ya - y0:yb - y0, xa - x0 - dx:xb - x0 - dx] * alpha
                x[ya:yb, xa:xb, ch] = x[ya:yb, xa:xb, ch] * (1 - mm) + mm

    def _prepare(self, t0=0, t1=None):
        import cv2
        self.cv2 = cv2
        sd = os.path.join(self.vd, 'renders', 'shots')
        self.srcs = {k: _Src(k, st, n, sd) for k, (st, n) in self.shots.items()}
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.yy, self.xx = yy, xx
        self.vig = (1 - 0.32 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)).clip(0.25, 1)[..., None]
        self.scan = np.where((np.arange(H) % 2) == 0, 1.0, 0.9).astype(np.float32)[:, None, None]
        r = np.random.default_rng(3)
        self.drops = np.c_[r.uniform(0, W, 500), r.uniform(0, H, 500), r.uniform(22, 40, 500), r.uniform(8, 22, 500)]

    def frame(self, t, prev):
        cv2, env, lv = self.cv2, self.env, self.level[t]
        k = self.p_shot[t]
        if k < 0:
            x = np.zeros((H, W, 3), np.float32)
        else:
            key = self.keys[k]
            s = self.p_src[t]
            if self.stutter[t] >= 0 and self.p_shot[self.stutter[t]] == k:
                s = self.p_src[self.stutter[t]]
            img = self.srcs[key].get(int(s))
            if key in self.crop:
                zm, cxf, cyf = self.crop[key]
                cw, chh = W / zm, H / zm
                x0 = int(np.clip(cxf * W - cw / 2, 0, W - cw)); y0 = int(np.clip(cyf * H - chh / 2, 0, H - chh))
                img = cv2.resize(img[y0:y0 + int(chh), x0:x0 + int(cw)], (W, H), interpolation=cv2.INTER_LANCZOS4)
            if self.p_flip[t]:
                img = img[:, ::-1]
            x = img.astype(np.float32) / 255 * self.gain.get(key, 1.0)
            if key in self.rain:
                r = np.zeros((H, W), np.float32)
                d = self.drops
                ys = (d[:, 1] + t * d[:, 2]) % (H + 60) - 30
                xs = (d[:, 0] - t * d[:, 2] * 0.25) % W
                for i in range(len(d)):
                    cv2.line(r, (int(xs[i]), int(ys[i])), (int(xs[i] - d[i, 3] * 0.25), int(ys[i] - d[i, 3])), 0.35, 1, cv2.LINE_AA)
                x = x + r[..., None] * np.array([0.8, 0.85, 1.0], np.float32)
        rng = np.random.default_rng(t)
        z = 1 + 0.045 * env['kick'][t] * lv
        shake = self.FT['sub_n'][t] * 5 * lv if lv > 0.6 else 0
        dx, dy = rng.normal(0, 1, 2) * shake
        if z != 1 or shake:
            M = np.float32([[z, 0, (1 - z) * W / 2 + dx], [0, z, (1 - z) * H / 2 + dy]])
            x = cv2.warpAffine(x, M, (W, H), borderMode=cv2.BORDER_REFLECT)
        wb, dv = env['wub'][t], env['dive'][t]
        if wb > 0.02 or dv > 0.02:
            mx = self.xx + wb * 22 * np.sin(self.yy / 23 + t * 1.3)
            my = self.yy - dv * 140 * (0.5 + 0.5 * np.sin(self.xx / 37 + t * 0.2)) * (self.yy / H)
            x = cv2.remap(x, mx.astype(np.float32), my.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
        gr = env['grind'][t]
        if gr > 0.05 and prev is not None:
            bs = 20                                        # tiles 960x540 exactly
            for by in range(0, H, bs):
                for bx in range(0, W, bs):
                    if rng.random() < gr * 0.6:
                        ox, oy = int(rng.integers(-2, 3) * 8 * gr), int(rng.integers(-2, 3) * 8 * gr)
                        sy, sx = np.clip(by + oy, 0, H - bs), np.clip(bx + ox, 0, W - bs)
                        x[by:by + bs, bx:bx + bs] = prev[sy:sy + bs, sx:sx + bs]
            x = x + rng.normal(0, 0.06 * gr, x.shape).astype(np.float32)
        rb = env['robot'][t]
        if rb > 0.05:
            q = int(4 + 18 * rb)
            x = cv2.resize(cv2.resize(x, (W // q, H // q), interpolation=cv2.INTER_AREA), (W, H), interpolation=cv2.INTER_NEAREST)
        tr = max(env['screech'][t], env['tear'][t])
        if tr > 0.05:
            for _ in range(int(3 + 9 * tr)):
                y0 = int(rng.integers(0, H - 8)); hgt = int(rng.integers(4, 40))
                x[y0:y0 + hgt] = np.roll(x[y0:y0 + hgt], int(rng.normal(0, 60 * tr)), axis=1)
        c = int(3 + 26 * max(env['yoi'][t], env['talk'][t] * 0.7, env['screech'][t] * 0.8, env['flash'][t] * 0.6) * (0.5 + 0.5 * lv))
        if c > 0:
            x[..., 2] = np.roll(x[..., 2], c, axis=1)
            x[..., 0] = np.roll(x[..., 0], -c, axis=1)
        mt = env['metal'][t]
        if mt > 0.05:
            lvls = 3 + int(5 * (1 - mt))
            x = np.round(x * lvls) / lvls
            g = cv2.cvtColor(np.clip(x, 0, 1), cv2.COLOR_BGR2GRAY)
            x = x + (np.abs(cv2.Laplacian(g, cv2.CV_32F)) * 1.5 * mt)[..., None]
        pm = env['palette'][t]
        if pm > 0.05:                                     # every pixel to the nearest shade of C.palette, by brightness
            pal = np.array([c[::-1] for c in self.palette], np.float32) / 255
            g = cv2.cvtColor(np.clip(x, 0, 1), cv2.COLOR_BGR2GRAY)
            q = np.clip((g * self.palette_gain * len(pal)).astype(np.int32), 0, len(pal) - 1)
            x = x * (1 - pm) + pal[q] * pm
        if env['zap'][t] > 0.5:
            x = 1 - np.clip(x, 0, 1)
        hw = max(env['howl'][t], env['ghost'][t])
        if hw > 0.05 and self.ghost_key:
            gs = self.srcs[self.ghost_key]
            g = gs.get(t % gs.n).astype(np.float32) / 255
            x = 1 - (1 - x) * (1 - g * 0.55 * hw)
        x = x + 0.14 * env['snare'][t] * lv + env['flash'][t] * 0.9
        for (a, b, s_, size, cx, cy) in self.texts:
            if a <= t < b:
                self._put_text(x, s_, size, cx + rng.normal(0, 2), cy + rng.normal(0, 2), 0.95, split=int(3 + 6 * lv))
        if self.crt_off is not None and t >= self.crt_off:
            x = self._crt(x, t - self.crt_off, t)
        x = np.clip(x, 0, 1.5) ** self.grade_gamma       # lift the mids: night renders
        x = 0.025 + x * 0.975                             # VHS blacks
        x = x + rng.normal(0, 0.03 + 0.05 * self.FT['high_n'][t] * lv, (H, W, 1)).astype(np.float32)
        return np.clip(x * self.scan * self.vig, 0, 1)

    def _crt(self, x, k2, t):
        cv2 = self.cv2
        if k2 < 6:
            sy = max(0.004, 1 - k2 / 6)
            M = np.float32([[1, 0, 0], [0, sy, (1 - sy) * H / 2]])
            return cv2.warpAffine(np.clip(x, 0, 1) + 0.6 * k2 / 6, M, (W, H))
        x = np.zeros_like(x)
        if k2 < 14:
            w_ = int(W * max(0.003, 1 - (k2 - 6) / 8) / 2)
            x[H // 2 - 1:H // 2 + 1, W // 2 - w_:W // 2 + w_] = 1.2
        elif k2 < 26:
            cv2.circle(x, (W // 2, H // 2), max(1, int(4 * (1 - (k2 - 14) / 12))), (1, 1, 1), -1, cv2.LINE_AA)
            x *= 1 - (k2 - 14) / 12
        else:
            for (a, b, s_, size, cx, cy) in self.texts:
                if a <= t < b:
                    self._put_text(x, s_, size, cx, cy, 0.8)
            if self.end_title and t >= self.end_title[0]:
                self._put_text(x, self.end_title[1], 40, W / 2, H / 2, 0.35 * min(1, (t - self.end_title[0]) / 20), split=2)
        return x

    # ---- output
    def run(self, t0, t1, out, audio=True, sheet=None):
        self._prepare(t0, t1)
        cv2 = self.cv2
        st, prev = time.time(), None
        if sheet:
            pick = set(np.linspace(t0, t1 - 1, sheet).astype(int).tolist())
            tiles = []
            for t in range(t0, t1):
                prev = self.frame(t, prev)
                if t in pick:
                    im = (prev * 255).astype(np.uint8)
                    bar = (t - self.off) / (self.fpb * self.bpb)
                    cv2.putText(im, f'{t} b{int(bar) + 1}.{(bar % 1) * self.bpb:.2f}', (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
                    tiles.append(cv2.resize(im, (480, 270)))
            rows = [np.hstack(tiles[i:i + 4] + [np.zeros_like(tiles[0])] * (4 - len(tiles[i:i + 4]))) for i in range(0, len(tiles), 4)]
            cv2.imwrite(out, np.vstack(rows))
            print('sheet', out)
            return out
        cmd = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{W}x{H}', '-r', str(self.fps), '-i', '-']
        if audio:
            cmd += ['-ss', f'{t0 / self.fps:.4f}', '-t', f'{(t1 - t0) / self.fps:.4f}', '-i', self.master]
        cmd += ['-vf', 'scale=1920:1080:flags=lanczos', '-c:v', 'libx264', '-preset', 'slow', '-crf', '18',
                '-maxrate', '24M', '-bufsize', '48M', '-pix_fmt', 'yuv420p', '-r', str(self.fps)]   # grain at 1080p made a 1.9 GB file
        if audio:
            cmd += ['-c:a', 'aac', '-b:a', '320k', '-shortest']
        p = subprocess.Popen(cmd + [out], stdin=subprocess.PIPE)
        for i, t in enumerate(range(t0, t1)):
            prev = self.frame(t, prev)
            p.stdin.write((prev * 255).astype(np.uint8).tobytes())
            if i % 300 == 0:
                print(f'  frame {t} ({i}/{t1 - t0})  {time.time() - st:.0f}s', flush=True)
        p.stdin.close(); p.wait()
        print('wrote', out, f'{time.time() - st:.0f}s')
        return out

    def main(self, argv=None):
        a = sys.argv[1:] if argv is None else argv
        os.makedirs(os.path.join(self.vd, 'build', 'look'), exist_ok=True)
        if a[:1] == ['--sheet']:
            n = int(a[3]) if len(a) > 3 else 16
            return self.run(self.B(int(a[1])), min(self.total, self.B(int(a[2]))),
                            os.path.join(self.vd, 'build', 'look', 'edit_sheet.png'), sheet=n)
        if a[:1] == ['--range']:
            return self.run(self.B(int(a[1])), min(self.total, self.B(int(a[2]))), os.path.join(self.vd, 'build', 'preview.mp4'))
        rd = os.path.join(self.vd, 'renders')
        os.makedirs(rd, exist_ok=True)
        v = 1
        while os.path.exists(os.path.join(rd, f'{self.title} v{v}.mp4')):
            v += 1
        return self.run(0, self.total, os.path.join(rd, f'{self.title} v{v}.mp4'))
