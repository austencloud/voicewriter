"""Turns microphone audio into finished sentences.

Segmenter cuts the stream into utterances at your pauses. Long stretches without
a pause are split at the quietest moment instead of being cut off, and every
sample lands in exactly one utterance, so nothing you say is thrown away.

Transcriber owns the GPU. Finished utterances always run before previews, in the
order they were spoken; a preview is only ever the newest one, so the bar never
lags behind your voice.
"""

import collections
import logging
import re
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np

from .config import AUDIO_DIR, HISTORY_DIR, Settings
from .vad import FRAME, StreamingVad

RATE = 16000
FRAME_MS = FRAME * 1000 / RATE
log = logging.getLogger("voicewriter.engine")

START_PROB = 0.5
KEEP_PROB = 0.35


def frames(ms: float) -> int:
    return max(1, int(round(ms / FRAME_MS)))


class Segmenter:
    def __init__(self, settings: Settings, vad: StreamingVad,
                 on_utterance: Callable[[int, np.ndarray, float], None],
                 on_partial: Callable[[int, np.ndarray], None]):
        self.vad = vad
        self.on_utterance = on_utterance
        self.on_partial = on_partial
        self.pause_frames = frames(settings.pause_ms)
        self.soft_frames = frames(settings.soft_split_s * 1000)
        self.hard_frames = frames(settings.hard_split_s * 1000)
        self.preview_frames = frames(400)
        self._preroll: collections.deque = collections.deque(maxlen=frames(400))
        self._pending = np.zeros(0, dtype=np.float32)
        self._utt: list[tuple[np.ndarray, float]] = []
        self._speaking = False
        self._start_run = 0
        self._silence_run = 0
        self._since_preview = 0
        self.utt_id = 0

    @property
    def speaking(self) -> bool:
        return self._speaking

    def feed(self, samples: np.ndarray) -> None:
        buf = np.concatenate([self._pending, samples]) if len(self._pending) else samples
        n = len(buf) // FRAME
        for i in range(n):
            self._frame(buf[i * FRAME:(i + 1) * FRAME])
        self._pending = buf[n * FRAME:].copy()

    def flush(self) -> None:
        """Finish whatever is in progress (used when you mute mid-sentence)."""
        if self._speaking:
            self._emit(self._utt)
        self._reset()

    def discard(self) -> None:
        """Drop whatever is in progress without sending it anywhere."""
        self._reset()

    def _reset(self) -> None:
        self._utt = []
        self._speaking = False
        self._start_run = 0
        self._silence_run = 0
        self._since_preview = 0
        self._preroll.clear()
        self.vad.reset()

    def _frame(self, frame: np.ndarray) -> None:
        p = self.vad(frame)
        if not self._speaking:
            self._preroll.append((frame, p))
            self._start_run = self._start_run + 1 if p >= START_PROB else 0
            if self._start_run >= 2:
                self._speaking = True
                self._utt = list(self._preroll)
                self._preroll.clear()
                self._silence_run = 0
                self._since_preview = 0
            return

        self._utt.append((frame, p))
        self._silence_run = 0 if p >= KEEP_PROB else self._silence_run + 1
        self._since_preview += 1

        if self._silence_run >= self.pause_frames:
            # Keep a little of the trailing silence; Whisper ends sentences better with it.
            keep = len(self._utt) - self._silence_run + frames(300)
            self._emit(self._utt[:keep])
            tail = self._utt[keep:]
            self._utt = []
            self._speaking = False
            self._start_run = 0
            self._preroll.extend(tail[-self._preroll.maxlen:])
            return

        if len(self._utt) >= self.soft_frames and self._silence_run >= frames(200):
            self._emit(self._utt)
            self._utt = []
            self._speaking = False
            self._start_run = 0
            return

        if len(self._utt) >= self.hard_frames:
            cut = self._quietest_cut()
            head, self._utt = self._utt[:cut], self._utt[cut:]
            self._emit(head)
            self._since_preview = self.preview_frames
            return

        if self._since_preview >= self.preview_frames:
            self._since_preview = 0
            self.on_partial(self.utt_id + 1, _join(self._utt))

    def _quietest_cut(self) -> int:
        probs = np.array([p for _, p in self._utt])
        energy = np.array([float(np.sqrt(np.mean(f * f))) for f, _ in self._utt])
        lo = max(1, len(self._utt) - frames(6000))
        window = 3
        score = np.convolve(probs + energy / (energy.max() + 1e-9), np.ones(window) / window, mode="same")
        return lo + int(np.argmin(score[lo:len(self._utt) - window]))

    def _emit(self, chunk: list[tuple[np.ndarray, float]]) -> None:
        speech = [p for _, p in chunk if p >= KEEP_PROB]
        if len(speech) < frames(200):
            return
        self.utt_id += 1
        self.on_utterance(self.utt_id, _join(chunk), float(np.mean(speech)))


