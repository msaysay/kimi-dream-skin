@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo 还没有安装，请先双击「安装.cmd」。
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m workbench %*
pause
