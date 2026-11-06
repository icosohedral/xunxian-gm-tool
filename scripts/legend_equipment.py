"""Build legend equipment definitions by copying a verified template."""
from pathlib import Path
from .unpack import DpkReader
from .repack import replace_resource
from .weapon_custom import append_weapon, verify_weapon, all_item_ids

LEGEND_LIST = 'life/legend_equip/legend_equip_list.txt'


def legend_row(package, item_id):
    with DpkReader(Path(package)) as reader:
        entry = next((e for e in reader.read_entries() if e.path.lower() == LEGEND_LIST), None)
        if entry is None:
            raise ValueError('客户端或主包缺少传奇装备列表')
        raw = reader.extract(entry)
    rows = [line.split('\t') for line in raw.decode('gb18030').splitlines() if line.split('\t', 1)[0] == str(item_id)]
    if len(rows) != 1:
        raise ValueError(f'传奇装备模板 {item_id} 匹配到 {len(rows)} 条记录')
    return raw, rows[0]


def append_legend(package, output, template_id, item_id):
    raw, row = legend_row(package, template_id)
    try:
        legend_row(package, item_id)
    except ValueError as exc:
        if '匹配到 0 条记录' not in str(exc):
            raise
    else:
        raise ValueError('传奇装备 ID 已存在')
    row[0] = str(item_id)
    text = raw.decode('gb18030')
    newline = '\r\n' if '\r\n' in text else '\n'
    if text and not text.endswith(('\r', '\n')):
        text += newline
    replace_resource(Path(package), LEGEND_LIST, None, Path(output), line_edit=None,
        replacement_data=(text + '\t'.join(row) + newline).encode('gb18030'),
        compression_level=6, zlib_mem_level=8, zlib_strategy='default', auto_zlib_parameters=True,
        allow_chain_growth=True, allow_chain_shrink=True, overwrite_output=False,
        overwrite_report=False, max_input_size=2*1024*1024*1024, report_path=None)
    _, actual = legend_row(output, item_id)
    if actual != row:
        raise ValueError('传奇装备写入后回读失败')


def create_legend(source, intermediate, output, template_id, item_id, fields, *, dynamic=False):
    if item_id in all_item_ids(source):
        raise ValueError('物品 ID 已存在')
    append_weapon(Path(source), Path(intermediate), template_id, item_id, fields,
                  fallback_fields=fields if dynamic else None)
    verify_weapon(Path(intermediate), item_id, fields)
    if dynamic:
        Path(intermediate).replace(output)
    else:
        append_legend(intermediate, output, template_id, item_id)
        verify_weapon(Path(output), item_id, fields)
