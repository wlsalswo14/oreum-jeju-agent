@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"
title OREUM install

echo ============================================================
echo   오름(OREUM) 설치 - 제주 지역경제 분석 AI Agent
echo   (인터넷 연결 필요, 약 3~5분)
echo ============================================================
echo.

rem --- 1. uv(파이썬 설치·관리 도구) 준비 -------------------------------
where uv > nul 2> nul
if %errorlevel% neq 0 (
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  ) else (
    echo [1/5] uv 설치 중... ^(https://astral.sh^)
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  )
) else (
  echo [1/5] uv 확인 완료
)

where uv > nul 2> nul
if %errorlevel% neq 0 goto :fallback

rem --- 2. Python 3.12 가상환경 -------------------------------------------
echo [2/5] Python 3.12 가상환경 만드는 중...
if exist .venv\Scripts\python.exe (
  echo      기존 가상환경을 그대로 사용합니다
) else (
  uv venv --python 3.12 .venv
)
if not exist .venv\Scripts\python.exe goto :fallback

rem --- 3. 패키지 설치 ------------------------------------------------------
echo [3/5] 필요한 패키지 설치 중...
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if %errorlevel% neq 0 goto :fail
goto :config

:fallback
echo.
echo [안내] uv를 사용할 수 없어 PC에 설치된 Python으로 진행합니다.
where py > nul 2> nul
if %errorlevel% neq 0 (
  echo [오류] Python이 없습니다. https://www.python.org 에서 Python 3.12를 설치한 뒤 다시 실행하세요.
  goto :fail
)
if not exist .venv\Scripts\python.exe (py -3.12 -m venv .venv 2> nul || py -3 -m venv .venv)
if %errorlevel% neq 0 goto :fail
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 goto :fail

:config
rem --- 4. 설정 파일 -----------------------------------------------------------
echo [4/5] 설정 파일 준비...
if not exist .env (
  copy /y .env.example .env > nul
  echo      .env 파일을 만들었습니다. AI를 쓰려면 .env 의 OPENAI_API_KEY 에 키를 넣고 check_ai.bat 으로 점검하세요.
) else (
  echo      기존 .env 유지
)

rem --- 5. 자가 점검 ----------------------------------------------------------
echo [5/5] 자가 점검...
.venv\Scripts\python.exe tools\selfcheck.py
if %errorlevel% neq 0 goto :fail

echo.
echo ============================================================
echo   설치 완료!  run.bat 을 더블클릭하면 오름이 실행됩니다.
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [실패] 설치 중 오류가 발생했습니다. 위 메시지를 확인하거나 매뉴얼의 '문제 해결'을 참고하세요.
pause
exit /b 1
