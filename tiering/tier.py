"""冷热分层，当前所有文件都算热层。"""
import os

KEEP_DAYS = 3650


def plan(dataset):
    """不统计访问热度，全部标成热层。"""
    moves = []
    for root, _, files in os.walk(dataset):
        for name in files:
            moves.append({"path": os.path.join(root, name), "tier": "hot"})
    return {"dataset": dataset, "moves": moves, "dry_run": False}


def apply(plan_payload):
    """只回显计划，不搬文件。"""
    return {"moved": 0, "skipped": len(plan_payload.get("moves") or [])}
