"""分级采样：错误/慢请求全留，正常请求按比例采样，总量可还原。"""
import json
import random
import time

ERROR_STATUS = 500
DEFAULT_SLOW_MS = 2000


class Sampler:
    """按 (站点, 小时) 分级采样，并为每个键保留精确计数。"""

    def __init__(self, seed=None):
        self._rng = random.Random(seed)
        self.slow_ms = DEFAULT_SLOW_MS
        self.reset()

    def reset(self):
        self._rows = []
        self._rates = {}      # (site, hour|None) -> 静态比例
        self._targets = {}    # site -> 每小时目标条数（动态比例）
        self._keys = {}       # (site, hour) -> 计数器
        self._batches = {}    # batch_id -> 保留的行
        self._paused = False
        self._batch_seq = 0

    # ---- 配置 ----
    def set_seed(self, seed):
        self._rng.seed(seed)

    def set_rate(self, site, rate, hour=None):
        """静态比例：精确 (site, hour) 优先，其次 site，最后 '*' 兜底。"""
        if not 0 < rate <= 1:
            raise ValueError("rate 必须在 (0, 1]")
        self._rates[(site, hour)] = float(rate)

    def set_target(self, site, target_per_hour):
        """动态比例：每小时约保留 target 条，第 n 条正常请求按 target/n 采样。"""
        self._targets[site] = int(target_per_hour)

    def pause(self):
        """临时关闭采样（异常批次全量保留）。"""
        self._paused = True

    def resume(self):
        self._paused = False

    # ---- 内部 ----
    def _active(self):
        return bool(self._rates or self._targets)

    def _rate_for(self, site, hour, seen):
        target = self._targets.get(site, self._targets.get("*"))
        if target:
            return min(1.0, target / max(seen, 1))
        for key in ((site, hour), (site, None), ("*", None)):
            if key in self._rates:
                return self._rates[key]
        return 1.0

    def _key_stats(self, site, hour):
        return self._keys.setdefault((site, hour), {
            "seen": 0, "errors": 0, "slows": 0,
            "normal_seen": 0, "kept_normal": 0,
            "est": 0.0, "var": 0.0,
        })

    def _is_error(self, row):
        return bool(row.get("error")) or int(row.get("status", 0) or 0) >= ERROR_STATUS

    def _is_slow(self, row):
        return bool(row.get("slow")) or float(row.get("duration_ms", 0) or 0) >= self.slow_ms

    # ---- 写入 ----
    def write(self, record, batch=None):
        """返回保留的行；被采掉时返回 None。未配置采样时全量写入、不加字段。"""
        row = dict(record)
        site = row.get("site", "default")
        hour = int(row.get("hour", time.localtime().tm_hour))
        stats = self._key_stats(site, hour)
        stats["seen"] += 1

        is_error = self._is_error(row)
        is_slow = not is_error and self._is_slow(row)

        reason = None
        rate = 1.0
        keep = True
        if self._active():
            if self._paused:
                reason = "paused"
            elif is_error:
                reason = "error"
            elif is_slow:
                reason = "slow"
            else:
                rate = self._rate_for(site, hour, stats["normal_seen"] + 1)
                keep = self._rng.random() < rate
                reason = "rate"
            row["sampled"] = keep
            row["sample_rate"] = rate
            row["sample_reason"] = reason
            if batch is not None:
                row["batch"] = batch

        if is_error:
            stats["errors"] += 1
        elif is_slow:
            stats["slows"] += 1
        else:
            stats["normal_seen"] += 1
            if keep and self._active():
                stats["kept_normal"] += 1
                stats["est"] += 1.0 / rate          # Horvitz-Thompson 权重
                stats["var"] += (1.0 - rate) / (rate * rate)

        if keep:
            self._rows.append(row)
            if batch is not None:
                self._batches.setdefault(batch, []).append(row)
            return row
        return None

    def write_batch(self, records, batch_id=None, keep_all=False):
        """按批次写入并记录 batch_id；keep_all=True 临时关闭采样。返回 batch_id。"""
        if batch_id is None:
            self._batch_seq += 1
            batch_id = "batch-%d" % self._batch_seq
        if keep_all:
            self.pause()
        try:
            for record in records:
                self.write(record, batch=batch_id)
        finally:
            if keep_all:
                self.resume()
        return batch_id

    def replay(self, batch_id):
        """回放某批次保留的样本行（JSON 字符串列表）。"""
        return [json.dumps(row, ensure_ascii=False)
                for row in self._batches.get(batch_id, [])]

    # ---- 查询 ----
    def lines(self):
        return [json.dumps(row, ensure_ascii=False) for row in self._rows]

    def stats(self, site=None):
        """按站点查询采样比例、精确计数、估算总量和 1-sigma 误差。"""
        out = {}
        for (s, hour), st in sorted(self._keys.items()):
            if site is not None and s != site:
                continue
            est = st["errors"] + st["slows"] + st["est"]
            err = st["var"] ** 0.5
            out["%s@%02d" % (s, hour)] = {
                "seen": st["seen"],
                "kept": st["errors"] + st["slows"] + st["kept_normal"],
                "errors": st["errors"],
                "slows": st["slows"],
                "rate": self._rate_for(s, hour, max(st["normal_seen"], 1)),
                "estimated_total": est,
                "error": err,
            }
        return out


_default = Sampler()


def reset():
    _default.reset()


def write(record):
    return _default.write(record)


def lines():
    return _default.lines()


def set_seed(seed):
    _default.set_seed(seed)


def set_rate(site, rate, hour=None):
    _default.set_rate(site, rate, hour)


def set_target(site, target_per_hour):
    _default.set_target(site, target_per_hour)


def pause():
    _default.pause()


def resume():
    _default.resume()


def write_batch(records, batch_id=None, keep_all=False):
    return _default.write_batch(records, batch_id=batch_id, keep_all=keep_all)


def replay(batch_id):
    return _default.replay(batch_id)


def stats(site=None):
    return _default.stats(site)
