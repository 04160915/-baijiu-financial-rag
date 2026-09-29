@echo off
cd /d "D:\财报库"
echo.
echo   继续算财报向量
echo.
echo   * 上次算到哪，就从哪接着算，不会从头来。
echo   * 算的时候别关这个黑窗口。中途关了也没事，
echo     下次再双击本文件，照样接着算。
echo   * 全部算完大约需要 90 分钟（剩多少看窗口里的提示）。
echo.
python -u "向量化.py"
echo.
echo   结束了。可以关掉这个窗口。
pause
