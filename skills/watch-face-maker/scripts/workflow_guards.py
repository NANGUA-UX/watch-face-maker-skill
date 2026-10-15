#!/usr/bin/env python3
"""只读校验表盘工作流合同和阶段门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from screen_geometry import screen_spec


GATE_ORDER = ("intake", "reference", "prototype", "assembly", "visual_preflight")
GATE_STATUSES = {"pending", "pass", "fail", "not_applicable"}
ROUTES = {"figma", "reference_image_edit", "approved_asset"}
TASK_MODES = {"replicate", "adapt", "original"}
COMPLEXITIES = {"complex_visual", "template", "native"}
WORKFLOW_STATUSES = {
    "in_progress",
    "draft_reviewable",
    "production_ready",
    "needs_stronger_model_or_human_asset",
}
SOURCE_MODES = {"new", "modify", "reuse_allowed", "reuse_forbidden"}
HASH_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _string_list(value: object, *, allow_empty: bool = True) -> bool:
    return (
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def validate_contract_structure(contract: object) -> list[str]:
    if not isinstance(contract, dict):
        return ["合同根节点必须是 JSON 对象"]

    errors: list[str] = []
    if contract.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    if not isinstance(contract.get("status"), str) or contract.get("status") not in WORKFLOW_STATUSES:
        errors.append("status 不受支持")
    if not isinstance(contract.get("task_mode"), str) or contract.get("task_mode") not in TASK_MODES:
        errors.append("task_mode 必须是 replicate、adapt 或 original")
    if not isinstance(contract.get("complexity"), str) or contract.get("complexity") not in COMPLEXITIES:
        errors.append("complexity 必须是 complex_visual、template 或 native")

    target = contract.get("target")
    if not isinstance(target, dict):
        errors.append("target 必须是对象")
    else:
        if not _is_positive_int(target.get("width")) or not _is_positive_int(target.get("height")):
            errors.append("target.width 和 target.height 必须是正整数")
        if not isinstance(target.get("shape"), str) or target.get("shape") not in {"circle", "rounded_rect", "round_rect"}:
            errors.append("target.shape 必须是 circle 或 rounded_rect（兼容 round_rect）")

    source = contract.get("source_strategy")
    if not isinstance(source, dict):
        errors.append("source_strategy 必须是对象")
    else:
        if not isinstance(source.get("mode"), str) or source.get("mode") not in SOURCE_MODES:
            errors.append("source_strategy.mode 不受支持")
        if not isinstance(source.get("reuse_permission_confirmed"), bool):
            errors.append("source_strategy.reuse_permission_confirmed 必须是布尔值")

    reference = contract.get("reference_contract")
    if not isinstance(reference, dict):
        errors.append("reference_contract 必须是对象")
    else:
        hashes = reference.get("reference_hashes")
        if not isinstance(hashes, list) or any(
            not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None
            for value in hashes or []
        ):
            errors.append("reference_contract.reference_hashes 必须是40或64位十六进制哈希数组")
        elif len(set(hashes)) != len(hashes):
            errors.append("reference_contract.reference_hashes 不能重复")
        for field in ("invariants", "allowed_changes", "forbidden_content"):
            if not _string_list(reference.get(field)):
                errors.append(f"reference_contract.{field} 必须是字符串数组")

    routes = contract.get("production_routes")
    seen_regions: set[str] = set()
    if not isinstance(routes, list):
        errors.append("production_routes 必须是数组")
    else:
        for index, route in enumerate(routes):
            label = f"production_routes[{index}]"
            if not isinstance(route, dict):
                errors.append(f"{label} 必须是对象")
                continue
            region = route.get("region")
            if not isinstance(region, str) or not region.strip() or region in seen_regions:
                errors.append(f"{label}.region 必须是唯一非空字符串")
            else:
                seen_regions.add(region)
            if (
                not isinstance(route.get("primary"), str)
                or not isinstance(route.get("fallback"), str)
                or route.get("primary") not in ROUTES
                or route.get("fallback") not in ROUTES
            ):
                errors.append(f"{label} 的 primary/fallback 必须使用登记路线")
            elif route.get("primary") == route.get("fallback"):
                errors.append(f"{label} 的 primary 与 fallback 不能相同")
            if "reference_input_hashes" in route:
                values = route["reference_input_hashes"]
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None
                    for value in values or []
                ):
                    errors.append(f"{label}.reference_input_hashes 必须是哈希数组")

    gates = contract.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates 必须是对象")
    else:
        missing = [gate for gate in GATE_ORDER if gate not in gates]
        extra = sorted(set(gates) - set(GATE_ORDER))
        if missing:
            errors.append("gates 缺少：" + "、".join(missing))
        if extra:
            errors.append("gates 包含未知阶段：" + "、".join(extra))
        for gate in GATE_ORDER:
            if gate in gates and (
                not isinstance(gates[gate], str) or gates[gate] not in GATE_STATUSES
            ):
                errors.append(f"gates.{gate} 状态无效")

    attempts = contract.get("attempts")
    if not isinstance(attempts, list):
        errors.append("attempts 必须是数组")
    else:
        route_by_region = {
            item.get("region"): item
            for item in (routes if isinstance(routes, list) else [])
            if isinstance(item, dict) and isinstance(item.get("region"), str)
        }
        for index, attempt in enumerate(attempts):
            label = f"attempts[{index}]"
            if not isinstance(attempt, dict):
                errors.append(f"{label} 必须是对象")
                continue
            region = attempt.get("region")
            route = attempt.get("route")
            registered = route_by_region.get(region, {}) if isinstance(region, str) else {}
            if not isinstance(region, str) or region not in route_by_region:
                errors.append(f"{label}.region 未在 production_routes 登记")
            if not isinstance(route, str) or route not in (registered.get("primary"), registered.get("fallback")):
                errors.append(f"{label}.route 未在对应区域登记")
            if not isinstance(attempt.get("outcome"), str) or attempt.get("outcome") not in {"pass", "improved", "failed"}:
                errors.append(f"{label}.outcome 无效")
            if not isinstance(attempt.get("improvement"), bool):
                errors.append(f"{label}.improvement 必须是布尔值")
            elif attempt.get("outcome") == "failed" and attempt.get("improvement") is not False:
                errors.append(f"{label} 失败结果不能声明有明确改善")
            elif attempt.get("outcome") in {"pass", "improved"} and attempt.get("improvement") is not True:
                errors.append(f"{label} 通过或改善结果必须声明明确改善")
    return errors


def _prototype_complete(contract: dict) -> bool:
    status = contract["gates"]["prototype"]
    return status == "pass" or (
        status == "not_applicable" and contract.get("complexity") in {"template", "native"}
    )


def _stage_complete(contract: dict, gate: str) -> bool:
    if gate == "prototype":
        return _prototype_complete(contract)
    return contract["gates"][gate] == "pass"


def _attempt_failures(contract: dict) -> list[str]:
    failures: list[str] = []
    attempts = contract["attempts"]
    routes = {item["region"]: item for item in contract["production_routes"]}
    for region, route in routes.items():
        indexed = [(index, item) for index, item in enumerate(attempts) if item["region"] == region]
        primary = route["primary"]
        fallback = route["fallback"]
        if any(item["route"] == fallback and item["outcome"] == "failed" for _, item in indexed):
            if contract.get("status") != "needs_stronger_model_or_human_asset":
                failures.append(f"{region} 的降级路线 {fallback} 仍失败，必须将 status 设为 needs_stronger_model_or_human_asset")
            continue
        pair_index = 0
        while pair_index < len(indexed) - 1:
            _, first = indexed[pair_index]
            _, second = indexed[pair_index + 1]
            if (
                first["route"] == second["route"] == primary
                and first["improvement"] is False
                and second["improvement"] is False
            ):
                if pair_index + 2 >= len(indexed):
                    failures.append(f"{region} 同一方法连续两次无明确改善，下一次尝试必须切换到降级路线 {fallback}")
                    break
                next_attempt = indexed[pair_index + 2][1]
                if next_attempt["route"] != fallback:
                    failures.append(f"{region} 同一方法连续两次无明确改善，下一次尝试必须切换到降级路线 {fallback}")
                    break
                pair_index += 3
                continue
            pair_index += 1
    return failures


def gate_failures(contract: dict, requested_gate: str) -> list[str]:
    if requested_gate not in GATE_ORDER:
        return [f"未知门禁：{requested_gate}"]
    failures: list[str] = []
    gates = contract["gates"]
    try:
        target = dict(contract["target"])
        if target.get("shape") == "round_rect":
            target["shape"] = "rounded_rect"
        screen_spec(target)
    except ValueError as exc:
        failures.append("目标规格需要适配：" + str(exc))

    if contract.get("status") == "needs_stronger_model_or_human_asset":
        return ["降级路线仍失败，已停止正式制作，等待更强模型或人工素材"]

    for later_index, later in enumerate(GATE_ORDER[1:], 1):
        if gates[later] in {"pass", "not_applicable"}:
            for earlier in GATE_ORDER[:later_index]:
                if not _stage_complete(contract, earlier):
                    failures.append(f"{later} 不得跳过未通过的 {earlier}")

    if contract["source_strategy"].get("reuse_permission_confirmed") is not True:
        failures.append("需求入口尚未确认素材复用权限")
    reference = contract["reference_contract"]
    requested_index = GATE_ORDER.index(requested_gate)
    if requested_index >= GATE_ORDER.index("reference") and contract["task_mode"] == "replicate":
        if not reference["reference_hashes"]:
            failures.append("复刻任务必须记录参考图哈希")
        if not reference["invariants"]:
            failures.append("复刻任务必须记录参考不变量")
        expected_hashes = set(reference["reference_hashes"])
        for route in contract["production_routes"]:
            if "reference_image_edit" in {route["primary"], route["fallback"]} and not expected_hashes.issubset(
                set(route.get("reference_input_hashes", []))
            ):
                failures.append(f"{route['region']} 的复刻生成必须把原参考图直接输入工具")

    if requested_gate in {"prototype", "assembly", "visual_preflight"} and not contract["production_routes"]:
        failures.append("进入样稿前必须登记制作路线和降级路线")
    if contract["complexity"] == "complex_visual" and gates["prototype"] == "not_applicable":
        failures.append("复杂视觉任务必须完成代表性样稿")

    for gate in GATE_ORDER[: requested_index + 1]:
        if not _stage_complete(contract, gate):
            failures.append(f"{gate} 尚未通过")

    failures.extend(_attempt_failures(contract))
    return list(dict.fromkeys(failures))


def load_contract(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_contract_structure(data)
    if errors:
        raise ValueError("；".join(errors))
    return data


def verified_gate_failures(path: Path, contract: dict, gate: str) -> list[str]:
    failures = gate_failures(contract, gate)
    if gate in GATE_ORDER[2:] and contract['gates']['prototype'] == 'pass':
        from prototype_evidence import checkpoint_failures
        failures.extend(checkpoint_failures(path, contract))
    return list(dict.fromkeys(failures))


def main() -> int:
    parser = argparse.ArgumentParser(description="只读校验表盘工作流合同与阶段门禁")
    parser.add_argument("contract", type=Path)
    parser.add_argument("--gate", required=True, choices=GATE_ORDER)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"合同结构无效：{exc}")
        return 2

    failures = verified_gate_failures(args.contract, contract, args.gate)
    if failures:
        print(f"{args.gate} 门禁失败：")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"{args.gate} 门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
