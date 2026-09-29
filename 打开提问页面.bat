@echo off
cd /d "D:\财报库"
echo.
echo   正在启动财报问答页面，浏览器会自动打开 ...
echo   用完直接关掉这个黑窗口就行。
echo.
python -m streamlit run "提问页面.py" --server.address 127.0.0.1
echo.
echo   页面已退出。
pause
