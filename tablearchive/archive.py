"""表归档：冷热分层 + 透明查询路由。

热层：_rows（在线内存表）。
冷层：_cold_dir 下的 JSONL 分区文件，_meta 记录分区位置、时间范围与校验和。
归档后源行从热层删除；查询按条件自动路由冷热两层并按原顺序合并；
恢复按元数据回迁，先校验行数与 SHA-256，通过才写回。
"""

import hashlib
import json
import os

_rows = []  # 热层在线行
_meta = []  # 冷层分区元数据：table/partition/path/field/start/end/count/checksum
_cold_dir = os.environ.get("TABLEARCHIVE_COLD_DIR", ".cold")

_time_field = "year"
_seq = 0  # 全局写入序号，保证冷热合并后保持原顺序
_order = {}  # id(热层行) -> 写入序号；归档行的序号随分区文件落盘
_last_route = {"table": None, "hot": 0, "cold": [], "pruned": [], "merged": 0}


def _stamp(rows):
    """给热层行分配写入序号（幂等）。"""
    global _seq
    for row in rows:
        key = id(row)
        if key not in _order:
            _order[key] = _seq
            _seq += 1


def _checksum(rows):
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_range(range_spec):
    """解析归档范围：before/after 开区间，from/to 闭区间，field 指定时间字段。"""
    spec = dict(range_spec or {})
    field = spec.pop("field", _time_field)
    lo = spec.pop("from", None)
    hi = spec.pop("to", None)
    before = spec.pop("before", None)
    after = spec.pop("after", None)
    if spec:
        raise ValueError("未知范围参数: %s" % ",".join(sorted(spec)))

    def hit(value):
        if value is None:
            return False
        if lo is not None and value < lo:
            return False
        if hi is not None and value > hi:
            return False
        if before is not None and value >= before:
            return False
        if after is not None and value <= after:
            return False
        return True

    return field, hit


def _save_meta():
    os.makedirs(_cold_dir, exist_ok=True)
    path = os.path.join(_cold_dir, "_meta.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_meta, fh, ensure_ascii=False, indent=2)


def _read_partition(path):
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                seq, row = json.loads(line)
                records.append((seq, row))
    return records


def archive(table, range_spec):
    """按时间范围把热层行归档到冷层分区，返回归档量与分区位置。"""
    field, hit = _parse_range(range_spec)
    _stamp(_rows)
    picked = [row for row in _rows if hit(row.get(field))]
    if not picked:
        return {"archived": 0}

    values = [row[field] for row in picked]
    start, end = min(values), max(values)
    base = "%s_%s_%s_%s" % (table, field, start, end)
    partition, n = base, 1
    existing = {m["partition"] for m in _meta}
    while partition in existing:
        n += 1
        partition = "%s_%d" % (base, n)

    os.makedirs(_cold_dir, exist_ok=True)
    path = os.path.join(_cold_dir, partition + ".jsonl")
    records = [(_order[id(row)], row) for row in picked]
    rows = [row for _, row in records]
    checksum = _checksum(rows)
    with open(path, "w", encoding="utf-8") as fh:
        for seq, row in records:
            fh.write(json.dumps([seq, row], ensure_ascii=False) + "\n")

    _meta.append({
        "table": table,
        "partition": partition,
        "path": path,
        "field": field,
        "start": start,
        "end": end,
        "count": len(rows),
        "checksum": checksum,
    })
    _save_meta()

    # 归档后删除源分区（热层行），并释放其序号映射
    gone = {id(row) for row in picked}
    _rows[:] = [row for row in _rows if id(row) not in gone]
    for row in picked:
        _order.pop(id(row), None)

    return {
        "archived": len(rows),
        "partition": partition,
        "path": path,
        "range": [start, end],
        "checksum": checksum,
    }


def query(table, filters):
    """等值过滤，自动路由冷热两层，合并结果保持原写入顺序。"""
    filters = filters or {}
    _stamp(_rows)

    def match(row):
        return all(row.get(k) == v for k, v in filters.items())

    hits = [(_order[id(row)], row) for row in _rows if match(row)]
    hot_count = len(hits)
    scanned, pruned = [], []
    for m in _meta:
        if m["table"] != table:
            continue
        tval = filters.get(m["field"])
        if tval is not None and not (m["start"] <= tval <= m["end"]):
            pruned.append(m["partition"])
            continue
        scanned.append(m["partition"])
        for seq, row in _read_partition(m["path"]):
            if match(row):
                hits.append((seq, row))

    merged = [row for _, row in sorted(hits, key=lambda item: item[0])]
    global _last_route
    _last_route = {
        "table": table,
        "hot": hot_count,
        "cold": scanned,
        "pruned": pruned,
        "merged": len(merged),
    }
    return merged


def restore(table, partition=None):
    """按元数据把冷层分区回迁热层：先全量校验，通过才写回并删除冷分区。"""
    targets = [
        m for m in _meta
        if m["table"] == table and (partition is None or m["partition"] == partition)
    ]
    if not targets:
        return {"restored": 0, "verified": True, "partitions": []}

    loaded = []
    for m in targets:
        try:
            records = _read_partition(m["path"])
        except OSError:
            return {"restored": 0, "verified": False, "partitions": [], "error": m["partition"]}
        rows = [row for _, row in records]
        if len(rows) != m["count"] or _checksum(rows) != m["checksum"]:
            return {"restored": 0, "verified": False, "partitions": [], "error": m["partition"]}
        loaded.append((m, records))

    restored = 0
    for m, records in loaded:
        for seq, row in records:
            _rows.append(row)
            _order[id(row)] = seq
            restored += 1
        os.remove(m["path"])
        _meta.remove(m)
    _save_meta()
    return {
        "restored": restored,
        "verified": True,
        "partitions": [m["partition"] for m, _ in loaded],
    }


def last_route():
    """最近一次 query 的路由信息：热层命中、扫描/裁剪的冷分区、合并行数。"""
    return dict(_last_route)
