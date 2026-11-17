"""配置基线比对：登记基线与允许差异范围，逐项检测漂移并跟踪整改。"""

import time
import uuid

_baseline = {}
_tolerances = {}
_records = {}  # (key, host) -> 最新漂移记录
_events = []  # 漂移/整改事件，按时间排序，供回看
_pending = {}  # key -> {"spec", "token"} 待二次确认的范围调整


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def baseline(config, tolerances=None):
    """登记基线配置和每个配置项允许的差异范围。

    tolerances 取值：
      None        严格相等（默认）
      数值        |当前值 - 基线值| <= 容差（按数值比较）
      list/set    当前值在枚举集合内即可
      {"min","max"} 当前值落在数值区间内
    """
    if not isinstance(config, dict):
        raise ValueError("config 需要字典")
    tolerances = tolerances or {}
    unknown = set(tolerances) - set(config)
    if unknown:
        raise ValueError("差异范围对应了未登记的配置项: %s" % sorted(unknown))
    _baseline.clear()
    _baseline.update(config)
    _tolerances.clear()
    _tolerances.update(tolerances)
    _records.clear()
    _events.clear()
    _pending.clear()
    return {"keys": sorted(_baseline), "count": len(_baseline)}


def set_tolerance(key, spec, confirm=None):
    """调整某个配置项允许的差异范围，需要二次确认才生效。

    第一次调用返回 token；带 confirm=token 再次调用（spec 一致）才真正应用。
    """
    if key not in _baseline:
        raise ValueError("未知配置项: %s" % key)
    if confirm is None:
        token = uuid.uuid4().hex[:8]
        _pending[key] = {"spec": spec, "token": token}
        return {"key": key, "spec": spec, "pending": True, "token": token}
    pending = _pending.get(key)
    if not pending or pending["token"] != confirm or pending["spec"] != spec:
        raise ValueError("二次确认不匹配，范围未调整")
    if spec is None:
        _tolerances.pop(key, None)
    else:
        _tolerances[key] = spec
    del _pending[key]
    return {"key": key, "spec": spec, "pending": False, "applied": True}


def _within(key, value):
    spec = _tolerances.get(key)
    base = _baseline[key]
    if spec is None:
        return value == base
    if isinstance(spec, bool):
        raise ValueError("非法的差异范围: %r" % spec)
    if isinstance(spec, (int, float)):
        try:
            return abs(float(value) - float(base)) <= spec
        except (TypeError, ValueError):
            return False
    if isinstance(spec, dict):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and v < float(lo):
            return False
        if hi is not None and v > float(hi):
            return False
        return True
    if isinstance(spec, (list, tuple, set, frozenset)):
        return value in spec
    if callable(spec):
        return bool(spec(value, base))
    raise ValueError("非法的差异范围: %r" % spec)


def diff(host):
    """按基线逐项比对一台主机，返回漂移项（含主机、基线值、当前值、重复次数）。"""
    if not isinstance(host, dict):
        raise ValueError("host 需要字典")
    name = host.get("host")
    if not name:
        raise ValueError("缺少主机名")
    current = host.get("config", {})
    items = []
    for key in sorted(_baseline):
        value = current.get(key)
        rid = (key, name)
        rec = _records.get(rid)
        if _within(key, value):
            # 整改后重新比对与基线一致，标记已整改
            if rec and not rec["fixed"]:
                rec["fixed"] = True
                rec["fixed_at"] = _now()
                _events.append(
                    {"time": rec["fixed_at"], "kind": "fixed", "key": key, "host": name}
                )
            continue
        if rec is None:
            repeats, first_seen, new_occurrence = 1, _now(), True
        elif rec["fixed"]:
            # 整改后再次漂移，重复次数加一
            repeats, first_seen, new_occurrence = rec["repeats"] + 1, rec["first_seen"], True
        else:
            # 同一漂移尚未整改，不重复计数，只刷新当前值
            repeats, first_seen, new_occurrence = rec["repeats"], rec["first_seen"], False
        rec = {
            "key": key,
            "host": name,
            "baseline": _baseline[key],
            "current": value,
            "repeats": repeats,
            "fixed": False,
            "first_seen": first_seen,
            "last_seen": _now(),
            "fixed_at": None,
        }
        _records[rid] = rec
        if new_occurrence:
            _events.append(
                {
                    "time": rec["last_seen"],
                    "kind": "drift",
                    "key": key,
                    "host": name,
                    "current": value,
                    "repeats": repeats,
                }
            )
        items.append(dict(rec))
    return {"host": name, "drift": items, "passed": not items}


def report(key=None, host=None):
    """按配置项/主机维度汇总漂移项、整改状态、重复次数和事件历史。"""
    items = [
        dict(rec)
        for (k, h), rec in sorted(_records.items())
        if (key is None or k == key) and (host is None or h == host)
    ]
    by_key, by_host = {}, {}
    for rec in items:
        if rec["fixed"]:
            continue
        by_key[rec["key"]] = by_key.get(rec["key"], 0) + 1
        by_host[rec["host"]] = by_host.get(rec["host"], 0) + 1
    # 重复漂移（整改后再次漂移）的配置项单独统计
    repeats = {}
    for (k, _), rec in _records.items():
        if rec["repeats"] > 1 and (key is None or k == key):
            repeats[k] = repeats.get(k, 0) + rec["repeats"]
    history = [
        dict(e)
        for e in _events
        if (key is None or e["key"] == key) and (host is None or e["host"] == host)
    ]
    return {
        "key": key,
        "host": host,
        "items": items,
        "hosts": sorted({h for (_, h) in _records}),
        "by_key": by_key,
        "by_host": by_host,
        "repeats": repeats,
        "history": history,
    }
