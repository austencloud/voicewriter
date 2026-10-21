"""Starts VoiceWriter and brings it back if it ever crashes.

Quitting from the bar's menu exits with code 0, which ends this loop too.
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
sys.path.insert(0, ROOT)
from voicewriter.config import DATA_DIR  # noqa: E402  (importing it also moves data from the old "Dictate" name)

LOG = os.path.join(DATA_DIR, "launcher.log")


def note(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


crashes: list[float] = []
while True:
    started = time.time()
    code = subprocess.call([PYTHONW, "-m", "voicewriter"], cwd=ROOT)
    if code == 0:
        note("exited normally")
        break
    note(f"crashed with code {code}, restarting")
    crashes = [t for t in crashes if time.time() - t < 60] + [time.time()]
    time.sleep(30 if len(crashes) >= 5 else 1)
sys.exit(0)
