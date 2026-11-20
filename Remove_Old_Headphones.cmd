@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Start_BuyWise.cmd in this folder first.
) else (
  ".venv\Scripts\python.exe" remove_old_headphones.py
)
pause
