@echo off
rem ai-research-desk setup: one environment per part, settings in %USERPROFILE%\.hedge-desk\.env.
rem Needs Python 3.12 from python.org (the py launcher finds it) and git.
setlocal
cd /d "%~dp0"
py -3.12 --version >nul 2>&1 || (echo Python 3.12 is required: https://www.python.org/downloads/windows/ & pause & exit /b 1)
py -3.12 install.py %*
echo.
echo Every install starts empty: no runs and no demo data. Start the dashboard with launch.bat.
pause
