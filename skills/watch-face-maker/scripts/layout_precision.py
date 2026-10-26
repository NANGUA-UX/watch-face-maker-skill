"""只对可由原始几何重建的节点保留精度；不豁免其子树。"""
import math

from pointer_checks import transform_at

TOLERANCE = .001
IDENTITY = [[1, 0, 0], [0, 1, 0]]


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _matrix(value):
    return (isinstance(value, list) and len(value) == 2
            and all(isinstance(row, list) and len(row) == 3 and all(_number(v) for v in row) for row in value))


def _close(a, b):
    return all(abs(a[r][c] - b[r][c]) <= TOLERANCE for r in range(2) for c in range(3))


def _compose(a, b):
    return [[a[r][0] * b[0][c] + a[r][1] * b[1][c] + (a[r][2] if c == 2 else 0)
             for c in range(3)] for r in range(2)]


def _integer(value):
    return _number(value) and abs(value - round(value)) <= TOLERANCE


def _translation(transform):
    return all(abs(transform[r][c] - IDENTITY[r][c]) <= TOLERANCE for r in range(2) for c in range(2))


def _container_parent(node, nodes):
    """Figma relativeTransform以最近容器为准，GROUP/BOOLEAN不是容器。"""
    parent, seen = nodes.get(node.get('parent_id')), {node['id']}
    while parent and parent.get('type') in {'GROUP', 'BOOLEAN_OPERATION'}:
        if parent['id'] in seen:
            return None
        seen.add(parent['id'])
        parent = nodes.get(parent.get('parent_id'))
    return parent


def _pointer(pointer, nodes, roots):
    identity = pointer.get('instance_id')
    node, master = nodes.get(identity), nodes.get(pointer.get('master_id'))
    if not node or not master:
        return set(), 'unverified', '缺少指针实例或源母件'
    if node.get('type') != 'INSTANCE' or master.get('type') != 'COMPONENT' or node.get('main_component_id') != master['id']:
        return set(), 'fail', '指针母件引用不一致'
    pivot, center, angle = pointer.get('pivot'), pointer.get('center'), pointer.get('angle')
    if not all(isinstance(v, list) and len(v) == 2 and all(_integer(n) for n in v) for v in (pivot, center)) or not _number(angle):
        return set(), 'fail', '缺少整数局部/屏幕轴心或有效角度'
    width, height = master.get('width'), master.get('height')
    if not all(_number(v) and v > 0 and v % 2 == 0 for v in (width, height)) or not (0 <= pivot[0] <= width and 0 <= pivot[1] <= height):
        return set(), 'fail', '母件尺寸或局部轴心无效'
    chain, seen, current = [], set(), node
    while current and current['id'] not in roots:
        if current['id'] in seen or not _matrix(current.get('relative_transform')):
            return set(), 'unverified', '缺少完整、无环的原始祖先矩阵'
        seen.add(current['id'])
        transform = current['relative_transform']
        if not all(_number(current.get(key)) and abs(current[key] - transform[row][2]) <= TOLERANCE
                   for row, key in enumerate(('x', 'y'))):
            return set(), 'fail', 'X/Y与原始相对矩阵不一致'
        chain.append(current)
        current = _container_parent(current, nodes)
    if not current or not roots:
        return set(), 'unverified', '祖先链未到达实际Active/AOD根节点'
    transform = IDENTITY
    for ancestor in reversed(chain):
        transform = _compose(transform, ancestor['relative_transform'])
    if not _close(transform, transform_at(pivot, center, angle)):
        return set(), 'fail', '指针复合旋转或轴心漂移'
    rotating = [n for n in chain if not _translation(n['relative_transform'])]
    if len(rotating) > 1:
        return set(), 'unverified', '多级旋转分摊缺少可独立重建的外框轴心证据'
    # 普通祖先/实例的局部布局仍须整数，只有承担已验证旋转的节点可保留派生平移。
    if any(not all(_integer(n[key]) for key in ('x', 'y')) for n in chain if n not in rotating):
        return set(), 'fail', '指针链中普通局部布局不是整数'
    allowed = {identity}
    if rotating:
        allowed.add(rotating[0]['id'])
    return allowed, 'pass', '母件、整数轴心、实际祖先复合矩阵及角度一致，仅允许对应节点派生平移'


def _point(transform, x, y):
    return [transform[r][0] * x + transform[r][1] * y + transform[r][2] for r in range(2)]


def _bounds(points):
    low = [min(point[r] for point in points) for r in range(2)]
    return low + [max(point[r] for point in points) - low[r] for r in range(2)]


