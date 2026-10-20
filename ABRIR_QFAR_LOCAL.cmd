@echo off
setlocal
if not exist "%~dp0dist-local\QMoney.exe" (
  echo A interface QMoney.exe ainda nao foi compilada. Execute scripts\build_local.ps1 uma vez.
  pause
  exit /b 1
)
echo Edicao local: o motor da campanha (moneymin) roda direto do codigo-fonte.
echo Alteracoes em arquivos .py valem na proxima abertura, sem recompilar.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_local.ps1"
if errorlevel 1 (
  echo Nao foi possivel atualizar ou abrir o QMoney. Confira o erro acima.
  pause
  exit /b 1
)
exit /b 0