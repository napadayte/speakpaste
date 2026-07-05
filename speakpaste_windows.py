#!/usr/bin/env python3
"""
SpeakPaste (Windows / NVIDIA): push-to-talk voice-to-text using faster-whisper on CUDA.
Hold hotkey -> speak -> release -> transcription pasted into the active window.

Config: WHISPER_PTT_* env vars or .env file.

Dependencies: faster-whisper, pyaudio, pynput, pyperclip, pystray, Pillow, requests.
Optional: Ollama for LLM transform.
"""

import ctypes
import os
import re
import sys
import queue
import subprocess
import time
import threading
import winsound
from collections import Counter

import atexit
import traceback

# ---------------------------------------------------------------------------
# Frozen (PyInstaller .exe) support: resolve paths next to the .exe and
# redirect output to a log file (a windowed exe has no console — print would crash)
# ---------------------------------------------------------------------------

_frozen = getattr(sys, "frozen", False)
_script_dir = os.path.dirname(sys.executable) if _frozen else os.path.dirname(os.path.abspath(__file__))

if _frozen or sys.stdout is None or sys.stderr is None:
    _log_path = os.path.join(_script_dir, "speakpaste.log")
    try:
        if os.path.isfile(_log_path) and os.path.getsize(_log_path) > 5 * 1024 * 1024:
            os.replace(_log_path, _log_path + ".old")
        _log_file = open(_log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        # Exe dir not writable (e.g. Program Files) — fall back to %LOCALAPPDATA%
        _fallback_dir = os.path.join(os.environ.get("LOCALAPPDATA", _script_dir), "SpeakPaste")
        os.makedirs(_fallback_dir, exist_ok=True)
        _log_file = open(os.path.join(_fallback_dir, "speakpaste.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file


def _fatal_alert(title, details):
    """Show a blocking error dialog so startup failures aren't silent."""
    try:
        ctypes.windll.user32.MessageBoxW(None, str(details), str(title), 0x10)  # MB_ICONERROR
    except Exception:
        pass


try:
    import numpy as np
    import pyaudio
    import pyperclip
except Exception as _import_error:
    traceback.print_exc()
    _fatal_alert(
        "SpeakPaste failed to start",
        f"{type(_import_error).__name__}: {_import_error}\n\nFull traceback: speakpaste.log",
    )
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Single instance guard (Windows named mutex)
# ---------------------------------------------------------------------------

_mutex_name = "Global\\SpeakPaste_SingleInstance"
_mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _mutex_name)
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    print("⚠️  SpeakPaste is already running. Exiting.")
    raise SystemExit(0)

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

_env_path = os.path.join(_script_dir, ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    if os.path.isfile(_env_path):
        print()
        print("  ❌  NOTE: .env file found but python-dotenv not installed.")
        print("      pip install python-dotenv")
        print()


def _env(key, default, *, type_=str):
    full_key = key if key.startswith("WHISPER_PTT_") else f"WHISPER_PTT_{key}"
    raw = os.environ.get(full_key, os.environ.get(key, default))
    if type_ is bool:
        s = str(raw).strip().lower()
        if s in ("1", "true", "yes", "on"):
            return True
        if s in ("0", "false", "no", "off", ""):
            return False
        return False
    if type_ is int:
        return int(raw)
    if type_ is float:
        return float(raw)
    return str(raw)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WHISPER_MODEL = _env("WHISPER_MODEL", "large-v3")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "")
WHISPER_INITIAL_PROMPT = _env("WHISPER_INITIAL_PROMPT", "") or None
WHISPER_DEVICE = _env("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = _env("WHISPER_COMPUTE_TYPE", "float16")

HOTKEY = _env("HOTKEY", "alt_r").strip().lower().replace(" ", "")
if "+" in HOTKEY:
    _parts = HOTKEY.split("+", 1)
    HOTKEY_MODIFIER, HOTKEY_KEY = _parts[0].strip(), _parts[1].strip()
else:
    HOTKEY_MODIFIER, HOTKEY_KEY = None, HOTKEY

USE_LLM_TRANSFORM = _env("USE_LLM_TRANSFORM", "false", type_=bool)
OLLAMA_MODEL = _env("OLLAMA_MODEL", "gemma3:12b")
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434/api/generate")
DEFAULT_LLM_TRANSFORM_PROMPT = """Fix the following speech-to-text transcription. Rules:
- Fix grammar, punctuation, and capitalization
- Remove filler words (um, uh, like, etc.)
- Keep the original language ({detected_lang})
- Keep the original meaning — do NOT add or change content
- If it's already clean, return as-is
- Return ONLY the cleaned text, nothing else

Transcription: {raw_text}"""
LLM_TRANSFORM_PROMPT = _env("LLM_TRANSFORM_PROMPT", DEFAULT_LLM_TRANSFORM_PROMPT)

USE_SOUND = _env("USE_SOUND", "true", type_=bool)
SOUND_START = _env("SOUND_START", "start")
SOUND_STOP = _env("SOUND_STOP", "stop")

COPY_TO_CLIPBOARD = _env("COPY_TO_CLIPBOARD", "true", type_=bool)
PASTE_TO_ACTIVE_WINDOW = _env("PASTE_TO_ACTIVE_WINDOW", "true", type_=bool)
CLIPBOARD_AFTER_PASTE_POLICY = _env("CLIPBOARD_AFTER_PASTE_POLICY", "restore").strip().lower()
if CLIPBOARD_AFTER_PASTE_POLICY not in ("restore", "clear", "preserve"):
    raise SystemExit(
        f"Invalid: CLIPBOARD_AFTER_PASTE_POLICY must be restore/clear/preserve (got {CLIPBOARD_AFTER_PASTE_POLICY!r})."
    )
KEYS_AFTER_PASTE = _env("KEYS_AFTER_PASTE", "none").strip().lower()
if KEYS_AFTER_PASTE in ("", "none"):
    KEYS_AFTER_PASTE = None

SAMPLE_RATE = _env("SAMPLE_RATE", "16000", type_=int)
CHANNELS = 1
CHUNK_SIZE = _env("CHUNK_SIZE", "1024", type_=int)
AUDIO_FORMAT = pyaudio.paInt16

PADDING_SEC = _env("PADDING_SEC", "0.2", type_=float)
MIN_FRAMES = _env("MIN_FRAMES", "5", type_=int)
SILENCE_AMPLITUDE_THRESHOLD = _env("SILENCE_AMPLITUDE", "750", type_=int)

TRANSCRIBE_MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# Model loading (faster-whisper)
# ---------------------------------------------------------------------------

_whisper_model = None
_model_ready = threading.Event()
_transcribe_queue = queue.Queue(maxsize=3)

# ---------------------------------------------------------------------------
# Recording state
# ---------------------------------------------------------------------------

_recording = False
_audio_frames = []
_rec_thread = None
_mic_stream = None
_pyaudio_instance = None
_last_audio_frames = None
_hotkey_active = False


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

def _play_sound(sound_name):
    if not USE_SOUND:
        return
    def _play():
        if sound_name == "start":
            winsound.MessageBeep(winsound.MB_OK)
        elif sound_name == "stop":
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif os.path.isfile(sound_name):
            winsound.PlaySound(sound_name, winsound.SND_FILENAME | winsound.SND_ASYNC)
    threading.Thread(target=_play, daemon=True).start()


def _reinit_pyaudio():
    global _pyaudio_instance
    try:
        _pyaudio_instance.terminate()
    except Exception:
        pass
    _pyaudio_instance = pyaudio.PyAudio()
    print("🔄 PyAudio reinitialized")


def _recording_worker():
    """Open a fresh mic stream, read chunks, close when done."""
    global _mic_stream
    for attempt in range(3):
        try:
            _mic_stream = _pyaudio_instance.open(
                format=AUDIO_FORMAT,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )
            break
        except OSError as e:
            print(f"❌ Mic open error (attempt {attempt + 1}/3): {e}")
            _reinit_pyaudio()
            if attempt == 2:
                return
    while _recording:
        try:
            chunk = _mic_stream.read(CHUNK_SIZE, exception_on_overflow=False)
        except OSError as e:
            print(f"⚠️  Mic read error: {e}")
            break
        if _recording:
            _audio_frames.append(chunk)
    try:
        _mic_stream.stop_stream()
        _mic_stream.close()
    except Exception:
        pass
    _mic_stream = None


def start_recording():
    global _recording, _audio_frames, _rec_thread, _hotkey_active
    if _recording:
        return
    _hotkey_active = True
    if _rec_thread is not None and _rec_thread.is_alive():
        _rec_thread.join(timeout=2)
    _play_sound(SOUND_START)
    _update_tray_icon("recording")
    time.sleep(0.15)
    _audio_frames = []
    _recording = True
    _rec_thread = threading.Thread(target=_recording_worker, daemon=True)
    _rec_thread.start()
    print("🎙️ Recording...")


def frames_to_numpy(frames, prepend_silence_sec=0):
    raw = b"".join(frames)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if prepend_silence_sec > 0:
        silence = np.zeros(int(prepend_silence_sec * SAMPLE_RATE), dtype=np.float32)
        audio = np.concatenate([silence, audio])
    return audio


# ---------------------------------------------------------------------------
# Hallucination filter
# ---------------------------------------------------------------------------

_HALLUCINATION_PHRASES = {
    "transcription by castingwords",
    "thank you for watching",
    "thanks for watching",
    "please subscribe",
    "subscribe to my channel",
    "like and subscribe",
    "subtitles by",
    "translated by",
    "продолжение следует",
    "субтитры сделал",
    "субтитры добавил",
    "редактор субтитров",
    "спасибо за просмотр",
    "подписывайтесь на канал",
}

_PUNCT_RE = re.compile(r"[\s.…,!?;:]+")


_TAIL_HALLUCINATIONS = re.compile(
    r"[\s.,…]*(?:" + "|".join(re.escape(p) for p in _HALLUCINATION_PHRASES) + r")[\s.,…]*$",
    re.IGNORECASE,
)


def _strip_hallucination_tail(text):
    cleaned = _TAIL_HALLUCINATIONS.sub("", text).rstrip(" .,…")
    if cleaned != text:
        print(f'🧹 Stripped hallucination tail: "{text[len(cleaned):][:60]}"')
    return cleaned


def _is_hallucination(text):
    t = _PUNCT_RE.sub(" ", text.lower()).strip()
    if not t:
        return True
    if t in _HALLUCINATION_PHRASES:
        return True
    for phrase in _HALLUCINATION_PHRASES:
        cleaned = t.replace(phrase, "").strip()
        if not cleaned:
            return True
    return False


def _is_repetitive(text, threshold=3):
    words = text.lower().split()
    if len(words) < 4:
        return False
    for ngram_size in (1, 2, 3):
        ngrams = [" ".join(words[i:i+ngram_size]) for i in range(len(words) - ngram_size + 1)]
        counts = Counter(ngrams)
        most_common_count = counts.most_common(1)[0][1]
        if most_common_count >= threshold and most_common_count / len(ngrams) > 0.4:
            return True
    return False


# ---------------------------------------------------------------------------
# Transcription (faster-whisper / CUDA)
# ---------------------------------------------------------------------------

def transcribe(audio_np):
    print("🔄 Transcribing...")
    t0 = time.time()

    kwargs = {
        "language": WHISPER_LANGUAGE or None,
        "initial_prompt": WHISPER_INITIAL_PROMPT,
        "condition_on_previous_text": False,
        "temperature": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        "compression_ratio_threshold": 2.4,
        "log_prob_threshold": -1.0,
        "no_speech_threshold": 0.6,
        "beam_size": 5,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 500},
    }

    last_error = None
    for attempt in range(1, TRANSCRIBE_MAX_RETRIES + 1):
        try:
            segments, info = _whisper_model.transcribe(audio_np, **kwargs)
            text = _strip_hallucination_tail(
                " ".join(seg.text.strip() for seg in segments).strip()
            )
            lang = info.language or "auto"

            if _is_hallucination(text) or _is_repetitive(text):
                print(f'⚠️  Hallucination filtered: "{text[:80]}" (attempt {attempt})')
                if attempt < TRANSCRIBE_MAX_RETRIES:
                    kwargs["initial_prompt"] = None
                    continue
                return "", lang

            print(f"📝 Whisper ({time.time() - t0:.1f}s): {text}")
            return text, lang
        except Exception as e:
            last_error = e
            print(f"⚠️  Transcription error (attempt {attempt}/{TRANSCRIBE_MAX_RETRIES}): {e}")
            if attempt < TRANSCRIBE_MAX_RETRIES:
                time.sleep(0.5)

    print(f"❌ Transcription failed after {TRANSCRIBE_MAX_RETRIES} attempts: {last_error}")
    return "", "unknown"


def transform_with_llm(raw_text, detected_lang):
    if not raw_text.strip():
        return raw_text
    import requests
    print("🔄 LLM transform...")
    t0 = time.time()
    prompt = LLM_TRANSFORM_PROMPT.format(detected_lang=detected_lang, raw_text=raw_text)
    try:
        r = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": len(raw_text) * 2},
            },
            timeout=30,
        )
        result = r.json()["response"].strip()
        print(f"✨ LLM ({time.time() - t0:.1f}s): {result}")
        return result
    except Exception as e:
        print(f"❌ LLM error: {e}, using raw text")
        return raw_text


