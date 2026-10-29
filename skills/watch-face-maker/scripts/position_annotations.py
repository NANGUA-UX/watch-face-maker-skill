"""从当前原生节点核对位置标注；图面可读性另由绑定PNG的人工评审确认。"""
import math
import re
from pointer_checks import _screen_transform, transform_at
from screen_geometry import screen_spec
from stroke_geometry import _number, _open_network, _painted, _under, ellipse_geometry


def _close(a, b):
    return len(a) == len(b) and all(abs(_number(x)-_number(y)) <= .001 for x, y in zip(a, b))


def _point(t, p):
    return [sum(t[r][c]*p[c] for c in (0, 1))+t[r][2] for r in (0, 1)]


def _transform(n, nodes, root):
    t, parent, _ = _screen_transform(n, nodes, {root})
    if t is None or parent != root:
        raise LookupError(n['id']+':缺少完整变换祖先链')
    for row in t:
        for v in row: _number(v)
    return t


def _get(nodes, id):
    if id not in nodes: raise LookupError(str(id)+':节点未采集')
    return nodes[id]


def _visible(n, nodes, root):
    seen = set()
    while n and n['id'] not in seen:
        seen.add(n['id'])
        if n.get('visible') is False or _number(n.get('opacity', 1)) <= 0:
            raise ValueError(n['id']+':标注或来源隐藏')
        if n['id'] == root: return
        n = nodes.get(n.get('parent_id'))
    raise LookupError('可见祖先链未采集')


def _rgb(paints):
    visible = [p for p in paints if p.get('visible', True) and p.get('opacity', 1) > 0]
    if len(visible) != 1 or visible[0].get('type') != 'SOLID':
        raise ValueError('标注须使用单一纯色绘制')
    if visible[0].get('opacity', 1) != 1: raise ValueError('标注纯色须不透明')
    return [_number(visible[0]['color'].get(k)) for k in ('r', 'g', 'b')]


def _regular_transform(t, unit=False):
    x, y = [math.hypot(t[0][i], t[1][i]) for i in (0, 1)]
    if min(x, y) <= 1e-7: return False
    dot = t[0][0]*t[0][1]+t[1][0]*t[1][1]
    determinant = t[0][0]*t[1][1]-t[0][1]*t[1][0]
    return _close([x, y, dot, abs(determinant)], [1, 1, 0, 1]) if unit else _close([x/y, dot/(x*y), abs(determinant)/(x*y)], [1, 0, 1])


def _ordered_curves(n, ends):
    network = n['vectorNetwork']; vertices = network['vertices']
    current = next(i for i, v in enumerate(vertices) if v is ends[0])
    remaining = list(network['segments']); curves = []
    while remaining:
        segment = next(s for s in remaining if current in (s['start'], s['end']))
        remaining.remove(segment)
        start, end = segment['start'], segment['end']
        p, q = ([vertices[i][k] for k in ('x', 'y')] for i in (start, end))
        handles = [segment.get(k, {'x': 0, 'y': 0}) for k in ('tangentStart', 'tangentEnd')]
        curve = [p, [p[i]+handles[0][k] for i, k in enumerate(('x', 'y'))],
                 [q[i]+handles[1][k] for i, k in enumerate(('x', 'y'))], q]
        if current == end: curve.reverse()
        curves.append(curve); current = end if current == start else start
    return curves


def _split(values):
    # de Casteljau subdivision preserves the exact curve and its convex-hull bound.
    left, right = [values[0]], [values[-1]]
    while len(values) > 1:
        values = [(a+b)/2 for a, b in zip(values, values[1:])]
        left.append(values[0]); right.append(values[-1])
    return left, list(reversed(right))


def _bounded(values, lower, upper, depth=0):
    if min(values) >= lower and max(values) <= upper: return True
    if not lower <= values[0] <= upper or not lower <= values[-1] <= upper: return False
    if depth == 24: return False  # 无法证明整段落在界内时保守拒绝，不能采样放行。
    return all(_bounded(part, lower, upper, depth+1) for part in _split(values))


