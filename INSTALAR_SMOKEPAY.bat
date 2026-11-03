@echo off
cd /d "%~dp0"
echo ==========================================
echo      SMOKE PAY - INSTALACAO
echo ==========================================
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERRO durante a instalacao.
  pause
  exit /b 1
)
echo.
echo Instalacao concluida.
pause
