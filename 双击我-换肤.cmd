@echo off
rem ============================================================
rem  Kimi Dream Skin - 双击换肤入口
rem  原理：通过 CDP 给官方 Kimi 桌面版注入 CSS，不改任何程序文件
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   ==========================================
echo     Kimi Dream Skin - 一键换肤 (10 套主题)
echo   ==========================================
echo.
echo     1. 青蓝歌姬      2. 粉雾玫瑰
echo     3. 财神打工      4. 舞台黑金
echo     5. 红白科幻      6. 哥特虚空
echo     7. 敦煌飞天      8. 青花瓷
echo     9. 千里江山(默认) 10. 水墨佳人
echo     0. 退出
echo.
set /p N="  请输入主题编号: "
if "%N%"=="0" exit /b 0
if "%N%"=="1" set THEME=diva
if "%N%"=="2" set THEME=rose
if "%N%"=="3" set THEME=caishen
if "%N%"=="4" set THEME=blackgold
if "%N%"=="5" set THEME=scifi
if "%N%"=="6" set THEME=gothic
if "%N%"=="7" set THEME=dunhuang
if "%N%"=="8" set THEME=qinghua
if "%N%"=="9" set THEME=jiangshan
if "%N%"=="10" set THEME=moyun
if not defined THEME (echo   编号无效 & pause & exit /b 1)

echo.
choice /c 12 /n /m "  1.经典版  2.惊喜版（同一主题的另一重宇宙）: "
if errorlevel 2 set THEME=%THEME%-surprise

where node >/dev/null 2>/dev/null
if errorlevel 1 (
    echo.
    echo   [缺少依赖] 未检测到 Node.js，请先安装 Node.js 18 或更高版本：
    echo   https://nodejs.org/
    echo.
    pause
    exit /b 1
)

:retry
echo.
echo   正在应用皮肤 [%THEME%] ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\scripts\start-dream-skin.ps1" -Theme %THEME%
if errorlevel 2 (
    echo.
    echo   请先完全退出 Kimi（包括右下角托盘图标），然后按任意键重试...
    pause >/dev/null
    goto retry
)
if errorlevel 1 (
    echo.
    echo   应用失败，请把上方错误信息截图反馈。
    pause
    exit /b 1
)

echo.
echo   完成！想换回官方外观时，双击"双击我-还原.cmd"即可。
pause
