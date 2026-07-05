/*
 * SpeakPaste — C wrapper that embeds Python in-process.
 *
 * By compiling this as the CFBundleExecutable, macOS attributes all TCC
 * permissions (Accessibility, Automation, Microphone) to
 * com.speakpaste.app instead of to a system python binary.
 */

#include <Python.h>
#include <string.h>
#include <stdlib.h>

int main(int argc, char *argv[])
{
    /* Redirect stdout (append) */
    freopen("/Users/napadayte/whisper_ptt/whisper_ptt.log", "a", stdout);

    /* Redirect stderr (append) */
    freopen("/Users/napadayte/whisper_ptt/whisper_ptt.error.log", "a", stderr);

    /* Set environment before Py_Initialize */
    setenv("PYTHONPATH",
           "/Users/napadayte/whisper_ptt/venv/lib/python3.11/site-packages",
           1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);

    /* Working directory */
    chdir("/Users/napadayte/whisper_ptt");

    /* Build argv for Python: ["SpeakPaste", "-u", "<script>"] */
    char *py_argv[] = {
        "SpeakPaste",
        "-u",
        "/Users/napadayte/whisper_ptt/whisper_ptt_apple_silicon.py",
        NULL
    };

    return Py_BytesMain(3, py_argv);
}
