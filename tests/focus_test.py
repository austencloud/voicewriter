r"""Checks that dictation only types into a Chrome page when a text field has focus.

Open tests/focus_page.html in Chrome (or any Chromium browser) in its own window,
click another window, then run:
    .venv\Scripts\python.exe tests\focus_test.py
The test brings that window forward. The page then moves focus through its body,
text fields, a read-only field and a button, and the test sends a real sentence
through the Typer at each step. Text should land only in the four editable fields.
At the end the page shows what each field got and how far it scrolled (should be 0).
"""

import ctypes
import logging
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
from voicewriter import focus, inject  # noqa: E402
from voicewriter.inject import Typer  # noqa: E402

user32 = ctypes.windll.user32
EXPECT = [("body", False), ("input", True), ("textarea", True), ("contenteditable", True),
          ("role=textbox", True), ("readonly input", False), ("button", False)]


def find_window(title_part):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        if title_part in buf.value and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def bring_forward(hwnd):
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    me = ctypes.windll.kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(me, fg_thread, True)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    user32.AttachThreadInput(me, fg_thread, False)
    return user32.GetForegroundWindow() == hwnd


class Watch:
    def should_space(self):
        return False

    def typed_dictation(self):
        results.append("typed")


logging.basicConfig(level=logging.DEBUG, format="  %(name)s: %(message)s")
logging.getLogger("comtypes").setLevel(logging.WARNING)
results = []
hwnd = find_window("VWPROBE")
if not hwnd or not bring_forward(hwnd):
    sys.exit("FAIL could not bring the VWPROBE window forward")
# If Chrome's address bar or tab strip has focus, F6 cycles it into the page, where the test happens.
for _ in range(6):
    if focus.client().GetFocusedElement().CurrentControlType == 50030:  # the page document
        break
    user32.keybd_event(0x75, 0, 0, 0)
    user32.keybd_event(0x75, 0, 2, 0)
    time.sleep(0.3)
user32.keybd_event(0x87, 0, 0, 0)  # F24 starts the page's focus steps
user32.keybd_event(0x87, 0, 2, 0)
real_send = inject.send_text


def guarded_send(text):
    # Never type anywhere but the test page, even if focus moves mid-test.
    if user32.GetForegroundWindow() != hwnd:
        print("FAIL the VWPROBE window lost focus; stopped before typing", flush=True)
        os._exit(1)
    return real_send(text)


inject.send_text = guarded_send
typer = Typer(lambda: 0, Watch(), lambda text, reason: results.append(reason))

time.sleep(2.25)  # land halfway through each 1.5 s focus step
failures = 0
for name, editable in EXPECT:
    results.clear()
    typer.type("one two three four five six seven eight. ")
    time.sleep(1.0)
    got = results[:1] == ["typed"]
    ok = got == editable and (editable or results[:1] == ["no_field"])
    failures += not ok
    print(("PASS " if ok else "FAIL ") + f"{name}: {results}")
    time.sleep(0.5)
sys.exit(1 if failures else 0)
