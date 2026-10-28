"""Build an entire modification queue locally before publishing any package."""

from pathlib import Path
import shutil

from .profession_skills import inspect_archive as inspect_profession, has_skill, patch_archive_batch
from .ride_speed import configuration_rows, prepare_changes, patch_archive as patch_rides
from .skill_limits import CAREER_GROUP, inspect_dpk_skills, patch_dpk_changes
from .summon_config import SUMMON_SKILL_NAME, load_monster_catalog, load_friend_catalog, patch_summon_dpk
from .weapon_custom import all_item_ids, append_weapon, verify_weapon
from .weapon_drafts import search_traits, weapon_catalog
from .repack import batch_repacking


KINDS = {'weapon', 'profession-skills', 'skill-limits', 'summon', 'ride-speed'}


def validate_items(items):
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('修改队列须包含 1 至 100 个项目')
    ids, kinds = set(), set()
    for item in items:
        if (not isinstance(item, dict) or item.get('kind') not in KINDS
                or not isinstance(item.get('id'), str) or not 1 <= len(item['id']) <= 100
                or item['id'] in ids or not isinstance(item.get('payload'), dict)):
            raise ValueError('修改队列项目格式无效或重复')
        ids.add(item['id'])
        if item['kind'] != 'weapon' and item['kind'] in kinds:
            raise ValueError('同类配置只能在队列中出现一次')
        kinds.add(item['kind'])


