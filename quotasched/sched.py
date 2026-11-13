"""数据任务调度：按资源装箱，高优先级可抢占并保存进度。"""

import time


class Queue:
    """资源感知的任务队列。

    - submit(task) 校验资源字段后入队；next() 按 (优先级降序, 入队先后)
      扫描等待队列，派出第一个能装进剩余容量的任务。
    - 队首任务装不下时，可抢占优先级严格更低的运行任务；被抢占任务
      保留 progress 并回到等待队列。
    - 默认容量 cpu=64, mem=256；clock 可注入便于测试与复现。
    """

    def __init__(self, cpu=64, mem=256, clock=None):
        if cpu <= 0:
            raise ValueError(f"capacity cpu must be > 0, got {cpu!r}")
        if mem < 0:
            raise ValueError(f"capacity mem must be >= 0, got {mem!r}")
        self.capacity = {"cpu": cpu, "mem": mem}
        self._clock = clock or time.monotonic
        self._waiting = []  # list[state]，按入队顺序
        self._running = {}  # id -> state
        self._seq = 0
        self._waits = {}  # id -> [每次派出的等待秒数]
        self._preemptions = 0
        self._dispatched = 0

    # ---- 入队 ----

    def submit(self, task):
        """校验并入队，返回当前等待任务数。非法任务直接抛错，不静默跳过。"""
        state = self._validate(task)
        self._waiting.append(state)
        return len(self._waiting)

    def _validate(self, task):
        if not isinstance(task, dict):
            raise TypeError(f"task must be a dict, got {type(task).__name__}")
        if "id" not in task:
            raise ValueError("task missing required field 'id'")
        tid = task["id"]
        if tid in self._running or any(s["id"] == tid for s in self._waiting):
            raise ValueError(f"duplicate task id: {tid!r}")
        cpu = task.get("cpu", 1)
        mem = task.get("mem", 0)
        for name, value in (("cpu", cpu), ("mem", mem)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number, got {value!r}")
        if cpu <= 0:
            raise ValueError(f"cpu must be > 0, got {cpu!r}")
        if mem < 0:
            raise ValueError(f"mem must be >= 0, got {mem!r}")
        if cpu > self.capacity["cpu"]:
            raise ValueError(
                f"cpu {cpu} exceeds capacity {self.capacity['cpu']}; task can never run"
            )
        if mem > self.capacity["mem"]:
            raise ValueError(
                f"mem {mem} exceeds capacity {self.capacity['mem']}; task can never run"
            )
        priority = task.get("priority", 0)
        if isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError(f"priority must be an int, got {priority!r}")
        progress = task.get("progress", 0.0)
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            raise ValueError(f"progress must be a number, got {progress!r}")
        if not 0.0 <= progress <= 1.0:
            raise ValueError(f"progress must be in [0, 1], got {progress!r}")
        state = {
            "task": dict(task),
            "id": tid,
            "cpu": cpu,
            "mem": mem,
            "priority": priority,
            "progress": float(progress),
            "seq": self._seq,
            "enqueued_at": self._clock(),
            "preempted": 0,
        }
        self._seq += 1
        return state

    # ---- 派出 ----

    def next(self):
        """派出下一个任务并占用资源；无任务或都装不下时返回 None。"""
        if not self._waiting:
            return None
        self._preempt_for_head()
        state = self._first_that_fits()
        if state is None:
            return None
        return self._dispatch(state)

    def _remaining(self):
        rem = dict(self.capacity)
        for s in self._running.values():
            rem["cpu"] -= s["cpu"]
            rem["mem"] -= s["mem"]
        return rem

    def _fits(self, state, rem):
        return state["cpu"] <= rem["cpu"] and state["mem"] <= rem["mem"]

    def _head(self):
        return min(self._waiting, key=lambda s: (-s["priority"], s["seq"]))

    def _first_that_fits(self):
        rem = self._remaining()
        for s in sorted(self._waiting, key=lambda s: (-s["priority"], s["seq"])):
            if self._fits(s, rem):
                return s
        return None

    def _preempt_for_head(self):
        """队首任务装不下时，抢占优先级严格更低的运行任务。

        受害者按 (优先级升序, 进度升序) 选取；全部抢占仍装不下则不抢占。
        """
        head = self._head()
        rem = self._remaining()
        if self._fits(head, rem):
            return
        victims = [s for s in self._running.values() if s["priority"] < head["priority"]]
        victims.sort(key=lambda s: (s["priority"], s["progress"]))
        chosen = []
        for v in victims:
            chosen.append(v)
            rem["cpu"] += v["cpu"]
            rem["mem"] += v["mem"]
            if self._fits(head, rem):
                break
        if not self._fits(head, rem):
            return  # 抢占也装不下，不误伤运行任务
        now = self._clock()
        for v in chosen:
            del self._running[v["id"]]
            v["preempted"] += 1
            v["enqueued_at"] = now
            self._waiting.append(v)
            self._preemptions += 1

    def _dispatch(self, state):
        self._waiting.remove(state)
        wait = self._clock() - state["enqueued_at"]
        self._waits.setdefault(state["id"], []).append(wait)
        self._running[state["id"]] = state
        self._dispatched += 1
        result = dict(state["task"])
        if state["preempted"]:
            result["progress"] = state["progress"]
        return result

    # ---- 运行中操作 ----

    def report(self, task_id, progress):
        """上报运行中任务的进度（0~1），供抢占时保存。"""
        if task_id not in self._running:
            raise ValueError(f"task not running: {task_id!r}")
        if isinstance(progress, bool) or not isinstance(progress, (int, float)):
            raise ValueError(f"progress must be a number, got {progress!r}")
        if not 0.0 <= progress <= 1.0:
            raise ValueError(f"progress must be in [0, 1], got {progress!r}")
        self._running[task_id]["progress"] = float(progress)

    def done(self, task_id):
        """标记运行任务完成并释放资源。"""
        if task_id not in self._running:
            raise ValueError(f"task not running: {task_id!r}")
        del self._running[task_id]

    # ---- 统计 ----

    def size(self):
        return len(self._waiting)

    def utilization(self):
        rem = self._remaining()
        used_cpu = self.capacity["cpu"] - rem["cpu"]
        used_mem = self.capacity["mem"] - rem["mem"]
        return {
            "cpu": used_cpu / self.capacity["cpu"],
            "mem": used_mem / self.capacity["mem"] if self.capacity["mem"] else 0.0,
        }

    def stats(self):
        waits = [w for ws in self._waits.values() for w in ws]
        u = self.utilization()
        return {
            "capacity": dict(self.capacity),
            "cpu_utilization": u["cpu"],
            "mem_utilization": u["mem"],
            "running": len(self._running),
            "waiting": len(self._waiting),
            "dispatched": self._dispatched,
            "preemptions": self._preemptions,
            "waits": {tid: list(ws) for tid, ws in self._waits.items()},
            "avg_wait": sum(waits) / len(waits) if waits else 0.0,
            "max_wait": max(waits) if waits else 0.0,
        }

    # ---- 序列化 ----

    def snapshot(self):
        """导出 JSON 可序列化的完整状态，用于重启后复现。"""
        return {
            "capacity": dict(self.capacity),
            "waiting": [self._export(s) for s in self._waiting],
            "running": [self._export(s) for s in self._running.values()],
            "seq": self._seq,
            "waits": [[tid, list(ws)] for tid, ws in self._waits.items()],
            "preemptions": self._preemptions,
            "dispatched": self._dispatched,
            "now": self._clock(),
        }

    @staticmethod
    def _export(state):
        return {k: (dict(v) if k == "task" else v) for k, v in state.items()}

    @classmethod
    def restore(cls, data, clock=None):
        """从 snapshot() 恢复。不传 clock 时用单调时钟平移接续原时间轴。"""
        if not isinstance(data, dict) or "capacity" not in data:
            raise ValueError("invalid snapshot: missing 'capacity'")
        q = cls(cpu=data["capacity"]["cpu"], mem=data["capacity"]["mem"], clock=clock)
        if clock is None:
            offset = data.get("now", 0.0) - time.monotonic()
            q._clock = lambda: time.monotonic() + offset
        required = {"task", "id", "cpu", "mem", "priority", "progress",
                    "seq", "enqueued_at", "preempted"}
        for section in ("waiting", "running"):
            for state in data.get(section, []):
                missing = required - state.keys()
                if missing:
                    raise ValueError(f"invalid snapshot: state missing {sorted(missing)}")
                if section == "waiting":
                    q._waiting.append(state)
                else:
                    q._running[state["id"]] = state
        q._seq = data.get("seq", 0)
        q._waits = {tid: list(ws) for tid, ws in data.get("waits", [])}
        q._preemptions = data.get("preemptions", 0)
        q._dispatched = data.get("dispatched", 0)
        return q
