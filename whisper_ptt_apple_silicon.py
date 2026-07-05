#!/usr/bin/env python3
"""
SpeakPaste: push-to-talk voice-to-text using mlx-whisper on Metal.
Hold hotkey -> speak -> release -> transcription pasted into the active window.

Config: WHISPER_PTT_* env vars or .env file (see .env.example-apple-silicon).

Dependencies: mlx-whisper, pyaudio, keyboard, pyperclip, requests.
Optional: Ollama for LLM transform.
"""

# Hide from Dock BEFORE any other imports (prevents bounce icon during slow module loads)
try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
except Exception:
    pass

import atexit
import fcntl
import os
import shutil

_script_dir = os.path.dirname(os.path.abspath(__file__))


def _rotate_log(path, max_bytes=5 * 1024 * 1024):
    """Copy an oversized log to .old and truncate it in place.

    Truncate (not rename): the C wrapper holds these files open in O_APPEND
    mode, so a rename would silently redirect all future output to the .old file.
    """
    try:
        if os.path.isfile(path) and os.path.getsize(path) > max_bytes:
            shutil.copyfile(path, path + ".old")
            os.truncate(path, 0)
    except Exception:
        pass


_rotate_log(os.path.join(_script_dir, "whisper_ptt.log"))
_rotate_log(os.path.join(_script_dir, "whisper_ptt.error.log"))

_lock_path = os.path.join(_script_dir, ".speakpaste.lock")
_lock_fd = open(_lock_path, "w")
try:
    fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("⚠️  SpeakPaste is already running. Exiting.")
    raise SystemExit(0)

def _cleanup_lock():
    try:
        fcntl.flock(_lock_fd, fcntl.LOCK_UN)
        _lock_fd.close()
        os.unlink(_lock_path)
    except Exception:
        pass

atexit.register(_cleanup_lock)

import gc
import re
from collections import Counter
import queue
import subprocess
import sys
import time
import threading
import traceback


def _fatal_alert(title, details):
    """Show a blocking error dialog so startup failures aren't silent."""
    try:
        from AppKit import NSAlert, NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(details)
        alert.runModal()
    except Exception:
        pass


try:
    import numpy as np
    import pyaudio
    import pyperclip
    import requests
    import mlx_whisper
    import mlx.core as mx
except Exception as _import_error:
    traceback.print_exc()
    _fatal_alert(
        "SpeakPaste failed to start",
        f"{type(_import_error).__name__}: {_import_error}\n\n"
        "Full traceback: whisper_ptt.error.log",
    )
    raise SystemExit(1)

_env_path = os.path.join(_script_dir, ".env")
try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    if os.path.isfile(_env_path):
        print()
        print("  ❌  NOTE: You have a .env file but python-dotenv is not installed, so it is not loaded.")
        print("      Config comes from environment variables only.")
        print("      To use .env, run:  pip install python-dotenv")
        print()


def _env(key, default, *, type_=str):
    """Read env var with type coercion. WHISPER_PTT_ prefix is optional."""
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


# -----------------------------------------------------------------------------
# Config (from env; values below are defaults)
# -----------------------------------------------------------------------------

# Whisper (DEVICE and COMPUTE_TYPE not needed — MLX uses Metal automatically)
WHISPER_MODEL = _env("WHISPER_MODEL", "large-v3-turbo")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "en")
WHISPER_INITIAL_PROMPT = _env("WHISPER_INITIAL_PROMPT", "") or None
# Unload the model after this many idle minutes to free ~1.5-2 GB RAM (0 = keep loaded).
# Next transcription reloads it automatically (+1-3s once).
MODEL_IDLE_UNLOAD_MIN = _env("MODEL_IDLE_UNLOAD_MIN", "30", type_=int)

# Hotkey (hold to record, release to stop). Default: option
HOTKEY = _env("HOTKEY", "option").strip().lower().replace(" ", "")
if "+" in HOTKEY:
    _parts = HOTKEY.split("+", 1)
    HOTKEY_MODIFIER, HOTKEY_KEY = _parts[0].strip(), _parts[1].strip()
