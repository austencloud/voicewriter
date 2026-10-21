"""Checks the three recovery paths without touching the running app.

1. Raw input: real mouse/keyboard events arrive and a click resets spacing.
2. Microphone: a dead stream, a device-change message and a wake-from-sleep
   message each lead to a fresh stream that delivers audio again.
3. GPU backlog: sentences waiting are counted and aged.

Run while using the PC normally (move the mouse, click once) so step 1 has real input:
    .venv\\Scripts\\python.exe tests\\recovery_test.py
"""

import ctypes
import sys
import time

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
import numpy as np  # noqa: E402

from voicewriter.audio import Mic  # noqa: E402
from voicewriter.config import Settings  # noqa: E402
from voicewriter.engine import Transcriber  # noqa: E402
from voicewriter.input_watch import (DBT_DEVNODES_CHANGED, PBT_APMRESUMEAUTOMATIC, RAWINPUT, RAWMOUSE,  # noqa: E402
                                 WM_DEVICECHANGE, WM_POWERBROADCAST, InputWatch)

user32 = ctypes.WinDLL("user32")
failures = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


def wait_for(cond, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def drain(mic: Mic) -> int:
    n = 0
    while not mic.chunks.empty():
        mic.chunks.get()
        n += 1
    return n


# 1. Raw input --------------------------------------------------------------
check(RAWMOUSE.usButtonFlags.offset == 4 and ctypes.sizeof(RAWINPUT) == 48, "raw input struct layout matches Windows")
events = {"devices": 0, "resume": 0}
watch = InputWatch("ctrl+shift+f24", lambda: None,
                   on_devices_changed=lambda: events.__setitem__("devices", events["devices"] + 1),
                   on_resume=lambda: events.__setitem__("resume", events["resume"] + 1))
check(watch.raw_ok, "raw input registered")
watch.need_space = True
print("   move the mouse and click once somewhere (10 s)...")
got_click = wait_for(lambda: watch.stats["clicks"] > 0, 10)
print(f"   saw {watch.stats}")
if watch.stats["mouse"] or watch.stats["keys"]:
    check(True, "real mouse/keyboard events arrive")
else:
    print("SKIP real input (nobody touched the mouse or keyboard)")
if got_click:
    check(not watch.need_space, "a click resets spacing")
else:
    print("SKIP click reset (no click in 10 s)")

user32.PostMessageW(watch.hwnd, WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, 0)
user32.PostMessageW(watch.hwnd, WM_POWERBROADCAST, PBT_APMRESUMEAUTOMATIC, 0)
check(wait_for(lambda: events["devices"] == 1 and events["resume"] == 1, 2), "device-change and wake messages reach the app")

# 2. Microphone -------------------------------------------------------------
mic = Mic()
mic.start()
check(wait_for(lambda: not mic.chunks.empty(), 3), f"mic delivers audio ({mic.device_name})")

for label, breaker in [
    ("dead stream", lambda: mic._stream.abort()),
    ("device change", lambda: mic.request_reopen("test device change", 0.2)),
    ("wake from sleep", lambda: mic.request_reopen("test wake", 0.2)),
]:
    gen = mic.generation
    t = time.time()
    breaker()
    reopened = wait_for(lambda: mic.generation > gen, 8)
    drain(mic)
    flowing = wait_for(lambda: not mic.chunks.empty(), 3)
    check(reopened and flowing, f"{label}: mic reopened and audio flows again in {time.time() - t:.1f}s")
mic.stop()

# 3. GPU backlog ------------------------------------------------------------
tr = Transcriber(Settings(), lambda *a: None, lambda *a: None, lambda *a: None)  # never started: nothing drains
for i in range(3):
    tr.submit_final(i + 1, np.zeros(16000, np.float32), 0.9)
time.sleep(0.3)
count, age = tr.waiting()
check(count == 3 and age >= 0.3, f"backlog counted: {count} waiting, oldest {age:.1f}s")

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
