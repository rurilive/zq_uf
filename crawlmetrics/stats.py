"""采集任务统计。

指标命名：
- 计数器：``<stage>.<result>``，stage 取 request/parse/storage，
  result 取 success/fail/retry，例如 ``request.fail``。
- 耗时：``duration.<stage>``，value 单位为秒，内部用直方图保存，
  快照输出 p50/p90/p95/p99 分位数。

站点（site）与任务（task）通过关键字维度传入，快照按总量、站点、任务
三种粒度聚合。快照只包含 JSON 原生类型，可跨进程合并，也可在进程重启
后还原并继续累加。未遵循命名约定的指标保持顶层整数计数的旧行为。
"""

import bisect
import json

STAGES = ("request", "parse", "storage")
RESULTS = ("success", "fail", "retry")
TIMER_PREFIX = "duration."
PERCENTILES = (50, 90, 95, 99)

# 直方图桶边界（秒），覆盖 1ms 到 10s；越界样本计入首/尾桶。
HISTOGRAM_EDGES = (
    0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15,
    0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0,
)
_BIN_COUNT = len(HISTOGRAM_EDGES) + 1


class _Histogram:
    """固定边界直方图，桶计数可直接相加合并。"""

    def __init__(self, counts=None):
        self.counts = list(counts) if counts else [0] * _BIN_COUNT

    def add(self, value):
        self.counts[bisect.bisect_right(HISTOGRAM_EDGES, value)] += 1

    def merge(self, counts):
        if len(counts) != _BIN_COUNT:
            raise ValueError("histogram bin count does not match")
        for index, count in enumerate(counts):
            self.counts[index] += count

    def quantile(self, q, min_value, max_value):
        total = sum(self.counts)
        if total == 0:
            return None
        target = max(1.0, q * total)
        cumulative = 0
        for index, count in enumerate(self.counts):
            if count == 0:
                continue
            if cumulative + count >= target:
                if index == 0:
                    return min_value
                low = HISTOGRAM_EDGES[index - 1]
                if index == len(HISTOGRAM_EDGES):
                    high = max_value
                else:
                    high = HISTOGRAM_EDGES[index]
                if high <= low:
                    return low
                fraction = (target - cumulative - 0.5) / count
                fraction = min(1.0, max(0.0, fraction))
                return low + (high - low) * fraction
            cumulative += count
        return max_value


class _Counter:
    """单个计数器，保留总量与站点/任务两个维度的滚动聚合。"""

    def __init__(self):
        self.total = 0
        self.sites = {}
        self.tasks = {}

    def add(self, value, site, task):
        self.total += value
        if site is not None:
            self.sites[site] = self.sites.get(site, 0) + value
        if task is not None:
            self.tasks[task] = self.tasks.get(task, 0) + value

    def fold(self, data):
        self.total += data.get("total", 0)
        for name, value in data.get("sites", {}).items():
            self.sites[name] = self.sites.get(name, 0) + value
        for name, value in data.get("tasks", {}).items():
            self.tasks[name] = self.tasks.get(name, 0) + value

    def to_dict(self):
        return {
            "total": self.total,
            "sites": {name: self.sites[name] for name in sorted(self.sites)},
            "tasks": {name: self.tasks[name] for name in sorted(self.tasks)},
        }


