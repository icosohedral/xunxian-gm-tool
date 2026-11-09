#!/usr/bin/env python3
"""Build a relocatable restore package on the existing Xunxian host.

Run as root on the source host. The source installation is read only.
"""
import configparser
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime

SRC = Path('/opt/xxserver')
BASE = Path('/home/ubuntu/xxsrv/xxserver-snapshot')
OUT = Path('/home/ubuntu/xxserver-current-20261109')
DBS = ['xunxian_84', 'xunxian_ims_84', 'xunxian_lba_84', 'xunxian_gis_84']

def run(*args, **kw):
    subprocess.run(args, check=True, **kw)

assert os.geteuid() == 0
assert SRC.is_dir() and BASE.is_dir() and not OUT.exists()
shutil.copytree(BASE, OUT, copy_function=shutil.copy2)
for sub in ('runtime', 'bin'):
    run('rsync', '-a', '--exclude=*.bak.*', '--exclude=*.old',
        '--exclude=__pycache__/', str(SRC / sub) + '/', str(OUT / 'payload' / sub) + '/')
effects = SRC / 'state/gmtool-persistent-effects'
if effects.is_dir():
    shutil.copytree(effects, OUT / 'payload/state/gmtool-persistent-effects',
                    ignore=shutil.ignore_patterns('before-*', '*.log', '__pycache__'))
unit = Path('/etc/systemd/system/gmtool-persistent-effects.service')
if unit.is_file():
    (OUT / 'systemd').mkdir()
    shutil.copy2(unit, OUT / 'systemd' / unit.name)
    installer = OUT / 'scripts/install.py'
    source = installer.read_text()
    source = source.replace(" for cmd in ['bash','python3','ip'",
                            " for cmd in ['gcc','gdb','systemctl','bash','python3','ip'")
    marker = " print('Installed to /opt/xxserver. Next: sudo /opt/xxserver/start.sh, sudo /opt/xxserver/status.sh, then manual client login.')"
    service_install = """ unit=PKG/'systemd/gmtool-persistent-effects.service'
 if unit.exists():
  dst=Path('/etc/systemd/system/gmtool-persistent-effects.service')
  assert not dst.exists(),'Existing effect restore service would be overwritten'
  shutil.copy2(unit,dst)
  subprocess.run(['systemctl','daemon-reload'],check=True)
  subprocess.run(['systemctl','enable',dst.name],check=True)
  subprocess.run(['systemctl','start',dst.name],check=True)
"""
    assert marker in source
    installer.write_text(source.replace(marker, service_install + marker))

client = configparser.ConfigParser()
client.read(SRC / 'database/runtime/app-client.cnf')
secret = client['client']['password'].encode()
config_paths = ['runtime/all/xxsrv.ini', 'runtime/xx/shm/cmncfg.txt',
                'runtime/xx/ITC/bin/cmncfg.txt']
old_ip = json.loads((SRC / 'state/installed.json').read_text())['game_ip'].encode()
for rel in config_paths:
    path = OUT / 'payload' / rel
    data = path.read_bytes().replace(secret, b'__APP_PASSWORD__')
    data = data.replace(old_ip, b'__GAME_IP__')
    if rel.endswith('xxsrv.ini'):
        data = re.sub(rb'(?m)^(allow_cidr\s*=\s*)[^\r\n]*',
                      rb'\1__CLIENT_CIDRS__', data)
    assert secret not in data and old_ip not in data, rel
    path.write_bytes(data)

db = OUT / 'database/business.sql.gz'
mysql = SRC / 'database/tools/mysql-5.5.47-linux2.6-x86_64/bin/mysqldump'
env = os.environ.copy()
env['LD_LIBRARY_PATH'] = ':'.join((str(SRC / 'database/compat64'),
    str(SRC / 'database/compat64/lib/x86_64-linux-gnu')))
args = [str(mysql), '--defaults-extra-file=' + str(SRC / 'database/runtime/admin-client.cnf'),
        '--single-transaction', '--quick', '--routines', '--triggers', '--events',
        '--hex-blob', '--add-drop-database', '--databases', *DBS]
with db.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', compresslevel=6) as zipped:
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    shutil.copyfileobj(p.stdout, zipped)
    stderr = p.stderr.read()
    if p.wait():
        raise RuntimeError('mysqldump failed: ' + stderr.decode(errors='replace')[-500:])

manifest = json.loads((OUT / 'manifest.json').read_text())
manifest.update(release='xxserver-current-20261109',
                snapshot_phase='FINAL_SNAPSHOT',
                snapshot_time=datetime.now().astimezone().isoformat(),
                purpose='CURRENT_SERVER_RESTORE_SNAPSHOT',
                sql_sha256=hashlib.sha256(db.read_bytes()).hexdigest(),
                final_client_acceptance_on_new_server='pending',
                final_data_checkpoint=None,
                current_features=['Current server files and four business databases',
                                  'Persistent game effect restore service'],
                production_policy='Install on an empty Ubuntu 24.04 x86_64 host')
(OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
(OUT / 'database/checkpoint.json').unlink(missing_ok=True)
with (OUT / 'README.md').open('a') as readme:
    readme.write('\n## Current server snapshot\n\nThis package contains the current runtime files and four business databases. '
                 'The installer also installs gmtool-persistent-effects.service. The target needs gcc with 32-bit '
                 'compilation support, gdb and systemd. A fresh-host restore and client login remain to be tested.\n')
rows=[]
for path in OUT.rglob('*'):
    if not path.is_file() or path.name in ('file-manifest.json', 'SHA256SUMS', 'install.env'):
        continue
    rel=path.relative_to(OUT).as_posix()
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
    rows.append({'path':rel,'bytes':path.stat().st_size,'sha256':h.hexdigest()})
rows.sort(key=lambda x:x['path'])
(OUT/'file-manifest.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
(OUT/'SHA256SUMS').write_text(''.join(f"{x['sha256']}  {x['path']}\n" for x in rows))
print(json.dumps({'package':str(OUT),'files':len(rows),'database_bytes':db.stat().st_size,
                  'payload_bytes':sum(x['bytes'] for x in rows)},ensure_ascii=False))
