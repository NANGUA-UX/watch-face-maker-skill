"""背景有效画面覆盖与可复核边缘证据；不自动判断黑白色是否为留白。"""
import hashlib
import io
import math
from functools import lru_cache

from run_evidence import safe_path
from screen_geometry import contains, screen_spec


def _read(root, relative):
    from PIL import Image

    if not isinstance(relative, str) or not relative:
        raise LookupError('缺少背景源或有效区域文件路径')
    path = safe_path(root, relative)
    raw = path.read_bytes()
    image = Image.open(io.BytesIO(raw))
    image.load()
    return image, {'path':relative, 'sha256':hashlib.sha256(raw).hexdigest()}, raw


def _matrix(value):
    if (not isinstance(value, list) or len(value) != 2
            or any(not isinstance(row, list) or len(row) != 3 for row in value)
            or any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v)
                   for row in value for v in row)):
        raise ValueError('背景变换须为有限数值2×3矩阵')
    return value


def _multiply(a, b):
    return [[a[i][0]*b[0][j]+a[i][1]*b[1][j]+(a[i][2] if j==2 else 0)
             for j in range(3)] for i in range(2)]


def _inverse(m):
    a,b,c=m[0]; d,e,f=m[1]
    det=a*e-b*d
    if abs(det)<1e-12:
        raise ValueError('背景变换不可逆')
    return [[e/det,-b/det,(b*f-e*c)/det],[-d/det,a/det,(d*c-a*f)/det]]


@lru_cache(maxsize=16)
def _screen(width, height, shape, radius, margin=0):
    from PIL import Image

    return Image.frombytes('L', (width,height), bytes(
        255 if contains(x+.5,y+.5,width,height,shape,radius,margin) else 0
        for y in range(height) for x in range(width)))


