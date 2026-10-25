"""Inspect and update profession skill cooldown and range values in mb.dpk."""

from __future__ import annotations

from pathlib import Path

from .repack import replace_resource
from .unpack import DpkReader
from .skill_limits import CAREER_GROUP


SKILL_LEARN = "skill/skill_learn.txt"
FACULTY = "object/faculty.txt"
SKILL_DATA = "skill/skill_data_{}.txt"
METHOD_DATA = "skill/method_data_yx.txt"
COOLDOWN_FIELD = 21
METHOD_DISTANCE_FIELD = 10
# Runtime value reported by the user for Qimen's Gouchen (skill 3003).
# Its client/main-package field is the negative code -11569, while the
# dynamic package contains 8; show the observed in-game cooldown in the editor.
KNOWN_RUNTIME_COOLDOWNS = {(4, 3003, "-11569"): "10"}


def _entries(path: Path):
    with DpkReader(path) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        wanted = {SKILL_LEARN, FACULTY}
        wanted.add(METHOD_DATA)
        for group, _ in CAREER_GROUP.values():
            wanted.add(SKILL_DATA.format(group))
        result = {}
        for name in wanted:
            entry = entries.get(name.lower())
            if entry:
                result[name] = reader.extract(entry)
        return result


def _parse(path: Path):
    resources = _entries(path)
    if SKILL_LEARN not in resources or FACULTY not in resources:
        raise ValueError("mb.dpk 缺少技能学习表或职业表")
    professions = []
    for line in resources[FACULTY].decode("gb18030").splitlines():
        fields = line.split("\t")
        if len(fields) >= 2 and fields[0].isdigit() and int(fields[0]) in CAREER_GROUP:
            professions.append({"id": int(fields[0]), "name": fields[1].strip()})
    learn_rows = {}
    for line in resources[SKILL_LEARN].decode("gb18030").splitlines():
        fields = line.split("\t")
        if len(fields) >= 7 and fields[1].isdigit() and fields[4].isdigit():
            learn_rows.setdefault(int(fields[1]), []).append({
                "name": fields[0].strip(), "profession_id": int(fields[4]),
                "learnable": fields[2].strip() == "1"})
    method_ranges = {}
    for line in resources.get(METHOD_DATA, b"").decode("gb18030").splitlines():
        fields = line.split("\t")
        if len(fields) > METHOD_DISTANCE_FIELD and fields[1].isdigit():
            method_type = int(fields[4]) if len(fields) > 4 and fields[4].isdigit() else 0
            method_ranges[int(fields[1])] = (method_type, fields[METHOD_DISTANCE_FIELD].strip())
    skills_by_group = {}
    for profession_id, (group, _) in CAREER_GROUP.items():
        resource = SKILL_DATA.format(group)
        data = resources.get(resource)
        if data is None:
            continue
        rows = {}
        for line in data.decode("gb18030").splitlines():
            fields = line.split("\t")
            if len(fields) > COOLDOWN_FIELD and fields[1].isdigit():
                sid = int(fields[1])
                method_ids = [int(value) for value in fields[2].split("*") if value.isdigit()] if len(fields) > 2 else []
                distances = sorted({method_ranges[mid][1] for mid in method_ids
                    if mid in method_ranges and method_ranges[mid][0] != 0
                    and method_ranges[mid][1].isdigit() and int(method_ranges[mid][1]) > 0}, key=int)
                rows[sid] = {"id": sid, "name": fields[0].strip(),
                    "profession_id": profession_id,
                    "cooldown": fields[COOLDOWN_FIELD].strip(),
                    "range": distances[0] if len(distances) == 1 else "",
                    "has_range": bool(distances),
                    "range_values": distances,
                    "method_ids": method_ids,
                    "resource": resource}
        # skill_data is shared by two career branches. Their exact learning
        # ownership comes from skill_learn.txt.
        skills_by_group.setdefault(group, rows)
    return professions, learn_rows, skills_by_group


