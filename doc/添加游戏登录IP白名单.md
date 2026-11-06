# 添加游戏登录 IP 白名单

适用于当前安装在 `/opt/xxserver` 的寻仙服务端。服务端配置位于 `/opt/xxserver/runtime/all/xxsrv.ini`，`[net]` 段的 `allow_cidr` 控制允许连接的客户端来源 IP。每个单独的公网 IPv4 地址写为 `/32`，多个地址用英文逗号分隔。

## 1. 确认客户端实际出口 IP

先让客户端尝试登录一次，再在服务器上查看最近运行目录的拒绝日志：

```bash
run=$(sudo python3 -c 'import json; print(json.load(open("/opt/xxserver/state/active.json"))["directory"])')
sudo grep '\[net\]' "$run/backend.log" | tail -20
```

工作台的「最近 5 次网络连接记录」按 `/opt/xxserver/runs/` 下的运行目录时间从新到旧读取。恢复启动创建的新运行目录可能尚未写入 `state/active.json`，因此页面记录不依赖该指针；手动排查时可直接检查对应的新运行目录日志。

如果日志显示 `拒绝UDP来源 116.6.131.30`，需要放行的是 `116.6.131.30/32`。不要仅凭客户端设备显示的本地 IP 或先前记录的公网 IP 判断；更换网络、运营商 NAT 后，实际出口 IP 可能变化。确认日志中的地址确实属于要登录的客户端，再添加。

## 2. 备份并追加地址

先查看现有配置，保留已有白名单：

```bash
cfg=/opt/xxserver/runtime/all/xxsrv.ini
sudo grep -E '^(allow_wan|allow_cidr|cls) =' "$cfg"
sudo cp -p "$cfg" "$cfg.bak.$(date +%Y%m%d-%H%M%S)"
```

编辑 `[net]` 段的 `allow_cidr`，在原值末尾用英文逗号追加新地址。例如本次操作后为：

```ini
allow_wan = false
allow_cidr = 115.231.111.101/32,14.113.141.88/32,116.6.131.30/32
```

可用 `sudo nano "$cfg"` 编辑。保存后再运行 `sudo grep '^allow_cidr' "$cfg"`，确认没有覆盖旧地址、重复添加或出现空格拼写错误。不要把 `allow_wan` 改为 `true`。

## 3. 正常重启使配置生效

让所有客户端先正常退出游戏。确认没有在线玩家后，使用服务端脚本停服和启动：

```bash
sudo /opt/xxserver/stop.sh --clients-exited
sudo /opt/xxserver/start.sh
sudo /opt/xxserver/status.sh
```

`stop.sh` 会再次检查在线人数；如仍有人在线，它会拒绝停服。启动输出中的 `ready_for_manual_client_test` 表示进程已就绪，还需要实际登录验证。不要直接杀进程代替正常停服。

## 4. 核对端口和登录结果

当前部署实际监听 **TCP/UDP 3100**（游戏入口）和 **UDP 15001**（CLS）：

```bash
sudo ss -lntup | grep -E ':(3100|15001)([[:space:]]|$)'
run=$(sudo python3 -c 'import json; print(json.load(open("/opt/xxserver/state/active.json"))["directory"])')
sudo grep '\[net\]' "$run/backend.log" | tail -20
```

客户端应连接服务器公网地址。云安全组需要允许客户端来源访问 TCP/UDP 3100 和 UDP 15001。当前服务**没有监听 15000**，不要把 15000 当作 CLS 端口。若新运行目录仍出现该客户端 IP 的 `[net] 拒绝` 日志，核对实际出口 IP、`allow_cidr` 和重启是否成功；若服务端完全没有收到包，优先核对客户端目标地址及云安全组。

## 本次记录（2026-10-23）

本次先加入 `14.113.141.88/32`，客户端仍无法登录；当前运行日志显示实际请求来自 `116.6.131.30`。确认后追加 `116.6.131.30/32`，保留原有 `115.231.111.101/32`，正常重启后客户端登录成功。此记录用于说明排错过程，日后操作应以服务器**当前**配置和**当前运行目录**日志为准。

## 公网登录门票地址排查（2026-11-02）

服务端重装后，即使白名单已放行，公网客户端仍提示检查 15000–15009 端口。排查时应区分**入口白名单**与**登录门票中的 CLS 地址**：

1. 工作台中新增客户端公网出口 `58.16.87.23/32`，重启后的日志显示该地址的握手已到达服务端。白名单页面的历史拒绝记录发生在添加之前，不能据此判断重启后仍被拒绝。
2. 查看当前运行日志里的门票目标：

   ```bash
   run=$(sudo python3 -c 'import json; print(json.load(open("/opt/xxserver/state/active.json"))["directory"])')
   sudo grep -aE 'V44 bridge alloc|V41 >>> 0x5d|拒绝UDP来源' "$run/backend.log" | tail -20
   sudo ss -lunp | grep -E ':(3100|15001)([[:space:]]|$)'
   ```

   本次服务端实际 CLS 监听 `10.0.0.4:15001`，但门票最初返回这个内网地址，公网客户端无法直接使用。
3. 本次在 `/opt/xxserver/runtime/xx/shm/XCLS2/cls2_cfg1.txt` 设置 CLS 的假对外地址，让它绑定内网地址的同时向客户端通告公网地址。原文件备份为同目录的 `cls2_cfg1.txt.bak.gmtool-public`。具体设置为：

   ```text
   #define FAKEWELCOMEADDR 124.221.201.132:0
   ```

   **这里的 `:0` 不是客户端要连接的端口。** 本次实现会将该值与实际 CLS 端口相加；曾填写 `:15001`，结果门票错误地返回了 `:30002`。不要把该配置机械套用于其他版本；以新运行日志中的实际门票和监听端口为准。公网 IP 改变时应使用当前服务器的实际公网地址，云安全组也需允许客户端访问 UDP 15001。
4. 正常停服并启动后，门票日志显示 `CLS=124.221.201.132:15001`，用户确认登录成功。若日志中地址正确仍无法登录，继续检查客户端出口地址、云安全组和 UDP 15001 的实际连通性。

注意：此恢复包的 `xxsrv.ini` 注释注明公网部署不受支持。白名单只限制后端入口来源，不应据此认为直接暴露的其他服务端口已获得同等保护；具体网络隔离仍以实际部署配置为准。
