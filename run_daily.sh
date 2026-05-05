#!/bin/bash

# 获取当前脚本所在的项目根目录
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR" || exit

# 尝试定位 uv 的位置
UV_PATH="$HOME/.local/bin/uv"
if [ ! -f "$UV_PATH" ]; then
    UV_PATH="$HOME/.cargo/bin/uv"
fi
if [ ! -f "$UV_PATH" ]; then
    UV_PATH=$(which uv)
fi

# 记录运行时间
echo "=====================================" >> ./daily.log
echo "Starting daily run at $(date)" >> ./daily.log

# 运行主程序并将日志追加到 daily.log 中
$UV_PATH run python src/zotero_arxiv_daily/main.py >> ./daily.log 2>&1

echo "Finished run at $(date)" >> ./daily.log
