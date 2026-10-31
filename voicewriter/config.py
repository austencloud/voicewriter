"""Settings, stored as JSON next to the history and logs."""

import json
import os
import shutil
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# VOICEWRITER_DATA (set by --data-dir) keeps a second copy, such as a test of the remote microphone, apart.
DATA_DIR = Path(os.environ.get("VOICEWRITER_DATA") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "VoiceWriter")
SETTINGS_PATH = DATA_DIR / "settings.json"


def _move_old_data() -> None:
    """VoiceWriter used to be called Dictate; bring its settings, word list and history along."""
    old = DATA_DIR.parent / "Dictate"
    if os.environ.get("VOICEWRITER_DATA") or not old.is_dir() or DATA_DIR.exists():
        return
    try:
        try:
            old.rename(DATA_DIR)
        except OSError:  # files still open (an old copy running): copy instead
            shutil.copytree(old, DATA_DIR)
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw.setdefault("close_other_dictation", True)  # Dictate always did this
        SETTINGS_PATH.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


_move_old_data()
HISTORY_DIR = DATA_DIR / "history"
AUDIO_DIR = DATA_DIR / "audio"
LOG_PATH = DATA_DIR / "voicewriter.log"
# The word list, one word or phrase per line. Opened from the bar's menu; changes apply on save.
WORDS_PATH = DATA_DIR / "words.txt"
WORDS_HEADER = ("# Words and names VoiceWriter should spell your way, one per line.\n"
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
    vocabulary: list[str] = field(default_factory=list)
    # Close Voice Access and Talon whenever they start, so words are never typed twice.
    # Off by default: some people rely on Voice Access to control their PC.
    close_other_dictation: bool = False
    # A sentence said with no text box selected goes to the Claude app's message box, if it's open and in view.
    send_to_claude: bool = True
    listen_on_start: bool = True
    hotkey: str = "ctrl+shift+space"
    bar_height: int = 40
    keep_audio_days: int = 2
    # A sentence's closing . ? or ! waits until you go on: if the next words carry the sentence on, the mark becomes
    # a comma or nothing. Typed as it was after hold_ms of quiet.
    hold_marks: bool = True
    hold_ms: int = 2500
    # Where pause lengths and sentence ends are logged, one JSON line each (empty: cadence.jsonl next to the history).
    cadence_log: str = ""
    # The remote microphone: a phone streams its microphone here and gets the words back, made exactly as the PC
    # microphone's are (remote.py). Listens on every network on this port, only while remote_token is set; 0 is off.
    remote_port: int = 8011
    remote_token: str = ""

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
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))  # keeps settings a newer version added
        except (OSError, ValueError):
            raw = {}
        raw = {**(raw if isinstance(raw, dict) else {}), **asdict(self)}
        tmp = SETTINGS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_PATH)
