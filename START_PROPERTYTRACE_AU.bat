@echo off
title PropertyTrace AU — LexCrypta
cd /d "%~dp0"

:: Check if Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
  echo Python not found. Opening directly in Chrome...
  start "" "propertytrace-au-v1.html"
  goto :end
)

:: Kill any existing server on port 8181
for /f "tokens=5" %%a in ('netstat -aon ^| find ":8181"') do (
  taskkill /f /pid %%a >nul 2>&1
)

:: Start Python HTTP server in background
echo Starting PropertyTrace server on localhost:8181...
start /b python -m http.server 8181

:: Wait for server to be ready
timeout /t 2 /nobreak >nul

:: Open in default browser
echo Opening PropertyTrace AU...
start "" "http://localhost:8181/propertytrace-au-v1.html"

echo.
echo PropertyTrace AU is running.
echo Close this window to stop the server.
echo.
pause

:end