def _product(a, b):
    m, n = len(a)-1, len(b)-1
    return [sum(math.comb(m, i)*math.comb(n, k-i)*a[i]*b[k-i]
                for i in range(max(0, k-n), min(m, k)+1))/math.comb(m+n, k)
            for k in range(m+n+1)]


def _linear_curves(curves):
    start, end = curves[0][0], curves[-1][-1]
    delta = [end[i]-start[i] for i in (0, 1)]; length = math.hypot(*delta)
    for curve in curves:
        position = [sum((p[i]-start[i])*delta[i] for i in (0, 1))/(length*length) for p in curve]
        derivative = [3*(b-a) for a, b in zip(position, position[1:])]
        if not _bounded(derivative, -1e-9, math.inf): raise ValueError('直线路径超出端点或发生反向回折')


def _sweep(x, y, depth=0):
    # 控制点全部位于起始半平面时，整段不可能隐藏额外半圈。
    if min(x[0]*a+y[0]*b for a, b in zip(x, y)) > 0:
        return math.atan2(x[0]*y[-1]-y[0]*x[-1], x[0]*x[-1]+y[0]*y[-1])
    if depth == 24: raise ValueError('圆弧完整扫角无法证明')
    return sum(_sweep(a, b, depth+1) for a, b in zip(_split(x), _split(y)))


def _arc_curves(curves, center, direction):
    radius = math.dist(curves[0][0], center); tolerance = max(.05, radius*.001)
    if radius <= tolerance: raise ValueError('圆弧半径无效')
    sign = 1 if direction == 'clockwise' else -1; sweep = 0
    for curve in curves:
        x, y = ([p[i]-center[i] for p in curve] for i in (0, 1))
        squared_radius = [a+b for a, b in zip(_product(x, x), _product(y, y))]
        if not _bounded(squared_radius, (radius-tolerance)**2, (radius+tolerance)**2):
            raise ValueError('开放路径并非登记轴心的规则圆弧')
        dx, dy = ([3*(b-a) for a, b in zip(values, values[1:])] for values in (x, y))
        cross = [sign*(a-b) for a, b in zip(_product(x, dy), _product(y, dx))]
        if not _bounded(cross, -1e-7, math.inf): raise ValueError('进度方向与开放路径完整切线不符')
        sweep += _sweep(x, y)
    if not 0 < sign*sweep < 2*math.pi-.001: raise ValueError('圆弧完整扫角范围无效')
    return sign*sweep


def _line(n, nodes, root, color=None, legacy=False):
    t = _transform(n, nodes, root)
    if legacy and n['type'] == 'RECTANGLE':
        ends = [[n['width']/2, 0], [n['width']/2, n['height']]]
        paints = n.get('fills')
    else:
        if _painted(n.get('fills')): raise ValueError(n['id']+':尺寸线不能有可见填充')
        if not _painted(n.get('strokes')) or _number(n.get('stroke_weight', n.get('strokeWeight'))) <= 0:
            raise ValueError(n['id']+':尺寸线缺少有效描边')
        paints = n.get('strokes')
        if n['type'] == 'LINE': ends = [[0, 0], [n['width'], n['height']]]
        elif n['type'] == 'VECTOR':
            vertices, linear = _open_network(n)
            if not linear: raise ValueError(n['id']+':尺寸线必须是开放直线')
            _linear_curves(_ordered_curves(n, vertices))
            ends = [[v['x'], v['y']] for v in vertices]
        else: raise ValueError(n['id']+':尺寸线须为LINE或开放VECTOR')
    if color == 'red' and not _close(_rgb(paints), [1, 0, 0]): raise ValueError(n['id']+':终点/尺寸线须为红色')
    if color == 'yellow':
        rgb = _rgb(paints)
        if not (.9 <= rgb[0] <= 1 and .6 <= rgb[1] <= 1 and 0 <= rgb[2] <= .1):
            raise ValueError(n['id']+':起点线须为黄色')
    return [_point(t, p) for p in ends]


def _numbers(n):
    if n.get('type') != 'TEXT': raise ValueError(n['id']+':标签须为真实TEXT')
    if not _painted(n.get('fills')) and not _painted(n.get('strokes')): raise ValueError(n['id']+':标签无可见绘制')
    return [float(v) for v in re.findall(r'[-+]?\d+(?:\.\d+)?', n.get('characters', ''))]


