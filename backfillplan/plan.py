"""回填计划：按时间和主键切片，分片带依赖和优先级。"""

MIN_SHARD_SPAN = 5
DEFAULT_TIME_STEP = 3600


def _split_keys(start, end, count):
    """把主键半开区间 [start, end) 切成 count 段，过细时自动合并。"""
    span = end - start
    count = max(1, min(count, span // MIN_SHARD_SPAN or 1))
    base, extra = divmod(span, count)
    bounds = []
    cur = start
    for i in range(count):
        step = base + (1 if i < extra else 0)
        bounds.append((cur, cur + step))
        cur += step
    return bounds


def _time_buckets(spec):
    """range 里带 time_start/time_end 时先按时间分桶。"""
    if "time_start" not in spec or "time_end" not in spec:
        return [None]
    step = spec.get("time_step", DEFAULT_TIME_STEP)
    buckets = []
    t = spec["time_start"]
    while t < spec["time_end"]:
        buckets.append((t, min(t + step, spec["time_end"])))
        t += step
    return buckets or [None]


def plan(range_spec, shards=1):
    """生成回填计划：先按时间分桶，桶内按主键切片。

    后一个时间桶的分片依赖前一个时间桶全部分片；
    优先级按时间桶从早到晚、主键从小到大递减。
    """
    spec = dict(range_spec)
    buckets = _time_buckets(spec)
    result = []
    prev_ids = []
    for bi, bucket in enumerate(buckets):
        bounds = _split_keys(spec["start"], spec["end"], shards)
        cur_ids = []
        for ki, (s, e) in enumerate(bounds):
            shard = {
                "id": "shard-%d-%d" % (bi, ki),
                "start": s,
                "end": e,
                "deps": list(prev_ids),
                "priority": (len(buckets) - bi) * 100000 - ki,
            }
            if bucket:
                shard["time_start"], shard["time_end"] = bucket
            result.append(shard)
            cur_ids.append(shard["id"])
        prev_ids = cur_ids
    return {"range": spec, "shards": result}