def _source_region(snapshot, exports, nodes, bg, size):
    from PIL import Image, ImageChops

    value=snapshot.get('background_source')
    if not isinstance(value,dict):
        raise LookupError('缺少background_source，不能由容器尺寸推断有效画面')
    if value.get('kind')=='native':
        state=snapshot.get('pagination',{})
        if (state.get('complete') is not True or state.get('total_nodes')!=len(nodes)
                or state.get('collected_nodes')!=len(nodes)
                or snapshot.get('traversal',{}).get('skip_instance_children')):
            raise LookupError('原生背景须完整回读，不能用缺失图片子层的局部快照推断')
        visible={bg['id']}
        for _ in range(len(nodes)):
            children={n['id'] for n in nodes.values() if n.get('parent_id') in visible and n.get('visible',True)}
            if children<=visible:break
            visible.update(children)
        if any(p.get('type')=='IMAGE' and p.get('visible',True)
               for identity in visible for key in ('fills','strokes') for p in nodes[identity].get(key,[])):
            raise ValueError('原生背景声明与实际图片填充冲突')
        return {'kind':'native'},Image.new('L',size,255)
    source_node=nodes.get(value.get('source_node_id'))
    if source_node is None:
        raise LookupError('背景源图片节点未采集')
    if source_node.get('parent_id')!=bg['id']:
        raise LookupError('背景基底须为母件直属图片；多层补边先在Figma合成基底并保留源组')
    if source_node.get('visible',True) is not True:
        raise ValueError('登记的背景图片源被隐藏，不能证明实际画面覆盖')
    if source_node.get('type')!='RECTANGLE' or any(
            n.get('parent_id')==bg['id'] and n.get('visible',True) and n.get('isMask') for n in nodes.values()):
        raise LookupError('非矩形基底或兄弟遮罩须先在Figma合成，不能推断实际裁切')
    if source_node.get('cornerRadius',0) or source_node.get('isMask'):
        raise LookupError('图片源另有裁切或遮罩，须先在Figma合成统一裁切基底')
    fills=[p for p in source_node.get('fills',[]) if p.get('type')=='IMAGE' and p.get('visible',True)]
    if len(fills)!=1:
        raise LookupError('背景基底没有唯一图片填充')
    paint=fills[0]
    if paint.get('scaleMode') not in ('FILL','FIT') or paint.get('rotation',0)!=0:
        raise LookupError('当前背景覆盖支持无旋转FILL/FIT；其他裁切须先导出配准后的基底')
    if paint.get('opacity',1)!=1 or source_node.get('opacity',1)!=1:
        raise LookupError('半透明背景基底须先核对合成后的有效区域')
    source,record,raw=_read(exports,value.get('source_path'))
    if hashlib.sha1(raw).hexdigest()!=paint.get('imageHash'):
        raise ValueError('背景源字节与当前Figma imageHash不一致，须保存实际getBytesAsync字节')
    sw,sh=source.size
    captured=[i.get('size') for i in source_node.get('images',[]) if i.get('imageHash')==paint.get('imageHash')]
    if captured!=[{'width':sw,'height':sh}]:
        raise ValueError('背景源实际像素尺寸与Figma回读不一致')
    nw,nh=source_node.get('width'),source_node.get('height')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in (nw,nh)):
        raise ValueError('背景图片节点尺寸无效')
    bg_transform=_matrix(bg.get('absolute_transform'))
    absolute=_matrix(source_node.get('absolute_transform'))
    local=_multiply(_inverse(bg_transform),absolute)
    relative=_matrix(source_node.get('relative_transform'))
    if any(abs(local[i][j]-relative[i][j])>1e-5 for i in range(2) for j in range(3)):
        raise ValueError('背景图片局部与完整变换不一致')
    scale=(max if paint['scaleMode']=='FILL' else min)(nw/sw,nh/sh)
    transform=_multiply(local,[[scale,0,(nw-sw*scale)/2],[0,scale,(nh-sh*scale)/2]])
    declared=value.get('source_to_background')
    if declared is not None:
        declared=_matrix(declared)
        if any(abs(transform[i][j]-declared[i][j])>1e-5 for i in range(2) for j in range(3)):
            raise ValueError('登记背景变换与当前Figma实际变换不一致')
    alpha=source.convert('RGBA').getchannel('A')
    basis=value.get('coverage_basis')
    result={'kind':'image','source_node_id':source_node['id'],'image_hash':paint['imageHash'],
            'source':record,'coverage_basis':basis,'source_to_background':transform,
            'scale_mode':paint['scaleMode']}
    if basis=='mask':
        valid,mask_record,_=_read(exports,value.get('valid_region_path'))
        if valid.size!=source.size:
            raise ValueError('有效画面区域尺寸必须与源图一致')
        alpha=ImageChops.multiply(alpha,valid.convert('L'))
        result['valid_region']=mask_record
    elif basis=='alpha':
        if alpha.getextrema()==(255,255):
            raise LookupError('源图全不透明，alpha不能证明圆外填充已排除')
    elif basis!='rectangle':
        raise LookupError('须确认矩形满幅、真实alpha或经核对的有效区域，不能猜测RGB黑白底')
    inverse=_inverse(transform)
    region=alpha.transform(size,Image.Transform.AFFINE,tuple(inverse[0]+inverse[1]),
                           resample=Image.Resampling.BILINEAR)
    local_inverse=_inverse(local)
    node_region=Image.frombytes('L',size,bytes(
        255 if (0<=local_inverse[0][0]*(x+.5)+local_inverse[0][1]*(y+.5)+local_inverse[0][2]<nw
                and 0<=local_inverse[1][0]*(x+.5)+local_inverse[1][1]*(y+.5)+local_inverse[1][2]<nh) else 0
        for y in range(size[1]) for x in range(size[0])))
    region=ImageChops.multiply(region,node_region)
    return result,region


