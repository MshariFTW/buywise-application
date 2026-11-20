@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3.12 -m venv .venv
  if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed
".venv\Scripts\python.exe" buywise_plus.py %1
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Check the error above. BuyWise needs Python 3.12 and a running PostgreSQL server.
pause
exit /b 1
