#!/usr/bin/env python3
"""生成同尺寸视觉预检证据和待人工填写的逐项结论。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

from workflow_guards import file_sha256, load_contract
from visual_artifacts import comparison_images


def parse_crop(value: str, width: int, height: int) -> tuple[str, tuple[int, int, int, int]]:
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9-]*):(\d+),(\d+),(\d+),(\d+)", value)
    if match is None:
        raise ValueError(f"局部图参数无效：{value}")
    name = match.group(1)
    x, y, crop_width, crop_height = map(int, match.groups()[1:])
    if crop_width <= 0 or crop_height <= 0:
        raise ValueError(f"局部图宽高必须为正数：{value}")
    if x + crop_width > width or y + crop_height > height:
        raise ValueError(f"局部图超出目标尺寸：{value}")
    return name, (x, y, x + crop_width, y + crop_height)


def artifact(path: Path) -> dict:
    return {"path": path.name, "sha256": file_sha256(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成表盘视觉预检并排图、叠图和关键区域局部图")
    parser.add_argument("reference", type=Path)
    parser.add_argument("implementation", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--exports-dir", type=Path, required=True)
    parser.add_argument("--implementation-node-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--crop",
        action="append",
        required=True,
        help="关键区域，格式 name:x,y,width,height；可重复",
    )
    args = parser.parse_args()

    try:
        from PIL import Image

        contract = load_contract(args.contract)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("清单根节点必须是对象")
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(snapshot, dict):
            raise ValueError("Figma 快照根节点必须是对象")
        export_item = next(
            (
                item for item in snapshot.get("export_items", [])
                if isinstance(item, dict)
                and item.get("node_id") == args.implementation_node_id
                and item.get("role") == "presentation_main"
            ),
            None,
        )
        if export_item is None or not isinstance(export_item.get("path"), str):
            raise ValueError("实现图节点未登记为当前快照的 presentation_main 导出")
        exports_root = args.exports_dir.resolve()
        expected_implementation = (exports_root / export_item["path"]).resolve()
        if not expected_implementation.is_relative_to(exports_root) or expected_implementation == exports_root:
            raise ValueError("实现图导出路径必须位于当前导出目录内")
        if args.implementation.resolve() != expected_implementation:
            raise ValueError("实现图不是当前快照登记的正式导出文件")
        reference_hash = file_sha256(args.reference)
        if reference_hash not in contract["reference_contract"]["reference_hashes"]:
            raise ValueError("参考图哈希未登记在工作流合同中")
        target = contract["target"]
        width, height = target["width"], target["height"]
        crops = [parse_crop(value, width, height) for value in args.crop]
        crop_names = [name for name, _ in crops]
        if len(set(crop_names)) != len(crop_names) or set(crop_names) & {"side_by_side", "overlay"}:
            raise ValueError("局部图名称不能重复")
        reference_image = Image.open(args.reference).convert("RGBA")
        implementation_image = Image.open(args.implementation).convert("RGBA")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"视觉预检输入无效：{exc}")
        return 2

    images = comparison_images(reference_image, implementation_image, (width, height), crops)
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    reference_source = output / ("reference-source" + args.reference.suffix)
    if args.reference.resolve() != reference_source.resolve():
        shutil.copyfile(args.reference, reference_source)
    reference_path = output / "reference-fitted.png"
    implementation_path = output / "implementation-fitted.png"
    side_path = output / "side-by-side.png"
    overlay_path = output / "overlay.png"
    images['reference_fitted'].save(reference_path)
    images['implementation_fitted'].save(implementation_path)
    images['side_by_side'].save(side_path)
    images['overlay'].save(overlay_path)
    crop_artifacts = []
    for name, box in crops:
        x1, y1, x2, y2 = box
        path = output / f"crop-{name}.png"
        images[name].save(path)
        crop_artifacts.append(dict(artifact(path), id=name, bounds=[x1, y1, x2 - x1, y2 - y1]))

    source = manifest.get("source_strategy", {})
    evidence_ids = ["side_by_side", "overlay", *crop_names]
    review = {
        "schema_version": 1,
        "workflow_contract_sha256": file_sha256(args.contract),
        "snapshot_sha256": file_sha256(args.snapshot),
        "reference_hashes": contract["reference_contract"]["reference_hashes"],
        "reference_input": artifact(reference_source),
        "implementation_sha256": file_sha256(args.implementation),
        "implementation_export": {
            "node_id": args.implementation_node_id,
            "path": export_item["path"],
            "sha256": file_sha256(args.implementation),
        },
        "source_hashes": {
            "reference": source.get("reference_image_hashes", []),
            "adopted": source.get("adopted_image_hashes", []),
            "reused": source.get("reused_image_hashes", []),
        },
        "artifacts": {
            "side_by_side": artifact(side_path),
            "overlay": artifact(overlay_path),
            "crops": crop_artifacts,
        },
        "checks": [
            {"invariant": invariant, "status": "unverified", "evidence": evidence_ids}
            for invariant in contract["reference_contract"]["invariants"]
        ],
    }
    if snapshot.get('config', {}).get('background_id'):
        from background_edges import analyze_background
        edge, edge_images = analyze_background(snapshot, args.exports_dir)
        edge['artifacts'] = {}
        for key, edge_image in edge_images.items():
            path = output / ('background-edge-' + key + '.png')
            edge_image.save(path)
            edge['artifacts'][key] = artifact(path)
        review['background_edge_review'] = edge
    review_path = output / "visual-review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已生成视觉预检证据：{review_path}")
    print("所有不变量初始状态均为 unverified；必须逐项人工评审后再运行正式校验。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
