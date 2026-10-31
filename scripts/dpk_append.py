"""Append new files to a whpackage1.0 archive without modifying the input."""
from __future__ import annotations

import argparse
import os
import shutil
import struct
import tempfile
from pathlib import Path

try:
    from .unpack import BLOCK_SIZE, DpkReader, read_u32
    from .repack import encode_whsc, required_block_count
except ImportError:
    from unpack import BLOCK_SIZE, DpkReader, read_u32
    from repack import encode_whsc, required_block_count


def _path(path):
    value = str(path).replace('\\', '/')
    parts = value.split('/')
    if not value or any(part in ('', '.', '..') for part in parts):
        raise ValueError(f'无效的包内路径: {path}')
    try:
        value.encode('ascii')
    except UnicodeEncodeError as exc:
        raise ValueError(f'包内路径必须为 ASCII: {path}') from exc
    return value


def _append_stream(fp, data):
    fp.seek(0, os.SEEK_END)
    size = fp.tell()
    if size % BLOCK_SIZE:
        raise ValueError('DPK 大小不是块大小的整数倍')
    root = size // BLOCK_SIZE
    count = required_block_count(len(data))
    if root == 0 or root + count > 0xffffffff:
        raise ValueError('DPK 块号超出支持范围')
    pos = 0
    for i in range(count):
        block = bytearray(BLOCK_SIZE)
        struct.pack_into('<II', block, 0, root + i - 1 if i else 0,
                         root + i + 1 if i + 1 < count else 0)
        start = 32 if i == 0 else 8
        if i == 0:
            struct.pack_into('<II', block, 8, root + count - 1, len(data))
        chunk = data[pos:pos + BLOCK_SIZE - start]
        block[start:start + len(chunk)] = chunk
        fp.write(block)
        pos += len(chunk)
    return root


def _directory(entries):
    # Preserve insertion order, while combining paths under the same directory.
    tree = {}
    for path, root in entries:
        node = tree
        parts = path.split('/')
        for part in parts[:-1]:
            child = node.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f'文件与目录路径冲突: {path}')
            node = child
        if parts[-1] in node:
            raise ValueError(f'文件或目录重复: {path}')
        node[parts[-1]] = root
    strings = bytearray()
    offsets = {}
    directory = bytearray()
    def offset(name):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode('ascii') + b'\0')
        return offsets[name]
    def walk(node):
        for name, value in node.items():
            if isinstance(value, dict):
                directory.extend(b'\x02' + struct.pack('<I', offset(name)))
                walk(value)
                directory.append(3)
            else:
                directory.extend(b'\x01' + struct.pack('<II', offset(name), value))
    walk(tree)
    return bytes(directory), bytes(strings)


def append_entries(dpk_path, output_path, add):
    """Add {archive_path: bytes}, reject collisions, and verify output before publishing."""
    source, output = Path(dpk_path).resolve(), Path(output_path).resolve()
    if source == output or output.exists():
        raise ValueError('输出文件必须不存在，且不能与输入文件相同')
    if not add:
        raise ValueError('没有要追加的文件')
    normalized = {}
    for name, data in add.items():
        name = _path(name)
        if not isinstance(data, bytes):
            raise TypeError(f'文件内容必须为 bytes: {name}')
        key = name.lower()
        if key in normalized:
            raise ValueError(f'新增路径重复: {name}')
        normalized[key] = (name, data)
    with DpkReader(source) as reader:
        existing = reader.read_entries()
        seen = set()
        for entry in existing:
            key = _path(entry.path).lower()
            if key in seen or key in normalized:
                raise ValueError(f'包内路径重复或已存在: {entry.path}')
            seen.add(key)
        templates = []
        for off in (132, 136):
            raw = reader.read_virtual_file(read_u32(reader.header, off))
            templates.append((raw[:48], raw[15]))
        header = bytearray(reader.header)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.dpk-append-', dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copyfile(source, temp)
        entries = [(e.path, e.root_block) for e in existing]
        with temp.open('r+b') as fp:
            for name, data in normalized.values():
                whsc = encode_whsc(data, flags=1, template_header=bytes(48), compression_level=6)
                entries.append((name, _append_stream(fp, whsc)))
            directory, strings = _directory(entries)
            for off, data, (template, flags) in zip((132, 136), (directory, strings), templates):
                encoded = encode_whsc(data, flags=flags, template_header=template, compression_level=6)
                struct.pack_into('<I', header, off, _append_stream(fp, encoded))
            fp.seek(0, os.SEEK_END)
            struct.pack_into('<I', header, 28, fp.tell() // BLOCK_SIZE)
            fp.seek(0); fp.write(header); fp.flush(); os.fsync(fp.fileno())
        with DpkReader(temp) as reader:
            actual = reader.read_entries()
            if {(e.path, e.root_block) for e in actual} != set(entries) or len(actual) != len(entries):
                raise ValueError('输出包目录验证失败')
            index = {e.path.lower(): e for e in actual}
            for key, (name, data) in normalized.items():
                if reader.extract(index[key]) != data:
                    raise ValueError(f'输出内容验证失败: {name}')
        # Hard-link publication fails if another process created output meanwhile.
        os.link(temp, output)
    finally:
        temp.unlink(missing_ok=True)
    return output


def main():
    parser = argparse.ArgumentParser(description='向 DPK 追加一个目录中的新资源')
    parser.add_argument('dpk', type=Path, help='输入 DPK')
    parser.add_argument('directory', type=Path, help='待追加的本地目录')
    parser.add_argument('archive_prefix', help='该目录对应的 DPK 内路径')
    parser.add_argument('-o', '--output', type=Path, required=True, help='新的输出 DPK，必须不存在')
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error('本地目录不存在')
    prefix = _path(args.archive_prefix)
    files = sorted(p for p in args.directory.rglob('*') if p.is_file())
    result = append_entries(args.dpk, args.output, {
        prefix + '/' + p.relative_to(args.directory).as_posix(): p.read_bytes() for p in files
    })
    print(f'已追加 {len(files)} 个文件：{result}')


if __name__ == '__main__':
    main()
