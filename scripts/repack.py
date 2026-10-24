from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import struct
import tempfile
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Sequence

from Crypto.Cipher import DES

try:
    from .unpack import (
        BLOCK_SIZE,
        CONTENT_KEY,
        PACKAGE_KEY,
        DpkEntry,
        DpkReader,
        decode_whsc,
        decrypt_payload,
        read_exact_at,
        read_u32,
    )
except ImportError:  # Allow `python scripts/repack.py` from the project root.
    from unpack import (
        BLOCK_SIZE,
        CONTENT_KEY,
        PACKAGE_KEY,
        DpkEntry,
        DpkReader,
        decode_whsc,
        decrypt_payload,
        read_exact_at,
        read_u32,
    )


ROOT_DATA_OFFSET = 32
ROOT_DATA_CAPACITY = BLOCK_SIZE - ROOT_DATA_OFFSET
NEXT_DATA_OFFSET = 8
NEXT_DATA_CAPACITY = BLOCK_SIZE - NEXT_DATA_OFFSET
WHSC_HEADER_SIZE = 48
SUPPORTED_FLAGS_MASK = 0x07
DEFAULT_MAX_INPUT_SIZE = 2 * 1024 * 1024 * 1024
ZLIB_STRATEGIES = {
    "default": zlib.Z_DEFAULT_STRATEGY,
    "filtered": zlib.Z_FILTERED,
    "huffman": zlib.Z_HUFFMAN_ONLY,
    "rle": zlib.Z_RLE,
}


class RepackError(Exception):
    """Base error for a safe DPK replacement operation."""


@dataclass(frozen=True)
class BlockChain:
    root_block: int
    tail_block: int
    virtual_size: int
    block_numbers: tuple[int, ...]

    @property
    def capacity(self) -> int:
        return ROOT_DATA_CAPACITY + NEXT_DATA_CAPACITY * (
            len(self.block_numbers) - 1
        )


@dataclass(frozen=True)
class WhscInfo:
    flags: int
    original_size: int
    payload_size: int


@dataclass(frozen=True)
class ZlibParameters:
    level: int
    mem_level: int
    strategy: str
    whsc_size: int


@dataclass(frozen=True)
class LineFieldEdit:
    line_number: int
    old_value: str
    new_value: str
    encoding: str


@dataclass(frozen=True)
class PatchReport:
    archive: str
    output: str
    entry: str
    root_block: int
    flags: int
    compression_level: int | None
    zlib_mem_level: int | None
    zlib_strategy: str | None
    source_mode: str
    replacement_file: str | None
    edited_line: int | None
    field_before: str | None
    field_after: str | None
    text_encoding: str | None
    field_edits: tuple[LineFieldEdit, ...]
    original_plain_size: int
    new_plain_size: int
    original_whsc_size: int
    new_whsc_size: int
    original_blocks: tuple[int, ...]
    used_blocks: tuple[int, ...]
    detached_blocks: tuple[int, ...]
    original_sha256: str
    new_sha256: str
    output_size: int
    entries_verified: int
    decoded_bytes_verified: int
    verified: bool


def write_u32(data: bytearray, offset: int, value: int) -> None:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"写入 uint32 越界：offset={offset}")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"uint32 数值越界：value={value}")
    struct.pack_into("<I", data, offset, value)


def required_block_count(virtual_size: int) -> int:
    if virtual_size < 0:
        raise ValueError("虚拟文件长度不能为负数")
    if virtual_size <= ROOT_DATA_CAPACITY:
        return 1
    remaining = virtual_size - ROOT_DATA_CAPACITY
    return 1 + (
        remaining + NEXT_DATA_CAPACITY - 1
    ) // NEXT_DATA_CAPACITY


def parse_whsc_info(data: bytes) -> WhscInfo:
    if len(data) < WHSC_HEADER_SIZE or data[:7] != b"whsc1.0":
        raise RepackError("目标资源不是有效的 WHSC 1.0 数据流")

    flags = data[15]
    unsupported = flags & ~SUPPORTED_FLAGS_MASK
    if unsupported:
        raise RepackError(
            f"目标 WHSC 使用了未知 flags 位：0x{unsupported:02X}"
        )
    if flags & 0x04 and not flags & 0x02:
        raise RepackError(
            "目标 WHSC 设置了 ContentKey 位，但没有设置加密位"
        )

    reserved = read_u32(data, 32)
    original_size = read_u32(data, 36)
    payload_size = read_u32(data, 40)

    if reserved != 0:
        raise RepackError("目标 WHSC 的保留字段不为 0")
    if payload_size > len(data) - WHSC_HEADER_SIZE:
        raise RepackError("目标 WHSC 的 Payload 长度越界")
    if WHSC_HEADER_SIZE + payload_size != len(data):
        raise RepackError("目标 WHSC 包含未识别的尾随数据")

    return WhscInfo(
        flags=flags,
        original_size=original_size,
        payload_size=payload_size,
    )


