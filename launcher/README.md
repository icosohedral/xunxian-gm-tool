# 寻仙登录器

macOS 小应用：保存多个游戏账号，点一下就通过 CrossOver 启动客户端并自动登录。

## 编译

```bash
./build.sh
```

生成 `../build/launcher/寻仙登录器.app`，并复制到 `../../xunxian_standalone/client/MhXunxian/`。也可以用第一个参数指定别的客户端目录。

## 图标

图标原图放在 `assets/AppIcon-source.png`。编译时 `make_icon.swift` 会重新切出圆角，再居中放进 1024×1024 画布，主体占 824×824，然后生成 `.icns`。`build.sh` 里的 `ICON_FLAGS` 决定怎么处理原图：

- 现在用的是 64×64、透明背景的游戏头像 `sociality.png`，参数是 `--pixelated --background "#FFFFFF,#FFFFFF" --fill 0.92`：先画一个纯白圆角底，再把头像按整数倍放大后居中放上去，最多占边长的 92%。底色改 `--background` 的两个颜色（上色、下色，相同就是纯色），大小改 `--fill`。
- 64×64、自带边框的技能图标用 `--trim 4 --pixelated`：裁掉四周 4 像素的边框，用最近邻放大保留像素感。
- 换成带透明背景的圆角方块大图时，把 `ICON_FLAGS` 清空即可，脚本会自动找出主体并去掉噪点。

macOS 26 会给只带 `.icns`、没有新版 Assets.car 的 App 套上灰色圆角框，而生成 Assets.car 需要完整的 Xcode。所以这里另想了两个办法：

- 编译后用 `set_icon.swift` 给 App 设置 Finder 自定义图标。
- App 启动时用 `Resources/AppIcon.png` 替换程序坞图标。

## 客户端目录结构

客户端根目录只留 `寻仙登录器.app` 一个启动入口，其余启动相关文件都放在 `launcher/` 子文件夹：

```
MhXunxian/
├── 寻仙登录器.app
└── launcher/
    ├── start_crossover.bat     # 启动模板，保存 GBK 编码的服务器名，不能删
    ├── _launcher.bat           # 每次登录时生成，不含密码
    ├── launcher_accounts.json  # 保存的账号、密码、备注
    ├── 启动寻仙.command        # 旧的手动启动脚本，登录器坏了时备用
    └── 啟動夢回尋仙.bat        # 原版 Windows 启动脚本，里面的服务器 IP 已过时
```

这些 bat 都在子文件夹里，所以开头用 `cd /d "%~dp0.."` 切回客户端根目录，`release\tty3d.exe` 的相对路径才对得上。旧版登录器放在根目录的 `launcher_accounts.json` 和 `start_crossover.bat` 也能自动识别：账号文件读到时会挪进 `launcher/`，模板则按先 `launcher/`、再根目录的顺序查找。

## 原理

`tty3d.exe` 支持命令行参数 `-q "x:账号:x:密码"`（冒号分隔 4 段，第 2 段是账号，第 4 段是密码）。客户端找不到腾讯 TCLS 登录器时，会把这两段写进网络对象的 agent name / agent pwd。登录窗口脚本 `gui/section/user_entry/user_entry.cc` 第一次显示时如果读到这两个值，就会自动填入并登录。

全部启动参数、函数地址和验证过程见 [客户端启动参数分析](../doc/客户端启动参数分析.md)。

登录器每次启动时会：

1. 解析服务器域名（默认 `game.xunxian.wiki`）。本机 DNS 解析失败时，改用 Cloudflare DoH。
2. 以 `launcher/start_crossover.bat` 为模板生成 `launcher/_launcher.bat`，换上最新 IP，追加 `-q "0:%XXL_USER%:0:%XXL_PASS%"`。
3. 通过环境变量把账号密码传给 `wine cmd /c launcher\_launcher.bat`。

账号、密码和备注明文保存在 `launcher/launcher_accounts.json`，复制或分享游戏文件夹前记得删掉它。旧版存进钥匙串的账号，第一次打开新版时会自动迁移过来，并从钥匙串删除。

## 限制

- 账号和密码里不能有 `:`、`"`、`%`。
- `launcher_accounts.json` 和游戏运行时的进程命令行里，密码都是明文。
- `-q` 不处理角色密码。设了角色密码的账号，仍会弹出输入框。
