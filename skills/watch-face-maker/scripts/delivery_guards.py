"""复核已确认制作决策与原始节点；不写Figma，不推断图标语义或审美。"""
import math
from pathlib import PurePosixPath
from pointer_checks import _screen_transform, transform_at
from stroke_geometry import validate_stroke_geometry


def check(name, errors, unknown, explanation):
    return dict(id='snapshot.'+name, status='fail' if errors else 'unverified' if unknown else 'pass',
                explanation=explanation, locations=errors+unknown)


def under(n, root, nodes):
    seen=set()
    while n and n['id'] not in seen:
        if n['id']==root: return True
        seen.add(n['id']);n=nodes.get(n.get('parent_id'))
    return False


def visible(n, root, nodes):
    seen=set()
    while n and n['id'] not in seen:
        if n.get('visible') is False or n.get('opacity',1)<=0: return False
        if n['id']==root: return True
        seen.add(n['id']);n=nodes.get(n.get('parent_id'))
    return None


def close(a,b):
    return len(a)==len(b) and all(math.isfinite(x) and math.isfinite(y) and abs(x-y)<=.001 for x,y in zip(a,b))


def node_box(n):
    try:
        w,h=n['width'],n['height'];t=n.get('absolute_transform') or n.get('relative_transform')
        if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in (w,h)) or w<=0 or h<=0:
            return None
        if t:
            points=[[t[r][0]*x+t[r][1]*y+t[r][2] for r in range(2)] for x,y in ((0,0),(w,0),(0,h),(w,h))]
            xs=[p[0] for p in points];ys=[p[1] for p in points]
            return min(xs),min(ys),max(xs),max(ys)
        x,y=n['x'],n['y']
        return x,y,x+w,y+h
    except (KeyError,TypeError,ValueError,IndexError):
        return None


def resource_board_layout(s,nodes):
    errors,unknown=[],[];root_id=s.get('config',{}).get('asset_board_id');root=nodes.get(root_id)
    if s.get('resource_board_layout') is not True:
        return check('resource_board_layout',errors,['未声明全资源分区排布检查'],'检查资源分区直属可见节点的跨族碰撞与越界')
    if not root:
        return check('resource_board_layout',errors,[str(root_id or '缺少asset_board_id')],'检查资源分区直属可见节点的跨族碰撞与越界')
    root_box=node_box(root)
    if not root_box:unknown.append(root_id+':分区边界无效')
    members=[]
    for n in nodes.values():
        if n.get('parent_id')!=root_id or n.get('type')=='SLICE' or n.get('visible') is False or n.get('opacity',1)<=0:
            continue
        box=node_box(n)
        if not box:unknown.append(n['id']+':边界无效');continue
        members.append((n,box))
        if root_box and (box[0]<root_box[0]-.001 or box[1]<root_box[1]-.001 or box[2]>root_box[2]+.001 or box[3]>root_box[3]+.001):
            errors.append(n['id']+':超出资源分区')
    if not members:unknown.append(root_id+':没有可检查的直属可见节点')
    for i,(a,ab) in enumerate(members):
        for b,bb in members[i+1:]:
            if min(ab[2],bb[2])-max(ab[0],bb[0])>.001 and min(ab[3],bb[3])-max(ab[1],bb[1])>.001:
                errors.append(a['id']+'↔'+b['id']+':直属资源相交')
    return check('resource_board_layout',sorted(set(errors)),sorted(set(unknown)),f'检查资源分区 {len(members)} 个直属可见节点，覆盖母件、标题、预览与用户保留对照节点')