def _instance(n, master):
    if n.get('type') != 'INSTANCE' or n.get('main_component_id') != master['id']:
        raise ValueError(n['id']+':母件引用错误')
    if not _close([n['width'], n['height']], [master['width'], master['height']]):
        raise ValueError(n['id']+':实例必须保持母件1×尺寸')
    if not isinstance(n.get('overrides'),list):raise LookupError(n['id']+':缺少实例覆盖原始证据')
    for override in n['overrides']:
        allowed = {'name', 'x', 'y', 'relativeTransform', 'rotation', 'exportSettings'} if override.get('id') == n['id'] else set()
        if set(override.get('overriddenFields', []))-allowed:
            raise ValueError(n['id']+':标注/来源实例含内容或几何覆盖')


def _measure(a, nodes, frame, master, image, legacy=False):
    center, pivot = a['center'], a['pivot']
    expected = transform_at(pivot, center, 0)
    if not _close(sum(_transform(image, nodes, frame['id']), []), sum(expected, [])):
        raise ValueError(image['id']+':标注未按实际轴心处于参考零位')
    measurement = a['measurement']; line = _get(nodes, measurement['line_id'])
    ends = sorted(_line(line, nodes, frame['id'], 'red', legacy), key=lambda p: p[1])
    if a['kind'] == 'pointer':
        meaning, top, bottom = 'slice_top_to_pivot', center[1]-pivot[1], center[1]
    else:
        meaning, top, bottom = 'pivot_to_slice_bottom', center[1], center[1]-pivot[1]+master['height']
    if measurement['kind'] != meaning or not _close([ends[0][0], ends[0][1], ends[1][1]], [ends[1][0], top, bottom]):
        raise ValueError(line['id']+':尺寸线端点或测量含义错误')
    if bottom <= top: raise ValueError(line['id']+':尺寸距离无效')
    if not legacy:
        label = _get(nodes, measurement['label_id'])
        if not _close(_numbers(label), [bottom-top]) or 'px' not in label.get('characters', ''):
            raise ValueError(label['id']+':尺寸标签与切图边界不符')


def _angle(p, center):
    return math.degrees(math.atan2(center[0]-p[0], center[1]-p[1]))


def _angle_close(a, b):
    return abs((a-b+180) % 360-180) <= .001