def _join(chunk: list[tuple[np.ndarray, float]]) -> np.ndarray:
    return np.concatenate([f for f, _ in chunk]) if chunk else np.zeros(0, dtype=np.float32)


# Phrases Whisper invents from breath or room noise. Dropped only when the voice detector
# was unsure there was speech at all, so saying "thank you" for real still types it.
_PHANTOMS = {
    "thank you", "thank you.", "thanks for watching", "thanks for watching!", "thank you for watching",
    "thank you for watching.", "you", "bye", "bye.", "okay.", "so", "uh", "um", "hmm",
    "subtitles by the amara.org community", "please subscribe", ".", "...",
}


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# The only two things VoiceWriter obeys, and only when said on their own between pauses.
# "I need to mute the TV" is just words and gets typed.
_COMMANDS = {"mute": "mute", "unmute": "unmute", "un mute": "unmute"}
COMMAND_MAX_S = 3.0  # utterances longer than this are never checked while muted


def command_of(text: str) -> str | None:
    words = re.sub(r"[^a-z ]+", " ", text.lower().replace("-", " "))
    return _COMMANDS.get(" ".join(words.split()))


class Transcriber:
    def __init__(self, settings: Settings,
                 on_preview: Callable[[int, str], None],
                 on_final: Callable[[int, str], None],
                 on_status: Callable[[str], None],
                 on_command: Callable[[str], None] = lambda cmd: None):
        self.settings = settings
        self.on_preview = on_preview
        self.on_final = on_final
        self.on_status = on_status
        self.on_command = on_command
        self._finals: collections.deque = collections.deque()
        self._preview: tuple[int, np.ndarray] | None = None
        self._done_id = 0
        self._submitted: collections.deque = collections.deque()  # one entry per final not yet finished
        self._cv = threading.Condition()
        self._context = ""
        self._last_final = 0.0
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="transcriber", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def submit_final(self, utt_id: int, audio: np.ndarray, vad_prob: float, muted: bool = False) -> None:
        """muted=True only listens for "unmute": nothing is typed, shown or saved."""
        with self._cv:
            self._finals.append((utt_id, audio, vad_prob, muted))
            self._submitted.append(time.monotonic())
            self._cv.notify()

    def submit_preview(self, utt_id: int, audio: np.ndarray) -> None:
        with self._cv:
            self._preview = (utt_id, audio[-RATE * 28:])
            self._cv.notify()

    def waiting(self) -> tuple[int, float]:
        """Sentences spoken but not typed yet, and how long the oldest has waited."""
        with self._cv:
            if not self._submitted:
                return 0, 0.0
            return len(self._submitted), time.monotonic() - self._submitted[0]

    def _load(self):
        from .gpu import add_cuda_dll_dirs
        add_cuda_dll_dirs()
        from faster_whisper import WhisperModel
        s = self.settings

        def load(name, **kw):
            # Cached copy first, so starting up never depends on the internet.
            try:
                return WhisperModel(name, local_files_only=True, **kw)
            except Exception:
                self.on_status(f"Downloading the {name} speech model (first run only, can take a few minutes)…")
                log.info("downloading model %s", name)
                return WhisperModel(name, **kw)

        import ctranslate2
        try:
            gpus = ctranslate2.get_cuda_device_count()
        except Exception:
            gpus = 0
        if gpus:
            # float16 needs an RTX 20-series or newer; older cards get int8, which is close in accuracy.
            ctype = "float16" if "float16" in ctranslate2.get_supported_compute_types("cuda") else "int8_float32"
            try:
                final = load(s.final_model, device="cuda", compute_type=ctype)
                preview = load(s.preview_model, device="cuda", compute_type=ctype)
                return final, preview
            except Exception as e:
                log.error("GPU load failed (%s); falling back to CPU", e)
                why = "The graphics card couldn't load the speech model"
        else:
            log.info("no NVIDIA GPU found; using the CPU")
            why = "No NVIDIA graphics card"
        final = load("small.en", device="cpu", compute_type="int8")
        self.on_status(f"{why}, so using the CPU: slower and less accurate")
        return final, final

    def _prompt(self) -> str:
        # The word list comes first and is never pushed out by recent sentences.
        vocab = ", ".join(self.settings.vocabulary)[:300]
        vocab = vocab + ". " if vocab else ""
        return vocab + self._context[-(450 - len(vocab)):]

    def _run(self) -> None:
        t = time.time()
        self.final_model, self.preview_model = self._load()
        # Warm both models so the first real sentence is as fast as the rest.
        silence = np.zeros(RATE, dtype=np.float32)
        for m in {id(self.final_model): self.final_model, id(self.preview_model): self.preview_model}.values():
            list(m.transcribe(silence, language=self.settings.language, beam_size=1)[0])
        log.info("models ready in %.1fs", time.time() - t)
        self.ready.set()
        self.on_status("")

        while True:
            with self._cv:
                while not self._finals and self._preview is None:
                    self._cv.wait()
                if self._finals:
                    job, kind = self._finals.popleft(), "final"
                else:
                    job, kind = self._preview, "preview"
                    self._preview = None
            try:
                if kind == "final":
                    try:
                        self._do_final(*job)
                    finally:
                        with self._cv:
                            self._submitted.popleft()
                elif job[0] > self._done_id:
                    self._do_preview(*job)
            except Exception:
                log.exception("%s transcription failed", kind)
                if kind == "final":
                    self._done_id = max(self._done_id, job[0])
                    if not job[3]:  # never keep audio heard while muted
                        _save_failed(job[1])
                        self.on_status("A sentence failed; its audio is saved in history")

    def _transcribe(self, model, audio: np.ndarray, beam: int) -> tuple[str, list]:
        segments, _ = model.transcribe(
            audio, language=self.settings.language, beam_size=beam, initial_prompt=self._prompt() or None,
            condition_on_previous_text=False, without_timestamps=True, vad_filter=False,
        )
        segments = list(segments)
        return clean(" ".join(s.text for s in segments)), segments

    def _do_preview(self, utt_id: int, audio: np.ndarray) -> None:
        text, _ = self._transcribe(self.preview_model, audio, beam=1)
        if utt_id > self._done_id:
            self.on_preview(utt_id, text)

    def _do_final(self, utt_id: int, audio: np.ndarray, vad_prob: float, muted: bool = False) -> None:
        now = time.time()
        if now - self._last_final > 120:
            self._context = ""
        if muted:
            # No prompt: past sentences shouldn't nudge a one-word clip toward other words.
            segments, _ = self.final_model.transcribe(audio, language=self.settings.language, beam_size=5,
                                                      condition_on_previous_text=False, without_timestamps=True)
            heard = command_of(" ".join(s.text for s in segments))
            self._done_id = max(self._done_id, utt_id)
            if heard == "unmute":
                log.info("final #%d while muted: unmute", utt_id)
                self.on_command("unmute")
            return
        t = time.time()
        text, segments = self._transcribe(self.final_model, audio, beam=5)
        if bare(text) and self._context:
            # Whisper copies the style of its prompt. One sentence that came back with no punctuation would
            # make every sentence after it bare too, so try this one again with only the word list as prompt.
            log.info("final #%d came back unpunctuated; retrying without context", utt_id)
            self._context = ""
            text, segments = self._transcribe(self.final_model, audio, beam=5)
        took = time.time() - t
        reason = None
        cmd = command_of(text)
        if not text:
            reason = "empty"
        elif cmd:
            reason = f"command: {cmd}"
        elif text.lower().strip(" !.,") in {p.strip(" !.,") for p in _PHANTOMS} and vad_prob < 0.75:
            reason = "phantom"
        _record(text, audio, reason)
        log.info("final #%d %.1fs audio in %.2fs vad=%.2f%s: %s", utt_id, len(audio) / RATE, took, vad_prob,
                 f" [{reason}]" if reason else "", text)
        self._done_id = max(self._done_id, utt_id)
        if reason:
            self.on_final(utt_id, "")
            if cmd:
                self.on_command(cmd)
            return
        if not bare(text):
            self._context = (self._context + " " + text)[-300:]
        self._last_final = now
        self.on_final(utt_id, text)


