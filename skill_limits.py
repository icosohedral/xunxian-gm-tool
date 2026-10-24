"""Client mb.dpk profession skill access editor."""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from repack import replace_resource
from unpack import DpkReader


SKILL_LEARN = "skill/skill_learn.txt"
NPC_TEACH = "skill/npc_teach_skill.txt"
CAREER_SKILLS = "skill/skill_cha_{}.txt"
CAREER_GROUP_NAMES = {"ls": "力士", "yx": "游侠", "fs": "法师", "fzs": "符咒师"}
CAREER_GROUP = {
    1: ("ls", "力士导师"), 2: ("ls", "力士导师"),
    3: ("yx", "游侠导师"), 4: ("yx", "游侠导师"),
    5: ("fs", "法师导师"), 6: ("fs", "法师导师"),
    7: ("fzs", "符咒师导师"), 8: ("fzs", "符咒师导师"),
    9: ("fs", "法师导师"), 10: ("yx", "游侠导师"),
}
SESSIONS: dict[str, Path] = {}


def _decode(data: bytes) -> tuple[list[str], str]:
    for encoding in ("gb18030", "utf-8-sig"):
        try:
            return data.decode(encoding).splitlines(keepends=True), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("技能配置编码无法识别")


def _resources(path: Path, wanted: set[str]) -> dict[str, bytes]:
    with DpkReader(path) as reader:
        entries = {e.path: e for e in reader.read_entries()}
        missing = wanted - entries.keys()
        if missing:
            raise ValueError("mb.dpk 缺少资源：" + ", ".join(sorted(missing)))
        return {name: reader.extract(entries[name]) for name in wanted}


def inspect_client_dpk(path: Path, cache_dir: Path) -> dict:
    professions, skills, encoding = inspect_dpk_skills(path)
    cache_dir.mkdir(parents=True, exist_ok=True)
    session_id = uuid.uuid4().hex
    stored = cache_dir / f"skill-limits-{session_id}.dpk"
    archive = path.read_bytes()
    stored.write_bytes(archive)
    SESSIONS[session_id] = stored
    return {"session_id": session_id, "sha256": hashlib.sha256(archive).hexdigest(),
            "professions": professions, "skills": skills, "encoding": encoding}


def inspect_dpk_skills(path: Path) -> tuple[list[dict], list[dict], str]:
    with DpkReader(path) as reader:
        entries = {e.path: e for e in reader.read_entries()}
        learn = reader.extract(entries[SKILL_LEARN]) if SKILL_LEARN in entries else None
        faculty = reader.extract(entries["object/faculty.txt"]) if "object/faculty.txt" in entries else None
        career_groups_by_id: dict[int, set[str]] = {}
        for group in CAREER_GROUP_NAMES:
            entry = entries.get(CAREER_SKILLS.format(group))
            if entry is None:
                continue
            for line in reader.extract(entry).splitlines():
                fields = line.split(b"\t")
                if len(fields) < 4:
                    continue
                for token in fields[3].split(b"*"):
                    if token.isdigit():
                        career_groups_by_id.setdefault(int(token), set()).add(group)
    if learn is None or faculty is None or NPC_TEACH not in entries:
        raise ValueError("这个 mb.dpk 缺少技能学习表、职业表或导师表")
    faculty_lines, _ = _decode(faculty)
    professions = []
    for line in faculty_lines:
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) >= 2 and fields[0].isdigit() and 1 <= int(fields[0]) <= 10:
            professions.append({"id": int(fields[0]), "name": fields[1]})
    learn_lines, encoding = _decode(learn)
    skills = []
    profession_ids = {p["id"] for p in professions}
    for line in learn_lines:
        fields = line.rstrip("\r\n").split("\t")
        if len(fields) < 7 or not fields[1].isdigit() or fields[2] != "1" or not fields[4].isdigit():
            continue
        owner = int(fields[4])
        if owner not in profession_ids:
            continue
        sid = int(fields[1])
        skills.append({"id": sid, "name": fields[0], "profession_id": owner,
                       "career_groups": sorted(career_groups_by_id.get(sid, set())),
                       "level": fields[3], "required_level": fields[6]})
    return professions, skills, encoding


def inspect_dpk_career_groups(path: Path) -> dict[int, list[str]]:
    """Read career-list membership, including IDs without a learn-table row."""
    groups_by_id: dict[int, set[str]] = {}
    with DpkReader(path) as reader:
        entries = {e.path: e for e in reader.read_entries()}
        for group in CAREER_GROUP_NAMES:
            entry = entries.get(CAREER_SKILLS.format(group))
            if entry is None:
                continue
            for line in reader.extract(entry).splitlines():
                fields = line.split(b"\t")
                if len(fields) < 4:
                    continue
                for token in fields[3].split(b"*"):
                    if token.isdigit():
                        groups_by_id.setdefault(int(token), set()).add(group)
    return {skill_id: sorted(groups) for skill_id, groups in groups_by_id.items()}


def _lines(data: bytes, name: str) -> tuple[list[bytes], bytes]:
    # Keep original line endings and encoding bytes intact.
    lines = data.splitlines(keepends=True)
    if not lines:
        raise ValueError(f"{name} 是空文件")
    return lines, b""


