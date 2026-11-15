"""增量字段画像：按批更新均值、分位数（草图）与空值率，异常按分位围栏判定。"""

import json
import math
import os
from collections import deque

from .sketch import QuantileSketch

_DEFAULTS = {
    "keep_versions": 5,        # 历史画像保留的版本数
    "min_samples": 4,          # 进入 IQR 模式所需的最少样本数
    "multiplier": 1.5,         # IQR 围栏倍数
    "lower_quantile": 0.25,
    "upper_quantile": 0.75,
    "absolute_threshold": 100.0,  # 冷启动退回的单侧上限阈值（兼容旧行为）
    "max_outlier_samples": 100,   # 异常样本保留条数上限
    "sketch_capacity": 128,
    "storage_dir": None,       # 画像落盘目录，None 表示不落盘
}

_config = dict(_DEFAULTS)
_field_thresholds = {}  # field -> 覆盖的阈值项
_fields = {}
_total_rows = 0
_version = 0
_history = deque(maxlen=_DEFAULTS["keep_versions"])


def _category(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "other"


class _FieldStats:
    __slots__ = ("type", "version", "rows", "null_count", "n_numeric",
                 "mean", "_m2", "min", "max", "sketch",
                 "outliers", "outlier_count")

    def __init__(self):
        self.type = None
        self.version = 0
        self.reset_stats()

    def reset_stats(self):
        self.rows = 0
        self.null_count = 0
        self.n_numeric = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.min = None
        self.max = None
        self.sketch = QuantileSketch(_config["sketch_capacity"])
        self.outliers = []
        self.outlier_count = 0

    def add_number(self, value):
        v = float(value)
        self.n_numeric += 1
        delta = v - self.mean
        self.mean += delta / self.n_numeric
        self._m2 += delta * (v - self.mean)
        self.min = v if self.min is None else min(self.min, v)
        self.max = v if self.max is None else max(self.max, v)
        self.sketch.add(v)


def configure(storage_dir=None, keep_versions=None, min_samples=None,
              multiplier=None, absolute_threshold=None,
              max_outlier_samples=None, sketch_capacity=None,
              field_thresholds=None):
    """全局配置；field_thresholds 按字段覆盖阈值，如
    {"latency": {"multiplier": 3.0, "absolute_threshold": 50.0}}。"""
    global _history
    updates = {
        "storage_dir": storage_dir,
        "keep_versions": keep_versions,
        "min_samples": min_samples,
        "multiplier": multiplier,
        "absolute_threshold": absolute_threshold,
        "max_outlier_samples": max_outlier_samples,
        "sketch_capacity": sketch_capacity,
    }
    for key, value in updates.items():
        if value is not None:
            _config[key] = value
    if keep_versions is not None:
        _history = deque(_history, maxlen=keep_versions)
    if field_thresholds:
        for field, th in field_thresholds.items():
            _field_thresholds.setdefault(field, {}).update(th)
    return dict(_config)


def reset():
    """清空全部状态并恢复默认配置（测试用）。"""
    global _total_rows, _version, _history
    _fields.clear()
    _field_thresholds.clear()
    _config.clear()
    _config.update(_DEFAULTS)
    _total_rows = 0
    _version = 0
    _history = deque(maxlen=_DEFAULTS["keep_versions"])


def _thresholds(field):
    th = {k: _config[k] for k in ("min_samples", "multiplier",
                                  "lower_quantile", "upper_quantile",
                                  "absolute_threshold")}
    th.update(_field_thresholds.get(field, {}))
    return th


def _fences(field, st):
    """返回 (lower, upper, mode)。样本足够且 IQR>0 用分位围栏，
    否则退回绝对阈值（只判上限）；绝对阈值设为 None 则不判异常。"""
    th = _thresholds(field)
    if st.n_numeric >= th["min_samples"]:
        q1 = st.sketch.quantile(th["lower_quantile"])
        q3 = st.sketch.quantile(th["upper_quantile"])
        iqr = q3 - q1
        if iqr > 0:
            k = th["multiplier"]
            return q1 - k * iqr, q3 + k * iqr, "iqr"
    absolute = th["absolute_threshold"]
    if absolute is None:
        return None
    return None, float(absolute), "absolute"


def _is_outlier(value, fences):
    if fences is None:
        return False
    lower, upper, mode = fences
    if mode == "absolute":
        return value > upper
    return value < lower or value > upper


def observe(rows, version=None):
    """按批观察行数据，增量更新各字段画像。每次调用递增数据版本。"""
    global _total_rows, _version
    rows = [dict(row) for row in rows]
    _version = version if version is not None else _version + 1
    _total_rows += len(rows)

    fields = set(_fields)
    for row in rows:
        fields.update(row)

    for field in sorted(fields):
        values = [row.get(field) for row in rows]
        present = any(field in row for row in rows)
        _update_field(field, values, present)

    snapshot = _snapshot()
    _history.append(snapshot)
    _persist(snapshot)
    return {"observed": len(rows), "total": _total_rows, "version": _version}


def _update_field(field, values, present):
    st = _fields.get(field)
    if st is None:
        st = _fields[field] = _FieldStats()
    st.rows += len(values)
    st.null_count += sum(1 for v in values if v is None)
    if not present:
        return
    st.version = _version

    cats = {_category(v) for v in values}
    cats.discard("null")
    if cats:
        new_type = cats.pop() if len(cats) == 1 else "mixed"
        if st.type is None:
            st.type = new_type
        elif new_type != st.type:
            # 字段类型变化：重置统计，从本批重新累计
            st.reset_stats()
            st.type = new_type
            st.rows = len(values)
            st.null_count = sum(1 for v in values if v is None)

    numbers = [v for v in values if _category(v) == "number"]
    for v in numbers:
        st.add_number(v)

    fences = _fences(field, st)
    if fences is not None:
        for v in numbers:
            if _is_outlier(float(v), fences):
                st.outlier_count += 1
                if len(st.outliers) < _config["max_outlier_samples"]:
                    st.outliers.append(v)  # 保留原值


def profile(field):
    """返回字段画像：统计、分位数、异常阈值与异常样本。"""
    st = _fields.get(field)
    if st is None:
        return {
            "field": field, "version": _version, "count": _total_rows,
            "type": None, "nulls": _total_rows,
            "null_rate": 1.0 if _total_rows else 0.0,
            "numeric_count": 0, "mean": 0.0, "stddev": 0.0,
            "min": None, "max": None, "quantiles": {},
            "outlier_threshold": None, "outliers": [], "outlier_count": 0,
            "updated": False,
        }
    quantiles = {}
    if st.n_numeric:
        for name, p in (("p25", 0.25), ("p50", 0.5), ("p75", 0.75),
                        ("p95", 0.95), ("p99", 0.99)):
            quantiles[name] = st.sketch.quantile(p)
    stddev = math.sqrt(st._m2 / (st.n_numeric - 1)) if st.n_numeric > 1 else 0.0
    fences = _fences(field, st)
    threshold = None
    if fences is not None:
        lower, upper, mode = fences
        threshold = {"mode": mode, "lower": lower, "upper": upper}
    return {
        "field": field,
        "version": _version,
        "count": _total_rows,
        "type": st.type,
        "nulls": st.null_count,
        "null_rate": st.null_count / st.rows if st.rows else 0.0,
        "numeric_count": st.n_numeric,
        "mean": st.mean,
        "stddev": stddev,
        "min": st.min,
        "max": st.max,
        "quantiles": quantiles,
        "outlier_threshold": threshold,
        "outliers": list(st.outliers),
        "outlier_count": st.outlier_count,
        "updated": st.version == _version,
    }


def export_profile(field=None, path=None):
    """导出画像：单字段或全量快照；给了 path 就写 JSON 文件。"""
    data = profile(field) if field is not None else _snapshot()
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, default=str)
    return data


def history():
    """最近的画像快照，最多保留 keep_versions 个版本。"""
    return list(_history)


def _snapshot():
    return {
        "version": _version,
        "total_rows": _total_rows,
        "fields": {field: profile(field) for field in sorted(_fields)},
    }


def _persist(snapshot):
    storage = _config["storage_dir"]
    if not storage:
        return
    os.makedirs(storage, exist_ok=True)
    name = "profile_v%s.json" % (_version,)
    with open(os.path.join(storage, name), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(storage, "latest.json"), "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=str)
    kept = sorted(f for f in os.listdir(storage)
                  if f.startswith("profile_v") and f.endswith(".json"))
    while len(kept) > _config["keep_versions"]:
        os.remove(os.path.join(storage, kept.pop(0)))
