@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0scripts\start_workbench.py" %*
  goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0scripts\start_workbench.py" %*
  goto finish
)
echo Python 3.10+ was not found. Install Python from python.org and reopen this launcher.
echo See docs\FIRST_RUN.md for setup instructions.
:finish
pause
