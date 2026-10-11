"""Minimal ctypes wrapper over teVirtualMIDI64.dll, the driver loopMIDI installs.

A virtual port is visible to every WinMM client as both an input and an output.
Creating it is a plug and closing it an unplug, as Windows sees them, so a test
can make a device appear and vanish and can inject or capture MIDI bytes without
hardware. Windows only.
"""

import ctypes
import threading
from ctypes import wintypes

TE_VM_FLAGS_PARSE_RX = 1
TE_VM_FLAGS_INSTANTIATE_BOTH = 12

_dll = ctypes.WinDLL('teVirtualMIDI64.dll')
CB = ctypes.WINFUNCTYPE(
    None, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), wintypes.DWORD, ctypes.c_void_p
)
_dll.virtualMIDICreatePortEx2.restype = ctypes.c_void_p
_dll.virtualMIDICreatePortEx2.argtypes = [
    wintypes.LPCWSTR, CB, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
]
_dll.virtualMIDIClosePort.argtypes = [ctypes.c_void_p]
_dll.virtualMIDISendData.restype = wintypes.BOOL
_dll.virtualMIDISendData.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), wintypes.DWORD,
]
_dll.virtualMIDIGetDriverVersion.restype = wintypes.LPCWSTR
_dll.virtualMIDIGetDriverVersion.argtypes = [ctypes.POINTER(wintypes.WORD)] * 4


def driver_version() -> str:
    w = [wintypes.WORD() for _ in range(4)]
    _dll.virtualMIDIGetDriverVersion(*[ctypes.byref(x) for x in w])
    return '.'.join(str(x.value) for x in w)


class VirtualPort:
    """A software MIDI device; ``received`` collects what is sent to it."""

    def __init__(self, name: str):
        self.name = name
        self.received: list[list[int]] = []
        self._lock = threading.Lock()
        self._cb = CB(self._on_data)  # keep a reference for the driver's thread
        self._h = _dll.virtualMIDICreatePortEx2(
            name, self._cb, None, 65535, TE_VM_FLAGS_PARSE_RX | TE_VM_FLAGS_INSTANTIATE_BOTH
        )
        if not self._h:
            raise OSError(ctypes.get_last_error() or 'virtualMIDICreatePortEx2 failed')

    def _on_data(self, port, data, length, instance):
        if data and length:
            with self._lock:
                self.received.append([data[i] for i in range(length)])

    def send(self, msg: list[int]) -> bool:
        buf = (ctypes.c_ubyte * len(msg))(*msg)
        return bool(_dll.virtualMIDISendData(self._h, buf, len(msg)))

    def close(self) -> None:
        if self._h:
            _dll.virtualMIDIClosePort(self._h)
            self._h = None
