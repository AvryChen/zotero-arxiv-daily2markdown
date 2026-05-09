@echo off
setlocal
chcp 65001 >nul

:: 获取当前脚本所在的项目根目录并进入
cd /d "%~dp0"

:: 记录运行时间
echo ===================================== >> daily.log
echo Starting daily run at %date% %time% >> daily.log

:: 运行主程序并将日志追加到 daily.log 中
:: 这里假设 uv 已经在系统的环境变量 PATH 中
uv run python src/zotero_arxiv_daily2markdown/main.py >> daily.log 2>&1

echo Finished run at %date% %time% >> daily.log

endlocal
