"""指标回填：分片重算 + 撤销重写 + 对账停批。

口径：
- 切片：按 shard_days 切时间窗，再按 dims 各维度取值做笛卡尔积切片；
  range_spec 里的 rows 按片均分（余数分给前几片）。
- 撤销重写：每片先撤销（删除）目标表中同区间的旧行，再重算写入，重复执行幂等。
- 对账：写入前对比本片原值与重算值的行数、值和校验和；
  abs(row_diff) 或 abs(value_diff) 超过 threshold 即停批，
  本片回滚保留原值，已回填分片保留。
"""

import zlib
from datetime import date
from itertools import product


def _to_days(start, end):
    """把 start/end 归一成序号，支持整数与 YYYY-MM-DD 字符串。"""
    if isinstance(start, str):
        s = date.fromisoformat(start)
        e = date.fromisoformat(end)
        return s.toordinal(), e.toordinal(), lambda d: date.fromordinal(d).isoformat()
    return int(start), int(end), lambda d: d


def _checksum(rows):
    h = 0
    for row in sorted(rows, key=repr):
        h = zlib.crc32(repr(sorted(row.items())).encode("utf-8"), h)
    return h


def _aggregate(rows):
    return {
        "rows": len(rows),
        "value": sum(r.get("value", 0) for r in rows),
        "checksum": _checksum(rows),
    }


def _split(total, n):
    """把整数 total 均分成 n 份，余数分给前几份。"""
    if total is None or n == 0:
        return None
    base, rem = divmod(int(total), n)
    return [base + (1 if i < rem else 0) for i in range(n)]


class MetricStore:
    """内存指标表。行是 dict：time（可选）、dims（dict）、value。"""

    def __init__(self, rows=()):
        self.rows = [dict(r) for r in rows]

    @staticmethod
    def _match(row, start, end, dims):
        t = row.get("time")
        if start is not None and t is not None and t < start:
            return False
        if end is not None and t is not None and t > end:
            return False
        row_dims = row.get("dims") or {}
        return all(row_dims.get(k) == v for k, v in (dims or {}).items())

    def read(self, start=None, end=None, dims=None):
        return [r for r in self.rows if self._match(r, start, end, dims)]

    def delete(self, start=None, end=None, dims=None):
        removed = self.read(start, end, dims)
        self.rows = [r for r in self.rows if not self._match(r, start, end, dims)]
        return removed

    def write(self, rows):
        self.rows.extend(dict(r) for r in rows)


class Ledger:
    """回填记录，按分片保留，支持按区间查询。"""

    def __init__(self):
        self.records = []

    def extend(self, records):
        self.records.extend(dict(r) for r in records)

    def query(self, start=None, end=None):
        """按时间区间查询分片记录，区间相交即命中；无时间的记录始终命中。"""
        out = []
        for r in self.records:
            s, e = r.get("start"), r.get("end")
            if start is not None and e is not None and e < start:
                continue
            if end is not None and s is not None and s > end:
                continue
            out.append(dict(r))
        return out


def plan(range_spec):
    """按时间和维度把 range_spec 切片，返回 {"range", "shards", "threshold"}。"""
    spec = dict(range_spec)
    # 时间切片
    if "start" in spec and "end" in spec and spec.get("shard_days"):
        s, e, render = _to_days(spec["start"], spec["end"])
        step = int(spec["shard_days"])
        windows = []
        cur = s
        while cur <= e:
            windows.append((render(cur), render(min(cur + step - 1, e))))
            cur += step
    elif "start" in spec and "end" in spec:
        windows = [(spec["start"], spec["end"])]
    else:
        windows = [(None, None)]
    # 维度切片：list 取值做笛卡尔积
    dim_spec = spec.get("dims") or {}
    dim_names = sorted(dim_spec)
    dim_values = [
        v if isinstance(v, (list, tuple)) else [v]
        for k in dim_names
        for v in [dim_spec[k]]
    ]
    combos = [dict(zip(dim_names, vals)) for vals in product(*dim_values)] or [{}]
    rows_per = _split(spec.get("rows"), len(windows) * len(combos))
    shards = []
    for i, ((ws, we), dims) in enumerate(product(windows, combos)):
        shard = {"shard_id": i, "dims": dims}
        if ws is not None:
            shard["start"], shard["end"] = ws, we
        if rows_per is not None:
            shard["rows"] = rows_per[i]
        shards.append(shard)
    return {"range": spec, "shards": shards, "threshold": spec.get("threshold")}


def _recompute(shard, source, start, end, dims):
    """重算一片：有 source 从源表取数，否则按 shard 的 rows 合成。"""
    if source is not None:
        return [dict(r) for r in source.read(start, end, dims)]
    n = shard.get("rows", 0)
    per = shard.get("value", n) / n if n else 0
    return [{"time": start, "dims": dict(dims), "value": per} for _ in range(n)]


def apply(plan_payload, store=None, source=None, ledger=None):
    """逐片执行：撤销旧值 -> 重算 -> 对账 -> 写入；超阈值停批并回滚本片。"""
    threshold = plan_payload.get("threshold")
    results, records = [], []
    total_rows = 0
    stopped, stop_reason = False, None
    for shard in plan_payload["shards"]:
        start, end = shard.get("start"), shard.get("end")
        dims = shard.get("dims") or {}
        removed = store.delete(start, end, dims) if store is not None else []
        original = _aggregate(removed)
        rows = _recompute(shard, source, start, end, dims)
        new = _aggregate(rows)
        diff = {
            "row_diff": new["rows"] - original["rows"],
            "value_diff": new["value"] - original["value"],
            "checksum_match": new["checksum"] == original["checksum"],
        }
        over = threshold is not None and (
            abs(diff["row_diff"]) > threshold or abs(diff["value_diff"]) > threshold
        )
        if over:
            if store is not None:
                store.write(removed)  # 回滚本片，保留已回填分片
            status = "rejected"
            stopped = True
            stop_reason = (
                "shard %s 对账差异超阈值: row_diff=%s value_diff=%s threshold=%s"
                % (shard["shard_id"], diff["row_diff"], diff["value_diff"], threshold)
            )
        else:
            if store is not None:
                store.write(rows)
            status = "applied"
            total_rows += new["rows"]
        record = {
            "shard_id": shard["shard_id"],
            "start": start,
            "end": end,
            "dims": dims,
            "status": status,
            "rows": new["rows"],
            "value": new["value"],
            "original_rows": original["rows"],
            "original_value": original["value"],
            "checksum": new["checksum"],
        }
        record.update(diff)
        results.append(record)
        records.append(record)
        if stopped:
            break
    if ledger is not None:
        ledger.extend(records)
    return {
        "rows": total_rows,
        "shards": len(results),
        "results": results,
        "stopped": stopped,
        "stop_reason": stop_reason,
        "records": records,
    }
