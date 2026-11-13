#!/usr/bin/env python3
"""接收 AI 桥发来的游戏数据包，每个包输出一行 JSON。

用法：python3 receiver.py [--port 47800] [--out packets.jsonl]

用 TCP 接收。帧格式：魔数 b"XXB1" | 序号 u32 | 毫秒时间 u32 | 长度 u32 | 原始包。
游戏启动后插件每秒尝试连接一次，先开接收端、后开游戏，或者反过来都可以。
原始包第 1 个字节是包类型 bType。名称和含义来自对录像的分析（doc/游戏录像格式分析.md），
标"?"的是推测。
"""

import argparse
import json
import socket
import struct
import sys
import time

TYPE_NAMES = {
    0x81: "move",            # 129 移动 / 位置同步
    0x83: "turn",            # 131 转向
    0x84: "move_method",     # 132 切换移动方式
    0x85: "flag",            # 133 对象标志（9 = 蓝量百分比?）
    0x87: "skill_end",       # 135 技能结束
    0x88: "skill_begin",     # 136 技能开始
    0x89: "equip",           # 137 装备信息
    0x8F: "state",           # 143 状态增删
    0x98: "title",           # 152 头顶称号
    0xB4: "appear",          # 180 角色 / 怪物进入视野
    0xB6: "attr",            # 182 属性值?
    0xBC: "npc_appear",      # 188 NPC 进入视野
    0xBF: "cooldown",        # 191 冷却计时
    0xC9: "self_full",       # 201 自己的完整角色数据
    0xCA: "enter_scene",     # 202 进入场景
    0xD0: "remove",          # 208 对象移除?
}


def main():
    parser = argparse.ArgumentParser(description="接收寻仙 AI 桥的数据包")
    parser.add_argument("--port", type=int, default=47800)
    parser.add_argument("--out", help="同时追加写入这个 JSONL 文件")
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(1)
    out = open(args.out, "a", encoding="utf-8") if args.out else None
    print(f"正在监听 127.0.0.1:{args.port}，等待游戏连接……", file=sys.stderr)

    while True:
        conn, _ = server.accept()
        print("游戏已连接", file=sys.stderr)
        last_seq = 0
        try:
            while True:
                head = recv_exact(conn, 16)
                if head is None:
                    break
                if head[:4] != b"XXB1":
                    print("帧头不对，断开重连", file=sys.stderr)
                    break
                seq, tick, size = struct.unpack_from("<III", head, 4)
                packet = recv_exact(conn, size)
                if packet is None:
                    break
                if last_seq and seq != last_seq + 1:
                    print(f"注意：序号从 {last_seq} 跳到 {seq}，中间丢了包", file=sys.stderr)
                last_seq = seq
                btype = packet[0]
                record = {
                    "seq": seq,
                    "tick": tick,
                    "time": time.strftime("%H:%M:%S"),
                    "bType": btype,
                    "name": TYPE_NAMES.get(btype, "?"),
                    "size": size,
                    "hex": packet.hex(),
                }
                line = json.dumps(record, ensure_ascii=False)
                print(line, flush=True)
                if out:
                    out.write(line + "\n")
                    out.flush()
        finally:
            conn.close()
            print("游戏断开连接，继续等待", file=sys.stderr)


def recv_exact(conn, size):
    """读满 size 字节；对方断开时返回 None。"""
    chunks = []
    while size:
        chunk = conn.recv(min(size, 65536))
        if not chunk:
            return None
        chunks.append(chunk)
        size -= len(chunk)
    return b"".join(chunks)

if __name__ == "__main__":
    main()
