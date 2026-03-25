#!/bin/bash
# RssCrawler 部署脚本
# 用法: ./deploy.sh

set -e

SERVER="openclaw"
REMOTE_DIR="/opt/RssCrawler"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== RssCrawler 部署 ==="
echo "本地目录: $LOCAL_DIR"
echo "目标服务器: $SERVER:$REMOTE_DIR"
echo ""

# 1. 同步文件
echo "[1/3] 同步文件到服务器..."
rsync -avz \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude 'articles.db' \
  --exclude 'logs/*.log' \
  --exclude 'output/*.xml' \
  --exclude '.git' \
  "$LOCAL_DIR/" "$SERVER:$REMOTE_DIR/"
echo ""

# 2. 安装依赖（仅在 requirements.txt 变更时需要）
echo "[2/3] 检查并安装依赖..."
ssh "$SERVER" "cd $REMOTE_DIR && pip3 install -r requirements.txt --break-system-packages -q"
echo ""

# 3. 重启服务
echo "[3/3] 重启服务..."
ssh "$SERVER" "
  sudo systemctl stop rsscrawler 2>/dev/null || true
  sleep 1
  sudo fuser -k 8080/tcp 2>/dev/null || true
  sleep 1
  sudo systemctl start rsscrawler
  sleep 2
  sudo systemctl status rsscrawler --no-pager
"

echo ""
echo "=== 部署完成 ==="
