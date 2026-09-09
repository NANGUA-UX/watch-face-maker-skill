"""表盘名称校验及只改名称的迁移；调用者必须先限定到目标分区。"""
import re


def validate_names(name_zh, name_en):
    errors = []
    if not isinstance(name_zh, str) or not re.fullmatch(r'[\u3400-\u9fff]{1,4}', name_zh):
        errors.append('中文名须为1–4个中文字符')
    if not isinstance(name_en, str) or not 1 <= len(name_en) <= 12 or not re.fullmatch(r'[A-Za-z0-9]+(?: [A-Za-z0-9]+)*', name_en):
        errors.append('英文名须为1–12个英文字母、数字或单个内部空格，空格计入长度')
    return errors


def rename_path(value, old_prefix, new_prefix):
    return new_prefix + value[len(old_prefix):] if value.startswith(old_prefix + '/') else value


def plan_rename(nodes, section_id, old_prefix, name_zh, name_en, *, chosen=False, occupied=()):
    errors = validate_names(name_zh, name_en)
    if not chosen:
        errors.append('用户尚未选名，不能迁移正式名称')
    section_name = f'{name_zh}-{name_en}'
    if any(n.casefold() == name_en.casefold() or n.casefold().startswith(name_en.casefold() + '/') or n.casefold().endswith('-' + name_en.casefold()) or n.startswith(name_zh + '-') for n in occupied):
        errors.append('目标文件或项目记录存在重名，请重新选名')
    ids = [n['id'] for n in nodes]
    if section_id not in ids or len(ids) != len(set(ids)):
        errors.append('分区缺失或节点ID重复')
    if errors:
        raise ValueError('；'.join(errors))
    edits = []
    for node in nodes:
        old = node['name']
        new = section_name if node['id'] == section_id else rename_path(old, old_prefix, name_en)
        if old != new:
            edits.append({'id': node['id'], 'before': old, 'after': new})
    return edits


def verify_rename(before, after, edits):
    expected = {n['id']: dict(n) for n in before}
    for edit in edits:
        expected[edit['id']]['name'] = edit['after']
    actual = {n['id']: n for n in after}
    errors = []
    if len(actual) != len(after) or set(expected) != set(actual):
        errors.append('节点ID集合变化或重复')
    for node_id in expected.keys() & actual.keys():
        if expected[node_id] != actual[node_id]:
            errors.append(f'节点名称或设计属性不符合迁移计划：{node_id}')
    return errors


def migrate_config(config, old_prefix, name_zh, name_en):
    errors = validate_names(name_zh, name_en)
    if errors:
        raise ValueError('；'.join(errors))
    def visit(value):
        if isinstance(value, dict):
            return {k: (name_en if k == 'prefix' and v == old_prefix else visit(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(v) for v in value]
        return rename_path(value, old_prefix, name_en) if isinstance(value, str) else value
    result = visit(config)
    result.update(name_zh=name_zh, name_en=name_en, prefix=name_en)
    return result
