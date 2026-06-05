@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 포트폴리오 서버

where python >nul 2>nul
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [최초 1회] 가상환경 생성 중...
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"

python -c "import streamlit, pandas, openpyxl, yfinance, requests, plotly, pdfplumber, streamlit_autorefresh" >nul 2>nul
if errorlevel 1 (
    echo [최초 1회] 패키지 설치 중... 수 분 소요
    pip install --upgrade pip
    pip install -r requirements.txt
)

echo.
echo ============================================
echo   포트폴리오 서버 시작
echo   - 로컬:   http://localhost:8765
echo   - 비밀번호: 1996
echo   - 외부 URL: tunnel.log 또는 잠시 뒤 표시됨
echo ============================================
echo.

REM 0) portfolio.xlsx에 누락된 시트 자동 보강 (meta 등)
python _seed_xlsx.py

REM 1) Streamlit을 백그라운드로 실행
start "Streamlit Server" /MIN cmd /c ".venv\Scripts\python.exe -m streamlit run app.py --server.port 8765 --server.headless true --browser.gatherUsageStats false"

REM 2) Streamlit이 뜨길 잠시 기다림
timeout /t 5 /nobreak >nul

REM 2-1) 브라우저 자동 오픈
start "" http://localhost:8765

REM 3) Cloudflare Tunnel 실행 (외부 접속용)
echo [터널] Cloudflare Tunnel 시작 중...
echo [터널] 아래에 https://...trycloudflare.com URL이 표시됩니다.
echo.
"tools\cloudflared.exe" tunnel --url http://localhost:8765 --logfile tunnel.log

echo.
echo 터널이 종료되었습니다. 아무 키나 누르면 창이 닫힙니다.
pause >nul
