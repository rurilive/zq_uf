"""数据倾斜检测：按分区和键统计行数与耗时，按中位数倍数判定倾斜。

- observe(task) 记录一次观测并给出倾斜判断、热点清单和处置建议；
- 任务重跑只追加历史，不覆盖；
- 检测周期可配（configure(period=秒)），周期内复用上次结果；
- replay(task) 基于留存的观测重新检测，可回放。
"""

import math
import time

_config = {"threshold": 3.0, "period": 0.0}
_observations = {}  # name -> [observation, ...]
_detections = {}    # name -> [detection, ...]


def configure(threshold=None, period=None):
    """配置倾斜倍数阈值与检测周期（秒）。"""
    if threshold is not None:
        _config["threshold"] = float(threshold)
    if period is not None:
        _config["period"] = float(period)
    return dict(_config)


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    return float(xs[mid]) if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def _ratio(value, median):
    if median > 0:
        return value / median
    return math.inf if value > 0 else 1.0


def _key_rows(keys):
    out = {}
    for k, v in (keys or {}).items():
        out[k] = float(v.get("rows", 0)) if isinstance(v, dict) else float(v)
    return out


def _detect(name, obs):
    """对一次观测做倾斜检测，返回判断、热点清单和建议。"""
    elapsed = float(obs.get("elapsed", 0))
    parts = obs.get("partitions") or []
    if not parts:
        return {"task": name, "elapsed": elapsed, "skewed": False,
                "ratio": 1.0, "hot": [], "hot_partitions": [], "advice": []}

    threshold = _config["threshold"]
    total_rows = sum(float(p.get("rows", 0)) for p in parts) or 1.0

    # 分区维度：行数 / 耗时 与中位数的倍数
    part_rows = [float(p.get("rows", 0)) for p in parts]
    med_rows = _median(part_rows)
    hot_parts = []
    for p, rows in zip(parts, part_rows):
        r = _ratio(rows, med_rows)
        if r >= threshold:
            hot_parts.append({"partition": p.get("id"), "rows": rows,
                              "elapsed": float(p.get("elapsed", 0)),
                              "ratio": round(r, 2)})
    hot_parts.sort(key=lambda h: h["ratio"], reverse=True)

    # 键维度：跨分区聚合每个键的行数，记录键落在哪些分区
    key_rows, key_parts = {}, {}
    for p in parts:
        for k, rows in _key_rows(p.get("keys")).items():
            key_rows[k] = key_rows.get(k, 0.0) + rows
            key_parts.setdefault(k, set()).add(p.get("id"))
    med_key = _median(list(key_rows.values()))
    hot_keys = []
    for k, rows in key_rows.items():
        r = _ratio(rows, med_key)
        if r >= threshold:
            hot_keys.append({"key": k, "rows": rows, "ratio": round(r, 2),
                             "partitions": sorted(key_parts[k], key=str)})
    hot_keys.sort(key=lambda h: h["ratio"], reverse=True)

    # 处置建议：热点键加盐、热点分区拆分，附预估收益（秒）
    advice = []
    for h in hot_keys:
        n = max(2, math.ceil(h["ratio"]))
        benefit = elapsed * (h["rows"] / total_rows) * (1 - 1 / h["ratio"])
        advice.append({"type": "salt", "key": h["key"], "ratio": h["ratio"],
                       "suggest": f"对热点键 '{h['key']}' 加盐拆分为 {n} 个子键",
                       "est_benefit": round(benefit, 2)})
    for h in hot_parts:
        benefit = elapsed * (h["rows"] / total_rows) * (1 - 1 / h["ratio"])
        advice.append({"type": "split", "partition": h["partition"],
                       "ratio": h["ratio"],
                       "suggest": f"拆分热点分区 {h['partition']}（行数达中位数 {h['ratio']} 倍）",
                       "est_benefit": round(benefit, 2)})

    ratio = max((_ratio(r, med_rows) for r in part_rows), default=1.0)
    return {"task": name, "elapsed": elapsed,
            "skewed": bool(hot_parts or hot_keys),
            "ratio": round(ratio, 2) if math.isfinite(ratio) else math.inf,
            "hot": hot_keys, "hot_partitions": hot_parts, "advice": advice}


def observe(task):
    """记录一次观测并返回倾斜检测结果；周期内复用上次检测。"""
    name = task.get("name", "")
    obs = dict(task)
    obs["time"] = float(task.get("time", time.time()))
    _observations.setdefault(name, []).append(obs)

    last = _detections.get(name, [None])[-1]
    if last and _config["period"] > 0 and obs["time"] - last["time"] < _config["period"]:
        return dict(last["result"], cached=True)

    result = _detect(name, obs)
    _detections.setdefault(name, []).append({"time": obs["time"], "result": result})
    return result


def report(task):
    """返回最近一次检测结果及历史次数；重跑不覆盖历史。"""
    entries = _detections.get(task, [])
    if not entries:
        return {"task": task, "elapsed": 0.0, "skewed": False, "ratio": 1.0,
                "hot": [], "hot_partitions": [], "advice": [], "runs": 0}
    return dict(entries[-1]["result"], runs=len(entries))


def history(task):
    """按时间返回全部历史检测结果，最旧在前。"""
    return [{"time": e["time"], **e["result"]} for e in _detections.get(task, [])]


def replay(task):
    """基于留存的观测逐条重新检测，用于回放验证。"""
    return [{"time": o["time"], **_detect(task, o)}
            for o in _observations.get(task, [])]
