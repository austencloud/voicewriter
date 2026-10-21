"""Registers a window as a Windows app bar docked to the top of a monitor.

This is the same mechanism Voice Access and the taskbar use: Windows reserves the
strip, and maximized windows shrink to sit below it instead of under it.
"""

import ctypes
import ctypes.wintypes as wt

shell32 = ctypes.WinDLL("shell32")
user32 = ctypes.WinDLL("user32")

ABM_NEW, ABM_REMOVE, ABM_QUERYPOS, ABM_SETPOS = 0, 1, 2, 3
ABE_TOP = 1
ABN_POSCHANGED, ABN_FULLSCREENAPP = 1, 2
HWND_TOPMOST, HWND_NOTOPMOST = wt.HWND(-1), wt.HWND(-2)
SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x10, 0x2, 0x1, 0x40
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_TOPMOST = 0x80, 0x08000000, 0x8
MONITOR_DEFAULTTOPRIMARY = 1


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uCallbackMessage", wt.UINT), ("uEdge", wt.UINT),
                ("rc", wt.RECT), ("lParam", wt.LPARAM)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


shell32.SHAppBarMessage.argtypes = (wt.DWORD, ctypes.POINTER(APPBARDATA))
shell32.SHAppBarMessage.restype = ctypes.c_size_t
user32.RegisterWindowMessageW.restype = wt.UINT
user32.MonitorFromPoint.argtypes = (wt.POINT, wt.DWORD)
user32.MonitorFromPoint.restype = wt.HMONITOR
user32.GetMonitorInfoW.argtypes = (wt.HMONITOR, ctypes.POINTER(MONITORINFO))
user32.SetWindowPos.argtypes = (wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT)
user32.GetWindowLongPtrW.argtypes = (wt.HWND, ctypes.c_int)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = (wt.HWND, ctypes.c_int, ctypes.c_ssize_t)
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t

CALLBACK_MSG = user32.RegisterWindowMessageW("VoiceWriterAppBarMessage")


def make_no_activate(hwnd: int) -> None:
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)


def primary_monitor_rect() -> wt.RECT:
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromPoint(wt.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY), ctypes.byref(info))
    return info.rcMonitor


class AppBar:
    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.registered = False

    def _data(self) -> APPBARDATA:
        d = APPBARDATA()
        d.cbSize = ctypes.sizeof(APPBARDATA)
        d.hWnd = self.hwnd
        return d

    def register(self, height_px: int) -> None:
        if not self.registered:
            d = self._data()
            d.uCallbackMessage = CALLBACK_MSG
            self.registered = bool(shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(d)))
        self.place(height_px)

    def place(self, height_px: int) -> None:
        mon = primary_monitor_rect()
        d = self._data()
        d.uEdge = ABE_TOP
        d.rc = wt.RECT(mon.left, mon.top, mon.right, mon.top + height_px)
        shell32.SHAppBarMessage(ABM_QUERYPOS, ctypes.byref(d))
        d.rc.bottom = d.rc.top + height_px
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(d))
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST, d.rc.left, d.rc.top, d.rc.right - d.rc.left, height_px,
                            SWP_NOACTIVATE | SWP_SHOWWINDOW)

    def set_topmost(self, on: bool) -> None:
        user32.SetWindowPos(self.hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0,
                            SWP_NOACTIVATE | SWP_NOMOVE | SWP_NOSIZE)

    def remove(self) -> None:
        if self.registered:
            shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self._data()))
            self.registered = False
