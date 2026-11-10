@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo   Heavy-Load Checker
echo   -------------------
echo   Starting local interface at http://127.0.0.1:8787/
echo   Press Ctrl+C to stop the server.
echo.
python server.py --open
pause
