"""Build offline replacements for the native stock-trading UI and data tables."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "build"


def replace_once(source: str, old: str, new: str, name: str) -> str:
    count = source.count(old)
    if count != 1:
        raise ValueError(f"{name}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def read_archive(path: Path, names: list[str]) -> dict[str, bytes]:
    with DpkReader(path) as reader:
        entries = {entry.path: entry for entry in reader.read_entries()}
        return {name: reader.extract(entries[name]) for name in names}


def write_file(name: str, content: bytes) -> None:
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    print(path.relative_to(ROOT))


def build_ui(resource: Path) -> None:
    paths = ["gui/group/shop/shop.xml", "gui/group/shop/shop.cc", "gui/group/chat/info.h"]
    original = read_archive(resource, paths)

    shop_xml = original[paths[0]].decode("utf-8").replace("\r\n", "\n")
    shop_xml = replace_once(shop_xml,
        '      <image_button name="btn_group_sale" text="@shop|shop_category_1" reference="btn_group_top"/>',
        '      <image_button name="btn_group_sale" text="@shop|shop_category_1" reference="btn_group_top"/>\n'
        '      <image_button name="btn_group_stock" text="股票交易" reference="btn_group_top"/>', paths[0])
    shop_xml = replace_once(shop_xml,
        '      <panel config="sale.xml"/>',
        '      <panel config="sale.xml"/>\n      <panel config="game_cangbao.xml"/>', paths[0])
    # Eight 84-pixel buttons fit the same 672-pixel space as the original seven 96-pixel buttons.
    for name in ("notice", "score", "sale", "stock", "shoping_cart", "deal", "ah", "purchase"):
        pattern = f'<image_button\\s+name="btn_group_{name}"'
        shop_xml, count = re.subn(pattern,
            lambda match: match.group(0) + (' size="84,28"' if name == "ah" else ' size="84,35"'),
            shop_xml, count=1)
        if count != 1:
            raise ValueError(f"{paths[0]}: top button {name} not found")
    ET.fromstring(shop_xml)
    ET.parse(HERE / "stock.xml")
    write_file(paths[0], shop_xml.replace("\n", "\r\n").encode("utf-8"))
    # Reuse an unreferenced existing member. Appending a new member to this
    # large resource package made the native VFS fail to find shop.cc.
    write_file("gui/group/shop/game_cangbao.xml",
               (HERE / "stock.xml").read_bytes().replace(b"\n", b"\r\n"))

    shop_cc = original[paths[1]].decode("gb18030").replace("\r\n", "\n")
    shop_cc = replace_once(shop_cc,
        'else if(name == L"btn_group_score")',
        'else if(name == L"btn_group_stock")\n\t{\n\t\treturn ui.find_control(L"group:shop:stock");\n\t}\n\telse if(name == L"btn_group_score")', paths[1])
    shop_cc = replace_once(shop_cc,
        '    XYshop_ptr()->visible(false);',
        '    ui.find_control(L"group:shop:stock")->visible(false);\n    XYshop_ptr()->visible(false);', paths[1])
    shop_cc = replace_once(shop_cc,
        '\tp->visible(true);\n\tXYshop_hot_update(true);',
        '\tp->visible(true);\n'
        '    if (p == ui.find_control(L"group:shop:stock"))\n'
        '    {\n'
        '        hot_container_ptr()->visible(false);\n'
        '        G_GetGameMng()->GetNet()->SendScriptPacket(1224, 0, 0, 0, 91827364);\n'
        '    }\n'
        '\tXYshop_hot_update(true);', paths[1])
    shop_cc += '\n#include "game_cangbao.cc"\n'
    write_file(paths[1], shop_cc.replace("\n", "\r\n").encode("gb18030"))
    write_file("gui/group/shop/game_cangbao.cc",
               (HERE / "stock_ui.cc.inc").read_text(encoding="utf-8")
               .replace("\n", "\r\n").encode("gb18030"))

    info_h = original[paths[2]].decode("gb18030").replace("\r\n", "\n")
    info_h = replace_once(info_h,
        'void info_recv(int channel, const wchar_t* name_,int vip, int auth, const wchar_t* text, int from)\n{',
        'void info_recv(int channel, const wchar_t* name_,int vip, int auth, const wchar_t* text, int from)\n{\n'
        '    variant stock_parts = variant(ui_string(text)).split_to_array(L\'|\');\n'
        '    if (stock_parts.size() == 10 && stock_parts.get(0).to_string() == L"@@STOCK")\n'
        '    {\n'
        '        ui.invoke(L"ui_shop", L"void stock_recv(const wchar_t*)", <void*>(text));\n'
        '        return;\n'
        '    }', paths[2])
    write_file(paths[2], info_h.replace("\n", "\r\n").encode("gb18030"))


def add_table_rows(path: Path, archive_name: str, additions: list[str]) -> None:
    original = read_archive(path, [archive_name])[archive_name].decode("gb18030")
    original_rows = original.splitlines()
    id_column = 1 if archive_name.endswith("flag_dword_player.txt") else 0
    present_ids = {line.split("\t")[id_column] for line in original_rows[1:]
                   if len(line.split("\t")) > id_column}
    width = max(len(line.split("\t")) for line in original_rows[:5])
    padded = []
    for row in additions:
        fields = row.split("\t")
        ident = fields[id_column]
        if ident in present_ids:
            raise ValueError(f"{archive_name}: ID {ident} already in use")
        if len(fields) > width:
            raise ValueError(f"{archive_name}: too many columns")
        padded.append(row + "\t" * (width - len(fields)))
    output = original.rstrip("\r\n") + "\r\n" + "\r\n".join(padded) + "\r\n"
    write_file(f"tables/{path.stem}/{archive_name}", output.encode("gb18030"))


def build_tables(server_main: Path, server_dynamic: Path, client: Path) -> None:
    # The second column enables client-originated SendScriptPacket calls.
    registration = "2501\t1\tr_stock_trade"
    reply = "50030\t@@STOCK|%d|%d|%d|%d|%d|%d|%d|%d|%d"
    flags = [f"股票持仓{i}\t{199+i}\t0\t0\t1000\t0" for i in range(1, 4)]
    for path in (server_main, server_dynamic):
        add_table_rows(path, "server/script_func_reg.txt", [registration])
        add_table_rows(path, "server/flag_dword_player.txt", flags)
        add_table_rows(path, "etc/talk_list.txt", [reply])
    add_table_rows(client, "etc/talk_list.txt", [reply])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", type=Path, required=True)
    parser.add_argument("--server-main", type=Path, default=ROOT / "data/server_mb_main.dpk")
    parser.add_argument("--server-dynamic", type=Path, default=ROOT / "data/server_mb_dynamic.dpk")
    parser.add_argument("--client", type=Path, default=ROOT / "data/client_mb_work.dpk")
    parser.add_argument("--ui-only", action="store_true")
    args = parser.parse_args()
    build_ui(args.resource)
    if not args.ui_only:
        build_tables(args.server_main, args.server_dynamic, args.client)


if __name__ == "__main__":
    main()
