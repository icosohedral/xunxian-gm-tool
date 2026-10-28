"""Temporarily release trace32.py's ptrace lock for one bounded GDB operation.

Execute inside the tracer's Python interpreter through PyRun_SimpleString.
The companion GDB job creates <gate>.done only after detaching from the child.
"""
import os
import signal
import time
import json

_child = pid  # trace32.py's own child
_gate = "/tmp/gmtool-merchant-gate-" + str(_child)
_detached = False
_result = {"ok": False, "pid": _child}
try:
    for _suffix in (".open", ".done", ".result"):
        try: os.unlink(_gate + _suffix)
        except FileNotFoundError: pass
    os.kill(_child, signal.SIGSTOP)
    _tid, _status = os.waitpid(_child, 0x40000000)
    if _tid != _child or not os.WIFSTOPPED(_status) or os.WSTOPSIG(_status) != signal.SIGSTOP:
        raise RuntimeError("游戏线程未停在预期信号")
    ptr(17, _child, 0, 0)  # PTRACE_DETACH
    _detached = True
    with open(_gate + ".open", "w") as _f: _f.write("ready\n")
    _deadline = time.monotonic() + 60
    while not os.path.exists(_gate + ".done") and time.monotonic() < _deadline:
        time.sleep(0.05)
    if not os.path.exists(_gate + ".done"):
        raise TimeoutError("调试器未在 60 秒内交还进程")
    _result["gdb_exit"] = int(open(_gate + ".done").read().strip())
    _result["ok"] = _result["gdb_exit"] == 0
except Exception as _exc:
    _result["error"] = str(_exc)
finally:
    if _detached:
        try:
            ptr(16, _child, 0, 0)  # PTRACE_ATTACH
            _tid, _status = os.waitpid(_child, 0x40000000)
            if _tid != _child or not os.WIFSTOPPED(_status):
                raise RuntimeError("无法重新附加游戏线程")
            ptr(0x4200, _child, 0, options)  # PTRACE_SETOPTIONS
            ptr(7, _child, 0, 0)  # PTRACE_CONT
            _result["tracer_restored"] = True
        except Exception as _exc:
            _result["tracer_restored"] = False
            _result["restore_error"] = str(_exc)
            _result["ok"] = False
    with open(_gate + ".result", "w") as _f:
        json.dump(_result, _f)
