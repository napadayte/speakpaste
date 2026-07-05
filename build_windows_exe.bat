@echo off
setlocal
:: Build a standalone SpeakPaste.exe (Windows / NVIDIA CUDA) with PyInstaller.
:: Run this on a Windows machine from the repo root. Output: dist\SpeakPaste\SpeakPaste.exe

echo ============================================================
echo   SpeakPaste - Windows .exe build
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

cd /d "%~dp0"

if not exist build_venv (
    echo [..] Creating build venv...
    python -m venv build_venv
)
call build_venv\Scripts\activate.bat

echo [..] Installing dependencies...
python -m pip install --upgrade pip >nul
pip install faster-whisper==1.2.1 ctranslate2==4.7.1 ^
    nvidia-cublas-cu12==12.4.5.8 nvidia-cudnn-cu12==9.1.0.70 ^
    nvidia-cuda-nvrtc-cu12 nvidia-cuda-runtime-cu12 ^
    PyAudio==0.2.14 pyperclip pynput pystray Pillow python-dotenv requests numpy ^
    pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

echo [..] Building SpeakPaste.exe (this takes a few minutes)...
pyinstaller --noconfirm speakpaste_windows.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed
    pause
    exit /b 1
)

echo [..] Adding default .env...
copy /Y installer\windows_default.env dist\SpeakPaste\.env >nul

echo [..] Creating portable zip...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\SpeakPaste' -DestinationPath 'dist\SpeakPaste-Windows.zip'"

echo.
echo ============================================================
echo   [OK] Done!
echo   Exe:  dist\SpeakPaste\SpeakPaste.exe
echo   Zip:  dist\SpeakPaste-Windows.zip
echo.
echo   First launch downloads the Whisper model (~3 GB).
echo   Logs: speakpaste.log next to the exe.
echo ============================================================
pause
