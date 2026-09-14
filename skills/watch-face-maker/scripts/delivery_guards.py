"""复核已确认制作决策与原始节点；不写Figma，不推断图标语义或审美。"""
import math
from pointer_checks import _screen_transform, transform_at


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
            if id in source_only or id not in exported or not n.get('export_settings'):errors.append(id+':独立资源未正式导出')
            if in_bg:errors.append(id+':独立变化内容同时固定在背景')
    return check('resource_ownership',errors,unknown,'交叉核对已确认逐元素行为、背景源实例、展示引用、source_only与正式导出；不按模块动态性推断图标')


def validate_delivery_guards(snapshot):
    nodes={n['id']:n for n in snapshot.get('nodes',[])}
    return [fn(snapshot,nodes) for fn in (icon_orientation,state_layout,pointer_annotations,resource_ownership)]
