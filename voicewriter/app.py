"""Wires the microphone, speech engine, typing and the bar together."""

import ctypes
import faulthandler
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
import winreg
from datetime import datetime

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QApplication

from .audio import Mic
from .config import DATA_DIR, HISTORY_DIR, LOG_PATH, WORDS_PATH, Settings, read_words, write_words
from .engine import COMMAND_MAX_S, Segmenter, Transcriber, prune_audio
from .guard import close_rivals, running_rivals
from .claude_target import ClaudeTarget
from .inject import Typer, send_text
from .input_watch import InputWatch
from .ui import STATE_LIVE, STATE_LOADING, STATE_OFF, Bar, build_menu
from .vad import StreamingVad

log = logging.getLogger("voicewriter")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def setup_logging() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr:
        root.addHandler(logging.StreamHandler(sys.stderr))
    faulthandler.enable(open(DATA_DIR / "crash.log", "a", encoding="utf-8"))
    sys.excepthook = lambda *exc: log.critical("unhandled", exc_info=exc)
    threading.excepthook = lambda a: log.critical("thread %s crashed", a.thread, exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def startup_command() -> str:
    pythonw = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    return f'"{pythonw}" "{os.path.join(ROOT, "launch.pyw")}"'


def starts_with_windows() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, "VoiceWriter")[0])
    except OSError:
        return False


def set_starts_with_windows(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, "VoiceWriter", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, "VoiceWriter")
            except OSError:
                pass


