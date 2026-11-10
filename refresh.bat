@echo off
rem Weekly refresh of the law database: picks up new amendments, newly verified texts,
rem and removes laws that are no longer in force. Writes a log to the logs folder.
rem Runs on this computer because the Knesset site may block servers outside Israel.

cd /d "%~dp0"
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set STAMP=%%i

if exist "%~dp0.venv\Scripts\activate.bat" (
  call "%~dp0.venv\Scripts\activate.bat"
) else (
  call "%~dp0..\.venv\Scripts\activate.bat"
)

set PYTHONUTF8=1
python -m ingest.run > "logs\refresh-%STAMP%.log" 2>&1
