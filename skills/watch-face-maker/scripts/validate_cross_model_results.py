#!/usr/bin/env python3
"""校验跨模型盲测矩阵、原始证据与验收阈值。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from run_evidence import digest
from workflow_guards import file_sha256, load_contract
from validate_manifest import validate_workflow_binding, production_validation_check


HASH = re.compile(r"[0-9a-fA-F]{64}")


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 根节点必须是对象")
    return value


def _safe_path(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError("原始答复缺少路径")
    base = root.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or path == base:
        raise ValueError("原始答复路径必须位于结果目录内")
    return path


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _load_cases(plan_path: Path, suite: dict, field: str) -> dict[str, dict]:
    relative = suite.get(field)
    if not isinstance(relative, str):
        raise ValueError(f"{field} 路径缺失")
    value = _read_object((plan_path.parent / relative).resolve())
    cases = value.get("cases")
    if value.get("schema_version") != 1 or not isinstance(cases, list):
        raise ValueError(f"{relative} 结构无效")
    mapping = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in mapping:
            raise ValueError(f"{relative} 包含无效或重复 case id")
        mapping[case["id"]] = case
    return mapping


def build_fingerprints(contract: dict, manifest: dict, snapshot: dict) -> tuple[str, str]:
    routes = sorted(contract["production_routes"], key=lambda item: item["region"])
    families = [
        {"name": family["name"], "kind": family.get("kind"), "count": len(family["node_ids"])}
        for family in snapshot.get("resource_families", [])
    ]
    split = {"capability_modes": manifest.get("capability_modes", {}),
             "states": manifest.get("states", {}),
             "families": sorted(families, key=lambda item: item["name"])}
    return digest(routes), digest(split)


def _target_key(target: object) -> tuple[str, str, str]:
    if not isinstance(target, dict) or any(
        not isinstance(target.get(field), str) or not target[field].strip()
        for field in ("file_key", "page_id", "section_id")
    ):
        raise ValueError("隔离目标必须登记 file_key、page_id 和 section_id")
    return tuple(target[field] for field in ("file_key", "page_id", "section_id"))


def revalidate_production(paths: dict[str, Path], exports: Path) -> dict:
    """通过正式入口复查当前产物，历史报告不能替代当前验收。"""
    with tempfile.TemporaryDirectory(prefix="watch-face-production-") as directory:
        report_path = Path(directory) / "report.json"
        command = [sys.executable, str(Path(__file__).with_name("validate_manifest.py")), str(paths["manifest"])]
        for field, flag in (("snapshot", "--snapshot"), ("evidence", "--evidence"),
                            ("workflow_contract", "--workflow-contract"), ("visual_review", "--visual-review")):
            command.extend([flag, str(paths[field])])
        command.extend(["--exports-dir", str(exports), "--report", str(report_path)])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"当前正式生产校验无法执行：{exc}") from exc
        if not report_path.is_file():
            raise ValueError("当前正式生产校验未生成报告")
        report = _read_object(report_path)
        if result.returncode != 0 or report.get("delivery", {}).get("production_validation") != "pass":
            raise ValueError("当前正式生产校验未通过：" + result.stdout[-2000:])
        return report


def validate_build_evidence(run: dict, plan: dict, root: Path) -> list[str]:
    try:
        evidence = run.get("build_evidence")
        if not isinstance(evidence, dict):
            raise ValueError("缺少实际制作 build_evidence")
        target = _target_key(evidence.get("target"))
        targets = plan.get("isolated_targets")
        if not isinstance(targets, list) or target not in {_target_key(value) for value in targets}:
            raise ValueError("目标未在冻结评测计划登记为隔离测试目标")
        paths = {}
        objects = {}
        hashes = {}
        for field in ("manifest", "workflow_contract", "snapshot", "visual_review", "evidence", "report"):
            record = evidence.get(field)
            if not isinstance(record, dict) or not isinstance(record.get("sha256"), str) or HASH.fullmatch(record["sha256"]) is None:
                raise ValueError(f"{field} 缺少有效文件记录")
            path = _safe_path(root, record.get("path"))
            if not path.is_file() or file_sha256(path) != record["sha256"]:
                raise ValueError(f"{field} 文件缺失或哈希不一致")
            paths[field] = path
            hashes[field] = record["sha256"]
            objects[field] = _read_object(path)
        exports = _safe_path(root, evidence.get("exports_dir"))
        if not exports.is_dir():
            raise ValueError("实际导出目录缺失")
        snapshot = objects["snapshot"]
        config = snapshot.get("config", {})
        if _target_key(config) != target or not any(
            node.get("id") == target[2] and node.get("type") == "SECTION"
            for node in snapshot.get("nodes", []) if isinstance(node, dict)
        ):
            raise ValueError("快照未绑定登记的隔离目标")
        checks = validate_workflow_binding(
            objects["manifest"], paths["workflow_contract"], paths["visual_review"],
            paths["snapshot"], exports, snapshot,
        )
        failures = [item["explanation"] for item in checks if item["status"] != "pass"]
        if failures:
            raise ValueError("实际制作证据未通过：" + "；".join(failures))
        report = objects["report"]
        expected_hashes = {field: hashes[field] for field in ("manifest", "workflow_contract", "snapshot", "visual_review", "evidence")}
        inputs = report.get("inputs")
        if not isinstance(inputs, dict) or any(inputs.get(field) != value for field, value in expected_hashes.items()):
            raise ValueError("正式报告未绑定当前制作输入哈希")
        report_checks = report.get("checks")
        if not isinstance(report_checks, list) or not report_checks or any(not isinstance(item, dict) for item in report_checks):
            raise ValueError("正式报告缺少有效检查记录")
        ids = {item.get("id") for item in report_checks}
        if not {"manifest", "snapshot.structure", "exports.png", "exports.binding", "workflow.contract", "workflow.visual_review", "workflow.production_validation"}.issubset(ids):
            raise ValueError("正式报告遗漏必要检查")
        if report.get("delivery", {}).get("production_validation") != "pass" or production_validation_check(report_checks, [])["status"] != "pass":
            raise ValueError("正式生产校验未通过")
        revalidate_production(paths, exports)
        routes, split = build_fingerprints(load_contract(paths["workflow_contract"]), objects["manifest"], snapshot)
        if run.get("production_route_fingerprint") != routes:
            raise ValueError("制作路线指纹与实际合同不一致")
        if run.get("resource_split_fingerprint") != split:
            raise ValueError("资源拆分指纹与实际清单和快照不一致")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return [str(exc)]
    return []


def validate_results(plan_path: Path, results_path: Path) -> list[str]:
    try:
        plan = _read_object(plan_path)
        results = _read_object(results_path)
        if plan.get("schema_version") != 1 or results.get("schema_version") != 1:
            raise ValueError("schema_version 必须为 1")
        models = plan.get("models")
        repetitions = plan.get("repetitions")
        reasoning = plan.get("reasoning_effort")
        suites = plan.get("suites")
        if not isinstance(models, list) or len(models) != len(set(models)) or not all(isinstance(item, str) for item in models):
            raise ValueError("models 必须是唯一字符串数组")
        if not isinstance(repetitions, int) or isinstance(repetitions, bool) or repetitions < 1:
            raise ValueError("repetitions 必须是正整数")
        if not isinstance(reasoning, str) or not reasoning:
            raise ValueError("reasoning_effort 缺失")
        if not isinstance(suites, dict) or set(suites) != {"pressure", "full_build"}:
            raise ValueError("suites 必须包含 pressure 和 full_build")
        pressure_prompts = _load_cases(plan_path, suites["pressure"], "prompts")
        pressure_rubric = _load_cases(plan_path, suites["pressure"], "rubric")
        full_prompts = _load_cases(plan_path, suites["full_build"], "prompts")
        full_rubric = _load_cases(plan_path, suites["full_build"], "rubric")
        if set(pressure_prompts) != set(pressure_rubric) or set(full_prompts) != set(full_rubric):
            raise ValueError("提示与隐藏评分标准的 case id 不一致")
        skill_hashes = results.get("skill_hashes")
        if not isinstance(skill_hashes, dict) or any(
            HASH.fullmatch(skill_hashes.get(phase, "")) is None for phase in ("baseline", "candidate")
        ):
            raise ValueError("skill_hashes 必须包含 baseline 和 candidate 的 SHA-256")
        runs = results.get("runs")
        if not isinstance(runs, list):
            raise ValueError("runs 必须是数组")
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return [f"评测文件结构无效：{exc}"]

    errors: list[str] = []
    expected: set[tuple] = set()
    prompt_maps = {"pressure": pressure_prompts, "full_build": full_prompts}
    for suite_name, cases in prompt_maps.items():
        phases = suites[suite_name].get("phases")
        if not isinstance(phases, list) or not phases:
            errors.append(f"{suite_name} 缺少 phases")
            continue
        for phase in phases:
            for case_id in cases:
                for model in models:
                    for repetition in range(1, repetitions + 1):
                        expected.add((suite_name, phase, case_id, model, repetition))

    actual: dict[tuple, dict] = {}
    raw_paths: set[Path] = set()
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            errors.append(f"runs[{index}] 必须是对象")
            continue
        key = (
            run.get("suite"), run.get("phase"), run.get("case_id"),
            run.get("model_id"), run.get("repetition"),
        )
        if key in actual:
            errors.append(f"运行矩阵存在重复项：{key}")
            continue
        actual[key] = run
        if key not in expected:
            errors.append(f"运行矩阵包含未计划项：{key}")
            continue
        suite_name, phase, case_id, model, _ = key
        if run.get("reasoning_effort") != reasoning:
            errors.append(f"{key} 推理强度不是 {reasoning}")
        if run.get("skill_sha256") != skill_hashes.get(phase):
            errors.append(f"{key} Skill 哈希与阶段冻结版本不一致")
        prompt = prompt_maps[suite_name][case_id].get("prompt")
        if not isinstance(prompt, str) or run.get("input_sha256") != _prompt_hash(prompt):
            errors.append(f"{key} 输入哈希不一致")
        raw = run.get("raw_response")
        try:
            if not isinstance(raw, dict) or HASH.fullmatch(raw.get("sha256", "")) is None:
                raise ValueError("原始答复缺少 SHA-256")
            path = _safe_path(results_path.parent, raw.get("path"))
            if path in raw_paths:
                raise ValueError("原始答复路径重复")
            raw_paths.add(path)
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != raw["sha256"]:
                raise ValueError("原始答复文件缺失或哈希不一致")
        except (OSError, ValueError) as exc:
            errors.append(f"{key}：{exc}")

    missing = expected - set(actual)
    extra = set(actual) - expected
    if missing or extra:
        errors.append(f"运行矩阵不完整：缺少 {len(missing)}，额外 {len(extra)}")

    pressure_candidate = [
        run for key, run in actual.items() if key in expected and key[0] == "pressure" and key[1] == "candidate"
    ]
    pressure_baseline = [
        run for key, run in actual.items() if key in expected and key[0] == "pressure" and key[1] == "baseline"
    ]
    pressure_runs = pressure_baseline + pressure_candidate
    for run in pressure_runs:
        case_id = run.get("case_id")
        allowed_codes = set(pressure_rubric.get(case_id, {}).get("critical_failure_codes", []))
        codes = run.get("failure_codes")
        label = f"压力场景 {case_id}/{run.get('phase')}/{run.get('model_id')}/{run.get('repetition')}"
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            errors.append(f"{label} 缺少有效失败代码数组")
            continue
        unknown_codes = sorted(set(codes) - allowed_codes)
        if unknown_codes:
            errors.append(f"{label} 包含评分标准未登记的失败代码：{', '.join(unknown_codes)}")
        passed = run.get("critical_gate_passed")
        if not isinstance(passed, bool):
            errors.append(f"{label} 缺少布尔型 critical_gate_passed")
        elif passed and codes:
            errors.append(f"{label} 声明门禁通过但同时记录关键失败代码")
        elif not passed and not codes:
            errors.append(f"{label} 声明门禁失败但没有记录评分标准中的失败代码")

    def recorded_critical_codes(run: dict) -> set[str]:
        codes = run.get("failure_codes")
        if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
            return set()
        allowed = set(pressure_rubric.get(run.get("case_id"), {}).get("critical_failure_codes", []))
        return set(codes) & allowed

    candidate_failures = sum(
        run.get("critical_gate_passed") is not True
        or bool(recorded_critical_codes(run))
        for run in pressure_candidate
    )
    baseline_failures = sum(
        run.get("critical_gate_passed") is not True
        or bool(recorded_critical_codes(run))
        for run in pressure_baseline
    )
    required_rate = plan.get("acceptance", {}).get("candidate_pressure_gate_rate", 1.0)
    candidate_rate = 1 - candidate_failures / len(pressure_candidate) if pressure_candidate else 0
    if candidate_rate < required_rate:
        errors.append(f"候选 Skill 关键门禁遵守率未达到 {required_rate:.0%}（当前 {candidate_rate:.0%}，要求100%）")
    reduction = 1 - candidate_failures / baseline_failures if baseline_failures else 0
    required_reduction = plan.get("acceptance", {}).get("minimum_failure_reduction", 0.8)
    if baseline_failures == 0:
        errors.append("RED 基线没有记录关键失败，无法证明候选 Skill 的失败下降")
    elif reduction < required_reduction:
        errors.append(f"候选 Skill 关键失败下降 {reduction:.0%}，低于 {required_reduction:.0%}")
    low_model = plan.get("acceptance", {}).get("low_tier_model")
    if any(
        run.get("model_id") == low_model
        and (
            run.get("critical_gate_passed") is not True
            or bool(recorded_critical_codes(run))
        )
        for run in pressure_candidate
    ):
        errors.append("低档模型仍有关键失败")

    for case_id in pressure_prompts:
        fingerprints = {
            run.get("decision_fingerprint") for run in pressure_candidate if run.get("case_id") == case_id
        }
        if None in fingerprints or "" in fingerprints or len(fingerprints) != 1:
            errors.append(f"压力场景 {case_id} 的候选制作决策不一致")

    full_runs = [
        run for key, run in actual.items() if key in expected and key[0] == "full_build" and key[1] == "candidate"
    ]
    seen_targets = set()
    for run in full_runs:
        label = f"完整制作 {run.get('case_id')}/{run.get('model_id')}/{run.get('repetition')}"
        if run.get("critical_failures") != []:
            errors.append(f"{label} 存在关键失败")
        expected_invariants = full_rubric.get(run.get("case_id"), {}).get("invariants", [])
        invariants = run.get("invariants")
        if not isinstance(invariants, dict) or set(invariants) != set(expected_invariants) or any(
            invariants.get(name) != "pass" for name in expected_invariants
        ):
            errors.append(f"{label} 的关键不变量未全部通过")
        if run.get("isolated_target") is not True:
            errors.append(f"{label} 未证明使用隔离测试目标")
        errors.extend(f"{label}：{error}" for error in validate_build_evidence(run, plan, results_path.parent))
        try:
            target = _target_key(run.get("build_evidence", {}).get("target"))
            if target in seen_targets:
                errors.append(f"{label} 复用了其他运行的隔离目标")
            seen_targets.add(target)
        except (ValueError, AttributeError):
            pass
    for case_id in full_prompts:
        case_runs = [run for run in full_runs if run.get("case_id") == case_id]
        for field, label in (
            ("production_route_fingerprint", "制作路线"),
            ("resource_split_fingerprint", "资源拆分"),
        ):
            values = {run.get(field) for run in case_runs}
            if None in values or "" in values or len(values) != 1:
                errors.append(f"完整制作 {case_id} 的{label}在模型或重复运行间不一致")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 watch-face-maker 跨模型评测结果")
    parser.add_argument("plan", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    errors = validate_results(args.plan, args.results)
    if errors:
        print("跨模型评测未通过：")
        for error in errors:
            print(f"- {error}")
        return 1
    print("跨模型评测通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
