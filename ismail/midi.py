"""MIDI inputs: the keyboards, pads and virtual ports (loopMIDI) this computer has, and a listener on one of them.

Windows reads them through winmm (no extra library). Elsewhere mido with python-rtmidi is used when installed.
sources() lists the inputs by name; Input(name, on_msg) opens one and calls on_msg(t, status, d1, d2) from the
driver's thread for every short message (t: time.perf_counter() at arrival), until close().
"""
import sys
import time

NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


class MidiError(RuntimeError):
    pass


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
                raise MidiError("MIDI input here needs mido and python-rtmidi: pip install mido python-rtmidi")
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
