"""Renders the bar to PNGs: normal, GPU-busy badge, and the no-microphone alert.

    python tests\\render_live.py out_dir
"""
import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

app = QApplication(sys.argv)
from voicewriter.ui import STATE_LIVE, Bar  # noqa: E402

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
b = Bar(40, "Ctrl+Shift+Space")
b.resize(1700, 40)
b.state = STATE_LIVE
b.level = 0.03
b.speaking = True
b.set_committed("So here's the thing I keep running into when I practice with the staff.")
b.preview = "I start the routine in the box position, then I move into a wall"
b.grab().save(str(out / "live.png"))
b.badge = "GPU busy · 3 waiting"
b.grab().save(str(out / "busy.png"))
b.badge = ""
b.preview = ""
b.committed = ""
b.speaking = False
b.level = 0.0
b.alert = "No microphone found. Plug one in; VoiceWriter picks it up by itself."
b.grab().save(str(out / "no_mic.png"))
