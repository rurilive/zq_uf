"""事件记录：保留每次评估的完整取值，供回放与复核。"""

from __future__ import annotations

import copy
import time
from datetime import date, datetime, timezone

_RECORDS = []
_BY_EVENT = {}
_SEQ = 0

__all__ = ["add", "clear", "get", "list_records", "records"]


def _now_iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def add(event_id, snapshot, version, result, at=None):
    """记录一次评估。同一事件 id 保留最新一条，全部记录按时间顺序保留。"""
    global _SEQ
    epoch = time.time() if at is None else _epoch(at)
    _SEQ += 1
    record = {
        "seq": _SEQ,
        "event": event_id,
        "snapshot": copy.deepcopy(snapshot),
        "version": version,
        "action": result["action"],
        "rule": result["rule"],
        "matched": copy.deepcopy(result["matched"]),
        "values": copy.deepcopy(result["values"]),
        "at": _now_iso(epoch),
        "epoch": epoch,
    }
    _RECORDS.append(record)
    if event_id != "":
        _BY_EVENT[event_id] = record
    return copy.deepcopy(record)


def records(start=None, end=None):
    """按时间段筛选记录，start/end 为闭区间。

    同一事件 id 被重复评估时只取窗口内最新一条，避免回放重复计数；
    没有 id 的事件无法去重，全部保留。
    """
    low = _epoch(start)
    high = _epoch(end)
    selected = [
        record
        for record in _RECORDS
        if (low is None or record["epoch"] >= low) and (high is None or record["epoch"] <= high)
    ]
    latest = {}
    for record in selected:
        if record["event"] != "":
            latest[record["event"]] = record
    return [
        copy.deepcopy(record)
        for record in selected
        if record["event"] == "" or latest[record["event"]] is record
    ]


def list_records():
    return [copy.deepcopy(record) for record in _RECORDS]


def get(event_id):
    record = _BY_EVENT.get(event_id)
    return copy.deepcopy(record) if record is not None else None


def clear():
    """清空全部事件记录，返回清掉的数量。"""
    count = len(_RECORDS)
    _RECORDS.clear()
    _BY_EVENT.clear()
    return {"events": count}


def _epoch(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("time bound must be a datetime, date, epoch number or ISO string")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp()
    if isinstance(value, str):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    raise ValueError("time bound must be a datetime, date, epoch number or ISO string")
