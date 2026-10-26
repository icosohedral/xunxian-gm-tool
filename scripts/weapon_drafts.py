"""Read weapon templates and prepare an in-memory draft; never write a DPK."""

from pathlib import Path

from .unpack import DpkReader

ITEM_LISTS = ("item/item_list.txt", "item/item_list2.txt", "item/item_list3.txt")
PROFESSIONS = {"1": "游侠", "2": "力士", "3": "符咒师", "4": "法师", "": "未限制"}
ITEM_TRAIT = "item/item_trait.txt"


def search_traits(path: Path, query: str = "", limit: int = 60,
                  ids: set[int] | None = None) -> dict:
    with DpkReader(path) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        entry = entries.get(ITEM_TRAIT)
        if entry is None:
            raise ValueError("客户端包缺少 item/item_trait.txt")
        rows = []
        for line in reader.extract(entry).decode("gb18030").splitlines():
            fields = line.split("\t")
            if len(fields) < 6 or not fields[0].isdigit():
                continue
            trait_id, name = int(fields[0]), fields[5].strip()
            if ids is not None and trait_id not in ids:
                continue
            if query and query.casefold() not in name.casefold() and query not in str(trait_id):
                continue
            rows.append({"id": trait_id, "name": name})
        return {"rows": rows[:limit], "total": len(rows)}


def read_items(path: Path, *, weapons: bool = True) -> tuple[list[str], list[dict]]:
    records = []
    header = []
    with DpkReader(path) as reader:
        entries = {entry.path.lower(): entry for entry in reader.read_entries()}
        for resource in ITEM_LISTS:
            entry = entries.get(resource)
            if entry is None:
                continue
            lines = reader.extract(entry).decode("gb18030").splitlines()
            columns = lines[0].split("\t") if lines else []
            if not header and len(columns) >= 55:
                header = columns
            for line in lines[1:]:
                fields = line.split("\t")
                if len(fields) < 12 or not fields[0].isdigit():
                    continue
                if weapons and fields[2] != "100":
                    continue
                records.append({"id": int(fields[0]), "name": fields[1],
                                "profession": fields[9], "level": fields[11],
                                "source": resource, "fields": fields})
    return header, records


def weapon_catalog(path: Path, *, profession: str = "all", query: str = "",
                   item_id: int | None = None, other_packages: tuple[Path, ...] = ()) -> dict:
    header, weapons = read_items(path)
    if not header:
        raise ValueError("客户端包缺少物品列表")
    used = {item["id"] for item in weapons}
    # Allocate against all item categories, including IDs in local server caches.
    _, all_items = read_items(path, weapons=False)
    used.update(item["id"] for item in all_items)
    for package in other_packages:
        if package.is_file():
            _, items = read_items(package, weapons=False)
            used.update(item["id"] for item in items)
    candidate = max(used, default=0) + 1
    while candidate in used:
        candidate += 1
    candidate = candidate if candidate <= 65535 else None
    counts = {key: sum(item["profession"] == key for item in weapons)
              for key in PROFESSIONS}
    if item_id is not None:
        selected = next((item for item in weapons if item["id"] == item_id), None)
        if selected is None:
            raise ValueError("找不到武器模板")
        icon_models = {}
        for item in weapons:
            icon = item["fields"][4].strip().lower()
            model = item["fields"][5].strip()
            if icon and model.isdigit():
                icon_models.setdefault(icon, model)
        return {"template": selected, "headers": header,
                "proposed_id": candidate, "counts": counts,
                "icon_models": icon_models}
    filtered = [item for item in weapons if
                (profession == "all" or item["profession"] == profession) and
                (not query or query.casefold() in item["name"].casefold() or query in str(item["id"]))]
    return {"rows": [{key: item[key] for key in ("id", "name", "profession", "level", "source")}
                     for item in filtered[:60]], "total": len(filtered),
            "counts": counts, "proposed_id": candidate}
