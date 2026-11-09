@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 오름(OREUM) AI 연결 점검 - .env 의 키·모델로 실제 요청 1번(비용 1원 미만)
.venv\Scripts\python.exe tools\check_ai.py
pause
