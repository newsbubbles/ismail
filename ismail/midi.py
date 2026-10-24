"""MIDI ports: the keyboards, pads, synths and virtual ports (loopMIDI) this computer has, a listener on an input
and a sender on an output.

Windows uses winmm (no extra library). Elsewhere mido with python-rtmidi is used when installed.
sources() lists the inputs by name; Input(name, on_msg) opens one and calls on_msg(t, status, d1, d2) from the
driver's thread for every short message (t: time.perf_counter() at arrival), until close().
outputs() lists the outputs; Output(name) opens one: send(msg) puts a short message (1 to 3 bytes: a note, a
controller, a clock pulse) on it at once, reset() turns every note off, close() resets and lets go of it.
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

    def outputs():
        out = []
        for i in range(_w.midiOutGetNumDevs()):
            c = _OUTCAPS()
            if _w.midiOutGetDevCapsW(i, ctypes.byref(c), ctypes.sizeof(c)) == 0:
                out.append(c.szPname)
        return out

    class Output:
        def __init__(self, name):
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
            b = _short(msg)
            if self._h:
                _w.midiOutShortMsg(self._h, b[0] | (b[1] << 8 if len(b) > 1 else 0) | (b[2] << 16 if len(b) > 2 else 0))

        def reset(self):
            if self._h:
                _w.midiOutReset(self._h)

        def close(self):
            if self._h:
                _w.midiOutReset(self._h)
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

            def cb(h, msg, inst, p1, p2):
                if msg == _MIM_DATA:
                    try:
                        self._on(time.perf_counter(), p1 & 0xFF, (p1 >> 8) & 0xFF, (p1 >> 16) & 0xFF)
                    except Exception:
                        pass
            self._cb = _CB(cb)              # kept: the driver calls it until close
            self._h = wintypes.HANDLE()
            r = _w.midiInOpen(ctypes.byref(self._h), names.index(name), self._cb, 0, _CALLBACK_FUNCTION)
            if r:
                raise MidiError(f"could not open MIDI input {name!r} (winmm error {r}): another program may have it "
                                f"open. Close it there, or use another port")
            _w.midiInStart(self._h)

        def close(self):
            if self._h:
                _w.midiInStop(self._h)
                _w.midiInReset(self._h)
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
            b = _short(msg)
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
            if 1 <= len(b) <= 3:
                b = b + [0] * (3 - len(b))
                self._on(time.perf_counter(), b[0], b[1], b[2])

        def close(self):
            if self._port is not None:
                self._port.close()
                self._port = None
