"""增量字段画像。

- ``observe`` 只把一批行投递到后台线程，采集侧不会被画像计算阻塞；
- 每批只保留聚合结果：Welford 均值/方差、可合并的 TDigest 分位数、
  空值计数与受限的取值频次，不再保留全表；
- 异常值阈值按「分位 + 倍数」计算并可配置，命中样本落盘保留，支持人工复核；
- 每次批量更新都会推进版本、写入历史快照并可自动保存，支持导出、查询与回放。
"""

from __future__ import annotations

import copy
import json
import math
import os
import queue
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from .digest import TDigest


# --------------------------------------------------------------------------- 配置
@dataclass
class OutlierPolicy:
    """异常值判定：以两个分位数为基准，用倍数扩张成上下界（Tukey 围栏）。

    ``upper = q(high) + factor * (q(high) - q(low))``
    ``lower = q(low)  - factor * (q(high) - q(low))``

    默认取 p99/p01、倍数 2，属于分位和倍数共同决定的自适应阈值：长尾业务
    整体抬高时阈值同步抬升，不会像写死的常量那样频繁误报；含 0 的有界指标
    也不会因为除法下界而被误判。数值样本少于 ``min_samples`` 时先不判定。
    """

    upper_quantile: float = 0.99
    lower_quantile: float = 0.01
    factor: float = 2.0
    min_samples: int = 20
    max_samples: int = 100

    def __post_init__(self):
        for name in ("lower_quantile", "upper_quantile"):
            value = getattr(self, name)
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError("%s must be in [0, 1]" % name)
        if self.lower_quantile > self.upper_quantile:
            raise ValueError("lower_quantile must not exceed upper_quantile")
        if float(self.factor) <= 0:
            raise ValueError("factor must be positive")
        if int(self.min_samples) < 1:
            raise ValueError("min_samples must be positive")
        if int(self.max_samples) < 1:
            raise ValueError("max_samples must be positive")

    def merged(self, **overrides) -> "OutlierPolicy":
        data = asdict(self)
        data.update({k: v for k, v in overrides.items() if v is not None})
        return OutlierPolicy(**data)


DEFAULT_QUANTILES = (0.01, 0.5, 0.9, 0.95, 0.99)