def bare(text: str) -> bool:
    """A sentence of several words with no punctuation at all: Whisper has dropped into its unpunctuated style."""
    return len(text.split()) >= 5 and not re.search(r"[.!?,;:]", text)


def _record(text: str, audio: np.ndarray, reason: str | None) -> None:
    now = datetime.now()
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{now:%H:%M:%S}  {text}" + (f"   [not typed: {reason}]" if reason else "")
        with open(HISTORY_DIR / f"{now:%Y-%m-%d}.txt", "a", encoding="utf-8") as f:
            f.write(line + "\n")
        day = AUDIO_DIR / f"{now:%Y-%m-%d}"
        day.mkdir(parents=True, exist_ok=True)
        _write_wav(day / f"{now:%H%M%S_%f}.wav", audio)
    except OSError:
        log.exception("could not write history")


def _save_failed(audio: np.ndarray) -> None:
    _record("[transcription failed]", audio, "error")


def _write_wav(path: Path, audio: np.ndarray) -> None:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


def prune_audio(days: int) -> None:
    cutoff = time.time() - days * 86400
    if not AUDIO_DIR.is_dir():
        return
    for day in AUDIO_DIR.iterdir():
        try:
            if day.is_dir() and day.stat().st_mtime < cutoff:
                for f in day.iterdir():
                    f.unlink()
                day.rmdir()
        except OSError:
            pass
