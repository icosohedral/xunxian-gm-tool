"""Build the Xiaohuang dialogue resources without changing source DPK files."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader

HERE = Path(__file__).resolve().parent
OUT = HERE / "build"
TALK_PATH = "etc/talk_list.txt"
REG_PATH = "server/script_func_reg.txt"
SCRIPT_ID = 2500
NPC_ID = 38316
SPAWN_PATH = "server/area_refresh/xxsc_tudimiao.txt"
TEMP_FLAG_PATH = "server/temp_dword_player.txt"


def append_rows(original: bytes, rows: list[str]) -> bytes:
    text = original.decode("gb18030")
    return (text.rstrip("\r\n") + "\r\n" + "\r\n".join(rows) + "\r\n").encode("gb18030")


def build_package(source: Path, name: str) -> None:
    with DpkReader(source) as reader:
        entries = {e.path: e for e in reader.read_entries()}
        talk = reader.extract(entries[TALK_PATH])
        lines = [line for line in (HERE / "talk_lines.tsv").read_text().splitlines() if line]
        used = {row.split("\t", 1)[0] for row in talk.decode("gb18030").splitlines()}
        ids = [line.split("\t", 1)[0] for line in lines]
        if len(ids) != len(set(ids)) or set(ids) & used:
            raise ValueError(f"{name}: 文字 ID 冲突")
        # Preserve the table's existing trailing tabs, which are present in the source.
        width = max(len(row.split("\t")) for row in talk.decode("gb18030").splitlines()[:5])
        padded = [line + "\t" * (width - len(line.split("\t"))) for line in lines]
        target = OUT / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "talk_list.txt").write_bytes(append_rows(talk, padded))

        reg = reader.extract(entries[REG_PATH])
        reg_lines = reg.decode("gb18030").splitlines()
        if str(SCRIPT_ID) in {line.split("\t", 1)[0] for line in reg_lines[1:]}:
            raise ValueError(f"{name}: 脚本 ID 冲突")
        fields = ["0"] * len(reg_lines[1].split("\t"))
        fields[0:3] = [str(SCRIPT_ID), "0", "n_xiaohuang_test"]
        fields[4] = "1"  # Npc_Talk
        (target / "script_func_reg.txt").write_bytes(append_rows(reg, ["\t".join(fields)]))
        # New NPC uses the existing small yellow dog's appearance and neutral NPC AI.
        cha = reader.extract(entries["object/cha_list.txt"])
        cha_rows = cha.decode("gb18030").splitlines()
        if any(row.split("\t")[1:2] == [str(NPC_ID)] for row in cha_rows[1:]):
            raise ValueError(f"{name}: NPC ID 冲突")
        template = next(row for row in cha_rows[1:] if row.split("\t")[1:2] == ["1399"])
        npc_fields = template.split("\t")
        npc_fields[0:2] = ["新葡京娱乐城", str(NPC_ID)]
        (target / "cha_list.txt").write_bytes(append_rows(cha, ["\t".join(npc_fields)]))

        if SPAWN_PATH in entries:
            spawn = reader.extract(entries[SPAWN_PATH])
            spawn_rows = spawn.decode("gb18030").splitlines()
            spawn_id = "4"
            if any(row.split("\t", 1)[0] == spawn_id for row in spawn_rows[1:]):
                raise ValueError(f"{name}: 土地庙刷新 ID 冲突")
            spawn_fields = spawn_rows[1].split("\t")
            spawn_fields[0:3] = [spawn_id, "xiaohuang_test", str(NPC_ID)]
            spawn_fields[7] = "xiaohuang_test"
            spawn_fields[9] = "0"
            spawn_fields[35] = ""
            (target / "xxsc_tudimiao.txt").write_bytes(append_rows(spawn, ["\t".join(spawn_fields)]))
        if TEMP_FLAG_PATH in entries:
            temp = reader.extract(entries[TEMP_FLAG_PATH])
            temp_rows = temp.decode("gb18030").splitlines()
            if any(row.split("\t")[1:2] == ["208"] for row in temp_rows[1:]):
                raise ValueError(f"{name}: 玩家临时标志 208 冲突")
            (target / "temp_dword_player.txt").write_bytes(
                append_rows(temp, ["小黄押大小结算状态\t208\t0\t0\t1\t0"]))
        print(name, "文字", len(lines), "条；脚本 ID", SCRIPT_ID)


if __name__ == "__main__":
    for filename, name in (
        ("server_mb_main.dpk", "server_main"),
        ("server_mb_dynamic.dpk", "server_dynamic"),
        ("client_mb_work.dpk", "client"),
    ):
        current = ROOT / "data" / filename
        original = current.with_name(current.name + ".bak.xiaohuang-20261106")
        build_package(original if original.exists() else current, name)
