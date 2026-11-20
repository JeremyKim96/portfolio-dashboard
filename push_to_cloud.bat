@echo off
chcp 65001 >nul
title Cloud Upload

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run run.bat once first.
    pause
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" "%~dp0push_to_cloud.py"

echo.
echo ----------------------------------------
echo Done. You can close this window.
pause >nul
