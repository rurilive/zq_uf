"""按站点权重公平调度的采集任务队列（纯标准库）。"""

import time
from collections import deque

_DEFAULT_SITE = "default"


class TaskQueue:
    """按站点权重轮流出队的公平队列。

    - 每个站点一个先进先出子队列，重试任务回到原站点队列尾部。
    - 调度采用平滑加权轮询：长期出队比例约等于站点权重比。
    - 同权重时按队头任务的入队时间排序，等待最久的站点先出队。
    - quota 限制每个站点的在途任务数，避免慢站占满并发池。
    """

    def __init__(self, weights=None, quota=None, clock=None):
        self._weights = {}
        for site, weight in (weights or {}).items():
            self.set_weight(site, weight)
        if quota is not None:
            if isinstance(quota, bool) or not isinstance(quota, int):
                raise TypeError("quota 必须是正整数或 None，收到 %r" % (quota,))
            if quota < 1:
                raise ValueError("quota 必须 >= 1，收到 %r" % (quota,))
        self._quota = quota
        self._clock = clock if clock is not None else time.monotonic
        self._queues = {}
        self._scores = {}
        self._inflight = {}
        self._inflight_tasks = {}
        self._stats = {}

    @staticmethod
    def _check_site(site):
        if not isinstance(site, str):
            raise TypeError("site 必须是字符串，收到 %r" % (site,))
        if not site:
            raise ValueError("site 不能为空字符串")

    @staticmethod
    def _check_weight(weight):
        if isinstance(weight, bool) or not isinstance(weight, (int, float)):
            raise TypeError("weight 必须是数字，收到 %r" % (weight,))
        if weight <= 0:
            raise ValueError("weight 必须 > 0，收到 %r" % (weight,))

    def set_weight(self, site, weight):
        """配置站点权重，缺省为 1。"""
        self._check_site(site)
        self._check_weight(weight)
        self._weights[site] = weight

    def _ensure_site(self, site):
        if site not in self._queues:
            self._queues[site] = deque()
            self._scores[site] = 0.0
            self._inflight[site] = 0
            self._stats[site] = {
                "dequeued": 0,
                "retries": 0,
                "total_wait": 0.0,
                "max_wait": 0.0,
            }

    def push(self, task):
        """入队。重试任务（retry 为真）回到原站点队列尾部。"""
        if not isinstance(task, dict):
            raise TypeError("task 必须是 dict，收到 %r" % (task,))
        site = task.get("site", _DEFAULT_SITE)
        self._check_site(site)
        self._ensure_site(site)
        self._queues[site].append((task, self._clock()))
        if task.get("retry"):
            self._stats[site]["retries"] += 1
        return task

    def next(self, worker=None):
        """按权重轮转出队一个任务；无可调度任务时返回 None。"""
        eligible = [
            site
            for site, queue in self._queues.items()
            if queue
            and (self._quota is None or self._inflight[site] < self._quota)
        ]
        if not eligible:
            return None
        total = 0.0
        for site in eligible:
            weight = self._weights.get(site, 1)
            self._scores[site] += weight
            total += weight
        best = max(
            eligible,
            key=lambda site: (self._scores[site], -self._queues[site][0][1]),
        )
        self._scores[best] -= total
        task, enqueued_at = self._queues[best].popleft()
        self._inflight[best] += 1
        self._inflight_tasks[id(task)] = best
        wait = self._clock() - enqueued_at
        stats = self._stats[best]
        stats["dequeued"] += 1
        stats["total_wait"] += wait
        stats["max_wait"] = max(stats["max_wait"], wait)
        return task

    def done(self, task):
        """标记任务完成，释放站点在途配额。任务不在在途集合中时抛 KeyError。"""
        key = id(task)
        if key not in self._inflight_tasks:
            raise KeyError("任务不在在途集合中，无法标记完成: %r" % (task,))
        site = self._inflight_tasks.pop(key)
        self._inflight[site] -= 1

    def size(self):
        return sum(len(queue) for queue in self._queues.values())

    def stats(self):
        """各站点的等待时长与出队统计。"""
        result = {}
        for site in self._queues:
            stats = self._stats[site]
            dequeued = stats["dequeued"]
            result[site] = {
                "pending": len(self._queues[site]),
                "in_flight": self._inflight[site],
                "dequeued": dequeued,
                "retries": stats["retries"],
                "total_wait": stats["total_wait"],
                "avg_wait": stats["total_wait"] / dequeued if dequeued else 0.0,
                "max_wait": stats["max_wait"],
            }
        return result
