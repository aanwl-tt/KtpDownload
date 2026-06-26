@echo off
echo ========================================
echo   课堂派下载工具 EXE 打包脚本
echo ========================================
echo.

echo [1/2] 正在打包 GUI 版本...
python -m PyInstaller --onefile --windowed --name "课堂派下载工具" --exclude-module torch --exclude-module scipy --exclude-module matplotlib --exclude-module pandas --exclude-module numpy --exclude-module PIL --exclude-module lxml --exclude-module jinja2 --exclude-module cryptography ktp_download_gui.py

echo.
echo [2/2] 打包完成!
echo EXE 文件位置: dist\课堂派下载工具.exe
echo.
pause
