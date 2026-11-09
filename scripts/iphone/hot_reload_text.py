"""Reload deployed server dialogue table 27 and verify iPhone text rows."""
import hashlib
import io
import json
import shlex
import sys
import tempfile
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader


def main():
    config = json.loads((ROOT / 'gm_config.json').read_text())
    with paramiko.SSHClient() as ssh:
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(config['ssh_host'], port=config['ssh_port'], username=config['ssh_user'],
                    password=config['ssh_password'], timeout=10)
        with tempfile.TemporaryDirectory() as directory, ssh.open_sftp() as sftp:
            package = Path(directory) / 'mb.dpk'
            sftp.get('/opt/xxserver/runtime/xx/shm/TTYGZS/mb.dpk', str(package))
            with DpkReader(package) as reader:
                entries = {entry.path: entry for entry in reader.read_entries()}
                table = reader.extract(entries['etc/talk_list.txt'])
            targets = {}
            for line in table.decode('gb18030').splitlines():
                fields = line.split('\t')
                if fields[0].isdigit() and 50031 <= int(fields[0]) <= 50092:
                    targets[int(fields[0])] = fields[1]
            if len(targets) != 62:
                raise ValueError('手机文字 ID 不完整或重复')
            temp = '/tmp/iphone-talk-' + hashlib.sha256(table).hexdigest()[:20] + '.txt'
            sftp.putfo(io.BytesIO(table), temp)
            source = (ROOT / 'scripts/skill_reload_agent.py').read_text().split("if __name__ == '__main__':")[0]
            source = source.replace("tables[str(number)] = {'rows': row_count,", "tables[str(number)] = {'values': values, 'rows': row_count,")
            source += '''
import shutil
target = %r
expected_hash = %r
tmp = Path(%r)
loose = BASE / 'mb/etc/talk_list.txt'
if loose.exists(): raise RuntimeError('对话表散文件已存在，停止以免覆盖')
if sha256(BASE / 'mb.dpk') != expected_hash: raise RuntimeError('线上主包已发生变化')
allowed_command = lambda command: command in ('help reloadexcel', 'reloadexcel 27')
def ids_snapshot():
    inspector = RuntimeInspector()
    raw = inspector.snapshot([{'id':27,'path':'etc/talk_list.txt','checks':[], 'typed_checks':[]}])
    count = max(raw[str(line)]['tables']['27']['rows'] for line in (1,9))
    spec = {'id':27,'path':'etc/talk_list.txt','checks':[[i,0,0] for i in range(count)],'typed_checks':[]}
    return inspector.snapshot([spec])
def verify_text(line, snap):
    ids = snap[str(line)]['tables']['27']['values']
    index = {v:i for i,v in enumerate(ids)}
    missing = [key for key in target if key not in index]
    if missing: raise RuntimeError('手机文字 ID 未载入: ' + str(missing))
    spec = {'id':27,'path':'etc/talk_list.txt','checks':[], 'typed_checks':[[index[k],1,'string',v] for k,v in sorted(target.items())]}
    result = RuntimeInspector().snapshot([spec])[str(line)]['tables']['27']
    if result['mismatches']: raise RuntimeError('手机文字校验失败: ' + str(result['examples']))
before = ids_snapshot()
loose.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(tmp,loose)
try:
    with NativeGMClient() as client:
        for line in (1,9):
            if not client.command(line,'help reloadexcel').startswith('reloadexcel '): raise RuntimeError('线路不支持表重载')
        for line in (1,9):
            reply=client.command(line,'reloadexcel 27')
            if reply.strip() != 'ReloadExcel: 27 OK!': raise RuntimeError('线路 %%d 重载失败: %%s' %% (line,reply))
            after=ids_snapshot()
            old=before[str(line)]; new=after[str(line)]
            if (old['pid'],old['start']) != (new['pid'],new['start']): raise RuntimeError('线路进程发生变化')
            old_ids=old['tables']['27']['values']; new_ids=new['tables']['27']['values']
            if not set(old_ids).issubset(set(new_ids)): raise RuntimeError('重载后部分原文字 ID 消失')
            verify_text(line,after)
            print('线路 %%d：对话表已重载，手机文字校验通过' %% line,flush=True)
finally:
    loose.unlink(missing_ok=True)
''' % (targets, hashlib.sha256(package.read_bytes()).hexdigest(), temp)
            command = 'sudo -n sh -c ' + shlex.quote(
                'python3 -u -c ' + shlex.quote(source) + '; result=$?; rm -f ' +
                shlex.quote(temp) + '; exit $result')
            stdin, stdout, stderr = ssh.exec_command(command, timeout=180)
            stdin.channel.shutdown_write()
            output = stdout.read().decode('utf8', 'replace')
            error = stderr.read().decode('utf8', 'replace')
            status = stdout.channel.recv_exit_status()
            print(output, end='')
            if status:
                print(error, file=sys.stderr)
                raise SystemExit(status)


if __name__ == '__main__':
    main()
