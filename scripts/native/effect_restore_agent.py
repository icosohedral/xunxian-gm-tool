"""Restore the verified weapon effects after game-server process starts."""
import fcntl, hashlib, json, os, re, subprocess, sys, threading, time
from pathlib import Path
from skill_reload_agent import ROOT, RuntimeInspector

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


def _gdb_refresh(line, process, module_path, gate_path, mode=1):
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
        + "set $f = (void*) dlsym($h, \"" + hook_symbol + "\")\n"
        + "call ((int (*)(unsigned,int))$f)(" + str(base) + "," + str(mode) + ")\ndetach\n")
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


STATE = ROOT / 'state/gmtool-persistent-effects'
GATE = STATE / 'tracer_gate.py'
SOURCES = STATE / 'sources'
INHERIT = (0xad76dd,0xad76f5,0xad770d,0xad7725,0xad77a3,0xad77bb,0xad77d3,0xad77eb,0xad7803,0xad781b,0xad787c,0xad78f0,0xad7908,0xad7920,0xad7938,0xad7950,0xad7968)
SPECS = (
 ('sign_search_enemy_test.c','gmtool_sign_search_hook',((0x950504,0x651f50),)),
 ('sign_search_enemy_test.c','gmtool_sign_activation_hook',((0x91d7fa,0x62b110),)),
 ('sign_search_enemy_test.c','gmtool_sign_distance_hook',((0x94acff,0x619000),(0x94afe2,0x619000))),
 ('summon_three_signs_test.c','gmtool_three_sign_hook',((0xabc48d,0x5ff420),)),
 ('summon_inherit_weapon_test.c','gmtool_inherit_hook',tuple((x,0x5df580) for x in INHERIT)+((0x929e3e,0x5e3480),)),
 ('giant_slayer_merged.c','gmtool_weapon_hook',((0x926bf8,0x653b60),(0x926c2e,0x653b60))),
 ('giant_slayer_merged.c','gmtool_mirror_hook',((0x928c3b,0x6018d0),)),
 ('giant_slayer_merged.c','gmtool_giant_defense_hook',((0x925695,None),)),
 ('summon_hate_multiplier_test.c','gmtool_sign_hate_hook',((0x9267b7,0x6449f0),)),
 ('looter_cooldown_test.c','gmtool_looter_cd_hook',((0x91adf5,0x5ee390),)),
 ('unclean_scatter_pierce_test.c','gmtool_unclean_scatter_hook',((0xa79473,0x6559b0),)),
 ('unclean_scatter_inner_test.c','gmtool_unclean_inner_hook',((0xb29da8,0x6559b0),(0xb29e35,0x6559b0))),
 ('unclean_scatter_targets_test.c','gmtool_unclean_targets_hook',((0xb1c859,0x657e50),)),
)

def classify(process, offsets, module):
    pid,base=process['pid'],process['base']
    rows=(Path('/proc')/str(pid)/'maps').read_text().splitlines()
    ranges=[]
    for row in rows:
        part=row.split()
        if len(part)>5 and part[-1]==str(module):
            lo,hi=part[0].split('-');ranges.append((int(lo,16),int(hi,16)))
    fd=os.open('/proc/%d/mem'%pid,os.O_RDONLY)
    try:
        kinds=[]
        for off,old in offsets:
            site=base+off;data=os.pread(fd,5,site)
            if len(data)!=5:raise RuntimeError('线路调用点读取失败: %#x'%off)
            if old is None and data == bytes.fromhex('890424ffd7'):
                kinds.append('original');continue
            if data[0]!=0xe8:raise RuntimeError('线路调用点不是预期的 call: %#x'%off)
            dest=(site+5+int.from_bytes(data[1:],'little',signed=True))&0xffffffff
            if old is not None and dest==base+old:kinds.append('original')
            elif any(lo<=dest<hi for lo,hi in ranges):kinds.append('installed')
            else:raise RuntimeError('线路调用点 %#x 已被其他模块占用'%off)
        if len(set(kinds))!=1:raise RuntimeError('线路模块处于部分安装状态')
        return kinds[0]
    finally:os.close(fd)

def restore():
    global hook_symbol
    with (ROOT/'state/gmtool-skill-reload.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        runtime=RuntimeInspector()
        modules={name:_compile_module((SOURCES/name).read_bytes()) for name,_,_ in SPECS}
        if not GATE.is_file() or GATE.is_symlink():raise RuntimeError('跟踪器交接脚本缺失')
        plan=[]
        for line,process in runtime.processes.items():
            for name,symbol,sites in SPECS:
                if classify(process,sites,modules[name])=='original':plan.append((line,name,symbol,sites))
        installed=[]
        try:
            for line,name,symbol,sites in plan:
                hook_symbol=symbol;process=runtime.processes[line];installed.append((line,name,symbol,sites))
                _gdb_refresh(line,process,modules[name],GATE,1)
                if classify(process,sites,modules[name])!='installed':raise RuntimeError('安装后回读失败: '+symbol)
            if RuntimeInspector().processes!=runtime.processes:raise RuntimeError('安装期间进程发生变化')
            for line,process in runtime.processes.items():
                for name,symbol,sites in SPECS:
                    if classify(process,sites,modules[name])!='installed':raise RuntimeError('最终核对失败: '+symbol)
        except Exception:
            errors=[]
            for line,name,symbol,sites in reversed(installed):
                try:
                    process=runtime.processes[line];state=classify(process,sites,modules[name])
                    if state=='installed':hook_symbol=symbol;_gdb_refresh(line,process,modules[name],GATE,0)
                except Exception as exc:errors.append(str(exc))
            if errors:raise RuntimeError('恢复失败，回滚不完整：'+'; '.join(errors))
            raise
        print(json.dumps({'ok':True,'processes':runtime.processes,'installed':len(plan)},ensure_ascii=False),flush=True)
        return runtime.processes

def signature():
    entries=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():continue
        try:
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if len(args)>1 and args[0].endswith(b'/debugS/XGZS_d') and args[1] in (b'gzs_cfg1.txt',b'gzs_cfg9.txt'):
                entries.append((args[1],proc.name,(proc/'stat').read_text().rsplit(')',1)[1].split()[19]))
        except (OSError,IndexError):pass
    return tuple(sorted(entries))

def stack_ready():
    try:
        active=json.loads((ROOT/'state/active.json').read_text())
        status=json.loads((Path(active['directory'])/'status.json').read_text())
        return (status.get('status')=='ready_waiting_manual_client'
                and status.get('world_ready') is True
                and status.get('trade_ready') is True)
    except (OSError,ValueError,KeyError,TypeError):
        return False

def watch():
    last=None;last_error=0
    while True:
        sig=signature()
        if len(sig)==2 and stack_ready() and sig!=last and time.monotonic()-last_error>=30:
            try:restore();last=sig;last_error=0
            except Exception as exc:
                print('恢复失败：'+str(exc),file=sys.stderr,flush=True);last_error=time.monotonic()
        if len(sig)!=2:last=None
        time.sleep(5)

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--watch':watch()
    else:restore()
