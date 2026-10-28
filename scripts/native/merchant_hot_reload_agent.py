"""Remote, version-pinned merchant cache refresh.

This source is appended to skill_reload_agent.py and the merchant cache reader.
It is run as root over SSH only after tables 50/95 (and optionally 41) verify.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


def _native_source(name, encoded, maximum):
    raw = base64.b64decode(encoded, validate=True)
    if not raw or len(raw) > maximum or b"\0" in raw:
        raise ValueError(name + " 内容无效")
    return raw


def _write_source(path, raw):
    if path.is_symlink():
        raise RuntimeError("原生热重载文件不允许符号链接")
    if not path.exists():
        path.write_bytes(raw)
        path.chmod(0o644)
    elif path.read_bytes() != raw:
        raise RuntimeError("原生热重载文件内容冲突")


def _compile_module(native):
    fingerprint = hashlib.sha256(native).hexdigest()[:24]
    directory = ROOT / "state/gmtool-merchant-refresh"
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    directory.chmod(0o755)
    source_path = directory / ("refresh-" + fingerprint + ".c")
    module_path = directory / ("refresh-" + fingerprint + ".so")
    _write_source(source_path, native)
    if not module_path.exists():
        temporary = directory / (".refresh-" + fingerprint + "-" + str(os.getpid()) + ".so")
        command = ["gcc", "-m32", "-std=gnu99", "-O2", "-Wall", "-Wextra", "-Werror",
                   "-fPIC", "-fno-stack-protector", "-shared", "-nostdlib",
                   "-o", str(temporary), str(source_path)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=40)
        if result.returncode:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("原生刷新模块编译失败：" + (result.stderr or result.stdout)[-1000:])
        temporary.chmod(0o755)
        os.replace(temporary, module_path)
    if module_path.is_symlink() or not module_path.is_file():
        raise RuntimeError("原生刷新模块无效")
    return module_path


def _tracer_for(child):
    status = (Path("/proc") / str(child) / "status").read_text()
    match = re.search(r"^TracerPid:\s*(\d+)\s*$", status, re.M)
    if not match or int(match.group(1)) <= 0:
        raise RuntimeError("游戏线路没有受支持的跟踪器")
    tracer = int(match.group(1))
    args = (Path("/proc") / str(tracer) / "cmdline").read_bytes().split(b"\0")
    if len(args) < 4 or args[1] != b"/opt/xxserver/bin/trace32.py":
        raise RuntimeError("游戏线路跟踪器版本不受支持")
    output_dir = Path(os.fsdecode(args[2]))
    if not output_dir.is_relative_to(ROOT / "runs") or int((output_dir / "trace-child.pid").read_text()) != child:
        raise RuntimeError("跟踪器与游戏线路不匹配")
    libc = Path("/usr/lib/x86_64-linux-gnu/libc.so.6")
    loaded = [line for line in (Path("/proc") / str(tracer) / "maps").read_text().splitlines()
              if "libc.so.6" in line and " 00000000 " in line]
    if not loaded or any("(deleted)" in line or int(line.split()[4]) != libc.stat().st_ino
                         for line in loaded):
        raise RuntimeError("跟踪器加载的 libc 与当前系统文件不一致，请先恢复跟踪器")
    return tracer


def _gdb_refresh(line, process, module_path, gate_path):
    child = process["pid"]
    thread_match = re.search(r"^Threads:\s*(\d+)\s*$",
                             (Path("/proc") / str(child) / "status").read_text(), re.M)
    if not thread_match or int(thread_match.group(1)) != 1:
        raise RuntimeError("线路 " + str(line) + " 不是已验证的单线程游戏进程")
    tracer = _tracer_for(child)
    base = process["base"]
    gate = Path("/tmp/gmtool-merchant-gate-" + str(child))
    for suffix in (".open", ".done", ".result", ".gdb.log"):
        (Path(str(gate) + suffix)).unlink(missing_ok=True)
    directory = ROOT / "state/gmtool-merchant-refresh"
    child_script = directory / ("child-" + str(child) + ".gdb")
    tracer_script = directory / ("tracer-" + str(child) + ".gdb")
    child_script.write_text("set pagination off\nset confirm off\nset unwind-on-signal on\n"
        + "set $h = (void*) dlopen(\"" + str(module_path) + "\", 2)\n"
        + "set $f = (void*) dlsym($h, \"gmtool_refresh\")\n"
        + "call ((int (*)(unsigned))$f)(" + str(base) + ")\ndetach\n")
    tracer_script.write_text("set pagination off\nset confirm off\nset unwind-on-signal on\n"
        + "set $g = (int) PyGILState_Ensure()\n"
        + "call (int) PyRun_SimpleString(\"exec(open('" + str(gate_path) + "').read())\")\n"
        + "call (void) PyGILState_Release($g)\ndetach\n")
    child_log = {}
    def watcher():
        deadline = time.monotonic() + 40
        while not Path(str(gate) + ".open").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        if not Path(str(gate) + ".open").exists():
            child_log["error"] = "等待跟踪器释放超时"
            return
        try:
            result = subprocess.run(["gdb", "-q", "-nx", "-batch", "-p", str(child),
                                     "-x", str(child_script)], capture_output=True,
                                    text=True, timeout=45)
            child_log["output"] = result.stdout + result.stderr
            child_log["exit"] = result.returncode
        except Exception as exc:
            child_log["output"] = str(exc)
            child_log["exit"] = 1
        Path(str(gate) + ".gdb.log").write_text(child_log["output"])
        Path(str(gate) + ".done").write_text(str(child_log["exit"]))
    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()
    outer = subprocess.run(["gdb", "-q", "-nx", "-batch", "-p", str(tracer),
                            "-x", str(tracer_script)], capture_output=True,
                           text=True, timeout=80)
    thread.join(timeout=50)
    result_path = Path(str(gate) + ".result")
    if not result_path.exists():
        raise RuntimeError("线路 " + str(line) + " 未收到跟踪器交接结果：" + (outer.stdout + outer.stderr)[-400:])
    gate_result = json.loads(result_path.read_text())
    if (outer.returncode or not gate_result.get("tracer_restored") or
            not gate_result.get("ok") or child_log.get("exit") != 0):
        raise RuntimeError("线路 " + str(line) + " 原生刷新调用失败：" +
                           str(gate_result) + " " + child_log.get("output", "")[-500:])
    values = re.findall(r"^\$\d+ = (-?\d+)\s*$", child_log.get("output", ""), re.M)
    if values != ["0"]:
        raise RuntimeError("线路 " + str(line) + " 原生刷新返回异常：" +
                           child_log.get("output", "")[-600:])
    return {"pid": child, "tracer": tracer, "result": 0}


try:
    request = json.load(sys.stdin)
    native = _native_source("原生模块", request["native_source"], 100_000)
    gate_source = _native_source("跟踪器交接", request["gate_source"], 30_000)
    import fcntl
    with (ROOT / "state/gmtool-skill-reload.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = RuntimeInspector()
        module_path = _compile_module(native)
        gate_path = (ROOT / "state/gmtool-merchant-refresh" /
                     ("tracer-gate-" + hashlib.sha256(gate_source).hexdigest()[:24] + ".py"))
        _write_source(gate_path, gate_source)
        before = merchant_cache_snapshot(runtime)
        calls = {}
        for line in (1, 9):
            calls[str(line)] = _gdb_refresh(line, runtime.processes[line], module_path, gate_path)
        after_runtime = RuntimeInspector()
        if after_runtime.processes != runtime.processes:
            raise RuntimeError("刷新期间游戏线路进程发生变化")
        after = merchant_cache_snapshot(after_runtime)
        effect = merchant_cache_effect(after, request["merchant_changes"], bool(request["price_changed"]))
        print(json.dumps({"ok": True, "data": {"calls": calls, "cache": effect,
            "before_counts": {line: {key: len(ids) for key, ids in shops.items()}
                for line, shops in before.items()},
            "after_counts": {line: {key: len(ids) for key, ids in shops.items()}
                for line, shops in after.items()}}}, ensure_ascii=False))
except Exception as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
    sys.exit(1)