else:
    HOTKEY_MODIFIER, HOTKEY_KEY = None, HOTKEY

# LLM transform (Ollama) — optional, OFF by default
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

# Sound feedback
USE_SOUND = _env("USE_SOUND", "true", type_=bool)
SOUND_START = _env("SOUND_START", "/System/Library/Sounds/Pop.aiff")
SOUND_STOP = _env("SOUND_STOP", "/System/Library/Sounds/Glass.aiff")

# Output: copy to clipboard and/or paste to active window
COPY_TO_CLIPBOARD = _env("COPY_TO_CLIPBOARD", "true", type_=bool)
PASTE_TO_ACTIVE_WINDOW = _env("PASTE_TO_ACTIVE_WINDOW", "true", type_=bool)
CLIPBOARD_AFTER_PASTE_POLICY = _env("CLIPBOARD_AFTER_PASTE_POLICY", "restore").strip().lower()
if CLIPBOARD_AFTER_PASTE_POLICY not in ("restore", "clear", "preserve"):
    raise SystemExit(
        f"Invalid config: CLIPBOARD_AFTER_PASTE_POLICY must be one of restore, clear, preserve (got {CLIPBOARD_AFTER_PASTE_POLICY!r})."
    )
KEYS_AFTER_PASTE = _env("KEYS_AFTER_PASTE", "enter").strip().lower()
if KEYS_AFTER_PASTE in ("", "none"):
    KEYS_AFTER_PASTE = None

# Audio
SAMPLE_RATE = _env("SAMPLE_RATE", "16000", type_=int)
CHANNELS = 1
CHUNK_SIZE = _env("CHUNK_SIZE", "1024", type_=int)
AUDIO_FORMAT = pyaudio.paInt16

# Recording
PADDING_SEC = _env("PADDING_SEC", "0.2", type_=float)
MIN_FRAMES = _env("MIN_FRAMES", "5", type_=int)
# Simple silence gate: max int16 amplitude below this is treated as silence.
SILENCE_AMPLITUDE_THRESHOLD = _env("SILENCE_AMPLITUDE", "750", type_=int)


# -----------------------------------------------------------------------------
# MLX model name → HuggingFace repo mapping
# -----------------------------------------------------------------------------

_MLX_MODEL_MAP = {
    "tiny": "mlx-community/whisper-tiny",
    "tiny.en": "mlx-community/whisper-tiny.en",
    "base": "mlx-community/whisper-base",
    "base.en": "mlx-community/whisper-base.en",
    "small": "mlx-community/whisper-small",
    "small.en": "mlx-community/whisper-small.en",
    "medium": "mlx-community/whisper-medium",
    "medium.en": "mlx-community/whisper-medium.en",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "turbo": "mlx-community/whisper-turbo",
}


def _resolve_model(name):
    """Resolve short model name to mlx-community HuggingFace repo. Pass-through if already a repo path."""
    return _MLX_MODEL_MAP.get(name, name)


# -----------------------------------------------------------------------------
# Recording state
# -----------------------------------------------------------------------------

_recording = False
_audio_frames = []
_rec_thread = None
_mic_stream = None
_pyaudio_instance = None
_mlx_model_path = None
_transcribe_queue = queue.Queue(maxsize=3)
_model_ready = threading.Event()
_last_audio_frames = None


# -----------------------------------------------------------------------------
# Audio
# -----------------------------------------------------------------------------

def _play_sound(sound_path):
    """Play a system sound via afplay in a background thread."""
    if USE_SOUND and sound_path and os.path.isfile(sound_path):
        def _play():
            subprocess.run(["afplay", sound_path], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=_play, daemon=True).start()


