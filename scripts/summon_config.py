"""Summoned creature catalog and DPK patching for the custom Qimen summon."""

from __future__ import annotations

import csv
import hashlib
import tempfile
from pathlib import Path

from .repack import replace_resource
from .unpack import DpkReader


SUMMON_SKILL_ID = 3126
SUMMON_TRIGGER_ID = "302247"
SUMMON_TEMPLATE_SKILL_ID = "3046"
SUMMON_LEARN_TEMPLATE_ID = SUMMON_TEMPLATE_SKILL_ID
SUMMON_ICON_SOURCE_SKILL_ID = "3027"
SUMMON_METHOD_ID_MAP = {"30174": "30422", "30173": "30423", "30063": "30424"}
NPC_SKILL_DIR = "skill/skill_data_npc"
NPC_SKILL_CHOOSE = "object/cha_skill_choose.txt"
CHA_LIST = "object/cha_list.txt"
CHA_FIGHT = "object/cha_fight.txt"
SUMMON_SKILL = "summon/summon_skill.txt"
SUMMON_SKILL_INDEX = "summon/summon_skill_index.txt"
SKILL_DATA = "skill/skill_data_yx.txt"
SKILL_METHOD_DATA = "skill/method_data_yx.txt"
SKILL_LEARN = "skill/skill_learn.txt"
SKILL_LEVELUP = "skill/skill_levelup.txt"
SKILL_TRIGGER = "skill/trigger_yx.txt"
NPC_TEACH = "skill/npc_teach_skill.txt"
CAREER_SKILLS = "skill/skill_cha_yx.txt"
SKILL_CHOOSE_FIELDS = (5, 9, 13, 17, 21)
IMMORTAL_FRIENDS = [
    ("罗刹导师真身", 37643, 11568, 1),
    ("金刚导师真身", 37644, 11567, 2),
    ("驭剑导师真身", 37645, 11569, 3),
    ("奇门导师真身", 37646, 11570, 4),
    ("控火导师真身", 37647, 11573, 5),
    ("掌电导师真身", 37648, 11572, 6),
    ("幽冥导师真身", 37649, 11576, 8),
    ("天君导师真身", 37650, 11575, 7),
    ("乘风导师真身", 37651, 11574, 9),
    ("乾坤导师真身", 37652, 11571, 10),
]


def load_friend_catalog(client_path: Path | None) -> list[dict]:
    """Build the selectable仙友 list and their profession skills from mb.dpk."""
    skills_by_profession: dict[int, list[dict]] = {}
    if client_path is not None and client_path.is_file():
        with DpkReader(client_path) as reader:
            entries = {entry.path.lower(): entry for entry in reader.read_entries()}
            learn_entry = entries.get(SKILL_LEARN)
            if learn_entry:
                lines, _ = _decode(reader.extract(learn_entry), SKILL_LEARN)
                for line in lines:
                    fields = line.rstrip("\r\n").split("\t")
                    if len(fields) < 5 or not fields[1].isdigit() or not fields[4].isdigit():
                        continue
                    profession_id, skill_id = int(fields[4]), int(fields[1])
                    if 1 <= profession_id <= 10 and skill_id != SUMMON_SKILL_ID:
                        skills_by_profession.setdefault(profession_id, []).append(
                            {"id": skill_id, "name": fields[0]})
    return [{"key": f"friend:{role_id}:{form_id}", "summon_type": "friend",
             "role_id": role_id, "form_id": form_id, "profession_id": profession_id,
             "name": name, "skills": skills_by_profession.get(profession_id, []),
             "members": [{"key": f"friend:{role_id}:{form_id}",
                          "role_id": role_id, "form_id": form_id}]}
            for name, role_id, form_id, profession_id in IMMORTAL_FRIENDS]


