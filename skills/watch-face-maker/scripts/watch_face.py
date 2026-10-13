#!/usr/bin/env python3
"""项目事实入口。同步需求，不制造测量或验收证据。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from project_spec import preflight, validate_spec
from validate_cross_model_results import revalidate_production

VIEWS = {'manifest': 'audit-manifest.json', 'workflow_contract': 'workflow-contract.json',
         'config': 'snapshot-config.json'}
ARTIFACTS = {'snapshot': 'snapshot.json', 'evidence': 'evidence.json',
             'visual_review': 'visual-review.json', 'report': 'report.json'}


def read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError(str(path)+' 必须是JSON对象')
    return value


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2)+'\n'
    if not path.exists() or path.read_text(encoding='utf-8') != raw:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix+'.tmp')
        temp.write_text(raw, encoding='utf-8')
        temp.replace(path)


def inside(root, name):
    path = (root/name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('项目写入及证据路径必须在项目目录内：'+str(name))
    return path


def get(obj, path):
    for key in path.split('.'):
        if not isinstance(obj, dict) or key not in obj:
            return None
        obj = obj[key]
    return obj


def put(obj, path, value):
    keys = path.split('.')
    for key in keys[:-1]:
        if key not in obj:
            obj[key] = {}
        if not isinstance(obj[key], dict):
            raise ValueError('派生字段的父级不是对象：'+path)
        obj = obj[key]
    obj[keys[-1]] = copy.deepcopy(value)


def derived(spec):
    """明确维护的字段白名单；实际计数和证据永不进入此表。"""
    target = spec.get('target', dict(width=None, height=None, shape=None))
    identity = spec.get('identity', {})
    name = identity.get('name_en')
    prefix = ''.join(name.split()) if isinstance(name, str) else None
    source = copy.deepcopy(spec.get('source_strategy', {}))
    if source.get('mode') and 'reuse_permission_confirmed' not in source:
        source['reuse_permission_confirmed'] = spec.get('confirmations', {}).get('source_strategy') is True
    return {
        'manifest': {**({'prefix': prefix} if prefix is not None else {}), 'canvas': target,
                     'languages': spec.get('languages', []), 'themes': spec.get('themes', []),
                     'features': spec.get('features', {}),
                     'capabilities.requested': spec.get('capabilities', {}).get('requested', []),
                     'capability_modes': spec.get('capability_modes', {}),
                     'source_strategy': spec.get('source_strategy', {})},
        'workflow_contract': {'task_mode': spec.get('task_mode'), 'complexity': spec.get('complexity'),
                             'target': target, 'source_strategy': source},
        'config': {k: spec.get('figma', {}).get(k) for k in ('file_key', 'page_id', 'section_id')},
    }


def templates():
    return {'manifest': {'components': {'total': None}, 'aod': {'lit_ratio': None},
                         'capabilities': {'implemented': []}},
            'workflow_contract': {'schema_version': 1, 'status': 'in_progress',
                                  'reference_contract': {'reference_hashes': [], 'invariants': [],
                                                         'allowed_changes': [], 'forbidden_content': []},
                                  'production_routes': [], 'attempts': [],
                                  'gates': {k: 'pending' for k in ('intake', 'reference', 'prototype', 'assembly', 'visual_preflight')}},
            'config': {k: None for k in ('asset_board_id', 'main_id', 'active_id', 'aod_id', 'preview_id')}}


def init(root, spec):
    errors = validate_spec(spec)
    if errors:
        return dict(exit_code=2, errors=errors)
    if spec['operation'] == 'audit' or (root.exists() and any(root.iterdir())):
        return dict(exit_code=1, errors=['审核不建立文件，或目标目录已含文件；请使用import或只读命令'])
    views, fields = templates(), derived(spec)
    for key in views:
        for path, value in fields[key].items():
            put(views[key], path, value)
    for key, view in views.items():
        save(root/VIEWS[key], view)
    save(root/'project-spec.json', spec)
    save(root/'project-state.json', dict(schema_version=1, views=VIEWS, spec=spec, owned=fields, stale_artifacts=[]))
    return dict(exit_code=0, summary='项目事实与待完成模板已建立；尚未验证制作结果')


def import_project(root, args):
    if (root/'project-spec.json').exists() or args.operation == 'audit':
        return dict(exit_code=1, errors=['已存在项目事实，或操作为只读审核；未写入'])
    manifest_path = inside(root, args.manifest)
    manifest = read(manifest_path)
    contract_path = root/'workflow-contract.json'
    contract = read(contract_path) if contract_path.is_file() else {}
    config_path = root/'snapshot-config.json'
    config = read(config_path) if config_path.is_file() else {}
    mtarget = copy.deepcopy(manifest.get('canvas', {}))
    ctarget = contract.get('target', {})
    conflicts = [dict(field='target.'+k, manifest=mtarget.get(k), contract=v)
                 for k, v in ctarget.items() if k in mtarget and mtarget[k] != v]
    msource, csource = manifest.get('source_strategy'), contract.get('source_strategy')
    if msource is not None and csource is not None and msource != csource:
        conflicts.append(dict(field='source_strategy', manifest=msource, contract=csource))
    spec = dict(schema_version=1, operation=args.operation, task_mode=contract.get('task_mode', 'adapt'),
                complexity=contract.get('complexity', 'native'), target=mtarget,
                identity={}, languages=manifest.get('languages', []), themes=manifest.get('themes', []),
                units=[], features=manifest.get('features', {}), figma={k: config.get(k) for k in ('file_key', 'page_id', 'section_id')}, scope={}, text_families=[],
                source_strategy=csource if csource is not None else (msource or {}),
                capability_modes=manifest.get('capability_modes', {}),
                capabilities={'requested': manifest.get('capabilities', {}).get('requested', [])},
                development={}, confirmations={})
    errors = validate_spec(spec)
    if errors:
        return dict(exit_code=2, errors=errors)
    views = dict(VIEWS, manifest=args.manifest)
    owned = {key: {path: get(read(inside(root, views[key])), path) for path in fields}
             for key, fields in derived(spec).items() if inside(root, views[key]).is_file()}
    save(root/'project-spec.json', spec)
    save(root/'project-state.json', dict(schema_version=1, views=views, spec=spec, owned=owned,
                                       conflicts=conflicts, stale_artifacts=[]))
    return dict(exit_code=1, summary='旧证据未改动；请确认冲突与缺失事实后同步', conflicts=conflicts,
                missing=['确认记录', '表盘名称', 'Figma目标与范围', '开发依据', '字模规格'])


def sync(root, spec, state):
    if spec['operation'] == 'audit':
        return dict(exit_code=1, errors=['audit为只读；未同步文件'])
    unresolved = [c for c in state.get('conflicts', [])
                  if spec.get('confirmations', {}).get(c['field'].split('.')[0]) is not True]
    if unresolved:
        return dict(exit_code=1, conflicts=unresolved, changes=[], summary='导入冲突尚未明确确认选择；未写入')
    fields = derived(spec)
    updates, conflicts, changes = {}, [], []
    for key, mapping in fields.items():
        path = inside(root, state['views'][key])
        obj = read(path) if path.is_file() else templates()[key]
        for field, value in mapping.items():
            old = state.get('owned', {}).get(key, {}).get(field)
            actual = get(obj, field)
            if actual != old and actual != value:
                conflicts.append(dict(file=path.name, field=field, previous=old, actual=actual, requested=value))
            elif actual != value:
                put(obj, field, value)
                changes.append(dict(file=path.name, field=field, value=value))
        updates[path] = obj
    if conflicts:
        return dict(exit_code=1, conflicts=conflicts, changes=[])
    fact_changed = spec != state['spec']
    stale = state.get('stale_artifacts', [])
    if fact_changed:
        stale = sorted(set(stale) | {v for v in ARTIFACTS.values() if (root/v).is_file()})
        contract = updates[inside(root, state['views']['workflow_contract'])]
        contract['status'] = 'in_progress'
        contract['gates'] = {k: 'pending' for k in contract.get('gates', {})}
    if changes or fact_changed:
        for path, obj in updates.items():
            save(path, obj)
        save(root/'project-state.json', dict(state, spec=spec, owned=fields, stale_artifacts=stale, conflicts=[]))
    return dict(exit_code=0, changes=changes, stale_artifacts=stale,
                summary='需求派生字段已同步' if changes else '无派生字段变化')


def remote_evidence(root, name):
    e = read(inside(root, name)) if name and inside(root, name).is_file() else None
    if e is None:
        return None
    permissions = e.get('permissions', {})
    basis = permissions.get('basis', {}) if isinstance(permissions, dict) else {}
    try:
        receipt = inside(root, basis['receipt_path'])
        raw = read(receipt)
        ok = digest(receipt) == basis['response_sha256'] and raw.get('file_key') == e.get('file_key')
        if basis.get('kind') == 'successful_write':
            ok = ok and bool(raw.get('changedNodeIds')) and raw['changedNodeIds'] == basis.get('changed_node_ids')
        else:
            ok = ok and basis.get('kind') == 'file_creation' and bool(raw.get('file_url'))
    except (KeyError, TypeError, ValueError, OSError):
        ok = False
    if not ok and isinstance(permissions, dict):
        permissions.pop('basis', None)
    return e


def requirement_blockers(spec, state, contract):
    if spec != state.get('spec'):
        return ['需求已变化，请先同步；旧合同不得推进门禁']
    blockers = [c['explanation'] for c in preflight(spec)['local_checks']
                if c['id'].startswith('requirements.') and c['status'] != 'pass']
    for field, expected in derived(spec)['workflow_contract'].items():
        if get(contract, field) != expected:
            blockers.append('合同与当前需求不一致：'+field+'；请先处理冲突并同步')
    if state.get('conflicts'):
        blockers.append('导入冲突尚未同步解决')
    return blockers


def status(root, spec, state, artifacts=None, exports_dir='exports', *, validated_report=None):
    paths = {key: inside(root, value) for key, value in dict(state['views'], **(artifacts or ARTIFACTS)).items() if key != 'config'}
    contract = read(paths['workflow_contract']) if paths['workflow_contract'].exists() else {}
    stale = set(state.get('stale_artifacts', []))
    ready, production, blockers = False, 'unverified', requirement_blockers(spec, state, contract)
    if spec != state.get('spec'):
        blockers.append('需求已变化，请先同步；原证据待重新采集')
        stale.update(v.name for v in paths.values() if v.is_file())
    required = ('manifest', 'snapshot', 'evidence', 'workflow_contract', 'visual_review')
    if validated_report is not None or paths['report'].is_file():
        report = validated_report if validated_report is not None else read(paths['report'])
        inputs = report.get('inputs', {})
        if not isinstance(inputs, dict) or not isinstance(report.get('delivery', {}), dict):
            raise ValueError('报告inputs和delivery必须是对象')
        current = all(paths[k].is_file() and inputs.get(k) == digest(paths[k]) for k in required)
        if not current:
            stale.add(paths['report'].name)
        elif not blockers and report.get('delivery', {}).get('production_validation') == 'pass':
            try:
                if validated_report is None:
                    revalidate_production(paths, inside(root, exports_dir))
                ready, production = True, 'pass'
                stale.clear()
            except ValueError as exc:
                blockers.append(str(exc)); production = 'fail'
    if not ready:
        blockers.append('缺少当前输入绑定的正式生产校验通过结果')
    pending = [k for k, v in contract.get('gates', {}).items() if v != 'pass']
    return dict(exit_code=0 if ready else 1, operation=spec['operation'],
                stage=pending[0] if pending else 'production_validation',
                production_validation=production, production_ready=ready,
                stale_artifacts=sorted(stale), blockers=blockers,
                next_step='处理阻塞项并重新执行check' if blockers else '等待用户验收与固件验证')


def main():
    parser = argparse.ArgumentParser(description='表盘项目事实与只读检查入口')
    parser.add_argument('command', choices=('init', 'import', 'sync', 'preflight', 'status', 'check'))
    parser.add_argument('project', type=Path)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--manifest', default='audit-manifest.json')
    parser.add_argument('--operation', choices=('create', 'modify', 'organize', 'audit'))
    parser.add_argument('--figma-evidence', default='figma-preflight.json')
    parser.add_argument('--gate')
    for name in ('snapshot', 'evidence', 'visual-review', 'report'):
        parser.add_argument('--'+name, default=ARTIFACTS[name.replace('-', '_')])
    parser.add_argument('--exports-dir', default='exports')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    root = args.project.resolve()
    artifacts = {k: getattr(args, k) for k in ARTIFACTS}
    try:
        if args.command == 'init':
            if not args.input:
                raise ValueError('init需要--input结构化需求')
            result = init(root, read(args.input))
        elif args.command == 'import':
            if not args.operation:
                raise ValueError('import需要--operation明确操作类型')
            result = import_project(root, args)
        else:
            spec = read(root/'project-spec.json')
            errors = validate_spec(spec)
            if errors:
                raise ValueError('；'.join(errors))
            state = read(root/'project-state.json')
            if args.operation:
                spec['operation'] = args.operation
            if args.command == 'sync':
                result = sync(root, spec, state)
            elif args.command == 'preflight':
                result = preflight(spec, remote_evidence(root, args.figma_evidence))
            elif args.command == 'check' and args.gate:
                contract_path = inside(root, state['views']['workflow_contract'])
                blockers = requirement_blockers(spec, state, read(contract_path))
                if blockers:
                    result = dict(exit_code=1, blockers=blockers, summary='；'.join(blockers))
                else:
                    command = [sys.executable, str(Path(__file__).with_name('workflow_guards.py')),
                               str(contract_path), '--gate', args.gate]
                    run = subprocess.run(command, capture_output=True, text=True)
                    result = dict(exit_code=run.returncode, summary=run.stdout+run.stderr)
            elif args.command == 'check':
                result = status(root, spec, state, artifacts, args.exports_dir)
                if not requirement_blockers(spec, state, read(inside(root, state['views']['workflow_contract']))):
                    paths = {k: inside(root, v) for k, v in dict(state['views'], **artifacts).items() if k != 'config'}
                    if all(paths[k].is_file() for k in ('manifest', 'snapshot', 'evidence', 'workflow_contract', 'visual_review')):
                        try:
                            report = revalidate_production(paths, inside(root, args.exports_dir))
                            result = status(root, spec, state, artifacts, args.exports_dir, validated_report=report)
                            if result['production_ready'] and spec['operation'] != 'audit':
                                previous = paths['report']
                                if previous.is_file():
                                    history = previous.parent/'history'/(previous.stem+'-'+digest(previous)+previous.suffix)
                                    history.parent.mkdir(parents=True, exist_ok=True)
                                    if not history.is_file():
                                        shutil.copyfile(previous, history)
                                save(previous, report)
                        except ValueError as exc:
                            result.update(exit_code=1, production_validation='fail', blockers=[str(exc)])
            else:
                result = status(root, spec, state, artifacts, args.exports_dir)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        result = dict(exit_code=2, errors=[str(exc)], summary='输入结构无效，未执行同步')
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result.get('summary', '检查通过，可继续' if result['exit_code'] == 0 else '检查未通过；请处理下列信息'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result['exit_code']


if __name__ == '__main__':
    sys.exit(main())