def native_progress(s,nodes,capability_modes=None):
    errors,unknown=[],[];entries=s.get('native_progress',[]);source_only=set(s.get('source_only_node_ids',[]))
    if isinstance(capability_modes,dict) and not entries and all(mode=='state_images' for mode in capability_modes.values()):
        return dict(id='snapshot.native_progress',status='not_applicable',explanation='已声明未使用原生连续进度',locations=[])
    if not entries:return check('native_progress',errors,['缺少原生连续进度映射'],'核对原生连续进度示意母件、可编辑几何及非PNG交付属性')
    if not isinstance(entries,list):return check('native_progress',['native_progress必须是数组'],unknown,'核对原生连续进度示意母件、可编辑几何及非PNG交付属性')
    seen=set()
    for entry in entries:
        if not isinstance(entry,dict):errors.append(str(entry));continue
        capability=entry.get('capability');id=entry.get('node_id');shape=entry.get('shape');n=nodes.get(id)
        if not isinstance(capability,str) or capability in seen:errors.append(str(capability)+':能力重复或缺失')
        else:seen.add(capability)
        if not n or n.get('type')!='COMPONENT':errors.append(str(id)+':示意母件缺失');continue
        if visible(n,s.get('config',{}).get('asset_board_id'),nodes) is not True:errors.append(id+':示意母件不可见或不在资源分区')
        if id not in source_only or n.get('export_settings'):errors.append(id+':原生示意不得作为PNG导出')
        if shape not in ('arc','linear'):errors.append(id+':shape必须为arc或linear')
        geometry=s.get('stroke_geometry')
        if geometry is None:unknown.append(id+':缺少描边几何证据')
        elif not isinstance(geometry,list):errors.append(id+':描边几何清单无效')
        else:
            related=[g for g in geometry if isinstance(g,dict) and isinstance(g.get('root_id'),str) and g.get('role')=='progress'
                     and g.get('shape') in (('arc','circle') if shape=='arc' else ('linear',))
                     and under(nodes.get(g.get('root_id')),id,nodes)]
            if not related:errors.append(id+':原生进度没有关联实际描边路径')
            else:
                checked=validate_stroke_geometry(s,nodes,related)
                if checked['status']=='fail':errors.extend(id+':'+v for v in checked['locations'])
                elif checked['status']!='pass':unknown.extend(id+':'+v for v in checked['locations'])
        if not isinstance(entry.get('framework_capability_source'),str) or not entry.get('framework_capability_source').strip():unknown.append(id+':缺少框架能力来源')
    return check('native_progress',sorted(set(errors)),sorted(set(unknown)),'核对原生连续进度示意母件、可编辑几何及非PNG交付属性；不代表固件接入或真机通过')


def basis(t):
    """保留旋转/反射，排除父画布平移与实例尺寸造成的缩放。"""
    lengths=[math.hypot(t[0][c],t[1][c]) for c in range(2)]
    if min(lengths)<=0: raise ValueError('零缩放')
    return [[t[r][c]/lengths[c] for c in range(2)] for r in range(2)]