# ---------------------------------------------------------------------------
# Output: clipboard + paste via Win32 SendInput
# ---------------------------------------------------------------------------

VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]


def _send_key(vk, flags=0):
    extra = ctypes.pointer(ctypes.c_ulong(0))
    ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=extra)
    inp = INPUT(type=INPUT_KEYBOARD, ki=ki)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_ctrl_v():
    _send_key(VK_CONTROL)
    _send_key(VK_V)
    _send_key(VK_V, KEYEVENTF_KEYUP)
    _send_key(VK_CONTROL, KEYEVENTF_KEYUP)


_VK_MAP = {
    "enter": VK_RETURN, "return": VK_RETURN,
    "tab": 0x09, "space": 0x20, "escape": 0x1B, "delete": 0x2E,
}


def _send_keys_after_paste():
    if not KEYS_AFTER_PASTE:
        return
    parts = KEYS_AFTER_PASTE.split("+")
    if len(parts) == 1:
        vk = _VK_MAP.get(parts[0].lower())
        if vk:
            _send_key(vk)
            _send_key(vk, KEYEVENTF_KEYUP)
    else:
        mod_name = parts[0].lower()
        mod_vk = (VK_CONTROL if "ctrl" in mod_name or "control" in mod_name
                  else 0x10 if "shift" in mod_name
                  else 0x12 if "alt" in mod_name
                  else None)
        key_vk = _VK_MAP.get(parts[1].lower())
        if mod_vk and key_vk:
            _send_key(mod_vk)
            _send_key(key_vk)
            _send_key(key_vk, KEYEVENTF_KEYUP)
            _send_key(mod_vk, KEYEVENTF_KEYUP)