def _change_list_resource(data: bytes, row_name: str, add_ids: set[int], remove_ids: set[int], label: str) -> bytes:
    lines = data.splitlines(keepends=True)
    matches = []
    for i, line in enumerate(lines):
        body = line.rstrip(b"\r\n")
        fields = body.split(b"\t")
        if fields and fields[0].decode("gb18030", "replace").strip() == row_name:
            matches.append((i, fields, line[len(body):]))
    if len(matches) != 1:
        raise ValueError(f"{label}中“{row_name}”匹配到 {len(matches)} 行，无法安全修改")
    i, fields, ending = matches[0]
    if len(fields) < 4:
        raise ValueError(f"{label}中的目标行列数不足")
    existing = fields[3].decode("ascii", "ignore").split("*")
    current = {int(x) for x in existing if x.isdigit()}
    kept = [token for token in existing
            if not (token.isdigit() and int(token) in remove_ids)]
    kept_values = {int(token) for token in kept if token.isdigit()}
    fields[3] = "*".join(kept + [str(x) for x in sorted(add_ids - kept_values)]).encode("ascii")
    lines[i] = b"\t".join(fields) + ending
    return b"".join(lines)


def patch_client_dpk(session_id: str, target_profession: int, changes: dict[int, bool],
                     source_professions: dict[int, int], cache_dir: Path) -> tuple[bytes, str]:
    source = SESSIONS.get(session_id)
    if not source or not source.is_file():
        raise ValueError("上传的客户端包已过期，请重新选择 mb.dpk")
    return patch_dpk_changes(source, target_profession, changes, source_professions, cache_dir)


def refresh_client_session(session_id: str, archive: bytes) -> None:
    source = SESSIONS.get(session_id)
    if not source or not source.is_file():
        raise ValueError("上传的客户端包会话已过期，请重新选择 mb.dpk")
    source.write_bytes(archive)


def patch_dpk_changes(source: Path, target_profession: int, changes: dict[int, bool],
                      source_professions: dict[int, int], cache_dir: Path,
                      allowed_missing_learn_ids: set[int] | None = None) -> tuple[bytes, str]:
    if target_profession not in CAREER_GROUP:
        raise ValueError("目标职业 ID 无效")
    desired = {int(skill_id): bool(assigned) for skill_id, assigned in changes.items()}
    if not desired:
        raise ValueError("没有待应用的技能修改")
    add_ids = {skill_id for skill_id, assigned in desired.items() if assigned}
    remove_ids = {skill_id for skill_id, assigned in desired.items() if not assigned}
    target_group, mentor_name = CAREER_GROUP[target_profession]
    wanted = {SKILL_LEARN, NPC_TEACH, CAREER_SKILLS.format(target_group)}
    original = _resources(source, wanted)
    learn_lines = original[SKILL_LEARN].splitlines(keepends=True)
    found: set[int] = set()
    result_lines = []
    for line in learn_lines:
        body = line.rstrip(b"\r\n")
        ending = line[len(body):]
        fields = body.split(b"\t")
        if len(fields) >= 5 and fields[1].isdigit() and int(fields[1]) in desired:
            sid = int(fields[1])
            if fields[2] != b"1":
                raise ValueError(f"技能 {sid} 当前标记为不可学习")
            if desired[sid]:
                fields[4] = str(target_profession).encode("ascii")
            else:
                source_profession = source_professions.get(sid)
                if source_profession not in CAREER_GROUP:
                    raise ValueError(f"技能 {sid} 缺少原职业信息，无法取消赋予")
                fields[4] = str(source_profession).encode("ascii")
            found.add(sid)
            line = b"\t".join(fields) + ending
        result_lines.append(line)
    missing = set(desired) - found
    if missing - (allowed_missing_learn_ids or set()):
        raise ValueError("以下技能 ID 在当前包的可学技能表中不存在：" + ", ".join(map(str, sorted(missing))))

    # Career lists and mentor lists are shared by all professions in a class.
    # Keep those entries when the original profession shares the target list.
    list_add_ids = add_ids
    list_remove_ids = {sid for sid in remove_ids
                       if CAREER_GROUP[source_professions[sid]][0] != target_group}
    patched = {
        SKILL_LEARN: b"".join(result_lines),
        NPC_TEACH: _change_list_resource(original[NPC_TEACH], mentor_name,
            list_add_ids, list_remove_ids, "职业导师技能列表"),
        CAREER_SKILLS.format(target_group): _change_list_resource(
            original[CAREER_SKILLS.format(target_group)],
            {"ls":"力士","yx":"游侠","fs":"法师","fzs":"符咒师"}[target_group],
            list_add_ids, list_remove_ids, "职业技能集"),
    }
    work = Path(tempfile.mkdtemp(prefix="skill-limits-", dir=cache_dir))
    current = source
    try:
        for index, (entry, data) in enumerate(patched.items()):
            output = work / f"step-{index}.dpk"
            replace_resource(current, entry, None, output,
                line_edit=None, line_edits=None, replacement_data=data,
                compression_level=6, zlib_mem_level=8, zlib_strategy="default",
                auto_zlib_parameters=True, allow_chain_shrink=False,
                overwrite_output=False, overwrite_report=False,
                max_input_size=2 * 1024 * 1024 * 1024, report_path=None)
            current = output
        result = current.read_bytes()
        return result, f"mb.skill-limits.{hashlib.sha256(result).hexdigest()[:8]}.dpk"
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
