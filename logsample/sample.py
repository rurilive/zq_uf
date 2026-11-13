"""分级采样：错误和慢请求全留，正常请求按比例采样，总量可还原。"""
import contextlib
import json
import math

ERROR_STATUS = 500

_rows = []
_counts = {}    # (site, hour) -> 精确计数与估算累加器
_rates = {}     # (site, hour) 或 (site, None) 站点级 或 (None, None) 全局
_batches = {}   # batch_id -> 保留的行
_sampling = False
_batch = 0


def reset():
    global _sampling, _batch
    _rows.clear()
    _counts.clear()
    _rates.clear()
    _batches.clear()
    _sampling = False
    _batch = 0


def enable_sampling(rate=None, site=None, hour=None):
    """开启采样，可同时设置比例（默认全局，可指定站点/小时）。"""
    global _sampling
    if rate is not None:
        set_rate(rate, site=site, hour=hour)
    _sampling = True


def disable_sampling():
    """关闭采样，恢复全量写入。"""
    global _sampling
    _sampling = False


@contextlib.contextmanager
def sampling_off():
    """异常批次临时关闭采样，退出后自动恢复。"""
    global _sampling
    was = _sampling
    _sampling = False
    try:
        yield
    finally:
        _sampling = was


def set_rate(rate, site=None, hour=None):
    """动态调比例：(site, hour) 最优先，站点级其次，都不给为全局默认。"""
    if not 0.0 <= rate <= 1.0:
        raise ValueError("rate 必须在 [0, 1] 之间")
    _rates[(site, hour)] = float(rate)


def get_rate(site="default", hour=None):
    """查询当前生效的采样比例。"""
    for key in ((site, hour), (site, None), (None, None)):
        if key in _rates:
            return _rates[key]
    return 1.0


def _new_stat():
    return {"total": 0, "kept": 0, "eligible": 0, "sampled": 0,
            "exact": 0, "est": 0.0, "var": 0.0}


def _is_error(row):
    return row.get("status", 200) >= ERROR_STATUS


def _is_slow(row):
    return bool(row.get("slow"))


def write(record):
    """写入一条记录；采样开启时把决策写进记录，丢弃的不落盘。"""
    row = dict(record)
    site = row.get("site", "default")
    hour = row.get("hour")
    stat = _counts.setdefault((site, hour), _new_stat())
    stat["total"] += 1
    if not _sampling:
        stat["kept"] += 1
        stat["exact"] += 1
        _rows.append(row)
        _batches.setdefault(_batch, []).append(row)
        return row
    rate = get_rate(site, hour)
    if _is_error(row) or _is_slow(row):
        keep = True
        reason = "error" if _is_error(row) else "slow"
        stat["exact"] += 1
    else:
        stat["eligible"] += 1
        n = stat["eligible"]
        keep = math.floor(n * rate) > math.floor((n - 1) * rate)
        reason = "sampled" if keep else "dropped"
        if keep:
            stat["sampled"] += 1
            stat["est"] += 1.0 / rate
            stat["var"] += (1.0 - rate) / (rate * rate)
    row["sampled"] = keep
    row["sample_reason"] = reason
    row["sample_rate"] = rate
    row["sample_key"] = "%s:%s" % (site, hour if hour is not None else "*")
    row["batch"] = _batch
    if keep:
        stat["kept"] += 1
        _rows.append(row)
        _batches.setdefault(_batch, []).append(row)
    return row


def lines():
    return [json.dumps(row, ensure_ascii=False) for row in _rows]


def begin_batch():
    """开新批次，返回批次号；之后写入的记录带上该批次号。"""
    global _batch
    _batch += 1
    return _batch


def replay(batch_id):
    """按批次回放：返回该批次保留的记录（含采样决策）。"""
    return [dict(row) for row in _batches.get(batch_id, [])]


def _aggregate(site):
    merged = {}
    for (s, _h), st in _counts.items():
        if site is not None and s != site:
            continue
        acc = merged.setdefault(s, _new_stat())
        for field, value in st.items():
            acc[field] += value
    return merged


def stats(site=None):
    """每个站点的精确计数，可按站点过滤。"""
    return {s: {k: v for k, v in st.items() if k not in ("est", "var")}
            for s, st in _aggregate(site).items()}


def estimate(site=None):
    """按站点推算总量：Horvitz-Thompson 估计，误差为 1 倍标准差。"""
    result = {}
    for s, st in _aggregate(site).items():
        result[s] = {
            "site": s,
            "total": st["total"],
            "kept": st["kept"],
            "estimated_total": st["est"] + st["exact"],
            "error": math.sqrt(st["var"]),
            "rate": get_rate(s),
        }
    return result