def paste_to_front(text):
    if not text.strip():
        print("❌ Empty text, skipping")
        return
    if not COPY_TO_CLIPBOARD and not PASTE_TO_ACTIVE_WINDOW:
        print("✅ Done (console only)")
        return
    old = pyperclip.paste()
    pyperclip.copy(text)
    if COPY_TO_CLIPBOARD:
        print("📋 Copied to clipboard!")
    if PASTE_TO_ACTIVE_WINDOW:
        time.sleep(0.05)
        _send_ctrl_v()
        time.sleep(0.1)
        if KEYS_AFTER_PASTE:
            time.sleep(0.05)
            _send_keys_after_paste()
        suffix = f' + "{KEYS_AFTER_PASTE.upper()}"' if KEYS_AFTER_PASTE else ""
        print(f"✅ Pasted to active window{suffix}!")
        if CLIPBOARD_AFTER_PASTE_POLICY == "restore":
            time.sleep(0.05)
            pyperclip.copy(old)
        elif CLIPBOARD_AFTER_PASTE_POLICY == "clear":
            pyperclip.copy("")


# ---------------------------------------------------------------------------
# Transcription worker thread
# ---------------------------------------------------------------------------

def _transcription_worker():
    global _whisper_model
    from faster_whisper import WhisperModel

    print(f"⏳ Loading faster-whisper model '{WHISPER_MODEL}' on {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE})...")
    _whisper_model = WhisperModel(
        WHISPER_MODEL,
        device=WHISPER_DEVICE,
        compute_type=WHISPER_COMPUTE_TYPE,
    )
    warmup = np.zeros(SAMPLE_RATE, dtype=np.float32)
    list(_whisper_model.transcribe(warmup, language="en"))
    print("✅ faster-whisper loaded!")
    _model_ready.set()

    while True:
        frames = _transcribe_queue.get()
        if frames is None:
            break
        try:
            audio_np = frames_to_numpy(frames, prepend_silence_sec=PADDING_SEC)
            raw_text, lang = transcribe(audio_np)
            if raw_text.strip():
                final_text = transform_with_llm(raw_text, lang) if USE_LLM_TRANSFORM else raw_text
                paste_to_front(final_text)
            else:
                print("❌ Empty transcription, skipping paste")
        except Exception as e:
            print(f"❌ Transcription worker error: {e}")
        _update_tray_icon("idle")


