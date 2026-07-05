# SpeakPaste Native App Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SpeakPaste.app a proper macOS application where the user grants permissions once to one app and paste works reliably.

**Architecture:** Replace the bash-script launcher with a compiled C wrapper that embeds Python in-process. This is how py2app and professional Python macOS apps work — the Mach-O binary IS the CFBundleExecutable, so macOS attributes all TCC permissions (Accessibility, Automation, Microphone) to `com.speakpaste.app`. No `exec`, no subprocess python — one process, one identity, one set of permissions.

**Tech Stack:** C (Python embedding API), Python 3.11, pyobjc/Quartz CGEvent, ad-hoc codesigning

**Root cause of current bugs:**
- The bash script `exec`s into `/opt/homebrew/.../python3.11` — macOS TCC sees that binary's code identity, NOT the .app bundle
- `AXIsProcessTrusted()` returns False because python3.11 is not in Accessibility
- CGEventPost silently drops Cmd+V keystrokes — paste never fires
- Lock file (`.speakpaste.lock`) isn't cleaned on crash → "already running" on next launch

---

### Task 1: Create the C wrapper binary

**Files:**
- Create: `SpeakPaste.app/Contents/MacOS/SpeakPaste.c` (source, not shipped)
- Create: `SpeakPaste.app/Contents/MacOS/SpeakPaste` (compiled binary, replaces bash script)
- Remove: `SpeakPaste.app/Contents/MacOS/python3` (no longer needed)

- [ ] **Step 1: Write the C wrapper**

```c
// SpeakPaste.app/Contents/MacOS/SpeakPaste.c
// Minimal Python embedder — runs whisper_ptt_apple_silicon.py in-process.
// Compiled binary is the CFBundleExecutable, so macOS TCC attributes
// all permissions (Accessibility, Automation, Mic) to com.speakpaste.app.

#include <Python.h>
#include <string.h>
#include <stdlib.h>
#include <libgen.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    // Find our own path to derive the project directory
    char exe_path[1024];
    uint32_t size = sizeof(exe_path);
    _NSGetExecutablePath(exe_path, &size);

    // Resolve symlinks
    char real_path[1024];
    realpath(exe_path, real_path);

    // Derive project dir: <bundle>/Contents/MacOS/SpeakPaste → go up 3 levels
    // But we hardcode the known project path for simplicity
    const char *project_dir = "/Users/napadayte/whisper_ptt";
    const char *venv_site = "/Users/napadayte/whisper_ptt/venv/lib/python3.11/site-packages";
    const char *script = "/Users/napadayte/whisper_ptt/whisper_ptt_apple_silicon.py";
    const char *log_path = "/Users/napadayte/whisper_ptt/whisper_ptt.log";
    const char *err_path = "/Users/napadayte/whisper_ptt/whisper_ptt.error.log";

    // Redirect stdout/stderr to log files
    freopen(log_path, "a", stdout);
    freopen(err_path, "a", stderr);

    // Set environment
    setenv("PYTHONPATH", venv_site, 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    chdir(project_dir);

    // Build argv for Python: ["SpeakPaste", "-u", "script.py"]
    // Filter out macOS Launch Services -psn_* args
    char *py_argv[4];
    py_argv[0] = "SpeakPaste";
    py_argv[1] = "-u";
    py_argv[2] = (char *)script;
    py_argv[3] = NULL;

    // Initialize and run Python
    return Py_BytesMain(3, py_argv);
}
```

- [ ] **Step 2: Compile the wrapper**

Run:
```bash
cc -o /Users/napadayte/whisper_ptt/SpeakPaste.app/Contents/MacOS/SpeakPaste \
    /Users/napadayte/whisper_ptt/SpeakPaste.app/Contents/MacOS/SpeakPaste.c \
    -I/opt/homebrew/opt/python@3.11/Frameworks/Python.framework/Versions/3.11/include/python3.11 \
    -L/opt/homebrew/opt/python@3.11/Frameworks/Python.framework/Versions/3.11/lib \
    -lpython3.11 \
    -framework CoreFoundation \
    -O2
```

Expected: binary at `SpeakPaste.app/Contents/MacOS/SpeakPaste` (~50KB)

- [ ] **Step 3: Verify the binary runs**

Run:
```bash
PYTHONPATH="/Users/napadayte/whisper_ptt/venv/lib/python3.11/site-packages" \
/Users/napadayte/whisper_ptt/SpeakPaste.app/Contents/MacOS/SpeakPaste 2>&1 | head -5
```

Expected: SpeakPaste starts, loads model, shows banner.

- [ ] **Step 4: Remove the copied python3 and source file**

```bash
rm -f /Users/napadayte/whisper_ptt/SpeakPaste.app/Contents/MacOS/python3
rm -f /Users/napadayte/whisper_ptt/SpeakPaste.app/Contents/MacOS/SpeakPaste.c
```

- [ ] **Step 5: Commit**

```bash
git add SpeakPaste.app/Contents/MacOS/SpeakPaste SpeakPaste.app/Contents/Info.plist
git commit -m "feat: native C wrapper for SpeakPaste.app — proper TCC attribution"
```

---

### Task 2: Fix the lock file cleanup

**Files:**
- Modify: `whisper_ptt_apple_silicon.py:19-28` (lock file logic)

