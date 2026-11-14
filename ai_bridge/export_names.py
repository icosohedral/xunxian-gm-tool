#!/usr/bin/env python3
"""从客户端 mb.dpk 导出实时窗口要用的名称表，写成 names.json。

用法（在仓库根目录，用项目的 .venv，需要 pycryptodome）：
    .venv/bin/python ai_bridge/export_names.py [mb.dpk 路径] [输出路径]

导出内容：
    skills   技能 ID → 名称   skill/skill_data_*.txt（第 2 列 ID，第 1 列名称）
    chars    角色 / 怪物 / NPC 表 ID → 名称   object/cha_list.txt
    states   状态 ID → 名称   skill/state_base.txt、skill/state_data.txt
    scenes   场景 ID → 名称   scn/area_info.txt（第 17 列场景 ID，可能用 * 分隔多个）
    items    物品 ID → 名称   item/item_list.txt
    stills   场景物件 ID → 名称   object/still_list.txt（鸟窝、凝香草等采集点和可交互物件）
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from dpk_reader import extract_dpk_entry  # noqa: E402

DEFAULT_DPK = ROOT.parent / "xunxian_standalone/client/MhXunxian/mb.dpk"
DEFAULT_OUT = Path(__file__).resolve().parent / "viewer/names.json"

SKILL_TABLES = ["cmn", "yx", "fs", "fzs", "ls", "npc", "npc_1", "npc_2", "npc_3", "npc_4", "npc_pet"]


def rows(archive, path):
    try:
        data = extract_dpk_entry(archive, path)
    except FileNotFoundError:
        return []
    return [line.split("\t") for line in data.decode("gb18030", "replace").splitlines()]


def name_by_id(table_rows, id_col, name_col, target):
    for cols in table_rows:
        if len(cols) <= max(id_col, name_col):
            continue
        key, name = cols[id_col].strip(), cols[name_col].strip()
        if key.isdigit() and name and key not in target:
            target[key] = name


def main():
    dpk = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DPK
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    archive = dpk.read_bytes()

    names = {"skills": {}, "chars": {}, "states": {}, "scenes": {}, "items": {}, "stills": {}}
    for group in SKILL_TABLES:
        name_by_id(rows(archive, f"skill/skill_data_{group}.txt"), 1, 0, names["skills"])
    name_by_id(rows(archive, "object/cha_list.txt"), 1, 0, names["chars"])
    name_by_id(rows(archive, "skill/state_base.txt"), 1, 0, names["states"])
    name_by_id(rows(archive, "skill/state_data.txt"), 1, 0, names["states"])
    name_by_id(rows(archive, "item/item_list.txt"), 0, 1, names["items"])
    name_by_id(rows(archive, "object/still_list.txt"), 1, 0, names["stills"])
    for cols in rows(archive, "scn/area_info.txt"):
        if len(cols) > 16 and cols[0].strip():
            for scene_id in cols[16].split("*"):
                if scene_id.strip().isdigit():
                    names["scenes"].setdefault(scene_id.strip(), cols[0].strip())

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(names, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"已导出 {out}：" + "，".join(f"{k} {len(v)} 条" for k, v in names.items()))


if __name__ == "__main__":
    main()