def _edge_images(image, spec, missing):
    from PIL import Image, ImageChops

    width,height=image.size
    full=_screen(width,height,spec['shape'],spec['corner_radius'])
    inner=_screen(width,height,spec['shape'],spec['corner_radius'],2)
    dark=Image.alpha_composite(Image.new('RGBA',image.size,'black'),image)
    light=Image.alpha_composite(Image.new('RGBA',image.size,'white'),image)
    outline=light.copy()
    outline.paste((30,220,100,255),mask=ImageChops.subtract(full,inner))
    outline.paste((255,30,80,255),mask=missing)
    images={'outline':outline,'dark':dark,'light':light}
    length=max(12,min(width,height)//6)
    names=('north','northeast','east','southeast','south','southwest','west','northwest')
    for k,name in enumerate(names):
        angle=k*math.pi/4
        dx,dy=math.sin(angle),-math.cos(angle)
        low,high=0,math.hypot(width,height)
        for _ in range(24):
            mid=(low+high)/2
            if contains(width/2+dx*mid,height/2+dy*mid,width,height,spec['shape'],spec['corner_radius']):low=mid
            else:high=mid
        x=max(0,min(width-length,round(width/2+dx*low-length/2)))
        y=max(0,min(height-length,round(height/2+dy*low-length/2)))
        images[name]=light.crop((x,y,x+length,y+length)).resize((length*4,length*4),Image.Resampling.NEAREST)
    return images


def local_background_animations(snapshot):
    entries = snapshot.get('animations', [])
    return [entry for entry in entries if isinstance(entry, dict) and entry.get('background_mode') == 'local'] if isinstance(entries, list) else []


def requires_background_review(snapshot):
    return bool(snapshot.get('config', {}).get('background_id') or local_background_animations(snapshot))


def local_background_layout(snapshot, spec, nodes):
    """从 Active 中的真实实例读取平移和层序，不使用资源板坐标。"""
    sequences = local_background_animations(snapshot)
    if len(sequences) != 1:
        raise ValueError('局部动态须唯一登记一组 Ani')
    sequence = sequences[0]
    ids = sequence.get('node_ids')
    if (not isinstance(ids, list) or len(ids) != 60 or any(not isinstance(i, str) for i in ids)
            or len(set(ids)) != 60):
        raise ValueError('局部动态须提供 60 个不同帧 ID')
    config = snapshot['config']
    active = nodes.get(config.get('active_id'))
    bg = nodes.get(config.get('background_id'))
    if not active or not bg or not isinstance(config.get('canvas'), dict):
        raise LookupError('缺少目标屏幕、Active 或 Bg 的回读')
    screen = (spec['width'], spec['height'])
    if (active.get('width'), active.get('height')) != screen:
        raise ValueError('局部背景所用 Active 与目标尺寸不符')
    if active.get('clips_content') is None or active.get('cornerRadius') is None:
        raise LookupError('缺少 Active 屏形裁切属性')
    if active['clips_content'] is not True or active['cornerRadius'] != spec['corner_radius']:
        raise ValueError('局部背景须由 Active 统一裁为目标屏形')
    if 'device_bg_canvas' in sequence and sequence['device_bg_canvas'] != 'screen':
        raise ValueError('明确要求整屏静态画布时须登记 device_bg_canvas=screen')

    if active.get('absolute_transform') is None:
        raise LookupError('缺少 Active 原始变换')
    to_screen = _inverse(_matrix(active['absolute_transform']))
    sources = {bg['id'], *ids}
    mask_parents = {node.get('parent_id') for node in nodes.values()
                    if node.get('isMask') and node.get('visible', True)}
    bg_layers, ani_layers = [], []
    for layer in nodes.values():
        source = layer.get('main_component_id')
        if layer.get('type') != 'INSTANCE' or source not in sources:
            continue
        chain = []
        current = layer
        seen = set()
        while current and current['id'] != active['id'] and current['id'] not in seen:
            seen.add(current['id'])
            chain.append(current)
            current = nodes.get(current.get('parent_id'))
        if (not current or current['id'] != active['id']
                or any(part.get('visible') is False or part.get('opacity') == 0 for part in chain)):
            continue
        if any(part.get('opacity', 1) != 1 or part.get('blendMode') not in (None, 'NORMAL', 'PASS_THROUGH')
               or part.get('isMask') or part.get('parent_id') in mask_parents for part in chain):
            raise LookupError('层间遮罩、透明度或混合效果须先在源母件内合成')
        for parent in chain[1:]:
            if parent.get('type') not in ('GROUP', 'FRAME') or any(parent.get(k) for k in ('clips_content', 'effects', 'fills', 'strokes')):
                raise LookupError('局部背景的布局祖先须无裁切、效果、填充或描边')
        if layer.get('overrides') != []:
            raise LookupError('Bg 与代表帧须回读为未覆写实例')
        master = nodes.get(source)
        if not master or layer.get('absolute_transform') is None:
            raise LookupError('缺少背景实例母件或实际摆位')
        size = master.get('width'), master.get('height')
        transform = _multiply(to_screen, _matrix(layer['absolute_transform']))
        x, y = transform[0][2], transform[1][2]
        linear = [row[:2] for row in transform]
        if (any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in size)
                or (layer.get('width'), layer.get('height')) != size or linear != [[1, 0], [0, 1]]
                or abs(x-round(x)) > .001 or abs(y-round(y)) > .001):
            raise ValueError('Bg 与代表帧须按原始尺寸和屏幕相对整数坐标摆放')
        x, y = round(x), round(y)
        if x < 0 or y < 0 or x+size[0] > screen[0] or y+size[1] > screen[1]:
            raise ValueError('局部背景的 Bg 或 Ani 裁框超出目标尺寸')
        target = bg_layers if source == bg['id'] else ani_layers
        target.append({'instance_id':layer['id'], 'node_id':source, 'offset':[x,y], 'size':list(size)})
    if len(bg_layers) > 1 or len(ani_layers) > 1:
        raise ValueError('Active 中须各有一个可见的 Bg 与 Ani 代表帧实例')
    if not bg_layers or not ani_layers:
        raise LookupError('缺少 Active 中实际摆放的 Bg 或 Ani 代表帧')
    bg_layer, ani_layer = bg_layers[0], ani_layers[0]
    order = list(nodes)
    if order.index(bg_layer['instance_id']) > order.index(ani_layer['instance_id']):
        raise ValueError('Active 中 Bg 须放在 Ani 下方')
    if sequence.get('device_bg_canvas') == 'screen' and (tuple(bg_layer['size']) != screen or bg_layer['offset'] != [0,0]):
        raise ValueError('目标设备要求整屏 Bg 画布且位于原点')
    return sequence, bg_layer, ani_layer