def encrypt_payload(payload: bytes, key: bytes) -> bytes:
    """
    Encrypt a WHSC payload and verify it with the known-good decoder.

    Complete blocks use DES-CBC with a zero IV and no padding. The final
    1-7 bytes are recovered by enumerating the at most eight mirror-swap
    states accepted by the original tail decoder.
    """
    if len(key) != 8:
        raise ValueError("DES 密钥必须正好为 8 字节")

    full_length = len(payload) // 8 * 8
    plain_full = payload[:full_length]
    plain_tail = payload[full_length:]

    if plain_full:
        cipher = DES.new(key, DES.MODE_CBC, iv=b"\x00" * 8)
        cipher_full = cipher.encrypt(plain_full)
        previous_cipher_block = cipher_full[-8:]
    else:
        cipher_full = b""
        previous_cipher_block = b"\x00" * 8

    if not plain_tail:
        result = cipher_full
    else:
        transformed_tail = bytearray(len(plain_tail))
        previous_transformed_byte = 0

        for index, plain_byte in enumerate(plain_tail):
            transformed_byte = (
                plain_byte
                ^ key[index]
                ^ previous_transformed_byte
                ^ previous_cipher_block[index]
            )
            transformed_tail[index] = transformed_byte
            previous_transformed_byte = transformed_byte

        pair_count = len(transformed_tail) // 2
        cipher_tail: bytes | None = None

        for swap_mask in range(1 << pair_count):
            candidate = bytearray(transformed_tail)

            for left in range(pair_count):
                if swap_mask & (1 << left):
                    right = len(candidate) - left - 1
                    candidate[left], candidate[right] = (
                        candidate[right],
                        candidate[left],
                    )

            test_cipher = cipher_full + bytes(candidate)
            if decrypt_payload(test_cipher, key) == payload:
                cipher_tail = bytes(candidate)
                break

        if cipher_tail is None:
            raise RepackError("无法生成可由原算法解密的尾部密文")

        result = cipher_full + cipher_tail

    if decrypt_payload(result, key) != payload:
        raise RepackError("Payload 加密往返自校验失败")

    return result


def compress_zlib(
    plain: bytes,
    *,
    level: int,
    mem_level: int,
    strategy: str,
) -> bytes:
    if not 0 <= level <= 9:
        raise ValueError("zlib 压缩级别必须在 0 到 9 之间")
    if not 1 <= mem_level <= 9:
        raise ValueError("zlib memLevel 必须在 1 到 9 之间")
    try:
        strategy_value = ZLIB_STRATEGIES[strategy]
    except KeyError as exc:
        choices = ", ".join(ZLIB_STRATEGIES)
        raise ValueError(
            f"未知 zlib strategy：{strategy}；可选值：{choices}"
        ) from exc

    compressor = zlib.compressobj(
        level,
        zlib.DEFLATED,
        zlib.MAX_WBITS,
        mem_level,
        strategy_value,
    )
    return compressor.compress(plain) + compressor.flush()


def encode_whsc(
    plain: bytes,
    *,
    flags: int,
    template_header: bytes,
    compression_level: int,
    zlib_mem_level: int = 8,
    zlib_strategy: str = "default",
) -> bytes:
    if len(template_header) != WHSC_HEADER_SIZE:
        raise ValueError("WHSC 模板头必须正好为 48 字节")
    unsupported = flags & ~SUPPORTED_FLAGS_MASK
    if unsupported:
        raise RepackError(
            f"无法编码未知 WHSC flags 位：0x{unsupported:02X}"
        )
    if flags & 0x04 and not flags & 0x02:
        raise RepackError(
            "ContentKey 位只能与加密位一起使用"
        )

    payload = plain
    if flags & 0x01:
        payload = compress_zlib(
            payload,
            level=compression_level,
            mem_level=zlib_mem_level,
            strategy=zlib_strategy,
        )

    if flags & 0x02:
        key = CONTENT_KEY if flags & 0x04 else PACKAGE_KEY
        payload = encrypt_payload(payload, key)

    header = bytearray(template_header)
    header[0:7] = b"whsc1.0"
    header[15] = flags
    header[16:32] = hashlib.md5(plain).digest()
    write_u32(header, 32, 0)
    write_u32(header, 36, len(plain))
    write_u32(header, 40, len(payload))

    result = bytes(header) + payload
    if decode_whsc(result) != plain:
        raise RepackError("新 WHSC 编码往返自校验失败")
    return result


