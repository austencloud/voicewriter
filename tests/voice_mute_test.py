"""Spoken "mute" / "unmute" through the real Controller, with recorded speech instead of the mic.

Plays: a sentence, "Mute.", a sentence said while muted, "Unmute.", then a sentence that
contains the word mute. Expects the two sentences typed, nothing from the muted one, the
commands never typed, and nothing heard while muted saved to history.
    .venv\\Scripts\\python.exe tests\\voice_mute_test.py
"""

import queue
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
import voicewriter.engine as engine  # noqa: E402

_tmp = Path(tempfile.mkdtemp(prefix="voicewriter-test-"))
engine.HISTORY_DIR, engine.AUDIO_DIR = _tmp / "history", _tmp / "audio"

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from voicewriter.app import Controller  # noqa: E402
from voicewriter.engine import command_of  # noqa: E402

AUDIO = Path(__file__).parent / "audio"
failures = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


for text, want in [("Mute.", "mute"), ("unmute", "unmute"), ("Un-mute!", "unmute"), ("UNMUTE.", "unmute"),
                   ("I need to mute the TV.", None), ("Mute the TV.", None), ("", None), ("Immune.", None)]:
    check(command_of(text) == want, f"command_of({text!r}) -> {want}")


def clip(name: str) -> np.ndarray:
    with wave.open(str(AUDIO / f"{name}.wav")) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


def gap(s: float) -> np.ndarray:
    return np.zeros(int(16000 * s), np.float32)


# After "Unmute." the next sentence starts 0.8 s later: just past the 0.7 s pause that ends
# "Unmute.", so it's already under way when the command is recognised. It must still be typed.
script = [("cmd_s1", 1.6), ("cmd_mute", 1.6), ("cmd_hidden", 1.6), ("cmd_unmute", 0.8), ("cmd_s2", 1.6)]
audio = np.concatenate([gap(1.6)] + [x for name, pause in script for x in (clip(name), gap(pause))] + [gap(1.6)])


class FakeMic:
    def __init__(self):
        self.chunks = queue.SimpleQueue()
        self.generation = 0
        self.level = 0.0
        self.error = None

    def stop(self):
        pass


app = QApplication(sys.argv)
ctl = Controller(app)
ctl.listening = ctl._utt_live = True
ctl.mic = FakeMic()
typed: list[str] = []
states: list[tuple[float, bool]] = []
ctl.typer.type = typed.append
orig_toggle = ctl.toggle


def toggle(**kw):
    orig_toggle(**kw)
    states.append((round(time.time() - t0, 1), ctl.listening))


ctl.sig.toggle.disconnect()
ctl.sig.command.disconnect()
ctl.toggle = toggle
ctl.sig.command.connect(ctl._on_command)
ctl.transcriber.start()
ctl.transcriber.ready.wait()
threading.Thread(target=ctl._pump, daemon=True).start()
finished = threading.Event()
t0 = time.time()


def play():
    for i in range(0, len(audio), 512):
        ctl.mic.chunks.put(audio[i:i + 512])
        delay = t0 + (i + 512) / 16000 - time.time()
        if delay > 0:
            time.sleep(delay)
    time.sleep(3)
    finished.set()


threading.Thread(target=play, daemon=True).start()
poll = QTimer()
poll.timeout.connect(lambda: finished.is_set() and app.quit())
poll.start(100)
app.exec()

print("typed:", typed)
print("listening changes (seconds in, on?):", states)
joined = " ".join(typed).lower()
check(len(typed) == 2, "exactly two sentences typed")
check("first sentence" in joined, "sentence before mute typed")
check("mute the tv before the show" in joined, "sentence started right after 'unmute' typed whole")
check("should not" not in joined, "sentence said while muted not typed")
check(all(command_of(t) is None for t in typed), "commands never typed")
check([on for _, on in states] == [False, True], "muted by voice, then unmuted by voice")
history = "\n".join(p.read_text(encoding="utf-8") for p in (_tmp / "history").glob("*.txt"))
check("should not" not in history.lower(), "nothing heard while muted is saved to history")
print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