def _progress(a, nodes, frame, master, image):
    center = a['center']; shape = a['shape']; root = frame['id']
    path = _get(nodes, a['source_path_id'])
    if not _under(path['id'], master['id'], nodes): raise ValueError('进度路径不属于引用母件')
    it = _transform(image, nodes, root)
    pt = _transform(path, nodes, master['id'])
    if not _close([it[0][0], it[0][1], it[1][0], it[1][1]], [1, 0, 0, 1]): raise ValueError('进度标注须保留原始方向与1×比例')
    transform = lambda p: _point(it, _point(pt, p))
    direction = a['direction']
    if direction not in ('clockwise', 'counterclockwise'): raise ValueError('进度方向无效')
    if path['type'] == 'ELLIPSE' and shape in ('arc', 'circle'):
        w, h, start, end = ellipse_geometry(path, shape)
        if w <= 0 or not _close([w], [h]) or not _regular_transform(pt): raise ValueError('进度不是规则圆形')
        actual_center = transform([w/2, h/2])
        if not _close(center, actual_center): raise ValueError('进度标注轴心与实际几何不符')
        determinant=pt[0][0]*pt[1][1]-pt[0][1]*pt[1][0]
        clockwise = (end-start)*determinant > 0
        # 单独验证方向，防止端点角度正确却改变实际增长方向。
        if (direction == 'clockwise') != clockwise: raise ValueError('进度方向与原始路径不符')
        if shape == 'circle':
            if a.get('full_turn') is not True or abs(abs(end-start)-2*math.pi) > .001: raise ValueError('整圆须明确完整一圈')
        elif not 0 < abs(end-start) < 2*math.pi-.001: raise ValueError('圆弧范围无效')
        endpoints = [transform([w/2+w/2*math.cos(v), h/2+h/2*math.sin(v)]) for v in (start, end)]
    elif shape in ('linear', 'arc') and path['type'] in ('LINE', 'VECTOR'):
        if path['type'] == 'LINE': points = [[0, 0], [path['width'], path['height']]]; linear = True
        else:
            vertices, linear = _open_network(path); points = [[v['x'], v['y']] for v in vertices]
            curves = _ordered_curves(path, vertices)
            if linear: _linear_curves(curves)
        if linear != (shape == 'linear'): raise ValueError('进度形态与原始路径不符')
        endpoints = [transform(p) for p in points]
        if shape == 'arc':
            if abs(math.dist(endpoints[0], center)-math.dist(endpoints[1], center)) > .001: raise ValueError('圆弧端点不共圆')
            sweep = _arc_curves([[transform(p) for p in curve] for curve in curves], center, direction)
            expected = ((a['start_angle']-a['end_angle']) if direction == 'clockwise' else (a['end_angle']-a['start_angle'])) % 360
            if abs(math.degrees(sweep)-expected) > .001: raise ValueError('圆弧完整扫角与登记范围不符')
    else: raise LookupError('进度完整路径尚无法证明')
    for index, key in enumerate(('start', 'end')):
        line = _get(nodes, a[key+'_line_id']); ends = _line(line, nodes, root, 'yellow' if index == 0 else 'red')
        label = _get(nodes, a[key+'_label_id']); numbers = _numbers(label)
        if shape == 'linear':
            expected = a[key]
            if not _close(expected, endpoints[index]): raise ValueError('线性进度起止位置错误')
            # 标记可以跨过端点，不要求使用独立圆点。
            if not _close([(ends[0][i]+ends[1][i])/2 for i in (0, 1)], expected): raise ValueError('线性进度标记未对准端点')
            if not _close(numbers, expected): raise ValueError('线性进度标签与实际端点不符')
        else:
            expected = _number(a[key+'_angle'])
            if not _angle_close(_angle(endpoints[index], center), expected): raise ValueError('进度角度与完整范围不符')
            if not any(_close(p, center) for p in ends): raise ValueError('进度射线未经过轴心')
            tip = next((p for p in ends if not _close(p, center)), center)
            if math.dist(tip, center) <= .001 or not _angle_close(_angle(tip, center), expected): raise ValueError('进度射线方向错误')
            if not numbers or not _angle_close(numbers[0], expected): raise ValueError('进度角度标签错误')
            if shape == 'circle' and key == 'end' and (360 not in numbers or direction not in label.get('characters', '')):
                raise ValueError('整圆标签须说明方向及360°')


def _required(s, nodes):
    sources = {p['instance_id'] for p in s.get('pointers', []) if p.get('role') != 'Hub' and p.get('instance_id')}
    paths = {g['path_id'] for g in s.get('stroke_geometry', []) if isinstance(g, dict) and g.get('role') == 'progress' and g.get('path_id')}
    time_masters = {id for f in s.get('resource_families', []) if f.get('kind') in ('time', 'time_hour', 'time_minute', 'second') for id in f.get('node_ids', [])}
    roots = {s.get('config', {}).get(k) for k in ('active_id', 'aod_id')} - {None}
    masters={a.get('master_id') for a in s.get('position_annotations',[]) if isinstance(a,dict)}
    for a in s.get('position_annotations',[]):
        if isinstance(a,dict) and isinstance(a.get('source_master_ids'),dict):masters.update(a['source_master_ids'].values())
    for n in nodes.values():
        if n.get('type') != 'INSTANCE':continue
        if not any(_under(n['id'], r, nodes) for r in roots): continue
        if n.get('main_component_id') in masters:sources.add(n['id'])
        if n.get('main_component_id') not in time_masters:continue
        t, _, _ = _screen_transform(n, nodes, roots)
        if t and not _close([t[0][0], t[0][1], t[1][0], t[1][1]], [1, 0, 0, 1]): sources.add(n['id'])
    return sources, paths