def read_block(reader: DpkReader, block_number: int) -> bytes:
    if block_number <= 0:
        raise RepackError(f"非法数据块编号：{block_number}")

    offset = block_number * BLOCK_SIZE
    if offset + BLOCK_SIZE > reader.file_size:
        raise RepackError(
            f"块 {block_number} 超出 DPK 文件范围"
        )
    return read_exact_at(reader.fp, offset, BLOCK_SIZE)


def read_block_chain(
    reader: DpkReader,
    root_block: int,
) -> BlockChain:
    root = read_block(reader, root_block)
    if read_u32(root, 0) != 0:
        raise RepackError(f"块 {root_block} 不是根块")

    tail_block = read_u32(root, 8)
    virtual_size = read_u32(root, 12)
    current = root_block
    next_block = read_u32(root, 4)
    block_numbers = [root_block]
    visited = {root_block}

    while next_block != 0:
        if next_block in visited:
            raise RepackError(
                f"块链在块 {next_block} 形成循环"
            )

        block = read_block(reader, next_block)
        previous = read_u32(block, 0)
        if previous != current:
            raise RepackError(
                f"块链 {current} -> {next_block} 的前驱字段错误"
            )

        visited.add(next_block)
        block_numbers.append(next_block)
        current = next_block
        next_block = read_u32(block, 4)

    if current != tail_block:
        raise RepackError(
            f"根块 {root_block} 的尾块字段与实际链尾不符"
        )

    chain = BlockChain(
        root_block=root_block,
        tail_block=tail_block,
        virtual_size=virtual_size,
        block_numbers=tuple(block_numbers),
    )

    if virtual_size > chain.capacity:
        raise RepackError(
            f"根块 {root_block} 声明的虚拟文件超过块链容量"
        )

    expected_blocks = required_block_count(virtual_size)
    if expected_blocks != len(block_numbers):
        raise RepackError(
            f"根块 {root_block} 的虚拟长度需要 {expected_blocks} 个块，"
            f"实际链包含 {len(block_numbers)} 个块"
        )

    return chain


def normalize_archive_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts: list[str] = []

    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise RepackError("包内路径不能包含 '..'")
        parts.append(part)

    if not parts:
        raise RepackError("包内路径不能为空")
    return "/".join(parts)


def edit_tab_delimited_field(
    data: bytes,
    edit: LineFieldEdit,
) -> bytes:
    return edit_tab_delimited_fields(data, (edit,))


def edit_tab_delimited_fields(
    data: bytes,
    edits: Sequence[LineFieldEdit],
) -> bytes:
    if not edits:
        raise RepackError("至少需要一项字段编辑")

    lines = data.splitlines(keepends=True)

    for edit in edits:
        _edit_one_tab_delimited_field(lines, edit)

    result = b"".join(lines)
    if result == data:
        raise RepackError("字段替换后资源内容没有变化")
    return result


def _edit_one_tab_delimited_field(
    lines: list[bytes],
    edit: LineFieldEdit,
) -> None:
    if edit.line_number < 1:
        raise RepackError("行号必须大于或等于 1")
    if not edit.old_value:
        raise RepackError("被替换字段不能为空")

    try:
        old_bytes = edit.old_value.encode(edit.encoding)
        new_bytes = edit.new_value.encode(edit.encoding)
    except (LookupError, UnicodeEncodeError) as exc:
        raise RepackError(
            f"字段无法使用编码 {edit.encoding} 表示：{exc}"
        ) from exc

    for label, value in (
        ("原字段", old_bytes),
        ("新字段", new_bytes),
    ):
        if b"\t" in value or b"\r" in value or b"\n" in value:
            raise RepackError(f"{label}不能包含制表符或换行符")

    if edit.line_number > len(lines):
        raise RepackError(
            f"资源只有 {len(lines)} 行，无法修改第 "
            f"{edit.line_number} 行"
        )

    index = edit.line_number - 1
    line = lines[index]
    if line.endswith(b"\r\n"):
        body, ending = line[:-2], b"\r\n"
    elif line.endswith((b"\n", b"\r")):
        body, ending = line[:-1], line[-1:]
    else:
        body, ending = line, b""

    fields = body.split(b"\t")
    matches = [
        field_index
        for field_index, field in enumerate(fields)
        if field == old_bytes
    ]
    if len(matches) != 1:
        raise RepackError(
            f"第 {edit.line_number} 行中，完整字段 "
            f"{edit.old_value!r} 的匹配数为 {len(matches)}，"
            "要求正好匹配一次"
        )

    fields[matches[0]] = new_bytes
    lines[index] = b"\t".join(fields) + ending


