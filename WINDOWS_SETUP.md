# SpeakPaste — Windows Setup (NVIDIA CUDA)

Push-to-talk voice-to-text: hold a hotkey, speak, release — text is pasted into the active window.

## Architecture

```
speakpaste_windows.py      Main script
├── faster-whisper          Whisper on CUDA (CTranslate2 backend)
├── pynput                  Hotkey listener (hold Right Alt to record)
├── pystray + Pillow        System tray icon with state indicator
├── pyaudio                 Microphone capture
├── Win32 SendInput         Paste via Ctrl+V (no pyautogui needed)
└── .env                    Configuration file
```

### Key differences from macOS version

| Component | macOS (Apple Silicon) | Windows (NVIDIA) |
|---|---|---|
| Whisper backend | `mlx-whisper` (Metal) | `faster-whisper` (CUDA via CTranslate2) |
| System tray | PyObjC `NSStatusItem` + SF Symbols | `pystray` + Pillow-drawn icon |
| Paste | AppleScript `key code` | Win32 `SendInput` (Ctrl+V) |
| Sound | `afplay` (system .aiff) | `winsound.MessageBeep` |
| Single instance | `fcntl.flock` | Win32 named mutex |
| No-dock | `NSApplicationActivationPolicyAccessory` | N/A (tray-only by default) |
| Default hotkey | Right Option (`option_r`) | Right Alt (`alt_r`) |

## Prerequisites

1. **Python 3.10+** — https://python.org (check "Add Python to PATH")
2. **NVIDIA GPU** with drivers installed — https://nvidia.com/drivers
3. **CUDA Toolkit 12.x** — installed automatically by faster-whisper via pip

## Standalone .exe (recommended)

No Python required on the target machine. Two ways to get the exe:

**A. GitHub Actions** — run the "Build Windows exe" workflow (Actions tab → Run workflow, or push a `v*` tag) and download the `SpeakPaste-Windows` artifact.

**B. Build locally on Windows** — from the repo root:

```bat
build_windows_exe.bat
```

Output: `dist\SpeakPaste\SpeakPaste.exe` + portable `dist\SpeakPaste-Windows.zip`.

Usage: unzip anywhere (e.g. `%USERPROFILE%\SpeakPaste`), run `SpeakPaste.exe`.
- Config: `.env` next to the exe (created with defaults)
- Logs: `speakpaste.log` next to the exe (no console window; auto-rotates at 5 MB)
- First launch downloads the Whisper model (~3 GB) to `%USERPROFILE%\.cache\huggingface`
- The bundle is large (~1.5+ GB) because it includes the CUDA runtime (cuBLAS/cuDNN)

## Quick Install (venv-based, requires Python)

```bat
install_windows.bat
```

This will:
- Create `%USERPROFILE%\SpeakPaste\` with a virtual environment
- Install all dependencies (faster-whisper, pyaudio, pynput, pystray, etc.)
- Create `.env` config with defaults
- Create Desktop + Start Menu shortcuts
- Launch the app

## Manual Install

```bat
cd %USERPROFILE%
mkdir SpeakPaste && cd SpeakPaste
python -m venv venv
venv\Scripts\pip install faster-whisper pyaudio pyperclip pynput pystray Pillow python-dotenv requests
copy path\to\speakpaste_windows.py .
venv\Scripts\pythonw speakpaste_windows.py
```

## Configuration (.env)

```ini
# Whisper model — options: tiny, base, small, medium, large-v2, large-v3
# Larger = more accurate but slower. large-v3 recommended for NVIDIA GPUs.
WHISPER_PTT_WHISPER_MODEL=large-v3

# Language — leave empty for auto-detect (supports multilingual speech).
# Set to "en", "ru", "nl", etc. to force a specific language.
WHISPER_PTT_WHISPER_LANGUAGE=

# Initial prompt — short punctuated sentence to set transcription style.
# Helps Whisper produce punctuation. Use the language you speak most.
# Leave empty for default behavior.
WHISPER_PTT_WHISPER_INITIAL_PROMPT=

# CUDA device and precision
# device: cuda (NVIDIA GPU) or cpu (fallback)
# compute_type: float16 (fast, needs GPU), int8 (smaller VRAM), float32 (CPU)
WHISPER_PTT_WHISPER_DEVICE=cuda
WHISPER_PTT_WHISPER_COMPUTE_TYPE=float16

# Hotkey — hold to record, release to transcribe and paste.
# Options: alt_r (Right Alt), ctrl_r, shift_r, f13, etc.
# Combo: ctrl+space, alt+r, etc.
WHISPER_PTT_HOTKEY=alt_r

# LLM post-processing via Ollama (optional, requires Ollama running)
WHISPER_PTT_USE_LLM_TRANSFORM=false

# Output
WHISPER_PTT_COPY_TO_CLIPBOARD=true
WHISPER_PTT_PASTE_TO_ACTIVE_WINDOW=true
WHISPER_PTT_KEYS_AFTER_PASTE=none

# Sound feedback
WHISPER_PTT_USE_SOUND=true
```

## How it works

1. **Startup**: Loads Whisper model onto NVIDIA GPU (first run downloads ~3 GB from HuggingFace). System tray icon appears.
2. **Hold hotkey** (Right Alt): Microphone activates, recording starts. Tray icon turns red.
3. **Release hotkey**: Recording stops. Audio is sent to faster-whisper for transcription. Tray icon turns yellow.
4. **Transcription**: Text is copied to clipboard and pasted into the active window via Ctrl+V. Tray icon returns to green.

### Hallucination filtering

Whisper sometimes produces phantom text (e.g., "Thank you for watching", "Продолжение следует..."). SpeakPaste filters these:
- Known hallucination phrases are detected and suppressed
- Repetitive text (same n-gram repeated) is detected
- If hallucination is detected, transcription is retried (up to 3 times)
- `condition_on_previous_text=False` prevents hallucination loops
- `vad_filter=True` (faster-whisper feature) skips silent segments

### Retranscribe

Right-click the tray icon → "Retranscribe Last" to re-run transcription on the last recording without re-recording. Useful when Whisper gives a bad result.

## Troubleshooting

### PyAudio install fails
```bat
pip install pipwin
pipwin install pyaudio
```
Or download a pre-built wheel from https://www.lfd.uci.edu/~gohlke/pythonlibs/#pyaudio

### CUDA not detected
- Verify: `nvidia-smi` should show your GPU
- Install NVIDIA drivers: https://nvidia.com/drivers
- faster-whisper bundles CUDA libraries, but you may need to install cuBLAS separately:
  ```bat
  pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
  ```

### Fallback to CPU
Set in `.env`:
```ini
WHISPER_PTT_WHISPER_DEVICE=cpu
WHISPER_PTT_WHISPER_COMPUTE_TYPE=float32
```

### Hotkey not working
- Try running as Administrator (some apps block global hotkeys)
- Check if another app uses the same hotkey
- Change hotkey in `.env` to something else (e.g., `f13`, `ctrl+space`)

## File structure

```
%USERPROFILE%\SpeakPaste\
├── speakpaste_windows.py    Main script
├── .env                     Configuration
├── venv\                    Python virtual environment
├── SpeakPaste.bat           Console launcher (shows log output)
└── SpeakPaste.vbs           Silent launcher (no console window)
```
