import sys, time
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from dictate.ui import Bar, STATE_LIVE
b = Bar(40, "Ctrl+Shift+Space"); b.resize(1700, 40)
b.state = STATE_LIVE; b.level = 0.03; b.speaking = True
b.set_committed("So here's the thing I keep running into when I practice with the staff.")
b.preview = "I start the routine in the box position, then I move into a wall"
b.grab().save(sys.argv[1])
