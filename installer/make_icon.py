"""Draws voicewriter.ico: a blue microphone on the bar's dark gray. Run it after changing the design."""

import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

BG = QColor("#1c1c1c")
LIVE = QColor("#4c9aff")
SIZES = (16, 24, 32, 48, 64, 256)


def draw(size: int) -> bytes:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    back = QPainterPath()
    back.addRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    p.fillPath(back, BG)
    p.setBrush(LIVE)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(QRectF(24, 10, 16, 28), 8, 8)  # capsule
    pen = QPen(LIVE, 4.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(17, 18, 30, 28), 180 * 16, 180 * 16)  # cradle
    p.drawLine(32, 46, 32, 53)  # stand
    p.drawLine(24, 53, 40, 53)  # base
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def main() -> None:
    QGuiApplication(sys.argv)
    pngs = [draw(s) for s in SIZES]
    out = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    for size, png in zip(SIZES, pngs):
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    path = Path(__file__).with_name("voicewriter.ico")
    path.write_bytes(out + b"".join(pngs))
    Path(__file__).with_name("voicewriter-256.png").write_bytes(pngs[-1])
    print(path)


if __name__ == "__main__":
    main()
