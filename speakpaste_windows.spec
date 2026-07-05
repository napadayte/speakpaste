# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for SpeakPaste (Windows / NVIDIA CUDA).
# Build on Windows: run build_windows_exe.bat, or:
#   pip install <deps> pyinstaller && pyinstaller --noconfirm speakpaste_windows.spec
# Output: dist\SpeakPaste\SpeakPaste.exe (onedir — CUDA DLLs are too big for onefile)

import glob
import os
import sysconfig

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []

# faster-whisper ships non-Python assets (Silero VAD model); ctranslate2 ships its DLL
for pkg in ("faster_whisper", "ctranslate2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# CUDA runtime DLLs from the nvidia-* pip wheels (cuBLAS, cuDNN, nvrtc, cudart).
# Placed flat into the app dir so ctranslate2.dll resolves them at load time.
site_packages = sysconfig.get_paths()["purelib"]
for dll in glob.glob(os.path.join(site_packages, "nvidia", "*", "bin", "*.dll")):
    binaries.append((dll, "."))

hiddenimports += [
    "pynput.keyboard._win32",
    "pynput.mouse._win32",
    "pystray._win32",
]

a = Analysis(
    ["speakpaste_windows.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SpeakPaste",
    icon="SpeakPaste.ico",
    console=False,
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SpeakPaste",
)
