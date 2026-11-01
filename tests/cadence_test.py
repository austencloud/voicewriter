"""Checks the held-mark rules: which marks are held, what a join verdict turns them into, and the next
sentence's first letter. No model or microphone needed:
    .venv\\Scripts\\python.exe tests\\cadence_test.py
"""

import json
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, __file__.rsplit("tests", 1)[0])
from voicewriter import cadence  # noqa: E402

failures = []


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


for text, want in [("I went to the park.", ("I went to the park", ".")), ("Are you free? ", ("Are you free", "?")),
                   ("Wow!", ("Wow", "!")), ("and then...", ("and then", "...")), ("so…", ("so", "…")),
                   ("Really?!", None), ("no mark here", None), (".", None), (" - .", None), ("...", None)]:
    check(cadence.hold_mark(text) == want, f"hold_mark({text!r}) -> {want}")

for held, verdict, want in [(".", None, "."), ("?", None, "?"), (".", ".", "."), ("?", ".", "?"), (".", "?", "?"),
                            (".", "!", "!"), (".", ",", ","), (".", "", ""), ("?", "", ""),
                            ("...", None, "..."), ("...", "", ""), ("...", ",", ","), ("...", ".", "."), ("...", "?", "?")]:
    check(cadence.mark_for(held, verdict) == want, f"mark_for({held!r}, {verdict!r}) -> {want!r}")

vocab = ["Kasey Foster", "poi", "Levi-wand", "Lincoln Park"]
for text, start, want in [("then we left", True, "Then we left"), ("Then we left", False, "then we left"),
                          ("I think so", False, "I think so"), ("I'm in", False, "I'm in"),
                          ("Kasey said", False, "Kasey said"), ("Kasey's poi", False, "Kasey's poi"),
                          ("TKA is ready", False, "TKA is ready"), ("Poi spin", False, "poi spin"),
                          ("", False, ""), ('"Hello" there', False, '"hello" there')]:
    check(cadence.start_case(text, start, vocab) == want, f"start_case({text!r}, {start}) -> {want!r}")


def w(word, start, end):
    return SimpleNamespace(word=word, start=start, end=end)


# "...free next Saturday? We need..." with the pause after Saturday at 3.0 s
words = [w(" free", 2.0, 2.3), w(" next", 2.3, 2.6), w(" Saturday?", 2.6, 2.95), w(" We", 3.6, 3.8), w(" need", 3.8, 4.0)]
check(cadence.join_mark(words, 3.2, "Are you free next Saturday?") == "?", "join on a question")
# Whisper stretched the word after the pause back across it: the clock picks "We", prev picks Saturday
stretched = [w(" free", 2.0, 2.3), w(" next", 2.3, 2.6), w(" Saturday?", 2.6, 2.95), w(" We", 2.95, 3.8)]
check(cadence.join_mark(stretched, 3.2, "next Saturday") == "?", "prev's last word settles a stretched join")
mid = [w(" the", 1.0, 1.2), w(" fire", 1.2, 1.6), w(" and", 2.4, 2.6), w(" the", 2.6, 2.7)]
check(cadence.join_mark(mid, 2.0, "we lit the fire.") == "", "mid-sentence pause gets no mark")
check(cadence.join_mark([w(" so...", 1.0, 1.5), w(" yes", 2.0, 2.2)], 1.8, "so") == "", "trailing off is not a stop")
check(cadence.join_mark([], 1.0, "x") == "", "no words, no mark")

# the log: whole lines from many threads at once
path = Path(tempfile.mkdtemp(prefix="voicewriter-test-")) / "cadence.jsonl"
threads = [threading.Thread(target=lambda i=i: [cadence.write(path, {"device": "test", "i": i, "pad": "x" * 500})
                                                 for _ in range(50)]) for i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
lines = path.read_text(encoding="utf-8").splitlines()
check(len(lines) == 400 and all(json.loads(line)["device"] == "test" for line in lines), "400 whole log lines")

print()
print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
sys.exit(1 if failures else 0)