def move_old_startup_entry() -> None:
    """VoiceWriter used to be called Dictate; swap its startup entry for the new one."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            winreg.QueryValueEx(k, "Dictate")
            winreg.DeleteValue(k, "Dictate")
    except OSError:
        return
    set_starts_with_windows(True)


class Signals(QObject):
    preview = pyqtSignal(int, str)
    final = pyqtSignal(int, str)
    mark = pyqtSignal(int, str)
    status = pyqtSignal(str)
    failed_typing = pyqtSignal(str, str)
    toggle = pyqtSignal()
    resumed = pyqtSignal()
    command = pyqtSignal(str)


class Controller:
    def __init__(self, app: QApplication):
        self.app = app
        self.settings = Settings.load()
        self.sig = Signals()
        self.listening = self.settings.listen_on_start
        self._flush_pending = False
        self._discard_pending = False
        self._utt_live = self.listening  # whether the utterance in progress started while listening
        self.last_text = ""

        label = "+".join(part.capitalize() for part in self.settings.hotkey.split("+"))
        self.bar = Bar(self.settings.bar_height, label)
        self.bar.toggle_requested.connect(self.toggle)
        self.bar.menu_requested.connect(self.show_menu)
        self.sig.preview.connect(self._on_preview)
        self.sig.final.connect(self._on_final)
        self.sig.mark.connect(self._on_mark)
        self.sig.status.connect(self._on_status)
        self.sig.failed_typing.connect(self._on_failed_typing)
        self.sig.toggle.connect(self.toggle)
        self.sig.command.connect(self._on_command)
        self.sig.resumed.connect(lambda: QTimer.singleShot(2000, self.bar.redock))
        self._bar_hwnd = 0
        self._behind = False
        self._words_mtime = 0.0
        self._clip_run, self._clip_run_at = "", 0.0
        self._sync_words(announce=False)

        self.mic = Mic(self.settings.microphone)
        self.watch = InputWatch(self.settings.hotkey, self.sig.toggle.emit, own_hwnd=lambda: self._bar_hwnd,
                                on_devices_changed=lambda: self.mic.request_reopen("devices changed"),
                                on_resume=self._on_resume)
        self.claude = ClaudeTarget()
        self.typer = Typer(lambda: int(self.bar.winId()), self.watch, self.sig.failed_typing.emit,
                           lambda text: self.settings.send_to_claude and self.claude.send(text, send_text))
        self.transcriber = Transcriber(self.settings, self.sig.preview.emit, self.sig.final.emit, self.sig.status.emit,
                                       self.sig.command.emit, self.sig.mark.emit, lambda: self.segmenter.speaking)
        self.segmenter = Segmenter(self.settings, StreamingVad(), self._on_utterance, self._on_partial)
        self._health = QTimer()
        self._health.timeout.connect(self._check_health)

    def start(self) -> None:
        self.bar.dock()
        self._bar_hwnd = int(self.bar.winId())
        self._health.start(500)
        self.bar.set_state(STATE_LOADING)
        if not self.watch.hotkey_ok:
            self.bar.show_notice(f"{self.bar.hotkey_label} is used by another program; click the bar instead.", 10)
        self.transcriber.start()
        self.mic.start()
        threading.Thread(target=self._pump, name="pump", daemon=True).start()
        threading.Thread(target=self._wait_ready, name="ready", daemon=True).start()
        threading.Thread(target=self._guard, name="guard", daemon=True).start()
        prune_audio(self.settings.keep_audio_days)

    def _wait_ready(self) -> None:
        self.transcriber.ready.wait()
        self.sig.status.emit("")
        self._refresh_state()

    def _refresh_state(self) -> None:
        if not self.transcriber.ready.is_set():
            state = STATE_LOADING
        else:
            state = STATE_LIVE if self.listening else STATE_OFF
        # Called from threads too; the bar repaints on its own timer.
        self.bar.state = state
        if state != STATE_LIVE:
            self.bar.preview = ""

    def _pump(self) -> None:
        """Moves audio from the microphone into the segmenter. Never blocks the microphone."""
        generation = self.mic.generation
        while True:
            try:
                chunk = self.mic.chunks.get(timeout=0.2)
            except queue.Empty:
                chunk = None
            if self.mic.generation != generation:
                # The mic was reopened (unplug, sleep): type what was cut off instead of holding it.
                generation = self.mic.generation
                self._flush_pending = True
            if self._flush_pending:
                self._flush_pending = False
                self.segmenter.flush()
            if self._discard_pending:
                # Unmuted mid-speech: drop what was said while muted, start fresh from here.
                self._discard_pending = False
                self.segmenter.discard()
            self.bar.level = self.mic.level
            self.bar.speaking = self.segmenter.speaking and self.listening
            if chunk is not None:
                if not self.segmenter.speaking:
                    self._utt_live = self.listening
                self.segmenter.feed(chunk)

    def _on_utterance(self, utt_id: int, audio, vad_prob: float) -> None:
        if self._utt_live:
            self.transcriber.submit_final(utt_id, audio, vad_prob, info=self.segmenter.last_info)
        elif len(audio) <= COMMAND_MAX_S * 16000:
            self.transcriber.submit_final(utt_id, audio, vad_prob, muted=True)

    def _on_partial(self, utt_id: int, audio) -> None:
        if self._utt_live and self.listening:
            self.transcriber.submit_preview(utt_id, audio)

    def _on_command(self, cmd: str) -> None:
        if (cmd == "mute") == self.listening:
            log.info("voice: %s", cmd)
            # Anything you started saying after "unmute" (while it was being recognised) is kept.
            self.toggle(keep_speech=True)

    def _guard(self) -> None:
        """Every 2 s, deal with Voice Access or Talon if one has started: close it, or warn once."""
        warned: set[int] = set()
        while True:
            try:
                if self.settings.close_other_dictation:
                    closed = close_rivals()
                    if closed:
                        self.sig.status.emit(f"Closed {' and '.join(closed)} so your words aren't typed twice.")
                else:
                    rivals = running_rivals()
                    new = [name for pid, name in rivals if pid not in warned]
                    warned = {pid for pid, _ in rivals}
                    if new:
                        self.sig.status.emit(f"{' and '.join(sorted(set(new)))} is also listening, so words may be typed "
                                             "twice. Right-click here to have VoiceWriter close it.")
            except Exception:
                log.exception("guard check failed")
            time.sleep(2)

    def _toggle_send_to_claude(self) -> None:
        self.settings.send_to_claude = not self.settings.send_to_claude
        self.settings.save()

    def _toggle_close_others(self) -> None:
        self.settings.close_other_dictation = not self.settings.close_other_dictation
        self.settings.save()

    def _on_resume(self) -> None:
        self.mic.request_reopen("woke from sleep", 2.0)
        self.sig.resumed.emit()

    def _sync_words(self, announce: bool = True) -> None:
        """Picks up the word list whenever its file is saved."""
        try:
            if not WORDS_PATH.exists():
                write_words(self.settings.vocabulary)
            mtime = WORDS_PATH.stat().st_mtime
            if mtime == self._words_mtime:
                return
            self._words_mtime = mtime
            words = read_words()
        except OSError:
            return
        if words != self.settings.vocabulary:
            self.settings.vocabulary = words
            self.settings.save()
            log.info("word list updated: %d entries", len(words))
            if announce:
                self.bar.show_notice(f"Word list updated ({len(words)} words).", 3)

    def _check_health(self) -> None:
        self._sync_words()
        self.bar.alert = self.mic.error or ""
        if not self.transcriber.ready.is_set():
            return
        count, age = self.transcriber.waiting()
        behind = count >= 2 or age > 3.0
        if behind:
            self.bar.badge = f"GPU busy · {count} waiting" if count > 1 else "GPU busy · catching up"
        else:
            self.bar.badge = ""
        if behind != self._behind:
            self._behind = behind
            log.info("GPU %s (%d waiting, oldest %.1fs)", "behind" if behind else "caught up", count, age)

    def toggle(self, keep_speech: bool = False) -> None:
        self.listening = not self.listening
        if not self.listening:
            self._flush_pending = True  # whatever you were saying still gets typed
        elif keep_speech:
            self._utt_live = True
        elif not self._utt_live:
            self._discard_pending = True
        log.info("listening %s", "on" if self.listening else "off")
        self._refresh_state()
        self.bar.update()

    def _on_preview(self, utt_id: int, text: str) -> None:
        if self.listening:
            self.bar.set_preview(text)

    def _on_final(self, utt_id: int, text: str) -> None:
        self.bar.set_committed(text)
        if text:
            self.last_text = text
            self.typer.type(text)

    def _on_mark(self, utt_id: int, mark: str) -> None:
        if mark:
            self.last_text += mark
            self.typer.type_mark(mark)

    def _on_status(self, text: str) -> None:
        # A message ending in "…" is ongoing work (a model download) and stays until the next status.
        if text:
            self.bar.show_notice(text, 0 if text.endswith("…") else 8)
        elif self.bar.notice.endswith("…"):
            self.bar.show_notice("", 0.01)

    def _on_failed_typing(self, text: str, reason: str) -> None:
        clipboard = QGuiApplication.clipboard()
        if reason != "no_field":
            clipboard.setText(text)
            self.bar.show_notice("Couldn't type into that window, so the text is on your clipboard. Paste with Ctrl+V.", 8)
            return
        # Several sentences said before clicking into a box all go on the clipboard together.
        recent = time.monotonic() - self._clip_run_at < 120 and clipboard.text() == self._clip_run
        self._clip_run = f"{self._clip_run} {text}" if recent else text
        self._clip_run_at = time.monotonic()
        clipboard.setText(self._clip_run)
        self.bar.show_notice("No text box selected, so it went to your clipboard. Click a box and press Ctrl+V.", 8)

    def show_menu(self, pos) -> None:
        mics = Mic.input_devices()
        current = self.settings.microphone
        mic_items = [("Windows default", lambda: self._set_mic(None), current is None)]
        mic_items += [(name, (lambda n=name: self._set_mic(n)), current == name) for name in mics]
        items = [
            ("Copy last sentence", self._copy_last),
            ("Open today's history", self._open_history),
            ("Edit word list…", lambda: os.startfile(WORDS_PATH)),
            ("-",),
            ("Microphone", mic_items),
            ("Start when Windows starts", lambda: set_starts_with_windows(not starts_with_windows()),
             starts_with_windows()),
            ("Send to Claude when no text box is selected", self._toggle_send_to_claude,
             self.settings.send_to_claude),
            ("Close Voice Access and Talon automatically", self._toggle_close_others,
             self.settings.close_other_dictation),
            ("-",),
            ("Quit VoiceWriter", self.quit),
        ]
        build_menu(self.bar, items).exec(pos)

    def _set_mic(self, name) -> None:
        self.settings.microphone = name
        self.settings.save()
        self.mic.set_device(name)

    def _copy_last(self) -> None:
        if self.last_text:
            QGuiApplication.clipboard().setText(self.last_text)
            self.bar.show_notice("Copied.", 2)

    def _open_history(self) -> None:
        path = HISTORY_DIR / f"{datetime.now():%Y-%m-%d}.txt"
        os.startfile(path if path.exists() else HISTORY_DIR if HISTORY_DIR.exists() else DATA_DIR)

    def quit(self) -> None:
        log.info("quit from menu")
        self.bar.undock()
        self.mic.stop()
        # Exit code 0 tells the launcher this was on purpose, so it doesn't restart.
        os._exit(0)


def single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "Local\\VoiceWriter")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def main() -> int:
    setup_logging()
    if not single_instance():
        log.info("already running")
        return 0
    move_old_startup_entry()
    sys.setswitchinterval(0.001)  # keeps the keyboard watch snappy while the GPU thread is busy
    log.info("starting")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    ctl = Controller(app)
    ctl.start()
    app.aboutToQuit.connect(ctl.bar.undock)
    code = app.exec()
    ctl.bar.undock()
    return code
