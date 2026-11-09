@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 오름(OREUM) 자동 테스트 실행 (약 1~2분)
if not exist "data\demo_center\SYNTHETIC_1-08_*.csv" (
  echo 센터 CSV 경로 시험용 형식 검증 데이터를 만드는 중입니다^(최초 1회, 약 20초 - 화면·보고서에는 쓰지 않음^)...
  .venv\Scripts\python.exe tools\make_demo_data.py
)
.venv\Scripts\python.exe -m pytest tests -q -p no:warnings
pause