def topology_preserving_compression_levels(
    plain: bytes,
    chain: BlockChain,
) -> list[int]:
    levels: list[int] = []

    for level in range(10):
        whsc_size = WHSC_HEADER_SIZE + len(
            compress_zlib(
                plain,
                level=level,
                mem_level=8,
                strategy="default",
            )
        )
        if (
            whsc_size <= chain.capacity
            and required_block_count(whsc_size)
            == len(chain.block_numbers)
        ):
            levels.append(level)

    return levels


def find_topology_preserving_zlib_parameters(
    plain: bytes,
    chain: BlockChain,
    *,
    target_whsc_size: int,
) -> ZlibParameters | None:
    candidates: list[tuple[tuple[int, ...], ZlibParameters]] = []

    for level in range(1, 10):
        for mem_level in range(1, 10):
            for strategy in ZLIB_STRATEGIES:
                payload = compress_zlib(
                    plain,
                    level=level,
                    mem_level=mem_level,
                    strategy=strategy,
                )
                whsc_size = WHSC_HEADER_SIZE + len(payload)

                if whsc_size > chain.capacity:
                    continue
                if (
                    required_block_count(whsc_size)
                    != len(chain.block_numbers)
                ):
                    continue

                parameters = ZlibParameters(
                    level=level,
                    mem_level=mem_level,
                    strategy=strategy,
                    whsc_size=whsc_size,
                )
                rank = (
                    abs(whsc_size - target_whsc_size),
                    0 if strategy == "default" else 1,
                    abs(level - 6),
                    abs(mem_level - 8),
                    level,
                    mem_level,
                )
                candidates.append((rank, parameters))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def format_compression_hint(levels: list[int]) -> str:
    if not levels:
        return ""
    values = ", ".join(str(level) for level in levels)
    return f" 可保持原块数的压缩级别：{values}。"


def find_unique_entry(
    entries: list[DpkEntry],
    archive_path: str,
) -> DpkEntry:
    normalized = normalize_archive_path(archive_path)
    matches = [
        entry
        for entry in entries
        if normalize_archive_path(entry.path) == normalized
    ]

    if not matches:
        raise RepackError(f"DPK 中不存在资源：{normalized}")
    if len(matches) != 1:
        raise RepackError(
            f"DPK 中存在 {len(matches)} 个同名资源：{normalized}"
        )
    return matches[0]


def check_target_chain_ownership(
    reader: DpkReader,
    entries: list[DpkEntry],
    target: DpkEntry,
) -> BlockChain:
    directory_root = read_u32(reader.header, 132)
    strings_root = read_u32(reader.header, 136)
    roots: list[tuple[str, int]] = [
        ("<directory>", directory_root),
        ("<strings>", strings_root),
    ]
    roots.extend((entry.path, entry.root_block) for entry in entries)

    chains_by_root: dict[int, BlockChain] = {}
    owners_by_block: dict[int, set[str]] = {}

    for owner, root_block in roots:
        chain = chains_by_root.get(root_block)
        if chain is None:
            chain = read_block_chain(reader, root_block)
            chains_by_root[root_block] = chain

        for block_number in chain.block_numbers:
            owners_by_block.setdefault(block_number, set()).add(owner)

    target_chain = chains_by_root[target.root_block]
    conflicts: list[str] = []

    for block_number in target_chain.block_numbers:
        other_owners = owners_by_block[block_number] - {target.path}
        if other_owners:
            conflicts.append(
                f"块 {block_number}: {', '.join(sorted(other_owners))}"
            )

    if conflicts:
        details = "; ".join(conflicts[:10])
        raise RepackError(
            "目标资源块链与其他虚拟文件共享，拒绝修改："
            f"{details}"
        )

    return target_chain


