"""Global hotkey, a light watch on your own typing and clicks, and system events.

The watch only answers one question: should the next dictated sentence start
with a space? After dictation or a typed letter, yes. After Enter, Tab, Space,
Esc, a mouse click or switching windows, no. Nothing is recorded or stored.

It uses Raw Input rather than keyboard/mouse hooks. Hooks sit in the path of
every key and mouse move, so a busy Python process could make the whole system
lag; Raw Input only gets a copy of each event afterwards, so it can never slow
your keyboard or mouse down. Input we typed ourselves arrives with no device
and is ignored.

The same hidden window hears about devices being plugged in or out and the PC
waking from sleep, which is when the microphone needs reopening.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import threading

log = logging.getLogger("voicewriter.input")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY, WM_INPUT, WM_DEVICECHANGE, WM_POWERBROADCAST = 0x0312, 0x00FF, 0x0219, 0x0218
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
DBT_DEVNODES_CHANGED, DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE = 0x0007, 0x8000, 0x8004
PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND = 0x0012, 0x0007
RIM_TYPEMOUSE, RIM_TYPEKEYBOARD = 0, 1
RID_INPUT, RIDEV_INPUTSINK = 0x10000003, 0x00000100
RI_MOUSE_BUTTON_DOWNS = 0x0001 | 0x0004 | 0x0010  # left, right, middle
GA_ROOT = 2
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "shift": MOD_SHIFT, "alt": MOD_ALT, "win": MOD_WIN}
_KEYS = {"space": 0x20, "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "capslock": 0x14,
         **{f"f{i}": 0x6F + i for i in range(1, 25)}}

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON),
                ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT), ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD), ("hDevice", wt.HANDLE), ("wParam", wt.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    # usButtonFlags sits inside a ULONG union, so it starts at offset 4, not 2.
    _fields_ = [("usFlags", wt.USHORT), ("_pad", wt.USHORT), ("usButtonFlags", wt.USHORT), ("usButtonData", wt.USHORT),
                ("ulRawButtons", wt.ULONG), ("lLastX", wt.LONG), ("lLastY", wt.LONG), ("ulExtraInformation", wt.ULONG)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [("MakeCode", wt.USHORT), ("Flags", wt.USHORT), ("Reserved", wt.USHORT), ("VKey", wt.USHORT),
                ("Message", wt.UINT), ("ExtraInformation", wt.ULONG)]


class _RAWDATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWDATA)]


user32.DefWindowProcW.argtypes = (wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = (ctypes.POINTER(WNDCLASSEXW),)
user32.CreateWindowExW.argtypes = (wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID)
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wt.UINT, wt.UINT)
user32.GetRawInputData.argtypes = (wt.HANDLE, wt.UINT, wt.LPVOID, ctypes.POINTER(wt.UINT), wt.UINT)
user32.GetRawInputData.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.WindowFromPoint.argtypes = (wt.POINT,)
user32.WindowFromPoint.restype = wt.HWND
user32.GetAncestor.argtypes = (wt.HWND, wt.UINT)
user32.GetAncestor.restype = wt.HWND
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


def _held(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


class InputWatch:
    def __init__(self, hotkey: str, on_hotkey, own_hwnd=lambda: None, on_devices_changed=lambda: None,
                 on_resume=lambda: None):
        self.on_hotkey = on_hotkey
        self.own_hwnd = own_hwnd
        self.on_devices_changed = on_devices_changed
        self.on_resume = on_resume
        self.hotkey = hotkey
        self.hotkey_ok = False
        self.raw_ok = False
        self.need_space = False
        self.hwnd = None
        self.stats = {"mouse": 0, "clicks": 0, "keys": 0}  # counts only, for tests and the log
        self._fg_at_last = None
        self._proc = WNDPROC(self._wndproc)
        self._buf = ctypes.create_string_buffer(ctypes.sizeof(RAWINPUT) + 64)
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="input-watch", daemon=True).start()
        self._ready.wait(3)

    def typed_dictation(self) -> None:
        self.need_space = True
        self._fg_at_last = user32.GetForegroundWindow()

    def should_space(self) -> bool:
        return self.need_space and user32.GetForegroundWindow() == self._fg_at_last

    # Events --------------------------------------------------------------

    def _on_raw(self, handle) -> None:
        size = wt.UINT(len(self._buf))
        if user32.GetRawInputData(handle, RID_INPUT, self._buf, ctypes.byref(size),
                                  ctypes.sizeof(RAWINPUTHEADER)) in (0, 0xFFFFFFFF):
            return
        raw = RAWINPUT.from_buffer(self._buf)
        if not raw.header.hDevice:
            return  # injected, i.e. our own typing
        if raw.header.dwType == RIM_TYPEMOUSE:
            self.stats["mouse"] += 1
            if raw.data.mouse.usButtonFlags & RI_MOUSE_BUTTON_DOWNS:
                self.stats["clicks"] += 1
                if not self._cursor_on_own_window():
                    self.need_space = False
        elif raw.header.dwType == RIM_TYPEKEYBOARD:
            k = raw.data.keyboard
            if k.Message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
                return
            self.stats["keys"] += 1
            if k.VKey in (0x0D, 0x09, 0x20, 0x1B):  # Enter, Tab, Space, Esc
                self.need_space = False
            elif _printable(k.VKey) and not (_held(0x11) or _held(0x12) or _held(0x5B) or _held(0x5C)):
                self.need_space = True
                self._fg_at_last = user32.GetForegroundWindow()

    def _cursor_on_own_window(self) -> bool:
        own = self.own_hwnd()
        if not own:
            return False
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        hit = user32.WindowFromPoint(pt)
        return bool(hit) and user32.GetAncestor(hit, GA_ROOT) == own

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_INPUT:
                self._on_raw(lparam)
            elif msg == WM_HOTKEY:
                self.on_hotkey()
            elif msg == WM_DEVICECHANGE and wparam in (DBT_DEVNODES_CHANGED, DBT_DEVICEARRIVAL,
                                                       DBT_DEVICEREMOVECOMPLETE):
                self.on_devices_changed()
            elif msg == WM_POWERBROADCAST and wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
                log.info("woke from sleep")
                self.on_resume()
        except Exception:
            log.exception("input watch handler failed")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = "VoiceWriterInputWatch"
        user32.RegisterClassExW(ctypes.byref(wc))
        # A hidden top-level window (not message-only) so it also gets device and power broadcasts.
        self.hwnd = user32.CreateWindowExW(0x80, "VoiceWriterInputWatch", "VoiceWriter input watch", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        devices = (RAWINPUTDEVICE * 2)(RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, self.hwnd),
                                       RAWINPUTDEVICE(0x01, 0x06, RIDEV_INPUTSINK, self.hwnd))
        self.raw_ok = bool(user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE)))
        if not self.raw_ok:
            log.warning("raw input unavailable (error %d); spacing falls back to per-window",
                        ctypes.get_last_error())
        try:
            mods, vk = parse_hotkey(self.hotkey)
            self.hotkey_ok = bool(user32.RegisterHotKey(self.hwnd, 1, mods, vk))
            if not self.hotkey_ok:
                log.warning("hotkey %s is taken by another program", self.hotkey)
        except ValueError as e:
            log.error("bad hotkey %r: %s", self.hotkey, e)
        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