def stop_recording_and_process():
    global _recording, _rec_thread, _last_audio_frames
    if not _recording:
        return
    _recording = False
    if _rec_thread:
        _rec_thread.join(timeout=2)
        _rec_thread = None
    _update_tray_icon("idle")

    frames = list(_audio_frames)
    duration_sec = len(frames) * CHUNK_SIZE / SAMPLE_RATE
    print(f"⏹️ Recorded {duration_sec:.1f}s")

    if duration_sec <= 0.7 or len(frames) < MIN_FRAMES:
        print("❌ Recording too short")
        _play_sound(SOUND_STOP)
        return

    raw = b"".join(frames)
    audio_int16 = np.frombuffer(raw, dtype=np.int16)
    if audio_int16.size == 0 or np.max(np.abs(audio_int16)) < SILENCE_AMPLITUDE_THRESHOLD:
        print("❌ Audio too quiet / silence, skipping")
        return

    _last_audio_frames = frames
    _update_tray_icon("transcribing")
    try:
        _transcribe_queue.put_nowait(frames)
    except queue.Full:
        print("⚠️  Transcription queue full, dropping recording")
        _update_tray_icon("idle")
        return
    _play_sound(SOUND_STOP)


def retranscribe_last():
    if _last_audio_frames is None:
        print("❌ No previous recording to retranscribe")
        return
    if _recording:
        return
    print("🔁 Retranscribing last recording...")
    _update_tray_icon("transcribing")
    _transcribe_queue.put(list(_last_audio_frames))