def validate_position_annotations(s, nodes):
    errors, unknown = [], []
    entries = s.get('position_annotations')
    explanation = '核对当前母件1×、原生参考线、切图边界尺寸、起止角与标签；明暗可读性须另验当前PNG'
    def result():
        return dict(id='snapshot.position_annotations', status='fail' if errors else 'unverified' if unknown else 'pass', explanation=explanation, locations=sorted(set(errors+unknown)))
    if entries is None:
        unknown.append('缺少position_annotations，旧证据未验证新版标注'); return result()
    if not isinstance(entries, list): errors.append('position_annotations须为数组'); return result()
    try: required_sources, required_paths = _required(s, nodes)
    except (KeyError, TypeError, ValueError): unknown.append('适用范围证据无效'); return result()
    covered_sources, covered_paths, seen = set(), set(), set()
    for a in entries:
        try:
            if not isinstance(a, dict) or a.get('kind') not in ('pointer', 'rotated_time', 'progress'): raise ValueError('标注类型无效')
            id = a['id']
            if not isinstance(id, str) or not id or id in seen: raise ValueError('标注ID缺失或重复')
            seen.add(id)
            frame = _get(nodes, a['frame_id']); master = _get(nodes, a['master_id']); image = _get(nodes, a['instance_id'])
            board = s.get('config', {}).get('asset_board_id')
            if frame['type'] != 'FRAME' or master['type'] != 'COMPONENT': raise ValueError('标注画框或母件类型错误')
            if not board: raise LookupError('缺少资源分区')
            if not _under(frame['id'], board, nodes): raise ValueError('标注不在资源分区')
            display_roots = {s.get('config', {}).get(k) for k in ('active_id', 'aod_id', 'main_id', 'preview_id')} - {None}
            if any(_under(frame['id'], r, nodes) for r in display_roots): raise ValueError('标注混入展示结构')
            canvas=s.get('config',{}).get('canvas',s.get('canvas'))
            if canvas is None:raise LookupError('缺少已确认的屏幕规格config.canvas')
            spec = screen_spec(canvas)
            if s.get('canvas') is not None and screen_spec(s['canvas'])!=spec:raise ValueError('顶层与生产config.canvas屏幕规格冲突')
            if not _close([frame['width'], frame['height'], frame.get('cornerRadius', frame.get('corner_radius', 0))], [spec['width'], spec['height'], spec['corner_radius']]):
                raise ValueError('标注屏形/尺寸/圆角与当前规格不符')
            _instance(image, master); center = a['center']
            if len(center) != 2: raise ValueError('标注轴心无效')
            if not all(0 <= _number(v) <= spec[k] for v, k in zip(center, ('width', 'height'))): raise ValueError('标注轴心超出屏幕')
            guides = frame.get('guides')
            if guides is None: raise LookupError('缺少原生guides')
            if not any(g.get('axis') == 'Y' and abs(_number(g.get('offset'))-center[1]) <= .001 for g in guides): raise ValueError('原生水平参考线未通过轴心')
            background = a['background']
            ancestor=frame
            while ancestor:
                if _number(ancestor.get('opacity',1))!=1:raise ValueError('标注底色及祖先须完全不透明')
                if ancestor['id']==board:break
                ancestor=nodes.get(ancestor.get('parent_id'))
            if background not in ('#000000', '#FFFFFF') or not _close(_rgb(frame.get('fills', [])), [0, 0, 0] if background == '#000000' else [1, 1, 1]):
                raise ValueError('实际标注底色与黑白底记录不符')
            sources = a['source_instance_ids']
            if not isinstance(sources, list) or not sources or len(set(sources)) != len(sources): raise ValueError('来源实例清单为空或重复')
            if not isinstance(a.get('source_master_ids',{}),dict):raise ValueError('配对母件映射须为对象')
            for source_id in sources:
                source = _get(nodes, source_id)
                source_master=_get(nodes,a.get('source_master_ids',{}).get(source_id,master['id']))
                if source_master['type']!='COMPONENT' or not _close([source_master['width'],source_master['height']],[master['width'],master['height']]):raise ValueError('共用图面来源的切图几何不一致')
                if a['kind']=='progress' and source_master['id']!=master['id']:raise ValueError('不同进度母件须分别核对完整路径')
                _instance(source, source_master)
                owner = next((r for r in display_roots if _under(source_id, r, nodes)), None)
                if not owner: raise ValueError('来源不是正式显示实例')
                _visible(source, nodes, owner)
                t = _transform(source, nodes, owner)
                if not _regular_transform(t, unit=True): raise ValueError('显示来源不是正交1×')
                if a['kind'] == 'progress' and not _close(sum(t, []), sum(_transform(image, nodes, frame['id']), [])):
                    raise ValueError('进度标注与正式来源定位不符')
                if a['kind'] != 'progress':
                    if not _close(_point(t, a['pivot']), center): raise ValueError('显示来源的实际轴心不符')
                    for p in s.get('pointers', []):
                        if p.get('instance_id') != source_id: continue
                        if not _close(p['pivot'], a['pivot']) or not _close(p['center'], center) or p['master_id'] != source_master['id']:
                            raise ValueError('标注与指针来源约定冲突')
                        old = p.get('annotation')
                        if old and any(old.get(k) != v for k, v in {'frame_id':frame['id'], 'instance_id':image['id'], 'line_node_id':a['measurement']['line_id']}.items()):
                            raise ValueError('新旧标注声明冲突')
            if a['kind'] == 'progress':
                _progress(a, nodes, frame, master, image); covered_paths.add(a['source_path_id'])
                helpers = [a[k] for k in ('start_line_id', 'end_line_id', 'start_label_id', 'end_label_id')]
            else:
                _measure(a, nodes, frame, master, image)
                helpers = [a['measurement'][k] for k in ('line_id', 'label_id')]
            axis = _get(nodes, a['axis_label_id'])
            if not _close(_numbers(axis), center) or 'px' not in axis.get('characters', ''): raise ValueError('屏幕轴心标签错误')
            if not _under(axis['id'], board, nodes) or _under(axis['id'], frame['id'], nodes): raise ValueError('轴心说明须在画框外资源分区内')
            covered_sources.update(sources)
            for helper in helpers:
                if not _under(helper, frame['id'], nodes): raise ValueError('尺寸线/标签不在标注画框内')
            members = [n for n in nodes.values() if _under(n['id'], frame['id'], nodes)] + [axis]
            for n in members:
                _visible(n, nodes, board)
                if n.get('export_settings') or any(e.get('node_id') == n['id'] for e in s.get('export_items', [])): raise ValueError(n['id']+':标注辅助不得正式导出')
                for ancestor in nodes.values():
                    if not _under(n['id'], ancestor['id'], nodes): continue
                    if ancestor.get('export_settings') or any(e.get('node_id') == ancestor['id'] for e in s.get('export_items', [])):
                        raise ValueError(n['id']+':标注被祖先资源带入正式导出')
            carried={ancestor['id'] for ancestor in nodes.values() if any(_under(m['id'],ancestor['id'],nodes) for m in members)}
            while True:
                previous=len(carried)
                for n in nodes.values():
                    if n.get('type')!='INSTANCE' or n.get('main_component_id') not in carried:continue
                    if any(_under(n['id'],r,nodes) for r in display_roots):raise ValueError('标注辅助或其祖先被正式显示引用')
                    carried.update(ancestor['id'] for ancestor in nodes.values() if _under(n['id'],ancestor['id'],nodes))
                if len(carried)==previous:break
        except LookupError as exc: unknown.append(str(exc))
        except KeyError as exc: unknown.append('标注证据字段缺失：'+str(exc))
        except (TypeError, ValueError, IndexError, AttributeError) as exc: errors.append(str(exc))
    unknown.extend('缺少适用来源标注：'+i for i in required_sources-covered_sources)
    unknown.extend('缺少进度完整范围标注：'+i for i in required_paths-covered_paths)
    for native in s.get('native_progress', []):
        if isinstance(native, dict) and not any(a.get('kind') == 'progress' and a.get('master_id') == native.get('node_id') for a in entries if isinstance(a, dict)):
            unknown.append('缺少原生进度范围标注：'+str(native.get('node_id')))
    if not entries and not errors and not unknown:
        return dict(id='snapshot.position_annotations', status='not_applicable', explanation='明确无适用标注；未见已登记指针、进度或实际旋转时间实例', locations=[])
    return result()


