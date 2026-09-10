"""本轮已确认屏幕规格及统一可见区域；不把分辨率写进资源名称。"""
import math

PROFILES = {
    (480, 480): ('circle', 240, (256, 256)),
    (466, 466): ('circle', 233, (256, 256)),
    (390, 390): ('circle', 195, (216, 216)),
    (390, 450): ('rounded_rect', 90, (240, 276)),
}


def screen_spec(canvas=None):
    canvas = {} if canvas is None else canvas
    if not isinstance(canvas, dict):
        raise ValueError('canvas 必须是对象')
    w, h = canvas.get('width', 480), canvas.get('height', 480)
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (w, h)) or (w, h) not in PROFILES:
        raise ValueError('支持画布480×480、466×466、390×390、390×450；其他规格须先核实规范')
    shape, radius, preview = PROFILES[w, h]
    if canvas.get('shape', shape) != shape or canvas.get('corner_radius', radius) != radius:
        raise ValueError('屏形或圆角与当前目标规范不一致（390×450采用已确认90px）')
    margin = canvas.get('safe_margin', 4)
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not math.isfinite(margin) or margin < 4 or margin >= min(w, h)/2:
        raise ValueError('安全边距须为有效数值且不小于4px、不超过屏幕可用范围')
    return dict(width=w, height=h, shape=shape, corner_radius=radius, preview=list(preview), safe_margin=margin)


def contains(x, y, width, height, shape='circle', corner_radius=0, margin=0):
    """点位于向内收缩margin后的区域；margin增加扫过半径可验证全周指针。"""
    if shape == 'circle':
        radius = min(width, height)/2-margin
        return radius >= 0 and math.hypot(x-width/2, y-height/2) <= radius + 1e-7
    if not (margin <= x <= width-margin and margin <= y <= height-margin):
        return False
    radius = max(0, corner_radius-margin) if shape == 'rounded_rect' else 0
    dx = max(margin+radius-x, 0, x-(width-margin-radius))
    dy = max(margin+radius-y, 0, y-(height-margin-radius))
    return math.hypot(dx, dy) <= radius + 1e-7


def fit_preview(spec):
    pw, ph = spec['preview']
    scale = min(pw/spec['width'], ph/spec['height'])
    w, h = spec['width']*scale, spec['height']*scale
    return dict(width=w, height=h, x=(pw-w)/2, y=(ph-h)/2, scale=scale)
