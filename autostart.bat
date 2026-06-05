@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" exit /b 1

".venv\Scripts\python.exe" _seed_xlsx.py 1>nul 2>nul

break > "%~dp0tunnel.log"

start "cloudflared" /B cmd /c "tools\cloudflared.exe tunnel --url http://localhost:8765 --no-autoupdate 2> "%~dp0tunnel.log""

start "streamlit" /B ".venv\Scripts\pythonw.exe" -m streamlit run app.py --server.port 8765 --server.headless true --browser.gatherUsageStats false

exit /b 0
