#!/bin/zsh
# 卸载替身插件，恢复客户端原来的 gcassist.so。
set -euo pipefail
cd -- "${0:A:h}"
RELEASE="${1:-../../xunxian_standalone/client/MhXunxian/release}"
if [[ -f "$RELEASE/gcassist.so.orig" ]]; then
  cp "$RELEASE/gcassist.so.orig" "$RELEASE/gcassist.so"
  rm -f "$RELEASE/gcassist_real.dll" "$RELEASE/gcassist.so.orig"
  echo "已恢复原插件"
else
  echo "没有找到备份 gcassist.so.orig，可能没安装过"
fi
