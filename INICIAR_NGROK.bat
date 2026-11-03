@echo off
cd /d "%~dp0"
echo Iniciando tunel para a porta 8000...
ngrok http 8000
pause
