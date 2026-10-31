# VoiceWriter

Speech-to-text for Windows that runs entirely on your own PC. A thin bar docks
across the top of the screen. You talk, your words appear in the bar as gray
preview text, and when you pause the finished sentence is typed into whatever
window has focus: a browser, an email, a chat, a code editor.

It only does dictation. It doesn't try to control your computer, so it never
mistakes a sentence for a command. The one exception is saying "mute" or
"unmute" on its own.

## Install

1. Download `VoiceWriter-Setup-x.y.z.exe` from the
   [Releases page](https://github.com/austencloud/voicewriter/releases).
2. Run it. It installs for your Windows account only and doesn't ask for
   administrator rights. Setup downloads Python and the speech libraries
   (about 2.5 GB installed), so it takes a few minutes.
3. VoiceWriter starts when setup finishes. The first time, it downloads the
   speech models (about 3.3 GB more) and the bar says so while it works.

Windows may warn about an unrecognized app because the installer isn't
code-signed. Choose "More info", then "Run anyway".

### Requirements

- Windows 10 or 11, 64-bit.
- An NVIDIA graphics card with at least 6 GB of memory is strongly recommended
  (GTX 10-series or newer; RTX cards are fastest). Without one VoiceWriter
  falls back to a smaller model on the CPU, which is slower and makes more
  mistakes.
- About 7 GB of disk space.
- English. The models can do other languages, but VoiceWriter is only tuned
  and tested for English so far.

## Using it

- **Start and stop:** click anywhere on the bar, press Ctrl+Shift+Space, or say
  "mute" or "unmute" by itself between pauses. Blue means listening, gray
  means muted. Inside a sentence those words are just typed like any other.
  While muted it listens only for "unmute": nothing it hears is typed, shown
  or saved.
- **Menu:** right-click the bar for: copy last sentence, today's history, word
  list, microphone, start with Windows, and quit.
- **Word list:** names and jargon you want spelled your way. "Edit word list…"
  in the menu opens it; put one word or phrase per line and save. The next
  sentence uses it.
- **Long talking is fine:** VoiceWriter splits long stretches at your natural
  pauses, or at the quietest moment if you never pause. Nothing is cut off.
- **Nothing gets lost:** every sentence is saved to today's history, and its
  audio is kept for two days.
- **Admin windows:** Windows doesn't let normal programs type into windows
  running as administrator. For those, VoiceWriter puts the sentence on the
  clipboard and tells you so on the bar; press Ctrl+V.
- **No text box selected:** in Chrome, Edge and apps built on them, VoiceWriter
  types only when a text box has focus. Otherwise the keys would land on the
  page itself, where every space scrolls it down.
- **Goes to Claude:** when nothing editable is selected (you're reading or
  scrolling a page, or clicked the desktop) and the Claude desktop app is open
  and mostly in view on any monitor, the sentence goes into Claude's message
  box, and the window you were in gets focus back. Turn this off in the menu
  ("Send to Claude when no text box is selected").
- **Otherwise, the clipboard:** with Claude closed or hidden, what you said goes
  to the clipboard (several sentences in a row are kept together), and the bar
  tells you; click a box and press Ctrl+V.
- **Recovers by itself:** the microphone reopens after an unplug, a device
  change, or waking from sleep. If VoiceWriter ever crashes it restarts.
- **Other dictation programs:** if Windows Voice Access or Talon is running
  too, the same words can be typed twice. VoiceWriter warns you, and the menu
  has a switch to close them automatically. It's off by default because some
  people rely on Voice Access to control their PC.

## Privacy

Everything happens on your PC. Audio never leaves it, and VoiceWriter makes no
network connections except to download the speech models the first time
(from Hugging Face). There's no account, telemetry or analytics. The one
exception is the remote microphone below, which is off until you set a token
for it.

Your history, saved audio, word list and settings are kept in
`%LOCALAPPDATA%\VoiceWriter`. Delete that folder to erase them.

## Settings

`%LOCALAPPDATA%\VoiceWriter\settings.json` holds the pause length (`pause_ms`,
700 by default), the hotkey, the models, the bar height and how many days of
audio to keep. Quit VoiceWriter before editing it. The log is
`voicewriter.log` in the same folder.

A sentence's closing `.`, `?` or `!` waits until you go on. If the next words
carry the sentence on, it becomes a comma or nothing, so pausing to think in
the middle of a sentence doesn't split it in two. After 2.5 seconds of quiet
(`hold_ms`) the mark is typed as it was. Set `hold_marks` to `false` to type
every mark straight away. Pause lengths and where sentences really ended go to
`cadence.jsonl` in the same folder, or to the file named in `cadence_log`.

## Remote microphone

A phone (or any other device) can use VoiceWriter as its speech engine. It
streams its microphone to the PC, and VoiceWriter sends the words back to that
device only: nothing it says is typed on the PC or written to the PC's
history. Its sentences are cut, transcribed and punctuated exactly like the
PC microphone's, with the same models and held marks.

It is off by default. To turn it on, set `remote_token` in `settings.json` to
a long random string, restart VoiceWriter, and allow TCP port 8011
(`remote_port`) through Windows Firewall. Anyone who can reach that port and
knows the token can use your PC's speech models, so keep it to your home
network or a private VPN. A device's sentences are kept in the `remote`
folder.

The protocol is plain TCP and is described at the top of
`voicewriter/remote.py`. `tools/fake_phone.py` plays a recording into it the
way a phone would. `python -m voicewriter --remote-only --data-dir <folder>`
runs the remote microphone on its own, without the PC microphone, the bar or
typing.

## How it works

- [Silero VAD](https://github.com/snakers4/silero-vad) detects speech, and a
  pause of 0.7 seconds ends a sentence.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) runs Whisper
  `large-v3` on the GPU for the text that gets typed, and `small.en` for the
  live preview.
- Text is typed with Windows `SendInput` as Unicode characters, so it works in
  any keyboard layout and any app that accepts typing.
- The bar is a PyQt6 window docked as a Windows app bar, so maximized windows
  sit below it instead of under it.

## Uninstalling

Use "Installed apps" in Windows Settings. It asks whether to also delete your
history and settings; the answer defaults to keeping them.

## Running from source

Needs Python 3.13 and [uv](https://github.com/astral-sh/uv):

```
uv venv .venv --python 3.13
uv pip install --python .venv -r requirements.txt
.venv\Scripts\pythonw.exe launch.pyw
```

Tests (the recordings they use are not in the repository; record your own):

```
.venv\Scripts\python.exe tests\monologue_test.py <recording.wav> <transcript.txt> 1.0
.venv\Scripts\python.exe tests\voice_mute_test.py
.venv\Scripts\python.exe tests\recovery_test.py
```

`recovery_test.py` asks you to move the mouse and click once while it runs.

## License

GPL-3.0, because VoiceWriter is built on PyQt6. See [LICENSE](LICENSE) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
