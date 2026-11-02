@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo   Python 3 wurde nicht gefunden.
  echo.
  echo   Bitte Python von https://www.python.org/downloads/ installieren
  echo   und beim Setup "Add Python to PATH" anhaken.
  echo.
  pause
  exit /b 1
)

echo Starte ETF-Timing-Studio ...
%PY% ETF_Tool.py %*

if errorlevel 1 (
  echo.
  echo Das Programm wurde mit einem Fehler beendet.
  pause
)