def write_virtual_file(
    fp: BinaryIO,
    chain: BlockChain,
    data: bytes,
    *,
    allow_chain_shrink: bool,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    needed = required_block_count(len(data))
    available = len(chain.block_numbers)

    if needed > available:
        raise RepackError(
            f"新 WHSC 需要 {needed} 个块，原链只有 {available} 个块；"
            "安全原位模式不会追加块"
        )
    if needed < available and not allow_chain_shrink:
        raise RepackError(
            f"新 WHSC 只需要 {needed} 个块，原链有 {available} 个块；"
            "默认拒绝改变块链拓扑。可调整压缩级别，或显式使用 "
            "--allow-chain-shrink"
        )

    used = chain.block_numbers[:needed]
    detached = chain.block_numbers[needed:]
    position = 0

    root_number = used[0]
    root = bytearray(read_exact_at(
        fp,
        root_number * BLOCK_SIZE,
        BLOCK_SIZE,
    ))
    write_u32(root, 0, 0)
    write_u32(root, 4, used[1] if len(used) > 1 else 0)
    write_u32(root, 8, used[-1])
    write_u32(root, 12, len(data))
    root[ROOT_DATA_OFFSET:] = b"\x00" * ROOT_DATA_CAPACITY

    root_chunk = data[:ROOT_DATA_CAPACITY]
    root[
        ROOT_DATA_OFFSET:ROOT_DATA_OFFSET + len(root_chunk)
    ] = root_chunk
    position += len(root_chunk)
    fp.seek(root_number * BLOCK_SIZE)
    fp.write(root)

    for index, block_number in enumerate(used[1:], start=1):
        block = bytearray(read_exact_at(
            fp,
            block_number * BLOCK_SIZE,
            BLOCK_SIZE,
        ))
        write_u32(block, 0, used[index - 1])
        write_u32(
            block,
            4,
            used[index + 1] if index + 1 < len(used) else 0,
        )
        block[NEXT_DATA_OFFSET:] = b"\x00" * NEXT_DATA_CAPACITY

        chunk = data[position:position + NEXT_DATA_CAPACITY]
        block[
            NEXT_DATA_OFFSET:NEXT_DATA_OFFSET + len(chunk)
        ] = chunk
        position += len(chunk)
        fp.seek(block_number * BLOCK_SIZE)
        fp.write(block)

    if position != len(data):
        raise AssertionError("写入块链后的数据长度不一致")

    return tuple(used), tuple(detached)


def verify_output(
    path: Path,
    *,
    expected_entries: list[DpkEntry],
    target: DpkEntry,
    expected_plain: bytes,
    expected_size: int,
) -> tuple[int, int]:
    if path.stat().st_size != expected_size:
        raise RepackError("输出 DPK 的文件大小发生了变化")

    with DpkReader(path) as reader:
        entries = reader.read_entries()
        before = [
            (entry.path, entry.root_block)
            for entry in expected_entries
        ]
        after = [(entry.path, entry.root_block) for entry in entries]
        if after != before:
            raise RepackError("输出 DPK 的目录条目或 RootBlock 发生变化")

        decoded_bytes = 0
        target_verified = False

        for entry in entries:
            data = reader.extract(entry)
            decoded_bytes += len(data)

            if (
                entry.path == target.path
                and entry.root_block == target.root_block
            ):
                if data != expected_plain:
                    raise RepackError("目标资源写后逐字节校验失败")
                target_verified = True

        if not target_verified:
            raise RepackError("写后校验时未找到目标资源")

    return len(entries), decoded_bytes


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as fp:
            temp_name = fp.name
            json.dump(data, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())

        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def replace_resource(
    archive: Path,
    archive_path: str,
    replacement: Path | None,
    output: Path,
    *,
    line_edit: LineFieldEdit | None,
    compression_level: int,
    zlib_mem_level: int,
    zlib_strategy: str,
    auto_zlib_parameters: bool,
    allow_chain_shrink: bool,
    overwrite_output: bool,
    overwrite_report: bool,
    max_input_size: int,
    report_path: Path | None,
    replacement_data: bytes | None = None,
    line_edits: Sequence[LineFieldEdit] | None = None,
) -> PatchReport:
    archive = archive.resolve()
    resolved_replacement = (
        replacement.resolve()
        if replacement is not None
        else None
    )
    output = output.resolve()
    resolved_report = (
        report_path.resolve()
        if report_path is not None
        else None
    )

    if line_edit is not None and line_edits is not None:
        raise RepackError(
            "line_edit 与 line_edits 只能提供一种"
        )
    effective_line_edits = (
        tuple(line_edits)
        if line_edits is not None
        else ((line_edit,) if line_edit is not None else None)
    )

    if not archive.is_file():
        raise RepackError(f"输入 DPK 不存在或不是文件：{archive}")
    source_count = sum(
        source is not None
        for source in (
            resolved_replacement,
            effective_line_edits,
            replacement_data,
        )
    )
    if source_count == 0:
        raise RepackError(
            "必须提供替换文件、内存数据或行字段编辑参数"
        )
    if source_count != 1:
        raise RepackError(
            "替换文件、内存数据与行字段编辑模式只能选择一种"
        )
    if (
        resolved_replacement is not None
        and not resolved_replacement.is_file()
    ):
        raise RepackError(
            f"替换资源不存在或不是文件：{resolved_replacement}"
        )
    if output == archive:
        raise RepackError("拒绝直接覆盖原 DPK，请指定另一个输出文件")
    if output == resolved_replacement:
        raise RepackError("输出 DPK 不能覆盖替换资源文件")
    if output.exists() and not output.is_file():
        raise RepackError(f"输出路径已存在且不是文件：{output}")
    if output.exists() and not overwrite_output:
        raise RepackError(
            f"输出文件已存在：{output}；"
            "如需替换请使用 --overwrite-output"
        )
    if max_input_size < 0:
        raise RepackError("--max-input-size 不能为负数")
    if archive.stat().st_size % BLOCK_SIZE != 0:
        raise RepackError("输入 DPK 大小不是 1024 字节的整数倍")

    if resolved_report is not None:
        protected_paths = {archive, output}
        if resolved_replacement is not None:
            protected_paths.add(resolved_replacement)
        if resolved_report in protected_paths:
            raise RepackError(
                "补丁报告路径不能与输入 DPK、替换资源或"
                "输出 DPK 相同"
            )
        if (
            resolved_report.exists()
            and not resolved_report.is_file()
        ):
            raise RepackError(
                f"报告路径已存在且不是文件：{resolved_report}"
            )
        if resolved_report.exists() and not overwrite_report:
            raise RepackError(
                f"报告文件已存在：{resolved_report}；"
                "如需替换请使用 --overwrite-report"
            )

    archive_size = archive.stat().st_size

    with DpkReader(archive) as reader:
        entries = reader.read_entries()
        target = find_unique_entry(entries, archive_path)
        chain = check_target_chain_ownership(reader, entries, target)
        original_whsc = reader.read_virtual_file(target.root_block)
        original_info = parse_whsc_info(original_whsc)
        original_plain = decode_whsc(original_whsc)

    if resolved_replacement is not None:
        replacement_size = resolved_replacement.stat().st_size
        if replacement_size > max_input_size:
            raise RepackError(
                f"替换文件大小 {replacement_size} 超过上限 "
                f"{max_input_size}"
            )
        new_plain = resolved_replacement.read_bytes()
        source_mode = "replacement_file"
    elif replacement_data is not None:
        new_plain = bytes(replacement_data)
        source_mode = "memory_data"
    else:
        assert effective_line_edits is not None
        new_plain = edit_tab_delimited_fields(
            original_plain,
            effective_line_edits,
        )
        source_mode = "line_field_edit"

    if len(new_plain) > max_input_size:
        raise RepackError("新资源大小超过了允许上限")

    new_whsc = encode_whsc(
        new_plain,
        flags=original_info.flags,
        template_header=original_whsc[:WHSC_HEADER_SIZE],
        compression_level=compression_level,
        zlib_mem_level=zlib_mem_level,
        zlib_strategy=zlib_strategy,
    )
    needed = required_block_count(len(new_whsc))

    if (
        original_info.flags & 0x01
        and auto_zlib_parameters
        and needed != len(chain.block_numbers)
    ):
        selected = find_topology_preserving_zlib_parameters(
            new_plain,
            chain,
            target_whsc_size=len(original_whsc),
        )
        if selected is not None:
            compression_level = selected.level
            zlib_mem_level = selected.mem_level
            zlib_strategy = selected.strategy
            new_whsc = encode_whsc(
                new_plain,
                flags=original_info.flags,
                template_header=original_whsc[:WHSC_HEADER_SIZE],
                compression_level=compression_level,
                zlib_mem_level=zlib_mem_level,
                zlib_strategy=zlib_strategy,
            )
            needed = required_block_count(len(new_whsc))

    preserving_levels = (
        topology_preserving_compression_levels(new_plain, chain)
        if original_info.flags & 0x01
        else []
    )
    compression_hint = format_compression_hint(preserving_levels)

    if len(new_whsc) > chain.capacity:
        raise RepackError(
            f"新 WHSC 为 {len(new_whsc)} 字节，原块链容量只有 "
            f"{chain.capacity} 字节；安全原位模式不会追加块。"
            f"{compression_hint}"
        )
    if needed < len(chain.block_numbers) and not allow_chain_shrink:
        raise RepackError(
            f"新 WHSC 从 {len(original_whsc)} 字节变为 "
            f"{len(new_whsc)} 字节，所需块数从 "
            f"{len(chain.block_numbers)} 降为 {needed}。"
            "默认拒绝产生孤立块；可调整压缩级别，或显式使用 "
            f"--allow-chain-shrink。{compression_hint}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as fp:
            temporary = Path(fp.name)

        shutil.copy2(archive, temporary)
        original_mode = stat.S_IMODE(archive.stat().st_mode)
        temporary.chmod(original_mode | stat.S_IWUSR)

        with temporary.open("r+b") as fp:
            used_blocks, detached_blocks = write_virtual_file(
                fp,
                chain,
                new_whsc,
                allow_chain_shrink=allow_chain_shrink,
            )
            fp.flush()
            os.fsync(fp.fileno())

        temporary.chmod(original_mode)
        entries_verified, decoded_bytes_verified = verify_output(
            temporary,
            expected_entries=entries,
            target=target,
            expected_plain=new_plain,
            expected_size=archive_size,
        )

        os.replace(temporary, output)
        temporary = None

    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    report = PatchReport(
        archive=str(archive),
        output=str(output),
        entry=target.path,
        root_block=target.root_block,
        flags=original_info.flags,
        compression_level=(
            compression_level
            if original_info.flags & 0x01
            else None
        ),
        zlib_mem_level=(
            zlib_mem_level
            if original_info.flags & 0x01
            else None
        ),
        zlib_strategy=(
            zlib_strategy
            if original_info.flags & 0x01
            else None
        ),
        source_mode=source_mode,
        replacement_file=(
            str(resolved_replacement)
            if resolved_replacement is not None
            else None
        ),
        edited_line=(
            effective_line_edits[0].line_number
            if effective_line_edits
            else None
        ),
        field_before=(
            effective_line_edits[0].old_value
            if effective_line_edits
            else None
        ),
        field_after=(
            effective_line_edits[0].new_value
            if effective_line_edits
            else None
        ),
        text_encoding=(
            effective_line_edits[0].encoding
            if effective_line_edits
            else None
        ),
        field_edits=(
            effective_line_edits
            if source_mode == "line_field_edit"
            else ()
        ),
        original_plain_size=len(original_plain),
        new_plain_size=len(new_plain),
        original_whsc_size=len(original_whsc),
        new_whsc_size=len(new_whsc),
        original_blocks=chain.block_numbers,
        used_blocks=used_blocks,
        detached_blocks=detached_blocks,
        original_sha256=hashlib.sha256(original_plain).hexdigest(),
        new_sha256=hashlib.sha256(new_plain).hexdigest(),
        output_size=output.stat().st_size,
        entries_verified=entries_verified,
        decoded_bytes_verified=decoded_bytes_verified,
        verified=True,
    )

    if resolved_report is not None:
        atomic_write_json(
            resolved_report,
            asdict(report),
        )

    return report


def default_output_path(archive: Path) -> Path:
    return archive.with_name(
        f"{archive.stem}.patched{archive.suffix}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "安全替换 whpackage1.0 DPK 中的单个现有资源；"
            "只复用原块链，不追加块，不直接覆盖原包"
        )
    )
    parser.add_argument("dpk", type=Path, help="输入 DPK 文件")
    parser.add_argument("archive_path", help="资源在 DPK 内的路径")
    parser.add_argument(
        "replacement",
        type=Path,
        nargs="?",
        help=(
            "用于替换的原始资源文件；使用 --line 和 "
            "--replace-field 时省略"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="输出 DPK；默认生成 <原名>.patched.dpk",
    )
    parser.add_argument(
        "--line",
        type=int,
        help="直接编辑包内资源时的 1-based 行号",
    )
    parser.add_argument(
        "--replace-field",
        nargs=2,
        action="append",
        metavar=("OLD", "NEW"),
        help=(
            "在指定行中将一个完整的制表符字段 OLD 替换为 NEW；"
            "可重复指定多个，所有字段都必须正好匹配一次"
        ),
    )
    parser.add_argument(
        "--text-encoding",
        default="gb18030",
        help="行字段编辑所用字符编码，默认 gb18030",
    )
    parser.add_argument(
        "--compression-level",
        type=int,
        choices=range(0, 10),
        default=6,
        metavar="0-9",
        help="原资源使用 zlib 时的压缩级别，默认 6",
    )
    parser.add_argument(
        "--zlib-mem-level",
        type=int,
        choices=range(1, 10),
        default=8,
        metavar="1-9",
        help="zlib 内存级别，默认 8",
    )
    parser.add_argument(
        "--zlib-strategy",
        choices=tuple(ZLIB_STRATEGIES),
        default="default",
        help="zlib 压缩策略，默认 default",
    )
    parser.add_argument(
        "--auto-zlib-parameters",
        action="store_true",
        help=(
            "当前压缩参数会改变块数时，搜索能保持原块数的"
            "标准 zlib 参数；大型文件可能需要十几秒"
        ),
    )
    parser.add_argument(
        "--allow-chain-shrink",
        action="store_true",
        help=(
            "允许缩短逻辑块链；多余物理块会保留，"
            "但不再被目标资源引用"
        ),
    )
    parser.add_argument(
        "--overwrite-output",
        action="store_true",
        help=(
            "允许原子替换已经存在的输出文件；"
            "仍不会覆盖输入 DPK"
        ),
    )
    parser.add_argument(
        "--max-input-size",
        type=int,
        default=DEFAULT_MAX_INPUT_SIZE,
        help="替换文件最大字节数，默认 2 GiB",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="可选的 JSON 补丁报告输出路径",
    )
    parser.add_argument(
        "--overwrite-report",
        action="store_true",
        help="允许原子替换已经存在的 JSON 补丁报告",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    edit_requested = (
        args.line is not None
        or args.replace_field is not None
    )
    if args.replacement is not None and edit_requested:
        parser.error(
            "替换文件不能与 --line/--replace-field 同时使用"
        )
    if args.replacement is None and not edit_requested:
        parser.error(
            "请提供替换文件，或同时提供 --line 和 --replace-field"
        )
    if edit_requested and (
        args.line is None
        or args.replace_field is None
    ):
        parser.error("--line 和 --replace-field 必须同时提供")

    line_edits = None
    if edit_requested:
        line_edits = tuple(
            LineFieldEdit(
                line_number=args.line,
                old_value=old_value,
                new_value=new_value,
                encoding=args.text_encoding,
            )
            for old_value, new_value in args.replace_field
        )

    output = args.output
    if output is None:
        output = default_output_path(args.dpk)

    try:
        report = replace_resource(
            args.dpk,
            args.archive_path,
            args.replacement,
            output,
            line_edit=None,
            line_edits=line_edits,
            compression_level=args.compression_level,
            zlib_mem_level=args.zlib_mem_level,
            zlib_strategy=args.zlib_strategy,
            auto_zlib_parameters=args.auto_zlib_parameters,
            allow_chain_shrink=args.allow_chain_shrink,
            overwrite_output=args.overwrite_output,
            overwrite_report=args.overwrite_report,
            max_input_size=args.max_input_size,
            report_path=args.report,
        )
    except (OSError, ValueError, EOFError, RepackError) as exc:
        parser.exit(1, f"重新打包失败：{exc}\n")

    print(f"替换成功：{report.entry}")
    if report.source_mode == "line_field_edit":
        for edit in report.field_edits:
            print(
                f"字段编辑：第 {edit.line_number} 行，"
                f"{edit.old_value!r} -> {edit.new_value!r}，"
                f"encoding={edit.encoding}"
            )
    print(
        f"明文：{report.original_plain_size} -> "
        f"{report.new_plain_size} bytes"
    )
    print(
        f"WHSC：{report.original_whsc_size} -> "
        f"{report.new_whsc_size} bytes"
    )
    print(
        f"块链：{len(report.original_blocks)} -> "
        f"{len(report.used_blocks)} 个块"
    )
    if report.compression_level is not None:
        print(
            "zlib："
            f"level={report.compression_level}, "
            f"memLevel={report.zlib_mem_level}, "
            f"strategy={report.zlib_strategy}"
        )
    print(
        f"深度校验：{report.entries_verified} 个资源，"
        f"{report.decoded_bytes_verified} bytes，全部通过"
    )
    print(f"输出：{report.output}")
    if args.report is not None:
        print(f"报告：{args.report.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
