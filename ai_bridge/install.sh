#!/bin/zsh
# 把替身插件装进客户端：原 gcassist.so 改名为 gcassist_real.dll（插件导出会转发给它），
# 再放入编译好的替身。可用第一个参数指定客户端的 release 目录。
set -euo pipefail
cd -- "${0:A:h}"
RELEASE="${1:-../../xunxian_standalone/client/MhXunxian/release}"
[[ -f "$RELEASE/tty3d.exe" ]] || { echo "找不到 $RELEASE/tty3d.exe"; exit 1; }
[[ -f build/gcassist.so ]] || ./build.sh

if [[ ! -f "$RELEASE/gcassist_real.dll" ]]; then
  cp "$RELEASE/gcassist.so" "$RELEASE/gcassist.so.orig"
  mv "$RELEASE/gcassist.so" "$RELEASE/gcassist_real.dll"
  echo "原插件已改名为 gcassist_real.dll，并备份为 gcassist.so.orig"
fi
cp build/gcassist.so "$RELEASE/gcassist.so"
echo "已安装替身插件到 $RELEASE/gcassist.so"