def inspect_archive(path: Path) -> dict:
    professions, learn_rows, skills_by_group = _parse(path)
    skills = []
    for profession in professions:
        profession_id = profession["id"]
        group = CAREER_GROUP[profession_id][0]
        for sid, row in skills_by_group.get(group, {}).items():
            learn_entries = learn_rows.get(sid, [])
            matches = [item for item in learn_entries if item["profession_id"] == profession_id]
            if not matches:
                continue
            cooldown = KNOWN_RUNTIME_COOLDOWNS.get(
                (profession_id, sid, row["cooldown"]), row["cooldown"])
            skills.append({**row, "profession_id": profession_id,
                "name": matches[0]["name"] or row["name"],
                "cooldown": cooldown,
                "learnable": any(item["learnable"] for item in matches)})
    skills.sort(key=lambda item: (item["profession_id"], item["name"].casefold(), item["id"]))
    return {"professions": professions, "skills": skills}


def has_skill(path: Path, skill_id: int, profession_id: int) -> bool:
    if profession_id not in CAREER_GROUP:
        return False
    resource = SKILL_DATA.format(CAREER_GROUP[profession_id][0])
    with DpkReader(path) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        entry = entries.get(resource.lower())
        if not entry:
            return False
        for line in reader.extract(entry).decode("gb18030").splitlines():
            fields = line.split("\t")
            if len(fields) > COOLDOWN_FIELD and fields[1].isdigit() and int(fields[1]) == skill_id:
                return True
    return False


def patch_archive(source: Path, output: Path, skill_id: int | list[int], profession_id: int,
                  cooldown: int, distance: int | None, *, allow_missing: bool = False) -> dict:
    skill_ids = [int(value) for value in ([skill_id] if isinstance(skill_id, int) else skill_id)]
    return patch_archive_batch(source, output, profession_id, [{
        "skill_ids": skill_ids, "cooldown": cooldown, "distance": distance}],
        allow_missing=allow_missing)