def _fingerprint(snapshot):
    import hashlib
    import json
    # 除标注图形外，母件、正式来源与几何声明同样绑定，不能回放旧评审。
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def annotation_review_template(snapshot):
    entries = snapshot.get('position_annotations') or []
    ids = sorted({a['frame_id'] for a in entries if isinstance(a, dict) and isinstance(a.get('frame_id'), str)})
    return [dict(frame_id=id, status='unverified', snapshot_fingerprint=_fingerprint(snapshot), evidence=None, export_receipt=None, notes='') for id in ids]


def validate_annotation_review(snapshot, review, base):
    import hashlib
    from pathlib import Path
    errors, unknown = [], []
    expected = {a['frame_id'] for a in snapshot.get('position_annotations', []) if isinstance(a, dict) and a.get('frame_id')}
    nodes = {n['id']:n for n in snapshot.get('nodes', [])}
    seen = set()
    if not isinstance(review, list): unknown.append('缺少当前标注PNG评审'); review = []
    for item in review:
        try:
            if not isinstance(item, dict): raise ValueError('标注评审须为对象')
            id = item['frame_id']
            if id in seen or id not in expected: raise ValueError('标注评审重复或不属于当前图面')
            seen.add(id)
            if item.get('snapshot_fingerprint') != _fingerprint(snapshot): raise ValueError(id+':标注评审已过期')
            if item.get('status') == 'unverified': unknown.append(id+':明暗与图面尚未评审'); continue
            if item.get('status') != 'pass': raise ValueError(id+':标注视觉未通过')
            if not isinstance(item.get('notes'), str) or not item['notes'].strip(): raise LookupError(id+':缺少实际图面评审说明')
            evidence = item.get('evidence')
            if not isinstance(evidence, dict): raise LookupError(id+':缺少独立标注1×PNG')
            relative = evidence.get('path')
            if not isinstance(relative, str) or not relative or Path(relative).is_absolute(): raise ValueError('标注PNG须为相对路径')
            path = (base/relative).resolve()
            if not path.is_relative_to(base.resolve()): raise ValueError('标注PNG越出评审目录')
            if hashlib.sha256(path.read_bytes()).hexdigest() != evidence.get('sha256'): raise ValueError(id+':标注PNG哈希不符')
            receipt = item.get('export_receipt')
            if not isinstance(receipt, dict) or not isinstance(receipt.get('annotation_capture'), dict):
                raise LookupError(id+':缺少同轮实际画框PNG导出回执')
            from run_evidence import check_annotation_capture, digest, resource_fingerprint
            if (receipt.get('node_id') != id or receipt.get('path') != relative
                    or receipt.get('sha256') != evidence.get('sha256') or receipt.get('purpose') != 'annotation'
                    or receipt.get('status') != 'verified' or not isinstance(receipt.get('run_id'), str) or not receipt['run_id']
                    or receipt.get('snapshot_sha256') != digest(snapshot)
                    or receipt.get('resource_sha256') != resource_fingerprint(snapshot, id)
                    or receipt.get('dimensions') != [nodes[id]['width'], nodes[id]['height']]):
                raise ValueError(id+':标注PNG回执不属于当前画框或快照')
            check_annotation_capture(snapshot, id, path, receipt.get('setting'), receipt.get('source_before'),
                                     receipt.get('source_after'), receipt['annotation_capture'])
            from PIL import Image
            with Image.open(path) as image:
                image.load()
                if image.format != 'PNG' or image.size != (nodes[id]['width'], nodes[id]['height']): raise ValueError(id+':标注PNG不是原生1×尺寸')
        except (OSError, LookupError, ImportError) as exc: unknown.append(str(exc))
        except (TypeError, ValueError) as exc: errors.append(str(exc))
    unknown.extend('缺少标注图面评审：'+id for id in expected-seen)
    return dict(id='workflow.position_annotations', status='fail' if errors else 'unverified' if unknown else 'pass',
                explanation='标注底色、元素边缘和读数的独立1×PNG逐图评审，绑定当前快照', locations=errors+unknown)
