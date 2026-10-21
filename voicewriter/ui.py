"""The bar across the top of the screen."""

import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QMenu, QWidget

from . import appbar

BG = QColor("#1c1c1c")
EDGE = QColor("#2e2e2e")
LIVE = QColor("#4c9aff")
OFF = QColor("#8a8a8a")
WARN = QColor("#f2b84b")
TEXT = QColor("#f2f2f2")
PREVIEW = QColor("#9a9a9a")
HINT = QColor("#6f6f6f")

STATE_LOADING, STATE_LIVE, STATE_OFF = "loading", "live", "off"


class Bar(QWidget):
    toggle_requested = pyqtSignal()
    menu_requested = pyqtSignal(object)

    def __init__(self, height: int, hotkey_label: str):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("VoiceWriter")
        self.bar_height = height
        self.hotkey_label = hotkey_label
        self.state = STATE_LOADING
        self.notice = ""
        self.notice_until = 0.0
        self.alert = ""  # stays until cleared, e.g. no microphone
        self.badge = ""  # small pill on the right, e.g. GPU behind
        self.committed = ""
        self.committed_at = 0.0
        self.preview = ""
        self.level = 0.0
        self.speaking = False
        self._appbar: appbar.AppBar | None = None
        self.font_main = QFont("Segoe UI Variable Text", 12)
        self.font_main.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.font_small = QFont("Segoe UI Variable Text", 10)
        self.font_small.setWeight(QFont.Weight.DemiBold)
        self._tick = QTimer(self)
        self._tick.timeout.connect(self.update)
        self._tick.start(50)

    # Docking -------------------------------------------------------------

    def dock(self) -> None:
        self.show()
        hwnd = int(self.winId())
        appbar.make_no_activate(hwnd)
        self._appbar = appbar.AppBar(hwnd)
        self._appbar.register(self._height_px())

    def redock(self) -> None:
        if self._appbar:
            self._appbar.place(self._height_px())

    def undock(self) -> None:
        if self._appbar:
            self._appbar.remove()

    def _height_px(self) -> int:
        return int(round(self.bar_height * self.devicePixelRatioF()))

    def nativeEvent(self, event_type, message):
        if event_type == b"windows_generic_MSG" and self._appbar:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == appbar.CALLBACK_MSG:
                if msg.wParam == appbar.ABN_POSCHANGED:
                    self.redock()
                elif msg.wParam == appbar.ABN_FULLSCREENAPP:
                    self._appbar.set_topmost(not msg.lParam)
            elif msg.message in (0x007E, 0x02E0):  # WM_DISPLAYCHANGE, WM_DPICHANGED
                QTimer.singleShot(300, lambda: self._appbar and self._appbar.place(self._height_px()))
        return False, 0

    # State from the app -----------------------------------------------------

    def set_state(self, state: str) -> None:
        self.state = state
        if state != STATE_LIVE:
            self.preview = ""
        self.update()

    def show_notice(self, text: str, seconds: float = 6) -> None:
        self.notice = text
        self.notice_until = time.monotonic() + seconds if seconds else float("inf")
        self.update()

    def set_preview(self, text: str) -> None:
        self.preview = text
        self.update()

    def set_committed(self, text: str) -> None:
        if text:
            self.committed = text
            self.committed_at = time.monotonic()
        self.preview = ""
        self.update()

    # Input ---------------------------------------------------------------

    def _mic_rect(self) -> QRectF:
        h = self.height()
        d = h - 12
        return QRectF(10, (h - d) / 2, d, d)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton or self._menu_rect().contains(e.position()):
            self.menu_requested.emit(e.globalPosition().toPoint())
        elif e.button() == Qt.MouseButton.LeftButton:
            self.toggle_requested.emit()

    def _menu_rect(self) -> QRectF:
        h = self.height()
        return QRectF(self.width() - h - 6, 0, h + 6, h)

    # Painting ------------------------------------------------------------

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, BG)
        live = self.state == STATE_LIVE
        p.fillRect(QRectF(0, h - 2, w, 2), LIVE if live else EDGE)

        mic = self._mic_rect()
        self._paint_mic(p, mic, live)

        # Status word and level meter.
        x = mic.right() + 10
        p.setFont(self.font_small)
        status = {STATE_LIVE: "Listening", STATE_OFF: "Muted", STATE_LOADING: "Starting"}[self.state]
        p.setPen(LIVE if live else OFF)
        fm = QFontMetricsF(self.font_small)
        p.drawText(QRectF(x, 0, 90, h), Qt.AlignmentFlag.AlignVCenter, status)
        x += max(fm.horizontalAdvance("Listening"), 60) + 8
        if live:
            self._paint_level(p, QRectF(x, h * 0.3, 22, h * 0.4))
        x += 34

        p.fillRect(QRectF(x - 10, h * 0.25, 1, h * 0.5), EDGE)
        right = self._menu_rect().left() - 8
        if self.badge:
            right = self._paint_badge(p, right, h) - 12
        self._paint_text(p, QRectF(x, 0, right - x, h - 2))
        self._paint_menu_dots(p, self._menu_rect())
        p.end()

    def _paint_mic(self, p: QPainter, r: QRectF, live: bool) -> None:
        if live:
            pulse = min(1.0, self.level * 25) if self.speaking else 0.0
            if pulse > 0.05:
                p.setPen(Qt.PenStyle.NoPen)
                halo = QColor(LIVE)
                halo.setAlphaF(0.25 * pulse)
                p.setBrush(halo)
                p.drawEllipse(r.adjusted(-3, -3, 3, 3))
            p.setBrush(LIVE)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(r)
            fg = QColor("#ffffff")
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(OFF if self.state == STATE_OFF else HINT, 1.5))
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            fg = OFF
        c = r.center()
        s = r.height() / 28
        pen = QPen(fg, 1.8 * s, cap=Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(fg)
        p.drawRoundedRect(QRectF(c.x() - 3.2 * s, c.y() - 8 * s, 6.4 * s, 11 * s), 3.2 * s, 3.2 * s)
        p.setBrush(Qt.BrushStyle.NoBrush)
        arc = QPainterPath()
        arc.arcMoveTo(QRectF(c.x() - 6 * s, c.y() - 6 * s, 12 * s, 12 * s), 200)
        arc.arcTo(QRectF(c.x() - 6 * s, c.y() - 6 * s, 12 * s, 12 * s), 200, 140)
        p.drawPath(arc)
        p.drawLine(QPointF(c.x(), c.y() + 6 * s), QPointF(c.x(), c.y() + 9 * s))
        if not live:
            p.setPen(QPen(fg, 1.8 * s, cap=Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(c.x() - 7 * s, c.y() - 8 * s), QPointF(c.x() + 7 * s, c.y() + 8 * s))

    def _paint_badge(self, p: QPainter, right: float, h: float) -> float:
        """Amber pill on the right, e.g. while the GPU is behind. Returns its left edge."""
        p.setFont(self.font_small)
        fm = QFontMetricsF(self.font_small)
        w = fm.horizontalAdvance(self.badge) + 20
        r = QRectF(right - w, h * 0.2, w, h * 0.6)
        fill = QColor(WARN)
        fill.setAlphaF(0.16)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.setPen(WARN)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.badge)
        return r.left()

    def _paint_level(self, p: QPainter, r: QRectF) -> None:
        level = min(1.0, self.level * 30)
        n = 4
        bw = r.width() / (n * 2 - 1)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(n):
            lit = level > (i + 0.5) / n
            p.setBrush(LIVE if lit else EDGE)
            bh = r.height() * (0.35 + 0.65 * (i + 1) / n)
            p.drawRoundedRect(QRectF(r.left() + i * 2 * bw, r.bottom() - bh, bw, bh), 1, 1)

    def _paint_text(self, p: QPainter, r: QRectF) -> None:
        now = time.monotonic()
        p.setFont(self.font_main)
        fm = QFontMetricsF(self.font_main)
        message = self.notice if self.notice and now < self.notice_until else self.alert
        if message:
            p.setPen(WARN)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter, fm.elidedText(message, Qt.TextElideMode.ElideRight, r.width()))
            return
        if self.state == STATE_LOADING:
            p.setPen(HINT)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter, "Loading speech model. Talk whenever you like; nothing is lost.")
            return
        if self.state == STATE_OFF and not self.preview:
            p.setPen(HINT)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter, f"Say \"unmute\", click here, or press {self.hotkey_label} to start.")
            return

        # Newest words stay visible: committed text dims with age, the live preview trails it.
        age = now - self.committed_at
        committed = self.committed if age < 20 else ""
        preview = self.preview
        if not committed and not preview:
            p.setPen(HINT)
            p.drawText(r, Qt.AlignmentFlag.AlignVCenter, "Start talking.")
            return
        sep = "   " if committed and preview else ""
        pw = fm.horizontalAdvance(preview)
        avail = r.width()
        if pw > avail:
            preview = fm.elidedText(preview, Qt.TextElideMode.ElideLeft, avail)
            committed, sep = "", ""
            pw = fm.horizontalAdvance(preview)
        cw_avail = avail - pw - fm.horizontalAdvance(sep)
        committed = fm.elidedText(committed, Qt.TextElideMode.ElideLeft, cw_avail) if committed else ""
        x = r.left()
        if committed:
            c = QColor(TEXT)
            c.setAlphaF(1.0 if age < 4 else max(0.45, 1.0 - (age - 4) / 16))
            p.setPen(c)
            p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignmentFlag.AlignVCenter, committed)
            x += fm.horizontalAdvance(committed + sep)
        if preview:
            p.setPen(PREVIEW)
            p.drawText(QRectF(x, r.top(), avail, r.height()), Qt.AlignmentFlag.AlignVCenter, preview)

    def _paint_menu_dots(self, p: QPainter, r: QRectF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(OFF)
        c = r.center()
        for dx in (-6, 0, 6):
            p.drawEllipse(QPointF(c.x() + dx, c.y()), 1.6, 1.6)


def build_menu(parent, items: list) -> QMenu:
    """items: (label, callback) | (label, callback, checked) | ("-",) | (label, [submenu items])."""
    menu = QMenu(parent)
    menu.setStyleSheet(
        "QMenu{background:#262626;color:#eee;border:1px solid #3a3a3a;padding:4px;font:10pt 'Segoe UI Variable Text';}"
        "QMenu::item{padding:6px 18px;border-radius:4px;}QMenu::item:selected{background:#3a3a3a;}"
        "QMenu::separator{height:1px;background:#3a3a3a;margin:4px 8px;}")
    for item in items:
        if item[0] == "-":
            menu.addSeparator()
        elif isinstance(item[1], list):
            menu.addMenu(build_menu(menu, item[1])).setText(item[0])
        else:
            act = QAction(item[0], menu)
            if len(item) > 2:
                act.setCheckable(True)
                act.setChecked(item[2])
            act.triggered.connect(item[1])
            menu.addAction(act)
    return menu
