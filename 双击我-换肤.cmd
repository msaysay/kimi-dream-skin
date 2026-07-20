@echo off
rem ============================================================
rem  Kimi Dream Skin - 双击换肤入口
rem  原理：通过 CDP 给官方 Kimi 桌面版注入 CSS，不改任何程序文件
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   ========================================
echo     Kimi Dream Skin - 一键换肤
echo   ========================================
echo.
echo     1. 青蓝歌姬   (diva)
echo     2. 粉雾玫瑰   (rose)
echo     3. 财神打工   (caishen)
echo     4. 舞台黑金   (blackgold)
echo     5. 红白科幻   (scifi)
echo     6. 哥特虚空   (gothic)   [默认推荐]
echo     0. 退出
echo.
choice /c 1234560 /n /m "  请选择皮肤编号: "
if errorlevel 7 exit /b 0
if errorlevel 6 set THEME=gothic
if errorlevel 5 set THEME=scifi
if errorlevel 4 set THEME=blackgold
if errorlevel 3 set THEME=caishen
if errorlevel 2 set THEME=rose
if errorlevel 1 set THEME=diva

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
