@echo off
chcp 65001 > nul
cd /d "%~dp0"
title OREUM
if not exist .venv\Scripts\python.exe (
  echo 먼저 install.bat 을 실행하세요.
  pause
  exit /b 1
)
echo 오름(OREUM)을 시작합니다. 잠시 후 브라우저가 열립니다.
echo 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
.venv\Scripts\python.exe -m app.main
pause
