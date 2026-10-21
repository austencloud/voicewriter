"""Global hotkey plus a light watch on your own typing and clicks.

The watch only answers one question: should the next dictated sentence start
with a space? After dictation or a typed letter, yes. After Enter, Tab, Space,
Esc or switching windows, no. Nothing is recorded or stored. Only the keyboard
is watched: a mouse hook would sit in the path of every mouse move.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger("dictate.input")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN, WM_HOTKEY, WM_QUIT = 0x0100, 0x0104, 0x0312, 0x0012
LLKHF_INJECTED = 0x10
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "alt": MOD_ALT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "capslock": 0x14,
         **{f"f{i}": 0x6F + i for i in range(1, 25)}}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD)
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = (wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetForegroundWindow.restype = wt.HWND
user32.GetKeyState.restype = ctypes.c_short
kernel32.GetModuleHandleW.restype = wt.HMODULE


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif part in _KEYS:
            vk = _KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        else:
            raise ValueError(f"unknown key {part!r}")
    if vk is None:
        raise ValueError("hotkey needs a key")
    return mods | MOD_NOREPEAT, vk


def _printable(vk: int) -> bool:
    return 0x30 <= vk <= 0x5A or 0x60 <= vk <= 0x6F or 0xBA <= vk <= 0xE2


class InputWatch:
    def __init__(self, hotkey: str, on_hotkey):
        self.on_hotkey = on_hotkey
        self.hotkey = hotkey
        self.hotkey_ok = False
        self.need_space = False
        self._fg_at_last = None
        self._thread_id = None
        self._kb = HOOKPROC(self._kb_proc)
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="input-watch", daemon=True).start()
        self._ready.wait(3)

    def typed_dictation(self) -> None:
        self.need_space = True
        self._fg_at_last = user32.GetForegroundWindow()

    def should_space(self) -> bool:
        return self.need_space and user32.GetForegroundWindow() == self._fg_at_last

    def _kb_proc(self, code, wparam, lparam):
        if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            k = KBDLLHOOKSTRUCT.from_address(lparam)
            if not k.flags & LLKHF_INJECTED:
                ctrl_or_alt = (user32.GetKeyState(0x11) & 0x8000) or (user32.GetKeyState(0x12) & 0x8000)
                if k.vkCode in (0x0D, 0x09, 0x20, 0x1B):  # Enter, Tab, Space, Esc
                    self.need_space = False
                elif _printable(k.vkCode) and not ctrl_or_alt:
                    self.need_space = True
                    self._fg_at_last = user32.GetForegroundWindow()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb, hmod, 0)
        if not kb:
            log.warning("input hooks unavailable; spacing falls back to per-window")
        try:
            mods, vk = parse_hotkey(self.hotkey)
            self.hotkey_ok = bool(user32.RegisterHotKey(None, 1, mods, vk))
            if not self.hotkey_ok:
                log.warning("hotkey %s is taken by another program", self.hotkey)
        except ValueError as e:
            log.error("bad hotkey %r: %s", self.hotkey, e)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                try:
                    self.on_hotkey()
                except Exception:
                    log.exception("hotkey handler failed")
