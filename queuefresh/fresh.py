"""采集队列：按新鲜度衰减和等待提权动态排序。"""
import time


class Queue:
    """新鲜度调度队列，分数小的先出，同分按入队顺序。

    分数公式：
        score = priority
              + stale_weight * (1 - 2 ** (-age / half_life))
              - boost_step * floor(wait / boost_threshold)
              + expired_penalty            （仅当 now > deadline）
    age = now - published_at，wait = now - 入队时刻。

    分数比较带 _EPS 容差：差值小于容差视为同分，按入队顺序出队，
    避免连续入队的真实时钟微秒差破坏 FIFO。
    """

    _EPS = 1e-6

    def __init__(self, half_life=3600.0, boost_threshold=600.0,
                 boost_step=1.0, stale_weight=4.0,
                 expired_penalty=1000.0, clock=time.time):
        self._half_life = self._positive("half_life", half_life)
        self._boost_threshold = self._positive("boost_threshold", boost_threshold)
        self._boost_step = self._nonnegative("boost_step", boost_step)
        self._stale_weight = self._nonnegative("stale_weight", stale_weight)
        self._expired_penalty = self._nonnegative("expired_penalty", expired_penalty)
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._entries = []
        self._seq = 0

    @staticmethod
    def _number(name, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                "%s must be a number, got %s" % (name, type(value).__name__))
        return float(value)

    @classmethod
    def _positive(cls, name, value):
        value = cls._number(name, value)
        if value <= 0:
            raise ValueError("%s must be > 0, got %r" % (name, value))
        return value

    @classmethod
    def _nonnegative(cls, name, value):
        value = cls._number(name, value)
        if value < 0:
            raise ValueError("%s must be >= 0, got %r" % (name, value))
        return value

    def push(self, task):
        """入队并快照任务字段，返回当前队列长度。

        task 为 dict，可选字段：priority（默认 5）、published_at（默认入队
        时刻）、deadline（默认 None，即不过期）。非法输入抛出
        TypeError/ValueError，不会静默跳过。
        """
        if not isinstance(task, dict):
            raise TypeError("task must be a dict, got %s" % type(task).__name__)
        snapshot = dict(task)
        snapshot["priority"] = self._number(
            "priority", snapshot.get("priority", 5))
        now = self._clock()
        snapshot["published_at"] = self._number(
            "published_at", snapshot.get("published_at", now))
        deadline = snapshot.get("deadline")
        if deadline is not None:
            deadline = self._number("deadline", deadline)
            if deadline < snapshot["published_at"]:
                raise ValueError(
                    "deadline (%r) is before published_at (%r)"
                    % (deadline, snapshot["published_at"]))
        snapshot["deadline"] = deadline
        self._entries.append({
            "seq": self._seq,
            "enqueued_at": now,
            "task": snapshot,
        })
        self._seq += 1
        return len(self._entries)

    def pop(self):
        """弹出当前分数最小的任务；空队列返回 None。

        返回任务快照的副本，附带 score（当前分数）和 boosts（提权次数）。
        """
        if not self._entries:
            return None
        now = self._clock()
        best = min(range(len(self._entries)),
                   key=lambda i: self._sort_key(self._entries[i], now))
        entry = self._entries.pop(best)
        score, boosts = self._score(entry, now)
        task = dict(entry["task"])
        task["score"] = score
        task["boosts"] = boosts
        return task

    def snapshot(self):
        """按出队顺序返回 [{task, score, boosts}]，用于观察当前队列。"""
        now = self._clock()
        rows = []
        for entry in self._entries:
            score, boosts = self._score(entry, now)
            rows.append({"task": dict(entry["task"]), "score": score,
                         "boosts": boosts, "seq": entry["seq"]})
        rows.sort(key=lambda row: (round(row["score"] / self._EPS),
                                   row["seq"]))
        for row in rows:
            del row["seq"]
        return rows

    def size(self):
        return len(self._entries)

    def _sort_key(self, entry, now):
        score, _ = self._score(entry, now)
        return (round(score / self._EPS), entry["seq"])

    def _score(self, entry, now):
        task = entry["task"]
        age = max(0.0, now - task["published_at"])
        staleness = self._stale_weight * (1.0 - 2.0 ** (-age / self._half_life))
        wait = max(0.0, now - entry["enqueued_at"])
        boosts = int(wait // self._boost_threshold)
        score = task["priority"] + staleness - self._boost_step * boosts
        if task["deadline"] is not None and now > task["deadline"]:
            score += self._expired_penalty
        return score, boosts
