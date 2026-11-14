"""分片执行器：动态并发、断点重试、幂等写入、可暂停可查询。"""

import time


class _RangeUpstream:
    """默认上游，按主键区间顺序吐键。"""

    def fetch(self, shard, checkpoint, limit):
        start = shard["start"] if checkpoint is None else checkpoint + 1
        return list(range(start, min(shard["end"], start + limit)))


class Job:
    """一次回填任务。

    upstream 需提供 fetch(shard, checkpoint, limit) -> list，返回空表表示
    分片读完；可选 pressure() -> 0..1 报告上游压力。sink 为按键去重的
    写入端，state 可跨次运行复用以跳过已完成分片。
    """

    def __init__(self, plan_payload, workers=1, *, upstream=None, sink=None,
                 state=None, max_retries=3, batch_size=100,
                 latency_threshold=0.05, after_shard=None):
        self._plan = plan_payload
        self._upstream = upstream or _RangeUpstream()
        self._sink = sink if sink is not None else {}
        self._max_workers = max(1, min(workers, len(plan_payload["shards"])))
        self._cur = max(1, self._max_workers // 2)
        self._max_retries = max_retries
        self._batch_size = max(1, batch_size)
        self._latency_threshold = latency_threshold
        self._after_shard = after_shard
        self._paused = False
        self._concurrency_log = []
        self._latencies = []
        self._written = 0
        self._duplicates = 0
        self._wave = 0
        self._state = state if state is not None else {}
        for shard in plan_payload["shards"]:
            st = self._state.setdefault(shard["id"], {
                "status": "pending", "checkpoint": None,
                "retries": 0, "error": None,
            })
            if st["status"] == "done":
                st["status"] = "skipped"

    def pause(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def status(self):
        return self._summary()

    def run(self):
        """按依赖和优先级跑分片，返回汇总；暂停时保留断点退出。"""
        while not self._paused:
            runnable = self._runnable()
            if not runnable:
                break
            self._wave += 1
            for shard in runnable[:self._cur]:
                self._run_shard(shard)
                if self._after_shard:
                    self._after_shard(self, shard)
                if self._paused:
                    break
            self._adjust()
        return self._summary()

    def _runnable(self):
        out = []
        for shard in self._plan["shards"]:
            if self._state[shard["id"]]["status"] != "pending":
                continue
            if all(self._state[d]["status"] in ("done", "skipped")
                   for d in shard["deps"]):
                out.append(shard)
        out.sort(key=lambda s: (-s["priority"], s["id"]))
        return out

    def _run_shard(self, shard):
        st = self._state[shard["id"]]
        st["status"] = "running"
        while not self._paused:
            started = time.monotonic()
            try:
                keys = self._upstream.fetch(shard, st["checkpoint"],
                                            self._batch_size)
            except Exception as exc:
                st["retries"] += 1
                st["error"] = str(exc)
                st["status"] = ("failed" if st["retries"] > self._max_retries
                                else "pending")
                return
            self._latencies.append(time.monotonic() - started)
            for key in keys:
                if key in self._sink:
                    self._duplicates += 1
                else:
                    self._sink[key] = True
                    self._written += 1
            if not keys:
                st["status"] = "done"
                st["error"] = None
                return
            st["checkpoint"] = keys[-1]
        st["status"] = "pending"

    def _adjust(self):
        old = self._cur
        pressure = getattr(self._upstream, "pressure", None)
        if callable(pressure):
            level = pressure()
            if level > 0.7:
                self._cur = max(1, self._cur // 2)
            elif level < 0.3:
                self._cur = min(self._max_workers, self._cur + 1)
        elif self._latencies:
            avg = sum(self._latencies) / len(self._latencies)
            self._latencies = []
            if avg > self._latency_threshold:
                self._cur = max(1, self._cur // 2)
            elif avg < self._latency_threshold / 4:
                self._cur = min(self._max_workers, self._cur + 1)
        if self._cur != old:
            self._concurrency_log.append(
                {"wave": self._wave, "from": old, "to": self._cur})

    def _summary(self):
        shards = []
        done = failed = 0
        for shard in self._plan["shards"]:
            st = self._state[shard["id"]]
            shards.append({
                "id": shard["id"],
                "status": st["status"],
                "checkpoint": st["checkpoint"],
                "retries": st["retries"],
                "priority": shard["priority"],
                "deps": list(shard["deps"]),
            })
            if st["status"] in ("done", "skipped"):
                done += 1
            elif st["status"] == "failed":
                failed += 1
        return {
            "workers": self._max_workers,
            "done": done,
            "failed": failed,
            "remaining": len(self._plan["shards"]) - done,
            "written": self._written,
            "duplicates": self._duplicates,
            "paused": self._paused,
            "concurrency": list(self._concurrency_log),
            "shards": shards,
        }


def run(plan_payload, workers=1, **kwargs):
    """跑完（或跑到暂停）并返回汇总，参数见 Job。"""
    return Job(plan_payload, workers, **kwargs).run()
