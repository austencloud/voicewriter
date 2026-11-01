"""The remote microphone: a phone streams its microphone to VoiceWriter and gets the words back.

Each connection gets its own voice detector and Segmenter, so its speech is cut into sentences exactly like the
PC microphone's, and its sentences go through the same Transcriber (same models, prompt and held marks) on a lane
of their own. Nothing a phone says is typed on the PC, sent to Claude or written to the PC's history; it has a
history of its own in the "remote" folder, with each sentence's audio in "remote/audio".

The protocol is plain TCP, so a phone needs nothing beyond its standard library:

  phone → PC   One JSON line first: {"token": "...", "rate": 16000, "device": "fold6"}. Then, in any order:
               audio frames, a 4-byte big-endian length and that many bytes of 16 kHz mono 16-bit PCM
               (little-endian), and control lines, {"cmd": "stop"} (finish the sentence in progress; "done" follows
               once everything is sent) or {"cmd": "discard"} (drop it). A control line starts with "{", which no
               frame length can (it would mean a frame over 2 GB).
  PC → phone   JSON lines: {"event": "ready"}, {"event": "preview", "utt": n, "text": ...},
               {"event": "final", "utt": n, "text": ..., "held": true|false, "mark": "."} (held: the closing mark,
               given in "mark", is left off and settled later),
               {"event": "mark", "utt": n, "mark": "."} (the held mark, settled: "." "?" "!" "," or ""),
               {"event": "idle"} (15 s without speech), {"event": "done"} (after stop) and {"event": "error", "message": ...}.
"""

import hmac
import json
import logging
import logging.handlers
import queue
import re
import socket
import sys
import threading
import time
from datetime import datetime

import numpy as np

from .config import DATA_DIR, LOG_PATH, Settings
from .engine import RATE, Lane, Segmenter, Transcriber, _write_wav
from .vad import StreamingVad

log = logging.getLogger("voicewriter.remote")
REMOTE_HISTORY_DIR = DATA_DIR / "remote"
REMOTE_AUDIO_DIR = REMOTE_HISTORY_DIR / "audio"  # each sentence's audio, kept keep_audio_days like the PC's
IDLE_MS = 15000
MAX_FRAME = 1 << 20
MAX_CONNECTIONS = 4
HELLO_TIMEOUT_S = 5
READ_TIMEOUT_S = 30  # a phone streams all the time; this much silence on the socket means it is gone
RETRY_BIND_S = 30


class RemoteMic:
    def __init__(self, settings: Settings, transcriber: Transcriber, port: int | None = None):
        self.settings = settings
        self.transcriber = transcriber
        self.port = port or settings.remote_port
        self._count = 0
        self.streaming = 0  # connections past the hello, sending audio
        self._lock = threading.Lock()
        self.bound = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, name="remote-mic", daemon=True).start()

    def listen(self) -> socket.socket:
        # Not SO_REUSEADDR: on Windows that would let two programs share the port.
        return socket.create_server(("0.0.0.0", self.port), backlog=8)

    def serve_forever(self, server: socket.socket | None = None) -> None:
        warned = False
        while server is None:
            try:
                server = self.listen()
            except OSError as e:
                if not warned:
                    log.error("remote microphone can't listen on port %d (%s); retrying every %d s",
                              self.port, e, RETRY_BIND_S)
                    warned = True
                time.sleep(RETRY_BIND_S)
        log.info("remote microphone listening on port %d", self.port)
        self.bound.set()
        while True:
            sock, addr = server.accept()
            with self._lock:
                full = self._count >= MAX_CONNECTIONS
                if not full:
                    self._count += 1
            if full:
                log.warning("remote microphone: refused %s, %d phones already connected", addr[0], MAX_CONNECTIONS)
                sock.close()
                continue
            threading.Thread(target=self._handle, args=(sock, addr), name=f"remote-{addr[0]}", daemon=True).start()

    def _handle(self, sock: socket.socket, addr) -> None:
        try:
            Connection(self, sock, addr).run()
        except Exception:
            log.exception("remote microphone: connection from %s failed", addr[0])
        finally:
            with self._lock:
                self._count -= 1


