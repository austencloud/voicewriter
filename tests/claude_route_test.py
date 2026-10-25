r"""Checks that a sentence said with no text box selected goes to the Claude window.

Uses stand-ins, so the real Claude app is never typed into. Open both pages in
Chrome, each in its own window:
    tests/claude_page.html   (title VWCLAUDE, a message box named "Prompt")
    tests/reader_page.html   (title VWREADER, a long page with no text box)
then run:
    .venv\Scripts\python.exe tests\claude_route_test.py
The test puts the two windows side by side on the left monitor, makes the reader
active, and dictates two sentences. They should land in the stand-in's box with
the reader active again afterwards. Then it covers the stand-in with the reader
and dictates once more: that sentence should go to the clipboard instead.
"""

import ctypes
import logging
import os
import sys
import time

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
from voicewriter import claude_target, focus, inject  # noqa: E402
from voicewriter.claude_target import ClaudeTarget, _title  # noqa: E402
from voicewriter.inject import Typer  # noqa: E402

user32 = ctypes.windll.user32
logging.basicConfig(level=logging.INFO, format="  %(name)s: %(message)s")
logging.getLogger("comtypes").setLevel(logging.WARNING)
failures = 0


def check(ok, what):
    global failures
    failures += not ok
    print(("PASS " if ok else "FAIL ") + what, flush=True)


stand_in = claude_target.find_window(lambda h: "VWCLAUDE" in _title(h))
reader = claude_target.find_window(lambda h: "VWREADER" in _title(h))
if not stand_in or not reader:
    sys.exit("FAIL open claude_page.html and reader_page.html in their own Chrome windows first")


def place(hwnd, x, y, w, h):
    user32.ShowWindow(hwnd, 9)  # restore, in case it's maximized
    user32.MoveWindow(hwnd, x, y, w, h, True)


def make_active(hwnd):
    claude_target._activate(hwnd)
    # F6 cycles Chrome's focus from the address bar or tabs into the page.
    for _ in range(6):
        if focus.client().GetFocusedElement().CurrentControlType == 50030:  # the page document
            return
        user32.keybd_event(0x75, 0, 0, 0)
        user32.keybd_event(0x75, 0, 2, 0)
        time.sleep(0.3)


def guarded_send(text):
    # Never type anywhere but the stand-in, even if focus moves mid-test.
    if user32.GetForegroundWindow() != stand_in:
        print("FAIL focus wasn't on the stand-in; stopped before typing", flush=True)
        os._exit(1)
    return inject.send_text(text)


target = ClaudeTarget(match=lambda h: h == stand_in)
outcomes = []
typer = Typer(lambda: 0, type("W", (), {"should_space": lambda s: False, "typed_dictation": lambda s: None})(),
              lambda text, reason: outcomes.append(("clipboard", reason)),
              lambda text: target.send(text, guarded_send) and (outcomes.append(("claude", text)) or True))

# Side by side on the left monitor, reader active with nothing selected.
place(stand_in, -2560, 0, 1280, 1400)
place(reader, -1280, 0, 1280, 1400)
claude_target._activate(stand_in)
make_active(reader)
time.sleep(0.3)

typer.type("First sentence.")
typer.type("Second sentence.")
time.sleep(2.5)
check([o[0] for o in outcomes] == ["claude", "claude"], f"both sentences went to the stand-in: {outcomes}")
check(user32.GetForegroundWindow() == reader, "the reader window is active again")
box = target._find_box(stand_in)
value = target._value(box).strip() if box else ""
check(value == "First sentence. Second sentence.", f"stand-in box holds {value!r}")

# Covered: the reader sits on top of the stand-in, so it isn't in view.
outcomes.clear()
place(reader, -2560, 0, 1400, 1400)
make_active(reader)
time.sleep(0.3)
typer.type("Third sentence.")
time.sleep(1.5)
check(outcomes == [("clipboard", "no_field")], f"covered stand-in: sentence went to the clipboard: {outcomes}")
sys.exit(1 if failures else 0)
