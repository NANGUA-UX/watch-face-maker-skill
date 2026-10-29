"""只读复核登记的单根描边几何；不推断图标语义或替代视觉评审。"""
import math


def _number(value):
    if value is None:
        raise LookupError('缺少实际数值')
    try:finite=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    except OverflowError:finite=False
    if not finite:
        raise ValueError('几何或描边数值无效')
    return value


def _point(value):
    if not isinstance(value,dict):
        raise ValueError('路径坐标必须是对象')
    return [_number(value.get(k)) for k in ('x','y')]


def _painted(paints):
    if paints is None:
        raise LookupError('缺少实际填充或描边')
    if not isinstance(paints,list) or any(not isinstance(p,dict) for p in paints):
        raise ValueError('填充或描边记录无效')
    painted=False
    for paint in paints:
        opacity=_number(paint.get('opacity',1))
        if not 0<=opacity<=1:
            raise ValueError('绘制不透明度无效')
        if not paint.get('visible',True) or opacity==0:
            continue
        kind=paint.get('type')
        if kind=='SOLID':
            color=paint.get('color')
            if not isinstance(color,dict):raise LookupError('缺少描边颜色')
            if any(not 0<=_number(color.get(k))<=1 for k in ('r','g','b')):
                raise ValueError('颜色值无效')
            painted=True
        elif isinstance(kind,str) and kind.startswith('GRADIENT_'):
            if kind not in ('GRADIENT_LINEAR','GRADIENT_RADIAL','GRADIENT_ANGULAR','GRADIENT_DIAMOND'):
                raise ValueError('渐变类型无效')
            transform=paint.get('gradientTransform')
            if transform is None:raise LookupError('缺少实际渐变变换')
            if not isinstance(transform,list) or len(transform)!=2 or any(not isinstance(row,list) or len(row)!=3 for row in transform):
                raise ValueError('渐变变换结构无效')
            for row in transform:
                for value in row:_number(value)
            stops=paint.get('gradientStops')
            if not isinstance(stops,list) or len(stops)<2:raise LookupError('渐变色标缺失')
            alpha=[]
            for stop in stops:
                if not isinstance(stop,dict) or not isinstance(stop.get('color'),dict):raise ValueError('渐变色标无效')
                if not 0<=_number(stop.get('position'))<=1:raise ValueError('渐变色标位置无效')
                if any(not 0<=_number(stop['color'].get(k))<=1 for k in ('r','g','b')):raise ValueError('渐变颜色无效')
                alpha.append(_number(stop['color'].get('a')))
            if any(not 0<=a<=1 for a in alpha):raise ValueError('渐变透明度无效')
            if any(a>0 for a in alpha):painted=True
        else:
            raise LookupError('当前绘制类型须另行核对：'+str(kind))
    return painted


def stroke_colors(node):
    """从实际描边取颜色样本；图像/叠加材质不能仅凭类型证明非纯色。"""
    paints=node.get('strokes')
    if not _painted(paints):raise LookupError('缺少可见进度描边颜色')
    paints=[p for p in paints if p.get('visible',True) and p.get('opacity',1)>0]
    if len(paints)!=1:raise LookupError('多层描边颜色须另行核对')
    paint=paints[0];opacity=paint.get('opacity',1)
    if paint['type']=='SOLID':
        return {tuple(paint['color'][k] for k in ('r','g','b'))+(opacity,)}
    stops=paint['gradientStops']
    if len({s['position'] for s in stops})<2:raise LookupError('渐变色标没有可核对的跨度')
    return {tuple(s['color'][k] for k in ('r','g','b'))+(s['color']['a']*opacity,) for s in stops}


def _under(identity,root,nodes):
    seen=set()
    while identity in nodes and identity not in seen:
        if identity==root:return True
        seen.add(identity);identity=nodes[identity].get('parent_id')
    return False