# ---------------------------------------------------------------------------
# System tray icon (pystray)
# ---------------------------------------------------------------------------

_tray_icon = None
_TRAY_STATES = {"idle": "🎤 SpeakPaste", "recording": "🔴 Recording...", "transcribing": "⏳ Transcribing..."}


def _create_tray_image(state="idle"):
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    colors = {"idle": (0, 200, 170), "recording": (220, 50, 50), "transcribing": (255, 180, 0)}
    color = colors.get(state, colors["idle"])
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=color)
    # Microphone shape
    draw.rounded_rectangle([24, 12, 40, 36], radius=6, fill=(255, 255, 255))
    draw.arc([20, 28, 44, 48], start=0, end=180, fill=(255, 255, 255), width=3)
    draw.line([32, 48, 32, 54], fill=(255, 255, 255), width=3)
    draw.line([24, 54, 40, 54], fill=(255, 255, 255), width=2)
    return img


def _update_tray_icon(state):
    if _tray_icon is None:
        return
    _tray_icon.icon = _create_tray_image(state)
    _tray_icon.title = _TRAY_STATES.get(state, _TRAY_STATES["idle"])


def _on_tray_retranscribe(icon, item):
    threading.Thread(target=retranscribe_last, daemon=True).start()


def _on_tray_quit(icon, item):
    icon.stop()
    os._exit(0)


# ---------------------------------------------------------------------------
# Hotkey listener (pynput — cross-platform)
# ---------------------------------------------------------------------------

def _on_hotkey_press(_event=None):
    if not _recording:
        start_recording()


def _on_hotkey_release(_event=None):
    global _hotkey_active
    if _hotkey_active:
        _hotkey_active = False
        stop_recording_and_process()


