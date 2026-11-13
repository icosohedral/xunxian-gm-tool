#!/bin/zsh
# 编译寻仙登录器 .app，并复制到客户端目录（可用第一个参数指定目标目录）。
set -euo pipefail
cd -- "${0:A:h}"

APP_NAME="寻仙登录器"
BUILD_DIR="../build/launcher"
APP="$BUILD_DIR/$APP_NAME.app"
DEST="${1:-../../xunxian_standalone/client/MhXunxian}"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -parse-as-library -O -target arm64-apple-macos14.0 \
  XunxianLauncher.swift -o "$APP/Contents/MacOS/XunxianLauncher"
cp Info.plist "$APP/Contents/Info.plist"

# 图标：整理成 1024 标准图标，再生成各尺寸并打包成 icns
# 当前原图是 64×64、透明背景的游戏头像：垫一个纯白圆角底，按像素画方式整数倍放大（占边长约九成）
ICON_FLAGS=(--pixelated --background "#FFFFFF,#FFFFFF" --fill 0.92)
if [[ -f assets/AppIcon-source.png ]]; then
  ICONSET="$BUILD_DIR/AppIcon.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  swiftc -O make_icon.swift -o "$BUILD_DIR/make_icon"
  "$BUILD_DIR/make_icon" assets/AppIcon-source.png "$BUILD_DIR/AppIcon-1024.png" "${ICON_FLAGS[@]}" >/dev/null
  for size in 16 32 128 256 512; do
    sips -z $size $size "$BUILD_DIR/AppIcon-1024.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z $double $double "$BUILD_DIR/AppIcon-1024.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
  # 运行时用它替换程序坞图标（.icns 在 macOS 26 上会被套灰框）
  cp "$BUILD_DIR/AppIcon-1024.png" "$APP/Contents/Resources/AppIcon.png"
  swiftc -O set_icon.swift -o "$BUILD_DIR/set_icon"
fi
codesign --force --sign - "$APP"
[[ -x "$BUILD_DIR/set_icon" ]] && "$BUILD_DIR/set_icon" "$APP" "$BUILD_DIR/AppIcon-1024.png"
echo "已生成：$APP"

if [[ -d "$DEST/release" ]]; then
  rm -rf "$DEST/$APP_NAME.app"
  cp -R "$APP" "$DEST/"
  [[ -x "$BUILD_DIR/set_icon" ]] && "$BUILD_DIR/set_icon" "$DEST/$APP_NAME.app" "$BUILD_DIR/AppIcon-1024.png"
  echo "已复制到：$DEST/$APP_NAME.app"
fi