def _open_network(node):
    net=node.get('vectorNetwork')
    if not isinstance(net,dict):raise LookupError('缺少原始vectorNetwork，不能证明路径开放')
    vertices,segments=net.get('vertices'),net.get('segments')
    if not isinstance(vertices,list) or not isinstance(segments,list):raise ValueError('矢量拓扑结构无效')
    if len(vertices)<2 or not segments:raise ValueError('开放路径为空或退化')
    points=[_point(v) for v in vertices];neighbors=[[] for _ in points];controls=[]
    for segment in segments:
        if not isinstance(segment,dict):raise ValueError('路径段无效')
        a,b=segment.get('start'),segment.get('end')
        if any(isinstance(i,bool) or not isinstance(i,int) or not 0<=i<len(points) for i in (a,b)) or a==b:
            raise ValueError('路径端点索引无效')
        neighbors[a].append(b);neighbors[b].append(a)
        for key,i in (('tangentStart',a),('tangentEnd',b)):
            handle=_point(segment.get(key,{'x':0,'y':0}))
            controls.append([points[i][j]+handle[j] for j in (0,1)])
    ends=[i for i,n in enumerate(neighbors) if len(n)==1]
    if len(ends)!=2 or any(len(n) not in (1,2) for n in neighbors):raise ValueError('路径闭合、分叉或包含孤立点')
    seen=set();stack=[ends[0]]
    while stack:
        i=stack.pop()
        if i in seen:continue
        seen.add(i);stack.extend(neighbors[i])
    if len(seen)!=len(points):raise ValueError('路径不连通')
    # Figma 也为开放 vectorPaths 返回隐式区域；开放性由连接拓扑决定。
    regions=net.get('regions')
    if regions is None:raise LookupError('缺少实际矢量区域填充证据')
    if not isinstance(regions,list):raise ValueError('矢量区域结构无效')
    for region in regions:
        if not isinstance(region,dict):raise ValueError('矢量区域结构无效')
        if _painted(region.get('fills')):raise ValueError('开放路径区域存在可见填充')
    start,end=[points[i] for i in ends];dx,dy=end[0]-start[0],end[1]-start[1]
    length=math.hypot(dx,dy)
    if length<1e-7:raise ValueError('开放路径端点重合')
    linear=all(abs(dx*(p[1]-start[1])-dy*(p[0]-start[0]))<=length*1e-5 for p in points+controls)
    return [vertices[i] for i in ends],linear


def ellipse_geometry(path,shape):
    """复核原生圆弧/整圆的中心曲线；绘制与端帽由调用方另行核对。"""
    if path.get('type')!='ELLIPSE':raise LookupError('缺少原生椭圆几何证据')
    if shape not in ('arc','circle'):raise ValueError('原生椭圆与登记形态不符')
    w,h=_number(path.get('width')),_number(path.get('height'))
    if min(w,h)<=0 or abs(w-h)>1e-5:raise ValueError('原生圆弧或整圆须为非退化正圆')
    arc=path.get('arcData')
    if arc is None:raise LookupError('缺少原生椭圆实际arcData')
    if not isinstance(arc,dict):raise ValueError('原生椭圆arcData无效')
    start,end=[_number(arc.get(k)) for k in ('startingAngle','endingAngle')]
    sweep=abs(end-start);inner=_number(arc.get('innerRadius'))
    if shape=='circle':
        if abs(sweep-2*math.pi)>1e-5 or inner not in (0,1):
            raise ValueError('整圆实际为部分扇区或双边圆环')
    elif not 1e-7<sweep<2*math.pi-1e-5 or inner!=1:
        raise ValueError('原生圆弧须为不足一圈的单中心曲线，不能是扇区或双边圆环')
    return w,h,start,end


