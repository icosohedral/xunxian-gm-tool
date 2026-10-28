"""List and remove custom item definitions from mb.dpk packages."""

from __future__ import annotations

from pathlib import Path

from .repack import batch_repacking, replace_resource
from .unpack import DpkReader
from .weapon_custom import inspect_item_tables


CUSTOM_ITEM_ID_MIN = 60074


def catalog(packages: dict[str, Path]) -> list[dict]:
    """Merge custom item rows across client and server packages by item ID."""
    merged = {}
    for source, package in packages.items():
        seen = set()
        for resource, table in inspect_item_tables(package).items():
            for line in table["lines"]:
                fields = line.split("\t")
                if not fields or not fields[0].isdigit():
                    continue
                item_id = int(fields[0])
                if item_id < CUSTOM_ITEM_ID_MIN:
                    continue
                if item_id in seen:
                    raise ValueError(f"{source}物品包中 ID {item_id} 重复")
                seen.add(item_id)
                row = merged.setdefault(item_id, {
                    "id": item_id,
                    "name": fields[1] if len(fields) > 1 else "",
                    "type": fields[2] if len(fields) > 2 else "",
                    "table": resource,
                    "sources": [],
                })
                if source == "客户端" or not row["name"]:
                    row["name"] = fields[1] if len(fields) > 1 else ""
                    row["type"] = fields[2] if len(fields) > 2 else ""
                    row["table"] = resource
                row["sources"].append(source)
    return [merged[item_id] for item_id in sorted(merged)]


def remove_from_package(package: Path, output: Path, item_id: int) -> list[str]:
    """Remove exactly one matching item row from an archive, if present."""
    if type(item_id) is not int or item_id < CUSTOM_ITEM_ID_MIN or item_id > 65535:
        raise ValueError("只能删除物品 ID 大于 60073 的自定义道具")
    package, output = Path(package), Path(output)
    tables = inspect_item_tables(package)
    matches = []
    for resource, table in tables.items():
        for line in table["lines"]:
            fields = line.split("\t")
            if fields and fields[0].isdigit() and int(fields[0]) == item_id:
                matches.append(resource)
    if len(matches) > 1:
        raise ValueError(f"物品包中 ID {item_id} 有多个定义，已停止删除")
    if not matches:
        return []

    resource = matches[0]
    with DpkReader(package) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        entry = entries.get(resource.lower())
        if entry is None:
            raise ValueError("物品表缺失：" + resource)
        source = reader.extract(entry).decode("gb18030")
    lines = source.splitlines(keepends=True)
    if not lines:
        raise ValueError("物品表为空：" + resource)
    retained = [lines[0]]
    removed = 0
    for line in lines[1:]:
        fields = line.rstrip("\r\n").split("\t")
        if fields and fields[0].isdigit() and int(fields[0]) == item_id:
            removed += 1
        else:
            retained.append(line)
    if removed != 1:
        raise ValueError(f"物品表 {resource} 的 ID {item_id} 回读数量异常")

    replacement = "".join(retained).encode("gb18030")
    with batch_repacking():
        replace_resource(
            package, resource, None, output, line_edit=None,
            replacement_data=replacement,
            compression_level=6, zlib_mem_level=8, zlib_strategy="default",
            auto_zlib_parameters=True,
            allow_chain_growth=True, allow_chain_shrink=True,
            overwrite_output=False, overwrite_report=False,
            max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
    after = inspect_item_tables(output)
    if any(line.split("\t", 1)[0].isdigit() and
           int(line.split("\t", 1)[0]) == item_id
           for table in after.values() for line in table["lines"]):
        output.unlink(missing_ok=True)
        raise ValueError(f"物品 ID {item_id} 删除后仍存在")
    return [resource]
