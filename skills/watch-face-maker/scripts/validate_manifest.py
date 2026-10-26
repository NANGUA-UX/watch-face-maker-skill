#!/usr/bin/env python3
"""检查表盘交付清单中的关键数量、语言、AOD和导出约束。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from delivery_guards import validate_delivery_guards
import re
import sys
from pathlib import Path
from naming import format_prefix, validate_legacy_names, validate_names
from run_evidence import safe_path, stable_snapshot, validate_run
from pointer_checks import validate_pointers, validate_aod_pointers
from screen_geometry import screen_spec, contains, fit_preview
from layout_precision import precision_exemptions
from workflow_guards import file_sha256, verified_gate_failures, load_contract
from visual_artifacts import comparison_images


STANDARD_STATE_COUNTS = {"weather": 48, "battery": 11, "bluetooth": 2}
STATE_CAPABILITY_TO_FAMILY = {
    "weather": "weather",
    "battery": "battery",
    "bluetooth": "bluetooth",
    "pace_progress": "pace_progress",
    "calories_progress": "calories_progress",
    "distance_progress": "distance_progress",
    "steps_progress": "steps_progress",
}
NATIVE_PROGRESS_CAPABILITIES = {
    "battery", "pace_progress", "calories_progress", "distance_progress", "steps_progress",
}


def is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def valid_image_hash(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value))


def validate_manifest(data: dict) -> tuple[list[str], dict]:
    errors: list[str] = []

    naming_data = data.get("naming")
    naming_mode = naming_data.get("mode") if isinstance(naming_data, dict) else None
    prefix = data.get("prefix")
    if not isinstance(prefix, str) or not prefix.strip():
        errors.append("prefix 必须是非空字符串")
    elif naming_mode == "legacy":
        if not re.fullmatch(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+)*", prefix):
            errors.append("legacy prefix 仅支持英文、数字及单个内部空格")
    elif not re.fullmatch(r"[A-Z][A-Za-z0-9]*", prefix):
        errors.append("新项目 prefix 须由表盘英文名去掉空格后连写，不能含空格或下划线")
    if naming_mode in {"full", "short"} or "name_zh" in data or "name_en" in data:
        if naming_mode == "legacy":
            errors.extend(validate_legacy_names(data.get("name_zh"), data.get("name_en")))
        else:
            errors.extend(validate_names(
                data.get("name_zh"), data.get("name_en"), data.get("product_code"),
                require_code=True,
            ))
        expected_prefix = data.get("name_en") if naming_mode == "legacy" else (
            format_prefix(data.get("name_en")) if isinstance(data.get("name_en"), str) else None
        )
        if prefix != expected_prefix:
            errors.append("prefix 必须等于 name_en 去掉空格后的连写值")

    source_strategy = data.get("source_strategy")
    if source_strategy is not None:
        if not isinstance(source_strategy, dict):
            errors.append("source_strategy 必须是对象")
        else:
            mode = source_strategy.get("mode")
            if mode not in {"new", "modify", "reuse_allowed", "reuse_forbidden"}:
                errors.append("source_strategy.mode 必须是 new、modify、reuse_allowed 或 reuse_forbidden")
            for field in ("reference_image_hashes", "adopted_image_hashes", "reused_image_hashes"):
                values = source_strategy.get(field)
                if not isinstance(values, list) or any(not valid_image_hash(value) for value in values):
                    errors.append(f"source_strategy.{field} 必须是40或64位十六进制哈希数组")
                elif len(set(values)) != len(values):
                    errors.append(f"source_strategy.{field} 不能包含重复项")
            reused = source_strategy.get("reused_image_hashes")
            if mode in {"new", "reuse_forbidden"} and isinstance(reused, list) and reused:
                errors.append(f"source_strategy.mode={mode} 时不能复用旧图片哈希")

    languages = data.get("languages", ["EN"])
    if not isinstance(languages, list) or not languages:
        errors.append("languages 必须是非空数组；未配置时默认使用 EN")
        languages = []
    elif any(not isinstance(item, str) or not item.strip() for item in languages):
        errors.append("languages 中的每一项必须是非空字符串")
    elif len(set(languages)) != len(languages):
        errors.append("languages 不能包含重复项")
    elif any(not re.fullmatch(r"[A-Z][A-Z0-9-]*", item) for item in languages):
        errors.append("languages 必须使用固定大写语言代码，例如 EN、SC、TC、DE、ES")

    themes = data.get("themes", ["Default"])
    if not isinstance(themes, list) or not themes:
        errors.append("themes 必须是非空数组")
        themes = []
    elif any(not isinstance(item, str) or not item.strip() for item in themes):
        errors.append("themes 中的每一项必须是非空字符串")
        themes = []
    elif len(set(themes)) != len(themes):
        errors.append("themes 不能包含重复项")

    features = data.get("features", {})
    if not isinstance(features, dict):
        errors.append("features 必须是对象")
        features = {}
    for field in ("color_variants", "special_date"):
        if field in features and not isinstance(features[field], bool):
            errors.append(f"features.{field} 必须是布尔值")
    color_variants = features.get("color_variants", False) is True
    special_date_enabled = features.get("special_date", False) is True
    if not color_variants and len(themes) > 1:
        errors.append("未启用 color_variants 时只能配置一个主题")

    special_date = data.get(
        "special_date_components", {"expected": 0, "actual": 0}
    )
    if not isinstance(special_date, dict):
        errors.append("special_date_components 必须包含 expected 和 actual")
    else:
        expected = special_date.get("expected", 0)
        actual = special_date.get("actual", 0)
        if not is_integer(expected) or expected < 0:
            errors.append("special_date_components.expected 必须是非负整数")
        if not is_integer(actual) or actual < 0:
            errors.append("special_date_components.actual 必须是非负整数")
        if special_date_enabled:
            if is_integer(expected) and expected < 1:
                errors.append("启用特殊日期时 expected 必须是正整数")
            elif is_integer(expected) and is_integer(actual) and actual != expected:
                errors.append(
                    f"特殊日期组件数量不匹配：期望 {expected}，实际 {actual}"
                )
        elif (
            is_integer(expected)
            and is_integer(actual)
            and (expected != 0 or actual != 0)
        ):
            errors.append("未启用 special_date 时不能生成特殊日期组件")

    canvas = data.get("canvas", {})
    if not isinstance(canvas, dict):
        errors.append("canvas 必须是对象")
        canvas = {}
    try:
        if "width" not in canvas or "height" not in canvas:
            raise ValueError("canvas 必须提供宽高")
        spec = screen_spec(canvas)
    except ValueError as exc:
        errors.append(str(exc))
        spec = screen_spec()
    safe_margin = canvas.get("safe_margin", 0)
    if not is_finite_number(safe_margin) or safe_margin < 4:
        errors.append("安全边距不能小于 4 px")

    aod = data.get("aod", {})
    if not isinstance(aod, dict):
        errors.append("aod 必须是对象")
        aod = {}
    if aod.get("required") is not True:
        errors.append("当前规范要求制作 AOD")
    ratio = aod.get("lit_ratio")
    if not is_finite_number(ratio):
        errors.append("AOD 必须提供有限数值的实际亮屏比例 lit_ratio")
    elif ratio > 0.10:
        errors.append(f"AOD 亮屏比例 {ratio:.4%} 超过 10% 上限")
    elif ratio < 0:
        errors.append("AOD 亮屏比例不能小于 0")
    if (
        not is_finite_number(aod.get("export_scale"))
        or aod.get("export_scale") != 1
    ):
        errors.append("aod.export_scale 必须为 1×")

    exports = data.get("exports", {})
    if not isinstance(exports, dict):
        errors.append("exports 必须是对象")
        exports = {}
    if (
        not is_finite_number(exports.get("active_scale"))
        or exports.get("active_scale") != 5
    ):
        errors.append("exports.active_scale 必须为 5×")
    if (
        not is_finite_number(exports.get("asset_scale"))
        or exports.get("asset_scale") != 1
    ):
        errors.append("exports.asset_scale 必须为 1×")
    if exports.get("preview") != spec["preview"]:
        errors.append(f"缩略图尺寸必须为 {spec['preview'][0]}×{spec['preview'][1]}")

    states = data.get("states", {})
    if not isinstance(states, dict):
        errors.append("states 必须是对象；没有状态图资源时使用空对象")
        states = {}
    else:
        for name, counts in states.items():
            if not isinstance(counts, dict):
                errors.append(f"{name} 状态必须包含 expected 和 actual")
                continue
            expected, actual = counts.get("expected"), counts.get("actual")
            expected_valid = is_integer(expected) and expected >= 1
            actual_valid = is_integer(actual) and actual >= 0
            if not expected_valid:
                errors.append(f"{name} expected 必须是正整数")
            if not actual_valid:
                errors.append(f"{name} actual 必须是非负整数")
            if expected_valid and actual_valid and actual != expected:
                errors.append(f"{name} 状态数量不匹配：期望 {expected}，实际 {actual}")
            standard_count = STANDARD_STATE_COUNTS.get(name)
            if (
                expected_valid
                and standard_count is not None
                and expected != standard_count
            ):
                errors.append(
                    f"{name} 规范状态数量应为 {standard_count}，清单填写 {expected}"
                )

    components = data.get("components", {})
    if not isinstance(components, dict):
        errors.append("components 必须是对象")
        components = {}
    if not is_integer(components.get("total")) or components.get("total", 0) < 1:
        errors.append("组件总数必须是正整数")
    for field, label in (
        ("duplicates", "存在重复组件名"),
        ("missing_png_export", "存在缺少 PNG 导出的组件"),
    ):
        value = components.get(field, [])
        if not isinstance(value, list):
            errors.append(f"components.{field} 必须是数组")
        elif value:
            errors.append(f"{label}：{', '.join(map(str, value))}")

    icon_assets = data.get("icon_assets")
    if not isinstance(icon_assets, dict):
        errors.append("icon_assets 必须是对象，并记录组件库实例、自绘图标和纯色色块占位检查")
    else:
        for field in ("library_instances", "fallback_blocks"):
            value = icon_assets.get(field)
            if not isinstance(value, list) or any(
                not isinstance(item, str) or not item.strip() for item in value
            ):
                errors.append(f"icon_assets.{field} 必须是字符串数组")
            elif len(set(value)) != len(value):
                errors.append(f"icon_assets.{field} 不能包含重复项")
        for field, label in (
            ("library_available_but_not_used", "组件库已有图标但未使用组件实例"),
            ("custom_drawn", "存在自行绘制或描摹的功能图标"),
            ("fallback_blocks_without_library_search", "纯色色块占位缺少组件库检索记录"),
        ):
            value = icon_assets.get(field)
            if not isinstance(value, list):
                errors.append(f"icon_assets.{field} 必须是数组")
            elif value:
                errors.append(f"{label}：{', '.join(map(str, value))}")

    naming = data.get("naming")
    if not isinstance(naming, dict):
        errors.append("naming 必须记录命名模式、规范状态和路径检查")
    else:
        mode = naming.get("mode")
        schema_status = naming.get("schema_status")
        if mode not in {"full", "short", "legacy"}:
            errors.append("naming.mode 必须是 full、short 或 legacy")
        if schema_status not in {"confirmed", "legacy_locked"}:
            errors.append("naming.schema_status 必须是 confirmed 或 legacy_locked")
        if mode == "legacy" and schema_status != "legacy_locked":
            errors.append("legacy 命名模式必须标记为 legacy_locked")
        if mode in {"full", "short"} and schema_status != "confirmed":
            errors.append("新命名模式必须在字段映射确认后标记为 confirmed")
        if mode in {"full", "short"} and naming.get("spec_source") != "figma-v3.0:1910:298":
            errors.append("新命名模式必须记录 naming.spec_source=figma-v3.0:1910:298")
        for field, label in (
            ("mixed_paths", "同一项目不能混用完整字段与缩写路径"),
            ("non_pascal_case_fields", "新开发字段必须使用 PascalCase"),
            ("duplicate_abbreviations", "缩写必须通过全量唯一性检查"),
            ("unresolved_terms", "命名冲突尚未确认，不能开始正式切图命名"),
            ("sequence_gaps", "序列资源必须从 0 连续且无缺号"),
            ("static_resources_with_sequence", "Icon、Pointer 等单图不能追加序号"),
        ):
            value = naming.get(field)
            if not isinstance(value, list):
                errors.append(f"naming.{field} 必须是数组")
            elif value:
                errors.append(f"{label}：{', '.join(map(str, value))}")

    capabilities = data.get("capabilities")
    if not isinstance(capabilities, dict):
        errors.append("capabilities 必须区分本次请求、实际实现和排除项")
    else:
        requested = capabilities.get("requested")
        implemented = capabilities.get("implemented")
        if not isinstance(requested, list) or any(
            not isinstance(item, str) or not item.strip() for item in requested
        ):
            errors.append("capabilities.requested 必须是字符串数组")
            requested = []
        elif len(set(requested)) != len(requested):
            errors.append("capabilities.requested 不能包含重复项")
        if not isinstance(implemented, list) or any(
            not isinstance(item, str) or not item.strip() for item in implemented
        ):
            errors.append("capabilities.implemented 必须是字符串数组")
            implemented = []
        elif len(set(implemented)) != len(implemented):
            errors.append("capabilities.implemented 不能包含重复项")
        if set(implemented) - set(requested):
            unexpected = sorted(set(implemented) - set(requested))
            errors.append("实际实现了未请求能力：" + ", ".join(unexpected))
        if set(requested) - set(implemented):
            missing = sorted(set(requested) - set(implemented))
            errors.append("缺少已请求能力：" + ", ".join(missing))
        for field, label in (
            ("unrequested_generated", "生成了本次未请求的能力资源"),
            ("excluded_generated", "生成了用户明确排除的配置项"),
            ("outside_boundary_requested", "本次请求包含当前开发能力边界外的效果"),
            ("outside_boundary_generated", "实际生成了当前开发能力边界外的效果"),
        ):
            value = capabilities.get(field)
            if not isinstance(value, list):
                errors.append(f"capabilities.{field} 必须是数组")
            elif value:
                errors.append(f"{label}：{', '.join(map(str, value))}")

        capability_modes = data.get("capability_modes", {})
        if not isinstance(capability_modes, dict):
            errors.append("capability_modes 必须是对象")
            capability_modes = {}
        presentations = data.get("capability_presentations", {})
        if not isinstance(presentations, dict):
            errors.append("capability_presentations 必须是对象")
            presentations = {}
        for capability, presentation in presentations.items():
            if capability != "battery" or capability not in requested or presentation not in ("numeric", "progress"):
                errors.append(f"无效的能力显示声明：{capability}")
            if presentation == "numeric" and (capability in capability_modes or capability in states):
                errors.append(f"{capability} 仅数字显示，不能同时声明图形进度或状态图")
        for capability, mode in capability_modes.items():
            if capability not in requested:
                errors.append(f"capability_modes 声明了未请求能力：{capability}")
            if mode not in {"state_images", "native_continuous"}:
                errors.append(f"{capability} 的实现模式必须是 state_images 或 native_continuous")
            elif mode == "native_continuous" and capability not in NATIVE_PROGRESS_CAPABILITIES:
                errors.append(f"{capability} 不支持 native_continuous 实现模式")
        for capability, state_family in STATE_CAPABILITY_TO_FAMILY.items():
            if capability not in requested:
                continue
            if presentations.get(capability) == "numeric":
                continue
            mode = capability_modes.get(capability, "state_images")
            if mode == "state_images" and state_family not in states:
                errors.append(f"已请求 {capability}，但缺少 {state_family} 状态族")
            elif mode == "native_continuous" and state_family in states:
                errors.append(f"{capability} 已使用 native_continuous，不能同时生成 {state_family} 状态族")

    composition = data.get("composition")
    if not isinstance(composition, dict):
        errors.append("composition 必须记录主表盘、AOD和缩略图的实例组装检查")
    else:
        for field, label in (
            ("main_non_instance_visible_leaf_count", "主表盘"),
            ("aod_non_instance_visible_leaf_count", "AOD"),
            ("preview_non_instance_visible_leaf_count", "缩略图"),
        ):
            value = composition.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"composition.{field} 必须是非负整数")
            elif value:
                errors.append(f"{label}存在 {value} 个未由切图组件实例承载的可见叶子元素")

    text_assets = data.get("text_assets")
    if not isinstance(text_assets, dict):
        errors.append("text_assets 必须记录文字切图的尺寸和边界检查")
    else:
        total = text_assets.get("total")
        if not isinstance(total, int) or isinstance(total, bool) or total < 1:
            errors.append("文字切图总数必须是正整数")
        for field, label in (
            ("non_multiple_of_four", "文字切图宽高必须是 4 的倍数"),
            ("mixed_height_families", "同一文字资源族必须统一高度"),
            ("non_uniform_data_date_digit_sizes", "数据类和日期类的 0–9 数字必须在同族统一宽高"),
            ("non_minimal_uniform_digit_width", "数据类和日期类数字必须使用最小安全公共宽度"),
            ("unsafe_bounds", "文字切图存在切边风险"),
            ("non_snug_flexible_width", "时间数字、标点、符号和单位的边框必须按各自内容收紧"),
        ):
            value = text_assets.get(field)
            if not isinstance(value, list):
                errors.append(f"text_assets.{field} 必须是数组")
            elif value:
                errors.append(f"{label}：{', '.join(map(str, value))}")

    return errors, {"languages": languages, "themes": themes}


def check_item(check_id: str, status: str, explanation: str, locations=None) -> dict:
    return {
        "id": check_id,
        "status": status,
        "explanation": explanation,
        "locations": locations or [],
    }


def descendants(nodes_by_id: dict, root_id: str) -> set[str]:
    found = {root_id}
    changed = True
    while changed:
        changed = False
        for node in nodes_by_id.values():
            if node.get("parent_id") in found and node.get("id") not in found:
                found.add(node["id"])
                changed = True
    return found


def descendants_up(node: dict, nodes_by_id: dict, stop_id: str) -> list[str]:
    ancestors = []
    parent_id = node.get("parent_id")
    while parent_id in nodes_by_id and parent_id != stop_id:
        ancestors.append(parent_id)
        parent_id = nodes_by_id[parent_id].get("parent_id")
    return ancestors


def png_export(node: dict) -> bool:
    return any(setting.get("format") == "PNG" for setting in node.get("export_settings", []))


def node_origin(node: dict) -> tuple[float, float] | None:
    transform = node.get("absolute_transform")
    if (
        not isinstance(transform, list) or len(transform) != 2
        or any(not isinstance(row, list) or len(row) != 3 for row in transform)
    ):
        return None
    x, y = transform[0][2], transform[1][2]
    return (x, y) if is_finite_number(x) and is_finite_number(y) else None


def has_translation_only_transform(node: dict) -> bool:
    transform = node.get("absolute_transform")
    return (
        isinstance(transform, list) and len(transform) == 2
        and transform[0][:2] == [1, 0] and transform[1][:2] == [0, 1]
    )


def effectively_visible(node: dict, nodes_by_id: dict, stop_id: str) -> bool:
    current = node
    while current:
        if current.get("visible") is False or current.get("opacity", 1) == 0:
            return False
        if current.get("id") == stop_id:
            return True
        current = nodes_by_id.get(current.get("parent_id"))
    return False


def rounded_multiple_of_four(value: float) -> int:
    return int(math.ceil(value / 4) * 4)


def has_digital_time(snapshot: dict) -> bool:
    style = snapshot.get("aod_style", {})
    roles = snapshot.get("roles", {})
    return (
        any(isinstance(style.get(key), str) and style[key] for key in ("active_node_name_prefix", "node_name_prefix"))
        or any(isinstance(roles, dict) and roles.get(key) for key in ("time_main_id", "time_aod_id"))
        or any(family.get("kind") == "time" for family in snapshot.get("resource_families", []) if isinstance(family, dict))
        or any(
            node.get("type") == "COMPONENT"
            and ("/Num/Time/" in node.get("name", "") or "/Num/TimeDark/" in node.get("name", ""))
            for node in snapshot.get("nodes", []) if isinstance(node, dict)
        )
    )


def text_source(node: dict, nodes_by_id: dict) -> dict | None:
    """按原生TEXT识别文字母件，不依赖数字、星期或单位等名称。"""
    if node.get("type") != "TEXT":
        return None
    parent = nodes_by_id.get(node.get("parent_id"))
    while parent:
        if parent.get("type") == "INSTANCE":
            return None
        if parent.get("type") == "COMPONENT":
            # 背景内的刻度文字属于整幅图案，不是独立文字切图。
            contents = descendants(nodes_by_id, parent["id"]) - {parent["id"]}
            return parent if all(nodes_by_id[i].get("type") in {"TEXT", "FRAME", "GROUP"} for i in contents) else None
        parent = nodes_by_id.get(parent.get("parent_id"))
    return None


def digit_source(node: dict, nodes_by_id: dict) -> dict | None:
    source = text_source(node, nodes_by_id)
    return source if source and "/Num/" in source.get("name", "") and re.fullmatch(r"[0-9]", str(node.get("characters", ""))) else None


def validate_digit_geometry(nodes_by_id: dict, all_text: bool = False, baseline_families=None) -> dict:
    failures, unknown, count = [], [], 0
    baseline_ids = {identity: family.get('name') for family in baseline_families or []
                    if family.get('baseline_policy') == 'shared_font_baseline' for identity in family.get('node_ids', [])}
    baselines = {}
    for node in nodes_by_id.values():
        source = text_source(node, nodes_by_id) if all_text else digit_source(node, nodes_by_id)
        if source is None:
            continue
        count += 1
        if all_text:
            alignment = node.get("text_align_horizontal")
            if alignment is None:
                unknown.append(node["id"])
            elif alignment != "CENTER":
                failures.append(node["id"])
        if not all(is_finite_number(node.get(k)) for k in ("width", "height", "x", "y")):
            unknown.append(node["id"])
            continue
        if any(node[k] <= 0 or abs(node[k] / 2 - round(node[k] / 2)) > .00001 for k in ("width", "height")) or any(abs(node[k] * 10 - round(node[k] * 10)) > .00001 for k in ("x", "y")):
            failures.append(node["id"])
        bounds = node.get("unclipped_glyph_bounds") or node.get("glyph_bounds") or node.get("render_bounds")
        origin = node_origin(source)
        if origin is None or not has_translation_only_transform(source) or not isinstance(bounds, dict) or not all(is_finite_number(bounds.get(k)) for k in ("x", "y", "width", "height")) or not all(is_finite_number(source.get(k)) and source[k] > 0 for k in ("width", "height")):
            unknown.append(node["id"])
            continue
        margins = (bounds["x"] - origin[0], bounds["y"] - origin[1], origin[0] + source["width"] - bounds["x"] - bounds["width"], origin[1] + source["height"] - bounds["y"] - bounds["height"])
        baseline_symbol = all_text and bool(re.fullmatch(r"[.:,;+-]", str(node.get("characters", ""))))
        shared_baseline = source['id'] in baseline_ids
        if shared_baseline:
            text_origin = node_origin(node)
            if text_origin is None or not has_translation_only_transform(node):
                unknown.append(node['id'])
                continue
            key = (baseline_ids[source['id']], json.dumps([node.get('font_name'), node.get('font_size')], sort_keys=True))
            baselines.setdefault(key, []).append((node['id'], round(text_origin[1] - origin[1], 3)))
        center_error = abs(margins[0] - margins[2]) if baseline_symbol or shared_baseline else max(abs(margins[0] - margins[2]), abs(margins[1] - margins[3]))
        if min(bounds["width"], bounds["height"]) <= 0 or min(margins) < 0 or center_error > .102:
            failures.append(node["id"])
    for group in baselines.values():
        if len({v for _, v in group}) > 1:
            failures.extend(identity for identity, _ in group)
    status = "fail" if failures else ("unverified" if unknown or not count else "pass")
    label = "文字" if all_text else "数字"
    explanation = f"核对{count}个{label}文本：宽高偶数整数，X/Y最多一位小数，实际字形中心偏差不超过0.05px（另容许0.001px计算尾差）"
    if all_text:
        explanation += "；文本水平对齐属性须为CENTER；单字符标点仅自动检查水平居中，垂直基线须另行核对"
    if baseline_ids:
        explanation += "；明确采用shared_font_baseline的族检查共同基线、水平中心与完整边界"
    return check_item("snapshot.text_geometry" if all_text else "snapshot.digit_geometry", status, explanation, sorted(set(failures + unknown)))


def validate_target_binding(manifest, snapshot):
    pagination = snapshot.get('pagination', {})
    if pagination.get('complete') is not True or pagination.get('total_nodes') != pagination.get('collected_nodes') or pagination.get('collected_nodes') != len(snapshot.get('nodes', [])):
        return [check_item('snapshot.target_binding', 'unverified', '快照不完整，不能绑定目标规格')]
    try:
        expected = screen_spec(manifest.get('canvas'))
        actual = screen_spec(snapshot.get('config', {}).get('canvas'))
        ok = expected == actual and snapshot.get('config', {}).get('preview', actual['preview']) == manifest.get('exports', {}).get('preview')
        return [check_item('snapshot.target_binding', 'pass' if ok else 'fail', '目标清单、快照屏形与缩略图规范绑定核对')]
    except ValueError as exc:
        return [check_item('snapshot.target_binding', 'fail', str(exc))]


def validate_capability_binding(manifest, snapshot):
    modes = manifest.get("capability_modes")
    if not isinstance(modes, dict):
        return [check_item("snapshot.capability_binding", "unverified", "清单未声明可多种实现的能力模式")]
    entries = snapshot.get("native_progress", [])
    if not isinstance(entries, list):
        return [check_item("snapshot.capability_binding", "fail", "native_progress 必须是数组")]
    if not modes and not entries:
        return [check_item("snapshot.capability_binding", "not_applicable", "已声明未使用可选进度能力")]
    failures = []
    by_capability = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("capability"), str):
            failures.append(str(entry))
            continue
        by_capability.setdefault(entry["capability"], []).append(entry)
    for capability, mode in modes.items():
        count = len(by_capability.get(capability, []))
        if mode == "native_continuous" and count != 1:
            failures.append(f"{capability}:原生连续进度映射数量为{count}")
        elif mode == "state_images" and count:
            failures.append(f"{capability}:状态图模式不应声明原生连续进度")
    extra = sorted(set(by_capability) - set(modes))
    failures.extend(f"{capability}:快照存在未在清单声明的原生连续进度" for capability in extra)
    return [check_item("snapshot.capability_binding", "fail" if failures else "pass", "清单实现模式与Figma原生连续进度映射一致", failures)]


def validate_target_geometry(snapshot, spec):
    config = snapshot['config']
    nodes = {n['id']: n for n in snapshot['nodes']}
    failures, unknown = [], []
    for key in ('main_id', 'active_id', 'aod_id', 'preview_id'):
        n = nodes.get(config.get(key), {})
        expected = spec['preview'] if key == 'preview_id' else [spec['width'], spec['height']]
        if [n.get('width'), n.get('height')] != expected:
            failures.append(str(config.get(key)))
    active = nodes[config['active_id']]
    if 'cornerRadius' not in active:
        unknown.append(active['id'])
    elif active['cornerRadius'] != spec['corner_radius']:
        failures.append(active['id'])
    if config.get('preview', spec['preview']) != spec['preview']:
        failures.append('config.preview')
    if nodes[config['asset_board_id']].get('type') != 'SECTION':
        failures.append(config['asset_board_id'])
    result = [check_item('snapshot.target_geometry', 'fail' if failures else ('unverified' if unknown else 'pass'), '核对目标画布、Active圆角、缩略图和切图分区', failures+unknown)]
    bg = nodes.get(config.get('background_id'))
    bg_ok = bg and [bg.get('width'), bg.get('height')] == [spec['width'], spec['height']]
    result.append(check_item('snapshot.background_fit', ('pass' if bg_ok else 'fail') if bg else 'unverified', '背景母件须匹配目标尺寸；铺满与构图仍须视觉复核', [config.get('background_id')] if bg else []))
    # 排除组件库内部几何；只核对Active/AOD中直接组装的切图实例及布局祖先。
    failures, unknown = [], []
    for root in (config['active_id'], config['aod_id']):
        ids = descendants(nodes, root)
        for n in (nodes[k] for k in ids if nodes[k].get('type') == 'INSTANCE'):
            ancestors = descendants_up(n, nodes, root)
            if any(nodes[k].get('type') == 'INSTANCE' for k in ancestors) or not effectively_visible(n, nodes, root):
                continue
            master = nodes.get(n.get('main_component_id'))
            if not master or any(abs(n.get(k, -1)-master.get(k, -2)) > .001 for k in ('width','height')):
                failures.append(n['id'])
                continue
            # 不允许祖先缩放把大切图伪装成小规格；合法旋转/反射保持单位长度。
            for part in [n] + [nodes[k] for k in ancestors if k != root]:
                t = part.get('relative_transform')
                if not t:
                    unknown.append(part['id']); continue
                a,b = t[0][:2]; c,d = t[1][:2]
                if abs(a*a+c*c-1) > .001 or abs(b*b+d*d-1) > .001 or abs(a*b+c*d) > .001:
                    failures.append(part['id'])
    result.append(check_item('snapshot.resource_scale', 'fail' if failures else ('unverified' if unknown else 'pass'), '目标正式实例以切图原尺寸1×装配，旋转和反射允许，缩略图另行等比检查', failures+unknown))
    # 安全区使用采集的实际渲染边界；未提供区域证据不能以空违规表宣布通过。
    failures, unknown = [], []
    ids = snapshot.get('content_safe_ids', [])
    regions = config.get('canvas', {}).get('reserved_regions')
    if regions is None:
        unknown.append('未提供顶部/底部状态点区域')
    if not ids:
        unknown.append('缺少内容边界清单')
    roots = {config[key]: descendants(nodes, config[key]) for key in ('active_id','aod_id')}
    for identity in ids:
        owner = next((root for root,members in roots.items() if identity in members), None)
        if owner is None:
            failures.append(str(identity)); continue
        n = nodes[identity]
        if not effectively_visible(n,nodes,owner):
            continue
        box = n.get('render_bounds')
        transform = nodes[owner].get('absolute_transform')
        if not box or not transform or transform[0][:2] != [1,0] or transform[1][:2] != [0,1]:
            unknown.append(identity); continue
        ox, oy = transform[0][2], transform[1][2]
        x, y, w, h = box['x']-ox, box['y']-oy, box['width'], box['height']
        if any(not contains(x+dx,y+dy,spec['width'],spec['height'],spec['shape'],spec['corner_radius'],spec['safe_margin']) for dx in (0,w) for dy in (0,h)):
            failures.append(identity)
        for r in regions or []:
            if x < r['x']+r['width'] and x+w > r['x'] and y < r['y']+r['height'] and y+h > r['y']:
                failures.append(identity)
    result.append(check_item('snapshot.safe_area', 'fail' if failures else ('unverified' if unknown else 'pass'), '按目标屏形核对指定功能内容渲染边界及状态点区域；背景装饰不套用内容留白', failures+unknown))
    groups = snapshot.get('numeric_groups', [])
    failures, unknown = [], []
    for identity in groups:
        group = nodes.get(identity)
        if not group:
            failures.append(str(identity)); continue
        children = [n for n in nodes.values() if n.get('parent_id') == identity and effectively_visible(n,nodes,identity)]
        if group.get('layoutMode') != 'HORIZONTAL' or group.get('itemSpacing') != 0 or group.get('primaryAxisAlignItems') == 'SPACE_BETWEEN':
            failures.append(identity)
        if not children or any(n.get('type') != 'INSTANCE' for n in children):
            failures.append(identity)
        # 顺序按快照中的子节点顺序，边框必须相接，包装节点不能伪造零间距。
        for a,b in zip(children,children[1:]):
            if not all(is_finite_number(v) for v in (a.get('x'),a.get('width'),b.get('x'))):
                unknown.append(identity)
            elif abs(a['x']+a['width']-b['x']) > .001:
                failures.append(identity)
    result.append(check_item('snapshot.numeric_spacing', 'fail' if failures else ('unverified' if unknown or not groups else 'pass'), '完整数值切图水平自动布局、零间距且相邻切图边框相接；字段/单位在外层排布', failures+unknown))
    return result


def validate_snapshot(snapshot: dict, capability_modes: dict | None = None) -> list[dict]:
    checks: list[dict] = []
    config = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    if config.get("stage") == "prototype":
        return [check_item("snapshot.production_scope", "unverified", "样稿快照仅证明已建立的角色，不能作为完整生产证据")]
    if config.get("profile") == "preflight" or snapshot.get("evidence_level") == "preflight":
        return [check_item("snapshot.preflight", "unverified", "轻量预检不能作为完整交付证据；设计稳定后采集正式快照")]
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    nodes_by_id = {node.get("id"): node for node in nodes if isinstance(node, dict) and node.get("id")}
    required = ("section_id", "asset_board_id", "main_id", "active_id", "aod_id", "preview_id")
    missing = [field for field in required if config.get(field) not in nodes_by_id]
    if config.get("aod_id") in (config.get("main_id"), config.get("active_id")) or config.get("preview_id") in (config.get("main_id"), config.get("active_id"), config.get("aod_id")):
        missing.append("AOD/Preview不能使用其他角色的节点别名")
    if snapshot.get("schema_version") not in (1, 2) or missing:
        checks.append(check_item("snapshot.structure", "fail", "快照结构不完整或指定节点缺失", missing))
        return checks
    checks.append(check_item("snapshot.structure", "pass", "快照结构和指定节点可定位", [config[field] for field in required]))

    pagination = snapshot.get("pagination", {})
    total = pagination.get("total_nodes")
    collected = pagination.get("collected_nodes")
    complete = pagination.get("complete") is True and total == collected == len(nodes)
    if not complete:
        checks.append(check_item("snapshot.pagination", "unverified", "分页快照未标记完整，或 total_nodes/collected_nodes 与节点数不一致"))
        for check_id in (
            "snapshot.section_scope", "snapshot.component_names", "snapshot.component_exports",
            "snapshot.instance_links", "snapshot.assembly_layout", "snapshot.deep_composition", "snapshot.text_measurements",
            "snapshot.icon_orientation", "snapshot.state_layout", "snapshot.pointer_annotations", "snapshot.resource_ownership",
            "snapshot.resource_families", "snapshot.time_cutout_pairs", "snapshot.shared_data", "snapshot.state_families", "snapshot.time_roles", "snapshot.aod_style", "snapshot.animations", "snapshot.integer_positions", "snapshot.digit_geometry", "snapshot.text_geometry", "snapshot.progress_tracks",
        ):
            analog_time_pairs = check_id == "snapshot.time_cutout_pairs" and snapshot.get("face_type") == "analog" and not has_digital_time(snapshot)
            checks.append(check_item(check_id, "not_applicable" if analog_time_pairs else "unverified", "纯指针表盘不要求数字时间字模配对" if analog_time_pairs else "快照不完整，不能据此推定通过"))
        return checks
    if len(nodes_by_id) != len(nodes):
        checks.append(check_item("snapshot.pagination", "fail", "合并快照含重复节点 ID"))
        return checks
    checks.append(check_item("snapshot.pagination", "pass", "合并快照页数完整，节点数与唯一 ID 一致"))

    section_ids = descendants(nodes_by_id, config["section_id"])
    shared_ids, ancestor_ids, invalid_shared = set(), set(), []
    for identity in config.get("shared_master_ids", []):
        master = nodes_by_id.get(identity)
        if not master or master.get("type") != "COMPONENT":
            invalid_shared.append(str(identity))
            continue
        shared_ids.update(descendants(nodes_by_id, identity))
        ancestor_ids.update(descendants_up(master, nodes_by_id, config["section_id"]))
    pending = list(shared_ids)
    visited_dependencies = set()
    while pending:
        identity = pending.pop()
        if identity in visited_dependencies:
            continue
        visited_dependencies.add(identity)
        n = nodes_by_id[identity]
        if n.get('type') == 'INSTANCE':
            master = nodes_by_id.get(n.get('main_component_id'))
            if not master or master.get('type') != 'COMPONENT':
                invalid_shared.append(identity)
                continue
            dependency_ids = descendants(nodes_by_id, master['id'])
            pending.extend(dependency_ids - shared_ids)
            shared_ids.update(dependency_ids)
            ancestor_ids.update(descendants_up(master, nodes_by_id, config['section_id']))
    outside = invalid_shared + [node["id"] for node in nodes if node.get("id") not in section_ids | shared_ids | ancestor_ids]
    checks.append(check_item("snapshot.section_scope", "fail" if outside else "pass", "发现指定 section 外节点" if outside else "所有节点都位于指定 section", outside))

    board_ids = descendants(nodes_by_id, config["asset_board_id"]) | shared_ids
    export_items = snapshot.get("export_items", [])
    item_errors = []
    formal_nodes = []
    for item in export_items if isinstance(export_items, list) else []:
        node = nodes_by_id.get(item.get("node_id")) if isinstance(item, dict) else None
        if not node or not isinstance(item.get("path"), str) or not item["path"].endswith(".png"):
            item_errors.append(str(item))
        else:
            formal_nodes.append(node)
    if not any(item.get("role") == "presentation_main" for item in export_items if isinstance(item, dict)):
        item_errors.append("缺少 presentation_main 导出映射")
    resource_ids = config.get("resource_ids")
    if resource_ids is not None:
        invalid_resources = set(resource_ids) - {n['id'] for n in nodes if n['id'] in board_ids and n.get('type') == 'COMPONENT'}
        checks.append(check_item("snapshot.resource_scope", "fail" if invalid_resources or len(set(resource_ids)) != len(resource_ids) else "pass", "核对目标资源映射中的真实母件", sorted(invalid_resources)))
    board_components = [node for node in nodes if node.get("id") in board_ids and node.get("type") == "COMPONENT" and (resource_ids is None or node['id'] in resource_ids) and (node['id'] not in shared_ids or node['id'] in config.get('shared_master_ids', []) or node['id'] in descendants(nodes_by_id, config['asset_board_id']))]
    try:
        spec = screen_spec(config.get('canvas'))
    except ValueError as exc:
        checks.append(check_item('snapshot.target_geometry', 'fail', str(exc)))
        return checks
    if 'canvas' in config:
        checks.extend(validate_target_geometry(snapshot, spec))
    position_failures, position_unknown, visited = [], [], set()
    children = {}
    for node in nodes:
        children.setdefault(node.get("parent_id"), []).append(node)

    artwork_roots = set()
    for entry in snapshot.get("vector_artwork_roots", []):
        root = nodes_by_id.get(entry.get("node_id"))
        content_ids = descendants(nodes_by_id, root["id"]) if root else set()
        if not root or root.get("type") != "FRAME" or not entry.get("source") or any(nodes_by_id[i].get("type") == "TEXT" for i in content_ids):
            position_unknown.append(str(entry))
        else:
            artwork_roots.add(root["id"])

    precise_ids, precision_diagnostics = precision_exemptions(snapshot, nodes_by_id)

    def check_position(node):
        if node["id"] in visited or node.get("visible") is False:
            return
        visited.add(node["id"])
        if not all(is_finite_number(node.get(k)) for k in ("x", "y")):
            position_unknown.append(node["id"])
        elif node["id"] not in precise_ids and node.get("parent_id") != config["preview_id"]:
            decimal_text = snapshot.get("text_geometry") is True and text_source(node, nodes_by_id)
            decimal_digit = snapshot.get("digit_geometry") is True and digit_source(node, nodes_by_id)
            precision = 10 if decimal_text or decimal_digit else 1
            if any(abs(node[k] * precision - round(node[k] * precision)) > .00001 for k in ("x", "y")):
                position_failures.append(node["id"])
        if node.get("type") != "INSTANCE" and node["id"] not in artwork_roots:
            for child in children.get(node["id"], []):
                check_position(child)

    if snapshot.get("integer_positions") is True:
        roots = board_components + [nodes_by_id[config[k]] for k in ("main_id", "aod_id", "preview_id")]
        for root in roots:
            for child in children.get(root["id"], []):
                check_position(child)
    else:
        position_unknown.append("未声明整数定位要求")
    checks.append(check_item("snapshot.integer_positions", "fail" if position_failures else ("unverified" if position_unknown else "pass"), f"核对{len(visited)}个原生可见图层的局部X/Y；按已声明的文字或数字规则允许文本一位小数，其余布局保持整数；派生坐标由源组件和缩放保证" if not position_failures else "原生布局位置精度不满足规则", position_failures + position_unknown))
    relevant = [d for d in precision_diagnostics if d['kind'] == 'pointer' or d['node_id'] in visited]
    invalid = [d['node_id']+':'+d['detail'] for d in relevant if d['status'] == 'fail']
    unknown = [d['node_id']+':'+d['detail'] for d in relevant if d['status'] == 'unverified']
    checks.append(check_item('snapshot.layout_precision', 'fail' if invalid else ('unverified' if unknown else ('pass' if relevant else 'not_applicable')),
                             '仅以实际轴心、祖先矩阵和径向源结构证明必要的小数几何，不豁免普通后代', invalid+unknown))
    checks.append(validate_digit_geometry(nodes_by_id, baseline_families=snapshot.get('resource_families')) if snapshot.get("digit_geometry") is True else check_item("snapshot.digit_geometry", "unverified", "未声明数字偶数尺寸与一位小数居中规则"))
    checks.append(validate_digit_geometry(nodes_by_id, all_text=True, baseline_families=snapshot.get('resource_families')) if snapshot.get("text_geometry") is True else check_item("snapshot.text_geometry", "unverified", "未声明完整文字切图几何规则，不能用数字检查代替"))

    progress_failures, progress_unknown = [], []
    progress_resources = snapshot.get("progress_resources", [])
    for family in progress_resources:
        prefix, segments = family.get("prefix"), family.get("segment_count")
        members = [n for n in board_components if isinstance(prefix, str) and n.get("name", "").startswith(prefix)]
        if family.get("shape") not in {"regular", "irregular", "segmented"} or not is_integer(segments) or segments < 1:
            progress_failures.append(str(prefix))
            continue
        if family["shape"] in {"irregular", "segmented"} and family.get("include_track") is not True:
            progress_failures.append(str(prefix))
        if family.get("include_track") is not True:
            continue
        if len(members) != segments + 1:
            progress_failures.append(str(prefix))
        for member in members:
            ids = descendants(nodes_by_id, member["id"])
            parts = [nodes_by_id[i] for i in ids if nodes_by_id[i].get("type") == "VECTOR"]
            hidden = any(nodes_by_id[i].get("visible") is False or nodes_by_id[i].get("opacity", 1) <= 0 for p in parts for i in [p["id"]] + descendants_up(p, nodes_by_id, member["id"]))
            if len(parts) != segments or hidden or member.get("visible") is False:
                progress_failures.append(member["id"])
            if any(not isinstance(p.get("fills"), list) or not any(f.get("visible", True) and f.get("opacity", 1) > 0 for f in p["fills"]) for p in parts):
                progress_unknown.append(member["id"])
        if any(n.get("name") == family.get("static_track_name") for n in nodes if n.get("visible") is not False):
            progress_failures.append(str(family.get("static_track_name")))
    if not progress_resources:
        progress_unknown.append("未提供进度形态与带底规则")
    pointer_progress = snapshot.get("progress_representation") == "pointers" and not progress_resources and any(p.get('role') not in {'Hour','Minute','Second','Hub','HourDark','MinuteDark'} for p in snapshot.get('pointers', []))
    native_progress = bool(snapshot.get("native_progress")) and not progress_resources
    non_state_progress = pointer_progress or native_progress
    explanation = "本次进度使用独立数据指针，转轴与范围由指针检查覆盖" if pointer_progress else ("本次进度使用原生连续控件，结构由 native_progress 检查覆盖" if native_progress else "异形或分段进度状态必须保留完整底；规则连续进度按实现模式检查")
    checks.append(check_item("snapshot.progress_tracks", "not_applicable" if non_state_progress else ("fail" if progress_failures else ("unverified" if progress_unknown else "pass")), explanation, [] if non_state_progress else progress_failures + progress_unknown))
    source_only_ids = set(snapshot.get("source_only_node_ids", []))
    invalid_source_only = sorted(source_only_ids - {node["id"] for node in board_components})
    asset_items = [item for item in export_items if isinstance(item, dict) and item.get("role") in {"asset", "aod_full", "presentation_main"}]
    asset_ids = {item.get("node_id") for item in asset_items}
    expected_asset_ids = {node["id"] for node in board_components} - source_only_ids
    uncovered = sorted(expected_asset_ids - asset_ids)
    item_errors.extend(f"未映射的目标资源:{i['node_id']}" for i in asset_items if i.get("role") == "asset" and i["node_id"] not in expected_asset_ids)
    wrong_paths = [item.get("node_id") for item in asset_items if item.get("node_id") in expected_asset_ids and nodes_by_id.get(item.get("node_id"), {}).get("name", "") + ".png" != item.get("path")]
    components = [node for node in board_components if node["id"] in expected_asset_ids]
    duplicate_names = sorted(name for name, count in Counter(node.get("name") for node in components).items() if name and count > 1)
    checks.append(check_item("snapshot.component_names", "fail" if duplicate_names else "pass", "正式资产板母件存在重复名" if duplicate_names else "正式资产板母件名称唯一；实例重名不计", duplicate_names))
    enabled_source_exports = [f"非交付源仍开启PNG导出:{node['id']}" for node in board_components if node["id"] in source_only_ids and png_export(node)]
    missing_exports = item_errors + invalid_source_only + enabled_source_exports + [f"未覆盖:{node_id}" for node_id in uncovered] + [f"路径不匹配:{node_id}" for node_id in wrong_paths] + [node["id"] for node in formal_nodes if not png_export(node)]
    checks.append(check_item("snapshot.component_exports", "fail" if missing_exports else "pass", ("正式导出项不完整或缺少 PNG 导出设置：" + ", ".join(missing_exports)) if missing_exports else f"资产板母件 {len(board_components)} 个，正式导出 {len(expected_asset_ids)} 个，source_only {len(source_only_ids)} 个，覆盖完整且均含 PNG 导出设置", missing_exports))

    state_families = snapshot.get("state_families", [])
    state_failures = []
    state_summaries = []
    for family in state_families:
        name = family.get("name", "未命名状态族")
        prefix = family.get("node_name_prefix")
        expected = family.get("expected_count")
        start = family.get("start_index", 0)
        width = family.get("index_width", 3)
        if not isinstance(prefix, str) or not is_integer(expected) or expected < 1 or not is_integer(start) or not is_integer(width) or width < 1:
            state_failures.append(f"{name} 配置无效")
            continue
        matched = [node for node in board_components if node.get("name", "").startswith(prefix)]
        names = [node.get("name", "") for node in matched]
        suffixes = [value[len(prefix):] for value in names]
        indices = [int(value) for value in suffixes if re.fullmatch(rf"\d{{{width}}}", value)]
        expected_indices = list(range(start, start + expected))
        missing_indices = sorted(set(expected_indices) - set(indices))
        extra_indices = sorted(set(indices) - set(expected_indices))
        duplicate_indices = sorted(index for index, count in Counter(indices).items() if count > 1)
        duplicate_state_names = sorted(value for value, count in Counter(names).items() if count > 1)
        invalid_suffixes = sorted(value for value in suffixes if not re.fullmatch(rf"\d{{{width}}}", value))
        not_exported = sorted(node["id"] for node in matched if node["id"] not in asset_ids)
        state_summaries.append(f"{name}:期望 {expected}，实际 {len(matched)}")
        if len(matched) != expected or missing_indices:
            state_failures.append(f"{name} 缺号 {missing_indices}，实际 {len(matched)}/{expected}")
        if duplicate_indices or duplicate_state_names:
            state_failures.append(f"{name} 重号 {duplicate_indices or duplicate_state_names}")
        if extra_indices or invalid_suffixes:
            state_failures.append(f"{name} 错号/格式错误 {extra_indices + invalid_suffixes}")
        if not_exported:
            state_failures.append(f"{name} 节点未进入正式导出 {not_exported}")
    state_status = "unverified" if not state_families else ("fail" if state_failures else "pass")
    checks.append(check_item("snapshot.state_families", state_status, "未提供 state_families，无法从原始母件验证状态数量与连续命名" if state_status == "unverified" else ("；".join(state_failures) if state_failures else "；".join(state_summaries) + "；序号连续无重号且正式导出已覆盖"), state_failures))

    instance_checks = []
    assembly_failures = []
    assembly_unverified = []
    for container_field in ("main_id", "preview_id"):
        container_ids = descendants(nodes_by_id, config[container_field])
        instances = [node for node in nodes if node.get("id") in container_ids and node.get("type") == "INSTANCE"]
        top_instances = [node for node in instances if not any(nodes_by_id[ancestor_id].get("type") == "INSTANCE" for ancestor_id in descendants_up(node, nodes_by_id, config[container_field]))]
        active_refs = [node for node in top_instances if node.get("main_component_id") == config["active_id"] and effectively_visible(node, nodes_by_id, config[container_field])]
        wrong = [node["id"] for node in top_instances if not node.get("main_component_id") or node.get("main_component_id") not in board_ids]
        if not active_refs:
            wrong.append(config[container_field])
        else:
            container = nodes_by_id[config[container_field]]
            expectation = (fit_preview(spec) if container_field == "preview_id" and "canvas" in config else snapshot.get("assembly_expectations", {}).get(container_field, {}))
            expected_x = expectation.get("x", 0)
            expected_y = expectation.get("y", 0)
            expected_width = expectation.get("width", container.get("width"))
            expected_height = expectation.get("height", container.get("height"))
            for instance in active_refs:
                if instance.get("parent_id") != config[container_field]:
                    assembly_unverified.append(instance["id"])
                    continue
                transform = instance.get("relative_transform")
                valid_transform = (
                    isinstance(transform, list) and len(transform) == 2
                    and all(isinstance(row, list) and len(row) == 3 for row in transform)
                    and all(is_finite_number(value) for row in transform for value in row)
                )
                if not valid_transform:
                    assembly_failures.append(instance["id"])
                    continue
                scale_x, skew_y, offset_x = transform[0]
                skew_x, scale_y, offset_y = transform[1]
                if (
                    abs(offset_x - expected_x) > .001 or abs(offset_y - expected_y) > .001
                    or scale_x <= 0 or scale_y <= 0
                    or abs(skew_x) > .001 or abs(skew_y) > .001 or abs(scale_x - scale_y) > .001
                    or abs(instance.get("width", math.inf) - expected_width) > .001
                    or abs(instance.get("height", math.inf) - expected_height) > .001
                ):
                    assembly_failures.append(instance["id"])
        instance_checks.extend(wrong)
    active_ids = descendants(nodes_by_id, config['active_id'])
    for n in (nodes_by_id[i] for i in active_ids if nodes_by_id[i].get('type') == 'INSTANCE'):
        if not any(nodes_by_id[i].get('type') == 'INSTANCE' for i in descendants_up(n,nodes_by_id,config['active_id'])):
            if n.get('main_component_id') not in expected_asset_ids | source_only_ids:
                instance_checks.append(n['id'])
    aod_ids = descendants(nodes_by_id, config["aod_id"])
    aod_instances = [node for node in nodes if node.get("id") in aod_ids and node.get("type") == "INSTANCE"]
    aod_top_instances = [node for node in aod_instances if not any(nodes_by_id[ancestor_id].get("type") == "INSTANCE" for ancestor_id in descendants_up(node, nodes_by_id, config["aod_id"]))]
    instance_checks.extend(node["id"] for node in aod_top_instances if not node.get("main_component_id") or node.get("main_component_id") not in board_ids)
    instance_checks.extend(node["id"] for node in nodes if node.get("type") == "INSTANCE" and not node.get("main_component_id"))
    checks.append(check_item("snapshot.instance_links", "fail" if instance_checks else "pass", "主稿或预览缺少有效可见 Active 引用，或存在断链/错误母件" if instance_checks else "主稿与预览均有有效可见 Active 引用，AOD 实例无断链", instance_checks))
    assembly_status = "fail" if assembly_failures else ("unverified" if assembly_unverified else "pass")
    checks.append(check_item("snapshot.assembly_layout", assembly_status, "主稿或缩略图 Active 实例组装布局存在偏移、非等比缩放或未填满容器" if assembly_failures else ("Active 实例存在中间容器，未复合祖先变换，组装布局未验证" if assembly_unverified else "主稿与缩略图 Active 实例均从局部 (0,0) 等比填满容器"), assembly_failures + assembly_unverified))

    if config.get("skip_instance_children") or snapshot.get("traversal", {}).get("skip_instance_children"):
        proof = snapshot.get('composition_proof', {})
        roots = proof.get('roots', [])
        expected_roots = {config[k] for k in ('main_id','aod_id','preview_id')}
        valid = bool(snapshot.get('capture_signature')) and proof.get('capture_signature') == snapshot.get('capture_signature') and len(roots) == 3 and {r.get('root_id') for r in roots} == expected_roots
        direct_leaves, leaf_count = [], 0
        for root in roots:
            leaves = root.get('leaves', [])
            ids = [n.get('id') for n in leaves]
            valid = valid and bool(leaves) and len(set(ids)) == len(ids) and all(ids) and root.get('visited', 0) >= len(leaves)
            leaf_count += len(leaves)
            direct_leaves.extend(n.get('id') for n in leaves if not n.get('instance_id') or not n.get('master_id'))
        checks.append(check_item("snapshot.deep_composition", ("fail" if direct_leaves else "pass") if valid else "unverified", f"同轮补充遍历核对{leaf_count}个实际可见叶子及实例祖先" if valid else "采集时跳过实例子节点，且缺少同轮逐叶子组装证据", direct_leaves))
    else:
        direct_leaves = []
        for container_field in ("main_id", "aod_id", "preview_id"):
            container_ids = descendants(nodes_by_id, config[container_field])
            for node_id in container_ids - {config[container_field]}:
                node = nodes_by_id[node_id]
                has_child = any(child.get("parent_id") == node_id for child in nodes)
                if has_child or node.get("type") == "INSTANCE" or not effectively_visible(node, nodes_by_id, config[container_field]):
                    continue
                ancestor_id = node.get("parent_id")
                carried = False
                while ancestor_id in container_ids and ancestor_id in nodes_by_id:
                    if nodes_by_id[ancestor_id].get("type") == "INSTANCE":
                        carried = True
                        break
                    ancestor_id = nodes_by_id[ancestor_id].get("parent_id")
                if not carried:
                    direct_leaves.append(node_id)
        checks.append(check_item("snapshot.deep_composition", "fail" if direct_leaves else "pass", "主稿、AOD 或预览存在未由切图实例承载的可见叶子" if direct_leaves else "深层可见叶子均由实例祖先承载", direct_leaves))

    measurements = {}
    measurement_failures = []
    measurement_unverified = []
    text_component_ids = {node_id for family in snapshot.get("resource_families", []) if family.get("kind") in {"time", "second", "data", "date", "unit", "symbol", "week", "month", "label"} for node_id in family.get("node_ids", [])}
    for component in components:
        if component["id"] not in text_component_ids:
            continue
        text_descendants = [nodes_by_id[node_id] for node_id in descendants(nodes_by_id, component["id"]) if node_id != component["id"] and "characters" in nodes_by_id[node_id]]
        if not text_descendants:
            continue
        if len(text_descendants) != 1:
            measurement_unverified.append(component["id"])
            continue
        text_node = text_descendants[0]
        bounds = text_node.get("unclipped_glyph_bounds") or text_node.get("glyph_bounds") or text_node.get("render_bounds")
        component_origin = node_origin(component)
        if not isinstance(bounds, dict) or component_origin is None or any(not is_finite_number(bounds.get(key)) for key in ("x", "y", "width", "height")):
            measurement_unverified.append(component["id"])
            continue
        if not all(is_finite_number(component.get(key)) and component.get(key) > 0 for key in ("width", "height")):
            measurement_unverified.append(component["id"])
            continue
        text_origin = node_origin(text_node)
        if text_origin is None:
            measurement_unverified.append(component["id"])
            continue
        local_x, local_y = bounds["x"] - component_origin[0], bounds["y"] - component_origin[1]
        text_local_y = text_origin[1] - component_origin[1]
        margins = (local_x, local_y, component.get("width", 0) - local_x - bounds["width"], component.get("height", 0) - local_y - bounds["height"])
        ancestor = nodes_by_id.get(component.get("parent_id"))
        clipped_ancestor = False
        while ancestor and ancestor.get("id") != config["section_id"]:
            if ancestor.get("clips_content") is True:
                ancestor_origin = node_origin(ancestor)
                if ancestor_origin is None or not has_translation_only_transform(ancestor) or not all(is_finite_number(ancestor.get(key)) for key in ("width", "height")):
                    clipped_ancestor = True
                    break
                ancestor_margins = (
                    bounds["x"] - ancestor_origin[0], bounds["y"] - ancestor_origin[1],
                    ancestor_origin[0] + ancestor["width"] - bounds["x"] - bounds["width"],
                    ancestor_origin[1] + ancestor["height"] - bounds["y"] - bounds["height"],
                )
                if min(ancestor_margins) <= 0:
                    clipped_ancestor = True
                    break
            ancestor = nodes_by_id.get(ancestor.get("parent_id"))
        if clipped_ancestor and not text_node.get("unclipped_glyph_bounds"):
            measurement_unverified.append(component["id"])
            continue
        bearing_x = bounds["x"] - text_origin[0]
        first_aligned_left = bearing_x + math.ceil(-bearing_x * 10 - .00001) / 10
        measurements[component["id"]] = {
            "size": (component.get("width"), component.get("height")),
            # 最左可用0.1px原点仍须容纳完整字形；极窄余量可能无法落在该坐标网格上。
            "minimum_width": rounded_multiple_of_four(bounds["width"] + first_aligned_left),
            "text_origin_y": text_local_y,
            "glyph_top": bounds["y"] - text_origin[1],
            "glyph_bottom": bounds["y"] + bounds["height"] - text_origin[1],
            "font": json.dumps([text_node.get("font_name"), text_node.get("font_size")], ensure_ascii=False, sort_keys=True),
            "character": str(text_node.get("characters", "")),
            "margins": margins,
        }
        if component.get("width", 0) % 4 or component.get("height", 0) % 4 or min(margins) < 0:
            measurement_failures.append(component["id"])
    measurement_status = "unverified" if not snapshot.get("resource_families") else ("fail" if measurement_failures else ("unverified" if measurement_unverified else "pass"))
    checks.append(check_item("snapshot.text_measurements", measurement_status, "文字母件不是 4 的倍数，或完整渲染范围超出切图边界" if measurement_failures else ("文字子节点测量不足，或被祖先裁切且无额外未裁测量" if measurement_unverified else "文字母件为4倍数；完整字形四边均未超出容器"), measurement_failures + measurement_unverified))

    # Pair by actual character, including separators; never infer equality from the displayed time alone.
    time_style = snapshot.get("aod_style", {})
    pair_failures, pair_unknown, time_pairs, time_groups = [], [], {}, []
    if "time_asset_pairs" in snapshot:
        entries = snapshot["time_asset_pairs"]
        groups, seen, by_id = {}, set(), {n["id"]: n for n in board_components}
        if not isinstance(entries, list) or not entries:
            pair_failures.append("time_asset_pairs须为非空逐字符配对清单")
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                pair_failures.append("时间字符配对必须是对象"); continue
            active, dark = (by_id.get(entry.get(k)) for k in ("active_id", "aod_id"))
            if not active or not dark or active["id"] == dark["id"] or any(n["id"] in seen for n in (active, dark)):
                pair_failures.append(str(entry)+":配对母件缺失或重复"); continue
            seen.update((active["id"], dark["id"]))
            key = tuple(n.get("name", "").rpartition("/")[0] for n in (active, dark))
            paired = groups.setdefault(key, ({}, {}))
            chars = []
            for i, master in enumerate((active, dark)):
                texts = [nodes_by_id[j] for j in descendants(nodes_by_id, master["id"]) if nodes_by_id[j].get("type") == "TEXT"]
                if len(texts) != 1 or not texts[0].get("characters"):
                    pair_unknown.append(master["id"]); continue
                char = texts[0]["characters"]; chars.append(char)
                if char in paired[i]: pair_failures.append(master["id"]+":重复时间字符")
                paired[i][char] = master
            if len(chars) == 2 and chars[0] != chars[1]: pair_failures.append(str(entry)+":字符不匹配")
        declared_dark = {i for f in snapshot.get("resource_families", []) if f.get("aod") is True and f.get("kind") in ("time", "time_hour", "time_minute", "second") for i in f.get("node_ids", [])}
        if not declared_dark.issubset(seen): pair_failures.append("已声明的AOD时间族未完整配对")
        for prefixes, paired in groups.items():
            for prefix, members in zip(prefixes, paired):
                actual = {n["id"] for n in board_components if n.get("name", "").rpartition("/")[0] == prefix}
                if actual != {n["id"] for n in members.values()}: pair_failures.append(prefix+":未完整登记配对")
            time_groups.extend(paired)
    else:
        for key in ("active_node_name_prefix", "node_name_prefix"):
            prefix = time_style.get(key)
            group = {}
            for master in board_components:
                if not isinstance(prefix, str) or not prefix or not master.get("name", "").startswith(prefix):
                    continue
                texts = [nodes_by_id[i] for i in descendants(nodes_by_id, master["id"]) if nodes_by_id[i].get("type") == "TEXT"]
                if len(texts) != 1 or not texts[0].get("characters"):
                    pair_unknown.append(master["id"])
                    continue
                char = texts[0]["characters"]
                if char in group:
                    pair_failures.append(f"重复时间字符：{char}")
                group[char] = master
            if not group:
                pair_unknown.append(key)
            time_groups.append(group)
        legacy_dark_ids = {n['id'] for n in time_groups[1].values()}
        if any(f.get('aod') is True and f.get('kind') in ('time', 'time_hour', 'time_minute', 'second')
               and not set(f.get('node_ids', [])).issubset(legacy_dark_ids) for f in snapshot.get('resource_families', [])):
            pair_unknown.append('额外声明的AOD时间族缺少time_asset_pairs，不能沿用单前缀通过结论')
    for group_index in range(0, len(time_groups), 2):
        active_chars, dark_chars = time_groups[group_index:group_index+2]
        if not active_chars or not dark_chars:
            continue
        if set(active_chars) != set(dark_chars) or not set("0123456789").issubset(active_chars):
            pair_failures.append("亮屏/AOD必须完整配对0–9及所有时间分隔符")
        for char in active_chars.keys() & dark_chars.keys():
            active, dark = active_chars[char], dark_chars[char]
            if active["id"] == dark["id"]:
                pair_failures.append(f"亮屏/AOD不得指向同一母件：{char}")
                continue
            if not all(is_finite_number(n.get(k)) and n[k] > 0 for n in (active, dark) for k in ("width", "height")):
                pair_unknown.append(char)
            elif (active["width"], active["height"]) != (dark["width"], dark["height"]):
                pair_failures.append(f"{char}: {active['id']} 与 {dark['id']} 切图宽高不同")
            time_pairs[active["id"]] = dark["id"]
            time_pairs[dark["id"]] = active["id"]
    pair_status = "fail" if pair_failures else ("unverified" if pair_unknown else "pass")
    if snapshot.get("face_type") == "analog" and not has_digital_time(snapshot):
        checks.append(check_item("snapshot.time_cutout_pairs", "not_applicable", "纯指针表盘不要求数字时间字模配对"))
    else:
        checks.append(check_item("snapshot.time_cutout_pairs", pair_status, "亮屏/AOD时间字模按实际字符逐对同宽同高；包括0–9及分隔符" if pair_status == "pass" else "时间字模配对尺寸不一致、字符不完整或缺少原始配对证据", pair_failures + pair_unknown))

    family_failures = []
    family_unverified = []
    for family in snapshot.get("resource_families", []):
        family_measurements = [measurements.get(node_id) for node_id in family.get("node_ids", [])]
        if not family_measurements or any(item is None for item in family_measurements):
            family_unverified.append(family.get("name", "未命名资源族"))
            continue
        vertical_padding = family.get("vertical_padding", 0)
        if not is_finite_number(vertical_padding) or vertical_padding < 0:
            family_unverified.append(family.get("name", "未命名资源族"))
            continue
        paired_ids = [time_pairs[node_id] for node_id in family.get("node_ids", []) if node_id in time_pairs]
        if any(node_id not in measurements for node_id in paired_ids):
            family_unverified.append(family.get("name", "未命名资源族"))
            continue
        paired_measurements = [measurements[node_id] for node_id in paired_ids]
        joint_measurements = family_measurements + paired_measurements
        group_top = min(item['glyph_top'] for item in joint_measurements)
        grid_top = group_top + math.ceil(-group_top * 10 - .00001) / 10 if family.get('baseline_policy') == 'shared_font_baseline' else 0
        common_height = rounded_multiple_of_four(max(item["glyph_bottom"] for item in joint_measurements) - group_top + grid_top + 2 * vertical_padding)
        digit_measurements = [item for item in family_measurements if re.fullmatch(r"[0-9]", item["character"])]
        common_width = max((item["minimum_width"] for item in joint_measurements if re.fullmatch(r"[0-9]", item["character"])), default=0)
        size_failure = any(item["size"][1] != common_height for item in family_measurements)
        if family.get("kind") in {"second", "data", "date"}:
            size_failure = size_failure or any(item["size"][0] != common_width for item in digit_measurements)
            size_failure = size_failure or any(item["size"][0] != max(item["minimum_width"], measurements.get(time_pairs.get(node_id), {}).get("minimum_width", 0)) for node_id, item in zip(family.get("node_ids", []), family_measurements) if item not in digit_measurements)
        else:
            size_failure = size_failure or any(item["size"][0] != max(item["minimum_width"], measurements.get(time_pairs.get(node_id), {}).get("minimum_width", 0)) for node_id, item in zip(family.get("node_ids", []), family_measurements))
        baseline_groups = {}
        shared_baseline = family.get('baseline_policy') == 'shared_font_baseline'
        for item in family_measurements:
            # 数字以实际可见字形居中；7/9等高度不同的字形需要微调Y。
            # 不把文本框同Y误当作居中，符号与单位仍保留各自字体基线。
            if not shared_baseline and snapshot.get("digit_geometry") is True and re.fullmatch(r"[0-9]", item["character"]):
                continue
            baseline_key = (item["font"], item["character"] in "0123456789" and len(item["character"]) == 1) if snapshot.get("digit_geometry") is True and not shared_baseline else item["font"]
            baseline_groups.setdefault(baseline_key, set()).add(round(item["text_origin_y"], 3))
        if size_failure or any(len(values) > 1 for values in baseline_groups.values()):
            family_failures.append(family.get("name", "未命名资源族"))
    status = "fail" if family_failures else ("unverified" if not snapshot.get("resource_families") or family_unverified else "pass")
    checks.append(check_item("snapshot.resource_families", status, "资源族映射或字形测量不足" if status == "unverified" else ("同族宽高或基线规则不满足" if family_failures else "秒数、数据和日期的同族 0-9 尺寸统一；主时间、symbol 与单位按各自规则"), family_failures + family_unverified))

    policy = snapshot.get("shared_data_policy")
    pool_failures, pool_unknown, pools = [], [], {}
    if not isinstance(policy, dict) or not isinstance(policy.get("prefix"), str) or not policy["prefix"]:
        pool_unknown.append("缺少共享数据字模范围")
    else:
        preserved = policy.get("preserved_families", [])
        for family in snapshot.get("resource_families", []):
            if family.get("kind") != "data" or family.get("name") in preserved:
                continue
            for node_id in family.get("node_ids", []):
                node = nodes_by_id.get(node_id)
                texts = [nodes_by_id[i] for i in descendants(nodes_by_id, node_id) if nodes_by_id[i].get("type") == "TEXT"] if node else []
                if len(texts) != 1:
                    pool_unknown.append(node_id)
                    continue
                text_node = texts[0]
                char = text_node.get("characters", "")
                if char not in "0123456789" or len(char) != 1:
                    continue
                path, _, index = node.get("name", "").rpartition("/")
                if index != f"{int(char):03}":
                    pool_failures.append(node_id)
                pools.setdefault(path, []).append(text_node)
        expected_base = policy["prefix"] + "/Num/Data"
        expected_paths = {expected_base} if len(pools) == 1 else {expected_base + "/Large", expected_base + "/Small"}
        if not pools:
            pool_unknown.append("未找到数据数字母件")
        elif set(pools) != expected_paths:
            pool_failures.extend(pools)
        sizes = {}
        for path, texts in pools.items():
            if sorted(t["characters"] for t in texts) != list("0123456789"):
                pool_failures.append(path)
            styles = set()
            for t in texts:
                if not all(k in t for k in ("font_name", "font_size", "fills", "strokes")) or not is_finite_number(t.get("font_size")):
                    pool_unknown.append(t["id"])
                    continue
                styles.add(json.dumps([t["font_name"], t["font_size"], t["fills"], t["strokes"], t.get("stroke_weight")], sort_keys=True))
                sizes[path] = t["font_size"]
            if len(styles) > 1:
                pool_failures.append(path)
        if set(sizes) == {expected_base + "/Large", expected_base + "/Small"} and sizes[expected_base + "/Large"] <= sizes[expected_base + "/Small"]:
            pool_failures.append("Large字号必须大于Small，同字号不能重复建立两套")
        for symbol in policy.get("symbol_resources", []):
            node_id = symbol.get("node_id")
            node = nodes_by_id.get(node_id)
            texts = [nodes_by_id[i] for i in descendants(nodes_by_id, node_id) if nodes_by_id[i].get("type") == "TEXT"] if node else []
            path = symbol.get("path", "")
            family_name = path.rpartition("/")[0]
            family = next((f for f in snapshot.get("resource_families", []) if f.get("name") == family_name), {})
            if not node or node.get("name") != path or len(texts) != 1 or texts[0].get("characters") != symbol.get("character") or family_name not in pools or node_id not in family.get("node_ids", []):
                pool_failures.append(node_id or path)
    pool_status = "fail" if pool_failures else ("unverified" if pool_unknown else "pass")
    checks.append(check_item("snapshot.shared_data", pool_status, "共享数据字模的命名、0–9序列或样式不符合规则" if pool_failures else ("共享字模原始证据不足" if pool_unknown else "共享字模命名、0–9完整性及大小字号关系通过"), pool_failures + pool_unknown))

    role_failures = []
    role_unverified = []
    role_locations = []
    role_geometries = {}
    role_values = {}
    roles = snapshot.get("roles", {})
    for role_name, owner_id in (("time_main_id", config["active_id"]), ("time_aod_id", config["aod_id"])):
        role_node = nodes_by_id.get(roles.get(role_name)) if isinstance(roles, dict) else None
        owner = nodes_by_id.get(owner_id)
        role_origin, owner_origin = node_origin(role_node or {}), node_origin(owner or {})
        if not role_node or role_origin is None or owner_origin is None or not all(is_finite_number(role_node.get(key)) for key in ("width", "height")):
            role_unverified.append(role_name)
            continue
        if not has_translation_only_transform(role_node) or not has_translation_only_transform(owner):
            role_unverified.append(role_name)
            continue
        local = (role_origin[0] - owner_origin[0], role_origin[1] - owner_origin[1])
        role_geometries[role_name] = tuple(round(value, 3) for value in (local[0], local[1], role_node["width"], role_node["height"]))
        role_locations.append(f"{role_name}:{local[0]},{local[1]},{role_node['width']}x{role_node['height']}")
        role_text = [nodes_by_id[node_id] for node_id in descendants(nodes_by_id, role_node["id"]) if node_id != role_node["id"] and "characters" in nodes_by_id[node_id] and effectively_visible(nodes_by_id[node_id], nodes_by_id, role_node["id"])]
        order_key = role_name.replace("_id", "_order")
        if order_key in roles:
            order = roles[order_key]
            actual_ids = {n["id"] for n in role_text}
            if not isinstance(order, list) or any(not isinstance(i, str) for i in order) or len(set(order)) != len(order) or set(order) != actual_ids:
                role_failures.append(order_key+":顺序必须完整覆盖实际可见文字")
            else:
                role_text = [nodes_by_id[i] for i in order]
        elif len({round((node_origin(n) or (0, 0))[1], 1) for n in role_text}) > 1:
            role_unverified.append(role_name+":多行时间缺少明确字段顺序")
        else:
            role_text.sort(key=lambda item: (node_origin(item) or (math.inf, math.inf))[0])
        role_values[role_name] = "".join(str(item.get("characters", "")) for item in role_text)
        if role_node["id"] not in descendants(nodes_by_id, owner_id) or local[0] < 0 or local[1] < 0 or local[0] + role_node["width"] > owner.get("width", 0) or local[1] + role_node["height"] > owner.get("height", 0):
            role_failures.append(role_node["id"])
    if roles and len(role_values) == 2 and (not all(role_values.values()) or role_values["time_main_id"] != role_values["time_aod_id"]):
        role_failures.append(f"时间值不一致：{role_values}")
    if len(role_geometries) == 2 and role_geometries["time_main_id"] != role_geometries["time_aod_id"]:
        role_failures.append(f"未同位同尺寸：{role_geometries}")
    role_status = "unverified" if not roles or role_unverified else ("fail" if role_failures else "pass")
    role_error_text = "；".join(str(item) for item in role_failures)
    checks.append(check_item("snapshot.time_roles", role_status, "缺少主表盘/AOD 时间角色映射或几何，或变换不是纯平移" if role_status == "unverified" else (("时间角色几何或实际值不通过：" + role_error_text) if role_failures else "时间角色在 Active/AOD 中同位同尺寸，实际时间值一致"), role_failures + role_unverified + role_locations))

    # 样式取自母件和 AOD 中实际显示的文字，不能只信资源名称。
    style = snapshot.get("aod_style", {})
    prefix = style.get("node_name_prefix")
    color = style.get("stroke_color", "")
    style_failures, style_unknown = [], []
    masters = [node for node in board_components if isinstance(prefix, str) and prefix and node.get("name", "").startswith(prefix)]
    if isinstance(snapshot.get("time_asset_pairs"), list):
        dark_ids = {e.get("aod_id") for e in snapshot["time_asset_pairs"] if isinstance(e, dict)}
        masters = [node for node in board_components if node["id"] in dark_ids]
    aod_time = nodes_by_id.get(roles.get("time_aod_id")) if isinstance(roles, dict) else None
    targets = set()
    for master in masters:
        targets.update(descendants(nodes_by_id, master["id"]))
    display_ids = descendants(nodes_by_id, aod_time["id"]) if aod_time else set()
    targets.update(display_ids)
    texts = [node for node in nodes if node["id"] in targets and node.get("type") == "TEXT" and effectively_visible(node, nodes_by_id, config["section_id"])]
    if not masters or not any(node["id"] in display_ids for node in texts) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        style_unknown.append("缺少描边样式要求、母件或实际显示文字")
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        expected_rgb = {key: int(color[1 + i * 2:3 + i * 2], 16) for i, key in enumerate("rgb")}
        for node in texts:
            if not isinstance(node.get("fills"), list) or not isinstance(node.get("strokes"), list) or not is_finite_number(node.get("stroke_weight")):
                style_unknown.append(node["id"])
                continue
            fills = [paint for paint in node["fills"] if paint.get("visible", True) and paint.get("opacity", 1) > 0]
            strokes = [paint for paint in node["strokes"] if paint.get("visible", True) and paint.get("opacity", 1) > 0]
            valid_stroke = len(strokes) == 1 and strokes[0].get("type") == "SOLID" and strokes[0].get("opacity", 1) == 1
            if valid_stroke:
                rgb = strokes[0].get("color", {})
                valid_stroke = all(is_finite_number(rgb.get(key)) and round(rgb[key] * 255) == value for key, value in expected_rgb.items())
            if fills or not valid_stroke or node["stroke_weight"] < 2:
                style_failures.append(node["id"])
    style_status = "fail" if style_failures else ("unverified" if style_unknown else "pass")
    checks.append(check_item("snapshot.aod_style", style_status, "AOD 数字时间描边须至少 2 px、无填充且颜色符合要求" if style_failures else ("缺少 AOD 原始样式证据" if style_unknown else f"AOD 母件与实际显示文字均无填充，描边为 {color} 且至少 2 px"), style_failures + style_unknown))

    animation_failures = []
    animations = snapshot.get("animations", [])
    for animation in animations:
        ids = animation.get("node_ids", [])
        members = [nodes_by_id[node_id] for node_id in ids if node_id in nodes_by_id]
        indices = []
        for node in members:
            match = re.search(r"(\d+)$", node.get("name", ""))
            indices.append(int(match.group(1)) if match else None)
        expected = animation.get("expected_count")
        start = animation.get("start_index")
        expected_indices = list(range(start, start + expected)) if is_integer(start) and is_integer(expected) else []
        sizes = {(node.get("width"), node.get("height")) for node in members}
        if len(members) != expected or indices != expected_indices or len(sizes) != 1 or any(not png_export(node) for node in members):
            animation_failures.extend(ids or ["未提供帧"])
    animation_status = "not_applicable" if not animations else ("fail" if animation_failures else "pass")
    checks.append(check_item("snapshot.animations", animation_status, "未声明动画资源" if animation_status == "not_applicable" else ("动画帧缺失、错序、尺寸不一或缺导出设置" if animation_failures else "动画原始帧数量、序列、尺寸和导出设置通过"), animation_failures))
    if snapshot.get("pointers") or snapshot.get("face_type") == "analog":
        pointers = snapshot.get("pointers", [])
        errors, measured = validate_pointers(snapshot, pointers, canvas=(spec["width"], spec["height"]), safe_margin=spec["safe_margin"], shape=spec["shape"], corner_radius=spec["corner_radius"])
        unknown = [p['id'] for p in measured if p['sweep_status'] == 'unverified']
        status = "fail" if errors else ("unverified" if not pointers or unknown else "pass")
        checks.append(check_item("snapshot.pointers", status, "核对实际指针母件、旋转矩阵、轴心、全周安全区及遮挡顺序；旋转平移不强制取整", errors+unknown))
    if snapshot.get("face_type") == "analog" and not has_digital_time(snapshot):
        checks = [c for c in checks if c['id'] not in {"snapshot.time_roles", "snapshot.aod_style"}]
    if snapshot.get("face_type") == "analog" or any(p.get("role", "").endswith("Dark") for p in snapshot.get("pointers", [])):
        errors, unknown = validate_aod_pointers(snapshot, snapshot.get("pointers", []))
        checks.append(check_item("snapshot.analog_aod", "fail" if errors else ("unverified" if unknown else "pass"), "亮屏与AOD指针同轴、同角、同尺寸；AOD母件及可见展示实例为至少2px的#B3B3B3描边", errors+unknown))
    checks.extend(validate_delivery_guards(snapshot, capability_modes))
    return checks


def validate_pngs(snapshot: dict, exports_dir: Path) -> list[dict]:
    try:
        from PIL import Image
    except ImportError:
        return [check_item("exports.png", "unverified", "未安装 Pillow，无法读取真实 PNG；可用 uv run --with pillow 执行")]
    nodes = snapshot.get("nodes", [])
    nodes_by_id = {node.get("id"): node for node in nodes if isinstance(node, dict) and node.get("id")}
    export_items = snapshot.get("export_items", [])
    text_asset_ids = {node_id for family in snapshot.get("resource_families", []) for node_id in family.get("node_ids", [])}
    failures = []
    images = {}
    try:
        spec = screen_spec(snapshot.get('config', {}).get('canvas'))
    except ValueError as exc:
        return [check_item('exports.target_geometry', 'fail', str(exc))]
    for item in export_items:
        node = nodes_by_id.get(item.get("node_id"))
        try:
            path = safe_path(exports_dir, item.get("path", ""))
        except (TypeError, ValueError) as exc:
            failures.append(f"导出路径无效：{item.get('path')} ({exc})")
            continue
        if node is None:
            failures.append(f"正式导出项节点不存在：{item.get('node_id')}")
            continue
        if not path.is_file():
            failures.append(f"缺少 PNG：{path}")
            continue
        try:
            with Image.open(path) as source:
                image = source.convert("RGBA")
            images[node.get("id")] = image
            settings = [value for value in node.get("export_settings", []) if value.get("format") == "PNG"]
            setting = item.get("setting", settings[0] if len(settings) == 1 else None)
            if setting not in settings:
                failures.append(f"必须明确实际使用的PNG导出设置：{node['id']}")
                continue
            constraint = setting.get("constraint", {})
            kind, value = constraint.get("type", "SCALE"), constraint.get("value", 1)
            if kind == "SCALE":
                expected = (round(node.get("width", 0) * value), round(node.get("height", 0) * value))
            elif kind == "WIDTH":
                expected = (round(value), round(node.get("height", 0) * value / node.get("width", 1)))
            elif kind == "HEIGHT":
                expected = (round(node.get("width", 0) * value / node.get("height", 1)), round(value))
            else:
                failures.append(f"无法识别 PNG 导出约束：{node['id']}")
                continue
            if image.size != expected:
                failures.append(f"PNG 尺寸不匹配：{path} 实际 {image.size}，导出设置推导 {expected}")
            if node["id"] in text_asset_ids:
                alpha_bbox = image.getchannel("A").getbbox()
                if alpha_bbox is None:
                    failures.append(f"PNG alpha bounds 为空：{path}")
                # 抗锯齿可触及边缘像素；是否切边由原始完整字形范围核对，不能强制留一整行透明像素。
        except Exception as exc:
            failures.append(f"PNG 无法读取：{path} ({exc})")
    if 'canvas' in snapshot.get('config', {}):
        config = snapshot['config']
        for item in export_items:
            image = images.get(item.get('node_id'))
            if image is None:
                continue
            n = nodes_by_id[item['node_id']]
            setting = item.get('setting', n.get('export_settings', [{}])[0])
            expected_scale = 5 if item.get('role') == 'presentation_main' else 1
            if setting.get('constraint') != {'type':'SCALE','value':expected_scale}:
                failures.append(f"目标导出倍率错误：{item['path']}")
            if item.get('role') == 'aod_full' or item['node_id'] == config.get('background_id'):
                if image.size != (spec['width'],spec['height']):
                    failures.append(f"全屏资源与目标尺寸不符：{item['path']}")
            if item['node_id'] == config.get('preview_id'):
                if list(image.size) != spec['preview']:
                    failures.append(f"缩略图尺寸错误：{item['path']}")
                else:
                    fit = fit_preview(spec)
                    bad = False
                    for y in range(image.height):
                        for x in range(image.width):
                            inside = contains(x+.5-fit['x'], y+.5-fit['y'], fit['width'],fit['height'],spec['shape'],spec['corner_radius']*fit['scale'], margin=-1)
                            rgba = image.getpixel((x,y))
                            if rgba[3] != 255 or (not inside and rgba[:3] != (0,0,0)):
                                bad = True; break
                        if bad: break
                    if bad: failures.append(f"缩略图屏形外未保持不透明黑底：{item['path']}")
    checks = [check_item("exports.png", "fail" if failures else "pass", "；".join(failures) if failures else "所有显式正式导出 PNG 存在、尺寸正确且文字非空；切边另由原始字形边界检查", failures)]
    aod_items = [item for item in export_items if item.get("role") == "aod_full"]
    if len(aod_items) != 1:
        checks.append(check_item("exports.aod_brightness", "unverified", "未唯一指定 role=aod_full 的全屏 AOD 正式导出"))
    else:
        item = aod_items[0]
        node = nodes_by_id.get(item.get("node_id"))
        image = images.get(item.get("node_id"))
        if node is None or image is None or image.size[0] == 0 or image.size[1] == 0:
            checks.append(check_item("exports.aod_brightness", "unverified", "AOD 全屏 PNG 缺失或不可读，无法计算亮像素比例", [item.get("node_id")]))
        else:
            width, height = image.size
            shape = spec["shape"]
            radius = spec["corner_radius"] * width / spec["width"]
            bright = total = 0
            for y in range(height):
                for x in range(width):
                    if contains(x+.5, y+.5, width, height, shape, radius):
                        total += 1
                        red, green, blue, alpha = image.getpixel((x, y))
                        composited = tuple((channel * alpha + 127) // 255 for channel in (red, green, blue))
                        if any(channel > 0 for channel in composited):
                            bright += 1
            ratio = bright / total if total else 0
            checks.append(check_item("exports.aod_brightness", "fail" if ratio > .10 else "pass", f"AOD 实际屏幕可见区域内合成黑底后 RGB>0 的亮像素比例 {ratio:.4%}，阈值 10%", [node["id"]]))
    if snapshot.get('config', {}).get('background_id'):
        from background_edges import analyze_background, coverage_status
        background, _ = analyze_background(snapshot, exports_dir)
        checks.append(check_item('exports.background_coverage', coverage_status(background),
            '背景有效区域/透明覆盖检查（不代表边缘视觉通过）：' + str(background['auto_checks'])))
    return checks


def manual_checks() -> list[dict]:
    return [
        check_item("manual.visual", "unverified", "人工视觉一致性必须独立确认"),
        check_item("manual.background_semantics", "unverified", "背景合并与语义必须独立确认"),
        check_item("manual.user_confirmation", "unverified", "尚无用户验收确认证据"),
        check_item("manual.device", "unverified", "真机显示、动画范围、播放与运行内存必须独立验证"),
    ]


def _contains_similarity_score(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            key in {"similarity_score", "similarity_percent", "similarity_percentage"}
            or _contains_similarity_score(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_similarity_score(child) for child in value)
    return False


def _visual_artifact(
    review_root: Path,
    value: object,
    artifact_id: str,
    errors: list[str],
    registry: dict[str, tuple[int, int]],
    expected_size: tuple[int, int],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{artifact_id} 证据必须是对象")
        return
    relative = value.get("path")
    digest = value.get("sha256")
    if not isinstance(relative, str) or not relative.strip():
        errors.append(f"{artifact_id} 缺少路径")
        return
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        errors.append(f"{artifact_id} 缺少有效 SHA-256")
        return
    try:
        path = safe_path(review_root, relative)
    except ValueError as exc:
        errors.append(f"{artifact_id}：{exc}")
        return
    if not path.is_file():
        errors.append(f"{artifact_id} 文件不存在：{relative}")
        return
    if file_sha256(path).lower() != digest.lower():
        errors.append(f"{artifact_id} 文件哈希不匹配：{relative}")
        return
    if artifact_id in registry:
        errors.append(f"视觉证据 ID 重复：{artifact_id}")
        return
    try:
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("不是 PNG")
            size = image.size
    except (ImportError, OSError, ValueError) as exc:
        errors.append(f"{artifact_id} 不是可解码的 PNG：{relative} ({exc})")
        return
    if size != expected_size:
        errors.append(f"{artifact_id} 图片尺寸应为 {expected_size[0]}×{expected_size[1]}，实际为 {size[0]}×{size[1]}")
        return
    registry[artifact_id] = size


def validate_workflow_binding(
    manifest: dict,
    contract_path: Path | None,
    visual_review_path: Path | None,
    snapshot_path: Path | None,
    exports_dir: Path | None = None,
    snapshot: dict | None = None,
    *, required_gate: str = "visual_preflight",
) -> list[dict]:
    if contract_path is None:
        return [
            check_item(
                "workflow.contract",
                "unverified",
                "旧项目未提供 workflow-contract.json；自动检查保持兼容，但不能取得新任务正式交付通过状态",
            ),
            check_item(
                "workflow.visual_review",
                "unverified",
                "未提供工作流合同，无法绑定视觉预检证据",
            ),
        ]

    contract_errors: list[str] = []
    if required_gate != "reference" and isinstance(snapshot, dict) and snapshot.get("config", {}).get("stage") == "prototype":
        contract_errors.append("样稿快照不能用于正式视觉封存或生产验证")
    contract = None
    try:
        contract = load_contract(contract_path)
        contract_errors.extend(verified_gate_failures(contract_path, contract, required_gate))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        contract_errors.append(str(exc))

    if contract is not None:
        target = contract["target"]
        canvas = manifest.get("canvas") if isinstance(manifest.get("canvas"), dict) else {}
        if target.get("width") != canvas.get("width") or target.get("height") != canvas.get("height"):
            contract_errors.append("合同目标尺寸与清单 canvas 不一致")
        manifest_shape = canvas.get("shape", "circle")
        if ("rounded_rect" if target.get("shape") == "round_rect" else target.get("shape")) != manifest_shape:
            contract_errors.append("合同屏形与清单 canvas 不一致")
        source = manifest.get("source_strategy")
        if not isinstance(source, dict):
            contract_errors.append("清单缺少 source_strategy，无法核对素材来源")
        else:
            if source.get("mode") != contract["source_strategy"].get("mode"):
                contract_errors.append("合同与清单的素材来源策略不一致")
            if source.get("reference_image_hashes") != contract["reference_contract"].get("reference_hashes"):
                contract_errors.append("合同与清单的参考图哈希不一致")

    contract_check = check_item(
        "workflow.contract",
        "fail" if contract_errors else "pass",
        "；".join(contract_errors) if contract_errors else "工作流合同结构、阶段顺序、目标和来源绑定通过",
        contract_errors,
    )
    if contract is None:
        return [
            contract_check,
            check_item("workflow.visual_review", "fail", "合同无效，无法验证视觉预检"),
        ]

    visual_errors: list[str] = []
    if visual_review_path is None:
        visual_errors.append("新工作流缺少 visual-review.json")
        review = None
    else:
        try:
            review = json.loads(visual_review_path.read_text(encoding="utf-8"))
            if not isinstance(review, dict):
                raise ValueError("视觉预检根节点必须是对象")
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            visual_errors.append(f"视觉预检无法读取：{exc}")
            review = None

    if review is not None:
        if review.get("schema_version") != 1:
            visual_errors.append("visual-review.schema_version 必须为 1")
        if _contains_similarity_score(review):
            visual_errors.append("视觉预检不得使用自拟相似百分比或相似分数")
        if review.get("workflow_contract_sha256") != file_sha256(contract_path):
            visual_errors.append("视觉预检绑定的合同哈希不是当前合同")
        if snapshot_path is None or not snapshot_path.is_file():
            visual_errors.append("缺少当前 Figma 快照，无法绑定视觉预检")
        elif review.get("snapshot_sha256") != file_sha256(snapshot_path):
            visual_errors.append("视觉预检绑定的快照哈希不是当前快照")
        expected_references = contract["reference_contract"]["reference_hashes"]
        if review.get("reference_hashes") != expected_references:
            visual_errors.append("视觉预检参考图哈希与合同不一致")

        manifest_source = manifest.get("source_strategy", {})
        expected_sources = {
            "reference": manifest_source.get("reference_image_hashes", []),
            "adopted": manifest_source.get("adopted_image_hashes", []),
            "reused": manifest_source.get("reused_image_hashes", []),
        }
        if review.get("source_hashes") != expected_sources:
            visual_errors.append("视觉预检素材来源哈希与清单不一致")

        implementation_path = None
        implementation = review.get("implementation_export")
        if not isinstance(implementation, dict):
            visual_errors.append("视觉预检缺少当前实现图导出绑定")
        elif exports_dir is None or not isinstance(snapshot, dict):
            visual_errors.append("缺少当前导出目录或快照，无法核对实现图")
        else:
            node_id = implementation.get("node_id")
            relative = implementation.get("path")
            digest = implementation.get("sha256")
            export_item = next(
                (
                    item for item in snapshot.get("export_items", [])
                    if isinstance(item, dict)
                    and item.get("node_id") == node_id
                    and item.get("path") == relative
                    and item.get("role") == "presentation_main"
                ),
                None,
            )
            if export_item is None:
                visual_errors.append("实现图未绑定当前快照的 presentation_main 导出项")
            elif not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
                visual_errors.append("实现图缺少有效 SHA-256")
            else:
                try:
                    implementation_path = safe_path(exports_dir, relative)
                    if not implementation_path.is_file() or file_sha256(implementation_path) != digest:
                        visual_errors.append("实现图文件缺失或哈希与当前导出不一致")
                        implementation_path = None
                    if review.get("implementation_sha256") != digest:
                        visual_errors.append("实现图摘要与导出绑定不一致")
                except (OSError, ValueError) as exc:
                    visual_errors.append(f"实现图导出路径无效：{exc}")

        artifacts = review.get("artifacts")
        registry: dict[str, tuple[int, int]] = {}
        valid_crops = []
        target_width = contract["target"]["width"]
        target_height = contract["target"]["height"]
        if not isinstance(artifacts, dict):
            visual_errors.append("visual-review.artifacts 必须是对象")
        else:
            _visual_artifact(
                visual_review_path.parent, artifacts.get("side_by_side"), "side_by_side",
                visual_errors, registry, (target_width * 2, target_height),
            )
            _visual_artifact(
                visual_review_path.parent, artifacts.get("overlay"), "overlay",
                visual_errors, registry, (target_width, target_height),
            )
            crops = artifacts.get("crops")
            if not isinstance(crops, list) or not crops:
                visual_errors.append("视觉预检必须至少包含一个关键区域局部图")
            else:
                for index, crop in enumerate(crops):
                    crop_id = crop.get("id") if isinstance(crop, dict) else None
                    if not isinstance(crop_id, str) or not crop_id.strip():
                        visual_errors.append(f"crops[{index}] 缺少唯一 id")
                        continue
                    if crop_id in {"side_by_side", "overlay"} or crop_id in registry:
                        visual_errors.append(f"视觉证据 ID 重复或使用保留名称：{crop_id}")
                        continue
                    bounds = crop.get("bounds")
                    if (
                        not isinstance(bounds, list) or len(bounds) != 4
                        or any(not isinstance(value, int) or isinstance(value, bool) for value in bounds)
                    ):
                        visual_errors.append(f"crops[{index}] 缺少整数 bounds")
                        continue
                    x, y, width, height = bounds
                    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > target_width or y + height > target_height:
                        visual_errors.append(f"crops[{index}] bounds 超出目标画布")
                        continue
                    _visual_artifact(
                        visual_review_path.parent, crop, crop_id, visual_errors,
                        registry, (width * 2, height),
                    )
                    if crop_id in registry:
                        valid_crops.append(crop)

        reference_input = review.get("reference_input")
        try:
            if not isinstance(reference_input, dict):
                raise ValueError("缺少原始参考图输入记录")
            digest = reference_input.get("sha256")
            if not isinstance(digest, str) or digest not in expected_references:
                raise ValueError("原始参考图哈希未登记在合同中")
            reference_path = safe_path(visual_review_path.parent, reference_input.get("path"))
            if file_sha256(reference_path) != digest:
                raise ValueError("原始参考图文件哈希不一致")
            if implementation_path is not None:
                from PIL import Image
                crop_boxes = []
                descriptors = {"side_by_side": artifacts.get("side_by_side"), "overlay": artifacts.get("overlay")} if isinstance(artifacts, dict) else {}
                for crop in valid_crops:
                    x, y, width, height = crop["bounds"]
                    crop_boxes.append((crop["id"], (x, y, x + width, y + height)))
                    descriptors[crop["id"]] = crop
                with Image.open(reference_path) as reference_image, Image.open(implementation_path) as implementation_image:
                    expected_images = comparison_images(reference_image, implementation_image, (target_width, target_height), crop_boxes)
                for artifact_id in registry:
                    path = safe_path(visual_review_path.parent, descriptors[artifact_id]["path"])
                    with Image.open(path) as actual:
                        if actual.convert("RGBA").tobytes() != expected_images[artifact_id].tobytes():
                            visual_errors.append(f"{artifact_id} 不是由当前参考图和实现图生成的视觉证据")
        except (ImportError, OSError, ValueError, TypeError) as exc:
            visual_errors.append(f"视觉证据输入或派生关系无效：{exc}")

        checks = review.get("checks")
        checked: set[str] = set()
        if not isinstance(checks, list):
            visual_errors.append("visual-review.checks 必须是数组")
        else:
            for index, item in enumerate(checks):
                if not isinstance(item, dict):
                    visual_errors.append(f"checks[{index}] 必须是对象")
                    continue
                invariant = item.get("invariant")
                if not isinstance(invariant, str) or not invariant.strip() or invariant in checked:
                    visual_errors.append(f"checks[{index}].invariant 缺失或重复")
                    continue
                checked.add(invariant)
                if not isinstance(item.get("status"), str) or item.get("status") not in {"pass", "fail", "unverified"}:
                    visual_errors.append(f"{invariant} 的视觉结论状态无效")
                elif item.get("status") != "pass":
                    visual_errors.append(f"{invariant} 的视觉结论仍为 {item.get('status')}")
                evidence = item.get("evidence")
                if not isinstance(evidence, list) or not evidence or any(not isinstance(value, str) for value in evidence):
                    visual_errors.append(f"{invariant} 缺少视觉证据引用")
                else:
                    missing = [value for value in evidence if value not in registry]
                    if missing:
                        visual_errors.append(f"{invariant} 引用了不存在的视觉证据：{', '.join(map(str, missing))}")
            missing_invariants = [
                value for value in contract["reference_contract"]["invariants"] if value not in checked
            ]
            if missing_invariants:
                visual_errors.append("视觉预检遗漏合同不变量：" + "、".join(missing_invariants))

    if isinstance(snapshot, dict) and snapshot.get('config', {}).get('background_id'):
        if review is None or exports_dir is None or visual_review_path is None:
            visual_errors.append('缺少背景边缘复核输入')
        else:
            from background_edges import validate_edge_review
            source_hashes = [value for values in expected_sources.values() for value in values]
            try:
                visual_errors.extend(validate_edge_review(snapshot, exports_dir,
                    review.get('background_edge_review'), visual_review_path.parent, source_hashes))
            except ImportError:
                visual_errors.append('缺少Pillow，背景边缘未验证')

    visual_check = check_item(
        "workflow.visual_review",
        "fail" if visual_errors else "pass",
        "；".join(visual_errors) if visual_errors else "同尺寸并排图、叠图、局部图及逐项视觉结论与当前合同和快照绑定",
        visual_errors,
    )
    return [contract_check, visual_check]


def production_validation_check(checks: list[dict], missing: list[str]) -> dict:
    applicable = [
        item for item in checks
        if not item.get("id", "").startswith("manual.")
        and item.get("id") != "workflow.production_validation"
    ]
    incomplete = [
        item.get("id", "未知检查")
        for item in applicable
        if item.get("status") not in {"pass", "not_applicable"}
    ]
    details = []
    if missing:
        details.append("缺少 " + "、".join(missing))
    if incomplete:
        details.append("未通过 " + "、".join(sorted(incomplete)))
    return check_item(
        "workflow.production_validation",
        "fail" if details else "pass",
        "；".join(details) if details else "全部适用的自动检查、合同、快照、视觉预检、正式PNG和本轮导出绑定均通过",
        missing + incomplete,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="校验表盘清单与原始证据")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--exports-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence", type=Path, help="本轮来源和实际导出文件的绑定记录")
    parser.add_argument("--workflow-contract", type=Path, help="本项目工作流合同")
    parser.add_argument("--visual-review", type=Path, help="与当前合同及快照绑定的视觉预检记录")
    args = parser.parse_args()
    path = args.manifest
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"清单文件不存在：{path}")
        return 2
    except json.JSONDecodeError as exc:
        print(f"清单不是有效 JSON：{exc}")
        return 2

    if not isinstance(data, dict):
        print("清单根节点必须是 JSON 对象")
        return 2

    errors, resolved = validate_manifest(data)
    checks = [check_item("manifest", "fail" if errors else "pass", "；".join(errors) if errors else "清单自报字段约束通过", [str(path)])]
    snapshot = None
    if args.snapshot:
        try:
            snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
            if not isinstance(snapshot, dict):
                raise ValueError("根节点必须是 JSON 对象")
            checks.extend(validate_snapshot(snapshot, data.get("capability_modes")))
            checks.extend(validate_target_binding(data, snapshot))
            checks.extend(validate_capability_binding(data, snapshot))
            checks.append(check_item("snapshot.stability", "pass" if stable_snapshot(snapshot) else "unverified", "核对采集前后内容与引用签名；旧快照不证明稳定"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            checks.append(check_item("snapshot.structure", "fail", f"快照无法读取：{exc}", [str(args.snapshot)]))
    else:
        checks.append(check_item("snapshot.structure", "unverified", "未提供 Figma 快照，相关原始节点检查未验证"))
    pagination = snapshot.get("pagination", {}) if snapshot else {}
    snapshot_complete = pagination.get("complete") is True and pagination.get("total_nodes") == pagination.get("collected_nodes") == len(snapshot.get("nodes", [])) if snapshot else False
    if args.exports_dir and snapshot is not None and snapshot_complete:
        checks.extend(validate_pngs(snapshot, args.exports_dir))
    elif args.exports_dir and snapshot is not None:
        checks.append(check_item("exports.png", "unverified", "快照分页未完整，不能确认正式 PNG 映射"))
    elif args.exports_dir:
        checks.append(check_item("exports.png", "unverified", "缺少可用快照，无法将 PNG 与节点对应"))
    else:
        checks.append(check_item("exports.png", "unverified", "未提供导出目录，真实 PNG 检查未验证"))
    if args.evidence and snapshot is not None and args.exports_dir:
        try:
            evidence = json.loads(args.evidence.read_text())
            binding_errors = validate_run(evidence, data, snapshot, args.exports_dir)
            checks.append(check_item("exports.binding", "fail" if binding_errors else "pass", "；".join(binding_errors) if binding_errors else "需求、范围、快照、导出设置和PNG哈希绑定一致"))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            checks.append(check_item("exports.binding", "fail", f"本轮证据无法校验：{exc}"))
    else:
        checks.append(check_item("exports.binding", "unverified", "未同时提供本轮证据、快照和PNG目录，不能证明文件属于当前设计"))
    checks.extend(validate_workflow_binding(
        data, args.workflow_contract, args.visual_review, args.snapshot,
        args.exports_dir, snapshot,
    ))
    if args.workflow_contract:
        required = {
            "snapshot": args.snapshot,
            "exports-dir": args.exports_dir,
            "evidence": args.evidence,
            "visual-review": args.visual_review,
        }
        missing = [name for name, value in required.items() if value is None]
        checks.append(production_validation_check(checks, missing))
    else:
        checks.append(check_item(
            "workflow.production_validation",
            "unverified",
            "旧项目未提供工作流合同，不能取得新任务正式生产校验通过状态",
        ))
    checks.extend(manual_checks())
    production_check = next(item for item in checks if item["id"] == "workflow.production_validation")
    contract_check = next(item for item in checks if item["id"] == "workflow.contract")
    draft_failures = [
        item for item in checks
        if item["status"] == "fail"
        and not item["id"].startswith("exports.")
        and item["id"] != "workflow.production_validation"
    ]
    delivery = {
        "draft_review": "fail" if draft_failures else ("pass" if contract_check["status"] == "pass" else contract_check["status"]),
        "production_validation": production_check["status"],
        "user_acceptance": "unverified",
        "firmware_validation": "unverified",
    }
    if args.report:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "summary": dict(Counter(item["status"] for item in checks)),
            "delivery": delivery,
            "inputs": {name: file_sha256(value) for name, value in {
                "manifest": path, "snapshot": args.snapshot, "evidence": args.evidence,
                "workflow_contract": args.workflow_contract, "visual_review": args.visual_review,
            }.items() if value is not None and value.is_file()},
            "exit_code_meaning": "0 仅表示已执行的自动检查无失败，不代表完整验收",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [item for item in checks if item["status"] == "fail"]
    if failed:
        print("检查失败：")
        for item in failed:
            print(f"- {item['explanation']}")
        return 1

    print("清单检查通过")
    print("语言：" + ", ".join(resolved["languages"]))
    print("主题：" + ", ".join(resolved["themes"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
