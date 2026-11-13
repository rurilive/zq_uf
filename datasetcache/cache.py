"""数据集查询缓存：按数据集版本和血缘依赖失效，分批限速，支持预热和旧值兜底。"""
import time

TTL = 3600
_cache = {}
_datasets = {}
_misses = 0
_invalidated = 0
_refetched = 0
_stale_served = 0
_miss_reasons = {}
_last_report = None


def clear():
    global _misses, _invalidated, _refetched, _stale_served, _last_report
    _cache.clear()
    _datasets.clear()
    _misses = 0
    _invalidated = 0
    _refetched = 0
    _stale_served = 0
    _miss_reasons.clear()
    _last_report = None


def register_dataset(name, version=None, depends_on=()):
    """登记数据集版本和上游依赖，版本来源见 README。"""
    _datasets[name] = {"version": version, "depends_on": set(depends_on)}


def set_version(name, version):
    """更新数据集版本；已缓存的同数据集条目将懒失效。"""
    _datasets.setdefault(name, {"depends_on": set()})["version"] = version


def put(key, value, dataset=None, version=None, depends=None, now=None):
    now = time.time() if now is None else now
    if dataset is not None:
        _datasets.setdefault(dataset, {"version": None, "depends_on": set()})
        if version is None:
            version = _datasets[dataset]["version"]
    _cache[key] = {
        "value": value,
        "expires": now + TTL,
        "dataset": dataset,
        "version": version,
        "depends": set(depends or ()),
    }
    return len(_cache)


def _miss(reason):
    global _misses
    _misses += 1
    _miss_reasons[reason] = _miss_reasons.get(reason, 0) + 1


def _version_stale(entry):
    if entry["dataset"] is None:
        return False
    current = _datasets.get(entry["dataset"])
    return current is not None and entry["version"] != current["version"]


def get(key, now=None, loader=None, allow_stale=False):
    """命中返回缓存值；未命中可回源（loader），回源失败可用旧值兜底。"""
    global _refetched, _stale_served
    now = time.time() if now is None else now
    entry = _cache.get(key)
    if entry is None:
        reason = "missing"
    elif entry["expires"] <= now:
        reason = "expired"
    elif _version_stale(entry):
        reason = "version"
    else:
        reason = None
    if reason is None:
        return entry["value"]
    _miss(reason)
    if loader is not None:
        try:
            value = loader(key)
        except Exception:
            value = None
        else:
            _refetched += 1
            put(key, value, dataset=entry["dataset"] if entry else None,
                depends=entry["depends"] if entry else None, now=now)
            return value
    if allow_stale and entry is not None:
        _stale_served += 1
        return entry["value"]
    return None


def preheat(keys, loader, dataset=None, depends=None, now=None):
    """预热：批量回源并写入缓存，返回写入条数。"""
    global _refetched
    now = time.time() if now is None else now
    count = 0
    for key in keys:
        put(key, loader(key), dataset=dataset, depends=depends, now=now)
        count += 1
    _refetched += count
    return count


def _affected_datasets(dataset):
    """按血缘找下游：依赖了 dataset 的数据集（含传递依赖）。"""
    affected = {dataset}
    queue = [dataset]
    while queue:
        current = queue.pop()
        for name, meta in _datasets.items():
            if name not in affected and current in meta["depends_on"]:
                affected.add(name)
                queue.append(name)
    return affected


def _is_affected(entry, affected):
    if entry["dataset"] is None and not entry["depends"]:
        return True  # 未知血缘，保守清理
    return entry["dataset"] in affected or bool(entry["depends"] & affected)


def invalidate(dataset=None, version=None, batch_size=None, rate=None, sleep=None):
    """按版本和依赖失效数据集缓存，分批限速，返回失效条数。

    version 与登记版本一致时视为无变化，直接返回 0；不传 version 按已变化处理。
    rate 为每秒最大失效条数，batch_size 为每批条数，sleep 可注入便于测试。
    """
    global _invalidated, _last_report
    sleep = time.sleep if sleep is None else sleep
    start = time.monotonic()
    if dataset is not None and version is not None:
        meta = _datasets.setdefault(dataset, {"depends_on": set()})
        if meta.get("version") == version:
            _last_report = {"dataset": dataset, "invalidated": 0, "batches": 0,
                            "elapsed": time.monotonic() - start}
            return 0
        meta["version"] = version
    if dataset is None:
        keys = list(_cache)
    else:
        affected = _affected_datasets(dataset)
        keys = [k for k, e in _cache.items() if _is_affected(e, affected)]
    if batch_size is None:
        batch_size = len(keys) if rate is None else max(1, int(rate))
    batch_size = max(1, batch_size)
    batches = 0
    for i in range(0, len(keys), batch_size):
        chunk = keys[i:i + batch_size]
        for key in chunk:
            del _cache[key]
        _invalidated += len(chunk)
        batches += 1
        if rate and i + batch_size < len(keys):
            sleep(len(chunk) / rate)
    _last_report = {"dataset": dataset, "invalidated": len(keys),
                    "batches": batches, "elapsed": time.monotonic() - start}
    return len(keys)


def stats():
    return {"size": len(_cache), "misses": _misses, "invalidated": _invalidated}


def report():
    """失效条数、回源数、耗时和未命中原因。"""
    return {
        "invalidated": _invalidated,
        "refetched": _refetched,
        "stale_served": _stale_served,
        "miss_reasons": dict(_miss_reasons),
        "last_invalidation": _last_report,
    }