def _background_png(snapshot, exports, node):
    items = [item for item in snapshot.get('export_items', []) if item.get('node_id') == node['id']]
    if len(items) != 1:
        raise LookupError(f'{node["id"]} 须唯一登记背景 1× PNG 导出')
    item = items[0]
    settings = node.get('export_settings', [])
    setting = item.get('setting', settings[0] if len(settings) == 1 else None)
    if (not isinstance(setting, dict) or setting.get('format') != 'PNG'
            or setting.get('constraint') != {'type':'SCALE','value':1} or setting not in settings):
        raise ValueError('背景边缘验证必须读取当前 1× PNG 导出')
    image, export, _ = _read(exports, item.get('path'))
    if image.format != 'PNG':
        raise ValueError('背景实际文件不是 PNG')
    return image.convert('RGBA'), export, setting


def _record_source(record, source):
    record['source'] = source
    record['files'].extend(source[key] for key in ('source', 'valid_region') if key in source)


def _check_protected_region(value, exports, image, record, check):
    from PIL import ImageChops

    if not isinstance(value, dict) or not (value.get('original_background_path') or value.get('protected_region_path')):
        return
    try:
        before, before_record, _ = _read(exports, value.get('original_background_path'))
        protected, protected_record, _ = _read(exports, value.get('protected_region_path'))
        if before.size != image.size or protected.size != image.size or protected.convert('L').getbbox() is None:
            raise ValueError('补边前背景/主体保护区域须为目标尺寸且保护区域非空')
        record['files'].extend([before_record, protected_record])
        diff = ImageChops.difference(image, before.convert('RGBA'))
        changed = ImageChops.lighter(ImageChops.lighter(diff.getchannel('R'), diff.getchannel('G')),
                                     ImageChops.lighter(diff.getchannel('B'), diff.getchannel('A')))
        changed = ImageChops.multiply(changed.point(lambda a:255 if a > 1 else 0), protected.convert('L'))
        check('protected_region', 'fail' if changed.getbbox() else 'pass', '保护区RGBA逐像素差不超过1/255；不得通过整体缩放遮边')
    except LookupError as exc:
        check('protected_region', 'unverified', str(exc))
    except (OSError, ValueError, TypeError) as exc:
        check('protected_region', 'fail', str(exc))


