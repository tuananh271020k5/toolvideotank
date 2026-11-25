@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo =======================================================
echo    PHAT HANH BAN CAP NHAT TU DONG - TANKVIDEO STUDIO
echo =======================================================
echo.
"%~dp0.venv\Scripts\python.exe" tools\publish_update.py
echo.
pause
