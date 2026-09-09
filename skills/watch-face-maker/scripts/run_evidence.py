"""绑定同轮需求、快照和实际文件；保存可恢复断点及改稿影响范围。"""
import hashlib
import json
import struct
from copy import deepcopy
from pathlib import Path
from uuid import uuid4


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stable_snapshot(snapshot):
    state = snapshot.get('stability', {})
    signature = snapshot.get('capture_signature')
    return bool(signature and snapshot.get('config', {}).get('profile') != 'preflight' and snapshot.get('evidence_level') != 'preflight'
                and snapshot.get('schema_version', 1) >= 2 and state.get('status') == 'pass'
                and state.get('before') == state.get('after') == signature)


def resource_fingerprint(snapshot, node_id):
    """覆盖资源子树、母件和祖先样式；证据不足时不允许跨版本复用。"""
    records = snapshot.get('nodes', [])
    nodes = {n['id']: n for n in records}
    if (not stable_snapshot(snapshot) or not snapshot.get('pagination', {}).get('complete')
            or snapshot.get('total_nodes') != len(records) or len(nodes) != len(records)
            or snapshot['pagination'].get('total_nodes') != len(records)
            or snapshot['pagination'].get('collected_nodes') != len(records)
            or snapshot.get('next_offset') is not None):
        return None
    children = {}
    for n in records:
        children.setdefault(n.get('parent_id'), []).append(n['id'])
    included, visiting = {}, set()

    def visit(identity):
        if identity in visiting:
            raise ValueError('资源依赖成环')
        if identity in included:
            return
        n = nodes[identity]
        visiting.add(identity)
        if n.get('type') == 'INSTANCE':
            # 跳过的实例内部覆盖不能仅凭母件ID推断；展开后再建立复用证据。
            if snapshot.get('traversal', {}).get('skip_instance_children'):
                raise ValueError('实例内部未采集')
            visit(n['main_component_id'])
        for child in children.get(identity, []):
            visit(child)
        included[identity] = {'node': n, 'children': children.get(identity, [])}
        visiting.remove(identity)
        parent = n.get('parent_id')
        ancestors = set()
        while parent and parent in nodes:
            if parent in ancestors:
                raise ValueError('祖先成环')
            ancestors.add(parent)
            included['ancestor:' + parent] = nodes[parent]
            parent = nodes[parent].get('parent_id')

    try:
        visit(node_id)
    except (KeyError, ValueError):
        return None
    return digest(included)


def start_run(requirements, snapshot):
    if not stable_snapshot(snapshot):
        raise ValueError('缺少采集前后稳定签名，不能建立本轮导出证据')
    return {'run_id': str(uuid4()), 'requirements_sha256': digest(requirements),
            'snapshot_sha256': digest(snapshot), 'scope_sha256': digest(snapshot['config']),
            'capture_signature': snapshot['capture_signature'], 'status': 'active', 'files': []}


def safe_path(directory, relative):
    root = Path(directory).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('导出路径必须位于本轮目录内')
    return path


