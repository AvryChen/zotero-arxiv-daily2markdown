#!/bin/bash

# 加载用户的环境变量，以防 cron 执行时找不到 uv 或 git 等命令
if [ -f ~/.bashrc ]; then
    source ~/.bashrc
fi

# 获取当前脚本所在的项目根目录，并进入该目录
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR" || exit

echo "=====================================" >> ./daily.log
echo "Starting daily run at $(date)" >> ./daily.log

# 在 Ubuntu 上，uv 默认安装路径通常是 ~/.cargo/bin/uv 或 ~/.local/bin/uv
# 如果你将其添加到了 PATH，也可以直接写 uv
UV_PATH="$HOME/.local/bin/uv"
if [ ! -f "$UV_PATH" ]; then
    UV_PATH="$HOME/.cargo/bin/uv"
fi
if [ ! -f "$UV_PATH" ]; then
    UV_PATH=$(which uv)
fi

$UV_PATH run python src/zotero_arxiv_daily2markdown/main.py >> ./daily.log 2>&1

echo "Finished run at $(date)" >> ./daily.log
