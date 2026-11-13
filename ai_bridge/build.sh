#!/bin/zsh
# 编译替身插件 gcassist.so（32 位 Windows DLL）。需要 brew install mingw-w64。
set -euo pipefail
cd -- "${0:A:h}"
mkdir -p build
i686-w64-mingw32-gcc -O2 -Wall -shared -static-libgcc \
  -o build/gcassist.so bridge.c gcassist.def -lws2_32
echo "已生成：$PWD/build/gcassist.so"
