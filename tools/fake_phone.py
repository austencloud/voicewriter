"""Plays a recording into the remote microphone as a phone would, and prints what comes back.

    python tools\\fake_phone.py <recording> --token <token> [--host 127.0.0.1] [--port 8011]
    python tools\\fake_phone.py <recording> --local

The recording is a 16-bit mono WAV, or raw 16-bit mono PCM with --rate. It is sent at real-time pace in 100 ms
frames, with a second of silence before and after, then "stop". The text is put together the way the keyboard does
it: each final, with its held mark added when the mark event comes.

--local runs the same audio through the PC microphone's path instead (Segmenter and Transcriber in this process,
nothing typed), so the two transcripts can be compared. It loads its own copy of the models.
"""

import argparse
import json
import os
import socket
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

RATE = 16000
FRAME_S = 0.1


def load(path: str, rate: int | None) -> np.ndarray:
    """16 kHz int16 samples, with a second of silence before and after."""
    if path.lower().endswith(".wav"):
        with wave.open(path) as w:
            rate = w.getframerate()
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    else:
        pcm = np.frombuffer(Path(path).read_bytes(), np.int16)
        rate = rate or RATE
    if rate != RATE:
        import soxr
        pcm = soxr.resample(pcm.astype(np.float32) / 32768, rate, RATE)
        pcm = (np.clip(pcm, -1, 1) * 32767).astype(np.int16)
    pad = np.zeros(RATE, np.int16)
    return np.concatenate([pad, pcm, pad, pad])


class Text:
    """The keyboard's view: finals in order, each held mark added after its sentence."""

    def __init__(self):
        self.parts: dict[int, str] = {}

    def final(self, utt: int, text: str) -> None:
        self.parts[utt] = text

    def mark(self, utt: int, mark: str) -> None:
        self.parts[utt] = self.parts.get(utt, "") + mark

    def __str__(self) -> str:
        return " ".join(t for _, t in sorted(self.parts.items()) if t)


def remote(audio: np.ndarray, host: str, port: int, token: str, device: str) -> str:
    sock = socket.create_connection((host, port), timeout=10)
    sock.settimeout(None)
    reader = sock.makefile("rb")
    sock.sendall((json.dumps({"token": token, "rate": RATE, "device": device}) + "\n").encode())
    text = Text()
    done = threading.Event()
    t0 = time.monotonic()

    def events():
        for line in reader:
            e = json.loads(line)
            stamp = f"{time.monotonic() - t0:6.2f}s"
            kind = e.get("event")
            if kind == "final":
                text.final(e["utt"], e["text"])
                print(f"{stamp} final #{e['utt']}{' (held)' if e['held'] else ''}: {e['text']}")
            elif kind == "mark":
                text.mark(e["utt"], e["mark"])
                print(f"{stamp} mark #{e['utt']}: {e['mark']!r}")
            elif kind == "preview":
                print(f"{stamp} preview #{e['utt']}: {e['text']}")
            else:
                print(f"{stamp} {kind}{': ' + e['message'] if 'message' in e else ''}")
            if kind in ("done", "error"):
                break
        done.set()

    threading.Thread(target=events, daemon=True).start()
    step = int(RATE * FRAME_S)
    for i in range(0, len(audio), step):
        frame = audio[i:i + step].astype("<i2").tobytes()
        try:
            sock.sendall(len(frame).to_bytes(4, "big") + frame)
        except OSError:
            break
        delay = t0 + (i + step) / RATE - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    try:
        sock.sendall(b'{"cmd": "stop"}\n')
    except OSError:
        pass
    done.wait(30)
    sock.close()
    return str(text)


def local(audio: np.ndarray) -> str:
    """The PC microphone's path, as app.py wires it, at real-time pace."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("VOICEWRITER_DATA", tempfile.mkdtemp(prefix="voicewriter-local-"))
    from voicewriter.config import Settings
    from voicewriter.engine import Segmenter, Transcriber
    from voicewriter.vad import StreamingVad

    settings = Settings.load()
    text = Text()
    t0 = time.monotonic()
    drained = threading.Event()

    def final(i, t):
        text.final(i, t)
        print(f"{time.monotonic() - t0:6.2f}s final #{i}: {t}")

    def mark(i, m):
        text.mark(i, m)
        print(f"{time.monotonic() - t0:6.2f}s mark #{i}: {m!r}")

    tr = Transcriber(settings, lambda i, t: None, final, lambda s: s and print("status:", s), on_mark=mark,
                     speaking=lambda: seg.speaking)
    tr.main.on_drained = drained.set
    seg = Segmenter(settings, StreamingVad(),
                    lambda i, a, p: tr.submit_final(i, a, p, info=seg.last_info), tr.submit_preview)
    tr.start()
    tr.ready.wait()
    t0 = time.monotonic()
    samples = audio.astype(np.float32) / 32768
    for i in range(0, len(samples), 512):
        seg.feed(samples[i:i + 512])
        delay = t0 + (i + 512) / RATE - time.monotonic()
        if delay > 0:
            time.sleep(delay)
    seg.flush()
    tr.drain(tr.main)
    drained.wait(30)
    return str(text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--rate", type=int, help="sample rate of a raw PCM recording")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--token", default=os.environ.get("VOICEWRITER_TOKEN", ""))
    ap.add_argument("--device", default="fake-phone")
    ap.add_argument("--local", action="store_true")
    ap.add_argument("--out", help="write the final text here")
    args = ap.parse_args()
    audio = load(args.recording, args.rate)
    text = local(audio) if args.local else remote(audio, args.host, args.port, args.token, args.device)
    print("\nTEXT:", text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
