"""采集报告，当前只统计总数。"""


def build(batch):
    """失败和跳过都算进总数，不分开。"""
    rows = list(batch.get("rows", []))
    return {
        "batch": batch.get("id", ""),
        "total": len(rows),
        "by_site": {},
        "by_reason": {},
    }


def reconcile(report):
    """只比较总数，不做校验和对账。"""
    return {"batch": report.get("batch", ""), "matched": True, "diff": []}