def _reinit_pyaudio(verbose=True):
    """Recreate the PyAudio instance when the audio subsystem is corrupted."""
    global _pyaudio_instance
    try:
        _pyaudio_instance.terminate()
    except Exception:
        pass
    _pyaudio_instance = pyaudio.PyAudio()
    if verbose:
        print("🔄 PyAudio reinitialized")


def _recording_worker():
    """Open a fresh mic stream, read chunks, close when done."""
    global _mic_stream
    # A PyAudio instance caches the device list; after a device change/sleep the
    # first open fails with AUHAL -10851 and can eat the whole recording.
    # A fresh instance (~tens of ms) opens reliably on the first try.
    _reinit_pyaudio(verbose=False)
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


_hotkey_active = False


def start_recording():
    """Start recording with a fresh mic stream each time."""
    global _recording, _audio_frames, _rec_thread, _hotkey_active
    if _recording:
        return
    _hotkey_active = True
    if _rec_thread is not None and _rec_thread.is_alive():
        _rec_thread.join(timeout=2)
    _play_sound(SOUND_START)
    _update_status_icon("recording")
    time.sleep(0.15)
    _audio_frames = []
    _recording = True
    _rec_thread = threading.Thread(target=_recording_worker, daemon=True)
    _rec_thread.start()
    print("🎙️ Recording...")


def frames_to_numpy(frames, prepend_silence_sec=0):
    """Raw PCM int16 frames → float32 numpy array normalised to [-1, 1]."""
    raw = b"".join(frames)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if prepend_silence_sec > 0:
        silence = np.zeros(int(prepend_silence_sec * SAMPLE_RATE), dtype=np.float32)
        audio = np.concatenate([silence, audio])
    return audio


# -----------------------------------------------------------------------------
# Transcription and LLM
# -----------------------------------------------------------------------------

TRANSCRIBE_MAX_RETRIES = 3

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
        print(f"🧹 Stripped hallucination tail: \"{text[len(cleaned):][:60]}\"")
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


def transcribe(audio_np):
    """Transcribe with retry logic. Returns (text, language_code)."""
    print("🔄 Transcribing...")
    t0 = time.time()
    kwargs = {
        "path_or_hf_repo": _mlx_model_path,
        "initial_prompt": WHISPER_INITIAL_PROMPT,
        "fp16": True,
        "condition_on_previous_text": False,
        "temperature": (0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        "compression_ratio_threshold": 2.4,
        "logprob_threshold": -1.0,
        "no_speech_threshold": 0.6,
    }
    if WHISPER_LANGUAGE:
        kwargs["language"] = WHISPER_LANGUAGE

    last_error = None
    for attempt in range(1, TRANSCRIBE_MAX_RETRIES + 1):
        try:
            result = mlx_whisper.transcribe(audio_np, **kwargs)
            text = _strip_hallucination_tail(result["text"].strip())
            lang = result.get("language", WHISPER_LANGUAGE or "auto")

            if _is_hallucination(text) or _is_repetitive(text):
                print(f"⚠️  Hallucination filtered: \"{text[:80]}\" (attempt {attempt})")
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
    """LLM transform: post-process transcription via Ollama."""
    if not raw_text.strip():
        return raw_text
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


# -----------------------------------------------------------------------------
# Output: clipboard and/or paste to active window
# -----------------------------------------------------------------------------

_KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32,
    "i": 34, "p": 35, "l": 37, "j": 38, "k": 40, "n": 45, "m": 46,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "delete": 51,
    "escape": 53,
}

_CG_MODIFIER_FLAGS = {
    "command": 0x100000,
    "control": 0x040000,
    "option":  0x080000,
    "shift":   0x020000,
}


def _send_key_via_cgevent(keycode, modifier=None):
    """Send a keystroke via CGEvent (needs Accessibility)."""
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventSetFlags,
        CGEventPost,
        kCGHIDEventTap,
    )
    flags = _CG_MODIFIER_FLAGS.get(modifier, 0) if modifier else 0
    ev_down = CGEventCreateKeyboardEvent(None, keycode, True)
    ev_up = CGEventCreateKeyboardEvent(None, keycode, False)
    if flags:
        CGEventSetFlags(ev_down, flags)
        CGEventSetFlags(ev_up, flags)
    CGEventPost(kCGHIDEventTap, ev_down)
    CGEventPost(kCGHIDEventTap, ev_up)


