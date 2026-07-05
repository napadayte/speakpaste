@echo off
setlocal EnableDelayedExpansion

:: ==========================================================================
:: SpeakPaste Post-Install Script
:: Called by Inno Setup after files are copied.
:: Usage: post_install.bat "<install_dir>"
:: ==========================================================================

set "INSTALL_DIR=%~1"
if "%INSTALL_DIR%"=="" set "INSTALL_DIR=%~dp0"

:: Remove trailing backslash if present
if "%INSTALL_DIR:~-1%"=="\" set "INSTALL_DIR=%INSTALL_DIR:~0,-1%"

set "LOG=%INSTALL_DIR%\post_install.log"

echo ============================================================ > "%LOG%"
echo   SpeakPaste Post-Install  %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo. >> "%LOG%"

:: ------------------------------------------------------------------
:: Detect Python command
:: ------------------------------------------------------------------

set "PYTHON_CMD="

:: Check if python_path.txt was written by the Inno Setup script
if exist "%INSTALL_DIR%\python_path.txt" (
    set /p PYTHON_CMD=<"%INSTALL_DIR%\python_path.txt"
    del "%INSTALL_DIR%\python_path.txt" >nul 2>&1
)

:: Validate the detected command, fall back to common names
if defined PYTHON_CMD (
    %PYTHON_CMD% --version >nul 2>&1
    if errorlevel 1 set "PYTHON_CMD="
)

if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
    ) else (
        python3 --version >nul 2>&1
        if not errorlevel 1 (
            set "PYTHON_CMD=python3"
        ) else (
            py -3 --version >nul 2>&1
            if not errorlevel 1 (
                set "PYTHON_CMD=py -3"
            )
        )
    )
)

if not defined PYTHON_CMD (
    echo [ERROR] Python not found. >> "%LOG%"
    echo [ERROR] Python not found. Install Python 3.10+ and rerun the installer.
    exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do set "PYVER=%%v"
echo [OK] %PYVER% (%PYTHON_CMD%) >> "%LOG%"

:: ------------------------------------------------------------------
:: Create virtual environment
:: ------------------------------------------------------------------

if not exist "%INSTALL_DIR%\venv" (
    echo [..] Creating virtual environment... >> "%LOG%"
    %PYTHON_CMD% -m venv "%INSTALL_DIR%\venv" >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo [ERROR] Failed to create venv. >> "%LOG%"
        exit /b 1
    )
)
echo [OK] Virtual environment ready >> "%LOG%"

:: ------------------------------------------------------------------
:: Set up pip/python paths
:: ------------------------------------------------------------------

set "VENV_PIP=%INSTALL_DIR%\venv\Scripts\pip.exe"
set "VENV_PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe"

:: ------------------------------------------------------------------
:: Upgrade pip
:: ------------------------------------------------------------------

echo [..] Upgrading pip... >> "%LOG%"
"%VENV_PYTHON%" -m pip install --upgrade pip >> "%LOG%" 2>&1

:: ------------------------------------------------------------------
:: Install dependencies
:: ------------------------------------------------------------------

echo [..] Installing faster-whisper (CUDA)... >> "%LOG%"
"%VENV_PIP%" install faster-whisper >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [WARN] faster-whisper install had issues, trying with CUDA libs... >> "%LOG%"
    "%VENV_PIP%" install nvidia-cublas-cu12 nvidia-cudnn-cu12 >> "%LOG%" 2>&1
    "%VENV_PIP%" install faster-whisper >> "%LOG%" 2>&1
)

echo [..] Installing audio, clipboard, tray, hotkeys... >> "%LOG%"
"%VENV_PIP%" install pyaudio pyperclip pynput pystray Pillow python-dotenv requests numpy >> "%LOG%" 2>&1
if errorlevel 1 (
    echo [WARN] PyAudio may need a pre-built wheel. Trying pipwin... >> "%LOG%"
    "%VENV_PIP%" install pipwin >> "%LOG%" 2>&1
    "%VENV_PYTHON%" -m pipwin install pyaudio >> "%LOG%" 2>&1
)

echo [OK] Dependencies installed >> "%LOG%"

:: ------------------------------------------------------------------
:: Create .env with defaults (only if it does not already exist)
:: ------------------------------------------------------------------

if not exist "%INSTALL_DIR%\.env" (
    echo [..] Creating default .env configuration... >> "%LOG%"
    (
        echo # SpeakPaste Configuration
        echo # Edit these values to customize behavior.
        echo #
        echo # Whisper model: tiny, base, small, medium, large-v3
        echo WHISPER_PTT_WHISPER_MODEL=large-v3
        echo #
        echo # Language: leave empty for auto-detect, or set to en, ru, nl, etc.
        echo WHISPER_PTT_WHISPER_LANGUAGE=
        echo #
        echo # Initial prompt for Whisper ^(helps with domain-specific words^)
        echo WHISPER_PTT_WHISPER_INITIAL_PROMPT=
        echo #
        echo # Device and compute type
        echo WHISPER_PTT_WHISPER_DEVICE=cuda
        echo WHISPER_PTT_WHISPER_COMPUTE_TYPE=float16
        echo #
        echo # Hotkey: hold to record, release to transcribe
        echo # Examples: alt_r, ctrl+shift, f9
        echo WHISPER_PTT_HOTKEY=alt_r
        echo #
        echo # LLM post-processing ^(requires Ollama running locally^)
        echo WHISPER_PTT_USE_LLM_TRANSFORM=false
        echo #
        echo # Output behavior
        echo WHISPER_PTT_COPY_TO_CLIPBOARD=true
        echo WHISPER_PTT_PASTE_TO_ACTIVE_WINDOW=true
        echo WHISPER_PTT_KEYS_AFTER_PASTE=none
        echo #
        echo # Sound feedback
        echo WHISPER_PTT_USE_SOUND=true
    ) > "%INSTALL_DIR%\.env"
    echo [OK] Created .env >> "%LOG%"
) else (
    echo [OK] Existing .env preserved >> "%LOG%"
)

:: ------------------------------------------------------------------
:: Create batch launcher (backup for VBS)
:: ------------------------------------------------------------------

(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo "%INSTALL_DIR%\venv\Scripts\pythonw.exe" "%INSTALL_DIR%\speakpaste_windows.py"
) > "%INSTALL_DIR%\SpeakPaste.bat"
echo [OK] Created SpeakPaste.bat >> "%LOG%"

:: ------------------------------------------------------------------
:: Done
:: ------------------------------------------------------------------

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo   Post-install complete  %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"

exit /b 0
