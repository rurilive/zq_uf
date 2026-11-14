"""站点地图增量合并：按 lastmod 比较，删除传播并保留墓碑，分片合并幂等。

主接口保持 ``merge(old, new)`` 与 ``diff()``：

* ``merge(old, new)``：``old`` 是上一版索引，``new`` 是上游这次的完整快照。
  同名 URL 比较 lastmod，新代覆盖旧代，旧代回放直接跳过；
  ``old`` 里有而 ``new`` 里没有的 URL 视为被上游删除，移出索引并留下墓碑。
* ``diff()``：返回最近一次合并的 ``added`` / ``updated`` / ``removed`` URL 列表。
* ``report()``：返回最近一次合并的新增、更新、删除、跳过数与墓碑总数。

入口（entry）支持以下写法：

* 带 ``lastmod`` 的映射（也认 ``last_modified`` / ``lastModified``）；
* ``(lastmod, 载荷)`` 二元组；
* 直接的时间戳字符串或 ``datetime``/``date``（此时整个值就是 lastmod）；
* 其余值按“没有 lastmod”处理，沿用新值覆盖旧值的兼容口径。

lastmod 支持 ISO-8601（``2024-01-02``、``2024-01-02T03:04:05Z``、带时区偏移）
以及映射里的数值时间戳（epoch 秒）；不带时区的按 UTC 处理。
lastmod 相同的两次出现视为同一代，直接跳过，保证重复合并、重复分片幂等。
"""

from __future__ import annotations

import datetime as _datetime
from collections.abc import Mapping

__all__ = [
    "FetchResult",
    "diff",
    "fetch_shards",
    "merge",
    "merge_shards",
    "report",
    "restore",
    "rollback_last",
    "tombstones",
]

_LASTMOD_KEYS = frozenset({"lastmod", "last_modified", "lastmodified"})
_UTC = _datetime.timezone.utc

# url -> {"value": 删除前最近一代的条目, "lastmod": 原始 lastmod, "deleted_in": 代号}
_tombstones = {}
# 最近一次合并的明细
_last = {"generation": 0, "added": [], "updated": [], "removed": [], "skipped": []}
_generation = 0


def _reset_state():
    """复位索引之外的模块状态；由 ``_Index.clear()`` 触发，便于测试隔离。"""
    global _generation
    _generation = 0
    _tombstones.clear()
    _last.update(generation=0, added=[], updated=[], removed=[], skipped=[])


class _Index(dict):
    """当前索引。``clear()`` 同时清掉墓碑和统计，测试里的 ``_index.clear()`` 即可隔离。"""

    def clear(self):
        super().clear()
        _reset_state()


_index = _Index()