class PackageQueueBuilder:
    def __init__(self, sources, directory, *, catalog_path, reference_client, provenance):
        self.directory = Path(directory)
        self.packages = {label: self.directory / (label + '.dpk') for label in sources}
        for label, source in sources.items():
            shutil.copyfile(source, self.packages[label])
        self.catalog_path = Path(catalog_path)
        self.reference_client = Path(reference_client)
        self.provenance = dict(provenance)
        self.summon_config = None
        self.results = []

    def build(self, items):
        with batch_repacking():
            return self._build(items)

    def _build(self, items):
        validate_items(items)
        for index, item in enumerate(items):
            outputs = {label: self.directory / f'step-{index}-{label}.dpk'
                       for label in self.packages}
            try:
                result = getattr(self, '_patch_' + item['kind'].replace('-', '_'))(
                    item['payload'], outputs)
                # Later items always read the results of earlier items, including
                # changes to the same table. Only these temporary packages move.
                for label, output in outputs.items():
                    if output.is_file():
                        output.replace(self.packages[label])
                self.results.append({'id': item['id'], 'kind': item['kind'], **result})
            except Exception as error:
                raise ValueError(f'第 {index + 1} 项修改失败：{error}') from error
        # A later edit must not remove a weapon allocated earlier in this batch.
        for result in self.results:
            if result['kind'] == 'weapon':
                for package in self.packages.values():
                    verify_weapon(package, result['weapon_id'], result['fields'])
        return self.packages

    def _patch_weapon(self, payload, outputs):
        fields = payload.get('fields')
        if not isinstance(fields, list) or len(fields) != 55 or any(not isinstance(v, str) for v in fields):
            raise ValueError('武器草稿字段数或格式无效')
        if not fields[1].strip() or fields[2] != '100' or fields[7] != '10' or fields[18] != '300':
            raise ValueError('武器名称、类型、品质或耐久值无效')
        if fields[9] not in {'', '1', '2', '3', '4'}:
            raise ValueError('职业限制值无效')
        if any(fields[i] and not fields[i].isdigit() for i in (11, 19, 20, 21, 22, 23, 24)):
            raise ValueError('武器等级与属性必须为非负整数')
        if not fields[4].strip() or not fields[5].isdigit():
            raise ValueError('道具图片或模型 ID 无效')
        if any(v and not v.isdigit() for v in fields[25:30]):
            raise ValueError('装备属性 ID 无效')
        trait_ids = {int(v) for v in fields[25:30] if v}
        if len(trait_ids) != sum(bool(v) for v in fields[25:30]):
            raise ValueError('装备属性不能重复')
        client = self.packages['客户端']
        if trait_ids and {row['id'] for row in search_traits(client, ids=trait_ids)['rows']} != trait_ids:
            raise ValueError('部分装备属性 ID 不存在于客户端词条表')
        template_id = int(payload['template_id'])
        icon = fields[4].strip().lower()
        models = weapon_catalog(client, item_id=template_id)['icon_models']
        if not models.get(icon) or models[icon] != fields[5]:
            raise ValueError('模型 ID 与道具图片不匹配，请重新选择图片')
        used = set()
        for package in self.packages.values():
            used.update(all_item_ids(package))
        weapon_id = max(used, default=0) + 1
        if weapon_id > 65535:
            raise ValueError('没有可分配的物品 ID（最大值 65535）')
        fields = list(fields)
        fields[0], fields[4] = str(weapon_id), icon
        for label, source in self.packages.items():
            append_weapon(source, outputs[label], template_id, weapon_id, fields,
                          fallback_fields=fields if label == '动态包' else None)
            verify_weapon(outputs[label], weapon_id, fields)
        return {'weapon_id': weapon_id, 'fields': fields}

    def _patch_profession_skills(self, payload, outputs):
        profession_id = int(payload['profession_id'])
        changes = payload.get('changes')
        if not isinstance(changes, list) or not changes:
            raise ValueError('没有待修改的职业技能')
        ids = [int(sid) for change in changes for sid in change['skill_ids']]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('职业技能 ID 为空或重复')
        valid = {row['id'] for row in inspect_profession(self.packages['客户端'])['skills']
                 if row['profession_id'] == profession_id}
        if not set(ids) <= valid:
            raise ValueError('所选技能不属于当前职业技能列表')
        patch_archive_batch(self.packages['客户端'], outputs['客户端'], profession_id, changes)
        for label in ('主包', '动态包'):
            source = self.packages[label]
            present = []
            for change in changes:
                found = [int(sid) for sid in change['skill_ids'] if has_skill(source, int(sid), profession_id)]
                if found:
                    present.append({**change, 'skill_ids': found})
            if not present:
                if label == '主包':
                    raise ValueError('服务器主包缺少所选技能定义')
                continue
            patch_archive_batch(source, outputs[label], profession_id, present, allow_missing=True)
        return {}

    def _patch_skill_limits(self, payload, outputs):
        target = int(payload['target_profession'])
        raw = payload.get('changes')
        if target not in CAREER_GROUP or not isinstance(raw, list) or not raw:
            raise ValueError('目标职业或技能修改内容无效')
        skills = {label: {row['id']: row for row in inspect_dpk_skills(path)[1]}
                  for label, path in self.packages.items()}
        changes, origins = {}, {}
        for item in raw:
            if not isinstance(item, dict) or type(item.get('assigned')) is not bool:
                raise ValueError('技能修改内容无效')
            sid = int(item['id'])
            client, main = skills['客户端'].get(sid), skills['主包'].get(sid)
            dynamic = skills['动态包'].get(sid)
            if sid in changes or not client or not main:
                raise ValueError(f'技能 {sid} 重复或不属于客户端与主包共有技能')
            if dynamic and main['profession_id'] != dynamic['profession_id']:
                raise ValueError(f'技能 {sid} 的主包与动态包职业不一致')
            origin = int(self.provenance.get(str(sid), client['profession_id']))
            if origin not in CAREER_GROUP or target == origin:
                raise ValueError(f'技能 {sid} 属于目标职业或缺少原职业信息')
            changes[sid], origins[sid] = item['assigned'], origin
        for label, source in self.packages.items():
            missing = set(changes) - skills[label].keys() if label == '动态包' else set()
            data, _ = patch_dpk_changes(source, target, changes, origins, self.directory, missing)
            outputs[label].write_bytes(data)
        for sid, assigned in changes.items():
            if assigned:
                self.provenance[str(sid)] = origins[sid]
            else:
                self.provenance.pop(str(sid), None)
        return {}

    def _patch_summon(self, payload, outputs):
        kind = str(payload.get('summon_type') or 'monster')
        if kind not in {'monster', 'friend', 'catcher'} or not isinstance(payload.get('skills'), list):
            raise ValueError('召唤类型或技能配置无效')
        config = {'summon_type': kind, 'role_id': int(payload['role_id']),
                  'form_id': int(payload['form_id']), 'skill_name': SUMMON_SKILL_NAME,
                  'summon_cooldown': 10,
                  'skills': [{'id': int(item['id']), 'cooldown': int(item['cooldown'])}
                             for item in payload['skills']]}
        client = self.packages['客户端']
        catalog = load_monster_catalog(self.catalog_path, self.reference_client)
        if kind == 'friend':
            catalog += load_friend_catalog(client)
        for label, source in self.packages.items():
            data, _ = patch_summon_dpk(source, outputs[label], config,
                optional_resources=(label == '动态包'), catalog=catalog,
                reference=client if label != '客户端' else None)
            outputs[label].write_bytes(data)
        self.summon_config = config
        return {}

    def _patch_ride_speed(self, payload, outputs):
        rows = configuration_rows(self.packages['客户端'], self.packages['主包'], self.packages['动态包'])
        changes, connections = prepare_changes(rows, payload.get('changes'))
        for label, source in self.packages.items():
            found, _ = patch_rides(source, outputs[label], changes, connections)
            if label != '动态包' and found != set(changes):
                raise ValueError(f'{label}缺少骑宠 RideID：' + ', '.join(map(str, sorted(set(changes) - found))))
        return {}
