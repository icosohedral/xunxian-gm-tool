"""Prepare resource.dpk replacements for the iPhone icon and delivery browser."""
from __future__ import annotations

from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader

HERE = Path(__file__).resolve().parent
OUT = HERE / "build/resource_edits"
RESOURCE = Path("/Users/icosohedral/Projects/xunxian_standalone/client/MhXunxian/resource.dpk")


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError(f"替换锚点数量异常：{old[:80]}")
    return text.replace(old, new, 1)


def main() -> None:
    paths = (
        "gui/group/quest/talk.xml",
        "gui/group/quest/quest.cc",
        "gui/group/quest/talk.h",
        "gui/group/chat/info.h",
    )
    with DpkReader(RESOURCE) as reader:
        entries = {entry.path: entry for entry in reader.read_entries()}
        raw = {name: reader.extract(entries[name]) for name in paths}
        assert "gui/icon/item/rw/rw498.png" in entries

    talk = raw[paths[0]].decode("utf-8")
    overlay = (HERE / "delivery_overlay.xml").read_text(encoding="utf-8")
    if 'name="phone_delivery"' not in talk:
        talk = once(talk, '  <panel reference="cmn_border_foot"/>',
                    overlay + '  <panel reference="cmn_border_foot"/>')
    ET.fromstring(talk)
    new = {paths[0]: talk.encode("utf-8")}

    quest = raw[paths[1]].decode("gb18030")
    if 'int phone_delivery_offset = 0;' in quest:
        quest = quest[:quest.index('int phone_delivery_offset = 0;')]
    quest += (HERE / "delivery_ui.cc.inc").read_text(encoding="utf-8")
    quest += (HERE / "music_ui.cc.inc").read_text(encoding="utf-8")
    new[paths[1]] = quest.encode("gb18030")

    talk_h = raw[paths[2]].decode("gb18030")
    if 'phone_delivery' not in talk_h:
        talk_h = once(talk_h,
        '    else\r\n    {\r\n        comic_ptr()->visible(false);\r\n    }\r\n}\r\n\r\nvoid on_talk_foot_click',
        '    else\r\n    {\r\n        comic_ptr()->visible(false);\r\n'
        '        ui_control* delivery = talk_ptr()->deep_find_child(L"phone_delivery");\r\n'
        '        if (delivery != NULL) delivery->visible(false);\r\n'
        '    }\r\n}\r\n\r\nvoid on_talk_foot_click')
    new[paths[2]] = talk_h.encode("gb18030")

    info = raw[paths[3]].decode("gb18030")
    if 'phone_music_parts' not in info:
        info = once(info,
        '    if (ui_string(text) == L"@@IPHONE_DELIVERY_OPEN")',
        '    variant phone_music_parts = variant(ui_string(text)).split_to_array(L\'|\');\r\n'
        '    if (phone_music_parts.size() == 2 && phone_music_parts.get(0).to_string() == L"@@IPHONE_MUSIC")\r\n'
        '    {\r\n        ui.invoke(L"ui_quest", L"void phone_music_recv(const wchar_t*)", <void*>(text));\r\n        return;\r\n    }\r\n'
        '    if (ui_string(text) == L"@@IPHONE_MUSIC_STOP")\r\n'
        '    {\r\n        ui.invoke(L"ui_quest", L"void phone_music_stop()");\r\n        return;\r\n    }\r\n'
        '    if (ui_string(text) == L"@@IPHONE_DELIVERY_OPEN")')
    if '@@IPHONE_DELIVERY_OPEN' not in info:
        info = once(info,
        'void info_recv(int channel, const wchar_t* name_,int vip, int auth, const wchar_t* text, int from)\r\n{',
        'void info_recv(int channel, const wchar_t* name_,int vip, int auth, const wchar_t* text, int from)\r\n{\r\n'
        '    if (ui_string(text) == L"@@IPHONE_DELIVERY_OPEN")\r\n'
        '    {\r\n'
        '        ui.invoke(L"ui_quest", L"void phone_delivery_open()");\r\n'
        '        return;\r\n'
        '    }\r\n'
        '    if (ui_string(text) == L"@@IPHONE_DELIVERY_CLOSE")\r\n'
        '    {\r\n'
        '        ui.invoke(L"ui_quest", L"void phone_delivery_hide()");\r\n'
        '        return;\r\n'
        '    }')
    new[paths[3]] = info.encode("gb18030")

    icon = OUT / "rw498.png"
    icon.parent.mkdir(parents=True, exist_ok=True)
    import subprocess
    subprocess.run([
        "magick", str(ROOT / "assets/icons/custom/iPhone.png"),
        "-resize", "58x58", "-gravity", "center", "-background", "none",
        "-extent", "64x64", "-define", "png:compression-level=9",
        "PNG32:" + str(icon),
    ], check=True)
    new["gui/icon/item/rw/rw498.png"] = icon.read_bytes()
    for name, content in new.items():
        target = OUT / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print(name, len(content))


if __name__ == "__main__":
    main()
