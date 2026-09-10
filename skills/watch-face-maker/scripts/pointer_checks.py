"""指针切图的轴心、旋转与圆屏扫过边界检查。角度从12点顺时针计。"""
import math
from screen_geometry import contains


def time_angles(hour, minute, second):
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ValueError('时间超出有效范围')
    return {'Hour': (hour % 12) * 30 + minute * .5 + second / 120,
            'Minute': minute * 6 + second * .1, 'Second': second * 6}


def completion_angle(value, target, start=180, sweep=360):
    if not math.isfinite(value) or not math.isfinite(target) or target <= 0:
        return None
    return start + max(0, min(1, value / target)) * sweep


def transform_at(pivot, center, angle):
    a = math.radians(angle)
    c, s = math.cos(a), math.sin(a)
    px, py = pivot
    cx, cy = center
    return [[c, -s, cx-c*px+s*py], [s, c, cy-s*px-c*py]]


def validate_pointers(snapshot, pointers, canvas=(480, 480), safe_margin=4, shape="circle", corner_radius=0):
    nodes = {n['id']: n for n in snapshot['nodes']}
    errors, measured = [], []
    order = {n['id']: i for i, n in enumerate(snapshot['nodes'])}
    layers = {}
    for p in pointers:
        n, master = nodes.get(p['instance_id']), nodes.get(p['master_id'])
        if not n or not master:
            errors.append(f"指针节点缺失：{p['role']}")
            continue
        if n.get('main_component_id') != master['id']:
            errors.append(f"指针母件引用错误：{n['id']}")
        w, h = master.get('width', 0), master.get('height', 0)
        px, py = p['pivot']
        if not (w > 0 and h > 0 and w % 2 == h % 2 == 0 and 0 <= px <= w and 0 <= py <= h):
            errors.append(f"指针尺寸或局部轴心无效：{master['id']}")
        transform = n.get('relative_transform')
        if not transform:
            errors.append(f"指针缺少实际变换：{n['id']}")
            continue
        actual_center = [transform[0][0]*px+transform[0][1]*py+transform[0][2],
                         transform[1][0]*px+transform[1][1]*py+transform[1][2]]
        delta = math.dist(actual_center, p['center'])
        expected = transform_at(p['pivot'], p['center'], p['angle'])
        if delta > .001 or any(abs(transform[r][c]-expected[r][c]) > .001 for r in range(2) for c in range(3)):
            errors.append(f"指针旋转或轴心漂移：{n['id']}")
        bounds = p.get('local_visible_bounds')
        sweep = None
        if bounds:
            x, y, bw, bh = bounds
            sweep = max(math.hypot(x+dx-px, y+dy-py) for dx in (0, bw) for dy in (0, bh))
            if not contains(*p["center"], *canvas, shape, corner_radius, safe_margin+sweep):
                errors.append(f"指针扫过屏幕安全区：{n['id']}")
        measured.append({'id': n['id'], 'pivot_error': delta, 'sweep_radius': sweep,
                         'sweep_status': ('fail' if bounds and not contains(*p['center'], *canvas, shape, corner_radius, safe_margin+sweep) else 'pass') if bounds else 'unverified'})
        if p['role'] in {'Hour', 'Minute', 'Second', 'Hub'}:
            layers.setdefault(n.get('parent_id'), {})[p['role']] = order[n['id']]
    for parent, values in layers.items():
        sequence = [values[k] for k in ('Hour', 'Minute', 'Second', 'Hub') if k in values]
        if sequence != sorted(sequence):
            errors.append(f'指针遮挡顺序错误：{parent}')
    return errors, measured


def validate_aod_pointers(snapshot, pointers):
    """核对母件与实际AOD实例描边，以及亮屏与熄屏的同轴同角关系。"""
    nodes = {n['id']: n for n in snapshot['nodes']}
    roles = {p['role']: p for p in pointers}
    errors, unknown = [], []
    def visible(node, stop=None):
        seen = set()
        while node:
            if node['id'] in seen or node.get('visible') is False or node.get('opacity', 1) <= 0:
                return False
            seen.add(node['id'])
            if node['id'] == stop:
                break
            node = nodes.get(node.get('parent_id'))
        return True

    def check_ink(root, displayed=False):
        descendants = {root['id']}
        while True:
            expanded = descendants | {n['id'] for n in nodes.values() if n.get('parent_id') in descendants}
            if expanded == descendants:
                break
            descendants = expanded
        all_vectors = [nodes[i] for i in descendants if nodes[i].get('type') == 'VECTOR']
        vectors = [n for n in all_vectors if visible(n, None if displayed else root['id'])]
        if not all_vectors:
            unknown.append(f'AOD指针缺少原始矢量：{root["id"]}')
        elif not vectors:
            errors.append(f'AOD指针没有可见描边：{root["id"]}')
        for n in vectors:
            if 'fills' not in n or 'strokes' not in n:
                unknown.append(f'AOD指针缺少原始填充/描边：{n["id"]}')
                continue
            fills = [p for p in n['fills'] if p.get('visible', True) and p.get('opacity', 1) > 0]
            strokes = [p for p in n['strokes'] if p.get('visible', True) and p.get('opacity', 1) > 0]
            weight = n.get('stroke_weight', n.get('strokeWeight', 0))
            if fills or len(strokes) != 1 or weight <= 0 or strokes[0].get('type') != 'SOLID' or strokes[0].get('opacity', 1) != 1 or any(round(strokes[0].get('color', {}).get(k, -1)*255) != 179 for k in 'rgb'):
                errors.append(f'AOD指针不是#B3B3B3无填充描边：{n["id"]}')

    for role in ('Hour', 'Minute'):
        active, dark = roles.get(role), roles.get(role+'Dark')
        if not active or not dark:
            errors.append(f'AOD缺少{role}配对')
            continue
        a, d = nodes.get(active['master_id']), nodes.get(dark['master_id'])
        if not a or not d:
            errors.append(f'AOD母件缺失：{role}')
            continue
        if any(active[k] != dark[k] for k in ('pivot', 'center', 'angle')) or any(a[k] != d[k] for k in ('width', 'height')):
            errors.append(f'AOD指针未同位同尺寸：{role}')
        check_ink(d)
        instance = nodes.get(dark['instance_id'])
        if not instance or instance.get('main_component_id') != d['id']:
            errors.append(f'AOD实例缺失或母件引用错误：{dark["instance_id"]}')
        elif not visible(instance):
            errors.append(f'AOD实例或祖先不可见：{instance["id"]}')
        elif snapshot.get('config', {}).get('skip_instance_children') or snapshot.get('traversal', {}).get('skip_instance_children'):
            unknown.append(f'AOD实例内部未采集：{instance["id"]}')
        else:
            check_ink(instance, displayed=True)
    return errors, unknown
