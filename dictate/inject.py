"""Types finished text into whatever window has focus.

Uses SendInput with Unicode key events: no clipboard, no shortcuts, works in
browsers, Electron apps, Office and terminals. Text goes out in one SendInput
call so your own typing can't interleave with it.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import queue
import threading
import time

log = logging.getLogger("dictate.inject")
user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


user32.SendInput.argtypes = (wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
kernel32.OpenProcess.restype = wt.HANDLE


def _elevated(pid: int) -> bool:
    """True when the process runs as administrator."""
    TOKEN_QUERY, TokenElevation = 0x0008, 20
    proc = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        return True  # can't even inspect it, so we won't be able to type into it either
    token = wt.HANDLE()
    try:
        if not advapi32.OpenProcessToken(proc, TOKEN_QUERY, ctypes.byref(token)):
            return True
        value, size = wt.DWORD(), wt.DWORD()
        advapi32.GetTokenInformation(token, TokenElevation, ctypes.byref(value), 4, ctypes.byref(size))
        return bool(value.value)
    finally:
        if token:
            kernel32.CloseHandle(token)
        kernel32.CloseHandle(proc)


_self_elevated = _elevated(kernel32.GetCurrentProcessId())


def _blocked(hwnd) -> bool:
    if _self_elevated:
        return False
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _elevated(pid.value)


def _modifiers_down() -> bool:
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN))


def send_text(text: str) -> bool:
    units = text.encode("utf-16-le")
    codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
    events = (INPUT * (len(codes) * 2))()
    for i, code in enumerate(codes):
        for j, flags in enumerate((KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)):
            ev = events[i * 2 + j]
            ev.type = INPUT_KEYBOARD
            ev.u.ki = KEYBDINPUT(0, code, flags, 0, 0)
    sent = user32.SendInput(len(events), events, ctypes.sizeof(INPUT))
    if sent != len(events):
        log.error("SendInput sent %d of %d events (error %d)", sent, len(events), ctypes.get_last_error())
        return False
    return True


class Typer:
    """Types sentences in order on its own thread, spacing them like one paragraph."""

    def __init__(self, own_hwnd_getter, watch, on_failed):
        self._own = own_hwnd_getter
        self._watch = watch
        self._on_failed = on_failed
        self._q: "queue.SimpleQueue[str]" = queue.SimpleQueue()
        threading.Thread(target=self._run, name="typer", daemon=True).start()

    def type(self, text: str) -> None:
        self._q.put(text)

    def _run(self) -> None:
        while True:
            text = self._q.get()
            try:
                self._type_one(text)
            except Exception:
                log.exception("typing failed")
                self._on_failed(text)

    def _type_one(self, text: str) -> None:
        # Wait for you to let go of Ctrl/Alt/Win so the letters don't become shortcuts.
        deadline = time.monotonic() + 5
        while _modifiers_down() and time.monotonic() < deadline:
            time.sleep(0.02)
        hwnd = user32.GetForegroundWindow()
        if not hwnd or hwnd == self._own() or _blocked(hwnd):
            self._on_failed(text)
            return
        if self._watch.should_space():
            text = " " + text
        if send_text(text):
            self._watch.typed_dictation()
        else:
            self._on_failed(text.strip())
