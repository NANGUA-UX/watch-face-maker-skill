"""已确认项目事实与只读开局预检；不把需求当成制作证据。"""
from __future__ import annotations

import importlib.util
import shutil
import re
import sys
from screen_geometry import screen_spec

OPERATIONS = {'create', 'modify', 'organize', 'audit'}


def validate_spec(spec):
    if not isinstance(spec, dict):
        return ['项目事实必须是对象']
    errors = []
    if type(spec.get('schema_version')) is not int or spec['schema_version'] != 1:
        errors.append('project-spec.schema_version 必须为1')
    if not isinstance(spec.get('operation'), str) or spec['operation'] not in OPERATIONS:
        errors.append('operation 必须为 create/modify/organize/audit')
    for field in ('target', 'confirmations', 'identity', 'source_strategy', 'figma', 'scope', 'capabilities', 'development', 'features', 'capability_modes', 'capability_presentations'):
        if field in spec and not isinstance(spec[field], dict):
            errors.append(field+' 必须是对象')
    for field in ('languages', 'themes', 'units', 'component_libraries'):
        if field in spec and (not isinstance(spec[field], list) or any(not isinstance(v, str) or not v.strip() for v in spec[field])):
            errors.append(field+' 必须是字符串数组')
    families = spec.get('text_families', [])
    if not isinstance(families, list) or any(not isinstance(f, dict) for f in families):
        errors.append('text_families 必须是对象数组')
    else:
        ids = [f.get('id') for f in families]
        if any(not isinstance(v, str) or not v.strip() for v in ids) or len(ids) != len(set(ids)):
            errors.append('字模族id必须唯一且非空')
        for family in families:
            if 'style' in family and not isinstance(family['style'], dict):
                errors.append('字模style必须是对象')
            if family.get('style') and family.get('style_source_node_id'):
                errors.append('字模style和style_source_node_id不能同时提供')
    target = spec.get('target', {})
    if isinstance(target, dict):
        for name in ('width', 'height'):
            if target.get(name) is not None and (type(target[name]) is not int or target[name] <= 0):
                errors.append('target.'+name+'必须为正整数或待确认的null')
    confirmations = spec.get('confirmations', {})
    if isinstance(confirmations, dict) and any(type(v) is not bool for v in confirmations.values()):
        errors.append('确认记录必须为布尔值；未知使用false')
    for field in ('scope', 'capabilities', 'development'):
        obj = spec.get(field, {})
        if isinstance(obj, dict):
            keys = {'scope': ('node_ids','instance_ids'), 'capabilities': ('requested',), 'development': ('supported',)}[field]
            for key in keys:
                if key in obj and (not isinstance(obj[key], list) or any(not isinstance(v,str) or not v for v in obj[key])):
                    errors.append(field+'.'+key+'必须是字符串数组')
    presentations = spec.get('capability_presentations', {})
    if isinstance(presentations, dict):
        requested = spec.get('capabilities', {})
        requested = requested.get('requested', []) if isinstance(requested, dict) else []
        requested = requested if isinstance(requested, list) else []
        for capability, presentation in presentations.items():
            if capability != 'battery' or not isinstance(presentation, str) or presentation not in ('numeric', 'progress') or capability not in requested:
                errors.append('capability_presentations 仅支持已请求battery的numeric/progress表现')
    if 'task_mode' in spec and spec['task_mode'] not in ('original','replicate','adapt'):
        errors.append('task_mode 必须为 original/replicate/adapt')
    if 'complexity' in spec and spec['complexity'] not in ('native','template','complex_visual'):
        errors.append('complexity 必须为 native/template/complex_visual')
    return errors


def item(identity, status, explanation):
    return dict(id=identity, status=status, explanation=explanation)


