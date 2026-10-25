#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo '请先安装 uv（Python 环境管理工具）'; exit 1; }
command -v npm >/dev/null || { echo '请安装 Node.js 22.12+ 或 24'; exit 1; }
if [ ! -x .venv/bin/python ]; then uv venv --python 3.12 .venv; fi
uv pip sync --python .venv/bin/python backend/requirements.txt
npm --prefix frontend ci
if [ ! -f .env ]; then cp .env.example .env; fi
npm --prefix frontend run build
echo '安装完成。运行 bash scripts/start.sh 启动。'
