@echo off
rem ============================================================
rem  3D Contests - daily update
rem    run_update.bat       : manual run (pauses at the end)
rem    run_update.bat auto  : unattended run, output -> update.log
rem  Pipeline: git pull -> fetch 10 platforms -> merge -> HTML -> push
rem  NOTE: keep this file ASCII-only. Non-ASCII text here makes cmd.exe
rem  mis-seek lines and execute garbage. Python handles msg encoding.
rem  NOTE: git pull/push go through scripts\git_push.py, which falls back
rem  to a working local proxy when the injected proxy env var is dead.
rem ============================================================
cd /d "%~dp0"

rem ---- Python interpreter ----
rem Always prefer .venv. Playwright installed into WorkBuddy's bundled temp
rem python disappears between sessions, which silently loses the
rem browser-rendered platforms (makerworld / jlc / makeronline).
set "PY=%~dp0.venv\Scripts\python.exe"
rem A missing .venv used to silently fall back to system python, which has no
rem Playwright and quietly loses the browser-rendered platforms. Fail loudly.
if exist "%PY%" goto :pyok
echo [FATAL] missing interpreter: %PY%
echo [FATAL] rebuild it: run setup_env.bat, then retry
if /i "%~1"=="auto" (
  >> update.log echo [FATAL] missing interpreter: %PY%
  >> update.log echo [FATAL] rebuild it: run setup_env.bat, then retry
)
exit /b 9

:pyok
if /i "%~1"=="auto" goto :auto

echo.
echo [1/4] git pull ...
"%PY%" scripts\git_push.py pull

echo.
echo [2/4] fetch platforms and merge contests.json ...
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%PY%" scripts\fetch_contests.py

echo.
echo [3/4] commit and push ...
git add contests.json 3d-contests.html
git commit -m "Daily update"
"%PY%" scripts\git_push.py push

echo.
echo [4/4] done. GitHub Pages refreshes in 1-2 minutes.
pause
exit /b 0

:auto
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
(
  rem %date% is locale-dependent (Chinese weekday) and turns into mojibake in
  rem the UTF-8 log, so let python stamp the log instead.
  "%PY%" -c "import datetime;print('===== '+datetime.datetime.now().isoformat(' ','seconds')+' =====')"
  "%PY%" scripts\git_push.py pull
  "%PY%" scripts\fetch_contests.py
  git add contests.json 3d-contests.html
  git commit -m "Daily update"
  "%PY%" scripts\git_push.py push
  "%PY%" -c "import datetime;print('===== end '+datetime.datetime.now().isoformat(' ','seconds')+' =====')"
) >> update.log 2>&1
exit /b 0
