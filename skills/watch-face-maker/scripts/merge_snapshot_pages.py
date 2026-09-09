"""校验分页完整性及同版本签名后合并；失败时不写出伪完整快照。"""
import argparse
import json
from pathlib import Path


def merge_pages(pages):
    if not pages:
        raise ValueError("没有快照页")
    first = pages[0]
    signature = first.get("capture_signature")
    if not signature:
        raise ValueError("缺少整组几何签名，请使用当前采集器重新读取")
    total = first["total_nodes"]
    offset, nodes = 0, []
    scope = {k: v for k, v in first["config"].items() if k not in {"offset", "limit", "max_bytes"}}
    for index, page in enumerate(pages):
        if page.get("schema_version", 1) != first.get("schema_version", 1):
            raise ValueError("分页采集器版本不一致")
        if page.get("schema_version", 1) >= 2:
            stable = page.get("stability", {})
            if stable.get("status") != "pass" or stable.get("before") != signature or stable.get("after") != signature:
                raise ValueError("采集前后签名不一致或缺失")
        if page.get("capture_signature") != signature or page["total_nodes"] != total:
            raise ValueError("采集期间Figma发生变化，不能混合不同版本")
        if {k: v for k, v in page["config"].items() if k not in {"offset", "limit", "max_bytes"}} != scope or page.get("traversal") != first.get("traversal"):
            raise ValueError("分页范围或遍历选项不一致")
        if page["page"]["offset"] != offset or page["page"]["count"] != len(page["nodes"]):
            raise ValueError("分页遗漏、重叠或数量不符")
        nodes.extend(page["nodes"])
        offset += len(page["nodes"])
        expected_next = offset if offset < total else None
        if page["next_offset"] != expected_next or (expected_next is None and index != len(pages) - 1):
            raise ValueError("分页游标或末页位置不符")
    if offset != total or len({n["id"] for n in nodes}) != total:
        raise ValueError("节点不完整或ID重复")
    return {
        **first, "nodes": nodes, "config": scope,
        "capture_end": pages[-1]["captured_at"], "next_offset": None,
        "page": {"offset": 0, "limit": total, "count": total},
        "pagination": {"total_nodes": total, "collected_nodes": total, "complete": True},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pages", type=Path, help="按采集顺序保存的分页JSON数组")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        snapshot = merge_pages(json.loads(args.pages.read_text()))
    except (ValueError, KeyError) as error:
        parser.exit(1, f"合并失败：{error}\n")
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
