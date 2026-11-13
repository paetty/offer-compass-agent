@echo off
setlocal
cd /d "%~dp0"
python -c "import sys, sqlite3; sys.exit(sys.version_info < (3, 7))" >nul 2>nul
if errorlevel 1 (
    py -3 "%~dp0offer_compass_server.py" --open
) else (
    python "%~dp0offer_compass_server.py" --open
)
if errorlevel 1 pause