def _send_keys_after_paste():
    """Parse KEYS_AFTER_PASTE (e.g. 'enter', 'ctrl+enter') and send via CGEvent."""
    if not KEYS_AFTER_PASTE:
        return
    parts = KEYS_AFTER_PASTE.split("+")
    if len(parts) == 1:
        code = _KEY_CODES.get(parts[0].lower())
        if code is not None:
            _send_key_via_cgevent(code)
    else:
        modifier = parts[0].replace("ctrl", "control").replace("cmd", "command")
        code = _KEY_CODES.get(parts[1].lower())
        if code is not None:
            _send_key_via_cgevent(code, modifier=modifier)


def paste_to_front(text):
    """Copy to clipboard and paste to active window via CGEvent Cmd+V."""
    if not text.strip():
        print("❌ Empty text, skipping")
        return
    if not COPY_TO_CLIPBOARD and not PASTE_TO_ACTIVE_WINDOW:
        print("✅ Done (console only)")
        return
    pyperclip.copy(text)
    if COPY_TO_CLIPBOARD:
        print("📋 Copied to clipboard!")
    if PASTE_TO_ACTIVE_WINDOW:
        try:
            _send_key_via_cgevent(9, modifier="command")
        except Exception as e:
            print(f"⚠️  Paste failed: {e}")
            print("💡 Text is in clipboard — paste manually with Cmd+V")
            return
        time.sleep(0.15)
        if KEYS_AFTER_PASTE:
            time.sleep(0.05)
            _send_keys_after_paste()
        suffix = f' + "{KEYS_AFTER_PASTE.upper()}"' if KEYS_AFTER_PASTE else ""
        print(f"✅ Pasted to active window{suffix}!")


# -----------------------------------------------------------------------------
# Transcription worker (all MLX/Metal ops on a single thread)
# -----------------------------------------------------------------------------

def _transcription_worker():
    """Persistent thread owning all MLX operations — Metal requires same-thread access."""
    global _mlx_model_path
    _mlx_model_path = _resolve_model(WHISPER_MODEL)
    print(f"⏳ Loading mlx-whisper model '{_mlx_model_path}'... (first run downloads from HuggingFace)")
    warmup_audio = np.zeros(SAMPLE_RATE, dtype=np.float32)
    kwargs = {
        "path_or_hf_repo": _mlx_model_path,
        "fp16": True,
        "verbose": False,
    }
    if WHISPER_LANGUAGE:
        kwargs["language"] = WHISPER_LANGUAGE
    mlx_whisper.transcribe(warmup_audio, **kwargs)
    mx.clear_cache()
    print("✅ mlx-whisper loaded!")
    _model_ready.set()

    last_use = time.time()
    model_unloaded = False
    while True:
        try:
            frames = _transcribe_queue.get(timeout=60)
        except queue.Empty:
            if (MODEL_IDLE_UNLOAD_MIN > 0 and not model_unloaded
                    and time.time() - last_use > MODEL_IDLE_UNLOAD_MIN * 60):
                from mlx_whisper.transcribe import ModelHolder
                ModelHolder.model = None
                ModelHolder.model_path = None
                mx.clear_cache()
                gc.collect()
                model_unloaded = True
                print(f"💤 Model unloaded after {MODEL_IDLE_UNLOAD_MIN} min idle (RAM freed)")
            continue
        if frames is None:
            break
        try:
            if model_unloaded:
                print("⏳ Reloading model after idle...")
                model_unloaded = False
            audio_np = frames_to_numpy(frames, prepend_silence_sec=PADDING_SEC)
            raw_text, lang = transcribe(audio_np)
            if raw_text.strip():
                if USE_LLM_TRANSFORM:
                    final_text = transform_with_llm(raw_text, lang)
                else:
                    final_text = raw_text
                paste_to_front(final_text)
            else:
                print("❌ Empty transcription, skipping paste")
        except Exception as e:
            print(f"❌ Transcription worker error: {e}")
        # Free the Metal buffer cache (can hold hundreds of MB) — weights stay loaded.
        mx.clear_cache()
        last_use = time.time()
        _update_status_icon("idle")


