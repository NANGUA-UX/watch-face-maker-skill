#!/usr/bin/env python3
"""按目标独立检查并规划共享导出；Figma写入和导出仍使用现有工具。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil

from run_evidence import (digest, file_hash, safe_path, start_run, record_export,
                          reusable_files, resource_fingerprint, validate_run, cleanup_exports, stable_snapshot)
from screen_geometry import screen_spec
from validate_manifest import (validate_manifest, validate_snapshot, validate_pngs,
                               validate_target_binding, manual_checks, check_item)


def target_id(target):
    spec = screen_spec(target['manifest']['canvas'])
    return f"{spec['width']}x{spec['height']}"


def check_targets(targets):
    ids = [target_id(t) for t in targets]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('目标尺寸列表为空或重复')
    for t in targets:
        items = t['snapshot'].get('export_items', [])
        paths = [i['path'] for i in items]
        if len(set(paths)) != len(paths):
            raise ValueError(f'{target_id(t)}内存在重复导出路径')
        # 输出根负责区分尺寸。切图名称和相对路径继续保留完整英文前缀。
        for item in items:
            safe_path('/task-exports', item['path'])
            if not item['path'].endswith('.png'):
                raise ValueError('正式导出路径必须是PNG')


def resource_key(target, item):
    s = target['snapshot']; config = s['config']
    n = next((n for n in s['nodes'] if n['id'] == item['node_id']), {})
    fingerprint = resource_fingerprint(s, item['node_id'])
    setting = item.get('setting')
    if setting is None and len(n.get('export_settings', [])) == 1:
        setting = n['export_settings'][0]
    if not fingerprint or setting not in n.get('export_settings', []):
        return None
    if item.get('role') != 'asset' or n.get('type') != 'COMPONENT':
        return None
    if item['node_id'] in {config.get(k) for k in ('active_id','aod_id','background_id','main_id','preview_id')}:
        return None
    if setting.get('constraint', {}) != {'type':'SCALE','value':1}:
        return None
    return (config['file_key'], item['node_id'], digest(setting), fingerprint)


def expected_dimensions(snapshot, item):
    n = next(n for n in snapshot['nodes'] if n['id'] == item['node_id'])
    setting = item.get('setting', n['export_settings'][0])
    c = setting.get('constraint', {'type':'SCALE','value':1})
    w,h = n['width'], n['height']
    if c['type'] == 'SCALE': return [round(w*c['value']),round(h*c['value'])]
    if c['type'] == 'WIDTH': return [round(c['value']),round(h*c['value']/w)]
    if c['type'] == 'HEIGHT': return [round(w*c['value']/h),round(c['value'])]
    raise ValueError('不支持的导出尺寸约束')


def valid_receipts(target, root):
    run, snapshot = target.get('run', {}), target['snapshot']
    available = set(reusable_files(run, target['manifest'], snapshot, root/target_id(target)))
    items = {i['path']:i for i in snapshot.get('export_items', [])}
    from PIL import Image
    receipts = {}
    for r in run.get('files', []):
        item = items.get(r['path'])
        if not item or r['path'] not in available or r.get('node_id') != item['node_id']:
            continue
        if (r.get('setting') != item.get('setting', r.get('setting'))
                or r.get('resource_sha256') != resource_fingerprint(snapshot, r['node_id'])
                or r.get('dimensions') != expected_dimensions(snapshot, item)):
            continue
        try:
            with Image.open(safe_path(root/target_id(target), r['path'])) as image:
                if image.format != 'PNG' or list(image.size) != r['dimensions']:
                    continue
                image.verify()
        except (OSError, ValueError, SyntaxError):
            continue
        receipts[r['path']] = r
    return receipts


def plan_batch_exports(targets, root):
    """只读规划。export使用Figma导出，copy必须在来源完成绑定后执行。"""
    check_targets(targets)
    for t in targets:
        s = t['snapshot']; p = s.get('pagination', {})
        if not stable_snapshot(s) or p.get('complete') is not True or p.get('total_nodes') != p.get('collected_nodes') or p.get('collected_nodes') != len(s.get('nodes', [])):
            raise ValueError('导出规划需要每目标完整且稳定的正式快照')
    root = Path(root)
    pool, available = {}, {}
    for t in targets:
        identity = target_id(t); available[identity] = valid_receipts(t, root)
        for item in t['snapshot'].get('export_items', []):
            key = resource_key(t, item)
            if key and item['path'] in available[identity]:
                pool.setdefault(key, (identity, item['path']))
    actions = []
    for t in targets:
        identity = target_id(t)
        for item in t['snapshot'].get('export_items', []):
            action = dict(target=identity, node_id=item['node_id'], path=item['path'])
            key = resource_key(t, item)
            if item['path'] in available[identity]:
                action['action'] = 'reuse'
            elif key and key in pool:
                action.update(action='copy', source_target=pool[key][0], source_path=pool[key][1])
            else:
                action['action'] = 'export'
                if key: pool[key] = (identity, item['path'])
            actions.append(action)
    return actions


def copy_shared_export(source, target, source_path, path, root):
    """重新核对实际文件和依赖后复制、绑定；不沿用源目标的整盘验收。"""
    root = Path(root)
    src = next(i for i in source['snapshot']['export_items'] if i['path'] == source_path)
    dst = next(i for i in target['snapshot']['export_items'] if i['path'] == path)
    key = resource_key(source, src)
    if not key or key != resource_key(target, dst):
        raise ValueError('共享资源母件、导出设置或完整依赖不一致')
    receipt = valid_receipts(source, root).get(source_path)
    if not receipt:
        raise ValueError('来源PNG缺失、改变、尺寸不符或未绑定当前轮次')
    target.setdefault('run', start_run(target['manifest'], target['snapshot']))
    run = target['run']; snapshot = target['snapshot']
    if run.get('status') != 'active' or run.get('snapshot_sha256') != digest(snapshot) or run.get('requirements_sha256') != digest(target['manifest']):
        raise ValueError('目标轮次已失效，先重新建立证据')
    src_file = safe_path(root/target_id(source), source_path)
    dst_file = safe_path(root/target_id(target), path)
    if dst_file.exists() and file_hash(dst_file) != receipt['sha256']:
        raise ValueError('目标文件已存在且内容不同，不能覆盖未确认文件')
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    if src_file != dst_file: shutil.copy2(src_file, dst_file)
    if file_hash(dst_file) != receipt['sha256']:
        raise ValueError('复制期间PNG内容改变')
    new = record_export(run, snapshot, dst['node_id'], path, root/target_id(target),
                        receipt['setting'], snapshot['capture_signature'], snapshot['capture_signature'])
    new['reused_from'] = {k: receipt[k] for k in ('run_id','snapshot_sha256','source_before','source_after','resource_sha256','sha256')}
    return new


def validate_batch(targets, root):
    check_targets(targets)
    reports, unique = {}, set()
    for t in targets:
        identity = target_id(t); snapshot = t['snapshot']; directory = Path(root)/identity
        errors, _ = validate_manifest(t['manifest'])
        checks = [check_item('manifest','fail' if errors else 'pass','；'.join(errors) if errors else '目标清单检查通过')]
        checks += validate_snapshot(snapshot) + validate_target_binding(t['manifest'], snapshot)
        pagination = snapshot.get('pagination', {})
        complete = pagination.get('complete') is True and pagination.get('total_nodes') == pagination.get('collected_nodes') == len(snapshot.get('nodes', []))
        checks += validate_pngs(snapshot,directory) if complete else [check_item('exports.png','unverified','快照不完整，不能验收正式资源映射')]
        if t.get('run'):
            binding = validate_run(t['run'],t['manifest'],snapshot,directory)
            checks.append(check_item('exports.binding','fail' if binding else 'pass','；'.join(binding) if binding else '本目标绑定一致'))
        else:
            checks.append(check_item('exports.binding','unverified','缺少本目标来源与PNG绑定'))
        checks += manual_checks()
        items = snapshot.get('export_items', [])
        for i in items:
            unique.add(resource_key(t,i) or (identity,i['node_id'],i['path']))
        reports[identity] = dict(checks=checks, summary=dict(Counter(c['status'] for c in checks)), resource_count=len(items))
    return dict(targets=reports, unique_resource_count=len(unique),
                note='数量按导出资源计算，不代表运行内存；自动检查、视觉确认、真机验证分开记录')


def cleanup_batch(targets, root, *, finished=False):
    if not finished: raise ValueError('必须在整个多尺寸任务结束后统一清理')
    check_targets(targets)
    return {target_id(t): cleanup_exports(t['run'], Path(root)/target_id(t)) for t in targets if t.get('run')}


def load_targets(path):
    data = json.loads(path.read_text())
    targets = []
    for item in data['targets']:
        t = {k: json.loads((path.parent/item[k]).read_text()) for k in ('manifest','snapshot')}
        if item.get('evidence'): t['run'] = json.loads((path.parent/item['evidence']).read_text())
        targets.append(t)
    return targets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch',type=Path)
    parser.add_argument('--exports-root',type=Path,required=True)
    parser.add_argument('--plan-exports',action='store_true')
    parser.add_argument('--report',type=Path)
    args = parser.parse_args()
    try:
        targets = load_targets(args.batch)
        report = plan_batch_exports(targets,args.exports_root) if args.plan_exports else validate_batch(targets,args.exports_root)
        text = json.dumps(report,ensure_ascii=False,indent=2)+'\n'
        if args.report:
            args.report.parent.mkdir(parents=True,exist_ok=True); args.report.write_text(text)
        else: print(text,end='')
        return 0 if args.plan_exports else int(any(r['summary'].get('fail',0) for r in report['targets'].values()))
    except (OSError,ValueError,KeyError,TypeError) as exc:
        parser.exit(2,f'多尺寸检查失败：{exc}\n')

if __name__ == '__main__': raise SystemExit(main())