class Connection:
    def __init__(self, mic: RemoteMic, sock: socket.socket, addr):
        self.mic = mic
        self.sock = sock
        self.addr = addr[0]
        self.device = self.addr
        self._out: "queue.SimpleQueue[bytes | None]" = queue.SimpleQueue()
        self._stopped = False
        self._idle_sent = False

    def send(self, event: dict) -> None:
        """From any thread, never blocking: the transcriber thread must not wait on a slow phone."""
        self._out.put((json.dumps(event) + "\n").encode("utf-8"))

    def _write(self) -> None:
        while (data := self._out.get()) is not None:
            try:
                self.sock.sendall(data)
            except OSError:
                return

    def run(self) -> None:
        self.sock.settimeout(HELLO_TIMEOUT_S)
        reader = self.sock.makefile("rb")
        writer = threading.Thread(target=self._write, name=f"remote-out-{self.addr}", daemon=True)
        writer.start()
        lane = None
        started = time.monotonic()
        try:
            line = reader.readline(4096)
            hello = _hello(line)
            if hello is None:
                if line.strip():  # a phone probing which address answers connects and closes: not worth a line
                    log.info("remote microphone: %s sent no hello, closing", self.addr)
                return
            token = self.mic.settings.remote_token
            if not token or not hmac.compare_digest(str(hello.get("token", "")).encode(), token.encode()):
                log.warning("remote microphone: wrong token from %s", self.addr)
                self.send({"event": "error", "message": "Wrong token"})
                return
            if hello.get("rate", RATE) != RATE:
                self.send({"event": "error", "message": f"Audio must be {RATE} Hz"})
                return
            self.device = _device_name(hello.get("device")) or self.addr
            tr = self.mic.transcriber
            self.seg = Segmenter(self.mic.settings, StreamingVad(), self._utterance, self._partial)
            lane = Lane(self._preview, self._final, self._mark, speaking=lambda: self.seg.speaking,
                        device=self.device, commands=False, record=self._record,
                        on_drained=lambda: self.send({"event": "done"}))
            self.lane = lane
            tr.add_lane(lane)
            with self.mic._lock:
                self.mic.streaming += 1
            log.info("remote microphone: %s connected from %s", self.device, self.addr)
            self.send({"event": "ready"})
            self.sock.settimeout(READ_TIMEOUT_S)
            self._read(reader)
        except (OSError, ValueError) as e:
            log.info("remote microphone: %s dropped (%s)", self.device, e.__class__.__name__)
        finally:
            if lane is not None:
                with self.mic._lock:
                    self.mic.streaming -= 1
                self.mic.transcriber.remove_lane(lane)
                log.info("remote microphone: %s disconnected after %.0f s", self.device, time.monotonic() - started)
            self._out.put(None)
            writer.join(2)
            try:
                self.sock.close()
            except OSError:
                pass

    def _read(self, reader) -> None:
        while first := reader.read(1):
            if first == b"{":
                self._command(first + reader.readline(4096))
                continue
            rest = reader.read(3)
            if len(rest) < 3:
                return
            n = int.from_bytes(first + rest, "big")
            if n > MAX_FRAME:
                self.send({"event": "error", "message": "Audio frame too large"})
                return
            pcm = reader.read(n)
            if len(pcm) < n:
                return
            if not self._stopped:
                self._feed(pcm)

    def _feed(self, pcm: bytes) -> None:
        samples = np.frombuffer(pcm[:len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768
        self.seg.feed(samples)
        if self.seg.speaking:
            self._idle_sent = False
        elif not self._idle_sent and self.seg.quiet_ms >= IDLE_MS:
            self._idle_sent = True
            self.send({"event": "idle"})

    def _command(self, line: bytes) -> None:
        try:
            cmd = json.loads(line).get("cmd")
        except (ValueError, AttributeError):
            cmd = None
        if cmd == "stop" and not self._stopped:
            self._stopped = True
            self.seg.flush()
            self.mic.transcriber.drain(self.lane)
        elif cmd == "discard":
            self.seg.discard()
        else:
            log.info("remote microphone: %s sent an unknown command %r", self.device, line[:80])

    # Segmenter callbacks (this connection's reader thread)
    def _utterance(self, utt_id: int, audio: np.ndarray, vad_prob: float) -> None:
        self.mic.transcriber.submit_final(utt_id, audio, vad_prob, info=self.seg.last_info, lane=self.lane)

    def _partial(self, utt_id: int, audio: np.ndarray) -> None:
        self.mic.transcriber.submit_preview(utt_id, audio, lane=self.lane)

    # Lane callbacks (the transcriber thread)
    def _preview(self, utt_id: int, text: str) -> None:
        self.send({"event": "preview", "utt": utt_id, "text": text})

    def _final(self, utt_id: int, text: str) -> None:
        held = self.lane.held if self.lane.held is not None and self.lane.held["utt_id"] == utt_id else None
        event = {"event": "final", "utt": utt_id, "text": text, "held": held is not None}
        if held:
            event["mark"] = held["mark"]  # so a phone that stops early can put it in itself
        self.send(event)

    def _mark(self, utt_id: int, mark: str) -> None:
        self.send({"event": "mark", "utt": utt_id, "mark": mark})

    def _record(self, text: str, audio: np.ndarray, reason: str | None) -> None:
        now = datetime.now()
        try:
            REMOTE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            line = f"{now:%H:%M:%S}  [{self.device}] {text}" + (f"   [not sent: {reason}]" if reason else "")
            with open(REMOTE_HISTORY_DIR / f"{now:%Y-%m-%d}.txt", "a", encoding="utf-8") as f:
                f.write(line + "\n")
            day = REMOTE_AUDIO_DIR / f"{now:%Y-%m-%d}"
            day.mkdir(parents=True, exist_ok=True)
            _write_wav(day / f"{now:%H%M%S_%f}-{self.device}.wav", audio)
        except OSError:
            log.exception("could not write the remote history")


def _hello(line: bytes) -> dict | None:
    try:
        hello = json.loads(line)
    except ValueError:
        return None
    return hello if isinstance(hello, dict) else None


def _device_name(name) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(name or ""))[:32]


def main(port: int | None = None) -> int:
    """The remote microphone on its own: no PC microphone, bar or typing (python -m voicewriter --remote-only)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr:
        root.addHandler(logging.StreamHandler(sys.stderr))
    settings = Settings.load()
    if not settings.remote_token:
        log.error("remote_token is not set in %s", DATA_DIR / "settings.json")
        return 2
    mic = RemoteMic(settings, None, port)  # transcriber set below, once the port is ours
    try:
        server = mic.listen()
    except OSError as e:
        log.error("port %d is taken (%s); is another copy running?", mic.port, e)
        return 3
    mic.transcriber = Transcriber(settings, lambda *a: None, lambda *a: None, lambda s: s and log.info("%s", s))
    mic.transcriber.start()
    log.info("remote-only VoiceWriter starting, data in %s", DATA_DIR)
    mic.serve_forever(server)
    return 0
