@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 클라우드로 데이터 올리기

echo ============================================
echo   포트폴리오 데이터를 클라우드로 올립니다
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [오류] 가상환경이 없습니다. 먼저 run.bat을 한 번 실행하세요.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" push_to_cloud.py

echo.
echo 완료. 창을 닫아도 됩니다.
pause >nul
