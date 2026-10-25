"""Read and patch the four dungeon entry count fields in mb.dpk archives."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .repack import replace_resource
from .unpack import DpkReader


TABLES = {
    "scene": ("scn/scn_info.txt", 5, "场景限制"),
    "copy": ("server/script_excel/xx_copy/xx_copyin.txt", 9, "活动副本 CD"),
    "tower": ("server/script_excel/zhenyaota/zhenyaota_copyin.txt", 6, "镇妖塔每日 CD"),
    "heart": ("etc/define.txt", 1, "心魔砺炼每日上限"),
}


def _identity(kind: str, fields: list[str]) -> tuple[str, str] | None:
    if kind == "scene" and len(fields) > 18 and fields[1].isdigit():
        return fields[1], fields[18].strip() or fields[0].strip()
    if kind == "copy" and len(fields) > 9 and fields[0].isdigit():
        return fields[0], fields[1].strip() or "活动副本"
    if kind == "tower" and len(fields) > 6 and fields[2].isdigit():
        return fields[2] + ":" + fields[1], fields[0].strip() or "镇妖塔"
    if kind == "heart" and len(fields) > 2 and fields[0] == "1047":
        return "1047", "心魔砺炼"
    return None


def _count(kind: str, value: str) -> int | None:
    value = value.strip()
    if kind == "scene":
        parts = value.split("*")
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            return None
        return int(parts[1])
    return int(value) if value.isdigit() else None


def _read_tables(archive: Path) -> dict[str, bytes]:
    with DpkReader(archive) as reader:
        entries = {e.path.lower(): e for e in reader.read_entries()}
        return {kind: reader.extract(entries[path]) for kind, (path, _, _) in TABLES.items()
                if path in entries}


def inspect_archive(archive: Path) -> list[dict]:
    rows = []
    for kind, data in _read_tables(archive).items():
        text = data.decode("gb18030")
        column = TABLES[kind][1]
        seen = set()
        for line in text.splitlines():
            fields = line.split("\t")
            if len(fields) <= column:
                continue
            identity = _identity(kind, fields)
            if not identity:
                continue
            key, name = identity
            count = _count(kind, fields[column])
            # A few ordinary dungeon scenes have no explicit X*N value in
            # scn_info. Include type-4 dungeons so the user can add one.
            blank_dungeon_limit = (kind == "scene" and fields[3].strip() == "4"
                                   and not fields[column].strip())
            if count is None and not blank_dungeon_limit:
                continue
            # The reference patch concerns dungeon limits, not PvP arenas.
            if kind == "scene" and fields[3].strip() == "35":
                continue
            if key in seen:
                raise ValueError(f"{TABLES[kind][0]} 存在重复记录 {key}")
            seen.add(key)
            scene_id = key if kind == "scene" else (
                fields[5].strip() if kind == "copy" else fields[2].strip() if kind == "tower" else "")
            rows.append({"key": f"{kind}:{key}", "name": name, "count": count,
                         "source": TABLES[kind][2], "scene_id": scene_id})
    return rows


def patch_define_overlay(data: bytes, count: int) -> bytes | None:
    """Patch loose etc/define.txt when it shadows the packaged definition."""
    text = data.decode("gb18030")
    result = []
    found = False
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        fields = body.split("\t")
        if fields and fields[0].strip() == "1047":
            if found or len(fields) < 2 or not fields[1].strip().isdigit():
                raise ValueError("松散 define.txt 的 1047 定义格式无效")
            found = True
            fields[1] = str(count)
            line = "\t".join(fields) + line[len(body):]
        result.append(line)
    return "".join(result).encode("gb18030") if found else None


def patch_archive(source: Path, output: Path, changes: dict[str, int]) -> set[str]:
    tables = _read_tables(source)
    replacements = []
    changed_keys = set()
    for kind, data in tables.items():
        column = TABLES[kind][1]
        text = data.decode("gb18030")
        result = []
        modified = False
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\r\n")
            suffix = line[len(body):]
            fields = body.split("\t")
            identity = _identity(kind, fields) if len(fields) > column else None
            key = f"{kind}:{identity[0]}" if identity else None
            old = fields[column] if len(fields) > column else ""
            blank_dungeon_limit = (kind == "scene" and len(fields) > column
                                   and fields[3].strip() == "4"
                                   and not old.strip())
            if key in changes and (_count(kind, old) is not None or blank_dungeon_limit):
                if kind == "scene" and fields[3].strip() == "35":
                    raise ValueError("对抗场景不属于副本限制修改范围")
                interval = old.split("*", 1)[0] if "*" in old else "0"
                fields[column] = (interval + "*" if kind == "scene" else "") + str(changes[key])
                changed_keys.add(key)
                modified |= old != fields[column]
                body = "\t".join(fields)
            result.append(body + suffix)
        if modified:
            replacements.append((TABLES[kind][0], "".join(result).encode("gb18030")))
    if not replacements:
        return changed_keys
    work = Path(tempfile.mkdtemp(prefix="dungeon-limits-", dir=output.parent))
    try:
        current = source
        for index, (path, data) in enumerate(replacements):
            next_path = work / f"step-{index}.dpk"
            replace_resource(current, path, None, next_path, line_edit=None,
                line_edits=None, replacement_data=data, compression_level=6,
                zlib_mem_level=8, zlib_strategy="default", auto_zlib_parameters=True,
                allow_chain_shrink=False, overwrite_output=False,
                overwrite_report=False, max_input_size=2 * 1024 * 1024 * 1024,
                report_path=None)
            current = next_path
        shutil.copyfile(current, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    actual = {row["key"]: row["count"] for row in inspect_archive(output)}
    for key in changed_keys:
        if actual.get(key) != changes[key]:
            raise RuntimeError(f"修改后校验失败：{key}")
    return changed_keys
