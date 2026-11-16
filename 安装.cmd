@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo 没有找到 Python。请先到 https://www.python.org/downloads/release/python-3129/ 下载安装 Python 3.12，
  echo 安装时勾选 Add python.exe to PATH，然后重新双击本文件。
  pause
  exit /b 1
)
%PY% scripts\install.py %*
echo.
pause