def icon_orientation(s,nodes):
    errors,unknown=[],[]
    pairs=s.get('icon_orientations',[])
    if not pairs:unknown.append('缺少库源与实际实例朝向对应证据')
    for p in pairs:
        label=p.get('instance_id','未指定实例')
        try:
            n=nodes[label];source=p['source'];root=p['root_id']
            if not source.get('file_key') or not source.get('node_id') or not source.get('key'):raise KeyError('库来源')
            t,_,_=_screen_transform(n,nodes,{root})
            if t is None or root not in nodes:raise KeyError('祖先链')
            if n.get('main_component_key')!=source['key']:errors.append(label+':库引用不符')
            a=basis(t);b=basis(source['relative_transform']);r=basis(p.get('placement_transform',[[1,0,0],[0,1,0]]))
            expected=[[sum(r[i][k]*b[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
            if not close(sum(a,[]),sum(expected,[])):errors.append(label+':源与实例合成朝向不符')
            if visible(n,root,nodes) is False:errors.append(label+':不可见')
        except (KeyError,TypeError,ValueError,IndexError):unknown.append(label+':原始变换或来源不足')
    return check('icon_orientation',errors,unknown,'仅验证所列实例与库源的旋转/反射关系；实际轮廓、语义和渲染需逐项看图')


def state_layout(s,nodes):
    errors,unknown=[],[]
    families=s.get('state_families',[])
    if not families:unknown.append('缺少状态排列范围')
    for family in families:
        name=family.get('name','状态');layout=family.get('layout')
        if not layout:unknown.append(name+':缺少当前排布基准');continue
        try:
            parent=layout['parent_id'];ox,oy=layout['origin'];dx,dy=layout['step'];cols=layout['columns']
            if not isinstance(cols,int) or cols<1:raise ValueError('columns')
            prefix=family['node_name_prefix'];count=family['expected_count'];start=family.get('start_index',0);digits=family.get('index_width',3)
            members=[n for n in nodes.values() if n.get('type')=='COMPONENT' and n.get('name','').startswith(prefix)]
            if len(members)!=count:errors.append(name+':状态总数与槽位不符')
            boxes=[]
            for i in range(count):
                matches=[n for n in members if n['name']==prefix+str(start+i).zfill(digits)]
                if len(matches)!=1:errors.append(name+':缺失或重复槽位 '+str(i));continue
                n=matches[0];label=n['id'];v=visible(n,parent,nodes)
                if n.get('parent_id')!=parent or not close([n['x'],n['y']],[ox+i%cols*dx,oy+i//cols*dy]):errors.append(label+':实际位置或父分区不符')
                if v is False:errors.append(label+':状态不可见')
                elif v is None:unknown.append(label+':显隐祖先未采集')
                x,y,w,h=(n[k] for k in ('x','y','width','height'))
                if w<=0 or h<=0:errors.append(label+':尺寸无效')
                if any(min(x+w,bx+bw)-max(x,bx)>.001 and min(y+h,by+bh)-max(y,by)>.001 for bx,by,bw,bh in boxes):errors.append(label+':状态资源互相覆盖')
                boxes.append((x,y,w,h))
        except (KeyError,TypeError,ValueError,IndexError):unknown.append(name+':排布原始证据不足')
    return check('state_layout',errors,unknown,'核对实际母件槽位、父分区、显隐及相互覆盖；数量/序号检查仍独立执行')


def pointer_annotations(s,nodes):
    errors,unknown=[],[]
    pointers=s.get('pointers',[])
    if not pointers and s.get('face_type')=='digital':
        return dict(id='snapshot.pointer_annotations',status='not_applicable',explanation='数字表盘未使用指针',locations=[])
    if not pointers:unknown.append('缺少指针及标注对应关系')
    for p in pointers:
        role=p.get('role','指针');a=p.get('annotation')
        if not a:unknown.append(role+':缺少标注图形映射');continue
        try:
            root=a['frame_id'];frame=nodes[root];hand=nodes[a['instance_id']];dot=nodes[a['pivot_node_id']];line=nodes[a['line_node_id']];master=nodes[p['master_id']]
            if frame['type']!='FRAME' or hand['type']!='INSTANCE' or dot['type']!='ELLIPSE' or line['type']!='RECTANGLE':raise ValueError('标注图形类型未支持')
            if not close([hand['width'],hand['height']],[master['width'],master['height']]):errors.append(role+':标注实例尺寸与切图不符')
            for ink in (dot,line):
                if 'fills' not in ink and 'strokes' not in ink:unknown.append(ink['id']+':缺少实际绘制样式')
                elif not any(paint.get('visible',True) and paint.get('opacity',1)>0 for paint in ink.get('fills',[])+ink.get('strokes',[])):errors.append(ink['id']+':标注图形无可见填充或描边')
            offset=a.get('offset',[0,0]);center=[p['center'][i]+offset[i] for i in range(2)]
            t,_,_=_screen_transform(hand,nodes,{root});dt,_,_=_screen_transform(dot,nodes,{root});lt,_,_=_screen_transform(line,nodes,{root})
            if any(v is None for v in (t,dt,lt)):raise KeyError('标注祖先链')
            if hand.get('main_component_id')!=p['master_id'] or not close(sum(t,[]),sum(transform_at(p['pivot'],center,0),[])):errors.append(role+':标注指针未按实际轴心置于零位')
            point=lambda m,x,y:[m[r][0]*x+m[r][1]*y+m[r][2] for r in range(2)]
            if not close(point(dt,dot['width']/2,dot['height']/2),center):errors.append(role+':标注轴心点错误')
            top=point(lt,line['width']/2,0);bottom=point(lt,line['width']/2,line['height'])
            if not close([top[1],bottom[1],top[0]],[center[1]-p['pivot'][1],center[1],bottom[0]]):errors.append(role+':红线顶边或轴心端点错误')
            for n in (frame,hand,dot,line):
                if n.get('export_settings'):errors.append(n['id']+':标注不应导出')
                v=visible(n,root,nodes)
                if v is False:errors.append(n['id']+':标注图形隐藏')
                elif v is None:unknown.append(n['id']+':祖先未采集')
        except (KeyError,TypeError,ValueError,IndexError):unknown.append(role+':标注原始节点不足')
    return check('pointer_annotations',errors,unknown,'从实际指针、轴心点和红线复合变换计算零位与距离，不以标签文字替代图形')


def resource_ownership(s,nodes):
    errors,unknown=[],[]
    decisions=s.get('resource_ownership',[]);config=s.get('config',{})
    bg=config.get('background_id');roots=[config.get(k) for k in ('active_id','aod_id')]
    exported={i.get('node_id') for i in s.get('export_items',[])};source_only=set(s.get('source_only_node_ids',[]))
    family_exports=verified_family_exports(s,nodes)
    if not decisions:unknown.append('缺少逐元素拆分决定与母件对应关系')
    for d in decisions:
        id=d.get('master_id','未指定母件');n=nodes.get(id)
        if not n or not d.get('reason') or bg not in nodes:unknown.append(id+':来源或背景范围不足');continue
        instances=[i for i in nodes.values() if i.get('type')=='INSTANCE' and i.get('main_component_id')==id]
        def carried_by_background(i):
            seen=set()
            while i and i['id'] not in seen:
                if i['id']==bg or i.get('main_component_id')==bg:return True
                seen.add(i['id']);i=nodes.get(i.get('parent_id'))
            return False
        in_bg=[i for i in instances if carried_by_background(i)]
        displayed=[i for i in instances if not carried_by_background(i) and any(r and under(i,r,nodes) for r in roots)]
        behavior,destination=d.get('behavior'),d.get('destination')
        if behavior not in ('static','dynamic') or destination not in ('background','independent'):unknown.append(id+':拆分决定无效');continue
        if destination=='background':
            if behavior!='static':errors.append(id+':动态元素被固定到背景')
            if id not in source_only or id in exported or n.get('export_settings'):errors.append(id+':已合背景的源仍独立导出')
            if displayed:errors.append(id+':Active/AOD仍重复组装固定图标')
            if not in_bg:unknown.append(id+':未见背景源实例，烘焙内容需另验像素证据')
            elif any(visible(i,config.get('section_id',bg),nodes) is not True for i in in_bg):errors.append(id+':背景源实例不可见或祖先缺失')
        else:
            if behavior=='static' and not d.get('independent_behavior'):unknown.append(id+':静态资源独立行为未说明')
            export_id=family_exports.get(id,id)
            if id in source_only or export_id not in exported or not nodes.get(export_id,{}).get('export_settings'):errors.append(id+':独立资源未正式导出')
            if in_bg:errors.append(id+':独立变化内容同时固定在背景')
    return check('resource_ownership',errors,unknown,'交叉核对已确认逐元素行为、背景源实例、展示引用、source_only与正式导出；不按模块动态性推断图标')


def family_exportability(s,nodes):
    """核对已声明成员到本区正式PNG节点的一一映射，不推断字符需求。"""
    errors,unknown=[],[];families=s.get('resource_families');board=s.get('config',{}).get('asset_board_id');items=s.get('export_items',[])
    explanation='逐族核对本资源分区可导出母件/实例完整覆盖已声明成员；不推断应有字符或评价视觉风格'
    if families==[]:
        named_text=any('/Num/' in str(n.get('name','')) for n in nodes.values()) or any(
            isinstance(item,dict) and '/Num/' in str(item.get('path','')) for item in (items if isinstance(items,list) else []))
        if named_text or any(n.get('type')=='TEXT' for n in nodes.values()):
            return check('family_exportability',errors,['空族清单仍含文字节点或Num资源线索，须核实是否有遗漏字模'],explanation)
        return dict(id='snapshot.family_exportability',status='not_applicable',explanation='明确无资源族且快照未见文字节点；不代表语义识别完整',locations=[])
    if families is None:return check('family_exportability',errors,['缺少资源族清单'],explanation)
    if not isinstance(families,list):return check('family_exportability',['resource_families必须为数组'],unknown,explanation)
    if board not in nodes:unknown.append('缺少目标资源分区节点')
    if not isinstance(items,list):return check('family_exportability',['export_items必须为数组'],unknown,explanation)
    paths={};used_ids=set();used_names=set()
    for item in items:
        if isinstance(item,dict) and isinstance(item.get('path'),str):
            path=str(PurePosixPath(item['path']));paths[path]=paths.get(path,0)+1
    for family in families:
        if not isinstance(family,dict):errors.append('资源族必须为对象');continue
        name=str(family.get('name','未命名族'));members=family.get('node_ids');ids=family.get('export_node_ids')
        if not isinstance(members,list) or not members or any(not isinstance(id,str) or not id for id in members):
            errors.append(name+':node_ids必须为非空节点数组');continue
        if len(set(members))!=len(members):errors.append(name+':成员重复')
        for id in members:
            if nodes.get(id,{}).get('type')!='COMPONENT':errors.append(id+':成员母件不存在或不是COMPONENT')
        if ids is None:unknown.append(name+':缺少export_node_ids，未验证本区独立导出');continue
        if not isinstance(ids,list) or not ids or any(not isinstance(id,str) or not id for id in ids):
            errors.append(name+':export_node_ids必须为非空节点数组');continue
        covered=set()
        for id in ids:
            if id in used_ids:errors.append(id+':重复导出节点')
            used_ids.add(id);n=nodes.get(id)
            if not n:errors.append(id+':本区导出节点不存在');continue
            if board in nodes and visible(n,board,nodes) is not True:errors.append(id+':导出节点不在本区或不可见')
            if n.get('type') not in ('COMPONENT','INSTANCE'):errors.append(id+':导出节点必须为母件或实例')
            master=n.get('main_component_id') if n.get('type')=='INSTANCE' else id
            if master not in members:errors.append(id+':母件不属于所声明资源族')
            elif master in covered:errors.append(id+':同一成员有多个导出节点')
            else:covered.add(master)
            if n.get('type')=='INSTANCE' and master in nodes:
                dimensions=[v for key in ('width','height') for v in (n.get(key),nodes[master].get(key))]
                if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in dimensions):
                    unknown.append(id+':缺少实例与母件实际尺寸')
                elif any(abs(n[key]-nodes[master][key])>.001 for key in ('width','height')):
                    errors.append(id+':导出实例尺寸与母件不一致')
                transforms=[node.get('absolute_transform') for node in (n,nodes[master])]
                if any(not isinstance(t,list) or len(t)!=2 or any(not isinstance(row,list) or len(row)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in row) for row in t) for t in transforms):
                    unknown.append(id+':缺少导出实例与母件的实际绝对变换')
                elif any(abs(transforms[0][row][column]-transforms[1][row][column])>.001 for row in range(2) for column in range(2)):
                    errors.append(id+':导出实例方向与母件不一致')
                overrides=n.get('overrides')
                if not isinstance(overrides,list):unknown.append(id+':缺少导出实例原始覆盖记录')
                else:
                    for override in overrides:
                        if not isinstance(override,dict) or not isinstance(override.get('id'),str) or not isinstance(override.get('overriddenFields'),list):
                            unknown.append(id+':导出实例覆盖记录不完整');continue
                        fields=override['overriddenFields']
                        allowed={'name','exportSettings','x','y','relativeTransform'} if override['id']==id else set()
                        if any(not isinstance(field,str) or field not in allowed for field in fields):
                            errors.append(id+':导出实例含内容或内部几何覆盖，不能代替共享母件')
            node_name=n.get('name')
            if not isinstance(node_name,str) or not node_name:errors.append(id+':缺少资源名称')
            elif node_name in used_names:errors.append(id+':本区资源重名')
            else:used_names.add(node_name)
            exports=[item for item in items if isinstance(item,dict) and item.get('node_id')==id]
            if len(exports)!=1:errors.append(id+':必须唯一登记正式导出项');continue
            item=exports[0];path=item.get('path')
            if not isinstance(path,str) or not path or '\\' in path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or PurePosixPath(path).suffix!='.png':
                errors.append(id+':PNG相对导出路径无效')
            elif paths[str(PurePosixPath(path))]!=1:errors.append(id+':重复导出路径 '+path)
            if item.get('role')!='asset':errors.append(id+':资源族导出角色必须为asset')
            settings=n.get('export_settings',[])
            settings=[setting for setting in settings if isinstance(setting,dict) and setting.get('format')=='PNG'] if isinstance(settings,list) else []
            selected=item.get('setting',settings[0] if len(settings)==1 else None)
            if selected is None or selected not in settings:errors.append(id+':缺少明确匹配的PNG导出设置')
        for id in set(members)-covered:errors.append(name+':缺少本区可导出成员 '+id)
    return check('family_exportability',sorted(set(errors)),sorted(set(unknown)),explanation)


def verified_family_exports(s,nodes):
    """仅复用整族已验证的本区导出映射；旧缺证清单保持原行为。"""
    if family_exportability(s,nodes)['status']!='pass':return {}
    exports={}
    for family in s['resource_families']:
        for identity in family['export_node_ids']:
            node=nodes[identity]
            master=node['main_component_id'] if node['type']=='INSTANCE' else identity
            exports[master]=identity
    return exports


def validate_delivery_guards(snapshot,capability_modes=None):
    nodes={n['id']:n for n in snapshot.get('nodes',[])}
    return [fn(snapshot,nodes) for fn in (icon_orientation,state_layout,pointer_annotations,resource_ownership,resource_board_layout,family_exportability)] + [validate_stroke_geometry(snapshot,nodes),native_progress(snapshot,nodes,capability_modes)]
