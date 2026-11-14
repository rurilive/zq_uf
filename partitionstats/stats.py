"""分区统计与裁剪：登记每分区行数/大小/时间范围，统计带缓存，查询按时间范围裁剪。"""
import json
import os
import time
from datetime import datetime

_REGISTRY = {}  # dataset -> {partition: {"rows", "size", "tmin", "tmax", "hits"}}
_CACHE = {}     # dataset -> 聚合统计


def _to_ts(value):
    """epoch 数字或 ISO 日期字符串统一成 epoch 秒；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return None
    return None


def _partition_stats(path):
    rows = 0
    tmin = tmax = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows += 1
            ts = None
            try:
                obj = json.loads(line)
            except ValueError:
                obj = None
            if isinstance(obj, dict):
                ts = _to_ts(obj.get("ts"))
            if ts is not None:
                tmin = ts if tmin is None else min(tmin, ts)
                tmax = ts if tmax is None else max(tmax, ts)
    return {
        "rows": rows,
        "size": os.path.getsize(path),
        "tmin": tmin,
        "tmax": tmax,
        "hits": 0,
    }


def register(dataset):
    """扫描目录，登记每分区统计，并使该数据集的缓存失效。写入或删除后调用。"""
    if not os.path.isdir(dataset):
        _REGISTRY.pop(dataset, None)
        _CACHE.pop(dataset, None)
        return {}
    parts = {}
    for name in sorted(os.listdir(dataset)):
        path = os.path.join(dataset, name)
        if os.path.isfile(path):
            parts[name] = _partition_stats(path)
    _REGISTRY[dataset] = parts
    _CACHE.pop(dataset, None)
    return parts


def invalidate(dataset):
    """写入或删除分区后调用：丢弃登记与缓存，下次统计/查询时重建。"""
    _REGISTRY.pop(dataset, None)
    _CACHE.pop(dataset, None)


def _registered(dataset):
    if dataset not in _REGISTRY:
        register(dataset)
    return _REGISTRY.get(dataset, {})


def stats(dataset):
    """聚合统计，带缓存；首次调用或失效后重新登记。"""
    if not os.path.isdir(dataset):
        return {"files": 0, "rows": 0}
    if dataset not in _CACHE:
        parts = _registered(dataset)
        _CACHE[dataset] = {
            "files": len(parts),
            "rows": sum(p["rows"] for p in parts.values()),
            "bytes": sum(p["size"] for p in parts.values()),
            "partitions": {n: dict(p) for n, p in parts.items()},
        }
    return dict(_CACHE[dataset])


def _select(dataset, start, end):
    scan, skip = [], []
    for name, p in _registered(dataset).items():
        if p["tmin"] is None or p["tmax"] is None:
            scan.append(name)  # 无时间范围的分区无法裁剪，总是扫描
        elif start is not None and p["tmax"] < start:
            skip.append(name)
        elif end is not None and p["tmin"] > end:
            skip.append(name)
        else:
            scan.append(name)
    return scan, skip


def prune(filters):
    """按 filters(dataset/start/end) 裁剪，返回 {"scan": [...], "skip": [...]}，并累计扫描分区的访问量。"""
    filters = filters or {}
    dataset = filters.get("dataset")
    if not dataset or not os.path.isdir(dataset):
        return {"scan": [], "skip": []}
    scan, skip = _select(dataset, _to_ts(filters.get("start")), _to_ts(filters.get("end")))
    parts = _registered(dataset)
    for name in scan:
        parts[name]["hits"] += 1
    return {"scan": scan, "skip": skip}


def query(dataset, filters=None):
    """按时间范围裁剪并输出耗时：返回扫描分区、跳过分区和耗时秒。"""
    filters = dict(filters or {})
    filters["dataset"] = dataset
    t0 = time.perf_counter()
    result = prune(filters)
    return {
        "scanned": result["scan"],
        "skipped": result["skip"],
        "elapsed": time.perf_counter() - t0,
    }


def hotspots(dataset, limit=None):
    """热点分区：按访问量、大小降序。"""
    parts = _registered(dataset)
    ranked = sorted(
        parts.items(),
        key=lambda kv: (kv[1]["hits"], kv[1]["size"]),
        reverse=True,
    )
    rows = [dict(p, partition=n) for n, p in ranked]
    return rows[:limit] if limit else rows