def preflight(spec, evidence=None, *, dependencies=None):
    errors = validate_spec(spec)
    if errors:
        return dict(exit_code=2, errors=errors, local_checks=[], figma_checks=[], routing=dict(write_allowed=False))
    if evidence is not None:
        if not isinstance(evidence, dict):
            errors.append('现场证据必须是对象')
        else:
            for field, kind in (('nodes',list),('fonts',list),('libraries',list),('source_fonts',dict),('permissions',dict)):
                if field in evidence and not isinstance(evidence[field],kind):
                    errors.append('现场证据'+field+'类型无效')
        if errors:
            return dict(exit_code=2, errors=errors, local_checks=[], figma_checks=[], routing=dict(write_allowed=False))
    operation = spec['operation']
    confirmed = spec.get('confirmations', {})
    local, remote = [], []
    target = spec.get('target', {})
    if not confirmed.get('target') or not all(target.get(k) is not None for k in ('width','height','shape')):
        local.append(item('requirements.target','unverified','目标尺寸与屏形尚未由用户确认，不采用默认尺寸'))
    else:
        try:
            canvas = dict(target)
            if canvas['shape'] == 'round_rect': canvas['shape'] = 'rounded_rect'
            screen_spec(canvas)
            local.append(item('requirements.target','pass','目标规格与正式校验器一致'))
        except ValueError as exc:
            local.append(item('requirements.target','fail','需要适配：'+str(exc)))
    source = spec.get('source_strategy', {})
    source_ok = confirmed.get('source_strategy') and source.get('mode') in ('new','modify','reuse_allowed','reuse_forbidden')
    local.append(item('requirements.source','pass' if source_ok else 'unverified','素材来源与复用权限已确认' if source_ok else '素材来源与复用权限待确认'))
    requested = spec.get('capabilities', {}).get('requested', [])
    development = spec.get('development', {})
    dev_ok = confirmed.get('development') and bool(development.get('source')) and set(requested).issubset(development.get('supported', []))
    local.append(item('requirements.development','pass' if dev_ok else 'unverified','本次能力有开发依据' if dev_ok else '本次能力或开发来源待核实'))
    if 'battery' in requested:
        explicit = 'battery' in spec.get('capability_presentations', {})
        local.append(item('requirements.presentations','pass' if explicit else 'unverified','电量表现已显式登记' if explicit else '电量表现待确认：numeric或progress；不推断旧项目表现'))
    if operation != 'audit':
        scope_ok = confirmed.get('write_scope') is True and bool(spec.get('scope', {}).get('node_ids'))
        local.append(item('requirements.write_scope','pass' if scope_ok else 'unverified','用户已授权本次修改范围' if scope_ok else '缺少用户授权的具体修改范围'))
    dependencies = dependencies if dependencies is not None else dict(python=sys.version_info >= (3,9),pillow=importlib.util.find_spec('PIL') is not None,node=shutil.which('node') is not None)
    for key in ('python','pillow','node'):
        local.append(item('dependencies.'+key,'pass' if dependencies.get(key) is True else 'fail','运行依赖可用' if dependencies.get(key) is True else '缺少运行依赖：'+key))
    figma = spec.get('figma', {})
    target_id = figma.get('section_id') or figma.get('page_id')
    remote_evidence = isinstance(evidence, dict) and evidence.get('origin') == 'figma_readonly' and bool(evidence.get('capture_signature'))
    if not remote_evidence:
        remote.append(item('figma.target','unverified','缺少当前Figma只读采集；不能猜测连接或目标权限'))
    else:
        nodes = evidence.get('nodes', [])
        matches = evidence.get('file_key') == figma.get('file_key') and evidence.get('target_id') == target_id and any(isinstance(n,dict) and n.get('id') == target_id for n in nodes if isinstance(nodes,list))
        remote.append(item('figma.target','pass' if matches else 'fail','目标读取与声明范围一致' if matches else '现场证据的文件或目标不一致'))
    e = evidence if remote_evidence else {}
    permissions = e.get('permissions')
    if operation == 'audit':
        perm_ok = isinstance(permissions,dict) and permissions.get('read') is True
        remote.append(item('figma.permissions','pass' if perm_ok else 'unverified','只读审核不要求写权限' if perm_ok else '缺少读取权限证据'))
    else:
        basis = permissions.get('basis') if isinstance(permissions,dict) else None
        basis_ok = isinstance(basis,dict) and basis.get('file_key') == figma.get('file_key') and basis.get('kind') in ('successful_write','file_creation') and isinstance(basis.get('response_sha256'),str) and re.fullmatch(r'[a-fA-F0-9]{64}',basis['response_sha256']) is not None
        if basis_ok and basis['kind'] == 'successful_write':
            changes = basis.get('changed_node_ids')
            basis_ok = isinstance(changes,list) and bool(changes) and all(isinstance(v,str) and v for v in changes)
        perm_ok = isinstance(permissions,dict) and permissions.get('read') is True and permissions.get('write') is True and basis_ok and permissions.get('receipt_validation') != 'invalid'
        denied = isinstance(permissions,dict) and permissions.get('write') is False
        first_action = isinstance(permissions,dict) and permissions.get('read') is True and not denied and 'basis' not in permissions and permissions.get('receipt_validation') != 'invalid'
        explanation = ('已有成功写入回执支持技术权限' if perm_ok else ('现场明确拒绝写入，停止制作' if denied else ('尚无历史写入回执；仅允许授权范围内首次必要制作，成功后保存真实响应' if first_action else '读取权限或已有写入回执无效；重新读取现场并核对原始响应')))
        remote.append(item('figma.permissions','pass' if perm_ok else ('fail' if denied else 'unverified'),explanation))
    required_fonts, font_unknown = [], False
    for family in spec.get('text_families', []):
        style = family.get('style', {})
        font = style.get('font_name')
        if family.get('style_source_node_id'):
            font = e.get('source_fonts', {}).get(family['style_source_node_id'])
        if isinstance(font,dict) and all(isinstance(font.get(k),str) for k in ('family','style')):
            required_fonts.append((font['family'],font['style']))
        else: font_unknown = True
    fonts = e.get('fonts')
    if not spec.get('text_families'):
        remote.append(item('figma.fonts','not_applicable','当前未准备字模族；建立字模前再预检字体'))
    elif not isinstance(fonts,list) or font_unknown:
        remote.append(item('figma.fonts','unverified','缺少当前可用字体或源文字字体采集'))
    else:
        available = {(f.get('family'),f.get('style')) for f in fonts if isinstance(f,dict)}
        missing = [f for f in required_fonts if f not in available]
        remote.append(item('figma.fonts','fail' if missing else 'pass','缺少字体：'+str(missing) if missing else '实际字体与样式可用，不替换字体'))
    required_libraries = spec.get('component_libraries', [])
    libraries = e.get('libraries')
    if not required_libraries:
        remote.append(item('figma.libraries','not_applicable','本次未声明组件库要求'))
    elif not isinstance(libraries,list):
        remote.append(item('figma.libraries','unverified','缺少指定组件库的当前检索证据'))
    else:
        found = {v.get('key') for v in libraries if isinstance(v,dict) and v.get('accessible') is True}
        missing = set(required_libraries)-found
        remote.append(item('figma.libraries','fail' if missing else 'pass','指定组件库不可用：'+str(sorted(missing)) if missing else '指定组件库可检索'))
    checks = local+remote
    required = [c for c in checks if c['id'] != 'figma.permissions']
    ready = all(c['status'] in ('pass','not_applicable') for c in required)
    permission_status = next(c['status'] for c in remote if c['id'] == 'figma.permissions')
    permission_ready = perm_ok if operation == 'audit' else (perm_ok or first_action)
    ready = ready and permission_ready
    write_ready = bool(ready and operation != 'audit')
    routing = dict(operation=operation,start_stage='intake' if operation=='create' else ('audit' if operation=='audit' else 'assembly'),write_allowed=write_ready)
    details = []
    for check in checks:
        if check['status'] in ('pass','not_applicable') or (check['id'] == 'figma.permissions' and permission_ready):
            continue
        key = check['id']
        category = 'implementation_error'
        if key.startswith('requirements.'):
            category = 'unsupported' if check['status'] == 'fail' else 'missing_user_fact'
        elif key.startswith('figma.') and check['status'] == 'unverified':
            category = 'stale_evidence'
        action = {
            'requirements.target':'补齐并确认目标尺寸、屏形；非支持规格先适配screen_spec',
            'requirements.source':'记录用户确认的素材来源与复用范围',
            'requirements.development':'补充开发来源及其支持的实际能力',
            'requirements.write_scope':'记录已获用户授权的scope.node_ids与confirmations.write_scope',
            'requirements.presentations':'确认并登记capability_presentations.battery为numeric或progress',
            'figma.target':'重新只读采集当前文件和目标节点，核对file_key与target_id',
            'figma.permissions':'核对读取/明确拒绝状态与原始写入响应；不得探测性试写',
            'figma.fonts':'只读采集可用字体与源节点字体，按原字体补齐',
            'figma.libraries':'检索声明组件库并保存真实可访问结果',
        }.get(key, '安装缺少的运行依赖后重新执行preflight')
        details.append(dict(id=key, category=category, explanation=check['explanation'], next_step=action))
    next_step = ('只读审核当前阶段证据' if operation == 'audit' and ready else
                 '按当前阶段在授权范围内执行必要制作；成功后保存真实工具响应并重新preflight' if write_ready else
                 details[0]['next_step'] if details else '重新读取现场证据')
    return dict(exit_code=0 if ready else 1,local_checks=local,figma_checks=remote,routing=routing,
                write_ready=write_ready,permissions=permission_status,production_validation='unverified',
                production_ready=False,blocker_details=details,next_step=next_step)
