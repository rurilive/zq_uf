"""带版本覆盖、撤回扣减和事件时间窗口的增量聚合。"""

from datetime import datetime, timezone

_DAY_SECONDS = 24 * 60 * 60


def _parse_event_time(value):
    """支持 epoch 秒（int/float）或 ISO 8601 字符串，统一转 epoch 秒。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("event_time 不能是布尔值")
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    raise TypeError("event_time 只支持 epoch 秒或 ISO 8601 字符串")


class Aggregator:
    """按 key 聚合 value，支持版本覆盖、撤回和 UTC 日窗口。

    每行明细字段：
      key        聚合主键
      id         明细唯一键（缺失时自动生成，不保证跨实例可重放）
      version    单调递增版本号，缺省为 0
      value      参与聚合的数值
      event_time 事件时间（epoch 秒或 ISO 8601），缺失则不进窗口
      op         "upsert"（默认）或 "retract"；也可用 retract=True
    """

    def __init__(self, allowed_lateness=0):
        self.allowed_lateness = allowed_lateness
        self.totals = {}
        self._records = {}
        self._windows = {}
        self._max_event_time = None
        self._anon_seq = 0

    def apply(self, batch):
        for row in batch:
            self._apply_row(row)
        return self.totals

    def query(self, key, window=None):
        if window is None:
            return self.totals.get(key, 0)
        buckets = self._windows.get(key, {})
        return buckets.get(window, {}).get("total", 0)

    def stats(self, key=None):
        """返回聚合值、明细条数、迟到条数和撤回条数，可按 key 或全局查看。"""
        if key is not None:
            return self._key_stats(key)
        keys = set(self._windows) | set(self.totals)
        merged = {
            "total": 0,
            "detail_count": 0,
            "late_count": 0,
            "retracted_count": 0,
        }
        for each_key in keys:
            for field in merged:
                merged[field] += self._key_stats(each_key)[field]
        return merged

    @staticmethod
    def window_for(event_time):
        """事件时间对应的 UTC 日窗口 id（epoch 天）。"""
        return _parse_event_time(event_time) // _DAY_SECONDS

    def _key_stats(self, key):
        buckets = self._windows.get(key, {})
        return {
            "key": key,
            "total": self.totals.get(key, 0),
            "detail_count": sum(bucket["count"] for bucket in buckets.values()),
            "late_count": sum(bucket["late"] for bucket in buckets.values()),
            "retracted_count": sum(
                1
                for record in self._records.values()
                if record["key"] == key and record["retracted"]
            ),
            "windows": {
                window: dict(bucket) for window, bucket in buckets.items()
            },
        }

    def _apply_row(self, row):
        key = row.get("key")
        record_id = row.get("id")
        if record_id is None:
            self._anon_seq += 1
            record_id = ("__anon__", self._anon_seq)
        version = row.get("version", 0)

        previous = self._records.get(record_id)
        if previous is not None and version <= previous["version"]:
            return

        event_time = _parse_event_time(row.get("event_time"))
        window = None
        if event_time is not None:
            if self._max_event_time is None or event_time > self._max_event_time:
                self._max_event_time = event_time
            window = event_time // _DAY_SECONDS
            watermark = self._max_event_time - self.allowed_lateness
            if event_time < watermark:
                self._mark_late(record_id, version, key, window, previous)
                return

        is_retract = row.get("op") == "retract" or bool(row.get("retract"))
        if is_retract:
            self._retract(record_id, version, key, window, previous)
        else:
            self._upsert(
                record_id, version, row.get("value", 0), key, window, previous
            )

    def _mark_late(self, record_id, version, key, window, previous):
        if previous is not None and not previous["retracted"] and not previous["late"]:
            self._apply_delta(
                previous["key"], previous["window"], -previous["value"], -1
            )
        self._bucket(key, window)["late"] += 1
        self._records[record_id] = {
            "key": key,
            "version": version,
            "value": 0,
            "window": window,
            "retracted": False,
            "late": True,
        }

    def _upsert(self, record_id, version, value, key, window, previous):
        if previous is not None and not previous["retracted"] and not previous["late"]:
            self._apply_delta(
                previous["key"], previous["window"], -previous["value"], -1
            )
        self._apply_delta(key, window, value, 1)
        self._records[record_id] = {
            "key": key,
            "version": version,
            "value": value,
            "window": window,
            "retracted": False,
            "late": False,
        }

    def _retract(self, record_id, version, key, window, previous):
        if previous is not None and not previous["retracted"] and not previous["late"]:
            self._apply_delta(
                previous["key"], previous["window"], -previous["value"], -1
            )
        self._records[record_id] = {
            "key": key,
            "version": version,
            "value": 0,
            "window": window,
            "retracted": True,
            "late": False,
        }

    def _apply_delta(self, key, window, value_delta, count_delta):
        bucket = self._bucket(key, window)
        bucket["total"] += value_delta
        bucket["count"] = max(0, bucket["count"] + count_delta)
        self.totals[key] = self.totals.get(key, 0) + value_delta

    def _bucket(self, key, window):
        return self._windows.setdefault(key, {}).setdefault(
            window, {"total": 0, "count": 0, "late": 0}
        )