def load_catalog(csv_path: Path) -> list[dict]:
    if not csv_path.is_file():
        raise ValueError("找不到召唤物列表.csv")
    grouped: dict[tuple[int, int, str], dict] = {}
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                role_id = int(row["角色ID"])
                form_id = int(row["外形ID"])
                skill_id = int(row["技能ID"])
            except (KeyError, TypeError, ValueError):
                continue
            name = (row.get("角色名称") or "").strip()
            skill_name = (row.get("技能名称") or "").strip()
            if not name or not skill_name:
                continue
            key = role_id, form_id, name
            creature = grouped.setdefault(key, {
                "role_id": role_id, "form_id": form_id, "name": name,
                "skills": {}, "instances": set(),
            })
            creature["skills"].setdefault(skill_id, skill_name)
            instance = (row.get("副本/首领") or "").strip()
            if instance:
                creature["instances"].add(instance)
    result = []
    for creature in grouped.values():
        result.append({
            "key": f"{creature['role_id']}:{creature['form_id']}",
            "role_id": creature["role_id"], "form_id": creature["form_id"],
            "name": creature["name"],
            "instances": sorted(creature["instances"]),
            "skills": [{"id": sid, "name": name}
                       for sid, name in sorted(creature["skills"].items())],
            "members": [{"key": f"{creature['role_id']}:{creature['form_id']}",
                         "role_id": creature["role_id"], "form_id": creature["form_id"]}],
        })
    result.sort(key=lambda x: (x["name"], x["form_id"], x["role_id"]))
    merged: dict[tuple, dict] = {}
    for creature in result:
        skill_signature = tuple((item["id"], item["name"]) for item in creature["skills"])
        signature = creature["name"], skill_signature
        current = merged.get(signature)
        if current is None:
            merged[signature] = creature
            continue
        current["members"].extend(creature["members"])
        current["instances"] = sorted(set(current["instances"]) | set(creature["instances"]))
    return sorted(merged.values(), key=lambda x: (x["name"], x["form_id"], x["role_id"]))


def _decode(data: bytes, label: str) -> tuple[list[str], str]:
    for encoding in ("gb18030", "utf-8-sig"):
        try:
            return data.decode(encoding).splitlines(keepends=True), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError(f"{label} 编码无法识别")


def _replace_resource_data(source: Path, output: Path, entry: str, data: bytes) -> None:
    replace_resource(
        source, entry, None, output, line_edit=None, line_edits=None,
        replacement_data=data, compression_level=6, zlib_mem_level=8,
        zlib_strategy="default", auto_zlib_parameters=True,
        allow_chain_shrink=True, allow_chain_growth=True, overwrite_output=False,
        overwrite_report=False, max_input_size=2 * 1024 * 1024 * 1024,
        report_path=None,
    )


def _resources(path: Path) -> tuple[dict[str, str], dict[str, bytes]]:
    with DpkReader(path) as reader:
        all_entries = {e.path.lower(): e for e in reader.read_entries()}
        selected = {name for name in all_entries if name in {
            SKILL_DATA, SKILL_METHOD_DATA, SKILL_LEARN, SKILL_LEVELUP,
            SKILL_TRIGGER, NPC_TEACH, CAREER_SKILLS,
            NPC_SKILL_CHOOSE, CHA_LIST, CHA_FIGHT, SUMMON_SKILL, SUMMON_SKILL_INDEX,
        }}
        selected.update(name for name in all_entries
                        if name.startswith(NPC_SKILL_DIR) and name.endswith(".txt"))
        selected.update(name for name in all_entries if name.startswith("skill/skill_data_")
                        and name.endswith(".txt") and not name.startswith(NPC_SKILL_DIR))
        entries = {name: all_entries[name].path for name in selected}
        data = {name: reader.extract(all_entries[name]) for name in selected}
    return entries, data


def _patch_row(data: bytes, label: str, key_column: int, key: str,
               changes: dict[int, str], *, required: bool = True) -> tuple[bytes, int]:
    lines, encoding = _decode(data, label)
    found = 0
    updated = []
    for line in lines:
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        body = line[:-len(ending)] if ending else line
        fields = body.split("\t")
        if len(fields) > key_column and fields[key_column] == key:
            if found:
                raise ValueError(f"{label} 中 {key} 重复，无法安全修改")
            for index, value in changes.items():
                while len(fields) <= index:
                    fields.append("")
                fields[index] = value
            body = "\t".join(fields)
            found += 1
        updated.append(body + ending)
    if required and not found:
        raise ValueError(f"{label} 中找不到 ID {key}")
    try:
        return "".join(updated).encode(encoding), found
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} 无法保存修改后的中文文本") from exc


