"""Checks the remote microphone's protocol and that each phone gets its own Segmenter and lane, with stand-in
models (no GPU, no microphone, nothing typed). Uses tests\\audio\\monologue.wav if it is there, else a made-up
tone burst:
    .venv\\Scripts\\python.exe tests\\remote_test.py
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

os.environ["VOICEWRITER_DATA"] = tempfile.mkdtemp(prefix="voicewriter-test-")  # not the real history or log
sys.path.insert(0, __file__.rsplit("tests", 1)[0])
from voicewriter import engine, remote  # noqa: E402
from voicewriter.config import Settings  # noqa: E402

failures = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


class FakeModel:
    """Every utterance comes back as one numbered sentence; a join hears no mark, so a held mark becomes ""."""

    def __init__(self):
        self.n = 0
        self.finals = 0

    def transcribe(self, audio, word_timestamps=False, beam_size=1, **kw):
        if word_timestamps:
            return [SimpleNamespace(text="", words=[])], None
        if len(audio) == engine.RATE and not audio.any():  # the warm-up
            return [], None
        self.n += 1
        self.finals += beam_size > 1
        return [SimpleNamespace(text=f" Sentence number {self.n}.")], None


def speech() -> np.ndarray:
    wav = Path(__file__).parent / "audio" / "monologue.wav"
    if wav.exists():
        with wave.open(str(wav)) as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        return pcm[: engine.RATE * 20]  # 20 s is plenty
    return np.zeros(engine.RATE * 20, np.int16)  # no speech: only the protocol parts below are meaningful


settings = Settings()
settings.hold_ms = 600
settings.remote_token = "test-token"
main_lane_calls = []
tr = engine.Transcriber(settings, lambda *a: main_lane_calls.append(a), lambda *a: main_lane_calls.append(a),
                        lambda s: None, on_mark=lambda *a: main_lane_calls.append(a))
fake = FakeModel()
tr._load = lambda: (fake, fake)
tr.start()
tr.ready.wait(5)

mic = remote.RemoteMic(settings, tr, port=1)
server = socket.create_server(("127.0.0.1", 0))
port = server.getsockname()[1]
threading.Thread(target=mic.serve_forever, args=(server,), daemon=True).start()


def connect(hello: dict | bytes):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(hello if isinstance(hello, bytes) else (json.dumps(hello) + "\n").encode())
    return s, s.makefile("rb")


def events(reader, until=("done", "error"), timeout=20) -> list[dict]:
    out = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            line = reader.readline()
        except socket.timeout:
            break
        if not line:
            break
        out.append(json.loads(line))
        if out[-1]["event"] in until:
            break
    return out


def frame(pcm: np.ndarray) -> bytes:
    data = pcm.astype("<i2").tobytes()
    return len(data).to_bytes(4, "big") + data


# 1. The hello -------------------------------------------------------------
s, r = connect({"token": "nope", "rate": 16000, "device": "x"})
got = events(r)
check(got == [{"event": "error", "message": "Wrong token"}], f"wrong token refused: {got}")
check(r.readline() == b"", "and the connection closed")
s.close()

s, r = connect(b"GET / HTTP/1.1\r\n\r\n")
check(r.readline() == b"", "a stray HTTP request is closed without an answer")
s.close()

s, r = connect({"token": "test-token", "rate": 44100})
check(events(r)[0].get("message") == "Audio must be 16000 Hz", "wrong sample rate refused")
s.close()

# 2. Two phones at once: one talks, one is silent ----------------------------
talk = speech()
a, ra = connect({"token": "test-token", "rate": 16000, "device": "fold6"})
b, rb = connect({"token": "test-token", "rate": 16000, "device": "tablet!@#"})
check(events(ra, until=("ready",))[0] == {"event": "ready"}, "phone A ready")
check(events(rb, until=("ready",))[0] == {"event": "ready"}, "phone B ready")
check({lane.device for lane in tr._lanes} == {"voicewriter", "fold6", "tablet"},
      f"one lane each, device names cleaned: {[lane.device for lane in tr._lanes]}")
check(mic.streaming == 2, f"both phones count as streaming, so the PC microphone pauses: {mic.streaming}")

step = 1600  # 100 ms frames, sent as fast as the socket takes them: the segmenter works in audio time
silence = np.zeros(len(talk) + engine.RATE * 16, np.int16)
for i in range(0, len(silence), step):
    if i < len(talk):
        a.sendall(frame(talk[i:i + step]))
        if i == 0:
            a.sendall(b'{"cmd": "nonsense"}\n')  # unknown commands are ignored, frames around it still parse
    b.sendall(frame(silence[i:i + step]))
a.sendall(b'{"cmd": "stop"}\n')
got_a = events(ra)
got_b = events(rb, until=("idle",))
finals = [e for e in got_a if e["event"] == "final"]
marks = [e for e in got_a if e["event"] == "mark"]
if speech().any():
    check(len(finals) >= 2, f"phone A got {len(finals)} sentences")
    check([e["utt"] for e in finals] == list(range(1, len(finals) + 1)), "utterance ids count up from 1")
    check(all(e["held"] and e.get("mark") == "." and not e["text"].endswith(".") for e in finals),
          "every final held its full stop, and said which mark it held")
    check([m["utt"] for m in marks] == [e["utt"] for e in finals], "every held mark was settled, in order")
    check(marks[-1]["mark"] == ".", "the last mark went in as it was when phone A stopped")
    check(all(m["mark"] == "" for m in marks[:-1]), "joins that heard no mark carried the sentence on")
check(got_a[-1] == {"event": "done"}, f"phone A got done after stop: {got_a[-1]}")
check([e["event"] for e in got_b] == ["idle"], f"silent phone B got only idle: {[e['event'] for e in got_b]}")
check(not main_lane_calls, "nothing reached the PC lane")
history = list((remote.REMOTE_HISTORY_DIR).glob("*.txt"))
check(not speech().any() or (history and "[fold6]" in history[0].read_text(encoding="utf-8")),
      "phone A's sentences are in the remote history")
check(not (engine.HISTORY_DIR.exists() and any(engine.HISTORY_DIR.iterdir())), "and not in the PC history")
clips = list(remote.REMOTE_AUDIO_DIR.glob("*/*-fold6.wav"))
check(not speech().any() or len(clips) == len(finals), f"each of phone A's sentences kept its audio: {len(clips)} clips")
check(not (engine.AUDIO_DIR.exists() and any(engine.AUDIO_DIR.iterdir())), "and none of it is in the PC's audio")

# 3. A phone that goes away mid-sentence --------------------------------------
before = fake.finals
ra.close()
a.close()
c, rc = connect({"token": "test-token", "rate": 16000, "device": "gone"})
events(rc, until=("ready",))
for i in range(0, min(len(talk), engine.RATE * 3), step):
    c.sendall(frame(talk[i:i + step]))
rc.close()
c.close()
time.sleep(1)
check({lane.device for lane in tr._lanes} == {"voicewriter", "tablet"}, "a closed connection's lane is removed")
rb.close()
b.close()
time.sleep(0.5)
check([lane.device for lane in tr._lanes] == ["voicewriter"], "only the PC lane is left")
check(mic.streaming == 0, f"no phone streaming, so the PC microphone is back on: {mic.streaming}")
check(fake.finals == before, "a dropped phone's half sentence is never transcribed in full")

# 4. Framing edge: a frame bigger than the limit ----------------------------------
d, rd = connect({"token": "test-token", "rate": 16000})
events(rd, until=("ready",))
d.sendall((remote.MAX_FRAME + 2).to_bytes(4, "big"))
check(events(rd)[-1].get("message") == "Audio frame too large", "an oversized frame ends the connection")
d.close()

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
