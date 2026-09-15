@echo off
setlocal
cd /d "%~dp0"
if defined RADAR_PYTHON (
  "%RADAR_PYTHON%" "%~dp0scripts\run_real_example.py" %*
  goto finish
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0scripts\run_real_example.py" %*
  goto finish
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0scripts\run_real_example.py" %*
  goto finish
)
echo Python 3.10+ was not found. Use the same Python that starts the workbench.
echo See docs\FIRST_REAL_RESULT.md for the exact command.
:finish
pause
