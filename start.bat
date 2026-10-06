@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    echo and check "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"

python -c "import rapidocr, customtkinter" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies (one-time, a few minutes)...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
)

python app.py
pause