def _ensure_skill_list(data: bytes, label: str, row_name: str, skill_id: int) -> bytes:
    lines, encoding = _decode(data, label)
    matches = []
    for index, line in enumerate(lines):
        fields = line.rstrip("\r\n").split("\t")
        if fields and fields[0] == row_name:
            matches.append((index, fields))
    if len(matches) != 1:
        raise ValueError(f"{label} 中“{row_name}”匹配到 {len(matches)} 行")
    index, fields = matches[0]
    while len(fields) <= 3:
        fields.append("")
    ids = [item for item in fields[3].split("*") if item]
    token = str(skill_id)
    if token not in ids:
        fields[3] = "*".join([*ids, token])
        line = lines[index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        lines[index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _patch_learn(data: bytes, skill_name: str) -> bytes:
    lines, encoding = _decode(data, SKILL_LEARN)
    target_index = None
    template = None
    for i, line in enumerate(lines):
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) >= 7 and fields[1] == str(SUMMON_SKILL_ID):
            if target_index is not None:
                raise ValueError("skill_learn.txt 中召唤技能 ID 重复")
            target_index = i
        if len(fields) >= 7 and fields[1] == SUMMON_LEARN_TEMPLATE_ID:
            if template is not None:
                raise ValueError("skill_learn.txt 中六合签模板 ID 重复")
            template = fields
    if template is None:
        raise ValueError("skill_learn.txt 找不到六合签学习模板")
    fields = list(template)
    fields[0] = skill_name
    fields[1] = str(SUMMON_SKILL_ID)
    if target_index is None:
        ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
        lines.append("\t".join(fields) + ending)
    else:
        line = lines[target_index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        lines[target_index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _patch_levelup(data: bytes, skill_name: str, *, required: bool = True) -> bytes:
    lines, encoding = _decode(data, SKILL_LEVELUP)
    template = None
    target_index = None
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        body = line[:-len(ending)] if ending else line
        fields = body.split("\t")
        if len(fields) < 2:
            continue
        if fields[1] == SUMMON_TEMPLATE_SKILL_ID:
            if template is not None:
                raise ValueError("skill_levelup.txt 中六合签模板 ID 重复")
            template = fields
        elif fields[1] == str(SUMMON_SKILL_ID):
            if target_index is not None:
                raise ValueError("skill_levelup.txt 中召唤技能 ID 重复")
            target_index = index
    if template is None:
        if not required:
            return data
        raise ValueError("skill_levelup.txt 找不到六合签升级模板")
    fields = list(template)
    fields[0] = skill_name
    fields[1] = str(SUMMON_SKILL_ID)
    if target_index is None:
        ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
        lines.append("\t".join(fields) + ending)
    else:
        line = lines[target_index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        lines[target_index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _patch_skill_template(data: bytes, skill_name: str, description: str,
                          cooldown: int, *, required: bool = True) -> bytes:
    lines, encoding = _decode(data, SKILL_DATA)
    template = None
    template_index = None
    target_index = None
    icon_index = None
    icon = None
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        body = line[:-len(ending)] if ending else line
        fields = body.split("\t")
        if len(fields) <= 1:
            continue
        if fields[1] == SUMMON_TEMPLATE_SKILL_ID:
            if template is not None:
                raise ValueError("skill_data_yx.txt 中六合签模板 ID 重复")
            template = fields
            template_index = index
        elif fields[1] == SUMMON_ICON_SOURCE_SKILL_ID:
            if icon_index is not None:
                raise ValueError("skill_data_yx.txt 中巨甲遁图标来源 ID 重复")
            icon_index = index
            if len(fields) > 3:
                icon = fields[3]
        elif fields[1] == str(SUMMON_SKILL_ID):
            if target_index is not None:
                raise ValueError("skill_data_yx.txt 中新召唤技能 ID 重复")
            target_index = index
    if template is None:
        if not required:
            return data
        raise ValueError("skill_data_yx.txt 找不到六合签模板")
    if icon_index is None:
        if not required:
            return data
        raise ValueError("skill_data_yx.txt 找不到巨甲遁图标来源")
    fields = list(template)
    fields[0] = skill_name
    fields[1] = str(SUMMON_SKILL_ID)
    fields[2] = "*".join(SUMMON_METHOD_ID_MAP.get(ref, ref) for ref in fields[2].split("*"))
    if icon is not None:
        fields[3] = icon
    fields[4] = description
    fields[21] = str(cooldown)
    if target_index is None:
        source_line = lines[icon_index]
        ending = "\r\n" if source_line.endswith("\r\n") else "\n" if source_line.endswith("\n") else "\r" if source_line.endswith("\r") else ""
        lines.insert(icon_index + 1, "\t".join(fields) + ending)
    else:
        line = lines[target_index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        lines[target_index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _patch_method_template(data: bytes, *, required: bool = True) -> bytes:
    lines, encoding = _decode(data, SKILL_METHOD_DATA)
    template_rows = []
    target_indexes = []
    for index, line in enumerate(lines):
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        body = line[:-len(ending)] if ending else line
        fields = body.split("\t")
        if not fields:
            continue
        if fields[0] == SUMMON_TEMPLATE_SKILL_ID:
            template_rows.append((fields, ending))
        elif fields[0] == str(SUMMON_SKILL_ID):
            target_indexes.append(index)
    if not template_rows:
        if not required:
            return data
        raise ValueError("method_data_yx.txt 找不到六合签技能动作模板")
    if not target_indexes and not required:
        return data
    if len({row[0][1] for row in template_rows if len(row[0]) > 1}) != len(template_rows):
        raise ValueError("method_data_yx.txt 中六合签模板动作 ID 重复")
    template_ids = {row[0][1] for row in template_rows if len(row[0]) > 1}
    if set(SUMMON_METHOD_ID_MAP) != template_ids:
        raise ValueError("method_data_yx.txt 的六合签动作 ID 与预设映射不一致")
    occupied = {fields[1] for index, line in enumerate(lines)
                if index not in target_indexes
                for fields in [line.rstrip("\r\n").split("\t")]
                if len(fields) > 1}
    if any(new_id in occupied for new_id in SUMMON_METHOD_ID_MAP.values()):
        raise ValueError("新召唤技能动作 ID 已被占用，拒绝覆盖现有技能")
    cloned = []
    for template_fields, ending in template_rows:
        fields = list(template_fields)
        fields[0] = str(SUMMON_SKILL_ID)
        fields[1] = SUMMON_METHOD_ID_MAP[fields[1]]
        if len(fields) > 2:
            refs = fields[2].split("*")
            fields[2] = "*".join(SUMMON_TRIGGER_ID if ref == "300498" else ref for ref in refs)
        cloned.append("\t".join(fields) + ending)
    first_index = min(target_indexes) if target_indexes else len(lines)
    target_indexes_set = set(target_indexes)
    result = [line for index, line in enumerate(lines) if index not in target_indexes_set]
    insertion_index = sum(1 for index in range(first_index) if index not in target_indexes_set)
    result[insertion_index:insertion_index] = cloned
    return "".join(result).encode(encoding)


def _patch_trigger_template(data: bytes, role_id: int, *, required: bool = True) -> bytes:
    lines, encoding = _decode(data, SKILL_TRIGGER)
    template = None
    target_index = None
    for index, line in enumerate(lines):
        fields = line.rstrip("\r\n").split("\t")
        if not fields:
            continue
        if fields[0] == "300498":
            if template is not None:
                raise ValueError("trigger_yx.txt 中六合签召唤触发 ID 重复")
            template = fields
        elif fields[0] == SUMMON_TRIGGER_ID:
            if target_index is not None:
                raise ValueError("trigger_yx.txt 中新召唤触发 ID 重复")
            target_index = index
    if template is None:
        if not required:
            return data
        raise ValueError("trigger_yx.txt 找不到六合签召唤触发模板")
    fields = list(template)
    fields[0] = SUMMON_TRIGGER_ID
    fields[5] = str(role_id)
    if target_index is None:
        ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
        lines.append("\t".join(fields) + ending)
    else:
        line = lines[target_index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        lines[target_index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _restore_keyed_rows(current: bytes, reference: bytes, label: str,
                        key_column: int, key: str, *, multiple: bool = False,
                        required: bool = True) -> bytes:
    current_lines, encoding = _decode(current, label)
    reference_lines, reference_encoding = _decode(reference, label)
    if reference_encoding != encoding:
        raise ValueError(f"{label} 的基准包与目标包编码不一致")

    def matches(lines: list[str]) -> list[tuple[int, str, str]]:
        result = []
        for index, line in enumerate(lines):
            ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
            body = line[:-len(ending)] if ending else line
            fields = body.split("\t")
            if len(fields) > key_column and fields[key_column] == key:
                result.append((index, body, ending))
        return result

    current_rows = matches(current_lines)
    reference_rows = matches(reference_lines)
    if not reference_rows:
        if required:
            raise ValueError(f"基准包 {label} 中找不到原始 ID {key}")
        return current
    if not multiple and (len(current_rows) > 1 or len(reference_rows) != 1):
        raise ValueError(f"{label} 中 ID {key} 行数异常，无法恢复")
    if not current_rows and not required:
        return current
    first_index = current_rows[0][0] if current_rows else len(current_lines)
    current_set = {row[0] for row in current_rows}
    result = [line for index, line in enumerate(current_lines) if index not in current_set]
    insert_at = sum(1 for index in range(first_index) if index not in current_set)
    replacement = [body + ending for _, body, ending in reference_rows]
    result[insert_at:insert_at] = replacement
    return "".join(result).encode(encoding)


def _patch_skill_choose(data: bytes, form_id: int, skill_ids: list[int], *, required: bool,
                       row_name: str = "自定义召唤物") -> bytes:
    lines, encoding = _decode(data, NPC_SKILL_CHOOSE)
    matches = []
    for index, line in enumerate(lines):
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) > 1 and fields[1] == str(form_id):
            matches.append((index, fields))
    if not matches:
        if required:
            fields = [""] * 46
            fields[0], fields[1] = row_name, str(form_id)
            for slot, skill_id in enumerate(skill_ids[:len(SKILL_CHOOSE_FIELDS)]):
                fields[SKILL_CHOOSE_FIELDS[slot]] = f"100*{skill_id}"
            ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
            lines.append("\t".join(fields) + ending)
            return "".join(lines).encode(encoding)
        return data
    if len(matches) != 1:
        raise ValueError(f"cha_skill_choose.txt 中外形 {form_id} 重复")
    index, fields = matches[0]
    while len(fields) <= max(SKILL_CHOOSE_FIELDS):
        fields.append("")
    for slot, field in enumerate(SKILL_CHOOSE_FIELDS):
        fields[field] = f"100*{skill_ids[slot]}" if slot < len(skill_ids) else ""
    line = lines[index]
    ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
    lines[index] = "\t".join(fields) + ending
    return "".join(lines).encode(encoding)


def _summon_type_for_role(data: bytes, role_id: int, form_id: int,
                          *, required: bool = True) -> str | None:
    lines, _ = _decode(data, CHA_LIST)
    matches = []
    for line in lines:
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) > 5 and fields[1] == str(role_id) and fields[5] == str(form_id):
            matches.append(fields[4])
    if len(matches) != 1 or not matches[0]:
        if not required:
            return None
        raise ValueError(f"cha_list.txt 中角色 {role_id} 的召唤物类型不唯一或为空")
    return matches[0]


def _ensure_friend_cha_list(data: bytes, creature: dict,
                            reference_data: bytes | None = None) -> bytes:
    """Copy the selected mentor's character row into a package that lacks it."""
    lines, encoding = _decode(data, CHA_LIST)
    role_id, form_id = int(creature["role_id"]), int(creature["form_id"])
    for line in lines:
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) > 5 and fields[1] == str(role_id) and fields[5] == str(form_id):
            return data
    if reference_data is None:
        raise ValueError(f"cha_list.txt 找不到导师真身 {role_id} 的角色记录")
    reference_lines, _ = _decode(reference_data, CHA_LIST)
    matches = [line for line in reference_lines
               if len(fields := line.rstrip("\r\n").split("\t")) > 5
               and fields[1] == str(role_id) and fields[5] == str(form_id)]
    if len(matches) != 1:
        raise ValueError(f"客户端 cha_list.txt 中导师真身 {role_id} 的记录不唯一或缺失")
    target_header = next((row for line in lines
                          if (row := line.rstrip("\r\n").split("\t"))[1:2] == ["ID"]), None)
    reference_header = next((row for line in reference_lines
                             if (row := line.rstrip("\r\n").split("\t"))[1:2] == ["ID"]), None)
    if (target_header and reference_header and
            target_header[1:] != reference_header[1:len(target_header)]):
        raise ValueError("cha_list.txt 与客户端导师真身记录列结构不一致")
    widths = {len(line.rstrip("\r\n").split("\t")) for line in lines
              if len(line.rstrip("\r\n").split("\t")) > 5}
    if len(widths) != 1:
        raise ValueError("cha_list.txt 角色行列数不一致")
    target_width = widths.pop()
    fields = matches[0].rstrip("\r\n").split("\t")
    if len(fields) < target_width:
        raise ValueError("cha_list.txt 仙友模板列数不足")
    fields = fields[:target_width]
    ending = "\r\n" if lines and lines[-1].endswith("\r\n") else "\n"
    lines.append("\t".join(fields) + ending)
    return "".join(lines).encode(encoding)


def _ensure_friend_npc_skills(resources: dict[str, bytes], skills: list[dict],
                              reference_resources: dict[str, bytes] | None = None) -> None:
    """Expose selected player profession skills through the NPC skill table."""
    npc_paths = sorted(path for path in resources
                       if path.startswith(NPC_SKILL_DIR) and path.endswith(".txt")
                       and not path.endswith("_pet.txt"))
    source_paths = sorted(path for path in resources
                          if path.startswith("skill/skill_data_")
                          and not path.startswith(NPC_SKILL_DIR) and not path.endswith("_pet.txt"))
    if not npc_paths:
        raise ValueError("当前包缺少可写入仙友职业技能的技能定义表")
    existing: set[int] = set()
    for path in npc_paths:
        lines, _ = _decode(resources[path], path)
        for line in lines:
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) > 1 and fields[1].isdigit(): existing.add(int(fields[1]))
    wanted = {int(item["id"]) for item in skills} - existing
    if not wanted: return
    definitions: dict[int, str] = {}
    source_encoding = None
    for source in (resources, reference_resources or {}):
        paths = source_paths if source is resources else sorted(
            path for path in source if path.startswith("skill/skill_data_")
            and not path.startswith(NPC_SKILL_DIR) and not path.endswith("_pet.txt"))
        remaining = wanted - definitions.keys()
        if not remaining:
            break
        for path in paths:
            lines, encoding = _decode(source[path], path)
            source_encoding = source_encoding or encoding
            for line in lines:
                fields = line.rstrip("\r\n").split("\t")
                if len(fields) > 1 and fields[1].isdigit() and int(fields[1]) in remaining:
                    sid = int(fields[1])
                    if sid in definitions:
                        raise ValueError(f"职业技能定义 ID {sid} 重复，无法加入仙友技能表")
                    definitions[sid] = line
    missing = wanted - definitions.keys()
    if missing: raise ValueError("职业技能定义表缺少技能：" + ", ".join(map(str, sorted(missing))))
    target = npc_paths[0]
    target_lines, target_encoding = _decode(resources[target], target)
    if target_encoding != source_encoding: raise ValueError("仙友技能表编码与职业技能表不一致")
    widths = {len(line.rstrip("\r\n").split("\t")) for line in target_lines if "\t" in line}
    if len(widths) != 1:
        raise ValueError("仙友技能表列数不一致")
    target_width = widths.pop()
    ending = "\r\n" if target_lines and target_lines[-1].endswith("\r\n") else "\n"
    for sid in sorted(wanted):
        fields = definitions[sid].rstrip("\r\n").split("\t")
        if len(fields) < target_width:
            raise ValueError(f"职业技能 {sid} 定义列数不足")
        target_lines.append("\t".join(fields[:target_width]) + ending)
    resources[target] = "".join(target_lines).encode(target_encoding)


def _patch_summon_skill_bar(skill_data: dict[str, bytes], creature_type: str,
                            skills: list[dict], *, required: bool) -> dict[str, bytes]:
    bar_data = skill_data.get(SUMMON_SKILL.lower())
    index_data = skill_data.get(SUMMON_SKILL_INDEX.lower())
    if bar_data is None or index_data is None:
        if required:
            raise ValueError("当前客户端包缺少召唤物技能栏配置表")
        return {}

    skill_sources: dict[int, tuple[str, str]] = {}
    for path, data in skill_data.items():
        if not path.startswith(NPC_SKILL_DIR) or not path.endswith(".txt"):
            continue
        lines, _ = _decode(data, path)
        for line in lines:
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) > 3 and fields[1].isdigit():
                sid = int(fields[1])
                if sid in {int(item["id"]) for item in skills}:
                    skill_sources[sid] = (fields[0], fields[3])
    missing = sorted({int(item["id"]) for item in skills} - skill_sources.keys())
    if missing:
        if required:
            raise ValueError("召唤物技能栏找不到技能定义：" + ", ".join(map(str, missing)))
        return {}

    index_lines, index_encoding = _decode(index_data, SUMMON_SKILL_INDEX)
    index_rows: dict[int, tuple[int, list[str]]] = {}
    for i, line in enumerate(index_lines):
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) > 1 and fields[1].isdigit():
            sid = int(fields[1])
            if sid in index_rows:
                raise ValueError(f"summon_skill_index.txt 中技能 {sid} 重复")
            index_rows[sid] = (i, fields)
    for item in skills:
        sid = int(item["id"])
        label, icon = skill_sources[sid]
        row = index_rows.get(sid)
        if row:
            _, fields = row
            if len(fields) < 3 or fields[0] != label or fields[2] != icon:
                raise ValueError(f"技能栏 ID {sid} 已被其他召唤物占用")
            continue
        ending = "\r\n" if index_lines and index_lines[-1].endswith("\r\n") else "\n"
        index_lines.append("\t".join((label, str(sid), icon, "2")) + ending)
    patched_index = "".join(index_lines).encode(index_encoding)

    bar_lines, bar_encoding = _decode(bar_data, SUMMON_SKILL)
    matches = []
    for i, line in enumerate(bar_lines):
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) > 1 and fields[1] == str(creature_type):
            matches.append((i, fields))
    if len(matches) > 1:
        raise ValueError(f"summon_skill.txt 中召唤物类型 {creature_type} 重复")
    first_skill = int(skills[0]["id"])
    other_skills = "*".join(str(int(item["id"])) for item in skills[1:])
    row_name = "自定义召唤物" + str(creature_type)
    if matches:
        index, fields = matches[0]
        fields[0] = row_name
        while len(fields) <= 3:
            fields.append("")
        fields[2] = str(first_skill)
        fields[3] = other_skills
        line = bar_lines[index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else "\r" if line.endswith("\r") else ""
        bar_lines[index] = "\t".join(fields) + ending
    else:
        ending = "\r\n" if bar_lines and bar_lines[-1].endswith("\r\n") else "\n"
        bar_lines.append("\t".join((row_name, creature_type, str(first_skill), other_skills)) + ending)
    return {
        SUMMON_SKILL.lower(): "".join(bar_lines).encode(bar_encoding),
        SUMMON_SKILL_INDEX.lower(): patched_index,
    }


def patch_summon_dpk(source: Path, output: Path, config: dict,
                     *, optional_resources: bool = False,
                     catalog: list[dict] | None = None,
                     reference: Path | None = None) -> tuple[bytes, dict]:
    role_id = int(config["role_id"])
    form_id = int(config["form_id"])
    name = str(config["skill_name"]).strip()
    summon_cd = int(config.get("summon_cooldown", 120))
    selected = config.get("skills") or []
    if not name or len(name) > 32 or any(c in name for c in "\t\r\n"):
        raise ValueError("召唤技能名称需为 1 到 32 个字符")
    if not 1 <= summon_cd <= 3600:
        raise ValueError("召唤技能冷却时间需为 1 到 3600 秒")
    if not 1 <= len(selected) <= 5:
        raise ValueError("召唤物至少选择 1 个技能，最多 5 个")
    skill_cds: dict[int, int] = {}
    for item in selected:
        sid, cd = int(item["id"]), int(item["cooldown"])
        if sid in skill_cds:
            raise ValueError("召唤物技能不能重复")
        if not 1 <= cd <= 3600:
            raise ValueError(f"技能 {sid} 的冷却时间需为 1 到 3600 秒")
        skill_cds[sid] = cd

    entries, resources = _resources(source)
    reference_resources: dict[str, bytes] = {}
    if reference is not None:
        _, reference_resources = _resources(reference)
        for path, key_column, key, multiple in (
            (SKILL_DATA, 1, SUMMON_ICON_SOURCE_SKILL_ID, False),
            (SKILL_METHOD_DATA, 0, SUMMON_ICON_SOURCE_SKILL_ID, True),
            (SKILL_TRIGGER, 0, "300194", False),
        ):
            lower = path.lower()
            if lower not in resources or lower not in reference_resources:
                if optional_resources:
                    continue
                raise ValueError(f"当前包或客户端基准包缺少 {path}")
            resources[lower] = _restore_keyed_rows(
                resources[lower], reference_resources[lower], path,
                key_column, str(key), multiple=multiple,
                required=not optional_resources)
    catalog = catalog or load_catalog(Path(__file__).resolve().parent / "doc" / "召唤物列表.csv")
    creature = next((c for c in catalog if any(
        member["role_id"] == role_id and member["form_id"] == form_id
        for member in c.get("members", [{"role_id": c["role_id"], "form_id": c["form_id"]}])
    )), None)
    if not creature:
        raise ValueError("召唤物不在允许列表中")
    allowed = {item["id"] for item in creature["skills"]}
    if set(skill_cds) - allowed:
        raise ValueError("所选技能不属于当前召唤物")

    changes: dict[str, bytes] = {}

    def get(path: str) -> bytes | None:
        return resources.get(path.lower())

    if config.get("summon_type") == "friend":
        _ensure_friend_npc_skills(resources, selected, reference_resources)

    summon_desc = f"召唤出{creature['name']}，协助你战斗，持续十分钟"
    skill_data = get(SKILL_DATA)
    if skill_data is not None:
        changes[SKILL_DATA.lower()] = _patch_skill_template(
            skill_data, name, summon_desc, summon_cd,
            required=not optional_resources)
    elif not optional_resources:
        raise ValueError("当前包缺少游侠技能定义表")

    method_data = get(SKILL_METHOD_DATA)
    if method_data is not None:
        changes[SKILL_METHOD_DATA.lower()] = _patch_method_template(
            method_data, required=not optional_resources)
    elif not optional_resources:
        raise ValueError("当前包缺少游侠技能动作表")

    trigger = get(SKILL_TRIGGER)
    if trigger is not None:
        # Use a unique copy of the 六合签 summon trigger so neither 巨甲遁's
        # original trigger nor 六合签's trigger is mutated.
        changes[SKILL_TRIGGER.lower()] = _patch_trigger_template(
            trigger, role_id, required=not optional_resources)
    elif not optional_resources:
        raise ValueError("当前包缺少游侠技能触发表")

    learn = get(SKILL_LEARN)
    if learn is not None:
        if optional_resources and str(SUMMON_SKILL_ID).encode("ascii") not in learn:
            try:
                changes[SKILL_LEARN.lower()] = _patch_learn(learn, name)
            except ValueError:
                pass
        else:
            changes[SKILL_LEARN.lower()] = _patch_learn(learn, name)
    elif not optional_resources:
        raise ValueError("当前包缺少技能学习表")

    levelup = get(SKILL_LEVELUP)
    if levelup is not None:
        changes[SKILL_LEVELUP.lower()] = _patch_levelup(
            levelup, name, required=not optional_resources)
    elif not optional_resources:
        raise ValueError("当前包缺少技能升级表")

    teach = get(NPC_TEACH)
    career = get(CAREER_SKILLS)
    if teach is not None:
        changes[NPC_TEACH.lower()] = _ensure_skill_list(teach, NPC_TEACH, "游侠导师", SUMMON_SKILL_ID)
    elif not optional_resources:
        raise ValueError("当前包缺少游侠导师技能表")
    if career is not None:
        changes[CAREER_SKILLS.lower()] = _ensure_skill_list(career, CAREER_SKILLS, "游侠", SUMMON_SKILL_ID)
    elif not optional_resources:
        raise ValueError("当前包缺少游侠职业技能表")

    choose = get(NPC_SKILL_CHOOSE)
    if choose is not None:
        patched_choose = choose
        member_forms = sorted({member["form_id"] for member in creature.get(
            "members", [{"form_id": form_id}])})
        for member_form in member_forms:
            patched_choose = _patch_skill_choose(
                patched_choose, member_form, list(skill_cds),
                required=(not optional_resources or config.get("summon_type") == "friend"),
                row_name=creature["name"] if config.get("summon_type") == "friend" else "自定义召唤物")
        changes[NPC_SKILL_CHOOSE.lower()] = patched_choose
    elif not optional_resources:
        raise ValueError("当前包缺少召唤物技能选择表")

    cha_list = get(CHA_LIST)
    if cha_list is not None:
        if config.get("summon_type") == "friend":
            patched_cha_list = _ensure_friend_cha_list(
                cha_list, creature, reference_resources.get(CHA_LIST.lower()))
            if patched_cha_list != cha_list:
                changes[CHA_LIST.lower()] = patched_cha_list
            cha_list = patched_cha_list
        summon_type = _summon_type_for_role(cha_list, role_id, form_id,
            required=(not optional_resources or config.get("summon_type") == "friend"))
        if config.get("summon_type") == "friend" and summon_type:
            fight = get(CHA_FIGHT)
            if fight is None:
                raise ValueError("当前包缺少导师真身战斗属性表")
            fight_lines, _ = _decode(fight, CHA_FIGHT)
            ability_rows = [line for line in fight_lines
                            if line.rstrip("\r\n").split("\t")[0] == summon_type]
            if len(ability_rows) != 1:
                raise ValueError(f"cha_fight.txt 中战斗能力 ID {summon_type} 不唯一或缺失")
        if summon_type:
            changes.update(_patch_summon_skill_bar(resources, summon_type, selected,
                                                    required=not optional_resources))
    elif not optional_resources:
        raise ValueError("当前包缺少召唤物角色表")

    skill_matches = {sid: 0 for sid in skill_cds}
    for path, data in resources.items():
        if not path.startswith(NPC_SKILL_DIR) or not path.endswith(".txt"):
            continue
        current_data = data
        for sid, cd in skill_cds.items():
            patched, count = _patch_row(current_data, path, 1, str(sid), {21: str(cd)}, required=False)
            if count:
                changes[path] = patched
                current_data = patched
                skill_matches[sid] += count
    missing = [sid for sid, count in skill_matches.items() if not count]
    if missing and not optional_resources:
        raise ValueError("当前包中找不到召唤物技能：" + ", ".join(map(str, missing)))

    if not changes:
        return source.read_bytes(), {"sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "patched": []}
    output.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="summon-config-", dir=output.parent))
    current = source
    try:
        for index, (lower_path, data) in enumerate(changes.items()):
            entry_path = entries.get(lower_path)
            if not entry_path:
                raise ValueError(f"DPK 中找不到资源 {lower_path}")
            next_path = work / f"step-{index}.dpk"
            _replace_resource_data(current, next_path, entry_path, data)
            current = next_path
        output.write_bytes(current.read_bytes())
        with DpkReader(output) as reader:
            final_entries = {e.path.lower(): e for e in reader.read_entries()}
            for path, expected in changes.items():
                actual = reader.extract(final_entries[path])
                if actual != expected:
                    raise ValueError(f"DPK 深度校验失败：{path}")
        return output.read_bytes(), {
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "patched": sorted(changes), "missing_optional_skills": missing,
        }
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
