@echo off
rem Run the desk from the command line: scripts\run-desk.bat MSFT --own --engines quant,vets,edge
setlocal
set PYTHONUTF8=1
cd /d "%~dp0..\desk"
".venv\Scripts\python.exe" -m desk %*