def record_export(run, snapshot, node_id, relative, directory, setting, before_signature, after_signature):
    if run['status'] != 'active' or digest(snapshot) != run['snapshot_sha256']:
        raise ValueError('轮次停止或快照改变，不能沿用导出记录')
    if before_signature != after_signature or before_signature != run['capture_signature']:
        raise ValueError('导出前后来源改变，请重新采集')
    node = next(n for n in snapshot['nodes'] if n['id'] == node_id)
    if setting not in node.get('export_settings', []):
        raise ValueError('实际导出设置不属于该节点')
    header = safe_path(directory, relative).read_bytes()[:24]
    if len(header) != 24 or header[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('实际文件不是PNG')
    dimensions = list(struct.unpack('>II', header[16:24]))
    receipt = {'dimensions': dimensions, 'node_id': node_id, 'path': relative, 'setting': setting,
               'run_id': run['run_id'], 'snapshot_sha256': run['snapshot_sha256'],
               'source_before': before_signature, 'source_after': after_signature,
               'resource_sha256': resource_fingerprint(snapshot, node_id),
               'sha256': file_hash(safe_path(directory, relative)), 'status': 'verified'}
    run['files'] = [r for r in run['files'] if r['path'] != relative] + [receipt]
    return receipt


def reuse_proof_valid(receipt, snapshot):
    if 'reused_from' not in receipt:
        return True
    proof = receipt['reused_from']
    current = resource_fingerprint(snapshot, receipt['node_id'])
    return bool(current and current == receipt.get('resource_sha256') == proof.get('resource_sha256')
                and proof.get('sha256') == receipt.get('sha256')
                and proof.get('snapshot_sha256') and proof.get('run_id')
                and proof.get('source_before') and proof.get('source_before') == proof.get('source_after'))


def validate_run(run, requirements, snapshot, directory):
    errors = []
    if run.get('status') != 'active':
        errors.append('轮次已停止或文件已清理，不能作为当前交付证据')
    if not stable_snapshot(snapshot):
        errors.append('快照未证明采集前后稳定')
    for key, value in [('requirements_sha256', digest(requirements)), ('snapshot_sha256', digest(snapshot)), ('scope_sha256', digest(snapshot['config']))]:
        if run.get(key) != value:
            errors.append(f'本轮绑定不匹配：{key}')
    nodes = {n['id']: n for n in snapshot['nodes']}
    expected = {(r['node_id'], r['path']) for r in snapshot.get('export_items', [])}
    actual = {(r['node_id'], r['path']) for r in run.get('files', [])}
    if not expected or expected != actual or len(actual) != len(run.get('files', [])):
        errors.append('本轮导出映射缺失、重复或与快照不一致')
    for r in run.get('files', []):
        node = nodes.get(r['node_id'], {})
        mapped = next((item for item in snapshot.get('export_items', []) if item['node_id'] == r['node_id'] and item['path'] == r['path']), {})
        if mapped.get('setting', r.get('setting')) != r.get('setting'):
            errors.append(f'导出映射与实际设置不一致：{r["path"]}')
        if (r.get('run_id') != run.get('run_id') or r.get('snapshot_sha256') != digest(snapshot)
                or r.get('source_before') != snapshot.get('capture_signature') or r.get('source_after') != snapshot.get('capture_signature')
                or r.get('setting') not in node.get('export_settings', [])):
            errors.append(f'资源来源或导出设置不匹配：{r["path"]}')
        if not reuse_proof_valid(r, snapshot):
            errors.append(f'跨版本复用证据不匹配：{r["path"]}')
        try:
            if r.get('status') != 'verified' or file_hash(safe_path(directory, r['path'])) != r['sha256']:
                errors.append(f'文件已变化或不能复用：{r["path"]}')
        except (OSError, ValueError):
            errors.append(f'文件不存在或路径无效：{r["path"]}')
    return errors


def reusable_files(run, requirements, snapshot, directory):
    if run.get('status') != 'active' or not stable_snapshot(snapshot):
        return []
    if any(run.get(k) != v for k, v in [('requirements_sha256', digest(requirements)), ('snapshot_sha256', digest(snapshot)), ('scope_sha256', digest(snapshot['config']))]):
        return []
    reusable = []
    for receipt in run.get('files', []):
        try:
            if (receipt['status'] == 'verified' and receipt.get('run_id') == run['run_id']
                    and receipt.get('snapshot_sha256') == run['snapshot_sha256']
                    and receipt.get('source_before') == receipt.get('source_after') == snapshot.get('capture_signature')
                    and reuse_proof_valid(receipt, snapshot)
                    and any(n['id'] == receipt['node_id'] and receipt.get('setting') in n.get('export_settings', []) for n in snapshot['nodes'])
                    and file_hash(safe_path(directory, receipt['path'])) == receipt['sha256']):
                reusable.append(receipt['path'])
        except (OSError, ValueError):
            pass
    return reusable


def plan_exports(run, requirements, previous, snapshot, directory):
    """只规划已有工具需要导出的文件，不调用Figma，也不修改旧证据。"""
    if not stable_snapshot(snapshot):
        raise ValueError('必须先取得稳定的正式快照，预检不能启动导出')
    valid_old = set(reusable_files(run, requirements, previous, directory))
    receipts = {r['path']: r for r in run.get('files', [])}
    same_scope = digest(previous['config']) == digest(snapshot['config'])
    same_snapshot = digest(previous) == digest(snapshot)
    reuse, export = [], []
    for item in snapshot.get('export_items', []):
        receipt = receipts.get(item['path'], {})
        current = resource_fingerprint(snapshot, item['node_id'])
        reusable = (same_scope and item['path'] in valid_old
                    and receipt.get('node_id') == item['node_id']
                    and receipt.get('setting') == item.get('setting', receipt.get('setting'))
                    and (same_snapshot or (current and current == receipt.get('resource_sha256')
                         == resource_fingerprint(previous, item['node_id']))))
        (reuse if reusable else export).append(item['path'])
    old_nodes = {n['id']: n for n in previous['nodes']}
    new_nodes = {n['id']: n for n in snapshot['nodes']}
    changed = [i for i in old_nodes.keys() | new_nodes.keys() if old_nodes.get(i) != new_nodes.get(i)]
    orders = []
    for source in (previous, snapshot):
        order = {}
        for n in source['nodes']:
            order.setdefault(n.get('parent_id'), []).append(n['id'])
        orders.append(order)
    changed += [i for i in orders[0].keys() | orders[1].keys()
                if i and orders[0].get(i) != orders[1].get(i)]
    # 旧图保留删除、移出节点的依赖，避免漏掉旧父组件。
    impact = impact_scope(previous['nodes'] + snapshot['nodes'], changed)
    return {'reuse': reuse, 'export': export, 'removed': sorted(set(receipts) - set(reuse) - set(export)),
            'affected_ids': impact['affected_ids']}


def advance_run(run, requirements, previous, snapshot, directory):
    plan = plan_exports(run, requirements, previous, snapshot, directory)
    if digest(previous) == digest(snapshot) and not plan['export'] and not plan['removed']:
        return deepcopy(run)
    fresh = start_run(requirements, snapshot)
    for old in run.get('files', []):
        if old['path'] not in plan['reuse']:
            continue
        receipt = deepcopy(old)
        receipt['reused_from'] = {key: old[key] for key in
                                  ('run_id', 'snapshot_sha256', 'source_before', 'source_after', 'resource_sha256', 'sha256')}
        receipt.update(run_id=fresh['run_id'], snapshot_sha256=fresh['snapshot_sha256'],
                       source_before=fresh['capture_signature'], source_after=fresh['capture_signature'])
        fresh['files'].append(receipt)
    # 路径从正式映射移除后仍保留清理归属，防止改名留下孤儿PNG。
    fresh['retired_files'] = deepcopy(run.get('retired_files', [])) + [
        deepcopy(r) for r in run.get('files', []) if r['path'] not in plan['reuse']]
    return fresh


def cleanup_exports(run, directory):
    """仅清除记录中的未变化PNG；源图、未登记文件和被用户修改的文件不删除。"""
    removed, retained = [], []
    for receipt in run.get('files', []) + run.get('retired_files', []):
        try:
            path = safe_path(directory, receipt['path'])
            if not path.exists():
                continue
            if path.suffix.lower() != '.png' or file_hash(path) != receipt['sha256']:
                retained.append(receipt['path'])
                continue
            path.unlink()
            removed.append(receipt['path'])
        except (OSError, ValueError):
            retained.append(receipt['path'])
    run.update(status='cleaned', cleanup={'removed': removed, 'retained': retained})
    return run['cleanup']


def checkpoint(path, run, *, stop_reason=None):
    if stop_reason:
        run.update(status='stopped', stop_reason=stop_reason)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(run, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def impact_scope(nodes, changed_ids, protected_ids=()):
    # 子节点影响父组件，母件再影响所有实例；只生成检查清单，不自动修改。
    affected = set(changed_ids)
    edges = []
    for n in nodes:
        if n.get('parent_id'):
            edges.append((n['id'], n['parent_id']))
        if n.get('main_component_id'):
            edges.append((n['main_component_id'], n['id']))
    while True:
        expanded = affected | {target for source, target in edges if source in affected}
        if expanded == affected:
            break
        affected = expanded
    return {'affected_ids': sorted(affected), 'protected_affected_ids': sorted(affected & set(protected_ids)),
            'export_ids': sorted(n['id'] for n in nodes if n['id'] in affected and any(e.get('format') == 'PNG' for e in n.get('export_settings', [])))}