def _analyze_local_background(snapshot, exports, nodes, spec, record, check):
    from PIL import Image, ImageChops

    sequence, bg, ani = local_background_layout(snapshot, spec, nodes)
    size = (spec['width'], spec['height'])
    source, export, setting = _background_png(snapshot, exports, nodes[bg['node_id']])
    if list(source.size) != bg['size']:
        raise ValueError('Bg 实际 PNG 尺寸与 Active 实例不一致')
    record['background'] = {**bg, **export, 'setting':setting}
    record['files'].append(export)
    record['animation'] = {**ani, 'frames':[]}
    check('container', 'pass', '从当前 Active 查得目标屏形、1× 实例、叠放顺序和整数坐标')
    base = Image.new('RGBA', size)
    base.paste(source, tuple(bg['offset']))
    offset = tuple(ani['offset'])
    paste_box = (*offset, offset[0]+ani['size'][0], offset[1]+ani['size'][1])
    opaque = Image.new('L', tuple(ani['size']), 255)
    missing = Image.new('L', size)
    screen = _screen(*size, spec['shape'], spec['corner_radius'])
    inner = _screen(*size, spec['shape'], spec['corner_radius'], 1)
    core = _screen(*size, spec['shape'], spec['corner_radius'], 2)
    holes = []
    for identity in sequence['node_ids']:
        node = nodes.get(identity)
        if not node:
            raise LookupError(f'缺少帧母件回读：{identity}')
        frame, export, setting = _background_png(snapshot, exports, node)
        if list(frame.size) != ani['size'] or [node.get('width'), node.get('height')] != ani['size']:
            raise ValueError(f'动画帧 {identity} 未使用共同裁框与 1× PNG 尺寸')
        record['animation']['frames'].append({'node_id':identity, **export, 'setting':setting})
        record['files'].append(export)
        composite = base.copy()
        composite.alpha_composite(frame, offset)
        alpha = composite.getchannel('A')
        gap = ImageChops.lighter(ImageChops.multiply(core, alpha.point(lambda a:255 if a < 250 else 0)),
                                 ImageChops.multiply(inner, alpha.point(lambda a:255 if a < 128 else 0)))
        if gap.getbbox():
            holes.append(identity)
            missing = ImageChops.lighter(missing, gap)
        opaque = ImageChops.darker(opaque, frame.getchannel('A'))
        if identity == ani['node_id']:
            representative = composite

    covered = Image.new('L', size)
    covered.paste(opaque.point(lambda a:255 if a == 255 else 0), paste_box)
    hidden = ImageChops.multiply(screen, ImageChops.multiply(covered, base.getchannel('A').point(lambda a:255 if a else 0)))
    check('output_alpha', 'fail' if holes else 'pass', 'Bg 与 Ani 合成存在透明缺口：' + '、'.join(holes) if holes else '全部 60 张合成帧覆盖目标屏形，外沿抗锯齿另作视觉复核')
    check('hidden_static_pixels', 'fail' if hidden.getbbox() else 'pass', '核对 Bg 中是否有被全部帧完全遮住的可去除像素；半透明和仅部分帧遮挡的区域保留所需底图')
    value = snapshot.get('background_source')
    if value is not None and not isinstance(value, dict):
        raise ValueError('背景来源记录结构无效')
    if value and any(k in value for k in ('kind', 'source_node_id', 'source_path', 'valid_region_path', 'source_to_background', 'coverage_basis')):
        try:
            source_info, region = _source_region(snapshot, exports, nodes, nodes[bg['node_id']], source.size)
            _record_source(record, source_info)
            positioned = Image.new('L', size)
            positioned.paste(region, tuple(bg['offset']))
            needed = ImageChops.subtract(inner, covered)
            gap = ImageChops.multiply(needed, positioned.point(lambda a:255 if a < 128 else 0))
            if gap.getbbox():
                missing = ImageChops.lighter(missing, gap)
            check('valid_region', 'fail' if gap.getbbox() else 'pass', '已登记的 Bg 有效画面须覆盖仍需要静态素材的屏内区域')
        except LookupError as exc:
            check('valid_region', 'unverified', str(exc))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            check('valid_region', 'fail', str(exc))
    record['missing_bounds'] = list(missing.getbbox()) if missing.getbbox() else None
    record['hidden_static_bounds'] = list(hidden.getbbox()) if hidden.getbbox() else None
    representative.putalpha(ImageChops.multiply(screen, representative.getchannel('A')))
    _check_protected_region(value, exports, representative, record, check)
    return _edge_images(representative, spec, ImageChops.lighter(missing, hidden))


