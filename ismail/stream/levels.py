"""What the visual page moves to: the set's levels and bands, the beat, and each track's notes as they sound.

Read in the stream server's own process, never on the mixer: the master comes over the engine's local stream (the
same feed the phone hears), and the bands are an FFT here. The beat and the notes about to sound come from the
engine's clock and onsets ops, asked once a second and every two seconds. It runs only while a page or the output
wants it."""
import http.client
import json
import math
import threading
import time
import urllib.request

import numpy as np

from .output import SR, live_engines

FPS = 30
N_FFT = 2048
N_BANDS = 16
LO_HZ, HI_HZ = 40.0, 16000.0
FLOOR_DB = -60.0


def band_edges(n=N_BANDS, lo=LO_HZ, hi=HI_HZ):
    return np.geomspace(lo, hi, n + 1)


_EDGES = band_edges()
_BINS = np.fft.rfftfreq(N_FFT, 1.0 / SR)
_IDX = [np.where((_BINS >= a) & (_BINS < b))[0] for a, b in zip(_EDGES[:-1], _EDGES[1:])]
_WIN = np.hanning(N_FFT).astype(np.float32)


def analyse(mono):
    """One frame of mono samples (N_FFT, float -1..1) -> rms and peak in dB, and N_BANDS band levels 0..1
    (FLOOR_DB..0 dBFS, log-spaced from 40 Hz to 16 kHz)."""
    x = np.asarray(mono, dtype=np.float32)[-N_FFT:]
    if x.size < N_FFT:
        x = np.pad(x, (N_FFT - x.size, 0))
    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
    peak = float(np.max(np.abs(x)) + 1e-12)
    spec = np.abs(np.fft.rfft(x * _WIN)) * (2.0 / np.sum(_WIN))
    bands = []
    for ix in _IDX:
        v = float(np.sqrt(np.sum(spec[ix] ** 2) / 1.5)) if ix.size else 0.0     # band energy; 1.5 = Hann's ENBW
        db = 20 * math.log10(v + 1e-12)
        bands.append(round(min(1.0, max(0.0, (db - FLOOR_DB) / -FLOOR_DB)), 3))
    return {'rms_db': round(20 * math.log10(rms), 1), 'peak_db': round(20 * math.log10(peak), 1),
            'level': round(min(1.0, max(0.0, (20 * math.log10(rms) - FLOOR_DB) / -FLOOR_DB)), 3), 'bands': bands}


def _engine_op(port, op, **args):
    req = urllib.request.Request(f'http://127.0.0.1:{port}/', data=json.dumps({'op': op, 'args': args}).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=3) as r:
        res = json.loads(r.read())
    if not res.get('ok'):
        raise RuntimeError(res.get('error'))
    return res['result']


class Levels:
    def __init__(self):
        self.frame = {'t': time.time(), 'engine': None, 'level': 0.0, 'rms_db': -120.0, 'peak_db': -120.0,
                      'bands': [0.0] * N_BANDS, 'beat': None, 'bpm': None, 'bpb': None, 'hits': []}
        self.seq = 0
        self.cond = threading.Condition()
        self.until = 0.0
        self.thread = None
        self.clock = None                # (wall, beat, bpm, bpb) from the engine
        self.onsets = []                 # [{t, b, l}] ahead of now
        self.port = None

    def want(self, seconds=15.0):
        """Keep the analysis running at least this long (a page that reads frames calls it every frame)."""
        self.until = max(self.until, time.time() + seconds)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name='levels', daemon=True)
            self.thread.start()

    def wait(self, seq, timeout=1.0):
        with self.cond:
            if self.seq <= seq:
                self.cond.wait(timeout)
            return self.seq, self.frame

    def _publish(self, f):
        with self.cond:
            self.frame, self.seq = f, self.seq + 1
            self.cond.notify_all()

    def beat_now(self):
        c = self.clock
        if not c or not c[2]:
            return None
        return c[1] + (time.time() - c[0]) * c[2] / 60.0

    def _run(self):
        while time.time() < self.until:
            eng = live_engines()
            port = max(eng, key=lambda p: eng[p].get('started', 0), default=None)
            if port is None:
                self.port = None
                self._publish({**self.frame, 't': time.time(), 'engine': None, 'level': 0.0,
                               'bands': [0.0] * N_BANDS, 'beat': None, 'hits': []})
                time.sleep(0.25)
                continue
            self.port = port
            stop = threading.Event()
            threading.Thread(target=self._timing, args=(port, stop), daemon=True).start()
            try:
                self._read(port)
            except (OSError, http.client.HTTPException):
                time.sleep(1.0)
            finally:
                stop.set()

    def _timing(self, port, stop):
        """The engine's beat once a second and the notes about to sound every two: small calls off the mixer."""
        last_onsets = 0.0
        while not stop.is_set():
            try:
                c = _engine_op(port, 'clock')
                self.clock = (c['wall'], c['beat'], c['bpm'], c['bpb'])
                if time.time() - last_onsets > 2.0:
                    self.onsets = _engine_op(port, 'onsets', ahead_s=4.0, behind_s=0.5, limit=200)['onsets']
                    last_onsets = time.time()
            except Exception:
                pass
            stop.wait(1.0)

    def _read(self, port):
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        try:
            c.request('GET', '/stream?name=master')
            r = c.getresponse()
            if r.status != 200:
                raise OSError(f'engine stream {r.status}')
            hop = SR // FPS
            ring = np.zeros(N_FFT, dtype=np.float32)
            buf, got, last_beat = b'', 0, None
            while time.time() < self.until:
                b = r.read1(16384)
                if not b:
                    return
                buf += b
                n = len(buf) // 4
                if n == 0:
                    continue
                pcm = np.frombuffer(buf[:n * 4], dtype='<i2').reshape(-1, 2).astype(np.float32) / 32768.0
                buf = buf[n * 4:]
                mono = pcm.mean(axis=1)
                ring = np.concatenate([ring, mono])[-N_FFT:]
                got += len(mono)
                if got < hop:
                    continue
                got = 0
                f = analyse(ring)
                beat = self.beat_now()
                hits = []
                if beat is not None:
                    lo = last_beat if last_beat is not None else beat - 0.05
                    hits = [{'track': o['t'], 'db': o['l']} for o in self.onsets if lo < o['b'] <= beat]
                    last_beat = beat
                c_ = self.clock
                self._publish({**f, 't': time.time(), 'engine': port, 'beat': round(beat, 3) if beat else None,
                               'bpm': c_[2] if c_ else None, 'bpb': c_[3] if c_ else None, 'hits': hits})
        finally:
            c.close()
