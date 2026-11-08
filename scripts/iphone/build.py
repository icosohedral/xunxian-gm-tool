"""Build iPhone item prototypes from the current server and client packages."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.dpk_append import append_entries
from scripts.unpack import DpkReader

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
ITEM_ID = 60080
USE_ID = 20402
SCRIPT_ID = 2502
TEXT = {
    50031: "一部神奇的 iPhone 手机，使用后打开功能菜单。",
    50032: "已安装App：",
    50033: "通讯录",
    50034: "同花顺",
    50035: "美团外卖",
    50036: "该功能即将开放。",
    50037: "返回",
    50038: "关闭",
    50039: "我的持仓：腾讯科技 %d 股（市值%s），英伟达 %d 股（市值%s），Anthropic %d 股（市值%s）。选择股票交易：",
    50040: "呼叫联系人",
    50041: "呼叫警察",
    50042: "呼叫救护车",
    50043: "腾讯科技",
    50044: "英伟达",
    50045: "Anthropic",
    50046: "美团外卖，送啥都快",
    50047: "@@IPHONE_DELIVERY_OPEN",
    50048: "@@IPHONE_DELIVERY_CLOSE",
    50049: "十级强效活血药 ×200　20金",
    50050: "十级强效理气药 ×200　20金",
    50051: "高级跌打药 ×50　5砖",
    50052: "返魂仙丹 ×50　10砖",
    50053: "妖怪精魄 ×200　1金",
    50054: "购买成功，物品已放入背包。",
    50055: "流通金不足，购买失败。",
    50056: "背包空间不足，购买失败。",
    50057: "石塘大药房",
    50058: "宠物商店",
    50059: "京东超市",
    50060: "高级侍宠玩具 ×10　10砖",
    50061: "坚固照妖镜 ×1　10金",
    50062: "炼妖壶 ×1　1砖",
    50063: "愚人节礼包 ×1　1砖",
    50064: "高级骑宠玩具 ×10　10砖",
    50065: "QQ音乐",
    50066: "QQ音乐：请选择音乐（第 %d 页 / 共 %d 页）。",
    50067: "中原·白天",
    50068: "中原·夜晚",
    50069: "停止播放",
    50070: "@@IPHONE_MUSIC|%d",
    50071: "备用",
    50072: "@@IPHONE_MUSIC_STOP",
    50073: "上一页",
    50074: "下一页",
}


def add_row(original: bytes, fields: list[str], ident: int) -> bytes:
    lines = original.decode("gb18030").splitlines()
    if any(line.split("\t", 1)[0] == str(ident) for line in lines[1:]):
        raise ValueError(f"ID {ident} 已存在")
    width = len(lines[1].split("\t"))
    if len(fields) > width:
        raise ValueError(f"新增行字段过多：{len(fields)} > {width}")
    new_row = "\t".join(fields + [""] * (width - len(fields))).encode("gb18030")
    return original.rstrip(b"\r\n") + b"\r\n" + new_row + b"\r\n"


def make_edits(source: Path) -> dict[str, bytes]:
    paths = ("item/item_list.txt", "item/item_use.txt",
             "server/script_func_reg.txt", "etc/talk_list.txt", "etc/text.txt",
             "pet/summon_type.txt")
    with DpkReader(source) as reader:
        entries = {entry.path: entry for entry in reader.read_entries()}
        original = {name: reader.extract(entries[name]) for name in paths}
    result = {}

    lines = original[paths[0]].decode("gb18030").splitlines()
    header = lines[0].split("\t")
    item = next(line.split("\t") for line in lines[1:]
                if line.split("\t", 1)[0] == "7")
    item[0:3] = [str(ITEM_ID), "iPhone手机", "1"]
    item[4] = "rw498"  # Existing unused icon slot, repacked with iPhone.png.
    item[6] = "50031"  # Tooltip is looked up in etc/text.txt.
    item[7] = "1"
    item[header.index("使用属性")] = str(USE_ID)
    item[header.index("重叠数量")] = "1"
    item[header.index("唯一")] = "1"
    result[paths[0]] = add_row(original[paths[0]], item, ITEM_ID)

    lines = original[paths[1]].decode("gb18030").splitlines()
    use = [""] * len(lines[1].split("\t"))
    use[0:3] = [str(USE_ID), "3", str(SCRIPT_ID)]
    use[3:7] = ["0", "0", "0", "0"]
    use[10] = "1"  # Keep the item after use.
    result[paths[1]] = add_row(original[paths[1]], use, USE_ID)

    lines = original[paths[2]].decode("gb18030").splitlines()
    header = lines[0].split("\t")
    reg = ["0"] * len(lines[1].split("\t"))
    reg[0:3] = [str(SCRIPT_ID), "0", "i_iphone_phone"]
    item_use_index = next(index for index, value in enumerate(header)
                          if "Item_Use" in value)
    reg[item_use_index] = "1"
    result[paths[2]] = add_row(original[paths[2]], reg, SCRIPT_ID)

    data = original[paths[3]]
    for ident, text in TEXT.items():
        data = add_row(data, [str(ident), text], ident)
    result[paths[3]] = data
    result[paths[4]] = add_row(original[paths[4]], ["50031", "来自2000年后的尖端科技"], 50031)
    summon_lines = original[paths[5]].decode("gb18030").splitlines()
    width = max(len(summon_lines[0].split("\t")), len(summon_lines[1].split("\t")))
    summon_row = [""] * width
    for index, value in {0: "手机援助", 2: "1", 4: "1", 5: "2", 6: "20", 7: "120", 9: "0", 11: "1", 12: "1", 13: "0", 14: "0", 15: "1"}.items():
        summon_row[index] = value
    if width > 16:
        summon_row[16] = "1"
    data = original[paths[5]]
    for ident in (430, 431):
        if any(len(line.split("\t")) > 1 and line.split("\t")[1] == str(ident) for line in summon_lines[1:]):
            raise ValueError(f"召唤类型 {ident} 已存在")
        summon_row[1] = str(ident)
        data = data.rstrip(b"\r\n") + b"\r\n" + ("\t".join(summon_row) + "\r\n").encode("gb18030")
    result[paths[5]] = data
    return result


def repack(source: Path, output: Path, edits: dict[str, bytes]) -> None:
    work = BUILD / source.stem
    work.mkdir(parents=True, exist_ok=True)
    current = source
    for index, (name, content) in enumerate(edits.items()):
        replacement = work / Path(name).name
        replacement.write_bytes(content)
        target = output if index == len(edits) - 1 else work / f"step{index + 1}.dpk"
        subprocess.run([sys.executable, str(ROOT / "scripts/repack.py"),
                        str(current), name, str(replacement), "-o", str(target),
                        "--allow-chain-growth", "--allow-chain-shrink",
                        "--overwrite-output"], check=True, stdout=subprocess.DEVNULL)
        if current != source:
            current.unlink()
        current = target
    with DpkReader(output) as reader:
        entries = {entry.path: entry for entry in reader.read_entries()}
        for name, expected in edits.items():
            if reader.extract(entries[name]) != expected:
                raise RuntimeError(f"回读校验失败：{output} {name}")
    print(output)


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)
    packages = (
        (ROOT / "data/iphone/remote/server_main.dpk", BUILD / "server_main.prototype.dpk"),
        (ROOT / "data/iphone/remote/server_dynamic.dpk", BUILD / "server_dynamic.prototype.dpk"),
        (Path("/Users/icosohedral/Projects/xunxian_standalone/client/MhXunxian/mb.dpk.bak.iphone-before"),
         BUILD / "client_mb.prototype.dpk"),
    )
    for source, output in packages:
        repack(source, output, make_edits(source))
    script = BUILD / "script.prototype.dpk"
    script.unlink(missing_ok=True)
    append_entries(ROOT / "data/iphone/remote/script.dpk", script,
                   {"specialuse_script/i_iphone_phone.c":
                    (HERE / "i_iphone_phone.c").read_text(encoding="utf-8").encode("gb18030")})
    with DpkReader(script) as reader:
        entries = {entry.path: entry for entry in reader.read_entries()}
        assert reader.extract(entries["specialuse_script/i_iphone_phone.c"]) == (
            HERE / "i_iphone_phone.c").read_text(encoding="utf-8").encode("gb18030")
    print(script)


if __name__ == "__main__":
    main()
