# Dictate

Dictation only. The one thing it obeys is "mute" / "unmute". A bar docks across
the top of the primary screen like Voice Access; you talk, gray words show up in
the bar as you speak, and each finished sentence is typed into whatever has focus
when you pause.

- **On/off:** click anywhere on the bar, press Ctrl+Shift+Space, or say "mute" / "unmute" on its own between pauses. Inside a sentence the word is just typed. While muted it only listens for "unmute"; nothing heard then is typed, shown or saved. Blue = listening, gray = muted.
- **Menu:** right-click the bar (or the dots on the right): copy last sentence, today's history, word list, microphone, start with Windows, quit.
- **Word list:** names and jargon Whisper should spell your way, in `%LOCALAPPDATA%\Dictate\words.txt`, one per line. "Edit word list…" opens it; saving applies it to the next sentence.
- **Nothing gets lost:** long monologues are split at your natural pauses (or the quietest moment if you never pause), never cut off. Every sentence is saved to `%LOCALAPPDATA%\Dictate\history\`, with its audio for two days. If a window refuses typed text (admin windows), the sentence goes to the clipboard and the bar says so.
- **Recovers by itself:** the mic reopens after an unplug, a device change or waking from sleep; the bar shows an amber badge when sentences queue up behind a busy GPU.
- **One dictation program only:** if Voice Access or Talon starts, Dictate closes it so words are never typed twice.
- **Speech engine:** Whisper large-v3 on the GPU for the typed text, small.en for the live preview. Runs fully offline after the first model download.
- **Crash-proof:** `launch.pyw` restarts it if it ever dies; Windows starts `launch.pyw` at login.

Settings live in `%LOCALAPPDATA%\Dictate\settings.json` (pause length, hotkey, models). Logs: `dictate.log` in the same folder.

Tests (the .wav files are not in git):

- `.venv\Scripts\python.exe tests\monologue_test.py tests\audio\monologue.wav tests\audio\monologue.txt`
- `.venv\Scripts\python.exe tests\voice_mute_test.py`
- `.venv\Scripts\python.exe tests\recovery_test.py` (move the mouse and click once while it runs)
