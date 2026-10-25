"""Read and patch riding-pet movement and mounted skill settings."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from .repack import replace_resource
from .unpack import DpkReader

RIDE_LIST = "object/ride_list.txt"
RIDE_CONNECTION = "object/ride_connection.txt"
PET_LIST = "pet/pet_list.txt"
ITEM_LISTS = ("item/item_list.txt", "item/item_list2.txt", "item/item_list3.txt")
ITEM_USE = "item/item_use.txt"
RUN_COLUMN = 10
TERRAIN_COLUMN = 14
FLY_COLUMN = 35
DRIVER_CONNECTION_COLUMN = 4
SKILL_PERMISSION_COLUMN = 18
CAST_TO_CODE = {"none": "0", "partial": "1", "all": "1*2"}
TERRAIN_TO_CODE = {"land": "0", "water": "1", "both": "2"}
CODE_TO_TERRAIN = {code: terrain for terrain, code in TERRAIN_TO_CODE.items()}


def _resource(reader: DpkReader, path: str) -> bytes | None:
    entry = next((item for item in reader.read_entries() if item.path.lower() == path), None)
    return reader.extract(entry) if entry else None


def _rows(data: bytes) -> dict[int, list[str]]:
    result = {}
    for line in data.decode("gb18030").splitlines():
        fields = line.split("\t")
        if len(fields) < 2 or not fields[1].isdigit():
            continue
        row_id = int(fields[1])
        if row_id in result:
            raise ValueError(f"配置表存在重复 ID {row_id}")
        result[row_id] = fields
    return result


def cast_mode(code: str) -> str:
    values = set(code.split("*")) - {"", "0"}
    if not values:
        return "none"
    return "all" if {"1", "2"}.issubset(values) else "partial"


def inspect_archive(archive: Path) -> dict:
    with DpkReader(archive) as reader:
        ride_data = _resource(reader, RIDE_LIST)
        connection_data = _resource(reader, RIDE_CONNECTION)
        pet_data = _resource(reader, PET_LIST)
        item_lists = [_resource(reader, path) for path in ITEM_LISTS]
        item_use_data = _resource(reader, ITEM_USE)
    if ride_data is None or connection_data is None:
        raise ValueError(f"{archive.name} 缺少骑宠或乘骑连接表")
    connections = _rows(connection_data)
    rides = {}
    for ride_id, fields in _rows(ride_data).items():
        if len(fields) <= TERRAIN_COLUMN or not fields[RUN_COLUMN].isdigit():
            raise ValueError(f"{RIDE_LIST} 中 RideID {ride_id} 的速度或场景格式无效")
        if fields[TERRAIN_COLUMN] not in CODE_TO_TERRAIN:
            raise ValueError(f"{RIDE_LIST} 中 RideID {ride_id} 的游泳字段无效")
        if len(fields) > FLY_COLUMN and fields[FLY_COLUMN] not in ("", "0", "1"):
            raise ValueError(f"{RIDE_LIST} 中 RideID {ride_id} 的是否可以飞字段无效")
        connection_id = int(fields[DRIVER_CONNECTION_COLUMN] or 0)
        connection = connections.get(connection_id)
        rides[ride_id] = {"id": ride_id, "ride_name": fields[0].strip(),
                          "walk": int(fields[9]), "run": int(fields[RUN_COLUMN]),
                          "terrain": CODE_TO_TERRAIN[fields[TERRAIN_COLUMN]],
                          "fly": fields[FLY_COLUMN] == "1" if len(fields) > FLY_COLUMN else None,
                          "connection_id": connection_id,
                          "connection_name": connection[0].strip() if connection else "",
                          "cast": cast_mode(connection[SKILL_PERMISSION_COLUMN])
                          if connection and len(connection) > SKILL_PERMISSION_COLUMN else "none"}
    pets = {}
    if pet_data is not None:
        for fields in _rows(pet_data).values():
            if len(fields) <= 6 or not fields[6].isdigit():
                continue
            ride_id = int(fields[6])
            pet = {"id": int(fields[1]), "name": fields[0].strip()}
            if pet not in pets.setdefault(ride_id, []):
                pets[ride_id].append(pet)
    item_rides = {}
    if item_use_data is not None:
        uses = {}
        for line in item_use_data.decode("gb18030", "replace").splitlines():
            fields = line.split("\t")
            if len(fields) > 2 and fields[0].isdigit() and fields[1] == "43" and fields[2].isdigit():
                uses[fields[0]] = int(fields[2])
        for item_data in item_lists:
            if item_data is None:
                continue
            for line in item_data.decode("gb18030", "replace").splitlines():
                fields = line.split("\t")
                if len(fields) <= 30 or not fields[0].isdigit() or fields[2] != "1001":
                    continue
                ride_id = uses.get(fields[30])
                if ride_id not in rides:
                    continue
                item = {"id": int(fields[0]), "name": fields[1].strip()}
                if item not in item_rides.setdefault(ride_id, []):
                    item_rides[ride_id].append(item)
    return {"rides": rides, "pets": pets, "item_rides": item_rides,
            "connection_permissions": {row_id: fields[SKILL_PERMISSION_COLUMN]
                for row_id, fields in connections.items() if len(fields) > SKILL_PERMISSION_COLUMN}}


def _replace(source: Path, output: Path, resource: str, data: bytes) -> None:
    replace_resource(source, resource, None, output,
        line_edit=None, line_edits=None, replacement_data=data,
        compression_level=6, zlib_mem_level=8, zlib_strategy="default",
        auto_zlib_parameters=True, allow_chain_shrink=False,
        overwrite_output=False, overwrite_report=False,
        max_input_size=2 * 1024 * 1024 * 1024, report_path=None,
        allow_chain_growth=True)


def patch_archive(source: Path, output: Path, changes: dict[int, dict],
                  connection_changes: dict[int, str]) -> tuple[set[int], bool]:
    with DpkReader(source) as reader:
        ride_data = _resource(reader, RIDE_LIST)
        connection_data = _resource(reader, RIDE_CONNECTION)
    if ride_data is None or connection_data is None:
        raise ValueError(f"{source.name} 缺少骑宠或乘骑连接表")
    connections = _rows(connection_data)
    found = set()
    modified_ride = False
    ride_lines = []
    for line in ride_data.decode("gb18030").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        fields = body.split("\t")
        if len(fields) > RUN_COLUMN and fields[1].isdigit():
            ride_id = int(fields[1])
            if ride_id in changes:
                if ride_id in found:
                    raise ValueError(f"{RIDE_LIST} 存在重复 RideID {ride_id}")
                found.add(ride_id)
                change = changes[ride_id]
                if "run" in change:
                    run = change["run"]
                    if isinstance(run, bool) or not isinstance(run, int) or not 1 <= run <= 9999:
                        raise ValueError("跑步速度须为 1 至 9999 的整数")
                    if fields[RUN_COLUMN] != str(run):
                        fields[RUN_COLUMN] = str(run)
                        modified_ride = True
                if "terrain" in change:
                    terrain = change["terrain"]
                    if not isinstance(terrain, str) or terrain not in TERRAIN_TO_CODE:
                        raise ValueError("使用场景须为仅陆地、仅水中或陆地和水中")
                    if fields[TERRAIN_COLUMN] != TERRAIN_TO_CODE[terrain]:
                        fields[TERRAIN_COLUMN] = TERRAIN_TO_CODE[terrain]
                        modified_ride = True
                if "fly" in change:
                    fly = change["fly"]
                    if type(fly) is not bool:
                        raise ValueError("允许飞行设置须为允许或不允许")
                    if len(fields) > FLY_COLUMN:
                        target = "1" if fly else ""
                        if fields[FLY_COLUMN] != target:
                            fields[FLY_COLUMN] = target
                            modified_ride = True
                body = "\t".join(fields)
        ride_lines.append(body + ending)
    modified_connection = False
    connection_lines = []
    for line in connection_data.decode("gb18030").splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        fields = body.split("\t")
        if len(fields) > SKILL_PERMISSION_COLUMN and fields[1].isdigit():
            mode = connection_changes.get(int(fields[1]))
            target = CAST_TO_CODE[mode] if mode is not None else None
            if target is not None and fields[SKILL_PERMISSION_COLUMN] != target:
                fields[SKILL_PERMISSION_COLUMN] = target
                body = "\t".join(fields)
                modified_connection = True
        connection_lines.append(body + ending)
    if not modified_ride and not modified_connection:
        return found, False
    output.parent.mkdir(parents=True, exist_ok=True)
    intermediate = output.with_name(output.stem + "-intermediate-" + uuid4().hex + output.suffix)
    try:
        if modified_ride and modified_connection:
            _replace(source, intermediate, RIDE_CONNECTION, "".join(connection_lines).encode("gb18030"))
            _replace(intermediate, output, RIDE_LIST, "".join(ride_lines).encode("gb18030"))
        elif modified_ride:
            _replace(source, output, RIDE_LIST, "".join(ride_lines).encode("gb18030"))
        else:
            _replace(source, output, RIDE_CONNECTION, "".join(connection_lines).encode("gb18030"))
    finally:
        intermediate.unlink(missing_ok=True)
    actual = inspect_archive(output)["rides"]
    for ride_id in found:
        if "run" in changes[ride_id] and actual[ride_id]["run"] != changes[ride_id]["run"]:
            raise RuntimeError(f"骑宠 RideID {ride_id} 跑步速度校验失败")
        if "terrain" in changes[ride_id] and actual[ride_id]["terrain"] != changes[ride_id]["terrain"]:
            raise RuntimeError(f"骑宠 RideID {ride_id} 使用场景校验失败")
        if "fly" in changes[ride_id] and actual[ride_id]["fly"] is not None \
                and actual[ride_id]["fly"] != changes[ride_id]["fly"]:
            raise RuntimeError(f"骑宠 RideID {ride_id} 允许飞行校验失败")
    permissions = inspect_archive(output)["connection_permissions"]
    for connection_id, mode in connection_changes.items():
        if connection_id in connections and permissions[connection_id] != CAST_TO_CODE[mode]:
            raise RuntimeError(f"乘骑连接 {connection_id} 的施法设置校验失败")
    return found, True
