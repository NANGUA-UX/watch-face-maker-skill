"""封存和只读复核样稿证据；不自动判断视觉质量。"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from workflow_guards import file_sha256, load_contract


def _read(path):
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('样稿证据必须是JSON对象')
    return value


def _path(root, name):
    if not isinstance(name, str) or not name:
        raise ValueError('样稿证据缺少相对路径')
    root = root.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('样稿证据路径越出项目或封存目录')
    return path


def _record(root, value):
    if not isinstance(value, dict) or not isinstance(value.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', value['sha256']):
        raise ValueError('样稿证据缺少有效SHA-256')
    path = _path(root, value.get('path'))
    if file_sha256(path) != value['sha256']:
        raise ValueError('样稿证据文件哈希不一致：' + path.name)
    return path


def _inputs(contract):
    value = {k: v for k, v in contract.items() if k not in {'gates', 'status', 'attempts', 'prototype_evidence'}}
    value['production_routes'] = sorted(
        ({k: v for k, v in item.items() if k != 'reason'} for item in contract['production_routes']),
        key=lambda item: item['region'],
    )
    return value


def _target_failures(snapshot, spec):
    target = _read(spec).get('figma', {}) if spec.is_file() else {}
    config = snapshot.get('config', {})
    if not isinstance(target, dict) or not isinstance(config, dict):
        raise ValueError('项目Figma目标和快照config必须是对象')
    return ['样稿快照与当前Figma目标不一致：' + key
            for key in ('file_key', 'page_id', 'section_id')
            if target.get(key) and config.get(key) != target[key]]


def checkpoint_failures(contract_path, contract):
    from validate_manifest import validate_workflow_binding

    try:
        receipt_path = _record(contract_path.parent, contract.get('prototype_evidence'))
        receipt = _read(receipt_path)
        if receipt.get('schema_version') != 1:
            raise ValueError('样稿封存版本无效')
        paths = {key: _record(receipt_path.parent, receipt.get(key))
                 for key in ('contract', 'manifest', 'snapshot', 'visual_review')}
        original = load_contract(paths['contract'])
        if _inputs(original) != _inputs(contract):
            raise ValueError('当前目标、参考契约或制作路线已变化，需要重新评审样稿')
        spec = contract_path.parent / 'project-spec.json'
        if receipt.get('project_spec_sha256') != (file_sha256(spec) if spec.is_file() else None):
            raise ValueError('当前项目需求已变化，需要重新评审样稿')
        target_failures = _target_failures(_read(paths['snapshot']), spec)
        if target_failures:
            return target_failures
        checks = validate_workflow_binding(
            _read(paths['manifest']), paths['contract'], paths['visual_review'], paths['snapshot'],
            _path(receipt_path.parent, receipt.get('exports_dir')), _read(paths['snapshot']),
            required_gate='reference',
        )
        return ['样稿证据未通过：' + item['explanation'] for item in checks if item['status'] != 'pass']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ['样稿封存证据缺失或无效：' + str(exc)]


def seal_prototype(args, contract, manifest, snapshot, review):
    from validate_manifest import validate_workflow_binding

    if any(contract['gates'][gate] != 'pending' for gate in ('assembly', 'visual_preflight')):
        return ['完整组装或视觉预检已推进，不能事后补签样稿；先退回受影响阶段']
    root = args.contract.parent.resolve()
    archive_root = _path(root, 'prototype-evidence')
    sources = {'contract': args.contract, 'manifest': args.manifest, 'snapshot': args.snapshot,
               'visual_review': args.visual_review}
    values = dict(contract=contract, manifest=manifest, snapshot=snapshot, visual_review=review)
    raw = {key: path.read_bytes() for key, path in sources.items()}
    if any(json.loads(raw[key]) != values[key] for key in sources):
        return ['封存输入已变化，请重新读取并评审，未覆盖当前需求']
    initial = {path: hashlib.sha256(raw[key]).hexdigest() for key, path in sources.items()}
    descriptors = [review['reference_input'], review['artifacts']['side_by_side'],
                   review['artifacts']['overlay'], *review['artifacts']['crops']]
    edge=review.get('background_edge_review',{})
    if (not isinstance(edge,dict) or not isinstance(edge.get('artifacts',{}),dict)
            or not isinstance(edge.get('files',[]),list)):
        raise ValueError('背景边缘证据结构无效')
    descriptors.extend(edge.get('artifacts',{}).values())
    artifacts = [_path(args.visual_review.parent, item['path']) for item in descriptors]
    implementation = _path(args.exports_dir, review['implementation_export']['path'])
    export_paths={review['implementation_export']['path']:implementation}
    for item in edge.get('files',[]):
        export_paths[item['path']]=_path(args.exports_dir,item['path'])
    initial.update({path: file_sha256(path) for path in [*artifacts, *export_paths.values()]})
    spec = root / 'project-spec.json'
    spec_hash = file_sha256(spec) if spec.is_file() else None
    target_failures = _target_failures(snapshot, spec)
    if target_failures:
        return target_failures
    if contract.get('prototype_evidence') and not checkpoint_failures(args.contract, contract):
        receipt_path = _record(root, contract['prototype_evidence'])
        receipt = _read(receipt_path)
        same = all(file_sha256(path) == receipt[key]['sha256'] for key, path in (
            ('manifest', args.manifest), ('snapshot', args.snapshot), ('visual_review', args.visual_review)))
        implementation = _path(args.exports_dir, review['implementation_export']['path'])
        if same and file_sha256(implementation) == review['implementation_sha256']:
            if snapshot.get('config',{}).get('background_id'):
                from background_edges import validate_edge_review
                source=manifest.get('source_strategy',{})
                hashes=[value for key in ('reference_image_hashes','adopted_image_hashes','reused_image_hashes')
                        for value in source.get(key,[])]
                return validate_edge_review(snapshot,args.exports_dir,review.get('background_edge_review'),
                                            args.visual_review.parent,hashes)
            return []
    checks = validate_workflow_binding(manifest, args.contract, args.visual_review, args.snapshot,
                                       args.exports_dir, snapshot, required_gate='reference')
    failures = [item['explanation'] for item in checks if item['status'] != 'pass']
    if failures:
        return failures

    identity = ''.join(initial.values()) + (spec_hash or '')
    bundle = archive_root / hashlib.sha256(identity.encode()).hexdigest()
    if bundle.exists():
        return ['已有同名样稿封存，保留原记录；请核对旧证据或重新采集']
    with tempfile.TemporaryDirectory(prefix='.prototype-', dir=root) as directory:
        staging = Path(directory)
        receipt = {'schema_version': 1, 'project_spec_sha256': spec_hash, 'exports_dir': 'exports'}
        copies = []
        for key, source in sources.items():
            relative = 'review/' + source.name if key == 'visual_review' else key + '.json'
            target = _path(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copies.append((source, target))
            receipt[key] = {'path': relative, 'sha256': file_sha256(target)}
        for item, source in zip(descriptors, artifacts):
            target = _path(staging / 'review', item['path'])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copies.append((source, target))
        for relative,source in export_paths.items():
            target = _path(staging / 'exports', relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copies.append((source, target))
        receipt_path = staging / 'receipt.json'
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        copied = {key: _record(staging, receipt[key]) for key in sources}
        checks = validate_workflow_binding(_read(copied['manifest']), copied['contract'],
            copied['visual_review'], copied['snapshot'], staging / 'exports',
            _read(copied['snapshot']), required_gate='reference')
        failures = [item['explanation'] for item in checks if item['status'] != 'pass']
        if any(file_sha256(source) != initial[source] or file_sha256(target) != initial[source]
               for source, target in copies) or (
            (file_sha256(spec) if spec.is_file() else None) != spec_hash
        ):
            failures.append('封存期间输入已变化，请重新采集和评审')
        if failures:
            return failures
        updated = dict(contract, status='draft_reviewable', gates=dict(contract['gates'], prototype='pass'),
                       prototype_evidence={'path': str((bundle / 'receipt.json').relative_to(root)),
                                           'sha256': file_sha256(receipt_path)})
        bundle.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, bundle)
    with tempfile.NamedTemporaryFile(prefix='.prototype-contract-', dir=root, mode='w', encoding='utf-8', delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(updated, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    try:
        if any(file_sha256(path) != expected for path, expected in initial.items()) or (
            (file_sha256(spec) if spec.is_file() else None) != spec_hash
        ):
            return ['提交封存前输入已变化，未覆盖当前需求；保留独立证据副本']
        os.replace(temporary, args.contract)
    finally:
        temporary.unlink(missing_ok=True)
    return []