def _parse_lastmod(value):
    """把 lastmod 归一成 UTC datetime；无法解析时返回 None。"""
    if isinstance(value, _datetime.datetime):
        when = value if value.tzinfo is not None else value.replace(tzinfo=_UTC)
        return when.astimezone(_UTC)
    if isinstance(value, _datetime.date):
        return _datetime.datetime(value.year, value.month, value.day, tzinfo=_UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            when = _datetime.datetime.fromisoformat(text)
        except ValueError:
            try:
                when = _datetime.datetime.strptime(text, "%Y-%m-%d")
            except ValueError:
                return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=_UTC)
        return when.astimezone(_UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return _datetime.datetime.fromtimestamp(value, _UTC)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _entry(value):
    """拆出 (lastmod 原值, lastmod datetime, 载荷)。"""
    if isinstance(value, Mapping):
        for key, raw in value.items():
            if isinstance(key, str) and key.strip().lower() in _LASTMOD_KEYS:
                return raw, _parse_lastmod(raw), value
        return None, None, value
    if isinstance(value, tuple) and len(value) == 2:
        raw, payload = value
        when = _parse_lastmod(raw)
        if when is not None:
            return raw, when, payload
        return None, None, value
    if isinstance(value, (str, _datetime.datetime, _datetime.date)):
        when = _parse_lastmod(value)
        if when is not None:
            return value, when, value
    return None, None, value


def _keep_newest_tombstone(url, value, generation):
    """写下墓碑；同一 URL 只保留删除前最近一代的条目，便于按代回滚。

    正常情况下一个 URL 复活时墓碑已作废，这里仍比较一次 lastmod，保证“只保留最近
    一代”的口径在 restore/手工写入等异常路径下也成立。
    """
    new_raw, new_when, _payload = _entry(value)
    existing = _tombstones.get(url)
    if existing is not None:
        old_when = _parse_lastmod(existing["lastmod"])
        if old_when is not None and (new_when is None or new_when < old_when):
            # 已有墓碑对应更近的一代，保留它
            return
    _tombstones[url] = {"value": value, "lastmod": new_raw, "deleted_in": generation}


def _decide(old_value, new_value):
    """判断同一 URL 的新值是否覆盖旧值。

    返回 ``(changed, take_new)``：``changed`` 计入“更新”，``take_new`` 决定是否落新值。
    """
    _old_raw, old_when, _old_payload = _entry(old_value)
    _new_raw, new_when, _new_payload = _entry(new_value)

    if old_when is not None and new_when is not None:
        # 新代覆盖旧代；同代或旧代回放都跳过，保证幂等
        return new_when > old_when, new_when > old_when
    if new_when is not None:
        # 旧条目没有可解析的 lastmod，新值权威；值相同则当作没变化
        changed = old_value != new_value
        return changed, changed
    # 新条目没有可用 lastmod：沿用整表覆盖的兼容口径
    changed = old_value != new_value
    return changed, changed


def merge(old, new, *, complete=True):
    """把新快照并进索引。

    ``complete=True``（默认）表示 ``new`` 覆盖上游全量：``old`` 有而 ``new`` 没有的
    URL 按删除处理并留墓碑。``complete=False`` 用于分片不完整时保守合并——缺失的
    URL 宁可保留也不删，避免把“没下载到”误判成“上游删了”。
    """
    global _generation

    # 允许直接把分片列表或下载结果交给 merge：merge(old, [shard1, shard2])
    if isinstance(new, FetchResult):
        return merge_shards(old, new, complete=complete)
    if not isinstance(new, Mapping) and isinstance(new, (list, tuple)):
        return merge_shards(old, new, complete=complete)

    old_map = dict(old or {})
    new_map = dict(new or {})
    _generation += 1
    generation = _generation

    added, updated, removed, skipped = [], [], [], []
    merged = {}

    for url, old_value in old_map.items():
        if url in new_map:
            new_value = new_map[url]
            changed, take_new = _decide(old_value, new_value)
            merged[url] = new_value if take_new else old_value
            (updated if changed else skipped).append(url)
            # 该 URL 在这次视图里是活的，旧墓碑作废
            _tombstones.pop(url, None)
            continue
        if not complete:
            # 分片不完整：无法区分“上游删除”和“该片未覆盖”，保守保留
            merged[url] = old_value
            skipped.append(url)
            continue
        # 缺失即删除：墓碑只保留删除前最近一代的条目，便于回滚
        _keep_newest_tombstone(url, old_value, generation)
        removed.append(url)

    for url, new_value in new_map.items():
        if url in old_map:
            continue
        # 墓碑上的 URL 重新出现 = 复活，墓碑作废
        _tombstones.pop(url, None)
        merged[url] = new_value
        added.append(url)

    # 原地替换，保持 _index 对象身份不变（测试和调用方都持有它）
    dict.clear(_index)
    dict.update(_index, merged)

    _last.update(
        generation=generation,
        added=added,
        updated=updated,
        removed=removed,
        skipped=skipped,
    )
    return dict(_index)


def merge_shards(old, shards, *, complete=True):
    """把多个分片当成同一份快照合并：先按 lastmod 取并集，再做一次整体合并。

    重复分片只会产生同样的并集，因此幂等；删除只对照“所有成功分片的并集”判断。
    传 ``FetchResult`` 时会自动采用它的成功分片与完整性，失败片覆盖到的旧 URL 不会
    被误删。
    """
    if isinstance(shards, FetchResult):
        complete = shards.complete
        shards = shards.payloads()

    union = {}
    saw_shard = False
    for shard in shards:
        if shard is None:
            continue
        saw_shard = True
        for url, value in shard.items():
            if url not in union:
                union[url] = value
                continue
            _changed, take_new = _decide(union[url], value)
            if take_new:
                union[url] = value

    if not saw_shard:
        # 一个分片都没拿到时不能把索引清空
        complete = False
    return merge(old, union, complete=complete)


class FetchResult:
    """分片下载结果：成功片、失败片、每片实际尝试次数。"""

    def __init__(self, shards=(), failed=None, attempts=None):
        self.shards = list(shards)
        self.failed = dict(failed or {})
        self.attempts = dict(attempts or {})

    @property
    def complete(self):
        """所有分片都成功时为真；只有完整时才允许传播删除。"""
        return not self.failed

    @property
    def keys(self):
        return [key for key, _payload in self.shards]

    def payloads(self):
        return [payload for _key, payload in self.shards]

    def merge_into(self, old):
        return merge_shards(old, self)

    def __repr__(self):
        return "FetchResult(ok=%d, failed=%d, attempts=%r)" % (
            len(self.shards),
            len(self.failed),
            self.attempts,
        )


def fetch_shards(shards, fetch, *, retries=1):
    """逐片下载；某片失败只重取该片，其它片不重复下载。

    ``shards`` 是 ``名字 -> 分片描述`` 的映射（也接受可迭代对象，按序号命名），
    ``fetch(描述)`` 返回该片的地图数据。``retries`` 是失败后的重试次数。
    返回 ``FetchResult``，可直接 ``merge_into(old)``。

    只重取失败片这一点由“每片独立的重试循环”保证：一片的异常被就地捕获并只对该片
    重试，已经取到的片不会再次进入下载器。
    """
    if isinstance(shards, Mapping):
        items = list(shards.items())
    else:
        items = list(enumerate(shards))

    tries = max(0, int(retries)) + 1
    fetched, failed, attempts = [], {}, {}

    for key, descriptor in items:
        last_error = None
        for attempt in range(1, tries + 1):
            try:
                payload = fetch(descriptor)
            except Exception as exc:  # 单片的异常不该连累其它片
                last_error = exc
                attempts[key] = attempt
                continue
            last_error = None
            attempts[key] = attempt
            fetched.append((key, payload))
            break
        if last_error is not None:
            failed[key] = last_error

    return FetchResult(fetched, failed, attempts)


def diff():
    """最近一次合并新增、删除、更新的 URL 列表。"""
    return {
        "added": list(_last["added"]),
        "removed": list(_last["removed"]),
        "updated": list(_last["updated"]),
    }


def report():
    """最近一次合并的计数与当前墓碑数量。"""
    return {
        "added": len(_last["added"]),
        "updated": len(_last["updated"]),
        "removed": len(_last["removed"]),
        "skipped": len(_last["skipped"]),
        "tombstones": len(_tombstones),
    }


def tombstones():
    """当前墓碑副本：每个 URL 只保留删除前最近一代的条目。"""
    return {url: dict(item) for url, item in _tombstones.items()}


def restore(url):
    """按墓碑把单个 URL 恢复回索引；没有墓碑时返回 False。"""
    item = _tombstones.pop(url, None)
    if item is None:
        return False
    _index[url] = item["value"]
    return True


def rollback_last():
    """回滚最近一次合并里的删除，返回被恢复的 URL 列表。

    只恢复本次合并写下的墓碑（``deleted_in`` 等于本次代号）；更早的墓碑属于更早的
    快照，不会被“后一次无删除的合并”误复活。墓碑只留最近一代，因此只能回滚一代。
    """
    restored = []
    generation = _last["generation"]
    for url in list(_last["removed"]):
        item = _tombstones.get(url)
        if item is None or item["deleted_in"] != generation or url in _index:
            continue
        _index[url] = item["value"]
        del _tombstones[url]
        restored.append(url)
    return restored
