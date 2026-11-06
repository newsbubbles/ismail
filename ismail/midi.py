"""MIDI ports: the keyboards, pads, synths and virtual ports (loopMIDI) this computer has, a listener on an input
and a sender on an output.

Windows uses winmm (no extra library). Elsewhere mido with python-rtmidi is used when installed.
sources() lists the inputs by name; Input(name, on_msg) opens one and calls on_msg(t, status, d1, d2) from the
driver's thread for every short message (t: time.perf_counter() at arrival), until close(). When on_msg has a
`sysex` attribute, SysEx comes in too: on_msg.sysex(t, data) with the whole message, F0 to F7 (a full-frame
timecode, for one).
outputs() lists the outputs; Output(name) opens one: send(msg) puts a short message (1 to 3 bytes: a note, a
controller, a clock pulse) or a SysEx (F0 ... F7) on it at once, reset() turns every note off, close() resets and
lets go of it.
find_output(name) picks an output by its name or a unique part of it.
"""
import sys
import time

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


class MidiError(RuntimeError):
    pass


INSTALL = "MIDI ports here need mido and python-rtmidi: pip install mido python-rtmidi"


def backend_missing():
    """None when this computer can reach its MIDI ports, else what to install (off Windows, without mido)."""
    if sys.platform == 'win32':
        return None
    try:
        import mido  # noqa: F401
        import rtmidi  # noqa: F401
    except ImportError:
        return INSTALL
    return None


def find_output(name, names=None):
    """The output `name` names: itself, or the one output whose name holds it (any case). Unknown or ambiguous:
    MidiError listing the outputs."""
    names = outputs() if names is None else list(names)
    if name in names:
        return name
    hits = [n for n in names if str(name).lower() in n.lower()]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise MidiError(f"MIDI output {name!r} matches {hits}; name one")
    miss = backend_missing()
    raise MidiError(f"no MIDI output {name!r}; outputs: {names or 'none'}" + (f" ({miss})" if miss and not names else
                    " (a loopMIDI port, a synth's USB MIDI, or the IAC bus on a Mac shows up here)"))


def _short(msg):
    b = bytes(msg)
    if not 1 <= len(b) <= 3:
        raise MidiError(f"a short MIDI message is 1 to 3 bytes, got {len(b)}")
    return b


def _sysex(msg):
    b = bytes(msg)
    if len(b) < 2 or b[0] != 0xF0 or b[-1] != 0xF7 or any(x > 0x7F for x in b[1:-1]):
        raise MidiError("a SysEx message starts with F0, ends with F7, and its bytes between are 0 to 127")
    return b


SYSEX_BUFS, SYSEX_LEN = 4, 1024     # Windows: input buffers queued for SysEx, and the bytes each takes


def note_name(n):
    return f"{NAMES[n % 12]}{n // 12 - 1}"


