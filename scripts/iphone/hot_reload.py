"""Reload the existing iPhone server script on both game lines.

The source must already be present in the deployed script.dpk. This command
does not publish packages or update dialogue tables.
"""
import json
import shlex
import sys
import tempfile
import hashlib
import io
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / 'scripts/skill_reload_agent.py'
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader


def main():
    config = json.loads((ROOT / 'gm_config.json').read_text())
    source = AGENT.read_text().split("if __name__ == '__main__':")[0]
    source += '''
allowed_command = lambda command: command in ('help reloadss', 'reloadss i_iphone_phone specialuse_script')
runtime = RuntimeInspector()
before = {line: (process['pid'], process['start']) for line, process in runtime.processes.items()}
with NativeGMClient() as client:
    for line in (1, 9):
        if not client.command(line, 'help reloadss').startswith('reloadss '):
            raise RuntimeError('线路不支持脚本重载: ' + str(line))
    for line in (1, 9):
        reply = client.command(line, 'reloadss i_iphone_phone specialuse_script')
        print('线路 %d: %s' % (line, reply), flush=True)
        if reply.strip() != 'Reload Script OK!':
            raise RuntimeError('脚本重载失败，检查两条线路状态和脚本日志')
after = RuntimeInspector()
if before != {line: (process['pid'], process['start']) for line, process in after.processes.items()}:
    raise RuntimeError('重载期间线路进程发生变化')
'''
    with paramiko.SSHClient() as ssh:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['ssh_host'], port=config['ssh_port'],
                    username=config['ssh_user'], password=config['ssh_password'], timeout=10)
        package_path = '/opt/xxserver/runtime/xx/shm/TTYGZS/script.dpk'
        loose_path = '/opt/xxserver/runtime/xx/shm/TTYGZS/script/specialuse_script/i_iphone_phone.c'
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / 'script.dpk'
            with ssh.open_sftp() as sftp:
                sftp.get(package_path, str(package))
                with DpkReader(package) as reader:
                    entries = {entry.path: entry for entry in reader.read_entries()}
                    content = reader.extract(entries['specialuse_script/i_iphone_phone.c'])
                temp_path = '/tmp/iphone-reload-' + hashlib.sha256(content).hexdigest()[:20] + '.c'
                sftp.putfo(io.BytesIO(content), temp_path)
            expected = hashlib.sha256(package.read_bytes()).hexdigest()
            prelude = '''
expected = %r
package_path = Path(%r)
loose_path = Path(%r)
temp_path = Path(%r)
if sha256(package_path) != expected:
    raise RuntimeError('线上脚本包在读取期间发生变化')
if loose_path.exists():
    raise RuntimeError('脚本散文件已经存在，停止以免覆盖')
''' % (expected, package_path, loose_path, temp_path)
            marker = 'allowed_command = lambda command:'
            definitions, work = source.split(marker, 1)
            work = marker + work
            source = definitions + prelude + '\nimport shutil\nshutil.copyfile(temp_path, loose_path)\ntry:\n' + ''.join('    ' + line for line in work.splitlines(keepends=True)) + 'finally:\n    loose_path.unlink(missing_ok=True)\n'
            # The SSH command cleans up the two staged files even on an error.
            command = 'sudo -n sh -c ' + shlex.quote(
                'python3 -c ' + shlex.quote(source) + '; result=$?; rm -f ' +
                shlex.quote(temp_path) + '; exit $result')
            stdin, stdout, stderr = ssh.exec_command(command, timeout=150)
        stdin.channel.shutdown_write()
        output = stdout.read().decode('utf-8', 'replace')
        error = stderr.read().decode('utf-8', 'replace')
        status = stdout.channel.recv_exit_status()
        print(output, end='')
        if status:
            print(error, file=sys.stderr)
            raise SystemExit(status)


if __name__ == '__main__':
    main()