def _radial(entry, nodes):
    """窄支持：完整圆环与刚体径向直线。其他构造保留未验证，不近似放行。"""
    root, intersect, union, ring = [nodes.get(entry.get(key)) for key in
                                   ('coordinate_root_id', 'intersect_id', 'union_id', 'ring_id')]
    ids, angles = entry.get('line_ids'), entry.get('angles')
    if not all((root, intersect, union, ring)) or not isinstance(ids, list) or not ids or not isinstance(angles, list) or len(ids) != len(angles):
        return set(), 'unverified', '缺少径向构造角色、源线ID或对应角度'
    lines = [nodes.get(identity) for identity in ids]
    if not all(lines) or len(set(ids)) != len(ids) or not all(_number(a) for a in angles):
        return set(), 'unverified', '缺少真实源线或角度无效'
    children = lambda identity: {n['id'] for n in nodes.values() if n.get('parent_id') == identity}
    if (intersect.get('type') != 'BOOLEAN_OPERATION' or intersect.get('booleanOperation') != 'INTERSECT'
            or union.get('type') != 'BOOLEAN_OPERATION' or union.get('booleanOperation') != 'UNION'
            or ring.get('type') != 'ELLIPSE' or any(line.get('type') != 'LINE' for line in lines)
            or intersect.get('parent_id') != root['id']
            or children(intersect['id']) != {union['id'], ring['id']} or children(union['id']) != set(ids)):
        return set(), 'fail', '原始INTERSECT/UNION/圆环/源线结构与声明不一致'
    if not _matrix(root.get('absolute_transform')) or not _translation(root['absolute_transform']):
        return set(), 'unverified', '坐标根须有原始平移矩阵；缩放或旋转根尚未验证'
    group = [intersect, union, ring] + lines
    for node in group:
        relative, absolute = node.get('relative_transform'), node.get('absolute_transform')
        parent = _container_parent(node, nodes)
        if not _matrix(relative) or not _matrix(absolute) or not parent or not _matrix(parent.get('absolute_transform')):
            return set(), 'unverified', '缺少同次采集的原始相对/绝对矩阵'
        if not _close(_compose(parent['absolute_transform'], relative), absolute):
            return set(), 'unverified', '源相对/绝对矩阵不满足实际容器复合，不能推断其坐标空间'
        if not all(_number(node.get(key)) and abs(node[key] - relative[row][2]) <= TOLERANCE for row, key in enumerate(('x', 'y'))):
            return set(), 'fail', 'X/Y与原始相对矩阵不一致'
    center, mode = entry.get('center'), entry.get('line_mode')
    if not isinstance(center, list) or len(center) != 2 or not all(_integer(v) for v in center) or mode not in ('ray', 'diameter'):
        return set(), 'fail', '径向圆心须为整数，线模式须为ray或diameter'
    origin = [root['absolute_transform'][r][2] for r in range(2)]
    local = {}
    for node in group:
        local[node['id']] = [[*node['absolute_transform'][r][:2], node['absolute_transform'][r][2] - origin[r]] for r in range(2)]
    diameter, weight, arc = ring.get('width'), ring.get('stroke_weight'), ring.get('arcData')
    if not arc or not all(_number(arc.get(key)) for key in ('startingAngle', 'endingAngle', 'innerRadius')):
        return set(), 'unverified', '缺少源圆环原始arcData'
    if not all(_number(v) for v in (diameter, weight, ring.get('height'))) or any(key not in ring for key in ('fills', 'strokes', 'stroke_align')):
        return set(), 'unverified', '缺少源圆环原始尺寸或描边属性'
    if abs(abs(arc['endingAngle'] - arc['startingAngle']) - 2 * math.pi) > TOLERANCE or arc['innerRadius'] != 0:
        return set(), 'unverified', '非完整源圆环几何尚未覆盖'
    if (diameter <= 0 or not _integer(diameter / 2) or not 0 < weight < diameter / 2
            or ring.get('height') != diameter or ring.get('fills') != [] or not ring.get('strokes')
            or ring.get('stroke_align') != 'INSIDE'):
        return set(), 'fail', '源圆环不是完整同心向内描边圆'
    radius = diameter / 2
    if not _close(local[ring['id']], [[1, 0, center[0] - radius], [0, 1, center[1] - radius]]):
        return set(), 'fail', '源圆环与声明圆心不一致'
    union_points, intersection_points, weights = [], [], []
    for line, angle in zip(lines, angles):
        length, stroke = line.get('width'), line.get('stroke_weight')
        if not _number(length) or not _number(stroke):
            return set(), 'unverified', '缺少源线长度或实际描边宽度'
        if any(key not in line for key in ('height', 'strokeCap', 'stroke_align', 'fills', 'strokes')):
            return set(), 'unverified', '缺少源线原始形状或描边属性'
        if line['strokeCap'] != 'NONE' or line['stroke_align'] != 'CENTER':
            return set(), 'unverified', '其他源线端帽或描边对齐尚未覆盖'
        if (line['height'] != 0 or length <= 0 or not 0 < stroke < 2 * radius
                or line['fills'] != [] or not line['strokes']):
            return set(), 'fail', '源线须为无端帽、中心描边的原始直线'
        weights.append(stroke)
        low, high = (0, length) if mode == 'ray' else (-length / 2, length / 2)
        if high < radius or (mode == 'diameter' and -low < radius):
            return set(), 'fail', '径向源线未覆盖外圆环'
        expected = transform_at([0 if mode == 'ray' else length / 2, 0], center, angle)
        actual = local[line['id']]
        if not _close(actual, expected):
            return set(), 'fail', '径向源线角度或共同轴心漂移'
        # 用描边矩形与内外圆边界的交点重建结果，局部阵列也须覆盖内圆边界。
        centered = transform_at([0, 0], center, angle)
        half = stroke / 2
        union_points.extend(_point(centered, x, y) for x in (low, high) for y in (-half, half))
        inner = radius - weight
        if mode == 'ray' and half >= inner:
            intersection_points.extend(_point(centered, 0, y) for y in (-half, half))
        for bound_radius in (inner, radius):
            if half <= bound_radius:
                edge = math.sqrt(bound_radius ** 2 - half ** 2)
                intersection_points.extend(_point(centered, x, y) for x in ((edge,) if mode == 'ray' else (-edge, edge)) for y in (-half, half))
            for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2):
                x, y = bound_radius * math.cos(a), bound_radius * math.sin(a)
                lx = centered[0][0] * x + centered[1][0] * y
                ly = centered[0][1] * x + centered[1][1] * y
                if low - TOLERANCE <= lx <= high + TOLERANCE and abs(ly) <= half + TOLERANCE:
                    intersection_points.append([center[0] + x, center[1] + y])
    if max(weights) - min(weights) > TOLERANCE or len({round(angle % (180 if mode == 'diameter' else 360), 6) for angle in angles}) != len(angles):
        return set(), 'fail', '同组源线宽度不一致或角度重复'
    for boolean, points in ((union, union_points), (intersect, intersection_points)):
        actual = local[boolean['id']]
        if not _translation(actual) or not all(_number(boolean.get(key)) for key in ('width', 'height')):
            return set(), 'unverified', '缺少未缩放布尔结果的原始尺寸'
        measured = [actual[0][2], actual[1][2], boolean['width'], boolean['height']]
        if any(abs(a - b) > TOLERANCE for a, b in zip(measured, _bounds(points))):
            return set(), 'unverified', '源径向结构与轴心已验证；布尔层框不同于理想墨迹包围框，缺少实际fillGeometry边界证据'
    return {intersect['id'], union['id'], ring['id'], *ids}, 'pass', '原始同心径向线、圆环及两级布尔包围框均可重建，仅允许这些节点派生位置'


