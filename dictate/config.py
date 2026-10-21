"""Settings, stored as JSON next to the history and logs."""

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Dictate"
SETTINGS_PATH = DATA_DIR / "settings.json"
HISTORY_DIR = DATA_DIR / "history"
AUDIO_DIR = DATA_DIR / "audio"
LOG_PATH = DATA_DIR / "dictate.log"
# The word list, one word or phrase per line. Opened from the bar's menu; changes apply on save.
WORDS_PATH = DATA_DIR / "words.txt"
WORDS_HEADER = ("# Words and names Dictate should spell your way, one per line.\n"
                "# Save this file and the next sentence uses the new list.\n")


def read_words() -> list[str]:
    lines = WORDS_PATH.read_text(encoding="utf-8-sig").splitlines()
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]


def write_words(words: list[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORDS_PATH.write_text(WORDS_HEADER + "\n" + "\n".join(words) + "\n", encoding="utf-8")


@dataclass
class Settings:
    # None means the Windows default microphone.
    microphone: str | None = None
    # Models: the final one decides what gets typed, the preview one fills the bar while you talk.
    final_model: str = "large-v3"
    preview_model: str = "small.en"
    language: str = "en"
    # A pause this long ends a sentence and types it.
    pause_ms: int = 700
    # Very long stretches without a pause get split here, at the quietest moment, so nothing waits forever.
    soft_split_s: float = 12.0
    hard_split_s: float = 27.0
    # Words Whisper should spell your way (names, jargon).
    vocabulary: list[str] = field(default_factory=lambda: ["TKA", "Flow Arts Composer", "Cirque Aflame", "poi", "Talon"])
    listen_on_start: bool = True
    hotkey: str = "ctrl+shift+space"
    bar_height: int = 40
    keep_audio_days: int = 2

    @classmethod
    def load(cls) -> "Settings":
        known = {f.name for f in fields(cls)}
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in raw.items() if k in known})
        except FileNotFoundError:
            s = cls()
            s.save()
            return s
        except (ValueError, TypeError):
            return cls()

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
