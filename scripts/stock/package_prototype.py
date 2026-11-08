"""Build and verify offline DPK prototypes; never touches live game files."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.dpk_append import append_entries  # noqa: E402
from scripts.unpack import BLOCK_SIZE, DpkReader, read_u32  # noqa: E402

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"


def replace_chain(source: Path, edits: list[tuple[str, Path]], output: Path) -> None:
    current = source
    for index, (archive_path, replacement) in enumerate(edits):
        target = output if index == len(edits) - 1 else output.with_name(
            output.name + f".step{index + 1}")
        cmd = [sys.executable, str(ROOT / "scripts/repack.py"), str(current),
               archive_path, str(replacement), "-o", str(target),
               "--allow-chain-growth", "--allow-chain-shrink", "--overwrite-output"]
        subprocess.run(cmd, check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
        if current != source:
            current.unlink()
        current = target
    with DpkReader(output) as reader:
        recorded_blocks = read_u32(reader.header, 28)
        actual_blocks = output.stat().st_size // BLOCK_SIZE
        if recorded_blocks != actual_blocks:
            raise RuntimeError(f"invalid DPK header block count: {recorded_blocks} != {actual_blocks}")
        members = {entry.path: entry for entry in reader.read_entries()}
        for archive_path, replacement in edits:
            if reader.extract(members[archive_path]) != replacement.read_bytes():
                raise RuntimeError(f"verification failed: {archive_path}")
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", type=Path, required=True)
    parser.add_argument("--script", type=Path, required=True)
    args = parser.parse_args()
    resource_edits = [(name, BUILD / name) for name in (
        "gui/group/shop/shop.xml", "gui/group/shop/shop.cc",
        "gui/group/shop/game_cangbao.xml", "gui/group/shop/game_cangbao.cc",
        "gui/group/chat/info.h")]
    replace_chain(args.resource, resource_edits, BUILD / "resource.prototype.dpk")

    script_output = BUILD / "script.prototype.dpk"
    if script_output.exists():
        script_output.unlink()
    append_entries(args.script, script_output,
                   {"r_stock_trade.c": (HERE / "r_stock_trade.c").read_bytes()})
    print(script_output)

    for name in ("server_mb_main", "server_mb_dynamic"):
        source = ROOT / "data" / f"{name}.dpk"
        edits = [(entry, BUILD / "tables" / name / entry) for entry in (
            "server/script_func_reg.txt", "server/flag_dword_player.txt",
            "etc/talk_list.txt")]
        replace_chain(source, edits, BUILD / f"{name}.prototype.dpk")
    replace_chain(ROOT / "data/client_mb_work.dpk",
                  [("etc/talk_list.txt", BUILD / "tables/client_mb_work/etc/talk_list.txt")],
                  BUILD / "client_mb.prototype.dpk")


if __name__ == "__main__":
    main()
