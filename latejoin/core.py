"""按事件时间关联多版本维表，支持迟到回补与匹配统计。

事实行用 event_time 表示发生时间，维表行用 effective_time 表示生效时间。
同一主键的多个维表版本全部保留，事实匹配生效时间不晚于发生时间的最新版本。
维表按传入顺序视为到达顺序：某版本生效时间早于同主键已到达版本的最大生效
时间时判定为迟到，迟到版本回补受影响的事实并记录回补批次。
关联键为空或没有可匹配版本时保留事实行并标记 matched=False。
"""

KEY_FIELD = "id"
FACT_TIME_FIELD = "event_time"
DIM_TIME_FIELD = "effective_time"
UNMATCHED_SAMPLE_LIMIT = 5


class JoinResult(list):
    """关联结果行列表，stats 属性保存匹配统计。"""

    def __init__(self, rows, stats):
        super().__init__(rows)
        self.stats = stats


class _Version:
    __slots__ = ("row", "effective_time", "arrival", "late")

    def __init__(self, row, arrival):
        self.row = row
        self.effective_time = row.get(DIM_TIME_FIELD)
        self.arrival = arrival
        self.late = False

    def sort_key(self):
        # 无生效时间的版本视为初始版本，按到达顺序排在带时间版本之前。
        if self.effective_time is None:
            return (0, self.arrival)
        return (1, self.effective_time, self.arrival)


def _build_chains(dims):
    chains = {}
    max_seen = {}
    for arrival, row in enumerate(dims):
        key = row.get(KEY_FIELD)
        if key is None:
            continue
        version = _Version(row, arrival)
        effective_time = version.effective_time
        if effective_time is not None:
            seen = max_seen.get(key)
            if seen is not None and seen > effective_time:
                version.late = True
            else:
                max_seen[key] = effective_time
        chains.setdefault(key, []).append(version)
    return chains


def _match(versions, event_time):
    if not versions:
        return None
    if event_time is None:
        return max(versions, key=_Version.sort_key)
    best = None
    for version in versions:
        if version.effective_time is not None and version.effective_time > event_time:
            continue
        if best is None or version.sort_key() > best.sort_key():
            best = version
    return best


def join(facts, dims):
    chains = _build_chains(dims)
    ontime_chains = {
        key: [v for v in versions if not v.late]
        for key, versions in chains.items()
    }
    late_versions = [v for versions in chains.values() for v in versions if v.late]
    affected_by_late = {id(v): [] for v in late_versions}

    rows = []
    matched = 0
    backfilled = 0
    unmatched_samples = []
    for fact in facts:
        key = fact.get(KEY_FIELD)
        event_time = fact.get(FACT_TIME_FIELD)
        final = _match(chains.get(key), event_time) if key is not None else None
        ontime = _match(ontime_chains.get(key), event_time) if key is not None else None
        was_backfilled = final is not None and final is not ontime
        if was_backfilled:
            backfilled += 1
            affected_by_late[id(final)].append(fact)
        rows.append({
            **fact,
            "dim": final.row if final else None,
            "matched": final is not None,
            "backfilled": was_backfilled,
        })
        if final is None:
            if len(unmatched_samples) < UNMATCHED_SAMPLE_LIMIT:
                unmatched_samples.append(fact)
        else:
            matched += 1

    batches = [
        {"dim": v.row, "affected": len(affected_by_late[id(v)]),
         "facts": affected_by_late[id(v)]}
        for v in late_versions
        if affected_by_late[id(v)]
    ]
    total = len(facts)
    stats = {
        "total": total,
        "matched": matched,
        "unmatched": total - matched,
        "match_rate": (matched / total) if total else 0.0,
        "backfilled": backfilled,
        "backfill_batches": batches,
        "unmatched_samples": unmatched_samples,
    }
    return JoinResult(rows, stats)
