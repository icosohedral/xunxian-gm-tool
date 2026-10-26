"""Add a custom weapon row to item tables in an mb.dpk archive."""

from __future__ import annotations

from pathlib import Path

from .repack import replace_resource
from .unpack import DpkReader

ITEM_LISTS = ("item/item_list.txt", "item/item_list2.txt", "item/item_list3.txt")
def inspect_item_tables(archive: Path) -> dict:
    result = {}
    with DpkReader(archive) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        for name in ITEM_LISTS:
            entry = entries.get(name)
            if not entry:
                continue
            lines = reader.extract(entry).decode("gb18030").splitlines()
            if not lines:
                raise ValueError(f"{name} 是空表")
            result[name] = {"header": lines[0].split("\t"), "lines": lines[1:]}
    return result


def all_item_ids(archive: Path) -> set[int]:
    ids = set()
    for table in inspect_item_tables(archive).values():
        for line in table["lines"]:
            fields = line.split("\t")
            if fields and fields[0].isdigit():
                ids.add(int(fields[0]))
    return ids


def _template_record(archive: Path, template_id: int, fallback_fields: list[str] | None = None) -> tuple[str, list[str], list[str]]:
    matches = []
    for resource, table in inspect_item_tables(archive).items():
        for line in table["lines"]:
            fields = line.split("\t")
            if fields and fields[0].isdigit() and int(fields[0]) == template_id:
                matches.append((resource, table["header"], fields))
    if not matches and fallback_fields is not None:
        table = inspect_item_tables(archive).get(ITEM_LISTS[0])
        if table:
            header = table["header"]
            canonical = client_header_names()
            mapping = dict(zip(canonical, fallback_fields))
            mapping["天赋系限制"] = mapping.get("天赋限制", "")
            mapping["不可买卖"] = mapping.get("不可买卖（1：不可买卖 2：不可卖店）", "")
            mapping["绑定拍卖"] = mapping.get("绑定拍卖（1-可拍卖，2-不可拍卖，0-根据绑定方式决定）", "")
            return ITEM_LISTS[0], header, [mapping.get(name, "") for name in header]
    if len(matches) != 1:
        raise ValueError(f"物品 ID {template_id} 在 {archive.name} 中匹配到 {len(matches)} 条记录")
    return matches[0]


def client_header_names() -> list[str]:
    return [
        "物品ID", "物品名称", "物品类", "物品掉落所属类", "道具图片", "模型ID", "文字介绍ID",
        "物品品质", "法宝阶数", "职业限制", "天赋限制", "等级限制", "技能限制", "性别限制",
        "唯一", "流通金钱", "购买价格", "售出价格", "耐久", "伤害力", "防御力", "体魄", "力量",
        "筋骨", "元神", "装备属性1", "装备属性2", "装备属性3", "装备属性4", "装备属性5",
        "使用属性", "书籍特性", "套装ID", "生活技能限制", "重叠数量", "永不磨损", "废弃",
        "不可买卖（1：不可买卖 2：不可卖店）", "不可寄存", "在线存在时间", "现实存在时间",
        "下线存在时间", "绑定方式", "废弃", "服务器标志位", "服务器数量限制", "法宝天赋树ID",
        "法宝天赋增加值", "法宝增强ID", "速率限制（秒）", "速率限制（个）",
        "绑定拍卖（1-可拍卖，2-不可拍卖，0-根据绑定方式决定）", "获取公告（不填为不公告，1为区域，2为一线，3为跨服）",
        "法术抗性", "装备唯一",
    ]


def append_weapon(archive: Path, output: Path, template_id: int,
                  weapon_id: int, client_fields: list[str], fallback_fields: list[str] | None = None) -> dict:
    """Copy an existing weapon row, overlay common fields by header, and append."""
    resource, header, template = _template_record(archive, template_id, fallback_fields)
    client_header_count = len(client_fields)
    if client_header_count != 55:
        raise ValueError("武器草稿字段数异常，预期 55 列")
    if len(header) not in (52, 55) or len(template) != len(header):
        raise ValueError(f"{resource} 列数与模板记录不一致")
    # Client/main use 55 columns; the dynamic table omits three client fields.
    # Header aliases cover the one column whose label differs between versions.
    client_header = client_header_names()
    source_by_name = {}
    for name, value in zip(client_header, client_fields):
        source_by_name[name] = value
        if name == "天赋限制":
            source_by_name["天赋系限制"] = value
        if name == "不可买卖（1：不可买卖 2：不可卖店）":
            source_by_name["不可买卖"] = value
        if name.startswith("绑定拍卖（"):
            source_by_name["绑定拍卖"] = value
    target = list(template)
    for index, name in enumerate(header):
        if name in source_by_name:
            target[index] = source_by_name[name]
    target[0] = str(weapon_id)
    with DpkReader(archive) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        data = reader.extract(entries[resource.lower()])
    decoded = data.decode("gb18030")
    ending = "\r\n" if "\r\n" in decoded else "\n"
    if decoded and not decoded.endswith(("\n", "\r")):
        decoded += ending
    updated = (decoded + "\t".join(target) + ending).encode("gb18030")
    report = replace_resource(
        archive, resource, None, output, line_edit=None, line_edits=None,
        replacement_data=updated, compression_level=6, zlib_mem_level=8,
        zlib_strategy="default", auto_zlib_parameters=True,
        allow_chain_growth=True, allow_chain_shrink=True,
        overwrite_output=False, overwrite_report=False,
        max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
    return {"resource": resource, "columns": len(header), "report": report}


def verify_weapon(archive: Path, weapon_id: int, expected: list[str]) -> dict:
    found = []
    for resource, table in inspect_item_tables(archive).items():
        for line in table["lines"]:
            fields = line.split("\t")
            if fields and fields[0].isdigit() and int(fields[0]) == weapon_id:
                found.append((resource, fields))
    if len(found) != 1:
        raise ValueError(f"写入后物品 ID {weapon_id} 匹配到 {len(found)} 条记录")
    resource, fields = found[0]
    header = inspect_item_tables(archive)[resource]["header"]
    if len(fields) != len(header):
        raise ValueError(f"写入后 {resource} 列数校验失败")
    expected_by_name = dict(zip(client_header_names(), expected))
    expected_by_name["天赋系限制"] = expected_by_name.get("天赋限制", "")
    expected_by_name["不可买卖"] = expected_by_name.get("不可买卖（1：不可买卖 2：不可卖店）", "")
    expected_by_name["绑定拍卖"] = expected_by_name.get("绑定拍卖（1-可拍卖，2-不可拍卖，0-根据绑定方式决定）", "")
    mismatches = [name for name, actual in zip(header, fields)
                  if name in expected_by_name and actual != expected_by_name[name]]
    if mismatches:
        raise ValueError("写入后字段回读不一致：" + "、".join(mismatches[:8]))
    return {"resource": resource, "columns": len(fields)}
