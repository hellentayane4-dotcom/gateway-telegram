@echo off
cd /d "%~dp0"
title Smoke Pay
py main.py
if errorlevel 1 (
  echo.
  echo O Smoke Pay encerrou com erro. Leia a mensagem acima.
  pause
)
