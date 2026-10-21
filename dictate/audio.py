"""Microphone capture at 16 kHz mono.

The PortAudio callback only copies samples into a queue, so a slow GPU or a busy
UI can never make the microphone drop audio.

Recovery: a watchdog reopens the stream whenever it stops delivering samples
(unplugged, driver reset, the PC waking from sleep), and the app asks for a
reopen when Windows reports devices changing or a resume from sleep. Reopening
restarts PortAudio so it sees the current device list, and runs on a throwaway
thread so a driver that hangs while closing can't freeze recovery for good.
Each successful open bumps `generation`, which tells the app to finish any
sentence that was cut off by the interruption.
"""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd
import soxr

RATE = 16000
STALE_S = 2.5
log = logging.getLogger("dictate.audio")


class Mic:
    def __init__(self, device: str | None = None):
        self.device = device
        self.chunks: "queue.SimpleQueue[np.ndarray]" = queue.SimpleQueue()
        self.error: str | None = None
        self.level = 0.0
        self.device_name = ""
        self.generation = 0
        self._stream: sd.InputStream | None = None
        self._resampler: soxr.ResampleStream | None = None
        self._last_audio = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._reopen_at = 0.0
        self._reopen_reason = ""
        self._reopening: threading.Thread | None = None
        self._reopen_started = 0.0
        self._watchdog = threading.Thread(target=self._watch, name="mic-watchdog", daemon=True)

    def start(self) -> None:
        self._open()
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def set_device(self, device: str | None) -> None:
        self.device = device
        self.request_reopen("microphone changed in menu", 0)

    def request_reopen(self, reason: str, delay: float = 1.5) -> None:
        """Reopen soon. Repeated requests (Windows sends device changes in bursts) collapse into one."""
        self._reopen_reason = reason
        self._reopen_at = time.monotonic() + delay

    @staticmethod
    def input_devices() -> list[str]:
        try:
            names = []
            default_api = sd.default.hostapi
            for d in sd.query_devices():
                if d["max_input_channels"] > 0 and d["hostapi"] == default_api and d["name"] not in names:
                    names.append(d["name"])
            return names
        except Exception:
            return []

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy() if indata.ndim == 2 else indata.copy()
        if self._resampler is not None:
            mono = self._resampler.resample_chunk(mono)
        self._last_audio = time.monotonic()
        if len(mono):
            self.level = float(np.sqrt(np.mean(mono * mono)))
            self.chunks.put(mono)

    def _device_index(self):
        if not self.device:
            return None
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == self.device and d["max_input_channels"] > 0 and d["hostapi"] == sd.default.hostapi:
                return i
        log.warning("microphone %r not found, using the default", self.device)
        return None

    def _open(self) -> None:
        with self._lock:
            try:
                index = self._device_index()
                try:
                    self._resampler = None
                    stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=512,
                                            device=index, callback=self._callback)
                except sd.PortAudioError:
                    native = int(sd.query_devices(index, "input")["default_samplerate"])
                    self._resampler = soxr.ResampleStream(native, RATE, 1, dtype="float32")
                    stream = sd.InputStream(samplerate=native, channels=1, dtype="float32",
                                            blocksize=int(native * 0.032), device=index, callback=self._callback)
                stream.start()
                self._stream = stream
                self._last_audio = time.monotonic()
                self.device_name = sd.query_devices(stream.device)["name"]
                self.error = None
                self.generation += 1
                log.info("microphone open: %s", self.device_name)
            except Exception as e:
                self._stream = None
                self._last_audio = time.monotonic()  # wait a full stale period before retrying
                if self.error is None:
                    log.error("could not open microphone: %s", e)
                self.error = "No microphone found. Plug one in; Dictate picks it up by itself."

    def _close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return

        def close():
            try:
                stream.abort()
                stream.close()
            except Exception:
                pass

        t = threading.Thread(target=close, name="mic-close", daemon=True)
        t.start()
        t.join(2.0)
        if t.is_alive():
            log.warning("microphone driver hung while closing; leaving it and opening fresh")

    def _reopen(self) -> None:
        self._close()
        # PortAudio caches the device list at startup; restart it so new and removed devices are seen.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            log.exception("PortAudio restart failed")
        self._open()

    def _start_reopen(self, reason: str) -> bool:
        if self._reopening and self._reopening.is_alive():
            if time.monotonic() - self._reopen_started < 15:
                return False
            log.warning("previous microphone reopen is stuck; starting another")
        log.info("reopening microphone: %s", reason)
        self._reopen_started = time.monotonic()
        self._reopening = threading.Thread(target=self._reopen, name="mic-reopen", daemon=True)
        self._reopening.start()
        return True

    def _watch(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            if self._reopen_at and now >= self._reopen_at:
                # A request made during a reopen in progress waits for it, then runs.
                self._reopen_at = 0.0 if self._start_reopen(self._reopen_reason) else now + 1.0
            elif now - self._last_audio > STALE_S:
                # Covers a dead stream, a stopped one, and retrying while no mic is plugged in.
                self._start_reopen("stopped delivering audio" if self._stream is not None else "no microphone")
                self._last_audio = now  # give the reopen time before judging it