def stop_recording_and_process():
    """Stop recording, close mic, then enqueue for transcription."""
    global _recording, _rec_thread
    if not _recording:
        return
    _recording = False
    if _rec_thread:
        _rec_thread.join(timeout=2)
        _rec_thread = None
    _update_status_icon("idle")

    frames = list(_audio_frames)
    duration_sec = len(frames) * CHUNK_SIZE / SAMPLE_RATE
    print(f"⏹️ Recorded {duration_sec:.1f}s")

    # Only process recordings longer than 0.7 seconds in total.
    if duration_sec <= 0.7 or len(frames) < MIN_FRAMES:
        print("❌ Recording too short")
        _play_sound(SOUND_STOP)
        return

    # Simple silence / noise gate: skip very low-energy audio.
    raw = b"".join(frames)
    audio_int16 = np.frombuffer(raw, dtype=np.int16)
    if audio_int16.size == 0 or np.max(np.abs(audio_int16)) < SILENCE_AMPLITUDE_THRESHOLD:
        print("❌ Audio too quiet / silence, skipping")
        return

    global _last_audio_frames
    _last_audio_frames = frames
    _update_status_icon("transcribing")
    try:
        _transcribe_queue.put_nowait(frames)
    except queue.Full:
        print("⚠️  Transcription queue full, dropping recording")
        _update_status_icon("idle")
        return
    _play_sound(SOUND_STOP)


def retranscribe_last():
    """Re-transcribe the last recording without re-recording."""
    if _last_audio_frames is None:
        print("❌ No previous recording to retranscribe")
        return
    if _recording:
        return
    print("🔁 Retranscribing last recording...")
    _update_status_icon("transcribing")
    _transcribe_queue.put(list(_last_audio_frames))


# -----------------------------------------------------------------------------
# Menu bar status icon (in-process via PyObjC)
# -----------------------------------------------------------------------------

_SF_SYMBOLS = {
    "idle": "mic.fill",
    "recording": "record.circle",
    "transcribing": "ellipsis.circle",
}
_status_item = None


def _make_icon(symbol_name):
    from AppKit import NSImage, NSImageSymbolConfiguration, NSFontWeightRegular
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, None)
    if img:
        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_(12, NSFontWeightRegular)
        img = img.imageWithSymbolConfiguration_(cfg)
        img.setTemplate_(True)
    return img


from Foundation import NSObject


class _MenuDelegate(NSObject):
    def retranscribe_(self, sender):
        threading.Thread(target=retranscribe_last, daemon=True).start()


_menu_delegate = None


def _init_status_bar():
    """Create the NSStatusItem on the main thread."""
    global _status_item, _menu_delegate
    from AppKit import NSStatusBar, NSSquareStatusItemLength, NSMenu, NSMenuItem

    _status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
        NSSquareStatusItemLength
    )
    icon = _make_icon(_SF_SYMBOLS["idle"])
    if icon:
        _status_item.button().setImage_(icon)
        _status_item.button().setTitle_("")
    else:
        _status_item.button().setTitle_("M")

    _menu_delegate = _MenuDelegate.alloc().init()

    menu = NSMenu.alloc().init()
    retranscribe_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Retranscribe Last", "retranscribe:", "r"
    )
    retranscribe_item.setTarget_(_menu_delegate)
    menu.addItem_(retranscribe_item)
    menu.addItem_(NSMenuItem.separatorItem())
    quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "Quit SpeakPaste", "terminate:", "q"
    )
    menu.addItem_(quit_item)
    _status_item.setMenu_(menu)
    print("✅ Menu bar icon active")


