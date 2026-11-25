@echo off
setlocal
cd /d "%~dp0"

echo [TankVideo] Starting local client...
set "PATH=%~dp0bin\ffmpeg;%PATH%"
"%~dp0.venv\Scripts\python.exe" "%~dp0ui\gui.py"

if errorlevel 1 (
    echo.
    echo [TankVideo] Local client exited with an error.
    pause
)