The current lock file code doesn't clean up on crash, causing "already running" on next launch.

- [ ] **Step 1: Add atexit cleanup for lock file**

Replace the lock section (lines 19-28) with:

```python
import atexit
import fcntl
import os

_lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".speakpaste.lock")
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
```

- [ ] **Step 2: Remove the duplicate atexit import at line 927**

The `main()` function imports atexit again — remove that import since it's already at the top.

- [ ] **Step 3: Verify lock cleanup works**

```bash
rm -f /Users/napadayte/whisper_ptt/.speakpaste.lock
# Start the app, then Ctrl+C — verify .speakpaste.lock is deleted
```

- [ ] **Step 4: Commit**

```bash
git add whisper_ptt_apple_silicon.py
git commit -m "fix: clean up lock file on exit to prevent stale lock"
```

---

### Task 3: Simplify paste to CGEvent-only with proper error logging

**Files:**
- Modify: `whisper_ptt_apple_silicon.py:415-516` (paste section)

Now that the C wrapper gives us proper TCC, CGEvent is the right method. The multi-method fallback is unnecessary complexity. Keep CGEvent as primary and log when it fails so the user sees actionable info.

- [ ] **Step 1: Replace the paste section**

Replace the entire paste section (from `_CG_MODIFIER_FLAGS` through `paste_to_front`) with:

```python
_CG_MODIFIER_FLAGS = {
    "command": 0x100000,
    "control": 0x040000,
    "option":  0x080000,
    "shift":   0x020000,
}


def _send_key_via_cgevent(keycode, modifier=None):
    """Send a keystroke via CGEvent (needs Accessibility on this .app bundle)."""
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
```

- [ ] **Step 2: Remove the NSAppleScript and osascript fallback functions**

Delete `_send_key_via_nsapplescript` and `_do_paste_keystroke` — no longer needed.

- [ ] **Step 3: Commit**

```bash
git add whisper_ptt_apple_silicon.py
git commit -m "fix: simplify paste to CGEvent-only, remove osascript fallbacks"
```

---

### Task 4: Sign the bundle, install, and reset permissions

**Files:**
- Modify: `SpeakPaste.app` (re-sign)
- No code changes

- [ ] **Step 1: Re-sign the .app bundle**

```bash
codesign --force --deep --sign - /Users/napadayte/whisper_ptt/SpeakPaste.app
```

Expected: `SpeakPaste.app: replacing existing signature`

- [ ] **Step 2: Verify the signature**

```bash
codesign -dvv /Users/napadayte/whisper_ptt/SpeakPaste.app 2>&1
```

Expected: `Executable=.../Contents/MacOS/SpeakPaste`, `Format=app bundle with Mach-O thin (arm64)`
Note: should say `Mach-O` now, not `generic` (bash script). This confirms macOS sees a real binary.

- [ ] **Step 3: Copy to /Applications and reset TCC**

```bash
rm -rf /Applications/SpeakPaste.app
cp -R /Users/napadayte/whisper_ptt/SpeakPaste.app /Applications/SpeakPaste.app
tccutil reset Accessibility com.speakpaste.app
tccutil reset AppleEvents com.speakpaste.app
rm -f /Users/napadayte/whisper_ptt/.speakpaste.lock
```

- [ ] **Step 4: Launch and grant permissions**

```bash
open /Applications/SpeakPaste.app
```

macOS will prompt for:
1. **Microphone** — allow
2. **Accessibility** — allow (may need to go to System Settings → Privacy & Security → Accessibility → toggle SpeakPaste ON)
3. **Automation / System Events** — allow when prompted

All three permissions go to ONE app: SpeakPaste.

- [ ] **Step 5: Verify paste works**

Hold Right Option → speak → release → text should appear in active window.

Check log:
```bash
tail -20 /Users/napadayte/whisper_ptt/whisper_ptt.log
```

Expected: `📋 Copied to clipboard!` then `✅ Pasted to active window!` and text actually appears.

---

### Task 5: Update the Accessibility check message

**Files:**
- Modify: `whisper_ptt_apple_silicon.py:708-733` (accessibility check)

The current message says "enable the terminal / Python app" — should say "enable SpeakPaste".

- [ ] **Step 1: Update the warning message**

Replace the accessibility warning (lines 719-725) with:

```python
            print()
            print("  ⚠️  Accessibility permission required!")
            print("  Hotkeys and paste won't work without it.")
            print()
            print("  Fix: System Settings → Privacy & Security → Accessibility")
            print("        → enable SpeakPaste")
            print()
```

- [ ] **Step 2: Commit**

```bash
git add whisper_ptt_apple_silicon.py
git commit -m "fix: update Accessibility message to reference SpeakPaste app"
```

---

## Verification Checklist

After all tasks are complete:

- [ ] `open /Applications/SpeakPaste.app` launches the app (no "already running", no crash)
- [ ] Menu bar icon appears (mic symbol)
- [ ] Hold Right Option → recording starts (sound plays)
- [ ] Release → transcription runs → text pastes into active window
- [ ] System Settings → Privacy & Security shows only "SpeakPaste" (no python3.11, no Terminal)
- [ ] Quit (menu bar → Quit) → lock file cleaned up → re-launch works
- [ ] Force-kill (kill -9) → re-launch works (lock released by kernel)