def _set_icon_on_main(symbol_name):
    icon = _make_icon(symbol_name)
    if icon and _status_item:
        _status_item.button().setImage_(icon)


def _update_status_icon(state):
    """Update the menu bar SF Symbol icon. Thread-safe dispatch to main thread."""
    if _status_item is None:
        return
    symbol = _SF_SYMBOLS.get(state, _SF_SYMBOLS["idle"])
    from PyObjCTools import AppHelper
    AppHelper.callAfter(_set_icon_on_main, symbol)


# -----------------------------------------------------------------------------
# Hotkey and banner
# -----------------------------------------------------------------------------

def _on_hotkey_press(_event=None):
    if not _recording:
        start_recording()


def _on_hotkey_release(_event=None):
    global _hotkey_active
    if _hotkey_active:
        _hotkey_active = False
        stop_recording_and_process()


def _check_accessibility():
    """Check if the process has macOS Accessibility permission and guide the user if not."""
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices")
            or "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        trusted = lib.AXIsProcessTrusted()
        if not trusted:
            print()
            print("  ⚠️  Accessibility permission required!")
            print("  Hotkeys and paste won't work without it.")
            print()
            print("  Fix: System Settings → Privacy & Security → Accessibility")
            print("        → enable SpeakPaste")
            print()
            subprocess.Popen(
                ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return False
    except Exception:
        pass
    return True


_MODIFIER_KEYCODES = {
    "option_r": 61, "opt_r": 61, "alt_r": 61, "right_option": 61, "right_alt": 61,
    "option": 58, "opt": 58, "alt": 58,
    "cmd": 55, "command": 55,
    "cmd_r": 54, "command_r": 54,
    "ctrl": 59, "control": 59,
    "ctrl_r": 62, "control_r": 62, "right_ctrl": 62, "right_control": 62,
    "shift": 56, "shift_r": 60, "right_shift": 60,
}

_MODIFIER_FLAGS = {
    58: 0x080000, 61: 0x080000,
    55: 0x100000, 54: 0x100000,
    59: 0x040000, 62: 0x040000,
    56: 0x020000, 60: 0x020000,
}


def _start_hotkey_listener_mac():
    """Hotkey listener using NSEvent monitors — doesn't interfere with fn/🌐 key."""
    _check_accessibility()

    from AppKit import NSEvent

    NSFlagsChangedMask = 1 << 12
    NSKeyDownMask = 1 << 10

    hotkey_keycode = _MODIFIER_KEYCODES.get(HOTKEY_KEY.lower())
    hotkey_is_modifier = hotkey_keycode is not None

    mod_keycode = _MODIFIER_KEYCODES.get(HOTKEY_MODIFIER.lower()) if HOTKEY_MODIFIER else None
    mod_flag = _MODIFIER_FLAGS.get(mod_keycode, 0) if mod_keycode else 0

    if HOTKEY_MODIFIER is None and hotkey_is_modifier:
        hotkey_flag = _MODIFIER_FLAGS.get(hotkey_keycode, 0)
        _was_pressed = [False]

        def flags_handler(event):
            if event.keyCode() != hotkey_keycode:
                return
            is_down = bool(event.modifierFlags() & hotkey_flag)
            if is_down and not _was_pressed[0]:
                _was_pressed[0] = True
                _on_hotkey_press()
            elif not is_down and _was_pressed[0]:
                _was_pressed[0] = False
                _on_hotkey_release()

        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSFlagsChangedMask, flags_handler)
        print(f"🎯 Hotkey active (NSEvent, keycode={hotkey_keycode})")

    elif HOTKEY_MODIFIER and not hotkey_is_modifier:
        _CHAR_KEYCODES = {
            "space": 49, "return": 36, "enter": 36, "tab": 48, "escape": 53,
            "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
            "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
            "y": 16, "t": 17, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38,
            "k": 40, "n": 45, "m": 46, "o": 31,
        }
        key_kc = _CHAR_KEYCODES.get(HOTKEY_KEY.lower())
        _mod_held = [False]
        _was_pressed = [False]

        def flags_handler(event):
            if event.keyCode() == mod_keycode:
                _mod_held[0] = bool(event.modifierFlags() & mod_flag)
                if not _mod_held[0] and _was_pressed[0]:
                    _was_pressed[0] = False
                    _on_hotkey_release()

        def key_handler(event):
            if event.keyCode() == key_kc and _mod_held[0] and not _was_pressed[0]:
                _was_pressed[0] = True
                _on_hotkey_press()

        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSFlagsChangedMask, flags_handler)
        NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSKeyDownMask, key_handler)
        print(f"🎯 Hotkey active (NSEvent, mod={HOTKEY_MODIFIER}, key={HOTKEY_KEY})")

    else:
        print(f"⚠️  Hotkey '{HOTKEY}' not supported natively, using pynput fallback")
        _start_hotkey_listener_pynput()
        return


