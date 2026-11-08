"""Create offline prototype DPKs from the generated replacement resources."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = Path(__file__).resolve().parent / "build"


def run(cmd):
    completed = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout)
    print(completed.stdout.splitlines()[-1])


for source, name in (
    (ROOT / "data/server_mb_main.dpk", "server_main"),
    (ROOT / "data/server_mb_dynamic.dpk", "server_dynamic"),
    (ROOT / "data/client_mb_work.dpk", "client"),
):
    original = source.with_name(source.name + ".bak.xiaohuang-20261106")
    if original.exists():
        source = original
    current = source
    edits = [
        ("etc/talk_list.txt", "talk_list.txt"),
        ("server/script_func_reg.txt", "script_func_reg.txt"),
        ("object/cha_list.txt", "cha_list.txt"),
    ]
    if (BASE / name / "temp_dword_player.txt").exists():
        edits.append(("server/temp_dword_player.txt", "temp_dword_player.txt"))
    if (BASE / name / "xxsc_tudimiao.txt").exists():
        edits.append(("server/area_refresh/xxsc_tudimiao.txt", "xxsc_tudimiao.txt"))
    for index, (archive_path, filename) in enumerate(edits):
        output = BASE / (f"{name}.step{index + 1}.dpk" if index + 1 < len(edits)
                         else f"{name}.prototype.dpk")
        run([sys.executable, str(ROOT / "scripts/repack.py"), str(current),
             archive_path, str(BASE / name / filename), "-o", str(output),
             "--allow-chain-growth", "--allow-chain-shrink", "--overwrite-output"])
        current = output
