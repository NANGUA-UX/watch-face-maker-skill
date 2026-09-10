#!/usr/bin/env python3
"""检查表盘交付清单中的关键数量、语言、AOD和导出约束。"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import re
import sys
from pathlib import Path
from naming import validate_names
from run_evidence import stable_snapshot, validate_run
from pointer_checks import validate_pointers, validate_aod_pointers


STANDARD_STATE_COUNTS = {"weather": 48, "battery": 11, "bluetooth": 2}
STATE_CAPABILITY_TO_FAMILY = {
    "weather": "weather",
    "battery": "battery",
    "bluetooth": "bluetooth",
    "pace_progress": "pace_progress",
    "calories_progress": "calories_progress",
    "distance_progress": "distance_progress",
}


def is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_manifest(data: dict) -> tuple[list[str], dict]:
    errors: list[str] = []

    prefix = data.get("prefix")
    if not isinstance(prefix, str) or not prefix.strip():
        errors.append("prefix 必须是非空字符串")
    elif not re.fullmatch(r"[A-Za-z0-9]+(?: [A-Za-z0-9]+)*", prefix):
        errors.append("prefix 仅支持英文、数字及单个内部空格")
    if "name_zh" in data or "name_en" in data:
        errors.extend(validate_names(data.get("name_zh"), data.get("name_en")))
        if prefix != data.get("name_en"):
            errors.append("prefix 必须等于完整 name_en，保留空格")

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
    if (
        not is_integer(canvas.get("width"))
        or not is_integer(canvas.get("height"))
        or canvas.get("width") != 480
        or canvas.get("height") != 480
    ):
        errors.append("当前规范要求画布尺寸为 480×480")
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
    if exports.get("preview") != [256, 256]:
        errors.append("缩略图尺寸必须为 256×256")

    states = data.get("states", {})
    if not isinstance(states, dict) or not states:
        errors.append("states 必须包含至少一个动态状态族")
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

        for capability, state_family in STATE_CAPABILITY_TO_FAMILY.items():
            if capability in requested and state_family not in states:
                errors.append(f"已请求 {capability}，但缺少 {state_family} 状态族")

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


def validate_digit_geometry(nodes_by_id: dict, all_text: bool = False) -> dict:
    failures, unknown, count = [], [], 0
    for node in nodes_by_id.values():
        source = text_source(node, nodes_by_id) if all_text else digit_source(node, nodes_by_id)
        if source is None:
            continue
        count += 1
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
        center_error = abs(margins[0] - margins[2]) if baseline_symbol else max(abs(margins[0] - margins[2]), abs(margins[1] - margins[3]))
        if min(bounds["width"], bounds["height"]) <= 0 or min(margins) < 0 or center_error > .102:
            failures.append(node["id"])
    status = "fail" if failures else ("unverified" if unknown or not count else "pass")
    label = "文字" if all_text else "数字"
    explanation = f"核对{count}个{label}文本：宽高偶数整数，X/Y最多一位小数，实际字形中心偏差不超过0.05px（另容许0.001px计算尾差）"
    if all_text:
        explanation += "；单字符标点仅自动检查水平居中，垂直基线须另行核对"
    return check_item("snapshot.text_geometry" if all_text else "snapshot.digit_geometry", status, explanation, sorted(set(failures + unknown)))


def validate_snapshot(snapshot: dict) -> list[dict]:
    checks: list[dict] = []
    config = snapshot.get("config") if isinstance(snapshot.get("config"), dict) else {}
    if config.get("profile") == "preflight" or snapshot.get("evidence_level") == "preflight":
        return [check_item("snapshot.preflight", "unverified", "轻量预检不能作为完整交付证据；设计稳定后采集正式快照")]
    nodes = snapshot.get("nodes") if isinstance(snapshot.get("nodes"), list) else []
    nodes_by_id = {node.get("id"): node for node in nodes if isinstance(node, dict) and node.get("id")}
    required = ("section_id", "asset_board_id", "main_id", "active_id", "aod_id", "preview_id")
    missing = [field for field in required if config.get(field) not in nodes_by_id]
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
            "snapshot.resource_families", "snapshot.shared_data", "snapshot.state_families", "snapshot.time_roles", "snapshot.aod_style", "snapshot.animations", "snapshot.integer_positions", "snapshot.digit_geometry", "snapshot.text_geometry", "snapshot.progress_tracks",
        ):
            checks.append(check_item(check_id, "unverified", "快照不完整，不能据此推定通过"))
        return checks
    if len(nodes_by_id) != len(nodes):
        checks.append(check_item("snapshot.pagination", "fail", "合并快照含重复节点 ID"))
        return checks
    checks.append(check_item("snapshot.pagination", "pass", "合并快照页数完整，节点数与唯一 ID 一致"))

    section_ids = descendants(nodes_by_id, config["section_id"])
    outside = [node["id"] for node in nodes if node.get("id") not in section_ids]
    checks.append(check_item("snapshot.section_scope", "fail" if outside else "pass", "发现指定 section 外节点" if outside else "所有节点都位于指定 section", outside))

    board_ids = descendants(nodes_by_id, config["asset_board_id"])
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
    board_components = [node for node in nodes if node.get("id") in board_ids and node.get("type") == "COMPONENT"]
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

    pointer_ids = {p.get("instance_id") for p in snapshot.get("pointers", [])}

    def check_position(node):
        if node["id"] in visited or node.get("visible") is False:
            return
        visited.add(node["id"])
        if not all(is_finite_number(node.get(k)) for k in ("x", "y")):
            position_unknown.append(node["id"])
        elif node["id"] not in pointer_ids:
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
    checks.append(validate_digit_geometry(nodes_by_id) if snapshot.get("digit_geometry") is True else check_item("snapshot.digit_geometry", "unverified", "未声明数字偶数尺寸与一位小数居中规则"))
    checks.append(validate_digit_geometry(nodes_by_id, all_text=True) if snapshot.get("text_geometry") is True else check_item("snapshot.text_geometry", "unverified", "未声明完整文字切图几何规则，不能用数字检查代替"))

    progress_failures, progress_unknown = [], []
    progress_resources = snapshot.get("progress_resources", [])
    for family in progress_resources:
        prefix, segments = family.get("prefix"), family.get("segment_count")
        members = [n for n in board_components if isinstance(prefix, str) and n.get("name", "").startswith(prefix)]
        if family.get("shape") not in {"regular", "irregular", "battery"} or not is_integer(segments) or segments < 1:
            progress_unknown.append(str(prefix))
            continue
        if family["shape"] in {"irregular", "battery"} and family.get("include_track") is not True:
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
    checks.append(check_item("snapshot.progress_tracks", "not_applicable" if pointer_progress else ("fail" if progress_failures else ("unverified" if progress_unknown else "pass")), "本次进度使用独立数据指针，转轴与范围由指针检查覆盖" if pointer_progress else "异形/电量进度各状态必须保留完整底；普通进度按拆分规则处理", [] if pointer_progress else progress_failures + progress_unknown))
    source_only_ids = set(snapshot.get("source_only_node_ids", []))
    invalid_source_only = sorted(source_only_ids - {node["id"] for node in board_components})
    asset_items = [item for item in export_items if isinstance(item, dict) and item.get("role") in {"asset", "aod_full", "presentation_main"}]
    asset_ids = {item.get("node_id") for item in asset_items}
    expected_asset_ids = {node["id"] for node in board_components} - source_only_ids
    uncovered = sorted(expected_asset_ids - asset_ids)
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
            expectation = snapshot.get("assembly_expectations", {}).get(container_field, {})
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
    text_component_ids = {node_id for family in snapshot.get("resource_families", []) if family.get("kind") in {"time", "data", "date", "unit", "symbol"} for node_id in family.get("node_ids", [])}
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
        common_height = rounded_multiple_of_four(max(item["glyph_bottom"] for item in family_measurements) - min(item["glyph_top"] for item in family_measurements) + 2 * vertical_padding)
        digit_measurements = [item for item in family_measurements if re.fullmatch(r"[0-9]", item["character"])]
        common_width = max((item["minimum_width"] for item in digit_measurements), default=0)
        size_failure = any(item["size"][1] != common_height for item in family_measurements)
        if family.get("kind") in {"data", "date"}:
            size_failure = size_failure or any(item["size"][0] != common_width for item in digit_measurements)
            size_failure = size_failure or any(item["size"][0] != item["minimum_width"] for item in family_measurements if item not in digit_measurements)
        else:
            size_failure = size_failure or any(item["size"][0] != item["minimum_width"] for item in family_measurements)
        baseline_groups = {}
        for item in family_measurements:
            # 数字以实际可见字形居中；7/9等高度不同的字形需要微调Y。
            # 不把文本框同Y误当作居中，符号与单位仍保留各自字体基线。
            if snapshot.get("digit_geometry") is True and re.fullmatch(r"[0-9]", item["character"]):
                continue
            baseline_key = (item["font"], item["character"] in "0123456789" and len(item["character"]) == 1) if snapshot.get("digit_geometry") is True else item["font"]
            baseline_groups.setdefault(baseline_key, set()).add(round(item["text_origin_y"], 3))
        if size_failure or any(len(values) > 1 for values in baseline_groups.values()):
            family_failures.append(family.get("name", "未命名资源族"))
    status = "fail" if family_failures else ("unverified" if not snapshot.get("resource_families") or family_unverified else "pass")
    checks.append(check_item("snapshot.resource_families", status, "资源族映射或字形测量不足" if status == "unverified" else ("同族宽高或基线规则不满足" if family_failures else "同族尺寸与基线规则满足；symbol 不纳入 0-9 统一宽度"), family_failures + family_unverified))

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
    aod_time = nodes_by_id.get(roles.get("time_aod_id")) if isinstance(roles, dict) else None
    targets = set()
    for master in masters:
        targets.update(descendants(nodes_by_id, master["id"]))
    display_ids = descendants(nodes_by_id, aod_time["id"]) if aod_time else set()
    targets.update(display_ids)
    texts = [node for node in nodes if node["id"] in targets and node.get("type") == "TEXT" and effectively_visible(node, nodes_by_id, config["section_id"])]
    if not masters or not any(node["id"] in display_ids for node in texts) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        style_unknown.append("缺少描边样式要求、母件或实际显示文字")
    else:
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
            if fills or not valid_stroke or node["stroke_weight"] <= 0:
                style_failures.append(node["id"])
    style_status = "fail" if style_failures else ("unverified" if style_unknown else "pass")
    checks.append(check_item("snapshot.aod_style", style_status, "AOD 描边、无填充或颜色不符合要求" if style_failures else ("缺少 AOD 原始样式证据" if style_unknown else f"AOD 母件与实际显示文字均无填充，描边为 {color}"), style_failures + style_unknown))

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
        errors, measured = validate_pointers(snapshot, pointers)
        unknown = [p['id'] for p in measured if p['sweep_status'] == 'unverified']
        status = "fail" if errors else ("unverified" if not pointers or unknown else "pass")
        checks.append(check_item("snapshot.pointers", status, "核对实际指针母件、旋转矩阵、轴心、全周安全区及遮挡顺序；旋转平移不强制取整", errors+unknown))
    if snapshot.get("face_type") == "analog":
        checks = [c for c in checks if c['id'] not in {"snapshot.time_roles", "snapshot.aod_style"}]
        errors, unknown = validate_aod_pointers(snapshot, snapshot.get("pointers", []))
        checks.append(check_item("snapshot.analog_aod", "fail" if errors else ("unverified" if unknown else "pass"), "亮屏与AOD时分针同轴、同角、同尺寸；AOD母件及可见展示实例为#B3B3B3描边", errors+unknown))
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
    for item in export_items:
        node = nodes_by_id.get(item.get("node_id"))
        path = exports_dir / item.get("path", "")
        if node is None:
            failures.append(f"正式导出项节点不存在：{item.get('node_id')}")
            continue
        if not path.is_file():
            failures.append(f"缺少 PNG：{path}")
            continue
        try:
            image = Image.open(path).convert("RGBA")
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
            radius = min(width, height) / 2
            center_x, center_y = width / 2, height / 2
            bright = total = 0
            for y in range(height):
                for x in range(width):
                    if (x + .5 - center_x) ** 2 + (y + .5 - center_y) ** 2 <= radius ** 2:
                        total += 1
                        red, green, blue, alpha = image.getpixel((x, y))
                        composited = tuple((channel * alpha + 127) // 255 for channel in (red, green, blue))
                        if any(channel > 0 for channel in composited):
                            bright += 1
            ratio = bright / total if total else 0
            checks.append(check_item("exports.aod_brightness", "fail" if ratio > .10 else "pass", f"AOD 圆屏内合成黑底后 RGB>0 的亮像素比例 {ratio:.4%}，阈值 10%", [node["id"]]))
    return checks


def manual_checks() -> list[dict]:
    return [
        check_item("manual.visual", "unverified", "人工视觉一致性必须独立确认"),
        check_item("manual.background_semantics", "unverified", "背景合并与语义必须独立确认"),
        check_item("manual.user_confirmation", "unverified", "尚无用户验收确认证据"),
        check_item("manual.device", "unverified", "真机显示、动画范围、播放与运行内存必须独立验证"),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="校验表盘清单与原始证据")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--exports-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--evidence", type=Path, help="本轮来源和实际导出文件的绑定记录")
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
            checks.extend(validate_snapshot(snapshot))
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
    checks.extend(manual_checks())
    if args.report:
        report = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
            "summary": dict(Counter(item["status"] for item in checks)),
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
