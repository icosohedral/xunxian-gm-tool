"""Read one resource from a whpackage1.0 DPK archive."""

import hashlib
import struct
import zlib


BLOCK_SIZE = 1024
PACKAGE_KEY = b"tianxiaw"
CONTENT_KEY = b"gandiaos"


def _u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise ValueError("DPK uint32 字段越界")
    return struct.unpack_from("<I", data, offset)[0]


def _decode_whsc(data):
    if len(data) < 48 or data[:7] != b"whsc1.0":
        raise ValueError("DPK 资源流不是 WHSC 格式")
    flags = data[15]
    expected_md5 = data[16:32]
    reserved = _u32(data, 32)
    original_size = _u32(data, 36)
    payload_size = _u32(data, 40)
    if reserved != 0 or payload_size > len(data) - 48:
        raise ValueError("WHSC 头部损坏")
    payload = data[48:48 + payload_size]

    if flags & 0x02:
        from Crypto.Cipher import DES
        key = CONTENT_KEY if flags & 0x04 else PACKAGE_KEY
        full_length = len(payload) // 8 * 8
        output = bytearray(len(payload))
        previous_cipher_block = bytearray(8)
        if full_length:
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
            for index, cipher_byte in enumerate(tail):
                plain_byte = (cipher_byte ^ key[index] ^ previous_tail_byte ^
                              previous_cipher_block[index])
                output[full_length + index] = plain_byte
                previous_tail_byte = cipher_byte
        payload = bytes(output)

    if flags & 0x01:
        try:
            payload = zlib.decompress(payload)
        except zlib.error as exc:
            raise ValueError(f"WHSC zlib 解压失败：{exc}") from exc
    if len(payload) != original_size:
        raise ValueError("WHSC 解压长度校验失败")
    if hashlib.md5(payload).digest() != expected_md5:
        raise ValueError("WHSC 内容 MD5 校验失败")
    return payload


def _read_virtual_file(archive, root_block):
    def block(number):
        offset = number * BLOCK_SIZE
        if number < 0 or offset + BLOCK_SIZE > len(archive):
            raise ValueError(f"DPK 块 {number} 超出文件范围")
        return archive[offset:offset + BLOCK_SIZE]

    root = block(root_block)
    previous = _u32(root, 0)
    next_block = _u32(root, 4)
    tail_block = _u32(root, 8)
    total_size = _u32(root, 12)
    if previous != 0:
        raise ValueError(f"DPK 块 {root_block} 不是虚拟文件根块")

    output = bytearray(root[32:32 + min(total_size, 992)])
    current = root_block
    visited = {root_block}
    while next_block != 0 and len(output) < total_size:
        if next_block in visited:
            raise ValueError("DPK 块链形成循环")
        visited.add(next_block)
        current_data = block(next_block)
        if _u32(current_data, 0) != current:
            raise ValueError("DPK 块链断裂")
        remaining = total_size - len(output)
        output.extend(current_data[8:8 + min(remaining, 1016)])
        current = next_block
        next_block = _u32(current_data, 4)
    if current != tail_block or len(output) != total_size:
        raise ValueError("DPK 虚拟文件链长度或尾块校验失败")
    return bytes(output)


def extract_dpk_entry(archive, entry_path):
    """Extract a single slash-separated resource from DPK bytes."""
    if len(archive) < BLOCK_SIZE or archive[:12] != b"whpackage1.0":
        raise ValueError("文件不是 whpackage1.0 DPK 包")
    if archive[32:48] != hashlib.md5(b"tianxiawudi").digest():
        raise ValueError("DPK 包头密码校验失败")

    directory_root = _u32(archive, 132)
    strings_root = _u32(archive, 136)
    directory_data = _decode_whsc(_read_virtual_file(archive, directory_root))
    strings_data = _decode_whsc(_read_virtual_file(archive, strings_root))
    strings = {}
    position = 0
    while position < len(strings_data):
        end = strings_data.find(b"\x00", position)
        if end < 0:
            raise ValueError("DPK 路径字符串表损坏")
        strings[position] = strings_data[position:end].decode("ascii")
        position = end + 1

    wanted = entry_path.replace("\\", "/").strip("/").lower()
    directories = []
    position = 0
    while position < len(directory_data):
        record_type = directory_data[position]
        position += 1
        if record_type == 1:
            if position + 8 > len(directory_data):
                raise ValueError("DPK 文件记录被截断")
            string_offset = _u32(directory_data, position)
            root_block = _u32(directory_data, position + 4)
            position += 8
            try:
                name = strings[string_offset]
            except KeyError as exc:
                raise ValueError("DPK 字符串表缺少文件路径") from exc
            path = "/".join([*directories, name])
            if path.replace("\\", "/").lower() == wanted:
                return _decode_whsc(_read_virtual_file(archive, root_block))
        elif record_type == 2:
            if position + 4 > len(directory_data):
                raise ValueError("DPK 目录记录被截断")
            string_offset = _u32(directory_data, position)
            position += 4
            try:
                directories.append(strings[string_offset])
            except KeyError as exc:
                raise ValueError("DPK 字符串表缺少目录路径") from exc
        elif record_type == 3:
            if not directories:
                raise ValueError("DPK 目录树不平衡")
            directories.pop()
        else:
            raise ValueError(f"未知的 DPK 目录记录：{record_type}")
    raise FileNotFoundError(f"DPK 中不存在 {entry_path}")
