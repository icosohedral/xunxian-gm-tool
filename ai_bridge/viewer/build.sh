#!/bin/zsh
# 编译「寻仙实时动态」悬浮窗 .app，并复制到客户端的 launcher/ 文件夹（可用第一个参数指定目标目录）。
# 名称表 names.json 每次从客户端 mb.dpk 重新导出（需要仓库的 .venv）。
set -euo pipefail
SCRIPT_DIR="${0:A:h}"
REPO="${SCRIPT_DIR:h:h}"
cd -- "$SCRIPT_DIR"

APP_NAME="寻仙实时动态"
BUILD_DIR="../build/viewer"
APP="$BUILD_DIR/$APP_NAME.app"
DEST="${1:-$REPO/../xunxian_standalone/client/MhXunxian/launcher}"

"$REPO/.venv/bin/python" "$REPO/ai_bridge/export_names.py"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -parse-as-library -O -target arm64-apple-macos14.0 \
  XunxianViewer.swift -o "$APP/Contents/MacOS/XunxianViewer"
cp Info.plist "$APP/Contents/Info.plist"
cp names.json "$APP/Contents/Resources/names.json"
codesign --force --sign - "$APP"
echo "已生成：$APP"

if [[ -d "$DEST" ]]; then
  rm -rf "$DEST/$APP_NAME.app"
  cp -R "$APP" "$DEST/"
  echo "已复制到：$DEST/$APP_NAME.app"
fi