# --------------------------------------------------------------------------- 工具
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _category(value) -> Optional[str]:
    """返回类型类别；None 表示空值。int/float 统一算 number。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return "number" if math.isfinite(value) else "other"
    if isinstance(value, str):
        return "str"
    return "other"


def _quantile_key(q: float) -> str:
    pct = float(q) * 100.0
    if pct == int(pct):
        return "p%02d" % int(pct)
    return ("p%.2f" % pct).rstrip("0")


def _relative_error(incremental, full, scale=None) -> Optional[float]:
    """相对误差；``scale`` 为分布尺度，避免真值接近 0 时误差被放大。"""
    if incremental is None or full is None:
        return None
    denom = max(abs(full), abs(scale or 0.0), 1e-12)
    return abs(incremental - full) / denom


def _outlier_bounds(digest: TDigest, policy: OutlierPolicy):
    """按分位和倍数（Tukey 围栏）计算异常上下界，返回 ``(lower, upper)``。"""
    high = digest.quantile(policy.upper_quantile)
    low = digest.quantile(policy.lower_quantile)
    if high is None or low is None:
        return None, None
    spread = high - low
    if not math.isfinite(spread) or spread <= 0:
        # 分布退化或数值溢出时退回到四分位距，再不行就用整体范围
        q1 = digest.quantile(0.25)
        q3 = digest.quantile(0.75)
        spread = (q3 - q1) if q1 is not None and q3 is not None else 0.0
    if not math.isfinite(spread) or spread <= 0:
        if digest.min is None or digest.max is None:
            return None, None
        spread = digest.max - digest.min
        high, low = digest.max, digest.min
    if not math.isfinite(spread) or spread <= 0:
        # 确实无法得到有限尺度时宁可不判定，也不要用 inf 阈值放过一切
        return None, None
    lower = low - policy.factor * spread
    upper = high + policy.factor * spread
    if not math.isfinite(lower) or not math.isfinite(upper):
        return None, None
    return lower, upper


def _is_outlier(value: float, lower, upper) -> bool:
    return (upper is not None and value > upper) or (lower is not None and value < lower)


def _finite(value):
    """把 inf/nan 换成 None，保证 JSON 导出不会因为 allow_nan=False 报错。"""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _scaled_m2(values, mean):
    """平方离差和；数值跨度极大时按比例缩放，避免中间结果溢出。

    先算归一化后的平方和（可能极小），再分两次乘回 scale，避免 ``scale²``
    在 ``scale`` 接近浮点上界时直接溢出。
    """
    scale = max((abs(v) for v in values), default=0.0)
    if scale <= 0 or not math.isfinite(scale):
        return 0.0
    try:
        normalized = math.fsum(((v - mean) / scale) ** 2 for v in values)
        result = (scale * normalized) * scale
        return result if math.isfinite(result) else math.inf
    except (OverflowError, ValueError):
        return math.inf


def _safe_mean(values):
    """均值；直接求和会溢出时先按最大绝对值缩放。"""
    scale = max((abs(v) for v in values), default=0.0)
    if scale <= 0:
        return 0.0
    try:
        mean = math.fsum(values) / len(values)
        if math.isfinite(mean):
            return mean
    except (OverflowError, ValueError):
        pass
    return scale * (math.fsum(v / scale for v in values) / len(values))


# --------------------------------------------------------------------------- 字段状态
class _Field:
    """单个字段的增量聚合状态。"""

    def __init__(self, name, compression=100, exact_limit=200,
                 distinct_limit=100, top_limit=10):
        self.name = name
        self.distinct_limit = distinct_limit
        self.top_limit = top_limit
        self.compression = compression
        self.exact_limit = exact_limit
        self.category = None
        self.count = 0
        self.null_count = 0
        self.type_counts: Dict[str, int] = {}
        self.digest = TDigest(compression, exact_limit)
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = None
        self.max = None
        self.top: Dict[str, int] = {}
        self.top_overflow = 0
        self.outliers: List[dict] = []
        self.outlier_total = 0
        self.version = 0
        self.updated_at = None
        self.resets: List[dict] = []

    # ---------------------------------------------------------------- 更新
    def reset(self, category, version, now, reason="type_change"):
        previous = self.category
        self.category = category
        self.count = 0
        self.null_count = 0
        self.type_counts = {}
        self.digest = TDigest(self.compression, self.exact_limit)
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.min = None
        self.max = None
        self.top = {}
        self.top_overflow = 0
        self.outliers = []
        self.outlier_total = 0
        self.version = version
        self.updated_at = now
        self.resets.append({"version": version, "at": now,
                            "from": previous, "to": category, "reason": reason})
        if len(self.resets) > 20:
            del self.resets[0:len(self.resets) - 20]

    def count_value(self, category):
        self.count += 1
        self.type_counts[category] = self.type_counts.get(category, 0) + 1

    def count_null(self):
        self.count += 1
        self.null_count += 1
        self.type_counts["null"] = self.type_counts.get("null", 0) + 1

    def add_number(self, values, version, now):
        """用一批数值更新 Welford 统计和分位草图。"""
        values = [float(v) for v in values]
        if not values:
            return

        self.digest.add_many(values)

        batch_n = len(values)
        batch_mean = _safe_mean(values)
        self._merge_moments(batch_mean, _scaled_m2(values, batch_mean), batch_n)
        batch_min = min(values)
        batch_max = max(values)
        self.min = batch_min if self.min is None else min(self.min, batch_min)
        self.max = batch_max if self.max is None else max(self.max, batch_max)
        self.version = version
        self.updated_at = now

    def _merge_moments(self, batch_mean, batch_m2, batch_n):
        """Welford 并行合并；溢出时把方差记为 inf（输出层会转成 None）。"""
        if self.n == 0:
            self.mean = batch_mean
            self.m2 = batch_m2
        else:
            total = self.n + batch_n
            delta = batch_mean - self.mean
            mean = self.mean + delta * batch_n / total
            correction = delta * delta * self.n * batch_n / total
            m2 = self.m2 + batch_m2 + correction
            self.mean = mean if math.isfinite(mean) else batch_mean
            self.m2 = m2 if math.isfinite(m2) else math.inf
        self.n += batch_n

    def add_top(self, value):
        try:
            if isinstance(value, str):
                key = value[:128]
            elif isinstance(value, (int, float, bool)):
                key = repr(value)
            else:
                key = type(value).__name__ + ":" + repr(value)[:96]
        except Exception:  # 坏掉的 __repr__ 只记类型，不让整批失败
            key = type(value).__name__
        if key in self.top:
            self.top[key] += 1
        elif len(self.top) < self.distinct_limit:
            self.top[key] = 1
        else:
            self.top_overflow += 1

    def add_outliers(self, samples, version, now):
        for value, lower, upper in samples:
            self.outlier_total += 1
            if len(self.outliers) >= self._sample_cap:
                self._evict_one()
            self.outliers.append({
                "id": uuid.uuid4().hex[:12],
                "value": _finite(value),
                "lower": _finite(lower),
                "upper": _finite(upper),
                "version": version,
                "observed_at": now,
                "review": None,
            })

    def _evict_one(self):
        """优先淘汰最旧的未复核样本，避免把人工结论挤出缓冲区。"""
        for index, sample in enumerate(self.outliers):
            if sample["review"] is None:
                del self.outliers[index]
                return
        self.outliers.pop(0)

    _sample_cap = 100

    # ---------------------------------------------------------------- 查询
    @property
    def variance(self):
        return self.m2 / self.n if self.n else None

    @property
    def std(self):
        var = self.variance
        return math.sqrt(var) if var is not None else None

    def quantiles(self, quantiles):
        return {_quantile_key(q): self.digest.quantile(q) for q in quantiles}

    def profile_dict(self, quantiles=DEFAULT_QUANTILES):
        numeric = None
        if self.category == "number" and self.n:
            numeric = {
                "count": self.n,
                "mean": _finite(self.mean),
                "variance": _finite(self.variance),
                "std": _finite(self.std),
                "min": _finite(self.min),
                "max": _finite(self.max),
                "quantiles": {k: _finite(v)
                              for k, v in self.quantiles(quantiles).items()},
            }
        top_values = None
        if self.category in ("str", "bool", "other"):
            ranked = sorted(self.top.items(), key=lambda kv: (-kv[1], kv[0]))
            top_values = [{"value": k, "count": v} for k, v in ranked[:self.top_limit]]
            if self.top_overflow:
                top_values.append({"value": "<其他>", "count": self.top_overflow})
        return {
            "field": self.name,
            "category": self.category,
            "count": self.count,
            "null_count": self.null_count,
            "null_ratio": (self.null_count / self.count) if self.count else 0.0,
            "type_counts": dict(sorted(self.type_counts.items())),
            "numeric": numeric,
            "top_values": top_values,
            "outlier_count": self.outlier_total,
            "pending_reviews": sum(1 for s in self.outliers if s["review"] is None),
            "version": self.version,
            "updated_at": self.updated_at,
            "resets": [dict(r) for r in self.resets],
        }

    def outliers_copy(self) -> List[dict]:
        return [copy.deepcopy(sample) for sample in self.outliers]

    # ---------------------------------------------------------------- 序列化
    def to_dict(self):
        return {
            "name": self.name,
            "compression": self.compression,
            "exact_limit": self.exact_limit,
            "distinct_limit": self.distinct_limit,
            "top_limit": self.top_limit,
            "category": self.category,
            "count": self.count,
            "null_count": self.null_count,
            "type_counts": dict(self.type_counts),
            "digest": self.digest.to_dict(),
            "n": self.n,
            "mean": self.mean,
            "m2": _finite(self.m2),
            "min": _finite(self.min),
            "max": _finite(self.max),
            "top": dict(self.top),
            "top_overflow": self.top_overflow,
            "outliers": copy.deepcopy(self.outliers),
            "outlier_total": self.outlier_total,
            "sample_cap": self._sample_cap,
            "version": self.version,
            "updated_at": self.updated_at,
            "resets": [dict(r) for r in self.resets],
        }

    @classmethod
    def from_dict(cls, data):
        state = cls(data.get("name", ""),
                    compression=data.get("compression", 100),
                    exact_limit=data.get("exact_limit", 200),
                    distinct_limit=data.get("distinct_limit", 100))
        state.top_limit = int(data.get("top_limit", state.top_limit))
        state.category = data.get("category")
        state.count = int(data.get("count", 0))
        state.null_count = int(data.get("null_count", 0))
        state.type_counts = dict(data.get("type_counts", {}))
        state.digest = TDigest.from_dict(data.get("digest", {}))
        state.n = int(data.get("n", 0))
        state.mean = float(data.get("mean") or 0.0)
        m2 = data.get("m2")
        state.m2 = float("inf") if m2 is None else float(m2)
        state.min = data.get("min")
        state.max = data.get("max")
        state.top = dict(data.get("top", {}))
        state.top_overflow = int(data.get("top_overflow", 0))
        state.outliers = copy.deepcopy(data.get("outliers", []))
        state.outlier_total = int(data.get("outlier_total", 0))
        state._sample_cap = int(data.get("sample_cap", state._sample_cap))
        state.version = int(data.get("version", 0))
        state.updated_at = data.get("updated_at")
        state.resets = [dict(r) for r in data.get("resets", [])]
        return state


# --------------------------------------------------------------------------- 画像器
class Profiler:
    """增量画像器。

    参数
    ----
    path: 自动保存路径，``None`` 表示不落盘。
    autosave: 每批更新后是否写盘。
    history_limit: 历史快照最多保留的版本数。
    history_every: 每多少个版本写一次快照（类型变化总会写）。
    quantiles: 输出哪些分位数。
    policy: 全局异常值策略，可用 ``set_policy`` 按字段覆盖。
    """

    def __init__(self, path=None, autosave=True, history_limit=5,
                 history_every=1, quantiles=DEFAULT_QUANTILES,
                 policy: Optional[OutlierPolicy] = None, distinct_limit=100,
                 top_limit=10, digest_compression=100, digest_exact_limit=200):
        self.path = path
        self.autosave = autosave
        self.history_limit = max(1, int(history_limit))
        self.history_every = max(1, int(history_every))
        self.quantiles = tuple(quantiles)
        self.policy = policy or OutlierPolicy()
        self.distinct_limit = distinct_limit
        self.top_limit = top_limit
        self.digest_compression = digest_compression
        self.digest_exact_limit = digest_exact_limit

        self._lock = threading.RLock()
        self._state: Dict[str, _Field] = {}
        self._policies: Dict[str, OutlierPolicy] = {}
        self._history: List[dict] = []
        self._version = 0
        self._updated_at = None
        self._pending = 0
        self._last_error = None
        self._closed = False

        self._queue: "queue.Queue" = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="profiler2-worker",
                                        daemon=True)
        self._worker.start()
        if path and os.path.exists(path):
            self._load_into_self(path)

    # ---------------------------------------------------------------- 采集侧
    def observe(self, rows: Iterable[dict], fields: Optional[Iterable[str]] = None) -> dict:
        """投递一批行，立即返回，画像在后台增量更新。

        ``fields`` 可只保留关心的字段，避免复制整行。每批行会在调用线程
        物化为列表，但其生命周期只到本批处理结束，不会累积。
        """
        if self._closed:
            raise RuntimeError("profiler is closed")
        wanted = set(fields) if fields is not None else None
        batch = []
        for row in rows:
            if wanted is None:
                batch.append(dict(row))
            else:
                batch.append({k: row[k] for k in wanted if k in row})
        if not batch:
            return {"queued": 0, "pending": self._pending, "version": self._version}

        with self._lock:
            # 与 close() 的检查在同一把锁内完成，避免批次落在停止信号之后。
            if self._closed:
                raise RuntimeError("profiler is closed")
            self._pending += 1
            self._queue.put(batch)
            return {"queued": len(batch), "pending": self._pending,
                    "version": self._version}

    def flush(self, timeout: Optional[float] = None) -> bool:
        """等待已投递批次处理完；``timeout=None`` 表示一直等。"""
        if not self._worker.is_alive():
            return False
        event = threading.Event()
        self._queue.put((event, None))
        return event.wait(timeout)

    # ---------------------------------------------------------------- 后台线程
    def _run(self):
        while True:
            item = self._queue.get()
            try:
                if isinstance(item, tuple):
                    event, action = item
                    event.set()
                    if action == "stop":
                        break
                    continue
                self._apply_batch(item)
            except Exception as exc:  # 采集侧不受影响，错误留待查询时查看
                self._last_error = repr(exc)
            finally:
                self._queue.task_done()

    def _apply_batch(self, rows):
        payload = None
        try:
            observed = set()
            for row in rows:
                observed.update(row.keys())
            if not observed:
                return
            with self._lock:
                self._version += 1
                version = self._version
                now = _now()
                reason = "update"
                for name in sorted(observed):
                    state = self._state.get(name)
                    if state is None:
                        state = _Field(name, self.digest_compression,
                                       self.digest_exact_limit, self.distinct_limit,
                                       self.top_limit)
                        self._state[name] = state
                    values = [row.get(name) for row in rows]
                    if self._apply_field(state, values, version, now):
                        reason = "type_change"
                self._updated_at = now
                if reason == "type_change" or version % self.history_every == 0:
                    self._archive(reason, now)
                if self.autosave and self.path:
                    # 在锁内取快照、锁外写盘：查询不会被磁盘 I/O 拖住。
                    payload = self._snapshot_payload()
            if payload is not None:
                try:
                    self._write(self.path, payload)
                except OSError as exc:
                    self._last_error = repr(exc)
        finally:
            with self._lock:
                self._pending -= 1

    def _policy_for(self, field) -> OutlierPolicy:
        return self._policies.get(field, self.policy)

    def _apply_field(self, state: _Field, values, version, now) -> bool:
        """应用一批值；返回是否发生了类型变化（触发统计重置）。"""
        # 先按整批的多数类型判断是否发生类型变化，单个脏值不会触发重置。
        counts: Dict[str, int] = {}
        for value in values:
            category = _category(value)
            if category is not None:
                counts[category] = counts.get(category, 0) + 1

        changed = False
        if counts:
            dominant = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if state.category is None:
                state.category = dominant
            elif dominant != state.category:
                state.reset(dominant, version, now)
                changed = True
        # 全是空值的批次没有类型可定，但仍要更新空值比例和计数。
        state.version = version
        state.updated_at = now

        numeric = []
        for value in values:
            category = _category(value)
            if category is None:
                state.count_null()
                continue
            # 少数派类型只计入类型分布，不污染主类型的统计量
            state.count_value(category)
            if category == state.category:
                if category == "number":
                    numeric.append(float(value))
                else:
                    state.add_top(value)

        if not numeric:
            return changed

        state.add_number(numeric, version, now)
        policy = self._policy_for(state.name)
        state._sample_cap = policy.max_samples
        if state.digest.count < policy.min_samples:
            return changed
        lower, upper = _outlier_bounds(state.digest, policy)
        hits = [(v, lower, upper) for v in numeric
                if _is_outlier(v, lower, upper)]
        if hits:
            state.add_outliers(hits, version, now)
        return changed

    # ---------------------------------------------------------------- 历史
    def _archive(self, reason, now=None):
        """写一份历史快照。

        快照必须是自包含的：``to_dict`` 会先整理草图的缓冲区，否则两份快照
        可能共享同一批待压缩点，回放时出现串档。
        """
        snapshot = {
            "version": self._version,
            "updated_at": now or self._updated_at or _now(),
            "reason": reason,
            "fields": {name: state.to_dict() for name, state in self._state.items()},
        }
        self._history.append(snapshot)
        if len(self._history) > self.history_limit:
            del self._history[0:len(self._history) - self.history_limit]

    def history(self, field: Optional[str] = None) -> List[dict]:
        with self._lock:
            entries = []
            for snapshot in self._history:
                fields = snapshot["fields"]
                if field is not None and field not in fields:
                    continue
                entries.append({
                    "version": snapshot["version"],
                    "updated_at": snapshot["updated_at"],
                    "reason": snapshot["reason"],
                    "fields": sorted(fields),
                    "field_version": fields.get(field, {}).get("version")
                    if field else None,
                })
            return entries

    def replay(self, version: int) -> dict:
        """把画像恢复到指定版本（按历史快照回放）。

        这是回滚语义：恢复到目标版本后，比它更新的历史快照会被丢弃，后续
        更新从该版本继续，避免出现版本号相同、内容不同的两个快照。
        """
        with self._lock:
            snapshot = self._pick_snapshot(version)
            if snapshot is None:
                raise KeyError("no snapshot for version %r" % (version,))
            self._state = {name: _Field.from_dict(data)
                           for name, data in snapshot["fields"].items()}
            self._version = snapshot["version"]
            self._updated_at = snapshot["updated_at"]
            self._history = [s for s in self._history
                             if s["version"] <= snapshot["version"]]
            if self.autosave and self.path:
                self._save_locked()
            return self._current_summary()

    def _pick_snapshot(self, version):
        best = None
        for snapshot in self._history:
            if snapshot["version"] <= version:
                best = snapshot
            else:
                break
        return best

    # ---------------------------------------------------------------- 查询
    def _snapshot_for(self, field, state: _Field, pending=0):
        return {
            "field": field,
            "profile": state.profile_dict(self.quantiles),
            "outliers": state.outliers_copy(),
            "version": self._version,
            "updated_at": self._updated_at,
            "pending_batches": pending,
            "last_error": self._last_error,
        }

    def _current_summary(self):
        with self._lock:
            return {
                "version": self._version,
                "updated_at": self._updated_at,
                "fields": {name: self._snapshot_for(name, state)
                           for name, state in self._state.items()},
                "pending_batches": self._pending,
            }

    def profile(self, field: Optional[str] = None, wait=True, timeout=5.0):
        """返回画像、异常样本、版本与更新时间。

        ``wait=True`` 会先排空已投递批次，保证结果与采集一致；采集侧调用
        ``observe`` 本身从不等待。
        """
        if wait:
            self.flush(timeout)
        with self._lock:
            if field is not None:
                state = self._state.get(field)
                if state is None:
                    raise KeyError("unknown field: %r" % (field,))
                return self._snapshot_for(field, state, self._pending)
            return self._current_summary()

    get = profile

    def query(self, field: Optional[str] = None, version: Optional[int] = None,
              wait=False):
        """查询当前画像，或按版本查询历史画像。"""
        if version is None:
            return self.profile(field, wait=wait)
        with self._lock:
            snapshot = self._pick_snapshot(version)
            if snapshot is None:
                raise KeyError("no snapshot for version %r" % (version,))
            if field is None:
                result = {
                    "version": snapshot["version"],
                    "updated_at": snapshot["updated_at"],
                    "reason": snapshot["reason"],
                    "fields": {},
                }
                for name, data in snapshot["fields"].items():
                    state = _Field.from_dict(data)
                    result["fields"][name] = {
                        "field": name,
                        "profile": state.profile_dict(self.quantiles),
                        "outliers": state.outliers_copy(),
                        "version": snapshot["version"],
                        "updated_at": snapshot["updated_at"],
                    }
                return result
            data = snapshot["fields"].get(field)
            if data is None:
                raise KeyError("no snapshot of field %r at version %r" % (field, version))
            state = _Field.from_dict(data)
            result = self._snapshot_for(field, state, self._pending)
            result["version"] = snapshot["version"]
            result["updated_at"] = snapshot["updated_at"]
            result["reason"] = snapshot["reason"]
            return result

    def outliers(self, field: str, only_pending=False) -> List[dict]:
        self.flush(5.0)
        with self._lock:
            state = self._state.get(field)
            if state is None:
                raise KeyError("unknown field: %r" % (field,))
            samples = state.outliers_copy()
        if only_pending:
            samples = [s for s in samples if s["review"] is None]
        return samples

    def review(self, field: str, outlier_id: str, status: str = "confirmed",
               note: str = "", reviewer: str = "") -> dict:
        """人工复核异常样本。status 取 pending / confirmed / false_positive。"""
        if status not in ("pending", "confirmed", "false_positive"):
            raise ValueError("status must be pending, confirmed or false_positive")
        self.flush(5.0)
        with self._lock:
            state = self._state.get(field)
            if state is None:
                raise KeyError("unknown field: %r" % (field,))
            for sample in state.outliers:
                if sample["id"] == outlier_id:
                    sample["review"] = None if status == "pending" else {
                        "status": status,
                        "note": note,
                        "reviewer": reviewer,
                        "reviewed_at": _now(),
                    }
                    if self.autosave and self.path:
                        self._save_locked()
                    return copy.deepcopy(sample)
        raise KeyError("unknown outlier: %r" % (outlier_id,))

    # ---------------------------------------------------------------- 策略
    def set_policy(self, field: str, **overrides) -> OutlierPolicy:
        with self._lock:
            policy = self._policy_for(field).merged(**overrides)
            self._policies[field] = policy
            return policy

    # ---------------------------------------------------------------- 导出/保存
    def export(self, fmt: str = "json", fields: Optional[Iterable[str]] = None,
               wait=True, indent: Optional[int] = 2):
        """导出当前画像（含异常样本），默认 JSON 字符串。"""
        if wait:
            self.flush(5.0)
        with self._lock:
            wanted = set(fields) if fields is not None else set(self._state)
            payload = {
                "version": self._version,
                "updated_at": self._updated_at,
                "fields": {},
                "outliers": {},
            }
            for name in sorted(wanted):
                state = self._state.get(name)
                if state is None:
                    continue
                payload["fields"][name] = state.profile_dict(self.quantiles)
                payload["outliers"][name] = state.outliers_copy()
        if fmt == "dict":
            return payload
        if fmt == "json":
            return json.dumps(payload, ensure_ascii=False, indent=indent,
                              allow_nan=False, default=str)
        raise ValueError("unsupported format: %r" % (fmt,))

    def to_dict(self):
        with self._lock:
            return self._snapshot_payload()

    def _snapshot_payload(self):
        return {
            "format": 1,
            "version": self._version,
            "updated_at": self._updated_at,
            "history_limit": self.history_limit,
            "quantiles": list(self.quantiles),
            "policy": asdict(self.policy),
            "policies": {k: asdict(v) for k, v in self._policies.items()},
            "fields": {name: state.to_dict() for name, state in self._state.items()},
            "history": copy.deepcopy(self._history),
        }

    def save(self, path: Optional[str] = None) -> str:
        """原子落盘，返回路径。"""
        target = path or self.path
        if not target:
            raise ValueError("no path configured")
        payload = self.to_dict()
        self._write(target, payload)
        self.path = target
        return target

    def _save_locked(self):
        self._write(self.path, self._snapshot_payload())

    @staticmethod
    def _write(path, payload):
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".profiler2-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, allow_nan=False)
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @classmethod
    def from_dict(cls, data, path=None, autosave=True):
        profiler = cls(path=None, autosave=autosave,
                       history_limit=data.get("history_limit", 5),
                       quantiles=tuple(data.get("quantiles") or DEFAULT_QUANTILES),
                       policy=OutlierPolicy(**data.get("policy", {})))
        profiler.path = path
        profiler._restore(data)
        return profiler

    def _restore(self, data):
        self._state = {name: _Field.from_dict(state)
                       for name, state in data.get("fields", {}).items()}
        self._policies = {k: OutlierPolicy(**v)
                          for k, v in data.get("policies", {}).items()}
        self._version = int(data.get("version", 0))
        self._updated_at = data.get("updated_at")
        self._history = copy.deepcopy(data.get("history", []))

    def _load_into_self(self, path):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        with self._lock:
            self._restore(data)

    @classmethod
    def load(cls, path, autosave=False):
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return cls.from_dict(data, path=path, autosave=autosave)

    # ---------------------------------------------------------------- 校验
    def verify(self, field: str, rows: Iterable[dict], tolerance: float = 0.01,
               quantiles: Optional[Iterable[float]] = None) -> dict:
        """对比同一字段的全量统计与增量画像，确认误差在允许范围内。

        ``rows`` 应传入已经喂给本画像器的数据（全量真值），本方法不修改画像。
        误差以「分位距」为尺度，避免中位数接近 0 时相对误差被放大。字段若在
        采集过程中发生过类型重置，增量只保留重置后的数据，此对比不适用。
        """
        self.flush(10.0)
        rows = list(rows)
        requested = tuple(quantiles or self.quantiles)
        values = [row.get(field) for row in rows]
        categories = [_category(v) for v in values]
        nulls = sum(1 for c in categories if c is None)
        numbers = [float(v) for v, c in zip(values, categories) if c == "number"]

        with self._lock:
            state = self._state.get(field)
            if state is None:
                raise KeyError("unknown field: %r" % (field,))
            current = state.profile_dict(requested)
            category = state.category

        # 非数值字段只核对计数与空值比例；数值字段再核对均值、分位等。
        if category != "number":
            numbers = []

        full = {
            "count": len(values),
            "null_ratio": (nulls / len(values)) if values else 0.0,
        }
        scale = None
        if numbers:
            numbers.sort()
            full["mean"] = sum(numbers) / len(numbers)
            full["min"] = numbers[0]
            full["max"] = numbers[-1]
            for q in requested:
                full[_quantile_key(q)] = _full_quantile(numbers, q)
            # 以四分位距作为分布尺度：中位数接近 0 时相对误差不会被放大。
            iqr = (_full_quantile(numbers, 0.75) or 0.0) - (_full_quantile(numbers, 0.25) or 0.0)
            scale = iqr if iqr > 0 else (numbers[-1] - numbers[0])
            if scale <= 0:
                scale = abs(full["mean"])

        differences = {}
        ok = True

        def compare(name, incremental, expected, kind="relative"):
            nonlocal ok
            if incremental is None or expected is None:
                error = absolute = None
            else:
                absolute = abs(incremental - expected)
                error = absolute if kind == "absolute" \
                    else _relative_error(incremental, expected, scale)
            differences[name] = {
                "incremental": incremental,
                "full": expected,
                "error": error,
                "absolute_error": absolute,
            }
            if error is not None and error > tolerance:
                ok = False

        compare("count", current["count"], full["count"], kind="absolute")
        compare("null_ratio", current["null_ratio"], full["null_ratio"],
                kind="absolute")
        if numbers and current["numeric"]:
            compare("mean", current["numeric"]["mean"], full["mean"])
            compare("min", current["numeric"]["min"], full["min"])
            compare("max", current["numeric"]["max"], full["max"])
            for q in requested:
                key = _quantile_key(q)
                compare(key, current["numeric"]["quantiles"].get(key), full[key])

        return {
            "field": field,
            "tolerance": tolerance,
            "within_tolerance": ok,
            "differences": differences,
            "version": self._version,
            "updated_at": self._updated_at,
        }

    # ---------------------------------------------------------------- 生命周期
    def close(self, timeout: float = 5.0):
        """排空队列并停止后台线程，按需落盘。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if not self.flush(timeout):
            self._last_error = "queue not drained before close"
        done = threading.Event()
        self._queue.put((done, "stop"))
        done.wait(timeout)
        self._worker.join(timeout)
        if self._worker.is_alive():
            self._last_error = "worker did not stop before timeout"
            return
        if self.path and self.autosave:
            try:
                self.save()
            except OSError:
                pass


def _full_quantile(sorted_values, q):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(sorted_values) - 1)
    frac = pos - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


# --------------------------------------------------------------------------- 默认实例
_default = None
_default_lock = threading.Lock()


def default_profiler() -> Profiler:
    global _default
    with _default_lock:
        if _default is None:
            _default = Profiler()
        return _default


def observe(rows, fields=None):
    return default_profiler().observe(rows, fields=fields)


def profile(field=None, wait=True, timeout=5.0):
    return default_profiler().profile(field, wait=wait, timeout=timeout)


def review(field, outlier_id, status="confirmed", note="", reviewer=""):
    return default_profiler().review(field, outlier_id, status, note, reviewer)


def export(fmt="json", fields=None):
    return default_profiler().export(fmt=fmt, fields=fields)


def query(field=None, version=None):
    return default_profiler().query(field, version)


def replay(version):
    return default_profiler().replay(version)


def history(field=None):
    return default_profiler().history(field)


def save(path=None):
    return default_profiler().save(path)


def reset_default():
    """重建默认实例，主要给测试/交互环境用。"""
    global _default
    with _default_lock:
        if _default is not None:
            _default.close()
        _default = None
