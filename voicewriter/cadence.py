"""Held sentence marks, and the cadence log.

Whisper ends every utterance with a full stop, so a sentence you pause in the middle of
comes out as two. A sentence that ends in . ? or ! is typed without its mark, which is
held. When the next utterance comes, the speech on both sides of the pause is
transcribed together with word timings, and the mark Whisper puts at the join decides:
a full stop (or ? or !) ends the sentence, a comma or nothing carries it on, and the
next sentence's first letter follows. With nothing said for a while, the mark is typed
as it was.

The cadence log is one JSON line per sentence and per join, so pause lengths can be
compared with where sentences really ended. No audio and no text beyond a few words
around each join goes into it.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("voicewriter.cadence")

# How much of each side of the pause is heard again. Only the words next to it matter, and the next sentence waits
# for the join before it is typed.
TAIL_S = 4.0
NEXT_S = 4.0
SLACK_S = 0.15


def hold_mark(text: str) -> tuple[str, str] | None:
    """The text without its closing mark, and the mark; None when there is nothing to hold ("..." and "?!" stay)."""
    t = text.rstrip()
    if len(t) < 2 or t[-1] not in ".?!" or t[-2] in ".?!":
        return None
    body = t[:-1].rstrip()
    return (body, t[-1]) if any(c.isalnum() for c in body) else None


def _bare(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", word.lower())


def join_mark(words: list, split: float, prev: str) -> str:
    """The mark Whisper put on the word at the join, from word timings across both utterances.

    Whisper stretches one of the two words next to a pause across the silence, so the clock alone can pick the
    word before or after the join; the held sentence's last word ([prev]) settles which one it is.
    """
    at = -1
    for i, w in enumerate(words):
        if w.end <= split + SLACK_S or w.start < split:
            at = i
    last = _bare(prev.split()[-1]) if prev.split() else ""
    if last:
        for i in (at, at - 1, at + 1, at - 2):
            if 0 <= i < len(words) and _bare(words[i].word) == last:
                at = i
                break
    if at < 0:
        return ""
    word = words[at].word.strip()
    if word.endswith("..") or word.endswith("…"):  # trailing off, not a full stop
        return ""
    return word[-1] if word and word[-1] in ".?!," else ""


def mark_for(held: str, verdict: str | None) -> str:
    """What the held mark becomes: the verdict's sentence end (keeping ? and !), a comma, or nothing."""
    if verdict is None:
        return held
    if verdict in ("?", "!"):
        return verdict
    if verdict == ".":
        return held
    return verdict  # "," or ""


def start_case(text: str, sentence_start: bool, vocabulary: list[str]) -> str:
    """Capitalises the first letter after a sentence end, lower-cases it otherwise ("I", names and acronyms stay)."""
    i = next((k for k, c in enumerate(text) if c.isalpha()), -1)
    if i < 0:
        return text
    if sentence_start:
        c = text[i].upper()
    elif _keeps_capital(text[i:], vocabulary):
        c = text[i]
    else:
        c = text[i].lower()
    return text[:i] + c + text[i + 1:]


def _keeps_capital(text: str, vocabulary: list[str]) -> bool:
    word = re.match(r"[\w'’-]*", text).group(0)
    if word == "I" or word.startswith(("I'", "I’")):
        return True
    if sum(c.isupper() for c in word) >= 2:  # TKA, LED, McDonald
        return True
    bare = re.split(r"['’]", word)[0].lower()
    return any(v[:1].isupper() and v.split()[0].lower() in (bare, word.lower()) for v in vocabulary if v.strip())


_lock = threading.Lock()


def write(path: Path, entry: dict) -> None:
    """Appends one JSON line in a single write, so other programs logging to the same file never interleave."""
    line = json.dumps({"t": datetime.now().astimezone().isoformat(timespec="milliseconds"), **entry}) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0))
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    except OSError:
        log.exception("could not write the cadence log")
