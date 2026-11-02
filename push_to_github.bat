@echo off
setlocal
cd /d "%~dp0"

echo.
echo   ETF-Timing-Studio  -^>  GitHub
echo   ============================================
echo.

where powershell >nul 2>nul
if errorlevel 1 (
  echo   Windows PowerShell wurde nicht gefunden.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0push_to_github.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
  echo   Der Upload wurde nicht abgeschlossen.
) else (
  echo   Upload abgeschlossen.
)
echo.
pause
exit /b %RC%