def _start_hotkey_listener():
    from pynput import keyboard as pynput_keyboard
    Key = pynput_keyboard.Key
    pressed = set()

    def _spec_from_name(name):
        if not name:
            return None
        n = str(name).strip().lower()
        if n in ("alt", "option", "opt"):
            return Key.alt
        if n in ("alt_r", "option_r", "opt_r", "right_alt", "right_option"):
            return Key.alt_r
        if n in ("ctrl", "control"):
            return Key.ctrl
        if n in ("ctrl_r", "control_r", "right_ctrl"):
            return Key.ctrl_r
        if n in ("shift",):
            return Key.shift
        if n in ("shift_r", "right_shift"):
            return Key.shift_r
        if n.startswith("f") and n[1:].isdigit():
            return getattr(Key, n, None)
        if len(n) == 1:
            return n
        return None

    hotkey_key_spec = _spec_from_name(HOTKEY_KEY)
    hotkey_mod_spec = _spec_from_name(HOTKEY_MODIFIER) if HOTKEY_MODIFIER else None

    def _matches(key, spec):
        if spec is None:
            return False
        if isinstance(spec, str):
            return getattr(key, "char", None) == spec
        return key == spec

    def on_press(key):
        pressed.add(key)
        if HOTKEY_MODIFIER is None:
            if _matches(key, hotkey_key_spec):
                _on_hotkey_press()
        else:
            if _matches(key, hotkey_key_spec) and any(_matches(k, hotkey_mod_spec) for k in pressed):
                _on_hotkey_press()

    def on_release(key):
        if HOTKEY_MODIFIER is None:
            if _matches(key, hotkey_key_spec):
                _on_hotkey_release()
        else:
            if _matches(key, hotkey_key_spec) or _matches(key, hotkey_mod_spec):
                _on_hotkey_release()
        pressed.discard(key)

    listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    return listener


# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

def _format_banner():
    w = 70
    def line(s, width=None):
        width = width or w
        padded = (s + " " * width)[:width]
        return "║" + padded + "║"
    parts = [
        "╔" + "═" * w + "╗\n",
        line("     🎤 SpeakPaste ready!", w - 1) + "\n",
        line("") + "\n",
        line(f'     Hotkey: "{HOTKEY.upper()}" (hold to record, release to transcribe)') + "\n",
        line(f"     Model: {WHISPER_MODEL}") + "\n",
        line(f"     Device: {WHISPER_DEVICE} ({WHISPER_COMPUTE_TYPE})") + "\n",
        line(f"     LLM transform: {'ON' if USE_LLM_TRANSFORM else 'OFF'}") + "\n",
        line(f"     Copy to clipboard: {'ON' if COPY_TO_CLIPBOARD else 'OFF'}") + "\n",
        line(f"     Paste to active window: {'ON' if PASTE_TO_ACTIVE_WINDOW else 'OFF'}") + "\n",
    ]
    if PASTE_TO_ACTIVE_WINDOW:
        parts.append((line(f'     Keys after paste: "{KEYS_AFTER_PASTE.upper()}"') if KEYS_AFTER_PASTE else line("     Keys after paste: —")) + "\n")
    parts.extend([line("") + "\n", line('     "CTRL+C" to exit') + "\n", "╚" + "═" * w + "╝"])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Setup worker
# ---------------------------------------------------------------------------

def _setup_worker():
    global _pyaudio_instance
    _model_ready.wait()

    _pyaudio_instance = pyaudio.PyAudio()
    atexit.register(lambda: _pyaudio_instance.terminate())

    print(_format_banner())
    print(f'👂 Listening — hold "{HOTKEY.upper()}" to start recording.')
    _start_hotkey_listener()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import pystray

    threading.Thread(target=_transcription_worker, daemon=True).start()
    threading.Thread(target=_setup_worker, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem("Retranscribe Last", _on_tray_retranscribe),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit SpeakPaste", _on_tray_quit),
    )

    global _tray_icon
    _tray_icon = pystray.Icon(
        "SpeakPaste",
        icon=_create_tray_image("idle"),
        title="SpeakPaste",
        menu=menu,
    )

    _tray_icon.run()


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 Exiting...")
        raise SystemExit(0)
    except Exception as e:
        traceback.print_exc()
        _fatal_alert(
            "SpeakPaste crashed",
            f"{type(e).__name__}: {e}\n\nFull traceback: speakpaste.log",
        )
        raise SystemExit(1)
