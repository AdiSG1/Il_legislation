@echo off
rem Opens the usage and cost dashboard in your browser (http://127.0.0.1:8765).
rem Keep this window open while you use the dashboard; close it to stop.

cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\activate.bat" (
  call "%~dp0.venv\Scripts\activate.bat"
) else (
  call "%~dp0..\.venv\Scripts\activate.bat"
)
set PYTHONUTF8=1
python -m dashboard.server
pause