def _start_hotkey_listener_pynput():
    """Fallback: pynput for non-modifier hotkeys."""
    try:
        from pynput import keyboard as pynput_keyboard
    except ImportError:
        print("❌ pynput is required for this hotkey. pip install pynput")
        return

    Key = pynput_keyboard.Key
    pressed = set()

    def _spec_from_name(name):
        if not name:
            return None
        n = str(name).strip().lower()
        mapping = {
            "cmd": Key.cmd, "command": Key.cmd,
            "option": Key.alt, "opt": Key.alt, "alt": Key.alt,
            "option_r": Key.alt_r, "opt_r": Key.alt_r, "alt_r": Key.alt_r,
            "ctrl": Key.ctrl, "control": Key.ctrl,
            "ctrl_r": Key.ctrl_r, "control_r": Key.ctrl_r,
            "shift": Key.shift, "shift_r": Key.shift_r,
        }
        if n in mapping:
            return mapping[n]
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
            if _matches(key, hotkey_key_spec) and any(
                _matches(k, hotkey_mod_spec) for k in pressed
            ):
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
        line(f"     Model: {_mlx_model_path}") + "\n",
        line(f"     LLM transform: {'ON' if USE_LLM_TRANSFORM else 'OFF'}") + "\n",
        line(f"     Copy to clipboard: {'ON' if COPY_TO_CLIPBOARD else 'OFF'}") + "\n",
        line(f"     Paste to active window: {'ON' if PASTE_TO_ACTIVE_WINDOW else 'OFF'}") + "\n",
    ]
    if PASTE_TO_ACTIVE_WINDOW:
        parts.append((line(f'     Keys after paste: "{KEYS_AFTER_PASTE.upper()}"') if KEYS_AFTER_PASTE else line("     Keys after paste: —")) + "\n")
    parts.extend([line("") + "\n", line('     "CTRL+C" to exit') + "\n", "╚" + "═" * w + "╝"])
    return "".join(parts)


def _setup_worker():
    """Background thread: load model, init PyAudio, start hotkey listener."""
    global _pyaudio_instance
    _model_ready.wait()

    _pyaudio_instance = pyaudio.PyAudio()

    print(_format_banner())
    print(f'👂 Listening — hold "{HOTKEY.upper()}" to start recording.')
    _start_hotkey_listener_mac()


def main():
    _init_status_bar()

    threading.Thread(target=_transcription_worker, daemon=True).start()
    threading.Thread(target=_setup_worker, daemon=True).start()

    atexit.register(lambda: _pyaudio_instance.terminate() if _pyaudio_instance else None)

    from PyObjCTools import AppHelper
    AppHelper.installMachInterrupt()
    AppHelper.runEventLoop()

    print("\n👋 Exiting...")


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
            f"{type(e).__name__}: {e}\n\nFull traceback: whisper_ptt.error.log",
        )
        raise SystemExit(1)
