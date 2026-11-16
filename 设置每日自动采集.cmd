@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 将创建一个 Windows 计划任务：每天 09:00 自动采集一次数据（电脑要开着，不需要打开工作台）。
echo 想取消的话，在「任务计划程序」里删除「创作者工作台-每日采集」即可。
echo.
schtasks /Create /F /SC DAILY /ST 09:00 /TN "创作者工作台-每日采集" /TR "\"%~dp0scripts\daily-collect.cmd\""
echo.
pause
