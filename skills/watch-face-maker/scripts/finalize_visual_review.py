#!/usr/bin/env python3
"""封存已人工评审通过的视觉记录，并推进合同视觉门禁。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from workflow_guards import load_contract
from validate_manifest import validate_workflow_binding


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="封存表盘视觉预检并推进 visual_preflight 门禁")
    parser.add_argument("contract", type=Path)
    parser.add_argument("visual_review", type=Path)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--exports-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        contract = load_contract(args.contract)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(snapshot, dict):
            raise ValueError("清单和快照必须是对象")
        review = json.loads(args.visual_review.read_text(encoding="utf-8"))
        if not isinstance(review, dict):
            raise ValueError("视觉预检根节点必须是对象")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"视觉预检封存输入无效：{exc}")
        return 2

    checks = validate_workflow_binding(
        manifest, args.contract, args.visual_review, args.snapshot, args.exports_dir,
        snapshot, required_gate="assembly",
    )
    failures = [item for item in checks if item["status"] != "pass"]
    if failures:
        print("视觉预检不能封存：当前视觉证据检查未通过")
        for item in failures:
            print(f"- {item['explanation']}")
        return 1

    contract["gates"]["visual_preflight"] = "pass"
    contract["status"] = "production_ready"
    contract_bytes = encoded(contract)
    review["workflow_contract_sha256"] = hashlib.sha256(contract_bytes).hexdigest()
    review_bytes = encoded(review)
    contract_tmp = args.contract.with_name(args.contract.name + ".tmp")
    review_tmp = args.visual_review.with_name(args.visual_review.name + ".tmp")
    try:
        contract_tmp.write_bytes(contract_bytes)
        review_tmp.write_bytes(review_bytes)
        os.replace(contract_tmp, args.contract)
        os.replace(review_tmp, args.visual_review)
    except OSError as exc:
        contract_tmp.unlink(missing_ok=True)
        review_tmp.unlink(missing_ok=True)
        print(f"视觉预检封存失败：{exc}")
        return 2
    print("视觉预检已封存，合同 visual_preflight 已通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
