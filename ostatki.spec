# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec: собирает приложение + модели OCR + иконки в один .exe.
# Запускать на Windows:  pyinstaller ostatki.spec
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("onnxruntime", "rapidocr", "cv2", "PIL", "customtkinter", "ttkthemes"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# модели OCR (папка models/)
models_dir = os.path.abspath(os.path.join(SPECPATH, "models"))
if os.path.isdir(models_dir):
    datas.append((models_dir, "models"))

# иконки интерфейса (папка icons/)
icons_dir = os.path.abspath(os.path.join(SPECPATH, "icons"))
if os.path.isdir(icons_dir):
    datas.append((icons_dir, "icons"))

# старая база (для автозагрузки рядом с exe)
tsv = os.path.abspath(os.path.join(SPECPATH, "ostatki_po_skladu.tsv"))
if os.path.isfile(tsv):
    datas.append((tsv, "."))

a = Analysis(
    ["app.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "PyQt5", "PySide6", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OstatkiSklada",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
