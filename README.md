# Dictate

Dictation only. No voice commands, ever. A bar docks across the top of the
primary screen like Voice Access; you talk, gray words show up in the bar as you
speak, and each finished sentence is typed into whatever has focus when you pause.

- **On/off:** click anywhere on the bar, or press Ctrl+Shift+Space. Blue = listening, gray = off.
- **Menu:** right-click the bar (or the dots on the right): copy last sentence, today's history, microphone, start with Windows, quit.
- **Nothing gets lost:** long monologues are split at your natural pauses (or the quietest moment if you never pause), never cut off. Every sentence is saved to `%LOCALAPPDATA%\Dictate\history\`, with its audio for two days. If a window refuses typed text (admin windows), the sentence goes to the clipboard and the bar says so.
- **Speech engine:** Whisper large-v3 on the GPU for the typed text, small.en for the live preview. Runs fully offline after the first model download.
- **Crash-proof:** `launch.pyw` restarts it if it ever dies; Windows starts `launch.pyw` at login.

Settings live in `%LOCALAPPDATA%\Dictate\settings.json` (pause length, vocabulary, hotkey, models). Logs: `dictate.log` in the same folder.

Test: `.venv\Scripts\python.exe tests\monologue_test.py tests\audio\monologue.wav tests\audio\monologue.txt`
