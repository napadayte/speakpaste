@echo off
setlocal EnableDelayedExpansion

echo ============================================================
echo   SpeakPaste Installer (Windows / NVIDIA CUDA)
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER%

:: Check NVIDIA GPU
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo [WARN] nvidia-smi not found. CUDA may not work.
    echo        Install NVIDIA drivers from https://nvidia.com/drivers
    echo.
)

:: Create project directory
set "INSTALL_DIR=%USERPROFILE%\SpeakPaste"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
echo [OK] Install dir: %INSTALL_DIR%

:: Copy files
copy /Y "%~dp0speakpaste_windows.py" "%INSTALL_DIR%\speakpaste_windows.py" >nul
echo [OK] Copied main script

:: Create venv
if not exist "%INSTALL_DIR%\venv" (
    echo [..] Creating virtual environment...
    python -m venv "%INSTALL_DIR%\venv"
)
echo [OK] Virtual environment ready

:: Activate and install deps
set "PIP=%INSTALL_DIR%\venv\Scripts\pip.exe"
set "PYTHON=%INSTALL_DIR%\venv\Scripts\python.exe"

echo [..] Installing dependencies (this may take a few minutes)...
echo.

:: Core deps
"%PIP%" install --upgrade pip >nul 2>&1

:: CUDA-enabled faster-whisper
echo      Installing faster-whisper (CUDA)...
"%PIP%" install faster-whisper 2>&1 | findstr /i "error" && (
    echo [WARN] faster-whisper install had errors, trying with CUDA toolkit...
    "%PIP%" install nvidia-cublas-cu12 nvidia-cudnn-cu12
    "%PIP%" install faster-whisper
)

:: Other deps
echo      Installing audio, clipboard, tray, hotkeys...
"%PIP%" install pyaudio pyperclip pynput pystray Pillow python-dotenv requests 2>&1 | findstr /i "error" && (
    echo [WARN] PyAudio may need pre-built wheel on Windows.
    echo        Try: pip install pipwin ^&^& pipwin install pyaudio
)

echo.
echo [OK] Dependencies installed

:: Create .env if not exists
if not exist "%INSTALL_DIR%\.env" (
    (
        echo WHISPER_PTT_WHISPER_MODEL=large-v3
        echo WHISPER_PTT_WHISPER_LANGUAGE=
        echo WHISPER_PTT_WHISPER_INITIAL_PROMPT=
        echo WHISPER_PTT_WHISPER_DEVICE=cuda
        echo WHISPER_PTT_WHISPER_COMPUTE_TYPE=float16
        echo WHISPER_PTT_HOTKEY=alt_r
        echo WHISPER_PTT_USE_LLM_TRANSFORM=false
        echo WHISPER_PTT_COPY_TO_CLIPBOARD=true
        echo WHISPER_PTT_PASTE_TO_ACTIVE_WINDOW=true
        echo WHISPER_PTT_KEYS_AFTER_PASTE=none
        echo WHISPER_PTT_USE_SOUND=true
    ) > "%INSTALL_DIR%\.env"
    echo [OK] Created default .env config
)

:: Create launcher
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo "%INSTALL_DIR%\venv\Scripts\pythonw.exe" "%INSTALL_DIR%\speakpaste_windows.py"
) > "%INSTALL_DIR%\SpeakPaste.bat"
echo [OK] Created SpeakPaste.bat launcher

:: Create hidden VBS launcher (no console window)
(
    echo Set oShell = CreateObject("WScript.Shell"^)
    echo oShell.Run """%INSTALL_DIR%\venv\Scripts\pythonw.exe"" ""%INSTALL_DIR%\speakpaste_windows.py""", 0, False
) > "%INSTALL_DIR%\SpeakPaste.vbs"
echo [OK] Created SpeakPaste.vbs (silent launcher)

:: Create Start Menu shortcut
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\SpeakPaste.lnk"
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%SHORTCUT%'); $s.TargetPath = '%INSTALL_DIR%\SpeakPaste.vbs'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.Description = 'SpeakPaste - Voice to Text'; $s.Save()" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Start Menu shortcut created
)

:: Create Desktop shortcut
set "DSHORTCUT=%USERPROFILE%\Desktop\SpeakPaste.lnk"
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('%DSHORTCUT%'); $s.TargetPath = '%INSTALL_DIR%\SpeakPaste.vbs'; $s.WorkingDirectory = '%INSTALL_DIR%'; $s.Description = 'SpeakPaste - Voice to Text'; $s.Save()" >nul 2>&1
if not errorlevel 1 (
    echo [OK] Desktop shortcut created
)

echo.
echo ============================================================
echo   Installation complete!
echo ============================================================
echo.
echo   Location:  %INSTALL_DIR%
echo   Config:    %INSTALL_DIR%\.env
echo   Launch:    Double-click SpeakPaste on Desktop or Start Menu
echo   Hotkey:    Hold Right Alt to record, release to transcribe
echo.
echo   First launch downloads the Whisper model (~3 GB).
echo   Make sure you have NVIDIA drivers + CUDA installed.
echo.
echo   To start now, press any key...
pause >nul
start "" "%INSTALL_DIR%\SpeakPaste.vbs"
