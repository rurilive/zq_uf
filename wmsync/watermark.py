"""多源水位对齐：全局水位取各源最小值，空闲源按心跳推进，迟到事件进侧输出。"""

_ts = {}      # source -> 当前水位（单调不减）
_last = {}    # source -> 最近一次事件时间（空闲判定依据）
_idle = set()  # 空闲源
_late = []    # 迟到事件侧输出 [{"source", "ts", "watermark"}]
_now = 0      # 最近一次心跳时间


def reset():
    global _now
    _ts.clear()
    _last.clear()
    _idle.clear()
    _late.clear()
    _now = 0


def update(source, ts):
    """上报 source 的事件时间，返回各源水位。

    ts 落后于当前全局水位视为迟到，进侧输出且不回退任何水位；
    新源校准到不低于当前全局水位，保证全局水位单调不减。
    """
    wm = watermark()
    if ts < wm:
        _late.append({"source": source, "ts": ts, "watermark": wm})
        if source not in _ts:
            _ts[source] = wm
    else:
        _ts[source] = max(_ts.get(source, ts), ts)
    _last[source] = max(_last.get(source, ts), ts)
    _idle.discard(source)
    return dict(_ts)


def watermark():
    """全局水位 = 各源水位最小值（按最慢源对齐，单调不减）。"""
    return min(_ts.values()) if _ts else 0


def heartbeat(now, idle_timeout=0):
    """推进到 now；超过 idle_timeout 未更新的源标记空闲。

    空闲源水位按 now - idle_timeout 推进，不再卡住全局最小值；
    恢复更新后自动回到活动集合，重新参与最小水位计算。
    """
    global _now
    _now = max(_now, now)
    floor = _now - idle_timeout
    for source, last in _last.items():
        if last <= floor:
            _idle.add(source)
            if floor > _ts[source]:
                _ts[source] = floor
        else:
            _idle.discard(source)
    return status()


def status():
    """各源水位、全局水位、空闲源和迟到数。"""
    return {
        "sources": dict(_ts),
        "watermark": watermark(),
        "idle": sorted(_idle),
        "late_count": len(_late),
    }


def late_events():
    """迟到事件侧输出。"""
    return [dict(event) for event in _late]


def snapshot():
    """可 JSON 序列化的完整状态，用于重启恢复。"""
    return {
        "sources": dict(_ts),
        "last": dict(_last),
        "idle": sorted(_idle),
        "late": late_events(),
        "now": _now,
    }


def restore(snap):
    """从 snapshot() 的状态恢复。"""
    global _now
    reset()
    _ts.update(snap["sources"])
    _last.update(snap["last"])
    _idle.update(snap["idle"])
    _late.extend(snap["late"])
    _now = snap["now"]
    return status()
