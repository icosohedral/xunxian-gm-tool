"""Validate registered hooks and package dependencies before recovery packaging."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.unpack import DpkReader


def validate(manifest_path, gameplay, packages):
    manifest_path = Path(manifest_path).resolve()
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('components'), list):
        raise ValueError('机制清单格式不受支持')
    digest = hashlib.sha256(Path(gameplay).read_bytes()).hexdigest()
    if digest != data.get('gameplay_sha256'):
        raise ValueError('服务端游戏逻辑程序哈希不匹配')
    sites, names, sources = {}, set(), []
    for component in data['components']:
        identity = component['id']
        if not re.fullmatch(r'[a-z0-9-]+', identity) or identity in names:
            raise ValueError('机制 ID 无效或重复：' + str(identity))
        names.add(identity)
        for offset in component['hook_sites']:
            number = int(offset, 0)
            if number in sites:
                raise ValueError(f'调用点 {offset} 冲突：{sites[number]} / {identity}')
            sites[number] = identity
        for name in component['sources']:
            path = (manifest_path.parent / name).resolve()
            if not path.is_relative_to(manifest_path.parent) or not path.is_file():
                raise ValueError('缺少机制源码：' + name)
            sources.append({'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    report = {}
    for label, path in packages.items():
        with DpkReader(Path(path)) as reader:
            entries = {entry.path.lower(): entry for entry in reader.read_entries()}
            for component in data['components']:
                for resource in component['resources']:
                    name = resource['path'].lower()
                    if name not in entries: raise ValueError(f'{label} 缺少资源 {name}')
                    lines = reader.extract(entries[name]).decode('gb18030').splitlines()
                    for identity in resource['ids']:
                        matches = [line for line in lines if len(f := line.split('\t')) > resource['key_column'] and f[resource['key_column']] == str(identity)]
                        if len(matches) != 1: raise ValueError(f'{label} {name} 编号 {identity} 数量为 {len(matches)}')
                weapons = {}
                for name in ('item/item_list.txt','item/item_list2.txt','item/item_list3.txt'):
                    if name not in entries: continue
                    for line in reader.extract(entries[name]).decode('gb18030').splitlines():
                        f = line.split('\t')
                        if len(f) > 1 and f[0] in component['weapons']:
                            if f[0] in weapons: raise ValueError(f'{label} 武器编号重复 {f[0]}')
                            weapons[f[0]] = f[1]
                if weapons != component['weapons']: raise ValueError(f'{label} 武器名称或编号不匹配')
        report[label] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {'gameplay_sha256': digest, 'components': sorted(names), 'sources': sources, 'packages': report, 'hook_sites': len(sites)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', default=str(Path(__file__).with_name('manifest.json')))
    p.add_argument('--gameplay', required=True)
    p.add_argument('--package', action='append', required=True, metavar='NAME=PATH')
    args = p.parse_args()
    packages = {}
    for entry in args.package:
        name, sep, path = entry.partition('=')
        if not sep or not name or not path or name in packages: p.error('包参数需要唯一的 NAME=PATH')
        packages[name] = path
    print(json.dumps(validate(args.manifest, args.gameplay, packages), ensure_ascii=False, indent=2))

if __name__ == '__main__': main()
