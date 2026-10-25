"""Sends a sentence to the Claude desktop app's message box when it can't be typed where you are.

That's when nothing editable has focus (you're reading or scrolling a page, or
clicked the desktop) and the Claude window is open and mostly in view on some
monitor. Claude is brought forward just long enough to type the sentence into
its message box, then the window you were in gets focus back.

The message box keeps its own cursor, so a sentence lands wherever you left it.
"""

import ctypes
import ctypes.wintypes as wt
import logging
import os
import time

from . import focus

log = logging.getLogger("voicewriter.claude")
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetForegroundWindow.restype = wt.HWND
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = (wt.POINT,)
user32.GetAncestor.restype = wt.HWND
kernel32.OpenProcess.restype = wt.HANDLE

GA_ROOT = 2
VK_LBUTTON, VK_RBUTTON = 0x01, 0x02
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _exe_name(hwnd) -> str:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    proc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not proc:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(buf))
        kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value).lower()
    finally:
        kernel32.CloseHandle(proc)


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def _rect(hwnd) -> wt.RECT:
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def is_claude(hwnd) -> bool:
    return _exe_name(hwnd) == "claude.exe"


def find_window(match=is_claude):
    """The largest visible, not minimized window that `match` accepts."""
    best, best_area = None, 0

    @WNDENUMPROC
    def visit(hwnd, _):
        nonlocal best, best_area
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and _title(hwnd) and match(hwnd):
            r = _rect(hwnd)
            area = (r.right - r.left) * (r.bottom - r.top)
            if area > best_area:
                best, best_area = hwnd, area
        return True

    user32.EnumWindows(visit, 0)
    return best


def _mostly_visible(hwnd) -> bool:
    """True when at least 6 of 9 points spread over the window aren't covered by another window."""
    r = _rect(hwnd)
    seen = 0
    for fx in (0.2, 0.5, 0.8):
        for fy in (0.2, 0.5, 0.8):
            pt = wt.POINT(int(r.left + (r.right - r.left) * fx), int(r.top + (r.bottom - r.top) * fy))
            hit = user32.WindowFromPoint(pt)
            seen += bool(hit) and user32.GetAncestor(hit, GA_ROOT) == hwnd
    return seen >= 6


def _activate(hwnd) -> bool:
    # Windows only lets the app you're using change the foreground window, so borrow its input state.
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return True
    theirs = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    mine = kernel32.GetCurrentThreadId()
    attached = bool(theirs) and theirs != mine and user32.AttachThreadInput(mine, theirs, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(mine, theirs, False)
    return user32.GetForegroundWindow() == hwnd


class ClaudeTarget:
    def __init__(self, match=is_claude, box_name="Prompt"):
        self._match = match
        self._box_name = box_name
        self._box = None
        self._box_hwnd = None

    def _find_box(self, hwnd):
        U = focus.uia_types()
        if self._box_hwnd == hwnd and self._box is not None:
            try:
                r = self._box.CurrentBoundingRectangle
                if r.right > r.left and not self._box.CurrentIsOffscreen:
                    return self._box
            except Exception:
                pass  # the page under it changed; look it up again
        uia = focus.client()
        cond = uia.CreateAndCondition(
            uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_EditControlTypeId),
            uia.CreatePropertyCondition(U.UIA_NamePropertyId, self._box_name))
        found = uia.ElementFromHandle(hwnd).FindAll(U.TreeScope_Descendants, cond)
        self._box = self._box_hwnd = None
        for i in range(found.Length):
            el = found.GetElement(i)
            if not el.CurrentIsOffscreen:
                self._box, self._box_hwnd = el, hwnd
                break
        return self._box

    def _value(self, box) -> str:
        U = focus.uia_types()
        vp = box.GetCurrentPattern(U.UIA_ValuePatternId).QueryInterface(U.IUIAutomationValuePattern)
        return vp.CurrentValue or ""

    def window(self):
        """The Claude window if it can take a sentence right now, else None."""
        hwnd = find_window(self._match)
        if not hwnd:
            return None
        if hwnd != user32.GetForegroundWindow() and not _mostly_visible(hwnd):
            return None
        return hwnd

    def send(self, text: str, send_text) -> bool:
        """Types `text` into Claude's message box and gives focus back. False if nothing was typed."""
        try:
            hwnd = self.window()
            box = hwnd and self._find_box(hwnd)
            if not box:
                return False
            # Don't pull focus away in the middle of a drag.
            deadline = time.monotonic() + 3
            while (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000 or user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000):
                if time.monotonic() > deadline:
                    return False
                time.sleep(0.03)
            before = self._value(box).rstrip("\n")
            if before and not before[-1].isspace():
                text = " " + text
            back_to = user32.GetForegroundWindow()
            if not _activate(hwnd):
                log.info("couldn't bring Claude forward")
                return False
            box.SetFocus()
            if not box.CurrentHasKeyboardFocus:
                time.sleep(0.05)
                if not box.CurrentHasKeyboardFocus:
                    log.info("Claude's message box didn't take focus")
                    self._give_back(back_to, hwnd)
                    return False
            if not send_text(text):
                self._give_back(back_to, hwnd)
                return False
            # Keys still waiting in the queue would go to whatever has focus next, so wait for them to land.
            want = len(before) + len(text) - 2
            deadline = time.monotonic() + 2
            while len(self._value(box).rstrip("\n")) < want and time.monotonic() < deadline:
                time.sleep(0.02)
            self._give_back(back_to, hwnd)
            log.info("sent to Claude (%d chars)", len(text))
            return True
        except Exception:
            log.exception("sending to Claude failed")
            return False

    @staticmethod
    def _give_back(back_to, hwnd) -> None:
        if back_to and back_to != hwnd:
            _activate(back_to)
