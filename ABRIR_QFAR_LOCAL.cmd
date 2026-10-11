@echo off
setlocal
if not exist "%~dp0dist-local\QMoney.exe" (
  echo O aplicativo local ainda nao foi compilado.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1"
