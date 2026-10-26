@echo off
chcp 65001 >nul
rem ============================================================
rem  3D 比赛追踪 - 一键更新（抓取10平台 → 增量合并 → 生成HTML → 推送GitHub）
rem  用法:
rem    双击运行（手动，结束后停留等待查看）
rem    run_update.bat auto  （自动模式：供计划任务调用，输出到 update.log）
rem ============================================================
cd /d "%~dp0"

rem ---- Python 解释器选择 ----
rem 必须优先使用本目录的 .venv：WorkBuddy 自带的临时 Python 里装的 playwright
rem 会随会话消失，用它抓 makerworld/jlc/makeronline 会静默失败（表现为"抓不全"）。
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [!] 未找到 .venv，回退到系统 python
  set PY=python
)

if /i "%~1"=="auto" goto :auto

echo.
echo [1/4] 拉取远端最新数据 (git pull) ...
git pull --rebase
if errorlevel 1 (
  echo [!] pull 失败，尝试普通 pull ...
  git pull
)

echo.
echo [2/4] 抓取各平台并增量合并 contests.json ...
"%PY%" scripts\fetch_contests.py

echo.
echo [3/4] 提交并推送到 GitHub ...
git add contests.json 3d-contests.html
git commit -m "Daily update"
git push
if errorlevel 1 (
  echo [!] push 失败，先 pull 再重试 push ...
  git pull --rebase
  git push
)

echo.
echo [4/4] 完成！已推送到 GitHub（页面地址见仓库 Pages 设置）。
pause
exit /b 0

:auto
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
  echo ===== done %time% =====
) >> update.log 2>&1
exit /b 0