class _Timer:
    """单阶段耗时统计：直方图 + 基础统计量，并按维度保留子计时。"""

    def __init__(self):
        self.histogram = _Histogram()
        self.count = 0
        self.sum = 0.0
        self.min = None
        self.max = None
        self.sites = {}
        self.tasks = {}

    def record(self, value, site, task):
        self._record(value)
        if site is not None:
            self.sites.setdefault(site, _Timer())._record(value)
        if task is not None:
            self.tasks.setdefault(task, _Timer())._record(value)

    def _record(self, value):
        self.histogram.add(value)
        self.count += 1
        self.sum += value
        if self.min is None or value < self.min:
            self.min = value
        if self.max is None or value > self.max:
            self.max = value

    def fold(self, data):
        histogram = data.get("histogram", {})
        bins = histogram.get("bins", HISTOGRAM_EDGES)
        if list(bins) != list(HISTOGRAM_EDGES):
            raise ValueError("histogram edges do not match")
        self.histogram.merge(histogram.get("counts", []))
        self.count += data.get("count", 0)
        self.sum += data.get("sum", 0.0)
        minimum = data.get("min")
        maximum = data.get("max")
        if minimum is not None and (self.min is None or minimum < self.min):
            self.min = minimum
        if maximum is not None and (self.max is None or maximum > self.max):
            self.max = maximum
        for name, child in data.get("sites", {}).items():
            self.sites.setdefault(name, _Timer()).fold(child)
        for name, child in data.get("tasks", {}).items():
            self.tasks.setdefault(name, _Timer()).fold(child)

    def _leaf_dict(self):
        return {
            "count": self.count,
            "sum": self.sum,
            "min": self.min,
            "max": self.max,
            "mean": self.sum / self.count if self.count else None,
            "quantiles": {
                "p{}".format(percentile): self.histogram.quantile(
                    percentile / 100.0, self.min, self.max
                )
                for percentile in PERCENTILES
            },
            "histogram": {
                "bins": list(HISTOGRAM_EDGES),
                "counts": list(self.histogram.counts),
            },
        }

    def to_dict(self):
        data = self._leaf_dict()
        data["sites"] = {name: self.sites[name]._leaf_dict()
                         for name in sorted(self.sites)}
        data["tasks"] = {name: self.tasks[name]._leaf_dict()
                         for name in sorted(self.tasks)}
        return data


class Stats:
    def __init__(self, snapshot=None):
        self._free = {}
        self._counters = {}
        self._timers = {}
        if snapshot is not None:
            self.merge(snapshot)

    def inc(self, name, value=1, *, site=None, task=None):
        """记录一次指标。

        ``duration.<stage>`` 视为耗时样本（秒）；``<stage>.<result>`` 视为
        分阶段计数器；其他名字保持顶层整数累加的旧行为。
        """
        if name.startswith(TIMER_PREFIX):
            stage = name[len(TIMER_PREFIX):]
            if stage not in STAGES:
                raise ValueError("unknown timing stage: {!r}".format(stage))
            self._timers.setdefault(stage, _Timer()).record(
                float(value), site, task
            )
            return

        stage, separator, result = name.partition(".")
        if separator and stage in STAGES:
            if result not in RESULTS:
                raise ValueError("unknown stage result: {!r}".format(name))
            if site is None and task is None:
                raise ValueError(
                    "stage counter {!r} requires site/task dimension".format(name)
                )
            self._counters.setdefault(name, _Counter()).add(value, site, task)
            return

        if site is not None or task is not None:
            raise ValueError(
                "dimensions are only supported for stage metrics: {!r}".format(name)
            )
        self._free[name] = self._free.get(name, 0) + value

    def timing(self, stage, seconds, *, site=None, task=None):
        """记录某阶段一次耗时（秒），等价于 inc('duration.<stage>', ...)。"""
        self.inc(TIMER_PREFIX + stage, seconds, site=site, task=task)

    def snapshot(self):
        """返回可 JSON 序列化的快照，每次调用都是独立深拷贝。"""
        snap = dict(self._free)
        if self._counters:
            snap["counters"] = {
                name: self._counters[name].to_dict()
                for name in sorted(self._counters)
            }
        if self._timers:
            snap["timers"] = {
                stage: self._timers[stage].to_dict()
                for stage in sorted(self._timers)
            }
        return snap

    def merge(self, snapshot):
        """把另一个进程的快照合并进当前实例，返回 self。"""
        for name, value in snapshot.items():
            if name in ("counters", "timers"):
                continue
            self._free[name] = self._free.get(name, 0) + value
        for name, data in snapshot.get("counters", {}).items():
            self._counters.setdefault(name, _Counter()).fold(data)
        for stage, data in snapshot.get("timers", {}).items():
            self._timers.setdefault(stage, _Timer()).fold(data)
        return self

    @classmethod
    def merge_snapshots(cls, snapshots):
        """合并多个进程的快照，返回一个新的快照字典。"""
        merged = cls()
        for snapshot in snapshots:
            merged.merge(snapshot)
        return merged.snapshot()

    def dump(self, path):
        """把快照写入 JSON 文件，供重启后 load 还原。"""
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.snapshot(), handle, ensure_ascii=False,
                      indent=2, sort_keys=True)

    @classmethod
    def load(cls, path):
        """从 dump 写出的 JSON 文件还原 Stats，历史指标继续累加。"""
        with open(path, "r", encoding="utf-8") as handle:
            return cls(json.load(handle))
