import sys
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
s = app.primaryScreen(); g = s.geometry()
print("screen", g.width(), g.height(), "dpr", s.devicePixelRatio())
s.grabWindow(0, 0, 0, g.width(), 120).save(sys.argv[1])
