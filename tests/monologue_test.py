"""End-to-end check that a long monologue comes through complete.

Feeds a recording through the same Segmenter and Transcriber the app uses, at real
speed, then compares the typed words to the script. Run:
    .venv\\Scripts\\python.exe tests\\monologue_test.py tests\\audio\\monologue.wav tests\\audio\\monologue.txt
"""

import difflib
import re
import sys
import threading
import time
import wave

import numpy as np

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

import voicewriter.engine as engine  # noqa: E402
from voicewriter.config import Settings  # noqa: E402
from voicewriter.engine import Segmenter, Transcriber  # noqa: E402

# Keep test sentences out of the real history.
_tmp = Path(tempfile.mkdtemp(prefix="voicewriter-test-"))
engine.HISTORY_DIR, engine.AUDIO_DIR = _tmp / "history", _tmp / "audio"
from voicewriter.vad import StreamingVad  # noqa: E402


def words(s: str) -> list[str]:
    return re.findall(r"[a-z']+", s.lower())


def main(wav_path: str, script_path: str, speed: float = 1.0) -> int:
    with wave.open(wav_path) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    # Silence before and after, like real life.
    audio = np.concatenate([np.zeros(16000, np.float32), audio, np.zeros(32000, np.float32)])
    script = open(script_path, encoding="utf-8-sig").read()

    settings = Settings()
    settings.cadence_log = str(_tmp / "cadence.jsonl")  # not the real log
    finals: dict[int, str] = {}
    previews = 0
    done = threading.Event()

    def on_final(i, text):
        finals[i] = text
        print(f"  final #{i}: {text}")

    def on_preview(i, text):
        nonlocal previews
        previews += 1

    def on_mark(i, mark):
        finals[i] += mark  # a held mark goes right after its sentence, as the app types it
        print(f"  mark #{i}: {mark!r}")

    tr = Transcriber(settings, on_preview, on_final, lambda s: s and print("status:", s), on_mark=on_mark,
                     speaking=lambda: seg.speaking)
    tr.start()
    tr.ready.wait()
    submitted = []
    seg = Segmenter(settings, StreamingVad(),
                    lambda i, a, p: (submitted.append((i, time.time(), len(a) / 16000)), tr.submit_final(i, a, p)),
                    tr.submit_preview)

    chunk = 512
    t0 = time.time()
    for i in range(0, len(audio), chunk):
        seg.feed(audio[i:i + chunk])
        target = t0 + (i + chunk) / 16000 / speed
        delay = target - time.time()
        if delay > 0:
            time.sleep(delay)
    seg.flush()
    last_id = seg.utt_id
    deadline = time.time() + 30
    while len(finals) < last_id and time.time() < deadline:
        time.sleep(0.05)
    lag = time.time() - (t0 + len(audio) / 16000 / speed)
    time.sleep(settings.hold_ms / 1000 + 0.5)  # the last sentence's held mark

    typed = " ".join(finals[k] for k in sorted(finals))
    ref, hyp = words(script), words(typed)
    sm = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    missing = [ref[i1:i2] for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag in ("delete", "replace")]
    print()
    print(f"utterances: {last_id}, durations: {[round(d, 1) for _, _, d in submitted]}")
    print(f"previews shown: {previews}")
    print(f"words in script: {len(ref)}, typed: {len(hyp)}, matched: {matched} ({matched / len(ref):.1%})")
    print(f"missing/changed: {missing}")
    print(f"last sentence typed {lag:.2f}s after the audio ended")
    return 0 if matched / len(ref) >= 0.97 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 1.0))