if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    _w = ctypes.windll.winmm
    _MIM_DATA = 0x3C3
    _CALLBACK_FUNCTION = 0x30000
    _CB = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)

    class _INCAPS(ctypes.Structure):
        _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD), ('vDriverVersion', wintypes.UINT),
                    ('szPname', wintypes.WCHAR * 32), ('dwSupport', wintypes.DWORD)]

    class _OUTCAPS(ctypes.Structure):
        _fields_ = [('wMid', wintypes.WORD), ('wPid', wintypes.WORD), ('vDriverVersion', wintypes.UINT),
                    ('szPname', wintypes.WCHAR * 32), ('wTechnology', wintypes.WORD), ('wVoices', wintypes.WORD),
                    ('wNotes', wintypes.WORD), ('wChannelMask', wintypes.WORD), ('dwSupport', wintypes.DWORD)]

    _w.midiOutOpen.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.UINT, ctypes.c_size_t, ctypes.c_size_t,
                               wintypes.DWORD]
    _w.midiOutShortMsg.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _w.midiOutReset.argtypes = [wintypes.HANDLE]
    _w.midiOutClose.argtypes = [wintypes.HANDLE]

    class _MIDIHDR(ctypes.Structure):
        _fields_ = [('lpData', ctypes.c_void_p), ('dwBufferLength', wintypes.DWORD),
                    ('dwBytesRecorded', wintypes.DWORD), ('dwUser', ctypes.c_size_t), ('dwFlags', wintypes.DWORD),
                    ('lpNext', ctypes.c_void_p), ('reserved', ctypes.c_size_t), ('dwOffset', wintypes.DWORD),
                    ('dwReserved', ctypes.c_size_t * 8)]

    _PHDR = ctypes.POINTER(_MIDIHDR)
    _MHDR_DONE = 0x1
    _MIM_LONGDATA = 0x3C4
    for _f in ('midiOutPrepareHeader', 'midiOutUnprepareHeader', 'midiOutLongMsg', 'midiInPrepareHeader',
               'midiInUnprepareHeader', 'midiInAddBuffer'):
        getattr(_w, _f).argtypes = [wintypes.HANDLE, _PHDR, wintypes.UINT]

    def outputs():
        out = []
        for i in range(_w.midiOutGetNumDevs()):
            c = _OUTCAPS()
            if _w.midiOutGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
                out.append(c.szPname)
        return out

    class Output:
        def __init__(self, name):
            self._long = []                 # SysEx buffers the driver may still be reading: (buffer, header)
            names = outputs()
            if name not in names:
                raise MidiError(f"no MIDI output {name!r}; outputs: {names or 'none'}")
            self.name = name
            self._h = wintypes.HANDLE()
            r = _w.midiOutOpen(ctypes.byref(self._h), names.index(name), 0, 0, 0)       # CALLBACK_NULL
            if r:
                self._h = None
                raise MidiError(f"could not open MIDI output {name!r} (winmm error {r}): another program may have "
                                f"it open, or the driver lets one program at a time use it. Close it there, or use "
                                f"another port (loopMIDI ports take several)")

        def send(self, msg):
            if bytes(msg[:1]) == b'\xf0':
                self._send_sysex(_sysex(msg))
                return
            b = _short(msg)
            if self._h:
                _w.midiOutShortMsg(self._h, b[0] | (b[1] << 8 if len(b) > 1 else 0) | (b[2] << 16 if len(b) > 2 else 0))

        def _send_sysex(self, b):
            """midiOutLongMsg: the driver sends it from the buffer; the buffer is let go once it is done."""
            if not self._h:
                return
            self._reap()
            buf = ctypes.create_string_buffer(b, len(b))
            hd = _MIDIHDR()
            hd.lpData, hd.dwBufferLength = ctypes.cast(buf, ctypes.c_void_p), len(b)
            r = _w.midiOutPrepareHeader(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
            if r:
                raise MidiError(f"SysEx to {self.name!r} did not go out (winmm error {r})")
            r = _w.midiOutLongMsg(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
            if r:
                _w.midiOutUnprepareHeader(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
                raise MidiError(f"SysEx to {self.name!r} did not go out (winmm error {r})")
            self._long.append((buf, hd))

        def _reap(self):
            keep = []
            for buf, hd in self._long:
                if hd.dwFlags & _MHDR_DONE:
                    _w.midiOutUnprepareHeader(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
                else:
                    keep.append((buf, hd))
            self._long = keep

        def reset(self):
            if self._h:
                _w.midiOutReset(self._h)

        def close(self):
            if self._h:
                _w.midiOutReset(self._h)          # marks every SysEx buffer still queued as done
                self._reap()
                _w.midiOutClose(self._h)
                self._h = None

    def sources():
        out = []
        for i in range(_w.midiInGetNumDevs()):
            c = _INCAPS()
            if _w.midiInGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
                out.append(c.szPname)
        return out

    class Input:
        def __init__(self, name, on_msg):
            names = sources()
            if name not in names:
                raise MidiError(f"no MIDI input {name!r}; inputs: {names or 'none'}")
            self.name = name
            self._on = on_msg
            self._sx = getattr(on_msg, 'sysex', None)
            self._bufs, self._part, self._closing = [], b'', False

            def cb(h, msg, inst, p1, p2):
                if msg == _MIM_DATA:
                    try:
                        self._on(time.perf_counter(), p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF)
                    except Exception:
                        pass
                elif msg == _MIM_LONGDATA:
                    try:
                        self._long_in(p1)
                    except Exception:
                        pass
            self._cb = _CB(cb)              # kept: the driver calls it until close
            self._h = wintypes.HANDLE()
            r = _w.midiInOpen(ctypes.byref(self._h), names.index(name), self._cb, 0, _CALLBACK_FUNCTION)
            if r:
                raise MidiError(f"could not open MIDI input {name!r} (winmm error {r}): another program may have it "
                                f"open. Close it there, or use another port")
            if self._sx is not None:        # SysEx comes in only into buffers queued for it
                for _ in range(SYSEX_BUFS):
                    buf = ctypes.create_string_buffer(SYSEX_LEN)
                    hd = _MIDIHDR()
                    hd.lpData, hd.dwBufferLength = ctypes.cast(buf, ctypes.c_void_p), SYSEX_LEN
                    if _w.midiInPrepareHeader(self._h, ctypes.byref(hd), ctypes.sizeof(hd)) == 0:
                        _w.midiInAddBuffer(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
                        self._bufs.append((buf, hd))
            _w.midiInStart(self._h)

        def _long_in(self, p1):
            """A SysEx buffer came back full (or with a message's end): its bytes, then the buffer queued again."""
            t = time.perf_counter()
            ph = ctypes.cast(ctypes.c_void_p(p1), _PHDR)
            hd = ph.contents
            data = ctypes.string_at(hd.lpData, hd.dwBytesRecorded) if hd.dwBytesRecorded else b''
            if self._closing:
                return
            _w.midiInAddBuffer(self._h, ph, ctypes.sizeof(_MIDIHDR))
            if not data or self._sx is None:
                return
            self._part = (self._part + data)[-65536:]
            if self._part.endswith(b'\xf7'):
                whole, self._part = self._part, b''
                if whole.startswith(b'\xf0'):
                    self._sx(t, whole)

        def close(self):
            if self._h:
                self._closing = True
                _w.midiInStop(self._h)
                _w.midiInReset(self._h)       # hands every queued SysEx buffer back
                for _, hd in self._bufs:
                    _w.midiInUnprepareHeader(self._h, ctypes.byref(hd), ctypes.sizeof(hd))
                self._bufs = []
                _w.midiInClose(self._h)
                self._h = None
else:
    def outputs():
        try:
            import mido
            return list(mido.get_output_names())
        except Exception:
            return []

    class Output:
        def __init__(self, name):
            self._port = None
            try:
                import mido
                self._mido = mido
                self._port = mido.open_output(name)
            except ImportError:
                raise MidiError(INSTALL)
            except (OSError, IOError) as e:
                raise MidiError(f"could not open MIDI output {name!r}: {e}; outputs: {outputs() or 'none'}")
            self.name = name

        def send(self, msg):
            b = _sysex(msg) if bytes(msg[:1]) == b'\xf0' else _short(msg)
            if self._port is not None:
                self._port.send(self._mido.Message.from_bytes(list(b)))

        def reset(self):
            if self._port is not None:
                self._port.reset()

        def close(self):
            if self._port is not None:
                self._port.reset()
                self._port.close()
                self._port = None

    def sources():
        try:
            import mido
            return list(mido.get_input_names())
        except Exception:
            return []

    class Input:
        def __init__(self, name, on_msg):
            try:
                import mido
                self._port = mido.open_input(name, callback=lambda m: self._msg(m))
            except ImportError:
                raise MidiError(INSTALL)
            except (OSError, IOError) as e:
                raise MidiError(f"could not open MIDI input {name!r}: {e}; inputs: {sources() or 'none'}")
            self.name = name
            self._on = on_msg

        def _msg(self, m):
            b = m.bytes()
            if b and b[0] == 0xF0:
                sx = getattr(self._on, 'sysex', None)
                if sx is not None:
                    sx(time.perf_counter(), bytes(b))
                return
            if 1 <= len(b) <= 3:
                b = b + [0] * (3 - len(b))
                self._on(time.perf_counter(), b[0], b[1], b[2])

        def close(self):
            if self._port is not None:
                self._port.close()
                self._port = None
