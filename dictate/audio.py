"""Microphone capture at 16 kHz mono.

The PortAudio callback only copies samples into a queue, so a slow GPU or a busy
UI can never make the microphone drop audio. A watchdog reopens the stream if the
device disappears or stops delivering samples (unplugged, default device changed).
"""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd
import soxr

RATE = 16000
log = logging.getLogger("dictate.audio")


class Mic:
    def __init__(self, device: str | None = None):
        self.device = device
        self.chunks: "queue.SimpleQueue[np.ndarray]" = queue.SimpleQueue()
        self.error: str | None = None
        self.level = 0.0
        self._stream: sd.InputStream | None = None
        self._resampler: soxr.ResampleStream | None = None
        self._last_audio = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._watchdog = threading.Thread(target=self._watch, name="mic-watchdog", daemon=True)

    def start(self) -> None:
        self._open()
        self._watchdog.start()

    def stop(self) -> None:
        self._stop.set()
        self._close()

    def set_device(self, device: str | None) -> None:
        self.device = device
        self._reopen()

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
                self.error = None
                log.info("microphone open: %s", sd.query_devices(stream.device)["name"])
            except Exception as e:
                self._stream = None
                self.error = "No microphone"
                log.error("could not open microphone: %s", e)

    def _close(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.abort()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def _reopen(self) -> None:
        self._close()
        # PortAudio caches the device list at startup; restart it so a new default mic is seen.
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        self._open()

    def _watch(self) -> None:
        while not self._stop.wait(1.0):
            stale = time.monotonic() - self._last_audio > 2.5
            if self._stream is None or not self._stream.active or stale:
                log.warning("microphone stopped delivering audio, reopening")
                self._reopen()
