@echo off
rem ============================================================
rem  3D Contests - daily update
rem    run_update.bat       : manual run (pauses at the end)
rem    run_update.bat auto  : unattended run, output -> update.log
rem  Pipeline: git pull -> fetch 10 platforms -> merge -> HTML -> push
rem  NOTE: keep this file ASCII-only. Non-ASCII text after "chcp 65001"
rem  makes cmd.exe mis-seek lines and execute garbage (seen: 'yongfa'
rem  being run as a command). Python used for output encoding instead.
rem ============================================================
cd /d "%~dp0"

rem ---- Python interpreter ----
rem Always prefer .venv. Playwright installed into WorkBuddy's bundled temp
rem python disappears between sessions, which silently loses the
rem browser-rendered platforms (makerworld / jlc / makeronline).
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if /i "%~1"=="auto" goto :auto

echo.
echo [1/4] git pull ...
git pull --rebase
if errorlevel 1 (
  echo [!] pull --rebase failed, retry plain pull ...
  git pull
)

echo.
echo [2/4] fetch platforms and merge contests.json ...
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
"%PY%" scripts\fetch_contests.py

echo.
echo [3/4] commit and push ...
git add contests.json 3d-contests.html
git commit -m "Daily update"
git push
if errorlevel 1 (
  echo [!] push failed, pull then retry ...
  git pull --rebase
  git push
)

echo.
echo [4/4] done. GitHub Pages refreshes in 1-2 minutes.
pause
exit /b 0

:auto
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
(
  echo ===== %date% %time% =====
  git pull --rebase
  "%PY%" scripts\fetch_contests.py
  git add contests.json 3d-contests.html
  git commit -m "Daily update"
  git push
  if errorlevel 1 (
    git pull --rebase
    git push
  )
  echo ===== end %date% %time% =====
) >> update.log 2>&1
exit /b 0