def _entry(entry,nodes,section):
    if not isinstance(entry,dict):raise ValueError('stroke_geometry条目必须是对象')
    root_id,path_id=entry.get('root_id'),entry.get('path_id')
    if not isinstance(root_id,str) or not isinstance(path_id,str):raise ValueError('缺少逻辑根或路径ID')
    root,path=nodes.get(root_id),nodes.get(path_id)
    if root is None or path is None:raise LookupError('逻辑根或路径未采集')
    if root_id==path_id or root.get('type') not in ('FRAME','GROUP','COMPONENT'):
        raise ValueError('逻辑根须为独立容器，不能以路径自身缩小检查范围')
    if not section or not _under(root_id,section,nodes):raise LookupError('逻辑根的分区祖先未采集')
    if not _under(path_id,root_id,nodes):raise ValueError('路径不属于登记逻辑根')
    if entry.get('role') not in ('progress','track','decoration'):raise ValueError('描边用途无效')
    shape,cap=entry.get('shape'),entry.get('cap')
    if shape not in ('arc','linear','circle') or cap not in ('NONE','ROUND','SQUARE'):raise ValueError('形态或预期端点无效')
    members=[n for n in nodes.values() if _under(n['id'],root_id,nodes)]
    for n in members:
        if n['id']==path_id:continue
        if n.get('type') not in ('FRAME','GROUP','COMPONENT'):
            raise ValueError('逻辑根内存在额外几何或独立端帽：'+n['id'])
        if _painted(n.get('fills')) or _painted(n.get('strokes')):raise ValueError('逻辑根内存在额外绘制容器')
        if n.get('clips_content') or n.get('clipsContent') or n.get('isMask'):raise LookupError('逻辑根裁切或遮罩需另核对')
    if _painted(path.get('fills')):raise ValueError('规则线条存在可见填充，应使用真实描边')
    if not _painted(path.get('strokes')):raise ValueError('没有可见描边')
    if _number(path.get('stroke_weight',path.get('strokeWeight')))<=0:raise ValueError('描边宽度须为正数')
    if path.get('isMask'):raise ValueError('描边路径不能作为遮罩')
    kind=path.get('type');ends=[]
    if kind=='ELLIPSE':
        ellipse_geometry(path,shape)
        if shape=='circle':return
        ends=[{},{}]
    elif shape=='circle':raise LookupError('整圆须提供原生椭圆闭环证据')
    elif kind=='LINE' and shape=='linear':
        if math.hypot(_number(path.get('width')),_number(path.get('height')))<1e-7:raise ValueError('直线长度为零')
        ends=[{},{}]
    elif kind=='VECTOR':
        ends,linear=_open_network(path)
        if (shape=='linear')!=linear:raise ValueError('实际路径曲直与登记形态不符')
    else:
        raise LookupError('缺少可证明开放性的描边路径')
    for vertex in ends:
        actual=vertex.get('strokeCap',path.get('strokeCap'))
        if actual is None or actual=='mixed':raise LookupError('缺少端点实际样式或顶点覆盖')
        if actual!=cap:raise ValueError('实际端点样式与预期不符')


def validate_stroke_geometry(snapshot,nodes,entries=None):
    errors,unknown=[],[]
    values=snapshot.get('stroke_geometry') if entries is None else entries
    if values is None:unknown.append('缺少stroke_geometry；旧记录不能自动取得新通过')
    elif not isinstance(values,list):errors.append('stroke_geometry必须是数组')
    elif not values:
        if snapshot.get('native_progress'):errors.append('已有原生进度却声明没有描边几何')
        else:return dict(id='snapshot.stroke_geometry',status='not_applicable',explanation='明确声明没有适用规则线条；清单完整性仍需设计评审',locations=[])
    else:
        state=snapshot.get('pagination',{})
        if (state.get('complete') is not True or state.get('total_nodes')!=len(nodes)
                or state.get('collected_nodes')!=len(nodes) or len(nodes)!=len(snapshot.get('nodes',[]))):
            unknown.append('描边逻辑根需要完整无重复节点快照')
        roots=set();paths=set()
        for entry in values:
            try:
                if isinstance(entry,dict):
                    root,path=entry.get('root_id'),entry.get('path_id')
                    if not isinstance(root,str) or not isinstance(path,str):raise ValueError('描边ID无效')
                    if root in roots or path in paths:raise ValueError('描边逻辑根或路径重复登记')
                    roots.add(root);paths.add(path)
                _entry(entry,nodes,snapshot.get('config',{}).get('section_id'))
            except LookupError as exc:unknown.append(str(exc))
            except (ValueError,TypeError,AttributeError) as exc:errors.append(str(exc))
    return dict(id='snapshot.stroke_geometry',status='fail' if errors else 'unverified' if unknown else 'pass',
                explanation='核对登记逻辑根的真实单路径、无填充描边与原生端点；不自动发现漏登记对象或证明视觉质量',locations=errors+unknown)