def precision_exemptions(snapshot, nodes_by_id):
    """返回(允许小数X/Y的精确节点ID集合, 诊断列表)。fail/unverified均不豁免。"""
    allowed, diagnostics = set(), []
    roots = {snapshot.get('config', {}).get(key) for key in ('active_id', 'aod_id')} - {None}
    for pointer in snapshot.get('pointers', []):
        exemptions, status, detail = _pointer(pointer, nodes_by_id, roots)
        allowed.update(exemptions)
        diagnostics.append(dict(kind='pointer', node_id=pointer.get('instance_id'), status=status, detail=detail))
    radial_entries = snapshot.get('radial_precision', [])
    declared = set()
    for entry in radial_entries:
        exemptions, status, detail = _radial(entry, nodes_by_id)
        allowed.update(exemptions)
        declared.add(entry.get('intersect_id'))
        diagnostics.append(dict(kind='radial', node_id=entry.get('intersect_id'), status=status, detail=detail))
    for node in nodes_by_id.values():
        if node['id'] not in declared and node.get('type') == 'BOOLEAN_OPERATION' and node.get('booleanOperation') == 'INTERSECT':
            children = [n for n in nodes_by_id.values() if n.get('parent_id') == node['id']]
            if any(n.get('booleanOperation') == 'UNION' for n in children) and any(n.get('type') == 'ELLIPSE' for n in children):
                diagnostics.append(dict(kind='radial', node_id=node['id'], status='unverified',
                                        detail='缺少同一次原始采集的radial_precision构造参数；不按类型或浮点位置豁免'))
    return allowed, diagnostics
