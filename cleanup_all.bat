@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ============================================================
echo   [담당자용] 모든 이용자 작업 공간 삭제(퇴실 정리 일괄)
echo   workspace 폴더의 업로드 CSV, 분석 결과, AI 전송 기록이 삭제됩니다.
echo   반출 승인 결과물 보존 폴더(approved_outputs)는 유지됩니다.
echo ============================================================
set /p ok=계속하려면 Y 입력:
if /i not "%ok%"=="Y" exit /b 0
if exist workspace rmdir /s /q workspace
mkdir workspace
echo 정리 완료.
pause
