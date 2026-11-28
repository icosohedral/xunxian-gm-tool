#!/usr/bin/env bash
# 把 index.html 和 assets/ 发布到 https://gm.xunxian.wiki（Cloudflare Worker 静态资源）。
# 用法：deploy/deploy.sh [--dry-run]
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$DEPLOY_DIR")"
PUBLIC="$DEPLOY_DIR/public"

rm -rf "$PUBLIC"
mkdir -p "$PUBLIC"
cp "$ROOT/index.html" "$PUBLIC/"
cp -R "$ROOT/assets" "$PUBLIC/assets"
find "$PUBLIC" -name '.DS_Store' -delete

echo "待发布文件：$(find "$PUBLIC" -type f | wc -l | tr -d ' ') 个"

# 显式指定配置文件，避免 wrangler 自动生成配置把整个仓库当站点上传。
cd "$DEPLOY_DIR"
npx -y wrangler@4 deploy --config "$DEPLOY_DIR/wrangler.jsonc" "$@"
