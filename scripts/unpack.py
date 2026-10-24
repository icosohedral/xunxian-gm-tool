from __future__ import annotations

import argparse
import hashlib
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import DES


BLOCK_SIZE = 1024

PACKAGE_PASSWORD = b"tianxiawudi"
CONTENT_PASSWORD = b"gandiaosifu"

PACKAGE_KEY = PACKAGE_PASSWORD[:8]   # b"tianxia"
CONTENT_KEY = CONTENT_PASSWORD[:8]   # b"gandiaos"


@dataclass(frozen=True)
class DpkEntry:
    path: str
    root_block: int


def read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"读取 uint32 越界：offset={offset}")
    return struct.unpack_from("<I", data, offset)[0]


def read_exact_at(fp, offset: int, count: int) -> bytes:
    fp.seek(offset)
    data = fp.read(count)

    if len(data) != count:
        raise EOFError(
            f"读取 DPK 时在偏移 {offset + len(data)} 提前结束，"
            f"需要 {count} 字节，实际读取 {len(data)} 字节"
        )

    return data


def decrypt_payload(payload: bytes, key: bytes) -> bytes:
    """
    复现 DpkReader.DecryptPayload。

    完整 8 字节块：
      DES-ECB 解密后手工 XOR 前一密文块，
      等价于 DES-CBC、全零 IV、无填充。

    最后不足 8 字节：
      使用原程序的自定义交换和链式 XOR。
    """
    if len(key) != 8:
        raise ValueError("DES 密钥必须正好为 8 字节")

    full_length = len(payload) // 8 * 8
    output = bytearray(len(payload))
    previous_cipher_block = bytearray(8)

    if full_length > 0:
        cipher = DES.new(key, DES.MODE_CBC, iv=b"\x00" * 8)
        output[:full_length] = cipher.decrypt(payload[:full_length])
        previous_cipher_block[:] = payload[full_length - 8:full_length]

    if full_length < len(payload):
        tail = bytearray(payload[full_length:])

        parity = (tail[-1] + tail[0]) & 1

        for left in range(len(tail) // 2):
            right = len(tail) - left - 1

            if ((tail[right] + tail[left]) & 1) == parity:
                tail[left], tail[right] = tail[right], tail[left]

        previous_tail_byte = 0

        for i in range(len(tail)):
            encrypted_tail_byte = tail[i]

            plain_byte = (
                encrypted_tail_byte
                ^ key[i]
                ^ previous_tail_byte
                ^ previous_cipher_block[i]
            )

            output[full_length + i] = plain_byte
            previous_tail_byte = encrypted_tail_byte

    return bytes(output)


def decode_whsc(data: bytes) -> bytes:
    if len(data) < 48 or data[:7] != b"whsc1.0":
        raise ValueError("DPK 文件流不是 WHSC 格式")

    flags = data[15]
    expected_md5 = data[16:32]

    reserved = read_u32(data, 32)
    original_size = read_u32(data, 36)
    payload_size = read_u32(data, 40)

    if reserved != 0 or payload_size > len(data) - 48:
        raise ValueError("WHSC 头部损坏")

    payload = data[48:48 + payload_size]

    if flags & 0x02:
        key = CONTENT_KEY if flags & 0x04 else PACKAGE_KEY
        payload = decrypt_payload(payload, key)

    if flags & 0x01:
        try:
            payload = zlib.decompress(payload)
        except zlib.error as exc:
            raise ValueError(f"WHSC zlib 解压失败：{exc}") from exc

    if len(payload) != original_size:
        raise ValueError(
            f"WHSC 解压长度不符：应为 {original_size}，"
            f"实际为 {len(payload)}"
        )

    actual_md5 = hashlib.md5(payload).digest()
    if actual_md5 != expected_md5:
        raise ValueError("WHSC 内容 MD5 校验失败")

    return payload


class DpkReader:
    def __init__(self, path: Path):
        self.path = path
        self.fp = path.open("rb")
        self.file_size = path.stat().st_size
        self.header = self._read_header()

    def close(self) -> None:
        self.fp.close()

    def __enter__(self) -> "DpkReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _read_header(self) -> bytes:
        header = read_exact_at(self.fp, 0, 1024)

        if header[:12] != b"whpackage1.0":
            raise ValueError("文件不是 whpackage1.0 DPK 包")

        expected = hashlib.md5(PACKAGE_PASSWORD).digest()
        if header[32:48] != expected:
            raise ValueError("DPK 包密码校验失败")

        return header

    def _read_block(self, block_number: int) -> bytes:
        offset = block_number * BLOCK_SIZE

        if offset < 0 or offset + BLOCK_SIZE > self.file_size:
            raise ValueError(
                f"块 {block_number} 超出文件范围，offset={offset}"
            )

        return read_exact_at(self.fp, offset, BLOCK_SIZE)

    def read_virtual_file(self, root_block: int) -> bytes:
        root = self._read_block(root_block)

        previous_block = read_u32(root, 0)
        next_block = read_u32(root, 4)
        tail_block = read_u32(root, 8)
        total_size = read_u32(root, 12)

        if previous_block != 0:
            raise ValueError(f"块 {root_block} 不是根块")

        output = bytearray()
        output.extend(root[32:32 + min(total_size, 992)])

        current_block = root_block
        visited = {root_block}

        while next_block != 0 and len(output) < total_size:
            if next_block in visited:
                raise ValueError(f"DPK 块链在 {next_block} 形成循环")

            visited.add(next_block)
            block = self._read_block(next_block)

            block_previous = read_u32(block, 0)
            block_next = read_u32(block, 4)

            if block_previous != current_block:
                raise ValueError(
                    f"DPK 块链 {current_block} -> {next_block} 断裂"
                )

            remaining = total_size - len(output)
            count = min(remaining, 1016)
            output.extend(block[8:8 + count])

            current_block = next_block
            next_block = block_next

        if current_block != tail_block:
            raise ValueError("DPK 尾块校验失败")

        if len(output) != total_size:
            raise ValueError(
                f"DPK 虚拟文件长度不符：应为 {total_size}，"
                f"实际为 {len(output)}"
            )

        return bytes(output)

    def read_entries(self) -> list[DpkEntry]:
        directory_root_block = read_u32(self.header, 132)
        strings_root_block = read_u32(self.header, 136)

        directory_data = decode_whsc(
            self.read_virtual_file(directory_root_block)
        )
        strings_data = decode_whsc(
            self.read_virtual_file(strings_root_block)
        )

        string_table: dict[int, str] = {}
        position = 0

        while position < len(strings_data):
            end = strings_data.find(b"\x00", position)

            if end < 0:
                raise ValueError("DPK 路径字符串表损坏")

            string_table[position] = strings_data[
                position:end
            ].decode("ascii")

            position = end + 1

        entries: list[DpkEntry] = []
        directory_stack: list[str] = []
        position = 0

        while position < len(directory_data):
            record_type = directory_data[position]
            position += 1

            if record_type == 1:
                if position + 8 > len(directory_data):
                    raise ValueError("DPK 文件记录被截断")

                string_offset = read_u32(directory_data, position)
                root_block = read_u32(directory_data, position + 4)
                position += 8

                try:
                    name = string_table[string_offset]
                except KeyError as exc:
                    raise ValueError(
                        f"字符串表不存在偏移 {string_offset}"
                    ) from exc

                path = "/".join([*directory_stack, name])
                entries.append(DpkEntry(path, root_block))

            elif record_type == 2:
                if position + 4 > len(directory_data):
                    raise ValueError("DPK 目录记录被截断")

                string_offset = read_u32(directory_data, position)
                position += 4

                try:
                    directory_stack.append(string_table[string_offset])
                except KeyError as exc:
                    raise ValueError(
                        f"字符串表不存在偏移 {string_offset}"
                    ) from exc

            elif record_type == 3:
                if not directory_stack:
                    raise ValueError("DPK 目录树不平衡")

                directory_stack.pop()

            else:
                raise ValueError(
                    f"未知的 DPK 目录记录：{record_type}"
                )

        return entries

    def extract(self, entry: DpkEntry) -> bytes:
        return decode_whsc(
            self.read_virtual_file(entry.root_block)
        )


def safe_output_path(root: Path, archive_path: str) -> Path:
    normalized = archive_path.replace("\\", "/")
    parts = []

    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise ValueError(f"资源路径包含上级目录：{archive_path}")
        parts.append(part)

    if not parts:
        raise ValueError("资源路径为空")

    output = root.joinpath(*parts)

    root_resolved = root.resolve()
    output_resolved = output.resolve()

    if os.path.commonpath([root_resolved, output_resolved]) != str(root_resolved):
        raise ValueError(f"资源路径越界：{archive_path}")

    return output


def extract_all(dpk_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    with DpkReader(dpk_path) as reader:
        entries = reader.read_entries()
        print(f"找到 {len(entries)} 个资源")

        for index, entry in enumerate(entries, start=1):
            try:
                data = reader.extract(entry)
                output_path = safe_output_path(output_dir, entry.path)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(data)

                print(
                    f"[{index}/{len(entries)}] "
                    f"{entry.path} ({len(data)} bytes)"
                )
            except Exception as exc:
                print(
                    f"[{index}/{len(entries)}] "
                    f"失败：{entry.path}: {exc}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="解包 whpackage1.0 DPK 文件"
    )
    parser.add_argument("dpk", type=Path, help="输入 DPK 文件")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出目录，默认使用 DPK 文件名加 _unpacked",
    )

    args = parser.parse_args()

    output_dir = args.output
    if output_dir is None:
        output_dir = args.dpk.with_name(
            args.dpk.stem + "_unpacked"
        )

    extract_all(args.dpk, output_dir)


if __name__ == "__main__":
    main()