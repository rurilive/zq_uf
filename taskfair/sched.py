"""带权重轮转、保底配额和抢占续跑的多队列调度器。

模块级函数（submit/next_task/...）操作一个默认调度器实例；
需要多个实例或注入时钟时直接使用 :class:`Scheduler`。
"""

import time
from collections import deque


class SchedulerError(Exception):
    """调度器配置或状态错误。"""


def _positive_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SchedulerError("%s 必须是正整数，收到 %r" % (field, value))
    return value


def _non_negative_int(value, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchedulerError("%s 必须是非负整数，收到 %r" % (field, value))
    return value


def _check_queue_name(queue):
    if not isinstance(queue, str) or not queue:
        raise SchedulerError("queue 必须是非空字符串，收到 %r" % (queue,))


class _Queue:
    def __init__(self, name, weight=1, guarantee=0, priority=0):
        self.name = name
        self.weight = weight
        self.guarantee = guarantee
        self.priority = priority
        self.entries = deque()
        self.queued_ids = set()
        self.credits = 0
        self.dequeued = 0
        self.total_wait = 0.0
        self.max_wait = 0.0
        self.preemptions = 0

    def snapshot(self):
        return {
            "name": self.name,
            "weight": self.weight,
            "guarantee": self.guarantee,
            "priority": self.priority,
            "entries": [dict(entry) for entry in self.entries],
            "queued_ids": list(self.queued_ids),
            "credits": self.credits,
            "dequeued": self.dequeued,
            "total_wait": self.total_wait,
            "max_wait": self.max_wait,
            "preemptions": self.preemptions,
        }


class Scheduler:
    """多队列加权轮转调度器。

    weight 决定每轮轮转各队列可出队的任务数；guarantee 是每轮保底
    出队数（与权重份额叠加，按队列单独配置）；priority 越大优先级越高，
    只用于抢占判断，不影响正常出队顺序。
    """

    def __init__(self, clock=time.time):
        self._clock = clock
        self._queues = {}
        self._seq = 0
        self._round = None
        self._running = {}

    def configure(self, queue, weight=1, guarantee=0, priority=0):
        """配置队列权重、保底额度和优先级，返回该队列的生效配置。"""
        _check_queue_name(queue)
        _positive_int(weight, "weight")
        _non_negative_int(guarantee, "guarantee")
        _non_negative_int(priority, "priority")
        q = self._queues.get(queue)
        if q is None:
            q = _Queue(queue)
            self._queues[queue] = q
        q.weight = weight
        q.guarantee = guarantee
        q.priority = priority
        return {
            "queue": queue,
            "weight": weight,
            "guarantee": guarantee,
            "priority": priority,
        }

    def submit(self, queue, task):
        """提交任务到指定队列，返回任务序号（保持旧接口的 int 返回值）。"""
        _check_queue_name(queue)
        if not hasattr(task, "keys") or not hasattr(task, "__getitem__"):
            raise SchedulerError("task 必须是映射（dict 风格），收到 %r" % (task,))
        if "id" not in task or task["id"] is None:
            raise SchedulerError("task 必须包含非空 id 字段，收到 %r" % (task,))
        task_id = task["id"]
        if isinstance(task_id, bool) or not isinstance(task_id, (str, int, tuple)):
            raise SchedulerError("task id 必须是 str/int/tuple，收到 %r" % (task_id,))
        if task_id in self._running:
            raise SchedulerError("任务 %r 正在执行中，不能重复提交" % (task_id,))
        q = self._queues.get(queue)
        if q is None:
            q = _Queue(queue)
            self._queues[queue] = q
        if task_id in q.queued_ids:
            raise SchedulerError("任务 %r 已在队列 %s 中，不能重复提交"
                                 % (task_id, queue))
        self._seq += 1
        entry = {
            "seq": self._seq,
            "task": dict(task),
            "enqueued_at": self._clock(),
            "attempts": 0,
        }
        q.entries.append(entry)
        q.queued_ids.add(task_id)
        return self._seq

    def _ensure_round(self):
        if self._round is not None:
            return
        self._round = {name: 0 for name in self._queues}
        for q in self._queues.values():
            q.credits = q.weight

    def _backlogged(self):
        return [q for q in self._queues.values() if q.entries]

    def _pick_by_wait(self, candidates):
        """同权重/同保底份额时按队头等待时间（再按提交序号）选队列。"""
        now = self._clock()

        def key(q):
            head = q.entries[0]
            return (now - head["enqueued_at"], head["seq"])

        return max(candidates, key=key)

    def next_task(self):
        """返回下一个应执行的任务 dict；没有可调度任务时返回 None。

        每轮先消耗各队列保底份额，再按权重轮转；同权重按等待最久的
        队列优先，同队列内部严格 FIFO。返回值即用户提交的 task 副本，
        不注入 preempted 等内部字段。
        """
        self._ensure_round()
        for name, q in self._queues.items():
            if name not in self._round:
                self._round[name] = 0
                q.credits = q.weight
        backlogged = self._backlogged()
        if not backlogged:
            return None

        guaranteed = [q for q in backlogged
                      if self._round[q.name] < q.guarantee]
        if guaranteed:
            q = self._pick_by_wait(guaranteed)
        else:
            eligible = [q for q in backlogged if q.credits > 0]
            if not eligible:
                self._round = None
                return self.next_task()
            min_weight = min(cand.weight for cand in eligible)
            if all(cand.weight == min_weight for cand in eligible):
                q = self._pick_by_wait(eligible)
            else:
                q = max(eligible, key=lambda item: item.credits)

        entry = q.entries.popleft()
        q.queued_ids.discard(entry["task"]["id"])
        now = self._clock()
        waited = now - entry["enqueued_at"]
        entry["attempts"] += 1
        q.total_wait += waited
        if waited > q.max_wait:
            q.max_wait = waited
        q.dequeued += 1
        self._round[q.name] += 1
        q.credits -= 1

        task = dict(entry["task"])
        self._running[task["id"]] = {
            "queue": q.name,
            "entry": entry,
            "started_at": now,
        }
        return task

    # next 是 next_task 的短名别名，满足 next() 接口要求。
    next = next_task

    def preempt(self, task_id=None, progress=None):
        """抢占正在执行的任务，保存进度并放回队头以便续跑。

        不传 task_id 时抢占优先级最低的运行任务，供高优队列触发；
        progress 写入任务的 progress 字段，下次出队随任务返回。
        """
        if not self._running:
            raise SchedulerError("当前没有正在执行的任务，无法抢占")
        if task_id is None:
            target_id = min(
                self._running,
                key=lambda tid: self._queues[self._running[tid]["queue"]].priority,
            )
        else:
            if task_id not in self._running:
                raise SchedulerError("任务 %r 不在执行中，无法抢占" % (task_id,))
            target_id = task_id
        record = self._running.pop(target_id)
        q = self._queues[record["queue"]]
        entry = record["entry"]
        if progress is not None:
            entry["task"]["progress"] = progress
        entry["enqueued_at"] = self._clock()
        q.entries.appendleft(entry)
        q.queued_ids.add(target_id)
        q.preemptions += 1
        return dict(entry["task"])

    def complete(self, task_id=None):
        """标记任务执行完成并释放执行位，返回完成的任务 id。"""
        if not self._running:
            raise SchedulerError("当前没有正在执行的任务，无法完成")
        if task_id is None:
            task_id = next(reversed(self._running))
        if task_id not in self._running:
            raise SchedulerError("任务 %r 不在执行中，无法完成" % (task_id,))
        del self._running[task_id]
        return task_id

    def stats(self):
        """返回各队列出队数、等待时长和抢占次数。"""
        result = {}
        for name, q in self._queues.items():
            avg_wait = q.total_wait / q.dequeued if q.dequeued else 0.0
            running = sum(1 for rec in self._running.values()
                          if rec["queue"] == name)
            result[name] = {
                "submitted": len(q.entries) + q.dequeued + running,
                "dequeued": q.dequeued,
                "waiting": len(q.entries),
                "running": running,
                "total_wait": q.total_wait,
                "avg_wait": avg_wait,
                "max_wait": q.max_wait,
                "preemptions": q.preemptions,
            }
        return result

    def to_dict(self):
        """导出可 JSON 序列化的完整调度状态。"""
        return {
            "version": 1,
            "seq": self._seq,
            "round": None if self._round is None else dict(self._round),
            "queues": [q.snapshot() for q in self._queues.values()],
            "running": [
                {
                    "task_id": task_id,
                    "queue": rec["queue"],
                    "entry": dict(rec["entry"]),
                    "started_at": rec["started_at"],
                }
                for task_id, rec in self._running.items()
            ],
        }

    @classmethod
    def from_dict(cls, data, clock=time.time):
        """从快照恢复调度器，结构不合法时抛出 SchedulerError。"""
        if not isinstance(data, dict) or data.get("version") != 1:
            raise SchedulerError("调度快照格式无效或版本不支持")
        if not isinstance(data.get("queues"), list):
            raise SchedulerError("调度快照缺少 queues 列表")
        if not isinstance(data.get("seq"), int) or isinstance(data.get("seq"), bool):
            raise SchedulerError("调度快照的 seq 字段无效")
        round_data = data.get("round")
        if round_data is not None and not isinstance(round_data, dict):
            raise SchedulerError("调度快照的 round 字段无效")
        scheduler = cls(clock=clock)
        try:
            scheduler._seq = data["seq"]
            for qs in data["queues"]:
                q = _Queue(
                    qs["name"],
                    weight=qs["weight"],
                    guarantee=qs["guarantee"],
                    priority=qs["priority"],
                )
                q.entries = deque(
                    {
                        "seq": entry["seq"],
                        "task": dict(entry["task"]),
                        "enqueued_at": entry["enqueued_at"],
                        "attempts": entry["attempts"],
                    }
                    for entry in qs["entries"]
                )
                if "queued_ids" in qs:
                    q.queued_ids = set(qs["queued_ids"])
                else:
                    q.queued_ids = {
                        entry["task"]["id"] for entry in q.entries
                    }
                q.credits = qs["credits"]
                q.dequeued = qs["dequeued"]
                q.total_wait = qs["total_wait"]
                q.max_wait = qs["max_wait"]
                q.preemptions = qs["preemptions"]
                if q.name in scheduler._queues:
                    raise SchedulerError("调度快照存在重复队列: %s" % q.name)
                scheduler._queues[q.name] = q
            if round_data is not None:
                unknown = set(round_data) - set(scheduler._queues)
                if unknown:
                    raise SchedulerError("调度快照 round 含未知队列: %s" % unknown)
                scheduler._round = dict(round_data)
            for rec in data["running"]:
                entry = rec["entry"]
                if rec["queue"] not in scheduler._queues:
                    raise SchedulerError(
                        "运行任务引用未知队列: %s" % rec["queue"])
                scheduler._running[rec["task_id"]] = {
                    "queue": rec["queue"],
                    "entry": {
                        "seq": entry["seq"],
                        "task": dict(entry["task"]),
                        "enqueued_at": entry["enqueued_at"],
                        "attempts": entry["attempts"],
                    },
                    "started_at": rec["started_at"],
                }
        except KeyError as exc:
            raise SchedulerError("调度快照缺少字段: %s" % exc.args[0]) from exc
        except (TypeError, ValueError) as exc:
            raise SchedulerError("调度快照结构损坏: %s" % exc) from exc
        return scheduler


_default = Scheduler()


def reset():
    """重置默认调度器（测试辅助）。"""
    global _default
    _default = Scheduler()


def configure(queue, weight=1, guarantee=0, priority=0):
    return _default.configure(queue, weight, guarantee, priority)


def submit(queue, task):
    return _default.submit(queue, task)


def next_task():
    return _default.next()


def preempt(task_id=None, progress=None):
    return _default.preempt(task_id, progress)


def complete(task_id=None):
    return _default.complete(task_id)


def stats():
    return _default.stats()


def snapshot():
    return _default.to_dict()


def restore(data):
    """从快照恢复默认调度器。"""
    global _default
    _default = Scheduler.from_dict(data)

