@echo off
rem The Desk dashboard on http://localhost:8790. Close this window to stop it.
setlocal
set PYTHONUTF8=1
cd /d "%~dp0.."
"desk\.venv\Scripts\python.exe" "dashboard\server.py" %*
