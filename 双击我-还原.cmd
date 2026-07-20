@echo off
rem ============================================================
rem  Kimi Dream Skin - 一键还原官方外观
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   正在还原 Kimi 官方外观...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\scripts\restore-dream-skin.ps1"

echo.
echo   已还原。下次启动 Kimi 将恢复官方外观。
pause
