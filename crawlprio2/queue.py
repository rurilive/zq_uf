"""按站点配额的采集调度队列。

核心规则：
- 出队按站点轮转，同一站点不能连续占满一批出队名额；
- 等待时间按固定速率提权，提权有上限（max_boost）；
- 高优任务只在本站队列内插队，出队一律消耗本站当日名额，无独立通道；
- 站点配额用尽返回次日 0 点前的等待秒数，未出队条目保持原排队；
- 放弃（release）的条目重新入队，保留首次入队时间；
- 租户之间状态与配额完全隔离；
- 站点连续 starve_days 天没有出队且仍有待出条目时产生饥饿告警。
"""

from .records import RecordLog

DEFAULT_TENANT = "default"


def _day_of(ts):
    """天序号：以秒级时间戳换算的自然天（默认 UTC，可注入 clock 统一控制）。"""
    return int(ts // 86400)


def _day_start(day):
    return day * 86400


class _Item:
    __slots__ = ("id", "site", "priority", "enqueued_at", "attempt", "task")

    def __init__(self, task, enqueued_at, attempt):
        self.id = task["id"]
        self.site = task.get("site")
        self.priority = task.get("priority", 0)
        self.enqueued_at = enqueued_at
        self.attempt = attempt
        self.task = dict(task)

    def effective_priority(self, now, aging_rate, max_boost):
        """提权后的优先级 = 基础优先级 + min(等待时长 * 提权速率, 提权上限)。"""
        boost = min(max(0.0, now - self.enqueued_at) * aging_rate, max_boost)
        return self.priority + boost, boost

    def snapshot(self):
        return {
            "id": self.id,
            "site": self.site,
            "priority": self.priority,
            "enqueued_at": self.enqueued_at,
            "attempt": self.attempt,
            "task": dict(self.task),
        }


class _TenantState:
    def __init__(self):
        self.queues = {}          # site -> [_Item]
        self.daily_quota = {}     # site -> 每日名额
        self.used = {}            # site -> 当日已用
        self.last_dequeue_day = {}  # site -> 最近一次出队的天序号
        self.day = None
        self.inflight = {}        # id -> (site, _Item, dequeued_at)
        self.ids = set()          # 全部在队/在途条目 id，防止重复入队


class CrawlQueue:
    """站点公平 + 等待提权 + 每日配额的多租户采集队列。"""

    def __init__(self, clock=None, aging_rate=1.0 / 60.0,
                 max_boost=5.0, default_daily_quota=10000,
                 starve_days=3):
        # clock 返回秒级时间戳，默认 UTC 实时时钟，测试与 demo 可注入假时钟。
        import time
        self._clock = clock or time.time
        self._aging_rate = aging_rate
        self._max_boost = max_boost
        self._default_daily_quota = default_daily_quota
        self._starve_days = starve_days
        self._tenants = {}
        self._records = RecordLog()

    # ---------- 内部工具 ----------

    def _state(self, tenant):
        st = self._tenants.get(tenant)
        if st is None:
            st = _TenantState()
            st.day = _day_of(self._clock())
            self._tenants[tenant] = st
        self._rollover(st)
        return st

    def _rollover(self, st):
        """跨天后重置当日已用配额。"""
        today = _day_of(self._clock())
        if st.day != today:
            st.day = today
            st.used = {site: 0 for site in st.queues}

    def _quota(self, st, site):
        return st.daily_quota.get(site, self._default_daily_quota)

    def _remaining(self, st, site):
        return self._quota(st, site) - st.used.get(site, 0)

    def _pick_best(self, items, now):
        """站点内选提权后优先级最高者，同优先级按入队时间、id 排序（FIFO、确定性）。"""
        best = None
        best_eff = None
        for item in items:
            eff, _ = item.effective_priority(
                now, self._aging_rate, self._max_boost)
            key = (eff, -item.enqueued_at, item.id)
            if best is None or key > best_eff:
                best, best_eff = item, key
        return best

    # ---------- 配置 ----------

    def configure_site(self, tenant, site, daily_quota):
        """配置站点每日名额；运营也可在当天手动调大，立即生效。"""
        st = self._state(tenant)
        st.daily_quota[site] = daily_quota

    set_quota = configure_site

    def remaining_quota(self, tenant, site):
        st = self._state(tenant)
        return self._remaining(st, site)

    # ---------- 入队 ----------

    def push(self, task, tenant=DEFAULT_TENANT):
        if "id" not in task:
            raise ValueError("task 必须包含 id")
        site = task.get("site")
        if site is None:
            raise ValueError("task 必须包含 site")
        st = self._state(tenant)
        if task["id"] in st.ids:
            return {"queued": len(st.queues.get(site, [])),
                    "priority": task.get("priority", 0),
                    "duplicate": True}
        item = _Item(task, self._clock(), 1)
        st.queues.setdefault(site, []).append(item)
        st.ids.add(item.id)
        st.used.setdefault(site, 0)
        return {"queued": len(st.queues[site]),
                "priority": item.priority,
                "duplicate": False}

    # ---------- 出队 ----------

    def pop(self, tenant=DEFAULT_TENANT, batch=1):
        """按站点轮转出队一批条目。

        每轮每个有名额的站点最多出 1 条，站点间按提权后优先级排序，
        因此同一站点无法连续占满整批名额。
        """
        st = self._state(tenant)
        now = self._clock()
        items_out = []
        blocked_sites = set()

        while len(items_out) < batch:
            candidates = []
            for site, queue in st.queues.items():
                if not queue:
                    continue
                if self._remaining(st, site) <= 0:
                    blocked_sites.add(site)
                    continue
                best = self._pick_best(queue, now)
                eff, boost = best.effective_priority(
                    now, self._aging_rate, self._max_boost)
                candidates.append((site, best, eff, boost))
            if not candidates:
                break

            # 一轮内：提权后优先级高的站点先出，每站只出一条 => 轮转公平。
            candidates.sort(
                key=lambda c: (c[2], -c[1].enqueued_at, c[1].id),
                reverse=True)
            for site, item, eff, boost in candidates:
                if len(items_out) >= batch or self._remaining(st, site) <= 0:
                    continue
                st.queues[site].remove(item)
                st.used[site] = st.used.get(site, 0) + 1
                st.last_dequeue_day[site] = st.day
                st.inflight[item.id] = (site, item, now)
                waited = now - item.enqueued_at
                entry = {
                    "id": item.id,
                    "site": site,
                    "priority": item.priority,
                    "boost": round(boost, 6),
                    "effective_priority": round(eff, 6),
                    "waited_seconds": waited,
                    "attempt": item.attempt,
                    "enqueued_at": item.enqueued_at,
                    "dequeued_at": now,
                    "task": dict(item.task),
                }
                items_out.append(entry)
                self._records.add(tenant, site, {
                    "dequeued_at": now,
                    "day": st.day,
                    "tenant": tenant,
                    "site": site,
                    "id": item.id,
                    "priority": item.priority,
                    "boost": round(boost, 6),
                    "effective_priority": round(eff, 6),
                    "waited_seconds": waited,
                    "attempt": item.attempt,
                })
                if len(items_out) >= batch:
                    break

        # 还有待出条目但所有相关站点配额用尽：给出可等待时间（到次日 0 点）。
        pending_sites = {site for site, q in st.queues.items() if q}
        blocked_sites = {
            site for site in pending_sites
            if self._remaining(st, site) <= 0}
        retry_after = None
        if pending_sites and blocked_sites == pending_sites:
            retry_after = max(0, _day_start(st.day + 1) - now)

        result = {
            "items": items_out,
            "remaining_quota": {
                site: self._remaining(st, site)
                for site in st.queues
                if st.queues[site] or site in blocked_sites},
            "retry_after_seconds": retry_after,
            "blocked_sites": sorted(blocked_sites),
            "starvation_alerts": self._starvation(st, now),
        }
        return result

    # ---------- 确认 / 放弃 ----------

    def ack(self, task_id, tenant=DEFAULT_TENANT):
        """处理成功：从在途表移除，释放 id。"""
        st = self._state(tenant)
        inflight = st.inflight.pop(task_id, None)
        if inflight is None:
            return {"id": task_id, "acked": False}
        st.ids.discard(task_id)
        return {"id": task_id, "acked": True}

    def release(self, task_id, tenant=DEFAULT_TENANT):
        """放弃的条目重新入队：保留首次入队时间与等待资历，尝试次数 +1。"""
        st = self._state(tenant)
        inflight = st.inflight.pop(task_id, None)
        if inflight is None:
            return {"id": task_id, "requeued": False}
        site, item, _ = inflight
        item.attempt += 1
        st.queues.setdefault(site, []).append(item)
        st.used.setdefault(site, 0)
        return {"id": task_id, "requeued": True,
                "site": site, "attempt": item.attempt}

    requeue = release

    # ---------- 小站饥饿告警 ----------

    def _starvation(self, st, now):
        """仍有待出条目、且连续 starve_days 天没有出队的站点。"""
        alerts = []
        today = _day_of(now)
        for site, queue in st.queues.items():
            if not queue:
                continue
            last_day = st.last_dequeue_day.get(site)
            if last_day is None:
                gap = today - min(_day_of(i.enqueued_at) for i in queue)
            else:
                gap = today - last_day
            if gap >= self._starve_days:
                alerts.append({
                    "site": site,
                    "days_without_dequeue": gap,
                    "pending": len(queue),
                    "current_daily_quota": self._quota(st, site),
                    "message": (
                        f"站点 {site} 已连续 {gap} 天没有出队，"
                        f"仍有 {len(queue)} 条待出，建议运营手动调大每日名额"),
                })
        alerts.sort(key=lambda a: (-a["days_without_dequeue"], a["site"]))
        return alerts

    def starvation_alerts(self, tenant=DEFAULT_TENANT):
        st = self._state(tenant)
        return self._starvation(st, self._clock())

    # ---------- 记录查询 / 导出 / 回放 ----------

    def records(self, tenant=DEFAULT_TENANT, site=None, day=None):
        return self._records.query(tenant, site, day)

    def export_records(self, tenant=DEFAULT_TENANT, site=None,
                       day=None, fmt="json"):
        return self._records.export(tenant, site, day, fmt)

    def replay_day(self, day, tenant=DEFAULT_TENANT, site=None):
        """把某天的出队记录按原顺序重新入队；已在队/在途的 id 跳过。"""
        st = self._state(tenant)
        snapshots = self._records.snapshots_for_replay(tenant, day, site)
        replayed, skipped = [], []
        for row in snapshots:
            if row["id"] in st.ids:
                skipped.append(row["id"])
                continue
            item = _Item(
                {"id": row["id"], "site": row["site"],
                 "priority": row["priority"]},
                row["dequeued_at"], row["attempt"])
            st.queues.setdefault(row["site"], []).append(item)
            st.ids.add(item.id)
            st.used.setdefault(row["site"], 0)
            replayed.append(row["id"])
        return {"day": day, "replayed": replayed, "skipped": skipped}


# ---------- 模块级便捷 API：作用于全局默认队列，保持与旧版调用兼容 ----------

_default = CrawlQueue()


def push(task, tenant=DEFAULT_TENANT):
    return _default.push(task, tenant)


def pop(tenant=DEFAULT_TENANT, batch=1):
    return _default.pop(tenant, batch)


def ack(task_id, tenant=DEFAULT_TENANT):
    return _default.ack(task_id, tenant)


def release(task_id, tenant=DEFAULT_TENANT):
    return _default.release(task_id, tenant)


def configure_site(tenant, site, daily_quota):
    return _default.configure_site(tenant, site, daily_quota)