def analyze_background(snapshot, exports):
    """返回可序列化事实与派生图片；所有人工边缘结论初始未验证。"""
    from PIL import Image, ImageChops

    record={'schema_version':1,'status':'unverified','notes':'','auto_checks':[], 'files':[]}
    images={}
    def check(name,status,detail):
        record['auto_checks'].append({'id':name,'status':status,'detail':detail})
    try:
        spec=screen_spec(snapshot.get('config',{}).get('canvas'))
        record['target']={k:spec[k] for k in ('width','height','shape','corner_radius')}
        size=(spec['width'],spec['height'])
        nodes={n['id']:n for n in snapshot.get('nodes',[])}
        if local_background_animations(snapshot):
            return record, _analyze_local_background(snapshot, exports, nodes, spec, record, check)
        bg=nodes.get(snapshot.get('config',{}).get('background_id'))
        if bg is None:raise LookupError('缺少背景母件回读')
        image,export,setting=_background_png(snapshot,exports,bg)
        record['background']={'node_id':bg['id'],**export,'setting':setting}
        record['files'].append(export)
        if image.size!=size:raise ValueError('实际背景PNG与目标屏幕尺寸不同')
        geometry=([bg.get('width'),bg.get('height')]==list(size)
                  and bg.get('clips_content') is True and bg.get('cornerRadius')==spec['corner_radius'])
        check('container','pass' if geometry else 'fail','按完整屏幕检查背景母件尺寸及统一裁切，不使用safe_margin')
        visible=_screen(*size,spec['shape'],spec['corner_radius'],1)
        core=_screen(*size,spec['shape'],spec['corner_radius'],2)
        alpha_holes=ImageChops.multiply(core,image.getchannel('A').point(lambda a:255 if a<250 else 0))
        check('output_alpha','fail' if alpha_holes.getbbox() else 'pass','内部透明缺口检查；最外2px抗锯齿另外审看')
        ring=ImageChops.subtract(_screen(*size,spec['shape'],spec['corner_radius']),
                                 _screen(*size,spec['shape'],spec['corner_radius'],4))
        black=white=0
        for pixel,marked in zip(image.getdata(),ring.getdata()):
            if marked and pixel[3]:
                black+=max(pixel[:3])<=16
                white+=min(pixel[:3])>=239
        record['edge_color_hints']={'near_black_pixels':black,'near_white_pixels':white,
                                    'classification':'requires_visual_context'}
        missing=alpha_holes
        try:
            source,region=_source_region(snapshot,exports,nodes,bg,size)
            _record_source(record, source)
            missing=ImageChops.lighter(missing,ImageChops.multiply(visible,region.point(lambda a:255 if a<128 else 0)))
            check('valid_region','fail' if missing.getbbox() else 'pass','有效画面经实际填充/节点变换后须覆盖屏幕；外沿1px只容许抗锯齿')
        except LookupError as exc:
            check('valid_region','unverified',str(exc))
        except (OSError,ValueError,TypeError,KeyError) as exc:
            check('valid_region','fail',str(exc))
        _check_protected_region(snapshot.get('background_source'), exports, image, record, check)
        record['missing_bounds']=list(missing.getbbox()) if missing.getbbox() else None
        images=_edge_images(image,spec,missing)
    except LookupError as exc:check('inputs','unverified',str(exc))
    except (OSError,ValueError,TypeError,KeyError) as exc:check('inputs','fail',str(exc))
    return record,images


def coverage_status(record):
    states=[c['status'] for c in record['auto_checks']]
    return 'fail' if 'fail' in states else ('unverified' if not states or 'unverified' in states else 'pass')


def validate_edge_review(snapshot, exports, review, review_root, source_hashes):
    """从当前原始输入重算事实和全部派生像素，不信任手写pass或旧摘要。"""
    errors=[]
    actual,images=analyze_background(snapshot,exports)
    if not isinstance(review,dict):return ['缺少background_edge_review；旧记录可读取但不能取得新的通过']
    if review.get('status')!='pass' or not isinstance(review.get('notes'),str) or not review['notes'].strip():
        errors.append('背景边缘视觉仍未验证或没有实际复核说明')
    if coverage_status(actual)!='pass':errors.append('背景有效画面覆盖未通过：'+str(actual['auto_checks']))
    expected={k:v for k,v in review.items() if k not in ('status','notes','artifacts')}
    current={k:v for k,v in actual.items() if k not in ('status','notes')}
    if expected!=current:errors.append('背景源、有效区域、变换或实际PNG证据已变化')
    source=actual.get('source',{}).get('source')
    if source and source['sha256'] not in source_hashes:errors.append('实际背景素材SHA-256未登记在清单来源中')
    artifacts=review.get('artifacts')
    if not isinstance(artifacts,dict) or set(artifacts)!=set(images):
        errors.append('背景边缘证据须包含完整外圈、八方向及深浅底11图')
    else:
        for key,expected_image in images.items():
            try:
                item=artifacts[key]
                image,record,_=_read(review_root,item.get('path'))
                if record!=item or image.size!=expected_image.size or image.convert('RGBA').tobytes()!=expected_image.tobytes():
                    raise ValueError('哈希、尺寸或派生像素不一致')
            except (OSError,ValueError,TypeError,AttributeError,LookupError) as exc:
                errors.append('背景边缘'+key+'证据无效：'+str(exc))
    return errors