def patch_archive_batch(source: Path, output: Path, profession_id: int,
                        changes: list[dict], *, allow_missing: bool = False) -> dict:
    if profession_id not in CAREER_GROUP:
        raise ValueError("职业 ID 无效")
    if not changes:
        raise ValueError("请先加入要修改的技能")
    requested = {}
    for change in changes:
        try:
            cooldown = int(change["cooldown"])
            distance = change.get("distance")
            distance = None if distance in (None, "") else int(distance)
            ids = {int(value) for value in change["skill_ids"]}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("技能参数无效") from exc
        if not ids:
            raise ValueError("至少需要选择一个技能")
        if not 0 <= cooldown <= 86400:
            raise ValueError("冷却时间需为 0 到 86400 秒")
        if distance is not None and not 1 <= distance <= 100000:
            raise ValueError("技能距离需为 1 到 100000")
        for skill_id in ids:
            if skill_id in requested:
                raise ValueError(f"技能 {skill_id} 重复加入修改列表")
            requested[skill_id] = {"cooldown": cooldown, "distance": distance}
    group = CAREER_GROUP[profession_id][0]
    resource = SKILL_DATA.format(group)
    with DpkReader(source) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        entry = entries.get(resource.lower())
        if not entry:
            raise ValueError(f"mb.dpk 缺少技能定义表：{resource}")
        data = reader.extract(entry)
    text = data.decode("gb18030")
    result = []
    found = set()
    methods_to_patch = {}
    distance_method_counts = {skill_id: 0 for skill_id, change in requested.items()
                              if change["distance"] is not None}
    old = {}
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        fields = body.split("\t")
        if len(fields) > COOLDOWN_FIELD and fields[1].isdigit() and int(fields[1]) in requested:
            current_id = int(fields[1])
            if current_id in found:
                raise ValueError(f"技能定义表中技能 {current_id} 重复")
            found.add(current_id)
            change = requested[current_id]
            old[str(current_id)] = {"cooldown": fields[COOLDOWN_FIELD]}
            method_ids = [int(value) for value in fields[2].split("*") if value.isdigit()]
            if change["distance"] is not None:
                for method_id in method_ids:
                    previous = methods_to_patch.get(method_id)
                    if previous is not None and previous[0] != change["distance"]:
                        raise ValueError("所选技能共用距离定义，请为它们设置相同距离后再应用")
                    if previous is None:
                        methods_to_patch[method_id] = (change["distance"], {current_id})
                    else:
                        previous[1].add(current_id)
            fields[COOLDOWN_FIELD] = str(change["cooldown"])
            body = "\t".join(fields)
        result.append(body + ending)
    missing = set(requested) - found
    if missing and not allow_missing:
        raise ValueError(f"技能 {', '.join(map(str, sorted(missing)))} 在 {resource} 中缺少定义")
    if not found:
        raise ValueError(f"所选技能在 {resource} 中没有匹配定义")
    first_output = output if not methods_to_patch else output.with_name(output.stem + ".cooldown.dpk")
    if first_output != source:
        first_output.unlink(missing_ok=True)
    replace_resource(source, resource, None, first_output, line_edit=None,
        line_edits=None, replacement_data="".join(result).encode("gb18030"),
        compression_level=6, zlib_mem_level=8, zlib_strategy="default",
        auto_zlib_parameters=True, allow_chain_growth=True,
        allow_chain_shrink=True, overwrite_output=False, overwrite_report=False,
        max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
    updated_method_ids = []
    if methods_to_patch:
        with DpkReader(first_output) as reader:
            entries = {entry.path.lower(): entry for entry in reader.read_entries()}
            method_entry = entries.get(METHOD_DATA.lower())
            method_data = reader.extract(method_entry) if method_entry else None
        if method_data is None:
            if not allow_missing:
                first_output.unlink(missing_ok=True)
                raise ValueError(f"mb.dpk 缺少距离定义表：{METHOD_DATA}")
            first_output.replace(output)
        else:
            method_result = []
            for line in method_data.decode("gb18030").splitlines(keepends=True):
                body = line.rstrip("\r\n")
                ending = line[len(body):]
                fields = body.split("\t")
                if len(fields) > METHOD_DISTANCE_FIELD and fields[1].isdigit() and int(fields[1]) in methods_to_patch:
                    method_id = int(fields[1])
                    method_type = int(fields[4]) if len(fields) > 4 and fields[4].isdigit() else 0
                    current_distance = fields[METHOD_DISTANCE_FIELD].strip()
                    if method_type != 0 and current_distance.isdigit() and int(current_distance) > 0:
                        distance, skill_ids = methods_to_patch[method_id]
                        fields[METHOD_DISTANCE_FIELD] = str(distance)
                        updated_method_ids.append(method_id)
                        for skill_id in skill_ids:
                            distance_method_counts[skill_id] += 1
                        body = "\t".join(fields)
                method_result.append(body + ending)
            if not updated_method_ids and not allow_missing:
                first_output.unlink(missing_ok=True)
                raise ValueError("所选技能没有可修改的施放距离字段")
            replace_resource(first_output, METHOD_DATA, None, output, line_edit=None,
                line_edits=None, replacement_data="".join(method_result).encode("gb18030"),
                compression_level=6, zlib_mem_level=8, zlib_strategy="default",
                auto_zlib_parameters=True, allow_chain_growth=True,
                allow_chain_shrink=True, overwrite_output=False, overwrite_report=False,
                max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
            first_output.unlink(missing_ok=True)
        missing_distance = [skill_id for skill_id, count in distance_method_counts.items() if count == 0]
        if missing_distance and not allow_missing:
            output.unlink(missing_ok=True)
            raise ValueError(f"技能 {', '.join(map(str, missing_distance))} 没有可修改的施放距离字段")
    return {"resource": resource, "old": old, "changes": requested,
        "updated_skill_ids": sorted(found), "missing_skill_ids": sorted(missing),
        "updated_method_ids": sorted(updated_method_ids)}
